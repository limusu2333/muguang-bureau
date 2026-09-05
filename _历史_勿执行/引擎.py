#!/usr/bin/env python3
"""办公室v3执行引擎：确认开工、并行线程、动作执行、急停与请示。"""
from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import 审批
import 会议
import 协同
from 引擎工具 import (
    COMPANY,
    PRODUCT,
    初始控制,
    急停文件,
    控制,
    解析工单,
    读控制,
    读路径,
    工单,
    写控制,
    写日志,
    写路径,
    提取_json,
    日志,
    mock模式,
    时刻,
    显,
    移动伴随文件,
    任务路径,
    原子写,
    追加记忆,
)

_线程: dict[str, threading.Thread] = {}
_锁 = threading.RLock()


class _停止信号(Exception):
    """急停/船主停止触发的中断：必须干净退出，绝不走熔断续步（A·急停一票否决）。"""


def _协同受领(path: Path) -> None:
    try:
        协同.记录工单受领(path)
    except Exception as exc:  # noqa: BLE001
        写日志(日志(path), f"协同记录失败：{exc}")


def _协同事件(path: Path, 类型: str, 发言人: str, 内容: str, 目标: str = "") -> None:
    try:
        协同.记录工单事件(path, 类型, 发言人, 内容, 目标=目标)
    except Exception as exc:  # noqa: BLE001
        写日志(日志(path), f"协同记录失败：{exc}")


def 初始化() -> None:
    for name in ("待确认", "进行中", "待验收", "待办", "已完成", "已作废"):
        (工单 / name).mkdir(parents=True, exist_ok=True)
    (COMPANY / "工具" / "mock产物").mkdir(parents=True, exist_ok=True)
    审批.初始化()
    会议.初始化()
    协同.初始化()
    _恢复运行中()


def _恢复运行中() -> None:
    """办公室重启后，把控制文件仍标记运行中的工单接回线程。"""
    if 急停文件.exists():
        return
    with _锁:
        for p in sorted((工单 / "进行中").glob("*.md")):
            if p.name.endswith(".日志.md"):
                continue
            if p.name in _线程 and _线程[p.name].is_alive():
                continue
            ctl = 读控制(控制(p))
            if ctl.get("停止"):
                continue
            state = str(ctl.get("状态", "运行中"))
            if state.startswith("暂停") or state.startswith("停止"):
                continue
            th = threading.Thread(target=_跑任务, args=(p,), daemon=True, name=f"xj-{p.stem}")
            _线程[p.name] = th
            th.start()


def 发起任务(需求: str, progress=None) -> dict[str, Any]:
    初始化()
    try:
        return 会议.kickoff(需求, progress=progress)
    except Exception as exc:  # noqa: BLE001
        cid = 审批.创建请示("kickoff", "项目经理", f"PM分解失败: {exc}", "请船主改写需求或人工分解")
        审批.经理预审(cid, mock=True)
        raise


def 确认开工(names: list[str] | None = None) -> list[str]:
    初始化()
    src_dir = 工单 / "待确认"
    dst_dir = 工单 / "进行中"
    todo = names or [p.name for p in sorted(src_dir.glob("*.md"))]
    started: list[str] = []
    moved: list[Path] = []
    for name in todo:
        src = src_dir / name
        if not src.exists():
            continue
        dst = dst_dir / name
        shutil.move(str(src), str(dst))
        写控制(控制(dst), 初始控制())
        写日志(日志(dst), f"确认开工：{name}")
        started.append(name)
        moved.append(dst)
    if moved:
        try:
            协同.确认开工会(moved)
        except Exception as exc:  # noqa: BLE001
            for path in moved:
                写日志(日志(path), f"协同开工交办失败：{exc}")
    for path in moved:
        _协同受领(path)
        _启动(path)
    return started


