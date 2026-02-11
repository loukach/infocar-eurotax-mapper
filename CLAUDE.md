# Infocar-Eurotax Mapping Desktop App v4.2

## Overview

Desktop application for matching Infocar vehicle codes to Eurotax catalog entries. Uses a two-stage matching algorithm with make+model candidate selection and multi-factor scoring including OEM match as a regular field.

## Single Source of Truth

**`MATCHING_ALGORITHM_V4.md` is the authoritative, tech-agnostic requirements document for the matching algorithm.** All code must implement what this document specifies. When making any change to matching logic, scoring, candidate selection, normalization, or confidence thresholds:

1. Update `MATCHING_ALGORITHM_V4.md` **first** (or simultaneously)
2. Then implement the change in code
3. Never let code diverge from the document

This document is the reference for understanding what the algorithm *should* do, independent of how it's implemented. If there's a conflict between the document and the code, the document wins — fix the code.

## Architecture

```
desktop-app-v4/
+-- main.py              # FastAPI application, UI server (lifespan event for Docker)
+-- matcher_v4.py        # Matching algorithm (Stage 1 + Stage 2)
+-- normalizers.py       # Fuel, body, transmission, traction, model normalization (100% coverage both sources)
+-- vehicle_class.py     # CAR vs LCV classification (includes BUS as LCV body type)
+-- mongodb_client.py    # MongoDB connection (Eurotax trims from x_catalogue)
+-- static/              # Frontend UI (thin display layer, no business logic)
+-- Dockerfile           # Container build for k8s deployment
+-- .env.example         # Credential template for new engineers
+-- .dockerignore        # Excludes dev files from Docker image
+-- benchmark/           # Benchmark scripts for comparing mapping sources
+-- experiments/         # Experiment and validation scripts
+-- analysis/            # Analysis session folders
```

### Documentation

| Document | Purpose |
|----------|---------|
| `MATCHING_ALGORITHM_V4.md` | **Single source of truth** for the matching algorithm (tech-agnostic requirements) |
| `UI_ARCHITECTURE.md` | UI design principles (v4.2), API response contract, match indicator logic |
| `CLAUDE.md` | Quick reference for developers/Claude (this file) |

## UI (v4.2)

- **Search bar**: input + Search button + profile dropdown + divider + vehicle identity (make + version name) + status
- **URL hash navigation**: `/#code` — browser back/forward, auto-search on page load
- **Top 3 candidates** by default, expandable to 10
- **Minimal candidate headers**: score bar + natcode (copiable) + "Map it" button (hidden on existing mapping)
- **OEM Code row**: eurotax code + OEM match badge (Exact/Cleaned/None) + match indicator border
- **3px transparent gap** between source and candidate columns
- **No context strip** — removed in v4.2 (vehicle identity moved inline with search)
- **No recent searches** — URL hash replaces recent pills
- **Column widths**: Field 130px, Source 260px, Gap 3px, candidates share remaining space equally

## Key Change from v3

**v3 bug:** OEM codes drove candidate selection via early stopping. If an exact OEM match was found, the algorithm stopped and never considered other candidates. This caused incorrect matches when the wrong variant (e.g., 3-door) shared the OEM code with the source, hiding the correct variant (5-door).

**v4 fix:** OEM is demoted from a candidate selection gate to a regular scoring field.
- All candidates come from make+model containment matching
- OEM match adds points per-candidate: exact +10, cleaned +5, none 0
- This ensures all variants appear as candidates and compete on overall merit

## Matching Algorithm (v4)

### Stage 1: Candidate Selection (Make+Model)

Single-step process (no OEM gating):

- Find all Eurotax records matching the source make
- Filter by model containment (either direction)
- Filter by vehicle class (CAR/LCV)

### Stage 2: Scoring (157 points max, configurable via weight profiles)

