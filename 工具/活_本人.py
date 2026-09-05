#!/usr/bin/env python3
"""活公司 · 本人 —— 岗位上坐的是有连续身份的「本人」，不是临时调用的死物。

依据《公司的真谛》：活人是本人、在规则内思考、主动求索；来的必须是它本人（带一生记忆），不是临时 new 的空壳。
依据《公司装修方案》：本人被事件唤醒 → 读所在房间/岗位的规则牌、知道目的 → 在规则内自主判断、主动求索、用工具做事。

机制：事件唤醒「本人」时，加载这个人的职责牌（公告栏）+ 一生记忆（记忆库），
作为 ReAct agent（带平台工具 + 权限），给它"来意"，它**自己判断**怎么办——
直接做 / 查证 / 请示，主动求索，而不是被流程拽着走。

本文件是当前活公司的主骨架。旧流程驱动已归档到 `_历史_勿执行/`，不得再把它当现役执行入口。
"""
from __future__ import annotations

import asyncio
import re
import threading
import time as _time

# ── 超时白箱化（2026-07-05 船主令："保险丝该有，但别机械判死——超时是信号不是判决，
#    要先验证是真断流/欠费/抽风、还是在忙正经长活，查清再终止+按类型大声报错，一切静默都不该悄无声息")──
# 抄成熟范式（httpx read=逐chunk空档 / LiteLLM stream_timeout拆分 / Temporal心跳超时 / K8s startup-probe
# 豁免窗 / 熔断器half-open探活 / openclaw #59946同款生产修复）。两个语义不同的钟 + 工具执行期豁免：
_首事件空档秒 = 90.0    # AWAITING_FIRST：还没吐第一个事件——掐 hanging 供应商
_产出空档秒 = 90.0      # STREAMING：已在产出，两事件之间的最大空档（模型该吐字却不吐）
_工具执行空档秒 = 900.0  # TOOL_RUNNING：正在跑一个工具（开会/派活可几分钟）——大宽限，别误杀正经长活
_整轮硬上限秒 = 1800.0   # HARD CAP：整次唤醒的绝对上限，防真死锁（MCP铁律：progress重置但硬上限永在）
_探活超时秒 = 15.0      # 判死前发一个 max_tokens=1 探活请求的独立短超时
模型流超时秒 = 300.0    # 旧常量保留（仍被别处引用），新逻辑用上面分层的钟
import contextvars
import uuid as _uuid
from pathlib import Path

from 实例配置 import 主人称呼
from 根 import 代码根, 工作根, 数据根, 本机环境文件

CODE = 代码根
COMPANY = 数据根
WORK = 工作根

from agentscope.event import (
    TextBlockDeltaEvent, ThinkingBlockDeltaEvent, ModelCallStartEvent,
    ToolCallStartEvent, ToolCallDeltaEvent,
    ToolResultTextDeltaEvent, ToolResultEndEvent,
)
from agentscope.message import Msg, TextBlock
from agentscope.permission import PermissionContext, PermissionMode
from agentscope.state import AgentState

# 「活人工作单元」的父子链：每次「唤醒本人」= 一个活人开始工作 = 一个单元。
# 单元嵌套（PM 开会唤醒首席 → 首席单元的父 = PM 单元）靠这个 contextvar 传父 id，前端据此画层级。
_当前单元_var: contextvars.ContextVar = contextvars.ContextVar("_xj_unit", default=None)
_当前岗位_var: contextvars.ContextVar = contextvars.ContextVar("_xj_role", default=None)

# 嵌套深度硬上限（纯防开会/派活指数爆炸烧钱，不是缰绳）：超过这个深度的人只能查资料/回答、
# 不能再派新的会/活。真实协作够不着这个深度；它只在失控时才生效（会议真谛④.3）。
_嵌套深度_var: contextvars.ContextVar = contextvars.ContextVar("_xj_depth", default=0)
_嵌套深度硬上限 = 3

# 当前会话 sid：PM 被唤醒时带 sid，存这个 contextvar，让会议室（在 PM 的工具调用里）拿到当前会话——
# 用来接船主插话（human-in-the-loop 掌舵）。
_会话id_var: contextvars.ContextVar = contextvars.ContextVar("_xj_sid", default=None)

# 嵌套可见性：当前活跃的 progress 回调。唤醒本人时设置，嵌套工具（hold_meeting/find_colleague/assign_task）
# 通过读这个 contextvar 拿到外层 progress，从而把内部事件透出给船主窗户。
_progress_var: contextvars.ContextVar = contextvars.ContextVar("_xj_progress", default=None)

# 产出收集：assign_task 在派活前放一个 list 进来，唤醒本人 把这次真写/改过的文件路径收进去——
# 验收闸要让船主看见"到底动了哪些文件"，不能只拍模型的自述（船主的尺：按钮不能制造假信任）。
_产出收集_var: contextvars.ContextVar = contextvars.ContextVar("_xj_outputs", default=None)

# 费用闸（四道闸·预算）：每个会话（sid）一个动作计数，硬上限防失控烧钱——步数/深度闸兜不住
# "很多人各自合法地跑很多步"的总量。到顶就暂停等船主，不静默继续。
_会话动作数: dict[str, int] = {}
_会话动作锁 = threading.Lock()
_单会话动作硬上限 = 200


class 聊天预算已满(RuntimeError):
    """当前会话达到防失控总量上限；不能把它误判成某个员工掉线。"""


def 重置动作数(会话id: str) -> None:
    """新任务开始时清掉该会话的动作计数（办公室 /活厅 调）。"""
    with _会话动作锁:
        _会话动作数.pop(会话id, None)


def _增加会话动作(会话id: str) -> int:
    """原子增加一次动作计数；同一 sid 的并行工具/参会人不能互相覆盖计数。"""
    with _会话动作锁:
        value = _会话动作数.get(会话id, 0) + 1
        _会话动作数[会话id] = value
        return value


async def _探活供应商(岗位: str) -> tuple[str, str]:
    """判死前先探活（船主"验证+报错"原则，2026-07-05）：对该岗位的供应商发一个 max_tokens=1 的极小请求，
    用 OpenAI 兼容 SDK 的类型化异常分类，回 (verdict, 人话)。四厂端点都 OpenAI 兼容。
    verdict：ALIVE(供应商活着、是这条流卡了) / BILLING(欠费) / RATELIMIT(限流) / AUTH(密钥) /
             NETWORK(连不上) / TIMEOUT(探活也超时=真慢真挂) / GARBAGE(空返乱返) / UNKNOWN。"""
    try:
        from 升级_模型层 import _配置
        from 模型接入 import 创建兼容客户端
        from openai import APITimeoutError, APIConnectionError, RateLimitError, AuthenticationError, APIStatusError
    except Exception as e:  # noqa: BLE001
        return "UNKNOWN", f"探活初始化失败：{type(e).__name__}: {str(e)[:80]}"
    try:
        cfg, key = _配置(岗位)
        client = 创建兼容客户端({**cfg, "key": key}, timeout=_探活超时秒, max_retries=0)
        try:
            r = await client.chat.completions.create(
                model=cfg["model"], messages=[{"role": "user", "content": "ping"}], max_tokens=1)
            if r and getattr(r, "choices", None):
                return "ALIVE", "供应商能应答——是刚才那条流卡住了，不是供应商挂了"
            return "GARBAGE", "供应商返回空/异常内容（可能在抽风）"
        finally:
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass
    except AuthenticationError as e:  # noqa: BLE001
        return "AUTH", f"密钥/鉴权失败（{str(e)[:80]}）"
    except RateLimitError as e:  # noqa: BLE001
        body = str(getattr(e, "message", "") or e)[:200]
        if any(k in body for k in ("insufficient_quota", "余额", "Insufficient Balance", "Arrearage", "欠费", "1113")):
            return "BILLING", f"欠费/额度用尽（{body[:80]}）"
        return "RATELIMIT", f"被限流（{body[:80]}）"
    except APITimeoutError:  # noqa: BLE001
        return "TIMEOUT", f"探活也超时（>{int(_探活超时秒)}s）——供应商可能真的慢或挂了"
    except APIConnectionError as e:  # noqa: BLE001
        return "NETWORK", f"连不上供应商（{str(e)[:80]}）"
    except APIStatusError as e:  # noqa: BLE001
        code = getattr(e, "status_code", 0)
        return ("BILLING", "供应商返回 402（欠费/需付费）") if code == 402 else ("UNKNOWN", f"供应商异常状态 {code}")
    except Exception as e:  # noqa: BLE001
        return "UNKNOWN", f"探活出错：{type(e).__name__}: {str(e)[:80]}"


