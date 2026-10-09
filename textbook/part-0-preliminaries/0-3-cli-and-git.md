# 0.3 命令行与 Git 基础

> 本章属于「篇零 · 零基础补课」。它不涉及 LLM，但它是你后续**每一次**与 coding agent 协作、**每一次**提交代码的底层语言。读完本篇你应当能独立完成：在终端里定位文件、跑脚本、看退出码，用 Git 记录一次可复现的改动，并读懂一个 Pull Request。

---

## 1. 本课目标

学完本章，你应当能够：

1. 解释 Shell 的**管道**、**重定向**、**退出码**三件事，并用 `ls` / `cd` / `cat` / `grep` / `find` / `chmod` / `env` 完成日常操作。
2. 说明环境变量与 `.env` 的作用，并用 `subprocess.run(...)` 在 Python 里安全地调用外部进程、读取其输出与返回码。
3. 用 **Git 三区模型**（工作区 / 暂存区 / 提交）解释 `add` / `commit` / `status` / `diff` / `log` 各自的职责。
4. 区分 `merge` 与 `rebase`，能手动解决一次合并冲突。
5. 完成一次远程协作闭环：`clone` / `fetch` / `pull` / `push`，并说清 Pull Request 与 `CODEOWNERS` 的作用。
6. 用 `.gitignore`、`git restore`、`git reset --soft`、`git stash` 等命令做「救援」。

## 2. 前置知识

- 能在图形界面的编辑器里打开、保存文件。
- 会一点点 Python（变量、函数、`import`）。篇零 0.1 已覆盖。
- 知道「文件系统的路径分绝对路径与相对路径」。如果你不确定，先跑一遍 `pwd`、`cd`、`ls`。

## 3. 为什么需要它

**因为 Agent 工程师的工作界面就是终端和 Git。**

- coding agent（包括你正在使用的这个 IDE 里的 agent）最终是通过**执行命令**和**读写文件**来工作的。你在第 6 章要亲自动手写一个「类 Claude Code 的编码 Agent」，它的核心工具之一就是 shell 执行；不理解退出码、stdin/stdout/stderr，你写出来的工具就无法判断命令到底成功没有。
- 本仓库（Pydantic AI）的全部工作流都建立在 Git 上：`pre-commit` 钩子在提交前跑 lint，CI 在 PR 上跑测试，PR 标题直接进入发布 changelog，`CODEOWNERS` 决定谁来评审。详见 [`code_wiki/12-development-workflow.md`](../../code_wiki/12-development-workflow.md)。
- 面试时，「你能不能用命令行把一个混乱的工作区救回来」是区分「会调 API」和「能交付工程」的分水岭。

一句话：命令行是你对机器的**接口**，Git 是你对时间的**存档**。两者都是杠杆。

## 4. 核心概念

### 4.1 Shell 基础：管道、重定向、退出码

Shell（bash / zsh）是一个「把命令读进来、执行、再读下一条」的循环。三个必须掌握的概念：

**管道 `|`**：把左边命令的 stdout 接到右边命令的 stdin。

```bash
ls -la | grep ".py"          # 只保留输出里含 .py 的行
git log --oneline | head -5  # 只看最近 5 次提交
```

**重定向**：`>` 覆盖写入文件，`>>` 追加，`2>` 重定向 stderr（错误信息），`<` 把文件喂给 stdin。

```bash
python script.py > out.log 2> err.log   # 正常输出与错误分开存
echo "done" >> notes.md                 # 追加，不覆盖
```

**退出码（exit code）**：每条命令结束都会返回一个 0–255 的整数。**0 表示成功，非 0 表示失败**。这是交互式世界里唯一可靠的「成败信号」，也是自动化脚本的判据。

```bash
ls /tmp
echo $?        # 打印上一条命令的退出码：存在则是 0
grep foo /etc/hosts
echo $?        # 没匹配到则返回 1
```

