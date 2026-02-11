# -*- coding: utf-8 -*-
"""
Infocar-Eurotax Mapping Desktop App v4

Key change from v3: OEM is a regular scoring field, not a candidate selection gate.
- Candidates found via make+model containment only
- OEM match scored per-candidate: exact +10, cleaned +5, none 0
- 157 point maximum score
"""
import os
import sys
import time
import webbrowser
import threading
from contextlib import asynccontextmanager
from collections import defaultdict
from typing import Optional, List, Dict, Any

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from matcher_v4 import (
    MatcherV4, rank_candidates, get_confidence, extract_trim_tokens,
    WEIGHT_PROFILES, DEFAULT_PROFILE, get_max_score
)
from normalizers import normalize_body, normalize_model, normalize_fuel, normalize_transmission, normalize_traction
from vehicle_class import identify_vehicle_class
from mongodb_client import fetch_eurotax_trims, test_connection


# ============================================================================
# CONFIGURATION
# ============================================================================

X_CATALOG_BASE_URL = "https://x-catalogue.motork.io"
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
REFRESH_INTERVAL = 3600  # Refresh data every hour (in seconds)


# ============================================================================
# DATA LOADING
# ============================================================================

eurotax_data: List[Dict] = []
natcode_lookup: Dict[str, Dict] = {}  # providerCode -> record for direct lookups
matcher: Optional[MatcherV4] = None
data_loaded = False
data_load_error = None
last_refresh_time: Optional[float] = None
refresh_count = 0


def load_eurotax_data():
    """Load Eurotax data from MongoDB and build v4 matcher."""
    global eurotax_data, natcode_lookup, matcher, data_loaded, data_load_error, last_refresh_time, refresh_count

    try:
        print("Loading Eurotax data from MongoDB (deduplicated server-side)...")
        eurotax_data = fetch_eurotax_trims(country="it")

        if not eurotax_data:
            data_load_error = "No Eurotax data returned from MongoDB. Check VPN connection."
            print(f"ERROR: {data_load_error}")
            return

        print(f"  Eurotax unique records: {len(eurotax_data):,}")

        # Build v4 matcher
        print("Building v4 matcher indexes...")
        matcher = MatcherV4(eurotax_data)

        # Build natcode lookup for direct eurotax record access
        natcode_lookup = {}
        for rec in eurotax_data:
            pc = str(rec.get('providerCode', ''))
            if pc:
                natcode_lookup[pc] = rec
        print(f"  Built natcode lookup: {len(natcode_lookup):,} entries")

        print(f"  Indexed {len(matcher.exact_oem_index):,} exact OEM codes (for scoring)")
        print(f"  Indexed {len(matcher.records_by_make):,} makes for candidate selection")

        last_refresh_time = time.time()
        refresh_count += 1
        data_loaded = True
        data_load_error = None  # Clear any previous error

        print(f"  Data loaded successfully (refresh #{refresh_count})")

    except Exception as e:
        data_load_error = str(e)
        print(f"Error loading Eurotax data: {e}")


def refresh_data_periodically():
    """Background thread to refresh data every hour."""
    while True:
        time.sleep(REFRESH_INTERVAL)
        print(f"\n[Auto-refresh] Refreshing Eurotax data from MongoDB...")
        load_eurotax_data()
        print(f"[Auto-refresh] Complete\n")


# ============================================================================
# X-CATALOG API CLIENT
# ============================================================================

