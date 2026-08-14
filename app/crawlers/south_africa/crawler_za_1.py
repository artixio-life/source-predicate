"""
Crawler for South Africa — SAHPRA Registered Health Products
(https://medapps.sahpra.org.za:6006/)

Data source
-----------
A plain server-rendered ASP.NET Core app — no browser needed (unlike the
UK crawler). Confirmed live:

  Listing  POST /Home/getData
      A standard jQuery DataTables server-side-processing endpoint (see
      the listing page's own inline <script>). No auth/session/CSRF
      required — a bare POST with the usual DataTables params works.
      Response: {"data": [...], "recordsTotal": N, "recordsFiltered": N}.
      As of 2026-08: 21,205 total rows.

      Each row: applicantName, productName, api, licence_no,
      application_no, reg_date, status, expiryDate, ingredient,
      therapeutic_area, secureId, appSecureId.

  Detail   GET /Home/Details/?id=<secureId>
      Plain HTML: a single <table id="reg"><tbody> of
      <tr><td>Label</td><td>Value</td></tr> rows:
        Applicant, Proprietary Name, Dosage Form, Ingredients, Strength,
        Registration number, Date Registered, Renewal Date, Date Expired,
        Active Pharmaceutical Ingredient, Status.

No document_url
----------------
This registry has no attached files (no SPC/PIL/PAR-equivalent) — every
row is pure structured registration data, so `document_url` is always
NULL/empty here; everything lives in `json_data`.

Record mapping
--------------
  name          -> productName from the list row (falls back to the detail
                   page's "Proprietary Name" if that's blank)
  document_url  -> always empty
  json_data     -> the list row's fields (snake_case) plus a `detail`
                   object parsed from the Details page.

Dedup
-----
By `application_no` — SAHPRA's own unique application identifier, always
populated and stable. `licence_no` is NOT usable: for legacy entries it is
literally the string "Old Medicine" (not unique), and `name` (product
name) isn't reliably unique either (distinct registrations, sometimes by
different applicants/eras, can share a display name). Uses
app.db.check_record_exists_by_json_field(country_id, 'application_no', ...).
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from app.db import (
    CountrySkipThresholdReached,
    check_record_exists_by_json_field,
    save_drug_record,
)
from app.config import MAX_RECORDS_PER_COUNTRY

logger = logging.getLogger(__name__)

BASE_URL = os.getenv('SAHPRA_BASE_URL', 'https://medapps.sahpra.org.za:6006')
LIST_ENDPOINT = f'{BASE_URL}/Home/getData'
DETAIL_ENDPOINT = f'{BASE_URL}/Home/Details/'

PAGE_SIZE = int(os.getenv('SAHPRA_PAGE_SIZE', '100'))
DETAIL_WORKERS = int(os.getenv('SAHPRA_DETAIL_WORKERS', '6'))
REQUEST_TIMEOUT = 30

_LIST_COLUMNS = (
    'applicantName', 'productName', 'api', 'licence_no',
    'application_no', 'reg_date', 'status', 'secureId',
)

# Detail page <td>Label</td><td>Value</td> -> json_data.detail key
_DETAIL_FIELD_MAP = {
    'applicant': 'applicant',
    'proprietary name': 'proprietary_name',
    'dosage form': 'dosage_form',
    'ingredients': 'ingredients',
    'strength': 'strength',
    'registration number': 'registration_number',
    'date registered': 'date_registered',
    'renewal date': 'renewal_date',
    'date expired': 'date_expired',
    'active pharmaceutical ingredient': 'active_pharmaceutical_ingredient',
    'status': 'status',
}


def _datatables_payload(start: int, length: int) -> dict:
    """Build the standard jQuery DataTables server-side request body this endpoint expects."""
    payload = {
        'draw': '1',
        'start': str(start),
        'length': str(length),
        'search[value]': '',
        'search[regex]': 'false',
        'order[0][column]': '0',
        'order[0][dir]': 'asc',
    }
    for i, col in enumerate(_LIST_COLUMNS):
        payload[f'columns[{i}][data]'] = col
        payload[f'columns[{i}][name]'] = col if col != 'secureId' else ''
        payload[f'columns[{i}][searchable]'] = 'true'
        payload[f'columns[{i}][orderable]'] = 'true'
        payload[f'columns[{i}][search][value]'] = ''
        payload[f'columns[{i}][search][regex]'] = 'false'
    return payload


class SouthAfricaSAHPRACrawler:
    """Walks the SAHPRA registered-products DataTables listing + per-row Details page."""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'X-Requested-With': 'XMLHttpRequest',
        })

    def close(self):
        self._session.close()

    # ------------------------------------------------------------------
    # Top-level crawl
    # ------------------------------------------------------------------

    def process_country(self, country_id: int):
        saved = 0
        start = 0
        total: Optional[int] = None
        limit_reached = False

        while not limit_reached:
            rows, total = self._fetch_list_page(start, PAGE_SIZE)
            if total is not None:
                logger.info(f"[SAHPRA] start={start} recordsTotal={total}")
            if not rows:
                break

            pending = [
                row for row in rows
                if not check_record_exists_by_json_field(
                    country_id, 'application_no', row.get('application_no')
                )
            ]

            with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
                futures = {pool.submit(self._process_row, country_id, row): row for row in pending}
                for future in as_completed(futures):
                    row = futures[future]
                    try:
                        if future.result():
                            saved += 1
                    except CountrySkipThresholdReached:
                        raise
                    except Exception:
                        logger.exception(
                            f"Failed to process SAHPRA row: {row.get('application_no')} "
                            f"({row.get('productName')!r})"
                        )

                    if MAX_RECORDS_PER_COUNTRY and saved >= MAX_RECORDS_PER_COUNTRY:
                        logger.info(f"Reached MAX_RECORDS_PER_COUNTRY={MAX_RECORDS_PER_COUNTRY}, stopping.")
                        # Exiting this `with` block normally calls
                        # shutdown(wait=True) — it would block until every
                        # already-submitted future in this page's batch
                        # finishes (up to PAGE_SIZE of them), completely
                        # ignoring the limit we just hit. Cancel every
                        # not-yet-started future explicitly first; only
                        # futures already mid-flight (bounded by
                        # DETAIL_WORKERS) still run to completion.
                        limit_reached = True
                        pool.shutdown(wait=False, cancel_futures=True)
                        break

            if limit_reached:
                break

            start += PAGE_SIZE
            if total is not None and start >= total:
                break

        logger.info(f"SAHPRA crawl finished. Saved/updated {saved} records.")

    # ------------------------------------------------------------------
    # Listing page
    # ------------------------------------------------------------------

    def _fetch_list_page(self, start: int, length: int) -> Tuple[List[dict], Optional[int]]:
        for attempt in range(3):
            try:
                resp = self._session.post(
                    LIST_ENDPOINT,
                    data=_datatables_payload(start, length),
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException as exc:
                logger.warning(f"SAHPRA list request failed at start={start} (attempt {attempt + 1}): {exc}")
                time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code != 200:
                logger.warning(f"SAHPRA list returned HTTP {resp.status_code} at start={start}")
                time.sleep(2)
                continue
            try:
                payload = resp.json()
            except ValueError:
                logger.warning(f"SAHPRA list returned non-JSON at start={start}")
                return [], None
            return payload.get('data') or [], payload.get('recordsTotal')
        return [], None

    # ------------------------------------------------------------------
    # Per-row processing
    # ------------------------------------------------------------------

    def _process_row(self, country_id: int, row: dict) -> bool:
        name = (row.get('productName') or '').strip()

        detail = self._fetch_detail(row.get('secureId'))
        if not name:
            name = (detail.get('proprietary_name') or '').strip()
        if not name:
            name = 'SAHPRA Product'

        json_data = {
            'applicant_name': row.get('applicantName'),
            'product_name': row.get('productName'),
            'api': row.get('api'),
            'licence_no': row.get('licence_no'),
            'application_no': row.get('application_no'),
            'reg_date': row.get('reg_date'),
            'status': row.get('status'),
            'expiry_date': row.get('expiryDate'),
            'ingredient': row.get('ingredient'),
            'therapeutic_area': row.get('therapeutic_area'),
            'secure_id': row.get('secureId'),
            'app_secure_id': row.get('appSecureId'),
            'detail': detail,
        }

        # No files on this source — document_url stays empty (see module docstring).
        save_drug_record(name[:255], country_id, None, json_data)
        return True

    def _fetch_detail(self, secure_id: Optional[str]) -> Dict[str, str]:
        if not secure_id:
            return {}
        for attempt in range(3):
            try:
                resp = self._session.get(
                    DETAIL_ENDPOINT,
                    params={'id': secure_id},
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException as exc:
                logger.warning(f"SAHPRA detail request failed for {secure_id[:12]}... (attempt {attempt + 1}): {exc}")
                time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code != 200:
                logger.warning(f"SAHPRA detail returned HTTP {resp.status_code} for {secure_id[:12]}...")
                time.sleep(2)
                continue
            return self._parse_detail_html(resp.text)
        return {}

    def _parse_detail_html(self, html: str) -> Dict[str, str]:
        soup = BeautifulSoup(html, 'html.parser')
        table = soup.find('table', id='reg')
        if not table:
            return {}

        detail: Dict[str, str] = {}
        for tr in table.find_all('tr'):
            cells = tr.find_all('td')
            if len(cells) < 2:
                continue
            label = cells[0].get_text(' ', strip=True).lower()
            value = cells[1].get_text(' ', strip=True)
            key = _DETAIL_FIELD_MAP.get(label)
            if key:
                detail[key] = value
        return detail


# ---------------------------------------------------------------------------
# Standalone test entry-point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import logging as _logging
    from app.db import init_db, get_or_create_country
    from app.crawlers.south_africa import COUNTRY_NAME, COUNTRY_CODE

    _logging.basicConfig(
        level=_logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )

    init_db()
    country_id = get_or_create_country(COUNTRY_NAME, COUNTRY_CODE)

    crawler = SouthAfricaSAHPRACrawler()
    try:
        crawler.process_country(country_id)
    finally:
        crawler.close()
