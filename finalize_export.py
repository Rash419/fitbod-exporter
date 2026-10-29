"""Make readable exports from fetched pages; all operations are local."""
import argparse
import csv
from datetime import datetime
import json
import os
from pathlib import Path
import zipfile
from zoneinfo import ZoneInfo

from fitbod_export import PRIVATE, flatten, running_only, csv_cell, save_json

SET_FIELDS = ['date_local', 'date_utc', 'workout_name', 'exercise_name', 'set_number',
              'reps', 'weight_kg', 'is_warmup', 'is_amrap', 'rest_seconds',
              'set_duration_raw', 'distance_m', 'notes', 'workout_id',
              'exercise_external_resource_id', 'exercise_position', 'circuit_position', 'set_id']


def local_date(value, timezone):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone).isoformat() if value else ''


def write_csv(path, fields, rows):
    with path.open('w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: csv_cell(v) for k, v in row.items()})


def set_rows(workouts, names, timezone):
    for r in flatten(workouts, names):
        if not r['exercise_name']:
            raise RuntimeError('Unmapped exercise ID: ' + str(r['exercise_external_resource_id']))
        yield dict(zip(SET_FIELDS, [
            local_date(r['date'], timezone), r['date'], r['workout_name'], r['exercise_name'],
            r['set_number'], r['reps'], r['weight_raw'], bool(r['is_warmup']),
            bool(r['is_amrap']), r['rest_time_raw'], r['duration_raw'], r['distance_m'],
            r['notes'], r['workout_id'], r['exercise_external_resource_id'],
            r['exercise_position'], r['circuit_position'], r['set_id']]))


def finalize(source, catalogue, timezone_name='UTC'):
    timezone = ZoneInfo(timezone_name)
    if not json.loads((source / 'status.json').read_text()).get('complete'):
        raise RuntimeError('Source fetch did not complete')
    unique = {}
    for page in sorted(source.glob('page-*.json')):
        for row in json.loads(page.read_text())['data']:
            unique.setdefault(str(row['id']), row)
    expected = json.loads((source / 'status.json').read_text())['workouts']
    if len(unique) != expected:
        raise RuntimeError('Record count differs from source completion status')
    names = json.loads((PRIVATE / 'exercise-names.json').read_text())
    customs = json.loads(catalogue.read_text())['data']
    for item in customs:
        a = item['attributes']
        names[str(a['external_resource_id'])] = a['name']
    save_json(PRIVATE / 'exercise-names.json', names)
    active = [w for w in unique.values() if not w['attributes'].get('deleted')]
    retained = [w for w in active if not running_only(w, names)]
    strength = [w for w in retained if w['attributes'].get('source_id') == 0]
    rows = list(set_rows(retained, names, timezone))
    strength_rows = list(set_rows(strength, names, timezone))
    out = PRIVATE / 'workout-export'
    out.mkdir(exist_ok=True, mode=0o700)
    write_csv(out / 'fitbod-nonrunning-sets.csv', SET_FIELDS, rows)
    write_csv(out / 'fitbod-strength-sets.csv', SET_FIELDS, strength_rows)
    fields = ['workout_id', 'date_local', 'date_utc', 'workout_name',
              'workout_duration_seconds', 'calories', 'source_id', 'set_count']
    workout_rows = []
    for w in retained:
        a = w['attributes']
        workout_rows.append(dict(zip(fields, [w['id'], local_date(a.get('date_performed'), timezone),
            a.get('date_performed'), a.get('workout_name'), a.get('workout_duration'),
            a.get('calories_burned'), a.get('source_id'), len(list(flatten([w], names)))])))
    write_csv(out / 'fitbod-workouts.csv', fields, workout_rows)
    save_json(out / 'fitbod-workouts.json', {'data': retained})
    dates = sorted(r['date_local'] for r in rows if r['date_local'])
    strength_dates = sorted(r['date_local'] for r in strength_rows if r['date_local'])
    summary = {'raw_api_records': len(unique), 'nonrunning_workouts': len(retained),
               'nonrunning_sets': len(rows), 'strength_workouts': len(strength),
               'strength_sets': len(strength_rows), 'running_workouts_excluded': len(active)-len(retained),
               'other_imported_nonrunning_workouts': len(retained)-len(strength),
               'custom_exercises_resolved': len(customs), 'unmapped_exercise_names': 0,
               'date_range_local': [dates[0], dates[-1]] if dates else [],
               'strength_date_range_local': [strength_dates[0], strength_dates[-1]] if strength_dates else [],
               'timezone': timezone_name, 'source': str(source),
               'notes': 'CSV omits deleted sets/exercises. Raw workout JSON retains original deletion markers. API pagination ended with an empty page; backend record counts may differ from the app counter.'}
    save_json(out / 'export-summary.json', summary)
    text = (
        'Fitbod workout export\n\n'
        'fitbod-nonrunning-sets.csv: all non-running sessions, including imported walking/cycling.\n'
        'fitbod-strength-sets.csv: Fitbod-authored sessions (source_id 0), excluding running-only sessions.\n'
        'fitbod-workouts.csv: one row per non-running workout.\n'
        'fitbod-workouts.json: original non-running workout records; includes deleted-set markers.\n\n'
        'CSV excludes deleted exercises, circuits, and sets marked deletedAt. Warmup sets are retained.\n'
        f'date_local uses {timezone_name}; date_utc preserves the API timestamp. Weight is kg, confirmed\n'
        'against sampled app sets; workout durations and rest times are seconds. set_duration_raw\n'
        'preserves the source value. Notes and names beginning with spreadsheet formula characters\n'
        'are escaped in CSV; JSON preserves originals. Custom exercise names are resolved from the supplied catalogue.\n'
        'The raw API pages, including excluded runs, remain in the separate private source directory.\n'
        'No passwords, HAR recordings, or session tokens are included in this export bundle.\n')
    (out / 'README.txt').write_text(text)
    bundle = PRIVATE / 'fitbod-workout-export.zip'
    with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in ['fitbod-nonrunning-sets.csv', 'fitbod-strength-sets.csv',
                     'fitbod-workouts.csv', 'fitbod-workouts.json', 'export-summary.json', 'README.txt']:
            z.write(out / name, arcname=name)
    print(json.dumps(summary, indent=2))
    print('Export bundle:', bundle)


if __name__ == '__main__':
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('--catalogue', required=True, type=Path)
    p.add_argument('--timezone', default=os.environ.get('FITBOD_TIMEZONE', 'UTC'))
    args = p.parse_args()
    finalize(args.source, args.catalogue, args.timezone)