def 继续任务(name: str, extra_steps: int = 4) -> str:
    初始化()
    if 急停文件.exists():
        return "全员急停中，请先解除急停再继续工单"
    src = 工单 / "待办" / name
    if not src.exists():
        return "回炉工单不存在，可能已被移动"
    extra = max(1, int(extra_steps))
    ctlp = 控制(src)
    ctl = 读控制(ctlp)
    ctl["状态"] = "运行中"
    ctl["停止"] = False
    ctl["追加预算"] = int(ctl.get("追加预算", 0) or 0) + extra
    写控制(ctlp, ctl)
    for card in 审批.列表().get("待船主", []):
        if str(card.get("任务", "")) == name and str(card.get("岗位", "")) == "引擎":
            try:
                审批.船主裁决(str(card.get("id")), "批", f"加步继续，追加{extra}步预算。")
            except Exception:  # noqa: BLE001
                pass
    写日志(日志(src), f"船主批准继续：追加{extra}步预算。")
    dst = 工单 / "进行中" / name
    移动伴随文件(src, dst)
    _启动(dst)
    return f"已继续：{name}（追加{extra}步）"


def 停止任务(name: str) -> str:
    path = 任务路径(name)
    if not path.exists():
        return "任务已结束或不存在"
    ctl = 读控制(控制(path))
    ctl["停止"] = True
    ctl["状态"] = "停止中"
    try:
        写控制(控制(path), ctl)
        写日志(日志(path), "船主点击停止。")
        _协同事件(path, "停止请求", "船主", "船主请求停止该工单。", 目标=str(解析工单(path).get("岗位", "")))
    except FileNotFoundError:
        return "任务已结束或不存在"
    return "已请求停止"


def 追加指示(name: str, text: str) -> str:
    path = 任务路径(name)
    if not path.exists():
        return "任务已结束或不存在"
    ctl = 读控制(控制(path))
    ctl.setdefault("追加指示", []).append({"时间": 时刻(), "内容": text})
    try:
        写控制(控制(path), ctl)
        写日志(日志(path), f"收到追加指示：{text}")
        _协同事件(path, "追加指示", "船主", text, 目标=str(解析工单(path).get("岗位", "")))
    except FileNotFoundError:
        return "任务已结束或不存在"
    return "已追加"


def 设置急停(on: bool) -> str:
    if on:
        急停文件.write_text(时刻(), encoding="utf-8")
        return "全员急停已开启"
    急停文件.unlink(missing_ok=True)
    return "全员急停已解除"


def 状态() -> dict[str, Any]:
    初始化()
    running = []
    for p in sorted((工单 / "进行中").glob("*.md")):
        if p.name.endswith(".日志.md"):
            continue
        ctl = 读控制(控制(p))
        running.append({"name": p.name, "control": ctl, "tail": 日志尾巴(p.name, 2500)})
    return {
        "急停": 急停文件.exists(),
        "待确认": 会议.待确认列表(),
        "进行中": running,
        "请示": 审批.列表(),
    }


