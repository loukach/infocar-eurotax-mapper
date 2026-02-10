# Matching Algorithm v4

## Overview

This document specifies the vehicle matching algorithm used in desktop-app-v4. The algorithm takes a 12-digit Infocar provider code as input and finds the best-matching Eurotax vehicle(s) from a catalog of ~79,000 deduplicated Italian-market records.

The process runs through five phases:

```
Phase 0: Source Resolution     Get the source vehicle from X-Catalog API
Phase 1: Normalization         Standardize raw values for both source and targets
Phase 2: Candidate Selection   Find potential matches via make+model containment
Phase 3: Scoring               Rate each candidate across 17 factors (157 pts max)
Phase 4: Classification        Assign confidence level based on top score
```

---

## Key Change from v3

**Problem:** In v3, OEM codes drove candidate selection via early stopping. If an exact OEM match was found, the algorithm stopped and never considered other candidates. This caused a bug where the correct match was invisible if a wrong variant (e.g., 3-door) shared the OEM code with the source, hiding the correct variant (5-door).

**Example:** Opel Corsa code `201812128598` - the 3p variant shared the source OEM code, so v3 selected only the 3p (exact OEM match) and never considered the 5p, which was the correct match.

**Fix:** OEM is demoted from a candidate selection gate to a regular scoring field. All candidates come from make+model matching. OEM adds points per-candidate: exact +10, cleaned +5, none 0. Both variants now appear as candidates, and the 5p wins on overall merit (doors + sellable window outweigh the OEM difference).

---

## Phase 0: Source Resolution

**Input:** 12-digit Infocar provider code (e.g., `201812128598`)

### Step 0a: X-Catalog API Lookup

Query X-Catalog API with the provider code to retrieve the source Infocar vehicle record. The response includes: name, make, model, OEM code, price, HP, CC, fuel type, body type, doors, transmission, traction, sellable window, and any existing Eurotax mappings.

### Step 0b: Inverted Code Fallback

If the code returns no results, try the inverted version (swap first 6 and last 6 digits):
- Original: `201807119019`
- Inverted: `119019201807`

### Step 0c: Existing Mapping Detection

Check both the API response and MongoDB `x_catalogue.mappings` collection for any existing Infocar-to-Eurotax mapping for this code. If found, it will be displayed in the UI but does not affect the matching algorithm.

---

## Phase 1: Normalization

### Why Normalization Is Needed

Infocar and Eurotax encode the same real-world values using different vocabulary and casing. Without normalization, direct string comparison fails:

| Field | Infocar (source) | Eurotax (target) | Same vehicle attribute? |
|---|---|---|---|
| Fuel | `benzina senza piombo` | `Benzina` | Yes (petrol) |
| Body | `berlina 2 volumi` | `Berlina` | Yes (sedan) |
| Transmission | `meccanico` | `Manuale` | Yes (manual) |
| Traction | `anteriore` | `Anteriore` | Yes (front-wheel drive) |

X-Catalogue does not provide normalized versions of these fields. Both sources store raw Italian-language values with source-specific conventions.

**Note:** X-Catalogue provides `normalizedMake` and `normalizedModel` (lowercased), which we use directly. It also stores `_cleanManufacturerCode` and a `type` field (`car`/`lcv`), but these are not used by our algorithm (see 1f and 1g below for why).

### Normalization Functions

In the implementation, normalizations happen at the point of use (during indexing, candidate selection, or scoring), but the transformations are defined centrally in `normalizers.py` and `matcher_v4.py`.

### 1a. Model Name Normalization

**Function:** `normalize_model()` in `normalizers.py`

Transforms raw model names to improve containment matching:

1. **Lowercase** the input
2. **Remove year/generation suffixes** (e.g., `corsa iii 2024` -> `corsa`, `500 v 2020` -> `500`)
3. **DS model name collapse:** `"ds N"` -> `"dsN"` for single digits (e.g., `"ds 3"` -> `"ds3"`, `"ds 7 crossback"` -> `"ds7 crossback"`). Infocar uses a space (`ds 3`), Eurotax does not (`ds3`); without this, containment matching fails.
4. **Expand abbreviations** using a lookup table:
   - `rr` -> `range rover`, `rre` -> `range rover evoque`
   - `vw` -> `volkswagen`, `ar` -> `alfa romeo`
   - Mercedes/BMW short codes pass through unchanged (already canonical)

