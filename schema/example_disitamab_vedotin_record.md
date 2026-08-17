# Worked example — 注射用维迪西妥单抗 / Disitamab Vedotin for Injection (NMPA, China)

**Source:** `1786801251440_CXSS2101011_注射用维迪西妥单抗_报告.pdf` — 20 pages
**Type:** 申请上市技术审评报告 — Technical Review Report for Marketing Application
**Issued by:** 国家药品监督管理局药品审评中心 — Center for Drug Evaluation (CDE), NMPA
**Report date:** 2022年6月 / June 2022
**Application no.:** CXSS2101011
**Approval no.:** 国药准字S20210017

---

## Read this first: it is not a label

The previous two examples were product labels (SmPC, bula). This is a
**regulatory assessment report**. Section 三(八) 说明书审核 says only
*"详见所附说明书"* — "see the attached label" — meaning the label is a separate
document that is **not in this PDF**.

So the columns this document can fill are almost the inverse of a label's:

| Fills well | Cannot fill at all |
| ---------- | ------------------ |
| Approval pathway, dates, review milestones | Contraindications |
| MAH / manufacturer identity | Excipients, packaging, storage, shelf life |
| Indication (exact approved wording) | Interactions |
| Dosing regimen | Labelled ADR frequency bands |
| Clinical trial evidence in depth | Pregnancy/lactation labelling |
| Risk management plan, post-market commitments | Physical appearance |

Two sections explicitly point elsewhere: 药理毒理评价 (pharmacology/toxicology)
and 临床药理学评价 (clinical pharmacology) both say *"参见注射用维迪西妥单抗
（CXSS2000044）公开审评报告"* — see the earlier review report for application
CXSS2000044. **There is no PK data in this document**, unlike the other two
examples.

---

## The row in `source.products`

| Column | Value | 原文 / Source |
| ------ | ----- | ------------- |
| id | `f2e8…` | generated |
| **product_name** | `Disitamab Vedotin for Injection` | 英文名 (given in the document) |
| **product_name_local** *(if added)* | `注射用维迪西妥单抗` | 通用名 |
| **brand_name** | `Aidixi` | transliteration of 商品名 |
| **brand_name_local** | `爱地希` | 商品名（中/英）— appears **only** in the RMP annex, p.18 |
| **generic_name** | `Disitamab Vedotin` | 活性成分（中/英）: 维迪西妥单抗 / disitamab vedotin |
| **country_id** | `→ CN` | 国家药品监督管理局 |
| regulator | `NMPA` | issued by CDE, the evaluation centre of NMPA |
| **mah_name** | `RemeGen Biopharmaceutical (Yantai) Co., Ltd.` | 上市许可持有人: 荣昌生物制药（烟台）股份有限公司 |
| **mah_address** | `No. 58 Beijing Middle Road, Yantai Development Zone, Yantai Area, China (Shandong) Pilot Free Trade Zone` | 中国(山东)自由贸易试验区烟台片区烟台开发区北京中路58号 |
| **manufacturer** | `RemeGen Biopharmaceutical (Yantai) Co., Ltd.` | 生产企业 — same entity |
| **manufacturer_address** | *(same as MAH)* | 同上 |
| **registration_number** | `国药准字S20210017` | 批准文号 |
| **product_type** | `Therapeutic Biological Product, Class 2.2` | 受理的注册分类: 治疗用生物制品 2.2 类 |
| status | `Active` | 批准文号 + 批准日期 present |
| **approval_date** | `2021-12-31` | 批准日期 |
| registration_date | `2021-12-31` | same event under NMPA |
| market_authorization_date | `2021-12-31` | same |
| expiry_date / withdrawal_date | *NULL* | not applicable |
| label_revision_date | *NULL* | **not a label** — the June 2022 date is the report date |
| **atc_code** | *NULL* | **not in the document; do not guess** — see note below |
| therapeutic_areas | `{Oncology, Urology}` | derived from indication |
| **indications** | `{HER2-overexpressing locally advanced or metastatic urothelial carcinoma, previously treated with platinum-containing chemotherapy}` | 适应症 |
| symptoms | `{}` | oncology indication — no symptom list in this document |
| **adverse_reactions** | `{Weight decreased, White blood cell count decreased, Hypoaesthesia, AST increased, Asthenia, Neutrophil count decreased, Alopecia, Hypertriglyceridaemia, ALT increased, Decreased appetite, Gamma-glutamyltransferase increased, Blood glucose increased, Constipation, Blood lactate dehydrogenase increased, Nausea, Anaemia, Pyrexia, Platelet count decreased, Haemoglobin decreased, Vomiting, Apolipoprotein B increased, Insomnia, Blood uric acid increased, Proteinuria, Hypertension, Peripheral sensory neuropathy, Sinus tachycardia, Palpitations}` | 安全性评价 — **trial TEAEs, not label ADRs**; see warning below |
| contraindications | `{}` | **not in this document** |
| **active_ingredients** | `{Disitamab vedotin}` | 活性成分 |
| **strengths** | `{60 mg}` | 剂型及规格: 60 mg/支 (per vial) |
| **dosage_forms** | `{Injection}` | 剂型: 注射剂 |
| **routes** | `{Intravenous}` | 静脉滴注 |
| **is_generic** | `false` | 首个国产靶向 Her2 的 ADC 产品 — first domestic HER2-targeting ADC |
| reference_product | *NULL* | innovator product |
| source_url | *(NMPA/CDE portal URL)* | — |
| **source_language** | `zh` | — |
| is_active | `true` | |
| **processing_status** | `NEEDS_REVIEW` | see below |

