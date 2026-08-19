"""
Crawler for Saudi Arabia — SFDA New Drug Approvals
(https://www.sfda.gov.sa/en/new-sfda-drug-approvals)

Data source
-----------
A plain server-rendered Drupal site (Views module) — no browser needed.
Confirmed live:

  Listing  GET /en/new-sfda-drug-approvals?page=<N>   (0-indexed)
      A standard Drupal Views table, 10 rows/page: Request Type, Drug Type,
      Trade Name, Scientific Name, Strength, Dosage Form, Approval Date,
      and a "SFDA Approved Use" link/modal per row pointing at
      /en/drug-approvals-use/<id>. As of 2026-08: 34 pages (~340 rows).
      `items_per_page` is not honored by this view (confirmed live) — the
      page size is fixed at 10, so this is a real ~34-request crawl, not
      something to try to shrink further.

  Detail   GET /en/drug-approvals-use/<id>
      A full HTML page (confirmed live: identical whether or not AJAX
      headers are sent — this endpoint doesn't special-case Drupal's
      use-ajax flow, it just always renders the full page) containing
      exactly one field beyond what the listing row already has:
      `field--name-field-sfda-approved-use` — the drug's approved
      indication/use text. No other metadata, no attached PDF/label.

No document_url
----------------
This source has no attached files (no SPC/label PDF-equivalent) — every
row is pure structured text, so `document_url` is always empty here;
everything lives in `json_data`.

Record mapping
--------------
  name          -> Trade Name from the list row
  document_url  -> always empty
  json_data     -> the list row's fields (snake_case) plus `approved_use`
                   (the indication text from the detail page) and
                   `sfda_use_id` (the detail page's node id, from its URL)

Dedup
-----
By `sfda_use_id` — the numeric id in each row's own
`/en/drug-approvals-use/<id>` link. Confirmed live this is per-row unique
(e.g. two different strengths of the same trade name, "Brevie" 25mg and
50mg, link to two different ids, 18627 and 18625) — trade_name alone is
not unique enough (multiple strengths/dosage forms of the same drug are
separate rows/approvals), so this is the natural stable identifier, the
same role SAHPRA's application_no or TGA's artg_id play for those
crawlers. Uses app.db.check_record_exists_by_json_field(country_id,
'sfda_use_id', ...).
"""

from __future__ import annotations

import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app.db import (
    CountrySkipThresholdReached,
    check_record_exists_by_json_field,
    save_drug_record,
)
from app.config import MAX_RECORDS_PER_COUNTRY

logger = logging.getLogger(__name__)

BASE_URL = os.getenv('SFDA_BASE_URL', 'https://www.sfda.gov.sa')
LIST_URL = f'{BASE_URL}/en/new-sfda-drug-approvals'

DETAIL_WORKERS = int(os.getenv('SFDA_DETAIL_WORKERS', '6'))
REQUEST_TIMEOUT = 30

# Testing override — limit how many listing pages to walk (0 = no limit).
MAX_PAGES = int(os.getenv('SFDA_MAX_PAGES', '0'))

# Listing table column header text -> json_data key (snake_case).
_COLUMN_MAP = {
    'request type': 'request_type',
    'drug type': 'drug_type',
    'trade name': 'trade_name',
    'scientific name': 'scientific_name',
    'strength': 'strength_value',
    'dosage form': 'dosage_form',
    'approval date': 'approval_date',
}

_DETAIL_ID_RE = re.compile(r'/drug-approvals-use/(\d+)')


