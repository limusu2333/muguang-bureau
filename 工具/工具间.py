#!/usr/bin/env python3
"""死平台 · 工具间 —— 公司里工具/能力的家，也是那块"谁在工作台上、谁空着"的考勤板。

为什么有这间房（2026-07-09）：船主问"我让老钟去看谁空着，他能看到老纪在忙吗"——查实：不能。
谁在跑任务的真状态，系统里没做成任何人能查的东西。真谛⑪：加能力＝建一间房＋写规则牌。
（执行室那台"跑代码的机器"本就说自己在"公司工具间里"；工具间就是这些能力的家。这是它的第一件工具：查在岗。）
（此前舟把这能力瞎塞进函数表、还自编过"值班室"，被船主纠正"场所≠工具、别乱编房间"——这版按《房间插槽规范》正经建在工具间里。）

规则牌（能力 vs 权限 vs 可见性）：
- **能力**（交给人）：谁想知道"此刻谁在跑活、谁空着"，进工具间查一眼（查在岗）——判断派谁、谁能接活。
- **权限**（焊平台）：只读、谁都能看；**没人能手工改在岗状态**——状态只由"被唤醒干活(唤醒本人)"这件事**自动打卡**：进房上工、活完下工。人手改不了考勤，防表演。
- **可见性**（焊平台）：每次上工/下工逐条落 `工具间/记录/YYYY-MM-DD.jsonl`——留痕是场所自带的功能（圣域⑫），失败不许拖垮主流程。
- **诚实边界**：这里的"在岗/在忙"只反映"此刻有没有被唤醒在跑 `唤醒本人` 的活"。**纯聊天不算忙、在别处不算忙**；它是真状态，不是翻聊天记录猜的。空着＝花名册里没在跑活的人。
"""
from __future__ import annotations

import datetime as dt
import contextvars
import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, ToolResponse

from 根 import 数据根

COMPANY = 数据根
记录目录 = COMPANY / "工具间" / "记录"

_在岗: dict[str, dict] = {}   # 人名 → {岗位, 干啥, 起}：此刻在跑任务的人（唤醒本人期间）。工具间自持、只读，别人改不了
_锁 = threading.Lock()


@dataclass
class _搜索预算:
    上限: int
    已用: int = 0
    锁: threading.Lock = field(default_factory=threading.Lock)

    def 占一次(self) -> bool:
        with self.锁:
            if self.已用 >= self.上限:
                return False
            self.已用 += 1
            return True


_搜索预算_var: contextvars.ContextVar[_搜索预算 | None] = contextvars.ContextVar(
    "_xj_unified_search_budget", default=None,
)


def 开始统一搜索轮(上限: int = 2):
    return _搜索预算_var.set(_搜索预算(max(1, min(int(上限), 4))))


def 结束统一搜索轮(token) -> None:
    _搜索预算_var.reset(token)


