"""为用户界面提供当前进程的可信版本身份。"""

from __future__ import annotations

import os
from typing import Any, Mapping

from .部署.发布仓库 import (
    DEVELOPMENT_VERSION_NAME,
    VERSION_PATTERN,
    发布仓库错误,
    当前已发布版本,
    校验版本显示名,
    读取正式版本,
)


class 版本身份错误(RuntimeError):
    pass


def _当前正式版本身份() -> dict[str, str] | None:
    try:
        version = 当前已发布版本()
        if not version:
            return None
        release = 读取正式版本(version, verify=False)
        version_name = 校验版本显示名(str(release.get("version_name") or ""))
    except 发布仓库错误 as exc:
        raise 版本身份错误("当前正式版本身份无法确认") from exc
    if str(release.get("version") or "") != version:
        raise 版本身份错误("活动版本锁与封存版本清单不一致")
    return {"version": version, "version_name": version_name}


def 读取运行版本身份(
    *,
    development: bool = False,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """开发进程标明开发版；正式进程核对进程、活动锁和封存清单。"""
    if development:
        identity: dict[str, Any] = {
            "mode": "development",
            "version": "dev",
            "version_name": DEVELOPMENT_VERSION_NAME,
            "display_name": DEVELOPMENT_VERSION_NAME,
        }
        formal = _当前正式版本身份()
        if formal:
            identity["base_version"] = formal["version"]
            identity["base_version_name"] = formal["version_name"]
            identity["display_name"] = (
                f"{DEVELOPMENT_VERSION_NAME} · 当前正式版 {formal['version_name']}"
            )
        return identity

    env = environ or os.environ
    process_version = str(env.get("XJ_ACTIVE_VERSION") or "").strip()
    if not VERSION_PATTERN.fullmatch(process_version):
        raise 版本身份错误("正式进程缺少有效的活动版本身份")

    try:
        locked_version = 当前已发布版本()
        if locked_version != process_version:
            raise 版本身份错误("正式进程与活动版本锁不一致")
        release = 读取正式版本(process_version, verify=False)
        version_name = 校验版本显示名(str(release.get("version_name") or ""))
    except 发布仓库错误 as exc:
        raise 版本身份错误("当前正式版本身份无法确认") from exc

    if str(release.get("version") or "") != process_version:
        raise 版本身份错误("正式进程与封存版本清单不一致")
    return {
        "mode": "formal",
        "version": process_version,
        "version_name": version_name,
        "display_name": version_name,
    }
