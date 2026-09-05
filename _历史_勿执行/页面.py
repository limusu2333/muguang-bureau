#!/usr/bin/env python3
"""办公室页面：只回 HTML 外壳。样式在 页面.css、交互在 页面.js（外部文件，
避免 f-string 花括号转义坑）。三栏布局与渲染全部由 页面.js 客户端构建。"""
from __future__ import annotations

import json
from pathlib import Path


def 页面(company: Path, roles: list[str], 模式: str, members: list[dict] | None = None) -> str:
    注入 = json.dumps(roles, ensure_ascii=False)
    成员 = json.dumps(members or [{"name": r, "title": r, "model": "未知"} for r in roles], ensure_ascii=False)
    模式j = json.dumps(模式, ensure_ascii=False)
    return (
        "<!doctype html><html lang=\"zh\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>XJ · 办公室</title><link rel=\"stylesheet\" href=\"/app.css\">"
        f"<script>window.XJ_ROLES={注入};window.XJ_MEMBERS={成员};window.XJ_MODE={模式j};</script></head><body>"
        "<header><span class=\"brand\">开发公司 · 办公室</span>"
        f"<span class=\"mode brand-mode\">{模式}</span><span class=\"spacer\"></span>"
        "<span class=\"mode\">为她而建 · 决策过程透明</span></header>"
        "<div id=\"app\"></div><div id=\"toasts\"></div>"
        "<script src=\"/app.js\"></script></body></html>"
    )
