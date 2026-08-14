"""
Crawler for Australia — TGA ARTG (Australian Register of Therapeutic Goods)
via the "ARTG Search Visualisation Tool" (https://compliance.health.gov.au/artg/)

Why this instead of www.tga.gov.au directly
--------------------------------------------
`www.tga.gov.au/resources/artg` (the Drupal frontend with the filterable
listing + `/resources/artg/<id>` detail pages) sits behind **Akamai Bot
Manager**. Confirmed blocked from this dev sandbox AND the user's own
Docker container on their own network (identical block message both times,
while the user's own regular browser reaches it fine) — the common factor
being headless Playwright-controlled Chromium, not the network. That
approach is abandoned.

Instead: `compliance.health.gov.au/artg/` — TGA's own "ARTG Search
Visualisation Tool", a Power BI report embedded in a Dynamics 365/Power
Pages portal — has **no Akamai protection at all** (confirmed live,
repeatedly). It has a full searchable data grid for Medicines (~34,392
unique ARTG IDs) with a built-in bulk "Export data" feature that returns
every field as an Excel file — no per-row page visits needed for metadata
at all.

Two-step data flow
-------------------
1. **Bulk metadata** (Playwright, one-time per crawl run):
   navigate -> click "Medicines" (left nav, inside the `reportEmbed`
   iframe) -> the report ships with a stray default text-filter value
   pre-applied (confirmed live: filters down to 1 row) -> clear it via the
   dedicated `button[name="clear-button"]` + `button[name="search-button"]`
   inside the visual's own sandboxed iframe (`cvSandboxPack.cshtml`) ->
   confirmed "Search Results (34390 ARTG entries)" -> hover the results
   table -> click its "More options" (⋯) icon -> click the "Export data"
   menu item (NOT matchable by `text=Export data` alone — that string is
   also a *substring* of the "Hover here to see more options (export
   data, full-page table, etc.)" hint text sitting at the same screen
   position, so it silently matches the wrong element; use
   `[role="menuitem"]` filtered to exact text instead) -> "Data with
   current layout" -> Export. Confirmed live: an async server-side export
   (shows an "Exporting data..." toast) that resolves to a real
   `.xlsx` download after roughly a minute, with 32 columns covering every
   field the site's own detail page shows (ingredients, sponsor, dosage
   form, route of administration, poison schedule, warnings, indications,
   pack size, storage conditions, and three document-availability columns:
   Public Summary / Consumer-Patient Info / Product Info — populated as
   the literal string "View", not a link; the export does not carry an
   actual URL for them).

2. **PDF documents** (plain `requests`, no browser, no Akamai): confirmed
   live on TWO different ARTG IDs (530062, 530052) that
       https://www.ebs.tga.gov.au/servlet/xmlmillr6
           ?dbid=ebs/PublicHTML/pdfStore.nsf&docid=<ARTG_ID>
           &agid=(PrintDetailsPublic)&actionid=1
   — a legacy Lotus Domino system, a completely different host from the
   Akamai-protected frontend — returns the Public Summary PDF directly,
   with `docid` simply being the ARTG ID. This covers 36,849 of 36,851
   exported rows (virtually all Medicines). The Product Info / Consumer
   Medicine Info documents (~10,300 rows, the "Registered" prescription
   subset) use some other URL not yet identified — not fetched here.

Row -> record mapping
----------------------
The export is a denormalized table: one ARTG ID can span multiple rows
(one per component/dosage-form/pack-size combination). Rows are grouped by
`ARTG ID`; fields that are constant per product (name, sponsor, category,
warnings, etc.) are taken from the first row, and fields that can vary per
component (active/excipient ingredients, dosage form, route, pack size,
container details, indications) are kept as a `components` list so no
per-component detail is discarded.

  name          -> Product Name
  document_url  -> our S3 key for the downloaded Public Summary PDF (bare
                   key, never a full s3://bucket/key URI — see
                   app.storage.upload_file), empty if no PDF or download failed
  json_data     -> every exported field, grouped as described above, plus
                   `s3_path` when the PDF was downloaded

Dedup
-----
By `artg_id` (the ARTG ID itself, unique per product) via
app.db.check_record_exists_by_json_field(country_id, 'artg_id', ...).
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from typing import Dict, List, Optional

import openpyxl
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from app.db import (
    CountrySkipThresholdReached,
    check_record_exists_by_json_field,
    save_drug_record,
)
from app.storage import upload_file, build_document_key, content_type_for_ext
from app.config import MAX_RECORDS_PER_COUNTRY

logger = logging.getLogger(__name__)

ARTG_TOOL_URL = 'https://compliance.health.gov.au/artg/'
EBS_PDF_URL = (
    'https://www.ebs.tga.gov.au/servlet/xmlmillr6'
    '?dbid=ebs/PublicHTML/pdfStore.nsf&docid={artg_id}&agid=(PrintDetailsPublic)&actionid=1'
)

NAV_TIMEOUT_MS = 45_000
SETTLE_MS = 9_000       # this Power BI report is slow to render after navigation/clicks
SHORT_SETTLE_MS = 1_200
EXPORT_TIMEOUT_MS = 150_000  # confirmed live: the async export can take ~a minute for 34k+ rows
REQUEST_TIMEOUT = 30

# Fields that are the same for every row sharing an ARTG ID.
_PRODUCT_LEVEL_FIELDS = {
    'Sponsor Name': 'sponsor_name',
    'ARTG Category': 'artg_category',
    'Approval Area': 'approval_area',
    'Therapeutic Type': 'therapeutic_type',
    'Start Date': 'start_date',
    'Black Triangle Scheme': 'black_triangle_scheme',
    'Product Type': 'product_type',
    'Effective Date': 'effective_date',
    'Indication Requirements': 'indication_requirements',
    'Warnings': 'warnings',
    'Additional Product Information': 'additional_product_information',
    'Poison Schedule': 'poison_schedule',
    'Storage Conditions': 'storage_conditions',
}

# Fields that can vary per component/dosage-form row within the same ARTG ID.
_COMPONENT_LEVEL_FIELDS = {
    'Active Ingredients': 'active_ingredients',
    'Excipient Ingredients': 'excipient_ingredients',
    'Indications': 'indications',
    'Conditions': 'conditions',
    'Container Type': 'container_type',
    'Container Material': 'container_material',
    'Container Life Time': 'container_life_time',
    'Container Temperature': 'container_temperature',
    'Container Closure': 'container_closure',
    'Pack Size': 'pack_size',
    'Component Name': 'component_name',
    'Dosage Form': 'dosage_form',
    'Route of Administration': 'route_of_administration',
    'Visual Identification': 'visual_identification',
}


def _fmt(value):
    """openpyxl returns date cells as datetime objects; make them JSON-serializable."""
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return value


class AustraliaTGACrawler:
    """Bulk-exports TGA ARTG Medicines metadata via Power BI, fetches PDFs directly."""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._http = requests.Session()
        self._http.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
            ),
        })

    def close(self):
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            logger.debug('Error while closing Playwright browser', exc_info=True)
        finally:
            self._context = None
            self._browser = None
            self._playwright = None
        self._http.close()

    def _ensure_browser(self):
        if self._browser is not None:
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
            viewport={'width': 1800, 'height': 1200},
            accept_downloads=True,
        )
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

    # ------------------------------------------------------------------
    # Top-level crawl
    # ------------------------------------------------------------------

    def process_country(self, country_id: int):
        self._ensure_browser()

        xlsx_path = self._export_medicines_data()
        if not xlsx_path:
            logger.error("Failed to export TGA ARTG Medicines data; aborting.")
            return

        try:
            records = self._parse_export(xlsx_path)
        finally:
            try:
                os.unlink(xlsx_path)
            except OSError:
                pass

        logger.info(f"[TGA ARTG] Parsed {len(records)} unique ARTG products from export")

        saved = 0
        for artg_id, record in records.items():
            if check_record_exists_by_json_field(country_id, 'artg_id', artg_id):
                continue
            try:
                if self._process_record(country_id, artg_id, record):
                    saved += 1
            except CountrySkipThresholdReached:
                raise
            except Exception:
                logger.exception(f"Failed to process ARTG record {artg_id}")

            if MAX_RECORDS_PER_COUNTRY and saved >= MAX_RECORDS_PER_COUNTRY:
                logger.info(f"Reached MAX_RECORDS_PER_COUNTRY={MAX_RECORDS_PER_COUNTRY}, stopping.")
                return

        logger.info(f"TGA ARTG crawl finished. Saved/updated {saved} records.")

    # ------------------------------------------------------------------
    # Step 1: bulk export via Power BI (Playwright)
    # ------------------------------------------------------------------

    def _export_medicines_data(self) -> Optional[str]:
        page = self._context.new_page()
        try:
            self._goto(page, ARTG_TOOL_URL, settle_ms=SETTLE_MS)

            pbi_frame = self._find_frame(page, 'reportEmbed')
            if not pbi_frame:
                logger.error("Power BI reportEmbed iframe not found")
                return None

            med_el = pbi_frame.query_selector("text=Medicines")
            if not med_el:
                logger.error("'Medicines' nav item not found in report")
                return None
            med_el.click()
            page.wait_for_timeout(SETTLE_MS)

            # The report ships with a stray default text-filter applied
            # (confirmed live: filters down to a single row) — clear it via
            # the visual's own sandboxed iframe, not the outer report frame.
            sandbox_frame = self._find_frame(page, 'cvSandboxPack')
            if sandbox_frame:
                clear_btn = sandbox_frame.query_selector('button[name="clear-button"]')
                search_btn = sandbox_frame.query_selector('button[name="search-button"]')
                if clear_btn and search_btn:
                    clear_btn.click()
                    page.wait_for_timeout(1000)
                    search_btn.click()
                    page.wait_for_timeout(SETTLE_MS)
            else:
                logger.warning("Text-filter sandbox iframe not found; results may be filtered unexpectedly")

            table_hint = pbi_frame.query_selector("text=Hover here to see more options")
            if not table_hint:
                logger.error("Results table not found (hover hint missing)")
                return None
            table_hint.scroll_into_view_if_needed()
            page.wait_for_timeout(1000)

            grid = pbi_frame.query_selector('[role="grid"]')
            if not grid:
                logger.error("Results grid not found")
                return None
            gbox = grid.bounding_box()
            page.mouse.move(gbox['x'] + gbox['width'] / 2, gbox['y'] - 5)
            page.wait_for_timeout(1000)

            # Multiple "More options" buttons exist on the page (one per
            # visual, e.g. the Filters panel has its own) — the correct one
            # sits at the same y-position as the results table's own hint.
            hint_box = table_hint.bounding_box()
            candidates = pbi_frame.query_selector_all('[aria-label="More options"]')
            more_btn = next(
                (el for el in candidates if abs(el.bounding_box()['y'] - hint_box['y']) < 5), None
            )
            if not more_btn:
                logger.error("Table's 'More options' button not found")
                return None
            more_btn.click(force=True)
            page.wait_for_timeout(SHORT_SETTLE_MS)

            # `text=Export data` also matches the hint text above (it's a
            # substring of "...more options (export data, full-page
            # table...)"), so filter role=menuitem by exact text instead.
            menu_items = pbi_frame.query_selector_all('[role="menuitem"]')
            export_item = next(
                (mi for mi in menu_items if mi.inner_text().strip() == 'Export data'), None
            )
            if not export_item:
                logger.error("'Export data' menu item not found")
                return None
            export_item.click(force=True)
            page.wait_for_timeout(2000)

            export_btn = pbi_frame.query_selector('button:has-text("Export")')
            if not export_btn:
                logger.error("Export confirmation dialog's 'Export' button not found")
                return None

            with page.expect_download(timeout=EXPORT_TIMEOUT_MS) as dl_info:
                export_btn.click(force=True)
            download = dl_info.value

            tmp_path = os.path.join(tempfile.gettempdir(), f'tga_artg_medicines_{int(time.time() * 1000)}.xlsx')
            download.save_as(tmp_path)
            logger.info(f"[TGA ARTG] Export downloaded to {tmp_path}")
            return tmp_path
        finally:
            page.close()

    def _find_frame(self, page, url_substring: str):
        for f in page.frames:
            if url_substring in f.url:
                return f
        return None

    def _goto(self, page, url: str, settle_ms: int = SETTLE_MS):
        try:
            page.goto(url, wait_until='load', timeout=NAV_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            logger.debug(f"load timeout for {url}, proceeding with current DOM state")
        page.wait_for_timeout(settle_ms)

    # ------------------------------------------------------------------
    # Step 2: parse the exported workbook into per-product records
    # ------------------------------------------------------------------

    def _parse_export(self, xlsx_path: str) -> Dict[str, dict]:
        wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
        ws = wb['Export']
        rows = ws.iter_rows(values_only=True)

        header = next(rows)
        col = {name: i for i, name in enumerate(header)}

        def cell(row, name):
            # openpyxl's read-only mode trims trailing empty cells per row,
            # so a row can be shorter than the header when its last columns
            # are blank — treat any out-of-range column as None, not an error.
            idx = col[name]
            return row[idx] if idx < len(row) else None

        records: Dict[str, dict] = {}
        for row in rows:
            artg_id_raw = cell(row, 'ARTG ID')
            if artg_id_raw is None:
                continue
            artg_id = str(artg_id_raw)

            if artg_id not in records:
                record = {
                    'artg_id': artg_id,
                    'product_name': cell(row, 'Product Name'),
                    'has_public_summary': cell(row, 'Public Summary') == 'View',
                    'has_consumer_patient_info': cell(row, 'Consumer/Patient Info') == 'View',
                    'has_product_info': cell(row, 'Product Info') == 'View',
                    'components': [],
                }
                for xlsx_name, key in _PRODUCT_LEVEL_FIELDS.items():
                    record[key] = _fmt(cell(row, xlsx_name))
                records[artg_id] = record

            component = {}
            for xlsx_name, key in _COMPONENT_LEVEL_FIELDS.items():
                component[key] = _fmt(cell(row, xlsx_name))
            records[artg_id]['components'].append(component)

        return records

    # ------------------------------------------------------------------
    # Per-product processing
    # ------------------------------------------------------------------

    def _process_record(self, country_id: int, artg_id: str, record: dict) -> bool:
        name = (record.get('product_name') or f'ARTG {artg_id}').strip()

        s3_path = None
        if record.get('has_public_summary'):
            s3_path = self._download_pdf(country_id, name, artg_id)

        json_data = dict(record)
        if s3_path:
            json_data['s3_path'] = s3_path

        document_url = [s3_path] if s3_path else []
        save_drug_record(name[:255], country_id, document_url, json_data)
        return True

    def _download_pdf(self, country_id: int, name: str, artg_id: str) -> Optional[str]:
        url = EBS_PDF_URL.format(artg_id=artg_id)
        try:
            resp = self._http.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            logger.warning(f"ARTG PDF request failed for {artg_id}: {exc}")
            return None
        if resp.status_code != 200 or not resp.content:
            logger.warning(f"ARTG PDF returned HTTP {resp.status_code} for {artg_id}")
            return None

        content_type = (resp.headers.get('Content-Type') or '').split(';')[0].strip().lower()
        if 'pdf' not in content_type and not resp.content.startswith(b'%PDF'):
            logger.warning(f"ARTG PDF response for {artg_id} doesn't look like a PDF (Content-Type={content_type})")
            return None

        try:
            key = build_document_key(f"australia/{country_id}", f"{name}_{artg_id}", '.pdf')
            return upload_file(resp.content, key, content_type_for_ext('.pdf'))
        except Exception:
            logger.exception(f"Failed to upload ARTG PDF for {artg_id}")
            return None


# ---------------------------------------------------------------------------
# Standalone test entry-point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import logging as _logging
    from app.db import init_db, get_or_create_country
    from app.crawlers.australia import COUNTRY_NAME, COUNTRY_CODE

    _logging.basicConfig(
        level=_logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )

    init_db()
    country_id = get_or_create_country(COUNTRY_NAME, COUNTRY_CODE)

    crawler = AustraliaTGACrawler()
    try:
        crawler.process_country(country_id)
    finally:
        crawler.close()
