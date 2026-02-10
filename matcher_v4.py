# -*- coding: utf-8 -*-
"""
Matcher v4 - OEM as Regular Scoring Field

Key changes from v3:
- OEM is no longer a candidate selection gate (no early stopping)
- All candidates come from make+model containment matching
- OEM match adds points like any other field: exact +10, cleaned +5, none 0
- Vehicle class filtering (CAR/LCV) unchanged
- Total: 157 points maximum (default profile, configurable via WEIGHT_PROFILES)

BUG FIX from v3:
    In v3, OEM codes drove candidate selection via early stopping. If an exact
    OEM match was found, the algorithm stopped and never considered other candidates.
    This caused a bug where the correct match was invisible if it had a different
    OEM code than a wrong-door-count variant that shared the source OEM code
    (e.g., Opel Corsa 5p vs 3p - code 201812128598).

    v4 fix: Demote OEM from a candidate selection gate to a regular scoring field.
    All candidates come from make+model matching, and OEM match simply adds points.
"""

import re
from typing import Dict, List, Set, Tuple, Optional, Any
from collections import defaultdict

from normalizers import (
    normalize_fuel, normalize_body, normalize_transmission, normalize_traction,
    clean_oem_code, normalize_model
)
from vehicle_class import identify_vehicle_class, VehicleClass


# =============================================================================
# SCORING WEIGHT PROFILES
# =============================================================================

WEIGHT_PROFILES = {
    'default': {
        'price': 25,
        'hp': 20,
        'trim': 15,          # Derived: extracted from vehicle name via known keyword list
        'cc': 15,
        'fuel': 15,
        'sellable': 10,      # Exact window match: 10, overlap: 5, no overlap: 0
        'body': 10,
        'oem': 10,           # v4: Regular scored field (exact +10, cleaned +5, none 0)
        'model': 5,          # Exact normalized match only (containment = 0)
        'transmission': 5,
        'traction': 5,
        'doors': 5,
        'name': 5,
        'seats': 3,
        'gears': 3,
        'kw': 3,
        'mass': 3,
    },
    'flat': {
        'price': 10,
        'hp': 10,
        'trim': 10,
        'cc': 10,
        'fuel': 10,
        'sellable': 10,
        'body': 10,
        'oem': 10,
        'model': 10,
        'transmission': 10,
        'traction': 10,
        'doors': 10,
        'name': 10,
        'seats': 3,
        'gears': 3,
        'kw': 3,
        'mass': 3,
    },
    'trim_heavy': {
        'price': 5,
        'hp': 10,
        'trim': 40,
        'cc': 10,
        'fuel': 10,
        'sellable': 20,
        'body': 10,
        'oem': 5,
        'model': 5,
        'transmission': 5,
        'traction': 5,
        'doors': 5,
        'name': 5,
        'seats': 3,
        'gears': 3,
        'kw': 3,
        'mass': 3,
    },
}

DEFAULT_PROFILE = 'default'

# Backward-compatible reference to default profile weights
WEIGHTS = WEIGHT_PROFILES[DEFAULT_PROFILE]


def get_max_score(weights=None):
    """Compute max possible score from a weights dict."""
    return sum((weights or WEIGHTS).values())


# =============================================================================
# TRIM TOKENS FOR SCORING
# =============================================================================

