# SmPC Extraction Gap Analysis

**Source document:** `1786801271319_PREGABALIN_WOCKHARDT_225MG_HARD_CAPSULES.pdf`
**Type:** UK SmPC (Summary of Product Characteristics), 21 pages, 10 sections
**Product:** Pregabalin Wockhardt 225 mg Hard Capsules — PL 29831/0647

Everything below is **present in this PDF but has nowhere to go** in the current `products` table.

---

## 0. Structural problems with the current table

| Issue | Detail |
| ----- | ------ |
| `manufacturer` conflates two different entities | Section 7 gives the **Marketing Authorisation Holder** (Wockhardt UK Ltd, Ash Road North, Wrexham LL13 9UF). The MAH is not necessarily the manufacturer, nor the batch-release site. These are three separate roles and regulators list them separately. |
| `symptoms TEXT[]` is ambiguous | In the TGA record you used it for *disease* symptoms. This SmPC's richest data is ~150 **adverse reactions**. Two completely different concepts sharing one column will poison any search. |
| No strength column | "225 mg" is in the product name and section 2, but there is no queryable `strengths` field. Strength is the single most common filter in a predicate search. |
| No ATC code | Section 5.1: `N03AX16`. This is the canonical cross-country product-matching key and it is absent. |
| No document provenance | Section 10 "Date of revision of text: 15/01/2026" is the label version. Without it you cannot detect when a label changes, diff versions, or know if your row is stale. |
| No `source_document` linkage | No URL, doc type (SmPC vs PIL vs Label), language, hash, or fetch timestamp. |
| Missing uniqueness key | Nothing enforces one row per `(country, registration_number)`. |

---

## 1. High-value extractions (do these first)

### 1.1 ATC / pharmacological classification — Section 5.1
```
ATC code: N03AX16
Pharmacotherapeutic group: Anti-epileptics, other anti-epileptics
Chemical name: (S)-3-(aminomethyl)-5-methylhexanoic acid
Mechanism of action: binds to the α2-δ auxiliary subunit of voltage-gated
                     calcium channels in the CNS
```
→ `atc_code TEXT`, `atc_codes TEXT[]`, `drug_class TEXT[]`, `mechanism_of_action TEXT`, `molecular_target TEXT`

**Why:** ATC + MoA is how you match this product to its equivalents in Australia, India, Brazil. This is the core of a source-predicate database and it is currently unrepresented.

### 1.2 Adverse drug reactions — Section 4.8 (Table 2)
The single largest structured dataset in the document: a MedDRA System Organ Class × frequency matrix covering ~150 terms.

```
SOC: Nervous system disorders
  Very common: dizziness, somnolence, headache
  Common:      ataxia, coordination abnormal, tremor, dysarthria, amnesia,
               memory impairment, disturbance in attention, paraesthesia,
               hypoaesthesia, sedation, balance disorder, lethargy
  Uncommon:    syncope, stupor, myoclonus, loss of consciousness, ...
  Rare:        convulsions, parosmia, hypokinesia, dysgraphia, Parkinsonism
```
Frequency bands are defined in the text: very common ≥1/10; common ≥1/100–<1/10; uncommon ≥1/1,000–<1/100; rare ≥1/10,000–<1/1,000; very rare <1/10,000; not known.

Also present: 8,900 patients exposed (5,600 in double-blind placebo-controlled), discontinuation rate 12% pregabalin vs 5% placebo, most common reasons dizziness + somnolence. Post-marketing reactions flagged separately (italics in source).

→ **Own child table**: `product_adverse_reactions(product_id, soc, frequency_band, term, is_postmarketing, source_section)`

### 1.3 Contraindications — Section 4.3
```
Hypersensitivity to the active substance or to any of the excipients.
```
→ `contraindications TEXT[]` — currently no column at all. This is a safety-critical field.

### 1.4 Warnings & precautions — Section 4.4
~18 distinct named warnings, several of which are severity-tier events:

- Suicidal ideation and behaviour (with epidemiological evidence of increased risk)
- Respiratory depression (severe; risk factors: compromised respiratory function, renal impairment, CNS depressants, elderly)
- Drug dependence, tolerance and abuse potential; drug withdrawal syndrome
- Severe cutaneous adverse reactions — SJS and TEN, life-threatening/fatal
- Hypersensitivity / angioedema
- Encephalopathy
- Congestive heart failure
- Concomitant opioid use → opioid-related death, **aOR 1.68 (95% CI 1.19–2.36)**; ≤300 mg aOR 1.52 (1.04–2.22); >300 mg aOR 2.51 (1.24–5.06)
- Dizziness, somnolence, loss of consciousness, confusion, mental impairment
- Vision-related effects (blurred vision, visual field changes, vision loss)
- Renal failure
- Reduced lower GI tract function (obstruction, paralytic ileus, constipation)
- Central neuropathic pain from spinal cord injury — increased CNS ADRs
- Diabetic patients — may need hypoglycaemic dose adjustment
- Withdrawal of concomitant anti-epileptics — insufficient data
- Women of childbearing potential — contraception required
- Lactose intolerance (excipient warning)
- Neonatal withdrawal syndrome if taken during pregnancy

