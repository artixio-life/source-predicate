"""
Flattens an EMA PMS ``$everything`` FHIR bundle into a flat, human-readable
record.

The raw bundle is a graph of eight resource types cross-referencing each other
by FHIR reference, with every categorical value expressed as a numeric SPOR
term id.  Downstream consumers want a row, not a graph, so this module walks
the graph once and emits the regulatory fields we actually care about, with
SPOR codes resolved to English labels wherever the credentials allow.

Every coded field is emitted as ``{'code': ..., 'label': ...}`` so a code whose
list is restricted (dose form, ATC, country -- see rms_terms.RESTRICTED_LISTS)
still round-trips as data instead of silently vanishing.
"""

from __future__ import annotations

import collections
from typing import Dict, List, Optional

# SPOR list ids, repeated here so the shapes below read as regulatory fields
# rather than as magic numbers.
L_ROUTE = '100000073345'
L_AUTH_STATUS = '100000072049'
L_PROCEDURE = '100000154442'
L_LEGAL_STATUS = '100000072051'
L_UNIT_PRESENTATION = '200000000014'
L_CLASSIFICATION = '200000000324'
L_INGREDIENT_ROLE = '100000072050'
L_DOMAIN = '100000000004'
L_DOSE_FORM = '200000000004'
L_COUNTRY = '100000000002'
L_PRODUCT_STATUS = '200000005003'
L_ATC = '100000093533'

# Name part types (list 220000000000).
PART_INVENTED = '220000000002'
PART_SCIENTIFIC = '220000000003'
PART_STRENGTH = '220000000004'
PART_DOSE_FORM = '220000000005'

ACTIVE_INGREDIENT_ROLE = '100000072072'


def _first_coding(concept: Optional[dict]) -> dict:
    if not isinstance(concept, dict):
        return {}
    coding = concept.get('coding')
    return coding[0] if isinstance(coding, list) and coding else {}


def _coded(concept: Optional[dict], list_id: str, terms) -> Optional[dict]:
    """A CodeableConcept -> {'code', 'label'}, or None when absent."""
    coding = _first_coding(concept)
    code = coding.get('code')
    if not code:
        return None
    return {'code': code, 'label': terms.label(list_id, code)}


def _group(bundle: dict) -> Dict[str, List[dict]]:
    grouped: Dict[str, List[dict]] = collections.defaultdict(list)
    for entry in bundle.get('entry') or []:
        resource = entry.get('resource') if isinstance(entry, dict) else None
        if isinstance(resource, dict) and resource.get('resourceType'):
            grouped[resource['resourceType']].append(resource)
    return grouped


def _extension(resource: dict, suffix: str) -> Optional[str]:
    for ext in resource.get('extension') or []:
        if str(ext.get('url', '')).endswith(suffix):
            return ext.get('valueDateTime') or ext.get('valueString')
    return None


def _name_parts(product: dict) -> Dict[str, Optional[str]]:
    """Product names are pre-decomposed by EMA into typed parts -- use them."""
    parts: Dict[str, Optional[str]] = {
        'invented_name': None,
        'scientific_name': None,
        'strength_part': None,
        'dose_form_part': None,
    }
    key_by_type = {
        PART_INVENTED: 'invented_name',
        PART_SCIENTIFIC: 'scientific_name',
        PART_STRENGTH: 'strength_part',
        PART_DOSE_FORM: 'dose_form_part',
    }
    for name in product.get('name') or []:
        for part in name.get('part') or []:
            key = key_by_type.get(_first_coding(part.get('type')).get('code'))
            if key and not parts[key]:
                parts[key] = (part.get('part') or '').strip() or None
    return parts


