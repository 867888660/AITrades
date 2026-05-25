import requests
import json
import urllib.parse
from datetime import datetime

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
TARGET_SLUG = "us-forces-enter-iran-by"

output_file = "iran_market_full_dump.md"

def fetch_data():
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"# 完整未删减数据: {TARGET_SLUG}\n\n")
        f.write(f"抓取时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        # 1. 抓取 Gamma API 完整数据
        f.write("## 1. Gamma API (`/events` 或 `/markets`) 完整返回\n")
        f.write("这里我们通过 slug 查找对应的市场信息。\n\n")
        
        try:
            # Polymarket 的 gamma api 可以通过 slug 搜索 event
            gamma_url = f"{GAMMA_API}/events?slug={TARGET_SLUG}"
            r = requests.get(gamma_url)
            r.raise_for_status()
            events_data = r.json()
            
            f.write("### 1.1 Event 级别完整响应 (Event 包含一个或多个 Markets)\n")
            f.write("```json\n")
            f.write(json.dumps(events_data, indent=2, ensure_ascii=False))
            f.write("\n```\n\n")
            
            # 找到具体的 Market (通常是一个 Event 里的第一个或者具有对应条件的 Market)
            if not events_data:
                f.write("> 未找到对应的 Event 数据。市场可能已下架或 Slug 不正确。\n")
                return

            event = events_data[0]
            markets = event.get('markets', [])
            
            if not markets:
                f.write("> 该 Event 下没有活跃的 Market。\n")
                return
                
            active_markets = [m for m in markets if m.get('active') and not m.get('closed')]
            if not active_markets:
                f.write("> 无活跃市场\n")
                return
            target_market = active_markets[0]
            condition_id = target_market.get('conditionId')
            
            # We don't need a second call, market data is inside event
            market_full_data = target_market
            
            f.write("### 1.2 Market 级别完整响应 (包含 Token IDs 和 实时价格)\n")
            f.write("注：这些数据直接包含在 Event 的 markets 数组中。\n")
            f.write("```json\n")
            f.write(json.dumps(market_full_data, indent=2, ensure_ascii=False))
            f.write("\n```\n\n")
            
            # 2. 抓取 CLOB API 完整数据
            f.write("## 2. CLOB API (`/book`) 完整返回\n")
            f.write("我们将使用提取到的 `clobTokenIds` 来请求 CLOB 订单簿。\n\n")
            
            clobTokenIds_str = market_full_data.get('clobTokenIds', '[]')
            outcomes_str = market_full_data.get('outcomes', '[]')
            
            clobTokenIds = json.loads(clobTokenIds_str) if isinstance(clobTokenIds_str, str) else clobTokenIds_str
            outcomes = json.loads(outcomes_str) if isinstance(outcomes_str, str) else outcomes_str
            
            if not clobTokenIds or len(clobTokenIds) == 0:
                f.write("> 无法解析出 clobTokenIds，无法调用 CLOB API。\n")
                return
                
            # 我们通常关注 "Yes" 选项的深度（假设它是第一个选项）
            target_token_id = clobTokenIds[0]
            target_outcome_name = outcomes[0] if len(outcomes) > 0 else "Unknown"
            
            clob_url = f"{CLOB_API}/book?token_id={target_token_id}"
            r_clob = requests.get(clob_url)
            clob_full_data = r_clob.json()
            
            f.write(f"### 选项 '{target_outcome_name}' 的 CLOB 完整订单流 (Token ID: {target_token_id})\n")
            f.write("注：由于订单簿可能极长，这里直接列出返回的所有几十或上百行出价记录。\n")
            f.write("```json\n")
            f.write(json.dumps(clob_full_data, indent=2, ensure_ascii=False))
            f.write("\n```\n\n")

        except Exception as e:
            f.write(f"\n请求过程中发生错误: {str(e)}\n")

if __name__ == "__main__":
    fetch_data()
    print("Fetch complete.")
