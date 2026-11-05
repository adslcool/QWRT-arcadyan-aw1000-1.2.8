import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile
import zlib
import shutil

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from password import derive,DEFAULT_KEY
from settings import validate
from build import get_ubi,extract_tar_files,driver_bundle,install_librespeed
from fdt import Node,pack,parse,prepare_led_fit,prepare_phone_fit
import phone
sys.path.insert(0,str(ROOT/'phone'))
import build_driver
import build_public_driver
from kernel_exports import prel32_exports,write_symvers
from customize import patch_qmodem,remove_apps
from upgrade_image import write_tar,verify_tar
import publish_release


class ModemdataTests(unittest.TestCase):
    def setUp(self):
        from customize import install_modemdata
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name);self.root=self.path/'root'
        info=self.root/'usr/lib/opkg/info';info.mkdir(parents=True)
        dependencies=('lua','luci-lib-nixio','luci-lib-jsonc','jq','jsonfilter','ubus-at-daemon','luci-base')
        (self.root/'usr/lib/opkg/status').write_text(''.join('Package: '+n+'\nStatus: install user installed\n\n' for n in dependencies))
        self.metadata=install_modemdata(self.root)
        self.bin=self.path/'bin';self.bin.mkdir()
        self.env={**os.environ,'PATH':str(self.bin)+':'+os.environ['PATH'],'MD_TEST_ROOT':str(self.path)}
    def executable(self,name,body):
        p=self.bin/name;p.write_text(body);p.chmod(0o755);return p
    def target(self):
        r=os.getenv('AW1000_TEST_TARGET_ROOT')
        if not r or not shutil.which('qemu-aarch64-static'):self.skipTest('complete QWRT runtime required')
        return Path(r)
    def transport(self):
        from upgrade_image import target_command
        import shlex
        target=self.target()
        self.env.update(LUA_CPATH=str(target)+'/usr/lib/lua/?.so',LUA_PATH=str(target)+'/usr/lib/lua/?.lua')
        launcher=self.path/'lua-launcher'
        launcher.write_text("""package.loaded['nixio.fs']={access=function(p) return p=='/dev/ttyUSB3' end}
local original=io.open
io.open=function(p,...) assert(not tostring(p):match('^/dev/'),'direct serial open');return original(p,...) end
local file=table.remove(arg,1);dofile(file)
""")
        ubus=self.executable('queue',r'''#!/usr/bin/env python3
import os,sys,json,pathlib
p=pathlib.Path(os.environ['MD_TEST_ROOT']);r=json.loads(sys.argv[-1]);cmd=r['at_cmd']
with (p/'at-calls').open('a') as f:f.write(json.dumps(r)+'\n')
if os.environ.get('MD_FAIL')=='1':print(json.dumps({'status':'error'}));sys.exit(0)
mode=os.environ.get('MD_CASE','sa');rat={'lte':7,'sa':11,'nsa':13}[mode]
lte='+QENG: "servingcell","NOCONN","LTE","FDD",460,00,73CFD7B,240,3590,8,3,3,36B2,-78,-7,-53,19,15,170,-'
sa='+QENG: "servingcell","NOCONN","NR5G-SA","TDD", 460,00,2312BF001,83,701113,504990,41,12,-87,-12,4,1,-'
nsa='+QENG: "servingcell","NOCONN"\n+QENG: "LTE","FDD",460,00,73CFD7B,240,3590,8,3,3,36B2,-78,-7,-53,19,15,170,-\n+QENG: "NR5G-NSA",460,00,572,-87,4,-12,504990,41,12'
if os.environ.get('MD_BAD_NSA')=='1':nsa=nsa.replace('572,-87','572,-32768')
table={'AT+CSQ':'+CSQ: 25,99','AT+COPS?':f'+COPS: 0,2,"46000",{rat}',
'AT+CREG?':f'+CREG: 2,1,"36B2","73CFD7B",{rat}','AT+CPIN?':'+CPIN: READY',
'AT+QTEMP':'+QTEMP: "xo_therm",45','AT+QUIMSLOT?':'+QUIMSLOT: 1',
'AT+QNWINFO':'+QNWINFO: "NR5G-SA","46000","NR5G BAND 41",504990' if mode=='sa' else '+QNWINFO: "FDD LTE","46000","LTE BAND 8",3590',
'AT+QCAINFO':'+QCAINFO: "PCC",3590,30,"LTE BAND 8",1,240,-78,-7,-53,19',
'AT+QENG="SERVINGCELL"':{'sa':sa,'lte':lte,'nsa':nsa}[mode],
'AT+QSPN':'+QSPN: "China Mobile","CMCC","CMCC",0,"46000"',
'AT+CGMI':'Quectel','AT+CGMM':'RG500Q-EA','AT+CGMR':'RG500QEA-test','AT+CGSN':'123456789012345','AT+CIMI':'460001234567890','AT+QCCID':'+QCCID: 89860012345678901234'}
body=table.get(cmd.upper(),'')
print(json.dumps({'status':'success','response':cmd+'\r\n'+body+'\r\nOK\r\n'}))
''')
        self.env['AW1000_MD_UBUS']=str(ubus)
        at=self.executable('at-adapter','#!/bin/sh\nexec '+shlex.join(target_command(target,'usr/bin/lua')+[str(launcher),str(self.root/'usr/libexec/aw1000-modemdata-at')])+' "$@"\n')
        return at
    def test_all_sources_pages_chinese_acl_and_dependencies_are_installed(self):
        from customize import modemdata_sources
        s,lock=modemdata_sources();self.assertEqual(len(s),133)
        self.assertEqual(self.metadata['views'],5);self.assertFalse(self.metadata['additional_serial_daemon'])
        menu=json.loads((self.root/'usr/share/luci/menu.d/luci-app-modemdata.json').read_text())
        self.assertEqual(sum(x.get('action',{}).get('type')=='view' for x in menu.values()),5)
        self.assertEqual(menu['admin/modem/modemdata']['title'],'移动网络信息')
        define=(self.root/'www/luci-static/resources/view/modem/modemdefine.js').read_text()
        self.assertIn('自动跟随 QModem AT 端口',define);self.assertNotIn('/etc/modem/modemlist.json',define)
        self.assertIn('/tmp/aw1000-modemdata-modemlist.json',define)
        preview=(self.root/'www/luci-static/resources/view/modem/modempreview.js').read_text()
        self.assertIn('!document.hidden',preview);self.assertIn('aw1000ModemdataFreshness(modem,json[2])',preview)
        for name in ('params.sh','params-upstream.sh','product.sh','vendorproduct/generic'):
            self.assertNotIn('sms_tool ',(self.root/'usr/share/modemdata'/name).read_text())
        self.assertIn('at-daemon',(self.root/'usr/libexec/aw1000-modemdata-at').read_text())
        self.assertFalse((self.root/'etc/init.d/modemdata').exists())
        cleanup=(ROOT/'files/usr/sbin/aw1000-network-cleanup').read_text()
        self.assertFalse(any('/etc/config/modemdata' in line for line in cleanup.splitlines() if line.startswith('rm ')))
    def test_target_first_boot_registers_auto_port_once_and_preserves_edits(self):
        from customize import MODEMDATA_DEFAULTS
        from upgrade_image import target_command
        import shlex
        target=self.target();cfg=self.root/'etc/config';delta=self.path/'delta';delta.mkdir()
        base=target_command(target,'sbin/uci')+['-c',str(cfg)]
        self.executable('uci','#!/bin/sh\nset -e\nif [ "$1" = commit ];then\n '+shlex.join(base+['-p',str(delta)])+' "$@"\n rm -f '+shlex.quote(str(delta))+'/"$2"\n echo "$2" >> "$MD_TEST_ROOT/commits"\nelse\n exec '+shlex.join(base+['-P',str(delta)])+' "$@"\nfi\n')
        script=self.path/'defaults';script.write_text(MODEMDATA_DEFAULTS.replace('/etc/',str(self.root/'etc')+'/'))
        def boot():
            p=subprocess.run(['sh',str(script)],env=self.env,text=True,capture_output=True,timeout=8)
            self.assertEqual(p.returncode,0,p.stderr)
        def uci(*args):return subprocess.check_output([str(self.bin/'uci'),*args],env=self.env,text=True).strip()
        # A staged user change is never committed as part of package initialization.
        uci('set','modemdata.@modemdata[0].updtime=30');boot()
        self.assertFalse((self.path/'commits').exists());uci('revert','modemdata')
        boot()
        self.assertEqual(uci('get','defmodems.aw1000.comm_port'),'auto')
        self.assertEqual(uci('get','defmodems.aw1000.network'),'wwan')
        self.assertEqual((self.path/'commits').read_text(),'defmodems\n')
        uci('set','defmodems.aw1000.comm_port=/dev/ttyUSB1');uci('commit','defmodems');boot()
        self.assertEqual(uci('get','defmodems.aw1000.comm_port'),'/dev/ttyUSB1')
        (self.root/'etc/aw1000-mod/modemdata-1.2.4.done').unlink()
        (cfg/'modemdata').write_text("config old 'old'\n option value 'keep'\n")
        boot()
        self.assertEqual(uci('get','modemdata.old.value'),'keep')
        self.assertEqual(uci('get','modemdata.@modemdata[0].updtime'),'10')
        self.assertEqual(uci('get','defmodems.aw1000.comm_port'),'/dev/ttyUSB1')

    def test_target_transport_splits_group_and_preserves_queue_payload(self):
        at=self.transport()
        p=subprocess.run([str(at),'-D','-d','/dev/ttyUSB3','at','AT+CSQ;+QENG="servingcell"'],env=self.env,text=True,capture_output=True,timeout=10)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn('NR5G-SA',p.stdout);self.assertIn('+CSQ:',p.stdout)
        calls=[json.loads(x) for x in (self.path/'at-calls').read_text().splitlines()]
        self.assertEqual([x['at_cmd'] for x in calls],['AT+CSQ','AT+QENG="servingcell"'])
        self.assertTrue(all(x['at_port']=='/dev/ttyUSB3' and x['timeout']==2 for x in calls))
    def test_target_queue_failure_marks_error_and_never_opens_tty(self):
        at=self.transport();self.env['MD_FAIL']='1';marker=self.path/'failure';self.env['AW1000_MD_FAILURE_FILE']=str(marker)
        p=subprocess.run([str(at),'-d','/dev/ttyUSB3','at','AT+CPIN?'],env=self.env,text=True,capture_output=True,timeout=8)
        self.assertNotEqual(p.returncode,0);self.assertTrue(marker.exists());self.assertEqual(p.stdout,'')
    def test_target_product_json_handles_identity_fields(self):
        at=self.transport()
        p=subprocess.run([str(at),'--product','/dev/ttyUSB3'],env=self.env,text=True,capture_output=True,timeout=10)
        self.assertEqual(p.returncode,0,p.stderr);data=json.loads(p.stdout)
        self.assertEqual(data['product'],'RG500Q-EA');self.assertEqual(data['iccid'],'89860012345678901234')
    def test_actual_ash_parser_handles_lte_sa_nsa_and_large_cell_id(self):
        from upgrade_image import target_command
        import shlex
        at=self.transport();target=self.target()
        self.executable('busybox','#!/bin/sh\nexec '+shlex.join(target_command(target,'bin/busybox'))+' "$@"\n')
        base=self.root/'usr/share/modemdata'
        helper=base/'libs/getdevicevendorproduct';helper.write_text('getdevicevendorproduct() { echo usb/2c7c0800; }\n')
        for file in base.rglob('*'):
            if file.is_file():
                try:txt=file.read_text()
                except UnicodeDecodeError:continue
                txt=txt.replace('/usr/libexec/aw1000-modemdata-at',str(at)).replace('/usr/share/modemdata',str(base))
                txt=txt.replace('[ -z "$DEVICE" ] || [ ! -e "$DEVICE" ]','[ -z "$DEVICE" ]')
                file.write_text(txt)
        script=base/'params-upstream.sh'
        for mode in ('lte','sa','nsa','nsa-unavailable'):
            with self.subTest(mode=mode):
                self.env['MD_CASE']='nsa' if mode=='nsa-unavailable' else mode
                self.env['MD_BAD_NSA']='1' if mode=='nsa-unavailable' else '0'
                p=subprocess.run(target_command(target,'bin/busybox')+['sh',str(script),'/dev/ttyUSB3','1'],env=self.env,text=True,capture_output=True,timeout=20)
                self.assertEqual(p.returncode,0,p.stderr);data=json.loads(p.stdout)
                self.assertEqual(data['registration'],'1')
                self.assertIn('LTE' if mode in ('lte','nsa-unavailable') else '5G',data['mode'])
                self.assertTrue(data['addon'])
                if mode=='sa':
                    self.assertEqual(data['cid_hex'],'2312BF001')
                    self.assertEqual(data['cid_dec'],str(int('2312BF001',16)))
                    self.assertTrue(any(x['key']=='RSRP' and '-87' in x['value'] for x in data['addon']))
    def test_data_cache_reuses_ram_backs_off_and_expires_failed_sample(self):
        from customize import MODEMDATA_CACHE
        dev=self.path/'dev/ttyUSB3';dev.parent.mkdir();dev.touch()
        now=self.path/'uptime';now.write_text('1000.00 0.00\n')
        self.env.update(AW1000_MD_RUN=str(self.path/'run'),AW1000_MD_UPTIME=str(now))
        sample=self.executable('sample','''#!/bin/sh
echo sample >> "$MD_TEST_ROOT/samples"
if [ -e "$MD_TEST_ROOT/fail" ];then
 if [ -e "$MD_TEST_ROOT/advance" ];then cat "$MD_TEST_ROOT/advance" > "$AW1000_MD_UPTIME";rm -f "$MD_TEST_ROOT/advance";fi
 echo failed > "$AW1000_MD_FAILURE_FILE";exit 1
fi
echo '{"mode":"5G SA","registration":"1","addon":[]}'
''')
        body=MODEMDATA_CACHE.replace('/dev/',str(self.path/'dev')+'/').replace('/usr/share/modemdata/params-upstream.sh',str(sample))
        worker=self.executable('cache',body)
        def run():
            p=subprocess.run(['sh',str(worker),'params',str(dev),'1'],env=self.env,text=True,capture_output=True,timeout=5)
            self.assertEqual(p.returncode,0,p.stderr);return json.loads(p.stdout)
        self.assertFalse(run()['_aw1000']['stale']);now.write_text('1001.00 0\n');run()
        self.assertEqual((self.path/'samples').read_text().splitlines(),['sample'])
        (self.path/'fail').touch();now.write_text('1011.00 0\n')
        (self.path/'advance').write_text('1014.00 0\n')
        self.assertTrue(run()['_aw1000']['stale'])
        self.assertEqual(next((self.path/'run').glob('*.retry')).read_text(),'1019\n')
        now.write_text('1015.00 0\n');run()
        self.assertEqual(len((self.path/'samples').read_text().splitlines()),2)
        now.write_text('1061.00 0\n');self.assertIn('error',run())
        self.assertFalse((self.path/'etc/config').exists())


