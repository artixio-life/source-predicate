# TOFACITINIB SUN — ARTG-422356

## Schema

```sql
CREATE TABLE products (
    id UUID PRIMARY KEY,

    brand_name TEXT,
    generic_name TEXT,

    country TEXT,
    regulator TEXT,

    manufacturer TEXT,
    manufacturer_address TEXT,
    registration_number TEXT,

    product_type TEXT,
    status TEXT,

    registration_date DATE,
    approval_date DATE,
    market_authorization_date DATE,

    expiry_date DATE,
    withdrawal_date DATE,

    therapeutic_areas TEXT[],
    indications TEXT[],
    symptoms TEXT[],

    active_ingredients TEXT[],
    dosage_forms TEXT[],
    routes TEXT[],

    product_data JSONB,

    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

## Overview

| Column               | Value                                                                                                                  |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| id                   | 422356                                                                                                                 |
| brand_name           | TOFACITINIB SUN                                                                                                        |
| generic_name         | Tofacitinib                                                                                                            |
| country              | Australia                                                                                                              |
| regulator            | TGA                                                                                                                    |
| manufacturer         | Sun Pharma ANZ Pty Ltd                                                                                                 |
| registration_number  | ARTG-422356                                                                                                            |
| registration_date    | 2026-07-29                                                                                                             |
| status               | Active                                                                                                                 |
| product_type         | Prescription Medicine                                                                                                  |
| therapeutic_areas    | {Rheumatology, Immunology, Gastroenterology}                                                                           |
| active_ingredients   | {Tofacitinib, Tofacitinib citrate}                                                                                     |
| dosage_forms         | {Film-coated tablet}                                                                                                   |
| routes               | {Oral}                                                                                                                 |
| indications          | {Rheumatoid Arthritis, Psoriatic Arthritis, Ulcerative Colitis, Ankylosing Spondylitis, Juvenile Idiopathic Arthritis} |

## Details

```json
{
  "regulatory": {
    "registration_number": "ARTG-422356",
    "regulator": "TGA",
    "country": "Australia",
    "status": "Active",
    "registration_date": "2026-07-29",
    "additional_monitoring": true,
    "prescription_schedule": "S4"
  },

  "manufacturer": {
    "name": "Sun Pharma ANZ Pty Ltd"
  },

  "therapeutic_areas": [
    "Rheumatology",
    "Immunology",
    "Gastroenterology"
  ],

  "indications": [
    {
      "disease": "Rheumatoid Arthritis",
      "population": "Adults",
      "severity": "Moderate to Severe"
    },
    {
      "disease": "Psoriatic Arthritis",
      "population": "Adults"
    },
    {
      "disease": "Ulcerative Colitis",
      "population": "Adults",
      "severity": "Moderately to Severely Active"
    },
    {
      "disease": "Ankylosing Spondylitis",
      "population": "Adults"
    },
    {
      "disease": "Juvenile Idiopathic Arthritis",
      "population": "Patients >= 2 years"
    }
  ],

  "symptoms": [
    "Joint pain",
    "Joint swelling",
    "Morning stiffness",
    "Inflammation",
    "Back pain",
    "Gastrointestinal inflammation",
    "Rectal bleeding"
  ],

  "drug_class": [
    "Janus Kinase Inhibitor",
    "JAK Inhibitor",
    "Immunomodulator"
  ],

  "presentations": [
    {
      "dosage_form": "Film-coated tablet",
      "route": "Oral",

      "ingredients": [
        {
          "substance": "Tofacitinib citrate",
          "strength": {
            "value": 8.075,
            "unit": "mg"
          },
          "equivalent_to": {
            "substance": "Tofacitinib",
            "value": 5,
            "unit": "mg"
          }
        }
      ],

      "packaging": [
        {
          "container_type": "Blister Pack",
          "pack_size": 56
        }
      ]
    }
  ],

  "appearance": {
    "color": "White to off-white",
    "shape": "Round",
    "imprint": "RX 13"
  },

  "storage": {
    "temperature": "Store below 25°C",
    "shelf_life_months": 36
  },

  "excipients": [
    "Croscarmellose sodium",
    "Hypromellose",
    "Lactose monohydrate",
    "Macrogol 3350",
    "Magnesium stearate",
    "Microcrystalline cellulose",
    "Purified water",
    "Titanium dioxide",
    "Triacetin"
  ]
}
```
