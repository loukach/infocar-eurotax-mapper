# -*- coding: utf-8 -*-
"""
Compare v4 weight profiles for matching strategies.

This script evaluates two weight profiles using known ground truth
mappings from MongoDB x_catalogue.mappings collection.

Strategy A: v4 default profile (140pt, OEM as scoring field)
Strategy B: No OEM scoring (130pt, oem weight = 0)

Reuses existing functions from:
- matcher_v4.py: rank_candidates(), extract_trim_tokens(), all score_*() functions
- normalizers.py: normalize_model(), normalize_fuel(), normalize_body(), etc.
- vehicle_class.py: identify_vehicle_class()
- main.py: extract_specs(), fetch_infocar_from_xcatalog()
- mongodb_client.py: fetch_eurotax_trims()

Usage:
    python compare_strategies.py [--samples N] [--output-dir DIR]
"""

import os
import sys
import json
import random
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime

# Add parent directory to path for imports (script is in experiments/ subfolder)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pymongo import MongoClient
from dotenv import load_dotenv

from matcher_v4 import (
    MatcherV4, rank_candidates, get_confidence, extract_trim_tokens,
    score_candidate, WEIGHT_PROFILES, get_max_score
)
from normalizers import normalize_model, normalize_body, clean_oem_code
from vehicle_class import identify_vehicle_class
from main import extract_specs, fetch_infocar_from_xcatalog
from mongodb_client import fetch_eurotax_trims, get_mongo_client


# =============================================================================
# MONGODB SAMPLING
# =============================================================================

def get_sample_mappings(
    limit: int = 200,
    country: str = "it",
    require_diverse_makes: bool = True
) -> List[Dict]:
    """
    Get sample mappings from MongoDB with known ground truth.

    Args:
        limit: Maximum number of samples to retrieve
        country: Country code (default: "it")
        require_diverse_makes: If True, ensure diverse brand representation

    Returns:
        List of mapping documents with sourceCode and destCode
    """
    client = get_mongo_client()
    db = client['x_catalogue']
    mappings_collection = db['mappings']

    # Query for infocar->eurotax mappings in Italy
    # Include all strategies to maximize sample size
    query = {
        'sourceProvider': 'infocar',
        'destProvider': 'eurotax',
        'country': country
    }

    # Get total count for sampling
    total_count = mappings_collection.count_documents(query)
    print(f"Total available mappings: {total_count:,}")

    if total_count == 0:
        return []

    # If we need diverse makes, first get a sample of distinct makes
    if require_diverse_makes:
        pipeline = [
            {'$match': query},
            {'$sample': {'size': min(limit * 3, total_count)}},  # Get more for diversity
        ]
        candidates = list(mappings_collection.aggregate(pipeline))

        if candidates:
            # We'll filter for diversity later after fetching Infocar data
            random.shuffle(candidates)
            return candidates[:limit]

    # Simple random sample
    pipeline = [
        {'$match': query},
        {'$sample': {'size': limit}}
    ]

    return list(mappings_collection.aggregate(pipeline))


# =============================================================================
# STRATEGY B: MAKE+MODEL ONLY (NO OEM MATCHING)
# =============================================================================

def find_candidates_make_model_only(
    matcher: MatcherV4,
    brand: str,
    model: str,
    vehicle_class: str
) -> List[Dict]:
    """
    Alternative Stage 1: Filter only by make+model+class (no OEM).

    IMPORTANT: This uses the EXACT same logic as the current fallback in
    matcher_v3.py find_candidates() lines 190-218, but runs for ALL cases
    (not just when OEM matching fails).

    Args:
        matcher: MatcherV4 instance with indexed records
        brand: Normalized make (uppercase)
        model: Normalized model (lowercase)
        vehicle_class: VehicleClass.CAR or VehicleClass.LCV

    Returns:
        List of matching candidate records
    """
    if not brand or not model:
        return []

    brand = brand.upper().strip()
    model = model.lower().strip()

    same_make = matcher.records_by_make.get(brand, [])
    matches = []

    # Normalize source model (expand abbreviations, remove year suffixes)
    source_model_norm = normalize_model(model)

    for rec in same_make:
        # Check vehicle class (same as current fallback)
        if rec.get('_vehicle_class') != vehicle_class:
            continue

        eurotax_model = (rec.get('normalizedModel') or '').lower().strip()
        if not eurotax_model:
            continue

        # Normalize target model
        target_model_norm = normalize_model(eurotax_model)

        # Model containment (either direction) using normalized names
        # EXACT same logic as matcher_v3.py lines 211-214
        if (source_model_norm in target_model_norm or
            target_model_norm in source_model_norm or
            model in eurotax_model or
            eurotax_model in model):
            matches.append(rec)

    return matches


