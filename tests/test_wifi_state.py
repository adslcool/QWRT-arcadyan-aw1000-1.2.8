"""Exercise the shipped panel against native and legacy QCA wireless states."""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def lua_value(value):
    if value is None:
        return 'nil'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return '{' + ','.join(lua_value(v) for v in value) + '}'
    if isinstance(value, dict):
        return '{' + ','.join('[' + lua_value(k) + ']=' + lua_value(v)
                              for k, v in value.items()) + '}'
    raise TypeError(type(value))


HARNESS = r'''
local writes, nodes, captured, iwinfo_calls = {}, {}, nil, 0
for i=0,12 do
 nodes['ipq::led'..i]={max_brightness='1',brightness='1',trigger='[none] timer'}
end
nodes['ipq::led3'].brightness=INITIAL
nodes['ipq::led3'].trigger='[timer] none'
local function file(value, path, onwrite)
 return {read=function() return value end,close=function() end,
  write=function(_,v)
   writes[path]=(writes[path] or 0)+1
   if onwrite then onwrite(tostring(v)) end
   return true
  end}
end
io.open=function(path, mode)
 local name, attribute=path:match('^/sys/class/leds/([^/]+)/([^/]+)$')
 if name and nodes[name] then
  if mode=='w' then
   return file('',path,function(value) nodes[name][attribute]=value end)
  end
  return file(nodes[name][attribute] or '',path)
 end
 if mode=='w' then return file('',path) end
 if EVENTS[path] then return file(EVENTS[path],path) end
 if path=='/tmp/sysinfo/board_name' then return file('arcadyan,aw1000',path) end
 if path=='/var/run/aw1000-wifi-off' and OFF_MARKER then return file('',path) end
 local device=path:match('^/sys/class/net/([^/]+)/flags$')
 if device then
  assert(not device:match('^wifi%d+$') and not device:match('^radio%d+$'),
         'physical QCA radios must not be treated as AP interfaces')
  if FLAGS[device]~=nil then return file(FLAGS[device],path) end
  return nil
 end
 assert(not path:find('/sys/class/net/',1,true),'unsafe wireless sysfs path')
 return nil
end
io.popen=function() error('wireless detection must not execute a command') end
os.rename=function() return true end
package.preload['uci']=function() return {cursor=function() return {
 unload=function() end,load=function() end,get=function() return '0' end,
 foreach=function() end
} end} end
package.preload['ubus']=function() return {connect=function() return {
 call=function(_, object, method, args)
  if object=='network.wireless' then
   assert(method=='status')
   if NATIVE_ERROR then error('native wireless unavailable') end
   return NATIVE
  end
  assert(object=='iwinfo','unexpected ubus object '..object)
  iwinfo_calls=iwinfo_calls+1
  if method=='devices' then
   if DEVICES_ERROR then error('iwinfo devices unavailable') end
   return {devices=DEVICES}
  end
  assert(method=='info' and args and type(args.device)=='string')
  assert(not args.device:match('^wifi%d+$') and not args.device:match('^radio%d+$'))
  if INFO_ERRORS[args.device] then error('iwinfo info unavailable') end
  return INFOS[args.device] or {}
 end,
 close=function() end
} end} end
package.preload['luci.jsonc']=function() return {
 stringify=function(state) captured=state;return '{}' end
} end
package.preload['nixio']=function() return {nanosleep=function()
 assert(captured,'panel tick did not finish')
 assert(captured.wifi==EXPECTED,'unexpected Wi-Fi state')
 assert(captured.wifi_source==SOURCE,'unexpected Wi-Fi state source')
 local path='/sys/class/leds/ipq::led3/brightness'
 if EXPECTED==nil then
  assert(captured.wifi_state=='unknown')
  assert(not writes[path],'unknown state must preserve the prior Wi-Fi lamp')
  assert(not writes['/sys/class/leds/ipq::led3/trigger'])
  assert(nodes['ipq::led3'].brightness==INITIAL)
  assert(nodes['ipq::led3'].trigger=='[timer] none')
 else
  assert(captured.wifi_state==(EXPECTED and 'up' or 'down'))
  assert(writes[path]==1)
  assert(nodes['ipq::led3'].brightness==(EXPECTED and '0' or '1'))
  assert(nodes['ipq::led3'].trigger=='none')
 end
 if EXPECT_IWINFO~=nil then
  assert((iwinfo_calls>0)==EXPECT_IWINFO,'unexpected iwinfo fallback')
 end
 assert(not writes['/sys/class/leds/ipq::led12/brightness'],'phone remains owned by fxsd')
 os.exit(0)
end} end
'''


