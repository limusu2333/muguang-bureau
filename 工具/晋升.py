#!/usr/bin/env python3
"""死平台 · 晋升曲线 —— 晋级的长跑（和信誉分开：信誉管信任/自主度、快；这条管升职、慢且难）。

船主拍板 2026-07-10：
- 增加要**非常努力**才达标——不是一两个任务，得十几二十个稳定成果、还不能错太多。
- 正常衰减由本人后续**真实工作批次**推进（约 10 批次减半）；仅仅不开公司只缓慢回落最多 5%，随后近乎停滞。
- **达标→冻结曲线（停衰减）→进 5 笔试用**：试用里保持准确（最多错1笔）→晋升；错到2笔→**清零**（晋升分归零）
  +解冻+恢复正常掉落，重新爬直到再达标。

一条人一条曲线，状态落 `职级/晋升.json`：{人名: {分, 时间, 冻结, 准, 错}}。
喂给它的是"任务结果"：验收通过按任务难度与完成质量加分，打回/偷懒/放水=错(−)。谁攒够+过试用，由上层调 职级.升。
"""
from __future__ import annotations

import datetime as dt
import json
import math
import threading
from pathlib import Path

from 状态存储 import 读JSON, 写JSON

from 根 import 数据根

COMPANY = 数据根
曲线文件 = COMPANY / "职级" / "晋升.json"
_锁 = threading.RLock()

# ── 曲线参数（都是一行常量、随船主手感调）──────────────────────────────────
# 设计（以每个真实任务批次为一步）：旧成果每步淡出，只有继续交付才能推进；休眠不等于工作。
半衰任务 = 10.0      # 本人每完成约 10 个真实工作批次，旧晋升分减半
半衰天 = 半衰任务    # 兼容旧调用名；单位已从自然日改成真实工作批次
休眠最多回落 = 0.05  # 长期不开工只有限度地缓慢回落，最多 5%
休眠趋稳天 = 3.0
休眠宽限天 = 1.0
增量 = 3.0          # 每个验收通过 +3
罚 = 9.0            # 每个打回/偷懒/放水 −9（错一次≈抵三次成功——不能错太多）
达标线 = {2: 5.0, 3: 28.0, 4: 34.0, 5: 39.0}   # 级2=转正(实习→正式)门槛低≈2个成果、不走试用；级3+才是长跑(主管≈15/副≈21/经理≈29个成果、还不能错)
试用窗 = 5          # 达标后 5 笔试用
试用容错 = 1        # 5 笔里最多错 1 笔；错到 2 笔就清零


def _达标线(目标级: int) -> float:
    return 达标线.get(目标级, 9e9)


# ── 存储 ────────────────────────────────────────────────────────────────
def _读() -> dict:
    return 读JSON(曲线文件, 默认={}, 类型=dict) or {}


def _存全(表: dict) -> None:
    写JSON(曲线文件, 表)


def _新rec(此刻: dt.datetime) -> dict:
    return {"分": 0.0, "时间": 此刻.strftime("%Y-%m-%d %H:%M"), "冻结": False, "准": 0, "错": 0, "已处理": []}


def _衰减后(rec: dict, 此刻: dt.datetime) -> float:
    """大厅显示用的休眠回落：有界、趋稳；冻结期仍不衰减。"""
    分 = float(rec.get("分", 0.0) or 0.0)
    if rec.get("冻结"):
        return 分
    try:
        t = dt.datetime.strptime(str(rec.get("时间") or "")[:16], "%Y-%m-%d %H:%M")
        龄 = max(0.0, (此刻 - t).total_seconds() / 86400.0 - 休眠宽限天)
        倍率 = 1.0 - 休眠最多回落 * (1.0 - math.exp(-龄 / 休眠趋稳天))
        return 分 * 倍率
    except Exception:  # noqa: BLE001
        return 分


def 分(人名: str, 此刻: dt.datetime | None = None) -> float:
    此刻 = 此刻 or dt.datetime.now()
    rec = _读().get(str(人名 or "").strip())
    return round(_衰减后(rec, 此刻), 1) if rec else 0.0