class LibrespeedTests(unittest.TestCase):
    def fixture(self,root):
        for directory in ('usr/lib/opkg/info','etc/rc.d'):(root/directory).mkdir(parents=True,exist_ok=True)
        (root/'usr/lib/opkg/status').write_text('Package: libc\nVersion: baseline\nStatus: install user installed\n\n')
        (root/'etc/passwd').write_text('root:x:0:0:root:/root:/bin/ash\n')
        (root/'etc/group').write_text('root:x:0:\n')
        (root/'etc/shadow').write_text('root:!:0:0:99999:7:::\n')

    def test_uploaded_package_installs_enabled_service_user_metadata_and_config(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root)
            metadata=install_librespeed(root)
            self.assertEqual(metadata['listen_port'],8989)
            cfg=(root/'etc/config/librespeed-go').read_text()
            for setting in ("option enabled '1'","option listen_port '8989'","option database_type 'none'"):
                self.assertIn(setting,cfg)
            self.assertEqual((root/'etc/rc.d/S99librespeed-go').readlink().as_posix(),'../init.d/librespeed-go')
            self.assertIn('librespeed:x:65536:65536:',(root/'etc/passwd').read_text())
            self.assertIn('librespeed:!:',(root/'etc/shadow').read_text())
            self.assertIn('Require-User: librespeed:librespeed',(root/'usr/lib/opkg/info/librespeed-go.control').read_text())
            status=(root/'usr/lib/opkg/status').read_text()
            self.assertEqual(status.count('Package: librespeed-go\n'),1)
            self.assertIn('/usr/bin/librespeed-go',(root/'usr/lib/opkg/info/librespeed-go.list').read_text())
            self.assertEqual((root/'usr/bin/librespeed-go').stat().st_mode&0o777,0o755)
            # Reinstalling cannot create duplicate service users or opkg records.
            before={name:(root/'etc'/name).read_bytes() for name in ('passwd','group','shadow')}
            install_librespeed(root)
            for name,data in before.items():self.assertEqual((root/'etc'/name).read_bytes(),data)
            self.assertEqual((root/'usr/lib/opkg/status').read_text().count('Package: librespeed-go\n'),1)

    def test_service_account_collision_never_uses_root(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root)
            (root/'etc/passwd').write_text('root:x:0:0:root:/root:/bin/ash\nlibrespeed:x:0:0:bad:/root:/bin/ash\n')
            with self.assertRaises(ValueError):install_librespeed(root)

    def test_first_boot_enables_restored_disabled_config_only_once(self):
        target=os.environ.get('AW1000_TEST_TARGET_ROOT')
        if not target or not shutil.which('qemu-aarch64-static'):self.skipTest('QWRT UCI and QEMU required')
        import shlex
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root);install_librespeed(root)
            config=root/'etc/config';binpath=root/'bin';binpath.mkdir()
            delta=root/'uci-delta';delta.mkdir()
            def executable(path,text):path.write_text(text);path.chmod(0o755)
            base=['qemu-aarch64-static','-L',target,target+'/sbin/uci','-c',str(config)]
            executable(binpath/'uci','#!/bin/sh\nset -e\nif [ "$1" = commit ]; then\n '+
                shlex.join(base+['-p',str(delta)])+' "$@"\n rm -f -- '+shlex.quote(str(delta))+'/"$2"\nelse\n exec '+
                shlex.join(base+['-P',str(delta)])+' "$@"\nfi\n')
            executable(root/'etc/init.d/librespeed-go','#!/bin/sh\necho "$*" >> '+shlex.quote(str(root/'service-actions'))+'\n')
            defaults=(ROOT/'files/etc/uci-defaults/99-aw1000-mod').read_text()
            block=defaults.split('. /lib/functions.sh\n',1)[1].split('rm -f /etc/banner',1)[0]
            block=block.replace('/usr/bin/librespeed-go',str(root/'usr/bin/librespeed-go')).replace('/etc/aw1000-mod',str(root/'etc/aw1000-mod')).replace('/etc/init.d/librespeed-go',str(root/'etc/init.d/librespeed-go'))
            lib='group_add_next() { echo 65536; }; user_exists() { return 0; }; user_add() { exit 95; };\n'
            env={**os.environ,'PATH':str(binpath)+':'+os.environ['PATH']}
            cmd=[str(binpath/'uci')]
            subprocess.run(cmd+['set','librespeed-go.config.enabled=0'],check=True,env=env)
            subprocess.run(cmd+['set','librespeed-go.config.listen_port=8999'],check=True,env=env)
            subprocess.run(cmd+['commit','librespeed-go'],check=True,env=env)
            p=subprocess.run(['sh'],input=lib+block,env=env,text=True,capture_output=True)
            self.assertEqual(p.returncode,0,p.stderr)
            def get(key):return subprocess.check_output(cmd+['get','librespeed-go.config.'+key],env=env,text=True).strip()
            self.assertEqual((get('enabled'),get('listen_port'),get('database_type')),('1','8989','none'))
            self.assertEqual((root/'service-actions').read_text(),'enable\n')
            subprocess.run(cmd+['set','librespeed-go.config.enabled=0'],check=True,env=env)
            subprocess.run(cmd+['commit','librespeed-go'],check=True,env=env)
            p=subprocess.run(['sh'],input=lib+block,env=env,text=True,capture_output=True)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(get('enabled'),'0')
            self.assertEqual((root/'service-actions').read_text(),'enable\n')


class SettingsTests(unittest.TestCase):
    def test_defaults_use_serial_mode_and_separate_ssids(self):
        s=validate({})
        self.assertEqual(s['root_password_hash'],'')
        self.assertNotEqual(s['ssid_24g'],s['ssid_5g'])
        self.assertEqual(s['phone_mode'],'bundled')
        self.assertNotIn('phone_bundle_url',s)
        self.assertEqual(s['router_serial'],'')

    def test_phone_modes_use_only_their_selected_driver_source(self):
        self.assertEqual(validate({'PHONE_MODE':'deferred'})['phone_mode'],'deferred')
        for env in ({'PHONE_MODE':'required'},
                    {'PHONE_MODE':'bundled','PHONE_BUNDLE_URL':'https://example.com/a'},
                    {'PHONE_MODE':'deferred','PHONE_BUNDLE_URL':'https://example.com/a'}):
            with self.assertRaises(ValueError):validate(env)

    def test_fixed_password_is_hashed(self):
        s=validate({'ROOT_PASSWORD':'a-test-password'})
        self.assertTrue(s['root_password_hash'].startswith('$1$'))
        self.assertNotIn('a-test-password',json.dumps(s))

    def test_serial_hint_is_validated_and_kept_for_missing_board_data(self):
        self.assertEqual(validate({'ROUTER_SERIAL':'ARC1234567890'})['router_serial'],'ARC1234567890')
        for value in ('1234567890','ARC12345678901','ARC123456789x',"ARC1234567890;false"):
            with self.assertRaises(ValueError):validate({'ROUTER_SERIAL':value})

    def test_rejects_invalid_ip_and_hostnames(self):
        for env in ({'LAN_IP':'8.8.8.8'},{'LAN_IP':'192.168.1.255'},
                    {'ROUTER_NAME':'x; reboot'},{'ROUTER_NAME':'-router'}):
            with self.subTest(env=env),self.assertRaises(ValueError):validate(env)

    def test_wifi_and_ssid_bounds(self):
        for env in ({'WIFI_PASSWORD':'short'},{'SSID_24G':'中'*11},{'SSID_5G':'a\nb'}):
            with self.assertRaises(ValueError):validate(env)

    def test_credentials_and_shell_metacharacters_remain_data(self):
        value="WiFi'$(touch /tmp/AW1000_UNWANTED)"
        s=validate({'SSID_24G':"a'$(false)", 'WIFI_PASSWORD':value})
        import shlex
        result=subprocess.check_output(['sh','-c','x='+shlex.quote(s['wifi_password'])+'; printf %s "$x"'],text=True)
        self.assertEqual(result,value)
        self.assertFalse(Path('/tmp/AW1000_UNWANTED').exists())

    def test_rejects_non_http_and_embedded_credentials(self):
        for value in ('file:///etc/shadow','https://user:pass@example.com/a','https://example.com/a#bad'):
            with self.assertRaises(ValueError):validate({'FIRMWARE_URL':value})


