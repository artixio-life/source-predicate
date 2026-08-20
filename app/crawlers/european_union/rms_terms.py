"""
SPOR RMS (Referentials Management Service) terminology lookup for EMA PMS.

The PMS FHIR bundles carry only numeric SPOR term ids -- a route of
administration comes back as ``100000073619``, never as "Oral use".  This
module turns those ids into English labels.

Two things are non-obvious about the RMS API and both are load-bearing:

1.  ``Accept: application/fhir+json`` (what the PMS API wants) returns 401
    here.  RMS needs ``Accept: */*`` and answers with XML, not JSON.
2.  The UPD/PMS client credentials do not grant every list.  Several lists
    used by PMS -- notably Dose Form, Country, Product Status, Unit of
    Measurement and ATC -- return 401 for this client.  Those codes are
    passed through unresolved rather than failing the crawl; see
    RESTRICTED_LISTS.

Lists are fetched once per process and cached on disk, because the full
Routes and Methods of Administration list alone is ~1.1 MB and the terms
endpoint is slow.
"""

from __future__ import annotations

import json
import logging
import os
import xml.etree.ElementTree as ET
from typing import Dict, Optional

logger = logging.getLogger(__name__)

RMS_BASE = os.getenv('EMA_RMS_BASE_URL', 'https://spor.ema.europa.eu/v1').rstrip('/')
CACHE_PATH = os.getenv(
    'EMA_RMS_CACHE_PATH',
    os.path.join('.cache', 'ema_rms_terms.json'),
)
SPOR_NS = '{http://ema.europa.eu/schema/spor}'

# SPOR list id -> the field it decodes. Only lists we actually resolve.
LISTS: Dict[str, str] = {
    '100000073345': 'route_of_administration',
    '100000072049': 'authorisation_status',
    '100000154442': 'procedure_type',
    '100000072051': 'legal_status_of_supply',
    '200000000014': 'unit_of_presentation',
    '200000000324': 'product_classification',
    '100000072050': 'ingredient_role',
    '100000000004': 'domain',
    '220000000000': 'name_part_type',
}

# Confirmed 401 for UPD/PMS client credentials as of 2026-08-20. Codes from
# these lists are emitted with label=None instead of blocking the crawl.
RESTRICTED_LISTS = {
    '200000000004': 'dose_form',
    '100000000002': 'country',
    '200000005003': 'product_status',
    '100000110633': 'unit_of_measurement',
    '100000093533': 'atc',
}


def _parse_terms(payload: bytes) -> Dict[str, Optional[str]]:
    """Pull {term_id: english_name} out of an RMS controlled-terms XML doc."""
    root = ET.fromstring(payload)
    terms: Dict[str, Optional[str]] = {}
    for term in root.iter(SPOR_NS + 'controlled-term'):
        term_id = term.find(SPOR_NS + 'term-id')
        if term_id is None or not term_id.get('id'):
            continue
        english = None
        for name in term.iter(SPOR_NS + 'name'):
            if name.get('lang') == 'en':
                english = (name.text or '').strip() or None
                break
        terms[term_id.get('id')] = english
    return terms


class RMSTerminology:
    """Resolves SPOR term ids to English labels, with an on-disk cache."""

    def __init__(self, session, token_provider, timeout: int = 240):
        self._session = session
        self._token = token_provider
        self._timeout = timeout
        self._lists: Dict[str, Dict[str, Optional[str]]] = {}
        self._unavailable = set()
        self._load_cache()

    def _load_cache(self):
        try:
            with open(CACHE_PATH) as handle:
                self._lists = json.load(handle)
            logger.info('Loaded %s cached RMS lists from %s', len(self._lists), CACHE_PATH)
        except (OSError, ValueError):
            self._lists = {}

    def _save_cache(self):
        try:
            os.makedirs(os.path.dirname(CACHE_PATH) or '.', exist_ok=True)
            with open(CACHE_PATH, 'w') as handle:
                json.dump(self._lists, handle)
        except OSError as exc:
            logger.warning('Could not write RMS cache to %s: %s', CACHE_PATH, exc)

    def _fetch_list(self, list_id: str):
        if list_id in self._lists or list_id in self._unavailable:
            return
        if list_id in RESTRICTED_LISTS:
            self._unavailable.add(list_id)
            return

        response = self._session.get(
            f'{RMS_BASE}/lists/{list_id}/terms',
            # RMS rejects application/fhir+json with a 401; it speaks XML.
            headers={'Authorization': f'Bearer {self._token()}', 'Accept': '*/*'},
            timeout=self._timeout,
        )
        if response.status_code != 200:
            logger.warning(
                'RMS list %s (%s) unavailable: HTTP %s -- codes will be left unresolved',
                list_id, LISTS.get(list_id, '?'), response.status_code,
            )
            self._unavailable.add(list_id)
            return

        self._lists[list_id] = _parse_terms(response.content)
        logger.info('Fetched RMS list %s (%s): %s terms',
                    list_id, LISTS.get(list_id, '?'), len(self._lists[list_id]))
        self._save_cache()

    def warm(self):
        """Pre-fetch every resolvable list so the crawl itself does no RMS I/O."""
        for list_id in LISTS:
            self._fetch_list(list_id)

    def label(self, list_id: Optional[str], code: Optional[str]) -> Optional[str]:
        if not list_id or not code:
            return None
        self._fetch_list(list_id)
        return (self._lists.get(list_id) or {}).get(code)
