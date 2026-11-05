"""Isolate the panel lamps from this exact QWRT driver's activity callbacks.

The original LED-only helper also handles LED GPIOs and LED timers. Returning
before its first stack instruction leaves AP/enhanced statistics intact. This
is an offline rootfs patch, never a live module reload or a register write.
"""
import hashlib
from pathlib import Path
import re
import struct

ORIGINAL_SHA256 = 'e09f70633bc21f932078484e6e6457800e7b7e901b2fab5835e73ad5a864bbd9'
PATCHED_SHA256 = '9b5bb825cf1c520dc5b7393fd0134f06bcdda7fc3feead52dfe82d6dfd7b3de4'
MODULE_PATH = 'lib/modules/5.4.213/qca_ol.ko'
INI_PATH = 'ini/internal/QCA8074V2_i.ini'
STARTUP_PATHS = ('lib/wifi/qcawifi.sh', 'lib/wifi/qcawificfg80211.sh')
TEXT_OFFSET = 0x68
HELPER_ENTRY = 0x99180
FILE_OFFSET = TEXT_OFFSET + HELPER_ENTRY
ORIGINAL_INSTRUCTION = bytes.fromhex('fd7bbaa9')
RETURN_INSTRUCTION = bytes.fromhex('c0035fd6')
ENTRY_CONTEXT = bytes.fromhex('fd030091f4d701a915604091')
LED_CALLS = (0x992d8, 0x993ac)
HELPER_CALLERS = (0x99424, 0x9947c)
VERMAGIC = b'vermagic=5.4.213 SMP preempt mod_unload aarch64'


def _require(condition, message):
    if not condition:
        raise ValueError('AW1000 LED 补丁拒绝未知基底：' + message)


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _cstring(data, offset):
    _require(0 <= offset < len(data), 'ELF 字符串位置越界')
    end = data.find(b'\0', offset)
    _require(end >= 0, 'ELF 字符串缺少结尾')
    return data[offset:end].decode('ascii')


def _elf_sections(data):
    _require(len(data) >= 64 and data[:7] == b'\x7fELF\x02\x01\x01',
             '需要 ELF64 little-endian 模块')
    kind, machine, version = struct.unpack_from('<HHI', data, 16)
    _require((kind, machine, version) == (1, 183, 1), '需要 AArch64 relocatable 模块')
    shoff = struct.unpack_from('<Q', data, 40)[0]
    size, count, names_index = struct.unpack_from('<HHH', data, 58)
    _require(size == 64 and 0 < names_index < count and
             shoff >= 64 and shoff + count * size <= len(data), 'ELF section table 无效')
    sections = []
    for index in range(count):
        h = struct.unpack_from('<IIQQQQIIQQ', data, shoff + index * size)
        section = dict(index=index, name_offset=h[0], type=h[1], flags=h[2],
                       offset=h[4], size=h[5], link=h[6], info=h[7], entsize=h[9])
        _require(h[1] == 8 or h[4] + h[5] <= len(data), 'ELF section 内容越界')
        sections.append(section)
    names = sections[names_index]
    _require(names['type'] == 3, 'ELF section 名称表类型无效')
    strings = data[names['offset']:names['offset'] + names['size']]
    by_name = {}
    for section in sections:
        section['name'] = _cstring(strings, section['name_offset'])
        _require(section['name'] not in by_name, 'ELF section 名称重复')
        by_name[section['name']] = section
    return sections, by_name


def _validate_elf(data, patched):
    sections, names = _elf_sections(data)
    _require(all(n in names for n in ('.text', '.modinfo', '.rela.text')),
             '缺少原模块 section')
    text = names['.text']
    _require(text['type'] == 1 and text['flags'] & 4 and
             text['offset'] == TEXT_OFFSET and text['size'] >= 0x99480,
             'LED helper 不在预期代码段')
    expected = RETURN_INSTRUCTION if patched else ORIGINAL_INSTRUCTION
    _require(data[FILE_OFFSET:FILE_OFFSET + 4] == expected and
             data[FILE_OFFSET + 4:FILE_OFFSET + 16] == ENTRY_CONTEXT,
             'LED helper 入口指令或上下文不同')
    modinfo = names['.modinfo']
    info = data[modinfo['offset']:modinfo['offset'] + modinfo['size']].split(b'\0')
    _require(VERMAGIC in info and b'name=qca_ol' in info, '模块名称或 vermagic 不同')
    for caller in HELPER_CALLERS:
        instruction = struct.unpack_from('<I', data, TEXT_OFFSET + caller)[0]
        immediate = instruction & 0x3ffffff
        if immediate & 0x2000000:
            immediate -= 0x4000000
        _require(instruction >> 26 == 0b100101 and
                 caller + immediate * 4 == HELPER_ENTRY, 'LED helper 调用入口不同')
    found = []
    for reloc in sections:
        if reloc['type'] != 4 or reloc['info'] != text['index']:
            continue
        _require(reloc['entsize'] == 24 and reloc['size'] % 24 == 0 and
                 0 < reloc['link'] < len(sections), 'ELF RELA 格式无效')
        symbols = sections[reloc['link']]
        _require(symbols['type'] == 2 and symbols['entsize'] == 24 and
                 0 < symbols['link'] < len(sections), 'ELF symbol table 无效')
        strings_section = sections[symbols['link']]
        _require(strings_section['type'] == 3, 'ELF symbol 字符串表无效')
        strings = data[strings_section['offset']:
                       strings_section['offset'] + strings_section['size']]
        for offset in range(reloc['offset'], reloc['offset'] + reloc['size'], 24):
            address, value, addend = struct.unpack_from('<QQq', data, offset)
            _require(not HELPER_ENTRY <= address < HELPER_ENTRY + 4,
                     'LED helper 入口存在重定位')
            if address not in LED_CALLS:
                continue
            symbol_index, relocation_type = value >> 32, value & 0xffffffff
            _require(symbol_index * 24 + 24 <= symbols['size'], 'LED symbol 越界')
            symbol = symbols['offset'] + symbol_index * 24
            name_offset = struct.unpack_from('<I', data, symbol)[0]
            _require(relocation_type == 283 and addend == 0 and
                     _cstring(strings, name_offset) == 'ipq_led_set_blink' and
                     data[TEXT_OFFSET + address:TEXT_OFFSET + address + 4] ==
                     bytes.fromhex('00000094'), 'LED 调用或 CALL26 重定位不同')
            found.append(address)
    _require(sorted(found) == list(LED_CALLS), 'LED 调用重定位缺失或重复')


