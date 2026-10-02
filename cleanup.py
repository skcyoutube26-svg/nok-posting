"""Delete Nok's superseded replies (the 3 Oct riddle replies) wherever they still stand."""
import json
from publish import call
OLD = json.load(open("old-replies.json", encoding="utf-8"))
for item in json.load(open("manual-replies.json", encoding="utf-8")):
    for c in call("GET", f"{item['media_id']}/comments", fields="id,text").get("data", []):
        for r in call("GET", f"{c['id']}/replies", fields="id,text,username,from").get("data", []):
            if r.get("text") in OLD:
                call("DELETE", r["id"]); print("DELETED reply", r["id"])
            else:
                print("KEPT reply", r["id"], (r.get("username") or (r.get("from") or {}).get("username")))
