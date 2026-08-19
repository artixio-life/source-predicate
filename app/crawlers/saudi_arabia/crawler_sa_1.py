"""
Crawler for Saudi Arabia — two independent SFDA sources

  1. New Drug Approvals   https://www.sfda.gov.sa/en/new-sfda-drug-approvals
     A small, rolling list of recent approvals with an indication/approved-
     use text field this source has and the registry below doesn't.
  2. Human Drugs Registry  https://www.sfda.gov.sa/ar/drugs-list
     The comprehensive national registry (~20,925 rows, confirmed live) —
     registration number, manufacturer, agents, ATC codes, pricing, etc.
     This source has no indication/approved-use text at all.

Both are plain server-rendered Drupal sites (Views module) — no browser
needed. They're combined into one crawler (this is the ONLY crawler class
registered for Saudi Arabia — see app/crawlers/__init__.py's one-
crawler-per-country registry) rather than kept as two separate scripts,
the same way the US crawler combines multiple openFDA/TSV sources into one
process_country. Each writes rows with its own dedup key (see below) so
neither can collide with or overwrite the other.

Source 1: New Drug Approvals
-----------------------------
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

  Dedup: `sfda_use_id` — the numeric id in each row's own
  `/en/drug-approvals-use/<id>` link. Confirmed live this is per-row
  unique (e.g. two different strengths of the same trade name, "Brevie"
  25mg and 50mg, link to two different ids, 18627 and 18625) — trade_name
  alone is not unique enough.

Source 2: Human Drugs Registry — ARABIC LOCALE ONLY, and why
--------------------------------------------------------------
  Listing  GET /ar/drugs-list?page=<N>   (0-indexed)
      Same shape as source 1's listing but a different, much larger view:
      15 rows/page, Scientific Name / Trade Name / Strength / Dosage Form
      / Price / a "Details" link to /ar/details_data?nid=17582&id=<N>.
      Confirmed live: 1,395 pages (page=0..1394), ~20,925 rows.

  Detail   POST /ar/details_data?nid=17582&id=<N>&page=<N>
      Confirmed live: the ENGLISH version of this exact endpoint
      (/en/details_data) returns only 6 fields (ATC codes, description
      code, authorization/marketing status, price) — a real gap in SFDA's
      own English rendering, not a fetch/parsing issue on our side. The
      Arabic version of the identical id returns the FULL ~28-field
      table (registration number, trade/scientific name, strength+unit,
      dosage form, route, pack type/size, dispensing method, monitoring,
      distribution, shelf life, storage conditions, manufacturer +
      country, marketing company + country, three agent fields, two ATC
      codes, a drug formulation code, license/marketing status, price).
      This crawler therefore fetches BOTH listing and detail in Arabic —
      matching field labels are mapped to English snake_case keys via
      _REGISTRY_DETAIL_FIELD_MAP.

      THREE confirmed traps on this one endpoint, in the order they were
      found — each looks like success (HTTP 200, a real-looking table) but
      silently returns the WRONG drug's data:
        1. Method: a plain GET to this exact URL doesn't error, it
           silently ignores `id` entirely and always returns the same
           fixed row ("ZETRON 250 MG CAPSULE") — verified across three
           real ids and one nonexistent one. Must be POST (confirmed live
           via `drupalSettings.ajax` on the listing page: every row's
           link is registered with `"httpMethod":"POST"`).
        2. Page scoping: the lookup is keyed by `(id, page)` TOGETHER, not
           `id` alone — the same `id` with the wrong `page` also silently
           returns a different, unrelated row. Fixed by always reusing a
           row's own already-correct `sfda_detail_url` verbatim (built
           from that row's own href) rather than reconstructing params —
           see _fetch_registry_detail.
        3. Per-id server-side failures, WIDESPREAD, not an edge case:
           even with the correct (id, page) pair reused verbatim, a large
           fraction of ids still return an unrelated row's detail — 35/75
           (~47%) in a sampled sequential run across pages 0-4, so this is
           common throughout the dataset, not just near the tail (page
           1380+ was where it was FIRST noticed, not where it's
           concentrated). Confirmed DETERMINISTIC per id, not flaky/random:
           one specific failing id was retried 8 times in a row and
           returned the exact same wrong row every time, and trying it
           against 9 different `page` values never once resolved
           correctly either — so this is not a (id, page)-pairing mistake
           on our end, it's specific underlying records that this
           endpoint's own lookup logic cannot serve correctly, for reasons
           opaque from outside SFDA's system (plausibly: a query keyed on
           something other than a stable primary key, silently falling
           back to a default/first row on a lookup miss). No request
           construction fixes this.

           _fetch_registry_detail_verified cross-checks the fetched
           detail's own trade_name against the listing row's (already
           known correct) trade_name, retries once — cheap, and harmless
           even though it does NOT fix the deterministic failures above;
           it exists in case some other, genuinely transient cause also
           exists — and gives up to an empty `detail` rather than ever
           persisting data attributed to the wrong drug. Practically: this
           source's rich ~28-field detail should be expected to be missing
           (row saved with `detail: {}`, only the 5 listing-level fields
           present) for roughly HALF of all rows, based on the sampled
           rate above — not a rare fallback path.

      Confirmed live: several fields (`strength_unit`, `package_type`,
      `dispensing_method`, `monitoring`, `storage_conditions`,
      `license_status`, `marketing_status`) come back as the literal
      string "Array" for essentially every row sampled — a PHP
      array-to-string bug on SFDA's own site (they're rendering an
      unresolved array value directly into the table cell). The real
      value is unrecoverable from this endpoint; stored as-is ("Array")
      rather than silently discarded, so it's visibly distinguishable
      from a genuinely absent field (empty string).

  Dedup: `sfda_drug_id` — the numeric `id` in each row's own
  `/ar/details_data?...&id=<N>` link, checked BEFORE the detail fetch so
  reruns skip already-ingested rows cheaply. A different id-space from
  source 1's `sfda_use_id` (confirmed live: e.g. 13064/13085 here vs.
  18638/18636 for source 1) but even if the raw integers ever coincided,
  dedup is scoped by field NAME via app.db.check_record_exists_by_json_field,
  so the two sources can never collide. `sfda_drug_id` is a site artifact,
  not a regulatory identifier, and is only used here because the real one
  (`registration_number`) isn't known until AFTER the (expensive, and per
  the traps above, sometimes unverifiable) detail fetch it would need to
  gate — it's promoted to a top-level json_data field once fetched, the
  same role SAHPRA's application_no or TGA's artg_id play for those
  crawlers, even though it can't be the dedup key here.

No document_url on either source
----------------------------------
Neither source has an attached file (no SPC/label PDF-equivalent) — every
row is pure structured text, so `document_url` is always empty; everything
lives in `json_data`.
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

# --- Source 2: Human Drugs Registry (Arabic locale) ------------------------

REGISTRY_LIST_URL = f'{BASE_URL}/ar/drugs-list'
REGISTRY_DETAIL_URL = f'{BASE_URL}/ar/details_data'
REGISTRY_NID = os.getenv('SFDA_REGISTRY_NID', '17582')

REGISTRY_DETAIL_WORKERS = int(os.getenv('SFDA_REGISTRY_DETAIL_WORKERS', '8'))
# Testing override — limit how many registry listing pages to walk (0 = no limit).
REGISTRY_MAX_PAGES = int(os.getenv('SFDA_REGISTRY_MAX_PAGES', '0'))

# Arabic listing table column header text -> json_data key.
_REGISTRY_COLUMN_MAP = {
    'الاسم العلمي': 'scientific_name',
    'الاسم التجاري': 'trade_name',
    'التركيز': 'strength_value',
    'الشكل الصيدلاني': 'dosage_form',
    'السعر': 'price',
}

# Arabic detail-table row label -> json_data key. See module docstring for
# which of these are confirmed live to come back as the literal string
# "Array" due to a bug on SFDA's own site (stored as-is either way).
_REGISTRY_DETAIL_FIELD_MAP = {
    'رقم التسجيل': 'registration_number',
    'الاسم التجاري': 'trade_name',
    'الاسم العلمي': 'scientific_name',
    'التركيز': 'strength_value',
    'وحدة التركيز': 'strength_unit',
    'الشكل الصيدلاني': 'dosage_form',
    'طريقة الإستعمال': 'route_of_administration',
    'الحجم': 'volume',
    'وحدة الحجم': 'volume_unit',
    'نوع العبوة': 'package_type',
    'حجم العبوة': 'package_size',
    'طريقة الصرف': 'dispensing_method',
    'المراقبة': 'monitoring',
    'مكان التوزيع': 'distribution_place',
    'مدة الصلاحية': 'shelf_life_months',
    'ظروف التخزين': 'storage_conditions',
    'الشركة الصانعة': 'manufacturer',
    'بلد التصنيع': 'manufacturing_country',
    'الشركة المسوقة': 'marketing_company',
    'بلد الشركة المسوقة': 'marketing_company_country',
    'الوكيل الأول': 'agent_1',
    'الوكيل الثاني': 'agent_2',
    'الوكيل الثالث': 'agent_3',
    'رمز التصنيف الكيميائي العلاجي التشريحي 1': 'atc_code_1',
    'رمز التصنيف الكيميائي العلاجي التشريحي 2': 'atc_code_2',
    'رمز التركيبة الدوائية': 'drug_formulation_code',
    'حالة الترخيص': 'license_status',
    'حالة التسويق': 'marketing_status',
    'السعر': 'price',
}

_REGISTRY_DETAIL_ID_RE = re.compile(r'[?&]id=(\d+)')


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
        new_approvals_saved = self._crawl_new_approvals(country_id)
        registry_saved = self._crawl_drugs_registry(country_id)
        logger.info(
            f"SFDA crawl finished. New approvals: {new_approvals_saved} saved/updated. "
            f"Drugs registry: {registry_saved} saved/updated."
        )

    # ------------------------------------------------------------------
    # Source 1: New Drug Approvals
    # ------------------------------------------------------------------

    def _crawl_new_approvals(self, country_id: int) -> int:
        saved = 0
        page = 0
        limit_reached = False

        while not limit_reached:
            if MAX_PAGES and page >= MAX_PAGES:
                logger.info(f"[SFDA] Reached SFDA_MAX_PAGES={MAX_PAGES}, stopping new-approvals crawl.")
                break

            rows = self._fetch_list_page(page)
            if not rows:
                logger.info(f"[SFDA] new-approvals page={page} returned no rows — end of listing")
                break
            logger.info(f"[SFDA] new-approvals page={page}: {len(rows)} row(s)")

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
                            f"Failed to process SFDA new-approvals row: {row.get('sfda_use_id')} "
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

        logger.info(f"[SFDA] New-approvals crawl finished. Saved/updated {saved} records.")
        return saved

    # ------------------------------------------------------------------
    # Source 2: Human Drugs Registry (Arabic locale — see module docstring)
    # ------------------------------------------------------------------

    def _crawl_drugs_registry(self, country_id: int) -> int:
        saved = 0
        page = 0
        limit_reached = False

        while not limit_reached:
            if REGISTRY_MAX_PAGES and page >= REGISTRY_MAX_PAGES:
                logger.info(f"[SFDA] Reached SFDA_REGISTRY_MAX_PAGES={REGISTRY_MAX_PAGES}, stopping registry crawl.")
                break

            rows = self._fetch_registry_list_page(page)
            if not rows:
                logger.info(f"[SFDA] registry page={page} returned no rows — end of listing")
                break
            logger.info(f"[SFDA] registry page={page}: {len(rows)} row(s)")

            pending = [
                row for row in rows
                if not check_record_exists_by_json_field(country_id, 'sfda_drug_id', row.get('sfda_drug_id'))
            ]

            with ThreadPoolExecutor(max_workers=REGISTRY_DETAIL_WORKERS) as pool:
                futures = {pool.submit(self._process_registry_row, country_id, row): row for row in pending}
                for future in as_completed(futures):
                    row = futures[future]
                    try:
                        if future.result():
                            saved += 1
                    except CountrySkipThresholdReached:
                        raise
                    except Exception:
                        logger.exception(
                            f"Failed to process SFDA registry row: {row.get('sfda_drug_id')} "
                            f"({row.get('trade_name')!r})"
                        )

                    if MAX_RECORDS_PER_COUNTRY and saved >= MAX_RECORDS_PER_COUNTRY:
                        logger.info(f"Reached MAX_RECORDS_PER_COUNTRY={MAX_RECORDS_PER_COUNTRY}, stopping.")
                        limit_reached = True
                        pool.shutdown(wait=False, cancel_futures=True)
                        break

            if limit_reached:
                break
            page += 1

        logger.info(f"[SFDA] Drugs-registry crawl finished. Saved/updated {saved} records.")
        return saved

    def _fetch_registry_list_page(self, page: int) -> List[dict]:
        html = self._get(REGISTRY_LIST_URL, params={'page': page})
        if not html:
            return []
        return self._parse_registry_list_page(html)

    def _parse_registry_list_page(self, html: str) -> List[dict]:
        soup = BeautifulSoup(html, 'html.parser')
        table = soup.find('table')
        if not table:
            return []

        headers = []
        thead = table.find('thead')
        if thead:
            headers = [
                _REGISTRY_COLUMN_MAP.get(th.get_text(strip=True))
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

            detail_link = tr.find('a', href=_REGISTRY_DETAIL_ID_RE)
            if detail_link:
                m = _REGISTRY_DETAIL_ID_RE.search(detail_link['href'])
                row['sfda_drug_id'] = m.group(1)
                row['sfda_detail_url'] = urljoin(BASE_URL, detail_link['href'])
            else:
                row['sfda_drug_id'] = None
                row['sfda_detail_url'] = None

            if row.get('trade_name'):
                rows.append(row)
        return rows

    def _process_registry_row(self, country_id: int, row: dict) -> bool:
        name = (row.get('trade_name') or 'SFDA Registered Drug').strip()

        detail = self._fetch_registry_detail_verified(row)

        json_data = {
            'scientific_name': row.get('scientific_name'),
            'trade_name': row.get('trade_name'),
            'strength_value': row.get('strength_value'),
            'dosage_form': row.get('dosage_form'),
            'price': row.get('price'),
            'sfda_drug_id': row.get('sfda_drug_id'),
            # Promoted to top level (in addition to living in `detail`
            # below) so this crawler's real regulatory identifier is as
            # visible/conventional as SAHPRA's application_no or TGA's
            # artg_id — even though `sfda_drug_id` (not this) is what
            # dedup actually keys on, since registration_number isn't
            # known until AFTER the detail fetch it would need to gate.
            'registration_number': detail.get('registration_number'),
            'source_url': row.get('sfda_detail_url'),
            'detail': detail,
        }

        # No files on this source — document_url stays empty (see module docstring).
        save_drug_record(name[:255], country_id, None, json_data)
        return True

    def _fetch_registry_detail_verified(self, row: dict) -> Dict[str, str]:
        """
        Fetch this row's detail and verify it actually belongs to THIS row
        before trusting it, retrying once on a mismatch.

        Confirmed live: the registry's (id, page)-scoped detail lookup is
        not reliably deterministic near the tail of the dataset — the
        exact same (id, page) pair returned a DIFFERENT, unrelated drug's
        detail across separate requests in testing (a row whose own
        trade_name was "Keytruda" came back with "ZETRON 250 MG CAPSULE"'s
        detail block instead) — on top of the two other confirmed traps
        this endpoint has (GET silently ignores `id` entirely; the wrong
        `page` for a given `id` also silently returns someone else's
        data — see _fetch_registry_detail). Comparing the fetched detail's
        own trade_name against the listing row's trade_name (already known
        correct — it came from this same row) is a cheap, reliable check:
        a genuine fetch for this row always echoes its own trade_name.

        One retry is attempted — a second request for the identical URL
        sometimes returns the correct result, consistent with a
        non-deterministic sort on SFDA's own side rather than a stable
        cache. If both attempts mismatch, the row is saved with an empty
        `detail` (listing-derived fields are kept) rather than data
        attributed to the wrong drug, and a warning is logged so this is
        visible in the crawl log rather than silently wrong.
        """
        detail_url = row.get('sfda_detail_url')
        expected_name = (row.get('trade_name') or '').strip().casefold()

        detail: Dict[str, str] = {}
        for attempt in range(2):
            detail = self._fetch_registry_detail(detail_url)
            if not detail:
                return {}
            fetched_name = (detail.get('trade_name') or '').strip().casefold()
            if not expected_name or fetched_name == expected_name:
                return detail
            logger.warning(
                f"[SFDA] registry detail mismatch for id={row.get('sfda_drug_id')} "
                f"(expected trade_name={row.get('trade_name')!r}, got {detail.get('trade_name')!r}) "
                f"— {'retrying' if attempt == 0 else 'giving up, saving without detail'}"
            )
        return {}

    def _fetch_registry_detail(self, detail_url: Optional[str]) -> Dict[str, str]:
        """
        Takes the row's OWN full `sfda_detail_url` (already correctly built
        in _parse_registry_list_page from that row's own href) rather than
        reconstructing `?nid=...&id=...&page=...` from parts. Confirmed
        live this matters: the server's lookup is scoped by BOTH `id` and
        `page` together — id=877 with the wrong page (0 instead of its
        real page, 50) silently returns id=1625's content ("ZETRON 250 MG
        CAPSULE") instead of erroring, the exact same trap as the GET-vs-POST
        one above. Reusing the row's own URL verbatim is the only way to
        guarantee `page` always matches, since a caller reconstructing the
        params has to already know which listing page this row came from.
        """
        if not detail_url:
            return {}
        html = self._get(detail_url, method='POST')
        if not html:
            return {}
        return self._parse_registry_detail_html(html)

    def _parse_registry_detail_html(self, html: str) -> Dict[str, str]:
        soup = BeautifulSoup(html, 'html.parser')
        table = soup.find('table', class_='table-striped')
        if not table:
            return {}

        detail: Dict[str, str] = {}
        for tr in table.find_all('tr'):
            th = tr.find('th')
            td = tr.find('td')
            if th is None or td is None:
                continue
            key = _REGISTRY_DETAIL_FIELD_MAP.get(th.get_text(strip=True))
            if key:
                detail[key] = td.get_text(' ', strip=True)
        return detail

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

    def _get(self, url: str, params: Optional[dict] = None, method: str = 'GET') -> Optional[str]:
        """
        `method='POST'` is required for the registry's details_data
        endpoint — confirmed live via `drupalSettings.ajax` on the listing
        page (each row's link is registered with `"httpMethod":"POST"`).
        A plain GET to the identical URL doesn't 404 or error; it silently
        returns the SAME fixed content regardless of `id` — confirmed live
        across three different real ids (13064, 13085, 1625) and even a
        nonexistent one (999), all returning "ZETRON 250 MG CAPSULE". POST
        with the same params/headers correctly returns each id's own row.
        This is a real, easy-to-miss trap: a GET-based crawl would silently
        persist ~20,925 identical, wrong detail blocks rather than erroring.
        """
        request = self._session.post if method == 'POST' else self._session.get
        for attempt in range(3):
            try:
                resp = request(url, params=params, timeout=REQUEST_TIMEOUT)
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
