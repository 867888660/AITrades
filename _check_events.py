import sqlite3
from services.strategy_data_source import connect as ds_connect

conn = ds_connect(readonly=True)
conn.row_factory = sqlite3.Row

# action events
rows = conn.execute(
    "SELECT * FROM strategy_virtual_events WHERE event_type = 'action' ORDER BY id DESC LIMIT 3"
).fetchall()
print("=== ACTION EVENTS ===")
for r in rows:
    print(dict(r))

# recent orders
rows2 = conn.execute(
    "SELECT * FROM strategy_virtual_orders ORDER BY id DESC LIMIT 5"
).fetchall()
print("\n=== RECENT ORDERS ===")
for r in rows2:
    print(dict(r))

# check strategy_id for row 22
rows3 = conn.execute(
    "SELECT strategy_id, event_type, content, last_seen_utc FROM strategy_virtual_events WHERE strategy_id = 22 AND event_type = 'action' ORDER BY id DESC LIMIT 5"
).fetchall()
print("\n=== STRATEGY 22 ACTION EVENTS ===")
for r in rows3:
    print(dict(r))

conn.close()