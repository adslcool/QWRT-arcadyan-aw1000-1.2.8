"""Remove requested applications by package ownership and adapt QModem transport."""
from pathlib import PurePosixPath
import re


def package_records(text):
    records={}
    for block in text.split('\n\n'):
        fields=dict(line.split(': ',1) for line in block.splitlines() if ': ' in line and not line.startswith(' '))
        if 'Package' in fields:
            fields['_block']=block.strip()
            fields['_deps']=set(re.findall(r'(?:^|[,|])\s*([A-Za-z0-9+_.-]+)',fields.get('Depends','')))
            records[fields['Package']]=fields
    return records


def removed_packages(records):
    removed={name for name in records if name in ('luci-app-modem-data','luci-app-ssr-plus','luci-app-openclash','mihomo','clash','clash-meta','3proxy')
             or name.startswith(('luci-i18n-modem-data-','luci-i18n-ssr-plus-','shadowsocksr-','luci-i18n-openclash-'))}
    applications=('design','coremark','iptv','iptvhelper','iptv-helper','xupnpd',
                  'ddns','vsftpd','pure-ftpd','proftpd','zerotier','udpxy',
                  'igmpproxy','mcproxy','omcproxy','msd_lite','rtp2httpd')
    removed.update(name for name in records if
        name in ('luci-theme-design','coremark','xupnpd','ddns-scripts','ddns-scripts-services',
                 'vsftpd','vsftpd-alt','pure-ftpd','proftpd','zerotier','udpxy','igmpproxy','mcproxy','omcproxy','msd_lite','rtp2httpd')
        or name.startswith('ddns-scripts-')
        or any(name=='luci-app-'+app or name.startswith('luci-i18n-'+app+'-') for app in applications))
    candidates={'dns2tcp','dns2socks','microsocks','ipt2socks','nping','tcping','lua-neturl','lyaml',
                'libsodium','libev','libudns','libpcre2','libyaml','xray-core','hysteria'}
    if not any(name.startswith('shadowsocksr-') or name=='luci-app-ssr-plus' for name in removed):candidates=set()
    if 'luci-app-openclash' in removed:
        candidates.update(name for name in records if name=='ruby' or name.startswith(('ruby-','libruby')))
        candidates.add('libyaml')
    if 'zerotier' in removed:candidates.update(('libminiupnpc','libnatpmp','libatomic1'))
    while True:
        orphaned={name for name in candidates & records.keys() - removed
                  if not any(name in data['_deps'] for other,data in records.items() if other not in removed)}
        if not orphaned:break
        removed.update(orphaned)
    for name,data in records.items():
        if name not in removed and data['_deps'] & removed:raise ValueError('删除软件将破坏保留包依赖：'+name)
    return removed


def purge_paths(root,paths):
    rootdir=root.resolve()
    for rel in sorted(paths,key=lambda p:len(PurePosixPath(p).parts),reverse=True):
        p=root/rel.lstrip('/')
        if not p.parent.resolve().is_relative_to(rootdir):raise ValueError('删除路径越界：'+rel)
        try:p.unlink()
        except FileNotFoundError:pass
        except IsADirectoryError:
            try:p.rmdir()
            except OSError:pass
    for rel in ('etc/ssrplus','usr/share/shadowsocksr','usr/lib/lua/luci/model/cbi/shadowsocksr','usr/lib/lua/luci/view/shadowsocksr',
                'etc/openclash','usr/share/openclash','usr/lib/lua/luci/model/cbi/openclash','usr/lib/lua/luci/view/openclash',
                'www/luci-static/resources/openclash'):
        directory=root/rel
        if not directory.exists():continue
        if directory.is_symlink() or not directory.resolve().is_relative_to(rootdir):raise ValueError('SSR 专用目录不安全：'+rel)
        for p in sorted(directory.rglob('*'),key=lambda p:len(p.parts),reverse=True):
            if p.is_dir() and not p.is_symlink():p.rmdir()
            else:raise ValueError('已删除插件目录存在未归属残留文件：'+str(p.relative_to(root)))
        directory.rmdir()


def remove_apps(root,manifest=None):
    status=root/'usr/lib/opkg/status';records=package_records(status.read_text())
    removed=removed_packages(records);info=root/'usr/lib/opkg/info';owned={}
    for name in records:
        listing=info/(name+'.list')
        if not listing.exists():continue
        for line in listing.read_text().splitlines():
            rel=PurePosixPath(line.lstrip('/'))
            if not line or rel.is_absolute() or '..' in rel.parts:raise ValueError('不安全的 opkg 文件清单：'+name)
            owned.setdefault(str(rel),set()).add(name)
    rootdir=root.resolve();removed_paths=set()
    for rel,owners in sorted(owned.items(),key=lambda item:len(PurePosixPath(item[0]).parts),reverse=True):
        if not owners <= removed:continue
        p=root/rel
        if not p.parent.resolve().is_relative_to(rootdir):raise ValueError('软件文件父路径越界：'+rel)
        if p.is_dir() and not p.is_symlink():
            try:p.rmdir()
            except OSError:pass
        else:
            removed_paths.add('/'+rel)
            try:p.unlink()
            except FileNotFoundError:pass
    for name in removed:
        for p in info.glob(name+'.*'):
            if p.is_symlink() or p.is_file():
                removed_paths.add('/'+p.relative_to(root).as_posix())
                p.unlink()
    # The firmware also exposes BusyBox's dormant FTP applet as a command.
    # Remove its entry point without replacing the shared BusyBox binary.
    ftpd=root/'usr/sbin/ftpd'
    if ftpd.is_symlink() or ftpd.is_file():
        ftpd.unlink();removed_paths.add('/usr/sbin/ftpd')
        listing=info/'busybox.list'
        if listing.exists():
            listing.write_text(''.join(line+'\n' for line in listing.read_text().splitlines()
                                       if line.lstrip('/')!='usr/sbin/ftpd'))
    inetd=root/'etc/inetd.conf'
    if inetd.exists():inetd.write_text(re.sub(r'^\s*ftp\s+.*\n?', '', inetd.read_text(), flags=re.M))
    alternatives={}
    for name,data in records.items():
        for item in re.split(r'[,\s]+',data.get('Alternatives','')):
            if not item:continue
            priority,link,target=item.split(':',2)
            if not priority.isdigit():raise ValueError('无效 alternatives 优先级：'+name)
            for path in (link,target):
                if not path.startswith('/') or '..' in PurePosixPath(path).parts:raise ValueError('不安全 alternatives 路径')
            alternatives.setdefault(link,[]).append((int(priority),name,target))
    for link,choices in alternatives.items():
        p=root/link.lstrip('/')
        if not p.parent.resolve().is_relative_to(rootdir):raise ValueError('alternatives 父路径越界')
        if not p.is_symlink():continue
        if not any(name in removed and str(p.readlink())==target for _,name,target in choices):continue
        retained=[item for item in choices if item[1] not in removed]
        p.unlink()
        if retained:p.symlink_to(max(retained)[2])
        else:removed_paths.add(link)
    if 'luci-app-openclash' in removed:
        # This retained theme ships a menu icon for the now-removed application.
        icon='/www/luci-static/design/images/openclash.png'
        p=root/icon.lstrip('/')
        if not p.parent.resolve().is_relative_to(rootdir):raise ValueError('图标父路径越界')
        p.unlink(missing_ok=True);removed_paths.add(icon)
    if removed & {'luci-app-ssr-plus','luci-app-openclash'}:purge_paths(root,removed_paths)
    rc=root/'etc/rc.d'
    if rc.is_dir():
        for p in rc.iterdir():
            if p.is_symlink() and not p.exists() and str(p.readlink()).startswith('../init.d/'):
                if '/etc/init.d/'+p.readlink().name in removed_paths:p.unlink()
    status.write_text('\n\n'.join(data['_block'] for name,data in records.items() if name not in removed)+'\n\n')
    if manifest is not None:manifest.extend(sorted(removed_paths))
    return sorted(removed)


