# -*- coding: utf-8 -*-
"""
Normalizers - v4

Centralized normalization functions for all vehicle attributes.
All comparisons in v4 use normalized values for consistency.
"""

import re
from typing import Optional


# =============================================================================
# FUEL TYPE NORMALIZATION
# =============================================================================

def normalize_fuel(fuel: Optional[str]) -> str:
    """
    Normalize fuel type to standard values:
    DIESEL, PETROL, HYBRID_PETROL, HYBRID_DIESEL, ELECTRIC, LPG, CNG

    Args:
        fuel: Raw fuel type string

    Returns:
        Normalized fuel type (uppercase) or empty string if unknown
    """
    if not fuel:
        return ''

    fuel_lower = fuel.lower().strip()

    # Electric (pure)
    if fuel_lower in ('elettrica', 'elettrico', 'electric'):
        return 'ELECTRIC'

    # Hybrid detection (must come before base fuel checks)
    if 'ibrido' in fuel_lower or 'ibrida' in fuel_lower or 'hybrid' in fuel_lower:
        if 'plug-in' in fuel_lower or 'plug in' in fuel_lower or 'phev' in fuel_lower:
            if 'diesel' in fuel_lower or 'gasolio' in fuel_lower:
                return 'HYBRID_DIESEL'  # PHEV diesel treated as hybrid diesel
            return 'HYBRID_PETROL'  # PHEV petrol treated as hybrid petrol
        if 'diesel' in fuel_lower or 'gasolio' in fuel_lower:
            return 'HYBRID_DIESEL'
        return 'HYBRID_PETROL'

    # Electric combinations (after hybrid check)
    if 'elettric' in fuel_lower:
        if 'benzina' in fuel_lower or 'petrol' in fuel_lower:
            return 'HYBRID_PETROL'
        if 'gasolio' in fuel_lower or 'diesel' in fuel_lower:
            return 'HYBRID_DIESEL'
        return 'ELECTRIC'

    # Base fuels
    if 'diesel' in fuel_lower or 'gasolio' in fuel_lower:
        return 'DIESEL'
    if 'benzina' in fuel_lower or 'petrol' in fuel_lower or 'gasoline' in fuel_lower:
        return 'PETROL'
    if 'metano' in fuel_lower or 'cng' in fuel_lower:
        return 'CNG'
    if 'gpl' in fuel_lower or 'lpg' in fuel_lower:
        return 'LPG'

    return ''


# =============================================================================
# BODY TYPE NORMALIZATION
# =============================================================================

def normalize_body(body: Optional[str]) -> str:
    """
    Normalize body type to standard values.

    Uses substring matching with priority ordering to handle compound Italian
    body type names from both Infocar and Eurotax sources.

    Validated against 57,376 existing Infocar-to-Eurotax matched pairs.

    Args:
        body: Raw body type string

    Returns:
        Normalized body type (uppercase) or empty string if unknown
    """
    if not body:
        return ''

    body_lower = body.lower().strip()
    # Remove door count suffixes like "3 porte", "5 porte"
    body_lower = re.sub(r'\s*\d+\s*port[ei]', '', body_lower).strip()
    # Remove trailing qualifiers like "Outdoor"
    body_lower = body_lower.strip()

    # --- Priority ordering matters for compound names ---

    # 1. PICKUP (before VAN - "microfurgone pick-up" contains both)
    if 'pick-up' in body_lower or 'pick up' in body_lower or 'pickup' in body_lower:
        return 'PICKUP'

    # 2. BUS
    if 'autobus' in body_lower or 'scuolabus' in body_lower or body_lower == 'bus':
        return 'BUS'

    # 3. PLATFORM (partial - cassone/carro before CHASSIS to catch "cabinato con cassone")
    if 'cassone' in body_lower or 'carro' in body_lower:
        return 'PLATFORM'

    # 4. VAN (before CHASSIS - "cabinato allestito" maps to Furgone in matched pairs)
    if ('furgone' in body_lower or body_lower == 'van' or 'furgonato' in body_lower
            or 'scudato' in body_lower or 'pulmino' in body_lower
            or 'promiscuo' in body_lower or 'combi' in body_lower
            or 'allestito' in body_lower):
        return 'VAN'

    # 5. CHASSIS (cabinato/telaio - after VAN and PLATFORM special cases)
    if 'cabinato' in body_lower or 'telaio' in body_lower or body_lower == 'chassis' or body_lower == 'cab':
        return 'CHASSIS'

    # 6. PLATFORM (remainder - pianale without cabinato)
    if 'pianale' in body_lower or 'platform' in body_lower:
        return 'PLATFORM'

    # 7. SUV (including off-road: fuoristrada, torpedo, FST)
    if ('suv' in body_lower or 'crossover' in body_lower or 'fuoristrada' in body_lower
            or 'torpedo' in body_lower or body_lower == 'fst'):
        return 'SUV'

    # 8. WAGON
    if 'wagon' in body_lower or 'familiare' in body_lower or 'estate' in body_lower or 'touring' in body_lower:
        return 'WAGON'

    # 9. CONVERTIBLE (before COUPE - "coupe-cabriolet" should be CONVERTIBLE)
    if ('cabrio' in body_lower or 'spider' in body_lower or 'roadster' in body_lower
            or 'convertible' in body_lower or 'apribile' in body_lower or 'barchetta' in body_lower):
        return 'CONVERTIBLE'

    # 10. COUPE ("coup" handles both "coupe" and "coupe" with accented e encoding)
    if 'coup' in body_lower:
        return 'COUPE'

    # 11. MPV (before SEDAN - "berlina multispazio" should be MPV not SEDAN)
    if 'monovolume' in body_lower or 'mpv' in body_lower or 'minivan' in body_lower or 'multispazio' in body_lower:
        return 'MPV'

    # 12. HATCHBACK
    if 'hatchback' in body_lower:
        return 'HATCHBACK'

    # 13. SEDAN (last - fallback for berlina variants)
    if 'berlina' in body_lower or 'sedan' in body_lower or '3 volumi' in body_lower:
        return 'SEDAN'

    return ''


