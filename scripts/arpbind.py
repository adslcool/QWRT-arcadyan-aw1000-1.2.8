"""Install pinned Chinese LuCI ARP binding, with cleanup limited to owned entries."""
import ast
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]

INIT = r'''#!/bin/sh /etc/rc.common
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2015 GuoGuo <gch981213@gmail.com>
# Copyright (C) 2025 ImmortalWrt.org
# AW1000 adaptation: serialize reconciliation; never reset ARP on network devices.
START=80
STOP=20
USE_PROCD=1
sync_bindings() (
    umask 077
    mkdir -p /var/run/arpbind || exit 1
    exec 9>/var/run/arpbind/lock
    flock -x 9 || exit 1
    /usr/bin/lua /usr/sbin/aw1000-arpbind.lua "$1"
)
start_service() { sync_bindings apply; }
reload_service() { sync_bindings apply; }
stop_service() { sync_bindings stop; }
service_triggers() { procd_add_reload_trigger arpbind; }
'''

HOTPLUG = r'''#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# AW1000: a recreated bridge needs its permanent neighbors restored.
case "$ACTION" in
    ifup|ifupdate)
        /etc/init.d/arpbind enabled && /etc/init.d/arpbind reload
        ;;
esac
exit 0
'''

WORKER = r'''#!/usr/bin/lua
-- SPDX-License-Identifier: Apache-2.0
-- AW1000 replacement backend for luci-app-arpbind. Only manage owned IPv4 neighbors.
local fs = require('nixio.fs')
local uci = require('uci')
local run = os.getenv('AW1000_ARPBIND_RUN') or '/var/run/arpbind'
local state = run .. '/owned'
local function log(s) io.stderr:write('arpbind: ' .. s .. '\n') end
local function quote(s) return "'" .. s:gsub("'", "'\\''") .. "'" end
local function ipv4(s)
    if type(s) ~= 'string' then return nil end
    local a,b,c,d = s:match('^(%d+)%.(%d+)%.(%d+)%.(%d+)$')
    if not a then return nil end
    a,b,c,d = tonumber(a),tonumber(b),tonumber(c),tonumber(d)
    if a < 1 or a >= 224 or a == 127 or b > 255 or c > 255 or d > 255 then return nil end
    return string.format('%d.%d.%d.%d',a,b,c,d)
end
local function mac(s)
    if type(s) ~= 'string' or #s ~= 17 or not s:match('^%x%x:%x%x:%x%x:%x%x:%x%x:%x%x$') then return nil end
    s = s:lower()
    if tonumber(s:sub(1,2),16) % 2 ~= 0 or s == '00:00:00:00:00:00' then return nil end
    return s
end
local function record(ip,hw,dev)
    ip,hw = ipv4(ip),mac(hw)
    if not ip or not hw or type(dev) ~= 'string' or #dev < 1 or #dev > 15 or dev:find('[^%w_.:%-]') then return nil end
    return {ip=ip,mac=hw,dev=dev,key=dev .. '|' .. ip}
end
local function sorted(t)
    local keys = {}; for key in pairs(t) do keys[#keys+1]=key end
    table.sort(keys); return keys
end
local function save(entries)
    local file,err = io.open(state .. '.new','w')
    if not file then error('cannot prepare ownership state: ' .. tostring(err)) end
    for _,key in ipairs(sorted(entries)) do
        local e=entries[key]
        if not file:write(e.ip .. ' ' .. e.mac .. ' ' .. e.dev .. '\n') then
            file:close(); error('cannot write ownership state')
        end
    end
    if not file:close() then error('cannot close ownership state') end
    assert(os.rename(state .. '.new',state),'cannot publish ownership state')
end
local function read_owned()
    local entries = {}; local file = io.open(state,'r')
    if not file then
        assert(not fs.access(state),'cannot read ownership state')
        return entries
    end
    for line in file:lines() do
        local ip,hw,dev = line:match('^(%S+) (%S+) (%S+)$')
        local e=record(ip,hw,dev)
        assert(e and not entries[e.key],'invalid ownership state; no neighbors changed')
        entries[e.key]=e
    end
    file:close(); return entries
end
local function desired_rules()
    local entries = {}; local cursor=uci.cursor()
    assert(cursor:load('arpbind'),'cannot load arpbind configuration')
    cursor:foreach('arpbind','arpbind',function(s)
        if s.enabled == '0' then return end
        local e=record(s.ipaddr,s.macaddr,s.ifname)
        assert(e,'invalid enabled binding; no neighbors changed')
        assert(not entries[e.key] or entries[e.key].mac == e.mac,'conflicting bindings; no neighbors changed')
        entries[e.key]=e
    end)
    return entries
end
local function current(e)
    if not fs.access('/sys/class/net/' .. e.dev) then return nil,'device unavailable' end
    local pipe=io.popen('ip -4 neigh show to ' .. quote(e.ip) .. ' dev ' .. quote(e.dev) ..
        ' 2>/dev/null; printf "\nAW1000_STATUS=%s\n" "$?"','r')
    if not pipe then return nil,'neighbor query failed' end
    local out=pipe:read('*a'); pipe:close()
    if out:match('\nAW1000_STATUS=(%d+)\n$') ~= '0' then return nil,'neighbor query failed' end
    return {mac=(out:match('lladdr%s+(%x%x:%x%x:%x%x:%x%x:%x%x:%x%x)') or ''):lower(),
            permanent=out:find('PERMANENT',1,true) ~= nil}
end
local function mutate(action,e)
    local command='ip -4 neigh ' .. action .. ' ' .. quote(e.ip)
    if action == 'replace' then command=command .. ' lladdr ' .. quote(e.mac) .. ' nud permanent' end
    local a,_,c=os.execute(command .. ' dev ' .. quote(e.dev) .. ' >/dev/null 2>&1')
    return a == 0 or (a == true and c == 0)
end
local function reconcile()
    local action=arg[1] or 'apply'
    assert(action == 'apply' or action == 'stop','unsupported action')
    local owned=read_owned()
    local wanted=action == 'stop' and {} or desired_rules()
    -- Validate all rules and verify state is writable before changing a neighbor.
    save(owned)
    local failed=false
    for _,key in ipairs(sorted(wanted)) do
        local e=wanted[key]; local cur,err=current(e); local old=owned[key]
        if not cur then
            log(e.dev .. ': ' .. err); failed=true
        elseif cur.permanent and not (old and old.mac == cur.mac) then
            -- A matching pre-existing permanent entry is satisfied, but not ours.
            if old then owned[key]=nil; save(owned) end
            if cur.mac ~= e.mac then log(e.ip .. ': permanent neighbor owned elsewhere'); failed=true end
        elseif not (old and cur.permanent and cur.mac == e.mac) then
            if mutate('replace',e) then owned[key]=e; save(owned)
            else log(e.ip .. ': cannot install binding'); failed=true end
        end
    end
    for _,key in ipairs(sorted(owned)) do
        if not wanted[key] then
            local e=owned[key]; local cur,err=current(e)
            if not cur and err == 'neighbor query failed' then
                log(e.ip .. ': ' .. err); failed=true
            elseif cur and cur.permanent and cur.mac == e.mac then
                if mutate('del',e) then owned[key]=nil; save(owned)
                else log(e.ip .. ': cannot remove binding'); failed=true end
            else owned[key]=nil; save(owned) end
        end
    end
    return not failed
end
local ok,result=pcall(reconcile)
if not ok then log(tostring(result)) end
os.exit(ok and result and 0 or 1)
'''


