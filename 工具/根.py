"""公司运行根目录的唯一入口。

本机未设置任何多用户环境变量时，所有默认值保持现役行为。远程实例必须
显式传入独立的数据根、工作根和实例配置，不能再从代码文件位置反推用户数据。
"""

from __future__ import annotations

import os
from pathlib import Path


def _路径(env_name: str, default: Path) -> Path:
    raw = os.environ.get(env_name, "").strip()
    return Path(raw).expanduser().resolve() if raw else default.resolve()


代码根 = _路径("XJ_CODE_ROOT", Path(__file__).resolve().parents[1])
运行模式 = os.environ.get("XJ_MODE", "local").strip().lower() or "local"
if 运行模式 not in {"local", "remote"}:
    raise RuntimeError(f"XJ_MODE 只能是 local 或 remote，实际为 {运行模式!r}")

数据根 = _路径("XJ_DATA_ROOT", 代码根)
_本机产品根 = 代码根.parent / "20_工程_XJ"
工作根 = _路径("XJ_WORKSPACE_ROOT", 数据根 if 运行模式 == "remote" else _本机产品根)
实例配置路径 = _路径("XJ_INSTANCE_CONFIG", 数据根 / "instance.yaml")
本机实例配置路径 = _路径("XJ_LOCAL_INSTANCE_CONFIG", 代码根 / "本机实例.yaml")

# 本机工作区仍是现役产品目录；远程实例把用户工作区收进 /work。
产品根 = _路径(
    "XJ_PRODUCT_ROOT",
    工作根,
)
备份根 = _路径(
    "XJ_BACKUP_ROOT",
    数据根 / "备份" if 运行模式 == "remote" else 代码根.parent / "90_备份",
)


def 是远程实例() -> bool:
    return 运行模式 == "remote"


def 本机环境文件() -> Path | None:
    """远程实例禁止读取镜像或数据卷里的 .env，只接受监督服务注入的环境变量。"""
    return None if 是远程实例() else 代码根 / ".env"


def 搜索键() -> str:
    """远程搜索只认 search-gw 令牌，绝不回退误用聊天虚拟 key。"""
    if 是远程实例():
        key = os.environ.get("EDITH_DASHSCOPE_KEY", "").strip()
        if not key:
            raise RuntimeError("远程模式缺 search-gw 实例令牌")
        return key
    key = (
        os.environ.get("EDITH_DASHSCOPE_KEY", "").strip()
        or os.environ.get("DASHSCOPE_API_KEY", "").strip()
    )
    if not key:
        raise RuntimeError("本机缺 DashScope 搜索密钥")
    return key


def 显示路径(path: str | Path) -> str:
    """按 code/data/work 三个明确根显示路径，不再用一个 COMPANY 猜。"""
    p = Path(path).expanduser().resolve()
    for label, root in (("code", 代码根), ("data", 数据根), ("work", 工作根)):
        if p == root or p.is_relative_to(root):
            rel = p.relative_to(root)
            return f"{label}:{rel}" if str(rel) != "." else f"{label}:"
    return str(p)


# 英文别名只用于逐步迁移旧模块；新代码优先使用中文名，避免又造第二套根。
CODE_ROOT = 代码根
DATA_ROOT = 数据根
WORKSPACE_ROOT = 工作根
PRODUCT_ROOT = 产品根
BACKUP_ROOT = 备份根
INSTANCE_CONFIG = 实例配置路径
LOCAL_INSTANCE_CONFIG = 本机实例配置路径