def 等待空闲(timeout: float = 5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        with _锁:
            living = [t for t in _线程.values() if t.is_alive()]
        if not living:
            return True
        time.sleep(0.05)
    return False


def 日志尾巴(name: str, n: int = 3000) -> str:
    path = 任务路径(name)
    log = 日志(path)
    if not log.exists():
        return ""
    return log.read_text(encoding="utf-8")[-n:]


def _启动(path: Path) -> None:
    if 急停文件.exists():
        写日志(日志(path), "全员急停中，未启动线程。")
        return
    with _锁:
        if path.name in _线程 and _线程[path.name].is_alive():
            return
        th = threading.Thread(target=_跑任务, args=(path,), daemon=True, name=f"xj-{path.stem}")
        _线程[path.name] = th
        th.start()


def _跑任务(path: Path) -> None:
    try:
        meta = 解析工单(path)
        _等依赖(path, meta)
        while True:
            ctl = 读控制(控制(path))
            if _应停(path, ctl):
                return
            if int(ctl.get("步数", 0)) > _预算上限(meta, ctl):
                if _熔断(path, meta, ctl, "超过预算步数"):
                    continue
                return
            action = _mock动作(path, meta, ctl) if mock模式() else _模型动作(path, meta, ctl)
            done = _执行动作(path, meta, action)
            if done:
                return
            ctl = 读控制(控制(path))
            ctl["步数"] = int(ctl.get("步数", 0)) + 1
            写控制(控制(path), ctl)
    except _停止信号:
        return
    except Exception as exc:  # noqa: BLE001
        if 急停文件.exists() or 读控制(控制(path)).get("停止"):
            _暂停(path, "急停或停止中断，不熔断")
            return
        写日志(日志(path), f"引擎异常：{exc}")
        try:
            meta = 解析工单(path)
        except Exception:  # noqa: BLE001
            meta = {}
        _熔断(path, meta, 读控制(控制(path)), reason=f"引擎异常：{exc}")


def _等依赖(path: Path, meta: dict[str, Any]) -> None:
    deps = [x.strip() for x in str(meta.get("依赖", "")).split(",") if x.strip()]
    for dep in deps:
        写日志(日志(path), f"等待依赖：{dep}")
        while not _依赖已满足(dep, meta):
            if _应停(path, 读控制(控制(path))):
                raise _停止信号
            time.sleep(1)


def _预算上限(meta: dict[str, Any], ctl: dict[str, Any]) -> int:
    return int(meta.get("预算步数", 8) or 8) + int(ctl.get("追加预算", 0) or 0)


def _依赖已满足(dep: str, meta: dict[str, Any]) -> bool:
    direct = dep if dep.endswith(".md") else dep + ".md"
    if (工单 / "待验收" / dep).exists() or (工单 / "待验收" / direct).exists():
        return True
    source = str(meta.get("来源会议") or "")
    for col in ("待验收", "已完成"):
        for p in (工单 / col).glob("*.md"):
            if p.name.endswith(".日志.md"):
                continue
            try:
                other = 解析工单(p)
            except Exception:  # noqa: BLE001
                other = {}
            if source and str(other.get("来源会议") or "") != source:
                continue
            title = ""
            try:
                title = p.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").split("·", 1)[-1].strip()
            except Exception:  # noqa: BLE001
                pass
            if dep in {p.name, p.stem, title}:
                return True
    return False


def _模型动作(path: Path, meta: dict[str, Any], ctl: dict[str, Any]) -> dict[str, Any]:
    from 办公室 import 岗位上下文
    from 模型接入 import 调用

    role = str(meta.get("岗位", "首席工程师"))
    additions = ctl.get("追加指示", [])
    used = int(ctl.get("已消费指示", 0))
    fresh = "\n".join(x.get("内容", "") for x in additions[used:])
    observations = _观察文本(ctl)
    ctl["已消费指示"] = len(additions)
    写控制(控制(path), ctl)
    prompt = (
        岗位上下文(role)
        + "\n\n## 公司资料室（你可主动读取，不要猜）\n"
        + "当工单缺少事实或配置时，先用「读文件」查看相关资料，再决定下一步。常用资料：\n"
        + "- 花名册.yaml：岗位、模型、端点、key_env，只能引用岗位和模型，不要泄露密钥名以外的任何密钥内容。\n"
        + "- 班规.md：公司通用纪律。\n"
        + "- README.md：办公室/公司使用说明。\n"
        + "- 岗位/<岗位>/说明书.md、技能/通用/*.md、技能/岗位/<岗位>/*.md：岗位职责、公司通用技能和岗位专项技能。\n"
        + "如果资料不存在、没有权限、互相冲突，或任务需要白名单外写入，再「请示」。"
        + "\n\n## 你的工单\n"
        + path.read_text(encoding="utf-8")
        + "\n\n## 最近执行日志\n"
        + 日志尾巴(path.name, 3000)
        + "\n\n## 最近观察结果（这是你上一轮读文件/跑命令/核验得到的完整材料）\n"
        + (observations or "无")
        + "\n\n## 船主追加指示\n"
        + (fresh or "无")
        + "\n\n只输出一个JSON动作，不要解释。动作可选：读文件/核验岗位/写文件/执行命令/请示/申请技能/完成。"
        + "\n字段请优先用中文：动作、路径、岗位、内容、追加、命令、建议、总结、复盘。"
        + "\n收尾归系统：岗位记忆、复盘归档、DEVLOG 由引擎在你「完成」时自动落盘；你不要去读写岗位记忆/DEVLOG，也不要为写它们而请示——只把复盘内容放进完成动作即可。"
        + "\n完成动作必须带复盘对象：{\"做对\":\"...\",\"做错\":\"...\",\"下次\":\"...\",\"技能\":\"可选\"}。"
        + "\n如果你用了英文 action/path/content/message/append/command/summary，引擎也会识别。"
    )
    raw = 调用(role, prompt, 最大tokens=2048)
    try:
        return 提取_json(raw)
    except Exception:
        raw2 = 调用(role, prompt + "\n上次不是JSON。请只输出JSON动作。", 最大tokens=1024)
        return 提取_json(raw2)


def _mock动作(path: Path, meta: dict[str, Any], ctl: dict[str, Any]) -> dict[str, Any]:
    step = int(ctl.get("步数", 0))
    target = f"工具/mock产物/{path.stem}-{meta.get('岗位', 'role')}.txt"
    if step == 0:
        return {"动作": "读文件", "路径": "README.md"}
    if step == 1:
        return {"动作": "写文件", "路径": target, "内容": f"{path.name} step1\n"}
    if step == 2:
        return {"动作": "请示", "内容": f"{path.name} 需要船主批准继续mock执行", "建议": "批"}
    if step == 3:
        return {"动作": "写文件", "路径": target, "内容": f"{path.name} approved\n", "追加": True}
    return {
        "动作": "完成",
        "总结": f"{path.name} mock闭环完成",
        "复盘": {"做对": "按读写请示闭环执行", "做错": "mock未覆盖真实模型判断", "下次": "真模式先核验证据再完成"},
    }


def _执行动作(path: Path, meta: dict[str, Any], action: dict[str, Any]) -> bool:
    kind = _动作值(action)
    if kind == "读文件":
        p = 读路径(str(_字段(action, "路径", "path", "file", "target")))
        text = p.read_text(encoding="utf-8", errors="replace")[:8000]
        _记观察(path, "读文件", 显(p), text)
        写日志(日志(path), f"查看资料：{显(p)}（已把内容放入下一轮观察结果）")
        return False
    if kind == "写文件":
        p = 写路径(str(_字段(action, "路径", "path", "file", "target")), meta)
        content = str(_字段(action, "内容", "content", "text", default=""))
        append = bool(_字段(action, "追加", "append", "appendMode", default=False))
        old = p.read_text(encoding="utf-8") if p.exists() and append else ""
        原子写(p, old + content)
        mode = "追加" if append else "写入"
        写日志(日志(path), f"{mode}文件：{显(p)}（约{len(content)}字）")
        return False
    if kind == "核验岗位":
        roles = _核验岗位列表(action)
        result = _核验岗位(roles)
        _记观察(path, "核验岗位", "、".join(roles), "\n".join(result))
        写日志(日志(path), "核验岗位：\n" + "\n".join(result))
        return False
    if kind == "执行命令":
        cmd = str(_字段(action, "命令", "command", "cmd"))
        try:
            out = _执行命令(path, meta, cmd)
        except Exception as exc:  # noqa: BLE001
            out = f"命令未执行：{exc}（请改用允许的只读验收命令，或检查路径/格式；本步不计失败，请据此修正）"
            写日志(日志(path), f"命令未执行：{exc}")
        _记观察(path, "执行命令", cmd, out)
        return False
    if kind == "申请技能":
        content = str(_字段(action, "内容", "message", "content", "question", default=""))
        suggestion = str(_字段(action, "建议", "suggestion", "advice", default=""))
        _协同事件(path, "技能申请", str(meta.get("岗位", "")), content, 目标="项目经理")
        ok = _请示(path, meta, "技能申请：" + content, suggestion or "请项目经理评估是否允许联网查找/安装相关skill")
        if not ok:
            _暂停(path, "技能申请未获批准")
        return False
    if kind == "请示":
        content = str(_字段(action, "内容", "message", "content", "question", default=""))
        suggestion = str(_字段(action, "建议", "suggestion", "advice", default=""))
        _协同事件(path, "请示", str(meta.get("岗位", "")), content, 目标="项目经理")
        ok = _请示(path, meta, content, suggestion)
        if not ok:
            _暂停(path, "请示未获批准")
        return False
    if kind == "完成":
        summary = str(_字段(action, "总结", "summary", "message", default=""))
        reflection = _复盘(action, summary)
        if not reflection:
            写日志(日志(path), "完成动作缺复盘，已暂停：必须写明做对/做错/下次。")
            _暂停(path, "完成缺少复盘")
            return False
        _完成(path, meta, summary, reflection)
        return True
    写日志(日志(path), f"动作未识别，已暂停等待修正：{_动作简述(action)}")
    _暂停(path, "动作格式未识别")
    return False


def _字段(action: dict[str, Any], *names: str, default: Any = "") -> Any:
    for name in names:
        if name in action:
            return action.get(name)
    return default


def _动作值(action: dict[str, Any]) -> str:
    raw = str(_字段(action, "动作", "action", "type", default="")).strip()
    aliases = {
        "read": "读文件",
        "read_file": "读文件",
        "readFile": "读文件",
        "write": "写文件",
        "write_file": "写文件",
        "writeFile": "写文件",
        "verify_role": "核验岗位",
        "verify_roles": "核验岗位",
        "rollcall": "核验岗位",
        "check_model": "核验岗位",
        "run": "执行命令",
        "run_command": "执行命令",
        "command": "执行命令",
        "ask": "请示",
        "question": "请示",
        "request_approval": "请示",
        "skill_request": "申请技能",
        "request_skill": "申请技能",
        "install_skill": "申请技能",
        "search_skill": "申请技能",
        "complete": "完成",
        "finish": "完成",
        "done": "完成",
    }
    return aliases.get(raw, raw)


def _动作简述(action: dict[str, Any]) -> str:
    kind = _字段(action, "动作", "action", "type", default="未写动作")
    target = _字段(action, "路径", "path", "file", "target", "命令", "command", default="")
    msg = _字段(action, "内容", "message", "content", "summary", default="")
    parts = [f"动作={kind}"]
    if target:
        parts.append(f"目标={target}")
    if msg:
        parts.append(f"说明={str(msg)[:160]}")
    return "；".join(parts)


def _核验岗位列表(action: dict[str, Any]) -> list[str]:
    raw = _字段(action, "岗位", "role", "roles", "target", default="")
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw or "").strip()
    if not text or text in {"全部", "所有", "所有人", "all", "*"}:
        from 模型接入 import 花名册

        return list(花名册().keys())
    return [x.strip() for x in re.split(r"[,，、\s]+", text) if x.strip()]


