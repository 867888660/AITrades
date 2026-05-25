import requests
import json

r = requests.get('https://gamma-api.polymarket.com/markets?limit=1&closed=false&active=true')
with open('m.json', 'w', encoding='utf-8') as f:
    json.dump(r.json()[0], f, indent=2)
