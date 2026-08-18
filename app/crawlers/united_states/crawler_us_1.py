"""
Crawler for United States — Drugs@FDA, via three interchangeable sources

Why not scrape accessdata.fda.gov
---------------------------------
This crawler used to walk the Drugs@FDA web UI
(`accessdata.fda.gov/scripts/cder/daf/index.cfm`) — 27 browse-by-letter
pages plus one overview page per application, ~30k requests per run.
That host sits behind Akamai bot/abuse detection and is confirmed live to
be unusable from a datacenter IP:

  - From a residential/office IP, plain `requests` fetched all 27 letter
    pages with HTTP 200 and no delay needed.
  - From the production cluster's egress IP, the FIRST request of the run
    (letter "A", attempt 1, nothing preceding it) is already redirected to
    `/apology_objects/abuse-detection-apology.html`, served with a 404.

Because the block lands on request #1 with no prior traffic, it is not a
rate limit and not a cadence problem: pacing, jitter and backoff were all
tried and none of them help. Akamai is scoring the egress IP itself,
before it ever evaluates behaviour. Driving headless Chromium via
Playwright is strictly worse — confirmed live that Akamai detects and
blocks headless Chromium's automation fingerprint on its very first
request, even from an IP where plain `requests` passes cleanly.

The three sources, in fallback order
------------------------------------
FDA publishes this data three ways. All three are tried in turn, because
they fail independently; the first one that yields records wins.

  1. `SOURCE_OPENFDA_API` — api.fda.gov/drug/drugsfda.json
     The live JSON API. Freshest, and the only source that reflects
     same-day corrections. ~30 requests per run (see the skip cap below).

  2. `SOURCE_OPENFDA_ZIP` — download.open.fda.gov/.../drug-drugsfda-*.json.zip
     openFDA's own bulk export of the exact same records. ONE request,
     ~9 MB. Covers the case where the query API is degraded but the static
     download service is fine.

  3. `SOURCE_FDA_TSV` — www.fda.gov/media/89850/download
     FDA's raw "Drugs@FDA Data Files" — 12 tab-delimited files, ~6 MB.
     A different data pipeline AND, importantly, different infrastructure:
     openFDA runs on api-umbrella/ApacheTrafficServer while www.fda.gov is
     Akamai. Confirmed live that openFDA served requests normally from an
     IP that Akamai had locked out of www.fda.gov, so keeping this source
     last gives the chain genuine infrastructure diversity rather than
     three correlated failures.

  4. Failing all three, a stale cache (see BULK_CACHE_PATH) is used at any
     age rather than ingesting nothing.

The openFDA skip cap — why the API needs partitioning
-----------------------------------------------------
Confirmed live: `skip` is hard-capped at 25,000 (`skip=25000` returns 200,
`skip=25001` returns "Skip value must 25000 or less."), `limit` is capped
at 1,000, and there are 29,269 drugsfda records. So naive pagination
CANNOT enumerate the dataset — it stalls at ~85% and silently looks
complete. `_api_collect` therefore partitions by application-number prefix
(`application_number:NDA*` etc.), confirmed live to be disjoint and exactly
complete: 22,913 ANDA + 5,875 NDA + 481 BLA = 29,269. Any partition that
would itself exceed the cap is subdivided a digit at a time (ANDA0*, ANDA2*
… — confirmed live: 13,246 + 9,667 = 22,913), so the scheme keeps working
as ANDA grows past 25,000 on its own.

Source equivalence, measured
----------------------------
Diffed record-by-record against the 2026-08-14 release:

    applications   TSV 29,270   openFDA 29,269   (openFDA lacks only 206627)
    products       TSV 51,642   openFDA 51,634
    submissions    TSV 188,086  openFDA 188,004  (82 fewer, across 65 apps)
    document links TSV 80,824   openFDA 80,613

The raw submission totals look 5,462 apart; 5,380 of that is orphan rows
whose ApplNo has no Applications.txt entry, which openFDA drops for the
same reason this parser does (see the orphan warning in `_assemble`). The
reconciliation is exact: 188,086 - 188,004 = 82.

openFDA is also better shaped: `dosage_form` and `route` arrive split
(the TSV crams both into one `Form` field), `active_ingredients` is a list
of {name, strength} objects instead of parallel strings needing manual
pairing, statuses/TE codes/class descriptions arrive pre-decoded rather
than needing lookup joins, and it carries an `openfda` enrichment block
(UNII, RxCUI, pharmacologic class, SPL ids, NDC) on ~12.5k records that
has no TSV equivalent at all. `_parse_openfda` and `_assemble` normalise
both into ONE record shape, so `json_data` is consistent no matter which
source served the run.

TSV parsing gotchas (all confirmed live, and all load-bearing)
--------------------------------------------------------------
1. Encoding is cp1252, NOT UTF-8 — `Submissions.txt` and
   `ApplicationDocs.txt` both contain bytes (0x92 curly apostrophe, 0xa0
   nbsp) that raise UnicodeDecodeError under UTF-8.
2. The files contain literal, unescaped `"` characters inside data fields
   (5 in Products.txt, 198 in Submissions.txt) which are NOT csv quoting.
   They must be read with `quoting=csv.QUOTE_NONE`, otherwise the default
   QUOTE_MINIMAL swallows subsequent rows into one giant field.
3. Exactly one row of ApplicationDocs.txt has a stray extra tab, yielding
   9 fields against an 8-column header. `_fit_row` repairs it.
4. Join keys need stripping — `SubmissionType` is space-padded (`'SUPPL     '`).

Record mapping
--------------
One row per ApplNo: an application can bundle several product names, so
`name` joins every distinct one.

`document_url` holds OUR S3 keys, not FDA's URLs — see app.storage.upload_file
and the UK/Australia crawlers for the same convention. Each document's
original `drugsatfda_docs` URL is kept in `json_data.documents[i].source_url`.
`json_data.source_url` points at the human-readable overview page for
provenance, but that page is NOT fetched.

Documents & the "PDF only" storage rule
---------------------------------------
Only links that are actually PDFs are downloaded and mirrored to S3;
everything else stays as `source_url`-only metadata. Two guards matter
because the document host is the WAF'd `accessdata.fda.gov` regardless of
which metadata source won: URLs are upgraded to https (both openFDA and
the TSVs still list many as http), and a downloaded body must start with
`%PDF` before upload, so an Akamai block page is never mirrored to S3
under a .pdf key. If document downloading is blocked from your egress,
metadata ingestion still succeeds — set DOWNLOAD_DOCUMENTS=false to skip.

SPL label content — the actual label TEXT, not just a PDF link
-----------------------------------------------------------------
`json_data.documents`/`label_documents` above are just PDF/document LINKS
(from drugsfda's submissions, or the TSV's ApplicationDocs.txt) — metadata
about a file, not its content. `json_data.spl_labels` is different: it's
openFDA's `api.fda.gov/drug/label.json`, the actual Structured Product
Labeling TEXT (indications_and_usage, warnings, dosage_and_administration,
active_ingredient, inactive_ingredient, contraindications, and more, all
already split into clean fields) fetched per-application via
`_fetch_labels`. No skip-cap partitioning is needed here (unlike the bulk
drugsfda discovery above) because each lookup is scoped to one
application_number and never returns more than a handful of records.

`openfda.application_number` is confirmed live to be an ARRAY on the label
side: some labels (e.g. trametinib/Mekinist, NDA204114 + NDA217513) list
MORE THAN ONE application_number, because a later application reused an
earlier one's already-approved label verbatim. This does NOT create
duplicate rows: `_fetch_labels` is only ever called with one already-
deduped, genuinely distinct application_number from drugsfda.json (this
crawler never enumerates label.json globally), so the shared label is
fetched independently for each application it belongs to and attached
under that application's own row — two real FDA applications sharing one
label document, not one label duplicated.

Dedup is by `json_data.application_number`
------------------------------------------
FDA's own application number (e.g. "020892") is stable and unique — the
natural fit for `app.db.check_record_exists_by_json_field`, same pattern as
SAHPRA/TGA/ANVISA. `name` isn't usable (many distinct applications share a
brand/generic name), and `document_url` isn't populated for applications
with no PDFs.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import logging
import os
import random
import re
import time
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional
from urllib.parse import urlparse, quote

import requests

from app.db import (
    CountrySkipThresholdReached,
    check_record_exists_by_json_field,
    save_drug_record,
)
from app.storage import upload_file, build_document_key, content_type_for_ext
from app.utils.request_helper import download_with_retries
from app.config import MAX_RECORDS_PER_COUNTRY, DOWNLOAD_DOCUMENTS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

SOURCE_OPENFDA_API = 'openfda_api'
SOURCE_OPENFDA_ZIP = 'openfda_zip'
SOURCE_FDA_TSV = 'fda_tsv'

# Order matters — see "The three sources" in the module docstring. Override
# with e.g. FDA_SOURCES=openfda_zip,fda_tsv to skip the API.
_sources_env = os.getenv('FDA_SOURCES', '')
SOURCES = (
    [s.strip() for s in _sources_env.split(',') if s.strip()]
    if _sources_env
    else [SOURCE_OPENFDA_API, SOURCE_OPENFDA_ZIP, SOURCE_FDA_TSV]
)

API_URL = os.getenv('FDA_API_URL', 'https://api.fda.gov/drug/drugsfda.json')
# openFDA's SPL label content — the actual label TEXT (indications_and_usage,
# warnings, dosage_and_administration, active/inactive ingredients, etc.),
# not just a PDF link. Queried per-application-number (never bulk-crawled),
# so it never needs skip-cap partitioning — see _fetch_labels.
LABEL_URL = os.getenv('FDA_LABEL_API_URL', 'https://api.fda.gov/drug/label.json')
LABEL_FETCH_LIMIT = int(os.getenv('FDA_LABEL_FETCH_LIMIT', '100'))
OPENFDA_ZIP_URL = os.getenv(
    'FDA_OPENFDA_ZIP_URL',
    'https://download.open.fda.gov/drug/drugsfda/drug-drugsfda-0001-of-0001.json.zip',
)
BULK_ZIP_URL = os.getenv('FDA_BULK_ZIP_URL', 'https://www.fda.gov/media/89850/download')

OVERVIEW_URL = ('https://www.accessdata.fda.gov/scripts/cder/daf/'
                'index.cfm?event=overview.process&ApplNo={appl_no}')

# Confirmed live: limit caps at 1000, skip at 25000. Both are hard API
# limits, not conventions — see the docstring for why the skip cap forces
# prefix partitioning rather than plain pagination.
API_PAGE = 1000
API_SKIP_MAX = 25_000
API_PARTITIONS = ['NDA', 'ANDA', 'BLA']
# An optional key raises the daily quota (240/min either way). Unset is fine
# for one run/day: a full crawl is ~30 requests.
API_KEY = os.getenv('FDA_API_KEY', '')
API_DELAY_SECONDS = float(os.getenv('FDA_API_DELAY_SECONDS', '0.2'))

# Only network I/O left after metadata load is per-document PDF fetching, so
# this stays a plain shared-nothing thread pool.
WORKERS = int(os.getenv('FDA_WORKERS', '8'))

# cp1252, not UTF-8 — see "TSV parsing gotchas" above.
BULK_ENCODING = 'cp1252'

# www.fda.gov runs its own Akamai abuse detection, separate from
# accessdata.fda.gov's. Confirmed live: pulling the TSV zip ~4 times in
# quick succession from one IP trips it — every subsequent request 302s to
# /apology_objects/abuse-detection-apology.html (served as a 404) until a
# cooldown well over 20 minutes elapses, while openFDA kept serving the same
# IP throughout. Retried rather than treated as a terminal 404 — which is
# what `download_with_retries` would do, hence the dedicated fetch.
BULK_ATTEMPTS = int(os.getenv('FDA_BULK_ATTEMPTS', '5'))
BULK_BACKOFF_SECONDS = float(os.getenv('FDA_BULK_BACKOFF_SECONDS', '60'))
APOLOGY_MARKER = 'apology_objects'

# On-disk cache of the PARSED records, not the raw payload — the cache is
# then source-agnostic, so a run served by any of the three sources can be
# replayed by any later run. Measured: 101.7 MB of JSON -> 4.9 MB gzipped.
#
# It exists to keep repeated runs from re-fetching data FDA only refreshes
# periodically — confirmed live that a handful of rapid TSV re-downloads
# while iterating is enough to trip the abuse detection above and lock the
# egress IP out, a self-inflicted outage the cache removes. It also makes
# the crawler survive being blocked: if every source fails but a cached copy
# exists, the run proceeds on it at ANY age (logged loudly) rather than
# ingesting nothing. FDA's data moves slowly enough that a slightly stale
# dataset is far better than an empty one.
#
# cwd is /app in the container, so this lands at /app/.cache/fda — mount a
# volume there (see docker-compose.yml) to persist it across recreations.
BULK_CACHE_DIR = os.getenv('FDA_BULK_CACHE_DIR', os.path.join(os.getcwd(), '.cache', 'fda'))
BULK_CACHE_TTL_SECONDS = float(os.getenv('FDA_BULK_CACHE_TTL_SECONDS', str(24 * 3600)))
BULK_CACHE_PATH = os.path.join(BULK_CACHE_DIR, 'applications.json.gz')

# source.drug_predicate_raw_records.name is VARCHAR(255) (schema/schema.sql).
# Confirmed live: 35 of 29,270 applications bundle enough products that the
# joined name blows past that — the longest is 4,669 chars (the 20-odd
# OSMITROL presentations).
#
# This is NOT about avoiding an insert error: save_drug_record already
# truncates anything over 255 (app/db.py:276-278), so the rows would persist
# either way. It's about WHERE the cut lands — db.py slices mid-word and logs
# a warning per record, which for a known-benign case is both a worse name and
# 35 lines of noise every run. _build_name cuts on a whole-product boundary
# and says how many were elided; json_data.products always holds the full list.
NAME_MAX_LEN = 255

_APPL_PREFIX_RE = re.compile(r'^([A-Z]+)0*(\d+)$')


# ---------------------------------------------------------------------------
# Shared normalisation helpers — used by BOTH parsers so the record shape is
# identical whichever source served the run.
# ---------------------------------------------------------------------------

def _https(url: str) -> str:
    """Both sources still list many document links as http://."""
    return 'https://' + url[len('http://'):] if url.startswith('http://') else url


