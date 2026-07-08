-- SmartHealth — Bed "occupied until" date (Project Pulse bed-matrix extension)
-- Run after 006_beds_and_tests.sql.
--
-- The field worker records, per bed type, the date by which the currently
-- occupied beds are expected to free up (patients' expected discharge). This
-- lets the admin dashboard project bed availability forward: a bed occupied
-- "until 2026-07-20" is known to be free after that date, so future-availability
-- calculations don't have to assume today's occupancy is permanent.

ALTER TABLE facility_beds ADD COLUMN IF NOT EXISTS occupied_until DATE;

COMMENT ON COLUMN facility_beds.occupied_until IS
    'Expected date the currently-occupied beds of this type free up (field-entered).';