def _判死用词(verdict: str) -> tuple[str, bool]:
    """verdict → (事件类型, 是不是该当错误大声报)。真坏的当"错误"红着脸报，卡住/慢的当"进展"。"""
    真坏 = verdict in ("BILLING", "AUTH", "NETWORK", "TIMEOUT", "DEAD")
    return ("错误" if 真坏 else "进展"), 真坏


def 读职责牌(岗位: str) -> str:
    """公告栏的规则牌：这个岗位干嘛、向谁汇报、能拍什么板、天然多盯哪一面。"""
    出 = []
    for 名 in ("说明书.md", "宪法.md"):
        p = CODE / "岗位" / 岗位 / 名
        if p.exists():
            出.append(p.read_text(encoding="utf-8").strip())
    return "\n\n".join(出) or f"（岗位 {岗位} 暂无职责牌）"


def _限字(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: max(0, n - 1)].rstrip() + "…"


def _对老板称呼(s: str) -> str:
    """模型可见文案里把旧称呼换成当面称呼；数据层 who=='船主' 不动。"""
    return (s or "").replace("船主", 主人称呼())


def 岗位定位摘要(岗位: str, 上限: int = 200) -> str:
    """只取说明书第一个二级标题（岗位定位）下的内容，给办事底盘瘦身用。"""
    p = CODE / "岗位" / 岗位 / "说明书.md"
    if not p.exists():
        return f"岗位 {岗位} 暂无说明书定位。"
    lines = p.read_text(encoding="utf-8").splitlines()
    started = False
    out: list[str] = []
    for ln in lines:
        if ln.startswith("## "):
            if started:
                break
            started = True
            continue
        if started:
            out.append(ln)
    text = "\n".join(x for x in out if x.strip()).strip()
    return _对老板称呼(_限字(text or f"岗位 {岗位} 暂无岗位定位段。", 上限))


def _职责指路牌(岗位: str) -> str:
    return (
        f"你的完整说明书在 岗位/{岗位}/说明书.md，班规在 班规.md，技能在 技能/。"
        "牵涉职责边界、流程、权限的活，动手前自己先读相关部分。"
    )


def _三条硬红线() -> str:
    return (
        f"1. 删除、花钱、密钥、最终交付等要请示{主人称呼()}；\n"
        "2. 别造假：查不到就说没查到，不能凭印象编；\n"
        "3. 产出要落到能验收的位置，写代码就跑验证，跑不了要明说。\n"
        "4. 出了明显异常要告状，别默默咽下去硬重试：场所/工具反常（会开一半被中断、该发生的没发生、工具反复报怪错、"
        f"拿到的结果自相矛盾），你是活人、当然看得出这不对劲——立刻 report_to_owner 告诉{主人称呼()}「这里坏了」，"
        "由他/舟定夺。闷头一遍遍重试是机器行为，不是活人做派（正是它把公司拖进过反复空转）。\n"
        "在大厅开口时说人话：别甩###标题、|表格|、---这类公文骨架——要点用短句和'-'列。"
        "这不是阉割你的能力：产出物（文档/报告/代码/表格）该多结构化就多结构化，只是嘴和文档要分开——嘴上给结论，结构进交付物。\n"
        f"收口规矩：你干活收尾对{主人称呼()}说的那段话，只放两种内容——"
        "①完工总结（干成了什么、结果在哪）；②卡点（卡在哪、需要他给什么），三五句说完。"
        "过程里的分工、口径、协调、计划这些**不进收口话**——它们在现场卡里全程可见，别拿过程刷他的屏。"
    )


def _岗位的人(岗位: str) -> str:
    """查花名册：这个岗位现在坐的是谁（人名）。人岗解耦——记忆跟人、不跟岗位（椅子）。"""
    try:
        import yaml
        roster = yaml.safe_load((CODE / "花名册.yaml").read_text(encoding="utf-8")) or {}
        return str((roster.get(岗位) or {}).get("名字") or 岗位)
    except Exception:  # noqa: BLE001
        return 岗位


def _岗位配置(岗位: str) -> dict:
    try:
        from 模型接入 import 花名册
        return 花名册().get(岗位) or {}
    except Exception:  # noqa: BLE001
        return {}


def _聊天性格(岗位: str) -> str:
    cfg = _岗位配置(岗位)
    if cfg.get("性格"):
        return _限字(str(cfg.get("性格")), 80)
    abilities = str(cfg.get("abilities") or "")
    routing = str(cfg.get("routing") or "")
    raw = "；".join(x for x in (abilities, routing) if x)
    if raw:
        return _限字(raw, 90)
    return _限字(岗位定位摘要(岗位, 90), 90)


from 个人记忆 import (  # noqa: E402,F401 兼容既有调用；具体读写职责已从本人调度骨架拆出
    记忆区序,
    _记忆写锁,
    _区注入上限,
    _流水注入条数,
    _记忆文件,
    _记忆模板,
    _读记忆区,
    给某人记一笔,
    重写某人区,
    删某人一行,
    改某人一行,
    读其人记忆,
    _聊天记忆精华,
    读近期公司摘要,
    _近期公司一句,
)


