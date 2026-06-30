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

momentum_usedata = {
    "NowTime": "2026-05-23T14:15:00Z",
    "Yes_now_ask": 0.45,
    "Yes_now_bid": 0.44,
    "No_now_ask": 0.57,
    "No_now_bid": 0.55,
    "Yes_Now_Pos": 0.1118,
    "No_Now_Pos": 0.0,
    "Yes_now_Qty": 47.41,
    "Yes_now_avgPrice": 0.30,
    "Yes_MACDHist": 0.012,
    "Yes_MACDHistPrev": 0.006,
    "RuntimeState": {"entry_price": 0.30, "entry_side": "Yes"},
    "day_to_end": 10,
}
momentum_node = dict(node)
momentum_node["Inputs"] = list(node["Inputs"])
momentum_node["Inputs"][0] = {"Context": json.dumps(momentum_usedata)}
momentum_outputs = mod.run_node(momentum_node)
assert momentum_outputs[1]["Boolean"] is True
momentum_payload = json.loads(momentum_outputs[0]["Context"])
assert momentum_payload["metrics"]["signal"] == "momentum_hold_take_profit"
assert momentum_payload["metrics"]["momentum_strong"] is True
assert momentum_payload["actions"] == []
assert momentum_payload["state_updates"]["profit_protected"] is True

weak_usedata = dict(momentum_usedata)
weak_usedata["NowTime"] = "2026-05-23T14:20:00Z"
weak_usedata["Yes_MACDHist"] = 0.004
weak_usedata["Yes_MACDHistPrev"] = 0.006
weak_node = dict(node)
weak_node["Inputs"] = list(node["Inputs"])
weak_node["Inputs"][0] = {"Context": json.dumps(weak_usedata)}
weak_payload = json.loads(mod.run_node(weak_node)[0]["Context"])
assert weak_payload["metrics"]["signal"] == "partial_take_profit"
assert weak_payload["actions"][0]["target_pct"] < weak_usedata["Yes_Now_Pos"]
assert weak_payload["state_updates"]["partial_tp_done"] is True

trailing_usedata = dict(momentum_usedata)
trailing_usedata["NowTime"] = "2026-05-23T14:30:00Z"
trailing_usedata["Yes_now_bid"] = 0.55
trailing_usedata["Yes_now_ask"] = 0.56
trailing_usedata["Yes_MACDHist"] = 0.002
trailing_usedata["Yes_MACDHistPrev"] = 0.004
trailing_usedata["RuntimeState"] = {
    "entry_price": 0.30,
    "entry_side": "Yes",
    "profit_protected": True,
    "peak_bid": 0.66,
    "momentum_hold_since": "2026-05-23T14:15:00Z",
}
trailing_node = dict(node)
trailing_node["Inputs"] = list(node["Inputs"])
trailing_node["Inputs"][0] = {"Context": json.dumps(trailing_usedata)}
trailing_payload = json.loads(mod.run_node(trailing_node)[0]["Context"])
assert trailing_payload["metrics"]["signal"] == "trailing_stop"
assert trailing_payload["actions"][0]["target_pct"] == 0.0

recovery_tp_usedata = {
    "NowTime": "2026-05-29T14:55:29Z",
    "Yes_now_ask": 0.45,
    "Yes_now_bid": 0.44,
    "No_now_ask": 0.57,
    "No_now_bid": 0.55,
    "Yes_Now_Pos": 0.0875,
    "No_Now_Pos": 0.0,
    "Yes_now_Qty": 26.515151515151516,
    "Yes_now_avgPrice": 0.33,
    "Yes_MACDHist": -0.0104,
    "Yes_MACDHistPrev": -0.0038,
    "RuntimeState": {
        "entry_price": 0.33,
        "entry_side": "Yes",
        "peak_bid": 0.41,
        "recent_peak_bid": 0.41,
        "recent_peak_bid_at": "2026-05-29T14:55:00Z",
        "last_bid": 0.41,
    },
    "day_to_end": 31.37,
}
recovery_tp_node = dict(node)
recovery_tp_node["Inputs"] = list(node["Inputs"])
while len(recovery_tp_node["Inputs"]) <= 31:
    recovery_tp_node["Inputs"].append({})