**When applied:** During candidate selection (Phase 2), on both source and target model names.

### 1b. Fuel Type Normalization

**Function:** `normalize_fuel()` in `normalizers.py`

Maps Italian and English raw fuel strings to 7 standard values:

| Normalized | Raw inputs (examples) |
|---|---|
| DIESEL | gasolio, diesel |
| PETROL | benzina, benzina senza piombo, petrol, gasoline |
| HYBRID_PETROL | ibrido benzina, hybrid benzina, elettrica/benzina, plug-in hybrid petrol |
| HYBRID_DIESEL | ibrido diesel, hybrid diesel, elettrica/gasolio, plug-in hybrid diesel |
| ELECTRIC | elettrica, elettrico, electric |
| LPG | gpl, lpg |
| CNG | metano, cng |

**Priority order:** Electric (pure) -> Hybrid detection (before base fuels) -> Electric combinations -> Base fuels. This prevents "elettrica/benzina" from matching as pure ELECTRIC.

**When applied:** During fuel scoring (Phase 3).

### 1c. Body Type Normalization

**Function:** `normalize_body()` in `normalizers.py`

Maps raw body strings to 12 standard values. Door count suffixes (e.g., "3 porte", "5 porte") are stripped before matching. Uses substring matching (not exact match) to handle compound Italian body type names.

Infocar and Eurotax use significantly different vocabulary for the same body types. The normalization must map both to consistent values. The mapping below was validated against 57,376 existing Infocar-to-Eurotax matched pairs from the `x_catalogue.mappings` collection.

#### Cross-Source Evidence

Top matched pairs showing how the same vehicle is described differently by each source:

| Infocar bodyType | Eurotax bodyType | Pairs | Both normalize to |
|---|---|---|---|
| `berlina 2 volumi` | `Berlina` | 12,486 | SEDAN |
| `fuoristrada/SUV 5 porte` | `SUV` | 9,285 | SUV |
| `station wagon` | `Familiare` | 6,630 | WAGON |
| `berlina 3 volumi` | `Berlina` | 5,615 | SEDAN |
| `multispazio` | `Monovolume` | 3,715 | MPV |
| `fuoristrada/SUV 5 porte` | `CrossOver` | 3,520 | SUV |
| `cabriolet` | `Cabriolet` | 1,633 | CONVERTIBLE |
| `furgone lamierato` | `Furgone` | 1,467 | VAN |
| `coupé tre volumi` | `Coupe` | 1,278 | COUPE |
| `furgone lamierato` | `Furgone tetto alto` | 1,180 | VAN |
| `autotelaio cabinato` | `Cabinato` | 778 | CHASSIS |
| `pulmino` | `Promiscuo` | 761 | VAN (see note) |
| `multispazio minifurgone` | `Furgone` | 685 | VAN |
| `fuoristrada/SUV coupé` | `SUV Coupe` | 541 | SUV |
| `coupé due volumi` | `Coupe` | 468 | COUPE |
| `autotelaio cabinato DC` | `Cabinato doppia cabina` | 361 | CHASSIS |
| `autotelaio cabinato con cassone` | `Carro` | 335 | PLATFORM |
| `furgone vetrato doppia cabina` | `Furgone doppia cabina` | 319 | VAN |
| `pick-up lungo lunga cabina` | `Pick-up` | 297 | PICKUP |
| `fuoristrada/SUV 5 porte` | `FST` | 285 | SUV |
| `multispazio combi` | `Promiscuo` | 214 | VAN |
| `berlina multispazio` | `Monovolume` | 164 | MPV |
| `autotelaio cabinato con cassone DC` | `Carro doppia cabina` | 129 | PLATFORM |
| `autotelaio cabinato allestito` | `Furgone` / `Furgone tetto alto` | 179 | VAN |
| `torpedo *` / `torpedo con hard-top *` | `FST` | 101 | SUV |
| `microfurgone` | `Furgone` | 32 | VAN |
| `microfurgone combi` | `Promiscuo` | 22 | VAN |
| `apribile con roll-bar integrato` | `Cabriolet` | 21 | CONVERTIBLE |
| `pianale cabinato` | `Cabinato` | 14 | CHASSIS |
| `multispazio combi` | `Bus` | 40 | BUS |

