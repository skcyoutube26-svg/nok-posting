"""Nok's DMs (owner, 3 Oct 2026: "send images as DMs when requested & decline with goddess aura when they offer paid promotions").
--test: check the token can read conversations. --private: one private reply per comment in manual-replies.json that promised a DM,
carrying the post's link (the API allows text only, once per comment, within 7 days)."""
import json, sys
from publish import call

def me():
    return call("GET", "me", fields="user_id")["user_id"]

if "--test" in sys.argv:
    try:
        d = call("GET", "me/conversations", fields="id,updated_time", limit="20")
        print("CONVERSATIONS_READ OK", len(d.get("data", [])))
    except RuntimeError as e:
        print("CONVERSATIONS_READ FAIL", str(e)[:300])

if "--private" in sys.argv:
    uid = me()
    src = sys.argv[sys.argv.index("--file") + 1] if "--file" in sys.argv else "manual-replies.json"
    for item in json.load(open(src, encoding="utf-8")):
        if "DM" not in item["reply"] and not item["reply"].startswith("Sending"):
            continue
        link = call("GET", item["media_id"], fields="permalink")["permalink"]
        for c in call("GET", f"{item['media_id']}/comments", fields="id,text").get("data", []):
            if c.get("text") == item["comment"]:
                try:
                    call("POST", f"{uid}/messages", recipient=json.dumps({"comment_id": c["id"]}),
                         message=json.dumps({"text": f"Here it is, as promised: {link}"}))
                    print("DM SENT for comment", c["id"])
                except RuntimeError as e:
                    print("DM FAIL for comment", c["id"], str(e)[:200])
                break
