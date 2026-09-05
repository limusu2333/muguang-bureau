#!/usr/bin/env python3
"""项目室历史协同层：展示旧工单项目会、交办、回执和可追溯报告。

现役活公司开会走 会议室.py；本模块只给旧项目室和历史看板保留可读记录。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
from pathlib import Path
from typing import Any

from 引擎工具 import COMPANY, 工单, 控制, 日志, 原子写, 时刻, 解析工单, 读控制

协同根 = COMPANY / "协同"
会议根 = 协同根 / "会议"
_锁 = threading.RLock()


def _列表化(v: Any) -> list[str]:
    """防 bug2:模型有时把『验收标准/资料需求』返回成 str(本该是 list)。
    str→整条作一项(空则空表);list/tuple→逐项转 str 去空白;其他→空表。
    避免 `for x in 字符串` 逐字符迭代出『- 交\\n- 付\\n- 物』。"""
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


def 初始化() -> None:
    会议根.mkdir(parents=True, exist_ok=True)


def _安全片段(text: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-")
    return (out or "meeting")[:48]


def _会议路径(mid: str) -> Path:
    return 会议根 / f"{mid}.json"


def _纪要路径(mid: str) -> Path:
    return 会议根 / f"{mid}.md"


def _读(mid: str) -> dict[str, Any]:
    p = _会议路径(mid)
    if not p.exists():
        raise FileNotFoundError(f"协同会议不存在: {mid}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("协同会议数据损坏")
    data.setdefault("事件", [])
    return data


def _写(data: dict[str, Any]) -> None:
    初始化()
    mid = str(data["id"])
    data["更新时间"] = 时刻()
    原子写(_会议路径(mid), json.dumps(data, ensure_ascii=False, indent=2))
    原子写(_纪要路径(mid), _渲染纪要(data))


def _渲染纪要(data: dict[str, Any]) -> str:
    lines = [
        f"# {data.get('类型', '会议')} · {data.get('标题', '')}",
        f"会议ID: {data.get('id', '')}",
        f"项目室: {data.get('项目室', '')}",
        f"主席: {data.get('主席', '')}",
        f"状态: {data.get('状态', '')}",
        f"参会: {'、'.join(map(str, data.get('参会', [])))}",
        "",
        "## 议程",
        str(data.get("议程", "") or "无"),
        "",
        "## 实时事件",
    ]
    for ev in data.get("事件", []):
        target = f" -> {ev.get('目标')}" if ev.get("目标") else ""
        task = f"（{ev.get('关联工单')}）" if ev.get("关联工单") else ""
        receipt = "｜需回执" if ev.get("需回执") else ""
        lines.append(
            f"- [{ev.get('时间', '')}] {ev.get('类型', '')}｜{ev.get('发言人', '')}{target}{task}{receipt}\n"
            f"  {str(ev.get('内容', '')).replace(chr(10), chr(10) + '  ')}"
        )
    return "\n".join(lines).rstrip() + "\n"


def 创建会议(
    项目室: str,
    类型: str,
    标题: str,
    主席: str,
    参会: list[str] | None = None,
    议程: str = "",
) -> str:
    初始化()
    now = dt.datetime.now()
    mid = f"{now:%Y%m%d%H%M%S}-{_安全片段(项目室)}-{_安全片段(类型)}"
    data = {
        "id": mid,
        "项目室": 项目室,
        "类型": 类型,
        "标题": 标题,
        "主席": 主席,
        "参会": _去重(["船主", 主席, *(参会 or [])]),
        "状态": "进行中",
        "议程": 议程,
        "创建时间": now.isoformat(timespec="seconds"),
        "更新时间": now.isoformat(timespec="seconds"),
        "事件": [],
    }
    _追加事件(data, 类型="开会", 发言人=主席, 内容=议程 or f"{标题} 开始。", 目标="全体")
    _写(data)
    return mid


def 开工会(
    项目室: str,
    标题: str,
    原始需求: str,
    覆盖说明: str,
    子工单: list[dict[str, Any]],
) -> str:
    return 开工交办会(项目室, 标题, 原始需求, 覆盖说明, 子工单)


def 记录项目会(
    项目室: str,
    标题: str,
    原始需求: str,
    覆盖说明: str,
    项目会: dict[str, Any],
    子工单: list[dict[str, Any]],
) -> str:
    existing = _最新项目会(项目室, 类型="项目会")
    if existing:
        return existing
    参会 = [str(x) for x in 项目会.get("参会", []) if str(x).strip()]
    if not 参会:
        参会 = [str(x.get("岗位", "")) for x in 子工单 if x.get("岗位")]
    agenda = (
        f"议题：{项目会.get('议题') or 标题}\n"
        f"船主原始需求：{原始需求}\n"
        f"会议目标：先讨论需求、方案、资料、标准、风险和岗位能力，再形成可追溯工单。\n"
        f"覆盖说明：{覆盖说明}"
    )
    mid = 创建会议(项目室, "项目会", 标题, "项目经理", 参会, agenda)
    for item in 项目会.get("发言") or []:
        if not isinstance(item, dict):
            continue
        记录(
            mid,
            str(item.get("类型") or "发言"),
            str(item.get("发言人") or "岗位"),
            str(item.get("内容") or ""),
            目标="项目会",
        )
    for item in 项目会.get("决议") or []:
        if not isinstance(item, dict):
            continue
        content = f"{item.get('编号', '')}：{item.get('内容', '')}\n落实：{item.get('落实', '')}"
        记录(mid, "决议", "项目经理", content, 目标="全体")
    standards = "\n".join(f"- {x}" for x in _列表化(项目会.get("验收标准")))
    if standards:
        记录(mid, "验收标准", "测试工程师", standards, 目标="全体")
    needs = "\n".join(f"- {x}" for x in _列表化(项目会.get("资料需求")))
    if needs:
        记录(mid, "资料需求", "项目经理", needs, 目标="全体")
    for item in 子工单:
        role = str(item.get("岗位", ""))
        name = str(item.get("文件", item.get("标题", "")))
        content = (
            f"拟落地工单：{item.get('标题', name)}\n"
            f"岗位：{role}\n"
            f"会议依据：{item.get('会议依据', '') or '未写'}\n"
            f"任务：{item.get('任务', '')}"
        )
        记录(mid, "落实到工单", "项目经理", content, 目标=role, 关联工单=name)
    return mid


def 开工交办会(
    项目室: str,
    标题: str,
    原始需求: str,
    覆盖说明: str,
    子工单: list[dict[str, Any]],
) -> str:
    参会 = [str(x.get("岗位", "")) for x in 子工单 if x.get("岗位")]
    agenda = (
        f"项目：{标题}\n"
        f"原始需求：{原始需求}\n"
        f"方案依据：{覆盖说明}\n"
        "说明：这是旧工单项目室的历史交办记录；现役活公司不靠确认开工按钮启动。"
    )
    mid = 创建会议(项目室, "开工交办", 标题, "项目经理", 参会, agenda)
    记录(mid, "历史开工确认", "项目经理", "旧项目室曾由船主确认开工，按当时项目会决议进入执行。", 目标="全体")
    for item in 子工单:
        _记录交办(mid, item)
    return mid


def 确认开工会(工单路径列表: list[Path]) -> str:
    """旧工单兼容：船主确认开工后记录交办；现役活公司不调用它启动工作。"""
    paths = [Path(p) for p in 工单路径列表 if Path(p).exists()]
    if not paths:
        return ""
    parsed = [(p, _安全解析(p)) for p in paths]
    room = str(parsed[0][1].get("来源会议") or "")
    if not room:
        return ""
    info = _静态拆解(room)
    tasks: list[dict[str, Any]] = []
    for p, meta in parsed:
        if str(meta.get("来源会议") or "") != room:
            continue
        tasks.append({
            "文件": p.name,
            "标题": _工单标题(p),
            "岗位": meta.get("岗位", ""),
            "任务": _工单节(p, "任务"),
            "白名单": _白名单(meta.get("白名单", "")),
            "预算步数": meta.get("预算步数", ""),
            "预算依据": meta.get("预算依据", ""),
            "会议依据": meta.get("会议依据", ""),
        })
    if not tasks:
        return ""
    existing = _最新项目会(room, 类型="开工交办")
    if existing:
        for item in tasks:
            _记录交办(existing, item)
        return existing
    return 开工交办会(
        room,
        info.get("标题") or room,
        info.get("需求") or "未读取到原始需求",
        info.get("覆盖说明") or "未读取到覆盖说明",
        tasks,
    )


def _记录交办(mid: str, item: dict[str, Any]) -> None:
    role = str(item.get("岗位", ""))
    name = str(item.get("文件", item.get("标题", "")))
    if _已有事件(mid, name, "交办"):
        return
    content = (
        f"交办给 {role}：{item.get('标题', name)}。\n"
        f"会议依据：{item.get('会议依据', '') or '未写'}\n"
        f"任务：{item.get('任务', '')}\n"
        f"白名单：{', '.join(map(str, item.get('白名单', []) or [])) or '未写'}\n"
        f"预算：{item.get('预算步数', '')}步；依据：{item.get('预算依据', '')}"
    )
    记录(mid, "交办", "项目经理", content, 目标=role, 关联工单=name, 需回执=True)


def 记录(
    会议id: str,
    类型: str,
    发言人: str,
    内容: str,
    目标: str = "",
    关联工单: str = "",
    需回执: bool = False,
) -> dict[str, Any]:
    with _锁:
        data = _读(会议id)
        ev = _追加事件(data, 类型, 发言人, 内容, 目标, 关联工单, 需回执)
        _写(data)
        return ev


def 船主发言(会议id: str, 内容: str) -> dict[str, Any]:
    text = str(内容 or "").strip()
    if not text:
        raise ValueError("发言不能为空")
    return 记录(会议id, "船主发言", "船主", text)


def 记录工单受领(path: Path) -> None:
    meta = _安全解析(path)
    room = str(meta.get("来源会议") or "")
    if not room:
        return
    role = str(meta.get("岗位") or "岗位")
    mid = _确保项目会(room, f"项目协同会 · {room}", [role])
    if _已有事件(mid, path.name, "受领回执"):
        return
    task = _工单节(path, "任务")
    content = (
        f"已受领 {path.name}。\n"
        f"岗位：{role}\n"
        f"理解：{task[:420] or '已读取工单正文'}\n"
        f"会议依据：{meta.get('会议依据', '') or '未写'}\n"
        f"边界：{meta.get('白名单', '') or '未写白名单'}\n"
        f"预算：{meta.get('预算步数', '') or '未写'}步；{meta.get('预算依据', '') or '未写预算依据'}"
    )
    记录(mid, "受领回执", role, content, 目标="项目经理", 关联工单=path.name)


def 记录工单事件(path: Path, 类型: str, 发言人: str, 内容: str, 目标: str = "") -> None:
    meta = _安全解析(path)
    room = str(meta.get("来源会议") or "")
    if not room:
        return
    mid = _确保项目会(room, f"项目协同会 · {room}", [发言人])
    记录(mid, 类型, 发言人, 内容, 目标=目标, 关联工单=path.name)


def 项目会议(项目室: str, limit: int = 80) -> list[dict[str, Any]]:
    初始化()
    out: list[dict[str, Any]] = []
    for p in sorted(会议根.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if str(data.get("项目室") or "") != 项目室:
            continue
        events = data.get("事件", [])
        if not isinstance(events, list):
            events = []
        out.append({
            "id": data.get("id", p.stem),
            "类型": data.get("类型", "会议"),
            "标题": data.get("标题", p.stem),
            "主席": data.get("主席", ""),
            "参会": data.get("参会", []),
            "状态": data.get("状态", ""),
            "创建时间": data.get("创建时间", ""),
            "更新时间": data.get("更新时间", ""),
            "事件": [_事件视图(x) for x in events[-limit:] if isinstance(x, dict)],
        })
    out.sort(key=lambda x: str(x.get("创建时间", "")))
    return out


def 项目状态摘要(项目室: str) -> str:
    chunks: list[str] = []
    meetings = 项目会议(项目室, limit=12)
    if meetings:
        chunks.append("## 最近会议/协同")
        for m in meetings[-3:]:
            chunks.append(f"- {m['类型']}｜{m['标题']}｜状态:{m['状态']}｜参会:{'、'.join(map(str, m.get('参会', [])))}")
            for ev in m.get("事件", [])[-5:]:
                chunks.append(f"  - {ev.get('时间', '')} {ev.get('类型', '')} {ev.get('发言人', '')}: {ev.get('内容', '')[:180]}")
    tasks = []
    for col in ("待确认", "待办", "进行中", "待验收", "已完成", "已作废"):
        for p in (工单 / col).glob("*.md"):
            if p.name.endswith(".日志.md"):
                continue
            meta = _安全解析(p)
            if str(meta.get("来源会议") or "") != 项目室:
                continue
            ctl = 读控制(控制(p)) if 控制(p).exists() else {}
            tasks.append(f"- {p.name}｜{meta.get('岗位', '')}｜{col}｜状态:{ctl.get('状态', '')}｜步数:{ctl.get('步数', 0)}/{meta.get('预算步数', '?')}")
    if tasks:
        chunks.append("## 工单状态")
        chunks.extend(tasks)
    return "\n".join(chunks[-80:]) or "暂无项目状态。"


def 全局协同摘要(limit: int = 8) -> str:
    初始化()
    meetings = []
    for p in sorted(会议根.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        meetings.append(f"- {data.get('项目室', '')}｜{data.get('类型', '')}｜{data.get('标题', '')}｜{len(data.get('事件', []) or [])}条事件")
    return "## 公司协同近况\n" + ("\n".join(meetings) if meetings else "暂无实时会议。")


def _追加事件(
    data: dict[str, Any],
    类型: str,
    发言人: str,
    内容: str,
    目标: str = "",
    关联工单: str = "",
    需回执: bool = False,
) -> dict[str, Any]:
    events = data.setdefault("事件", [])
    seq = len(events) + 1
    ev = {
        "id": f"{data.get('id', 'meeting')}-{seq:04d}",
        "时间": 时刻(),
        "类型": 类型,
        "发言人": 发言人,
        "目标": 目标,
        "关联工单": 关联工单,
        "需回执": bool(需回执),
        "内容": str(内容 or "").strip(),
    }
    events.append(ev)
    return ev


def _事件视图(ev: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(ev.get("id", "")),
        "时间": str(ev.get("时间", "")),
        "短时": str(ev.get("时间", ""))[11:16],
        "类型": str(ev.get("类型", "")),
        "发言人": str(ev.get("发言人", "")),
        "目标": str(ev.get("目标", "")),
        "关联工单": str(ev.get("关联工单", "")),
        "需回执": bool(ev.get("需回执")),
        "内容": str(ev.get("内容", "")),
    }


def _确保项目会(项目室: str, 标题: str, 参会: list[str] | None = None) -> str:
    existing = _最新项目会(项目室)
    if existing:
        return existing
    return 创建会议(项目室, "过程会", 标题, "项目经理", 参会 or [], "补记项目执行过程、回执、异常和新决议。")


def _最新项目会(项目室: str, 类型: str = "") -> str:
    初始化()
    found: list[tuple[str, str]] = []
    for p in 会议根.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if 类型 and str(data.get("类型") or "") != 类型:
            continue
        if str(data.get("项目室") or "") == 项目室 and str(data.get("状态") or "") != "已结束":
            found.append((str(data.get("创建时间") or ""), str(data.get("id") or p.stem)))
    found.sort()
    return found[-1][1] if found else ""


def _已有事件(mid: str, task: str, kind: str) -> bool:
    try:
        data = _读(mid)
    except Exception:  # noqa: BLE001
        return False
    for ev in data.get("事件", []):
        if ev.get("关联工单") == task and ev.get("类型") == kind:
            return True
    return False


def _安全解析(path: Path) -> dict[str, Any]:
    try:
        return 解析工单(path)
    except Exception:  # noqa: BLE001
        return {}


def _工单节(path: Path, 标题: str) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:  # noqa: BLE001
        return ""
    collecting = False
    buf: list[str] = []
    for line in lines:
        if line.startswith("## "):
            collecting = line[3:].strip() == 标题
            continue
        if collecting:
            buf.append(line)
    return "\n".join(buf).strip()


def _工单标题(path: Path) -> str:
    try:
        first = path.read_text(encoding="utf-8").splitlines()[0]
    except Exception:  # noqa: BLE001
        return path.name
    return first.lstrip("# ").split("·", 1)[-1].strip() or path.name


def _白名单(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [x.strip() for x in str(value or "").split(",") if x.strip()]


def _静态拆解(room: str) -> dict[str, str]:
    info = {"标题": room, "需求": "", "覆盖说明": ""}
    p = COMPANY / "会议" / room
    if not p.exists():
        return info
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception:  # noqa: BLE001
        return info
    if lines:
        head = lines[0].lstrip("# ").strip()
        info["标题"] = head.split("·", 1)[-1].strip() if "·" in head else head
    info["需求"] = _静态节(lines, "原始需求") or _静态节(lines, "需求")
    info["覆盖说明"] = _静态节(lines, "覆盖说明")
    return info


def _静态节(lines: list[str], 标题: str) -> str:
    collecting = False
    buf: list[str] = []
    for line in lines:
        if line.startswith("## "):
            collecting = line[3:].strip() == 标题
            continue
        if collecting:
            buf.append(line)
    return "\n".join(buf).strip()


def _去重(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out