---

## Five things this document forces you to handle

### 1. `source_document_type` is not cosmetic — it decides which columns can be filled

A review report and a label are both "the product", but they populate disjoint
column sets. If you write this row and mark it `ENRICHED`, downstream consumers
will read `contraindications = {}` as *"this drug has no contraindications"*
rather than *"this document type does not carry them."*

Two options, and I'd take the second:

- Add `source_document_type` to `products` (it was in the first draft, dropped
  in the simplification), or
- Keep it in `product_data.source.document_type` and set
  `processing_status = 'NEEDS_REVIEW'` until the 说明书 (label) is also parsed.

I set `NEEDS_REVIEW` above for exactly this reason.

### 2. This is an indication **addition**, not a new product — so it is an UPDATE

申报情况: ☑ **增加新适应症** ("adding a new indication"), not ☐ 首次申请上市.

`国药准字S20210017` was **already approved in June 2021** for gastric cancer:

> 注射用维迪西妥单抗是首个国产靶向 Her2 的 ADC 产品，2021 年 6 月附条件批准上市，
> 用于至少接受过 2 个系统化疗的 HER2 过表达局部晚期或转移性胃癌（包括胃食管结合部腺癌）的患者

*"…the first domestic HER2-targeting ADC product, conditionally approved in
June 2021 for patients with HER2-overexpressing locally advanced or metastatic
gastric cancer (including gastro-oesophageal junction adenocarcinoma) who had
received at least 2 prior systemic chemotherapies."*

Since `(country_id, registration_number)` is the upsert key, this document must
**append to `indications`** on the existing row, not create a second one. A
naive `INSERT` fails the unique constraint; a naive `UPDATE` that replaces
`indications` silently **deletes the gastric cancer indication**.

This is the sharpest correctness risk in the whole China pipeline. The indication
array needs union-merge semantics, with each indication carrying its own approval
date in `product_data`.

### 3. Trial TEAEs are not labelled ADR frequencies — do not mix them

The Pregabalin SmPC gave *labelled* frequency bands (very common ≥1/10, etc.)
derived from the whole clinical programme and agreed with the regulator. This
document gives raw **TEAE incidence in 111 trial patients** — a different thing
with a different denominator and no regulatory agreement behind it.

Both can populate `adverse_reactions` as flat terms, but the detail in
`product_data` must record which it is. Comparing "54.1% weight decreased" from
a review report against "very common" from an SmPC as though they were the same
measurement would be wrong.

### 4. `不适用` ("not applicable") ≠ missing

```
化学名:      不适用    Chemical name:      Not applicable
化学结构:    不适用    Chemical structure: Not applicable
分子式/分子量: 不适用    Formula / MW:       Not applicable
```

These are `不适用` because it is a **biologic** (an antibody-drug conjugate), not
because the data is absent. Store the distinction — a parser that maps both
"missing" and "not applicable" to NULL loses the fact that this is a large
molecule.

### 5. `atc_code` — I left it NULL deliberately

Disitamab vedotin is a HER2-targeted ADC, so it belongs in the WHO ATC **L01FD**
group (HER2 inhibitors) alongside trastuzumab emtansine (L01FD03) and
trastuzumab deruxtecan (L01FD04). But I have not verified the specific code
assigned to disitamab vedotin, and this document does not carry one.

Guessing `L01FD06` would put an unverified value in your primary cross-country
match key. Leave it NULL and let the enrichment step resolve it against a real
ATC index.

---

## Full extraction inventory

> **Not the storage spec.** This is everything the document contains, kept as a
> reference for building the parser. What actually gets stored in `product_data`
> is the six-key predicate subset in [product_data_spec.md](product_data_spec.md).

