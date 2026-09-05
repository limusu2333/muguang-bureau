#!/usr/bin/env python3
"""死平台 · 任务台：任何异步工作先登记，再执行，再明确收尾。

任务状态持久到 `运行状态/任务/*.json`。进程重启后，遗留的“已接收/运行中/收尾中”会被标成
“被重启中断”，只提示恢复或重做，不自动重跑，避免重复写文件、重复调用外部服务或重复记账。
"""
from __future__ import annotations

import datetime as dt
import threading
import uuid
from pathlib import Path
from typing import Any

from 状态存储 import 读JSON, 写JSON

from 根 import 数据根

COMPANY = 数据根
任务目录 = COMPANY / "运行状态" / "任务"
_锁 = threading.RLock()
_终态 = {"已完成", "失败", "已停止", "被重启中断", "待验收", "被门禁拦住"}
_运行态 = {"已接收", "运行中", "收尾中"}


def _时刻() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _安全id(raw: str) -> str:
    s = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(raw or "").strip())
    return s[:96] or ("任务-" + uuid.uuid4().hex[:12])


def 路径(task_id: str) -> Path:
    return 任务目录 / f"{_安全id(task_id)}.json"


def 读取(task_id: str) -> dict[str, Any] | None:
    p = 路径(task_id)
    return 读JSON(p, 默认=None, 类型=dict) if p.exists() else None


def 创建(类型: str, 来源: str, 内容: str, *, task_id: str = "", 负责人: str = "", 房间: str = "", 父任务: str = "") -> str:
    """先落“已接收”记录。重复使用同 id 会增加尝试次数并保留上次状态。"""
    tid = _安全id(task_id or ("任务-" + uuid.uuid4().hex[:12]))
    with _锁:
        旧 = 读取(tid) or {}
        now = _时刻()
        rec = {
            "schema": 1,
            "id": tid,
            "类型": str(类型 or "任务"),
            "来源": str(来源 or ""),
            "内容": str(内容 or "")[:2000],
            "负责人": str(负责人 or ""),
            "房间": str(房间 or ""),
            "父任务": str(父任务 or ""),
            "状态": "已接收",
            "创建时间": str(旧.get("创建时间") or now),
            "更新时间": now,
            "尝试": int(旧.get("尝试") or 0) + 1,
            "历史": list(旧.get("历史") or []),
            "结果": "",
            "错误": "",
        }
        if 旧:
            rec["历史"].append({"时间": now, "从": 旧.get("状态"), "到": "已接收", "说明": "再次启动"})
        写JSON(路径(tid), rec)
    return tid


def 更新(task_id: str, 状态: str, *, 结果: str = "", 错误: str = "", **字段: Any) -> dict[str, Any]:
    with _锁:
        rec = 读取(task_id)
        if rec is None:
            raise FileNotFoundError(f"任务记录不存在：{task_id}")
        old = str(rec.get("状态") or "")
        now = _时刻()
        if old != 状态:
            rec.setdefault("历史", []).append({"时间": now, "从": old, "到": 状态})
        rec["状态"] = 状态
        rec["更新时间"] = now
        if 结果:
            rec["结果"] = str(结果)[:4000]
        if 错误:
            rec["错误"] = str(错误)[:2000]
        for k, v in 字段.items():
            rec[k] = v
        写JSON(路径(task_id), rec)
        return rec


def 列表(*, 状态: set[str] | None = None, 限制: int = 100) -> list[dict[str, Any]]:
    if not 任务目录.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(任务目录.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            rec = 读JSON(p, 类型=dict)
        except Exception:  # 健康页会单独报状态损坏；列表不要拿空记录冒充正常
            continue
        if 状态 is None or str(rec.get("状态")) in 状态:
            out.append(rec)
        if len(out) >= 限制:
            break
    return out


def 恢复中断() -> list[str]:
    """开机时把上次未收尾任务标成中断。返回被标记的任务 id。"""
    ids: list[str] = []
    for rec in 列表(状态=_运行态, 限制=10000):
        tid = str(rec.get("id") or "")
        if not tid:
            continue
        更新(tid, "被重启中断", 错误="办公室进程重启前任务没有正常收尾；未自动重跑，避免重复副作用。")
        ids.append(tid)
    return ids


def 中断运行中(说明: str) -> list[str]:
    ids: list[str] = []
    for rec in 列表(状态=_运行态, 限制=10000):
        tid = str(rec.get("id") or "")
        if tid:
            更新(tid, "被重启中断", 错误=说明)
            ids.append(tid)
    return ids


def 运行中() -> list[dict[str, Any]]:
    return 列表(状态=_运行态)


def 中断项() -> list[dict[str, Any]]:
    return 列表(状态={"被重启中断", "失败", "被门禁拦住"})
