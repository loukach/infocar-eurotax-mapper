# Experiments

Validation and comparison scripts for the matching algorithm.

## Available Scripts

### compare_strategies.py

Compares OEM-based matching (v3) vs Make+Model-only matching.

**Usage:**
```bash
cd desktop-app-v4
python experiments/compare_strategies.py --samples 200
```

**Options:**
- `--samples N` - Number of random samples (default: 200)
- `--output-dir DIR` - Custom output directory

**Output:** Results saved to `analysis/YYYY-MM-DD_strategy_comparison/`

**Last run:** 2026-02-06 - OEM-based matching validated as superior (86% vs 6% accuracy in divergences)

## Adding New Experiments

1. Create script in this folder
2. Import modules from parent: `sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))`
3. Load `.env` from parent: `Path(__file__).parent.parent / '.env'`
4. Save results to `../analysis/YYYY-MM-DD_description/`
