import unittest
import io
import json
import urllib.error
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path
from fitbod_export import (fetch_pages, flatten, csv_cell, local_credentials, running_only,
                           history_url, request, JSONAPI, FitbodHTTPError)


class ExportTests(unittest.TestCase):
    def test_deleted_set_tombstones_and_deleted_exercises_are_excluded(self):
        live = {'exercise_external_resource_id': 2, 'set_breakdown': {'individual_sets': [
            {'reps': 8, 'weight': 50, 'deletedAt': None},
            {'reps': 8, 'weight': 50, 'deletedAt': '2026-01-01T00:00:00Z'}]}}
        removed = {'deleted': True, 'exercise_external_resource_id': 3,
                   'set_breakdown': {'individual_sets': [{'reps': 10, 'weight': 30}]}}
        workout = {'id': 'test', 'attributes': {'exercise_sets': [live, removed],
                   'circuits': [{'deleted': True, 'exercise_sets': [live]}]}}
        rows = list(flatten([workout], {'2': 'Bench Press'}))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['exercise_name'], 'Bench Press')

    def test_history_query_matches_installed_app_and_pages_without_filter(self):
        self.assertEqual(history_url(1), 'https://nautilus.fitbod.me/api/v3/workout_data?page[size]=1000&sort=-date_performed')
        self.assertTrue(history_url(2).endswith('&page[number]=2'))
        self.assertNotIn('filter', history_url(2))

    def test_authenticated_reads_use_jsonapi_accept_header(self):
        response = io.BytesIO(b'{"data": []}')
        response.headers = {}
        with patch('fitbod_export.urllib.request.build_opener') as builder:
            builder.return_value.open.return_value = response
            request(history_url(1), token='test-session')
            sent = builder.return_value.open.call_args.args[0]
            self.assertEqual(sent.get_header('Accept'), JSONAPI)

    def test_cloudflare_error_is_identified_without_saving_request_token(self):
        headers = {'Content-Type': 'text/html', 'Server': 'cloudflare',
                   'Set-Cookie': 'must-not-save-response-cookie'}
        failure = urllib.error.HTTPError(history_url(1), 403, 'Forbidden', headers,
                                        io.BytesIO(b'<html>Cloudflare: blocked</html>'))
        with TemporaryDirectory() as tmp, patch('fitbod_export.PRIVATE', Path(tmp)), patch('fitbod_export.urllib.request.build_opener') as builder:
            builder.return_value.open.side_effect = failure
            with self.assertRaises(FitbodHTTPError) as caught:
                request(history_url(1), token='must-not-save-request-token')
            error = caught.exception
            self.assertIn('Cloudflare HTML', str(error))
            diagnostic = error.diagnostic.read_text()
            self.assertNotIn('must-not-save', diagnostic)
            self.assertEqual(json.loads(diagnostic)['status'], 403)

    def test_running_sessions_excluded_but_mixed_sessions_retained(self):
        names = {'1': 'Running', '2': 'Bench Press'}
        run = {'exercise_external_resource_id': 1}
        bench = {'exercise_external_resource_id': 2}
        self.assertTrue(running_only({'attributes': {'exercise_sets': [run]}}, names))
        self.assertFalse(running_only({'attributes': {'exercise_sets': [run, bench]}}, names))
        self.assertFalse(running_only({'attributes': {}}, names))

    def test_dotenv_credentials_are_literal_and_environment_can_override(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.env').write_text('FITBOD_EMAIL="test@example.com" # note\n'
                                      'FITBOD_PASSWORD=\'literal$()#value\'\n'
                                      'UNRELATED=ignored\n')
            with patch('fitbod_export.ROOT', root), patch.dict('os.environ', {}, clear=True):
                values = local_credentials()
                self.assertEqual(values, {'FITBOD_EMAIL': 'test@example.com',
                                          'FITBOD_PASSWORD': 'literal$()#value'})
            with patch('fitbod_export.ROOT', root), patch.dict('os.environ', {'FITBOD_EMAIL': 'override@example.com'}, clear=True):
                self.assertEqual(local_credentials()['FITBOD_EMAIL'], 'override@example.com')

    def test_short_pages_do_not_truncate_history(self):
        pages = [{'data': [{'id': '1'}]}, {'data': [{'id': '2'}]}, {'data': []}]
        saved = []
        rows = fetch_pages(lambda n: pages[n - 1], lambda n, body: saved.append(n))
        self.assertEqual([row['id'] for row in rows], ['1', '2'])
        self.assertEqual(saved, [1, 2, 3])

    def test_repeating_page_fails_instead_of_claiming_complete(self):
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            fetch_pages(lambda n: {'data': [{'id': '1'}]}, lambda *args: None)

    def test_overlapping_pages_are_deduplicated_by_workout_id(self):
        pages = [{'data': [{'id': '1'}, {'id': '2'}]},
                 {'data': [{'id': '2'}, {'id': '3'}]}, {'data': []}]
        rows = fetch_pages(lambda n: pages[n - 1], lambda *args: None)
        self.assertEqual([row['id'] for row in rows], ['1', '2', '3'])

    def test_circuits_warmups_and_zero_weight_are_preserved(self):
        ex = {'exercise_external_resource_id': 218, 'list_position': 0,
              'set_breakdown': {'individual_sets': [
                  {'weight': 0, 'reps': 10, 'isWarmup': True, 'restTime': 60}]}}
        workouts = [{'id': '1', 'attributes': {'date_performed': '2026-10-29',
                     'exercise_sets': [ex], 'circuits': [{'exercise_sets': [ex]}]}},
                    {'id': '2', 'attributes': {'deleted': True, 'exercise_sets': [ex]}}]
        rows = list(flatten(workouts, {'218': 'Leg Press'}))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['weight_raw'], 0)
        self.assertEqual(rows[0]['exercise_name'], 'Leg Press')
        self.assertTrue(rows[0]['is_warmup'])
        self.assertEqual(rows[0]['rest_time_raw'], 60)
        self.assertIsNone(rows[0]['circuit_position'])
        self.assertEqual(rows[1]['circuit_position'], 0)

    def test_spreadsheet_formulas_are_escaped_but_numbers_preserved(self):
        self.assertEqual(csv_cell('=HYPERLINK("example")'), '\'=HYPERLINK("example")')
        self.assertEqual(csv_cell(-10), -10)
        self.assertEqual(csv_cell('Bench press'), 'Bench press')


if __name__ == '__main__':
    unittest.main()
