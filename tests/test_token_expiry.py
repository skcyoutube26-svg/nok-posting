"""Mocked tests for token_expiry.py. No network, no real token. Run: python -m unittest discover -s tests"""
import io, json, os, sys, tempfile, unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import token_expiry as te

FAKE = "FAKE-TOKEN-abc123"
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
DAY = 86400


def opener_for(payload, seen=None):
    def opener(url, timeout=None):
        if seen is not None:
            seen.append(url)
        return io.StringIO(json.dumps(payload))
    return opener


def run(seconds, issue_file=None):
    out = io.StringIO()
    argv = ["--issue-file", issue_file] if issue_file else []
    payload = {"access_token": "NEW-" + FAKE, "token_type": "bearer", "expires_in": seconds}
    with redirect_stdout(out):
        days = te.main(argv, env={"IG_TOKEN": FAKE}, opener=opener_for(payload), now=NOW)
    return days, out.getvalue()


class Maths(unittest.TestCase):
    def test_days_floor(self):
        self.assertEqual(te.days_left(32 * DAY), 32)
        self.assertEqual(te.days_left(14 * DAY + 3600), 14)
        self.assertEqual(te.days_left(14 * DAY - 1), 13)
        self.assertEqual(te.days_left(0), 0)

    def test_expiry_date_26_nov(self):
        self.assertEqual(te.expiry_date(32 * DAY, NOW).isoformat(), "2026-11-26")


class Output(unittest.TestCase):
    def test_only_expires_line_and_no_token(self):
        days, out = run(30 * DAY)
        self.assertEqual(days, 30)
        self.assertEqual(out, "expires in 30 days\n")
        self.assertNotIn(FAKE, out)

    def test_no_issue_above_14_days(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "issue.txt")
            _, out = run(15 * DAY, f)
            self.assertFalse(os.path.exists(f))
            self.assertEqual(out, "expires in 15 days\n")

    def test_issue_at_14_days_and_text(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "issue.txt")
            _, out = run(14 * DAY, f)
            text = Path(f).read_text()
        title, body = text.split("\n", 1)
        self.assertEqual(title, te.TITLE)
        self.assertIn("expires in 14 days", body)
        self.assertIn("2026-11-08", body)
        self.assertIn("IG_TOKEN", body)  # the secret's name is fine; its value is not
        for blob in (text, out):
            self.assertNotIn(FAKE, blob)
            self.assertNotIn("NEW-", blob)

    def test_issue_text_direct(self):
        title, body = te.issue_text(12, 12 * DAY, NOW)
        self.assertIn("12 days", body)
        self.assertIn("2026-11-06", body)


class Failures(unittest.TestCase):
    def test_bad_answers_raise_without_leaking(self):
        for payload in ({}, {"expires_in": -5}, {"expires_in": "soon"}):
            with self.assertRaises(RuntimeError) as c:
                te.fetch_seconds_left(FAKE, opener_for(payload))
            self.assertNotIn(FAKE, str(c.exception))

    def test_network_error_does_not_leak(self):
        def boom(url, timeout=None):
            raise OSError(f"cannot reach {url}")  # url would carry the token
        with self.assertRaises(RuntimeError) as c:
            te.fetch_seconds_left(FAKE, boom)
        self.assertNotIn(FAKE, str(c.exception))

    def test_request_goes_to_refresh_endpoint(self):
        seen = []
        te.fetch_seconds_left(FAKE, opener_for({"expires_in": DAY}, seen))
        self.assertTrue(seen[0].startswith(te.URL + "?grant_type=ig_refresh_token"))


if __name__ == "__main__":
    unittest.main()
