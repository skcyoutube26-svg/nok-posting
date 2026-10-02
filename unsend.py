"""Remove every DM Nok sent through the API (owner, 3 Oct 2026: "stop & delete all the auto DMs"). Logs ids only."""
from publish import call
me = call("GET", "me", fields="user_id")["user_id"]
for conv in call("GET", "me/conversations", fields="id", limit="50").get("data", []):
    msgs = call("GET", conv["id"], fields="messages{id,from,created_time}").get("messages", {}).get("data", [])
    for m in msgs:
        if (m.get("from") or {}).get("id") == me:
            try:
                call("DELETE", m["id"]); print("UNSENT", m["id"])
            except RuntimeError as e:
                print("UNSEND FAIL", m["id"], str(e)[:220])
        else:
            print("THEIR MESSAGE", m["id"])
