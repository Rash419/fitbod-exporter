"""Build a local exercise-name map from your own installed Fitbod APK."""
import argparse
import json
import os
from pathlib import Path
import zipfile

from android_export import ADB, SERIAL, run, select_device
from fitbod_export import PRIVATE, save_json


def extract(apk):
    with zipfile.ZipFile(apk) as archive:
        doc = json.loads(archive.read('res/raw/exercises_production.json'))
    rows = doc['data'] if isinstance(doc, dict) else doc
    return {str(a['external_resource_id']): a['name']
            for row in rows for a in [row.get('attributes', {})]
            if a.get('external_resource_id') is not None and a.get('name')}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apk', type=Path, help='Use an existing APK instead of pulling from the phone')
    parser.add_argument('--adb', default=ADB)
    parser.add_argument('--serial', default=SERIAL)
    args = parser.parse_args()
    PRIVATE.mkdir(exist_ok=True, mode=0o700)
    apk = args.apk
    if apk is None:
        adb = [args.adb, '-s', select_device(args.adb, args.serial)]
        paths = run(adb + ['shell', 'pm', 'path', 'com.fitbod.fitbod']).decode().splitlines()
        remote = next((line.removeprefix('package:') for line in paths
                       if line.startswith('package:/') and line.endswith('/base.apk')), None)
        if not remote:
            raise RuntimeError('Installed Fitbod base.apk not found; provide --apk')
        apk = PRIVATE / 'fitbod.apk'
        run(adb + ['pull', remote, str(apk)])
    names = extract(apk)
    save_json(PRIVATE / 'exercise-names.json', names)
    print(f'Extracted {len(names)} exercise names locally.')


if __name__ == '__main__':
    main()
