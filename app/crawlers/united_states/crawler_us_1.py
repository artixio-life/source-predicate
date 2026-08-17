"""
Crawler for United States — Drugs@FDA (accessdata.fda.gov)

Navigation (plain `requests` + BeautifulSoup — confirmed live: this is a
server-rendered ColdFusion app, no browser needed)
-----------------------------------------------------------------
    /scripts/cder/daf/index.cfm?event=browseByLetter.page&productLetter=<A-Z,0-9>
        -> one accordion entry per drug name, each already containing every
           application (ANDA/NDA/BLA) link for that name in the initial HTML.
    /scripts/cder/daf/index.cfm?event=overview.process&ApplNo=<n>
        -> full application detail: products, approval history, labels,
           therapeutic equivalents, and document links.

Two-phase crawl
---------------
Phase 1 (`_discover_applications`, sequential): 27 letter pages (A-Z plus a
single "0-9" bucket — confirmed live, FDA groups all digits into one nav
link, unlike MHRA's 36 individual letters/digits). Collects every unique
ApplNo into a dict, deduped globally (the same application can't reappear
under a different letter in practice, but a plain dict makes that free).

Confirmed live: the big per-letter drug list uses a client-side pagination
plugin (`footable`, `data-page-size="100"`) that only hides rows with
JS/CSS after the page loads — every row for the whole letter (checked
letter "V": 288 accordion entries) is already present in the single GET
response, so no pagination handling is needed here.

Phase 2 (`_process_applications_concurrently`): the bottleneck — tens of
thousands of ApplNo detail pages. Plain `requests`/BeautifulSoup (no
browser state to isolate per thread, unlike MHRA), so a single shared
`requests.Session` drained by `FDA_WORKERS` (default 8) threads is enough
— the same pattern already used by the South Africa/SAHPRA crawler for its
per-row detail fetches.

Detail page parsing
--------------------
Confirmed live (NDA, ANDA, and BLA overview pages, e.g. ApplNo 020892,
060002, 761235):
  - Application type/number/company: the one `<span style="font-size:1.1em">`
    block — `<strong>` holds the type label (e.g. "New Drug Application
    (NDA)"), two `.appl-details-top` spans hold the number and the company.
  - Every other section is a `<table>` with a fixed id: `exampleProd`
    (products), `exampleApplOrig` / `exampleApplSuppl` (approval history),
    `exampleLabels` (labels), and one or more `exampleTEVA*` tables
    (therapeutic equivalents) — all parsed generically via `<thead>` th text
    -> snake_case key, `<tbody>` td text -> value. Confirmed live that older
    applications (e.g. ANDA 060002, approved 1982) can be missing every
    table except `exampleProd` — the parser tolerates any table being absent.

Documents & the "PDF only" storage rule
-----------------------------------------
Every document link on the page (label/letter/review) points at
`https://www.accessdata.fda.gov/drugsatfda_docs/...` and is collected via
one page-wide anchor scan (deduped by href — the same label PDF is often
linked from both the approval-history table and the dedicated "Labels for
..." table). Per the task, only files that are actually PDFs are downloaded
and mirrored to S3; anything else (e.g. a Review link that points at an
`.html` page, or an application with no label on file at all — confirmed
live, e.g. "Label is not available on this site.") is kept as plain
`source_url` metadata in `json_data` and never fetched as a binary.

Record mapping
--------------
One row per ApplNo (an "application" can bundle multiple product names —
see NDA 020892's Products table — so `name` joins every distinct product
name found there, falling back to the browse-by-letter link text for the
rare case a `exampleProd` table is absent).

`document_url` holds OUR S3 keys, not FDA's URLs — see app.storage.upload_file
and the UK/Australia crawlers for the same convention. Each document's
original `drugsatfda_docs` URL is kept separately in
`json_data.documents[i].source_url` for provenance.

Dedup is by `json_data.application_number`
--------------------------------------------
FDA's own application number (from the ApplNo query param, e.g. "020892")
is stable and unique — the natural fit for
`app.db.check_record_exists_by_json_field`, same pattern as SAHPRA/TGA/ANVISA.
`name` isn't usable (many distinct applications share a brand/generic
name), and `document_url` isn't populated for applications with no PDFs.
"""

