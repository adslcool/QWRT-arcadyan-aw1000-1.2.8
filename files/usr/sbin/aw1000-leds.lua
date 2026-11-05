#!/usr/bin/lua
-- The QWRT driver ignores DT labels and exposes only ipq::led0..12.
local uci = require('uci').cursor()
local ubus = require('ubus')
local json = require('luci.jsonc')
local nixio = require('nixio')
local function read(path)
    local f=io.open(path); if not f then return '' end
    local v=f:read('*a'); f:close(); return (v:gsub('%s+$',''))
end
local function write(path,value)
    local f=io.open(path,'w'); if f then f:write(tostring(value)); f:close(); return true end
end
local function monotime()
    return tonumber(read('/proc/uptime'):match('^(%d+)')) or os.time()
end
if read('/tmp/sysinfo/board_name')~='arcadyan,aw1000' then os.exit(1) end
local conn
local last={}
local missing={}
local levels={}
local last_signal,last_radio
local last_radio_sample,unknown_since,had_modem_node,last_modem_port
local RADIO_SAMPLE_EXPIRY=120
local no_service_since,signal_fault_since,signal_fault_kind
local NO_SERVICE_GRACE=15
local SIGNAL_FAULT_TIMEOUT=60
-- This pinned QWRT LEDC driver exposes raw inverted output bits. The four
-- single-colour AW1000 lamps are active-high, unlike its RGB channels.
-- Phone (numeric led12) is deliberately absent: fxsd owns it exclusively.
local inverted={['ipq::led0']=true,['ipq::led3']=true,['ipq::led4']=true}
local aliases={
    ['ipq::led_power']='ipq::led0',
    ['ipq::led_5G_NR_R']='ipq::led10', ['ipq::led_5G_NR_G']='ipq::led9', ['ipq::led_5G_NR_B']='ipq::led7',
    ['ipq::led_4G_LTE_R']='ipq::led6', ['ipq::led_4G_LTE_G']='ipq::led2', ['ipq::led_4G_LTE_B']='ipq::led1',
    ['ipq::led_wifi']='ipq::led3', ['ipq::led_internet']='ipq::led4'
}
local function call(object,method,args)
    if not conn then conn=ubus.connect() end
    if not conn then return {} end
    local ok,r=pcall(conn.call,conn,object,method,args or {})
    if not ok then conn:close(); conn=nil; return {} end
    return type(r)=='table' and r or {}
end
local function led(name,mode)
    if not name or name=='' then return end
    if read('/sys/class/leds/'..name..'/max_brightness')=='' then name=aliases[name] or name end
    local p='/sys/class/leds/'..name..'/'
    if read(p..'max_brightness')=='' then
        if not missing[name] then io.stderr:write('aw1000-leds: missing LED '..name..'\n');missing[name]=true end
        return
    end
    local trigger=read(p..'trigger')
    local maximum=tonumber(read(p..'max_brightness')) or 1
    local want=mode=='on' and maximum or 0
    if inverted[name] then want=maximum-want end
    want=tostring(want)
    levels[name]={logical=mode,raw=want,inverted=inverted[name] or false}
    if last[name]==mode then
        if mode=='blink' and trigger:find('[timer]',1,true) and read(p..'delay_on')=='500' and read(p..'delay_off')=='500' then return end
        if mode~='blink' and trigger:find('[none]',1,true) and read(p..'brightness')==want then return end
    end
    if mode=='blink' then
        if write(p..'trigger','timer') then write(p..'delay_on',500); write(p..'delay_off',500) end
    else
        write(p..'trigger','none')
        write(p..'brightness',want)
    end
    last[name]=mode
end
local function rgb(prefix,color)
    for _,c in ipairs({'R','G','B'}) do
        local on=(color=='white' or color==({R='red',G='green',B='blue'})[c])
        led(prefix..c,on and 'on' or color=='green_blink' and c=='G' and 'blink' or color=='red_blink' and c=='R' and 'blink' or 'off')
    end
end
local function at(port,cmd)
    local r=call('at-daemon','sendat',{at_port=port,at_cmd=cmd,timeout=3})
    return r.status=='success' and type(r.response)=='string' and r.response or ''
