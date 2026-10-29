#!/usr/bin/env python3
"""Personal, read-only Fitbod history export. Python standard library only.

Private API contract: https://github.com/Muno459/mcp-fitbod
No workout/profile writes; POST is used only for authentication.
"""
import argparse
import csv
import getpass
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / 'private'
TOKEN = PRIVATE / 'refresh-token.txt'
BASE = 'https://nautilus.fitbod.me'
WORKOUTS = '/api/v3/workout_data'
JSONAPI = 'application/vnd.api+json'


class FitbodHTTPError(RuntimeError):
    def __init__(self, status, message, diagnostic=None):
        super().__init__(message)
        self.status = status
        self.diagnostic = diagnostic


def record_http_error(url, exc):
    """Keep error evidence local; never print response bodies or auth headers."""
    raw = exc.read(65536).decode('utf-8', errors='replace')
    low = raw.lower()
    html = 'html' in exc.headers.get('Content-Type', '').lower()
    cloudflare_html = html and any(marker in low for marker in (
        'cloudflare', 'you have been blocked', 'just a moment', 'error code: 1020'))
    classification = 'Cloudflare HTML block/challenge' if cloudflare_html else 'API/HTTP rejection'
    dest = PRIVATE / 'http-errors'
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = dest / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '.json')
    report = {'url': url, 'status': exc.code, 'classification': classification,
              'headers': {key: exc.headers.get(key) for key in (
                  'Server', 'Content-Type', 'CF-Ray', 'CF-Mitigated') if exc.headers.get(key)},
              'body': raw}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    return classification, path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, payload=None, token=None):
    """Do not follow redirects or print server bodies containing account data."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc not in {
        'gate-keeper.fitbod.me', 'nautilus.fitbod.me'
    }:
        raise RuntimeError('Unexpected API destination')
    # Match the headers used by the reviewed reference API client.
    headers = {'Accept': JSONAPI if token else 'application/json', 'Content-Type': 'application/json',
               'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                             'AppleWebKit/537.36 (KHTML, like Gecko) '
                             'Chrome/140.0.0.0 Safari/537.36'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as res:
            return res.headers, json.load(res)
    except urllib.error.HTTPError as exc:
        classification, diagnostic = record_http_error(url, exc)
        raise FitbodHTTPError(exc.code,
                             f'HTTP {exc.code} at {parsed.hostname}{parsed.path}: '
                             f'{classification}. Diagnostic saved to {diagnostic}',
                             diagnostic) from None
    except (urllib.error.URLError, ValueError):
        raise RuntimeError('Network failure or unexpected API response; stopped') from None


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def local_credentials():
    """Read only the two credential keys; never execute .env as shell code."""
    values = {}
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            match = re.match(r'^\s*(?:export\s+)?(FITBOD_EMAIL|FITBOD_PASSWORD)\s*=\s*(.*)$', line)
            if not match:
                continue
            key, value = match.groups()
            value = value.strip()
            if value.startswith(('"', "'")):
                quote = value[0]
                end = value.rfind(quote)
                tail = value[end + 1:].strip()
                if end == 0 or (tail and not tail.startswith('#')):
                    raise RuntimeError('Malformed quoted credential in .env')
                value = value[1:end]
            else:
                value = re.split(r'\s+#', value, maxsplit=1)[0].rstrip()
            values[key] = value
    for key in ('FITBOD_EMAIL', 'FITBOD_PASSWORD'):
        if key in os.environ:
            values[key] = os.environ[key]
    return values


def login():
    credentials = local_credentials()
    email = credentials.get('FITBOD_EMAIL') or input('Fitbod email: ').strip()
    password = credentials.get('FITBOD_PASSWORD') or getpass.getpass('Fitbod password (hidden): ')
    if not email or not password:
        raise RuntimeError('Email and password are required')
    headers, _ = request('https://gate-keeper.fitbod.me/users/login',
                         {'user': {'email': email, 'password': password}})
    password = None
    token = headers.get('Authorization', '').strip()
    if token.lower().startswith('bearer '):
        token = token.split(None, 1)[1]
    if not token:
        raise RuntimeError('Login returned no session token')
    TOKEN.write_text(token, encoding='utf-8')
    TOKEN.chmod(0o600)
    print('Session saved locally. Password was not saved.')


def fetch_pages(get, save):
    """Continue until an empty page; fail explicitly if pagination repeats."""
    seen = set()
    rows = []
    for page in range(1, 10001):
        body = get(page)
        if not isinstance(body, dict) or not isinstance(body.get('data'), list):
            raise RuntimeError('Unexpected history schema; raw pages retained')
        save(page, body)
        batch = body['data']
        if not batch:
            return rows
        for row in batch:
            if not isinstance(row, dict) or row.get('id') is None:
                raise RuntimeError('History record missing an ID; stopped')
        ids = {str(row['id']) for row in batch}
        if not ids - seen:
            raise RuntimeError('Pagination repeated; export is incomplete')
        for row in batch:
            ident = str(row['id'])
            if ident not in seen:
                rows.append(row)
                seen.add(ident)
    raise RuntimeError('Pagination safety limit reached; export is incomplete')


FIELDS = ['workout_id', 'date', 'workout_name', 'exercise_external_resource_id', 'exercise_name',
          'exercise_position', 'circuit_position', 'set_number', 'set_id',
          'weight_raw', 'reps', 'is_warmup', 'is_amrap', 'rest_time_raw',
          'duration_raw', 'distance_m', 'notes']


def flatten(workouts, names=None):
    """Preserve set values as received, including warmups and circuit sets."""
    names = names or {}
    for workout in workouts:
        attrs = workout.get('attributes') or {}
        if attrs.get('deleted'):
            continue
        groups = [(None, attrs.get('exercise_sets') or [])]
        groups += [(i, circuit.get('exercise_sets') or [])
                   for i, circuit in enumerate(attrs.get('circuits') or []) if not circuit.get('deleted')]
        for circuit_pos, exercises in groups:
            for ex in exercises:
                if ex.get('deleted'):
                    continue
                sets = (ex.get('set_breakdown') or {}).get('individual_sets') or []
                for i, item in enumerate(sets, 1):
                    if item.get('deletedAt'):
                        continue
                    yield dict(zip(FIELDS, [
                        workout['id'], attrs.get('date_performed'), attrs.get('workout_name'),
                        ex.get('exercise_external_resource_id'),
                        names.get(str(ex.get('exercise_external_resource_id')), ''),
                        ex.get('list_position'),
                        circuit_pos, i, item.get('_id'), item.get('weight'), item.get('reps'),
                        item.get('isWarmup'), item.get('is_amrap'), item.get('restTime'),
                        item.get('duration'), item.get('distance_m'), ex.get('notes')]))


def csv_cell(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    # Prevent workout names/notes from being evaluated as spreadsheet formulas.
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def running_only(workout, names):
    attrs = workout.get('attributes') or {}
    exercises = list(attrs.get('exercise_sets') or [])
    for circuit in attrs.get('circuits') or []:
        exercises.extend(circuit.get('exercise_sets') or [])
    return bool(exercises) and all(
        names.get(str(ex.get('exercise_external_resource_id')), '').strip().lower() == 'running'
        for ex in exercises)


def history_url(page):
    # Exact first-page query declared in the installed 8.28 APK. Do not add
    # an undocumented server-side deleted filter; filter deleted rows locally.
    query = {'page[size]': 1000, 'sort': '-date_performed'}
    if page > 1:
        query['page[number]'] = page
    return BASE + WORKOUTS + '?' + urllib.parse.urlencode(query, safe='[]')


def export():
    if not TOKEN.exists():
        raise RuntimeError('No local session. Run with --login or --import-token first')
    token = TOKEN.read_text(encoding='utf-8').strip()
    _, auth = request(BASE + '/access_token', {'refresh_token': token})
    access = auth.get('access_token')
    if not access:
        raise RuntimeError('Could not obtain an access token')
    print('Fitbod accepted the saved session and issued an access token.', flush=True)
    save_json(PRIVATE / 'session-status.json', {'source': 'saved refresh token', 'api_validated': True})
    name = datetime.now(timezone.utc).strftime('export-%Y%m%dT%H%M%S.%fZ')
    dest = PRIVATE / name
    dest.mkdir(mode=0o700)
    save_json(dest / 'status.json', {'complete': False})

    def get(page):
        if page > 1:
            time.sleep(1)
        return request(history_url(page), token=access)[1]

    def save(page, body):
        save_json(dest / f'page-{page:05d}.json', body)
        print(f'Saved history page {page}: {len(body["data"])} workouts', flush=True)

    try:
        fetched = fetch_pages(get, save)
    except FitbodHTTPError as exc:
        if exc.status == 403:
            # One independent read of a documented public catalogue endpoint
            # helps distinguish a route-specific rejection from broad blocking.
            time.sleep(1)
            try:
                _, control = request(BASE + '/api/v3/muscle_groups', token=access)
                result = {'catalogue_read_succeeded': isinstance(control, dict) and 'data' in control}
            except FitbodHTTPError as control_error:
                result = {'catalogue_read_succeeded': False, 'http_status': control_error.status,
                          'diagnostic': str(control_error.diagnostic)}
            except RuntimeError:
                result = {'catalogue_read_succeeded': False, 'network_error': True}
            save_json(dest / 'access-check.json', result)
            print(f'Connectivity check saved to {dest / "access-check.json"}', flush=True)
        raise
    names_file = PRIVATE / 'exercise-names.json'
    names = json.loads(names_file.read_text(encoding='utf-8')) if names_file.exists() else {}
    active = [w for w in fetched if not (w.get('attributes') or {}).get('deleted')]
    workouts = [w for w in active if not running_only(w, names)]
    save_json(dest / 'workouts.json', {'data': workouts})
    count = 0
    with (dest / 'sets.csv').open('w', newline='', encoding='utf-8') as out:
        writer = csv.DictWriter(out, fieldnames=FIELDS)
        writer.writeheader()
        for row in flatten(workouts, names):
            writer.writerow({key: csv_cell(value) for key, value in row.items()})
            count += 1
    save_json(dest / 'status.json', {'complete': True, 'workouts': len(workouts),
                                   'sets': count,
                                   'running_workouts_excluded': len(active) - len(workouts),
                                   'deleted_workouts_excluded': len(fetched) - len(active),
                                   'notes': 'Units preserved as received. Names mapped from the installed APK when available; unmatched IDs retained. Completeness means API pagination reached an empty page, not an independent account audit.'})
    print(f'Exported {len(workouts)} workouts and {count} sets to {dest}')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--login', action='store_true')
    action.add_argument('--import-token', action='store_true')
    args = parser.parse_args()
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    if args.login:
        login()
    elif args.import_token:
        token = getpass.getpass('Existing Fitbod refresh token (hidden): ').strip()
        if token.lower().startswith('bearer '):
            token = token.split(None, 1)[1]
        if not token or '\n' in token or '\r' in token:
            raise RuntimeError('Invalid session token')
        TOKEN.write_text(token, encoding='utf-8')
        TOKEN.chmod(0o600)
        print('Session saved locally.')
    else:
        export()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError) as exc:
        print(f'Export stopped: {exc}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('\nCancelled.', file=sys.stderr)
        sys.exit(130)
