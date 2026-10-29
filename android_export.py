"""Bulk Fitbod export through the authorized phone's native HTTPS implementation."""
import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from fitbod_export import ROOT, PRIVATE, TOKEN, FIELDS, flatten, csv_cell, running_only, save_json

SDK = Path(os.environ.get('ANDROID_SDK_ROOT') or os.environ.get('ANDROID_HOME')
           or Path.home() / 'Android' / 'Sdk')
JDK = (Path(os.environ['JAVA_HOME']) / 'bin' if os.environ.get('JAVA_HOME')
       else Path(shutil.which('javac') or '/opt/android-studio/jbr/bin/javac').parent)
ADB = str(SDK / 'platform-tools/adb') if (SDK / 'platform-tools/adb').exists() else (shutil.which('adb') or 'adb')
BUILD = PRIVATE / 'native-build'
SERIAL = os.environ.get('ANDROID_SERIAL', '')


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, timeout=300, **kwargs)
    if result.returncode:
        # Build/ADB commands contain no credentials. Do not print stdin.
        raise RuntimeError(result.stderr.decode(errors='replace')[:2000] or 'Command failed')
    return result.stdout


def select_device(adb=ADB, serial=SERIAL):
    if serial:
        return serial
    lines = run([adb, 'devices']).decode().splitlines()
    devices = [line.split()[0] for line in lines if len(line.split()) == 2 and line.split()[1] == 'device']
    if len(devices) != 1:
        raise RuntimeError('Connect and authorize one Android device, or select one with --serial / ANDROID_SERIAL')
    return devices[0]


def newest_sdk_file(pattern):
    candidates = list(SDK.glob(pattern))
    if not candidates:
        raise RuntimeError(f'Android SDK component missing: {pattern}. Set ANDROID_SDK_ROOT or --sdk-root.')
    return max(candidates, key=lambda p: tuple(map(int, re.findall(r'\d+', str(p.relative_to(SDK))))))


def build():
    classes = BUILD / 'classes'
    classes.mkdir(parents=True, exist_ok=True, mode=0o700)
    android = newest_sdk_file('platforms/*/android.jar')
    run([str(JDK / 'javac'), '--release', '8', '-classpath', str(android),
         '-d', str(classes), str(ROOT / 'android/FitbodNativeExport.java')])
    jar = BUILD / 'native-export.jar'
    run([str(JDK / 'java'), '-cp', str(newest_sdk_file('build-tools/*/lib/d8.jar')),
         'com.android.tools.r8.D8', '--min-api', '26', '--lib', str(android),
         '--output', str(jar), *map(str, classes.glob('*.class'))])
    print('Android networking helper compiled locally; no app installation needed.', flush=True)
    return jar


def convert(dest):
    status = json.loads((dest / 'status.json').read_text())
    if not status.get('complete'):
        raise RuntimeError(f'Android fetch incomplete. Diagnostics are saved in {dest}')
    unique = {}
    for path in sorted(dest.glob('page-*.json')):
        for row in json.loads(path.read_text())['data']:
            unique.setdefault(str(row['id']), row)
    if len(unique) != status['workouts']:
        raise RuntimeError('Transferred record count does not match Android fetch count')
    names = json.loads((PRIVATE / 'exercise-names.json').read_text())
    active = [w for w in unique.values() if not (w.get('attributes') or {}).get('deleted')]
    workouts = [w for w in active if not running_only(w, names)]
    save_json(dest / 'workouts.json', {'data': workouts})
    count = 0
    with (dest / 'sets.csv').open('w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for row in flatten(workouts, names):
            writer.writerow({k: csv_cell(v) for k, v in row.items()})
            count += 1
    save_json(dest / 'export-summary.json', {'workouts': len(workouts), 'sets': count,
              'running_workouts_excluded': len(active) - len(workouts),
              'deleted_workouts_excluded': len(unique) - len(active),
              'source': 'Android native HTTPS bulk API fetch',
              'notes': 'Weight/duration fields preserve API values until units are verified against the app.'})
    print(f'Saved {len(workouts)} non-running workouts and {count} sets to {dest}', flush=True)


def main():
    global SDK, JDK, ADB, SERIAL
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-only', action='store_true')
    parser.add_argument('--catalogue-only', action='store_true')
    parser.add_argument('--sdk-root', type=Path, default=SDK)
    parser.add_argument('--jdk-bin', type=Path, default=JDK)
    parser.add_argument('--serial', default=SERIAL)
    args = parser.parse_args()
    SDK, JDK = args.sdk_root, args.jdk_bin
    ADB = str(SDK / 'platform-tools/adb') if (SDK / 'platform-tools/adb').exists() else (shutil.which('adb') or 'adb')
    jar = build()
    if args.build_only:
        return
    SERIAL = select_device(ADB, args.serial)
    refresh = TOKEN.read_bytes().strip()
    if b'\n' in refresh or b'\r' in refresh:
        raise RuntimeError('Malformed saved session')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    remote = '/data/local/tmp/fitbod-native-' + stamp
    adb = [ADB, '-s', SERIAL]
    run(adb + ['shell', 'mkdir', '-m', '700', remote])
    run(adb + ['push', str(jar), remote + '/helper.jar'])
    # Token goes to the Java process via stdin, never shell arguments or a file.
    command = f'umask 077; CLASSPATH={remote}/helper.jar app_process /system/bin FitbodNativeExport {remote}'
    if args.catalogue_only:
        command += ' catalogue'
    result = subprocess.run(adb + ['shell', '-T', command], input=refresh + b'\n',
                            capture_output=True, timeout=300)
    refresh = None
    print(result.stdout.decode(errors='replace'), end='', flush=True)
    print(result.stderr.decode(errors='replace'), end='', file=sys.stderr, flush=True)
    dest = PRIVATE / (('android-catalogue-' if args.catalogue_only else 'android-export-') + stamp)
    dest.mkdir(mode=0o700)
    run(adb + ['pull', remote + '/results/.', str(dest)])
    # Remove only this run's temporary helper and copied result files after pull.
    run(adb + ['shell', 'rm', '-r', remote])
    if result.returncode:
        raise RuntimeError(f'Android networking test failed. Diagnostics saved to {dest}')
    if args.catalogue_only:
        print(f'Custom exercise catalogue saved to {dest}', flush=True)
        return
    convert(dest)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc))