TRIM_TOKENS = {
    # Performance / sporty
    'sport', 'sportline', 's-line', 's line', 'sline',
    'amg', 'amg line', 'm sport', 'msport', 'r-line', 'r line', 'rline',
    'gt line', 'gtline', 'gt-line', 'n line', 'nline',
    'gs line', 'gsline', 'gs-line',
    'fr', 'cupra', 'st', 'rs', 'vrs', 'gti', 'gtd', 'gte', 'gt', 'gts',
    'r-design', 'r-dynamic', 'polestar', 'veloce', 'competition',
    'performance', 'sprint', 'racing', 's-design',
    # Luxury / premium
    'executive', 'premium', 'luxury', 'exclusive', 'ultimate',
    'inscription', 'designo', 'maybach', 'lusso', 'tributo',
    'prestige', 'platinum', 'vip', 'deluxe', 'luxe',
    # Equipment levels
    'business', 'businessline', 'style', 'elegance', 'ambition', 'ambiente',
    'comfort', 'life', 'edition', 'special', 'limited', 'advanced', 'tech',
    'active', 'plus', 'pro', 'base', 'standard', 'lounge', 'pop', 'cult',
    'icon', 'iconic', 'trend', 'essential', 'select', 'selection', 'core',
    'pure', 'prime', 'entry', 'move', 'access', 'modern', 'individual',
    'signature', 'collection', 'premiere', 'bright', 'fresh',
    # Renault / Dacia
    'dynamique', 'seduction', 'initiale', 'intens', 'intense', 'zen',
    'expression', 'laureate', 'equilibre', 'ambiance', 'energy',
    'esprit', 'hypnotic', 'classique', 'authentique', 'invite',
    'techroad', 'stepway', 'wave', 'evolve',
    # Peugeot / Citroen / DS
    'shine', 'allure', 'feline', 'feel', 'live', 'uptown',
    'sense', 'chic', 'hype', 'mylife', 'allstreet', 'crossway',
    'bastille', 'rivoli', 'opera', 'etoile', 'sesame', 'trocadero',
    'extravagance', 'irresistible', 'attitude',
    # Fiat / Alfa Romeo / Maserati / Abarth
    'easy', 'distinctive', 'eletta', 'progression', 'dolcevita',
    'mirror', 'ecochic', 'elective', 'eccelsa', 'duel', 'goldplay',
    'passion', 'glam', 'trekking', 'competizione', 'quadrifoglio',
    'trofeo', 'modena',
    # VW group / Seat / Skoda
    'comfortline', 'highline', 'trendline', 'xcellence', 'xperience',
    'admired', 'monte carlo', 'scout', 'scoutline', 'connectline',
    'emotion',
    # BMW
    'xline', 'x-line', 'advantage', 'sport line', 'luxury line',
    # Mercedes
    'avantgarde', 'progressive', 'black edition', 'dark',
    'night edition', 'atmosphere',
    # Volvo
    'kinetic', 'summum', 'design',
    # Opel
    'cosmo', 'attraction', 'enjoy', 'youngster',
    # Ford
    'titanium', 'vignale', 'zetec', 'ghia', 'wildtrak',
    'connected', 'st-line',
    # Nissan
    'acenta', 'tekna', 'visia', 'n-connecta', 'n-design', 'n-joy',
    # Honda / Mazda
    'instyle', 'homura', 'takumi',
    # Hyundai / Kia / Genesis
    'essentia', 'calligraphy', 'exceed',
    # Suzuki
    'attiva', 'excite',
    # Jaguar / Land Rover
    'hse', 'se', 'dynamic', 'momentum', 'autobiography',
    'portfolio', 'vogue',
    # Jeep
    'longitude', 'altitude', 'overland', 'trailhawk', 'rebel',
    'summit', 'sahara', 'rubicon',
    # MG / other
    'trophy', 'futura', 'classic', 'favoured',
    'blackline', 'startline', 'ocean', 'outdoor', 'trail',
    # Special editions
    'anniversary', 'innovation', 'advance', 'connect',
    'first edition', 'launch', 'techno',
    'evolution', 'ultra', 'extreme',
    'authentic', 'lifestyle', 'pulse',
    'junior', 'club',
    # Variants / drivetrain
    'urban', 'city', 'cross', 'adventure', 'offroad', 'allroad', 'quattro',
    '4matic', 'xdrive', '4x4', '4wd', 'traction',
}