class PasswordTests(unittest.TestCase):
    def provision_fixture(self, folder, serial='', admin=''):
        root=Path(folder)
        state=root/'etc/aw1000-mod';state.mkdir(parents=True)
        (state/'derivation.key').write_text(DEFAULT_KEY+'\n')
        for name in ('network.done','wireless.done'):(state/name).touch()
        if serial:(state/'serial.hint').write_text(serial+'\n')
        (root/'etc/shadow').write_text('root:!:0:0:99999:7:::\nnobody:*:0:0:99999:7:::\n')
        (root/'board_name').write_text('arcadyan,aw1000\n')
        (root/'mtd-table').write_text('dev: size erasesize name\n')
        digest=validate({'ROOT_PASSWORD':admin})['root_password_hash']
        (root/'etc/aw1000-build.conf').write_text("ROOT_PASSWORD_HASH='"+digest+"'\n")
        binpath=root/'bin';binpath.mkdir()
        (binpath/'logger').write_text('#!/bin/sh\nexit 0\n');(binpath/'logger').chmod(0o755)
        credentials=binpath/'credentials'
        credentials.write_text((ROOT/'files/usr/sbin/aw1000-credentials').read_text()
                               .replace('/etc/',str(root/'etc')+'/')
                               .replace('/sys/',str(root/'sys')+'/')
                               .replace('/proc/mtd',str(root/'mtd-table')))
        credentials.chmod(0o755)
        provision=binpath/'provision'
        provision.write_text((ROOT/'files/usr/sbin/aw1000-provision').read_text()
                             .replace('/usr/sbin/aw1000-credentials',str(credentials))
                             .replace('/etc/',str(root/'etc')+'/')
                             .replace('/tmp/sysinfo/board_name',str(root/'board_name'))
                             .replace('/sysupgrade.tgz',str(root/'sysupgrade.tgz'))
                             .replace('/tmp/sysupgrade.tar',str(root/'sysupgrade.tar')))
        env={**os.environ,'PATH':str(binpath)+os.pathsep+os.environ['PATH']}
        env.pop('AW1000_KEY',None)
        return root,state,credentials,provision,env

    def assert_stored_password(self, root, expected):
        actual=(root/'etc/shadow').read_text().splitlines()[0].split(':')[1]
        self.assertTrue(actual.startswith('$1$'))
        computed=subprocess.check_output(['openssl','passwd','-1','-salt',actual.split('$')[2],'-stdin'],
                                         input=expected+'\n',text=True).strip()
        self.assertEqual(actual,computed)

    def test_first_boot_sn_password_uses_target_supported_hash(self):
        with tempfile.TemporaryDirectory() as d:
            root,state,credentials,provision,env=self.provision_fixture(d,'ARC1234567890')
            subprocess.run(['sh',str(provision)],env=env,check=True)
            self.assert_stored_password(root,derive('ARC1234567890'))
            self.assertIn('mode=sn-v3',(state/'credentials.info').read_text())
            self.assertEqual(subprocess.check_output([str(credentials),'show'],env=env,text=True).strip(),derive('ARC1234567890'))
            self.assertIn('shadow_format=md5-crypt',subprocess.check_output([str(credentials),'status'],env=env,text=True))

    def test_fallback_password_is_reported_after_later_serial_discovery(self):
        with tempfile.TemporaryDirectory() as d:
            root,state,credentials,provision,env=self.provision_fixture(d)
            subprocess.run(['sh',str(provision)],env=env,check=True)
            self.assert_stored_password(root,'password')
            (state/'serial.hint').write_text('ARC1234567890\n')
            self.assertEqual(subprocess.check_output([str(credentials),'show'],env=env,text=True).strip(),'password')
            before=(root/'etc/shadow').read_bytes()
            subprocess.run(['sh',str(provision)],env=env,check=True)
            self.assertEqual((root/'etc/shadow').read_bytes(),before)

    def test_explicit_password_is_written_and_not_reported_as_derived(self):
        with tempfile.TemporaryDirectory() as d:
            root,state,credentials,provision,env=self.provision_fixture(d,'ARC1234567890','test-explicit-password')
            subprocess.run(['sh',str(provision)],env=env,check=True)
            self.assert_stored_password(root,'test-explicit-password')
            self.assertIn('mode=explicit',(state/'credentials.info').read_text())
            r=subprocess.run([str(credentials),'show'],env=env,capture_output=True,text=True)
            self.assertNotEqual(r.returncode,0)
            self.assertEqual(r.stdout,'')

    def test_hardware_serial_wins_over_build_hint(self):
        with tempfile.TemporaryDirectory() as d:
            root,state,credentials,provision,env=self.provision_fixture(d,'ARC1234567890')
            f=root/'sys/firmware/devicetree/base/serial-number';f.parent.mkdir(parents=True)
            f.write_bytes(b'ARC0000000000\0')
            self.assertEqual(subprocess.check_output([str(credentials),'serial'],env=env,text=True).strip(),'ARC0000000000')

    def test_shell_matches_v150_reference_for_multiple_serials(self):
        for serial in ('ARC1234500001','ARC0000000000','ARC9999999999','ARC1234567890'):
            result=subprocess.check_output(['sh',str(ROOT/'files/usr/sbin/aw1000-credentials'),'derive',serial],
                    env={**os.environ,'AW1000_KEY':DEFAULT_KEY},text=True).strip()
            self.assertEqual(result,derive(serial))
            self.assertRegex(result,r'^[a-z0-9]{8}$')
            self.assertTrue(any(c.isdigit() for c in result))
            self.assertTrue(any(c.isalpha() for c in result))

    def test_key_override_changes_result(self):
        self.assertNotEqual(derive('ARC1234500001'),derive('ARC1234500001','ab'*32))

    def test_invalid_serial_is_rejected_in_both_implementations(self):
        for s in ('IMEI123456789012345','ARC123','ARC12345678900','ARC123456789x'):
            with self.assertRaises(ValueError):derive(s)
            r=subprocess.run(['sh',str(ROOT/'files/usr/sbin/aw1000-credentials'),'derive',s],
                env={**os.environ,'AW1000_KEY':DEFAULT_KEY},capture_output=True)
            self.assertNotEqual(r.returncode,0)


class ArchiveTests(unittest.TestCase):
    def test_actual_bundled_driver_installs_only_as_explicit_experiment(self):
        bundle=ROOT/'vendor/aw1000-phone-driver.tar.gz'
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);target=root/'lib/modules/5.4.213';target.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError,'实验驱动'):driver_bundle(bundle,root)
            self.assertFalse((target/'aw1000-fxs.ko').exists())
            meta=driver_bundle(bundle,root,allow_experimental=True)
            self.assertEqual(hashlib.sha256((target/'aw1000-fxs.ko').read_bytes()).hexdigest(),meta['sha256'])
            self.assertFalse(meta['hardware_verified'])
            self.assertFalse(meta['compatibility_verified'])
            self.assertEqual(meta['profile'],'public-qsdk-experimental')

    def test_bundled_archive_is_rejected_if_its_pinned_bytes_change(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'modified.tar.gz'
            p.write_bytes((ROOT/'vendor/aw1000-phone-driver.tar.gz').read_bytes()+b'\0')
            with self.assertRaisesRegex(ValueError,'内置实验驱动包 SHA256'):
                driver_bundle(p,Path(d),allow_experimental=True)

    def test_public_driver_is_rejected_by_strict_firmware_integration(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'public.tar.gz'
            with tarfile.open(p,'w:gz') as t:
                for name,data in [('manifest.json',b'{"profile":"public-qsdk-experimental"}'),
                                  ('aw1000-fxs.ko',b'not a real module')]:
                    m=tarfile.TarInfo(name);m.size=len(data);t.addfile(m,io.BytesIO(data))
            with self.assertRaisesRegex(ValueError,'实验驱动'):
                driver_bundle(p,Path(d))

    def test_packed_phone_source_is_complete_and_hash_checked(self):
        lock=json.loads((ROOT/'sources.lock.json').read_text())
        files=phone.upstream_files()
        self.assertEqual(set(files),set(lock['phone_git_blobs']))
        self.assertEqual(len(files),74)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'vendor').mkdir()
            archive=root/lock['phone_archive'];archive.write_bytes(b'corrupt')
            (root/'sources.lock.json').write_text(json.dumps(lock))
            with mock.patch.object(phone,'ROOT',root),self.assertRaisesRegex(ValueError,'SHA256'):
                phone.upstream_files()

    def test_phone_source_cannot_extract_paths_or_links(self):
        for name,kind in (('../escape',tarfile.REGTYPE),('LICENSE',tarfile.SYMTYPE)):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as d:
                root=Path(d);(root/'vendor').mkdir()
                lock=json.loads((ROOT/'sources.lock.json').read_text())
                archive=root/lock['phone_archive']
                with tarfile.open(archive,'w:xz') as t:
                    m=tarfile.TarInfo(name);m.type=kind;m.linkname='/tmp/escape';t.addfile(m)
                lock['files'][lock['phone_archive']]=hashlib.sha256(archive.read_bytes()).hexdigest()
                (root/'sources.lock.json').write_text(json.dumps(lock))
                with mock.patch.object(phone,'ROOT',root),self.assertRaises(ValueError):phone.upstream_files()

    def test_driver_rejects_same_version_with_wrong_kernel_image(self):
        with tempfile.TemporaryDirectory() as d:
            kernel=Path(d)
            (kernel/'include/generated').mkdir(parents=True)
            (kernel/'arch/arm64/boot').mkdir(parents=True)
            (kernel/'.config').write_bytes((ROOT/'phone/qwrt-kernel.config').read_bytes())
            (kernel/'include/generated/utsrelease.h').write_text('#define UTS_RELEASE "5.4.213"\n')
            (kernel/'Module.symvers').write_text('0x00000000\tmisc_register\tvmlinux\tEXPORT_SYMBOL\n')
            (kernel/'arch/arm64/boot/Image').write_bytes(b'a different 5.4.213 kernel')
            with self.assertRaisesRegex(ValueError,'Image 与上传 QWRT 不同'):
                build_driver.validate_kernel(kernel,'/missing/toolchain-')

    def test_accepts_a_single_ubi_from_zip(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);z=p/'in.zip'
            with zipfile.ZipFile(z,'w') as f:f.writestr('firmware/base.ubi',b'UBI#abc')
            get_ubi(z,p/'out.ubi');self.assertEqual((p/'out.ubi').read_bytes(),b'UBI#abc')

    def test_zip_path_traversal_and_multiple_images_rejected(self):
        for entries in ({'../base.ubi':b'UBI#'},{'a.ubi':b'UBI#','b.ubi':b'UBI#'}):
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)
                with zipfile.ZipFile(p/'in.zip','w') as z:
                    for k,v in entries.items():z.writestr(k,v)
                with self.assertRaises(ValueError):get_ubi(p/'in.zip',p/'out')

    def test_tar_cannot_escape_via_existing_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'root';root.mkdir();(root/'escape').symlink_to('/tmp')
            blob=io.BytesIO()
            with tarfile.open(fileobj=blob,mode='w') as t:
                m=tarfile.TarInfo('escape/AW1000_UNWANTED');m.size=3;t.addfile(m,io.BytesIO(b'bad'))
            with self.assertRaises(ValueError):extract_tar_files(blob.getvalue(),root)

    def test_driver_rejects_abi_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bundle.tgz'
            with tarfile.open(p,'w:gz') as t:
                for name,b in [('manifest.json',b'{"kernel_package":"6.12"}'),('aw1000-fxs.ko',b'bad')]:
                    m=tarfile.TarInfo(name);m.size=len(b);t.addfile(m,io.BytesIO(b))
            with self.assertRaises(ValueError):driver_bundle(p,Path(d))


