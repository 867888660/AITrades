from services.strategy_event_service import list_strategy_events
r1 = list_strategy_events(22, {"limit": 20})
r2 = list_strategy_events(22, {"limit": 20})

def sig(events):
    return ";".join(
        "{}|{}|{}".format(e.get("id",""), e.get("ts",""), e.get("repeat_count",""))
        for e in events
    )

s1 = sig(r1["data"])
s2 = sig(r2["data"])
print("sig stable:", s1 == s2)
print("sample ids:", [e["id"] for e in r1["data"][:5]])
print("sample ts:", [e["ts"] for e in r1["data"][:5]])