"""Stamp the displayed firmware release once, using the Shanghai build date."""
from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo


def _assignment(text, key, value, quote="'"):
    pattern = r'^' + re.escape(key) + r'=.*$'
    if len(re.findall(pattern, text, re.MULTILINE)) != 1:
        raise ValueError('固件版本字段缺失或重复：' + key)
    return re.sub(pattern, key + '=' + quote + value + quote, text, flags=re.MULTILINE)


def stamp(root, version, instant=None):
    """Return display/build/base metadata after patching release and first boot.

    ``instant`` is an aware datetime for reproducible tests. Normal builds use
    the current time, not the input image's timestamp or the router's first boot.
    Distribution release, target, architecture and original build ID stay intact
    for feed/ABI compatibility. The original release file is retained separately.
    """
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('工程版本格式错误')
    instant = datetime.now(timezone.utc) if instant is None else instant
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError('构建时间必须包含时区')
    local = instant.astimezone(ZoneInfo('Asia/Shanghai'))
    revision = f'R{local.year % 100:02d}.{local.month}.{local.day}_V{version}'
    display = 'QWRT ' + revision

    release = root / 'etc/openwrt_release'
    original = release.read_text()
    backup = root / 'etc/aw1000-base-release'
    if not backup.exists():
        backup.write_text(original)
    base = backup.read_text()
    def base_value(key):
        match = re.search(r'^' + key + r"=['\"]([^'\"]*)['\"]$", base, re.MULTILINE)
        if not match:
            raise ValueError('原固件版本字段缺失：' + key)
        return match[1]

    # LuCI concatenates description + revision without inserting a separator.
    original = _assignment(original, 'DISTRIB_REVISION', revision)
    release.write_text(_assignment(original, 'DISTRIB_DESCRIPTION', 'QWRT '))

    defaults = root / 'etc/uci-defaults/99-default-settings'
    text = defaults.read_text()
    pattern = r'^echo "DISTRIB_REVISION=\'[^\']*\'" >> /etc/openwrt_release$'
    if len(re.findall(pattern, text, re.MULTILINE)) != 1:
        raise ValueError('原 QWRT 首次启动版本写入逻辑不符')
    text = re.sub(pattern, f'echo "DISTRIB_REVISION=\'{revision}\'" >> /etc/openwrt_release',
                  text, flags=re.MULTILINE)
    # The input has an optional iStore branch; both branches must retain QWRT.
    text = text.replace("DISTRIB_DESCRIPTION='iStoreOS '", "DISTRIB_DESCRIPTION='QWRT '")
    defaults.write_text(text)

    os_release = root / 'usr/lib/os-release'
    text = os_release.read_text()
    for key in ('PRETTY_NAME', 'OPENWRT_RELEASE'):
        text = _assignment(text, key, display, '"')
    os_release.write_text(text)
    date = local.date().isoformat()
    (root / 'etc/aw1000-build-date').write_text(date + '\n')
    return {'firmware_display': display, 'firmware_revision': revision,
            'build_date': date, 'build_timezone': 'Asia/Shanghai',
            'base_release': base_value('DISTRIB_RELEASE'),
            'base_revision': base_value('DISTRIB_REVISION')}
