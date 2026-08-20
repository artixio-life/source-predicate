"""
Crawler for Saudi Arabia — SFDA "oldsfda" Registered Drugs API
(https://oldsfda.sfda.gov.sa/en/drugs-list)

Why this source, not www.sfda.gov.sa
-------------------------------------
The previous version of this crawler scraped www.sfda.gov.sa's two Drupal
Views pages (New Drug Approvals + the /ar/drugs-list Human Drugs Registry).
That site has three confirmed-live problems this one doesn't:
  1. Its registry detail endpoint (/ar/details_data) requires a fragile
     POST-per-row fetch that silently returns the WRONG drug's data for a
     large, deterministic fraction of ids (no request-construction fix).
  2. Its listing pagination silently stops advancing past page 928 (13,930
     rows) and re-serves that same last page forever instead of ever
     returning empty — an unbounded crawl of it never terminates.
  3. Its exposed TradeName/RegNo filter form is broken: submitting it
     redirects to a GET with the filter as a query param, but that GET
     silently ignores the param and returns a fixed default page.
This oldsfda.sfda.gov.sa site is a different, older SFDA property with the
same registry data behind a real backend JSON API instead of scraped HTML,
and has none of the three problems above (confirmed live, 2026-08-20 — see
below).

Data source
-----------
  Listing  POST /GetDrugs.php
      Body (all plain form fields; every one may be blank):
        TradeName, scientificName, Agent, ManufacturerName, RegNo, page
      This is a real filter, not cosmetic — confirmed live: posting a
      known RegNo returns exactly that one record (`rowCount: 1`), unlike
      the equivalent-looking but non-functional form on www.sfda.gov.sa.
      We don't use the filter fields for a full crawl (all blank, just
      paginating), but they're documented here since a future targeted
      lookup by registration number can use this endpoint directly instead
      of walking every page.

      Response shape:
        {"code": 200, "data": {"result": {
            "currentPage": N, "pageCount": N, "rowCount": N,
            "firstRowOnPage": N, "lastRowOnPage": N,
            "results": [ {..drug record..}, ... ]
        }}}
      `results` already contains the FULL record inline — registration
      number, both trade names, ATC codes, strength/package/pricing
      fields, ~14 lookup objects (domain, drug type, dosage form, storage
      conditions, marketing/legal/authorization status, etc, each as
      {id, nameEn, nameAr, ...}), the marketing company (+ country), and
      arrays of agents (drugAgents) and manufacturers (drugManufacturers,
      each with its own country). No separate per-row detail fetch is
      needed at all — a first for this country's crawler.

      Confirmed live 2026-08-20: 452 pages, 20 rows/page except a partial
      last page (rowCount 9,039 total). Requesting page > pageCount does
      NOT repeat forever (unlike www.sfda.gov.sa) — the response clamps
      `currentPage` to `pageCount` and keeps returning the same last page,
      but `pageCount` itself is stable and known from page 1's response,
      so the crawl loop bounds on that number rather than on an empty
      page ever appearing.

No document_url
----------------
Every field lives in json_data; there is no attached PDF/label file on
this endpoint either, so document_url is always empty here too.

Dedup
-----
By `registration_number` (`registerNumber` in the API) — SFDA's own
registration identifier, present directly on every listing row (no detail
fetch needed to learn it, unlike the old source). Confirmed unique per
sampled page. Uses
app.db.check_record_exists_by_json_field(country_id, 'registration_number', ...).
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

import requests

from app.db import (
    CountrySkipThresholdReached,
    check_record_exists_by_json_field,
    save_drug_record,
)
from app.config import MAX_RECORDS_PER_COUNTRY

logger = logging.getLogger(__name__)

BASE_URL = os.getenv('SFDA_BASE_URL', 'https://oldsfda.sfda.gov.sa')
LIST_ENDPOINT = f'{BASE_URL}/GetDrugs.php'

PAGE_WORKERS = int(os.getenv('SFDA_PAGE_WORKERS', '6'))
REQUEST_TIMEOUT = 30

# Testing override — limit how many listing pages to walk (0 = no limit).
MAX_PAGES = int(os.getenv('SFDA_MAX_PAGES', '0'))


def _lookup_name(obj: Optional[dict]) -> Optional[str]:
    """Pull the display name out of one of this API's {id, nameEn, nameAr, ...} lookup objects."""
    if not isinstance(obj, dict):
        return None
    return obj.get('nameEn') or obj.get('nameAr') or None