```json
{
  "_schema_version": "1",

  "source": {
    "document_type": "regulatory_review_report",
    "document_type_local": "申请上市技术审评报告",
    "issuer": "Center for Drug Evaluation (CDE), NMPA",
    "issuer_local": "国家药品监督管理局药品审评中心",
    "report_date": "2022-06",
    "application_number": "CXSS2101011",
    "is_label": false,
    "label_reference": "说明书审核：详见所附说明书 — label reviewed separately, not included in this document",
    "cross_references": [
      { "application_number": "CXSS2000044",
        "for": ["pharmacology_toxicology", "clinical_pharmacology"],
        "note": "药理毒理评价 and 临床药理学评价 both defer to this earlier public review report" }
    ]
  },

  "product_identity": {
    "generic_name_local": "注射用维迪西妥单抗",
    "generic_name_en": "Disitamab Vedotin For Injection",
    "brand_name_local": "爱地希",
    "brand_name_en": "Aidixi",
    "active_substance_local": "维迪西妥单抗",
    "active_substance_en": "disitamab vedotin",
    "modality": "Antibody-drug conjugate (ADC)",
    "target": "HER2",
    "payload": "MMAE (monomethyl auristatin E)",
    "payload_source": "重组人源化抗 HER2 单抗-MMAE 偶联剂 — recombinant humanised anti-HER2 mAb-MMAE conjugate",
    "chemical_name": "不适用 / Not applicable",
    "chemical_structure": "不适用 / Not applicable",
    "molecular_formula": "不适用 / Not applicable",
    "not_applicable_reason": "Biologic — large molecule, no small-molecule chemical descriptors",
    "structural_classification_local": "已上市产品增加适应症",
    "structural_classification_en": "Marketed product, indication addition",
    "first_of_kind": [
      "首个国产靶向 Her2 的 ADC 产品 — first domestically developed HER2-targeting ADC",
      "全球首个申报注册用于 Her2 过表达尿路上皮癌的 ADC 产品 — first ADC globally filed for HER2-overexpressing urothelial carcinoma"
    ]
  },

  "presentations": [
    {
      "strength": "60 mg",
      "strength_value": 60,
      "strength_unit": "mg",
      "per_unit": "vial",
      "per_unit_local": "支",
      "dosage_form_local": "注射剂",
      "dosage_form_en": "Injection",
      "route": "Intravenous infusion",
      "route_local": "静脉滴注"
    }
  ],

  "regulatory_pathway": {
    "application_number": "CXSS2101011",
    "approval_number": "国药准字S20210017",
    "approval_date": "2021-12-31",
    "acceptance_date": "2021-07-13",
    "registration_class_local": "治疗用生物制品 2.2 类",
    "registration_class_en": "Therapeutic Biological Product, Class 2.2",
    "application_type_local": "增加新适应症",
    "application_type_en": "Addition of new indication",
    "is_new_marketing_application": false,

    "conditional_approval": true,
    "conditional_approval_local": "附条件批准",
    "conditional_approval_basis": "Single-arm trial objective response rate. Full approval depends on whether the ongoing confirmatory trial demonstrates clinical benefit in this population.",
    "conditional_approval_basis_local": "上述适应症是基于单臂临床试验的客观缓解率结果给予的附条件批准。上述适应症的完全批准将取决于正在开展中的确证性临床试验能否证实本品在上述人群的临床获益。",

    "priority_review": true,
    "priority_review_local": "优先审评审批",
    "priority_review_grounds": [
      "（四）纳入突破性治疗药物程序的药品 — included in the breakthrough therapy programme",
      "（五）符合附条件批准的药品 — eligible for conditional approval"
    ],

    "milestones": [
      { "date": "2020-12-25", "event": "纳入突破性治疗药物程序", "event_en": "Granted breakthrough therapy designation" },
      { "date": "2020-12-31", "event": "递交 pre-NDA 沟通交流申请", "event_en": "Pre-NDA communication request submitted" },
      { "date": "2021-04-23", "event": "递交 pre-NDA 沟通交流申请", "event_en": "Second pre-NDA communication request submitted" },
      { "date": "2021-07-13", "event": "受理", "event_en": "Application accepted" },
      { "date": "2021-09-09", "event": "列为优先审评审批品种", "event_en": "Designated for priority review" },
      { "date": "2021-10-29", "event": "递交沟通交流申请，讨论确证性临床研究方案", "event_en": "Communication request on confirmatory trial design" },
      { "date": "2021-11-10", "event": "临床专业审评会", "event_en": "Clinical review meeting" },
      { "date": "2021-12-31", "event": "批准", "event_en": "Approved" }
    ],

    "prior_approvals": [
      {
        "date": "2021-06",
        "indication_en": "HER2-overexpressing locally advanced or metastatic gastric cancer (including gastro-oesophageal junction adenocarcinoma) in patients who have received at least 2 prior systemic chemotherapies",
        "indication_local": "至少接受过 2 个系统化疗的 HER2 过表达局部晚期或转移性胃癌（包括胃食管结合部腺癌）",
        "conditional": true,
        "note": "This row already existed before the current document. The UC indication is ADDED, not replacing."
      }
    ]
  },

  "indications_detail": [
    {
      "condition_en": "Urothelial carcinoma",
      "condition_local": "尿路上皮癌",
      "qualifier_en": "HER2-overexpressing, locally advanced or metastatic, previously treated with platinum-containing chemotherapy",
      "qualifier_local": "既往接受过含铂化疗且 HER2 过表达局部晚期或转移性尿路上皮癌",
      "biomarker": { "marker": "HER2", "method": "Immunohistochemistry (IHC)",
                     "criterion": "2+ or 3+",
                     "criterion_local": "HER2 免疫组织化学检查结果为 2+或 3+" },
      "approval_date": "2021-12-31",
      "conditional": true,
      "anatomical_scope_en": "Bladder, ureter, renal pelvis and urethra",
      "anatomical_scope_local": "膀胱、输尿管、肾盂及尿道来源"
    }
  ],

  "dosing": {
    "regimen_local": "尿路上皮癌患者：2.0 mg/kg，每两周一次，静脉滴注，直至疾病进展或出现不可耐受的毒性。",
    "regimen_en": "Urothelial carcinoma: 2.0 mg/kg by intravenous infusion once every 2 weeks, until disease progression or unacceptable toxicity.",
    "dose_value": 2.0,
    "dose_unit": "mg/kg",
    "frequency": "Every 2 weeks",
    "route": "Intravenous infusion",
    "treatment_duration": "Until disease progression or unacceptable toxicity",
    "dose_modification_note_en": "The C009 protocol tightened neurotoxicity dose reduction relative to C005: C005 required reduction at Grade 3 neurotoxicity, C009 at Grade 2. Earlier reduction improved tolerability and reduced discontinuations for neurotoxicity."
  },

  "clinical_evidence": {
    "supporting_studies": ["RC48-C005", "RC48-C009"],
    "safety_pool_studies": ["RC48-C002", "RC48-C005", "RC48-C009"],

    "studies": [
      {
        "study_id": "RC48-C005",
        "design_local": "开放性、多中心、单臂 II 期临床试验",
        "design_en": "Open-label, multicentre, single-arm Phase II",
        "population_en": "HER2-overexpressing (IHC 2+ or 3+) locally advanced or metastatic urothelial carcinoma after failure of at least first-line systemic chemotherapy; ECOG 0-2; no prior ADC of the same class",
        "n": 43,
        "baseline": {
          "male_pct": 76.7, "median_age": 64.0, "age_range": [45, 75],
          "lung_metastasis_pct": 51.2, "liver_metastasis_pct": 46.5,
          "liver_and_lung_metastasis_pct": 30.2,
          "prior_platinum_pct": 95.3, "prior_2plus_lines_pct": 32.6,
          "prior_pd1_pdl1_pct": 18.6,
          "ecog_1_n": 28, "ecog_1_pct": 65.1,
          "her2_ihc_2plus": { "n": 26, "pct": 60.5 },
          "her2_ihc_3plus": { "n": 17, "pct": 39.5 }
        },
        "results_irc_recist_v11_itt": {
          "orr_pct": 51.2, "orr_ci95": [35.5, 66.7],
          "best_response": { "cr_n": 0, "cr_pct": 0.0, "pr_n": 22, "pr_pct": 51.2,
                             "sd_n": 17, "sd_pct": 39.5, "pd_n": 3, "pd_pct": 7.0,
                             "ne_n": 1, "ne_pct": 2.3 },
          "dcr_pct": 90.7, "dcr_ci95": [77.9, 97.4],
          "median_dor_months": 7.0, "median_dor_ci95": [4.7, 12.4],
          "pfs": { "events_n": 33, "events_pct": 76.7,
                   "median_months": 6.9, "median_ci95": [5.4, 9.0],
                   "rate_6mo_pct": 56.7, "rate_6mo_ci95": [40.4, 70.2] },
          "os":  { "events_n": 25, "events_pct": 58.1,
                   "median_months": 13.9, "median_ci95": [9.1, null],
                   "rate_6mo_pct": 83.7, "rate_6mo_ci95": [68.9, 91.9],
                   "rate_12mo_pct": 55.8, "rate_12mo_ci95": [39.8, 69.1] }
        },
        "orr_by_her2": {
          "ihc_2plus": { "orr_pct": 46.2, "ci95": [26.6, 66.6] },
          "ihc_3plus": { "orr_pct": 58.8, "ci95": [32.9, 81.6] }
        }
      },
      {
        "study_id": "RC48-C009",
        "design_en": "Open-label, multicentre, single-arm Phase II",
        "population_en": "HER2-overexpressing (IHC 2+ or 3+) locally advanced or metastatic urothelial carcinoma after failure of platinum, gemcitabine and taxane therapy; ECOG 0-1; no prior ADC of the same class",
        "n": 64,
        "baseline": {
          "male_pct": 73.4, "median_age": 62.5, "age_range": [40, 79],
          "lung_metastasis_pct": 48.4, "liver_metastasis_pct": 43.8,
          "liver_and_lung_metastasis_pct": 20.3,
          "prior_platinum_pct": 100, "prior_2plus_lines_pct": 85.9,
          "prior_pd1_pdl1_pct": 29.7,
          "ecog_1_n": 38, "ecog_1_pct": 59.4,
          "her2_ihc_2plus": { "n": 41, "pct": 64.1 },
          "her2_ihc_3plus": { "n": 23, "pct": 35.9 }
        },
        "results_irc_recist_v11_fas": {
          "orr_pct": 50.0, "orr_ci95": [37.2, 62.8],
          "best_response": { "cr_n": 1, "cr_pct": 1.6, "pr_n": 31, "pr_pct": 48.4,
                             "sd_n": 17, "sd_pct": 26.6, "pd_n": 13, "pd_pct": 20.3,
                             "ne_n": 2, "ne_pct": 3.1 },
          "dcr_pct": 76.6, "dcr_ci95": [64.3, 86.2],
          "median_dor_months": 8.3, "median_dor_ci95": [4.3, 12.0],
          "pfs": { "events_n": 53, "events_pct": 82.8,
                   "median_months": 5.3, "median_ci95": [4.0, 7.1],
                   "rate_6mo_pct": 44.0, "rate_6mo_ci95": [31.4, 55.8] },
          "os":  { "events_n": 39, "events_pct": 60.9,
                   "median_months": 14.2, "median_ci95": [8.7, 19.2],
                   "rate_6mo_pct": 84.1, "rate_6mo_ci95": [72.5, 91.1],
                   "rate_12mo_pct": 53.2, "rate_12mo_ci95": [40.0, 64.7],
                   "rate_18mo_pct": 38.4, "rate_18mo_ci95": [25.5, 51.2] }
        },
        "orr_by_her2": {
          "ihc_2plus": { "orr_pct": 43.9, "ci95": [28.5, 60.3] },
          "ihc_3plus": { "orr_pct": 60.9, "ci95": [38.5, 80.3] }
        },
        "orr_by_prior_pd1": {
          "prior_pd1_yes": { "n": 19, "orr_pct": 47.4, "ci95": [24.4, 71.1] },
          "prior_pd1_no":  { "n": 45, "orr_pct": 51.1, "ci95": [35.8, 66.3] },
          "conclusion_en": "No difference in efficacy regardless of prior PD-1 therapy"
        }
      }
    ],

    "pooled_analysis": {
      "n": 107,
      "orr_pct": 50.5, "orr_ci95": [40.6, 60.3],
      "dcr_pct": 82.2, "dcr_ci95": [73.7, 89.0],
      "median_dor_months": 7.1, "median_dor_ci95": [5.0, 10.8],
      "conclusion_en": "Superior to historical chemotherapy data. Investigator assessment consistent with IRC. HER2 expression level positively correlated with efficacy.",
      "conclusion_local": "优于化疗的历史数据。研究者评估的结果和 IRC 结果一致，并且 Her2 表达水平和疗效有一定正相关性。"
    },

    "historical_comparators_en": {
      "chemotherapy_orr_pct": 10,
      "pd1_pdl1_orr_pct": [15, 25],
      "note": "Cited as the unmet-need baseline in the benefit-risk assessment"
    }
  },

  "safety": {
    "provenance_warning": "These are trial-observed TEAE incidences in 111 patients from RC48-C002/C005/C009 — NOT regulator-agreed label frequency bands. Do not compare directly with SmPC-style frequency categories.",
    "n_patients": 111,
    "indication": "Urothelial carcinoma",
    "exposure": {
      "mean_cycles": 11.1, "mean_cycles_sd": 7.40,
      "mean_exposure_weeks": 24.649, "mean_exposure_weeks_sd": 18.2523,
      "mean_total_dose_mg_kg": 20.696, "mean_total_dose_sd": 12.5246,
      "dose_interruption_n": 57, "dose_interruption_pct": 51.4,
      "dose_adjustment_n": 32, "dose_adjustment_pct": 28.8
    },

    "teae_incidence_ge_20pct": [
      { "term_en": "Weight decreased",                    "term_local": "体重降低",          "pct": 54.1 },
      { "term_en": "White blood cell count decreased",    "term_local": "白细胞计数降低",     "pct": 53.2 },
      { "term_en": "Hypoaesthesia",                       "term_local": "感觉减退",          "pct": 51.4 },
      { "term_en": "Aspartate aminotransferase increased","term_local": "AST 升高",          "pct": 49.5 },
      { "term_en": "Asthenia",                            "term_local": "乏力",              "pct": 45.9 },
      { "term_en": "Neutrophil count decreased",          "term_local": "中性粒细胞计数降低",  "pct": 45.0 },
      { "term_en": "Alopecia",                            "term_local": "脱发",              "pct": 43.2 },
      { "term_en": "Hypertriglyceridaemia",               "term_local": "高甘油三酯血症",     "pct": 40.5 },
      { "term_en": "Alanine aminotransferase increased",  "term_local": "ALT 升高",          "pct": 38.7 },
      { "term_en": "Decreased appetite",                  "term_local": "食欲减退",          "pct": 36.0 },
      { "term_en": "Gamma-glutamyltransferase increased", "term_local": "γ-谷氨酰转移酶升高", "pct": 34.2 },
      { "term_en": "Blood glucose increased",             "term_local": "血葡萄糖升高",       "pct": 34.2 },
      { "term_en": "Constipation",                        "term_local": "便秘",              "pct": 33.3 },
      { "term_en": "Blood lactate dehydrogenase increased","term_local": "血乳酸脱氢酶升高",  "pct": 33.3 },
      { "term_en": "Nausea",                              "term_local": "恶心",              "pct": 30.6 },
      { "term_en": "Anaemia",                             "term_local": "贫血",              "pct": 27.0 },
      { "term_en": "Pyrexia",                             "term_local": "发热",              "pct": 27.0 },
      { "term_en": "Platelet count decreased",            "term_local": "血小板计数降低",     "pct": 26.1 },
      { "term_en": "Haemoglobin decreased",               "term_local": "血红蛋白降低",       "pct": 23.4 },
      { "term_en": "Vomiting",                            "term_local": "呕吐",              "pct": 22.5 },
      { "term_en": "Apolipoprotein B increased",          "term_local": "载脂蛋白 B 升高",    "pct": 21.6 },
      { "term_en": "Insomnia",                            "term_local": "失眠",              "pct": 21.6 },
      { "term_en": "Blood uric acid increased",           "term_local": "血尿酸升高",         "pct": 20.7 },
      { "term_en": "Proteinuria",                         "term_local": "尿蛋白检出",         "pct": 20.7 }
    ],

    "ctcae_grade_ge_3": {
      "any_n": 74, "any_pct": 66.7,
      "terms_ge_5pct": [
        { "term_en": "Hypoaesthesia",              "term_local": "感觉减退",         "pct": 14.4 },
        { "term_en": "Neutrophil count decreased", "term_local": "中性粒细胞计数降低","pct": 13.5 },
        { "term_en": "Gamma-glutamyltransferase increased", "term_local": "γ-谷氨酰转移酶升高", "pct": 9.0 },
        { "term_en": "Hypertension",               "term_local": "高血压",           "pct": 5.4 }
      ]
    },

    "discontinuation_due_to_teae": {
      "n": 21, "pct": 18.9,
      "leading_terms": [
        { "term_en": "Hypoaesthesia", "pct": 9.9 },
        { "term_en": "Peripheral sensory neuropathy", "pct": 2.7 },
        { "term_en": "Peripheral neuropathy", "pct": 1.8 }
      ]
    },
    "dose_reduction_due_to_teae": {
      "n": 32, "pct": 28.8,
      "leading_terms": [
        { "term_en": "Hypoaesthesia", "pct": 17.1 },
        { "term_en": "Peripheral sensory neuropathy", "pct": 5.4 },
        { "term_en": "Neutrophil count decreased", "pct": 4.5 },
        { "term_en": "Asthenia", "pct": 2.7 }
      ]
    },
    "dose_interruption_due_to_teae": {
      "n": 64, "pct": 57.7,
      "leading_terms": [
        { "term_en": "Hypoaesthesia", "pct": 17.1 },
        { "term_en": "Asthenia", "pct": 7.2 },
        { "term_en": "Peripheral sensory neuropathy", "pct": 7.2 },
        { "term_en": "Neutrophil count decreased", "pct": 6.3 },
        { "term_en": "White blood cell count decreased", "pct": 3.6 },
        { "term_en": "Aspartate aminotransferase increased", "pct": 2.7 }
      ]
    },

    "serious_adverse_events": {
      "n": 31, "pct": 27.9,
      "common_terms": [
        { "term_en": "Infectious pneumonia",       "term_local": "感染性肺炎",   "pct": 2.7 },
        { "term_en": "Pyrexia",                    "term_local": "发热",        "pct": 2.7 },
        { "term_en": "Hypoaesthesia",              "term_local": "感觉减退",    "pct": 1.8 },
        { "term_en": "Intestinal obstruction",     "term_local": "肠梗阻",      "pct": 1.8 },
        { "term_en": "Incomplete intestinal obstruction", "term_local": "不全肠梗阻", "pct": 1.8 },
        { "term_en": "Anaemia",                    "term_local": "贫血",        "pct": 1.8 }
      ]
    },

    "deaths": { "n": 1, "assessment_en": "One on-treatment death, considered probably unrelated to study drug" },

    "cardiac": {
      "soc_incidence_n": 57, "soc_incidence_pct": 13.8,
      "note_en": "No cardiac TEAE by preferred term exceeded 5%",
      "terms": [
        { "term_en": "Sinus tachycardia", "term_local": "窦性心动过速", "pct": 4.8, "drug_related_pct": 1.7 },
        { "term_en": "Palpitations",      "term_local": "心悸",        "pct": 2.9, "drug_related_pct": 1.9 }
      ],
      "ecg_qt_prolongation_pct": 1.0, "ecg_qt_prolongation_drug_related_pct": 0.7,
      "ejection_fraction_decreased_pct": 1.0, "ejection_fraction_decreased_drug_related_pct": 1.0,
      "grade_ge_3_qt_prolongation": "None",
      "grade_ge_3_ejection_fraction_decreased_pct": 0.2
    }
  },

  "risk_management_plan": {
    "important_identified_risks": [
      { "risk_en": "Peripheral neurotoxicity", "risk_local": "周围神经毒性" },
      { "risk_en": "Myelosuppression",         "risk_local": "骨髓抑制" },
      { "risk_en": "Abnormal liver function",  "risk_local": "肝功能异常" },
      { "risk_en": "Gastrointestinal injury",  "risk_local": "胃肠道损伤" }
    ],
    "important_potential_risks": [
      { "risk_en": "Reproductive toxicity", "risk_local": "生殖毒性" },
      { "risk_en": "Cardiotoxicity",        "risk_local": "心脏毒性" },
      { "risk_en": "Hyperglycaemia",        "risk_local": "高血糖" }
    ],
    "reviewer_addition_en": "The reviewer determined that hyperglycaemia and cardiac risk should be added to the important potential risks, with ongoing data collection.",
    "missing_information": [
      { "population_en": "Hepatic impairment",        "population_local": "肝功能损伤" },
      { "population_en": "Renal impairment",          "population_local": "肾功能损伤" },
      { "population_en": "Elderly",                   "population_local": "老年人群" },
      { "population_en": "Pregnant and lactating women", "population_local": "妊娠和哺乳期妇女" },
      { "population_en": "Children",                  "population_local": "儿童" },
      { "population_en": "Drug interactions",         "population_local": "药物相互作用" },
      { "population_en": "Long-term use",             "population_local": "长期用药" }
    ],
    "additional_pharmacovigilance_en": "Confirmatory Phase III randomised controlled study in urothelial carcinoma, with further monitoring and confirmation of the important risks (peripheral neurotoxicity, myelosuppression, abnormal liver function, gastrointestinal injury).",
    "risk_minimisation_measures": [
      { "measure_en": "Physician education",                    "measure_local": "医生教育" },
      { "measure_en": "Physician education of patients",        "measure_local": "医生对患者进行教育" },
      { "measure_en": "Patient education booklet as required",  "measure_local": "按需制定患者教育手册" }
    ]
  },

  "post_marketing_requirements": [
    {
      "type_en": "Confirmatory study for conditional approval",
      "study_id": "RC48-C016",
      "design_en": "Randomised, open-label, parallel-controlled, multicentre Phase III study of disitamab vedotin plus toripalimab versus gemcitabine plus cisplatin/carboplatin in HER2-expressing locally advanced or metastatic urothelial carcinoma",
      "design_local": "注射用维迪西妥单抗联合特瑞普利单抗对比吉西他滨联合顺铂/卡铂治疗 HER2 表达局部晚期或转移性尿路上皮癌的随机、开放、平行对照、多中心 III 期临床研究",
      "start_deadline_en": "Within 1 year of approval of this indication",
      "completion_deadline_en": "Within 5 years",
      "submission_en": "Full study report to be submitted as a supplementary application, applying for full approval"
    }
  ],

  "inspection": {
    "development_and_manufacturing_site_inspection": "不适用 / Not applicable",
    "sample_testing": "不适用 / Not applicable",
    "clinical_data_inspection": {
      "conducted_by_en": "NMPA Inspection Center jointly with Beijing and Chongqing Municipal Drug Administrations",
      "conducted_by_local": "国家局核查中心联合北京市药品监督管理局、重庆市药品监督管理局",
      "dates": ["2021-11-10 to 2021-11-12", "2021-11-01 to 2021-11-03"],
      "sites_en": ["Peking University Cancer Hospital", "Chongqing Cancer Hospital"],
      "sites_local": ["北京大学肿瘤医院", "重庆市肿瘤医院"],
      "finding_en": "No authenticity issues identified",
      "finding_local": "未发现真实性问题"
    }
  },

  "disease_background": {
    "condition_en": "Urothelial carcinoma (UC)",
    "epidemiology": {
      "bladder_cancer_global_incidence_2018": 549393,
      "bladder_cancer_global_deaths_2018": 199922,
      "male_to_female_incidence_ratio": 4,
      "china_new_cases_2015": 81000,
      "china_deaths_2015": 33000,
      "uc_in_bladder_pct": ">90",
      "utuc_pct": [5, 10]
    },
    "standard_of_care_en": {
      "first_line": "Platinum-based combination chemotherapy, response rate ~50%",
      "gc_regimen_median_pfs_months": 7,
      "gc_regimen_median_os_months": 14,
      "five_year_survival_pct": [10, 20],
      "carboplatin_regimen_median_pfs_months": 5,
      "carboplatin_regimen_median_os_months": 9,
      "second_line": "Paclitaxel, albumin-bound paclitaxel, docetaxel; median PFS 3-6 months, median OS 7-9 months",
      "immunotherapy_note": "Tislelizumab and toripalimab conditionally approved in China for advanced UC after platinum failure; ORR under 30%"
    }
  },

  "benefit_risk_conclusion": {
    "technical_conclusion_local": "经风险获益评估，现有研究和数据支持本品附条件批准用于\"既往接受过含铂化疗且 HER2 过表达局部晚期或转移性尿路上皮癌的患者，HER2 过表达定义为 HER2 免疫组织化学检查结果为 2+或 3+\"。",
    "technical_conclusion_en": "Following benefit-risk assessment, the available studies and data support conditional approval for patients with HER2-overexpressing locally advanced or metastatic urothelial carcinoma previously treated with platinum-containing chemotherapy, where HER2 overexpression is defined as IHC 2+ or 3+.",
    "assessment_en": "Positive benefit-risk. Most adverse reactions are manageable with dose adjustment and symptomatic supportive care.",
    "unmet_need_en": "Substantial unmet need in locally advanced or metastatic UC after platinum failure, particularly in patients who have also failed gemcitabine and taxanes."
  },

  "raw_sections": {
    "适应症": "本品适用于既往接受过含铂化疗且 HER2 过表达局部晚期或转移性尿路上皮癌的患者，HER2 过表达定义为 HER2 免疫组织化学检查结果为 2+或 3+。上述适应症是基于单臂临床试验的客观缓解率结果给予的附条件批准。上述适应症的完全批准将取决于正在开展中的确证性临床试验能否证实本品在上述人群的临床获益。",
    "用法用量": "尿路上皮癌患者：2.0 mg/kg，每两周一次，静脉滴注，直至疾病进展或出现不可耐受的毒性。",
    "技术结论": "经风险获益评估，现有研究和数据支持本品附条件批准用于\"既往接受过含铂化疗且 HER2 过表达局部晚期或转移性尿路上皮癌的患者，HER2 过表达定义为 HER2 免疫组织化学检查结果为 2+或 3+\"。"
  }
}
```