def 状态(人名: str) -> dict:
    """给前端/画像看：{分, 冻结, 试用准, 试用错, 达标线(下一级用上层给)}。"""
    人名 = str(人名 or "").strip()
    rec = _读().get(人名) or _新rec(dt.datetime.now())
    return {"分": 分(人名), "冻结": bool(rec.get("冻结")), "试用准": int(rec.get("准", 0)), "试用错": int(rec.get("错", 0))}


def 已处理(人名: str, 结果键: str) -> bool:
    rec = _读().get(str(人名 or "").strip()) or {}
    return bool(结果键) and str(结果键) in set(rec.get("已处理") or [])


def 记交付(人名: str, 当前级: int, 成功: bool, 此刻: dt.datetime | None = None, 结果键: str = "",
       成果分: float | None = None) -> str | None:
    """喂一笔任务结果进曲线。返回事件：
      '达标冻结' —— 分攒到达标线，冻结进试用；
      '晋升'     —— 试用期保持了准确（满5笔、错≤1），该升级了（上层据此调 职级.升）；
      '清零'     —— 试用期错太多（错到2笔），曲线归零、解冻、重新爬；
      None       —— 普通累积/试用中，无大事。
    """
    人名 = str(人名 or "").strip()
    if not 人名:
        return None
    此刻 = 此刻 or dt.datetime.now()
    目标 = int(当前级) + 1
    ts = 此刻.strftime("%Y-%m-%d %H:%M")
    结果键 = str(结果键 or "").strip()
    with _锁:
        表 = _读()
        rec = 表.get(人名) or _新rec(此刻)
        已处理 = list(rec.get("已处理") or [])
        if 结果键 and 结果键 in 已处理:
            return None
        事件 = None
        实得 = 增量 if 成果分 is None else max(0.0, float(成果分))
        if rec.get("冻结"):
            # 试用期：数准/错，冻结不衰减
            if int(rec.get("准", 0)) >= 试用窗:
                # 3H1 三审：已通过试用、只是还没真升成（等赢坑/信誉够）——保持『就绪』、别再数错、别重置曲线，让上层反复试升到成功
                事件 = "晋升就绪"
            else:
                if 成功 and 实得 > 0:   # 同批拆单已吃满奖励时，不靠多拆几张单冲完试用
                    rec["准"] = int(rec.get("准", 0)) + 1
                elif not 成功:
                    rec["错"] = int(rec.get("错", 0)) + 1
                if rec["错"] >= 试用容错 + 1:          # 错太多 → 清零重爬
                    rec = _新rec(此刻)
                    rec["已处理"] = 已处理
                    事件 = "清零"
                elif rec["准"] >= 试用窗:               # 保持了准确 → 晋升就绪（不重置！升成后由上层调 清曲线，避免坑满白清+假公告）
                    事件 = "晋升就绪"
        else:
            # 爬坡期：先衰减到此刻，再加/扣
            当前 = _衰减后(rec, 此刻)
            当前 *= 0.5 ** (1.0 / 半衰任务)   # 真实交付才推进正常衰减；仅打开页面不会动
            当前 += 实得 if 成功 else -罚
            当前 = max(0.0, 当前)
            rec = {"分": round(当前, 3), "时间": ts, "冻结": False, "准": 0, "错": 0}
            if 目标 <= 5 and 当前 >= _达标线(目标):
                if 目标 <= 2:                          # 转正(实习→正式)：达标即升就绪，不走试用（基层）；升成后上层清曲线
                    事件 = "晋升就绪"
                else:                                  # 级3+：达标 → 冻结进 5 笔试用
                    rec["冻结"] = True
                    事件 = "达标冻结"
        if 结果键:
            已处理.append(结果键)
            rec["已处理"] = 已处理[-1000:]
        else:
            rec.setdefault("已处理", 已处理)
        表[人名] = rec
        _存全(表)
    return 事件


def 清曲线(人名: str) -> None:
    """船主手调级/调任后重置某人的晋升曲线（在新级别从头爬）。"""
    人名 = str(人名 or "").strip()
    with _锁:
        表 = _读()
        if 人名 in 表:
            已处理 = list((表.get(人名) or {}).get("已处理") or [])
            rec = _新rec(dt.datetime.now())
            rec["已处理"] = 已处理[-1000:]
            表[人名] = rec
            _存全(表)


if __name__ == "__main__":
    print("参数：", {"半衰任务": 半衰任务, "增量": 增量, "罚": 罚, "达标线": 达标线})
