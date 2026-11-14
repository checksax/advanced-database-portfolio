# Portfolio 4 query results

MongoDB 9.0.4, wall-clock median of 7 warm runs.

## Storage profile

| Collection | Model | Documents | Avg doc size | Data size | Storage (compressed) | Indexes |
|---|---|---:|---:|---:|---:|---:|
| `patients` | document | 50,000 | 752 B | 35.9 MB | 12.8 MB | 1.6 MB |
| `encounters` | document | 172,743 | 1,501 B | 247.4 MB | 70.8 MB | 8.9 MB |
| `drugs` | document (attribute pattern) | 20 | 201 B | 0.0 MB | 0.0 MB | 0.0 MB |
| `staff` | document | 360 | 248 B | 0.1 MB | 0.0 MB | 0.0 MB |
| `referrals` | graph edges | 13,008 | 217 B | 2.7 MB | 0.7 MB | 0.3 MB |
| `vitals` | time series | 24,771 | 0 B | 0.5 MB | 0.2 MB | 0.1 MB |
| `cache` | key-value (TTL) | 1 | 272 B | 0.0 MB | 0.0 MB | 0.0 MB |
| `queue` | key-value / queue | 0 | 0 B | 0.0 MB | 0.0 MB | 0.0 MB |

## Queries

| Query | Question | Docs returned | Client time | Server time | Keys examined | Docs examined | Plan | Index |
|---|---|---:|---:|---:|---:|---:|---|---|
| M1 | Patient clinical history (= SQL Q3) | 293 | 5.31 ms | 3 ms | 293 | 293 | IXSCAN > FETCH > PROJECTION_DEFAULT | patient_history |
| M2 | Weekly malaria cases, last 13 weeks (= SQL Q7) | 14 | 32.76 ms | 47 ms | 2,929 | 2,867 | IXSCAN > FETCH > PROJECTION_SIMPLE | diagnosis_date |
| M3 | Critical lab results in last 7 days + patient phone (= SQL Q8) | 76 | 24.18 ms | 24 ms | 197 | 154 | IXSCAN > FETCH > PROJECTION_DEFAULT | critical_labs |
| M3b | M3 without the $lookup (as if phone were embedded in encounter.patient) | 76 | 2.98 ms | 2 ms | 121 | 78 | IXSCAN > FETCH > PROJECTION_DEFAULT | critical_labs |
| M4 | Case-insensitive patient search via collation index (cf. SQL Q2) | 11 | 1.39 ms | 1 ms | 11 | 11 | IXSCAN > FETCH > PROJECTION_SIMPLE > SORT | name_ci |
| M5 | Revenue collected by department and visit month, last 12 months | 72 | 483.56 ms | 578 ms | 86,338 | 86,338 | IXSCAN > FETCH > GROUP | date |
| M6 | Top 10 drugs dispensed, last 90 days ($unwind of embedded items) | 10 | 200.00 ms | 211 ms | 21,377 | 21,377 | IXSCAN > FETCH > PROJECTION_DEFAULT | date |
| M7 | Antibiotic resistance from polymorphic culture results | 10 | 441.71 ms | 481 ms | 0 | 172,743 | COLLSCAN > PROJECTION_SIMPLE | - |
| M8 | Time series: inpatients whose 12-hour moving-average temperature is above 38 C now | 3 | 15.82 ms | 14 ms | 0 | 408 | CLUSTERED_IXSCAN | - |
| M9 | Graph: departments reachable through referral chains from General Medicine doctor | 10 | 132.87 ms | 137 ms | 1 | 1 | EXPRESS_IXSCAN | _id_ |
| M10 | Key-value: read a cached patient summary by key | 1 | 0.86 ms | 0 ms | 1 | 1 | EXPRESS_IXSCAN | _id_ |

## MongoDB vs PostgreSQL on the same questions

| Question | MongoDB (indexed) | MongoDB (forced COLLSCAN) | PostgreSQL (Portfolio 2) | Same answer? |
|---|---:|---:|---:|:---:|
| M1 Patient clinical history | 5.31 ms (293 docs) | 185.69 ms (172,743 docs) | 7.43 ms | yes |
| M2 Weekly malaria cases, last 13 weeks | 32.76 ms (2,867 docs) | 256.15 ms (172,743 docs) | 22.01 ms | yes |
| M3 Critical lab results in last 7 days + patient phone | 24.18 ms (154 docs) | 348.93 ms (172,819 docs) | 2.68 ms | yes |

## Query output (first rows)

### M1 Patient clinical history (= SQL Q3)

