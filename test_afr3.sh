#!/bin/bash
sudo -u postgres psql -d ziggs_dev -c "DELETE FROM player_kill_events WHERE killer_player_id IN (SELECT id FROM albion_players WHERE lower(name) = 'africanow') OR victim_player_id IN (SELECT id FROM albion_players WHERE lower(name) = 'africanow');" 2>/dev/null
sudo -u postgres psql -d ziggs_dev -c "DELETE FROM albion_players WHERE lower(name) = 'africanow';"
echo "Player removido"

# Limpa cache de PNG
rm -f /home/ziggs/ziggs/backend/data/profile_preview_cache/*

echo "=== Testando embed PNG (cold load) ==="
START=$(python3 -c "import time; print(int(time.time()))")
curl -s -o /tmp/afr_cold.png -w "HTTP %{http_code} Size=%{size_download}" "http://localhost:8000/players/embed/americas/Africanow.png"
END=$(python3 -c "import time; print(int(time.time()))")
ELAPSED=$((END - START))
echo " (${ELAPSED}s)"

echo "=== Verificando dados ==="
python3 << 'PYEOF'
import json, urllib.request
url = "http://localhost:8000/players/by-name/americas/Africanow"
req = urllib.request.Request(url)
with urllib.request.urlopen(req, timeout=30) as r:
    d = json.loads(r.read())
    print(f'KillFame={d.get("KillFame")} DeathFame={d.get("DeathFame")}')
    ls = d.get("LifetimeStatistics") or {}
    print(f'PvE={(ls.get("PvE") or {}).get("Total",0)}')
PYEOF