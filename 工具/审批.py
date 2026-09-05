#!/usr/bin/env python3
"""办公室v3审批卡：员工请示 -> 项目经理预审 -> 船主裁决。"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from 根 import 数据根

COMPANY = 数据根
待经理 = COMPANY / "请示" / "待经理"
待船主 = COMPANY / "请示" / "待船主"
已决 = COMPANY / "请示" / "已决"


def 初始化() -> None:
    for d in (待经理, 待船主, 已决):
        d.mkdir(parents=True, exist_ok=True)


def _时刻() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _安全名(s: str) -> str:
    t = re.sub(r"[^\w.-]+", "_", s, flags=re.ASCII).strip("_")
    return t[:48] or "card"


def _写(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _读(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _找(card_id: str) -> Path | None:
    初始化()
    for d in (待经理, 待船主, 已决):
        p = d / card_id
        if p.exists():
            return p
        hits = list(d.glob(f"{card_id}*.json"))
        if hits:
            return hits[0]
    return None


def 创建请示(任务: str, 岗位: str, 内容: str, 建议: str = "", 直接上报: bool = False) -> str:
    初始化()
    cid = f"{_安全名(Path(任务).stem)}-{dt.datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}.json"
    data: dict[str, Any] = {
        "id": cid,
        "任务": 任务,
        "岗位": 岗位,
        "内容": 内容,
        "建议": 建议,
        # 缺证据这类只能船主提供/授权的，直接上报船主，不在项目经理权限内（圆桌查证用）
        "状态": "待船主" if 直接上报 else "待经理",
        "创建时间": _时刻(),
        "经理理由": "（缺证据，超出项目经理权限，直接上报船主）" if 直接上报 else "",
        "船主理由": "",
    }
    _写((待船主 if 直接上报 else 待经理) / cid, data)
    return cid


def 经理预审(card_id: str, mock: bool = False) -> dict[str, Any]:
    path = _找(card_id)
    if path is None or path.parent != 待经理:
        raise FileNotFoundError(f"待经理请示不存在: {card_id}")
    data = _读(path)
    if mock:
        verdict = _本地规则预审(data)
    else:
        verdict = _调用项目经理(data)
    裁 = str(verdict.get("裁决", "")).strip()
    理由 = str(verdict.get("理由", ""))
    if 裁 not in {"批", "驳", "上报"}:
        raise ValueError(f"项目经理裁决格式异常，卡片仍留在待经理：{verdict}")
    data["经理裁决"] = 裁
    data["经理理由"] = 理由
    report = {
        "理由": 理由,
        "依据": verdict.get("依据", ""),
        "建议": verdict.get("建议", ""),
        "边界": verdict.get("边界", ""),
        "需补技能": verdict.get("需补技能", ""),
    }
    data["经理报告"] = {k: v for k, v in report.items() if v}
    if str(verdict.get("追加步数", "")).strip():
        try:
            data["追加步数"] = max(1, min(8, int(verdict.get("追加步数"))))
        except (TypeError, ValueError):
            data["追加步数"] = 2
    data["经理时间"] = _时刻()
    # L15 二审：先写目标、写成功再删源卡——原来先删后写，写崩(磁盘满/序列化失败)就把请示弄丢还回报"已排队"
    data["状态"] = "待船主" if 裁 == "上报" else 裁
    _写((待船主 if 裁 == "上报" else 已决) / data["id"], data)
    path.unlink(missing_ok=True)
    return data


def _经理岗() -> str:
    """此刻握审批权的经理坐的岗位；经理身份不明时拒绝猜旧岗位。"""
    import 部门
    import 职级

    m = 职级.现任经理()
    if not m:
        raise RuntimeError("现任经理无法确定，经理预审已暂停")
    岗 = 部门._人名到岗位(m)
    if not 岗:
        raise RuntimeError(f"现任经理『{m}』不在花名册，经理预审已暂停")
    return 岗


def _调用项目经理(data: dict[str, Any]) -> dict[str, Any]:
    from 模型接入 import 调用, 花名册

    经理岗 = _经理岗()
    经理模型 = str(花名册().get(经理岗, {}).get("model") or "花名册登记模型")
    prompt = (
        f"你是开发公司的项目经理，当前由 {经理模型} 承担。你的职责不是转发，而是判断。"
        "所有一级审批都先由你裁决；拿得准必须批或驳，不能把责任上推给船主。"
        "只有按公司制度仍拿不准，或触发费用、密钥/账号、删除/覆盖、架构/项目方向、公司利益受损、第三方skill/脚本安装、模型真实能力冲突等红线，才上报。"
        "如果只是预算过紧，但仍在原白名单、无新增权限、无新增费用、只是完成自检/收尾/低风险核验，你必须自己批准追加少量步数，并说明这是项目经理预算估算责任。"
        "如果你发现自己缺少判断依据或岗位技能，先说明缺口；需要安装第三方skill时上报，需要只读学习或查本地技能时可批准。"
        "请审查下面员工请示，只输出JSON，不要Markdown。"
        '格式为 {"裁决":"批|驳|上报","理由":"一句话结论","依据":["工单目标/白名单/日志/班规等证据"],"建议":"下一步怎么做","边界":"批准后的限制","追加步数":2,"需补技能":["没有就写无"]}。\n\n'
        "审批准则：\n"
        "- 批：白名单同域只读扩展、必要且低成本的真实核验、不改变项目资料的检查动作。\n"
        "- 批：预算超限但只是自检/收尾/读回/交给测试，且不扩大白名单、不新增费用、不碰密钥、不删除覆盖重要资料。\n"
        "- 驳：越界写入、无必要扩大权限、可能泄密、与任务无关、能用更小权限完成。\n"
        "- 上报：你拿不准，或明显增加费用、密钥/账号、删除/覆盖重要资料、架构/项目方向、公司利益可能受损、第三方skill/脚本安装、登记值与现场值冲突且影响交付结论。\n"
        "理由必须说明依据：工单目标、白名单、班规、成本、风险或公司利益。"
        "禁止只写“拿不准”；拿不准时必须写清缺少哪条证据、为什么不能由你裁决。\n\n"
        f"任务: {data.get('任务')}\n岗位: {data.get('岗位')}\n"
        f"内容:\n{data.get('内容')}\n建议: {data.get('建议')}"
    )
    raw = 调用(经理岗, prompt, 最大tokens=512)   # 唤醒此刻真正的经理（评级驱动，可能已不是老钟）
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError("项目经理没有返回裁决 JSON，卡片仍留在待经理")
    try:
        obj = json.loads(raw[start:end])
    except json.JSONDecodeError as e:
        raise ValueError("项目经理裁决 JSON 损坏，卡片仍留在待经理") from e
    if not isinstance(obj, dict):
        raise ValueError("项目经理裁决不是对象，卡片仍留在待经理")
    return obj


def _本地规则预审(data: dict[str, Any]) -> dict[str, Any]:
    text = f"{data.get('内容', '')}\n{data.get('建议', '')}"
    hard_report = (
        "费用", "大量调用", "密钥", "账号", "删除", "覆盖", "架构", "项目方向",
        "公司利益", "第三方", "安装", "脚本", "供应链", "真实能力冲突",
        "她是谁", "主动", "人格", "档案",
    )
    deny = ("白名单外写入", "越界写", "泄露密钥", "无关", "删除公司资料")
    approve = ("只读", "核验岗位", "model字段", "读取", "查看", "低成本核验")
    low_risk_budget = (
        ("超过预算步数" in text or "预算超限" in text)
        and any(x in text for x in ("白名单", "同白名单", "读回", "自检", "收尾", "复盘", "待测试"))
    )
    if low_risk_budget:
        return {
            "裁决": "批",
            "理由": "项目经理批准追加预算：当前超步属于预算估算不足，仍在原白名单内，只需完成自检/收尾，不应上推船主。",
            "依据": ["预算超限", "原白名单未扩大", "未触发费用/密钥/删除/架构红线"],
            "建议": "追加2步继续，先完成自检复盘，再交给后续验收。",
            "边界": "不得新增白名单、不得改动无关文件；若再次超步或出现新权限需求再重新审批。",
            "追加步数": 2,
            "需补技能": ["项目经理预算估算"],
        }
    if any(x in text for x in hard_report):
        return {"裁决": "上报", "理由": "触发上报红线：涉及费用/密钥/删除/架构/公司利益/第三方安装/项目方向或项目经理无法独立判断的风险。"}
    if any(x in text for x in deny):
        return {"裁决": "驳", "理由": "按班规驳回：请求包含越界写入、泄密或与任务无关风险。"}
    if any(x in text for x in approve):
        return {"裁决": "批", "理由": "按项目经理一级审批批准：低成本只读核验，未改变项目资料，且有助于完成当前工单。"}
    return {"裁决": "上报", "理由": "mock本地规则无法充分判断，按拿不准标准上报。"}


def 船主裁决(card_id: str, 裁决: str, 理由: str = "") -> dict[str, Any]:
    path = _找(card_id)
    if path is None or path.parent != 待船主:
        raise FileNotFoundError(f"待船主请示不存在: {card_id}")
    if 裁决 not in {"批", "驳"}:
        raise ValueError("船主裁决只能是 批 或 驳")
    data = _读(path)
    data["状态"] = 裁决
    data["船主理由"] = 理由
    data["船主时间"] = _时刻()
    _写(已决 / data["id"], data)   # L15：先写已决、再删源，写崩不丢卡
    path.unlink(missing_ok=True)
    try:   # M11 二审：清缓存下沉到这里——不管从哪个端点裁决，看板下次读都是新的（省得每个调用点各记各的、漏一个就前端过时）
        import 看板数据
        看板数据.清待办缓存()
    except Exception:  # noqa: BLE001
        pass
    return data


def 读取裁决(card_id: str) -> dict[str, Any] | None:
    path = _找(card_id)
    if path is None or path.parent != 已决:
        return None
    return _读(path)


def 列表() -> dict[str, list[dict[str, Any]]]:
    初始化()
    out: dict[str, list[dict[str, Any]]] = {"待经理": [], "待船主": [], "已决": []}
    for name, d in (("待经理", 待经理), ("待船主", 待船主), ("已决", 已决)):
        for p in sorted(d.glob("*.json"), reverse=True):
            out[name].append(_读(p))
    return out
