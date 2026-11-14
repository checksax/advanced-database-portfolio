-- =====================================================================
-- St. Grace Specialist Hospital - Reference / lookup data
-- Small, hand-curated tables. Bulk transactional data (staff, patients,
-- appointments, encounters ...) is produced by ../../data-generator.
-- =====================================================================
SET search_path TO hospital, public;

INSERT INTO departments (name, location, phone_ext) VALUES
 ('General Medicine',    'Block A, Ground Floor', '101'),
 ('Paediatrics',         'Block B, First Floor',  '201'),
 ('Obstetrics & Gynaecology', 'Block C, Ground Floor', '301'),
 ('Surgery',             'Block D, First Floor',  '401'),
 ('Cardiology',          'Block A, Second Floor', '501'),
 ('Emergency',           'Main Gate Wing',        '911'),
 ('Laboratory',          'Block E, Ground Floor', '601'),
 ('Pharmacy',            'Block A, Ground Floor', '701'),
 ('Radiology',           'Block E, First Floor',  '801'),
 ('Administration & Finance', 'Admin Block',      '100');

INSERT INTO insurance_providers (name, coverage_percent, phone) VALUES
 ('NHIA (National Health Insurance Authority)', 90.00, '0800-000-6442'),
 ('Hygeia HMO',          80.00, '0700-449-4342'),
 ('AXA Mansard Health',  85.00, '0700-292-6273'),
 ('Reliance HMO',        75.00, '0700-735-4262'),
 ('Leadway Health',      80.00, '0700-532-3929');

INSERT INTO icd10_codes (code, description, category) VALUES
 ('B54',    'Unspecified malaria',                                 'Infectious diseases'),
 ('B50.9',  'Plasmodium falciparum malaria, unspecified',          'Infectious diseases'),
 ('A01.0',  'Typhoid fever',                                       'Infectious diseases'),
 ('A09',    'Infectious gastroenteritis and colitis',              'Infectious diseases'),
 ('B20',    'HIV disease',                                         'Infectious diseases'),
 ('A15.0',  'Tuberculosis of lung',                                'Infectious diseases'),
 ('I10',    'Essential (primary) hypertension',                    'Circulatory system'),
 ('I50.9',  'Heart failure, unspecified',                          'Circulatory system'),
 ('I63.9',  'Cerebral infarction, unspecified',                    'Circulatory system'),
 ('E11.9',  'Type 2 diabetes mellitus without complications',      'Endocrine & metabolic'),
 ('E66.9',  'Obesity, unspecified',                                'Endocrine & metabolic'),
 ('D57.1',  'Sickle-cell disease without crisis',                  'Blood disorders'),
 ('D64.9',  'Anaemia, unspecified',                                'Blood disorders'),
 ('J18.9',  'Pneumonia, unspecified organism',                     'Respiratory system'),
 ('J45.9',  'Asthma, unspecified',                                 'Respiratory system'),
 ('J06.9',  'Acute upper respiratory infection, unspecified',      'Respiratory system'),
 ('K29.7',  'Gastritis, unspecified',                              'Digestive system'),
 ('K35.8',  'Acute appendicitis, other and unspecified',           'Digestive system'),
 ('N39.0',  'Urinary tract infection, site not specified',         'Genitourinary system'),
 ('O80',    'Single spontaneous delivery',                         'Pregnancy & childbirth'),
 ('O14.9',  'Pre-eclampsia, unspecified',                          'Pregnancy & childbirth'),
 ('M54.5',  'Low back pain',                                       'Musculoskeletal'),
 ('S52.5',  'Fracture of lower end of radius',                     'Injury'),
 ('R50.9',  'Fever, unspecified',                                  'Symptoms & signs'),
 ('Z00.0',  'General adult medical examination',                   'Health services');