end
local function radio(s)
    local result={rat='unknown'}
    local lte,nsa,sa,wcdma
    for line in s:gmatch('[^\r\n]+') do
        if line:match('^%+QENG:') then
            local f={};for v in (line:gsub('"','')..','):gmatch('(.-),') do f[#f+1]=v:match('^%s*(.-)%s*$') end
            if f[2]=='SEARCH' or f[2]=='LIMSRV' then result.registered=false;return result end
            if f[1]=='+QENG: NR5G-NSA' then nsa={rat='nsa',dbm=tonumber(f[5])} end
            if f[1]=='+QENG: LTE' then lte={rat='lte',dbm=tonumber(f[12])} end
            if f[1]=='+QENG: servingcell' then
                if f[3]=='NR5G-SA' then sa={rat='sa',dbm=tonumber(f[13])}
                elseif f[3]=='LTE' then lte={rat='lte',dbm=tonumber(f[14])}
                elseif f[3]=='WCDMA' then wcdma={rat='wcdma',dbm=tonumber(f[11])} end
            end
        end
    end
    local serving=sa or nsa or lte or wcdma
    if serving then serving.registered=true end
    return serving or result
end
-- aw1000-wifi-led-hotfix-1
-- true: active AP; false: confirmed inactive; nil: query failed/unknown.
local function vap_reported_down(name)
    if not name:match('^ath%d+$') then return false end
    local event=read('/var/run/aw1000-wifi/events/'..name)
    if not event:find('state=down',1,true) then return false end
    local index=read('/sys/class/net/'..name..'/ifindex')
    return index~='' and event:match('ifindex=(%d+)')==index
end
local function wifi_status()
    local native=call('network.wireless','status')
    local down_reported=vap_reported_down('ath0') or vap_reported_down('ath1')
    local count,complete,native_active=0,true,false
    for _,r in pairs(native) do
        if type(r)=='table' and type(r.up)=='boolean' then
            count=count+1
            if r.up and r.disabled~=true then
                native_active=true
                if not down_reported then return true,'netifd' end
            end
        else complete=false end
    end
    if count>0 and complete and not native_active then return false,'netifd' end
    local devices=call('iwinfo','devices').devices
    if type(devices)~='table' then return nil,'iwinfo-unavailable' end
    local entries=0
    for key in pairs(devices) do
        if type(key)~='number' or key<1 or key%1~=0 then return nil,'iwinfo-malformed' end
        entries=entries+1
    end
    for i=1,entries do
        if devices[i]==nil then return nil,'iwinfo-malformed' end
    end
    local uncertain=false
    for _,name in ipairs(devices) do
        if type(name)~='string' or not name:match('^[%w_.:-]+$') or name=='.' or name=='..' then
            uncertain=true
        elseif not name:match('^wifi%d+$') and not name:match('^radio%d+$') then
            local flags=tonumber(read('/sys/class/net/'..name..'/flags'))
            if not flags then uncertain=true
            elseif flags%2==1 and not vap_reported_down(name) then
                local info=call('iwinfo','info',{device=name})
                local mode=type(info.mode)=='string' and info.mode:lower() or ''
                if mode=='master' or mode=='ap' or mode=='master(vlan)' then
                    local channel=tonumber(info.channel)
                    if channel and channel>0 then return true,'iwinfo' end
                    uncertain=true
                elseif mode=='' or mode=='unknown' then uncertain=true end
            end
        end
    end
    if uncertain then return nil,'iwinfo-incomplete' end
    return false,down_reported and 'qca-vap-down' or 'iwinfo'
end
local function tick()
    led('ipq::led_power','on')
    uci:unload('qmodem'); uci:load('qmodem')
    local selected,count,seen=nil,0,{}
    uci:foreach('qmodem','modem-device',function(s)
        local port=s.override_at_port or ''
        if port=='' then port=s.at_port or '' end
        if (s.name or ''):lower()=='rg500q-ea' and port:match('^/dev/ttyUSB%d+$') then
            s.selected_at_port=port
            if not seen[port] then selected=s;count=count+1;seen[port]=true
            elseif s.data_interface=='usb' then selected=s end
        end
    end)
    uci:unload('mwan3'); uci:load('mwan3')
    local sim,rs,online,ip='unknown',{rat='unknown'},false,''
    local iface,health,reason='','','no-cellular-session'
    local managed=uci.get and uci:get('mwan3','globals','aw1000_enabled')=='1'
    if managed then
        -- One AT sampler serves routing and lamps; mwan3 alone probes IPv4.
        local function sample(flag)
            local h=io.popen('/usr/sbin/aw1000-internet '..flag..' 2>/dev/null')
            if not h then return '' end
            local value=h:read('*a');h:close();return value or ''
        end
        sim=sample('--sim-state'):match('^%s*(%w+)') or 'unknown'
        local raw=sample('--radio-state')
        local ok,value=pcall(json.parse,raw)
        if ok and type(value)=='table' then rs=value end
        -- Enrollment can discover wwan0/livecell despite a stale QModem alias.
        -- The actual enrolled interface is the only authoritative tracker key.
        iface=uci:get('mwan3','globals','aw1000_cellular') or ''
        if iface:match('^[%w_]+$') then
            local info=call('network.interface.'..iface,'status')
            for _,a in ipairs(info['ipv4-address'] or {}) do
                if a.address and a.address~='0.0.0.0' then ip=a.address end
            end
            health=read('/var/run/mwan3track/'..iface..'/STATUS')
            local blocked=read('/var/run/aw1000-internet/blocked')==iface
            local fault=sim=='blocked' or sim=='locked' or sim=='absent' or sim=='failed'
            -- A missing/stale AT sample must not override a proven data path.
            online=info.up==true and ip~='' and not blocked and not fault and
                (health=='online' or health=='disconnecting')
            reason=blocked and 'sim-gated' or fault and 'sim-fault' or online and 'mwan3-online' or
                health=='' and 'tracker-missing' or 'mwan3-'..health
        end
    elseif count==1 then
        local r=at(selected.selected_at_port,'AT+CPIN?')
        sim=r:match('%+CPIN:%s*READY') and 'ready' or r:match('%+CPIN:') and 'blocked' or r:match('ERROR') and 'failed' or 'unknown'
        rs=radio(at(selected.selected_at_port,'AT+QENG="servingcell"'))
        local alias=selected.alias
        if not alias or not alias:match('^[%w_]+$') then alias=selected['.name'] end
        iface=alias or ''
        if not iface:match('^[%w_]+$') then iface='' end
        local info=iface~='' and call('network.interface.'..iface,'status') or {}
        local dev=info.l3_device or ''
        if info.up==true and dev:match('^[%w_.:-]+$') and sim=='ready' then
            for _,a in ipairs(info['ipv4-address'] or {}) do if a.address and a.address~='0.0.0.0' then ip=a.address end end
            -- Check cellular reachability independently of the wired default route.
            if ip~='' then
                local h=io.popen("ping -I '"..dev.."' -c 1 -W 1 223.5.5.5 >/dev/null 2>&1 && echo online")
                if h then online=h:read('*a'):find('online',1,true)~=nil;h:close() end
                reason=online and 'cellular-probe-online' or 'cellular-probe-failed'
            end
        end
    end
    local clock=monotime()
    local color=({sa='white',nsa='blue',lte='green',wcdma='green_blink'})[rs.rat] or 'off'
    local signal='off'
    local hard_fault=sim=='blocked' or sim=='locked' or sim=='absent' or sim=='failed'
    -- Mobile Signal describes radio reception, not the data tracker. A
    -- disabled/offline tracker or an unused PDP session cannot prove radio loss.
    -- Explicit SEARCH/LIMSRV is loss; missing AT samples are unknown.
    local radio_registered
    if rs.rat=='sa' or rs.rat=='nsa' or rs.rat=='lte' or rs.rat=='wcdma' then radio_registered=true
    elseif rs.registered==false then radio_registered=false end
    if radio_registered~=nil then last_radio_sample=clock;unknown_since=nil
    elseif not unknown_since then unknown_since=clock end
    local sample_age=math.max(0,clock-(last_radio_sample or unknown_since or clock))
    local sample_status=radio_registered~=nil and 'fresh' or sample_age>=RADIO_SAMPLE_EXPIRY and 'expired' or 'unknown'
    if selected and selected.selected_at_port then last_modem_port=selected.selected_at_port end
    local modem_disappeared=false
    if last_modem_port then
        local node=read('/sys/class/tty/'..last_modem_port:match('([^/]+)$')..'/dev')
        if node~='' then had_modem_node=true
        elseif had_modem_node then modem_disappeared=true end
    end
    if modem_disappeared then hard_fault=true;sim='failed' end
    local network_loss=sim=='ready' and radio_registered==false
    local signal_fault=''
    if hard_fault then
        no_service_since=nil
        if not signal_fault_since then signal_fault_since=clock end
        signal_fault_kind='sim-'..sim
        signal_fault=signal_fault_kind
    elseif network_loss then
        if not no_service_since then no_service_since=clock end
        if signal_fault_since then
            signal_fault_kind='network-unavailable'
            signal_fault=signal_fault_kind
        elseif clock-no_service_since>=NO_SERVICE_GRACE then
            if not signal_fault_since then signal_fault_since=clock end
            signal_fault_kind='network-unavailable'
            signal_fault=signal_fault_kind
        else signal_fault_kind=nil end
    elseif (sim=='unknown' or (sim=='ready' and radio_registered==nil)) and not online then
        signal_fault=signal_fault_since and (signal_fault_kind or 'state-unknown') or ''
    else
        no_service_since=nil;signal_fault_since=nil;signal_fault_kind=nil
    end
    local signal_fault_elapsed=signal_fault~='' and math.max(0,clock-(signal_fault_since or clock)) or 0
    local signal_fault_expired=signal_fault~='' and signal_fault_elapsed>=SIGNAL_FAULT_TIMEOUT
    if signal_fault~='' then
        signal=signal_fault_expired and 'off' or 'red_blink'
        last_signal=nil
        if hard_fault then color='off';last_radio=nil
        elseif color~='off' then last_radio=color
        elseif last_radio then color=last_radio end
    elseif sim=='ready' then
        if color~='off' then last_radio=color elseif last_radio then color=last_radio end
        local db=rs.dbm
        local good,average=-95,-110
        if rs.rat=='wcdma' then good,average=-85,-100 end
        if db and db>=-140 and db<=-25 then
            signal=db>=good and 'green' or db>=average and 'blue' or 'red'
            last_signal=signal
        else signal=last_signal or 'off' end
    else
        color=last_radio or color
        signal=last_signal or 'off'
    end
    if sample_status=='expired' and not hard_fault and signal_fault=='' then
        signal='off';color='off';last_signal=nil;last_radio=nil
    end
    local wifi,wifi_source=wifi_status()
    rgb('ipq::led_5G_NR_',color);rgb('ipq::led_4G_LTE_',signal)
    if wifi~=nil then led('ipq::led_wifi',wifi and 'on' or 'off') end
    -- Signal LEDs describe the modem/radio, never mwan3 recovery probes. The
    -- Internet LED independently follows the egress currently published by
    -- the policy so a healthy wired exit remains visible with cellular idle.
    local egress=read('/var/run/aw1000-mwan-follow/selected')
    if not egress:match('^[%w_]+$') then egress='' end
    if not managed then egress=iface end
    local selected_health=egress~='' and read('/var/run/mwan3track/'..egress..'/STATUS') or ''
    if not managed then selected_health=online and 'online' or 'offline' end
    local internet=false
    if egress==iface and iface~='' then
        internet=online
    elseif egress~='' then
        local info=call('network.interface.'..egress,'status')
        local has_ip=false
        for _,a in ipairs(info['ipv4-address'] or {}) do
            if a.address and a.address~='0.0.0.0' then has_ip=true end
        end
        internet=info.up==true and has_ip and (selected_health=='online' or selected_health=='disconnecting')
    end
    local internet_reason=egress=='' and 'no-selected-egress' or internet and 'selected-online' or 'selected-'..(selected_health~='' and selected_health or 'unknown')
    led('ipq::led_internet',internet and 'on' or 'off')
    local state={rat=rs.rat,dbm=rs.dbm,sim=sim,internet=internet,cellular_online=online,
        radio_registered=radio_registered,signal_source='radio',radio_sample_status=sample_status,
        radio_sample_age=sample_age,modem_disappeared=modem_disappeared,
        cellular_interface=iface,cellular_health=health,cellular_reason=reason,
        selected=egress,selected_health=selected_health,internet_reason=internet_reason,
        signal_fault=signal_fault,signal_fault_elapsed=signal_fault_elapsed,
        signal_fault_expired=signal_fault_expired,signal_fault_timeout=SIGNAL_FAULT_TIMEOUT,
        no_service_grace=NO_SERVICE_GRACE,
        wifi=wifi,wifi_source=wifi_source,wifi_state=wifi==nil and 'unknown' or wifi and 'up' or 'down',time=os.time(),modem_count=count,missing_leds=missing,led_levels=levels}
    write('/var/run/aw1000-leds.json.tmp',json.stringify(state))
    os.rename('/var/run/aw1000-leds.json.tmp','/var/run/aw1000-leds.json')
end
while true do
    local ok,err=pcall(tick)
    if not ok then io.stderr:write('aw1000-leds: '..tostring(err)..'\n');last={} end
    nixio.nanosleep(5)
end
