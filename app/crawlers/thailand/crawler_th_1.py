"""
Crawler for Thailand — FDA Public Pharmaceutical Regulatory Information portal
(https://porta.fda.moph.go.th/fda_search_center_new/)

Why brute-force search terms
-----------------------------
The public portal has no "list everything" endpoint. Its Angular search UI
enforces a minimum search-term length of 4 characters (submitting anything
shorter shows a validation error), and every request must supply a query
string matched against product name / license number / company name — there
is no way to page through the full registry with a blank filter.

To reach full coverage anyway, this crawler brute-forces every 4-letter
lowercase combination (a-z, 26**4 = 456,976 terms by default — see
TH_FDA_ALPHABET / TH_FDA_COMBO_LENGTH) as the search term. Because the API
matches substrings across several fields (Thai/English product name, license
number, company name), most real products are reachable by at least one such
combination. Results are deduplicated in-memory and against the database by
`Newcode` (the site's own stable per-registration identifier), so overlap
between combinations (the same product surfacing under several different
4-letter substrings) costs redundant search requests but never a duplicate
row. This is a genuinely large number of requests — see TH_FDA_SEARCH_WORKERS
/ TH_FDA_MAX_COMBOS below for pacing and testing controls.

Data source
-----------
  Search   POST https://porta.fda.moph.go.th/FDA_SEARCH_CENTER_BACKEND/SEACH_ALL/GET_SEARCH
      multipart/form-data body:
        MODEL         JSON-encoded filter object (see SEARCH_MODEL_BASE below)
        search_input  the search term (duplicated from MODEL.SEARCH_VALUE)
      MODEL reproduces the site's own "Search by product" > "Medication"
      filter (RADIO_TYPE = "สืบค้นแยกรายผลิตภัณฑ์" i.e. "Search by product",
      RADIO_TYPE_ETC_DRUG = "Y" i.e. the Medication checkbox) — confirmed
      live by reproducing the exact filter combination shown in the site's
      own UI screenshot and comparing result counts.
      Response: a bare JSON array of result rows, each carrying `Newcode`
      (the field needed for the detail fetch) plus a partial preview of the
      product (name, license number, licensee, status, etc).

  Detail   POST https://pertento.fda.moph.go.th/FDA_INFORMATION_DRUG/SV_CENTER/GET_PHAR_PRODUCT_INFO?Newcode=<code>
      Requires Referer / Origin / X-Requested-With headers matching a normal
      browser session on that host — a bare request with no Referer/Origin
      returns {"MSG_CODE": "403", "MSG_RESULT": "ERROR"} instead of data
      (confirmed live). No cookies/auth token needed, just those headers.
      Returns the FULL structured product record: registration number,
      approval/expiry/cancellation dates, both trade names, dose form,
      legislation class, indication text, active-ingredient formula
      (XML_DRUG_FORMULA), ATC classification (XML_DRUG_ATC), veterinary-use
      table (XML_DRUG_ANIMAL), manufacturer/repacker/release-site details
      with each one's role (XML_DRUG_FRGN), licensee/establishment name and
      address, and status history.

No document_url
----------------
Confirmed live across multiple products (both active and revoked, human and
originally-branded): DOCUMENT_FILE, REPORT_FILE, and PICTURE_FLIE_1/2/3 are
always empty arrays on this public endpoint — there is no attached PDF/label
file to download here, only structured text data. document_url is therefore
always empty, same as the Saudi Arabia and South Africa crawlers. This was
verified against the user's own manually-saved "print to PDF" of a product
detail page — every field visible on that printed page is present in the
detail JSON (and the JSON is strictly richer: e.g. it exposes each
manufacturer's role, whereas the printed page just repeats a flat table).

Dedup
-----
By `Newcode` (stored as json_data.newcode) — the portal's own stable
identifier for a registration, present on every search result row and
confirmed present with no null hits across all fetched detail records. Uses
app.db.check_record_exists_by_json_field(country_id, 'newcode', ...), plus an
in-process set to avoid redundant detail fetches within a single run when the
same product surfaces under more than one 4-letter search term.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Dict, List, Optional
from urllib.parse import quote

import requests

from app.db import (
    CountrySkipThresholdReached,
    check_record_exists_by_json_field,
    save_drug_record,
)
from app.config import MAX_RECORDS_PER_COUNTRY

logger = logging.getLogger(__name__)

SEARCH_BASE_URL = os.getenv(
    'TH_FDA_SEARCH_BASE_URL', 'https://porta.fda.moph.go.th/FDA_SEARCH_CENTER_BACKEND/SEACH_ALL'
)
SEARCH_ENDPOINT = f'{SEARCH_BASE_URL}/GET_SEARCH'

DETAIL_BASE_URL = os.getenv('TH_FDA_DETAIL_BASE_URL', 'https://pertento.fda.moph.go.th/FDA_INFORMATION_DRUG')
DETAIL_ENDPOINT = f'{DETAIL_BASE_URL}/SV_CENTER/GET_PHAR_PRODUCT_INFO'
DETAIL_PAGE_URL = f'{DETAIL_BASE_URL}/Home/Phar_Product_Inform_Page'

# Reproduces the site's own "Search by product" > "Medication" checkbox
# filter — confirmed live against the UI's screenshot of that exact
# combination. Only SEARCH_VALUE varies per request.
SEARCH_MODEL_BASE = {
    'SEARCH_VALUE': None,
    'RADIO_TYPE': 'สืบค้นแยกรายผลิตภัณฑ์',  # "Search by product"
    'RADIO_TYPE_ETC_FOOD': None,
    'RADIO_TYPE_ETC_DRUG': 'Y',  # "Medication" checkbox
    'RADIO_TYPE_ETC_HERB': None,
    'RADIO_TYPE_ETC_TXC': None,
    'RADIO_TYPE_ETC_CMT': None,
    'RADIO_TYPE_ETC_NCT': None,
    'RADIO_TYPE_ETC_MDC': None,
    'RADIO_TYPE_ETC_ADVER': None,
    'RADIO_TYPE_LOCATION': None,
}

# Brute-force search-term space. Default: every 4-letter lowercase
# combination (26**4 = 456,976 terms) — the site's own minimum search length.
ALPHABET = os.getenv('TH_FDA_ALPHABET', 'abcdefghijklmnopqrstuvwxyz')
COMBO_LENGTH = int(os.getenv('TH_FDA_COMBO_LENGTH', '4'))

# Testing override — cap how many search terms to try (0 = no limit, walk
# the full combination space).
MAX_COMBOS = int(os.getenv('TH_FDA_MAX_COMBOS', '0'))

SEARCH_WORKERS = int(os.getenv('TH_FDA_SEARCH_WORKERS', '8'))
# Separate pool for per-product detail fetches, so a single search term with
# a huge hit count (confirmed live: some 4-letter terms return 5,000-10,000+
# rows) doesn't monopolize one search worker thread — see process_country().
DETAIL_WORKERS = int(os.getenv('TH_FDA_DETAIL_WORKERS', '16'))
REQUEST_TIMEOUT = int(os.getenv('TH_FDA_TIMEOUT', '30'))
REQUEST_RETRIES = 3

_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
)


def _generate_combos():
    for combo_tuple in itertools.product(ALPHABET, repeat=COMBO_LENGTH):
        yield ''.join(combo_tuple)


def _thai_or_none(value) -> Optional[str]:
    """Normalize a portal text field: strip whitespace, treat '-'/'' as null."""
    if not isinstance(value, str):
        return value
    value = value.strip()
    return value if value and value != '-' else None


class ThailandFDACrawler:
    """Brute-force-searches the Thai FDA public portal and fetches full detail per product."""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({'User-Agent': _USER_AGENT})
        # Default HTTPAdapter pool size is 10 — too small once search and
        # detail workers combined can hold this many connections open at
        # once (confirmed live: "Connection pool is full, discarding
        # connection" warnings + connection thrashing under the default).
        pool_size = SEARCH_WORKERS + DETAIL_WORKERS
        adapter = requests.adapters.HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
        self._session.mount('https://', adapter)
        self._session.mount('http://', adapter)
        self._seen_newcodes = set()
        self._seen_lock = threading.Lock()
        self._saved_lock = threading.Lock()

    def close(self):
        self._session.close()

    # ------------------------------------------------------------------
    # Top-level crawl
    # ------------------------------------------------------------------

    def process_country(self, country_id: int):
        """
        Runs search-term and detail-fetch work in two separate thread pools
        instead of one. A single generic search term (e.g. "tabl") can match
        thousands of products — confirmed live, some 4-letter terms return
        5,000-10,000+ rows in one response. If detail-fetching those rows
        happened inline inside the search-term task (one thread per term),
        that one term would monopolize a worker thread for a very long time
        while every other combo waits. Splitting search and detail into their
        own pools means a huge result set's detail fetches get spread across
        every detail worker immediately, and the search pool keeps moving on
        to the next combo without waiting for them.
        """
        combo_iter = _generate_combos()
        if MAX_COMBOS:
            combo_iter = itertools.islice(combo_iter, MAX_COMBOS)

        saved_ref = [0]
        combos_done = 0
        stop = False

        with ThreadPoolExecutor(max_workers=SEARCH_WORKERS) as search_pool, \
                ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as detail_pool:
            search_futures = {}
            detail_futures = set()

            def _submit_next_combo():
                try:
                    combo = next(combo_iter)
                except StopIteration:
                    return False
                search_futures[search_pool.submit(self._search_term, country_id, combo)] = combo
                return True

            for _ in range(SEARCH_WORKERS * 2):
                if not _submit_next_combo():
                    break

            while search_futures or detail_futures:
                done, _ = wait(
                    set(search_futures) | detail_futures,
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    if future in search_futures:
                        combo = search_futures.pop(future)
                        combos_done += 1
                        try:
                            newcodes = future.result()
                        except Exception:
                            logger.exception(f"[Thailand] Failed to process search term {combo!r}")
                            newcodes = []

                        for newcode in newcodes:
                            detail_futures.add(
                                detail_pool.submit(self._detail_and_save, country_id, newcode)
                            )

                        if combos_done % 1000 == 0:
                            logger.info(
                                f"[Thailand] {combos_done} search terms processed, "
                                f"{saved_ref[0]} records saved so far "
                                f"({len(detail_futures)} detail fetches in flight)"
                            )

                        if not stop:
                            _submit_next_combo()
                    else:
                        detail_futures.discard(future)
                        try:
                            if future.result():
                                with self._saved_lock:
                                    saved_ref[0] += 1
                        except CountrySkipThresholdReached:
                            raise
                        except Exception:
                            logger.exception("[Thailand] Failed to fetch/save a product detail")

                if MAX_RECORDS_PER_COUNTRY and saved_ref[0] >= MAX_RECORDS_PER_COUNTRY:
                    # Stop pulling new combos; let already-submitted search
                    # and detail futures drain so the crawl winds down
                    # cleanly instead of queuing more work.
                    stop = True

        logger.info(
            f"[Thailand] crawl finished. {combos_done} search terms tried, "
            f"{saved_ref[0]} records saved/updated."
        )

    # ------------------------------------------------------------------
    # Per search-term processing
    # ------------------------------------------------------------------

    def _search_term(self, country_id: int, term: str) -> List[str]:
        """Search one term and return the Newcodes worth a detail fetch (new + not already stored)."""
        rows = self._search(term)
        logger.info(f"[Thailand] search term {term!r} -> {len(rows) if rows else 0} result(s)")
        if not rows:
            return []

        pending = []
        for row in rows:
            newcode = row.get('Newcode')
            if not newcode:
                continue

            with self._seen_lock:
                if newcode in self._seen_newcodes:
                    continue
                self._seen_newcodes.add(newcode)

            if check_record_exists_by_json_field(country_id, 'newcode', newcode):
                continue

            pending.append(newcode)
        return pending

    def _detail_and_save(self, country_id: int, newcode: str) -> bool:
        detail = self._fetch_detail(newcode)
        if not detail:
            return False
        self._save_detail(country_id, newcode, detail)
        return True

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _search(self, term: str) -> Optional[List[dict]]:
        model = dict(SEARCH_MODEL_BASE, SEARCH_VALUE=term)
        files = {
            'MODEL': (None, json.dumps(model)),
            'search_input': (None, term),
        }
        for attempt in range(REQUEST_RETRIES):
            try:
                resp = self._session.post(SEARCH_ENDPOINT, files=files, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                logger.warning(f"[Thailand] search request failed for {term!r} (attempt {attempt + 1}): {exc}")
                time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code != 200:
                logger.warning(f"[Thailand] search returned HTTP {resp.status_code} for {term!r}")
                time.sleep(2)
                continue
            try:
                body = resp.json()
            except ValueError:
                logger.warning(f"[Thailand] search returned non-JSON for {term!r}")
                return None
            if isinstance(body, list):
                return body
            return None
        return None

    # ------------------------------------------------------------------
    # Detail
    # ------------------------------------------------------------------

    def _fetch_detail(self, newcode: str) -> Optional[dict]:
        # Some Newcode values returned by search contain stray non-ASCII
        # (Thai) characters — a data-quality quirk on the source's side,
        # confirmed live (e.g. "U1DH00102สธ10038570016181C"). Raw non-latin1
        # characters break header encoding, so percent-encode for both the
        # query string and the Referer, matching what a real browser's
        # address bar would send.
        encoded_newcode = quote(newcode, safe='')
        headers = {
            'Referer': f'{DETAIL_PAGE_URL}?Newcode={encoded_newcode}',
            'Origin': DETAIL_BASE_URL.split('/FDA_INFORMATION_DRUG')[0],
            'X-Requested-With': 'XMLHttpRequest',
        }
        url = f'{DETAIL_ENDPOINT}?Newcode={encoded_newcode}'
        for attempt in range(REQUEST_RETRIES):
            try:
                resp = self._session.post(url, headers=headers, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                logger.warning(f"[Thailand] detail request failed for {newcode} (attempt {attempt + 1}): {exc}")
                time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code != 200:
                logger.warning(f"[Thailand] detail returned HTTP {resp.status_code} for {newcode}")
                time.sleep(2)
                continue
            try:
                body = resp.json()
            except ValueError:
                logger.warning(f"[Thailand] detail returned non-JSON for {newcode}")
                return None
            if isinstance(body, dict) and body.get('MSG_RESULT') == 'ERROR':
                logger.warning(f"[Thailand] detail fetch rejected for {newcode}: {body.get('MSG_CODE')}")
                time.sleep(2)
                continue
            if isinstance(body, dict) and body.get('NEWCODE'):
                return body
            return None
        return None

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_detail(self, country_id: int, newcode: str, detail: dict):
        name = (
            _thai_or_none(detail.get('PRODUCT_NAME_MAIN_EN'))
            or _thai_or_none(detail.get('PRODUCT_NAME_MAIN_TH'))
            or _thai_or_none(detail.get('REGISTER_LICENSE'))
            or newcode
        )

        json_data: Dict = {
            # 'newcode' is this crawler's OWN dedup key against
            # source.drug_predicate_raw_records (see check_record_exists_by_json_field
            # calls above) — the portal's opaque internal id, always unique but not
            # a real registration number.
            #
            # 'registration_number' is a SEPARATE, deliberately-named field for the
            # downstream predicate-assessment repo's processing/promote.py, which
            # extracts a country's registration number from differently-named
            # json_data fields per crawler (see its _TOP_LEVEL_KEYS comment) and
            # checks 'registration_number' FIRST. It must hold REGISTER_LICENSE
            # (the official Marketing Authorization Number, e.g. "1A 300/33") —
            # NOT raw RGTNO (e.g. "3300300"), which is only the numeric portion and
            # is not guaranteed unique without its RGTTPCD type-code prefix ("1A" vs
            # "1C" etc.); using RGTNO alone risks two unrelated Thai products
            # colliding on drug.products' (country_id, registration_number) unique
            # constraint during promotion.
            'newcode': newcode,
            'registration_number': _thai_or_none(detail.get('REGISTER_LICENSE')),
            'registration_type_code': detail.get('RGTTPCD'),
            'registration_number_raw': detail.get('RGTNO'),
            'approval_date': _thai_or_none(detail.get('APP_DATE')),
            'cancellation_date': _thai_or_none(detail.get('CNC_DATE')),
            'expiry_date': _thai_or_none(detail.get('EXP_DATE')),
            'updated_date': _thai_or_none(detail.get('UPDATE_DATE')),
            'status_name': _thai_or_none(detail.get('STATUS_NAME')),
            'status_remark': _thai_or_none(detail.get('STATUS_REMARK')),
            'licensee_name': _thai_or_none(detail.get('ENTREPRENEUR_NAME')),
            'product_name_en': _thai_or_none(detail.get('PRODUCT_NAME_MAIN_EN')),
            'product_name_th': _thai_or_none(detail.get('PRODUCT_NAME_MAIN_TH')),
            'establishment_license': _thai_or_none(detail.get('LOCATION_LICENSE')),
            'establishment_name': _thai_or_none(detail.get('LOCATION_NAME')),
            'establishment_address': _thai_or_none(detail.get('LOCATION_ADDR')),
            'product_category': _thai_or_none(detail.get('PRODUCT_CATEGORY')),
            'dosage_form_th': _thai_or_none(detail.get('PRODUCT_DOSAGE_FORM_TH')),
            'dosage_form_en': _thai_or_none(detail.get('PRODUCT_DOSAGE_FORM_EN')),
            'legislation_class': _thai_or_none(detail.get('PRODUCT_thakindnm')),
            'point_of_use_category': _thai_or_none(detail.get('PRODUCT_thaclassnm')),
            'indication': _thai_or_none(detail.get('PRODUCT_INDICATION')),
            'formula': [
                {
                    'ingredient_name': _thai_or_none(f.get('FML_NAME')),
                    'amount': _thai_or_none(f.get('FML_AMOUNT')),
                    'per_unit': _thai_or_none(f.get('FML_DRGPERUNIT')),
                    'line_no': _thai_or_none(f.get('FML_FLINENO')),
                }
                for f in (detail.get('XML_DRUG_FORMULA') or [])
                if isinstance(f, dict)
            ],
            'atc_codes': [
                {
                    'code': _thai_or_none(a.get('PRODUCT_ATC_CODE')),
                    'name': _thai_or_none(a.get('PRODUCT_ATC_NAME')),
                }
                for a in (detail.get('XML_DRUG_ATC') or [])
                if isinstance(a, dict)
            ],
            'manufacturers': [
                {
                    'name': _thai_or_none(m.get('FRG_NAME')),
                    'city': _thai_or_none(m.get('FRG_CITY')),
                    'country': _thai_or_none(m.get('FRG_COUNTRY')),
                    'role': _thai_or_none(m.get('FRG_METHOD_TYPE')),
                }
                for m in (detail.get('XML_DRUG_FRGN') or [])
                if isinstance(m, dict)
            ],
            'agents': detail.get('XML_DRUG_AGENT') or [],
            'veterinary_use': [
                v
                for v in (detail.get('XML_DRUG_ANIMAL') or [])
                if isinstance(v, dict) and any(_thai_or_none(val) for val in v.values())
            ],
            'history': detail.get('XML_DRUG_HISTORY') or [],
        }

        # No attached PDF/label on this source — document_url stays empty
        # (see module docstring).
        save_drug_record(name[:255], country_id, None, json_data)


# ---------------------------------------------------------------------------
# Standalone test entry-point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import logging as _logging
    from app.db import init_db, get_or_create_country
    from app.crawlers.thailand import COUNTRY_NAME, COUNTRY_CODE

    _logging.basicConfig(
        level=_logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )

    init_db()
    country_id = get_or_create_country(COUNTRY_NAME, COUNTRY_CODE)

    crawler = ThailandFDACrawler()
    try:
        crawler.process_country(country_id)
    finally:
        crawler.close()
