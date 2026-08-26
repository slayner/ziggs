import json
d = json.load(open('/tmp/esc.json'))
e = d['enlisted'][0] if d['enlisted'] else {}
print('weapon_fns:', e.get('weapon_fns'))
print('keys:', e.get('keys'))
print('functions:', e.get('functions'))
a = d['assignments'][0] if d['assignments'] else {}
print('assignment:', a)
r = d['parties'][0]['slots'][0]['roles'][0] if d['parties'] and d['parties'][0]['slots'] and d['parties'][0]['slots'][0]['roles'] else {}
print('role weapon_id:', r.get('weapon_id'))
bi = r.get('build_items', [])
print('weapon build_item:', [x for x in bi if x.get('slot') == 'weapon'])