class WifiStateTests(unittest.TestCase):
    def run_panel(self, expected, source, *, native=None, devices=None,
                  flags=None, infos=None, native_error=False,
                  devices_error=False, info_errors=None, initial='1',
                  off_marker=False, expect_iwinfo=True, events=None):
        lua = shutil.which('lua5.1') or shutil.which('lua')
        self.assertIsNotNone(lua, 'Lua is required to execute the shipped panel')
        values = dict(EXPECTED=expected, SOURCE=source, EVENTS=events or {},
                      NATIVE={} if native is None else native,
                      DEVICES=devices, FLAGS=flags or {}, INFOS=infos or {},
                      NATIVE_ERROR=native_error, DEVICES_ERROR=devices_error,
                      INFO_ERRORS=info_errors or {}, INITIAL=initial,
                      OFF_MARKER=off_marker, EXPECT_IWINFO=expect_iwinfo)
        prefix = ''.join(k + '=' + lua_value(v) + '\n' for k, v in values.items())
        with tempfile.TemporaryDirectory() as folder:
            fixture = Path(folder) / 'wifi.lua'
            fixture.write_text(prefix + HARNESS + '\ndofile(' +
                               json.dumps(str(ROOT / 'files/usr/sbin/aw1000-leds.lua')) + ')\n')
            result = subprocess.run([lua, str(fixture)], capture_output=True,
                                    text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_native_up_and_confirmed_down_remain_authoritative(self):
        scenarios = [({'radio0': {'up': True}}, True),
                     ({'radio0': {'up': False}}, False),
                     ({'radio0': {'up': True, 'disabled': True},
                       'radio1': {'up': False}}, False)]
        for native, expected in scenarios:
            with self.subTest(native=native):
                self.run_panel(expected, 'netifd', native=native,
                               devices=['ath0'], flags={'ath0': '0x1003'},
                               infos={'ath0': {'mode': 'Master', 'channel': 36}},
                               expect_iwinfo=False)

    def test_legacy_qca_ap_is_detected_despite_empty_native_state(self):
        for mode in ('Master', 'AP', 'Master(VLAN)'):
            with self.subTest(mode=mode):
                self.run_panel(True, 'iwinfo', devices=['wifi1', 'ath0', 'wifi0', 'radio0'],
                               flags={'ath0': '0x1003'},
                               infos={'ath0': {'mode': mode, 'channel': 6}},
                               off_marker=True)

    def test_failed_or_partial_native_query_can_fall_back_to_ap(self):
        for native, failed in [({}, True), ({'radio0': {'up': False}, 'bad': 3}, False)]:
            with self.subTest(native=native, failed=failed):
                self.run_panel(True, 'iwinfo', native=native, native_error=failed,
                               devices=['ath1'], flags={'ath1': '1'},
                               infos={'ath1': {'mode': 'Master', 'channel': 149}})

    def test_down_interfaces_physical_radios_and_client_links_are_not_active_aps(self):
        scenarios = [([], {}, {}),
                     (['wifi0', 'radio1'], {}, {}),
                     (['ath0'], {'ath0': '0x1002'}, {}),
                     (['ath0'], {'ath0': '1'}, {'ath0': {'mode': 'Client', 'channel': 36}}),
                     (['ath0'], {'ath0': '1'}, {'ath0': {'mode': 'Monitor', 'channel': 36}})]
        for devices, flags, infos in scenarios:
            with self.subTest(devices=devices, flags=flags, infos=infos):
                self.run_panel(False, 'iwinfo', devices=devices, flags=flags, infos=infos)

    def test_unavailable_or_malformed_device_queries_preserve_lamp(self):
        scenarios = [('iwinfo-unavailable', None, False),
                     ('iwinfo-unavailable', 'ath0', False),
                     ('iwinfo-unavailable', ['ath0'], True),
                     ('iwinfo-malformed', {'ath0': True}, False),
                     ('iwinfo-malformed', {1: 'ath0', 3: 'ath1'}, False)]
        for source, devices, failed in scenarios:
            for initial in ('0', '1'):
                with self.subTest(source=source, devices=devices, failed=failed, initial=initial):
                    self.run_panel(None, source, devices=devices,
                                   devices_error=failed, initial=initial)

    def test_incomplete_interface_evidence_preserves_lamp(self):
        scenarios = [(['ath0'], {}, {}, {}),
                     (['ath0'], {'ath0': 'not-a-number'}, {}, {}),
                     (['ath0'], {'ath0': '1'}, {}, {'ath0': True}),
                     (['ath0'], {'ath0': '1'}, {'ath0': {'mode': 'unknown'}}, {}),
                     (['ath0'], {'ath0': '1'}, {'ath0': {'mode': 'Master', 'channel': 0}}, {}),
                     (['../ath0'], {}, {}, {}),
                     (['.'], {}, {}, {}),
                     ([7], {}, {}, {})]
        for devices, flags, infos, errors in scenarios:
            with self.subTest(devices=devices, flags=flags, infos=infos):
                self.run_panel(None, 'iwinfo-incomplete', devices=devices,
                               flags=flags, infos=infos, info_errors=errors)



    def test_driver_vap_down_overrides_up_flags_but_old_ifindex_does_not(self):
        flags={'ath0':'0x1343','ath1':'0x1343'}
        infos={name:{'mode':'Master','channel':1} for name in flags}
        events={'/var/run/aw1000-wifi/events/ath0':'state=down\nifindex=26\ntime=306\n',
                '/var/run/aw1000-wifi/events/ath1':'state=down\nifindex=27\ntime=323\n',
                '/sys/class/net/ath0/ifindex':'26','/sys/class/net/ath1/ifindex':'27'}
        self.run_panel(False,'qca-vap-down',native={'wifi0':{'up':True}},devices=['ath0','ath1'],flags=flags,infos=infos,events=events)
        events['/sys/class/net/ath0/ifindex']='40'
        self.run_panel(True,'iwinfo',devices=['ath0','ath1'],flags=flags,infos=infos,events=events)

if __name__ == '__main__':
    unittest.main()