---

## What this document changes about the schema

Comparing the three examples:

| | Pregabalin (MHRA) | Ansentron (ANVISA) | Disitamab Vedotin (NMPA) |
| --- | --- | --- | --- |
| Document type | SmPC (label) | Bula (label) | **Review report** |
| Contraindications | ✓ | ✓ | ✗ |
| Excipients / packaging / storage | ✓ | ✓ | ✗ |
| Pharmacokinetics | ✓ | ✓ | ✗ (deferred to another report) |
| ADR data | labelled frequency bands | labelled frequency bands | **trial TEAE incidence** |
| Approval pathway / milestones | ✗ | partial | ✓✓ |
| Clinical trial detail | summary | one citation | ✓✓ full |
| Risk management plan | ✗ | ✗ | ✓ |
| Post-market commitments | ✗ | ✗ | ✓ |

Three conclusions:

**1. Restore `source_document_type` as a column.** It is the only way a consumer
can tell "no contraindications listed" from "this document type never carries
them". Everything else in `product_data` is display-only, but this one changes
how you *interpret* NULLs — which makes it a filter, which makes it a column.

**2. `indications` needs union-merge, not replace.** This document adds urothelial
carcinoma to a registration that already carried gastric cancer. A replace-on-upsert
will destroy prior indications, and for China — where 增加新适应症 filings are
routine — that will happen often and silently.

**3. One product will span multiple documents.** This registration has at least
three: the CXSS2000044 review (gastric cancer, with the tox and PK), this
CXSS2101011 review (UC), and the 说明书 label that neither review contains. The
current `source_url TEXT` holds one. Your existing
`drug_predicate_raw_records.document_url` is already `TEXT[]` for precisely this
reason — the products layer should follow it.
