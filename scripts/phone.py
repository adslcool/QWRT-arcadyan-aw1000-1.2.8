"""Prepare an auditable Linux 5.4 source port and the Chinese phone application."""
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import struct
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
COMMIT = 'f26f9f5a4bfbee391a3a15c9dab886c96d7ca539'


def upstream_files():
    """Read the complete pinned supplier source without expanding the upload tree."""
    lock = json.loads((ROOT/'sources.lock.json').read_text())
    archive = ROOT/lock['phone_archive']
    if hashlib.sha256(archive.read_bytes()).hexdigest() != lock['files'][lock['phone_archive']]:
        raise ValueError('电话源码归档 SHA256 校验失败')
    expected = {name[len('phone/upstream/'):]: digest for name, digest in lock['files'].items()
                if name.startswith('phone/upstream/')}
    result = {}; total = 0
    with tarfile.open(archive, 'r:xz') as t:
        for m in t:
            name = m.name; path = PurePosixPath(name)
            if (not m.isfile() or path.is_absolute() or '..' in path.parts or '\\' in name
                    or str(path) != name or name not in expected or name in result):
                raise ValueError('电话源码归档包含意外或不安全成员：'+name)
            total += m.size
            if m.size < 0 or total > 8*1024*1024:
                raise ValueError('电话源码归档解包大小超出限制')
            data = t.extractfile(m).read()
            if len(data) != m.size or hashlib.sha256(data).hexdigest() != expected[name]:
                raise ValueError('电话供应文件校验失败：'+name)
            blob = hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
            if blob != lock['phone_git_blobs'].get(name):
                raise ValueError('电话上游 Git blob 校验失败：'+name)
            result[name] = data
    if set(result) != set(expected) or set(result) != set(lock['phone_git_blobs']):
        raise ValueError('电话源码归档文件不完整')
    return result


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('电话源码检查失败：'+old[:70])
    return text.replace(old, new)


def library_files():
    """Pinned complete LGPL/ISC sources; never replace the router's ubus libraries."""
    lock = json.loads((ROOT/'sources.lock.json').read_text())
    meta = lock['phone_libraries']
    archive = ROOT/meta['archive']
    if hashlib.sha256(archive.read_bytes()).hexdigest() != lock['files'][meta['archive']]:
        raise ValueError('电话静态库源码归档 SHA256 校验失败')
    expected = meta['files']; result = {}; total = 0
    with tarfile.open(archive, 'r:xz') as stream:
        for item in stream:
            name = item.name; path = PurePosixPath(name)
            if (not item.isfile() or path.is_absolute() or '..' in path.parts or '\\' in name
                    or str(path) != name or name not in expected or name in result):
                raise ValueError('电话静态库源码归档包含不安全成员：'+name)
            total += item.size
            if item.size < 0 or total > 16*1024*1024:
                raise ValueError('电话静态库源码超出大小上限')
            data = stream.extractfile(item).read()
            if len(data) != item.size or hashlib.sha256(data).hexdigest() != expected[name]:
                raise ValueError('电话静态库源码校验失败：'+name)
            result[name] = data
    if set(result) != set(expected):
        raise ValueError('电话静态库源码不完整')
    return result


def build_libraries(destination, cc='aarch64-linux-gnu-gcc'):
    """Build only the client libraries, without JSON-C, Lua or a replacement ubusd."""
    if destination.exists():
        raise ValueError('电话静态库工作目录必须不存在')
    destination.mkdir(parents=True)
    for name, data in library_files().items():
        p = destination/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
    include = destination/'include'; include.mkdir()
    (include/'libubox').symlink_to('../libubox', target_is_directory=True)
    for name in ('libubus.h', 'ubusmsg.h', 'ubus_common.h'):
        shutil.copyfile(destination/'ubus'/name, include/name)
    ubox = 'avl avl-cmp blob blobmsg uloop usock ustream ustream-fd vlist utils safe_list runqueue md5 kvlist ulog base64'.split()
    ubus = 'libubus libubus-io libubus-obj libubus-sub libubus-req libubus-acl'.split()
    ar = cc[:-3]+'ar' if cc.endswith('gcc') else 'ar'
    for name, sources in [('libubox', ubox), ('ubus', ubus)]:
        objects = []; src = destination/name
        for unit in sources:
            obj = src/(unit+'.o'); objects.append(str(obj))
            subprocess.run([cc, '-Os', '-std=gnu99', '-D_GNU_SOURCE', '-ffunction-sections',
                            '-Werror=implicit-function-declaration', '-fdata-sections', '-I'+str(include),
                            '-DUBUS_UNIX_SOCKET="/var/run/ubus/ubus.sock"', '-DUBUS_MAX_MSGLEN=1048576',
                            '-c', str(src/(unit+'.c')), '-o', str(obj)], check=True)
        subprocess.run([ar, 'rcs', str(destination/('libubox.a' if name == 'libubox' else 'libubus.a'))]+objects, check=True)
    return include, [destination/'libubus.a', destination/'libubox.a']


