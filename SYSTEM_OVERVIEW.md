# SankofaWatch — System Overview

---

## 1. What the System Is

SankofaWatch is a web-based platform for detecting illegal (galamsey) mining activity in Ghana using satellite imagery and an AI model. An admin user defines an area on a map, the system fetches satellite data for that area, runs it through a deep learning model, and produces a map of detected mining sites. If any of those sites fall outside registered mining concessions, the system flags them as illegal, raises alerts, and dispatches field inspectors to verify on the ground.

The system is built in **Django** (Python), uses **PostgreSQL + PostGIS** for spatial data, **Celery** for async task processing, **Google Earth Engine (GEE)** as the satellite data source for detection, and **Planet Labs** (Education Program via Planet Data API) for historical timelapse imagery.

---

## 2. Users and Roles

The system has exactly **three user roles**:

### System Admin
- Global access across all organisations
- After login, routed to `/dashboard/admin/`
- Sees all jobs, detections, and alerts regardless of which organisation created them
- Manages all user accounts, organisations, and system-wide settings
- Can trigger manual scans and manage the automated scanning configuration
- Receives email notifications when scans complete, fail, or when field reports come in
- Receives escalation emails when critical alerts remain in OPEN or ACKNOWLEDGED status for 48+ hours without inspector action
- Can see the audit log of all system actions

### Agency Admin
- Organisation-scoped operator — sees only their own organisation's jobs and alerts, plus all automated scans
- After login, routed to `/dashboard/home/`
- Can draw an Area of Interest (AOI) on the map and trigger a detection scan
- Reviews detection results and alerts scoped to their organisation
- Assigns field inspectors to alerts
- Manages user accounts within their organisation

### Inspector
- A field officer from EPA, Minerals Commission, Forestry Commission, CERSGIS, or other organisation
- After login, routed to `/dashboard/inspector/` — a separate, limited view showing only their assigned alerts
- Receives email/in-app notification when assigned to a site
- Visits the physical location and submits a field report: outcome (mining confirmed / false positive / inconclusive), visit date, GPS coordinates, field notes, and photo evidence
- Receives SLA reminder when their assignment is past the 5-day due date; the system escalates to admins if 2+ days past the due date (day 7+)
- If three consecutive inconclusive field reports are submitted for the same alert, the system automatically escalates that alert to CRITICAL severity

**There is no researcher, NGO, or public data access role in the system.**

---

## 3. Organisations

Organisations are modelled as a dedicated `Organisation` database table (UUID primary key). Each user's `UserProfile` has a foreign key to an `Organisation` record. The current organisations are:
- Environmental Protection Agency (EPA)
- Minerals Commission
- CERSGIS
- Forestry Commission
- Other

Organisation membership **does** affect data access: an agency admin can only see jobs and alerts created by users in their own organisation, plus all automated scans. System admins see all data regardless of organisation.

---

## 4. The Detection Pipeline

This is the core technical process. It runs as an async Celery task and goes through these steps in sequence:

### Step 1 — Job Creation
An admin draws a polygon on the map (an AOI, Area of Interest, between 10–60 ha) and clicks "Scan AOI". This creates a `Job` record in the database with `status=QUEUED`. The frontend then polls the job status every 3 seconds to show progress.

### Step 2 — Validation and GEE Export (Validating → Exporting)
The job is first validated (AOI geometry, date range), then the system authenticates with Google Earth Engine using a service account and calls the GEE API to fetch **Harmonised Landsat and Sentinel-2 (HLS)** imagery for the AOI within the selected date range. The `NASA/HLS/HLSS30/v002` collection is loaded, filtered to the date range, cloud-masked using the HLS Fmask quality band, and a median composite is calculated. GEE clips the composite to the AOI and downloads it as a GeoTIFF to local storage. The exported GeoTIFF contains raw spectral bands: **B2 (Blue), B3 (Green), B4 (Red), B8 (NIR), B11 (SWIR-1), and B12 (SWIR-2)**.

### Step 3 — Preprocessing
The downloaded GeoTIFF is read with `rasterio`. The six model input bands are extracted: B3, B4, B8, B11, and B12 are taken directly, and a **Bare Soil Index (BSI)** is computed pixel-by-pixel from the raw bands using the formula `((SWIR-1 + Red) − (NIR + Blue)) / ((SWIR-1 + Red) + (NIR + Blue))`. Each reflectance band is normalised to [0, 1] using per-patch p2–p98 percentile stretching. BSI is shifted from [−1, 1] to [0, 1]. The six bands are stacked into a normalised float32 tensor ready for the model.

### Step 4 — Inference (Inferring)
The tensor is passed to a **Feature Pyramid Network (FPN) with a ResNet-50 encoder** — a segmentation model trained to identify surface disturbance patterns associated with illegal mining. The model outputs a **probability mask** (a per-pixel floating-point score from 0 to 1) over the AOI.

### Step 5 — Postprocessing
The probability mask is thresholded at 0.5 and converted from a raster to **vector polygons** using rasterio's shape-tracing function. Polygons are simplified with Douglas-Peucker and reprojected to WGS84. **Polygons smaller than 100m² are discarded.** A confidence score (mean pixel probability within each polygon) is computed for each surviving polygon. Each polygon becomes a potential `DetectedSite`.