**Note on `pulmino`:** Infocar `pulmino` (minibus) maps to Eurotax `Promiscuo` 761 times. Open question: should `pulmino` normalize to VAN (matching Promiscuo) or BUS? Mapping it to VAN preserves body match for 761 known-correct pairs.

#### Normalization Table

| Normalized | Infocar raw values | Eurotax raw values |
|---|---|---|
| SEDAN | berlina 2 volumi, berlina 3 volumi, berlina 2 volumi e mezzo | Berlina |
| SUV | fuoristrada/SUV 5 porte, fuoristrada/SUV coupé, fuoristrada/SUV 2/3 porte, torpedo telonata *, torpedo con hard-top * | SUV, CrossOver, SUV Coupe, FST |
| WAGON | station wagon | Familiare |
| CONVERTIBLE | cabriolet, coupé-cabriolet, spider, apribile con roll-bar integrato, barchetta | Cabriolet, Spider |
| COUPE | coupé tre volumi, coupé due volumi | Coupe |
| MPV | multispazio, berlina multispazio | Monovolume, Multispazio |
| HATCHBACK | - | hatchback |
| VAN | furgone lamierato, furgone vetrato, furgone vetrato doppia cabina, multispazio minifurgone, multispazio combi, microfurgone, microfurgone combi, autotelaio cabinato allestito, scudato, pulmino, pulmino lamierato | Furgone, Furgone tetto alto, Furgone doppia cabina, Furgone vetrato, Promiscuo, Van |
| CHASSIS | autotelaio cabinato, autotelaio cabinato DC, pianale cabinato | Cabinato, Cabinato doppia cabina |
| PICKUP | pick-up lungo lunga cabina, pick-up lungo, pick-up corto, pick-up corto lunga cabina, pick-up con cassone, pick-up derivato da autovettura, microfurgone pick-up, pick-up telaio/chassis | Pick-up, Pick up |
| PLATFORM | autotelaio cabinato con cassone, autotelaio cabinato con cassone DC | Carro, Carro doppia cabina |
| BUS | autobus e scuolabus | Bus |

#### Substring Matching Rules

Matching uses substring containment (`keyword in body_lower`), checked in this priority order to handle compound names correctly. Door count suffixes ("3 porte", "5 porte") are stripped before matching.

1. **PICKUP**: `pick-up`, `pick up`, `pickup` (before VAN, since `microfurgone pick-up` contains both)
2. **BUS**: `autobus`, `scuolabus`, exact `bus`
3. **PLATFORM** (partial): `cassone`, `carro` (before CHASSIS, to catch `cabinato con cassone` → PLATFORM)
4. **VAN**: `furgone`, `furgonato`, exact `van`, `scudato`, `pulmino`, `promiscuo`, `combi`, `allestito` (before CHASSIS, since `cabinato allestito` → VAN per matched pairs)
5. **CHASSIS**: `cabinato`, `telaio`, exact `chassis`, exact `cab` (after VAN and PLATFORM partial)
6. **PLATFORM** (remainder): `pianale`, `platform`
7. **SUV**: `suv`, `crossover`, `fuoristrada`, `torpedo`, exact `fst` (after LCV types)
8. **WAGON**: `wagon`, `familiare`, `estate`, `touring`
9. **CONVERTIBLE**: `cabrio`, `spider`, `roadster`, `convertible`, `apribile`, `barchetta` (before COUPE, since `coupé-cabriolet` → CONVERTIBLE)
10. **COUPE**: `coup` (handles both `coupe` and `coupé` encoding variants)
11. **MPV**: `monovolume`, `mpv`, `minivan`, `multispazio` (before SEDAN, since `berlina multispazio` → MPV)
12. **HATCHBACK**: `hatchback`
13. **SEDAN**: `berlina`, `sedan`, `3 volumi` (last, as fallback)

**Note:** `microfurgone` is not an explicit keyword - it's caught by the `furgone` substring match. Similarly, `fuoristrada/SUV` is caught by `fuoristrada`.

