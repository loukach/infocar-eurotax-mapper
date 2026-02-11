# UI Architecture (v4.2)

## Principle: No Business Logic in the UI

The frontend (`static/index.html`) is a **thin display layer**. All matching logic, normalization, scoring, and confidence classification happens server-side. The UI only:

- Sends search requests to the API
- Renders the API response as a comparison table
- Derives visual indicators (green/yellow/red) from the API's `breakdown` scores
- Manages URL hash navigation for browser back/forward

**Why:** Duplicating business logic in the UI (normalization maps, tolerance thresholds, comparison rules) causes inconsistencies. The backend scoring may give points where the UI shows a red X, misleading the user. By using the API's actual breakdown, what the user sees matches what the algorithm computed.

## UI Layout (v4.2)

- **Header bar**: dark slim bar with status dot, record count, version
- **Search section**: input + Search button + profile dropdown + divider + vehicle identity (make + version name, shown after search) + status text
- **Comparison table**: Field labels | Source | 3px gap | Top 3 candidates (expandable to 10) | optional far-right existing column
- **URL hash navigation**: `/#code` updates on search, supports browser back/forward, auto-searches on page load if hash present

---

## API Response Contract

The UI depends on the following structure from `/api/search`:

### Top-level fields

| Field | Type | Usage |
|-------|------|-------|
| `found` | bool | Whether the Infocar vehicle was found |
| `infocar_provider_code` | string | Displayed in header |
| `infocar_code` | string | OEM code |
| `brand` | string | Make name |
| `infocar_name` | string | Vehicle version name |
| `infocar_specs` | object | Source vehicle specs (see Specs below) |
| `infocar_trims` | string[] | Extracted trim tokens for source |
| `vehicle_class` | string | "CAR" or "LCV" |
| `candidate_count` | int | Total candidates found |
| `candidates` | object[] | Top 10 scored candidates |
| `stage2_decision` | string | Confidence label: PERFECT/LIKELY/POSSIBLE/UNLIKELY |
| `stage2_confidence` | float | Score as fraction of max (0-1) |
| `stage2_recommended_natcode` | string | Top candidate's natcode |
| `existing_mapping` | object/null | Most recent mapping from X-Catalog API (real-time via `/v1/private/mapping/infocar/{code}`) |
| `weight_profile` | string | Active profile name |
| `max_score` | int | Max possible score for this profile |

### Specs object (both `infocar_specs` and `candidate.specs`)

| Field | Type | Notes |
|-------|------|-------|
| `name` | string | Vehicle version name |
| `make` | string | Normalized make |
| `model` | string | Normalized model (roman numerals/years stripped) |
| `cc` | int | Engine displacement |
| `hp` | int | Horsepower |
| `kw` | int | Kilowatts |
| `price` | float | Price |
| `fuel` | string | Raw fuel type (Italian) |
| `body` | string | Raw body type (Italian) |
| `doors` | int | Door count |
| `seats` | int | Seat count |
| `gears` | int | Gear count |
| `gear_type` | string | Raw transmission type (Italian) |
| `traction` | string | Raw traction type (Italian) |
| `mass` | int | Weight in kg |
| `sellable_begin` | int | Sellable window start year |
| `sellable_end` | int | Sellable window end year |
| `fuel_norm` | string | Normalized: DIESEL, PETROL, HYBRID_PETROL, etc. |
| `body_norm` | string | Normalized: SEDAN, SUV, WAGON, VAN, etc. |
| `gear_type_norm` | string | Normalized: AUTOMATIC, MANUAL, CVT |
| `traction_norm` | string | Normalized: FWD, RWD, AWD |

The `*_norm` fields are computed server-side by the same functions used in scoring, ensuring the displayed normalization always matches what the scorer used.

### Candidate object

| Field | Type | Notes |
|-------|------|-------|
| `natcode` | string | Eurotax provider code |
| `eurotax_code` | string | OEM/manufacturer code |
| `eurotax_name` | string | Eurotax vehicle name |
| `specs` | object | Same structure as Specs above |
| `score` | int | Total score |
| `breakdown` | object | Points per scoring factor (see below) |
| `oem_match_type` | string | "EXACT", "CLEANED", or "NONE" |
| `trim_matched` | string[] | Trim tokens found in both source and target |
| `trim_source_only` | string[] | Trim tokens only in source |
| `trim_target_only` | string[] | Trim tokens only in target |

### Breakdown object

Contains actual points awarded per scoring factor. Keys match the weight profile keys:

```
{ price, hp, trim, cc, fuel, sellable, body, oem, model, transmission, traction, doors, name, seats, gears, kw, mass }
```

Also contains metadata prefixed with `_`:
- `_oem_match_type`: "EXACT", "CLEANED", or "NONE"
- `_trim_matched`, `_trim_source_only`, `_trim_target_only`: trim token sets

---

## Match Indicator Logic

The UI determines green/yellow/red indicators for each field using a single function:

```javascript
function getMatchFromBreakdown(breakdownKey, breakdown, weights) {
    points = breakdown[breakdownKey]
    max = weights[breakdownKey]

    if points >= max  -> green checkmark  (full match)
    if points > 0     -> yellow tilde     (partial match)
    if points == 0    -> red X            (no match)
}
```

