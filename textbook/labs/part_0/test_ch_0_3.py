"""第 0.3 章验收测试：命令行与 Git 基础。

运行方式（在 textbook/labs 目录下）:

    uv run --no-project --with pytest python -m pytest part_0/test_ch_0_3.py -q

本机未安装 git 时，Git 相关测试会自动跳过。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

GIT = shutil.which("git")
requires_git = pytest.mark.skipif(GIT is None, reason="git not installed")


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """在 `repo` 目录执行 git 命令并返回完整结果（含 stdout/stderr/returncode）。"""
    assert GIT is not None
    return subprocess.run(
        [GIT, *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_subprocess_captures_stdout_and_returncode() -> None:
    """不依赖 git：用 subprocess 跑一段 Python 代码并断言输出与退出码。"""
    result = subprocess.run(
        [sys.executable, "-c", "print('hi')"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == "hi\n"


@requires_git
def test_git_init_commit_log_and_status(tmp_path: Path) -> None:
    """init → 写文件 → add → commit → log/status 的完整闭环。"""
    repo = tmp_path / "repo"
    repo.mkdir()

    _run_git(repo, "init")
    assert (repo / ".git").is_dir()

    readme = repo / "README.md"
    readme.write_text("# hello\n", encoding="utf-8")
    assert readme.read_text(encoding="utf-8") == "# hello\n"

    _run_git(repo, "add", "README.md")
    staged = _run_git(repo, "status", "--porcelain").stdout
    assert "README.md" in staged

    _run_git(
        repo,
        "-c",
        "user.name=Textbook Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "chore: initial commit",
    )

    log = _run_git(repo, "log", "--oneline").stdout
    assert "chore: initial commit" in log

    clean = _run_git(repo, "status", "--porcelain").stdout
    assert clean.strip() == ""

    readme.write_text("# hello\n\nchanged\n", encoding="utf-8")
    dirty = _run_git(repo, "status", "--porcelain").stdout
    assert "README.md" in dirty
