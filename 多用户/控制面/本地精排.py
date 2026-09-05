"""Host-side MLX adapter for the fixed Qwen3-Reranker-4B release."""

from __future__ import annotations

import asyncio
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
最大长度 = 2048
最大文档数 = 16
默认批大小 = 2
最大批令牌 = 4096
最大请求令牌 = 24_576
缓存上限字节 = 256 * 1024 * 1024
内存指导上限字节 = 8 * 1024 * 1024 * 1024
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
    "失败次数": 0,
    "最近失败": "",
    "active_bytes": 0,
    "cache_bytes": 0,
    "peak_bytes": 0,
    "调用次数": 0,
}


class 请求超限(ValueError):
    pass


def 模型目录() -> Path:
    raw = os.environ.get("XJ_RERANK_MODEL_PATH", "").strip()
    return Path(raw).expanduser().resolve() if raw else 默认目录


def 批大小() -> int:
    raw = os.environ.get("XJ_RERANK_BATCH_SIZE", str(默认批大小)).strip()
    try:
        return max(1, min(int(raw), 默认批大小))
    except ValueError:
        return 默认批大小


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
        return False, f"模型权重不完整：只有 {weight.stat().st_size / 1024 / 1024 / 1024:.2f} GB"
    return True, f"完整，权重 {weight.stat().st_size / 1024 / 1024 / 1024:.2f} GB"


def 状态() -> dict[str, Any]:
    with _状态锁:
        out = dict(_状态)
    out["目录"] = str(模型目录())
    out["可用"] = out.get("状态") == "可用"
    out["批大小"] = 批大小()
    return out


def 内存状态() -> dict[str, int]:
    """Read MLX counters without importing MLX or loading the model."""
    mx = sys.modules.get("mlx.core")
    if mx is None:
        return {"active_bytes": 0, "cache_bytes": 0, "peak_bytes": 0}
    try:
        return {
            "active_bytes": int(mx.get_active_memory()),
            "cache_bytes": int(mx.get_cache_memory()),
            "peak_bytes": int(mx.get_peak_memory()),
        }
    except Exception:  # MLX 首次导入期间可能暂时没有完整的内存计数接口。
        return {"active_bytes": 0, "cache_bytes": 0, "peak_bytes": 0}


def _改状态(**fields: Any) -> None:
    with _状态锁:
        _状态.update(fields)


def _加载分词器() -> Any:
    global _分词器
    if _分词器 is not None:
        return _分词器
    with _加载锁:
        if _分词器 is not None:
            return _分词器
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
            return _分词器
        except Exception as exc:  # noqa: BLE001
            _改状态(状态="不可用", 错误=f"{type(exc).__name__}: {exc}")
            raise


def _加载() -> tuple[Any, Any]:
    global _模型, _分词器
    if _模型 is not None and _分词器 is not None:
        return _模型, _分词器
    with _加载锁:
        if _模型 is not None and _分词器 is not None:
            return _模型, _分词器
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

            mx.set_cache_limit(缓存上限字节)
            mx.set_memory_limit(内存指导上限字节)
            model, tokenizer = load(str(target), lazy=False)
            yes = tokenizer.encode("yes", add_special_tokens=False)
            no = tokenizer.encode("no", add_special_tokens=False)
            if len(yes) != 1 or len(no) != 1:
                raise RuntimeError(f"yes/no 不是单 token：yes={yes!r}, no={no!r}")
            _模型, _分词器 = model, tokenizer
            _改状态(状态="可用", 加载秒=round(time.monotonic() - started, 2), 错误="")
            return _模型, _分词器
        except Exception as exc:  # noqa: BLE001
            _改状态(状态="不可用", 加载秒=round(time.monotonic() - started, 2), 错误=f"{type(exc).__name__}: {exc}")
            raise


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


def _分批(encoded: list[list[int]]) -> list[list[tuple[int, list[int]]]]:
    batches: list[list[tuple[int, list[int]]]] = []
    current: list[tuple[int, list[int]]] = []
    width = 0
    for index, item in sorted(enumerate(encoded), key=lambda pair: len(pair[1]), reverse=True):
        next_width = max(width, len(item))
        if current and (
            len(current) >= 批大小()
            or next_width * (len(current) + 1) > 最大批令牌
        ):
            batches.append(current)
            current = []
            width = 0
            next_width = len(item)
        current.append((index, item))
        width = next_width
    if current:
        batches.append(current)
    return batches


def _取层参数(layer: Any, name: str) -> Any:
    try:
        return layer[name]
    except (KeyError, TypeError):
        return getattr(layer, name)