# =============================================================================
# TRANSMISSION NORMALIZATION
# =============================================================================

def normalize_transmission(trans: Optional[str]) -> str:
    """
    Normalize transmission type to standard values:
    AUTOMATIC, MANUAL, CVT

    Args:
        trans: Raw transmission type string

    Returns:
        Normalized transmission type (uppercase) or empty string if unknown
    """
    if not trans:
        return ''

    trans_lower = trans.lower().strip()

    if any(kw in trans_lower for kw in ('automatic', 'auto', 'dsg', 'dct', 'robotizzato', 'sequenziale')):
        return 'AUTOMATIC'
    if any(kw in trans_lower for kw in ('manual', 'manuale', 'meccanico')):
        return 'MANUAL'
    if 'cvt' in trans_lower:
        return 'CVT'

    return ''


# =============================================================================
# TRACTION NORMALIZATION
# =============================================================================

def normalize_traction(traction: Optional[str]) -> str:
    """
    Normalize traction type to standard values:
    FWD, RWD, AWD

    Args:
        traction: Raw traction type string

    Returns:
        Normalized traction type (uppercase) or empty string if unknown
    """
    if not traction:
        return ''

    traction_lower = traction.lower().strip()

    if any(kw in traction_lower for kw in ('anteriore', 'front', 'fwd')):
        return 'FWD'
    if any(kw in traction_lower for kw in ('posteriore', 'rear', 'rwd')):
        return 'RWD'
    if any(kw in traction_lower for kw in ('integrale', 'all-wheel', 'awd', '4x4', '4wd')):
        return 'AWD'

    return ''


# =============================================================================
# MODEL NAME NORMALIZATION
# =============================================================================

# Model abbreviations and synonyms (lowercase)
MODEL_EXPANSIONS = {
    # Land Rover abbreviations
    'rr': 'range rover',
    'r.r.': 'range rover',
    'rre': 'range rover evoque',
    'rrs': 'range rover sport',
    'rrv': 'range rover velar',
    # BMW abbreviations
    'x1': 'x1',
    'x2': 'x2',
    'x3': 'x3',
    'x4': 'x4',
    'x5': 'x5',
    'x6': 'x6',
    'x7': 'x7',
    # Mercedes abbreviations
    'cla': 'cla',
    'cls': 'cls',
    'gla': 'gla',
    'glb': 'glb',
    'glc': 'glc',
    'gle': 'gle',
    'gls': 'gls',
    'eqa': 'eqa',
    'eqb': 'eqb',
    'eqc': 'eqc',
    'eqe': 'eqe',
    'eqs': 'eqs',
    # Alfa Romeo
    'ar': 'alfa romeo',
    # Volkswagen
    'vw': 'volkswagen',
}


def normalize_model(model: Optional[str]) -> str:
    """
    Normalize model name by expanding common abbreviations.

    Args:
        model: Raw model name

    Returns:
        Normalized model name (lowercase)
    """
    if not model:
        return ''

    model_lower = model.lower().strip()

    # Remove year suffixes like "ii 2024", "v 2020", "iii", "iv", etc.
    # But keep the base model name
    model_clean = re.sub(r'\s+(i{1,3}|iv|v|vi|vii|viii)(\s+\d{4})?$', '', model_lower)
    model_clean = re.sub(r'\s+\d{4}$', '', model_clean)  # Remove trailing year

    # DS models: collapse "ds N" -> "dsN" (Infocar uses space, Eurotax doesn't)
    model_clean = re.sub(r'^ds\s+(\d)$', r'ds\1', model_clean)
    model_clean = re.sub(r'^ds\s+(\d)\b', r'ds\1', model_clean)

    # Expand abbreviations
    words = model_clean.split()
    expanded_words = []
    for word in words:
        if word in MODEL_EXPANSIONS:
            expanded_words.append(MODEL_EXPANSIONS[word])
        else:
            expanded_words.append(word)

    return ' '.join(expanded_words)