def fetch_infocar_from_xcatalog(provider_code: str, country: str = "it") -> Optional[Dict]:
    """Fetch Infocar vehicle data from X-Catalog API."""
    url = f"{X_CATALOG_BASE_URL}/trim/search"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    payload = {
        "country": country,
        "source": "infocar",
        "referenceCode": provider_code,
        "vehicleType": "auto",
        "referenceDate": "",
        "equipmentTypes": [],
        "optionCodes": None
    }

    try:
        response = requests.put(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        if data and isinstance(data, list) and len(data) > 0:
            return data[0]
        elif data and isinstance(data, dict):
            if data.get('code') in ('TRIM_NOT_FOUND', 'NOT_FOUND', 'ERROR'):
                return None
            if data.get('make') or data.get('name'):
                return data
        return None

    except requests.exceptions.RequestException as e:
        print(f"X-Catalog API error: {e}")
        return None


def fetch_existing_mapping(source_code: str, vehicle_type: str, country: str = "it") -> Optional['ExistingMapping']:
    """Fetch the most recent existing mapping from X-Catalog API."""
    url = f"{X_CATALOG_BASE_URL}/v1/private/mapping/infocar/{source_code}"
    params = {"country": country, "vehicleType": vehicle_type}
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }

    try:
        response = requests.get(url, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        mappings = response.json()

        if not mappings or not isinstance(mappings, list):
            return None

        # Filter to eurotax mappings and pick the most recent by createdAt
        eurotax = [m for m in mappings if m.get('destProvider') == 'eurotax']
        if not eurotax:
            return None

        # Pick most recent: use id (MongoDB ObjectId, encodes timestamp) since API doesn't return createdAt
        latest = max(eurotax, key=lambda m: m.get('id') or '')
        return ExistingMapping(
            dest_code=str(latest.get('destCode', '')),
            dest_provider=latest.get('destProvider', ''),
            score=latest.get('score'),
            strategy=latest.get('strategy'),
            created_at=None
        )
    except requests.exceptions.RequestException as e:
        print(f"Existing mapping lookup error: {e}")
        return None


def submit_mapping_to_xcatalog(
    source_code: str,
    dest_code: str,
    score: int,
    max_score: int,
    vehicle_class: str,
    country: str = "it",
) -> Dict[str, Any]:
    """Submit a mapping to X-Catalog API."""
    url = f"{X_CATALOG_BASE_URL}/v1/private/mapping"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    normalized_score = round(score / max_score, 4) if max_score > 0 else 0
    vehicle_type = "lcv" if vehicle_class == "LCV" else "car"
    payload = {
        "country": country,
        "destCode": dest_code,
        "destProvider": "eurotax",
        "score": normalized_score,
        "sourceCode": source_code,
        "sourceProvider": "infocar",
        "strategy": "manual",
        "vehicleType": vehicle_type
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        return {"success": True, "data": response.json() if response.text else {}}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}


# ============================================================================
# SPECS EXTRACTION
# ============================================================================

def extract_specs(rec: Dict) -> Dict:
    """Extract vehicle specs from a record."""
    sw = rec.get('sellableWindow', {})
    begin = sw.get('begin', {})
    end = sw.get('end', {})

    if isinstance(begin, dict) and begin.get('$numberLong'):
        begin_year = int(begin['$numberLong']) // 1000 // 86400 // 365 + 1970
    elif isinstance(begin, (int, float)):
        begin_year = int(begin) // 1000 // 86400 // 365 + 1970
    else:
        begin_year = None

    if isinstance(end, dict) and end.get('$numberLong'):
        end_year = int(end['$numberLong']) // 1000 // 86400 // 365 + 1970
    elif isinstance(end, (int, float)):
        end_year = int(end) // 1000 // 86400 // 365 + 1970
    else:
        end_year = None

    # Get price from nested structure if present
    price = rec.get('price')
    if not price:
        prices = rec.get('prices', {})
        if isinstance(prices, dict):
            otr = prices.get('onTheRoad', {})
            if isinstance(otr, dict):
                price = otr.get('value')

    fuel_raw = rec.get('fuelType', '')
    body_raw = rec.get('bodyType', '')
    gear_raw = rec.get('gearType', '')
    traction_raw = rec.get('tractionType', '')

    return {
        'name': rec.get('name', ''),
        'make': rec.get('normalizedMake', ''),
        'model': normalize_model(rec.get('normalizedModel', '')),
        'cc': rec.get('cc'),
        'hp': rec.get('powerHp'),
        'kw': rec.get('powerKw'),
        'price': price,
        'fuel': fuel_raw,
        'body': body_raw,
        'doors': rec.get('doors'),
        'seats': rec.get('seats'),
        'gears': rec.get('gears'),
        'gear_type': gear_raw,
        'traction': traction_raw,
        'mass': rec.get('mass'),
        'sellable_begin': begin_year,
        'sellable_end': end_year,
        # Normalized values (same logic as matcher scoring)
        'fuel_norm': normalize_fuel(fuel_raw),
        'body_norm': normalize_body(body_raw),
        'gear_type_norm': normalize_transmission(gear_raw),
        'traction_norm': normalize_traction(traction_raw),
    }


# ============================================================================
# FASTAPI APPLICATION
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load data on startup when running via uvicorn (Docker/k8s)."""
    load_thread = threading.Thread(target=load_eurotax_data, daemon=True)
    load_thread.start()
    refresh_thread = threading.Thread(target=refresh_data_periodically, daemon=True)
    refresh_thread.start()
    yield


app = FastAPI(
    title="Infocar-Eurotax Mapping Desktop v4",
    description="OEM as regular scoring field - make+model candidate selection",
    version="4.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ExistingMapping(BaseModel):
    dest_code: str
    dest_provider: str
    score: Optional[float] = None
    strategy: Optional[str] = None
    created_at: Optional[str] = None


class SearchResult(BaseModel):
    found: bool
    error: Optional[str] = None
    infocar_provider_code: Optional[str] = None
    infocar_code: Optional[str] = None
    brand: Optional[str] = None
    infocar_name: Optional[str] = None
    infocar_specs: Optional[Dict[str, Any]] = None
    infocar_trims: List[str] = []
    vehicle_class: Optional[str] = None
    candidate_count: int = 0
    candidates: List[Dict[str, Any]] = []
    stage2_decision: Optional[str] = None
    stage2_confidence: Optional[float] = None
    stage2_recommended_natcode: Optional[str] = None
    existing_mapping: Optional[ExistingMapping] = None
    original_code: Optional[str] = None
    was_inverted: bool = False
    weight_profile: str = DEFAULT_PROFILE
    max_score: int = 157


class MappingRequest(BaseModel):
    source_code: str
    dest_code: str
    score: int
    profile: str = DEFAULT_PROFILE
    vehicle_class: str = "CAR"
    country: str = "it"


@app.get("/")
async def root():
    """Serve the main UI."""
    return FileResponse(os.path.join(os.path.dirname(__file__), 'static', 'index.html'))


@app.get("/api/stats")
async def stats():
    """Get application status."""
    if data_load_error:
        return {"status": "error", "message": data_load_error}
    if not data_loaded:
        return {"status": "loading", "message": "Loading Eurotax data from MongoDB..."}

    # Format last refresh time
    last_refresh_str = None
    next_refresh_in = None
    if last_refresh_time:
        import datetime
        last_refresh_str = datetime.datetime.fromtimestamp(last_refresh_time).isoformat()
        elapsed = time.time() - last_refresh_time
        next_refresh_in = max(0, REFRESH_INTERVAL - int(elapsed))

    return {
        "status": "ready",
        "eurotax_count": len(eurotax_data),
        "eurotax_oem_codes": len(matcher.exact_oem_index) if matcher else 0,
        "api_url": X_CATALOG_BASE_URL,
        "version": "4.1.0 (OEM as Scoring Field)",
        "data_source": "MongoDB (x_catalogue.trims)",
        "last_refresh": last_refresh_str,
        "next_refresh_in_seconds": next_refresh_in,
        "refresh_count": refresh_count
    }


@app.get("/api/profiles")
async def list_profiles():
    """Get available weight profiles."""
    return {
        'profiles': {
            name: {'weights': w, 'max_score': sum(w.values())}
            for name, w in WEIGHT_PROFILES.items()
        },
        'default': DEFAULT_PROFILE
    }


@app.get("/api/eurotax/{natcode}")
async def get_eurotax_record(natcode: str):
    """Look up a single Eurotax record by natcode (providerCode)."""
    if not data_loaded:
        raise HTTPException(status_code=503, detail="Data not loaded yet")

    rec = natcode_lookup.get(natcode)
    if not rec:
        return {"found": False, "natcode": natcode}

    return {
        "found": True,
        "natcode": natcode,
        "eurotax_code": rec.get('manufacturerCode', ''),
        "eurotax_name": rec.get('name', ''),
        "specs": extract_specs(rec)
    }


def invert_provider_code(code: str) -> Optional[str]:
    """Invert a 12-digit provider code by swapping first 6 and last 6 digits."""
    if not code or len(code) != 12 or not code.isdigit():
        return None
    return code[6:] + code[:6]


@app.get("/api/search")
async def search(
    code: str = Query(..., description="Infocar provider code"),
    profile: str = Query(default=DEFAULT_PROFILE, description="Weight profile name")
):
    """Search for Eurotax matches using v4 matching algorithm."""
    if not data_loaded or not matcher:
        raise HTTPException(status_code=503, detail="Data not loaded yet")

    # Resolve weight profile
    if profile not in WEIGHT_PROFILES:
        raise HTTPException(status_code=400, detail=f"Unknown profile: {profile}. Available: {list(WEIGHT_PROFILES.keys())}")
    weights = WEIGHT_PROFILES[profile]
    max_score = get_max_score(weights)

    # Try original code first
    infocar_rec = fetch_infocar_from_xcatalog(code)
    used_code = code
    was_inverted = False

    # If not found, try inverted code
    if not infocar_rec:
        inverted = invert_provider_code(code)
        if inverted:
            infocar_rec = fetch_infocar_from_xcatalog(inverted)
            if infocar_rec:
                used_code = inverted
                was_inverted = True

    if not infocar_rec:
        return SearchResult(
            found=False,
            error="Vehicle not found in X-Catalog. Check the provider code and VPN connection.",
            weight_profile=profile,
            max_score=max_score
        )

    # Extract info
    brand = (infocar_rec.get('normalizedMake') or infocar_rec.get('make') or '').upper().strip()
    model = (infocar_rec.get('normalizedModel') or '').lower().strip()
    oem_code = infocar_rec.get('manufacturerCode', '')
    infocar_specs = extract_specs(infocar_rec)

    # Identify vehicle class
    body_type = normalize_body(infocar_rec.get('bodyType', ''))
    vehicle_class = identify_vehicle_class(brand, model, body_type)

    # Get existing mapping via X-Catalog API (most recent)
    vehicle_type = "lcv" if vehicle_class == "LCV" else "car"
    existing_mapping = fetch_existing_mapping(used_code, vehicle_type)

    # V4 MATCHING: Stage 1 - Make+Model candidates (no OEM gating)
    candidate_records = matcher.find_candidates(brand, model, vehicle_class)

    candidates = []
    if candidate_records:
        for rec in candidate_records:
            candidates.append({
                'eurotax_code': rec.get('manufacturerCode', ''),
                'natcode': str(rec.get('providerCode', '')),
                'eurotax_name': rec.get('name', ''),
                'specs': extract_specs(rec),
                'vehicle_class': rec.get('_vehicle_class', vehicle_class)
            })

    if not candidates:
        infocar_trims = list(extract_trim_tokens(infocar_rec.get('name', '')))
        return SearchResult(
            found=True,
            infocar_provider_code=used_code,
            infocar_code=oem_code,
            brand=brand,
            infocar_name=infocar_rec.get('name', ''),
            infocar_specs=infocar_specs,
            infocar_trims=infocar_trims,
            vehicle_class=vehicle_class,
            candidate_count=0,
            candidates=[],
            stage2_decision="NO_CANDIDATES",
            existing_mapping=existing_mapping,
            original_code=code,
            was_inverted=was_inverted,
            weight_profile=profile,
            max_score=max_score
        )

    # Stage 2: Score and rank candidates (OEM scored per-candidate)
    ranked = rank_candidates(infocar_specs, candidates, oem_code, brand, weights=weights)

    top = ranked[0]
    decision = get_confidence(top['score'], max_score)

    # Extract trims
    infocar_trims = list(extract_trim_tokens(infocar_rec.get('name', '')))

    return SearchResult(
        found=True,
        infocar_provider_code=used_code,
        infocar_code=oem_code,
        brand=brand,
        infocar_name=infocar_rec.get('name', ''),
        infocar_specs=infocar_specs,
        infocar_trims=infocar_trims,
        vehicle_class=vehicle_class,
        candidate_count=len(ranked),
        candidates=ranked[:10],  # Top 10
        stage2_decision=decision,
        stage2_confidence=top['score'] / max_score,
        stage2_recommended_natcode=top['natcode'],
        existing_mapping=existing_mapping,
        original_code=code,
        was_inverted=was_inverted,
        weight_profile=profile,
        max_score=max_score
    )


@app.post("/api/mapping")
async def create_mapping(request: MappingRequest):
    """Submit a new mapping to X-Catalog."""
    weights = WEIGHT_PROFILES.get(request.profile, WEIGHT_PROFILES[DEFAULT_PROFILE])
    max_score = get_max_score(weights)
    result = submit_mapping_to_xcatalog(
        source_code=request.source_code,
        dest_code=request.dest_code,
        score=request.score,
        max_score=max_score,
        vehicle_class=request.vehicle_class,
        country=request.country
    )
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


# Mount static files
static_dir = os.path.join(os.path.dirname(__file__), 'static')
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def find_available_port(start_port=8000, max_attempts=10):
    """Find an available port starting from start_port."""
    import socket
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
                return port
        except OSError:
            continue
    return None


def open_browser(port):
    """Open the browser after a short delay."""
    import time
    time.sleep(1.5)
    webbrowser.open(f"http://127.0.0.1:{port}")


def main():
    """Run the desktop application."""
    print("=" * 60)
    print("Infocar-Eurotax Mapping Desktop App v4.0")
    print("OEM as Scoring Field - Make+Model Candidate Selection")
    print("=" * 60)

    # Find available port
    port = find_available_port()
    if port is None:
        print("\nERROR: Could not find an available port (8000-8009).")
        print("Please close other applications using these ports and try again.")
        return

    # Test MongoDB connection first
    print("\nTesting MongoDB connection...")
    conn_test = test_connection()
    if conn_test.get('connected'):
        print(f"  Connected to MongoDB: {conn_test.get('database')}.{conn_test.get('collection')}")
        print(f"  Available Eurotax IT records: {conn_test.get('eurotax_it_count', 'unknown'):,}")
    else:
        print(f"  WARNING: MongoDB connection failed: {conn_test.get('error')}")
        print("  App will retry when loading data...")

    # Data loading and refresh are handled by FastAPI lifespan event
    # (triggered automatically when uvicorn.run starts the app below)

    # Open browser
    browser_thread = threading.Thread(target=lambda: open_browser(port), daemon=True)
    browser_thread.start()

    print(f"\nStarting server at http://127.0.0.1:{port}")
    print(f"Data refresh interval: {REFRESH_INTERVAL // 60} minutes")
    print("Press Ctrl+C to stop\n")

    # Run server
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