**Coverage:** 100% on both Eurotax (79,234 records) and Infocar (181,230 of 181,232 records; 2 unmapped: "dune buggy").

**Implementation note:** Ordering matters. PLATFORM is split into two checks: `cassone`/`carro` must come before CHASSIS (for `cabinato con cassone` → PLATFORM), while `pianale`/`platform` come after. `multispazio` must be checked before `berlina` (for `berlina multispazio` → MPV). `pick-up` must be checked before `furgone` (for `microfurgone pick-up` → PICKUP).

**When applied:** During index building (for vehicle class identification) and during body scoring (Phase 3).

### 1d. Transmission Normalization

**Function:** `normalize_transmission()` in `normalizers.py`

| Normalized | Raw inputs (examples) |
|---|---|
| AUTOMATIC | automatico, automatic, auto, dsg, dct, robotizzato, sequenziale |
| MANUAL | manuale, manual, meccanico |
| CVT | cvt |

**When applied:** During transmission scoring (Phase 3).

### 1e. Traction Normalization

**Function:** `normalize_traction()` in `normalizers.py`

| Normalized | Raw inputs (examples) |
|---|---|
| FWD | anteriore, front, fwd |
| RWD | posteriore, rear, rwd |
| AWD | integrale, all-wheel, awd, 4x4, 4wd |

**When applied:** During traction scoring (Phase 3).

### 1f. Vehicle Class Identification

**Function:** `identify_vehicle_class()` in `vehicle_class.py`

X-Catalogue stores a `type` field (`car`/`lcv`) on records, but the Infocar source always sets `type` to `"car"` even for LCV vehicles. This field is unreliable for Infocar data, so we derive vehicle class ourselves using rule-based classification:

| Rule | Check | Result |
|---|---|---|
| 1 | Make is in LCV-only list (IVECO, MAN, ISUZU, PIAGGIO VEICOLI COMMERCIALI) | LCV |
| 2 | Model name contains an LCV keyword (ducato, sprinter, transit, tourneo, ...) | LCV |
| 3 | Normalized body type is VAN, CHASSIS, PICKUP, PLATFORM, or BUS | LCV |
| 4 | Default | CAR |

**Note on Tourneo:** Ford Tourneo models (Tourneo Custom, Tourneo Connect, Tourneo Courier) are the passenger versions of Transit vans. Infocar classifies them with body type "multispazio" (MPV -> CAR), but Eurotax classifies them as LCV. Adding `tourneo` to the LCV model keyword list ensures they match correctly against Eurotax candidates.

**When applied:** During index building (every Eurotax record is classified) and for the source vehicle before candidate selection.

### 1g. OEM Code Cleaning

**Function:** `clean_oem_code()` in `normalizers.py`

X-Catalogue stores a `_cleanManufacturerCode` field on records, but this is not used by our algorithm at this time. We apply our own brand-specific transformations to strip variant-encoding suffixes/prefixes from OEM codes. This enables a "cleaned" tier of OEM matching when exact codes differ due to encoding variations.

| Brand | Transformation | Example |
|---|---|---|
| Renault | Remove 2-3 char prefix | `XJK12345` -> `12345` |
| Dacia | Remove 2-3 char prefix | Similar to Renault |
| Volkswagen | Remove `-XXX` suffix | `ABC123-WI1` -> `ABC123` |
| Skoda | Remove known suffixes (RAA, WI1) | `ABC123WI1` -> `ABC123` |
| Mercedes | Extract to DL marker, or remove `-XX` suffix | `123DL456-AB` -> `123DL4` |
| Audi | Remove known suffixes (YEG, YEA, WK4) or `-X{1,3}` | `ABC-YEG` -> `ABC` |
| Opel | Remove trailing 1-2 chars (if numeric precedes alpha) | `1234567A` -> `123456` |
| Mini | Remove known 3-char suffixes (7EL, ZKQ, ...) | `ABC1237EL` -> `ABC123` |
| Peugeot/Citroen/DS | Remove last 2 chars | `12345678` -> `123456` |
| KIA/Hyundai | Remove last 3 chars | `123456789` -> `123456` |
| Mazda | Remove last 1 char | `12345` -> `1234` |
| Cupra | Remove suffix starting at P-pattern | `ABCDEFP0X...` -> `ABCDEF` |
| MG | Remove known terminal suffixes (BJAY, WSB, ...) | `ABCDEFGHBJAY` -> `ABCDEFGH` |

