"""
Crawler Registry - Maps a country name to its crawler class.

To add a new country crawler:
1. Create a new directory in app/crawlers/ with the country name (snake_case).
2. Implement your crawler class in that directory. It must expose:
     - process_country(self, country_id: int): crawl logic, saves via app.db.save_drug_record
     - close(self): release any held resources (HTTP sessions, browser drivers, ...)
3. In the directory's __init__.py, define:
     COUNTRY_NAME = 'Some Country'          # matches source.country.name
     COUNTRY_CODE = 'SC'                    # matches source.country.code
     COUNTRY_CRAWLER = ('app.crawlers.some_country.crawler_1', 'SomeCountryCrawler')

That's it — the registry below discovers it automatically at import time.
"""
import logging
import importlib
import pkgutil
import os

logger = logging.getLogger(__name__)

# Registry mapping country NAME to (module_name, class_name)
CRAWLER_BY_COUNTRY_NAME = {}

# Registry mapping country NAME to its 2-letter code
COUNTRY_CODE_BY_NAME = {}

# Directory-name -> country-name overrides, for names title() can't derive
_SPECIAL_NAMES = {
    'United Kingdom': 'United Kingdom',
    'United States': 'United States',
    'European Union': 'European Union',
    'South Korea': 'South Korea',
    'Hong Kong': 'Hong Kong',
    'New Zealand': 'New Zealand',
}


def load_registries():
    """Dynamically load crawler registrations from subpackages."""
    global CRAWLER_BY_COUNTRY_NAME, COUNTRY_CODE_BY_NAME

    package_path = os.path.dirname(__file__)
    for _, name, is_pkg in pkgutil.iter_modules([package_path]):
        if not is_pkg:
            continue

        try:
            module = importlib.import_module(f"app.crawlers.{name}")

            country_name = getattr(module, 'COUNTRY_NAME', None)
            country_code = getattr(module, 'COUNTRY_CODE', None)
            if not country_name:
                base = name.replace('_', ' ').title()
                country_name = _SPECIAL_NAMES.get(base, base)

            if hasattr(module, 'COUNTRY_CRAWLER'):
                CRAWLER_BY_COUNTRY_NAME[country_name] = module.COUNTRY_CRAWLER
                COUNTRY_CODE_BY_NAME[country_name] = country_code
        except Exception as e:
            logger.error(f"Failed to load crawler registry from {name}: {e}")


load_registries()


def get_registered_countries():
    """Return [(country_name, country_code), ...] for every registered crawler."""
    return [(name, COUNTRY_CODE_BY_NAME.get(name)) for name in CRAWLER_BY_COUNTRY_NAME]


def get_crawler_for_country_name(country_name: str):
    """Instantiate the crawler class registered for a country name, or None."""
    if country_name not in CRAWLER_BY_COUNTRY_NAME:
        logger.warning(f"No crawler registered for country: {country_name}")
        return None

    module_name, class_name = CRAWLER_BY_COUNTRY_NAME[country_name]
    try:
        module = importlib.import_module(module_name)
        crawler_class = getattr(module, class_name)
        logger.info(f"Using {class_name} for {country_name}")
        return crawler_class()
    except Exception as e:
        logger.error(f"Failed to load crawler for {country_name}: {e}")
        return None
