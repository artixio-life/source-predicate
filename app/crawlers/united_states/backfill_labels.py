"""
One-off maintenance: backfill json_data.spl_labels onto EXISTING
source.drug_predicate_raw_records rows for the United States, without
re-crawling anything.

Why this exists
----------------
By the time `UnitedStatesFDACrawler.prefetch_labels` (batched, correctly
type-prefixed openFDA label.json lookups) landed, a full ~29k-application
US crawl had already run under earlier versions of the crawler that either
never fetched labels at all, or fetched them one-per-application with a
since-fixed bug that made every lookup return empty regardless of whether
a real label existed (see crawler_us_1.py's module docstring). None of
that is a reason to re-crawl from scratch: application/product/submission
data and every downloaded PDF are already correct and untouched by the
label fix — only `json_data.spl_labels` needs recomputing. Re-crawling
would re-fetch drugsfda.json/the TSV and re-download every PDF for no
reason, taking hours; this script instead:

  1. Reads every existing US row's `application_number` and
     `application_type_code` straight out of its OWN already-stored
     json_data — no fresh crawl, no re-parsing drugsfda.json/the TSV.
  2. Runs them through the same batched `iter_label_batches()` the crawler
     itself now uses (~100 requests for ~29k applications, not ~29k) —
     consumed as a GENERATOR here, one batch at a time, not accumulated
     into one big dict first (see below).
  3. UPDATEs ONLY the `json_data.spl_labels` key on each row, via
     Postgres's jsonb `||` merge operator — every other field already in
     json_data (products, documents, approval_history, ...) and the row's
     `document_url`/`name` are left completely untouched.

Memory: confirmed live that a full ~29k-application run holding every
application's full label TEXT (indications, warnings, dosage, adverse
reactions — often tens of KB each) in memory at once — i.e. calling the
crawler's accumulating `prefetch_labels()` instead — is enough to threaten
OOM in a memory-constrained container. This script instead consumes
`iter_label_batches()` directly and writes each batch's rows immediately,
so peak memory stays bounded to one batch (`FDA_LABEL_BATCH_SIZE`
applications' worth of labels) rather than the whole run.

Every row's application_number is reprocessed unconditionally, not just
rows missing `spl_labels` — a present-but-empty `spl_labels: []` from a
run that predates the prefix fix is not trustworthy evidence that no
label exists (see crawler_us_1.py's fix commit), so it must be recomputed
too, not skipped. Rows added after the fix that legitimately have zero
labels (confirmed live for old/discontinued applications like RAXAR,
DECLOMYCIN, STARLIX — see prior investigation) simply get `spl_labels: []`
written again — a harmless no-op re-confirmation, not a regression.

Usage
-----
Set FDA_BACKFILL_LABELS=true (checked at the top of app/main.py, which
runs this INSTEAD of the normal crawl loop when set) and run once:

    FDA_BACKFILL_LABELS=true python -m app.main

Preview first without writing anything:

    FDA_BACKFILL_LABELS=true FDA_BACKFILL_DRY_RUN=true python -m app.main

Unset FDA_BACKFILL_LABELS afterwards so subsequent runs go back to the
normal crawl.
"""
import logging
import os

from psycopg2.extras import Json, RealDictCursor

from app.db import get_db_connection, get_or_create_country
from app.crawlers.united_states import COUNTRY_NAME, COUNTRY_CODE
from app.crawlers.united_states.crawler_us_1 import UnitedStatesFDACrawler

logger = logging.getLogger(__name__)

DRY_RUN = os.getenv('FDA_BACKFILL_DRY_RUN', 'false').lower() == 'true'