### Step 6 — Deduplication
Before saving new sites, the system checks if an existing site centroid is within 500 m of the new detection. If so, it increments that site's `recurrence_count` instead of creating a duplicate. A recurring site triggers a higher-severity alert.

### Step 7 — Legal Classification (Storing)
Each detected polygon is spatially joined against the `LegalConcession` table (1,069 Ghana Minerals Commission concession boundaries). The primary check uses PostGIS's `contains` predicate on the site's centroid. If the centroid falls exactly on a boundary edge, a fallback `intersects` check is used. If either check finds a matching concession, the site is marked `legal`; otherwise it is marked `illegal`. For sites that intersect a concession, the overlap percentage (intersection area ÷ site area) is calculated and stored in the `DetectedSite` record for informational purposes only — it does not affect the legal/illegal classification.

### Step 8 — Alert Generation
For each `illegal` detected site, the system checks if an active alert already exists (status OPEN, ACKNOWLEDGED, or DISPATCHED) to avoid duplicates. If not, it creates an `Alert` with a severity and type determined by four rules applied in priority order:
- **CRITICAL** / type `RECURRING_SITE` — recurrence count greater than 1 (site detected before)
- **HIGH** / type `HIGH_CONFIDENCE` — model confidence score ≥ 85%
- **HIGH** / type `EXPANSION_DETECTED` — site area greater than 5 ha
- **MEDIUM** / type `NEW_DETECTION` — all other new detections

If three consecutive inconclusive field reports are submitted for the same alert, the system automatically escalates that alert to CRITICAL severity.

### Step 9 — ML Visualisation Images
The system generates 4 PNG images per job and per site:
- False colour composite (NIR/Red/Green)
- Binary prediction mask
- Probability heatmap
- Overlay (prediction on top of false colour)

### Step 10 — Timelapse Fetch (async, background queue)
For each newly detected site, a separate Celery task fetches historical satellite imagery from **Planet Labs (Education Program) via the Planet Data API** to build a timelapse showing how the site has changed over time. The task searches back up to **7 years** from the detection date and collects up to **5 years** of available imagery. Each annual frame covers the **dry season from November to March** — the period of lowest cloud cover in Ghana's mining regions. The clearest available scene for each year is selected and downloaded as a thumbnail cropped to the detection site. Each frame is saved to local media storage and recorded as a `SiteTimeLapse` entry, storing the **year, acquisition period, and cloud cover percentage**.

### Step 11 — Notification
If the scan was triggered manually, admin users receive an HTML email and an in-app notification summarising the results (total sites, illegal count). Automated scan completions are not emailed individually — they are batched into a **daily digest sent at 18:00** to the operations team.

---

## 5. Automated Scanning

In addition to manual AOI scans, the system has an automated background scanner.

- Ghana is divided into a regular grid of **6,528 ScanTile** records (0.07° per side, ~7.77 km) stored in the database
- Each tile is classified as **Hotspot** (773 tiles that intersect known mining belt polygons) or **Normal** (5,755 tiles). This classification is set at grid generation time and is permanent.
- A **Celery Beat** periodic task runs every 5 minutes and selects **up to 5 tiles per tick** using a three-tier priority system: (1) never-scanned hotspot tiles first, (2) hotspot tiles last scanned more than 20 hours ago, (3) normal tiles last scanned more than 7 days ago
- Scanning is restricted to a daily window (6:00 AM – 6:00 PM)
- If Google Earth Engine returns a rate-limit error, scanning stops for the rest of the day and the rate-limited flag must be manually cleared by an administrator through the dashboard before scanning resumes
- Automated scan results feed into the same `Job`, `DetectedSite`, and `Alert` tables as manual scans, tagged with `Job.source = 'automated'`
- Manual scans are enqueued at higher priority than automated scans so they are never delayed
- There is a singleton `AutoScanConfig` record that stores the current state: enabled/disabled, window hours, daily tile count, and rate-limit date

---

## 6. The Alert and Inspection Workflow

This is the operational workflow after detections are found:

```
DetectedSite (illegal)
    → Alert created (Open)
        → Admin reviews alert
        → Admin assigns to Inspector (Dispatched)
            → Inspector receives email + in-app notification
            → Due date set to 5 calendar days from assignment
            → Inspector visits site in the field
            → Inspector submits field report:
                - Outcome: Mining Confirmed / False Positive / Inconclusive
                - Visit date, GPS coordinates
                - Field notes
                - Evidence photos (uploaded, SHA-256 hashed for integrity)
            → Admin receives email notification of the report
            → Alert marked Resolved or returned for re-inspection
```

**SLA Tracking**: A Celery Beat task runs daily at **07:00** and checks all open assignments:
- Past due date → sends reminder email + in-app notification to inspector (on or after day 5)
- 2+ days past due date → escalates to all admins by email and in-app notification, creates audit log entry (on or after day 7)

**Inconclusive Escalation**: If three consecutive inconclusive field reports are submitted for the same alert, the system automatically escalates the alert to CRITICAL severity.

