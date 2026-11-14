# ER Diagram — St. Grace Specialist Hospital

The diagram below uses crow's-foot notation (Mermaid `erDiagram`). It renders on GitHub
and in VS Code. The raw source is `diagrams/er-diagram.mmd`: paste it into https://mermaid.live and use Actions > PNG to export `diagrams/er-diagram.png`. pgAdmin (right-click the database > ERD For Database) gives a second, auto-generated diagram for `screenshots/`.

- `||--o{` one to zero-or-many
- `||--|{` one to one-or-many
- `|o--o{` zero-or-one to zero-or-many
- `||--o|` one to zero-or-one

```mermaid
erDiagram
    DEPARTMENTS ||--|{ STAFF : employs
    DEPARTMENTS |o--o| STAFF : "headed by"
    DEPARTMENTS ||--o{ WARDS : runs
    STAFF ||--o| DOCTORS : "is a (ISA)"

    INSURANCE_PROVIDERS |o--o{ PATIENTS : covers
    PATIENTS ||--o{ PATIENT_ALLERGIES : has

    PATIENTS ||--o{ APPOINTMENTS : books
    DOCTORS  ||--o{ APPOINTMENTS : attends
    APPOINTMENTS |o--o| ENCOUNTERS : "results in"

    WARDS ||--|{ BEDS : contains
    BEDS ||--o{ ADMISSIONS : "assigned in"
    PATIENTS ||--o{ ADMISSIONS : "admitted as"
    DOCTORS ||--o{ ADMISSIONS : admits
    ADMISSIONS |o--o{ ENCOUNTERS : "ward round"

    PATIENTS ||--o{ ENCOUNTERS : has
    DOCTORS  ||--o{ ENCOUNTERS : conducts
    ENCOUNTERS ||--o{ DIAGNOSES : records
    ICD10_CODES ||--o{ DIAGNOSES : classifies

    ENCOUNTERS ||--o{ PRESCRIPTIONS : generates
    DOCTORS ||--o{ PRESCRIPTIONS : writes
    PRESCRIPTIONS ||--|{ PRESCRIPTION_ITEMS : lists
    DRUGS ||--o{ PRESCRIPTION_ITEMS : "dispensed as"

    ENCOUNTERS ||--o{ LAB_ORDERS : requests
    LAB_TESTS ||--o{ LAB_ORDERS : "ordered as"
    LAB_ORDERS ||--o| LAB_RESULTS : produces
    STAFF ||--o{ LAB_RESULTS : performs

    PATIENTS ||--o{ INVOICES : billed
    ENCOUNTERS |o--o{ INVOICES : "billed for"
    ADMISSIONS |o--o{ INVOICES : "billed for"
    INVOICES ||--|{ INVOICE_ITEMS : contains
    INVOICES ||--o{ PAYMENTS : "settled by"
    STAFF ||--o{ PAYMENTS : receives

    DEPARTMENTS {
        int department_id PK
        varchar name UK
        varchar location
        int head_staff_id FK
    }
    STAFF {
        int staff_id PK
        varchar staff_no UK
        varchar first_name
        varchar last_name
        varchar role "CHECK enum"
        int department_id FK
        varchar email UK
        date hire_date
        bool is_active
    }
    DOCTORS {
        int staff_id PK,FK
        varchar specialty
        varchar license_no UK
        numeric consultation_fee
    }
    INSURANCE_PROVIDERS {
        int provider_id PK
        varchar name UK
        numeric coverage_percent
    }
    PATIENTS {
        int patient_id PK
        varchar hospital_no UK
        varchar first_name
        varchar last_name
        date date_of_birth
        char gender
        varchar blood_group
        varchar genotype
        varchar phone
        int provider_id FK
        varchar policy_no
        timestamp registered_at
    }
    PATIENT_ALLERGIES {
        int patient_id PK,FK
        varchar allergen PK
        varchar severity
    }
    APPOINTMENTS {
        int appointment_id PK
        int patient_id FK
        int doctor_id FK
        timestamp scheduled_start
        timestamp scheduled_end
        varchar status
    }
    WARDS {
        int ward_id PK
        varchar name UK
        varchar ward_type
        int department_id FK
        numeric daily_rate
    }
    BEDS {
        int bed_id PK
        int ward_id FK
        varchar bed_number
        varchar status
    }
    ADMISSIONS {
        int admission_id PK
        int patient_id FK
        int bed_id FK
        int admitting_doctor_id FK
        timestamp admitted_at
        timestamp discharged_at
    }
    ENCOUNTERS {
        int encounter_id PK
        int patient_id FK
        int doctor_id FK
        int appointment_id FK,UK
        int admission_id FK
        varchar encounter_type
        timestamp encounter_date
        numeric temperature_c
        smallint bp_systolic
    }
    ICD10_CODES {
        varchar code PK
        varchar description
        varchar category
    }
    DIAGNOSES {
        int diagnosis_id PK
        int encounter_id FK
        varchar icd10_code FK
        varchar diagnosis_type
    }
    DRUGS {
        int drug_id PK
        varchar generic_name
        varchar dosage_form
        varchar strength
        numeric unit_price
        int stock_quantity
    }
    PRESCRIPTIONS {
        int prescription_id PK
        int encounter_id FK
        int prescribed_by FK
        varchar status
        int dispensed_by FK
    }
    PRESCRIPTION_ITEMS {
        int prescription_id PK,FK
        int drug_id PK,FK
        varchar dose
        varchar frequency
        int quantity
        int dispensed_quantity
    }
    LAB_TESTS {
        int test_id PK
        varchar test_code UK
        varchar name
        numeric normal_low
        numeric normal_high
        numeric price
    }
    LAB_ORDERS {
        int lab_order_id PK
        int encounter_id FK
        int test_id FK
        int ordered_by FK
        varchar priority
        varchar status
    }
    LAB_RESULTS {
        int lab_order_id PK,FK
        numeric result_value
        varchar result_text
        varchar flag
        int performed_by FK
    }
    INVOICES {
        int invoice_id PK
        varchar invoice_no UK
        int patient_id FK
        int encounter_id FK
        int admission_id FK
        varchar status
    }
    INVOICE_ITEMS {
        int invoice_item_id PK
        int invoice_id FK
        varchar item_type
        int quantity
        numeric unit_price
        numeric line_total "GENERATED"
    }
    PAYMENTS {
        int payment_id PK
        int invoice_id FK
        numeric amount
        varchar method
        varchar reference UK
        int received_by FK
    }
```