def patch_qmodem(root):
    p=root/'usr/share/qmodem/modem_scan.sh';text=p.read_text()
    old='''set qmodem.$section_name.extend_prefix="1"
set qmodem.$section_name.pdp_type="ipv4v6"'''
    new='''# AW1000 owns LAN IPv6 and dnsmasq upstream selection. A cellular /64
# must not be extended onto br-lan. The legacy QMI command is adapted below;
# dnsmasq's no-resolv protects its own upstream list from shared resolver writes.
set qmodem.$section_name.extend_prefix="0"
set qmodem.$section_name.ra_master="0"
set qmodem.$section_name.do_not_add_dns="1"
set qmodem.$section_name.pdp_type="ipv4v6"'''
    if text.count(old)!=1:raise ValueError('QModem 扫描默认值版本不符')
    p.write_text(text.replace(old,new,1))
    p=root/'usr/share/qmodem/modem_ctrl.sh';text=p.read_text()
    old='use_ubus=$(uci get qmodem.$config_section.use_ubus)'
    if text.count(old)!=1:raise ValueError('QModem 控制脚本版本不符')
    text=text.replace(old,'use_ubus=$(uci -q get qmodem.$config_section.use_ubus)\nuse_ubus=${use_ubus:-1}')
    p.write_text(text.replace('[ $use_ubus -eq 1 ]','[ "$use_ubus" = 1 ]'))
    p=root/'usr/share/qmodem/modem_util.sh';text=p.read_text()
    old='tom_modem -d $at_port -o a -c "$atcmd" -t 1'
    if text.count(old)!=1:raise ValueError('QModem AT 工具脚本版本不符')
    text=text.replace(old,'tom_modem $use_ubus_flag -d $at_port -o a -c "$atcmd" -t 1')
    old='# Copyright (C) 2024 Tom <fjrcn@outlook.com>'
    if text.count(old)!=1:raise ValueError('QModem AT 工具来源不符')
    p.write_text(text.replace(old,old+'\n# Default to the shared AT daemon.\nuse_ubus=${use_ubus:-1}\nuse_ubus_flag=""\n[ "$use_ubus" = 1 ] && use_ubus_flag="-u"',1))
    p=root/'usr/share/qmodem/modem_dial.sh';text=p.read_text()
    old='''set_led()
{
    local type=$1'''
    new='''set_led()
{
    # aw1000-leds is the sole owner of this board's front-panel controller.
    return 0
    local type=$1'''
    if text.count(old)!=1:raise ValueError('QModem LED 控制脚本版本不符')
    text=text.replace(old,new,1)
    # The pinned QWRT contains QConnectManager 1.6.5, not quectel-CM-M.
    # Uppercase -D is unsupported and makes that binary print help and exit.
    # Retain a nonempty then-body; deleting only the assignment is invalid sh.
    old='        cmd_line="${cmd_line} -D"'
    if text.count(old)!=1:raise ValueError('QModem QMI DNS 参数版本不符')
    p.write_text(text.replace(old,'        : # AW1000 QMI compatibility',1))
    # QConnectManager 1.6.5 launches a second, unmanaged udhcpc process.  The
    # QWRT dispatcher adds a metric-0 mobile default and then deletes every
    # default route whose gateway is not the mobile gateway.  netifd already
    # runs the managed DHCP client for wwan, so leave all default-route work to
    # netifd/mwan3 while retaining the address setup QConnectManager requires.
    p=root/'usr/share/udhcpc/default.script';text=p.read_text()
    whitespace='\t\tdone\n\t\t\n\t\teval'
    clean_whitespace='\t\tdone\n\n\t\teval'
    if text.count(whitespace)==1:text=text.replace(whitespace,clean_whitespace,1)
    elif text.count(clean_whitespace)!=1:raise ValueError('QConnectManager DHCP 空白锚点不符')
    old='''\t\techo "udhcpc: setting default routers: $router"

\t\tlocal valid_gw=""
\t\tfor i in $router ; do
\t\t\troute add default gw $i dev $interface
\t\t\tvalid_gw="${valid_gw:+$valid_gw|}$i"
\t\tdone

\t\teval $(route -n | awk '
\t\t\t/^0.0.0.0\\W{9}('$valid_gw')\\W/ {next}
\t\t\t/^0.0.0.0/ {print "route del -net "$1" gw "$2";"}
\t\t')'''
    new='''\t\tcase "$interface" in
\t\t\twwan*)
\t\t\t\t# The logical wwan netifd client owns defaults and metrics.
\t\t\t\techo "udhcpc: leaving default routing for $interface to netifd"
\t\t\t\t;;
\t\t\t*)
\t\t\t\techo "udhcpc: setting default routers: $router"

\t\t\t\tlocal valid_gw=""
\t\t\t\tfor i in $router ; do
\t\t\t\t\troute add default gw $i dev $interface
\t\t\t\t\tvalid_gw="${valid_gw:+$valid_gw|}$i"
\t\t\t\tdone

\t\t\t\teval $(route -n | awk '
\t\t\t\t\t/^0.0.0.0\\W{9}('$valid_gw')\\W/ {next}
\t\t\t\t\t/^0.0.0.0/ {print "route del -net "$1" gw "$2";"}
\t\t\t\t')
\t\t\t\t;;
\t\tesac'''
    if text.count(old)!=1:raise ValueError('QConnectManager DHCP 默认路由锚点不符')
    text=text.replace(old,new,1)
    old='''\t\techo "udhcpc: adding route for $type $1 via $2"
\t\troute add -$type "$1" gw "$2" dev "$interface"'''
    new='''\t\tcase "$interface:$1" in
\t\t\twwan*:0.0.0.0/0)
\t\t\t\techo "udhcpc: leaving classless default for $interface to netifd";;
\t\t\t*) echo "udhcpc: adding route for $type $1 via $2"
\t\t\t   route add -$type "$1" gw "$2" dev "$interface";;
\t\tesac'''
    if text.count(old)!=1:raise ValueError('QConnectManager DHCP 类路由锚点不符')
    p.write_text(text.replace(old,new,1))
    p=root/'etc/init.d/qmodem_network';text=p.read_text()
    old='''start_service(){
    force_stop=0
    mkdir -p $MODEM_RUNDIR
    update_modem
}'''
    new='''start_service(){
    force_stop=0
    # rc.common already opened the service transaction. Always collect the
    # complete enabled instance set, without nesting rc_procd/dial inside it.
    aw1000_procd_batch=1
    mkdir -p $MODEM_RUNDIR
    update_modem
    aw1000_procd_batch=0
}'''
    if text.count(old)!=1:raise ValueError('QModem procd 启动入口版本不符')
    text=text.replace(old,new,1)
    old='''reload_service()
{
    force_stop=0
    mkdir -p $MODEM_RUNDIR
    sleep 1
    update_modem
}'''
    new='''reload_service()
{
    sleep 1
    # Reload, unlike start_service, has no outer service transaction.
    start
}'''
    if text.count(old)!=1:raise ValueError('QModem procd 重载入口版本不符')
    text=text.replace(old,new,1)
    old='''    data=$(_procd_ubus_call list | jq -r  ".$service.instances.modem_$1")'''
    if text.count(old)!=1:raise ValueError('QModem procd 实例查询版本不符')
    text=text.replace(old,'    data=$(_modem_instance "$1")',1)
    old='''    local target
    target=1'''
    if text.count(old)!=1:raise ValueError('QModem procd 更新入口版本不符')
    text=text.replace(old,'    local target data current_status\n    target=1',1)
    old='''    [ "$data" == "null" ] && current_status=0 || current_status=1'''
    if text.count(old)!=1:raise ValueError('QModem procd 实例状态版本不符')
    text=text.replace(old,'    { [ -z "$data" ] || [ "$data" = "null" ]; } && current_status=0 || current_status=1',1)
    old='''    if [ "$current_status" != "$target" ];then
        [ "$target" -eq 1 ] && dial $1 || hang $1
        logger -t qmodem_network "update modem cfg: $1 dial:$target"
    fi'''
    new='''    if [ "$aw1000_procd_batch" = "1" ]; then
        if [ "$target" -eq 1 ]; then
            _dial "$1"
        elif [ "$current_status" -eq 1 ]; then
            # procd_kill uses its own JSON message. Keep it in a subshell so
            # it cannot erase the parent's pending instances transaction.
            ( hang "$1" )
        fi
    elif [ "$current_status" != "$target" ]; then
        [ "$target" -eq 1 ] && dial "$1" || hang "$1"
        logger -t qmodem_network "update modem cfg: $1 dial:$target"
    fi'''
    if text.count(old)!=1:raise ValueError('QModem procd 嵌套调用版本不符')
    text=text.replace(old,new,1)
    old='''    data=$(_procd_ubus_call list | jq  -r ".$service.instances.$instances")'''
    if text.count(old)!=1:raise ValueError('QModem procd 状态查询版本不符')
    text=text.replace(old,'    data=$(_modem_instance "$1")',1)
    text+='''

_modem_instance()
{
    # Query service state independently of any open procd JSON transaction.
    ubus call service list "{\\"name\\":\\"$service\\"}" |
        jq -c --arg service "$service" --arg instance "modem_$1" '.[$service].instances[$instance] // null'
}
'''
    p.write_text(text)