def _substances(ingredients: List[dict], terms) -> List[dict]:
    """Active substances with their strengths, de-duplicated by SMS code.

    PMS repeats each ingredient once per ManufacturedItemDefinition and once
    per AdministrableProductDefinition, so the same substance shows up twice.
    """
    seen: Dict[str, dict] = {}
    for ingredient in ingredients:
        role_code = _first_coding(ingredient.get('role')).get('code')
        substance = ingredient.get('substance') or {}
        sms_code = _first_coding((substance.get('code') or {}).get('concept')).get('code')
        if not sms_code:
            continue

        strengths = []
        for strength in substance.get('strength') or []:
            ratio = strength.get('presentationRatio') or {}
            numerator = ratio.get('numerator') or {}
            denominator = ratio.get('denominator') or {}
            strengths.append({
                'text': strength.get('textPresentation'),
                'value': numerator.get('value'),
                'unit_code': numerator.get('code'),
                'per_value': denominator.get('value'),
                'per_unit': _coded(
                    {'coding': [denominator]} if denominator.get('code') else None,
                    L_UNIT_PRESENTATION, terms,
                ),
            })

        existing = seen.setdefault(sms_code, {
            # SMS substance names need SubstanceDefinition access, which the
            # PMS credentials do not carry -- the code is the INN handle.
            'sms_code': sms_code,
            'is_active': role_code == ACTIVE_INGREDIENT_ROLE,
            'role': {'code': role_code, 'label': terms.label(L_INGREDIENT_ROLE, role_code)},
            'strengths': [],
        })
        for strength in strengths:
            existing['strengths'] = _merge_strength(existing['strengths'], strength)
    return list(seen.values())


def _merge_strength(collected: List[dict], candidate: dict) -> List[dict]:
    """Add a strength, collapsing the same value reported with and without text.

    The manufactured-item and administrable-product ingredients carry the same
    numbers, but only one of the two sets textPresentation -- naively appending
    both leaves a null-text twin next to every real strength.
    """
    def numbers(entry):
        return (entry.get('value'), entry.get('unit_code'), entry.get('per_value'))

    for index, existing in enumerate(collected):
        if numbers(existing) != numbers(candidate):
            continue
        if not existing.get('text') and candidate.get('text'):
            collected[index] = candidate
        return collected
    collected.append(candidate)
    return collected


