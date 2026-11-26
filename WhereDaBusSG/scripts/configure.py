"""Local configuration helper, run in Docker's Python image by the CMD launcher.

No network calls, database access, shell commands, or credentials in arguments.
The launcher gets only a validated numeric port and a key-present flag.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
KEY_PATTERN = re.compile(r'[A-Za-z0-9_./+=:-]{1,512}')
PASSWORD_PATTERN = re.compile(r'[A-Fa-f0-9]{32,128}')


def read_config(path):
    if not path.is_file():
        raise ValueError('No .env exists yet. Run Setup.cmd first.')
    values = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = re.fullmatch(r'\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*', line)
        if not match:
            raise ValueError('A .env line is not in NAME=value format. Check the file without sharing its contents.')
        name, value = match.groups()
        if name in values:
            raise ValueError(f'The .env entry {name} appears more than once. Keep only one entry.')
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[name] = value
    return values


def validate_config(values, analytics=False):
    for name in ('POSTGRES_PASSWORD', 'MONGO_PASSWORD'):
        if not PASSWORD_PATTERN.fullmatch(values.get(name, '')):
            raise ValueError(f'{name} must be the generated 32-128 character hexadecimal password. Restore the original .env if this database already exists.')
    port = values.get('APP_PORT', '')
    if not re.fullmatch(r'[0-9]{1,5}', port) or not 1024 <= int(port) <= 65535:
        raise ValueError('APP_PORT must be an unused port from 1024 to 65535. The default is 8081.')
    key = values.get('LTA_ACCOUNT_KEY', '')
    if key and not KEY_PATTERN.fullmatch(key):
        raise ValueError('LTA_ACCOUNT_KEY contains unsupported characters. Paste the AccountKey exactly, without spaces or quotes.')
    if analytics:
        if not key:
            raise ValueError('Analytics collection needs an LTA key. Run Setup.cmd first.')
        stops = {part.strip() for part in values.get('SAMPLE_STOPS', '').split(',')}
        if not 1 <= len(stops) <= 20 or any(not re.fullmatch(r'[0-9]{5}', code) for code in stops):
            raise ValueError('Set SAMPLE_STOPS in .env to 1-20 real five-digit stop codes separated by commas.')
        services = values.get('SAMPLE_SERVICES', '')
        if services and any(not re.fullmatch(r'[A-Za-z0-9]{1,12}', part.strip()) for part in services.split(',')):
            raise ValueError('SAMPLE_SERVICES must contain valid service numbers separated by commas, or be blank.')
    return values


def set_config_value(path, name, value):
    if '\n' in value or '\r' in value:
        raise ValueError('Configuration values cannot contain line breaks.')
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    expression = re.compile(r'\s*' + re.escape(name) + r'\s*=')
    found = False
    updated = []
    for line in lines:
        if expression.match(line):
            updated.append(f'{name}={value}')
            found = True
        else:
            updated.append(line)
    if not found:
        updated.append(f'{name}={value}')
    path.write_text('\n'.join(updated) + '\n', encoding='utf-8')


def setup_config(root, existing_volumes=False, prompt=getpass.getpass):
    path = root / '.env'
    if not path.exists():
        if existing_volumes:
            raise ValueError('Existing wheredabus-fresh volumes were found but .env is missing. Restore this installation\'s .env backup; new passwords cannot unlock existing databases.')
        template = root / '.env.example'
        if not template.is_file():
            raise ValueError('The package is missing .env.example. Extract the complete ZIP again.')
        # Prepare complete credentials before exclusive creation; never persist
        # template password placeholders or overwrite another setup's .env.
        content = template.read_text(encoding='utf-8-sig')
        for name in ('POSTGRES_PASSWORD', 'MONGO_PASSWORD'):
            value = secrets.token_hex(24)
            expression = re.compile(r'^\s*' + re.escape(name) + r'\s*=.*$', re.MULTILINE)
            if expression.search(content):
                content = expression.sub(f'{name}={value}', content)
            else:
                content = content.rstrip('\n') + f'\n{name}={value}\n'
        with path.open('x', encoding='utf-8') as stream:
            stream.write(content)
        print('Created .env with random database passwords. Keep this file with this installation and back it up privately.')
    values = validate_config(read_config(path))
    if not values.get('LTA_ACCOUNT_KEY'):
        print('Paste your LTA DataMall AccountKey below. It will not be displayed.')
        print('Press Enter to skip and explore the built-in demo first.')
        key = prompt('LTA AccountKey: ').strip()
    else:
        print('Your database passwords will be retained. A replacement LTA key will not be displayed.')
        key = prompt('New LTA AccountKey (Enter to keep current): ').strip()
    if key and not KEY_PATTERN.fullmatch(key):
        raise ValueError('The key contains unsupported characters. Run Setup.cmd again and paste the AccountKey without spaces or quotes.')
    if key:
        set_config_value(path, 'LTA_ACCOUNT_KEY', key)
    return validate_config(read_config(path))


def analytics_config(root, prompt=input):
    """Collect the user's stop cohort, then save both settings together.

    Credentials are never prompted for or rewritten here. Cancelling either
    question, or submitting an invalid answer, cannot partially update .env.
    """
    path = root / '.env'
    values = validate_config(read_config(path))
    if not values.get('LTA_ACCOUNT_KEY'):
        raise ValueError('Analytics collection needs an LTA key. Run Setup.cmd first.')
    print('Choose the stops you want to track. Find their five-digit codes in Nearby stops or Live arrivals.')
    print('Enter 1-20 stop codes, separated by commas. Collection starts when this launcher finishes.')
    current_stops = values.get('SAMPLE_STOPS', '')
    while True:
        question = 'Stop codes'
        if current_stops:
            question += f' (Enter to keep {current_stops})'
        answer = prompt(question + ': ').strip()
        stops = answer or current_stops
        stops = ','.join(dict.fromkeys(part.strip() for part in stops.split(',')))
        try:
            validate_config({**values, 'SAMPLE_STOPS': stops, 'SAMPLE_SERVICES': ''}, analytics=True)
        except ValueError as error:
            print(error)
        else:
            break
    current_services = values.get('SAMPLE_SERVICES', '')
    print('Optional: limit collection to particular bus services. Enter * to collect all services at your stops.')
    while True:
        question = ('Bus services (Enter to keep ' + current_services + ', * for all): '
                    if current_services else 'Bus services (Enter for all): ')
        answer = prompt(question).strip()
        services = '' if answer == '*' else (answer or current_services)
        services = ','.join(dict.fromkeys(part.strip() for part in services.split(',')))
        try:
            updated = validate_config({**values, 'SAMPLE_STOPS': stops,
                                       'SAMPLE_SERVICES': services}, analytics=True)
        except ValueError as error:
            print(error)
        else:
            break
    save_config_values(root, {'SAMPLE_STOPS': stops, 'SAMPLE_SERVICES': services})
    print(f'Saved stops: {stops}. Services: {services or "all"}.')
    return updated


def save_config_values(root, changes):
    """Replace the selected settings together, retaining unrelated entries."""
    path = root / '.env'
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    remaining = dict(changes)
    for index, line in enumerate(lines):
        match = re.match(r'\s*([A-Z][A-Z0-9_]*)\s*=', line)
        if match and match[1] in remaining:
            name = match[1]
            lines[index] = f'{name}={remaining.pop(name)}'
    lines.extend(f'{name}={value}' for name, value in remaining.items())
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=root,
                                         prefix='.env.analytics-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write('\n'.join(lines) + '\n')
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def analytics_auto_config(root):
    """Check automatic startup without reading any interactive input."""
    values = validate_config(read_config(root / '.env'))
    if not values.get('LTA_ACCOUNT_KEY'):
        raise ValueError('Analytics collection needs an LTA key. Run Setup.cmd first.')
    needs_stops = not values.get('SAMPLE_STOPS', '').strip()
    # A temporary syntactically valid code checks a retained service filter;
    # this placeholder is never persisted or sent to the provider.
    validate_config({**values, 'SAMPLE_STOPS': '00000'} if needs_stops else values, analytics=True)
    if needs_stops:
        print('Automatic sampling will select up to 10 imported stops. No stop codes need to be entered.')
    else:
        print('Keeping your existing sampling stops and service filter.')
    return values, needs_stops


def apply_auto_stops(root):
    """Persist only validated codes from the local, read-only SQL selection."""
    values, needs_stops = analytics_auto_config(root)
    if not needs_stops:
        return values
    selection_path = root / '.launcher-sampling.json'
    if not selection_path.is_file() or selection_path.stat().st_size > 65536:
        raise ValueError('Automatic stop selection did not return a valid result. Run Refresh-Data.cmd, then Start-Analytics.cmd.')
    try:
        selection = json.loads(selection_path.read_text(encoding='utf-8-sig'))
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError('Automatic stop selection returned unreadable data. Run Start-Analytics.cmd again.') from None
    entries = selection.get('stops') if isinstance(selection, dict) else None
    if not isinstance(entries, list) or not 1 <= len(entries) <= 10:
        raise ValueError('Automatic sampling needs 1-10 imported stops. Run Refresh-Data.cmd first.')
    codes = []
    for entry in entries:
        code = entry.get('stopCode') if isinstance(entry, dict) else None
        if not isinstance(code, str) or not re.fullmatch(r'[0-9]{5}', code) or code in codes:
            raise ValueError('Automatic stop selection contained invalid or duplicate codes. No configuration was changed.')
        codes.append(code)
    updated = validate_config({**values, 'SAMPLE_STOPS': ','.join(codes)}, analytics=True)
    save_config_values(root, {'SAMPLE_STOPS': updated['SAMPLE_STOPS']})
    print(f'Automatically selected {len(codes)} stops. View their names in the app. Your credentials are retained.')
    return updated


def write_launcher_state(root, values, needs_stops=None):
    # Both fields are constrained here even when a caller skips validation.
    port = int(values['APP_PORT'])
    if not 1024 <= port <= 65535:
        raise ValueError('Invalid launch port.')
    has_key = int(bool(values.get('LTA_ACCOUNT_KEY')))
    metadata = f'LAUNCH_PORT={port}\nLAUNCH_HAS_KEY={has_key}\n'
    if needs_stops is not None:
        metadata += f'LAUNCH_NEEDS_STOPS={int(bool(needs_stops))}\n'
    (root / '.launcher-state').write_text(metadata, encoding='ascii')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('setup', 'check', 'analytics', 'analytics-auto', 'apply-auto-stops'))
    parser.add_argument('--existing-volumes', action='store_true')
    args = parser.parse_args()
    try:
        needs_stops = None
        if args.action == 'setup':
            values = setup_config(ROOT, args.existing_volumes)
        elif args.action == 'analytics':
            values = analytics_config(ROOT)
            needs_stops = False
        elif args.action == 'analytics-auto':
            values, needs_stops = analytics_auto_config(ROOT)
        elif args.action == 'apply-auto-stops':
            values = apply_auto_stops(ROOT)
            needs_stops = False
        else:
            values = validate_config(read_config(ROOT / '.env'))
        write_launcher_state(ROOT, values, needs_stops)
        return 0
    except (ValueError, OSError) as error:
        print(f'Configuration stopped: {error}', file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        launcher = 'Choose-Sampling.cmd' if args.action == 'analytics' else 'Setup.cmd'
        print(f'\nConfiguration cancelled. Run {launcher} when ready.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
