# -*- coding: utf-8 -*-
"""
Vehicle Class Identification - v4

Identifies whether a vehicle is a CAR or LCV (Light Commercial Vehicle).
"""

from typing import Optional


# =============================================================================
# VEHICLE CLASS CONSTANTS
# =============================================================================

class VehicleClass:
    CAR = 'CAR'
    LCV = 'LCV'


# LCV-only makes (these brands only produce commercial vehicles)
LCV_MAKES = frozenset([
    'IVECO',
    'MAN',
    'ISUZU',
    'PIAGGIO VEICOLI COMMERCIALI',
])


# LCV model name patterns (case-insensitive substring match)
LCV_MODELS = frozenset([
    'ducato',
    'daily',
    'sprinter',
    'transit',
    'transporter',
    'crafter',
    'vito',
    'citan',
    'boxer',
    'jumper',
    'expert',
    'jumpy',
    'berlingo van',
    'partner',
    'kangoo',
    'trafic',
    'master',
    'movano',
    'vivaro',
    'combo cargo',
    'proace',
    'hiace',
    'nv200',
    'nv300',
    'nv400',
    'e-nv200',
    'tourneo',
])


# LCV body types
LCV_BODY_TYPES = frozenset([
    'VAN',
    'CHASSIS',
    'PICKUP',
    'PLATFORM',
    'BUS',
])


# =============================================================================
# IDENTIFICATION FUNCTION
# =============================================================================

def identify_vehicle_class(
    normalized_make: Optional[str],
    normalized_model: Optional[str],
    normalized_body_type: Optional[str]
) -> str:
    """
    Identify the vehicle class (CAR or LCV) using rules in order:

    1. LCV-only makes (IVECO, MAN, etc.)
    2. LCV model names (Ducato, Sprinter, etc.)
    3. LCV body types (VAN, CHASSIS, PICKUP, PLATFORM)
    4. Default: CAR

    Args:
        normalized_make: Normalized make name (uppercase)
        normalized_model: Normalized model name (any case)
        normalized_body_type: Normalized body type (uppercase)

    Returns:
        VehicleClass.CAR or VehicleClass.LCV
    """
    # Rule 1: LCV-only makes
    if normalized_make and normalized_make.upper() in LCV_MAKES:
        return VehicleClass.LCV

    # Rule 2: LCV model names (case-insensitive substring match)
    if normalized_model:
        model_lower = normalized_model.lower()
        for lcv_model in LCV_MODELS:
            if lcv_model in model_lower:
                return VehicleClass.LCV

    # Rule 3: LCV body types
    if normalized_body_type and normalized_body_type.upper() in LCV_BODY_TYPES:
        return VehicleClass.LCV

    # Rule 4: Default to CAR
    return VehicleClass.CAR
