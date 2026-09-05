#!/usr/bin/env python3
"""关于船主的记忆 —— 公司唯一一份「关于实例主人」的档案（不是员工的工作记忆）。

为什么要这份（2026-07-05 压测 F1/F3 逼出）：
  公司现有记忆全对着「活」——抽取规矩(记忆抽取._抽取规矩 第6/7条)明令「情绪、寒暄一律不抽」，
  船主只作为「发指令的老板」躺在员工流水里，没有一份「他这个人」的档案：情绪、状态、走势全被当噪音扔了。
  这份反过来：专抽关于船主本人的——他此刻的状态/情绪、随口的偏好、在意的人、说过要做的事，跨会话让公司认得他。

诚实边界（两套目标、两套规矩，护栏都在）：
  · 员工自己的流水仍按老规矩「不抽情绪」（护栏在 记忆抽取._抽取规矩）——这份只管船主，不碰员工记忆。
  · 只增不臆造：抽不出/看不出就留空，不编。删档橡皮擦在船主手里。
  · 不重构：复用 记忆抽取 的最便宜模型 + _剥JSON；巩固复用同一台睡前引擎的调法。全公司共享这一份。
"""
from __future__ import annotations

import datetime as _dt
import re
import threading as _threading
from pathlib import Path

from 实例配置 import 主人ID, 主人别名, 主人称呼
from 根 import 数据根

COMPANY = 数据根
from 资料室 import 船主档案   # 公共记忆搬进资料室(2026-07-09)
# A-7：船主.md 也是读-改-写，两场对话挨着收尾会并发写、互相盖掉。读-改-写整段过这把锁串行。
_写锁 = _threading.Lock()

_标题 = f"# 关于{主人称呼()}（公司共同的、关于船主这个人的记忆）"
_区顺序 = ["基本事实", "他在意的_偏好", "近期状态流水", "当前状态基调", "他说过要做的事"]


def _今天() -> str:
    return _dt.date.today().isoformat()


def _发生日(发生日期: str | None = None) -> str:
    return (发生日期 or "").strip() or _今天()


def _切区(文: str) -> dict:
    区, 当前, buf = {}, None, []
    for line in 文.splitlines():
        if line.startswith("## "):
            if 当前 is not None:
                区[当前] = "\n".join(buf).strip()
            当前, buf = line[3:].strip(), []
        elif 当前 is not None:
            buf.append(line)
    if 当前 is not None:
        区[当前] = "\n".join(buf).strip()
    return 区


def _读档() -> str:
    if not 船主档案.exists():
        船主档案.parent.mkdir(parents=True, exist_ok=True)
        _写全({})
    return 船主档案.read_text(encoding="utf-8")


def _写全(区: dict) -> None:
    out = [_标题, ""]
    for name in _区顺序:
        out.append(f"## {name}")
        c = (区.get(name, "") or "").strip()
        if c:
            out.append(c)
        out.append("")
    # F4 二审：不在固定栏顺序里的栏（旧版遗留、或船主手加的 ## 备注）也保留、附在后面，别静默抹掉
    for name, c in 区.items():
        if name in _区顺序:
            continue
        out.append(f"## {name}")
        c = (c or "").strip()
        if c:
            out.append(c)
        out.append("")
    船主档案.parent.mkdir(parents=True, exist_ok=True)
    船主档案.write_text("\n".join(out).strip() + "\n", encoding="utf-8")


def 读区(区名: str) -> str:
    return _切区(_读档()).get(区名, "")


def _写区(区名: str, 新内容: str) -> None:
    with _写锁:  # A-7：读-改-写整段串行
        区 = _切区(_读档())
        区[区名] = (新内容 or "").strip()
        _写全(区)


def _追加区(区名: str, 行: str, 去重: bool = True) -> None:
    行 = 行.strip()
    if not 行:
        return
    with _写锁:  # A-7：读-改-写整段串行
        区 = _切区(_读档())
        旧 = (区.get(区名, "") or "").strip()
        if 去重 and 行 in 旧:
            return
        区[区名] = (旧 + "\n" + 行).strip() if 旧 else 行
        _写全(区)


_船主别名 = set(主人别名()) | {"他"}


_分隔字符 = set("，。、；：！？「」『』（）()[]【】{}<>《》\"'“”‘’ \t-—…·|/\\")
# 连接字：把宾语粘进句子、但自己不构成更长词的字（是/的/叫/养/患…）——它们也算边界，
# 好让散文里"是婚礼摄影师""养的猫叫小咪"能匹配到宾，但不会把"猫粮""熊猫""小北京"这种真复合词的前缀当宾。
_连接字 = set("是的叫养患有在为成爱做给用了只和与跟对把被将会能要想过来去也都还就")
_边界 = _分隔字符 | _连接字