def patch_interface_names(root):
    """Keep the single managed mobile interface stable across dial/reconnect."""
    p=root/'usr/share/qmodem/modem_dial.sh';text=p.read_text()
    old='    interface6_name=${interface_name}v6'
    if text.count(old)!=1:raise ValueError('QModem IPv6 命名锚点不符')
    text=text.replace(old,'''    case "$interface_name" in
        wwan) interface6_name=wwan_v6;;
        *) interface6_name=${interface_name}v6;;
    esac''',1)
    old='    [ -n "$alias" ] && interface_name=$alias'
    if text.count(old)!=1:raise ValueError('QModem 接口 alias 锚点不符')
    text=text.replace(old,old+'''
    # The AW1000 RG500Q is always the managed wwan link.
    case "$(uci -q get qmodem.$modem_config.name | tr A-Z a-z)" in
        rg500q-ea) interface_name=wwan;;
    esac''',1)
    text=text.replace('uci set network.${interface6_name}.ifname="@${interface_name}"',
                      'uci -q delete network.${interface6_name}.ifname')
    text=text.replace('uci set network.${interface_name}.ifname="${set_modem_netcard}"',
                      'uci -q delete network.${interface_name}.ifname')
    text=text.replace('[ "$origin_netcard" == "$set_modem_netcard" ] && ', '')
    p.write_text(text)


