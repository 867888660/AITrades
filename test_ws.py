import asyncio

# Manual network diagnostic, not an automated pytest test.  Keep its optional
# proxy dependency out of collection and import it only when executed directly.
__test__ = False

async def test_ws_connection():
    from websockets_proxy import Proxy, proxy_connect
    url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    proxy_url = "http://127.0.0.1:7890"
    print(f"[*] 准备连接到: {url}")
    
    try:
        # Create Proxy object
        proxy = Proxy.from_url(proxy_url)
        
        async with proxy_connect(
            url, 
            proxy=proxy,
            ping_interval=30, 
            ping_timeout=10, 
            close_timeout=5,
            open_timeout=10
        ) as ws:
            print("\n[+] WebSocket 握手成功！连接已建立！")
    except Exception as e:
        print(f"\n[!] 发生异常: {type(e).__name__}: {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_ws_connection())