def _fetch_existing_rows(country_id):
    """
    One row per already-ingested US application: its raw_records id, plus
    application_number/application_type_code read straight out of its own
    stored json_data (never re-derived from a fresh crawl).
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id,
                       json_data->>'application_number' AS appl_no,
                       json_data->>'application_type_code' AS application_type_code
                FROM source.drug_predicate_raw_records
                WHERE country_id = %s
                  AND json_data ? 'application_number'
                """,
                (country_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def _update_row_spl_labels(country_id, row_id, spl_labels):
    """
    Merge ONLY the spl_labels key into this row's json_data via Postgres's
    jsonb `||` operator — no read-modify-write round trip of the whole
    blob client-side, and every other field in json_data is untouched.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE source.drug_predicate_raw_records
                SET json_data = json_data || jsonb_build_object('spl_labels', %s::jsonb),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND country_id = %s
                """,
                (Json(spl_labels), row_id, country_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run():
    logger.info(f"[FDA backfill] Starting spl_labels backfill for existing US rows"
                f"{' (DRY RUN — no writes)' if DRY_RUN else ''}")

    country_id = get_or_create_country(COUNTRY_NAME, COUNTRY_CODE)
    rows = _fetch_existing_rows(country_id)
    logger.info(f"[FDA backfill] {len(rows)} existing US row(s) with an application_number")
    if not rows:
        return

    applications = {}
    row_ids_by_appl_no = {}
    no_type_code_appl_nos = set()
    for row in rows:
        appl_no = row['appl_no']
        if not appl_no:
            continue
        if not row.get('application_type_code'):
            no_type_code_appl_nos.add(appl_no)
        applications[appl_no] = {'application_type_code': row.get('application_type_code')}
        row_ids_by_appl_no.setdefault(appl_no, []).append(row['id'])

    if no_type_code_appl_nos:
        logger.warning(
            f"[FDA backfill] {len(no_type_code_appl_nos)} application(s) have no "
            f"application_type_code in their stored json_data — those can't be searched "
            f"against openFDA and get spl_labels=[] directly, without a lookup"
        )

    # Consume iter_label_batches directly (NOT the accumulating
    # prefetch_labels wrapper) and write each batch's rows immediately —
    # see the module docstring's "Memory" section for why this matters at
    # ~29k applications. Peak memory here is bounded to one batch
    # (FDA_LABEL_BATCH_SIZE applications' worth of labels), not the whole
    # run — each batch's dict is discarded once its rows are written.
    updated = 0
    failed = 0
    with_labels = 0
    checked = 0

    def _apply(appl_no, spl_labels):
        nonlocal updated, failed, with_labels
        if spl_labels:
            with_labels += 1
        if DRY_RUN:
            return
        for row_id in row_ids_by_appl_no.get(appl_no, ()):
            try:
                _update_row_spl_labels(country_id, row_id, spl_labels)
                updated += 1
            except Exception:
                failed += 1
                logger.exception(f"[FDA backfill] Failed to update row id={row_id} (appl_no={appl_no})")

    crawler = UnitedStatesFDACrawler()
    try:
        for batch_num, total_batches, batch_appl_nos, labels_this_batch in crawler.iter_label_batches(applications):
            for appl_no in batch_appl_nos:
                _apply(appl_no, labels_this_batch.get(appl_no, []))
            checked += len(batch_appl_nos)
            logger.info(f"[FDA backfill] Batch {batch_num}/{total_batches} written "
                        f"({checked}/{len(applications) - len(no_type_code_appl_nos)} application(s) so far)")
    finally:
        crawler.close()

    # These never appear in any batch — iter_label_batches can't search for
    # an application with no type code at all — so they're handled directly
    # here rather than being silently skipped, giving every row a
    # definitive spl_labels value.
    for appl_no in no_type_code_appl_nos:
        _apply(appl_no, [])
    checked += len(no_type_code_appl_nos)

    logger.info(
        f"[FDA backfill] Done{' (DRY RUN, nothing written)' if DRY_RUN else ''}: "
        f"{checked} application(s) checked, "
        f"{with_labels} resolved at least one spl_label, "
        f"{checked - with_labels} confirmed empty (no label on file for that application)"
        + (f" — {updated} row(s) updated, {failed} failed" if not DRY_RUN else "")
    )


if __name__ == '__main__':
    import logging as _logging

    _logging.basicConfig(
        level=_logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )
    run()