def extract_trim_tokens(name: str) -> Set[str]:
    """Extract trim level tokens from vehicle name."""
    if not name:
        return set()

    name_lower = name.lower()
    found = set()

    for token in TRIM_TOKENS:
        pattern = r'\b' + re.escape(token) + r'\b'
        if re.search(pattern, name_lower):
            found.add(token)

    return found


# =============================================================================
# MATCHER CLASS
# =============================================================================

class MatcherV4:
    """
    Vehicle matcher v4 with:
    - Make+model candidate selection (no OEM early stopping)
    - OEM as a regular scoring field
    - Vehicle class filtering (CAR/LCV)
    """

    def __init__(self, eurotax_records: List[Dict]):
        """Build indexes from Eurotax records."""
        self.eurotax_records = eurotax_records

        # OEM code indexes (kept for scoring, not for candidate selection)
        self.exact_oem_index: Dict[str, List[Dict]] = defaultdict(list)
        self.cleaned_oem_index: Dict[str, Dict[str, List[Dict]]] = defaultdict(lambda: defaultdict(list))

        # Make+Model index (primary candidate selection)
        self.records_by_make: Dict[str, List[Dict]] = defaultdict(list)

        # Build indexes
        for rec in eurotax_records:
            oem = (rec.get('manufacturerCode') or '').upper().strip()
            make = (rec.get('normalizedMake') or '').upper().strip()
            model = normalize_model((rec.get('normalizedModel') or '').lower().strip())

            # Determine vehicle class for this record
            body_type = normalize_body(rec.get('bodyType', ''))
            rec['_vehicle_class'] = identify_vehicle_class(make, model, body_type)

            # OEM indexes (used during scoring to determine OEM match type)
            if oem:
                self.exact_oem_index[oem].append(rec)

                cleaned = clean_oem_code(oem, make)
                if cleaned:
                    self.cleaned_oem_index[make][cleaned.upper()].append(rec)

            # Make index for candidate selection
            if make:
                self.records_by_make[make].append(rec)

        # Convert to regular dicts
        self.exact_oem_index = dict(self.exact_oem_index)
        self.cleaned_oem_index = {k: dict(v) for k, v in self.cleaned_oem_index.items()}
        self.records_by_make = dict(self.records_by_make)

    def find_candidates(
        self,
        brand: str,
        model: str,
        vehicle_class: str
    ) -> List[Dict]:
        """
        Stage 1: Find candidates using make+model containment.

        v4 change: OEM is no longer used for candidate selection.
        All candidates come from make+model matching with vehicle class filter.

        Args:
            brand: Normalized make (uppercase)
            model: Normalized model (lowercase)
            vehicle_class: VehicleClass.CAR or VehicleClass.LCV

        Returns:
            List of candidate records
        """
        if not brand:
            return []

        if not model:
            return []

        same_make = self.records_by_make.get(brand, [])
        if not same_make:
            return []

        # Normalize source model (expand abbreviations, remove year suffixes)
        source_model_norm = normalize_model(model)
        source_spaceless = source_model_norm.replace(' ', '')

        candidates = []
        for rec in same_make:
            # Check vehicle class
            if rec.get('_vehicle_class') != vehicle_class:
                continue

            eurotax_model = (rec.get('normalizedModel') or '').lower().strip()
            if not eurotax_model:
                continue

            # Normalize target model
            target_model_norm = normalize_model(eurotax_model)

            # Model containment (either direction) using normalized names
            # Spaceless variants handle inconsistent spacing (e.g., "500 x" vs "500x")
            target_spaceless = target_model_norm.replace(' ', '')
            if (source_model_norm in target_model_norm or
                target_model_norm in source_model_norm or
                model in eurotax_model or
                eurotax_model in model or
                source_spaceless in target_spaceless or
                target_spaceless in source_spaceless):
                candidates.append(rec)

        return candidates


# =============================================================================
# SCORING FUNCTIONS
# =============================================================================

