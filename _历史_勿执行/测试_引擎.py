#!/usr/bin/env python3
"""办公室v3 mock验收测试。用pytest运行，不依赖第三方测试辅助库。"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from collections.abc import Callable
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "工具"))


def 工单文件(col: str) -> list[Path]:
    return [p for p in (ROOT / "工单" / col).glob("*.md") if not p.name.endswith(".日志.md")]


def 清理(path: Path) -> None:
    for _ in range(20):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
            return
        except OSError:
            time.sleep(0.05)


@contextlib.contextmanager
def 沙箱() -> Iterator[None]:
    backup = Path(tempfile.mkdtemp(prefix="xj-office-v3-"))
    targets = ["工单", "会议", "协同", "请示", "信箱", "项目记忆", "工具/mock产物", "公司急停.flag"]
    for t in targets:
        src = ROOT / t
        dst = backup / t
        if src.is_dir():
            shutil.copytree(src, dst)
        elif src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    try:
        for t in targets:
            src = ROOT / t
            if src.is_dir():
                shutil.rmtree(src)
            elif src.exists():
                src.unlink()
        os.environ["XJ_MOCK"] = "1"
        yield
    finally:
        (ROOT / "公司急停.flag").write_text("test cleanup", encoding="utf-8")
        try:
            import 引擎

            引擎.等待空闲(2.0)
        except Exception:
            time.sleep(0.5)
        for t in targets:
            src = ROOT / t
            清理(src)
        for t in targets:
            src = backup / t
            dst = ROOT / t
            if src.is_dir():
                shutil.copytree(src, dst)
            elif src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        shutil.rmtree(backup)


def 等待(pred: Callable[[], Any], 秒: float = 5.0) -> Any:
    end = time.time() + 秒
    last = None
    while time.time() < end:
        last = pred()
        if last:
            return last
        time.sleep(0.05)
    return last


def test_kickoff_confirm_approval_parallel() -> None:
    with 沙箱():
        import 审批
        import 引擎

        result = 引擎.发起任务("mock验证办公室v3闭环")
        assert len(result["工单"]) == 2
        assert len(工单文件("待确认")) == 2
        started = 引擎.确认开工()
        assert len(started) == 2
        cards = 等待(lambda: 审批.列表()["待船主"] if len(审批.列表()["待船主"]) == 2 else [], 秒=3)
        assert len(cards) == 2
        for card in cards:
            审批.船主裁决(card["id"], "批", "测试批准")
        done = 等待(
            lambda: 工单文件("待验收")
            if len(工单文件("待验收")) == 2
            else [],
            秒=5,
        )
        assert len(done) == 2
        logs = [p.with_name(p.stem + ".日志.md").read_text(encoding="utf-8") for p in done]
        assert all("船主裁决：批" in x for x in logs)
        assert sum("确认开工" in x for x in logs) == 2


def test_stop_emergency_and_whitelist() -> None:
    with 沙箱():
        import 引擎
        from 引擎工具 import 写路径

        引擎.发起任务("mock停止验证")
        name = 引擎.确认开工()[0]
        assert 引擎.停止任务(name) in {"已请求停止", "任务已结束或不存在"}
        assert 引擎.设置急停(True) == "全员急停已开启"
        assert 引擎.状态()["急停"] is True
        assert 引擎.设置急停(False) == "全员急停已解除"
        meta = {"白名单列表": ["工具/mock产物/**"]}
        try:
            写路径("../README.md", meta)
        except PermissionError:
            pass
        else:
            raise AssertionError("越界写入未被拦截")


def test_resume_backlog_task_clears_engine_card() -> None:
    with 沙箱():
        import 审批
        import 引擎
        from 引擎工具 import 初始控制, 控制, 写控制, 写日志, 日志

        name = "RESUME-1.md"
        p = ROOT / "工单" / "待办" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            """# v3工单 · 回炉恢复测试

