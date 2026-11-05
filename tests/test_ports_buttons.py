"""Native QWRT UCI migration and isolated Ethernet/button behavior."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import unittest

import test_internet as fixtures

ROOT=Path(__file__).resolve().parents[1]


class PortNamingTests(unittest.TestCase):
    def setUp(self):
        target=os.environ.get('AW1000_TEST_TARGET_ROOT')
        if not target or not shutil.which('qemu-aarch64-static'):self.skipTest('QWRT root and QEMU required')
        self.case=fixtures.InternetTests();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.path=self.case.path;self.config=self.case.config;self.bin=self.case.bin
        self.target=Path(target);self.env=dict(self.case.env)
        self.run=self.path/'ports-run';self.state=self.path/'ports-state';self.sys=self.path/'net';self.sys.mkdir()
        self.board=self.path/'board';self.board.write_text('arcadyan,aw1000\n')
        self.board_json=self.path/'board.json'
        self.board_json.write_text(json.dumps({'network':{'lan':{'ifname':'eth0 eth1 eth2 eth3 eth4'},'wan':{'ifname':'wwan0'}},'model':{'id':'keep'}}))
        self.env.update(AW1000_PORTS_SYS=str(self.sys),AW1000_PORTS_RUN=str(self.run),
                        AW1000_LAYOUT_STATE=str(self.state),AW1000_BOARD_FILE=str(self.board),
                        AW1000_PORTS_BOARD_JSON=str(self.board_json))
        for n in range(5):
            p=self.sys/('eth'+str(n));p.mkdir();(p/'ifindex').write_text(str(n+2));(p/'type').write_text('1');(p/'flags').write_text('0x1003')
        (self.config/'network').write_text('''config device 'br_lan'
 option name 'br-lan'
 option type 'bridge'
 list ports 'eth0'
 list ports 'eth1'
 list ports 'eth2'
 list ports 'eth3'
config interface 'lan'
 option device 'br-lan'
 option ipaddr '192.168.9.1'
config device 'aw_wan_vlan'
 option type '8021q'
 option ifname 'eth4'
 option name 'eth4.85'
 option vid '85'
config interface 'wan'
 option proto 'pppoe'
 option device 'eth4.85'
 option username 'test-user'
 option password 'literal-password'
config interface 'wan_v6'
 option proto 'dhcpv6'
 option device '@wan'
''')
        (self.config/'aw1000').write_text("config policy 'internet'\n option base_device 'eth4'\n option vlan '85'\n")
        (self.config/'arpbind').write_text("config arpbind 'user'\n option enabled '1'\n option ifname 'eth1'\n option ipaddr '192.168.9.20'\n option macaddr '02:11:22:33:44:55'\n")
        (self.config/'system').write_text("config led 'custom'\n option dev 'eth4'\n")
        delta=self.path/'uci-delta';delta.mkdir()
        qemu=shutil.which('qemu-aarch64-static')
        self.case.executable('uci','#!/bin/sh\nexec '+shlex.join([qemu,'-L',str(self.target),str(self.target/'sbin/uci'),'-c',str(self.config),'-P',str(delta)])+' "$@"\n')
        prefix='package.cpath='+json.dumps(str(self.target/'usr/lib/lua/?.so'))+';package.path='+json.dumps(str(self.target/'usr/lib/lua/?.lua'))+';'
        self.case.executable('native-lua','#!/bin/sh\nexec '+shlex.join([qemu,'-L',str(self.target),str(self.target/'usr/bin/lua'),'-e',prefix])+' "$@"\n')
        self.env['AW1000_PORTS_LUA']=str(self.bin/'native-lua')
        self.case.executable('pidof','#!/bin/sh\n[ "${TEST_NETIFD_ACTIVE:-0}" = 1 ]\n')
        self.case.executable('sleep','#!/bin/sh\nexit 0\n')
        self.case.executable('ip','''#!/usr/bin/env python3
import json,os,pathlib,sys
root=pathlib.Path(os.environ['AW1000_PORTS_SYS']);args=sys.argv[1:]
with (root.parent/'ip-ports.log').open('a') as f:f.write(json.dumps(args)+'\\n')
assert args[:3]==['link','set','dev'],args
p=root/args[3]
if args[4]=='name':
 if os.environ.get('TEST_RENAME_FAIL')==args[5]:sys.exit(2)
 if not p.exists() or (root/args[5]).exists():sys.exit(3)
 p.rename(root/args[5])
elif args[4] in ('up','down'):
 if not p.exists():sys.exit(4)
 (p/'flags').write_text('0x1003' if args[4]=='up' else '0x1002')
else:raise AssertionError(args)
''')
        self.script=ROOT/'files/etc/init.d/aw1000-ports'

    def execute(self,success=True):
        p=subprocess.run(['sh','-c','. '+shlex.quote(str(self.script))+'; start'],env=self.env,capture_output=True,text=True,timeout=30)
        self.assertEqual(p.returncode==0,success,p.stderr)
        return p

    def get(self,key):return subprocess.check_output(['uci','-q','get',key],env=self.env,text=True).strip()

    def test_physical_rename_migrates_bridge_vlan_policy_and_arp_bindings(self):
        self.execute()
        self.assertEqual(sorted(p.name for p in self.sys.iterdir()),['lan1','lan2','lan3','lan4','wan'])
        for key,expected in {'network.br_lan.ports':'lan1 lan2 lan3 lan4','network.wan.device':'wan.85',
          'network.aw_wan_vlan.ifname':'wan','network.aw_wan_vlan.name':'wan.85','network.wan_v6.device':'@wan',
          'aw1000.internet.base_device':'wan','arpbind.user.ifname':'lan2','system.custom.dev':'wan',
          'network.lan.ipaddr':'192.168.9.1','network.wan.password':'literal-password'}.items():self.assertEqual(self.get(key),expected,key)
        for cfg in ('network','aw1000','arpbind','system'):
            self.assertEqual(subprocess.check_output(['uci','changes',cfg],env=self.env,text=True),'')
        board=json.loads(self.board_json.read_text());self.assertEqual(board['network']['lan']['ifname'],'lan1 lan2 lan3 lan4')
        self.assertEqual(board['network']['wan']['ifname'],'wan');self.assertEqual(board['model']['id'],'keep')
        self.assertTrue((self.state/'ports-layout-1.0.16-backup/network').exists())
        before=(self.path/'ip-ports.log').read_bytes();self.execute();self.assertEqual((self.path/'ip-ports.log').read_bytes(),before)

    def test_failed_rename_rolls_back_and_keeps_legacy_network_usable(self):
        self.env['TEST_RENAME_FAIL']='lan3';self.execute(False)
        self.assertEqual(sorted(p.name for p in self.sys.iterdir()),['eth0','eth1','eth2','eth3','eth4'])
        self.assertEqual(self.get('network.br_lan.ports'),'eth0 eth1 eth2 eth3')
        self.assertEqual(self.get('network.wan.device'),'eth4.85')
        self.assertIn('mode=legacy',(self.run/'status').read_text())
        for p in self.sys.iterdir():self.assertEqual((p/'flags').read_text(),'0x1003')

    def test_name_collision_and_active_netifd_do_not_disrupt_ports(self):
        p=self.sys/'wan';p.mkdir();(p/'ifindex').write_text('999')
        self.execute(False);self.assertEqual((p/'ifindex').read_text(),'999')
        self.assertFalse((self.path/'ip-ports.log').exists())
        self.env['TEST_NETIFD_ACTIVE']='1';before=(self.config/'network').read_bytes()
        self.execute(False);self.assertEqual((self.config/'network').read_bytes(),before)
        self.assertFalse((self.path/'ip-ports.log').exists())

    def test_pending_user_edits_prevent_renaming(self):
        subprocess.run(['uci','set','network.wan.pending=keep'],env=self.env,check=True)
        before=(self.config/'network').read_bytes();self.execute(False)
        self.assertEqual((self.config/'network').read_bytes(),before)
        self.assertEqual(self.get('network.wan.pending'),'keep')
        self.assertFalse((self.path/'ip-ports.log').exists())


class ButtonTests(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.InternetTests();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.path=self.case.path;self.bin=self.case.bin;self.env=dict(self.case.env)
        self.run=self.path/'button-run';self.run.mkdir();self.lock=self.path/'button-lock';self.lock.mkdir()
        self.clock=self.path/'button-uptime';self.clock.write_text('100.00 0.00\n')
        self.sys=self.path/'wifi-sys';self.sys.mkdir()
        for name in ('ath0','ath1'):
            p=self.sys/name;p.mkdir();(p/'flags').write_text('0x1343')
        self.env.update(AW1000_BUTTON_RUN=str(self.run),AW1000_BUTTON_LOCK=str(self.lock),
                        AW1000_BUTTON_UPTIME=str(self.clock),AW1000_WIFI_SYS=str(self.sys))
        self.case.write_config('wireless',{'wifi0':{'.type':'wifi-device','disabled':'0'},'wifi1':{'.type':'wifi-device','disabled':'0'},
            'aw1000_2g':{'.type':'wifi-iface','mode':'ap','device':'wifi1','wps_pushbutton':'1','encryption':'psk2+ccmp','ssid':'keep-ssid','key':'keep-secret'},
            'aw1000_5g':{'.type':'wifi-iface','mode':'ap','device':'wifi0','wps_pushbutton':'0','encryption':'psk2+ccmp'}})
        self.case.executable('wifi','''#!/usr/bin/env python3
import json,os,pathlib,sys
p=pathlib.Path(os.environ['TEST_ROOT']);cfg=json.loads((pathlib.Path(os.environ['TEST_CONFIG'])/'wireless').read_text())
assert sys.argv[1:] == ['up'],sys.argv
states={cfg[r]['disabled'] for r in ('wifi0','wifi1')};assert len(states)==1,states
disabled=states.pop();assert disabled in ('0','1')
with (p/'wifi-actions').open('a') as f:f.write(sys.argv[1]+'\\n')
if os.environ.get('TEST_WIFI_NO_APPLY')!='1':
 root=pathlib.Path(os.environ['AW1000_WIFI_SYS'])
 for name in ('ath0','ath1'):(root/name/'flags').write_text('0x1002' if disabled=='1' else '0x1343')
if os.environ.get('TEST_WIFI_FAIL')==sys.argv[1]:sys.exit(1)
''')
        self.env['AW1000_WIFI_CMD']=str(self.bin/'wifi')
        self.helper=ROOT/'files/usr/sbin/aw1000-wifi-toggle'

    def execute(self,script=None,success=True,target=False):
        cmd=['sh']
        if target:
            root=Path(os.environ['AW1000_TEST_TARGET_ROOT']);cmd=['qemu-aarch64-static','-L',str(root),str(root/'bin/busybox'),'sh']
        p=subprocess.run(cmd+[str(script or self.helper)],env=self.env,capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode==0,success,p.stderr)
        return p

    def advance(self):self.clock.write_text('103.00 0.00\n')

    def test_wifi_button_commits_both_radios_and_deduplicates_events(self):
        self.execute();cfg=self.case.read_config('wireless')
        self.assertEqual((cfg['wifi0']['disabled'],cfg['wifi1']['disabled']),('1','1'))
        self.assertEqual(cfg['aw1000_2g']['key'],'keep-secret')
        self.assertTrue((self.run/'aw1000-wifi-off').exists())
        self.assertIn('result=matched',(self.run/'aw1000-wifi.button-state').read_text())
        self.execute();self.assertEqual((self.path/'wifi-actions').read_text(),'up\n')
        self.advance();self.execute();self.assertFalse((self.run/'aw1000-wifi-off').exists())
        self.assertEqual(self.case.read_config('wireless')['wifi1']['disabled'],'0')
        self.assertEqual((self.path/'wifi-actions').read_text(),'up\nup\n')

    def test_wifi_benign_nonzero_never_causes_inverse_reload_and_pending_edits_are_preserved(self):
        self.env['TEST_WIFI_FAIL']='up';self.execute()
        self.assertEqual(self.case.read_config('wireless')['wifi0']['disabled'],'1')
        self.assertEqual((self.path/'wifi-actions').read_text(),'up\n')
        state=(self.run/'aw1000-wifi.button-state').read_text()
        self.assertIn('reload_rc=1',state);self.assertIn('result=matched',state)
        self.advance();subprocess.run(['uci','set','wireless.wifi0.disabled=0'],env=self.env,check=True)
        self.execute(success=False)
        self.assertEqual(subprocess.check_output(['uci','get','wireless.wifi0.disabled'],env=self.env,text=True).strip(),'0')
        self.assertEqual((self.path/'wifi-actions').read_text(),'up\n')

    def test_wifi_runtime_state_wins_over_stale_uci_flags(self):
        cfg=self.case.read_config('wireless')
        cfg['wifi0']['disabled']=cfg['wifi1']['disabled']='1';self.case.write_config('wireless',cfg)
        self.execute()
        state=(self.run/'aw1000-wifi.button-state').read_text()
        self.assertIn('action=disable',state);self.assertIn('before_aps=ath0 ath1',state)
        self.assertEqual(self.case.read_config('wireless')['wifi0']['disabled'],'1')

    def test_wps_vendor_library_unset_variables_do_not_abort_pbc(self):
        sock=self.run/'hostapd-wifi0';sock.mkdir();(sock/'ath0').touch();(sock/'ath1').touch()
        utils=self.path/'wifi-utils.sh';utils.write_text('[ -z "$UNSET_VENDOR_VARIABLE" ] || :\nget_wpad_var_run() { printf "%s\\n" '+shlex.quote(str(self.run))+'; }\n')
        self.env['AW1000_WIFI_UTILS']=str(utils)
        self.case.executable('hostapd_cli','#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_ROOT/wps-actions"\necho OK\n')
        script=ROOT/'files/usr/sbin/aw1000-wps-button';self.execute(script)
        calls=(self.path/'wps-actions').read_text();self.assertEqual(calls.count('wps_pbc'),2)
        self.execute(script);self.assertEqual((self.path/'wps-actions').read_text(),calls)
        self.assertIn('ap_count=2',(self.run/'aw1000-wps.button-state').read_text())

    def test_qsdk_wps_upgrade_only_repairs_owned_opted_in_aps(self):
        script=self.case.rewrite('usr/sbin/aw1000-network-cleanup')
        script.write_text(script.read_text().replace('/etc/',str(self.path/'etc')+'/'))
        self.case.script=script;self.case.invoke()
        cfg=self.case.read_config('wireless');self.assertEqual(cfg['aw1000_2g']['wps_pbc'],'1')
        self.assertNotIn('wps_pbc',cfg['aw1000_5g'])
        self.assertIn('wps_pbc=1',(ROOT/'files/usr/sbin/aw1000-provision').read_text())

    def test_real_busybox_button_helper_avoids_vendor_nounset_failure(self):
        target=os.environ.get('AW1000_TEST_TARGET_ROOT')
        if not target or not shutil.which('qemu-aarch64-static'):self.skipTest('QWRT root and QEMU required')
        root=Path(target)
        self.env.pop('IPKG_INSTROOT',None)
        old=subprocess.run(['qemu-aarch64-static','-L',str(root),str(root/'bin/busybox'),'sh','-eu','-c','. /lib/functions.sh'],
            env=self.env,capture_output=True,text=True,timeout=10)
        self.assertNotEqual(old.returncode,0);self.assertIn('IPKG_INSTROOT',old.stderr)
        self.execute(target=True)
        self.assertEqual(self.case.read_config('wireless')['wifi0']['disabled'],'1')

    def test_button_entry_points_ignore_press_and_dispatch_release(self):
        for name in ('wlan','rfkill','wifi','wps'):
            path=self.path/('button-'+name)
            text=(ROOT/'files/etc/rc.button'/name).read_text().replace('/usr/sbin/aw1000-wifi-toggle',str(self.bin/'button-action')).replace('/usr/sbin/aw1000-wps-button',str(self.bin/'button-action'))
            path.write_text(text);self.case.executable('button-action','#!/bin/sh\necho action >> "$TEST_ROOT/button-actions"\n')
            self.env['ACTION']='pressed';self.execute(path)
            self.env['ACTION']='released';self.execute(path)
        self.assertEqual((self.path/'button-actions').read_text().splitlines(),['action']*4)



    def test_scan_reads_cache_only_and_reuses_result_without_rebuilding_ap(self):
        for i,name in enumerate(('ath0','ath1')):
            (self.sys/name/'ifindex').write_text(str(30+i))
        iw=self.case.executable('iw-cache','''#!/usr/bin/env python3
import os,sys,pathlib
assert sys.argv[1:]==['dev','ath0','scan','dump'],sys.argv
p=pathlib.Path(os.environ['TEST_ROOT'])
with (p/'scan-actions').open('a') as f:f.write('cache\\n')
print('BSS 02:11:22:33:44:55(on ath0)\\n\\tfreq: 5745\\n\\tsignal: -60.0 dBm\\n\\tSSID: cache-only')
''')
        self.env['AW1000_WIFI_IW']=str(iw)
        before=(self.case.config/'wireless').read_bytes()
        for _ in range(2):
            p=subprocess.run(['sh',str(self.helper),'--scan','wifi0'],env=self.env,text=True,capture_output=True,timeout=8)
            self.assertEqual(p.returncode,0,p.stderr);r=json.loads(p.stdout)
            self.assertFalse(r['active_scan_supported']);self.assertEqual(r['mode'],'cache');self.assertIn('cache-only',r['stdout'])
        self.assertEqual((self.path/'scan-actions').read_text(),'cache\n')
        self.assertFalse((self.path/'wifi-actions').exists())
        self.assertEqual((self.case.config/'wireless').read_bytes(),before)

    def test_kernel_vap_events_are_scoped_to_current_interface_generation(self):
        (self.sys/'ath0/ifindex').write_text('26')
        lines='noise\nkernel: [ 306.835914] wlan: mlme_ext_vap_down: VAP (ath0) aa:bb:cc:dd:ee:ff is down\n'
        p=subprocess.run(['sh',str(self.helper),'--events'],input=lines,env=self.env,text=True,capture_output=True,timeout=5)
        self.assertEqual(p.returncode,0,p.stderr)
        event=self.run/'aw1000-wifi/events/ath0'
        self.assertEqual(event.read_text(),'state=down\nifindex=26\ntime=306\n')
        # The monitor never mutates radio flags or configuration.
        self.assertEqual((self.sys/'ath0/flags').read_text(),'0x1343')
        self.assertFalse((self.path/'wifi-actions').exists())
        up='kernel: [ 310.20] wlan: mlme_ext_vap_up: VAP (ath0) aa:bb:cc:dd:ee:ff is up\n'
        subprocess.run(['sh',str(self.helper),'--events'],input=up,env=self.env,text=True,check=True)
        self.assertIn('state=up',event.read_text())

if __name__=='__main__':unittest.main()