def score_price(source_price: Optional[float], target_price: Optional[float], weights=None) -> int:
    """Score price proximity."""
    w = (weights or WEIGHTS)['price']
    if not source_price or not target_price:
        return 0
    if source_price <= 0 or target_price <= 0:
        return 0

    diff_pct = abs(source_price - target_price) / max(source_price, target_price) * 100

    if diff_pct <= 10:
        return w  # full
    elif diff_pct <= 20:
        return int(w * 0.6)  # 60%
    elif diff_pct <= 35:
        return int(w * 0.3)  # 30%
    return 0


def score_hp(source_hp: Optional[int], target_hp: Optional[int], weights=None) -> int:
    """Score HP proximity."""
    w = (weights or WEIGHTS)['hp']
    if not source_hp or not target_hp:
        return 0

    diff = abs(source_hp - target_hp)

    if diff == 0:
        return w
    elif diff <= 5:
        return int(w * 0.8)
    elif diff <= 10:
        return int(w * 0.5)
    return 0


def score_cc(source_cc: Optional[int], target_cc: Optional[int], weights=None) -> int:
    """Score CC proximity."""
    w = (weights or WEIGHTS)['cc']
    if not source_cc or not target_cc:
        return 0

    diff = abs(source_cc - target_cc)

    if diff == 0:
        return w
    elif diff <= 50:
        return int(w * 0.8)
    elif diff <= 100:
        return int(w * 0.5)
    return 0


def score_fuel(source_fuel: str, target_fuel: str, weights=None) -> int:
    """Score fuel type match using normalized values."""
    w = (weights or WEIGHTS)['fuel']
    norm_source = normalize_fuel(source_fuel)
    norm_target = normalize_fuel(target_fuel)

    if not norm_source or not norm_target:
        return 0
    if norm_source == norm_target:
        return w

    # Partial match for hybrid variants
    if 'HYBRID' in norm_source and 'HYBRID' in norm_target:
        return int(w * 0.7)

    return 0


def score_body(source_body: str, target_body: str, weights=None) -> int:
    """Score body type match using normalized values."""
    w = (weights or WEIGHTS)['body']
    norm_source = normalize_body(source_body)
    norm_target = normalize_body(target_body)

    if not norm_source or not norm_target:
        return 0
    if norm_source == norm_target:
        return w

    return 0


def score_transmission(source_trans: str, target_trans: str, source_fuel: str, weights=None) -> int:
    """Score transmission match (lenient for EVs)."""
    w = (weights or WEIGHTS)['transmission']
    norm_source = normalize_transmission(source_trans)
    norm_target = normalize_transmission(target_trans)

    if not norm_source or not norm_target:
        return 0
    if norm_source == norm_target:
        return w

    # EVs often have different transmission encoding
    if normalize_fuel(source_fuel) == 'ELECTRIC':
        return int(w * 0.5)

    return 0


def score_traction(source_traction: str, target_traction: str, weights=None) -> int:
    """Score traction match."""
    w = (weights or WEIGHTS)['traction']
    norm_source = normalize_traction(source_traction)
    norm_target = normalize_traction(target_traction)

    if not norm_source or not norm_target:
        return 0
    if norm_source == norm_target:
        return w

    return 0


def score_doors(source_doors: Optional[int], target_doors: Optional[int], weights=None) -> int:
    """Score doors match (allow off-by-one for hatch counting)."""
    w = (weights or WEIGHTS)['doors']
    if not source_doors or not target_doors:
        return 0

    diff = abs(source_doors - target_doors)

    if diff == 0:
        return w
    elif diff == 1:
        return int(w * 0.6)

    return 0


def score_seats(source_seats: Optional[int], target_seats: Optional[int], weights=None) -> int:
    """Score seats match (exact or off-by-one)."""
    w = (weights or WEIGHTS)['seats']
    if not source_seats or not target_seats:
        return 0

    diff = abs(source_seats - target_seats)

    if diff == 0:
        return w
    elif diff == 1:
        return int(w * 0.6)

    return 0


