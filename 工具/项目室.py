#!/usr/bin/env python3
"""项目室场所：保存一个历史项目自己的对话痕迹，并把新工作交回活厅主线。"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import threading
from pathlib import Path

from 根 import 数据根

COMPANY = 数据根
记忆目录 = COMPANY / "项目记忆"
_锁 = threading.RLock()


def 安全名(room: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+", "_", str(room or "")).strip("_")[:72] or "room"
    digest = hashlib.sha1(str(room or "").encode("utf-8")).hexdigest()[:8]
    return f"{digest}_{safe}.md"


def 记忆路径(room: str) -> Path:
    return 记忆目录 / 安全名(room)


def 记录(room: str, who: str, text: str, target: str = "") -> None:
    room = str(room or "").strip()
    text = str(text or "").strip()
    if not room or not text:
        return
    with _锁:
        记忆目录.mkdir(parents=True, exist_ok=True)
        p = 记忆路径(room)
        new = not p.exists()
        now = dt.datetime.now().strftime("%m-%d %H:%M")
        label = f"{who} -> {target}" if target else who
        with p.open("a", encoding="utf-8") as f:
            if new:
                f.write(f"# 项目记忆 · {room}\n\n绑定项目室: {room}\n")
            f.write(f"\n**[{now}] {label}**: {text}\n")
            f.flush()
            os.fsync(f.fileno())


def 读取(room: str) -> str:
    p = 记忆路径(str(room or ""))
    return p.read_text(encoding="utf-8") if p.exists() else ""