def upstream_files():
    lock=json.loads((ROOT/'sources.lock.json').read_text())['arpbind']
    archive=ROOT/lock['archive']
    blob=archive.read_bytes()
    if lock.get('inner_archive'):
        if hashlib.sha256(blob).hexdigest()!=lock['container_sha256']:
            raise ValueError('LuCI sources container mismatch')
        with tarfile.open(fileobj=io.BytesIO(blob),mode='r:xz') as container:
            blob=container.extractfile(lock['inner_archive']).read()
    if hashlib.sha256(blob).hexdigest()!=lock['sha256']:
        raise ValueError('ARP 绑定源码归档校验失败')
    result={}
    with tarfile.open(fileobj=io.BytesIO(blob),mode='r:xz') as source:
        for member in source:
            name=member.name;path=PurePosixPath(name)
            if not member.isfile() or path.is_absolute() or '..' in path.parts or '\\' in name or name not in lock['files'] or name in result or member.size>128*1024:
                raise ValueError('ARP 绑定源码归档路径或大小不符')
            data=source.extractfile(member).read()
            if hashlib.sha256(data).hexdigest()!=lock['files'][name]:raise ValueError('ARP 绑定文件校验失败：'+name)
            blob=lock['git_blobs'].get(name)
            if blob and hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()!=blob:
                raise ValueError('ARP 绑定 Git blob 不符：'+name)
            result[name]=data
    if result.keys()!=lock['files'].keys():raise ValueError('ARP 绑定归档不完整')
    return result


