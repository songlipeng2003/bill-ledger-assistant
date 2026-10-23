#!/usr/bin/env python3
"""Prepare a local Gmail trash manifest from complete, fresh reconciliation evidence. Never deletes."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def stamp(value):
    require(isinstance(value, str), 'capture timestamp required')
    t = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(t.tzinfo is not None and t.utcoffset() is not None, 'capture timestamp must include timezone')
    return t.astimezone(timezone.utc)


def index(items, key):
    require(isinstance(items, list), f'{key} list required')
    result = {}
    for item in items:
        require(isinstance(item, dict) and isinstance(item.get(key), str) and item[key], f'{key} required')
        require(item[key] not in result, f'duplicate {key}')
        result[item[key]] = item
    return result


def prepare(report, fresh, *, now=None, max_age_minutes=30):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    require(report.get('schema_version') == 1 and fresh.get('schema_version') == 1, 'schema_version must be 1')
    require(type(max_age_minutes) is int and 1 <= max_age_minutes <= 1440, 'invalid maximum evidence age')
    require(isinstance(report.get('report_id'), str) and re.fullmatch('[0-9a-f]{64}', report['report_id']), 'invalid report_id')
    matches = index(report.get('matches'), 'event_id')
    sources = index(report.get('sources'), 'id')
    latest = index(fresh.get('sources'), 'id')
    decisions = index(report.get('source_results'), 'source_id')
    require(set(decisions) == set(sources), 'report source decisions do not cover sources')
    require(isinstance(fresh.get('requested_source_ids'), list) and fresh['requested_source_ids'], 'explicit requested_source_ids required')
    selected = fresh['requested_source_ids']
    require(len(selected) == len(set(selected)) and set(selected) <= set(sources), 'unknown or duplicate requested sources')
    require(isinstance(fresh.get('mailbox'), str) and fresh['mailbox'], 'fresh mailbox identity required')
    require(not (set(latest) - set(sources)), 'fresh inventory contains unrelated sources')
    candidates, skipped = [], []
    used_ledger_ids = {}
    for event in matches.values():
        if event.get('status') == 'matched':
            rid = event.get('ledger_id')
            require(isinstance(rid, str) and rid, 'matched event lacks actual ledger ID')
            require(rid not in used_ledger_ids, 'one ledger ID reused across distinct events')
            used_ledger_ids[rid] = event['event_id']
    for sid in selected:
        source, current = sources[sid], latest.get(sid)
        reasons = []
        def fresh_enough(value):
            try:
                age = now - stamp(value)
                return -timedelta(minutes=2) <= age <= timedelta(minutes=max_age_minutes)
            except (ValueError, TypeError):
                return False
        if not fresh_enough(report.get('ledger_captured_at')):
            reasons.append('stale_or_invalid_ledger_snapshot')
        if not current:
            reasons.append('source_not_recaptured')
        if source.get('mailbox') != fresh['mailbox']:
            reasons.append('mailbox_mismatch_or_unknown')
        if not source.get('locator') or not isinstance(source.get('fingerprint'), str) or not re.fullmatch('[0-9a-f]{64}', source['fingerprint']):
            reasons.append('invalid_source_identity')
        ids = source.get('event_ids')
        if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)) or any(eid not in matches for eid in ids):
            reasons.append('invalid_or_incomplete_event_inventory')
        elif any(matches[eid].get('status') != 'matched' for eid in ids):
            reasons.append('unresolved_events')
        if decisions[sid].get('eligible') is not True:
            reasons.append('reconciliation_did_not_release_source')
        if source.get('complete') is not True or source.get('inventory_complete') is not True or source.get('review_blockers') != []:
            reasons.append('source_incomplete_or_blocked')
        if current:
            if current.get('mailbox') != fresh['mailbox'] or current.get('locator') != source.get('locator'):
                reasons.append('current_identity_changed')
            if current.get('complete') is not True or current.get('inventory_complete') is not True or current.get('review_blockers') != []:
                reasons.append('recapture_incomplete_or_blocked')
            if not fresh_enough(current.get('captured_at')):
                reasons.append('stale_recapture')
            for field in ['fingerprint', 'message_ids', 'event_ids']:
                if current.get(field) != source.get(field):
                    reasons.append('changed_' + field)
            if current.get('location') != 'inbox':
                reasons.append('already_in_trash' if current.get('location') == 'trash' else 'not_in_expected_inbox')
        if reasons:
            skipped.append({'source_id': sid, 'reasons': sorted(set(reasons))})
        else:
            candidates.append({'source_id': sid, 'mailbox': fresh['mailbox'], 'locator': source['locator'],
                               'fingerprint': source['fingerprint'], 'message_ids': source['message_ids'],
                               'event_ids': ids, 'recaptured_at': current['captured_at'], 'action': 'move_to_trash'})
    plan = {'schema_version': 1, 'report_id': report['report_id'], 'prepared_at': now.isoformat(),
            'requires_action_time_confirmation': True, 'execution_authorized': False,
            'action': 'move_to_trash_only', 'candidates': candidates, 'skipped': skipped}
    plan['manifest_id'] = digest(plan)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report'); parser.add_argument('fresh_sources'); parser.add_argument('--out', required=True)
    parser.add_argument('--max-age-minutes', type=int, default=30)
    args = parser.parse_args()
    try:
        require(Path(args.out).resolve() not in {Path(args.report).resolve(), Path(args.fresh_sources).resolve()}, 'do not overwrite evidence inputs')
        plan = prepare(json.loads(Path(args.report).read_text()), json.loads(Path(args.fresh_sources).read_text()), max_age_minutes=args.max_age_minutes)
        Path(args.out).write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    except (ValueError, TypeError, KeyError, OSError) as e:
        parser.exit(2, f'cleanup preparation stopped: {e}\n')

if __name__ == '__main__':
    main()