**When applied:** During OEM scoring (Phase 3), on both source and target OEM codes.

### 1h. Trim Token Extraction

**Function:** `extract_trim_tokens()` in `matcher_v4.py`

Scans vehicle name for 255 known trim/equipment keywords using word-boundary regex matching. Returns a set of matched tokens.

**Keyword categories (representative examples, see `TRIM_TOKENS` in `matcher_v4.py` for full list):**
- **Performance:** sport, sportline, s-line, amg, amg line, m sport, r-line, gt line, gs line, n line, fr, cupra, st, rs, vrs, gti, gtd, gte, gt, gts, r-design, r-dynamic, polestar, veloce, competition, performance, sprint, racing, s-design, st-line
- **Luxury/Premium:** executive, premium, luxury, exclusive, ultimate, inscription, designo, maybach, lusso, tributo, prestige, platinum, vip, deluxe, luxe
- **Equipment levels:** business, businessline, style, elegance, ambition, ambiente, comfort, life, edition, special, limited, advanced, tech, active, plus, pro, base, standard, lounge, pop, cult, icon, iconic, trend, essential, select, selection, core, pure, prime, entry, move, access, modern, individual, signature, collection, premiere, bright, fresh
- **Renault/Dacia:** dynamique, seduction, initiale, intens, intense, zen, expression, laureate, equilibre, ambiance, energy, esprit, hypnotic, classique, authentique, invite, techroad, stepway, wave, evolve
- **Peugeot/Citroen/DS:** shine, allure, feline, feel, live, uptown, sense, chic, hype, mylife, allstreet, crossway, bastille, rivoli, opera, etoile, sesame, trocadero, extravagance, irresistible, attitude
- **Fiat/Alfa Romeo/Maserati:** easy, distinctive, eletta, progression, dolcevita, mirror, ecochic, elective, eccelsa, duel, goldplay, passion, glam, trekking, competizione, quadrifoglio, trofeo, modena
- **VW/Seat/Skoda:** comfortline, highline, trendline, xcellence, xperience, admired, monte carlo, scout, scoutline, connectline, emotion
- **BMW/Mercedes:** xline, x-line, advantage, sport line, luxury line, avantgarde, progressive, black edition, dark, night edition, atmosphere
- **Ford:** titanium, vignale, zetec, ghia, wildtrak, connected
- **Nissan:** acenta, tekna, visia, n-connecta, n-design, n-joy
- **Jeep:** longitude, altitude, overland, trailhawk, rebel, summit, sahara, rubicon
- **Jaguar/Land Rover:** hse, se, dynamic, momentum, autobiography, portfolio, vogue
- **Opel:** cosmo, attraction, enjoy, youngster
- **Honda/Mazda:** instyle, homura, takumi
- **Hyundai/Kia:** essentia, calligraphy, exceed
- **Suzuki:** attiva, excite, futura
- **Other/cross-brand:** classic, favoured, blackline, startline, ocean, outdoor, trail, trophy, anniversary, innovation, advance, connect, first edition, launch, techno, evolution, ultra, extreme, authentic, lifestyle, pulse, junior, club
- **Variants/drivetrain:** urban, city, cross, adventure, offroad, allroad, quattro, 4matic, xdrive, awd, 4x4, 4wd, traction

**Coverage (Italian market, 255 tokens):**

| Source | Total records | With trim | No trim | % no trim |
|--------|--------------|-----------|---------|-----------|
| Eurotax | 492,727 | 389,580 | 103,147 | 20.9% |
| Infocar | 181,232 | 106,948 | 74,284 | 41.0% |

- Eurotax unmatched: mostly commercial vehicles (Jumper, Boxer, etc.) and bare version names with only engine specs (e.g., "Corsa 1.2 s&s 75cv")
- Infocar unmatched: higher rate due to shorter/older-style names with Italian abbreviations (SX, GL, XT) or just engine displacement (e.g., "1.6", "2.0 HDi 5p.")
- A significant portion will always be 0 because the vehicle name contains no marketing trim keyword at all

