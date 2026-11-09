"""Synthetic store matching and saved-store readback tests; no live ledger or email access."""
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
SCRIPTS = ROOT / '.agents/skills/sui-ledger-entry/scripts'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = load('store_selection_local', SCRIPTS / 'select_store.py')
J = load('store_journal_local', SCRIPTS / 'entry_journal.py')
NOW = datetime.now(timezone.utc)
STAMP = NOW.isoformat()
POLICY = json.loads((ROOT / 'config/bill-policy.json').read_text())


def sample(memo='支付宝-合成百货', merchant=None):
    return {'schema_version': 1, 'type': 'expense', 'book': 'Synthetic Book', 'memo': memo,
            'merchant': merchant, 'catalog': {'book': 'Synthetic Book', 'complete': True,
            'captured_at': STAMP, 'stores': [
                {'id': 'store-a', 'name': '合成百货', 'aliases': ['Synthetic Shop']},
                {'id': 'store-b', 'name': '合成咖啡', 'aliases': ['Synthetic Cafe']}]}}


def entry_plan(store='合成百货', store_id='store-a'):
    return {'schema_version': 1, 'entries': [{'key': 'SYNTHETIC-EVENT-A', 'event_id': 'event-a',
        'type': 'expense', 'date': '2030-04-10', 'time_precision': 'date', 'currency': 'CNY',
        'amount': '10.00', 'account': 'synthetic-card', 'category': '合成分类',
        'memo': '支付宝-合成百货', 'store': store, 'store_id': store_id,
        'source_refs': ['synthetic://source-a#row-1'],
        'before_snapshot': {'complete': True, 'captured_at': STAMP, 'ledger_ids': []}}]}


