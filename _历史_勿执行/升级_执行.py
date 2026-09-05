#!/usr/bin/env python3
"""开发公司 · AgentScope 迁移层 — 执行引擎。【对齐 agentscope 2.0.2 真实 API】

工单执行 = 一个 Agent + 一组白名单工具（Toolkit(tools=[FunctionTool(fn)...])）。
制度逻辑保留、底座纯函数复用。

2.0.2 真实 API（在船主 Mac introspect 确认）：
  - 工具：def/async def fn(...) -> ToolResponse；FunctionTool(fn) 包成 ToolBase；Toolkit(tools=[...]) 构造时传。
  - 智能体：迁移_模型层.建Agent(..., toolkit=Toolkit, 预算步数=N)；预算=ReActConfig.max_iters（官方自带，不再手搓 hook）。
  - 消息：Msg(name=, content=, role=)（关键字）。
  - 急停：agent.interrupt()（若版本支持；不支持则退回开跑前闸 + 旗标）。

复用旧底座纯函数（不重写、不碰旧同步引擎）：
  引擎工具.{读路径,写路径,急停文件,解析工单,任务路径,移动伴随文件,追加记忆,日志,写日志,显,归一,在内,时刻,COMPANY}
  审批.创建请示（三级审批入口）

⚠️ 执行层真模式（接 P1 时）待核验：ToolResponse vs ToolChunk 的返回兼容、agent.interrupt() 在 2.0.2 的可用性。
"""
from __future__ import annotations

import asyncio
import re
import threading
from pathlib import Path
from typing import Any

import 引擎工具 as 巧  # noqa: E402
import 审批  # noqa: E402
from 升级_模型层 import 建Agent  # noqa: E402（各厂官方 ChatModel）
from 升级_权限层 import 建权限上下文  # noqa: E402（债①:官方 PermissionEngine 按白名单 glob 判,删手写双轨）
from agentscope.state import AgentState  # noqa: E402

try:
    from agentscope.message import Msg, TextBlock
    from agentscope.tool import FunctionTool, Toolkit, ToolResponse, Write, Read, Edit
except ImportError as e:  # noqa: BLE001
    raise ImportError(
        "未安装 AgentScope（2.0.x）。请先按『部署_AgentScope.md』执行 `pip install agentscope`。"
        f" 原始错误：{e}"
    ) from e

COMPANY = 巧.COMPANY
进行中 = COMPANY / "工单" / "进行中"
待验收 = COMPANY / "工单" / "待验收"
待确认 = COMPANY / "工单" / "待确认"

_批次锁 = threading.Lock()
_批次在跑 = 0  # 在跑的"确认开工"批次数;瞬时停止键据此决定:收尾后清旗 还是 当场清
_会在跑 = 0   # 在跑的"开会(圆桌立项)"数;停止键据此一并判空闲——停止要管"开会"和"干活"两条线


def 开会开始() -> None:
    """圆桌立项开跑前调(适配.发起任务包它):让瞬时停止键知道"有会在开"。"""
    global _会在跑
    with _批次锁:
        _会在跑 += 1


def 开会结束() -> None:
    """圆桌收尾调:计数-1;若全公司都空闲(无批次无会)且急停旗还在(因停止停下)→清旗,公司即待命。"""
    global _会在跑
    with _批次锁:
        _会在跑 -= 1
        空闲 = _批次在跑 == 0 and _会在跑 == 0
    if 空闲 and 巧.急停文件.exists():
        巧.急停文件.unlink(missing_ok=True)


def _回(text: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=text)])


def 跑验收命令(命令: str) -> str:
    """只允许 test -f / grep -q(-a) / echo，用 && 串联；任何缺失/异常都返回『不通过+原因』，绝不抛异常（制度C）。"""
    try:
        段 = [s.strip() for s in 命令.split("&&") if s.strip()]
        if not 段:
            return "验收不通过：空命令"
        for part in 段:
            toks = part.split()
            if toks[:2] == ["test", "-f"] and len(toks) == 3:
                if not Path(巧.归一(toks[2])).is_file():
                    return f"验收不通过：文件不存在 {toks[2]}"
            elif toks and toks[0] == "grep":
                args = [t for t in toks[1:] if not t.startswith("-")]
                if len(args) < 2:
                    return f"验收不通过：grep 格式不允许 {part}"
                pat, f = args[0].strip("'\""), args[-1]
                p = Path(巧.归一(f))
                if not p.is_file():
                    return f"验收不通过：grep 目标文件不存在 {f}"
                if not re.search(re.escape(pat), p.read_text(encoding="utf-8", errors="replace")):
                    return f"验收不通过：未匹配 {pat}"
            elif toks and toks[0] == "echo":
                continue
            else:
                return f"验收不通过：命令片段不允许（只读白名单 test -f/grep -q/echo）：{part}"
        return "验收通过：全部检查命中"
    except Exception as exc:  # noqa: BLE001
        return f"验收不通过（已优雅兜底，未崩溃）：{exc}"


