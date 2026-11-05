from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from build_stamp import stamp


class BuildStampTests(unittest.TestCase):
    def fixture(self, folder):
        root = Path(folder)
        (root / 'etc/uci-defaults').mkdir(parents=True)
        (root / 'usr/lib').mkdir(parents=True)
        (root / 'etc/openwrt_release').write_text(
            "DISTRIB_ID='QWRT'\nDISTRIB_RELEASE='25.12.2'\n"
            "DISTRIB_REVISION='r47986-d312c785fd'\nDISTRIB_TARGET='ipq807x/generic'\n"
            "DISTRIB_ARCH='aarch64_cortex-a53'\nDISTRIB_DESCRIPTION='QWRT 25.12.2 r47986-d312c785fd'\n")
        (root / 'usr/lib/os-release').write_text(
            'PRETTY_NAME="QWRT 25.12.2"\nVERSION_ID="25.12.2"\n'
            'BUILD_ID="r47986-d312c785fd"\nOPENWRT_RELEASE="QWRT 25.12.2 r47986-d312c785fd"\n')
        (root / 'etc/uci-defaults/99-default-settings').write_text(
            "sed -i '/DISTRIB_REVISION/d' /etc/openwrt_release\n"
            'echo "DISTRIB_REVISION=\'R26.5.5 (QSDK 12.5)\'" >> /etc/openwrt_release\n'
            "sed -i '/DISTRIB_DESCRIPTION/d' /etc/openwrt_release\n"
            'if [ -f /bin/is-opkg ]; then\n'
            '  echo "DISTRIB_DESCRIPTION=\'iStoreOS \'" >> /etc/openwrt_release\n'
            'else\n  echo "DISTRIB_DESCRIPTION=\'QWRT \'" >> /etc/openwrt_release\nfi\n')
        return root

    def test_shanghai_build_date_survives_both_first_boot_branches(self):
        for istore in (False, True):
            with self.subTest(istore=istore), tempfile.TemporaryDirectory() as folder:
                root = self.fixture(folder)
                meta = stamp(root, '1.1.5', datetime(2026, 10, 26, 16, 1, tzinfo=timezone.utc))
                self.assertEqual(meta['firmware_display'], 'QWRT R26.10.27_V1.1.5')
                self.assertEqual(meta['build_date'], '2026-10-27')
                self.assertEqual(meta['base_revision'], 'r47986-d312c785fd')
                marker = root / 'is-opkg'
                if istore:
                    marker.touch()
                # Only the harmless version block exists in this fixture.
                script = (root / 'etc/uci-defaults/99-default-settings').read_text()
                script = script.replace('/etc/openwrt_release', str(root / 'etc/openwrt_release'))
                script = script.replace('/bin/is-opkg', str(marker))
                subprocess.run(['sh', '-c', script], check=True)
                version = subprocess.check_output(
                    ['sh', '-c', '. "$1"; printf "%s%s" "$DISTRIB_DESCRIPTION" "$DISTRIB_REVISION"',
                     'version', str(root / 'etc/openwrt_release')], text=True)
                self.assertEqual(version, 'QWRT R26.10.27_V1.1.5')
                release = (root / 'etc/openwrt_release').read_text()
                self.assertIn("DISTRIB_RELEASE='25.12.2'", release)
                self.assertIn("DISTRIB_TARGET='ipq807x/generic'", release)
                self.assertIn("DISTRIB_ARCH='aarch64_cortex-a53'", release)
                os_release = (root / 'usr/lib/os-release').read_text()
                self.assertIn('PRETTY_NAME="QWRT R26.10.27_V1.1.5"', os_release)
                self.assertIn('VERSION_ID="25.12.2"', os_release)
                self.assertIn('BUILD_ID="r47986-d312c785fd"', os_release)

    def test_unpadded_january_and_repeat_stamping_preserve_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            instant = datetime(2027, 1, 1, tzinfo=timezone.utc)
            first = stamp(root, '1.1.5', instant)
            backup = (root / 'etc/aw1000-base-release').read_bytes()
            self.assertEqual(first['firmware_revision'], 'R27.1.1_V1.1.5')
            self.assertEqual(stamp(root, '1.1.5', instant), first)
            self.assertEqual((root / 'etc/aw1000-base-release').read_bytes(), backup)
            self.assertIn(b"DISTRIB_REVISION='r47986-d312c785fd'", backup)

    def test_naive_build_instant_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            with self.assertRaisesRegex(ValueError, '时区'):
                stamp(root, '1.1.5', datetime(2026, 10, 27))

    def test_invalid_project_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.fixture(folder)
            with self.assertRaisesRegex(ValueError, '工程版本'):
                stamp(root, 'v1.1.5', datetime(2026, 10, 27, tzinfo=timezone.utc))


if __name__ == '__main__':
    unittest.main()
