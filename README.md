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

This crawler does **not** scrape the Drugs@FDA web UI. It ingests FDA's
official bulk data files — one download per run instead of ~30k page
fetches (`app/crawlers/united_states/crawler_us_1.py`):

```
https://www.fda.gov/media/89850/download   (~6 MB zip, 12 tab-delimited files)
```

**Why not scrape `accessdata.fda.gov`.** It used to walk 27 browse-by-letter
pages plus one overview page per application. That host now sits behind
Akamai bot/abuse detection and is confirmed live to be unusable from a
datacenter IP: from a residential/office IP plain `requests` fetched all 27
letter pages with HTTP 200, but from the production cluster's egress IP the
*first* request of the run (letter "A", attempt 1, nothing preceding it) is
already redirected to `/apology_objects/abuse-detection-apology.html`,
served with a 404. Because the block lands on request #1 with no prior
traffic it is not a rate limit — pacing, jitter and backoff were all tried
and none help. Driving headless Chromium is strictly worse: Akamai detects
and blocks its automation fingerprint on the very first request, even from
an IP where plain `requests` passes. The bulk files live on `www.fda.gov`
instead, and one fetch per run replaces ~30k, shrinking the bot-detection
surface to a single request. It does not remove it: `www.fda.gov` runs its
own Akamai abuse detection, and pulling the zip several times in quick
succession trips it (confirmed live — the block arrives as a 404 redirect to
the apology page and clears after a cooldown, while `accessdata.fda.gov`
kept serving the same IP normally). `_fetch_bulk_zip` therefore retries that
block with backoff instead of treating the 404 as terminal, which is what
`download_with_retries` would do; tune with `FDA_BULK_ATTEMPTS` /
`FDA_BULK_BACKOFF_SECONDS`.

**The zip is cached on disk, and that matters.** FDA only refreshes this
file periodically, so re-downloading it every run is pure waste — and a few
rapid re-downloads while iterating locally is exactly what trips the abuse
detection above, locking the egress IP out for >20 minutes (observed).
`docker-compose.yml` mounts `./.cache:/app/.cache` so the cache survives
container recreation; without that volume every `docker compose up` starts
from scratch.

**`json_data.spl_labels` — the actual label TEXT, not just a PDF link.**
Every other field above is *metadata about* a document (a PDF URL, a
submission date); `spl_labels` is the document's actual content —
`api.fda.gov/drug/label.json`'s Structured Product Labeling text
(`indications_and_usage`, `warnings`, `dosage_and_administration`,
`active_ingredient`, `inactive_ingredient`, and more, already split into
clean fields). Confirmed live that `openfda.application_number` is an ARRAY
on the label side — some labels (e.g. trametinib/Mekinist: `NDA204114` +
`NDA217513`) list more than one application_number, because a later
application reused an earlier one's already-approved label verbatim. This
does **not** create duplicate rows: a returned label is indexed under
every application_number in its own array that matches one of
drugsfda.json's own already-deduped, genuinely distinct applications, so
the shared label is attached under each application's own row — two
genuinely distinct FDA applications sharing one label, not the same record
twice.

**Fetched in ~100 batched requests, not one per application.** An earlier
version called label.json once per application from inside each worker
(~29k calls for a full run). Measured live: 8 concurrent workers each
independently pacing their own calls produced an aggregate rate of ~249
req/min — already at/over openFDA's published 240/min per-IP cap (global
across every endpoint, not per-worker), so the crawl was constantly
tripping 429s and paying a 30-90s backoff sleep per hit. `prefetch_labels`
now OR-batches many application_numbers into each query (confirmed live
openFDA supports this, bounded by the reverse proxy's ~8KB URL length
limit — `FDA_LABEL_BATCH_SIZE` defaults to 300 for margin) in one pass
before the worker pool starts, cutting label lookups to roughly
`applications / FDA_LABEL_BATCH_SIZE` requests total — comfortably under
the rate limit even run single-lane with no concurrency at all. Tune with
`FDA_LABEL_API_URL` / `FDA_LABEL_BATCH_SIZE`; a batch that fails outright
logs a warning and every application in it gets `spl_labels: []` rather
than blocking the whole run, since not every application predates the SPL
requirement anyway (an empty result is often correct, not a failure).

**Backfilling `spl_labels` onto an already-ingested run.** If US data was
already fully crawled before this batching (or the label feature) existed,
re-crawling from scratch just to pick up `spl_labels` would mean
re-fetching every application from drugsfda.json/the TSV and
re-downloading every PDF for no reason — none of that is affected by the
label fix. `app/crawlers/united_states/backfill_labels.py` instead reads
every existing US row's `application_number`/`application_type_code`
straight out of its own already-stored `json_data`, runs them through the
same batched `prefetch_labels()`, and `UPDATE`s only the `spl_labels` key
via Postgres's jsonb `||` merge — every other field (products, documents,
approval_history, `document_url`, ...) is left untouched. Every row is
reprocessed unconditionally (not just rows missing the key), since a
present-but-empty `spl_labels: []` from a run that predates the
type-prefix fix isn't trustworthy evidence that no label exists. Triggered
via `FDA_BACKFILL_LABELS=true`, checked at the top of `app/main.py` — when
set, it runs ONLY the backfill instead of the normal per-country crawl
loop, then exits:

