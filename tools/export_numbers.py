"""Numbers-only weekly table for sharing off this PC: no account name, ids, captions, comments or links.
Use: python tools/export_numbers.py insights/raw-YYYY-MM-DD.json > insights/export-YYYY-MM-DD.csv"""
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
keys = ["views", "reach", "likes", "comments", "saved", "shares", "total_interactions"]
print("posted_utc,type," + ",".join(keys))
for p in d["posts"]:
    t = "REEL" if p.get("media_product_type") == "REELS" else p.get("media_type", "")
    ins = p.get("insights", p)
    print(",".join([str(p.get("timestamp", ""))[:16], t] + [str(ins.get(k, "")) for k in keys]))
print("# followers,%s" % d["account"].get("followers_count", ""))