def patch_wifi_scan(root):
    """QCA scan compatibility: cache reads only; no temporary STA or AP-force."""
    directory=root/'www/luci-static/resources'
    channel=directory/'view/status/channel_analysis.js'
    wireless=directory/'view/network/wireless.js'
    network=directory/'network.js'
    original=channel.read_text()
    if 'AW1000_CACHE_SCAN_V123' in original:
        return {'mode':'cache-only','active_scan_hardware_verified':False}
    parser=original[original.index('function parseIwDevMap'):original.index('return view.extend(')]
    names=re.findall(r'function\s+([A-Za-z0-9_]+)\(',parser)
    mapping={name:'aw1000'+name[0].upper()+name[1:] for name in names}
    parser=re.sub(r'\b('+('|'.join(map(re.escape,names)))+r')\b',lambda m:mapping[m[0]],parser)
    n=network.read_text()
    anchor='getScanList(){return callIwinfoScan(this.sid);}'
    if n.count(anchor)!=1:raise ValueError('QWRT getScanList scan anchor changed')
    rpc="const callAw1000Scan=rpc.declare({object:'aw1000.internet',method:'scan',params:['device'],nobatch:true,expect:{}});"
    n=n.replace(anchor,"getScanList(){return callAw1000Scan(this.sid).then(r=>aw1000ParseIwScan(r.stdout||''));}")
    n=n.replace("'require firewall';","'require firewall';/* AW1000_CACHE_SCAN_V123 */"+rpc+parser,1)
    network.write_text(n)
    c=original
    start=c.index('return view.extend({callFrequencyList:')
    end=c.index('render_signal_badge(',start)
    c=c[:start]+"""/* AW1000_CACHE_SCAN_V123 */
return view.extend({
callFrequencyList:rpc.declare({object:'iwinfo',method:'freqlist',params:['device'],expect:{results:[]}}),
callSafeScan:rpc.declare({object:'aw1000.internet',method:'scan',params:['device'],nobatch:true,expect:{}}),
getScanResultsForRadio(radioDev){
 if(this.scanBusy)return Promise.resolve([]);
 this.scanBusy=true;
 return this.callSafeScan(radioDev.getName()).then(r=>{
  if(r.error)throw new Error(r.error);
  return parseIwScan(r.stdout||'');
 }).finally(()=>{this.scanBusy=false;});
},
"""+c[end:]
    c=c.replace('if(!this.radios[this.active_tab].loadedOnce)\nthis.handleScanRefresh();','')
    c=c.replace('radio.loadedOnce=true;poll.stop();','radio.loadedOnce=true;')
    c=c.replace("L.resolveDefault(fs.exec('/usr/sbin/iw',['dev']),null)","Promise.resolve(null)")
    start=c.index('this.pollFn=L.bind(this.handleScanRefresh,this);')
    end=c.index('return E(',start)
    c=c[:start]+'this.pollFn=null;'+c[end:]
    c=c.replace('return E(\'div\',{},[h2,tabs]);',
      "return E('div',{},[h2,E('p',{class:'alert-message notice'},'当前 QCA 平台只读取已有扫描缓存，点击刷新不会执行主动扫描。缓存可能为空或过期；已停用会中断 AP 的临时 STA 和强制扫描。'),tabs]);")
    if 'ap-force' in c or "'interface','add'" in c:raise ValueError('Unsafe QCA scan fallback remains')
    channel.write_text(c)
    w=wireless.read_text()
    before='this.pollFn=L.bind(this.handleScanRefresh,this,radioDev,{},table,stop);poll.add(this.pollFn);poll.start();'
    if w.count(before)!=1:raise ValueError('QWRT wireless scan polling anchor changed')
    w=w.replace(before,'this.pollFn=L.bind(this.handleScanRefresh,this,radioDev,{},table,stop);this.pollFn();')
    start=w.index('s.handleScanStartStop=function')
    end=w.index('s.handleScanAbort=function',start)
    w=w[:start]+"s.handleScanStartStop=function(){if(this.pollFn)return this.pollFn();};"+w[end:]
    w=w.replace("_('Stop refresh')","_('刷新缓存')").replace("_('Scan')","_('扫描缓存')")
    w=w.replace("const md=ui.showModal(_('Join Network: Wireless Scan'),[table,",
       "const md=ui.showModal(_('Join Network: Wireless Scan'),[E('p',{},'当前 QCA 平台只显示已有扫描缓存，不执行主动扫描；结果可能为空或过期。'),table,")
    wireless.write_text(w)
    # QSDK may add the same UCI section twice to its transient VIF list.
    q=root/'lib/wifi/qcawificfg80211.sh'
    text=q.read_text();begin=text.index('get_vif() {');end=text.index('\n}\n',begin)+2
    body=text[begin:end]
    body=body.replace('local vifs1=','local vifs1=\n\tlocal config_bkp=\n\tvif=',1)
    body=body.replace('\tconfig_load wireless','''\tconfig_load wireless
\tlocal unique="" item
\tfor item in $vif; do
\t\tcase " $unique " in *" $item "*) :;; *) unique="$unique $item";; esac
\tdone
\tvif="${unique# }"''',1)
    q.write_text(text[:begin]+body+text[end:])
    return {'mode':'cache-only','temporary_sta':False,'ap_force':False,'active_scan_hardware_verified':False}


