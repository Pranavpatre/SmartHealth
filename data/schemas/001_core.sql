-- SmartHealth Core Schema
-- PostgreSQL + TimescaleDB
-- Run: psql -U smarthealth -f 001_core.sql

-- ─────────────────────────────────────────────
-- Extensions
-- ─────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS postgis;       -- for distance-matrix queries
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ────────────────────────────────────────────
-- Reference / lookup tables
-- ────────────────────────────────────────────

CREATE TABLE states (
    id          SERIAL PRIMARY KEY,
    code        VARCHAR(5)  NOT NULL UNIQUE,   -- e.g. MH, TN, KA
    name        VARCHAR(100) NOT NULL
);

CREATE TABLE districts (
    id          SERIAL PRIMARY KEY,
    state_id    INT NOT NULL REFERENCES states(id),
    code        VARCHAR(10) NOT NULL UNIQUE,
    name        VARCHAR(100) NOT NULL
);

CREATE TYPE facility_type AS ENUM ('PHC', 'CHC', 'SUB_CENTRE', 'DISTRICT_HOSPITAL');

CREATE TABLE facilities (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    district_id     INT NOT NULL REFERENCES districts(id),
    code            VARCHAR(20) NOT NULL UNIQUE,  -- e.g. MH-PUNE-PHC-021
    name            VARCHAR(200) NOT NULL,
    facility_type   facility_type NOT NULL,
    location        GEOMETRY(Point, 4326),         -- lat/lng for distance calc
    address         TEXT,
    bed_capacity    INT NOT NULL DEFAULT 10,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX facilities_district_idx ON facilities(district_id);
CREATE INDEX facilities_location_idx ON facilities USING GIST(location);

-- ─────────────────────────────────────────────
-- Users & RBAC
-- ────────────────────────────────────────────

CREATE TYPE user_role AS ENUM ('FIELD_WORKER', 'PHC_ADMIN', 'DISTRICT_OFFICER', 'STATE_ADMIN', 'SUPERADMIN');

CREATE TABLE users (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    facility_id     UUID REFERENCES facilities(id),   -- NULL for district/state roles
    district_id     INT REFERENCES districts(id),
    role            user_role NOT NULL,
    name            VARCHAR(200) NOT NULL,
    phone           VARCHAR(15) NOT NULL UNIQUE,      -- used for WhatsApp auth
    language_pref   VARCHAR(10) NOT NULL DEFAULT 'hi', -- BCP-47: hi, mr, ta, te, kn, bn, gu, or
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID REFERENCES users(id),
    action          VARCHAR(100) NOT NULL,
    table_name      VARCHAR(100),
    record_id       TEXT,
    old_value       JSONB,
    new_value       JSONB,
    ip_address      INET,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────
-- Medicine & Supplies Catalogue
-- ─────────────────────────────────────────────

CREATE TYPE medicine_category AS ENUM (
    'ESSENTIAL', 'ANTIBIOTIC', 'VACCINE', 'ORS', 'ANALGESIC',
    'ANTIDIABETIC', 'ANTIHYPERTENSIVE', 'ANTIMALARIAL', 'DIAGNOSTICS_KIT',
    'REAGENT', 'EQUIPMENT', 'OTHER'
);

CREATE TABLE medicines (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    generic_name    VARCHAR(200),
    category        medicine_category NOT NULL,
    unit            VARCHAR(20) NOT NULL DEFAULT 'units',  -- tablets, vials, strips, kits
    reorder_level   INT NOT NULL DEFAULT 50,               -- district-wide default
    lead_time_days  INT NOT NULL DEFAULT 7,                -- supplier lead time
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

-- ─────────────────────────────────────────────
-- Stock Inventory (FEFO batches)
-- ─────────────────────────────────────────────

CREATE TABLE stock_batches (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    facility_id     UUID NOT NULL REFERENCES facilities(id),
    medicine_id     INT NOT NULL REFERENCES medicines(id),
    batch_number    VARCHAR(50),
    quantity        INT NOT NULL CHECK (quantity >= 0),
    expiry_date     DATE NOT NULL,
    received_at     TIMESTAMPTZ DEFAULT NOW(),
    received_by     UUID REFERENCES users(id)
);

CREATE INDEX stock_batches_facility_medicine ON stock_batches(facility_id, medicine_id);
CREATE INDEX stock_batches_expiry ON stock_batches(expiry_date);

-- Aggregate view: current stock per facility per medicine (FEFO order)
CREATE VIEW facility_stock AS
SELECT
    facility_id,
    medicine_id,
    SUM(quantity)                                       AS total_quantity,
    MIN(expiry_date)                                    AS earliest_expiry,
    COUNT(*) FILTER (WHERE expiry_date < NOW() + INTERVAL '30 days') AS batches_expiring_soon
FROM stock_batches
WHERE quantity > 0
GROUP BY facility_id, medicine_id;

-- ─────────────────────────────────────────────
-- Daily Operational Snapshots (TimescaleDB hypertable)
-- ─────────────────────────────────────────────

CREATE TABLE daily_snapshots (
    time            TIMESTAMPTZ NOT NULL,
    facility_id     UUID NOT NULL REFERENCES facilities(id),
    recorded_by     UUID REFERENCES users(id),
    -- Patient footfall
    opd_count       INT NOT NULL DEFAULT 0,
    ipd_count       INT NOT NULL DEFAULT 0,
    emergency_count INT NOT NULL DEFAULT 0,
    -- Beds
    beds_occupied   INT NOT NULL DEFAULT 0,
    beds_available  INT,                            -- derived if NULL: capacity - occupied
    -- Staff
    doctors_present INT NOT NULL DEFAULT 0,
    doctors_rostered INT NOT NULL DEFAULT 0,
    nurses_present  INT NOT NULL DEFAULT 0,
    -- Input channel
    input_channel   VARCHAR(20) DEFAULT 'app',      -- app | voice | whatsapp | sms
    language_used   VARCHAR(10),
    notes           TEXT
);

SELECT create_hypertable('daily_snapshots', 'time');
CREATE INDEX daily_snapshots_facility_time ON daily_snapshots(facility_id, time DESC);

-- ────────────────────────────────────────────
-- Diagnostics / Test Availability
-- ────────────────────────────────────────────

CREATE TABLE diagnostic_tests (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,       -- Malaria RDT, CBC Kit, Urine Strip
    category        VARCHAR(100),
    unit            VARCHAR(20) DEFAULT 'tests',
    reorder_level   INT NOT NULL DEFAULT 20
);

CREATE TABLE diagnostic_stock_snapshots (
    time            TIMESTAMPTZ NOT NULL,
    facility_id     UUID NOT NULL REFERENCES facilities(id),
    test_id         INT NOT NULL REFERENCES diagnostic_tests(id),
    quantity        INT NOT NULL DEFAULT 0,
    equipment_status VARCHAR(20) DEFAULT 'operational',  -- operational | under_repair | down
    recorded_by     UUID REFERENCES users(id)
);

SELECT create_hypertable('diagnostic_stock_snapshots', 'time');

-- ─────────────────────────────────────────────
-- AI Predictions
-- ────────────────────────────────────────────

CREATE TYPE prediction_type AS ENUM (
    'STOCKOUT', 'FOOTFALL', 'DIAGNOSTIC_SHORTAGE', 'ANOMALY', 'HEALTH_SCORE'
);

CREATE TABLE ai_predictions (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    facility_id     UUID NOT NULL REFERENCES facilities(id),
    medicine_id     INT REFERENCES medicines(id),      -- NULL for footfall/anomaly types
    test_id         INT REFERENCES diagnostic_tests(id),
    prediction_type prediction_type NOT NULL,
    predicted_at    TIMESTAMPTZ DEFAULT NOW(),
    horizon_days    INT NOT NULL DEFAULT 3,            -- how far ahead
    predicted_value NUMERIC(10,2),                    -- e.g. days until stockout, expected patients
    confidence      NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    reasoning       JSONB,                            -- {"monsoon": 0.20, "dengue_trend": 0.15, ...}
    recommendation  TEXT,
    model_version   VARCHAR(50),
    -- Outcome tracking for retraining
    actual_value    NUMERIC(10,2),                    -- filled post-hoc
    worker_feedback VARCHAR(20),                      -- correct | wrong | partial
    feedback_at     TIMESTAMPTZ
);

CREATE INDEX ai_predictions_facility ON ai_predictions(facility_id, predicted_at DESC);
CREATE INDEX ai_predictions_type ON ai_predictions(prediction_type, predicted_at DESC);

-- ─────────────────────────────────────────────
-- Resource Redistribution
-- ─────────────────────────────────────────

CREATE TYPE transfer_status AS ENUM (
    'PENDING', 'APPROVED', 'DEFERRED', 'IN_TRANSIT', 'COMPLETED', 'CANCELLED'
);

CREATE TABLE redistribution_plans (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    district_id     INT NOT NULL REFERENCES districts(id),
    generated_at    TIMESTAMPTZ DEFAULT NOW(),
    approved_by     UUID REFERENCES users(id),
    approved_at     TIMESTAMPTZ,
    status          transfer_status NOT NULL DEFAULT 'PENDING',
    total_savings   NUMERIC(12,2),                    -- estimated ₹ savings
    notes           TEXT
);

CREATE TABLE redistribution_items (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    plan_id         UUID NOT NULL REFERENCES redistribution_plans(id),
    medicine_id     INT REFERENCES medicines(id),
    test_id         INT REFERENCES diagnostic_tests(id),
    from_facility   UUID NOT NULL REFERENCES facilities(id),
    to_facility     UUID NOT NULL REFERENCES facilities(id),
    quantity        INT NOT NULL,
    distance_km     NUMERIC(6,2),
    estimated_cost  NUMERIC(10,2),
    estimated_saving NUMERIC(10,2),
    status          transfer_status NOT NULL DEFAULT 'PENDING',
    trigger_prediction UUID REFERENCES ai_predictions(id)
);

-- ─────────────────────────────────────────────
-- Facility Health Scores (daily snapshot)
-- ─────────────────────────────────────────────

CREATE TABLE facility_health_scores (
    time                TIMESTAMPTZ NOT NULL,
    facility_id         UUID NOT NULL REFERENCES facilities(id),
    medicine_score      NUMERIC(5,2),   -- 0-100, weight 25%
    doctor_score        NUMERIC(5,2),   -- weight 20%
    bed_score           NUMERIC(5,2),   -- weight 20%
    wait_time_score     NUMERIC(5,2),   -- weight 20%
    diagnostics_score   NUMERIC(5,2),   -- weight 15%
    overall_score       NUMERIC(5,2),   -- weighted composite
    status              VARCHAR(10)     -- GREEN | YELLOW | RED
);

SELECT create_hypertable('facility_health_scores', 'time');

-- ─────────────────────────────────────────────
-- Alerts & Notifications
-- ─────────────────────────────────────────────

CREATE TYPE alert_severity AS ENUM ('INFO', 'WARNING', 'CRITICAL');
CREATE TYPE alert_status AS ENUM ('OPEN', 'ACKNOWLEDGED', 'RESOLVED', 'SNOOZED');

CREATE TABLE alerts (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    facility_id     UUID NOT NULL REFERENCES facilities(id),
    prediction_id   UUID REFERENCES ai_predictions(id),
    severity        alert_severity NOT NULL,
    status          alert_status NOT NULL DEFAULT 'OPEN',
    title           VARCHAR(300) NOT NULL,
    body            TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    acknowledged_by UUID REFERENCES users(id),
    acknowledged_at TIMESTAMPTZ,
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX alerts_facility_status ON alerts(facility_id, status, created_at DESC);
CREATE INDEX alerts_open ON alerts(status, severity) WHERE status = 'OPEN';

-- Notification delivery log (WhatsApp, SMS, push)
CREATE TABLE notifications (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    alert_id        UUID REFERENCES alerts(id),
    user_id         UUID NOT NULL REFERENCES users(id),
    channel         VARCHAR(20) NOT NULL,   -- whatsapp | sms | push | in_app
    language        VARCHAR(10) NOT NULL,
    message         TEXT NOT NULL,
    sent_at         TIMESTAMPTZ,
    delivered_at    TIMESTAMPTZ,
    response        VARCHAR(20),            -- YES | NO | DEFER (for WhatsApp actions)
    response_at     TIMESTAMPTZ
);

-- ────────────────────────────────────────────
-- Procurement Escalation
-- ────────────────────────────────────────────

CREATE TYPE procurement_status AS ENUM ('FLAGGED', 'REVIEWED', 'ORDERED', 'DELIVERED', 'CLOSED');

CREATE TABLE procurement_flags (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    district_id     INT NOT NULL REFERENCES districts(id),
    medicine_id     INT REFERENCES medicines(id),
    test_id         INT REFERENCES diagnostic_tests(id),
    flagged_at      TIMESTAMPTZ DEFAULT NOW(),
    flagged_by      UUID REFERENCES users(id),  -- system or manual
    reason          TEXT NOT NULL,
    status          procurement_status NOT NULL DEFAULT 'FLAGGED',
    quantity_needed INT,
    notes           TEXT
);

-- ────────────────────────────────────────────
-- Disease / Outbreak Calendar
-- ─────────────────────────────────────────────

CREATE TABLE disease_events (
    id              SERIAL PRIMARY KEY,
    district_id     INT NOT NULL REFERENCES districts(id),
    disease_name    VARCHAR(100) NOT NULL,  -- dengue, malaria, cholera
    start_date      DATE NOT NULL,
    end_date        DATE,
    severity        VARCHAR(20),            -- low | moderate | high | outbreak
    source          VARCHAR(100),           -- IDSP, manual, HMIS
    notes           TEXT
);
