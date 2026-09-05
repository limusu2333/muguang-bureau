#!/usr/bin/env python3
"""死平台 · 本人的工具集 —— 平台提供给「本人」取用的工具。

依据《公司的真谛》《公司装修方案》：工具是死平台提供、活人本人取用。
每个工具的 docstring = 它的「规则说明」（什么时候用我、怎么用），本人读了**自己决定**何时用。
英文 name（避 OpenAI 中文工具名 400，自传二十六/二十二章坑）。

这是当前活公司的工具层。旧流程驱动已归档到 `_历史_勿执行/`，不得再作为现役执行入口。
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, Toolkit, ToolResponse

from 实例配置 import 主人称呼


# 搜历史/翻近期(查大厅历史)已搬进「工具间」这间房，和查在岗同住"查"类；此处只留对船主/协调类工具。


async def report_to_owner(结论: str) -> ToolResponse:
    """把你这件事的最终结论/成果报告给实例主人（老板）。干完活、或讨论出结果、要给老板交代时用我。

    Args:
        结论 (str): 用人话说清你做了什么、结果如何、有什么要老板知道的。像当面汇报那样说人话，别工程腔、别列一堆 1234。
    """
    # 报告落大厅痕迹簿（2026-07-02 彻查修正）：此前报告只回给调用方、不落任何持久层——
    # 任务被停止/崩溃，报告就人间蒸发（06-30 那份评测报告就是这么丢的）。对船主说的话属于大厅，忠实记录。
    try:
        from 活_本人 import _当前岗位_var, _岗位的人
        岗位 = _当前岗位_var.get()
        if 岗位:
            import 大厅记录
            大厅记录.记一句(f"{_岗位的人(岗位)}（{岗位}）", f"【报告{主人称呼()}】" + 结论.strip())
    except Exception:  # noqa: BLE001
        # 留痕失败必须让本人知道（体检P1：假成功会让他以为报告已进大厅，其实蒸发了）
        return ToolResponse(content=[TextBlock(type="text", text=(
            f"【报告{主人称呼()}】\n" + 结论
            + "\n\n（系统提示：这份报告写进大厅失败了，只有你自己看到——收口发言时务必把结论亲口再说一遍。）"))])
    return ToolResponse(content=[TextBlock(type="text", text=f"【报告{主人称呼()}】\n" + 结论)])


def _直达船主(当前岗位: str, 问题: str, 建议: str = "") -> ToolResponse:
    """把一条请示直接送进船主右栏「等你裁决」——经理本人上报、或聊/活消歧（澄清船主自己的话）这类直达场景用。"""
    import 审批
    标题 = "活厅请示·" + 当前岗位 + "·" + (问题.strip().splitlines()[0][:14] if 问题.strip() else "未命名") + "·" + dt.datetime.now().strftime("%H%M%S")
    审批.创建请示(任务=标题, 岗位=当前岗位, 内容=问题, 建议=建议, 直接上报=True)
    return ToolResponse(content=[TextBlock(type="text", text=f"【{当前岗位}已请示{主人称呼()}，在右栏「等你裁决」等他拍板】问题：{问题}" + (f"\n我的建议：{建议}" if 建议 else ""))])


async def ask_owner(问题: str, 你的建议: str = "") -> ToolResponse:
    """遇到只有实例主人能拍板的事（删除/花钱/密钥/最终交付/越界写文件，或需求本身没说清楚），写下来请示、等定。

    路径（越级不存在，2026-07-09）：**员工的请示先经经理（老钟）酌情**——经理能按制度批/驳就自己了结，
    拿不准或触红线才上报船主；**经理本人**的请示直达船主（他是对船主的唯一汇总口）。别自己硬拍越权的事。

    Args:
        问题 (str): 要请示的事，说清为什么得拍板。
        你的建议 (str): 你倾向怎么办（给个参考，好拍）。
    """
    import 审批
    try:
        from 活_本人 import _当前岗位_var, _岗位的人
        当前岗位 = _当前岗位_var.get()
    except Exception as e:  # noqa: BLE001
        return ToolResponse(content=[TextBlock(
            type="text",
            text=f"请示失败：当前没有真实岗位上下文，不能代替任何人请示。错误：{type(e).__name__}: {e}",
        )])
    if not 当前岗位:
        return ToolResponse(content=[TextBlock(
            type="text",
            text="请示失败：当前没有真实岗位本人，不能用默认身份代替任何人请示。",
        )])
    # 经理办公室（有跨部门权）＝对船主的唯一汇总口 → 直达船主
    import 部门
    if 部门.有跨部门权(_岗位的人(当前岗位)):
        return _直达船主(当前岗位, 问题, 你的建议)
    # 员工请示 → 走经理酌情（越级不存在）：经理批/驳自己了结，拿不准才上报船主
    标题 = "活厅请示·" + 当前岗位 + "·" + (问题.strip().splitlines()[0][:14] if 问题.strip() else "未命名") + "·" + dt.datetime.now().strftime("%H%M%S")
    cid = 审批.创建请示(任务=标题, 岗位=当前岗位, 内容=问题, 建议=你的建议, 直接上报=False)
    import asyncio
    try:
        data = await asyncio.get_running_loop().run_in_executor(None, 审批.经理预审, cid)
    except Exception as e:  # noqa: BLE001
        return ToolResponse(content=[TextBlock(type="text", text=(
            f"【{当前岗位}的请示未完成经理预审，流程已暂停、没有越级】"
            f"{type(e).__name__}: {e}\n请示仍在『待经理』，等经理恢复后重审。"
        ))])
    裁 = str(data.get("经理裁决") or "上报")
    理由 = str(data.get("经理理由") or "").strip() or "（未附理由）"
    if 裁 == "上报":
        return ToolResponse(content=[TextBlock(type="text", text=f"【{当前岗位}的请示：经理（老钟）拿不准，已上报船主，在右栏「等你裁决」等船主拍板】\n经理说：{理由}\n问题：{问题}")])
    if 裁 == "批":
        return ToolResponse(content=[TextBlock(type="text", text=f"【经理（老钟）已批准——这事经理拍了、不用惊动船主】理由：{理由}")])
    return ToolResponse(content=[TextBlock(type="text", text=f"【经理（老钟）驳回】理由：{理由}——按经理意见办，确有必要再补充理由重提。")])


def 可参与岗位() -> set:
    """当前公司所有可参与岗位；花名册读坏或为空时停止，不恢复固定旧班子。"""
    import 模型接入

    岗 = {str(k) for k in 模型接入.花名册().keys() if str(k).strip()}
    if not 岗:
        raise RuntimeError("花名册为空，拒绝使用固定旧岗位绕行")
    return 岗


def _别名对() -> list:
    """(关键词, 正式岗位) 容错对：先从花名册『别名』动态生成，再补功能关键词兜底（顺序：具体在前）。"""
    import 模型接入

    对 = []
    for 岗, c in 模型接入.花名册().items():
        for a in (c or {}).get("别名", []) or []:
            if str(a).strip():
                对.append((str(a).strip(), 岗))
    对 += [("首席", "首席工程师"), ("测试", "测试工程师"), ("文案", "文案工程师（兼职）"),
           ("经理", "项目经理"), ("项目", "项目经理"), ("工程", "工程师"), ("开发", "工程师"), ("研发", "工程师")]
    return 对


def 归一岗位(名: str) -> str | None:
    """把模型写的岗位名（常写简称：测试/首席/文案/工程，或别名梁工/强子）容错归一到正式岗位名；认不出返回 None。
    动态读花名册＋别名——招人/改花名册即时生效。顺序要紧：先认更具体的，免得『首席工程师』被『工程』先吃成普通工程师。"""
    名 = (名 or "").strip()
    if not 名:
        return None
    岗集 = 可参与岗位()
    if 名 in 岗集:
        return 名
    for 键, 正 in _别名对():
        if 键 in 名 and 正 in 岗集:
            return 正
    return None


def 归一岗位列表(岗位们: list[str] | str) -> list[str]:
    """把工具给出的公开发言对象归到现役花名册；只解析结构化名单，不分析用户原话。"""
    import 模型接入

    花名册岗位 = [str(k) for k in 模型接入.花名册().keys() if str(k).strip()]
    原项 = [岗位们] if isinstance(岗位们, str) else list(岗位们 or [])
    片段 = [
        x.strip()
        for item in 原项
        for x in re.split(r"[、,，;；/\s]+", str(item or ""))
        if x.strip()
    ]
    if "全员" in 片段:
        return 花名册岗位
    结果: list[str] = []
    for raw in 片段:
        岗位 = 归一岗位(raw)
        if 岗位 and 岗位 not in 结果:
            结果.append(岗位)
    return 结果


async def find_colleague(岗位: str, 要问的事: str) -> ToolResponse:
    """需要别的岗位帮忙、拿主意、或核对他那块的口径时，**自己去找那个同事问**。
    比如：工程师拿不准测试的验收口径，就 find_colleague('测试工程师', '...')；
    项目经理想听首席对方案的判断，就 find_colleague('首席工程师', '...')。这是横向协调，不用谁批准。

    Args:
        岗位 (str): 要找的同事岗位，只能是：项目经理 / 首席工程师 / 工程师 / 测试工程师 / 文案工程师（兼职）。
        要问的事 (str): 你要问他什么、要他帮什么、给他什么背景。说清楚，他才好答。
    """
    岗位 = 归一岗位(岗位) or 岗位
    _岗集 = 可参与岗位()
    if 岗位 not in _岗集:
        return ToolResponse(content=[TextBlock(type="text", text=f"没有「{岗位}」这个岗位。可找：{'、'.join(_岗集)}")])
    from 活_本人 import 唤醒本人, _progress_var, _当前岗位_var, _岗位的人  # 延迟 import 避循环
    # 被找的人是自主的人：步数给足（只设硬上限）、能自己查资料/再找人——不再 预算步数=6、可找人=False 按死。
    回 = await 唤醒本人(岗位, f"同事来找你商量一件事：{要问的事}", progress=_progress_var.get())
    # 走廊留痕（圣域⑫，2026-07-02 补）：横向协调此前在一切永久记录里隐形，现在逐次落走廊痕迹簿。
    try:
        import 走廊记录
        发起岗 = _当前岗位_var.get() or "（无岗位上下文）"
        走廊记录.记一次(f"{_岗位的人(发起岗)}（{发起岗}）" if 发起岗 != "（无岗位上下文）" else 发起岗,
                    f"{_岗位的人(岗位)}（{岗位}）", 要问的事, 回, 类型="找人")
    except Exception:  # noqa: BLE001
        pass
    return ToolResponse(content=[TextBlock(type="text", text=f"【{岗位}的回复】\n{回}")])


async def hold_meeting(议题: str, 参会岗位: str) -> ToolResponse:
    """在真实会议室发起一场讨论。你作为当前本人召集并主持;会议室只提供场地规则:共享桌面、
    忠实留痕、接住实例主人插话并通知全员响应。**开会的门槛（《会议的真谛》五，铁律）：只在"需要几个角色一起协作、互相补盲、凑出一份给船主的共同产出（方案/决议/联合报告）"时才开。** 各自汇报各自的、谈感想、闲聊、不牵扯协作的——**别开会**：自己办、或 find_colleague 找一个人问、或让相关的人各自在大厅答就够了。拿不准要不要开，用 ask_owner 问船主。

    Args:
        议题 (str): 要讨论什么，说清背景和要拿到什么结论。
        参会岗位 (str): 叫谁来，顿号或逗号分隔，从 项目经理/首席工程师/工程师/测试工程师/文案工程师（兼职） 里选**相关**的（一般 2-4 人，至少有执行岗 + 验收/反证岗）。
    """
    参会 = [归一岗位(p) for p in 参会岗位.replace("、", ",").replace("，", ",").split(",")]
    参会 = list(dict.fromkeys(g for g in 参会 if g))  # 容错归一 + 去重保序
    if not 参会:
        return ToolResponse(content=[TextBlock(type="text", text="没填有效参会岗位。可选：" + "、".join(可参与岗位()))])
    from 活_本人 import _会话id_var, _当前岗位_var, _progress_var
    召集人 = _当前岗位_var.get()
    if not 召集人:
        return ToolResponse(content=[TextBlock(type="text", text="会议室拒绝代开会：当前没有真实召集人本人。")])
    import 会议室
    桌面, 结束原因 = await 会议室.开会(
        议题,
        参会,
        progress=_progress_var.get(),
        会话id=_会话id_var.get(),
        召集人=召集人,
    )
    纪要 = "\n\n".join(f"【{g}】{s}" for g, s in 桌面)
    if 结束原因 == "异常":
        # 「人是活的·出了怪事要告状」（2026-07-04 船主令）：会开一半被场地中断=明显异常，
        # 别默默再开一场一样的会（那正是探针4连开5场跑飞的病），而要如实上报，让船主知道场地坏了。
        return ToolResponse(content=[TextBlock(type="text", text=(
            f"⚠️【会议异常中止 · {议题}】\n"
            "这场会没能正常开完就被会议室中断了——不是你或大家判的散会，是**场地出了异常**。这是明显的异常情况。\n"
            f"**别默默再开一场一样的会**：调 report_to_owner 如实告诉{主人称呼()}「会议室异常中止、会没开完」，附下面已有讨论，让他知道场地坏了、由他/舟定夺。\n\n"
            f"已有讨论（供你报告时引用）：\n{纪要}"
        ))])
    if 结束原因 == "达上限":
        return ToolResponse(content=[TextBlock(type="text", text=(
            f"【会议纪要（未自然收敛·到轮次上限）· {议题}】\n参会：{'、'.join(参会)}\n"
            f"讨论了很多轮但主席没判散会，可能太发散。你可以基于已有讨论自己收口给结论，或如实告诉{主人称呼()}卡在哪。别无脑重开。\n\n"
            f"{纪要}"
        ))])
    return ToolResponse(content=[TextBlock(type="text", text=f"【会议纪要 · {议题}】\n参会：{'、'.join(参会)}\n\n{纪要}")])


# ── 交付门判据（2026-07-11 重构：从"用规则猜自然语言"改成"读模型自己下的制式结论"）──────
# 审核人（部门头/经理）本身是模型——让它用自己的语义能力把结论下好、按**制式批文**交出来：
#   【结论】放行   或   【结论】打回
#   【理由】<一句话>
# 本函数只读【结论】栏、精确认『放行/打回』两个词，理由随它写、一个字都不猜。
# 为什么弃掉旧的关键词/否定词判据：中文说"不行"的花样无穷、规则永远填不满，放水/冤枉反复冒（二~四审全栽在这）；
# 语义判断本就是模型的强项、该还给模型（船主 2026-07-11 点破："这种语义理解模型很擅长，你为啥老硬编码"）。
# 这也跟『审批』线早就在用的结构化裁决字段（{"裁决":"批|驳|上报"}，代码只读字段、读不到默认上报）一路。
# 读不到合规【结论】＝没按制式说清 → 判不清（安全兜底：重审/升级，绝不放水、不冤枉）；空/失败标记 → 审崩。
# 结论词后要紧跟边界（标点/空白/结尾），"放行标准没达到"这种被写成句子的不算放行——防漏判放水。
# 解析要点（2026-07-11 六审加固，堵掉首版解析器4个洞）：**逐行**处理，只在**行首**认【结论】/【理由】栏；
# 结论栏的值要**精确等于**『放行/打回』（写成句子/夹别的字都不算 → 判不清，不放水也不冤枉）——
# 六审揪出首版拿正则扫全文(把理由栏/散文里的'结论：放行'当了裁决)、兜底裸 startswith('打回')冤枉、放行兜底不校验整行、边界漏 markdown 四个洞。
from 交付门 import _审结果, _过交付门, _过独立复核, _过经理收敛  # noqa: E402,F401
async def assign_task(岗位: str, 任务: str, 可写路径: str, 任务难度: int = 1) -> ToolResponse:
    """把一个**具体的活派给某岗位的本人去做**（他会真写文件/代码）。讨论清楚了、要落地产出时用我。

    Args:
        岗位 (str): 派给谁（执行岗：首席工程师/工程师/测试工程师/文案工程师（兼职））。
        任务 (str): 让他做什么，说清要产出什么文件、什么内容、验收标准。
        可写路径 (str): 允许他写哪些文件/目录（顿号或逗号分隔，相对公司根或绝对路径），**保护其他文件不被乱碰**。必须给。
        任务难度 (int): 1到5。1=小修，2=单模块，3=跨模块，4=高风险/复杂联调，5=核心架构或不可逆风险。派活者按任务本身自动判断。
    """
    岗位 = 归一岗位(岗位) or 岗位
    if 岗位 not in 可参与岗位():
        return ToolResponse(content=[TextBlock(type="text", text=f"没有「{岗位}」这个岗位。")])
    白名单 = [p.strip() for p in 可写路径.replace("、", ",").replace("，", ",").split(",") if p.strip()]
    if not 白名单:
        return ToolResponse(content=[TextBlock(type="text", text="必须给「可写路径」白名单（保护其他文件不被乱碰）。")])
    try:
        任务难度 = int(任务难度)
    except (TypeError, ValueError):
        任务难度 = 0
    if not 1 <= 任务难度 <= 5:
        return ToolResponse(content=[TextBlock(type="text", text="「任务难度」必须是 1 到 5 的整数。")])
    # 分派门（权限焊在部门规则牌上）：经理(跨部门权)→任何人；部门头→只本部门的员；下级不能给上级派活。
    from 活_本人 import _当前岗位_var as _派活上下文, _会话id_var, _岗位的人 as _名
    _派活岗 = _派活上下文.get()
    if _派活岗:  # 有真实岗位上下文才拦（船主/大厅直接驱动无上下文时放行）
        import 部门
        if not 部门.可派活(_名(_派活岗), _名(岗位)):
            return ToolResponse(content=[TextBlock(type="text", text=f"你（{_派活岗}）没权限给「{岗位}」派活——只有经理能跨部门派活、部门头只能派本部门的员，下级不能给上级派活。要派请走经理，或用 find_colleague / 转办事。")])
    from 活_本人 import 唤醒本人, _progress_var, _产出收集_var, _当前岗位_var, _岗位的人, 给某人记一笔
    # 被派的人是自主的人：步数给足（只设硬上限）、能自己查资料/多步迭代——不再 预算步数=20、可找人=False 按死。
    # 产出收集：干活期间真写/改过的文件路径，验收闸要让船主看见"到底动了哪些文件"（2026-07-02 补）。
    import 部门 as _bm
    def _轨now():
        return dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    轨迹: list = []
    if _派活岗:
        轨迹.append({"时间": _轨now(), "层级": _bm.层级名(_名(_派活岗)), "谁": _名(_派活岗), "动作": f"下发任务 → {岗位}"})
    轨迹.append({"时间": _轨now(), "层级": _bm.层级名(_名(岗位)), "谁": _名(岗位), "动作": "领受、开工"})
    # 先登记、后干活：即使模型/门禁/进程中途失败，船主也能看到这件活曾开工、断在哪，不留下无主产物。
    try:
        import 待验收记录
        import 任务台
        任务组 = str(_会话id_var.get() or "").strip()
        记录id = 待验收记录.开始(岗位, 任务, 轨迹=轨迹, 任务组=任务组, 难度=任务难度)
        任务组 = 任务组 or 记录id
        子任务id = 任务台.创建(
            "派活", "assign_task", 任务, task_id=f"交付-{记录id}",
            负责人=_名(岗位), 父任务=任务组,
        )
        任务台.更新(子任务id, "运行中", 可写路径=白名单, 交付记录=记录id, 难度=任务难度)
    except Exception as e:  # noqa: BLE001
        return ToolResponse(content=[TextBlock(type="text", text=f"【派活未开始：任务/交付登记失败】{type(e).__name__}: {e}")])
    产出文件: list[str] = []
    _tok = _产出收集_var.set(产出文件)
    try:
        回 = await 唤醒本人(岗位, f"派给你一个活，去做：\n{任务}", 干活白名单=白名单, progress=_progress_var.get())
    except Exception as e:  # noqa: BLE001
        待验收记录.标记异常(记录id, f"执行失败：{type(e).__name__}: {e}")
        任务台.更新(子任务id, "失败", 错误=f"{type(e).__name__}: {e}")
        return ToolResponse(content=[TextBlock(type="text", text=f"【{岗位}执行失败，已留下任务记录#{记录id[:6]}】{type(e).__name__}: {e}")])
    finally:
        _产出收集_var.reset(_tok)
    产出文件 = list(dict.fromkeys(str(p) for p in 产出文件 if str(p).strip()))
    轨迹.append({"时间": _轨now(), "层级": _bm.层级名(_名(岗位)), "谁": _名(岗位), "动作": f"提交产出（{len(产出文件)} 个文件）"})
    # 走廊留痕（圣域⑫）：派活交接也是真实往来，逐次落走廊痕迹簿
    try:
        import 走廊记录
        派活岗 = _当前岗位_var.get() or "（无岗位上下文）"
        走廊记录.记一次(f"{_岗位的人(派活岗)}（{派活岗}）" if 派活岗 != "（无岗位上下文）" else 派活岗,
                    f"{_岗位的人(岗位)}（{岗位}）", f"派活：{任务}", 回, 类型="派活")
    except Exception:  # noqa: BLE001
        pass
    if not 产出文件:
        说明 = "被派的是正式落地产出任务，但本次没有真写/改任何文件，不能冒充完成、不能进入验收。"
        待验收记录.标记异常(记录id, 说明)
        任务台.更新(子任务id, "被门禁拦住", 错误=说明)
        return ToolResponse(content=[TextBlock(type="text", text=f"【{岗位}本次空转，未进入验收#{记录id[:6]}】{说明}\n\n模型回复：\n{回}")])
    # 交付门（D）：员工产出必过部门头把关；部门头/单人部门直接进验收（权限焊在部门规则牌上）
    _门过, _门文案, _放行人, _门节点 = await _过交付门(岗位, 任务, 回)
    if _门节点:
        轨迹.append(_门节点)
    if not _门过:
        待验收记录.标记异常(记录id, _门文案)
        任务台.更新(子任务id, "被门禁拦住", 错误=_门文案[:1000])
        return ToolResponse(content=[TextBlock(type="text", text=_门文案)])
    # 信誉受限只会增加独立复核；高信誉不会减少部门头/经理/船主任何一层。
    _复过, _复文案, _复核人, _复节点 = await _过独立复核(岗位, 任务, 回)
    if _复节点:
        轨迹.append(_复节点)
    if not _复过:
        待验收记录.标记异常(记录id, _复文案)
        任务台.更新(子任务id, "被门禁拦住", 错误=_复文案[:1000])
        return ToolResponse(content=[TextBlock(type="text", text=_复文案)])
    # 经理收敛门（②）：谁派的都要在成果出来后重新审；经理本人交付则换独立代理经理。
    _收过, _收文案, _收敛经理, _收节点 = await _过经理收敛(
        任务, 回, _名(_派活岗) if _派活岗 else "", _名(岗位), _放行人 or ""
    )
    if _收节点:
        轨迹.append(_收节点)
    if not _收过:
        待验收记录.标记异常(记录id, _收文案)
        任务台.更新(子任务id, "被门禁拦住", 错误=_收文案[:1000])
        return ToolResponse(content=[TextBlock(type="text", text=_收文案)])
    轨迹.append({"时间": _轨now(), "层级": "船主", "谁": "你", "动作": "待验收拍板"})
    # 护栏丙②：活公司产出→右栏「待你验收」，船主批了才算交付（《装修方案》2026-06-26）
    try:
        待验收记录.提交(
            记录id, 回, 文件=产出文件, 部门头=_放行人,
            独立复核=_复核人, 收敛经理=_收敛经理, 轨迹=轨迹,
        )
        任务台.更新(子任务id, "待验收", 结果=f"交付记录#{记录id}", 文件=产出文件)
    except Exception as e:  # noqa: BLE001
        try:
            待验收记录.标记异常(记录id, f"待验收提交失败：{type(e).__name__}: {e}")
            任务台.更新(子任务id, "失败", 错误=f"待验收提交失败：{type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001
            pass
        return ToolResponse(content=[TextBlock(
            type="text",
            text=f"【{岗位}已完成，但产出登记失败，不能算已交付】\n待验收登记错误：{type(e).__name__}: {e}\n\n{回}",
        )])
    # 成长闭环（圣域⑦，2026-07-02 补）：干完一件活，平台给这个人记一笔事实记录（干了什么、动了哪些文件）。
    # 只记事实不记模型的自夸；船主验收批准/打回时由 待验收记录 再补一笔裁决——人由此越干越懂。
    try:
        给某人记一笔(_岗位的人(岗位), f"[干活] {任务.strip()[:80]} → 产出 {len(产出文件)} 个文件，待验收#{记录id[:6]}")
    except Exception:  # noqa: BLE001
        pass
    文件行 = ("\n实际动了的文件：" + "、".join(产出文件)) if 产出文件 else "\n（本次没有真写/改任何文件）"
    门行 = (f"\n{_门文案}" if _门文案 else "") + (f"\n{_复文案}" if _复文案 else "") + (f"\n{_收文案}" if _收文案 else "")
    return ToolResponse(content=[TextBlock(type="text", text=f"【{岗位}已完成，产出已进「待你验收」#{记录id[:6]}，等{主人称呼()}批了才算交付】{门行}{文件行}\n{回}")])


def 干活工具集() -> Toolkit:
    """给"被派活、要真写文件的本人"装：Write/Read/Edit（白名单门禁由 state 权限上下文把关）+ 资料 + 报告/请示。
    不带找人/开会/派活——专心干活，不再转包。"""
    from agentscope.tool import Edit, Write
    from 房间注册 import 装配工具

    return Toolkit(tools=[
        Write(), Edit(), *装配工具("干活"),
        FunctionTool(report_to_owner, name="report_to_owner"),
        FunctionTool(ask_owner, name="ask_owner"),
    ])


def 本人工具集(岗位: str = "", 可找人: bool = True) -> Toolkit:
    """给「本人」装的工具：精确读文件 + 统一搜索 + 报告/请示实例主人 +（主事的本人还有）找同事、开会、派活。
    可找人=False 给"被找来帮忙/参会的同事"用——他只查资料/回答，不再转手找人、不再发起会/派活（防无限嵌套）。
    """
    from 房间注册 import 装配工具

    工具 = [
        *装配工具("本人"),
        FunctionTool(report_to_owner, name="report_to_owner"),
        FunctionTool(ask_owner, name="ask_owner"),
    ]
    if 可找人:
        工具.append(FunctionTool(find_colleague, name="find_colleague"))
        工具.append(FunctionTool(hold_meeting, name="hold_meeting"))
        工具.append(FunctionTool(assign_task, name="assign_task"))
    return Toolkit(tools=工具)


async def 转办事(原因: str) -> str:
    """语义接活：意图判定归活人，不归词表。
    你在聊天场合听出实例主人这话其实是派活/要开会/要产出——调我，
    你会立刻带着全套工具重新上岗来办这件事。原因写一句你判断的活是什么。"""
    return "收到，正在给你全套工具重新上岗。"


async def 转交对话(岗位: str, 原因: str) -> ToolResponse:
    """只在另一位同事应当接管后续对话时转交责任席。

    只是想听同事意见，用 find_colleague，问完仍由你回答；
    只有你不再是最合适的承接人时，才用这个工具。
    """
    归一 = 归一岗位(岗位)
    if not 归一:
        return ToolResponse(content=[TextBlock(type="text", text=f"找不到『{岗位}』这个岗位，没有转交。")])
    try:
        from 活_本人 import _当前岗位_var
        当前 = str(_当前岗位_var.get() or "")
    except Exception:  # noqa: BLE001
        当前 = ""
    if 归一 == 当前:
        return ToolResponse(content=[TextBlock(type="text", text="你已经在承接这场对话，不能转交给自己。")])
    return ToolResponse(content=[TextBlock(type="text", text=(
        f"已提出把后续对话交给{归一}。"
        f"用一句人话告诉{主人称呼()}为什么转交，然后收口。原因：{str(原因 or '').strip()}"
    ))])


async def 邀请公开发言(岗位: list[str], 原因: str) -> ToolResponse:
    """请若干同事在大厅依次亲自回答，但不改变当前责任席。

    适用于实例主人在请多人分别表达，或当前承接人认为几个人应当公开说自己的那份。
    这是公开发言，不是私下征询，也不是把整场对话转交给别人。

    Args:
        岗位 (list[str]): 应当公开回答的岗位列表；邀请所有现役员工可只填“全员”。
        原因 (str): 为什么请这些人亲自说，写清他们要回应的事情。
    """
    名单 = 归一岗位列表(岗位)
    try:
        from 活_本人 import _当前岗位_var
        当前 = str(_当前岗位_var.get() or "")
    except Exception:  # noqa: BLE001
        当前 = ""
    名单 = [x for x in 名单 if x != 当前]
    if not 名单:
        return ToolResponse(content=[TextBlock(type="text", text="没有找到可邀请的其他同事，本轮不会虚构任何人发言。")])
    return ToolResponse(content=[TextBlock(type="text", text=(
        f"已发出大厅公开邀请：{'、'.join(名单)}。"
        "你若也是被问的一员，就说清自己的那份；不要替他们回答，也不要只在文字里承诺他们会说。"
        f"邀请原因：{str(原因 or '').strip()}"
    ))])


async def 提醒承接人(原因: str) -> ToolResponse:
    """受邀公开发言时，发现事情可能需要正式处理，就把判断交还当前责任人。

    这只是一条提醒，不会创建任务、开会、派活或改变责任席。
    """
    原因 = str(原因 or "").strip()
    if not 原因:
        return ToolResponse(content=[TextBlock(type="text", text="提醒没有写清原因，未形成正式信号。")])
    return ToolResponse(content=[TextBlock(type="text", text=(
        "已把『可能需要正式处理』的判断交还当前责任人；由责任人决定是否接成工作。"
        f"提醒原因：{原因}"
    ))])


def 大厅承接工具集() -> Toolkit:
    """大厅承接人的轻工具箱：查证、询问、公开请人说话、转交，或升级为正式工作。"""
    from 房间注册 import 装配工具
    return Toolkit(tools=[
        *装配工具("发言"),
        FunctionTool(find_colleague, name="find_colleague"),
        FunctionTool(邀请公开发言, name="invite_colleagues_to_speak", description="请一个或多个同事在大厅依次亲自回答，责任席仍归你。参数岗位必须是岗位列表；请全体可传['全员']。这是公开发言，不是私下询问，也不是转交；不要与 handoff_conversation 同轮调用。"),
        FunctionTool(转交对话, name="handoff_conversation", description="转交后续对话的责任席。仅当另一岗位应该直接接管时调用；只是问意见请用 find_colleague。不要与 invite_colleagues_to_speak 同轮调用。参数：岗位、原因。"),
        FunctionTool(转办事, name="escalate_to_work", description="转办事：只有明确要修改文件、产出交付物、执行操作，或进入正式协作时才调我；问感受/谈感想/闲聊/'你们觉得…吗'不算活，别调，就在大厅人话答。参数原因=你判断的活。"),
        FunctionTool(ask_owner, name="ask_owner", description=f"问实例主人：拿不准这话是聊还是活、该不该开会、该出报告还是各自聊聊时，别自己拍、别抢着 escalate——问{主人称呼()}一句、给二选一，他定。参数问题=要问的，你的建议=你倾向。"),
    ])


def 大厅承接工具名() -> tuple[str, ...]:
    """大厅责任人的工具名正源。"""
    return (
        "unified_search", "recent_hall_chats", "who_is_free", "company_runtime", "find_colleague",
        "invite_colleagues_to_speak", "handoff_conversation", "escalate_to_work", "ask_owner",
    )


def 受邀发言工具集() -> Toolkit:
    """受邀者只查资料和提醒责任人；不能转交、扩散邀请或开启正式工作。"""
    from 房间注册 import 装配工具
    return Toolkit(tools=[
        *装配工具("发言"),
        FunctionTool(提醒承接人, name="flag_for_responsible_person", description="只在你发现事情可能需要正式处理时，提醒当前责任人重新判断。它不会开工或改变责任席。参数原因=为什么需要负责人重看。"),
    ])


def 受邀发言工具名() -> tuple[str, ...]:
    return "unified_search", "recent_hall_chats", "who_is_free", "company_runtime", "flag_for_responsible_person"


def 承接复核工具集() -> Toolkit:
    """责任人收到受邀者提醒后，只能查证、请示或决定是否进入正式工作。"""
    from 房间注册 import 装配工具
    return Toolkit(tools=[
        *装配工具("发言"),
        FunctionTool(转办事, name="escalate_to_work", description="确认确实需要修改文件、产出交付物、执行操作或正式协作时，进入正式工作。参数原因=要办什么。"),
        FunctionTool(ask_owner, name="ask_owner", description=f"仍拿不准是否应进入正式工作时，向{主人称呼()}问清楚。参数问题、你的建议。"),
    ])


def 承接复核工具名() -> tuple[str, ...]:
    return "unified_search", "recent_hall_chats", "who_is_free", "company_runtime", "escalate_to_work", "ask_owner"


def 会议裁决工具集() -> Toolkit:
    """会议主席裁决只输出固定格式，不携带大厅动作或协作工具。"""
    return Toolkit(tools=[])


def 会议裁决工具名() -> tuple[str, ...]:
    return ()
