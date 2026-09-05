#!/usr/bin/env python3
"""记忆P2 · 实时抽取——大厅每场对话收尾后，给每个**说了话的人**跑一次轻量抽取。

抄件（2026-07-03 船主定：扒头部现成的，效果是打分标准）：
- mem0 FACT_RETRIEVAL 的抽取规矩：条目自成体系、具体细节不泛化、相对时间转绝对日期、
  已有的不重复（ADD/NONE 由提示词里带旧流水让模型自己判）。
- 陪伴 App（Nomi/Kindroid）的 passive 方式：流水**实时自动落**，不等审批——错了后台可删（P3）。
- 教导：船主的规矩/纠正直接进他的『船主教导』区(叮嘱)、即时生效；船主在记忆库页可删/可改（2026-07-06 取消『待审』审批门）。

调用方：办公室.py 在一场大厅对话结束后，起后台线程调 抽取一场()，不挡收尾。
模型：统一用文案工程师岗的模型（花名册里最便宜的一台），谁的记忆都由它代抽——
     抽取是机械活，不是"本人"行为，不占各人自己的贵模型。
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from 根 import 数据根
from 实例配置 import 主人ID, 主人别名

COMPANY = 数据根
抽取模型岗 = "项目经理"   # 记忆抽取/判去重/巩固走项目经理的现役线路。
# 改岗即换模型，具体型号只从花名册读取；不要把历史型号写成第二份配置。
船主别名 = list(主人别名())

_抽取规矩 = """你是记忆抽取器，帮「{人名}」（岗位：{岗位}）从刚结束的这场大厅对话里，抽出**值得他长期记住**的内容。

# 抽取规矩（严格遵守）
1. 只抽和{人名}有关的：船主对他说的话/要求/评价、他自己做过的事和结论、对他往后干活有用的事实。
2. 每条 15~80 字，自成体系（单独拿出来也看得懂，不许出现"这个""刚才那事"这种没头没尾的指代）。
3. 具体细节保留原样：数字、名字、文件名、日期，一个都不许泛化丢掉。
4. 相对时间转绝对日期（今天={今天}）。
   本场对话发生于 {今天}；落记忆、写时间一律以这天为准，不要用模型运行当天的日期替代它。
5. 下面给了他已有的最近流水——**已经记过的不要再出**；没有新东西就给空列表，宁缺毋滥。
6. 寒暄、客套、和他无关的别人闲聊，一律不抽。
7. 纯粹的"到场应答"（在/收到/我在线）不抽——那不是经历，是出勤记录。
8. 易变的技术参数（依赖版本号、端口号、文件大小这类明天就可能变的数字）不进个人流水——那是项目状态，不是他该长期记住的事。

# 他已有的最近流水（查重用）
{已有流水}

# 他的旧近况
{旧近况}

