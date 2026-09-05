#!/usr/bin/env python3
"""办公室前端的只读聚合层：把散在各处的工单/会议/请示/信箱，拼成"项目室/历史"视角。

设计命题（见全局记忆/致接任者_工作手册第六节）：船主的窗户=决策过程透明+判断居中。
本模块只读，不改任何文件，不碰引擎/会议/审批的写逻辑；它不是活公司的执行入口。
顺着每张子工单的「来源会议」字段，把它们认回同一个娘家任务（=一间项目室）。
"""
from __future__ import annotations

import re
import hashlib
import threading
import time
from pathlib import Path
from typing import Any

import 审批
from 引擎工具 import COMPANY, 工单, 控制, 日志, 解析工单, 读控制, 急停文件

# 看板分两层：前端先轮询一个很小的“变化版本”，版本没变就不下载整张看板；
# 真变化时才重算完整快照。文件指纹兜住后台任务等未显式通知的写入，30 秒时间桶再兜一次漏网变化。
_待办缓存: dict = {"t": 0.0, "数据": None, "版本": 0}
_待办锁 = threading.Lock()
_待办有效秒 = 2.0
_看板条件 = threading.Condition()
_看板缓存: dict = {
    "t": 0.0, "数据": None, "键": None, "源版本": None, "版本": 0, "计算中": False,
    "兜底代": 0, "下一兜底": 0.0,
}
_看板兜底秒 = 30.0
_看板时钟 = time.monotonic

# 只看会改变 /board 返回值的文件；明确排除 embedding缓存等大目录。
_看板监视目录: tuple[Path, ...] = (
    COMPANY / "工单",
    COMPANY / "请示" / "待经理",
    COMPANY / "请示" / "待船主",
    COMPANY / "请示" / "已决",
    COMPANY / "待验收",
    COMPANY / "信誉",
    COMPANY / "职级",
    COMPANY / "会议",
    COMPANY / "管理动作" / "待确认",
)
_看板监视文件: tuple[Path, ...] = (
    COMPANY / "花名册.yaml",
    COMPANY / "公司急停.flag",
    COMPANY / "运行状态" / "模型核验.json",
)


def _指纹名(path: Path) -> str:
    try:
        return str(path.relative_to(COMPANY))
    except ValueError:
        return str(path)


def _看板源指纹() -> str:
    """只读取文件名和大小/修改时间，不读正文；通常几毫秒内完成。"""
    rows: list[str] = []
    for root in _看板监视目录:
        if not root.exists():
            rows.append(f"D|{_指纹名(root)}|missing")
            continue
        try:
            files = sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: str(p))
        except OSError as e:
            rows.append(f"D|{_指纹名(root)}|error|{type(e).__name__}")
            continue
        for path in files:
            try:
                st = path.stat()
                rows.append(f"F|{_指纹名(path)}|{st.st_size}|{st.st_mtime_ns}|{st.st_ctime_ns}")
            except OSError as e:
                rows.append(f"F|{_指纹名(path)}|error|{type(e).__name__}")
    for path in _看板监视文件:
        try:
            st = path.stat()
            rows.append(f"S|{_指纹名(path)}|{st.st_size}|{st.st_mtime_ns}|{st.st_ctime_ns}")
        except FileNotFoundError:
            rows.append(f"S|{_指纹名(path)}|missing")
        except OSError as e:
            rows.append(f"S|{_指纹名(path)}|error|{type(e).__name__}")
    return hashlib.blake2b("\n".join(rows).encode("utf-8"), digest_size=16).hexdigest()


def _看板源版本(key: tuple[str, tuple[str, ...]]) -> str:
    fingerprint = _看板源指纹()
    with _看板条件:
        generation = int(_看板缓存["版本"])
        now = _看板时钟()
        if not _看板缓存["下一兜底"]:
            _看板缓存["下一兜底"] = now + _看板兜底秒
        elif now >= _看板缓存["下一兜底"]:
            _看板缓存["兜底代"] += 1
            _看板缓存["下一兜底"] = now + _看板兜底秒
        fallback_generation = int(_看板缓存["兜底代"])
    raw = f"{key!r}|{generation}|{fallback_generation}|{fingerprint}"
    return hashlib.blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()


def 看板版本(模式: str, 岗位: list[str]) -> str:
    """给前端的轻量变化标记；相同表示整张看板不必重新下载。"""
    return _看板源版本((str(模式), tuple(岗位)))


def 清待办缓存() -> None:
    """写动作后立刻让待办缓存过期（船主拍板/调级后下一次读就是新的）。同时进版本号——
    L16 二审：防在途重算(读盘在锁外)随后把旧快照写回、盖掉刚清的缓存。"""
    with _待办锁:
        _待办缓存["数据"] = None
        _待办缓存["版本"] += 1
    with _看板条件:
        _看板缓存["数据"] = None
        _看板缓存["版本"] += 1
        _看板缓存["下一兜底"] = 0.0
        _看板条件.notify_all()

会议目录 = COMPANY / "会议"
信箱目录 = COMPANY / "信箱"
项目记忆目录 = COMPANY / "项目记忆"
列们 = ("待确认", "待办", "进行中", "待验收", "已完成", "已作废")


def _读全部子工单() -> list[tuple[Path, str, dict[str, Any]]]:
    out: list[tuple[Path, str, dict[str, Any]]] = []
    for col in 列们:
        d = 工单 / col
        if not d.exists():
            continue
        for p in sorted(d.glob("*.md")):
            if p.name.endswith(".日志.md"):
                continue
            try:
                meta = 解析工单(p)
            except Exception:  # noqa: BLE001
                meta = {}
            out.append((p, col, meta))
    return out


