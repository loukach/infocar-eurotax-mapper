# -*- coding: utf-8 -*-
"""
Find common trim-level keywords in Eurotax vehicle names (Italian market).

Connects to MongoDB x_catalogue.trims, extracts the "name" field from all
Italian Eurotax records, tokenizes the names, counts word frequency, and
filters out non-trim words to surface candidate trim tokens.

Output: words appearing in 50+ vehicle names, sorted by frequency descending.
"""
import os
import re
import sys
from pathlib import Path
from collections import Counter
from dotenv import load_dotenv
from pymongo import MongoClient

# Load .env from desktop-app-v4 directory
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)

MONGO_URI = os.environ.get('MONGO_URI')
if not MONGO_URI:
    print("ERROR: MONGO_URI not set. Check .env file.")
    sys.exit(1)

# ---- Known trim tokens to EXCLUDE (already cataloged) ----
KNOWN_TRIMS = {
    "sport", "sportline", "s-line", "s line", "sline", "amg", "m sport",
    "msport", "r-line", "r line", "rline", "gt line", "gtline", "gt-line",
    "n line", "nline", "gs line", "gsline", "gs-line", "fr", "cupra", "st",
    "rs", "vrs", "gti", "gtd", "gte", "gt", "r-design", "polestar", "veloce",
    "executive", "premium", "luxury", "exclusive", "ultimate", "inscription",
    "designo", "maybach", "lusso", "tributo", "business", "style", "elegance",
    "ambition", "ambiente", "comfort", "life", "edition", "special", "limited",
    "advanced", "tech", "active", "plus", "base", "standard", "lounge", "pop",
    "cult", "icon", "iconic", "hse", "se", "dynamic", "momentum", "titanium",
    "avantgarde", "progressive", "black edition", "dark", "first edition",
    "launch", "initiale", "intens", "zen", "techno", "shine", "allure",
    "feline", "feel", "live", "uptown", "anniversary", "innovation", "advance",
    "connect", "comfortline", "highline", "trendline", "xcellence", "xline",
    "x-line", "advantage", "kinetic", "summum", "design", "cosmo", "attraction",
    "dynamique", "seduction", "easy", "distinctive", "eletta", "progression",
    "acenta", "admired", "monte carlo", "futura", "classic", "urban", "city",
    "cross", "adventure", "offroad", "allroad", "quattro", "4matic", "xdrive",
    "awd", "4x4", "4wd", "traction",
}

# Single-word tokens from the known set for quick lookup
KNOWN_SINGLE_TOKENS = set()
for t in KNOWN_TRIMS:
    for word in t.split():
        KNOWN_SINGLE_TOKENS.add(word.lower())

# ---- Words to exclude (not trim levels) ----
# Common car makes
MAKES = {
    "fiat", "alfa", "romeo", "lancia", "maserati", "ferrari", "lamborghini",
    "abarth", "jeep", "dodge", "chrysler", "ford", "opel", "vauxhall",
    "volkswagen", "vw", "audi", "bmw", "mercedes", "mercedes-benz", "benz",
    "porsche", "mini", "smart", "volvo", "saab", "peugeot", "citroen",
    "renault", "dacia", "nissan", "toyota", "honda", "mazda", "mitsubishi",
    "subaru", "suzuki", "hyundai", "kia", "ssangyong", "mg", "seat", "skoda",
    "cupra", "tesla", "rivian", "lucid", "byd", "nio", "xpeng", "lynk",
    "polestar", "genesis", "lexus", "infiniti", "acura", "cadillac",
    "chevrolet", "buick", "gmc", "lincoln", "land", "rover", "range",
    "jaguar", "bentley", "rolls", "royce", "aston", "martin", "mclaren",
    "lotus", "alpine", "ds", "dr", "evo", "mahindra", "tata", "isuzu",
    "dfsk", "maxus", "piaggio", "iveco", "man", "daf", "scania", "volvo",
    "ebro", "aiways", "seres", "leapmotor", "great", "wall", "gwm",
    "chery", "omoda", "jaecoo", "forthing", "voyah", "zeekr", "xev",
    "mobilize", "ineos", "fisker", "ora", "wey", "haval",
}

