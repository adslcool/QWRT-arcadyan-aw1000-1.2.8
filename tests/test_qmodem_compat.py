"""Run the patched QModem lifecycle and QMI argument generation in isolation."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from customize import patch_qmodem

# Pinned original QWRT service and QMI command builder. No router or downloaded
# firmware is needed to execute these regression cases in the source-only CI.
NETWORK_INIT = r'''#!/bin/sh /etc/rc.common
START=99
STOP=13
USE_PROCD=1
service="$(basename ${basescript:-$initscript})"
#脚本目录
SCRIPT_DIR="/usr/share/qmodem"

#运行目录
MODEM_RUNDIR="/var/run/qmodem"

extra_command "dial" "<modem_cfg> run dial script"
extra_command "hang" "<modem_cfg> run hang script"
extra_command "redial" "<modem_cfg> stop previous dial script. run hang script. and run dial script"
extra_command "modem_status" "<modem_cfg> Check dial script status" 


service_triggers()
{
	procd_add_reload_trigger "qmodem"

}

start_service(){
    force_stop=0
    mkdir -p $MODEM_RUNDIR
    update_modem
}

stop_service(){
    force_stop=1
    update_modem
    
}

reload_service()
{
    force_stop=0
    mkdir -p $MODEM_RUNDIR
    sleep 1
    update_modem
}


update_modem(){
    config_load qmodem
    config_get g_enabled main enable_dial
    config_foreach _update_modem modem-device
}

dial()
{
    rc_procd _dial $1
}

hang()
{
    _hang $1
}

modem_status()
{
    _modem_status $1
}

redial()
{
    hang $1
    dial $1
}

_update_modem()
{
    local target
    target=1
    config_get enabled $1 enable_dial
    config_get state $1 state
    if [ "$state" = "disabled" ] || [ "$enabled" = "0" ] || [ "$g_enabled" = "0" ] || [ "$force_stop" = "1" ];then
        target=0
    fi
    data=$(_procd_ubus_call list | jq -r  ".$service.instances.modem_$1")
    [ "$data" == "null" ] && current_status=0 || current_status=1
    logger -t qmodem_network "cfg:$1 current:$current_status;target:$target "
    if [ "$current_status" != "$target" ];then
        [ "$target" -eq 1 ] && dial $1 || hang $1
        logger -t qmodem_network "update modem cfg: $1 dial:$target"
    fi
}

_dial()
{
    procd_open_instance "modem_$1"
    procd_set_param command "/usr/share/qmodem/modem_dial.sh" "$1" dial
    procd_set_param respawn 120 15 10
    procd_close_instance
    logger -t qmodem_network "Modem $1 Start Dial Now"
}

_hang()
{
    procd_kill $service "modem_$1"
    /usr/share/qmodem/modem_dial.sh "$1" hang
    logger -t qmodem_network "Modem $1 Stop Dial and Hang"
}

_modem_status()
{ 
	local instances="modem_$1"
	local data
    data=$(_procd_ubus_call list | jq  -r ".$service.instances.$instances")
    [ "$data" == "null" ] && echo "$instances Not Running"  || echo "$data"
}
'''
QMI_DIAL = r'''qmi_dial()
{
    cmd_line="quectel-CM"
    [ -e "/usr/bin/quectel-CM-M" ] && cmd_line="quectel-CM-M" && tom_modified=1
    case $pdp_type in
        "ip") cmd_line="$cmd_line -4" ;;
        "ipv6") cmd_line="$cmd_line -6" ;;
        "ipv4v6") cmd_line="$cmd_line -4 -6" ;;
        *) cmd_line="$cmd_line -4 -6" ;;
    esac

    if [ "$network_bridge" = "1" ]; then
        cmd_line="$cmd_line -b"
    fi
    if [ -n "$pdp_index" ] && [ "$userset_pdp_index" = "1" ]; then
        cmd_line="$cmd_line -n $pdp_index"
    fi
    if [ "$manufacturer" = "telit" ];then
        test_apn="cbnet"
        [ "$apn" = "cbnet" ] && test_apn="auto"
        $cmd_line -s $test_apn &
        telit_cmd_pid=$!
        sleep 1
        kill $telit_cmd_pid 2>/dev/null
        wait $telit_cmd_pid 2>/dev/null
    fi
    if [ -n "$apn" ]; then
        cmd_line="$cmd_line -s $apn"
    fi
    if [ -n "$username" ]; then
        cmd_line="$cmd_line $username"
    fi
    if [ -n "$password" ]; then
        cmd_line="$cmd_line $password"
    fi
    if [ "$auth" != "none" ]; then
        cmd_line="$cmd_line $auth"
    fi
    if [ -n "$modem_netcard" ]; then
    qmi_if=$modem_netcard
    #if is wwan* ,use the first part of the name
    if  [[ "$modem_netcard" = "wwan"* ]];then
        qmi_if=$(echo "$modem_netcard" | cut -d_ -f1)
    fi
    #if is rmnet* ,use the first part of the name
    if [[ "$modem_netcard" = "rmnet"* ]];then
        qmi_if=$(echo "$modem_netcard" | cut -d. -f1)
    fi
        cmd_line="${cmd_line} -i ${qmi_if}"
    fi
    if [ "$en_bridge" = "1" ];then
        cmd_line="${cmd_line} -b"
    fi
    if [ "$do_not_add_dns" = "1" ];then
        cmd_line="${cmd_line} -D"
    fi
    if [ -e "/usr/bin/quectel-CM-M" ];then
        [ -n "$metric" ] && cmd_line="$cmd_line -d -M $metric"
        [ "$force_set_apn" == "1" ] && cmd_line="$cmd_line -F"
    else
        [ -n "$metric" ] && cmd_line="$cmd_line"
    fi
    cmd_line="$cmd_line -f $log_file"
    while true; do
        m_debug "dialing: $cmd_line"
        $cmd_line
        m_debug "quectel-CM exited, retrying dial"
    done
}
'''

UDHCPC_SCRIPT = r'''#!/bin/sh
[ -z "$1" ] && echo "Error: should be run by udhcpc" && exit 1

set_classless_routes() {
	local max=128
	local type
	while [ -n "$1" -a -n "$2" -a $max -gt 0 ]; do
		[ ${1##*/} -eq 32 ] && type=host || type=net
		echo "udhcpc: adding route for $type $1 via $2"
		route add -$type "$1" gw "$2" dev "$interface"
		max=$(($max-1))
		shift 2
	done
}

setup_interface() {
	echo "udhcpc: ifconfig $interface $ip netmask ${subnet:-255.255.255.0} broadcast ${broadcast:-+}"
	ifconfig $interface $ip netmask ${subnet:-255.255.255.0} broadcast ${broadcast:-+}

	[ -n "$router" ] && [ "$router" != "0.0.0.0" ] && [ "$router" != "255.255.255.255" ] && {
		echo "udhcpc: setting default routers: $router"

		local valid_gw=""
		for i in $router ; do
			route add default gw $i dev $interface
			valid_gw="${valid_gw:+$valid_gw|}$i"
		done

		eval $(route -n | awk '
			/^0.0.0.0\W{9}('$valid_gw')\W/ {next}
			/^0.0.0.0/ {print "route del -net "$1" gw "$2";"}
		')
	}

	# CIDR STATIC ROUTES (rfc3442)
	[ -n "$staticroutes" ] && set_classless_routes $staticroutes
	[ -n "$msstaticroutes" ] && set_classless_routes $msstaticroutes
}

applied=
case "$1" in
	deconfig) ifconfig "$interface" 0.0.0.0;;
	renew) setup_interface update;;
	bound) setup_interface ifup;;
esac

[ -f /etc/udhcpc.user ] && . /etc/udhcpc.user
exit 0
'''


def qmodem_fixture(root):
    root=Path(root)
    q=root/'usr/share/qmodem';q.mkdir(parents=True)
    (q/'modem_scan.sh').write_text('set qmodem.$section_name.extend_prefix="1"\nset qmodem.$section_name.pdp_type="ipv4v6"\n')
    (q/'modem_ctrl.sh').write_text('use_ubus=$(uci get qmodem.$config_section.use_ubus)\n[ $use_ubus -eq 1 ]\n')
    (q/'modem_util.sh').write_text('# Copyright (C) 2024 Tom <fjrcn@outlook.com>\ntom_modem -d $at_port -o a -c "$atcmd" -t 1\n')
    (q/'modem_dial.sh').write_text('set_led()\n{\n    local type=$1\n}\n'+QMI_DIAL)
    udhcpc=root/'usr/share/udhcpc';udhcpc.mkdir(parents=True)
    (udhcpc/'default.script').write_text(UDHCPC_SCRIPT)
    init=root/'etc/init.d/qmodem_network';init.parent.mkdir(parents=True)
    init.write_text(NETWORK_INIT)
    return init


class QModemCompatTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.init=qmodem_fixture(self.root)
        patch_qmodem(self.root)

    def test_qmi_no_unsupported_flag_with_nonempty_then_and_unchanged_pdp(self):
        script=self.root/'usr/share/qmodem/modem_dial.sh'
        text=script.read_text()
        self.assertNotIn('cmd_line="${cmd_line} -D"',text)
        subprocess.run(['bash','-n',str(script)],check=True)
        function=text[text.index('qmi_dial()\n{'):]
        # capture exits after the first attempt: no retry loop or modem access.
        function=function.replace('cmd_line="quectel-CM"','cmd_line="capture"',1)
        for pdp,expected in [('ip',['-4']),('ipv6',['-6']),('ipv4v6',['-4','-6'])]:
            with self.subTest(pdp=pdp):
                harness='''capture() { printf '%s\\n' "$@"; exit 0; }
m_debug() { :; }
auth=none
modem_netcard=wwan0_1
do_not_add_dns=1
metric=20
log_file=/tmp/isolated-log
'''+ 'pdp_type='+pdp+'\n'+function+'\nqmi_dial\n'
                result=subprocess.run(['bash','-c',harness],text=True,capture_output=True,check=True,timeout=3)
                self.assertEqual(result.stdout.splitlines(),expected+['-i','wwan0','-f','/tmp/isolated-log'])

    def test_qconnectmanager_udhcpc_leaves_wwan_defaults_to_netifd(self):
        script=self.root/'usr/share/udhcpc/default.script'
        subprocess.run(['sh','-n',str(script)],check=True)
        bindir=self.root/'bin';bindir.mkdir();events=self.root/'route-events'
        for name in ('route','ifconfig'):
            p=bindir/name
            p.write_text('#!/bin/sh\nprintf "%s %s\\n" "${0##*/}" "$*" >> "$EVENTS"\n')
            p.chmod(0o755)
        base={**os.environ,'PATH':str(bindir)+os.pathsep+os.environ['PATH'],'EVENTS':str(events),
              'ip':'10.243.4.52','subnet':'255.255.255.248','broadcast':'10.243.4.55',
              'router':'10.243.4.53','staticroutes':'0.0.0.0/0 10.243.4.53','msstaticroutes':''}
        subprocess.run(['sh',str(script),'bound'],env={**base,'interface':'wwan0_1'},check=True,
                       text=True,capture_output=True)
        mobile=events.read_text().splitlines()
        self.assertTrue(any(line.startswith('ifconfig wwan0_1 ') for line in mobile))
        self.assertFalse(any(line.startswith('route ') for line in mobile))
        events.unlink()
        subprocess.run(['sh',str(script),'bound'],env={**base,'interface':'eth9','staticroutes':''},check=True,
                       text=True,capture_output=True)
        normal=events.read_text()
        self.assertIn('route add default gw 10.243.4.53 dev eth9',normal)

    def lifecycle(self,action,enabled=('1','1'),running=(),global_enabled='1'):
        events=self.root/'events'
        events.unlink(missing_ok=True)
        env={**os.environ,'EVENTS':str(events),'PATCHED_INIT':str(self.init),
             'RUN_DIR':str(self.root/'run'),'GLOBAL_ENABLED':global_enabled,
             'ENABLED_4':enabled[0],'ENABLED_7':enabled[1],
             'RUNNING_JSON':json.dumps({'qmodem_network':{'instances':{
                 'modem_'+name:{'running':True} for name in running}}})}
        harness=r'''
service=qmodem_network
initscript=/etc/init.d/qmodem_network
context=0
instance=0
extra_command() { :; }
logger() { :; }
sleep() { :; }
config_load() { :; }
config_get() {
 local value=''
 case "$2.$3" in
  main.enable_dial) value="$GLOBAL_ENABLED";;
  4_1.enable_dial) value="$ENABLED_4";;
  7_1.enable_dial) value="$ENABLED_7";;
  *.state) value=enabled;;
 esac
 printf -v "$1" '%s' "$value"
}
config_foreach() { "$1" 4_1; "$1" 7_1; }
ubus() {
 [ "$1 $2 $3" = 'call service list' ] || exit 81
 printf '%s' "$4" | jq -e '. == {"name":"qmodem_network"}' >/dev/null || exit 82
 printf '%s\n' "$RUNNING_JSON"
}
# Model the actual rc.common transaction boundaries. A nested open or a kill
# clearing the parent JSON is rejected just as malformed service data would be.
procd_open_service() {
 [ "$context" = 0 ] || { echo nested-procd >&2; exit 91; }
 context=1
 printf 'OPEN %s\n' "$1" >> "$EVENTS"
}
procd_close_service() {
 [ "$context" = 1 ] && [ "$instance" = 0 ] || { echo invalid-procd-context >&2; exit 92; }
 printf 'CLOSE %s\n' "${1:-set}" >> "$EVENTS"
 context=0
}
procd_open_instance() {
 [ "$context" = 1 ] && [ "$instance" = 0 ] || exit 93
 instance=1
 printf 'INSTANCE %s\n' "$1" >> "$EVENTS"
}
procd_set_param() {
 [ "$context" = 1 ] && [ "$instance" = 1 ] || exit 94
 printf 'PARAM %s\n' "$*" >> "$EVENTS"
}
procd_close_instance() { instance=0; }
procd_kill() {
 # _procd_kill/json_init/json_cleanup resets its JSON namespace.
 context=0
 printf 'KILL %s %s\n' "$1" "$2" >> "$EVENTS"
}
rc_procd() {
 local method=set
 [ -n "$2" ] && method=add
 procd_open_service "$service"
 "$@"
 procd_close_service "$method"
}
start() { rc_procd start_service "$@"; }
. "$PATCHED_INIT"
MODEM_RUNDIR="$RUN_DIR"
_hang() {
 procd_kill "$service" "modem_$1"
 printf 'HANG %s\n' "$1" >> "$EVENTS"
}
'''+'\n'+action+'\n'
        result=subprocess.run(['bash','-c',harness],env=env,text=True,capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        return events.read_text().splitlines() if events.exists() else []

    def test_start_collects_all_enabled_instances_without_nested_procd(self):
        events=self.lifecycle('start')
        self.assertEqual([x for x in events if x.startswith('INSTANCE ')],['INSTANCE modem_4_1','INSTANCE modem_7_1'])
        self.assertEqual([x for x in events if x.startswith('CLOSE ')],['CLOSE set'])

    def test_start_keeps_already_running_instances_in_complete_set(self):
        events=self.lifecycle('start',running=('4_1','7_1'))
        self.assertEqual([x for x in events if x.startswith('INSTANCE ')],['INSTANCE modem_4_1','INSTANCE modem_7_1'])
        self.assertFalse(any(x.startswith('HANG ') for x in events))

    def test_reload_hangs_disabled_instance_without_erasing_parent_transaction(self):
        for enabled,kept,removed in [(('1','0'),'4_1','7_1'),(('0','1'),'7_1','4_1')]:
            with self.subTest(enabled=enabled):
                events=self.lifecycle('reload_service',enabled=enabled,running=('4_1','7_1'))
                self.assertEqual([x for x in events if x.startswith('INSTANCE ')],['INSTANCE modem_'+kept])
                self.assertIn('HANG '+removed,events)
                self.assertEqual([x for x in events if x.startswith('CLOSE ')],['CLOSE set'])

    def test_stop_and_globally_disabled_start_hang_only_existing_instances(self):
        events=self.lifecycle('stop_service',running=('4_1',))
        self.assertIn('HANG 4_1',events)
        self.assertNotIn('HANG 7_1',events)
        self.assertFalse(any(x.startswith('INSTANCE ') for x in events))
        events=self.lifecycle('start',global_enabled='0',running=('4_1','7_1'))
        self.assertIn('HANG 4_1',events);self.assertIn('HANG 7_1',events)
        self.assertFalse(any(x.startswith('INSTANCE ') for x in events))
        self.assertIn('CLOSE set',events)

    def test_extra_redial_preserves_single_instance_add_semantics(self):
        events=self.lifecycle('redial 4_1',running=('4_1','7_1'))
        self.assertEqual([x for x in events if x.startswith('INSTANCE ')],['INSTANCE modem_4_1'])
        self.assertIn('HANG 4_1',events)
        self.assertNotIn('HANG 7_1',events)
        self.assertEqual([x for x in events if x.startswith('CLOSE ')],['CLOSE add'])


if __name__ == '__main__': unittest.main()