This means:
- **Price** within 10% shows green, 10-20% shows yellow, 20-35% shows yellow (partial points), >35% shows red
- **HP** exact shows green, within 5 shows yellow (80% points), within 10 shows yellow (50% points), >10 shows red
- **Fuel** hybrid cross-match (e.g., HYBRID_PETROL vs HYBRID_DIESEL) shows yellow (70% points)
- **Transmission** EV leniency shows yellow (50% points)
- **Doors** off-by-one shows yellow (60% points)

All of these thresholds are defined in `matcher_v4.py` scoring functions, not in the UI.

### Field-to-breakdown mapping

| UI Field | `breakdownKey` | Scored? |
|----------|---------------|---------|
| OEM Code | `oem` | Yes (merged: code + OEM match badge + border indicator) |
| Make | - | No (always matches, Stage 1 filter) |
| Version Name | `name` | Yes |
| Trim Level | `trim` | Yes (special row) |
| Model | `model` | Yes (exact normalized match only) |
| CC | `cc` | Yes |
| HP | `hp` | Yes |
| Price | `price` | Yes |
| Fuel Type | `fuel` | Yes |
| Body Type | `body` | Yes |
| Doors | `doors` | Yes |
| Seats | `seats` | Yes |
| Gears | `gears` | Yes |
| Transmission | `transmission` | Yes |
| Traction | `traction` | Yes |
| KW | `kw` | Yes |
| Mass | `mass` | Yes |
| Sellable Window | `sellable` | Yes |

Fields without a `breakdownKey` (OEM Code display, Make) show values only, with no match indicator.

---

## Normalized Value Display

For fields with a `normKey` property, the UI shows the server-provided normalized value below the raw value:

```
Gasolio
-> DIESEL
```

| UI Field | `normKey` | Example |
|----------|----------|---------|
| Fuel Type | `fuel_norm` | "Gasolio" -> "DIESEL" |
| Body Type | `body_norm` | "Berlina 2 volumi" -> "SEDAN" |
| Transmission | `gear_type_norm` | "Automatico sequenziale" -> "AUTOMATIC" |
| Traction | `traction_norm` | "Integrale permanente" -> "AWD" |

---

## Score Display

- **Score badge** per candidate: `Score: {score}/{maxScore}` with color class from `getScoreClass(score, maxScore)`
  - Green (score-high): >= 60% of max
  - Orange (score-medium): >= 40% of max
  - Red (score-low): < 40% of max
- **Confidence label**: from `stage2_decision` in API response (PERFECT/LIKELY/POSSIBLE/UNLIKELY)
- **Profile info**: shown in result subtitle as `Profile: {name} ({maxScore}pt)`
- **Header subtitle**: updated dynamically when profile changes

---

## Weight Profiles

Profiles are loaded from `/api/profiles` on page load. The dropdown shows `name (maxScore pt)` for each. Changing the profile:

1. Updates `currentMaxScore`
2. Updates the header subtitle
3. Re-runs the current search with the new profile

The weights from the selected profile are used by `getMatchFromBreakdown()` to determine max points per field.

---

## Column Layout

The comparison table uses **full viewport width** with `table-layout: fixed` to ensure all columns (including the far-right existing mapping) are always visible without horizontal scrolling.

### Fixed Layout (default view, top 3 candidates)

| Column | Width | Notes |
|--------|-------|-------|
| Field labels | 130px | Sticky left, always visible |
| Source | 260px | Sticky left, text wraps |
| Gap | 3px | Transparent separator between source and candidates |
| Candidates (up to 3) | auto | Equal share of remaining viewport space |
| See more button | 70px | Only shown when > 3 candidates exist |
| Existing far-right | auto | Same width as candidates (only when not in top 3) |

With `table-layout: fixed`, the Field, Source, Gap, and See-more columns get explicit widths. All remaining viewport space is divided equally among candidate columns (and the far-right existing column if present). No container `max-width` — the table spans the full viewport.

### Expanded Layout (show all candidates, capped at 10)

When the user clicks "+N more", the table shows up to 10 candidates. If more than 5 are visible, the table switches to `table-layout: auto` with `min-width: max-content` (CSS class `expanded`). This allows horizontal scrolling, with `min-width: 140px` / `max-width: 200px` restored on each candidate.

### Existing Mapping Display

The existing mapping (if any) is shown differently depending on whether it appears among the visible candidates:

**Mode A — In visible candidates:** The candidate column whose natcode matches the existing mapping gets a light purple background highlight (`col-cand-existing` class). The purple background coexists with the green/yellow/red match indicator borders. A solid purple "EXISTING" badge appears in the header natcode row. The "Map it" button is hidden for this candidate.

**Mode B — Not in visible candidates:** The existing mapping appears as a dedicated far-right column after the "see more" expand button. This column has no match indicator borders (since it's shown for reference only, not as a scored top candidate). Header shows "EXISTING", a dashed "NOT IN TOP 3" badge, and score/strategy inline.

**Color scheme:** Purple (`--purple-50` to `--purple-500`) is used exclusively for existing mapping indicators. This avoids interference with the green/yellow/red scoring borders.

---

## Special Rows

### OEM Code row
- Source column: Infocar OEM code
- Candidate columns: Eurotax OEM code + OEM match badge (Exact OEM / Cleaned OEM / No OEM match) + match indicator border from `oem` breakdown key

### Trim Level row
- Source column: extracted trim tokens (uppercase, comma-separated)
- Candidate columns: matched + target-only tokens, with match indicator from breakdown
