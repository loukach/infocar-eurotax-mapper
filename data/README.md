# Data Directory (Legacy)

> **Note:** As of v3.1.0, this directory is no longer required. The application now fetches Eurotax data directly from MongoDB.

## Current Behavior (v3.1.0+)

Data is loaded from MongoDB:
- **Database:** `x_catalogue`
- **Collection:** `trims`
- **Query:** `{country: "it", _source: "eurotax"}`
- **Refresh:** Automatic, every hour

## Legacy Behavior (v3.0.x)

Previous versions loaded data from a JSON file:

- `trims_eurotax.json` - Eurotax vehicle trims data (~86 MB)

### Getting Legacy Data (if needed)

Copy from v2 data directory:

**Windows:**
```batch
copy ..\desktop-app-v2\data\trims_eurotax.json data\
```

**Mac/Linux:**
```bash
cp ../desktop-app-v2/data/trims_eurotax.json data/
```

## Reverting to JSON File (Not Recommended)

To revert to JSON file loading:

1. Modify `main.py` to import from JSON instead of `mongodb_client`
2. Restore the original `load_eurotax_data()` function
3. Copy `trims_eurotax.json` to this directory

**Note:** JSON file data becomes stale and requires manual updates. MongoDB provides live, auto-refreshing data.
