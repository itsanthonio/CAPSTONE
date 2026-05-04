# SankofaWatch — System Overview

---

## 1. What the System Is

SankofaWatch is a web-based platform for detecting illegal (galamsey) mining activity in Ghana using satellite imagery and an AI model. An admin user defines an area on a map, the system fetches satellite data for that area, runs it through a deep learning model, and produces a map of detected mining sites. If any of those sites fall outside registered mining concessions, the system flags them as illegal, raises alerts, and dispatches field inspectors to verify on the ground.

The system is built in **Django** (Python), uses **PostgreSQL + PostGIS** for spatial data, **Celery** for async task processing, and **Google Earth Engine (GEE)** as the satellite data source.

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
- Receives escalation emails when critical alerts go unactioned for 48+ hours
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
- Receives SLA reminders if the report is overdue; the system escalates to admins if 2+ days past deadline

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
An admin draws a polygon on the map (an AOI, Area of Interest, between 10–60 ha) and clicks "Scan AOI". This creates a `Job` record in the database with `status=queued`. The frontend then polls the job status every 3 seconds to show progress.

### Step 2 — GEE Export (Exporting)
The system authenticates with Google Earth Engine using a service account and calls the GEE API to fetch **Harmonised Landsat and Sentinel-2 (HLS)** imagery for the AOI within the selected date range. GEE clips the imagery to the AOI and downloads it as a GeoTIFF to local storage. The imagery uses 6 spectral bands: **B3 (Green), B4 (Red), B8 (NIR), B11 (SWIR-1), B12 (SWIR-2), and BSI (Bare Soil Index)**.

### Step 3 — Preprocessing
The downloaded GeoTIFF is read with `rasterio` and converted into a normalised 6-band NumPy tensor ready for the model.

### Step 4 — Inference (Inferring)
The tensor is passed to a **Feature Pyramid Network (FPN) with a ResNet-50 encoder** — a segmentation model trained to identify surface disturbance patterns associated with illegal mining. The model outputs a **probability mask** (a per-pixel floating-point score from 0 to 1) over the AOI.

### Step 5 — Postprocessing
The probability mask is thresholded (default: 0.5) and converted from a raster to **vector polygons** using rasterio/shapely. Polygons below a minimum area are discarded. Each polygon becomes a potential `DetectedSite`.

### Step 6 — Deduplication
Before saving new sites, the system checks if an existing site centroid is within ~500 m of the new detection. If so, it increments that site's `recurrence_count` instead of creating a duplicate. A recurring site triggers a higher-severity alert.

### Step 7 — Legal Classification
Each detected polygon is spatially joined against the `LegalConcession` table (Ghana Minerals Commission concession boundaries). If the centroid of a detected site falls inside an active concession, it is marked `legal`. If not, it is marked `illegal`. The percentage overlap with the nearest concession is also stored.

### Step 8 — Alert Generation
For each `illegal` detected site, the system checks if an active alert already exists (to avoid spam). If not, it creates an `Alert` with a severity and type based on:
- **Critical** — recurring site (seen before)
- **High** — confidence ≥ 85% or area > 5 ha
- **Medium** — new detection below the high thresholds

### Step 9 — ML Visualisation Images
The system generates 4 PNG images per job and per site:
- False colour composite (NIR/Red/Green)
- Binary prediction mask
- Probability heatmap
- Overlay (prediction on top of false colour)

### Step 10 — Timelapse Fetch (async)
For each detected site, a separate Celery task fetches historical annual GEE RGB composites (one per year) to build a timelapse showing how the site has changed over time. NDVI and BSI values are recorded per frame to show vegetation loss and bare soil progression.

### Step 11 — Notification
If the scan was triggered manually, admin users receive an HTML email and an in-app notification summarising the results (total sites, illegal count). Automated scans are batched into a daily digest instead.

---

## 5. Automated Scanning

In addition to manual AOI scans, the system has an automated background scanner.

- Ghana is divided into a grid of **ScanTile** records stored in the database
- Each tile has a priority: **Hotspot** (known mining belt areas) or **Normal**
- A **Celery Beat** periodic task runs every 5 minutes and picks the next tile to scan
- Scanning is restricted to a daily window (default: 6am–6pm)
- If Google Earth Engine returns a rate-limit error, scanning stops for the rest of the day
- If detections are found on a Normal tile, it is automatically **promoted to Hotspot** so it gets scanned more frequently
- Automated scan results feed into the same `Job`, `DetectedSite`, and `Alert` tables as manual scans, tagged with `Job.source = 'automated'`
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
            → Inspector visits site in the field
            → Inspector submits field report:
                - Outcome: Mining Confirmed / False Positive / Inconclusive
                - Visit date, GPS coordinates
                - Field notes
                - Evidence photos (uploaded, SHA-256 hashed for integrity)
            → Admin receives email notification of the report
            → Alert marked Resolved or returned for re-inspection
