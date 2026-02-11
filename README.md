# Infocar-Eurotax Mapping Tool v4.2

**Web application for mapping Infocar vehicle codes to Eurotax natcodes with live MongoDB data.**

---

## Table of Contents

- [Overview](#overview)
- [Key Changes from v3](#key-changes-from-v3)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Matching Algorithm](#matching-algorithm)
- [API Reference](#api-reference)
- [User Interface](#user-interface)
- [File Structure](#file-structure)
- [Troubleshooting](#troubleshooting)
- [Version History](#version-history)

---

## Overview

This tool helps MotorK operators map Infocar vehicle versions to their corresponding Eurotax natcodes for the Italian market. It fetches live data from MongoDB and provides an intuitive web interface for searching, comparing, and submitting mappings.

### What It Does

1. **Search** - Enter an Infocar provider code (12 digits)
2. **Fetch** - Retrieves Infocar vehicle data from X-Catalog API
3. **Match** - Finds Eurotax candidates using make+model matching
4. **Score** - Ranks candidates by spec similarity (157 points max, including OEM match)
5. **Display** - Shows comparison table with per-candidate OEM match indicators
6. **Map** - Submits selected mapping to X-Catalog

---

## Key Changes from v3

| Aspect | v3 | v4 |
|--------|----|----|
| **Candidate selection** | OEM-based with early stopping | Make+model only (no OEM gating) |
| **OEM role** | Candidate gate (+20/+10/0 bonus) | Regular scoring field (+10/+5/0 per candidate) |
| **Max score** | 160 points | 157 points |
| **OEM visibility** | Global match type badge | Per-candidate OEM match badge |
| **Bug fix** | Wrong variant could hide correct match | All variants compete on overall merit |

### Bug Fixed

In v3, if the wrong variant (e.g., Opel Corsa 3-door) shared an OEM code with the source vehicle, the algorithm stopped at exact OEM match and never considered the correct 5-door variant. v4 ensures all variants appear as candidates and the one with the best overall score wins.

---

## Requirements

### System Requirements

- **Python 3.10+** (tested with 3.11, 3.12, 3.13)
- **Memory**: 2GB+ RAM (for loading ~500k records)
- **Network**: VPN connection to MotorK network

### Network Access Required

| Service | Host | Port | Purpose |
|---------|------|------|---------|
| MongoDB | mongodb-0.stockspark.app | 443 | Eurotax version catalog (`x_catalogue.trims`) |
| X-Catalog API | x-catalogue.motork.io | 443 | Infocar data, existing mappings, mapping submission |

### External API Endpoints (X-Catalog)

| Operation | Method | Endpoint | Notes |
|-----------|--------|----------|-------|
| Fetch Infocar version | PUT | `/trim/search` | `source: "infocar"`, returns vehicle details + stale mappings array |
| Fetch existing mappings | GET | `/v1/private/mapping/infocar/{code}?country=it&vehicleType={car\|lcv}` | Real-time, most recent picked by ObjectId |
| Create mapping | POST | `/v1/private/mapping` | Score normalized 0-1, strategy lowercase, vehicleType from vehicle class |

### Python Dependencies

```
fastapi>=0.100.0
uvicorn>=0.23.0
requests>=2.31.0
pydantic>=2.0.0
pymongo>=4.0.0
python-dotenv>=1.0.0
```

---

## Quick Start

### Step 1: Navigate to Directory

```bash
cd desktop-app-v4
```

### Step 2: Create Environment File

Create `.env` file with MongoDB credentials:

```env
MONGO_URI=mongodb://x-catalogue-prod-read-only:YOUR_PASSWORD@mongodb-0.stockspark.app:443/?authSource=x_catalogue&readPreference=primaryPreferred&ssl=true&tlsAllowInvalidCertificates=true&tlsAllowInvalidHostnames=true&directConnection=true
```

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 4: Connect to VPN

Ensure you're connected to MotorK VPN before running.

### Step 5: Run Application

**Windows:**
```batch
run.bat
```

**Mac/Linux:**
```bash
./run.sh
```

**Manual:**
```bash
python main.py
```

### Step 6: Access the Application

Browser opens automatically at `http://127.0.0.1:8000`

**First startup takes 60-90 seconds** to load data from MongoDB.

---

## Configuration

### Environment Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `MONGO_URI` | MongoDB connection string | Yes | - |

### Application Constants

| Constant | Value | File | Description |
|----------|-------|------|-------------|
| `REFRESH_INTERVAL` | 3600 | main.py | Seconds between data refreshes |
| `X_CATALOG_BASE_URL` | https://x-catalogue.motork.io | main.py | X-Catalog API base URL |

---

## Matching Algorithm

See `MATCHING_ALGORITHM_V4.md` for complete technical specification.

### Stage 1: Candidate Finding (Make+Model)

```
Target make == Source make
AND Target model contains Source model (or vice versa)
AND Target vehicle_class == Source vehicle_class
```

No OEM filtering. All matching vehicles become candidates.

### Stage 2: Scoring (157 points max, default profile)

| Attribute | Points | Logic |
|-----------|--------|-------|
| Price | 25 | <=10%: 100%, <=20%: 60%, <=35%: 30% |
| HP | 20 | 0: 100%, <=5: 80%, <=10: 50% |
| Trim | 15 | Derived: known trim keywords extracted from vehicle name, then set intersection |
| CC | 15 | 0: 100%, <=50: 80%, <=100: 50% |
| Fuel | 15 | Exact normalized match |
| Sellable | 10 | Exact window match: 10, overlap: 5, no overlap: 0 |
| Body | 10 | Exact normalized match |
| **OEM** | **10** | **Exact: +10, Cleaned: +5, None: 0** |
| Model | 5 | Exact normalized match only (containment = 0) |
| Transmission | 5 | MANUAL/AUTOMATIC/CVT match |
| Traction | 5 | FWD/RWD/AWD match |
| Doors | 5 | Exact or +-1 |
| Name | 5 | Token overlap ratio |
| Seats | 3 | Exact or +-1 match |
| Gears | 3 | Exact or +-1 match |
| KW | 3 | Within 0/5/10 KW tolerance |
| Mass | 3 | Within 5%/10% tolerance |
| **TOTAL** | **157** | |

### Confidence Levels (percentage-based)

| Threshold | Decision | Default (157pt) |
|-----------|----------|-----------------|
| >= 71.4% | PERFECT | >= 113 |
| >= 53.5% | LIKELY | >= 84 |
| >= 28.5% | POSSIBLE | >= 45 |
| < 28.5% | UNLIKELY | < 45 |

---

## API Reference

### GET /api/stats

Returns application status and statistics.

### GET /api/search

Search for Eurotax matches.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `code` | string | Yes | 12-digit Infocar provider code |

**Response fields (changed from v3):**
- Removed: `match_type`, `oem_bonus` (no longer global concepts)
- Added per candidate: `oem_match_type` (EXACT, CLEANED, NONE)
- Changed: `stage2_confidence` now divides by 157 (was 160)

### POST /api/mapping

Submit a new mapping. Accepts `profile` and `vehicle_class` to normalize score server-side.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source_code` | string | Yes | Infocar provider code |
| `dest_code` | string | Yes | Eurotax natcode |
| `score` | int | Yes | Raw score (normalized to 0-1 server-side using profile max) |
| `profile` | string | No | Weight profile name (default: "default") |
| `vehicle_class` | string | No | "CAR" or "LCV" (default: "CAR") |
| `country` | string | No | Country code (default: "it") |

---

## User Interface

### v4.2 UI

- **Vehicle identity** shown inline with search bar (make + version name)
- **URL hash navigation** (`/#code`) — supports browser back/forward, auto-search on page load
- **3 candidates** shown by default, expandable to 10 via "+N more" button
- **Minimal candidate headers** — score bar + natcode + copy + "Map it" button (no "Candidate X" labels)
- **OEM badge merged into OEM Code row** — no separate OEM Match row
- **"Map it" button** hidden on the candidate that is already the existing mapping
- **Transparent 3px gap** separates source column from candidate columns
- **No context strip** — vehicle identity moved to search row for a cleaner layout
- **No recent searches** — URL hash replaces the need for recent pills

### Comparison Table Features

- **3 candidates shown** by default, "See more" button expands to 10
- **Color-coded match indicators**: Green (full match), Yellow (partial), Red (mismatch)
- **OEM Code row**: Shows eurotax code + OEM match badge (Exact/Cleaned/None) + match border
- **Copy button** on natcodes for easy clipboard access
- **"Map it" button** only shown for non-existing-mapping candidates
- **EXISTING badge** highlights the current mapping candidate

---

## File Structure

```
desktop-app-v4/
+-- main.py                    # FastAPI application entry point (v4.0.0)
+-- mongodb_client.py          # MongoDB connection and queries
+-- matcher_v4.py              # Core matching logic (no OEM gating)
+-- normalizers.py             # Value normalization functions (100% coverage both sources)
+-- vehicle_class.py           # CAR/LCV classification (BUS added as LCV body type)
+-- requirements.txt           # Python dependencies
+-- .env                       # MongoDB credentials (DO NOT COMMIT)
+-- .gitignore                 # Git ignore rules
+-- run.bat                    # Windows launcher script
+-- run.sh                     # Mac/Linux launcher script
+-- README.md                  # This documentation
+-- CLAUDE.md                  # Claude Code instructions
+-- MATCHING_ALGORITHM_V4.md   # Detailed algorithm specification
+-- static/
|   +-- index.html             # Web UI (v4.2 - streamlined layout)
+-- experiments/               # Validation scripts
+-- data/                      # Legacy data directory
```

---

## Troubleshooting

### VPN Connection

The app requires MotorK VPN for both MongoDB and X-Catalog API access.

```bash
# Quick connectivity check
ping x-catalogue.motork.io
```

If unreachable, connect to VPN and retry.

### MongoDB Connection

Test MongoDB independently:

```bash
python mongodb_client.py
```

Expected output: `connected: True` with a record count. If it fails:
- Verify VPN is connected
- Check `.env` file contains a valid `MONGO_URI`
- Ensure the MongoDB password hasn't been rotated

### Port Conflict

If the app fails to start with "address already in use":
- The app auto-scans ports 8000-8009
- Kill any process using those ports, or wait for the next available port
- Check: `lsof -i :8000` (Mac/Linux) or `netstat -ano | findstr :8000` (Windows)

### Data Loading Timeout

First startup loads ~80K deduplicated records from MongoDB (60-90 seconds). If it stalls:
- Check available RAM (needs 2GB+)
- Check network stability (VPN disconnects cause cursor timeouts)
- Monitor with `curl http://127.0.0.1:8000/api/stats` — returns `loading` while in progress

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| **4.2.1** | 2026-02-10 | Mapping fixes: score normalized 0-1, strategy lowercase, vehicleType from vehicle class. Existing mapping lookup via X-Catalog API (real-time, most recent by ObjectId). Removed update mapping endpoint and MongoDB mapping lookup. |
| **4.2.0** | 2026-02-10 | UI redesign: top 3 candidates (expand to 10), URL hash navigation, vehicle identity inline with search, OEM badge merged into OEM Code row, minimal candidate headers, "Map it" button hidden on existing mapping, transparent gap column, context strip removed |
| **4.1.0** | 2026-02-10 | Added Model, Seats, Gears, KW, Mass scoring factors (157pt max), percentage-based confidence thresholds, spaceless model containment matching, flat weight profile, FastAPI lifespan for Docker/k8s compatibility, Dockerfile added, housekeeping (stale v3 refs, README accuracy) |
| **4.0.0** | 2026-02-09 | OEM demoted from candidate gate to scoring field, make+model candidate selection, per-candidate OEM badges, bug fix for hidden correct variants, body type normalization overhauled to 100% coverage on both sources (was 86.1% Eurotax / 57.3% Infocar), BUS added as LCV body type |
| 3.1.0 | 2026-02-06 | MongoDB integration, auto-refresh, existing mapping detection, Docker/K8s support |
| 3.0.0 | 2025-xx-xx | Simplified matcher, two-tier OEM bonus, vehicle class support, 160pt max |
| 2.0.0 | 2025-xx-xx | Progressive matcher with 6 layers (L0-L5) |
| 1.0.0 | 2025-xx-xx | Initial release with 2-layer OEM matching |

---

## Contact

**Project:** MotorK Internal Tools
**Repository:** `infocar-eurotax-mapping/desktop-app-v4`
