#!/usr/bin/env python3
"""④ 记忆向量 · 公司用:个人记忆「按意思检索」(语义检索,不靠翻全文)。

为什么:个人记忆现在是 记忆库/<人名>.md 纯文本 append,越攒越长;本人干活时把全文塞进上下文=费 token、抓不住重点。
本模块让本人「按当前任务的意思」从自己记忆里挑出最相关的几条喂进去,而不是全文塞。

官方零件(已 introspect 钉死,2026-06-21):
  · agentscope.embedding.DashScopeEmbeddingModel(credential, model="text-embedding-v4", embedding_cache)
  · 调用 await model(inputs=[文本列表]) → resp.embeddings: List[List[float]] / resp.source: 'cache'|'api'
  · FileEmbeddingCache(cache_dir):同一条文本的向量缓存到磁盘,不重复烧钱(resp.source=='cache' 即命中)

小验(真调一次 embedding,成本~0,DashScope text-embedding 每千token几厘):`cd 工具 && python3 记忆向量.py`
接入(下一步,记忆量大了再接):读个人记忆时,改用 检索个人记忆() 取 topN 喂上下文,替代全文塞。
"""
from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path
from typing import Any

from 根 import 代码根, 数据根, 本机环境文件, 搜索键

COMPANY = 数据根
_缓存目录 = 数据根 / "运行状态" / "embedding缓存"
_模型名 = "text-embedding-v4"


def _建模型():
    from agentscope.credential import DashScopeCredential
    from agentscope.embedding import DashScopeEmbeddingModel, FileEmbeddingCache

    key = 搜索键()
    return DashScopeEmbeddingModel(
        credential=DashScopeCredential(api_key=key),
        model=_模型名,
        embedding_cache=FileEmbeddingCache(cache_dir=str(_缓存目录)),
    )


def _余弦(a: list[float], b: list[float]) -> float:
    点积 = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 点积 / (na * nb) if na and nb else 0.0


async def _检索async(查询: str, 候选: list[str], topN: int = 3) -> list[dict[str, Any]]:
    候选 = [c for c in 候选 if c.strip()]
    if not 候选:
        return []
    model = _建模型()
    resp = await model(inputs=[查询] + 候选)  # 一次调用 embed 查询+全部候选(省调用)
    向量 = resp.embeddings
    q = 向量[0]
    打分 = [(_余弦(q, 向量[i + 1]), 候选[i]) for i in range(len(候选))]
    打分.sort(key=lambda x: x[0], reverse=True)
    return [{"相似度": round(s, 4), "记忆": m} for s, m in 打分[:topN]]


def 检索(查询: str, 候选: list[str], topN: int = 3) -> list[dict[str, Any]]:
    """按语义从候选记忆里找最相关的 topN 条(带相似度,降序)。"""
    return asyncio.run(_检索async(查询, 候选, topN))


async def 判重async(新: str, 现有: list[str], 阈: float = 0.85) -> bool:
    """新的一条 和 现有任一条 语义是否≈重复(cosine≥阈)——mem0 式"写前语义去重(NOOP)"用。
    阈=0.85 抄 mem0 默认。embedding 不可用(缺键等)时返回 False，降级给字面去重兜底、不硬崩。"""
    现有 = [c for c in (现有 or []) if c and str(c).strip()]
    新 = (新 or "").strip()
    if not (新 and 现有):
        return False
    try:
        r = await _检索async(新, 现有, topN=1)
        return bool(r and r[0].get("相似度", 0) >= 阈)
    except Exception:  # noqa: BLE001
        return False


def 读个人记忆条目(人名: str) -> list[str]:
    """把某人 记忆库/<人名>.md 拆成一条条(去标题/空行/列表符号),供检索。"""
    from 活_本人 import _记忆文件; p = _记忆文件(人名)
    if not p.exists():
        return []
    out: list[str] = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        s = ln.strip().lstrip("-*").strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def 读岗位记忆条目(role: str) -> list[str]:
    """兼容旧调用：按花名册把岗位映射到当前坐在这把椅子上的人，再读个人记忆。"""
    try:
        import yaml
        roster = yaml.safe_load((代码根 / "花名册.yaml").read_text(encoding="utf-8")) or {}
        person = str((roster.get(role) or {}).get("名字") or role)
    except Exception:  # noqa: BLE001
        person = role
    return 读个人记忆条目(person)


def 检索个人记忆(人名: str, 查询: str, topN: int = 3) -> list[dict[str, Any]]:
    """给某个人:按当前任务语义,从他的记忆里挑最相关的几条。"""
    return 检索(查询, 读个人记忆条目(人名), topN)


def 检索岗位记忆(role: str, 查询: str, topN: int = 3) -> list[dict[str, Any]]:
    """兼容旧调用：岗位先映射到人，再检索人的记忆。"""
    return 检索(查询, 读岗位记忆条目(role), topN)


if __name__ == "__main__":
    # 加载 .env(独立跑时拿 DASHSCOPE_API_KEY)
    envf = 本机环境文件()
    if envf is not None and envf.exists():
        for line in envf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    print("④ 记忆向量小验(真调一次 DashScope embedding,成本~0):")
    记忆 = [
        "项目经理外包需求书必须写明:完工含 git commit",
        "测试工程师发现验收命令 grep 缺文件会崩,要明示失败不抛异常",
        "船主是西安人、婚礼摄影师,喜欢深夜调色",
        "首席工程师强调最简方案、反对过度设计",
    ]
    查询 = "怎么写一份外包给 Codex 的需求书,别漏东西"
    res = 检索(查询, 记忆, topN=2)
    print(f"  查询:{查询}")
    for r in res:
        print(f"    [{r['相似度']}] {r['记忆']}")
    assert res, "应检索出结果"
    assert "git commit" in res[0]["记忆"], f"语义检索失败:外包需求书该排第一,实际:{res[0]['记忆']}"
    print("  ✓ 语义检索命中:'外包需求书'相关记忆排第一(按意思找到,不靠字面)")

    # 验缓存:再查一次,同文本应命中缓存(不重复烧钱)
    from agentscope.credential import DashScopeCredential
    from agentscope.embedding import DashScopeEmbeddingModel, FileEmbeddingCache

    async def _验缓存():
        m = DashScopeEmbeddingModel(
            credential=DashScopeCredential(api_key=搜索键()),
            model=_模型名, embedding_cache=FileEmbeddingCache(cache_dir=str(_缓存目录)))
        r = await m(inputs=[记忆[0]])
        return r.source

    src = asyncio.run(_验缓存())
    print(f"  ✓ 缓存验证:同文本再 embed → source={src}" + ("（命中缓存,不重复烧钱）" if src == "cache" else "（首次入缓存）"))
    print("\n④ 记忆向量小验通过 ✅(语义检索命中 + FileCache 去重;成本~0)")
