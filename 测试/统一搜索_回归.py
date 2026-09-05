"""统一搜索的隔离回归；全部模型均为假实现，不碰真实 API。"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


工具目录 = Path(__file__).resolve().parents[1] / "工具"
if str(工具目录) not in sys.path:
    sys.path.insert(0, str(工具目录))

import 图记忆  # noqa: E402
import 本地精排  # noqa: E402
import 统一搜索  # noqa: E402
from 统一搜索 import EmbeddingVector, SearchContext, api_smoke, search  # noqa: E402


class FakeEmbedder:
    model_id = "fake-hybrid-v1"

    def __init__(self, max_batch: int = 8):
        self.max_batch = max_batch
        self.document_batches: list[list[str]] = []

    @staticmethod
    def _vector(text: str) -> EmbeddingVector:
        text = str(text)
        if any(x in text for x in ("回炉", "返工", "退回", "重新来", "酒红")):
            return EmbeddingVector([1.0, 0.0, 0.0, 0.0], {1: 1.0})
        if any(x in text for x in ("银杏-417", "银杏暗号")):
            return EmbeddingVector([0.0, 1.0, 0.0, 0.0], {2: 1.0})
        if any(x in text for x in ("雪松-993", "雪松暗号")):
            return EmbeddingVector([0.0, 0.0, 1.0, 0.0], {3: 1.0})
        if any(x in text for x in ("火星基地", "氧气循环")):
            return EmbeddingVector([0.0, 0.0, 0.0, 1.0], {4: 1.0})
        if any(x in text for x in ("A7M5", "装备", "晨曦婚礼", "下周")):
            return EmbeddingVector([0.7, 0.7, 0.0, 0.0], {5: 1.0})
        return EmbeddingVector([0.0, 0.0, 0.0, 0.0], {})

    async def embed_documents(self, texts: list[str]) -> list[EmbeddingVector]:
        assert len(texts) <= self.max_batch
        self.document_batches.append(list(texts))
        return [self._vector(x) for x in texts]

    async def embed_query(self, text: str) -> EmbeddingVector:
        return self._vector(text)


class FakeReranker:
    model_id = "fake-rerank-v1"
    max_documents = 16

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        assert len(documents) <= self.max_documents
        q = FakeEmbedder._vector(query).dense
        out = []
        for doc in documents:
            d = FakeEmbedder._vector(doc).dense
            out.append(0.95 if q == d and any(q) else 0.05)
        return out


class LowReranker:
    model_id = "fake-low-rerank"
    max_documents = 16

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        return [0.40] * len(documents)


class FailingReranker:
    model_id = "fake-busy-rerank"
    max_documents = 16

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        raise RuntimeError("本地精排正在忙，等待队列已满")


class SameVectorEmbedder(FakeEmbedder):
    model_id = "fake-same-vector"

    @staticmethod
    def _vector(text: str) -> EmbeddingVector:
        return EmbeddingVector([1.0, 0.0], {})


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")


def _context(root: Path, specs: list[dict], *, principal: str | None = None,
             paths: dict[str, Path] | None = None, embedder: FakeEmbedder | None = None,
             reranker=None) -> SearchContext:
    return SearchContext(
        root=root, principal=principal,
        allowed_personal_memories={principal} if principal else set(),
        personal_memory_paths=paths, source_specs=specs,
        index_db=root / "运行状态" / "统一搜索.db",
        embedder=embedder or FakeEmbedder(), reranker=reranker or FakeReranker(),
        force_no_trigram=True,
    )


def test_统一搜索_同义改写与两字关键词都有出处():
    with tempfile.TemporaryDirectory(prefix="unified-search-") as td:
        root = Path(td)
        _write_jsonl(root / "大厅/对话/2026-07-08.jsonl", [
            {"who": "老纪", "时间": "2026-07-08 10:00", "text": "酒红版本需要回炉，边框太抢。"},
        ])
        ctx = _context(root, [{"id": "hall", "类型": "hall_jsonl", "路径": "大厅/对话", "标签": "大厅原话"}])
        semantic = asyncio.run(search("把这套视觉退回重新来", context=ctx))
        assert semantic.status == "ok" and semantic.hits
        hit = semantic.hits[0]
        assert "回炉" in hit.content and hit.path.endswith("2026-07-08.jsonl") and hit.line == 1
        short = asyncio.run(search("酒红", context=ctx))
        assert short.hits and short.hits[0].person == "老纪" and short.hits[0].timestamp.startswith("2026-07-08")


def test_统一搜索_人物时间先过滤再截断():
    with tempfile.TemporaryDirectory(prefix="unified-search-") as td:
        root = Path(td)
        rows = [
            {"who": "老梁", "时间": "2026-07-10 10:00", "text": f"酒红验收方案需要返工 {i}"}
            for i in range(25)
        ]
        rows.append({"who": "老纪", "时间": "2026-07-08 09:00", "text": "酒红版本我验过，需要回炉。"})
        _write_jsonl(root / "大厅/对话/2026-07-10.jsonl", rows)
        ctx = _context(root, [{"id": "hall", "类型": "hall_jsonl", "路径": "大厅/对话", "标签": "大厅原话"}])
        result = asyncio.run(search(
            "酒红返工", top_n=1,
            filters={"people": ["老纪"], "start_at": "2026-07-08", "end_at": "2026-07-08"},
            context=ctx,
        ))
        assert len(result.hits) == 1 and result.hits[0].person == "老纪"


def test_统一搜索_同事件跨源合并且保留四份证据():
    with tempfile.TemporaryDirectory(prefix="unified-search-") as td:
        root = Path(td)
        event = "<!--事件:evt-red-001-->"
        (root / "知识").mkdir(parents=True)
        (root / "知识/规则.md").write_text(f"# 复盘\n{event}\n酒红方案需要回炉重新做。", encoding="utf-8")
        _write_jsonl(root / "大厅/对话/2026-07-08.jsonl", [
            {"who": "老纪", "时间": "2026-07-08 10:00", "event_key": "evt-red-001", "text": "酒红版本需要回炉。"},
        ])
        memory = root / "测试部/老纪.md"
        memory.parent.mkdir(parents=True)
        memory.write_text(f"# 老纪的记忆\n## 教训\n{event}\n- 酒红版本返工时要看边框。", encoding="utf-8")
        graph = root / "资料室/图谱.db"
        ep = 图记忆.落原话("酒红版本需要回炉", "event:evt-red-001", "2026-07-08", graph)
        图记忆.落边("老纪", "验收", "酒红版本", "老纪验收后要求酒红版本回炉", ep, 库=graph)
        specs = [
            {"id": "docs", "类型": "markdown", "路径": ["知识"], "标签": "公司文档"},
            {"id": "hall", "类型": "hall_jsonl", "路径": "大厅/对话", "标签": "大厅原话"},
            {"id": "graph", "类型": "graph", "路径": "资料室/图谱.db", "标签": "公共图谱"},
        ]
        ctx = _context(root, specs, principal="老纪", paths={"老纪": memory})
        result = asyncio.run(search("酒红返工", context=ctx))
        assert result.hits
        merged = result.hits[0]
        assert len({x["source_id"] for x in merged.evidence}) == 4, merged.evidence


def test_统一搜索_个人记忆权限不能由查询参数越权():
    with tempfile.TemporaryDirectory(prefix="unified-search-") as td:
        root = Path(td)
        old = root / "测试部/老纪.md"
        qiang = root / "编程部/阿强.md"
        old.parent.mkdir(parents=True); qiang.parent.mkdir(parents=True)
        old.write_text("# 老纪\n## 私记\n- 银杏-417 是我的暗号。", encoding="utf-8")
        qiang.write_text("# 阿强\n## 私记\n- 雪松-993 是我的暗号。", encoding="utf-8")
        ctx = _context(root, [], principal="老纪", paths={"老纪": old, "阿强": qiang})
        own = asyncio.run(search("银杏暗号", context=ctx))
        assert own.hits and "银杏-417" in own.hits[0].content
        leaked = asyncio.run(search("雪松暗号", filters={"people": ["阿强"], "memory_owner": "阿强"}, context=ctx))
        assert not leaked.hits
        con = sqlite3.connect(ctx.index_db)
        assert con.execute("SELECT count(*) FROM documents WHERE content LIKE '%雪松-993%'").fetchone()[0] == 0
        con.close()


def test_统一搜索_图谱真正两跳且严格只读():
    with tempfile.TemporaryDirectory(prefix="unified-search-") as td:
        graph = Path(td) / "图谱.db"
        ep1 = 图记忆.落原话("示例主人说下周拍晨曦婚礼", "event:g-2hop", "2026-07-20", graph)
        first = 图记忆.落边("示例主人", "拍", "晨曦婚礼", "示例主人下周拍晨曦婚礼", ep1, 库=graph)
        ep2 = 图记忆.落原话("晨曦婚礼使用索尼A7M5", "event:g-2hop", "2026-07-20", graph)
        图记忆.落边("晨曦婚礼", "使用机身", "索尼A7M5", "晨曦婚礼使用索尼A7M5", ep2, 库=graph)
        con = sqlite3.connect(graph)
        before = con.execute("SELECT strength,last_hit FROM edges WHERE id=?", (first,)).fetchone()
        con.close()
        result = 图记忆.检索("示例主人 下周 装备", topN=3, 跳=2, 库=graph, record_hits=False)
        target = [x for x in result if x["dst"] == "索尼A7M5"]
        assert target and len(target[0]["路径"]) == 2
        assert all(x["原话"] for x in target[0]["路径"])
        con = sqlite3.connect(graph)
        after = con.execute("SELECT strength,last_hit FROM edges WHERE id=?", (first,)).fetchone()
        con.close()
        assert before == after


def test_统一搜索_负查询不拿低分第一名凑答案():
    with tempfile.TemporaryDirectory(prefix="unified-search-") as td:
        root = Path(td)
        (root / "知识").mkdir()
        (root / "知识/规则.md").write_text("# 公司规则\n验收必须留证据。", encoding="utf-8")
        ctx = _context(root, [{"id": "docs", "类型": "markdown", "路径": ["知识"], "标签": "公司规则"}])
        result = asyncio.run(search("火星基地氧气循环参数", context=ctx))
        assert result.status == "not_found" and result.hits == []


def test_统一搜索_纯向量很像但重排低分仍不凑数():
    with tempfile.TemporaryDirectory(prefix="unified-search-") as td:
        root = Path(td)
        (root / "知识").mkdir()
        (root / "知识/规则.md").write_text("# 公司规则\n验收必须留证据。", encoding="utf-8")
        ctx = SearchContext(
            root=root, source_specs=[{"id": "docs", "类型": "markdown", "路径": ["知识"], "标签": "公司规则"}],
            index_db=root / "运行状态/统一搜索.db", embedder=SameVectorEmbedder(), reranker=LowReranker(),
            force_no_trigram=True,
        )
        result = asyncio.run(search("火星基地氧气循环参数", context=ctx))
        assert result.status == "not_found" and result.hits == []


def test_统一搜索_真实接口自检契约可离线验证():
    result = asyncio.run(api_smoke(FakeEmbedder(), FakeReranker()))
    assert result["status"] == "ok"
    assert result["dense_dimension"] == 4 and result["sparse_terms"] > 0
    assert result["rerank_scores"][0] > result["rerank_scores"][1]


def test_本地精排_异步并发入口保序且限制候选数():
    assert 本地精排.最大文档数 == 16
    assert 本地精排.最大长度 == 2048
    encoded = [(0, [1] * 1800), (1, [2] * 100), (2, [3] * 1900)]
    batches = 本地精排._分批(encoded)
    assert all(max(len(tokens) for _, tokens in batch) * len(batch) <= 4096 for batch in batches)
    assert sorted(index for batch in batches for index, _ in batch) == [0, 1, 2]


def test_统一搜索_默认精排按本机与正式用户边界选择():
    local = object()
    remote = object()
    with patch("统一搜索.是远程实例", return_value=True), patch(
        "统一搜索.本地精排网关", return_value=remote,
    ):
        assert 统一搜索._默认精排器() is remote
    with patch("统一搜索.是远程实例", return_value=False), patch(
        "本地精排客户端.默认精排器", return_value=local,
    ):
        assert 统一搜索._默认精排器() is local


def test_统一搜索_精排候选最多16篇():
    class CountingReranker(FakeReranker):
        def __init__(self):
            self.count = 0

        async def rerank(self, query: str, documents: list[str]) -> list[float]:
            self.count = len(documents)
            return await super().rerank(query, documents)

    with tempfile.TemporaryDirectory(prefix="unified-search-") as td:
        root = Path(td)
        _write_jsonl(root / "大厅/对话/2026-07-08.jsonl", [
            {"who": "老纪", "时间": "2026-07-08", "text": f"酒红版本需要返工回炉 {index}"}
            for index in range(30)
        ])
        reranker = CountingReranker()
        ctx = _context(
            root,
            [{"id": "hall", "类型": "hall_jsonl", "路径": "大厅/对话", "标签": "大厅原话"}],
            reranker=reranker,
        )
        result = asyncio.run(search("酒红返工", context=ctx))
        assert result.hits
        assert reranker.count == 16


def test_统一搜索_精排繁忙时保留强字面证据而不让整轮失败():
    with tempfile.TemporaryDirectory(prefix="unified-search-") as td:
        root = Path(td)
        _write_jsonl(root / "大厅/对话/2026-07-08.jsonl", [
            {"who": "老纪", "时间": "2026-07-08", "text": "酒红版本需要返工回炉。"},
        ])
        ctx = _context(
            root,
            [{"id": "hall", "类型": "hall_jsonl", "路径": "大厅/对话", "标签": "大厅原话"}],
            reranker=FailingReranker(),
        )
        result = asyncio.run(search("酒红返工回炉", context=ctx))
        assert result.status == "partial"
        assert result.hits
        assert result.source_status["rerank"]["status"] == "error"


def test_统一搜索_单源损坏大声标记而非假装全局无结果():
    with tempfile.TemporaryDirectory(prefix="unified-search-") as td:
        root = Path(td)
        _write_jsonl(root / "大厅/对话/2026-07-08.jsonl", [
            {"who": "老纪", "时间": "2026-07-08", "text": "酒红版本需要回炉。"},
        ])
        bad = root / "资料室/图谱.db"
        bad.parent.mkdir(parents=True)
        bad.write_bytes(b"not-a-sqlite-db")
        specs = [
            {"id": "hall", "类型": "hall_jsonl", "路径": "大厅/对话", "标签": "大厅原话"},
            {"id": "graph", "类型": "graph", "路径": "资料室/图谱.db", "标签": "公共图谱"},
        ]
        result = asyncio.run(search("酒红返工", context=_context(root, specs)))
        assert result.status == "partial" and result.hits
        assert result.source_status["graph"]["status"] == "error" and result.source_status["graph"].get("detail")


def test_统一搜索_长记忆分批和增量索引():
    with tempfile.TemporaryDirectory(prefix="unified-search-") as td:
        root = Path(td)
        hall = root / "大厅/对话/2026-07-08.jsonl"
        _write_jsonl(hall, [
            {"who": "老纪", "时间": "2026-07-08 10:00", "text": "普通记录一"},
            {"who": "老纪", "时间": "2026-07-08 10:01", "text": "普通记录二"},
        ])
        memory = root / "测试部/老纪.md"
        memory.parent.mkdir(parents=True)
        memory.write_text("# 老纪\n## 流水\n" + "\n".join(f"- 普通经验 {i}" for i in range(160)) + "\n- 酒红版本需要回炉\n", encoding="utf-8")
        embedder = FakeEmbedder(max_batch=7)
        specs = [{"id": "hall", "类型": "hall_jsonl", "路径": "大厅/对话", "标签": "大厅原话"}]
        ctx = _context(root, specs, principal="老纪", paths={"老纪": memory}, embedder=embedder)
        first = asyncio.run(search("酒红返工", context=ctx))
        assert first.hits and max(map(len, embedder.document_batches)) <= 7
        embedded_first = sum(map(len, embedder.document_batches))
        asyncio.run(search("酒红返工", context=ctx))
        assert sum(map(len, embedder.document_batches)) == embedded_first
        old_stat = hall.stat()
        with hall.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"who": "老纪", "时间": "2026-07-08 10:02", "text": "酒红追加记录需要返工"}, ensure_ascii=False) + "\n")
        os.utime(hall, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        asyncio.run(search("酒红返工", context=ctx))
        assert sum(map(len, embedder.document_batches)) == embedded_first + 1
        hall.unlink()
        after_delete = asyncio.run(search("追加记录", filters={"sources": ["hall"]}, context=ctx))
        assert after_delete.hits == []


def test_统一搜索_离职人员私册缓存会从索引清掉():
    with tempfile.TemporaryDirectory(prefix="unified-search-") as td:
        root = Path(td)
        (root / "花名册.yaml").write_text(
            "工程师:\n  名字: 阿强\n旧岗位:\n  名字: 离职者\n",
            encoding="utf-8",
        )
        old_memory = root / "旧部/离职者.md"
        old_memory.parent.mkdir(parents=True)
        old_memory.write_text("# 离职者\n## 原则\n- 银杏暗号只在旧私册。\n", encoding="utf-8")
        old_ctx = _context(root, [], principal="离职者", paths={"离职者": old_memory})
        asyncio.run(search("银杏暗号", context=old_ctx))
        con = sqlite3.connect(root / "运行状态/统一搜索.db")
        assert con.execute("SELECT count(*) FROM documents WHERE source_id='personal:离职者'").fetchone()[0] > 0
        con.close()

        (root / "花名册.yaml").write_text("工程师:\n  名字: 阿强\n", encoding="utf-8")
        current_memory = root / "编程部/阿强.md"
        current_memory.parent.mkdir(parents=True)
        current_memory.write_text("# 阿强\n## 原则\n- 雪松暗号属于现役。\n", encoding="utf-8")
        current_ctx = _context(root, [], principal="阿强", paths={"阿强": current_memory})
        asyncio.run(search("雪松暗号", context=current_ctx))
        con = sqlite3.connect(root / "运行状态/统一搜索.db")
        assert con.execute("SELECT count(*) FROM documents WHERE source_id='personal:离职者'").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM embeddings WHERE document_id LIKE 'personal:离职者:%'").fetchone()[0] == 0
        con.close()
