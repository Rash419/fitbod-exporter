import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from android_export import convert


class AndroidTransferTests(unittest.TestCase):
    def test_transfer_filters_runs_and_preserves_strength_sets(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'exercise-names.json').write_text(json.dumps({'1': 'Running', '2': 'Bench Press'}))
            dest = root / 'export'
            dest.mkdir()
            strength = {'id': 'strength', 'attributes': {'exercise_sets': [
                {'exercise_external_resource_id': 2, 'set_breakdown': {
                    'individual_sets': [{'weight': 50, 'reps': 8, 'isWarmup': False}]}}]}}
            run = {'id': 'run', 'attributes': {'exercise_sets': [{'exercise_external_resource_id': 1}]}}
            (dest / 'status.json').write_text(json.dumps({'complete': True, 'workouts': 2}))
            (dest / 'page-00001.json').write_text(json.dumps({'data': [run, strength]}))
            (dest / 'page-00002.json').write_text(json.dumps({'data': []}))
            with patch('android_export.PRIVATE', root), contextlib.redirect_stdout(io.StringIO()):
                convert(dest)
            summary = json.loads((dest / 'export-summary.json').read_text())
            self.assertEqual(summary['workouts'], 1)
            self.assertEqual(summary['sets'], 1)
            self.assertEqual(summary['running_workouts_excluded'], 1)
            self.assertIn('Bench Press', (dest / 'sets.csv').read_text())

    def test_incomplete_transfer_cannot_be_reported_as_an_export(self):
        with TemporaryDirectory() as temp:
            dest = Path(temp)
            (dest / 'status.json').write_text('{"complete": false}')
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                convert(dest)
            self.assertFalse((dest / 'sets.csv').exists())


if __name__ == '__main__':
    unittest.main()