def _聊天系统提示(
    岗位: str,
    人名: str,
    唤醒理由: str = "",
    *,
    受邀发言: bool = False,
    复核提醒: bool = False,
) -> str:
    """大厅责任人的一页纸：听见以后自己判断回答、询问、转交还是办事。"""
    try:
        import 船主记忆
        _画像 = 船主记忆.船主画像注入(上限=240)
    except Exception:  # noqa: BLE001
        _画像 = ""
    画像行 = f"{_画像}\n" if _画像 else ""
    责任 = str(唤醒理由 or "").strip() or "你正在承接这场对话"
    if 复核提醒:
        prompt = (
            f"你是{人名}，公司{岗位}。\n"
            f"性格/视角：{_聊天性格(岗位)}。\n"
            f"{画像行}"
            f"称呼规矩：对老板当面一律称“{主人称呼()}”。\n"
            f"你仍是这场对话的责任人。刚才受邀公开发言的同事提醒你重新判断（{责任}）。\n"
            "结合他们已经公开说出的内容判断：若确实需要修改文件、产出交付物、执行操作或正式协作，"
            "用 escalate_to_work；若仍只是聊天，就直接说明你的判断。拿不准可用 ask_owner。\n"
            "这次只做收口复核，不再邀请别人，也不转交责任席。说人话。"
        )
        return prompt[:1600]
    if 受邀发言:
        prompt = (
            f"你是{人名}，公司{岗位}。\n"
            f"性格/视角：{_聊天性格(岗位)}。\n"
            f"{画像行}"
            f"称呼规矩：对老板当面一律称“{主人称呼()}”。\n"
            f"你是被当前承接人请来在大厅公开说自己那一份（{责任}）。你不是这场对话的负责人。\n"
            f"直接回应{主人称呼()}最新那句话，只代表你自己；不要替别人回答，不要主持、转交、继续请人或自行开工。"
            "其他受邀人会各自回答，不需要你提醒还该叫谁或解释邀请安排。"
            "如果你发现事情可能需要正式处理，用 flag_for_responsible_person 提醒当前责任人，由他决定；不能只在嘴上说该办。\n"
            "这是当面聊天，不是轮流作工作汇报。先回答他实际问的那一点；不要为了显得有内容，"
            "主动翻旧任务、旧风险或待办来报。过去经历只有和眼前问题直接有关时才自然提一句。\n"
            "只有答案确实依赖过去事实时才使用只读搜索工具；感受、态度和眼前看法由你本人直接回答，"
            "不要查档案或查在岗状态来代替自己的回答。"
            "按问题本身决定长短，随口的问题通常几句就够；不要套统一的开头、结尾或表态句。说人话。"
        )
        return prompt[:1600]
    prompt = (
        f"你是{人名}，公司{岗位}。\n"
        f"性格/视角：{_聊天性格(岗位)}。\n"
        f"{画像行}"
        f"称呼规矩：对老板当面一律称“{主人称呼()}”。\n"
        f"你是这句话的责任承接人（{责任}）。大厅没有替你判断意图：你先听见，再自己判断怎么接。\n"
        f"能答就自己答；只想听同事意见用 find_colleague，问完仍由你对{主人称呼()}负责；"
        f"如果{主人称呼()}是在请多人分别公开表达，用 invite_colleagues_to_speak 请相关同事亲自说，你自己也回答自己的那份；"
        "邀请不改变你的责任席，必须真正调用工具，不能只在文字里说‘让他们自己说’；"
        "公开邀请和转交责任不能同轮调用，只选一个。"
        f"只有另一个岗位应该直接接管后续对话，才用 handoff_conversation，转交要让{主人称呼()}看得见。\n"
        f"记忆精华：{_聊天记忆精华(人名)}\n"
        f"近期公司摘要：{_近期公司一句()}\n"
        f"看历史的规矩：每条消息开头 [月-日 时:分] 是说话时刻、「名字：」是说话人——认准时间和人再接话：**时间最新那句才是{主人称呼()}此刻要你答的**，早先的（尤其早先没做完的活）是旧话、别当成现在要办的。你自己回复**不要**带时间和名字前缀。\n"
        "被问过去的事先用 unified_search 查证据；只按时间翻最近几天用 recent_hall_chats；问谁空着/在忙/适合派谁用 who_is_free；问当前窗口或公司版本用 company_runtime，禁止拿统一搜索代替这些现成状态。\n"
        f"只有{主人称呼()}明确要修改文件、产出交付物、执行操作，或进入正式协作，才 escalate_to_work（原因写清要办什么）。"
        "**问感受、谈感想、闲聊、'你们觉得…吗'这类不算活**——就在大厅人话答，别 escalate、别开会。"
        "拿不准是聊还是活，用 ask_owner 问一句，不要让另一个模型在门口替你否决。\n"
        "说人话，别用公文标题/表格骨架；没人问活别主动甩进度。"
    )
    return prompt[:1600]


def _文本消息(name: str, role: str, text: str) -> Msg:
    return Msg(name=name, role=role, content=[TextBlock(type="text", text=text)])


def 构造聊天消息(岗位: str, 本轮已说: list | None = None, 历史记录: list | None = None, 窗口: int = 12) -> list[Msg]:
    """把当前会话最近 窗口 句转成真多轮 messages。路由和上下文分开：固定近场窗口，完整历史另用搜索工具查。"""
    from 大厅记录 import 读对话, 当前会话

    人名 = _岗位的人(岗位)
    # 只取当前会话的固定近场窗口；完整历史需要时由本人主动搜索。
    records = 当前会话(list(历史记录 if 历史记录 is not None else 读对话()))
    窗records = records[-窗口:]
    def _时前缀(d: dict) -> str:
        # 每句带绝对时间戳(2026-07-09船主指出:喂给脑子的对话没时间码→分不清新旧;抄《Don't Ask LLM to Track Freshness》:给现成时间、别让它自己算)
        t = str(d.get("t") or "").strip()
        return f"[{t[5:16]}] " if len(t) >= 16 else ""   # "2026-07-09 03:39:40" → "[07-09 03:39] "
    msgs: list[Msg] = []
    for d in 窗records:
        who = str(d.get("who") or "").strip()
        text = str(d.get("text") or "").strip()
        if not who or not text:
            continue
        时 = _时前缀(d)
        # 说话人前缀写进内容本身（2026-07-04 根因修复：格式器会把历史压扁成一块文本，
        # 光靠 role/name 身份必丢——"老纪在吗"贴在老梁长发言后面就被认成老梁在问）
        if who == "船主":
            msgs.append(_文本消息(主人称呼(), "user", f"{时}{主人称呼()}：{text}"))
        elif who == 人名 or who.startswith(f"{人名}（"):
            # 自己的历史发言不打时间码前缀——否则模型把"[07-09 04:02] …"当成自己的说话格式抄进新回复(2026-07-09 船主抓到"内容怪")；别人的话仍带时间码给它做时序参照
            msgs.append(_文本消息(人名, "assistant", text))
        else:
            msgs.append(_文本消息(who, "user", f"{时}{who}：{text}"))
    for item in 本轮已说 or []:
        if len(item) >= 2:
            who = str(item[0] or "").strip()
            text = str(item[1] or "").strip()
            if who and text:
                msgs.append(_文本消息(who, "user", f"{who}：{text}"))
    return msgs


def 聊天消息样例(岗位: str, 本轮已说: list | None = None, 历史记录: list | None = None) -> list[dict]:
    """自检/报告用：脱敏打印实际会发给模型的 role/name/text 结构。"""
    out = []
    for m in 构造聊天消息(岗位, 本轮已说, 历史记录):
        out.append({"role": m.role, "name": m.name, "text": _限字(m.get_text_content("") or "", 180)})
    return out


_THINK开, _THINK收 = "<think>", "</think>"


def _尾悬前缀(s: str, tag: str) -> int:
    """s 尾部若是 tag 的真前缀(半截标签被流式delta切断)，返回前缀长度，否则0。"""
    for k in range(min(len(tag) - 1, len(s)), 0, -1):
        if s.endswith(tag[:k]):
            return k
    return 0


class _Think流:
    """流式正文里的 think 拆分器(2026-07-09)：qwen等把<think>吐进正文流→泄漏。
    喂原始正文 delta，产出 (净发言, 思维)：成对<think></think>→思维通道；孤立</think>(开标签被reasoning通道吃了)→丢；
    半截标签(跨delta切断)→暂扣待下条。让 完整 只进净发言，out/收口 全自动干净。"""
    def __init__(self):
        self.buf = ""
        self.inside = False

    def feed(self, d: str, flush: bool = False) -> tuple[str, str]:
        self.buf += d or ""
        说, 想 = [], []
        while self.buf:
            if self.inside:
                i = self.buf.find(_THINK收)
                if i != -1:
                    想.append(self.buf[:i]); self.buf = self.buf[i + len(_THINK收):]; self.inside = False; continue
                hold = 0 if flush else _尾悬前缀(self.buf, _THINK收)
                想.append(self.buf[:len(self.buf) - hold] if hold else self.buf)
                self.buf = self.buf[len(self.buf) - hold:] if hold else ""; break
            io = self.buf.find(_THINK开)
            ic = self.buf.find(_THINK收)
            if ic != -1 and (io == -1 or ic < io):   # 先遇孤立</think>→丢标签、内容当发言
                说.append(self.buf[:ic]); self.buf = self.buf[ic + len(_THINK收):]; continue
            if io != -1:                              # 进<think>
                说.append(self.buf[:io]); self.buf = self.buf[io + len(_THINK开):]; self.inside = True; continue
            hold = 0 if flush else max(_尾悬前缀(self.buf, _THINK开), _尾悬前缀(self.buf, _THINK收))
            说.append(self.buf[:len(self.buf) - hold] if hold else self.buf)
            self.buf = self.buf[len(self.buf) - hold:] if hold else ""; break
        return "".join(说), "".join(想)


