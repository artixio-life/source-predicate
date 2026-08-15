"""
Crawler for Brazil — ANVISA Medicamentos consultation
(https://consultas.anvisa.gov.br/#/medicamentos/)

Data source
-----------
consultas.anvisa.gov.br is an AngularJS SPA sitting on a plain REST/JSON
backend — confirmed live via captured network traffic, no HTML scraping
needed:

  List    GET /api/consulta/medicamento/produtos/
              ?column=&count=<N>&page=<P>&order=asc
              &filter[checkNotificado]=false&filter[checkRegistrado]=true
          Same two filters as the site's own default listing
          (.../#/medicamentos/q/?checkNotificado=false&checkRegistrado=true).
          Response: {content: [...], totalPages, totalElements, ...}.
          As of 2026-08: 32,653 total products.

  Detail  GET /api/consulta/medicamento/produtos/codigo/<codigo>
          Full structured record: company, process, every apresentacao
          (presentation/registration), anexoRotulos (labeling PDF
          metadata), and an `existeBula` flag.

  Bulario GET /api/consulta/bulario
              ?column=&count=10&page=1&order=asc
              &filter[numeroRegistro]=<numeroRegistro>
          Only meaningful when the detail's `existeBula` is true (in
          practice: actively-registered products). Returns a short-lived
          (~5 min) `idBulaProfissionalProtegido` token per entry.

  PDF     GET /api/consulta/medicamentos/arquivo/bula/parecer/<token>/?Authorization=
          Downloads the "Bula do Profissional" (professional package
          insert) PDF directly. Must be fetched immediately after the
          Bulario call above, before the token's ~5 minute window closes.

Every one of these requires the header `Authorization: Guest` (confirmed
live: omitting it returns HTTP 500 `mensagens.MSG-004`) — Angular's own
$http interceptor adds it automatically, so a bare fetch/requests call
needs it added explicitly.

Why Playwright, and why NOT FlareSolverr
-----------------------------------------
consultas.anvisa.gov.br sits behind a Cloudflare WAF that hard-blocks any
non-browser client: confirmed live that neither plain `requests` nor
`curl_cffi` (Chrome-124 TLS/JA3 impersonation) gets past it — both return
Cloudflare's static 403 "Attention Required" page, even when replaying the
exact cookies a solved FlareSolverr session produced. FlareSolverr itself
launches a real (if separately-patched) browser to solve the challenge,
but that's no different in kind from just driving Playwright's own
headless Chromium directly, which is confirmed live to pass this WAF
cleanly on every endpoint above with zero extra work. So — unlike
`app/crawlers/brazil` in source-information (a different ANVISA property,
a Plone CMS under www.gov.br/anvisa, which really does need FlareSolverr)
— this crawler drives Playwright directly and never touches FlareSolverr.

The real obstacle: a Cloudflare rate-limit rule
-------------------------------------------------
Confirmed live: bursting above roughly 25-30 requests in quick succession
(from a single IP, regardless of client) trips a genuine Cloudflare rate
limit — HTTP 429 with `Retry-After: 600` (a 10-minute penalty box) — not
an application-level throttle, and not something FlareSolverr or a
different HTTP client would sidestep, since it's enforced at Cloudflare's
edge for the origin/IP pair. Sequential, one-request-at-a-time traffic
paced ~1s apart ran 100% clean in testing. So this crawler deliberately
runs single-lane (no concurrency) with a small delay between every API
call, and treats a 429 as a first-class signal: sleep for exactly the
`Retry-After` duration, then resume. A full crawl of ~32k products
necessarily takes several hours; reruns are cheap afterwards since
already-ingested products are skipped via dedup.

Record mapping
--------------
  name          -> detail's `nomeComercial` (falls back to the listing
                   row's `produto.nome`)
  document_url  -> our S3 key for the downloaded "Bula do Profissional"
                   PDF (see below), empty if the product has no bulário
                   entry or `DOWNLOAD_DOCUMENTS=false`
  json_data     -> the entire detail API response (as-is: company,
                   process, every apresentacao, anexoRotulos metadata),
                   plus the raw listing row and a `bulario` list (one
                   entry per bulário record: expediente, transaction id,
                   publication date, and `s3_path` once uploaded)

Only the "Bula do Profissional" PDF is downloaded (per instruction) — the
"Bula do Paciente" (patient leaflet) and the labeling PDFs
(`anexoRotulos`) are recorded as metadata only (already part of the raw
detail response stored in json_data), not fetched.

Dedup
-----
By `codigo_produto` (ANVISA's own stable per-product id, the `codigo`
used throughout the API above) via
app.db.check_record_exists_by_json_field — most products have no
downloaded PDF (no bulário entry), so `document_url` can't serve as the
dedup key the way it does for the UK/Australia crawlers.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from app.db import (
    CountrySkipThresholdReached,
    check_record_exists_by_json_field,
    save_drug_record,
)
from app.storage import upload_file, build_document_key, content_type_for_ext
from app.config import MAX_RECORDS_PER_COUNTRY, DOWNLOAD_DOCUMENTS

logger = logging.getLogger(__name__)

BASE_URL = 'https://consultas.anvisa.gov.br'
LIST_ENDPOINT = f'{BASE_URL}/api/consulta/medicamento/produtos/'
DETAIL_ENDPOINT = f'{BASE_URL}/api/consulta/medicamento/produtos/codigo'
BULARIO_ENDPOINT = f'{BASE_URL}/api/consulta/bulario'
PARECER_ENDPOINT = f'{BASE_URL}/api/consulta/medicamentos/arquivo/bula/parecer'

PAGE_SIZE = int(os.getenv('ANVISA_PAGE_SIZE', '200'))
# Deliberately conservative — see module docstring on the confirmed
# Cloudflare rate-limit rule (429 + Retry-After: 600) this API enforces.
REQUEST_DELAY_SECONDS = float(os.getenv('ANVISA_REQUEST_DELAY_SECONDS', '1.2'))
NAV_TIMEOUT_MS = 45_000
SETTLE_MS = 4_000
MAX_RETRIES = 5
DEFAULT_RETRY_AFTER = 600  # seconds; the penalty observed live when a response has no Retry-After header

_AUTH_HEADERS = {'authorization': 'Guest'}

_FETCH_TEXT_JS = """
async ({url, headers}) => {
    try {
        const r = await fetch(url, {headers: headers || {}});
        const text = await r.text();
        return {status: r.status, text, retryAfter: r.headers.get('retry-after')};
    } catch (e) {
        return {status: 0, text: String(e), retryAfter: null};
    }
}
"""

# btoa() on the full binary string in one call blows the JS call-stack for
# multi-hundred-KB PDFs — build the base64 string chunk-by-chunk instead.
_FETCH_BINARY_JS = """
async ({url, headers}) => {
    try {
        const r = await fetch(url, {headers: headers || {}});
        if (!r.ok) {
            return {status: r.status, b64: null, contentType: null, retryAfter: r.headers.get('retry-after')};
        }
        const buf = await r.arrayBuffer();
        const bytes = new Uint8Array(buf);
        let binary = '';
        const chunkSize = 0x8000;
        for (let i = 0; i < bytes.length; i += chunkSize) {
            binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
        }
        return {status: r.status, b64: btoa(binary), contentType: r.headers.get('content-type'), retryAfter: null};
    } catch (e) {
        return {status: 0, b64: null, contentType: null, retryAfter: null};
    }
}
"""


def _list_url(page_num: int) -> str:
    params = {
        'column': '',
        'count': PAGE_SIZE,
        'filter[checkNotificado]': 'false',
        'filter[checkRegistrado]': 'true',
        'order': 'asc',
        'page': page_num,
    }
    return f'{LIST_ENDPOINT}?{urlencode(params)}'


def _bulario_url(numero_registro: str) -> str:
    params = {
        'column': '',
        'count': 10,
        'filter[numeroRegistro]': numero_registro,
        'order': 'asc',
        'page': 1,
    }
    return f'{BULARIO_ENDPOINT}?{urlencode(params)}'


class BrazilANVISACrawler:
    """Walks the ANVISA Medicamentos API — listing, per-product detail, and Bula do Profissional PDFs."""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def close(self):
        try:
            if self._page:
                self._page.close()
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            logger.debug('Error while closing Playwright browser', exc_info=True)
        finally:
            self._page = self._context = self._browser = self._playwright = None

    def _ensure_page(self):
        if self._page is not None:
            return
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled'],
        )
        self._context = self._browser.new_context(
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
            ),
            viewport={'width': 1600, 'height': 1000},
        )
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        self._page = self._context.new_page()
        try:
            self._page.goto(f'{BASE_URL}/#/', wait_until='load', timeout=NAV_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            logger.debug("load timeout on initial navigation, proceeding with current DOM state")
        self._page.wait_for_timeout(SETTLE_MS)

    # ------------------------------------------------------------------
    # Rate-limit-aware request helpers — see module docstring: this API
    # sits behind a real Cloudflare rate-limit rule (429 + Retry-After:
    # 600), so every call goes through here, single-lane and paced, and
    # honours Retry-After exactly rather than guessing at a backoff.
    # ------------------------------------------------------------------

    def _get_json(self, url: str) -> Optional[dict]:
        text = self._get_text(url)
        if text is None:
            return None
        try:
            return json.loads(text)
        except ValueError:
            logger.warning(f"Non-JSON response from {url}: {text[:200]!r}")
            return None

    def _get_text(self, url: str) -> Optional[str]:
        for attempt in range(MAX_RETRIES):
            result = self._page.evaluate(_FETCH_TEXT_JS, {'url': url, 'headers': _AUTH_HEADERS})
            status = result.get('status')
            time.sleep(REQUEST_DELAY_SECONDS)

            if status == 200:
                return result.get('text')
            if status == 429:
                wait = self._parse_retry_after(result.get('retryAfter'))
                logger.warning(f"Rate limited (429) on {url}; sleeping {wait}s per Retry-After (attempt {attempt + 1}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            if status == 0 or (isinstance(status, int) and 500 <= status < 600):
                wait = 5 * (attempt + 1)
                logger.warning(f"Request failed (status={status}) for {url}; retrying in {wait}s (attempt {attempt + 1}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            logger.warning(f"Unexpected status {status} for {url}: {result.get('text', '')[:200]!r}")
            return None
        logger.error(f"Exhausted retries for {url}")
        return None

    def _get_binary_base64(self, url: str) -> Optional[Dict[str, str]]:
        for attempt in range(MAX_RETRIES):
            result = self._page.evaluate(_FETCH_BINARY_JS, {'url': url, 'headers': {}})
            status = result.get('status')
            time.sleep(REQUEST_DELAY_SECONDS)

            if status == 200 and result.get('b64'):
                return {'b64': result['b64'], 'content_type': result.get('contentType') or 'application/pdf'}
            if status == 429:
                wait = self._parse_retry_after(result.get('retryAfter'))
                logger.warning(f"Rate limited (429) downloading {url}; sleeping {wait}s (attempt {attempt + 1}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            if status == 0 or (isinstance(status, int) and 500 <= status < 600):
                wait = 5 * (attempt + 1)
                time.sleep(wait)
                continue
            logger.warning(f"Failed to download {url}: status={status}")
            return None
        logger.error(f"Exhausted retries downloading {url}")
        return None

    @staticmethod
    def _parse_retry_after(value) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return DEFAULT_RETRY_AFTER

    # ------------------------------------------------------------------
    # Top-level crawl
    # ------------------------------------------------------------------

    def process_country(self, country_id: int):
        self._ensure_page()

        saved = 0
        page_num = 1
        total_pages: Optional[int] = None

        while total_pages is None or page_num <= total_pages:
            listing = self._get_json(_list_url(page_num))
            if listing is None:
                logger.error(f"[ANVISA] Failed to fetch listing page={page_num}; stopping.")
                break

            total_pages = listing.get('totalPages')
            rows = listing.get('content') or []
            logger.info(f"[ANVISA] page={page_num}/{total_pages} rows={len(rows)}")
            if not rows:
                break

            for row in rows:
                produto = row.get('produto') or {}
                codigo = produto.get('codigo')
                if codigo is None:
                    continue

                if check_record_exists_by_json_field(country_id, 'codigo_produto', codigo):
                    continue

                try:
                    if self._process_product(country_id, codigo, row):
                        saved += 1
                except CountrySkipThresholdReached:
                    raise
                except Exception:
                    logger.exception(f"Failed to process ANVISA product codigo={codigo}")

                if MAX_RECORDS_PER_COUNTRY and saved >= MAX_RECORDS_PER_COUNTRY:
                    logger.info(f"Reached MAX_RECORDS_PER_COUNTRY={MAX_RECORDS_PER_COUNTRY}, stopping.")
                    logger.info(f"ANVISA crawl finished. Saved/updated {saved} records.")
                    return

            page_num += 1

        logger.info(f"ANVISA crawl finished. Saved/updated {saved} records.")

    # ------------------------------------------------------------------
    # Per-product processing
    # ------------------------------------------------------------------

    def _process_product(self, country_id: int, codigo: int, listing_row: dict) -> bool:
        detail = self._get_json(f'{DETAIL_ENDPOINT}/{codigo}')
        if detail is None:
            return False

        produto = listing_row.get('produto') or {}
        name = (detail.get('nomeComercial') or produto.get('nome') or 'ANVISA Product').strip()

        json_data = dict(detail)
        json_data['codigo_produto'] = codigo
        json_data['listing_row'] = listing_row

        document_url: List[str] = []
        if detail.get('existeBula'):
            bulario_entries, s3_keys = self._process_bulario(country_id, name, detail.get('numeroRegistro'), codigo)
            if bulario_entries:
                json_data['bulario'] = bulario_entries
            document_url.extend(s3_keys)

        save_drug_record(name[:255], country_id, document_url, json_data)
        return True

    def _process_bulario(
        self, country_id: int, name: str, numero_registro: Optional[str], codigo: int
    ) -> Tuple[List[dict], List[str]]:
        entries: List[dict] = []
        s3_keys: List[str] = []
        if not numero_registro:
            return entries, s3_keys

        bulario = self._get_json(_bulario_url(numero_registro))
        if not bulario:
            return entries, s3_keys

        for item in bulario.get('content') or []:
            # Safety net: numeroRegistro isn't guaranteed globally unique —
            # only trust bulário entries that actually point back at this
            # product's own codigo.
            if item.get('idProduto') != codigo:
                continue

            entry = {
                'expediente': item.get('expediente'),
                'numero_transacao': item.get('numeroTransacao'),
                'data': item.get('data'),
                'data_atualizacao': item.get('dataAtualizacao'),
            }

            token = item.get('idBulaProfissionalProtegido')
            if token and DOWNLOAD_DOCUMENTS:
                # Fetched immediately, not deferred/batched — the token is
                # short-lived (~5 min), see module docstring.
                s3_path = self._download_bula_profissional(country_id, name, codigo, token)
                if s3_path:
                    entry['s3_path'] = s3_path
                    s3_keys.append(s3_path)

            entries.append(entry)

        return entries, s3_keys

    def _download_bula_profissional(self, country_id: int, name: str, codigo: int, token: str) -> Optional[str]:
        result = self._get_binary_base64(f'{PARECER_ENDPOINT}/{token}/?Authorization=')
        if not result:
            return None
        try:
            content = base64.b64decode(result['b64'])
            key = build_document_key(f"brazil/{country_id}", f"{name}_{codigo}_bula_profissional", '.pdf')
            return upload_file(content, key, content_type_for_ext('.pdf'))
        except Exception:
            logger.exception(f"Failed to upload Bula do Profissional PDF for codigo={codigo}")
            return None


# ---------------------------------------------------------------------------
# Standalone test entry-point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import logging as _logging
    from app.db import init_db, get_or_create_country
    from app.crawlers.brazil import COUNTRY_NAME, COUNTRY_CODE

    _logging.basicConfig(
        level=_logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )

    init_db()
    country_id = get_or_create_country(COUNTRY_NAME, COUNTRY_CODE)

    crawler = BrazilANVISACrawler()
    try:
        crawler.process_country(country_id)
    finally:
        crawler.close()