from __future__ import annotations

import logging
import os
import re
import string
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

from app.db import (
    CountrySkipThresholdReached,
    check_record_exists_by_json_field,
    save_drug_record,
)
from app.storage import upload_file, build_document_key, content_type_for_ext
from app.utils.request_helper import download_with_retries
from app.config import MAX_RECORDS_PER_COUNTRY, DOWNLOAD_DOCUMENTS

logger = logging.getLogger(__name__)

BASE_URL = 'https://www.accessdata.fda.gov'
LETTER_URL = BASE_URL + '/scripts/cder/daf/index.cfm?event=browseByLetter.page&productLetter={letter}'
OVERVIEW_URL = BASE_URL + '/scripts/cder/daf/index.cfm?event=overview.process&ApplNo={appl_no}'
DOCS_URL_PREFIX = BASE_URL + '/drugsatfda_docs/'

# Comma-separated override for testing a subset, e.g. FDA_LETTERS=V,0-9
_letters_env = os.getenv('FDA_LETTERS', '')
LETTERS = (
    [c.strip().upper() for c in _letters_env.split(',') if c.strip()]
    if _letters_env
    else list(string.ascii_uppercase) + ['0-9']
)

# Plain requests + BeautifulSoup (no browser state to isolate), so unlike
# MHRA's per-thread browser instances, FDA_WORKERS threads share one Session
# — the same pattern already used by the South Africa crawler's DETAIL_WORKERS.
WORKERS = int(os.getenv('FDA_WORKERS', '8'))
REQUEST_TIMEOUT = 30

_APPL_TYPE_RE = re.compile(r'(ANDA|NDA|BLA)\s*#?\s*\d+')
_APPL_TYPE_CODE_RE = re.compile(r'\(([A-Z]+)\)')
_DOC_CATEGORY_RE = re.compile(r'Links?\s+to\s+(\w+)', re.I)


def _slugify(text: Optional[str]) -> str:
    slug = re.sub(r'[^a-z0-9]+', '_', (text or '').lower()).strip('_')
    return slug or 'col'


def _parse_table_element(table) -> List[Dict[str, str]]:
    thead = table.find('thead')
    headers = [_slugify(th.get_text(' ', strip=True)) for th in thead.find_all('th')] if thead else []

    tbody = table.find('tbody')
    if not tbody:
        return []

    rows: List[Dict[str, str]] = []
    for tr in tbody.find_all('tr'):
        cells = tr.find_all('td')
        if not cells:
            continue
        row = {
            (headers[i] if i < len(headers) else f'col_{i}'): td.get_text(' ', strip=True)
            for i, td in enumerate(cells)
        }
        rows.append(row)
    return rows


def _parse_table(soup, table_id: str) -> List[Dict[str, str]]:
    table = soup.find('table', id=table_id)
    return _parse_table_element(table) if table else []


def _extract_documents(soup) -> List[Dict[str, Optional[str]]]:
    """
    Every document link on an overview page points at drugsatfda_docs/... —
    collected once, page-wide, deduped by href (the same label PDF commonly
    appears in both the approval-history table and the "Labels for ..."
    table). `is_pdf` decides downloading, not `category` — some categories
    (e.g. Review) are often non-PDF (.html/.cfm) source pages.
    """
    seen = set()
    documents = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if not href.startswith(DOCS_URL_PREFIX) or href in seen:
            continue
        seen.add(href)

        title_attr = a.get('title') or ''
        m = _DOC_CATEGORY_RE.search(title_attr)
        category = (m.group(1).lower() if m else None) or 'document'

        documents.append({
            'category': category,
            'text': a.get_text(strip=True),
            'source_url': href,
            'is_pdf': href.lower().endswith('.pdf'),
        })
    return documents


