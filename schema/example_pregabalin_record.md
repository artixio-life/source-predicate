# Worked example — Pregabalin Wockhardt 225 mg (PL 29831/0647)

One row in `source.products`, populated from the SmPC PDF.

| Column | Value |
| ------ | ----- |
| id | `a3f1…` |
| **product_name** | `Pregabalin Wockhardt 225 mg Hard Capsules` |
| brand_name | `Pregabalin Wockhardt` |
| brand_name_local | *NULL* (source is English) |
| generic_name | `Pregabalin` |
| **country_id** | `1` → GB |
| regulator | `MHRA` |
| **mah_name** | `Wockhardt UK Ltd` |
| **mah_address** | `Ash Road North, Wrexham LL13 9UF` |
| manufacturer | *NULL* — the SmPC does not state it |
| registration_number | `PL 29831/0647` |
| product_type | `Prescription Medicine` |
| status | `Active` |
| approval_date | `2021-08-31` |
| **label_revision_date** | `2026-01-15` |
| **atc_code** | `N03AX16` |
| therapeutic_areas | `{Neurology, Psychiatry, Pain Management}` |
| indications | `{Neuropathic pain, Partial seizures, Generalised Anxiety Disorder}` |
| symptoms | `{Neuropathic pain, Seizures, Anxiety}` |
| **adverse_reactions** | `{Dizziness, Somnolence, Headache, Ataxia, Tremor, …}` (~150 terms) |
| **contraindications** | `{Hypersensitivity to pregabalin or any excipient}` |
| active_ingredients | `{Pregabalin}` |
| **strengths** | `{225 mg}` |
| dosage_forms | `{Hard capsule}` |
| routes | `{Oral}` |
| **is_generic** | `true` |
| **reference_product** | `Lyrica` |
| **source_url** | *(MHRA document URL)* |
| **source_language** | `en` |
| **is_active** | `true` |
| **processing_status** | `ENRICHED` |

`status='Active'` and `is_active=true` answer different questions. A withdrawn
product is `status='Withdrawn'`, `is_active=true` — the row is still current truth.

## Chinese record — the naming columns in use

| Column | Value |
| ------ | ----- |
| product_name | `Pregabalin Capsules` *(translated)* |
| brand_name | `Lyrica` *(translated)* |
| **brand_name_local** | `乐瑞卡` *(as printed)* |
| generic_name | `Pregabalin` |
| source_language | `zh` |

## Full extraction inventory

> **Not the storage spec.** This is everything the document contains, kept as a
> reference for building the parser. What actually gets stored in `product_data`
> is the six-key predicate subset in [product_data_spec.md](product_data_spec.md).