常用命令速查：

| 命令 | 作用 | 例 |
|------|------|----|
| `pwd` | 打印当前目录 | `pwd` |
| `cd` | 切换目录 | `cd /workspace/textbook` |
| `ls` | 列目录 | `ls -la` |
| `cat` | 打印文件内容 | `cat README.md` |
| `grep` | 按模式搜文本 | `grep -rn "TODO" src/` |
| `find` | 按名字/属性找文件 | `find . -name "*.py"` |
| `chmod` | 改权限 | `chmod +x run.sh` |
| `env` | 打印/设置环境变量 | `env` 或 `export A=1` |

### 4.2 环境变量与 `.env`

环境变量是**进程启动时继承的一组键值对**，用来给程序传配置（尤其是密钥）。常见的：`PATH`（Shell 去哪里找可执行文件）、`HOME`、`PYTHONPATH`。

```bash
export OPENAI_API_KEY="sk-..."   # 当前 Shell 及其子进程可见
echo "$OPENAI_API_KEY"
env | grep OPENAI
```

把密钥写进代码或提交进仓库是**严重事故**。工程上的标准做法是放在 `.env` 文件里（本地开发用），并把 `.env` 写进 `.gitignore`：

```bash
# .env（绝不提交）
OPENAI_API_KEY=sk-...
```

在 Python 里读取用 `os.environ`：

```python
import os

api_key = os.environ.get("OPENAI_API_KEY")  # 不存在返回 None，不抛异常
if api_key is None:
    raise RuntimeError("请先设置 OPENAI_API_KEY")
```

> 注意：`export` 只对**当前 Shell 及其派生的子进程**生效。子进程能继承父进程的环境变量，这正是「用 `env` 给子进程传配置」的原理。

### 4.3 进程与子进程

Python 脚本本身是一个**进程**。当它需要调用外部程序（`git`、`uv`、`docker`）时，会通过 `subprocess` 模块创建一个**子进程**。

最小用法：

```python
import subprocess
import sys

result = subprocess.run(
    [sys.executable, "-c", "print('hi')"],
    check=False,          # check=True 时非零退出码会抛 CalledProcessError
    capture_output=True,  # 捕获 stdout 与 stderr
    text=True,            # 解码为 str
)
print(result.returncode)  # 0
print(result.stdout)      # 'hi\n'
```

三个关键参数：

- `capture_output=True`：默认子进程的输出会直接打到你的终端；捕获后你才能在 Python 里检查它。
- `text=True`：把 bytes 解码成 str。
- `check=True`：把「非零退出码」升级为异常，避免错误被忽略——这是把外部命令的退出码接入你程序逻辑的正确方式。

**永远用列表传参**（`["git", "commit", "-m", msg]`），不要拼字符串交给 `shell=True`。前者参数不会被 Shell 二次解析，能避免命令注入与空格转义问题。

### 4.4 Git 三区模型

Git 把你的改动分成三个区域，理解它，`add` / `commit` / `status` 就都通了：

```
工作区 (Working Directory)   你正在编辑的文件
   │  git add
   ▼
暂存区 (Staging Area / Index)  下次提交的快照清单
   │  git commit
   ▼
提交 (Commit / HEAD)          不可变的历史记录
```

| 命令 | 作用 | 影响哪个区 |
|------|------|-----------|
| `git status` | 看当前哪些文件被改动/暂存 | 只读 |
| `git diff` | 看**工作区 vs 暂存区**的差异 | 只读 |
| `git diff --staged` | 看**暂存区 vs 最后一次提交**的差异 | 只读 |
| `git add <file>` | 把改动放入暂存区 | 工作区 → 暂存区 |
| `git commit -m "..."` | 把暂存区固化为一次提交 | 暂存区 → 提交 |
| `git log --oneline` | 查看提交历史 | 只读 |