def _核验岗位(roles: list[str]) -> list[str]:
    from 模型接入 import 调用带元, 花名册

    roster = 花名册()
    out: list[str] = []
    for role in roles:
        expected = roster.get(role, {}).get("model", "未登记")
        if role not in roster:
            out.append(f"✗ {role}: 花名册无此岗位")
            continue
        try:
            reply, actual = 调用带元(role, "只回复两个字：到岗", 最大tokens=16)
            mark = "✓" if expected.lower() in actual.lower() or actual.lower() in expected.lower() else "⚠"
            out.append(f"{mark} {role}: 登记={expected}；实际model={actual}；回复={reply.strip()[:20]}")
        except Exception as exc:  # noqa: BLE001
            out.append(f"✗ {role}: 登记={expected}；核验失败={exc}")
    return out


def _请示(path: Path, meta: dict[str, Any], 内容: str, 建议: str) -> bool:
    role = str(meta.get("岗位", ""))
    cid = 审批.创建请示(path.name, role, 内容, 建议)
    result = 审批.经理预审(cid, mock=mock模式())
    写日志(日志(path), f"发起请示：{cid}，经理裁决：{result.get('经理裁决')}")
    if result.get("状态") == "批":
        _协同事件(
            path,
            "项目经理裁决",
            "项目经理",
            f"批：{result.get('经理理由', '')}",
            目标=str(meta.get("岗位", "")),
        )
        return True
    if result.get("状态") == "驳":
        _协同事件(
            path,
            "项目经理裁决",
            "项目经理",
            f"驳：{result.get('经理理由', '')}",
            目标=str(meta.get("岗位", "")),
        )
        return False
    deadline = time.time() + 30 * 60
    while time.time() < deadline:
        if _应停(path, 读控制(控制(path))):
            return False
        decided = 审批.读取裁决(cid)
        if decided:
            写日志(日志(path), f"船主裁决：{decided.get('状态')} {decided.get('船主理由', '')}")
            _协同事件(
                path,
                "船主裁决",
                "船主",
                f"{decided.get('状态')}：{decided.get('船主理由', '')}",
                目标=role,
            )
            return decided.get("状态") == "批"
        time.sleep(0.2 if mock模式() else 2)
    _暂停(path, "请示超过30分钟未裁决")
    return False


