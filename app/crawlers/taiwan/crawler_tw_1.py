"""
Crawler for Taiwan — TFDA "All Drug Product Approval Data Set"
(data.gov.tw dataset https://data.gov.tw/en/datasets/9122)

Data source
-----------
The dataset's "Visit this site" links point at data.fda.gov.tw's open-data
export endpoint for dataset id 36:
    https://data.fda.gov.tw/data/opendata/export/36/json
Despite the `/json` path and the site's own listing advertising this as a
JSON download, the response body is actually a ZIP archive (confirmed live
2026-08-20: magic bytes `PK\x03\x04`) containing a single member named like
`36_5.json` (the numeric suffix is a revision counter that changes between
snapshots, so we don't hardcode it — we just take whichever `.json` member
is in the archive). That member is a flat JSON array, no pagination,
~72k entries as of this writing, each with Traditional Chinese keys, e.g.:
    {
      "許可證字號": "...",      # license number
      "中文品名": "...",        # Chinese product name
      "英文品名": "...",        # English product name
      "製造商名稱": "...",      # manufacturer name
      "製造廠廠址": "...",      # manufacturer site address
      ...
    }

One license, multiple manufacturing-site rows
-----------------------------------------------
`許可證字號` (license number) is NOT a unique row key on its own — a single
license appears once per manufacturing site (raw material vs. packaging
vs. secondary packaging plants, etc; confirmed live: one license had 8 rows,
identical in every field except manufacturer name/address/country/process).
This crawler groups all rows sharing a license number and folds the
per-site fields into a `manufacturers` array on a single saved record,
matching the pattern used for the Saudi Arabia crawler's drugManufacturers
list. All non-manufacturer fields are confirmed identical across a group's
rows, so the first row supplies them.

Dedup
-----
By `license_number` (`許可證字號`), post-grouping. Uses
app.db.check_record_exists_by_json_field(country_id, 'license_number', ...).

No document_url
----------------
This export has no attached PDF/label file per record, so document_url is
always empty here.

packaging_barcode is currently always empty
---------------------------------------------
`包裝與國際條碼` ("packaging and international barcode") is a real column in
the schema, but confirmed live (2026-08-21, full scan of all 72,013 rows)
it is null on every single row. `包裝` ("packaging") is populated on ~93%
of rows but only ever holds a container-type descriptor (e.g. 瓶裝
"bottled", 管裝 "tube", 安瓿 "ampoule") — not a pack size or barcode value.
So there is no actual barcode data obtainable from this source today; we
still map both fields through in case TFDA starts populating the barcode
column later.
"""

from __future__ import annotations

import io
import json
import logging
import os
import zipfile
from typing import Dict, List, Optional

import requests

from app.config import MAX_RECORDS_PER_COUNTRY
from app.db import check_record_exists_by_json_field, save_drug_record

logger = logging.getLogger(__name__)

DATA_URL = os.getenv('TW_TFDA_DATA_URL', 'https://data.fda.gov.tw/data/opendata/export/36/json')
REQUEST_TIMEOUT = int(os.getenv('TW_TFDA_TIMEOUT', '180'))


def _clean(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return value


def _download_records() -> List[dict]:
    response = requests.get(
        DATA_URL,
        headers={'User-Agent': 'source-predicate/Taiwan-TFDA-crawler'},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        json_names = [name for name in archive.namelist() if name.lower().endswith('.json')]
        if not json_names:
            raise ValueError(f'TFDA export archive had no .json member: {archive.namelist()}')
        with archive.open(json_names[0]) as handle:
            return json.loads(handle.read().decode('utf-8'))


def _group_by_license(rows: List[dict]) -> Dict[str, dict]:
    """Fold rows sharing a license number into one record with a manufacturers list."""
    grouped: Dict[str, dict] = {}
    for row in rows:
        license_number = _clean(row.get('許可證字號'))
        if not license_number:
            continue

        manufacturer = {
            'name': _clean(row.get('製造商名稱')),
            'address': _clean(row.get('製造廠廠址')),
            'company_address': _clean(row.get('製造廠公司地址')),
            'country': _clean(row.get('製造廠國別')),
            'process': _clean(row.get('製程')),
        }

        record = grouped.get(license_number)
        if record is None:
            active_ingredients_raw = _clean(row.get('主成分略述'))
            record = {
                'license_number': license_number,
                'license_type': _clean(row.get('許可證種類')),
                'old_license_number': _clean(row.get('舊證字號')),
                'cancellation_status': _clean(row.get('註銷狀態')),
                'cancellation_date': _clean(row.get('註銷日期')),
                'cancellation_reason': _clean(row.get('註銷理由')),
                'valid_until_date': _clean(row.get('有效日期')),
                'issue_date': _clean(row.get('發證日期')),
                'customs_clearance_document_id': _clean(row.get('通關簽審文件編號')),
                'name_zh': _clean(row.get('中文品名')),
                'name_en': _clean(row.get('英文品名')),
                'indications': _clean(row.get('適應症')),
                'dosage_form': _clean(row.get('劑型')),
                'packaging': _clean(row.get('包裝')),
                'packaging_barcode': _clean(row.get('包裝與國際條碼')),
                'drug_category': _clean(row.get('藥品類別')),
                'controlled_drug_class': _clean(row.get('管制藥品分類級別')),
                'active_ingredients': (
                    [part.strip() for part in active_ingredients_raw.split(';;') if part.strip()]
                    if active_ingredients_raw else []
                ),
                'applicant_name': _clean(row.get('申請商名稱')),
                'applicant_address': _clean(row.get('申請商地址')),
                'applicant_uniform_number': _clean(row.get('申請商統一編號')),
                'dosage_and_administration': _clean(row.get('用法用量')),
                'last_updated_date': _clean(row.get('異動日期')),
                'manufacturers': [],
            }
            grouped[license_number] = record

        if any(manufacturer.values()) and manufacturer not in record['manufacturers']:
            record['manufacturers'].append(manufacturer)

    return grouped


class TaiwanFDACrawler:
    """Downloads and ingests the TFDA all-drug-products open-data export."""

    def __init__(self):
        pass

    def close(self):
        pass

    def process_country(self, country_id: int):
        rows = _download_records()
        logger.info(f"[TW] downloaded {len(rows)} raw rows from TFDA export")

        grouped = _group_by_license(rows)
        logger.info(f"[TW] grouped into {len(grouped)} license records")

        saved = 0
        for license_number, json_data in grouped.items():
            if check_record_exists_by_json_field(country_id, 'license_number', license_number):
                continue

            name = (json_data.get('name_zh') or json_data.get('name_en') or 'TFDA Drug').strip()
            try:
                save_drug_record(name[:255], country_id, None, json_data)
                saved += 1
            except Exception:
                logger.exception(f"Failed to process TFDA license: {license_number}")
                continue

            if MAX_RECORDS_PER_COUNTRY and saved >= MAX_RECORDS_PER_COUNTRY:
                logger.info(f"Reached MAX_RECORDS_PER_COUNTRY={MAX_RECORDS_PER_COUNTRY}, stopping.")
                break

        logger.info(f"[TW] crawl finished. Saved {saved} records.")


# ---------------------------------------------------------------------------
# Standalone test entry-point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import logging as _logging
    from app.db import init_db, get_or_create_country
    from app.crawlers.taiwan import COUNTRY_NAME, COUNTRY_CODE

    _logging.basicConfig(
        level=_logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )

    init_db()
    country_id = get_or_create_country(COUNTRY_NAME, COUNTRY_CODE)

    crawler = TaiwanFDACrawler()
    try:
        crawler.process_country(country_id)
    finally:
        crawler.close()
