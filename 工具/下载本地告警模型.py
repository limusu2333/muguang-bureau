#!/usr/bin/env python3
"""下载并核对健康告警专用的本地 MLX 模型。"""
from __future__ import annotations

import os
from pathlib import Path

模型仓 = "mlx-community/Qwen3.5-4B-4bit"
模型版本 = "0e7ffd5c629ef7719d4cbc04069232580bfa9d9c"
默认目录 = Path.home() / "Library" / "Application Support" / "沐光破事部" / "models" / "Qwen3.5-4B-4bit"
必需文件 = ("config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json")


def 模型目录() -> Path:
    raw = os.environ.get("XJ_ALERT_MODEL_PATH", "").strip()
    return Path(raw).expanduser().resolve() if raw else 默认目录


def 完整性(path: Path | None = None) -> tuple[bool, str]:
    target = path or 模型目录()
    missing = [name for name in 必需文件 if not (target / name).is_file()]
    if missing:
        return False, "缺少：" + "、".join(missing)
    weight_size = (target / "model.safetensors").stat().st_size
    if weight_size < 3_000_000_000:
        return False, f"模型权重不完整：只有 {weight_size / 1024 / 1024:.1f} MB"
    return True, f"完整，权重 {weight_size / 1024 / 1024 / 1024:.2f} GB"


def 下载() -> Path:
    from huggingface_hub import snapshot_download

    target = 模型目录()
    target.mkdir(parents=True, exist_ok=True)
    print(f"下载 {模型仓}@{模型版本[:10]} 到：\n{target}", flush=True)
    snapshot_download(
        repo_id=模型仓,
        revision=模型版本,
        local_dir=target,
    )
    ok, message = 完整性(target)
    if not ok:
        raise RuntimeError(message)
    print(f"模型下载完成：{message}", flush=True)
    return target


if __name__ == "__main__":
    下载()
