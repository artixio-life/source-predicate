# Drug Predicate Assessment Crawler

A **country-wise regulatory drug-database crawler** that fetches raw product/document
records (name, source document URL, structured metadata) from national drug
regulators, for use in downstream predicate/reference-product assessment.

Metadata is stored in **PostgreSQL**; source documents (PARs, SPCs, PILs, etc.)
are mirrored to the **MinIO/S3 instance already used by `source-information`** —
this repo does not run its own MinIO.

First country: **United Kingdom** (MHRA Products Database). More countries are
added the same way `source-information` adds them — one crawler package per
country, registered automatically.

---

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│                      main.py (Entry Point)                    │
│  1. Applies schema/schema.sql (idempotent)                    │
│  2. Reads registered countries from the Crawler Registry      │
│  3. Filters by TEST_MODE / SKIP_COUNTRIES                     │
│  4. Runs one worker process per country (ProcessPoolExecutor) │
└───────────────────────────┬─────────────────────────────────--┘
                            │
                 ┌──────────▼──────────┐
                 │   Crawler Registry   │
                 │ (app/crawlers/__init__.py) │
                 │  country name -> crawler class │
                 └──────────┬──────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                                        ▼
 ┌───────────────┐                      ┌────────────────┐
 │ United Kingdom │                      │ <next country>  │
 │ MHRA Crawler   │                      │ crawler         │
 └───────┬────────┘                      └────────┬────────┘
         │                                         │
         ▼                                         ▼
 ┌─────────────────────────────────────────────────────────┐
 │                     Shared Services                       │
 │  app/db.py      → PostgreSQL (upsert + dedup + crawl log) │
 │  app/storage.py → MinIO/S3 (document upload)               │
 │  app/utils/     → HTTP retry helper                        │
 └─────────────────────────────────────────────────────────┘
```

## Database Schema

All tables live under the `source` schema (shared with `source-information` if
pointed at the same database).

| Table | Purpose |
|-------|---------|
| `source.country` | Countries (`name`, 2-letter `code`). Created if missing; reused if `source-information` already seeded it. |
| `source.drug_predicate_raw_records` | One row per crawled record: `name`, `country_id`, `document_url` (`TEXT[]` — one or more related document URLs, e.g. PAR + PIL + SPC for the same product), `json_data` (JSONB — full structured metadata; `s3_path` holds the **bare object key**, never a full `s3://bucket/...` URI — see note below). Unique on `(country_id, document_url)`, so an exact re-crawl upserts instead of duplicating. |
| `source.drug_predicate_crawl_log` | One row per country per run: `status` (`START`/`DONE`/`FAILED`/`SKIPPED`), `started_at`/`finished_at`, `detail`. |

```sql
CREATE TABLE IF NOT EXISTS source.drug_predicate_raw_records (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255),
  country_id INTEGER REFERENCES source.country(id) ON DELETE SET NULL,
  document_url TEXT[],
  json_data JSONB,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

> **S3 path convention:** `app.storage.upload_file` returns the bare object key
> (e.g. `united_kingdom/1/1755160000000_paracetamol.pdf`), never a
> `s3://bucket/key` URI. The bucket name is never embedded in a stored path —
> reconstruct the full location at read time from `MINIO_BUCKET` + the stored
> key. A prior project stored the full URI and had to migrate every DB row
> during a bucket rename; don't repeat that here.

## Project Structure

```
source-predicate/
├── app/
│   ├── main.py                        # Entry point — schema init + parallel country crawling
│   ├── config.py                      # Env-driven configuration
│   ├── db.py                          # Postgres ops: upsert, dedup, crawl log, skip-threshold
│   ├── storage.py                     # MinIO/S3 upload (reuses source-information's MinIO)
│   ├── utils/
│   │   └── request_helper.py          # HTTP download with retries/backoff
│   └── crawlers/
│       ├── __init__.py                # Crawler Registry (country name -> crawler class)
│       └── united_kingdom/
│           ├── __init__.py            # COUNTRY_NAME / COUNTRY_CODE / COUNTRY_CRAWLER
│           └── crawler_uk_1.py         # MHRA Products Database crawler
├── schema/
│   └── schema.sql                     # Table definitions (idempotent)
├── Dockerfile
├── docker-compose.yml                 # Crawler only — no MinIO service (reused externally)
├── docker-compose.yml.example
└── requirements.txt
```

## United Kingdom Crawler — MHRA Products Database

`products.mhra.gov.uk` is a client-rendered Next.js app with no static HTML
to scrape — the crawler (`app/crawlers/united_kingdom/crawler_uk_1.py`)
drives a real headless Chromium via **Playwright** through the site's own
navigation:

