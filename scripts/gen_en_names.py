"""
Gera src/data/en-names.json com mapeamento baseId -> nome EN-US
do ao-bin-dumps. Rodar da raiz do projeto:
  python scripts/gen_en_names.py
"""
import httpx, json, re, pathlib

NAMES_URL = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/formatted/items.json"

TIER_STRIP = re.compile(
    r"^(Beginner's|Novice's|Journeyman's|Adept's|Expert's|Master's|"
    r"Grandmaster's|Elder's|Novice|Journeyman|Adept|Expert|Master|Grandmaster|Elder)\s+",
    re.IGNORECASE,
)

def base_id(uid: str) -> str:
    return re.sub(r"^T\d+_", "", uid.split("@")[0])

def clean_en(name: str) -> str:
    return TIER_STRIP.sub("", name).strip()

def tier_of(uid: str) -> int:
    m = re.match(r"^T(\d+)_", uid)
    return int(m.group(1)) if m else 0

print("Buscando formatted/items.json…")
data = httpx.get(NAMES_URL, timeout=60).json()

# Prefer T4 entry; strip tier prefix; ignore enchantments
tier_map: dict[str, tuple[int, str]] = {}  # bid -> (tier, clean_name)
for item in data:
    if not isinstance(item, dict) or "UniqueName" not in item:
        continue
    uid = item["UniqueName"]
    if "@" in uid:
        continue
    en = (item.get("LocalizedNames") or {}).get("EN-US", "")
    if not en:
        continue
    bid = base_id(uid)
    t = tier_of(uid)
    cleaned = clean_en(en)
    existing_t, _ = tier_map.get(bid, (0, ""))
    # Prefer T4; if not yet found or current is closer to T4
    if bid not in tier_map or abs(t - 4) < abs(existing_t - 4):
        tier_map[bid] = (t, cleaned)

mapping = {bid: name for bid, (_, name) in tier_map.items() if name}

out = pathlib.Path(__file__).parent.parent / "frontend" / "src" / "data" / "en-names.json"
out.write_text(json.dumps(mapping, indent=2, ensure_ascii=False))
print(f"Escrito {len(mapping)} entradas em {out}")
