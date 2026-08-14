"""
Crawler for China — CDE (Center for Drug Evaluation, NMPA) Listed Drug Information
(https://www.cde.org.cn/main/xxgk/listpage/b40868b5e21c038a6aa8b4319d21b07d)

Data source
-----------
A public page (no login) under CDE's "信息公开" (Information Disclosure) ->
"上市药品信息" (Listed Drug Information) section. This is NOT the same as
"受理品种信息" (Accepted Variety Information — a much larger, ~13k/year
table that DOES have a year filter): confirmed live that THAT table's rows
have no click/detail behavior at all (no ondblclick, no attachments) — it's
a plain acceptance log. This "Listed Drug Information" table is smaller
(~1,962 records as of 2026-08) and has no year filter because it isn't
segmented by year at all — every listed drug is shown together, so there's
nothing to iterate by year here.

The whole site sits behind a WAF/anti-bot challenge — confirmed live that
every plain HTTP request (even to unrelated paths) returns HTTP 202 with an
obfuscated JS blob. A real browser (Playwright) gets through it; plain
`requests` cannot.

Row structure (from the page's own art-template, id="listDrugInfoTpl"):
    <tr ondblclick="defaultObj.methods.openListDrugInfoDetail('{{acceptidCODE}}')">
      <td>ROW_ID</td><td>acceptid</td><td>drgnamecn</td><td>drugtype</td>
      <td>registerkind</td><td>companys</td><td>createddate</td>
    </tr>
Double-clicking a row (NOT a plain click — confirmed live) opens
    /main/xxgk/postmarketpage?acceptidCODE=<hash>
in a new tab, showing the same fields plus 公示日期 (disclosure date) and
a list of attachment links — typically 2 (confirmed live with JXHS2500035:
a technical review report PDF and the package insert PDF).

IMPORTANT: navigating directly to a postmarketpage URL (bypassing the list
page) leaves the page missing the inline script that defines `defaultObj`
— confirmed live, attachment clicks then do nothing. The detail page must
be reached via double-click from the list page in the same browser
context for the download handlers to work.

Getting the PDF bytes
----------------------
Confirmed live: clicking an attachment link (`a.textLink[data-fileid]`,
onclick="defaultObj.methods.downloadFile(fileid, acceptid, filename)")
triggers a Playwright `download` event whose URL is a `data:` URI — the
site base64-encodes the file client-side rather than serving a fetchable
URL. Playwright's `download.save_as()` decodes this directly to real PDF
bytes (confirmed: valid `%PDF-1.6` header, correct file size). This
sidesteps needing to fight the WAF for the file itself.

Record mapping
--------------
  name          -> drgnamecn (drug name)
  document_url  -> our S3 keys for each downloaded attachment (bare keys —
                   there isn't a fetchable site URL for these anyway, see
                   above)
  json_data     -> list-row fields (snake_case) + disclosure date from the
                   detail page + a `documents` array (filename, fileid,
                   s3_path per attachment)

Dedup
-----
By `acceptance_no` (e.g. "JXHS2500035") — CDE's own acceptance number,
stable and unique per row — via app.db.check_record_exists_by_json_field.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Dict, List, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from app.db import (
    CountrySkipThresholdReached,
    check_record_exists_by_json_field,
    save_drug_record,
)
from app.storage import upload_file, build_document_key, content_type_for_ext
from app.config import MAX_RECORDS_PER_COUNTRY

logger = logging.getLogger(__name__)

BASE_URL = 'https://www.cde.org.cn'
LIST_URL = f'{BASE_URL}/main/xxgk/listpage/b40868b5e21c038a6aa8b4319d21b07d'

NAV_TIMEOUT_MS = 45_000
SETTLE_MS = 3_000  # this site's WAF challenge + AJAX table render is slow
PAGE_SIZE = 50      # max page size the site's own <select> offers
DOWNLOAD_TIMEOUT_MS = 20_000

_ONDBLCLICK_RE = re.compile(r"openListDrugInfoDetail\('([a-f0-9]+)'\)")

_DETAIL_FIELD_MAP = {
    '受理号': 'acceptance_no',
    '药品名称': 'drug_name',
    '药品类型': 'drug_type',
    '注册分类': 'registration_category',
    '承办日期': 'acceptance_date',
    '公示日期': 'disclosure_date',
    '企业名称': 'company_name',
}


class ChinaCDECrawler:
    """Walks CDE's public 'Listed Drug Information' table + per-row detail/attachments."""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None

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
            viewport={'width': 1400, 'height': 1000},
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
        page = self._context.new_page()
        saved = 0

        try:
            self._goto(page, LIST_URL)
            self._set_page_size(page)

            page_num = 1
            while True:
                rows = self._collect_rows(page)
                logger.info(f"[CDE] page={page_num} rows={len(rows)}")
                if not rows:
                    break

                for row in rows:
                    acceptance_no = row.get('acceptid')
                    if check_record_exists_by_json_field(country_id, 'acceptance_no', acceptance_no):
                        continue
                    try:
                        if self._process_row(page, country_id, row):
                            saved += 1
                    except CountrySkipThresholdReached:
                        raise
                    except Exception:
                        logger.exception(f"Failed to process CDE row {acceptance_no}")

                    if MAX_RECORDS_PER_COUNTRY and saved >= MAX_RECORDS_PER_COUNTRY:
                        logger.info(f"Reached MAX_RECORDS_PER_COUNTRY={MAX_RECORDS_PER_COUNTRY}, stopping.")
                        return

                if not self._go_next_page(page):
                    break
                page_num += 1
        finally:
            page.close()
            logger.info(f"CDE crawl finished. Saved/updated {saved} records.")

    # ------------------------------------------------------------------
    # Listing page
    # ------------------------------------------------------------------

    def _set_page_size(self, page):
        try:
            page.select_option(".layui-laypage-limits select", str(PAGE_SIZE))
            page.wait_for_timeout(SETTLE_MS)
        except Exception:
            logger.debug("Could not set page size; continuing with default", exc_info=True)

    def _collect_rows(self, page) -> List[dict]:
        return page.eval_on_selector_all(
            "table tr[ondblclick]",
            """
            els => els.map(e => {
                const cells = Array.from(e.querySelectorAll('td')).map(td => td.innerText.trim());
                const m = e.getAttribute('ondblclick').match(/openListDrugInfoDetail\\('([a-f0-9]+)'\\)/);
                return {
                    row_id: cells[0], acceptid: cells[1], drgnamecn: cells[2],
                    drugtype: cells[3], registerkind: cells[4], companys: cells[5],
                    createddate: cells[6], acceptidcode: m ? m[1] : null,
                };
            })
            """,
        )

    def _go_next_page(self, page) -> bool:
        next_btn = page.query_selector('a.layui-laypage-next')
        if not next_btn:
            return False
        classes = next_btn.get_attribute('class') or ''
        if 'layui-disabled' in classes:
            return False
        next_btn.click()
        page.wait_for_timeout(SETTLE_MS)
        return True

    # ------------------------------------------------------------------
    # Per-row: open detail (double-click), grab attachments
    # ------------------------------------------------------------------

    def _process_row(self, list_page, country_id: int, row: dict) -> bool:
        code = row.get('acceptidcode')
        if not code:
            return False

        tr = list_page.query_selector(f'tr[ondblclick*="{code}"]')
        if not tr:
            return False

        try:
            with self._context.expect_page(timeout=NAV_TIMEOUT_MS) as pinfo:
                tr.dblclick()
            detail = pinfo.value
            detail.wait_for_load_state('load', timeout=NAV_TIMEOUT_MS)
            detail.wait_for_timeout(SETTLE_MS)
        except PlaywrightTimeoutError:
            logger.warning(f"Detail page did not open for acceptance_no={row.get('acceptid')}")
            return False

        try:
            detail_fields = self._extract_detail_fields(detail)
            documents = self._download_attachments(detail, country_id, row.get('acceptid'))
        finally:
            detail.close()

        name = (row.get('drgnamecn') or detail_fields.get('drug_name') or 'CDE Drug').strip()
        s3_keys = [d['s3_path'] for d in documents if d.get('s3_path')]

        json_data = {
            'serial_number': row.get('row_id'),
            'acceptance_no': row.get('acceptid'),
            'drug_name': row.get('drgnamecn'),
            'drug_type': row.get('drugtype'),
            'registration_category': row.get('registerkind'),
            'company_name': row.get('companys'),
            'acceptance_date': row.get('createddate'),
            'disclosure_date': detail_fields.get('disclosure_date'),
            'documents': documents,
        }
        save_drug_record(name[:255], country_id, s3_keys, json_data)
        return True

    def _extract_detail_fields(self, detail_page) -> Dict[str, str]:
        text = detail_page.inner_text('body')
        fields = {}
        for cn_label, key in _DETAIL_FIELD_MAP.items():
            m = re.search(rf'{cn_label}[:：]\s*([^\t\n]+)', text)
            if m:
                fields[key] = m.group(1).strip()
        return fields

    def _download_attachments(self, detail_page, country_id: int, acceptance_no: Optional[str]) -> List[Dict]:
        anchors = detail_page.query_selector_all("a.textLink[data-fileid]")
        documents: List[Dict] = []
        for a in anchors:
            filename = a.get_attribute('data-filename') or 'attachment.pdf'
            fileid = a.get_attribute('data-fileid')
            doc = {'filename': filename, 'fileid': fileid}
            tmp_path = None
            try:
                with detail_page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as dl_info:
                    a.click()
                dl = dl_info.value
                with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                    tmp_path = tmp.name
                dl.save_as(tmp_path)
                with open(tmp_path, 'rb') as f:
                    content = f.read()

                ext = os.path.splitext(filename)[1].lower() or '.pdf'
                title = os.path.splitext(filename)[0]
                key = build_document_key(f"china/{country_id}", title, ext)
                doc['s3_path'] = upload_file(content, key, content_type_for_ext(ext))
            except Exception:
                logger.exception(
                    f"Failed to download/upload CDE attachment '{filename}' for {acceptance_no}"
                )
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            documents.append(doc)
        return documents

    # ------------------------------------------------------------------
    # Navigation helper
    # ------------------------------------------------------------------

    def _goto(self, page, url: str):
        try:
            page.goto(url, wait_until='load', timeout=NAV_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            logger.debug(f"load timeout for {url}, proceeding with current DOM state")
        page.wait_for_timeout(SETTLE_MS)


# ---------------------------------------------------------------------------
# Standalone test entry-point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import logging as _logging
    from app.db import init_db, get_or_create_country
    from app.crawlers.china import COUNTRY_NAME, COUNTRY_CODE

    _logging.basicConfig(
        level=_logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )

    init_db()
    country_id = get_or_create_country(COUNTRY_NAME, COUNTRY_CODE)

    crawler = ChinaCDECrawler()
    try:
        crawler.process_country(country_id)
    finally:
        crawler.close()
