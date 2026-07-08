import sqlite3, json
conn = sqlite3.connect("cache.db")
row = conn.execute("SELECT items_json FROM agenda_cache WHERE meeting_link LIKE '%00400%'").fetchone()
items = json.loads(row[0])
title = items[0]["title"]
print("Title:", repr(title[:30]))
print()
for i, c in enumerate(title[:20]):
    print(f"  [{i}] U+{ord(c):04X} = {repr(c)}")
