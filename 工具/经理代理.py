#!/usr/bin/env python3
"""经理临时代理租约。

经理本人掉线时，本单可由职级最高的可用同事临时承接统筹；这不是升职，
不改职级账。原经理下一单能到岗就自动恢复。
"""
from __future__ import annotations

import contextvars
import datetime as dt

_租约: dict[str, dict] = {}
_临时统筹人: contextvars.ContextVar[str] = contextvars.ContextVar("临时统筹人", default="")


def _花名册() -> dict:
    import 模型接入

    return 模型接入.花名册()


def 岗位人名(岗位: str) -> str:
    cfg = (_花名册().get(str(岗位 or "")) or {})
    return str(cfg.get("名字") or 岗位 or "").strip()


def 岗位按人名(人名: str) -> str | None:
    人名 = str(人名 or "").strip()
    if not 人名:
        return None
    for 岗, cfg in _花名册().items():
        if str((cfg or {}).get("名字") or "").strip() == 人名:
            return str(岗)
    return None


def 候选代理岗位(排除岗位=(), 排除人名=()) -> tuple[str | None, str | None]:
    """按职级和信誉选临时统筹人；只读职级，不写晋升/降职。"""
    import 职级
    import 信誉

    排岗 = {str(x).strip() for x in (排除岗位 or []) if str(x).strip()}
    排人 = {str(x).strip() for x in (排除人名 or []) if str(x).strip()}
    候选: list[tuple[float, float, int, str, str]] = []
    for 岗, cfg in _花名册().items():
        岗 = str(岗)
        人 = str((cfg or {}).get("名字") or "").strip()
        if not 人 or 岗 in 排岗 or 人 in 排人:
            continue
        try:
            级 = float(职级.评级(人))
            信 = float(信誉.工作态信誉分(人))
        except Exception:  # noqa: BLE001
            continue
        rank = int((cfg or {}).get("rank") or 999)
        候选.append((级, 信, -rank, 岗, 人))
    if not 候选:
        return None, None
    _级, _信, _rank, 岗, 人 = max(候选)
    return 岗, 人


def 确保租约(key: str, 原岗位: str = "项目经理", 排除岗位=(), 排除人名=(), 原因: str = "") -> dict:
    key = str(key or "大厅")
    已有 = _租约.get(key)
    if 已有:
        return dict(已有)
    原人 = 岗位人名(原岗位)
    排岗 = {str(x).strip() for x in (排除岗位 or []) if str(x).strip()}
    排人 = {str(x).strip() for x in (排除人名 or []) if str(x).strip()}
    排岗.add(str(原岗位))
    if 原人:
        排人.add(原人)
    代理岗, 代理人 = 候选代理岗位(排除岗位=排岗, 排除人名=排人)
    if not 代理岗 or not 代理人:
        raise RuntimeError("没有可承接的临时代理经理")
    lease = {
        "key": key,
        "原岗位": str(原岗位),
        "原经理": 原人,
        "代理岗位": 代理岗,
        "代理人": 代理人,
        "原因": str(原因 or ""),
        "创建时间": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _租约[key] = dict(lease)
    return lease


def 清租约(key: str) -> None:
    _租约.pop(str(key or "大厅"), None)


def 当前租约(key: str) -> dict | None:
    lease = _租约.get(str(key or "大厅"))
    return dict(lease) if lease else None


def 当前临时统筹(key: str = "") -> tuple[str | None, str | None]:
    if key:
        lease = _租约.get(str(key or "大厅"))
        if lease:
            return str(lease.get("代理岗位") or ""), str(lease.get("代理人") or "")
    人 = _临时统筹人.get()
    if not 人:
        return None, None
    return 岗位按人名(人), 人


def 进入临时统筹(租约: dict):
    return _临时统筹人.set(str((租约 or {}).get("代理人") or ""))


def 退出临时统筹(token) -> None:
    _临时统筹人.reset(token)


def 是临时统筹(人名: str) -> bool:
    当前 = _临时统筹人.get()
    return bool(当前) and str(人名 or "").strip() == 当前