def score_gears(source_gears: Optional[int], target_gears: Optional[int], weights=None) -> int:
    """Score gears match (exact or off-by-one)."""
    w = (weights or WEIGHTS)['gears']
    if not source_gears or not target_gears:
        return 0

    diff = abs(source_gears - target_gears)

    if diff == 0:
        return w
    elif diff == 1:
        return int(w * 0.6)

    return 0


def score_kw(source_kw: Optional[int], target_kw: Optional[int], weights=None) -> int:
    """Score KW proximity."""
    w = (weights or WEIGHTS)['kw']
    if not source_kw or not target_kw:
        return 0

    diff = abs(source_kw - target_kw)

    if diff == 0:
        return w
    elif diff <= 5:
        return int(w * 0.8)
    elif diff <= 10:
        return int(w * 0.5)
    return 0


def score_mass(source_mass: Optional[float], target_mass: Optional[float], weights=None) -> int:
    """Score mass proximity (percentage tolerance)."""
    w = (weights or WEIGHTS)['mass']
    if not source_mass or not target_mass:
        return 0
    if source_mass <= 0 or target_mass <= 0:
        return 0

    diff_pct = abs(source_mass - target_mass) / max(source_mass, target_mass) * 100

    if diff_pct <= 5:
        return w
    elif diff_pct <= 10:
        return int(w * 0.6)
    return 0


def score_trim(source_name: str, target_name: str, weights=None) -> Tuple[int, Set[str], Set[str], Set[str]]:
    """
    Score trim level match.

    Returns: (score, matched_trims, source_only, target_only)
    """
    w = (weights or WEIGHTS)['trim']
    source_trims = extract_trim_tokens(source_name)
    target_trims = extract_trim_tokens(target_name)

    matched = source_trims & target_trims
    source_only = source_trims - target_trims
    target_only = target_trims - source_trims

    if not source_trims and not target_trims:
        return 0, set(), set(), set()

    if not source_trims or not target_trims:
        return 0, matched, source_only, target_only

    if matched:
        match_ratio = len(matched) / max(len(source_trims), len(target_trims))
        return int(w * match_ratio), matched, source_only, target_only

    return 0, matched, source_only, target_only


def score_name_similarity(source_name: str, target_name: str, weights=None) -> int:
    """Score overall name token similarity."""
    w = (weights or WEIGHTS)['name']
    if not source_name or not target_name:
        return 0

    # Tokenize
    source_tokens = set(re.findall(r'\b\w+\b', source_name.lower()))
    target_tokens = set(re.findall(r'\b\w+\b', target_name.lower()))

    # Remove common noise words
    noise = {'cv', 'hp', 'kw', 'auto', 'aut', 'man', 'the', 'and', 'di', 'da'}
    source_tokens -= noise
    target_tokens -= noise

    if not source_tokens or not target_tokens:
        return 0

    common = source_tokens & target_tokens
    similarity = len(common) / max(len(source_tokens), len(target_tokens))

    return int(w * similarity)


def score_model(source_model: str, target_model: str, weights=None) -> int:
    """Score model name match (exact normalized only, space-insensitive)."""
    w = (weights or WEIGHTS)['model']
    if not source_model or not target_model:
        return 0
    s = source_model.strip().lower()
    t = target_model.strip().lower()
    if s == t:
        return w
    # Spaceless comparison for inconsistent spacing (e.g., "500 x" vs "500x")
    if s.replace(' ', '') == t.replace(' ', ''):
        return w
    return 0


