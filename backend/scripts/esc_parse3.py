import json
d = json.load(open('/tmp/esc.json'))
print(json.dumps(d['enlisted'][0], indent=2))