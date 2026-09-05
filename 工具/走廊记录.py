#!/usr/bin/env python3
"""死平台 · 走廊/工位的痕迹簿 —— 找同事、派活这类"场所外"的真实往来，也要忠实记录。

为什么（2026-07-02 彻查）：大厅、会议室各有痕迹簿，但 find_colleague 的对话、assign_task 的交接
不发生在任何"场所"里——于是在一切永久记录里隐形（只活在会话事件的临时流里，会话被清就没了）。
圣域⑫"任何场所内容忠实记录"漏了走廊和工位。本模块补上：横向协调与派活交接，逐条落
`走廊/对话/YYYY-MM-DD.jsonl`。

与 大厅记录/会议室记录 同构。默认不摊进大厅时间线（避免噪声淹掉船主的对话），但痕迹永远在、
将来要展示随时可读（读走廊()）。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from 根 import 数据根

COMPANY = 数据根
对话目录 = COMPANY / "走廊" / "对话"


def 记一次(谁: str, 找谁: str, 事由: str, 回复: str, 类型: str = "找人") -> None:
    """忠实记下一次走廊往来：谁找了谁、为什么事、对方回了什么（原话不改、不摘要）。"""
    if not 谁 or not 找谁:
        return
    now = dt.datetime.now()
    对话目录.mkdir(parents=True, exist_ok=True)
    with (对话目录 / f"{now.strftime('%Y-%m-%d')}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "时间": now.strftime("%Y-%m-%d %H:%M:%S"),
            "类型": 类型,  # 找人 | 派活
            "谁": str(谁), "找谁": str(找谁),
            "事由": (事由 or "").strip(),
            "回复": (回复 or "").strip(),
        }, ensure_ascii=False) + "\n")


def 读走廊() -> list[dict[str, Any]]:
    """读出走廊全部痕迹（只读；将来接展示用）。"""
    出: list[dict[str, Any]] = []
    if not 对话目录.exists():
        return 出
    for p in sorted(对话目录.glob("*.jsonl")):
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                出.append(json.loads(ln))
            except Exception:  # noqa: BLE001
                continue
    return 出