```json
{
  "_schema_version": "1",

  "presentations": [
    {
      "strength": "225 mg",
      "dosage_form": "Capsule, hard",
      "route": "Oral",
      "ingredients": [
        { "substance": "Pregabalin", "value": 225, "unit": "mg" }
      ],
      "appearance": {
        "capsule_size": "1",
        "cap_colour": "Pinkish-orange",
        "body_colour": "White",
        "imprint": "225",
        "print_colour": "Black",
        "fill": "White to off-white powder"
      },
      "packaging": {
        "container": "Blister",
        "material": "PVC/Aluminium",
        "pack_sizes": [10, 14, 20, 21, 28, 30, 56, 60, 84, 90, 100],
        "multipacks": ["84 (2 x 42)", "112 (2 x 56)", "120 (2 x 60)", "200 (2 x 100)"],
        "note": "Not all pack sizes may be marketed"
      }
    }
  ],

  "dosing": {
    "dose_range": { "min": 150, "max": 600, "unit": "mg/day" },
    "frequency": "2 or 3 divided doses",
    "food_effect": "May be taken with or without food",
    "discontinuation": "Taper gradually over a minimum of 1 week",
    "titration": [
      { "indication": "Neuropathic pain", "start": "150 mg/day",
        "steps": ["300 mg/day after 3-7 days"], "max": "600 mg/day after a further 7 days" },
      { "indication": "Epilepsy", "start": "150 mg/day",
        "steps": ["300 mg/day after 1 week"], "max": "600 mg/day after a further week" },
      { "indication": "Generalised Anxiety Disorder", "start": "150 mg/day",
        "steps": ["300 mg/day after 1 week", "450 mg/day after a further week"],
        "max": "600 mg/day after a further week" }
    ],
    "renal_adjustment": [
      { "crcl_min": 60,   "crcl_max": null, "start_mg_day": 150,    "max_mg_day": 600, "regimen": "BID or TID" },
      { "crcl_min": 30,   "crcl_max": 60,   "start_mg_day": 75,     "max_mg_day": 300, "regimen": "BID or TID" },
      { "crcl_min": 15,   "crcl_max": 30,   "start_mg_day": "25-50","max_mg_day": 150, "regimen": "Once daily or BID" },
      { "crcl_min": null, "crcl_max": 15,   "start_mg_day": 25,     "max_mg_day": 75,  "regimen": "Once daily" }
    ],
    "haemodialysis_supplement": { "start_mg": 25, "max_mg": 100, "regimen": "Single dose after each 4-hour session" },
    "special_populations": {
      "hepatic": "No dose adjustment required",
      "paediatric": "Not established below 12 years or in adolescents 12-17",
      "elderly": "May require reduction due to decreased renal function"
    }
  },

  "adverse_reaction_detail": {
    "patients_exposed": 8900,
    "discontinuation_rate_active": 0.12,
    "discontinuation_rate_placebo": 0.05,
    "by_soc": [
      { "soc": "Nervous system disorders", "frequency": "very_common",
        "terms": ["Dizziness", "Somnolence", "Headache"] },
      { "soc": "Nervous system disorders", "frequency": "common",
        "terms": ["Ataxia", "Tremor", "Dysarthria", "Amnesia", "Paraesthesia",
                  "Sedation", "Balance disorder", "Lethargy"] },
      { "soc": "Psychiatric disorders", "frequency": "not_known",
        "terms": ["Drug dependence"], "postmarketing": true }
    ]
  },

  "warnings": [
    { "topic": "Suicidal ideation and behaviour", "severity": "high" },
    { "topic": "Respiratory depression", "severity": "high",
      "risk_factors": ["compromised respiratory function", "renal impairment",
                       "concomitant CNS depressants", "elderly"] },
    { "topic": "Drug dependence, tolerance and abuse potential", "severity": "high" },
    { "topic": "Severe cutaneous adverse reactions", "severity": "high",
      "events": ["Stevens-Johnson syndrome", "Toxic epidermal necrolysis"] },
    { "topic": "Concomitant use with opioids", "severity": "high",
      "evidence": { "outcome": "opioid-related death", "aor": 1.68, "ci95": [1.19, 2.36] } },
    { "topic": "Lactose intolerance", "severity": "low" }
  ],

  "interactions": [
    { "substance": "Ethanol",   "type": "potentiates", "effect": "Potentiated CNS effects" },
    { "substance": "Lorazepam", "type": "potentiates", "effect": "Potentiated CNS effects" },
    { "substance": "Opioids",   "type": "caution",
      "effect": "Respiratory failure, coma and death reported" },
    { "substance": "Phenytoin",         "type": "no_interaction" },
    { "substance": "Carbamazepine",     "type": "no_interaction" },
    { "substance": "Valproic acid",     "type": "no_interaction" },
    { "substance": "Ethinyl oestradiol","type": "no_interaction" }
  ],

  "pharmacokinetics": {
    "bioavailability_pct": ">=90",
    "tmax_hours": 1,
    "tmax_hours_with_food": 2.5,
    "cmax_reduction_with_food_pct": [25, 30],
    "volume_of_distribution_l_kg": 0.56,
    "plasma_protein_binding_pct": 0,
    "metabolism": "Negligible; 98% recovered unchanged in urine",
    "half_life_hours": 6.3,
    "elimination_route": "Renal, unchanged drug",
    "dialyzable": true,
    "dialysis_removal_pct": 50
  },

  "pregnancy_lactation": {
    "pregnancy_use": "Not to be used unless clearly necessary",
    "mcm_rate_exposed_pct": 5.9,
    "mcm_rate_unexposed_pct": 4.1,
    "adjusted_prevalence_ratio": 1.14,
    "apr_ci95": [0.96, 1.35],
    "breastfeeding": "Excreted in human milk; milk:plasma ratio ~0.76",
    "contraception_required": true,
    "neonatal_withdrawal_risk": true
  },

  "overdose": {
    "symptoms": ["Somnolence", "Confusional state", "Agitation", "Restlessness",
                 "Seizures", "Coma"],
    "management": "General supportive measures; haemodialysis if necessary"
  },

  "excipients": [
    { "name": "Lactose monohydrate", "component": "content", "quantity": "24.75 mg", "known_effect": true },
    { "name": "Maize starch",        "component": "content" },
    { "name": "Talc",                "component": "content", "e_number": "E553b" },
    { "name": "Gelatin",             "component": "shell" },
    { "name": "Titanium dioxide",    "component": "shell", "e_number": "E171" },
    { "name": "Red iron oxide",      "component": "shell", "e_number": "E172" },
    { "name": "Yellow iron oxide",   "component": "shell", "e_number": "E172" },
    { "name": "Shellac",             "component": "ink" },
    { "name": "Black iron oxide",    "component": "ink",   "e_number": "E172" },
    { "name": "Propylene glycol",    "component": "ink" },
    { "name": "Potassium hydroxide", "component": "ink" }
  ],

  "storage": {
    "shelf_life_months": 36,
    "conditions": "No special storage conditions required"
  },

  "raw_sections": {
    "4.3": "Hypersensitivity to the active substance or to any of the excipients listed in section 6.1.",
    "4.4": "…verbatim text…"
  }
}
```

Two details in there worth keeping when you write the parser:

**Salt vs base.** Pregabalin is quoted as the base so it is simple, but your
Tofacitinib record is not: `Tofacitinib citrate 8.075 mg equivalent to
tofacitinib 5 mg`. Keep both in the ingredient entry —
`{"substance": "Tofacitinib citrate", "value": 8.075, "unit": "mg",
"equivalent_to": {"substance": "Tofacitinib", "value": 5, "unit": "mg"}}`.
Storing only the base loses the salt; storing only the salt makes it
un-matchable against a country that quotes the base.

**Excipient component.** Gelatin is in the *shell*, not the contents. Keeping
`component` is what lets you derive an animal-derived flag later; a flat
excipient list cannot.