→ `warnings JSONB[]` with `{topic, severity_tier, text}`, plus boolean flags worth promoting: `has_dependence_risk`, `has_suicidality_warning`, `has_scar_warning`, `requires_contraception`

### 1.5 Dosing / posology — Section 4.2
Completely absent from the schema. Highly structurable:

```
dose_range:        150–600 mg/day
frequency:         2 or 3 divided doses (BID or TID)
max_daily_dose:    600 mg
route:             oral only
food_effect:       may be taken with or without food
discontinuation:   taper gradually over minimum 1 week
```

Per-indication titration:
| Indication | Start | Step 1 | Step 2 | Max |
| --- | --- | --- | --- | --- |
| Neuropathic pain | 150 mg/day | 300 mg after 3–7 days | — | 600 mg after +7 days |
| Epilepsy | 150 mg/day | 300 mg after 1 wk | — | 600 mg after +1 wk |
| GAD | 150 mg/day | 300 mg after 1 wk | 450 mg after +1 wk | 600 mg after +1 wk |

Renal dose adjustment (Table 1) — a real lookup table:
| CrCl (mL/min) | Starting (mg/day) | Max (mg/day) | Regimen |
| --- | --- | --- | --- |
| ≥ 60 | 150 | 600 | BID or TID |
| ≥ 30 – <60 | 75 | 300 | BID or TID |
| ≥ 15 – <30 | 25–50 | 150 | Once daily or BID |
| < 15 | 25 | 75 | Once daily |
| Post-haemodialysis supplement | 25 | 100 | Single dose |

Plus the CrCl formula itself, and special populations: hepatic impairment = no adjustment; paediatric <12 y and adolescents 12–17 y = not established; elderly >65 = may need reduction.

→ `dosing JSONB` + child table `product_dose_adjustments(product_id, population, criterion, criterion_value, starting_dose, max_dose, regimen)`

### 1.6 Indications with qualifiers — Section 4.1
Your `indications TEXT[]` flattens away the qualifiers that actually matter:

| Indication | Population | Therapy line |
| --- | --- | --- |
| Peripheral and central neuropathic pain | Adults | Monotherapy |
| Partial seizures ± secondary generalisation | Adults | **Adjunctive therapy** |
| Generalised Anxiety Disorder (GAD) | Adults | Monotherapy |

→ `indications JSONB[]` with `{condition, population, age_min, age_max, therapy_line, severity}` — mirroring what you already did in the TGA `product_data`, but consistently and as a first-class field.

---

## 2. Pharmacokinetics — Section 5.2 (critical for a predicate database)

Nothing in the current schema. For bioequivalence / predicate matching this is the highest-signal block in the document.

| Parameter | Value |
| --- | --- |
| Oral bioavailability | ≥ 90%, dose-independent |
| Tmax (fasted) | ~1 hour |
| Tmax (with food) | ~2.5 hours |
| Cmax with food | ↓ 25–30% |
| Time to steady state | 24–48 hours |
| Volume of distribution | 0.56 L/kg |
| Plasma protein binding | None (not bound) |
| Metabolism | Negligible; 98% recovered unchanged in urine |
| Major metabolite | N-methylated derivative, 0.9% of dose |
| Elimination half-life | 6.3 hours |
| Elimination route | Renal, unchanged drug |
| Dialyzability | ~50% removed in 4 h haemodialysis |
| Linearity | Linear over recommended range |
| Inter-subject variability | < 20% |
| Gender effect | None clinically significant |
| Paediatric t½ | 3–4 h (≤6 y), 4–6 h (≥7 y); AUC 30% lower if <30 kg |
| Breast milk | 76% of maternal plasma; infant dose ~7% of maternal mg/kg |

→ `pharmacokinetics JSONB` — or a dedicated `product_pk_parameters(product_id, parameter, value, unit, condition)` table if you intend to query on it.

---

## 3. Pregnancy, lactation, fertility — Section 4.6

