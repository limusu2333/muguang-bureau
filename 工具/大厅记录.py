#!/usr/bin/env python3
"""死平台 · 大厅这个「场所」的对话持久层 —— 任何在大厅发生的话，都被忠实记录。

依据（船主圣域 2026-06-26）：**任何发生在场所（大厅/会议室…）里的内容都应该被忠实记录，这是死平台的底层规则。**
依据《公司的真谛》：场所是客观存在的死物，有自己的规则与状态——大厅作为一个场所，就该有它**自己的、单一权威**的对话真相，
而不是靠 看板数据 从 会议/协同/信箱 这些别的用途的文件**逆向拼凑**出来（那是流程驱动时代的历史包袱）。
依据大厅规则牌：**对话永久留存、按天归档**。

机制：每在大厅说一句（船主发话 / 本人的报告/发言），就 append 一条到 `大厅/对话/YYYY-MM-DD.jsonl`。
- 按天一个文件 = 直接落地"按天归档"，正好接前端大厅的"按天折叠"。
- 这是**正源**：看板数据.大厅() 从这里读对话气泡；旧的 会议/协同 仍读、但只作"开会里程碑/项目室过程"。
- 这一层不依赖运行时会话（事件总线的 {sid}.jsonl 是一次 run 的过程流、会被当垃圾清掉，不能当永久根基）。

新旧并存：本模块是大厅的新地基，不碰旧的 会议/协同/信箱 写逻辑。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from 根 import 数据根

COMPANY = 数据根
对话目录 = COMPANY / "大厅" / "对话"
承接状态文件 = COMPANY / "运行状态" / "大厅承接.json"
_写锁 = threading.RLock()
会话静默间隔秒 = 90 * 60


def 当前承接岗位(房间键: str = "大厅", *, 现在: dt.datetime | None = None) -> str | None:
    """读这一场对话的责任席。跨天或静默超过90分钟即失效。

    状态损坏也不能堵住大厅：状态存储会登记故障，这里返回 None，由大厅回到项目经理席。
    """
    from 状态存储 import 读JSON

    try:
        状态 = 读JSON(承接状态文件, 默认={}, 类型=dict) or {}
        记录 = 状态.get(str(房间键 or "大厅")) or {}
        岗位 = str(记录.get("岗位") or "").strip()
        更新 = dt.datetime.fromisoformat(str(记录.get("更新时间") or ""))
    except Exception:  # noqa: BLE001
        return None
    现在 = 现在 or dt.datetime.now()
    if not 岗位 or 更新.date() != 现在.date():
        return None
    间隔 = (现在 - 更新).total_seconds()
    if 间隔 < 0 or 间隔 > 会话静默间隔秒:
        return None
    return 岗位


def 记承接岗位(岗位: str, 房间键: str = "大厅", 原因: str = "") -> None:
    """记住谁对这场对话负责。这是明确承接状态，不是“最后说话者”推测。"""
    from 状态存储 import 读JSON, 写JSON

    岗位 = str(岗位 or "").strip()
    if not 岗位:
        raise ValueError("承接岗位不能为空")
    key = str(房间键 or "大厅")
    with _写锁:
        状态 = 读JSON(承接状态文件, 默认={}, 类型=dict) or {}
        状态[key] = {
            "岗位": 岗位,
            "原因": str(原因 or "").strip(),
            "更新时间": dt.datetime.now().isoformat(timespec="seconds"),
        }
        写JSON(承接状态文件, 状态)


def 清承接岗位(房间键: str | None = None) -> None:
    """清掉一个房间的责任席；不传房间时清全部（主要给隔离测试/维护）。"""
    from 状态存储 import 读JSON, 写JSON

    with _写锁:
        if 房间键 is None:
            承接状态文件.unlink(missing_ok=True)
            return
        状态 = 读JSON(承接状态文件, 默认={}, 类型=dict) or {}
        状态.pop(str(房间键 or "大厅"), None)
        if 状态:
            写JSON(承接状态文件, 状态)
        else:
            承接状态文件.unlink(missing_ok=True)


def 发言事件键(会话id: str, 谁: str, 话: str) -> str:
    """为同一轮大厅发言生成稳定键，防止不同收尾路径重复落盘。"""
    raw = "\0".join((str(会话id or ""), str(谁 or ""), str(话 or "").strip()))
    return "大厅发言:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def 记一句(谁: str, 话: str, 时间: str | None = None, **附加字段: Any) -> None:
    """把大厅里发生的一句话忠实记下来（追加，不覆盖）。船主发话、本人的报告/发言都往这写。

    谁 = 发言人（"船主" / "项目经理" / 人名…）；话 = 原话（不改动、不摘要——忠实记录）。
    """
    话 = (话 or "").strip()
    # 平台内部信号（聊天/办事底盘之间的哨兵），不是场所里真说的话——永不落盘、永不渲染成气泡（G-8）
    内部哨兵 = {"[转办事]", "[沉默]", "（没说话）", "(没说话)"}
    if not 谁 or not 话 or 话 in 内部哨兵:
        return
    now = dt.datetime.now()
    日 = now.strftime("%Y-%m-%d")
    ts = 时间 or now.strftime("%Y-%m-%d %H:%M:%S")
    对话目录.mkdir(parents=True, exist_ok=True)
    row = {"时间": ts, "who": 谁, "text": 话}
    for k, v in 附加字段.items():
        if v is not None:
            row[k] = v
    p = 对话目录 / f"{日}.jsonl"
    事件键 = str(row.get("事件键") or "").strip()
    with _写锁:
        if 事件键 and p.exists():
            for ln in p.read_text(encoding="utf-8").splitlines():
                try:
                    if str(json.loads(ln).get("事件键") or "") == 事件键:
                        return
                except Exception:  # noqa: BLE001
                    continue
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


_读缓存: dict[str, tuple[float, list]] = {}  # 文件→(mtime, 解析结果)：/hall每2.5s轮询，别每次全量重读历史


def 读对话() -> list[dict[str, Any]]:
    """读出大厅的全部对话，给 看板数据.大厅() 当 `话` 气泡的正源（只读、不改任何文件）。
    带 mtime 缓存：只有当天在追加的文件会被重读，历史天文件读一次就缓存。
    返回大厅事件格式 [{k:"话", t, who, text, role}]；时间排序/按天分组交给 大厅()/前端。
    """
    出: list[dict[str, Any]] = []
    if not 对话目录.exists():
        return 出
    for p in sorted(对话目录.glob("*.jsonl")):
        mt = p.stat().st_mtime
        缓 = _读缓存.get(p.name)
        if 缓 and 缓[0] == mt:
            出.extend(缓[1])
            continue
        本文件: list[dict[str, Any]] = []
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                d = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
            who = str(d.get("who") or "")
            text = str(d.get("text") or "")
            if not who or not text:
                continue
            if d.get("会议id") and text.startswith("[会议]"):
                主题 = str(d.get("主题") or text.replace("[会议]", "", 1).strip())
                本文件.append({
                    "k": "会议",
                    "t": str(d.get("时间") or ""),
                    "who": who,
                    "text": text,
                    "role": who,
                    "会议id": str(d.get("会议id") or ""),
                    "主题": 主题,
                    "参会": d.get("参会") or [],
                    "状态": "已结束",
                })
            else:
                本文件.append({"k": "话", "t": str(d.get("时间") or ""), "who": who, "text": text, "role": who})
        _读缓存[p.name] = (mt, 本文件)
        出.extend(本文件)
    return 出


def 读某日(day: str) -> list[dict[str, Any]]:
    """按需读取某一天，兼容现役与压缩归档；不会把全部旧史塞进 `/hall`。"""
    import 大厅档案
    path = 大厅档案.找日(day, 对话目录)
    if path is None:
        return []
    本文件: list[dict[str, Any]] = []
    for ln in 大厅档案.读文本(path).splitlines():
        try:
            d = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        who = str(d.get("who") or "")
        text = str(d.get("text") or "")
        if not who or not text:
            continue
        if d.get("会议id") and text.startswith("[会议]"):
            本文件.append({
                "k": "会议", "t": str(d.get("时间") or ""), "who": who, "text": text, "role": who,
                "会议id": str(d.get("会议id") or ""),
                "主题": str(d.get("主题") or text.replace("[会议]", "", 1).strip()),
                "参会": d.get("参会") or [], "状态": "已结束",
            })
        else:
            本文件.append({"k": "话", "t": str(d.get("时间") or ""), "who": who, "text": text, "role": who})
    return 本文件


def 全部日期() -> list[str]:
    import 大厅档案
    return 大厅档案.日期列表(对话目录)


def 当前会话(记录: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从记录尾部往回切出"当前会话"：遇到 **跨自然日** 或 **静默>90分钟** 的空档就停——
    那之前是上一场，不带进当前上下文。治"跨天/跨会话的旧任务污染当前 agent 上下文、被误当当前活"
    （2026-07-09 船主拍板；抄 LangGraph thread-scoping，gap+跨天边界式）。
    **只给"喂 agent 上下文"的入口用**（判定门/聊天窗口/办事底盘）；读对话() 默认不动（前端要全天渲染）。"""
    if not 记录:
        return 记录
    import datetime as _dt

    def _ts(d: dict) -> Any:
        try:
            return _dt.datetime.strptime(str(d.get("t") or "")[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:  # noqa: BLE001
            return None
    out: list[dict[str, Any]] = []
    上时 = None
    for d in reversed(记录):
        t = _ts(d)
        if 上时 is not None and t is not None:
            if t.date() != 上时.date() or (上时 - t).total_seconds() > 会话静默间隔秒:
                break   # 跨天 或 大空档 = 上一场边界，停（那之前的旧活/旧话不进当前上下文）
        out.append(d)
        if t is not None:
            上时 = t
    out.reverse()
    return out