INSERT INTO drugs (generic_name, brand_name, dosage_form, strength, unit_price, stock_quantity, reorder_level) VALUES
 ('Artemether/Lumefantrine', 'Coartem',     'Tablet',    '20/120 mg', 350.00, 5000, 500),
 ('Paracetamol',            'Emzor',        'Tablet',    '500 mg',     20.00, 20000, 2000),
 ('Amoxicillin',            'Amoxil',       'Capsule',   '500 mg',     60.00, 8000, 800),
 ('Ciprofloxacin',          'Ciprotab',     'Tablet',    '500 mg',     90.00, 4000, 400),
 ('Metronidazole',          'Flagyl',       'Tablet',    '400 mg',     30.00, 6000, 600),
 ('Amlodipine',             'Norvasc',      'Tablet',    '5 mg',       45.00, 7000, 700),
 ('Lisinopril',             'Zestril',      'Tablet',    '10 mg',      55.00, 5000, 500),
 ('Metformin',              'Glucophage',   'Tablet',    '500 mg',     40.00, 9000, 900),
 ('Insulin (Soluble)',      'Actrapid',     'Injection', '100 IU/ml', 4500.00, 300, 50),
 ('Omeprazole',             'Losec',        'Capsule',   '20 mg',      70.00, 6000, 600),
 ('Ceftriaxone',            'Rocephin',     'Injection', '1 g',      1500.00, 1200, 150),
 ('Artesunate',             'Artesun',      'Injection', '60 mg',    2500.00,  800, 100),
 ('Ferrous Sulphate',       NULL,           'Tablet',    '200 mg',     10.00, 15000, 1500),
 ('Folic Acid',             NULL,           'Tablet',    '5 mg',        8.00, 15000, 1500),
 ('Salbutamol',             'Ventolin',     'Inhaler',   '100 mcg',  3200.00,  400, 60),
 ('Ibuprofen',              'Brufen',       'Tablet',    '400 mg',     35.00, 10000, 1000),
 ('Oral Rehydration Salts', 'ORS',          'Suspension','20.5 g',    150.00, 3000, 300),
 ('Normal Saline',          NULL,           'Infusion',  '0.9% 500 ml', 900.00, 2000, 300),
 ('Hydrocortisone',         NULL,           'Cream',     '1%',        800.00,  600, 80),
 ('Hydroxyurea',            'Hydrea',       'Capsule',   '500 mg',    250.00, 2000, 200);

INSERT INTO lab_tests (test_code, name, sample_type, unit, normal_low, normal_high, price) VALUES
 ('MPS',    'Malaria Parasite Smear',        'Blood', NULL,         NULL,  NULL,   1500.00),
 ('FBC',    'Full Blood Count (Haemoglobin)','Blood', 'g/dL',       12.00, 17.50,  4000.00),
 ('PCV',    'Packed Cell Volume',            'Blood', '%',          36.00, 50.00,  1500.00),
 ('WIDAL',  'Widal Test',                    'Blood', NULL,         NULL,  NULL,   2000.00),
 ('FBS',    'Fasting Blood Sugar',           'Blood', 'mmol/L',      3.90,  5.60,  1500.00),
 ('HBA1C',  'Glycated Haemoglobin',          'Blood', '%',           4.00,  5.60,  8000.00),
 ('LIPID',  'Lipid Profile (Total Chol.)',   'Blood', 'mmol/L',      0.00,  5.20,  9000.00),
 ('EUCR',   'Electrolytes, Urea, Creatinine','Blood', 'umol/L',     60.00, 110.00, 7500.00),
 ('LFT',    'Liver Function Test (ALT)',     'Blood', 'U/L',         7.00, 56.00,  7500.00),
 ('HIV',    'HIV 1&2 Screening',             'Blood', NULL,         NULL,  NULL,   2500.00),
 ('GENO',   'Haemoglobin Genotype',          'Blood', NULL,         NULL,  NULL,   2000.00),
 ('UA',     'Urinalysis',                    'Urine', NULL,         NULL,  NULL,   1500.00),
 ('MCS-U',  'Urine Microscopy, Culture & Sensitivity', 'Urine', NULL, NULL, NULL,  5000.00),
 ('STOOL',  'Stool Microscopy',              'Stool', NULL,         NULL,  NULL,   2000.00),
 ('AFB',    'Sputum AFB (TB)',               'Sputum',NULL,         NULL,  NULL,   3000.00);

INSERT INTO wards (name, ward_type, department_id, daily_rate)
SELECT v.name, v.ward_type, d.department_id, v.daily_rate
FROM (VALUES
 ('Florence Nightingale Ward', 'General',    'General Medicine',         15000.00),
 ('Mary Slessor Ward',         'Private',    'General Medicine',         45000.00),
 ('Intensive Care Unit',       'ICU',        'Emergency',               120000.00),
 ('Rainbow Children Ward',     'Paediatric', 'Paediatrics',              12000.00),
 ('Mother & Child Ward',       'Maternity',  'Obstetrics & Gynaecology', 18000.00),
 ('Surgical Ward',             'Surgical',   'Surgery',                  25000.00)
) AS v(name, ward_type, dept, daily_rate)
JOIN departments d ON d.name = v.dept;

-- Beds: ICU has 8, everything else 20.
INSERT INTO beds (ward_id, bed_number)
SELECT w.ward_id, left(w.ward_type, 3) || '-' || lpad(n::text, 2, '0')
FROM wards w
CROSS JOIN LATERAL generate_series(1, CASE WHEN w.ward_type = 'ICU' THEN 8 ELSE 20 END) AS n;
