"""Tests for the weekly refresh-token job in publish.yml. No network, no real token: the job's own shell
steps run against fake curl and gh programs. Run: python -m unittest discover -s tests"""
import os, stat, subprocess, tempfile, unittest
from pathlib import Path
import yaml

WF = Path(__file__).parent.parent / ".github" / "workflows" / "publish.yml"
FAKE = "FAKE-TOKEN-abc123"
JOB = yaml.safe_load(WF.read_text())["jobs"]["refresh-token"]
REFRESH = next(s for s in JOB["steps"] if s["name"] == "Refresh token")["run"]
ALERT = next(s for s in JOB["steps"] if s["name"] == "Open issue if refresh failed")["run"]


def fake_bin(d, curl_exit, open_issue=""):
    """curl exits curl_exit; gh logs its args and answers an issue-list with open_issue."""
    b = Path(d, "bin"); b.mkdir()
    (b / "curl").write_text(f'#!/bin/sh\necho "$@" >> "{d}/curl.log"\nexit {curl_exit}\n')
    (b / "gh").write_text(
        f'#!/bin/sh\necho "$@" >> "{d}/gh.log"\n'
        f'if [ "$2" = list ]; then echo "{open_issue}"; fi\n'
        f'if [ "$2" = create ]; then cp "$6" "{d}/created_body.txt"; fi\n')
    for f in b.iterdir():
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return b


def sh(script, d, bin_dir, **extra):
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "RUNNER_TEMP": d, "IG_TOKEN": FAKE,
           "RUN_URL": "https://github.com/o/r/actions/runs/1", **extra}
    return subprocess.run(["bash", "-e", "-c", script], env=env, capture_output=True, text=True)


class Workflow(unittest.TestCase):
    def test_job_wiring(self):
        self.assertEqual(JOB["if"], "github.event.schedule == '0 3 * * 1'")
        self.assertEqual(JOB["permissions"], {"contents": "read", "issues": "write"})
        alert = next(s for s in JOB["steps"] if s["name"] == "Open issue if refresh failed")
        self.assertEqual(alert["if"], "failure()")
        self.assertNotIn("secrets.IG_TOKEN", REFRESH)  # only via env, never inline in the command
        self.assertNotIn("${{ secrets", ALERT)

    def test_no_new_schedule_and_no_token_expiry_workflow(self):
        on = yaml.safe_load(WF.read_text())[True]
        self.assertEqual([c["cron"] for c in on["schedule"]], ["*/10 * * * *", "0 3 * * 1"])
        self.assertFalse((WF.parent / "token-expiry.yml").exists())


class RefreshFailure(unittest.TestCase):
    def test_refresh_success_exits_zero(self):
        with tempfile.TemporaryDirectory() as d:
            r = sh(REFRESH, d, fake_bin(d, 0))
        self.assertEqual(r.returncode, 0)

    def test_refresh_http_error_fails_the_step(self):
        with tempfile.TemporaryDirectory() as d:  # curl -f exits 22 on an HTTP error answer
            r = sh(REFRESH, d, fake_bin(d, 22))
            self.assertEqual(r.returncode, 22)
            self.assertIn("-sf", Path(d, "curl.log").read_text())

    def test_refresh_network_error_fails_the_step(self):
        with tempfile.TemporaryDirectory() as d:  # curl exit 6 = could not resolve host
            self.assertEqual(sh(REFRESH, d, fake_bin(d, 6)).returncode, 6)

    def test_no_token_in_output_on_failure(self):
        with tempfile.TemporaryDirectory() as d:
            r = sh(REFRESH, d, fake_bin(d, 22))
        self.assertNotIn(FAKE, r.stdout.replace("::add-mask::" + FAKE, ""))
        self.assertNotIn(FAKE, r.stderr)


class Alert(unittest.TestCase):
    def test_opens_issue_with_run_link_and_no_token(self):
        with tempfile.TemporaryDirectory() as d:
            r = sh(ALERT, d, fake_bin(d, 0), IG_TOKEN="")
            self.assertEqual(r.returncode, 0, r.stderr)
            gh = Path(d, "gh.log").read_text()
            body = Path(d, "created_body.txt").read_text()
        self.assertIn("issue create --title Instagram token refresh failed", gh)
        self.assertIn("https://github.com/o/r/actions/runs/1", body)
        self.assertIn("IG_TOKEN", body)  # the secret's name is fine
        for blob in (gh, body, r.stdout, r.stderr):
            self.assertNotIn(FAKE, blob)

    def test_alert_step_never_sees_the_token(self):
        self.assertNotIn("IG_TOKEN", yaml.safe_load(WF.read_text())["jobs"]["refresh-token"]["steps"][1].get("env", {}))

    def test_no_duplicate_when_issue_already_open(self):
        with tempfile.TemporaryDirectory() as d:
            r = sh(ALERT, d, fake_bin(d, 0, open_issue="7"), IG_TOKEN="")
            self.assertEqual(r.returncode, 0)
            self.assertNotIn("create", Path(d, "gh.log").read_text())


if __name__ == "__main__":
    unittest.main()
