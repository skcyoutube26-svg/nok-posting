"""Read-only @noknyc numbers: followers, and per post views, reach, saves, shares, likes, comments, AI label.
Prints JSON to stdout. Run: IG_TOKEN=... python stats.py
"""
import json
from publish import call

METRICS = "views,reach,saved,shares,likes,comments,total_interactions"

me = call("GET", "me", fields="username,followers_count,media_count")
posts = []
url_fields = "id,caption,media_type,media_product_type,timestamp,permalink,is_ai_generated"
for m in call("GET", "me/media", fields=url_fields, limit="50").get("data", []):
    try:
        ins = {d["name"]: d["values"][0]["value"] for d in call("GET", f"{m['id']}/insights", metric=METRICS)["data"]}
    except RuntimeError as e:
        ins = {"error": str(e)[:200]}
    m["caption"] = (m.get("caption") or "")[:60]
    try:
        m["comments_list"] = [[c.get("username"), (c.get("text") or "")[:120]] for c in call("GET", f"{m['id']}/comments", fields="text,username").get("data", [])]
    except RuntimeError as e:
        m["comments_list"] = str(e)[:120]
    posts.append({**m, **ins})
print(json.dumps({"account": me, "posts": posts}, ensure_ascii=False))