recovery_tp_node["Inputs"][0] = {"Context": json.dumps(recovery_tp_usedata)}
recovery_tp_node["Inputs"][2] = {"Num": 0.45}
recovery_tp_node["Inputs"][3] = {"Num": 0.05}
recovery_tp_node["Inputs"][7] = {"Num": 0.30}
recovery_tp_node["Inputs"][22] = {"Num": 0.14}
recovery_tp_node["Inputs"][23] = {"Num": 0.20}
recovery_tp_node["Inputs"][25] = {"Num": 0.20}
recovery_tp_node["Inputs"][31] = {"Num": 0.10}
recovery_tp_payload = json.loads(mod.run_node(recovery_tp_node)[0]["Context"])
assert recovery_tp_payload["metrics"]["signal"] == "price_recovery_hold_take_profit"
assert recovery_tp_payload["metrics"]["target"] == recovery_tp_usedata["Yes_Now_Pos"]
assert recovery_tp_payload["metrics"]["price_follow_through"] is True
assert recovery_tp_payload["actions"] == []
assert recovery_tp_payload["state_updates"]["profit_protected"] is True

guard_usedata = {
    "NowTime": "2026-05-28T13:37:53Z",
    "Yes_now_ask": 0.42,
    "Yes_now_bid": 0.41,
    "No_now_ask": 0.59,
    "No_now_bid": 0.58,
    "Yes_Now_Pos": 0.088,
    "No_Now_Pos": 0.0,
    "Yes_now_Qty": 20.94,
    "Yes_now_avgPrice": 0.42,
    "Yes_MACDHist": -0.002,
    "Yes_MACDHistPrev": 0.004,
    "RuntimeState": {
        "entry_price": 0.42,
        "entry_side": "Yes",
        "last_valid_day_to_end": 2.44,
        "last_valid_day_to_end_at": "2026-05-28T13:21:35Z",
    },
    "day_to_end": 0.0,
}
guard_node = dict(node)
guard_node["Inputs"] = list(node["Inputs"])
guard_node["Inputs"][0] = {"Context": json.dumps(guard_usedata)}
guard_node["Inputs"][2] = {"Num": 0.58}
guard_node["Inputs"][3] = {"Num": 0.06}
guard_node["Inputs"][5] = {"Num": 0.18}
guard_node["Inputs"][8] = {"Num": 5.0}
guard_node["Inputs"][10] = {"Num": 0.01}
guard_payload = json.loads(mod.run_node(guard_node)[0]["Context"])
assert guard_payload["metrics"]["day_to_end_guarded"] is True
assert guard_payload["metrics"]["day_to_end"] > 2.0
assert guard_payload["metrics"]["target"] > 0.0
assert guard_payload["metrics"]["signal"] == "de_risk"
assert guard_payload["actions"][0]["target_pct"] < guard_usedata["Yes_Now_Pos"]

missing_time_usedata = dict(guard_usedata)
missing_time_usedata["Yes_Now_Pos"] = 0.0
missing_time_usedata["Yes_now_Qty"] = 0.0
missing_time_usedata["Yes_now_avgPrice"] = 0.0
missing_time_usedata["RuntimeState"] = {}
missing_time_node = dict(guard_node)
missing_time_node["Inputs"] = list(guard_node["Inputs"])
missing_time_node["Inputs"][0] = {"Context": json.dumps(missing_time_usedata)}
missing_time_payload = json.loads(mod.run_node(missing_time_node)[0]["Context"])
assert missing_time_payload["metrics"]["day_to_end_guarded"] is True
assert missing_time_payload["metrics"]["signal"] == "time_data_guard"
assert missing_time_payload["metrics"]["target"] == 0.0
assert missing_time_payload["actions"] == []