**暂存区的意义**：它让你可以把「一堆杂乱的改动」精心组织成「若干语义清晰的提交」。这也是本仓库 `atomic-commit-discipline` 的物理基础。

### 4.5 分支、合并与变基

**分支（branch）** 只是一个指向某次提交的可移动指针。默认分支通常叫 `main`。

```bash
git switch -c feature/login    # 从当前提交开一条新分支并切过去
# ... 在新分支上 commit ...
git switch main
git merge feature/login        # 把 feature/login 合并进 main
```

**`merge` vs `rebase`**：

| | `merge` | `rebase` |
|---|---|---|
| 结果 | 生成一个合并提交，保留两条历史 | 把你的提交「搬到」目标分支顶端，历史是直线 |
| 历史形状 | 有分叉、忠实 | 线性、整洁 |
| 是否改写提交 | 否 | **是**（产生新 commit hash） |
| 适用场景 | 已推送的共享分支、要保留上下文 | 尚未推送的本地分支、想保持线性历史 |

**取舍口诀**：**已推送的分支不要 rebase**（会改写别人正在用的历史）。本地未推送的功能分支，用 `rebase` 把主线更新拉进来，历史最干净。

**冲突解决**：当两个分支改了同一处，merge/rebase 会停下来标记冲突：

```bash
git status              # 列出 "both modified" 的文件
# 手动编辑文件，删掉 <<<<<<< ======= >>>>>>> 标记，留下正确内容
git add path/to/file    # 标记为已解决
git commit              # merge 场景：完成合并提交
git rebase --continue   # rebase 场景：继续
# 想放弃：git merge --abort  或  git rebase --abort
```

### 4.6 远程协作与 PR 流程

```bash
git clone https://github.com/pydantic/pydantic-ai.git   # 克隆远程仓库
git fetch origin                                        # 拉取远程更新（不合并）
git pull                                                # = fetch + merge
git push -u origin feature/login                        # 推送并设置上游分支
```

- `fetch` 只更新本地的远程跟踪分支（`origin/main`），不动你的工作区：**最安全**，先看再决定。
- `pull` 等价于 `fetch` 后立刻 `merge`。
- 首次推送用 `git push -u origin <branch>` 记录上游，之后直接 `git push`。

**Pull Request（PR）流程**：在 GitHub 上从你的分支向 `main` 发起 PR，触发 CI 跑测试，由评审者审阅、讨论、合并。

**`CODEOWNERS`**：一个位于 `.github/CODEOWNERS` 的文本文件，把路径映射到「必须评审该改动的人/团队」。被匹配到的路径有改动时，对应 owner 会被自动请求评审。它把「谁负责这块代码」写进版本控制，是团队协作的自动化守卫。

## 5. 最小可运行示例

下面这个模块只依赖标准库，做两件事：用 `subprocess` 捕获子进程输出，以及在临时目录里完成一次真实的 Git 提交。完整文件见 `textbook/labs/part_0/ch_0_3.py`。

```python
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
```

运行它：

```bash
cd textbook/labs
uv run --no-project python part_0/ch_0_3.py
```

预期输出（提交哈希会不同）：

```
== 0.3 命令行与 Git 基础 · 演示 ==
[subprocess] returncode=0 stdout='hi\n'
[git] 临时目录：/tmp/ch_0_3_xxxxxxxx
[git] add 后 status --porcelain： 'A  README.md'
[git] log --oneline：
89a2380 chore: initial commit
[git] commit 后 status --porcelain（应为空）： ''
[git] 修改后 status --porcelain： 'M README.md'
[git] 临时目录已清理。
```

**逐行讲解**：

