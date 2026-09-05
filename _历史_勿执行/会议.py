#!/usr/bin/env python3
"""办公室v3项目会层：先讨论方案，再把会议结论落成待确认工单。"""
from __future__ import annotations

import datetime as dt
import concurrent.futures as cf
import json
import os
import re
from pathlib import Path
from typing import Any

COMPANY = Path(__file__).resolve().parents[1]
待确认 = COMPANY / "工单" / "待确认"
已作废 = COMPANY / "工单" / "已作废"
会议目录 = COMPANY / "会议"
可参会岗位 = ["首席工程师", "工程师", "文案工程师（兼职）", "测试工程师"]


def 初始化() -> None:
    待确认.mkdir(parents=True, exist_ok=True)
    会议目录.mkdir(parents=True, exist_ok=True)


def _编号() -> int:
    return len(list(会议目录.glob("*.md"))) + 1


def _安全名(s: str) -> str:
    t = re.sub(r"[^\w.-]+", "_", s, flags=re.ASCII).strip("_")
    return t[:52] or "task"


def _提取_json(raw: str) -> dict[str, Any]:
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError("没有JSON对象")
    data = json.loads(raw[start:end])
    if not isinstance(data, dict):
        raise ValueError("顶层不是JSON对象")
    return data


def _去重(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _列表化(v: Any) -> list[str]:
    """防 bug2:模型有时把『验收标准/资料需求』返回成 str(本该是 list)。
    str→整条作一项;list/tuple→逐项去空白;其他→空表。避免 `for x in 字符串` 逐字符渲染成『- 交\\n- 付』。"""
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


def _紧凑(obj: Any, limit: int = 9000) -> str:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    return text if len(text) <= limit else text[:limit] + "\n...（已截断）"


def _校验(data: dict[str, Any]) -> dict[str, Any]:
    data.setdefault("项目会", {})
    meeting = data["项目会"] if isinstance(data.get("项目会"), dict) else {}
    meeting.setdefault("议题", data.get("标题", "办公室v3任务"))
    meeting.setdefault("参会", [])
    meeting.setdefault("发言", [])
    meeting.setdefault("决议", [])
    meeting.setdefault("验收标准", [])
    meeting.setdefault("资料需求", [])
    data["项目会"] = meeting
    subs = data.get("子工单")
    if not isinstance(subs, list) or not subs:
        raise ValueError("子工单为空")
    for i, item in enumerate(subs, 1):
        if not isinstance(item, dict):
            raise ValueError(f"第{i}个子工单不是对象")
        for k in ("标题", "岗位", "任务", "白名单"):
            if not item.get(k):
                raise ValueError(f"第{i}个子工单缺{k}")
        if not isinstance(item["白名单"], list):
            raise ValueError(f"第{i}个子工单白名单必须是数组")
        item.setdefault("依赖", [])
        item.setdefault("预算步数", 8)
        item.setdefault("预算依据", "未写")
        item.setdefault("预计字数", "")
        item.setdefault("预计token", "")
        item.setdefault("验收命令", "")
        item.setdefault("会议依据", "")
    data.setdefault("标题", "办公室v3任务")
    data.setdefault("覆盖说明", "待补充")
    return data


def _mock分解(需求: str) -> dict[str, Any]:
    return {
        "标题": "mock-v3执行闭环",
        "覆盖说明": "项目会确认：用两张子工单验证执行闭环，一张写入演示产物，一张读取资料并请示。",
        "项目会": {
            "议题": "mock-v3执行闭环项目会",
            "参会": ["船主", "项目经理", "首席工程师", "工程师", "测试工程师"],
            "发言": [
                {"发言人": "项目经理", "类型": "开场", "内容": f"收到船主议题：{需求}。本会先澄清目标、边界和验收，再决定是否拆工单。"},
                {"发言人": "首席工程师", "类型": "方案", "内容": "建议先做最小可验闭环，避免一上来扩大到真实产品目录。"},
                {"发言人": "工程师", "类型": "风险", "内容": "需要验证读写、请示和回炉路径，产物应限制在 mock 目录。"},
                {"发言人": "测试工程师", "类型": "验收", "内容": "验收标准应包含：待确认、确认开工、请示裁决、待验收日志均可追溯。"},
                {"发言人": "项目经理", "类型": "结论", "内容": "采用最小闭环方案，工单只触碰工具/mock产物目录。"},
            ],
            "决议": [
                {"编号": "D1", "内容": "先验证最小执行闭环，不触碰真实产品目录。", "落实": "mock核心写入"},
                {"编号": "D2", "内容": "必须覆盖接口侧读取和一次请示裁决。", "落实": "mock接口确认"},
            ],
            "验收标准": ["两张工单进入待确认", "确认开工后并行执行", "请示能被裁决并继续", "完成后进入待验收"],
            "资料需求": ["班规", "花名册", "当前引擎白名单规则"],
        },
        "子工单": [
            {
                "标题": "mock核心写入",
                "岗位": "首席工程师",
                "任务": f"围绕需求做最小实现演示：{需求}",
                "白名单": ["工具/mock产物/**"],
                "依赖": [],
                "预算步数": 6,
                "预算依据": "读资料1步、写文件1步、请示/续步1步、完成复盘1步，预留2步。",
                "预计字数": "50-200",
                "预计token": "3000-8000",
                "验收命令": "",
                "会议依据": "D1",
            },
            {
                "标题": "mock接口确认",
                "岗位": "工程师",
                "任务": f"围绕需求做接口侧演示，并在执行中发起一次请示：{需求}",
                "白名单": ["工具/mock产物/**"],
                "依赖": [],
                "预算步数": 6,
                "预算依据": "读资料1步、写文件1步、请示1步、完成复盘1步，预留2步。",
                "预计字数": "50-200",
                "预计token": "3000-8000",
                "验收命令": "",
                "会议依据": "D2",
            },
        ],
    }


def _真分解(需求: str, progress=None) -> dict[str, Any]:
    from 模型接入 import 调用

    _进度(progress, "项目经理正在准备项目会议题。")
    plan = _会前议题(需求)
    roles = [x for x in _去重([str(x) for x in plan.get("参会", [])]) if x in 可参会岗位]
    if not roles:
        roles = ["文案工程师（兼职）", "测试工程师"]
    _进度(progress, "项目会参会岗位：" + "、".join(roles))
    speeches = _并行岗位发言(需求, plan, roles, progress=progress)
    _进度(progress, "项目经理正在汇总会议发言，形成决议和工单。")
    final_prompt = (
        "你是开发公司的项目经理。下面是真实项目会材料：你的会前议题，以及相关岗位各自的发言。"
        "你现在只能基于这些发言、公司制度和船主需求形成会议决议与待确认工单，不能把交办冒充成讨论。\n"
        "只输出JSON对象，不要Markdown，不要解释。\n"
        "JSON字段: 标题, 覆盖说明, 项目经理结论, 决议, 验收标准, 资料需求, 子工单。\n"
        "其中'项目经理结论'字段用大白话写——像你在会上拍板那样，几句话说清最后怎么定、为啥、采纳了谁的意见、谁的顾虑怎么照顾；别用一二三、别列条。其余字段照常给结构化数据。\n"
        "决议数组元素字段: 编号, 内容, 落实。编号用D1/D2/D3。\n"
        "子工单数组元素字段: 标题, 岗位, 任务, 白名单(数组), 依赖(数组, 可空), 预算步数(整数), "
        "预算依据, 预计字数, 预计token, 验收命令, 会议依据。\n"
        "每张子工单的会议依据必须引用决议编号；岗位只能从 项目经理/首席工程师/工程师/文案工程师（兼职）/测试工程师 中选。"
        "白名单必须逐条列出，只允许未来执行触碰这些路径。\n"
        "预算必须覆盖读资料、实现/写入、读回自检、完成复盘、必要测试交接；不要只够写文件。\n\n"
        f"船主需求:\n{需求}\n\n"
        f"会前议题:\n{_紧凑(plan, 5000)}\n\n"
        f"岗位发言（含全部轮次）:\n{_紧凑(speeches, 12000)}"
    )
    raw = 调用("项目经理", final_prompt, 最大tokens=6144)
    try:
        final = _提取_json(raw)
    except Exception:
        raw2 = 调用(
            "项目经理",
            final_prompt + "\n\n上次输出不是合法JSON。请立刻重试，只输出JSON，不要解释。",
            最大tokens=6144,
        )
        final = _提取_json(raw2)
    meeting = {
        "议题": plan.get("议题") or final.get("标题") or "项目会",
        "参会": _去重(["船主", "项目经理", *roles]),
        "发言": [
            {
                "发言人": "项目经理",
                "类型": "开场",
                "内容": str(plan.get("主持说明") or plan.get("覆盖说明") or "先讨论需求、资料、风险、标准和岗位分工，再形成决议。"),
            },
            *speeches,
            {"发言人": "项目经理", "类型": "结论", "内容": str(final.get("项目经理结论") or final.get("覆盖说明") or "已形成会议决议和待确认工单。")},
        ],
        "决议": final.get("决议") or [],
        "验收标准": final.get("验收标准") or [],
        "资料需求": final.get("资料需求") or plan.get("资料需求") or [],
    }
    data = {
        "标题": final.get("标题") or plan.get("标题") or "办公室v3任务",
        "覆盖说明": final.get("覆盖说明") or plan.get("覆盖说明") or "项目会已形成决议。",
        "项目会": meeting,
        "子工单": final.get("子工单") or [],
    }
    checked = _校验(data)
    _进度(progress, f"项目会已形成：{len(checked['项目会'].get('决议') or [])} 条决议，{len(checked['子工单'])} 张待确认工单。")
    return checked


def _会前议题(需求: str) -> dict[str, Any]:
    from 模型接入 import 调用

    prompt = (
        "你是开发公司的项目经理。现在只做项目会会前准备，不生成工单。"
        "你要决定需要哪些岗位参加，并给出讨论议题。只输出JSON对象。\n"
        "JSON字段: 标题, 议题, 覆盖说明, 主持说明, 参会, 讨论问题, 资料需求, 验收关注, 风险关注。\n"
        "参会只能从 首席工程师/工程师/文案工程师（兼职）/测试工程师 中选择，选真正相关的岗位；"
        "一般至少要有执行岗位和验收/反证岗位。不要把船主或项目经理放进参会数组。\n\n"
        f"船主需求:\n{需求}"
    )
    raw = 调用("项目经理", prompt, 最大tokens=2048)
    try:
        data = _提取_json(raw)
    except Exception:
        data = {
            "标题": "办公室v3任务",
            "议题": "项目会",
            "覆盖说明": "项目经理会前准备输出不合格，改用保守会议配置。",
            "主持说明": "先由执行与验收岗位分别说明理解、资料需求、风险和验收标准。",
            "参会": ["文案工程师（兼职）", "测试工程师"],
            "讨论问题": ["需求怎么理解", "需要哪些资料", "验收如何证明", "是否有权限或预算风险"],
            "资料需求": [],
            "验收关注": [],
            "风险关注": ["会前议题JSON解析失败"],
        }
    data.setdefault("标题", "办公室v3任务")
    data.setdefault("议题", data.get("标题", "项目会"))
    data.setdefault("覆盖说明", "")
    data.setdefault("主持说明", "")
    data.setdefault("讨论问题", [])
    data.setdefault("资料需求", [])
    data.setdefault("验收关注", [])
    data.setdefault("风险关注", [])
    data["参会"] = [x for x in _去重([str(x) for x in data.get("参会", [])]) if x in 可参会岗位]
    if not data["参会"]:
        data["参会"] = ["文案工程师（兼职）", "测试工程师"]
    return data


def _评估收敛(需求: str, 发言流: list[dict[str, str]]) -> dict[str, Any]:
    """项目经理判：是否卡在缺关键证据(要去查)、以及讨论够不够拍板（方案82-83质量判据，不靠固定轮数）。"""
    from 模型接入 import 调用

    桌面 = "\n\n".join(f"{s.get('发言人')}（{s.get('类型', '')}）：{s.get('内容')}" for s in 发言流)
    prompt = (
        "你是项目经理，在主持这场会。下面是目前为止的全部发言。\n\n"
        + 桌面
        + "\n\n你判两件事，只输出JSON：\n"
        + "1) 有没有人卡在『缺某个关键文件/证据、读不到、没法下判断』上？有就把要查的文件名/路径列进 缺证据（抓发言里提到的文件名原话）。\n"
        + "2) 不缺证据的话，现在讨论够不够清楚、够不够支撑你拍板出决议和工单？\n"
        + "JSON：{\"缺证据\": [\"文件名或路径\"], \"够了\": true, \"理由\": \"一句话\"}。\n"
        + "有人卡在缺证据就把文件列进缺证据、够了填false；不缺证据且说透了填true；不缺证据但关键分歧没说透填false。"
    )
    raw = 调用("项目经理", prompt, 最大tokens=2048)
    try:
        d = _提取_json(raw)
        缺 = d.get("缺证据") or []
        if not isinstance(缺, list):
            缺 = [str(缺)]
        return {"缺证据": [str(x) for x in 缺 if str(x).strip()], "够了": bool(d.get("够了")), "理由": str(d.get("理由", ""))}
    except Exception:  # noqa: BLE001
        return {"缺证据": [], "够了": True, "理由": "评估输出无法解析，默认收敛、不空转"}


def _尝试查阅(文件: str) -> str:
    """在公司可读范围(COMPANY 目录下)安全读取证据；不在范围或不存在返回空（→该请示船主）。"""
    名 = str(文件 or "").strip().strip("`《》'\" ")
    if not 名:
        return ""
    for 候选 in (COMPANY / 名, COMPANY / 名.lstrip("/")):
        try:
            p = 候选.resolve()
            base = COMPANY.resolve()
            if (base == p or base in p.parents) and p.is_file():
                return p.read_text(encoding="utf-8", errors="replace")[:6000]
        except Exception:  # noqa: BLE001
            continue
    return ""


def _并行岗位发言(需求: str, plan: dict[str, Any], roles: list[str], progress=None, 上限: int = 4) -> list[dict[str, str]]:
    """圆桌（方案_v1 第82-83行：不固定轮数）：各自发言→项目经理判够不够→不够再聊一轮→够则收敛；硬上限兜底。"""
    from 办公室 import 发言名

    def _跑(fn, 标签: str) -> dict[str, dict[str, str]]:
        res: dict[str, dict[str, str]] = {}
        with cf.ThreadPoolExecutor(max_workers=max(1, min(len(roles), 5))) as ex:
            futs = {ex.submit(fn, role): role for role in roles}
            for fut in cf.as_completed(futs):
                role = futs[fut]
                try:
                    res[role] = fut.result()
                    _进度(progress, f"{role} 已{标签}。")
                except Exception as exc:  # noqa: BLE001
                    res[role] = {"发言人": role, "类型": "异常", "内容": f"{role}{标签}失败：{exc}"}
                    _进度(progress, f"{role} {标签}失败，已记录。")
        return res

    for role in roles:
        _进度(progress, f"已邀请 {role} 参会。")
    全部: list[dict[str, str]] = []
    收敛 = {"够了": False, "理由": ""}
    轮 = 0
    while 轮 < max(1, 上限):
        轮 += 1
        if 轮 == 1:
            本轮 = _跑(lambda role: _岗位发言(role, 需求, plan), f"第{轮}轮发言")
        else:
            桌面 = "\n\n".join(f"{发言名(s['发言人'])}（{s['发言人']}）：{s['内容']}" for s in 全部)
            本轮 = _跑(lambda role, _桌面=桌面: _岗位回应(role, 需求, _桌面), f"第{轮}轮回应")
        for role in roles:
            if role in 本轮:
                全部.append(本轮[role])

        评 = _评估收敛(需求, 全部)
        缺 = 评.get("缺证据") or []
        if 缺:
            待船主: list[str] = []
            for f in 缺:
                内容 = _尝试查阅(str(f))
                if 内容:
                    全部.append({"发言人": "资料室", "类型": "已查阅", "内容": f"【{f}】（按申请查阅）\n{内容[:3000]}"})
                    _进度(progress, f"按申请查阅：{f}，内容已补进会议。")
                else:
                    待船主.append(str(f))
            if 待船主:
                try:
                    import 审批
                    审批.创建请示(
                        str(plan.get("议题") or "项目会"), "项目经理",
                        f"圆桌卡在缺关键证据：{'、'.join(待船主)}（公司可读范围内找不到）。",
                        "请船主授权读取或直接提供该证据，再重开此会。",
                        直接上报=True,
                    )
                except Exception:  # noqa: BLE001
                    pass
                全部.append({
                    "发言人": "项目经理", "类型": "缺证据待船主",
                    "内容": f"卡在缺关键证据：{'、'.join(待船主)}（公司读不到）。已请示船主，待提供后重开此会，先不硬出实现工单。",
                })
                _进度(progress, f"缺关键证据且公司读不到：{'、'.join(待船主)}，已请示船主，圆桌收尾。")
                收敛 = {"够了": False, "理由": "缺证据待船主"}
                break
            全部.append({"发言人": "项目经理", "类型": "主持", "内容": "相关资料已按申请补进会议，接着往下聊。"})
            _进度(progress, "已补充资料，继续讨论。")
            continue

        全部.append({
            "发言人": "项目经理", "类型": "收敛判断",
            "内容": f"（第{轮}轮后）{'够了，可以收敛' if 评.get('够了') else '还没够，再聊一轮'}：{评.get('理由', '')}",
        })
        收敛 = {"够了": bool(评.get("够了")), "理由": str(评.get("理由", ""))}
        _进度(progress, f"第{轮}轮后项目经理评估：{'够了，收敛' if 收敛['够了'] else '没够，再聊一轮'}——{收敛.get('理由', '')}")
        if 收敛["够了"]:
            break

    if not 收敛["够了"] and 收敛.get("理由") != "缺证据待船主":
        全部.append({
            "发言人": "项目经理", "类型": "未谈拢",
            "内容": f"聊了{轮}轮仍未谈拢（{收敛.get('理由', '')}）。按现有讨论先收尾，建议船主介入定夺。",
        })
    return 全部


def _岗位发言(岗位: str, 需求: str, plan: dict[str, Any]) -> dict[str, str]:
    """第一轮：用说人话的发言上下文(不灌培训材料)各自亮观点，返回自然语言发言。"""
    from 办公室 import 发言上下文
    from 模型接入 import 调用

    议题 = str(plan.get("议题") or "").strip()
    问题 = "、".join(str(x) for x in (plan.get("讨论问题") or [])[:5])
    prompt = (
        发言上下文(岗位)
        + "\n\n"
        + f"船主要办的事：{需求}\n"
        + (f"这次会上要谈的：{议题}\n" if 议题 and 议题 != 需求 else "")
        + (f"重点掰扯这几点：{问题}\n" if 问题 else "")
        + "\n轮到你了，说说你怎么看、站哪边、为啥——大白话，几句话，别列条。"
    )
    raw = 调用(岗位, prompt).strip()
    return {"发言人": 岗位, "类型": _岗位默认发言类型(岗位), "内容": (raw or "（没说话）")[:2000]}


def _岗位回应(岗位: str, 需求: str, 桌面: str) -> dict[str, str]:
    """第二轮：看见彼此的发言后，点名回应/反驳/被说服。"""
    from 办公室 import 发言上下文
    from 模型接入 import 调用

    prompt = (
        发言上下文(岗位)
        + "\n\n刚才会上大家各自说的——\n\n"
        + 桌面
        + "\n\n你也在场，都听见了。回应一下：你同意谁、想驳谁、补啥，还是被谁说服改主意了？"
        + "直接点名冲着具体的话说，别又从头自说自话。大白话，几句话，别列条。"
    )
    raw = 调用(岗位, prompt).strip()
    return {"发言人": 岗位, "类型": "回应", "内容": (raw or "（没回应）")[:2000]}


def _岗位默认发言类型(岗位: str) -> str:
    if 岗位 == "测试工程师":
        return "验收"
    if 岗位 == "文案工程师（兼职）":
        return "方案"
    if 岗位 == "工程师":
        return "风险"
    if 岗位 == "首席工程师":
        return "方案"
    return "发言"


def _进度(progress, text: str) -> None:
    if not progress:
        return
    try:
        progress(text)
    except Exception:  # noqa: BLE001
        pass


def kickoff(需求: str, mock: bool | None = None, progress=None) -> dict[str, Any]:
    初始化()
    use_mock = os.environ.get("XJ_MOCK") == "1" if mock is None else mock
    if use_mock:
        _进度(progress, "演示模式：生成本地 mock 项目会。")
        data = _校验(_mock分解(需求))
    else:
        data = _校验(_真分解(需求, progress=progress))
    idx = _编号()
    title = _安全名(str(data["标题"]))
    meeting = 会议目录 / f"{idx:03d}-{title}.md"
    _进度(progress, f"正在写入项目会纪要：{meeting.name}")
    lines = [
        f"# v3项目会 {idx:03d} · {data['标题']}",
        f"时间: {dt.datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 原始需求",
        需求,
        "",
        "## 覆盖说明",
        str(data["覆盖说明"]),
        "",
        "## 项目会",
        _渲染项目会(data["项目会"]),
        "",
        "## 子工单",
    ]
    names: list[str] = []
    task_records: list[dict[str, Any]] = []
    for i, item in enumerate(data["子工单"], 1):
        name = f"V3-{idx:03d}-{i}_{_安全名(str(item['标题']))}.md"
        names.append(name)
        task_records.append({**item, "文件": name})
        lines.append(f"- `{name}` ｜ {item['岗位']} ｜ 依据: {item.get('会议依据', '') or '未写'} ｜ 依赖: {', '.join(item.get('依赖') or []) or '无'}")
        _写子工单(待确认 / name, item, meeting.name)
        _进度(progress, f"已生成待确认工单：{name}")
    meeting.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        import 协同

        协同.记录项目会(meeting.name, str(data["标题"]), 需求, str(data["覆盖说明"]), data["项目会"], task_records)
        _进度(progress, "已写入项目会实时事件流。")
    except Exception:  # noqa: BLE001
        pass
    return {"会议": meeting.name, "工单": names, "覆盖说明": data["覆盖说明"]}


def _渲染项目会(meeting: dict[str, Any]) -> str:
    lines = [
        f"议题：{meeting.get('议题', '')}",
        f"参会：{', '.join(map(str, meeting.get('参会') or [])) or '未写'}",
        "",
        "### 发言",
    ]
    for item in meeting.get("发言") or []:
        if not isinstance(item, dict):
            continue
        lines.append(f"- **{item.get('发言人', '')}｜{item.get('类型', '')}**：{item.get('内容', '')}")
    lines.append("")
    lines.append("### 决议")
    for item in meeting.get("决议") or []:
        if not isinstance(item, dict):
            continue
        lines.append(f"- `{item.get('编号', '')}` {item.get('内容', '')} → {item.get('落实', '')}")
    lines.append("")
    lines.append("### 验收标准")
    for item in _列表化(meeting.get("验收标准")):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("### 资料需求")
    for item in _列表化(meeting.get("资料需求")):
        lines.append(f"- {item}")
    return "\n".join(lines).strip()


def _写子工单(path: Path, item: dict[str, Any], meeting: str) -> None:
    deps = ", ".join(map(str, item.get("依赖") or []))
    whitelist = ", ".join(map(str, item.get("白名单") or []))
    text = f"""# v3工单 · {item['标题']}

| | |
|---|---|
| 来源会议 | {meeting} |
| 岗位 | {item['岗位']} |
| 状态 | 待确认 |
| 依赖 | {deps} |
| 预算步数 | {int(item.get('预算步数', 8))} |
| 预算依据 | {item.get('预算依据', '')} |
| 预计字数 | {item.get('预计字数', '')} |
| 预计token | {item.get('预计token', '')} |
| 会议依据 | {item.get('会议依据', '')} |
| 白名单 | {whitelist} |
| 验收命令 | {item.get('验收命令', '')} |

## 任务
{item['任务']}

## 会议落实
本工单落实项目会决议：{item.get('会议依据', '') or '未写'}。

## 交付物
- [ ] 按白名单完成改动
- [ ] 写入执行日志
- [ ] 按工作质量门完成自查/审查
- [ ] 完成动作必须带复盘：做对/做错/下次

## 铁律
班规十条全文适用。本单由办公室v3引擎执行。
"""
    path.write_text(text, encoding="utf-8")


def 待确认列表() -> list[dict[str, str]]:
    初始化()
    out: list[dict[str, str]] = []
    for p in sorted(待确认.glob("*.md")):
        out.append({"name": p.name, "text": p.read_text(encoding="utf-8")})
    return out


def 移作废(name: str) -> bool:
    """打回处理：把一张待确认工单移到 工单/已作废/（撤废，或重做前清掉旧工单）。
    返回是否真的移动了。真撤真作废，不再是旧『打回重分解』那种只追加一行理由的空操作。"""
    初始化()
    已作废.mkdir(parents=True, exist_ok=True)
    src = 待确认 / name
    if not src.exists():
        return False
    now = dt.datetime.now().strftime("%m-%d %H:%M")
    try:
        with src.open("a", encoding="utf-8") as f:
            f.write(f"\n> [船主打回 · 作废 {now}]\n")
    except Exception:  # noqa: BLE001
        pass
    src.rename(已作废 / name)
    return True