def prepare(destination):
    files = upstream_files()
    if destination.exists() and (destination.is_symlink() or not destination.is_dir() or any(destination.iterdir())):
        raise ValueError('电话源码工作目录必须为空且不是符号链接')
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        p = destination/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        p.chmod(0o755 if p.suffix == '.sh' else 0o644)
    p = destination/'kernel/aw1000-fxs/fxs_main.c'
    p.write_text(p.read_text().replace('timer_delete_sync(', 'del_timer_sync(').replace('timer_delete(', 'del_timer('))
    p = destination/'kernel/aw1000-fxs/fxs_pcm.c'
    text = p.read_text()
    text = once(text, 'sched_set_fifo_low(current);', '{\n\t\tstruct sched_param param = { .sched_priority = 1 };\n\t\tsched_setscheduler(current, SCHED_FIFO, &param);\n\t}')
    p.write_text('#include <uapi/linux/sched/types.h>\n'+text)
    p = destination/'openwrt/aw1000-fxs/Makefile'
    p.write_text(once(p.read_text(), '@TARGET_qualcommax_ipq807x', '@TARGET_ipq807x'))
    p = destination/'fxsd/src/modem.c'
    text = p.read_text()
    start = text.index('static const char *port(void)')
    stop = text.index('\nvoid modem_set_port', start)
    text = text[:start]+'''static const char *port(void)
{
    FILE *p;
    if (strcmp(at_port, "auto")) return at_port;
    cur[0] = 0;
    p = popen("/usr/sbin/aw1000-phone-port", "r");
    if (p) {
        if (!fgets(cur, sizeof(cur), p)) cur[0] = 0;
        cur[strcspn(cur, "\\r\\n")] = 0;
        if (pclose(p)) cur[0] = 0;
    }
    return cur;
}
'''+text[stop:]
    text = once(text,
        '"ubus call at-daemon sendat \'{\\"at_port\\":\\"%s\\",\\"at_cmd\\":\\"%s\\",\\"timeout\\":3}\' 2>/dev/null",',
        '"/usr/sbin/aw1000-phone-at \'%s\' \'%s\' 2>/dev/null",')
    # The wrapper takes raw AT text, not the JSON-escaped form.
    text = once(text, ' ap, esc);', ' ap, cmd_start);') if ' ap, esc);' in text else text
    text = once(text, 'const char *ap = port();', 'const char *ap = port();\n\tconst char *cmd_start = cmd;')
    p.write_text(text)
    for name in ('voip.c', 'urc.c'):
        p = destination/'fxsd/src'/name
        text = p.read_text()
        text = once(text, '#include <libubus.h>\n', '\x23include <libubus.h>\n' + r'''
/* QWRT and newer OpenWrt use different default socket paths. */
static struct ubus_context *phone_ubus_connect(void)
{
    struct ubus_context *ctx = ubus_connect("/var/run/ubus/ubus.sock");
    return ctx ? ctx : ubus_connect("/var/run/ubus.sock");
}
''')
        p.write_text(text.replace('ubus_connect(NULL)', 'phone_ubus_connect()'))
    p = destination/'fxsd/src/bringup_main.c'
    text = p.read_text()
    text = re.sub(r'^#define QDAI_CMD .*$', '#define QDAI_CMD "/usr/sbin/aw1000-phone-at auto \'AT+QDAI?\' 2>/dev/null"', text, count=1, flags=re.M)
    p.write_text(text)
    p = destination/'fxsd/src/fxsd_main.c'
    text = p.read_text()
    text = once(text, 'static void led_set(int mode)\n{\n', '''static int led_max(void)
{
	char buf[32], *end;
	unsigned long value;

	if (led_read("max_brightness", buf, sizeof(buf)))
		return 1;
	errno = 0;
	value = strtoul(buf, &end, 10);
	if (errno || end == buf)
		return 1;
	while (*end == ' ' || *end == '\\t' || *end == '\\r' || *end == '\\n')
		end++;
	return !*end && value > 0 && value <= 65535 ? (int)value : 1;
}

static int led_raw(int on)
{
	/* QWRT LEDC inverts all output bits. The AW1000 phone is active-high. */
	if (!strcmp(led_name, "ipq::led12"))
		on = !on;
	return on ? led_max() : 0;
}

static void led_set(int mode)
{
	char level[16];
''')
    text = once(text, '\tif (mode == LED_SLOW || mode == LED_FAST) {\n\t\tconst char *ms',
        '\tsnprintf(level, sizeof(level), "%d",\n\t\t(mode == LED_SLOW || mode == LED_FAST) ? led_max() : led_raw(mode == LED_ON));\n\tif (mode == LED_SLOW || mode == LED_FAST) {\n\t\tconst char *ms')
    text = once(text, '\t\tled_write("trigger", "timer");',
        '\t\tled_write("trigger", "none");\n\t\tled_write("brightness", level);\n\t\tled_write("trigger", "timer");')
    text = once(text, 'led_write("brightness", mode == LED_ON ? "1" : "0");',
        'led_write("brightness", level);')
    text = once(text, '(atoi(buf) != 0) != (mode == LED_ON)',
        'atoi(buf) != led_raw(mode == LED_ON)')
    p.write_text(text)
    p = destination/'openwrt/luci-app-fxs/files/phone.js'
    text = p.read_text()
    start = text.index("\n\t\t\tE('div', { 'class': 'fxs-footer' }")
    stop = text.index('\n\t\t]);', start)
    if text[start-1] != ',':
        raise ValueError('电话页面页脚锚点不匹配')
    p.write_text(text[:start]+"\n\t\t\tE('div', { 'class': 'fxs-footer' }, verText)"+text[stop:])
    p = destination/'openwrt/luci-app-fxs/files/settings.js'
    text = once(p.read_text(), "\t\ts.tab('led', _('LED'));\n", '')
    text = once(text, "' <a href=\"https://github.com/Perz3us/openwrt-aw1000-phone/issues\" target=\"_blank\" rel=\"noreferrer\">GitHub</a>.</strong>'", "'</strong>'")

    start = text.index("\n\t\t[\n\t\t\t[ 'led_ready'")
    stop = text.index("\n\t\to = s.taboption('advanced', form.ListValue, 'channel'", start)
    p.write_text(text[:start]+text[stop:])
    return destination