# =============================================================================
# COMPARISON RUNNER
# =============================================================================

def run_single_comparison(
    matcher: MatcherV4,
    infocar_code: str,
    ground_truth_natcode: str
) -> Optional[Dict]:
    """
    Run both strategies on a single Infocar code and compare results.

    Args:
        matcher: MatcherV4 instance
        infocar_code: Infocar provider code
        ground_truth_natcode: Expected Eurotax natcode from mapping

    Returns:
        Comparison result dict, or None if Infocar data not found
    """
    # Fetch Infocar data from X-Catalog
    infocar_rec = fetch_infocar_from_xcatalog(infocar_code)

    if not infocar_rec:
        return None

    # Extract vehicle info
    brand = (infocar_rec.get('normalizedMake') or infocar_rec.get('make') or '').upper().strip()
    model = (infocar_rec.get('normalizedModel') or '').lower().strip()
    oem_code = infocar_rec.get('manufacturerCode', '')
    infocar_name = infocar_rec.get('name', '')
    infocar_specs = extract_specs(infocar_rec)

    # Identify vehicle class
    body_type = normalize_body(infocar_rec.get('bodyType', ''))
    vehicle_class = identify_vehicle_class(brand, model, body_type)

    # =========================================================================
    # STRATEGY A: v4 default profile (make+model candidates, OEM as scoring field)
    # =========================================================================
    candidates_a_records = matcher.find_candidates(brand, model, vehicle_class)

    # Build candidate dicts with specs for ranking
    candidates_a_with_specs = []
    for rec in candidates_a_records:
        candidates_a_with_specs.append({
            'eurotax_code': rec.get('manufacturerCode', ''),
            'natcode': str(rec.get('providerCode', '')),
            'eurotax_name': rec.get('name', ''),
            'specs': extract_specs(rec),
            'vehicle_class': rec.get('_vehicle_class', vehicle_class)
        })

    weights_a = WEIGHT_PROFILES['default']
    ranked_a = rank_candidates(infocar_specs, candidates_a_with_specs, oem_code, brand, weights=weights_a)
    top_a = ranked_a[0] if ranked_a else None

    # =========================================================================
    # STRATEGY B: Make+Model only (no OEM scoring)
    # =========================================================================
    candidates_b = find_candidates_make_model_only(matcher, brand, model, vehicle_class)

    # Build candidate dicts with specs for ranking
    candidates_b_with_specs = []
    for rec in candidates_b:
        candidates_b_with_specs.append({
            'eurotax_code': rec.get('manufacturerCode', ''),
            'natcode': str(rec.get('providerCode', '')),
            'eurotax_name': rec.get('name', ''),
            'specs': extract_specs(rec),
            'vehicle_class': rec.get('_vehicle_class', vehicle_class)
        })

    # Strategy B: use default weights but with oem=0
    weights_b = dict(weights_a)
    weights_b['oem'] = 0
    ranked_b = rank_candidates(infocar_specs, candidates_b_with_specs, oem_code, brand, weights=weights_b)
    top_b = ranked_b[0] if ranked_b else None

    # =========================================================================
    # Build result
    # =========================================================================
    result = {
        'infocar_code': infocar_code,
        'ground_truth_natcode': ground_truth_natcode,
        'brand': brand,
        'model': model,
        'infocar_name': infocar_name,
        'oem_code': oem_code,
        'vehicle_class': vehicle_class,

        # Strategy A results (v4 default profile)
        'strategy_a': {
            'candidate_count': len(candidates_a_records),
            'top_natcode': top_a['natcode'] if top_a else None,
            'top_name': top_a['eurotax_name'] if top_a else None,
            'top_score': top_a['score'] if top_a else None,
            'max_score': get_max_score(weights_a),
        },

        # Strategy B results (no OEM scoring)
        'strategy_b': {
            'candidate_count': len(candidates_b),
            'top_natcode': top_b['natcode'] if top_b else None,
            'top_name': top_b['eurotax_name'] if top_b else None,
            'top_score': top_b['score'] if top_b else None,
            'max_score': get_max_score(weights_b),
        },
    }

    # =========================================================================
    # Determine divergence and ground truth match
    # =========================================================================
    a_natcode = result['strategy_a']['top_natcode']
    b_natcode = result['strategy_b']['top_natcode']

    # Divergence: different top candidates
    result['is_divergence'] = (a_natcode != b_natcode)

    # Ground truth match
    result['a_matches_ground_truth'] = (a_natcode == ground_truth_natcode)
    result['b_matches_ground_truth'] = (b_natcode == ground_truth_natcode)
    result['both_match_ground_truth'] = (
        result['a_matches_ground_truth'] and result['b_matches_ground_truth']
    )

    # Score comparison (for divergences)
    if result['is_divergence']:
        a_score = result['strategy_a']['top_score'] or 0
        b_score = result['strategy_b']['top_score'] or 0
        result['score_winner'] = 'A' if a_score > b_score else ('B' if b_score > a_score else 'TIE')
    else:
        result['score_winner'] = 'SAME'

    return result


