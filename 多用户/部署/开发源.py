"""登记并校验唯一可写开发区。

发布候选只能从这个目录的已提交 Git 版本生成；候选、暂存和封存版
都不能反向成为开发源。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping


开发源标记文件 = ".xj-development-source.json"
开发源类型 = "xiaojiu-canonical-development-source"


class 开发源错误(RuntimeError):
    pass


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", "-C", str(root), *args],
        stdin=subprocess.DEVNULL, text=True, capture_output=True, check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:]
        raise 开发源错误(detail[0] if detail else "Git 工作区校验失败")
    return result.stdout.strip()


def _读JSON(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise 开发源错误(f"无法读取开发源身份：{path}") from exc
    if not isinstance(value, dict):
        raise 开发源错误("开发源身份格式无效")
    return value


def _写JSON(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(value), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _是相同或子目录(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def 核对开发源(
    source: Path,
    *,
    release_store: Path | None = None,
    require_canonical_branch: bool = True,
) -> dict[str, Any]:
    """校验一个真正的 Git 开发根目录，不接受封存副本或子目录。"""
    raw = source.expanduser()
    if raw.is_symlink():
        raise 开发源错误("唯一开发区不能是符号链接")
    try:
        root = raw.resolve(strict=True)
    except OSError as exc:
        raise 开发源错误("唯一开发区不存在") from exc
    if not root.is_dir():
        raise 开发源错误("唯一开发区不是目录")
    if release_store is not None:
        store = release_store.expanduser().resolve()
        if _是相同或子目录(root, store):
            raise 开发源错误("候选、暂存或封存副本不能登记为开发区")

    top = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    if top != root:
        raise 开发源错误("开发源必须指向 Git 顶层目录")
    marker_path = root / 开发源标记文件
    if not marker_path.is_file() or marker_path.is_symlink():
        raise 开发源错误("该目录没有唯一开发源身份")
    marker = _读JSON(marker_path)
    source_id = str(marker.get("source_id") or "")
    canonical_branch = str(marker.get("canonical_branch") or "")
    if marker.get("schema") != 1 or marker.get("kind") != 开发源类型 or not source_id or not canonical_branch:
        raise 开发源错误("唯一开发源身份无效")
    branch = _git(root, "branch", "--show-current")
    if require_canonical_branch and branch != canonical_branch:
        raise 开发源错误(
            f"当前是 {branch or '游离提交'}，唯一开发线必须是 {canonical_branch}"
        )
    common_dir_text = _git(root, "rev-parse", "--git-common-dir")
    common_dir = Path(common_dir_text)
    if not common_dir.is_absolute():
        common_dir = root / common_dir
    return {
        "schema": 2,
        "source": str(root),
        "source_id": source_id,
        "canonical_branch": canonical_branch,
        "branch": branch,
        "git_common_dir": str(common_dir.resolve()),
        "head": _git(root, "rev-parse", "HEAD"),
    }


def 登记唯一开发源(
    source: Path,
    *,
    record_path: Path,
    release_store: Path | None = None,
    replace: bool = False,
) -> Path:
    identity = 核对开发源(source, release_store=release_store)
    try:
        existing: dict[str, Any] | None = _读JSON(record_path)
    except 开发源错误:
        existing = None
    old_source = str((existing or {}).get("source") or "")
    if old_source and old_source != identity["source"] and not replace:
        raise 开发源错误(
            f"本机已登记其他开发区：{old_source}；替换必须明确使用 --replace-development-source"
        )
    same_source = bool(existing) and all(existing.get(key) == identity.get(key) for key in (
        "schema", "source", "source_id", "canonical_branch", "branch", "git_common_dir",
    ))
    if same_source and existing.get("head") == identity["head"]:
        return Path(str(identity["source"]))
    now = time.time()
    payload = {
        **identity,
        "registered_at": (
            float(existing.get("registered_at"))
            if same_source and isinstance(existing.get("registered_at"), (int, float))
            else now
        ),
        "verified_at": now,
    }
    _写JSON(record_path, payload)
    return Path(str(identity["source"]))


def 读取唯一开发源(
    *,
    record_path: Path,
    release_store: Path | None = None,
    configured: str | None = None,
) -> Path:
    try:
        record = _读JSON(record_path)
    except 开发源错误 as exc:
        raise 开发源错误("本机尚未登记唯一开发区，禁止生成候选") from exc
    source = Path(str(record.get("source") or "")).expanduser()
    identity = 核对开发源(source, release_store=release_store)
    for key in ("source", "source_id", "canonical_branch", "git_common_dir"):
        if str(record.get(key) or "") != str(identity.get(key) or ""):
            raise 开发源错误("开发源登记与当前 Git 身份不一致，禁止生成候选")
    selected = (configured if configured is not None else os.environ.get("XJ_DEVELOPMENT_SOURCE", "")).strip()
    if selected and Path(selected).expanduser().resolve() != Path(str(identity["source"])):
        raise 开发源错误("进程指定的开发源与本机唯一登记不一致")
    return Path(str(identity["source"]))