```
{"_id": 172347, "type": "Emergency", "date": {"$date": "2026-11-01T20:40:54Z"}, "doctor": {"name": "Dr. Eno Aminu"}, "diagnoses": [{"code": "K35.8", "description": "Acute appendicitis, other and unspecified", "category": "Digestive system",
{"_id": 171252, "type": "Outpatient", "date": {"$date": "2026-10-27T13:18:00Z"}, "doctor": {"name": "Dr. Joy Etim"}, "diagnoses": [{"code": "I10", "description": "Essential (primary) hypertension", "category": "Circulatory system", "type": 
{"_id": 170414, "type": "Outpatient", "date": {"$date": "2026-10-23T09:34:00Z"}, "doctor": {"name": "Dr. Doosuur Osagie"}, "diagnoses": [{"code": "M54.5", "description": "Low back pain", "category": "Musculoskeletal", "type": "Primary"}, {"
{"_id": 169691, "type": "Emergency", "date": {"$date": "2026-10-21T06:05:16Z"}, "doctor": {"name": "Dr. Mfon Afolabi"}, "diagnoses": [{"code": "J45.9", "description": "Asthma, unspecified", "category": "Respiratory system", "type": "Primary
{"_id": 168115, "type": "Outpatient", "date": {"$date": "2026-10-14T11:03:00Z"}, "doctor": {"name": "Dr. Ladi Adebayo"}, "diagnoses": [{"code": "D64.9", "description": "Anaemia, unspecified", "category": "Blood disorders", "type": "Primary"
{"_id": 168069, "type": "Outpatient", "date": {"$date": "2026-10-14T09:42:00Z"}, "doctor": {"name": "Dr. Nnamdi Ibe"}, "diagnoses": [{"code": "S52.5", "description": "Fracture of lower end of radius", "category": "Injury", "type": "Primary"
{"_id": 164931, "type": "Outpatient", "date": {"$date": "2026-10-01T08:12:00Z"}, "doctor": {"name": "Dr. Efe Etim"}, "diagnoses": [{"code": "S52.5", "description": "Fracture of lower end of radius", "category": "Injury", "type": "Primary"}]
{"_id": 164648, "type": "Outpatient", "date": {"$date": "2026-09-30T09:02:00Z"}, "doctor": {"name": "Dr. Efe Etim"}, "diagnoses": [{"code": "M54.5", "description": "Low back pain", "category": "Musculoskeletal", "type": "Primary"}]}
... 285 more
```

### M2 Weekly malaria cases, last 13 weeks (= SQL Q7)

```
{"_id": {"$date": "2026-08-03T00:00:00Z"}, "cases": 186}
{"_id": {"$date": "2026-08-10T00:00:00Z"}, "cases": 238}
{"_id": {"$date": "2026-08-17T00:00:00Z"}, "cases": 210}
{"_id": {"$date": "2026-08-24T00:00:00Z"}, "cases": 205}
{"_id": {"$date": "2026-08-31T00:00:00Z"}, "cases": 221}
{"_id": {"$date": "2026-09-07T00:00:00Z"}, "cases": 210}
{"_id": {"$date": "2026-09-14T00:00:00Z"}, "cases": 245}
{"_id": {"$date": "2026-09-21T00:00:00Z"}, "cases": 249}
... 6 more
```

### M3 Critical lab results in last 7 days + patient phone (= SQL Q8)

```
{"resulted_at": {"$date": "2026-11-03T03:58:00Z"}, "test": "Lipid Profile (Total Chol.)", "hospital_no": "SGH-0041939", "phone": "08062528005"}
{"resulted_at": {"$date": "2026-11-03T00:06:00Z"}, "test": "Lipid Profile (Total Chol.)", "hospital_no": "SGH-0016804", "phone": "08032814085"}
{"resulted_at": {"$date": "2026-11-02T15:36:00Z"}, "test": "Lipid Profile (Total Chol.)", "hospital_no": "SGH-0005548", "phone": "08167287696"}
{"resulted_at": {"$date": "2026-11-02T09:29:00Z"}, "test": "Lipid Profile (Total Chol.)", "hospital_no": "SGH-0023627", "phone": "08096641427"}
{"resulted_at": {"$date": "2026-11-01T04:04:00Z"}, "test": "Sputum AFB (TB)", "hospital_no": "SGH-0005562", "phone": "08161313676"}
{"resulted_at": {"$date": "2026-10-31T23:39:00Z"}, "test": "Sputum AFB (TB)", "hospital_no": "SGH-0001700", "phone": "09026065372"}
{"resulted_at": {"$date": "2026-10-31T10:18:54Z"}, "test": "Electrolytes, Urea, Creatinine", "hospital_no": "SGH-0008718", "phone": "08093282971"}
{"resulted_at": {"$date": "2026-10-31T07:17:00Z"}, "test": "Lipid Profile (Total Chol.)", "hospital_no": "SGH-0049658", "phone": "09023680370"}
... 68 more
```