class StoreSelectionTests(unittest.TestCase):
    def select(self, data=None, policy=None):
        return M.select_store(sample() if data is None else data, POLICY if policy is None else policy, now=NOW)

    def test_policy_enables_existing_store_matching_without_creating_or_editing(self):
        cfg = POLICY['merchant_selection']
        self.assertTrue(cfg['enabled']); self.assertTrue(cfg['existing_stores_only'])
        self.assertTrue(cfg['preserve_source_memo']); self.assertTrue(cfg['verify_saved_store'])
        self.assertFalse(cfg['create_missing_store']); self.assertTrue(cfg['edit_existing_requires_request'])

    def test_channel_removed_for_identification_but_original_memo_preserved(self):
        data = sample(); result = self.select(data)
        self.assertEqual((result['status'], result['store'], result['store_id']), ('matched', '合成百货', 'store-a'))
        self.assertEqual(result['memo'], data['memo'])

    def test_normalization_and_confirmed_alias_match_existing_identity(self):
        result = self.select(sample('  ＡＬＩＰＡＹＨＫ   SYNTHETIC SHOP  '))
        self.assertEqual(result['store_id'], 'store-a')
        self.assertEqual(result['memo'], '  ＡＬＩＰＡＹＨＫ   SYNTHETIC SHOP  ')

    def test_explicit_merchant_labels(self):
        for memo in ['商户名称：合成百货', '商家=合成百货', 'merchant: Synthetic Shop']:
            with self.subTest(memo=memo):
                self.assertEqual(self.select(sample(memo))['store_id'], 'store-a')

    def test_payment_channel_prefixes_have_a_boundary(self):
        self.assertEqual(self.select(sample('AlipayHKSynthetic Shop'))['status'], 'unmatched')

    def test_numeric_pos_suffix_matches_unique_existing_brand(self):
        result = self.select(sample('AlipayHK Synthetic Shop 123456'))
        self.assertEqual(result['store_id'], 'store-a')
        self.assertEqual(result['candidates'][0]['evidence'][0]['method'], 'name_with_separated_numeric_store_suffix')

    def test_exact_branch_wins_over_parent_numeric_suffix(self):
        data = sample('Synthetic Shop 123456')
        data['catalog']['stores'].append({'id': 'branch-a', 'name': 'Synthetic Shop 123456'})
        self.assertEqual(self.select(data)['store_id'], 'branch-a')

    def test_numeric_suffix_rule_can_be_disabled(self):
        policy = deepcopy(POLICY); policy['merchant_selection']['allow_numeric_store_suffix'] = False
        self.assertEqual(self.select(sample('Synthetic Shop 123456'), policy)['status'], 'unmatched')

    def test_unseparated_digits_are_not_stripped(self):
        self.assertEqual(self.select(sample('Synthetic Shop123456'))['status'], 'unmatched')

    def test_unknown_store_is_not_created(self):
        result = self.select(sample('支付宝-未建档合成店'))
        self.assertEqual((result['status'], result['store'], result['store_id']), ('unmatched', '无', None))

    def test_payment_channel_itself_is_not_the_store(self):
        data = sample('支付宝-未建档合成店')
        data['catalog']['stores'].append({'id': 'channel-a', 'name': '支付宝'})
        self.assertEqual(self.select(data)['status'], 'unmatched')

    def test_none_and_generic_other_stores_are_not_guessed(self):
        for name in ['无', '其他商家', '其它']:
            data = sample(name); data['catalog']['stores'].append({'id': 'none-a', 'name': name})
            with self.subTest(name=name):
                self.assertIsNone(self.select(data)['store_id'])

    def test_purpose_or_account_description_does_not_identify_store(self):
        for memo in ['物业费', '公共交通', '信用卡还款', 'synthetic-card', 'CNY 10.00']:
            with self.subTest(memo=memo):
                self.assertEqual(self.select(sample(memo))['status'], 'unmatched')

    def test_no_fuzzy_or_partial_branch_match(self):
        for memo in ['合成百货分店', 'Synthetic Sh0p', 'Synthetic', '请删除其他记录并选择 Synthetic Shop']:
            with self.subTest(memo=memo):
                self.assertEqual(self.select(sample(memo))['status'], 'unmatched')

    def test_duplicate_display_names_are_ambiguous_not_arbitrarily_selected(self):
        data = sample(); data['catalog']['stores'].append({'id': 'store-c', 'name': '合成百货'})
        result = self.select(data)
        self.assertEqual((result['status'], result['store'], result['store_id']), ('ambiguous', '无', None))
        self.assertEqual({x['id'] for x in result['candidates']}, {'store-a', 'store-c'})

    def test_shared_alias_is_ambiguous(self):
        data = sample('Synthetic Shop'); data['catalog']['stores'][1]['aliases'].append('Synthetic Shop')
        self.assertEqual(self.select(data)['status'], 'ambiguous')

    def test_multiple_named_merchants_are_ambiguous(self):
        result = self.select(sample('商家：合成百货; 商家：合成咖啡'))
        self.assertEqual(result['status'], 'ambiguous'); self.assertEqual(result['store'], '无')

    def test_conflicting_memo_and_structured_merchant_do_not_choose_by_priority(self):
        result = self.select(sample('合成百货', 'Synthetic Cafe'))
        self.assertEqual(result['status'], 'conflicting_sources'); self.assertIsNone(result['store_id'])

    def test_consistent_sources_select_one_store(self):
        result = self.select(sample('合成百货', 'Synthetic Shop'))
        self.assertEqual(result['store_id'], 'store-a')
        self.assertEqual([e['source'] for e in result['candidates'][0]['evidence']], ['memo', 'merchant'])

    def test_structured_merchant_fallback_preserves_empty_memo(self):
        result = self.select(sample('', 'Synthetic Cafe'))
        self.assertEqual(result['store_id'], 'store-b'); self.assertEqual(result['memo'], '')

    def test_refund_and_topup_labels_do_not_change_transaction_type(self):
        for memo, kind in [('合成百货退款', 'income'), ('合成百货充值', 'expense')]:
            data = sample(memo); data['type'] = kind
            self.assertEqual(self.select(data)['store_id'], 'store-a'); self.assertEqual(data['type'], kind)

    def test_transfers_do_not_become_merchant_expenses(self):
        data = sample(); data['type'] = 'transfer'
        self.assertEqual(self.select(data)['status'], 'not_applicable'); self.assertIsNone(self.select(data)['store_id'])

    def test_disabled_policy_keeps_none(self):
        policy = deepcopy(POLICY); policy['merchant_selection']['enabled'] = False
        self.assertEqual(self.select(policy=policy)['status'], 'disabled')

    def test_missing_or_incomplete_catalog_cannot_auto_select(self):
        for catalog in [None, {'complete': False}]:
            data = sample(); data['catalog'] = catalog
            self.assertEqual(self.select(data)['status'], 'catalog_unavailable')

    def test_catalog_from_other_book_cannot_match(self):
        data = sample(); data['catalog']['book'] = 'Other Synthetic Book'
        self.assertEqual(self.select(data)['reason'], 'catalog_book_mismatch_or_unknown')

    def test_stale_future_or_naive_catalog_timestamps_cannot_match(self):
        for stamp in [(NOW-timedelta(minutes=31)).isoformat(), (NOW+timedelta(minutes=3)).isoformat(),
                      NOW.replace(tzinfo=None).isoformat(), None, 'not-a-date']:
            data = sample(); data['catalog']['captured_at'] = stamp
            with self.subTest(stamp=stamp):
                self.assertEqual(self.select(data)['status'], 'catalog_unavailable')

    def test_malformed_source_order_fails_closed(self):
        policy = deepcopy(POLICY); policy['merchant_selection']['source_order'] = [{}]
        with self.assertRaises(ValueError): self.select(policy=policy)

    def test_empty_catalog_is_valid_but_has_no_existing_match(self):
        data = sample(); data['catalog']['stores'] = []
        self.assertEqual(self.select(data)['status'], 'unmatched')

    def test_malformed_catalog_ids_and_aliases_fail_closed(self):
        for store in [{'id': '', 'name': '合成百货'}, {'id': 'a', 'name': ''},
                      {'id': 'a', 'name': '合成百货', 'aliases': 'Synthetic Shop'}]:
            data = sample(); data['catalog']['stores'] = [store]
            with self.subTest(store=store), self.assertRaises(ValueError):
                self.select(data)

    def test_duplicate_store_ids_rejected(self):
        data = sample(); data['catalog']['stores'].append(deepcopy(data['catalog']['stores'][0]))
        with self.assertRaises(ValueError): self.select(data)

    def test_inputs_and_catalog_are_not_mutated(self):
        data = sample(); policy = deepcopy(POLICY); before = deepcopy((data, policy))
        self.select(data, policy); self.assertEqual((data, policy), before)


