"""Synthetic-only tests. No browser, Gmail, ledger service or personal source files."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / '.agents' / 'skills'


def load(name, relative):
    path = SKILLS / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = load('reconcile_local', 'bank-ledger-reconcile/scripts/reconcile.py')
C = load('cleanup_local', 'verified-bank-mail-cleanup/scripts/prepare_cleanup.py')
J = load('journal_local', 'sui-ledger-entry/scripts/entry_journal.py')
NOW = datetime.now(timezone.utc)
STAMP = NOW.isoformat()


def event(eid='event-a', **kw):
    value = {'id': eid, 'bank': 'cmb', 'type': 'purchase', 'date': '2030-04-10', 'currency': 'CNY',
             'amount': '10.00', 'account': 'card-a', 'merchant': 'Synthetic Shop',
             'observations': [{'source_id': 'source-a', 'message_id': 'message-a', 'item_ref': eid}]}
    value.update(kw)
    return value


def row(rid='ledger-a', **kw):
    value = {'id': rid, 'type': 'expense', 'date': '2030-04-10', 'currency': 'CNY', 'amount': '10.00',
             'account': 'card-a', 'merchant': 'Synthetic Shop'}
    value.update(kw)
    return value


def case(events=None, ledger=None, windows=None):
    events = deepcopy([event()] if events is None else events)
    ledger = deepcopy([row()] if ledger is None else ledger)
    sources = {}
    for e in events:
        for o in e['observations']:
            sid = o['source_id']
            source = sources.setdefault(sid, {'id': sid, 'mailbox': 'synthetic-mailbox', 'locator': 'synthetic://' + sid,
                'fingerprint': 'a' * 64, 'captured_at': STAMP, 'complete': True, 'inventory_complete': True,
                'message_ids': [], 'event_ids': [], 'review_blockers': []})
            if o['message_id'] not in source['message_ids']:
                source['message_ids'].append(o['message_id'])
            if e['id'] not in source['event_ids']:
                source['event_ids'].append(e['id'])
    return {'schema_version': 1, 'scope': {'start': '2030-04-01', 'end': '2030-04-30', 'accounts': ['card-a', 'card-b', 'cash-a']},
            'policy': {'date_windows': windows or {}}, 'sources': list(sources.values()), 'transactions': events,
            'ledger_snapshot': {'complete': True, 'captured_at': STAMP,
                'scope': {'start': '2030-03-01', 'end': '2030-05-31', 'accounts': ['card-a', 'card-b', 'cash-a']}, 'entries': ledger}}


def fresh(report):
    return {'schema_version': 1, 'mailbox': 'synthetic-mailbox',
            'requested_source_ids': [s['id'] for s in report['sources']],
            'sources': [{**deepcopy(s), 'captured_at': STAMP, 'location': 'inbox'} for s in report['sources']]}


def plan():
    return {'schema_version': 1, 'entries': [{'key': 'BILL-REF-A', 'event_id': 'event-a', 'type': 'income',
        'date': '2030-04-10', 'time_precision': 'date', 'currency': 'CNY', 'amount': '10.00', 'account': 'card-a',
        'category': '退款', 'memo': 'BILL-REF-A; Synthetic Shop; date-only source', 'source_refs': ['synthetic://source-a#row-1']}]}


def observation(entry=None, **kw):
    e = deepcopy(entry or plan()['entries'][0])
    e['id'] = 'actual-ledger-a'
    value = {'search_complete': True, 'captured_at': datetime.now(timezone.utc).isoformat(), 'records': [e]}
    value.update(kw)
    return value


class ReconcileTests(unittest.TestCase):
    def status(self, data):
        return R.reconcile(data)['matches'][0]['status']

    def test_exact_match(self):
        report = R.reconcile(case())
        self.assertEqual(report['matches'][0]['status'], 'matched')
        self.assertTrue(report['source_results'][0]['eligible'])

    def test_purchase_and_refund_do_not_cancel_or_share_row(self):
        data = case([event(), event('refund-a', type='refund')], [row(), row('refund-row', type='income')])
        report = R.reconcile(data)
        self.assertEqual([x['status'] for x in report['matches']], ['matched', 'matched'])
        self.assertEqual(len({x['ledger_id'] for x in report['matches']}), 2)

    def test_two_equal_transactions_two_rows(self):
        data = case([event(), event('event-b')], [row(), row('ledger-b')])
        report = R.reconcile(data)
        self.assertTrue(all(x['status'] == 'matched' for x in report['matches']))
        self.assertEqual(len({x['ledger_id'] for x in report['matches']}), 2)

    def test_two_equal_transactions_only_one_row(self):
        report = R.reconcile(case([event(), event('event-b')], [row()]))
        self.assertTrue(all(x['status'] == 'cardinality_conflict' for x in report['matches']))
        self.assertFalse(report['source_results'][0]['eligible'])

    def test_one_event_two_ledger_rows_is_ambiguous(self):
        self.assertEqual(self.status(case(ledger=[row(), row('ledger-b')])), 'ambiguous')

    def test_global_assignment_avoids_greedy_stealing(self):
        data = case([event('event-a', bank='hsbc', date='2030-04-11'), event('event-b', bank='hsbc', date='2030-04-09')],
                    [row('ledger-a', date='2030-04-10'), row('ledger-b', date='2030-04-12')], {'hsbc': 1})
        report = R.reconcile(data)
        matched = {m['event_id']: m['ledger_id'] for m in report['matches']}
        self.assertEqual(matched, {'event-a': 'ledger-b', 'event-b': 'ledger-a'})
        self.assertTrue(all(m['status'] == 'matched' for m in report['matches']))

    def test_material_alternative_assignment_is_ambiguous(self):
        data = case([event(bank='hsbc', date='2030-04-10'), event('event-b', bank='hsbc', date='2030-04-11')],
                    [row(), row('ledger-b', date='2030-04-11')], {'hsbc': 2})
        self.assertTrue(all(m['status'] == 'ambiguous' for m in R.reconcile(data)['matches']))

    def test_hsbc_two_days(self):
        self.assertEqual(self.status(case([event(bank='hsbc')], [row(date='2030-04-12')], {'hsbc': 2})), 'matched')

    def test_hsbc_window_not_used_for_cmb(self):
        self.assertEqual(self.status(case(ledger=[row(date='2030-04-12')], windows={'hsbc': 2})), 'date_mismatch')

    def test_account_mismatch_not_missing_expense(self):
        report = R.reconcile(case(ledger=[row(account='card-b')]))
        self.assertEqual(report['matches'][0]['status'], 'account_mismatch')
        self.assertEqual(report['summaries'][0]['unmatched_expenses'], '0.00')

    def test_currency_not_cross_matched(self):
        report = R.reconcile(case(ledger=[row(currency='HKD')]))
        self.assertEqual(report['matches'][0]['status'], 'unmatched')
        self.assertEqual(report['summaries'][0]['currency'], 'CNY')

    def test_repayment_is_target_transfer_not_expense(self):
        data = case([event(type='repayment', merchant='Repayment channel')],
                    [row(type='transfer', account=None, from_account='cash-a', to_account='card-a', merchant=None)])
        self.assertEqual(self.status(data), 'matched')

    def test_transfer_requires_both_accounts(self):
        data = case([event(type='transfer', from_account='cash-a', to_account='card-a')],
                    [row(type='transfer', account=None, from_account='cash-a', to_account='card-b')])
        self.assertEqual(self.status(data), 'account_mismatch')

    def test_unknown_transfer_leg_cannot_match(self):
        self.assertEqual(self.status(case([event(type='transfer', from_account='cash-a', to_account=None)])), 'insufficient_information')

    def test_interest_uses_period_without_invented_day(self):
        data = case([event(type='interest', date=None, merchant=None, period={'start': '2030-04-01', 'end': '2030-04-30'})],
                    [row(category_type='interest', merchant=None, date='2030-04-29')])
        report = R.reconcile(data)
        self.assertEqual(report['matches'][0]['status'], 'matched')
        self.assertIsNone(report['matches'][0]['date'])

    def test_missing_merchant_does_not_auto_release(self):
        report = R.reconcile(case(ledger=[row(merchant=None)]))
        self.assertEqual(report['matches'][0]['status'], 'matched_fields_only')
        self.assertFalse(report['source_results'][0]['eligible'])

    def test_wrong_merchant_stays_review(self):
        self.assertEqual(self.status(case(ledger=[row(merchant='Unrelated Shop')])), 'merchant_mismatch')

    def test_wrong_amount_stays_review(self):
        self.assertEqual(self.status(case(ledger=[row(amount='10.01')])), 'amount_mismatch')

    def test_temporary_authorization_not_final_consumption(self):
        self.assertEqual(self.status(case([event(type='authorization')])), 'pending_authorization')

    def test_unknown_currency_or_amount_not_zero(self):
        self.assertEqual(self.status(case([event(amount=None, currency=None)])), 'insufficient_information')

    def test_incomplete_snapshot_blocks_matching(self):
        d = case(); d['ledger_snapshot']['complete'] = False
        self.assertEqual(self.status(d), 'incomplete_ledger_coverage')

    def test_window_boundary_coverage_required(self):
        d = case([event(bank='hsbc', date='2030-04-01')], windows={'hsbc': 2})
        d['ledger_snapshot']['scope']['start'] = '2030-04-01'
        self.assertEqual(self.status(d), 'incomplete_ledger_coverage')

    def test_mixed_thread_one_missing_event_retains_whole_thread(self):
        d = case([event(), event('event-b', amount='20.00')], [row()])
        self.assertFalse(R.reconcile(d)['source_results'][0]['eligible'])

    def test_incomplete_thread_and_delivery_failure_retained(self):
        d = case(); d['sources'][0]['inventory_complete'] = False; d['sources'][0]['review_blockers'] = ['delivery failure']
        self.assertFalse(R.reconcile(d)['source_results'][0]['eligible'])

    def test_category_review_blocks_release(self):
        self.assertEqual(self.status(case(ledger=[row(review_blockers=['category unconfirmed'])])), 'needs_review')

    def test_multiple_sources_one_event_one_ledger_record(self):
        t = event(); t['observations'].append({'source_id': 'source-b', 'message_id': 'message-b', 'item_ref': 'row-1'})
        report = R.reconcile(case([t]))
        self.assertEqual(len(report['matches']), 1)
        self.assertTrue(all(s['eligible'] for s in report['source_results']))

    def test_float_negative_zero_extra_precision_rejected(self):
        for value in [10.0, '-10.00', '0.00', '10.001', '1,000.00', 'NaN']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                R.reconcile(case([event(amount=value)]))

    def test_duplicate_ledger_id_rejected(self):
        with self.assertRaises(ValueError):
            R.reconcile(case(ledger=[row(), row()]))

    def test_duplicate_observation_across_events_rejected(self):
        a, b = event(), event('event-b'); b['observations'] = deepcopy(a['observations'])
        with self.assertRaises(ValueError):
            R.reconcile(case([a, b]))

    def test_source_omitted_event_rejected(self):
        d = case(); d['sources'][0]['event_ids'].append('unread-event')
        with self.assertRaises(ValueError):
            R.reconcile(d)

    def test_event_outside_scope_rejected(self):
        with self.assertRaises(ValueError):
            R.reconcile(case([event(date='2030-03-31')]))

    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'bad.json'; p.write_text('{"schema_version":1,"schema_version":2}')
            with self.assertRaises(ValueError):
                R.read_json(p)

    def test_exact_decimal_separate_accounts_and_currencies(self):
        d = case([event(amount='0.10'), event('event-b', amount='0.20', account='card-b', currency='HKD')], [])
        report = R.reconcile(d)
        self.assertEqual(len(report['summaries']), 2)
        self.assertEqual({x['unmatched_expenses'] for x in report['summaries']}, {'0.10', '0.20'})

    def test_purchase_and_refund_not_netted_in_summary(self):
        d = case([event(), event('event-b', type='refund', amount='3.00')], [])
        summary = R.reconcile(d)['summaries'][0]
        self.assertEqual(summary['unmatched_expenses'], '10.00')
        self.assertEqual(summary['unmatched_refunds'], '3.00')


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.report = R.reconcile(case()); self.fresh = fresh(self.report)

    def prepare(self):
        return C.prepare(self.report, self.fresh, now=NOW)

    def test_ready_manifest_is_not_execution_authorization(self):
        p = self.prepare()
        self.assertEqual(len(p['candidates']), 1)
        self.assertFalse(p['execution_authorized'])
        self.assertTrue(p['requires_action_time_confirmation'])

    def test_changed_body_fingerprint_blocks(self):
        self.fresh['sources'][0]['fingerprint'] = 'b' * 64
        self.assertFalse(self.prepare()['candidates'])

    def test_new_message_blocks_even_if_fingerprint_not_updated(self):
        self.fresh['sources'][0]['message_ids'].append('new-message')
        self.assertFalse(self.prepare()['candidates'])

    def test_changed_event_inventory_blocks(self):
        self.fresh['sources'][0]['event_ids'].append('new-event')
        self.assertFalse(self.prepare()['candidates'])

    def test_stale_ledger_blocks(self):
        self.report['ledger_captured_at'] = (NOW - timedelta(minutes=31)).isoformat()
        self.assertFalse(self.prepare()['candidates'])

    def test_future_recapture_clock_blocks(self):
        self.fresh['sources'][0]['captured_at'] = (NOW + timedelta(minutes=3)).isoformat()
        self.assertFalse(self.prepare()['candidates'])

    def test_missing_recapture_is_skipped(self):
        self.fresh['sources'] = []
        self.assertFalse(self.prepare()['candidates'])

    def test_wrong_mailbox_blocks(self):
        self.fresh['mailbox'] = 'other-mailbox'
        self.assertFalse(self.prepare()['candidates'])

    def test_originally_in_trash_not_counted_as_new_deletion(self):
        self.fresh['sources'][0]['location'] = 'trash'
        p = self.prepare()
        self.assertFalse(p['candidates'])
        self.assertIn('already_in_trash', p['skipped'][0]['reasons'])

    def test_incomplete_attachment_inventory_blocks(self):
        self.fresh['sources'][0]['inventory_complete'] = False
        self.assertFalse(self.prepare()['candidates'])

    def test_fields_only_match_blocks_even_with_tampered_eligible_boolean(self):
        self.report['matches'][0]['status'] = 'matched_fields_only'
        self.report['source_results'][0]['eligible'] = True
        self.assertFalse(self.prepare()['candidates'])

    def test_reused_actual_ledger_id_rejected(self):
        duplicate = deepcopy(self.report['matches'][0]); duplicate['event_id'] = 'other-event'
        self.report['matches'].append(duplicate)
        with self.assertRaises(ValueError):
            self.prepare()

    def test_no_implicit_selection_of_all_sources(self):
        self.fresh['requested_source_ids'] = []
        with self.assertRaises(ValueError):
            self.prepare()

    def test_source_blocker_overrides_eligible_flag(self):
        self.report['sources'][0]['review_blockers'] = ['unresolved refund']
        self.assertFalse(self.prepare()['candidates'])


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.journal = J.initialize(plan()); self.key = 'BILL-REF-A'

    def attempted(self):
        J.transition(self.journal, self.key, 'submitted')

    def test_initialization_does_not_mean_saved(self):
        self.assertEqual(self.journal['entries'][self.key]['state'], 'planned')

    def test_submitted_cannot_be_blindly_retried(self):
        self.attempted()
        with self.assertRaises(ValueError):
            J.transition(self.journal, self.key, 'submitted')

    def test_uncertain_cannot_be_blindly_retried(self):
        self.attempted(); J.transition(self.journal, self.key, 'uncertain')
        with self.assertRaises(ValueError):
            J.transition(self.journal, self.key, 'submitted')

    def test_actual_readback_verifies_record(self):
        self.attempted(); J.transition(self.journal, self.key, 'verify', observation())
        self.assertEqual(self.journal['entries'][self.key]['state'], 'verified')
        self.assertEqual(self.journal['entries'][self.key]['actual_ledger_id'], 'actual-ledger-a')

    def test_incorrect_amount_stays_uncertain(self):
        self.attempted(); observed = observation(); observed['records'][0]['amount'] = '10.01'
        J.transition(self.journal, self.key, 'verify', observed)
        self.assertEqual(self.journal['entries'][self.key]['state'], 'uncertain')

    def test_missing_actual_record_stays_uncertain(self):
        self.attempted(); J.transition(self.journal, self.key, 'verify', observation(records=[]))
        self.assertEqual(self.journal['entries'][self.key]['state'], 'uncertain')

    def test_duplicate_stable_key_stays_uncertain(self):
        self.attempted(); observed = observation(); observed['records'].append(deepcopy(observed['records'][0]))
        J.transition(self.journal, self.key, 'verify', observed)
        self.assertEqual(self.journal['entries'][self.key]['state'], 'uncertain')

    def test_incomplete_search_cannot_prove_absence(self):
        self.attempted()
        with self.assertRaises(ValueError):
            J.transition(self.journal, self.key, 'verify', observation(records=[], search_complete=False))
        self.assertEqual(self.journal['entries'][self.key]['state'], 'submitted')

    def test_stale_readback_rejected(self):
        self.attempted()
        with self.assertRaises(ValueError):
            J.transition(self.journal, self.key, 'verify', observation(captured_at=(NOW - timedelta(hours=1)).isoformat()))

    def test_plan_duplicate_event_rejected(self):
        p = plan(); e = deepcopy(p['entries'][0]); e['key'] = 'OTHER'; e['memo'] = 'OTHER'; p['entries'].append(e)
        with self.assertRaises(ValueError):
            J.initialize(p)

    def test_reused_actual_id_cannot_verify_second_entry(self):
        p = plan(); second = deepcopy(p['entries'][0]); second.update(key='BILL-REF-B', event_id='event-b', memo='BILL-REF-B'); p['entries'].append(second)
        journal = J.initialize(p)
        J.transition(journal, self.key, 'submitted'); J.transition(journal, self.key, 'verify', observation())
        J.transition(journal, second['key'], 'submitted'); J.transition(journal, second['key'], 'verify', observation(second))
        self.assertEqual(journal['entries'][second['key']]['state'], 'uncertain')

    def test_invalid_amount_rejected(self):
        for value in [10.0, '-10.00', '0.00']:
            p = plan(); p['entries'][0]['amount'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                J.initialize(p)

    def test_known_time_is_required_and_rechecked(self):
        p = plan(); p['entries'][0].update(time_precision='minute', time='09:31')
        journal = J.initialize(p); J.transition(journal, self.key, 'submitted')
        observed = observation(p['entries'][0]); observed['records'][0]['time'] = '09:32'
        J.transition(journal, self.key, 'verify', observed)
        self.assertEqual(journal['entries'][self.key]['state'], 'uncertain')

    def test_date_placeholder_is_explicit_not_fake_actual_time(self):
        p = plan(); p['entries'][0]['time'] = '00:00'
        with self.assertRaises(ValueError):
            J.initialize(p)
        p['entries'][0]['time_is_placeholder'] = True
        self.assertEqual(J.initialize(p)['entries'][self.key]['state'], 'planned')

    def test_missing_or_invalid_known_clock_rejected(self):
        for value in [None, '24:00', '12:99']:
            p = plan(); p['entries'][0].update(time_precision='minute', time=value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                J.initialize(p)

    def test_cli_refuses_overwrite_existing_journal(self):
        script = SKILLS / 'sui-ledger-entry/scripts/entry_journal.py'
        with tempfile.TemporaryDirectory() as temp:
            p, j = Path(temp) / 'plan.json', Path(temp) / 'journal.json'; p.write_text(json.dumps(plan()))
            args = [sys.executable, str(script), 'init', str(p), str(j)]
            subprocess.run(args, check=True, capture_output=True)
            before = j.read_bytes()
            retry = subprocess.run(args, capture_output=True)
            self.assertNotEqual(retry.returncode, 0)
            self.assertEqual(j.read_bytes(), before)


class SourceOnlyMemoJournalTests(unittest.TestCase):
    def source_plan(self, memo='Synthetic Shop退款'):
        p = plan()
        p['entries'][0].update(memo=memo, before_snapshot={
            'complete': True, 'captured_at': STAMP, 'ledger_ids': ['existing-ledger-a']})
        return p

    def submitted(self, p):
        j = J.initialize(p)
        J.transition(j, p['entries'][0]['key'], 'submitted')
        return j

    def test_source_memo_without_embedded_key_verifies_by_new_id(self):
        p = self.source_plan(); e = p['entries'][0]; j = self.submitted(p)
        obs = observation(e); obs['records'][0].pop('key')
        J.transition(j, e['key'], 'verify', obs)
        self.assertEqual(j['entries'][e['key']]['state'], 'verified')
        self.assertEqual(j['entries'][e['key']]['entry']['memo'], 'Synthetic Shop退款')

    def test_empty_source_memo_is_preserved(self):
        p = self.source_plan(''); e = p['entries'][0]; j = self.submitted(p)
        J.transition(j, e['key'], 'verify', observation(e))
        self.assertEqual(j['entries'][e['key']]['state'], 'verified')
        self.assertEqual(j['entries'][e['key']]['entry']['memo'], '')

    def test_source_only_memo_requires_complete_before_snapshot(self):
        for snapshot in [None, {'complete': False, 'captured_at': STAMP, 'ledger_ids': []}]:
            p = self.source_plan(); p['entries'][0]['before_snapshot'] = snapshot
            with self.subTest(snapshot=snapshot), self.assertRaises(ValueError):
                J.initialize(p)

    def test_duplicate_or_invalid_before_ids_rejected(self):
        for ids in [['existing-a', 'existing-a'], [''], [None], 'not-a-list']:
            p = self.source_plan(); p['entries'][0]['before_snapshot']['ledger_ids'] = ids
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                J.initialize(p)

    def test_stale_before_snapshot_rejected_before_saving(self):
        p = self.source_plan(); e = p['entries'][0]; j = J.initialize(p)
        e['before_snapshot']['captured_at'] = (NOW - timedelta(minutes=31)).isoformat()
        with self.assertRaises(ValueError):
            J.transition(j, e['key'], 'submitted')
        self.assertEqual(j['entries'][e['key']]['state'], 'planned')

    def test_existing_record_cannot_be_relabelled_as_new(self):
        p = self.source_plan(); e = p['entries'][0]; j = self.submitted(p)
        obs = observation(e); obs['records'][0]['id'] = 'existing-ledger-a'
        J.transition(j, e['key'], 'verify', obs)
        self.assertEqual(j['entries'][e['key']]['state'], 'uncertain')

    def test_multiple_new_identical_records_remain_ambiguous(self):
        p = self.source_plan(); e = p['entries'][0]; j = self.submitted(p)
        obs = observation(e); other = deepcopy(obs['records'][0])
        other.update(id='second-new-record'); other.pop('key')
        obs['records'].append(other)
        J.transition(j, e['key'], 'verify', obs)
        self.assertEqual(j['entries'][e['key']]['state'], 'uncertain')

    def test_appended_generated_memo_does_not_verify(self):
        p = self.source_plan(); e = p['entries'][0]; j = self.submitted(p)
        obs = observation(e); obs['records'][0]['memo'] += '; ' + e['key']
        J.transition(j, e['key'], 'verify', obs)
        self.assertEqual(j['entries'][e['key']]['state'], 'uncertain')

    def test_readback_before_baseline_is_rejected(self):
        p = self.source_plan(); e = p['entries'][0]; j = self.submitted(p)
        with self.assertRaises(ValueError):
            J.transition(j, e['key'], 'verify', observation(e, captured_at=(NOW - timedelta(seconds=1)).isoformat()))

    def test_source_only_record_id_cannot_verify_two_events(self):
        p = self.source_plan(); first = p['entries'][0]; second = deepcopy(first)
        second.update(key='BILL-REF-B', event_id='event-b'); p['entries'].append(second)
        j = self.submitted(p); J.transition(j, first['key'], 'verify', observation(first))
        J.transition(j, second['key'], 'submitted')
        J.transition(j, second['key'], 'verify', observation(second))
        self.assertEqual(j['entries'][second['key']]['state'], 'uncertain')

    def test_complete_empty_baseline_can_verify_first_record(self):
        p = self.source_plan(); e = p['entries'][0]; e['before_snapshot']['ledger_ids'] = []
        j = self.submitted(p); J.transition(j, e['key'], 'verify', observation(e))
        self.assertEqual(j['entries'][e['key']]['state'], 'verified')


class IntegrationTests(unittest.TestCase):
    def test_synthetic_cli_reconcile_then_prepare_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            i, o, md, f, m = [folder / name for name in ['input.json', 'report.json', 'report.md', 'fresh.json', 'manifest.json']]
            i.write_text(json.dumps(case()))
            subprocess.run([sys.executable, str(SKILLS/'bank-ledger-reconcile/scripts/reconcile.py'), str(i), '--out', str(o), '--markdown', str(md)], check=True, capture_output=True)
            report = json.loads(o.read_text()); f.write_text(json.dumps(fresh(report)))
            subprocess.run([sys.executable, str(SKILLS/'verified-bank-mail-cleanup/scripts/prepare_cleanup.py'), str(o), str(f), '--out', str(m)], check=True, capture_output=True)
            manifest = json.loads(m.read_text())
            self.assertEqual(len(manifest['candidates']), 1)
            self.assertFalse(manifest['execution_authorized'])
            self.assertIn('matched', md.read_text())


if __name__ == '__main__':
    unittest.main()