# =============================================================================
# OEM CODE CLEANING (Brand-Specific)
# =============================================================================

def clean_oem_code(oem: str, brand: str) -> Optional[str]:
    """
    Apply brand-specific OEM code cleaning transformations.

    Args:
        oem: Raw OEM code (will be uppercased)
        brand: Brand name (case-insensitive)

    Returns:
        Cleaned OEM code, or None if no transformation applies
    """
    if not oem:
        return None

    oem = oem.upper().strip()
    brand = (brand or '').upper().strip()

    # Renault: Remove 2-3 char prefix before digits
    if brand == 'RENAULT':
        # Pattern: XX(X)digit... -> remove prefix
        match = re.match(r'^[A-Z]{2,3}\d(.+)$', oem)
        if match and len(match.group(1)) >= 5:
            return match.group(1)
        # Alternative pattern
        match = re.match(r'^[A-Z]{2}\d{2}(.+)$', oem)
        if match and len(match.group(1)) >= 5:
            return match.group(1)
        # Generic: drop first 3 chars if long enough
        if len(oem) > 6:
            return oem[3:]

    # Dacia: Similar to Renault
    elif brand == 'DACIA':
        match = re.match(r'^[A-Z0-9]{2,3}\d?([A-Z].+)$', oem)
        if match and len(match.group(1)) >= 5:
            return match.group(1)
        if len(oem) > 8:
            return oem[3:]

    # Volkswagen: Remove -XXX suffix
    elif brand == 'VOLKSWAGEN':
        if re.search(r'-[A-Z0-9]{3}$', oem):
            return oem[:-4]  # Remove "-XXX" (4 chars)

    # Skoda: Remove -XXX suffix (similar to VW)
    elif brand == 'SKODA':
        for suffix in ['RAA', 'WI1']:
            if oem.endswith(suffix):
                return oem[:-len(suffix)]

    # Mercedes: Remove -XX suffix
    elif brand in ('MERCEDES', 'MERCEDES-BENZ'):
        match = re.match(r'^(.+DL\d)', oem)
        if match:
            return match.group(1)
        if re.search(r'-[A-Z0-9]{2}$', oem):
            return oem[:-3]  # Remove "-XX" (3 chars)

    # Audi: Remove -X, -XX, -XXX suffixes
    elif brand == 'AUDI':
        for suffix in ['YEG', 'YEA', 'WK4']:
            if oem.endswith(suffix):
                return oem[:-len(suffix)]
        # Generic suffix removal
        match = re.match(r'^(.+)-[A-Z0-9]{1,3}$', oem)
        if match:
            return match.group(1)

    # Opel: Remove trailing single letter
    elif brand == 'OPEL':
        if len(oem) >= 7 and oem[-1].isalpha() and not oem[-2].isalpha():
            return oem[:-1]
        # Alternative: remove last 2 chars
        if len(oem) >= 7:
            return oem[:-2]

    # Mini: Remove -XX suffix
    elif brand == 'MINI':
        for suffix in ['7EL', 'ZKQ', 'ZEA', 'ZEB', 'ZBI', 'ZBU', 'ZBX']:
            if oem.endswith(suffix):
                return oem[:-len(suffix)]

    # Peugeot/Citroen/DS
    elif brand in ('PEUGEOT', 'CITROEN', 'DS'):
        if len(oem) >= 8:
            return oem[:-2]

    # KIA/Hyundai
    elif brand in ('KIA', 'HYUNDAI'):
        if len(oem) >= 8:
            return oem[:-3]

    # Mazda
    elif brand == 'MAZDA':
        if len(oem) >= 5:
            return oem[:-1]

    # Cupra
    elif brand == 'CUPRA':
        match = re.match(r'^(.+?)(P[0-9X][0-9A-Z]|PF[0-9]).*$', oem)
        if match and len(match.group(1)) >= 5:
            return match.group(1)

    # MG
    elif brand == 'MG':
        match = re.match(r'^(.+?)(BJAY|WSB|JAY|JAB|LMD|LJAY|SSA|YGM|RSJ)$', oem)
        if match and len(match.group(1)) >= 8:
            return match.group(1)

    return None