def run_comparison(
    samples: List[Dict],
    progress_callback: callable = None
) -> Dict:
    """
    Run comparison on all samples.

    Args:
        samples: List of mapping documents
        progress_callback: Optional callback for progress updates

    Returns:
        Dict with all results and summary statistics
    """
    print("Loading Eurotax data from MongoDB...")
    eurotax_data = fetch_eurotax_trims(country="it")

    if not eurotax_data:
        print("ERROR: No Eurotax data loaded. Check VPN connection.")
        return {'error': 'No Eurotax data'}

    # Deduplicate by natcode (keep most complete record)
    important_fields = [
        'name', 'manufacturerCode', 'powerHp', 'powerKw', 'cc',
        'price', 'fuelType', 'bodyType', 'doors', 'gears',
        'gearType', 'tractionType', 'seats', 'mass'
    ]

    by_natcode = {}
    for rec in eurotax_data:
        natcode = rec.get('providerCode')
        if not natcode:
            continue
        natcode_str = str(natcode)
        if natcode_str not in by_natcode:
            by_natcode[natcode_str] = rec
        else:
            existing_score = sum(1 for f in important_fields if by_natcode[natcode_str].get(f))
            new_score = sum(1 for f in important_fields if rec.get(f))
            if new_score > existing_score:
                by_natcode[natcode_str] = rec

    eurotax_data = list(by_natcode.values())
    print(f"Loaded {len(eurotax_data):,} Eurotax records (deduplicated)")

    # Build matcher
    print("Building matcher indexes...")
    matcher = MatcherV4(eurotax_data)
    print(f"Indexed {len(matcher.exact_oem_index):,} OEM codes")
    print(f"Indexed {len(matcher.records_by_make):,} makes")

    # Run comparisons
    results = []
    divergences = []

    stats = {
        'total_samples': len(samples),
        'processed': 0,
        'skipped_not_found': 0,
        'agreements': 0,
        'divergences': 0,
        'a_correct_only': 0,
        'b_correct_only': 0,
        'both_correct': 0,
        'neither_correct': 0,
        'makes': {}
    }

    print(f"\nProcessing {len(samples)} samples...")

    for i, mapping in enumerate(samples):
        source_code = str(mapping.get('sourceCode', ''))
        dest_code = str(mapping.get('destCode', ''))

        if progress_callback:
            progress_callback(i + 1, len(samples))

        if (i + 1) % 20 == 0:
            print(f"  Progress: {i + 1}/{len(samples)} ({(i + 1) * 100 // len(samples)}%)")

        result = run_single_comparison(matcher, source_code, dest_code)

        if result is None:
            stats['skipped_not_found'] += 1
            continue

        stats['processed'] += 1

        # Track make distribution
        make = result['brand']
        stats['makes'][make] = stats['makes'].get(make, 0) + 1

        # Track divergences
        if result['is_divergence']:
            stats['divergences'] += 1
            divergences.append(result)
        else:
            stats['agreements'] += 1

        # Track ground truth accuracy
        if result['a_matches_ground_truth'] and result['b_matches_ground_truth']:
            stats['both_correct'] += 1
        elif result['a_matches_ground_truth']:
            stats['a_correct_only'] += 1
        elif result['b_matches_ground_truth']:
            stats['b_correct_only'] += 1
        else:
            stats['neither_correct'] += 1

        results.append(result)

    # Calculate rates
    if stats['processed'] > 0:
        stats['agreement_rate'] = stats['agreements'] / stats['processed'] * 100
        stats['divergence_rate'] = stats['divergences'] / stats['processed'] * 100
        stats['a_accuracy'] = (stats['a_correct_only'] + stats['both_correct']) / stats['processed'] * 100
        stats['b_accuracy'] = (stats['b_correct_only'] + stats['both_correct']) / stats['processed'] * 100
    else:
        stats['agreement_rate'] = 0
        stats['divergence_rate'] = 0
        stats['a_accuracy'] = 0
        stats['b_accuracy'] = 0

    return {
        'stats': stats,
        'results': results,
        'divergences': divergences
    }


