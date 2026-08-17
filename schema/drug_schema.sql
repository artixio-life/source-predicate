-- ============================================================================
-- DRUG SCHEMA
-- Drug regulatory data maintained by Artixio
-- ============================================================================
-- This schema contains all drug regulatory data including:
-- - Geographic and regulatory reference data (countries, agencies, regions)
-- - Drug classifications, guidelines, and regulatory information
-- - Predicate analysis data
-- - Versioning and relationship tracking
-- - Drug-specific features (import logs, therapy areas)
-- ============================================================================

-- Create drug schema
CREATE SCHEMA IF NOT EXISTS drug;

-- ============================================================================
-- 1. REGULATORY GEOGRAPHY (Denormalized: Region + Country + Agency)
-- ============================================================================
-- Single table combining region, country, and agency information
-- Hierarchy: Region (1) → Country (N) → Agency (1 per country for drugs/biologics)
-- ============================================================================

CREATE TABLE IF NOT EXISTS drug.regulatory_geography (
  id SERIAL PRIMARY KEY,
  
  -- Region information
  region_name VARCHAR(255) NOT NULL,
  region_code VARCHAR(10),                    -- e.g., 'NA', 'EU', 'APAC', 'LATAM'
  region_description TEXT,
  
  -- Country information
  country_name VARCHAR(255) NOT NULL,
  country_code VARCHAR(3) NOT NULL UNIQUE,    -- ISO 3166-1 alpha-2/3 (e.g., 'US', 'EU', 'UK')
  flag_icon VARCHAR(10),                      -- Flag emoji (e.g., 🇺🇸)
  flag_url VARCHAR(512),                      -- Optional flag image URL
  
  -- Agency information (one regulatory agency per country for drugs/biologics)
  agency_name VARCHAR(255) NOT NULL,
  agency_acronym VARCHAR(50),                 -- e.g., 'FDA', 'EMA', 'PMDA'
  agency_description TEXT,
  agency_icon_url VARCHAR(512),               -- Agency logo URL
  agency_icon_name VARCHAR(100),              -- Icon identifier
  agency_website VARCHAR(512),                -- Official agency website
  
  -- Metadata
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
  created_by INTEGER                          -- References public.users (not enforced)
);

-- Indexes for regulatory_geography
CREATE INDEX IF NOT EXISTS idx_reg_geo_country_code ON drug.regulatory_geography(country_code);
CREATE INDEX IF NOT EXISTS idx_reg_geo_country_name ON drug.regulatory_geography(country_name);
CREATE INDEX IF NOT EXISTS idx_reg_geo_region_name ON drug.regulatory_geography(region_name);
CREATE INDEX IF NOT EXISTS idx_reg_geo_region_code ON drug.regulatory_geography(region_code);
CREATE INDEX IF NOT EXISTS idx_reg_geo_agency_acronym ON drug.regulatory_geography(agency_acronym);
CREATE INDEX IF NOT EXISTS idx_reg_geo_is_active ON drug.regulatory_geography(is_active);

-- Unique constraint on agency acronym (one agency per acronym)
CREATE UNIQUE INDEX IF NOT EXISTS idx_reg_geo_unique_agency 
ON drug.regulatory_geography (LOWER(agency_acronym)) WHERE agency_acronym IS NOT NULL;

-- ============================================================================
-- Sample data structure for regulatory_geography:
-- ============================================================================
-- | region_name    | region_code | country_name   | country_code | flag_icon | agency_name                        | agency_acronym |
-- |----------------|-------------|----------------|--------------|-----------|------------------------------------| ---------------|
-- | North America  | NA          | United States  | US           | 🇺🇸        | Food and Drug Administration       | FDA            |
-- | North America  | NA          | Canada         | CA           | 🇨🇦        | Health Canada                      | HC             |
-- | Europe         | EU          | European Union | EU           | 🇪🇺        | European Medicines Agency          | EMA            |
-- | Europe         | EU          | United Kingdom | UK           | 🇬🇧        | MHRA                               | MHRA           |
-- | Europe         | EU          | Switzerland    | CH           | 🇨🇭        | Swissmedic                         | SWISSMEDIC     |
-- | Asia Pacific   | APAC        | Japan          | JP           | 🇯🇵        | PMDA                               | PMDA           |
-- | Asia Pacific   | APAC        | China          | CN           | 🇨🇳        | NMPA                               | NMPA           |
-- | Asia Pacific   | APAC        | India          | IN           | 🇮🇳        | CDSCO                              | CDSCO          |
-- | Asia Pacific   | APAC        | Australia      | AU           | 🇦🇺        | TGA                                | TGA            |
-- | Asia Pacific   | APAC        | South Korea    | KR           | 🇰🇷        | MFDS                               | MFDS           |
-- | Latin America  | LATAM       | Brazil         | BR           | 🇧🇷        | ANVISA                             | ANVISA         |
-- | Latin America  | LATAM       | Mexico         | MX           | 🇲🇽        | COFEPRIS                           | COFEPRIS       |
-- ============================================================================

-- ============================================================================
-- 2. UNIFIED REFERENCE DATA TABLE
-- ============================================================================
-- Single table for all lookup/reference data types:
-- - guideline_type: Types of guidelines (ICH, FDA Guidance, etc.)
-- - function: Business functions (Regulatory Affairs, CMC, Clinical, QA, etc.)
-- - key_topic: Key topics (Safety, Efficacy, Manufacturing, etc.)
-- - classification: Drug classifications with risk levels
-- - product: Drug products reference
-- ============================================================================

CREATE TABLE IF NOT EXISTS drug.reference_data (
  id SERIAL PRIMARY KEY,
  
  -- Type discriminator
  ref_type VARCHAR(50) NOT NULL CHECK (ref_type IN (
      'guideline_type',
  'function',
  'key_topic',
  'classification',
  'product',
  'therapeutic_area',
  'application_area'
  )),
  
  -- Common fields
  name VARCHAR(255) NOT NULL,
  code VARCHAR(50),
  description TEXT,
  
  -- Type-specific fields
  category VARCHAR(100),        -- For functions, products
  risk_level VARCHAR(50),       -- For classifications
  geography_id INTEGER REFERENCES drug.regulatory_geography(id) ON DELETE SET NULL,  -- For classifications
  
  -- Hierarchy support (for nested categories)
  parent_id INTEGER REFERENCES drug.reference_data(id) ON DELETE CASCADE,
  
  -- Flexible metadata for future extensions
  metadata JSONB DEFAULT '{}'::jsonb,
  
  -- Standard fields
  is_active BOOLEAN DEFAULT TRUE,
  sort_order INTEGER DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  created_by INTEGER,
  
  -- Unique constraint per type
  UNIQUE(ref_type, name)
);

ALTER TABLE drug.reference_data
ADD COLUMN domains TEXT[];

-- Indexes for reference_data
CREATE INDEX IF NOT EXISTS idx_reference_data_type ON drug.reference_data(ref_type);
CREATE INDEX IF NOT EXISTS idx_reference_data_name ON drug.reference_data(name);
CREATE INDEX IF NOT EXISTS idx_reference_data_code ON drug.reference_data(code);
CREATE INDEX IF NOT EXISTS idx_reference_data_category ON drug.reference_data(category);
CREATE INDEX IF NOT EXISTS idx_reference_data_geography ON drug.reference_data(geography_id);
CREATE INDEX IF NOT EXISTS idx_reference_data_parent ON drug.reference_data(parent_id);
CREATE INDEX IF NOT EXISTS idx_reference_data_active ON drug.reference_data(is_active);
CREATE INDEX IF NOT EXISTS idx_reference_data_type_active ON drug.reference_data(ref_type, is_active);

-- ============================================================================
-- Sample data structure for reference_data:
-- ============================================================================
-- | ref_type       | name                    | code  | category        | risk_level |
-- |----------------|-------------------------|-------|-----------------|------------|
-- | guideline_type | ICH Guideline           | ICH   | NULL            | NULL       |
-- | guideline_type | FDA Guidance            | FDA   | NULL            | NULL       |
-- | function       | Regulatory Affairs      | RA    | Operations      | NULL       |
-- | function       | CMC                     | CMC   | Technical       | NULL       |
-- | function       | Clinical                | CLIN  | Technical       | NULL       |
-- | key_topic      | Safety                  | SAF   | NULL            | NULL       |
-- | key_topic      | Efficacy                | EFF   | NULL            | NULL       |
-- | classification | Class I Medical Device  | CL1   | NULL            | Low        |
-- | classification | Class III Drug          | CL3   | NULL            | High       |
-- ============================================================================

-- NOTE: The following tables were MERGED into reference_data:
-- - drug_guideline_type → ref_type = 'guideline_type'
-- - drug_function → ref_type = 'function'
-- - drug_key_topic → ref_type = 'key_topic'
-- - drug_classification → ref_type = 'classification'
-- - drug_products → ref_type = 'product'

-- ============================================================================
-- 2.5 REGULATORY HIERARCHY SYSTEM
-- Product Type → Regulatory Pathway → Guideline Category → Guidelines
-- ============================================================================