class SaudiArabiaSFDACrawler:
    """Walks the SFDA oldsfda GetDrugs.php JSON listing — no per-row detail fetch needed."""

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
        saved_ref = [0]

        first = self._fetch_page(1)
        if not first:
            logger.error("[SFDA] Could not fetch page 1 — aborting crawl.")
            return
        first_rows, page_count, row_count = first
        if not page_count:
            logger.error("[SFDA] page 1 response had no pageCount — aborting crawl.")
            return
        if MAX_PAGES:
            page_count = min(page_count, MAX_PAGES)
        logger.info(f"[SFDA] rowCount={row_count} pageCount={page_count} (walking {page_count} pages)")

        limit_reached = self._process_rows(country_id, first_rows, saved_ref)

        remaining_pages = list(range(2, page_count + 1))
        if not limit_reached and remaining_pages:
            with ThreadPoolExecutor(max_workers=PAGE_WORKERS) as pool:
                futures = {pool.submit(self._fetch_page, p): p for p in remaining_pages}
                for future in as_completed(futures):
                    page_num = futures[future]
                    try:
                        result = future.result()
                    except Exception:
                        logger.exception(f"[SFDA] Failed to fetch page {page_num}")
                        continue
                    if not result:
                        continue
                    page_rows, _, _ = result

                    limit_reached = self._process_rows(country_id, page_rows, saved_ref)
                    if limit_reached:
                        logger.info(f"Reached MAX_RECORDS_PER_COUNTRY={MAX_RECORDS_PER_COUNTRY}, stopping.")
                        pool.shutdown(wait=False, cancel_futures=True)
                        break

        logger.info(f"[SFDA] crawl finished. Saved/updated {saved_ref[0]} records.")

    def _process_rows(self, country_id: int, rows: List[dict], saved_ref: List[int]) -> bool:
        """Process one page's rows in the calling thread; returns True if MAX_RECORDS_PER_COUNTRY was hit."""
        pending = [
            row for row in rows
            if not check_record_exists_by_json_field(country_id, 'registration_number', row.get('registerNumber'))
        ]
        for row in pending:
            try:
                if self._process_row(country_id, row):
                    saved_ref[0] += 1
            except CountrySkipThresholdReached:
                raise
            except Exception:
                logger.exception(
                    f"Failed to process SFDA row: {row.get('registerNumber')} ({row.get('tradeName')!r})"
                )
            if MAX_RECORDS_PER_COUNTRY and saved_ref[0] >= MAX_RECORDS_PER_COUNTRY:
                return True
        return False

    # ------------------------------------------------------------------
    # Listing page
    # ------------------------------------------------------------------

    def _fetch_page(self, page: int) -> Optional[Tuple[List[dict], Optional[int], Optional[int]]]:
        payload = {
            'TradeName': '',
            'scientificName': '',
            'Agent': '',
            'ManufacturerName': '',
            'RegNo': '',
            'page': str(page),
        }
        for attempt in range(3):
            try:
                resp = self._session.post(LIST_ENDPOINT, data=payload, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                logger.warning(f"[SFDA] list request failed at page={page} (attempt {attempt + 1}): {exc}")
                time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code != 200:
                logger.warning(f"[SFDA] list returned HTTP {resp.status_code} at page={page}")
                time.sleep(2)
                continue
            try:
                body = resp.json()
            except ValueError:
                logger.warning(f"[SFDA] list returned non-JSON at page={page}")
                return None
            result = ((body or {}).get('data') or {}).get('result') or {}
            rows = result.get('results') or []
            return rows, result.get('pageCount'), result.get('rowCount')
        return None

    # ------------------------------------------------------------------
    # Per-row processing
    # ------------------------------------------------------------------

    def _process_row(self, country_id: int, row: dict) -> bool:
        name = (row.get('tradeName') or row.get('scientificName') or 'SFDA Drug').strip()

        agents = [
            _lookup_name(a.get('agent'))
            for a in (row.get('drugAgents') or [])
            if isinstance(a, dict) and _lookup_name(a.get('agent'))
        ]
        manufacturers = [
            {
                'name': _lookup_name(m.get('manufacture')),
                'country': _lookup_name((m.get('manufacture') or {}).get('country')),
            }
            for m in (row.get('drugManufacturers') or [])
            if isinstance(m, dict) and m.get('manufacture')
        ]
        company = row.get('company') or {}

        json_data: Dict = {
            'registration_number': row.get('registerNumber'),
            'sfda_internal_id': row.get('id'),
            'register_year': row.get('registerYear'),
            'old_register_number': row.get('oldRegisterNumber'),
            'reference_number': row.get('referenceNumber'),
            'gtin': row.get('gtin'),
            'certificate_date': row.get('certificateDate'),
            'trade_name': row.get('tradeName'),
            'trade_name_ar': row.get('tradeNameAr'),
            'scientific_name': row.get('scientificName'),
            'atc_code_1': row.get('atcCode1'),
            'atc_code_2': row.get('atcCode2'),
            'package_size': row.get('packageSize'),
            'size': row.get('size'),
            'strength': row.get('strength'),
            'strength_unit': _lookup_name(row.get('strengthUnit')),
            'shelf_life_months': row.get('shelfLife'),
            'cif_price': row.get('cifPrice'),
            'wholesale_price': row.get('wholesalePrice'),
            'price': row.get('price'),
            'public_pricing_date': row.get('publicPricingDate'),
            'domain': _lookup_name(row.get('domain')),
            'drug_type': _lookup_name(row.get('drugType')),
            'drug_branch': _lookup_name(row.get('drugBranch')),
            'package_type': _lookup_name(row.get('packageType')),
            'size_unit': _lookup_name(row.get('sizeUnit')),
            'administration_route': _lookup_name(row.get('administrationRoute')),
            'pharmaceutical_form': _lookup_name(row.get('pharmaceuticalForm')),
            'storage_conditions': _lookup_name(row.get('storageConditions')),
            'marketing_status': _lookup_name(row.get('marketingStatus')),
            'legal_status': _lookup_name(row.get('legalStatus')),
            'product_control': _lookup_name(row.get('productControl')),
            'authorization_status': _lookup_name(row.get('authorizationStatus')),
            'distribution_area': _lookup_name(row.get('distributionArea')),
            'marketing_company': _lookup_name(company),
            'marketing_company_country': _lookup_name(company.get('country')) if isinstance(company, dict) else None,
            'agents': agents,
            'manufacturers': manufacturers,
        }

        # No files on this source — document_url stays empty (see module docstring).
        save_drug_record(name[:255], country_id, None, json_data)
        return True


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