| | |
|---|---|
| 来源会议 | TEST.md |
| 岗位 | 首席工程师 |
| 状态 | 待办 |
| 依赖 |  |
| 预算步数 | 1 |
| 白名单 | 工具/mock产物/** |
| 验收命令 |  |

## 任务
验证回炉工单可以追加预算继续。
""",
            encoding="utf-8",
        )
        ctl = 初始控制()
        ctl["步数"] = 1
        写控制(控制(p), ctl)
        写日志(日志(p), "熔断：超过预算步数")
        cid = 审批.创建请示(name, "引擎", "超过预算步数", "请船主处理")
        审批.经理预审(cid, mock=True)
        old_start = 引擎._启动
        引擎._启动 = lambda _path: None
        try:
            reply = 引擎.继续任务(name, 3)
        finally:
            引擎._启动 = old_start
        assert "已继续" in reply
        moved = ROOT / "工单" / "进行中" / name
        assert moved.exists()
        new_ctl = json.loads(控制(moved).read_text(encoding="utf-8"))
        assert new_ctl["追加预算"] == 3
        assert not 审批.列表()["待船主"]
        assert 审批.列表()["已决"][0]["状态"] == "批"


def test_pm_auto_approves_low_risk_budget_overrun() -> None:
    with 沙箱():
        import 引擎
        from 引擎工具 import 初始控制, 控制, 写控制, 写日志, 日志

        name = "BUDGET-1.md"
        p = ROOT / "工单" / "进行中" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            """# v3工单 · 低风险收尾

| | |
|---|---|
| 来源会议 | TEST.md |
| 岗位 | 文案工程师（兼职） |
| 状态 | 进行中 |
| 依赖 |  |
| 预算步数 | 1 |
| 预算依据 | 故意给低预算测试自动续步 |
| 白名单 | 工具/mock产物/** |
| 验收命令 |  |

## 任务
验证预算超限但仍在原白名单内时，由项目经理自动批准续步。
""",
            encoding="utf-8",
        )
        ctl = 初始控制()
        ctl["步数"] = 2
        ctl["观察"] = [{"时间": "now", "动作": "读文件", "目标": "README.md", "内容": "x"}]
        写控制(控制(p), ctl)
        写日志(日志(p), "查看资料：README.md（已把内容放入下一轮观察结果）")
        ok = 引擎._熔断(p, 引擎.解析工单(p), ctl, "超过预算步数")
        assert ok is True
        new_ctl = json.loads(控制(p).read_text(encoding="utf-8"))
        assert new_ctl["追加预算"] >= 2
        assert new_ctl["正式报告"][-1]["类别"] == "经理裁决报告"
        log = 日志(p).read_text(encoding="utf-8")
        assert "项目经理批准自动续步" in log
        assert "正式报告：" in log
        assert not (ROOT / "工单" / "待办" / name).exists()


def test_safe_validation_command_runner() -> None:
    with 沙箱():
        import 引擎
        from 引擎工具 import 控制, 初始控制, 写控制

        name = "VALIDATE-1.md"
        p = ROOT / "工单" / "进行中" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            """# v3工单 · 验收命令测试

| | |
|---|---|
| 来源会议 | TEST.md |
| 岗位 | 测试工程师 |
| 状态 | 进行中 |
| 依赖 |  |
| 预算步数 | 2 |
| 白名单 | 工具/mock产物/** |
| 验收命令 | test -f 工具/mock产物/验收.txt && grep -q '项目经理' 工具/mock产物/验收.txt |

## 任务
验证只读验收命令可以由引擎安全执行。
""",
            encoding="utf-8",
        )
        写控制(控制(p), 初始控制())
        target = ROOT / "工具" / "mock产物" / "验收.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("项目经理 已写入\n", encoding="utf-8")
        meta = 引擎.解析工单(p)
        out = 引擎._执行命令(p, meta, "test -f 工具/mock产物/验收.txt && grep -q '项目经理' 工具/mock产物/验收.txt && echo '验收通过'")
        assert "ok test -f" in out
        assert "ok grep 项目经理" in out
        assert "验收通过" in out


def test_emergency_stop_overrides_breaker() -> None:
    """A·急停一票否决：急停期间熔断不得自动续步。"""
    with 沙箱():
        import 引擎
        from 引擎工具 import 初始控制, 控制, 写控制

        name = "ESTOP-1.md"
        p = ROOT / "工单" / "进行中" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            """# v3工单 · 急停否决熔断

| | |
|---|---|
| 来源会议 | TEST.md |
| 岗位 | 工程师 |
| 状态 | 进行中 |
| 依赖 |  |
| 预算步数 | 1 |
| 白名单 | 工具/mock产物/** |
| 验收命令 |  |

## 任务
验证急停期间熔断不得自动续步。
""",
            encoding="utf-8",
        )
        ctl = 初始控制()
        ctl["步数"] = 5
        写控制(控制(p), ctl)
        引擎.设置急停(True)
        try:
            ok = 引擎._熔断(p, 引擎.解析工单(p), ctl, "超过预算步数")
        finally:
            引擎.设置急停(False)
        assert ok is False
        new_ctl = json.loads(控制(p).read_text(encoding="utf-8"))
        assert str(new_ctl.get("状态", "")).startswith("暂停")
        assert int(new_ctl.get("追加预算", 0) or 0) == 0


def test_validation_command_missing_file_no_crash() -> None:
    """C·验收命令永不崩：grep 不存在文件时优雅返回不通过，不抛异常。"""
    with 沙箱():
        import 引擎
        from 引擎工具 import 初始控制, 控制, 写控制

        name = "VALIDATE-MISS.md"
        p = ROOT / "工单" / "进行中" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            """# v3工单 · 验收缺文件不崩

| | |
|---|---|
| 来源会议 | TEST.md |
| 岗位 | 测试工程师 |
| 状态 | 进行中 |
| 依赖 |  |
| 预算步数 | 2 |
| 白名单 | 工具/mock产物/** |
| 验收命令 |  |

## 任务
验证 grep 不存在文件时优雅返回不通过、不抛异常。
""",
            encoding="utf-8",
        )
        写控制(控制(p), 初始控制())
        meta = 引擎.解析工单(p)
        out = 引擎._执行命令(p, meta, "grep -q '项目经理' 工具/mock产物/不存在.txt")
        assert "fail" in out
        assert "文件不存在" in out


def test_board_exposes_formal_reports() -> None:
    with 沙箱():
        import 引擎
        import 看板数据
        from 引擎工具 import 初始控制, 控制, 写控制

        name = "REPORT-1.md"
        p = ROOT / "工单" / "进行中" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            """# v3工单 · 报告展示测试

| | |
|---|---|
| 来源会议 | TEST.md |
| 岗位 | 工程师 |
| 状态 | 进行中 |
| 依赖 |  |
| 预算步数 | 2 |
| 白名单 | 工具/mock产物/** |
| 验收命令 |  |

## 任务
验证暂停或熔断后，项目室能看到正式报告。
""",
            encoding="utf-8",
        )
        写控制(控制(p), 初始控制())
        引擎._暂停(p, "规则阻断")
        detail = 看板数据.项目详情("TEST.md")
        reports = detail["泳道"][0]["过程"]["正式报告"]
        assert reports[-1]["类别"] == "暂停报告"
        assert reports[-1]["下一步"]


def test_collaboration_meeting_and_owner_participation() -> None:
    with 沙箱():
        import 协同
        import 引擎
        import 看板数据

        result = 引擎.发起任务("mock验证会议协同可旁听可参会")
        room = result["会议"]
        detail = 看板数据.项目详情(room)
        meetings = detail["会议协同"]
        assert meetings
        project_meeting = meetings[0]
        assert project_meeting["类型"] == "项目会"
        assert any(ev["类型"] == "方案" for ev in project_meeting["事件"])
        assert any(ev["类型"] == "决议" for ev in project_meeting["事件"])
        assert all("会议依据" in p.read_text(encoding="utf-8") for p in 工单文件("待确认"))
        started = 引擎.确认开工([result["工单"][0]])
        assert started
        detail = 看板数据.项目详情(room)
        meetings = detail["会议协同"]
        assert meetings
        kickoff = [m for m in meetings if m["类型"] == "开工交办"][0]
        assert any(ev["类型"] == "开工确认" for ev in kickoff["事件"])
        assert any(ev["类型"] == "交办" for ev in kickoff["事件"])
        assert any(
            ev["类型"] == "受领回执"
            for meeting in detail["会议协同"]
            for ev in meeting["事件"]
        )
        more = 引擎.确认开工([result["工单"][1]])
        assert more
        detail = 看板数据.项目详情(room)
        assert sum(
            ev["类型"] == "交办"
            for meeting in detail["会议协同"]
            for ev in meeting["事件"]
        ) == 2
        协同.船主发言(project_meeting["id"], "我在会上旁听：继续按边界推进。")
        detail = 看板数据.项目详情(room)
        assert any(
            ev["类型"] == "船主发言" and "旁听" in ev["内容"]
            for meeting in detail["会议协同"]
            for ev in meeting["事件"]
        )
        hall = 看板数据.大厅(["项目经理", "首席工程师", "工程师", "测试工程师", "文案工程师（兼职）"])
        assert any(ev["k"] == "会" and "船主发言" in ev["text"] for ev in hall["事件"])


def test_real_kickoff_uses_multi_role_meeting_discussion() -> None:
    with 沙箱():
        import 会议
        import 模型接入

        calls: list[str] = []
        original_call = 模型接入.调用

        def fake_call(role: str, prompt: str, 最大tokens: int = 4096) -> str:
            calls.append(role)
            if role == "项目经理" and "够不够" in prompt:
                return json.dumps({"缺证据": [], "够了": True, "理由": "测试：一轮即收敛"}, ensure_ascii=False)
            if role == "项目经理" and "会前准备" in prompt:
                return json.dumps({
                    "标题": "真实协作测试",
                    "议题": "新建并核查公告",
                    "覆盖说明": "先讨论资料、执行和验收，再落工单。",
                    "主持说明": "请文案说明写法，测试说明验收标准。",
                    "参会": ["文案工程师（兼职）", "测试工程师"],
                    "讨论问题": ["公告内容怎么写", "如何验收"],
                    "资料需求": ["花名册"],
                    "验收关注": ["三段齐全"],
                    "风险关注": ["不能碰其他文件"],
                }, ensure_ascii=False)
            if role == "文案工程师（兼职）":
                return json.dumps({
                    "发言人": "文案工程师（兼职）",
                    "类型": "方案",
                    "内容": "我建议只写公司公告.md，按成立日期、岗位模型、协作署名三段组织，并读回核对。",
                    "资料需求": ["花名册"],
                    "验收建议": "读回三段",
                    "风险": "不要扩大到其他文档",
                    "是否适合承办": "适合",
                    "反对或补充": "无",
                }, ensure_ascii=False)
            if role == "测试工程师":
                return json.dumps({
                    "发言人": "测试工程师",
                    "类型": "验收",
                    "内容": "我要求验收命令证明文件存在、日期存在、五个岗位都存在，并追加测试结果。",
                    "资料需求": [],
                    "验收建议": "grep 日期与岗位",
                    "风险": "不能只看生成文件名",
                    "是否适合承办": "适合验收",
                    "反对或补充": "测试应依赖文案交付",
                }, ensure_ascii=False)
            if role == "项目经理":
                return json.dumps({
                    "标题": "真实协作测试",
                    "覆盖说明": "采纳文案写作方案和测试验收标准。",
                    "项目经理结论": "先由文案写公告，再由测试读回验收。",
                    "决议": [
                        {"编号": "D1", "内容": "公告只写公司公告.md三段内容。", "落实": "文案工单"},
                        {"编号": "D2", "内容": "验收必须读回文件并证明关键字段。", "落实": "测试工单"},
                    ],
                    "验收标准": ["公司公告.md存在", "日期与五岗齐全", "测试结果已追加"],
                    "资料需求": ["花名册"],
                    "子工单": [
                        {
                            "标题": "编写公告",
                            "岗位": "文案工程师（兼职）",
                            "任务": "写公司公告.md三段内容。",
                            "白名单": ["公司公告.md"],
                            "依赖": [],
                            "预算步数": 5,
                            "预算依据": "查资料、写入、读回、自检、交付。",
                            "预计字数": "300-600",
                            "预计token": "3000-6000",
                            "验收命令": "",
                            "会议依据": "D1",
                        },
                        {
                            "标题": "核查公告",
                            "岗位": "测试工程师",
                            "任务": "读回公司公告.md并追加测试结果。",
                            "白名单": ["公司公告.md"],
                            "依赖": ["V3-001-1_编写公告.md"],
                            "预算步数": 4,
                            "预算依据": "读文件、核对、追加结果、复盘。",
                            "预计字数": "100-300",
                            "预计token": "2000-5000",
                            "验收命令": "",
                            "会议依据": "D2",
                        },
                    ],
                }, ensure_ascii=False)
            raise AssertionError(role)

        模型接入.调用 = fake_call
        try:
            result = 会议.kickoff("真实多岗位项目会测试", mock=False)
        finally:
            模型接入.调用 = original_call

        assert calls.count("项目经理") >= 2
        assert "文案工程师（兼职）" in calls
        assert "测试工程师" in calls
        detail = (ROOT / "会议" / result["会议"]).read_text(encoding="utf-8")
        assert "文案工程师（兼职）｜方案" in detail
        assert "测试工程师｜验收" in detail
        assert all("会议依据" in p.read_text(encoding="utf-8") for p in 工单文件("待确认"))


def test_board_merges_backlog_and_engine_approval() -> None:
    with 沙箱():
        import 审批
        import 看板数据
        from 引擎工具 import 初始控制, 控制, 写控制, 写日志, 日志

        name = "MERGE-1.md"
        p = ROOT / "工单" / "待办" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            """# v3工单 · 合并右栏卡

| | |
|---|---|
| 来源会议 | TEST.md |
| 岗位 | 文案工程师（兼职） |
| 状态 | 待办 |
| 依赖 |  |
| 预算步数 | 1 |
| 白名单 | 公司公告.md |
| 验收命令 |  |

## 任务
验证回炉工单和同源引擎请示只出现一张右栏卡。
""",
            encoding="utf-8",
        )
        ctl = 初始控制()
        ctl["步数"] = 2
        写控制(控制(p), ctl)
        写日志(日志(p), "写入文件：公司公告.md（约10字）")
        写日志(日志(p), "熔断：超过预算步数")
        cid = 审批.创建请示(name, "引擎", "超过预算步数", "请船主处理")
        审批.经理预审(cid, mock=True)
        summary = 看板数据.待办汇总()
        assert len(summary["待办"]) == 1
        assert summary["待办"][0]["请示"]["任务"] == name
        assert summary["待办"][0]["产物"][0]["状态"] == "草稿/未验收"
        assert summary["待裁决"] == []


def test_room_bound_memory_cross_context() -> None:
    with 沙箱():
        import 办公室
        import 看板数据
        import 模型接入

        room = "ROOM-MEMORY.md"
        seen_prompts: list[str] = []
        original_call = 模型接入.调用
        old_mock = os.environ.get("XJ_MOCK")
        os.environ.pop("XJ_MOCK", None)

        def fake_call(role: str, prompt: str, 最大tokens: int = 4096) -> str:
            seen_prompts.append(prompt)
            return f"已读取项目记忆并回应：{role}"

        模型接入.调用 = fake_call
        try:
            first = 办公室.对话("项目经理", "记住：本项目优先保护公司利益。", room=room)
            second = 办公室.对话("测试工程师", "请根据项目记忆说出重点。", room=room)
        finally:
            模型接入.调用 = original_call
            if old_mock is not None:
                os.environ["XJ_MOCK"] = old_mock
            else:
                os.environ.pop("XJ_MOCK", None)

        assert "项目经理" in first
        assert "测试工程师" in second
        assert len(seen_prompts) == 2
        assert "本项目优先保护公司利益" in seen_prompts[-1]
        detail = 看板数据.项目详情(room)
        assert any("保护公司利益" in item["text"] for item in detail["项目记忆"])


def test_legacy_http_endpoints_200() -> None:
    with 沙箱():
        import threading
        import 办公室

        srv = 办公室.http.server.ThreadingHTTPServer(("127.0.0.1", 0), 办公室.H)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        base = f"http://127.0.0.1:{srv.server_port}"
        try:
            assert urllib.request.urlopen(base + "/", timeout=3).status == 200
            body = json.dumps({"role": "项目经理", "text": "到岗"}).encode()
            req = urllib.request.Request(base + "/chat", data=body, headers={"content-type": "application/json"}, method="POST")
            assert urllib.request.urlopen(req, timeout=3).status == 200
            mid = "HTTP-ROOM.md"
            import 协同

            meeting_id = 协同.创建会议(mid, "测试会", "HTTP测试会", "项目经理", ["测试工程师"], "验证会议接口")
            body = json.dumps({"id": meeting_id, "text": "船主参会测试"}).encode()
            req = urllib.request.Request(base + "/meeting_say", data=body, headers={"content-type": "application/json"}, method="POST")
            assert urllib.request.urlopen(req, timeout=3).status == 200
            data = json.loads(urllib.request.urlopen(base + "/meetings?room=" + mid, timeout=3).read().decode())
            assert data["会议"][0]["事件"][-1]["类型"] == "船主发言"
            body = json.dumps({"room": mid, "text": "@项目经理 这句话要绑定到项目室"}).encode()
            req = urllib.request.Request(base + "/room_say", data=body, headers={"content-type": "application/json"}, method="POST")
            assert urllib.request.urlopen(req, timeout=3).status == 200
            room_data = json.loads(urllib.request.urlopen(base + "/room?id=" + mid, timeout=3).read().decode())
            assert any("绑定到项目室" in item["text"] for item in room_data["项目记忆"])
            assert urllib.request.urlopen(base + "/state", timeout=3).status == 200
        finally:
            srv.shutdown()


def main() -> int:
    tests = [
        test_kickoff_confirm_approval_parallel,
        test_stop_emergency_and_whitelist,
        test_resume_backlog_task_clears_engine_card,
        test_pm_auto_approves_low_risk_budget_overrun,
        test_safe_validation_command_runner,
        test_emergency_stop_overrides_breaker,
        test_validation_command_missing_file_no_crash,
        test_board_exposes_formal_reports,
        test_collaboration_meeting_and_owner_participation,
        test_real_kickoff_uses_multi_role_meeting_discussion,
        test_board_merges_backlog_and_engine_approval,
        test_room_bound_memory_cross_context,
        test_legacy_http_endpoints_200,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
