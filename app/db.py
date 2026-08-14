import os
import json
import logging

import psycopg2
from psycopg2.extras import RealDictCursor

from app.config import MAX_SKIPPED_RECORDS_PER_COUNTRY

logger = logging.getLogger(__name__)


class CountrySkipThresholdReached(BaseException):
    """
    Control-flow signal to abort crawling a country whose feed is mostly
    already-ingested documents.

    Inherits from BaseException so it is not swallowed by generic
    `except Exception` blocks inside crawler loops.
    """

    def __init__(self, country_id, skipped_count, threshold):
        self.country_id = country_id
        self.skipped_count = skipped_count
        self.threshold = threshold
        super().__init__(
            f"Country {country_id} skipped {skipped_count} existing records "
            f"(threshold: {threshold})."
        )


_country_skip_counts = {}


def reset_country_skip_counter(country_id):
    """Reset duplicate-skip counter for a country at the start of processing."""
    if country_id is not None:
        _country_skip_counts[country_id] = 0


def clear_country_skip_counter(country_id):
    """Clear duplicate-skip counter for a country after processing."""
    if country_id is not None:
        _country_skip_counts.pop(country_id, None)


def _register_skipped_existing_record(country_id):
    """Track skipped existing records and stop the country when threshold is exceeded."""
    if country_id is None or MAX_SKIPPED_RECORDS_PER_COUNTRY <= 0:
        return

    count = _country_skip_counts.get(country_id, 0) + 1
    _country_skip_counts[country_id] = count

    if count == MAX_SKIPPED_RECORDS_PER_COUNTRY:
        logger.warning(
            f"Country {country_id} has skipped {count} existing records. "
            "Next duplicate will stop this country early."
        )
    elif count > MAX_SKIPPED_RECORDS_PER_COUNTRY:
        raise CountrySkipThresholdReached(
            country_id=country_id,
            skipped_count=count,
            threshold=MAX_SKIPPED_RECORDS_PER_COUNTRY,
        )


def get_db_connection():
    try:
        return psycopg2.connect(
            host=os.getenv('POSTGRES_HOST'),
            port=os.getenv('POSTGRES_PORT'),
            database=os.getenv('POSTGRES_DB'),
            user=os.getenv('POSTGRES_USER'),
            password=os.getenv('POSTGRES_PASSWORD'),
        )
    except Exception as e:
        logger.error(f"Error connecting to database: {e}")
        raise


def init_db():
    """Apply schema/schema.sql. Safe to run on every startup (all statements are idempotent)."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            schema_path = os.path.join(os.getcwd(), 'schema', 'schema.sql')
            if os.path.exists(schema_path):
                with open(schema_path, 'r') as f:
                    logger.info("Applying schema...")
                    cur.execute(f.read())
                    conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Error initializing database: {e}")
        raise
    finally:
        conn.close()


def get_or_create_country(name, code):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM source.country WHERE name = %s", (name,))
            row = cur.fetchone()
            if row:
                return row[0]

            logger.info(f"Inserting country: {name} ({code})")
            cur.execute(
                "INSERT INTO source.country (name, code) VALUES (%s, %s) RETURNING id",
                (name, code),
            )
            country_id = cur.fetchone()[0]
            conn.commit()
            return country_id
    except Exception as e:
        conn.rollback()
        logger.error(f"Error getting/creating country {name}: {e}")
        raise
    finally:
        conn.close()


def check_record_exists_by_url(country_id, document_url):
    """
    URL-based duplicate check within a country.

    document_url is a single URL string; membership is checked against every
    row's document_url array (a row may hold more than one related URL, e.g.
    PAR + PIL + SPC for the same product) via ANY(). Registers a skip on hit.
    """
    if not document_url:
        return False
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM source.drug_predicate_raw_records
                WHERE country_id = %s AND %s = ANY(document_url)
                LIMIT 1
                """,
                (country_id, document_url),
            )
            exists = cur.fetchone() is not None
            if exists:
                _register_skipped_existing_record(country_id)
            return exists
    except Exception as e:
        logger.error(f"Error checking record existence for country {country_id}: {e}")
        return False
    finally:
        conn.close()