```
/substance-index/?letter=<A-Z,0-9>  →  /substance/?substance=<strain>  →  /product/?product=<name>
```

Link discovery (letters → strains → unique product links) runs on one
browser page, sequentially — a couple thousand navigations at most. Product
pages are the actual bottleneck (tens of thousands of them), so they're
processed by `MHRA_BROWSER_WORKERS` (default `4`) independent headless
Chromium instances draining a shared queue concurrently — Playwright's sync
API ties a page to the thread that opened it, so each worker owns its own
browser rather than sharing one.

For each product page it:

1. Accepts the per-product legal disclaimer gate when present (checks
   `input#agree-checkbox`, clicks the "Agree" button).
2. Collects every document card (`div.search-result`: doc type badge
   SPC/PIL/PAR, title, filename, file size, active substances), following
   "Next page" pagination (10 docs/page) until exhausted.
3. Groups all of a product's documents into **one row**: `name` is the SPC
   document's title when an SPC exists (else the first available doc's
   title), `document_url` is the ordered list of that product's document
   URLs with **SPC first**, then PIL, then PAR.
4. Skips the whole product if a row with the same `name` already exists for
   this country (`app.db.check_record_exists_by_name`) — one row per
   distinct `/product/?product=<name>` page by design.
5. Downloads each document and uploads it to `MINIO_BUCKET` (unless
   `DOWNLOAD_DOCUMENTS=false`), storing each one's bare object key as
   `document_url` — never the MHRA blob URL itself, and never a full
   `s3://bucket/key` URI. Each document's original MHRA URL is kept
   separately at `json_data.documents[i].source_url` for provenance/re-download.

