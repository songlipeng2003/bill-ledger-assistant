#!/usr/bin/env python3
"""Pure local, conservative one-to-one reconciliation. No browser or network I/O."""
from __future__ import annotations
import argparse
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

TYPES = {'purchase', 'direct_debit', 'refund', 'credit', 'fee', 'interest', 'repayment', 'transfer', 'authorization'}
OUT = {'purchase', 'direct_debit', 'fee', 'interest'}
IN = {'refund', 'credit'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical_digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        require(key not in out, f'duplicate JSON key: {key}')
        out[key] = value
    return out


def read_json(path):
    return json.loads(Path(path).read_text(), object_pairs_hook=_pairs,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f'invalid JSON constant: {x}')))


def money(value):
    require(isinstance(value, str) and re.fullmatch(r'(0|[1-9]\d*)\.\d{2}', value),
            f'amount must be a positive two-decimal string, not a float: {value!r}')
    amount = Decimal(value)
    require(amount > 0, 'zero or negative transaction amount; use explicit type and positive magnitude')
    return amount


def day(value):
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value), 'date must be YYYY-MM-DD')
    return date.fromisoformat(value)


def timestamp(value):
    require(isinstance(value, str), 'capture timestamp required')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None, 'capture timestamp requires timezone')
    return parsed


def string(value):
    return isinstance(value, str) and bool(value.strip())


def unique(items, label):
    require(isinstance(items, list) and all(isinstance(x, dict) and string(x.get('id')) for x in items), f'{label}: object list with IDs required')
    ids = [x['id'] for x in items]
    require(len(set(ids)) == len(ids), f'{label}: duplicate IDs')
    return {x['id']: x for x in items}


def check_scope(scope):
    require(isinstance(scope, dict), 'scope object required')
    require(day(scope.get('start')) <= day(scope.get('end')), 'scope start is after end')
    require(isinstance(scope.get('accounts'), list) and scope['accounts'] and all(string(x) for x in scope['accounts']), 'scope accounts required')
    require(len(scope['accounts']) == len(set(scope['accounts'])), 'duplicate scope accounts')


def fingerprint(value):
    return isinstance(value, str) and bool(re.fullmatch('[0-9a-f]{64}', value))


def date_window(t, policy):
    if t.get('date') is not None:
        n = policy.get('date_windows', {}).get(t['bank'], 0)
        d = day(t['date'])
        return d - timedelta(days=n), d + timedelta(days=n)
    if t['type'] in {'interest', 'fee'} and t.get('period'):
        p = t['period']
        start, end = day(p.get('start')), day(p.get('end'))
        require(start <= end, 'invalid fee/interest period')
        return start, end
    return None


def event_accounts(t):
    if t['type'] == 'transfer':
        return {t.get('from_account'), t.get('to_account')} - {None, ''}
    return {t.get('account')} - {None, ''}


