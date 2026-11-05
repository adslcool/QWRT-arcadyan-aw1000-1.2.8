"""Run the shipped backends with QWRT's real, regex-free AArch64 jq.

Set AW1000_TEST_TARGET_ROOT to an extracted original QWRT root. The other
commands stay inside InternetTests' isolated fixtures; no host service is run.
"""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import unittest

import test_internet as fixtures


class TargetJqCompatibilityTests(unittest.TestCase):
    def test_radio_registration_samples_with_original_qwrt_jq(self):
        self.fx.test_radio_sampling_distinguishes_real_loss_from_missing_at_response()

    def setUp(self):
        target = os.environ.get('AW1000_TEST_TARGET_ROOT')
        qemu = shutil.which('qemu-aarch64-static')
        if not target or not qemu:
            self.skipTest('set AW1000_TEST_TARGET_ROOT and install qemu-aarch64-static')
        self.target = Path(target)
        binary = self.target/'usr/bin/jq'
        if not binary.is_file():
            self.skipTest('target jq is unavailable')
        self.jq = [qemu, '-L', str(self.target), str(binary)]
        # Compose the fixture instead of inheriting its whole test suite.
        self.fx = fixtures.InternetTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.fx.executable('jq', '#!/bin/sh\nexec '+shlex.join(self.jq)+' "$@"\n')

    def dns_fixture(self):
        f = self.fx
        m=f.read_config('mwan3');m['wwan']=m.pop('cellular');m['globals']['aw1000_cellular']='wwan';m['aw_cell']['interface']='wwan';f.write_config('mwan3',m)
        f.script = f.rewrite('usr/sbin/aw1000-mwan-follow')
        for name in ('dnsmasq', 'flow-flush', 'logger'):
            f.executable(name, '#!/bin/sh\necho "'+name+' $*" >> "$TEST_ROOT/events"\n')
        f.executable('ipv6-follow', '#!/bin/sh\necho simulated-v6-error >&2\nexit 77\n')
        f.executable('iptables', '#!/bin/sh\ncat "$TEST_ROOT/policy"\n')
        (f.path/'ubus.json').write_text(json.dumps({
            'network.interface.wwan': {
                'up': True, 'l3_device': 'wwan0_1',
                'ipv4-address': [{'address': '10.39.182.83', 'mask': 29}],
                'dns-server': ['183.203.33.222', '183.203.33.211',
                               '2409:800c:2000::222', '999.1.2.3', None, 42]},
            'network.interface.wan': {
                'up': True, 'l3_device': 'eth4',
                'ipv4-address': [{'address': '192.0.2.2', 'mask': 24}],
                'dns-server': ['192.0.2.53', '2001:db8::53']}}))
        return f.path/'tmp/dnsmasq.d/aw1000-mwan.conf', f.var/'aw1000-mwan-follow'

    def select(self, mark):
        (self.fx.path/'policy').write_text(
            '-A mwan3_policy_aw_failover -j MARK --set-xmark '+mark+'/0xff0000\n')
        self.fx.invoke('--once')

    def test_original_target_regex_error_is_reproducible(self):
        result = subprocess.run(self.jq+['-n', '"183.203.33.222" | test("^[0-9.]+$")'],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 5)
        self.assertIn('compiled without ONIGURUMA', result.stderr)

    def test_dns_recovers_from_startup_no_resolv_despite_ipv6_failure(self):
        dns, run = self.dns_fixture()
        # This is the device failure sequence: no egress at startup, followed
        # by a usable cellular link. A regex error used to pin no-resolv here.
        self.select('0xfe0000')
        self.assertEqual(dns.read_text(), 'no-resolv\n')
        self.assertEqual((run/'dns-signature').read_text(), '|auto|\n')
        self.select('0x20000')
        self.assertEqual(dns.read_text().splitlines(), [
            'no-resolv', 'server=183.203.33.222', 'server=183.203.33.211',
            'server=223.5.5.5', 'server=119.29.29.29'])
        self.assertEqual((run/'selected').read_text().strip(), 'wwan')
        self.assertTrue((run/'dns-signature').read_text().startswith('wwan|auto|'))
        self.assertEqual((run/'ipv6-status').read_text().strip(), '77')
        self.assertIn('simulated-v6-error', (run/'ipv6-last.log').read_text())
        self.assertEqual((self.fx.path/'events').read_text().count('dnsmasq restart'), 2)

    def test_dns_failover_replaces_isp_servers_and_no_egress_clears_them(self):
        dns, run = self.dns_fixture()
        self.select('0x20000')
        self.select('0x10000')
        self.assertEqual(dns.read_text().splitlines(), [
            'no-resolv', 'server=192.0.2.53', 'server=223.5.5.5', 'server=119.29.29.29'])
        self.assertTrue((run/'dns-signature').read_text().startswith('wan|auto|'))
        self.assertIn('flow-flush 0x20000', (self.fx.path/'events').read_text())
        self.select('0xfe0000')
        self.assertEqual(dns.read_text(), 'no-resolv\n')
        self.assertEqual((run/'dns-signature').read_text(), '|auto|\n')

    def test_ipv6_native_plan_accepts_pd_without_wan_global_address(self):
        f = self.fx
        f.ipv6_fixture(True)
        # Invalid provider data must not become nft syntax or a prefix class.
        path = f.path/'ubus.json'
        status = json.loads(path.read_text())
        status['network.interface.wan6']['ipv6-prefix'].extend([
            {'address': '2001:db8:bad::;reject', 'mask': 56, 'class': 'bad'},
            {'address': None, 'mask': 56, 'class': 'bad'}])
        path.write_text(json.dumps(status))
        f.invoke('wan', '[{"enabled":true,"target":"wan"}]')
        state = json.loads((f.var/'aw1000-ipv6-follow/status.json').read_text())
        self.assertEqual(state['mode'], 'native')
        self.assertEqual(state['upstream']['address'], '')
        self.assertEqual(state['upstream']['prefixes'], [
            {'address': '2001:db8:1234::', 'mask': 56, 'class': 'wan6'}])
        self.assertEqual(f.read_runtime('network')['lan']['ip6class'], ['local', 'wan6'])
        policy = (f.path/'applied.nft').read_text()
        self.assertIn('2001:db8:1234::/56', policy)
        self.assertNotIn(';reject', policy)
        self.assertNotIn('snat to', policy)

    def test_ipv6_nat66_uses_global_address_and_real_gateway(self):
        f = self.fx
        f.ipv6_fixture(False)
        path = f.path/'ubus.json'
        status = json.loads(path.read_text())
        v6 = status.pop('network.interface.wan6')
        v6['l3_device'] = 'wwan0_1'
        v6['ipv6-address'] = [
            {'address': 'fe80::1', 'mask': 64},
            {'address': '2409:BAD::;reject', 'mask': 64},
            {'address': None, 'mask': 64},
            {'address': '2409:890d:378:19a5::2', 'mask': 64}]
        v6['route'] = [
            {'target': '::', 'mask': 0, 'nexthop': 'invalid:gateway'},
            {'target': '::', 'mask': 0, 'nexthop': 'fe80::991a:f31a:b14e:c246'}]
        status['network.interface.wwan_v6'] = v6
        path.write_text(json.dumps(status))
        rows = '[{"enabled":true,"target":"wwan"}]'
        f.invoke('wwan', rows)
        state = json.loads((f.var/'aw1000-ipv6-follow/status.json').read_text())
        self.assertEqual(state['mode'], 'nat66')
        self.assertEqual(state['upstream']['address'], '2409:890d:378:19a5::2')
        self.assertEqual(state['upstream']['gateway'], 'fe80::991a:f31a:b14e:c246')
        f.assert_snat('2409:890d:378:19a5::2', 'wwan0_1')
        self.assertNotIn('type nat', (f.path/'applied.nft').read_text())
        self.assertNotIn('snat to', (f.path/'applied.nft').read_text())
        self.assertIn('from 2409:890d:378:19a5:0:0:0:2/128', (f.path/'events').read_text())
        f.invoke('', rows)
        state = json.loads((f.var/'aw1000-ipv6-follow/status.json').read_text())
        self.assertEqual(state['mode'], 'unavailable')
        self.assertEqual(f.read_runtime('dhcp')['lan']['ra_lifetime'], '0')
        f.assert_nat_clean()

    def test_apply_accepts_literal_credentials_and_normalizes_numeric_fields(self):
        f = self.fx
        password = 'pa\'$(false);"literal'
        self.assertTrue(f.apply(mode='pppoe', username='user@example', password=password,
                                vlan='0085', mtu='1492')['ok'])
        wan = f.read_config('network')['wan']
        self.assertEqual(wan['password'], password)
        self.assertEqual(wan['device'], 'eth4.85')
        self.assertEqual(wan['mtu'], '1492')
        state = f.invoke('--status')
        self.assertNotIn(password, json.dumps(state))
        self.assertTrue(f.invoke('--rpc', 'call', 'confirm')['ok'])

    def test_apply_rejects_bad_types_control_characters_and_numeric_bounds(self):
        f = self.fx
        before = {name: (f.config/name).read_bytes() for name in
                  ('network', 'mwan3', 'aw1000', 'qmodem', 'dhcp', 'firewall')}
        cases = [
            {'vlan': '0'}, {'vlan': '4095'}, {'vlan': '8x'}, {'vlan': '00850'},
            {'vlan': None}, {'mtu': '1279'}, {'mtu': '1501'}, {'mtu': 1492},
            {'mode': 'pppoe', 'username': 'u', 'password': 'p', 'mtu': '1500'},
            {'mode': 'pppoe', 'username': ''}, {'username': 'u\nx'},
            {'username': 'u\x7fx'}, {'password': 'p\x00x'}, {'username': ['u']},
            {'priority': 'loadbalance'}, {'mode': 'static'}]
        for values in cases:
            with self.subTest(values=values):
                self.assertIn('error', f.apply(**values))
                self.assertFalse((f.path/'pending').exists())
                for name, content in before.items():
                    self.assertEqual((f.config/name).read_bytes(), content, name)


if __name__ == '__main__':
    unittest.main()
