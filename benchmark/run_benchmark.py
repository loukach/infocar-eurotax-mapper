# -*- coding: utf-8 -*-
"""
V4 Benchmark - Compare Mapping Sources

Compares three mapping sources side by side for 412 disagreement rows:
1. Existing mapping - from MongoDB x_catalogue.mappings
2. Sven's algorithm - from CSV (our_* columns)
3. V4 matcher - from running our v4 matcher with default weights

Usage:
    cd desktop-app-v4
    python -m benchmark.run_benchmark
"""
import csv
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# Add parent directory to path so we can import project modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matcher_v4 import (
    MatcherV4, rank_candidates, get_confidence, get_max_score,
    extract_trim_tokens, WEIGHT_PROFILES, DEFAULT_PROFILE
)
from normalizers import normalize_body, normalize_model
from vehicle_class import identify_vehicle_class
from mongodb_client import fetch_eurotax_trims, get_existing_mapping
from main import extract_specs, fetch_infocar_from_xcatalog, invert_provider_code

# ============================================================================
# CONFIGURATION
# ============================================================================

INPUT_CSV = r"C:\Users\lucas.gros\Downloads\comparison_export_disagreement_2026-02-06T14-44-57.csv"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
MAX_WORKERS = 5


def load_input_csv(path: str) -> List[Dict]:
    """Load the disagreement CSV into a list of dicts."""
    rows = []
    with open(path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def build_natcode_lookup(eurotax_data: List[Dict]) -> Dict[str, Dict]:
    """Build a dict[providerCode -> record] for resolving make/model/version from any natcode."""
    lookup = {}
    for rec in eurotax_data:
        pcode = str(rec.get('providerCode', ''))
        if pcode:
            lookup[pcode] = rec
    return lookup


def fetch_xcatalog_for_row(infocar_code: str) -> Tuple[str, Optional[Dict], str]:
    """Fetch X-Catalog data for a single infocar_code, trying inverted code if needed.
    Returns (original_code, record_or_None, used_code)."""
    rec = fetch_infocar_from_xcatalog(infocar_code)
    if rec:
        return (infocar_code, rec, infocar_code)
    # Try inverted code
    inverted = invert_provider_code(infocar_code)
    if inverted:
        rec = fetch_infocar_from_xcatalog(inverted)
        if rec:
            return (infocar_code, rec, inverted)
    return (infocar_code, None, infocar_code)


def process_row(
    row: Dict,
    xcatalog_cache: Dict[str, Optional[Dict]],
    used_code_map: Dict[str, str],
    matcher: MatcherV4,
    natcode_lookup: Dict[str, Dict],
    weights: Dict,
    max_score: int,
) -> Dict:
    """Process a single CSV row and return the output row dict."""
    infocar_code = row['infocar_code']
    used_code = used_code_map.get(infocar_code, infocar_code)
    result = {
        'infocar_code': infocar_code,
        'infocar_make': row.get('infocar_make', ''),
        'infocar_model': row.get('infocar_model', ''),
        'infocar_version': row.get('infocar_version', ''),
    }

    # --- Sven's data (directly from CSV) ---
    result['eurotax_code_sven'] = row.get('our_eurotax_code', '')
    result['sven_make'] = row.get('our_eurotax_make', '')
    result['sven_model'] = row.get('our_eurotax_model', '')
    result['sven_version'] = row.get('our_eurotax_version', '')

    # --- Existing mapping (from MongoDB, try both original and inverted codes) ---
    existing_mapping = get_existing_mapping(used_code)
    if not existing_mapping and used_code != infocar_code:
        existing_mapping = get_existing_mapping(infocar_code)
    if not existing_mapping:
        inverted = invert_provider_code(infocar_code)
        if inverted and inverted != used_code:
            existing_mapping = get_existing_mapping(inverted)
    existing_natcode = ''
    if existing_mapping:
        existing_natcode = str(existing_mapping.get('destCode', ''))

    result['eurotax_code_existing'] = existing_natcode

    # Resolve existing natcode to make/model/version via lookup
    if existing_natcode and existing_natcode in natcode_lookup:
        ex_rec = natcode_lookup[existing_natcode]
        result['existing_make'] = ex_rec.get('normalizedMake', '')
        result['existing_model'] = ex_rec.get('normalizedModel', '')
        result['existing_version'] = ex_rec.get('name', '')
    else:
        result['existing_make'] = ''
        result['existing_model'] = ''
        result['existing_version'] = ''

    # --- V4 matcher ---
    infocar_rec = xcatalog_cache.get(infocar_code)

    if not infocar_rec:
        result['eurotax_code_v4'] = ''
        result['v4_make'] = ''
        result['v4_model'] = ''
        result['v4_version'] = ''
        result['v4_score'] = ''
        result['v4_confidence'] = 'NOT_FOUND'
        result['v4_max_score'] = max_score
    else:
        brand = (infocar_rec.get('normalizedMake') or infocar_rec.get('make') or '').upper().strip()
        model = (infocar_rec.get('normalizedModel') or '').lower().strip()
        oem_code = infocar_rec.get('manufacturerCode', '')
        infocar_specs = extract_specs(infocar_rec)

        body_type = normalize_body(infocar_rec.get('bodyType', ''))
        vehicle_class = identify_vehicle_class(brand, model, body_type)

        candidate_records = matcher.find_candidates(brand, model, vehicle_class)

        if not candidate_records:
            result['eurotax_code_v4'] = ''
            result['v4_make'] = ''
            result['v4_model'] = ''
            result['v4_version'] = ''
            result['v4_score'] = ''
            result['v4_confidence'] = 'NO_CANDIDATES'
            result['v4_max_score'] = max_score
        else:
            candidates = []
            for rec in candidate_records:
                candidates.append({
                    'eurotax_code': rec.get('manufacturerCode', ''),
                    'natcode': str(rec.get('providerCode', '')),
                    'eurotax_name': rec.get('name', ''),
                    'specs': extract_specs(rec),
                    'vehicle_class': rec.get('_vehicle_class', vehicle_class),
                })

            ranked = rank_candidates(infocar_specs, candidates, oem_code, brand, weights=weights)
            top = ranked[0]

            result['eurotax_code_v4'] = top['natcode']
            result['v4_make'] = top['specs'].get('make', '')
            result['v4_model'] = top['specs'].get('model', '')
            result['v4_version'] = top['specs'].get('name', '')
            result['v4_score'] = top['score']
            result['v4_confidence'] = get_confidence(top['score'], max_score)
            result['v4_max_score'] = max_score

    # --- Trim levels (extracted from version names) ---
    result['infocar_trim'] = ', '.join(sorted(extract_trim_tokens(result.get('infocar_version', ''))))
    result['existing_trim'] = ', '.join(sorted(extract_trim_tokens(result.get('existing_version', ''))))
    result['sven_trim'] = ', '.join(sorted(extract_trim_tokens(result.get('sven_version', ''))))
    result['v4_trim'] = ', '.join(sorted(extract_trim_tokens(result.get('v4_version', ''))))

    # --- Agreement flags ---
    v4_code = str(result.get('eurotax_code_v4', ''))
    sven_code = str(result.get('eurotax_code_sven', ''))
    existing_code = str(result.get('eurotax_code_existing', ''))

    result['agreement_sven_v4'] = (v4_code == sven_code and v4_code != '') if v4_code else False
    result['agreement_existing_v4'] = (v4_code == existing_code and v4_code != '') if v4_code else False

    return result


def main():
    print("=" * 70)
    print("V4 Benchmark - Compare Mapping Sources")
    print("=" * 70)

    # 1. Load input CSV
    print(f"\n[1/5] Loading input CSV...")
    if not os.path.exists(INPUT_CSV):
        print(f"ERROR: Input CSV not found: {INPUT_CSV}")
        sys.exit(1)

    rows = load_input_csv(INPUT_CSV)
    total = len(rows)
    print(f"  Loaded {total} disagreement rows")

    # 2. Load Eurotax data and build matcher
    print(f"\n[2/5] Loading Eurotax data from MongoDB...")
    eurotax_data = fetch_eurotax_trims(country="it")
    if not eurotax_data:
        print("ERROR: No Eurotax data returned. Check VPN and MongoDB connection.")
        sys.exit(1)
    print(f"  Eurotax unique records: {len(eurotax_data):,}")

    print("  Building v4 matcher indexes...")
    matcher = MatcherV4(eurotax_data)
    print(f"  Indexed {len(matcher.records_by_make):,} makes")

    # Build natcode lookup
    natcode_lookup = build_natcode_lookup(eurotax_data)
    print(f"  Natcode lookup: {len(natcode_lookup):,} entries")

    weights = WEIGHT_PROFILES[DEFAULT_PROFILE]
    max_score = get_max_score(weights)

    # 3. Fetch X-Catalog data in parallel (tries inverted code if original not found)
    print(f"\n[3/5] Fetching X-Catalog data ({total} API calls, {MAX_WORKERS} workers)...")
    xcatalog_cache: Dict[str, Optional[Dict]] = {}
    used_code_map: Dict[str, str] = {}  # original_code -> code that worked
    infocar_codes = [row['infocar_code'] for row in rows]

    start_time = time.time()
    completed = 0
    not_found = 0
    inverted_found = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(fetch_xcatalog_for_row, code): code
            for code in infocar_codes
        }
        for future in as_completed(futures):
            code, rec, used_code = future.result()
            xcatalog_cache[code] = rec
            used_code_map[code] = used_code
            completed += 1
            if rec is None:
                not_found += 1
            elif used_code != code:
                inverted_found += 1
            if completed % 50 == 0 or completed == total:
                elapsed = time.time() - start_time
                rate = completed / elapsed if elapsed > 0 else 0
                eta = (total - completed) / rate if rate > 0 else 0
                print(f"  [{completed}/{total}] {rate:.1f} req/s, ETA: {eta:.0f}s, not found: {not_found}")

    fetch_elapsed = time.time() - start_time
    print(f"  X-Catalog fetch complete: {fetch_elapsed:.1f}s ({not_found} not found, {inverted_found} found via inverted code)")

    # 4. Process all rows
    print(f"\n[4/5] Processing rows (matching + existing lookups)...")
    results = []
    for i, row in enumerate(rows):
        result = process_row(row, xcatalog_cache, used_code_map, matcher, natcode_lookup, weights, max_score)
        results.append(result)
        if (i + 1) % 50 == 0 or (i + 1) == total:
            print(f"  [{i+1}/{total}] Processing {row.get('infocar_make', '')} {row.get('infocar_model', '')}...")

    # 5. Write output CSV
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    output_filename = f"benchmark_comparison_{timestamp}.csv"
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    output_columns = [
        'infocar_code', 'infocar_make', 'infocar_model', 'infocar_version', 'infocar_trim',
        'eurotax_code_existing', 'existing_make', 'existing_model', 'existing_version', 'existing_trim',
        'eurotax_code_sven', 'sven_make', 'sven_model', 'sven_version', 'sven_trim',
        'eurotax_code_v4', 'v4_make', 'v4_model', 'v4_version', 'v4_trim',
        'v4_score', 'v4_confidence', 'v4_max_score',
        'agreement_sven_v4', 'agreement_existing_v4',
    ]

    print(f"\n[5/5] Writing output CSV...")
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=output_columns)
        writer.writeheader()
        for result in results:
            writer.writerow(result)

    print(f"  Output: {output_path}")

    # --- Summary stats ---
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    total_processed = len(results)
    v4_found = sum(1 for r in results if r['v4_confidence'] not in ('NOT_FOUND', 'NO_CANDIDATES'))
    v4_not_found = sum(1 for r in results if r['v4_confidence'] == 'NOT_FOUND')
    v4_no_candidates = sum(1 for r in results if r['v4_confidence'] == 'NO_CANDIDATES')
    existing_found = sum(1 for r in results if r['eurotax_code_existing'])

    agree_sven_v4 = sum(1 for r in results if r['agreement_sven_v4'])
    agree_existing_v4 = sum(1 for r in results if r['agreement_existing_v4'])

    print(f"\nTotal rows:           {total_processed}")
    print(f"V4 matched:           {v4_found} ({v4_found/total_processed*100:.1f}%)")
    print(f"V4 not found (API):   {v4_not_found}")
    print(f"V4 no candidates:     {v4_no_candidates}")
    print(f"Existing mapping:     {existing_found} ({existing_found/total_processed*100:.1f}%)")

    print(f"\nAgreement rates:")
    if v4_found > 0:
        print(f"  Sven == V4:         {agree_sven_v4}/{v4_found} ({agree_sven_v4/v4_found*100:.1f}%)")
        print(f"  Existing == V4:     {agree_existing_v4}/{v4_found} ({agree_existing_v4/v4_found*100:.1f}%)")

    # Confidence distribution
    confidence_counts = {}
    for r in results:
        conf = r['v4_confidence']
        confidence_counts[conf] = confidence_counts.get(conf, 0) + 1

    print(f"\nV4 confidence distribution:")
    for conf in ['PERFECT', 'LIKELY', 'POSSIBLE', 'UNLIKELY', 'NO_CANDIDATES', 'NOT_FOUND']:
        count = confidence_counts.get(conf, 0)
        if count > 0:
            print(f"  {conf:<15} {count:>4} ({count/total_processed*100:.1f}%)")

    print(f"\nDone! Total time: {time.time() - start_time:.1f}s")


if __name__ == "__main__":
    main()