| Field | Value |
| --- | --- |
| Pregnancy use | Not to be used unless clearly necessary |
| MCM prevalence | 5.9% exposed vs 4.1% unexposed (Nordic study, >2,700 pregnancies) |
| Adjusted prevalence ratio | 1.14 (0.96–1.35) vs unexposed; 1.29 (1.01–1.65) vs lamotrigine; 1.39 (1.07–1.82) vs duloxetine |
| Malformation types | Nervous system, eye, orofacial clefts, urinary, genital |
| Placental transfer | Crosses in rats; may cross human placenta |
| Breast-feeding | Excreted in human milk; effect on newborn unknown |
| Contraception | Required in women of childbearing potential |
| Fertility (human) | No effect on sperm motility at 600 mg/day × 3 months |
| Fertility (animal) | Adverse reproductive effects in male and female rats |

→ `pregnancy_lactation JSONB`, plus a promoted `contraception_required BOOLEAN` and `pregnancy_risk_category TEXT`.

---

## 4. Interactions — Section 4.5

Both directions are valuable — "no interaction" is as useful as "interaction":

**Potentiates / caution:** ethanol, lorazepam, opioids (respiratory failure, coma, death reported), other CNS depressants, oxycodone (additive cognitive/motor impairment)

**No clinically relevant interaction:** phenytoin, carbamazepine, valproic acid, lamotrigine, gabapentin, lorazepam*, oxycodone*, ethanol* (PK level), oral antidiabetics, diuretics, insulin, phenobarbital, tiagabine, topiramate, norethisterone, ethinyl oestradiol

Basis: no CYP inhibition in vitro, no plasma protein binding, <2% metabolised.

→ Child table `product_interactions(product_id, interacting_substance, interaction_type, direction, effect, severity)`

---

## 5. Overdose — Section 4.9

```
Symptoms:   somnolence, confusional state, agitation, restlessness,
            seizures, coma (rare)
Management: general supportive measures; haemodialysis if necessary
```
→ `overdose JSONB {symptoms[], management}` — no column exists.

---

## 6. Efficacy / clinical trial evidence — Section 5.1

Currently discarded entirely. Useful if you ever rank products by evidence strength.

- Neuropathic pain: 10 controlled trials up to 13 wk; peripheral — 35% pregabalin vs 18% placebo achieved 50% pain reduction; central — 22% vs 7%
- Epilepsy: 3 controlled 12-wk trials, adjunctive; seizure reduction by Week 1
- Monotherapy: 1 trial, 56 wk — **failed non-inferiority vs lamotrigine**
- GAD: 6 controlled trials 4–6 wk + 8-wk elderly study + 6-month relapse prevention; 52% vs 38% achieved 50% HAM-A improvement
- Paediatric: 5 studies (n=295, 175, 65, 54, 431); 10 mg/kg/day 40.6% vs placebo 22.6% (p=0.0068); PGTC study n=219 showed **no benefit** over placebo
- Ophthalmologic testing in >3,600 patients: visual acuity reduced 6.5% vs 4.8%; visual field changes 12.4% vs 11.7%

→ `clinical_evidence JSONB[]` with `{indication, trial_count, duration, n, endpoint, result_active, result_placebo, p_value, outcome}`

---

## 7. Preclinical safety — Section 5.3

- Not teratogenic in mice, rats, rabbits; offspring developmental toxicity at >2× human exposure
- Not genotoxic (in vitro and in vivo battery)
- Carcinogenicity: no tumours in rats up to 24× exposure; **haemangiosarcoma in mice** at higher exposures — mechanism non-genotoxic (platelet changes), judged not human-relevant
- Retinal atrophy in aged albino rats at ≥5× exposure
- Juvenile rats: CNS hyperactivity, bruxism, growth suppression; oestrus cycle effects at 5×

→ `preclinical_safety JSONB` — lower priority, but cheap to store.

---

## 8. Pharmaceutical particulars — Section 6

### 8.1 Excipients, segmented by component (6.1)
Your TGA record stored a flat excipient list. This SmPC segments them, and that segmentation carries real filtering value:

| Component | Excipients |
| --- | --- |
| Capsule content | Lactose monohydrate, Maize starch, Talc (E553b) |
| Capsule shell | **Gelatin**, Titanium dioxide (E171), Red iron oxide (E172), Yellow iron oxide (E172) |
| Printing ink | Shellac, Black iron oxide (E172), Propylene glycol, Potassium hydroxide |

→ `excipients JSONB[]` with `{name, e_number, component}`