def _白名单转绝对(白名单列表: list[str]) -> list[str]:
    """工单白名单(相对公司根/`../20_工程_XJ/`前缀/绝对) → 官方 Write/Edit 能 fnmatch 的**绝对路径 glob**。

    官方 Write.match_rule = fnmatch(绝对 file_path, rule_content),故规则必须是绝对 glob。
    """
    out: list[str] = []
    for pat in 白名单列表:
        pat = pat.strip()
        if not pat:
            continue
        if pat.startswith("../20_工程_XJ/"):
            out.append(str(巧.PRODUCT / pat[len("../20_工程_XJ/"):]))
        elif pat.startswith("/") or pat.startswith("~"):
            out.append(str(Path(pat).expanduser()))
        else:
            out.append(str(COMPANY / pat))
    return out


def _建工具集(meta: dict[str, Any], 任务名: str, on_request) -> Toolkit:
    """债①:文件读写改用**官方 Write/Read/Edit**(权限由官方 PermissionEngine 按工单白名单 glob 把门,见 跑工单);
    只保留两件官方没有的自带 FunctionTool:run_check(只读验收链) / request_review(三级请示人事制度)。
    不再在工具内部硬判白名单——那是被删掉的"手写双轨"。"""

    async def 执行验收命令(命令: str) -> ToolResponse:
        """跑工单的只读验收命令（test -f / grep -q / echo）。永不崩，返回通过或不通过+原因。

        Args:
            命令 (str): 验收命令串，可用 && 串联。
        """
        return _回(跑验收命令(命令))

    async def 请示(内容: str, 建议: str = "") -> ToolResponse:
        """遇到白名单外、缺资料、权限/费用/密钥/删除/架构等拿不准的事，发起三级请示（经理预审→必要时船主）。

        Args:
            内容 (str): 你卡在哪、已经查过什么、要谁判断什么。
            建议 (str): 你建议怎么做（可空）。
        """
        cid = 审批.创建请示(任务名, str(meta.get("岗位", "")), 内容, 建议)
        on_request(cid, 内容)
        return _回(f"已发起请示（{cid}），等待裁决。裁决前先做不依赖该判断的部分，或停在此处。")

    自带工具 = [
        FunctionTool(执行验收命令, name="run_check"),
        FunctionTool(请示, name="request_review"),
    ]
    # 官方文件工具:Write/Edit 写改受官方引擎按白名单 glob 把门(白名单内自动执行/外 ASK 停);Read 只读放行。
    官方文件工具 = [Write(), Read(), Edit()]
    return Toolkit(tools=官方文件工具 + 自带工具)


def 执行系统提示(岗位: str, 工单文本: str) -> str:
    from 模型接入 import 花名册
    cfg = 花名册().get(岗位, {})
    parts = [
        f"## 你的真实身份（系统注入）\n你是开发公司「{岗位}」岗，由模型 {cfg.get('model','未知')} 驱动。",
        "## 工作纪律（最高优先）\n动手前先查清事实与项目约束→显式分析→用证据下结论→诚实标注未验证。\n"
        f"## 工具用法（重要）\n读文件用 `Read`、新建文件用 `Write`、改已有文件**先 `Read` 再 `Edit`**（官方安全规则：没读过的已存在文件直接写会被拒）；"
        f"所有 file_path **必须是绝对路径**。公司根=`{COMPANY}`，产品仓=`{巧.PRODUCT}`，按工单白名单拼绝对路径。\n"
        "你只能写工单白名单内的路径：白名单内自动放行，白名单外会被系统拦下——这时别硬试，用 `request_review` 发起请示说明缺什么。"
        "验收用 `run_check`（只读 test -f/grep/echo）。绝不编造、绝不写白名单外。",
    ]
    for rel in (f"岗位/{岗位}/说明书.md", "班规.md", f"岗位/{岗位}/记忆.md"):
        p = COMPANY / rel
        if p.exists():
            parts.append(p.read_text(encoding="utf-8"))
    技能目录 = COMPANY / "技能"
    for sk in sorted((技能目录 / "通用").glob("*.md")) + sorted((技能目录 / "岗位" / 岗位).glob("*.md")):
        parts.append(sk.read_text(encoding="utf-8"))
    parts.append(
        "## 你的工单\n" + 工单文本
        + "\n\n按工单白名单完成改动（绝对路径），用 `run_check` 自验，完成时用一句话复盘（做对/做错/下次）结束。"
    )
    return "\n\n---\n\n".join(parts)


