"""Install a pinned fw3 mwan3 backend; never download target packages at build time."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]

# Runs inside the existing tracker. No duplicate ping worker, UCI writes or
# process signals by unverified PID. One-second sleeps keep link events prompt.
TRACKER_RUNTIME = r'''
aw_role() {
    local pref cell selected wan_health
    pref=${AW_PREF:-$(uci -q get aw1000.internet.priority)}
    cell=${AW_CELL:-$(uci -q get mwan3.globals.aw1000_cellular)}
    AW_ROLE=legacy; AW_INTERVAL=$interval
    [ "$INTERFACE" = wan ] || [ "$INTERFACE" = "$cell" ] || return 0
    case "$pref:$INTERFACE" in
        wired_only:wan) AW_ROLE=active; AW_INTERVAL=2; return;;
        cellular_only:wan) AW_ROLE=off; AW_INTERVAL=0; return;;
    esac
    if [ "$pref" = wired_only ]; then AW_ROLE=off; AW_INTERVAL=0; return; fi
    selected=$(cat /var/run/aw1000-mwan-follow/selected 2>/dev/null)
    wan_health=$(cat "$MWAN3TRACK_STATUS_DIR/wan/STATUS" 2>/dev/null)
    if [ "$INTERFACE" = wan ]; then
        AW_ROLE=active; AW_INTERVAL=2
        if [ "$selected" != wan ]; then
            if [ "$wan_health" = online ]; then AW_ROLE=standby; else AW_ROLE=recovery; fi
        fi
        return
    fi
    AW_ROLE=recovery; AW_INTERVAL=2
    if [ "$pref" = cellular_only ]; then AW_ROLE=active; AW_INTERVAL=30
    elif [ "$selected" = "$cell" ]; then AW_ROLE=active; AW_INTERVAL=10
    elif [ "$pref" = cellular ]; then AW_ROLE=recovery; AW_INTERVAL=30
    elif [ "$pref" = wired ] && [ "$wan_health" = online ]; then
        AW_ROLE=standby; AW_INTERVAL=60
        if [ "$aw_failures" -ge 3 ]; then AW_INTERVAL=300
        elif [ "$aw_failures" -ge 2 ]; then AW_INTERVAL=120; fi
    fi
    # Once a recovery round succeeds, finish all four checks promptly.
    if [ "$score" -gt 0 ] && [ "$score" -lt "$up" ]; then AW_INTERVAL=2; fi
    [ "$STATUS" != disconnecting ] || AW_INTERVAL=2
}
aw_publish() {
    local dir="$MWAN3TRACK_STATUS_DIR/$INTERFACE"
    printf '%s\n' "$AW_ROLE" > "$dir/AW_ROLE"
    printf '%s\n' "$AW_INTERVAL" > "$dir/AW_INTERVAL"
}
aw_wait() {
    local elapsed=0 old_role="$AW_ROLE" old_interval=$AW_INTERVAL
    # A long-interval healthy standby must be revalidated on WAN failure.
    # Withdraw native tracker health before allowing its recovery checks.
    while [ "$elapsed" -lt "$sleep_time" ]; do
        sleep 1 & SLEEP_PID=$!; wait "$SLEEP_PID"; unset SLEEP_PID
        [ "$IFDOWN_EVENT" -eq 0 ] && [ "$IFUP_EVENT" -eq 0 ] || return
        elapsed=$((elapsed+1))
        aw_role
        if [ "$old_role" = standby ] && [ "$AW_ROLE" != standby ] &&
           [ "$INTERFACE" = "${AW_CELL:-$(uci -q get mwan3.globals.aw1000_cellular)}" ] &&
           [ "${AW_PREF:-$(uci -q get aw1000.internet.priority)}" = wired ] &&
           [ "$(cat "$MWAN3TRACK_STATUS_DIR/wan/STATUS" 2>/dev/null)" != online ]; then
            disconnected
            return
        fi
        [ "$AW_ROLE" = "$old_role" ] && [ "$AW_INTERVAL" = "$old_interval" ] || return
    done
}
'''


def patch_tracker_runtime(text):
    text = replace_once(text, "\tSTATUS='offline'", '''
    if [ "$INTERFACE" = wan ] && { [ "$status" = online ] || [ "$status" = disconnecting ]; }; then
        get_uptime > "$MWAN3TRACK_STATUS_DIR/wan/AW_OUTAGE"
    fi
\tSTATUS='offline' ''')
    text = replace_once(text, "\tSTATUS='online'", '''
    if [ "$INTERFACE" = "$(uci -q get mwan3.globals.aw1000_cellular)" ]; then
        cat "$MWAN3TRACK_STATUS_DIR/wan/AW_OUTAGE" 2>/dev/null > "$MWAN3TRACK_STATUS_DIR/$INTERFACE/AW_VERIFIED_OUTAGE"
    fi
\tSTATUS='online' ''')
    text = replace_once(text, 'main() {', TRACKER_RUNTIME+'\nmain() {')
    text = replace_once(text, '\tfirstconnect\n\twhile true; do',
        '\tlocal aw_failures=0 aw_suspended=0\n'
        '\tAW_PREF=$(uci -q get aw1000.internet.priority)\n'
        '\tAW_CELL=$(uci -q get mwan3.globals.aw1000_cellular)\n'
        '\tfirstconnect\n\twhile true; do')
    text = replace_once(text, '\t\tsleep_time=$interval\n', '''
        aw_role
        aw_publish
        if [ "$AW_ROLE" = off ]; then
            if [ "$aw_suspended" = 0 ]; then disconnected; aw_suspended=1; fi
            echo disabled > "$MWAN3TRACK_STATUS_DIR/$INTERFACE/STATUS"
            IFDOWN_EVENT=0; IFUP_EVENT=0
            sleep_time=1; aw_wait
            continue
        fi
        if [ "$aw_suspended" = 1 ]; then
            aw_suspended=0
            firstconnect
        fi
        sleep_time=$AW_INTERVAL
        if [ "$INTERFACE" = "$(uci -q get mwan3.globals.aw1000_cellular)" ]; then
            size=8; count=1; timeout=2; reliability=1; down=2; up=4
        fi
''')
    text = replace_once(text, '\t\tif [ $host_up_count -lt $reliability ]; then\n\t\t\tlet score--',
        '\t\tif [ $host_up_count -lt $reliability ]; then\n'
        '\t\t\taw_failures=$((aw_failures+1))\n\t\t\tlet score--')
    text = replace_once(text, '\t\t\tlet score++', '\t\t\taw_failures=0\n\t\t\tget_uptime > "$MWAN3TRACK_STATUS_DIR/$INTERFACE/AW_LAST_SUCCESS"\n\t\t\tlet score++')
    text = replace_once(text, '\t\thost_up_count=0\n\t\tif [ "${IFDOWN_EVENT}"',
        '\t\taw_role\n\t\taw_publish\n\t\tsleep_time=$AW_INTERVAL\n'
        '\t\thost_up_count=0\n\t\tif [ "${IFDOWN_EVENT}"')
    text = replace_once(text, '\t\t\tsleep "${sleep_time}" &\n\t\t\tSLEEP_PID=$!\n\t\t\twait\n\t\t\tunset SLEEP_PID', '\t\t\taw_wait')
    text = replace_once(text, '\t\t\t\tcase "$track_method" in', '''                aw_attempts=$(cat "$MWAN3TRACK_STATUS_DIR/$INTERFACE/AW_PROBE_ATTEMPTS" 2>/dev/null || echo 0)
                case "$aw_attempts" in ''|*[!0-9]*) aw_attempts=0;; esac
                echo "$((aw_attempts+1))" > "$MWAN3TRACK_STATUS_DIR/$INTERFACE/AW_PROBE_ATTEMPTS"
                get_uptime > "$MWAN3TRACK_STATUS_DIR/$INTERFACE/AW_LAST_PROBE"
\t\t\t\tcase "$track_method" in''')
    return text


# Only netifd/odhcpd receive this preload. Persistent config readers (LuCI,
# QModem, UCI CLI) continue to use /etc/config. An absolute uci_load filename
# makes the override self-contained, without shared pending UCI deltas.
RUNTIME_UCI_C = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
struct uci_context;
struct uci_package;
static int same_file(const char *a, const char *b) {
    FILE *fa = fopen(a, "rb"), *fb = fopen(b, "rb");
    int equal = fa && fb;
    if (equal) {
        int ca, cb;
        do { ca=fgetc(fa); cb=fgetc(fb); if (ca!=cb) { equal=0; break; } } while (ca!=EOF);
        if (ferror(fa) || ferror(fb)) equal=0;
    }
    if (fa) fclose(fa);
    if (fb) fclose(fb);
    return equal;
}
int uci_load(struct uci_context *ctx, const char *name, struct uci_package **pkg) {
    int (*real_load)(struct uci_context *, const char *, struct uci_package **);
    char path[256], base[256], stamp[256];
    real_load = dlsym(RTLD_NEXT, "uci_load");
    if (!real_load) return 1;
    /* netifd's children inherit LD_PRELOAD. Keep their ordinary UCI reads. */
    char exe[256];
    ssize_t len=readlink("/proc/self/exe", exe, sizeof(exe)-1);
    if (len<0) return real_load(ctx, name, pkg);
    exe[len]=0;
    const char *program=strrchr(exe, '/');
    program=program ? program+1 : exe;
    if (strcmp(program,"netifd") && strcmp(program,"odhcpd"))
        return real_load(ctx, name, pkg);
    if (name && (!strcmp(name, "network") || !strcmp(name, "dhcp"))) {
        snprintf(path, sizeof(path), "/var/run/aw1000-ipv6-follow/config/%s", name);
        snprintf(base, sizeof(base), "/etc/config/%s", name);
        snprintf(stamp, sizeof(stamp), "%s.base", path);
        if (same_file(base, stamp) && access(path, R_OK) == 0)
            return real_load(ctx, path, pkg);
    }
    return real_load(ctx, name, pkg);
}
'''


def install_runtime_uci(root, work, cc):
    src = work/'aw1000-runtime-uci.c'
    src.write_text(RUNTIME_UCI_C)
    lib = root/'lib/libaw1000-runtime-uci.so'
    subprocess.run([cc, '-Os', '-shared', '-fPIC', '-nostdlib', '-fno-stack-protector',
                    '-fno-builtin', '-U_FORTIFY_SOURCE', '-Wall', '-Wextra', '-Werror',
                    '-Wl,--hash-style=both', '-o', str(lib), str(src)], check=True)
    if 'NEEDED' in subprocess.check_output(['readelf','-d',str(lib)],text=True):
        raise ValueError('runtime UCI helper unexpectedly links host libc')
    for name, command in [('network','/sbin/netifd'),('odhcpd','/usr/sbin/odhcpd')]:
        p=root/'etc/init.d'/name
        text=p.read_text()
        if 'LD_PRELOAD=' in text: raise ValueError('Existing preload: '+name)
        p.write_text(replace_once(text, 'procd_set_param command '+command,
            'procd_set_param env LD_PRELOAD=/lib/libaw1000-runtime-uci.so\n\tprocd_set_param command '+command))


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('mwan3 兼容补丁锚点不符：' + old[:65])
    return text.replace(old, new, 1)


def patch_common(text):
    """Publish only complete, validated masks; derive capacity in every process."""
    validator = '''# Accept a literal 32-bit mask, never a shell arithmetic expression.
mwan3_normalize_mmx_mask()
{
\tlocal value digits
\tvalue="$1"
\tcase "$value" in
\t\t0[xX]*)
\t\t\tdigits="${value#??}"
\t\t\tcase "$digits" in ''|*[!0-9a-fA-F]*) return 1;; esac
\t\t\t[ "${#digits}" -le 8 ] || return 1
\t\t\tvalue=$((0x$digits))
\t\t\t;;
\t\t*)
\t\t\tcase "$value" in ''|*[!0-9]*) return 1;; esac
\t\t\t# Avoid octal interpretation of a decimal mask with leading zeroes.
\t\t\twhile [ "${value#0}" != "$value" ]; do value="${value#0}"; done
\t\t\t[ -n "$value" ] && [ "${#value}" -le 10 ] || return 1
\t\t\tvalue=$((value))
\t\t\t;;
\tesac
\t[ "$value" -le 4294967295 ] || return 1
\t[ "$(mwan3_count_one_bits "$value")" -ge 3 ] || return 1
\tprintf '0x%x\\n' "$value"
}

'''
    text = replace_once(text, 'mwan3_init()\n{\n\tlocal bitcnt mmdefault source_routing\n',
                        validator + 'mwan3_init()\n{\n'
                        '\tlocal bitcnt mmdefault source_routing configured_mask mask_tmp cached_mask\n')
    old = '''\t# mwan3's MARKing mask (at least 3 bits should be set)
\tif [ -e "${MWAN3_STATUS_DIR}/mmx_mask" ]; then
\t\tMMX_MASK=$(cat "${MWAN3_STATUS_DIR}/mmx_mask")
\t\tMWAN3_INTERFACE_MAX=$(uci_get_state mwan3 globals iface_max)
\telse
\t\tconfig_get MMX_MASK globals mmx_mask '0x3F00'
\t\techo "$MMX_MASK"| tr 'A-F' 'a-f' > "${MWAN3_STATUS_DIR}/mmx_mask"
\t\tLOG debug "Using firewall mask ${MMX_MASK}"

\t\tbitcnt=$(mwan3_count_one_bits MMX_MASK)
\t\tmmdefault=$(((1<<bitcnt)-1))
\t\tMWAN3_INTERFACE_MAX=$((mmdefault-3))
\t\tuci_toggle_state mwan3 globals iface_max "$MWAN3_INTERFACE_MAX"
\t\tLOG debug "Max interface count is ${MWAN3_INTERFACE_MAX}"
\tfi
'''
    new = '''\t# Keep the active mask until mwan3 removes its cache during stop.
\t# An empty/partial cache is never allowed to reach mark arithmetic.
\tMMX_MASK=$(mwan3_normalize_mmx_mask "$(cat "${MWAN3_STATUS_DIR}/mmx_mask" 2>/dev/null)") || {
\t\tconfig_get configured_mask globals mmx_mask '0x3F00'
\t\tMMX_MASK=$(mwan3_normalize_mmx_mask "$configured_mask") || {
\t\t\tLOG err "Invalid mwan3 mmx_mask; refusing to generate firewall marks"
\t\t\texit 1
\t\t}
\t\tmask_tmp="${MWAN3_STATUS_DIR}/mmx_mask.tmp.$$"
\t\tif ! printf '%s\\n' "$MMX_MASK" > "$mask_tmp"; then
\t\t\trm -f "$mask_tmp"
\t\t\tLOG err "Cannot prepare mwan3 mask cache"
\t\t\texit 1
\t\tfi
\t\t# Reuse a valid mask published by a concurrent initializer.
\t\tif cached_mask=$(mwan3_normalize_mmx_mask "$(cat "${MWAN3_STATUS_DIR}/mmx_mask" 2>/dev/null)"); then
\t\t\tMMX_MASK="$cached_mask"
\t\t\trm -f "$mask_tmp"
\t\telif ! mv -f "$mask_tmp" "${MWAN3_STATUS_DIR}/mmx_mask"; then
\t\t\trm -f "$mask_tmp"
\t\t\tLOG err "Cannot publish mwan3 mask cache"
\t\t\texit 1
\t\tfi
\t\tLOG debug "Using firewall mask ${MMX_MASK}"
\t}
\t# The state file may not yet exist when another process reads the cache.
\tbitcnt=$(mwan3_count_one_bits MMX_MASK)
\tmmdefault=$(((1<<bitcnt)-1))
\tMWAN3_INTERFACE_MAX=$((mmdefault-3))
\tuci_toggle_state mwan3 globals iface_max "$MWAN3_INTERFACE_MAX"
\tLOG debug "Max interface count is ${MWAN3_INTERFACE_MAX}"
'''
    return replace_once(text, old, new)


def install(root, work, cc):
    lock = json.loads((ROOT/'sources.lock.json').read_text())['mwan3_legacy']
    archive = ROOT/lock['archive']
    if hashlib.sha256(archive.read_bytes()).hexdigest() != lock['sha256']:
        raise ValueError('mwan3 源码归档校验失败')
    source = work/'mwan3-source'
    source.mkdir()
    with tarfile.open(archive, 'r:xz') as bundle:
        for member in bundle:
            name = PurePosixPath(member.name)
            if not member.isfile() or name.is_absolute() or '..' in name.parts:
                raise ValueError('mwan3 源码路径不安全')
            data = bundle.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != lock['files'].get(member.name):
                raise ValueError('mwan3 文件校验失败：' + member.name)
            target = source/member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    paths = []
    for p in (source/'files').rglob('*'):
        if not p.is_file():
            continue
        name = p.relative_to(source/'files').as_posix()
        if name == 'etc/config/mwan3':
            continue  # The overlay provides the AW1000 policy, not upstream examples.
        dest = root/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = p.read_text()
        if name == 'lib/mwan3/common.sh':
            text = patch_common(text)
        if name == 'lib/mwan3/mwan3.sh':
            text = replace_once(text, '\t[ -n "$iface" ] || return 0\n\tnetwork_get_device',
                                '\t[ -n "$iface" ] || return 0\n'
                                '\t# AW1000: explicit SIM failures override tracker events.\n'
                                '\t[ "$(cat /var/run/aw1000-internet/blocked 2>/dev/null)" != "$iface" ] || return 0\n'
                                '\tnetwork_get_device')
            text = replace_once(text, '\tis_offline=$?\n', '''\tis_offline=$?
    # A healthy but old standby sample cannot select cellular during a new
    # WAN outage. The native tracker publishes verification after four rounds.
    if [ "$iface" = "$(uci -q get mwan3.globals.aw1000_cellular)" ] &&
       [ "$(uci -q get aw1000.internet.priority)" = wired ]; then
        aw_outage=$(cat /var/run/mwan3track/wan/AW_OUTAGE 2>/dev/null)
        aw_verified=$(cat "/var/run/mwan3track/$iface/AW_VERIFIED_OUTAGE" 2>/dev/null)
        [ "$aw_outage" = "$aw_verified" ] || is_offline=1
    fi
''')
        if name == 'usr/sbin/mwan3track':
            text = patch_tracker_runtime(text)
            text = replace_once(text, '. /lib/mwan3/common.sh', '. /lib/mwan3/mwan3.sh')
            # Probe via its dedicated table, even when this member is excluded from business traffic.
            text = replace_once(text, 'FWMARK=$MMX_DEFAULT LD_PRELOAD=', 'FWMARK=$PROBE_MARK LD_PRELOAD=')
            text = replace_once(text, '\tmwan3_init\n', '\tmwan3_init\n'
                                '\tmwan3_get_iface_id probe_id "$INTERFACE"\n'
                                '\t[ -n "$probe_id" ] || exit 1\n'
                                '\tPROBE_MARK=$(mwan3_id2mask probe_id MMX_MASK)\n')
            anchor = '\t\tconfig_get STATUS $INTERFACE initial_state "online"\n\tfi\n'
            text = replace_once(text, anchor, anchor +
                                '\t[ "$(cat /var/run/aw1000-internet/blocked 2>/dev/null)" != "$INTERFACE" ] || STATUS=offline\n')
        if name == 'etc/init.d/mwan3':
            text = replace_once(text, 'START=19', 'START=97')
            preflight = '''aw1000_preflight() {
    [ -d "$MWAN3_STATUS_DIR" ] && return 0
    config_load mwan3
    aw_id=0; aw_collision=0
    aw_check() {
        aw_id=$((aw_id+1))
        config_get aw_family "$1" family ipv4
        case "$aw_family" in ipv4) aw_ip="ip -4";; ipv6) aw_ip="ip -6";; *) aw_collision=1; return;; esac
        [ -z "$($aw_ip route show table "$aw_id" 2>/dev/null)" ] || aw_collision=1
        for aw_pref in $((1000+aw_id)) $((2000+aw_id)) $((3000+aw_id)); do
            $aw_ip rule show | grep -q "^$aw_pref:" && aw_collision=1
        done
        return 0
    }
    config_foreach aw_check interface
    for aw_family in 4 6; do
        ip -$aw_family rule show | grep -Eq '^(2253|2254):' && aw_collision=1
    done
    [ "$aw_collision" = 0 ] || { LOG err "AW1000 routing table/rule conflict; refusing to overwrite"; return 1; }
}

'''
            text = replace_once(text, 'start_service() {', preflight+'start_service() {\n\taw1000_preflight || return 1')
            text = replace_once(text, 'stop_service() {', 'stop_service() {\n\t[ -d "$MWAN3_STATUS_DIR" ] || return 0')
            begin = text.index('\t\tfor tid in $($IP route list table all')
            end = text.index('\t\ttable="$($IPT -S)"', begin)
            text = text[:begin] + '''        # Delete only configured mwan3 tables and exact owned priorities.
        aw_cleanup_iface() {
            local owned_family owned_id
            config_get owned_family "$1" family ipv4
            [ "$owned_family" = "$family" ] || return 0
            mwan3_get_iface_id owned_id "$1"
            [ -n "$owned_id" ] || return 0
            $IP route flush table "$owned_id" 2>/dev/null
            for rule in $((1000+owned_id)) $((2000+owned_id)) $((3000+owned_id)); do
                $IP rule del pref "$rule" 2>/dev/null
            done
            return 0
        }
        config_foreach aw_cleanup_iface interface
        for rule in $((MM_BLACKHOLE+2000)) $((MM_UNREACHABLE+2000)); do
            $IP rule del pref "$rule" 2>/dev/null
        done
''' + text[end:]
        if name == 'etc/hotplug.d/iface/15-mwan3':
            text = replace_once(text, 'case "$ACTION" in\n', '''# A physical WAN loss must invalidate old cellular verification before
# native policy rebuilding, not after the tracker receives its signal.
if [ "$INTERFACE" = wan ] && [ "$ACTION" = ifdown ]; then
    mkdir -p /var/run/mwan3track/wan
    get_uptime > /var/run/mwan3track/wan/AW_OUTAGE
fi
case "$ACTION" in
''')
        dest.write_text(text)
        dest.chmod(0o755 if text.startswith('#!/bin/sh') else 0o644)
        paths.append('/'+name)
        subprocess.run(['bash', '-n', str(dest)], check=True)

    wrapper = root/'lib/mwan3/libwrap_mwan3_sockopt.so.1.0'
    wrapper_source = source/'src/sockopt_wrap.c'
    text = wrapper_source.read_text()
    # New host headers redirect strtol to a C23-only glibc symbol. Name the
    # original musl symbol explicitly without changing its public C ABI.
    text = replace_once(text, 'static int (*next_socket)',
                        'extern long aw1000_strtol(const char *, char **, int) __asm__("strtol");\n'
                        'static int (*next_socket)')
    text = replace_once(text, '(int)strtol(fwmark_str, NULL, 0)',
                        '(int)aw1000_strtol(fwmark_str, NULL, 0)')
    text = text.replace('sockfd < 1024', 'sockfd >= 0 && sockfd < 1024')
    wrapper_source.write_text(text)
    # No glibc linkage: AArch64 socket ABI declarations come from system headers;
    # all imported functions resolve against the ORIGINAL target musl at load time.
    subprocess.run([cc, '-Os', '-fPIC', '-shared', '-nostdlib', '-fno-stack-protector',
                    '-fno-builtin', '-U_FORTIFY_SOURCE', '-DCONFIG_IPV6',
                    '-Wl,--hash-style=both', '-Wl,-soname,libwrap_mwan3_sockopt.so.1.0',
                    '-o', str(wrapper), str(wrapper_source)], check=True)
    dynamic = subprocess.check_output(['readelf', '-d', str(wrapper)], text=True)
    versions = subprocess.check_output(['readelf', '-V', str(wrapper)], text=True)
    if 'NEEDED' in dynamic or 'GLIBC_' in versions:
        raise ValueError('mwan3 wrapper 意外依赖主机运行库')
    # Load the actual shared object through the original QWRT dynamic loader.
    libpath = str(root/'lib')+':'+str(root/'usr/lib')
    script = ('package.cpath='+json.dumps(str(root/'usr/lib/lua/?.so'))+';'
              'local n=require("nixio");local s=assert(n.socket("inet","dgram"));'
              'assert(s:sendto("test","127.0.0.1",9)==4);s:close();'
              'assert(not n.socket("inet6","dgram"))')
    subprocess.run(['qemu-aarch64-static', '-L', str(root), str(root/'lib/libc.so'),
                    '--library-path', libpath, '--preload', str(wrapper),
                    str(root/'usr/bin/lua'), '-e', script], check=True,
                   env={**os.environ, 'FAMILY':'ipv4', 'SRCIP':'127.0.0.1', 'DEVICE':'', 'FWMARK':'0'})
    paths.append('/lib/mwan3/libwrap_mwan3_sockopt.so.1.0')

    helper = root/'usr/sbin/aw1000-flow-flush'
    subprocess.run([cc, '-Os', '-static', '-Wall', '-Wextra', '-Werror',
                    '-o', str(helper), str(ROOT/'network/flow-flush.c')], check=True)
    subprocess.run(['qemu-aarch64-static', str(helper), '--selftest'], check=True)
    subprocess.run(['qemu-aarch64-static', str(helper), '--filter-selftest'], check=True)
    timeout_link=root/'usr/bin/timeout'
    if not timeout_link.exists() and not timeout_link.is_symlink():
        timeout_link.symlink_to('../sbin/aw1000-flow-flush')
        paths.append('/usr/bin/timeout')
    install_runtime_uci(root, work, cc)
    paths.append('/lib/libaw1000-runtime-uci.so')

    from customize import package_records
    status = root/'usr/lib/opkg/status'
    records = package_records(status.read_text())
    required = ['ip-full', 'ipset', 'iptables', 'ip6tables', 'iptables-mod-conntrack-extra',
                'iptables-mod-ipopt', 'jshn']
    if any(name not in records for name in required):
        raise ValueError('原固件缺少 mwan3 依赖：'+str(set(required)-records.keys()))
    extras = {'nftables-json', 'kmod-nft-core', 'kmod-nft-nat6', 'kmod-nf-conntrack-netlink', 'rpcd-mod-file'}
    if not extras <= records.keys():
        raise ValueError('原固件缺少 IPv6/连接清理/RPC 依赖：'+str(extras-records.keys()))
    for name in ('sbin/ip', 'usr/sbin/ipset', 'usr/sbin/iptables',
                 'usr/sbin/iptables-restore', 'usr/sbin/ip6tables'):
        p = root/name
        if not p.is_file() and not p.is_symlink():
            raise ValueError('缺少网络工具：'+name)
    control = ('Package: mwan3\nVersion: 2.11.7-2.aw1000\nArchitecture: all\n'
               'License: GPL-2.0\nDepends: '+', '.join(required)+'\n'
               'Description: AW1000 fw3 failover backend\n')
    info = root/'usr/lib/opkg/info'
    (info/'mwan3.control').write_text(control)
    (info/'mwan3.list').write_text('\n'.join(paths+['/etc/config/mwan3'])+'\n')
    (info/'mwan3.conffiles').write_text('/etc/config/mwan3\n/etc/mwan3.user\n')
    status.write_text(status.read_text()+control+'Status: install user installed\n\n')
    return {'backend':'mwan3-fw3', 'version':lock['version'], 'archive_sha256':lock['sha256'],
            'wrapper_original_musl_load_verified':True, 'hardware_verified':False}