**Edge cases:**
- If both source and target have no trim tokens: score is 0 (no penalty, no reward)
- If one side has tokens and the other does not: score is 0

**When applied:** During trim scoring (Phase 3), on both source and target vehicle names.

---

## Phase 2: Candidate Selection

**Input:** Source vehicle's normalized make, model, and vehicle class.

### Selection Logic

Find all Eurotax records where all three conditions are true:

1. **Make match:** Target normalized make == Source normalized make (exact, case-insensitive)
2. **Model containment** (any of these six checks):
   - Normalized source model is a substring of normalized target model
   - Normalized target model is a substring of normalized source model
   - Raw source model is a substring of raw target model
   - Raw target model is a substring of raw source model
   - Spaceless source model is a substring of spaceless target model
   - Spaceless target model is a substring of spaceless source model
3. **Vehicle class match:** Target vehicle class == Source vehicle class (CAR or LCV)

The 6-way model containment check covers three layers:
- **Normalized + raw (both directions):** Handles cases where normalization changes the matching behavior. For example, if normalization expands `rr` to `range rover`, the raw check still catches direct substring matches.
- **Spaceless (both directions):** Handles inconsistent spacing in Eurotax `normalizedModel` values. For example, Eurotax stores both `"500 x"` (227 records) and `"500x"` (28 records) for the same FIAT 500X model. Without spaceless containment, `"500x" in "500 x"` fails because the space breaks the substring match. The spaceless check compares `"500x" in "500x"` which succeeds. Affected models in the Italian dataset: FIAT 500X, FIAT Tipo 5 Porte, DR Motor DR1/DR2/DR3/DR5, KIA ProCeed, Volvo XC60.

**No OEM filtering.** All make+model+class matches become candidates regardless of OEM code. This is the key v4 change.

---

## Phase 3: Scoring

Each candidate is scored independently across 17 factors. Maximum total: **157 points**.

### Factor Summary

```
price:        25 pts    Percentage tolerance
hp:           20 pts    Absolute tolerance
trim:         15 pts    Derived from name, keyword set intersection
cc:           15 pts    Absolute tolerance
fuel:         15 pts    Normalized match (with hybrid partial match)
sellable:     10 pts    Window overlap
body:         10 pts    Normalized match
oem:          10 pts    Exact / cleaned / none
model:         5 pts    Exact normalized match
transmission:  5 pts    Normalized match (with EV leniency)
traction:      5 pts    Normalized match
doors:         5 pts    Exact or off-by-one
name:          5 pts    Token overlap ratio
seats:         3 pts    Exact or off-by-one
gears:         3 pts    Exact or off-by-one
kw:            3 pts    Absolute tolerance
mass:          3 pts    Percentage tolerance
                -----
TOTAL:        157 pts
```

### Detailed Scoring Rules

#### Price (25 pts) - Percentage tolerance

| Condition | Points |
|---|---|
| Difference <= 10% | 25 (full) |
| Difference <= 20% | 15 (60%) |
| Difference <= 35% | 7 (30%) |
| Difference > 35% or missing | 0 |

Percentage difference = `abs(source - target) / max(source, target) * 100`

Both values must be > 0 to score.

#### HP (20 pts) - Absolute tolerance

| Condition | Points |
|---|---|
| Exact match | 20 (full) |
| Difference <= 5 HP | 16 (80%) |
| Difference <= 10 HP | 10 (50%) |
| Difference > 10 HP or missing | 0 |

#### Trim (15 pts) - Derived keyword matching

1. Extract trim tokens from source vehicle name (Phase 1h)
2. Extract trim tokens from target vehicle name (Phase 1h)
3. Find intersection of the two sets
4. Score = `15 * len(intersection) / max(len(source_set), len(target_set))`

**Edge cases:**
- Both sides empty: 0 pts (no penalty)
- One side empty: 0 pts
- Perfect overlap: 15 pts
- Partial overlap: proportional (e.g., 2 of 3 tokens match = 10 pts)

#### CC (15 pts) - Absolute tolerance

| Condition | Points |
|---|---|
| Exact match | 15 (full) |
| Difference <= 50 cc | 12 (80%) |
| Difference <= 100 cc | 7 (50%) |
| Difference > 100 cc or missing | 0 |

#### Fuel (15 pts) - Normalized match with hybrid partial