def validate(data):
    require(data.get('schema_version') == 1, 'schema_version must be 1')
    check_scope(data.get('scope'))
    sources = unique(data.get('sources'), 'sources')
    events = unique(data.get('transactions'), 'transactions')
    snapshot = data.get('ledger_snapshot')
    require(isinstance(snapshot, dict) and isinstance(snapshot.get('complete'), bool), 'ledger snapshot and boolean complete required')
    timestamp(snapshot.get('captured_at'))
    check_scope(snapshot.get('scope'))
    ledger = unique(snapshot.get('entries'), 'ledger')
    policy = data.get('policy', {})
    require(isinstance(policy, dict) and isinstance(policy.get('date_windows', {}), dict), 'invalid policy')
    require(all(string(k) and type(v) is int and 0 <= v <= 31 for k, v in policy.get('date_windows', {}).items()), 'invalid bank date window')
    seen_observations = set()
    linked = defaultdict(set)
    for t in events.values():
        require(t.get('type') in TYPES and isinstance(t.get('bank'), str) and re.fullmatch('[a-z0-9-]+', t['bank']), 'invalid transaction type or lowercase bank key')
        require(isinstance(t.get('review_blockers', []), list), 'event blockers must be a list')
        require('amount' in t and 'currency' in t and 'date' in t, 'explicit amount/currency/date fields required, null allowed for unknown data')
        if t['amount'] is not None:
            money(t['amount'])
        if t['currency'] is not None:
            require(bool(re.fullmatch('[A-Z]{3}', t['currency'])), 'invalid currency')
        window = date_window(t, policy)
        if t['date'] is not None:
            require(day(data['scope']['start']) <= day(t['date']) <= day(data['scope']['end']), 'transaction date is outside requested scope')
        elif window:
            require(day(data['scope']['start']) <= window[0] <= window[1] <= day(data['scope']['end']), 'interest/fee period outside scope')
        require(event_accounts(t) <= set(data['scope']['accounts']), 'event account outside requested scope')
        obs = t.get('observations')
        require(isinstance(obs, list) and obs, 'event observations required')
        for o in obs:
            require(isinstance(o, dict), 'invalid observation')
            key = (o.get('source_id'), o.get('message_id'), o.get('item_ref'))
            require(all(string(x) for x in key) and key not in seen_observations, 'duplicate or invalid observation identity')
            seen_observations.add(key)
            require(key[0] in sources and key[1] in sources[key[0]].get('message_ids', []), 'observation is not in source inventory')
            linked[key[0]].add(t['id'])
    for s in sources.values():
        require(string(s.get('mailbox')) and string(s.get('locator')) and string(s.get('captured_at')) and fingerprint(s.get('fingerprint')), 'source mailbox, locator, capture time and SHA-256 required')
        timestamp(s.get('captured_at'))
        require(type(s.get('complete')) is bool and type(s.get('inventory_complete')) is bool, 'source completeness must be boolean')
        for key in ['message_ids', 'event_ids', 'review_blockers']:
            require(isinstance(s.get(key), list), f'source {key} list required')
        require(s['message_ids'] and all(string(x) for x in s['message_ids']), 'source message inventory empty')
        require(len(set(s['message_ids'])) == len(s['message_ids']), 'duplicate source message IDs')
        require(len(set(s['event_ids'])) == len(s['event_ids']), 'duplicate source event IDs')
        require(set(s['event_ids']) <= events.keys(), 'source contains event missing from input; do not omit out-of-scope events to make a thread deletable')
        require(set(s['event_ids']) == linked[s['id']], 'source event inventory differs from observations')
    for r in ledger.values():
        require(r.get('type') in {'expense', 'income', 'transfer'}, 'invalid ledger type')
        require(isinstance(r.get('review_blockers', []), list), 'ledger blockers must be a list')
        money(r.get('amount'))
        day(r.get('date'))
        require(isinstance(r.get('currency'), str) and bool(re.fullmatch('[A-Z]{3}', r['currency'])), 'invalid ledger currency')
        require(day(snapshot['scope']['start']) <= day(r['date']) <= day(snapshot['scope']['end']), 'ledger date outside declared snapshot')
        accounts = {r.get('from_account'), r.get('to_account')} if r['type'] == 'transfer' else {r.get('account')}
        require(all(string(a) for a in accounts) and accounts <= set(snapshot['scope']['accounts']), 'ledger account outside declared snapshot')
    return sources, events, ledger, policy


def normalized_merchant(item):
    # A merchant_key may be supplied only for evidence-backed aliases, not guessed similarity.
    return ' '.join(str(item.get('merchant_key') or item.get('merchant') or '').casefold().split())


def merchant_compatible(t, r):
    a, b = normalized_merchant(t), normalized_merchant(r)
    return not a or not b or a == b


def direction(t, r):
    if t['type'] in OUT:
        return r['type'] == 'expense'
    if t['type'] in IN:
        return r['type'] == 'income'
    return t['type'] in {'repayment', 'transfer'} and r['type'] == 'transfer'


def same_account(t, r):
    if t['type'] == 'repayment':
        return r.get('to_account') == t.get('account')
    if t['type'] == 'transfer':
        return r.get('from_account') == t.get('from_account') and r.get('to_account') == t.get('to_account')
    return r.get('account') == t.get('account')


def strong_evidence(t, r):
    if t['type'] in {'repayment', 'transfer'}:
        return True
    if t['type'] in {'interest', 'fee'} and r.get('category_type') == t['type']:
        return True
    return bool(normalized_merchant(t) and normalized_merchant(r) and merchant_compatible(t, r))


def maximum_matching(event_ids, edges, forbidden=None):
    owner = {}
    def augment(eid, seen):
        for rid in edges[eid]:
            if (eid, rid) == forbidden or rid in seen:
                continue
            seen.add(rid)
            if rid not in owner or augment(owner[rid], seen):
                owner[rid] = eid
                return True
        return False
    for eid in sorted(event_ids, key=lambda x: (len(edges[x]), x)):
        augment(eid, set())
    return {eid: rid for rid, eid in owner.items()}


