#!/usr/bin/env python3
"""房间注册台：自动发现房间清单、校验契约并按场景装配工具。"""
from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path
from typing import Any

from 根 import 数据根

COMPANY = 数据根
清单目录 = Path(__file__).resolve().parent / "房间"
_加载失败: dict[str, str] = {}
_必填 = ("id", "名称", "类型", "实现", "入口", "输入契约", "输出契约", "留痕", "权限边界", "场景")
_类型 = {"场所", "能力", "资料", "治理"}


def 各房间() -> list[dict[str, Any]]:
    rooms: list[dict[str, Any]] = []
    _加载失败.clear()
    importlib.invalidate_caches()
    for item in pkgutil.iter_modules([str(清单目录)]):
        if item.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"房间.{item.name}")
            spec = dict(getattr(mod, "房间"))
            spec["清单模块"] = item.name
            rooms.append(spec)
        except Exception as e:  # noqa: BLE001
            _加载失败[item.name] = f"{type(e).__name__}: {e}"
    return sorted(rooms, key=lambda x: str(x.get("id") or ""))


def _出口工具(spec: dict[str, Any]) -> list:
    export = str(spec.get("工具出口") or "").strip()
    if not export:
        return []
    mod = importlib.import_module(str(spec["实现"]))
    factory = getattr(mod, export)
    value = factory()
    return value if isinstance(value, list) else [value]


def 校验() -> list[str]:
    errors: list[str] = []
    rooms = 各房间()
    for name, error in _加载失败.items():
        errors.append(f"房间清单『{name}.py』加载失败：{error}")
    ids: dict[str, list[str]] = {}
    names: dict[str, list[str]] = {}
    tool_owners: dict[str, list[str]] = {}
    for spec in rooms:
        label = str(spec.get("名称") or spec.get("清单模块") or "?")
        for key in _必填:
            if key not in spec or spec[key] in (None, ""):
                errors.append(f"房间『{label}』缺契约字段『{key}』")
        rid = str(spec.get("id") or "")
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", rid):
            errors.append(f"房间『{label}』id 必须是稳定英文标识：{rid or '空'}")
        ids.setdefault(rid, []).append(label)
        names.setdefault(label, []).append(rid)
        if spec.get("类型") not in _类型:
            errors.append(f"房间『{label}』类型无效：{spec.get('类型')}")
        try:
            mod = importlib.import_module(str(spec.get("实现") or ""))
            entries = spec.get("入口") or []
            if not isinstance(entries, list) or not entries:
                errors.append(f"房间『{label}』入口必须是非空列表")
            else:
                for entry in entries:
                    if not callable(getattr(mod, str(entry), None)):
                        errors.append(f"房间『{label}』实现缺入口：{spec.get('实现')}.{entry}")
            exported = _出口工具(spec)
            exported_names: set[str] = set()
            for tool in exported:
                name = str(getattr(tool, "name", "") or "")
                if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                    errors.append(f"房间『{label}』导出工具名无效：{name or '空'}")
                tool_owners.setdefault(name, []).append(label)
                exported_names.add(name)
            tool_scenes = spec.get("工具场景")
            if tool_scenes is not None:
                if not isinstance(tool_scenes, dict):
                    errors.append(f"房间『{label}』工具场景必须是 工具名→场景列表")
                else:
                    for tool_name, values in tool_scenes.items():
                        if tool_name not in exported_names:
                            errors.append(f"房间『{label}』工具场景引用了未导出的工具：{tool_name}")
                        if not isinstance(values, list) or any(x not in ("本人", "干活", "发言") for x in values):
                            errors.append(f"房间『{label}』工具『{tool_name}』场景只能是 本人/干活/发言")
        except Exception as e:  # noqa: BLE001
            errors.append(f"房间『{label}』实现/工具出口加载失败：{type(e).__name__}: {e}")
        path = (COMPANY / str(spec.get("留痕") or "")).resolve()
        if not path.is_relative_to(COMPANY.resolve()):
            errors.append(f"房间『{label}』留痕路径越出公司：{path}")
        scenes = spec.get("场景") or []
        if not isinstance(scenes, list) or any(x not in ("本人", "干活", "发言") for x in scenes):
            errors.append(f"房间『{label}』场景只能是 本人/干活/发言")
    for rid, owners in ids.items():
        if rid and len(owners) > 1:
            errors.append(f"房间 id『{rid}』被重复认领：{'、'.join(owners)}")
    for name, rids in names.items():
        if name and len(rids) > 1:
            errors.append(f"房间名称『{name}』重复：{'、'.join(rids)}")
    for name, owners in tool_owners.items():
        if name and len(set(owners)) > 1:
            errors.append(f"工具名『{name}』被多个房间导出：{'、'.join(sorted(set(owners)))}")
    return errors


def 装配工具(场景: str) -> list:
    problems = 校验()
    if problems:
        raise RuntimeError("房间注册校验失败，拒绝装配：" + "；".join(problems[:8]))
    tools = []
    for spec in 各房间():
        if 场景 in (spec.get("场景") or []):
            tool_scenes = spec.get("工具场景") or {}
            for tool in _出口工具(spec):
                name = str(getattr(tool, "name", "") or "")
                if 场景 in tool_scenes.get(name, spec.get("场景") or []):
                    tools.append(tool)
    return tools


def 快照() -> list[dict[str, Any]]:
    return [{k: v for k, v in room.items() if k != "清单模块"} for room in 各房间()]