**Derived flags worth promoting to columns:** `contains_gelatin BOOLEAN` (animal-derived — halal/kosher/vegan filtering is a genuine commercial query), `contains_lactose BOOLEAN`, `e_numbers TEXT[]`.

### 8.2 Excipients with known effect (Section 2)
```
Lactose monohydrate 24.75 mg per capsule
```
Distinct from the full list — this is the regulator-flagged allergen/intolerance subset, with a quantity. Section 4.4 adds: patients with galactose intolerance, total lactase deficiency, or glucose-galactose malabsorption should not take this medicine.

→ `excipients_with_known_effect JSONB[]` `{substance, quantity, unit, warning}`

### 8.3 Physical appearance (Section 3)
```
Form:       Hard capsule, size 1
Cap:        Pinkish-orange
Body:       White
Imprint:    "225" printed in black ink on body
Contents:   White to off-white powder
```
→ `appearance JSONB {capsule_size, cap_colour, body_colour, imprint, print_colour, fill_description}` — enables pill-identification lookup.

### 8.4 Packaging (6.5)
```
Container: PVC/Aluminium blister
Pack sizes: 10, 14, 20, 21, 28, 30, 56, 60, 84, 90, 100
Multipacks: 84 (2×42), 112 (2×56), 120 (2×60), 200 (2×100)
Caveat: "Not all pack sizes may be marketed"
```
→ Child table `product_packaging(product_id, container_material, container_type, pack_size, is_multipack, multipack_composition, marketed_status_known)`

The "not all pack sizes may be marketed" caveat matters — do not treat listed pack sizes as available SKUs.

### 8.5 Shelf life, storage, incompatibilities, disposal (6.2–6.6)
| Field | Value |
| --- | --- |
| Shelf life | 3 years |
| Storage | No special storage conditions required |
| Incompatibilities | Not applicable |
| Disposal | No special requirements |

→ `shelf_life_months INT`, `storage_conditions TEXT`, `incompatibilities TEXT`, `disposal_requirements TEXT`

Note the contrast with your TGA record ("Store below 25°C") — storage conditions vary by product and country and belong in a real column.

---

## 9. Regulatory / administrative — Sections 7–10

| Field | Value in this doc | Current schema |
| --- | --- | --- |
| MAH name | Wockhardt UK Ltd | conflated into `manufacturer` |
| MAH address | Ash Road North, Wrexham LL13 9UF | **missing** |
| MAH country | United Kingdom | **missing** |
| MA number | PL 29831/0647 | `registration_number` ✓ |
| MA number scheme | UK PL (licence-holder code 29831 + product 0647) | **missing** |
| Date of first authorisation | 31/08/2021 | maps to `approval_date` ✓ |
| Date of renewal | (same field in SmPC, not populated here) | **missing** as distinct field |
| **Date of revision of text** | **15/01/2026** | **missing — critical** |
| Legal status of supply | Prescription-only (implied) | partly `product_type` |
| Pharmacovigilance channel | MHRA Yellow Card, www.mhra.gov.uk/yellowcard | **missing** |
| Additional monitoring (black triangle) | Not present on this product | **missing** as a field |

**`label_revision_date` is the one I'd add today.** Without it you cannot tell a re-crawled row from a genuinely updated label, and change detection across 21 pages of free text is otherwise impossible.

---

## 10. Fields the document implies but does not state — worth designing for now

These are not in this PDF but are in the wider MHRA/EMA record set, and adding the columns later is more expensive than adding them now:

- `manufacturing_sites JSONB[]` — actual production and batch-release sites (in the PIL / MA dossier, not the SmPC)
- `controlled_drug_schedule TEXT` — pregabalin is a **Schedule 3 Controlled Drug in the UK**; not stated in this SmPC but essential for a UK product record
- `legal_status TEXT` — POM / P / GSL
- `is_generic BOOLEAN` + `reference_product TEXT` — Pregabalin Wockhardt is a generic; the originator is Lyrica (Pfizer). **For a source-predicate database this is arguably the most important missing relationship in the entire schema.**
- `orphan_designation BOOLEAN`, `paediatric_investigation_plan TEXT`
- `parallel_import BOOLEAN`

---

## 11. Recommended schema changes

