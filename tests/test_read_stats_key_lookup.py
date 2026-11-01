"""tools/read_stats.sh key lookup: $STATS_KEY first, then ../.stats-key.pem above the repo, else exit 2.
Runs a copy of the script in a throwaway layout with fake gh/openssl on PATH. Fake key only; no network."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "read_stats.sh"
SECRET = "FAKE-KEY-SECRET-DO-NOT-PRINT"
SH = shutil.which("sh")

pytestmark = pytest.mark.skipif(SH is None, reason="no sh available")

FAKE_GH = """#!/bin/sh
case "$*" in
  *"run view"*) echo "KEY:QUJD"; echo "DATA:QUJD" ;;
  *"run list"*) echo 1 ;;
esac
"""
FAKE_OPENSSL = """#!/bin/sh
case "$1 $2" in
  "pkeyutl -decrypt")
    while [ $# -gt 0 ]; do
      [ "$1" = -inkey ] && echo "$2" >> "$USED_LOG"
      [ "$1" = -out ] && echo x > "$2"
      shift
    done ;;
  *) echo '{"ok": 1}' ;;
esac
"""


@pytest.fixture
def layout(tmp_path):
    parent = tmp_path / "parent"
    repo = parent / "repo"
    (repo / "tools").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "tools" / "read_stats.sh")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in (("gh", FAKE_GH), ("openssl", FAKE_OPENSSL)):
        (bin_ / name).write_text(body, newline="\n")
        (bin_ / name).chmod(0o755)
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    log = tmp_path / "used.log"
    log.write_text("")

    def run(stats_key=None):
        env = dict(os.environ, PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}",
                   RUN="1", TMPDIR=str(tmp), USED_LOG=str(log))
        env.pop("STATS_KEY", None)
        if stats_key is not None:
            env["STATS_KEY"] = str(stats_key)
        p = subprocess.run([SH, str(repo / "tools" / "read_stats.sh")], env=env,
                           capture_output=True, text=True)
        return p, log.read_text().split(), tmp

    run.parent, run.repo = parent, repo
    return run


def fake_key(path):
    path.write_text(SECRET)
    return path


def test_env_key_wins_over_parent(layout, tmp_path):
    parent_key = fake_key(layout.parent / ".stats-key.pem")
    env_key = fake_key(tmp_path / "elsewhere.pem")
    p, used, _ = layout(stats_key=env_key)
    assert p.returncode == 0, p.stderr
    assert [Path(u) for u in used] == [env_key]
    assert parent_key.exists() and '"ok"' in p.stdout


def test_falls_back_to_parent_of_repo(layout):
    parent_key = fake_key(layout.parent / ".stats-key.pem")
    p, used, _ = layout()
    assert p.returncode == 0, p.stderr
    assert [Path(u).resolve() for u in used] == [parent_key.resolve()]


def test_key_inside_repo_is_not_used(layout):
    fake_key(layout.repo / ".stats-key.pem")
    p, used, _ = layout()
    assert p.returncode == 2 and used == []


def test_no_key_exit_2_names_both_paths(layout, tmp_path):
    missing = tmp_path / "nope.pem"
    p, used, _ = layout(stats_key=missing)
    assert p.returncode == 2
    assert str(missing) in p.stderr and ".stats-key.pem" in p.stderr
    assert used == [] and p.stdout == ""


def test_never_prints_key_content(layout):
    fake_key(layout.parent / ".stats-key.pem")
    p, _, _ = layout()
    assert SECRET not in p.stdout and SECRET not in p.stderr


def test_temp_files_under_tmpdir_and_cleaned(layout):
    fake_key(layout.parent / ".stats-key.pem")
    p, _, tmp = layout()
    assert p.returncode == 0
    assert list(tmp.iterdir()) == []
    assert not (layout.repo / "insights").exists()
