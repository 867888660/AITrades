import requests
import json
import os

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

output_md = "api_examples.md"

with open(output_md, 'w', encoding='utf-8') as f:
    f.write("# Polymarket API 真实数据示例\n\n")
    
    # 获取 Gamma 数据
    try:
        r = requests.get(f"{GAMMA_API}/markets?limit=15&active=true&withTokens=true&withClobTokenIds=true&withOutcomes=true")
        resp_json = r.json()
        gamma_data = resp_json.get('data', resp_json) if isinstance(resp_json, dict) else resp_json
        gamma_data = gamma_data[:10]
        
        f.write("## 1. Gamma API (`/markets`) 返回的 10 条完整数据\n\n")
        f.write("这是包含市场元数据、问题、结束时间、以及当前赔率的宏观接口。\n\n")
        for i, item in enumerate(gamma_data):
            f.write(f"### Gamma 示例 {i+1}: {item.get('question', 'Unknown Question')}\n")
            f.write("```json\n")
            f.write(json.dumps(item, indent=2, ensure_ascii=False))
            f.write("\n```\n\n")
            
        f.write("## 2. CLOB API (`/book`) 返回的 10 条完整深度数据\n\n")
        f.write("这是对应上述市场中某个具体选项（Yes/No）的订单簿微观接口。\n\n")
        
        clob_count = 0
        clob_data = resp_json.get('data', resp_json) if isinstance(resp_json, dict) else resp_json
        for item in clob_data:
            if clob_count >= 10:
                break
            
            clobTokenIds_str = item.get('clobTokenIds', '[]')
            outcomes_str = item.get('outcomes', '[]')
            try:
                clobTokenIds = json.loads(clobTokenIds_str) if isinstance(clobTokenIds_str, str) else clobTokenIds_str
                outcomes = json.loads(outcomes_str) if isinstance(outcomes_str, str) else outcomes_str
                
                if clobTokenIds and isinstance(clobTokenIds, list) and len(clobTokenIds) > 0:
                    token_id = clobTokenIds[0]
                    outcome_name = outcomes[0] if outcomes and isinstance(outcomes, list) and len(outcomes) > 0 else "Unknown"
                    
                    if token_id:
                        clob_resp = requests.get(f"{CLOB_API}/book?token_id={token_id}").json()
                        f.write(f"### CLOB 示例 {clob_count+1}: 选项 '{outcome_name}' 的实时订单流 (Token: {token_id})\n")
                        f.write("```json\n")
                        f.write(json.dumps(clob_resp, indent=2, ensure_ascii=False))
                        f.write("\n```\n\n")
                        clob_count += 1
            except Exception as e:
                print(f"Error parsing token ids for clob: {e}")
                
    except Exception as e:
        f.write(f"发生错误: {str(e)}")

print(f"Successfully generated {output_md}")
