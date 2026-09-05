"""Retired compatibility entry for the former release-time source merger."""

from __future__ import annotations

from typing import NoReturn


class 正式版错误(RuntimeError):
    pass


def _已停用() -> NoReturn:
    raise 正式版错误("旧发布器已停用：发布时不再合并两份源码，请使用候选制造与正式部署流程")


def 发布正式版(*_args: object, **_kwargs: object) -> NoReturn:
    _已停用()


def 确认发布候选(*_args: object, **_kwargs: object) -> NoReturn:
    _已停用()


def 取消发布候选(*_args: object, **_kwargs: object) -> NoReturn:
    _已停用()


def 同步状态(*_args: object, **_kwargs: object) -> dict[str, object]:
    return {"ok": False, "reason": "旧发布时合并器已停用"}


def 验证已同步(*_args: object, **_kwargs: object) -> NoReturn:
    _已停用()
