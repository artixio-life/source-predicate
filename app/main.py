import os
import sys
import logging
import concurrent.futures

# Ensure stdout/stderr use UTF-8 so Unicode log messages don't fail on Windows
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from app.db import (
    init_db,
    get_or_create_country,
    log_crawl_start,
    log_crawl_finish,
    reset_country_skip_counter,
    clear_country_skip_counter,
    CountrySkipThresholdReached,
)
from app.crawlers import get_registered_countries, get_crawler_for_country_name
from app.config import TEST_MODE, TEST_COUNTRIES, SKIP_COUNTRIES, MAX_WORKERS

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def process_country_wrapper(country_name, country_code):
    """
    Worker function to crawl a single country's drug database.
    This runs in a separate process.
    """
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    logging.basicConfig(
        level=logging.INFO,
        format=f'%(asctime)s - [{country_name}] - %(levelname)s - %(message)s',
        force=True,
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    worker_logger = logging.getLogger(f"worker_{country_name}")
    worker_logger.info(f"Worker started for {country_name}")

    country_id = get_or_create_country(country_name, country_code)
    crawl_log_id = log_crawl_start(country_id)
    reset_country_skip_counter(country_id)

    crawler = None
    try:
        crawler = get_crawler_for_country_name(country_name)
        if not crawler:
            worker_logger.error(f"No crawler available for {country_name}, skipping")
            log_crawl_finish(crawl_log_id, 'SKIPPED', 'no crawler available')
            return False

        crawler.process_country(country_id)
        worker_logger.info(f"Worker finished for {country_name}")
        log_crawl_finish(crawl_log_id, 'DONE')
        return True
    except CountrySkipThresholdReached as e:
        worker_logger.info(
            f"Stopping {country_name} early: {e} "
            "This likely indicates mostly old/already-ingested data."
        )
        log_crawl_finish(crawl_log_id, 'SKIPPED', 'skip threshold reached')
        return True
    except Exception as e:
        worker_logger.error(f"Failed to process {country_name}: {e}")
        log_crawl_finish(crawl_log_id, 'FAILED', str(e))
        return False
    finally:
        clear_country_skip_counter(country_id)
        if crawler:
            try:
                crawler.close()
            except Exception as e:
                worker_logger.error(f"Error closing crawler for {country_name}: {e}")


def main():
    logger.info("Predicate Crawler Application Started")

    try:
        init_db()
    except Exception as e:
        logger.error(f"DB init failed: {e}")

    # One-off maintenance path: backfill json_data.spl_labels onto already-
    # ingested US rows without re-crawling anything (see
    # app/crawlers/united_states/backfill_labels.py for why). Runs INSTEAD
    # of the normal crawl loop below, then exits — unset this afterwards.
    if os.getenv('FDA_BACKFILL_LABELS', 'false').lower() == 'true':
        from app.crawlers.united_states import backfill_labels
        logger.info("FDA_BACKFILL_LABELS=true — running the US spl_labels backfill instead of a normal crawl")
        backfill_labels.run()
        return

    countries = get_registered_countries()
    logger.info(f"Found {len(countries)} registered country crawlers: {[c[0] for c in countries]}")

    if TEST_MODE:
        countries = [c for c in countries if c[0] in TEST_COUNTRIES]
        logger.info(f"TEST MODE: Filtering to {len(countries)} countries: {[c[0] for c in countries]}")
    elif SKIP_COUNTRIES:
        initial_count = len(countries)
        countries = [c for c in countries if c[0] not in SKIP_COUNTRIES]
        logger.info(
            f"SKIP_COUNTRIES provided: Skipping {initial_count - len(countries)} countries. "
            f"{len(countries)} countries remaining."
        )

    if not countries:
        logger.warning("No countries to process after filtering.")
        return

    logger.info(f"Starting parallel execution with {MAX_WORKERS} workers")

    results = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(process_country_wrapper, name, code): name
            for name, code in countries
        }
        for future in concurrent.futures.as_completed(futures):
            country_name = futures[future]
            try:
                results.append(future.result())
            except concurrent.futures.BrokenExecutor as e:
                logger.error(f"Process pool broken while processing {country_name}: {e}")
                results.append(False)
            except Exception as e:
                logger.error(f"{country_name} raised unexpected error: {e}")
                results.append(False)

    logger.info(f"All countries processed. Results: {results}")


if __name__ == "__main__":
    main()
