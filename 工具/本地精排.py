#!/usr/bin/env python3
"""Qwen3-Reranker-4B 的受限 MLX 推理核心。

生产查询由独立的 ``本地精排服务.py`` 调用这里。评分只保留每段文本最后
位置的 yes/no 判定，不再生成整段文本每个位置的完整词表结果。
"""
from __future__ import annotations

import gc
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any


模型仓 = "mlx-community/Qwen3-Reranker-4B-mxfp8"
模型版本 = "25f203a237b822a90f38763843562b93a5baf82f"
默认目录 = Path.home() / "Library" / "Application Support" / "沐光破事部" / "models" / "Qwen3-Reranker-4B-mxfp8"

# 这些是资源边界，不是模型能力边界。公司索引块通常不到 900 字，无需把
# 4B 模型按 8x8192 的极端生成场景运行。
最大长度 = 2048
最大文档数 = 16
最大总令牌数 = 24_576
默认批大小 = 2
默认批令牌数 = 4096
默认内存上限字节 = 8 * 1024**3
默认缓存上限字节 = 256 * 1024**2

指令 = (
    "Given a company knowledge-base query, retrieve passages that directly answer the query "
    "with verifiable evidence. Prefer exact people, dates, events, decisions, and quoted facts "
    "over merely related topics."
)
前缀 = (
    '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the '
    'Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n'
    '<|im_start|>user\n'
)
后缀 = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"

_加载锁 = threading.Lock()
_推理锁 = threading.RLock()
_模型: Any = None
_分词器: Any = None
_yes_id: int | None = None
_no_id: int | None = None
_状态锁 = threading.Lock()
_状态: dict[str, Any] = {
    "状态": "未加载",
    "模型": 模型仓,
    "版本": 模型版本,
    "目录": "",
    "错误": "",
    "加载秒": None,
    "最近推理秒": None,
    "最近批量": 0,
    "最近总令牌": 0,
    "调用次数": 0,
    "失败次数": 0,
    "最近使用": None,
}


class 请求超限(ValueError):
    pass


def _环境整数(name: str, default: int, lower: int, upper: int) -> int:
    try:
        return max(lower, min(int(os.environ.get(name, str(default)).strip()), upper))
    except (TypeError, ValueError):
        return default


def 模型目录() -> Path:
    raw = os.environ.get("XJ_RERANK_MODEL_PATH", "").strip()
    return Path(raw).expanduser().resolve() if raw else 默认目录


def 批大小() -> int:
    return _环境整数("XJ_RERANK_BATCH_SIZE", 默认批大小, 1, 默认批大小)


def 批令牌数() -> int:
    return _环境整数("XJ_RERANK_BATCH_TOKENS", 默认批令牌数, 512, 默认批令牌数)


def 完整性(path: Path | None = None) -> tuple[bool, str]:
    target = path or 模型目录()
    required = (
        "config.json", "model.safetensors", "model.safetensors.index.json",
        "tokenizer.json", "tokenizer_config.json",
    )
    missing = [name for name in required if not (target / name).is_file()]
    if missing:
        return False, "缺少：" + "、".join(missing)
    weight = target / "model.safetensors"
    if weight.stat().st_size < 3_500_000_000:
        return False, f"模型权重不完整：只有 {weight.stat().st_size / 1024**3:.2f} GB"
    return True, f"完整，权重 {weight.stat().st_size / 1024**3:.2f} GB"


def _mlx内存() -> dict[str, int]:
    mx = sys.modules.get("mlx.core")
    if mx is None:
        return {"活动字节": 0, "缓存字节": 0, "峰值字节": 0}
    try:
        return {
            "活动字节": int(mx.get_active_memory()),
            "缓存字节": int(mx.get_cache_memory()),
            "峰值字节": int(mx.get_peak_memory()),
        }
    except Exception:  # noqa: BLE001
        return {"活动字节": 0, "缓存字节": 0, "峰值字节": 0}


def 状态() -> dict[str, Any]:
    with _状态锁:
        out = dict(_状态)
    files_ok, files_detail = 完整性()
    out.update({
        "目录": str(模型目录()),
        "已加载": _模型 is not None and _分词器 is not None,
        "模型文件完整": files_ok,
        "模型文件说明": files_detail,
        "批大小": 批大小(),
        "批令牌上限": 批令牌数(),
        "最大长度": 最大长度,
        "最大文档数": 最大文档数,
        "内存": _mlx内存(),
    })
    return out


def _改状态(**fields: Any) -> None:
    with _状态锁:
        _状态.update(fields)


