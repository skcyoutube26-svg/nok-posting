"""Build a queue entry for publish.py and check that Pages really serves its media.

Two steps, with a push in between. Both make runs (dry or --write) run the house post_check script
(--post-check PATH or the POST_CHECK env var) on the real media (--media); any FAIL or non-zero exit blocks
--write. With no path the tool prints POST_CHECK NOT RUN and refuses --write. --no-post-check skips the check
for a dry run only; combined with --write it is refused. With --write the entry
is written only after the media URL (already pushed) (--pages-base, default the PAGES_BASE env var) answers 200 with the
right content type and the post time clears the margin; on timeout nothing is written and the exit is 2.

  1. make     python tools/make_queue_entry.py make --media photo.jpg --caption-file cap.txt \\
                  --hashtags "#a #b" --alt-text "..." --publish-at 2026-11-02T17:00:00Z \\
                  --approved "Owner, 1 Nov: ..." [--write]
              Dry run unless --write. Checks the post the way publish.py will, copies the media into
              <pages-dir>/media/ and writes the entry JSON into the queue folder.
  2. confirm  python tools/make_queue_entry.py confirm queue/<entry>.json --pages-base https://<user>.github.io/<repo>/
              Run after the push. Polls the media URL until it answers 200 with the right content type,
              then insists the entry falls due at least CONFIRM_MARGIN after that moment (Meta caches a 404 for
              about 10 minutes if it fetches early). Exit 0 only when that holds.

The tool cannot stop an entry already on main from falling due; the timing rule is enforced by the exit code.
It never reads IG_TOKEN and never calls Instagram.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import publish  # noqa: E402  (the real publisher: its container_params is the type check)

MEDIA_DIR = "media"
MIN_LEAD = timedelta(minutes=30)       # push + Pages build + the margin below, checked when writing
CACHE_404 = timedelta(minutes=10)      # how long Meta keeps a 404 it got by fetching early
CONFIRM_MARGIN = CACHE_404 + timedelta(minutes=5)  # due time must be at least this long after the confirmed fetch
POLL_EVERY = 20                        # seconds
POLL_MAX = timedelta(minutes=30)

# extension -> (post type it implies, content type Pages should answer with)
MEDIA_TYPES = {
    ".jpg": ("IMAGE", "image/jpeg"),
    ".jpeg": ("IMAGE", "image/jpeg"),
    ".png": ("IMAGE", "image/png"),
    ".mp4": ("REELS", "video/mp4"),
    ".mov": ("REELS", "video/quicktime"),
}
# The pre-commit hook greps for: approved, colon, a quoted value containing Owner or owner.
APPROVED_RE = re.compile(r'approved"?\s*:\s*"[^"]*[Oo]wner')
FAIL_ROW = re.compile(r"^\s*FAIL\b", re.MULTILINE)  # a result row ("  FAIL  check ..."), not the summary "No FAIL in 1 post."
POST_CHECK_TIMEOUT = 120               # seconds
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class Refused(Exception):
    """The entry (or the timing) is not acceptable; the message says what to fix."""


# ---- inputs ----------------------------------------------------------------

def parse_utc(text):
    """Explicit UTC only ('...Z' or '+00:00'): publish.py parses every queue file with no error handling."""
    text = text.strip()
    if not (text.endswith("Z") or text.endswith("+00:00")):
        raise Refused(f"post time {text!r} must be explicit UTC, e.g. 2026-11-02T17:00:00Z")
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise Refused(f"post time {text!r} is not a valid date and time") from None
    return when.astimezone(timezone.utc).replace(microsecond=0)


def fmt_utc(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_hashtags(raw, caption=""):
    """'#a, b  #A' -> ['#a', '#b']; case-insensitive duplicates and tags already in the caption are dropped."""
    seen = {t.lower() for t in re.findall(r"#\w+", caption)}
    tags = []
    for word in re.split(r"[\s,]+", raw or ""):
        if not word:
            continue
        tag = "#" + word.lstrip("#")
        if not re.fullmatch(r"#\w+", tag):
            raise Refused(f"hashtag {word!r} has characters Instagram will not link")
        if tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    return tags


def build_entry(*, media_name, post_type, caption, hashtags, alt_text, cover_offset_ms, publish_at, approved):
    """Same key order and shape as the files in done/."""
    entry = {"publish_at": fmt_utc(publish_at), "type": post_type, "file": f"{MEDIA_DIR}/{media_name}"}
    if cover_offset_ms is not None:
        entry["thumb_offset_ms"] = cover_offset_ms
    body = caption.strip()
    if hashtags:
        body = (body + "\n\n" if body else "") + " ".join(hashtags)
    entry["caption"] = body
    if alt_text:
        entry["alt_text"] = alt_text.strip()
    entry["approved"] = approved.strip()
    return entry


# ---- the checks publish.py applies, plus the ones this task demands --------

def check_entry(entry, media_src, now):
    """Return (errors, warnings). Errors block the write."""
    errors, warnings = [], []
    post_type = entry["type"]
    if not entry.get("approved"):  # publish.run_due: SKIP when there is no owner approval
        errors.append("no approval words: the owner's own words must be recorded")
    elif not APPROVED_RE.search(json.dumps(entry, indent=1, ensure_ascii=False)):  # the pre-commit hook's own grep
        errors.append('approval does not pass the pre-commit check: the approved value must be quoted and contain "Owner" or "owner"')
    if not (media_src.is_file() and media_src.stat().st_size > 0):  # publish.run_due: FAIL when the file is missing
        errors.append(f"media file {media_src} is missing or empty")
    try:  # publish.container_params raises on an unknown type
        publish.container_params(entry, entry["file"])
    except ValueError as e:
        errors.append(str(e))
    if post_type != "STORIES" and not entry.get("caption"):
        errors.append("caption is empty")
    if entry.get("alt_text") and post_type != "IMAGE":
        errors.append("alt text only works on image feed posts; publish.py would ignore it")
    if "thumb_offset_ms" in entry and post_type != "REELS":
        errors.append("a cover offset only works on Reels; publish.py would ignore it")
    due = parse_utc(entry["publish_at"])
    if due < now + MIN_LEAD:
        errors.append(f"post time {entry['publish_at']} is too soon: it must be at least {int(MIN_LEAD.total_seconds() // 60)} "
                      "minutes from now, so the push and the Pages build finish and the margin holds")
    caption = entry.get("caption", "")
    if len(caption) > 2200:
        warnings.append(f"caption is {len(caption)} characters; Instagram allows 2200")
    if len(re.findall(r"#\w+", caption)) > 30:
        warnings.append("more than 30 hashtags; Instagram may reject the post")
    if len(entry.get("alt_text", "")) > 1000:
        warnings.append("alt text is over 1000 characters; Instagram may cut or reject it")
    if media_src.suffix.lower() == ".png":
        warnings.append("Instagram feed posts want JPEG; a PNG may be rejected")
    return errors, warnings


def resolve_type(media_src, requested):
    ext = media_src.suffix
    if ext != ext.lower():
        raise Refused(f"extension {ext!r} must be lowercase (publish.py tests for '.mp4' exactly)")
    if ext not in MEDIA_TYPES:
        raise Refused(f"unsupported media type {ext!r}; use one of {', '.join(sorted(MEDIA_TYPES))}")
    implied = MEDIA_TYPES[ext][0]
    if requested is None:
        return implied
    if requested == "STORIES":
        if ext == ".mov":
            raise Refused("a Story video must be .mp4: publish.py would send a .mov as an image")
        return "STORIES"
    if requested != implied:
        raise Refused(f"--type {requested} does not fit a {ext} file (that is {implied})")
    return requested


# ---- post_check ------------------------------------------------------------

def run_post_check(path, entry, media):
    """Run the house post_check script on a copy of the entry, with --media naming the real file (the copy sits
    in a temp folder, so the script would look for the media there). Any FAIL or non-zero exit raises Refused."""
    script = Path(path)
    if not script.is_file():
        raise Refused(f"post_check script {path} not found")
    cmd = [sys.executable, str(script)] if script.suffix == ".py" else [str(script)]
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "entry.json"
        probe.write_text(json.dumps(entry, indent=1, ensure_ascii=False), encoding="utf-8")
        try:
            r = subprocess.run(cmd + [str(probe), "--media", str(Path(media).resolve())], capture_output=True, text=True,
                               timeout=POST_CHECK_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Refused(f"post_check could not run: {e}") from None
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if out:
        print(out)
    if r.returncode != 0 or FAIL_ROW.search(out):
        raise Refused(f"post_check failed (exit {r.returncode})")
    print("POST_CHECK PASSED")


def check_margin(due, confirmed, margin):
    """The post time must be at least `margin` after the moment Pages was confirmed serving the media."""
    earliest = confirmed + margin
    if due < earliest:
        raise Refused(f"post time {fmt_utc(due)} is sooner than the confirmed fetch plus the margin "
                      f"({int(margin.total_seconds() // 60)} min, earliest {fmt_utc(earliest)}): "
                      "move the post time later (or remove the entry) before it falls due")
    return earliest


# ---- step 1: make ----------------------------------------------------------

def make(args, now=None, fetch_fn=None, sleep=time.sleep, clock=None):
    now = now or datetime.now(timezone.utc)
    clock = clock or (lambda: datetime.now(timezone.utc))
    fetch_fn = fetch_fn or fetch
    root = Path(args.pages_dir) if args.pages_dir else publish.ROOT
    queue = Path(args.queue_dir) if args.queue_dir else publish.ROOT / "queue"
    media_src = Path(args.media)
    if not SAFE_NAME.match(media_src.name):
        raise Refused(f"media name {media_src.name!r}: use only letters, digits, dot, dash and underscore")
    post_type = resolve_type(media_src, args.type)
    caption = Path(args.caption_file).read_text(encoding="utf-8") if args.caption_file else ""
    if args.cover_offset_ms is not None and args.cover_offset_ms < 0:
        raise Refused("cover offset must be zero or more milliseconds")
    entry = build_entry(
        media_name=media_src.name, post_type=post_type, caption=caption,
        hashtags=normalize_hashtags(args.hashtags, caption), alt_text=args.alt_text,
        cover_offset_ms=args.cover_offset_ms, publish_at=parse_utc(args.publish_at), approved=args.approved or "")
    errors, warnings = check_entry(entry, media_src, now)
    for w in warnings:
        print(f"WARN {w}")
    if errors:
        raise Refused("; ".join(errors))

    slug = args.slug or media_src.stem
    if not re.fullmatch(r"[A-Za-z0-9._-]+", slug):
        raise Refused(f"slug {slug!r}: use only letters, digits, dot, dash and underscore")
    due = parse_utc(entry["publish_at"])
    entry_path = queue / f"{due.strftime('%Y-%m-%dT%H%MZ')}-{slug}.json"
    media_dest = root / entry["file"]
    if entry_path.exists():
        raise Refused(f"{entry_path} already exists; not overwriting")
    if media_dest.exists() and media_dest.read_bytes() != media_src.read_bytes():
        raise Refused(f"{media_dest} already exists with different content; not overwriting")

    if args.no_post_check and args.write:
        raise Refused("--no-post-check cannot go with --write: a written entry must pass post_check. "
                      "Drop --no-post-check, or drop --write for a dry run")
    if args.no_post_check and args.post_check:
        raise Refused("--no-post-check and --post-check contradict each other")
    if args.no_post_check:
        print("POST_CHECK SKIPPED (--no-post-check given)")
    elif args.post_check:
        run_post_check(args.post_check, entry, media_src)
    else:
        print("POST_CHECK NOT RUN: no --post-check path and no POST_CHECK env var")
        if args.write:
            raise Refused("POST_CHECK NOT RUN: give --post-check PATH or set POST_CHECK; --write never skips post_check")
    if args.write and not args.pages_base:
        raise Refused("--write needs --pages-base (or the PAGES_BASE env var) to confirm Pages serves the media")
    if args.write and not args.pages_base.startswith("https://"):
        raise Refused("--pages-base must be an https address")

    text = json.dumps(entry, indent=1, ensure_ascii=False)
    print(f"{'WRITE' if args.write else 'DRY RUN'} entry {entry_path}\n{text}")
    print(f"media {media_src} -> {media_dest}")
    if not args.write:
        print("DRY RUN: nothing written, Pages not polled. Add --write to write.")
        return entry_path

    # Nothing touches disk until Pages serves the media (push it first if it is not there yet).
    url = media_url(args.pages_base, entry["file"])
    confirmed = wait_for_pages(url, MEDIA_TYPES[media_src.suffix][1], fetch_fn=fetch_fn, clock=clock, sleep=sleep,
                               every=args.poll_seconds, max_wait=timedelta(minutes=args.max_wait_minutes))
    print(f"CONFIRMED {url} answered 200 at {fmt_utc(confirmed)}")
    check_margin(due, confirmed, CONFIRM_MARGIN + timedelta(minutes=max(0, args.extra_margin_minutes)))

    copied = not media_dest.exists()
    media_dest.parent.mkdir(parents=True, exist_ok=True)
    entry_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if copied:
            shutil.copyfile(media_src, media_dest)
        entry_path.write_text(text, encoding="utf-8")
        readback = json.loads(entry_path.read_text(encoding="utf-8"))
        parse_utc(readback["publish_at"])  # the line publish.py will run on this file
        if not (root / readback["file"]).is_file():
            raise Refused(f"{readback['file']} is not under {root}")
    except Exception:
        entry_path.unlink(missing_ok=True)
        if copied:
            media_dest.unlink(missing_ok=True)
        raise
    print(f"WRITTEN {entry_path}. Next: push it; 'confirm {entry_path}' re-checks after the push.")
    return entry_path


# ---- step 2: confirm -------------------------------------------------------

def media_url(pages_base, file):
    """Built exactly as publish.publish builds it."""
    return urllib.parse.urljoin(pages_base.rstrip("/") + "/", urllib.parse.quote(file))


def fetch(url):
    """One GET; returns (status, content type). Network trouble is (None, None): not live yet."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as r:
            return r.status, r.headers.get("Content-Type")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type") if e.headers else None
    except (urllib.error.URLError, OSError):
        return None, None


