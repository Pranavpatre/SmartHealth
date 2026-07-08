-- SmartHealth Demo Seed Data
-- 10 PHCs in Pune district, Maharashtra
-- Run after 001_core.sql

-- States & Districts
INSERT INTO states (code, name) VALUES ('MH', 'Maharashtra');

INSERT INTO districts (state_id, code, name)
VALUES ((SELECT id FROM states WHERE code = 'MH'), 'MH-PUNE', 'Pune');

-- 10 demo facilities
INSERT INTO facilities (code, district_id, name, facility_type, location, bed_capacity) VALUES
('MH-PUNE-PHC-01', (SELECT id FROM districts WHERE code='MH-PUNE'), 'Shirur PHC',         'PHC', ST_SetSRID(ST_MakePoint(74.3676, 18.8260), 4326), 12),
('MH-PUNE-PHC-02', (SELECT id FROM districts WHERE code='MH-PUNE'), 'Daund PHC',          'PHC', ST_SetSRID(ST_MakePoint(74.5825, 18.4612), 4326), 10),
('MH-PUNE-PHC-03', (SELECT id FROM districts WHERE code='MH-PUNE'), 'Baramati PHC',       'PHC', ST_SetSRID(ST_MakePoint(74.5825, 18.1560), 4326), 15),
('MH-PUNE-PHC-04', (SELECT id FROM districts WHERE code='MH-PUNE'), 'Indapur PHC',        'PHC', ST_SetSRID(ST_MakePoint(75.0175, 18.1148), 4326), 10),
('MH-PUNE-PHC-05', (SELECT id FROM districts WHERE code='MH-PUNE'), 'Bhor PHC',           'PHC', ST_SetSRID(ST_MakePoint(73.8455, 18.1508), 4326), 8),
('MH-PUNE-PHC-06', (SELECT id FROM districts WHERE code='MH-PUNE'), 'Velhe PHC',          'PHC', ST_SetSRID(ST_MakePoint(73.6516, 18.2682), 4326), 8),
('MH-PUNE-PHC-07', (SELECT id FROM districts WHERE code='MH-PUNE'), 'Mulshi PHC',         'PHC', ST_SetSRID(ST_MakePoint(73.5225, 18.5355), 4326), 10),
('MH-PUNE-PHC-08', (SELECT id FROM districts WHERE code='MH-PUNE'), 'Haveli PHC',         'PHC', ST_SetSRID(ST_MakePoint(73.9700, 18.5908), 4326), 12),
('MH-PUNE-PHC-09', (SELECT id FROM districts WHERE code='MH-PUNE'), 'Khed PHC',           'PHC', ST_SetSRID(ST_MakePoint(73.8993, 18.8492), 4326), 10),
('MH-PUNE-CHC-01', (SELECT id FROM districts WHERE code='MH-PUNE'), 'Junnar CHC',         'CHC', ST_SetSRID(ST_MakePoint(73.8795, 19.2049), 4326), 30);

-- Essential medicines catalogue
INSERT INTO medicines (name, generic_name, category, unit, reorder_level, lead_time_days) VALUES
('ORS Sachet',              'Oral Rehydration Salt',       'ORS',             'sachets', 200, 5),
('Paracetamol 500mg',       'Paracetamol',                 'ANALGESIC',       'tablets', 500, 5),
('Amoxicillin 500mg',       'Amoxicillin',                 'ANTIBIOTIC',      'capsules', 200, 7),
('Metformin 500mg',         'Metformin',                   'ANTIDIABETIC',    'tablets', 150, 7),
('Amlodipine 5mg',          'Amlodipine',                  'ANTIHYPERTENSIVE','tablets', 150, 7),
('Insulin Regular 100IU',   'Insulin',                     'ANTIDIABETIC',    'vials',    30, 10),
('Artemether+Lumefantrine', 'AL Combo',                    'ANTIMALARIAL',    'tablets', 100, 7),
('IV Fluid Normal Saline',  'Sodium Chloride 0.9%',        'OTHER',           'bottles',  50, 5),
('DPT Vaccine',             'Diphtheria+Pertussis+Tetanus','VACCINE',         'vials',    20, 14),
('Measles Vaccine',         'Measles',                     'VACCINE',         'vials',    20, 14);

