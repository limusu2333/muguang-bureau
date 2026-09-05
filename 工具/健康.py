#!/usr/bin/env python3
"""公司系统健康快照：只读汇总，不调用模型、不修改业务状态。"""
from __future__ import annotations

import datetime as dt
import os
import re
import zipfile
from typing import Any

from 根 import 数据根

COMPANY = 数据根
健康告警位 = COMPANY / "运行状态" / "健康告警.json"


def _时间(raw: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(raw or "").replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return None


def _任务匹配键(item: dict[str, Any]) -> tuple[str, str, str]:
    """用任务事实匹配重试，不把问号、句号差异误判成两项任务。"""
    内容 = re.sub(r"[\W_]+", "", str(item.get("内容") or ""), flags=re.UNICODE)
    return (
        内容,
        str(item.get("负责人") or "").strip(),
        str(item.get("房间") or "").strip(),
    )


def _后续已成功(item: dict[str, Any], 已完成: list[dict[str, Any]]) -> bool:
    """同一任务之后已经成功完成，就不再把旧失败当成活动告警。"""
    失败时间 = _时间(str(item.get("更新时间") or ""))
    if not 失败时间:
        return False
    键 = _任务匹配键(item)
    for 完成项 in 已完成:
        if _任务匹配键(完成项) != 键 or str(完成项.get("状态") or "") != "已完成":
            continue
        完成时间 = _时间(str(完成项.get("更新时间") or ""))
        if 完成时间 and 完成时间 > 失败时间:
            return True
    return False


def _延期任务(records: list[dict[str, Any]]) -> dict[str, dt.datetime]:
    """读取每项任务最后一次处理；最后决定为“稍后”才算已延期。"""
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        alert = record.get("告警") or {}
        task_id = str(alert.get("任务id") or "")
        if task_id:
            latest[task_id] = record
    deferred: dict[str, dt.datetime] = {}
    for task_id, record in latest.items():
        if str(record.get("动作") or "") != "稍后":
            continue
        when = _时间(str(record.get("更新时间") or record.get("时间") or ""))
        if when:
            deferred[task_id] = when
    return deferred


def _读取延期任务() -> dict[str, dt.datetime]:
    try:
        from 状态存储 import 读JSON

        state = 读JSON(健康告警位, 默认={}, 类型=dict) or {}
        return _延期任务([item for item in (state.get("记录") or []) if isinstance(item, dict)])
    except Exception:  # noqa: BLE001
        return {}


def _告警已延期(alert: dict[str, Any], deferred: dict[str, dt.datetime]) -> bool:
    """只隐藏用户已经明确延期、且任务之后没有新变化的告警。"""
    task_id = str(alert.get("任务id") or "")
    deferred_at = deferred.get(task_id)
    if not task_id or not deferred_at:
        return False
    alert_at = _时间(str(alert.get("更新时间") or ""))
    return alert_at is None or alert_at <= deferred_at


def _区分当前与历史告警(
    alerts: list[dict[str, Any]],
    活动起点: dt.datetime | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """启动前的任务失败留在历史；本次启动后新发生的任务失败才进入当前告警。"""
    if 活动起点 is None:
        return alerts, []
    current: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    for alert in alerts:
        when = _时间(str(alert.get("更新时间") or ""))
        if alert.get("类别") == "任务" and when and when < 活动起点:
            history.append(alert)
        else:
            current.append(alert)
    return current, history


def _问题(level: str, area: str, message: str) -> dict[str, str]:
    return {"级别": level, "区域": area, "说明": message}


def _大小(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _告警(
    level: str,
    area: str,
    title: str,
    detail: str,
    suggestion: str = "",
    *,
    target: str = "",
    task_id: str = "",
    owner: str = "",
    room: str = "",
    updated: str = "",
    task_status: str = "",
    owner_label: str = "",
    actions: list[dict[str, str]] | None = None,
    raw_detail: str | None = None,
    failure_type: str = "",
) -> dict[str, Any]:
    fingerprint = "|".join((task_id, updated, owner, task_status, title, detail))
    return {
        "级别": level,
        "类别": area,
        "标题": title,
        "详情": detail,
        "建议": suggestion,
        "目标": target,
        "任务id": task_id,
        "负责人": owner,
        "房间": room,
        "更新时间": updated,
        "任务状态": task_status,
        "负责人称谓": owner_label,
        "原始详情": detail if raw_detail is None else raw_detail,
        "故障类型": failure_type,
        "指纹": fingerprint,
        "可选动作": actions or [],
    }


def _短句(value: Any, limit: int = 30) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _负责人称谓(name: str) -> str:
    name = str(name or "").strip()
    if not name:
        return "任务负责人"
    # 有些系统任务登记的是岗位而不是姓名，再拼部门/职级会变成“员工项目经理”。
    if any(name.endswith(role) for role in ("项目经理", "经理", "主管", "工程师", "负责人", "船主")):
        # 任务记录有时只存岗位。用户需要同时看到岗位和实际接单人，才能知道是谁卡住了。
        try:
            from 活_本人 import _岗位的人 as _人

            person = str(_人(name) or "").strip()
            if person and person != name:
                return f"{name}{person}"
        except Exception:  # noqa: BLE001
            pass
        return name
    try:
        import 部门

        dept = 部门.认领部门(name)
        dept_name = str(getattr(dept, "目录名", "") or "").strip()
        level = str(部门.层级名(name) or "").strip()
        role = "主管" if level == "部门头" else ("经理" if level == "经理" else level)
        return f"{dept_name}{role}{name}" if dept_name or role else name
    except Exception:  # noqa: BLE001
        return name


def _处理动作(kind: str, title: str, description: str) -> dict[str, str]:
    return {"id": kind, "标题": title, "说明": description}


def _故障类型(error: str) -> str:
    text = str(error or "")
    lower = text.lower()
    if any(value in lower for value in (
        "upstream service temporarily unavailable",
        "service temporarily unavailable",
        "bad gateway",
        "gateway timeout",
        "http 502",
        "http 503",
        "http 504",
    )):
        return "模型服务无回应"
    if "timeout" in lower or "timed out" in lower or "超时" in text:
        return "模型调用超时"
    if any(value in lower for value in ("connection refused", "connection reset", "network is unreachable")):
        return "中转站连接失败"
    if "话题判定门" in text or "大厅意图判定" in text:
        return "大厅判断流程卡住"
    if any(value in text for value in ("AccessDenied", "403", "使用资格", "未购买", "Unpurchased")):
        return "模型使用资格不足"
    if any(value in text for value in ("权限", "白名单", "不允许", "拒绝")):
        return "权限边界拦截"
    return ""


def _失败事实(谁: str, 任务摘要: str, error: str, room: str) -> tuple[str, str, str]:
    """把任务失败拆成用户能判断的事实、结果和下一步；原始错误仍由调用方单独留存。"""
    kind = _故障类型(error)
    location = f"在{room}里" if room else ""
    if kind == "模型服务无回应":
        return (
            f"{谁}刚才{location}执行“{任务摘要}”时，模型服务没有回应，所以这次没有拿到结果。"
            "不是权限问题，也不是你的提问有问题。",
            f"{谁}执行“{任务摘要}”时模型没有回应",
            "按原要求重新执行这次任务；如果再次失败，系统会把新的原因单独报给你。",
        )
    if kind == "模型调用超时":
        return (
            f"{谁}刚才{location}执行“{任务摘要}”时，模型一直没有在规定时间内回应，所以这次没有拿到结果。",
            f"{谁}执行“{任务摘要}”时模型响应超时",
            "按原要求重新执行这次任务；如果仍然超时，再处理模型或网络问题。",
        )
    if kind == "中转站连接失败":
        return (
            f"{谁}刚才{location}执行“{任务摘要}”时，连接不上模型中转服务，所以这次没有拿到结果。",
            f"{谁}执行“{任务摘要}”时连接不上模型服务",
            "重新执行这次任务；如果仍失败，再检查中转服务连接。",
        )
    if kind == "大厅判断流程卡住":
        return (
            f"{谁}刚才{location}执行“{任务摘要}”时，大厅没有完成流程判断，所以任务没有继续，也没有拿到结果。",
            f"{谁}执行“{任务摘要}”时大厅流程没有走完",
            "让项目经理按原任务重新处理；这不是让你重新猜原因。",
        )
    if kind == "模型使用资格不足":
        return (
            f"{谁}刚才{location}执行“{任务摘要}”时，账号没有这个模型的使用资格，所以任务没有开始。",
            f"{谁}执行“{任务摘要}”时没有模型使用资格",
            "核对模型名称和账号权益；需要购买或换模型时，再按页面选项决定。",
        )
    if kind == "权限边界拦截":
        return (
            f"{谁}刚才{location}执行“{任务摘要}”时，被权限边界拦下，所以任务没有完成。",
            f"{谁}执行“{任务摘要}”时因权限边界失败",
            "只处理这项任务需要的权限；是否扩大权限由你决定。",
        )
    error_hint = _短句(error, 120) if error else "系统没有留下具体失败原因"
    return (
        f"{谁}刚才{location}执行“{任务摘要}”没有完成，也没有产生可验收结果。系统记录的原因是：{error_hint}。",
        f"{谁}执行“{任务摘要}”时失败",
        "让项目经理先核对原记录，再决定是否重试；如果需要越权，系统应再次向你请示。",
    )


def _任务动作(status: str, error: str) -> list[dict[str, str]]:
    if status == "被重启中断":
        primary = _处理动作("恢复任务", "恢复这项任务", "交给项目经理核对断点并继续处理")
    elif "没有真写" in error or "没有真实" in error:
        primary = _处理动作("补交产物", "让负责人补交产物", "项目经理核对原任务，并要求负责人拿出可验收文件")
    elif "不在公司仓或产品仓内" in error:
        primary = _处理动作("改正交付位置", "改到公司仓再交付", "不扩大目录权限，按现有边界重新提交")
    elif any(word in error for word in ("AccessDenied", "403", "使用资格", "未购买", "Unpurchased")):
        primary = _处理动作("核对模型权益", "核对模型权益并修正", "确认模型名和购买资格，必要时换成已经可用的模型")
    elif any(word in error for word in ("权限", "白名单", "不允许", "拒绝")):
        primary = _处理动作("核对权限", "核对并处理本次权限", "只判断这项任务需要的最小权限，扩大范围仍须再次请示")
    elif _故障类型(error) in {"模型服务无回应", "模型调用超时", "中转站连接失败"}:
        primary = _处理动作("重试任务", "重新执行这次任务", "按原要求再执行一遍，不改权限和模型配置")
    else:
        primary = _处理动作("查明并重试", "查明原因后重试", "让项目经理核对原记录，修正真实原因后继续")
    return [
        primary,
        _处理动作("稍后", "暂不处理，移到待处理列表", "这次先不派发，不在启动时打断你；任务有新变化时再提醒"),
        _处理动作("补充", "补充我的判断", "写下要求或判断，再交给项目经理处理"),
    ]


def _系统动作(area: str) -> list[dict[str, str]]:
    primary_by_area = {
        "备份": _处理动作("重新备份", "立即备份并核验", "由公司备份程序直接生成新备份，不经过模型转述"),
        "模型身份": _处理动作("重新核验模型", "重新核验员工模型", "逐个真实调用岗位模型，更新点名和可用状态"),
        "统一搜索": _处理动作("重新检测搜索", "重新检测公司搜索", "真实调用搜索和排序接口，查明配置、额度或网络问题"),
        "组织契约": _处理动作("修正组织配置", "修正组织配置", "核对岗位、部门和房间关系，只修正违反公司契约的配置"),
        "事件总线": _处理动作("修复事件记录", "修复事件记录", "先保留原始记录，再查明损坏或写入失败的原因"),
        "状态文件": _处理动作("修复状态文件", "修复状态文件", "先保留损坏文件，再恢复可读取的公司状态"),
        "管理动作": _处理动作("恢复管理动作", "核对并恢复管理动作", "查明执行卡停在哪里，再继续或明确结束"),
        "验收台": _处理动作("重试验收收尾", "重试验收收尾", "核对已经裁决但尚未完成的后续动作"),
        "容量": _处理动作("查明容量占用", "查明容量占用", "先列出占用来源；涉及删除时仍须再次请示"),
    }
    primary = primary_by_area.get(
        area,
        _处理动作("交给项目经理排查", "交给项目经理排查", "核对原始记录，查明原因后再继续处理"),
    )
    return [
        primary,
        _处理动作("稍后", "暂不处理，移到待处理列表", "这次先不派发，不在启动时打断你；问题有新变化时再提醒"),
        _处理动作("补充", "补充我的判断", "写下要求或判断，再交给项目经理处理"),
    ]


def _班子动作(name: str) -> list[dict[str, str]]:
    return [
        _处理动作("恢复岗位调用", f"核对并恢复{name}", "检查这个岗位的模型、账号、额度和网络，再做一次真实调用"),
        _处理动作("稍后", "暂不处理，移到待处理列表", "这次先不派发，不在启动时打断你；岗位有新变化时再提醒"),
        _处理动作("补充", "补充我的判断", "写下要求或判断，再交给项目经理处理"),
    ]


def 补齐告警(班子: dict[str, Any], 系统: dict[str, Any]) -> list[dict[str, Any]]:
    """把任务、系统和岗位问题统一成同一种可决策告警；不调用模型。"""
    alerts = [dict(item) for item in (系统.get("告警") or []) if isinstance(item, dict)]
    has_task_alerts = any(item.get("类别") == "任务" for item in alerts)
    system_copy = {
        "模型身份": ("员工模型身份需要重新核验", "重新点名并真实调用，避免继续使用过期记录。"),
        "统一搜索": ("公司搜索服务没有通过最近一次检查", "重新检测搜索和排序接口，查清是配置、额度还是网络问题。"),
        "组织契约": ("公司的岗位或房间配置不一致", "按现有组织规则核对并修正冲突项。"),
        "事件总线": ("公司的事件记录出现持久化故障", "先保留原始记录，再修复损坏或写入失败。"),
        "状态文件": ("公司的状态文件现在无法可靠读取", "先保留损坏文件，再恢复可读取状态。"),
        "管理动作": ("有管理动作停在执行途中", "核对原执行卡，明确继续还是结束。"),
        "验收台": ("有已裁决事项尚未完成收尾", "重试后续动作，并保留失败原因。"),
        "容量": ("公司运行记录占用已经超过预警线", "先查清占用来源；需要删除时再次向你请示。"),
    }
    for issue in 系统.get("问题") or []:
        if not isinstance(issue, dict) or issue.get("级别") not in ("错误", "警告"):
            continue
        area = str(issue.get("区域") or "系统")
        if area == "任务台":
            if has_task_alerts or (
                "任务" in 系统
                and int((系统.get("任务") or {}).get("当前待处理告警") or 0) == 0
            ):
                continue
        detail = str(issue.get("说明") or "没有留下具体原因。")
        if area == "备份":
            if "定时任务" in detail:
                title, suggestion = "公司的自动备份没有正常接入", "重新安装当前公司的定时任务，并确认它已被 macOS 加载。"
            elif "ZIP 完整性失败" in detail:
                title, suggestion = "最新公司备份已经损坏", "保留损坏原件，立即生成新备份并做完整校验。"
            elif "恢复演练" in detail:
                title, suggestion = "公司备份最近没有通过恢复演练", "保留原备份和失败记录，修正原因后重新演练。"
            elif "失败" in detail:
                title, suggestion = "公司最近一次备份没有成功", "按上面的具体原因修正后，重新备份并核验。"
            elif "没有公司记忆备份" in detail:
                title, suggestion = "公司还没有任何可恢复的备份", "立即生成第一份备份并做完整校验。"
            else:
                title, suggestion = "公司的自动备份检查已经超期", "立即检查公司是否发生变化；有变化就备份，无变化只更新检查时间。"
        else:
            title, suggestion = system_copy.get(
                area,
                (f"{area}出现需要处理的问题", "让项目经理核对原始记录，查明原因后再处理。"),
            )
        alerts.append(_告警(
            "错误" if issue.get("级别") == "错误" else "警告",
            "系统",
            title,
            detail,
            suggestion,
            target=area,
            task_status=str(issue.get("级别") or "异常"),
            owner_label="公司系统",
            actions=_系统动作(area),
        ))
    for name, result in sorted((班子 or {}).items()):
        if not isinstance(result, dict) or result.get("ok") is not False:
            continue
        status = str(result.get("状态") or "调用异常")
        reason = str(result.get("原因") or "没有留下具体原因。")
        alerts.append(_告警(
            "错误",
            "班子",
            f"{name}当前不能正常工作",
            f"{status}：{reason}",
            "核对这个岗位的模型名称、账号权益、余额和网络，再做一次真实调用。",
            target=name,
            owner=name,
            task_status=status,
            owner_label=name,
            actions=_班子动作(name),
        ))
    return alerts


def 系统健康(*, 活动起点: dt.datetime | None = None) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    now = dt.datetime.now()

    import 部门
    import 房间注册
    for message in 部门.校验() + 房间注册.校验():
        issues.append(_问题("错误", "组织契约", message))

    from 状态存储 import 故障清单
    state_faults = 故障清单()
    for fault in state_faults:
        issues.append(_问题("错误", "状态文件", f"{Path(str(fault.get('文件') or '')).name}: {fault.get('错误')}"))

    import 事件总线
    event_health = 事件总线.健康状态()
    if not event_health.get("健康"):
        issues.append(_问题("错误", "事件总线", f"有 {len(event_health.get('损坏或写入失败') or [])} 个持久化故障"))

    import 任务台
    running = 任务台.运行中()
    interrupted = 任务台.中断项()
    已完成 = 任务台.列表(状态={"已完成"}, 限制=10000)
    延期任务 = _读取延期任务()
    recent = 0
    recent_items: list[dict[str, Any]] = []
    alerts: list[dict[str, str]] = []
    for item in interrupted:
        # 健康提醒的处理结果由原提问卡承接；再生成一张健康提醒会形成自我告警循环。
        if (
            str(item.get("来源") or "") == "健康告警处理"
            or str(item.get("类型") or "") == "健康告警处理"
            or str(item.get("内容") or "").lstrip().startswith("【船主处理健康告警】")
        ):
            continue
        if _后续已成功(item, 已完成):
            continue
        changed = _时间(str(item.get("更新时间") or ""))
        if changed and (now - changed).days <= 7:
            recent += 1
            记录 = {
                "id": str(item.get("id") or ""),
                "状态": str(item.get("状态") or ""),
                "类型": str(item.get("类型") or ""),
                "负责人": str(item.get("负责人") or ""),
                "房间": str(item.get("房间") or ""),
                "更新时间": str(item.get("更新时间") or ""),
                "错误": str(item.get("错误") or ""),
                "内容": str(item.get("内容") or "")[:160],
            }
            recent_items.append(记录)
            状态 = str(item.get("状态") or "")
            负责人 = str(item.get("负责人") or "")
            房间 = str(item.get("房间") or "")
            错误 = str(item.get("错误") or "") or "没有留下错误原因"
            任务摘要 = _短句(item.get("内容") or item.get("id") or "未命名任务")
            谁 = _负责人称谓(负责人)
            if 状态 == "被门禁拦住":
                if "没有真写" in 错误 or "没有真实" in 错误:
                    标题 = f"{谁}执行「{任务摘要}」时被交付门拦下"
                    建议 = "这不是缺权限，而是没有真实产出。请让负责人补交产物，或说明为什么这项任务本来就不该产出文件。"
                elif any(word in 错误 for word in ("权限", "白名单", "不允许", "拒绝")):
                    标题 = f"{谁}执行「{任务摘要}」时因权限边界被拦下"
                    建议 = "需要你决定是否只为这项任务扩大权限；不同意，就让负责人按原边界重做。"
                else:
                    标题 = f"{谁}执行「{任务摘要}」时被门禁拦下"
                    建议 = "先按门禁理由补齐条件；如果确实需要越过原边界，再由你明确授权。"
            elif 状态 == "被重启中断":
                标题 = f"{谁}处理「{任务摘要}」时被办公室重启打断"
                建议 = "这项工作没有正常收尾。仍然需要它，就同意恢复；不急可以稍后。"
            elif 状态 == "失败":
                if "不在公司仓或产品仓内" in 错误:
                    标题 = f"{谁}提交「{任务摘要}」时被验收登记拒绝"
                    建议 = "交付文件写到了公司仓外。请改存到公司仓或产品仓；只有你明确要求交到外部目录时才扩大边界。"
                elif any(word in 错误 for word in ("AccessDenied", "403", "使用资格", "未购买", "Unpurchased")):
                    标题 = f"{谁}处理「{任务摘要}」时没有模型使用资格"
                    建议 = "需要核对模型名和账号权益；同意后，应补这一个模型的使用资格，或换成已经购买的模型。"
                elif "权限" in 错误:
                    标题 = f"{谁}执行「{任务摘要}」时因缺少权限失败"
                    建议 = "需要你决定是否只为这项任务补权限；同意不代表开放其他目录或其他任务。"
                else:
                    详情, 标题, 建议 = _失败事实(谁, 任务摘要, 错误, 房间)
                    alerts.append(_告警(
                        "错误",
                        "任务",
                        标题,
                        详情,
                        建议,
                        target=str(item.get("内容") or ""),
                        task_id=str(item.get("id") or ""),
                        owner=负责人,
                        room=房间,
                        updated=str(item.get("更新时间") or ""),
                        task_status=状态,
                        owner_label=谁,
                        actions=_任务动作(状态, 错误),
                        raw_detail=错误,
                        failure_type=_故障类型(错误),
                    ))
                    continue
            else:
                标题 = f"{谁}处理「{任务摘要}」时状态异常"
                建议 = "请先看下面的真实原因，再决定同意处理还是补充要求。"
            alerts.append(_告警(
                "警告",
                "任务",
                标题,
                f"原因：{错误}" + (f"；发生在：{房间}" if 房间 else ""),
                建议,
                target=str(item.get("内容") or ""),
                task_id=str(item.get("id") or ""),
                owner=负责人,
                room=房间,
                updated=str(item.get("更新时间") or ""),
                task_status=状态,
                owner_label=谁,
                actions=_任务动作(状态, 错误),
            ))
    alerts = [item for item in alerts if not _告警已延期(item, 延期任务)]
    alerts, 历史告警 = _区分当前与历史告警(alerts, 活动起点)
    # 近七天失败是任务台的历史统计，不是当前系统故障。
    # 具体任务若仍需处理，由 alerts 单独说明人、事、原因和动作；不能再拿历史总数弹一张空泛决策卡。

    import 管理动作
    管理动作.初始化()
    executing = len(list(管理动作.执行中.glob("*.json")))
    if executing:
        issues.append(_问题("警告", "管理动作", f"有 {executing} 张执行中/待恢复卡"))

    import 待验收记录
    side_effects = sum(
        1 for item in 待验收记录.列表(仅待验收=False)
        if item.get("状态") in ("已批", "已打回") and 待验收记录.副作用待处理(str(item.get("id") or ""))
    )
    if side_effects:
        issues.append(_问题("警告", "验收台", f"有 {side_effects} 条裁决副作用待重试"))

    capacity = {
        "会话事件字节": _大小(COMPANY / "运行状态" / "会话事件"),
        "统一搜索索引字节": _大小(COMPANY / "运行状态" / "统一搜索.db"),
        "任务记录字节": _大小(COMPANY / "运行状态" / "任务"),
        "工具间记录字节": _大小(COMPANY / "工具间" / "记录"),
    }
    thresholds = {
        "会话事件字节": 1024 * 1024 * 1024,
        "统一搜索索引字节": 512 * 1024 * 1024,
        "任务记录字节": 256 * 1024 * 1024,
        "工具间记录字节": 256 * 1024 * 1024,
    }
    for key, limit in thresholds.items():
        if capacity[key] > limit:
            issues.append(_问题("警告", "容量", f"{key.removesuffix('字节')} 已达 {capacity[key] / 1024 / 1024:.1f}MB"))

    verify_file = COMPANY / "运行状态" / "模型核验.json"
    verification: dict[str, Any] = {"存在": verify_file.exists(), "最旧天数": None}
    if verify_file.exists():
        try:
            from 状态存储 import 读JSON
            data = 读JSON(verify_file, 默认={}, 类型=dict) or {}
            ages = []
            for item in data.values():
                checked = _时间(str((item or {}).get("checked_at") or ""))
                if checked:
                    ages.append((now - checked).total_seconds() / 86400)
            if ages:
                verification["最旧天数"] = round(max(ages), 1)
                if max(ages) > 7:
                    issues.append(_问题("警告", "模型身份", f"最旧一次真实模型核验距今 {max(ages):.1f} 天"))
            else:
                issues.append(_问题("警告", "模型身份", "核验文件没有可用时间"))
        except Exception as e:  # noqa: BLE001
            issues.append(_问题("错误", "模型身份", f"核验文件读不了：{type(e).__name__}: {e}"))
    else:
        issues.append(_问题("警告", "模型身份", "尚无真实模型核验记录"))

    search_status_file = COMPANY / "运行状态" / "统一搜索接口.json"
    search_status: dict[str, Any] = {"status": "missing"}
    if search_status_file.exists():
        try:
            from 状态存储 import 读JSON
            search_status = 读JSON(search_status_file, 默认={}, 类型=dict) or {}
            checked = _时间(str(search_status.get("checked_at") or ""))
            if search_status.get("status") != "ok":
                issues.append(_问题("错误", "统一搜索", f"真实接口自检失败：{search_status.get('error', '未知错误')}"))
            elif not checked or (now - checked).total_seconds() > 7 * 86400:
                issues.append(_问题("警告", "统一搜索", "真实接口自检已超过 7 天"))
        except Exception as e:  # noqa: BLE001
            issues.append(_问题("错误", "统一搜索", f"接口自检状态读不了：{type(e).__name__}: {e}"))
    elif os.environ.get("XJ_MOCK") != "1":
        issues.append(_问题("警告", "统一搜索", "尚无真实 embedding/rerank 接口自检记录"))

    try:
        from 本地精排客户端 import 状态 as _精排服务状态

        rerank_service = _精排服务状态()
        if not rerank_service.get("模型文件完整"):
            issues.append(_问题("错误", "统一搜索", str(rerank_service.get("模型文件说明") or "本地精排模型文件不完整")))
        service_payload = rerank_service.get("服务") if isinstance(rerank_service.get("服务"), dict) else {}
        if rerank_service.get("服务状态") == "运行中" and service_payload.get("ok") is False:
            issues.append(_问题("警告", "统一搜索", "本地精排独立服务当前异常，搜索会使用基础排序"))
        memory = ((service_payload.get("state") or {}).get("内存") or {}) if isinstance(service_payload, dict) else {}
        if int(memory.get("活动字节") or 0) > 8 * 1024**3:
            issues.append(_问题("错误", "统一搜索", "本地精排独立服务内存超过 8GB，已进入资源异常范围"))
    except Exception as e:  # noqa: BLE001
        rerank_service = {"服务状态": "状态不可读", "错误": f"{type(e).__name__}: {e}"}
        issues.append(_问题("警告", "统一搜索", "本地精排独立服务状态暂时读不到，搜索会使用基础排序"))

    import 备份 as 备份系统
    import 备份任务

    backup_root = 备份系统.默认目录
    backups = sorted(backup_root.glob("公司记忆_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True) if backup_root.exists() else []
    backup: dict[str, Any] = {"最新": "", "天数": None, "可打开": False}
    try:
        policy = 备份系统.读取策略()
        backup_state = 备份系统.读取状态()
    except Exception as e:  # noqa: BLE001
        policy = dict(备份系统.默认策略)
        backup_state = {}
        issues.append(_问题("错误", "备份", f"备份状态无法读取：{type(e).__name__}: {e}"))
    schedule = 备份任务.检查()
    backup.update({
        "状态": str(backup_state.get("状态") or "尚无运行记录"),
        "最近检查": str(backup_state.get("最近检查") or ""),
        "最近成功": str(backup_state.get("最近成功") or ""),
        "最近恢复演练": str(backup_state.get("最近恢复演练") or ""),
        "恢复演练状态": str(backup_state.get("恢复演练状态") or "尚未执行"),
        "自动任务": schedule,
        "异盘状态": str(backup_state.get("异盘状态") or "未配置"),
    })
    if schedule.get("适用", True) and (not schedule.get("配置正确") or not schedule.get("已加载")):
        reason = "；".join(str(x) for x in (schedule.get("问题") or [])) or "macOS 没有加载当前公司的备份任务"
        issues.append(_问题("错误", "备份", f"自动备份定时任务未正常接入：{reason}"))
    if not backups:
        issues.append(_问题("错误", "备份", "没有公司记忆备份"))
    else:
        latest = backups[0]
        age = (now.timestamp() - latest.stat().st_mtime) / 86400
        backup.update({"最新": latest.name, "天数": round(age, 1)})
        try:
            with zipfile.ZipFile(latest) as zf:
                # 健康接口会被前端频繁轮询，只检查容器和清单是否可读。
                # 完整 CRC、逐文件哈希及真实恢复由备份程序按策略执行。
                zf.getinfo("公司快照/备份清单.json")
                backup["可打开"] = True
        except Exception:  # noqa: BLE001
            backup["可打开"] = False
        if not backup["可打开"]:
            issues.append(_问题("错误", "备份", "最新备份 ZIP 完整性失败"))
        error = str(backup_state.get("错误") or "")
        if error:
            issues.append(_问题("错误", "备份", f"最近一次备份维护失败：{error}"))
        checked = _时间(str(backup_state.get("最近检查") or ""))
        reference = checked or dt.datetime.fromtimestamp(latest.stat().st_mtime)
        unchecked_days = (now - reference).total_seconds() / 86400
        if unchecked_days > int(policy["严重告警天数"]):
            issues.append(_问题("错误", "备份", f"自动备份检查已超期 {unchecked_days:.1f} 天"))
        elif unchecked_days > int(policy["提醒天数"]):
            issues.append(_问题("警告", "备份", f"自动备份检查已超期 {unchecked_days:.1f} 天"))

        drill_error = str(backup_state.get("恢复演练错误") or "")
        if drill_error:
            issues.append(_问题("错误", "备份", f"最近一次恢复演练失败：{drill_error}"))
        drilled = _时间(str(backup_state.get("最近恢复演练") or ""))
        if drilled and (now - drilled).total_seconds() > int(policy["恢复演练间隔天数"]) * 86400:
            issues.append(_问题("警告", "备份", f"恢复演练已超过 {policy['恢复演练间隔天数']} 天未执行"))

    errors = sum(1 for item in issues if item["级别"] == "错误")
    warnings = sum(1 for item in issues if item["级别"] == "警告")
    return {
        "ok": errors == 0,
        "错误数": errors,
        "警告数": warnings,
        "问题": issues,
        "告警": alerts,
        "历史告警": 历史告警,
        "任务": {
            "运行中": len(running),
            "失败或中断": len(interrupted),
            "近7天失败或中断": recent,
            "当前待处理告警": sum(1 for item in alerts if item.get("类别") == "任务"),
            "近7天明细": recent_items,
        },
        "事件": event_health,
        "容量": capacity,
        "备份": backup,
        "模型核验": verification,
        "统一搜索": search_status,
        "本地精排": rerank_service,
        "时间": now.isoformat(timespec="seconds"),
    }
