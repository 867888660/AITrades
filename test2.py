import requests
import json

r = requests.get('https://gamma-api.polymarket.com/markets?limit=10&closed=false&active=true')
res = []
for m in r.json():
    res.append({
        'Question (市场标题)': m.get('question'),
        'Outcomes (选项)': m.get('outcomes'),
        'Prices (当下价格/概率)': m.get('outcomePrices'),
        'Liquidity (流动池深度)': m.get('liquidity'),
        'Volume (历史交易量)': m.get('volume'),
        'EndDate (截止结算日)': m.get('endDate'),
        'ConditionID (底层合约ID)': m.get('conditionId')
    })

with open('examples.json', 'w', encoding='utf-8') as f:
    json.dump(res, f, ensure_ascii=False, indent=2)