def patch_module(data):
    """Return only the approved four-byte patch, accepting its exact result."""
    digest = _sha256(data)
    _require(digest in (ORIGINAL_SHA256, PATCHED_SHA256), 'qca_ol.ko SHA256 不匹配')
    already_patched = digest == PATCHED_SHA256
    _validate_elf(data, already_patched)
    original = data[:FILE_OFFSET] + ORIGINAL_INSTRUCTION + data[FILE_OFFSET + 4:]
    _require(_sha256(original) == ORIGINAL_SHA256, '重建原模块校验失败')
    result = original[:FILE_OFFSET] + RETURN_INSTRUCTION + original[FILE_OFFSET + 4:]
    _require(len(result) == len(original) and result[:FILE_OFFSET] == original[:FILE_OFFSET]
             and result[FILE_OFFSET + 4:] == original[FILE_OFFSET + 4:]
             and sum(a != b for a, b in zip(original, result)) == 4,
             '补丁超出已审核的四字节范围')
    _require(_sha256(result) == PATCHED_SHA256, '修补后模块 SHA256 不匹配')
    _validate_elf(result, True)
    return result, digest


def _disable_gpio_ini(data):
    text = data.decode('utf-8')
    lines = re.findall(r'(?m)^[ \t]*led_gpio_enable_8074[ \t]*=.*$', text)
    _require(len(lines) == 1, 'INI LED GPIO 开关缺失或重复')
    pattern = r'(?m)^([ \t]*led_gpio_enable_8074[ \t]*=[ \t]*)[01]([ \t]*\r?)$'
    result, count = re.subn(pattern, r'\g<1>0\2', text)
    _require(count == 1, 'INI LED GPIO 开关格式或值不同')
    return result.encode('utf-8')


def _disable_gpio_startup(data):
    text = data.decode('utf-8')
    command = r'update_internal_ini[ \t]+QCA8074V2_i\.ini[ \t]+led_gpio_enable_8074'
    lines = re.findall(r'(?m)^[ \t]*' + command + r'(?:[ \t].*|$)', text)
    _require(len(lines) == 1, '启动脚本 LED GPIO setter 缺失或重复')
    pattern = r'(?m)^([ \t]*' + command + r'[ \t]+)[01]([ \t]*\r?)$'
    result, count = re.subn(pattern, r'\g<1>0\2', text)
    _require(count == 1, '启动脚本 LED GPIO setter 格式或值不同')
    return result.encode('utf-8')


def install(root):
    """Patch an extracted rootfs after validating every input before writing."""
    root = Path(root)
    paths = (MODULE_PATH, INI_PATH) + STARTUP_PATHS
    _require(all((root / p).is_file() for p in paths), '原固件 LED 文件不完整')
    inputs = {p: (root / p).read_bytes() for p in paths}
    module, input_hash = patch_module(inputs[MODULE_PATH])
    outputs = {MODULE_PATH: module, INI_PATH: _disable_gpio_ini(inputs[INI_PATH])}
    outputs.update({p: _disable_gpio_startup(inputs[p]) for p in STARTUP_PATHS})
    for path, data in outputs.items():
        if data != inputs[path]:
            (root / path).write_bytes(data)
    return {
        'profile': 'qwrt-5.4.213-led-helper-ret',
        'module': MODULE_PATH,
        'input_sha256': input_hash,
        'baseline_sha256': ORIGINAL_SHA256,
        'sha256': PATCHED_SHA256,
        'patch_file_offset': FILE_OFFSET,
        'changed_bytes': 0 if input_hash == PATCHED_SHA256 else 4,
        'led_gpio_enable_8074': 0,
        'startup_scripts': list(STARTUP_PATHS),
        'ap_statistics': 'unchanged',
        'dt_mapping': 'unchanged',
    }