class FitTests(unittest.TestCase):
    def fixture(self):
        dt=Node('');dt.props['compatible']=b'arcadyan,aw1000\0qcom,ipq8074\0'
        dt.reservations=struct.pack('>QQ',0x40000000,0x1000)+b'\0'*16
        soc=Node('soc');spi=Node('spi@78b8000');spi.props['status']=b'ok\0'
        ledc=Node('ledc@191E000');ledc.props={'compatible':b'qti,ledc\0','qti,ledc_blink_indices':struct.pack('>15I',5,6,7,14,13,1,9,10,2,11,12,3,4,8,15)}
        soc.children=[spi,ledc];dt.children=[soc]
        fit=Node('');images=Node('images');fit.children=[images]
        kernel=Node('kernel@1');kernel.props={'type':b'kernel\0','data':b'original-kernel'}
        fdt=Node('fdt@1');fdt.props={'type':b'flat_dt\0','data':pack(dt)}
        for algo in ('sha1','crc32'):
            h=Node('hash-'+algo);h.props={'algo':algo.encode()+b'\0','value':b'old'};fdt.children.append(h)
        images.children=[kernel,fdt]
        return fit

    def test_phone_fit_preserves_kernel_reservations_and_rehashes_dtb(self):
        out=prepare_phone_fit(pack(self.fixture()))
        fit,_=parse(out);self.assertEqual(fit.get('/images/kernel@1').props['data'],b'original-kernel')
        f=fit.get('/images/fdt@1');dt,_=parse(f.props['data'])
        self.assertEqual(dt.get('/soc/spi@78b8000').props['status'],b'disabled\0')
        self.assertEqual(dt.reservations,struct.pack('>QQ',0x40000000,0x1000)+b'\0'*16)
        for n in f.children:
            expected=hashlib.sha1(f.props['data']).digest() if n.props['algo']==b'sha1\0' else struct.pack('>I',zlib.crc32(f.props['data']))
            self.assertEqual(n.props['value'],expected)

    def test_signed_fit_is_not_silently_modified(self):
        fit=self.fixture();fit.children.append(Node('signature@1'))
        with self.assertRaises(ValueError):prepare_phone_fit(pack(fit))

    def test_led_fit_maps_only_free_led0_to_physical_power_channel(self):
        out=prepare_led_fit(pack(self.fixture()))
        fit,_=parse(out);dt,_=parse(fit.get('/images/fdt@1').props['data'])
        values=struct.unpack('>15I',dt.get('/soc/ledc@191E000').props['qti,ledc_blink_indices'])
        self.assertEqual(values,(15,6,7,14,13,1,9,10,2,11,12,3,4,8,15))
        self.assertEqual(fit.get('/images/kernel@1').props['data'],b'original-kernel')

    def test_truncated_fdt_rejected(self):
        with self.assertRaises(ValueError):parse(b'bad')


class PublicDriverTests(unittest.TestCase):
    def test_prel32_records_require_valid_contiguous_structures(self):
        data=bytearray(4096);position=0x500
        for i in range(8):
            entry=0x100+i*12;name=('test_export_'+str(i)).encode()+b'\0'
            data[position:position+len(name)]=name
            struct.pack_into('<iii',data,entry,0x20+i*4-entry,position-(entry+4),0)
            position+=len(name)
        exports=prel32_exports(bytes(data))
        self.assertEqual(set(exports),{'test_export_'+str(i) for i in range(8)})
        self.assertEqual(exports['test_export_2']['value_offset'],0x28)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'reconstructed.symvers';write_symvers(exports,p)
            self.assertEqual(len(p.read_text().splitlines()),8)
            self.assertIn('EXPORT_SYMBOL_GPL',p.read_text())
        struct.pack_into('<i',data,0x100+4,0x7fffffff)
        self.assertEqual(prel32_exports(bytes(data)),{})

    def test_public_build_does_not_accept_critical_config_drift(self):
        original={'CONFIG_ARM64':'y','CONFIG_PREEMPT':'y','CONFIG_MODVERSIONS':'n'}
        build_public_driver.check_critical(original,dict(original))
        for key,value in (('CONFIG_PREEMPT','n'),('CONFIG_MODVERSIONS','y')):
            current={**original,key:value}
            with self.assertRaisesRegex(ValueError,'关键配置变化'):
                build_public_driver.check_critical(original,current)