- `shutil.which("git")`：在 `PATH` 中查找可执行文件，找不到返回 `None`。先探测再使用，是「本机可能没装」这类环境差异的标准处理。
- `tempfile.TemporaryDirectory()`：创建临时目录，`with` 块退出时自动递归清理，不污染你的机器。
- `git -c user.name=... -c user.email=... commit`：`-c` 是「仅本次命令生效的配置」，因此我们不需要改用户全局的 Git 配置。
- `git status --porcelain`：机器可读的稳定输出格式（供脚本解析），比人类可读的 `git status` 更适合断言。`A ` 前缀表示「已暂存的新增」，` M` 表示「已修改未暂存」，空字符串表示「干净」。
- `git log --oneline`：每条提交压成一行（短哈希 + 标题），适合快速浏览。

## 6. 深入剖析

**`subprocess.run` 的签名要点**（标准库）：

```python
subprocess.run(
    args,                  # 列表（推荐）或字符串（需 shell=True）
    *,
    capture_output=False,  # True 等价于同时设 stdout=PIPE, stderr=PIPE
    check=False,           # True 时非零退出码抛 CalledProcessError
    cwd=None,              # 子进程的工作目录
    env=None,              # 子进程的环境变量（默认继承父进程）
    text=False,            # True 时以文本模式返回（自动解码）
    timeout=None,          # 超时秒数，超时抛 TimeoutExpired
) -> CompletedProcess
```

`CompletedProcess` 携带 `returncode`、`stdout`、`stderr`。要点：

- **退出码是唯一可靠的成功判据**。不要靠解析 stdout 里的「成功」二字。
- 想让子进程拿到干净的、可复现的环境，就显式传 `env=`（通常基于 `os.environ.copy()` 再增删）。测试里尤其重要。
- `check=True` 是「把外部命令纳入异常流」的开关；配合 `try/except subprocess.CalledProcessError` 可读取 `e.returncode` / `e.stdout` / `e.stderr`。

**`.gitignore`**：告诉 Git 哪些路径**永远不要**纳入版本控制。典型内容：

```gitignore
__pycache__/
*.py[cod]
.venv/
.env
*.log
dist/
```

注意：`.gitignore` 只对**尚未被跟踪**的文件生效。已经 `git add` 过的文件再加进忽略列表不会自动失效，需要 `git rm --cached <file>`。

**救援命令**：

| 命令 | 作用 | 风险 |
|------|------|------|
| `git restore <file>` | 丢弃工作区对该文件的修改，回到暂存区版本 | 丢掉未保存的改动，不可恢复 |
| `git restore --staged <file>` | 把文件从暂存区撤回工作区（不改内容） | 低 |
| `git reset --soft HEAD~1` | 撤销最近一次提交，改动留在暂存区 | 低（不改工作区） |
| `git reset --mixed HEAD~1` | 撤销最近一次提交，改动留在工作区（默认） | 中 |
| `git reset --hard HEAD~1` | 撤销提交并丢弃所有改动 | **高，不可恢复** |
| `git stash` | 把当前改动暂存起来、让工作区变干净 | 低 |
| `git stash pop` | 恢复最近一次 stash | 可能冲突 |
| `git reflog` | 查看 HEAD 曾经指向过哪里 | 只读，是你「最后的后悔药」 |

## 7. 常见变体与工程实践

- **原子提交**：一个提交只做一件事，标题写「为什么」而非「做了什么」。本仓库约定：commit subject 可用 `fix:` / `docs:` / `chore:` 前缀，但**PR 标题不加前缀**，且所有代码标识符用反引号包裹。见 [`code_wiki/12-development-workflow.md`](../../code_wiki/12-development-workflow.md) 第 11 节。
- **pre-commit 钩子**：本仓库配置了 `no-commit-to-branch`（阻止直接提交到 `main`）、`end-of-file-fixer`、`ruff` format/lint 等钩子。钩子在本地提交时自动运行，把问题挡在 CI 之前。
- **`.env` + 环境变量**：本地密钥放 `.env`（进 `.gitignore`），生产环境用平台注入的环境变量；代码中统一用 `os.environ` 读取并做缺失校验。
- **`git fetch` 优先于 `git pull`**：在动自己工作区之前，先 `fetch` 看清远程发生了什么，降低冲突惊吓。
- **在脚本里调用外部命令时**：始终 `check=True`（或显式检查 `returncode`）、始终用列表传参、始终设 `timeout`，这是把 shell 能力安全地嵌进 Python 的三条底线。
- **给 coding agent 的启发**：agent 的「执行 shell」工具本质上就是一次 `subprocess.run`，它必须把 `returncode`、`stdout`、`stderr` 三者都回传给模型，模型才能判断「命令是否成功、错在哪」。这正是第 6 章要实现的。

