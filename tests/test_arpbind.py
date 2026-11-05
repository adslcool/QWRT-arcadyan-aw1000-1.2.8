"""ARP ownership, hotplug recovery, target modules and source-delivery regressions."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import arpbind
import phone


def lua_value(value):
    if isinstance(value,str):return json.dumps(value)
    if isinstance(value,list):return '{'+','.join(lua_value(v) for v in value)+'}'
    if isinstance(value,dict):return '{'+','.join('['+lua_value(k)+']='+lua_value(v) for k,v in value.items())+'}'
    raise TypeError(value)


class ArpBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name);self.bin=self.path/'bin';self.bin.mkdir()
        self.run=self.path/'run';self.run.mkdir()
        self.worker=self.path/'worker.lua';self.worker.write_text(arpbind.WORKER)
        self.rules=[];self.devices=['br-lan','eth4'];self.neighbors=self.path/'neighbors.json'
        self.neighbors.write_text('{}')
        self.log=self.path/'ip.log'
        tool=self.bin/'ip';tool.write_text('''#!/usr/bin/env python3
import json,os,pathlib,sys
base=pathlib.Path(os.environ['TEST_ARP_ROOT']);p=base/'neighbors.json';entries=json.loads(p.read_text())
args=sys.argv[1:]
with (base/'ip.log').open('a') as f:f.write(json.dumps(args)+'\\n')
assert args[:2]==['-4','neigh'],args
action=args[2];ip=args[4] if action=='show' else args[3];dev=args[args.index('dev')+1];key=dev+'|'+ip
if action=='show':
 if key in entries:
  e=entries[key];print(ip+' dev '+dev+' lladdr '+e['mac']+' '+('PERMANENT' if e['permanent'] else 'REACHABLE'))
elif action=='replace':
 if os.environ.get('TEST_ARP_REPLACE_FAIL')=='1':sys.exit(2)
 entries[key]={'mac':args[args.index('lladdr')+1],'permanent':True}
elif action=='del':entries.pop(key,None)
else:raise AssertionError(args)
p.write_text(json.dumps(entries))
''');tool.chmod(0o755)
        self.env={**os.environ,'PATH':str(self.bin)+os.pathsep+os.environ['PATH'],
                  'TEST_ARP_ROOT':str(self.path),'AW1000_ARPBIND_RUN':str(self.run)}

    def rule(self,ip='192.168.1.20',mac='02:11:22:33:44:55',enabled='1'):
        return {'ipaddr':ip,'macaddr':mac,'ifname':'br-lan','enabled':enabled}

    def execute(self,action='apply',success=True):
        lua=shutil.which('lua5.1') or shutil.which('lua')
        self.assertIsNotNone(lua,'Lua 5.1 is required')
        bootstrap=self.path/'bootstrap.lua'
        bootstrap.write_text('local rules='+lua_value(self.rules)+'\nlocal devices='+lua_value(self.devices)+'''
package.preload['uci']=function() return {cursor=function() return {
 load=function() return true end,
 foreach=function(_,_,_,callback) for _,row in ipairs(rules) do callback(row) end end
} end} end
package.preload['nixio.fs']=function() return {access=function(path)
 for _,dev in ipairs(devices) do if path=='/sys/class/net/'..dev then return true end end
 local f=io.open(path,'r');if f then f:close();return true end;return false
end} end
arg={'''+ '[1]='+lua_value(action)+'}\ndofile('+lua_value(str(self.worker))+')\n')
        p=subprocess.run([lua,str(bootstrap)],env=self.env,capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode==0,success,p.stderr)
        return p

    def entries(self):return json.loads(self.neighbors.read_text())

    def test_add_replace_disable_and_stop_touch_only_owned_neighbors(self):
        foreign={'eth4|192.0.2.1':{'mac':'02:aa:bb:cc:dd:ee','permanent':False},
                 'br-lan|192.168.1.30':{'mac':'02:33:33:33:33:33','permanent':True}}
        self.neighbors.write_text(json.dumps(foreign));self.rules=[self.rule()];self.execute()
        self.assertEqual(self.entries()['br-lan|192.168.1.20']['mac'],'02:11:22:33:44:55')
        self.rules=[self.rule(mac='02:11:22:33:44:66')];self.execute()
        self.assertEqual(self.entries()['br-lan|192.168.1.20']['mac'],'02:11:22:33:44:66')
        self.rules=[self.rule(enabled='0')];self.execute();self.assertEqual(self.entries(),foreign)
        self.rules=[self.rule()];self.execute();self.execute('stop');self.assertEqual(self.entries(),foreign)
        self.assertNotIn('ip link',arpbind.INIT);self.assertNotIn('arp off',arpbind.WORKER)

    def test_foreign_permanent_entries_are_neither_replaced_nor_claimed(self):
        self.rules=[self.rule()];self.neighbors.write_text(json.dumps({'br-lan|192.168.1.20':{'mac':'02:aa:bb:cc:dd:ee','permanent':True}}))
        before=self.neighbors.read_bytes();self.execute(success=False);self.assertEqual(self.neighbors.read_bytes(),before)
        self.rules=[self.rule(mac='02:aa:bb:cc:dd:ee')];self.execute();self.assertEqual((self.run/'owned').read_text(),'')
        self.execute('stop');self.assertEqual(self.neighbors.read_bytes(),before)
        # An administrator may replace one of our entries after it was installed.
        self.neighbors.write_text('{}');self.rules=[self.rule()];self.execute()
        changed={'br-lan|192.168.1.20':{'mac':'02:77:77:77:77:77','permanent':True}}
        self.neighbors.write_text(json.dumps(changed));self.execute('stop')
        self.assertEqual(self.entries(),changed)

    def test_invalid_or_conflicting_rules_leave_existing_bindings_intact(self):
        self.rules=[self.rule()];self.execute();before=self.neighbors.read_bytes()
        for invalid in (self.rule(ip='192.168.1.999'),self.rule(ip="1.2.3.4;reboot"),self.rule(mac='ff:ff:ff:ff:ff:ff'),
                        {**self.rule(),'ifname':'br-lan;false'}):
            self.rules=[self.rule(),invalid];self.execute(success=False);self.assertEqual(self.neighbors.read_bytes(),before)
        self.rules=[self.rule(),self.rule(mac='02:11:22:33:44:66')]
        self.execute(success=False);self.assertEqual(self.neighbors.read_bytes(),before)
        (self.run/'owned').write_text('damaged state\n');self.rules=[self.rule()]
        self.execute(success=False);self.assertEqual(self.neighbors.read_bytes(),before)

    def test_interface_recreation_reapplies_and_failed_changes_keep_ownership(self):
        self.rules=[self.rule()];self.devices=[];self.execute(success=False);self.assertEqual(self.entries(),{})
        self.devices=['br-lan'];self.execute();original=(self.run/'owned').read_text()
        self.rules=[self.rule(mac='02:11:22:33:44:66')];self.env['TEST_ARP_REPLACE_FAIL']='1'
        self.execute(success=False);self.assertEqual((self.run/'owned').read_text(),original)
        del self.env['TEST_ARP_REPLACE_FAIL'];self.neighbors.write_text('{}');self.execute()
        self.assertEqual(self.entries()['br-lan|192.168.1.20']['mac'],'02:11:22:33:44:66')
        self.assertIn('ifup|ifupdate',arpbind.HOTPLUG)

    def test_real_qwrt_lua_uci_and_nixio_load_the_backend(self):
        target=os.environ.get('AW1000_TEST_TARGET_ROOT')
        if not target or not shutil.which('qemu-aarch64-static'):self.skipTest('original QWRT and QEMU required')
        target=Path(target);config=self.path/'config';config.mkdir();delta=self.path/'delta';delta.mkdir()
        (config/'arpbind').write_text("config arpbind\n option enabled '0'\n")
        bootstrap=self.path/'native.lua'
        bootstrap.write_text('package.cpath='+lua_value(str(target/'usr/lib/lua/?.so'))+'\n'+
            "local u=require('uci');local cursor=u.cursor;u.cursor=function() return cursor("+
            lua_value(str(config))+','+lua_value(str(delta))+') end\narg={[1]="apply"}\ndofile('+lua_value(str(self.worker))+')\n')
        p=subprocess.run(['qemu-aarch64-static','-L',str(target),str(target/'usr/bin/lua'),str(bootstrap)],
                         env=self.env,capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual((self.run/'owned').read_text(),'')
        self.assertFalse(self.log.exists(),'disabled rules must not invoke ip')


class SourceIntegrationTests(unittest.TestCase):
    def test_chinese_luci_install_preserves_configuration_and_phone_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);info=root/'usr/lib/opkg/info';info.mkdir(parents=True)
            status=root/'usr/lib/opkg/status';status.write_text('\n\n'.join('Package: '+n for n in ('luci-base','lua','libuci-lua','luci-lib-nixio','ip-full'))+'\n\n')
            arpbind.install(root)
            menu=json.loads((root/'usr/share/luci/menu.d/luci-app-arpbind.json').read_text())
            self.assertEqual(menu['admin/network/arpbind']['title'],'IP/MAC 绑定')
            view=(root/'www/luci-static/resources/view/arpbind.js').read_text()
            self.assertIn("'ip4addr'",view);self.assertIn("o.default = 'br-lan'",view);self.assertIn('静态',view)
            config=root/'etc/config/arpbind';config.write_text('# existing user bindings\n')
            arpbind.install(root);self.assertEqual(config.read_text(),'# existing user bindings\n')
            self.assertEqual(status.read_text().count('Package: luci-app-arpbind\n'),1)
            self.assertEqual((root/'etc/rc.d/S80arpbind').readlink(),Path('../init.d/arpbind'))
            menu_path=root/'usr/share/luci/menu.d/luci-app-fxs.json';menu_path.parent.mkdir(parents=True,exist_ok=True)
            phone_menu={'admin/services/fxs/phone':{'title':'电话'},'admin/services/fxs/build':{'title':'改造状态'}}
            menu_path.write_text(json.dumps(phone_menu));page=root/'www/luci-static/resources/view/fxs';page.mkdir()
            (page/'phone.js').write_text('phone remains');(page/'build.js').write_text('old status')
            phone.hide_build_status(root)
            self.assertEqual(json.loads(menu_path.read_text()),{'admin/services/fxs/phone':{'title':'电话'}})
            self.assertFalse((page/'build.js').exists());self.assertEqual((page/'phone.js').read_text(),'phone remains')

    def test_source_archive_and_root_folder_share_versioned_name(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'source';script=root/'scripts/package_source.py';script.parent.mkdir(parents=True)
            shutil.copyfile(ROOT/'scripts/package_source.py',script);(root/'VERSION').write_text('1.2.3\n')
            subprocess.run([sys.executable,str(script)],check=True,capture_output=True,text=True)
            output=Path(temp)/'QWRT-arcadyan-aw1000-1.2.3.zip'
            with zipfile.ZipFile(output) as z:
                self.assertTrue(all(p.startswith('QWRT-arcadyan-aw1000-1.2.3/') for p in z.namelist()))
                self.assertEqual(z.read('QWRT-arcadyan-aw1000-1.2.3/VERSION'),b'1.2.3\n')


if __name__=='__main__':unittest.main()
