#!/usr/bin/env python3
"""第5步 · 办公室桥接:让验证过"舒服"的旧窗户(办公室.py/页面.js)接到新后端内核(升级_圆桌/执行)。

办公室.py 只需把局部的 `import 引擎` 换成 `import 升级_办公室适配 as 引擎`,
所有动作(立项/确认开工/急停…)即全部走新内核(各厂官方模型+权限白名单+命脉执行);
读取/显示(看板数据/页面.js/会议.待确认列表)不变——新内核复用 会议.py/引擎工具 落盘,格式兼容。

接口与旧 引擎.py 同名同签名,故是 drop-in 替换。
"""
from __future__ import annotations

from typing import Any

import 引擎工具 as 巧  # noqa: E402
import 升级_圆桌  # noqa: E402
import 升级_执行  # noqa: E402


def 初始化() -> None:
    # 急停=瞬时停止键,不是长状态:办公室一启动就清掉残留信号(上次没正常收尾留下的),不跨重启赖着。
    try:
        巧.急停文件.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        import 会议
        会议.初始化()
    except Exception:  # noqa: BLE001
        pass


def 发起任务(需求: str, progress=None, 插话源=None) -> dict[str, Any]:
    """大厅立项 → 新圆桌:各厂模型五岗开会 → 动态收敛 → 缺证据不编造 → 出待确认工单。

    插话源:债②持续会话用——一个 callable,返回本步船主新插话列表(事件总线队列);办公室 /trigger 传入。
    瞬时停止键:开会开始/结束 让 停止() 知道"有会在开",并在收尾时若因停止停下则清旗复位。
    """
    升级_执行.开会开始()
    try:
        return 升级_圆桌.运行(需求, progress=progress, 插话源=插话源)
    finally:
        升级_执行.开会结束()


def 确认开工(names: list[str] | None = None) -> list:
    """确认开工 → 新执行命脉:各厂模型干活 → 白名单门禁 → 验收命令 → 真复盘进记忆 → 正式报告。"""
    if not names:
        import 会议
        names = [x["name"] for x in 会议.待确认列表()]
    return 升级_执行.运行(list(names))


def 设置急停(on: bool) -> str:
    return 升级_执行.设置急停(on)


def 停止() -> str:
    """船主左栏「停止」瞬时键:终止当前全部在跑工作,收尾后自动恢复待命,不留长状态。"""
    return 升级_执行.停止()


def 状态() -> dict[str, Any]:
    return {"急停": 巧.急停文件.exists()}


def 继续任务(name: str, extra_steps: int = 4) -> str:
    """新执行是一次性闭环;超迭代会熔断并发请示,批准续步=重新确认开工该工单。"""
    return f"新执行为一次性闭环：{name} 若熔断未完成,在右栏回炉/请示中批准后重新确认开工即可续跑。"


def 停止任务(name: str) -> str:
    停止()  # 瞬时停止键:终止当前在跑工作,收尾后自动待命(不再留"需手动解除"的长状态)
    return f"已停止当前进行中的工作(含 {name})，收尾后公司即恢复待命。"


def 追加指示(name: str, text: str) -> str:
    try:
        p = 巧.任务路径(name)
        if p and p.exists():
            巧.写日志(巧.日志(p), f"船主追加指示：{text}")
    except Exception:  # noqa: BLE001
        pass
    return f"已记录追加指示到 {name}（下轮执行会读到）。"