## 8. 练习

**练习 1（热身）**：在当前目录下，用一条管道命令列出所有 `.md` 文件的名字。

<details>
<summary>参考答案要点</summary>

`ls | grep ".md"` 或更规范的 `find . -maxdepth 1 -name "*.md"`。前者靠文本过滤，后者按文件属性查找，后者更健壮。
</details>

**练习 2（退出码）**：写一条命令检查 `/etc/hosts` 是否存在，并根据退出码打印成功或失败。

<details>
<summary>参考答案要点</summary>

```bash
test -f /etc/hosts && echo "exists" || echo "missing"
```

`&&` 在前一条成功（退出码 0）时执行，`||` 在失败时执行。也可用 `if [ -f /etc/hosts ]; then ... fi`。
</details>

**练习 3（subprocess）**：用 Python 运行 `python -c "print('hi')"`，断言退出码为 0 且 stdout 为 `'hi\n'`。

<details>
<summary>参考答案要点</summary>

```python
import subprocess, sys

r = subprocess.run([sys.executable, "-c", "print('hi')"], check=False, capture_output=True, text=True)
assert r.returncode == 0
assert r.stdout == "hi\n"
```

用 `sys.executable` 而非硬编码 `"python"`，保证用的是当前解释器。
</details>

**练习 4（三区模型）**：解释 `git diff` 与 `git diff --staged` 的区别，各在什么场景使用。

<details>
<summary>参考答案要点</summary>

`git diff` 比较「工作区 vs 暂存区」，用于提交前确认还有哪些改动**尚未** `add`；`git diff --staged` 比较「暂存区 vs 最后一次提交」，用于确认**即将提交**的内容是否符合预期。提交前两者都看一遍，可避免漏提交或误提交。
</details>

**练习 5（综合）**：在临时目录里初始化仓库、提交一个文件、再修改它，用 `git status --porcelain` 验证「提交后为空、修改后非空」。

<details>
<summary>参考答案要点</summary>

见本章验收测试 `textbook/labs/part_0/test_ch_0_3.py`：init → 写 `README.md` → `add` → `commit`（记得用 `-c user.name/email` 或配置身份）→ 断言 `status --porcelain` 为空 → 修改文件 → 断言再次非空。空字符串 vs 非空字符串，是「干净 vs 有改动」的机器可读判据。
</details>

## 9. 验收标准

本章配套的验收测试在 `textbook/labs/part_0/test_ch_0_3.py`。运行：

```bash
cd textbook/labs
uv run --no-project --with pytest python -m pytest part_0/test_ch_0_3.py -q
```

测试覆盖以下断言，**全部通过**即视为本章掌握：

- 不依赖 git：`python -c "print('hi')"` 的退出码为 `0`、stdout 为 `"hi\n"`。
- `git init` 后 `.git` 目录存在。
- 写入文件后内容可读回。
- `git add` 后 `git status --porcelain` 含该文件名。
- `git commit` 后 `git log --oneline` 含提交信息。
- 提交后 `git status --porcelain` 为空（工作区干净）。
- 再次修改文件后 `git status --porcelain` 非空。

> 本机未安装 git 时，Git 相关测试会自动 `skip`（而非失败），并在报告中标注 `git not installed`。

## 10. 常见坑与排错

