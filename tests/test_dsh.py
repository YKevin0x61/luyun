"""dsh 状态行工具的端到端测试（纯离线，不依赖数据库/网络）。

被测对象是仓库根目录的 ``dsh`` 脚本。测试全部用 subprocess 调用，覆盖：
帮助与退出码、默认 status 输出结构、goal 创建/只读输出/不覆盖/标记完成/
活动记录、stdin 创建、config 忽略标记、以及 status 不写文件。

注意：测试不 import 项目模块，因此不受 config/数据库影响。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DSH = REPO_ROOT / "dsh"
BASH = shutil.which("bash") or "/bin/bash"


def run(*args: str, cwd: Path | None = None, stdin: str | None = None, global_config: Path | None = None):
    """运行 dsh，返回 CompletedProcess（text 模式）。

    显式钉死 global config 路径，避免测试受开发机 ~/.config/dsh 影响。
    """
    env = dict(os.environ)
    if global_config is not None:
        env["DSH_GLOBAL_CONFIG"] = str(global_config)
    else:
        env["DSH_GLOBAL_CONFIG"] = str(Path(os.environ.get("TMPDIR", "/tmp")) / "dsh-test-none" / "config.json")
    return subprocess.run(
        [BASH, str(DSH), *args],
        cwd=str(cwd) if cwd else None,
        input=stdin,
        text=True,
        capture_output=True,
        env=env,
        timeout=60,
    )


def test_help_and_unknown_arguments(tmp_path: Path) -> None:
    r = run("--help", cwd=tmp_path)
    assert r.returncode == 0
    assert "Usage:" in r.stdout
    assert "Commands:" in r.stdout

    r = run("-h", cwd=tmp_path)
    assert r.returncode == 0

    # 未知参数与未知子命令都必须打印帮助并返回 2
    for bad in (("--nope",), ("nope",)):
        r = run(*bad, cwd=tmp_path)
        assert r.returncode == 2, bad
        assert "Usage:" in r.stdout


def test_no_args_without_tty_prints_help(tmp_path: Path) -> None:
    # pytest 的 stdin 不是 tty，命中「无参数 + 非 tty -> 帮助」分支
    r = run(cwd=tmp_path, stdin="")
    assert r.returncode == 0
    assert "Usage:" in r.stdout


def test_status_structure_and_read_only(tmp_path: Path) -> None:
    r = run("status", cwd=tmp_path)
    assert r.returncode == 0
    out = r.stdout
    assert f"Workspace: {tmp_path.resolve()}" in out
    assert "Diagnostics:" in out
    assert "Config:" in out
    assert "No goal is currently set." in out
    # status 只读：不应创建状态目录
    assert not (tmp_path / ".dsh").exists()


def test_goal_create_json_and_status_summary(tmp_path: Path) -> None:
    objective = (
        "Ship the report. Must keep the scope small.\n"
        "- [ ] write tests\n"
        "- [x] read the code"
    )
    r = run("goal", objective, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "Goal created." in r.stdout
    assert (tmp_path / ".dsh" / "goal").is_file()

    r = run("goal", cwd=tmp_path)
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["status"] == "active"
    assert len(data["tasks"]) == 2
    assert data["tasks"][0]["done"] is False
    assert data["tasks"][1]["done"] is True
    assert any("Must keep the scope small." == c for c in data["constraints"])

    r = run("status", cwd=tmp_path)
    assert "Goal Summary:" in r.stdout
    assert "Tasks: 2 total, 1 done, 1 remaining" in r.stdout
    assert "Progress: 50%" in r.stdout
    assert "Remaining Work:" in r.stdout
    assert "- write tests" in r.stdout
    assert "Important constraints/preferences:" in r.stdout
    assert "Recent goal activity:" in r.stdout

    # 输出确定：两次 status 完全一致
    assert run("status", cwd=tmp_path).stdout == r.stdout


def test_goal_is_never_overwritten(tmp_path: Path) -> None:
    assert run("goal", "first objective", cwd=tmp_path).returncode == 0
    state = (tmp_path / ".dsh" / "goal").read_bytes()

    r = run("goal", "second objective", cwd=tmp_path)
    assert r.returncode == 0
    assert "not overwritten" in r.stdout
    assert (tmp_path / ".dsh" / "goal").read_bytes() == state

    # 无参数只读输出同样不改文件
    assert run("goal", cwd=tmp_path).returncode == 0
    assert (tmp_path / ".dsh" / "goal").read_bytes() == state


def test_goal_from_stdin_and_done(tmp_path: Path) -> None:
    r = run("goal", "-", cwd=tmp_path, stdin="Fix the login bug\n")
    assert r.returncode == 0, r.stderr
    assert "Goal created." in r.stdout
    data = json.loads(run("goal", cwd=tmp_path).stdout)
    assert "login bug" in data["objective"]

    r = run("done", cwd=tmp_path)
    assert r.returncode == 0
    assert "completed" in r.stdout
    assert json.loads(run("goal", cwd=tmp_path).stdout)["status"] == "completed"

    # 幂等：再次 done 不改状态
    r = run("done", cwd=tmp_path)
    assert r.returncode == 0
    assert "already completed" in r.stdout

    # note 追加活动记录
    assert run("note", "blocked on config", cwd=tmp_path).returncode == 0
    data = json.loads(run("goal", cwd=tmp_path).stdout)
    assert any("blocked on config" in item for item in data["activity"])


def test_empty_goal_text_is_rejected(tmp_path: Path) -> None:
    r = run("goal", "   ", cwd=tmp_path)
    assert r.returncode == 2
    assert not (tmp_path / ".dsh").exists()

    r = run("goal", "-", cwd=tmp_path, stdin="\n")
    assert r.returncode == 2
    assert not (tmp_path / ".dsh").exists()


def test_missing_workspace_is_rejected(tmp_path: Path) -> None:
    r = run("status", str(tmp_path / "does-not-exist"))
    assert r.returncode == 2


@pytest.mark.skipif(shutil.which("git") is None, reason="git 不可用，跳过 gitignore 标记测试")
def test_gitignored_config_is_marked(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(".dsh/\n", encoding="utf-8")
    (tmp_path / ".dsh").mkdir()
    (tmp_path / ".dsh" / "config.json").write_text("{}\n", encoding="utf-8")

    r = run("status", cwd=tmp_path)
    assert r.returncode == 0
    assert ".dsh/config.json  (exists, gitignored)" in r.stdout


def test_init_is_idempotent(tmp_path: Path) -> None:
    r = run("init", cwd=tmp_path)
    assert r.returncode == 0
    config = tmp_path / ".dsh" / "config.json"
    assert config.is_file()
    payload = json.loads(config.read_text(encoding="utf-8"))
    assert payload["workspace"] == str(tmp_path.resolve())
    assert payload["version"] == 1

    first = config.read_bytes()
    r = run("init", cwd=tmp_path)
    assert r.returncode == 0
    assert "already initialized" in r.stdout
    assert config.read_bytes() == first
