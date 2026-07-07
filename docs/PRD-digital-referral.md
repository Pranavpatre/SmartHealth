# PRD — Digital Patient Referral (PHC/CHC → District Hospital)

**Product:** PrediCare (SmartHealth)
**Status:** Draft for review
**Author:** Pranav (with Claude)
**Date:** 2026-07-07
**Origin:** Suggestion from Hari Sir — let a patient's referral + record travel digitally from the PHC/CHC to the district hospital so the patient skips re-queuing and re-explaining, and any hospital can see their past history.

---

## 1. Problem

When a PHC/CHC refers a patient "up" to a district hospital (DH), today the patient:
- carries a paper slip (often lost or illegible),
- re-queues at the DH and re-registers from scratch,
- re-explains their history; prior vitals, diagnoses and tests don't travel with them.

There is **no digital thread** connecting the two visits. PrediCare currently models facilities, stock, staffing and **aggregate** footfall — it has **no patient-level record** at all.

## 2. Goal

A **completely digital, consent-based referral** that:
1. Is created at the PHC/CHC in under a minute.
2. Is delivered to the patient's phone (WhatsApp) as a QR + code + link — no paper.
3. Can be pulled up at the district hospital instantly by **QR**, **referral code**, or **phone + OTP** (fallback if the QR/code is lost).
4. Shows the DH the referral + attached clinical summary, so the patient skips intake re-entry.
5. Accumulates into a **longitudinal record** that "floats" to any facility the patient consents to share with — the first step toward ABDM interoperability.

### Success signals
- % of DH arrivals that present a digital referral (vs paper / none).
- Median DH intake time for referred vs walk-in patients.
- % of referrals retrieved by QR vs code vs phone+OTP (validates the fallback).

## 3. Non-goals (for this MVP)

- Not a full HMIS/EHR (no billing, ADT, lab-order management, imaging).
- Not building a parallel national health-record store — long term we federate via **ABDM** (see §11), not centralize.
- No clinical decision support.

## 4. Users & roles

| Actor | Does |
|---|---|
| **PHC/CHC staff** (`FIELD_WORKER` / `PHC_ADMIN`) | Creates the referral, attaches summary, triggers delivery. |
| **Doctor / Hospital staff** (`HOSPITAL_STAFF`, authenticated) | Searches by patient **name/phone**, opens the referral + history, appends the visit outcome. |
| **Patient** | Receives the QR/code on WhatsApp; approves *out-of-referral* sharing via OTP (consent). |
| **District/State officers** | Aggregate view only (referral volumes, PHC→DH flows) — no PII by default. |

> **Access model (decided):** authenticated **doctor/hospital-staff** only — **no kiosk / self-service page**. The doctor knows the patient's full name and **searches the database** to pull up the record. Consent is handled by the two-tier rule in §6 (referral = implied consent; anything beyond needs OTP or break-glass).

## 5. Core flows

### 5.1 Create referral (PHC/CHC)
1. Staff opens "Refer patient", enters: patient **name, phone, age/sex**, reason for referral, short **clinical summary** (vitals, provisional diagnosis, meds given), and **referred-to** (a specific DH, or "any district hospital").
2. On save, the system:
   - creates/links a minimal **patient** record (by phone; ABHA optional later),
   - creates a **referral** with a short human-readable **code** (e.g. `BID-7Q4K2`) and a signed **QR/deep-link token**,
   - sets an **expiry** (default 30 days) and status `CREATED`.
3. Delivery is triggered (§5.2).

### 5.2 Deliver (fully digital)
- Send the patient a WhatsApp message: greeting + referred-to hospital + **referral code** + **link** (opens the retrieval page) + **QR image**.
- **Graceful degrade:** the create response always returns the code + link + QR so the PHC screen can show/print/re-share it even when WhatsApp isn't configured. WhatsApp auto-sends once creds exist (§10).

### 5.3 Retrieve at the District Hospital (authenticated doctor/hospital staff)

