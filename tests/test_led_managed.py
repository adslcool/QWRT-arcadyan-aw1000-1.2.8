"""Execute the panel program with a renamed cellular interface and real states."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ManagedLedTests(unittest.TestCase):
    def test_only_wired_keeps_real_signal_for_minutes_without_data_or_probes(self):
        lua=shutil.which('lua5.1') or shutil.which('lua')
        harness=r'''local writes,captured,stage={},nil,1
local phases={
 {now=100,rat='sa',db=-87,health='disabled',mode='wired_only',color='green'},
 {now=116,rat='sa',db=-87,health='disabled',mode='wired_only',color='green'},
 {now=180,rat='sa',db=-103,health='disabled',mode='wired_only',color='blue'},
 {now=241,rat='sa',db=-117,health='disabled',mode='wired_only',color='red'},
 {now=310,health='disabled',mode='wired_only',color='red'},
 {now=400,rat='lte',db=-90,health='connecting',mode='wired',color='green'},
 {now=500,rat='wcdma',db=-96,health='offline',mode='cellular',color='blue'}
}
local function file(value,path)
 return {read=function()return tostring(value)end,close=function()end,
 write=function(_,v)writes[path]=tostring(v);return true end}
end
io.open=function(path,mode)
 if mode=='w' then return file('',path) end
 if path=='/tmp/sysinfo/board_name' then return file('arcadyan,aw1000') end
 if path=='/proc/uptime' then return file(phases[stage].now..' 0') end
 if path=='/var/run/aw1000-mwan-follow/selected' then return file('wan') end
 if path=='/var/run/mwan3track/livecell/STATUS' then return file(phases[stage].health) end
 if path=='/var/run/mwan3track/wan/STATUS' then return file('online') end
 if path=='/var/run/aw1000-internet/blocked' then return file('') end
 if path:match('^/sys/class/leds/ipq::led%d+/max_brightness$') then return file('1') end
end
io.popen=function(cmd)
 if cmd:find('--sim-state',1,true) then return file('ready') end
 assert(cmd:find('--radio-state',1,true),'no extra AT queries or network probes')
 return file('{}')
end
os.rename=function()return true end
package.preload.uci=function()return {cursor=function()return {
 unload=function()end,load=function()end,
 get=function(_,c,_,k)
  if c=='mwan3' and k=='aw1000_enabled' then return '1' end
  if c=='mwan3' and k=='aw1000_cellular' then return 'livecell' end
  if c=='aw1000' and k=='priority' then return phases[stage].mode end
 end,
 foreach=function(_,_,_,fn)fn({name='rg500q-ea',at_port='/dev/ttyUSB3'})end
}end}end
package.preload.ubus=function()return {connect=function()return {
 call=function(_,obj)
  if obj=='network.interface.livecell' then return {up=false,['ipv4-address']={}} end
  if obj=='network.interface.wan' then return {up=true,['ipv4-address']={{address='192.0.2.2'}}} end
  if obj=='network.wireless' then return {radio0={up=true}} end
  error(obj)
 end,close=function()end
}end}end
package.preload['luci.jsonc']=function()return {
 parse=function()return {rat=phases[stage].rat,dbm=phases[stage].db}end,
 stringify=function(v)captured=v;return '{}' end
}end
package.preload.nixio=function()return {nanosleep=function()
 assert(captured,'tick failed')
 assert(captured.signal_fault=='' and not captured.signal_fault_expired,'disabled/failed data tracker is not radio loss')
 assert(captured.cellular_online==false and captured.internet==true)
 assert(writes['/sys/class/leds/ipq::led4/brightness']=='0')
 local wanted={red='6',green='2',blue='1'}
 for color,node in pairs(wanted) do
  local path='/sys/class/leds/ipq::led'..node..'/'
  assert(writes[path..'trigger']=='none','Mobile Signal must stay steady')
  assert(writes[path..'brightness']==(color==phases[stage].color and '1' or '0'))
 end
 assert(writes['/sys/class/leds/ipq::led12/brightness']==nil)
 if stage==#phases then os.exit(0) end
 stage=stage+1
end}end
'''
        with tempfile.TemporaryDirectory() as d:
            fixture=Path(d)/'led.lua'
            fixture.write_text(harness+'\ndofile('+json.dumps(str(ROOT/'files/usr/sbin/aw1000-leds.lua'))+')\n')
            result=subprocess.run([lua,str(fixture)],text=True,capture_output=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_signal_ignores_probe_transitions_and_internet_follows_selected_egress(self):
        lua = shutil.which('lua5.1') or shutil.which('lua')
        self.assertIsNotNone(lua)
        scenarios = [
            ('online', 'ready', True, False, 'livecell', 'online', True, True, False),
            ('disconnecting', 'ready', True, False, 'livecell', 'disconnecting', True, True, False),
            ('offline', 'ready', True, False, 'livecell', 'offline', True, False, False),
            ('connecting', 'ready', True, False, 'livecell', 'connecting', True, False, False),
            ('offline', 'ready', True, False, 'wan', 'online', True, True, False),
            ('offline', 'ready', True, False, 'wan', 'connecting', True, False, False),
            ('online', 'absent', True, False, 'wan', 'online', True, True, True),
            ('online', 'ready', True, True, 'livecell', 'online', True, False, False),
        ]
        for health, sim, up, blocked, selected, selected_health, selected_up, expected, red_blink in scenarios:
            with self.subTest(health=health, sim=sim, selected=selected, selected_health=selected_health):
                harness = '''local writes,loads={},{};local captured
local function fakefile(value,path)
 return {read=function() return value end,close=function() end,
 write=function(_,v) writes[path]=tostring(v);return true end}
end
io.open=function(path,mode)
 if mode=='w' then return fakefile('',path) end
 if path=='/tmp/sysinfo/board_name' then return fakefile('arcadyan,aw1000') end
 if path=='/var/run/aw1000-mwan-follow/selected' then return fakefile(SELECTED) end
 if path=='/var/run/mwan3track/livecell/STATUS' then return fakefile(HEALTH) end
 if path=='/var/run/mwan3track/wan/STATUS' then return fakefile(SELECTED_HEALTH) end
 if path=='/var/run/mwan3track/stalealias/STATUS' then error('stale tracker alias') end
 if path=='/var/run/aw1000-internet/blocked' then return fakefile(BLOCKED and 'livecell' or '') end
 local n=path:match('^/sys/class/leds/ipq::led(%d+)/max_brightness$')
 if n and tonumber(n)<=12 then return fakefile('1') end
end
io.popen=function(cmd)
 if cmd:find('--sim-state',1,true) then return fakefile(SIM) end
 assert(cmd:find('--radio-state',1,true),'managed mode must reuse tracker')
 return fakefile('{}')
end
os.rename=function() return true end
package.preload['uci']=function() return {cursor=function() return {
 unload=function(_,c) loads[c]=true end,load=function() end,
 get=function(_,c,s,k)
  if c=='mwan3' and k=='aw1000_enabled' then return '1' end
  if c=='mwan3' and k=='aw1000_cellular' then return 'livecell' end
 end,
 foreach=function(_,_,_,fn)
 fn({name='rg500q-ea',at_port='/dev/ttyUSB3',alias='stalealias'}) end} end} end
package.preload['ubus']=function() return {connect=function() return {
 call=function(_,obj)
  if obj=='network.interface.livecell' then return {up=UP,l3_device='wwan0_1',['ipv4-address']={{address='10.0.0.2'}}} end
  if obj=='network.interface.wan' then return {up=SELECTED_UP,l3_device='wan',['ipv4-address']={{address='192.0.2.2'}}} end
  if obj=='network.wireless' then return {radio0={up=true}} end
  error('unexpected object '..obj)
 end,close=function() end} end} end
package.preload['luci.jsonc']=function() return {
 parse=function() return {rat='sa',dbm=-87} end,
 stringify=function(v) captured=v;return '{}' end} end
package.preload['nixio']=function() return {nanosleep=function()
 assert(captured,'tick failed')
 assert(captured.internet==EXPECTED)
 assert(captured.cellular_interface=='livecell' and captured.cellular_health==HEALTH)
 assert(captured.selected==SELECTED and captured.selected_health==SELECTED_HEALTH)
 assert(writes['/sys/class/leds/ipq::led4/brightness']==(EXPECTED and '0' or '1'))
 assert((writes['/sys/class/leds/ipq::led6/trigger']=='timer')==RED_BLINK)
 if not RED_BLINK then assert(writes['/sys/class/leds/ipq::led2/brightness']=='1') end
 assert(writes['/sys/class/leds/ipq::led0/brightness']=='0')
 assert(captured.led_levels['ipq::led0'].logical=='on')
 assert(writes['/sys/class/leds/ipq::led12/brightness']==nil)
 assert(loads.mwan3 and loads.qmodem)
 os.exit(0)
end} end
'''
                values = dict(HEALTH=health, SIM=sim, UP=up, BLOCKED=blocked,
                              SELECTED=selected, SELECTED_HEALTH=selected_health,
                              SELECTED_UP=selected_up, EXPECTED=expected, RED_BLINK=red_blink)
                prefix = ''.join(k+'='+json.dumps(v)+'\n' for k,v in values.items())
                with tempfile.TemporaryDirectory() as folder:
                    p = Path(folder)/'fixture.lua'
                    p.write_text(prefix+harness+'dofile('+json.dumps(str(ROOT/'files/usr/sbin/aw1000-leds.lua'))+')\n')
                    r = subprocess.run([lua,str(p)], capture_output=True, text=True, timeout=5)
                    self.assertEqual(r.returncode,0,r.stderr)

    def test_signal_fault_blinks_for_one_minute_then_turns_off_and_recovers(self):
        lua = shutil.which('lua5.1') or shutil.which('lua')
        self.assertIsNotNone(lua)
        harness = r'''local writes,captured,stage={},nil,0
local function fakefile(value,path)
 return {read=function() return tostring(value) end,close=function() end,
 write=function(_,v) writes[path]=tostring(v);return true end}
end
io.open=function(path,mode)
 if mode=='w' then return fakefile('',path) end
 if path=='/tmp/sysinfo/board_name' then return fakefile('arcadyan,aw1000') end
 if path=='/proc/uptime' then return fakefile(tostring(NOW)..' 0\n') end
 if path=='/var/run/aw1000-mwan-follow/selected' then return fakefile('wan') end
 if path=='/var/run/mwan3track/livecell/STATUS' then return fakefile(HEALTH) end
 if path=='/var/run/mwan3track/wan/STATUS' then return fakefile('online') end
 if path=='/var/run/aw1000-internet/blocked' then return fakefile('') end
 local n=path:match('^/sys/class/leds/ipq::led(%d+)/max_brightness$')
 if n and tonumber(n)<=12 then return fakefile('1') end
end
io.popen=function(cmd)
 if cmd:find('--sim-state',1,true) then return fakefile(SIM) end
 if cmd:find('--radio-state',1,true) then return fakefile('{}') end
 error('unexpected command '..cmd)
end
os.rename=function() return true end
package.preload['uci']=function() return {cursor=function() return {
 unload=function() end,load=function() end,
 get=function(_,c,_,k)
  if c=='mwan3' and k=='aw1000_enabled' then return '1' end
  if c=='mwan3' and k=='aw1000_cellular' then return 'livecell' end
 end,
 foreach=function(_,_,_,fn) fn({name='rg500q-ea',at_port='/dev/ttyUSB3'}) end} end} end
package.preload['ubus']=function() return {connect=function() return {
 call=function(_,obj)
  if obj=='network.interface.livecell' then return {up=true,l3_device='wwan0_1',['ipv4-address']={{address='10.0.0.2'}}} end
  if obj=='network.interface.wan' then return {up=true,l3_device='wan',['ipv4-address']={{address='192.0.2.2'}}} end
  if obj=='network.wireless' then return {radio0={up=true}} end
  error('unexpected object '..obj)
 end,close=function() end} end} end
package.preload['luci.jsonc']=function() return {
 parse=function()
  if CASE=='network' and HEALTH~='online' then return {rat='unknown',registered=false} end
  return {rat='sa',dbm=-87,registered=true}
 end,
 stringify=function(v) captured=v;return '{}' end} end
package.preload['nixio']=function() return {nanosleep=function()
 stage=stage+1
 if CASE=='hard' then
  if stage==1 then
   assert(captured.signal_fault=='sim-absent' and captured.signal_fault_elapsed==0)
   assert(captured.signal_fault_expired==false)
   assert(writes['/sys/class/leds/ipq::led6/trigger']=='timer')
   NOW=161;return
  end
  assert(stage==2 and captured.signal_fault_elapsed==61 and captured.signal_fault_expired==true)
  assert(writes['/sys/class/leds/ipq::led6/trigger']=='none')
  assert(writes['/sys/class/leds/ipq::led6/brightness']=='0')
  os.exit(0)
 end
 if stage==1 then
  assert(captured.signal_fault=='' and captured.signal_fault_expired==false)
  assert(writes['/sys/class/leds/ipq::led2/brightness']=='0')
  assert(writes['/sys/class/leds/ipq::led6/trigger']=='none')
  NOW=116;return
 elseif stage==2 then
  assert(captured.signal_fault=='network-unavailable' and captured.signal_fault_elapsed==0)
  assert(writes['/sys/class/leds/ipq::led6/trigger']=='timer')
  NOW=177;return
 elseif stage==3 then
  assert(captured.signal_fault_expired==true and captured.signal_fault_elapsed==61)
  assert(writes['/sys/class/leds/ipq::led6/trigger']=='none')
  assert(writes['/sys/class/leds/ipq::led6/brightness']=='0')
  SIM='unknown';NOW=182;return
 elseif stage==4 then
  assert(captured.signal_fault=='network-unavailable' and captured.signal_fault_expired==true)
  assert(writes['/sys/class/leds/ipq::led6/brightness']=='0')
  SIM='ready';HEALTH='online';NOW=187;return
 end
 assert(stage==5 and captured.signal_fault=='' and captured.signal_fault_expired==false)
 assert(writes['/sys/class/leds/ipq::led2/brightness']=='1')
 os.exit(0)
end} end
'''
        for case in ('hard', 'network'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                values = {'CASE': case, 'NOW': 100,
                          'SIM': 'absent' if case == 'hard' else 'ready',
                          'HEALTH': 'offline'}
                prefix = ''.join(k+'='+json.dumps(v)+'\n' for k,v in values.items())
                p = Path(folder)/'fixture.lua'
                p.write_text(prefix+harness+'dofile('+json.dumps(str(ROOT/'files/usr/sbin/aw1000-leds.lua'))+')\n')
                r = subprocess.run([lua,str(p)], capture_output=True, text=True, timeout=5)
                self.assertEqual(r.returncode,0,r.stderr)


class RadioSampleExpiryTests(unittest.TestCase):
    def test_unknown_samples_do_not_preserve_old_green_forever(self):
        import ast
        module=ast.parse((ROOT/'tests/test_led_managed.py').read_text())
        method=next(n for n in ast.walk(module) if isinstance(n,ast.FunctionDef) and n.name=='test_only_wired_keeps_real_signal_for_minutes_without_data_or_probes')
        h=next(n.value.value for n in method.body if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='harness' for x in n.targets))
        start=h.index('local phases={');end=h.index('\nlocal function file',start)
        h=h[:start]+"""local phases={
 {now=100,rat='sa',db=-87,health='disabled',mode='wired_only',color='green'},
 {now=10000,health='disabled',mode='wired_only',color='off'}
}
"""+h[end:]
        h=h.replace("return file('ready')","return file(stage==1 and 'ready' or 'unknown')")
        h=h.replace("assert(captured,'tick failed')","assert(captured,'tick failed');if stage==2 then assert(captured.radio_sample_status=='expired') end")
        lua=shutil.which('lua5.1') or shutil.which('lua')
        p=subprocess.run([lua,'-'],input=h+'\ndofile('+json.dumps(str(ROOT/'files/usr/sbin/aw1000-leds.lua'))+')\n',text=True,capture_output=True,timeout=5)
        self.assertEqual(p.returncode,0,p.stderr)