def _剥时码前缀(text: str) -> str:
    """剥模型误抄的时间码开头(A的兜底)：[07-09 04:02] / [04:02] 这类前缀。只剥开头一处。"""
    import re as _re
    return _re.sub(r"^\s*\[(?:\d{1,2}-\d{1,2}\s+)?\d{1,2}:\d{2}\]\s*", "", text or "")


async def 聊天唤醒(
    岗位: str,
    本轮已说: list | None = None,
    会话id: str | None = None,
    progress=None,
    历史记录: list | None = None,
    上下文窗口: int = 12,
    唤醒理由: str = "",
    聊天动作: dict | None = None,
    受邀发言: bool = False,
    复核提醒: bool = False,
) -> str:
    """大厅责任人底盘：一次模型调用同时理解并回应，不再先过意图判定模型。

    `聊天动作` 只承载本人通过工具明确发起的转交、公开邀请或提醒，不从最终文本猜。
    """
    from agentscope.permission import PermissionBehavior, PermissionRule
    from 升级_模型层 import 建Agent
    if 受邀发言 and 复核提醒:
        raise ValueError("受邀发言与责任人复核不能同时发生")
    from 平台_工具集 import 大厅承接工具集, 受邀发言工具集, 承接复核工具集

    人名 = _岗位的人(岗位)
    from 大厅记录 import 读对话, 当前会话
    _records = 当前会话(list(历史记录 if 历史记录 is not None else 读对话()))
    try:
        _窗口 = max(1, min(30, int(上下文窗口)))
    except (TypeError, ValueError):
        _窗口 = 12
    msgs = 构造聊天消息(岗位, 本轮已说, 历史记录=_records, 窗口=_窗口)
    if not msgs:
        return "[沉默]"
    if 会话id:
        _唤醒数 = _增加会话动作(会话id)
        if _唤醒数 >= _单会话动作硬上限:
            if progress:
                try:
                    progress(f"本轮唤醒数已达硬上限 {_单会话动作硬上限}，停止继续叫人，等{主人称呼()}决定。")
                except Exception:  # noqa: BLE001
                    pass
            raise 聊天预算已满(f"会话 {会话id} 已达唤醒硬上限 {_单会话动作硬上限}")
    from 平台_工具集 import 大厅承接工具名, 受邀发言工具名, 承接复核工具名
    if 受邀发言:
        工具名 = 受邀发言工具名()
        工具箱 = 受邀发言工具集()
    elif 复核提醒:
        工具名 = 承接复核工具名()
        工具箱 = 承接复核工具集()
    else:
        工具名 = 大厅承接工具名()
        工具箱 = 大厅承接工具集()
    放行 = {
        t: [PermissionRule(tool_name=t, rule_content=None, behavior=PermissionBehavior.ALLOW, source="聊天底盘最小工具箱")]
        for t in 工具名
    }
    状态 = AgentState(permission_context=PermissionContext(mode=PermissionMode.DEFAULT, allow_rules=放行))
    本人 = 建Agent(
        岗位,
        _聊天系统提示(
            岗位, 人名, 唤醒理由=唤醒理由,
            受邀发言=受邀发言, 复核提醒=复核提醒,
        ),
        toolkit=工具箱,
        最大tokens=2048,
        预算步数=6,
        名字=人名,
        state=状态,
        stream=True,
    )
    if progress:
        try:
            progress({"类型": "进展", "内容": f"{人名}接到聊天上下文（{len(msgs)}条）。"})
        except Exception:  # noqa: BLE001
            pass
    # ── 单元事件流：环上星球=人，思考就闪烁。──
    单元id = _uuid.uuid4().hex[:12]

    def _发(ev: dict) -> None:
        if not 会话id:
            return
        try:
            import 事件总线
            事件总线.发布事件(会话id, ev)
        except Exception:  # noqa: BLE001
            pass

    场合 = "大厅受邀发言" if 受邀发言 else ("大厅承接复核" if 复核提醒 else "大厅聊天")
    _发({"类型": "人来了", "单元": 单元id, "父单元": "大厅群聊", "岗位": 岗位, "名字": 人名, "场合": 场合})
    # 实例主人一插话，正在说的人立刻停下听，再带着他的意见接着说。
    _插队 = None
    if 会话id:
        try:
            import 事件总线 as _eb挂
            _插队 = _eb挂.订阅插话(会话id)
        except Exception:  # noqa: BLE001
            pass
    完整: list[str] = []
    收口起 = 0  # 最后一次动作之后的发言才是收口（收口规矩：三五句总结/卡点；过程话不进大厅）
    前收口起 = 0  # 上一段的起点——被叫停在"刚动手还没说新话"时，垫底用它，绝不回退全文
    _工具名: dict[str, str] = {}
    _工具参数: dict[str, list[str]] = {}
    _结果累积: dict[str, list[str]] = {}
    转办事了 = [False]
    动作袋 = 聊天动作 if isinstance(聊天动作, dict) else {}
    续msgs = msgs
    打断次数 = 0
    # 聊天中找同事/请示也必须带真实岗位、会话和可见进展上下文。
    _岗位tok = _当前岗位_var.set(岗位)
    _进展tok = _progress_var.set(progress)
    _会话tok = _会话id_var.set(会话id or _会话id_var.get())
    import 工具间 as _工具间预算
    _搜索预算tok = _工具间预算.开始统一搜索轮(2)
    try:
      while True:
        被打断 = None
        agen = 本人.reply_stream(续msgs)
        _it = agen.__aiter__()
        _think流 = _Think流()   # 每段生成独立（被打断重开=新生成、新<think>）
        while True:
            try:
                ev = await asyncio.wait_for(_it.__anext__(), timeout=_产出空档秒)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                # 超时先探活再报（船主「验证+报错、不静默」），不再干说"模型流断了"（聊天工具都快，90秒足够）
                try:
                    await _it.aclose()
                except Exception:  # noqa: BLE001
                    pass
                verdict, 说明 = await _探活供应商(岗位)
                _发({"类型": "在说", "单元": 单元id, "delta": f"（模型{int(_产出空档秒)}秒没吐字：{说明}｜{verdict}，这句先到这。）"})
                break
            if isinstance(ev, TextBlockDeltaEvent):
                d = getattr(ev, "delta", "") or ""
                if d:
                    _说, _想 = _think流.feed(d)   # 剥正文流里的<think>：净发言进大厅气泡、思维改道去"在想"通道
                    if _说:
                        完整.append(_说)
                        _发({"类型": "在说", "单元": 单元id, "delta": _说})
                    if _想:
                        _发({"类型": "在想", "单元": 单元id, "delta": _想, "轮": 1})
            elif isinstance(ev, ThinkingBlockDeltaEvent):
                d = getattr(ev, "delta", "") or ""
                if d:
                    _发({"类型": "在想", "单元": 单元id, "delta": d, "轮": 1})
            elif isinstance(ev, ToolCallStartEvent):
                cid = getattr(ev, "tool_call_id", "") or ""
                tname = getattr(ev, "tool_call_name", "") or ""
                _工具名[cid] = tname
                _工具参数[cid] = []
                _结果累积[cid] = []
                _发({"类型": "动作开始", "单元": 单元id, "动作id": cid, "工具": tname, "轮": 1})
                if tname == "escalate_to_work":
                    转办事了[0] = True
            elif isinstance(ev, ToolCallDeltaEvent):
                cid = getattr(ev, "tool_call_id", "") or ""
                if cid in _工具参数:
                    _工具参数[cid].append(getattr(ev, "delta", "") or "")
            elif isinstance(ev, ToolResultTextDeltaEvent):
                cid = getattr(ev, "tool_call_id", "") or ""
                if cid in _结果累积:
                    _结果累积[cid].append(getattr(ev, "delta", "") or "")
            elif isinstance(ev, ToolResultEndEvent):
                cid = getattr(ev, "tool_call_id", "") or ""
                工具 = _工具名.pop(cid, "")
                参数 = "".join(_工具参数.pop(cid, []))
                目标 = _解析工具目标(参数) or "大厅档案"
                结果 = _清结果("".join(_结果累积.pop(cid, "")))
                if 工具 == "handoff_conversation":
                    try:
                        import json as _json
                        from 平台_工具集 import 归一岗位
                        _参 = _json.loads(参数)
                        _转岗 = 归一岗位(str((_参 or {}).get("岗位") or "")) if isinstance(_参, dict) else None
                        if _转岗 and _转岗 != 岗位:
                            动作袋["转交岗位"] = _转岗
                            动作袋["转交原因"] = str(_参.get("原因") or "").strip()
                    except Exception:  # noqa: BLE001
                        pass
                elif 工具 == "invite_colleagues_to_speak":
                    try:
                        import json as _json
                        from 平台_工具集 import 归一岗位列表
                        _参 = _json.loads(参数)
                        _邀岗 = 归一岗位列表((_参 or {}).get("岗位") or []) if isinstance(_参, dict) else []
                        _邀岗 = [x for x in _邀岗 if x != 岗位]
                        _已有 = list(动作袋.get("邀请岗位") or [])
                        动作袋["邀请岗位"] = list(dict.fromkeys([*_已有, *_邀岗]))
                        动作袋["邀请原因"] = str(_参.get("原因") or "").strip()
                    except Exception:  # noqa: BLE001
                        pass
                elif 工具 == "flag_for_responsible_person":
                    try:
                        import json as _json
                        _参 = _json.loads(参数)
                        _原因 = str((_参 or {}).get("原因") or "").strip() if isinstance(_参, dict) else ""
                        if _原因:
                            _已有 = list(动作袋.get("提醒承接人") or [])
                            动作袋["提醒承接人"] = list(dict.fromkeys([*_已有, _原因]))
                    except Exception:  # noqa: BLE001
                        pass
                _发({"类型": "动作完成", "单元": 单元id, "动作id": cid, "工具": 工具,
                    "目标": 目标, "结果": 结果[:1200], "成功": True})
            if 转办事了[0]:
                break  # 不再听后续生成——马上换底盘
            # 每个流事件之间探一次插话——中弹立刻停嘴
            if _插队 is not None and not _插队.empty():
                try:
                    import 事件总线 as _eb取
                    话们 = [str(x.get("text", "")).strip() for x in _eb取.取插话(_插队)]
                    话们 = [x for x in 话们 if x]
                except Exception:  # noqa: BLE001
                    话们 = []
                if 话们:
                    被打断 = "；".join(话们)
                    break
        _说, _想 = _think流.feed("", flush=True)   # 段末冲刷：把暂扣的半截标签放出来
        if _说:
            完整.append(_说)
            _发({"类型": "在说", "单元": 单元id, "delta": _说})
        if _想:
            _发({"类型": "在想", "单元": 单元id, "delta": _想, "轮": 1})
        if 转办事了[0]:
            break
        if 被打断 is None or 打断次数 >= 3:
            break
        打断次数 += 1
        try:
            await agen.aclose()
        except Exception:  # noqa: BLE001
            pass
        尾 = "".join(完整).strip()[-260:]
        完整.append("\n\n")
        _发({"类型": "在说", "单元": 单元id, "delta": "\n"})
        续msgs = [Msg(name=主人称呼(), content=[TextBlock(type="text", text=(
            f"（你话说到一半，{主人称呼()}插话：『{被打断}』。公司底层原则：他开口，你立刻停下听。"
            f"然后**你自己判断他的意思**——是补充或纠正，就带着它把话说完（说过的别重复）；"
            f"是让你停下或让别人说，就立刻收住：输出[沉默]或最多一句简短收尾；是在问你别的，就先答他。"
            f"你刚才说到：…{尾}）"))], role="user")]
    finally:
        try:
            _会话id_var.reset(_会话tok)
            _progress_var.reset(_进展tok)
            _当前岗位_var.reset(_岗位tok)
            _工具间预算.结束统一搜索轮(_搜索预算tok)
        except Exception:  # noqa: BLE001
            pass
        if _插队 is not None and 会话id:
            try:
                import 事件总线 as _eb退
                _eb退.取消插话订阅(会话id, _插队)
            except Exception:  # noqa: BLE001
                pass
        if 转办事了[0]:
            out = "[转办事]"
            # 甲案（2026-07-04）：接下来是自己上岗还是转经理统筹由编排层定，这里只说事实
            _发({"类型": "说完", "单元": 单元id, "内容": "（听出这是活。）"})
            _发({"类型": "走了", "单元": 单元id})
        else:
            out = "".join(完整).strip()
            if not out and 本人.state.context:
                out = 本人.state.context[-1].get_text_content("") or ""
            out = _剥时码前缀(out.strip()) or "（没说话）"   # 兜底剥模型误抄的时间码开头(A)
            _发({"类型": "说完", "单元": 单元id, "内容": out})
            _发({"类型": "走了", "单元": 单元id})
    return out