def semantic_signature(item, event=False):
    keys = ['type', 'date', 'currency', 'amount', 'account', 'from_account', 'to_account', 'category_type', 'category']
    if event:
        keys += ['bank', 'period']
    return canonical_digest({**{k: item.get(k) for k in keys}, 'merchant': normalized_merchant(item)})


def reconcile(data):
    sources, events, ledger, policy = validate(data)
    snapshot = data['ledger_snapshot']
    edges = {}
    rows = {}
    def result(t, status, candidates=None, matched=None):
        return {'event_id': t['id'], 'bank': t['bank'], 'type': t['type'], 'date': t['date'],
                'account': t.get('account') or f"{t.get('from_account')} -> {t.get('to_account')}",
                'currency': t['currency'], 'amount': t['amount'], 'merchant': t.get('merchant'),
                'status': status, 'candidate_ledger_ids': candidates or [], 'ledger_id': matched}
    for eid, t in events.items():
        window = date_window(t, policy)
        if t['type'] == 'authorization':
            rows[eid] = result(t, 'pending_authorization'); continue
        valid_accounts = (len(event_accounts(t)) == 2 if t['type'] == 'transfer' else bool(t.get('account')))
        if not window or t['amount'] is None or t['currency'] is None or not valid_accounts:
            rows[eid] = result(t, 'insufficient_information'); continue
        covers = (snapshot['complete'] and day(snapshot['scope']['start']) <= window[0]
                  and window[1] <= day(snapshot['scope']['end'])
                  and event_accounts(t) <= set(snapshot['scope']['accounts']))
        if not covers:
            rows[eid] = result(t, 'incomplete_ledger_coverage'); continue
        base = [r for r in ledger.values() if direction(t, r) and r['currency'] == t['currency']]
        in_window = [r for r in base if window[0] <= day(r['date']) <= window[1]]
        eligible = [r for r in in_window if same_account(t, r) and r['amount'] == t['amount'] and merchant_compatible(t, r)]
        edges[eid] = sorted(r['id'] for r in eligible)
        if eligible:
            continue
        wrong_account = [r['id'] for r in in_window if r['amount'] == t['amount'] and not same_account(t, r) and merchant_compatible(t, r)]
        wrong_merchant = [r['id'] for r in in_window if same_account(t, r) and r['amount'] == t['amount'] and not merchant_compatible(t, r)]
        wrong_amount = [r['id'] for r in in_window if same_account(t, r) and normalized_merchant(t)
                        and normalized_merchant(t) == normalized_merchant(r) and r['amount'] != t['amount']]
        wrong_date = [r['id'] for r in base if same_account(t, r) and r['amount'] == t['amount']
                      and merchant_compatible(t, r) and not window[0] <= day(r['date']) <= window[1]]
        if wrong_account:
            rows[eid] = result(t, 'account_mismatch', wrong_account)
        elif wrong_merchant:
            rows[eid] = result(t, 'merchant_mismatch', wrong_merchant)
        elif wrong_amount:
            rows[eid] = result(t, 'amount_mismatch', wrong_amount)
        elif wrong_date:
            rows[eid] = result(t, 'date_mismatch', wrong_date)
        else:
            rows[eid] = result(t, 'unmatched')
    reverse = defaultdict(set)
    for eid, ids in edges.items():
        for rid in ids:
            reverse[rid].add(eid)
    remaining = {eid for eid in edges if edges[eid]}
    while remaining:
        pending = deque([min(remaining)]); component = set(); row_ids = set()
        while pending:
            eid = pending.popleft()
            if eid in component:
                continue
            component.add(eid); remaining.discard(eid)
            for rid in edges[eid]:
                row_ids.add(rid)
                pending.extend(reverse[rid] - component)
        assignment = maximum_matching(component, edges)
        interchangeable = (len(component) == len(row_ids) and
            len({semantic_signature(events[eid], True) for eid in component}) == 1 and
            len({semantic_signature(ledger[rid]) for rid in row_ids}) == 1)
        alternate = not interchangeable and any(len(maximum_matching(component, edges, (eid, rid))) == len(assignment)
                        for eid, rid in assignment.items())
        for eid in component:
            t = events[eid]
            if len(assignment) < len(component):
                rows[eid] = result(t, 'cardinality_conflict', edges[eid])
            elif alternate and not interchangeable:
                rows[eid] = result(t, 'ambiguous', edges[eid])
            else:
                rid = assignment[eid]
                status = ('needs_review' if t.get('review_blockers') or ledger[rid].get('review_blockers') else
                          'matched' if strong_evidence(t, ledger[rid]) else 'matched_fields_only')
                rows[eid] = result(t, status, edges[eid], rid)
    summaries = defaultdict(lambda: {'unmatched_expenses': Decimal(0), 'unmatched_refunds': Decimal(0),
                                    'unmatched_other_income': Decimal(0), 'unmatched_repayments': Decimal(0),
                                    'unmatched_transfers': Decimal(0), 'review_count': 0})
    for row in rows.values():
        if row['currency'] is None:
            continue
        key = (row['account'], row['currency'])
        if row['status'] == 'unmatched':
            bucket = ('unmatched_expenses' if row['type'] in OUT else 'unmatched_refunds' if row['type'] == 'refund'
                      else 'unmatched_other_income' if row['type'] == 'credit' else 'unmatched_repayments' if row['type'] == 'repayment'
                      else 'unmatched_transfers')
            summaries[key][bucket] += money(row['amount'])
        elif row['status'] != 'matched':
            summaries[key]['review_count'] += 1
    source_results = []
    for sid, source in sources.items():
        reasons = []
        if not source['complete'] or not source['inventory_complete']:
            reasons.append('incomplete_source_inventory')
        if source['review_blockers']:
            reasons.append('source_review_blockers')
        if not source['event_ids']:
            reasons.append('no_transaction_events')
        if any(rows[eid]['status'] != 'matched' for eid in source['event_ids']):
            reasons.append('unresolved_events')
        source_results.append({'source_id': sid, 'eligible': not reasons, 'reasons': reasons})
    return {'schema_version': 1, 'report_id': canonical_digest(data), 'scope': data['scope'],
            'ledger_captured_at': snapshot['captured_at'], 'sources': list(sources.values()),
            'matches': [rows[eid] for eid in events], 'source_results': source_results,
            'summaries': [{'account': key[0], 'currency': key[1], **{k: f'{v:.2f}' if isinstance(v, Decimal) else v for k, v in values.items()}}
                          for key, values in sorted(summaries.items())],
            'notes': ['Unmatched cashflow is not current debt, proven financial loss, or a verified balance difference.',
                      'Eligibility is a plan only; source recapture, scope authorization and action-time confirmation are still required.']}


