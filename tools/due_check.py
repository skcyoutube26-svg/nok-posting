"""Read-only overdue check for the posting queue.
Local only: reads queue/*.json and lists done/ by name; never touches the network, publish.py,
the queue or any key, and writes nothing.

Use:  python tools/due_check.py
      python tools/due_check.py --root path/to/repo-copy --grace 10

A queue entry is OVERDUE when its publish_at is more than --grace minutes (default 10) in the
past and done/ holds no record with the same file name (publish.py moves a posted entry to done/
under the same name). One line per entry, oldest first:
  OVERDUE <entry> due <publish_at> late <minutes>

Exit codes: 0 nothing overdue, 3 at least one overdue entry, 2 an entry cannot be read
(printed as ERROR on stderr; overdue lines are still printed, and 3 wins if both happen).
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

GRACE_MIN = 10
ROOT = Path(__file__).resolve().parent.parent


def parse_time(s):
    t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def find_overdue(root, now, grace=GRACE_MIN):
    """(overdue, errors): overdue is a list of (name, publish_at, late_minutes) oldest due first."""
    root = Path(root)
    done = {f.name for f in (root / "done").glob("*.json")}
    overdue, errors = [], []
    for f in sorted((root / "queue").glob("*.json")):
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))["publish_at"]
            due = parse_time(raw)
        except (OSError, ValueError, KeyError, TypeError) as e:
            errors.append((f.name, e))
            continue
        late = (now - due).total_seconds() / 60
        if late > grace and f.name not in done:
            overdue.append((f.name, raw, int(late), due))
    overdue.sort(key=lambda r: (r[3], r[0]))
    return [r[:3] for r in overdue], errors


def main(argv=None, now=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(ROOT), help="folder holding queue/ and done/ (default: this repo)")
    ap.add_argument("--grace", type=float, default=GRACE_MIN, help="minutes of lateness allowed (default 10)")
    args = ap.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    overdue, errors = find_overdue(args.root, now, args.grace)
    for name, raw, late in overdue:
        print(f"OVERDUE {name} due {raw} late {late}")
    for name, e in errors:
        print(f"ERROR {name}: cannot read publish_at ({e})", file=sys.stderr)
    return 3 if overdue else 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
