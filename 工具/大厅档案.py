#!/usr/bin/env python3
"""大厅按日档案的统一只读入口。

现役文件在 `大厅/对话/YYYY-MM-DD.jsonl`，旧日可在 `归档/` 下以 jsonl 或 jsonl.gz 保存。
搜索、摘要和场次回看都走这里，归档只改变存放位置，不能让公司忘掉历史。
"""
from __future__ import annotations

import gzip
from pathlib import Path

from 根 import 数据根

COMPANY = 数据根
默认目录 = COMPANY / "大厅" / "对话"


def 日期(path: Path) -> str:
    name = Path(path).name
    if name.endswith(".jsonl.gz"):
        return name[:-len(".jsonl.gz")]
    if name.endswith(".jsonl"):
        return name[:-len(".jsonl")]
    return Path(path).stem


def 各日文件(root: Path | None = None) -> list[Path]:
    """每个日期只返回一份；现役文件优先于归档副本。"""
    base = Path(root or 默认目录)
    if not base.exists():
        return []
    chosen: dict[str, Path] = {}
    for path in sorted((base / "归档").rglob("*.jsonl.gz")) if (base / "归档").exists() else []:
        chosen.setdefault(日期(path), path)
    for path in sorted((base / "归档").rglob("*.jsonl")) if (base / "归档").exists() else []:
        chosen.setdefault(日期(path), path)
    for path in sorted(base.glob("*.jsonl")):
        chosen[日期(path)] = path
    return [chosen[day] for day in sorted(chosen)]


def 日期列表(root: Path | None = None) -> list[str]:
    return [日期(path) for path in 各日文件(root)]


def 找日(day: str, root: Path | None = None) -> Path | None:
    wanted = str(day or "").strip()
    return next((path for path in 各日文件(root) if 日期(path) == wanted), None)


def 读文本(path: Path) -> str:
    path = Path(path)
    if path.name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as src:
            return src.read()
    return path.read_text(encoding="utf-8")
