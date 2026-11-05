"""Exercise interface migration, modem naming and removal boundaries."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

import test_internet as fixtures
from customize import package_records, removed_packages, patch_interface_names

ROOT=Path(__file__).resolve().parents[1]


class NetworkLayoutTests(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.InternetTests();self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root=self.case.path
        self.env=dict(self.case.env)
        board=self.root/'board';board.write_text('arcadyan,aw1000\n')
        self.state=self.root/'layout-state'
        self.env.update(AW1000_BOARD_FILE=str(board),AW1000_LAYOUT_STATE=str(self.state))
        self.script=ROOT/'files/usr/sbin/aw1000-network-layout'
        n=self.case.read_config('network')
        n['wan6']=n.pop('wan_v6')
        n['wan'].update(proto='pppoe',username='test-user',password="literal'$(false)",device='eth4.85')
        n['lan']={'.type':'interface','device':'br-lan','ipaddr':'192.168.9.1','netmask':'255.255.255.0'}
        n['cellular']['modem_config']='4_1'
        n['cellularv6']={'.type':'interface','proto':'dhcpv6','device':'@cellular','metric':'20'}
        self.case.write_config('network',n)
        a=self.case.read_config('aw1000');a['internet']['vlan']='85';self.case.write_config('aw1000',a)
        f=self.case.read_config('firewall');f['wan']['network']=['wan','wan6','cellular','cellularv6','custom_vpn']
        self.case.write_config('firewall',f)

    def run_layout(self,success=True):
        p=subprocess.run(['sh',str(self.script)],env=self.env,text=True,capture_output=True,timeout=60)
        self.assertEqual(p.returncode==0,success,p.stderr)
        return p

    def test_rename_preserves_lan_pppoe_modem_and_custom_zone_members(self):
        self.run_layout()
        n=self.case.read_config('network');q=self.case.read_config('qmodem')
        self.assertEqual(n['lan']['ipaddr'],'192.168.9.1')
        self.assertEqual(n['wan']['password'],"literal'$(false)")
        self.assertEqual(n['wan']['device'],'eth4.85')
        self.assertEqual(n['br_lan']['ports'],['eth0','eth1','eth2','eth3'])
        self.assertEqual(n['wan_v6']['device'],'@wan')
        self.assertEqual(n['wwan_v6']['device'],'@wwan')
        self.assertEqual(n['wwan']['modem_config'],'4_1')
        self.assertEqual(q['4_1']['alias'],'wwan')
        self.assertEqual(q['usb0']['alias'],'wwan')
        self.assertTrue({'wan6','cellular','cellularv6'}.isdisjoint(n))
        self.assertEqual(self.case.read_config('mwan3')['globals']['aw1000_cellular'],'wwan')
        self.assertEqual(self.case.read_config('firewall')['wan']['network'],
                         ['custom_vpn','wan','wan_v6','wwan','wwan_v6'])
        before={p.name:p.read_bytes() for p in self.case.config.iterdir()}
        self.run_layout()
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.case.config.iterdir()})
        self.assertTrue((self.state/'network-layout-1.0.14-backup/network').exists())

    def test_conflicting_interfaces_roll_back_and_pending_user_edits_are_preserved(self):
        n=self.case.read_config('network');n['wwan']={'.type':'interface','proto':'static','ipaddr':'192.0.2.2'}
        self.case.write_config('network',n)
        before={c:(self.case.config/c).read_bytes() for c in ('network','qmodem','mwan3','dhcp','firewall')}
        self.run_layout(False)
        for c,data in before.items():self.assertEqual((self.case.config/c).read_bytes(),data)
        self.assertFalse((self.state/'network-layout-1.0.14.done').exists())
        (self.case.config/'staged.json').write_text(json.dumps({'network':n}))
        self.run_layout(False)
        self.assertEqual(json.loads((self.case.config/'staged.json').read_text()),{'network':n})

    def test_delayed_provisioning_keeps_already_renamed_ports(self):
        run=self.root/'ports-run';run.mkdir();(run/'status').write_text('mode=named\n')
        self.env['AW1000_PORTS_RUN']=str(run)
        self.run_layout()
        n=self.case.read_config('network')
        self.assertEqual(n['br_lan']['ports'],['lan1','lan2','lan3','lan4'])
        self.assertEqual(n['wan']['device'],'wan.85')

    def test_real_qwrt_uci_migration_and_modem_naming(self):
        target=os.environ.get('AW1000_TEST_TARGET_ROOT')
        if not target or not shutil.which('qemu-aarch64-static'):self.skipTest('original QWRT root and QEMU required')
        if Path('/tmp/.uci').exists():self.skipTest('host UCI delta directory must be absent for isolated commit test')
        target=Path(target)
        for file in self.case.config.iterdir():
            data=json.loads(file.read_text());text=''
            for name,section in data.items():
                # The fixture's unnamed defaults are irrelevant to the five migrated configs.
                if name.startswith('@'):continue
                text+='config '+section['.type']+' '+shlex.quote(name)+'\n'
                for key,value in section.items():
                    if key=='.type':continue
                    for item in (value if isinstance(value,list) else [value]):
                        text+=(' list ' if isinstance(value,list) else ' option ')+key+' '+shlex.quote(str(item))+'\n'
            file.write_text(text)
        delta=self.root/'uci-delta';delta.mkdir()
        qemu=shutil.which('qemu-aarch64-static')
        base=[qemu,'-L',str(target),str(target/'sbin/uci'),'-c',str(self.case.config)]
        # This old CLI's -P deliberately disables commit. Load the isolated
        # delta with -p for commit, then clear that test-only delta on success.
        self.case.executable('uci','#!/bin/sh\nset -e\nif [ "$1" = commit ]; then\n'
            ' [ ! -e /tmp/.uci ] || exit 99\n '+shlex.join(base+['-p',str(delta)])+' "$@"\n'
            ' rm -f -- '+shlex.quote(str(delta))+'/"$2"\nelse\n exec '+
            shlex.join(base+['-P',str(delta)])+' "$@"\nfi\n')
        self.run_layout()
        # Read with a fresh empty delta, so staged-only changes cannot pass.
        for config in ('network','qmodem','mwan3','dhcp','firewall'):
            self.assertEqual(subprocess.check_output(['uci','changes',config],env=self.env,text=True),'')
        self.assertFalse(Path('/tmp/.uci').exists())
        def get(key):return subprocess.check_output(['uci','get',key],env=self.env,text=True).strip()
        self.assertEqual(get('network.wan.password'),"literal'$(false)")
        self.assertEqual(get('network.wwan_v6.device'),'@wwan')
        self.assertEqual(get('mwan3.aw_cell.interface'),'wwan')
        dial=self.root/'usr/share/qmodem';dial.mkdir(parents=True)
        shutil.copyfile(target/'usr/share/qmodem/modem_dial.sh',dial/'modem_dial.sh')
        if 'wwan) interface6_name=wwan_v6;;' not in (dial/'modem_dial.sh').read_text():
            patch_interface_names(self.root)
        text=(dial/'modem_dial.sh').read_text()
        subprocess.run(['sh','-n',str(dial/'modem_dial.sh')],check=True)
        block=text[text.index('    interface_name=$modem_config'):]
        block=block[:block.index('\n}')]
        for model,alias,names in [('rg500q-ea','cellular','wwan|wwan_v6'),('other','usb_other','usb_other|usb_otherv6')]:
            command='uci() { echo '+shlex.quote(model)+'; }; modem_config=4_1; alias='+shlex.quote(alias)+'\n'+block+'\nprintf "%s|%s" "$interface_name" "$interface6_name"'
            self.assertEqual(subprocess.check_output(['sh','-c',command],text=True),names)

    def test_requested_packages_and_translations_are_removed_with_shared_libraries_retained(self):
        names=('luci-theme-design','coremark','mcproxy','xupnpd','luci-app-xupnpd','luci-i18n-xupnpd-zh-cn',
               'msd_lite','rtp2httpd','luci-app-msd_lite','luci-i18n-msd_lite-zh-cn',
               'ddns-scripts','ddns-scripts-services','luci-app-ddns','luci-i18n-ddns-zh-cn',
               'vsftpd-alt','luci-app-vsftpd','luci-i18n-vsftpd-zh-cn','zerotier','luci-app-zerotier','luci-i18n-zerotier-zh-cn')
        records=package_records('\n\n'.join('Package: '+name for name in names)+
            '\n\nPackage: libminiupnpc\n\nPackage: libnatpmp\n\nPackage: libatomic1\n\n'
            'Package: other-vpn\nDepends: libatomic1, kmod-tun\n\nPackage: kmod-tun\n\n'
            'Package: miniupnpd\n\nPackage: luci-app-upnp\nDepends: miniupnpd\n\n')
        removed=removed_packages(records)
        self.assertTrue(set(names)<=removed)
        self.assertTrue({'libminiupnpc','libnatpmp'}<=removed)
        self.assertTrue({'miniupnpd','luci-app-upnp','kmod-tun','libatomic1','other-vpn'}.isdisjoint(removed))


if __name__=='__main__':unittest.main()
