"""macOS Keychain access. Secret values are never written to project files."""

from __future__ import annotations

import re
import subprocess


_实例引用 = re.compile(r"keychain://xj-multiuser/(acc_[0-9A-HJKMNP-TV-Z]{26}):(chat|search)", re.IGNORECASE)
_平台名称 = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")


class 凭据错误(RuntimeError):
    pass


class 凭据不存在(凭据错误):
    pass


def _security(args: list[str], *, secret_input: str | None = None) -> str:
    try:
        result = subprocess.run(
            ["/usr/bin/security", *args],
            input=secret_input,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise 凭据错误("macOS Keychain 当前不可用") from exc
    if result.returncode == 44:
        raise 凭据不存在("macOS Keychain 中没有该凭据")
    if result.returncode != 0:
        raise 凭据错误("macOS Keychain 拒绝了凭据操作")
    return result.stdout.rstrip("\n")


def _实例账户(ref: str) -> str:
    match = _实例引用.fullmatch(str(ref or ""))
    if not match:
        raise 凭据错误("实例凭据引用无效")
    return f"{match.group(1)}:{match.group(2)}"


def 读取实例(ref: str) -> str:
    value = _security(["find-generic-password", "-s", "xj-multiuser", "-a", _实例账户(ref), "-w"])
    if not value:
        raise 凭据错误("实例凭据为空")
    return value


def 保存实例(ref: str, value: str) -> None:
    if not value or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise 凭据错误("拒绝保存无效实例凭据")
    _security(["add-generic-password", "-U", "-s", "xj-multiuser", "-a", _实例账户(ref), "-w", value])


def 删除实例(ref: str, *, missing_ok: bool = False) -> None:
    try:
        _security(["delete-generic-password", "-s", "xj-multiuser", "-a", _实例账户(ref)])
    except 凭据不存在:
        if not missing_ok:
            raise


def 读取平台(name: str) -> str:
    if not _平台名称.fullmatch(name):
        raise 凭据错误("平台凭据名称无效")
    value = _security(["find-generic-password", "-s", "xj-multiuser-platform", "-a", name, "-w"])
    if not value:
        raise 凭据错误("平台凭据为空")
    return value


def 保存平台(name: str, value: str) -> None:
    if not _平台名称.fullmatch(name) or not value or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise 凭据错误("拒绝保存无效平台凭据")
    _security(["add-generic-password", "-U", "-s", "xj-multiuser-platform", "-a", name, "-w", value])