flat_entry_usedata = dict(usedata)
flat_entry_usedata.update({
    "Yes_MACDHist": -0.003,
    "Yes_MACDHistPrev": 0.002,
    "RuntimeState": {
        "recent_peak_bid": 0.60,
        "recent_peak_bid_at": "2026-05-21T09:55:00Z",
    },
})
flat_entry_node = dict(node)
flat_entry_node["Inputs"] = list(node["Inputs"])
flat_entry_node["Inputs"][0] = {"Context": json.dumps(flat_entry_usedata)}
flat_payload = json.loads(mod.run_node(flat_entry_node)[0]["Context"])
assert flat_payload["metrics"]["momentum_state"] == "flat"
assert flat_payload["metrics"]["macd_flat_without_shock"] is True
assert flat_payload["metrics"]["target"] > 0.0

flat_accum_usedata = {
    "NowTime": "2026-05-30T01:48:25Z",
    "Yes_now_ask": 0.36,
    "Yes_now_bid": 0.35,
    "No_now_ask": 0.65,
    "No_now_bid": 0.64,
    "Yes_Now_Pos": 0.11111111111111116,
    "No_Now_Pos": 0.0,
    "Yes_now_Qty": 30.864197530864214,
    "Yes_now_avgPrice": 0.36,
    "Yes_MACDHist": -0.0104,
    "Yes_MACDHistPrev": -0.0038,
    "RuntimeState": {
        "entry_price": 0.36,
        "entry_side": "Yes",
        "recent_peak_bid": 0.36,
        "recent_peak_bid_at": "2026-05-30T01:47:25Z",
    },
    "day_to_end": 31.37,
}
flat_accum_node = dict(node)
flat_accum_node["Inputs"] = list(node["Inputs"])
while len(flat_accum_node["Inputs"]) <= 31:
    flat_accum_node["Inputs"].append({})
flat_accum_node["Inputs"][0] = {"Context": json.dumps(flat_accum_usedata)}
flat_accum_node["Inputs"][2] = {"Num": 0.45}
flat_accum_node["Inputs"][3] = {"Num": 0.05}
flat_accum_node["Inputs"][5] = {"Num": 1.0}
flat_accum_node["Inputs"][22] = {"Num": 0.14}
flat_accum_node["Inputs"][23] = {"Num": 0.20}
flat_accum_node["Inputs"][25] = {"Num": 0.20}
flat_accum_node["Inputs"][31] = {"Num": 0.10}
flat_accum_payload = json.loads(mod.run_node(flat_accum_node)[0]["Context"])
assert flat_accum_payload["metrics"]["momentum_state"] == "flat"
assert flat_accum_payload["metrics"]["entry_momentum_multiplier"] >= 0.70
assert flat_accum_payload["metrics"]["target"] > 0.30
assert flat_accum_payload["metrics"]["signal"] == "add_edge"
assert flat_accum_payload["actions"][0]["target_pct"] > 0.30

downtrend_entry_usedata = dict(flat_entry_usedata)
downtrend_entry_usedata.update({
    "RuntimeState": {
        "recent_peak_bid": 0.70,
        "recent_peak_bid_at": "2026-05-21T09:55:00Z",
    },
})
downtrend_entry_node = dict(node)
downtrend_entry_node["Inputs"] = list(node["Inputs"])
downtrend_entry_node["Inputs"][0] = {"Context": json.dumps(downtrend_entry_usedata)}
downtrend_payload = json.loads(mod.run_node(downtrend_entry_node)[0]["Context"])
assert downtrend_payload["metrics"]["signal"] == "wait_momentum"
assert downtrend_payload["metrics"]["momentum_state"] == "down"
assert downtrend_payload["metrics"]["shock_triggered"] is True
assert downtrend_payload["metrics"]["target"] == 0.0