-- Product types (Drugs, Biologics, Cell Therapy, Gene Therapy, etc.)
CREATE TABLE IF NOT EXISTS drug.product_type (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  code VARCHAR(50) NOT NULL UNIQUE,
  description TEXT,
  icon_name VARCHAR(100),
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_product_type_code ON drug.product_type(code);
CREATE INDEX IF NOT EXISTS idx_product_type_name ON drug.product_type(name);
CREATE INDEX IF NOT EXISTS idx_product_type_is_active ON drug.product_type(is_active);

-- Regulatory pathways (Agency-specific: NDA, ANDA, BLA for FDA; MAA for EMA; etc.)
CREATE TABLE IF NOT EXISTS drug.regulatory_pathway (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  code VARCHAR(50) NOT NULL,
  description TEXT,
  geography_id INTEGER NOT NULL REFERENCES drug.regulatory_geography(id) ON DELETE CASCADE,  -- Links to country/agency
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(code, geography_id)  -- Same code can exist for different geographies
);

CREATE INDEX IF NOT EXISTS idx_regulatory_pathway_code ON drug.regulatory_pathway(code);
CREATE INDEX IF NOT EXISTS idx_regulatory_pathway_geography ON drug.regulatory_pathway(geography_id);
CREATE INDEX IF NOT EXISTS idx_regulatory_pathway_is_active ON drug.regulatory_pathway(is_active);

-- Guideline categories (Self-referencing hierarchy for technical domains)
-- Level 1: Main categories (CMC, GMP, Clinical, Labeling, etc.)
-- Level 2+: Sub-categories (Drug Substance, Drug Product, etc.)
CREATE TABLE IF NOT EXISTS drug.guideline_category (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  code VARCHAR(50) NOT NULL UNIQUE,
  description TEXT,
  parent_id INTEGER REFERENCES drug.guideline_category(id) ON DELETE CASCADE,
  level INTEGER NOT NULL DEFAULT 1,  -- 1=main category, 2=sub-category, 3=sub-sub (if needed)
  sort_order INTEGER DEFAULT 0,
  icon_name VARCHAR(100),
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_guideline_category_code ON drug.guideline_category(code);
CREATE INDEX IF NOT EXISTS idx_guideline_category_parent ON drug.guideline_category(parent_id);
CREATE INDEX IF NOT EXISTS idx_guideline_category_level ON drug.guideline_category(level);
CREATE INDEX IF NOT EXISTS idx_guideline_category_sort_order ON drug.guideline_category(sort_order);
CREATE INDEX IF NOT EXISTS idx_guideline_category_is_active ON drug.guideline_category(is_active);

-- ============================================================================
-- 3. DRUG GUIDELINES (Main Content Table)
-- ============================================================================

-- Drug guidelines with all 31 columns
CREATE TABLE IF NOT EXISTS drug.drug_guideline (
  id SERIAL PRIMARY KEY,
  
  -- Core information
  title VARCHAR(255) NOT NULL,
  headline VARCHAR(500),
  description TEXT,
  
  -- Documents
  document_url VARCHAR(512),
  original_url VARCHAR(512),
  document_filename VARCHAR(255),
  
  -- Dates
  published_date DATE,
  effective_date DATE,
  deadline_date DATE,
  
  -- References
  guideline_type_id INTEGER REFERENCES drug.reference_data(id) ON DELETE SET NULL,  -- ref_type='guideline_type'
  geography_id INTEGER REFERENCES drug.regulatory_geography(id) ON DELETE SET NULL,  -- Links to region/country/agency
  
  -- Status and type
  status VARCHAR(100) DEFAULT 'Published',
  is_active BOOLEAN DEFAULT TRUE,
  document_type VARCHAR(100) DEFAULT 'Guidance Document' CHECK (document_type IN (
    'Act', 'Advisory', 'Advice', 'Alert', 'Announcement', 'Amendments', 'Circular', 'Consultation',
    'Decree', 'Directive', 'Disposition', 'FAQ', 'Draft Guidance', 'Guidance Document', 'Guideline',
    'Guide', 'Legislation', 'Manual', 'Normative Instruction', 'Notice', 'Notification', 'Ordinance',
    'Policy', 'Paper', 'Q&A', 'Reflection Paper', 'Requirement', 'Regulation', 'Resolution', 'Rule',
    'Report', 'Standard', 'Specifications', 'SOP (Standard Operating Procedure)', 'Inspection Report',
    'Template/Form', 'Checklist', 'Update', 'Interpretation', 'News', 'Recommendation'
  )),
  
  -- Impact Assessment
  impact_assessment VARCHAR(3) NOT NULL DEFAULT 'No' CHECK (impact_assessment IN ('Yes', 'No')),
  impact_category VARCHAR(255),
  functions_impacted TEXT,
  documents_impacted TEXT,
  current_state TEXT,
  probable_future_state TEXT,
  recommendations TEXT,
  
  -- General functions
  functions TEXT,
  
  -- Multi-select arrays
  functional_area TEXT[],
  product_category TEXT[],
  
  -- Enhanced content
  background TEXT,
  summary TEXT,
  guideline_document_text TEXT,
  
  -- Metadata
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  created_by INTEGER
);


-- Indexes for drug guidelines
CREATE INDEX IF NOT EXISTS idx_drug_guideline_title ON drug.drug_guideline(title);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_published_date ON drug.drug_guideline(published_date DESC);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_effective_date ON drug.drug_guideline(effective_date);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_guideline_type ON drug.drug_guideline(guideline_type_id);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_geography ON drug.drug_guideline(geography_id);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_status ON drug.drug_guideline(status);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_is_active ON drug.drug_guideline(is_active);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_document_type ON drug.drug_guideline(document_type);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_impact_assessment ON drug.drug_guideline(impact_assessment);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_functional_area ON drug.drug_guideline USING GIN(functional_area);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_product_category ON drug.drug_guideline USING GIN(product_category);

-- Full-text search indexes
CREATE INDEX IF NOT EXISTS idx_drug_guideline_title_search ON drug.drug_guideline USING gin(to_tsvector('english', title));
CREATE INDEX IF NOT EXISTS idx_drug_guideline_description_search ON drug.drug_guideline USING gin(to_tsvector('english', COALESCE(description, '')));
CREATE INDEX IF NOT EXISTS idx_drug_guideline_document_text_search ON drug.drug_guideline USING gin(to_tsvector('english', COALESCE(guideline_document_text, '')));

-- ============================================================================
-- 4. GUIDELINE RELATIONSHIPS & JUNCTION TABLES
-- ============================================================================

-- ============================================================================
-- Guideline-Reference Junction Tables
-- All reference IDs point to reference_data table with appropriate ref_type
-- ============================================================================

-- Guideline-Classification relationships (ref_type='classification')
CREATE TABLE IF NOT EXISTS drug.drug_guideline_classification (
  id SERIAL PRIMARY KEY,
  guideline_id INTEGER REFERENCES drug.drug_guideline(id) ON DELETE CASCADE,
  reference_id INTEGER REFERENCES drug.reference_data(id) ON DELETE CASCADE,  -- ref_type='classification'
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(guideline_id, reference_id)
);

CREATE INDEX IF NOT EXISTS idx_drug_guideline_classification_guideline ON drug.drug_guideline_classification(guideline_id);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_classification_ref ON drug.drug_guideline_classification(reference_id);

-- Guideline-Key Topic relationships (ref_type='key_topic')
CREATE TABLE IF NOT EXISTS drug.drug_guideline_key_topic (
  id SERIAL PRIMARY KEY,
  guideline_id INTEGER REFERENCES drug.drug_guideline(id) ON DELETE CASCADE,
  reference_id INTEGER REFERENCES drug.reference_data(id) ON DELETE CASCADE,  -- ref_type='key_topic'
  relevance_score INTEGER,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(guideline_id, reference_id)
);

CREATE INDEX IF NOT EXISTS idx_drug_guideline_key_topic_guideline ON drug.drug_guideline_key_topic(guideline_id);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_key_topic_ref ON drug.drug_guideline_key_topic(reference_id);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_key_topic_relevance ON drug.drug_guideline_key_topic(relevance_score);

-- Guideline-Function relationships (ref_type='function')
CREATE TABLE IF NOT EXISTS drug.drug_guideline_function (
  id SERIAL PRIMARY KEY,
  guideline_id INTEGER REFERENCES drug.drug_guideline(id) ON DELETE CASCADE,
  reference_id INTEGER REFERENCES drug.reference_data(id) ON DELETE CASCADE,  -- ref_type='function'
  impact_level VARCHAR(50),
  description TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  created_by INTEGER,
  UNIQUE(guideline_id, reference_id)
);

CREATE INDEX IF NOT EXISTS idx_drug_guideline_function_guideline ON drug.drug_guideline_function(guideline_id);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_function_ref ON drug.drug_guideline_function(reference_id);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_function_impact_level ON drug.drug_guideline_function(impact_level);

-- ============================================================================
-- 4.5 REGULATORY HIERARCHY JUNCTION TABLES
-- Link guidelines to Product Type, Regulatory Pathway, and Guideline Category
-- ============================================================================

-- Guideline-Product Type relationships (many-to-many)
CREATE TABLE IF NOT EXISTS drug.drug_guideline_product_type (
  id SERIAL PRIMARY KEY,
  guideline_id INTEGER NOT NULL REFERENCES drug.drug_guideline(id) ON DELETE CASCADE,
  product_type_id INTEGER NOT NULL REFERENCES drug.product_type(id) ON DELETE CASCADE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(guideline_id, product_type_id)
);

CREATE INDEX IF NOT EXISTS idx_drug_guideline_product_type_guideline ON drug.drug_guideline_product_type(guideline_id);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_product_type_product_type ON drug.drug_guideline_product_type(product_type_id);

-- Guideline-Regulatory Pathway relationships (many-to-many)
CREATE TABLE IF NOT EXISTS drug.drug_guideline_pathway (
  id SERIAL PRIMARY KEY,
  guideline_id INTEGER NOT NULL REFERENCES drug.drug_guideline(id) ON DELETE CASCADE,
  pathway_id INTEGER NOT NULL REFERENCES drug.regulatory_pathway(id) ON DELETE CASCADE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(guideline_id, pathway_id)
);

CREATE INDEX IF NOT EXISTS idx_drug_guideline_pathway_guideline ON drug.drug_guideline_pathway(guideline_id);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_pathway_pathway ON drug.drug_guideline_pathway(pathway_id);

-- Guideline-Guideline Category relationships (many-to-many)
CREATE TABLE IF NOT EXISTS drug.drug_guideline_guideline_category (
  id SERIAL PRIMARY KEY,
  guideline_id INTEGER NOT NULL REFERENCES drug.drug_guideline(id) ON DELETE CASCADE,
  category_id INTEGER NOT NULL REFERENCES drug.guideline_category(id) ON DELETE CASCADE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(guideline_id, category_id)
);

CREATE INDEX IF NOT EXISTS idx_drug_guideline_guideline_category_guideline ON drug.drug_guideline_guideline_category(guideline_id);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_guideline_category_category ON drug.drug_guideline_guideline_category(category_id);

-- ============================================================================
-- 4.6 PATHWAY-CATEGORY RELATIONSHIPS
-- Defines which technical categories are relevant for each regulatory pathway
-- This answers: "What CMC/GMP/Clinical requirements apply to BLA?"
-- ============================================================================

-- Pathway-Category mapping (which categories apply to which pathways)
CREATE TABLE IF NOT EXISTS drug.pathway_category (
  id SERIAL PRIMARY KEY,
  pathway_id INTEGER NOT NULL REFERENCES drug.regulatory_pathway(id) ON DELETE CASCADE,
  category_id INTEGER NOT NULL REFERENCES drug.guideline_category(id) ON DELETE CASCADE,
  is_mandatory BOOLEAN DEFAULT FALSE,  -- Is this category mandatory for this pathway?
  applicability_notes TEXT,            -- Notes about how this category applies to the pathway
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(pathway_id, category_id)
);

CREATE INDEX IF NOT EXISTS idx_pathway_category_pathway ON drug.pathway_category(pathway_id);
CREATE INDEX IF NOT EXISTS idx_pathway_category_category ON drug.pathway_category(category_id);
CREATE INDEX IF NOT EXISTS idx_pathway_category_mandatory ON drug.pathway_category(is_mandatory);

-- Pathway-Product Type mapping (which product types can use which pathways)
-- E.g., BLA is for Biologics, NDA is for Drugs, 351(k) is for Biosimilars
CREATE TABLE IF NOT EXISTS drug.pathway_product_type (
  id SERIAL PRIMARY KEY,
  pathway_id INTEGER NOT NULL REFERENCES drug.regulatory_pathway(id) ON DELETE CASCADE,
  product_type_id INTEGER NOT NULL REFERENCES drug.product_type(id) ON DELETE CASCADE,
  is_primary BOOLEAN DEFAULT FALSE,    -- Is this the primary pathway for this product type?
  notes TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(pathway_id, product_type_id)
);

CREATE INDEX IF NOT EXISTS idx_pathway_product_type_pathway ON drug.pathway_product_type(pathway_id);
CREATE INDEX IF NOT EXISTS idx_pathway_product_type_product_type ON drug.pathway_product_type(product_type_id);
CREATE INDEX IF NOT EXISTS idx_pathway_product_type_primary ON drug.pathway_product_type(is_primary);

-- ============================================================================
-- 4.7 REGULATORY ANNOUNCEMENTS & UPDATES
-- Announcements, notices, and updates linked to parent guidelines
-- This is the "right side" - dynamic regulatory intelligence
-- ============================================================================

-- Regulatory announcements (updates, notices, draft guidances, etc.)
CREATE TABLE IF NOT EXISTS drug.regulatory_announcement (
  id SERIAL PRIMARY KEY,
  
  -- Core information
  title VARCHAR(500) NOT NULL,
  headline VARCHAR(1000),
  description TEXT,
  summary TEXT,
  
  -- Document references
  document_url TEXT[],
  original_url VARCHAR(512),
  document_filename VARCHAR(255),
  
  -- Dates
  announcement_date DATE,      -- When the announcement was made
  effective_date DATE,                   -- When changes take effect
  comment_deadline DATE,                 -- For draft guidances - comment period deadline
  implementation_deadline DATE,          -- When full compliance is required
  
  -- Type and status
  announcement_type VARCHAR(100) NOT NULL CHECK (announcement_type IN (
    'Draft Guidance',           -- New draft guidance for comment
    'Final Guidance',           -- Finalized guidance
    'Guidance Revision',        -- Update to existing guidance
    'Guidance Withdrawal',      -- Withdrawal of guidance
    'Regulatory Notice',        -- Formal regulatory notice
    'Advisory',                 -- Non-binding advisory
    'Policy Update',            -- Policy change announcement
    'Inspection Alert',         -- Inspection-related notice
    'Safety Alert',             -- Safety-related announcement
    'Compliance Update',        -- Compliance requirement changes
    'Fee Update',               -- Fee schedule changes
    'Form Update',              -- Form/template changes
    'System Update',            -- eSubmission system changes
    'General Announcement',     -- General regulatory news
    'Q&A Update',               -- Q&A document updates
    'Technical Clarification'   -- Clarification of existing requirements
  )),
  status VARCHAR(50) DEFAULT 'Active' CHECK (status IN (
    'Active',           -- Currently relevant
    'Superseded',       -- Replaced by newer announcement
    'Expired',          -- Past deadline/no longer relevant
    'Withdrawn',        -- Formally withdrawn
    'Pending'           -- Future effective date
  )),
  
  -- Geographic/regulatory reference
  geography_id INTEGER REFERENCES drug.regulatory_geography(id) ON DELETE SET NULL,  -- Links to region/country/agency
  
  -- Impact severity
  impact_level VARCHAR(20) DEFAULT 'Medium' CHECK (impact_level IN (
    'Critical',    -- Immediate action required
    'High',        -- Significant impact, action needed soon
    'Medium',      -- Moderate impact, plan for changes
    'Low',         -- Minor impact, informational
    'Informational' -- No direct action needed
  )),
  
  -- Full text content
  announcement_text TEXT,
  
  -- ============================================================================
  -- IMPACT ASSESSMENT (Inlined from separate announcement_impact table)
  -- ============================================================================
  
  -- Assessment summary
  impact_assessment_title VARCHAR(500),
  impact_assessment_summary TEXT,
  
  -- Impact categorization
  impact_scope VARCHAR(50) CHECK (impact_scope IN (
    'Global',           -- Affects all markets
    'Regional',         -- Affects specific region
    'Country-Specific', -- Affects single country
    'Product-Specific', -- Affects specific product types
    'Company-Specific'  -- Affects specific company actions
  )),
  
  -- Timeline actions
  immediate_actions TEXT,          -- What needs to happen immediately
  short_term_actions TEXT,         -- Actions within 30-90 days
  long_term_actions TEXT,          -- Actions beyond 90 days
  
  -- Business impact areas
  regulatory_impact TEXT,          -- Impact on regulatory submissions
  manufacturing_impact TEXT,       -- Impact on manufacturing/GMP
  labeling_impact TEXT,            -- Impact on labeling/artwork
  clinical_impact TEXT,            -- Impact on clinical programs
  commercial_impact TEXT,          -- Impact on commercial operations
  
  -- Analysis
  current_state TEXT,              -- Current regulatory landscape
  future_state TEXT,               -- Expected future state after changes
  gap_analysis TEXT,               -- Gap between current and future state
  
  -- Recommendations
  impact_recommendations TEXT,
  risk_mitigation TEXT,
  
  -- Assessment status
  assessment_status VARCHAR(50) DEFAULT 'Draft' CHECK (assessment_status IN (
    'Draft', 'Under Review', 'Approved', 'Published'
  )),
  assessed_by INTEGER,             -- User who performed assessment
  assessed_at TIMESTAMP,
  approved_by INTEGER,             -- User who approved assessment
  approved_at TIMESTAMP,
  
  -- Impacted functions stored as JSONB array (replaces announcement_impact_function table)
  -- Structure: [{"function_id": 1, "function_name": "RA", "severity": "High", "description": "...", "action": "..."}]
  impacted_functions JSONB DEFAULT '[]'::jsonb,
  
  -- Metadata
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  created_by INTEGER
);

CREATE INDEX IF NOT EXISTS idx_regulatory_announcement_date ON drug.regulatory_announcement(announcement_date DESC);
CREATE INDEX IF NOT EXISTS idx_regulatory_announcement_type ON drug.regulatory_announcement(announcement_type);
CREATE INDEX IF NOT EXISTS idx_regulatory_announcement_status ON drug.regulatory_announcement(status);
CREATE INDEX IF NOT EXISTS idx_regulatory_announcement_geography ON drug.regulatory_announcement(geography_id);
CREATE INDEX IF NOT EXISTS idx_regulatory_announcement_impact ON drug.regulatory_announcement(impact_level);
CREATE INDEX IF NOT EXISTS idx_regulatory_announcement_active ON drug.regulatory_announcement(is_active);
CREATE INDEX IF NOT EXISTS idx_regulatory_announcement_effective ON drug.regulatory_announcement(effective_date);
CREATE INDEX IF NOT EXISTS idx_regulatory_announcement_deadline ON drug.regulatory_announcement(comment_deadline);

-- Full-text search for announcements
CREATE INDEX IF NOT EXISTS idx_regulatory_announcement_title_search ON drug.regulatory_announcement USING gin(to_tsvector('english', title));
CREATE INDEX IF NOT EXISTS idx_regulatory_announcement_text_search ON drug.regulatory_announcement USING gin(to_tsvector('english', COALESCE(announcement_text, '')));

-- Impact assessment indexes
CREATE INDEX IF NOT EXISTS idx_regulatory_announcement_impact_scope ON drug.regulatory_announcement(impact_scope);
CREATE INDEX IF NOT EXISTS idx_regulatory_announcement_assessment_status ON drug.regulatory_announcement(assessment_status);
CREATE INDEX IF NOT EXISTS idx_regulatory_announcement_impacted_functions ON drug.regulatory_announcement USING GIN(impacted_functions);

-- ============================================================================
-- 4.8 ANNOUNCEMENT RELATIONSHIPS
-- Link announcements to guidelines, pathways, categories, and product types
-- ============================================================================

-- Announcement-Guideline link (which guidelines does this announcement affect?)
CREATE TABLE IF NOT EXISTS drug.announcement_guideline (
  id SERIAL PRIMARY KEY,
  announcement_id INTEGER NOT NULL REFERENCES drug.regulatory_announcement(id) ON DELETE CASCADE,
  guideline_id INTEGER NOT NULL REFERENCES drug.drug_guideline(id) ON DELETE CASCADE,
  relationship_type VARCHAR(50) DEFAULT 'affects' CHECK (relationship_type IN (
    'affects',          -- Announcement affects this guideline
    'supersedes',       -- Announcement supersedes this guideline
    'supplements',      -- Announcement supplements/adds to guideline
    'clarifies',        -- Announcement clarifies guideline interpretation
    'references',       -- Announcement references this guideline
    'implements'        -- Announcement implements requirements from guideline
  )),
  notes TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(announcement_id, guideline_id, relationship_type)
);

CREATE INDEX IF NOT EXISTS idx_announcement_guideline_announcement ON drug.announcement_guideline(announcement_id);
CREATE INDEX IF NOT EXISTS idx_announcement_guideline_guideline ON drug.announcement_guideline(guideline_id);
CREATE INDEX IF NOT EXISTS idx_announcement_guideline_type ON drug.announcement_guideline(relationship_type);

-- ============================================================================
-- NOTE: Junction tables with evidence/confidence for LLM tagging
-- ============================================================================
-- announcement_pathway, announcement_classification, announcement_key_topic,
-- announcement_guideline_category, announcement_product_type, announcement_function
-- are defined in the ANNOUNCEMENT SCHEMA EXTENSIONS section below.
-- 
-- KEPT: announcement_guideline (has relationship_type field)
-- ============================================================================

-- ============================================================================
-- NOTE: Impact Assessment tables REMOVED and INLINED into regulatory_announcement
-- ============================================================================
-- The following tables were removed:
-- - announcement_impact → columns now in regulatory_announcement
-- - announcement_impact_function → replaced by impacted_functions JSONB column
-- ============================================================================

-- ============================================================================
-- 5. PREDICATE ANALYSIS
-- ============================================================================

-- Drug predicate assessments
CREATE TABLE IF NOT EXISTS drug.drug_predicate_assessments (
  id SERIAL PRIMARY KEY,
  ingredient_name VARCHAR(1000),
  product_name VARCHAR(255),
  geography_id INTEGER REFERENCES drug.regulatory_geography(id) ON DELETE SET NULL,  -- Country of origin
  approval_date DATE,
  end_date DATE,
  application_type VARCHAR(100),
  classification VARCHAR(100),
  registration_number VARCHAR(100),
  registration_holder VARCHAR(255),
  manufacturer VARCHAR(255),
  importer VARCHAR(255),
  generic_name VARCHAR(255),
  reference_drug VARCHAR(255),
  dosage_form VARCHAR(255),
  strength VARCHAR(1000),
  route_administration VARCHAR(255),
  indication TEXT,
  therapy_area VARCHAR(255),
  other_trade_name VARCHAR(255),
  patent_information TEXT,
  distributor VARCHAR(255),
  marketing_status VARCHAR(100),
  submission_type VARCHAR(100),
  submission_number VARCHAR(100),
  submission_date DATE,
  json_data JSONB,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  created_by INTEGER
);

CREATE INDEX IF NOT EXISTS idx_drug_predicate_product_name ON drug.drug_predicate_assessments(product_name);
CREATE INDEX IF NOT EXISTS idx_drug_predicate_geography ON drug.drug_predicate_assessments(geography_id);
CREATE INDEX IF NOT EXISTS idx_drug_predicate_approval_date ON drug.drug_predicate_assessments(approval_date);
CREATE INDEX IF NOT EXISTS idx_drug_predicate_classification ON drug.drug_predicate_assessments(classification);
CREATE INDEX IF NOT EXISTS idx_drug_predicate_reg_holder ON drug.drug_predicate_assessments(registration_holder);
CREATE INDEX IF NOT EXISTS idx_drug_predicate_manufacturer ON drug.drug_predicate_assessments(manufacturer);
CREATE INDEX IF NOT EXISTS idx_drug_predicate_generic_name ON drug.drug_predicate_assessments(generic_name);
CREATE INDEX IF NOT EXISTS idx_drug_predicate_therapy_area ON drug.drug_predicate_assessments(therapy_area);

-- Saved searches for predicates
CREATE TABLE IF NOT EXISTS drug.drug_predicate_saved_searches (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  search_query TEXT,
  filters JSONB,
  user_id INTEGER, -- References public.users but not enforced
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_drug_predicate_saved_searches_user ON drug.drug_predicate_saved_searches(user_id);
CREATE INDEX IF NOT EXISTS idx_drug_predicate_saved_searches_created ON drug.drug_predicate_saved_searches(created_at DESC);

-- Predicate analyses
CREATE TABLE IF NOT EXISTS drug.drug_predicate_analyses (
  id SERIAL PRIMARY KEY,
  search_id INTEGER REFERENCES drug.drug_predicate_saved_searches(id) ON DELETE SET NULL,
  analysis_text TEXT,
  user_id INTEGER, -- References public.users but not enforced
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_drug_predicate_analyses_search ON drug.drug_predicate_analyses(search_id);
CREATE INDEX IF NOT EXISTS idx_drug_predicate_analyses_user ON drug.drug_predicate_analyses(user_id);
CREATE INDEX IF NOT EXISTS idx_drug_predicate_analyses_created ON drug.drug_predicate_analyses(created_at DESC);

-- ============================================================================
-- 6. VERSIONING SYSTEM
-- ============================================================================

-- Guideline relation types (supersedes, updates, revises, etc.)
CREATE TABLE IF NOT EXISTS drug.drug_guideline_relation_type (
  id SERIAL PRIMARY KEY,
  name VARCHAR(100) NOT NULL UNIQUE,
  description TEXT,
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  created_by INTEGER -- References public.users but not enforced
);

CREATE INDEX IF NOT EXISTS idx_drug_guideline_relation_type_name ON drug.drug_guideline_relation_type(name);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_relation_type_is_active ON drug.drug_guideline_relation_type(is_active);

-- Guideline links (relationships between guidelines)
CREATE TABLE IF NOT EXISTS drug.drug_guideline_link (
  id SERIAL PRIMARY KEY,
  from_guideline_id INTEGER NOT NULL REFERENCES drug.drug_guideline(id) ON DELETE CASCADE,
  to_guideline_id INTEGER NOT NULL REFERENCES drug.drug_guideline(id) ON DELETE CASCADE,
  relation_type_id INTEGER NOT NULL REFERENCES drug.drug_guideline_relation_type(id) ON DELETE RESTRICT,
  notes TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  created_by INTEGER,
  
  UNIQUE(from_guideline_id, to_guideline_id, relation_type_id),
  CONSTRAINT drug_guideline_link_no_self_ref CHECK (from_guideline_id != to_guideline_id)
);

CREATE INDEX IF NOT EXISTS idx_drug_guideline_link_from ON drug.drug_guideline_link(from_guideline_id);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_link_to ON drug.drug_guideline_link(to_guideline_id);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_link_type ON drug.drug_guideline_link(relation_type_id);

-- Guideline versions (with JSONB snapshot for relationships)
CREATE TABLE IF NOT EXISTS drug.drug_guideline_version (
  id SERIAL PRIMARY KEY,
  guideline_id INTEGER REFERENCES drug.drug_guideline(id) ON DELETE CASCADE,
  version_number INTEGER NOT NULL,
  title VARCHAR(255) NOT NULL,
  description TEXT,
  document_url VARCHAR(512),
  published_date DATE,
  effective_date DATE,
  deadline_date DATE,
  guideline_type_id INTEGER REFERENCES drug.reference_data(id) ON DELETE SET NULL,  -- ref_type='guideline_type'
  geography_id INTEGER,  -- Snapshot of geography at version time
  status VARCHAR(100) DEFAULT 'Published',
  is_active BOOLEAN DEFAULT TRUE,
  
  -- Version metadata
  version_created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  version_created_by INTEGER,
  change_summary TEXT,
  change_reason VARCHAR(255),
  is_current_version BOOLEAN DEFAULT FALSE,
  
  -- JSONB snapshot of all relationships at this version
  -- Replaces: version_classification, version_key_topic, version_function tables
  relationships_snapshot JSONB DEFAULT '{}'::jsonb,
  -- Structure: {
  --   "classifications": [{"id": 1, "name": "...", "code": "..."}],
  --   "key_topics": [{"id": 1, "name": "...", "relevance_score": 5}],
  --   "functions": [{"id": 1, "name": "...", "impact_level": "High"}],
  --   "product_types": [{"id": 1, "name": "...", "code": "..."}],
  --   "pathways": [{"id": 1, "name": "...", "code": "..."}],
  --   "categories": [{"id": 1, "name": "...", "code": "..."}]
  -- }
  
  -- Full guideline snapshot
  guideline_snapshot JSONB,  -- Complete copy of guideline data at this version
  
  UNIQUE(guideline_id, version_number)
);

CREATE INDEX IF NOT EXISTS idx_drug_guideline_version_guideline_id ON drug.drug_guideline_version(guideline_id);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_version_current ON drug.drug_guideline_version(guideline_id, is_current_version);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_version_created_at ON drug.drug_guideline_version(version_created_at DESC);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_version_snapshot ON drug.drug_guideline_version USING GIN(relationships_snapshot);

-- NOTE: The following junction tables were REMOVED (replaced by relationships_snapshot JSONB):
-- - drug_guideline_version_classification
-- - drug_guideline_version_key_topic
-- - drug_guideline_version_function

-- ============================================================================
-- 7. DRUG-SPECIFIC FEATURES
-- ============================================================================

-- Import logs (moved from public, now drug-specific)
CREATE TABLE IF NOT EXISTS drug.drug_import_log (
  id SERIAL PRIMARY KEY,
  file_hash VARCHAR(32) NOT NULL,
  file_name VARCHAR(255) NOT NULL,
  total_rows INTEGER DEFAULT 0,
  imported_count INTEGER DEFAULT 0,
  skipped_count INTEGER DEFAULT 0,
  import_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'failed')),
  error_message TEXT,
  user_id INTEGER, -- References public.users but not enforced
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_drug_import_log_file_hash ON drug.drug_import_log(file_hash);
CREATE INDEX IF NOT EXISTS idx_drug_import_log_import_time ON drug.drug_import_log(import_time DESC);
CREATE INDEX IF NOT EXISTS idx_drug_import_log_status ON drug.drug_import_log(status);
CREATE INDEX IF NOT EXISTS idx_drug_import_log_user ON drug.drug_import_log(user_id);

-- Therapy areas
CREATE TABLE IF NOT EXISTS drug.drug_therapy_areas (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255) UNIQUE NOT NULL,
  description TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_drug_therapy_areas_name ON drug.drug_therapy_areas(name);

-- Newsletter guideline changes tracking (moved from public)
CREATE TABLE IF NOT EXISTS drug.drug_newsletter_guideline_changes (
  id SERIAL PRIMARY KEY,
  guideline_id INTEGER NOT NULL REFERENCES drug.drug_guideline(id) ON DELETE CASCADE,
  change_type VARCHAR(50) NOT NULL CHECK (change_type IN ('created', 'updated', 'superseded', 'deleted', 'status_changed')),
  
  -- Change details
  changed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
  old_values JSONB DEFAULT '{}',
  new_values JSONB DEFAULT '{}',
  change_summary TEXT,
  
  -- Guideline snapshot for newsletters
  title VARCHAR(255),
  description TEXT,
  published_date DATE,
  effective_date DATE,
  geography_id INTEGER REFERENCES drug.regulatory_geography(id) ON DELETE SET NULL,  -- Country/agency reference
  guideline_type VARCHAR(255),
  
  -- Processing status
  processed_for_newsletters BOOLEAN DEFAULT FALSE,
  processed_at TIMESTAMP WITH TIME ZONE,
  
  -- Metadata
  created_by INTEGER -- References public.users but not enforced
);

CREATE INDEX IF NOT EXISTS idx_drug_newsletter_changes_guideline ON drug.drug_newsletter_guideline_changes(guideline_id);
CREATE INDEX IF NOT EXISTS idx_drug_newsletter_changes_changed_at ON drug.drug_newsletter_guideline_changes(changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_drug_newsletter_changes_processed ON drug.drug_newsletter_guideline_changes(processed_for_newsletters);
CREATE INDEX IF NOT EXISTS idx_drug_newsletter_changes_change_type ON drug.drug_newsletter_guideline_changes(change_type);

-- ============================================================================
-- COMMENTS FOR DOCUMENTATION
-- ============================================================================

COMMENT ON SCHEMA drug IS 'Domain-specific drug regulatory data maintained by Artixio';

COMMENT ON TABLE drug.regulatory_geography IS 'Denormalized table: Region → Country → Agency (one row per country, each country has one agency for drugs/biologics)';
COMMENT ON COLUMN drug.regulatory_geography.region_name IS 'Geographic region (North America, Europe, Asia Pacific, Latin America)';
COMMENT ON COLUMN drug.regulatory_geography.country_code IS 'ISO country code (US, EU, UK, JP, etc.)';
COMMENT ON COLUMN drug.regulatory_geography.agency_acronym IS 'Regulatory agency acronym (FDA, EMA, PMDA, etc.)';

COMMENT ON TABLE drug.reference_data IS 'Unified reference/lookup table for all types: guideline_type, function, key_topic, classification, product';
COMMENT ON COLUMN drug.reference_data.ref_type IS 'Type discriminator: guideline_type, function, key_topic, classification, product';
COMMENT ON COLUMN drug.reference_data.metadata IS 'JSONB for flexible type-specific extra fields';

COMMENT ON TABLE drug.drug_guideline IS 'Main drug guidelines table with comprehensive metadata and impact assessment';
COMMENT ON COLUMN drug.drug_guideline.impact_assessment IS 'Whether this guideline requires impact assessment (Yes/No)';
COMMENT ON COLUMN drug.drug_guideline.functional_area IS 'Array of functional areas this guideline applies to';
COMMENT ON COLUMN drug.drug_guideline.product_category IS 'Array of product categories this guideline applies to';

COMMENT ON TABLE drug.drug_guideline_relation_type IS 'Types of relationships between guidelines (supersedes, updates, etc.)';
COMMENT ON TABLE drug.drug_guideline_link IS 'Relationships between guidelines (newer to older)';
COMMENT ON COLUMN drug.drug_guideline_link.from_guideline_id IS 'The newer guideline';
COMMENT ON COLUMN drug.drug_guideline_link.to_guideline_id IS 'The older guideline being updated/superseded';

COMMENT ON TABLE drug.drug_guideline_version IS 'Version history for guidelines';
COMMENT ON TABLE drug.drug_predicate_assessments IS 'Predicate drug product analysis data';

COMMENT ON TABLE drug.drug_import_log IS 'Tracks drug guideline imports to prevent duplicates';
COMMENT ON TABLE drug.drug_therapy_areas IS 'Therapy areas for drug regulatory guidelines';
COMMENT ON TABLE drug.drug_newsletter_guideline_changes IS 'Tracks guideline changes for newsletter generation';

-- Regulatory Hierarchy System
COMMENT ON TABLE drug.product_type IS 'Product types (Drugs, Biologics, Cell Therapy, Gene Therapy, Vaccines, Biosimilars, etc.)';
COMMENT ON TABLE drug.regulatory_pathway IS 'Agency-specific regulatory pathways (NDA, ANDA, BLA for FDA; MAA for EMA; NDS for Health Canada)';
COMMENT ON TABLE drug.guideline_category IS 'Hierarchical guideline categories (CMC, GMP, Clinical, Labeling, etc. with sub-categories)';
COMMENT ON COLUMN drug.guideline_category.parent_id IS 'Self-reference for hierarchy: NULL=main category, set=sub-category';
COMMENT ON COLUMN drug.guideline_category.level IS '1=main category, 2=sub-category, 3+=deeper nesting';

COMMENT ON TABLE drug.drug_guideline_product_type IS 'Junction: guidelines to product types (many-to-many)';
COMMENT ON TABLE drug.drug_guideline_pathway IS 'Junction: guidelines to regulatory pathways (many-to-many)';
COMMENT ON TABLE drug.drug_guideline_guideline_category IS 'Junction: guidelines to guideline categories (many-to-many)';

-- Pathway-Category-ProductType Relationships
COMMENT ON TABLE drug.pathway_category IS 'Defines which technical categories (CMC, GMP, Clinical) apply to each regulatory pathway';
COMMENT ON COLUMN drug.pathway_category.is_mandatory IS 'Whether this category is mandatory for submissions via this pathway';
COMMENT ON TABLE drug.pathway_product_type IS 'Defines which product types (Drugs, Biologics) can use which pathways (NDA, BLA)';
COMMENT ON COLUMN drug.pathway_product_type.is_primary IS 'Whether this is the primary/default pathway for this product type';

-- Regulatory Announcements
COMMENT ON TABLE drug.regulatory_announcement IS 'Regulatory updates, notices, draft guidances - the dynamic intelligence layer';
COMMENT ON COLUMN drug.regulatory_announcement.announcement_type IS 'Type of announcement: Draft Guidance, Final Guidance, Notice, Alert, etc.';
COMMENT ON COLUMN drug.regulatory_announcement.impact_level IS 'Severity of impact: Critical, High, Medium, Low, Informational';
COMMENT ON COLUMN drug.regulatory_announcement.comment_deadline IS 'For draft guidances - deadline for submitting comments';

-- Announcement Relationships
COMMENT ON TABLE drug.announcement_guideline IS 'Links announcements to the guidelines they affect, supersede, or clarify';

-- Impact Assessments (now inlined into regulatory_announcement)
COMMENT ON COLUMN drug.regulatory_announcement.impact_scope IS 'Scope: Global, Regional, Country-Specific, Product-Specific';
COMMENT ON COLUMN drug.regulatory_announcement.impacted_functions IS 'JSONB array of impacted functions with severity and actions';


CREATE TABLE IF NOT EXISTS drug.guideline_chunks (
  id SERIAL PRIMARY KEY,
  guideline_id INTEGER NOT NULL
    REFERENCES drug.drug_guideline(id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL,
  chunk_text TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_guideline_chunks_guideline_chunk
ON drug.guideline_chunks (guideline_id, chunk_index);

-- Full-text search: BM25/ts_rank retrieval (runtimeFacetDiscovery.js, regulatoryRAGService.js).
-- A STORED generated column (not a GIN index on the raw to_tsvector(chunk_text) expression) —
-- an expression index still forces Postgres's "lossy recheck" to re-tokenize chunk_text from
-- scratch for every phrase-query candidate. Querying the stored column directly measured 36x
-- faster (10.45s -> 0.29s) for phrase queries. ADD COLUMN backfills existing rows, which can take
-- a while on a large table.
ALTER TABLE drug.guideline_chunks
ADD COLUMN IF NOT EXISTS chunk_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', chunk_text)) STORED;

CREATE INDEX IF NOT EXISTS idx_guideline_chunks_text_search
ON drug.guideline_chunks USING gin(chunk_tsv);


CREATE TABLE IF NOT EXISTS drug.guideline_embeddings (
  id SERIAL PRIMARY KEY,
  chunk_id INTEGER NOT NULL
    REFERENCES drug.guideline_chunks(id) ON DELETE CASCADE,
  embedding VECTOR(1024) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_guideline_chunks_guideline_id
ON drug.guideline_chunks (guideline_id);


CREATE INDEX IF NOT EXISTS idx_guideline_embeddings_hnsw 
ON drug.guideline_embeddings 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Index on chunk_id for fast joins
CREATE INDEX IF NOT EXISTS idx_guideline_embeddings_chunk_id
ON drug.guideline_embeddings (chunk_id);



ALTER TABLE drug.guideline_category 
ADD COLUMN synonyms TEXT[];


ALTER TABLE drug.regulatory_pathway 
ADD COLUMN synonyms TEXT[];


ALTER TABLE drug.product_type
ADD COLUMN synonyms TEXT[];


ALTER TABLE drug.reference_data
ADD COLUMN synonyms TEXT[];


ALTER TABLE drug.drug_guideline_pathway
ADD COLUMN evidence TEXT,
ADD COLUMN confidence_score VARCHAR(255);


ALTER TABLE drug.drug_guideline_classification
ADD COLUMN evidence TEXT,
ADD COLUMN confidence_score VARCHAR(255)
; 



ALTER TABLE drug.drug_guideline_key_topic
ADD COLUMN evidence TEXT,
ADD COLUMN confidence_score VARCHAR(255);


ALTER TABLE drug.drug_guideline_function
ADD COLUMN evidence TEXT,
ADD COLUMN confidence_score VARCHAR(255)
;

ALTER TABLE drug.drug_guideline_product_type
ADD COLUMN evidence TEXT,
ADD COLUMN confidence_score VARCHAR(255)
;

ALTER TABLE drug.drug_guideline_guideline_category
ADD COLUMN evidence TEXT,
ADD COLUMN confidence_score VARCHAR(255)
;


ALTER TABLE drug.drug_guideline
ALTER COLUMN published_date DROP NOT NULL;


-- ============================================================================
-- ANNOUNCEMENT SCHEMA EXTENSIONS
-- Additional tables for processing announcements like guidelines
-- ============================================================================

-- ============================================================================
-- 1. ANNOUNCEMENT CHUNKS (for LLM processing)
-- ============================================================================

CREATE TABLE IF NOT EXISTS drug.announcement_chunks (
  id SERIAL PRIMARY KEY,
  announcement_id INTEGER NOT NULL
    REFERENCES drug.regulatory_announcement(id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL,
  chunk_text TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_announcement_chunks_announcement_chunk
ON drug.announcement_chunks (announcement_id, chunk_index);

CREATE INDEX IF NOT EXISTS idx_announcement_chunks_announcement_id
ON drug.announcement_chunks (announcement_id);

-- Full-text search: BM25/ts_rank retrieval (runtimeFacetDiscovery.js, regulatoryRAGService.js) —
-- same stored-column rationale as guideline_chunks.chunk_tsv above. On a large announcement_chunks
-- table this ADD COLUMN backfill is a heavy, long-running operation; run it separately in prod
-- ahead of deploying any code that queries ac.chunk_tsv.
ALTER TABLE drug.announcement_chunks
ADD COLUMN IF NOT EXISTS chunk_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', chunk_text)) STORED;

CREATE INDEX IF NOT EXISTS idx_announcement_chunks_text_search
ON drug.announcement_chunks USING gin(chunk_tsv);

-- ============================================================================
-- 2. ANNOUNCEMENT EMBEDDINGS (for semantic search)
-- ============================================================================

CREATE TABLE IF NOT EXISTS drug.announcement_embeddings (
  id SERIAL PRIMARY KEY,
  chunk_id INTEGER NOT NULL
    REFERENCES drug.announcement_chunks(id) ON DELETE CASCADE,
  embedding VECTOR(1024) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_announcement_embeddings_hnsw 
ON drug.announcement_embeddings 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_announcement_embeddings_chunk_id
ON drug.announcement_embeddings (chunk_id);

-- ============================================================================
-- 3. JUNCTION TABLES WITH EVIDENCE/CONFIDENCE
-- ============================================================================

-- Announcement-Pathway relationships
CREATE TABLE IF NOT EXISTS drug.announcement_pathway (
  id SERIAL PRIMARY KEY,
  announcement_id INTEGER NOT NULL REFERENCES drug.regulatory_announcement(id) ON DELETE CASCADE,
  pathway_id INTEGER NOT NULL REFERENCES drug.regulatory_pathway(id) ON DELETE CASCADE,
  evidence TEXT,
  confidence_score VARCHAR(255),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(announcement_id, pathway_id)
);

CREATE INDEX IF NOT EXISTS idx_announcement_pathway_announcement ON drug.announcement_pathway(announcement_id);
CREATE INDEX IF NOT EXISTS idx_announcement_pathway_pathway ON drug.announcement_pathway(pathway_id);

-- Announcement-Classification relationships (ref_type='classification')
CREATE TABLE IF NOT EXISTS drug.announcement_classification (
  id SERIAL PRIMARY KEY,
  announcement_id INTEGER NOT NULL REFERENCES drug.regulatory_announcement(id) ON DELETE CASCADE,
  reference_id INTEGER NOT NULL REFERENCES drug.reference_data(id) ON DELETE CASCADE,
  evidence TEXT,
  confidence_score VARCHAR(255),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(announcement_id, reference_id)
);

CREATE INDEX IF NOT EXISTS idx_announcement_classification_announcement ON drug.announcement_classification(announcement_id);
CREATE INDEX IF NOT EXISTS idx_announcement_classification_ref ON drug.announcement_classification(reference_id);

-- Announcement-Key Topic relationships (ref_type='key_topic')
CREATE TABLE IF NOT EXISTS drug.announcement_key_topic (
  id SERIAL PRIMARY KEY,
  announcement_id INTEGER NOT NULL REFERENCES drug.regulatory_announcement(id) ON DELETE CASCADE,
  reference_id INTEGER NOT NULL REFERENCES drug.reference_data(id) ON DELETE CASCADE,
  evidence TEXT,
  confidence_score VARCHAR(255),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(announcement_id, reference_id)
);

CREATE INDEX IF NOT EXISTS idx_announcement_key_topic_announcement ON drug.announcement_key_topic(announcement_id);
CREATE INDEX IF NOT EXISTS idx_announcement_key_topic_ref ON drug.announcement_key_topic(reference_id);

-- Announcement-Category relationships
CREATE TABLE IF NOT EXISTS drug.announcement_guideline_category (
  id SERIAL PRIMARY KEY,
  announcement_id INTEGER NOT NULL REFERENCES drug.regulatory_announcement(id) ON DELETE CASCADE,
  category_id INTEGER NOT NULL REFERENCES drug.guideline_category(id) ON DELETE CASCADE,
  evidence TEXT,
  confidence_score VARCHAR(255),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(announcement_id, category_id)
);

CREATE INDEX IF NOT EXISTS idx_announcement_guideline_category_announcement ON drug.announcement_guideline_category(announcement_id);
CREATE INDEX IF NOT EXISTS idx_announcement_guideline_category_category ON drug.announcement_guideline_category(category_id);

-- Announcement-Product Type relationships
CREATE TABLE IF NOT EXISTS drug.announcement_product_type (
  id SERIAL PRIMARY KEY,
  announcement_id INTEGER NOT NULL REFERENCES drug.regulatory_announcement(id) ON DELETE CASCADE,
  product_type_id INTEGER NOT NULL REFERENCES drug.product_type(id) ON DELETE CASCADE,
  evidence TEXT,
  confidence_score VARCHAR(255),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(announcement_id, product_type_id)
);

CREATE INDEX IF NOT EXISTS idx_announcement_product_type_announcement ON drug.announcement_product_type(announcement_id);
CREATE INDEX IF NOT EXISTS idx_announcement_product_type_product_type ON drug.announcement_product_type(product_type_id);

-- Announcement-Function relationships (ref_type='function')
CREATE TABLE IF NOT EXISTS drug.announcement_function (
  id SERIAL PRIMARY KEY,
  announcement_id INTEGER NOT NULL REFERENCES drug.regulatory_announcement(id) ON DELETE CASCADE,
  reference_id INTEGER NOT NULL REFERENCES drug.reference_data(id) ON DELETE CASCADE,
  evidence TEXT,
  confidence_score VARCHAR(255),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(announcement_id, reference_id)
);

CREATE INDEX IF NOT EXISTS idx_announcement_function_announcement ON drug.announcement_function(announcement_id);
CREATE INDEX IF NOT EXISTS idx_announcement_function_ref ON drug.announcement_function(reference_id);

-- ============================================================================
-- COMMENTS
-- ============================================================================

COMMENT ON TABLE drug.announcement_chunks IS 'Text chunks from announcements for LLM processing';
COMMENT ON TABLE drug.announcement_embeddings IS 'Vector embeddings for semantic search on announcement chunks';
COMMENT ON TABLE drug.announcement_pathway IS 'Junction: announcements to regulatory pathways with evidence';
COMMENT ON TABLE drug.announcement_classification IS 'Junction: announcements to classifications with evidence';
COMMENT ON TABLE drug.announcement_key_topic IS 'Junction: announcements to key topics with evidence';
COMMENT ON TABLE drug.announcement_guideline_category IS 'Junction: announcements to guideline categories with evidence';
COMMENT ON TABLE drug.announcement_product_type IS 'Junction: announcements to product types with evidence';
COMMENT ON TABLE drug.announcement_function IS 'Junction: announcements to business functions with evidence';


-- ============================================================================
-- PROCESSING STATUS TRACKING
-- Tracks document processing state to avoid reprocessing
-- ============================================================================
-- 
-- Status Codes:
--   0 = UNPROCESSED     - Default, needs processing
--   1 = PROCESSED       - Successfully processed (even if no matches found)
--   2 = DOWNLOAD_FAILED - Could not download document from S3
--   3 = UNSUPPORTED_FORMAT - File format not PDF/DOCX/DOC
--   4 = INSUFFICIENT_TEXT - Text extraction yielded less than 50 chars
--   5 = LOW_OCR_CONFIDENCE - OCR confidence below threshold (40%)
--   6 = DB_CONNECTION_FAILED - Could not get database connection
--   7 = CHUNK_CREATION_FAILED - Error creating/storing text chunks
--   8 = PROCESSING_ERROR - General exception during processing
--
-- ============================================================================

-- Add processing_status to drug_guideline
ALTER TABLE drug.drug_guideline
ADD COLUMN IF NOT EXISTS processing_status SMALLINT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_drug_guideline_processing_status 
ON drug.drug_guideline(processing_status);

-- Add processing_status to regulatory_announcement
ALTER TABLE drug.regulatory_announcement
ADD COLUMN IF NOT EXISTS processing_status SMALLINT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_regulatory_announcement_processing_status 
ON drug.regulatory_announcement(processing_status);

COMMENT ON COLUMN drug.drug_guideline.processing_status IS 
'Processing status: 0=unprocessed, 1=processed, 2=download_failed, 3=unsupported_format, 4=insufficient_text, 5=low_ocr_confidence, 6=db_connection_failed, 7=chunk_creation_failed, 8=processing_error';

COMMENT ON COLUMN drug.regulatory_announcement.processing_status IS 
'Processing status: 0=unprocessed, 1=processed, 2=download_failed, 3=unsupported_format, 4=insufficient_text, 5=low_ocr_confidence, 6=db_connection_failed, 7=chunk_creation_failed, 8=processing_error';



ALTER TABLE drug.drug_guideline
ADD COLUMN json_data JSONB;
 
ALTER TABLE drug.regulatory_announcement
ADD COLUMN json_data JSONB;


-- ============================================================================
-- ADD DOMAIN COLUMN
-- Run this migration to add domain classification to guidelines/announcements
-- ============================================================================
-- 
-- Domain Values:
--   Drugs, Biologics, Vaccines, Biosimilars, Cell Therapy, Gene Therapy,
--   Medical Devices, Combination Products, Dietary Supplements, Food,
--   Cosmetics, OTC Products
--
-- ============================================================================

-- Add domain column to drug_guideline
ALTER TABLE drug.drug_guideline
ADD COLUMN IF NOT EXISTS domain TEXT[];

-- Add domain column to regulatory_announcement
ALTER TABLE drug.regulatory_announcement
ADD COLUMN IF NOT EXISTS domain TEXT[];

-- Add GIN indexes for array queries (efficient for @> and && operators)
CREATE INDEX IF NOT EXISTS idx_drug_guideline_domain 
ON drug.drug_guideline USING GIN(domain);

CREATE INDEX IF NOT EXISTS idx_regulatory_announcement_domain 
ON drug.regulatory_announcement USING GIN(domain);

-- Documentation
COMMENT ON COLUMN drug.drug_guideline.domain IS 
'Regulatory domains: Drugs, Biologics, Vaccines, Biosimilars, Cell Therapy, Gene Therapy, Medical Devices, Combination Products, Dietary Supplements, Food, Cosmetics, OTC Products';

COMMENT ON COLUMN drug.regulatory_announcement.domain IS 
'Regulatory domains: Drugs, Biologics, Vaccines, Biosimilars, Cell Therapy, Gene Therapy, Medical Devices, Combination Products, Dietary Supplements, Food, Cosmetics, OTC Products';

ALTER TABLE drug.drug_guideline ALTER COLUMN document_url TYPE text[]
  USING string_to_array(document_url, ',');

ALTER TABLE drug.regulatory_announcement  ALTER COLUMN document_url TYPE text[]
  USING string_to_array(document_url, ',');


-- Add original_headline column to both tables
ALTER TABLE drug.drug_guideline
ADD COLUMN IF NOT EXISTS original_headline VARCHAR(500);

ALTER TABLE drug.regulatory_announcement
ADD COLUMN IF NOT EXISTS original_headline VARCHAR(500);

ALTER TABLE drug.drug_guideline
ADD COLUMN IF NOT EXISTS original_language VARCHAR(10);

ALTER TABLE drug.drug_guideline
ADD COLUMN IF NOT EXISTS translation_status VARCHAR(20) DEFAULT 'original_english';

-- Add translation columns to regulatory_announcement
ALTER TABLE drug.regulatory_announcement
ADD COLUMN IF NOT EXISTS original_text TEXT;

ALTER TABLE drug.regulatory_announcement
ADD COLUMN IF NOT EXISTS original_language VARCHAR(10);

ALTER TABLE drug.regulatory_announcement
ADD COLUMN IF NOT EXISTS translation_status VARCHAR(20) DEFAULT 'original_english';

-- Add check constraints for translation_status
-- Valid values: 'original_english', 'translated', 'translation_failed', 'skipped'
ALTER TABLE drug.drug_guideline
DROP CONSTRAINT IF EXISTS chk_guideline_translation_status;

ALTER TABLE drug.drug_guideline
ADD CONSTRAINT chk_guideline_translation_status 
CHECK (translation_status IS NULL OR translation_status IN (
  'original_english',    -- Document was originally in English
  'translated',          -- Document was translated to English
  'translation_failed',  -- Translation was attempted but failed
  'skipped'              -- Translation skipped (too short, low confidence, etc.)
));

ALTER TABLE drug.regulatory_announcement
DROP CONSTRAINT IF EXISTS chk_announcement_translation_status;

ALTER TABLE drug.regulatory_announcement
ADD CONSTRAINT chk_announcement_translation_status 
CHECK (translation_status IS NULL OR translation_status IN (
  'original_english',
  'translated',
  'translation_failed',
  'skipped'
));

-- Indexes for filtering by language/translation status
CREATE INDEX IF NOT EXISTS idx_drug_guideline_original_language 
ON drug.drug_guideline(original_language);

CREATE INDEX IF NOT EXISTS idx_drug_guideline_translation_status 
ON drug.drug_guideline(translation_status);

CREATE INDEX IF NOT EXISTS idx_regulatory_announcement_original_language 
ON drug.regulatory_announcement(original_language);

CREATE INDEX IF NOT EXISTS idx_regulatory_announcement_translation_status 
ON drug.regulatory_announcement(translation_status);

-- Documentation
COMMENT ON COLUMN drug.drug_guideline.original_document_text IS 
'Original document text before translation (NULL if originally English)';

ALTER TABLE drug.drug_guideline
ADD COLUMN IF NOT EXISTS original_document_text TEXT;



CREATE TABLE IF NOT EXISTS drug.guideline_translated (
  id SERIAL PRIMARY KEY,
  guideline_id INTEGER NOT NULL REFERENCES drug.drug_guideline(id) ON DELETE CASCADE,
  page_number INTEGER NOT NULL,
  json_data JSONB NOT NULL, -- Structure: {"original_text": "...", "translated_text": "..."}
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(guideline_id, page_number)
);

CREATE INDEX IF NOT EXISTS idx_guideline_translated_guideline ON drug.guideline_translated(guideline_id);
CREATE INDEX IF NOT EXISTS idx_guideline_translated_page ON drug.guideline_translated(page_number);

-- Index on chunk_id for fast joins
CREATE INDEX IF NOT EXISTS idx_guideline_embeddings_chunk_id
ON drug.guideline_embeddings (chunk_id);


CREATE TABLE IF NOT EXISTS drug.announcement_translated (
  id SERIAL PRIMARY KEY,
  announcement_id INTEGER NOT NULL REFERENCES drug.regulatory_announcement(id) ON DELETE CASCADE,
  page_number INTEGER NOT NULL,
  json_data JSONB NOT NULL, -- Structure: {"original_text": "...", "translated_text": "..."}
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(announcement_id, page_number)
);

CREATE INDEX IF NOT EXISTS idx_announcement_translated_announcement ON drug.announcement_translated(announcement_id);
CREATE INDEX IF NOT EXISTS idx_announcement_translated_page ON drug.announcement_translated(page_number);


-- ============================================================================
-- NOTE: REGULATORY INTELLIGENCE REPORTS moved to public schema
-- See generic_public_schema.sql for public.regulatory_report table
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_guideline_embeddings_cosine
ON drug.guideline_embeddings
USING hnsw (embedding vector_cosine_ops);


CREATE TABLE IF NOT EXISTS drug.app_notification (
  id SERIAL PRIMARY KEY,
  title VARCHAR(255) NOT NULL,
  message TEXT NOT NULL,
  notification_type VARCHAR(50) DEFAULT 'update' CHECK (notification_type IN ('update', 'feature', 'maintenance', 'alert', 'announcement')),
  action_url VARCHAR(500),
  action_label VARCHAR(100),
  priority VARCHAR(20) DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'critical')),
  scheduled_at TIMESTAMP WITH TIME ZONE,
  published_at TIMESTAMP WITH TIME ZONE,
  expires_at TIMESTAMP WITH TIME ZONE,
  status VARCHAR(20) DEFAULT 'draft' CHECK (status IN ('draft', 'scheduled', 'published', 'expired')),
  metadata JSONB DEFAULT '{}',
  created_by_email VARCHAR(255),
  created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
 
CREATE INDEX IF NOT EXISTS idx_drug_app_notification_status ON drug.app_notification(status);
CREATE INDEX IF NOT EXISTS idx_drug_app_notification_published ON drug.app_notification(published_at DESC) WHERE status = 'published';
CREATE INDEX IF NOT EXISTS idx_drug_app_notification_scheduled ON drug.app_notification(scheduled_at) WHERE status = 'scheduled';
 
-- Track which client instances have synced each notification
CREATE TABLE IF NOT EXISTS drug.notification_client_sync (
  id SERIAL PRIMARY KEY,
  notification_id INTEGER NOT NULL REFERENCES drug.app_notification(id) ON DELETE CASCADE,
  client_identifier VARCHAR(255) NOT NULL,
  synced_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
  users_notified INTEGER DEFAULT 0,
  UNIQUE(notification_id, client_identifier)
);
 
CREATE INDEX IF NOT EXISTS idx_notification_client_sync_notification ON drug.notification_client_sync(notification_id);
CREATE INDEX IF NOT EXISTS idx_notification_client_sync_client ON drug.notification_client_sync(client_identifier);
 
-- Comments
COMMENT ON TABLE drug.app_notification IS 'Central notification store - shared across all client applications';
COMMENT ON TABLE drug.notification_client_sync IS 'Tracks which client apps have distributed each notification to their users';
COMMENT ON COLUMN drug.app_notification.metadata IS 'Can contain: target_portals (array), target_orgs (array), etc.';



ALTER TABLE drug.reference_data
ADD COLUMN domains TEXT[];


CREATE TABLE IF NOT EXISTS drug.drug_guideline_therapeutic_area (
  id SERIAL PRIMARY KEY,

  guideline_id INTEGER
    REFERENCES drug.drug_guideline(id) ON DELETE CASCADE,

  reference_id INTEGER
    REFERENCES drug.reference_data(id) ON DELETE CASCADE,  
    -- ref_type = 'therapeutic_area'

  evidence TEXT,
  confidence_score VARCHAR(255),

  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  UNIQUE(guideline_id, reference_id)
);

CREATE INDEX IF NOT EXISTS idx_drug_guideline_therapeutic_area_guideline ON drug.drug_guideline_therapeutic_area(guideline_id);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_therapeutic_area_reference ON drug.drug_guideline_therapeutic_area(reference_id);



CREATE TABLE IF NOT EXISTS drug.drug_guideline_application_area (
  id SERIAL PRIMARY KEY,

  guideline_id INTEGER
    REFERENCES drug.drug_guideline(id) ON DELETE CASCADE,

  reference_id INTEGER
    REFERENCES drug.reference_data(id) ON DELETE CASCADE,  
    -- ref_type = 'application_area'

  evidence TEXT,
  confidence_score VARCHAR(255),

  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  UNIQUE(guideline_id, reference_id)
);

CREATE INDEX IF NOT EXISTS idx_drug_guideline_application_area_guideline ON drug.drug_guideline_application_area(guideline_id);
CREATE INDEX IF NOT EXISTS idx_drug_guideline_application_area_reference ON drug.drug_guideline_application_area(reference_id);



CREATE TABLE IF NOT EXISTS drug.announcement_therapeutic_area (
  id SERIAL PRIMARY KEY,

  announcement_id INTEGER NOT NULL
    REFERENCES drug.regulatory_announcement(id) ON DELETE CASCADE,

  reference_id INTEGER NOT NULL
    REFERENCES drug.reference_data(id) ON DELETE CASCADE,
    -- ref_type = 'therapeutic_area'

  evidence TEXT,
  confidence_score VARCHAR(255),

  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  UNIQUE(announcement_id, reference_id)
);

CREATE INDEX IF NOT EXISTS idx_announcement_therapeutic_area_announcement ON drug.announcement_therapeutic_area(announcement_id);
CREATE INDEX IF NOT EXISTS idx_announcement_therapeutic_area_reference ON drug.announcement_therapeutic_area(reference_id);


CREATE TABLE IF NOT EXISTS drug.announcement_application_area (
  id SERIAL PRIMARY KEY,

  announcement_id INTEGER NOT NULL
    REFERENCES drug.regulatory_announcement(id) ON DELETE CASCADE,

  reference_id INTEGER NOT NULL
    REFERENCES drug.reference_data(id) ON DELETE CASCADE,
    -- ref_type = 'application_area'

  evidence TEXT,
  confidence_score VARCHAR(255),

  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  UNIQUE(announcement_id, reference_id)
);

CREATE INDEX IF NOT EXISTS idx_announcement_application_area_announcement ON drug.announcement_application_area(announcement_id);
CREATE INDEX IF NOT EXISTS idx_announcement_application_area_reference ON drug.announcement_application_area(reference_id);


-- ============================================================================
-- Artixio Intelligence Module
-- ============================================================================

CREATE TABLE IF NOT EXISTS drug.intelligence_item (
  id SERIAL PRIMARY KEY,
  title VARCHAR(500) NOT NULL,
  description TEXT,
  summary TEXT,
  extracted_text TEXT,
  source_type VARCHAR(50) NOT NULL CHECK (source_type IN (
    'email', 'meeting_note', 'conversation', 'document',
    'report', 'presentation', 'regulatory_filing', 'web', 'other'
  )),
  source_date TIMESTAMPTZ,
  source_reference VARCHAR(500),
  geography_id INTEGER REFERENCES drug.regulatory_geography(id),
  status VARCHAR(50) DEFAULT 'draft',
  processing_status VARCHAR(50) DEFAULT 'pending',
  priority VARCHAR(20) DEFAULT 'normal',
  ai_summary TEXT,
  ai_key_points JSONB,
  ai_action_items JSONB,
  ai_confidence_score NUMERIC(3,2),
  ai_processed_at TIMESTAMPTZ,
  ai_model_used VARCHAR(100),
  json_data JSONB DEFAULT '{}'::jsonb,
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  created_by INTEGER,
  organization_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_intel_item_geography ON drug.intelligence_item(geography_id);
CREATE INDEX IF NOT EXISTS idx_intel_item_source_type ON drug.intelligence_item(source_type);
CREATE INDEX IF NOT EXISTS idx_intel_item_status ON drug.intelligence_item(status);
CREATE INDEX IF NOT EXISTS idx_intel_item_processing ON drug.intelligence_item(processing_status);
CREATE INDEX IF NOT EXISTS idx_intel_item_priority ON drug.intelligence_item(priority);
CREATE INDEX IF NOT EXISTS idx_intel_item_source_date ON drug.intelligence_item(source_date DESC);
CREATE INDEX IF NOT EXISTS idx_intel_item_created ON drug.intelligence_item(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_intel_item_org ON drug.intelligence_item(organization_id);
CREATE INDEX IF NOT EXISTS idx_intel_item_active ON drug.intelligence_item(is_active);

CREATE TABLE IF NOT EXISTS drug.intelligence_file (
  id SERIAL PRIMARY KEY,
  intelligence_item_id INTEGER NOT NULL REFERENCES drug.intelligence_item(id) ON DELETE CASCADE,
  original_filename VARCHAR(500) NOT NULL,
  s3_key VARCHAR(1000),
  file_size BIGINT,
  mime_type VARCHAR(100),
  file_type VARCHAR(50),
  extracted_text TEXT,
  extraction_status VARCHAR(50) DEFAULT 'pending',
  extraction_error TEXT,
  page_count INTEGER,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  uploaded_by INTEGER
);

CREATE INDEX IF NOT EXISTS idx_intel_file_item ON drug.intelligence_file(intelligence_item_id);
CREATE INDEX IF NOT EXISTS idx_intel_file_type ON drug.intelligence_file(file_type);
CREATE INDEX IF NOT EXISTS idx_intel_file_status ON drug.intelligence_file(extraction_status);

CREATE TABLE IF NOT EXISTS drug.intelligence_contact (
  id SERIAL PRIMARY KEY,
  intelligence_item_id INTEGER NOT NULL REFERENCES drug.intelligence_item(id) ON DELETE CASCADE,
  name VARCHAR(255) NOT NULL,
  email VARCHAR(255),
  organization VARCHAR(255),
  role VARCHAR(100),
  contact_type VARCHAR(50) DEFAULT 'external',
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_intel_contact_item ON drug.intelligence_contact(intelligence_item_id);
CREATE INDEX IF NOT EXISTS idx_intel_contact_name ON drug.intelligence_contact(name);
CREATE INDEX IF NOT EXISTS idx_intel_contact_org ON drug.intelligence_contact(organization);

CREATE TABLE IF NOT EXISTS drug.intelligence_key_topic (
  id SERIAL PRIMARY KEY,
  intelligence_item_id INTEGER NOT NULL REFERENCES drug.intelligence_item(id) ON DELETE CASCADE,
  reference_id INTEGER NOT NULL REFERENCES drug.reference_data(id) ON DELETE CASCADE,
  relevance_score NUMERIC(3,2),
  evidence TEXT,
  confidence_score NUMERIC(3,2),
  is_manual BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(intelligence_item_id, reference_id)
);

CREATE INDEX IF NOT EXISTS idx_intel_key_topic_item ON drug.intelligence_key_topic(intelligence_item_id);
CREATE INDEX IF NOT EXISTS idx_intel_key_topic_ref ON drug.intelligence_key_topic(reference_id);

CREATE TABLE IF NOT EXISTS drug.intelligence_classification (
  id SERIAL PRIMARY KEY,
  intelligence_item_id INTEGER NOT NULL REFERENCES drug.intelligence_item(id) ON DELETE CASCADE,
  reference_id INTEGER NOT NULL REFERENCES drug.reference_data(id) ON DELETE CASCADE,
  evidence TEXT,
  confidence_score NUMERIC(3,2),
  is_manual BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(intelligence_item_id, reference_id)
);

CREATE INDEX IF NOT EXISTS idx_intel_classification_item ON drug.intelligence_classification(intelligence_item_id);
CREATE INDEX IF NOT EXISTS idx_intel_classification_ref ON drug.intelligence_classification(reference_id);

CREATE TABLE IF NOT EXISTS drug.intelligence_function (
  id SERIAL PRIMARY KEY,
  intelligence_item_id INTEGER NOT NULL REFERENCES drug.intelligence_item(id) ON DELETE CASCADE,
  reference_id INTEGER NOT NULL REFERENCES drug.reference_data(id) ON DELETE CASCADE,
  impact_level VARCHAR(20),
  description TEXT,
  evidence TEXT,
  confidence_score NUMERIC(3,2),
  is_manual BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(intelligence_item_id, reference_id)
);

CREATE INDEX IF NOT EXISTS idx_intel_function_item ON drug.intelligence_function(intelligence_item_id);
CREATE INDEX IF NOT EXISTS idx_intel_function_ref ON drug.intelligence_function(reference_id);

CREATE TABLE IF NOT EXISTS drug.intelligence_therapeutic_area (
  id SERIAL PRIMARY KEY,
  intelligence_item_id INTEGER NOT NULL REFERENCES drug.intelligence_item(id) ON DELETE CASCADE,
  reference_id INTEGER NOT NULL REFERENCES drug.reference_data(id) ON DELETE CASCADE,
  evidence TEXT,
  confidence_score NUMERIC(3,2),
  is_manual BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(intelligence_item_id, reference_id)
);

CREATE INDEX IF NOT EXISTS idx_intel_therapeutic_area_item ON drug.intelligence_therapeutic_area(intelligence_item_id);
CREATE INDEX IF NOT EXISTS idx_intel_therapeutic_area_ref ON drug.intelligence_therapeutic_area(reference_id);

CREATE TABLE IF NOT EXISTS drug.intelligence_application_area (
  id SERIAL PRIMARY KEY,
  intelligence_item_id INTEGER NOT NULL REFERENCES drug.intelligence_item(id) ON DELETE CASCADE,
  reference_id INTEGER NOT NULL REFERENCES drug.reference_data(id) ON DELETE CASCADE,
  evidence TEXT,
  confidence_score NUMERIC(3,2),
  is_manual BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(intelligence_item_id, reference_id)
);

CREATE INDEX IF NOT EXISTS idx_intel_application_area_item ON drug.intelligence_application_area(intelligence_item_id);
CREATE INDEX IF NOT EXISTS idx_intel_application_area_ref ON drug.intelligence_application_area(reference_id);

CREATE TABLE IF NOT EXISTS drug.intelligence_product_type (
  id SERIAL PRIMARY KEY,
  intelligence_item_id INTEGER NOT NULL REFERENCES drug.intelligence_item(id) ON DELETE CASCADE,
  product_type_id INTEGER NOT NULL REFERENCES drug.product_type(id) ON DELETE CASCADE,
  evidence TEXT,
  confidence_score NUMERIC(3,2),
  is_manual BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(intelligence_item_id, product_type_id)
);

CREATE INDEX IF NOT EXISTS idx_intel_product_type_item ON drug.intelligence_product_type(intelligence_item_id);
CREATE INDEX IF NOT EXISTS idx_intel_product_type_type ON drug.intelligence_product_type(product_type_id);

CREATE TABLE IF NOT EXISTS drug.intelligence_pathway (
  id SERIAL PRIMARY KEY,
  intelligence_item_id INTEGER NOT NULL REFERENCES drug.intelligence_item(id) ON DELETE CASCADE,
  pathway_id INTEGER NOT NULL REFERENCES drug.regulatory_pathway(id) ON DELETE CASCADE,
  evidence TEXT,
  confidence_score NUMERIC(3,2),
  is_manual BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(intelligence_item_id, pathway_id)
);

CREATE INDEX IF NOT EXISTS idx_intel_pathway_item ON drug.intelligence_pathway(intelligence_item_id);
CREATE INDEX IF NOT EXISTS idx_intel_pathway_pathway ON drug.intelligence_pathway(pathway_id);

CREATE TABLE IF NOT EXISTS drug.intelligence_guideline_category (
  id SERIAL PRIMARY KEY,
  intelligence_item_id INTEGER NOT NULL REFERENCES drug.intelligence_item(id) ON DELETE CASCADE,
  category_id INTEGER NOT NULL REFERENCES drug.guideline_category(id) ON DELETE CASCADE,
  evidence TEXT,
  confidence_score NUMERIC(3,2),
  is_manual BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(intelligence_item_id, category_id)
);

CREATE INDEX IF NOT EXISTS idx_intel_category_item ON drug.intelligence_guideline_category(intelligence_item_id);
CREATE INDEX IF NOT EXISTS idx_intel_category_category ON drug.intelligence_guideline_category(category_id);

CREATE TABLE IF NOT EXISTS drug.intelligence_guideline_link (
  id SERIAL PRIMARY KEY,
  intelligence_item_id INTEGER NOT NULL REFERENCES drug.intelligence_item(id) ON DELETE CASCADE,
  guideline_id INTEGER REFERENCES drug.drug_guideline(id) ON DELETE CASCADE,
  announcement_id INTEGER REFERENCES drug.regulatory_announcement(id) ON DELETE CASCADE,
  relationship_type VARCHAR(50) DEFAULT 'related',
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  CHECK (guideline_id IS NOT NULL OR announcement_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_intel_link_item ON drug.intelligence_guideline_link(intelligence_item_id);
CREATE INDEX IF NOT EXISTS idx_intel_link_guideline ON drug.intelligence_guideline_link(guideline_id);
CREATE INDEX IF NOT EXISTS idx_intel_link_announcement ON drug.intelligence_guideline_link(announcement_id);

CREATE TABLE IF NOT EXISTS drug.intelligence_chunks (
  id SERIAL PRIMARY KEY,
  intelligence_item_id INTEGER NOT NULL REFERENCES drug.intelligence_item(id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL,
  chunk_text TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(intelligence_item_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_intel_chunks_item ON drug.intelligence_chunks(intelligence_item_id);

CREATE TABLE IF NOT EXISTS drug.intelligence_embeddings (
  id SERIAL PRIMARY KEY,
  chunk_id INTEGER NOT NULL REFERENCES drug.intelligence_chunks(id) ON DELETE CASCADE,
  embedding VECTOR(1024),
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_intel_embeddings_chunk ON drug.intelligence_embeddings(chunk_id);


-- Add agency columns to drug.drug_guideline
ALTER TABLE drug.drug_guideline 
ADD COLUMN IF NOT EXISTS agency_name TEXT, 
ADD COLUMN IF NOT EXISTS agency_acronym TEXT;
 
-- Add agency columns to drug.regulatory_announcement
ALTER TABLE drug.regulatory_announcement 
ADD COLUMN IF NOT EXISTS agency_name TEXT, 
ADD COLUMN IF NOT EXISTS agency_acronym TEXT;

-- ============================================================================
-- CONTENT DEDUPLICATION (fingerprints + findings audit log)
-- Runbook for existing databases: database/migrations/20260728_content_dedupe_review.sql
-- ============================================================================

-- Content fingerprints on both content tables. Written by the ingestion
-- pipeline (processing/content_dedupe.py); NULL content_hashed_at => not hashed yet.
ALTER TABLE drug.drug_guideline
  ADD COLUMN IF NOT EXISTS content_sha256   TEXT,
  ADD COLUMN IF NOT EXISTS content_simhash  BIGINT,
  ADD COLUMN IF NOT EXISTS content_hashed_at TIMESTAMP;

ALTER TABLE drug.regulatory_announcement
  ADD COLUMN IF NOT EXISTS content_sha256   TEXT,
  ADD COLUMN IF NOT EXISTS content_simhash  BIGINT,
  ADD COLUMN IF NOT EXISTS content_hashed_at TIMESTAMP;

-- btree indexes for the exact-duplicate GROUP BY on content_sha256
CREATE INDEX IF NOT EXISTS idx_drug_guideline_content_sha256
  ON drug.drug_guideline (content_sha256);
CREATE INDEX IF NOT EXISTS idx_regulatory_announcement_content_sha256
  ON drug.regulatory_announcement (content_sha256);

COMMENT ON COLUMN drug.drug_guideline.content_sha256 IS
  'SHA256 of the normalized/cleaned full document text (guideline_document_text). Exact match => byte-identical after cleaning.';
COMMENT ON COLUMN drug.drug_guideline.content_simhash IS
  '64-bit SimHash of the cleaned full text, stored in signed BIGINT range. Near-duplicates have a small Hamming distance.';
COMMENT ON COLUMN drug.drug_guideline.content_hashed_at IS
  'When content_sha256/content_simhash were last computed. NULL => not yet hashed.';
COMMENT ON COLUMN drug.regulatory_announcement.content_sha256 IS
  'SHA256 of the normalized/cleaned full document text (announcement_text). Exact match => byte-identical after cleaning.';
COMMENT ON COLUMN drug.regulatory_announcement.content_simhash IS
  '64-bit SimHash of the cleaned full text, stored in signed BIGINT range. Near-duplicates have a small Hamming distance.';
COMMENT ON COLUMN drug.regulatory_announcement.content_hashed_at IS
  'When content_sha256/content_simhash were last computed. NULL => not yet hashed.';

-- One row per detected candidate pair + the LLM verdict on the relationship.
-- No FKs on entity_a_id/entity_b_id: the pair is polymorphic over
-- drug_guideline / regulatory_announcement.
--
-- EACH SIDE CARRIES ITS OWN TYPE. A pair can straddle the two tables (entity_type =
-- 'cross') because the same document is routinely ingested as a guideline by one source
-- feed and as an announcement by another, so entity_a_type/entity_b_type discriminate the
-- sides INDEPENDENTLY and entity_type is only a coarse pair-kind label. Guideline ids and
-- announcement ids come from separate SERIAL sequences and collide freely, so an id must
-- never be resolved against a table chosen by the pair-wide entity_type.
-- Convention: same-table pairs keep entity_a_id < entity_b_id; a cross pair is always
-- a = guideline, b = announcement (id ordering cannot order a cross pair).
--
-- To upgrade an existing deployment (CREATE TABLE IF NOT EXISTS is a no-op there), run
-- database/migrations/20260804_content_dedupe_cross_table.sql.
CREATE TABLE IF NOT EXISTS drug.content_dedupe_review (
  id SERIAL PRIMARY KEY,

  entity_type          VARCHAR(20) NOT NULL,  -- 'guideline' | 'announcement' | 'cross'
  entity_a_type        VARCHAR(20) NOT NULL,  -- table entity_a_id lives in
  entity_a_id          INTEGER NOT NULL,
  entity_b_type        VARCHAR(20) NOT NULL,  -- table entity_b_id lives in
  entity_b_id          INTEGER NOT NULL,

  detection_method   VARCHAR(20) NOT NULL CHECK (detection_method IN ('sha256', 'simhash')),
  similarity_score   REAL,               -- simhash: 1 - hamming/64 ; sha256: 1.0
  hamming_distance   INTEGER,            -- NULL for sha256 exact matches

  -- LLM verdict (metadata + content aware)
  llm_relationship   VARCHAR(30) CHECK (llm_relationship IN (
                        'EXACT_DUPLICATE', 'NEAR_DUPLICATE', 'DRAFT_TO_FINAL',
                        'UPDATED_VERSION', 'SUMMARY_OF', 'DIFFERENT')),
  llm_keep_entity_id INTEGER,            -- id the LLM recommends keeping (NULL if DIFFERENT)
  llm_keep_entity_type VARCHAR(20),      -- table that id lives in; the id alone is ambiguous
  llm_confidence     VARCHAR(10) CHECK (llm_confidence IN ('HIGH', 'MEDIUM', 'LOW')),
  llm_reason         TEXT,
  difference_summary TEXT,               -- what actually differs between the two

  -- Review workflow (for the later acting phase; v1 leaves everything 'pending')
  review_status      VARCHAR(20) NOT NULL DEFAULT 'pending'
                        CHECK (review_status IN ('pending', 'approved', 'rejected')),
  reviewed_by        INTEGER,
  reviewed_at        TIMESTAMP,

  created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

  CONSTRAINT content_dedupe_review_entity_type_check CHECK (
    entity_type IN ('guideline', 'announcement', 'cross')),
  CONSTRAINT content_dedupe_review_side_types CHECK (
    entity_a_type IN ('guideline', 'announcement')
    AND entity_b_type IN ('guideline', 'announcement')),
  CONSTRAINT content_dedupe_review_keep_type_check CHECK (
    llm_keep_entity_type IS NULL
    OR llm_keep_entity_type IN ('guideline', 'announcement')),

  -- Only a same-type match on the same id is a self-pair; guideline 5 vs announcement 5 is
  -- two different documents.
  CONSTRAINT content_dedupe_review_no_self CHECK (
    entity_a_type <> entity_b_type OR entity_a_id <> entity_b_id),
  CONSTRAINT content_dedupe_review_unique UNIQUE (
    entity_a_type, entity_a_id, entity_b_type, entity_b_id),

  -- A cross pair is always (guideline, announcement); the mirror would be a distinct row
  -- under the unique key above and would be re-detected as "new" forever.
  CONSTRAINT content_dedupe_review_cross_order CHECK (
    entity_a_type = entity_b_type
    OR (entity_a_type = 'guideline' AND entity_b_type = 'announcement')),
  CONSTRAINT content_dedupe_review_entity_type_derived CHECK (
    entity_type = CASE WHEN entity_a_type = entity_b_type THEN entity_a_type ELSE 'cross' END),
  CONSTRAINT content_dedupe_review_keep_type_present CHECK (
    (llm_keep_entity_id IS NULL) = (llm_keep_entity_type IS NULL)),
  -- The keeper must be one of the two documents, matched on (type, id) TOGETHER: with ids
  -- alone, "keep 5" is satisfied by both sides of a guideline-5 vs announcement-5 pair.
  CONSTRAINT content_dedupe_review_keep_is_a_side CHECK (
    llm_keep_entity_id IS NULL
    OR (llm_keep_entity_type = entity_a_type AND llm_keep_entity_id = entity_a_id)
    OR (llm_keep_entity_type = entity_b_type AND llm_keep_entity_id = entity_b_id))
);

CREATE INDEX IF NOT EXISTS idx_content_dedupe_review_status
  ON drug.content_dedupe_review (review_status);
CREATE INDEX IF NOT EXISTS idx_content_dedupe_review_relationship
  ON drug.content_dedupe_review (llm_relationship);
CREATE INDEX IF NOT EXISTS idx_content_dedupe_review_entity_a
  ON drug.content_dedupe_review (entity_a_type, entity_a_id);
CREATE INDEX IF NOT EXISTS idx_content_dedupe_review_entity_b
  ON drug.content_dedupe_review (entity_b_type, entity_b_id);
CREATE INDEX IF NOT EXISTS idx_content_dedupe_review_type_created
  ON drug.content_dedupe_review (entity_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_content_dedupe_review_similarity
  ON drug.content_dedupe_review (similarity_score DESC);
CREATE INDEX IF NOT EXISTS idx_content_dedupe_review_confidence
  ON drug.content_dedupe_review (llm_confidence);
CREATE INDEX IF NOT EXISTS idx_content_dedupe_review_detection_method
  ON drug.content_dedupe_review (detection_method);

COMMENT ON TABLE drug.content_dedupe_review IS
  'Audit log of content-based near-duplicate candidate pairs and their LLM verdicts. EXACT_DUPLICATE/NEAR_DUPLICATE verdicts at HIGH confidence also deactivate (is_active=false) the non-kept row; all other relationships and confidence levels are recorded for human review only.';

-- ============================================================================
-- ENTITY COMPARISON CACHE (universal, shared across all client deployments)
-- Runbook for existing databases: database/migrations/20260730_entity_comparison_cache.sql,
-- database/migrations/20260730_entity_comparison_cache_drop_model_key.sql
-- ============================================================================

-- Cached AI comparison text for an ordered (left, right) entity pair — one row
-- per pair regardless of model (the admin-configured default model can change
-- over time; an older comparison is still valid to show). `model` just records
-- which model produced the current text. Direction is NOT normalized: "left"
-- is always the document the user was viewing (framed as CURRENT in the
-- prompt), "right" is the related one (framed as RELATED) — swapping them
-- changes the generated text.
CREATE TABLE IF NOT EXISTS drug.entity_comparison_cache (
  id             SERIAL PRIMARY KEY,
  left_type      VARCHAR(20)  NOT NULL,
  left_id        INTEGER      NOT NULL,
  right_type     VARCHAR(20)  NOT NULL,
  right_id       INTEGER      NOT NULL,
  model          VARCHAR(100) NOT NULL,
  comparison     TEXT         NOT NULL,
  left_meta      JSONB        NOT NULL,
  right_meta     JSONB        NOT NULL,
  relation       JSONB,
  diff_available BOOLEAN      NOT NULL DEFAULT FALSE,
  generated_by   INTEGER,
  created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  CONSTRAINT entity_comparison_cache_unique UNIQUE (left_type, left_id, right_type, right_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_comparison_cache_left ON drug.entity_comparison_cache(left_type, left_id);
CREATE INDEX IF NOT EXISTS idx_entity_comparison_cache_right ON drug.entity_comparison_cache(right_type, right_id);

COMMENT ON TABLE drug.entity_comparison_cache IS
  'Cached AI comparison text for an ordered (left, right, model) entity pair, shared across all client deployments.';
