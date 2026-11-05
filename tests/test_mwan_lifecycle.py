"""Exercise the shipped reload coordinator and firewall callback together."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MwanLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        self.bin = self.path/'bin'; self.bin.mkdir()
        self.run = self.path/'run'; self.run.mkdir()
        self.events = self.path/'events'
        self.env = {**os.environ, 'TEST_ROOT':str(self.path), 'TEST_RUN':str(self.run),
                    'PATH':str(self.bin)+os.pathsep+os.environ['PATH'],
                    'AW1000_INTERNET_RUN':str(self.run),
                    'AW1000_PENDING':str(self.path/'pending')}
        self.executable('uci', '''#!/bin/sh
case "$*" in *mwan3.globals.aw1000_enabled*) echo 1;; esac
''')
        self.executable('ubus', '''#!/bin/sh
echo "ubus $*" >> "$TEST_ROOT/events"
''')
        hook = (ROOT/'files/etc/hotplug.d/firewall/99-aw1000-follow').read_text()
        self.executable('firewall-hook',hook.replace('/var/run/aw1000-internet',str(self.run)))
        self.executable('firewall', '''#!/bin/sh
echo "firewall $*" >> "$TEST_ROOT/events"
sh "$TEST_ROOT/bin/firewall-hook"
''')
        self.executable('mwan-init', '''#!/bin/sh
case "$1" in
 running) [ -f "$TEST_ROOT/mwan-running" ]; exit $?;;
 start|restart)
  echo "mwan $1" >> "$TEST_ROOT/events"
  if [ -f "$TEST_ROOT/fail-mwan" ]; then exit 45; fi
  touch "$TEST_ROOT/mwan-running"
  if [ -f "$TEST_ROOT/late-firewall" ]; then
   rm -f "$TEST_ROOT/late-firewall"
   sh "$TEST_ROOT/bin/firewall-hook"
  fi
 ;;
esac
''')
        self.executable('uplink','#!/bin/sh\necho uplink >> "$TEST_ROOT/events"\n')
        self.executable('follow','#!/bin/sh\necho "follow $*" >> "$TEST_ROOT/events"\n')
        script = (ROOT/'files/usr/sbin/aw1000-internet').read_text()
        script = script.replace('/etc/init.d/firewall',str(self.bin/'firewall'))
        script = script.replace('/etc/init.d/mwan3',str(self.bin/'mwan-init'))
        script = script.replace('/usr/sbin/aw1000-uplink',str(self.bin/'uplink'))
        script = script.replace('/usr/sbin/aw1000-mwan-follow',str(self.bin/'follow'))
        self.script = self.executable('internet',script)

    def executable(self,name,text):
        p=self.bin/name;p.write_text(text);p.chmod(0o755);return p

    def invoke(self,expected=0):
        r=subprocess.run(['sh',str(self.script),'--reload-pending'],env=self.env,
                         capture_output=True,text=True,timeout=5)
        self.assertEqual(r.returncode,expected,r.stderr)

    def test_full_reload_merges_firewall_callback_into_one_mwan_restart(self):
        (self.path/'mwan-running').touch()
        (self.run/'reload-needed').touch()
        self.invoke()
        self.assertEqual(self.events.read_text().splitlines(),
                         ['ubus call network reload {}','firewall reload','mwan restart'])
        self.assertFalse((self.run/'reload-needed').exists())
        self.assertFalse((self.run/'mwan-reload-needed').exists())
        self.invoke()
        self.assertEqual(self.events.read_text().count('mwan restart'),1)

    def test_external_firewall_callback_queues_instead_of_entering_init(self):
        subprocess.run(['sh',str(self.bin/'firewall-hook')],env=self.env,check=True,timeout=5)
        self.assertFalse(self.events.exists())
        self.assertTrue((self.run/'mwan-reload-needed').exists())
        self.invoke()
        self.assertEqual(self.events.read_text().splitlines(),['mwan start'])

    def test_failure_retries_only_mwan_and_preserves_late_firewall_request(self):
        (self.run/'reload-needed').touch()
        (self.path/'fail-mwan').touch()
        self.invoke(expected=1)
        self.assertFalse((self.run/'reload-needed').exists())
        self.assertTrue((self.run/'mwan-reload-needed').exists())
        (self.path/'fail-mwan').unlink()
        (self.path/'late-firewall').touch()
        self.invoke()
        self.assertTrue((self.run/'mwan-reload-needed').exists())
        self.invoke()
        self.assertFalse((self.run/'mwan-reload-needed').exists())
        events=self.events.read_text()
        self.assertEqual(events.count('ubus call network reload {}'),1)
        self.assertEqual(events.count('firewall reload'),1)
        self.assertEqual(events.count('mwan start'),2)
        self.assertEqual(events.count('mwan restart'),1)

    def test_manager_start_queues_missing_mwan_without_running_init_itself(self):
        init=(ROOT/'files/etc/init.d/aw1000-internet').read_text()
        init=init.replace('/var/run/aw1000-internet',str(self.run))
        init=init.replace('/etc/init.d/mwan3',str(self.bin/'mwan-init'))
        init=init.replace('/usr/sbin/aw1000-network-cleanup','true')
        harness='''procd_open_instance() { :; }
procd_set_param() { :; }
procd_close_instance() { :; }
'''+init+'\nstart_service\n'
        p=self.path/'init-fixture.sh';p.write_text(harness)
        r=subprocess.run(['sh',str(p)],env=self.env,capture_output=True,text=True,timeout=5)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertTrue((self.run/'mwan-reload-needed').exists())
        self.assertFalse(self.events.exists())
        self.invoke()
        self.assertEqual(self.events.read_text().splitlines(),['mwan start'])

    def test_manager_start_does_not_queue_rebuild_for_running_mwan(self):
        (self.path/'mwan-running').touch()
        init=(ROOT/'files/etc/init.d/aw1000-internet').read_text()
        init=init.replace('/var/run/aw1000-internet',str(self.run))
        init=init.replace('/etc/init.d/mwan3',str(self.bin/'mwan-init'))
        init=init.replace('/usr/sbin/aw1000-network-cleanup','true')
        harness='''procd_open_instance() { :; }
procd_set_param() { :; }
procd_close_instance() { :; }
'''+init+'\nstart_service\n'
        p=self.path/'init-fixture.sh';p.write_text(harness)
        r=subprocess.run(['sh',str(p)],env=self.env,capture_output=True,text=True,timeout=5)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertFalse((self.run/'mwan-reload-needed').exists())
        self.assertFalse(self.events.exists())

    def test_stopping_guard_preserves_requests_without_starting_mwan(self):
        (self.run/'reload-needed').touch()
        (self.run/'mwan-reload-needed').touch()
        (self.run/'stopping').touch()
        self.invoke()
        self.assertFalse(self.events.exists())
        self.assertTrue((self.run/'reload-needed').exists())
        self.assertTrue((self.run/'mwan-reload-needed').exists())

    def test_stop_drains_coordinator_but_does_not_hold_lock_during_service_calls(self):
        import fcntl
        import time
        self.executable('stop-check', '''#!/bin/sh
exec 9>"$TEST_RUN/reconcile.lock"
flock -n 9 || exit 77
echo "stopped $*" >> "$TEST_ROOT/events"
''')
        init=(ROOT/'files/etc/init.d/aw1000-internet').read_text()
        init=init.replace('/var/run/aw1000-internet',str(self.run))
        init=init.replace('/etc/init.d/mwan3',str(self.bin/'stop-check'))
        init=init.replace('/etc/init.d/firewall',str(self.bin/'stop-check'))
        init=init.replace('/usr/sbin/aw1000-mwan-follow',str(self.bin/'stop-check'))
        p=self.path/'stop-fixture.sh';p.write_text(init+'\nstop_service\n')
        with (self.run/'reconcile.lock').open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            proc=subprocess.Popen(['sh',str(p)],env=self.env,stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,text=True)
            deadline=time.monotonic()+2
            while not (self.run/'stopping').exists() and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertTrue((self.run/'stopping').exists())
            self.assertFalse(self.events.exists())
            fcntl.flock(lock,fcntl.LOCK_UN)
            _,stderr=proc.communicate(timeout=5)
        self.assertEqual(proc.returncode,0,stderr)
        self.assertEqual(self.events.read_text().splitlines(),
                         ['stopped stop','stopped --restore','stopped reload'])

    def test_stop_waits_for_old_whole_round_and_new_round_does_not_run(self):
        import time
        os.mkfifo(self.path/'release-uplink')
        self.executable('uplink','''#!/bin/sh
echo uplink-entered >> "$TEST_ROOT/events"
touch "$TEST_ROOT/uplink-entered"
read -r release < "$TEST_ROOT/release-uplink"
echo uplink-left >> "$TEST_ROOT/events"
''')
        self.executable('stop-check','''#!/bin/sh
exec 9>"$TEST_RUN/reconcile.lock"
flock -n 9 || exit 77
echo "stopped $*" >> "$TEST_ROOT/events"
''')
        init=(ROOT/'files/etc/init.d/aw1000-internet').read_text()
        init=init.replace('/var/run/aw1000-internet',str(self.run))
        for src in ('/etc/init.d/mwan3','/etc/init.d/firewall','/usr/sbin/aw1000-mwan-follow'):
            init=init.replace(src,str(self.bin/'stop-check'))
        p=self.path/'stop-fixture.sh';p.write_text(init+'\nstop_service\n')
        old=subprocess.Popen(['sh',str(self.script),'--reconcile-once'],env=self.env,
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        deadline=time.monotonic()+2
        while not (self.path/'uplink-entered').exists() and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertTrue((self.path/'uplink-entered').exists())
        stop=subprocess.Popen(['sh',str(p)],env=self.env,stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,text=True)
        deadline=time.monotonic()+2
        while not (self.run/'stopping').exists() and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertTrue((self.run/'stopping').exists())
        new=subprocess.run(['sh',str(self.script),'--reconcile-once'],env=self.env,
                           capture_output=True,text=True,timeout=5)
        self.assertEqual(new.returncode,0,new.stderr)
        self.assertEqual(self.events.read_text().splitlines(),['uplink-entered'])
        with (self.path/'release-uplink').open('w') as release:
            release.write('continue\n')
        _,old_err=old.communicate(timeout=5)
        _,stop_err=stop.communicate(timeout=5)
        self.assertEqual(old.returncode,0,old_err)
        self.assertEqual(stop.returncode,0,stop_err)
        expected=['uplink-entered','uplink-left','follow --once',
                  'stopped stop','stopped --restore','stopped reload']
        self.assertEqual(self.events.read_text().splitlines(),expected)
        new=subprocess.run(['sh',str(self.script),'--reconcile-once'],env=self.env,
                           capture_output=True,text=True,timeout=5)
        self.assertEqual(new.returncode,0,new.stderr)
        self.assertEqual(self.events.read_text().splitlines(),expected)


if __name__=='__main__':
    unittest.main()
