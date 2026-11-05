"""Exercise the offline LED patch and fail-closed rootfs integration."""
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import led_hardware


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fixture_module():
    """A valid, relocatable AArch64 fixture with the reviewed entry/calls."""
    text = bytearray(0x99500)
    entry = led_hardware.HELPER_ENTRY
    text[entry:entry + 16] = (led_hardware.ORIGINAL_INSTRUCTION +
                              led_hardware.ENTRY_CONTEXT)
    for call in led_hardware.LED_CALLS:
        struct.pack_into('<I', text, call, 0x94000000)
    for call in led_hardware.HELPER_CALLERS:
        instruction = 0x94000000 | (((entry - call) // 4) & 0x3ffffff)
        struct.pack_into('<I', text, call, instruction)
    names = ('', '.text', '.shstrtab', '.strtab', '.symtab', '.rela.text', '.modinfo')
    string_names = b'\0'
    offsets = {'': 0}
    for name in names[1:]:
        offsets[name] = len(string_names)
        string_names += name.encode() + b'\0'
    symbol_strings = b'\0ipq_led_set_blink\0'
    symbols = bytes(24) + struct.pack('<IBBHQQ', 1, 0x10, 0, 0, 0, 0)
    relocs = b''.join(struct.pack('<QQq', address, (1 << 32) | 283, 0)
                      for address in led_hardware.LED_CALLS)
    modinfo = led_hardware.VERMAGIC + b'\0name=qca_ol\0'
    payloads = (bytes(text), string_names, symbol_strings, symbols, relocs, modinfo)
    image = bytearray(led_hardware.TEXT_OFFSET)
    headers = [(0,) * 10]
    for index, (name, payload) in enumerate(zip(names[1:], payloads), 1):
        image.extend(bytes((-len(image)) % 8))
        at = len(image)
        image.extend(payload)
        kind = 2 if name == '.symtab' else 4 if name == '.rela.text' else \
               3 if name in ('.shstrtab', '.strtab') else 1
        link = 3 if name == '.symtab' else 4 if name == '.rela.text' else 0
        info = 1 if name == '.rela.text' else 0
        headers.append((offsets[name], kind, 6 if name == '.text' else 0,
                        0, at, len(payload), link, info, 8,
                        24 if name in ('.symtab', '.rela.text') else 0))
    image.extend(bytes((-len(image)) % 8))
    section_offset = len(image)
    for header in headers:
        image.extend(struct.pack('<IIQQQQIIQQ', *header))
    ident = b'\x7fELF\x02\x01\x01' + bytes(9)
    struct.pack_into('<16sHHIQQQIHHHHHH', image, 0,
                     ident, 1, 183, 1, 0, 0, section_offset, 0, 64, 0, 0, 64, 7, 2)
    return bytes(image)


class LedHardwareTests(unittest.TestCase):
    def setUp(self):
        self.original = fixture_module()
        at = led_hardware.FILE_OFFSET
        self.patched = (self.original[:at] + led_hardware.RETURN_INSTRUCTION +
                        self.original[at + 4:])
        self.profile = mock.patch.multiple(
            led_hardware, ORIGINAL_SHA256=digest(self.original),
            PATCHED_SHA256=digest(self.patched))
        self.profile.start()
        self.addCleanup(self.profile.stop)

    def make_root(self, root):
        files = {
            led_hardware.MODULE_PATH: self.original,
            led_hardware.INI_PATH: b'# board settings\nled_gpio_enable_8074=1\nother=1\n',
        }
        for path in led_hardware.STARTUP_PATHS:
            files[path] = (b'#!/bin/sh\nif [ "$board_name" = "ap-hk09" ]; then\n'
                           b'\tupdate_internal_ini QCA8074V2_i.ini led_gpio_enable_8074 1\n'
                           b'fi\nenable_ol_stats 1\n')
        for path, data in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (root / led_hardware.MODULE_PATH).chmod(0o644)
        return files

    def test_only_reviewed_entry_changes_and_relocations_are_preserved(self):
        result, source_hash = led_hardware.patch_module(self.original)
        self.assertEqual(source_hash, digest(self.original))
        self.assertEqual(result, self.patched)
        self.assertEqual([i for i, (a, b) in enumerate(zip(self.original, result)) if a != b],
                         list(range(led_hardware.FILE_OFFSET, led_hardware.FILE_OFFSET + 4)))
        _, sections = led_hardware._elf_sections(result)
        for name in ('.rela.text', '.symtab', '.modinfo'):
            section = sections[name]
            span = slice(section['offset'], section['offset'] + section['size'])
            self.assertEqual(result[span], self.original[span])

    def test_root_install_is_idempotent_and_keeps_stats_setting(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.make_root(root)
            metadata = led_hardware.install(root)
            self.assertEqual(metadata['changed_bytes'], 4)
            self.assertEqual(metadata['sha256'], digest(self.patched))
            self.assertEqual(metadata['ap_statistics'], 'unchanged')
            self.assertEqual((root / led_hardware.MODULE_PATH).read_bytes(), self.patched)
            self.assertEqual((root / led_hardware.MODULE_PATH).stat().st_mode & 0o777, 0o644)
            self.assertIn(b'led_gpio_enable_8074=0\n', (root / led_hardware.INI_PATH).read_bytes())
            for path in led_hardware.STARTUP_PATHS:
                text = (root / path).read_text()
                self.assertIn('led_gpio_enable_8074 0\n', text)
                self.assertIn('enable_ol_stats 1\n', text)
            saved = {p: (root / p).read_bytes() for p in
                     (led_hardware.MODULE_PATH, led_hardware.INI_PATH) + led_hardware.STARTUP_PATHS}
            self.assertEqual(led_hardware.install(root)['changed_bytes'], 0)
            self.assertEqual(saved, {p: (root / p).read_bytes() for p in saved})

    def test_unknown_module_or_configuration_never_partially_installs(self):
        mutations = {
            'module': (led_hardware.MODULE_PATH, b'not the approved driver'),
            'duplicate_ini': (led_hardware.INI_PATH,
                              b'led_gpio_enable_8074=0\nled_gpio_enable_8074=1\n'),
            'unknown_ini': (led_hardware.INI_PATH, b'led_gpio_enable_8074=2\n'),
            'missing_setter': (led_hardware.STARTUP_PATHS[0], b'#!/bin/sh\n'),
            'duplicate_setter': (led_hardware.STARTUP_PATHS[1],
                b'update_internal_ini QCA8074V2_i.ini led_gpio_enable_8074 1\n' * 2),
            'unknown_setter': (led_hardware.STARTUP_PATHS[1],
                b'update_internal_ini QCA8074V2_i.ini led_gpio_enable_8074 2\n'),
        }
        for name, (path, content) in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                files = self.make_root(root)
                (root / path).write_bytes(content)
                before = {p: (root / p).read_bytes() for p in files}
                with self.assertRaisesRegex(ValueError, '拒绝未知基底'):
                    led_hardware.install(root)
                self.assertEqual(before, {p: (root / p).read_bytes() for p in files})

    def test_elf_and_instruction_guards_are_independent_of_hash_allowlist(self):
        def edit(data, at, value):
            result = bytearray(data)
            result[at:at + len(value)] = value
            return bytes(result)
        _, sections = led_hardware._elf_sections(self.original)
        relocation = sections['.rela.text']['offset']
        cases = {
            'wrong_architecture': edit(self.original, 18, struct.pack('<H', 62)),
            'wrong_entry': edit(self.original, led_hardware.FILE_OFFSET, bytes(4)),
            'entry_relocation': edit(self.original, relocation,
                                     struct.pack('<Q', led_hardware.HELPER_ENTRY)),
            'wrong_led_relocation': edit(self.original, relocation + 8,
                                         struct.pack('<Q', (1 << 32) | 282)),
            'wrong_caller': edit(self.original,
                                 led_hardware.TEXT_OFFSET + led_hardware.HELPER_CALLERS[0], bytes(4)),
        }
        for name, data in cases.items():
            with self.subTest(name=name), mock.patch.object(
                    led_hardware, 'ORIGINAL_SHA256', digest(data)):
                with self.assertRaisesRegex(ValueError, '拒绝未知基底'):
                    led_hardware.patch_module(data)

    def test_wrong_expected_result_hash_is_rejected(self):
        with mock.patch.object(led_hardware, 'PATCHED_SHA256', '0' * 64):
            with self.assertRaisesRegex(ValueError, '修补后模块 SHA256'):
                led_hardware.patch_module(self.original)


if __name__ == '__main__':
    unittest.main()
