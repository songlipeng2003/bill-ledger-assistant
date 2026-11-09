#!/usr/bin/env python3
"""Suggest an existing ledger store from source text and a fresh catalog; local JSON only."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import unicodedata


def require(value, message):
    if not value:
        raise ValueError(message)


def normalize(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    return re.sub(r'\s+', ' ', text.translate(str.maketrans({'—': '-', '–': '-'}))).strip()


def name_candidates(text, prefixes):
    """Remove only explicit channel/merchant/transaction wrappers, not arbitrary prose."""
    pieces = [text, *re.split(r'[;；\n|]', text)]
    pieces += re.findall(r'(?:商户(?:名称)?|商家|merchant)\s*[:：=]\s*([^;；\n|]+)', text, re.I)
    result = set()
    channels = sorted((normalize(p) for p in prefixes), key=len, reverse=True)
    for piece in pieces:
        value = normalize(piece)
        value = re.sub(r'^(?:商户(?:名称)?|商家|merchant)\s*[:=]\s*', '', value)
        for _ in range(len(channels)):
            prefix = next((p for p in channels if value.startswith(p)
                           and (len(value) == len(p) or value[len(p)] in ' -:/')), None)
            if prefix is None:
                break
            value = value[len(prefix):].lstrip(' -:/')
        if value:
            result.add(value)
            # A transaction label is not part of the store identity.
            activity = re.sub(r'\s*[-:]?\s*(?:退款|退货|充值)\s*$', '', value).strip()
            if activity:
                result.add(activity)
    return result


def select_store(data, policy, *, now=None):
    require(isinstance(data, dict) and data.get('schema_version') == 1, 'version-1 input required')
    require(isinstance(data.get('memo'), str), 'source memo string required; empty is allowed')
    require(data.get('merchant') is None or isinstance(data['merchant'], str), 'merchant must be text or null')
    require(isinstance(policy, dict) and isinstance(policy.get('merchant_selection'), dict),
            'project merchant_selection policy required')
    cfg = policy['merchant_selection']
    require(type(cfg.get('enabled')) is bool, 'merchant selection enabled must be boolean')
    result = {'schema_version': 1, 'status': 'unmatched', 'store': '无', 'store_id': None,
              'memo': data['memo'], 'candidates': [], 'reason': 'no_existing_store_match'}
    if not cfg['enabled']:
        return {**result, 'status': 'disabled', 'reason': 'merchant_selection_disabled'}
    if data.get('type') not in cfg.get('applies_to', ['expense', 'income']):
        return {**result, 'status': 'not_applicable', 'reason': 'transaction_type_has_no_automatic_store_selection'}
    catalog = data.get('catalog')
    if not isinstance(catalog, dict) or catalog.get('complete') is not True:
        return {**result, 'status': 'catalog_unavailable', 'reason': 'complete_live_catalog_required'}
    if not isinstance(data.get('book'), str) or not data['book'] or catalog.get('book') != data['book']:
        return {**result, 'status': 'catalog_unavailable', 'reason': 'catalog_book_mismatch_or_unknown'}
    limit = cfg.get('catalog_max_age_minutes', 30)
    require(type(limit) is int and 1 <= limit <= 1440, 'invalid catalog evidence age')
    try:
        captured = datetime.fromisoformat(str(catalog.get('captured_at')).replace('Z', '+00:00'))
        require(captured.tzinfo is not None, 'catalog timestamp requires timezone')
        age = ((now or datetime.now(timezone.utc)) - captured).total_seconds()
        fresh = -120 <= age <= limit * 60
    except (ValueError, TypeError, OverflowError):
        fresh = False
    if not fresh:
        return {**result, 'status': 'catalog_unavailable', 'reason': 'catalog_capture_stale_or_invalid'}
    stores = catalog.get('stores')
    require(isinstance(stores, list), 'catalog stores list required')
    prefixes = cfg.get('payment_channel_prefixes', [])
    require(isinstance(prefixes, list) and all(isinstance(p, str) and normalize(p) for p in prefixes),
            'nonempty payment channel prefixes required')
    ignored = {normalize(n) for n in ['无', '其他', '其它', '其他商家', '其它商家', *prefixes]}
    index, by_id = {}, {}
    for store in stores:
        require(isinstance(store, dict) and isinstance(store.get('id'), str) and store['id'].strip(),
                'actual nonempty store ID required')
        require(store['id'] not in by_id, 'duplicate store ID')
        require(isinstance(store.get('name'), str) and normalize(store['name']), 'store name required')
        aliases = store.get('aliases', [])
        require(isinstance(aliases, list) and all(isinstance(a, str) and normalize(a) for a in aliases),
                'store aliases must be verified nonempty strings')
        by_id[store['id']] = store
        if normalize(store['name']) in ignored:
            continue
        for name in [store['name'], *aliases]:
            normalized = normalize(name)
            if normalized not in ignored:
                index.setdefault(normalized, set()).add(store['id'])
    order = cfg.get('source_order', ['memo', 'merchant'])
    require(isinstance(order, list) and order and all(isinstance(s, str) and s in {'memo', 'merchant'} for s in order)
            and len(order) == len(set(order)), 'invalid merchant source order')
    hits_by_source, evidence = {}, {}
    for source in order:
        candidates = name_candidates(data.get(source) or '', prefixes)
        hits = {sid for name in candidates for sid in index.get(name, ())}
        method = 'exact_name_or_verified_alias'
        if not hits and cfg.get('allow_numeric_store_suffix') is True:
            base_names = {re.sub(r'[\s#-]+[0-9]{3,}$', '', c).strip() for c in candidates}
            hits = {sid for name in base_names for sid in index.get(name, ())}
            method = 'name_with_separated_numeric_store_suffix'
        hits_by_source[source] = hits
        for sid in hits:
            evidence.setdefault(sid, []).append({'source': source, 'method': method})
    ids = set().union(*hits_by_source.values())
    result['candidates'] = [{'id': sid, 'name': by_id[sid]['name'], 'evidence': evidence[sid]}
                            for sid in sorted(ids)]
    if len(ids) > 1:
        conflict = sum(bool(ids) for ids in hits_by_source.values()) > 1 and len(set(
            tuple(sorted(ids)) for ids in hits_by_source.values() if ids)) > 1
        return {**result, 'status': 'conflicting_sources' if conflict else 'ambiguous',
                'reason': 'multiple_existing_store_identities; keep_none_do_not_guess'}
    if ids:
        sid = next(iter(ids))
        return {**result, 'status': 'matched', 'store': by_id[sid]['name'], 'store_id': sid,
                'reason': 'unique_existing_store_identity'}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input'); parser.add_argument('--policy', required=True); parser.add_argument('--out', required=True)
    args = parser.parse_args()
    try:
        require(Path(args.out).resolve() not in {Path(args.input).resolve(), Path(args.policy).resolve()},
                'do not overwrite input or project policy')
        result = select_store(json.loads(Path(args.input).read_text()), json.loads(Path(args.policy).read_text()))
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    except (ValueError, TypeError, KeyError, OSError) as error:
        parser.exit(2, f'store selection stopped: {error}\n')


if __name__ == '__main__':
    main()