# Pinned full modemdata integration; serial ownership stays with at-daemon.
MODEMDATA_AT = r'''#!/usr/bin/lua
local json=require('luci.jsonc')
local fs=require('nixio.fs')
local function q(s) return "'"..tostring(s):gsub("'","'\\''").."'" end
local function run(s)
 local p=io.popen(s);if not p then return end
 local out=p:read('*a');local ok=p:close();if ok then return out end
end
local function fail(s)
 local marker=os.getenv('AW1000_MD_FAILURE_FILE')
 if marker then local f=io.open(marker,'w');if f then f:write(s);f:close() end end
 io.stderr:write('modemdata: '..s..'\n')
end
local product=arg[1]=='--product' or arg[1]=='--product-lines'
local port,request
if product then port=arg[2] else
 local i=1
 while arg[i] do
  if arg[i]=='-d' then port=arg[i+1];i=i+2
  elseif arg[i]=='-D' then i=i+1
  elseif arg[i]=='at' then request=arg[i+1];break
  else fail('unsupported argument');os.exit(2) end
 end
end
if not port or port=='auto' then
 port=run(q(os.getenv('AW1000_MD_PORT_CMD') or '/usr/sbin/aw1000-phone-port')..' 2>/dev/null')
 port=port and port:match('^%s*(.-)%s*$')
end
local suffix=port and (port:match('^/dev/ttyUSB(%d+)$') or port:match('^/dev/ttyACM(%d+)$') or port:match('^/dev/wwan%d+at(%d+)$') or port:match('^/dev/mhi_DUN(%d*)$'))
if not suffix or not fs.access(port) then fail('Device not found');os.exit(1) end
local function at(s)
 if #s>512 or s:find('[%z\1-\31\127]') or not s:upper():match('^AT') then fail('invalid AT command');return end
 local payload=json.stringify({at_port=port,at_cmd=s,timeout=2})
 local raw=run(q(os.getenv('AW1000_MD_UBUS') or '/bin/ubus')..' -t 4 call at-daemon sendat '..q(payload)..' 2>/dev/null')
 local r=raw and json.parse(raw)
 if type(r)~='table' or r.status~='success' or type(r.response)~='string' then fail('AT queue unavailable');return end
 return r.response:gsub('\r','')
end
if product then
 local result={}
 for _,row in ipairs({{'vendor','AT+CGMI','CGMI'},{'product','AT+CGMM','CGMM'},{'revision','AT+CGMR','CGMR'},{'imei','AT+CGSN','CGSN'},{'imsi','AT+CIMI','CIMI'},{'iccid','AT+QCCID','CCID'}}) do
  local s=at(row[2]);local values={}
  if s then
   for line in s:gmatch('[^\n]+') do
    line=line:match('^%s*(.-)%s*$')
    if line~='' and line~='OK' and not line:upper():match('^AT') and not line:find('ERROR',1,true) then
     values[#values+1]=line:gsub('^%+%w+:%s*',''):gsub('^"',''):gsub('"$','')
    end
   end
  end
  result[row[1]]=table.concat(values,' ')
  if arg[1]=='--product-lines' then print('+'..row[3]..':'..result[row[1]]) end
 end
 if arg[1]=='--product' then print(json.stringify(result)) end
 os.exit(0)
end
if type(request)~='string' then fail('missing command');os.exit(2) end
local parts,current,quoted={},'',false
for i=1,#request do
 local c=request:sub(i,i)
 if c=='"' then quoted=not quoted end
 if c==';' and not quoted then parts[#parts+1]=current;current='' else current=current..c end
end
parts[#parts+1]=current
if quoted or #parts>24 then fail('invalid command group');os.exit(2) end
local good=true
for _,s in ipairs(parts) do
 s=s:match('^%s*(.-)%s*$')
 if s~='' then
  if not s:upper():match('^AT') then s='AT'..s end
  local response=at(s)
  if response then io.write(response);if response:sub(-1)~='\n' then io.write('\n') end else good=false end
 end
end
os.exit(good and 0 or 1)
'''
MODEMDATA_CACHE = r'''#!/bin/sh
set -u
umask 077
kind=$1;shift
case "$kind" in product|params) :;; *) exit 2;; esac
port=${1:-auto};forced=${2:-}
case "$port" in auto|'') port=$("${AW1000_MD_PORT_CMD:-/usr/sbin/aw1000-phone-port}" 2>/dev/null) || port='';; esac
case "$port" in /dev/ttyUSB[0-9]*|/dev/ttyACM[0-9]*|/dev/wwan[0-9]*at[0-9]*|/dev/mhi_DUN*) :;; *) echo '{"error":"Device not found"}';exit 0;; esac
[ -e "$port" ] || { echo '{"error":"Device not found"}';exit 0; }
RUN=${AW1000_MD_RUN:-/var/run/aw1000-modemdata}
mkdir -p "$RUN" || exit 1
chmod 700 "$RUN"
token=$(stat -c '%t:%T:%i:%Y' "$port" 2>/dev/null || printf '%s' "$port")
key=$(printf '%s' "$kind|$port|$forced|$token" | sha256sum);key=${key%% *}
file="$RUN/$key.json"
now=$(cut -d. -f1 "${AW1000_MD_UPTIME:-/proc/uptime}")
case "$now" in ''|*[!0-9]*) exit 1;; esac
age=999999
if [ -s "$file" ];then
 stamp=$(jq -r '.time // 0' "$file" 2>/dev/null) || stamp=0
 case "$stamp" in ''|*[!0-9]*) stamp=0;; esac
 [ "$now" -lt "$stamp" ] || age=$((now-stamp))
fi
emit() { jq --argjson age "$age" --argjson stale "$1" '.data + {_aw1000:{age:$age,stale:$stale}}' "$file"; }
ttl=10;[ "$kind" != product ] || ttl=300
if [ "$age" -lt "$ttl" ];then emit false;exit 0;fi
exec 9>"$RUN/serial.lock"
if ! flock -n 9;then
 if [ "$age" -le 60 ];then emit true;else echo '{"error":"Device is busy"}';fi
 exit 0
fi
retry="$RUN/$key.retry"
if [ -s "$retry" ] && [ "$(cat "$retry")" -gt "$now" ];then
 if [ "$age" -le 60 ];then emit true;else echo '{"error":"AT queue unavailable; retry pending"}';fi
 exit 0
fi
raw="$RUN/$key.raw.$$";failure="$RUN/$key.failure"
rm -f "$failure";export AW1000_MD_FAILURE_FILE="$failure"
ok=0
if [ "$kind" = product ];then
 timeout 24 /usr/libexec/aw1000-modemdata-at --product "$port" >"$raw" 2>"$RUN/last-error" && ok=1
else
 timeout 24 /bin/sh /usr/share/modemdata/params-upstream.sh "$port" "$forced" >"$raw" 2>"$RUN/last-error" && ok=1
fi
# Cache age and retry deadlines start after collection, including slow failures.
now=$(cut -d. -f1 "${AW1000_MD_UPTIME:-/proc/uptime}")
case "$now" in ''|*[!0-9]*) rm -f "$raw";exit 1;; esac
if [ -s "$file" ] && [ "$now" -ge "${stamp:-0}" ];then age=$((now-stamp));else age=999999;fi
if [ "$ok" = 1 ] && [ ! -e "$failure" ] && jq -e 'type=="object" and (has("error")|not)' "$raw" >/dev/null 2>&1;then
 jq --argjson time "$now" '{time:$time,data:.}' "$raw" >"$file.next" && mv "$file.next" "$file"
 rm -f "$raw" "$retry";age=0;emit false;exit 0
fi
rm -f "$raw";echo "$((now+5))" >"$retry"
if [ "$age" -le 60 ];then emit true;else echo '{"error":"AT queue unavailable"}';fi
'''
MODEMDATA_SERIAL = r'''#!/bin/sh
umask 077
device=${1:-auto};network=${2:-wwan};forced=${3:-}
case "$network" in ''|*[!A-Za-z0-9_]*) echo '[{},{},{"error":"Invalid network interface"}]';exit 0;; esac
case "$device" in 192.168.*) exec /usr/bin/md_serial_ecm-upstream "$@";; esac
product=$(/usr/share/modemdata/product.sh "$device") || product='{}'
net=$(/usr/share/modemdata/network.sh "$network") || net='{}'
params=$(/usr/share/modemdata/params.sh "$device" "$forced") || params='{"error":"AT queue unavailable"}'
jq -n --argjson p "$product" --argjson n "$net" --argjson s "$params" '[$p,$n,$s]'
'''
MODEMDATA_DEFAULTS = r'''#!/bin/sh
umask 077
[ -e /etc/aw1000-mod/modemdata-1.2.4.done ] && exit 0
for c in defmodems modemdata;do [ -z "$(uci -q changes "$c")" ] || exit 0;done
if [ -z "$(uci -q get modemdata.@modemdata[0])" ];then
 section=$(uci add modemdata modemdata)
 uci set "modemdata.$section.updtime=10"
fi
if [ -z "$(uci -q show defmodems | sed -n '/=defmodems$/p')" ];then
 [ -n "$(uci -q get defmodems.@general[0])" ] || uci add defmodems general >/dev/null
 uci set defmodems.aw1000=defmodems
 uci set defmodems.aw1000.modem='Quectel RG500Q-EA'
 uci set defmodems.aw1000.modemdata=serial
 uci set defmodems.aw1000.comm_port=auto
 uci set defmodems.aw1000.comm_serial=auto
 uci set defmodems.aw1000.network=wwan
 uci set defmodems.aw1000.user_desc='AW1000 内置模块'
 uci set defmodems.@general[0].main_modem=aw1000
fi
for c in defmodems modemdata;do [ -z "$(uci -q changes "$c")" ] || uci commit "$c";done
mkdir -p /etc/aw1000-mod;touch /etc/aw1000-mod/modemdata-1.2.4.done
exit 0
'''