```bash
# Preview first — logs counts, writes nothing
FDA_BACKFILL_LABELS=true FDA_BACKFILL_DRY_RUN=true python -m app.main

# Then actually write
FDA_BACKFILL_LABELS=true python -m app.main
```

Unset `FDA_BACKFILL_LABELS` afterwards so later runs go back to the normal
crawl.

Resolution order is **fresh cache → download → stale cache**. That last step
is deliberate: if the download is blocked but any cached copy exists, the run
proceeds on it at any age with a loud warning, because FDA's data moves
slowly enough that a slightly dated dataset beats ingesting nothing.
Verified live during an actual lockout — the run still ingested all 29,270
applications from cache. Tune with `FDA_BULK_CACHE_DIR` (default
`$PWD/.cache/fda`) and `FDA_BULK_CACHE_TTL_SECONDS` (default 24h); delete
`.cache/fda/drugsfda.zip` to force a refresh.

**File → field mapping** (all confirmed live against the 2026-08-14
release: 29,270 applications, 51,653 products, 193,466 submissions, 80,824
document links):

| Bulk file | Replaces |
|---|---|
| `Applications.txt` | application type/number/sponsor header |
| `Products.txt` | the `exampleProd` table |
| `Submissions.txt` | `exampleApplOrig` / `exampleApplSuppl` (ORIG vs SUPPL) |
| `ApplicationDocs.txt` | the page-wide `drugsatfda_docs` anchor scan, typed via `ApplicationsDocsType_Lookup` |
| `TE.txt` | the `exampleTEVA*` therapeutic-equivalents tables |
| `MarketingStatus.txt` | the "Marketing Status" product column |
| `Join_Submission_ActionTypes_Lookup.txt` + `ActionTypes_Lookup.txt` | supplement action types (only free text in the HTML) |

**Parsing gotchas** — all four are load-bearing:

1. Encoding is **cp1252, not UTF-8**; `Submissions.txt` and
   `ApplicationDocs.txt` both contain bytes (0x92, 0xa0) that raise
   `UnicodeDecodeError` under UTF-8.
2. The files contain literal unescaped `"` characters inside data fields (5
   in `Products.txt`, 198 in `Submissions.txt`) that are **not** csv
   quoting — they must be read with `quoting=csv.QUOTE_NONE`, or the
   default `QUOTE_MINIMAL` swallows following rows into one giant field.
3. Exactly one `ApplicationDocs.txt` row has a stray extra tab (9 fields
   against an 8-column header); `_fit_row` repairs it instead of dropping it.
4. Join keys need stripping — `SubmissionType` is space-padded (`'SUPPL     '`).

**Only PDFs are downloaded**, unchanged from the scraping version: links
that don't end in `.pdf` (e.g. a Review pointing at `.html`) stay as
`source_url`-only metadata. Two guards matter because the document host is
still the WAF'd `accessdata.fda.gov`: URLs are upgraded to https (the bulk
file lists many as http), and a body must actually start with `%PDF` before
upload, so an Akamai block page is never mirrored to S3 under a `.pdf` key.
If document downloading is blocked from your egress, metadata ingestion
still succeeds — set `DOWNLOAD_DOCUMENTS=false` to skip it entirely.

One row per ApplNo (an application can bundle several products, so `name`
joins every distinct `DrugName`). `name` is bounded to the column's
`VARCHAR(255)` on a whole-product boundary with a `(+N more)` marker —
confirmed live that 35 of 29,270 applications otherwise exceed it (the longest
is 4,669 chars). This is not about avoiding an insert error: `save_drug_record`
already truncates over-long names (`app/db.py:276-278`). It's about where the
cut lands — that path slices mid-word and logs a warning per record, so a
clean boundary gives a better name and drops 35 lines of noise per run.
Nothing is lost either way, since `json_data.products` holds the full list.
`document_url` holds
OUR S3 keys, never FDA's URLs (see `app.storage.upload_file`); each
document's original `drugsatfda_docs` URL is kept at
`json_data.documents[i].source_url`, and `json_data.source_url` still points
at the human-readable overview page for provenance (that page is not
fetched).

Dedup is by `json_data.application_number` (FDA's own ApplNo, stable and
unique) via `app.db.check_record_exists_by_json_field` — `name` isn't usable
since many distinct applications share a brand/generic name.

`FDA_WORKERS` (default 8) threads persist the parsed applications; the only
network I/O left in that fan-out is optional PDF downloading.
`FDA_BULK_ZIP_URL` overrides the download location.

## Saudi Arabia Crawler — SFDA New Drug Approvals + Human Drugs Registry