| Factor | Weight | Notes |
|--------|--------|-------|
| Price | 25 | Within 10%/20%/35% tolerance |
| HP | 20 | Within 0/5/10 HP tolerance |
| Trim | 15 | Derived: known trim keywords extracted from vehicle name, then set intersection |
| CC | 15 | Within 0/50/100cc tolerance |
| Fuel | 15 | Normalized (DIESEL, PETROL, HYBRID_*, ELECTRIC, etc.) |
| Sellable | 10 | Exact window match: 10, overlap: 5, no overlap: 0 |
| Body | 10 | Normalized (SEDAN, SUV, WAGON, etc.) |
| **OEM** | **10** | **Exact: +10, Cleaned: +5, None: 0 (per candidate)** |
| **Model** | **5** | **Exact normalized match only (containment = 0)** |
| Transmission | 5 | AUTOMATIC, MANUAL, CVT |
| Traction | 5 | FWD, RWD, AWD |
| Doors | 5 | Exact or +/-1 match |
| Name | 5 | Token similarity |
| Seats | 3 | Exact or +/-1 match |
| Gears | 3 | Exact or +/-1 match |
| KW | 3 | Within 0/5/10 KW tolerance |
| Mass | 3 | Within 5%/10% tolerance |

### Confidence Thresholds (percentage-based: `score / max_score`)

| Threshold | Confidence | Default (157pt) |
|-----------|------------|-----------------|
| >= 71.4% | PERFECT | >= 113 |
| >= 53.5% | LIKELY | >= 84 |
| >= 28.5% | POSSIBLE | >= 45 |
| < 28.5% | UNLIKELY | < 45 |

---

## Running the App

### Desktop mode (local development)

```bash
cd desktop-app-v4
python main.py
```

Opens browser at `http://127.0.0.1:8000`. Scans ports 8000-8009 for availability.

### Server mode (Docker/k8s)

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Or via Docker:

```bash
docker build -t infocar-eurotax-v4 .
docker run -e MONGO_URI=... -p 8000:8000 infocar-eurotax-v4
```

Data loading and hourly refresh are handled by the FastAPI lifespan event, so both modes load data automatically. Desktop mode additionally opens the browser and scans for available ports.

### Prerequisites

- VPN connection to MotorK network
- MongoDB credentials in `.env` file: `MONGO_URI=mongodb://...` (see `.env.example`)
- Python packages: `fastapi`, `uvicorn`, `pymongo`, `requests`, `python-dotenv`

---

## Brand-Specific OEM Cleaning

The `clean_oem_code()` function applies brand-specific transformations (unchanged from v3):

| Brand | Pattern | Example |
|-------|---------|---------|
| Renault/Dacia | Remove 2-3 char prefix | `XJK12345` -> `12345` |
| VW/Skoda | Remove `-XXX` suffix | `ABC123-WI1` -> `ABC123` |
| Mercedes | Remove `-XX` suffix or extract to DL | `123DL456-AB` -> `123DL4` |
| Audi | Remove suffix patterns | `ABC-YEG` -> `ABC` |
| Opel | Remove trailing chars | `1234567A` -> `123456` |
| Peugeot/Citroen/DS | Remove last 2 chars | `12345678` -> `123456` |
| KIA/Hyundai | Remove last 3 chars | `123456789` -> `123456` |

---

## Data Sources

| Operation | Source | Endpoint |
|-----------|--------|----------|
| Load Eurotax versions | MongoDB | `x_catalogue.trims` collection (aggregation pipeline, loaded on startup, refreshed hourly) |
| Fetch Infocar version details | X-Catalog API | `PUT /trim/search` with `source: "infocar"` |
| Fetch existing mappings | X-Catalog API | `GET /v1/private/mapping/infocar/{code}?country=it&vehicleType={car\|lcv}` (real-time, picks most recent by ObjectId) |
| Create new mapping | X-Catalog API | `POST /v1/private/mapping` (score normalized 0-1, strategy lowercase, vehicleType from vehicle class) |
