#!/usr/bin/env python3
"""公司的专业级文件搜索 —— Glob 遍历时剪掉依赖/垃圾目录，像 ripgrep / git / IDE 一样：又快又对。

为什么要这个（2026-06-23 卡死根因）：
  官方 `agentscope.tool.Glob` 用 `os.walk` 把整棵树全趟、`.gitignore`/隐藏目录/依赖目录一概不忽略。
  公司根目录下有 7 万+ 文件（`.venv` 1.4 万 + `前端/node_modules` + `_官方参考_agentscope` 5.6 万），
  岗位"查资料"时一个 `Glob("**/*.md")` 就从公司根趟这 7 万文件 + 逐个 os.stat 排序 → 卡死几十分钟。
  这不是给搜索"划个小圈"（那是阉能力），是把搜索本身做对：**遍历时原地剪枝、不下钻依赖垃圾目录**，
  岗位仍能搜整个公司、应付任何任务，只是不趟依赖。Grep 走 ripgrep 本就认 gitignore/跳隐藏，无需改。

只读、无副作用；供发言/查资料的只读工具集用。
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import Glob as _官方Glob, Grep, Read, Toolkit
from agentscope.tool import ToolChunk

from 密钥闸 import 密钥文件模式, 是密钥文件, 拒读
from 根 import 是远程实例, 代码根, 工作根, 数据根

公司根 = 代码根
搜索秒数上限 = 5.0
搜索结果上限 = 5000
_预算 = threading.local()


class _搜索中止(RuntimeError):
    pass


def _解析路径(
    raw: str | os.PathLike[str] | None,
    *,
    根: str = "code",
) -> Path:
    """按显式根解释相对路径；远程实例的绝对路径也不能越过三个实例根。"""
    roots = {"code": 代码根, "data": 数据根, "work": 工作根}
    if 根 not in roots:
        raise ValueError("根只能是 code、data 或 work（分别表示代码、用户数据、用户工作区）")
    默认 = roots[根]
    p = Path(raw).expanduser() if raw else 默认
    if not p.is_absolute():
        p = 公司根 / p
    try:
        p = p.resolve(strict=False)
    except OSError as e:
        raise ValueError(f"路径无法解析：{e}") from e
    if 是远程实例() and not any(p.is_relative_to(root.resolve()) for root in roots.values()):
        raise ValueError(f"远程实例禁止读取自身代码、数据和工作区以外的路径：{p}")
    return p


def _开始预算() -> None:
    _预算.截止 = time.monotonic() + 搜索秒数上限
    _预算.结果数 = 0


def _检查预算(*, 新增结果: int = 0) -> None:
    if time.monotonic() > getattr(_预算, "截止", 0.0):
        raise _搜索中止(f"搜索超过 {搜索秒数上限:g} 秒，请缩小目录或文件模式后重试")
    _预算.结果数 = getattr(_预算, "结果数", 0) + 新增结果
    if _预算.结果数 > 搜索结果上限:
        raise _搜索中止(f"搜索结果超过 {搜索结果上限} 个，请缩小目录或文件模式后重试")


def _错误响应(message: str) -> ToolChunk:
    return ToolChunk(
        content=[TextBlock(text=message)],
        state=ToolResultState.ERROR,
        is_last=True,
    )

# 依赖 / 构建产物 / 版本控制 / 缓存 / vendored 参考——专业搜索工具默认都跳这些
忽略目录 = {
    ".venv", "venv", "env", "node_modules",
    ".git", ".svn", ".hg", ".jj", ".sl", ".bzr",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache",
    "dist", "build", ".idea", ".vscode", "site-packages",
    "_官方参考_agentscope",
}


def _可下钻(name: str) -> bool:
    """该目录要不要往里走：跳掉依赖/垃圾目录 + 隐藏目录(.venv/.git 等都是隐藏)。"""
    return name not in 忽略目录 and not name.startswith(".")


class 净Glob(_官方Glob):
    """剪枝版 Glob：在遍历时就把依赖/垃圾/隐藏目录剪掉（os.walk/os.scandir 不下钻它们）。

    既正确（结果不含依赖噪声）又飞快（根本不进 7 万文件的依赖树）。只覆写两处遍历方法，
    glob 的模式语义与官方完全一致（继承 name/description/schema/__call__）。
    """

    def collect_all(self, current_dir: str, results: list[str]) -> None:
        try:
            for root, dirs, files in os.walk(current_dir):
                _检查预算()
                dirs[:] = [d for d in dirs if _可下钻(d)]  # 原地剪枝：os.walk 不再下钻这些目录
                for f in files:
                    _检查预算()
                    if 是密钥文件(f):  # 密钥闸：连文件名都不摆出来，别把岗位往密钥上引
                        continue
                    results.append(os.path.join(root, f))
                    _检查预算(新增结果=1)
        except (PermissionError, OSError):
            pass

    def match_parts(
        self,
        parts: list[str],
        part_index: int,
        current_dir: str,
        results: list[str],
    ) -> None:
        _检查预算()
        if part_index >= len(parts):
            return
        part = parts[part_index]
        is_last = part_index == len(parts) - 1
        if part == "**":
            if is_last:
                self.collect_all(current_dir, results)
            else:
                self.match_parts(parts, part_index + 1, current_dir, results)
                try:
                    with os.scandir(current_dir) as entries:
                        for entry in entries:
                            _检查预算()
                            if entry.is_dir(follow_symlinks=False) and _可下钻(entry.name):
                                self.match_parts(parts, part_index, entry.path, results)
                except (PermissionError, OSError):
                    pass
        else:
            regex = self.glob_part_to_regex(part)
            try:
                with os.scandir(current_dir) as entries:
                    for entry in entries:
                        _检查预算()
                        if not regex.match(entry.name):
                            continue
                        full_path = entry.path
                        if is_last:
                            if entry.is_file(follow_symlinks=False) and not 是密钥文件(entry.name):
                                results.append(full_path)
                                _检查预算(新增结果=1)
                        elif entry.is_dir(follow_symlinks=False) and _可下钻(entry.name):
                            self.match_parts(parts, part_index + 1, full_path, results)
            except (PermissionError, OSError):
                pass

    async def __call__(self, pattern: str, path: str | None = None, 根: str = "code") -> ToolChunk:  # type: ignore[override]
        try:
            base_dir = _解析路径(path, 根=根)
        except ValueError as e:
            return _错误响应(str(e))
        if not base_dir.exists() or not base_dir.is_dir():
            return _错误响应(f"搜索目录不存在或不是目录：{base_dir}")

        _开始预算()
        try:
            matches = self.glob_match(pattern, str(base_dir))
            _检查预算()
        except _搜索中止 as e:
            return _错误响应(str(e))
        finally:
            _预算.__dict__.clear()

        try:
            matches.sort(key=lambda p: os.stat(p).st_mtime, reverse=True)
        except (OSError, FileNotFoundError):
            pass
        if not matches:
            return ToolChunk(
                content=[TextBlock(text=f"No files found matching pattern: {pattern}")],
                state="running",
                is_last=True,
            )
        return ToolChunk(
            content=[TextBlock(text="\n".join(matches))],
            state="running",
            is_last=True,
        )


def _密钥闸响应(目标: str) -> ToolChunk:
    """密钥文件被拦时统一的大声报错（白箱：说清为什么、指向 ask_owner）。"""
    return ToolChunk(
        content=[TextBlock(text=拒读(目标))],
        state=ToolResultState.ERROR,
        is_last=True,
    )


class 净Read(Read):
    """带密钥闸的 Read：读到 .env / 私钥 / 凭据文件一律拒读、大声报错，其余和官方 Read 完全一致。

    只在最前面加一道判断，不碰官方的读取逻辑（继承 name/description/schema）。密钥红线是平台死规矩，
    不是岗位能力问题——所以焊在工具层，而不是靠提示词求岗位别读。
    """

    async def __call__(self, file_path: str = "", *args, 根: str = "code", **kwargs) -> ToolChunk:  # type: ignore[override]
        try:
            target = _解析路径(file_path, 根=根)
        except ValueError as e:
            return _错误响应(str(e))
        if 是密钥文件(str(target)):
            return _密钥闸响应(str(target))
        return await super().__call__(str(target), *args, **kwargs)


class 净Grep(Grep):
    """带密钥闸的 Grep：三层焊死，密钥文件的内容一个字都搜不出来。

    实测过 ripgrep 行为（2026-07-05）：`--glob '!.env'` 能在**遍历**时排除，但把 .env 当**显式路径**
    传进去时排除失效、照样读。所以：
      ① __call__ 先拦 path 参数——有人直接 `Grep(path='.env')` 时当场拒；
      ② _run_ripgrep 注入 `--glob '!密钥模式'`——遍历时根本不进密钥文件；
      ③ 出结果再按路径过一遍——任何漏网的密钥文件行都剔掉。兜底的兜底。
    """

    async def __call__(self, pattern: str, path: str | None = None, *args, 根: str = "code", **kwargs) -> ToolChunk:  # type: ignore[override]
        try:
            target = _解析路径(path, 根=根)
        except ValueError as e:
            return _错误响应(str(e))
        if 是密钥文件(str(target)):
            return _密钥闸响应(str(target))
        return await super().__call__(pattern, str(target), *args, **kwargs)

    async def _run_ripgrep(self, args, search_path, timeout: int = 30):  # type: ignore[override]
        排除 = []
        for 模式 in 密钥文件模式:
            排除 += ["--glob", f"!{模式}"]
        行 = await super()._run_ripgrep([*排除, *args], search_path, timeout)
        # ripgrep 各输出模式里，路径都在行首、到第一个冒号为止（macOS 路径不含冒号）
        return [ln for ln in 行 if not 是密钥文件(ln.split(":", 1)[0])]


def 资料工具集() -> Toolkit:
    """兼容旧调用的精确文件工具集。跨源按意思查由工具间 unified_search 统一提供。"""
    return Toolkit(tools=[净Read(), 净Grep(), 净Glob()])


if __name__ == "__main__":
    # 自检（不烧钱）：对比官方 Glob 与净Glob 在公司根扫 **/*.md 的耗时与是否含依赖噪声。
    import time
    根 = str(代码根)

    def 跑(g, 名):
        t = time.time()
        try:
            res = g.glob_match("**/*.md", 根)
        except Exception as e:  # noqa: BLE001
            print(f"  {名}: 异常 {type(e).__name__}: {e}")
            return
        脏 = [r for r in res if any(j in r for j in ("/.venv/", "/node_modules/", "/_官方参考_agentscope/", "/.git/"))]
        print(f"  {名}: {len(res)} 个结果，{time.time()-t:.2f}s，依赖噪声 {len(脏)} 条")

    print("搜索工具自检（公司根扫 **/*.md）：")
    跑(_官方Glob(), "官方 Glob")
    跑(净Glob(), "净 Glob ")
