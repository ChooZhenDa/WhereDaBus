"""Isolation and repeatability checks for a genuinely separate installation."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.configure import read_config
from scripts.install_config import (
    check_installation, read_selected_port, setup_installation,
    validate_installation, write_launcher_state,
)


class InstallConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / '.launcher-port').write_text('18081\n', encoding='ascii')
        self.output = io.StringIO()
        self.capture = redirect_stdout(self.output)
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)

    def create(self, key='TEST_KEY'):
        return setup_installation(self.root, prompt=lambda _: key)

    def test_fresh_setup_generates_separate_project_and_passwords(self):
        values = self.create()
        self.assertRegex(values['WDB_PROJECT'], r'^wheredabus-[0-9a-f]{16}$')
        self.assertRegex(values['POSTGRES_PASSWORD'], r'^[0-9a-f]{48}$')
        self.assertRegex(values['MONGO_PASSWORD'], r'^[0-9a-f]{48}$')
        self.assertNotEqual(values['POSTGRES_PASSWORD'], values['MONGO_PASSWORD'])
        self.assertEqual(values['APP_PORT'], '18081')
        self.assertEqual(values, read_config(self.root / '.env'))
        for name in ('POSTGRES_PASSWORD', 'MONGO_PASSWORD', 'LTA_ACCOUNT_KEY'):
            self.assertNotIn(values[name], self.output.getvalue())

    def test_two_clean_folders_get_different_identities_and_credentials(self):
        first = self.create()
        other = self.root / 'second'
        other.mkdir()
        (other / '.launcher-port').write_text('18082')
        second = setup_installation(other, prompt=lambda _: 'SECOND_KEY')
        for name in ('WDB_PROJECT', 'POSTGRES_PASSWORD', 'MONGO_PASSWORD'):
            self.assertNotEqual(first[name], second[name])
        self.assertEqual(second['APP_PORT'], '18082')

    def test_old_placeholder_config_cannot_adopt_old_project_or_passwords(self):
        original = b'POSTGRES_PASSWORD=GENERATE_A_RANDOM_HEX_PASSWORD\nMONGO_PASSWORD=placeholder\nLTA_ACCOUNT_KEY=OLD_KEY\nAPP_PORT=8081\n'
        (self.root / '.env').write_bytes(original)
        values = self.create('NEW_KEY')
        self.assertNotEqual(values['WDB_PROJECT'], 'wheredabus-fresh')
        self.assertEqual(values['APP_PORT'], '18081')
        self.assertEqual(values['LTA_ACCOUNT_KEY'], 'NEW_KEY')
        backups = list(self.root.glob('.env.before-standalone-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)

    def test_valid_legacy_credentials_are_not_adopted(self):
        original = 'POSTGRES_PASSWORD=' + 'a' * 48 + '\nMONGO_PASSWORD=' + 'b' * 48 + '\nAPP_PORT=8081\nLTA_ACCOUNT_KEY=OLD_KEY\n'
        (self.root / '.env').write_text(original)
        values = self.create()
        self.assertNotEqual(values['POSTGRES_PASSWORD'], 'a' * 48)
        self.assertNotEqual(values['MONGO_PASSWORD'], 'b' * 48)
        self.assertEqual(values['APP_PORT'], '18081')

    def test_repeat_setup_preserves_identity_credentials_port_and_file(self):
        original = self.create()
        original_bytes = (self.root / '.env').read_bytes()
        (self.root / '.launcher-port').write_text('18099')
        repeated = self.create('')
        self.assertEqual(repeated, original)
        self.assertEqual((self.root / '.env').read_bytes(), original_bytes)
        self.assertEqual(list(self.root.glob('.env.before-standalone-*')), [])

    def test_key_replacement_keeps_database_identity_and_comments(self):
        original = self.create('OLD_KEY')
        with (self.root / '.env').open('a') as stream:
            stream.write('# keep this comment\nCUSTOM=value\n')
        repeated = self.create('NEW_KEY')
        for name in ('WDB_PROJECT', 'POSTGRES_PASSWORD', 'MONGO_PASSWORD', 'APP_PORT'):
            self.assertEqual(repeated[name], original[name])
        self.assertEqual(repeated['LTA_ACCOUNT_KEY'], 'NEW_KEY')
        saved = (self.root / '.env').read_text()
        self.assertIn('# keep this comment', saved)
        self.assertIn('CUSTOM=value', saved)
        self.assertEqual(len(list(self.root.glob('.env.before-standalone-*'))), 1)

    def test_invalid_key_does_not_write_or_backup_existing_config(self):
        original = b'POSTGRES_PASSWORD=placeholder\nAPP_PORT=8081\n'
        (self.root / '.env').write_bytes(original)
        with self.assertRaisesRegex(ValueError, 'unsupported characters'):
            self.create('bad key\nINJECT=secret')
        self.assertEqual((self.root / '.env').read_bytes(), original)
        self.assertEqual(list(self.root.glob('.env.before-standalone-*')), [])

    def test_invalid_key_on_new_install_creates_no_config(self):
        with self.assertRaises(ValueError):
            self.create('bad key')
        self.assertFalse((self.root / '.env').exists())

    def test_cancelled_prompt_preserves_previous_config(self):
        original = b'POSTGRES_PASSWORD=placeholder\n'
        (self.root / '.env').write_bytes(original)
        def cancel(_):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            setup_installation(self.root, prompt=cancel)
        self.assertEqual((self.root / '.env').read_bytes(), original)
        self.assertFalse(list(self.root.glob('.env.before-standalone-*')))

    def test_damaged_managed_config_gets_new_identity_without_reusing_data(self):
        first = self.create()
        content = (self.root / '.env').read_text().replace(first['POSTGRES_PASSWORD'], 'placeholder')
        (self.root / '.env').write_text(content)
        second = self.create('NEW_KEY')
        self.assertNotEqual(second['WDB_PROJECT'], first['WDB_PROJECT'])
        self.assertNotEqual(second['MONGO_PASSWORD'], first['MONGO_PASSWORD'])
        self.assertEqual(list(self.root.glob('.env.before-standalone-*'))[0].read_text(), content)

    def test_bad_encoding_and_duplicate_entries_are_backed_up_before_new_setup(self):
        for original in (b'\xff\xfe\x00garbled', b'APP_PORT=8081\nAPP_PORT=8082\n'):
            with self.subTest(original=original):
                (self.root / '.env').write_bytes(original)
                values = self.create()
                self.assertEqual(check_installation(self.root), values)
                self.assertIn(original, [p.read_bytes() for p in self.root.glob('.env.before-standalone-*')])

    def test_check_refuses_legacy_without_rewriting_it(self):
        original = b'POSTGRES_PASSWORD=placeholder\n'
        (self.root / '.env').write_bytes(original)
        with self.assertRaisesRegex(ValueError, 'Run Setup.cmd'):
            check_installation(self.root)
        self.assertEqual((self.root / '.env').read_bytes(), original)

    def test_launcher_metadata_has_only_validated_public_fields(self):
        values = self.create()
        write_launcher_state(self.root, values)
        result = (self.root / '.launcher-state').read_text()
        self.assertEqual(result, 'LAUNCH_PROJECT=' + values['WDB_PROJECT'] + '\nLAUNCH_PORT=18081\nLAUNCH_HAS_KEY=1\n')
        for name in ('POSTGRES_PASSWORD', 'MONGO_PASSWORD', 'LTA_ACCOUNT_KEY'):
            self.assertNotIn(values[name], result)
        with self.assertRaises(ValueError):
            write_launcher_state(self.root, {**values, 'WDB_PROJECT': 'wheredabus-fresh & whoami'})

    def test_launcher_metadata_is_crlf_even_with_linux_default_text_writes(self):
        values = self.create()
        original_open = Path.open
        def linux_open(path, mode='r', *args, **kwargs):
            if 'b' not in mode and any(flag in mode for flag in ('w', 'a', 'x')):
                if len(args) < 4 and kwargs.get('newline') is None:
                    kwargs['newline'] = '\n'
            return original_open(path, mode, *args, **kwargs)
        with patch.object(Path, 'open', linux_open):
            write_launcher_state(self.root, values)
        expected = ('LAUNCH_PROJECT=' + values['WDB_PROJECT'] + '\r\n'
                    'LAUNCH_PORT=18081\r\nLAUNCH_HAS_KEY=1\r\n').encode('ascii')
        self.assertEqual((self.root / '.launcher-state').read_bytes(), expected)

    def test_blank_key_is_explicit_demo_mode(self):
        values = self.create('')
        write_launcher_state(self.root, values)
        self.assertIn('LAUNCH_HAS_KEY=0', (self.root / '.launcher-state').read_text())

    def test_fresh_setup_requires_valid_selected_port(self):
        for port in ('80', '65536', '18081&whoami', 'abc'):
            with self.subTest(port=port):
                (self.root / '.launcher-port').write_text(port)
                with self.assertRaises(ValueError):
                    self.create()
                self.assertFalse((self.root / '.env').exists())

    def test_existing_managed_setup_does_not_depend_on_port_selection_file(self):
        original = self.create()
        (self.root / '.launcher-port').unlink()
        self.assertEqual(self.create(''), original)

    def test_configuration_change_during_prompt_is_not_overwritten(self):
        def changed(_):
            (self.root / '.env').write_text('OTHER=created elsewhere\n')
            return 'KEY'
        with self.assertRaisesRegex(ValueError, 'changed'):
            setup_installation(self.root, prompt=changed)
        self.assertEqual((self.root / '.env').read_text(), 'OTHER=created elsewhere\n')

    def test_keep_key_cannot_emit_stale_identity_after_concurrent_change(self):
        self.create()
        def changed(_):
            path = self.root / '.env'
            path.write_text(path.read_text().replace('APP_PORT=18081', 'APP_PORT=18082'))
            return ''
        with self.assertRaisesRegex(ValueError, 'changed'):
            setup_installation(self.root, prompt=changed)
        self.assertEqual(read_config(self.root / '.env')['APP_PORT'], '18082')


if __name__ == '__main__':
    unittest.main()
