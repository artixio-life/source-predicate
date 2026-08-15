"""
Crawler for United Kingdom — MHRA Products Database (products.mhra.gov.uk)

Navigation (Playwright, real browser — this site is a client-rendered
Next.js app with no static HTML to scrape with plain requests)
-----------------------------------------------------------------
    /substance-index/?letter=<A-Z,0-9>   -> list of active-substance/strain links
    /substance/?substance=<strain name>  -> list of product links using that strain
    /product/?product=<product name>     -> document list for that product

Two-phase crawl
---------------
Phase 1 (`_discover_products`, one browser page, sequential): walks all 36
letters -> every strain page -> collects every unique `/product/?product=`
link. This is a couple thousand navigations at most (letters + strains),
cheap enough to do serially.

Phase 2 (`_process_products_concurrently`): the actual bottleneck — tens of
thousands of product pages, each a full SPA navigation + disclaimer click +
doc-card scrape + document downloads. `BROWSER_WORKERS` (env
`MHRA_BROWSER_WORKERS`, default 4) independent Chromium instances drain a
shared queue of discovered products concurrently. Playwright's sync API is
bound to the thread that started it — a `Page` can't be driven from another
thread — so each worker launches and owns its own browser for its lifetime
rather than sharing one; this is why phase 1 and each phase-2 worker each
call `_new_browser_session()` independently instead of reusing a single
instance-level browser like the old single-page walk did.

Every product page may sit behind a per-product legal disclaimer gate: an
`input#agree-checkbox` that must be checked before its `button` ("Agree",
disabled until checked) can be clicked. Once agreed, the SPA renders a
`div.search-result` card per document, paginated 10-per-page behind a
`button.arrow[aria-label*="next page"]` control (confirmed live: clicking
it re-issues the same query with `$skip` incremented by 10 client-side, no
navigation).

Each card:
    <div class="search-result">
      <dt><p class="icon">SPC|PIL|PAR</p></dt>
      <dd>
        <a class="doc-type-*" href="https://mhraproducts4853.blob.core.windows.net/docs/<hash>">
          <p class="title">...</p><p class="subtitle">filename.pdf</p>
        </a>
        <p class="metadata">File size: ...</p>
        <p class="metadata">Active substances: ...</p>
      </dd>
    </div>

Known site bug — "no results" fallback
---------------------------------------
Confirmed live: the product link text shown on a /substance/ page is
whitespace-normalized by the browser's rendering (React's `innerText`
collapses runs of whitespace), but the underlying `product_name` field the
product page filters on can contain a literal double space (e.g. "VAXIGRIP
SUSPENSION FOR INJECTION IN PRE-FILLED SYRINGE  TRIVALENT INFLUENZA
VACCINE" — two spaces before TRIVALENT). MHRA's own site builds its
`$filter=product_name eq '<name>'` query from the already-collapsed display
text, so it queries for the wrong string and gets zero results / no
disclaimer / "There are no search results for ..." — even though the
document exists. This crawler falls back to a full-text `search=` query
against the same Azure Search index the site itself uses
(`https://mhraproducts4853.search.windows.net/indexes/products-index/docs`,
public read-only key shipped in the site's own JS bundle), then filters
client-side by whitespace-normalized product_name equality, whenever the
on-page navigation turns up zero document cards.

Record mapping
--------------
One row per product (not per document): a product's SPC, PIL, and PAR
documents are collected into a single `document_url` array, ordered SPC
first (then PIL, then PAR, then anything else) per instruction. `name` is
taken from the SPC card's title when an SPC exists, else the first
available card's title, else the product link's own text.

`document_url` holds OUR S3 keys, not MHRA's URLs
--------------------------------------------------
`document_url` (the DB column) stores the bare S3 object key of each
document in *our* `MINIO_BUCKET` mirror (e.g.
"united_kingdom/40/1786695245803_..._TABLETS.pdf") — never the original
MHRA blob URL, and never a full "s3://bucket/key" URI (see
app.storage.upload_file). Each document's original MHRA source URL is kept
separately, at json_data.documents[i].source_url, purely for
provenance/re-download — it is NOT used for dedup (see below), since it
would require a JSONB scan for no real benefit here.

Dedup is by `name`
------------------
One row per distinct /product/?product=<name> page by design, so `name`
(indexed, `source.drug_predicate_raw_records.name`) is a stable, cheap
duplicate check (`app.db.check_record_exists_by_name`) — unlike
`document_url` (a fresh S3 key on every upload, never reproducible, so it
can never match a prior run).
"""

