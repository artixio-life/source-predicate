# Worked example — ANSENTRON (ANVISA, Brazil)

**Source:** `1786801266199_ANSENTRON_1293416_bula_profissional.pdf` — 12 pages
**Type:** Bula para Profissional da Saúde (VPS), per Resolução-RDC nº 47/2009
**Registro:** 1.0573.0562

This record exercises three things the Pregabalin one did not: two strengths
under a single registration number, a MAH whose address differs from the
manufacturer's, and a non-English source document.

---

## The row in `source.products`

| Column | Value | Source in document |
| ------ | ----- | ------------------ |
| id | `c9d4…` | generated |
| **product_name** | `ANSENTRON Solução Injetável 4 mg/2 mL e 8 mg/4 mL` | cover page |
| brand_name | `Ansentron` | I – Identificação |
| brand_name_local | *NULL* — Latin script, identical | — |
| **generic_name** | `Ondansetron` | INN, normalised from `cloridrato de ondansetrona di-hidratado` |
| **country_id** | `→ BR` | Indústria Brasileira / Dizeres Legais |
| regulator | `ANVISA` | RDC references throughout |
| **mah_name** | `Aché Laboratórios Farmacêuticos S.A.` | "Registrado por" |
| **mah_address** | `Av. Brigadeiro Faria Lima, 201 – 1º ao 4º andar, São Paulo – SP` | "Registrado por" |
| **manufacturer** | `Aché Laboratórios Farmacêuticos S.A.` | "Produzido por" |
| **manufacturer_address** | `Av. das Nações Unidas, 22.428, São Paulo – SP` | "Produzido por" |
| **registration_number** | `1.0573.0562` | III – Dizeres Legais |
| product_type | `Medicamento Similar` | "MEDICAMENTO SIMILAR EQUIVALENTE AO MEDICAMENTO DE REFERÊNCIA" |
| status | *NULL* | **not in this document** — see traps below |
| registration_date | *NULL* | **not in this document** |
| approval_date | *NULL* | **not in this document** |
| market_authorization_date | *NULL* | not in this document |
| expiry_date | *NULL* | not in this document |
| withdrawal_date | *NULL* | n/a |
| **label_revision_date** | `2026-06-10` | most recent row of Histórico de Alterações da Bula |
| **atc_code** | `A04AA01` | **enriched, not extracted** — the bula has no ATC code |
| therapeutic_areas | `{Oncology, Anaesthesiology, Supportive Care}` | derived from indications |
| **indications** | `{Chemotherapy-induced nausea and vomiting, Radiotherapy-induced nausea and vomiting, Post-operative nausea and vomiting}` | 1. Indicações |
| symptoms | `{Nausea, Vomiting}` | 1. Indicações |
| **adverse_reactions** | `{Headache, Flushing, Constipation, Injection site reaction, Seizure, Extrapyramidal disorder, Oculogyric crisis, Dystonic reaction, Dyskinesia, Arrhythmia, Chest pain, ST segment depression, Bradycardia, Hypotension, Hiccups, Hepatic enzyme increased, Hypersensitivity, Anaphylaxis, Dizziness, Visual disturbance, Blurred vision, QT prolongation, Torsades de pointes, Transient blindness, Toxic epidermal necrolysis, Myocardial ischaemia}` | 9. Reações Adversas |
| **contraindications** | `{Hypersensitivity to any component of the formulation, Concomitant use with apomorphine}` | 4. Contraindicações |
| **active_ingredients** | `{Ondansetron}` | Composição (base, not salt) |
| **strengths** | `{4 mg/2 mL, 8 mg/4 mL}` | Apresentações — **two, one registration** |
| dosage_forms | `{Solution for injection}` | Apresentações |
| **routes** | `{Intravenous, Intramuscular}` | "USO INTRAVENOSO OU INTRAMUSCULAR" |
| **is_generic** | `true` | "MEDICAMENTO SIMILAR" — but see note |
| reference_product | `Zofran` | implied by "equivalente ao medicamento de referência"; **enriched** |
| **source_url** | *(ANVISA bulário URL)* | — |
| **source_language** | `pt` | — |
| is_active | `true` | row currency |
| processing_status | `ENRICHED` | ATC + reference product added post-parse |