```

**SLA Tracking**: Each inspector assignment has a `due_date`. A periodic Celery task checks:
- Past due → sends email reminder to inspector
- 2+ days past due → escalates to admins by email and in-app notification, creates audit log entry

**Critical Alert Escalation**: A separate periodic task checks for critical alerts that have been open/acknowledged for 48+ hours with no action and emails the ops team.

---

## 7. Notifications

The system sends both **HTML emails** and **in-app notifications** (stored in a `NotificationInbox` table). Email recipients are filtered by `UserProfile.receive_email_alerts = True`.

| Event | Recipients |
|---|---|
| Scan completed (manual) | All admins |
| Scan failed (manual) | All admins |
| Field report submitted | All admins |
| New assignment | Assigned inspector |
| Assignment reminder (3+ days pending) | Inspector |
| SLA deadline breach | Inspector |
| SLA escalation (2+ days past deadline) | All admins |
| Critical alert open 48h+ | Ops email list |
| Mining concession expiring in 30 days | Ops email list |

---

## 8. Data Models (Key Entities)

| Model | Description |
|---|---|
| `Job` | One scan run. Tracks status through the pipeline. Has `source` (manual/automated). |
| `ScanTile` | A geographic grid tile for automated scanning. Has hotspot/normal priority. |
| `AutoScanConfig` | Singleton config for the automated scanner (window hours, pause state, rate-limit). |
| `DetectedSite` | One detected mining polygon. Has geometry, confidence score, area, legal status, recurrence count. |
| `LegalConcession` | Ghana Minerals Commission concession boundaries used for legal/illegal classification. |
| `Region` | Named monitoring zones (protected forests, water bodies, buffer zones, districts, hotspots). |
| `Alert` | Raised for each illegal DetectedSite. Has severity, type, and status. |
| `InspectorAssignment` | Links an alert to an inspector. Tracks outcome, visit date, SLA, evidence photos. |
| `EvidencePhoto` | Individual photo uploaded with a field report. SHA-256 hashed. |
| `Inspection` | Field verification record with GPS coordinates, outcome, drone footage path. |
| `SiteTimelapse` | Historical annual GEE composites per detected site. Stores NDVI and BSI per frame. |
| `SatelliteImagery` | Log of each HLS scene processed — which satellite, bands, cloud cover, acquisition date. |
| `ModelRun` | Log of each inference run — which model checkpoint, threshold, validation metrics. |
| `AuditLog` | Append-only log of significant system actions (alert status changes, assignments, job completions/failures). Never updated or deleted. |
| `UserProfile` | Extends Django's User with role (system_admin/agency_admin/inspector) and a FK to Organisation. |
| `UserPreferences` | Per-user UI preferences: theme, layout, notification settings, mobile settings. |
| `NotificationInbox` | In-app notification inbox entries per user. |

---

## 9. Frontend / Map Interface

- The main map page (`/analysis/live-map/`) uses **MapLibre GL JS** for rendering
- AOI drawing uses the **MapboxDraw** plugin
- Area is calculated client-side with **Turf.js** (`turf.area()` → converted to hectares)
- AOI validation: minimum 100 ha, maximum 6,000 ha — enforced client-side (with a red warning) and server-side
- After scan submission, the frontend polls `/api/jobs/{id}/` every 3 seconds to show pipeline progress
- Detection results are displayed as a GeoJSON layer on the map and as a list in the sidebar
- Detections are colour-coded by legal status (illegal = red, legal = green)

There is also an **Auto Scan page** built with Leaflet.js that displays the Ghana grid, animates which tiles are being scanned, and shows live scanning statistics.

---

## 10. Technology Stack

| Component | Technology |
|---|---|
| Backend framework | Django (Python) |
| Database | PostgreSQL + PostGIS (spatial queries) |
| Async tasks | Celery + Celery Beat |
| Satellite data | Google Earth Engine (HLS — Landsat 8/9, Sentinel-2A/2B) |
| ML model | FPN-ResNet50 (6-band segmentation) |
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
