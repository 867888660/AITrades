import importlib.util
import json
from pathlib import Path


path = Path("StrategyCode") / "Stragy_Fllow_Truth.py"
spec = importlib.util.spec_from_file_location("truth_strategy", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

usedata = {
    "NowTime": "2026-05-21T10:00:00Z",
    "Yes_now_ask": 0.58,
    "Yes_now_bid": 0.57,
    "No_now_ask": 0.43,
    "No_now_bid": 0.42,
    "Yes_Now_Pos": 0.0,
    "No_Now_Pos": 0.0,
    "day_to_end": 10,
}

node = {
    "Inputs": [
        {"Context": json.dumps(usedata)},
        {"Context": "Yes"},
        {"Num": 0.65},
        {"Num": 0.05},
        {"Num": 0.01},
        {"Num": 0.5},
        {"Num": 0.25},
        {"Num": 0.4},
        {"Num": 1.0},
        {"Num": 600},
        {"Num": 0.03},
    ]
}

outputs = mod.run_node(node)
assert outputs[1]["Boolean"] is True
payload = json.loads(outputs[0]["Context"])
assert payload["schema_version"] == "2.0"
assert payload["metrics"]["signal"] == "entry"
assert payload["actions"][0]["type"] == "SETPOS"
assert payload["actions"][0]["side"] == "Yes"
assert payload["actions"][0]["target_pct"] > 0
assert payload["state_updates"]["entry_price"] == 0.58
print(json.dumps({"ok": True, "signal": payload["metrics"]["signal"], "target": payload["actions"][0]["target_pct"]}))
