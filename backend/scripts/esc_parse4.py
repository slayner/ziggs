import json, sys
d = json.load(sys.stdin)
e = d['enlisted'][0]
print(json.dumps(e['weapon_fns'], indent=2))