async def 跑工单(任务名: str) -> dict[str, Any]:
    """执行单张工单：急停闸→建 agent+白名单工具→Agent 跑（max_iters=预算）→收尾(复盘进记忆,移待验收)。"""
    if 巧.急停文件.exists():  # 制度A：开跑前先看急停旗
        return {"任务": 任务名, "状态": "未启动", "原因": "全员急停中"}

    path = 巧.任务路径(任务名)
    if not path.exists():
        return {"任务": 任务名, "状态": "缺失", "原因": "找不到工单文件"}
    meta = 巧.解析工单(path)
    岗位 = str(meta.get("岗位", "")).strip()
    预算 = int(str(meta.get("预算步数", "8")).split("/")[0].strip() or 8) if str(meta.get("预算步数", "")) else 8
    工单文本 = path.read_text(encoding="utf-8")

    请示记录: list[str] = []
    tk = _建工具集(meta, 任务名, lambda cid, c: 请示记录.append(cid))
    # 债①:官方 PermissionEngine 按工单白名单 glob 把门(删手写双轨)——
    # Write/Edit 命中白名单(绝对 glob)自动执行、白名单外引擎默认 ASK 停、危险文件(.env/.git)安全 ASK;
    # Read 只读放行;run_check/request_review 自带工具名级放行(安全性由函数自身保证:只读/只写请示流)。
    白名单绝对 = _白名单转绝对(meta.get("白名单列表", []))
    _pc = 建权限上下文(白名单绝对, 名级放行工具=["run_check", "request_review"])
    agent = 建Agent(岗位, 执行系统提示(岗位, 工单文本), toolkit=tk, 预算步数=预算,
                   state=AgentState(permission_context=_pc))
    巧.写日志(巧.日志(path), f"AgentScope 执行开始：{岗位}，预算 {预算} 步（ReActConfig.max_iters）。")

    # 制度A：跑的过程中也能一票急停——监视急停旗，触发就 interrupt
    停 = asyncio.Event()

    async def 监视急停():
        while not 停.is_set():
            if 巧.急停文件.exists():
                try:
                    agent.interrupt()
                except Exception:  # noqa: BLE001  2.0.2 若无 interrupt，退回开跑前闸
                    pass
                return
            await asyncio.sleep(0.5)

    watcher = asyncio.create_task(监视急停())
    try:
        结果 = await agent.reply(Msg(name="项目经理", content=[TextBlock(type="text", text=f"开工：{任务名}。按工单完成，完成请给一句复盘。")], role="user"))
        复盘 = _首文本(结果)
    except asyncio.CancelledError:
        复盘 = "（执行被急停/熔断中断）"
    finally:
        停.set()
        watcher.cancel()

    # 制度B+C：收尾归引擎,区分三结局,绝不把"截断/急停"误判成完成(防绿灯幻觉)
    急停了 = 巧.急停文件.exists()
    未完成 = any(s in 复盘 for s in ("maximum iteration", "without finishing", "Waiting for tool"))
    if 急停了:
        状态 = "急停中断"
        _写正式报告(path, "急停中断", "执行被急停一票否决", "执行中", "已让位急停,未完成", "解除急停后续跑")
    elif 未完成:
        状态 = "熔断未完成"
        _写正式报告(path, "熔断", f"预算{预算}步内未主动报完成", "执行中", "产物可能为草稿,未经验收", "请项目经理/船主批准续步")
        try:
            cid = 审批.创建请示(任务名, 岗位, f"预算{预算}步用尽,任务未主动报完成(产物或为草稿)。", "建议批准追加步数续跑。")
            请示记录.append(cid)
        except Exception:  # noqa: BLE001
            pass
    elif 岗位:
        巧.追加记忆(岗位, f"[{任务名}] {复盘}")
        待验收.mkdir(parents=True, exist_ok=True)
        if 巧.在内(path, 进行中):
            巧.移动伴随文件(path, 待验收 / path.name)
        状态 = "待验收"
        _写正式报告(path, "完工", "任务按工单完成", "完成", 复盘[:200], "等船主验收")
    else:
        状态 = "异常"
    return {"任务": 任务名, "状态": 状态, "岗位": 岗位, "复盘": 复盘, "请示": 请示记录}