---

## Four mapping decisions this document forces

### 1. Two strengths, one registration number → one row

The cover page says `4 mg/2 ml e 8 mg/4 ml` and the Dizeres Legais gives a
single `Registro: 1.0573.0562`. ANVISA registers the product, not the strength.

So: **one row**, `strengths = {'4 mg/2 mL', '8 mg/4 mL'}`, with the per-strength
composition and packaging in `product_data.presentations`.

Contrast with MHRA (Pregabalin), where each strength has its own PL and
therefore its own row. Same document shape, opposite outcome — which is why
that branch belongs to the regulator, not the parser's guess.

### 2. MAH address ≠ manufacturer address

```
Registrado por: Aché Laboratórios Farmacêuticos S.A.
                Av. Brigadeiro Faria Lima, 201 – 1º ao 4º andar, São Paulo – SP
                CNPJ 60.659.463/0029-92

Produzido por:  Aché Laboratórios Farmacêuticos S.A.
                Av. das Nações Unidas, 22.428, São Paulo – SP
```

Same company, two different sites — the registered office and the plant. A
single `manufacturer` column would have silently dropped one of them. This is
the concrete case that justifies the `mah_*` / `manufacturer_*` split.

### 3. Salt vs base

```
Cada ampola de Ansentron 4 mg contém:
cloridrato de ondansetrona di-hidratado ................ 5 mg
(equivalente a 4 mg de ondansetrona base)
```

`active_ingredients = {Ondansetron}` — the **base**, because that is what
matches against the UK/AU/CN records. The salt and its quantity survive in
`product_data.presentations[].ingredients`. Store only `5 mg` and this product
stops matching every other country's ondansetron; store only `4 mg` and you
lose which salt was used.

### 4. Arrays are normalised to English; Portuguese stays verbatim in JSON

`adverse_reactions` holds `Headache`, not `cefaleia`. The arrays are the
cross-country search index, so they must be in one language — otherwise a query
for products causing headache silently misses every Brazilian record.

The Portuguese is not discarded: it lives in `product_data.raw_sections` and in
the `term_local` field of each ADR entry, so a mistranslation is fixable without
re-crawling.

---

## Traps in this document

**Do not map "validade de 24 meses" to `expiry_date`.** It is shelf life from
manufacture, not a date. It goes to `product_data.storage.shelf_life_months = 24`.
`expiry_date` stays NULL.

**Do not infer `status` or `registration_date`.** Neither appears anywhere in
the bula. The Histórico table gives *label* revision dates (2013–2026), not the
registration date. A published bula suggests the product is active, but that is
an inference — those fields come from the ANVISA registry, not this PDF. Leave
them NULL rather than guessing.

**`is_generic = true` flattens a real distinction.** Brazil has three
categories: *genérico*, *similar*, and *referência*. This is a **similar** —
brand-named, equivalent to the reference, but not a substitutable generic in the
Brazilian sense. `product_type = 'Medicamento Similar'` carries the nuance;
`is_generic` alone would lose it.

**`atc_code` is enrichment, not extraction.** ANVISA bulas do not carry ATC
codes. `A04AA01` comes from an ondansetron lookup after parsing — which is
exactly why `processing_status` distinguishes `PARSED` from `ENRICHED`.

---

## Full extraction inventory

> **Not the storage spec.** This is everything the document contains, kept as a
> reference for building the parser. What actually gets stored in `product_data`
> is the six-key predicate subset in [product_data_spec.md](product_data_spec.md).