def _含整值(行: str, 词: str) -> bool:
    """词 是否作为『整段』出现在行里——两侧是边界(标点/连接字/行首尾)才算，防子串误伤（如『猫』伤到『猫粮』『熊猫』）。
    太短(1字)不够特异，一律不匹配：宁漏删也不误删船主的记忆。"""
    词 = 词.strip()
    if len(词) < 2:
        return False
    i = 行.find(词)
    while i >= 0:
        前 = 行[i - 1] if i > 0 else ""
        后 = 行[i + len(词)] if i + len(词) < len(行) else ""
        if (not 前 or 前 in _边界) and (not 后 or 后 in _边界):
            return True
        i = 行.find(词, i + 1)
    return False


def 删含(宾: str, 主: str = "") -> int:
    """统一删除·跨层级联：图谱删一条事实时，顺手清 船主.md 里**同一条事实**的行。
    整事实匹配（2026-07-10 二审再加固）：
    - 主体不是船主本人：要求行里同时含『主体+宾值』（主体给了特异性，宾用子串够了）——删『小北患白血病』不连坐『小北是猫』。
    - 主体是船主本人（档案通篇隐含主语、只能凭宾）：宾用**整值边界匹配**、且宾≥2字才删——防『猫』这类短宾把所有含猫的行全抹掉。
    返回删了几行。原始流水(在员工那边)按 ground truth 留着不动。宁漏删不误删。"""
    宾 = (宾 or "").strip()
    if not 宾:
        return 0
    主 = (主 or "").strip()
    需主 = bool(主) and 主 not in _船主别名   # 主体是别人才要求行里也含主体
    with _写锁:
        区 = _切区(_读档())
        删 = 0
        for 名 in list(区.keys()):
            行们 = [l for l in (区.get(名, "") or "").splitlines()]
            留 = []
            for l in 行们:
                命中 = (宾 in l and 主 in l) if 需主 else _含整值(l, 宾)
                if 命中:
                    删 += 1
                else:
                    留.append(l)
            区[名] = "\n".join(留).strip()
        if 删:
            _写全(区)
        return 删


_抽取规矩 = """你在从一段 AI 公司群聊里，抽取【关于{主人称呼}本人】值得长期记住的东西——注意，抽的是**船主这个人**，不是员工干了什么活。

# 规矩
1. 只抽关于船主这个人的：他此刻透出的情绪/状态、他随口提到的偏好、他在意的人和事、他说过要去做（还没做完）的事。
2. **情绪状态要抽**——这正是和员工流水相反的地方：员工流水不抽情绪，这份专抽船主的情绪状态。看出来才抽，没有就留空，别臆造。
3. 具体细节保留原样（数字、名字、日期），相对时间转绝对日期（今天={今天}）。
   本场对话发生于 {今天}；落记忆、写时间一律以这天为准，不要用模型运行当天的日期替代它。
4. 寒暄客套、纯工作细节、和"船主这个人"无关的，不抽。

# 输出（只输出 JSON，不要解释、不要代码围栏）
{{"状态": "若这场看得出船主的情绪/状态，写一句(含日期)，如 '2026-07-05 忙完一天有点低落、说一个人待着不知图啥'；看不出就空串",
  "偏好": ["他随口提的偏好/在意的人和事，每条自成体系；没有就空列表"],
  "待办": ["他说过要做、还没闭环的事；没有就空列表"],
  "基本事实": ["关于他是谁的稳定事实(职业/城市/家庭等)，仅当这场明确出现的；没有就空列表"]}}"""


