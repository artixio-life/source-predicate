"""
One-off backfill: re-partitions existing UK MHRA rows by registration
number, and resolves registration numbers that file_name alone can't give.

Background
----------
crawler_uk_1.py used to store every document card on one
/product/?product= page into a single DB row, even though that page can
bundle documents from several distinct MHRA licences under one display
name (e.g. an originator's own PLGB authorisation plus unrelated PLPI
parallel-import licences that happen to share the product name). The
crawler itself now groups by registration number going forward (one row
per licence); this script retroactively fixes rows saved before that
change — no re-download, since every document here already has an
s3_path from the original crawl. This is a pure DB repartition.

Per existing UK row
--------------------
1. Resolve a registration number for every document:
     a. Regex on file_name (see crawler_uk_1._extract_registration_number)
        — works when file_name is a real display filename like
        "spc-doc_PLGB 00101-1041.pdf".
     b. Fallback: a live query against the same public Azure Search index
        crawler_uk_1's own fallback path already uses, matched by
        source_url == the index's metadata_storage_path, reading its
        `pl_number` field directly (confirmed live: always "PL" + a 0-2
        letter licence-type suffix + exactly 9 digits, e.g.
        "PLGB001011041" -> PLGB, 00101/1041 — unambiguous, no parsing
        guesswork). This recovers the number even when file_name is an
        opaque content ID (the "CON##########" rows) or a filename shared
        across several licences that plain regex can't disambiguate.
2. Group the row's documents by resolved registration number, sorted
   SPC > PIL > PAR within each group (same order the crawler already
   uses).
3. The group containing documents[0] (whatever was first stored) is
   updated in place on the existing row — this is the group any prior AI
   extraction already ran against, so its position/content never changes
   beyond gaining the new registration_number/licence_type fields.
4. Every OTHER group becomes a brand-new row, reusing its documents'
   existing s3_path values (already uploaded, never re-downloaded here).
5. Documents where no registration number resolves even via the live
   fallback stay together in one group under the original row, exactly
   like today — nothing is invented, nothing is dropped.

Usage
-----
    python -m app.crawlers.united_kingdom.backfill_registration_numbers [--apply] [--limit N]

Without --apply this only logs what would change — no DB writes. Pass
--apply to actually update/insert. --limit caps how many rows are
processed, for a small trial run first.
"""

from __future__ import annotations

import argparse
import logging
import re
from typing import Dict, List, Optional, Tuple

import requests

from app.db import get_db_connection, get_or_create_country, check_record_exists_by_json_field
from app.crawlers.united_kingdom import COUNTRY_NAME, COUNTRY_CODE
from app.crawlers.united_kingdom.crawler_uk_1 import (
    _DOC_TYPE_ORDER,
    _extract_registration_number,
    SEARCH_ENDPOINT,
    SEARCH_API_KEY,
    API_VERSION,
)

logger = logging.getLogger(__name__)

_PL_NUMBER_FIELD_RE = re.compile(r'^PL([A-Z]{0,2})(\d{5})(\d{4})$', re.IGNORECASE)