def translations():
    # Parse the simple singular PO catalog supplied with v1.50, including multiline entries.
    result = {}; key = val = ''; field = None
    for line in (ROOT/'phone/fxs.zh-cn.po').read_text().splitlines()+['']:
        if line.startswith('msgid '): key = ast.literal_eval(line[6:]); val=''; field='key'
        elif line.startswith('msgstr '): val = ast.literal_eval(line[7:]); field='val'
        elif line.startswith('"'):
            if field == 'key': key += ast.literal_eval(line)
            elif field == 'val': val += ast.literal_eval(line)
        elif not line.strip():
            if key and val: result[key] = val
            key = val = ''; field = None
    extra = ROOT/'phone/zh-extra.json'
    if extra.exists(): result.update(json.loads(extra.read_text()))
    return result


def check_static_elf(path):
    b = Path(path).read_bytes()
    if b[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', b, 18)[0] != 183:
        raise ValueError('电话程序不是 AArch64 ELF64')
    phoff = struct.unpack_from('<Q', b, 32)[0]
    phsize, count = struct.unpack_from('<HH', b, 54)
    for i in range(count):
        kind = struct.unpack_from('<I', b, phoff+i*phsize)[0]
        if kind in (2, 3): raise ValueError('电话程序必须静态链接，无目标 libc 依赖')


def check_toolchain(cc='aarch64-linux-gnu-gcc'):
    """Compile and link against target libc before any firmware download/extraction."""
    probe = '''#include <errno.h>
#include <dirent.h>
#include <stdint.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>
int main(void) {
    struct timespec ts;
    DIR *d = opendir(".");
    void *p = malloc(16);
    int result = clock_gettime(CLOCK_MONOTONIC, &ts);
    if (d) closedir(d);
    free(p);
    return result;
}
'''
    with tempfile.TemporaryDirectory(prefix='aw1000-cross-check-') as d:
        exe = Path(d)/'libc-probe'
        try:
            result = subprocess.run([cc, '-x', 'c', '-Os', '-static', '-o', str(exe), '-'],
                                    input=probe, text=True, capture_output=True)
        except OSError as exc:
            raise ValueError('无法运行 ARM64 交叉编译器；Debian/Ubuntu 请安装 '
                             'gcc-aarch64-linux-gnu 和 libc6-dev-arm64-cross：'+str(exc)) from None
        if result.returncode:
            raise ValueError('ARM64 头文件或静态链接检查失败；Debian/Ubuntu 请显式安装 '
                             'gcc-aarch64-linux-gnu 和 libc6-dev-arm64-cross '
                             '（--no-install-recommends 不会自动安装后者）。\n'
                             +(result.stderr or result.stdout).strip())
        check_static_elf(exe)
    print('ARM64 libc 头文件、静态链接及 ELF 架构检查通过。', flush=True)


def install(root, source, cc='aarch64-linux-gnu-gcc'):
    subprocess.run(['make', '-C', str(source/'fxsd'), 'clean'], check=True)
    include, libs = build_libraries(source/'private-libs', cc)
    subprocess.run(['make', '-C', str(source/'fxsd'), 'CC='+cc,
                    'CFLAGS=-Os -static -I'+shlex.quote(str(include)),
                    'FXSD_LIBS='+' '.join(shlex.quote(str(p)) for p in libs)+' -pthread',
                    'LDFLAGS=-Wl,--gc-sections', 'WERROR='], check=True)
    for name in ('fxsd', 'fxs-bringup'):
        b = source/'fxsd/build'/name
        check_static_elf(b)
        target = root/'usr/sbin'/name
        shutil.copyfile(b, target); target.chmod(0o755)
    app = source/'openwrt/luci-app-fxs/files'
    dest = root/'www/luci-static/resources/view/fxs'; dest.mkdir(parents=True, exist_ok=True)
    tr = translations(); missing = set()
    pattern = re.compile(r"_\(('(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")\)")
    for p in app.glob('*.js'):
        def translate(match):
            msg = ast.literal_eval(match.group(1))
            if msg not in tr: missing.add(msg); return match.group(0)
            return '_('+json.dumps(tr[msg], ensure_ascii=False)+')'
        (dest/p.name).write_text(pattern.sub(translate, p.read_text()))
    if missing: raise ValueError('电话界面仍有未翻译词条：'+repr(sorted(missing)))
    menu = json.loads((app/'menu.json').read_text())
    for item in menu.values():
        item['title'] = tr[item['title']]
    for name, data in [('usr/share/luci/menu.d/luci-app-fxs.json', json.dumps(menu, ensure_ascii=False)),
                       ('usr/share/rpcd/acl.d/luci-app-fxs.json', (app/'acl.json').read_text())]:
        p = root/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(data)
    p = root/'usr/libexec/rpcd/fxs'; p.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(app/'fxs.rpcd', p); p.chmod(0o755)
    # Source-built programs have no supplier APK/opkg record. Report the actual
    # bundled source versions rather than inheriting an old package database.
    versions = {}
    for key, package in [('fxsd', 'fxsd'), ('luci', 'luci-app-fxs'), ('driver', 'aw1000-fxs')]:
        text = (source/'openwrt'/package/'Makefile').read_text()
        match = re.search(r'^PKG_VERSION:=(\d+\.\d+\.\d+)$', text, re.M)
        if not match:
            raise ValueError('电话上游版本字段无效：'+package)
        versions[key] = match[1]
    text = p.read_text(); start = text.index('version() {'); stop = text.index('\nset_enabled()', start)
    body = "version() {\n\tjson_init\n"
    for key in ('fxsd', 'luci'):
        body += "\tjson_add_string "+key+" '"+versions[key]+"'\n"
    body += "\t[ ! -s /lib/modules/5.4.213/aw1000-fxs.ko ] || json_add_string driver '"+versions['driver']+"'\n"
    body += "\tjson_add_string source '"+COMMIT+"'\n\tjson_dump\n}\n"
    p.write_text(text[:start]+body+text[stop:])
    cfg = (source/'openwrt/fxsd/files/fxs.config').read_text().replace("option enabled '1'", "option enabled '0'")
    cfg = once(cfg, "option led 'green:phone'", "option led 'ipq::led12'")
    cfg = once(cfg, "option led_ringing 'fast'", "option led_ringing 'on'")
    cfg = once(cfg, "option led_offhook 'slow'", "option led_offhook 'on'")
    # The reference RG500Q-EA reports +QDAI: x,1,0,4,0,1,2,1,2 (16 kHz).
    # Match that rate so first startup does not switch this modem back to 8 kHz.
    cfg = once(cfg, "option hd '0'", "option hd '1'")
    (root/'etc/config/fxs').write_text(cfg)
    init = (source/'openwrt/fxsd/files/fxsd.init').read_text()
    if init.count('config_get led main led green:phone') != 2:
        raise ValueError('电话源码 LED 配置入口数量变化')
    init = init.replace('config_get led main led green:phone', 'config_get led main led ipq::led12')
    init = once(init, 'config_get led_ringing main led_ringing fast', 'config_get led_ringing main led_ringing on')
    init = once(init, 'config_get led_offhook main led_offhook slow', 'config_get led_offhook main led_offhook on')
    init = once(init, 'start_service() {', '''phone_led_off() {
	local led="$1" level=0
	[ -n "$led" ] && [ -w "/sys/class/leds/$led/brightness" ] || return 0
	if [ "$led" = ipq::led12 ]; then
		read -r level < "/sys/class/leds/$led/max_brightness" || level=1
		case "$level" in ''|*[!0-9]*) level=1;; esac
		[ "$level" -gt 0 ] && [ "$level" -le 65535 ] || level=1
	fi
	echo none > "/sys/class/leds/$led/trigger"
	echo "$level" > "/sys/class/leds/$led/brightness"
}

start_service() {''')
    init = once(init, '\tconfig_load fxs\n\tconfig_get_bool enabled main enabled 1\n', '''\tconfig_load fxs
\tconfig_get led main led ipq::led12
\tphone_led_off "$led"
\tconfig_get_bool enabled main enabled 1
''')
    init = once(init, '''\t[ -n "$led" ] && [ -w "/sys/class/leds/$led/brightness" ] && {
\t\techo none > "/sys/class/leds/$led/trigger"
\t\techo 0 > "/sys/class/leds/$led/brightness"
\t}''', '\tphone_led_off "$led"')
    init = once(init, '\tconfig_get channel main channel 1', '''	[ -s /lib/modules/5.4.213/aw1000-fxs.ko ] || {
		logger -t fxsd '缺少匹配 QWRT 5.4.213 的电话驱动，电话未启动'
		return 0
	}
	config_get channel main channel 1''')
    init = init.replace('insmod aw1000-fxs', 'insmod /lib/modules/5.4.213/aw1000-fxs.ko')
    (root/'etc/init.d/fxsd').write_text(init); (root/'etc/init.d/fxsd').chmod(0o755)
    patch = ROOT/'vendor/si3226x_patch.bin'
    if hashlib.sha256(patch.read_bytes()).hexdigest() != 'a8c305841adf327e180bff6cbac92ada5a0625ca2d9e8c22733e4b3f2b8cad99':
        raise ValueError('DSP patch 校验失败')
    (root/'etc/fxs').mkdir(exist_ok=True)
    shutil.copyfile(patch, root/'etc/fxs/si3226x_patch.bin')


def hide_build_status(root):
    """Keep driver diagnostics internal; remove the requested LuCI build-status page."""
    (root/'www/luci-static/resources/view/fxs/build.js').unlink(missing_ok=True)
    menu=root/'usr/share/luci/menu.d/luci-app-fxs.json'
    data=json.loads(menu.read_text())
    data.pop('admin/services/fxs/build',None)
    menu.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path, nargs='?')
    parser.add_argument('--check-toolchain', action='store_true')
    parser.add_argument('--cc', default='aarch64-linux-gnu-gcc')
    args = parser.parse_args()
    if args.check_toolchain == (args.destination is not None):
        parser.error('请选择源码输出目录或 --check-toolchain')
    try:
        if args.check_toolchain: check_toolchain(args.cc)
        else: prepare(args.destination)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