-- Diagnostic tests catalogue
INSERT INTO diagnostic_tests (name, category, unit, reorder_level) VALUES
('Malaria RDT',     'Infectious Disease', 'kits',   30),
('CBC Kit',         'Haematology',        'tests',  50),
('Urine Strip',     'Urinalysis',         'strips', 100),
('Blood Glucose',   'Biochemistry',       'strips', 80),
('Dengue NS1',      'Infectious Disease', 'kits',   20),
('Typhoid Widal',   'Infectious Disease', 'kits',   20),
('HIV Rapid Test',  'Infectious Disease', 'kits',   15),
('Pregnancy Test',  'Reproductive Health','kits',   25);

-- Demo district officer user (an administrator, not a clinician)
INSERT INTO users (facility_id, district_id, role, name, phone, language_pref) VALUES
(NULL, (SELECT id FROM districts WHERE code='MH-PUNE'), 'DISTRICT_OFFICER', 'Rajesh Deshmukh', '+919876543210', 'mr');

-- Demo field workers (one per PHC)
INSERT INTO users (facility_id, district_id, role, name, phone, language_pref)
SELECT f.id, f.district_id, 'FIELD_WORKER',
       'Worker - ' || f.name,
       '+9198765' || LPAD(ROW_NUMBER() OVER ()::TEXT, 5, '0'),
       'mr'
FROM facilities f;

-- Demo PHC/CHC admin users (facility in-charge) — one for a PHC, one for the
-- CHC, so both facility_type branches of the role are exercised in the demo.
INSERT INTO users (facility_id, district_id, role, name, phone, language_pref) VALUES
((SELECT id FROM facilities WHERE code='MH-PUNE-PHC-01'),
 (SELECT district_id FROM facilities WHERE code='MH-PUNE-PHC-01'),
 'PHC_ADMIN', 'Admin - Shirur PHC', '+919876500011', 'mr'),
((SELECT id FROM facilities WHERE code='MH-PUNE-CHC-01'),
 (SELECT district_id FROM facilities WHERE code='MH-PUNE-CHC-01'),
 'PHC_ADMIN', 'Admin - Junnar CHC', '+919876500012', 'mr');

-- Stock batches: realistic inventory with some facilities deliberately low (for demo)
-- PHC-08 (Haveli) has high insulin surplus — used as donor in demo
-- PHC-01 (Shirur) has critically low insulin — triggers demo alert
INSERT INTO stock_batches (facility_id, medicine_id, batch_number, quantity, expiry_date)
SELECT
    f.id,
    m.id,
    'BATCH-' || f.code || '-' || m.id,
    CASE
        WHEN f.code = 'MH-PUNE-PHC-01' AND m.name = 'Insulin Regular 100IU' THEN 8    -- critically low
        WHEN f.code = 'MH-PUNE-PHC-08' AND m.name = 'Insulin Regular 100IU' THEN 300  -- high surplus
        WHEN f.code = 'MH-PUNE-PHC-04' AND m.name = 'Paracetamol 500mg'     THEN 900  -- surplus
        WHEN f.code = 'MH-PUNE-PHC-06' AND m.name = 'Paracetamol 500mg'     THEN 40   -- low
        WHEN f.code = 'MH-PUNE-PHC-07' AND m.name = 'ORS Sachet'            THEN 15   -- very low
        ELSE (RANDOM() * 400 + 80)::INT
    END,
    NOW()::DATE + (RANDOM() * 300 + 30)::INT   -- expiry between 30-330 days from now
FROM facilities f CROSS JOIN medicines m;

-- 90-day historical daily snapshots for time-series training
INSERT INTO daily_snapshots (time, facility_id, opd_count, ipd_count, beds_occupied,
                              doctors_present, doctors_rostered, input_channel)
SELECT
    generate_series(NOW() - INTERVAL '90 days', NOW() - INTERVAL '1 day', INTERVAL '1 day') AS time,
    f.id,
    -- Baseline footfall varies by facility size + seasonal trend
    GREATEST(0, (
        CASE f.facility_type WHEN 'CHC' THEN 180 ELSE 90 END
        + (RANDOM() * 60 - 30)::INT                          -- ±30 noise
        + CASE WHEN EXTRACT(MONTH FROM NOW()) IN (6,7,8,9)   -- monsoon spike
               THEN (RANDOM() * 40)::INT ELSE 0 END
    ))::INT,
    (RANDOM() * 5)::INT,
    (RANDOM() * (f.bed_capacity * 0.8))::INT,
    CASE WHEN RANDOM() > 0.15 THEN 2 ELSE 1 END,            -- 15% chance only 1 doctor
    2,
    'app'
FROM facilities f,
     generate_series(1, 1) gs;  -- one row per day per facility