### M3b M3 without the $lookup (as if phone were embedded in encounter.patient)

```
{"resulted_at": {"$date": "2026-11-03T03:58:00Z"}, "test": "Lipid Profile (Total Chol.)", "hospital_no": "SGH-0041939"}
{"resulted_at": {"$date": "2026-11-03T00:06:00Z"}, "test": "Lipid Profile (Total Chol.)", "hospital_no": "SGH-0016804"}
{"resulted_at": {"$date": "2026-11-02T15:36:00Z"}, "test": "Lipid Profile (Total Chol.)", "hospital_no": "SGH-0005548"}
{"resulted_at": {"$date": "2026-11-02T09:29:00Z"}, "test": "Lipid Profile (Total Chol.)", "hospital_no": "SGH-0023627"}
{"resulted_at": {"$date": "2026-11-01T04:04:00Z"}, "test": "Sputum AFB (TB)", "hospital_no": "SGH-0005562"}
{"resulted_at": {"$date": "2026-10-31T23:39:00Z"}, "test": "Sputum AFB (TB)", "hospital_no": "SGH-0001700"}
{"resulted_at": {"$date": "2026-10-31T10:18:54Z"}, "test": "Electrolytes, Urea, Creatinine", "hospital_no": "SGH-0008718"}
{"resulted_at": {"$date": "2026-10-31T07:17:00Z"}, "test": "Lipid Profile (Total Chol.)", "hospital_no": "SGH-0049658"}
... 68 more
```

### M4 Case-insensitive patient search via collation index (cf. SQL Q2)

```
{"_id": 12629, "hospital_no": "SGH-0012629", "name": {"first": "Ngozi", "last": "Okafor"}, "date_of_birth": {"$date": {"$numberLong": "-491356800000"}}}
{"_id": 25477, "hospital_no": "SGH-0025477", "name": {"first": "Ngozi", "last": "Okafor"}, "date_of_birth": {"$date": {"$numberLong": "-288316800000"}}}
{"_id": 26889, "hospital_no": "SGH-0026889", "name": {"first": "Ngozi", "last": "Okafor"}, "date_of_birth": {"$date": "1977-12-31T00:00:00Z"}}
{"_id": 253, "hospital_no": "SGH-0000253", "name": {"first": "Ngozi", "last": "Okafor"}, "date_of_birth": {"$date": "1988-12-28T00:00:00Z"}}
{"_id": 20465, "hospital_no": "SGH-0020465", "name": {"first": "Ngozi", "last": "Okafor"}, "date_of_birth": {"$date": "1989-10-17T00:00:00Z"}}
{"_id": 13781, "hospital_no": "SGH-0013781", "name": {"first": "Ngozi", "last": "Okafor"}, "date_of_birth": {"$date": "2001-11-18T00:00:00Z"}}
{"_id": 37123, "hospital_no": "SGH-0037123", "name": {"first": "Ngozi", "last": "Okafor"}, "date_of_birth": {"$date": "2004-12-16T00:00:00Z"}}
{"_id": 24328, "hospital_no": "SGH-0024328", "name": {"first": "Ngozi", "last": "Okafor"}, "date_of_birth": {"$date": "2005-06-02T00:00:00Z"}}
... 3 more
```

### M5 Revenue collected by department and visit month, last 12 months

```
{"_id": {"month": {"$date": "2025-11-01T00:00:00Z"}, "department": "Cardiology"}, "collected": {"$numberDecimal": "15619571.95"}, "visits": 740}
{"_id": {"month": {"$date": "2025-11-01T00:00:00Z"}, "department": "Emergency"}, "collected": {"$numberDecimal": "30975512.07"}, "visits": 662}
{"_id": {"month": {"$date": "2025-11-01T00:00:00Z"}, "department": "General Medicine"}, "collected": {"$numberDecimal": "57101510.63"}, "visits": 1908}
{"_id": {"month": {"$date": "2025-11-01T00:00:00Z"}, "department": "Obstetrics & Gynaecology"}, "collected": {"$numberDecimal": "25884862.90"}, "visits": 1108}
{"_id": {"month": {"$date": "2025-11-01T00:00:00Z"}, "department": "Paediatrics"}, "collected": {"$numberDecimal": "26880065.41"}, "visits": 1137}
{"_id": {"month": {"$date": "2025-11-01T00:00:00Z"}, "department": "Surgery"}, "collected": {"$numberDecimal": "27793059.51"}, "visits": 1143}
{"_id": {"month": {"$date": "2025-12-01T00:00:00Z"}, "department": "Cardiology"}, "collected": {"$numberDecimal": "17377517.95"}, "visits": 811}
{"_id": {"month": {"$date": "2025-12-01T00:00:00Z"}, "department": "Emergency"}, "collected": {"$numberDecimal": "28661193.90"}, "visits": 612}
... 64 more
```

