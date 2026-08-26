import json
d = json.load(open('/tmp/esc.json'))
for p in d['parties'][:3]:
  for s in p['slots'][:5]:
    for r in s['roles'][:3]:
      print(f"slot={s['id']} role={r['name']} weapon_id={r.get('weapon_id')} weapon_name={r.get('weapon_name')}")
      bi = r.get('build_items', [])
      w = [x for x in bi if x.get('slot') == 'weapon']
      if w:
        print(f"  build_items weapon: {w[0].get('item_id')}")