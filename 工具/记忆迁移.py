#!/usr/bin/env python3
"""死平台 · 记忆迁移 —— 调任换部门时，把人的个人记忆册子搬到新部门目录（合并、不丢）。

只在『调任』（同级换职能）时用；升降职不换部门、不迁记忆。
调任的『源目录』必须在设置调任覆盖『之前』算好传进来——否则部门解析已经指向新部门，就找不到旧册子了。
"""
from __future__ import annotations

from pathlib import Path

from 根 import 数据根

COMPANY = 数据根


def 迁移个人记忆(人名: str, 源目录: Path, 目标部门目录名: str) -> str:
    """把 <人名>.md 从『源目录』搬到『目标部门目录』。目标已有同名册子则合并追加（带分隔、不覆盖）。
    原子写目标后再删源，中途崩不会两头丢。返回一句结果说明。"""
    人名 = str(人名 or "").strip()
    目标名 = str(目标部门目录名 or "").strip()
    if not 人名 or not 目标名:
        return "缺人名/目标部门，跳过迁移"
    源 = Path(源目录) / f"{人名}.md"
    目标目录 = COMPANY / 目标名
    目标目录.mkdir(parents=True, exist_ok=True)
    目标 = 目标目录 / f"{人名}.md"
    if 源.resolve() == 目标.resolve():
        return f"{人名} 已在 {目标名}，无需迁"
    if not 源.exists():
        return f"{人名} 无旧记忆册子，无需迁"
    内容 = 源.read_text(encoding="utf-8")
    if 目标.exists():
        旧 = 目标.read_text(encoding="utf-8")
        if 内容.strip() and 内容.strip() in 旧:   # 3L2：已并入过(上次崩在replace后、unlink前)——重跑别再并一遍，直接删源
            源.unlink(missing_ok=True)
            return f"{人名} 记忆已在 {目标名}（重跑幂等，未重复并入）"
        新 = 旧.rstrip() + f"\n\n<!-- 调任并入：来自 {源.parent.name} -->\n\n" + 内容
    else:
        新 = 内容
    临时 = 目标.with_suffix(".md.tmp")
    临时.write_text(新, encoding="utf-8")
    临时.replace(目标)      # 原子：目标先落定
    源.unlink(missing_ok=True)   # 再删源，两头不丢
    return f"{人名} 记忆已迁 {源.parent.name}→{目标名}"