- **`git commit` 报 `Please tell me who you are`**：本机没配 `user.name` / `user.email`。临时办法是 `git -c user.name=X -c user.email=Y commit ...`；一劳永逸是 `git config --global user.name "..."`。
- **以为 `git add` 就提交了**：`add` 只是放入暂存区。必须 `git commit` 才写入历史。用 `git status` 能清楚看到「Changes to be committed」与「Changes not staged」两栏。
- **`.gitignore` 不生效**：文件已被跟踪时忽略规则无效，需要 `git rm --cached <file>` 后再提交。
- **`git pull` 突然产生冲突**：养成「先 `git status` 确认工作区干净再 `pull`」的习惯；有未提交改动时先 `git stash`。
- **误删改动想找回**：先 `git reflog` 看 HEAD 历史，绝大多数「丢失的提交」都能通过它找回。
- **`subprocess` 输出是 `b'...'`**：忘了 `text=True`。加上它即可得到 str。
- **`shell=True` 带来的注入风险**：永远优先用列表传参；只在确实需要 Shell 特性（通配符、管道）时才用 `shell=True`，且绝不把未净化的用户输入拼进去。
- **`CalledProcessError` 吞掉了详细信息**：异常对象上有 `e.returncode` / `e.stdout` / `e.stderr`，排错时把它们打出来。

## 11. 面试延伸

**问 1：`git merge` 和 `git rebase` 怎么选？**

要点：`merge` 保留分叉历史、生成合并提交、不改写已有提交，适合已推送的共享分支；`rebase` 把你的提交重放到目标分支顶端，得到线性历史但**会改写 commit hash**，只适合尚未推送的本地分支。原则：**已推送的历史不要 rebase**。

**问 2：描述 Git 的三区模型，以及 `add` 为什么存在？**

要点：工作区 / 暂存区 / 提交。暂存区（index）是一个「待提交快照清单」，`add` 的意义在于让你**有选择地、分批次地**组织提交，把一次杂乱的修改拆成若干语义清晰的原子提交。

**问 3：`git reset --soft` / `--mixed` / `--hard` 的区别？**

要点：三者都移动 HEAD 指针。`--soft` 只移动指针，改动全留在暂存区；`--mixed`（默认）移动指针并重置暂存区，改动留在工作区；`--hard` 移动指针并**丢弃**暂存区与工作区的所有改动，不可恢复。回退已推送的提交应改用 `git revert`（生成反向提交，不改写历史）。

**问 4：什么是退出码，为什么自动化脚本依赖它？**

要点：进程结束时返回的 0–255 整数，0 表示成功、非 0 表示失败。它是唯一标准化的「成败信号」，语言无关、无需解析输出文本，因此 CI、Shell 的 `&&` / `||`、以及 `subprocess.run(check=True)` 都以它为判据。

**问 5：为什么在 Python 里调用外部命令要用列表传参而不是字符串 + `shell=True`？**

要点：列表传参下，参数由 `subprocess` 直接传给 `exec`，不经过 Shell 解析，因此不怕空格、通配符与命令注入；`shell=True` 会把字符串交给 Shell 二次解析。另外要配 `check=True`（或检查 `returncode`）与 `timeout`，才能正确处理失败与挂起。

## 12. 延伸阅读

- [`code_wiki/12-development-workflow.md`](../../code_wiki/12-development-workflow.md)：本仓库的 Makefile target、pre-commit、CI、以及**提交与 PR 约定**（第 11 节）——本章 4.5 / 4.6 / 7 节的工程落地。
- [`code_wiki/11-cli-clai-clai2.md`](../../code_wiki/11-cli-clai-clai2.md)：Pydantic AI 的官方 CLI（`pai` / `clai` / `clai2`）如何组织命令行入口——命令行能力在真实项目中的形态。
- 下一章：`1.1 智能体心智模型：LLM、上下文、工具调用、ReAct、成本与延迟`。
