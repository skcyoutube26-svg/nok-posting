"""due_check on a throwaway queue/ and done/ under tmp_path. No network, nothing outside tmp_path."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import due_check as dc  # noqa: E402

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def tree(tmp_path):
    for d in ("queue", "done"):
        (tmp_path / d).mkdir()
    return tmp_path


def put(tree, folder, name, publish_at):
    (tree / folder / name).write_text(json.dumps({"publish_at": publish_at}), encoding="utf-8")


def run(tree, capsys):
    code = dc.main(["--root", str(tree)], now=NOW)
    return code, capsys.readouterr()


def test_on_time_overdue_and_done(tree, capsys):
    put(tree, "queue", "a-future.json", "2026-11-01T13:00:00Z")
    put(tree, "queue", "b-late.json", "2026-11-01T11:30:00Z")
    put(tree, "queue", "c-posted.json", "2026-11-01T09:00:00Z")
    put(tree, "done", "c-posted.json", "2026-11-01T09:00:00Z")
    code, out = run(tree, capsys)
    assert code == 3
    assert out.out == "OVERDUE b-late.json due 2026-11-01T11:30:00Z late 30\n"


def test_nothing_overdue(tree, capsys):
    put(tree, "queue", "a.json", "2026-11-01T12:30:00Z")
    code, out = run(tree, capsys)
    assert (code, out.out) == (0, "")


def test_grace_edge(tree, capsys):
    put(tree, "queue", "ten.json", "2026-11-01T11:50:00Z")  # exactly 10 min: not yet overdue
    assert run(tree, capsys)[0] == 0
    put(tree, "queue", "eleven.json", "2026-11-01T11:49:00Z")
    code, out = run(tree, capsys)
    assert code == 3 and "eleven.json" in out.out and "ten.json" not in out.out


def test_read_only(tree, capsys):
    put(tree, "queue", "b.json", "2026-11-01T11:00:00Z")
    before = sorted(p.name for p in tree.rglob("*"))
    run(tree, capsys)
    assert sorted(p.name for p in tree.rglob("*")) == before


def test_unreadable_entry_exit_2(tree, capsys):
    (tree / "queue" / "bad.json").write_text("{", encoding="utf-8")
    code, out = run(tree, capsys)
    assert code == 2 and "ERROR bad.json" in out.err
