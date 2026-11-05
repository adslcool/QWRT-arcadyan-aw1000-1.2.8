"""Run the shipped read-only diagnostics against isolated QWRT-like fixtures."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.calls = self.root / 'calls.jsonl'
        self.env = {**os.environ, 'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
                    'DIAGNOSTIC_ROOT': str(self.root)}
        self.secret = 'DO_NOT_PRINT_PPP_PASSWORD'
        command = '''#!/usr/bin/env python3
import json,os,pathlib,subprocess,sys
root=pathlib.Path(os.environ['DIAGNOSTIC_ROOT']);name=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]
with (root/'calls.jsonl').open('a') as f:f.write(json.dumps([name,*args])+'\\n')
if name=='uci':
 args=[a for a in args if a!='-q']
 if args[0]=='show':print("dhcp.@dnsmasq[0]=dnsmasq\\ndhcp.@dnsmasq[0].password='DO_NOT_PRINT_PPP_PASSWORD'")
 elif args[0]=='get':
  values=json.loads((root/'uci.json').read_text())
  if args[1] not in values:sys.exit(1)
  print(values[args[1]])
 else:raise SystemExit('Mutation attempted')
elif name=='ubus':
 obj=args[args.index('call')+1]
 values=json.loads((root/'ubus.json').read_text());print(json.dumps(values.get(obj,{})))
elif name=='ip':print('default via 192.0.2.1 dev eth4' if '-4' in args else 'unreachable default metric 32767')
elif name=='iptables':
 assert '-S' in args
 print('-A mwan3_policy_aw_failover -j MARK --set-xmark 0x20000/0xff0000')
elif name=='nft':
 assert args[0]=='list';print('table ip6 aw1000_v6 { }')
elif name=='netstat':print('Proto Recv-Q Send-Q Local Address Foreign Address State\\nudp 0 0 0.0.0.0:53 0.0.0.0:*')
elif name=='timeout':sys.exit(subprocess.call(args[1:]))
elif name=='nslookup':print('Address: 192.0.2.80')
elif name=='od':raise SystemExit('od must not be invoked')
elif name=='logread':print('aw1000-leds: fixture')
'''
        for name in ('uci', 'ubus', 'ip', 'iptables', 'nft', 'netstat', 'timeout', 'nslookup', 'od', 'logread'):
            self.executable(self.bin / name, command)
        for service in ('aw1000-internet', 'aw1000-leds', 'mwan3', 'dnsmasq', 'mosdns', 'odhcpd', 'fxsd'):
            self.executable(self.root / 'etc/init.d' / service, '#!/bin/sh\n[ "$1" = status ] || exit 99\necho running\n')
        self.write('etc/aw1000-mod-version', '1.0.13\n')
        self.write('etc/aw1000-build-date', '2026-10-27\n')
        self.write('etc/openwrt_release', "DISTRIB_DESCRIPTION='QWRT '\nDISTRIB_REVISION='R26.10.27'\n")
        self.write('var/run/aw1000-mwan-follow/selected', 'livecell\n')
        self.write('var/run/aw1000-mwan-follow/dns-signature', 'livecell|auto|198.51.100.53 223.5.5.5\n')
        self.write('var/run/aw1000-internet/wan-route-guard.json',
                   '{"device":"eth4","table":"1","repaired":{"main":true,"mwan":true}}\n')
        self.write('var/run/mwan3track/livecell/STATUS', 'online\n')
        self.write('var/run/aw1000-internet/mwan-reload-needed', '')
        self.write('var/run/aw1000-internet/dirty', '')
        self.write('var/run/aw1000-internet/blocked', 'livecell\n')
        self.write('etc/resolv.conf', 'nameserver 127.0.0.1\n')
        self.write('tmp/dnsmasq.d/aw1000-mwan.conf', 'no-resolv\nserver=198.51.100.53\nserver=223.5.5.5\n')
        self.write('var/etc/dnsmasq.conf.cfg001', 'port=53\nconf-dir=/tmp/dnsmasq.d\npassword=' + self.secret + '\n')
        self.write('var/run/aw1000-ipv6-follow/status.json', json.dumps(
            {'choice': 'livecell', 'mode': 'unavailable', 'upstream': {'iface': 'livecellv6', 'dev': 'wwan0'}}))
        values = {'mwan3.globals.aw1000_cellular': 'livecell', 'mwan3.globals.aw1000_enabled': '1',
                  'aw1000.internet.priority': 'cellular', 'network.lan.ipaddr': '192.168.1.1',
                  'network.livecellv6': 'interface', 'dhcp.lan.dhcp_option': '6,192.168.1.1 224,' + self.secret,
                  'dhcp.@dnsmasq[0].port': '53', 'dhcp.@dnsmasq[0].noresolv': '0',
                  'dhcp.@dnsmasq[0].confdir': '/tmp/dnsmasq.d',
                  'dhcp.@dnsmasq[0].server': '127.0.0.1#5335 https://user:' + self.secret + '@resolver.invalid',
                  'network.wan.password': self.secret, 'fxs.main.led': 'ipq::led12'}
        self.write('uci.json', json.dumps(values))
        iface = {'up': True, 'proto': 'dhcp', 'l3_device': 'wwan0', 'ipv4-address': [{'address': '198.51.100.2'}],
                 'dns-server': ['198.51.100.53'], 'route': [{'target': '0.0.0.0', 'mask': 0}],
                 'password': self.secret, 'username': 'PRIVATE_USER',
                 'errors': [{'code': 'TEST_ERROR', 'data': {'password': self.secret}}]}
        self.write('ubus.json', json.dumps({'network.interface.livecell': iface,
            'network.interface.livecellv6': {'proto': 'dhcpv6', 'errors': [{'code': 'NO_ROUTE', 'password': self.secret}]},
            'aw1000.internet': {'selected': 'livecell', 'dns_mode': 'auto', 'username': self.secret,
                                'wired_errors': [{'code': 'AUTH_FAILED', 'data': [self.secret]}]}}))

    def executable(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o755)

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)

    def script(self, name):
        text = (ROOT / 'files/usr/sbin' / name).read_text()
        text = re.sub(r'/(etc|var|tmp|sys)/', lambda match: str(self.root) + match[0], text)
        script = self.root / name
        self.executable(script, text)
        return script

    def run_script(self, name, *args):
        result = subprocess.run(['sh', str(self.script(name)), *args], env=self.env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def read_calls(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def test_dns_default_collects_actual_state_without_queries_credentials_or_mutations(self):
        output = self.run_script('aw1000-dns-check')
        self.assertIn('cellular_interface=livecell', output)
        self.assertIn('tracker_STATUS=online', output)
        self.assertIn('198.51.100.2', output)
        self.assertIn('server=198.51.100.53', output)
        self.assertIn('0.0.0.0:53', output)
        self.assertIn('NO_ROUTE', output)
        self.assertIn('mwan-reload-needed=present', output)
        self.assertIn('"repaired":{"main":true,"mwan":true}', output)
        self.assertIn('reload-needed=absent', output)
        self.assertIn('dirty=present', output)
        self.assertIn('blocked=present interface=livecell', output)
        self.assertIn('dnsmasq_section=@dnsmasq[0]', output)
        self.assertIn('server=127.0.0.1#5335', output)
        self.assertNotIn(self.secret, output)
        self.assertNotIn('PRIVATE_USER', output)
        self.assertNotIn('224,', output)
        calls = self.read_calls()
        self.assertFalse(any(c[0] in ('timeout', 'nslookup') for c in calls))
        self.assertTrue(all('get' in c or 'show' in c for c in calls if c[0] == 'uci'))
        self.assertTrue(all(c[-1] == 'status' for c in calls if c[0] == 'ubus'))

    def test_dns_queries_are_explicit_bounded_and_invalid_domains_stop_before_collection(self):
        output = self.run_script('aw1000-dns-check', '--query', 'test.example')
        self.assertIn('query=test.example resolver=127.0.0.1', output)
        calls = self.read_calls()
        self.assertEqual(len([c for c in calls if c[0] == 'nslookup']), 3)
        self.assertTrue(all(c[1] == '8' for c in calls if c[0] == 'timeout'))
        before = self.calls.read_bytes()
        result = subprocess.run(['sh', str(self.script('aw1000-dns-check')), '--query', '$(touch /tmp/UNWANTED)'],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.calls.read_bytes(), before)

    def test_led_big_endian_dump_uses_lua_without_od(self):
        lua = shutil.which('lua5.1') or shutil.which('lua')
        self.assertIsNotNone(lua)
        (self.bin / 'lua').symlink_to(lua)
        dt = self.root / 'sys/firmware/devicetree/base/soc/ledc@191E000'
        (dt / 'led0').mkdir(parents=True)
        (dt / 'qti,ledc_blink_indices').write_bytes(bytes.fromhex('0000000f000000ff'))
        (dt / 'led0/active').write_bytes(b'')
        output = self.run_script('aw1000-led-check')
        self.assertIn('hex=00 00 00 0f 00 00 00 ff', output)
        self.assertIn('cells=15 255', output)
        self.assertIn('(empty boolean property)', output)
        self.assertFalse(any(c[0] == 'od' for c in self.read_calls()))


if __name__ == '__main__':
    unittest.main()