async def 抽取船主(对话文本: str, 发生日期: str | None = None) -> dict:
    """一场大厅对话收尾后跑一次（不是每人一次——船主是一个人）。用最便宜的文案岗模型，一次调用落档。"""
    from agentscope.message import Msg, TextBlock
    from 记忆抽取 import _剥JSON, 抽取模型岗
    from 升级_模型层 import 建Agent

    日期 = _发生日(发生日期)
    系统 = _抽取规矩.format(今天=日期, 主人称呼=主人称呼())
    a = 建Agent(抽取模型岗, 系统, 名字="船主记忆抽取器", 最大tokens=2048, 思考=False)
    r = await a.reply(Msg(name="办公室", content=[TextBlock(type="text", text=f"# 这场对话\n{对话文本[-4000:]}")], role="user"))
    c = getattr(r, "content", "")
    文 = c if isinstance(c, str) else "".join(
        (b.get("text") if isinstance(b, dict) else getattr(b, "text", "")) or "" for b in (c or [])
    )
    结果 = _剥JSON(文)
    状态 = (结果.get("状态") or "").strip()
    if 状态:
        行 = f"- {状态}" if not 状态.startswith("-") else 状态
        if not re.search(r"\d{4}-\d{2}-\d{2}", 行):
            行 = f"- {日期} {行.lstrip('- ').strip()}"
        _追加区("近期状态流水", 行)
    for p in 结果.get("偏好") or []:
        if isinstance(p, str) and p.strip():
            _追加区("他在意的_偏好", f"- {p.strip()}")
    for t in 结果.get("待办") or []:
        if isinstance(t, str) and t.strip():
            _追加区("他说过要做的事", f"- [{日期}] {t.strip()}")
    for f in 结果.get("基本事实") or []:
        if isinstance(f, str) and f.strip():
            _追加区("基本事实", f"- {f.strip()}")
    return 结果


_巩固规矩 = """下面是{主人称呼}最近一段的「状态流水」（一条一场，记他那会儿的情绪状态）。
请蒸出一句【当前状态基调】：他这段整体往上还是往下、有没有反复出现的压力源或牵挂。不超过 80 字。
**看不出明显走势就直说「近期状态平稳/看不出明显走势」，绝不编。**

# 近期状态流水
{流水}

# 输出（只输出这一句话，不要解释、不要 JSON、不要围栏）"""

_基本事实对账规矩 = """你在维护「{主人称呼}」档案里的【基本事实】区。
下面这些行按写入顺序排列，越靠后越新。它们可能有字面重复、语义重复，或同一件事的新旧值冲突。

# 任务
输出去重、去矛盾后的【当前】基本事实集：
- 同一件事有新旧值时，只留最新值；例如机身从 A7M4 更新到 A7M5，只留 A7M5。
- 字面重复/语义重复合并成一条。
- 明显已被后文推翻的过时事实删掉。
- 拿不准的保留，宁留勿误删。
- 不新增输入里没有的事实，不把具体事实改成更泛的空话。
- 每条保持一行，并以 "- " 开头。

# 原基本事实
{事实}

# 输出
只输出 JSON，不要解释、不要代码围栏：
{{"当前基本事实":["- ..."]}}"""


async def _对账基本事实(建Agent, 抽取模型岗: str) -> tuple[bool, str]:
    """mem0 式睡前判定：只重写【基本事实】区；护栏触发时原样保留。"""
    from agentscope.message import Msg, TextBlock
    from 记忆抽取 import _剥JSON

    原 = [l.strip() for l in 读区("基本事实").splitlines() if l.strip()]
    if not 原:
        return False, "基本事实为空，跳过"
    # 膨胀阈值门(抄"少滚少漂")：没胀到阈值就别每晚 LLM 重写——每次全量重写都有滚雪球漂移风险，
    # 只在真攒多了(去重收益大)才对账一次。原始流水始终是不可变账本，这里省下的重写=少一次漂移机会。
    if len(原) < 90:
        return False, f"基本事实{len(原)}行未膨胀(<90)，跳过对账少滚少漂"

    a = 建Agent(
        抽取模型岗,
        _基本事实对账规矩.format(事实="\n".join(原), 主人称呼=主人称呼()),
        名字="船主基本事实对账器",
        最大tokens=4096,
        思考=False,
    )
    r = await a.reply(Msg(name="办公室", content=[TextBlock(type="text", text="对账基本事实。")], role="user"))
    c = getattr(r, "content", "")
    文 = c if isinstance(c, str) else "".join(
        (b.get("text") if isinstance(b, dict) else getattr(b, "text", "")) or "" for b in (c or [])
    )
    解析 = _剥JSON(文)
    候 = 解析.get("当前基本事实") if isinstance(解析, dict) else None
    if not isinstance(候, list):
        return False, "解析失败，保留原样"

    新: list[str] = []
    已见: set[str] = set()
    for x in 候:
        if not isinstance(x, str):
            continue
        行 = x.strip()
        if not 行:
            continue
        行 = f"- {行.lstrip('- ').strip()}"
        if 行 not in 已见:
            新.append(行)
            已见.add(行)

    if not 新:
        return False, "模型返回空，保留原样"
    # 护栏只挡"砍到几乎空"的 garbage/截断；正当去重(基本事实本就该从膨胀的几百行收敛到几十行)必须放行。
    # 关于船主的 distinct 事实明显 >8（职业/城市/工作室/机身/镜头/自学/转行/平台/跑步…），低于此判为异常、保留原样。
    if len(新) < 8:
        return False, f"模型只剩{len(新)}行(疑似garbage/截断)，保留原样"

    if 新 == 原:
        return False, "无变化"
    _写区("基本事实", "\n".join(新))
    return True, f"基本事实已对账({len(原)}→{len(新)})"