def _节(lines: list[str], 标题: str) -> str:
    """取 markdown 中 '## 标题' 到下一个 '## ' 之间的正文。"""
    收 = False
    buf: list[str] = []
    for ln in lines:
        if ln.startswith("## "):
            收 = ln[3:].strip() == 标题
            continue
        if 收:
            buf.append(ln)
    return "\n".join(buf).strip()


def _单标题(p: Path) -> str:
    try:
        first = p.read_text(encoding="utf-8").splitlines()[0]
    except Exception:  # noqa: BLE001
        return p.name
    return first.lstrip("# ").split("·", 1)[-1].strip() or p.name


def _解析会议(name: str) -> dict[str, str]:
    info = {"id": name, "标题": name, "时间": "", "需求": "", "覆盖说明": "", "项目会": ""}
    if not name:
        return info
    p = 会议目录 / name
    if not p.exists():
        info["标题"] = re.sub(r"^\d{3}-|\.md$", "", name) or name
        return info
    lines = p.read_text(encoding="utf-8").splitlines()
    if lines:
        head = lines[0].lstrip("# ").strip()
        info["标题"] = head.split("·", 1)[-1].strip() if "·" in head else head
    for ln in lines:
        if ln.startswith("时间:") or ln.startswith("时间："):
            info["时间"] = ln.split(":", 1)[-1].split("：", 1)[-1].strip()
            break
    info["需求"] = _节(lines, "原始需求") or _节(lines, "需求")
    info["覆盖说明"] = _节(lines, "覆盖说明")
    info["项目会"] = _节(lines, "项目会")
    return info


def _会议号(name: str) -> str:
    m = re.match(r"(\d{3})", name or "")
    return m.group(1) if m else ""


def _日志过程(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or not line.startswith("["):
            continue
        body = line.split("] ", 1)[-1]
        if body.startswith("确认开工"):
            out.append("已收到船主确认，开始执行。")
        elif body.startswith("等待依赖"):
            out.append("正在等待前置工作完成：" + body.split("：", 1)[-1])
        elif body.startswith("查看资料"):
            out.append("查看了资料：" + body.split("：", 1)[-1])
        elif body.startswith("读文件"):
            out.append("查看了资料：" + body.split("：", 1)[-1].splitlines()[0])
        elif body.startswith("写入文件") or body.startswith("追加文件"):
            out.append(body)
        elif body.startswith("写文件"):
            out.append("写入文件：" + body.split("：", 1)[-1])
        elif body.startswith("发起请示"):
            out.append("遇到不确定事项，已发起请示。")
        elif body.startswith("船主裁决"):
            out.append(body)
        elif body.startswith("完成"):
            out.append(body)
        elif body.startswith("熔断"):
            out.append("已暂停：" + body.split("：", 1)[-1])
        elif body.startswith("未知动作，已忽略"):
            try:
                obj = __import__("json").loads(body.split("：", 1)[-1])
                act = obj.get("动作") or obj.get("action") or "动作"
                path = obj.get("路径") or obj.get("path") or ""
                msg = obj.get("内容") or obj.get("message") or obj.get("content") or ""
                if act in ("写文件", "write", "write_file") and path:
                    out.append(f"尝试写入文件：{path}，但旧引擎没有识别动作格式。")
                elif act in ("请示", "ask", "request_approval"):
                    out.append(f"尝试请示：{str(msg)[:120]}，但旧引擎没有识别动作格式。")
                else:
                    out.append("模型给出了动作，但旧引擎没有识别。")
            except Exception:  # noqa: BLE001
                out.append("模型给出了动作，但旧引擎没有识别。")
        elif body.startswith("动作未识别"):
            out.append(body)
    dedup: list[str] = []
    for item in out:
        if item and (not dedup or dedup[-1] != item):
            dedup.append(item)
    return dedup[-8:]


def _事件id(name: str, raw: str) -> str:
    return hashlib.sha1(f"{name}|{raw}".encode("utf-8")).hexdigest()[:10]


def _动作说明(body: str) -> str:
    if body.startswith("确认开工"):
        return "已收到船主确认，开始执行。"
    if body.startswith("等待依赖"):
        return "等待依赖：" + body.split("：", 1)[-1]
    if body.startswith("查看资料"):
        return "查看资料：" + body.split("：", 1)[-1].split("（", 1)[0]
    if body.startswith("读文件"):
        return "查看资料：" + body.split("：", 1)[-1].splitlines()[0]
    if body.startswith("写入文件") or body.startswith("追加文件"):
        return body
    if body.startswith("写文件"):
        return "写入文件：" + body.split("：", 1)[-1]
    if body.startswith("执行命令"):
        return body.splitlines()[0]
    if body.startswith("发起请示"):
        return "发起请示，等待裁决。"
    if body.startswith("项目经理熔断预审"):
        return "项目经理已预审熔断：" + body.split("：", 1)[-1]
    if body.startswith("项目经理批准自动续步"):
        return body
    if body.startswith("正式报告"):
        return body
    if body.startswith("船主批准继续"):
        return body
    if body.startswith("完成"):
        return body
    if body.startswith("岗位复盘"):
        return body
    if body.startswith("熔断"):
        return "已回炉：" + body.split("：", 1)[-1]
    if body.startswith("暂停"):
        return body
    return ""


def _叙事(role: str, body: str, title: str) -> tuple[str, str]:
    who = role or "岗位"
    if body.startswith("查看资料"):
        target = body.split("：", 1)[-1].split("（", 1)[0]
        return f"{who} 查看资料：{target}", "为了补足事实依据，读取资料后会在下一步使用。"
    if body.startswith("写入文件") or body.startswith("追加文件") or body.startswith("写文件"):
        return f"{who} 产出草稿：{_动作说明(body)}", "文件已写入，但仍需读回、自检、复盘或交给测试，未验收前不能算完成。"
    if body.startswith("熔断"):
        return f"{who} 暂停：{body.split('：', 1)[-1]}", "当前不是完成失败，而是预算/规则触发暂停，需要经理裁决或回炉继续。"
    if body.startswith("项目经理熔断预审"):
        return f"项目经理预审：{body.split('：', 1)[-1]}", "项目经理正在判断是否低风险续步，还是需要船主裁决。"
    if body.startswith("项目经理批准自动续步"):
        return "项目经理批准自动续步", body
    if body.startswith("正式报告"):
        parts = body.split("｜")
        kind = parts[0].replace("正式报告：", "").strip()
        stage = parts[1].strip() if len(parts) > 1 else ""
        next_step = parts[-1].replace("下一步：", "").strip() if "下一步：" in parts[-1] else ""
        return f"{who} 提交{kind}", "；".join(x for x in (stage, next_step) if x)
    if body.startswith("等待依赖"):
        return f"{who} 等待前置任务", body.split("：", 1)[-1]
    if body.startswith("确认开工"):
        return f"{title} 已开工", "船主确认后，工单进入执行泳道。"
    if body.startswith("完成"):
        return f"{who} 完成工单", body
    return f"{who} {_动作说明(body)}".strip(), ""


def _日志里程碑(p: Path, col: str, meta: dict[str, Any]) -> list[dict[str, Any]]:
    role = str(meta.get("岗位") or "")
    room = str(meta.get("来源会议") or "")
    lg = 日志(p)
    if not lg.exists():
        return []
    out: list[dict[str, Any]] = []
    for raw in lg.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\[([^\]]+)\]\s*(.*)", raw.strip())
        if not m:
            continue
        when, body = m.group(1), m.group(2)
        kind = "工"
        if body.startswith("发起请示"):
            kind = "请示"
        elif body.startswith(("完成", "岗位复盘")):
            kind = "完"
        elif body.startswith(("熔断", "暂停")):
            kind = "警"
        elif body.startswith("正式报告"):
            kind = "报告"
        text = _动作说明(body)
        if text:
            title, detail = _叙事(role, body, _单标题(p))
            out.append({"k": kind, "t": _规范时刻(when), "who": role, "role": role,
                        "text": title or text, "副": detail or col, "room": room,
                        "event": _事件id(p.name, raw), "task": p.name})
    return out[-4:]


