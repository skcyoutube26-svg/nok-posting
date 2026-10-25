"""Read how long the Instagram token has left and write a warning issue body when it is 14 days or fewer.
Uses Meta's refresh endpoint, which answers with expires_in (seconds). Only that number is read; the
token string in the answer is dropped at once, and nothing about the answer is ever printed.
Prints one line: "expires in N days". Writes the issue title/body to files only when a warning is due.
Run: IG_TOKEN=... python token_expiry.py [--issue-file FILE]
"""
import json, os, sys, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

URL = "https://graph.instagram.com/refresh_access_token"
WARN_DAYS = 14
TITLE = "Instagram token expires soon: re-issue it"


def fetch_seconds_left(token, opener=urllib.request.urlopen):
    """Seconds until the token dies. Raises RuntimeError without any token or response text."""
    q = urllib.parse.urlencode({"grant_type": "ig_refresh_token", "access_token": token})
    try:
        with opener(f"{URL}?{q}", timeout=60) as r:
            seconds = json.load(r)["expires_in"]
    except Exception as e:
        raise RuntimeError(f"expiry check failed ({type(e).__name__})") from None
    if not isinstance(seconds, (int, float)) or seconds < 0:
        raise RuntimeError("expiry check failed (no usable expires_in)")
    return seconds


def days_left(seconds):
    return int(seconds // 86400)


def expiry_date(seconds, now):
    return (now + timedelta(seconds=seconds)).date()


def issue_text(days, seconds, now):
    """Title and body for the warning issue. Never contains the token."""
    when = expiry_date(seconds, now).isoformat()
    body = (
        f"The Instagram token expires in {days} days (about {when}).\n\n"
        "After that, publishing, replies and DMs stop working.\n\n"
        "To fix it, before that date:\n"
        "1. Make a fresh token in the Meta app dashboard.\n"
        "2. Replace the `IG_TOKEN` secret in this repo's Actions secrets with it.\n"
        "3. Run the `token expiry` workflow by hand; it should say a larger number of days.\n\n"
        "Opened automatically by the token expiry workflow. This issue never contains the token."
    )
    return TITLE, body


def main(argv, env=os.environ, opener=urllib.request.urlopen, now=None):
    now = now or datetime.now(timezone.utc)
    issue_file = argv[argv.index("--issue-file") + 1] if "--issue-file" in argv else None
    seconds = fetch_seconds_left(env["IG_TOKEN"], opener)
    days = days_left(seconds)
    print(f"expires in {days} days")
    if days <= WARN_DAYS and issue_file:
        title, body = issue_text(days, seconds, now)
        with open(issue_file, "w") as f:
            f.write(f"{title}\n{body}\n")
        print("warning due")
    return days


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except RuntimeError as e:
        print(e)
        sys.exit(1)