def _两词投影(layer: Any, final_hidden: Any, no_id: int, yes_id: int, mx: Any) -> Any:
    indexes = mx.array([no_id, yes_id])
    weight = _取层参数(layer, "weight")[indexes]
    if hasattr(layer, "scales"):
        biases = getattr(layer, "biases", None)
        pair = mx.quantized_matmul(
            final_hidden,
            weight,
            scales=_取层参数(layer, "scales")[indexes],
            biases=biases[indexes] if biases is not None else None,
            transpose=True,
            group_size=layer.group_size,
            bits=layer.bits,
            mode=layer.mode,
        )
    else:
        pair = mx.matmul(final_hidden, mx.transpose(weight))
    bias = getattr(layer, "bias", None)
    if bias is not None:
        pair = pair + bias[indexes]
    return pair


def _最后位置概率(model: Any, inputs: Any, lengths: list[int], no_id: int, yes_id: int, mx: Any) -> Any:
    """Project only two token rows from final hidden states."""
    hidden = model.model(inputs)
    final_hidden = mx.stack([hidden[row, length - 1] for row, length in enumerate(lengths)])
    layer = model.model.embed_tokens if bool(getattr(model.args, "tie_word_embeddings", False)) else model.lm_head
    pair = _两词投影(layer, final_hidden, no_id, yes_id, mx)
    probabilities = mx.softmax(pair, axis=1)[:, 1]
    mx.eval(probabilities)
    return probabilities


def _打分同步(query: str, documents: list[str]) -> list[float]:
    scores: list[float | None] = [None] * len(documents)
    started = time.monotonic()
    total_tokens = 0
    failure = ""
    with _推理锁:
        try:
            tokenizer = _加载分词器()
            yes_id = tokenizer.encode("yes", add_special_tokens=False)[0]
            no_id = tokenizer.encode("no", add_special_tokens=False)[0]
            encoded = [_编码(tokenizer, query, document) for document in documents]
            total_tokens = sum(map(len, encoded))
            if total_tokens > 最大请求令牌:
                raise 请求超限(
                    f"本地精排单次最多处理 {最大请求令牌} token，实际 {total_tokens} token"
                )
            model, tokenizer = _加载()
            pad_id = int(getattr(tokenizer, "pad_token_id", None) or getattr(tokenizer, "eos_token_id", 0))
            import mlx.core as mx

            for batch in _分批(encoded):
                indexes = [index for index, _item in batch]
                items = [item for _index, item in batch]
                lengths = [len(item) for item in items]
                width = max(lengths)
                padded = [item + [pad_id] * (width - len(item)) for item in items]
                probabilities = _最后位置概率(
                    model, mx.array(padded), lengths, no_id, yes_id, mx,
                )
                for index, score in zip(indexes, probabilities.tolist(), strict=True):
                    scores[index] = float(score)
                del probabilities
        except Exception as exc:
            failure = f"{type(exc).__name__}: {str(exc)[:180]}"
            raise
        finally:
            mx = sys.modules.get("mlx.core")
            if mx is not None:
                mx.clear_cache()
            memory = 内存状态()
            with _状态锁:
                _状态.update({
                    "最近推理秒": round(time.monotonic() - started, 3),
                    "最近批量": len(documents),
                    "最近总令牌": total_tokens,
                    "最近失败": failure,
                    **memory,
                })
                if failure:
                    _状态["失败次数"] = int(_状态.get("失败次数") or 0) + 1
                else:
                    _状态["调用次数"] = int(_状态.get("调用次数") or 0) + 1
    if any(score is None for score in scores):
        raise RuntimeError("本地精排结果数量不完整")
    return [float(score) for score in scores]


def 卸载() -> bool:
    """Release the single resident model after the service has gone idle."""
    global _模型, _分词器
    with _推理锁:
        loaded = _模型 is not None or _分词器 is not None
        _模型 = None
        _分词器 = None
        gc.collect()
        mx = sys.modules.get("mlx.core")
        if mx is not None:
            mx.clear_cache()
        _改状态(状态="未加载", 错误="", 最近批量=0)
        return loaded


class LocalQwen3Reranker:
    model_id = f"{模型仓}@{模型版本[:12]}"
    max_documents = 最大文档数

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if len(documents) > self.max_documents:
            raise ValueError(f"本地精排一次最多接收 {self.max_documents} 篇候选，实际 {len(documents)} 篇")
        if not documents:
            return []
        return await asyncio.to_thread(_打分同步, str(query), [str(item) for item in documents])


_默认精排器 = LocalQwen3Reranker()


def 默认精排器() -> LocalQwen3Reranker:
    return _默认精排器


def 同步精排(query: str, documents: list[str]) -> list[float]:
    if len(documents) > 最大文档数:
        raise ValueError(f"本地精排一次最多接收 {最大文档数} 篇候选，实际 {len(documents)} 篇")
    return _打分同步(str(query), [str(item) for item in documents]) if documents else []
