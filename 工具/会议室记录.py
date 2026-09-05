#!/usr/bin/env python3
"""死平台 · 会议室这个「场所」的痕迹簿 —— 忠实记录是会议室自带的功能，不是开会人记得去记。

依据（船主圣域 2026-06-26）：**"任何场所内容忠实记录"是场所自己的功能。** 就像真实的会议室：
人开完会走了去忙别的，可发生在里面的事，**会议室自己留下了痕迹**。责任在场所，不在使用者。

所以记录写在会议室这个场所的运行逻辑里（`会议室.开会`）、**逐条即时落痕**：开场记一笔、每有人说一句就记一句——
人说一句、会议室记一句，哪怕会中途散了，已发生的也留在痕迹簿上（这才叫忠实）。不是等会开完由 hold_meeting 收尾补记。

存储：`会议室/对话/YYYY-MM-DD.jsonl`，一行一条痕迹（开会一条 + 每句发言一条），同一场会用 `会id` 串起。
看板数据.大厅() 读它，按时间拼成大厅时间线上的会议里程碑（k:会）+ 逐条发言气泡（k:话），前端零改动。

与 大厅记录.py 同构（大厅是一种场所、会议室是另一种），都是死平台给场所配的"痕迹簿"。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from 根 import 数据根

COMPANY = 数据根
会议目录 = COMPANY / "会议室" / "对话"


def _写(now: dt.datetime, obj: dict[str, Any]) -> None:
    会议目录.mkdir(parents=True, exist_ok=True)
    with (会议目录 / f"{now.strftime('%Y-%m-%d')}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def 开一场会(议题: str, 参会: list[str], 召集人: str = "项目经理", 时间: str | None = None) -> str:
    """会议室开场：在痕迹簿记下"开了一场会"，返回 会id（这场会后面的发言都归到它名下）。"""
    now = dt.datetime.now()
    会id = now.strftime("%Y%m%d_%H%M%S")
    _写(now, {"会id": 会id, "时间": 时间 or now.strftime("%Y-%m-%d %H:%M:%S"),
            "类型": "开会", "议题": (议题 or "").strip(), "参会": list(参会), "召集人": 召集人})
    return 会id


def 记一句(会id: str, who: str, text: str, 时间: str | None = None) -> None:
    """会议室如实记下一句发言（人说一句、会议室记一句；原话不改、中途散场也留痕）。"""
    text = (text or "").strip()
    if not 会id or not who or not text:
        return
    now = dt.datetime.now()
    _写(now, {"会id": 会id, "时间": 时间 or now.strftime("%Y-%m-%d %H:%M:%S"),
            "类型": "发言", "who": str(who), "text": text})


def 读会议() -> list[dict[str, Any]]:
    """读出会议室的全部痕迹，给 看板数据.大厅() 拼时间线（只读）。

    每条"开会"痕迹 → 一个会议里程碑（k:会，召集人开了一场会·议题，副=参会）；
    每条"发言"痕迹 → 一个气泡（k:话）。按痕迹簿里的真实先后产出，时间排序交给 大厅()。
    """
    出: list[dict[str, Any]] = []
    if not 会议目录.exists():
        return 出
    for p in sorted(会议目录.glob("*.jsonl")):
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                m = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
            t = str(m.get("时间") or "")
            if m.get("类型") == "开会":
                参会 = [str(x) for x in (m.get("参会") or [])]
                召集人 = str(m.get("召集人") or "项目经理")
                出.append({"k": "会", "t": t, "who": 召集人, "role": 召集人,
                          "text": f"开了一场会 · {str(m.get('议题') or '')}",
                          "副": "参会：" + "、".join(参会) if 参会 else "",
                          "会议id": str(m.get("会id") or ""),
                          "主题": str(m.get("议题") or ""),
                          "参会": 参会})
            elif m.get("类型") == "发言":
                who = str(m.get("who") or "")
                text = str(m.get("text") or "")
                if who and text:
                    出.append({"k": "话", "t": t, "who": who, "role": who, "text": text})
    return 出


def 读一场(会id: str) -> list[dict[str, Any]]:
    """按会id读一场会议的完整留痕，给 /meeting_replay 回看。

    返回统一的时间线行：时间 / who / text；开场行也保留，方便回看知道主题和参会。
    """
    会id = (会id or "").strip()
    if not 会id or not 会议目录.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(会议目录.glob("*.jsonl")):
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                m = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
            if str(m.get("会id") or "") != 会id:
                continue
            t = str(m.get("时间") or "")
            if m.get("类型") == "开会":
                参会 = "、".join(str(x) for x in (m.get("参会") or []))
                out.append({
                    "时间": t,
                    "who": str(m.get("召集人") or "会议室"),
                    "text": f"[开会] {str(m.get('议题') or '')}" + (f"\n参会：{参会}" if 参会 else ""),
                    "类型": "开会",
                    "会议id": 会id,
                    "主题": str(m.get("议题") or ""),
                    "参会": m.get("参会") or [],
                })
            elif m.get("类型") == "发言":
                out.append({
                    "时间": t,
                    "who": str(m.get("who") or ""),
                    "text": str(m.get("text") or ""),
                    "类型": "发言",
                    "会议id": 会id,
                })
    return out
