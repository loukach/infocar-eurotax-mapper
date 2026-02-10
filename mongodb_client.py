# -*- coding: utf-8 -*-
"""
MongoDB client for fetching Eurotax data from x_catalogue database.

Configuration:
    Create a .env file in the desktop-app-v4 directory with:
    MONGO_URI=mongodb://user:pass@mongodb-0.stockspark.app:443/?authSource=x_catalogue&...
"""
import os
from pathlib import Path
from typing import List, Dict, Optional
from pymongo import MongoClient
from dotenv import load_dotenv

# Load .env file from the same directory as this script
env_path = Path(__file__).parent / '.env'
load_dotenv(env_path)

# Get MongoDB URI from environment
MONGO_URI = os.environ.get('MONGO_URI')

if not MONGO_URI:
    print("WARNING: MONGO_URI not set. Create a .env file with MongoDB credentials.")

_client = None


def get_mongo_client() -> MongoClient:
    """Get singleton MongoDB client."""
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI)
    return _client


def fetch_eurotax_trims(country: str = "it") -> List[Dict]:
    """
    Fetch deduplicated Eurotax trims from MongoDB for the specified country.

    Uses an aggregation pipeline to:
    1. Filter by country + eurotax source
    2. Project only fields used by the matcher
    3. Compute a completeness score per record
    4. Group by providerCode, keeping the most complete record

    This reduces ~493K raw records to ~80K unique natcodes server-side,
    avoiding cursor timeouts and eliminating client-side deduplication.

    Args:
        country: Country code (default: "it" for Italy)

    Returns:
        List of deduplicated trim documents
    """
    client = get_mongo_client()
    db = client['x_catalogue']
    collection = db['trims']

    # Fields used by the matcher (for projection and completeness scoring)
    fields = [
        'name', 'normalizedMake', 'normalizedModel',
        'providerCode', 'manufacturerCode',
        'powerHp', 'powerKw', 'cc',
        'price', 'prices',
        'fuelType', 'bodyType', 'doors', 'seats',
        'gears', 'gearType', 'tractionType', 'mass',
        'sellableWindow',
    ]

    # Build projection stage
    project_stage = {f: 1 for f in fields}
    project_stage['_id'] = 0
    # Completeness score: count of non-null important fields (same logic as main.py)
    important_fields = [
        'name', 'manufacturerCode', 'powerHp', 'powerKw', 'cc',
        'price', 'fuelType', 'bodyType', 'doors', 'gears',
        'gearType', 'tractionType', 'seats', 'mass',
    ]
    project_stage['_completeness'] = {
        '$sum': [
            {'$cond': [{'$ifNull': [f'${f}', False]}, 1, 0]}
            for f in important_fields
        ]
    }

    pipeline = [
        {'$match': {
            'country': country,
            '_source': 'eurotax',
            'providerCode': {'$exists': True, '$ne': None},
        }},
        {'$project': project_stage},
        {'$sort': {'_completeness': -1}},
        {'$group': {
            '_id': '$providerCode',
            'doc': {'$first': '$$ROOT'},
        }},
        {'$replaceRoot': {'newRoot': '$doc'}},
    ]

    cursor = collection.aggregate(pipeline, allowDiskUse=True)
    results = list(cursor)

    # Remove the temporary _completeness field
    for rec in results:
        rec.pop('_completeness', None)

    return results


def get_existing_mapping(source_code: str, source_provider: str = "infocar", dest_provider: str = "eurotax") -> Optional[Dict]:
    """
    Check if a mapping already exists for the given source code.

    Args:
        source_code: The provider code to check
        source_provider: Source provider (default: "infocar")
        dest_provider: Destination provider (default: "eurotax")

    Returns:
        Mapping document if found, None otherwise
    """
    if not MONGO_URI:
        return None

    try:
        client = get_mongo_client()
        db = client['x_catalogue']
        collection = db['mappings']

        mapping = collection.find_one({
            'sourceCode': source_code,
            'sourceProvider': source_provider,
            'destProvider': dest_provider
        })

        return mapping
    except Exception as e:
        print(f"Error checking existing mapping: {e}")
        return None


def test_connection() -> Dict:
    """
    Test MongoDB connection and return stats.

    Returns:
        Dict with connection status and record count
    """
    try:
        client = get_mongo_client()
        db = client['x_catalogue']
        collection = db['trims']

        # Count records (field is '_source' not 'provider')
        count = collection.count_documents({'country': 'it', '_source': 'eurotax'})

        return {
            'connected': True,
            'database': 'x_catalogue',
            'collection': 'trims',
            'eurotax_it_count': count
        }
    except Exception as e:
        return {
            'connected': False,
            'error': str(e)
        }


if __name__ == "__main__":
    # Test connection when run directly
    print("Testing MongoDB connection...")
    result = test_connection()
    print(f"Result: {result}")