def _留痕(obj: dict) -> None:
    try:
        记录目录.mkdir(parents=True, exist_ok=True)
        obj.setdefault("时间", dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        with (记录目录 / f"{dt.date.today()}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 留痕失败不许拖累主流程
        pass


# ── 查大厅历史（只读）——原在 平台_工具集 的散函数，归进工具间这一家"查"工具 ──
def _截断工具出参(obj, 上限: int = 1500) -> str:
    """只读工具出参截断，防止一次把大厅历史刷屏。"""
    s = json.dumps(obj, ensure_ascii=False, indent=1)
    return s if len(s) <= 上限 else s[: 上限 - 1] + "…"


async def 搜历史(query: str, 条数: int = 5) -> ToolResponse:
    """搜索大厅历史对话。记不清过去聊过什么、某个词是谁提过时用我。

    Args:
        query (str): 要找的关键词或短句。
        条数 (int): 返回条数，默认5，最多20。
    """
    from 大厅检索 import 搜历史 as _搜历史
    return ToolResponse(content=[TextBlock(type="text", text=_截断工具出参(_搜历史(query, 条数), 1500))])


async def 统一查(
    query: str,
    条数: int = 6,
    范围: str = "全部",
    人物: str = "",
    开始日期: str = "",
    结束日期: str = "",
) -> ToolResponse:
    """统一搜索公司证据：公司文档、大厅原话、本人私册、公共图谱一次查全并统一排序。

    Args:
        query (str): 用自然语言描述要找的事实、旧事、人物或规则。
        条数 (int): 返回几条可靠结果，默认6，最多10。
        范围 (str): 全部/公司文档/大厅/本人记忆/图谱；默认全部。
        人物 (str): 可选，只看与此人直接相关的证据。
        开始日期 (str): 可选，YYYY-MM-DD；时间过滤在排序前执行。
        结束日期 (str): 可选，YYYY-MM-DD；时间过滤在排序前执行。
    """
    from 统一搜索 import render_result, search

    scope = {
        "公司文档": ["company_document"], "文档": ["company_document"],
        "大厅": ["hall"], "本人记忆": ["personal_memory"], "个人记忆": ["personal_memory"],
        "图谱": ["graph"], "公共图谱": ["graph"],
    }
    filters: dict = {}
    if str(范围 or "全部").strip() not in ("", "全部"):
        wanted: list[str] = []
        for part in re.split(r"[,，、/\s]+", str(范围)):
            wanted.extend(scope.get(part, []))
        if not wanted:
            return ToolResponse(content=[TextBlock(type="text", text="统一搜索拒绝执行：范围只能是 全部/公司文档/大厅/本人记忆/图谱。")])
        filters["sources"] = list(dict.fromkeys(wanted))
    if str(人物 or "").strip():
        filters["people"] = [str(人物).strip()]
    if str(开始日期 or "").strip():
        filters["start_at"] = str(开始日期).strip()
    if str(结束日期 or "").strip():
        filters["end_at"] = str(结束日期).strip()
    budget = _搜索预算_var.get()
    if budget is not None and not budget.占一次():
        return ToolResponse(content=[TextBlock(type="text", text=(
            f"本轮已经完成 {budget.上限} 次统一搜索。请先根据已有证据回答；"
            "确实缺少关键事实时，向实例主人说明还缺什么，下一轮再精确查。"
        ))])
    result = await search(query, max(1, min(int(条数 or 6), 10)), filters)
    _留痕({
        "动作": "统一搜索", "查询": str(query)[:120], "范围": 范围,
        "状态": result.status, "命中数": len(result.hits),
        "故障源": [k for k, v in result.source_status.items() if v.get("status") == "error"],
    })
    return ToolResponse(content=[TextBlock(type="text", text=render_result(result))])


async def 翻近期(天数: int = 3) -> ToolResponse:
    """翻最近几天大厅聊过什么：每天的摘要行 + 当天最后3句原文。

    Args:
        天数 (int): 最近几天，默认3，最多14。
    """
    from 大厅检索 import 翻近期 as _翻近期
    return ToolResponse(content=[TextBlock(type="text", text=_截断工具出参(_翻近期(天数), 1500))])


def 上工(人名: str, 岗位: str, 干啥: str) -> None:
    """打卡上工：被唤醒开始跑活时登记（唤醒本人调）。和 下工 成对——调用方在 finally 里下工。"""
    人名 = str(人名 or "").strip()
    if not 人名:
        return
    条 = {"岗位": str(岗位 or ""), "干啥": (干啥 or "").strip().replace("\n", " ")[:44] or "(没写清)",
          "起": dt.datetime.now().strftime("%H:%M:%S")}
    with _锁:
        _在岗[人名] = 条
    _留痕({"动作": "上工", "人名": 人名, **条})


def 下工(人名: str) -> None:
    """打卡下工：活完/中止都销账——考勤板只留此刻真在跑的人。"""
    人名 = str(人名 or "").strip()
    with _锁:
        条 = _在岗.pop(人名, None)
    if 条:
        _留痕({"动作": "下工", "人名": 人名, "干的": 条.get("干啥", ""), "起于": 条.get("起", "")})


def 在岗快照() -> list[dict]:
    """此刻在跑任务的人（真状态，非聊天猜）。返回 [{人名,岗位,干啥,起}...]，空＝此刻没人在跑活。"""
    with _锁:
        return [{"人名": k, **v} for k, v in _在岗.items()]


# ── 工具间的第一件工具：查在岗 ──
# 契约·吃：无参。 契约·吐：ToolResponse(此刻谁在忙/谁空着的人话)。
async def 查在岗() -> ToolResponse:
    """查此刻**谁在跑任务、谁空着**——真状态（谁正被唤醒干活），不是翻聊天记录猜。
    船主问"谁空着/谁在忙/派谁合适/谁能接活"时用我，别再翻大厅历史猜谁在不在。无参数。"""
    try:
        import 常识门
        花 = 常识门._花名册()
    except Exception:  # noqa: BLE001
        花 = {}
    忙 = 在岗快照()
    忙名 = {b["人名"] for b in 忙}
    全员 = [str((c or {}).get("名字") or g) for g, c in 花.items()]
    空 = [n for n in 全员 if n not in 忙名]
    行 = []
    if 忙:
        行.append("此刻在跑任务：" + "；".join(
            f"{b['人名']}({b.get('岗位', '')})正在「{b.get('干啥', '')}」(起{b.get('起', '')})" for b in 忙))
    else:
        行.append("此刻没有人在跑任务。")
    行.append("此刻空着(没在跑任务)：" + ("、".join(空) if 空 else "无"))
    try:  # 公告栏·自尊维度：信任等级公开，同事互见——谁把关准、谁老放水，一目了然
        import 信誉
        行.append("信任等级（公开·由把关/交付/验收的真实结果算）：" + "、".join(f"{n}({信誉.信任等级(n)})" for n in 全员))
    except Exception:  # noqa: BLE001
        pass
    行.append("(以上是真状态——谁正被唤醒干活、进过工具间打卡，不是翻聊天记录猜的。)")
    return ToolResponse(content=[TextBlock(type="text", text="\n".join(行))])


async def 查运行版本() -> ToolResponse:
    """依据运行标记和发布锁直接说明版本，不调用模型或搜索历史。"""
    info = _当前正式版本()
    runtime_version = (
        os.environ.get("XJ_COMPANY_VERSION", "").strip()
        or os.environ.get("XJ_ACTIVE_VERSION", "").strip()
    )
    formal_runtime = bool(
        re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", runtime_version)
        and runtime_version not in {"dev", "unversioned"}
    )
    if formal_runtime:
        display = (
            info["显示名"]
            if info is not None and info["编号"] == runtime_version
            else runtime_version
        )
        return ToolResponse(content=[TextBlock(type="text", text=(
            f"当前这个账号运行的是实例主人已经发布的正式版本：{display}"
            f"（内部编号 {runtime_version}）。正式账号不会看到开发版里的未发布改动。"
        ))])
    if info is None:
        formal = "正式用户当前版本记录暂时读不到；这不等于没有发布，也不会改用历史搜索猜。"
    else:
        formal = f"其他所有账号当前运行：{info['显示名']}（内部编号 {info['编号']}）。"
    return ToolResponse(content=[TextBlock(type="text", text=(
        "实例主人当前这间办公室运行的是未命名开发版，只供您本人边改边测。"
        + formal + "正式账号不会看到开发版里的未发布改动。"
    ))])


def _当前正式版本() -> dict[str, str] | None:
    lock_override = os.environ.get("XJ_RELEASE_LOCK", "").strip()
    locks = [Path(lock_override).expanduser()] if lock_override else [
        Path.home() / "Library" / "Application Support" / "XJ Multiuser" / "data" / "active-release.json",
    ]
    store = Path(os.environ.get(
        "XJ_RELEASE_STORE", str(COMPANY.parent / "90_备份" / "发布仓库"),
    )).expanduser()
    for lock in locks:
        try:
            data = json.loads(lock.read_text(encoding="utf-8"))
            version = str(data.get("version") or "").strip()
            if data.get("schema") != 3 or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", version):
                continue
            display = str(data.get("version_name") or "").strip()
            manifest = store / "published" / version / "manifest.json"
            if not display and manifest.is_file():
                release = json.loads(manifest.read_text(encoding="utf-8"))
                if str(release.get("version") or "") == version:
                    display = str(release.get("version_name") or release.get("product_version_name") or "").strip()
            if not display or len(display) > 80 or any(ord(char) < 32 for char in display):
                display = version
            return {"编号": version, "显示名": display}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return None


def 工具间工具() -> list[FunctionTool]:
    """把工具间对外的"查"类工具包成 AgentScope 工具（英文 name 避 OpenAI 400），装进聊天/干活工具集，谁都能用。
    以后往工具间加新能力，就往这个列表里添一件——机房/工具集只认这一个出口，不再散着登记。"""
    return [
        FunctionTool(统一查, name="unified_search", description="统一搜索：公司文档、大厅原话、本人私册、公共图谱共用一条证据链；失败会明确说明，私册只能查本人。"),
        FunctionTool(翻近期, name="recent_hall_chats", description="翻近期：翻最近几天大厅摘要和最后几句原文，只读。"),
        FunctionTool(查在岗, name="who_is_free", description="查在岗：此刻谁在跑任务/谁空着，真状态非翻聊天猜。船主问'谁空着/谁在忙/派谁'时用。无参数。"),
        FunctionTool(查运行版本, name="company_runtime", description="查当前版本：直接说明这个窗口是开发版还是正式用户版，不搜索历史、不调用模型。无参数。"),
    ]


if __name__ == "__main__":
    import asyncio

    async def _t():
        print("工具间自检：")
        print((await 查在岗()).content[0].text)
        上工("老纪", "测试工程师", "跑回归基线")
        print("--- 登记老纪后 ---")
        print((await 查在岗()).content[0].text)
        下工("老纪")
        print("--- 老纪下工后 ---")
        print((await 查在岗()).content[0].text)

    asyncio.run(_t())
