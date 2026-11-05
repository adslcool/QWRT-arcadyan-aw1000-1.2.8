"""Execute the shipped shell backend against isolated UCI/ubus fixtures."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from customize import removed_packages, package_records, remove_apps

UCI = r'''#!/usr/bin/env python3
import json,os,sys,pathlib
base=pathlib.Path(os.environ['TEST_CONFIG']);private_delta=None
args=[a for a in sys.argv[1:] if a!='-q']
if '-c' in args:
 i=args.index('-c');base=pathlib.Path(args[i+1]);del args[i:i+2]
if '-t' in args:
 i=args.index('-t');del args[i:i+2]
if '-P' in args:
 i=args.index('-P');private_delta=pathlib.Path(args[i+1]);del args[i:i+2]
cmd=args[0];key=args[1] if len(args)>1 else ''
stage=private_delta/key.split('.')[0] if private_delta else base/'staged.json'
staged=json.loads(stage.read_text()) if stage.exists() else {}
def original(c):
 p=base/c
 return json.loads(p.read_text()) if p.exists() else {}
def data(c):return staged.get(c,original(c))
def text(v):return ' '.join(v) if isinstance(v,list) else str(v)
if cmd in ('get','set','delete','add_list','del_list'):
 value=''
 if cmd in ('set','add_list','del_list'):key,value=key.split('=',1)
 parts=key.split('.');c,s=parts[:2];option=parts[2] if len(parts)>2 else '.type';d=data(c)
 if cmd=='get':
  if s not in d or option not in d[s]:sys.exit(1)
  print(text(d[s][option]));sys.exit(0)
 if cmd=='set':d.setdefault(s,{})[option]=value
 elif cmd=='delete':
  if len(parts)==2:d.pop(s,None)
  else:d.setdefault(s,{}).pop(option,None)
 else:
  values=d.setdefault(s,{}).setdefault(option,[])
  if cmd=='add_list':values.append(value)
  else:d[s][option]=[v for v in values if v!=value]
 staged[c]=d
elif cmd=='rename':
 key,new=key.split('=',1);c,old=key.split('.',1);d=data(c)
 if old not in d or new in d:sys.exit(1)
 d[new]=d.pop(old);staged[c]=d
elif cmd=='show':
 for s,values in data(key).items():
  print(key+'.'+s+'='+values.get('.type',''))
  for k,v in values.items():
   if k!='.type':print(key+'.'+s+'.'+k+'='+repr(text(v)))
elif cmd=='changes':
 if key in staged:print(key+' changed')
elif cmd=='export':print(json.dumps(data(key)))
elif cmd=='commit':
 if key in staged:(base/key).write_text(json.dumps(staged.pop(key)))
elif cmd=='revert':staged.pop(key,None)
else:raise SystemExit('Unsupported UCI '+cmd)
stage.write_text(json.dumps(staged))
'''


class InternetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.config = self.path/'config'; self.config.mkdir()
        self.bin = self.path/'bin'; self.bin.mkdir()
        self.var = self.path/'var/run'; self.var.mkdir(parents=True)
        self.now = self.path/'uptime'; self.now.write_text('1000.00 0.00\n')
        self.boot = self.path/'boot'; self.boot.write_text('boot-1\n')
        self.env = {**os.environ, 'PATH':str(self.bin)+os.pathsep+os.environ['PATH'],
                    'TEST_CONFIG':str(self.config), 'TEST_ROOT':str(self.path), 'AW1000_IPV6_INLINE':'1',
                    'AW1000_CONFIG_ROOT':str(self.config),
                    'AW1000_INTERNET_RUN':str(self.var/'aw1000-internet'),
                    'AW1000_PENDING':str(self.path/'pending'),
                    'AW1000_SYS_ROOT':str(self.path/'sys')}
        fixtures = {
            'network': {'wan':{'.type':'interface','device':'eth4','proto':'dhcp','metric':'10','defaultroute':'1'},
                        'wan_v6':{'.type':'interface','device':'@wan','proto':'dhcpv6'},
                        'cellular':{'.type':'interface','device':'wwan0','proto':'dhcp','metric':'20','defaultroute':'1'}},
            'mwan3': {'globals':{'.type':'globals','aw1000_enabled':'1','mmx_mask':'0x00ff0000',
                                'aw1000_cellular':'cellular','aw1000_modem':'4_1'},
                      'wan':{'.type':'interface'}, 'cellular':{'.type':'interface'},
                      'aw_wired':{'.type':'member','interface':'wan','metric':'1'},
                      'aw_cell':{'.type':'member','interface':'cellular','metric':'2'},
                      'aw_failover':{'.type':'policy','use_member':['aw_wired','aw_cell'],'last_resort':'unreachable'},
                      'aw_default':{'.type':'rule','family':'ipv4','dest_ip':'0.0.0.0/0','use_policy':'aw_failover'}},
            'aw1000': {'internet':{'.type':'policy','priority':'wired','base_device':'eth4'},'ipv6':{'.type':'policy'}},
            'qmodem': {'4_1':{'.type':'modem-device','at_port':'/dev/ttyUSB3','name':'rg500q-ea','alias':'cellular'},
                       'usb0':{'.type':'modem-slot'}},
            'dhcp': {'main':{'.type':'dnsmasq'},'lan':{'.type':'dhcp'}},
            'firewall': {'@defaults[0]':{'.type':'defaults'},'wan':{'.type':'zone','name':'wan','network':['wan','wan6']}},
            'mosdns': {'config':{'.type':'mosdns','enabled':'0','redirect':'1'}}}
        for name,value in fixtures.items(): self.write_config(name,value)
        self.executable('uci', UCI)
        self.executable('logger', '#!/bin/sh\nexit 0\n')
        self.executable('network-layout', '#!/bin/sh\nexit 0\n')
        self.executable('hotplug-call', '#!/bin/sh\nprintf "%s:%s\\n" "$ACTION" "$INTERFACE" >> "$TEST_ROOT/events"\n')
        self.executable('mwan3', '#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_ROOT/events"\n')
        self.executable('ubus', '''#!/usr/bin/env python3
import json,os,sys,pathlib
p=pathlib.Path(os.environ['TEST_ROOT'])/'ubus.json'
responses=json.loads(p.read_text()) if p.exists() else {}
print(json.dumps(responses.get(sys.argv[sys.argv.index('call')+1],{})))
''')
        self.script = self.rewrite('usr/sbin/aw1000-internet')

    def executable(self, name, text):
        p=self.bin/name;p.write_text(text);p.chmod(0o755);return p

    def rewrite(self, name):
        text=(ROOT/'files'/name).read_text()
        for src,dst in [('/var/run/',str(self.var)+'/'),('/tmp/dnsmasq',str(self.path)+'/tmp/dnsmasq'),
                        ('/etc/aw1000-internet-pending',str(self.path/'pending')),
                        ('/proc/uptime',str(self.now)),('/proc/sys/kernel/random/boot_id',str(self.boot)),
                        ('/sys/kernel/debug/ecm',str(self.path/'ecm')),
                        ('/usr/sbin/mwan3',str(self.bin/'mwan3')),('/sbin/hotplug-call',str(self.bin/'hotplug-call')),
                        ('/usr/sbin/aw1000-flow-flush',str(self.bin/'flow-flush')),
                        ('/usr/sbin/aw1000-ipv6-follow',str(self.bin/'ipv6-follow')),
                        ('/usr/sbin/aw1000-network-layout',str(self.bin/'network-layout')),
                        ('/usr/sbin/ftpd',str(self.path/'usr/sbin/ftpd')),
                        ('/etc/init.d/dnsmasq',str(self.bin/'dnsmasq'))]:
            text=text.replace(src,dst)
        return self.executable(Path(name).name,text)

    def write_config(self, name, value): (self.config/name).write_text(json.dumps(value))
    def read_config(self, name): return json.loads((self.config/name).read_text())
    def read_runtime(self, name):
        return json.loads((self.var/'aw1000-ipv6-follow/config'/name).read_text())
    def invoke(self, *args, data=None):
        result=subprocess.run(['sh',str(self.script),*args],input=json.dumps(data) if data is not None else '',
                              env=self.env,text=True,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else None
    def apply(self, **kw):
        data={'priority':'wired','mode':'dhcp','username':'','password':'','vlan':'','mtu':''};data.update(kw)
        return self.invoke('--rpc','call','apply',data=data)

    def test_four_modes_keep_standby_routes_and_limit_policy_members(self):
        for mode,members,wm,cm in [('wired',['aw_wired','aw_cell'],'1','2'),('cellular',['aw_wired','aw_cell'],'2','1'),
                                   ('wired_only',['aw_wired'],'1','2'),('cellular_only',['aw_cell'],'2','1')]:
            self.assertTrue(self.apply(priority=mode)['ok'])
            m=self.read_config('mwan3');n=self.read_config('network')
            self.assertEqual(m['aw_failover']['use_member'],members)
            self.assertEqual((m['aw_wired']['metric'],m['aw_cell']['metric']),(wm,cm))
            self.assertEqual(n['cellular']['defaultroute'],'1')
            self.assertEqual(n['wan_v6']['device'],'@wan')
            self.assertTrue(self.invoke('--rpc','call','confirm')['ok'])

    def test_wan_route_guard_repairs_main_then_rebuilds_mwan_table_once(self):
        (self.path/'ubus.json').write_text(json.dumps({'network.interface.wan':{
            'up':True,'l3_device':'eth4','ipv4-address':[{'address':'192.0.2.2','mask':24}],
            'route':[{'target':'0.0.0.0','mask':0,'nexthop':'192.0.2.1'}]}}))
        self.executable('ip',r'''#!/bin/sh
printf 'ip %s\n' "$*" >> "$TEST_ROOT/events"
case "$*" in
  '-4 rule show') echo '1001: from all iif eth4 lookup 1';;
  '-4 route show table main default')
      [ ! -f "$TEST_ROOT/main-route" ] || cat "$TEST_ROOT/main-route";;
  '-4 route show table 1 default')
      [ ! -f "$TEST_ROOT/table-route" ] || cat "$TEST_ROOT/table-route";;
  '-4 route replace default '*)
      printf '%s\n' "${*#-4 route replace }" > "$TEST_ROOT/main-route";;
esac
''')
        self.invoke('--route-guard')
        events=(self.path/'events').read_text()
        self.assertIn('ip -4 route replace default via 192.0.2.1 dev eth4 src 192.0.2.2 metric 10',events)
        self.assertEqual(events.count('ifup wan'),1)
        repair=json.loads((self.var/'aw1000-internet/wan-route-guard.json').read_text())
        self.assertEqual(repair['device'],'eth4')
        self.assertTrue(repair['repaired']['main']);self.assertTrue(repair['repaired']['mwan'])
        # A missing policy table cannot cause a two-second restart loop; mwan3
        # gets a full recovery window before another repair is allowed.
        self.invoke('--route-guard')
        self.assertEqual((self.path/'events').read_text().count('ifup wan'),1)
        self.now.write_text('1021.00 0.00\n')
        self.invoke('--route-guard')
        self.assertEqual((self.path/'events').read_text().count('ifup wan'),2)
        repair=json.loads((self.var/'aw1000-internet/wan-route-guard.json').read_text())
        self.assertFalse(repair['repaired']['main']);self.assertTrue(repair['repaired']['mwan'])
        (self.path/'table-route').write_text('default via 192.0.2.1 dev eth4 src 192.0.2.2 metric 10\n')
        self.invoke('--route-guard')
        self.assertEqual((self.path/'events').read_text().count('ifup wan'),2)

    def test_pppoe_password_is_literal_and_never_returned(self):
        password="pa'$(touch /tmp/AW1000_UNWANTED);word"
        self.assertTrue(self.apply(mode='pppoe',username="user'$(false)",password=password,vlan='0085',mtu='1492')['ok'])
        n=self.read_config('network')
        self.assertEqual(n['wan']['password'],password)
        self.assertEqual(n['wan']['device'],'eth4.85')
        self.assertFalse(Path('/tmp/AW1000_UNWANTED').exists())
        status=self.invoke('--status')
        self.assertNotIn(password,json.dumps(status));self.assertEqual(status['remaining'],120)
        self.assertEqual(status['wan_turn'],0)
        self.invoke('--rpc','call','confirm')
        self.assertTrue(self.apply(mode='pppoe',username='user',password='')['ok'])
        self.assertEqual(self.read_config('network')['wan']['password'],password)

    def test_timeout_and_reboot_restore_all_transaction_configs(self):
        for reboot in (False,True):
            before={n:(self.config/n).read_bytes() for n in ('network','mwan3','aw1000','qmodem','dhcp','firewall')}
            self.assertTrue(self.apply(priority='cellular_only')['ok'])
            if reboot:self.boot.write_text('new-boot\n')
            else:self.now.write_text('1121.00 0.00\n')
            self.invoke('--tick')
            self.assertFalse((self.path/'pending').exists())
            for name,b in before.items():self.assertEqual((self.config/name).read_bytes(),b,name)

    def test_late_confirmation_rolls_back_and_second_apply_is_rejected(self):
        self.assertTrue(self.apply()['ok'])
        self.assertIn('error',self.apply())
        self.now.write_text('1120.00 0.00\n')
        self.assertIn('error',self.invoke('--rpc','call','confirm'))
        self.assertFalse((self.path/'pending').exists())

    def test_invalid_input_and_foreign_policy_do_not_mutate_configs(self):
        before=(self.config/'network').read_bytes()
        for kw in ({'vlan':'4095'},{'vlan':'-1'},{'mode':'pppoe','username':'u','password':'p','mtu':'1500'},
                   {'username':'u\nx'},{'priority':'loadbalance'},{'mode':'static'}):
            self.assertIn('error',self.apply(**kw))
            self.assertEqual((self.config/'network').read_bytes(),before)
        m=self.read_config('mwan3');m['vpn']={'.type':'rule'};self.write_config('mwan3',m)
        self.assertIn('error',self.apply())

    def test_radio_sampling_distinguishes_real_loss_from_missing_at_response(self):
        usb=self.path/'sys/devices/usb4/4-1';usb.mkdir(parents=True)
        (usb/'devnum').write_text('2\n');(usb/'busnum').write_text('4\n')
        tty=self.path/'sys/class/tty/ttyUSB3';tty.mkdir(parents=True);(tty/'device').symlink_to(usb)
        cases=[
            ('+QENG: "servingcell","NOCONN","NR5G-SA","TDD",460,00,2312BF001,83,701113,504990,41,12,-87,-12,4,1,-\r\n','sa',True),
            ('+QENG: "servingcell","SEARCH"\r\n','unknown',False),
            ('+QENG: "servingcell","LIMSRV"\r\n','unknown',False),
            ('ERROR\r\n','unknown',None),('', 'unknown',None)]
        for reply,rat,registered in cases:
            p=subprocess.run(['sh',str(self.script),'--radio-result','4_1'],input=reply,env=self.env,text=True,capture_output=True)
            self.assertEqual(p.returncode,0,p.stderr)
            state=self.invoke('--radio-state','4_1')
            self.assertEqual((state['rat'],state['registered']),(rat,registered))
        (usb/'devnum').write_text('3\n')
        self.assertEqual(self.invoke('--radio-state','4_1'),{})

    def test_sim_generation_staleness_and_override_port(self):
        usb=self.path/'sys/devices/usb4/4-1';usb.mkdir(parents=True)
        (usb/'devnum').write_text('2\n');(usb/'busnum').write_text('4\n')
        tty=self.path/'sys/class/tty/ttyUSB2';tty.mkdir(parents=True);(tty/'device').symlink_to(usb)
        q=self.read_config('qmodem');q['4_1']['override_at_port']='/dev/ttyUSB2';self.write_config('qmodem',q)
        result=subprocess.run(['sh',str(self.script),'--sim-result','4_1'],input='+CPIN: READY\r\n',env=self.env,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        r=subprocess.check_output(['sh',str(self.script),'--sim-state'],env=self.env,text=True).strip();self.assertEqual(r,'ready')
        (usb/'devnum').write_text('3\n')
        r=subprocess.check_output(['sh',str(self.script),'--sim-state'],env=self.env,text=True).strip();self.assertEqual(r,'unknown')

    def test_published_policy_drives_selection_dns_and_old_flow_cleanup(self):
        self.script=self.rewrite('usr/sbin/aw1000-mwan-follow')
        for name in ('ipv6-follow','dnsmasq','flow-flush'):
            self.executable(name,'#!/bin/sh\necho "'+name+' $*" >> "$TEST_ROOT/events"\n')
        self.executable('iptables','#!/bin/sh\ncat "$TEST_ROOT/policy"\n')
        (self.path/'ubus.json').write_text(json.dumps({
            'network.interface.wan':{'up':True,'l3_device':'eth4','ipv4-address':[{'address':'192.0.2.2','mask':24}],
                                     'dns-server':['192.0.2.53','2400:3200::1'],'route':[{'target':'0.0.0.0','mask':0,'nexthop':'192.0.2.1'}]},
            'network.interface.cellular':{'up':True,'l3_device':'wwan0','ipv4-address':[{'address':'198.51.100.2','mask':24}],
                                         'dns-server':['198.51.100.53','2402:4e00::']}}))
        (self.path/'policy').write_text('-A mwan3_policy_aw_failover -j MARK --set-xmark 0x10000/0xff0000\n')
        self.invoke('--once')
        run=self.var/'aw1000-mwan-follow'
        self.assertEqual((run/'selected').read_text().strip(),'wan')
        dns=self.path/'tmp/dnsmasq.d/aw1000-mwan.conf'
        self.assertIn('server=192.0.2.53',dns.read_text())
        self.assertIn('server=223.5.5.5',dns.read_text())
        self.assertIn('server=119.29.29.29',dns.read_text())
        self.assertNotIn('2400:3200::1',dns.read_text())
        # Main metrics remain wired-first while the installed policy selects cellular.
        (self.path/'policy').write_text('-A mwan3_policy_aw_failover -j MARK --set-xmark 0x20000/0xff0000\n')
        self.invoke('--once')
        self.assertEqual((run/'selected').read_text().strip(),'cellular')
        self.assertIn('server=198.51.100.53',dns.read_text())
        self.assertNotIn('2402:4e00::',dns.read_text())
        self.assertIn('cellular|auto|',(run/'dns-signature').read_text())
        self.assertIn('flow-flush 0x10000',(self.path/'events').read_text())
        # MosDNS takes DNS ownership without leaving our ISP resolver injection behind.
        m=self.read_config('mosdns');m['config']['enabled']='1';self.write_config('mosdns',m)
        self.invoke('--once');self.assertFalse(dns.exists())
        (self.path/'policy').write_text('-A mwan3_policy_aw_failover -j MARK --set-xmark 0xfe0000/0xff0000\n')
        self.invoke('--once');self.assertEqual((run/'selected').read_text().strip(),'')

    def test_ipv6_failure_does_not_block_initial_dns_or_failover_update(self):
        self.script=self.rewrite('usr/sbin/aw1000-mwan-follow')
        self.executable('ipv6-follow','#!/bin/sh\necho simulated-v6-error >&2\nexit 77\n')
        for name in ('dnsmasq','flow-flush','logger'):
            self.executable(name,'#!/bin/sh\necho "'+name+' $*" >> "$TEST_ROOT/events"\n')
        self.executable('iptables','#!/bin/sh\ncat "$TEST_ROOT/policy"\n')
        (self.path/'ubus.json').write_text(json.dumps({
            'network.interface.wan':{'up':True,'l3_device':'eth4',
                'ipv4-address':[{'address':'192.0.2.2','mask':24}],'dns-server':['192.0.2.53']},
            'network.interface.cellular':{'up':True,'l3_device':'wwan0_1',
                'ipv4-address':[{'address':'198.51.100.2','mask':24}],'dns-server':['198.51.100.53']}}))
        (self.path/'policy').write_text('-A mwan3_policy_aw_failover -j MARK --set-xmark 0x10000/0xff0000\n')
        self.invoke('--once')
        run=self.var/'aw1000-mwan-follow'
        dns=self.path/'tmp/dnsmasq.d/aw1000-mwan.conf'
        self.assertIn('server=192.0.2.53',dns.read_text())
        self.assertIn('wan|auto|',(run/'dns-signature').read_text())
        # A continuing IPv6 error must not pin DNS to the previous wired exit.
        (self.path/'policy').write_text('-A mwan3_policy_aw_failover -j MARK --set-xmark 0x20000/0xff0000\n')
        self.invoke('--once')
        self.assertNotIn('192.0.2.53',dns.read_text())
        self.assertIn('server=198.51.100.53',dns.read_text())
        self.assertIn('server=223.5.5.5',dns.read_text())
        self.assertIn('cellular|auto|',(run/'dns-signature').read_text())
        events=(self.path/'events').read_text()
        self.assertEqual(events.count('dnsmasq restart'),2)
        self.assertEqual(events.count('IPv6 同步失败（状态 77）'),1)
        self.assertIn('simulated-v6-error',(run/'ipv6-last.log').read_text())
        self.assertEqual((run/'ipv6-error').read_text().strip(),'77')
        self.assertEqual((run/'ipv6-status').read_text().strip(),'77')
        self.assertIn('flow-flush 0x10000',events)
        self.executable('ipv6-follow','#!/bin/sh\nexit 0\n')
        self.invoke('--once')
        self.assertEqual((self.path/'events').read_text().count('dnsmasq restart'),2)
        self.assertFalse((run/'ipv6-error').exists())
        self.assertEqual((run/'ipv6-status').read_text().strip(),'0')

        # Retained runtime signatures must not suppress repair of lost temp DNS.
        dns.unlink()
        self.invoke('--once')
        self.assertIn('server=198.51.100.53',dns.read_text())
        self.assertEqual((self.path/'events').read_text().count('dnsmasq restart'),3)
        dns.write_text('no-resolv\nserver=192.0.2.53\n')
        self.invoke('--once')
        self.assertNotIn('192.0.2.53',dns.read_text())
        self.assertEqual((self.path/'events').read_text().count('dnsmasq restart'),4)
        (run/'dns-files').unlink()
        self.invoke('--once')
        self.assertEqual((self.path/'events').read_text().count('dnsmasq restart'),5)
        d=self.read_config('dhcp');d['main']['disabled']='1';self.write_config('dhcp',d)
        self.invoke('--once')
        self.assertEqual((run/'dns-files').read_text(),'')
        self.assertEqual((self.path/'events').read_text().count('dnsmasq restart'),6)
        self.invoke('--once')
        self.assertEqual((self.path/'events').read_text().count('dnsmasq restart'),6)
        d=self.read_config('dhcp');d['main']['disabled']='0';self.write_config('dhcp',d)
        self.invoke('--once')
        self.assertEqual((self.path/'events').read_text().count('dnsmasq restart'),7)
        dns.unlink()
        self.executable('dnsmasq','#!/bin/sh\nexit 44\n')
        failed=subprocess.run(['sh',str(self.script),'--once'],env=self.env,text=True,capture_output=True,timeout=30)
        self.assertEqual(failed.returncode,1)
        self.assertFalse((run/'dns-signature').exists())
        self.executable('dnsmasq','#!/bin/sh\necho "dnsmasq $*" >> "$TEST_ROOT/events"\n')
        self.now.write_text('1006.00 0.00\n')
        self.invoke('--once')
        self.assertIn('cellular|auto|',(run/'dns-signature').read_text())
        self.assertEqual((self.path/'events').read_text().count('dnsmasq restart'),8)

    def test_dns_confdir_with_spaces_is_repaired_and_no_exit_adds_no_servers(self):
        self.script=self.rewrite('usr/sbin/aw1000-mwan-follow')
        for name in ('ipv6-follow','dnsmasq','flow-flush'):
            self.executable(name,'#!/bin/sh\necho "'+name+' $*" >> "$TEST_ROOT/events"\n')
        self.executable('iptables','#!/bin/sh\ncat "$TEST_ROOT/policy"\n')
        dnsdir=self.path/'tmp/dnsmasq with spaces.d'
        d=self.read_config('dhcp');d['main']['confdir']=str(dnsdir);self.write_config('dhcp',d)
        (self.path/'ubus.json').write_text(json.dumps({'network.interface.cellular':{
            'up':True,'l3_device':'wwan0_1','ipv4-address':[{'address':'198.51.100.2','mask':24}],
            'dns-server':['198.51.100.53']}}))
        policy=self.path/'policy'
        policy.write_text('-A mwan3_policy_aw_failover -j MARK --set-xmark 0x20000/0xff0000\n')
        self.invoke('--once')
        dns=dnsdir/'aw1000-mwan.conf';run=self.var/'aw1000-mwan-follow'
        self.assertIn('server=198.51.100.53',dns.read_text())
        self.assertEqual((run/'dns-files').read_text(),str(dns)+'\n')
        self.assertFalse((self.path/'tmp/aw1000-mwan.conf').exists())
        self.invoke('--once')
        self.assertEqual((self.path/'events').read_text().count('dnsmasq restart'),1)
        dns.unlink();self.invoke('--once')
        self.assertIn('server=198.51.100.53',dns.read_text())
        self.assertEqual((self.path/'events').read_text().count('dnsmasq restart'),2)
        # An unreachable policy must not fall back to the standby/default route.
        policy.write_text('-A mwan3_policy_aw_failover -j MARK --set-xmark 0xfe0000/0xff0000\n')
        self.invoke('--once')
        self.assertEqual(dns.read_text(),'no-resolv\n')
        self.assertEqual((run/'selected').read_text().strip(),'')
        self.assertEqual((run/'dns-signature').read_text(),'|auto|\n')

    def test_real_ipv6_nft_failure_with_unreachable_table_still_updates_dns(self):
        self.ipv6_fixture(False)
        # Reproduce the previously installed unavailable policy. The new
        # fail-closed guard must not flush this table if nft cannot apply.
        before={'rules':['8893: to ff00::/8 lookup main',
                         '8895: oif wwan0_1 lookup main',
                         '8900: lookup main suppress_prefixlength 0',
                         '8901: lookup 61000','8902: unreachable'],
                'routes':['unreachable default metric 32767']}
        (self.path/'ip6-state.json').write_text(json.dumps(before))
        v6run=self.var/'aw1000-ipv6-follow';v6run.mkdir()
        (v6run/'rules-owned').touch()
        self.executable('ipv6-follow',self.script.read_text())
        self.script=self.rewrite('usr/sbin/aw1000-mwan-follow')
        for name in ('dnsmasq','logger'):
            self.executable(name,'#!/bin/sh\necho "'+name+' $*" >> "$TEST_ROOT/events"\n')
        self.executable('iptables','#!/bin/sh\ncat "$TEST_ROOT/policy"\n')
        self.executable('nft','''#!/bin/sh
case "$1" in
 list) exit 1;;
 -f) echo 'simulated nft apply failure' >&2; exit 2;;
esac
''')
        (self.path/'policy').write_text('-A mwan3_policy_aw_failover -j MARK --set-xmark 0x20000/0xff0000\n')
        (self.path/'ubus.json').write_text(json.dumps({
            'network.interface.cellular':{'up':True,'l3_device':'wwan0_1',
                'ipv4-address':[{'address':'198.51.100.2','mask':24}],'dns-server':[]},
            'network.interface.cellularv6':{'proto':'dhcpv6','up':True,'autostart':True,
                'l3_device':'wwan0_1','ipv6-address':[],'route':[]},
            'network.interface.lan':{'l3_device':'br-lan'}}))
        self.invoke('--once')
        run=self.var/'aw1000-mwan-follow'
        v6run=self.var/'aw1000-ipv6-follow'
        self.assertEqual((run/'selected').read_text().strip(),'cellular')
        self.assertTrue((v6run/'policy.nft').exists())
        self.assertTrue((v6run/'config-owned').exists())
        failed=json.loads((v6run/'status.json').read_text())
        self.assertEqual(failed['mode'],'unavailable')
        self.assertEqual(failed['nat_backend'],'none')
        self.assertEqual(self.read_runtime('dhcp')['lan']['ra_lifetime'],'0')
        self.assertEqual(json.loads((self.path/'ip6-state.json').read_text()),before)
        events=(self.path/'events').read_text()
        self.assertNotIn('route flush table 61000',events)
        self.assertNotIn('rule add pref 8894',events)
        dns=(self.path/'tmp/dnsmasq.d/aw1000-mwan.conf').read_text()
        self.assertEqual(dns,'no-resolv\nserver=223.5.5.5\nserver=119.29.29.29\n')
        self.assertIn('cellular|auto|',(run/'dns-signature').read_text())
        failure_status=(run/'ipv6-status').read_text().strip()
        self.assertGreater(int(failure_status),0)
        self.assertIn('IPv6 同步失败（状态 '+failure_status+'）',events)
        self.assertIn('simulated nft apply failure',(run/'ipv6-last.log').read_text())

    def test_openclash_removal_keeps_shared_network_and_vpn_dependencies(self):
        records=package_records('Package: luci-app-openclash\nDepends: ruby, ruby-yaml, curl, dnsmasq-full, kmod-tun\n\n'
                                'Package: ruby\nDepends: libruby3.3\n\nPackage: ruby-yaml\nDepends: libruby3.3, libyaml\n\n'
                                'Package: libruby3.3\n\nPackage: libyaml\n\nPackage: mihomo\n\n'
                                'Package: luci-app-mosdns\nDepends: curl\n\nPackage: curl\n\n'
                                'Package: dnsmasq-full\n\nPackage: zerotier\nDepends: kmod-tun\n\nPackage: kmod-tun\n\n')
        removed=removed_packages(records)
        self.assertEqual(removed,{'luci-app-openclash','mihomo','ruby','ruby-yaml','libruby3.3','libyaml','zerotier'})
        with tempfile.TemporaryDirectory() as folder:
            r=Path(folder);info=r/'usr/lib/opkg/info';info.mkdir(parents=True)
            (r/'usr/lib/opkg/status').write_text(
                'Package: luci-app-openclash\n\nPackage: mihomo\n'
                'Alternatives: 300:/usr/bin/mihomo:/usr/libexec/mihomo 300:/usr/bin/clash:/usr/libexec/mihomo\n\n'
                'Package: other\nAlternatives: 100:/usr/bin/mihomo:/usr/libexec/other\n\n')
            (r/'usr/bin').mkdir();(r/'usr/libexec').mkdir()
            (r/'usr/libexec/mihomo').write_text('remove');(r/'usr/libexec/other').write_text('retain')
            for name in ('mihomo','clash'):(r/'usr/bin'/name).symlink_to('/usr/libexec/mihomo')
            (info/'mihomo.list').write_text('/usr/libexec/mihomo\n')
            empty=r/'www/luci-static/resources/openclash/addon';empty.mkdir(parents=True)
            remove_apps(r)
            self.assertEqual(str((r/'usr/bin/mihomo').readlink()),'/usr/libexec/other')
            self.assertFalse((r/'usr/bin/clash').is_symlink())
            self.assertFalse(empty.parent.exists())

    def test_enrolls_actual_qmodem_interface_and_correct_ipv6_suffix(self):
        self.script=self.rewrite('usr/sbin/aw1000-uplink')
        n=self.read_config('network')
        n['wwan']={'.type':'interface','proto':'dhcp','device':'wwan0'}
        n['wwan_v6']={'.type':'interface','proto':'dhcpv6','device':'@wwan','extendprefix':'1'}
        n['globals']={'.type':'globals','ula_prefix':'auto'}
        self.write_config('network',n)
        q=self.read_config('qmodem');q['4_1'].update(alias='wwan',pdp_type='IPV4V6',extend_prefix='1',ra_master='1',do_not_add_dns='0')
        self.write_config('qmodem',q)
        d=self.read_config('dhcp');d['wwan_v6']={'.type':'dhcp','interface':'wwan_v6','master':'1','ra':'relay','ndp':'relay'}
        self.write_config('dhcp',d)
        self.invoke()
        m=self.read_config('mwan3');self.assertEqual(m['aw_cell']['interface'],'wwan')
        self.assertNotIn('cellular',m);self.assertEqual(m['wwan']['initial_state'],'offline')
        self.assertEqual(m['wwan']['up'],'4')
        networks=self.read_config('firewall')['wan']['network']
        self.assertIn('wwan',networks);self.assertIn('wwan_v6',networks)
        self.assertEqual(self.read_config('network')['wwan']['metric'],'20')
        for name in ('wwan','wwan_v6'):
            self.assertEqual(self.read_config('network')[name]['modem_config'],'4_1')
        self.assertRegex(self.read_config('network')['globals']['ula_prefix'],r'^fd[0-9a-f]{2}:[0-9a-f]{4}:[0-9a-f]{4}::/48$')
        q=self.read_config('qmodem')['4_1']
        self.assertEqual(q['use_ubus'],'1')
        self.assertEqual(q['pdp_type'],'ipv4v6')
        self.assertEqual(q['extend_prefix'],'0')
        self.assertEqual(q['ra_master'],'0')
        self.assertEqual(q['do_not_add_dns'],'1')
        self.assertNotIn('extendprefix',self.read_config('network')['wwan_v6'])
        self.assertNotIn('wwan_v6',self.read_config('dhcp'))
        before={name:(self.config/name).stat().st_mtime_ns for name in ('network','qmodem','mwan3','aw1000','dhcp','firewall')}
        # On stable ticks the RAM fingerprint bypasses all UCI commands.
        (self.bin/'uci').rename(self.bin/'uci-real')
        self.executable('uci','#!/bin/sh\necho invoked >> "$TEST_ROOT/uci-invocations"\nexec "$(dirname "$0")/uci-real" "$@"\n')
        self.invoke();self.invoke()
        self.assertFalse((self.path/'uci-invocations').exists())
        for name,stamp in before.items():self.assertEqual((self.config/name).stat().st_mtime_ns,stamp)

    def test_upgrade_cleanup_preserves_mosdns_and_firewall_script_syntax(self):
        script=self.rewrite('usr/sbin/aw1000-network-cleanup')
        script.write_text(script.read_text().replace('/etc/',str(self.path/'etc')+'/'))
        etc=self.path/'etc';etc.mkdir();(etc/'crontabs').mkdir()
        (etc/'crontabs/root').write_text('0 * * * * /usr/share/openclash/a.sh\n1 * * * * /usr/bin/keep-job\n')
        (etc/'firewall.user').write_text('if [ -f /etc/openclash/firewall.include ]; then\n. /etc/openclash/firewall.include\nfi\necho keep\n')
        f=self.read_config('firewall');f['oc']={'.type':'include','path':str(etc/'openclash/firewall.include')}
        f['custom']={'.type':'include','path':'/etc/custom.include'};self.write_config('firewall',f)
        d=self.read_config('dhcp');d['main'].update(server=['127.0.0.1#7874','127.0.0.1#5335'],noresolv='1')
        self.write_config('dhcp',d)
        self.script=script;self.invoke()
        self.assertNotIn('oc',self.read_config('firewall'));self.assertIn('custom',self.read_config('firewall'))
        self.assertEqual(self.read_config('dhcp')['main']['server'],['127.0.0.1#5335'])
        self.assertEqual(self.read_config('dhcp')['main']['noresolv'],'1')
        self.assertIn('keep-job',(etc/'crontabs/root').read_text());self.assertNotIn('openclash',(etc/'crontabs/root').read_text())
        subprocess.run(['sh','-n',str(etc/'firewall.user')],check=True)
        self.invoke()  # Idempotent after the version marker is installed.

    def test_upgrade_cleanup_migrates_only_known_dns_and_qmodem_recipe(self):
        script=self.rewrite('usr/sbin/aw1000-network-cleanup')
        script.write_text(script.read_text().replace('/etc/',str(self.path/'etc')+'/'))
        n=self.read_config('network')
        n['lan']={'.type':'interface','device':'br-lan','dns':['223.5.5.5','119.29.29.29']}
        n['cellularv6']={'.type':'interface','proto':'dhcpv6','device':'@cellular','extendprefix':'1'}
        self.write_config('network',n)
        d=self.read_config('dhcp')
        d['main'].update(server=['223.5.5.5','119.29.29.29','2400:3200::1','2402:4e00::'],noresolv='1')
        d['lan'].update(dns=['2400:3200::1','2402:4e00::'],dhcp_option=['6,223.5.5.5,119.29.29.29','3,192.168.1.1'])
        d['cellularv6']={'.type':'dhcp','interface':'cellularv6','master':'1','ra':'relay','ndp':'relay'}
        self.write_config('dhcp',d)
        q=self.read_config('qmodem')
        q['4_1'].update(pdp_type='IPV4V6',extend_prefix='1',ra_master='1',do_not_add_dns='0')
        self.write_config('qmodem',q)
        self.script=script;self.invoke()
        n=self.read_config('network')
        self.assertNotIn('dns',n['lan']);self.assertNotIn('extendprefix',n['cellularv6'])
        d=self.read_config('dhcp')
        self.assertNotIn('server',d['main']);self.assertEqual(d['main']['noresolv'],'0')
        self.assertEqual(d['main']['resolvfile'],'/tmp/resolv.conf.d/resolv.conf.auto')
        self.assertNotIn('dns',d['lan']);self.assertEqual(d['lan']['dhcp_option'],['3,192.168.1.1'])
        self.assertNotIn('cellularv6',d)
        q=self.read_config('qmodem')['4_1']
        self.assertEqual({k:q[k] for k in ('pdp_type','extend_prefix','ra_master','do_not_add_dns')},
                         {'pdp_type':'ipv4v6','extend_prefix':'0','ra_master':'0','do_not_add_dns':'1'})
        self.invoke()  # The versioned migration remains idempotent.

    def test_upgrade_cleanup_shortens_only_owned_line_recovery_recipe(self):
        script=self.rewrite('usr/sbin/aw1000-network-cleanup')
        script.write_text(script.read_text().replace('/etc/',str(self.path/'etc')+'/'))
        m=self.read_config('mwan3')
        recipe={'.type':'interface','enabled':'1','initial_state':'offline','interval':'2',
                'failure_interval':'2','recovery_interval':'2','down':'2','up':'15'}
        m['wan'].update(recipe);m['cellular'].update(recipe)
        m['custom']={**recipe,'up':'15'}
        self.write_config('mwan3',m)
        self.script=script;self.invoke()
        m=self.read_config('mwan3')
        self.assertEqual(m['wan']['up'],'4');self.assertEqual(m['cellular']['up'],'4')
        self.assertEqual(m['custom']['up'],'15')
        before=(self.config/'mwan3').read_bytes();self.invoke()
        self.assertEqual((self.config/'mwan3').read_bytes(),before)

    def test_ipv6_switches_change_only_runtime_copies_and_restore_persistent_view(self):
        self.ipv6_fixture(True)
        before={n:(self.config/n).read_bytes() for n in ('network','dhcp','mwan3')}
        self.invoke('wan','[{"enabled":true,"target":"wan"}]')
        self.assertEqual(self.read_runtime('network')['lan']['ip6class'],['local','wan6'])
        self.invoke('','[]')
        self.assertEqual(self.read_runtime('dhcp')['lan']['ra_lifetime'],'0')
        for n,data in before.items():self.assertEqual((self.config/n).read_bytes(),data)
        self.invoke('--restore')
        self.assertFalse((self.var/'aw1000-ipv6-follow/config/network').exists())
        for n,data in before.items():self.assertEqual((self.config/n).read_bytes(),data)

    def ipv6_fixture(self, pd):
        self.script=self.rewrite('usr/sbin/aw1000-ipv6-follow')
        self.env['AW1000_IPV6_RUN']=str(self.var/'aw1000-ipv6-follow')
        board=self.path/'board';board.write_text('arcadyan,aw1000\n')
        self.env['AW1000_BOARD_FILE']=str(board)
        n=self.read_config('network');n['globals']={'.type':'globals','ula_prefix':'fd12:3456:789a::/48'}
        n['lan']={'.type':'interface','device':'br-lan'};self.write_config('network',n)
        v6={'proto':'dhcpv6','up':True,'l3_device':'eth4',
            'route':[{'target':'::','mask':0,'nexthop':'fe80::1'}]}
        if pd:v6['ipv6-prefix']=[{'address':'2001:db8:1234::','mask':56,'class':'wan6'}]
        else:v6['ipv6-address']=[{'address':'2001:db8::2','mask':64}]
        (self.path/'ubus.json').write_text(json.dumps({'network.interface.wan6':v6,'network.interface.lan':{'l3_device':'br-lan'}}))
        self.executable('ip','''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
root=Path(os.environ['TEST_ROOT']);path=root/'ip6-state.json'
args=sys.argv[1:]
with (root/'events').open('a') as out:out.write('ip '+' '.join(args)+'\\n')
if args and args[0]=='-6':args=args[1:]
state=json.loads(path.read_text()) if path.exists() else {'rules':[], 'routes':[]}
if args[:2]==['rule','show']:
 print('\\n'.join(state['rules']));sys.exit(0)
if args[:2] in (['rule','add'],['rule','del']):
 pref=args[args.index('pref')+1]
 matches=[line for line in state['rules'] if line.startswith(pref+':') and (len(args)<=4 or line==pref+': '+' '.join(args[4:]))]
 if args[1]=='del':
  if not matches:sys.exit(1)
  state['rules'].remove(matches[0])
 else:state['rules'].append(pref+': '+' '.join(args[4:]))
elif args[:2]==['route','show']:
 if args[2:]==['table','61000']:print('\\n'.join(state['routes']))
 sys.exit(0)
elif args[:2]==['route','flush']:state['routes']=[]
elif args[:2]==['route','del']:
 route=args[2:]
 if 'table' in route:
  offset=route.index('table');route=route[:offset]+route[offset+2:]
 value=' '.join(route)
 if value not in state['routes']:sys.exit(1)
 state['routes'].remove(value)
elif args[:2] in (['route','add'],['route','replace']):
 route=args[2:]
 if 'table' in route:
  offset=route.index('table');route=route[:offset]+route[offset+2:]
 state['routes'].append(' '.join(route))
path.write_text(json.dumps(state))
''')
        self.executable('nft','''#!/usr/bin/env python3
import os,re,sys
from pathlib import Path
root = Path(os.environ['TEST_ROOT'])
applied = root/'applied.nft'
if sys.argv[1] == 'list':
    if applied.exists(): print(applied.read_text());sys.exit(0)
    sys.exit(1)
if sys.argv[1] == 'delete':
    applied.unlink(missing_ok=True);sys.exit(0)
if sys.argv[1] == '-f':
    policy = Path(sys.argv[2]).read_text()
    # QWRT CONFIG_NF_TABLES_SET is disabled: no address/interface sets.
    if re.search(r'(?:ip6\\s+(?:saddr|daddr)|iifname|oifname)\\s*[{]', policy):
        print('Target kernel: nft set backend is unavailable', file=sys.stderr)
        sys.exit(95)
    # QWRT has ip6tables NAT, but no nftables NAT-chain backend.
    if re.search(r'\\btype\\s+nat\\b|\\bsnat\\s+to\\b', policy):
        print('Target kernel: nft NAT backend is unavailable', file=sys.stderr)
        sys.exit(97)
    for prefix in ('fc00::/7', 'fe80::/10', 'ff00::/8'):
        expected = 'iifname "br-lan" ip6 daddr '+prefix+' return'
        if policy.splitlines().count(expected) != 1:
            print('Missing exact single-prefix rule: '+expected, file=sys.stderr)
            sys.exit(96)
    applied.write_text(policy)
''')
        self.executable('ip6tables','''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
root=Path(os.environ['TEST_ROOT']);path=root/'ip6tables-state.json'
args=sys.argv[1:]
with (root/'events').open('a') as out:out.write('ip6tables '+' '.join(args)+'\\n')
while args and args[0]=='-w':
 args=args[1:]
 if args and args[0].isdigit():args=args[1:]
if args[:2]!=['-t','nat']:sys.exit(92)
args=args[2:]
state=json.loads(path.read_text()) if path.exists() else {'POSTROUTING':[
 ['-s','2001:db8:ffff::/64','-j','ACCEPT']]}
op=args[0];chain=args[1] if len(args)>1 else ''
rule=args[2:]
failure=os.environ.get('TEST_IP6TABLES_FAIL','')
if failure=='all' or (failure=='SNAT' and op in ('-A','-I') and 'SNAT' in rule):
 print('simulated ip6tables NAT failure',file=sys.stderr);sys.exit(88)
if op in ('-S','-L'):
 if chain and chain not in state:sys.exit(1)
 for name in ([chain] if chain else state):
  if name!='POSTROUTING':print('-N '+name)
  for entry in state[name]:print('-A '+name+' '+' '.join(entry))
 sys.exit(0)
if op=='-N':
 if chain in state:sys.exit(1)
 state[chain]=[]
elif op=='-X':
 if chain=='POSTROUTING' or chain not in state or state[chain]:sys.exit(1)
 if any('-j' in r and r[r.index('-j')+1]==chain for rows in state.values() for r in rows):sys.exit(1)
 del state[chain]
elif op=='-F':
 if chain=='POSTROUTING':print('forbidden unrelated-chain flush',file=sys.stderr);sys.exit(91)
 if chain not in state:sys.exit(1)
 state[chain]=[]
elif op in ('-A','-I','-C','-D'):
 if chain not in state:sys.exit(1)
 if op=='-C':sys.exit(0 if rule in state[chain] else 1)
 if op=='-D':
  if rule not in state[chain]:sys.exit(1)
  state[chain].remove(rule)
 else:
  pos=0
  if op=='-I' and rule and rule[0].isdigit():pos=int(rule.pop(0))-1
  if '-j' in rule:
   target=rule[rule.index('-j')+1]
   if target not in ('SNAT','ACCEPT','RETURN') and target not in state:sys.exit(1)
  if op=='-I':state[chain].insert(pos,rule)
  else:state[chain].append(rule)
else:sys.exit(93)
path.write_text(json.dumps(state))
''')
        self.executable('flow-flush','#!/bin/sh\necho "flush $*" >> "$TEST_ROOT/events"\n')
        # Keep init scripts in the fixture; no host service can be touched.
        text=self.script.read_text().replace('/etc/init.d/odhcpd',str(self.bin/'odhcpd'))
        self.script.write_text(text);self.executable('odhcpd','#!/bin/sh\necho "odhcpd $*" >> "$TEST_ROOT/events"\n')

    def test_ipv6_native_pd_does_not_require_an_isp_wan_global_address(self):
        self.ipv6_fixture(True)
        self.invoke('wan', '[{"enabled":true,"target":"wan"}]')
        state=json.loads((self.var/'aw1000-ipv6-follow/status.json').read_text())
        self.assertEqual(state['mode'],'native')
        policy=(self.path/'applied.nft').read_text()
        self.assertIn('2001:db8:1234::/56',policy);self.assertNotIn('snat to',policy)
        self.assertIn('default via fe80::1 dev eth4 metric 10',(self.path/'events').read_text())
        self.assertEqual(self.read_runtime('network')['lan']['ip6class'],['local','wan6'])

    def nat_state(self):
        path=self.path/'ip6tables-state.json'
        return json.loads(path.read_text()) if path.exists() else {
            'POSTROUTING':[['-s','2001:db8:ffff::/64','-j','ACCEPT']]}

    def assert_snat(self, address='2001:db8::2', dev='eth4'):
        state=self.nat_state()
        self.assertIn(['-s','fc00::/7','-j','AW1000_V6_SNAT'],state['POSTROUTING'])
        self.assertEqual(state['AW1000_V6_SNAT'],[
            ['-s','fd12:3456:789a::/48','-o',dev,'-j','SNAT','--to-source',address]])
        self.assertIn(['-s','2001:db8:ffff::/64','-j','ACCEPT'],state['POSTROUTING'])

    def assert_nat_clean(self):
        state=self.nat_state()
        self.assertNotIn('AW1000_V6_SNAT',state)
        self.assertEqual(state['POSTROUTING'],[
            ['-s','2001:db8:ffff::/64','-j','ACCEPT']])

    def test_ipv6_without_pd_uses_nat66_and_no_line_withdraws_default_ra(self):
        self.ipv6_fixture(False)
        rows='[{"enabled":true,"target":"wan"}]'
        self.invoke('wan',rows)
        self.assert_snat()
        self.assertNotIn('type nat',(self.path/'applied.nft').read_text())
        self.assertNotIn('snat to',(self.path/'applied.nft').read_text())
        self.invoke('',rows)
        state=json.loads((self.var/'aw1000-ipv6-follow/status.json').read_text())
        self.assertEqual(state['mode'],'unavailable')
        self.assertEqual(self.read_runtime('dhcp')['lan']['ra_lifetime'],'0')
        self.assert_nat_clean()

    def test_nat66_native_unavailable_and_restore_clean_only_owned_rules(self):
        self.ipv6_fixture(False)
        rows='[{"enabled":true,"target":"wan"}]'
        self.invoke('wan',rows)
        self.assert_snat()
        ubus=self.path/'ubus.json';data=json.loads(ubus.read_text())
        data['network.interface.wan6']['ipv6-prefix']=[
            {'address':'2001:db8:1234::','mask':56,'class':'wan6'}]
        ubus.write_text(json.dumps(data))
        self.invoke('wan',rows)
        state=json.loads((self.var/'aw1000-ipv6-follow/status.json').read_text())
        self.assertEqual(state['mode'],'native')
        self.assert_nat_clean()
        del data['network.interface.wan6']['ipv6-prefix']
        ubus.write_text(json.dumps(data))
        self.invoke('wan',rows)
        self.assert_snat()
        self.invoke('',rows)
        self.assert_nat_clean()
        self.invoke('wan',rows)
        self.assert_snat()
        self.invoke('--restore')
        self.assert_nat_clean()
        self.assertFalse((self.var/'aw1000-ipv6-follow/status.json').exists())
        self.assertFalse((self.var/'aw1000-ipv6-follow/signature').exists())

    def test_nat66_same_signature_repairs_external_rule_and_hook_flush(self):
        self.ipv6_fixture(False)
        rows='[{"enabled":true,"target":"wan"}]'
        self.invoke('wan',rows)
        self.assert_snat()
        run=self.var/'aw1000-ipv6-follow'
        signature=(run/'signature').read_text()
        # An unchanged, intact policy must not reload LAN or recreate NAT.
        events=(self.path/'events').read_text()
        self.invoke('wan',rows)
        extra=(self.path/'events').read_text()[len(events):]
        self.assertNotIn('odhcpd reload',extra)
        self.assertNotIn(' -F AW1000_V6_SNAT',extra)
        self.assert_snat()
        for damage in ('rule','hook'):
            with self.subTest(damage=damage):
                state=self.nat_state()
                if damage=='rule':state['AW1000_V6_SNAT']=[]
                else:state['POSTROUTING'].remove(['-s','fc00::/7','-j','AW1000_V6_SNAT'])
                (self.path/'ip6tables-state.json').write_text(json.dumps(state))
                self.invoke('wan',rows)
                self.assert_snat()
                self.assertEqual((run/'signature').read_text(),signature)

    def test_failed_snat_install_or_repair_never_publishes_working_ipv6(self):
        self.ipv6_fixture(False)
        rows='[{"enabled":true,"target":"wan"}]'
        dhcp=self.read_config('dhcp');dhcp['lan']['ra_lifetime']='0'
        self.write_config('dhcp',dhcp)
        run=self.var/'aw1000-ipv6-follow'
        for repair in (False,True):
            with self.subTest(repair=repair):
                if repair:
                    self.env.pop('TEST_IP6TABLES_FAIL',None)
                    self.invoke('wan',rows)
                    self.assert_snat()
                    self.assertEqual(self.read_runtime('dhcp')['lan']['ra_lifetime'],'1800')
                    state=self.nat_state();state['AW1000_V6_SNAT']=[]
                    (self.path/'ip6tables-state.json').write_text(json.dumps(state))
                self.env['TEST_IP6TABLES_FAIL']='SNAT'
                result=subprocess.run(['sh',str(self.script),'wan',rows],env=self.env,
                                      text=True,capture_output=True,timeout=30)
                # A safe helper may return an error or publish unavailable;
                # it must withdraw both success status and the default RA.
                self.assertEqual(self.read_runtime('dhcp')['lan']['ra_lifetime'],'0',result.stderr)
                if (run/'status.json').exists():
                    self.assertNotEqual(json.loads((run/'status.json').read_text())['mode'],'nat66')
                if (run/'signature').exists():
                    self.assertNotIn('|nat66|',(run/'signature').read_text())
                self.assert_nat_clean()

    def test_foreign_same_name_nat_chain_is_preserved_and_not_advertised(self):
        self.ipv6_fixture(False)
        dhcp=self.read_config('dhcp');dhcp['lan']['ra_lifetime']='0'
        self.write_config('dhcp',dhcp)
        foreign={'POSTROUTING':[['-s','2001:db8:ffff::/64','-j','ACCEPT']],
                 'AW1000_V6_SNAT':[['-s','fd99::/48','-j','SNAT','--to-source','2001:db8:ffff::2']]}
        (self.path/'ip6tables-state.json').write_text(json.dumps(foreign))
        result=subprocess.run(['sh',str(self.script),'wan','[{"enabled":true,"target":"wan"}]'],
                              env=self.env,text=True,capture_output=True,timeout=30)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.nat_state(),foreign)
        self.assertEqual(self.read_runtime('dhcp')['lan']['ra_lifetime'],'0')
        run=self.var/'aw1000-ipv6-follow'
        self.assertFalse((run/'nat-owned').exists())
        if (run/'status.json').exists():
            self.assertNotEqual(json.loads((run/'status.json').read_text())['mode'],'nat66')

    def test_restore_reports_ownership_error_and_cleans_early_failure_guard(self):
        self.ipv6_fixture(False)
        run=self.var/'aw1000-ipv6-follow';run.mkdir()
        marker=run/'nat-owned';marker.write_text('invalid-owner\n')
        failed=subprocess.run(['sh',str(self.script),'--restore'],env=self.env,
                              text=True,capture_output=True,timeout=30)
        self.assertNotEqual(failed.returncode,0)
        self.assertIn('Invalid IPv6 SNAT ownership marker',failed.stderr)
        self.assertEqual(marker.read_text(),'invalid-owner\n')
        marker.unlink()
        self.env['TEST_IP6TABLES_FAIL']='SNAT'
        failed=subprocess.run(['sh',str(self.script),'wan','[{"enabled":true,"target":"wan"}]'],
                              env=self.env,text=True,capture_output=True,timeout=30)
        self.assertNotEqual(failed.returncode,0)
        self.assertTrue((run/'filter-owned').exists())
        self.assertTrue((self.path/'applied.nft').exists())
        self.assertFalse((run/'rules-owned').exists())
        self.env.pop('TEST_IP6TABLES_FAIL')
        self.invoke('--restore')
        for name in ('filter-owned','rules-owned','config-owned','nat-owned','signature','status.json'):
            self.assertFalse((run/name).exists(),name)
        self.assertFalse((self.path/'applied.nft').exists())
        self.assert_nat_clean()

    def test_cellular_ipv6_prefers_real_gateway_and_rejects_bare_raw_ip_route(self):
        self.ipv6_fixture(False)
        status={'proto':'dhcpv6','up':True,'l3_device':'wwan0_1',
                'ipv6-address':[{'address':'2001:db8::2','mask':64}],
                'route':[{'target':'::','mask':0,'nexthop':'::'}]}
        (self.path/'ubus.json').write_text(json.dumps({'network.interface.cellularv6':status,
                                                       'network.interface.lan':{'l3_device':'br-lan'}}))
        self.executable('ip','''#!/bin/sh
echo "ip $*" >> "$TEST_ROOT/events"
case "$*" in
  "-6 route show default dev wwan0_1")
    echo "default dev wwan0_1 metric 1024"
    echo "default via fe80::2 dev wwan0_1 metric 1024"
    exit 0;;
  *"rule show"*|*"route show"*) exit 0;;
  *"rule del"*) exit 1;;
esac
''')
        rows='[{"enabled":true,"target":"cellular"}]'
        self.invoke('cellular',rows)
        state=json.loads((self.var/'aw1000-ipv6-follow/status.json').read_text())
        self.assertEqual(state['mode'],'nat66')
        self.assertIn('default via fe80::2 dev wwan0_1',(self.path/'events').read_text())
        self.executable('ip','''#!/bin/sh
echo "ip $*" >> "$TEST_ROOT/events"
case "$*" in
  "-6 route show default dev wwan0_1") echo "default dev wwan0_1 metric 1024"; exit 0;;
  *"rule show"*|*"route show"*) exit 0;;
  *"rule del"*) exit 1;;
esac
''')
        self.invoke('cellular',rows)
        state=json.loads((self.var/'aw1000-ipv6-follow/status.json').read_text())
        self.assertEqual(state['mode'],'unavailable')



class StabilityV123Tests(unittest.TestCase):
    def setUp(self):
        self.f=InternetTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def follow(self):
        f=self.f;f.script=f.rewrite('usr/sbin/aw1000-mwan-follow')
        for n in ('ipv6-follow','dnsmasq','flow-flush'):f.executable(n,'#!/bin/sh\necho "'+n+' $*" >> "$TEST_ROOT/events"\n')
        f.executable('iptables','#!/bin/sh\ncat "$TEST_ROOT/policy"\n')
        (f.path/'policy').write_text('-A mwan3_policy_aw_failover -j MARK --set-xmark 0x10000/0xff0000\n')
        return f.var/'aw1000-mwan-follow'
    def test_priority_only_preserves_wan_settings(self):
        f=self.f;n=f.read_config('network')
        n['wan'].update(proto='pppoe',device='eth4.85',username='keep',password='literal',mtu='1480',custom='keep')
        n['vlan']={'.type':'device','name':'eth4.85','vid':'85'};f.write_config('network',n)
        a=f.read_config('aw1000');a['internet']['vlan']='85';f.write_config('aw1000',a)
        self.assertTrue(f.invoke('--rpc','call','apply_priority',data={'priority':'cellular_only'})['ok'])
        after=f.read_config('network')
        for k,v in n['wan'].items():
            if k!='metric':self.assertEqual(after['wan'][k],v)
        self.assertEqual(after['vlan'],n['vlan']);self.assertEqual(f.read_config('aw1000')['internet']['vlan'],'85')
        f.invoke('--rpc','call','confirm')
        self.assertTrue(f.invoke('--rpc','call','apply_priority',data={'priority':'cellular_only'})['unchanged'])
        self.assertFalse((f.path/'pending').exists())
    def test_disabled_dns_instance_and_old_directory_are_isolated(self):
        f=self.f;run=self.follow();d=f.read_config('dhcp')
        d['unused']={'.type':'dnsmasq','disabled':'1','server':['127.0.0.1#5555']};f.write_config('dhcp',d);f.invoke('--once')
        old=f.path/'tmp/dnsmasq.d/aw1000-mwan.conf'
        self.assertTrue(old.exists());self.assertIn('|auto|',(run/'dns-signature').read_text())
        d['main']['confdir']=str(f.path/'tmp/dnsmasq-new.d');f.write_config('dhcp',d);f.invoke('--once')
        self.assertFalse(old.exists());new=f.path/'tmp/dnsmasq-new.d/aw1000-mwan.conf';self.assertTrue(new.exists())
        d['custom']={'.type':'dnsmasq','confdir':str(f.path/'tmp/dnsmasq-custom.d'),'server':['127.0.0.1#5335']}
        f.write_config('dhcp',d);f.invoke('--once')
        self.assertTrue(new.exists());self.assertIn('|mixed|',(run/'dns-signature').read_text())
        d['main']['disabled']='1';f.write_config('dhcp',d);f.invoke('--once');self.assertFalse(new.exists())
    def test_dns_retry_backoff_and_snapshot(self):
        f=self.f;run=self.follow();f.executable('dnsmasq','#!/bin/sh\necho restart >> "$TEST_ROOT/restarts"\nexit 44\n')
        for _ in range(2):
            p=subprocess.run(['sh',str(f.script),'--once'],env=f.env,capture_output=True,text=True,timeout=5)
            self.assertEqual(p.returncode,1,p.stderr)
        self.assertEqual((f.path/'restarts').read_text().splitlines(),['restart'])
        s=json.loads((run/'state.json').read_text());self.assertEqual(s['dns']['phase'],'retry');self.assertEqual(s['generation'],s['dns']['generation'])
        self.assertFalse((run/'dns-signature').exists())
        f.now.write_text('1006.00 0.00\n');f.executable('dnsmasq','#!/bin/sh\nexit 0\n');f.invoke('--once')
        self.assertEqual(json.loads((run/'state.json').read_text())['dns']['phase'],'applied')
    def test_ipv6_integrity_repair_preserves_persistent_configs(self):
        f=self.f;f.ipv6_fixture(False);rows='[{"enabled":true,"target":"wan"}]';f.invoke('wan',rows)
        before={n:(f.config/n).read_bytes() for n in ('network','dhcp','mwan3')}
        for damage in ('route','rule','filter'):
            state=json.loads((f.path/'ip6-state.json').read_text())
            if damage=='route':state['routes']=[x for x in state['routes'] if not x.startswith('default ')]
            if damage=='rule':state['rules']=[x for x in state['rules'] if not x.startswith('8901:')]
            (f.path/'ip6-state.json').write_text(json.dumps(state))
            if damage=='filter':(f.path/'applied.nft').write_text('table ip6 aw1000_v6 {}\n')
            f.invoke('wan',rows);state=json.loads((f.path/'ip6-state.json').read_text())
            self.assertTrue(any('default via fe80::1' in x for x in state['routes']))
            self.assertTrue(any(x.startswith('8901:') and '61000' in x for x in state['rules']))
            self.assertIn('forward_guard',(f.path/'applied.nft').read_text())
            self.assertEqual(json.loads((f.var/'aw1000-ipv6-follow/status.json').read_text())['mode'],'nat66')
        for n,v in before.items():self.assertEqual((f.config/n).read_bytes(),v)
    def test_standby_renumbering_does_not_reset_active_ipv6(self):
        f=self.f;f.ipv6_fixture(False);rows='[{"enabled":true,"target":"wan"},{"enabled":true,"target":"cellular"}]'
        path=f.path/'ubus.json';data=json.loads(path.read_text())
        data['network.interface.cellularv6']={'proto':'dhcpv6','up':True,'l3_device':'wwan0','ipv6-address':[{'address':'2001:db8:abcd::2','mask':64}]}
        path.write_text(json.dumps(data));f.invoke('wan',rows);before=(f.path/'events').read_text();policy=(f.path/'applied.nft').read_bytes()
        data['network.interface.cellularv6']['ipv6-address'][0]['address']='2001:db8:abcd::3';path.write_text(json.dumps(data));f.invoke('wan',rows)
        extra=(f.path/'events').read_text()[len(before):]
        for forbidden in ('odhcpd reload','flush ipv6',' -F AW1000_V6_SNAT','route add'):self.assertNotIn(forbidden,extra)
        self.assertEqual((f.path/'applied.nft').read_bytes(),policy);self.assertIn('rule add pref 8894',extra)
    def test_restore_deletes_only_exact_owned_rules(self):
        f=self.f;f.ipv6_fixture(False);f.invoke('wan','[{"enabled":true,"target":"wan"}]')
        path=f.path/'ip6-state.json';s=json.loads(path.read_text());foreign='8901: from 2001:db8:ffff::/64 lookup 12345'
        s['rules'].insert(0,foreign);path.write_text(json.dumps(s));f.invoke('--restore');s=json.loads(path.read_text())
        self.assertEqual(s['rules'],[foreign]);self.assertEqual(s['routes'],[])
    def test_no_dhcp_route_is_invented(self):
        f=self.f;f.executable('ip','#!/bin/sh\necho "$*" >> "$TEST_ROOT/routes"\n')
        info={'up':True,'l3_device':'eth4','ipv4-address':[{'address':'192.0.2.2','mask':24}],'route':[]}
        for routes in ([],[{'target':'0.0.0.0','mask':0}]):
            info['route']=routes;(f.path/'ubus.json').write_text(json.dumps({'network.interface.wan':info}));f.invoke('--route-guard')
            self.assertFalse((f.path/'routes').exists())
    def test_slow_ipv6_cannot_block_dns_or_retain_old_generation(self):
        import time
        f=self.f;run=self.follow();f.env['AW1000_IPV6_INLINE']='0'
        f.executable('ipv6-follow','#!/bin/sh\necho "$1" >> "$TEST_ROOT/v6-start"\n[ "$1" != wan ] || sleep 30\n')
        begin=time.monotonic();f.invoke('--once');self.assertLess(time.monotonic()-begin,3)
        self.assertTrue((f.path/'tmp/dnsmasq.d/aw1000-mwan.conf').exists())
        worker=subprocess.Popen(['sh',str(f.script),'--ipv6-worker'],env=f.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            end=time.monotonic()+5
            while not (f.path/'v6-start').exists() and time.monotonic()<end:time.sleep(.05)
            self.assertTrue((f.path/'v6-start').exists())
            (f.path/'policy').write_text('-A mwan3_policy_aw_failover -j MARK --set-xmark 0x20000/0xff0000\n');f.invoke('--once')
            end=time.monotonic()+7
            while time.monotonic()<end:
                try:
                    s=json.loads((run/'ipv6-result.json').read_text())
                    if s['choice']=='cellular' and s['code']==0:break
                except (FileNotFoundError,json.JSONDecodeError):pass
                time.sleep(.1)
            else:self.fail('new IPv6 generation not reconciled')
            self.assertIn('cellular|auto|',(run/'dns-signature').read_text())
        finally:
            stop=f.var/'aw1000-internet/stopping';stop.parent.mkdir(exist_ok=True);stop.touch()
            try:worker.communicate(timeout=6)
            except subprocess.TimeoutExpired:worker.kill();worker.communicate();self.fail('IPv6 worker did not stop')

    def test_unowned_matching_rules_and_routes_are_never_adopted(self):
        for kind in ('rules','routes'):
            with self.subTest(kind=kind):
                f=InternetTests();f.setUp()
                try:
                    f.ipv6_fixture(False)
                    existing='8901: from all lookup 61000' if kind=='rules' else 'unreachable default metric 32767'
                    state={'rules':[],'routes':[]};state[kind]=[existing]
                    (f.path/'ip6-state.json').write_text(json.dumps(state))
                    p=subprocess.run(['sh',str(f.script),'wan','[{"enabled":true,"target":"wan"}]'],
                                     env=f.env,text=True,capture_output=True,timeout=30)
                    self.assertNotEqual(p.returncode,0)
                    self.assertIn('without ownership',p.stderr)
                    f.invoke('--restore')
                    self.assertEqual(json.loads((f.path/'ip6-state.json').read_text()),state)
                finally:f.doCleanups()

if __name__ == '__main__': unittest.main()