from __future__ import annotations

import logging
import os
import queue
import re
import string
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from app.db import (
    CountrySkipThresholdReached,
    check_record_exists_by_name,
    save_drug_record,
)
from app.storage import upload_file, build_document_key, content_type_for_ext
from app.utils.request_helper import download_with_retries
from app.config import MAX_RECORDS_PER_COUNTRY, DOWNLOAD_DOCUMENTS

logger = logging.getLogger(__name__)

BASE_URL = 'https://products.mhra.gov.uk'

# Comma-separated override for testing a subset, e.g. MHRA_LETTERS=A,B
_letters_env = os.getenv('MHRA_LETTERS', '')
LETTERS = (
    [c.strip().upper() for c in _letters_env.split(',') if c.strip()]
    if _letters_env
    else list(string.ascii_uppercase + string.digits)
)

# Fallback full-text search — same public Azure Search index/key the site's
# own JS bundle uses (see crawler module docstring above for why this exists).
SEARCH_ENDPOINT = os.getenv(
    'MHRA_SEARCH_ENDPOINT',
    'https://mhraproducts4853.search.windows.net/indexes/products-index/docs',
)
SEARCH_API_KEY = os.getenv('MHRA_SEARCH_API_KEY', '17CCFC430C1A78A169B392A35A99C49D')
API_VERSION = '2017-11-11'

NAV_TIMEOUT_MS = 30_000
SETTLE_MS = 1200
MAX_DOC_PAGES_PER_PRODUCT = 50   # safety cap: 50 * 10 = 500 docs/product
REQUEST_TIMEOUT = 30

# Product pages are the bottleneck (tens of thousands of them, each a full
# SPA navigation + disclaimer click + doc-card scrape + document downloads),
# while link discovery (letter index + strain pages) is a couple thousand
# navigations at most. So only the product-processing phase is parallelized.
# Playwright's sync API is bound to the thread that started it — a Page
# can't be driven from another thread — so each worker gets its own
# Chromium instance rather than sharing one. Each instance is a real
# headless browser process; raise cautiously and size docker-compose's
# shm_size accordingly (512m was sized for a single instance).
BROWSER_WORKERS = int(os.getenv('MHRA_BROWSER_WORKERS', '4'))

_DOC_TYPE_ORDER = {'SPC': 0, 'PIL': 1, 'PAR': 2}

_CARD_EXTRACT_JS = """
els => els.map(el => {
    const icon = el.querySelector('p.icon');
    const link = el.querySelector('a[class^="doc-type-"]');
    const title = link ? link.querySelector('p.title') : null;
    const subtitle = link ? link.querySelector('p.subtitle') : null;
    const metas = Array.from(el.querySelectorAll('p.metadata')).map(m => m.innerText.trim());
    return {
        doc_type: icon ? icon.innerText.trim() : null,
        href: link ? link.getAttribute('href') : null,
        title: title ? title.innerText.trim() : null,
        file_name: subtitle ? subtitle.innerText.trim() : null,
        metadata: metas,
    };
})
"""


def _normalize_ws(text: Optional[str]) -> str:
    return re.sub(r'\s+', ' ', text or '').strip()


def _product_name_matches(indexed_product_name: Optional[str], target_normalized: str) -> bool:
    """
    True if `target_normalized` (an already whitespace-normalized product
    name) identifies the same product as the index's `product_name` field.

    Confirmed live: `product_name` is sometimes a single comma-joined field
    covering several brand names that share one licence, e.g.
    "INFLUENZA VACCINE TIV VIATRIS SUSPENSION FOR INJECTION IN PRE-FILLED
    SYRINGE, INFLUVAC SUB-UNIT TIV SUSPENSION FOR INJECTION IN PRE-FILLED
    SYRINGE" — each half is listed as its own separate product link on the
    strain page, but neither half equals the full compound field. A plain
    equality check misses these entirely, so match against the whole
    normalized field OR any one of its comma-separated components.
    """
    if not indexed_product_name:
        return False
    normalized = _normalize_ws(indexed_product_name)
    if normalized == target_normalized:
        return True
    return any(_normalize_ws(part) == target_normalized for part in normalized.split(','))