def check_record_exists_by_source_url(country_id, source_url):
    """
    Dedup check against the ORIGINAL source document URL (e.g. an MHRA blob
    URL) — NOT the `document_url` column. Crawlers that store their own S3
    key in `document_url` (instead of the source URL) can't use that column
    to answer "have I already crawled this source document", since the S3
    key doesn't exist yet until after the (possibly skippable) download runs.
    Each document's original source URL is expected to be preserved at
    json_data.documents[i].source_url; this checks membership there via a
    JSONB array scan. Registers a skip on hit.
    """
    if not source_url:
        return False
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM source.drug_predicate_raw_records
                WHERE country_id = %s
                  AND EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements(COALESCE(json_data->'documents', '[]'::jsonb)) doc
                      WHERE doc->>'source_url' = %s
                  )
                LIMIT 1
                """,
                (country_id, source_url),
            )
            exists = cur.fetchone() is not None
            if exists:
                _register_skipped_existing_record(country_id)
            return exists
    except Exception as e:
        logger.error(f"Error checking record existence by source_url for country {country_id}: {e}")
        return False
    finally:
        conn.close()


def check_record_exists_by_json_field(country_id, field, value):
    """
    Dedup check against a top-level scalar field inside json_data.

    For registry-style sources with no attached documents (so no
    document_url/source_url to key off) and no reliably-unique product
    name (many distinct registrations can share a display name), the
    registry's own identifier — stored as a plain json_data field, e.g.
    SAHPRA's `application_no` — is the right dedup key. Generic and
    reusable by any future country crawler in the same situation.
    """
    if not value:
        return False
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM source.drug_predicate_raw_records
                WHERE country_id = %s AND json_data->>%s = %s
                LIMIT 1
                """,
                (country_id, field, str(value)),
            )
            exists = cur.fetchone() is not None
            if exists:
                _register_skipped_existing_record(country_id)
            return exists
    except Exception as e:
        logger.error(f"Error checking record existence by json field '{field}' for country {country_id}: {e}")
        return False
    finally:
        conn.close()


def check_record_exists_by_name(country_id, name):
    """Secondary duplicate check by name, for sources with no stable document_url."""
    if not name:
        return False
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM source.drug_predicate_raw_records
                WHERE country_id = %s AND name = %s
                LIMIT 1
                """,
                (country_id, name),
            )
            exists = cur.fetchone() is not None
            if exists:
                _register_skipped_existing_record(country_id)
            return exists
    except Exception as e:
        logger.error(f"Error checking record existence by name for country {country_id}: {e}")
        return False
    finally:
        conn.close()


def save_drug_record(name, country_id, document_url, json_data=None):
    """
    Insert or update a raw drug-predicate record.

    `document_url` is stored as a TEXT[] — pass either a single URL string
    (wrapped into a 1-element list) or a list of URLs when a record bundles
    several related documents (e.g. PAR + PIL + SPC for the same product).

    Upserts on (country_id, document_url) so re-crawling the same exact set
    of URLs refreshes name/json_data instead of creating a duplicate row.
    Records with no document_url (NULL/empty) are never deduplicated by
    Postgres's unique constraint semantics — callers without a stable URL
    should dedupe via check_record_exists_by_name() before calling this.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if name and len(name) > 255:
                logger.warning(f"Truncating name for country {country_id} (length {len(name)}): {name[:100]}...")
                name = name[:255]

            if document_url is None:
                document_urls = []
            elif isinstance(document_url, (list, tuple, set)):
                document_urls = [u for u in document_url if u]
            else:
                document_urls = [document_url] if document_url else []

            if json_data is not None and isinstance(json_data, str):
                try:
                    json_data = json.loads(json_data)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON string for country {country_id}, storing as null")
                    json_data = None

            if document_urls:
                query = """
                    INSERT INTO source.drug_predicate_raw_records
                        (name, country_id, document_url, json_data)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (country_id, document_url)
                    DO UPDATE SET
                        name = EXCLUDED.name,
                        json_data = EXCLUDED.json_data,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING id;
                """
            else:
                query = """
                    INSERT INTO source.drug_predicate_raw_records
                        (name, country_id, document_url, json_data)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id;
                """
            cur.execute(query, (name, country_id, document_urls or None, json.dumps(json_data) if json_data else None))
            record_id = cur.fetchone()[0]
            conn.commit()
            return record_id
    except Exception as e:
        conn.rollback()
        logger.error(f"Error saving drug predicate record: {e}")
        raise
    finally:
        conn.close()


def log_crawl_start(country_id):
    """
    Insert a drug_predicate_crawl_log row when a country starts crawling.

    Returns the new log id (used later by log_crawl_finish), or None if
    logging fails — logging must never break a crawl.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO source.drug_predicate_crawl_log (country_id, status, started_at)
                VALUES (%s, 'START', CURRENT_TIMESTAMP)
                RETURNING id
                """,
                (country_id,),
            )
            log_id = cur.fetchone()[0]
            conn.commit()
            return log_id
    except Exception as e:
        logger.error(f"Failed to log crawl start for country {country_id}: {e}")
        return None
    finally:
        if conn:
            conn.close()


def log_crawl_finish(log_id, status='DONE', detail=None):
    """status: DONE | FAILED | SKIPPED. No-op if log_id is None."""
    if log_id is None:
        return
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE source.drug_predicate_crawl_log
                SET status = %s, finished_at = CURRENT_TIMESTAMP, detail = %s
                WHERE id = %s
                """,
                (status, detail, log_id),
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to log crawl finish for crawl_log id {log_id}: {e}")
    finally:
        if conn:
            conn.close()
