"""Run the pinned mwan3 common.sh with isolated mask/state fixtures."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from internet import patch_common


def vendor_common():
    lock = json.loads((ROOT/'sources.lock.json').read_text())['mwan3_legacy']
    with tarfile.open(ROOT/lock['archive'], 'r:xz') as bundle:
        return bundle.extractfile('files/lib/mwan3/common.sh').read().decode()


HARNESS = r'''. "$COMMON"
MWAN3_STATUS_DIR="$TEST_RUN"
MWAN3_STATUS_IPTABLES_LOG_DIR="$TEST_RUN/iptables_log"
config_load() { :; }
config_get() {
    case "$3" in
        mmx_mask) export "$1=${TEST_MASK-$4}";;
        source_routing) export "$1=0";;
        *) exit 90;;
    esac
}
uci_get_state() { echo unexpected-state-read >> "$TEST_STATE"; return 1; }
uci_toggle_state() { printf '%s\n' "$4" >> "$TEST_STATE"; }
LOG() { [ "$1" != err ] || printf '%s\n' "$*" >&2; return 0; }
mv() {
    # A complete temporary file must exist before the cache is published.
    [ "$1" = -f ] && [ "$2" = "$TEST_RUN/mmx_mask.tmp.$$" ] || exit 91
    mwan3_normalize_mmx_mask "$(cat "$2")" >/dev/null || exit 92
    [ "${TEST_FAIL_MV:-0}" != 1 ] || return 1
    command mv "$@"
}
mwan3_init
printf '%s|%s|%s|%s|%s\n' "$MMX_MASK" "$MWAN3_INTERFACE_MAX" \
    "$MMX_DEFAULT" "$MMX_BLACKHOLE" "$MMX_UNREACHABLE"
echo continued
'''


class MwanMaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.run = self.path/'run'
        self.run.mkdir()
        self.cache = self.run/'mmx_mask'
        self.state = self.path/'state'
        self.common = self.path/'common.sh'
        self.common.write_text(patch_common(vendor_common()))
        self.env = {**os.environ, 'COMMON':str(self.common), 'TEST_RUN':str(self.run),
                    'TEST_STATE':str(self.state), 'TEST_MASK':'0x00ff0000'}

    def execute(self, mask=None, shell=None):
        env = dict(self.env)
        if mask is not None:
            env['TEST_MASK'] = mask
        return subprocess.run((shell or ['/bin/sh'])+['-c', HARNESS], env=env,
                              text=True, capture_output=True, timeout=15)

    def assert_marks(self, result, mask=0xff0000):
        self.assertEqual(result.returncode, 0, result.stderr)
        bits = [n for n in range(32) if (mask >> n) & 1]
        full = (1 << len(bits))-1

        def spread(value):
            return sum(((value >> n) & 1) << bit for n, bit in enumerate(bits))

        expected = [hex(mask), str(full-3), hex(mask), hex(spread(full-2)), hex(spread(full-1))]
        self.assertEqual(result.stdout.splitlines(), ['|'.join(expected), 'continued'])
        self.assertNotIn('unexpected-state-read', self.state.read_text())
        self.assertEqual(self.state.read_text().splitlines()[-1], str(full-3))

    def test_patch_requires_the_pinned_function(self):
        with self.assertRaises(ValueError):
            patch_common(vendor_common().replace('mwan3_init()', 'changed_init()'))

    def test_missing_empty_and_invalid_cache_are_repaired(self):
        for cache in (None, '', 'not-a-mask', '0x3', '0x100000000', '1 + 2', '0xff00\n0xff00'):
            with self.subTest(cache=cache):
                self.cache.unlink(missing_ok=True)
                if cache is not None:
                    self.cache.write_text(cache)
                self.assert_marks(self.execute())
                self.assertEqual(self.cache.read_text(), '0xff0000\n')
                self.assertEqual(list(self.run.glob('mmx_mask.tmp.*')), [])

    def test_valid_active_cache_wins_over_changed_or_invalid_config(self):
        for config in ('0xff0000', '', 'bad-config'):
            with self.subTest(config=config):
                self.cache.write_text('0X3F00\n')
                self.assert_marks(self.execute(config), 0x3f00)
                self.assertEqual(self.cache.read_text(), '0X3F00\n')

    def test_capacity_is_computed_when_state_is_absent_or_stale(self):
        self.cache.write_text('0xff0000\n')
        self.assertFalse(self.state.exists())
        self.assert_marks(self.execute())
        self.state.write_text('99999\n')
        self.assert_marks(self.execute())

    def test_literal_masks_and_noncontiguous_bits(self):
        for configured, numeric in (('0X00FF0000', 0xff0000), ('16711680', 0xff0000),
                                    ('00001792', 1792), ('0x80000003', 0x80000003),
                                    ('0xffffffff', 0xffffffff)):
            with self.subTest(mask=configured):
                self.cache.unlink(missing_ok=True)
                self.assert_marks(self.execute(configured), numeric)
                self.assertEqual(self.cache.read_text(), hex(numeric)+'\n')

    def test_invalid_config_stops_before_marks_or_state_are_generated(self):
        for configured in ('', '0', '0x0', '0x3', '0x100000000', '4294967296',
                           '-1', 'mask', '0xff00 + 1', '$(touch forbidden)', '0xff00\n'):
            with self.subTest(mask=configured):
                self.cache.unlink(missing_ok=True)
                self.state.unlink(missing_ok=True)
                result = self.execute(configured)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertIn('Invalid mwan3 mmx_mask', result.stderr)
                self.assertFalse(self.cache.exists())
                self.assertFalse(self.state.exists())

    def test_concurrent_initializers_publish_complete_cache(self):
        self.cache.write_text('')
        children = [subprocess.Popen(['/bin/sh', '-c', HARNESS], env=self.env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for _ in range(16)]
        for child in children:
            out, err = child.communicate(timeout=15)
            self.assert_marks(subprocess.CompletedProcess(child.args, child.returncode, out, err))
        self.assertEqual(self.cache.read_text(), '0xff0000\n')
        self.assertEqual(self.state.read_text().splitlines(), ['252']*len(children))
        self.assertEqual(list(self.run.glob('mmx_mask.tmp.*')), [])

    def test_cache_write_failure_stops_current_process(self):
        self.env['TEST_FAIL_MV'] = '1'
        result = self.execute()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('Cannot publish mwan3 mask cache', result.stderr)
        self.assertFalse(self.state.exists())
        self.assertEqual(list(self.run.glob('mmx_mask.tmp.*')), [])

    def test_original_target_busybox_mask_arithmetic(self):
        # Optional local integration check: a normal source checkout needs only sh.
        target = os.environ.get('AW1000_TEST_TARGET_ROOT')
        if not target or not shutil.which('qemu-aarch64-static'):
            self.skipTest('set AW1000_TEST_TARGET_ROOT to the extracted QWRT root')
        root = Path(target)
        busybox = root/'bin/busybox'
        if not busybox.is_file():
            self.skipTest('target BusyBox is unavailable')
        shell = ['qemu-aarch64-static', '-L', str(root), str(busybox), 'ash']
        for configured, numeric in (('0x00ff0000', 0xff0000), ('0x3f00', 0x3f00),
                                    ('0x80000003', 0x80000003), ('0xffffffff', 0xffffffff)):
            with self.subTest(mask=configured):
                self.cache.unlink(missing_ok=True)
                self.assert_marks(self.execute(configured, shell), numeric)


class LowTrafficTests(unittest.TestCase):
    def test_ipv6_memory_config_uses_supported_original_qwrt_uci_flags(self):
        target=os.getenv('AW1000_TEST_TARGET_ROOT')
        if not target or not shutil.which('qemu-aarch64-static'):
            self.skipTest('QWRT runtime and QEMU required')
        import shlex
        source=(ROOT/'files/usr/sbin/aw1000-ipv6-follow').read_text()
        wrapper=source[source.index('uci() {\n'):source.index('\n# Do not incorporate')]
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);cfg=root/'config';cfg.mkdir();delta=root/'delta';delta.mkdir()
            persist=root/'persistent';persist.mkdir();binpath=root/'bin';binpath.mkdir()
            values={'network':"config interface 'lan'\n option ip6class 'local'\n",
                    'dhcp':"config dhcp 'lan'\n option ra_lifetime '0'\n"}
            for name,value in values.items():
                (persist/name).write_text(value);(cfg/name).write_text(value)
            cli=binpath/'uci'
            cli.write_text('#!/bin/sh\nexec '+shlex.join(['qemu-aarch64-static','-L',target,target+'/sbin/uci'])+' "$@"\n');cli.chmod(0o755)
            body='RUN='+shlex.quote(d)+'\n'+wrapper+r'''
uci delete network.lan.ip6class
uci add_list network.lan.ip6class=local
uci add_list network.lan.ip6class=wan_v6
uci commit network
uci set dhcp.lan.ra_lifetime=1800
uci commit dhcp
uci -q get network.lan.ip6class
uci -q get dhcp.lan.ra_lifetime
'''
            p=subprocess.run(['sh','-e'],input=body,text=True,capture_output=True,
                env={**os.environ,'PATH':str(binpath)+':'+os.environ['PATH']},timeout=10)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(p.stdout.splitlines(),['local wan_v6','1800'])
            self.assertFalse((delta/'network').exists());self.assertFalse((delta/'dhcp').exists())
            for name,value in values.items():self.assertEqual((persist/name).read_text(),value)
            # Read again with a fresh process and no staged delta.
            command=[str(cli),'-c',str(cfg),'get','dhcp.lan.ra_lifetime']
            self.assertEqual(subprocess.check_output(command,text=True).strip(),'1800')

    def test_wan_recovery_survives_delayed_selected_publication(self):
        from internet import patch_tracker_runtime
        lock=json.loads((ROOT/'sources.lock.json').read_text())['mwan3_legacy']
        with tarfile.open(ROOT/lock['archive'],'r:xz') as z:
            text=patch_tracker_runtime(z.extractfile('files/usr/sbin/mwan3track').read().decode())
        text='\n'.join(line for line in text.splitlines() if not line.startswith('. /lib/'))
        text=text.replace('main "$@"','').replace('while true; do','while [ ! -f "$TEST_RUN/stop" ]; do')
        harness=r'''
MWAN3TRACK_STATUS_DIR="$TEST_RUN"; MAX_SLEEP=100
uci() { case "$*" in *aw1000.internet.priority) echo wired;; *aw1000_cellular) echo cellular;; esac; }
mwan3_init() { :; }
config_get() {
    case "$3" in
      family) value=ipv4;; track_method) value=ping;; count|reliability) value=1;;
      timeout) value=1;; interval|down) value=2;; up) value=4;; *) value=$4;;
    esac
    eval "$1=\"\$value\""
}
config_get_bool() { eval "$1=\"\$4\""; }
validate_track_method() { PING=ping; }
config_list_foreach() { track_ips='223.5.5.5 119.29.29.29'; }
firstconnect() { STARTED=1; STATUS=offline; score=0; echo offline > "$TEST_RUN/wan/STATUS"; }
get_uptime() { cat "$TEST_RUN/clock"; }
LOG() { :; }
env() {
    echo "$(get_uptime):$*" >> "$TEST_RUN/events"
    case "$*" in
        *ACTION=connected*) touch "$TEST_RUN/publish";;
        *ACTION=disconnected*) : > "$TEST_RUN/selected";;
    esac
}
WRAP() { get_uptime >> "$TEST_RUN/pings"; return 0; }
sleep() {
    t=$(cat "$TEST_RUN/clock"); t=$((t+1)); echo "$t" > "$TEST_RUN/clock"
    if [ -f "$TEST_RUN/publish" ]; then echo wan > "$TEST_RUN/selected"; rm "$TEST_RUN/publish"; fi
    [ "$t" -lt 40 ] || touch "$TEST_RUN/stop"
}
main wan
'''
        with tempfile.TemporaryDirectory() as d:
            run=Path(d);(run/'wan').mkdir();(run/'cellular').mkdir()
            (run/'selected').write_text('');(run/'clock').write_text('0\n')
            text=text.replace('/var/run/aw1000-mwan-follow/selected',str(run/'selected'))
            target=os.getenv('AW1000_TEST_TARGET_ROOT')
            cmd=['qemu-aarch64-static','-L',target,target+'/bin/busybox','ash'] if target else ['bash']
            p=subprocess.run(cmd,input=text+harness,text=True,capture_output=True,
                env={**os.environ,'TEST_RUN':d},timeout=15)
            self.assertEqual(p.returncode,0,p.stderr)
            events=(run/'events').read_text()
            self.assertEqual(events.count('ACTION=connected '),1,events)
            self.assertNotIn('ACTION=disconnected ',events)
            self.assertEqual((run/'wan/STATUS').read_text().strip(),'online')
            self.assertEqual((run/'wan/SCORE').read_text().strip(),'6')
            self.assertFalse((run/'wan/AW_OUTAGE').exists())

    def test_native_tracker_sends_no_probes_in_off_mode_and_revalidates_standby(self):
        from internet import patch_tracker_runtime
        lock=json.loads((ROOT/'sources.lock.json').read_text())['mwan3_legacy']
        with tarfile.open(ROOT/lock['archive'],'r:xz') as z:
            text=patch_tracker_runtime(z.extractfile('files/usr/sbin/mwan3track').read().decode())
        text='\n'.join(line for line in text.splitlines() if not line.startswith('. /lib/'))
        text=text.replace('main "$@"','').replace('while true; do','while [ ! -f "$TEST_RUN/stop" ]; do')
        harness=r'''
MWAN3TRACK_STATUS_DIR="$TEST_RUN"; MAX_SLEEP=100
uci() { case "$*" in *aw1000.internet.priority) echo "$PREF";; *aw1000_cellular) echo cellular;; esac; }
mwan3_init() { :; }
config_get() {
    case "$3" in
      family) value=ipv4;; track_method) value=ping;; count|reliability) value=1;;
      timeout) value=2;; interval|down) value=2;; up) value=4;; *) value=$4;;
    esac
    eval "$1=\"\$value\""
}
config_get_bool() { eval "$1=\"\$4\""; }
validate_track_method() { PING=ping; }
config_list_foreach() { track_ips='223.5.5.5 119.29.29.29'; }
firstconnect() { STARTED=1; STATUS=online; score=6; echo online > "$TEST_RUN/cellular/STATUS"; }
get_uptime() { cat "$TEST_RUN/clock"; }
LOG() { :; }
env() {
    echo "$(get_uptime):$*" >> "$TEST_RUN/events"
    case "$*" in *ACTION=connected*) echo cellular > "$TEST_RUN/selected";; esac
}
WRAP() { get_uptime >> "$TEST_RUN/pings"; return 0; }
sleep() {
    t=$(cat "$TEST_RUN/clock"); t=$((t+1)); echo "$t" > "$TEST_RUN/clock"
    if [ "$SCENARIO" = wake ] && [ "$t" = 5 ]; then
        echo offline > "$TEST_RUN/wan/STATUS"; echo 5 > "$TEST_RUN/wan/AW_OUTAGE"
    fi
    [ "$t" -lt 65 ] || touch "$TEST_RUN/stop"
}
main cellular
'''
        for scenario,pref,expected in [('standby','wired',2),('off','wired_only',0),('wake','wired',None)]:
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as d:
                root=Path(d);(root/'wan').mkdir();(root/'cellular').mkdir()
                (root/'wan/STATUS').write_text('online\n');(root/'selected').write_text('wan\n');(root/'clock').write_text('0\n')
                script=text.replace('/var/run/aw1000-mwan-follow/selected',str(root/'selected'))+harness
                target=os.getenv('AW1000_TEST_TARGET_ROOT')
                cmd=['qemu-aarch64-static','-L',target,target+'/bin/busybox','ash'] if target else ['bash']
                p=subprocess.run(cmd,input=script,text=True,capture_output=True,
                    env={**os.environ,'TEST_RUN':d,'PREF':pref,'SCENARIO':scenario},timeout=20)
                self.assertEqual(p.returncode,0,p.stderr)
                times=(root/'pings').read_text().splitlines() if (root/'pings').exists() else []
                if expected is not None:self.assertEqual(len(times),expected,p.stderr)
                else:
                    self.assertEqual(times[:5],['0','5','7','9','11'])
                    self.assertRegex((root/'events').read_text(),r'11:.*ACTION=connected')
                    self.assertEqual((root/'cellular/AW_VERIFIED_OUTAGE').read_text(),'5\n')

    def test_roles_backoff_single_modes_and_event_wakeup(self):
        from internet import TRACKER_RUNTIME
        with tempfile.TemporaryDirectory() as d:
            run=Path(d); (run/'wan').mkdir();(run/'cellular').mkdir()
            (run/'wan/STATUS').write_text('online\n')
            (run/'selected').write_text('wan\n')
            lib=TRACKER_RUNTIME.replace('/var/run/aw1000-mwan-follow/selected',str(run/'selected'))
            harness=r'''
uci() { case "$*" in *aw1000.internet.priority) echo "$PREF";; *aw1000_cellular) echo cellular;; *) exit 92;; esac; }
INTERFACE=cellular; MWAN3TRACK_STATUS_DIR="$TEST_RUN"
interval=2; score=6; up=4; STATUS=online; aw_failures=0
PREF=wired; aw_role; echo "$AW_ROLE:$AW_INTERVAL"
aw_failures=2; aw_role; echo "$AW_ROLE:$AW_INTERVAL"
aw_failures=3; aw_role; echo "$AW_ROLE:$AW_INTERVAL"
PREF=wired_only; aw_role; echo "$AW_ROLE:$AW_INTERVAL"
PREF=cellular_only; aw_role; echo "$AW_ROLE:$AW_INTERVAL"
PREF=cellular; aw_role; echo "$AW_ROLE:$AW_INTERVAL"
echo cellular > "$TEST_RUN/selected"; aw_role; echo "$AW_ROLE:$AW_INTERVAL"
INTERFACE=wan; PREF=cellular_only; aw_role; echo "$AW_ROLE:$AW_INTERVAL"
INTERFACE=cellular; PREF=wired; echo wan > "$TEST_RUN/selected"
aw_role; sleep_time=300; IFDOWN_EVENT=0; IFUP_EVENT=0
sleep() { echo offline > "$TEST_RUN/wan/STATUS"; }
disconnected() { echo withdrawn; score=0; STATUS=offline; }
aw_wait
aw_role; echo "$AW_ROLE:$AW_INTERVAL"
score=1; PREF=cellular; aw_role; echo "$AW_ROLE:$AW_INTERVAL"
'''
            p=subprocess.run(['bash'],input=lib+harness,text=True,capture_output=True,
                env={**os.environ,'TEST_RUN':d},timeout=10)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(p.stdout.splitlines(),['standby:60','standby:120','standby:300',
                'off:0','active:30','recovery:30','active:10','off:0','withdrawn','recovery:2','recovery:2'])

    def test_vendor_tracker_patch_applies_and_passes_target_shell(self):
        from internet import patch_tracker_runtime
        lock=json.loads((ROOT/'sources.lock.json').read_text())['mwan3_legacy']
        with tarfile.open(ROOT/lock['archive'],'r:xz') as z:
            text=patch_tracker_runtime(z.extractfile('files/usr/sbin/mwan3track').read().decode())
        p=subprocess.run(['bash','-n'],input=text,text=True,capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertNotIn('uci commit',text)
        self.assertIn('AW_VERIFIED_OUTAGE',text)
        self.assertIn('size=8; count=1; timeout=2',text)
        target=os.getenv('AW1000_TEST_TARGET_ROOT')
        if target:
            p=subprocess.run(['qemu-aarch64-static','-L',target,target+'/bin/busybox','ash','-n'],
                input=text,text=True,capture_output=True)
            self.assertEqual(p.returncode,0,p.stderr)

    def test_original_qwrt_uci_loads_runtime_copy_and_rejects_stale_copy(self):
        from internet import RUNTIME_UCI_C
        target=os.getenv('AW1000_TEST_TARGET_ROOT')
        if not target or not shutil.which('qemu-aarch64-static'):
            self.skipTest('QWRT runtime and QEMU required')
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); persist=base/'persist';cfg=base/'config'
            persist.mkdir();cfg.mkdir()
            p=persist/'network'; p.write_text("config interface 'lan'\n option ip6class 'persistent'\n")
            (cfg/'network').write_text("config interface 'lan'\n option ip6class 'runtime'\n")
            (cfg/'network.base').write_bytes(p.read_bytes())
            src=base/'hook.c';lib=base/'hook.so'
            src.write_text(RUNTIME_UCI_C.replace('/var/run/aw1000-ipv6-follow/config',str(cfg)).replace('/etc/config',str(persist)))
            subprocess.run(['aarch64-linux-gnu-gcc','-shared','-fPIC','-nostdlib','-fno-stack-protector',
                '-fno-builtin','-U_FORTIFY_SOURCE','-o',str(lib),str(src)],check=True)
            # Lua's explicit load calls the public libuci entry point, as do
            # netifd and odhcpd. The CLI's internal lookup may bind locally.
            lua=('package.cpath='+json.dumps(target+'/usr/lib/lua/?.so')+';'
                 'local c=require("uci").cursor('+json.dumps(str(persist))+');'
                 'assert(c:load("network"));print(c:get("network","lan","ip6class"))')
            # Rename a copy of the target Lua executable to exercise the same
            # executable-name guard as the daemons, without running a daemon.
            executable=base/'netifd';shutil.copyfile(target+'/usr/bin/lua',executable);executable.chmod(0o755)
            cmd=['qemu-aarch64-static','-L',target,'-E','LD_PRELOAD='+str(lib)+':'+target+'/lib/libuci.so',str(executable),'-e',lua]
            result=subprocess.run(cmd,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),'runtime')
            normal=cmd.copy();normal[normal.index(str(executable))]=target+'/usr/bin/lua'
            ordinary=subprocess.run(normal,text=True,capture_output=True)
            self.assertEqual(ordinary.stdout.strip(),'persistent',ordinary.stderr)
            p.write_text("config interface 'lan'\n option ip6class 'changed-persistent'\n")
            result=subprocess.run(cmd,text=True,capture_output=True)
            self.assertEqual(result.stdout.strip(),'changed-persistent',result.stderr)
            (cfg/'network').unlink()
            result=subprocess.run(cmd,text=True,capture_output=True)
            self.assertEqual(result.stdout.strip(),'changed-persistent',result.stderr)


if __name__ == '__main__':
    unittest.main()


class HelperIsolationTests(unittest.TestCase):
    def test_timeout_terminates_child_group_and_ipv6_filter_requires_prefix(self):
        import time
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);binary=root/'flow'
            subprocess.run(['gcc','-Os','-Wall','-Wextra','-Werror','-o',str(binary),str(ROOT/'network/flow-flush.c')],check=True)
            subprocess.run([str(binary),'--selftest'],check=True)
            subprocess.run([str(binary),'--filter-selftest'],check=True)
            self.assertEqual(subprocess.run([str(binary),'ipv6']).returncode,2)
            self.assertEqual(subprocess.run([str(binary),'ipv6','::/0']).returncode,2)
            timeout=root/'timeout';timeout.symlink_to(binary)
            clock=time.monotonic()
            p=subprocess.run([str(timeout),'1','sh','-c','sleep 30 & wait'],capture_output=True,timeout=5)
            self.assertEqual(p.returncode,124)
            self.assertLess(time.monotonic()-clock,5)
            self.assertEqual(subprocess.run([str(timeout),'2','sh','-c','exit 7']).returncode,7)