def wait_for_pages(url, expected_type, *, fetch_fn=fetch, clock=None, sleep=time.sleep,
                   every=POLL_EVERY, max_wait=POLL_MAX):
    """Poll until 200 with the right content type; return the time it was confirmed."""
    clock = clock or (lambda: datetime.now(timezone.utc))
    started = clock()
    while True:
        status, ctype = fetch_fn(url)
        got = (ctype or "").split(";")[0].strip().lower()
        if status == 200 and got == expected_type:
            return clock()
        print(f"waiting: {status} {got or '-'} (want 200 {expected_type})", flush=True)
        if clock() - started >= max_wait:
            raise Refused(f"gave up after {int(max_wait.total_seconds() // 60)} minutes: {url} never answered 200 {expected_type}")
        sleep(every)


def confirm(args, clock=None, fetch_fn=fetch, sleep=time.sleep):
    clock = clock or (lambda: datetime.now(timezone.utc))
    entry = json.loads(Path(args.entry).read_text(encoding="utf-8"))
    if not args.pages_base:
        raise Refused("--pages-base is needed (or set the PAGES_BASE env var)")
    if not args.pages_base.startswith("https://"):
        raise Refused("--pages-base must be an https address")
    ext = Path(entry["file"]).suffix
    if ext not in MEDIA_TYPES:
        raise Refused(f"cannot tell the content type for {entry['file']}")
    margin = CONFIRM_MARGIN + timedelta(minutes=max(0, args.extra_margin_minutes))
    due = parse_utc(entry["publish_at"])
    url = media_url(args.pages_base, entry["file"])
    confirmed = wait_for_pages(url, MEDIA_TYPES[ext][1], fetch_fn=fetch_fn, clock=clock, sleep=sleep,
                               every=args.poll_seconds, max_wait=timedelta(minutes=args.max_wait_minutes))
    print(f"CONFIRMED {url} answered 200 {MEDIA_TYPES[ext][1]} at {fmt_utc(confirmed)}")
    earliest = check_margin(due, confirmed, margin)
    print(f"OK: due {entry['publish_at']} is at or after {fmt_utc(earliest)}")