def markdown(report):
    def esc(x):
        return str(x if x is not None else 'unknown').replace('|', '／').replace('\n', ' ')
    lines = ['# 银行账本对账结果', '', '本报告只比较提供的快照，不是当前余额或实际损失证明。', '',
             '| 事件 | 账户 | 日期 | 类型 | 币种 | 金额 | 状态 | 实际账目 |', '|---|---|---|---|---|---:|---|---|']
    for r in report['matches']:
        lines.append('| ' + ' | '.join(esc(r[k]) for k in ['event_id', 'account', 'date', 'type', 'currency', 'amount', 'status', 'ledger_id']) + ' |')
    lines += ['', '## 精确未匹配金额（待确认候选另列，不算作确定遗漏）', '']
    for s in report['summaries']:
        lines.append(f"- {esc(s['account'])} / {esc(s['currency'])}: 支出 {s['unmatched_expenses']}；退款 {s['unmatched_refunds']}；其他收入 {s['unmatched_other_income']}；还款 {s['unmatched_repayments']}；转账 {s['unmatched_transfers']}；待复核 {s['review_count']}项。")
    lines += ['', '## 邮件级结果', '']
    for s in report['source_results']:
        lines.append(f"- {esc(s['source_id'])}: {'可准备清理清单（未获执行授权）' if s['eligible'] else '保留：' + ', '.join(s['reasons'])}")
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input'); parser.add_argument('--out', required=True); parser.add_argument('--markdown')
    args = parser.parse_args()
    try:
        report = reconcile(read_json(args.input))
        require(Path(args.input).resolve() != Path(args.out).resolve(), 'do not overwrite the input snapshot')
        if args.markdown:
            require(Path(args.markdown).resolve() not in {Path(args.input).resolve(), Path(args.out).resolve()}, 'output paths must differ')
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        if args.markdown:
            Path(args.markdown).write_text(markdown(report))
    except (ValueError, KeyError, TypeError, OSError) as e:
        parser.exit(2, f'reconciliation stopped: {e}\n')

if __name__ == '__main__':
    main()