# 输出（只输出 JSON，不要任何解释、不要代码围栏）
{{"流水": ["条目", "..."],
  "近况": "若这场对话明显改变了他的近况，在旧近况基础上改写一段≤120字的新近况；否则空字符串",
  "教导候选": ["**仅当**船主明确给他立了一条**往后要一直守的规矩/原则/纠正**(不是一次性派活、不是夸奖表扬、不是普通工作安排)，才原意抄下来；**绝大多数对话都没有**，宁缺毋滥、就空列表"]}}"""


def _今天() -> str:
    return _dt.date.today().isoformat()


def _发生日(发生日期: str | None = None) -> str:
    return (发生日期 or "").strip() or _今天()


def _剥JSON(文: str) -> dict:
    文 = 文.strip()
    文 = re.sub(r"^```(?:json)?\s*|\s*```$", "", 文, flags=re.S)
    m = re.search(r"\{.*\}", 文, flags=re.S)
    if not m:
        return {}
    try:
        r = json.loads(m.group(0))
        return r if isinstance(r, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _更新近况(人名: str, 新文: str, 发生日期: str | None = None) -> None:
    """整段改写近况区（滚动摘要，mem0 的 UPDATE）。"""
    from 活_本人 import 重写某人区
    重写某人区(人名, "近况", f"（{_发生日(发生日期)} 更新）{新文.strip()}")


def _正文(l: str) -> str:
    return re.sub(r"^-\s*(?:\[[^\]]*\]\s*)?", "", (l or "").strip())


async def _记教导(人名: str, 条目: list[str], 发生日期: str | None = None) -> None:
    """船主的教导/规矩直接落进他的『船主教导』区(叮嘱)——先进来即时生效；船主在记忆库页可删/可改。
    和图谱同一套 mem0 式写前判定：让 LLM 判每个候选是不是「真·该长期守的规矩」+「和已有叮嘱不重复」，只留真的——
    治两病：抽取过度(把普通派活/夸奖/闲聊当规矩) + 同一条规矩反复灌。LLM 判不动→字面去重兜底。
    (2026-07-06 船主令：取消『待审』审批门，改乐观写入+可删改。)"""
    from 活_本人 import 给某人记一笔, _读记忆区
    from agentscope.message import Msg, TextBlock
    from 升级_模型层 import 建Agent

    候选 = [c.strip() for c in 条目 if c and c.strip()]
    if not 候选:
        return
    已有 = [_正文(l) for l in _读记忆区(人名).get("船主教导", "").splitlines() if l.strip()]

    def 归一(x: str) -> str:
        return re.sub(r"[\s，。、·:：;；!！?？\-—]", "", (x or ""))

    def 字面新() -> list[str]:
        键 = [归一(h) for h in 已有]
        out, 见 = [], list(键)
        for 条 in 候选:
            k = 归一(条)
            if any(k and h and (k == h or (min(len(k), len(h)) >= 4 and (k in h or h in k))) for h in 见):
                continue
            out.append(条)
            见.append(k)
        return out

    留: list[str]
    try:
        系统 = ("『叮嘱』= 船主给这个人立的、**往后要一直守的规矩/原则/纠正**——不是一次性派活、不是夸奖表扬、不是普通工作安排、不是闲聊。\n"
               f"他已有的叮嘱：{已有}\n刚抽到的候选：{候选}\n"
               "从候选里挑出**真正该记进叮嘱**的——**丢掉**：①和已有叮嘱同义/重复的；②其实不是'长期规矩'的(一次性的活、夸奖、普通话、闲聊)。宁缺毋滥。\n"
               '只输出JSON、别的不说：{{"记下":[从候选里挑真正该记的规矩，原样照抄；没有就空数组]}}')
        g = 建Agent(抽取模型岗, 系统, 名字="叮嘱判定", 最大tokens=512, 思考=False)
        rr = await g.reply(Msg(name="x", content=[TextBlock(type="text", text="判")], role="user"))
        cc = getattr(rr, "content", "")
        wen = cc if isinstance(cc, str) else "".join((b.get("text") if isinstance(b, dict) else getattr(b, "text", "")) or "" for b in (cc or []))
        解析 = _剥JSON(wen)
        留 = [c for c in (解析.get("记下") or []) if c in 候选] if "记下" in 解析 else 字面新()
    except Exception:  # noqa: BLE001
        留 = 字面新()
    写入日期 = (发生日期 or "").strip() or None
    for 条 in 留:
        给某人记一笔(人名, 条, 区="船主教导", 发生日期=写入日期)


async def 给一个人抽取(人名: str, 岗位: str, 对话文本: str, 发生日期: str | None = None) -> dict:
    """一次轻量模型调用 → 自动落流水/近况，教导候选进待确认。返回抽取结果（日志用）。"""
    from agentscope.message import Msg, TextBlock
    from 活_本人 import _读记忆区, 给某人记一笔
    from 升级_模型层 import 建Agent

    日期 = _发生日(发生日期)
    区 = _读记忆区(人名)
    流水尾 = "\n".join([l for l in 区["流水"].splitlines() if l.strip()][-15:]) or "（还没有流水）"
    旧近况 = 区["近况"].strip() or "（空）"
    系统 = _抽取规矩.format(人名=人名, 岗位=岗位, 今天=日期, 已有流水=流水尾, 旧近况=旧近况)
    a = 建Agent(抽取模型岗, 系统, 名字="记忆抽取器", 最大tokens=2048, 思考=False)
    r = await a.reply(Msg(name="办公室", content=[TextBlock(type="text", text=f"# 这场对话\n{对话文本[-4000:]}")], role="user"))
    c = getattr(r, "content", "")
    文 = c if isinstance(c, str) else "".join(
        (b.get("text") if isinstance(b, dict) else getattr(b, "text", "")) or "" for b in (c or [])
    )
    结果 = _剥JSON(文)
    写入日期 = (发生日期 or "").strip() or None
    for 条 in 结果.get("流水") or []:
        if isinstance(条, str) and 条.strip():
            给某人记一笔(人名, 条, 发生日期=写入日期)
    新近况 = (结果.get("近况") or "").strip()
    if 新近况:
        _更新近况(人名, 新近况, 发生日期=日期)
    候选 = [x for x in (结果.get("教导候选") or []) if isinstance(x, str) and x.strip()]
    if 候选:
        await _记教导(人名, 候选, 发生日期=日期)
    return 结果


def 抽取一场(发言者们: list[tuple[str, str]], 对话文本: str, 发生日期: str | None = None) -> None:
    """同步入口（给办公室后台线程用）：对这场每个说了话的人各跑一次抽取。
    错误只落日志不外抛——记忆抽取失败不能影响公司干活。"""
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    日志 = COMPANY / "记忆库" / "抽取日志.md"
    日志.parent.mkdir(parents=True, exist_ok=True)
    for 人名, 岗位 in 发言者们:
        try:
            r = loop.run_until_complete(给一个人抽取(人名, 岗位, 对话文本, 发生日期=发生日期))
            n = len(r.get("流水") or [])
            with 日志.open("a", encoding="utf-8") as f:
                f.write(f"- [{_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}] {人名}：流水+{n}"
                        f"{'，近况已更新' if (r.get('近况') or '').strip() else ''}"
                        f"{'，叮嘱+' + str(len(r.get('教导候选') or [])) if r.get('教导候选') else ''}\n")
        except Exception as e:  # noqa: BLE001
            with 日志.open("a", encoding="utf-8") as f:
                f.write(f"- [{_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}] {人名}：抽取失败 {type(e).__name__}: {e}\n")
    # 关于船主这个人的记忆：一场跑一次（不是每人一次——船主是一个人）。和员工流水两套目标：员工不抽情绪，这份专抽船主的情绪/状态。
    # 失败只落日志、不外抛——它挂了不能影响公司干活，也不影响上面员工记忆。
    try:
        import 船主记忆
        r2 = loop.run_until_complete(船主记忆.抽取船主(对话文本, 发生日期=发生日期))
        有 = bool((r2.get("状态") or "").strip() or (r2.get("偏好") or []) or (r2.get("待办") or []) or (r2.get("基本事实") or []))
        with 日志.open("a", encoding="utf-8") as f:
            f.write(f"- [{_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}] 船主记忆：{'已更新' if 有 else '这场没抽到关于船主的新东西'}\n")
    except Exception as e:  # noqa: BLE001
        with 日志.open("a", encoding="utf-8") as f:
            f.write(f"- [{_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}] 船主记忆：抽取失败 {type(e).__name__}: {e}\n")
    # 图谱记忆(记忆v2 step4·populate)：同一场对话顺手抽三元组入图——结构化事实、能查现值/连关系/不打架。
    # 受控谓词+时间不编+低置信待核，失败只落日志、不影响公司也不影响上面的记忆。
    try:
        import 图记忆
        图记忆.落实体(主人ID(), 船主别名)
        落 = loop.run_until_complete(图记忆.从对话抽图(对话文本, source="大厅对话"))
        with 日志.open("a", encoding="utf-8") as f:
            f.write(f"- [{_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}] 图谱：入图 {len(落)} 条三元组\n")
    except Exception as e:  # noqa: BLE001
        with 日志.open("a", encoding="utf-8") as f:
            f.write(f"- [{_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}] 图谱：抽取失败 {type(e).__name__}: {e}\n")
    try:
        loop.run_until_complete(loop.shutdown_asyncgens())  # 关掉流式生成器，避免"Task was destroyed"警告
    except Exception:  # noqa: BLE001
        pass
    loop.close()