**Known MHRA site bug — whitespace-mismatch fallback:** some product links
generated by the site's own strain pages don't match the underlying
`product_name` field exactly (confirmed live: `product_name` can contain a
literal double space that the displayed link text has already collapsed to
one), so the product page falsely reports "no search results" even though
matching documents exist. When on-page navigation turns up zero cards, the
crawler falls back to a full-text `search=` query against the same public
Azure Cognitive Search index the site itself queries
(`mhraproducts4853.search.windows.net/indexes/products-index`, read-only key
shipped in the site's JS bundle), filtered client-side by
whitespace-normalized `product_name` equality (also matching against
comma-joined compound `product_name` values — confirmed live: some entries
store two brand names sharing one licence as a single comma-separated
field, so an exact match against either half alone would otherwise miss).

## South Africa Crawler — SAHPRA Registered Health Products

`medapps.sahpra.org.za:6006` is a plain server-rendered ASP.NET Core app —
no browser needed, unlike the UK crawler
(`app/crawlers/south_africa/crawler_za_1.py` uses plain `requests`):

- **Listing**: `POST /Home/getData` — a standard jQuery DataTables
  server-side-processing endpoint (confirmed live: no auth/session/CSRF
  required). ~21,205 rows as of 2026-08, paginated 100/page.
- **Detail**: `GET /Home/Details/?id=<secureId>` — plain HTML, one
  `<table id="reg">` of label/value rows (Applicant, Proprietary Name,
  Dosage Form, Ingredients, Strength, Registration number, Date Registered,
  Renewal Date, Date Expired, Active Pharmaceutical Ingredient, Status),
  fetched concurrently (`SAHPRA_DETAIL_WORKERS`, default 6) per listing page.

This registry has **no attached documents** (no SPC/PIL/PAR-equivalent) —
`document_url` is always empty; every field lives in `json_data` (the list
row's fields plus a nested `detail` object from the Details page).

Dedup is by `json_data.application_no` (`app.db.check_record_exists_by_json_field`)
— SAHPRA's own stable identifier. Neither `licence_no` (literally the
string `"Old Medicine"` for many legacy entries — not unique) nor `name`
(distinct registrations, sometimes different applicants/eras, can share a
display name) work as a dedup key here.

## Brazil Crawler — ANVISA Medicamentos

`consultas.anvisa.gov.br` is an AngularJS SPA on a plain REST/JSON backend
(`app/crawlers/brazil/crawler_br_1.py`) — confirmed live via network
traffic, no HTML scraping needed:

- **Listing**: `GET /api/consulta/medicamento/produtos/` (DataTables-style
  `count`/`page` paging, filtered to `checkNotificado=false&checkRegistrado=true`
  — the same scope as the site's own default listing). ~32,653 products as
  of 2026-08.
- **Detail**: `GET /api/consulta/medicamento/produtos/codigo/<codigo>` —
  full structured record (company, process, every apresentação/registration,
  labeling-PDF metadata, and an `existeBula` flag).
- **Bulário + PDF**: for products with `existeBula: true`,
  `GET /api/consulta/bulario?filter[numeroRegistro]=...` returns a
  short-lived (~5 min) token, immediately exchanged via
  `GET /api/consulta/medicamentos/arquivo/bula/parecer/<token>/` for the
  **Bula do Profissional** (professional package insert) PDF — the only
  document this crawler downloads (the patient-leaflet PDF and the
  labeling PDFs are recorded as metadata only, already present in the raw
  detail response).

Every endpoint requires an `Authorization: Guest` header (confirmed live:
omitting it returns HTTP 500 `mensagens.MSG-004`).

**Cloudflare, and why this crawler does NOT use FlareSolverr:** confirmed
live that plain `requests` and `curl_cffi` (Chrome TLS/JA3 impersonation)
are both hard-blocked by Cloudflare's WAF, even replaying cookies from a
solved FlareSolverr session — but Playwright's own headless Chromium
passes cleanly on every endpoint above with no extra work. FlareSolverr
just runs another browser to solve the same challenge, so it would add a
service dependency for no benefit here (unlike `source-information`'s
separate Brazil crawler, a different ANVISA property — a Plone CMS under
`www.gov.br/anvisa` — which does need it).

**The real constraint is a Cloudflare rate-limit rule**, not the WAF:
bursting above ~25-30 requests trips a genuine `429` with
`Retry-After: 600` (a 10-minute penalty), confirmed live and reproducible
regardless of client. So this crawler runs strictly single-lane (no
concurrency), pacing every API call (`ANVISA_REQUEST_DELAY_SECONDS`,
default `1.2`s) and sleeping for the exact `Retry-After` duration on a 429
rather than guessing at backoff. A full crawl of ~32k products
necessarily takes several hours; reruns are cheap afterwards since
already-ingested products are skipped via dedup.

Dedup is by `json_data.codigo_produto` (ANVISA's own per-product id) via
`app.db.check_record_exists_by_json_field` — most products have no
downloaded PDF (no bulário entry), so `document_url` can't serve as the
dedup key the way it does for the UK/Australia crawlers.

## United States Crawler — Drugs@FDA

`accessdata.fda.gov` is a plain server-rendered ColdFusion app — no browser
needed, unlike the UK crawler
(`app/crawlers/united_states/crawler_us_1.py` uses plain `requests` +
BeautifulSoup):

```
/scripts/cder/daf/index.cfm?event=browseByLetter.page&productLetter=<A-Z,0-9>
    -> /scripts/cder/daf/index.cfm?event=overview.process&ApplNo=<n>
```

- **Discovery** (27 letter pages: A-Z plus a single `0-9` bucket — confirmed
  live, FDA groups all digits into one nav link, unlike MHRA's 36
  individual letters/digits): every accordion drug-name section on a
  letter page already lists every ANDA/NDA/BLA application link for that
  name directly in the initial HTML. Confirmed live: the big per-letter
  list uses a client-side pagination plugin (`footable`) that only hides
  rows after the page loads — every row is already present in one GET
  response, so no pagination handling is needed.
- **Detail**: `event=overview.process&ApplNo=<n>` — application
  type/number/company plus four fixed-id tables (`exampleProd`,
  `exampleApplOrig`/`exampleApplSuppl`, `exampleLabels`) and zero or more
  `exampleTEVA*` therapeutic-equivalents tables, all parsed generically via
  `<thead>` th text -> snake_case key. Confirmed live across NDA, ANDA, and
  BLA applications (e.g. ApplNo 020892, 060002, 761235) that older
  applications can be missing every table except `exampleProd`.

**Only PDFs are downloaded.** Every document link on an overview page is
collected in one page-wide anchor scan (deduped by href — the same label
PDF is often linked from both the approval-history table and the dedicated
"Labels for ..." table), but only links that actually end in `.pdf` are
fetched and mirrored to S3; a Review link that points at an `.html` page,
or an application with "Label is not available on this site.", stays as
plain `source_url` metadata in `json_data` and is never downloaded.

One row per ApplNo (an application can bundle several product names — see
`exampleProd` — so `name` joins every distinct product name found there).
`document_url` holds OUR S3 keys, never FDA's URLs (see
`app.storage.upload_file`); each document's original `drugsatfda_docs` URL
is kept separately at `json_data.documents[i].source_url`.

Dedup is by `json_data.application_number` (FDA's own ApplNo, stable and
unique) via `app.db.check_record_exists_by_json_field` — `name` isn't
usable since many distinct applications share a brand/generic name.

`FDA_WORKERS` (default 8) threads share one `requests.Session` for the
detail-page fan-out — no browser state to isolate per thread, unlike MHRA,
so this follows the same pattern as SAHPRA's `DETAIL_WORKERS`.

## Crawler Interface

Every crawler class registered in `app/crawlers/<country>/__init__.py` must implement:

```python
class SomeCountryCrawler:
    def process_country(self, country_id: int):
        """Crawl this country's drug database, saving via app.db.save_drug_record."""

    def close(self):
        """Release any held resources (HTTP sessions, browser drivers, ...)."""
```

## Adding a New Country Crawler

1. Create `app/crawlers/<country_name>/__init__.py`:
   ```python
   COUNTRY_NAME = 'Some Country'
   COUNTRY_CODE = 'SC'
   COUNTRY_CRAWLER = ('app.crawlers.some_country.crawler_1', 'SomeCountryCrawler')
   ```
2. Implement `crawler_1.py` with the `process_country(self, country_id)` / `close(self)` interface above.
3. Nothing else to register — `app/crawlers/__init__.py` discovers it automatically at import time.
4. Add the country name to `TEST_COUNTRIES` and run with `TEST_MODE=true` to verify.

## Configuration Reference

| Variable | Description | Default |
|----------|-------------|---------|
| `POSTGRES_HOST` / `PORT` / `DB` / `USER` / `PASSWORD` | Postgres connection | see `.env.example` |
| `MINIO_ENDPOINT` | MinIO/S3 endpoint — point at the **existing** `source-information` MinIO | `http://host.docker.internal:9000` |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | MinIO credentials | `minioadmin` |
| `MINIO_BUCKET` | Bucket for uploaded documents | `drug-predicate-documents` |
| `TEST_MODE` | Only crawl countries in `TEST_COUNTRIES` | `true` |
| `TEST_COUNTRIES` | Comma-separated country names | `United Kingdom` |
| `SKIP_COUNTRIES` | Comma-separated country names to skip (used when `TEST_MODE=false`) | _(empty)_ |
| `MAX_RECORDS_PER_COUNTRY` | Cap on saved records per run (`0` = unlimited) | `0` |
| `MAX_SKIPPED_RECORDS_PER_COUNTRY` | Abandon a country early after this many duplicate skips (`0` = disabled, the default) | `0` |
| `MAX_WORKERS` | Parallel country-crawler workers | `2` |
| `DOWNLOAD_DOCUMENTS` | Download + upload source documents to S3 (`false` = metadata only) | `true` |

> **Important:** set `TEST_MODE=false` (and optionally `SKIP_COUNTRIES`) in production so every registered country is crawled.

## Getting Started

### 1. Configure

```bash
cp .env.example .env
# edit .env: Postgres creds, and MINIO_ENDPOINT pointing at the running
# source-information MinIO (host.docker.internal:9000 if its ports are
# published on the host — see docker-compose.yml for the alternative of
# joining its Docker network directly).
```

### 2. Set up the database

The schema is applied automatically on startup (`init_db()` in `app/main.py`),
or apply it manually:

```sql
\i schema/schema.sql
```

### 3. Build & run

```bash
docker-compose up --build
```

### 4. Local development (no Docker)

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export POSTGRES_HOST=localhost
export MINIO_ENDPOINT=http://127.0.0.1:9000
# ... see Configuration Reference ...

python -m app.main
```

To run just the UK crawler directly (bypassing `main.py`'s country filtering):

```bash
python -m app.crawlers.united_kingdom.crawler_uk_1
```

Same pattern for the FDA crawler (limit to a couple of letters first via
`FDA_LETTERS=V,0-9` — a full A-Z crawl visits tens of thousands of
application pages and takes a long time):

```bash
FDA_LETTERS=V python -m app.crawlers.united_states.crawler_us_1
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| **Crawler can't connect to DB** | Check `POSTGRES_HOST` — `host.docker.internal` from Docker, `localhost` for local dev. |
| **MinIO upload fails** | Verify `MINIO_ENDPOINT` points at the running `source-information` MinIO and the credentials match. The bucket is auto-created if missing. |
| **"No crawler available"** | Ensure the country has a package under `app/crawlers/` with `COUNTRY_CRAWLER` defined. |
| **Country skipped too early** | The skip-counter is disabled by default (`MAX_SKIPPED_RECORDS_PER_COUNTRY=0`). If it is set above `0` in your environment, a country can stop early on a run of duplicates — regulatory feeds are not date-ordered, so this is usually not what you want. |
