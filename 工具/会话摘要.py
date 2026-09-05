#!/usr/bin/env python3
"""跨窗口记忆 · 近期会话摘要。

抄 ChatGPT Recent Conversation Content 的机制：把每天大厅里真正发生过的对话压成一行，
下次唤醒时作为场所共同记忆注入。实现自己重写，不复制参考件代码。
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import inspect
import json
import re
from pathlib import Path
from typing import Callable

import 大厅记录
import 大厅档案

from 根 import 数据根

COMPANY = 数据根
对话目录 = 大厅记录.对话目录
记忆目录 = COMPANY / "记忆库"
from 资料室 import 会话摘要文件 as 摘要文件   # 进资料室；.巩固状态/抽取日志(杂项)仍在记忆库
状态文件 = 记忆目录 / ".巩固状态.json"
日志文件 = 记忆目录 / "抽取日志.md"


def _记日志(内容: str) -> None:
    日志文件.parent.mkdir(parents=True, exist_ok=True)
    with 日志文件.open("a", encoding="utf-8") as f:
        f.write(f"- [{_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}] 会话摘要：{内容}\n")


def _读状态() -> dict:
    try:
        return json.loads(状态文件.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _写状态(状态: dict) -> None:
    状态文件.parent.mkdir(parents=True, exist_ok=True)
    状态文件.write_text(json.dumps(状态, ensure_ascii=False, indent=1), encoding="utf-8")


def _取文本(msg) -> str:
    c = getattr(msg, "content", "")
    if isinstance(c, str):
        return c.strip()
    out: list[str] = []
    for b in c or []:
        t = b.get("text") if isinstance(b, dict) else getattr(b, "text", None)
        if t:
            out.append(str(t))
    return "".join(out).strip()


async def _默认摘要器(档案文本: str, mmdd: str) -> str:
    """用文案岗蒸馏一天大厅对话；单独包一层，测试时可替换成假摘要器。"""
    from agentscope.message import Msg, TextBlock
    from 升级_模型层 import 建Agent

    系统 = (
        "你是公司的会话摘要器。把昨天大厅对话蒸馏成 1 到 2 行，供下个窗口接续上下文。\n"
        f"格式必须是：{mmdd} 聊了<主题>：<要点，含具体名词/数字>\n"
        "只写事实，不补没出现的内容；寒暄不写；不要代码围栏。"
    )
    a = 建Agent("文案工程师（兼职）", 系统, 名字="会话摘要器", 最大tokens=512, 思考=False)
    r = await a.reply(Msg(name="大厅档案", content=[TextBlock(type="text", text=档案文本[-12000:])], role="user"))
    return _取文本(r)


def _跑摘要器(摘要器: Callable[[str, str], str] | None, 档案文本: str, mmdd: str) -> str:
    fn = 摘要器 or _默认摘要器
    结果 = fn(档案文本, mmdd)
    if inspect.isawaitable(结果):
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return str(loop.run_until_complete(结果))
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # noqa: BLE001
                pass
            loop.close()
    return str(结果)


def _读档案(日期: _dt.date) -> str:
    p = 大厅档案.找日(日期.isoformat(), 对话目录)
    if p is None:
        return ""
    out: list[str] = []
    for ln in 大厅档案.读文本(p).splitlines():
        try:
            d = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        who = str(d.get("who") or "").strip()
        text = str(d.get("text") or "").strip()
        t = str(d.get("时间") or "").strip()
        if who and text:
            out.append(f"{t} {who}: {text}")
    return "\n".join(out)


def _规范摘要(原文: str, mmdd: str) -> list[str]:
    行们 = [re.sub(r"\s+", " ", x).strip(" -") for x in (原文 or "").splitlines() if x.strip()]
    if not 行们:
        return []
    行们 = 行们[:2]
    出: list[str] = []
    for 行 in 行们:
        if not 行.startswith(mmdd):
            行 = f"{mmdd} 聊了公司大厅：{行}"
        出.append(行[:260])
    return 出


def _摘要已存在(mmdd: str) -> bool:
    if not 摘要文件.exists():
        return False
    return any(line.strip().startswith(mmdd + " ") for line in 摘要文件.read_text(encoding="utf-8").splitlines())


def _追加并保留40行(行们: list[str]) -> None:
    摘要文件.parent.mkdir(parents=True, exist_ok=True)
    旧 = []
    if 摘要文件.exists():
        旧 = [x.rstrip() for x in 摘要文件.read_text(encoding="utf-8").splitlines() if x.strip()]
    新 = (旧 + 行们)[-40:]
    摘要文件.write_text("\n".join(新).rstrip() + "\n", encoding="utf-8")


def 生成昨日摘要(*, 今天: _dt.date | None = None, 摘要器: Callable[[str, str], str] | None = None) -> bool:
    """读昨天大厅档案，生成一行近期会话摘要。已生成/无档案则跳过。

    摘要器是测试注入口；生产默认用 `升级_模型层.建Agent("文案工程师（兼职）", ...)`。
    返回 True 表示本次新增了摘要。
    """
    今天 = 今天 or _dt.date.today()
    目标日 = 今天 - _dt.timedelta(days=1)
    目标 = 目标日.isoformat()
    mmdd = 目标日.strftime("%m%d")
    状态 = _读状态()
    if 状态.get("_会话摘要") == 目标 or _摘要已存在(mmdd):
        return False
    档案 = _读档案(目标日)
    if not 档案.strip():
        return False
    try:
        摘要 = _规范摘要(_跑摘要器(摘要器, 档案, mmdd), mmdd)
        if not 摘要:
            return False
        _追加并保留40行(摘要)
        状态["_会话摘要"] = 目标
        _写状态(状态)
        return True
    except Exception as e:  # noqa: BLE001
        _记日志(f"{目标} 生成失败 {type(e).__name__}: {e}")
        return False


def 补齐摘要(*, 今天: _dt.date | None = None, 摘要器: Callable[[str, str], str] | None = None,
             上限天: int = 40) -> int:
    """把**所有落下的、有对话但还没生成摘要的天**补齐（不只是昨天）。

    治船主间歇使用：他不是每天开，常忙十天半个月才回来接着开发。旧逻辑只"今天补昨天"，
    于是"活跃那天的第二天没来"→那天的摘要永远丢了。这里改成：扫所有 < 今天、有大厅档案的日期，
    逐个补上还没摘要的。只补最近 上限天 天（更老的意义不大、且摘要文件本就只留40行）。返回新增几条。
    """
    今天 = 今天 or _dt.date.today()
    if not 对话目录.exists():
        return 0
    候选: list[_dt.date] = []
    for p in 大厅档案.各日文件(对话目录):
        try:
            d = _dt.date.fromisoformat(大厅档案.日期(p))
        except ValueError:
            continue
        if d < 今天:
            候选.append(d)
    候选 = sorted(候选)[-上限天:]  # 太老的不补、按时间序补（老的在前，注入时时间线才顺）
    新增 = 0
    for 日 in 候选:
        mmdd = 日.strftime("%m%d")
        if _摘要已存在(mmdd):
            continue
        档案 = _读档案(日)
        if not 档案.strip():
            continue
        try:
            摘要 = _规范摘要(_跑摘要器(摘要器, 档案, mmdd), mmdd)
            if not 摘要:
                continue
            _追加并保留40行(摘要)
            新增 += 1
        except Exception as e:  # noqa: BLE001
            _记日志(f"{日.isoformat()} 补齐失败 {type(e).__name__}: {e}")
    if 新增:
        状态 = _读状态()
        状态["_会话摘要"] = (今天 - _dt.timedelta(days=1)).isoformat()
        _写状态(状态)
        _记日志(f"补齐了 {新增} 天落下的会话摘要")
    return 新增