def _日志事件(p: Path, text: str, role: str, col: str, room: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    title = _单标题(p)
    for raw in text.splitlines():
        m = re.match(r"\[([^\]]+)\]\s*(.*)", raw.strip())
        if not m:
            continue
        when, body = m.group(1), m.group(2)
        shown = _动作说明(body)
        if not shown:
            continue
        kind = "进展"
        if body.startswith(("熔断", "暂停")):
            kind = "回炉"
        elif body.startswith(("完成", "岗位复盘")):
            kind = "完成"
        elif body.startswith("正式报告"):
            kind = "报告"
        elif body.startswith(("发起请示", "项目经理熔断预审", "项目经理批准自动续步", "船主裁决")):
            kind = "决策"
        head, detail = _叙事(role, body, title)
        out.append({
            "id": _事件id(p.name, raw),
            "时间": _规范时刻(when),
            "短时": _规范时刻(when)[11:16],
            "类别": kind,
            "标题": head or shown,
            "说明": detail,
            "原文": body,
            "角色": role,
            "列": col,
            "room": room,
        })
    return out


def _会议大厅事件(rooms: set[str]) -> list[dict[str, Any]]:
    try:
        import 协同
    except Exception:  # noqa: BLE001
        return []
    # 债⑤:岗位"发言"类固化成气泡(k:话,与立项流式临时气泡同形态)——开完会前端清临时后由此接管,气泡仍在且不重复;
    # 会议"流程"类仍是里程碑(k:会)。
    发言类 = {"开场", "方案", "风险", "验收", "结论", "回应", "发言", "船主发言", "插话", "叫停"}
    流程类 = {"决议", "验收标准", "资料需求", "落实到工单", "交办", "受领回执", "异常报告", "项目经理裁决", "交付报告", "回炉报告", "开工确认",
            # 开会过程留痕(船主圣域:开会的轮次推进/收敛/小结也是记录,刷新也在、永久可回看)
            "收敛判断", "会议小结", "插话响应", "未谈拢", "要问船主"}
    out: list[dict[str, Any]] = []
    for room in sorted(x for x in rooms if x):
        for meeting in 协同.项目会议(room, limit=10**9):  # 大厅永久历史:每会议全部事件不截断(2026-06-23)
            for ev in meeting.get("事件", []):  # 不截断:开完会发言全留在大厅(船主要一直显示、能回看),别只剩最后6条
                kind = str(ev.get("类型", "会议"))
                speaker = str(ev.get("发言人", ""))
                content = str(ev.get("内容", ""))
                t = _规范时刻(str(ev.get("时间", "")))
                eid = str(ev.get("id", ""))
                if kind in 发言类:
                    out.append({
                        "k": "话", "t": t, "who": speaker, "role": speaker,
                        "text": content[:1200], "room": room, "event": eid,
                    })
                    continue
                if kind == "开会":
                    title, detail = f"{meeting.get('类型', '会议')}开始", content[:180]
                elif kind in 流程类:
                    title, detail = f"{speaker} · {kind}", content[:220]
                else:
                    continue
                out.append({
                    "k": "会", "t": t, "who": speaker, "role": speaker,
                    "text": title, "副": detail, "room": room, "event": eid,
                })
    return out  # 大厅永久历史:不截断(2026-06-23)


def _观察视图(obs: Any) -> list[dict[str, str]]:
    if not isinstance(obs, list):
        return []
    out: list[dict[str, str]] = []
    for item in obs:
        if not isinstance(item, dict):
            continue
        target = str(item.get("目标", ""))
        action = str(item.get("动作", "观察"))
        content = str(item.get("内容", ""))
        summary = f"{action}：{target}（原文约{len(content)}字）"
        if target == "花名册.yaml":
            roles = re.findall(r"^([^#\s][^:\n]+):\n(?:.*?\n)*?\s+model:\s*([^\n]+)", content, re.M)
            if roles:
                summary = "读取花名册，提取岗位模型：" + "、".join(f"{r.strip()}={m.strip()}" for r, m in roles[:6])
            else:
                summary = "读取花名册，供岗位/模型核对使用；完整原文已折叠。"
        elif target.endswith("公司公告.md"):
            bits = []
            for key in ("2026-06-11", "项目经理", "首席工程师", "工程师", "测试工程师", "文案工程师（兼职）"):
                if key in content:
                    bits.append(key)
            summary = "读回公司公告草稿，已看到：" + ("、".join(bits) if bits else "公告正文")
        out.append({
            "时间": str(item.get("时间", "")),
            "动作": action,
            "目标": target,
            "摘要": summary,
            "原文": content,
        })
    return out


def _正式报告视图(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append({
            "时间": str(item.get("时间", "")),
            "类别": str(item.get("类别", "报告")),
            "原因": str(item.get("原因", "")),
            "阶段": str(item.get("阶段", "")),
            "判断": str(item.get("判断", "")),
            "下一步": str(item.get("下一步", "")),
        })
    return out[-5:]


def _产物状态(log_text: str, col: str) -> list[dict[str, str]]:
    files: list[str] = []
    for line in log_text.splitlines():
        m = re.search(r"(?:写入|追加)文件：([^（\n]+)", line)
        if not m:
            m = re.search(r"写文件：([^（\n]+)", line)
        if m:
            name = m.group(1).strip()
            if name and name not in files:
                files.append(name)
    if not files:
        return []
    completed = "完成：" in log_text
    if col == "已完成":
        status, reason = "已完成", "工单已验收完成。"
    elif col == "待验收" or completed:
        status, reason = "待验收", "员工已提交完成，等待测试/船主验收。"
    else:
        status, reason = "草稿/未验收", "文件已经生成，但工单尚未完成自检、复盘或验收。"
    return [{"路径": f, "状态": status, "说明": reason} for f in files]


def _经理报告(card: dict[str, Any] | None) -> dict[str, Any]:
    if not card:
        return {}
    report = card.get("经理报告") if isinstance(card.get("经理报告"), dict) else {}
    evidence = report.get("依据") if isinstance(report, dict) else ""
    if isinstance(evidence, str):
        evidence_list = [evidence] if evidence else []
    elif isinstance(evidence, list):
        evidence_list = [str(x) for x in evidence if str(x).strip()]
    else:
        evidence_list = []
    reason = str((report or {}).get("理由") or card.get("经理理由") or "")
    suggestion = str((report or {}).get("建议") or card.get("建议") or "")
    boundary = str((report or {}).get("边界") or "")
    missing = (report or {}).get("需补技能") or []
    if isinstance(missing, str):
        missing = [missing] if missing else []
    if reason == "mock本地规则无法充分判断，按拿不准标准上报。":
        evidence_list = ["旧本地规则只记录了“超过预算步数”，没有核对工单目标、白名单、已完成动作和风险。"]
        suggestion = "这张旧卡不合格；低风险收尾应由项目经理批准追加步数，或在右栏点“批准续步”。"
        boundary = "仅限原白名单内继续完成自检/复盘/测试交接。"
        missing = ["项目经理预算估算", "熔断报告写作"]
    return {
        "裁决": str(card.get("经理裁决") or ""),
        "理由": reason,
        "依据": evidence_list,
        "建议": suggestion,
        "边界": boundary,
        "追加步数": card.get("追加步数", 2),
        "需补技能": missing,
        "原始状态": str(card.get("状态") or ""),
    }


def 项目列表() -> list[dict[str, Any]]:
    subs = _读全部子工单()
    groups: dict[str, list[tuple[Path, str, dict]]] = {}
    name2mid: dict[str, str] = {}
    for p, col, meta in subs:
        mid = str(meta.get("来源会议") or "")
        name2mid[p.name] = mid
        if mid:
            groups.setdefault(mid, []).append((p, col, meta))
    待船主 = 审批.列表().get("待船主", [])
    out: list[dict[str, Any]] = []
    for mid, items in groups.items():
        info = _解析会议(mid)
        汇总 = {c: 0 for c in 列们}
        col_by_name: dict[str, str] = {}
        for _p, col, _m in items:
            汇总[col] += 1
            col_by_name[_p.name] = col
        活跃 = any(汇总[c] > 0 for c in ("待确认", "待办", "进行中", "待验收"))
        本任请示 = sum(
            1 for c in 待船主
            if name2mid.get(str(c.get("任务", ""))) == mid and col_by_name.get(str(c.get("任务", ""))) != "待办"
        )
        待处理 = 汇总["待确认"] + 汇总["待办"] + 汇总["待验收"] + 本任请示
        out.append({
            **info,
            "汇总": 汇总,
            "活跃": 活跃,
            "待处理": 待处理,
            "工单数": len(items),
        })
    out.sort(key=lambda x: str(x["id"]), reverse=True)
    return out


def _演示历史(info: dict[str, str]) -> bool:
    title = info.get("标题", "")
    need = info.get("需求", "")
    return title == "流水线演示" or need.startswith("演示任务:")


def 项目详情(mid: str) -> dict[str, Any]:
    subs = _读全部子工单()
    name2mid = {p.name: str(meta.get("来源会议") or "") for p, _c, meta in subs}
    items = [(p, col, meta) for p, col, meta in subs if str(meta.get("来源会议") or "") == mid]
    info = _解析会议(mid)
    请示全 = 审批.列表()
    待裁名 = {str(c.get("任务", "")) for c in 请示全.get("待船主", [])}
    待审名 = {str(c.get("任务", "")) for c in 请示全.get("待经理", [])}

    泳道: list[dict[str, Any]] = []
    for p, col, meta in items:
        ctlp = 控制(p)
        ctl = 读控制(ctlp) if ctlp.exists() else {}
        lg = 日志(p)
        日志文本 = lg.read_text(encoding="utf-8")[-2600:] if lg.exists() else ""
        追加预算 = int(ctl.get("追加预算", 0) or 0)
        基础预算 = int(meta.get("预算步数", 0) or 0)
        try:
            正文行 = p.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue
        裁决 = [ln for ln in 正文行 if ln.lstrip().startswith("> [船主")]
        过程 = {
            "任务理解": _节(正文行, "任务") or _节(正文行, "目标"),
            "执行要求": _节(正文行, "验收") or _节(正文行, "验收标准"),
            "白名单": str(meta.get("白名单", "")),
            "会议依据": str(meta.get("会议依据", "")),
            "预算依据": str(meta.get("预算依据", "")),
            "预计字数": str(meta.get("预计字数", "")),
            "预计token": str(meta.get("预计token", "")),
            "步骤": _日志过程(日志文本),
            "最近日志": 日志文本,
            "观察": _观察视图(ctl.get("观察", [])),
            "正式报告": _正式报告视图(ctl.get("正式报告", [])),
            "事件": _日志事件(p, 日志文本, str(meta.get("岗位") or ""), col, str(meta.get("来源会议") or "")),
            "产物": _产物状态(日志文本, col),
        }
        泳道.append({
            "name": p.name,
            "角色": meta.get("岗位", ""),
            "列": col,
            "控制状态": ctl.get("状态", ""),
            "步数": int(ctl.get("步数", 0) or 0),
            "预算": str(基础预算 + 追加预算) if 追加预算 else meta.get("预算步数", ""),
            "追加预算": 追加预算,
            "模型": _角色模型(str(meta.get("岗位", ""))),
            "白名单": meta.get("白名单", ""),
            "任务": _节(正文行, "任务"),
            "日志": 日志文本,
            "过程": 过程,
            "裁决": 裁决,
            "请示中": p.name in 待裁名 or p.name in 待审名,
        })

    def _挂本室(card: dict[str, Any]) -> bool:
        t = str(card.get("任务", ""))
        m = name2mid.get(t)
        if m:
            return m == mid
        g = re.search(r"V3-(\d{3})", t)
        return bool(g and g.group(1) == _会议号(mid))

    请示: list[dict[str, Any]] = []
    for 桶 in ("待船主", "待经理", "已决"):
        for c in 请示全.get(桶, []):
            if _挂本室(c):
                请示.append({**c, "桶": 桶})
    try:
        import 协同

        meetings = 协同.项目会议(mid)
    except Exception:  # noqa: BLE001
        meetings = []
    return {**info, "泳道": 泳道, "请示": 请示, "会议协同": meetings, "项目记忆": 项目记忆(mid)}


def 待办汇总(强制: bool = False) -> dict[str, Any]:
    """带短TTL缓存的待办汇总（省每次轮询重算全盘）。强制=True 或缓存过期就重算。"""
    now = time.time()
    with _待办锁:
        if not 强制 and _待办缓存["数据"] is not None and _待办缓存["t"] + _待办有效秒 > now:
            return _待办缓存["数据"]
        起版 = _待办缓存["版本"]   # 记下重算前的版本
    结果 = _待办汇总_算()
    with _待办锁:
        if 起版 == _待办缓存["版本"]:   # 重算期间没被清过才写回；被清过就丢弃这份旧快照(别盖回刚清的)
            _待办缓存["t"] = time.time()
            _待办缓存["数据"] = 结果
    return 结果


def _待办汇总_算() -> dict[str, Any]:
    subs = _读全部子工单()
    待船主 = 审批.列表().get("待船主", [])
    card_by_task = {str(c.get("任务", "")): c for c in 待船主}
    待确认 = [
        {"name": p.name, "角色": meta.get("岗位", ""), "会议": str(meta.get("来源会议") or ""), "标题": _单标题(p)}
        for p, col, meta in subs if col == "待确认"
    ]
    待验收 = [
        {"name": p.name, "角色": meta.get("岗位", ""), "会议": str(meta.get("来源会议") or ""), "标题": _单标题(p)}
        for p, col, meta in subs if col == "待验收"
    ]
    回炉: list[dict[str, Any]] = []
    covered_cards: set[str] = set()
    for p, col, meta in subs:
        if col != "待办" or not str(meta.get("来源会议") or ""):
            continue
        card = card_by_task.get(p.name)
        if card:
            covered_cards.add(str(card.get("id", "")))
        ctl = 读控制(控制(p)) if 控制(p).exists() else {}
        log_text = 日志(p).read_text(encoding="utf-8") if 日志(p).exists() else ""
        回炉.append({
            "name": p.name,
            "角色": meta.get("岗位", ""),
            "会议": str(meta.get("来源会议") or ""),
            "标题": _单标题(p),
            "过程": _日志过程(log_text)[-2:],
            "预算": int(meta.get("预算步数", 0) or 0) + int(ctl.get("追加预算", 0) or 0),
            "步数": int(ctl.get("步数", 0) or 0),
            "产物": _产物状态(log_text, col),
            "请示": card,
            "经理报告": _经理报告(card) if card else {},
        })
    裁决 = [c for c in 待船主 if str(c.get("id", "")) not in covered_cards]
    # 护栏丙②：活公司产出的待验收列表（与旧工单 待验收 分开，type 字段区分前端动作）
    try:
        import 待验收记录
        活公司待验收 = 待验收记录.列表(仅待验收=True)
    except Exception:  # noqa: BLE001
        活公司待验收 = []
    # 给每张卡挂"轨迹"（线性时间轴）+ 统一"到你时间"（落到船主桌上的时刻，多件待批时用来区分/排序）
    try:
        import 部门 as _bm
        from 活_本人 import _岗位的人 as _名

        def _请示轨迹(card: dict) -> list:
            岗 = str(card.get("岗位") or "")
            人 = _名(岗) or 岗
            轨 = [{"时间": str(card.get("创建时间") or ""), "层级": _bm.层级名(人), "谁": 人,
                   "动作": "请示「" + (str(card.get("内容") or "").strip()[:24]) + "」"}]
            if card.get("经理时间") or card.get("经理裁决"):
                轨.append({"时间": str(card.get("经理时间") or ""), "层级": "经理", "谁": "老钟",
                           "动作": "预审 → " + (str(card.get("经理裁决") or "上报"))
                                   + (("「" + str(card.get("经理理由") or "").strip()[:24] + "」") if card.get("经理理由") else "")})
            轨.append({"时间": "", "层级": "船主", "谁": "你", "动作": "待裁决"})
            return 轨

        def _验收轨迹存量(r: dict) -> list:
            """存量待验收记录（早于轨迹字段）没轨迹——从岗位/部门头/收敛经理/时间重建一条最简时间轴，别只剩空白。"""
            人 = _名(str(r.get("岗位") or "")) or str(r.get("岗位") or "")
            ts = str(r.get("时间") or "")
            轨 = [{"时间": ts, "层级": _bm.层级名(人) if 人 else "员工", "谁": 人,
                   "动作": "交付「" + str(r.get("任务") or "").strip()[:24] + "」"}]
            头 = str(r.get("部门头") or "").strip()
            if 头 and 头 != 人:
                轨.append({"时间": ts, "层级": "部门头", "谁": 头, "动作": "审 → 放行"})
            经 = str(r.get("收敛经理") or "").strip()
            if 经:
                轨.append({"时间": ts, "层级": "经理", "谁": 经, "动作": "收敛 → 放行"})
            轨.append({"时间": "", "层级": "船主", "谁": "你", "动作": "待你拍板"})
            return 轨

        for c in 裁决:
            c["轨迹"] = _请示轨迹(c)
            c["到你时间"] = str(c.get("经理时间") or c.get("创建时间") or "")
        for r in 活公司待验收:
            r["到你时间"] = str(r.get("时间") or "")
            if not r.get("轨迹"):   # 存量记录重建轨迹，时间轴不空白
                r["轨迹"] = _验收轨迹存量(r)
    except Exception:  # noqa: BLE001 轨迹拼装失败不许拖垮看板
        pass
    已决 = []   # 已决回看：船主回看拍过板的请示+验收（只读、最近在前）
    try:
        for c in 审批.列表().get("已决", [])[:40]:
            已决.append({
                "类": "请示", "id": str(c.get("id") or ""), "标题": str(c.get("内容") or c.get("任务") or "").strip()[:40],
                "岗位": str(c.get("岗位") or ""), "裁决": str(c.get("状态") or ""),
                "理由": str(c.get("船主理由") or c.get("经理理由") or ""), "时间": str(c.get("船主时间") or c.get("经理时间") or c.get("创建时间") or ""),
            })
        全验 = 待验收记录.列表(仅待验收=False)
        for r in [x for x in 全验 if x.get("状态") in ("已批", "已打回")][-40:]:
            已决.append({
                "类": "验收", "id": str(r.get("id") or ""), "标题": str(r.get("任务") or "").strip()[:40],
                "岗位": str(r.get("岗位") or ""), "裁决": str(r.get("状态") or ""),
                "理由": str(r.get("批注") or ""), "时间": str(r.get("批注时间") or r.get("时间") or ""),
            })
        已决.sort(key=lambda x: x.get("时间") or "", reverse=True)
    except Exception:  # noqa: BLE001
        已决 = []
    管理待确认 = []   # 船主自然语言直控·待确认卡（2026-07-11）：随轮询下发前端，画成三键卡
    try:
        import 管理动作
        管理待确认 = 管理动作.待确认列表()
    except Exception:  # noqa: BLE001
        管理待确认 = []
    return {
        "待确认": 待确认,
        "待办": 回炉,
        "待验收": 待验收,
        "待裁决": 裁决,
        "活公司待验收": 活公司待验收,
        "已决": 已决[:60],
        "管理待确认": 管理待确认,
    }


def 信箱(role: str) -> list[dict[str, str]]:
    p = 信箱目录 / f"{role}.md"
    if not role or not p.exists():
        return []
    text = p.read_text(encoding="utf-8")
    out: list[dict[str, str]] = []
    pat = re.compile(r"\*\*\[([^\]]+)\]\s*(.+?)\*\*\s*[:：]\s*(.*?)(?=\n\*\*\[|\Z)", re.S)
    for m in pat.finditer(text):
        body = m.group(3).strip()
        if "[mock·" in body or "真模式请在.env配key后重启办公室" in body:
            continue
        if body in {"在吗", "到岗"}:
            continue
        if "到岗并读完班规/岗位说明/技能包/岗位记忆" in body:
            continue
        if "到岗并读完班规/岗位说明/技能包/个人记忆" in body:
            continue
        out.append({"时间": m.group(1).strip(), "who": m.group(2).strip(), "text": body})
    return out[-60:]


def 项目记忆(mid: str) -> list[dict[str, str]]:
    if not mid:
        return []
    try:
        import 项目室

        p = 项目室.记忆路径(mid)
    except Exception:  # noqa: BLE001
        return []
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8")
    out: list[dict[str, str]] = []
    pat = re.compile(r"\*\*\[([^\]]+)\]\s*(.+?)\*\*\s*[:：]\s*(.*?)(?=\n\*\*\[|\Z)", re.S)
    for m in pat.finditer(text):
        out.append({"时间": m.group(1).strip(), "who": m.group(2).strip(), "text": m.group(3).strip()})
    return out[-80:]


def _规范时刻(s: str) -> str:
    """把各处时间写法归一成可排序串；认不出返回空串。"""
    import datetime as _dt

    s = (s or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}"
    m = re.match(r"(\d{2})-(\d{2})[ T](\d{2}):(\d{2})", s)
    if m:
        return f"{_dt.date.today().year}-{m.group(1)}-{m.group(2)} {m.group(3)}:{m.group(4)}"
    return ""


def 大厅(岗位: list[str]) -> dict[str, Any]:
    """全公司一条时间线：立项、请示、预审、裁决、验收、对话。只读。"""
    ev: list[dict[str, Any]] = []
    # 大厅永久历史(2026-06-23):不再只显示"活跃/有工单"的会议——所有真实会议永久留在大厅,
    # 船主能一直回看(发言再不"消失")。仅过滤演示/mock 垃圾。
    visible_rooms: set[str] = set()
    for p in 会议目录.glob("*.md"):
        if not _演示历史(_解析会议(p.name)):
            visible_rooms.add(p.name)
    # 暂停(要问船主)的会议也并入(可能无 .md):发言+待问已落盘,船主看着回话续会(债⑥)
    for st in 会议目录.glob("*.待续.json"):
        visible_rooms.add(st.name[:-len(".待续.json")] + ".md")
    for p in sorted(会议目录.glob("*.md")):
        if p.name not in visible_rooms:
            continue
        info = _解析会议(p.name)
        ev.append({"k": "立项", "t": _规范时刻(info["时间"]), "who": "项目经理", "role": "项目经理",
                   "text": info["标题"], "副": (info["需求"] or "")[:140], "room": p.name})
    subs = _读全部子工单()
    name2mid = {p.name: str(meta.get("来源会议") or "") for p, _c, meta in subs}
    for 桶, cards in 审批.列表().items():
        for c in cards:
            room = name2mid.get(str(c.get("任务", "")), "")
            visible_rooms.add(room)
            ev.append({"k": "请示", "t": _规范时刻(str(c.get("创建时间", ""))), "who": c.get("岗位", ""),
                       "role": c.get("岗位", ""),
                       "text": c.get("内容", ""), "副": c.get("建议", ""), "桶": 桶,
                       "id": c.get("id", ""), "room": room})
            if c.get("经理理由"):
                ev.append({"k": "决", "t": _规范时刻(str(c.get("经理时间", ""))), "who": "项目经理",
                           "role": "项目经理", "text": f"预审「{c.get('经理裁决', '')}」：{c.get('经理理由', '')}", "room": room})
            if str(c.get("状态", "")) in ("批", "驳"):
                ev.append({"k": "决", "t": _规范时刻(str(c.get("船主时间", ""))), "who": "船主",
                           "role": "船主", "text": f"裁决「{c.get('状态', '')}」{c.get('船主理由', '')}", "room": room})
    for p, col, meta in subs:
        room = str(meta.get("来源会议") or "")
        if room and room in visible_rooms and col in ("进行中", "待办", "待验收"):
            ev.extend(_日志里程碑(p, col, meta))
    ev.extend(_会议大厅事件(visible_rooms))
    for p, _col, meta in subs:
        for ln in p.read_text(encoding="utf-8").splitlines():
            m = re.match(r">\s*\[船主裁决 ([^\]]+)\]\s*\*\*(\S+)\*\*\s*(.*)", ln.lstrip())
            if m:
                room = str(meta.get("来源会议") or "")
                if room not in visible_rooms:
                    continue
                ev.append({"k": "验", "t": _规范时刻(m.group(1)), "who": "船主", "role": "船主",
                           "text": f"验收「{m.group(2)}」{p.name} {m.group(3)}".strip(),
                           "room": room})
    for r in 岗位:
        for m2 in 信箱(r):
            ev.append({"k": "话", "t": _规范时刻(m2["时间"]), "who": m2["who"], "text": m2["text"], "role": r})
    # 活公司的「场所」对话正源（船主圣域 2026-06-26：场所里发生的内容忠实记录、永久留存）。
    # 大厅 + 会议室各有持久层；活厅路径把船主发话/本人发言/开会逐条落在这里，不再靠会议/协同文件逆向拼凑。
    # 先按二级精度时间把场所内事件的真实先后排好（船主话→开会→本人报告），再并进大厅时间线。
    import 会议室记录
    import 大厅记录
    # 大厅永久气泡 = 船主发话 + PM 报告（大厅记录） + 会议标签锚点（k:会议）。
    # 会议里每个人的逐句发言（会议室记录的 k:话）**不再**作为大厅独立气泡——那些活的过程
    # 由实时「工作现场」按人分层展示（船主问题5：分到每人姓名下）。会议室 jsonl 仍永久留痕（圣域），
    # 只是大厅不再把它摊成一串气泡和工作现场打架（消除双重显示）。
    大厅事件 = 大厅记录.读对话()
    已有会议锚点 = {str(e.get("会议id") or "") for e in 大厅事件 if e.get("k") == "会议"}
    旧会议锚点 = []
    for e in 会议室记录.读会议():
        if e.get("k") != "会":
            continue
        mid = str(e.get("会议id") or "")
        if mid and mid in 已有会议锚点:
            continue
        旧会议锚点.append({**e, "k": "会议", "状态": "已结束", "参会": e.get("参会") or []})
    场所事件 = 大厅事件 + 旧会议锚点
    场所事件.sort(key=lambda e: e.get("t") or "")
    for d in 场所事件:
        ev.append({**d, "t": _规范时刻(d.get("t", "")) or str(d.get("t") or "")})
    ev.sort(key=lambda e: e.get("t") or "0000")
    import 大厅检索
    场次 = 大厅检索.场次索引()
    return {"事件": ev, "历史日期": [x["日期"] for x in 场次], "历史场次": 场次}


def 大厅某日(day: str) -> dict[str, Any]:
    """场次页按需回看一天；归档再久也能读，但不把全部历史塞进常驻 `/hall`。"""
    import 大厅记录
    events = 大厅记录.读某日(day)
    events.sort(key=lambda e: e.get("t") or "")
    return {"事件": events, "历史日期": [day] if events else []}


def 看板(模式: str, 岗位: list[str]) -> dict[str, Any]:
    """按来源版本缓存整张看板；多窗口同一刻只允许一份重算。"""
    key = (str(模式), tuple(岗位))
    while True:
        source_version = _看板源版本(key)
        with _看板条件:
            if (
                _看板缓存["数据"] is not None
                and _看板缓存["键"] == key
                and _看板缓存["源版本"] == source_version
            ):
                return _看板缓存["数据"]
            if not _看板缓存["计算中"]:
                _看板缓存["计算中"] = True
                version = _看板缓存["版本"]
                break
            _看板条件.wait()
    try:
        result = _看板_算(模式, 岗位)
    except Exception:
        with _看板条件:
            _看板缓存["计算中"] = False
            _看板条件.notify_all()
        raise
    with _看板条件:
        if version == _看板缓存["版本"]:
            _看板缓存.update({"t": time.time(), "数据": result, "键": key, "源版本": source_version})
            _看板缓存["下一兜底"] = _看板时钟() + _看板兜底秒
        _看板缓存["计算中"] = False
        _看板条件.notify_all()
    return result


def _看板_算(模式: str, 岗位: list[str]) -> dict[str, Any]:
    try:
        from 模型接入 import 成员信息
        members = 成员信息(岗位)
    except Exception:
        members = [{"name": r, "title": r, "model": "未知"} for r in 岗位]
    try:   # 挂职级/职称/信任/晋升进度，班子视图显出来（升职真接管权力、晋升长跑得让船主看得见）
        import 信誉
        import 晋升
        import 职级
        经理 = 职级.现任经理()
        for m in members:
            人 = str(m.get("名字") or m.get("title") or m.get("name") or "").strip()
            if not 人:
                continue
            级 = 职级.评级(人)
            m["评级"] = 级
            m["职称"] = 职级.职称(人)
            m["是部门头"] = bool(职级.部门头(人) == 人)
            m["是经理"] = bool(人 == 经理)
            m["信任等级"] = 信誉.信任等级(人)
            m["信誉分"] = 信誉.信誉分(人)
            st = 晋升.状态(人)
            m["晋升分"] = st.get("分", 0)
            m["晋升达标"] = 晋升.达标线.get(级 + 1, 0)
            m["晋升试用"] = bool(st.get("冻结"))
    except Exception:  # noqa: BLE001 职级/信誉挂不上不拖累看板
        pass
    return {
        "模式": 模式,
        "急停": 急停文件.exists(),
        "岗位": 岗位,
        "成员": members,
        "项目": 项目列表(),
        # 整张看板只在来源变化时才重算；此时待办也必须同步重算，不能再沿用两秒旧值。
        "待办": 待办汇总(强制=True),
    }


def _角色模型(role: str) -> dict[str, Any]:
    try:
        from 模型接入 import 成员信息
        for m in 成员信息([role]):
            if m.get("name") == role:
                return m
    except Exception:
        pass
    return {"name": role, "title": role, "model": "未知"}