def chinese_view(text,catalog):
    translations={};key=value='';field=None
    for line in catalog.splitlines()+['']:
        if line.startswith('msgid '):key=ast.literal_eval(line[6:]);value='';field='key'
        elif line.startswith('msgstr '):value=ast.literal_eval(line[7:]);field='value'
        elif line.startswith('"'):
            if field=='key':key+=ast.literal_eval(line)
            elif field=='value':value+=ast.literal_eval(line)
        elif not line.strip():
            if key and value:translations[key]=value
            key=value='';field=None
    def translate(match):
        message=ast.literal_eval(match.group(1))
        if message not in translations:raise ValueError('ARP 绑定缺少中文词条：'+message)
        return '_('+json.dumps(translations[message],ensure_ascii=False)+')'
    text=re.sub(r"_\(('(?:\\.|[^'\\])*')\)",translate,text)
    text=text.replace('let hosts = data[0]?.hosts;', 'let hosts = data[0]?.hosts || {};')
    text=text.replace("o.datatype = 'ipaddr';", "o.datatype = 'ip4addr';")
    text=text.replace("o.multiple = false;", "o.default = 'br-lan';\n\t\to.multiple = false;")
    text=text.replace("\t\treturn m.render();", "\t\tm.description += ' 这里只固定路由器的 IPv4 ARP 映射；自动分配固定 IP 请在 DHCP/DNS 的静态租约中设置。';\n\t\treturn m.render();")
    return '// AW1000: Chinese UI, IPv4 validation and default LAN bridge.\n'+text


def install(root):
    from customize import package_records
    source=upstream_files();status=root/'usr/lib/opkg/status'
    records=package_records(status.read_text())
    required=['luci-base','lua','libuci-lua','luci-lib-nixio','ip-full']
    if not set(required)<=records.keys():raise ValueError('ARP 绑定依赖缺失：'+str(set(required)-records.keys()))
    paths=[]
    for name,data in source.items():
        if name.startswith('root/'):target=name[5:]
        elif name.startswith('htdocs/'):target='www/'+name[7:]
        else:continue
        if target=='etc/init.d/arpbind':data=INIT.encode()
        elif target=='etc/hotplug.d/iface/50-arpbind':data=HOTPLUG.encode()
        elif target=='www/luci-static/resources/view/arpbind.js':data=chinese_view(data.decode(),source['po/zh_Hans/arpbind.po'].decode()).encode()
        elif target=='usr/share/luci/menu.d/luci-app-arpbind.json':
            menu=json.loads(data);menu['admin/network/arpbind']['title']='IP/MAC 绑定';data=(json.dumps(menu,ensure_ascii=False,indent=2)+'\n').encode()
        if target=='etc/config/arpbind':data=data.replace(b"option ifname ''",b"option ifname 'br-lan'")
        dest=root/target;dest.parent.mkdir(parents=True,exist_ok=True)
        if target=='etc/config/arpbind' and dest.exists():paths.append('/'+target);continue
        dest.write_bytes(data)
        dest.chmod(0o755 if data.startswith(b'#!') else 0o644);paths.append('/'+target)
        if dest.suffix=='.js':subprocess.run(['node','--check',str(dest)],check=True)
        if data.startswith(b'#!/bin/sh'):subprocess.run(['sh','-n',str(dest)],check=True)
    worker=root/'usr/sbin/aw1000-arpbind.lua';worker.parent.mkdir(parents=True,exist_ok=True)
    worker.write_text(WORKER);worker.chmod(0o755);paths.append('/usr/sbin/aw1000-arpbind.lua')
    license_path=root/'usr/share/licenses/luci-app-arpbind/LICENSE';license_path.parent.mkdir(parents=True,exist_ok=True)
    license_path.write_bytes(source['LICENSE']);paths.append('/usr/share/licenses/luci-app-arpbind/LICENSE')
    rc=root/'etc/rc.d';rc.mkdir(parents=True,exist_ok=True)
    for name in ('S80arpbind','K20arpbind'):
        link=rc/name;link.unlink(missing_ok=True);link.symlink_to('../init.d/arpbind');paths.append('/etc/rc.d/'+name)
    info=root/'usr/lib/opkg/info';info.mkdir(parents=True,exist_ok=True)
    control=('Package: luci-app-arpbind\nVersion: 1.aw1000\nArchitecture: all\nLicense: Apache-2.0\n'
             'Depends: '+', '.join(required)+'\nDescription: Chinese IPv4 ARP bindings with scoped cleanup\n')
    (info/'luci-app-arpbind.control').write_text(control)
    (info/'luci-app-arpbind.list').write_text('\n'.join(paths)+'\n')
    (info/'luci-app-arpbind.conffiles').write_text('/etc/config/arpbind\n')
    text=status.read_text()
    if 'luci-app-arpbind' in records:
        text='\n\n'.join(block for block in text.strip().split('\n\n') if not block.startswith('Package: luci-app-arpbind\n'))+'\n\n'
    status.write_text(text+control+'Status: install user installed\n\n')
    lock=json.loads((ROOT/'sources.lock.json').read_text())['arpbind']
    return {'package':'luci-app-arpbind','commit':lock['commit'],'archive_sha256':lock['sha256'],
            'chinese':True,'default_device':'br-lan','cleanup':'owned-permanent-neighbors-only','hardware_verified':False}
