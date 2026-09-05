#!/usr/bin/env python3
"""死平台 · 会议室这个真实场所。

会议室不是项目经理的私人工具,也不是一段临时 for 循环。它是公司平台上的一间房:
有人发起一场会,相关的人被通知进来;会议室负责维持秩序、共享桌面、忠实留痕、接住船主插话。

会议室只管场地规则:记录、广播桌面、通知谁该进来、把船主插话放上桌。
该谁说、够不够散会,由发起这场会的主席本人判断;场所不藏一个模型替人拿主意。
"""
from __future__ import annotations

import json
from typing import Any

from 根 import 代码根

_会议失控上限 = 30


def _桌面文本(桌面: list[tuple[str, str]]) -> str:
    if not 桌面:
        return "（还没有人发言）"
    return "\n\n".join(f"{who}：{text}" for who, text in 桌面)


def _取_json(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("主席裁决输出里没有 JSON 对象")
    obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("主席裁决 JSON 不是对象")
    return obj


def _暂停(理由: str) -> dict[str, str]:
    return {"动作": "暂停", "理由": 理由}


def _解析主席裁决(text: str, 参会: list[str], 召集人: str | None = None) -> dict[str, str]:
    """解析主席本人的裁决。平台是死物、该服务活人的判断，不该因一点不完美就把人轰出去：
    ①岗位名容错归一（"首席/老梁/文案"都认）；②主席/召集人本来就能在自己的会上发言；
    ③解析不了/点错人不直接暂停散会，而是返回「重问」——给主席把话说清的机会（真散会只由主席说了算）。"""
    from 平台_工具集 import 归一岗位
    try:
        obj = _取_json(text)
    except Exception:  # noqa: BLE001
        return {"动作": "重问", "理由": "这次没吐出能读的 JSON 裁决"}
    动作 = str(obj.get("动作") or "").strip()
    岗位原 = str(obj.get("岗位") or "").strip()
    理由 = str(obj.get("理由") or "").strip()
    if 动作 == "散会":
        return {"动作": "散会", "理由": 理由 or "主席判断讨论已经够了"}
    if 动作 == "发言":
        可点 = list(参会) + ([召集人] if 召集人 else [])  # 主席/召集人本来就该能在自己的会上补一句
        for 候选 in (归一岗位(岗位原), 岗位原):  # 先归一（首席→首席工程师），再退回原名精确匹配
            if 候选 and 候选 in 可点:
                return {"动作": "发言", "岗位": 候选, "理由": 理由 or "主席点名"}
        return {"动作": "重问", "理由": f"点的'{岗位原 or '空'}'没归到本场能发言的人（本场：{'、'.join(可点)}）"}
    return {"动作": "重问", "理由": f"动作没读懂：{动作 or '空'}"}


def _参会人名(参会: list[str]) -> list[str]:
    try:
        import yaml
        roster = yaml.safe_load((代码根 / "花名册.yaml").read_text(encoding="utf-8")) or {}
        return [str((roster.get(x) or {}).get("名字") or x) for x in 参会]
    except Exception:  # noqa: BLE001
        return list(参会)


async def _请主席裁决(议题: str, 主席: str, 参会: list[str], 桌面: list[tuple[str, str]], progress=None, 重问: str | None = None) -> dict[str, str]:
    """主席本人判断下一步。会议室不代判，只忠实执行主席的判断；读不懂就再问一次（重问），不擅自散会。"""
    from 活_本人 import 唤醒本人

    可点 = list(参会) + ([主席] if 主席 and 主席 not in 参会 else [])
    来意 = (
        f"你是这场会的召集人/主席本人,不是会议室、不是平台规则。议题: {议题}\n\n"
        f"这场能发言的人（含你自己）: {'、'.join(可点)}\n\n"
        f"完整会议桌面:\n{_桌面文本(桌面)}\n\n"
        "请你用人的语义判断下一步:谁现在最该回应/补盲/澄清（你自己也可以),或者讨论是否已经够了可以散会。"
        "不要让平台替你排轮次;这是你的主席判断。\n"
        "**见好就收**：会议是为了收敛出结论、不是走流程、不是每个人都要发一遍言。"
        "如果核心结论大家已经一致、没有实质新增量了（甚至有人已经在说'别反复讨论了'/'方向没变'），"
        "就**果断散会给结论**，别再为了周全一个个补点人——那只会把会拖成马拉松、白烧钱。宁可散早一点让结论落地。\n"
        "**尤其：如果桌面上最后已经是一份收口总结/最终答案（特别是你自己刚给完的那种），下一步就直接『散会』——"
        "绝不再点人来复述『同意』『没补充』，那纯是浪费、还让船主觉得这会没收住、总结完了底下还在刷。**\n"
        '只输出一行 JSON,不要 Markdown、不要多余的话: {"动作":"发言","岗位":"工程师","理由":"为什么现在该他说"} '
        '或 {"动作":"散会","理由":"为什么够了"}。岗位必须从上面「能发言的人」里精确挑一个。'
    )
    if 重问:
        来意 += f"\n\n⚠️ 上一次平台没读懂你的裁决（{重问}）。请**只输出那一行 JSON**，岗位从上面名单里精确挑。"
    回 = await 唤醒本人(主席, 来意, 预算步数=12, 可找人=False, progress=progress, 只发言=True)
    return _解析主席裁决(回, 参会, 召集人=主席)


def _收船主插话(会id: str, 桌面: list[tuple[str, str]], 插话队列: Any) -> list[str]:
    if 插话队列 is None:
        return []
    try:
        import 事件总线
        import 会议室记录
        插话 = 事件总线.取插话(插话队列)
        收到: list[str] = []
        for item in 插话:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            收到.append(text)
            桌面.append(("船主", text))
            if 会id:
                会议室记录.记一句(会id, "船主", text)
        return 收到
    except Exception:  # noqa: BLE001
        return []


def _发言来意(议题: str, 岗位: str, 桌面: list[tuple[str, str]], 主席: str, 主席理由: str) -> str:
    前文 = _桌面文本(桌面)
    if not 桌面:
        return (
            f"你现在走进会议室。议题: {议题}\n\n"
            f"主席 {主席} 点你先说,理由: {主席理由}\n"
            "前面还没人发言。讲你的判断、理由和你认为要注意的风险。"
            "需要证据就先查资料,别凭空编。说人话。"
        )
    return (
        f"你现在走进会议室。议题: {议题}\n\n"
        f"主席 {主席} 现在点你发言,理由: {主席理由}\n\n"
        f"完整会议桌面如下,包括船主可能的掌舵插话:\n{前文}\n\n"
        "轮到你。你必须真听前面的话:先说你接住/认同/被改变的是哪一点,"
        "再说你还要补充或反对什么、为什么。没新东西就直说没有要补的。"
        "需要证据就先查资料。说人话,别工程腔。"
    )


def _插话响应来意(议题: str, 岗位: str, 桌面: list[tuple[str, str]], 插话: list[str]) -> str:
    return (
        f"你正在会议室里。议题: {议题}\n\n"
        f"船主刚刚插话掌舵: {' / '.join(插话)}\n\n"
        f"完整会议桌面:\n{_桌面文本(桌面)}\n\n"
        "这是掌舵,不是新开一场会。你必须立刻重新思考并调整:船主的话改变了你哪些判断、"
        "你原先的意见是否要收回/补充/坚持,接下来会议应该注意什么。说人话。"
    )


async def _全员响应插话(
    议题: str,
    参会: list[str],
    桌面: list[tuple[str, str]],
    插话: list[str],
    会id: str,
    progress=None,
) -> None:
    from 活_本人 import 唤醒本人
    import 会议室记录

    for 岗位 in 参会:
        发言 = await 唤醒本人(
            岗位,
            _插话响应来意(议题, 岗位, 桌面, 插话),
            progress=progress,
            可找人=False,
            场合覆盖="会议",
            会议id=会id,
            会议主题=议题,
        )
        桌面.append((岗位, 发言))
        if 会id:
            会议室记录.记一句(会id, 岗位, 发言)


async def 开会(议题: str, 参会: list[str], *, 召集人: str, progress=None, 会话id: str | None = None) -> list[tuple[str, str]]:
    """在会议室发起一场会。

    会议室负责:
    - 开场与逐句忠实留痕;
    - 广播完整桌面给下一位发言人;
    - 每一步先接船主插话,把插话作为正式桌面输入,并通知全员响应;
    - 请召集这场会的主席本人判断下一位发言人或散会。
    """
    if not 参会:
        return []
    召集人 = (召集人 or "").strip()
    if not 召集人:
        raise ValueError("会议室拒绝代开会:缺少真实召集人")
    import 会议室记录
    from 活_本人 import 唤醒本人

    会id = 会议室记录.开一场会(议题, 参会, 召集人=召集人)
    人名参会 = _参会人名(参会)
    try:
        import 大厅记录
        大厅记录.记一句(
            "场所",
            f"[会议] {议题}",
            会议id=会id,
            主题=议题,
            参会=人名参会,
            参会岗位=参会,
            状态="进行中",
        )
    except Exception:  # noqa: BLE001
        pass
    if progress:
        try:
            progress({"类型": "会议开始", "会议id": 会id, "主题": 议题, "参会": 人名参会, "参会岗位": 参会})
        except Exception:  # noqa: BLE001
            pass

    插话队列 = None
    if 会话id:
        try:
            import 事件总线
            插话队列 = 事件总线.订阅插话(会话id)
        except Exception:  # noqa: BLE001
            插话队列 = None

    桌面: list[tuple[str, str]] = []
    结束原因 = "散会"  # 散会=主席判够了正常收敛；异常=平台没读懂主席/主席卡住被迫中止；达上限=轮次耗尽
    try:
        for _ in range(_会议失控上限):
            插话 = _收船主插话(会id, 桌面, 插话队列)
            if 插话:
                await _全员响应插话(议题, 参会, 桌面, 插话, 会id, progress=progress)
            决定 = await _请主席裁决(议题, 召集人, 参会, 桌面, progress=progress)
            if 决定.get("动作") == "重问":  # 平台没读懂主席这一句——再问一次把话说清，别一点不完美就散会
                决定 = await _请主席裁决(议题, 召集人, 参会, 桌面, progress=progress, 重问=决定.get("理由"))
            if 决定.get("动作") in ("重问", "暂停"):  # 连问两次还不行 = 真异常，不是正常散会
                结束原因 = "异常"
                说明 = f"会议异常中止：{决定.get('理由') or '主席裁决不可用'}（连问两次仍没拿到可执行裁决，这不是正常散会）"
                桌面.append(("会议室", 说明))
                会议室记录.记一句(会id, "会议室", 说明)
                break
            if 决定.get("动作") == "散会":
                插话 = _收船主插话(会id, 桌面, 插话队列)
                if 插话:
                    await _全员响应插话(议题, 参会, 桌面, 插话, 会id, progress=progress)
                    continue
                结束原因 = "散会"
                break
            岗位 = str(决定.get("岗位") or "")
            发言 = await 唤醒本人(
                岗位,
                _发言来意(议题, 岗位, 桌面, 召集人, 决定.get("理由", "")),
                progress=progress,
                可找人=False,
                场合覆盖="会议",
                会议id=会id,
                会议主题=议题,
            )
            桌面.append((岗位, 发言))
            会议室记录.记一句(会id, 岗位, 发言)
        else:
            结束原因 = "达上限"
            插话 = _收船主插话(会id, 桌面, 插话队列)
            if 插话:
                await _全员响应插话(议题, 参会, 桌面, 插话, 会id, progress=progress)
            说明 = "会议达到轮次上限还没散会（讨论太发散没收敛），平台只暂停烧钱、不替主席判散会"
            桌面.append(("会议室", 说明))
            会议室记录.记一句(会id, "会议室", 说明)
    finally:
        if progress and 会id:
            try:
                progress({"类型": "会议结束", "会议id": 会id})
            except Exception:  # noqa: BLE001
                pass
        if 插话队列 is not None and 会话id:
            try:
                import 事件总线
                事件总线.取消插话订阅(会话id, 插话队列)
            except Exception:  # noqa: BLE001
                pass
    return 桌面, 结束原因