def _配置mlx(mx: Any) -> None:
    memory_mb = _环境整数(
        "XJ_RERANK_MEMORY_LIMIT_MB", 默认内存上限字节 // 1024**2, 4096, 16_384,
    )
    cache_mb = _环境整数(
        "XJ_RERANK_CACHE_LIMIT_MB", 默认缓存上限字节 // 1024**2, 0, 1024,
    )
    mx.set_memory_limit(memory_mb * 1024**2)
    mx.set_cache_limit(cache_mb * 1024**2)
    mx.reset_peak_memory()


def _加载分词器() -> tuple[Any, int, int]:
    global _分词器, _yes_id, _no_id
    if _分词器 is not None and _yes_id is not None and _no_id is not None:
        return _分词器, _yes_id, _no_id
    with _加载锁:
        if _分词器 is not None and _yes_id is not None and _no_id is not None:
            return _分词器, _yes_id, _no_id
        target = 模型目录()
        ok, message = 完整性(target)
        if not ok:
            _改状态(状态="不可用", 错误=message)
            raise FileNotFoundError(f"本地精排模型不可用：{message}")
        try:
            from mlx_lm.utils import load_tokenizer

            tokenizer = load_tokenizer(target)
            yes = tokenizer.encode("yes", add_special_tokens=False)
            no = tokenizer.encode("no", add_special_tokens=False)
            if len(yes) != 1 or len(no) != 1:
                raise RuntimeError(f"yes/no 不是单 token：yes={yes!r}, no={no!r}")
            _分词器 = tokenizer
            _yes_id, _no_id = int(yes[0]), int(no[0])
            return _分词器, _yes_id, _no_id
        except Exception as exc:  # noqa: BLE001
            _改状态(状态="不可用", 错误=f"{type(exc).__name__}: {exc}")
            raise


def _加载() -> tuple[Any, Any, int, int]:
    global _模型, _分词器, _yes_id, _no_id
    if _模型 is not None and _分词器 is not None and _yes_id is not None and _no_id is not None:
        return _模型, _分词器, _yes_id, _no_id
    with _加载锁:
        if _模型 is not None and _分词器 is not None and _yes_id is not None and _no_id is not None:
            return _模型, _分词器, _yes_id, _no_id
        target = 模型目录()
        ok, message = 完整性(target)
        if not ok:
            _改状态(状态="不可用", 错误=message)
            raise FileNotFoundError(f"本地精排模型不可用：{message}")
        started = time.monotonic()
        _改状态(状态="加载中", 错误="")
        try:
            import mlx.core as mx
            from mlx_lm import load

            _配置mlx(mx)
            model, tokenizer = load(str(target), lazy=False)
            yes = tokenizer.encode("yes", add_special_tokens=False)
            no = tokenizer.encode("no", add_special_tokens=False)
            if len(yes) != 1 or len(no) != 1:
                raise RuntimeError(f"yes/no 不是单 token：yes={yes!r}, no={no!r}")
            _模型, _分词器 = model, tokenizer
            _yes_id, _no_id = int(yes[0]), int(no[0])
            _改状态(
                状态="可用", 加载秒=round(time.monotonic() - started, 2),
                错误="", 最近使用=time.time(),
            )
            return _模型, _分词器, _yes_id, _no_id
        except Exception as exc:  # noqa: BLE001
            _改状态(
                状态="不可用", 加载秒=round(time.monotonic() - started, 2),
                错误=f"{type(exc).__name__}: {exc}",
            )
            raise


def 释放模型() -> bool:
    """只由独立服务的单工作者在空闲或关停时调用。"""
    global _模型, _分词器, _yes_id, _no_id
    with _推理锁, _加载锁:
        had_model = _模型 is not None or _分词器 is not None
        if had_model:
            _改状态(状态="卸载中")
        _模型 = None
        _分词器 = None
        _yes_id = None
        _no_id = None
        gc.collect()
        mx = sys.modules.get("mlx.core")
        if mx is not None:
            try:
                mx.clear_cache()
            except Exception:  # noqa: BLE001
                pass
        _改状态(状态="未加载", 最近批量=0, 最近总令牌=0)
        return had_model


def _编码(tokenizer: Any, query: str, document: str) -> list[int]:
    prefix = tokenizer.encode(前缀, add_special_tokens=False)
    suffix = tokenizer.encode(后缀, add_special_tokens=False)
    query_part = tokenizer.encode(
        f"<Instruct>: {指令}\n<Query>: {query}\n<Document>: ",
        add_special_tokens=False,
    )
    document_part = tokenizer.encode(str(document), add_special_tokens=False)
    available = 最大长度 - len(prefix) - len(suffix) - len(query_part)
    if available <= 0:
        raise ValueError("查询模板本身已经超过本地精排最大长度")
    return prefix + query_part + document_part[:available] + suffix