def _parse_pl_number_field(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Parses the search index's own `pl_number` field, e.g. "PLGB001011041"
    -> ("PLGB", "00101/1041"). Confirmed live: always this exact shape
    (prefix + 9 digits, 5+4), unlike file_name which varies in format.
    """
    if not raw:
        return None, None
    match = _PL_NUMBER_FIELD_RE.match(raw.strip())
    if not match:
        return None, None
    suffix, first, second = match.groups()
    return f'PL{suffix.upper()}', f'{first}/{second}'


class _LiveResolver:
    """
    Resolves a single document's registration number via the live MHRA
    search index when file_name-based regex fails, matched by source_url
    (== the index's metadata_storage_path). Caches one search per product
    name per run, since a row's documents usually share a product name.
    """

    def __init__(self):
        self._http = requests.Session()
        self._cache: Dict[str, List[dict]] = {}

    def _search(self, product_name: str) -> List[dict]:
        if product_name in self._cache:
            return self._cache[product_name]
        # Only a genuine response (even an empty one) gets cached. A
        # transient failure must NOT be memoized as "no results" — with a
        # large backfill run touching hundreds of rows, one network blip
        # would otherwise silently poison every later document that shares
        # this product_name for the rest of the run, permanently marking
        # them unresolvable with no retry.
        for attempt in range(3):
            try:
                resp = self._http.get(
                    SEARCH_ENDPOINT,
                    params={
                        'api-key': SEARCH_API_KEY,
                        'api-version': API_VERSION,
                        'search': product_name,
                        'searchMode': 'all',
                        '$top': 50,
                    },
                    timeout=30,
                )
                if resp.status_code == 200:
                    results = resp.json().get('value', [])
                    self._cache[product_name] = results
                    return results
                logger.warning(
                    f"Search index returned HTTP {resp.status_code} for {product_name!r} "
                    f"(attempt {attempt + 1}/3)"
                )
            except requests.RequestException as exc:
                logger.warning(f"Search index request failed for {product_name!r}: {exc} (attempt {attempt + 1}/3)")
        logger.warning(f"Giving up on live lookup for {product_name!r} after 3 attempts — not caching the failure")
        return []

    def resolve(self, product_name: Optional[str], source_url: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        if not product_name or not source_url:
            return None, None
        for doc in self._search(product_name):
            if doc.get('metadata_storage_path') == source_url:
                pl_numbers = doc.get('pl_number') or []
                if pl_numbers:
                    return _parse_pl_number_field(pl_numbers[0])
                return None, None
        return None, None

    def close(self):
        self._http.close()


def _name_for_group(docs: List[dict], fallback: str) -> str:
    for d in docs:
        if (d.get('doc_type') or '').upper() == 'SPC' and d.get('title'):
            return d['title'][:255]
    if docs and docs[0].get('title'):
        return docs[0]['title'][:255]
    return (fallback or 'MHRA Product')[:255]


def _resolve_and_group(
    documents: List[dict], product_name: Optional[str], resolver: _LiveResolver
) -> Tuple[List[Tuple[Optional[str], List[dict]]], int]:
    """
    Enriches each document with licence_type/registration_number (regex
    first, live index lookup as fallback) and groups them by the result,
    preserving first-seen group order — same semantics as
    crawler_uk_1._group_cards_by_registration. Because `documents` is
    walked in its original stored order, the group at index 0 of the
    returned list is always the one containing documents[0] — the
    document any prior AI extraction already ran against — so callers
    never need to re-locate it by identity.

    Returns (groups, live_resolved_count).
    """
    groups: Dict[Optional[str], List[dict]] = {}
    order: List[Optional[str]] = []
    live_resolved = 0
    for doc in documents:
        licence_type, reg_no = _extract_registration_number(doc.get('file_name'))
        if reg_no is None:
            licence_type, reg_no = resolver.resolve(product_name, doc.get('source_url'))
            if reg_no is not None:
                live_resolved += 1
        enriched = dict(doc, licence_type=licence_type, registration_number=reg_no)
        if reg_no not in groups:
            groups[reg_no] = []
            order.append(reg_no)
        groups[reg_no].append(enriched)
    return [(key, groups[key]) for key in order], live_resolved


def _sorted_group(docs: List[dict]) -> List[dict]:
    return sorted(docs, key=lambda d: _DOC_TYPE_ORDER.get((d.get('doc_type') or '').upper(), 99))


def _process_row(conn, country_id: int, resolver: _LiveResolver, row_id, name, json_data, apply: bool) -> dict:
    """
    Processes one row and returns stats: {live_resolved, split, new_rows,
    skipped_existing}. Raises on unexpected failure — callers should catch
    per-row so one malformed row doesn't abort the whole backfill.
    """
    stats = {'live_resolved': 0, 'split': False, 'new_rows': 0, 'skipped_existing': 0}

    documents = (json_data or {}).get('documents') or []
    if not documents:
        return stats

    product_name = (json_data or {}).get('product_name') or name

    grouped, live_resolved = _resolve_and_group(documents, product_name, resolver)
    stats['live_resolved'] = live_resolved

    # grouped[0] always contains documents[0] — see _resolve_and_group.
    first_doc_reg_no, primary_group = grouped[0]
    other_groups = grouped[1:]

    primary_docs = _sorted_group(primary_group)
    primary_name = _name_for_group(primary_docs, product_name)
    primary_s3_keys = [d['s3_path'] for d in primary_docs if d.get('s3_path')]

    logger.info(
        f"[backfill] row {row_id}: {len(documents)} doc(s) -> "
        f"{len(grouped)} group(s) (primary reg={first_doc_reg_no!r}, "
        f"{len(other_groups)} split off)"
    )

    # Split-off groups are inserted as their own new rows FIRST, and only
    # once every one of them is safely committed does the primary row get
    # trimmed down to just its own group. If this process dies between
    # commits (kill, OOM, deploy restart) with this ordering the worst case
    # is a harmless re-run — a group already inserted is skipped via
    # check_record_exists_by_json_field below, and the primary row (not yet
    # trimmed) still holds every document. Trimming the primary row FIRST
    # would risk losing a split-off group's data entirely if the process
    # died right after that commit but before its new row existed.
    for reg_no, docs in other_groups:
        docs_sorted = _sorted_group(docs)
        group_name = _name_for_group(docs_sorted, product_name)
        s3_keys = [d['s3_path'] for d in docs_sorted if d.get('s3_path')]

        if reg_no and check_record_exists_by_json_field(country_id, 'registration_number', reg_no):
            # See module docstring: this registration number already has a
            # row elsewhere (e.g. two /product/ pages both listing the same
            # parallel-import PDF). We don't insert a duplicate, but the
            # primary row's trim below still drops this group from ITS
            # json_data — so log loud enough to find these documents again
            # later if needed. The underlying S3 object is untouched either way.
            logger.warning(
                f"[backfill] row {row_id}: registration_number={reg_no!r} already exists on "
                f"another row — skipping insert, dropping this group from row {row_id}'s "
                f"json_data. Documents: "
                f"{[(d.get('file_name'), d.get('s3_path')) for d in docs_sorted]}"
            )
            stats['skipped_existing'] += 1
            continue

        group_json_data = {'product_name': product_name, 'documents': docs_sorted}
        if reg_no:
            group_json_data['registration_number'] = reg_no

        logger.info(
            f"[backfill] row {row_id}: {'would insert' if not apply else 'inserting'} "
            f"new row for reg={reg_no!r} name={group_name!r} ({len(docs_sorted)} doc(s))"
        )

        if apply:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO source.drug_predicate_raw_records
                        (name, country_id, document_url, json_data)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (group_name, country_id, s3_keys or None, _to_json(group_json_data)),
                )
            conn.commit()
        stats['new_rows'] += 1

    new_json_data = dict(json_data or {})
    new_json_data['documents'] = primary_docs
    if first_doc_reg_no:
        new_json_data['registration_number'] = first_doc_reg_no
    else:
        new_json_data.pop('registration_number', None)

    if apply:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE source.drug_predicate_raw_records
                SET name = %s, document_url = %s, json_data = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (primary_name, primary_s3_keys or None, _to_json(new_json_data), row_id),
            )
        conn.commit()

    stats['split'] = bool(other_groups)
    return stats


def run(apply: bool, limit: Optional[int]):
    country_id = get_or_create_country(COUNTRY_NAME, COUNTRY_CODE)
    resolver = _LiveResolver()
    conn = get_db_connection()

    rows_touched = 0
    rows_split = 0
    rows_failed = 0
    new_rows_inserted = 0
    docs_resolved_live = 0
    docs_skipped_existing = 0

    try:
        with conn.cursor() as cur:
            query = """
                SELECT id, name, json_data
                FROM source.drug_predicate_raw_records
                WHERE country_id = %s
                  AND jsonb_array_length(COALESCE(json_data->'documents', '[]'::jsonb)) > 0
                ORDER BY id
            """
            if limit:
                query += " LIMIT %s"
                cur.execute(query, (country_id, limit))
            else:
                cur.execute(query, (country_id,))
            rows = cur.fetchall()

        logger.info(f"[backfill] {len(rows)} UK row(s) to process (apply={apply})")

        for row_id, name, json_data in rows:
            try:
                stats = _process_row(conn, country_id, resolver, row_id, name, json_data, apply)
            except Exception:
                logger.exception(f"[backfill] row {row_id}: failed, rolling back and continuing")
                conn.rollback()
                rows_failed += 1
                continue

            rows_touched += 1
            docs_resolved_live += stats['live_resolved']
            new_rows_inserted += stats['new_rows']
            docs_skipped_existing += stats['skipped_existing']
            if stats['split']:
                rows_split += 1

    finally:
        resolver.close()
        conn.close()

    logger.info(
        f"[backfill] done. rows processed={rows_touched}, rows failed={rows_failed}, "
        f"rows split={rows_split}, new rows {'inserted' if apply else 'that would be inserted'}="
        f"{new_rows_inserted}, documents resolved via live index={docs_resolved_live}, "
        f"documents skipped (registration_number already exists elsewhere)={docs_skipped_existing}"
    )


def _to_json(obj) -> str:
    import json
    return json.dumps(obj)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Actually write changes (default: dry run, log only)')
    parser.add_argument('--limit', type=int, default=None, help='Process at most N rows (for a trial run)')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    run(apply=args.apply, limit=args.limit)