# Common model names (top Italian market models)
MODELS = {
    "500", "500x", "500l", "500e", "panda", "tipo", "punto", "bravo",
    "stilo", "doblo", "ducato", "fiorino", "qubo", "talento", "scudo",
    "ulysse", "multipla", "croma", "linea", "freemont", "sedici", "idea",
    "giulia", "giulietta", "stelvio", "tonale", "mito", "159", "147", "156",
    "brera", "spider", "4c", "gt",
    "ypsilon", "delta", "musa", "thesis", "voyager",
    "ghibli", "levante", "quattroporte", "grecale", "granturismo", "grancabrio",
    "renegade", "compass", "cherokee", "wrangler", "gladiator", "avenger",
    "golf", "polo", "tiguan", "touareg", "passat", "arteon", "touran",
    "t-roc", "t-cross", "taigo", "id.3", "id.4", "id.5", "id.7", "caddy",
    "transporter", "crafter", "amarok", "up",
    "a1", "a3", "a4", "a5", "a6", "a7", "a8", "q2", "q3", "q4", "q5",
    "q7", "q8", "e-tron", "etron",
    "serie", "series", "x1", "x2", "x3", "x4", "x5", "x6", "x7", "ix",
    "ix1", "ix3", "i3", "i4", "i5", "i7", "z4",
    "classe", "class", "gla", "glb", "glc", "gle", "gls", "eqa", "eqb",
    "eqc", "eqe", "eqs", "cla", "cls", "slc", "slk", "sl", "amg",
    "c3", "c4", "c5", "berlingo", "jumpy", "spacetourer",
    "208", "308", "408", "508", "2008", "3008", "4008", "5008",
    "partner", "expert", "traveller", "rifter",
    "clio", "captur", "megane", "scenic", "kadjar", "koleos", "talisman",
    "kangoo", "trafic", "master", "austral", "espace", "symbioz",
    "sandero", "duster", "jogger", "spring", "bigster",
    "qashqai", "juke", "leaf", "ariya", "micra", "x-trail", "navara",
    "townstar", "primastar", "interstar",
    "yaris", "corolla", "rav4", "c-hr", "highlander", "land cruiser",
    "hilux", "proace", "aygo", "camry", "supra", "bz4x",
    "jazz", "civic", "cr-v", "hr-v", "e:ny1", "zr-v",
    "cx-3", "cx-30", "cx-5", "cx-60", "mx-30", "mx-5",
    "tucson", "kona", "ioniq", "bayon", "i10", "i20", "i30", "santa",
    "staria",
    "sportage", "niro", "sorento", "ceed", "xceed", "stonic", "picanto",
    "ev6", "ev9", "carnival",
    "octavia", "fabia", "superb", "karoq", "kodiaq", "kamiq", "scala",
    "enyaq", "elroq",
    "fortwo", "forfour", "eq",
    "xc40", "xc60", "xc90", "c40", "ex30", "ex40", "ex90", "s60", "s90",
    "v40", "v60", "v90",
    "model", "cooper", "countryman", "clubman", "paceman",
    "corsa", "astra", "mokka", "crossland", "grandland", "combo", "vivaro",
    "movano", "zafira", "insignia", "karl", "adam", "meriva",
    "ibiza", "leon", "ateca", "arona", "tarraco",
    "born", "tavascan", "formentor", "terramar",
    "fiesta", "focus", "kuga", "puma", "mustang", "explorer", "bronco",
    "transit", "ranger", "tourneo", "ecosport", "edge", "mondeo", "galaxy",
    "s-max", "mach-e",
    "tivoli", "korando", "rexton", "musso", "torres",
    "jimny", "vitara", "s-cross", "swift", "ignis", "across", "swace",
    "asx", "eclipse", "outlander", "space", "star", "l200",
    "t01", "t03",
    "spring", "duster", "jogger", "sandero", "bigster",
}

# Body types, fuel types, drivetrain, transmission words
TECHNICAL_WORDS = {
    "diesel", "benzina", "petrol", "gasoline", "metano", "gpl", "lpg", "cng",
    "hybrid", "hybride", "ibrido", "ibrida", "elettrica", "elettrico",
    "electric", "phev", "mhev", "hev", "bev", "fhev",
    "plug-in", "plugin", "mild",
    "berlina", "sedan", "saloon", "wagon", "estate", "touring", "avant",
    "sportback", "shooting", "brake", "sw", "station",
    "suv", "crossover", "coupe", "cabrio", "cabriolet", "convertible",
    "spider", "roadster", "targa", "spyder",
    "hatchback", "hatch", "3p", "5p", "3-porte", "5-porte",
    "van", "furgone", "furgonato", "cassone", "pianale", "cabinato",
    "combi", "bus", "minibus", "shuttle",
    "pick-up", "pickup", "double", "cab", "single", "crew",
    "monovolume", "mpv", "minivan",
    "automatico", "automatica", "automatic", "manuale", "manual",
    "dsg", "dct", "cvt", "tiptronic", "multitronic", "s-tronic",
    "stronic", "powershift", "easytronic", "quickshift", "eat8", "eat6",
    "edg", "at6", "at8", "at9",
    "fwd", "rwd", "integrale",
    "turbo", "biturbo", "twinturbo", "compressor", "supercharged",
    "multiair", "multijet", "multijet2", "jtd", "jtdm", "cdti", "tdci",
    "tdi", "tsi", "tfsi", "fsi", "gdi", "crdi", "mpi", "dci", "hdi",
    "puretech", "ecoboost", "ecotec", "skyactiv", "skyactiv-g", "skyactiv-d",
    "skyactiv-x", "e-skyactiv", "bluehdi", "bluehdi", "blue", "hdi",
    "1.0", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8", "1.9",
    "2.0", "2.2", "2.3", "2.4", "2.5", "2.7", "2.8", "2.9",
    "3.0", "3.2", "3.3", "3.5", "3.6", "3.8", "4.0", "4.2", "4.4",
    "4.7", "5.0", "5.2", "5.5", "6.0", "6.2", "6.5",
    "cv", "hp", "kw", "ps", "bhp",
    "con", "con.", "senza", "e", "ed", "di", "del", "della", "delle",
    "dei", "degli", "da", "dal", "dalla", "in", "per", "il", "la",
    "lo", "le", "gli", "un", "una", "uno",
    "and", "or", "the", "with", "for", "new", "nuova", "nuova", "nuovo",
    "version", "versione",
    "passo", "lungo", "corto", "standard", "short", "long", "lwb", "swb",
    "mwb", "extra",
    "porte", "posti", "seats", "doors",
    "pack", "package", "kit", "set", "optional",
}

