"""Recover a Fitbod session from a locally saved browser HAR, without logging it."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from fitbod_export import PRIVATE, TOKEN, BASE, request

JWT = re.compile(r'(?<![A-Za-z0-9_-])([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)(?![A-Za-z0-9_-])')


def candidates(har):
    found = {}

    def scan(value, priority):
        if not isinstance(value, str):
            return
        for match in JWT.finditer(value):
            token = match.group(1)
            try:
                payload = token.split('.')[1]
                claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
                expiry = claims.get('exp')
                if not isinstance(expiry, (int, float)) or expiry <= time.time():
                    continue
            except (ValueError, TypeError, UnicodeError):
                continue
            rank = (priority, expiry)
            if token not in found or rank > found[token]:
                found[token] = rank

    def body_tokens(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if str(key).lower() in {'refresh_token', 'refreshtoken', 'authorization', 'auth_token', 'token'}:
                    scan(value, 4 if 'refresh' in str(key).lower() else 2)
                body_tokens(value)
        elif isinstance(obj, list):
            for item in obj:
                body_tokens(item)

    for entry in har.get('log', {}).get('entries', []):
        req = entry.get('request', {})
        url = urlsplit(req.get('url', ''))
        if url.scheme != 'https' or not (url.hostname == 'fitbod.me' or (url.hostname or '').endswith('.fitbod.me')):
            continue
        for part in ('response', 'request'):
            for header in entry.get(part, {}).get('headers', []):
                name = header.get('name', '').lower()
                if name in {'authorization', 'set-cookie', 'cookie'}:
                    priority = 5 if part == 'response' and url.hostname == 'gate-keeper.fitbod.me' and name == 'authorization' else 1
                    scan(header.get('value', ''), priority)
            for cookie in entry.get(part, {}).get('cookies', []):
                scan(cookie.get('value', ''), 1)
        content = entry.get('response', {}).get('content', {})
        text = content.get('text', '')
        try:
            if content.get('encoding') == 'base64':
                text = base64.b64decode(text).decode('utf-8')
            body_tokens(json.loads(text))
        except (ValueError, TypeError, UnicodeError):
            pass
    return sorted(found, key=lambda t: found[t], reverse=True)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('har', type=Path)
    parser.add_argument('--capture-only', action='store_true',
                        help='Save a captured refresh cookie for validation on Android, without desktop API requests')
    args = parser.parse_args()
    har = json.loads(args.har.read_text(encoding='utf-8-sig'))
    tokens = candidates(har)
    if not tokens:
        raise RuntimeError('No usable JWTs found. Capture login again with sensitive authentication headers included.')
    print(f'Found {len(tokens)} distinct, unexpired Fitbod session candidates. Tokens will not be displayed.')
    if args.capture_only:
        refresh = set()
        for entry in har.get('log', {}).get('entries', []):
            url = urlsplit(entry.get('request', {}).get('url', ''))
            response = entry.get('response', {})
            if url.scheme == 'https' and url.hostname == 'app.fitbod.me' and url.path == '/api/auth/login' and response.get('status') == 200:
                refresh.update(c['value'] for c in response.get('cookies', [])
                               if c.get('name') == 'pegasus-refresh-token' and c.get('value') in tokens)
        if len(refresh) != 1:
            raise RuntimeError('Expected one refresh cookie from a successful official web login; none or multiple found')
        PRIVATE.mkdir(exist_ok=True, mode=0o700)
        TOKEN.write_text(refresh.pop(), encoding='utf-8')
        TOKEN.chmod(0o600)
        print('Captured session saved locally. API validation will occur on Android.')
        return
    # Validate against the documented token exchange. Only Fitbod receives tokens.
    for index, token in enumerate(tokens[:5], 1):
        try:
            _, body = request(BASE + '/access_token', {'refresh_token': token})
        except RuntimeError as exc:
            if 'HTTP 401' in str(exc) or 'HTTP 422' in str(exc):
                continue
            raise
        if isinstance(body, dict) and body.get('access_token'):
            PRIVATE.mkdir(exist_ok=True, mode=0o700)
            TOKEN.write_text(token, encoding='utf-8')
            TOKEN.chmod(0o600)
            print('Validated a Fitbod session and saved it locally. Ready for bulk export.')
            return
        time.sleep(1)
    raise RuntimeError('Captured tokens were not accepted as workout API refresh tokens; further login-flow analysis is needed.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        raise SystemExit(str(exc))