def _执行命令(path: Path, meta: dict[str, Any], cmd: str) -> str:
    if _是验收命令(cmd):
        out = _执行验收命令(meta, cmd)
        写日志(日志(path), f"执行验收命令：{cmd}\n{out}")
        return out
    args = shlex.split(cmd)
    ok = (
        args[:3] == ["python3", "-m", "pytest"]
        or args[:2] == ["python3", "-c"]
        or args[:2] == ["make", "check"]
    )
    if not ok:
        raise PermissionError(f"命令前缀不允许: {cmd}")
    if not PRODUCT.exists():
        raise FileNotFoundError(f"产品仓不存在: {PRODUCT}")
    run = subprocess.run(args, cwd=PRODUCT, text=True, capture_output=True, timeout=120)
    out = (run.stdout + "\n" + run.stderr)[-4000:]
    写日志(日志(path), f"执行命令：{cmd}\n退出码：{run.returncode}\n{out}")
    return f"退出码：{run.returncode}\n{out}"


def _是验收命令(cmd: str) -> bool:
    parts = [x.strip() for x in str(cmd or "").split("&&") if x.strip()]
    if not parts:
        return False
    allowed = {"test", "grep", "echo"}
    try:
        return all(shlex.split(part)[0] in allowed for part in parts)
    except Exception:
        return False


