"""Queue one post end to end: push the media, wait for Pages to serve it, then write and push the entry.
Never reads IG_TOKEN and never calls Instagram. Network use is limited to git, gh and one GET of the media URL.

Use:  python tools/queue_post.py --media photo.jpg --caption-file cap.txt --hashtags "#a #b" \\
          --alt-text "..." --publish-at 2026-11-02T17:00:00Z --approved "Owner, 1 Nov: ..." \\
          --post-check PATH [--dry-run]
      Accepts the same post options as tools/make_queue_entry.py make (--type, --cover-offset-ms, --slug,
      --pages-base, --extra-margin-minutes, --poll-seconds, --max-wait-minutes). --pages-dir/--queue-dir
      default to the repo, which must be a git checkout on --branch (default main) with a remote --remote
      (default origin). gh must be installed and signed in; --gh-repo OWNER/NAME overrides the repo gh uses.

The order, and nothing runs out of it (two Pages deploys never overlap):
  0. checks the post the way make_queue_entry does, post_check included. Any FAIL, or no post_check path, refuses
     before anything is copied, committed or pushed.
  1. waits until no pages-build-deployment run is queued or running.
  2. copies the media into media/, commits and pushes it.
  3. waits until the pages-build-deployment run for that commit has completed with success.
  4. waits until the media URL answers 200 with the right content type.
  5. runs make_queue_entry make --write (the post time must clear its margin after that confirmed fetch).
  6. commits and pushes the entry. This is the only push after the media one.
If step 3 or 4 times out, or anything before step 6 refuses, the entry is not written and not pushed. The media
may already be on the remote; run the same command again once Pages is healthy (an unchanged media file is
not committed twice).

--dry-run prints the plan and the entry, changes nothing, and does not touch git, gh or the network.
Exit codes: 0 ok, 2 refused, a step failed, or Pages never confirmed.
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_queue_entry as mqe  # noqa: E402
from make_queue_entry import Refused  # noqa: E402

WORKFLOW = "pages-build-deployment"
RUN_POLL = 15                      # seconds between gh checks
RUN_MAX = timedelta(minutes=20)    # how long to wait for a deploy run
CMD_TIMEOUT = 300                  # seconds for one git or gh call


def run_cmd(cmd):
    """Run one command; returns (exit code, stdout, stderr). A missing program is Refused."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=CMD_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Refused(f"could not run {cmd[0]}: {e}") from None
    return r.returncode, r.stdout or "", r.stderr or ""


class Git:
    def __init__(self, root, run, remote, branch, gh_repo=None):
        self.root, self.run, self.remote, self.branch, self.gh_repo = Path(root), run, remote, branch, gh_repo

    def git(self, *a, ok=(0,)):
        code, out, err = self.run(["git", "-C", str(self.root), *a])
        if code not in ok:
            raise Refused(f"git {a[0]} failed (exit {code}): {(err or out).strip()}")
        return out.strip()

    def current_branch(self):
        return self.git("rev-parse", "--abbrev-ref", "HEAD")

    def head(self):
        return self.git("rev-parse", "HEAD")

    def changed(self, path):
        return bool(self.git("status", "--porcelain", "--", str(path)))

    def commit_push(self, paths, message):
        """Add and commit just `paths` (if they changed), then push. Returns the HEAD sha."""
        names = [str(p) for p in paths]
        self.git("add", "--", *names)
        if self.git("diff", "--cached", "--name-only", "--", *names):
            self.git("commit", "-m", message, "--", *names)
        else:
            print(f"nothing new to commit for {', '.join(names)}")
        self.git("push", self.remote, f"HEAD:{self.branch}")
        return self.head()

    def runs(self):
        cmd = ["gh", "run", "list", "--workflow", WORKFLOW, "--limit", "20",
               "--json", "databaseId,status,conclusion,headSha"]
        if self.gh_repo:
            cmd += ["--repo", self.gh_repo]
        code, out, err = self.run(cmd)
        if code != 0:
            raise Refused(f"gh run list failed (exit {code}): {(err or out).strip()}")
        try:
            return json.loads(out or "[]")
        except ValueError:
            raise Refused("gh run list did not return JSON") from None