# =============================================================================
# REPORT GENERATION
# =============================================================================

def generate_divergence_report(results: Dict, output_dir: str) -> str:
    """
    Generate detailed divergence report.

    Args:
        results: Comparison results dict
        output_dir: Output directory path

    Returns:
        Path to generated report
    """
    divergences = results.get('divergences', [])
    stats = results.get('stats', {})

    lines = []
    lines.append("=" * 80)
    lines.append("MATCHING STRATEGY COMPARISON: DIVERGENCE REPORT")
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"Generated: {datetime.now().isoformat()}")
    lines.append(f"Total samples processed: {stats.get('processed', 0)}")
    lines.append(f"Total divergences: {stats.get('divergences', 0)}")
    lines.append(f"Agreement rate: {stats.get('agreement_rate', 0):.1f}%")
    lines.append("")

    # Strategy descriptions
    lines.append("-" * 80)
    lines.append("STRATEGY DESCRIPTIONS")
    lines.append("-" * 80)
    lines.append("")
    lines.append("Strategy A (v4 default profile):")
    lines.append("  - Make+model candidate selection, OEM as regular scoring field")
    lines.append("  - OEM: exact +10, cleaned +5, none 0")
    lines.append("  - Max score: 140 pts")
    lines.append("")
    lines.append("Strategy B (No OEM scoring):")
    lines.append("  - Same candidate selection as A (make+model)")
    lines.append("  - OEM weight set to 0")
    lines.append("  - Max score: 130 pts")
    lines.append("")

    # Summary statistics
    lines.append("-" * 80)
    lines.append("SUMMARY STATISTICS")
    lines.append("-" * 80)
    lines.append("")
    lines.append(f"Processed:             {stats.get('processed', 0):,}")
    lines.append(f"Skipped (not found):   {stats.get('skipped_not_found', 0):,}")
    lines.append("")
    lines.append(f"Agreements:            {stats.get('agreements', 0):,} ({stats.get('agreement_rate', 0):.1f}%)")
    lines.append(f"Divergences:           {stats.get('divergences', 0):,} ({stats.get('divergence_rate', 0):.1f}%)")
    lines.append("")

    # Make distribution
    lines.append("Make Distribution (top 15):")
    makes = sorted(stats.get('makes', {}).items(), key=lambda x: -x[1])[:15]
    for make, count in makes:
        pct = count / stats.get('processed', 1) * 100
        lines.append(f"  {make:20s} {count:4,} ({pct:5.1f}%)")
    lines.append("")

    # Divergence details
    lines.append("=" * 80)
    lines.append("DIVERGENCE DETAILS")
    lines.append("=" * 80)
    lines.append("")
    lines.append("For each divergence, compare Strategy A vs Strategy B to determine")
    lines.append("which produced the better match. Mark your assessment at the end.")
    lines.append("")

    for i, div in enumerate(divergences, 1):
        lines.append("=" * 80)
        lines.append(f"DIVERGENCE #{i}: Infocar {div['infocar_code']}")
        lines.append("=" * 80)
        lines.append(f"Source: {div['brand']} {div['model']} - {div['infocar_name']}")
        lines.append(f"OEM Code: {div['oem_code']}")
        lines.append(f"Vehicle Class: {div['vehicle_class']}")
        lines.append("")

        # Strategy A
        a = div['strategy_a']
        lines.append("STRATEGY A (v4 default):")
        lines.append(f"  Candidates: {a['candidate_count']}")
        if a['top_natcode']:
            lines.append(f"  Top: natcode={a['top_natcode']}, score={a['top_score']}/{a['max_score']}")
            lines.append(f"       Name: {a['top_name']}")
        else:
            lines.append("  Top: NO MATCH FOUND")
        lines.append("")

        # Strategy B
        b = div['strategy_b']
        lines.append("STRATEGY B (No OEM):")
        lines.append(f"  Candidates: {b['candidate_count']}")
        if b['top_natcode']:
            lines.append(f"  Top: natcode={b['top_natcode']}, score={b['top_score']}/{b['max_score']}")
            lines.append(f"       Name: {b['top_name']}")
        else:
            lines.append("  Top: NO MATCH FOUND")
        lines.append("")

        # Comparison
        lines.append("COMPARISON:")
        if a['top_score'] and b['top_score']:
            lines.append(f"  A score: {a['top_score']}/{a['max_score']} vs B score: {b['top_score']}/{b['max_score']}")
            lines.append(f"  Score winner: {div['score_winner']}")
        lines.append("")

        # Manual review checkbox
        lines.append("MANUAL REVIEW: [ ] A is correct  [ ] B is correct  [ ] Both valid  [ ] Neither")
        lines.append("")

    # Summary section
    lines.append("=" * 80)
    lines.append("REVIEW SUMMARY")
    lines.append("=" * 80)
    lines.append("")
    lines.append("After reviewing divergences, tally your results:")
    lines.append("")
    lines.append("  A correct:     ____")
    lines.append("  B correct:     ____")
    lines.append("  Both valid:    ____")
    lines.append("  Neither:       ____")
    lines.append("")
    lines.append("Recommendation: _______________________________________________")
    lines.append("")

    report_content = '\n'.join(lines)

    # Write report
    report_path = os.path.join(output_dir, 'divergence_report.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report_content)

    return report_path


def generate_summary(results: Dict, output_dir: str) -> str:
    """
    Generate summary report focusing on divergence comparison.

    Args:
        results: Comparison results dict
        output_dir: Output directory path

    Returns:
        Path to generated summary
    """
    stats = results.get('stats', {})
    divergences = results.get('divergences', [])

    lines = []
    lines.append("# Strategy Comparison Summary")
    lines.append("")
    lines.append(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    lines.append("## Test Overview")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total Samples | {stats.get('processed', 0):,} |")
    lines.append(f"| Agreements | {stats.get('agreements', 0):,} ({stats.get('agreement_rate', 0):.1f}%) |")
    lines.append(f"| Divergences | {stats.get('divergences', 0):,} ({stats.get('divergence_rate', 0):.1f}%) |")
    lines.append("")

    lines.append("## Divergence Score Comparison")
    lines.append("")
    lines.append("When strategies disagree on the top candidate:")
    lines.append("")

    # Calculate score winner distribution for divergences
    a_wins_score = sum(1 for d in divergences if d.get('score_winner') == 'A')
    b_wins_score = sum(1 for d in divergences if d.get('score_winner') == 'B')
    ties = sum(1 for d in divergences if d.get('score_winner') == 'TIE')

    lines.append("| Score Winner | Count |")
    lines.append("|--------------|-------|")
    lines.append(f"| A (default profile) higher | {a_wins_score} |")
    lines.append(f"| B (no OEM) higher | {b_wins_score} |")
    lines.append(f"| Tie (same score) | {ties} |")
    lines.append("")

    lines.append("## Candidate Count Comparison")
    lines.append("")

    # Calculate average candidate counts
    if divergences:
        avg_a_candidates = sum(d['strategy_a']['candidate_count'] for d in divergences) / len(divergences)
        avg_b_candidates = sum(d['strategy_b']['candidate_count'] for d in divergences) / len(divergences)
        lines.append(f"- Strategy A average candidates: {avg_a_candidates:.1f}")
        lines.append(f"- Strategy B average candidates: {avg_b_candidates:.1f}")
        lines.append("")
        lines.append(f"Strategy B finds {avg_b_candidates/avg_a_candidates:.1f}x more candidates on average")
    lines.append("")

    lines.append("## Next Steps")
    lines.append("")
    lines.append("1. Review `divergence_report.md` to manually assess each divergence")
    lines.append("2. For each divergence, determine which strategy picked the better match")
    lines.append("3. Tally results to make final recommendation")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*See `divergence_report.md` for detailed divergence analysis.*")

    summary_content = '\n'.join(lines)

    # Write summary
    summary_path = os.path.join(output_dir, 'summary.md')
    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write(summary_content)

    return summary_path


def save_raw_results(results: Dict, output_dir: str) -> str:
    """
    Save raw results as JSON for further analysis.

    Args:
        results: Comparison results dict
        output_dir: Output directory path

    Returns:
        Path to saved JSON file
    """
    # Create serializable version
    serializable = {
        'stats': results['stats'],
        'divergences': results['divergences'],
        'sample_results': results['results'][:50]  # First 50 for reference
    }

    json_path = os.path.join(output_dir, 'results.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False)

    return json_path


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Compare OEM-based vs Make+Model-only matching strategies'
    )
    parser.add_argument(
        '--samples', '-n',
        type=int,
        default=200,
        help='Number of samples to test (default: 200)'
    )
    parser.add_argument(
        '--output-dir', '-o',
        type=str,
        default=None,
        help='Output directory (default: analysis/YYYY-MM-DD_strategy_comparison)'
    )

    args = parser.parse_args()

    # Set up output directory (in parent's analysis folder)
    if args.output_dir:
        output_dir = args.output_dir
    else:
        date_str = datetime.now().strftime('%Y-%m-%d')
        output_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'analysis',
            f'{date_str}_strategy_comparison'
        )

    os.makedirs(output_dir, exist_ok=True)

    print("=" * 60)
    print("MATCHING STRATEGY COMPARISON TEST")
    print("=" * 60)
    print("")
    print(f"Strategy A: v4 default profile (OEM as scoring field)")
    print(f"Strategy B: No OEM scoring (oem weight = 0)")
    print(f"Output directory: {output_dir}")
    print("")

    # Load .env from parent directory
    env_path = Path(__file__).parent.parent / '.env'
    load_dotenv(env_path)

    # Get samples
    print(f"Fetching {args.samples} sample mappings from MongoDB...")
    samples = get_sample_mappings(limit=args.samples)

    if not samples:
        print("ERROR: No sample mappings found. Check MongoDB connection.")
        return 1

    print(f"Retrieved {len(samples)} samples")
    print("")

    # Run comparison
    results = run_comparison(samples)

    if 'error' in results:
        print(f"ERROR: {results['error']}")
        return 1

    print("")
    print("=" * 60)
    print("GENERATING REPORTS")
    print("=" * 60)

    # Generate reports
    report_path = generate_divergence_report(results, output_dir)
    print(f"Divergence report: {report_path}")

    summary_path = generate_summary(results, output_dir)
    print(f"Summary: {summary_path}")

    json_path = save_raw_results(results, output_dir)
    print(f"Raw results: {json_path}")

    # Print final summary
    stats = results['stats']
    print("")
    print("=" * 60)
    print("FINAL RESULTS")
    print("=" * 60)
    print("")
    print(f"Processed:       {stats.get('processed', 0):,} samples")
    print(f"Agreements:      {stats.get('agreements', 0):,} ({stats.get('agreement_rate', 0):.1f}%)")
    print(f"Divergences:     {stats.get('divergences', 0):,} ({stats.get('divergence_rate', 0):.1f}%)")
    print("")
    print(f"Strategy A accuracy: {stats.get('a_accuracy', 0):.1f}%")
    print(f"Strategy B accuracy: {stats.get('b_accuracy', 0):.1f}%")
    print("")

    return 0


if __name__ == "__main__":
    sys.exit(main())