def _执行验收命令(meta: dict[str, Any], cmd: str) -> str:
    """只执行工单常用的只读验收链：test -f / grep / echo，不开 shell。"""
    lines: list[str] = []
    code = 0
    for part in [x.strip() for x in cmd.split("&&") if x.strip()]:
        args = shlex.split(part)
        if not args:
            continue
        if args[0] == "test" and len(args) == 3 and args[1] == "-f":
            p = 写路径(args[2], meta)
            if not p.is_file():
                lines.append(f"fail test -f {显(p)}：文件不存在")
                code = 1
                break
            lines.append(f"ok test -f {显(p)}")
            continue
        if args[0] == "grep" and len(args) >= 3:
            opts = [x for x in args[1:] if x.startswith("-")]
            rest = [x for x in args[1:] if not x.startswith("-")]
            if not rest or any(o not in {"-q", "-a", "-aq", "-qa"} for o in opts):
                raise PermissionError(f"验收命令grep参数不允许: {part}")
            if len(rest) != 2:
                raise PermissionError(f"验收命令grep格式不允许: {part}")
            pattern, file_name = rest
            p = 写路径(file_name, meta)
            if not p.is_file():
                lines.append(f"fail grep {pattern} {显(p)}：文件不存在")
                code = 1
                break
            text = p.read_text(encoding="utf-8", errors="replace")
            try:
                hit = re.search(pattern, text) is not None
            except re.error:
                hit = pattern in text
            if not hit:
                lines.append(f"fail grep {pattern} {显(p)}：未匹配")
                code = 1
                break
            lines.append(f"ok grep {pattern} {显(p)}")
            continue
        if args[0] == "echo":
            lines.append(" ".join(args[1:]))
            continue
        raise PermissionError(f"验收命令片段不允许: {part}")
    return (f"退出码：{code}\n" + "\n".join(lines))[-4000:]


def _观察文本(ctl: dict[str, Any]) -> str:
    obs = ctl.get("观察", [])
    if not isinstance(obs, list) or not obs:
        return ""
    chunks: list[str] = []
    for item in obs[-6:]:
        if not isinstance(item, dict):
            continue
        chunks.append(
            f"[{item.get('时间', '')}] {item.get('动作', '观察')}：{item.get('目标', '')}\n"
            + str(item.get("内容", ""))[:8000]
        )
    return "\n\n---\n\n".join(chunks)[-12000:]