async def 巩固船主() -> bool:
    """睡前跑一次：状态流水蒸走势基调，并对【基本事实】做保守语义对账。"""
    from agentscope.message import Msg, TextBlock
    from 记忆抽取 import 抽取模型岗
    from 升级_模型层 import 建Agent

    有更新 = False
    try:
        ok, _说明 = await _对账基本事实(建Agent, 抽取模型岗)
        有更新 = 有更新 or ok
    except Exception:  # noqa: BLE001
        pass  # 护栏：对账失败只保留原样，不能影响走势巩固

    流水 = 读区("近期状态流水").strip()
    if not 流水:
        return 有更新
    尾 = "\n".join([l for l in 流水.splitlines() if l.strip()][-20:])
    a = 建Agent(抽取模型岗, _巩固规矩.format(流水=尾, 主人称呼=主人称呼()), 名字="船主走势巩固器", 最大tokens=512, 思考=False)
    r = await a.reply(Msg(name="办公室", content=[TextBlock(type="text", text="蒸一句走势基调。")], role="user"))
    c = getattr(r, "content", "")
    文 = c if isinstance(c, str) else "".join(
        (b.get("text") if isinstance(b, dict) else getattr(b, "text", "")) or "" for b in (c or [])
    )
    基调 = (文 or "").strip().strip("。").strip()
    if 基调:
        _写区("当前状态基调", f"（{_今天()} 巩固）{基调}")
        return True
    return 有更新


def 船主画像注入(上限: int = 280) -> str:
    """给唤醒的岗位注入的一小段：认得船主这个人(基本事实 + 图谱现行事实) + 他此刻大致状态(走势基调)。
    跨会话让公司记得他，不是每轮现认。空档案=空串，不硬塞。
    图谱现行事实(记忆v2 step4·inject)：只出'现行'(高置信、没作废、时间上没被新值取代)，去重、封顶——旧值永不喂出。"""
    事实 = 读区("基本事实").strip()
    偏好 = 读区("他在意的_偏好").strip()   # 2026-07-09：偏好也注入——员工认得船主的习惯(升级闸/接活判得准，越攒越懂)
    基调 = 读区("当前状态基调").strip()
    段 = []
    if 事实:
        段.append(f"你知道的{主人称呼()}：" + " ".join(l.lstrip("- ").strip() for l in 事实.splitlines() if l.strip())[:120])
    if 偏好:
        段.append("他的习惯/偏好：" + "；".join(l.lstrip("- ").strip() for l in 偏好.splitlines() if l.strip())[:110])
    try:
        import 图记忆
        现行 = 图记忆.查现值(主人ID())
        对 = []
        已有 = "；".join(段)
        for x in 现行[:8]:
            d = str(x.get("dst") or "").strip()
            if d and d not in 已有 and d not in "、".join(对):   # 去重：基本事实/图谱互不重复
                对.append(f"{x['rel']}{d}")
        if 对:
            段.append("（记得：" + "、".join(对)[:90] + "）")
    except Exception:  # noqa: BLE001
        pass   # 图谱空/没建/查不到都不硬塞——白箱降级不静默造假
    if 基调:
        段.append("他近期状态：" + 基调[:100])
    return ("；".join(段))[:上限] if 段 else ""


if __name__ == "__main__":
    # 自检（不烧钱）：读写分区、追加、去重、注入拼接
    _写全({})
    _追加区("基本事实", "- 西安人，婚礼摄影师/调色师，独居咸阳")
    _追加区("近期状态流水", "- 2026-07-05 忙完一天有点低落、说一个人待着不知图啥")
    _追加区("近期状态流水", "- 2026-07-05 忙完一天有点低落、说一个人待着不知图啥")  # 去重
    _写区("当前状态基调", "（2026-07-05 巩固）近几日偏累偏低，反复提到独处时的空落")
    print("船主.md 现状：\n" + _读档())
    print("注入串：", 船主画像注入())
    print("状态流水条数：", len([l for l in 读区("近期状态流水").splitlines() if l.strip()]), "（应为1，去重生效）")
