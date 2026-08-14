"""
Crawler Configuration
All configuration is via environment variables (see .env.example / docker-compose.yml).
"""
import os

# Set TEST_MODE=true to only crawl countries listed in TEST_COUNTRIES
TEST_MODE = os.getenv('TEST_MODE', 'false').lower() == 'true'

# Countries to process during testing (comma-separated country names,
# matching COUNTRY_NAME in a crawler's package, e.g. "United Kingdom")
_test_countries_env = os.getenv('TEST_COUNTRIES', '')
if _test_countries_env:
    TEST_COUNTRIES = [c.strip() for c in _test_countries_env.split(',') if c.strip()]
else:
    TEST_COUNTRIES = ['United Kingdom']

# Countries to skip during processing (used only when TEST_MODE is false)
SKIP_COUNTRIES = [c.strip() for c in os.getenv('SKIP_COUNTRIES', '').split(',') if c.strip()]

# Maximum records to save per country per run (0 = no limit)
MAX_RECORDS_PER_COUNTRY = int(os.getenv('MAX_RECORDS_PER_COUNTRY', '0'))

# Maximum number of duplicate-skipped records before abandoning a country
# early for this run (default: 200; set 0 to disable)
MAX_SKIPPED_RECORDS_PER_COUNTRY = int(os.getenv('MAX_SKIPPED_RECORDS_PER_COUNTRY', '200'))

# Maximum number of parallel country-crawler workers
MAX_WORKERS = int(os.getenv('MAX_WORKERS', '2'))

# Whether crawlers should download source documents and upload them to S3/MinIO.
# Set to false to only ingest metadata (name/document_url/json_data) quickly.
DOWNLOAD_DOCUMENTS = os.getenv('DOWNLOAD_DOCUMENTS', 'true').lower() == 'true'