def _记观察(path: Path, 动作: str, 目标: str, 内容: str) -> None:
    ctlp = 控制(path)
    ctl = 读控制(ctlp)
    obs = ctl.get("观察", [])
    if not isinstance(obs, list):
        obs = []
    obs.append({"时间": 时刻(), "动作": 动作, "目标": 目标, "内容": 内容[:8000]})
    ctl["观察"] = obs[-8:]
    写控制(ctlp, ctl)


def _复盘(action: dict[str, Any], summary: str) -> str:
    raw = _字段(action, "复盘", "reflection", "lesson", default="")
    if isinstance(raw, dict):
        ok = str(raw.get("做对") or raw.get("right") or "").strip()
        bad = str(raw.get("做错") or raw.get("wrong") or "").strip()
        nxt = str(raw.get("下次") or raw.get("next") or "").strip()
        skill = str(raw.get("技能") or raw.get("skill") or "").strip()
        if ok and bad and nxt:
            tail = f" skill:{skill}" if skill else ""
            return f"我做对了：{ok}；我没做好：{bad}；下次我会：{nxt}{tail}"
    text = str(raw or "").strip()
    if text and ("下次" in text or "next" in text.lower()):
        return text[:700]
    if summary and all(x in summary for x in ("对:", "错:", "下次:")):
        return summary[:700]
    return ""


def _完成(path: Path, meta: dict[str, Any], summary: str, reflection: str) -> None:
    写日志(日志(path), f"完成：{summary}")
    写日志(日志(path), f"岗位复盘：{reflection}")
    _写正式报告(path, "完工报告", "员工声明完成，进入待验收。", "已完成", summary or "已提交完成", "等待测试/船主验收。")
    _协同事件(
        path,
        "交付报告",
        str(meta.get("岗位", "")),
        f"完成声明：{summary}\n复盘：{reflection}",
        目标="测试/船主",
    )
    if mock模式():
        写日志(日志(path), "mock复盘：已触发岗位记忆追加流程。")
    else:
        追加记忆(str(meta.get("岗位", "")), f"做完「{path.stem}」：{reflection}")
    dst = 工单 / "待验收" / path.name
    移动伴随文件(path, dst)


def _熔断(path: Path, meta: dict[str, Any] | None = None, ctl: dict[str, Any] | None = None, reason: str = "引擎熔断") -> bool:
    """预算/异常熔断。项目经理能判断的低风险续步应在这里闭环，不默认打扰船主。"""
    meta = meta or {}
    ctl = ctl or 读控制(控制(path))
    if 急停文件.exists():
        _暂停(path, "全员急停（熔断让位于急停，不续步）")
        return False
    写日志(日志(path), f"熔断：{reason}")
    content = _熔断报告(path, meta, ctl, reason)
    _写正式报告(path, "熔断报告", reason, "提交项目经理预审", "请求项目经理判断是否可低风险续步。", "等待项目经理裁决。")
    _协同事件(
        path,
        "异常报告",
        str(meta.get("岗位", "")) or "引擎",
        content,
        目标="项目经理",
    )
    cid = 审批.创建请示(path.name, "引擎", content, "项目经理先裁决：低风险收尾请批追加2步；触发红线再上报船主。")
    result = 审批.经理预审(cid, mock=mock模式())
    写日志(日志(path), f"项目经理熔断预审：{result.get('经理裁决')}｜{result.get('经理理由', '')}")
    if result.get("状态") == "批":
        extra = int(result.get("追加步数", 2) or 2)
        fresh = 读控制(控制(path))
        fresh["状态"] = "运行中"
        fresh["停止"] = False
        fresh["追加预算"] = int(fresh.get("追加预算", 0) or 0) + max(1, extra)
        写控制(控制(path), fresh)
        report = result.get("经理报告") or {}
        boundary = report.get("边界", "保持原工单边界")
        写日志(日志(path), f"项目经理批准自动续步：追加{max(1, extra)}步；边界：{boundary}")
        _写正式报告(path, "经理裁决报告", reason, "已批准续步", str(result.get("经理理由", "")), f"追加{max(1, extra)}步；边界：{boundary}")
        _协同事件(
            path,
            "项目经理裁决",
            "项目经理",
            f"批：追加{max(1, extra)}步；理由：{result.get('经理理由', '')}；边界：{boundary}",
            目标=str(meta.get("岗位", "")) or "引擎",
        )
        return True
    _写正式报告(path, "回炉报告", reason, "进入待办", str(result.get("经理理由", "项目经理未批准自动续步")), "等待船主/掌舵处理回炉申请。")
    _协同事件(
        path,
        "回炉报告",
        "项目经理",
        f"未自动续步：{result.get('经理理由', '项目经理未批准自动续步')}",
        目标="船主/舟",
    )
    dst = 工单 / "待办" / path.name
    if path.exists():
        移动伴随文件(path, dst)
    return False