| Condition | Points |
|---|---|
| Exact normalized match | 15 (full) |
| Both are HYBRID variants (HYBRID_PETROL vs HYBRID_DIESEL) | 10 (70%) |
| Different fuel types or missing | 0 |

The hybrid partial match (70%) handles cases where Infocar and Eurotax categorize the same plug-in hybrid differently (e.g., one says HYBRID_PETROL, the other HYBRID_DIESEL).

#### Sellable Window (10 pts) - Temporal overlap

| Condition | Points |
|---|---|
| Exact match (same begin AND same end) | 10 (full) |
| Overlap (windows intersect but differ) | 5 (50%) |
| No overlap or missing data | 0 |

Missing end date is treated as open-ended (9999). Both begin dates must be present to score.

Overlap check: `NOT (source_begin > target_end OR target_begin > source_end)`

#### Body (10 pts) - Normalized match

| Condition | Points |
|---|---|
| Exact normalized match | 10 (full) |
| Different body types or missing | 0 |

#### OEM (10 pts) - Per-candidate match

| Condition | Points | Match Type |
|---|---|---|
| Exact match (case-insensitive) | 10 (full) | EXACT |
| Cleaned codes match (brand-specific cleaning, Phase 1g) | 5 (50%) | CLEANED |
| No match or missing | 0 | NONE |

Checked in order: exact first, then cleaned. Both source and target OEM codes must be present to score.

#### Transmission (5 pts) - Normalized match with EV leniency

| Condition | Points |
|---|---|
| Exact normalized match | 5 (full) |
| Mismatch but source fuel is ELECTRIC | 2 (50%) |
| Different transmission types or missing | 0 |

The EV leniency handles the fact that electric vehicles often have inconsistent transmission encoding between data sources (some report AUTOMATIC, others report nothing or CVT).

#### Traction (5 pts) - Normalized match

| Condition | Points |
|---|---|
| Exact normalized match | 5 (full) |
| Different traction types or missing | 0 |

#### Doors (5 pts) - Exact or off-by-one

| Condition | Points |
|---|---|
| Exact match | 5 (full) |
| Off by 1 | 3 (60%) |
| Off by 2+ or missing | 0 |

Off-by-one tolerance handles hatch counting differences (e.g., a hatchback may be listed as 3-door or 5-door depending on whether the hatch is counted).

#### Seats (3 pts) - Exact or off-by-one

| Condition | Points |
|---|---|
| Exact match | 3 (full) |
| Off by 1 | 1 (60%) |
| Off by 2+ or missing | 0 |

Seat count is a discrete, low-variance field. Off-by-one tolerance handles 5-vs-4 seat variants common across trim levels.

#### Gears (3 pts) - Exact or off-by-one

| Condition | Points |
|---|---|
| Exact match | 3 (full) |
| Off by 1 | 1 (60%) |
| Off by 2+ or missing | 0 |

Gear count is typically 5, 6, 7, or 8. Off-by-one tolerance handles minor catalog differences.

#### KW (3 pts) - Absolute tolerance

| Condition | Points |
|---|---|
| Exact match | 3 (full) |
| Difference <= 5 KW | 2 (80%) |
| Difference <= 10 KW | 1 (50%) |
| Difference > 10 KW or missing | 0 |

Same logic as HP but lower weight since HP already captures engine power. Provides a cross-check.

#### Mass (3 pts) - Percentage tolerance

| Condition | Points |
|---|---|
| Difference <= 5% | 3 (full) |
| Difference <= 10% | 1 (60%) |
| Difference > 10% or missing | 0 |

Mass varies by ~100-200kg across trims. Percentage tolerance handles different vehicle size classes.

#### Name Similarity (5 pts) - Token overlap ratio

1. Tokenize both names (split on word boundaries, lowercase)
2. Remove noise words: `cv, hp, kw, auto, aut, man, the, and, di, da`
3. Calculate: `score = 5 * len(intersection) / max(len(source_tokens), len(target_tokens))`

Returns 0 if either token set is empty after noise removal.

#### Model (5 pts) - Exact normalized match (space-insensitive)

| Condition | Points |
|---|---|
| Exact normalized match (`500x` == `500x`) | 5 (full) |
| Spaceless match (`500x` == `500 x` after space removal) | 5 (full) |
| Containment only (`500` in `500x`) or missing | 0 |