def score_sellable_window(
    source_begin: Optional[int], source_end: Optional[int],
    target_begin: Optional[int], target_end: Optional[int],
    weights=None
) -> int:
    """
    Score sellable window compatibility.

    - Exact match (same start AND same end): full pts
    - Overlap (windows intersect but differ): 50%
    - No overlap or missing data: 0 pts
    """
    w = (weights or WEIGHTS)['sellable']
    if source_begin is None or target_begin is None:
        return 0

    # Treat missing end as open-ended
    s_end = source_end if source_end else 9999
    t_end = target_end if target_end else 9999

    # No overlap
    if source_begin > t_end or target_begin > s_end:
        return 0

    # Exact match: same start AND same end
    if source_begin == target_begin and s_end == t_end:
        return w

    # Overlap but not exact
    return int(w * 0.5)


def score_oem(
    source_oem: str,
    target_oem: str,
    brand: str,
    weights=None
) -> Tuple[int, str]:
    """
    Score OEM code match (NEW in v4 - regular scoring field).

    Args:
        source_oem: Source vehicle OEM code
        target_oem: Target candidate OEM code
        brand: Brand name for cleaned OEM comparison
        weights: Optional weights dict

    Returns:
        Tuple of (score, match_type)
        match_type: 'EXACT', 'CLEANED', 'NONE'
    """
    w = (weights or WEIGHTS)['oem']
    if not source_oem or not target_oem:
        return 0, 'NONE'

    source_upper = source_oem.upper().strip()
    target_upper = target_oem.upper().strip()

    # Exact OEM match: full weight
    if source_upper == target_upper:
        return w, 'EXACT'

    # Cleaned OEM match: half weight
    source_cleaned = clean_oem_code(source_upper, brand)
    target_cleaned = clean_oem_code(target_upper, brand)

    if source_cleaned and target_cleaned:
        if source_cleaned.upper() == target_cleaned.upper():
            return int(w * 0.5), 'CLEANED'

    return 0, 'NONE'


# =============================================================================
# MAIN SCORING FUNCTION
# =============================================================================

def score_candidate(
    source_specs: Dict[str, Any],
    target_specs: Dict[str, Any],
    source_oem: str,
    target_oem: str,
    brand: str,
    weights=None
) -> Tuple[int, Dict[str, Any]]:
    """
    Score a single candidate.

    Args:
        source_specs: Source vehicle specs dict
        target_specs: Target candidate specs dict
        source_oem: Source vehicle OEM code
        target_oem: Target candidate OEM code
        brand: Brand name for OEM cleaning
        weights: Optional weights dict (defaults to WEIGHTS)

    Returns:
        Tuple of (total_score, breakdown_dict)
    """
    w = weights or WEIGHTS
    breakdown = {}

    # Price
    breakdown['price'] = score_price(
        source_specs.get('price'),
        target_specs.get('price'),
        weights=w
    )

    # HP
    breakdown['hp'] = score_hp(
        source_specs.get('hp'),
        target_specs.get('hp'),
        weights=w
    )

    # CC
    breakdown['cc'] = score_cc(
        source_specs.get('cc'),
        target_specs.get('cc'),
        weights=w
    )

    # Fuel
    breakdown['fuel'] = score_fuel(
        source_specs.get('fuel', ''),
        target_specs.get('fuel', ''),
        weights=w
    )

    # Body
    breakdown['body'] = score_body(
        source_specs.get('body', ''),
        target_specs.get('body', ''),
        weights=w
    )

    # Transmission
    breakdown['transmission'] = score_transmission(
        source_specs.get('gear_type', ''),
        target_specs.get('gear_type', ''),
        source_specs.get('fuel', ''),
        weights=w
    )

    # Traction
    breakdown['traction'] = score_traction(
        source_specs.get('traction', ''),
        target_specs.get('traction', ''),
        weights=w
    )

    # Doors
    breakdown['doors'] = score_doors(
        source_specs.get('doors'),
        target_specs.get('doors'),
        weights=w
    )

    # Seats
    breakdown['seats'] = score_seats(
        source_specs.get('seats'),
        target_specs.get('seats'),
        weights=w
    )

    # Gears
    breakdown['gears'] = score_gears(
        source_specs.get('gears'),
        target_specs.get('gears'),
        weights=w
    )

    # KW
    breakdown['kw'] = score_kw(
        source_specs.get('kw'),
        target_specs.get('kw'),
        weights=w
    )

    # Mass
    breakdown['mass'] = score_mass(
        source_specs.get('mass'),
        target_specs.get('mass'),
        weights=w
    )

    # Trim
    trim_score, matched_trims, source_only, target_only = score_trim(
        source_specs.get('name', ''),
        target_specs.get('name', ''),
        weights=w
    )
    breakdown['trim'] = trim_score
    breakdown['_trim_matched'] = list(matched_trims)
    breakdown['_trim_source_only'] = list(source_only)
    breakdown['_trim_target_only'] = list(target_only)

    # Name similarity
    breakdown['name'] = score_name_similarity(
        source_specs.get('name', ''),
        target_specs.get('name', ''),
        weights=w
    )

    # Model (exact normalized match)
    breakdown['model'] = score_model(
        source_specs.get('model', ''),
        target_specs.get('model', ''),
        weights=w
    )

    # Sellable window
    breakdown['sellable'] = score_sellable_window(
        source_specs.get('sellable_begin'),
        source_specs.get('sellable_end'),
        target_specs.get('sellable_begin'),
        target_specs.get('sellable_end'),
        weights=w
    )

    # OEM match (v4 - regular scored field)
    oem_score, oem_match_type = score_oem(source_oem, target_oem, brand, weights=w)
    breakdown['oem'] = oem_score
    breakdown['_oem_match_type'] = oem_match_type

    # Calculate total
    total_score = sum(v for k, v in breakdown.items() if not k.startswith('_'))

    return total_score, breakdown