def _iso_date(value: str) -> Optional[str]:
    """
    Normalise the two date formats to YYYY-MM-DD.

    The TSVs use '1998-09-25 00:00:00', openFDA uses '19980925'.
    """
    value = (value or '').strip()
    if not value:
        return None
    if len(value) == 8 and value.isdigit():
        return f'{value[:4]}-{value[4:6]}-{value[6:]}'
    return value.split(' ')[0] or None


def _yes_no(value) -> Optional[bool]:
    """
    Normalise the two sources' boolean spellings to a real bool.

    The TSVs use '1'/'0' for ReferenceDrug/ReferenceStandard; openFDA uses
    'Yes'/'No' for the same fields. Left as None when absent so "unknown"
    stays distinguishable from "no".
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ('1', 'yes', 'true'):
        return True
    if text in ('0', 'no', 'false'):
        return False
    return None


def _split_form_route(form: str) -> tuple:
    """
    The TSVs pack both into one `Form` field ('SOLUTION;INTRAVESICAL');
    openFDA delivers them already split. Returns (dosage_form, route).
    """
    form = (form or '').strip()
    if not form:
        return None, None
    if ';' in form:
        dosage_form, route = form.split(';', 1)
        return dosage_form.strip() or None, route.strip() or None
    return form, None


def _split_appl_number(value: str) -> tuple:
    """
    openFDA prefixes the type onto the number ('NDA020892'); the TSVs keep
    them in separate columns. Returns (appl_no, type_code) with appl_no in
    the TSV's zero-padded 6-digit form so the two sources dedup identically.
    """
    value = (value or '').strip()
    m = _APPL_PREFIX_RE.match(value)
    if not m:
        return value, None
    type_code, digits = m.group(1), m.group(2)
    return digits.zfill(6), type_code


def _fit_row(values: List[str], width: int) -> List[str]:
    """
    Coerce a split row to exactly `width` fields.

    Confirmed live: one ApplicationDocs.txt row carries a stray extra tab,
    which shows up as an extra EMPTY column wedged before the trailing
    date. Dropping empties from the right (never the final value) repairs
    that row instead of discarding it. Short rows are padded so callers can
    index every column unconditionally.
    """
    if len(values) > width:
        values = list(values)
        i = len(values) - 2
        while len(values) > width and i >= 0:
            if values[i] == '':
                del values[i]
            i -= 1
        values = values[:width]
    if len(values) < width:
        values = list(values) + [''] * (width - len(values))
    return values


def _build_name(product_names: List[str], appl_no: str) -> str:
    """
    Join every distinct product name under an application, bounded by the
    name column's VARCHAR(255) (see NAME_MAX_LEN). Whole names are kept and
    the elided count is spelled out rather than cutting mid-word.
    """
    if not product_names:
        return f'FDA Application {appl_no}'

    joined = '; '.join(product_names)
    if len(joined) <= NAME_MAX_LEN:
        return joined

    kept: List[str] = []
    for product_name in product_names:
        candidate = kept + [product_name]
        remaining = len(product_names) - len(candidate)
        suffix = f' (+{remaining} more)' if remaining else ''
        if len('; '.join(candidate)) + len(suffix) > NAME_MAX_LEN:
            break
        kept = candidate

    if not kept:
        # A single product name longer than the column on its own.
        return product_names[0][:NAME_MAX_LEN]

    remaining = len(product_names) - len(kept)
    name = '; '.join(kept) + (f' (+{remaining} more)' if remaining else '')
    return name[:NAME_MAX_LEN]


def _read_tsv(zf: zipfile.ZipFile, filename: str) -> List[Dict[str, str]]:
    """
    Parse one tab-delimited member of the TSV bulk zip into dicts.

    QUOTE_NONE is mandatory (literal `"` chars in the data are not csv
    quoting), and every value is stripped since join keys such as
    SubmissionType are space-padded in the source files.
    """
    with zf.open(filename) as fh:
        text = io.TextIOWrapper(fh, encoding=BULK_ENCODING, errors='replace').read()

    reader = csv.reader(text.splitlines(), delimiter='\t', quoting=csv.QUOTE_NONE)
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        logger.warning(f"[FDA] bulk file {filename} is empty")
        return []

    rows = []
    for values in reader:
        if not any(v.strip() for v in values):
            continue
        fitted = _fit_row(values, len(header))
        rows.append({header[i]: fitted[i].strip() for i in range(len(header))})
    return rows


def _lookup(zf: zipfile.ZipFile, filename: str, key: str, value: str) -> Dict[str, str]:
    """Build a {code -> description} map from one of the *_Lookup.txt files."""
    return {
        row[key]: row[value]
        for row in _read_tsv(zf, filename)
        if row.get(key)
    }


class UnitedStatesFDACrawler:
    """Ingests Drugs@FDA from openFDA (API, then bulk) then FDA's TSV files."""

    def close(self):
        pass

    # ------------------------------------------------------------------
    # Top-level crawl
    # ------------------------------------------------------------------

    def process_country(self, country_id: int):
        applications = self._load_applications()
        if not applications:
            logger.error("[FDA] No applications available from any source — aborting.")
            return

        logger.info(f"[FDA] {len(applications)} unique application(s) to process")
        saved = self._process_applications_concurrently(country_id, applications)
        logger.info(f"FDA crawl finished. Saved/updated {saved} applications "
                    f"({len(applications)} discovered).")

    # ------------------------------------------------------------------
    # Source chain: fresh cache -> each source in turn -> stale cache
    # ------------------------------------------------------------------

    def _load_applications(self) -> Dict[str, dict]:
        cached, cached_age, cached_source = self._read_cache()
        if cached is not None and cached_age < BULK_CACHE_TTL_SECONDS:
            logger.info(
                f"[FDA] Using cached applications ({len(cached)} records from "
                f"{cached_source}, {cached_age / 3600:.1f}h old, TTL "
                f"{BULK_CACHE_TTL_SECONDS / 3600:.0f}h) — skipping fetch"
            )
            return cached
        if cached is not None:
            logger.info(f"[FDA] Cache is {cached_age / 3600:.1f}h old "
                        f"(TTL {BULK_CACHE_TTL_SECONDS / 3600:.0f}h) — refreshing")

        fetchers = {
            SOURCE_OPENFDA_API: self._fetch_openfda_api,
            SOURCE_OPENFDA_ZIP: self._fetch_openfda_zip,
            SOURCE_FDA_TSV: self._fetch_fda_tsv,
        }

        for source in SOURCES:
            fetcher = fetchers.get(source)
            if fetcher is None:
                logger.warning(f"[FDA] Unknown source {source!r} in FDA_SOURCES — skipping")
                continue

            logger.info(f"[FDA] Trying source: {source}")
            try:
                applications = fetcher()
            except Exception:
                logger.exception(f"[FDA] Source {source} raised — falling through")
                continue

            if applications:
                logger.info(f"[FDA] Source {source} yielded {len(applications)} application(s)")
                self._write_cache(applications, source)
                return applications

            logger.warning(f"[FDA] Source {source} yielded nothing — falling through")

        if cached is not None:
            logger.warning(
                f"[FDA] Every source failed — falling back to the STALE cache "
                f"({len(cached)} records from {cached_source}, "
                f"{cached_age / 3600:.1f}h old). Data may be out of date; the run "
                f"continues rather than ingesting nothing."
            )
            return cached

        logger.error(
            f"[FDA] Every source failed and no cache exists at {BULK_CACHE_PATH}. "
            f"See the warnings above; if they show the Akamai abuse-detection "
            f"page, that egress IP is in a cooldown (observed >20 min)."
        )
        return {}

    # ------------------------------------------------------------------
    # Cache (parsed records, gzipped JSON — source-agnostic)
    # ------------------------------------------------------------------

    def _read_cache(self) -> tuple:
        """Return (applications|None, age_seconds, source_label)."""
        try:
            with gzip.open(BULK_CACHE_PATH, 'rt', encoding='utf-8') as fh:
                payload = json.load(fh)
            age = time.time() - os.path.getmtime(BULK_CACHE_PATH)
        except (OSError, EOFError, json.JSONDecodeError, gzip.BadGzipFile):
            return None, 0.0, ''

        applications = payload.get('applications')
        if not isinstance(applications, dict) or not applications:
            logger.warning(f"[FDA] Ignoring unusable cache at {BULK_CACHE_PATH}")
            return None, 0.0, ''
        return applications, age, payload.get('source', 'unknown')

    def _write_cache(self, applications: Dict[str, dict], source: str):
        try:
            os.makedirs(BULK_CACHE_DIR, exist_ok=True)
            # Write-then-rename so a crash mid-write can't leave a truncated
            # file that later looks cache-worthy.
            tmp = BULK_CACHE_PATH + '.tmp'
            payload = {'source': source, 'applications': applications}
            with gzip.open(tmp, 'wt', encoding='utf-8') as fh:
                json.dump(payload, fh)
            os.replace(tmp, BULK_CACHE_PATH)
            size_mb = os.path.getsize(BULK_CACHE_PATH) / 1_048_576
            logger.info(f"[FDA] Cached {len(applications)} application(s) from "
                        f"{source} at {BULK_CACHE_PATH} ({size_mb:.1f} MB)")
        except OSError as exc:
            # A read-only or missing cache dir must not fail the crawl.
            logger.warning(f"[FDA] Could not write cache: {exc}")

    # ------------------------------------------------------------------
    # Source 1: the openFDA query API
    # ------------------------------------------------------------------

    def _api_get(self, search: str, limit: int, skip: int, base_url: str = API_URL) -> Optional[dict]:
        params = f'search={quote(search)}&limit={limit}&skip={skip}'
        if API_KEY:
            params += f'&api_key={quote(API_KEY)}'
        url = f'{base_url}?{params}'

        for attempt in range(3):
            if API_DELAY_SECONDS:
                time.sleep(API_DELAY_SECONDS)
            try:
                resp = requests.get(url, timeout=60)
            except requests.RequestException as exc:
                logger.warning(f"[FDA] openFDA API request failed ({search}, skip={skip}): {exc}")
                continue

            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    logger.warning(f"[FDA] openFDA API returned non-JSON for {search}")
                    continue

            # 404 is openFDA's "no matches", which is a legitimate answer for
            # a partition prefix that matches nothing (e.g. ANDA1*), or for an
            # application with no SPL label on file at all (common for older
            # approvals that predate the SPL requirement).
            if resp.status_code == 404:
                return {'results': [], 'meta': {'results': {'total': 0}}}

            if resp.status_code == 429:
                wait = 30 * (attempt + 1)
                logger.warning(f"[FDA] openFDA API rate-limited; waiting {wait}s")
                time.sleep(wait)
                continue

            logger.warning(f"[FDA] openFDA API returned HTTP {resp.status_code} "
                           f"for {search} (skip={skip})")
            return None
        return None

    def _fetch_labels(self, openfda_application_number: str) -> Optional[List[dict]]:
        """
        Fetch every SPL label record openFDA links to this application via
        `openfda.application_number` — an exact-match keyword field on the
        label side, confirmed live, but an ARRAY: some labels (e.g.
        trametinib/Mekinist) list MORE THAN ONE application_number, because a
        later application (a new indication, typically) reused an earlier
        one's already-approved label verbatim.

        `openfda_application_number` MUST be the type-PREFIXED form (e.g.
        "NDA020695", matching drugsfda.json's own native `application_number`
        field exactly) — NOT this crawler's internal prefix-stripped,
        zero-padded `appl_no` (e.g. "020695") used for dedup/storage
        elsewhere in this file. Confirmed live: querying with the bare form
        returns a 404 unconditionally, for every application, regardless of
        whether a real label exists — a real bug caught only by running
        _process_application end-to-end rather than unit-testing this method
        with a hand-typed, already-correct argument. See the caller in
        _process_application for how the prefixed form is reconstructed.

        Querying per-application-number here — never enumerating label.json
        globally — means a shared label is fetched once per application it
        belongs to and attached under EACH application's own row. That is
        correct, not a duplicate: the caller always passes one of
        drugsfda.json's own already-deduped, genuinely distinct
        application_number values (see _process_application's dedup check),
        so two applications sharing one label are two real FDA records that
        happen to reference the same document — both rows are supposed to
        carry a copy of it.

        Returns None (not []) on a real fetch failure, so callers can tell
        "we don't know" apart from "confirmed zero labels" (a 404 — see
        _api_get) rather than silently treating a failure as "no labels".
        """
        page = self._api_get(
            f'openfda.application_number:"{openfda_application_number}"',
            limit=LABEL_FETCH_LIMIT, skip=0, base_url=LABEL_URL,
        )
        if page is None:
            return None

        results = page.get('results') or []
        total = (page.get('meta', {}).get('results', {}) or {}).get('total', 0)
        if total > len(results):
            logger.warning(
                f"[FDA] label lookup for {openfda_application_number} truncated at {len(results)}/{total} "
                f"(LABEL_FETCH_LIMIT={LABEL_FETCH_LIMIT}) — raise it if this recurs"
            )
        return results

    def _api_collect(self, prefix: str, depth: int = 0) -> Optional[List[dict]]:
        """
        Collect every record whose application_number starts with `prefix`,
        subdividing when a partition would exceed the skip cap.

        Returns None if any page outright failed — a partial partition must
        not be mistaken for a complete one, or the run would silently ingest
        a fraction of the dataset and look successful.
        """
        search = f'application_number:{prefix}*'
        head = self._api_get(search, limit=1, skip=0)
        if head is None:
            return None

        total = (head.get('meta', {}).get('results', {}) or {}).get('total', 0)
        if not total:
            return []

        # Beyond the skip cap the tail is unreachable — split the partition.
        if total > API_SKIP_MAX + API_PAGE:
            if depth >= 3:
                logger.error(f"[FDA] Partition {prefix}* still has {total} records at "
                             f"max depth — cannot enumerate it within the skip cap")
                return None
            logger.info(f"[FDA] Partition {prefix}* has {total} records "
                        f"(> skip cap) — subdividing")
            collected: List[dict] = []
            for digit in '0123456789':
                part = self._api_collect(prefix + digit, depth + 1)
                if part is None:
                    return None
                collected.extend(part)
            return collected

        records: List[dict] = []
        skip = 0
        while skip < total:
            if skip > API_SKIP_MAX:
                logger.error(f"[FDA] Hit the skip cap on {prefix}* at {skip}/{total}")
                return None
            page = self._api_get(search, limit=API_PAGE, skip=skip)
            if page is None:
                return None
            batch = page.get('results') or []
            if not batch:
                break
            records.extend(batch)
            skip += API_PAGE

        if len(records) < total:
            logger.error(f"[FDA] Partition {prefix}* returned {len(records)} of "
                         f"{total} records — treating as incomplete")
            return None
        logger.info(f"[FDA] Partition {prefix}*: {len(records)} record(s)")
        return records

    def _fetch_openfda_api(self) -> Dict[str, dict]:
        records: List[dict] = []
        for prefix in API_PARTITIONS:
            part = self._api_collect(prefix)
            if part is None:
                # Incomplete data is worse than no data — it would look like
                # a successful run. Fall through to the next source instead.
                logger.warning(f"[FDA] Partition {prefix}* incomplete — abandoning the API source")
                return {}
            records.extend(part)
        return self._parse_openfda(records)

    # ------------------------------------------------------------------
    # Source 2: openFDA's bulk export of the same records
    # ------------------------------------------------------------------

    def _fetch_openfda_zip(self) -> Dict[str, dict]:
        content, _content_type, status = download_with_retries(
            OPENFDA_ZIP_URL, retries=3, timeout=180
        )
        if not content:
            logger.warning(f"[FDA] openFDA bulk download failed (HTTP {status})")
            return {}
        if not content.startswith(b'PK'):
            logger.warning(f"[FDA] openFDA bulk download is not a zip "
                           f"({len(content)} bytes, starts {content[:32]!r})")
            return {}

        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                names = [n for n in zf.namelist() if n.lower().endswith('.json')]
                if not names:
                    logger.warning("[FDA] openFDA bulk zip contains no .json member")
                    return {}
                with zf.open(names[0]) as fh:
                    payload = json.load(fh)
        except (zipfile.BadZipFile, ValueError) as exc:
            logger.warning(f"[FDA] Could not read openFDA bulk zip: {exc}")
            return {}

        return self._parse_openfda(payload.get('results') or [])

    # ------------------------------------------------------------------
    # Source 3: FDA's raw TSV files (different pipeline AND different infra)
    # ------------------------------------------------------------------

    def _fetch_tsv_zip(self) -> Optional[bytes]:
        """
        Fetch the TSV zip, retrying through Akamai's abuse-detection block.

        Deliberately not `download_with_retries`: that helper returns on any
        4xx without retrying, and this block arrives as a 404, so it would
        give up on a condition that clears on its own after a cooldown.
        """
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept': 'application/zip,application/octet-stream,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }

        for attempt in range(BULK_ATTEMPTS):
            if attempt:
                wait = BULK_BACKOFF_SECONDS * attempt + random.uniform(0, 5)
                logger.info(f"[FDA] Retrying TSV download in {wait:.0f}s "
                            f"(attempt {attempt + 1}/{BULK_ATTEMPTS})")
                time.sleep(wait)

            try:
                resp = requests.get(BULK_ZIP_URL, headers=headers, timeout=180)
            except requests.RequestException as exc:
                logger.warning(f"[FDA] TSV download request failed: {exc}")
                continue

            if APOLOGY_MARKER in resp.url:
                logger.warning(
                    f"[FDA] www.fda.gov served the Akamai abuse-detection page "
                    f"for the TSV zip (HTTP {resp.status_code}, final_url={resp.url!r})"
                )
                continue

            if resp.status_code != 200:
                logger.warning(f"[FDA] TSV download returned HTTP {resp.status_code} "
                               f"(final_url={resp.url!r})")
                continue

            # Akamai can also answer 200 with an HTML interstitial; the zip
            # magic number is the only trustworthy signal.
            if not resp.content.startswith(b'PK'):
                logger.warning(f"[FDA] TSV download is not a zip "
                               f"({len(resp.content)} bytes, starts {resp.content[:32]!r})")
                continue

            return resp.content
        return None

    def _fetch_fda_tsv(self) -> Dict[str, dict]:
        content = self._fetch_tsv_zip()
        if not content:
            return {}
        try:
            zf = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile:
            logger.warning(f"[FDA] TSV download is not a valid zip "
                           f"(first bytes: {content[:80]!r})")
            return {}
        with zf:
            return self._assemble(zf)

    # ------------------------------------------------------------------
    # Parser A: openFDA JSON (API and bulk zip share this exact shape)
    # ------------------------------------------------------------------

    def _parse_openfda(self, records: List[dict]) -> Dict[str, dict]:
        applications: Dict[str, dict] = {}

        for record in records:
            appl_no, type_code = _split_appl_number(record.get('application_number', ''))
            if not appl_no:
                continue

            products = []
            te_entries = []
            for product in record.get('products') or []:
                ingredients = product.get('active_ingredients') or []
                names = [i.get('name') for i in ingredients if i.get('name')]
                strengths = [i.get('strength') for i in ingredients if i.get('strength')]
                dosage_form = product.get('dosage_form')
                route = product.get('route')
                products.append({
                    'product_number': product.get('product_number'),
                    'drug_name': product.get('brand_name'),
                    'active_ingredients': '; '.join(names) or None,
                    'strength': '; '.join(strengths) or None,
                    'dosage_form': dosage_form,
                    'route': route,
                    'dosage_form_route': ';'.join(p for p in (dosage_form, route) if p) or None,
                    'marketing_status': product.get('marketing_status'),
                    'te_code': product.get('te_code'),
                    'reference_drug': _yes_no(product.get('reference_drug')),
                    'reference_standard': _yes_no(product.get('reference_standard')),
                })
                if product.get('te_code'):
                    te_entries.append({
                        'product_number': product.get('product_number'),
                        'te_code': product.get('te_code'),
                        'marketing_status': product.get('marketing_status'),
                    })

            originals, supplements, documents = [], [], []
            seen_urls = set()
            for submission in record.get('submissions') or []:
                submission_type = (submission.get('submission_type') or '').upper()
                entry = {
                    'submission_type': submission_type,
                    'submission_number': submission.get('submission_number'),
                    'submission_status': submission.get('submission_status'),
                    'submission_status_date': _iso_date(submission.get('submission_status_date', '')),
                    'submission_class_code': submission.get('submission_class_code'),
                    'submission_class_description': submission.get('submission_class_code_description'),
                    'review_priority': submission.get('review_priority'),
                    # The TSVs carry action types via a join table; openFDA
                    # exposes property types instead. Keep the key present so
                    # the shape matches, and record what this source does give.
                    'action_types': [],
                    'property_types': [
                        p.get('code') for p in (submission.get('submission_property_type') or [])
                        if p.get('code')
                    ],
                    'public_notes': submission.get('submission_public_notes') or None,
                }
                (supplements if submission_type == 'SUPPL' else originals).append(entry)

                for doc in submission.get('application_docs') or []:
                    url = _https((doc.get('url') or '').strip())
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    category = (doc.get('type') or 'document').lower()
                    documents.append({
                        'category': category,
                        'text': doc.get('title') or doc.get('type') or category,
                        'source_url': url,
                        'is_pdf': url.lower().endswith('.pdf'),
                        'submission_type': submission_type,
                        'submission_number': submission.get('submission_number'),
                        'document_date': _iso_date(doc.get('date', '')),
                    })

            applications[appl_no] = {
                'application_type_code': type_code,
                'company': record.get('sponsor_name') or None,
                'public_notes': None,
                'products': products,
                'original_approvals': originals,
                'supplements': supplements,
                'therapeutic_equivalents': te_entries,
                'documents': documents,
                'labels': [d for d in documents if d['category'] == 'label'],
                # No TSV equivalent — UNII, RxCUI, pharm class, SPL ids, NDC.
                'openfda': record.get('openfda') or None,
            }

        logger.info(
            f"[FDA] parsed {len(applications)} application(s), "
            f"{sum(len(v['products']) for v in applications.values())} product(s), "
            f"{sum(len(v['documents']) for v in applications.values())} document link(s)"
        )
        return applications

    # ------------------------------------------------------------------
    # Parser B: FDA's TSV files, joined into the same record shape
    # ------------------------------------------------------------------

    def _assemble(self, zf: zipfile.ZipFile) -> Dict[str, dict]:
        doc_types = _lookup(zf, 'ApplicationsDocsType_Lookup.txt',
                            'ApplicationDocsType_Lookup_ID',
                            'ApplicationDocsType_Lookup_Description')
        marketing_statuses = _lookup(zf, 'MarketingStatus_Lookup.txt',
                                     'MarketingStatusID', 'MarketingStatusDescription')
        submission_classes = {
            row['SubmissionClassCodeID']: (row.get('SubmissionClassCode'),
                                           row.get('SubmissionClassCodeDescription'))
            for row in _read_tsv(zf, 'SubmissionClass_Lookup.txt')
            if row.get('SubmissionClassCodeID')
        }
        action_types = _lookup(zf, 'ActionTypes_Lookup.txt',
                              'ActionTypes_LookupID', 'ActionTypes_LookupDescription')

        status_by_product: Dict[tuple, str] = {}
        for row in _read_tsv(zf, 'MarketingStatus.txt'):
            key = (row.get('ApplNo'), row.get('ProductNo'))
            status_by_product[key] = marketing_statuses.get(
                row.get('MarketingStatusID', ''), row.get('MarketingStatusID', '')
            )

        te_by_product: Dict[tuple, str] = {}
        te_by_appl: Dict[str, List[dict]] = defaultdict(list)
        for row in _read_tsv(zf, 'TE.txt'):
            appl_no, product_no = row.get('ApplNo'), row.get('ProductNo')
            te_by_product[(appl_no, product_no)] = row.get('TECode', '')
            te_by_appl[appl_no].append({
                'product_number': product_no,
                'te_code': row.get('TECode'),
                'marketing_status': marketing_statuses.get(
                    row.get('MarketingStatusID', ''), row.get('MarketingStatusID', '')
                ),
            })

        actions_by_submission: Dict[tuple, List[str]] = defaultdict(list)
        for row in _read_tsv(zf, 'Join_Submission_ActionTypes_Lookup.txt'):
            key = (row.get('ApplNo'), row.get('SubmissionType'), row.get('SubmissionNo'))
            description = action_types.get(row.get('ActionTypes_LookupID', ''))
            if description and description not in actions_by_submission[key]:
                actions_by_submission[key].append(description)

        products_by_appl: Dict[str, List[dict]] = defaultdict(list)
        for row in _read_tsv(zf, 'Products.txt'):
            appl_no, product_no = row.get('ApplNo'), row.get('ProductNo')
            dosage_form, route = _split_form_route(row.get('Form', ''))
            products_by_appl[appl_no].append({
                # Keys mirror openFDA's normalised shape (see _parse_openfda)
                # so json_data is identical whichever source served the run.
                'product_number': product_no,
                'drug_name': row.get('DrugName'),
                'active_ingredients': row.get('ActiveIngredient') or None,
                'strength': row.get('Strength') or None,
                'dosage_form': dosage_form,
                'route': route,
                'dosage_form_route': row.get('Form') or None,
                'marketing_status': status_by_product.get((appl_no, product_no)),
                'te_code': te_by_product.get((appl_no, product_no)),
                'reference_drug': _yes_no(row.get('ReferenceDrug')),
                'reference_standard': _yes_no(row.get('ReferenceStandard')),
            })

        originals_by_appl: Dict[str, List[dict]] = defaultdict(list)
        supplements_by_appl: Dict[str, List[dict]] = defaultdict(list)
        for row in _read_tsv(zf, 'Submissions.txt'):
            appl_no = row.get('ApplNo')
            submission_type = (row.get('SubmissionType') or '').upper()
            class_code, class_description = submission_classes.get(
                row.get('SubmissionClassCodeID', ''), (None, None)
            )
            entry = {
                'submission_type': submission_type,
                'submission_number': row.get('SubmissionNo'),
                'submission_status': row.get('SubmissionStatus'),
                'submission_status_date': _iso_date(row.get('SubmissionStatusDate', '')),
                'submission_class_code': class_code,
                'submission_class_description': class_description,
                'review_priority': row.get('ReviewPriority'),
                'action_types': actions_by_submission.get(
                    (appl_no, submission_type, row.get('SubmissionNo')), []
                ),
                'property_types': [],
                'public_notes': row.get('SubmissionsPublicNotes') or None,
            }
            if submission_type == 'SUPPL':
                supplements_by_appl[appl_no].append(entry)
            else:
                originals_by_appl[appl_no].append(entry)

        docs_by_appl: Dict[str, List[dict]] = defaultdict(list)
        seen_urls: Dict[str, set] = defaultdict(set)
        for row in _read_tsv(zf, 'ApplicationDocs.txt'):
            appl_no = row.get('ApplNo')
            url = _https(row.get('ApplicationDocsURL') or '')
            if not url or url in seen_urls[appl_no]:
                continue
            seen_urls[appl_no].add(url)

            category = doc_types.get(row.get('ApplicationDocsTypeID', ''), 'document')
            docs_by_appl[appl_no].append({
                'category': category.lower(),
                'text': row.get('ApplicationDocsTitle') or category,
                'source_url': url,
                'is_pdf': url.lower().endswith('.pdf'),
                'submission_type': row.get('SubmissionType'),
                'submission_number': row.get('SubmissionNo'),
                'document_date': _iso_date(row.get('ApplicationDocsDate', '')),
            })

        applications: Dict[str, dict] = {}
        for row in _read_tsv(zf, 'Applications.txt'):
            appl_no = row.get('ApplNo')
            if not appl_no:
                continue
            documents = docs_by_appl.get(appl_no, [])
            applications[appl_no] = {
                'application_type_code': row.get('ApplType') or None,
                'company': row.get('SponsorName') or None,
                'public_notes': row.get('ApplPublicNotes') or None,
                'products': products_by_appl.get(appl_no, []),
                'original_approvals': originals_by_appl.get(appl_no, []),
                'supplements': supplements_by_appl.get(appl_no, []),
                'therapeutic_equivalents': te_by_appl.get(appl_no, []),
                'documents': documents,
                'labels': [d for d in documents if d['category'] == 'label'],
                # This source has no openfda enrichment block.
                'openfda': None,
            }

        logger.info(
            f"[FDA] parsed {len(applications)} application(s), "
            f"{sum(len(v['products']) for v in applications.values())} product(s), "
            f"{sum(len(v['documents']) for v in applications.values())} document link(s)"
        )

        # Confirmed live: plenty of ApplNos appear in the child tables but
        # have no row in Applications.txt — in the 2026-08-14 release, 11
        # products, 3 docs and 5,380 submissions across ~1,460 ApplNos. That
        # is an inconsistency in FDA's own export, not a parsing bug (openFDA
        # drops the same rows). They're skipped because there's no
        # sponsor/type to build a record from, but never silently.
        orphans = (
            set(products_by_appl) | set(docs_by_appl)
            | set(originals_by_appl) | set(supplements_by_appl)
        ) - set(applications)
        if orphans:
            orphan_products = sum(len(products_by_appl[a]) for a in orphans if a in products_by_appl)
            orphan_docs = sum(len(docs_by_appl[a]) for a in orphans if a in docs_by_appl)
            orphan_subs = sum(
                len(originals_by_appl.get(a, ())) + len(supplements_by_appl.get(a, ()))
                for a in orphans
            )
            logger.warning(
                f"[FDA] {len(orphans)} ApplNo(s) have no Applications.txt row and "
                f"were skipped ({orphan_products} product(s), {orphan_subs} "
                f"submission(s), {orphan_docs} document(s)); "
                f"first few: {sorted(orphans)[:10]}"
            )
        return applications

    # ------------------------------------------------------------------
    # Persist every application (WORKERS threads; the only I/O left is
    # optional PDF downloading)
    # ------------------------------------------------------------------

    def _process_applications_concurrently(self, country_id: int, applications: Dict[str, dict]) -> int:
        saved = 0
        error = None

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {
                pool.submit(self._process_application, country_id, appl_no, record): appl_no
                for appl_no, record in applications.items()
            }
            for future in as_completed(futures):
                appl_no = futures[future]
                try:
                    if future.result():
                        saved += 1
                except CountrySkipThresholdReached as exc:
                    error = exc
                    # Exiting the `with` block normally calls
                    # shutdown(wait=True), which would block until every
                    # already-submitted future runs — here that's ~29k.
                    # Cancel everything not yet started; only futures
                    # already mid-flight (bounded by WORKERS) finish.
                    pool.shutdown(wait=False, cancel_futures=True)
                    break
                except Exception:
                    logger.exception(f"Failed to process FDA application ApplNo={appl_no}")
                    continue

                if MAX_RECORDS_PER_COUNTRY and saved >= MAX_RECORDS_PER_COUNTRY:
                    logger.info(f"Reached MAX_RECORDS_PER_COUNTRY={MAX_RECORDS_PER_COUNTRY}, stopping.")
                    pool.shutdown(wait=False, cancel_futures=True)
                    break

        if error is not None:
            raise error
        return saved

    def _process_application(self, country_id: int, appl_no: str, record: dict) -> bool:
        if check_record_exists_by_json_field(country_id, 'application_number', appl_no):
            return False

        products = record['products']
        product_names = list(dict.fromkeys(
            p['drug_name'].strip() for p in products
            if p.get('drug_name') and p['drug_name'].strip()
        ))
        name = _build_name(product_names, appl_no)

        documents = record['documents']
        if DOWNLOAD_DOCUMENTS:
            self._download_documents(country_id, appl_no, documents)

        s3_keys = [d['s3_path'] for d in documents if d.get('s3_path')]

        # The actual SPL label TEXT (indications, warnings, dosage,
        # ingredients, etc.) — see _fetch_labels for why this is looked up
        # per-application-number rather than bulk-crawled, and why a label
        # shared across more than one application_number is correctly
        # fetched and attached once per application, not a duplicate.
        #
        # `appl_no` here is OUR internal, prefix-stripped, zero-padded key
        # (e.g. "020695") — NOT what label.json's own openfda.application_number
        # field contains, which is always prefixed (e.g. "NDA020695", matching
        # drugsfda.json's own native application_number exactly, confirmed
        # live). Searching with the bare form returns a 404 unconditionally,
        # for every application, regardless of whether a real label exists —
        # confirmed live this was silently swallowing every match (e.g. a
        # shared Mekinist-style label) as "no label found". Reconstruct the
        # prefixed form before calling _fetch_labels; skip the lookup
        # entirely (rather than searching with a value known to never match)
        # if application_type_code is missing.
        openfda_appl_no = (
            f"{record['application_type_code']}{appl_no}"
            if record.get('application_type_code') else None
        )
        spl_labels = self._fetch_labels(openfda_appl_no) if openfda_appl_no else []
        if openfda_appl_no and spl_labels is None:
            logger.warning(f"[FDA] Could not fetch openFDA SPL label(s) for {appl_no} "
                            f"— saving the application without them")
            spl_labels = []

        json_data = {
            'application_number': appl_no,
            'application_type_code': record['application_type_code'],
            'company': record['company'],
            'public_notes': record['public_notes'],
            'products': products,
            'approval_history': {
                'original_approvals': record['original_approvals'],
                'supplements': record['supplements'],
            },
            # PDF/document LINKS filed against submissions (category=='label'
            # is just one of several document categories here) — distinct
            # from spl_labels below, which is the actual label text content.
            'label_documents': record['labels'],
            'therapeutic_equivalents': record['therapeutic_equivalents'],
            'documents': documents,
            'spl_labels': spl_labels,
            'openfda': record.get('openfda'),
            'source_url': OVERVIEW_URL.format(appl_no=appl_no),
        }
        save_drug_record(name, country_id, s3_keys, json_data)
        return True

    # ------------------------------------------------------------------
    # Document download + upload — PDFs only, per the storage rule
    # ------------------------------------------------------------------

    def _download_documents(self, country_id: int, appl_no: str, documents: List[dict]):
        for doc in documents:
            url = doc.get('source_url')
            if not doc.get('is_pdf') or not url:
                continue

            content, content_type, status = download_with_retries(url, retries=3, timeout=60)
            if not content:
                logger.warning(f"Failed to download FDA document (HTTP {status}): {url}")
                continue

            # These URLs live on the WAF'd accessdata.fda.gov, so a "success"
            # can still be a block/error page. Never mirror a non-PDF body to
            # S3 under a .pdf key.
            if not content.startswith(b'%PDF'):
                logger.warning(
                    f"Skipping FDA document — response is not a PDF "
                    f"(HTTP {status}, {len(content)} bytes, starts {content[:16]!r}): {url}"
                )
                continue

            try:
                # FDA's own filename (e.g. "020892s019lbl") already encodes
                # the application number and submission — a clear, stable
                # slug, so reuse it rather than composing one from scratch.
                stem = os.path.splitext(os.path.basename(urlparse(url).path))[0] or f'{appl_no}_document'
                key = build_document_key(f"united_states/{country_id}", stem, '.pdf')
                doc['s3_path'] = upload_file(content, key, content_type or content_type_for_ext('.pdf'))
            except Exception:
                logger.exception(f"Failed to upload FDA document to S3: {url}")


# ---------------------------------------------------------------------------
# Standalone test entry-point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import logging as _logging
    from app.db import init_db, get_or_create_country
    from app.crawlers.united_states import COUNTRY_NAME, COUNTRY_CODE

    _logging.basicConfig(
        level=_logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )

    init_db()
    country_id = get_or_create_country(COUNTRY_NAME, COUNTRY_CODE)

    crawler = UnitedStatesFDACrawler()
    try:
        crawler.process_country(country_id)
    finally:
        crawler.close()
