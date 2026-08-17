-- source.products — parsed product layer, downstream of drug_predicate_raw_records.
-- One flat table. Detail that is only ever displayed (never filtered on) goes in product_data.

CREATE TABLE IF NOT EXISTS source.products (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- naming ----------------------------------------------------------------
    -- product_name: full name as printed, incl. strength and form.
    --               'Pregabalin Wockhardt 225 mg Hard Capsules'
    -- brand_name:   trade-name portion, English/translated. 'Pregabalin Wockhardt'
    -- brand_name_local: same in original script, for CN/BR/etc. '普瑞巴林胶囊'
    --               Never overwrite this with a translation — it is the only
    --               way to re-derive a bad translation without re-crawling.
    product_name              TEXT,
    brand_name                TEXT,
    brand_name_local          TEXT,
    generic_name              TEXT,

    -- geography -------------------------------------------------------------
    country_id                INTEGER REFERENCES source.country(id) ON DELETE SET NULL,
    regulator                 TEXT,

    -- companies -------------------------------------------------------------
    -- MAH (licence holder) and manufacturer are different roles. Most sources
    -- give you the MAH: SmPC section 7 here is Wockhardt UK Ltd.
    mah_name                  TEXT,
    mah_address               TEXT,
    manufacturer              TEXT,
    manufacturer_address      TEXT,

    -- registration ----------------------------------------------------------
    registration_number       TEXT,
    product_type              TEXT,
    status                    TEXT,          -- regulatory: Active | Withdrawn | Suspended
    registration_date         DATE,
    approval_date             DATE,
    market_authorization_date DATE,
    expiry_date               DATE,
    withdrawal_date           DATE,
    -- SmPC section 10. The label's own version stamp — the only reliable way
    -- to tell a re-crawl from a genuinely changed document.
    label_revision_date       DATE,

    -- classification --------------------------------------------------------
    atc_code                  TEXT,          -- 'N03AX16' — the cross-country match key

    -- clinical --------------------------------------------------------------
    therapeutic_areas         TEXT[],
    indications               TEXT[],
    symptoms                  TEXT[],        -- indication symptoms (what the patient has)
    adverse_reactions         TEXT[],        -- what the drug causes — NOT the same thing
    contraindications         TEXT[],
    active_ingredients        TEXT[],
    strengths                 TEXT[],        -- {'225 mg'}
    dosage_forms              TEXT[],
    routes                    TEXT[],

    is_generic                BOOLEAN,
    reference_product         TEXT,          -- originator, e.g. 'Lyrica'

    product_data              JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- provenance + pipeline -------------------------------------------------
    source_url                TEXT,
    source_language           TEXT,          -- language of the source doc: en, zh, pt

    -- is_active is ROW currency, not regulatory status. A withdrawn product is
    -- status='Withdrawn' AND is_active=true — the row is still current truth.
    -- is_active=false means the row itself is superseded.
    is_active                 BOOLEAN NOT NULL DEFAULT TRUE,
    processing_status         TEXT NOT NULL DEFAULT 'PENDING',

    created_at                TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_processing_status CHECK (processing_status IN
        ('PENDING','PARSED','ENRICHED','NEEDS_REVIEW','FAILED','SKIPPED')),

    -- upsert key on re-crawl
    CONSTRAINT uq_products_country_regnum UNIQUE (country_id, registration_number)
);

CREATE INDEX IF NOT EXISTS idx_products_country     ON source.products(country_id);
CREATE INDEX IF NOT EXISTS idx_products_generic     ON source.products(lower(generic_name));
CREATE INDEX IF NOT EXISTS idx_products_atc         ON source.products(atc_code);
CREATE INDEX IF NOT EXISTS idx_products_regnum      ON source.products(registration_number);
CREATE INDEX IF NOT EXISTS idx_products_queue       ON source.products(processing_status)
    WHERE processing_status IN ('PENDING','NEEDS_REVIEW');
CREATE INDEX IF NOT EXISTS idx_products_ingredients ON source.products USING GIN(active_ingredients);
CREATE INDEX IF NOT EXISTS idx_products_strengths   ON source.products USING GIN(strengths);
CREATE INDEX IF NOT EXISTS idx_products_indications ON source.products USING GIN(indications);
CREATE INDEX IF NOT EXISTS idx_products_data        ON source.products USING GIN(product_data jsonb_path_ops);


-- ============================================================================
-- Multiple strengths in one PDF
-- ============================================================================
--
-- strengths TEXT[] carries them for search; the per-strength detail (pack
-- sizes, appearance, ingredient breakdown) goes in product_data.presentations.
--
-- The only case that needs more than one ROW is when the regulator issues a
-- separate registration number per strength. MHRA does: the Pregabalin
-- Wockhardt line is PL 29831/0640 through /0648, one per strength, published
-- under a single SmPC. Since registration_number is the upsert key, those
-- become 9 rows sharing one source_url — each with strengths = {'225 mg'} etc.
--
-- TGA does not: one ARTG can cover 5 mg and 10 mg, so that stays one row with
-- strengths = {'5 mg','10 mg'} and two entries in product_data.presentations.
--
-- Decide this per regulator in the parser, not per document.
--
--
-- ============================================================================
-- product_data  —  predicate assessment only. Full spec: product_data_spec.md
-- ============================================================================
--
-- Scope: everything here serves either MATCHING (is this the same drug, form
-- and use?) or PRECEDENT (approved how, on what evidence?). Anything serving
-- neither is not stored. Six keys, nothing else:
--
--   substance          inn, salt_form, modality, target, moa       -> match
--   presentations      strength / form / route / salt              -> match
--   indications        condition, population, line, biomarker,
--                      per-indication approval_date                -> match
--   approval           pathway, registration_class, conditional,
--                      priority_review, reference_product          -> precedent
--   pivotal_evidence   design, n, endpoint, value, comparator,
--                      outcome — headline results only             -> precedent
--   key_risks          the handful driving labelling/monitoring    -> precedent
--
-- Deliberately NOT stored: full ADR matrices, warning narratives, interaction
-- lists, detailed PK, trial demographics, epidemiology, risk management plans,
-- packaging, storage, excipients, verbatim section text. See the spec for the
-- reasoning on each, and for the two that may need to come back (excipients if
-- pharmaceutical equivalence enters scope; pharmacokinetics if bioequivalence
-- does).
--
-- Set product_data->>'_schema_version' so parsers can migrate.
-- Never duplicate a column into product_data; the two copies will diverge.