class SaudiArabiaSFDACrawler:
    """Walks the SFDA new-drug-approvals Views listing + per-row approved-use detail page."""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
        })

    def close(self):
        self._session.close()

    # ------------------------------------------------------------------
    # Top-level crawl
    # ------------------------------------------------------------------

    def process_country(self, country_id: int):
        saved = 0
        page = 0
        limit_reached = False

        while not limit_reached:
            if MAX_PAGES and page >= MAX_PAGES:
                logger.info(f"[SFDA] Reached SFDA_MAX_PAGES={MAX_PAGES}, stopping.")
                break

            rows = self._fetch_list_page(page)
            if not rows:
                logger.info(f"[SFDA] page={page} returned no rows — end of listing")
                break
            logger.info(f"[SFDA] page={page}: {len(rows)} row(s)")

            pending = [
                row for row in rows
                if not check_record_exists_by_json_field(country_id, 'sfda_use_id', row.get('sfda_use_id'))
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
                            f"Failed to process SFDA row: {row.get('sfda_use_id')} "
                            f"({row.get('trade_name')!r})"
                        )

                    if MAX_RECORDS_PER_COUNTRY and saved >= MAX_RECORDS_PER_COUNTRY:
                        logger.info(f"Reached MAX_RECORDS_PER_COUNTRY={MAX_RECORDS_PER_COUNTRY}, stopping.")
                        # See the South Africa crawler for why futures must
                        # be cancelled explicitly rather than relying on the
                        # `with` block's implicit shutdown(wait=True).
                        limit_reached = True
                        pool.shutdown(wait=False, cancel_futures=True)
                        break

            if limit_reached:
                break
            page += 1

        logger.info(f"SFDA crawl finished. Saved/updated {saved} records.")

    # ------------------------------------------------------------------
    # Listing page
    # ------------------------------------------------------------------

    def _fetch_list_page(self, page: int) -> List[dict]:
        html = self._get(LIST_URL, params={'page': page})
        if not html:
            return []
        return self._parse_list_page(html)

    def _parse_list_page(self, html: str) -> List[dict]:
        soup = BeautifulSoup(html, 'html.parser')
        table = soup.find('table', class_='views-table')
        if not table:
            return []

        headers = []
        thead = table.find('thead')
        if thead:
            headers = [
                _COLUMN_MAP.get(th.get_text(' ', strip=True).lower())
                for th in thead.find_all('th')
            ]

        tbody = table.find('tbody')
        if not tbody:
            return []

        rows = []
        for tr in tbody.find_all('tr'):
            cells = tr.find_all('td')
            if not cells:
                continue

            row = {}
            for i, td in enumerate(cells):
                key = headers[i] if i < len(headers) else None
                if key:
                    row[key] = td.get_text(' ', strip=True)

            use_link = tr.find('a', href=_DETAIL_ID_RE)
            if use_link:
                m = _DETAIL_ID_RE.search(use_link['href'])
                row['sfda_use_id'] = m.group(1)
                row['sfda_approved_use_url'] = urljoin(BASE_URL, use_link['href'])
            else:
                row['sfda_use_id'] = None
                row['sfda_approved_use_url'] = None

            if row.get('trade_name'):
                rows.append(row)
        return rows

    # ------------------------------------------------------------------
    # Per-row processing
    # ------------------------------------------------------------------

    def _process_row(self, country_id: int, row: dict) -> bool:
        name = (row.get('trade_name') or 'SFDA Drug Approval').strip()

        approved_use = self._fetch_approved_use(row.get('sfda_approved_use_url'))

        json_data = {
            'request_type': row.get('request_type'),
            'drug_type': row.get('drug_type'),
            'trade_name': row.get('trade_name'),
            'scientific_name': row.get('scientific_name'),
            'strength_value': row.get('strength_value'),
            'dosage_form': row.get('dosage_form'),
            'approval_date': row.get('approval_date') or None,
            'approved_use': approved_use,
            'sfda_use_id': row.get('sfda_use_id'),
            'source_url': row.get('sfda_approved_use_url'),
        }

        # No files on this source — document_url stays empty (see module docstring).
        save_drug_record(name[:255], country_id, None, json_data)
        return True

    def _fetch_approved_use(self, url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        html = self._get(url)
        if not html:
            return None

        soup = BeautifulSoup(html, 'html.parser')
        field = soup.find(class_='field--name-field-sfda-approved-use')
        if not field:
            return None
        return field.get_text('\n', strip=True) or None

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    def _get(self, url: str, params: Optional[dict] = None) -> Optional[str]:
        for attempt in range(3):
            try:
                resp = self._session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                logger.warning(f"SFDA request failed for {url} (attempt {attempt + 1}): {exc}")
                time.sleep(2 * (attempt + 1))
                continue

            if resp.status_code == 200:
                return resp.text
            if resp.status_code in (429, 502, 503, 504):
                wait = 5 * (attempt + 1)
                logger.warning(f"SFDA returned HTTP {resp.status_code} for {url}, waiting {wait}s")
                time.sleep(wait)
                continue

            logger.warning(f"SFDA returned HTTP {resp.status_code} for {url}")
            return None
        return None


# ---------------------------------------------------------------------------
# Standalone test entry-point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import logging as _logging
    from app.db import init_db, get_or_create_country
    from app.crawlers.saudi_arabia import COUNTRY_NAME, COUNTRY_CODE

    _logging.basicConfig(
        level=_logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )

    init_db()
    country_id = get_or_create_country(COUNTRY_NAME, COUNTRY_CODE)

    crawler = SaudiArabiaSFDACrawler()
    try:
        crawler.process_country(country_id)
    finally:
        crawler.close()
