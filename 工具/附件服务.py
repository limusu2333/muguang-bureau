#!/usr/bin/env python3
"""大厅附件的校验与持久化边界。"""
from __future__ import annotations

import base64
import datetime as dt
import os
import re
import uuid
from pathlib import Path
from typing import Any

from 根 import 数据根
from 实例配置 import 主人称呼

COMPANY = 数据根
单附件上限 = 5 * 1024 * 1024
附件总上限 = 12 * 1024 * 1024
附件数上限 = 8
_附件扩展 = {
    ".txt", ".md", ".json", ".jsonl", ".csv", ".tsv", ".yaml", ".yml", ".xml",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".sql", ".log",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp", ".pdf",
    ".docx", ".xlsx", ".pptx", ".zip",
}


def 保存附件(items: Any, scope: str) -> list[str]:
    """严格保存附件；任何一项不合格都拒绝整批，避免“少存一个却继续开工”。"""
    if items in (None, []):
        return []
    if not isinstance(items, list):
        raise ValueError("附件格式应为列表")
    if len(items) > 附件数上限:
        raise ValueError(f"附件最多 {附件数上限} 个")
    root = COMPANY / "附件" / dt.datetime.now().strftime("%Y-%m-%d") / re.sub(r"[^A-Za-z0-9_-]", "_", scope)
    saved: list[str] = []
    decoded: list[tuple[str, bytes]] = []
    total = 0
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise ValueError(f"第 {i + 1} 个附件格式错误")
        original = str(it.get("name") or f"附件{i+1}")
        name = re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]", "_", original)[:80]
        ext = Path(name).suffix.lower()
        if ext not in _附件扩展:
            raise ValueError(f"不支持的附件类型：{ext or '无扩展名'}")
        data = str(it.get("data") or "")
        if not data:
            raise ValueError(f"附件「{name}」没有内容")
        mime = str(it.get("type") or "").lower().strip()
        if data.startswith("data:"):
            m = re.match(r"^data:([^;,]*);base64,([A-Za-z0-9+/=\r\n]+)$", data, re.S)
            if not m:
                raise ValueError(f"附件「{name}」不是合法 Base64 数据")
            declared_mime, data = m.group(1).lower(), m.group(2)
            if mime and declared_mime and mime != declared_mime:
                raise ValueError(f"附件「{name}」的 MIME 类型前后不一致")
            mime = mime or declared_mime
        try:
            raw = base64.b64decode(data, validate=True)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"附件「{name}」Base64 解码失败") from e
        if not raw:
            raise ValueError(f"附件「{name}」是空文件")
        if len(raw) > 单附件上限:
            raise ValueError(f"附件「{name}」超过 {单附件上限 // 1024 // 1024}MB")
        total += len(raw)
        if total > 附件总上限:
            raise ValueError(f"附件合计超过 {附件总上限 // 1024 // 1024}MB")
        signatures = {
            ".png": raw.startswith(b"\x89PNG\r\n\x1a\n"),
            ".jpg": raw.startswith(b"\xff\xd8\xff"), ".jpeg": raw.startswith(b"\xff\xd8\xff"),
            ".gif": raw.startswith((b"GIF87a", b"GIF89a")),
            ".webp": len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP",
            ".bmp": raw.startswith(b"BM"), ".pdf": raw.startswith(b"%PDF-"),
            ".zip": raw.startswith(b"PK\x03\x04"), ".docx": raw.startswith(b"PK\x03\x04"),
            ".xlsx": raw.startswith(b"PK\x03\x04"), ".pptx": raw.startswith(b"PK\x03\x04"),
        }
        if ext in signatures and not signatures[ext]:
            raise ValueError(f"附件「{name}」的内容与扩展名不符")
        if ext in {".txt", ".md", ".json", ".jsonl", ".csv", ".tsv", ".yaml", ".yml", ".xml", ".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".sql", ".log"} and b"\x00" in raw:
            raise ValueError(f"附件「{name}」标成文本但含二进制内容")
        decoded.append((name, raw))
    root.mkdir(parents=True, exist_ok=True)
    for i, (name, raw) in enumerate(decoded):
        out = root / f"{uuid.uuid4().hex[:12]}_{i+1}_{name}"
        with out.open("xb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        saved.append(str(out.relative_to(COMPANY)))
    return saved


def 带附件文本(text: str, paths: list[str]) -> str:
    if not paths:
        return text
    lines = "\n".join(f"- {p}" for p in paths)
    图片后缀 = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp")
    有图 = any(p.lower().endswith(图片后缀) for p in paths)
    注 = (f"\n（注意：其中的图片当前班子只能看到文件路径，看不到画面内容——涉及画面判断要老实告诉{主人称呼()}看不了，别装看过）" if 有图 else "")
    return f"{text}\n\n[{主人称呼()}附上的文件/截图已保存]\n{lines}{注}"