def _strip_file_count(text: Optional[str]) -> str:
    """Strip a trailing " (N files)" / " (N file)" suffix from a link's display text."""
    return re.sub(r'\s*\(\d+\s*files?\)\s*$', '', _normalize_ws(text), flags=re.I)


def _parse_metadata(metas: List[str]) -> Tuple[Optional[str], List[str]]:
    file_size = None
    active_substances: List[str] = []
    for m in metas:
        low = m.lower()
        if low.startswith('file size'):
            file_size = m.split(':', 1)[1].strip() if ':' in m else m
        elif low.startswith('active substances'):
            rest = m.split(':', 1)[1].strip() if ':' in m else m
            active_substances = [s.strip() for s in rest.split(',') if s.strip()]
    return file_size, active_substances


class UnitedKingdomMHRACrawler:
    """Walks the MHRA Products Database via substance-index -> strain -> product navigation."""

    def __init__(self):
        self._http = requests.Session()

    def close(self):
        self._http.close()

    def _new_browser_session(self):
        """
        A standalone Playwright/browser/context/page, independent of any
        other instance. Used once for link discovery and once per product
        worker — never shared across threads (see BROWSER_WORKERS above).
        """
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
        )
        context = browser.new_context(
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            viewport={'width': 1366, 'height': 900},
        )
        page = context.new_page()
        return pw, browser, context, page

    # ------------------------------------------------------------------
    # Top-level crawl
    # ------------------------------------------------------------------

    def process_country(self, country_id: int):
        pending = self._discover_products()
        logger.info(f"[UK MHRA] {len(pending)} unique product page(s) to process")
        saved = self._process_products_concurrently(country_id, pending)
        logger.info(f"UK MHRA crawl finished. Saved/updated {saved} products "
                    f"({len(pending)} unique product pages discovered).")

    # ------------------------------------------------------------------
    # Phase 1: enumerate every unique product link (letter -> strain -> product)
    # ------------------------------------------------------------------

    def _discover_products(self) -> List[Tuple[str, str]]:
        pw, browser, context, page = self._new_browser_session()
        seen_products: set = set()
        pending: List[Tuple[str, str]] = []
        try:
            for letter in LETTERS:
                strain_links = self._collect_links(page, f'{BASE_URL}/substance-index/?letter={letter}', '/substance/?substance=')
                logger.info(f"[UK MHRA] letter={letter}: {len(strain_links)} strain(s)")

                for strain_href, _strain_text in strain_links:
                    try:
                        product_links = self._collect_links(page, BASE_URL + strain_href, '/product/?product=')
                    except Exception:
                        logger.exception(f"Failed to load strain page: {strain_href}")
                        continue

                    for product_href, product_text in product_links:
                        if product_href in seen_products:
                            continue
                        seen_products.add(product_href)
                        pending.append((product_href, product_text))
        finally:
            context.close()
            browser.close()
            pw.stop()
        return pending

    # ------------------------------------------------------------------
    # Phase 2: process every discovered product, BROWSER_WORKERS browser
    # instances draining a shared queue concurrently
    # ------------------------------------------------------------------

    def _process_products_concurrently(self, country_id: int, pending: List[Tuple[str, str]]) -> int:
        work_queue: "queue.Queue[Tuple[str, str]]" = queue.Queue()
        for item in pending:
            work_queue.put(item)

        state_lock = threading.Lock()
        stop_event = threading.Event()
        state = {'saved': 0, 'error': None}

        def worker():
            pw, browser, context, page = self._new_browser_session()
            try:
                while not stop_event.is_set():
                    try:
                        product_href, product_text = work_queue.get_nowait()
                    except queue.Empty:
                        return

                    try:
                        result = self._process_product(page, country_id, product_href, product_text)
                    except CountrySkipThresholdReached as exc:
                        with state_lock:
                            if state['error'] is None:
                                state['error'] = exc
                        stop_event.set()
                        return
                    except Exception:
                        logger.exception(f"Failed to process product: {product_href}")
                        continue

                    if not result:
                        continue

                    with state_lock:
                        state['saved'] += 1
                        hit_limit = MAX_RECORDS_PER_COUNTRY and state['saved'] >= MAX_RECORDS_PER_COUNTRY
                    if hit_limit:
                        logger.info(f"Reached MAX_RECORDS_PER_COUNTRY={MAX_RECORDS_PER_COUNTRY}, stopping.")
                        stop_event.set()
                        return
            finally:
                context.close()
                browser.close()
                pw.stop()

        with ThreadPoolExecutor(max_workers=BROWSER_WORKERS) as pool:
            futures = [pool.submit(worker) for _ in range(BROWSER_WORKERS)]
            for future in as_completed(futures):
                future.result()

        if state['error'] is not None:
            raise state['error']
        return state['saved']

    # ------------------------------------------------------------------
    # Link collection (letter index page, strain page) — both are single-page
    # renders in practice, but a Next button is honoured defensively.
    # ------------------------------------------------------------------

    def _collect_links(self, page, url: str, href_prefix: str) -> List[Tuple[str, str]]:
        self._goto(page, url)

        seen: set = set()
        results: List[Tuple[str, str]] = []
        for _ in range(MAX_DOC_PAGES_PER_PRODUCT):
            raw = page.eval_on_selector_all(
                f'a[href^="{href_prefix}"]',
                'els => els.map(e => ({text: e.innerText.trim(), href: e.getAttribute("href")}))',
            )
            for item in raw:
                href = item.get('href')
                if not href or href in seen:
                    continue
                seen.add(href)
                results.append((href, _strip_file_count(item.get('text'))))

            next_btn = page.query_selector("button.arrow[aria-label*='next page' i]")
            if not next_btn or not next_btn.is_enabled():
                break
            next_btn.click()
            page.wait_for_timeout(SETTLE_MS)

        return results

    # ------------------------------------------------------------------
    # Product page
    # ------------------------------------------------------------------

    def _process_product(self, page, country_id: int, product_href: str, link_text: str) -> bool:
        self._goto(page, BASE_URL + product_href)
        self._accept_disclaimer_if_present(page)

        cards = self._collect_doc_cards(page)
        if not cards:
            # See module docstring: MHRA's own product link can mismatch the
            # index's stored product_name (whitespace collapse), yielding a
            # false "no results" page. Fall back to the same search index
            # the site itself queries, matched by normalized product_name.
            cards = self._fallback_search_cards(link_text)
            if not cards:
                return False
            logger.info(f"Used fallback search for product (on-page nav returned nothing): {link_text!r}")

        name, documents = self._build_record(link_text, cards)
        if not documents:
            return False

        # Dedup by product name: one row per distinct /product/?product=<name>
        # page by design (see module docstring), and `name` is a stable,
        # indexed column — unlike document_url (our own S3 key, freshly
        # timestamped on every upload, never reproducible) or an individual
        # document's source_url (works, but a slower JSONB scan for no real
        # benefit here since a product is only ever reached under one name).
        if check_record_exists_by_name(country_id, name):
            return False

        if DOWNLOAD_DOCUMENTS:
            self._download_all(country_id, name, documents)

        # document_url is OUR storage location for each document (the bare
        # S3 object key — never a full s3://bucket/key URI, see
        # app.storage.upload_file), ordered SPC-first same as `documents`.
        # A document whose download/upload failed (or DOWNLOAD_DOCUMENTS is
        # off) simply has no s3_path and is omitted here, but its metadata
        # (including source_url, used for dedup above) still lives in
        # json_data.documents so nothing about it is lost.
        s3_keys = [d['s3_path'] for d in documents if d.get('s3_path')]

        json_data = {
            'product_name': link_text,
            'documents': documents,
        }
        save_drug_record(name, country_id, s3_keys, json_data)
        return True

    def _accept_disclaimer_if_present(self, page):
        checkbox = page.query_selector('input#agree-checkbox')
        if not checkbox:
            return
        checkbox.click()
        page.wait_for_timeout(200)
        agree_btn = page.query_selector("button:has-text('Agree')")
        if agree_btn:
            agree_btn.click()
            page.wait_for_timeout(SETTLE_MS)

    def _collect_doc_cards(self, page) -> List[dict]:
        all_cards: List[dict] = []
        for _ in range(MAX_DOC_PAGES_PER_PRODUCT):
            cards = page.eval_on_selector_all('div.search-result', _CARD_EXTRACT_JS)
            all_cards.extend(cards)

            next_btn = page.query_selector("button.arrow[aria-label*='next page' i]")
            if not next_btn or not next_btn.is_enabled():
                break
            next_btn.click()
            page.wait_for_timeout(SETTLE_MS)

        return all_cards

    # ------------------------------------------------------------------
    # Fallback: query the underlying Azure Search index directly
    # ------------------------------------------------------------------

    def _fallback_search_cards(self, product_name: str) -> List[dict]:
        if not product_name:
            return []
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
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.warning(f"Fallback search request failed for {product_name!r}: {exc}")
            return []
        if resp.status_code != 200:
            logger.warning(f"Fallback search returned HTTP {resp.status_code} for {product_name!r}")
            return []

        target = _normalize_ws(product_name)
        cards = []
        for doc in resp.json().get('value', []):
            if not _product_name_matches(doc.get('product_name'), target):
                continue
            size = doc.get('metadata_storage_size')
            substances = doc.get('substance_name') or []
            cards.append({
                'doc_type': (doc.get('doc_type') or '').upper(),
                'href': doc.get('metadata_storage_path'),
                # Mirror the on-page card semantics exactly: the live DOM's
                # p.title is the site's own `product_name` (confirmed via
                # the site's JS: `product: n.product_name` feeds p.title),
                # NOT the raw index `title` field — that field is often
                # filename-like (e.g. "spc-doc_PL 04425-0910.pdf") rather
                # than a clean product name, which would corrupt `name` in
                # _build_record if used here. Use `target` (the requested
                # product name), not the raw indexed field, so a compound
                # entry (see _product_name_matches) surfaces under the name
                # actually being looked up.
                'title': product_name,
                'file_name': doc.get('file_name') or doc.get('title'),
                'metadata': [
                    f"File size: {size} bytes" if size is not None else '',
                    f"Active substances: {', '.join(substances)}" if substances else '',
                ],
            })
        return cards

    # ------------------------------------------------------------------
    # Record assembly
    # ------------------------------------------------------------------

    def _build_record(self, link_text: str, cards: List[dict]) -> Tuple[str, List[Dict]]:
        cards_sorted = sorted(
            cards, key=lambda c: _DOC_TYPE_ORDER.get((c.get('doc_type') or '').upper(), 99)
        )

        documents: List[Dict] = []
        name: Optional[str] = None

        for c in cards_sorted:
            href = c.get('href')
            if not href:
                continue
            doc_type = (c.get('doc_type') or '').upper()
            file_size, active_substances = _parse_metadata(c.get('metadata') or [])

            documents.append({
                'doc_type': doc_type,
                'title': c.get('title'),
                'file_name': c.get('file_name'),
                'file_size': file_size,
                'active_substances': active_substances,
                # The ORIGINAL MHRA blob URL — kept for provenance/download.
                # `document_url` (the DB column) holds OUR s3_path instead,
                # once _download_all populates it below. Dedup is by `name`
                # (see _process_product), not this field.
                'source_url': href,
            })

            if name is None and doc_type == 'SPC':
                name = c.get('title')

        if name is None and documents:
            name = documents[0]['title']
        if not name:
            name = link_text

        return (name or 'MHRA Product')[:255], documents

    # ------------------------------------------------------------------
    # Document download + upload
    # ------------------------------------------------------------------

    def _download_all(self, country_id: int, name: str, documents: List[Dict]):
        for doc in documents:
            url = doc.get('source_url')
            if not url:
                continue
            ext = os.path.splitext(urlparse(url).path)[1].lower() or '.pdf'
            content, content_type, status = download_with_retries(url, retries=3, timeout=60)
            if not content:
                logger.warning(f"Failed to download MHRA document (HTTP {status}): {url}")
                continue
            try:
                key = build_document_key(f"united_kingdom/{country_id}", doc.get('title') or name, ext)
                doc['s3_path'] = upload_file(content, key, content_type or content_type_for_ext(ext))
            except Exception:
                logger.exception(f"Failed to upload MHRA document to S3: {url}")

    # ------------------------------------------------------------------
    # Navigation helper
    # ------------------------------------------------------------------

    def _goto(self, page, url: str):
        try:
            page.goto(url, wait_until='networkidle', timeout=NAV_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            logger.debug(f"networkidle timeout for {url}, proceeding with current DOM state")
        page.wait_for_timeout(SETTLE_MS)


# ---------------------------------------------------------------------------
# Standalone test entry-point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import logging as _logging
    from app.db import init_db, get_or_create_country
    from app.crawlers.united_kingdom import COUNTRY_NAME, COUNTRY_CODE

    _logging.basicConfig(
        level=_logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )

    init_db()
    country_id = get_or_create_country(COUNTRY_NAME, COUNTRY_CODE)

    crawler = UnitedKingdomMHRACrawler()
    try:
        crawler.process_country(country_id)
    finally:
        crawler.close()