# ---- command line ----------------------------------------------------------

def parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make", help="build and (with --write) write a queue entry")
    m.add_argument("--media", required=True, help="the image or video file")
    m.add_argument("--caption-file", help="text file with the caption")
    m.add_argument("--hashtags", default="", help='e.g. "#marblesculpture #newyorkcity"')
    m.add_argument("--alt-text", default="", help="image feed posts only")
    m.add_argument("--cover-offset-ms", type=int, help="Reels cover frame, in milliseconds")
    m.add_argument("--publish-at", required=True, help="UTC, e.g. 2026-11-02T17:00:00Z")
    m.add_argument("--approved", required=True, help="the owner's approval words, verbatim")
    m.add_argument("--type", choices=["IMAGE", "REELS", "STORIES"], help="default follows the file; use STORIES to make a Story")
    m.add_argument("--pages-dir", help="Pages folder; media is copied to <pages-dir>/media/ (default: the repo)")
    m.add_argument("--queue-dir", help="where the entry goes (default: the repo's queue/)")
    m.add_argument("--slug", help="name part of the entry file (default: the media name)")
    m.add_argument("--post-check", default=os.environ.get("POST_CHECK"), help="path to the house post_check script (default: POST_CHECK env var)")
    m.add_argument("--no-post-check", action="store_true", help="dry run only: skip post_check (refused together with --write)")
    m.add_argument("--pages-base", default=os.environ.get("PAGES_BASE"), help="https://<user>.github.io/<repo>/ (default: PAGES_BASE env var)")
    m.add_argument("--extra-margin-minutes", type=int, default=0, help="add to the margin; it cannot be lowered")
    m.add_argument("--poll-seconds", type=int, default=POLL_EVERY)
    m.add_argument("--max-wait-minutes", type=int, default=int(POLL_MAX.total_seconds() // 60))
    m.add_argument("--write", action="store_true", help="copy and write, but only after post_check passes and Pages serves the media; without it nothing changes")
    c = sub.add_parser("confirm", help="after the push: wait for Pages, then check the post time")
    c.add_argument("entry", help="the queue entry JSON")
    c.add_argument("--pages-base", default=os.environ.get("PAGES_BASE"), help="https://<user>.github.io/<repo>/ (default: PAGES_BASE env var)")
    c.add_argument("--extra-margin-minutes", type=int, default=0, help="add to the margin; it cannot be lowered")
    c.add_argument("--poll-seconds", type=int, default=POLL_EVERY)
    c.add_argument("--max-wait-minutes", type=int, default=int(POLL_MAX.total_seconds() // 60))
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        (make if args.cmd == "make" else confirm)(args)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