def rank_candidates(
    source_specs: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    source_oem: str,
    brand: str,
    weights=None
) -> List[Dict[str, Any]]:
    """
    Score and rank all candidates.

    Args:
        source_specs: Source vehicle specs
        candidates: List of candidate dicts with 'specs' and 'eurotax_code' keys
        source_oem: Source vehicle OEM code (for per-candidate OEM scoring)
        brand: Brand name (for OEM cleaning)
        weights: Optional weights dict (defaults to WEIGHTS)

    Returns:
        List of candidates sorted by score (highest first)
    """
    scored = []

    for cand in candidates:
        target_specs = cand.get('specs', {})
        target_oem = cand.get('eurotax_code', '')

        score, breakdown = score_candidate(
            source_specs, target_specs,
            source_oem, target_oem, brand,
            weights=weights
        )

        scored_cand = cand.copy()
        scored_cand['score'] = score
        scored_cand['breakdown'] = breakdown
        scored_cand['oem_match_type'] = breakdown.get('_oem_match_type', 'NONE')
        # Extract trim fields for UI
        scored_cand['trim_matched'] = breakdown.get('_trim_matched', [])
        scored_cand['trim_source_only'] = breakdown.get('_trim_source_only', [])
        scored_cand['trim_target_only'] = breakdown.get('_trim_target_only', [])
        scored.append(scored_cand)

    # Sort by score descending
    scored.sort(key=lambda x: -x['score'])

    return scored


def get_confidence(score: int, max_score: int = 157) -> str:
    """
    Get confidence level from score using percentage-based thresholds.

    Thresholds (percentage of max_score):
    - PERFECT: >= 71.4%  (113/157 with default weights)
    - LIKELY:  >= 53.5%  (84/157 with default weights)
    - POSSIBLE: >= 28.5% (45/157 with default weights)
    - UNLIKELY: < 28.5%
    """
    if max_score <= 0:
        return 'UNLIKELY'
    pct = score / max_score
    if pct >= 0.714:
        return 'PERFECT'
    elif pct >= 0.535:
        return 'LIKELY'
    elif pct >= 0.285:
        return 'POSSIBLE'
    else:
        return 'UNLIKELY'
