"""INSTANCE_CONFIG 的唯一加载与校验入口。"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

from 根 import 实例配置路径, 本机实例配置路径, 是远程实例


_本机默认: dict[str, Any] = {
    "schema_version": 1,
    "account_id": "local",
    "instance_id": "local",
    "owner": {
        "owner_id": "local-owner",
        "display_name": "本机主人",
        "称呼": "老板",
        "别名": ["老板", "先生"],
    },
    "公司目的": "服务本机主人当前交给公司的工作",
    "模型路由": {},
    "预算": {"月上限": None, "日上限": None},
    "配额": {"文件总量MB": 2048},
    "功能开关": {"统一搜索": True},
}

_固定员工 = {
    "emp_pm": "老钟",
    "emp_chief": "老梁",
    "emp_engineer": "阿强",
    "emp_test": "老纪",
    "emp_writer": "阿言",
}


def _文本(value: Any, field: str, *, max_len: int, required: bool = True) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise RuntimeError(f"INSTANCE_CONFIG 缺少 {field}")
    if len(text) > max_len:
        raise RuntimeError(f"INSTANCE_CONFIG 的 {field} 超过 {max_len} 字")
    if any(ord(ch) < 32 for ch in text) or "{" in text or "}" in text:
        raise RuntimeError(f"INSTANCE_CONFIG 的 {field} 含控制字符或花括号")
    return text


def _校验(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuntimeError("INSTANCE_CONFIG 顶层必须是 YAML 对象")
    if int(raw.get("schema_version") or 0) != 1:
        raise RuntimeError("INSTANCE_CONFIG 只支持 schema_version: 1")
    account_id = _文本(raw.get("account_id"), "account_id", max_len=64)
    instance_id = _文本(raw.get("instance_id"), "instance_id", max_len=64)
    if not account_id.startswith("acc_") or not instance_id.startswith("inst_"):
        raise RuntimeError("远程 account_id/instance_id 必须使用系统生成的 acc_/inst_ 前缀")

    owner = raw.get("owner")
    if not isinstance(owner, dict):
        raise RuntimeError("INSTANCE_CONFIG 缺少 owner 对象")
    owner_id = _文本(owner.get("owner_id"), "owner.owner_id", max_len=64)
    if not owner_id.startswith("own_"):
        raise RuntimeError("远程 owner.owner_id 必须使用系统生成的 own_ 前缀")
    display_name = _文本(owner.get("display_name"), "owner.display_name", max_len=32)
    call_name = _文本(owner.get("称呼"), "owner.称呼", max_len=32)
    aliases_raw = owner.get("别名") or []
    if not isinstance(aliases_raw, list):
        raise RuntimeError("INSTANCE_CONFIG 的 owner.别名 必须是数组")
    aliases = [_文本(x, "owner.别名[]", max_len=32) for x in aliases_raw]
    aliases_text = "、".join(aliases)
    if len(aliases_text) > 128:
        raise RuntimeError("INSTANCE_CONFIG 的 owner.别名 合计超过 128 字")

    company_purpose = _文本(raw.get("公司目的"), "公司目的", max_len=200)
    employees = raw.get("employees") or []
    if not isinstance(employees, list):
        raise RuntimeError("INSTANCE_CONFIG 的 employees 必须是数组")
    seen_employees: set[str] = set()
    for item in employees:
        if not isinstance(item, dict):
            raise RuntimeError("INSTANCE_CONFIG 的 employees[] 必须是对象")
        employee_id = _文本(item.get("employee_id"), "employees[].employee_id", max_len=32)
        name = _文本(item.get("人名"), "employees[].人名", max_len=32)
        if employee_id not in _固定员工 or name != _固定员工[employee_id]:
            raise RuntimeError("第一版不允许修改员工姓名或增加员工")
        if employee_id in seen_employees:
            raise RuntimeError(f"INSTANCE_CONFIG 员工重复：{employee_id}")
        seen_employees.add(employee_id)
    out = dict(raw)  # 未知字段原样保留，但本模块不会让它们生效。
    out.update({"schema_version": 1, "account_id": account_id, "instance_id": instance_id})
    out["owner"] = dict(owner)
    out["owner"].update({
        "owner_id": owner_id,
        "display_name": display_name,
        "称呼": call_name,
        "别名": aliases,
    })
    out["公司目的"] = company_purpose
    out["employees"] = [
        {"employee_id": employee_id, "人名": name, "display_name": name}
        for employee_id, name in _固定员工.items()
    ]
    return out


@lru_cache(maxsize=1)
def 读取实例配置() -> dict[str, Any]:
    if not 是远程实例():
        if 本机实例配置路径.is_file():
            raw = yaml.safe_load(本机实例配置路径.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("owner"), dict):
                raise RuntimeError(f"本机实例配置无效：{本机实例配置路径}")
            return raw
        return yaml.safe_load(yaml.safe_dump(_本机默认, allow_unicode=True))
    if not 实例配置路径.is_file():
        raise RuntimeError(f"远程实例缺少 INSTANCE_CONFIG：{实例配置路径}")
    return _校验(yaml.safe_load(实例配置路径.read_text(encoding="utf-8")))


def 实例字段(*path: str, 默认: Any = None) -> Any:
    value: Any = 读取实例配置()
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return 默认
        value = value[key]
    return value


def 主人ID() -> str:
    return str(实例字段("owner", "owner_id"))


def 主人显示名() -> str:
    return str(实例字段("owner", "display_name"))


def 主人称呼() -> str:
    return str(实例字段("owner", "称呼"))


def 主人别名() -> tuple[str, ...]:
    aliases = ["船主", 主人ID(), 主人显示名(), 主人称呼()]
    aliases.extend(str(x) for x in (实例字段("owner", "别名", 默认=[]) or []))
    return tuple(dict.fromkeys(x.strip() for x in aliases if x and x.strip()))


def 公司目的() -> str:
    return str(实例字段("公司目的"))