def _写正式报告(path: Path, 类别: str, 原因: str, 阶段: str, 判断: str, 下一步: str) -> None:
    """暂停/熔断/完工时写正式报告进工单日志(原因·阶段·判断·下一步),供前端窗户呈现。"""
    报告 = (f"## 正式报告 · {类别}\n- 原因：{原因}\n- 阶段：{阶段}\n"
            f"- 判断：{判断}\n- 下一步：{下一步}\n- 时间：{巧.时刻()}")
    try:
        巧.写日志(巧.日志(path), 报告)
    except Exception:  # noqa: BLE001
        pass


def _首文本(msg: Any) -> str:
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content.strip()
    out = []
    for b in content or []:
        t = b.get("text") if isinstance(b, dict) else getattr(b, "text", None)
        if t:
            out.append(str(t))
    return "".join(out).strip() or "（无复盘文本）"


def 设置急停(on: bool) -> str:  # 保留:内部/兼容用;船主左栏按钮走下面的瞬时 停止()
    if on:
        巧.急停文件.write_text(巧.时刻(), encoding="utf-8")
        return "全员急停已开启"
    巧.急停文件.unlink(missing_ok=True)
    return "全员急停已解除"


def 停止() -> str:
    """瞬时停止键(船主左栏「停止」)：写信号→在跑工单的 watcher(0.5s 轮询)看到即 interrupt。
    有批次在跑→信号留着,等批次收尾(确认开工末尾读完旗、分类完后)安全自动清;无批次在跑→当场清。
    这是个【动作】不是开关:不跨调用、不跨重启赖着(启动还会再清一次,见 适配.初始化)。"""
    巧.急停文件.write_text(巧.时刻(), encoding="utf-8")
    with _批次锁:
        空闲 = _批次在跑 == 0 and _会在跑 == 0  # 干活批次 + 开会 两条线都没活才算真空闲
    if 空闲:
        巧.急停文件.unlink(missing_ok=True)
        return "当前没有在跑的工作，已就绪。"
    return "已停止当前进行中的全部工作（含正在开的会），收尾后公司即恢复待命，可继续发新消息。"


async def 确认开工(任务名列表: list[str]) -> list[dict[str, Any]]:
    """船主点确认开工后，把待确认工单移进行中并并行执行（asyncio.gather）。"""
    global _批次在跑
    进行中.mkdir(parents=True, exist_ok=True)
    跑: list[str] = []
    for name in 任务名列表:
        src = 待确认 / name
        if src.exists():
            巧.移动伴随文件(src, 进行中 / name)
        跑.append(name)
    with _批次锁:
        _批次在跑 += 1
    try:
        return list(await asyncio.gather(*(跑工单(n) for n in 跑)))
    finally:
        with _批次锁:
            _批次在跑 -= 1
            空闲 = _批次在跑 == 0 and _会在跑 == 0
        # 瞬时停止键收尾:本批所有工单都收尾完(各自已在 line "急停了=旗.exists()" 读完旗、分类完)后,
        # 若旗还在(因急停停下)→清掉,公司随即恢复待命。务必在收尾【之后】清,绝不重蹈"急停被覆盖"。
        if 空闲 and 巧.急停文件.exists():
            巧.急停文件.unlink(missing_ok=True)


def 运行(任务名列表: list[str]) -> list[dict[str, Any]]:
    return asyncio.run(确认开工(任务名列表))


if __name__ == "__main__":
    import sys
    print(运行(sys.argv[1:]))