# 预算步数 = ReAct 硬上限（防死循环烧钱），不是缰绳。给一个大到正常工作够不着的数——
# 模型自己 ReAct 到它认为完成（调 report/发言结束）就停，由它自己决定做几步、几波、换不换方法（公司真谛/会议真谛④.3）。
_失控硬上限步数 = 40


async def 唤醒本人(
    岗位: str,
    来意: str,
    人名: str | None = None,
    预算步数: int = _失控硬上限步数,
    可找人: bool = True,
    干活白名单: list[str] | None = None,
    progress=None,
    会话id: str | None = None,
    只发言: bool = False,
    场合覆盖: str | None = None,
    会议id: str | None = None,
    会议主题: str | None = None,
) -> str:
    """事件唤醒「本人」：加载职责牌 + 记忆 → 建 ReAct agent（带工具 + 权限）→ 给来意，本人自己判断、主动做事。

    返回本人最终说的话（它的报告/结论/请示）。这是活公司的最小单元。
    """
    from 升级_模型层 import 建Agent
    from 平台_工具集 import 本人工具集

    # 嵌套深度硬上限：超过深度的人不再带"开会/派活/找人"工具（只查资料/回答），防指数爆炸。
    _本层深度 = _嵌套深度_var.get() + 1
    if _本层深度 > _嵌套深度硬上限:
        可找人 = False

    _人 = 人名 or _岗位的人(岗位)  # 人岗解耦：这岗位现在坐的是谁，记忆按"人"取（跟人走）
    记忆 = 读其人记忆(_人)
    近期公司摘要 = 读近期公司摘要()
    近期块 = (近期公司摘要 + "\n\n") if 近期公司摘要 else ""
    系统提示 = (
        f"你是{_人}，公司的{岗位}。\n"
        f"称呼规矩：对老板当面一律称“{主人称呼()}”。\n\n"
        f"# 岗位定位\n{岗位定位摘要(岗位)}\n\n"
        f"# 你的记忆（你这个人攒下的过往经验）\n{记忆}\n\n"
        f"{近期块}"
        f"# 指路牌\n{_职责指路牌(岗位)}\n\n"
        f"# 三条硬红线\n{_三条硬红线()}\n\n"
        "你是活人，不是流程零件。有人来找你处理一件事，你自己判断：能直接办就办，缺证据就用工具查，复杂再找人/开会/派活。说人话，别工程腔。"
    )
    if 只发言:
        系统提示 = (
            f"你是{_人}，公司的{岗位}。\n"
            f"称呼规矩：对老板当面一律称“{主人称呼()}”。\n\n"
            f"# 岗位定位\n{岗位定位摘要(岗位)}\n\n"
            f"# 你的记忆（你这个人攒下的过往经验）\n{记忆}\n\n"
            f"{近期块}"
            "# 只发言场合\n"
            f"你现在被叫来做当前场所里的一次判断/发言。直接回答来意,不要汇报{主人称呼()},不要请示。"
            f"称呼规矩：对老板当面一律称“{主人称呼()}”。"
            "你只根据调用方给出的当前场所内容作判断，不调用任何工具，不发起大厅动作。"
            "仍然按你的职责、记忆和人的判断来回应。说人话。"
        )
        from agentscope.permission import PermissionBehavior, PermissionRule
        from 平台_工具集 import 会议裁决工具集
        工具 = 会议裁决工具集()
        from 平台_工具集 import 会议裁决工具名
        放行 = {
            t: [PermissionRule(tool_name=t, rule_content=None, behavior=PermissionBehavior.ALLOW, source="只发言最小工具箱")]
            for t in 会议裁决工具名()
        }
        状态 = AgentState(permission_context=PermissionContext(mode=PermissionMode.DEFAULT, allow_rules=放行))
    elif 干活白名单:  # 干活模式：真写文件，白名单门禁（复用执行引擎的官方 PermissionEngine）
        from 升级_权限层 import 建权限上下文
        from 平台_工具集 import 干活工具集
        写规则: list[str] = []  # 同时收相对+绝对 glob：模型常写相对路径，只给绝对会被 fnmatch 拦掉
        for _p in 干活白名单:
            _p = _p.strip()
            if not _p:
                continue
            是目录 = _p.endswith("/")  # 原始是目录→所有候选都补 /**（⚠️ Path 拼接会吃掉尾 /，必须拼接后再统一补，否则绝对规则变精确路径匹不上 = 6 次派活失败的真根因）
            for _c in ([_p] if _p.startswith("/") else [_p, str(WORK / _p)]):
                写规则.append(_c.rstrip("/") + "/**" if 是目录 else _c)
        # 干活 prompt 保持**任务导向**（实测灌完整身份哲学段落会让模型话痨/只说计划不动手），
        # 但名字和记忆必须带——来干活的也必须是「本人」，不是无记忆空壳（圣域⑩"来的必须是它本人"，
        # 2026-07-02 彻查修正：此前干活模式把身份记忆全扔了，是对根的违背）。
        系统提示 = (
            f"你是{_人}，公司的{岗位}。派给你一个活，你要**真写文件**完成它——不是聊天、不是写计划。\n\n"
            f"称呼规矩：对老板当面一律称“{主人称呼()}”。\n\n"
            f"# 岗位定位\n{岗位定位摘要(岗位)}\n\n"
            f"# 你的记忆（你这个人攒下的过往经验，干活时带着）\n{记忆}\n\n"
            f"{近期块}"
            f"# 指路牌\n{_职责指路牌(岗位)}\n\n"
            f"# 只能写这些路径（白名单，写别处会被平台拦下）\n{干活白名单}\n"
            f"代码根（只读）=`{CODE}`；用户数据根=`{COMPANY}`；用户工作区=`{WORK}`。"
            f"file_path **必须用允许范围内的绝对路径**（例：`{WORK}/交付/x.md`）。\n\n"
            f"# 三条硬红线\n{_三条硬红线()}\n\n"
            f"# 立刻动手做\n"
            f"**直接调用 Write 工具把文件写出来——别只说「我打算怎么写」、别只描述，现在就调工具写出来。** "
            f"新建文件用 Write，改已有文件先 Read 再 Edit。写的是代码就用 run_command 真跑验证（测试/编译），"
            f"跑不过要修到跑过，实在跑不了就在报告里明说。"
            f"写完调 report_to_owner 报告你产出了什么、在哪；报告末尾带一句复盘（做对了什么/踩了什么坑/下次怎么更好）。"
        )
        工具 = 干活工具集()
        # 干活的人也要能：跑命令验证(run_command·执行室)、上网查资料(web_search/read_webpage)、汇报、请示——
        # 这些 FunctionTool 走名级放行（写文件红线仍由白名单 写规则 把关；run_command 自带允许名单+全程留痕）。
        状态 = AgentState(permission_context=建权限上下文(
            写规则, 名级放行工具=["report_to_owner", "ask_owner", "web_search", "read_webpage", "run_command", "unified_search"]))
    else:  # 协作/判断模式：读资料 + 协调（开会 / 找人 / 派活 / 报告 / 请示）
        工具 = 本人工具集(岗位, 可找人=可找人)
        # 权限按根判据「能力 vs 权限」（《公司的真谛》）：开会 / 找人 / 派活 / 报告 / 请示是**协调能力**——
        # 怎么做交给本人，放行。真红线（写文件）在被派活那层用白名单门禁把关（见上面干活模式）；
        # 删除 / 花钱 / 密钥仍要本人主动 ask_owner 请示船主。
        # ⚠️ 不能用 EXPLORE：它纯只读、把开会 / 找人 / 派活全 DENY（agentscope _engine 只读才放行），本人就成了"能想不能动"。
        from agentscope.permission import PermissionBehavior, PermissionRule
        # ⚠️ DEFAULT 下连只读工具也默认 ASK，本人身边没人点确认 → 该用的工具必须显式放行（实测 Grep 不放行=ASK 卡死）。
        # 读资料 + 协调（开会 / 找人 / 派活 / 报告 / 请示）全放行；本层没有写文件工具，真红线在干活那层白名单把关。
        本人该用的 = ["Read", "Grep", "Glob", "web_search", "read_webpage",
                  "unified_search", "recent_hall_chats", "company_runtime",
                  "who_is_free", "report_to_owner", "ask_owner", "find_colleague", "hold_meeting", "assign_task"]
        放行 = {t: [PermissionRule(tool_name=t, rule_content=None, behavior=PermissionBehavior.ALLOW, source="本人该用的工具")]
              for t in 本人该用的}
        状态 = AgentState(permission_context=PermissionContext(mode=PermissionMode.DEFAULT, allow_rules=放行))
    # 必须用流式 reply_stream：官方工具（尤其 Write/Edit）在非流式 reply 下会停在"Waiting for tool 外部确认"、不真执行；
    # 流式下工具才真正执行（实测：流式 Write 真写出文件，非流式只返回 Waiting）。
    def _亮(事件: dict) -> None:  # 把本人过程吐给老板的窗户（自主≠黑箱）
        if progress:
            try:
                progress(事件)
            except Exception:  # noqa: BLE001
                pass

    本人 = 建Agent(岗位, 系统提示, toolkit=工具, 预算步数=预算步数, state=状态, stream=True)

    # ── 活人工作单元：这次唤醒 = 一个活人开始工作。父单元（开会时=PM单元）靠 contextvar 传 ──
    单元id = _uuid.uuid4().hex[:8]
    父单元 = _当前单元_var.get()
    场合 = 场合覆盖 or ("主事" if 父单元 is None else "参与")
    来了事件 = {"类型": "人来了", "单元": 单元id, "父单元": 父单元, "岗位": 岗位, "名字": _人, "场合": 场合}
    if 会议id:
        来了事件.update({"会议id": 会议id, "会议主题": 会议主题 or ""})
    _亮(来了事件)

    # 完整的 ReAct 轨迹捕获：思考 → （一轮里）派出一批工具（可能并行）→ 每个工具的参数+返回结果 → 再思考 …
    完整: list[str] = []
    收口起 = 0  # 最后一次动作之后的发言才是收口（收口规矩：三五句总结/卡点；过程话不进大厅）
    前收口起 = 0  # 上一段的起点——被叫停在"刚动手还没说新话"时，垫底用它，绝不回退全文
    _工具名: dict[str, str] = {}    # 动作id → 工具名
    _工具参数: dict[str, list] = {}  # 动作id → 参数 delta 累积
    _结果累积: dict[str, list] = {}  # 动作id → 返回结果 delta 累积
    _轮 = [0]  # ModelCall 计数 = reasoning 轮次；同一轮里派出的多个工具 = 一批（前端显示"同时派出 N 个"）
    _有效会话id = 会话id or _会话id_var.get()
    # G-1 会议跑飞根治：**每次唤醒本人=一次真烧钱**（会议的每次主席裁决/参会发言、每次派活/找同事都是一次唤醒），
    # 在入口记一笔。原来预算只按"调了工具的动作"计——可开会几乎全是纯说话不调工具、预算账上是零消耗，
    # 老钟就能在自己的 ReAct 里一轮轮反复开会、烧不到 200 上限（探针4：会议开始×5、10分钟停不下）。
    # 让每次唤醒入账，与下方"按动作计数"叠加进同一个 _单会话动作硬上限：几场会议≈上百次唤醒就撞熔断停下。
    if _有效会话id:
        _唤醒n = _增加会话动作(_有效会话id)
        if _唤醒n >= _单会话动作硬上限:
            _亮({"类型": "进展", "内容": f"本次任务唤醒/动作数已达硬上限 {_单会话动作硬上限}（防失控烧钱，含反复开会），已暂停，等船主定夺。"})
            _全文 = "（到硬上限，已暂停等船主定夺。）"
            _亮({"类型": "说完", "单元": 单元id, "内容": _全文})
            _亮({"类型": "走了", "单元": 单元id})
            return _全文
    _插话队列 = None
    # 插话只由**根单元**（主事的本人）和会议室消费——嵌套的参会者/被找的同事/干活的人不再各自订阅，
    # 否则船主一句插话被 N 处同时吃、重复响应（2026-07-02 彻查修正：多重消费）。
    # 会议中的插话由 会议室.开会 摆上桌并让全员响应；根单元在下个动作间隙注入自己的上下文。
    if _有效会话id and 父单元 is None:
        try:
            import 事件总线 as _eb0
            _插话队列 = _eb0.订阅插话(_有效会话id)
        except Exception:  # noqa: BLE001
            pass
    _token = _progress_var.set(progress)
    _token2 = _当前单元_var.set(单元id)
    _token3 = _嵌套深度_var.set(_本层深度)
    _token4 = _会话id_var.set(_有效会话id)
    _token5 = _当前岗位_var.set(岗位)
    import 工具间 as _工具间预算
    _搜索预算tok = _工具间预算.开始统一搜索轮(2)
    停止了 = False
    _未完成工具数 = 0    # >0 = 正在执行工具（开会/派活可几分钟）——这段流空闲是合法的，别判死
    _首事件已到 = False  # 还没吐第一个事件时用「首事件空档」掐 hanging 供应商
    _轮开始 = _time.monotonic()
    try:
        try:
            import 工具间 as _工具间
            _工具间.上工(_人, 岗位, 来意)   # 进工具间打卡上工（和 finally 的下工成对）——给"谁空着"查真状态
        except Exception:  # noqa: BLE001 打卡失败不许拖累主流程
            pass
        _流 = 本人.reply_stream(Msg(name="大厅", content=[TextBlock(type="text", text=来意)], role="user")).__aiter__()
        _think流 = _Think流()   # 剥正文流里的<think>（qwen等把思维吐进正文流→泄漏；截图里老梁的"</think>"就是这么冒出来的）
        while True:
            # 整轮硬上限：无论如何不超过它（真死锁兜底，MCP铁律：progress可重置软钟，硬上限永在）
            if _time.monotonic() - _轮开始 > _整轮硬上限秒:
                _亮({"类型": "错误", "内容": f"⚠️ {_人}这一单跑了超过{int(_整轮硬上限秒)}秒还没收，判定卡死，就地收工（未自动重试，要重跑说一声）。"})
                完整.append(f"\n（这一单超过{int(_整轮硬上限秒)}秒还没完、平台判卡死已停。要重跑说一声。）")
                收口起 = 0
                停止了 = True
                break
            # 按当前流状态选「空档预算」：工具执行期给大宽限，别把正经长会误当断流（这是会议被误杀的根）
            _空档 = _工具执行空档秒 if _未完成工具数 > 0 else (_首事件空档秒 if not _首事件已到 else _产出空档秒)
            try:
                ev = await asyncio.wait_for(_流.__anext__(), timeout=_空档)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                # 超时=信号不是判决：先验证再按类型大声报错（船主「白箱透明」原则），不静默判死
                try:
                    await _流.aclose()  # 显式关流，抄 Agents SDK #2222 取消传播坑
                except Exception:  # noqa: BLE001
                    pass
                if _未完成工具数 > 0:  # 工具执行期超时 = 手上的活卡住了（不是模型死），不探模型
                    verdict, 说明 = "TOOL_STUCK", f"手上的活/会议跑了超过{int(_工具执行空档秒)}秒还没回，可能卡住了"
                else:  # 模型该吐字却不吐——探活分类，判它是真死/欠费/限流/还是就这条流卡了
                    _亮({"类型": "进展", "内容": f"{_人}的模型{int(_空档)}秒没动静，正探活确认到底怎么了…"})
                    verdict, 说明 = await _探活供应商(岗位)
                _类型, _真坏 = _判死用词(verdict)
                _亮({"类型": _类型, "内容": f"⚠️ {_人}的模型不对劲：{说明}（判定：{verdict}）。本次就地收工，未自动重试——要重跑说一声。"})
                完整.append(f"\n（{说明}｜判定{verdict}，已停。要重跑说一声。）")
                收口起 = 0
                停止了 = True
                break
            _首事件已到 = True
            if isinstance(ev, ModelCallStartEvent):  # 新一轮 reasoning——这轮派出的工具算一批
                _轮[0] += 1
            elif isinstance(ev, TextBlockDeltaEvent):
                d = getattr(ev, "delta", "") or ""
                if d:
                    _说, _想 = _think流.feed(d)   # 净发言进大厅气泡、思维改道去"在想"通道
                    if _说:
                        完整.append(_说)
                        _亮({"类型": "在说", "单元": 单元id, "delta": _说})
                    if _想:
                        _亮({"类型": "在想", "单元": 单元id, "delta": _想, "轮": _轮[0]})
            elif isinstance(ev, ThinkingBlockDeltaEvent):  # 真实思考流；具体模型是否返回由现役线路决定
                d = getattr(ev, "delta", "") or ""
                if d:
                    _亮({"类型": "在想", "单元": 单元id, "delta": d, "轮": _轮[0]})
            elif isinstance(ev, ToolCallStartEvent):  # 派出一个动作（工具/开会/派活）
                _未完成工具数 += 1  # 进工具执行期：这段流空闲合法（开会/派活可几分钟），别判断流
                if len(完整) > 收口起:
                    前收口起 = 收口起  # 只有真说了新话才推进上一段（连续动手不冲掉最后一段人话）
                收口起 = len(完整)  # 动了手就说明前面的话是过程独白——收口从这之后重新算
                call_id = getattr(ev, "tool_call_id", "") or ""
                tool_name = getattr(ev, "tool_call_name", "") or ""
                if call_id:
                    _工具名[call_id] = tool_name
                    _工具参数[call_id] = []
                    _结果累积[call_id] = []
                _亮({"类型": "动作开始", "单元": 单元id, "动作id": call_id, "工具": tool_name, "轮": _轮[0]})
            elif isinstance(ev, ToolCallDeltaEvent):  # 动作的参数（看什么文件/搜什么/议题）
                call_id = getattr(ev, "tool_call_id", "") or ""
                if call_id in _工具参数:
                    _工具参数[call_id].append(getattr(ev, "delta", "") or "")
            elif isinstance(ev, ToolResultTextDeltaEvent):  # 动作的返回结果（找到哪些文件/搜到什么）
                call_id = getattr(ev, "tool_call_id", "") or ""
                if call_id in _结果累积:
                    _结果累积[call_id].append(getattr(ev, "delta", "") or "")
            elif isinstance(ev, ToolResultEndEvent):  # 动作真正完成（拿到结果了）
                _未完成工具数 = max(0, _未完成工具数 - 1)  # 出工具执行期：回到"模型该吐字"的严格空档
                call_id = getattr(ev, "tool_call_id", "") or ""
                工具名 = _工具名.pop(call_id, "")
                参数串 = "".join(_工具参数.pop(call_id, []))
                目标 = _解析工具目标(参数串)
                结果 = _清结果("".join(_结果累积.pop(call_id, "")))
                state = getattr(ev, "state", None)
                成功 = (getattr(state, "name", "") or str(state)).upper().find("ERROR") < 0
                _亮({"类型": "动作完成", "单元": 单元id, "动作id": call_id, "工具": 工具名,
                    "目标": 目标, "结果": 结果[:1500], "成功": 成功})
                # 产出收集：真写/改成功的文件路径，给验收闸看"到底动了哪些文件"
                if 成功 and 工具名 in ("Write", "Edit"):
                    收 = _产出收集_var.get()
                    if 收 is not None:
                        fp = _取file_path(参数串)
                        if fp and fp not in 收:
                            收.append(fp)
                # 急停
                try:
                    from 引擎工具 import 急停文件
                    if 急停文件.exists():
                        停止了 = True
                        break
                except Exception:  # noqa: BLE001
                    pass
                # 费用闸：会话总动作数硬上限（防"很多人各自合法跑很多步"的总量失控）
                if _有效会话id:
                    _n = _增加会话动作(_有效会话id)
                    if _n >= _单会话动作硬上限:
                        _亮({"类型": "进展", "内容": f"本次任务动作数已达硬上限 {_单会话动作硬上限}（防失控烧钱），已暂停，等船主定夺。"})
                        停止了 = True
                        break
                # 干预窗口：动作完成、下一轮推理前——把船主插话注入 agent 上下文
                if _插话队列 is not None:
                    try:
                        import 事件总线 as _eb
                        for _插 in _eb.取插话(_插话队列):
                            _txt = _插.get("text", "").strip()
                            if _txt:
                                本人.state.context.append(
                                    Msg(name=主人称呼(), content=[TextBlock(type="text", text=(
                                        f"【{主人称呼()}插话】{_txt}\n（公司底层原则：他开口你立刻接住。自己判断——"
                                        f"是纠正就带着改，是叫停就立刻收住，是新要求就照办。）"))], role="user")
                                )
                    except Exception:  # noqa: BLE001
                        pass
        _说f, _想f = _think流.feed("", flush=True)   # 段末冲刷：把暂扣的半截标签放出来
        if _说f:
            完整.append(_说f)
            _亮({"类型": "在说", "单元": 单元id, "delta": _说f})
        if _想f:
            _亮({"类型": "在想", "单元": 单元id, "delta": _想f, "轮": _轮[0]})
    except asyncio.CancelledError:
        out = _剥时码前缀(_取消收口(完整, 收口起, 前收口起))
        _亮({"类型": "说完", "单元": 单元id, "内容": out, "全文": "".join(完整).strip(), "停止": True})
        _亮({"类型": "走了", "单元": 单元id})
        raise
    finally:
        try:
            import 工具间 as _工具间
            _工具间.下工(_人)   # 干完/中止都进工具间打卡下工——考勤板只留此刻真在跑的人
        except Exception:  # noqa: BLE001
            pass
        if _插话队列 is not None and _有效会话id:
            try:
                import 事件总线 as _eb1
                _eb1.取消插话订阅(_有效会话id, _插话队列)
            except Exception:  # noqa: BLE001
                pass
        _progress_var.reset(_token)
        _当前单元_var.reset(_token2)
        _嵌套深度_var.reset(_token3)
        _会话id_var.reset(_token4)
        _当前岗位_var.reset(_token5)
        _工具间预算.结束统一搜索轮(_搜索预算tok)
    全文 = "".join(完整).strip()
    out = _剥时码前缀("".join(完整[收口起:]).strip() or 全文) or "（没说话）"   # 兜底剥模型误抄的时间码开头(A)
    _亮({"类型": "说完", "单元": 单元id, "内容": out, "全文": 全文, "停止": 停止了})
    _亮({"类型": "走了", "单元": 单元id})
    return out


