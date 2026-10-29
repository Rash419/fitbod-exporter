import base64
import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
import zipfile

import android_export
from extract_exercise_names import extract
import finalize_export
import import_fitbod_har


class ToolingTests(unittest.TestCase):
    def test_device_selection_requires_one_authorized_device(self):
        listing = b'List of devices attached\nexample-device\tdevice\nother\tunauthorized\n'
        with patch('android_export.run', return_value=listing):
            self.assertEqual(android_export.select_device('adb', ''), 'example-device')
        with patch('android_export.run', return_value=listing + b'second\tdevice\n'):
            with self.assertRaisesRegex(RuntimeError, 'one Android device'):
                android_export.select_device('adb', '')

    def test_sdk_versions_sort_numerically(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for version in ('9.0.0', '36.0.0', '36.1.0'):
                path = root / 'build-tools' / version / 'lib/d8.jar'
                path.parent.mkdir(parents=True)
                path.touch()
            with patch('android_export.SDK', root):
                self.assertIn('36.1.0', str(android_export.newest_sdk_file('build-tools/*/lib/d8.jar')))

    def test_apk_mapping_uses_external_exercise_ids(self):
        with TemporaryDirectory() as temp:
            apk = Path(temp) / 'fixture.apk'
            with zipfile.ZipFile(apk, 'w') as archive:
                archive.writestr('res/raw/exercises_production.json', json.dumps({'data': [
                    {'id': '291', 'attributes': {'external_resource_id': 218, 'name': 'Leg Press'}}]}))
            self.assertEqual(extract(apk), {'218': 'Leg Press'})

    def test_har_capture_only_does_not_send_network_requests(self):
        payload = base64.urlsafe_b64encode(json.dumps({'sub': 'fixture', 'exp': time.time()+3600}).encode()).decode().rstrip('=')
        token = 'eyJhbGciOiJIUzI1NiJ9.' + payload + '.fixture_signature'
        har = {'log': {'entries': [{'request': {'url': 'https://app.fitbod.me/api/auth/login'},
                'response': {'status': 200, 'cookies': [{'name': 'pegasus-refresh-token', 'value': token}]}}]}}
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'fixture.har'
            source.write_text(json.dumps(har))
            target = root / 'refresh-token.txt'
            output = io.StringIO()
            with patch('import_fitbod_har.PRIVATE', root), patch('import_fitbod_har.TOKEN', target), patch('import_fitbod_har.request') as network, patch('sys.argv', ['import_fitbod_har.py', str(source), '--capture-only']), contextlib.redirect_stdout(output):
                import_fitbod_har.main()
            network.assert_not_called()
            self.assertEqual(target.read_text(), token)
            self.assertNotIn(token, output.getvalue())

    def test_empty_history_finalizes_without_index_errors(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'
            source.mkdir()
            (source / 'status.json').write_text('{"complete":true,"workouts":0}')
            (source / 'page-00001.json').write_text('{"data":[]}')
            (root / 'exercise-names.json').write_text('{}')
            catalogue = root / 'custom.json'
            catalogue.write_text('{"data":[]}')
            with patch('finalize_export.PRIVATE', root), contextlib.redirect_stdout(io.StringIO()):
                finalize_export.finalize(source, catalogue, 'UTC')
            summary = json.loads((root / 'workout-export/export-summary.json').read_text())
            self.assertEqual(summary['date_range_local'], [])
            self.assertEqual(summary['strength_sets'], 0)


if __name__ == '__main__':
    unittest.main()