```json
{
  "_schema_version": "1",

  "presentations": [
    {
      "strength": "4 mg/2 mL",
      "strength_value": 4, "strength_unit": "mg",
      "per_value": 2, "per_unit": "mL",
      "dosage_form": "Solução injetável",
      "dosage_form_en": "Solution for injection",
      "routes": ["Intravenous", "Intramuscular"],
      "ingredients": [
        {
          "substance": "Cloridrato de ondansetrona di-hidratado",
          "substance_en": "Ondansetron hydrochloride dihydrate",
          "value": 5, "unit": "mg",
          "equivalent_to": { "substance": "Ondansetron", "value": 4, "unit": "mg", "basis": "base" }
        }
      ],
      "packaging": {
        "container": "Ampoule",
        "fill_volume_ml": 2,
        "pack_sizes": [1, 100],
        "single_use": true,
        "note": "Ampolas devem ser usadas somente uma vez; não devem ser autoclavadas"
      }
    },
    {
      "strength": "8 mg/4 mL",
      "strength_value": 8, "strength_unit": "mg",
      "per_value": 4, "per_unit": "mL",
      "dosage_form": "Solução injetável",
      "dosage_form_en": "Solution for injection",
      "routes": ["Intravenous", "Intramuscular"],
      "ingredients": [
        {
          "substance": "Cloridrato de ondansetrona di-hidratado",
          "substance_en": "Ondansetron hydrochloride dihydrate",
          "value": 10, "unit": "mg",
          "equivalent_to": { "substance": "Ondansetron", "value": 8, "unit": "mg", "basis": "base" }
        }
      ],
      "packaging": {
        "container": "Ampoule",
        "fill_volume_ml": 4,
        "pack_sizes": [1, 100],
        "single_use": true
      }
    }
  ],

  "excipients": [
    { "name": "Sodium chloride",        "name_local": "cloreto de sódio" },
    { "name": "Citric acid",            "name_local": "ácido cítrico" },
    { "name": "Sodium citrate dihydrate","name_local": "citrato de sódio di-hidratado" },
    { "name": "Water for injection",    "name_local": "água para injetáveis" }
  ],

  "indications_detail": [
    {
      "condition": "Chemotherapy-induced nausea and vomiting",
      "condition_local": "Náuseas e vômitos induzidos por quimioterapia",
      "population": "Adults and children", "age_min": "6 months"
    },
    {
      "condition": "Radiotherapy-induced nausea and vomiting",
      "population": "Adults and children", "age_min": "6 months"
    },
    {
      "condition": "Post-operative nausea and vomiting",
      "condition_local": "Náuseas e vômitos pós-operatórios",
      "population": "Adults and children", "age_min": "1 month",
      "use": ["prevention", "treatment"]
    }
  ],

  "dosing": {
    "routes": ["Intravenous", "Intramuscular"],
    "cinv": {
      "adults": {
        "standard": "8 mg IV or IM immediately before treatment",
        "highly_emetogenic": "Single IV dose up to 16 mg infused over 15 min",
        "max_single_iv_dose_mg": 16,
        "follow_up": "Two further 8 mg doses at 2-4 hour intervals, or constant infusion 1 mg/h for up to 24 h",
        "dexamethasone_combination": "20 mg dexamethasone sodium phosphate IV pre-chemotherapy increases efficacy",
        "dilution_rule": "Doses >8 mg and <=16 mg must be diluted in 50-100 mL 0.9% NaCl or 5% dextrose and infused over >=15 min"
      },
      "paediatric_bsa": [
        { "bsa": ">=0.6 to <=1.2 m2", "day_1": "5 mg/m2 IV, then 4 mg orally after 12 h", "days_2_to_6": "4 mg orally every 12 h" },
        { "bsa": ">1.2 m2",           "day_1": "5 or 8 mg/m2 IV, then 8 mg orally after 12 h", "days_2_to_6": "8 mg orally every 12 h" }
      ],
      "paediatric_weight": [
        { "weight": ">10 kg", "day_1": "Up to 3 doses of 0.15 mg/kg IV every 4 h", "days_2_to_6": "4 mg orally every 12 h" }
      ],
      "paediatric_max_iv_dose_mg": 8
    },
    "ponv": {
      "adults": { "prevention": "Single 4 mg dose IM or slow IV at induction of anaesthesia",
                  "treatment": "Single 4 mg dose IM or slow IV" },
      "paediatric": { "dose": "0.1 mg/kg slow IV", "max_mg": 4, "age_min": "1 month" }
    },
    "elderly": [
      { "age": ">=65 years", "rule": "All IV doses diluted and infused over 15 min; repeat interval >=4 h" },
      { "age": "65-74 years", "rule": "Initial IV 8 mg or 16 mg over 15 min, then two 8 mg doses over 15 min, interval >=4 h" },
      { "age": ">=75 years",  "rule": "Initial IV dose must not exceed 8 mg over 15 min, then two 8 mg doses, interval >=4 h" }
    ],
    "renal_impairment": "No change to route, daily dose or frequency",
    "hepatic_impairment": "Moderate or severe: total daily dose (IV or oral) must not exceed 8 mg",
    "cyp2d6_poor_metabolisers": "No dose change required"
  },

  "iv_compatibility": {
    "rule": "Must not be mixed in the same syringe or infusion as any other medicine; only recommended infusion fluids",
    "stability": "Stable 7 days below 25 °C under fluorescent light, or refrigerated",
    "container_materials": ["PVC bags", "PET bags", "Type 1 glass bottles", "Polypropylene syringes"],
    "compatible_fluids": [
      "Sodium chloride 0.9% w/v", "Glucose 5% w/v", "Mannitol 10% w/v", "Ringer's solution",
      "Potassium chloride 0.3% + sodium chloride 0.9% w/v",
      "Potassium chloride 0.3% + glucose 5% w/v"
    ],
    "compatible_drugs": [
      { "drug": "Cisplatin",    "concentration": "up to 0.48 mg/mL", "duration": "1-8 h" },
      { "drug": "Fluorouracil", "concentration": "up to 0.8 mg/mL",  "note": "High concentrations may precipitate ondansetron" },
      { "drug": "Carboplatin",  "concentration": "0.18-9.9 mg/mL",   "duration": "10 min-1 h" },
      { "drug": "Etoposide",    "concentration": "0.144-0.25 mg/mL", "duration": "30 min-1 h" },
      { "drug": "Ceftazidime",  "concentration": "250-2000 mg reconstituted", "duration": "~5 min bolus" },
      { "drug": "Cyclophosphamide", "concentration": "100 mg-1 g",   "duration": "~5 min bolus" },
      { "drug": "Doxorubicin",  "concentration": "10-100 mg",        "duration": "~5 min bolus" },
      { "drug": "Dexamethasone sodium phosphate", "concentration": "32 mcg-2.5 mg/mL" }
    ]
  },

  "adverse_reaction_detail": {
    "frequency_definitions": {
      "very_common": ">1/10", "common": ">1/100 and <=1/10", "uncommon": ">1/1000 and <=1/100",
      "rare": ">1/10000 and <=1/1000", "very_rare": "<=1/10000", "unknown": "spontaneous reports"
    },
    "by_frequency": [
      { "frequency": "very_common", "terms": ["Headache"], "terms_local": ["Cefaleia"] },
      { "frequency": "common",
        "terms": ["Sensation of warmth or flushing", "Constipation", "Injection site reaction"],
        "terms_local": ["Sensação de calor ou rubor", "Constipação", "Reações no local da injeção intravenosa"] },
      { "frequency": "uncommon",
        "terms": ["Seizure", "Movement disorders including extrapyramidal disturbances",
                  "Oculogyric crisis", "Dystonic reaction", "Dyskinesia", "Arrhythmia",
                  "Chest pain with or without ST segment depression", "Bradycardia",
                  "Hypotension", "Hiccups", "Asymptomatic increase in liver function tests"],
        "note": "Hepatic enzyme elevations observed in patients receiving cisplatin chemotherapy" },
      { "frequency": "rare",
        "terms": ["Immediate hypersensitivity reaction, sometimes severe including anaphylaxis",
                  "Dizziness during rapid IV administration", "Transient visual disturbance such as blurred vision",
                  "QT prolongation including torsades de pointes"] },
      { "frequency": "very_rare",
        "terms": ["Transient blindness, predominantly during IV administration",
                  "Toxic skin eruption including toxic epidermal necrolysis"],
        "note": "Most blindness cases resolved within 20 minutes; most patients had received chemotherapy including cisplatin" },
      { "frequency": "unknown", "soc": "Cardiac disorders",
        "terms": ["Myocardial ischaemia"], "postmarketing": true }
    ]
  },

  "warnings": [
    { "topic": "QT prolongation", "severity": "high",
      "text": "Dose-dependent QT prolongation; post-marketing torsades de pointes reported. Avoid in congenital long QT syndrome." },
    { "topic": "Myocardial ischaemia", "severity": "high",
      "text": "Reported predominantly during IV administration; exercise caution during and after administration" },
    { "topic": "Electrolyte abnormalities", "severity": "medium",
      "text": "Hypokalaemia and hypomagnesaemia must be corrected before administration" },
    { "topic": "Serotonin syndrome", "severity": "high",
      "text": "Described with concomitant serotonergic drugs; appropriate observation recommended" },
    { "topic": "ECG monitoring", "severity": "medium",
      "text": "Consider in electrolyte abnormality, congestive heart failure, bradyarrhythmia, or QT-prolonging co-medication" },
    { "topic": "Subacute intestinal obstruction", "severity": "medium",
      "text": "Ondansetron increases large bowel transit time; monitor patients with signs of subacute obstruction" },
    { "topic": "Cross-hypersensitivity", "severity": "medium",
      "text": "Reported in patients with prior reactions to other selective 5-HT3 antagonists" }
  ],

  "interactions": [
    { "substance": "Apomorphine", "type": "contraindicated",
      "effect": "Profound hypotension and loss of consciousness" },
    { "substance": "Phenytoin",     "type": "reduces_exposure", "effect": "CYP3A4 induction increases ondansetron clearance, reducing plasma concentrations" },
    { "substance": "Carbamazepine", "type": "reduces_exposure", "effect": "CYP3A4 induction increases ondansetron clearance" },
    { "substance": "Rifampicin",    "type": "reduces_exposure", "effect": "CYP3A4 induction increases ondansetron clearance" },
    { "substance": "SSRIs",  "type": "caution", "effect": "Serotonin syndrome" },
    { "substance": "SNRIs",  "type": "caution", "effect": "Serotonin syndrome" },
    { "substance": "Tramadol", "type": "caution", "effect": "Ondansetron may reduce the analgesic effect of tramadol" },
    { "substance": "QT-prolonging agents", "type": "caution", "effect": "Additive QT prolongation" },
    { "substance": "Alcohol",   "type": "no_interaction" },
    { "substance": "Temazepam", "type": "no_interaction" },
    { "substance": "Furosemide","type": "no_interaction" },
    { "substance": "Propofol",  "type": "no_interaction" }
  ],

  "pharmacokinetics": {
    "plasma_protein_binding_pct": [70, 76],
    "volume_of_distribution_l": 140,
    "half_life_hours": 3,
    "metabolism": "Predominantly hepatic via multiple CYP450 enzymes: CYP3A4, CYP2D6, CYP1A2",
    "cyp2d6_polymorphism_effect": "None — absence of CYP2D6 does not alter pharmacokinetics",
    "renal_excretion_unchanged_pct": "<5",
    "im_iv_equivalence": "Equivalent systemic exposure after IM and IV administration",
    "special_populations": {
      "sex": "Females show greater rate and extent of absorption, reduced systemic clearance and volume of distribution",
      "paediatric_1_to_4_months": { "half_life_hours": 6.7, "note": "Clearance ~30% lower than 5-24 months" },
      "paediatric_5_to_24_months": { "half_life_hours": 2.9 },
      "elderly": "Slight age-related decrease in clearance and increase in half-life; no dose change justified below 75 years",
      "renal_moderate": { "crcl_ml_min": [15, 60], "half_life_hours": 5.4, "note": "Clinically insignificant" },
      "renal_severe_dialysis": "Essentially unchanged after IV administration",
      "hepatic_severe": { "half_life_hours": [15, 32], "oral_bioavailability_pct": 100 }
    }
  },

  "pharmacodynamics": {
    "mechanism_of_action": "Potent, highly selective 5-HT3 receptor antagonist",
    "mechanism_detail": "Blocks 5-HT release from the small intestine that initiates the vomiting reflex via vagal afferents, and 5-HT release in the area postrema of the fourth ventricle",
    "prolactin_effect": "No alteration of plasma prolactin concentrations",
    "qt_study": {
      "design": "Crossover, double-blind, randomised, placebo- and moxifloxacin-controlled",
      "n": 58,
      "doses_mg": [8, 32],
      "infusion_minutes": 15,
      "qtcf_mean_difference_msec": { "32_mg": 19.6, "8_mg": 5.8 },
      "qtcf_upper_90ci_msec": { "32_mg": 21.5, "8_mg": 7.8 },
      "no_measurement_above_480_msec": true,
      "no_prolongation_above_60_msec": true
    }
  },

  "efficacy": {
    "summary": "Ondansetron controlled nausea and vomiting in 75% of patients treated with cisplatin chemotherapy",
    "reference": "Marty M et al. Comparison of the 5-hydroxytryptamine3 (serotonin) antagonist ondansetron (GR 38032F) with high-dose metoclopramide in the control of cisplatin-induced emesis. N Engl J Med 1990;322(12):816-21."
  },

  "pregnancy_lactation": {
    "risk_category_br": "B",
    "pregnancy_use": "Not recommended during pregnancy; must not be used by pregnant women without medical or dental guidance",
    "human_data": [
      { "design": "Cohort", "n_pregnancies": 88467, "finding": "Increased risk of orofacial clefts",
        "measure": "adjusted RR 1.24 (95% CI 1.03-1.48)", "absolute": "3 additional cases per 10,000 women treated",
        "cardiac_malformations": "No apparent increase" },
      { "design": "Cohort subgroup, IV exposure", "n_pregnancies": 23877,
        "finding": "No increased risk of oral clefts or cardiac malformations" },
      { "design": "Case-control, birth defect registries", "n_cases": 23200,
        "finding": "Increased risk of cleft palate in one dataset, none in the other" },
      { "design": "Cohort", "n_pregnancies": 3733, "finding": "Slightly increased risk of ventricular septal defect",
        "measure": "adjusted RR 1.7 (95% CI 1.0-2.9)", "significance": "Not statistically significant for cardiac malformations overall" }
    ],
    "animal_data": "No direct or indirect reproductive toxicity in rats (up to 15 mg/kg/day) or rabbits (up to 30 mg/kg/day)",
    "lactation": "Unknown whether transferred to human milk; excreted in the milk of lactating rats. Breast-feeding not recommended.",
    "contraception": "Effective contraception during treatment and for 2 days after discontinuation",
    "pregnancy_test_required": true,
    "fertility": "No effect of Ansentron on fertility"
  },

  "overdose": {
    "symptoms": ["Similar to those at recommended doses", "Dose-dependent QT prolongation",
                 "Serotonin syndrome in small children after oral overdose"],
    "management": "No specific antidote. Symptomatic and supportive therapy. ECG monitoring recommended. Ipecacuanha is not recommended — unlikely to be effective given the antiemetic action itself.",
    "poison_control": "0800 722 6001"
  },

  "storage": {
    "temperature": "15 °C to 30 °C (ambient)",
    "light_protection": true,
    "shelf_life_months": 24,
    "shelf_life_basis": "from date of manufacture",
    "appearance": "Clear, colourless liquid",
    "appearance_local": "Líquido límpido e incolor",
    "in_use": "Single use only; inject or dilute immediately after opening; discard any remaining solution; do not autoclave"
  },

  "legal": {
    "dispensing": "VENDA SOB PRESCRIÇÃO",
    "dispensing_en": "Prescription only",
    "restriction": "USO RESTRITO A ESTABELECIMENTOS DE SAÚDE",
    "restriction_en": "Restricted to healthcare establishments",
    "bula_regulation": "Resolução-RDC nº 47/2009",
    "bula_version": "VPS — Bula para Profissional da Saúde",
    "mah_cnpj": "60.659.463/0029-92",
    "origin": "Indústria Brasileira",
    "product_category_br": "Similar",
    "category_note": "Brazil distinguishes genérico, similar and referência. This is a 'similar' — equivalent to the reference product but not a substitutable generic in the Brazilian sense.",
    "pharmacovigilance": "Sistema VigiMed, Portal da Anvisa",
    "internal_doc_code": "Ansentron_BU08c_VPS_768"
  },

  "label_change_history": [
    { "submission_date": "2026-06-10", "subject": "10450 – SIMILAR – Notificação de Alteração de Texto de Bula",
      "sections_changed": ["I. Identificação do Medicamento", "5. Advertências e Precauções",
                           "7. Cuidados de Armazenamento", "III. Dizeres Legais"],
      "versions": ["VP", "VPS"] },
    { "submission_date": "2022-08-22", "expedient": "4583808/22-4",
      "petition_date": "2022-08-02", "petition_expedient": "4487827/22-3",
      "subject": "11012 - RDC 73/2016 – Inclusão de local de embalagem secundária",
      "sections_changed": ["III – Dizeres Legais"] },
    { "submission_date": "2021-12-28", "expedient": "8538680/21-8",
      "sections_changed": ["5. Advertências e Precauções", "9. Reações Adversas"] },
    { "submission_date": "2021-07-08", "expedient": "2654678/21-1", "sections_changed": ["Apresentações"] },
    { "submission_date": "2021-06-04", "expedient": "0462842/21-0", "sections_changed": ["9. Reações Adversas"] },
    { "submission_date": "2020-06-16", "expedient": "1909581/20-3", "sections_changed": ["5. Advertências e Precauções"] },
    { "submission_date": "2020-05-20", "expedient": "1586775/20-7", "sections_changed": ["5. Advertências e Precauções"] },
    { "submission_date": "2019-12-18", "expedient": "3495093/19-1", "sections_changed": ["5. Advertências e Precauções"] },
    { "submission_date": "2019-04-03", "expedient": "0302403/19-2",
      "petition_date": "2018-09-28", "petition_expedient": "0962452/18-0",
      "approval_date": "2018-12-24",
      "subject": "1995 – Solicitação de Transferência de Titularidade de Registro (Incorporação de Empresa)",
      "sections_changed": ["9. Reações Adversas", "III – Dizeres Legais"] },
    { "submission_date": "2018-11-23", "expedient": "0050084/19-4",
      "sections_changed": ["3. Características Farmacológicas", "5. Advertências e Precauções",
                           "8. Posologia e Modo de Usar", "10. Superdose"] },
    { "submission_date": "2015-12-11", "expedient": "1079058/15-6",
      "subject": "10756 – Notificação de alteração de texto de bula para adequação a intercambialidade",
      "sections_changed": ["Identificação do Medicamento"] },
    { "submission_date": "2015-04-02", "expedient": "0290166/15-8", "sections_changed": ["8 – Posologia e Modo de Usar"] },
    { "submission_date": "2014-11-11", "expedient": "1014846/14-9",
      "sections_changed": ["1. Indicações", "3. Características Farmacológicas",
                           "7. Cuidados de Armazenamento", "8. Posologia e Modo de Usar", "III – Dizeres Legais"] },
    { "submission_date": "2013-09-11", "expedient": "0766720/13-5",
      "subject": "10457 – Inclusão Inicial de Texto de Bula – RDC 60/12" }
  ],

  "raw_sections": {
    "1": "Este medicamento está indicado para uso em adultos e crianças a partir de 6 meses de idade para o controle de náuseas e vômitos induzidos por quimioterapia ou radioterapia. Também é indicado para prevenção e tratamento de náuseas e vômitos pós-operatórios, em adultos e crianças a partir de 1 mês de idade.",
    "4": "Ansentron é contraindicado a pacientes que apresentam hipersensibilidade conhecida a qualquer componente da fórmula. Tendo como base os relatos de hipotensão profunda e perda de consciência quando Ansentron foi administrado com cloridrato de apomorfina, o uso concomitante dessas substâncias é contraindicado."
  }
}
```

---

## One thing worth adding to the schema after this document

ANVISA publishes a **Histórico de Alterações da Bula** — a dated, per-section
label change log going back to 2013. Two pages of this 12-page PDF are that
table alone.

It is already captured above as `product_data.label_change_history`, and
`label_revision_date` is derived from its most recent row. That is enough for
now. But it is the only structured *diff* any of your four regulators publishes,
so if change tracking becomes a feature, this is where the history already
exists rather than having to be reconstructed from successive crawls.
