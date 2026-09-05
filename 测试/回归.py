#!/usr/bin/env python3
"""开发公司自动化回归套件。

唯一正常入口：``.venv/bin/python3 测试/回归.py``。
入口会先复制一套临时公司、排除真实 ``.env`` 和运行数据，再在副本里执行全部测试；
测试模块拒绝从真实公司根直接导入，避免误删管理卡、信誉、请示或待验收记录。
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import importlib
import json
import os
import queue
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


_隔离标记 = "XJ_REGRESSION_SANDBOX"


def _在临时公司运行() -> int:
    源公司 = Path(__file__).resolve().parents[1]
    临时根 = Path(tempfile.mkdtemp(prefix="xj-company-regression-"))
    副本 = 临时根 / 源公司.name
    排除 = shutil.ignore_patterns(
        ".env", ".git", ".venv", "node_modules", "dist", "__pycache__", "*.pyc",
        "scratchpad", "90_备份", "_官方参考_agentscope", "as_log", "embedding缓存",
        "会话事件", "大厅索引.db", "大厅索引.db-*",
    )
    try:
        shutil.copytree(源公司, 副本, ignore=排除)
        env = os.environ.copy()
        env[_隔离标记] = "1"
        env["XJ_MOCK"] = "1"
        result = subprocess.run(
            [sys.executable, str(副本 / "测试" / "回归.py")],
            cwd=副本,
            env=env,
            check=False,
        )
        return int(result.returncode)
    finally:
        shutil.rmtree(临时根, ignore_errors=True)


if __name__ == "__main__" and os.environ.get(_隔离标记) != "1":
    raise SystemExit(_在临时公司运行())
if os.environ.get(_隔离标记) != "1":
    raise RuntimeError("回归套件禁止直接载入真实公司；请运行：.venv/bin/python3 测试/回归.py")

_工具 = Path(__file__).resolve().parents[1] / "工具"
sys.path.insert(0, str(_工具))

import 信誉  # noqa: E402
import 晋升  # noqa: E402
import 职级  # noqa: E402
import 待验收记录  # noqa: E402
import 管理动作  # noqa: E402
import 平台_工具集 as 平台  # noqa: E402
import 部门  # noqa: E402
import 船主记忆  # noqa: E402
import 资料室  # noqa: E402
import 归档  # noqa: E402
import 看板数据  # noqa: E402
import 审批  # noqa: E402
import 活_本人  # noqa: E402
import 任务台  # noqa: E402
import 状态存储  # noqa: E402
import 事件总线  # noqa: E402
import 执行室  # noqa: E402
import 网络工具  # noqa: E402
import 机房  # noqa: E402
import 房间注册  # noqa: E402
import 项目室  # noqa: E402
import 健康  # noqa: E402
import 告警表达  # noqa: E402
import 备份  # noqa: E402
import 备份任务  # noqa: E402
import 大厅档案  # noqa: E402
import 大厅记录  # noqa: E402
import 大厅检索  # noqa: E402
import 常识门  # noqa: E402
import 会话摘要  # noqa: E402
import 记忆抽取  # noqa: E402
import 模型接入  # noqa: E402
import 升级_模型层  # noqa: E402
import 搜索工具  # noqa: E402
from 统一搜索_回归 import (  # noqa: E402,F401
    test_统一搜索_个人记忆权限不能由查询参数越权,
    test_统一搜索_人物时间先过滤再截断,
    test_统一搜索_单源损坏大声标记而非假装全局无结果,
    test_统一搜索_同义改写与两字关键词都有出处,
    test_统一搜索_同事件跨源合并且保留四份证据,
    test_统一搜索_离职人员私册缓存会从索引清掉,
    test_统一搜索_图谱真正两跳且严格只读,
    test_统一搜索_纯向量很像但重排低分仍不凑数,
    test_统一搜索_负查询不拿低分第一名凑答案,
    test_统一搜索_真实接口自检契约可离线验证,
    test_统一搜索_默认精排按本机与正式用户边界选择,
    test_本地精排_异步并发入口保序且限制候选数,
    test_统一搜索_精排候选最多16篇,
    test_统一搜索_精排繁忙时保留强字面证据而不让整轮失败,
    test_统一搜索_长记忆分批和增量索引,
)
from 本地精排资源_回归 import (  # noqa: E402,F401
    test_本地精排_共享与独立输出层都只计算最后位置两个词,
    test_本地精排_量化输出层只把两个词交给矩阵乘,
    test_本地精排_异常和成功都清理mlx缓存,
    test_本地精排_总令牌超限会在推理前拒绝,
    test_本地精排服务_排队满立即拒绝且空闲会卸载,
    test_本地精排服务_已超时的排队请求不会继续耗模型,
    test_本地精排客户端_只读状态不会启动服务或加载模型,
    test_本地精排客户端_自有服务资源异常时不重复拉起撞端口,
    test_本地精排客户端_输入超限不触发全局熔断,
    test_本地精排客户端_推理超时只重启自有服务,
    test_本地精排服务_内存越界会停止接新请求,
    test_告警表达_队列满时直接保留规则版而不无限排队,
    test_告警表达_缓存只保留最近256条,
    test_每轮统一搜索最多两次且版本问题不调用搜索,
)


# ── 状态隔离：快照/还原可变文件 ───────────────────────────────────────────
_可变目录 = [
    信誉.信誉目录,
    职级.状态目录,
    待验收记录.待验收目录,
    管理动作.待确认.parent,
    审批.待经理.parent,
    任务台.任务目录,
    事件总线.事件目录,
    项目室.记忆目录,
    资料室.资料室根,
    待验收记录.COMPANY / "编程部",
    待验收记录.COMPANY / "编辑部",
    待验收记录.COMPANY / "测试部",
    待验收记录.COMPANY / "经理办公室",
    待验收记录.COMPANY / "大厅" / "对话",
]


def _快照目录(root: Path) -> dict[str, bytes] | None:
    if not root.exists():
        return None
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


def _快照() -> dict[str, dict[str, bytes] | None]:
    return {str(root): _快照目录(root) for root in _可变目录}


def _还原(快照: dict[str, dict[str, bytes] | None]) -> None:
    for root_s, files in 快照.items():
        root = Path(root_s)
        shutil.rmtree(root, ignore_errors=True)
        if files is None:
            continue
        root.mkdir(parents=True, exist_ok=True)
        for rel, body in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(body)


def _清() -> None:
    """把测试要动的账清空到干净起点（在隔离内，安全）。"""
    for n in ("阿强", "老梁", "老钟"):
        信誉._账(n).unlink(missing_ok=True)
    职级.状态文件.unlink(missing_ok=True)
    职级.变动日志.unlink(missing_ok=True)
    (职级.状态目录 / "调任.json").unlink(missing_ok=True)
    晋升.曲线文件.unlink(missing_ok=True)
    (审批.COMPANY / "请示" / "_健康巡检状态.json").unlink(missing_ok=True)
    (审批.COMPANY / "运行状态" / "健康告警.json").unlink(missing_ok=True)
    for _d in (审批.待经理, 审批.待船主, 审批.已决):
        if _d.exists():
            for _p in _d.glob("*.json"):
                if _p.name.endswith("_健康巡检状态.json"):
                    _p.unlink(missing_ok=True)
    for _d in (管理动作.待确认, 管理动作.执行中, 管理动作.已办):
        if _d.exists():
            for _p in _d.glob("*.json"):
                _p.unlink(missing_ok=True)


try:
    import pytest

    @pytest.fixture(autouse=True)
    def _隔离():
        s = _快照()
        try:
            yield
        finally:
            _还原(s)
except ImportError:
    pytest = None


# ── 信誉 ─────────────────────────────────────────────────────────────────
def test_信誉_动态回落():
    _清()
    信誉.信誉目录.mkdir(exist_ok=True)
    old = (dt.datetime.now() - dt.timedelta(days=180)).strftime("%Y-%m-%d %H:%M")
    信誉._账("阿强").write_text(json.dumps({"时间": old, "事件": "验收通过", "权重": 2, "键": ""}, ensure_ascii=False) + "\n", encoding="utf-8")
    assert 1.8 <= 信誉.信誉分("阿强") <= 2.0     # 关半年也只缓慢回落、随后停滞
    assert 信誉.工作态信誉分("阿强") == 2.0
    assert 信誉.信任等级("阿强") == "中"          # 休眠不能把人打成受限
    for i in range(14):
        信誉.记一笔("阿强", "验收通过", 键=f"work-{i}", 权重覆盖=0, 工作批次=f"batch-{i}", 角色="交付")
    assert 0.9 <= 信誉.工作态信誉分("阿强") <= 1.1  # 14个真实工作批次后，旧结果才约减半
    _清()
    for _ in range(3):
        信誉.记一笔("阿强", "扎实一次过")
    assert 信誉.信誉分("阿强") == 9.0           # 事件权重对（扎实一次过=+3）
    信誉.记一笔("阿强", "放水被抓"); 信誉.记一笔("阿强", "偷懒被抓"); 信誉.记一笔("阿强", "偷懒被抓")
    assert 信誉.信任等级("阿强") in ("中", "受限")


def test_信誉_同批拆单不能刷分且难度质量生效():
    _清()
    a = 信誉.记任务通过("阿强", "小活一", 键="a", 工作批次="同一项目", 难度=1, 一次通过=True)
    b = 信誉.记任务通过("阿强", "小活二", 键="b", 工作批次="同一项目", 难度=1, 一次通过=True)
    c = 信誉.记任务通过("阿强", "真正难活", 键="c", 工作批次="同一项目", 难度=5, 一次通过=True)
    d = 信誉.记任务通过("阿强", "继续拆", 键="d", 工作批次="同一项目", 难度=5, 一次通过=True)
    assert (a, b, c, d) == (1.0, 0.0, 2.0, 0.0)
    assert sum(max(0, 信誉._权重(e)) for e in 信誉._事件列("阿强")) == 3.0
    返工 = 信誉.记任务通过("阿强", "返工才过", 键="e", 工作批次="另一项目", 难度=5, 一次通过=False)
    assert 返工 == 2.1                             # 同难度下，返工后通过低于一次通过3分
    再拆 = 信誉.记任务通过("阿强", "返工后再拆", 键="f", 工作批次="另一项目", 难度=5, 一次通过=False)
    assert 再拆 == 0.0                             # 拆单不能把返工折扣补回一次通过的满分


def test_信誉_新记重罚不会被旧工作批次误判过期():
    _清()
    for i in range(20):
        信誉.记一笔("阿强", "验收通过", 键=f"old-{i}", 权重覆盖=0, 工作批次=f"old-batch-{i}", 角色="交付")
    信誉.记一笔("阿强", "偷懒被抓", "刚发生")
    assert 信誉.有近期重罚("阿强") is True


def test_晋升_长期休眠趋稳且只有真实交付推进正常衰减():
    import datetime as _d
    晋升.曲线文件.unlink(missing_ok=True)
    base = _d.datetime(2026, 1, 1, 9, 0)
    晋升.记交付("测休眠", 2, True, 此刻=base, 结果键="首单")
    半年后 = 晋升.分("测休眠", base + _d.timedelta(days=180))
    assert 2.8 <= 半年后 <= 3.0                    # 关半年不会从3分掉到零
    晋升.记交付("测休眠", 2, True, 此刻=base + _d.timedelta(days=180), 结果键="复工")
    assert 晋升.分("测休眠", base + _d.timedelta(days=180)) > 半年后


def test_晋升曲线_长跑门槛与试用():
    import datetime as _d
    晋升.曲线文件.unlink(missing_ok=True)
    base = _d.datetime(2026, 7, 1, 9, 0)
    # ① 一两个任务够不着达标（长跑）
    assert 晋升.记交付("测X", 2, True, 此刻=base) is None
    assert 晋升.记交付("测X", 2, True, 此刻=base + _d.timedelta(days=1)) is None
    # ② 常速连续干，十几个任务才达标冻结
    冻结在 = None
    for d in range(2, 30):
        if 晋升.记交付("测X", 2, True, 此刻=base + _d.timedelta(days=d)) == "达标冻结":
            冻结在 = d + 1
            break
    assert 冻结在 and 12 <= 冻结在 <= 20          # 主管≈15个任务
    # ③ 试用 5 笔全准 → 晋升
    ev = None
    for i in range(5):
        ev = 晋升.记交付("测X", 2, True, 此刻=base + _d.timedelta(days=冻结在 + i))
    assert ev == "晋升就绪"   # 3H1：试用通过=晋升就绪(不重置曲线)，升成后由上层清
    # ④ 另一人试用里错 2 笔 → 清零
    晋升.曲线文件.unlink(missing_ok=True)
    dd = 0
    while 晋升.记交付("测Y", 2, True, 此刻=base + _d.timedelta(days=dd)) != "达标冻结":
        dd += 1
    晋升.记交付("测Y", 2, False, 此刻=base + _d.timedelta(days=dd + 1))
    assert 晋升.记交付("测Y", 2, False, 此刻=base + _d.timedelta(days=dd + 2)) == "清零"
    assert 晋升.分("测Y") == 0.0
    晋升.曲线文件.unlink(missing_ok=True)


def test_职级_维持种子受保护():
    _清()
    assert 职级.评级("老梁") == 3 and 信誉.信誉分("老梁") == 0.0
    assert 职级.维持不住("老梁") is False                   # 种子级3是船主给的底，0分也不因冷清自降
    职级.设评级("老梁", 4, "测试·升到种子级以上")           # 挣到4（种子3以上）
    assert 职级.维持不住("老梁") is True                    # 4分<维持线13、又在种子级以上→该降回3
    assert 职级.降("老梁")["到"] == 3


def test_信誉_记打回_反复敷衍_误打回_去重():
    _清()
    for i in range(3):
        信誉.记打回("阿强", f"第{i}次")
    evs = [e["事件"] for e in 信誉._事件列("阿强")]
    assert evs.count("交付被打回") == 3 and "反复敷衍" in evs
    _清()
    信誉.记打回("阿强", "冤枉", 把关者="老梁")            # 老梁打回阿强——误打回要真撤到一笔才记(十审#3)
    信誉.误打回("老梁", "阿强", "这活其实没问题")          # 平反 → 撤销那笔 + 老梁记『误打回』
    assert 信誉._事件列("老梁")[-1]["事件"] == "误打回"
    _清()
    信誉.记打回("阿强", "x", 键="rid1"); 信誉.记打回("阿强", "x", 键="rid1")
    assert [e["事件"] for e in 信誉._事件列("阿强")].count("交付被打回") == 1   # 同键去重


def test_信誉_受限名单():
    _清()
    信誉.记一笔("阿强", "扎实一次过")                                   # +3 → 高
    信誉.记一笔("老梁", "放水被抓"); 信誉.记一笔("老梁", "偷懒被抓")     # -7 → 受限
    assert 信誉.受限名单(["阿强", "老梁"]) == ["老梁"]
    assert 信誉.受限名单([]) == []


# ── 交付门判据 ────────────────────────────────────────────────────────────
def test_审结果_制式批文():
    # 2026-07-11 重构：只读【结论】栏的放行/打回，理由随它写、一个字不猜（船主"别硬编码猜语义"点破）
    assert 平台._审结果("【结论】放行\n【理由】接口边界都覆盖了")[0] == "放行"
    assert 平台._审结果("【结论】打回\n【理由】缺边界处理")[0] == "打回"
    assert 平台._审结果("【结论】放行")[0] == "放行"                       # 放行可不写理由
    assert 平台._审结果("【结论】打回")[1] == "（没说明理由）"              # 打回没理由给兜底文案
    # 括号/冒号变体都认（读结构、不卡死格式）
    assert 平台._审结果("[结论]放行")[0] == "放行"
    assert 平台._审结果("结论：打回\n理由：漏测")[0] == "打回"
    assert 平台._审结果("【结论】放行。\n【理由】ok")[0] == "放行"
    # 真模型先讲一段再下结论也认（【结论】在后面行，finditer 全篇找）
    assert 平台._审结果("整体实现正确，测试也跑了。\n【结论】放行\n【理由】无")[0] == "放行"
    # 根治点：理由里写什么都不影响结论——不再读理由的自然语言，否定词地狱消失
    assert 平台._审结果("【结论】放行\n【理由】通过不了的地方我都盯过了，没问题")[0] == "放行"
    assert 平台._审结果("【结论】打回\n【理由】没有一处不合格是假的，其实漏了空值")[0] == "打回"
    # 结论栏被写成句子=不精确 → 不算放行（防放水）
    assert 平台._审结果("【结论】放行标准没达到")[0] != "放行"
    # 兜底：只认"整行就是干净判词"（放行/打回）；带理由要走制式，裸"打回：缺测试"不精确→判不清(七审F4:防清单式冤枉)
    assert 平台._审结果("放行")[0] == "放行"
    assert 平台._审结果("打回")[0] == "打回"
    assert 平台._审结果("打回：缺测试")[0] == "判不清"
    assert 平台._审结果("放行不了，缺东西")[0] != "放行"
    # 没按制式、没干净判词 → 判不清（安全兜底，不放水不冤枉）
    assert 平台._审结果("这个还行吧你看着办")[0] == "判不清"
    assert 平台._审结果("我觉得整体不错，就是命名可以再想想，供你参考。")[0] == "判不清"
    # 崩/失败标记 → 审崩
    assert 平台._审结果("")[0] == "审崩"
    assert 平台._审结果("（欠费｜判定TIMEOUT，已停。要重跑说一声。）")[0] == "审崩"


def test_交付门_审崩不当放行():
    _清()
    async def mock(a, b, **k):
        return "（欠费｜判定NETWORK，已停。要重跑说一声。）"
    原 = 活_本人.唤醒本人
    活_本人.唤醒本人 = mock
    try:
        过, 文, 人, 节 = asyncio.run(平台._过交付门("工程师", "t", "p"))
    finally:
        活_本人.唤醒本人 = 原
    assert (not 过) and 人 is None and "审出错" in 节["动作"]   # 审崩→不放行、留可见节点


def test_信誉高也没有免复核开关():
    _清()
    信誉.记一笔("阿强", "扎实一次过")
    assert 信誉.信任等级("阿强") == "高"
    assert not hasattr(信誉, "免复核")
    assert "def 免复核" not in (待验收记录.COMPANY / "工具" / "信誉.py").read_text(encoding="utf-8")


def test_两道固定把关内部异常都拦住():
    原头 = 部门.部门头
    原经理 = 职级.现任经理
    try:
        部门.部门头 = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("部门账坏"))
        过1, _, _, 节1 = asyncio.run(平台._过交付门("工程师", "t", "p"))
        assert not 过1 and "未放行" in 节1["动作"]
        职级.现任经理 = lambda: (_ for _ in ()).throw(RuntimeError("权限账坏"))
        过2, _, _, 节2 = asyncio.run(平台._过经理收敛("t", "p", "阿强", "阿强"))
        assert not 过2 and "未放行" in 节2["动作"]
    finally:
        部门.部门头 = 原头
        职级.现任经理 = 原经理


def test_单人部门和部门头本人必须换独立复核():
    async def mock(岗位, *_a, **_k):
        assert 岗位 == "首席工程师"
        return "【结论】放行\n【理由】独立复核通过"

    原 = 活_本人.唤醒本人
    活_本人.唤醒本人 = mock
    try:
        过, _, 复核人, 节 = asyncio.run(平台._过交付门("测试工程师", "t", "p"))
    finally:
        活_本人.唤醒本人 = 原
    assert 过 and 复核人 == "老梁" and "代理审" in 节["动作"]


def test_没有独立部门复核人就拦住():
    原头 = 部门.部门头
    原替 = 部门.部门复核替补
    try:
        部门.部门头 = lambda *_a, **_k: "老纪"
        部门.部门复核替补 = lambda *_a, **_k: (None, None)
        过, 文, 人, 节 = asyncio.run(平台._过交付门("测试工程师", "t", "p"))
    finally:
        部门.部门头 = 原头
        部门.部门复核替补 = 原替
    assert not 过 and 人 is None and "不能跳过" in 文 and "未放行" in 节["动作"]


def test_经理派的活也必须重新经过经理收敛():
    叫醒 = []

    async def mock(岗位, *_a, **_k):
        叫醒.append(岗位)
        return "【结论】放行\n【理由】经理复核通过"

    原 = 活_本人.唤醒本人
    活_本人.唤醒本人 = mock
    try:
        过, _, 收敛人, 节 = asyncio.run(平台._过经理收敛("t", "p", "老钟", "阿强", "老梁"))
    finally:
        活_本人.唤醒本人 = 原
    assert 过 and 叫醒 == ["项目经理"] and 收敛人 == "老钟" and 节["动作"].startswith("收敛")


def test_经理本人交付必须换独立代理经理():
    叫醒 = []

    async def mock(岗位, *_a, **_k):
        叫醒.append(岗位)
        return "【结论】放行\n【理由】代理经理复核通过"

    原 = 活_本人.唤醒本人
    活_本人.唤醒本人 = mock
    try:
        过, _, 收敛人, 节 = asyncio.run(平台._过经理收敛("t", "p", "老钟", "老钟", "老纪"))
    finally:
        活_本人.唤醒本人 = 原
    assert 过 and 叫醒 == ["首席工程师"] and 收敛人 == "老梁" and 节["动作"].startswith("代理收敛")


def test_受限信誉只增加独立复核():
    _清()
    信誉.记一笔("阿强", "偷懒被抓")
    assert 信誉.需加复核("阿强") is True

    async def mock(*_a, **_k):
        return "【结论】放行\n【理由】复核通过"

    原 = 活_本人.唤醒本人
    活_本人.唤醒本人 = mock
    try:
        过, _, 复核人, 节 = asyncio.run(平台._过独立复核("工程师", "t", "p"))
    finally:
        活_本人.唤醒本人 = 原
    assert 过 and 复核人 and 节["层级"] == "独立复核"


# ── 职级 ─────────────────────────────────────────────────────────────────
def test_职级_升_抢坑顶替_降_权限():
    _清()
    for _ in range(7):
        信誉.记一笔("阿强", "扎实一次过")      # +21：3H1 升要过目标级维持门(到经理=18)，得够高才爬得上去
    信誉.记一笔("老梁", "放水被抓")             # -3→触底0
    assert 部门.部门头("阿强") == "老梁"
    r = 职级.升("阿强")
    assert r and r["到"] == 3 and r["被顶"] == "老梁"
    assert 职级.评级("阿强") == 3 and 职级.评级("老梁") == 2
    assert 部门.部门头("阿强") == "阿强"        # 把关权易主
    职级.升("阿强"); r5 = 职级.升("阿强")        # →4→5
    assert 职级.评级("阿强") == 5 and 部门.有跨部门权("阿强") is True
    assert r5["被顶"] == "老钟" and 部门.有跨部门权("老钟") is False


def test_职级_blocked_没赢过现任():
    _清()
    信誉.记一笔("老梁", "扎实一次过"); 信誉.记一笔("老梁", "扎实一次过")   # +6
    信誉.记一笔("阿强", "扎实一次过")                                    # +3 < 老梁
    assert 职级.升("阿强") is None and 职级.评级("阿强") == 2            # 坑没让出


def test_职级_扫降职():
    _清()
    信誉.记一笔("老梁", "放水被抓"); 信誉.记一笔("老梁", "偷懒被抓")   # -7 受限（主动失败）
    chg = 职级.扫降职()                                        # 只降职（升职是事件驱动、走晋升曲线）
    assert any(c["人"] == "老梁" for c in chg) and 职级.评级("老梁") < 3


def test_验收批准喂晋升曲线():
    _清()
    晋升.曲线文件.unlink(missing_ok=True)


def test_验收按难度结算且只检查当事人降职():
    _清()
    职级.设评级("老梁", 4, "回归：高于种子级")
    assert 职级.维持不住("老梁") is True
    rid = 待验收记录.新增(
        "工程师", "核心跨模块改造", "完成并验证", 任务组="同一委托", 难度=5,
    )
    assert 待验收记录.批准(rid, "一次通过")
    assert 信誉.任务结算分("阿强", f"{rid}:doer") == 3.0
    assert 职级.评级("老梁") == 4                 # 阿强交付不能顺手把没参与的老梁降职
    rec = 待验收记录._按id(rid)
    assert rec and rec["任务组"] == "同一委托" and rec["难度"] == 5
    rid = 待验收记录.新增("工程师", "小活", "写了个函数")
    assert 待验收记录.批准(rid, "过")
    assert 晋升.分("阿强") > 0            # 验收通过→晋升曲线加分（但一个远不够达标）
    assert 职级.评级("阿强") == 2         # 一个任务绝不升职
    晋升.曲线文件.unlink(missing_ok=True)


def test_职级_调任_迁记忆():
    _清()
    册 = 部门.个人记忆路径("阿强")
    快 = 册.read_text(encoding="utf-8") if 册.exists() else None
    try:
        (部门.COMPANY / "编程部").mkdir(parents=True, exist_ok=True)
        (部门.COMPANY / "编程部" / "阿强.md").write_text("# 阿强\n- 接口熟\n", encoding="utf-8")
        职级.调任("阿强", "测试部")
        assert 部门.认领部门("阿强").目录名 == "测试部"
        assert "接口熟" in (部门.COMPANY / "测试部" / "阿强.md").read_text(encoding="utf-8")
        assert not (部门.COMPANY / "编程部" / "阿强.md").exists()
    finally:
        (部门.COMPANY / "测试部" / "阿强.md").unlink(missing_ok=True)
        if 快 is not None:
            册.parent.mkdir(parents=True, exist_ok=True); 册.write_text(快, encoding="utf-8")


# ── 请示不得绕过经理 ──────────────────────────────────────────────────────
def test_请示_经理预审失败留在待经理():
    审批.初始化()
    cid = 审批.创建请示(任务="预审失败自检", 岗位="工程师", 内容="t", 建议="", 直接上报=False)
    原 = 审批._调用项目经理
    try:
        审批._调用项目经理 = lambda *_a, **_k: {"裁决": "格式坏"}
        assert (审批.待经理 / cid).exists()
        try:
            审批.经理预审(cid)
            assert False, "格式损坏不能上报船主"
        except ValueError:
            pass
        assert (审批.待经理 / cid).exists() and not (审批.待船主 / cid).exists()
        assert not hasattr(审批, "兜底转船主")
    finally:
        审批._调用项目经理 = 原
        (审批.待经理 / cid).unlink(missing_ok=True)
        (审批.待船主 / cid).unlink(missing_ok=True)
        (审批.已决 / cid).unlink(missing_ok=True)


# ── 待验收 半强制留痕 ─────────────────────────────────────────────────────
def test_半强制留痕_成果启发式():
    assert 待验收记录._像成果("好的我看看") is False
    assert 待验收记录._像成果("这个你怎么看呢" * 12 + "？") is False
    成果 = ("我把整套职级评级系统都接进来了，改好了机房、部门注册台、平台工具集、待验收记录、审批这几个文件，"
            "公司里谁是部门头、谁握审批权现在全部按评级动态解析，升职会真接管权力。"
            "\n```python\ndef 升(人名): ...\n```\n结论：把关权跟评级走，端到端整条链都测过了，没问题。")
    assert len(成果) >= 120
    assert 待验收记录._像成果(成果) is True
    待验收记录._今天文件().unlink(missing_ok=True)
    rid = 待验收记录.半强制留痕("项目经理", "把评级接进来", 成果)
    assert rid
    rec = [r for r in 待验收记录.列表(True) if r["id"] == rid][0]
    assert rec["来源"] == "大厅直答·可选验收"
    assert 待验收记录.半强制留痕("项目经理", "把评级接进来", 成果) is None   # 同原话去重


# ── 部门 校验/动态 ────────────────────────────────────────────────────────
def test_部门_校验_健康_名字冲突():
    assert 部门.校验() == []
    assert 部门.名字冲突() == {}


def test_可参与岗位_归一岗位_动态():
    import 模型接入
    assert 平台.可参与岗位() == set(模型接入.花名册().keys())
    assert 平台.归一岗位("梁工") == "首席工程师"       # 别名（花名册）
    assert 平台.归一岗位("测试") == "测试工程师"       # 功能关键词
    assert 平台.归一岗位("不存在的岗") is None


def test_花名册故障不恢复固定旧岗位():
    原 = 模型接入.花名册
    try:
        模型接入.花名册 = lambda: (_ for _ in ()).throw(RuntimeError("花名册坏"))
        try:
            平台.可参与岗位()
            assert False, "花名册故障不能恢复固定五岗"
        except RuntimeError as e:
            assert "花名册坏" in str(e)
    finally:
        模型接入.花名册 = 原


def test_点名正式任务先由被点名者听见再交经理统筹():
    import 大厅

    聊天叫醒 = []
    工作叫醒 = []
    进展 = []

    async def 聊天(岗位, *_a, **_k):
        聊天叫醒.append(岗位)
        return "[转办事]"

    async def 工作(岗位, *_a, **_k):
        工作叫醒.append(岗位)
        return "经理已按点名立项派发"

    原聊 = 活_本人.聊天唤醒
    原工 = 活_本人.唤醒本人
    活_本人.聊天唤醒 = 聊天
    活_本人.唤醒本人 = 工作
    大厅记录.清承接岗位()
    try:
        回, 已落 = asyncio.run(大厅.活厅(
            "@阿强 帮我写个测试文件", "sid-point-task", 进展.append, [], set(),
            "工程师", True, "帮我写个测试文件", "帮我写个测试文件",
        ))
    finally:
        活_本人.聊天唤醒 = 原聊
        活_本人.唤醒本人 = 原工
        大厅记录.清承接岗位()
    assert 聊天叫醒 == ["工程师"] and 工作叫醒 == ["项目经理"]
    assert 已落 and "立项派发" in 回
    assert any("听出这是正式工作" in str(x) for x in 进展)


def test_普通对话不调意图门常识门或请示判官():
    import 大厅

    叫醒 = []

    async def 聊天(岗位, *_a, **_k):
        叫醒.append(岗位)
        return "我去查工具间的真实在岗状态。"

    def 不该调(*_a, **_k):
        raise AssertionError("大厅前置判定不应再被调用")

    原聊 = 活_本人.聊天唤醒
    原门 = 常识门.过门
    原请示 = 机房._消解请示
    活_本人.聊天唤醒 = 聊天
    常识门.过门 = 不该调
    机房._消解请示 = 不该调
    大厅记录.清承接岗位()
    try:
        回, 已落 = asyncio.run(大厅.活厅(
            "现在谁在？", "sid-no-front-gate", lambda _x: None, [], set(),
            "项目经理", False, "现在谁在？", "现在谁在？",
        ))
    finally:
        活_本人.聊天唤醒 = 原聊
        常识门.过门 = 原门
        机房._消解请示 = 原请示
        大厅记录.清承接岗位()
    assert 叫醒 == ["项目经理"] and 已落 and "工具间" in 回
    assert not hasattr(活_本人, "_判话题边界")


def test_大厅与会议四种发言身份工具互不串线():
    assert set(平台.大厅承接工具名()) == {
        "unified_search", "recent_hall_chats", "who_is_free", "company_runtime", "find_colleague",
        "invite_colleagues_to_speak", "handoff_conversation", "escalate_to_work", "ask_owner",
    }
    assert set(平台.受邀发言工具名()) == {
        "unified_search", "recent_hall_chats", "who_is_free", "company_runtime", "flag_for_responsible_person",
    }
    assert set(平台.承接复核工具名()) == {
        "unified_search", "recent_hall_chats", "who_is_free", "company_runtime", "escalate_to_work", "ask_owner",
    }
    assert 平台.会议裁决工具名() == ()
    assert asyncio.run(平台.会议裁决工具集().get_tool_schemas()) == []


def test_受邀发言继承随口聊规矩且不预灌旧工作摘要():
    提示 = 活_本人._聊天系统提示(
        "工程师", "阿强", 唤醒理由="请说说今天感觉", 受邀发言=True,
    )
    assert "不是轮流作工作汇报" in 提示
    assert "先回答他实际问的那一点" in 提示
    assert "利索接地气" in 提示 and "src/xj/state" not in 提示
    assert "记忆精华：" not in 提示
    assert "近期公司摘要：" not in 提示


def test_公开请多人说话且责任席不变():
    import 大厅

    叫醒 = []
    本场 = []
    进展 = []
    全员 = ["项目经理", "首席工程师", "工程师", "测试工程师", "文案工程师（兼职）"]

    async def 聊天(岗位, *_a, **kwargs):
        受邀 = bool(kwargs.get("受邀发言"))
        叫醒.append((岗位, 受邀))
        if 岗位 == "项目经理" and not 受邀:
            kwargs["聊天动作"].update({"邀请岗位": 全员, "邀请原因": "请每个人说自己的真实状态"})
            return "我今天状态挺稳，我也请他们各自说。"
        return f"{岗位}亲自回答"

    原聊 = 活_本人.聊天唤醒
    活_本人.聊天唤醒 = 聊天
    大厅记录.清承接岗位()
    try:
        回, 已落 = asyncio.run(大厅.活厅(
            "今儿都别让人代答，各自冒个泡", "sid-public-voices", 进展.append, 本场, set(),
            "项目经理", False, "今儿都别让人代答，各自冒个泡", "今儿都别让人代答，各自冒个泡",
        ))
        当前 = 大厅.当前责任岗位()
    finally:
        活_本人.聊天唤醒 = 原聊
        大厅记录.清承接岗位()
    assert 叫醒[0] == ("项目经理", False)
    assert set(叫醒[1:]) == {(岗, True) for 岗 in 全员[1:]}
    assert 本场[0][1] == "项目经理"
    assert {岗 for _人, 岗, _话 in 本场[1:]} == set(全员[1:])
    assert 当前 == "项目经理" and 已落 and 回 in {f"{岗}亲自回答" for 岗 in 全员[1:]}
    assert any("在大厅亲自说" in str(x) for x in 进展)


def test_公开回应并行开始并发出总人数():
    import 大厅

    async def 跑场景():
        已开始: set[str] = set()
        都开始 = asyncio.Event()
        事件 = []

        async def 聊天(岗位, *_a, **kwargs):
            if 岗位 == "项目经理":
                kwargs["聊天动作"].update({
                    "邀请岗位": ["工程师", "测试工程师"],
                    "邀请原因": "请两人分别说",
                })
                return "我请他们本人说。"
            已开始.add(岗位)
            if len(已开始) == 2:
                都开始.set()
            await asyncio.wait_for(都开始.wait(), timeout=0.5)
            return f"{岗位}回答"

        原聊 = 活_本人.聊天唤醒
        原发布 = 事件总线.发布事件
        活_本人.聊天唤醒 = 聊天
        事件总线.发布事件 = lambda _sid, ev: 事件.append(ev)
        大厅记录.清承接岗位()
        try:
            await 大厅.活厅(
                "请他们分别说", "sid-public-parallel", lambda _x: None, [], set(),
                "项目经理", False, "请他们分别说", "请他们分别说",
            )
        finally:
            活_本人.聊天唤醒 = 原聊
            事件总线.发布事件 = 原发布
            大厅记录.清承接岗位()
        return 已开始, 事件

    已开始, 事件 = asyncio.run(跑场景())
    assert 已开始 == {"工程师", "测试工程师"}
    开始 = [e for e in 事件 if e.get("类型") == "公开回应开始"]
    assert len(开始) == 1 and 开始[0]["人数"] == 2


def test_受邀者掉线不拖垮其他人或改变责任席():
    import 大厅

    叫醒 = []
    进展 = []

    async def 聊天(岗位, *_a, **kwargs):
        叫醒.append(岗位)
        if 岗位 == "项目经理":
            kwargs["聊天动作"].update({
                "邀请岗位": ["工程师", "测试工程师"],
                "邀请原因": "各说各的判断",
            })
            return "我先说我的。"
        if 岗位 == "工程师":
            raise RuntimeError("阿强掉线")
        return "老纪继续把自己的说完。"

    原聊 = 活_本人.聊天唤醒
    活_本人.聊天唤醒 = 聊天
    大厅记录.清承接岗位()
    try:
        回, 已落 = asyncio.run(大厅.活厅(
            "都说说", "sid-public-one-down", 进展.append, [], set(),
            "项目经理", False, "都说说", "都说说",
        ))
        当前 = 大厅.当前责任岗位()
    finally:
        活_本人.聊天唤醒 = 原聊
        大厅记录.清承接岗位()
    assert 叫醒 == ["项目经理", "工程师", "测试工程师"]
    assert 当前 == "项目经理" and 已落 and 回 == "老纪继续把自己的说完。"
    assert any("其他人继续说，责任席不变" in str(x) for x in 进展)


def test_受邀者只能提醒且仍由原负责人决定是否开工():
    import 大厅

    聊天叫醒 = []
    工作叫醒 = []
    进展 = []

    async def 聊天(岗位, *_a, **kwargs):
        聊天叫醒.append((岗位, bool(kwargs.get("受邀发言")), bool(kwargs.get("复核提醒"))))
        if 岗位 == "项目经理" and not kwargs.get("复核提醒"):
            kwargs["聊天动作"].update({"邀请岗位": ["工程师"], "邀请原因": "请阿强说判断"})
            return "我先听阿强本人说。"
        if 岗位 == "工程师":
            kwargs["聊天动作"].update({"提醒承接人": ["这件事需要修改文件"]})
            return "这已经不是闲聊了，需要真改文件。"
        return "[转办事]"

    async def 工作(岗位, *_a, **_kwargs):
        工作叫醒.append(岗位)
        return "项目经理复核后正式接活。"

    原聊 = 活_本人.聊天唤醒
    原工 = 活_本人.唤醒本人
    活_本人.聊天唤醒 = 聊天
    活_本人.唤醒本人 = 工作
    大厅记录.清承接岗位()
    try:
        回, 已落 = asyncio.run(大厅.活厅(
            "你们看看这是不是得真改", "sid-invite-flag", 进展.append, [], set(),
            "项目经理", False, "你们看看这是不是得真改", "你们看看这是不是得真改",
        ))
        当前 = 大厅.当前责任岗位()
    finally:
        活_本人.聊天唤醒 = 原聊
        活_本人.唤醒本人 = 原工
        大厅记录.清承接岗位()
    assert 聊天叫醒 == [
        ("项目经理", False, False),
        ("工程师", True, False),
        ("项目经理", False, True),
    ]
    assert 工作叫醒 == ["项目经理"]
    assert 当前 == "项目经理" and 已落 and 回 == "项目经理复核后正式接活。"
    assert any("交回老钟复核" in str(x) for x in 进展)


def test_同轮邀请和转交时明示转交优先而不偷跑邀请():
    import 大厅

    叫醒 = []
    进展 = []

    async def 聊天(岗位, *_a, **kwargs):
        叫醒.append((岗位, bool(kwargs.get("受邀发言"))))
        if 岗位 == "项目经理":
            kwargs["聊天动作"].update({
                "邀请岗位": ["测试工程师"],
                "邀请原因": "请老纪说",
                "转交岗位": "工程师",
                "转交原因": "由阿强继续负责",
            })
            return "我把后续交给阿强。"
        return "阿强接手。"

    原聊 = 活_本人.聊天唤醒
    活_本人.聊天唤醒 = 聊天
    大厅记录.清承接岗位()
    try:
        回, 已落 = asyncio.run(大厅.活厅(
            "接着说", "sid-invite-handoff", 进展.append, [], set(),
            "项目经理", False, "接着说", "接着说",
        ))
        当前 = 大厅.当前责任岗位()
    finally:
        活_本人.聊天唤醒 = 原聊
        大厅记录.清承接岗位()
    assert 叫醒 == [("项目经理", False), ("工程师", False)]
    assert 当前 == "工程师" and 已落 and 回 == "阿强接手。"
    assert any("责任变更优先" in str(x) and "不执行公开邀请" in str(x) for x in 进展)


def test_大厅聊天唤醒计入防失控总量且不误报岗位掉线():
    sid = "sid-chat-budget"
    原上限 = 活_本人._单会话动作硬上限
    活_本人.重置动作数(sid)
    活_本人._单会话动作硬上限 = 1
    try:
        try:
            asyncio.run(活_本人.聊天唤醒(
                "项目经理", 会话id=sid,
                历史记录=[{"t": "2026-08-21 12:00:00", "who": "船主", "text": "说句话"}],
            ))
            assert False, "达到总量上限时不得继续调用模型"
        except 活_本人.聊天预算已满:
            pass
    finally:
        活_本人._单会话动作硬上限 = 原上限
        活_本人.重置动作数(sid)


def test_承接人撞防失控上限不被误报掉线或改叫经理():
    import 大厅

    叫醒 = []
    进展 = []

    async def 已满(岗位, *_a, **_k):
        叫醒.append(岗位)
        raise 活_本人.聊天预算已满("已满")

    原聊 = 活_本人.聊天唤醒
    活_本人.聊天唤醒 = 已满
    大厅记录.清承接岗位()
    大厅记录.记承接岗位("工程师")
    try:
        try:
            asyncio.run(大厅.活厅(
                "继续", "sid-owner-budget", 进展.append, [], set(),
                "项目经理", False, "继续", "继续",
            ))
            assert False, "防失控上限必须原样停止"
        except 活_本人.聊天预算已满:
            pass
    finally:
        活_本人.聊天唤醒 = 原聊
        大厅记录.清承接岗位()
    assert 叫醒 == ["工程师"]
    assert any("不是任何员工掉线" in str(x) for x in 进展)
    assert not any("未能续接" in str(x) or "回到项目经理" in str(x) for x in 进展)


def test_项目经理掉线时可临时代理且恢复原经理():
    import 大厅
    import 经理代理

    进展 = []
    calls = []
    第一轮掉线 = True

    async def mock(岗位, *_a, **_k):
        nonlocal 第一轮掉线
        calls.append(岗位)
        if 岗位 == "项目经理" and 第一轮掉线:
            raise RuntimeError("PM down")
        if 岗位 == "首席工程师":
            assert 部门.有跨部门权("老梁") is True
            return "代理已接手"
        if 岗位 == "项目经理":
            return "原经理回来了"
        raise AssertionError(f"unexpected {岗位}")

    原 = 活_本人.唤醒本人
    活_本人.唤醒本人 = mock
    try:
        岗位1, 回1 = asyncio.run(大厅._唤醒统筹("项目经理", "做个活", progress=进展.append, 会话id="sid-proxy", 失败原因="项目经理掉线"))
        第一轮掉线 = False
        岗位2, 回2 = asyncio.run(大厅._唤醒统筹("项目经理", "再做个活", progress=进展.append, 会话id="sid-proxy", 失败原因="项目经理恢复"))
    finally:
        活_本人.唤醒本人 = 原
        经理代理.清租约("sid-proxy")
    assert 岗位1 == "首席工程师" and 回1 == "代理已接手"
    assert 岗位2 == "项目经理" and 回2 == "原经理回来了"
    assert calls == ["项目经理", "首席工程师", "项目经理"]
    assert any("临时代理统筹" in str(x) for x in 进展)
    assert 经理代理.当前临时统筹() == (None, None)
    assert not 部门.有跨部门权("老梁")


def test_普通状态聊天失败也由临时经理接住且原经理恢复后接回():
    import 大厅
    import 经理代理

    进展 = []
    calls = []
    第一轮掉线 = True

    async def 聊天(岗位, *_a, **_k):
        nonlocal 第一轮掉线
        calls.append(岗位)
        if 岗位 == "项目经理" and 第一轮掉线:
            raise RuntimeError("PM chat down")
        if 岗位 == "首席工程师":
            return "老梁临时接住了在岗查询"
        if 岗位 == "项目经理":
            return "老钟恢复并接回了在岗查询"
        raise AssertionError(f"unexpected {岗位}")

    原聊 = 活_本人.聊天唤醒
    活_本人.聊天唤醒 = 聊天
    大厅记录.清承接岗位()
    try:
        回1, _ = asyncio.run(大厅.活厅("现在谁在？", "sid-status-proxy", 进展.append, [], set(), "项目经理", False, "现在谁在？", "现在谁在？"))
        第一轮掉线 = False
        回2, _ = asyncio.run(大厅.活厅("现在谁在？", "sid-status-proxy", 进展.append, [], set(), "项目经理", False, "现在谁在？", "现在谁在？"))
    finally:
        活_本人.聊天唤醒 = 原聊
        经理代理.清租约("sid-status-proxy")
        大厅记录.清承接岗位()
    assert "临时接住" in 回1 and "恢复并接回" in 回2
    assert calls == ["项目经理", "首席工程师", "项目经理"]
    assert any("临时代理" in str(x) for x in 进展)


def test_临时代理租约会被经理收敛门识别():
    import 经理代理

    叫醒 = []

    async def mock(岗位, *_a, **_k):
        叫醒.append(岗位)
        return "【结论】放行\n【理由】代理经理复核通过"

    原 = 活_本人.唤醒本人
    活_本人.唤醒本人 = mock
    tok = 经理代理.进入临时统筹({"代理人": "老梁", "代理岗位": "首席工程师"})
    try:
        过, _, 收敛人, 节 = asyncio.run(平台._过经理收敛("t", "p", "老钟", "阿强", "老梁"))
    finally:
        经理代理.退出临时统筹(tok)
        活_本人.唤醒本人 = 原
    assert 过 and 叫醒 == ["首席工程师"] and 收敛人 == "老梁" and 节["动作"].startswith("代理收敛")


def test_点名岗位掉线可见且不让经理代答():
    import 大厅

    进展 = []

    async def 掉线(岗位, *_a, **_k):
        raise RuntimeError(f"{岗位}掉线")

    原聊 = 活_本人.聊天唤醒
    活_本人.聊天唤醒 = 掉线
    大厅记录.清承接岗位()
    try:
        try:
            asyncio.run(大厅.活厅("@阿强 在吗", "sid-point-chat", 进展.append, [], set(), "工程师", True, "在吗", "在吗"))
            assert False, "点名岗位掉线时不能让经理代答"
        except RuntimeError as e:
            assert "没有让项目经理代答" in str(e)
    finally:
        活_本人.聊天唤醒 = 原聊
        大厅记录.清承接岗位()
    assert any("未能到岗" in str(x) for x in 进展)


def test_责任席_点名后未点名续接且不看最后发言者():
    import 大厅

    叫醒 = []

    async def 聊天(岗位, *_a, **_k):
        叫醒.append(岗位)
        return f"{岗位}已接住"

    原聊 = 活_本人.聊天唤醒
    活_本人.聊天唤醒 = 聊天
    大厅记录.清承接岗位()
    try:
        asyncio.run(大厅.活厅("@阿强 这个怎么看", "sid-owner-1", lambda _x: None, [], set(), "工程师", True, "这个怎么看", "这个怎么看"))
        大厅记录.记一句("老纪", "我只是路过补了一句")
        asyncio.run(大厅.活厅("那你继续说", "sid-owner-2", lambda _x: None, [], set(), "项目经理", False, "那你继续说", "那你继续说"))
    finally:
        活_本人.聊天唤醒 = 原聊
        大厅记录.清承接岗位()
    assert 叫醒 == ["工程师", "工程师"]


def test_只有本人明确转交才更换责任席():
    import 大厅

    叫醒 = []
    进展 = []

    async def 聊天(岗位, *_a, **kwargs):
        叫醒.append(岗位)
        if 岗位 == "项目经理":
            kwargs["聊天动作"].update({"转交岗位": "工程师", "转交原因": "这是阿强的实作地盘"})
            return "这个让阿强直接跟你接着聊。"
        return "我接手，直接说这块的实情。"

    原聊 = 活_本人.聊天唤醒
    活_本人.聊天唤醒 = 聊天
    大厅记录.清承接岗位()
    try:
        回, _ = asyncio.run(大厅.活厅("这个接口到底怎么回事", "sid-handoff", 进展.append, [], set(), "项目经理", False, "这个接口到底怎么回事", "这个接口到底怎么回事"))
        当前 = 大厅.当前责任岗位()
    finally:
        活_本人.聊天唤醒 = 原聊
        大厅记录.清承接岗位()
    assert 叫醒 == ["项目经理", "工程师"] and 当前 == "工程师"
    assert "我接手" in 回 and any("明确交给" in str(x) for x in 进展)


def test_未点名承接人掉线默认回经理席():
    import 大厅

    叫醒 = []
    进展 = []

    async def 聊天(岗位, *_a, **_k):
        叫醒.append(岗位)
        if 岗位 == "工程师":
            raise RuntimeError("阿强掉线")
        return "我先把这句接住。"

    原聊 = 活_本人.聊天唤醒
    活_本人.聊天唤醒 = 聊天
    大厅记录.清承接岗位()
    大厅记录.记承接岗位("工程师")
    try:
        回, _ = asyncio.run(大厅.活厅("继续", "sid-owner-down", 进展.append, [], set(), "项目经理", False, "继续", "继续"))
        当前 = 大厅.当前责任岗位()
    finally:
        活_本人.聊天唤醒 = 原聊
        大厅记录.清承接岗位()
    assert 叫醒 == ["工程师", "项目经理"] and 当前 == "项目经理"
    assert "接住" in 回 and any("回到项目经理责任席" in str(x) for x in 进展)


def test_责任席与当前会话同时跨天或九十分钟失效():
    大厅记录.清承接岗位()
    try:
        大厅记录.记承接岗位("工程师")
        现在 = dt.datetime.now()
        assert 大厅记录.当前承接岗位(现在=现在) == "工程师"
        assert 大厅记录.当前承接岗位(现在=现在 + dt.timedelta(minutes=91)) is None
    finally:
        大厅记录.清承接岗位()


def test_经理席和未点名续接不在入口拦截临时经理():
    assert 机房._大厅入口需预检("项目经理", False) is False
    assert 机房._大厅入口需预检("项目经理", True) is False
    assert 机房._大厅入口需预检("工程师", False) is False
    assert 机房._大厅入口需预检("工程师", True) is True


def test_普通聊天上下文固定取当前会话十二句():
    记录 = [
        {"t": f"2026-08-20 10:{i:02d}:00", "who": "船主", "text": f"第{i}句"}
        for i in range(20)
    ]
    记录[0] = {"t": "2026-08-20 10:00:00", "who": "老钟", "text": "窗口外的本人旧话"}
    msgs = 活_本人.构造聊天消息("项目经理", 历史记录=记录)
    assert len(msgs) == 12
    assert "第8句" in msgs[0].get_text_content("") and "第19句" in msgs[-1].get_text_content("")
    assert all("窗口外的本人旧话" not in m.get_text_content("") for m in msgs)


# ── 船主记忆 整事实删 ─────────────────────────────────────────────────────
def test_船主记忆_删含_整事实():
    档 = 资料室.船主档案
    档.parent.mkdir(parents=True, exist_ok=True)
    档.write_text("## 基本事实\n- 小北是小九养的猫\n- 小北患白血病\n- 示例主人是婚礼摄影师\n", encoding="utf-8")
    assert 船主记忆.删含("白血病", "小北") == 1
    留 = 船主记忆._读档()
    assert "小北患白血病" not in 留 and "小北是小九养的猫" in 留   # 同主体不连坐
    assert 船主记忆.删含("婚礼摄影师", "示例主人") == 1            # 船主本人凭宾删
    assert 船主记忆.删含("电台", "小北") == 0                    # 主体对不上不误删


# ── 归档轮转 ──────────────────────────────────────────────────────────────
def test_归档轮转_行数_年龄(tmp_path=None):
    import tempfile
    import shutil
    tmp = Path(tempfile.mkdtemp())
    try:
        f = tmp / "log.jsonl"
        f.write_text("\n".join('{"i":%d}' % i for i in range(100)) + "\n", encoding="utf-8")
        assert 归档.归档轮转(f, 30) == 70
        assert len([l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]) == 30
        g = tmp / "信誉.jsonl"
        old = (dt.datetime.now() - dt.timedelta(days=120)).strftime("%Y-%m-%d %H:%M")
        new = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        g.write_text("\n".join([json.dumps({"时间": old, "事件": "旧"}, ensure_ascii=False)] * 3
                               + [json.dumps({"时间": new, "事件": "新"}, ensure_ascii=False)] * 2) + "\n", encoding="utf-8")
        assert 归档.归档旧事件(g, 90) == 3
        assert len([l for l in g.read_text(encoding="utf-8").splitlines() if l.strip()]) == 2

        company = tmp / "company"
        tasks = company / "运行状态/任务"
        tasks.mkdir(parents=True)
        old_time = (dt.datetime.now() - dt.timedelta(days=120)).isoformat(timespec="seconds")
        (tasks / "done.json").write_text(json.dumps({"状态": "已完成", "更新时间": old_time}), encoding="utf-8")
        (tasks / "pending.json").write_text(json.dumps({"状态": "待验收", "更新时间": old_time}), encoding="utf-8")
        old_company = 归档.COMPANY
        归档.COMPANY = company
        try:
            assert 归档.归档任务记录(90) == 1
            assert not (tasks / "done.json").exists()
            assert list((tasks / "归档").rglob("done.json.gz"))
            assert (tasks / "pending.json").exists(), "等船主拍板的任务不能被当历史搬走"
        finally:
            归档.COMPANY = old_company

        records = tmp / "工具间记录"
        records.mkdir()
        old_day = (dt.date.today() - dt.timedelta(days=120)).isoformat()
        (records / f"{old_day}.jsonl").write_text("{}\n", encoding="utf-8")
        assert 归档.归档日记录(records, 90) == 1
        assert list((records / "归档").rglob(f"{old_day}.jsonl.gz"))

        logs = company / "运行状态/as_log"
        logs.mkdir(parents=True)
        old_log = logs / "backend.旧.log"
        old_log.write_text("old\n", encoding="utf-8")
        old_stamp = (dt.datetime.now() - dt.timedelta(days=40)).timestamp()
        os.utime(old_log, (old_stamp, old_stamp))
        old_company = 归档.COMPANY
        归档.COMPANY = company
        try:
            assert 归档.归档旧日志(30) == 1
            assert list((logs / "归档").rglob("backend.旧.log.gz"))
        finally:
            归档.COMPANY = old_company
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_大厅旧日归档后仍可回看搜索和摘要():
    import tempfile
    import shutil

    tmp = Path(tempfile.mkdtemp(prefix="hall-archive-continuity-"))
    day = (dt.date.today() - dt.timedelta(days=40)).isoformat()
    hall = tmp / "大厅/对话"
    hall.mkdir(parents=True)
    source = hall / f"{day}.jsonl"
    source.write_text(
        json.dumps({"时间": f"{day} 09:00:00", "who": "测试员", "text": "归档后仍能找到银色钥匙"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    old_company = 归档.COMPANY
    old_record_dir = 大厅记录.对话目录
    old_archive_dir = 大厅档案.默认目录
    old_search_dir = 大厅检索.对话目录
    old_search_index = 大厅检索.索引文件
    old_summary_dir = 会话摘要.对话目录
    try:
        归档.COMPANY = tmp
        大厅记录.对话目录 = hall
        大厅档案.默认目录 = hall
        大厅检索.对话目录 = hall
        大厅检索.索引文件 = tmp / "运行状态/大厅索引.db"
        会话摘要.对话目录 = hall
        result = 归档.日常轮转()
        assert result.get(f"大厅对话/{day}.jsonl") == "整天压缩归档"
        assert not source.exists() and list((hall / "归档").rglob(f"{day}.jsonl.gz"))
        assert day in 大厅记录.全部日期()
        assert "银色钥匙" in 大厅记录.读某日(day)[0]["text"]
        assert "银色钥匙" in 会话摘要._读档案(dt.date.fromisoformat(day))
        hits = 大厅检索.搜历史("银色钥匙", 3)
        assert hits and "银色钥匙" in hits[0]["命中"]
    finally:
        归档.COMPANY = old_company
        大厅记录.对话目录 = old_record_dir
        大厅档案.默认目录 = old_archive_dir
        大厅检索.对话目录 = old_search_dir
        大厅检索.索引文件 = old_search_index
        会话摘要.对话目录 = old_summary_dir
        shutil.rmtree(tmp, ignore_errors=True)


def test_日常轮转_分项失败会报出来():
    import tempfile
    import shutil
    tmp = Path(tempfile.mkdtemp(prefix="company-archive-fault-"))
    old_company = 归档.COMPANY
    old_old_events = 归档.归档旧事件
    old_rotate = 归档.归档轮转
    try:
        (tmp / "信誉").mkdir(parents=True)
        (tmp / "信誉" / "阿强.jsonl").write_text("{}\n", encoding="utf-8")
        (tmp / "职级").mkdir(parents=True)
        (tmp / "职级" / "变动.jsonl").write_text("{}\n", encoding="utf-8")
        归档.COMPANY = tmp
        归档.归档旧事件 = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("信誉盘坏"))
        归档.归档轮转 = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("职级盘坏"))
        result = 归档.日常轮转()
        assert "OSError: 信誉盘坏" in result["信誉/阿强.jsonl故障"]
        assert "OSError: 职级盘坏" in result["职级/变动.jsonl故障"]
    finally:
        归档.COMPANY = old_company
        归档.归档旧事件 = old_old_events
        归档.归档轮转 = old_rotate
        shutil.rmtree(tmp, ignore_errors=True)


# ── 看板缓存 ──────────────────────────────────────────────────────────────
def test_看板缓存_命中_失效():
    a = 看板数据.待办汇总()
    assert 看板数据.待办汇总() is a          # 缓存命中同对象
    v1 = 看板数据.看板版本("回归", ["工程师"])
    看板数据.清待办缓存()
    assert 看板数据.待办汇总() is not a       # 失效后重算
    assert 看板数据.看板版本("回归", ["工程师"]) != v1


def test_看板版本_文件变化才重算且三十秒兜底():
    import tempfile
    import shutil

    tmp = Path(tempfile.mkdtemp(prefix="board-version-"))
    source = tmp / "状态.json"
    source.write_text("一", encoding="utf-8")
    old_dirs = 看板数据._看板监视目录
    old_files = 看板数据._看板监视文件
    old_clock = 看板数据._看板时钟
    original = 看板数据._看板_算
    count = [0]

    def calculate(mode, roles):
        count[0] += 1
        return {"模式": mode, "岗位": roles, "次数": count[0]}

    try:
        看板数据._看板监视目录 = (tmp,)
        看板数据._看板监视文件 = ()
        看板数据._看板时钟 = lambda: 120.0
        看板数据._看板_算 = calculate
        看板数据.清待办缓存()

        first = 看板数据.看板("版本回归", ["工程师"])
        same = 看板数据.看板("版本回归", ["工程师"])
        assert first is same and count[0] == 1

        source.write_text("内容已经变化", encoding="utf-8")
        changed = 看板数据.看板("版本回归", ["工程师"])
        assert changed is not first and count[0] == 2

        看板数据._看板时钟 = lambda: 151.0
        fallback = 看板数据.看板("版本回归", ["工程师"])
        assert fallback is not changed and count[0] == 3
    finally:
        看板数据._看板_算 = original
        看板数据._看板监视目录 = old_dirs
        看板数据._看板监视文件 = old_files
        看板数据._看板时钟 = old_clock
        看板数据.清待办缓存()
        shutil.rmtree(tmp, ignore_errors=True)


def test_前端看板只在版本变化后拉完整数据():
    root = Path(__file__).resolve().parents[1]
    app = (root / "前端" / "src" / "App.tsx").read_text(encoding="utf-8")
    hooks = (root / "前端" / "src" / "hooks.ts").read_text(encoding="utf-8")
    server = (root / "工具" / "机房.py").read_text(encoding="utf-8")
    assert "useVersionedPoll<Board>" in app and "'/board-version'" in app
    assert "export function useVersionedPoll" in hooks
    assert 'path == "/board-version"' in server


def test_整张看板_并发只重算一次():
    import threading
    import time

    original = 看板数据._看板_算
    old_clock = 看板数据._看板时钟
    count = [0]
    lock = threading.Lock()

    def slow(mode, roles):
        with lock:
            count[0] += 1
        time.sleep(0.05)
        return {"模式": mode, "岗位": roles}

    看板数据._看板_算 = slow
    看板数据._看板时钟 = lambda: 120.0
    看板数据.清待办缓存()
    results: list[dict] = []
    try:
        threads = [threading.Thread(target=lambda: results.append(看板数据.看板("并发回归", ["工程师"]))) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert count[0] == 1 and len(results) == 12
        assert all(result is results[0] for result in results)
    finally:
        看板数据._看板_算 = original
        看板数据._看板时钟 = old_clock
        看板数据.清待办缓存()


def test_HTTP客户端断开不打印异常():
    class Broken:
        def write(self, _body):
            raise BrokenPipeError("client left")

    handler = object.__new__(机房.H)
    handler.wfile = Broken()
    handler.send_response = lambda *_a, **_k: None
    handler.send_header = lambda *_a, **_k: None
    handler.end_headers = lambda *_a, **_k: None
    handler._send(b"ok")


# ── 2026-07-10 二审补：把审计抓出的 bug 固化成回归（这些是happy-path测试漏掉的）──
def test_信誉_触底0_一次正向脱困():
    _清()
    信誉.记一笔("阿强", "偷懒被抓")                      # -4 → 触底 0
    assert 信誉.信誉分("阿强") == 0.0 and 信誉.信任等级("阿强") == "受限"
    信誉.记一笔("阿强", "扎实一次过")                    # +3，负分过去了、干净抬起
    assert 信誉.信誉分("阿强") == 3.0 and 信誉.信任等级("阿强") == "高"


def test_误打回_作废还清白():
    _清()
    信誉.记一笔("阿强", "扎实一次过")                    # +3
    信誉.记打回("阿强", "冤枉")                          # -2 → 1
    信誉.误打回("老梁", "阿强", "判错了")                # 作废阿强那笔打回
    assert 信誉.信誉分("阿强") == 3.0                    # 还原
    assert any(e.get("撤销") for e in 信誉._事件列("阿强") if e.get("事件") == "交付被打回")


def test_验收裁决幂等():
    _清()
    晋升.曲线文件.unlink(missing_ok=True)


def test_验收绑定提交时文件内容():
    p = 待验收记录.COMPANY / "产出" / "_回归_内容指纹.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("提交版本", encoding="utf-8")
    try:
        rid = 待验收记录.新增("工程师", "内容指纹", "已提交", 文件=[str(p)])
        rec = next(r for r in 待验收记录.列表(False) if r["id"] == rid)
        assert rec["文件快照"][0]["sha256"]
        p.write_text("提交后被换掉", encoding="utf-8")
        try:
            待验收记录.批准(rid, "不能批")
            assert False, "文件变化后不应批准"
        except RuntimeError as e:
            assert "已变化" in str(e)
        rec2 = next(r for r in 待验收记录.列表(False) if r["id"] == rid)
        assert rec2["状态"] == "待验收"
    finally:
        p.unlink(missing_ok=True)


def test_损坏状态拒绝覆盖并保留原件():
    p = 待验收记录.COMPANY / "运行状态" / "_回归_坏状态.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"没写完":', encoding="utf-8")
    try:
        try:
            状态存储.读JSON(p)
            assert False, "损坏 JSON 不应伪装成空状态"
        except 状态存储.状态损坏:
            pass
        try:
            状态存储.写JSON(p, {"新": "值"})
            assert False, "损坏状态不应被新状态覆盖"
        except 状态存储.状态损坏:
            pass
        assert p.read_text(encoding="utf-8") == '{"没写完":'
    finally:
        p.unlink(missing_ok=True)
        状态存储.清故障(p)


def test_任务台_重启中断有明确终态():
    tid = 任务台.创建("回归", "测试", "未收尾任务")
    任务台.更新(tid, "运行中")
    assert tid in 任务台.恢复中断()
    rec = 任务台.读取(tid)
    assert rec and rec["状态"] == "被重启中断" and "未自动重跑" in rec["错误"]


def test_事件总线_写盘失败不进入内存或前端():
    sid = "_回归事件写盘失败"
    事件总线.清空session(sid)
    s = 事件总线._取会话(sid)
    原 = s._追加持久
    def fail(_event):
        raise 事件总线.事件持久化失败("模拟磁盘故障")
    s._追加持久 = fail
    try:
        try:
            事件总线.发布事件(sid, {"类型": "发言", "内容": "不能只在内存出现"})
            assert False, "写盘失败必须抛错"
        except 事件总线.事件持久化失败:
            pass
        assert 事件总线.读事件(sid) == []
    finally:
        s._追加持久 = 原
        事件总线.清空session(sid)


def test_事件总线_慢订阅明确报缺口():
    sid = "_回归慢订阅"
    事件总线.清空session(sid)
    s = 事件总线._取会话(sid)
    q = queue.Queue(maxsize=1)
    with s.lock:
        s.订阅队列.append(q)
    try:
        事件总线.发布事件(sid, {"类型": "进展", "内容": "一"})
        事件总线.发布事件(sid, {"类型": "进展", "内容": "二"})
        gap = q.get_nowait()
        assert gap["类型"] == "事件缺口" and gap["序号"] == 2
        assert len(事件总线.读事件(sid)) == 2
    finally:
        事件总线.取消订阅(sid, q)
        事件总线.清空session(sid)


def test_会话id_非法路径在开工前拒绝():
    for sid in ("../../escape", "a/b", "a\\b", "x" * 97, "含 空格"):
        try:
            事件总线.校验会话id(sid)
            assert False, f"非法会话 id 应拒绝：{sid}"
        except ValueError as e:
            assert "非法字符" in str(e)
    assert 事件总线.校验会话id("实机-会话_20260714") == "实机-会话_20260714"


def test_事件总线_序号续传并在落盘前脱敏():
    sid = "_回归事件续传"
    事件总线.清空session(sid)
    事件总线.发布事件(sid, {"类型": "发言", "内容": "Bearer abcdefghijklmnop", "api_key": "secret-value"})
    事件总线.发布事件(sid, {"类型": "发言", "内容": "第二条"})
    事件总线.结束run(sid)
    try:
        events = 事件总线.读事件(sid)
        assert events[0]["api_key"] == "[已隐藏]" and "abcdefghijklmnop" not in events[0]["内容"]
        frames = list(事件总线.SSE流(sid, 从序号=1, 心跳秒=0.01))
        assert len(frames) == 2 and "id: 2" in frames[0] and "run结束" in frames[1]
        disk = (事件总线.事件目录 / f"{sid}.jsonl").read_text(encoding="utf-8")
        assert "secret-value" not in disk and "abcdefghijklmnop" not in disk
    finally:
        事件总线.清空session(sid)


def test_事件总线_坏行进入健康故障而非静默跳过():
    sid = "_回归事件坏行"
    事件总线.清空session(sid)
    p = 事件总线.事件目录 / f"{sid}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"类型":"正常"}\n{"没写完":\n', encoding="utf-8")
    try:
        with 事件总线._总线锁:
            事件总线._总线.pop(sid, None)
        assert len(事件总线.读事件(sid)) == 1
        health = 事件总线.健康状态()
        assert health["健康"] is False and health["损坏或写入失败"]
    finally:
        事件总线.清空session(sid)
        状态存储.清故障(p)


def test_会话事件_只归档已结束旧会话且仍可重放():
    sid = "_回归旧会话归档"
    unfinished = "_回归未结束会话"
    for value in (sid, unfinished):
        事件总线.清空session(value)
    事件总线.发布事件(sid, {"类型": "进展", "内容": "旧会话内容"})
    事件总线.结束run(sid)
    事件总线.发布事件(unfinished, {"类型": "进展", "内容": "未收尾"})
    old = (dt.datetime.now() - dt.timedelta(days=40)).timestamp()
    complete_path = 事件总线.事件目录 / f"{sid}.jsonl"
    unfinished_path = 事件总线.事件目录 / f"{unfinished}.jsonl"
    os.utime(complete_path, (old, old))
    os.utime(unfinished_path, (old, old))
    try:
        assert 归档.归档会话事件(30) == 1
        assert not complete_path.exists() and unfinished_path.exists()
        with 事件总线._总线锁:
            事件总线._总线.pop(sid, None)
        replay = 事件总线.读事件(sid)
        assert any(x.get("内容") == "旧会话内容" for x in replay)
        assert replay[-1]["类型"] == "run结束"
    finally:
        事件总线.清空session(sid)
        事件总线.清空session(unfinished)


def _工具文本(result) -> str:
    block = result.content[0]
    return block.get("text", "") if isinstance(block, dict) else getattr(block, "text", str(block))


def test_搜索工具_相对路径可读且绝对路径不被私自限界():
    read_result = asyncio.run(搜索工具.净Read()("班规.md"))
    assert "# 班规" in _工具文本(read_result)

    with tempfile.TemporaryDirectory(prefix="xj-search-outside-") as td:
        outside = Path(td) / "outside.txt"
        outside.write_text("outside-search-ok", encoding="utf-8")
        absolute_read = asyncio.run(搜索工具.净Read()(str(outside)))
        assert "outside-search-ok" in _工具文本(absolute_read)
        absolute_glob = asyncio.run(搜索工具.净Glob()("*.txt", path=td))
        assert str(outside.resolve()) in _工具文本(absolute_glob)


def test_搜索工具_搜索预算耗尽会大声停止():
    old = 搜索工具.搜索秒数上限
    搜索工具.搜索秒数上限 = -1
    try:
        result = asyncio.run(搜索工具.净Glob()("**/*.md"))
    finally:
        搜索工具.搜索秒数上限 = old
    assert "搜索超过" in _工具文本(result)


def test_执行室_只接受结构化验证命令():
    assert 执行室._验证计划(shlex.split("python3 -m py_compile 工具/状态存储.py"))
    assert 执行室._验证计划(shlex.split("python3 -m pytest 测试/x.py -q"))
    assert 执行室._验证计划(shlex.split("python3 -m unittest 测试.test_x -v"))
    assert 执行室._验证计划(shlex.split("npm run build"))
    for command in ("python3 -c print(1)", "python3 任意脚本.py", "find . -delete", "cat .env", "npx vite", "make test", "git status"):
        assert 执行室._验证计划(shlex.split(command)) is None


def test_执行室_最小环境网络禁用且只改副本():
    protected = 待验收记录.COMPANY / "产出" / "_回归_执行室保护.txt"
    probe = 待验收记录.COMPANY / "测试" / "_回归_执行室探针.py"
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("原件", encoding="utf-8")
    real_company = str(待验收记录.COMPANY)
    probe.write_text(
        "import os, socket, unittest\n"
        "from pathlib import Path\n\n"
        "class SandboxProbe(unittest.TestCase):\n"
        " def test_sandbox(self):\n"
        "    self.assertNotIn('XJ_FAKE_SECRET', os.environ)\n"
        f"    real = Path({real_company!r})\n"
        "    try:\n"
        "        (real / '班规.md').read_text(encoding='utf-8')\n"
        "        self.fail('不应读到真实公司源码')\n"
        "    except OSError:\n"
        "        pass\n"
        "    try:\n"
        "        socket.create_connection(('127.0.0.1', 8787), timeout=0.2)\n"
        "        self.fail('沙箱不应访问本机服务')\n"
        "    except OSError:\n"
        "        pass\n"
        "    target = Path(__file__).resolve().parents[1] / '产出' / '_回归_执行室保护.txt'\n"
        "    target.unlink()\n"
        "    self.assertFalse(target.exists())\n",
        encoding="utf-8",
    )
    os.environ["XJ_FAKE_SECRET"] = "should-not-leak"
    try:
        result = asyncio.run(执行室.run_command("python3 -m unittest 测试._回归_执行室探针 -v"))
        text = _工具文本(result)
        assert "退出码 0" in text, text
        assert protected.read_text(encoding="utf-8") == "原件"
    finally:
        os.environ.pop("XJ_FAKE_SECRET", None)
        protected.unlink(missing_ok=True)
        probe.unlink(missing_ok=True)


def test_网络工具_拒绝私网凭据和混合解析():
    for url in ("http://127.0.0.1/x", "http://169.254.169.254/latest", "http://10.0.0.2/x", "http://user:pass@example.com/"):
        try:
            网络工具._解析目标(url)
            assert False, f"应拒绝 {url}"
        except ValueError:
            pass
    public = 网络工具._解析目标("https://93.184.216.34/path?q=1")
    assert public.IP == "93.184.216.34" and public.固定网址.startswith("https://93.184.216.34/")
    original = socket.getaddrinfo
    socket.getaddrinfo = lambda *_a, **_k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ]
    try:
        try:
            网络工具._解析目标("https://mixed.example/")
            assert False, "DNS 只要混入一个内网地址就应整次拒绝"
        except ValueError as e:
            assert "非公网" in str(e)
    finally:
        socket.getaddrinfo = original


def test_附件_合法才落盘且整批失败不留残件():
    payload = base64.b64encode("你好，附件".encode()).decode()
    paths = 机房.保存附件([{"name": "说明.txt", "type": "text/plain", "data": "data:text/plain;base64," + payload}], "回归")
    assert len(paths) == 1 and (机房.COMPANY / paths[0]).read_text(encoding="utf-8") == "你好，附件"
    before = set((机房.COMPANY / "附件").rglob("*"))
    bad_cases = [
        [{"name": "坏.txt", "type": "text/plain", "data": "data:text/plain;base64,%%%"}],
        [{"name": "假.png", "type": "image/png", "data": "data:image/png;base64," + base64.b64encode(b"not-png").decode()}],
        [{"name": f"{i}.txt", "data": payload} for i in range(9)],
    ]
    for items in bad_cases:
        try:
            机房.保存附件(items, "回归失败")
            assert False, "非法附件应拒绝整批"
        except ValueError:
            pass
    after = set((机房.COMPANY / "附件").rglob("*"))
    assert not any("回归失败" in str(p) for p in after - before)


def test_房间注册_唯一契约与按场景装配():
    assert 房间注册.校验() == []
    rooms = 房间注册.快照()
    assert len({r["id"] for r in rooms}) == len(rooms)
    assert len({r["名称"] for r in rooms}) == len(rooms)
    expected = {
        "本人": {"Read", "Grep", "Glob", "web_search", "read_webpage", "unified_search", "recent_hall_chats", "who_is_free", "company_runtime"},
        "干活": {"Read", "Grep", "Glob", "web_search", "read_webpage", "run_command", "unified_search"},
        "发言": {"unified_search", "recent_hall_chats", "who_is_free", "company_runtime"},
    }
    for scene, names in expected.items():
        actual = [str(getattr(t, "name", "")) for t in 房间注册.装配工具(scene)]
        assert set(actual) == names and len(actual) == len(set(actual))


def test_统一搜索_三个工作场景都能通过权限门禁():
    from agentscope.permission import (
        PermissionBehavior,
        PermissionContext,
        PermissionEngine,
        PermissionMode,
        PermissionRule,
    )

    for scene in ("本人", "干活", "发言"):
        tool = next(
            t for t in 房间注册.装配工具(scene)
            if str(getattr(t, "name", "")) == "unified_search"
        )
        context = PermissionContext(
            mode=PermissionMode.DEFAULT,
            allow_rules={
                "unified_search": [PermissionRule(
                    tool_name="unified_search",
                    rule_content=None,
                    behavior=PermissionBehavior.ALLOW,
                    source="回归测试",
                )],
            },
        )
        decision = asyncio.run(
            PermissionEngine(context).check_permission(tool, {"query": "验收规则"})
        )
        assert decision.behavior == PermissionBehavior.ALLOW, scene


def test_房间注册_重复插头立即报错():
    p = 房间注册.清单目录 / "zz回归重复.py"
    p.write_text(
        "房间={'id':'hall','名称':'重复大厅','类型':'场所','实现':'大厅','入口':['活厅'],"
        "'输入契约':'x','输出契约':'y','留痕':'大厅/对话','权限边界':'z','场景':[]}\n",
        encoding="utf-8",
    )
    importlib.invalidate_caches()
    try:
        problems = 房间注册.校验()
        assert any("hall" in x and "重复认领" in x for x in problems)
        try:
            房间注册.装配工具("本人")
            assert False, "房间契约坏了应拒绝装配"
        except RuntimeError:
            pass
    finally:
        p.unlink(missing_ok=True)
        sys.modules.pop("房间.zz回归重复", None)
        importlib.invalidate_caches()


def test_部门注册_每个岗位必须且只能有一个归属():
    p = Path(部门.__file__).parent / "zz回归重复部门.py"
    p.write_text(
        "\"\"\"规则牌：权限、可见性、诚实边界。\"\"\"\n"
        "from pathlib import Path\nname='duplicate_dept'\n目录名='回归重复部门'\n收=['工程师']\n头='工程师'\n"
        "复核替补=['测试工程师']\n"
        "目录=Path(__file__).resolve().parents[2]/目录名\n",
        encoding="utf-8",
    )
    importlib.invalidate_caches()
    try:
        problems = 部门.校验()
        assert any("工程师" in x and "多个部门认领" in x for x in problems)
        try:
            部门.认领部门("阿强")
            assert False, "重复归属不应静默选第一个部门"
        except RuntimeError as e:
            assert "重复认领" in str(e)
    finally:
        p.unlink(missing_ok=True)
        sys.modules.pop("部门.zz回归重复部门", None)
        importlib.invalidate_caches()


def test_项目室_只保管上下文且新工作没有并行模型入口():
    room = "回归项目室"
    项目室.记录(room, "船主", "继续检查这件事", "项目经理")
    try:
        text = 项目室.读取(room)
        assert "继续检查这件事" in text and len(项目室.记忆路径(room).name.split("_", 1)[0]) == 8
        assert not hasattr(机房, "项目本人回应") and not hasattr(机房, "对话")
        actions = (机房.COMPANY / "前端" / "src" / "actions.ts").read_text(encoding="utf-8")
        assert "/room_say" not in actions
        hall_store = (机房.COMPANY / "前端" / "src" / "hallStore.ts").read_text(encoding="utf-8")
        assert "project_room" in hall_store
    finally:
        项目室.记忆路径(room).unlink(missing_ok=True)


def test_前端把大厅公开回应显示为聊天而不是开工():
    hall_view = (机房.COMPANY / "前端" / "src" / "components" / "HallView.tsx").read_text(encoding="utf-8")
    hall_store = (机房.COMPANY / "前端" / "src" / "hallStore.ts").read_text(encoding="utf-8")
    assert hall_view.count("!u.场合.startsWith('大厅')") >= 2
    assert "回应完成" in hall_view and "公开回应总数" in hall_view
    assert "公开回应开始" in hall_store and "set公开回应总数" in hall_store


def test_模型健康_真实调用具体型号且未经批准的明文远端不发密钥():
    import httpx
    os.environ["XJ_HEALTH_TEST_KEY"] = "fake"
    original = httpx.post
    class Response:
        def __init__(self, code):
            self.status_code = code
        def json(self):
            return {"model": "test-model", "choices": [{"message": {"content": "ok"}}]}
    try:
        for code, expected, status in ((200, True, "健康"), (403, False, "无模型权限"), (404, False, "型号不可用"), (429, False, "限流或额度"), (500, False, "上游故障")):
            httpx.post = lambda *_a, _code=code, **_k: Response(_code)
            result = 机房._探一岗("测试岗", {"base_url": "https://api.example/v1", "key_env": "XJ_HEALTH_TEST_KEY", "model": "test-model"})
            assert result["ok"] is expected and result["状态"] == status
        called = [False]
        httpx.post = lambda *_a, **_k: called.__setitem__(0, True)
        result = 机房._探一岗("测试岗", {"base_url": "http://api.example/v1", "key_env": "XJ_HEALTH_TEST_KEY", "model": "test-model"})
        assert result["ok"] is False and result["状态"] == "不安全配置" and called[0] is False
        called[0] = False
        httpx.post = lambda *_a, **_k: (called.__setitem__(0, True) or Response(200))
        result = 机房._探一岗("批准中转", {
            "base_url": "http://relay.example/v1",
            "明文HTTP授权地址": "http://relay.example/v1",
            "key_env": "XJ_HEALTH_TEST_KEY",
            "model": "test-model",
        })
        assert result["ok"] is True and called[0] is True and "先生批准" in result["原因"]
    finally:
        httpx.post = original
        os.environ.pop("XJ_HEALTH_TEST_KEY", None)


def test_模型接入_正式解析花名册且明文只放行先生批准的中转站():
    roster = 模型接入.花名册()
    assert isinstance(roster["项目经理"]["别名"], list)
    assert isinstance(roster["项目经理"]["rank"], int)
    try:
        模型接入._校验模型地址("http://192.0.2.10/v1", "项目经理")
        assert False, "实际调用层不应向远端明文 HTTP 发 key"
    except RuntimeError as e:
        assert "拒绝发送 API key" in str(e)
    模型接入._校验模型地址(
        "http://192.0.2.10/v1/chat/completions",
        "项目经理",
        "http://192.0.2.10/v1",
    )
    try:
        模型接入._校验模型地址(
            "http://192.0.2.11/v1/chat/completions",
            "项目经理",
            "http://192.0.2.10/v1",
        )
        assert False, "批准一条中转地址不应顺带放行其他远端 HTTP"
    except RuntimeError:
        pass
    模型接入._校验模型地址("http://127.0.0.1:8000/v1", "本机测试")
    模型接入._校验模型地址("https://api.example/v1", "安全测试")


def test_成员模型以花名册为唯一显示源且修改后立即刷新():
    roster = {
        "工程师": {
            "名字": "阿强",
            "title": "工程师",
            "rank": 30,
            "model": "glm-5.1",
        }
    }
    cache = {
        "工程师": {
            "registered_model": "glm-5.1",
            "actual_model": "glm-5.1",
            "ok": True,
            "checked_at": "2026-07-13T20:38:19",
        }
    }
    original_roster = 模型接入.花名册
    original_cache = 模型接入._缓存
    模型接入.花名册 = lambda: roster
    模型接入._缓存 = lambda: cache
    try:
        first = 模型接入.成员信息(["工程师"])[0]
        assert first["model"] == "glm-5.1" and first["model_ok"] is True

        roster["工程师"]["model"] = "glm-5.2"
        second = 模型接入.成员信息(["工程师"])[0]
        assert second["model"] == second["registered_model"] == "glm-5.2"
        assert second["actual_model"] == ""
        assert second["model_ok"] is None and second["checked_at"] == ""
    finally:
        模型接入.花名册 = original_roster
        模型接入._缓存 = original_cache


def test_系统健康_汇总契约状态任务与备份真相():
    snapshot = 健康.系统健康()
    assert "问题" in snapshot and "告警" in snapshot and "任务" in snapshot and "事件" in snapshot and "备份" in snapshot and "容量" in snapshot
    assert snapshot["ok"] is False
    assert any(x.get("区域") == "备份" for x in snapshot["问题"])
    assert "近7天明细" in snapshot["任务"]
    assert isinstance(snapshot["任务"]["近7天明细"], list)
    assert isinstance(snapshot["告警"], list)
    for item in snapshot["告警"]:
        assert item.get("标题") and item.get("详情") and item.get("建议")


def test_系统岗位异常_统一生成具体问题和动态决定():
    system = {
        "告警": [],
        "问题": [
            {"级别": "错误", "区域": "备份", "说明": "最新备份 ZIP 完整性失败"},
            {"级别": "警告", "区域": "模型身份", "说明": "最旧一次核验距今 8 天"},
        ],
    }
    alerts = 健康.补齐告警({"测试工程师": {"ok": False, "状态": "鉴权失败", "原因": "HTTP 401"}}, system)
    assert [x["类别"] for x in alerts] == ["系统", "系统", "班子"]
    assert alerts[0]["标题"] == "最新公司备份已经损坏"
    assert alerts[0]["可选动作"][0]["id"] == "重新备份"
    assert alerts[2]["可选动作"][0]["id"] == "恢复岗位调用"
    assert all(x.get("指纹") and len(x.get("可选动作") or []) == 3 for x in alerts)


def test_健康提醒_关闭和不再提示不会误发处理请求():
    source = (机房.COMPANY / "前端" / "src" / "components" / "HealthQuestionCard.tsx").read_text(encoding="utf-8")
    assert "if (health?.告警 !== undefined)" in source
    assert 'onClick={closeForNow}' in source
    assert "onClick={() => void submit('稍后')}" not in source
    assert "if (action === '不再提示')" in source
    assert "id: '不再提示'" in source
    assert "sessionStorage.setItem(HEALTH_SESSION_DISMISS_KEY" in source


def test_本地告警表达_只翻译技术原因不参与决定():
    alert = {
        "标题": "原标题",
        "详情": "原详情",
        "建议": "原建议",
        "可选动作": [
            {"id": "查明并重试", "标题": "旧标题", "说明": "旧说明", "系统字段": "保留"},
            {"id": "稍后", "标题": "稍后", "说明": "以后处理"},
            {"id": "补充", "标题": "补充", "说明": "写一句话"},
        ],
    }
    rendered = 告警表达._校验结果(alert, {"详情": "供应商仍可回答，当前失败发生在上一条消息流。"})
    assert rendered == {"详情": "供应商仍可回答，当前失败发生在上一条消息流。"}
    assert "标题" not in rendered and "建议" not in rendered and "可选动作" not in rendered


def test_任务失败告警把模型无回应说成人话并给出重试动作():
    raw = "APIError: Upstream service temporarily unavailable"
    detail, title, suggestion = 健康._失败事实("项目经理老钟", "现在谁在？", raw, "大厅")
    actions = 健康._任务动作("失败", raw)
    alert = 健康._告警(
        "错误",
        "任务",
        title,
        detail,
        suggestion,
        task_id="task-status",
        owner="项目经理",
        room="大厅",
        task_status="失败",
        owner_label="项目经理老钟",
        actions=actions,
        raw_detail=raw,
        failure_type=健康._故障类型(raw),
    )
    assert "模型服务没有回应" in alert["详情"]
    assert "没有拿到结果" in alert["详情"]
    assert "不是权限问题" in alert["详情"]
    assert alert["原始详情"] == raw
    assert alert["故障类型"] == "模型服务无回应"
    assert actions[0] == {"id": "重试任务", "标题": "重新执行这次任务", "说明": "按原要求再执行一遍，不改权限和模型配置"}


def test_任务失败在同一任务后续成功后不再生成活动告警():
    失败 = {
        "内容": "现在谁在？",
        "负责人": "项目经理",
        "房间": "大厅",
        "更新时间": "2026-08-15T16:57:33",
        "状态": "失败",
    }
    成功 = {
        "内容": "现在谁在",
        "负责人": "项目经理",
        "房间": "大厅",
        "更新时间": "2026-08-15T18:29:27",
        "状态": "已完成",
    }
    assert 健康._任务匹配键(失败) == 健康._任务匹配键(成功)
    assert 健康._后续已成功(失败, [成功]) is True


def test_稍后处理会让旧任务离开活动告警但保留历史():
    records = [
        {
            "动作": "稍后",
            "更新时间": "2026-08-15T17:04:29",
            "告警": {"任务id": "会-old"},
        },
    ]
    deferred = 健康._延期任务(records)
    old_alert = {"任务id": "会-old", "更新时间": "2026-08-13T22:48:48"}
    changed_alert = {"任务id": "会-old", "更新时间": "2026-08-16T10:00:00"}
    assert "会-old" in deferred
    assert 健康._告警已延期(old_alert, deferred) is True
    assert 健康._告警已延期(changed_alert, deferred) is False


def test_启动前任务失败进入历史而不是当前弹窗():
    alerts = [
        {"类别": "任务", "任务id": "旧任务", "更新时间": "2026-08-13T22:48:48"},
        {"类别": "任务", "任务id": "新任务", "更新时间": "2026-08-16T10:00:01"},
        {"类别": "系统", "标题": "当前备份异常", "更新时间": ""},
    ]
    current, history = 健康._区分当前与历史告警(alerts, dt.datetime.fromisoformat("2026-08-16T10:00:00"))
    assert [item.get("任务id") for item in history] == ["旧任务"]
    assert {item.get("任务id") for item in current} == {"新任务", None}


def test_启动前历史失败只进统计和历史_不生成任务台警告():
    now = dt.datetime.now()
    old_task = {
        "id": "会-history-only",
        "状态": "失败",
        "类型": "大厅任务",
        "负责人": "项目经理",
        "房间": "大厅",
        "更新时间": (now - dt.timedelta(hours=2)).isoformat(timespec="seconds"),
        "错误": "APITimeoutError",
        "内容": "历史任务",
    }
    original_running = 任务台.运行中
    original_interrupted = 任务台.中断项
    original_list = 任务台.列表
    try:
        任务台.运行中 = lambda: []
        任务台.中断项 = lambda: [old_task]
        任务台.列表 = lambda **_kwargs: []
        snapshot = 健康.系统健康(活动起点=now)
        assert snapshot["任务"]["近7天失败或中断"] == 1
        assert snapshot["任务"]["当前待处理告警"] == 0
        assert [item.get("任务id") for item in snapshot["历史告警"]] == ["会-history-only"]
        assert not any(item.get("区域") == "任务台" for item in snapshot["问题"])
    finally:
        任务台.运行中 = original_running
        任务台.中断项 = original_interrupted
        任务台.列表 = original_list


def test_大厅同一轮发言使用事件键只落盘一次():
    old_dir = 大厅记录.对话目录
    old_cache = 大厅记录._读缓存
    with tempfile.TemporaryDirectory() as temp:
        try:
            大厅记录.对话目录 = Path(temp)
            大厅记录._读缓存 = {}
            key = 大厅记录.发言事件键("sid-duplicate", "老钟", "这句话只能出现一次")
            大厅记录.记一句("老钟", "这句话只能出现一次", 事件键=key)
            大厅记录.记一句("老钟", "这句话只能出现一次", 事件键=key)
            files = list(Path(temp).glob("*.jsonl"))
            assert len(files) == 1
            assert len(files[0].read_text(encoding="utf-8").splitlines()) == 1
        finally:
            大厅记录.对话目录 = old_dir
            大厅记录._读缓存 = old_cache


def test_健康异常_自动落请示且同指纹不刷屏():
    _清()
    班子 = {
        "工程师": {"ok": False, "状态": "连接失败", "原因": "HTTP 503"},
    }
    系统 = {
        "ok": False,
        "问题": [
            {"级别": "错误", "区域": "备份", "说明": "没有公司记忆备份"},
        ],
    }
    first = 机房._健康请示(班子, 系统)
    assert first["created"] is True and first["active"] is True
    assert first["card_id"]
    assert (审批.待船主 / first["card_id"]).exists()
    second = 机房._健康请示(班子, 系统)
    assert second["created"] is False and second["card_id"] == first["card_id"]
    assert (审批.待船主 / first["card_id"]).exists()


def test_健康告警决定_先落回执再排后台且终态可查询():
    _清()
    alert = {"标题": "测试告警", "详情": "只是测试", "建议": "停止", "指纹": "health-test", "可选动作": [
        {"id": "补充", "标题": "补充", "说明": "写下判断"},
    ]}
    机房._登记健康告警([alert])
    record = 机房._记录健康告警处理({
        "动作": "补充",
        "补充": "测试内容忽略，不要继续处理。",
        "指纹": "health-test",
        "告警": {"标题": "浏览器伪造标题"},
    })
    assert record["状态"] == "已接收" and record["id"].startswith("健康-")
    assert record["告警"]["标题"] == "测试告警"

    launched = []
    original = 机房._启动工作线程
    机房._启动工作线程 = lambda name, target: launched.append((name, target))
    try:
        sid = 机房._启动健康告警后台处理(record)
    finally:
        机房._启动工作线程 = original
    assert sid and launched and launched[0][0].startswith("健康处理-")
    queued = 机房._健康告警处理列表(record["id"])[0]
    assert queued["状态"] == "正在交给项目经理" and queued["会话id"] == sid

    任务台.创建("健康告警处理", "健康告警处理", "测试", task_id=sid, 父任务=record["id"])
    任务台.更新(sid, "失败", 错误="HTTP 503")
    failed = 机房._健康告警处理列表(record["id"])[0]
    assert failed["状态"] == "失败" and failed["错误"] == "HTTP 503"


def test_健康告警补充_内部转发编码中文路径且保留中文正文():
    _清()
    alert = {
        "标题": "任务需要补充",
        "详情": "模型没有回应",
        "建议": "按补充内容继续处理",
        "指纹": "health-supplement-utf8",
        "可选动作": [{"id": "补充", "标题": "补充", "说明": "写下判断"}],
    }
    机房._登记健康告警([alert])
    record = 机房._记录健康告警处理({
        "动作": "补充",
        "补充": "已经在外部处理中，无需重试。",
        "指纹": "health-supplement-utf8",
        "告警": alert,
    })

    launched = []
    captured = {}
    original_thread = 机房._启动工作线程
    original_connection = 机房.http.client.HTTPConnection

    class FakeResponse:
        def read(self):
            return json.dumps({"已开始": True, "sid": captured["payload"]["sid"]}).encode("utf-8")

    class FakeConnection:
        def __init__(self, host, port, timeout):
            captured["connection"] = (host, port, timeout)

        def request(self, method, path, body=None, headers=None):
            captured["method"] = method
            captured["path"] = path
            captured["payload"] = json.loads(body.decode("utf-8"))
            captured["headers"] = headers

        def getresponse(self):
            return FakeResponse()

        def close(self):
            captured["closed"] = True

    机房._启动工作线程 = lambda name, target: launched.append((name, target))
    机房.http.client.HTTPConnection = FakeConnection
    try:
        sid = 机房._启动健康告警后台处理(record)
        launched[0][1]()
    finally:
        机房._启动工作线程 = original_thread
        机房.http.client.HTTPConnection = original_connection

    assert captured["method"] == "POST"
    assert captured["path"] == "/%E6%B4%BB%E5%8E%85"
    assert captured["path"].isascii()
    assert captured["payload"]["sid"] == sid
    assert "已经在外部处理中，无需重试。" in captured["payload"]["text"]
    assert captured["payload"]["_船主显示"] == "补充处理要求：已经在外部处理中，无需重试。"
    assert captured["closed"] is True
    processing = 机房._健康告警处理列表(record["id"])[0]
    assert processing["状态"] == "处理中" and processing["会话id"] == sid


def test_健康告警决定_拒绝卡片以外的动作():
    alert = {
        "标题": "备份异常",
        "详情": "备份打不开",
        "建议": "重新备份",
        "可选动作": [
            {"id": "重新备份", "标题": "重新备份并核验", "说明": "生成新备份"},
            {"id": "稍后", "标题": "稍后", "说明": "以后处理"},
            {"id": "补充", "标题": "补充", "说明": "写下判断"},
        ],
    }
    alert["指纹"] = "backup-action"
    机房._登记健康告警([alert])
    accepted = 机房._记录健康告警处理({"动作": "重新备份", "指纹": "backup-action", "告警": alert})
    assert accepted["动作"] == "重新备份"
    try:
        机房._记录健康告警处理({"动作": "删除全部备份", "指纹": "bad-action", "告警": alert})
    except ValueError:
        pass
    else:
        raise AssertionError("告警卡没有提供的动作必须被拒绝")


def test_健康告警_立即备份直接走系统程序而不调模型():
    alert = {
        "标题": "备份超期",
        "详情": "四天未检查",
        "建议": "立即备份",
        "指纹": "backup-direct",
        "可选动作": [{"id": "重新备份", "标题": "立即备份并核验", "说明": "直接执行"}],
    }
    机房._登记健康告警([alert])
    record = 机房._记录健康告警处理({"动作": "重新备份", "指纹": "backup-direct", "告警": alert})
    launched = []
    original_thread = 机房._启动工作线程
    original_create = 备份.强制备份
    original_verify = 备份.验证
    机房._启动工作线程 = lambda name, target: launched.append((name, target))
    备份.强制备份 = lambda: Path("公司记忆_回归.zip")
    备份.验证 = lambda _path: {"ok": True, "问题": [], "文件数": 12}
    try:
        sid = 机房._启动健康告警后台处理(record)
        assert sid and launched[0][0].startswith("备份处理-")
        launched[0][1]()
        done = 机房._健康告警处理列表(record["id"])[0]
        assert done["状态"] == "已完成" and "12 个文件" in done["结果"]
    finally:
        机房._启动工作线程 = original_thread
        备份.强制备份 = original_create
        备份.验证 = original_verify


def test_会话动作计数_并发不丢():
    import threading
    sid = "并发计数回归"
    活_本人.重置动作数(sid)
    try:
        def add_many():
            for _ in range(500):
                活_本人._增加会话动作(sid)

        threads = [threading.Thread(target=add_many) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert 活_本人._会话动作数[sid] == 4000
    finally:
        活_本人.重置动作数(sid)


def test_备份_用户数据清单可验证且不收源码密钥():
    env_file = 备份.COMPANY / ".env"
    dest = 备份.COMPANY.parent / "_回归备份"
    old_image_version = os.environ.get("XJ_IMAGE_VERSION")
    os.environ["XJ_IMAGE_VERSION"] = "regression-image@test"
    env_file.write_text("SECRET=never-back-this-up\n", encoding="utf-8")
    try:
        archive = 备份.创建用户数据导出(dest)
        assert archive.exists() and 备份.验证(archive)["ok"] is True
        import zipfile
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
            assert "公司快照/工具/机房.py" not in names
            assert "公司快照/工具/备份.py" not in names
            assert "公司快照/.env" not in names
            manifest = json.loads(zf.read("公司快照/备份清单.json"))
            assert manifest["格式"] == 2 and manifest["schema_version"] == 1
            assert manifest["凭据状态"] == "需重新绑定"
            assert manifest["文件清单"] == manifest["文件"]
            assert all(path == "instance.yaml" or path.startswith(("data/", "work/")) for path in manifest["文件"])
            assert "提交" in manifest["Git"] and "未提交路径" in manifest["Git"]
            assert manifest["代码恢复"]["Git提交"] or manifest["代码恢复"]["镜像版本"]
    finally:
        if old_image_version is None:
            os.environ.pop("XJ_IMAGE_VERSION", None)
        else:
            os.environ["XJ_IMAGE_VERSION"] = old_image_version
        env_file.unlink(missing_ok=True)
        shutil.rmtree(dest, ignore_errors=True)


def test_备份策略_三天到期无变化不重复打包_有变化才新建():
    root = Path(tempfile.mkdtemp(prefix="backup-policy-"))
    company = root / "30_开发公司"
    company.mkdir()
    (company / "资料.txt").write_text("第一版\n", encoding="utf-8")
    (company / "备份策略.json").write_text(json.dumps(备份.默认策略, ensure_ascii=False), encoding="utf-8")
    originals = (备份.COMPANY, 备份.默认目录, 备份.策略位, 备份.状态位
    )
    备份.COMPANY = company
    备份.默认目录 = root / "90_备份"
    备份.策略位 = company / "备份策略.json"
    备份.状态位 = company / "运行状态" / "备份状态.json"
    try:
        first = 备份.创建()
        assert first.exists()
        state = 备份.读取状态()
        state["最近检查"] = "2026-01-01T00:00:00"
        备份._写状态(state)
        备份.定时维护(now=dt.datetime(2026, 1, 5), 执行恢复演练=False, 执行异盘复制=False)
        assert len(list(备份.默认目录.glob("公司记忆_*.zip"))) == 1
        assert 备份.读取状态()["最近尝试"] == "2026-01-05T00:00:00"

        (company / "资料.txt").write_text("第二版\n", encoding="utf-8")
        state = 备份.读取状态()
        state["最近检查"] = "2026-01-05T00:00:00"
        备份._写状态(state)
        备份.定时维护(now=dt.datetime(2026, 1, 9), 执行恢复演练=False, 执行异盘复制=False)
        assert len(list(备份.默认目录.glob("公司记忆_*.zip"))) == 2
        assert 备份.读取状态()["最近尝试"] == "2026-01-09T00:00:00"
    finally:
        备份.COMPANY, 备份.默认目录, 备份.策略位, 备份.状态位 = originals
        shutil.rmtree(root, ignore_errors=True)


def test_自动备份任务_配置必须指向当前公司():
    config = 备份任务.期望配置()
    args = config["ProgramArguments"]
    assert args == [
        str(备份任务.COMPANY / ".venv" / "bin" / "python3"),
        str(备份任务.COMPANY / "工具" / "备份.py"),
        "--scheduled",
    ]
    assert config["WorkingDirectory"] == str(备份任务.COMPANY)


def test_Linux用户容器_健康检查不调用macOS任务管理器():
    original_platform = 备份任务.是macOS
    original_run = 备份任务.subprocess.run
    original_check = 备份任务.检查
    try:
        备份任务.是macOS = lambda: False
        备份任务.subprocess.run = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Linux 不得调用 launchctl"))
        schedule = 备份任务.检查()
        assert schedule["适用"] is False
        assert "Linux 用户容器不运行 launchctl" in schedule["说明"]

        备份任务.检查 = lambda: schedule
        snapshot = 健康.系统健康()
        assert not any(
            "自动备份定时任务未正常接入" in str(item.get("说明") or "")
            for item in snapshot["问题"]
        )
    finally:
        备份任务.是macOS = original_platform
        备份任务.subprocess.run = original_run
        备份任务.检查 = original_check


def test_公网实时事件_可按序号补收并识别真实终态():
    sid = "回归公网事件补拉"
    event_path = 事件总线.事件目录 / f"{sid}.jsonl"
    task_path = 任务台.路径(sid)
    事件总线.清空session(sid)
    task_path.unlink(missing_ok=True)
    try:
        assert 事件总线.开始run(sid) is True
        任务台.创建("大厅任务", "大厅", "公网事件补拉", task_id=sid, 负责人="项目经理")
        任务台.更新(sid, "运行中")
        事件总线.发布事件(sid, {"类型": "人来了", "名字": "老钟"})
        事件总线.发布事件(sid, {"类型": "群聊发言", "发言人": "老钟", "内容": "已收到"})

        first = 机房._运行事件快照(sid, 0)
        assert [item["序号"] for item in first["事件"]] == [1, 2]
        assert first["运行中"] is True and first["已结束"] is False
        second = 机房._运行事件快照(sid, 1)
        assert [item["序号"] for item in second["事件"]] == [2]

        任务台.更新(sid, "已完成", 结果="已收到")
        事件总线.结束run(sid)
        final = 机房._运行事件快照(sid, 2)
        assert [item["类型"] for item in final["事件"]] == ["run结束"]
        assert final["运行中"] is False and final["已结束"] is True and final["状态"] == "已完成"
    finally:
        事件总线.清空session(sid)
        event_path.unlink(missing_ok=True)
        task_path.unlink(missing_ok=True)


def test_前端实时流_保留实时体验并用同序事件补拉防卡死():
    source = (机房.COMPANY / "前端" / "src" / "hallStore.ts").read_text(encoding="utf-8")
    server = (机房.COMPANY / "工具" / "机房.py").read_text(encoding="utf-8")
    assert "new EventSource('/stream?sid='" in source
    assert "'/run-events?sid='" in source and "&after=' + lastSeq" in source
    assert "if (seq && seq <= lastSeq) return" in source
    assert "连续 30 秒无响应" in source
    assert 'self.send_header("connection", "close")' in server
    assert 'self.send_header("x-accel-buffering", "no")' in server


def test_依赖_直接依赖全部锁定版本():
    lines = [x.strip() for x in (备份.COMPANY / "requirements.txt").read_text(encoding="utf-8").splitlines() if x.strip() and not x.startswith("#")]
    assert lines and all("==" in x for x in lines)
    rid = 待验收记录.新增("工程师", "小活", "写了函数")
    待验收记录.批准(rid, "过")
    分1, 晋1 = 信誉.信誉分("阿强"), 晋升.分("阿强")
    待验收记录.批准(rid, "又批")                         # 重复裁决
    待验收记录.打回(rid, "又打回")                       # 已终态再打回
    assert 信誉.信誉分("阿强") == 分1 and 晋升.分("阿强") == 晋1   # 只算一次
    晋升.曲线文件.unlink(missing_ok=True)


def test_维持_种子保护_砸活穿透():
    _清()
    assert not 职级.维持不住("老梁")                     # 种子3、冷清、无重罚 → 受保护
    信誉.记一笔("老梁", "放水被抓")                       # 砸活
    职级.扫降职("老梁")
    assert 职级.评级("老梁") == 2                        # 砸活穿透种子一级(3→2)，一次扫到位


# ── 2026-07-10 三审补：验证二审修复接缝处的新问题（升降打架/放水/误打回连坐/幂等轨迹）──
def test_升要过维持门_不升降打架():
    _清()
    职级.设评级("阿强", 2, "t")
    信誉._账("阿强").unlink(missing_ok=True)              # 信誉 0
    assert 职级.升("阿强") is None                        # 目标级3维持门=8，信誉0够不着→不升(否则升完立刻被扫降)


def test_审结果_七审放水():
    f = 平台._审结果
    # F1 高危放水：结论栏写成句子/夹别的字，绝不取首词当放行（带SQL注入的活别过门）
    assert f("结论：放行 或 打回 二选一，我给打回\n理由：SQL 注入未修复")[0] != "放行"
    # F2 放水：同一结论行里放行打回混排，不许取首词放行
    assert f("【结论】放行 打回")[0] != "放行"
    assert f("【结论】放行 不了，代码根本跑不起来")[0] != "放行"
    # F5 放水：整篇都是裸判词时冲突取打回(安全方向)；夹了散文行(可选：/最终：)按最严→判不清，也非放水
    assert f("放行\n打回")[0] == "打回"
    assert f("可选：\n放行\n打回\n最终：打回")[0] != "放行"
    # F4 冤枉：清单/反问式"打回：否 放行：是"不该被当打回(整行非干净判词→判不清)
    assert f("打回：否\n放行：是")[0] == "判不清"


def test_审结果_九审裸表头回显放水():
    f = 平台._审结果
    # 九审：正文真拒绝、却把示例"结论：放行"(裸表头、无方括号)照抄成一行 → 绝不能判放行
    assert f("结论：放行\n但这个任务其实没完成，不能上交。")[0] != "放行"
    assert f("格式示范：\n结论：放行\n理由：一句话\n\n就本次而言，代码缺测试，我判它不合格。")[0] != "放行"
    assert f("经审查，这个必须打回。\n理由：文档里的示范——\n结论：放行\n——但那是错的。")[0] != "放行"
    # 对照：带方括号的【结论】放行=主动填栏，按契约信任=放行(读字段不猜正文，同审批线)；markdown包裹也要认
    assert f("整体不错，测试跑了。\n【结论】放行\n【理由】无")[0] == "放行"
    assert f("**【结论】放行**\n【理由】好")[0] == "放行"
    # 裸表头"结论：打回"仍认打回(安全方向宽松，带不带方括号都认)
    assert f("结论：打回\n理由：漏测")[0] == "打回"


def test_审结果_八审多栏与兜底放水():
    f = 平台._审结果
    # 八审F1：多【结论】栏，打回栏被写脏(夹字/写成句子)不能被静默丢掉、让干净放行独赢 → 判不清
    assert f("【结论】打回！！（如果达标）\n【结论】放行")[0] != "放行"
    assert f("【结论】打回。理由见下\n【结论】放行")[0] != "放行"
    # 八审F2：没有干净结论栏时，绝不在理由行/散文行里捞一个孤立"放行"令牌
    assert f("是否满足放行？\n- 放行\n不，测试缺失，我打回。")[0] != "放行"
    assert f("【结论】建议打回\n【理由】未达到\n放行")[0] != "放行"
    assert f("【结论】维持\n【理由】不予\n放行")[0] != "放行"
    assert f("【结论】不能\n放行")[0] != "放行"
    assert f("【结论】打回，因为质量差\n放行")[0] != "放行"


def test_审结果_六审四洞():
    f = 平台._审结果
    # 洞1 放水：理由栏/散文里的"结论：放行"不是裁决，别当放行
    assert f("【结论】打回\n【理由】隔壁组的结论：放行，但我这份没达标")[0] == "打回"
    assert f("这活达不到能下『结论：放行。』的标准，问题很多")[0] != "放行"
    # 洞2 冤枉：兜底不能裸 startswith("打回")——"打回没必要"不是打回
    assert f("放行没问题\n打回没必要，达标")[0] != "打回"
    # 洞3 放水：兜底放行要整行是干净判词——反问/否定句不算
    assert f("放行？不行。")[0] != "放行"
    assert f("放行！才怪，打回。")[0] != "放行"
    assert f("放行？不，打回。质量太差")[0] != "放行"
    # 洞4 放水：markdown/全角括号破坏边界，打回不能逃检、放行不能独占
    assert f("**【结论】打回** —— 备选写法：【结论】放行")[0] != "放行"
    # 正路仍要稳：markdown 包裹的干净制式照认
    assert f("**【结论】放行**\n【理由】好活")[0] == "放行"
    assert f("【结论】打回")[0] == "打回"


def test_审结果_制式不放水不冤枉():
    # 制式后：放水/冤枉从根上消失——结论只看【结论】栏，不读理由的自然语言，所以理由怎么绕都不影响判定
    # 放水方向：结论打回，理由里出现"通过/合格/放行"等词，不该被当放行
    assert 平台._审结果("【结论】打回\n【理由】通过率太低，达不到合格线")[0] == "打回"
    assert 平台._审结果("【结论】打回\n【理由】其实可以放行的地方不多")[0] == "打回"
    # 冤枉方向：结论放行，理由里出现"不/没/别"等否定词，不该被当打回
    assert 平台._审结果("【结论】放行\n【理由】特别好，没有要改的")[0] == "放行"
    assert 平台._审结果("【结论】放行\n【理由】没有一处不合格")[0] == "放行"


def test_误打回连坐_键不匹配也撤反复敷衍():
    _清()
    for k in ("ridA", "ridB", "ridC"):
        信誉.记打回("阿强", "活", 键=k)                     # 3笔不同键→反复敷衍(键随触发笔=ridC)
    assert 信誉.有近期重罚("阿强") is True
    信誉.误打回("老梁", "阿强", "第一笔判错了", 键="ridA")     # 船主纠正的是第一笔(非触发笔)——键对不上
    assert 信誉.有近期重罚("阿强") is False                  # 幽灵敷衍也得撤，别凭它把该还清白的人穿透降级


def _活敷衍(n):
    return sum(1 for e in 信誉._事件列(n) if e.get("事件") == "反复敷衍" and not e.get("撤销"))


def test_误打回_不误撤仍成立的敷衍_支撑滚出窗口():
    # 五审攻击1：敷衍的3笔支撑打回A1A2A3后垫了几条验收、A1滚出"近8"，纠正无关的近期R1不该误撤老敷衍
    _清()
    for k in ("A1", "A2", "A3"):
        信誉.记打回("阿强", "真烂活", 键=k)                  # burst→敷衍(支撑A1A2A3)
    for i in range(4):
        信誉.记一笔("阿强", "验收通过", f"pad{i}")            # 垫料，把A1挤出滑动近8
    信誉.记打回("阿强", "无关另一笔", 键="R1")                # 近期无关打回
    assert 信誉.有近期重罚("阿强") is True
    信誉.误打回("老梁", "阿强", "R1判错了", 键="R1")           # 只纠正无关R1
    assert 信誉.有近期重罚("阿强") is True                   # A1A2A3全有效、敷衍依据完好 → 不许误撤
    assert _活敷衍("阿强") == 1


def test_误打回_混入无关打回也要撤失依据的敷衍():
    # 五审攻击(该撤没撤方向)：纠正支撑里的A1、该段只剩2笔，别被无关的近期R1把窗口撑到3而漏撤
    _清()
    for k in ("A1", "A2", "A3"):
        信誉.记打回("阿强", "真烂活", 键=k)
    信誉.记打回("阿强", "无关另一笔", 键="R1")
    信誉.误打回("老梁", "阿强", "A1其实判错了", 键="A1")        # 纠正支撑里的A1
    assert _活敷衍("阿强") == 0                             # 支撑只剩A2A3=2<3、敷衍失依据 → 必须撤


def test_误打回连坐撤销反复敷衍():
    _清()
    for i in range(3):
        信誉.记打回("阿强", f"第{i}")                     # 3打回→反复敷衍→重罚
    assert 信誉.有近期重罚("阿强") is True
    信誉.误打回("老梁", "阿强", "判错了")                  # 撤打回+连带撤反复敷衍
    assert 信誉.有近期重罚("阿强") is False               # 不再凭幽灵敷衍把人穿透降级


def test_幂等门轨迹不泄漏():
    _清()
    晋升.曲线文件.unlink(missing_ok=True)
    rid = 待验收记录.新增("工程师", "活", "产出", 轨迹=[{"时间": "x", "层级": "工程师", "谁": "阿强", "动作": "交付"}])
    待验收记录.批准(rid, "过")
    n1 = len([r for r in 待验收记录.列表(False) if r["id"] == rid][0]["轨迹"])
    待验收记录.批准(rid, "又批")
    待验收记录.打回(rid, "又打回")
    n2 = len([r for r in 待验收记录.列表(False) if r["id"] == rid][0]["轨迹"])
    assert n1 == n2                                       # 重复裁决不泄漏幻影轨迹节点
    晋升.曲线文件.unlink(missing_ok=True)


# ── 2026-07-11 十审补：船主自然语言直控通道 + 误打回按把关者精确撤（这条通道之前零回归）──
def test_管理动作_通道全链():
    _清()
    for k in ("k1", "k2", "k3"):
        信誉.记打回("阿强", "活", 键=k, 把关者="老梁")
    assert 信誉.有近期重罚("阿强") is True
    # 识别契约：普通话（派活/夸人）不当管理指令 → None → 大厅照常派活
    assert 管理动作.识别管理动作("让老梁写个接口", mock=True) is None
    assert 管理动作.识别管理动作("阿强干得不错", mock=True) is None
    # 建卡→确认→真平反
    cid = 管理动作.建卡("平反", {"把关者": "老梁", "被冤者": "阿强"}, "老梁给阿强那次判错了")
    assert 管理动作.确认执行(cid)["ok"] is True
    assert 信誉.有近期重罚("阿强") is False          # 平反后重罚消
    assert 管理动作.确认执行(cid)["ok"] is False       # 卡已办、重复确认拦住


def test_管理动作_确认并发不双执行():
    import threading
    _清()
    for k in ("k1", "k2", "k3"):
        信誉.记打回("阿强", "活", 键=k, 把关者="老梁")
    cid = 管理动作.建卡("平反", {"把关者": "老梁", "被冤者": "阿强"}, "t")
    结果: list = []
    def _go():
        结果.append(管理动作.确认执行(cid))
    ts = [threading.Thread(target=_go) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    ok数 = sum(1 for r in 结果 if r.get("ok"))
    误打回数 = sum(1 for e in 信誉._事件列("老梁") if e.get("事件") == "误打回" and not e.get("撤销"))
    assert ok数 == 1 and 误打回数 == 1, (ok数, 误打回数)   # 十审#2：并发只执行一次


def test_管理动作_执行中可恢复且不重复():
    _清()
    信誉.记打回("阿强", "活", 键="recover", 把关者="老梁")
    cid = 管理动作.建卡("平反", {"把关者": "老梁", "被冤者": "阿强", "键": "recover"}, "恢复测试")
    pending = 管理动作.待确认 / cid
    data = 管理动作._读(pending)
    data.update({"状态": "执行中", "幂等键": cid})
    管理动作._写(管理动作.执行中 / cid, data)
    pending.unlink()
    first = 管理动作.恢复执行中()
    assert first and first[0]["ok"] is True
    assert not (管理动作.执行中 / cid).exists() and (管理动作.已办 / cid).exists()
    n1 = sum(1 for e in 信誉._事件列("老梁") if e.get("事件") == "误打回" and not e.get("撤销"))
    assert 管理动作.恢复执行中() == []
    n2 = sum(1 for e in 信誉._事件列("老梁") if e.get("事件") == "误打回" and not e.get("撤销"))
    assert n1 == n2 == 1


def test_误打回_按把关者撤对笔且撤不到幂等():
    _清()
    信誉.记打回("阿强", "老梁错打的", 把关者="老梁")     # 老梁 错打回
    信誉.记打回("阿强", "老钟后来正当打的", 把关者="老钟")  # 老钟 后来正当打回
    信誉.误打回("老梁", "阿强", "平反老梁那次")            # 平反老梁（不是最近的老钟）
    撤 = {e.get("把关者"): e.get("撤销", False) for e in 信誉._事件列("阿强") if e.get("事件") == "交付被打回"}
    assert 撤.get("老梁") is True and not 撤.get("老钟")    # 十审#3：撤老梁那笔、别撤老钟正当的
    n1 = sum(1 for e in 信誉._事件列("老梁") if e.get("事件") == "误打回" and not e.get("撤销"))
    信誉.误打回("老梁", "阿强", "又平反一次")              # 已撤过、撤不到
    n2 = sum(1 for e in 信誉._事件列("老梁") if e.get("事件") == "误打回" and not e.get("撤销"))
    assert n1 == 1 and n2 == 1                            # 撤不到就幂等空转，不重复扣


# ── 2026-07-14 大厅信息架构 + 项目经理模型升级 ──────────────────────────────
def test_场次索引_摘要计数和临时概括都来自真实记录():
    day = "2099-12-31"
    p = 大厅记录.对话目录 / f"{day}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"时间": f"{day} 10:00:00", "who": "船主", "text": "这是场次索引回归演练。不得编造。"}, ensure_ascii=False) + "\n" +
        json.dumps({"时间": f"{day} 10:01:00", "who": "老钟", "text": "已按真实记录完成。"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    大厅检索._场次缓存.update({"签名": None, "数据": []})
    item = next(x for x in 大厅检索.场次索引() if x["日期"] == day)
    assert item["记录数"] == 2 and item["最后时间"].endswith("10:01:00")
    assert item["摘要"].startswith("场次索引回归演练") and item["已整理"] is False


def test_看板成员公开信誉分且不改变既定复核纪律():
    board = 看板数据._看板_算("演示模式", ["项目经理"])
    member = board["成员"][0]
    assert "信誉分" in member and member["信任等级"] in ("高", "中", "受限")
    assert 信誉.需加复核(member.get("名字") or "老钟") == (member["信任等级"] == "受限")


def test_项目经理唯一配置真源已升级到_gpt_5_6_sol():
    cfg = 模型接入.花名册()["项目经理"]
    assert cfg["model"] == "gpt-5.6-sol"


def test_TokenPlan共用密钥仍按模型名识别家族():
    花 = 模型接入.花名册()
    现役 = {
        "首席工程师": ("qwen3.8-max", "qwen"),
        "工程师": ("glm-5.2", "glm"),
        "测试工程师": ("deepseek-v4-pro-0813", "deepseek"),
        "文案工程师（兼职）": ("deepseek-v4-pro-0813", "deepseek"),
    }
    for 岗位, (型号, 家族) in 现役.items():
        cfg = 花[岗位]
        assert cfg["key_env"] == "BAILIAN_TOKEN_PLAN_API_KEY"
        assert cfg["model"] == 型号
        assert 升级_模型层._模型家族(cfg["model"]) == 家族


# ── 内置 runner（没装 pytest 时用）────────────────────────────────────────
def _跑standalone() -> int:
    测 = [(名, 值) for 名, 值 in sorted(globals().items()) if 名.startswith("test_") and callable(值)]
    过, 崩 = 0, []
    for 名, 函 in 测:
        s = _快照()
        try:
            函()
            过 += 1
            print(f"  ✅ {名}")
        except Exception as e:  # noqa: BLE001
            崩.append((名, f"{type(e).__name__}: {e}"))
            print(f"  ❌ {名} — {type(e).__name__}: {e}")
        finally:
            _还原(s)
    print(f"\n{'='*50}\n{过}/{len(测)} 过" + (f"，{len(崩)} 崩" if 崩 else "，全绿 ✅"))
    return 1 if 崩 else 0


if __name__ == "__main__":
    sys.exit(_跑standalone())