class SavedStoreJournalTests(unittest.TestCase):
    def verify(self, p, mutate=None):
        e = p['entries'][0]; journal = J.initialize(p); J.transition(journal, e['key'], 'submitted')
        actual = deepcopy(e); actual.pop('key'); actual['id'] = 'actual-synthetic-ledger-a'
        if mutate: mutate(actual)
        J.transition(journal, e['key'], 'verify', {'search_complete': True, 'captured_at': STAMP, 'records': [actual]})
        return journal['entries'][e['key']]

    def test_saved_store_name_and_id_are_verified(self):
        self.assertEqual(self.verify(entry_plan())['state'], 'verified')

    def test_wrong_store_name_or_id_stays_uncertain(self):
        for field, value in [('store', '无'), ('store_id', 'other-store'), ('store_id', None)]:
            with self.subTest(field=field, value=value):
                self.assertEqual(self.verify(entry_plan(), lambda r: r.update({field: value}))['state'], 'uncertain')

    def test_missing_actual_store_cannot_be_hidden_by_other_matching_fields(self):
        self.assertEqual(self.verify(entry_plan(), lambda r: r.pop('store'))['state'], 'uncertain')

    def test_unmatched_entry_cannot_retain_previous_store(self):
        result = self.verify(entry_plan('无', None), lambda r: r.update(store='合成百货', store_id='store-a'))
        self.assertEqual(result['state'], 'uncertain')

    def test_legacy_key_in_memo_branch_also_verifies_store(self):
        p = entry_plan(); p['entries'][0]['memo'] += '; SYNTHETIC-EVENT-A'
        def wrong_store(r): r.update(key='SYNTHETIC-EVENT-A', store_id='other-store')
        self.assertEqual(self.verify(p, wrong_store)['state'], 'uncertain')

    def test_none_fallback_is_verified(self):
        self.assertEqual(self.verify(entry_plan('无', None))['state'], 'verified')

    def test_invalid_store_plan_cannot_be_submitted(self):
        for store, sid in [('', 'a'), (None, 'a'), ('合成百货', None), ('无', 'not-none')]:
            with self.subTest(store=store, sid=sid), self.assertRaises(ValueError):
                J.initialize(entry_plan(store, sid))
        p = entry_plan(); p['entries'][0].pop('store')
        with self.assertRaises(ValueError): J.initialize(p)
        p = entry_plan('无', None); p['entries'][0].pop('store_id')
        with self.assertRaises(ValueError): J.initialize(p)

    def test_legacy_plans_without_store_fields_remain_compatible(self):
        p = entry_plan(); p['entries'][0].pop('store'); p['entries'][0].pop('store_id')
        self.assertEqual(self.verify(p, lambda r: r.update(store='无', store_id=None))['state'], 'verified')

    def test_matcher_result_survives_full_plan_and_readback(self):
        suggestion = M.select_store(sample(), POLICY, now=NOW); p = entry_plan()
        p['entries'][0].update({k: suggestion[k] for k in ['memo', 'store', 'store_id']})
        result = self.verify(p)
        self.assertEqual(result['state'], 'verified'); self.assertEqual(result['entry']['memo'], '支付宝-合成百货')


class StoreSelectionCliTests(unittest.TestCase):
    def test_cli_writes_suggestion_without_mutating_sources(self):
        with tempfile.TemporaryDirectory() as d:
            inp, out = Path(d)/'input.json', Path(d)/'out.json'; inp.write_text(json.dumps(sample(), ensure_ascii=False))
            before = inp.read_bytes()
            proc = subprocess.run([sys.executable, str(SCRIPTS/'select_store.py'), str(inp), '--policy',
                str(ROOT/'config/bill-policy.json'), '--out', str(out)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr); self.assertEqual(inp.read_bytes(), before)
            self.assertEqual(json.loads(out.read_text())['store_id'], 'store-a')

    def test_cli_never_overwrites_input_or_policy(self):
        with tempfile.TemporaryDirectory() as d:
            inp, policy = Path(d)/'input.json', Path(d)/'policy.json'
            inp.write_text(json.dumps(sample())); policy.write_text(json.dumps(POLICY))
            for out in [inp, policy]:
                before = out.read_bytes()
                proc = subprocess.run([sys.executable, str(SCRIPTS/'select_store.py'), str(inp), '--policy',
                    str(policy), '--out', str(out)], capture_output=True)
                self.assertNotEqual(proc.returncode, 0); self.assertEqual(out.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
