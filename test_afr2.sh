#!/bin/bash
# Limpa o player do DB pra simular primeira visita
python3 << 'PYEOF'
import psycopg2
conn = psycopg2.connect("dbname=ziggs_dev user=ziggs password=b770e312c828031d02364893003a9caf host=localhost")
cur = conn.cursor()
cur.execute("DELETE FROM player_kill_events WHERE killer_player_id IN (SELECT id FROM albion_players WHERE name = 'Africanow') OR victim_player_id IN (SELECT id FROM albion_players WHERE name = 'Africanow')")
cur.execute("DELETE FROM albion_players WHERE lower(name) = 'africanow'")
conn.commit()
cur.close()
conn.close()
print("Africanow removido do DB")
PYEOF

echo "=== Testando embed PNG ==="
START=$(date +%s)
curl -s -o /tmp/afr_test.png -w "HTTP %{http_code} Size=%{size_download}" "http://localhost:8000/players/embed/americas/Africanow.png"
END=$(date +%s)
echo " (${END}s total)"

echo "=== Verificando dados no PNG ==="
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