Model is the vehicle's identity -- more discriminating than seats, gears, kw, or mass. A wrong model is a fundamentally wrong match.

**Why no partial credit for containment:** Containment already did its job in Phase 2 getting candidates into the list. If the model isn't an exact match after normalization, that's a meaningful signal the candidate may be a different vehicle (e.g., FIAT 500 vs FIAT 500X).

**Space-insensitive comparison:** Eurotax `normalizedModel` values have inconsistent spacing for some models (e.g., `"500 x"` vs `"500x"`). The scoring function compares spaceless versions as a fallback, so `"500 x"` and `"500x"` both receive full points. This mirrors the spaceless containment check in Phase 2.

**Data flow:** Both source and target `specs['model']` already have `normalize_model()` applied during `extract_specs()` in `main.py`. The scoring function performs case-insensitive comparison, then falls back to spaceless comparison if needed.

---

## Phase 4: Classification

After scoring, candidates are sorted by total score (highest first). The top candidate's score determines the confidence level using percentage-based thresholds (`score / max_score`):

| Threshold | Classification | Default (157pt) | Meaning |
|---|---|---|---|
| >= 71.4% | PERFECT | >= 113 | Very high confidence, likely correct |
| >= 53.5% | LIKELY | >= 84 | Good match, review recommended |
| >= 28.5% | POSSIBLE | >= 45 | Plausible but uncertain, manual review needed |
| < 28.5% | UNLIKELY | < 45 | Low confidence, probably wrong |

The top 10 candidates are returned to the UI.

### Configurable Weight Profiles

Weights are defined as named profiles in `WEIGHT_PROFILES` dict in `matcher_v4.py`. The default profile produces 157 points max. Additional profiles can be added to experiment with different weight distributions.

- Profiles are selectable via the UI dropdown or the `profile` query parameter on `/api/search`
- `/api/profiles` endpoint returns all available profiles with their weights and max scores
- Confidence thresholds use percentage-based calculation (`score / max_score`), so they adapt automatically to any profile's total
- Adding a new profile requires only adding an entry to `WEIGHT_PROFILES` in `matcher_v4.py`

---

## Appendix A: Weight Comparison v3 vs v4

| Factor | v3 | v4 | Change |
|---|---|---|---|
| Price | 25 | 25 | - |
| HP | 20 | 20 | - |
| Trim | 15 | 15 | Clarified: derived from vehicle name via keyword extraction |
| CC | 15 | 15 | - |
| Fuel | 15 | 15 | - |
| Year | 10 | - | **Removed** (merged into Sellable) |
| Sellable | 10 | 10 | Simplified: exact 10, overlap 5, none 0 |
| Body | 10 | 10 | - |
| OEM | 20 (bonus) | **10 (scored)** | Demoted from gate to regular field, weight halved |
| Model | - | **5** | **New**: exact normalized match (containment = 0) |
| Transmission | 5 | 5 | - |
| Traction | 5 | 5 | - |
| Doors | 5 | 5 | - |
| Name | 5 | 5 | - |
| Seats | - | **3** | **New**: exact or off-by-one |
| Gears | - | **3** | **New**: exact or off-by-one |
| KW | - | **3** | **New**: absolute tolerance (cross-check for HP) |
| Mass | - | **3** | **New**: percentage tolerance |
| **Total** | **160** | **157** | -3 |

---

## Appendix B: Implementation Files

| File | Role |
|---|---|
| `matcher_v4.py` | Phases 2-4: candidate selection, scoring, classification. Exports `WEIGHT_PROFILES`, `DEFAULT_PROFILE`, `get_max_score()` for profile support |
| `normalizers.py` | Phase 1: all normalization functions (fuel, body, transmission, traction, model, OEM cleaning) |
| `vehicle_class.py` | Phase 1f: CAR/LCV classification |
| `main.py` | Phase 0: source resolution, spec extraction, API layer. Endpoints: `/api/profiles`, `/api/search?profile=` |
| `mongodb_client.py` | Data loading: MongoDB connection and queries |
| `static/index.html` | UI: displays results, comparison table, mapping actions, profile dropdown |