def _parse_application_header(soup) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    header_span = soup.select_one('span[style*="font-size:1.1em"]')
    if not header_span:
        return None, None, None

    strong = header_span.find('strong')
    application_type_label = strong.get_text(strip=True) if strong else None

    application_type_code = None
    if application_type_label:
        m = _APPL_TYPE_CODE_RE.search(application_type_label)
        application_type_code = m.group(1) if m else None

    detail_spans = header_span.find_all('span', class_='appl-details-top')
    company = detail_spans[1].get_text(strip=True) if len(detail_spans) > 1 else None

    return application_type_label, application_type_code, company


class UnitedStatesFDACrawler:
    """Walks Drugs@FDA via browse-by-letter -> per-application overview pages."""

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
        applications = self._discover_applications()
        logger.info(f"[FDA] {len(applications)} unique application(s) to process")
        saved = self._process_applications_concurrently(country_id, applications)
        logger.info(f"FDA crawl finished. Saved/updated {saved} applications "
                    f"({len(applications)} discovered).")

    # ------------------------------------------------------------------
    # Phase 1: enumerate every unique ApplNo (letter -> drug name -> application)
    # ------------------------------------------------------------------

    def _discover_applications(self) -> Dict[str, dict]:
        applications: Dict[str, dict] = {}
        for letter in LETTERS:
            html = self._fetch_html(LETTER_URL.format(letter=letter))
            if not html:
                logger.warning(f"[FDA] Failed to fetch letter page: {letter}")
                continue

            soup = BeautifulSoup(html, 'html.parser')
            before = len(applications)

            for ul in soup.find_all('ul', id=re.compile(r'^drugName\d+$')):
                header_a = ul.find_previous_sibling('a')
                accordion_name = header_a.get_text(strip=True) if header_a else None

                for li in ul.find_all('li'):
                    a = li.find('a', href=re.compile(r'ApplNo='))
                    if not a or not a.get('href'):
                        continue
                    appl_no = (parse_qs(urlparse(a['href']).query).get('ApplNo') or [None])[0]
                    if not appl_no or appl_no in applications:
                        continue

                    parts = [p.strip() for p in li.get_text(' ', strip=True).split('|')]
                    type_match = _APPL_TYPE_RE.search(parts[1]) if len(parts) > 1 else None

                    applications[appl_no] = {
                        'display_name': a.get_text(strip=True),
                        'accordion_name': accordion_name,
                        'application_type_code': type_match.group(1) if type_match else None,
                        'dosage_form_route': parts[2] if len(parts) > 2 else None,
                        'company': ' | '.join(parts[3:]).strip() if len(parts) > 3 else None,
                    }

            logger.info(f"[FDA] letter={letter}: {len(applications) - before} new application(s), "
                        f"{len(applications)} total")
        return applications

    # ------------------------------------------------------------------
    # Phase 2: process every discovered application, WORKERS threads sharing
    # one requests.Session (no browser state to isolate, unlike MHRA)
    # ------------------------------------------------------------------

    def _process_applications_concurrently(self, country_id: int, applications: Dict[str, dict]) -> int:
        saved = 0
        error = None

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {
                pool.submit(self._process_application, country_id, appl_no, info.get('display_name')): appl_no
                for appl_no, info in applications.items()
            }
            for future in as_completed(futures):
                appl_no = futures[future]
                try:
                    if future.result():
                        saved += 1
                except CountrySkipThresholdReached as exc:
                    error = exc
                    # Same reasoning as SAHPRA: exiting the `with` block
                    # normally calls shutdown(wait=True), which would block
                    # until every already-submitted future runs — here that
                    # could be tens of thousands. Cancel everything not yet
                    # started; only futures already mid-flight (bounded by
                    # WORKERS) still run to completion.
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

    # ------------------------------------------------------------------
    # Per-application overview page
    # ------------------------------------------------------------------

    def _process_application(self, country_id: int, appl_no: str, fallback_name: Optional[str]) -> bool:
        # Dedup before fetching the detail page — cheap, avoids a wasted GET
        # on reruns (same pattern as SAHPRA's application_no check).
        if check_record_exists_by_json_field(country_id, 'application_number', appl_no):
            return False

        overview_url = OVERVIEW_URL.format(appl_no=appl_no)
        html = self._fetch_html(overview_url)
        if not html:
            logger.warning(f"Failed to fetch FDA overview page for ApplNo={appl_no}")
            return False

        soup = BeautifulSoup(html, 'html.parser')
        application_type_label, application_type_code, company = _parse_application_header(soup)

        products = _parse_table(soup, 'exampleProd')
        original_approvals = _parse_table(soup, 'exampleApplOrig')
        supplements = _parse_table(soup, 'exampleApplSuppl')
        labels = _parse_table(soup, 'exampleLabels')

        therapeutic_equivalents: List[Dict[str, str]] = []
        for te_table in soup.find_all('table', id=re.compile(r'^exampleTEVA')):
            therapeutic_equivalents.extend(_parse_table_element(te_table))

        documents = _extract_documents(soup)

        product_names = list(dict.fromkeys(
            p['drug_name'].strip() for p in products if p.get('drug_name') and p['drug_name'].strip()
        ))
        name = '; '.join(product_names) or fallback_name or f'FDA Application {appl_no}'

        if DOWNLOAD_DOCUMENTS:
            self._download_documents(country_id, appl_no, documents)

        s3_keys = [d['s3_path'] for d in documents if d.get('s3_path')]

        json_data = {
            'application_number': appl_no,
            'application_type': application_type_label,
            'application_type_code': application_type_code or None,
            'company': company,
            'products': products,
            'approval_history': {
                'original_approvals': original_approvals,
                'supplements': supplements,
            },
            'labels': labels,
            'therapeutic_equivalents': therapeutic_equivalents,
            'documents': documents,
            'source_url': overview_url,
        }
        save_drug_record(name, country_id, s3_keys, json_data)
        return True

    # ------------------------------------------------------------------
    # Document download + upload — PDFs only, per the storage rule (see
    # module docstring): non-PDF links (e.g. an .html Review page, or a
    # supplement with no label at all) stay as source_url-only metadata.
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

            try:
                # FDA's own filename (e.g. "020892s019lbl") already encodes
                # the application number and submission — a clear, stable
                # slug, so reuse it rather than composing one from scratch.
                stem = os.path.splitext(os.path.basename(urlparse(url).path))[0] or f'{appl_no}_document'
                key = build_document_key(f"united_states/{country_id}", stem, '.pdf')
                doc['s3_path'] = upload_file(content, key, content_type or content_type_for_ext('.pdf'))
            except Exception:
                logger.exception(f"Failed to upload FDA document to S3: {url}")

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    def _fetch_html(self, url: str) -> Optional[str]:
        for attempt in range(3):
            try:
                resp = self._session.get(url, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                logger.warning(f"FDA request failed for {url} (attempt {attempt + 1}): {exc}")
                time.sleep(2 * (attempt + 1))
                continue

            if resp.status_code == 200:
                return resp.text
            if resp.status_code in (429, 502, 503, 504):
                wait = 5 * (attempt + 1)
                logger.warning(f"FDA returned HTTP {resp.status_code} for {url}, waiting {wait}s")
                time.sleep(wait)
                continue

            # Log enough to tell a real "not found" apart from a WAF/CDN
            # block page returning a 404 to avoid revealing itself: the
            # final URL after any redirect, a couple of infra-identifying
            # headers, and a short body snippet.
            snippet = (resp.text or '')[:300].replace('\n', ' ').strip()
            logger.warning(
                f"FDA returned HTTP {resp.status_code} for {url} "
                f"(final_url={resp.url!r}, server={resp.headers.get('Server')!r}, "
                f"via={resp.headers.get('Via')!r}, cf_ray={resp.headers.get('CF-RAY')!r}): "
                f"{snippet!r}"
            )
            return None
        return None


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
