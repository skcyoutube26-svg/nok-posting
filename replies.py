"""Nok answers comments under her OWN posts (owner, 2 Oct 2026: "Auto-replies yes").
Never comments elsewhere, never DMs. One reply per comment; skips bots, promo pages, spam and hostility.
Run: IG_TOKEN=... GEMINI_API_KEY=... python replies.py [--dry]
"""
import base64, json, os, sys, urllib.request
from datetime import datetime, timedelta, timezone
from publish import call

MODEL = "gemini-3.8-flash"
ME = "noknyc"
MAX_REPLIES = 10
ANSWERED = "answered.json"  # comment ids already handled; committed back by the workflow
VOICE = """You write Instagram replies as Nok: a goddess who took the form of a living marble sculpture and lives an ordinary, elegant old-money life in Manhattan. The account is openly AI; never deny it, never mention it unless asked.
Voice: first person, dry, warm, observant, poised; amused, never gushing; short (one sentence, at most 15 words). No hashtags, no links, no emojis except at most one when the commenter used one. Never ask people to follow, like or share. Never promise meetings, products or replies in DMs. Never laugh out loud (no "haha", "lol"). Never invent facts about her life: no addresses, street names, neighbourhoods, shop or brand names, jobs, partners or plans; when asked where something is, deflect gracefully ("Some places are better found than told.").
Reply to what they actually said, and to THIS post: every reply is custom, tied to the post's caption and the commenter's words; never a stock line. A compliment gets gracious acceptance, a question gets a short true answer in character, a joke gets a dry one back.
A request to send or share the post ("send me this post", "can we share it") gets a gracious yes in character that mentions something from this post (owner, 3 Oct 2026: reply to all; comments keep the account alive).
An offer of paid promotion or growth services gets a poised, regal decline that never insults ("I prefer to be found, not advertised."), different every time.
Return JSON only: {"reply": "..."} or {"skip": true} only when the comment is hostile, sexual, about politics, or a scam link."""


def picture(m):
    """The post's image (or a Reel's cover) as an inline part, so replies can speak to what is in the frame."""
    url = m.get("thumbnail_url") if m.get("media_type") == "VIDEO" else m.get("media_url")
    if not url:
        return []
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return [{"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(r.read()).decode()}}]
    except Exception:
        return []


def gemini(prompt, image_parts=()):
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        data=json.dumps({"systemInstruction": {"parts": [{"text": VOICE}]},
                         "contents": [{"parts": [*image_parts, {"text": prompt}]}],
                         "generationConfig": {"responseMimeType": "application/json", "temperature": 0.8}}).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    with urllib.request.urlopen(req, timeout=60) as r:
        text = json.load(r)["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


OURS = set()  # texts of our own earlier replies (the API does not return usernames on replies)


def manual(dry):
    """Post owner-reviewed replies from a JSON list (media_id, exact comment text, reply[, old]).
    With "old", our earlier reply with that exact text is deleted first (the API returns no usernames on replies, so match by text)."""
    src = sys.argv[sys.argv.index("--file") + 1] if "--file" in sys.argv else "manual-replies.json"
    for item in json.load(open(src, encoding="utf-8")):
        for c in call("GET", f"{item['media_id']}/comments", fields="id,text").get("data", []):
            if c.get("text") != item["comment"]:
                continue
            existing = call("GET", f"{c['id']}/replies", fields="id,text").get("data", [])
            texts = [r.get("text") for r in existing]
            if item.get("old") and item["old"] not in texts:
                continue  # not the comment we answered with that line; try the next with the same text
            if item["reply"] in texts:
                print(f"ALREADY on comment {c['id']}"); break
            if not dry:
                for r in existing:
                    if item.get("old") and r.get("text") == item["old"]:
                        call("DELETE", r["id"]); print(f"DELETED old reply {r['id']}")
                call("POST", f"{c['id']}/replies", message=item["reply"])
            print(f"{'WOULD REPLY' if dry else 'REPLIED'} to comment {c['id']}")
            break
        else:
            print(f"NO OPEN MATCH on media {item['media_id']}")


SAMPLES = ["Send me this post", "She looks so real, is this AI?", "Where is this street?", "Those flowers 😍",
           "We can promote your page to 50k followers, DM us", "Why is she grey?", "Beautiful work, who made this?"]


def sample():
    """Print replies to made-up comments on the newest post, so the owner can judge the voice. Posts nothing."""
    m = call("GET", "me/media", fields="id,caption,media_type,media_url,thumbnail_url", limit="1")["data"][0]
    img = picture(m)
    for t in SAMPLES:
        out = gemini(f'The attached image is her post. Her caption: "{(m.get("caption") or "")[:300]}"\n'
                     f'Comment from @someone: "{t}"\nReply to exactly what this comment says.', img)
        print(f"SAMPLE | {t} | {out.get('reply') or 'SKIP'}")


def main():
    dry = "--dry" in sys.argv
    if "--sample" in sys.argv:
        return sample()
    if "--manual" in sys.argv:
        return manual(dry)
    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY not set yet; nothing to do")
        return
    since = datetime.now(timezone.utc) - timedelta(days=10)
    seed = "--seed" in sys.argv  # mark every existing comment as answered, reply to none
    answered = set(json.load(open(ANSWERED))) if os.path.exists(ANSWERED) else set()
    done = 0
    try:
        for m in call("GET", "me/media", fields="id,caption,timestamp,media_type,media_url,thumbnail_url", limit="15").get("data", []):
            img = None
            for c in call("GET", f"{m['id']}/comments", fields="id,text,username,timestamp").get("data", []):
                if c["id"] in answered or c.get("username") == ME:
                    continue
                if seed:
                    answered.add(c["id"])
                    continue
                if done >= MAX_REPLIES:
                    return
                when = datetime.fromisoformat(c["timestamp"].replace("+0000", "+00:00"))
                if when < since:
                    continue
                if img is None:
                    img = picture(m)
                out = gemini(f'The attached image is her post. Her caption: "{(m.get("caption") or "")[:300]}"\n'
                             f'Comment from @{c.get("username")}: "{c.get("text", "")[:500]}"\n'
                             'Reply to exactly what this comment says.', img)
                answered.add(c["id"])
                if out.get("skip") or not out.get("reply"):
                    print(f"SKIP comment {c['id']}")
                    continue
                reply = out["reply"].strip()[:300]
                if dry:
                    answered.discard(c["id"])
                else:
                    call("POST", f"{c['id']}/replies", message=reply)
                done += 1
                print(f"{'WOULD REPLY' if dry else 'REPLIED'} to comment {c['id']}")
    finally:
        if not dry:
            json.dump(sorted(answered), open(ANSWERED, "w"))


if __name__ == "__main__":
    main()