def _熔断报告(path: Path, meta: dict[str, Any], ctl: dict[str, Any], reason: str) -> str:
    limit = _预算上限(meta, ctl)
    used = int(ctl.get("步数", 0) or 0)
    obs = ctl.get("观察", [])
    targets = []
    if isinstance(obs, list):
        for item in obs[-5:]:
            if isinstance(item, dict):
                targets.append(f"{item.get('动作', '观察')}:{item.get('目标', '')}")
    tail = 日志尾巴(path.name, 1800).strip()
    task = ""
    try:
        task = path.read_text(encoding="utf-8").split("## 任务", 1)[-1].split("## ", 1)[0].strip()
    except Exception:  # noqa: BLE001
        pass
    return (
        f"预算熔断：{reason}\n"
        f"工单目标：{task[:500] or path.name}\n"
        f"岗位：{meta.get('岗位', '')}\n"
        f"预算与进度：已用{used}步 / 当前上限{limit}步（含追加预算{ctl.get('追加预算', 0) or 0}）。\n"
        f"白名单：{meta.get('白名单', '') or '未写'}。\n"
        f"最近动作：{'; '.join(targets) or '无观察记录'}。\n"
        f"最近日志：\n{tail or '无'}\n"
        "项目经理判断要求：先核对是否仍在原白名单、是否只是读回/自检/复盘/交付收尾、是否新增费用或权限。"
        "若低风险，直接批准追加2步并要求继续完成；若触发红线，写清证据后上报船主。"
    )


def _暂停(path: Path, reason: str) -> None:
    ctl = 读控制(控制(path))
    ctl["状态"] = f"暂停：{reason}"
    写控制(控制(path), ctl)
    _写正式报告(path, "暂停报告", reason, "已暂停", "执行被停止或规则阻断。", "等待追加指示、回炉或人工裁决。")
    写日志(日志(path), f"暂停：{reason}")
    try:
        meta = 解析工单(path)
    except Exception:  # noqa: BLE001
        meta = {}
    _协同事件(path, "暂停报告", str(meta.get("岗位", "")) or "引擎", reason, 目标="项目经理")


def _写正式报告(path: Path, 类别: str, 原因: str, 阶段: str, 判断: str, 下一步: str) -> None:
    ctlp = 控制(path)
    ctl = 读控制(ctlp)
    reports = ctl.get("正式报告", [])
    if not isinstance(reports, list):
        reports = []
    report = {
        "时间": 时刻(),
        "类别": 类别,
        "原因": 原因,
        "阶段": 阶段,
        "判断": 判断,
        "下一步": 下一步,
    }
    reports.append(report)
    ctl["正式报告"] = reports[-8:]
    ctl["最后报告"] = report
    写控制(ctlp, ctl)
    写日志(日志(path), f"正式报告：{类别}｜{阶段}｜{原因}｜下一步：{下一步}")


def _应停(path: Path, ctl: dict[str, Any]) -> bool:
    if 急停文件.exists():
        _暂停(path, "全员急停")
        return True
    if ctl.get("停止"):
        _暂停(path, "船主停止")
        return True
    return False