### Promote to columns on `products`
```sql
ALTER TABLE products
  ADD COLUMN atc_code                TEXT,
  ADD COLUMN atc_codes               TEXT[],
  ADD COLUMN drug_class              TEXT[],
  ADD COLUMN mechanism_of_action     TEXT,
  ADD COLUMN strengths               TEXT[],
  ADD COLUMN contraindications       TEXT[],
  ADD COLUMN excipients              TEXT[],
  ADD COLUMN e_numbers               TEXT[],
  ADD COLUMN adverse_reactions       TEXT[],   -- flat, for search; detail in child table
  ADD COLUMN interacting_substances  TEXT[],

  ADD COLUMN mah_name                TEXT,
  ADD COLUMN mah_address             TEXT,
  ADD COLUMN mah_country             TEXT,

  ADD COLUMN legal_status            TEXT,
  ADD COLUMN controlled_drug_schedule TEXT,
  ADD COLUMN prescription_only       BOOLEAN,
  ADD COLUMN additional_monitoring   BOOLEAN,

  ADD COLUMN is_generic              BOOLEAN,
  ADD COLUMN reference_product       TEXT,

  ADD COLUMN max_daily_dose_mg       NUMERIC,
  ADD COLUMN shelf_life_months       INT,
  ADD COLUMN storage_conditions      TEXT,

  ADD COLUMN contains_lactose        BOOLEAN,
  ADD COLUMN contains_gelatin        BOOLEAN,

  ADD COLUMN label_revision_date     DATE,
  ADD COLUMN source_document_url     TEXT,
  ADD COLUMN source_document_type    TEXT,   -- SmPC | PIL | Label | ARTG
  ADD COLUMN source_document_hash    TEXT,
  ADD COLUMN source_language         TEXT,
  ADD COLUMN extracted_at            TIMESTAMP;

ALTER TABLE products
  ADD CONSTRAINT products_country_regnum_uniq UNIQUE (country, registration_number);
```

### Rename for clarity
`symptoms` currently means two different things across your two records. Split it:
```sql
ALTER TABLE products RENAME COLUMN symptoms TO indication_symptoms;
```
…and keep adverse reactions in their own structure.

### New child tables
```sql
CREATE TABLE product_adverse_reactions (
    product_id       UUID REFERENCES products(id),
    system_organ_class TEXT,
    frequency_band   TEXT,   -- very_common | common | uncommon | rare | very_rare | not_known
    term             TEXT,
    is_postmarketing BOOLEAN,
    source_section   TEXT
);

CREATE TABLE product_dose_adjustments (
    product_id      UUID REFERENCES products(id),
    population      TEXT,    -- renal | hepatic | paediatric | elderly | haemodialysis
    criterion       TEXT,    -- e.g. 'creatinine_clearance_ml_min'
    criterion_min   NUMERIC,
    criterion_max   NUMERIC,
    starting_dose   TEXT,
    max_dose        TEXT,
    regimen         TEXT
);

CREATE TABLE product_interactions (
    product_id            UUID REFERENCES products(id),
    interacting_substance TEXT,
    interaction_type      TEXT,  -- potentiates | no_interaction | contraindicated
    effect                TEXT,
    severity              TEXT
);

CREATE TABLE product_packaging (
    product_id             UUID REFERENCES products(id),
    container_material     TEXT,   -- PVC/Aluminium
    container_type         TEXT,   -- blister
    pack_size              INT,
    is_multipack           BOOLEAN,
    multipack_composition  TEXT    -- '2 x 56'
);
```

### Keep in `product_data JSONB`
Narrative blocks that are worth storing but not worth normalising: full warning texts, preclinical safety, clinical trial narratives, posology prose, overdose management, pregnancy narrative, PK detail.

---

## 12. Priority order

| Priority | Items | Rationale |
| --- | --- | --- |
| **P0** | `atc_code`, `strengths`, `label_revision_date`, MAH split from manufacturer, `contraindications`, unique constraint, source provenance | Cross-country matching, change detection, and data integrity — everything else depends on these |
| **P1** | Adverse reactions table, interactions table, dosing + renal adjustment, `is_generic` / `reference_product` | The actual clinical payload, and the generic↔originator link a predicate database exists to serve |
| **P2** | Pharmacokinetics, excipients with E-numbers + derived flags, packaging table, appearance, pregnancy/lactation | Bioequivalence work, sourcing filters, pill identification |
| **P3** | Clinical trial evidence, preclinical safety, overdose, driving, disposal | Nice to have; store as JSONB rather than normalising |

---

## 13. One caution

This SmPC is a **UK MHRA** document. The section numbering (1–10) is the EU/UK harmonised SmPC format and is stable across every EU/UK product — so a section-aware parser will generalise well across that whole corpus.

It will **not** generalise to TGA (Australia), ANVISA (Brazil), NMPA (China), or SAHPRA (South Africa), which use different document structures. Design the extraction layer as `country → parser` with a common output schema, rather than assuming this section layout everywhere.
