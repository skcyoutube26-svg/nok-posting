"""Nok answers comments under her OWN posts (owner, 2 Oct 2026: "Auto-replies yes").
Never comments elsewhere, never DMs. One reply per comment; skips bots, promo pages, spam and hostility.
Run: IG_TOKEN=... GEMINI_API_KEY=... python replies.py [--dry]
"""
import json, os, sys, urllib.request
from datetime import datetime, timedelta, timezone
from publish import call

MODEL = "gemini-3.8-flash"
ME = "noknyc"
MAX_REPLIES = 10
VOICE = """You write Instagram replies as Nok: a goddess who took the form of a living marble sculpture and lives an ordinary, elegant old-money life in Manhattan. The account is openly AI; never deny it, never mention it unless asked.
Voice: first person, dry, warm, observant, poised; amused, never gushing; short (one sentence, at most 15 words). No hashtags, no links, no emojis except at most one when the commenter used one. Never ask people to follow, like or share. Never promise meetings, products or replies in DMs. Never laugh out loud (no "haha", "lol"). Never invent facts about her life: no addresses, street names, neighbourhoods, shop or brand names, jobs, partners or plans; when asked where something is, deflect gracefully ("Some places are better found than told.").
Reply to what they actually said, and to THIS post: every reply is custom, tied to the post's caption and the commenter's words; never a stock line. A compliment gets gracious acceptance, a question gets a short true answer in character, a joke gets a dry one back.
A request to send or share the post ("send me this post", "can we share it") gets a gracious yes in character that mentions something from this post (owner, 3 Oct 2026: reply to all; comments keep the account alive).
An offer of paid promotion or growth services gets a poised, regal decline that never insults ("I prefer to be found, not advertised."), different every time.
Return JSON only: {"reply": "..."} or {"skip": true} only when the comment is hostile, sexual, about politics, or a scam link."""


def gemini(prompt):
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        data=json.dumps({"systemInstruction": {"parts": [{"text": VOICE}]},
                         "contents": [{"parts": [{"text": prompt}]}],
                         "generationConfig": {"responseMimeType": "application/json", "temperature": 0.8}}).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    with urllib.request.urlopen(req, timeout=60) as r:
        text = json.load(r)["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def manual(dry):
    """Post owner-reviewed replies from manual-replies.json (media_id, exact comment text, reply); skips comments already answered."""
    for item in json.load(open("manual-replies.json", encoding="utf-8")):
        for c in call("GET", f"{item['media_id']}/comments", fields="id,text,username,replies{username}").get("data", []):
            replied = any(r.get("username") == ME for r in c.get("replies", {}).get("data", []))
            if c.get("text") == item["comment"] and not replied:
                if not dry:
                    call("POST", f"{c['id']}/replies", message=item["reply"])
                print(f"{'WOULD REPLY' if dry else 'REPLIED'} to comment {c['id']}")
                break
        else:
            print(f"NO OPEN MATCH on media {item['media_id']}")


def main():
    dry = "--dry" in sys.argv
    if "--manual" in sys.argv:
        return manual(dry)
    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY not set yet; nothing to do")  # owner runs set-gemini-key after the token gets the comments permission
        return
    since = datetime.now(timezone.utc) - timedelta(days=10)
    done = 0
    for m in call("GET", "me/media", fields="id,caption,timestamp", limit="15").get("data", []):
        for c in call("GET", f"{m['id']}/comments", fields="id,text,username,timestamp,replies{username}").get("data", []):
            if done >= MAX_REPLIES:
                return
            when = datetime.fromisoformat(c["timestamp"].replace("+0000", "+00:00"))
            replied = any(r.get("username") == ME for r in c.get("replies", {}).get("data", []))
            if c.get("username") == ME or replied or when < since:
                continue
            out = gemini(f'Her post caption: "{(m.get("caption") or "")[:300]}"\nComment from @{c.get("username")}: "{c.get("text", "")[:500]}"')
            if out.get("skip") or not out.get("reply"):
                print(f"SKIP comment {c['id']}")
                continue
            reply = out["reply"].strip()[:300]
            if not dry:
                call("POST", f"{c['id']}/replies", message=reply)
            done += 1
            print(f"{'WOULD REPLY' if dry else 'REPLIED'} to comment {c['id']}")


if __name__ == "__main__":
    main()
