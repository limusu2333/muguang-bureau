#!/usr/bin/env python3
"""公司知识的「语义查资料」——按『意思』在公司知识里找最相关的片段(不靠字面关键词)。

搜索定位两条腿(2026 共识)：
  · 精确找文件/关键词 → Grep(ripgrep) / 净Glob(剪枝) —— 见 搜索工具.py
  · 按意思找概念/背景/为什么 → 本模块的 semantic_search —— 向量语义检索

复用 记忆向量.py 的 DashScope embedding(text-embedding-v4) + FileEmbeddingCache(同文本不重复烧钱)。
只索引公司【真知识】(公司是谁/班规/会议真谛/岗位/技能/会议历史)，不碰 .venv/node_modules/官方参考/工单。
装给查资料的岗位(搜索工具.资料工具集)。这就是「装合适的能力给合适的人」,不手搓、不阉能力。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, ToolResponse

from 记忆向量 import _建模型, _余弦  # 复用 embedding 管子,不重造
from 根 import 代码根, 数据根, 本机环境文件

COMPANY = 数据根

# 公司知识范围：只这些是"该被按意思检索的知识"。不含依赖/工单/舟的规划文档。
_知识文件 = ["公司是谁_为什么干活.md", "班规.md", "会议的真谛_人类开会的意义与真正形式.md"]
_制度目录 = ["岗位", "技能"]
_运行目录 = ["会议", "协同/会议"]
_最大块字数 = 600


def _收集片段() -> list[tuple[str, str]]:
    """收公司知识 .md，按空行分段、合并到 ~600 字一块；返回 [(相对路径, 片段)]。"""
    文件: list[Path] = []
    for f in _知识文件:
        p = 代码根 / f
        if p.exists():
            文件.append(p)
    for root, dirs in ((代码根, _制度目录), (数据根, _运行目录)):
        for d in dirs:
            base = root / d
            if base.exists():
                文件 += sorted(base.rglob("*.md"))

    片段: list[tuple[str, str]] = []
    for p in 文件:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        root = 代码根 if p.is_relative_to(代码根) else 数据根
        rel = ("code:" if root == 代码根 else "data:") + str(p.relative_to(root))
        cur = ""
        for para in text.split("\n\n"):
            para = para.strip()
            if not para:
                continue
            if cur and len(cur) + len(para) > _最大块字数:
                片段.append((rel, cur))
                cur = para
            else:
                cur = (cur + "\n\n" + para) if cur else para
        if cur.strip():
            片段.append((rel, cur.strip()))
    return 片段


_向量缓存: dict[str, list[float]] = {}  # 进程内:片段文本→向量,一会内重复查不重算


async def _embed(model, 文本表: list[str]) -> list[list[float]]:
    """批量 embed(每批10条避 DashScope batch 上限);命中进程内/FileCache 的不重复烧钱。"""
    待算 = [t for t in dict.fromkeys(文本表) if t not in _向量缓存]
    for i in range(0, len(待算), 10):
        批 = 待算[i:i + 10]
        resp = await model(inputs=批)
        for t, v in zip(批, resp.embeddings):
            _向量缓存[t] = v
    return [_向量缓存[t] for t in 文本表]


async def _查async(查询: str, topN: int) -> list[dict[str, Any]]:
    片段 = _收集片段()
    if not 片段:
        return []
    model = _建模型()
    qv = (await _embed(model, [查询]))[0]
    向量表 = await _embed(model, [b for _, b in 片段])
    打分 = [(_余弦(qv, 向量表[i]), 片段[i][0], 片段[i][1]) for i in range(len(片段))]
    打分.sort(key=lambda x: x[0], reverse=True)
    return [{"相似度": round(s, 4), "文件": f, "片段": b} for s, f, b in 打分[:topN]]


async def semantic_search(query: str, top_n: int = 5) -> ToolResponse:
    """按『意思』在公司知识(公司是谁/班规/会议真谛/岗位说明书/技能/会议历史)里找最相关的片段。

    什么时候用我:你想搞清"公司为什么这么做/某个概念/背景/某岗位该怎么做/过去定过什么"这类
    【按意思找】的问题。精确找某文件名或某关键词,用 grep;找概念和背景,用我更准。

    Args:
        query (str): 你想按意思查的问题或主题(用自然语言,不用关键词)。
        top_n (int): 返回最相关的几段,默认 5。
    """
    try:
        res = await _查async(query, max(1, min(int(top_n), 10)))
    except Exception as e:  # noqa: BLE001
        return ToolResponse(content=[TextBlock(type="text", text=f"语义检索失败：{e}")])
    if not res:
        return ToolResponse(content=[TextBlock(type="text", text="公司知识里没按意思找到相关内容。")])
    out = [f"按意思找到 {len(res)} 段（相似度降序）："]
    for r in res:
        out.append(f"\n【{r['文件']}】(相似度 {r['相似度']})\n{r['片段'][:800]}")
    return ToolResponse(content=[TextBlock(type="text", text="\n".join(out))])


def 语义检索工具() -> FunctionTool:
    """包成 AgentScope 工具(英文 name 避 OpenAI 400)，给资料工具集用。"""
    return FunctionTool(semantic_search, name="semantic_search")


if __name__ == "__main__":
    import os
    envf = 本机环境文件()
    if envf is not None and envf.exists():
        for line in envf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    片段 = _收集片段()
    print(f"语义检索自检：公司知识共 {len(片段)} 段（来自 {len(set(f for f, _ in 片段))} 个文件）")
    查询 = "开会发言时观点要不要跟着别人变？"
    res = asyncio.run(_查async(查询, 3))
    print(f"查询：{查询}")
    for r in res:
        print(f"  [{r['相似度']}] 《{r['文件']}》：{r['片段'][:70].replace(chr(10),' ')}…")
    assert res, "应检索出结果"
    assert "真谛" in res[0]["文件"], f"语义检索应把会议真谛排第一,实际:{res[0]['文件']}"
    print("✓ 语义命中:'观点要不要跟着变'→会议真谛文档排第一(按意思找到,不靠字面)")