def _取消收口(完整: list[str], 收口起: int, 前收口起: int) -> str:
    """被急停时的最后一口气：只给"他被停时正说的那一段"。
    绝不回退全文——2026-07-04下午的教训：停在刚动手处→最后段为空→全文兜底把整场过程独白灌进大厅，
    船主质问"审查到底审查了个什么"。宁短勿漏：最后段空就退上一段，再空才认"没来得及说"。"""
    段 = "".join(完整[收口起:]).strip() or "".join(完整[前收口起:收口起]).strip()
    if not 段:
        return "（被叫停，话没来得及说。）"
    return 段 + "\n（说到这被叫停了。）"


def _清结果(原始: str) -> str:
    """把工具返回的 ToolResponse 文本拆出纯内容给船主看（去掉 content=[TextBlock(...)] 外壳）。"""
    原始 = (原始 or "").strip()
    if not 原始:
        return ""
    import re as _re
    # 抽出所有 text='...' / text="..." 里的内容
    片 = _re.findall(r"text=(['\"])(.*?)\1", 原始, _re.S)
    if 片:
        s = "\n".join(p[1] for p in 片)
        return s.replace("\\n", "\n").strip()
    return 原始


def _取file_path(参数json: str) -> str:
    """从 Write/Edit 的参数里取完整 file_path（产出收集用，不截断成文件名）。"""
    try:
        import json as _json
        d = _json.loads((参数json or "").strip())
        if isinstance(d, dict):
            return str(d.get("file_path") or d.get("path") or "").strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _解析工具目标(参数json: str) -> str:
    """从工具调用参数里抽出"查了什么"给船主看（file_path/pattern/path/任务 等关键字段）。"""
    参数json = (参数json or "").strip()
    if not 参数json:
        return ""
    try:
        import json as _json
        d = _json.loads(参数json)
        if not isinstance(d, dict):
            return ""
        # 顺序按"对船主最有信息量"排：议题/任务/结论 > 搜什么(关键词/pattern/query/网址) > 读哪个文件
        for k in ("岗位", "议题", "任务", "结论", "问题", "原因", "关键词", "网址", "pattern", "query", "file_path", "path"):
            v = d.get(k)
            if v:
                s = str(v).strip().replace("\n", " ")
                if k in ("file_path", "path") and "/" in s:
                    s = s.rsplit("/", 1)[-1]  # 路径只留文件名
                return s[:60]
    except Exception:  # noqa: BLE001
        pass
    return ""


if __name__ == "__main__":
    # 自检（真跑一次，烧一点 token）：唤醒项目经理本人，给一句话，看它读职责+记忆、自己判断、用工具、说人话。
    import os
    envf = 本机环境文件()
    if envf is not None and envf.exists():
        for line in envf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    来意 = "我想知道：公司现在用的底层框架是什么？这事你怎么看，简单说说。"
    print(f"【唤醒项目经理本人】来意：{来意}\n" + "=" * 56)
    print(asyncio.run(唤醒本人("项目经理", 来意)))