### M6 Top 10 drugs dispensed, last 90 days ($unwind of embedded items)

```
{"_id": "Paracetamol", "units": 143834, "prescriptions": 6140}
{"_id": "Ibuprofen", "units": 65066, "prescriptions": 2799}
{"_id": "Ferrous Sulphate", "units": 60326, "prescriptions": 2486}
{"_id": "Metronidazole", "units": 57104, "prescriptions": 2391}
{"_id": "Amoxicillin", "units": 47028, "prescriptions": 1375}
{"_id": "Folic Acid", "units": 44012, "prescriptions": 1877}
{"_id": "Amlodipine", "units": 38024, "prescriptions": 1686}
{"_id": "Lisinopril", "units": 36548, "prescriptions": 1485}
... 2 more
```

### M7 Antibiotic resistance from polymorphic culture results

```
{"_id": {"organism": "E. coli", "antibiotic": "Nitrofurantoin"}, "tested": 1609, "resistant": 321, "resistance_pct": 20.0}
{"_id": {"organism": "E. coli", "antibiotic": "Ceftriaxone"}, "tested": 1634, "resistant": 325, "resistance_pct": 19.9}
{"_id": {"organism": "E. coli", "antibiotic": "Gentamicin"}, "tested": 1613, "resistant": 320, "resistance_pct": 19.8}
{"_id": {"organism": "E. coli", "antibiotic": "Ciprofloxacin"}, "tested": 1607, "resistant": 312, "resistance_pct": 19.4}
{"_id": {"organism": "E. coli", "antibiotic": "Amoxicillin-clavulanate"}, "tested": 1645, "resistant": 315, "resistance_pct": 19.1}
{"_id": {"organism": "Klebsiella spp.", "antibiotic": "Nitrofurantoin"}, "tested": 1705, "resistant": 391, "resistance_pct": 22.9}
{"_id": {"organism": "Klebsiella spp.", "antibiotic": "Ciprofloxacin"}, "tested": 1687, "resistant": 351, "resistance_pct": 20.8}
{"_id": {"organism": "Klebsiella spp.", "antibiotic": "Gentamicin"}, "tested": 1728, "resistant": 356, "resistance_pct": 20.6}
... 2 more
```

### M8 Time series: inpatients whose 12-hour moving-average temperature is above 38 C now

```
{"ts": {"$date": "2026-11-03T09:00:00Z"}, "temperature_c": 38.6, "admission_id": 9736, "ward": "Rainbow Children Ward", "temp_ma": 38.75}
{"ts": {"$date": "2026-11-03T06:00:00Z"}, "temperature_c": 38.4, "admission_id": 9735, "ward": "Rainbow Children Ward", "temp_ma": 38.73}
{"ts": {"$date": "2026-11-03T08:00:00Z"}, "temperature_c": 38.2, "admission_id": 9733, "ward": "Mary Slessor Ward", "temp_ma": 38.4}
```

### M9 Graph: departments reachable through referral chains from General Medicine doctor

```
{"_id": {"hops": 0, "department": "Obstetrics & Gynaecology"}, "referrals": 90}
{"_id": {"hops": 0, "department": "Cardiology"}, "referrals": 85}
{"_id": {"hops": 0, "department": "Surgery"}, "referrals": 76}
{"_id": {"hops": 1, "department": "General Medicine"}, "referrals": 4348}
{"_id": {"hops": 1, "department": "Surgery"}, "referrals": 1528}
{"_id": {"hops": 1, "department": "Obstetrics & Gynaecology"}, "referrals": 1528}
{"_id": {"hops": 1, "department": "Cardiology"}, "referrals": 1263}
{"_id": {"hops": 2, "department": "Surgery"}, "referrals": 1531}
... 2 more
```

### M10 Key-value: read a cached patient summary by key

```
{"_id": "patient:summary:1", "value": {"_id": 1, "hospital_no": "SGH-0000001", "name": {"first": "Patience", "last": "Bello"}, "stats": {"encounters": 293, "admissions": 11, "last_visit": {"$date": "2026-11-01T20:40:54Z"}, "total_billed": {
```