`sfda.gov.sa` is a plain server-rendered Drupal site (Views module) — no
browser needed (`app/crawlers/saudi_arabia/crawler_sa_1.py` uses plain
`requests` + BeautifulSoup, the same pattern as the South Africa crawler).
Two independent sources are combined into this ONE crawler (the registry
has one crawler class per country — see "Adding a New Country Crawler"
below), each with its own dedup key so neither can collide with the other:

**Source 1 — New Drug Approvals** (a small, rolling list; ~336 rows):
- **Listing**: `GET /en/new-sfda-drug-approvals?page=<N>` (0-indexed) — a
  standard Drupal Views table, 10 rows/page: Request Type, Drug Type,
  Trade Name, Scientific Name, Strength, Dosage Form, Approval Date, and a
  "SFDA Approved Use" link per row pointing at
  `/en/drug-approvals-use/<id>`. As of 2026-08: 34 pages. Confirmed live
  `items_per_page` isn't honored by this view (the page size is fixed at
  10), so this is a real ~34-request crawl.
- **Detail**: `GET /en/drug-approvals-use/<id>` — a full HTML page
  (confirmed live: identical whether or not Drupal's AJAX headers are
  sent) containing exactly one field beyond what the listing row already
  has — the drug's approved indication/use text.
- Dedup: `json_data.sfda_use_id` — confirmed live per-row unique even when
  `trade_name` isn't (two strengths of "Brevie" link to two different ids).

**Source 2 — Human Drugs Registry** (the comprehensive national registry;
~20,925 rows, `/ar/drugs-list`, ARABIC locale only — see below):
- **Listing**: `GET /ar/drugs-list?page=<N>` — 15 rows/page, 1,395 pages.
  Confirmed live the English version (`/en/drugs-list`) has the same
  listing but a details endpoint that returns only 6 of ~28 fields — a gap
  in SFDA's own English rendering, so this source is crawled in Arabic and
  mapped to English snake_case keys.
- **Detail**: `POST /ar/details_data?nid=<view-id>&id=<row-id>&page=<N>` —
  the full ~28-field table (registration number, manufacturer, agents, ATC
  codes, pack/shelf-life info, pricing). **Three confirmed traps**, found
  in order: (1) GET silently ignores `id` and always returns the same
  fixed row — must be POST, confirmed via `drupalSettings.ajax`'s
  `"httpMethod":"POST"`; (2) the lookup is scoped by `(id, page)`
  TOGETHER, not `id` alone, so params must be reused verbatim from the
  row's own link, never reconstructed; (3) even then, a **confirmed ~47%
  of rows** (35/75 sampled sequentially across pages 0-4) deterministically
  return an unrelated row's detail — verified NOT flaky (one failing id
  retried 8× returned the identical wrong row every time, and no `page`
  value ever resolved it) — a bug in SFDA's own backend with no
  client-side fix. The crawler cross-checks the fetched detail's own trade
  name against the listing row's before trusting it, and saves an empty
  `detail` rather than ever persisting data attributed to the wrong drug —
  so roughly half of this source's rows will only have their 5
  listing-level fields, by design, not as a bug in this crawler.
  Several further fields (`strength_unit`, `package_type`, etc.) come back
  as the literal string `"Array"` on essentially every row — a separate,
  confirmed PHP array-to-string bug on SFDA's side — stored as-is so it's
  visibly distinct from a genuinely empty field.
- Dedup: `json_data.sfda_drug_id` (the row's own `id`, checked before the
  detail fetch) — not `registration_number`, since that isn't known until
  after the detail fetch it would need to gate. `registration_number` is
  still promoted to a top-level `json_data` field once successfully
  fetched, the same role SAHPRA's `application_no`/TGA's `artg_id` play.

Neither source has an attached PDF/label, so `document_url` is always
empty on both — the same as the South Africa crawler.

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

Same pattern for the FDA crawler. It makes a single ~6 MB download and then
works offline from the parsed bulk files, so discovery is fast; the slow
part is the optional PDF mirroring, which `MAX_RECORDS_PER_COUNTRY` (or
`DOWNLOAD_DOCUMENTS=false`) keeps short for a smoke test:

```bash
MAX_RECORDS_PER_COUNTRY=20 python -m app.crawlers.united_states.crawler_us_1
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| **Crawler can't connect to DB** | Check `POSTGRES_HOST` — `host.docker.internal` from Docker, `localhost` for local dev. |
| **MinIO upload fails** | Verify `MINIO_ENDPOINT` points at the running `source-information` MinIO and the credentials match. The bucket is auto-created if missing. |
| **"No crawler available"** | Ensure the country has a package under `app/crawlers/` with `COUNTRY_CRAWLER` defined. |
| **Country skipped too early** | The skip-counter is disabled by default (`MAX_SKIPPED_RECORDS_PER_COUNTRY=0`). If it is set above `0` in your environment, a country can stop early on a run of duplicates — regulatory feeds are not date-ordered, so this is usually not what you want. |
