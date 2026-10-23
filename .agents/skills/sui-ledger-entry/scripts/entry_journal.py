#!/usr/bin/env python3
"""Local record of planned/submitted/uncertain/verified ledger writes; no browser or network I/O."""
import argparse
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

FIELDS = ('type', 'date', 'time', 'time_precision', 'time_is_placeholder', 'currency', 'amount', 'account', 'from_account', 'to_account', 'category', 'memo')


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_entry(entry):
    require(isinstance(entry, dict) and isinstance(entry.get('key'), str) and entry['key'], 'entry key required')
    require(isinstance(entry.get('event_id'), str) and entry['event_id'], 'event_id required')
    require(entry.get('type') in {'expense', 'income', 'transfer'}, 'invalid entry type')
    require(isinstance(entry.get('amount'), str) and re.fullmatch(r'(0|[1-9]\d*)\.\d{2}', entry['amount']) and Decimal(entry['amount']) > 0, 'positive two-decimal amount string required')
    require(isinstance(entry.get('currency'), str) and re.fullmatch('[A-Z]{3}', entry['currency']), 'currency required')
    require(isinstance(entry.get('date'), str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', entry['date']), 'ISO date required')
    date.fromisoformat(entry['date'])
    require(entry.get('time_precision') in {'date', 'minute', 'second'}, 'time_precision required')
    precision = entry['time_precision']
    clock = entry.get('time')
    if precision in {'minute', 'second'}:
        pattern = r'(?:[01]\d|2[0-3]):[0-5]\d' + (r':[0-5]\d' if precision == 'second' else '')
        require(isinstance(clock, str) and re.fullmatch(pattern, clock), 'known time precision requires valid explicit time')
        require(entry.get('time_is_placeholder') is not True, 'known time cannot be a placeholder')
    elif clock is not None:
        require(clock == '00:00' and entry.get('time_is_placeholder') is True, 'date-only UI time must be an explicitly labelled placeholder')
    require(isinstance(entry.get('memo'), str) and entry['key'] in entry['memo'], 'memo must preserve stable key')
    if entry['type'] == 'transfer':
        require(all(isinstance(entry.get(k), str) and entry[k] for k in ['from_account', 'to_account']), 'both transfer accounts required')
        require(entry['from_account'] != entry['to_account'], 'identical transfer accounts')
    else:
        require(isinstance(entry.get('account'), str) and entry['account'] and isinstance(entry.get('category'), str) and entry['category'], 'account/category required')
    require(isinstance(entry.get('source_refs'), list) and entry['source_refs'] and all(isinstance(ref, str) and ref for ref in entry['source_refs']), 'source_refs required')


def initialize(plan):
    require(plan.get('schema_version') == 1 and isinstance(plan.get('entries'), list) and plan['entries'], 'nonempty version-1 entry plan required')
    result = {}
    events = set()
    for entry in plan['entries']:
        validate_entry(entry)
        key = entry['key']
        require(key not in result and entry['event_id'] not in events, 'duplicate key/event in write plan')
        events.add(entry['event_id'])
        result[key] = {'entry': entry, 'state': 'planned', 'history': []}
    return {'schema_version': 1, 'plan_id': digest(plan), 'entries': result}


def transition(journal, key, action, observation=None):
    require(journal.get('schema_version') == 1 and key in journal.get('entries', {}), 'unknown journal/key')
    record = journal['entries'][key]
    previous = record['state']
    event = {'at': datetime.now(timezone.utc).isoformat(), 'action': action, 'previous_state': previous}
    if action == 'submitted':
        require(previous == 'planned', 'write already attempted; inspect actual ledger before any retry')
        record['state'] = 'submitted'
    elif action == 'uncertain':
        require(previous in {'planned', 'submitted', 'uncertain'}, 'verified entry cannot become uncertain through a retry')
        record['state'] = 'uncertain'
    elif action == 'verify':
        require(previous in {'submitted', 'uncertain'}, 'verify requires a recorded attempt')
        require(isinstance(observation, dict) and observation.get('search_complete') is True,
                'fresh complete search required; incomplete search cannot prove absence')
        captured = datetime.fromisoformat(str(observation.get('captured_at', '')).replace('Z', '+00:00'))
        require(captured.tzinfo is not None, 'observation timestamp must include timezone')
        age = (datetime.now(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds()
        require(-120 <= age <= 1800, 'observation is stale; recapture actual records')
        rows = observation.get('records')
        require(isinstance(rows, list), 'records list required')
        hits = [r for r in rows if isinstance(r, dict) and r.get('key') == key]
        record['state'] = 'uncertain'
        if len(hits) != 1:
            event['reason'] = 'duplicate_or_missing_actual_record'
        else:
            actual, expected = hits[0], record['entry']
            differences = [field for field in FIELDS if expected.get(field) != actual.get(field)]
            used_ids = {r.get('actual_ledger_id') for k, r in journal['entries'].items() if k != key and r['state'] == 'verified'}
            if differences or not isinstance(actual.get('id'), str) or not actual['id'] or actual['id'] in used_ids:
                event['reason'] = 'field_mismatch_or_reused_ledger_id'
                event['different_fields'] = differences
            else:
                record['state'] = 'verified'
                record['actual_ledger_id'] = actual['id']
                record['verified_at'] = observation['captured_at']
        event['observation_digest'] = digest(observation)
    else:
        raise ValueError('unsupported transition')
    event['state'] = record['state']
    record['history'].append(event)
    return journal


@contextmanager
def locked(path):
    lock_path = Path(str(path) + '.lock')
    try:
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError('journal locked; inspect concurrent/interrupted writer before removing a stale lock')
    try:
        os.close(fd)
        yield
    finally:
        lock_path.unlink()


def atomic_write(path, data):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init'); init.add_argument('plan'); init.add_argument('journal')
    for name in ['submitted', 'uncertain', 'verify']:
        p = sub.add_parser(name); p.add_argument('journal'); p.add_argument('key')
        if name == 'verify':
            p.add_argument('observation')
    args = parser.parse_args()
    path = Path(args.journal)
    try:
        with locked(path):
            if args.command == 'init':
                require(not path.exists(), 'journal already exists; resume it instead of resetting')
                require(path.resolve() != Path(args.plan).resolve(), 'journal must differ from plan')
                journal = initialize(json.loads(Path(args.plan).read_text()))
            else:
                journal = json.loads(path.read_text())
                observation = json.loads(Path(args.observation).read_text()) if args.command == 'verify' else None
                journal = transition(journal, args.key, args.command, observation)
            atomic_write(path, journal)
    except (ValueError, TypeError, KeyError, OSError) as e:
        parser.exit(2, f'journal stopped: {e}\n')

if __name__ == '__main__':
    main()