The doctor is logged in and pulls up the patient by any of:
- **A. Search by name / phone** — primary path. Returns matches from **referrals directed to this facility** (see consent §6). Disambiguate by name + age/sex when a phone is shared.
- **B. Scan QR** — opens the referral directly via the signed token (patient possession = consent).
- **C. Referral code** — type the short code.
- **D. Phone + OTP** — used only to unlock records **beyond** this facility's referrals (full cross-facility history, or a walk-in with no referral). OTP goes to the patient's WhatsApp.

No kiosk / unauthenticated page. Every open is audit-logged against the doctor's identity.

### 5.4 View + close the loop
- DH sees: referral header, clinical summary, and the patient's **prior referrals/visits** (their history so far).
- DH staff can append a **visit outcome** (diagnosis, action, follow-up), which becomes part of the floating record for the next facility.
- Referral status: `CREATED → DELIVERED → VIEWED → COMPLETED` (or `EXPIRED`).

## 6. Consent & privacy — the two-tier rule

The pivotal question — *does the doctor need patient credentials?* — is resolved by **what they're accessing**:

- **Tier 1 — patient referred to this facility → NO credential needed.** The PHC's act of referring the patient *to this hospital* is the consent. The doctor searches by name/phone and opens the referral directly. This is the frictionless path Hari Sir asked for. Still: authenticated doctor + audit-logged.
- **Tier 2 — anything beyond that (walk-in with no referral, or the patient's full history held at *other* facilities) → credential required.** Either the patient's **phone-OTP** (they authorize in the moment) or a logged **"break-glass" emergency reason**.

Guardrails:
- **Search is scoped** to Tier-1 (this facility's referrals) by default; reaching Tier-2 is an explicit, gated action — prevents free-text fishing across the whole population.
- **Access log** on every view: doctor id, patient, tier, method, reason (for break-glass), timestamp. Surfaced to the patient on request.
- **Data minimization:** least PII (name, phone, age/sex). No Aadhaar. ABHA preferred over raw PII long term.
- **DPDP Act 2023:** retention of the *record* is indefinite (§7), but **consent to share is separate, time-boxable, and revocable**, and the patient can request erasure. Retention ≠ perpetual open access.
- **Transport/at-rest:** HTTPS only; QR tokens signed + expiring; PII columns are a candidate for column-level encryption in a hardening pass.

## 7. Data model (additive; nothing patient-level exists today)

```
patients
  id (uuid, pk)
  phone (text, indexed)         -- primary lookup key
  name (text)
  sex (enum), year_of_birth (int)
  abha_id (text, nullable)      -- ABDM linkage, later
  created_at

referrals
  id (uuid, pk)
  patient_id (fk -> patients)
  from_facility_id (fk -> facilities)
  to_facility_id (fk -> facilities, nullable = "any DH")
  code (text, unique, short)    -- e.g. BID-7Q4K2
  reason (text)
  clinical_summary (jsonb)      -- vitals, provisional dx, meds
  status (enum: CREATED|DELIVERED|VIEWED|COMPLETED|EXPIRED)
  created_by (fk -> users)
  created_at, expires_at, delivered_at, viewed_at, completed_at

referral_access_log            -- consent/audit trail
  id, referral_id (fk), accessed_by (text: user uuid | 'kiosk')
  method (enum: QR|CODE|OTP), accessed_at, ip (nullable)

visit_notes                    -- the "floating history" the DH appends
  id, referral_id (fk), facility_id (fk), author_id (fk -> users)
  note (jsonb: dx, action, follow_up), created_at
```

Reuses existing `facilities` and `users`. Consistent with the current flat-migration cloud DB; indexes needed: `patients(phone)`, `referrals(name)` / trigram for name search, `referrals(code)`, `referrals(patient_id)`, `referrals(to_facility_id)` (for the doctor's facility-scoped search).

**New role:** add `HOSPITAL_STAFF` (doctor / DH staff) to the user role enum. Search + view is scoped to Tier-1 (their facility's referrals) per §6.

**Retention:** the patient record + `visit_notes` are retained **indefinitely** (a lifelong longitudinal history is the point). `expires_at` on a referral governs only the **QR link / share-consent window** (default 30 days), *not* deletion of the record. DPDP erasure-on-request still overrides (consent withdrawal → stop sharing / delete on demand).

## 8. API (FastAPI, under `/api/v1/referrals`)

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| POST | `/referrals` | PHC staff | Create referral + patient (returns code, link, QR data URL) |
| POST | `/referrals/{id}/deliver` | PHC staff | (Re)send via WhatsApp |
| POST | `/referrals/lookup/otp/request` | public | Send OTP to a patient phone (rate-limited) |
| POST | `/referrals/lookup/otp/verify` | public | Verify OTP → session token → list patient's referrals |
| GET | `/referrals/by-code/{code}` | staff or kiosk-session | Fetch a referral (logs access) |
| GET | `/referrals/token/{signed}` | QR link | Fetch via signed QR token (logs access) |
| POST | `/referrals/{id}/visit-note` | DH staff | Append visit outcome |
| GET | `/referrals?district_id=` | officer | Aggregate list (no PII) |

Reuse the existing OTP pattern from `backend/api/auth/router.py` (`_otp_store`, 6-digit, 10-min TTL) — generalized to patient phones with rate-limiting; dev-OTP `000000` still works in non-prod for demos.

## 9. Screens

- **PHC/CHC — "Refer a patient"** (new dashboard page + field-app screen): form → success card showing QR + code + "Sent on WhatsApp" (or "Show/print QR" fallback).
- **District Hospital — "Retrieve referral"** (authenticated dashboard page): tabs for *Scan QR* / *Enter code* / *Phone + OTP* → referral detail + history + "Add outcome".
- **Kiosk / self-service page** (standalone, no nav/login): same three retrieval methods, single-referral view, session-scoped, big touch targets, 10-language.
- **Officer view:** referral volume + PHC→DH flow counts (extends existing dashboard; no PII).

All screens localized across the existing 10 languages.

## 10. WhatsApp integration

- Reuse `backend/integrations/whatsapp.py` (`WhatsAppClient`, Meta Cloud API `/messages`).
- **Deferred for now (per decision #4):** WhatsApp provisioning is *not* being set up yet. The MVP runs on **graceful degrade** — the app returns/shows the QR + code + link on the PHC screen so it's fully demoable and usable without WhatsApp.
- **When ready later:** provision a WhatsApp Business number, permanent access token, `phone_number_id`, and a **Meta-approved template**; set `WHATSAPP_TOKEN` + `WHATSAPP_PHONE_NUMBER_ID` on Cloud Run (currently unset). Send then flips on automatically — no code change.
- SMS can be added as a secondary channel later (same OTP/delivery abstraction).

## 11. ABDM integration (Phase 3) — the real end-state

The "one report that floats to all hospitals" is exactly what India's **ABDM** (Ayushman Bharat Digital Mission) provides — the compliant way is federated-with-consent, not one central store. ABDM exposes three registries/components we integrate with:

| Component | What it is | How PrediCare uses it | Link field |
|---|---|---|---|
| **ABHA** | 14-digit Ayushman Bharat Health Account — the citizen's portable health identity | Key the patient record to ABHA; via **HIE-CM** pull their past records (labs, prescriptions) from other ABDM providers on consent | `patients.abha_id` |
| **HPR** | Healthcare Professionals Registry — verified doctors/nurses | Verify a `HOSPITAL_STAFF` user is a real registered professional at onboarding | `users.hpr_id` |
| **HFR** | Health Facility Registry — public + private hospitals/clinics/labs | Link our 52k facilities to their official HFR ids; discover the right district hospital to refer to | `facilities.hfr_id` |

**Linkage fields are already in the schema (added 2026-07-07)** — `patients.abha_id`, `users.hpr_id`, `facilities.hfr_id` (all nullable, unused until integration). No further migration needed to start.

**Access model — gated, two environments:**
- **Sandbox** (`sandbox.abdm.gov.in`): free self-registration → `client_id`/`client_secret`; test against **synthetic** data. This is where the POC/demo integration runs.
- **Production** (real citizen records): requires the org to register as a **HIU/HIP** (Health Information User/Provider), pass **milestone-based certification**, and meet security/DPDP compliance — an organizational onboarding process, not a code change.

**Planned integration surface** (new `backend/api/integrations/abdm.py` client, gated on sandbox creds):
- **ABHA**: create / verify ABHA (OTP via Aadhaar or mobile) → store `abha_id` on the patient.
- **HFR**: search facilities → populate `hfr_id`; use official facility identity for `to_facility`.
- **HPR**: verify professional → populate `hpr_id` on the doctor's account.
- **HIE-CM (records)**: raise a **consent request** → patient approves → fetch **FHIR** care-context bundles → render alongside our `visit_notes`. Our existing OTP-consent + `referral_access_log` maps directly onto ABDM's consent-artefact + audit model.
- **FHIR**: shape `clinical_summary` / `visit_notes` as FHIR resources for exchange.

**Sequencing:** the MVP already builds the workflow + consent UX; Phase 3 swaps the internal store for ABDM linkage **without changing the user experience** — the doctor still searches by name; behind the scenes the record is pulled from ABDM with consent. First step is obtaining ABDM **sandbox** credentials (org self-registration); then the `abdm.py` client + wiring can be built against synthetic data.

> Security note: never call ABDM production endpoints without authorization; the sandbox uses synthetic data and is the correct surface for development/demos. Do not hardcode endpoints — follow the official ABDM sandbox API docs.

## 12. Phasing

- **Phase 1 (MVP) — DONE & LIVE:** patient + referral model, create at PHC, WhatsApp delivery (graceful degrade), retrieval by name-search / code / phone-OTP (authenticated doctor), two-tier consent + access log, DH visit-note. 10-language UI. Synthetic/demo data.
- **Phase 2:** basic FHIR shaping; SMS fallback; document attachments; patient-facing "who accessed my record". (ABDM linkage fields `abha_id`/`hfr_id`/`hpr_id` already added to the schema.)
- **Phase 3 — ABDM integration (see §11):** obtain sandbox creds → `abdm.py` client (ABHA verify/create, HFR facility link, HPR professional verify, HIE-CM consent + FHIR record pull); then production HIU/HIP certification. Plus retention/consent-expiry automation and column-level PII encryption.

## 13. Decisions & remaining questions

**Resolved (2026-07-07):**
- **No kiosk.** Access is authenticated **doctor/hospital-staff** only; the doctor searches by patient name/phone. (#1)
- **Doctor credentials rule:** two-tier — referral-to-this-facility needs no patient credential; out-of-referral access needs OTP or break-glass (§6). (#1)
- **Role:** new **`HOSPITAL_STAFF`** (doctor / DH staff). (#2)
- **Retention:** record retained **forever**; only the share-link/consent window expires. (#3)
- **WhatsApp:** provisioning deferred; graceful-degrade for now. (#4)

**Still open:**
1. **Patient de-duplication** when a phone is shared/reused (family) or a name has many matches — confirm disambiguation UX (name + age/sex + village/facility).
2. **Break-glass policy:** what justifications are valid, and who reviews the break-glass log?
3. **Search reach:** should a doctor ever search *all* facilities' patients (not just referrals to them) with consent, or is name-search always facility-scoped?

## 14. Demo plan

Seed a few synthetic patients + referrals in the already-seeded districts (e.g. Bidar). Show: PHC creates a referral → QR/link shown on screen (WhatsApp-ready) → doctor at the DH **searches by name** → opens the referral + summary → adds the visit outcome → (optional) demonstrate phone+OTP unlocking a record from another facility. Clearly badged **demo data**.
