"""Regression tests for the Docker-backed Windows configuration helper."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.configure import (
    analytics_config, analytics_auto_config, apply_auto_stops, read_config,
    set_config_value, setup_config, validate_config,
    write_launcher_state,
)


class ConfigureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.temp.name)
        (self.root / '.env.example').write_text(
            '# retained comment\nPOSTGRES_PASSWORD=placeholder\nMONGO_PASSWORD=placeholder\n'
            'LTA_ACCOUNT_KEY=\nAPP_PORT=8081\nSAMPLE_STOPS=\nSAMPLE_SERVICES=\n',
            encoding='utf-8')
        self.output = io.StringIO()
        self.capture = redirect_stdout(self.output)
        self.capture.__enter__()

    def tearDown(self):
        self.capture.__exit__(None, None, None)
        self.temp.cleanup()

    def create(self, key=''):
        return setup_config(self.root, prompt=lambda _: key)

    def test_setup_generates_distinct_hex_passwords_without_printing_them(self):
        values = self.create()
        self.assertRegex(values['POSTGRES_PASSWORD'], r'^[a-f0-9]{48}$')
        self.assertRegex(values['MONGO_PASSWORD'], r'^[a-f0-9]{48}$')
        self.assertNotEqual(values['POSTGRES_PASSWORD'], values['MONGO_PASSWORD'])
        for name in ('POSTGRES_PASSWORD', 'MONGO_PASSWORD'):
            self.assertNotIn(values[name], self.output.getvalue())

    def test_key_is_saved_without_echoing_and_metadata_contains_no_secrets(self):
        values = self.create('example-private-key+/=')
        self.assertEqual(values['LTA_ACCOUNT_KEY'], 'example-private-key+/=')
        write_launcher_state(self.root, values)
        metadata = (self.root / '.launcher-state').read_text()
        self.assertEqual(metadata, 'LAUNCH_PORT=8081\nLAUNCH_HAS_KEY=1\n')
        self.assertNotIn(values['LTA_ACCOUNT_KEY'], self.output.getvalue())
        self.assertNotIn(values['POSTGRES_PASSWORD'], metadata)

    def test_skip_key_is_explicit_demo_flag(self):
        values = self.create()
        self.assertEqual(values['LTA_ACCOUNT_KEY'], '')
        write_launcher_state(self.root, values)
        self.assertIn('LAUNCH_HAS_KEY=0', (self.root / '.launcher-state').read_text())

    def test_repeat_setup_enter_preserves_credentials(self):
        original = self.create('existing-key')
        prompts = []
        def keep_prompt(message):
            prompts.append(message)
            return ''
        repeated = setup_config(self.root, existing_volumes=True, prompt=keep_prompt)
        self.assertEqual(original, repeated)
        self.assertEqual(prompts, ['New LTA AccountKey (Enter to keep current): '])

    def test_repeat_setup_replaces_key_without_changing_database_passwords(self):
        original = self.create('existing-key')
        repeated = setup_config(self.root, existing_volumes=True, prompt=lambda _: 'replacement-key')
        self.assertEqual(repeated['LTA_ACCOUNT_KEY'], 'replacement-key')
        self.assertEqual(repeated['POSTGRES_PASSWORD'], original['POSTGRES_PASSWORD'])
        self.assertEqual(repeated['MONGO_PASSWORD'], original['MONGO_PASSWORD'])
        self.assertNotIn('replacement-key', self.output.getvalue())

    def test_complete_passwords_exist_before_the_key_prompt(self):
        def inspect_prompt(_):
            contents = (self.root / '.env').read_text()
            self.assertNotIn('placeholder', contents)
            validate_config(read_config(self.root / '.env'))
            return ''
        setup_config(self.root, prompt=inspect_prompt)

    def test_key_can_be_added_after_demo_without_changing_database_passwords(self):
        first = self.create()
        second = setup_config(self.root, existing_volumes=True, prompt=lambda _: 'new-key')
        self.assertEqual(second['POSTGRES_PASSWORD'], first['POSTGRES_PASSWORD'])
        self.assertEqual(second['MONGO_PASSWORD'], first['MONGO_PASSWORD'])
        self.assertEqual(second['LTA_ACCOUNT_KEY'], 'new-key')

    def test_existing_volumes_without_config_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'Restore this installation'):
            setup_config(self.root, existing_volumes=True, prompt=lambda _: '')
        self.assertFalse((self.root / '.env').exists())

    def test_invalid_port_or_password_is_refused(self):
        values = self.create()
        for name, bad in (('APP_PORT', '80'), ('APP_PORT', '65536'),
                          ('APP_PORT', '8081&echo'), ('POSTGRES_PASSWORD', 'short'),
                          ('MONGO_PASSWORD', 'unsafe$password')):
            with self.subTest(name=name, bad=bad):
                with self.assertRaises(ValueError):
                    validate_config({**values, name: bad})

    def test_invalid_key_cannot_inject_environment_entries(self):
        with self.assertRaisesRegex(ValueError, 'unsupported characters'):
            self.create('key\nAPP_PORT=9000')
        values = read_config(self.root / '.env')
        self.assertEqual(values['LTA_ACCOUNT_KEY'], '')
        self.assertEqual(values['APP_PORT'], '8081')

    def test_updates_preserve_comments_and_other_entries(self):
        self.create()
        path = self.root / '.env'
        set_config_value(path, 'CUSTOM', 'unchanged')
        set_config_value(path, 'APP_PORT', '8090')
        values = read_config(path)
        self.assertEqual(values['CUSTOM'], 'unchanged')
        self.assertEqual(values['APP_PORT'], '8090')
        self.assertIn('# retained comment', path.read_text())

    def test_duplicate_entries_are_refused(self):
        self.create()
        path = self.root / '.env'
        with path.open('a') as stream:
            stream.write('APP_PORT=8090\n')
        with self.assertRaisesRegex(ValueError, 'appears more than once'):
            read_config(path)

    def test_analytics_requires_key_and_valid_stop_cohort(self):
        values = self.create('key')
        valid = {**values, 'SAMPLE_STOPS': '01001,01002', 'SAMPLE_SERVICES': '7,15A'}
        self.assertEqual(validate_config(valid, analytics=True), valid)
        for changes in ({'LTA_ACCOUNT_KEY': ''}, {'SAMPLE_STOPS': ''},
                        {'SAMPLE_STOPS': '1001'}, {'SAMPLE_STOPS': '01001,'},
                        {'SAMPLE_STOPS': ','.join(f'{n:05d}' for n in range(21))},
                        {'SAMPLE_SERVICES': '15,A B'}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    validate_config({**valid, **changes}, analytics=True)

    def test_metadata_rejects_non_numeric_or_out_of_range_port(self):
        for bad in ('8081&calc', '0', '65536'):
            with self.subTest(port=bad):
                with self.assertRaises(ValueError):
                    write_launcher_state(self.root, {'APP_PORT': bad})

    def test_analytics_prompts_for_stops_and_preserves_credentials_and_comments(self):
        original = self.create('private-existing-key')
        set_config_value(self.root / '.env', 'CUSTOM', 'unchanged')
        answers = iter((' 01001, 01002,01001 ', '7, 15A'))
        updated = analytics_config(self.root, prompt=lambda _: next(answers))
        self.assertEqual(updated['SAMPLE_STOPS'], '01001,01002')
        self.assertEqual(updated['SAMPLE_SERVICES'], '7,15A')
        saved = read_config(self.root / '.env')
        self.assertEqual(saved, updated)
        self.assertEqual(saved['CUSTOM'], 'unchanged')
        self.assertIn('# retained comment', (self.root / '.env').read_text())
        for name in ('LTA_ACCOUNT_KEY', 'POSTGRES_PASSWORD', 'MONGO_PASSWORD', 'APP_PORT'):
            self.assertEqual(saved[name], original[name])
        for name in ('LTA_ACCOUNT_KEY', 'POSTGRES_PASSWORD', 'MONGO_PASSWORD'):
            self.assertNotIn(saved[name], self.output.getvalue())

    def test_analytics_enter_keeps_existing_stops_and_services(self):
        self.create('key')
        set_config_value(self.root / '.env', 'SAMPLE_STOPS', '01001,01002')
        set_config_value(self.root / '.env', 'SAMPLE_SERVICES', '7,15A')
        before = read_config(self.root / '.env')
        self.assertEqual(analytics_config(self.root, prompt=lambda _: ''), before)

    def test_analytics_star_removes_existing_service_filter(self):
        self.create('key')
        set_config_value(self.root / '.env', 'SAMPLE_STOPS', '01001')
        set_config_value(self.root / '.env', 'SAMPLE_SERVICES', '7')
        answers = iter(('', '*'))
        updated = analytics_config(self.root, prompt=lambda _: next(answers))
        self.assertEqual(updated['SAMPLE_STOPS'], '01001')
        self.assertEqual(updated['SAMPLE_SERVICES'], '')
        self.assertEqual(read_config(self.root / '.env'), updated)

    def test_analytics_retries_bad_inputs_without_partial_writes(self):
        self.create('key')
        path = self.root / '.env'
        before = path.read_bytes()
        answers = iter(('', '1234', '01001,', '01001', '7,A B', '7'))
        def answer(_):
            self.assertEqual(path.read_bytes(), before)
            return next(answers)
        updated = analytics_config(self.root, prompt=answer)
        self.assertEqual(updated['SAMPLE_STOPS'], '01001')
        self.assertEqual(updated['SAMPLE_SERVICES'], '7')
        self.assertIn('five-digit stop codes', self.output.getvalue())
        self.assertIn('valid service numbers', self.output.getvalue())

    def test_analytics_cancellation_preserves_original_file(self):
        self.create('key')
        path = self.root / '.env'
        before = path.read_bytes()
        answers = iter(('01001',))
        def cancel(_):
            try:
                return next(answers)
            except StopIteration:
                raise EOFError
        with self.assertRaises(EOFError):
            analytics_config(self.root, prompt=cancel)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.env.analytics-*')), [])

    def test_analytics_failed_replace_preserves_original_and_cleans_temporary(self):
        self.create('key')
        path = self.root / '.env'
        before = path.read_bytes()
        answers = iter(('01001', ''))
        with patch('scripts.configure.os.replace', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                analytics_config(self.root, prompt=lambda _: next(answers))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.env.analytics-*')), [])

    def test_analytics_without_key_fails_before_prompting(self):
        self.create()
        def unexpected_prompt(_):
            self.fail('Must not prompt until a key is configured')
        with self.assertRaisesRegex(ValueError, 'needs an LTA key'):
            analytics_config(self.root, prompt=unexpected_prompt)

    def save_selection(self, stops=None):
        selection = {'stops': stops if stops is not None else [
            {'stopCode': '01001', 'description': 'First stop', 'services': ['7']},
            {'stopCode': '01002', 'description': 'Second stop', 'services': ['15A']},
        ]}
        (self.root / '.launcher-sampling.json').write_text(json.dumps(selection), encoding='utf-8')

    def test_automatic_start_needs_no_prompts_or_initial_config_changes(self):
        original = self.create('private-key')
        before = (self.root / '.env').read_bytes()
        with patch('builtins.input', side_effect=AssertionError('Unexpected prompt')):
            values, needs = analytics_auto_config(self.root)
        self.assertTrue(needs)
        self.assertEqual(values, original)
        self.assertEqual((self.root / '.env').read_bytes(), before)
        write_launcher_state(self.root, values, needs)
        metadata = (self.root / '.launcher-state').read_text()
        self.assertIn('LAUNCH_NEEDS_STOPS=1', metadata)
        self.assertNotIn('private-key', metadata + self.output.getvalue())

    def test_automatic_start_retains_valid_existing_selection_without_writing(self):
        self.create('key')
        set_config_value(self.root / '.env', 'SAMPLE_STOPS', '01002,01001')
        set_config_value(self.root / '.env', 'SAMPLE_SERVICES', '7')
        before = (self.root / '.env').read_bytes()
        values, needs = analytics_auto_config(self.root)
        self.assertFalse(needs)
        self.assertEqual(values['SAMPLE_STOPS'], '01002,01001')
        self.assertEqual(values['SAMPLE_SERVICES'], '7')
        self.assertEqual((self.root / '.env').read_bytes(), before)
        # A stale selection file must never override the user's configured stops.
        self.save_selection([{'stopCode': '99999'}])
        self.assertEqual(apply_auto_stops(self.root), values)
        self.assertEqual((self.root / '.env').read_bytes(), before)

    def test_automatic_start_rejects_existing_invalid_selection_or_filter(self):
        self.create('key')
        for changes in ({'SAMPLE_STOPS': '1234', 'SAMPLE_SERVICES': ''},
                        {'SAMPLE_STOPS': '', 'SAMPLE_SERVICES': '7,A B'}):
            for name, value in changes.items():
                set_config_value(self.root / '.env', name, value)
            before = (self.root / '.env').read_bytes()
            with self.assertRaises(ValueError):
                analytics_auto_config(self.root)
            self.assertEqual((self.root / '.env').read_bytes(), before)

    def test_apply_automatic_selection_preserves_credentials_filter_and_comments(self):
        original = self.create('private-key')
        set_config_value(self.root / '.env', 'SAMPLE_SERVICES', '7')
        self.save_selection()
        values = apply_auto_stops(self.root)
        self.assertEqual(values['SAMPLE_STOPS'], '01001,01002')
        self.assertEqual(values['SAMPLE_SERVICES'], '7')
        self.assertEqual(read_config(self.root / '.env'), values)
        self.assertIn('# retained comment', (self.root / '.env').read_text())
        for name in ('LTA_ACCOUNT_KEY', 'POSTGRES_PASSWORD', 'MONGO_PASSWORD', 'APP_PORT'):
            self.assertEqual(values[name], original[name])
        for name in ('LTA_ACCOUNT_KEY', 'POSTGRES_PASSWORD', 'MONGO_PASSWORD'):
            self.assertNotIn(original[name], self.output.getvalue())

    def test_invalid_automatic_payloads_never_change_config(self):
        self.create('key')
        before = (self.root / '.env').read_bytes()
        invalid = [[], [{'stopCode': '01001'}, {'stopCode': '01001'}],
                   [{'stopCode': 1001}], [{'stopCode': '01001\nLTA_ACCOUNT_KEY=other'}],
                   [{'stopCode': f'{number:05d}'} for number in range(11)],
                   [None], ['01001']]
        for stops in invalid:
            with self.subTest(stops=stops):
                self.save_selection(stops)
                with self.assertRaises(ValueError):
                    apply_auto_stops(self.root)
                self.assertEqual((self.root / '.env').read_bytes(), before)

    def test_unreadable_automatic_payload_is_rejected_without_writes(self):
        self.create('key')
        before = (self.root / '.env').read_bytes()
        path = self.root / '.launcher-sampling.json'
        for contents in ('not JSON', '{"stops": "01001"}', '[' + ' ' * 65537):
            with self.subTest(contents=contents[:30]):
                path.write_text(contents)
                with self.assertRaises(ValueError):
                    apply_auto_stops(self.root)
                self.assertEqual((self.root / '.env').read_bytes(), before)

    def test_automatic_selection_missing_key_or_result_is_actionable(self):
        self.create()
        with self.assertRaisesRegex(ValueError, 'needs an LTA key'):
            analytics_auto_config(self.root)
        set_config_value(self.root / '.env', 'LTA_ACCOUNT_KEY', 'key')
        with self.assertRaisesRegex(ValueError, 'Refresh-Data.cmd'):
            apply_auto_stops(self.root)

    def test_automatic_selection_failed_replace_retains_config(self):
        self.create('key')
        self.save_selection()
        before = (self.root / '.env').read_bytes()
        with patch('scripts.configure.os.replace', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                apply_auto_stops(self.root)
        self.assertEqual((self.root / '.env').read_bytes(), before)
        self.assertEqual(list(self.root.glob('.env.analytics-*')), [])


if __name__ == '__main__':
    unittest.main()