def poll(check, what, *, clock, sleep, every, max_wait):
    """Call check() until it returns something other than None; Refused after max_wait."""
    started = clock()
    while True:
        found = check()
        if found is not None:
            return found
        if clock() - started >= max_wait:
            raise Refused(f"gave up after {int(max_wait.total_seconds() // 60)} minutes waiting for {what}")
        print(f"waiting for {what}", flush=True)
        sleep(every)


def wait_idle(g, **kw):
    """No Pages deploy may be queued or running before we push."""
    def check():
        busy = [r for r in g.runs() if r.get("status") != "completed"]
        return True if not busy else None
    poll(check, "the running Pages deploy to finish", **kw)


def wait_run(g, sha, **kw):
    """The pages-build-deployment run for this commit must complete with success."""
    def check():
        for r in g.runs():
            if r.get("headSha") == sha and r.get("status") == "completed":
                return r
        return None
    r = poll(check, f"the Pages deploy for {sha[:7]}", **kw)
    if r.get("conclusion") != "success":
        raise Refused(f"the Pages deploy for {sha[:7]} finished with {r.get('conclusion')!r}, not success")
    return r


# ---- plan ------------------------------------------------------------------

def make_argv(args, write):
    """The same options, handed to make_queue_entry's own parser so its checks and defaults apply."""
    argv = ["make", "--media", args.media, "--publish-at", args.publish_at, "--approved", args.approved,
            "--poll-seconds", str(args.poll_seconds), "--max-wait-minutes", str(args.max_wait_minutes),
            "--extra-margin-minutes", str(args.extra_margin_minutes)]
    for flag, value in (("--caption-file", args.caption_file), ("--hashtags", args.hashtags), ("--alt-text", args.alt_text),
                        ("--cover-offset-ms", args.cover_offset_ms), ("--type", args.type), ("--slug", args.slug),
                        ("--pages-dir", args.pages_dir), ("--queue-dir", args.queue_dir),
                        ("--post-check", args.post_check), ("--pages-base", args.pages_base)):
        if value not in (None, ""):
            argv += [flag, str(value)]
    if write:
        argv.append("--write")
    return argv


def queue_post(args, *, run=run_cmd, fetch_fn=None, sleep=time.sleep, clock=None, now=None):
    clock = clock or (lambda: datetime.now(timezone.utc))
    fetch_fn = fetch_fn or mqe.fetch
    parsed = mqe.parser().parse_args(make_argv(args, write=False))
    if parsed.no_post_check or not parsed.post_check:
        raise Refused("queue_post always runs post_check: give --post-check PATH or set the POST_CHECK env var")
    # Step 0. Dry run of make: entry checks, post_check (FAIL refuses), file clashes. Writes nothing.
    entry_path = mqe.make(parsed, now=now)
    pages_base = mqe.resolve_pages_base(parsed)
    if not pages_base or not pages_base.startswith("https://"):
        raise Refused("an https Pages base is needed (--pages-base, PAGES_BASE env var, or pages_config.json)")
    root = Path(args.pages_dir) if args.pages_dir else mqe.publish.ROOT
    media_src = Path(args.media)
    media_dest = root / mqe.MEDIA_DIR / media_src.name
    entry_rel = Path(entry_path).name

    plan = [f"wait until no {WORKFLOW} run is active",
            f"copy {media_src} -> {media_dest}, commit and push to {args.remote}/{args.branch}",
            f"wait for the {WORKFLOW} run of that commit to complete with success",
            f"wait for {mqe.media_url(pages_base, mqe.MEDIA_DIR + '/' + media_src.name)} to answer 200 "
            f"{mqe.MEDIA_TYPES[media_src.suffix][1]}",
            f"run make_queue_entry make --write -> {entry_path}",
            f"commit and push the entry ({entry_rel}) to {args.remote}/{args.branch}"]
    print("PLAN" + (" (dry run)" if args.dry_run else ""))
    for i, step in enumerate(plan, 1):
        print(f" {i}. {step}")
    if args.dry_run:
        print("DRY RUN: nothing copied, committed, pushed or polled.")
        return entry_path

    g = Git(root, run, args.remote, args.branch, args.gh_repo)
    if g.current_branch() != args.branch:
        raise Refused(f"the repo is on {g.current_branch()!r}, not {args.branch!r}: Pages deploys from {args.branch}")
    wait = dict(clock=clock, sleep=sleep, every=args.run_poll_seconds, max_wait=timedelta(minutes=args.run_max_minutes))

    wait_idle(g, **wait)
    media_dest.parent.mkdir(parents=True, exist_ok=True)
    if not media_dest.exists():
        shutil.copyfile(media_src, media_dest)
    sha = g.commit_push([media_dest], f"media: {media_src.name}")
    print(f"PUSHED media {media_src.name} in {sha[:7]}")
    wait_run(g, sha, **wait)
    print(f"DEPLOYED {sha[:7]}")
    url = mqe.media_url(pages_base, mqe.MEDIA_DIR + "/" + media_src.name)
    confirmed = mqe.wait_for_pages(url, mqe.MEDIA_TYPES[media_src.suffix][1], fetch_fn=fetch_fn, clock=clock, sleep=sleep,
                                   every=args.poll_seconds, max_wait=timedelta(minutes=args.max_wait_minutes))
    print(f"CONFIRMED {url} answered 200 at {mqe.fmt_utc(confirmed)}")

    written = mqe.make(mqe.parser().parse_args(make_argv(args, write=True)), now=now, fetch_fn=fetch_fn,
                       sleep=sleep, clock=clock)
    sha = g.commit_push([Path(written)], f"queue: {media_src.stem} {mqe.fmt_utc(mqe.parse_utc(parsed.publish_at))}")
    print(f"PUSHED entry {entry_rel} in {sha[:7]}")
    return written


def parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--media", required=True, help="the image or video file")
    p.add_argument("--caption-file", help="text file with the caption")
    p.add_argument("--hashtags", default="", help='e.g. "#marblesculpture #newyorkcity"')
    p.add_argument("--alt-text", default="", help="image feed posts only")
    p.add_argument("--cover-offset-ms", type=int, help="Reels cover frame, in milliseconds")
    p.add_argument("--publish-at", required=True, help="UTC, e.g. 2026-11-02T17:00:00Z")
    p.add_argument("--approved", required=True, help="the owner's approval words, verbatim")
    p.add_argument("--type", choices=["IMAGE", "REELS", "STORIES"], help="default follows the file")
    p.add_argument("--pages-dir", help="repo folder to commit in (default: this repo)")
    p.add_argument("--queue-dir", help="where the entry goes (default: the repo's queue/)")
    p.add_argument("--slug", help="name part of the entry file (default: the media name)")
    p.add_argument("--post-check", default=mqe.os.environ.get("POST_CHECK"), help="path to the house post_check script (default: POST_CHECK env var)")
    p.add_argument("--pages-base", default=mqe.os.environ.get("PAGES_BASE"), help="https://<user>.github.io/<repo>/ (default: PAGES_BASE env var, then pages_config.json)")
    p.add_argument("--extra-margin-minutes", type=int, default=0, help="add to the margin; it cannot be lowered")
    p.add_argument("--poll-seconds", type=int, default=mqe.POLL_EVERY, help="seconds between media URL checks")
    p.add_argument("--max-wait-minutes", type=int, default=int(mqe.POLL_MAX.total_seconds() // 60), help="media URL wait limit")
    p.add_argument("--run-poll-seconds", type=int, default=RUN_POLL, help="seconds between gh run checks")
    p.add_argument("--run-max-minutes", type=int, default=int(RUN_MAX.total_seconds() // 60), help="deploy run wait limit")
    p.add_argument("--remote", default="origin", help="git remote to push to (default origin)")
    p.add_argument("--branch", default="main", help="branch Pages deploys from (default main)")
    p.add_argument("--gh-repo", help="OWNER/NAME for gh (default: the checkout's own repo)")
    p.add_argument("--dry-run", action="store_true", help="print the plan and change nothing")
    return p


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        queue_post(args)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