def _分批(encoded: list[tuple[int, list[int]]]) -> list[list[tuple[int, list[int]]]]:
    """按长度分桶，避免一段长文本把同批短文本全部补到同样长度。"""
    ordered = sorted(encoded, key=lambda item: len(item[1]))
    batches: list[list[tuple[int, list[int]]]] = []
    current: list[tuple[int, list[int]]] = []
    width = 0
    for item in ordered:
        proposed_width = max(width, len(item[1]))
        proposed_count = len(current) + 1
        if current and (proposed_count > 批大小() or proposed_width * proposed_count > 批令牌数()):
            batches.append(current)
            current = []
            width = 0
        current.append(item)
        width = max(width, len(item[1]))
    if current:
        batches.append(current)
    return batches


def _两词投影(head: Any, final_hidden: Any, no_id: int, yes_id: int, mx: Any) -> Any:
    ids = mx.array([no_id, yes_id])
    if hasattr(head, "scales"):
        weights = head.weight[ids]
        scales = head.scales[ids]
        biases = head.biases[ids] if getattr(head, "biases", None) is not None else None
        return mx.quantized_matmul(
            final_hidden, weights, scales=scales, biases=biases, transpose=True,
            group_size=head.group_size, bits=head.bits, mode=head.mode,
        )
    return final_hidden @ head.weight[ids].T


def _最后位置概率(model: Any, tokens: Any, lengths: list[int], no_id: int, yes_id: int, mx: Any) -> Any:
    """只把最后隐藏态投影到 no/yes；绝不生成 [batch, sequence, vocab]。"""
    hidden = model.model(tokens)
    final_hidden = mx.stack([hidden[row, length - 1] for row, length in enumerate(lengths)])
    head = model.model.embed_tokens if bool(getattr(model.args, "tie_word_embeddings", False)) else model.lm_head
    pair = _两词投影(head, final_hidden, no_id, yes_id, mx)
    probabilities = mx.softmax(pair, axis=1)[:, 1]
    mx.eval(probabilities)
    return probabilities


def _打分同步(query: str, documents: list[str]) -> list[float]:
    if not 1 <= len(documents) <= 最大文档数:
        raise ValueError(f"本地精排一次只接受 1 到 {最大文档数} 篇候选")
    scores = [0.0] * len(documents)
    started = time.monotonic()
    total_tokens = 0
    try:
        tokenizer, yes_id, no_id = _加载分词器()
        encoded = [(index, _编码(tokenizer, query, document)) for index, document in enumerate(documents)]
        total_tokens = sum(len(tokens) for _, tokens in encoded)
        if total_tokens > 最大总令牌数:
            raise 请求超限(f"本地精排本次共有 {total_tokens} 个令牌，超过上限 {最大总令牌数}")
        model, tokenizer, yes_id, no_id = _加载()
        pad_id = int(getattr(tokenizer, "pad_token_id", None) or getattr(tokenizer, "eos_token_id", 0))
        _改状态(状态="推理中", 最近批量=len(documents), 最近总令牌=total_tokens, 最近使用=time.time())
        with _推理锁:
            import mlx.core as mx

            for batch in _分批(encoded):
                indices = [index for index, _ in batch]
                lengths = [len(tokens) for _, tokens in batch]
                width = max(lengths)
                padded = [tokens + [pad_id] * (width - len(tokens)) for _, tokens in batch]
                probabilities = _最后位置概率(model, mx.array(padded), lengths, no_id, yes_id, mx)
                for row, original_index in enumerate(indices):
                    scores[original_index] = float(probabilities[row].item())
                del probabilities
        elapsed = round(time.monotonic() - started, 3)
        with _状态锁:
            _状态.update({
                "状态": "可用", "最近推理秒": elapsed, "最近批量": len(documents),
                "最近总令牌": total_tokens, "最近使用": time.time(), "错误": "",
                "调用次数": int(_状态.get("调用次数") or 0) + 1,
            })
        return scores
    except Exception as exc:  # noqa: BLE001
        with _状态锁:
            _状态.update({
                "状态": "错误", "错误": f"{type(exc).__name__}: {exc}",
                "失败次数": int(_状态.get("失败次数") or 0) + 1,
                "最近使用": time.time(),
            })
        raise
    finally:
        mx = sys.modules.get("mlx.core")
        if mx is not None:
            try:
                mx.clear_cache()
            except Exception:  # noqa: BLE001
                pass


def 同步精排(query: str, documents: list[str]) -> list[float]:
    """只供独立服务工作者和显式隔离验收使用。"""
    return _打分同步(str(query), [str(item) for item in documents]) if documents else []
