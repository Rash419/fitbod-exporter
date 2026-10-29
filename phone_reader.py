"""Read Fitbod's visible Android accessibility tree over an authorized USB link."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from android_export import ADB, SERIAL, select_device

OUT = Path(__file__).resolve().parent / 'private' / 'phone'


def adb(*args):
    result = subprocess.run([ADB, '-s', select_device(ADB, SERIAL), *args], capture_output=True, timeout=25)
    if result.returncode:
        raise RuntimeError('Android connection failed: ' + result.stderr.decode(errors='replace')[:200])
    return result.stdout


def rid(node):
    return node.get('resource-id', '').split('/')[-1]


def bounds(node):
    return tuple(map(int, re.findall(r'\d+', node.get('bounds', ''))))


def snapshot():
    adb('shell', 'uiautomator', 'dump', '/data/local/tmp/fitbod-window.xml')
    raw = adb('exec-out', 'cat', '/data/local/tmp/fitbod-window.xml')
    root = ET.fromstring(raw)
    if not any(n.get('package') == 'com.fitbod.fitbod' for n in root.iter('node')):
        raise RuntimeError('Fitbod is not visible; stopped')
    OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
    (OUT / f'screen-{time.time_ns()}.xml').write_bytes(raw)
    return root


def describe(root):
    return [{'id': rid(n), 'text': n.get('text') or n.get('content-desc'),
             'bounds': n.get('bounds'), 'clickable': n.get('clickable') == 'true'}
            for n in root.iter('node') if n.get('package') == 'com.fitbod.fitbod'
            and (n.get('text') or n.get('content-desc'))]


def cards(root):
    rv = next((n for n in root.iter('node') if rid(n) == 'log_recycler_view'), None)
    if rv is None:
        raise RuntimeError('Past-workout list is not visible; stopped')
    result, current = [], None
    for child in rv:
        values = {rid(n): n.get('text') for n in child.iter('node') if n.get('text')}
        if 'date' in values:
            current = {'header_bounds': bounds(child)}
            result.append(current)
        if current is not None:
            current.update(values)
    return result


def running(card):
    return card.get('num_exercises') == '1' and card.get('exercises_list', '').strip().lower() == 'running'


def find_strength(limit):
    seen, previous = {}, None
    for page in range(limit):
        root = snapshot()
        rows = cards(root)
        for card in rows:
            if not card.get('exercises_list'):
                continue
            key = json.dumps([card.get(k) for k in ('date', 'duration', 'muscle_groups_text', 'exercises_list')])
            seen[key] = card
            x1, y1, x2, y2 = card['header_bounds']
            if not running(card) and 400 < (y1 + y2) // 2 < 1900:
                (OUT / 'observed-list.json').write_text(json.dumps(list(seen.values()), indent=2))
                print('Found non-running workout:', card.get('date'), card.get('muscle_groups_text'), flush=True)
                adb('shell', 'input', 'tap', str((x1+x2)//2), str((y1+y2)//2))
                print(json.dumps(describe(snapshot()), indent=1), flush=True)
                return
        signature = [(r.get('date'), r.get('duration'), r.get('exercises_list')) for r in rows]
        if signature == previous:
            raise RuntimeError('List did not advance; stopped')
        previous = signature
        (OUT / 'observed-list.json').write_text(json.dumps(list(seen.values()), indent=2))
        print(f'Scanned screen {page+1}: {len(seen)} workout summaries; running entries skipped', flush=True)
        adb('shell', 'input', 'swipe', '540', '1800', '540', '650', '450')
    print('Scan limit reached; saved observed summaries. No strength workout opened.', flush=True)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--find-strength', action='store_true')
    parser.add_argument('--max-screens', type=int, default=50)
    args = parser.parse_args()
    if args.find_strength:
        find_strength(args.max_screens)
    else:
        print(json.dumps(describe(snapshot()), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
