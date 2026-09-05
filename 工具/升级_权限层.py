#!/usr/bin/env python3
"""权限层:当前派活/历史工单白名单 → 官方 PermissionEngine(替手写"写路径校验")。

- 白名单内的 Write/Edit 放行;白名单外默认要批(DEFAULT 模式下 = ASK,我们当"拦+请示")。
- 只读 Read/Grep 由工具自检放行。
- 比手写白名单强:5 模式 + 危险文件保护(.env/.ssh/.git 默认拒) + glob 路径匹配。
- 白名单来自当前派活参数或历史工单 meta,本层只做"白名单 → 权限上下文"的翻译。

自检:`cd 工具 && python3 升级_权限层.py`,看白名单内/外、只读各自的裁决。
"""
from __future__ import annotations

from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
)

写改工具 = ["Write", "Edit"]
只读工具 = ["Read", "Grep", "Glob"]


def 建权限上下文(
    白名单: list[str],
    *,
    模式: PermissionMode = PermissionMode.DEFAULT,
    名级放行工具: list[str] | None = None,
) -> PermissionContext:
    """当前派活/历史工单白名单路径列表 → 官方权限上下文。

    - 写改工具(官方 Write/Edit):白名单是**绝对路径 glob**(官方 match_rule 用 fnmatch 匹绝对 file_path);命中→ALLOW,未命中→引擎默认 ASK(拦)。
    - 只读工具(官方 Read/Grep/Glob):rule_content 空 = 引擎层 `not rule_content` 短路放行。
    - 名级放行工具:我们自带的 FunctionTool(run_check 只读验收 / request_review 请示),它们不override match_rule、无法按路径判,故工具名级放行(rule_content=None=匹配全部),其安全性由函数自身保证(只读/只写请示文件)。
    """
    allow: dict[str, list[PermissionRule]] = {}
    for tool in 写改工具:
        allow[tool] = [
            PermissionRule(tool_name=tool, rule_content=p, behavior=PermissionBehavior.ALLOW, source="白名单")
            for p in 白名单
        ]
    for tool in 只读工具:  # 只读放行(rule_content 空 = 匹配全部)
        allow[tool] = [PermissionRule(tool_name=tool, rule_content="", behavior=PermissionBehavior.ALLOW, source="只读放行")]
    for tool in (名级放行工具 or []):  # 自带 FunctionTool 工具名级放行(rule_content=None)
        allow[tool] = [PermissionRule(tool_name=tool, rule_content=None, behavior=PermissionBehavior.ALLOW, source="自带工具放行")]
    return PermissionContext(mode=模式, allow_rules=allow)


def 建引擎(白名单: list[str], **kw) -> PermissionEngine:
    return PermissionEngine(建权限上下文(白名单, **kw))


if __name__ == "__main__":
    import asyncio

    from agentscope.tool import Read, Write

    async def main() -> None:
        w = Write()
        keys = list(getattr(w, "input_schema", {}).get("properties", {}).keys())
        print("Write 输入字段:", keys)
        # 用 schema 里真实的路径字段名
        路径字段 = "file_path" if "file_path" in keys else (keys[0] if keys else "path")
        eng = 建引擎(["工单/进行中/**", "公司公告.md"])
        print(f"\n白名单=[工单/进行中/**, 公司公告.md](路径字段={路径字段}):")
        for path in ["工单/进行中/x.md", "公司公告.md", "秘密.txt", "../../etc/passwd"]:
            d = await eng.check_permission(w, {路径字段: path, "content": "x"})
            print(f"  写 {path:24} → {d.behavior.name}")
        r = Read()
        rk = list(getattr(r, "input_schema", {}).get("properties", {}).keys())
        rkey = "file_path" if "file_path" in rk else (rk[0] if rk else "path")
        d = await eng.check_permission(r, {rkey: "任意.md"})
        print(f"  读 任意.md{' ':15} → {d.behavior.name}（只读应放行）")

    asyncio.run(main())
