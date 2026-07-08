-- 010_doctors.sql
-- Per-facility doctor roster + daily per-doctor attendance. Field workers
-- maintain the doctor list for their PHC/CHC and mark each doctor present/absent
-- per day; the admin dashboard reads the roster + today's status. Idempotent.

CREATE TABLE IF NOT EXISTS doctors (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    facility_id UUID NOT NULL REFERENCES facilities(id),
    name        VARCHAR(200) NOT NULL,
    specialty   VARCHAR(120),
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_doctors_facility ON doctors(facility_id);

CREATE TABLE IF NOT EXISTS doctor_attendance (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    doctor_id       UUID NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
    facility_id     UUID NOT NULL REFERENCES facilities(id),
    attendance_date DATE NOT NULL,
    present         BOOLEAN NOT NULL DEFAULT FALSE,
    marked_by       UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (doctor_id, attendance_date)
);
CREATE INDEX IF NOT EXISTS idx_doctor_attendance_fac_date
    ON doctor_attendance(facility_id, attendance_date);

-- Seed a couple of demo doctors for each seeded facility so the roster isn't
-- empty on first use (only where none exist yet).
-- Two demo doctors per facility, names varied per-facility (deterministic hash
-- into a name pool) so the roster looks realistic rather than the same two names
-- everywhere. These are the clinicians on staff (distinct from the admin users).
INSERT INTO doctors (facility_id, name, specialty)
SELECT f.id,
       (ARRAY['Dr. Priya Sharma','Dr. Rahul Verma','Dr. Anjali Nair','Dr. Vikram Singh',
              'Dr. Meera Iyer','Dr. Arjun Reddy','Dr. Kavita Joshi','Dr. Sanjay Gupta',
              'Dr. Neha Deshpande','Dr. Amit Patil','Dr. Sunita Rao','Dr. Rajesh Menon'])
             [1 + ((abs(hashtext(f.id::text)) + g) % 12)],
       (ARRAY['General Medicine','Pediatrics','Obstetrics','General Medicine'])[1 + (g % 4)]
FROM facilities f
CROSS JOIN generate_series(0, 1) g
WHERE NOT EXISTS (SELECT 1 FROM doctors dd WHERE dd.facility_id = f.id)
  AND f.facility_type IN ('PHC', 'CHC');