# Patterns to exclude (regex-based)
EXCLUDE_PATTERNS = [
    r'^\d+$',             # Pure numbers
    r'^\d+\.\d+$',        # Decimal numbers (engine sizes)
    r'^\d+cv$',           # HP values like "150cv"
    r'^\d+kw$',           # KW values
    r'^\d+hp$',           # HP values
    r'^\d+ps$',           # PS values
    r'^[a-z]\d+$',        # Single letter + numbers (model codes like "e6")
    r'^\d+[a-z]$',        # Numbers + single letter
    r'^[ivx]+$',          # Roman numerals
    r'^[a-z]$',           # Single letters
    r'^[a-z]{1,2}$',      # 1-2 letter words (too short to be meaningful)
    r'^\d+-\d+$',         # Number ranges
    r'^\(\d+\)$',         # Numbers in parens
]


def should_exclude(word: str) -> bool:
    """Check if a word should be excluded from trim analysis."""
    w = word.lower().strip()

    # Too short
    if len(w) < 3:
        return True

    # In known trim tokens
    if w in KNOWN_SINGLE_TOKENS:
        return True

    # In exclusion sets
    if w in MAKES or w in MODELS or w in TECHNICAL_WORDS:
        return True

    # Matches exclusion patterns
    for pattern in EXCLUDE_PATTERNS:
        if re.match(pattern, w):
            return True

    return False


def main():
    print("Connecting to MongoDB...")
    client = MongoClient(MONGO_URI)
    db = client['x_catalogue']
    collection = db['trims']

    print("Querying Italian Eurotax trims (name field only)...")
    cursor = collection.find(
        {'country': 'it', '_source': 'eurotax'},
        {'name': 1, '_id': 0}
    )

    # Count words across all names
    word_counter = Counter()
    total_records = 0
    names_with_no_name = 0

    for doc in cursor:
        total_records += 1
        name = doc.get('name', '')
        if not name:
            names_with_no_name += 1
            continue

        # Tokenize: split on spaces, hyphens (but keep hyphenated words too),
        # slashes, commas, parentheses, dots (when not in decimals)
        # First, normalize the name
        name_lower = name.lower().strip()

        # Split on whitespace and common delimiters
        tokens = re.split(r'[\s/,\(\)\[\]\+]+', name_lower)

        # Also add hyphenated compounds as single tokens
        # e.g., "n-line" stays as "n-line" in addition to "n" and "line"
        seen_in_name = set()
        for token in tokens:
            token = token.strip('.-_')
            if not token:
                continue
            if token not in seen_in_name:
                seen_in_name.add(token)
                word_counter[token] += 1

    print(f"\nTotal records: {total_records}")
    print(f"Records without name: {names_with_no_name}")
    print(f"Unique tokens found: {len(word_counter)}")

    # Filter: minimum 50 occurrences, not excluded
    MIN_COUNT = 50
    candidates = {
        word: count for word, count in word_counter.items()
        if count >= MIN_COUNT and not should_exclude(word)
    }

    # Sort by frequency descending
    sorted_candidates = sorted(candidates.items(), key=lambda x: -x[1])

    print(f"\n{'='*60}")
    print(f"POTENTIAL TRIM TOKENS (appearing in {MIN_COUNT}+ vehicle names)")
    print(f"{'='*60}")
    print(f"{'Rank':<6} {'Token':<30} {'Count':<10}")
    print(f"{'-'*6} {'-'*30} {'-'*10}")

    for i, (word, count) in enumerate(sorted_candidates, 1):
        print(f"{i:<6} {word:<30} {count:<10}")

    print(f"\n{'='*60}")
    print(f"Total candidate trim tokens: {len(sorted_candidates)}")
    print(f"{'='*60}")

    # Also print as a Python list for easy copy-paste
    print("\n# Python list for copy-paste:")
    token_list = [word for word, _ in sorted_candidates]
    print(f"new_trim_tokens = {token_list}")

    client.close()


if __name__ == "__main__":
    main()