## Design decisions shown in the diagram

| Decision | Why |
|---|---|
| **Specialisation (ISA):** `DOCTORS` is a subtype of `STAFF` | Only doctors have a licence, specialty and fee. Putting those columns on `STAFF` would leave them NULL for about 80% of rows. |
| **Weak entity:** `PATIENT_ALLERGIES` | An allergy has no meaning without its patient. Its key is `(patient_id, allergen)`, and rows are deleted with the patient (`ON DELETE CASCADE`). |
| **Associative entity:** `PRESCRIPTION_ITEMS` | Resolves the many-to-many between `PRESCRIPTIONS` and `DRUGS` and stores attributes of the link (dose, frequency, quantity). |
| **1 : 0..1** `LAB_ORDERS` → `LAB_RESULTS` | A result exists only after the test is done, so pending orders don't carry empty result columns. |
| **1 : 0..1** `APPOINTMENTS` → `ENCOUNTERS` | A no-show or cancelled appointment produces no encounter. Walk-in emergency encounters have no appointment. `UNIQUE(appointment_id)` enforces "at most one". |
| **Circular reference** `DEPARTMENTS.head_staff_id` ↔ `STAFF.department_id` | Resolved with a `DEFERRABLE INITIALLY DEFERRED` foreign key, so a department and its head can be created in the same transaction. |
| **Central `ENCOUNTERS` entity** | Every clinical event (diagnosis, prescription, lab order, bill) hangs off one visit. That makes patient-history queries a single join path. |
