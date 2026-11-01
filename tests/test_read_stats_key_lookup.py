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
      case "$1" in -in|-out|-pass) echo "$2" >> "$ARGS_LOG" ;; esac
      shift
    done ;;
  *)
    while [ $# -gt 0 ]; do
      case "$1" in -in|-pass) echo "$2" >> "$ARGS_LOG" ;; esac
      shift
    done
    echo '{"ok": 1}' ;;
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
    args_log = tmp_path / "args.log"
    args_log.write_text("")

    def run(stats_key=None):
        env = dict(os.environ, PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}",
                   RUN="1", TMPDIR=str(tmp), USED_LOG=str(log), ARGS_LOG=str(args_log))
        env.pop("STATS_KEY", None)
        if stats_key is not None:
            env["STATS_KEY"] = str(stats_key)
        p = subprocess.run([SH, str(repo / "tools" / "read_stats.sh")], env=env,
                           capture_output=True, text=True)
        return p, log.read_text().split(), tmp

    run.parent, run.repo, run.args_log = parent, repo, args_log
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


def test_openssl_gets_relative_temp_paths(layout):
    """Windows native openssl cannot open POSIX temp paths, so temp files must be passed relative."""
    fake_key(layout.parent / ".stats-key.pem")
    p, _, _ = layout()
    assert p.returncode == 0, p.stderr
    args = layout.args_log.read_text().split()
    assert args, "openssl was never given temp files"
    for a in args:
        a = a.removeprefix("file:")
        assert not a.startswith("/") and ":" not in a and "\\" not in a, a


def test_relative_stats_key_still_works(layout, tmp_path):
    fake_key(tmp_path / "rel.pem")
    env = dict(os.environ, PATH=f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}", RUN="1",
               TMPDIR=str(tmp_path / "tmp"), USED_LOG=str(tmp_path / "used.log"),
               ARGS_LOG=str(layout.args_log), STATS_KEY="rel.pem")
    p = subprocess.run([SH, str(layout.repo / "tools" / "read_stats.sh")], env=env, cwd=tmp_path,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl")
def test_real_openssl_roundtrip_with_throwaway_key(layout, tmp_path):
    """Real openssl, throwaway key generated here (never a real key)."""
    import base64
    work = tmp_path / "rt"
    work.mkdir()
    kw = dict(cwd=work, check=True, capture_output=True)
    subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                    "-out", "priv.pem"], **kw)
    subprocess.run(["openssl", "pkey", "-in", "priv.pem", "-pubout", "-out", "pub.pem"], **kw)
    (work / "sym").write_bytes(b"sym-secret-1234567890")
    (work / "plain").write_text('{"real": 1}')
    subprocess.run(["openssl", "pkeyutl", "-encrypt", "-pubin", "-inkey", "pub.pem", "-in", "sym",
                    "-out", "sym.enc"], **kw)
    subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-in", "plain", "-out", "d.enc",
                    "-pass", "file:sym"], **kw)
    b = lambda n: base64.b64encode((work / n).read_bytes()).decode()
    gh = tmp_path / "bin" / "gh"
    gh.write_text(f'#!/bin/sh\ncase "$*" in *"run view"*) echo "KEY:{b("sym.enc")}"; '
                  f'echo "DATA:{b("d.enc")}" ;; *"run list"*) echo 1 ;; esac\n', newline="\n")
    (tmp_path / "bin" / "openssl").unlink()
    key = work / "priv.pem"
    env = dict(os.environ, PATH=f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}", RUN="1",
               TMPDIR=str(tmp_path / "tmp"), STATS_KEY=str(key))
    p = subprocess.run([SH, str(layout.repo / "tools" / "read_stats.sh")], env=env,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == '{"real": 1}'