shock_usedata = dict(usedata)
shock_usedata.update({
    "NowTime": "2026-05-21T10:10:00Z",
    "Yes_now_ask": 0.50,
    "Yes_now_bid": 0.49,
    "Yes_MACDHist": 0.002,
    "Yes_MACDHistPrev": 0.004,
    "RuntimeState": {
        "recent_peak_bid": 0.60,
        "recent_peak_bid_at": "2026-05-21T10:00:00Z",
    },
})
shock_node = dict(node)
shock_node["Inputs"] = list(node["Inputs"])
shock_node["Inputs"][0] = {"Context": json.dumps(shock_usedata)}
shock_payload = json.loads(mod.run_node(shock_node)[0]["Context"])
assert shock_payload["metrics"]["shock_triggered"] is True
assert shock_payload["metrics"]["signal"] == "shock_block_open"
assert shock_payload["metrics"]["target"] == 0.0

stop_loss_usedata = {
    "NowTime": "2026-06-01T13:49:11Z",
    "Yes_now_ask": 0.24,
    "Yes_now_bid": 0.23,
    "No_now_ask": 0.77,
    "No_now_bid": 0.75,
    "Yes_Now_Pos": 0.75,
    "No_Now_Pos": 0.0,
    "Yes_now_Qty": 225.71,
    "Yes_now_avgPrice": 0.29,
    "RuntimeState": {
        "entry_price": 0.29,
        "entry_side": "Yes",
    },
    "day_to_end": 28.42,
}
stop_loss_node = dict(node)
stop_loss_node["Inputs"] = list(node["Inputs"])
while len(stop_loss_node["Inputs"]) <= 22:
    stop_loss_node["Inputs"].append({})
stop_loss_node["Inputs"][0] = {"Context": json.dumps(stop_loss_usedata)}
stop_loss_node["Inputs"][2] = {"Num": 0.45}
stop_loss_node["Inputs"][6] = {"Num": 0.18}
stop_loss_node["Inputs"][22] = {"Num": 0.14}
stop_loss_payload = json.loads(mod.run_node(stop_loss_node)[0]["Context"])
assert stop_loss_payload["metrics"]["signal"] == "stop_loss"
assert stop_loss_payload["actions"][0]["target_pct"] == 0.0
assert stop_loss_payload["machine_state_updates"]["state"] == "stop_loss_locked"

locked_usedata = dict(usedata)
locked_usedata.update({
    "Yes_now_ask": 0.18,
    "Yes_now_bid": 0.17,
    "MachineState": "stop_loss_locked",
    "StrategyState": {"state": "stop_loss_locked"},
})
locked_node = dict(stop_loss_node)
locked_node["Inputs"] = list(stop_loss_node["Inputs"])
locked_node["Inputs"][0] = {"Context": json.dumps(locked_usedata)}
locked_payload = json.loads(mod.run_node(locked_node)[0]["Context"])
assert locked_payload["metrics"]["signal"] == "stop_loss_locked"
assert locked_payload["metrics"]["stop_loss_locked"] is True
assert locked_payload["metrics"]["target"] == 0.0
assert locked_payload["actions"] == []

print(json.dumps({
    "ok": True,
    "entry_signal": payload["metrics"]["signal"],
    "entry_target": payload["actions"][0]["target_pct"],
    "momentum_signal": momentum_payload["metrics"]["signal"],
    "weak_signal": weak_payload["metrics"]["signal"],
    "trailing_signal": trailing_payload["metrics"]["signal"],
    "recovery_tp_signal": recovery_tp_payload["metrics"]["signal"],
    "guarded_day_to_end": guard_payload["metrics"]["day_to_end"],
    "missing_time_signal": missing_time_payload["metrics"]["signal"],
    "flat_entry_signal": flat_payload["metrics"]["signal"],
    "flat_accum_target": flat_accum_payload["metrics"]["target"],
    "downtrend_entry_signal": downtrend_payload["metrics"]["signal"],
    "shock_signal": shock_payload["metrics"]["signal"],
    "stop_loss_machine_state": stop_loss_payload["machine_state_updates"]["state"],
    "locked_signal": locked_payload["metrics"]["signal"],
}))