**Critical Alert Escalation**: A separate hourly task checks for CRITICAL alerts that have been in OPEN or ACKNOWLEDGED status for 48+ hours without inspector action and sends an escalation email to the operations team.

---

## 7. Notifications

The system sends both **HTML emails** and **in-app notifications** (stored in a `NotificationInbox` table). Email recipients are filtered by `UserProfile.receive_email_alerts = True`.

| Event | Recipients |
|---|---|
| Scan completed (manual) | All admins |
| Scan failed (manual) | All admins |
| Field report submitted | All admins |
| New assignment | Assigned inspector |
| SLA reminder (past 5-day due date) | Inspector |
| SLA escalation (2+ days past due date / day 7+) | All admins |
| Critical alert open 48h+ without action | Ops email list |
| Automated scan daily digest (18:00) | Ops email list |
| Mining concession expiring in 30 days | Ops email list |

---

## 8. Data Models (Key Entities)

| Model | Description |
|---|---|
| `Job` | One scan run. Tracks status through the pipeline (QUEUED → VALIDATING → EXPORTING → PREPROCESSING → INFERRING → POSTPROCESSING → STORING → COMPLETED). Has `source` (manual/automated). |
| `ScanTile` | A geographic grid tile (~7.77 km per side) for automated scanning. Has hotspot/normal priority, set permanently at generation time. |
| `AutoScanConfig` | Singleton config for the automated scanner (window hours, pause state, rate-limit date). |
| `DetectedSite` | One detected mining polygon. Has geometry, confidence score, area, legal status, recurrence count. |
| `LegalConcession` | Ghana Minerals Commission concession boundaries (1,069 polygons) used for legal/illegal classification. |
| `Region` | Named monitoring zones (protected forests, water bodies, buffer zones, districts, hotspots). |
| `Alert` | Raised for each illegal DetectedSite. Has severity (CRITICAL/HIGH/MEDIUM), type (RECURRING_SITE/HIGH_CONFIDENCE/EXPANSION_DETECTED/NEW_DETECTION), and status. |
| `InspectorAssignment` | Links an alert to an inspector. Tracks outcome, visit date, SLA due date, evidence photos. |
| `EvidencePhoto` | Individual photo uploaded with a field report. SHA-256 hashed for integrity verification. |
| `Inspection` | Field verification record with GPS coordinates, outcome, drone footage path. |
| `SiteTimelapse` | Historical Planet Labs dry-season imagery per detected site. Stores year, acquisition period, and cloud cover percentage per frame. |
| `SatelliteImagery` | Log of each HLS scene processed — which satellite, bands, cloud cover, acquisition date. |
| `ModelRun` | Log of each inference run — which model checkpoint, threshold, validation metrics. |
| `AuditLog` | Append-only log of significant system actions (alert status changes, assignments, job completions/failures). Never updated or deleted. |
| `UserProfile` | Extends Django's User with role (system_admin/agency_admin/inspector) and a FK to Organisation. |
| `UserPreferences` | Per-user UI preferences: theme, layout, notification settings, quiet hours, privacy settings. |
| `NotificationInbox` | In-app notification inbox entries per user. |

---

## 9. Frontend / Map Interface

- The main map page (`/analysis/live-map/`) uses **MapLibre GL JS** for rendering
- AOI drawing uses the **MapboxDraw** plugin
- Area is calculated client-side with **Turf.js** (`turf.area()` → converted to hectares)
- AOI validation: minimum 10 ha, maximum 60 ha — enforced client-side (with a red warning) and server-side
- After scan submission, the frontend polls `/api/jobs/{id}/` every 3 seconds to show pipeline progress
- Detection results are displayed as a GeoJSON layer on the map and as a list in the sidebar
- Detections are colour-coded by legal status (illegal = red, legal = green)

There is also an **Auto Scan page** built with Leaflet.js that displays the Ghana grid, animates which tiles are being scanned, and shows live scanning statistics.

---

## 10. Technology Stack

| Component | Technology |
|---|---|
| Backend framework | Django 4.2 (Python) |
| Database | PostgreSQL + PostGIS (spatial queries) |
| Async tasks | Celery + Celery Beat + Redis |
| Detection satellite data | Google Earth Engine (HLS — NASA/HLS/HLSS30/v002) |
| Timelapse satellite data | Planet Labs Education Program (Planet Data API) |
| ML model | FPN-ResNet50 (6-band segmentation, PyTorch) |
| Raster processing | rasterio, NumPy |
| Spatial operations | GeoDjango, Shapely |
| Frontend map | MapLibre GL JS + MapboxDraw + Turf.js |
| REST API | Django REST Framework |
| Email | Django's email backend (HTML + plain text) |

---

## 11. What Is NOT in the System

For accuracy when writing a report, the following are **not implemented**:
- No public or researcher/NGO access tier
- No anonymous data access or aggregated public data export
- No mobile app (mobile settings exist in `UserPreferences` but there is no separate app)
- No real-time drone integration (field is present in the model for a drone footage path, but no upload pipeline)
- No map-based reporting for inspectors (they submit via a form on their dashboard, not by marking locations on a map)