class IntegrationTests(unittest.TestCase):

    def test_staged_sysupgrade_keeps_versioned_filename(self):
        target=os.environ.get('AW1000_TEST_TARGET_ROOT')
        if not target or not shutil.which('qemu-aarch64-static'):self.skipTest('QWRT fwtool and QEMU required')
        from upgrade_image import create_sysupgrade
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);work=p/'work';out=p/'out';work.mkdir();out.mkdir()
            kernel=p/'kernel';squash=p/'squash'
            kernel.write_bytes(b'\xd0\x0d\xfe\xedkernel');squash.write_bytes(b'hsqsrootfs')
            image=create_sysupgrade(Path(target),kernel,squash,out,work,'1.2.3')
            self.assertEqual(image.name,'QWRT-arcadyan-aw1000-1.2.3-sysupgrade.bin')
            self.assertEqual(list(out.iterdir()),[image])
            self.assertFalse((work/image.name).exists())
            verify_tar(image,kernel,squash)

    def test_generated_scan_page_runs_safe_rpc_and_parses_cached_results(self):
        target=os.environ.get('AW1000_TEST_TARGET_ROOT')
        if not target:self.skipTest('built QWRT root required')
        directory=Path(target)/'www/luci-static/resources'
        channel=directory/'view/status/channel_analysis.js'
        harness=r"""
const assert=require('assert'),fs=require('fs');
const source=fs.readFileSync(process.argv[1],'utf8');
const sample='BSS 02:11:22:33:44:55(on ath0)\n\tfreq: 5745\n\tsignal: -60.0 dBm\n\tSSID: cached-test\n';
let scans=[],pendingResolve;
const rpc={declare(o){return (...args)=>{
 if(o.method==='scan'){
  assert.equal(o.object,'aw1000.internet');scans.push(args[0]);
  return new Promise(resolve=>{pendingResolve=resolve});
 }
 return Promise.resolve([]);
}}};
const forbidden=new Proxy({}, {get(){throw Error('scan attempted a filesystem mutation')}});
const page=new Function('view','rpc','fs','_',source)({extend:v=>v},rpc,forbidden,s=>s);
(async()=>{
 const first=page.getScanResultsForRadio({getName:()=> 'wifi0'});
 assert.deepEqual(await page.getScanResultsForRadio({getName:()=> 'wifi1'}),[]);
 assert.deepEqual(scans,['wifi0']);
 pendingResolve({stdout:sample,mode:'cache'});
 const result=await first;
 assert.equal(result.length,1);assert.equal(result[0].ssid,'cached-test');
 assert.equal(result[0].channel,149);assert.equal(result[0].signal,-60);
 assert.equal(page.scanBusy,false);
 const second=page.getScanResultsForRadio({getName:()=> 'wifi1'});
 pendingResolve({stdout:'',mode:'cache'});
 assert.deepEqual(await second,[]);assert.deepEqual(scans,['wifi0','wifi1']);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        p=subprocess.run(['node','-e',harness,str(channel)],text=True,capture_output=True,timeout=10)
        self.assertEqual(p.returncode,0,p.stderr)
        # A parsed empty cache is honest; neither page can silently create a STA.
        for file in (channel,directory/'network.js',directory/'view/network/wireless.js'):
            subprocess.run(['node','--check',str(file)],check=True,capture_output=True)
        text=channel.read_text()
        self.assertNotIn('ap-force',text);self.assertNotIn("'interface','add'",text)
        self.assertNotIn('poll.add(',text)

    def test_internet_page_names_and_excluded_line_status_keep_existing_mode_keys(self):
        menu=json.loads((ROOT/'files/usr/share/luci/menu.d/aw1000-internet.json').read_text())
        self.assertEqual(menu['admin/network/aw1000_internet']['title'],'上网设置')
        ui=ROOT/'files/www/luci-static/resources/view/aw1000/internet.js'
        harness=r'''
const assert=require('assert');
const fs=require('fs');
function E(tag,attrs,children){
  const n={tag,attrs:attrs||{},children:Array.isArray(children)?children.slice():children==null?[]:[children],style:{},value:attrs&&attrs.value||'',textContent:'',
    addEventListener(){},appendChild(c){this.children.push(c)},removeChild(c){this.children.splice(this.children.indexOf(c),1)}};
  Object.defineProperty(n,'firstChild',{get(){return this.children[0]}});return n;
}
const view={extend(v){return v}},rpc={declare(){return ()=>Promise.resolve({})}},poll={add(){}},ui={createHandlerFn(_,f){return f}};
const page=new Function('view','rpc','poll','ui','E','L',fs.readFileSync(process.argv[1],'utf8'))(view,rpc,poll,ui,E,{url:x=>'/'+x});
function text(n){if(n==null)return '';if(Array.isArray(n))return n.map(text).join(' ');if(typeof n==='string')return n;return (n.textContent||'')+' '+text(n.children)}
function find(n,p,out=[]){if(!n||typeof n!=='object')return out;if(Array.isArray(n)){n.forEach(x=>find(x,p,out));return out}if(p(n))out.push(n);find(n.children,p,out);return out}
const keys=['wired_only','cellular_only','wired','cellular'];
const labels=['仅有线网络','仅SIM卡上网（3G/4G/5G）','有线网络优先，断网自动用SIM卡','SIM卡上网优先，断网自动用有线'];
for(const key of keys){
  const s={priority:key,mode:'dhcp',selected:key==='cellular_only'||key==='cellular'?'wwan':'wan',sim:'ready',
    wired_up:true,cellular_up:true,wan_health:key==='cellular_only'?'offline':'online',cell_health:key==='wired_only'?'offline':'online',wan_turn:20,
    tracking:{wan:{role:'recovery',interval:2},cellular:{role:'recovery',interval:2}},ipv6:{}};
  const root=page.render(s),all=text(root);
  const selector=find(root,n=>n.tag==='select')[0];
  assert.deepEqual(selector.children.map(n=>n.attrs.value),keys);
  assert.deepEqual(selector.children.map(n=>text(n).trim()),labels);
  assert.equal(selector.value,key);
  assert(all.includes('上网设置')&&all.includes('线路模式')&&!all.includes('有线接入方式'));
  assert(all.includes('当前上网线路：'+(s.selected==='wan'?'有线网络':'SIM卡网络')));
  const statusLine=find(root,n=>n.tag==='p').map(text).join(' ');
  assert.equal(find(root,n=>n.tag==='select').length,1);
  assert.equal(find(root,n=>n.tag==='input').length,0);
  assert.equal(find(root,n=>n.tag==='details').length,1);
  if(key==='wired_only')assert(statusLine.includes('SIM卡网络：本模式不使用，已停止检测'));
  if(key==='cellular_only')assert(statusLine.includes('有线网络：本模式不使用，已停止检测'));
}
console.log('UI mode labels, stable keys and excluded-line status passed');
'''
        p=subprocess.run(['node','-e',harness,str(ui)],text=True,capture_output=True,timeout=10)
        self.assertEqual(p.returncode,0,p.stderr)

    def test_phone_footer_link_is_removed_but_csv_export_kept(self):
        with tempfile.TemporaryDirectory() as d:
            src=phone.prepare(Path(d)/'phone')/'openwrt/luci-app-fxs/files'
            self.assertNotIn('https://github.com/Perz3us',(src/'phone.js').read_text())
            self.assertNotIn("E('a'",(src/'phone.js').read_text())
            self.assertIn('URL.createObjectURL',(src/'history.js').read_text())
            settings=(src/'settings.js').read_text()
            self.assertNotIn("s.tab('led'",settings)
            self.assertNotIn("'led_ready'",settings)

    def test_package_removal_preserves_shared_dependencies_and_files(self):
        text=('Package: luci-app-ssr-plus\nDepends: libc, mosdns, dns2tcp\n\n'
              'Package: luci-app-modem-data\nDepends: libc, quectel-CM-5G\n\n'
              'Package: luci-app-mosdns\nDepends: mosdns\n\nPackage: mosdns\nDepends: libc\n\n'
              'Package: dns2tcp\nDepends: libc\n\nPackage: libc\n\nPackage: quectel-CM-5G\nDepends: libc\n\n')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);info=root/'usr/lib/opkg/info';info.mkdir(parents=True)
            (root/'usr/lib/opkg/status').write_text(text)
            p=root/'etc/init.d';p.mkdir(parents=True);(p/'shadowsocksr').write_text('service')
            (root/'usr/shared').write_text('keep')
            (info/'luci-app-ssr-plus.list').write_text('/etc/init.d/shadowsocksr\n/usr/shared\n')
            (info/'mosdns.list').write_text('/usr/shared\n')
            rc=root/'etc/rc.d';rc.mkdir();(rc/'S99shadowsocksr').symlink_to('../init.d/shadowsocksr')
            manifest=[]
            self.assertEqual(set(remove_apps(root,manifest)),{'luci-app-ssr-plus','luci-app-modem-data','dns2tcp'})
            self.assertTrue((root/'usr/shared').exists())
            self.assertFalse((rc/'S99shadowsocksr').is_symlink())
            self.assertNotIn('Package: luci-app-ssr-plus',(root/'usr/lib/opkg/status').read_text())
            metadata='/usr/lib/opkg/info/luci-app-ssr-plus.list'
            self.assertIn(metadata,manifest)
            (root/metadata.lstrip('/')).write_text('restored stale record')
            from customize import purge_paths
            purge_paths(root,manifest)
            self.assertFalse((root/metadata.lstrip('/')).exists())

    def test_sysupgrade_contains_separate_kernel_root_and_control(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);k=root/'kernel';r=root/'root';p=root/'upgrade.bin'
            k.write_bytes(b'\xd0\x0d\xfe\xedkernel');r.write_bytes(b'hsqsrootfs')
            write_tar(p,k,r,0);verify_tar(p,k,r)
            r.write_bytes(b'a different rootfs')
            with self.assertRaisesRegex(ValueError,'root 回读失败'):verify_tar(p,k,r)

    def test_aw1000_sysupgrade_override_validates_tar_and_uses_reset_fallback(self):
        script=ROOT/'files/lib/upgrade/zz-aw1000.sh'
        text=script.read_text()
        self.assertNotIn('umount -a',text)
        self.assertIn("CI_UBIPART='rootfs'",text)
        self.assertIn('ubiupdatevol',text)
        self.assertIn('AW1000_SYSRQ_TRIGGER',text)
        self.assertIn('exit 0',text)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);kernel=root/'kernel';squash=root/'root';image=root/'upgrade.bin'
            kernel.write_bytes(b'\xd0\x0d\xfe\xedkernel')
            squash.write_bytes(b'hsqsrootfs')
            write_tar(image,kernel,squash,0)
            harness='''
board_name() { echo arcadyan,aw1000; }
identify_tar() { case "$2" in */kernel) echo fit;; */root) echo squashfs;; esac; }
. "$1"
aw1000_validate_sysupgrade "$2"
'''
            result=subprocess.run(['sh','-c',harness,'test',str(script),str(image)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            wrong=harness.replace('echo arcadyan,aw1000','echo another,board')
            result=subprocess.run(['sh','-c',wrong,'test',str(script),str(image)],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)

            trigger=root/'sysrq-trigger';trigger.touch()
            finish='''
nand_restore_config() { return 0; }
sync() { echo sync >> "$TRACE"; }
sleep() { echo "sleep $*" >> "$TRACE"; }
reboot() {
    echo "reboot $*" >> "$TRACE"
    [ ! -s "$TRIGGER" ] || exit 90
    return 1
}
TRACE="$3"
TRIGGER="$2"
AW1000_SYSRQ_TRIGGER="$2"
. "$1"
aw1000_write_upgrade_marker() { echo marker >> "$TRACE"; }
aw1000_finish_upgrade 12 34
'''
            # Mock all host reset operations; only a regular file receives SysRq.
            trace=root/'trace'
            result=subprocess.run(['sh','-c',finish,'test',str(script),str(trigger),str(trace)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(trigger.read_text(),'b\n')
            self.assertEqual(trace.read_text().splitlines(),['sync','marker','sync','sleep 2','sync','reboot -f','sleep 5'])

    def test_aw1000_primary_reboot_terminates_before_sysrq_fallback(self):
        script=ROOT/'files/lib/upgrade/zz-aw1000.sh'
        with tempfile.TemporaryDirectory() as d:
            trigger=Path(d)/'sysrq-trigger';trigger.touch()
            harness='''
nand_restore_config() { return 0; }
sync() { :; }
sleep() { :; }
reboot() { [ "$*" = '-f' ] || exit 91; exit 42; }
AW1000_SYSRQ_TRIGGER="$2"
. "$1"
aw1000_write_upgrade_marker() { return 0; }
aw1000_finish_upgrade 12 34
'''
            result=subprocess.run(['sh','-c',harness,'test',str(script),str(trigger)],capture_output=True,text=True)
            self.assertEqual(result.returncode,42,result.stderr)
            self.assertEqual(trigger.read_text(),'')

    def test_aw1000_returned_reboot_without_sysrq_hands_off_to_upgraded(self):
        script=ROOT/'files/lib/upgrade/zz-aw1000.sh'
        with tempfile.TemporaryDirectory() as d:
            missing=Path(d)/'absent'/'sysrq-trigger'
            harness='''
nand_restore_config() { return 0; }
sync() { :; }
sleep() { :; }
reboot() { return 0; }
AW1000_SYSRQ_TRIGGER="$2"
. "$1"
aw1000_write_upgrade_marker() { return 0; }
aw1000_finish_upgrade 12 34
exit 92
'''
            result=subprocess.run(['sh','-c',harness,'test',str(script),str(missing)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('handing reset to upgraded',result.stderr)
            self.assertFalse(missing.exists())

    def test_release_publishes_only_both_images_and_checksums(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'VERSION').write_text('1.0.7\n')
            out=root/'out';out.mkdir();work=root/'work/build';work.mkdir(parents=True)
            result={'version':'1.0.7','factory':'QWRT-arcadyan-aw1000-1.0.7-factory.ubi',
                    'sysupgrade':'QWRT-arcadyan-aw1000-1.0.7-sysupgrade.bin',
                    'checksums':'QWRT-arcadyan-aw1000-1.0.7.sha256','phone':{'phone_driver_experimental':True}}
            (work/'result.json').write_text(json.dumps(result))
            for name in (result['factory'],result['sysupgrade'],result['checksums']):(out/name).touch()
            with mock.patch.object(publish_release,'ROOT',root),mock.patch.dict(os.environ,{'GITHUB_RUN_NUMBER':'7','GITHUB_SHA':'test'}),mock.patch.object(publish_release.subprocess,'run') as run:
                publish_release.publish()
                self.assertIn(str(out/result['sysupgrade']),run.call_args.args[0])
                self.assertIn('QWRT-arcadyan-aw1000-1.0.7',run.call_args.args[0])
                (out/'unexpected.txt').touch()
                with self.assertRaisesRegex(ValueError,'额外文件'):publish_release.publish()

    def test_actual_led_script_handles_real_lte_sa_and_numeric_nodes(self):
        lua=shutil.which('lua5.1') or shutil.which('lua');self.assertIsNotNone(lua)
        for rat,reply,dbm in [('lte','+QENG: "servingcell","NOCONN","LTE","FDD",460,00,123,240,3590,8,3,3,123,-78,-7,-53,19,15,80,-',-78),
                              ('sa','+QENG: "servingcell","NOCONN","NR5G-SA","TDD", 460,00,123,83,123,504990,41,12,-87,-12,4,1,-',-87)]:
            harness='''local writes={};local captured
local function fakefile(value,path)
 return {read=function() return value end,close=function() end,write=function(_,v) writes[path]=tostring(v) end}
end
io.open=function(path,mode)
 if mode=='w' then return fakefile('',path) end
 if path=='/tmp/sysinfo/board_name' then return fakefile('arcadyan,aw1000') end
 local n=path:match('^/sys/class/leds/ipq::led(%d+)/max_brightness$')
 if n and tonumber(n)<=12 then return fakefile('1') end
end
io.popen=function() return fakefile('online') end
os.rename=function() return true end
package.preload['uci']=function() return {cursor=function() return {
 unload=function() end,load=function() end,foreach=function(_,_,_,fn)
 fn({name='rg500q-ea',at_port='/dev/ttyUSB3',override_at_port='/dev/ttyUSB2',alias='cellular'}) end} end} end
package.preload['ubus']=function() return {connect=function() return {call=function(_,obj,method,args)
 if obj=='at-daemon' then
  assert(args.at_port=='/dev/ttyUSB2')
  return {status='success',response=args.at_cmd=='AT+CPIN?' and '+CPIN: READY' or REPLY}
 end
 if obj=='network.interface.cellular' then return {up=true,l3_device='wwan0',['ipv4-address']={{address='10.0.0.2'}}} end
 if obj=='network.wireless' then return {radio0={up=true}} end
 return {}
 end,close=function() end} end} end
package.preload['luci.jsonc']=function() return {stringify=function(v) captured=v;return '{}' end} end
package.preload['nixio']=function() return {nanosleep=function()
 assert(captured.rat==RAT and captured.dbm==DBM and captured.internet and captured.wifi)
 assert(writes['/sys/class/leds/ipq::led9/brightness']=='1')
 assert(writes['/sys/class/leds/ipq::led7/brightness']==(RAT=='sa' and '1' or '0'))
 assert(writes['/sys/class/leds/ipq::led10/brightness']==(RAT=='sa' and '1' or '0'))
 assert(writes['/sys/class/leds/ipq::led2/brightness']=='1')
 assert(writes['/sys/class/leds/ipq::led3/brightness']=='0')
 assert(writes['/sys/class/leds/ipq::led4/brightness']=='0')
 assert(writes['/sys/class/leds/ipq::led0/brightness']=='0')
 assert(writes['/sys/class/leds/ipq::led12/brightness']==nil)
 assert(next(captured.missing_leds)==nil)
 os.exit(0)
end} end
'''
            harness='REPLY='+json.dumps(reply)+';RAT='+json.dumps(rat)+';DBM='+str(dbm)+'\n'+harness
            harness+='dofile('+json.dumps(str(ROOT/'files/usr/sbin/aw1000-leds.lua'))+')\n'
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/'harness.lua';p.write_text(harness)
                result=subprocess.run([lua,str(p)],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)

    def test_line_recovery_and_panel_semantics_are_decoupled(self):
        policy=(ROOT/'files/etc/config/mwan3').read_text()
        self.assertEqual(policy.count("option up '4'"),2)
        self.assertNotIn("option up '15'",policy)
        uplink=(ROOT/'files/usr/sbin/aw1000-uplink').read_text()
        self.assertIn('recovery_interval:2 down:2 up:4',uplink)
        panel=(ROOT/'files/usr/sbin/aw1000-leds.lua').read_text()
        self.assertNotIn("if not online then signal='red_blink'",panel)
        self.assertIn("local hard_fault=sim=='blocked'",panel)
        self.assertIn("/var/run/aw1000-mwan-follow/selected",panel)
        self.assertIn('local NO_SERVICE_GRACE=15',panel)
        self.assertIn('local SIGNAL_FAULT_TIMEOUT=60',panel)
        self.assertIn("signal_fault_expired and 'off' or 'red_blink'",panel)
        ui=(ROOT/'files/www/luci-static/resources/view/aw1000/internet.js').read_text()
        self.assertIn('Number(turn||0)<4',ui)
        self.assertIn('连续通过 4 轮检测',ui)

    def test_toolchain_probe_rejects_native_x86_64_binary(self):
        with self.assertRaisesRegex(ValueError,'不是 AArch64'):
            phone.check_toolchain('gcc')

    def test_toolchain_probe_reports_missing_cross_compiler(self):
        with self.assertRaisesRegex(ValueError,'libc6-dev-arm64-cross'):
            phone.check_toolchain('/nonexistent/aw1000-aarch64-gcc')

    def test_real_qdai_reply_is_parsed_by_actual_phone_c_source(self):
        reply=json.loads((ROOT/'tests/fixtures/at-daemon-qdai.json').read_text())
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); harness=p/'qdai.c'; exe=p/'qdai'
            harness.write_text('#include <stdio.h>\n#include "bringup.h"\n'
                               'int main(int argc,char **argv) { if(argc!=2) return 2; '
                               'printf("%d %d\\n",qdai_is_slave(argv[1]),qdai_rate(argv[1])); return 0; }\n')
            src=phone.prepare(p/'phone')/'fxsd/src'
            subprocess.run(['gcc','-ffunction-sections','-fdata-sections','-I'+str(src),
                            str(harness),str(src/'bringup.c'),'-Wl,--gc-sections','-o',str(exe)],check=True)
            for raw,expected in ((reply['response'],'1 1'),
                                 ('+QDAI: x,1,0,4,0,0,2,1,2\r\n','1 0'),
                                 ('+QDAI: x,0,0,4,0,1,2,1,2\r\n','0 1'),
                                 ('ERROR\r\n','-1 -1')):
                with self.subTest(raw=raw):
                    self.assertEqual(subprocess.check_output([str(exe),raw],text=True).strip(),expected)

    def test_phone_at_wrapper_decodes_real_daemon_json_and_rejects_errors(self):
        reply=json.loads((ROOT/'tests/fixtures/at-daemon-qdai.json').read_text())
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); ubus=p/'ubus'
            ubus.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
                            'assert sys.argv[1:5]==["-t","5","call","at-daemon"]\n'
                            'assert sys.argv[5]=="sendat"\n'
                            'assert json.loads(sys.argv[6])=={"at_port":"/dev/ttyUSB3","at_cmd":"AT+QDAI?","timeout":3}\n'
                            'print(os.environ["AW1000_TEST_REPLY"])\n')
            ubus.chmod(0o755)
            env={**os.environ,'PATH':str(p)+os.pathsep+os.environ['PATH']}
            wrapper=ROOT/'files/usr/sbin/aw1000-phone-at'
            for value,ok in ((reply,True),({'status':'error','response':'ERROR'},False),
                             ({'status':'success','response':None},False)):
                env['AW1000_TEST_REPLY']=json.dumps(value)
                result=subprocess.run(['sh',str(wrapper),'/dev/ttyUSB3','AT+QDAI?'],
                                      env=env,capture_output=True,text=True)
                self.assertEqual(result.returncode==0,ok)
                if ok:
                    self.assertEqual(result.stdout.strip(),reply['response'].replace('\r\n','\n').replace('\r','\n').strip())
                    self.assertNotIn('"status"',result.stdout)

    def test_phone_port_removes_new_kernel_apis_and_uses_shared_at_queue(self):
        with tempfile.TemporaryDirectory() as d:
            src=phone.prepare(Path(d)/'phone')
            main=(src/'kernel/aw1000-fxs/fxs_main.c').read_text()
            pcm=(src/'kernel/aw1000-fxs/fxs_pcm.c').read_text()
            self.assertNotIn('timer_delete',main);self.assertNotIn('sched_set_fifo_low',pcm)
            self.assertIn('/usr/sbin/aw1000-phone-at',(src/'fxsd/src/modem.c').read_text())

    def test_all_upstream_phone_ui_literals_are_translated(self):
        import re,ast
        pattern=re.compile(r"_\(('(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")\)")
        tr=phone.translations();missing=set()
        for name,data in phone.upstream_files().items():
            if not name.startswith('openwrt/luci-app-fxs/files/') or not name.endswith('.js'):continue
            for m in pattern.finditer(data.decode()):
                if ast.literal_eval(m[1]) not in tr:missing.add(ast.literal_eval(m[1]))
        self.assertEqual(missing,set())

    def test_no_artifacts_actions_or_wps_factory_reset(self):
        for p in (ROOT/'.github/workflows').glob('*.yml'):
            self.assertNotRegex(p.read_text(),r'uses:\s*actions/(upload|download)-artifact')
        self.assertFalse((ROOT/'files/etc/hotplug.d/button/50-aw1000-wps').exists())
        build=(ROOT/'scripts/build.py').read_text()
        self.assertIn("'50-aw1000-wps'",build)
        self.assertIn('/usr/sbin/aw1000-wps-button',(ROOT/'files/etc/rc.button/wps').read_text())
        helper=(ROOT/'files/usr/sbin/aw1000-wps-button').read_text()
        self.assertNotRegex(helper,r'(?m)^\s*(?:reboot|jffs2reset)\b')

    def test_banner_is_removed_from_image_and_restored_configuration(self):
        build = (ROOT / 'scripts/build.py').read_text()
        self.assertIn("'/etc/banner'", build)
        self.assertIn("'/etc/hotplug.d/button/50-aw1000-wps'", build)
        defaults=(ROOT/'files/etc/uci-defaults/99-aw1000-mod').read_text()
        self.assertIn('rm -f /etc/banner',defaults);self.assertIn('BannerFile',defaults)

    def test_qmodem_and_phone_cannot_compete_with_led_daemon(self):
        from test_qmodem_compat import qmodem_fixture
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);qmodem_fixture(root);q=root/'usr/share/qmodem'
            patch_qmodem(root)
            self.assertIn('aw1000-leds is the sole owner',(q/'modem_dial.sh').read_text())
            scan=(q/'modem_scan.sh').read_text()
            self.assertIn('set qmodem.$section_name.extend_prefix="0"',scan)
            self.assertIn('set qmodem.$section_name.do_not_add_dns="1"',scan)
        defaults=(ROOT/'files/etc/uci-defaults/99-aw1000-mod').read_text()
        self.assertIn('net_led\\|sim_led',defaults);self.assertIn('fxs.main.led=ipq::led12',defaults)
        led=(ROOT/'files/usr/sbin/aw1000-leds.lua').read_text()
        self.assertNotIn('ipq::led_phone',led);self.assertNotIn("led12','on",led)
        phone_script=(ROOT/'scripts/phone.py').read_text()
        self.assertIn("option led 'ipq::led12'",phone_script)
        self.assertIn("option led_ringing 'on'",phone_script)
        self.assertIn("option led_offhook 'on'",phone_script)



class PhoneUpdateTests(unittest.TestCase):
    def test_reused_driver_rejects_kernel_source_changes(self):
        files=phone.upstream_files()
        name='kernel/aw1000-fxs/fxs_main.c'
        files[name]+=b'\n/* changed kernel source */\n'
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(phone,'upstream_files',return_value=files):
                with self.assertRaisesRegex(ValueError,'内核源码已变化'):
                    driver_bundle(ROOT/'vendor/aw1000-phone-driver.tar.gz',Path(folder),allow_experimental=True)

    def test_new_caller_id_fsk_framing_number_format_and_country(self):
        with tempfile.TemporaryDirectory() as folder:
            work=Path(folder);source=phone.prepare(work/'phone')/'fxsd/src'
            harness=r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include "cid.c"
#include "country.h"
struct fixture { unsigned count; uint8_t mode, framing[128], bytes[128]; uint32_t amplitude; };
static int rd(void *ctx, uint8_t ch, uint8_t reg, uint8_t *value) {
    (void)ctx;(void)ch;*value=reg==SI_IRQ1?IRQ1_FSKBUF:0;return 0;
}
static int wr(void *ctx, uint8_t ch, uint8_t reg, uint8_t value) {
    struct fixture *f=ctx;(void)ch;
    if(reg==SI_OMODE)f->mode=value;
    if(reg==SI_FSKDAT){assert(f->count<128);f->framing[f->count]=f->mode;f->bytes[f->count++]=value;}
    return 0;
}
static int ram(void *ctx, uint8_t ch, uint16_t reg, uint32_t value) {
    (void)ch;if(reg==RAM_FSKAMP0)((struct fixture *)ctx)->amplitude=value;return 0;
}
static void delay(void *ctx,unsigned ms){(void)ctx;(void)ms;}
int main(void) {
    const struct country *country=country_find(460);
    assert(country && !strcmp(country->cc,"86") && !strcmp(country->intl,"00"));
    assert(!country_find(999));
    char number[64];cid_local("86","","00");
    assert(!strcmp(local_number("+8613800138000",number,sizeof(number)),"13800138000"));
    assert(!strcmp(local_number("008613800138000",number,sizeof(number)),"13800138000"));
    assert(!strcmp(local_number("+442071234567",number,sizeof(number)),"00442071234567"));
    assert(!strcmp(local_number("13800138000",number,sizeof(number)),"13800138000"));
    const struct fsk_cfg *configs[]={&bell202,&v23};
    for(unsigned c=0;c<2;c++) {
        const struct fsk_cfg *cfg=configs[c];struct fixture fixture={0};
        struct slic slic={.ctx=&fixture,.rd=rd,.wr=wr,.wrram=ram,.delay=delay};
        uint8_t message[]={0x80,0x12,0x34};
        assert(!fsk_send(&slic,1,cfg,message,sizeof(message)));
        unsigned preamble=cfg->seizure+cfg->mark+1;
        assert(fixture.count==preamble+sizeof(message)+1);
        for(unsigned i=0;i<preamble;i++)assert(fixture.framing[i]&OMODE_FSK8);
        for(unsigned i=preamble;i<fixture.count;i++)assert(!(fixture.framing[i]&OMODE_FSK8));
        assert(!memcmp(fixture.bytes+preamble,message,sizeof(message)));
        assert(!(fixture.mode&0x08));assert(fixture.amplitude==cfg->amp[0]);
    }
    struct tm stamp={.tm_mon=10,.tm_mday=5,.tm_hour=10,.tm_min=30};uint8_t bytes[CID_MSG_MAX];
    int length=cid_mdmf("13800138000",&stamp,bytes,sizeof(bytes));assert(length>0);
    unsigned checksum=0;for(int i=0;i<length;i++)checksum+=bytes[i];assert(!(checksum&255));
    return 0;
}
'''
            fixture=work/'caller-id.c';fixture.write_text(harness);executable=work/'caller-id'
            subprocess.run(['gcc','-std=gnu11','-ffunction-sections','-fdata-sections','-I'+str(source),
                            str(fixture),str(source/'country.c'),'-Wl,--gc-sections','-o',str(executable)],check=True)
            subprocess.run([str(executable)],check=True)

    def test_new_dnd_schedule_allowlist_repeat_call_and_phone_off(self):
        with tempfile.TemporaryDirectory() as folder:
            work=Path(folder);source=phone.prepare(work/'phone')/'fxsd/src'
            harness=r'''
#include <assert.h>
#include <time.h>
static time_t fixed_time;
static time_t fixture_time(time_t *out){if(out)*out=fixed_time;return fixed_time;}
#define time fixture_time
#define main daemon_main
#include "fxsd_main.c"
#undef main
#undef time
int main(void) {
    setenv("TZ","UTC",1);tzset();
    struct fxsd state={0};assert(!dnd_now());
    dnd_parse("22:00,07:00,3");
    fixed_time=21*3600;assert(!dnd_now());
    fixed_time=22*3600;assert(dnd_now());
    fixed_time=6*3600+59*60;assert(dnd_now());
    fixed_time=7*3600;assert(!dnd_now());
    dnd_parse("25:00,07:00,3");assert(dnd_from==22*60);
    dnd_parse("00:00,00:00,3");assert(dnd_now());
    strcpy(state.line.peer,"13800138000");
    fixed_time=100000;assert(dnd_silence(&state));
    fixed_time+=60;assert(!dnd_silence(&state));
    fixed_time+=300;assert(dnd_silence(&state));
    strcpy(allow[0],"13800138000");nallow=1;assert(!dnd_silence(&state));
    nallow=0;state.test_ring=1;assert(!dnd_silence(&state));state.test_ring=0;
    state.line.recall=1;assert(!dnd_silence(&state));state.line.recall=0;
    phone_off=1;assert(led_want(&state)==LED_OFF);
    return 0;
}
'''
            fixture=work/'dnd.c';fixture.write_text(harness);executable=work/'dnd'
            subprocess.run(['gcc','-std=gnu11','-ffunction-sections','-fdata-sections','-I'+str(source),
                            str(fixture),'-Wl,--gc-sections','-o',str(executable)],check=True)
            subprocess.run([str(executable)],check=True)

    def test_new_driver_manifest_matches_pinned_source_and_audio_api(self):
        with tarfile.open(ROOT/'vendor/aw1000-phone-driver.tar.gz') as t:
            meta=json.loads(t.extractfile('manifest.json').read())
            module=t.extractfile('aw1000-fxs.ko').read()
        self.assertEqual(meta['version'],'1.2.5')
        lock=json.loads((ROOT/'sources.lock.json').read_text())
        self.assertEqual(meta['phone_commit'],lock['bundled_phone_driver']['phone_commit'])
        self.assertEqual(hashlib.sha256(module).hexdigest(),meta['sha256'])
        self.assertEqual(meta['struct_module_size'],meta['original_struct_module_size'])
        self.assertEqual(len(meta['import_checks']),60)
        self.assertFalse(meta['hardware_verified'])
        self.assertIn(b'fxs0audio\0',module)
        header=phone.upstream_files()['kernel/aw1000-fxs/fxs_uapi.h']
        self.assertIn(b'FXS_AUDIO_STAT',header)

    def test_static_library_sources_are_complete_and_corruption_is_rejected(self):
        files=phone.library_files()
        self.assertEqual(len(files),171)
        self.assertIn('libubox/assert.h',files)
        self.assertIn('ubus/libubus-io.c',files)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'vendor').mkdir()
            (p/'sources.lock.json').write_bytes((ROOT/'sources.lock.json').read_bytes())
            (p/'vendor/phone-libs.tar.xz').write_bytes(b'broken')
            with mock.patch.object(phone,'ROOT',p),self.assertRaisesRegex(ValueError,'SHA256'):
                phone.library_files()

    def test_upstream_call_drop_regression(self):
        with tempfile.TemporaryDirectory() as d:
            src=phone.prepare(Path(d)/'phone')
            subprocess.run(['make','-C',str(src/'fxsd'),'CC=gcc','test'],check=True,
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE)

    def test_cadence_resampling_and_sip_ownership(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);src=phone.prepare(p/'phone')/'fxsd/src'
            text=(src/'fxsd_main.c').read_text()
            cadence=text[text.index('static void ring_parse('):text.index('static uint16_t ring_ticks(')]
            harness=r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "pcmsock.h"
#include "voip.h"
static unsigned int ring_cad[4]={2000,4000,0,0};
CADENCE
int main(void) {
    ring_parse("1000,4000");assert(ring_cad[0]==1000 && !ring_cad[2]);
    ring_parse("400,200,400,2000");assert(ring_cad[2]==400 && ring_cad[3]==2000);
    ring_parse("99,4000");assert(ring_cad[0]==400);
    ring_parse("2000,4000");assert(ring_cad[0]==2000 && !ring_cad[2]);
    struct pcm_down a={0}, b={0};
    int16_t in[]={1000,2000,3000,4000}, out[8]={0}, split[8]={0};
    assert(pcm_downsample(&a,in,4,out)==2);
    assert(pcm_downsample(&b,in,1,split)==0);
    assert(pcm_downsample(&b,in+1,3,split)==2);
    assert(!memcmp(out,split,4));
    struct pcm_up up={0};
    assert(pcm_upsample(&up,in,2,out)==4);
    assert(out[0]==500 && out[1]==1000 && out[2]==1500 && out[3]==2000);
    struct voip_snap s={0};struct clcc_call c;
    voip_snap_set(&s,"incoming_ringing","cellular","none","12345678",1);
    assert(voip_calls(&s,&c)==1 && c.stat==CLCC_INCOMING && !voip_sip_audio(&s));
    voip_snap_set(&s,"active","cellular","rj11","12345678",1);
    assert(voip_calls(&s,&c)==1 && c.stat==CLCC_ACTIVE && !voip_sip_audio(&s));
    voip_snap_set(&s,"active","cellular","sip:1","12345678",1);
    assert(voip_calls(&s,&c)==0 && voip_sip_audio(&s));
    voip_snap_set(&s,"idle",NULL,NULL,NULL,1);
    assert(!voip_calls(&s,&c) && !voip_sip_audio(&s));
    return 0;
}
'''.replace('CADENCE',cadence)
            c=p/'audio.c';c.write_text(harness);exe=p/'audio'
            subprocess.run(['gcc','-std=gnu11','-ffunction-sections','-fdata-sections','-I'+str(src),
                            str(c),str(src/'pcmsock.c'),str(src/'voip_map.c'),'-pthread',
                            '-Wl,--gc-sections','-o',str(exe)],check=True)
            subprocess.run([str(exe)],check=True)

    def test_system_log_omits_numbers_but_call_history_retains_them(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);src=phone.prepare(p/'phone')/'fxsd/src/fxsd_main.c';text=src.read_text()
            fn=text[text.index('static void op_log('):text.index('static int op_elsewhere(')]
            harness=r'''
#include <assert.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <syslog.h>
#include <time.h>
#define CALL_LOG "LOG_PATH"
#define CALL_LOG_MAX 2000
static char message[128];
static void msg(int priority,const char *fmt,...) {
    (void)priority;va_list ap;va_start(ap,fmt);vsnprintf(message,sizeof(message),fmt,ap);va_end(ap);
}
static void trim_log(void) {}
FUNCTION
int main(void) {
    char line[256];op_log(NULL,"answered","12345678901");
    assert(!strcmp(message,"call answered") && !strstr(message,"12345678901"));
    FILE *f=fopen(CALL_LOG,"r");assert(f && fgets(line,sizeof(line),f));fclose(f);
    assert(strstr(line,"answered 12345678901"));return 0;
}
'''.replace('LOG_PATH',str(p/'calls.log')).replace('FUNCTION',fn)
            c=p/'log.c';c.write_text(harness);exe=p/'log'
            subprocess.run(['gcc',str(c),'-o',str(exe)],check=True)
            subprocess.run([str(exe)],check=True)

    def test_queued_urc_and_sip_events_use_real_blob_payloads_and_socket_fallback(self):
        if not shutil.which('aarch64-linux-gnu-gcc') or not shutil.which('qemu-aarch64-static'):
            self.skipTest('ARM64 compiler and qemu required')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);src=phone.prepare(p/'phone')/'fxsd/src'
            include,libs=phone.build_libraries(p/'client-libs')
            harness=r'''
#include <assert.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include <libubus.h>
#include <libubox/blobmsg.h>
#include "voip.h"
#include "urc.h"
static struct ubus_context contexts[4];
static struct ubus_event_handler *handlers[4];
static int occupied[4],pending[4],legacy,backend_off,legacy_hits;
static void fixture_event(struct uloop_fd *u,unsigned int flags);
static int index_of(struct ubus_context *c) { return (int)(c-contexts); }
struct ubus_context *fixture_connect(const char *path) {
    if (legacy && !strcmp(path,"/var/run/ubus/ubus.sock")) return NULL;
    assert(!strcmp(path,legacy ? "/var/run/ubus.sock" : "/var/run/ubus/ubus.sock"));
    legacy_hits+=legacy;
    for(int i=0;i<4;i++) if(!occupied[i]) {
        memset(&contexts[i],0,sizeof(contexts[i]));occupied[i]=1;
        contexts[i].sock.fd=100+i;contexts[i].sock.cb=fixture_event;return &contexts[i];
    }
    assert(0);return NULL;
}
void fixture_free(struct ubus_context *c) {
    int i=index_of(c);occupied[i]=pending[i]=0;handlers[i]=NULL;
}
int fixture_register(struct ubus_context *c,struct ubus_event_handler *h,const char *pattern) {
    assert(!strcmp(pattern,"qmodem_voip.call") || !strcmp(pattern,"qmodem.at.line"));
    handlers[index_of(c)]=h;return 0;
}
int fixture_lookup(struct ubus_context *c,const char *name,uint32_t *id) {
    (void)c;assert(!strcmp(name,"qmodem_voip"));*id=1;return 0;
}
int fixture_invoke(struct ubus_context *c,uint32_t id,const char *method,struct blob_attr *msg,
                   ubus_data_handler_t cb,void *priv,int timeout,int fd) {
    (void)c;(void)priv;assert(id==1 && timeout==4000 && fd==-1);
    if(backend_off)return UBUS_STATUS_NOT_FOUND;
    struct blob_buf b={0};blob_buf_init(&b,0);
    if(!strcmp(method,"dial")) {
        const struct blobmsg_policy p[]={{.name="endpoint",.type=BLOBMSG_TYPE_STRING},
                                        {.name="number",.type=BLOBMSG_TYPE_STRING}};
        struct blob_attr *a[2];blobmsg_parse(p,2,a,blob_data(msg),blob_len(msg));
        assert(a[0] && a[1] && !strcmp(blobmsg_get_string(a[0]),"rj11"));
        assert(!strcmp(blobmsg_get_string(a[1]),"12345678"));
        blobmsg_add_string(&b,"state","active");blobmsg_add_string(&b,"answer_owner","rj11");
    } else {
        assert(!strcmp(method,"status"));blobmsg_add_string(&b,"state","incoming_ringing");
        blobmsg_add_string(&b,"answer_owner","none");
    }
    blobmsg_add_string(&b,"origin","cellular");blobmsg_add_string(&b,"remote_number","12345678");
    blobmsg_add_u8(&b,"enabled",1);cb(NULL,0,b.head);blob_buf_free(&b);return 0;
}
int fixture_poll(struct pollfd *fds,nfds_t n,int timeout) {
    assert(n==1 && timeout==0);int i=fds[0].fd-100;assert(i>=0 && i<4);
    fds[0].revents=pending[i] ? POLLIN : 0;return !!pending[i];
}
static void fixture_event(struct uloop_fd *u,unsigned int flags) {
    assert(flags==ULOOP_READ);int i=u->fd-100;struct ubus_context *c=&contexts[i];assert(handlers[i] && pending[i]);struct blob_buf b={0};blob_buf_init(&b,0);
    if(pending[i]==1) {
        blobmsg_add_string(&b,"raw_line","NO CARRIER");
        handlers[i]->cb(c,handlers[i],"qmodem.at.line",b.head);
    } else {
        blobmsg_add_string(&b,"state","active");blobmsg_add_string(&b,"origin","cellular");
        blobmsg_add_string(&b,"answer_owner","sip:1");blobmsg_add_string(&b,"remote_number","12345678");
        blobmsg_add_u8(&b,"enabled",1);handlers[i]->cb(c,handlers[i],"qmodem_voip.call",b.head);
    }
    pending[i]=0;blob_buf_free(&b);
}
int main(void) {
    for(legacy=0;legacy<=1;legacy++) {
        assert(!voip_connect() && voip_connected() && voip_snap()->st==VS_RINGING);
        assert(!voip_request("dial","12345678") && voip_snap()->owner==VO_RJ11);
        assert(!urc_start());
        pending[2]=1;assert(urc_released() && !urc_released());
        pending[0]=2;voip_events();assert(voip_sip_audio(voip_snap()));
        assert(voip_changed() && !voip_changed());
        backend_off=1;assert(voip_refresh()<0 && !voip_connected());backend_off=0;
        assert(!voip_connect() && voip_connected());
        urc_stop();voip_disconnect();assert(!voip_connected());
    }
    assert(legacy_hits>=3);return 0;
}
'''
            c=p/'events.c';c.write_text(harness);exe=p/'events'
            renames={'ubus_connect':'fixture_connect','ubus_free':'fixture_free',
                     'ubus_register_event_handler':'fixture_register','ubus_lookup_id':'fixture_lookup',
                     'ubus_invoke_fd':'fixture_invoke','poll':'fixture_poll'}
            subprocess.run(['aarch64-linux-gnu-gcc','-static','-std=gnu11',
                            *['-D'+a+'='+b for a,b in renames.items()],
                            '-I'+str(src),'-I'+str(include),str(c),str(src/'voip.c'),str(src/'urc.c'),
                            str(src/'voip_map.c'),str(libs[1]),'-o',str(exe)],check=True)
            subprocess.run(['qemu-aarch64-static',str(exe)],check=True,timeout=10)



class PhoneLedTests(unittest.TestCase):
    def test_generated_init_uses_physical_off_for_numeric_and_named_leds(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            source = phone.prepare(work/'phone')
            binaries = source/'fxsd/build'; binaries.mkdir()
            for name in ('fxsd', 'fxs-bringup'):
                (binaries/name).write_bytes(b'fixture executable')
            root = work/'root'
            for path in ('usr/sbin', 'etc/config', 'etc/init.d'):
                (root/path).mkdir(parents=True)
            with mock.patch.object(phone.subprocess, 'run'), mock.patch.object(phone, 'check_static_elf'):
                phone.install(root, source)
            sysfs = work/'leds'
            for name in ('ipq::led12', 'green:phone'):
                node = sysfs/name; node.mkdir(parents=True)
                (node/'brightness').write_text('99')
                (node/'max_brightness').write_text('255\n')
                (node/'trigger').write_text('timer')
            init = (root/'etc/init.d/fxsd').read_text()
            self.assertEqual(init.count('phone_led_off "$led"'), 2)
            fixture = work/'fxsd-init.sh'
            fixture.write_text(init.replace('/sys/class/leds', str(sysfs))+'\nphone_led_off "$1"\n')
            subprocess.run(['sh', '-n', str(fixture)], check=True)
            for name, expected in (('ipq::led12', '255'), ('green:phone', '0')):
                subprocess.run(['sh', str(fixture), name], check=True)
                self.assertEqual((sysfs/name/'brightness').read_text().strip(), expected)
                self.assertEqual((sysfs/name/'trigger').read_text().strip(), 'none')

    def test_fxsd_corrects_qwrt_phone_polarity_and_preserves_named_leds(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            source = phone.prepare(work/'phone')/'fxsd/src/fxsd_main.c'
            text = source.read_text()
            led = text[text.index('static void led_write('):text.index('static int led_want(')]
            root = work/'leds'
            for name in ('ipq::led12', 'green:phone'):
                (root/name).mkdir(parents=True)
            # Compile the shipped C functions, keeping their reads and writes real.
            harness = r'''
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define LED_DIR "LED_ROOT/"
#define LED_NIGHT "LED_ROOT/night"
static const char *led_name = "ipq::led12";
enum { LED_OFF, LED_ON, LED_SLOW, LED_FAST };
static int led_cur = -1;
LED_FUNCTIONS
static void check(const char *file, const char *expected) {
    char actual[512];
    assert(!led_read(file, actual, sizeof(actual)));
    assert(!strcmp(actual, expected));
}
int main(void) {
    led_write("max_brightness", "255\n");
    led_set(LED_ON);
    check("brightness", "0");
    // Target LEDC inversion means physical on is raw zero for phone only.
    led_write("trigger", "[none]");
    led_write("brightness", "255");
    led_update(LED_ON, 1);
    check("brightness", "0");
    led_set(LED_FAST);
    check("brightness", "255");
    check("trigger", "timer");
    check("delay_on", "120");
    check("delay_off", "120");
    led_set(LED_SLOW);
    check("brightness", "255");
    check("delay_on", "600");
    led_set(LED_OFF);
    check("brightness", "255");
    // Binary LED drivers and malformed metadata use a bounded safe fallback.
    const char *maxima[] = { "1\n", "0", "-1", "65536", "garbage", "" };
    for (unsigned i = 0; i < sizeof(maxima)/sizeof(maxima[0]); i++) {
        led_write("max_brightness", maxima[i]);
        led_set(LED_OFF);
        check("brightness", "1");
    }
    // Modern named LED drivers already provide correct physical polarity.
    led_name = "green:phone";
    led_write("max_brightness", "255\n");
    led_set(LED_ON);
    check("brightness", "255");
    led_write("trigger", "[none]");
    led_write("brightness", "1");
    led_update(LED_ON, 1);
    check("brightness", "255");
    led_set(LED_OFF);
    check("brightness", "0");
    return 0;
}
'''.replace('LED_ROOT', str(root)).replace('LED_FUNCTIONS', led)
            c = work/'phone-led.c'; c.write_text(harness)
            executable = work/'phone-led'
            subprocess.run(['gcc', '-std=gnu11', '-Wall', '-Wextra', str(c), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)



if __name__=='__main__':unittest.main()
