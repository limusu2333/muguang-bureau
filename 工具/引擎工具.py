#!/usr/bin/env python3
"""办公室v3引擎的文件、白名单、日志与控制文件工具。"""
from __future__ import annotations

import datetime as dt
import fnmatch
import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any

from 根 import 产品根, 代码根, 工作根, 数据根

COMPANY = 数据根
PRODUCT = 产品根
CODE = 代码根
WORK = 工作根
工单 = 数据根 / "工单"
急停文件 = 数据根 / "公司急停.flag"


def 解析工单(path: Path) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 2 and cells[0]:
            meta[cells[0]] = cells[1]
    wl = [x.strip() for x in str(meta.get("白名单", "")).split(",") if x.strip()]
    meta["白名单列表"] = wl
    return meta


def 提取_json(raw: str) -> dict[str, Any]:
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError("动作不是JSON")
    obj = json.loads(raw[start:end])
    if not isinstance(obj, dict):
        raise ValueError("动作JSON不是对象")
    return obj


def 读路径(raw: str) -> Path:
    p = 归一(raw)
    if any(在内(p, root) for root in (CODE, COMPANY, WORK, PRODUCT)):
        return p
    raise PermissionError(f"读文件越界: {raw}")


def 写路径(raw: str, meta: dict[str, Any]) -> Path:
    p = 归一(raw)
    if not any(在内(p, root) for root in (COMPANY, WORK, PRODUCT)):
        raise PermissionError(f"写文件越界: {raw}")
    candidates: list[str] = []
    if 在内(p, COMPANY):
        candidates.append(str(p.relative_to(COMPANY)))
        candidates.append("data/" + str(p.relative_to(COMPANY)))
    if 在内(p, WORK):
        candidates.append("work/" + str(p.relative_to(WORK)))
    if 在内(p, PRODUCT):
        candidates.append("../20_工程_XJ/" + str(p.relative_to(PRODUCT)))
        candidates.append("product/" + str(p.relative_to(PRODUCT)))
    for pat in meta.get("白名单列表", []):
        if any(fnmatch.fnmatchcase(c, pat) for c in candidates):
            return p
    raise PermissionError(f"不在白名单: {raw}")


def 归一(raw: str) -> Path:
    p = Path(raw).expanduser()
    return (p if p.is_absolute() else WORK / p).resolve()


def 在内(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
        return True
    except ValueError:
        return False


def 任务路径(name: str) -> Path:
    for col in ("进行中", "待验收", "待确认", "待办"):
        p = 工单 / col / name
        if p.exists():
            return p
    return 工单 / "进行中" / name


def 日志(path: Path) -> Path:
    return path.with_name(path.stem + ".日志.md")


def 控制(path: Path) -> Path:
    return path.with_name(path.stem + ".控制.json")


def 初始控制() -> dict[str, Any]:
    return {
        "状态": "运行中",
        "步数": 0,
        "停止": False,
        "追加指示": [],
        "已消费指示": 0,
        "观察": [],
        "开始": 时刻(),
    }


def 读控制(path: Path) -> dict[str, Any]:
    if not path.exists():
        return 初始控制()
    return json.loads(path.read_text(encoding="utf-8"))


def 写控制(path: Path, data: dict[str, Any]) -> None:
    原子写(path, json.dumps(data, ensure_ascii=False, indent=2))


def 写日志(path: Path, text: str) -> None:
    now = dt.datetime.now().strftime("%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"\n[{now}] {text}\n")


def 原子写(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def 移动伴随文件(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    for maker in (日志, 控制):
        a = maker(src)
        if a.exists():
            shutil.move(str(a), str(maker(dst)))


def 追加记忆(role: str, summary: str) -> None:
    try:
        from 活_本人 import _岗位的人, 给某人记一笔
        给某人记一笔(_岗位的人(role), summary[:600])
    except Exception:  # noqa: BLE001
        return


def 显(path: Path) -> str:
    if 在内(path, COMPANY):
        return "data:" + str(path.relative_to(COMPANY))
    if 在内(path, WORK):
        return "work:" + str(path.relative_to(WORK))
    if 在内(path, PRODUCT):
        return "../20_工程_XJ/" + str(path.relative_to(PRODUCT))
    if 在内(path, CODE):
        return "code:" + str(path.relative_to(CODE))
    return str(path)


def 时刻() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def mock模式() -> bool:
    return os.environ.get("XJ_MOCK") == "1"