def flatten(bundle: dict, source_url: str, retrieved_at: str, terms) -> dict:
    """Turn one $everything bundle into a flat record."""
    grouped = _group(bundle)
    product = (grouped.get('MedicinalProductDefinition') or [{}])[0]
    authorisations = grouped.get('RegulatedAuthorization') or []
    packages = grouped.get('PackagedProductDefinition') or []
    administrables = grouped.get('AdministrableProductDefinition') or []
    manufactured = grouped.get('ManufacturedItemDefinition') or []
    ingredients = grouped.get('Ingredient') or []

    # Product-level authorisations are the ones whose subject is the product
    # itself; the rest are per-pack (EU/1/20/1432/001 etc).  A product can
    # carry several product-level entries and only one of them holds the
    # procedure and the first-authorisation date, so rank rather than take
    # the first -- picking blindly loses the approval date on ~10% of records.
    product_auths = [
        auth for auth in authorisations
        if any('MedicinalProductDefinition' in (subject.get('reference') or '')
               for subject in auth.get('subject') or [])
    ]
    product_auth = next(
        (a for a in product_auths if _extension(a, 'dateOfFirstAuthorisation')),
        next((a for a in product_auths if a.get('case')), None),
    ) or (product_auths[0] if product_auths else None)

    routes, seen_routes = [], set()
    for administrable in administrables:
        for route in administrable.get('routeOfAdministration') or []:
            coded = _coded(route.get('code'), L_ROUTE, terms)
            if coded and coded['code'] not in seen_routes:
                seen_routes.add(coded['code'])
                routes.append(coded)

    dose_forms, seen_forms = [], set()
    for resource, key in (
        [(m, 'manufacturedDoseForm') for m in manufactured]
        + [(a, 'administrableDoseForm') for a in administrables]
    ):
        coded = _coded(resource.get(key), L_DOSE_FORM, terms)
        if coded and coded['code'] not in seen_forms:
            seen_forms.add(coded['code'])
            dose_forms.append(coded)

    packs = []
    for package in packages:
        pack_ref = f"PackagedProductDefinition/{package.get('id')}"
        pack_authorisations = [
            identifier.get('value')
            for auth in authorisations
            if any((s.get('reference') or '') == pack_ref for s in auth.get('subject') or [])
            for identifier in auth.get('identifier') or []
            if 'MarketingAuthorizationNumber' in (identifier.get('system') or '')
        ]
        quantity = (package.get('containedItemQuantity') or [{}])[0]
        packs.append({
            'pack_id': package.get('id'),
            'description': package.get('description'),
            'registration_numbers': pack_authorisations,
            'quantity': quantity.get('value'),
            'quantity_unit': _coded(
                {'coding': [quantity]} if quantity.get('code') else None,
                L_UNIT_PRESENTATION, terms,
            ),
        })

    holders, seen_holders = [], set()
    for auth in authorisations:
        holder = auth.get('holder') or {}
        display = holder.get('display')
        if display and display not in seen_holders:
            seen_holders.add(display)
            holders.append({
                'name': display,
                'location_id': (holder.get('identifier') or {}).get('value'),
            })

    case = (product_auth or {}).get('case') or {}
    registration_numbers = sorted({
        identifier.get('value')
        for auth in authorisations
        for identifier in auth.get('identifier') or []
        if 'MarketingAuthorizationNumber' in (identifier.get('system') or '')
        and identifier.get('value')
    })

    classifications = []
    for classification in product.get('classification') or []:
        coding = _first_coding(classification)
        list_id = str(coding.get('system', '')).rsplit('/', 1)[-1]
        code = coding.get('code')
        if code:
            classifications.append({
                'code': code,
                'label': terms.label(list_id, code),
                'scheme': 'atc' if list_id == L_ATC else 'product_classification',
            })

    substances = _substances(ingredients, terms)
    name_parts = _name_parts(product)

    return {
        # 1. Brand name
        'brand_name': next(
            (n.get('productName') for n in product.get('name') or [] if n.get('productName')),
            None,
        ),
        'name_parts': name_parts,
        # 2. Route of administration
        'route_of_administration': routes,
        # 3. Approval date
        'approval_date': _extension(product_auth or {}, 'dateOfFirstAuthorisation'),
        'status_date': (product_auth or {}).get('statusDate'),
        # 4. Procedure (CP / DCP / MRP / national)
        'procedure': {
            'number': (case.get('identifier') or {}).get('value'),
            **(_coded(case.get('type'), L_PROCEDURE, terms) or {'code': None, 'label': None}),
        },
        # 5. INN / active moiety (SMS substance codes; names need SMS access)
        'active_substances': [s for s in substances if s['is_active']],
        'all_substances': substances,
        # 6. Presentation / pack
        'packs': packs,
        # 7. Marketing status
        'marketing_status': _coded(product.get('status'), L_PRODUCT_STATUS, terms),
        # The authorisation carrying the procedure often carries no status of
        # its own; the sibling product-level entries do.
        'authorisation_status': _coded(
            next((a.get('status') for a in product_auths if a.get('status')), None),
            L_AUTH_STATUS, terms,
        ),
        # 8. Indication
        'indication': product.get('indication'),
        # 9. Dosage form
        'dosage_form': dose_forms,
        # 10. Marketing authorisation holder
        'marketing_authorisation_holder': holders,
        # 11. Manufacturing site -- not published in the PMS public bundle.
        'manufacturing_sites': [],
        # 12/13. Qualitative composition and strength
        'composition': [
            {
                'sms_code': s['sms_code'],
                'role': s['role'],
                'strength_text': [x['text'] for x in s['strengths'] if x.get('text')],
            }
            for s in substances
        ],
        'strengths': [x for s in substances for x in s['strengths']],
        # 14. Registration number
        'registration_numbers': registration_numbers,
        # 15. RS / RLD -- no EU equivalent is published in PMS.
        'reference_product_status': None,
        # 16. Source URL and retrieval stamp
        'source_url': source_url,
        'retrieved_at': retrieved_at,
        'source_last_updated': (product.get('meta') or {}).get('lastUpdated'),
        # 17. Specification / STP -- not published in PMS.
        'specification_stp': None,
        # Extras that come free and are worth keeping.
        'pms_id': product.get('id'),
        'product_version': product.get('version'),
        'domain': _coded(product.get('domain'), L_DOMAIN, terms),
        'legal_status_of_supply': _coded(product.get('legalStatusOfSupply'), L_LEGAL_STATUS, terms),
        'classifications': classifications,
        'additional_monitoring': _first_coding(
            product.get('additionalMonitoringIndicator')).get('code'),
        'paediatric_use': _first_coding(product.get('pediatricUseIndicator')).get('code'),
        'regulator': ((product_auth or {}).get('regulator') or {}).get('display'),
        'region': _coded((((product_auth or {}).get('region') or [{}])[0]), L_COUNTRY, terms),
    }