def modemdata_sources():
    import hashlib,json,tarfile
    from pathlib import Path
    project=Path(__file__).resolve().parents[1]
    lock=json.loads((project/'sources.lock.json').read_text())['modemdata']
    archive=project/lock['archive']
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=lock['sha256']:raise ValueError('modemdata archive mismatch')
    result={}
    with tarfile.open(archive,'r:xz') as t:
        for m in t:
            if not m.name.startswith('modemdata/'):continue
            name=m.name[len('modemdata/'):]
            if name not in lock['files'] or not m.isfile() or m.size>512*1024 or name in result:raise ValueError('invalid modemdata member')
            data=t.extractfile(m).read();e=lock['files'][name]
            if hashlib.sha256(data).hexdigest()!=e['sha256'] or hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()!=e['git_blob']:raise ValueError('modemdata file mismatch: '+name)
            result[name]=data
    if result.keys()!=lock['files'].keys():raise ValueError('incomplete modemdata sources')
    return result,lock

def install_modemdata(root):
    import ast,json,subprocess,posixpath
    source,lock=modemdata_sources();paths={'modemdata':[],'luci-app-modemdata':[]}
    def write(name,data,package='luci-app-modemdata'):
        if isinstance(data,str):data=data.encode()
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True)
        if p.is_symlink():p.unlink()
        p.write_bytes(data);p.chmod(0o755 if data.startswith(b'#!') else 0o644)
        paths[package].append('/'+name)
        if name.endswith('.js'):subprocess.run(['node','--check',str(p)],check=True)
        if data.startswith(b'#!/bin/sh'):subprocess.run(['sh','-n',str(p)],check=True)
    def contents(name,seen=None):
        seen=set() if seen is None else seen
        if name in seen:raise ValueError('source symlink cycle')
        seen.add(name)
        if lock['files'][name]['mode']!='120000':return source[name]
        target=source[name].decode()
        resolved=posixpath.normpath(str(PurePosixPath(name).parent/target))
        if target.startswith('/') or not resolved.startswith('backend/modemdata/files/') or resolved not in source:raise ValueError('unsafe source symlink')
        return contents(resolved,seen)
    translations={};key=value='';field=None
    for line in source['ui/luci-app-modemdata/po/zh_Hans/modemdata.po'].decode().splitlines()+['']:
        if line.startswith('msgid '):key=ast.literal_eval(line[6:]);value='';field='key'
        elif line.startswith('msgstr '):value=ast.literal_eval(line[7:]);field='value'
        elif line.startswith('"'):
            if field=='key':key+=ast.literal_eval(line)
            elif field=='value':value+=ast.literal_eval(line)
        elif not line.strip():
            if key and value:translations[key]=value
            key=value='';field=None
    def chinese(text):
        def sub(m):
            try:message=ast.literal_eval(m[1].replace('\\\n',''))
            except (ValueError,SyntaxError):return m[0]
            return '_('+json.dumps(translations[message],ensure_ascii=False)+')' if message in translations else m[0]
        return re.sub(r"_\(\s*('(?:\\[\s\S]|[^'\\])*'|\"(?:\\[\s\S]|[^\"\\])*\")\s*\)",sub,text)
    for name in sorted(source):
        if name.startswith('backend/modemdata/files/'):
            target=name[len('backend/modemdata/files/'):];data=contents(name).decode()
            data=re.sub(r'\bsms_tool\b','/usr/libexec/aw1000-modemdata-at',data)
            data=data.replace('gcom -d "$DEVICE" -s $RES/vendorproduct/vendorproduct.gcom','/usr/libexec/aw1000-modemdata-at --product-lines "$DEVICE"')
            if 'gcom -d ' in data:raise ValueError('unadapted direct serial fallback')
            if '# Quectel RG500Q-EA' in data:
                # COPS/QCAINFO can lag or omit NR CA; a valid QENG NR leg is authoritative.
                anchor='case "$MODE_NUM" in\n\t7*)'
                detect='''AW_NSA=0
if printf '%s\\n' "$O" | awk -F[,:] '/^\\+QENG: *"NR5G-NSA"/ {gsub(/[[:space:]"]+/,"");if($6<0 && $6>-200)found=1} END{exit !found}';then
 MODE_NUM=13;AW_NSA=1
fi
'''
                if anchor not in data:raise ValueError('Quectel RAT parsing anchor changed')
                data=data.replace(anchor,detect+anchor,1)
                data=data.replace('if echo "$O" | grep -q -i "+QCAINFO.*NR5G"; then',
                                  'if [ "$AW_NSA" = 1 ] || echo "$O" | grep -q -i "+QCAINFO.*NR5G"; then',1)
            write(target,data,'modemdata')
        elif name.startswith('ui/luci-app-modemdata/htdocs/'):
            target='www/'+name[len('ui/luci-app-modemdata/htdocs/'):];data=contents(name).decode()
            if target.endswith('.js'):
                data=data.replace('/etc/modem/modemlist.json','/tmp/aw1000-modemdata-modemlist.json')
                data=data.replace('ui.addTimeLimitedNotification(a, message, timeout, severity)','(ui.addTimeLimitedNotification ? ui.addTimeLimitedNotification(a, message, timeout, severity) : ui.addNotification(a, message, severity))')
                if target.endswith('modempreview.js'):
                    data=data.replace('if (idx == null) return null;','if (idx == null || document.hidden) return null;')
                    data=data.replace('  if (!_sigModalOpen) return;','  if (!_sigModalOpen || document.hidden) return;\n  if(!document.getElementById("bs_bar_csq")){_sigModalOpen=false;if(_sigModalTimer){clearInterval(_sigModalTimer);_sigModalTimer=null;}return;}')
                    data=data.replace('function CreateModemMultiverse(modemTabs, sectionsxt) {','function CreateModemMultiverse(modemTabs, sectionsxt) {\n  if(document.hidden)return Promise.resolve();')
                    hook='''function aw1000ModemdataFreshness(modem, sample) {
 const anchor=document.getElementById(modem.modeId);
 if(!anchor || !anchor.parentNode)return;
 const id='aw1000-md-fresh-'+modem.modeId;
 let badge=document.getElementById(id);
 if(!badge){badge=E('small',{id:id,style:'display:block;margin-top:4px'},'');anchor.parentNode.appendChild(badge);}
 const meta=sample && sample._aw1000;
 badge.textContent=sample && sample.error ? '查询暂不可用；其他字段可能为上次结果' : meta && meta.stale ? '查询未成功，显示 '+meta.age+' 秒前的缓存' : meta ? '数据缓存 '+meta.age+' 秒' : '';
 badge.style.color=sample && sample.error || meta && meta.stale ? '#c60' : '';
}
'''
                    data=data.replace('function CreateModemMultiverse(',hook+'\nfunction CreateModemMultiverse(',1)
                    a=data.index('        if (json?.[2]?.error) {')
                    z=data.index('        let rowsWcdma = [];',a)
                    data=data[:a]+'        aw1000ModemdataFreshness(modem,json[2]);\n        if(json[2].error)return;\n\n'+data[z:]
                    data=data.replace('parseInt(raw, 10) : 5','parseInt(raw, 10) : 10').replace('if (!Number.isFinite(n)) n = 5','if (!Number.isFinite(n)) n = 10').replace('return 5;','return 10;')
                    data=data.replace('if (interval && sec === 0 && typeof runFetchFn','if (!document.hidden && interval && sec === 0 && typeof runFetchFn')
                if target.endswith('modemdefine.js'):
                    data=data.replace("L.resolveDefault(fs.list('/dev'), null)","L.resolveDefault(fs.list('/dev'), [])").replace("data[1].trim()","String(data[1] || '').trim()")
                    data=data.replace("let have_smstool = installedList.find(function (s) { return s && s.indexOf('sms-tool') !== -1; }) ? true : false;","let have_smstool = installedList.some(function(s){return s && s.indexOf('ubus-at-daemon') !== -1;});")
                    data=data.replace("{ name: 'sms-tool', label: 'sms-tool' }","{ name: 'ubus-at-daemon', label: 'ubus-at-daemon（共用 AT 队列）' }")
                    data=data.replace("o.placeholder = _('Please select a port');","o.value('auto', '自动跟随 QModem AT 端口');\n    o.placeholder = _('Please select a port');").replace("o.default = 'wan';","o.default = 'wwan';")
                if target.endswith('modemupdate.js'):
                    a=data.index("\t\tlet info = ");z=data.index("\n\n",a)
                    data=data[:a]+"\t\tlet info = '固件内置兼容版：插件升级随固件更新提供。以下保留上游支持和问题反馈入口。';"+data[z:]
                if target.endswith('modemconfig.js'):
                    data=data.replace("o.default = '5';","o.default = '10';")
                    data=data.replace("_('Configuration panel for modemdata and gui application.')","_('Configuration panel for modemdata and gui application.') + ' 串口动态数据使用至少 10 秒的共享缓存；选择更短页面刷新间隔不会增加串口查询频率。'")
                data=chinese(data)
            write(target,data)
        elif name.startswith('ui/luci-app-modemdata/root/'):
            target=name[len('ui/luci-app-modemdata/root/'):]
            if not target.startswith('etc/uci-defaults/'):write(target,contents(name))
    write('usr/libexec/aw1000-modemdata-at',MODEMDATA_AT,'modemdata')
    write('usr/libexec/aw1000-modemdata-cache',MODEMDATA_CACHE,'modemdata')
    original=(root/'usr/share/modemdata/params.sh').read_text()
    a=original.index('addon() {');z=original.index('\nDEVICE=$1',a)
    original=original[:a]+'''addon() {
 [ -n "$ADDON" ] && ADDON="$ADDON,"
 row=$(jq -cn --argjson idx "$1" --arg key "$2" --arg value "$3" '{idx:$idx,key:$key,value:$value}')
 ADDON="$ADDON$row"
}
'''+original[z:]
    a=original.rindex('cat <<EOF')
    original=original[:a]+'''jq -n --arg csq "$CSQ" --arg signal "$CSQ_PER" --arg operator_name "$COPS" \\
 --arg operator_mcc "$COPS_MCC" --arg operator_mnc "$COPS_MNC" --arg country "$COUNTRY" \\
 --arg mode "$MODE" --arg registration "$REG" --arg lac_dec "$LAC_DEC" --arg lac_hex "$LAC_HEX" \\
 --arg cid_dec "$CID_DEC" --arg cid_hex "$CID_HEX" --argjson addon "[$ADDON]" \\
 '{csq:$csq,signal:$signal,operator_name:$operator_name,operator_mcc:$operator_mcc,operator_mnc:$operator_mnc,country:$country,mode:$mode,registration:$registration,lac_dec:$lac_dec,lac_hex:$lac_hex,cid_dec:$cid_dec,cid_hex:$cid_hex,addon:$addon}'
'''
    write('usr/share/modemdata/params-upstream.sh',original,'modemdata')
    for kind in ('product','params'):write('usr/share/modemdata/'+kind+'.sh','#!/bin/sh\nexec /usr/libexec/aw1000-modemdata-cache '+kind+' "$@"\n','modemdata')
    write('usr/bin/md_serial_ecm-upstream',(root/'usr/bin/md_serial_ecm').read_bytes())
    write('usr/bin/md_serial_ecm',MODEMDATA_SERIAL)
    write('etc/config/modemdata',(root/'etc/config/modemdata').read_text().replace("option updtime '5'","option updtime '10'"))
    write('etc/uci-defaults/97-aw1000-modemdata',MODEMDATA_DEFAULTS)
    menu=json.loads((root/'usr/share/luci/menu.d/luci-app-modemdata.json').read_text());menu.pop('admin/modem',None)
    titles={'Modemdata Status':'移动网络信息','Modem(s)':'模块信息','Diagnostics':'诊断','Modem(s) configuration':'模块定义','Package configuration':'插件配置','Package update and support':'更新与支持'}
    for row in menu.values():
        if row.get('title') in titles:row['title']=titles[row['title']]
    write('usr/share/luci/menu.d/luci-app-modemdata.json',json.dumps(menu,ensure_ascii=False,indent=2)+'\n')
    acl=json.loads((root/'usr/share/rpcd/acl.d/luci-app-modemdata.json').read_text())
    for section in ('read','write'):
        row=acl['luci-app-modemdata'][section]['file']
        if '/etc/modem/modemlist.json' in row:row['/tmp/aw1000-modemdata-modemlist.json']=row.pop('/etc/modem/modemlist.json')
    for name in ('/usr/bin/opkg','/usr/bin/ubus','/bin/ls','/usr/libexec/aw1000-modemdata-cache'):acl['luci-app-modemdata']['read']['file'][name]=['exec']
    write('usr/share/rpcd/acl.d/luci-app-modemdata.json',json.dumps(acl,ensure_ascii=False,indent=2)+'\n')
    for package,name in [('luci-app-modemdata','ui/LICENSE'),('modemdata','backend/README.md')]:write('usr/share/licenses/'+package+'/UPSTREAM',source[name],package)
    info=root/'usr/lib/opkg/info';info.mkdir(parents=True,exist_ok=True);status=root/'usr/lib/opkg/status'
    records=package_records(status.read_text())
    required={'modemdata':['lua','luci-lib-nixio','luci-lib-jsonc','jq','jsonfilter','ubus-at-daemon'],'luci-app-modemdata':['luci-base','modemdata']}
    for package in paths:
        missing=set(required[package])-records.keys()-paths.keys()
        if missing:raise ValueError('modemdata runtime dependencies missing: '+str(missing))
        version=lock['ui_version'] if package=='luci-app-modemdata' else lock['backend_version']
        control='Package: '+package+'\nVersion: '+version+'-aw1000\nArchitecture: all\nDepends: '+', '.join(required[package])+'\nDescription: AW1000 shared-AT modem information\n'
        if package=='luci-app-modemdata':control+='License: MIT\n'
        (info/(package+'.control')).write_text(control);(info/(package+'.list')).write_text('\n'.join(sorted(set(paths[package])))+'\n')
        if package=='luci-app-modemdata':(info/(package+'.conffiles')).write_text('/etc/config/modemdata\n/etc/config/defmodems\n')
        blocks=[x for x in status.read_text().strip().split('\n\n') if not x.startswith('Package: '+package+'\n')]
        status.write_text('\n\n'.join(blocks)+'\n\n'+control+'Status: install user installed\n\n')
    return {'ui_version':lock['ui_version'],'backend_version':lock['backend_version'],'ui_commit':lock['ui_commit'],'backend_commit':lock['backend_commit'],'views':5,'transport':'at-daemon','refresh_seconds':10,'product_cache_seconds':300,'hardware_verified':False,'additional_serial_daemon':False}
