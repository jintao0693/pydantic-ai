"""第 0.3 章配套示例：命令行与 Git 基础。

本模块仅依赖标准库，演示两件事：

1. 用 `subprocess.run` 捕获子进程的标准输出与退出码；
2. 在临时目录里初始化一个 Git 仓库并完成一次提交。

运行方式（在 textbook/labs 目录下）:

    uv run --no-project python part_0/ch_0_3.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 在临时仓库里显式提供提交身份，避免读写甚至污染用户的全局 git 配置。
# 用 `git -c key=value` 传参，等价于一次性的 `git config`。
_COMMIT_IDENTITY = ("-c", "user.name=Textbook Bot", "-c", "user.email=textbook@example.invalid")


def git_available() -> bool:
    """本机是否安装了 git（`shutil.which` 在 PATH 中查找可执行文件）。"""
    return shutil.which("git") is not None


def run_git(*args: str, cwd: Path) -> str:
    """在 `cwd` 目录执行一条 git 子命令，返回去除首尾空白的 stdout。

    - `check=True`：非零退出码直接抛 `CalledProcessError`，不会被静默忽略；
    - `capture_output=True`：同时捕获 stdout 与 stderr；
    - `text=True`：把 bytes 解码为 str，省去手动 `.decode()`。
    """
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def run_python(code: str) -> tuple[int, str]:
    """用当前解释器跑一段代码，返回 `(returncode, stdout)`。"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode, completed.stdout


def init_and_commit(repo: Path, message: str = "chore: initial commit") -> dict[str, str]:
    """在 `repo` 中完成 init → 写文件 → add → commit，返回关键命令的输出。"""
    run_git("init", cwd=repo)
    readme = repo / "README.md"
    readme.write_text("# Ch 0.3 demo\n\n由 labs/part_0/ch_0_3.py 生成。\n", encoding="utf-8")
    run_git("add", "README.md", cwd=repo)
    staged = run_git("status", "--porcelain", cwd=repo)
    run_git(*_COMMIT_IDENTITY, "commit", "-m", message, cwd=repo)
    return {
        "staged_status": staged,
        "log": run_git("log", "--oneline", cwd=repo),
        "clean_status": run_git("status", "--porcelain", cwd=repo),
    }


def demo() -> None:
    """打印一次端到端演示的全部步骤。"""
    print("== 0.3 命令行与 Git 基础 · 演示 ==")

    code, out = run_python("print('hi')")
    print(f"[subprocess] returncode={code} stdout={out!r}")

    if not git_available():
        print("[git] 未检测到 git，跳过仓库演示。")
        print("      安装方式见 https://git-scm.com/downloads（macOS: brew install git；Ubuntu: apt install git）")
        return

    with tempfile.TemporaryDirectory(prefix="ch_0_3_") as tmp:
        repo = Path(tmp)
        print(f"[git] 临时目录：{repo}")
        info = init_and_commit(repo)
        print("[git] add 后 status --porcelain：", repr(info["staged_status"]))
        print("[git] log --oneline：")
        print(info["log"])
        print("[git] commit 后 status --porcelain（应为空）：", repr(info["clean_status"]))

        # 修改文件后再次查看状态，验证「工作区已偏离最后一次提交」。
        (repo / "README.md").write_text("# Ch 0.3 demo\n\n已修改。\n", encoding="utf-8")
        print("[git] 修改后 status --porcelain：", repr(run_git("status", "--porcelain", cwd=repo)))

    print("[git] 临时目录已清理。")


if __name__ == "__main__":
    demo()
