#!/usr/bin/env python3
"""船主自然语言直控公司 · 管理动作通道（2026-07-11 船主定的方向）。

船主用大白话下管理指令（平反/…以后扩） → 模型听出意图、抽参数 → 生成一张『待确认卡』
→ 船主三选一：【确认执行】才真办、【说清楚点】当场纠正重解、【取消】丢弃。

确认门是双保险：① 只有船主能点＝防越权（本就该谁都不能靠嘴改账）；
② 模型把话理解错、船主不点确认就什么都不发生＝防误解伤账。
舟只负责『搭这条通道』；运行时是船主说话、系统办，不经舟的手。

第一批只接『平反』（误打回），把整条通道跑通；调级/升降/表扬/叫停等以后挂到同一条通道。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from 状态存储 import 读JSON, 写JSON

from 根 import 数据根
from 实例配置 import 主人显示名

COMPANY = 数据根
待确认 = COMPANY / "管理动作" / "待确认"
执行中 = COMPANY / "管理动作" / "执行中"
已办 = COMPANY / "管理动作" / "已办"
_锁 = threading.RLock()   # 卡操作串行：确认/取消/重解 并发或双击时别双执行——ThreadingHTTPServer 请求并行，无锁会双扣分/多撤打回（十审#2）


def 初始化() -> None:
    for d in (待确认, 执行中, 已办):
        d.mkdir(parents=True, exist_ok=True)


def _时刻() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _写(p: Path, data: dict[str, Any]) -> None:
    写JSON(p, data)


def _读(p: Path) -> dict[str, Any]:
    return 读JSON(p, 类型=dict)


def _找(cid: str) -> Path | None:
    初始化()
    for d in (待确认, 执行中, 已办):
        p = d / cid
        if p.exists():
            return p
    return None


def _清缓存() -> None:
    try:
        import 看板数据
        看板数据.清待办缓存()
    except Exception:  # noqa: BLE001
        pass


# ── 动作表：每个管理动作 = 怎么执行 + 怎么用人话说清『要干嘛/影响谁』+ 哪些参数必填 ──────
def _执行_平反(参数: dict[str, str]) -> None:
    import 信誉
    信誉.误打回(
        参数.get("把关者", ""), 参数.get("被冤者", ""),
        参数.get("理由") or "船主回看判定打回有误", 参数.get("键", ""),
    )


动作表: dict[str, dict[str, Any]] = {
    "平反": {
        "执行": _执行_平反,
        "幂等": True,
        "必填": ("把关者", "被冤者"),
        "释义": lambda p: f"把「{p.get('把关者') or '？'}」给「{p.get('被冤者') or '？'}」的那次打回作废、还「{p.get('被冤者') or '？'}」清白，并记「{p.get('把关者') or '？'}」一次『误打回』。",
        "影响": lambda p: f"{p.get('被冤者') or '？'} 撤销那笔『交付被打回』的扣分（连带失依据的『反复敷衍』一并撤）；{p.get('把关者') or '？'} 记一次『误打回』（-2，乱打回的代价）。",
    },
}


# ── 意图识别：船主一句话 → {动作, 参数} 或 None（不是管理指令/拿不准）──────────────────
def _判官岗() -> str:
    try:
        import 部门
        import 职级
        m = 职级.现任经理()
        if m:
            return 部门._人名到岗位(m) or "项目经理"
    except Exception:  # noqa: BLE001
        pass
    return "项目经理"


def _抽json(raw: str) -> dict[str, Any] | None:
    s, e = raw.find("{"), raw.rfind("}") + 1
    if s < 0 or e <= s:
        return None
    try:
        obj = json.loads(raw[s:e])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _mock识别(话: str) -> dict[str, Any] | None:
    if not any(k in 话 for k in ("平反", "判错", "打回错", "冤枉", "错怪")):
        return None
    m = re.search(r"([一-龥A-Za-z0-9_]{1,6})给([一-龥A-Za-z0-9_]{1,6})", 话)
    把, 冤 = (m.group(1), m.group(2)) if m else ("", "")
    return {"动作": "平反", "参数": {"把关者": 把, "被冤者": 冤, "理由": "船主判定打回有误", "键": ""}}


def 识别管理动作(话: str, mock: bool = False) -> dict[str, Any] | None:
    """船主这句话是不是一条管理指令；是就返回 {动作, 参数}，否则 None。拿不准一律 None（宁可放过，靠船主再说清；确认门兜底）。"""
    话 = str(话 or "").strip()
    if not 话:
        return None
    if mock:
        return _mock识别(话)
    from 模型接入 import 调用
    prompt = (
        f"你是开发公司的意图判官。船主（{主人显示名()}）刚说了一句话，判断这是不是一条『管理指令』"
        "（要对公司里某个人执行一个管理动作），是就抽出动作和参数。**只输出 JSON、不要 Markdown、不要解释。**\n"
        "现在只支持这些管理动作：\n"
        "- 平反：船主认为『某人 A 给 某人 B 的一次打回判错了』，要作废它、还 B 清白、并记 A 一次误打回。"
        "参数 把关者=打回别人的 A、被冤者=被打回的 B。\n"
        '输出格式：{"动作":"平反|无","把关者":"","被冤者":"","理由":""}。\n'
        '**只要不是明确的管理指令（普通聊天/派活/请示/问问题），或你拿不准，就输出 {"动作":"无"}**——'
        "宁可放过、绝不乱触发；船主真要执行会再说清楚。\n\n"
        f"船主说：{话}"
    )
    try:
        raw = 调用(_判官岗(), prompt, 最大tokens=256)
    except Exception:  # noqa: BLE001 判官叫不醒＝当没识别出，别拦住船主说话
        return None
    obj = _抽json(raw)
    if not obj:
        return None
    动作 = str(obj.get("动作", "") or "").strip()
    if 动作 not in 动作表:
        return None
    参数 = {k: str(obj.get(k, "") or "").strip() for k in ("把关者", "被冤者", "理由", "键")}
    return {"动作": 动作, "参数": 参数}


# ── 卡的一生：建卡 → 待确认 → 确认执行 / 取消 / 说清楚点重解 ────────────────────────────
def 建卡(动作: str, 参数: dict[str, str], 原话: str) -> str:
    初始化()
    d = 动作表[动作]
    cid = f"{动作}-{dt.datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}.json"
    data = {
        "id": cid, "原话": str(原话 or ""), "动作": 动作, "参数": 参数,
        "释义": d["释义"](参数), "影响": d["影响"](参数),
        "缺参": [k for k in d["必填"] if not 参数.get(k)],
        "状态": "待确认", "创建时间": _时刻(),
    }
    _写(待确认 / cid, data)
    _清缓存()
    return cid


def 从船主话建卡(话: str, mock: bool = False) -> str | None:
    """船主大厅发话的总入口：识别出管理动作就建卡、返回卡号；不是管理指令返回 None（让大厅照常走派活/请示）。"""
    识 = 识别管理动作(话, mock=mock)
    if not 识:
        return None
    return 建卡(识["动作"], 识["参数"], 话)


def 确认执行(cid: str) -> dict[str, Any]:
    with _锁:
        p = _找(cid)
        if p is None or p.parent == 已办:
            return {"ok": False, "error": "卡不存在或已处理"}
        data = _读(p)
        if data.get("缺参"):
            return {"ok": False, "error": "信息不全（缺 " + "、".join(data["缺参"]) + "）——请点『说清楚点』补上再确认"}
        spec = 动作表.get(str(data.get("动作") or "")) or {}
        if not spec.get("幂等"):
            return {"ok": False, "error": "该管理动作没有声明幂等执行，平台拒绝运行，防止重启后重复生效"}
        if p.parent == 待确认:
            data["状态"] = "执行中"
            data["执行开始"] = _时刻()
            data["幂等键"] = cid
            _写(执行中 / cid, data)   # 先落执行意图，再删待确认；崩了也能从执行中恢复
            p.unlink(missing_ok=True)
            p = 执行中 / cid
        return _继续执行(p, data)


def _继续执行(p: Path, data: dict[str, Any]) -> dict[str, Any]:
    """继续一张“执行中”卡。动作必须按自身语义幂等，重复调用不得重复产生副作用。"""
    try:
        参数 = dict(data.get("参数") or {})
        参数["动作键"] = str(data.get("幂等键") or data.get("id") or p.name)
        动作表[data["动作"]]["执行"](参数)
    except Exception as e:  # noqa: BLE001
        data["状态"] = "执行失败·待重试"
        data["最后错误"] = f"{type(e).__name__}: {e}"[:500]
        data["最后尝试"] = _时刻()
        _写(p, data)
        return {"ok": False, "error": data["最后错误"]}
    data["状态"] = "已执行"
    data["执行时间"] = _时刻()
    data.pop("最后错误", None)
    _写(已办 / p.name, data)   # 先写已办，再删执行中；动作已幂等，任何接缝都可重试
    p.unlink(missing_ok=True)
    _清缓存()
    return {"ok": True, "释义": data.get("释义", "")}


def 恢复执行中() -> list[dict[str, Any]]:
    """开机重试上次没收尾的执行中卡。返回每张卡的处理结果。"""
    初始化()
    out = []
    with _锁:
        for p in sorted(执行中.glob("*.json")):
            try:
                out.append({"卡": p.name, **_继续执行(p, _读(p))})
            except Exception as e:  # noqa: BLE001
                out.append({"卡": p.name, "ok": False, "error": f"{type(e).__name__}: {e}"})
    return out


def 取消(cid: str) -> dict[str, Any]:
    with _锁:
        p = _找(cid)
        if p is None or p.parent != 待确认:
            return {"ok": False, "error": "卡不存在或已处理"}
        data = _读(p)
        data["状态"] = "已取消"
        data["取消时间"] = _时刻()
        _写(已办 / cid, data)
        p.unlink(missing_ok=True)
        _清缓存()
        return {"ok": True}


def 重解(cid: str, 补充: str, mock: bool = False) -> dict[str, Any]:
    """『说清楚点』：船主补一句 → 连同原话重新识别 → 作废旧卡、出一张改好的新卡（或撤掉）。"""
    with _锁:
        p = _找(cid)
        if p is None or p.parent != 待确认:
            return {"ok": False, "error": "卡不存在或已处理"}
        data = _读(p)
        合并 = f"{data.get('原话', '')}\n（船主补充说清楚）：{str(补充 or '').strip()}"
        新 = 识别管理动作(合并, mock=mock)
        data["状态"] = "已重解"
        data["重解时间"] = _时刻()
        _写(已办 / cid, data)
        p.unlink(missing_ok=True)
        if not 新:
            _清缓存()
            return {"ok": True, "新卡": None, "说明": "补充后仍没识别成明确的管理动作，这张卡撤了；要执行请再说清楚。"}
        新cid = 建卡(新["动作"], 新["参数"], 合并)
        return {"ok": True, "新卡": 新cid}


def 待确认列表() -> list[dict[str, Any]]:
    初始化()
    return [_读(p) for p in sorted(待确认.glob("*.json"))]   # FIFO：先下的指令先弹（十审B9：原 reverse=True 是后说先弹、同秒还随机）


if __name__ == "__main__":
    # 自检：建卡(确定参数)→确认执行 全链，验证确认真调 误打回；并测 取消/重解 不误伤
    import 信誉
    冤, 把 = "_自检被冤", "_自检把关"
    for k in ("z1", "z2", "z3"):
        信誉.记打回(冤, "活", 键=k)
    print("平反前 有近期重罚(被冤)=", 信誉.有近期重罚(冤))
    cid = 建卡("平反", {"把关者": 把, "被冤者": 冤, "理由": "船主判定打回有误", "键": ""}, "老X给被冤那次判错了，平反")
    卡 = _读(_找(cid))
    print("【卡·释义】", 卡["释义"])
    print("【卡·影响】", 卡["影响"])
    print("取消一张不执行：", 取消(建卡("平反", {"把关者": 把, "被冤者": 冤}, "试")))
    print("确认执行：", 确认执行(cid))
    print("平反后 有近期重罚(被冤)=", 信誉.有近期重罚(冤), "（应 True→False）")
    信誉._账(冤).unlink(missing_ok=True)
    信誉._账(把).unlink(missing_ok=True)
    for p in list(已办.glob("*.json")):
        p.unlink(missing_ok=True)
    print("自检账已清")
