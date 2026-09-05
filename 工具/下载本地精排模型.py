#!/usr/bin/env python3
"""下载并核对统一搜索使用的本机 Qwen3-Reranker-4B MLX 模型。"""
from __future__ import annotations

from pathlib import Path

from 本地精排 import 模型仓, 模型版本, 模型目录, 完整性


def 下载() -> Path:
    from huggingface_hub import snapshot_download

    target = 模型目录()
    target.mkdir(parents=True, exist_ok=True)
    print(f"下载 {模型仓}@{模型版本[:12]} 到：\n{target}", flush=True)
    snapshot_download(
        repo_id=模型仓,
        revision=模型版本,
        local_dir=str(target),
    )
    ok, message = 完整性(target)
    if not ok:
        raise RuntimeError(message)
    print(f"本地精排模型下载完成：{message}", flush=True)
    return target


if __name__ == "__main__":
    下载()
