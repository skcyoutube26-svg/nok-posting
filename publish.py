"""Publish due @noknyc posts through the Instagram API with Instagram Login.

A post is one JSON file in queue/ (added only after the owner's word):
  {"publish_at": "2026-10-02T12:30:00Z", "type": "IMAGE" | "REELS" | "STORIES",
   "file": "media/rugs.jpg", "caption": "...", "alt_text": "...", "approved": "<owner's words>"}
Media is fetched by Meta from GitHub Pages (PAGES_BASE). Every post carries the AI label.
When published, the JSON moves to done/ with the media id and the media file is deleted.
Run: IG_TOKEN=... PAGES_BASE=https://<user>.github.io/<repo>/ python publish.py [--dry]
"""
import json, os, sys, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

API = "https://graph.instagram.com"
ROOT = Path(__file__).parent


def call(method, path, **params):
    params["access_token"] = os.environ["IG_TOKEN"]
    data = urllib.parse.urlencode(params).encode()
    url = f"{API}/{path}"
    req = urllib.request.Request(url, data=data) if method == "POST" else urllib.request.Request(f"{url}?{data.decode()}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path}: {e.code} {e.read().decode()[:500]}") from None


def container_params(post, url):
    kind = post["type"]
    p = {"is_ai_generated": "true"}
    if kind == "IMAGE":
        p.update(image_url=url, caption=post.get("caption", ""))
        if post.get("alt_text"):
            p["alt_text"] = post["alt_text"]  # image feed posts only (Meta)
    elif kind == "REELS":
        p.update(media_type="REELS", video_url=url, caption=post.get("caption", ""), share_to_feed="true")
        if post.get("thumb_offset_ms"):
            p["thumb_offset"] = str(post["thumb_offset_ms"])  # cover frame
    elif kind == "STORIES":
        p.update(media_type="STORIES", **({"video_url": url} if url.endswith(".mp4") else {"image_url": url}))
    else:
        raise ValueError(f"unknown type {kind}")
    return p


def publish(post):
    url = urllib.parse.urljoin(os.environ["PAGES_BASE"].rstrip("/") + "/", urllib.parse.quote(post["file"]))
    cid = call("POST", "me/media", **container_params(post, url))["id"]
    for _ in range(60):  # videos process for a while; images are usually FINISHED at once
        status = call("GET", cid, fields="status_code")["status_code"]
        if status == "FINISHED":
            break
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {cid} {status}")
        time.sleep(10)
    else:
        raise RuntimeError(f"container {cid} still processing after 10 min")
    mid = call("POST", "me/media_publish", creation_id=cid)["id"]
    try:  # it is live now: a failed read-back must not send it to the retry path
        ai = call("GET", mid, fields="is_ai_generated,permalink")
    except Exception as e:
        ai = {"read_back_error": str(e)}
    return mid, ai


WAIT_MAX = 5.5 * 3600  # GitHub's cron fires hours apart, so a run waits for posts due within this window (job limit 6 h)


def next_due():
    times = [datetime.fromisoformat(json.loads(f.read_text(encoding="utf-8"))["publish_at"].replace("Z", "+00:00"))
             for f in (ROOT / "queue").glob("*.json")]
    future = [t for t in times if t > datetime.now(timezone.utc)]
    return min(future) if future else None


def main():
    dry = "--dry" in sys.argv
    failed = run_due(dry)
    while not dry and (nxt := next_due()) is not None:
        wait = (nxt - datetime.now(timezone.utc)).total_seconds()
        if wait > WAIT_MAX:
            break
        print(f"WAIT {int(wait)} s for the post due {nxt.isoformat()}", flush=True)
        time.sleep(wait + 5)
        failed |= run_due(dry)
    sys.exit(1 if failed else 0)


def run_due(dry):
    now = datetime.now(timezone.utc)
    failed = False
    for f in sorted((ROOT / "queue").glob("*.json")):
        post = json.loads(f.read_text(encoding="utf-8"))
        due = datetime.fromisoformat(post["publish_at"].replace("Z", "+00:00"))
        if due > now:
            continue
        if not post.get("approved"):
            print(f"SKIP {f.name}: no owner approval recorded"); continue
        if not (ROOT / post["file"]).exists():
            print(f"FAIL {f.name}: {post['file']} missing"); failed = True; continue
        if dry:
            print(f"DUE {f.name}: {container_params(post, post['file'])}"); continue
        try:
            mid, ai = publish(post)
        except Exception as e:
            print(f"FAIL {f.name}: {e}"); failed = True; continue
        post.update(media_id=mid, permalink=ai.get("permalink"), is_ai_generated=ai.get("is_ai_generated"), read_back_error=ai.get("read_back_error"),
                    published_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        (ROOT / "done" / f.name).write_text(json.dumps(post, indent=1, ensure_ascii=False), encoding="utf-8")
        f.unlink()
        (ROOT / post["file"]).unlink(missing_ok=True)
        print(f"POSTED {f.name}: {post['permalink']} ai_label={post['is_ai_generated']}", flush=True)
    return failed


if __name__ == "__main__":
    main()
