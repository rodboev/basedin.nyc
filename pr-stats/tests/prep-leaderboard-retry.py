import json
import sys
from pathlib import Path

cache_file = Path(sys.argv[1])
data = json.loads(cache_file.read_text(encoding="utf-8"))
leaderboards = data.setdefault("leaderboards", {})
key = "NousResearch/hermes-agent|community-shipped-v4|all"
if key in leaderboards:
    entry = leaderboards[key]
    stats = entry.get("stats") or {}
    logins = entry.get("logins") or []
    if len(stats) == 0 and len(logins) == 0:
        del leaderboards[key]
        print(f"Removed empty cache entry: {key}")
    else:
        print(f"Keeping populated cache entry: {key}")
else:
    print(f"No cache entry to remove: {key}")
cache_file.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
