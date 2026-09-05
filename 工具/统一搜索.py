#!/usr/bin/env python3
"""统一搜索中枢：公司文档、大厅原话、本人私册、公共图谱共用一条检索链。"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import math
import os
import re
import sqlite3
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import httpx

from 根 import 代码根, 数据根, 是远程实例, 搜索键

COMPANY = 数据根
默认索引 = 数据根 / "运行状态" / "统一搜索.db"
_事件标记 = re.compile(
    r"(?:<!--\s*)?(?:事件|event)(?:键|_key)?\s*[:：=]\s*([\w.-]+?)(?:\s*-->|\s|$)",
    re.I,
)
_日期 = re.compile(r"(?P<y>20\d{2})[-年/.](?P<m>\d{1,2})[-月/.](?P<d>\d{1,2})日?")
_空白 = re.compile(r"\s+")
_标点 = re.compile(r"[\s，。、；;：:！？!?（）()【】\[\]<>《》\-—_/\\\"'`]+")
_精排总等待秒 = 80.0


@dataclass
class EmbeddingVector:
    dense: list[float]
    sparse: dict[int, float]


@dataclass
class SearchDocument:
    document_id: str
    source_id: str
    source_type: str
    title: str
    path: str
    line: int
    content: str
    timestamp: str = ""
    person: str = ""
    event_key: str = ""
    authority: float = 0.7
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def index_text(self) -> str:
        prefix = " ".join(x for x in (self.title, self.person, self.timestamp) if x)
        return (prefix + "\n" + self.content).strip()

    @property
    def content_hash(self) -> str:
        raw = json.dumps({
            "title": self.title, "path": self.path, "line": self.line,
            "content": self.content, "timestamp": self.timestamp,
            "person": self.person, "event_key": self.event_key,
            "authority": self.authority, "metadata": self.metadata,
        }, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    @property
    def embedding_hash(self) -> str:
        return hashlib.sha256(self.index_text.encode()).hexdigest()


@dataclass
class SearchHit:
    document_id: str
    source: str
    source_type: str
    title: str
    path: str
    line: int
    content: str
    timestamp: str
    person: str
    score: float
    authority: float
    evidence: list[dict[str, Any]] = field(default_factory=list)
    graph_path: list[dict[str, Any]] = field(default_factory=list)
    also_sources: list[str] = field(default_factory=list)


@dataclass
class SearchResult:
    query: str
    status: str
    hits: list[SearchHit]
    source_status: dict[str, dict[str, Any]]
    searched_sources: list[str]
    filters: dict[str, Any]
    index_stats: dict[str, int]

    @property
    def complete(self) -> bool:
        return self.status in {"ok", "not_found"}


@dataclass
class SearchContext:
    # root 保留给旧测试/调用兼容；新代码按 code_root/data_root 明确选根。
    root: Path | None = None
    code_root: Path | None = None
    data_root: Path | None = None
    principal: str | None = None
    allowed_personal_memories: set[str] | None = None
    personal_memory_paths: dict[str, Path] | None = None
    source_specs: list[dict[str, Any]] | None = None
    index_db: Path | None = None
    graph_db: Path | None = None
    embedder: Any = None
    reranker: Any = None
    now: Callable[[], dt.datetime] = dt.datetime.now
    record_graph_hits: bool = False
    force_no_trigram: bool = False
    semantic_threshold: float = 0.50

    def __post_init__(self) -> None:
        legacy = Path(self.root).resolve() if self.root is not None else None
        self.code_root = Path(self.code_root or legacy or 代码根).resolve()
        self.data_root = Path(self.data_root or legacy or 数据根).resolve()
        self.root = self.data_root
        self.index_db = Path(self.index_db or (self.data_root / "运行状态" / "统一搜索.db"))
        if self.allowed_personal_memories is None:
            self.allowed_personal_memories = {self.principal} if self.principal else set()


class DashScopeHybridEmbedder:
    model_name = "qwen3.7-text-embedding"
    model_id = f"{model_name}:dense+sparse:1024"
    max_batch = 10

    def __init__(self) -> None:
        self.api_key = 搜索键()

    @staticmethod
    def _parse(resp: Any) -> list[EmbeddingVector]:
        code = int(getattr(resp, "status_code", 0) or 0)
        if code < 200 or code >= 300:
            raise RuntimeError(f"embedding 服务返回 HTTP {code or '未知'}")
        output = getattr(resp, "output", {}) or {}
        items = output.get("embeddings", []) if isinstance(output, dict) else getattr(output, "embeddings", [])
        out: list[EmbeddingVector] = []
        for item in items or []:
            dense = item.get("embedding") or item.get("dense_embedding") or []
            sparse_raw = item.get("sparse_embedding") or []
            sparse = {int(x["index"]): float(x["value"]) for x in sparse_raw if "index" in x and "value" in x}
            out.append(EmbeddingVector([float(x) for x in dense], sparse))
        if not out:
            raise RuntimeError("embedding 服务没有返回向量")
        return out

    async def embed_documents(self, texts: list[str]) -> list[EmbeddingVector]:
        import dashscope
        resp = await asyncio.to_thread(
            dashscope.TextEmbedding.call,
            model=self.model_name, input=texts, api_key=self.api_key,
            text_type="document", dimension=1024, output_type="dense&sparse",
        )
        return self._parse(resp)

    async def embed_query(self, text: str) -> EmbeddingVector:
        import dashscope
        resp = await asyncio.to_thread(
            dashscope.TextEmbedding.call,
            model=self.model_name, input=text, api_key=self.api_key,
            text_type="query", dimension=1024, output_type="dense&sparse",
            instruct="检索公司内部可追溯证据，重视人物、时间、事件和事实关系，避免只因主题相近而命中。",
        )
        return self._parse(resp)[0]


class 本地精排网关:
    """通过多用户搜索网关调用宿主机上的 MLX 4B；协议名只为兼容旧调用方。"""

    model_id = "mlx-community/Qwen3-Reranker-4B-mxfp8@25f203a237b8"
    max_documents = 16

    def __init__(self) -> None:
        self.api_key = 搜索键()
        self.base_url = os.environ.get("DASHSCOPE_HTTP_BASE_URL", "http://search-gw:4100").rstrip("/")

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if len(documents) > self.max_documents:
            raise ValueError(f"本地精排一次最多接收 {self.max_documents} 篇候选，实际 {len(documents)} 篇")
        async with httpx.AsyncClient(timeout=65, follow_redirects=False, trust_env=False) as client:
            resp = await client.post(
                self.base_url + "/services/rerank/text-rerank/text-rerank",
                json={
                    "model": "qwen3-rerank",
                    "input": {"query": str(query), "documents": [str(item) for item in documents]},
                    "parameters": {
                        "top_n": len(documents), "return_documents": False,
                        "instruct": "按是否能直接回答查询、出处是否明确、人物和时间是否一致排序。仅主题相近但不能作证的内容应低分。",
                    },
                },
                headers={"authorization": "Bearer " + self.api_key},
            )
        if resp.status_code < 200 or resp.status_code >= 300:
            raise RuntimeError(f"本地精排网关返回 HTTP {resp.status_code}")
        payload = resp.json()
        output = payload.get("output", {}) if isinstance(payload, dict) else {}
        items = output.get("results", []) if isinstance(output, dict) else []
        scores = [0.0] * len(documents)
        for item in items or []:
            idx = int(item.get("index", -1))
            if 0 <= idx < len(scores):
                scores[idx] = float(item.get("relevance_score", item.get("score", 0.0)) or 0.0)
        return scores


def _默认精排器() -> Any:
    if 是远程实例():
        return 本地精排网关()
    from 本地精排客户端 import 默认精排器

    return 默认精排器()


def _current_principal() -> str | None:
    try:
        import 活_本人
        role = 活_本人._当前岗位_var.get()
        return 活_本人._岗位的人(role) if role else None
    except Exception:  # noqa: BLE001
        return None


def default_context() -> SearchContext:
    person = _current_principal()
    return SearchContext(principal=person, allowed_personal_memories={person} if person else set())


def _source_specs(ctx: SearchContext) -> list[dict[str, Any]]:
    if ctx.source_specs is not None:
        return [dict(x) for x in ctx.source_specs]
    import 房间注册
    out: list[dict[str, Any]] = []
    for room in 房间注册.各房间():
        for spec in room.get("搜索源", []) or []:
            item = dict(spec)
            if 是远程实例() and item.get("远程") is False:
                continue
            item.setdefault("房间", room.get("名称", ""))
            out.append(item)
    return out


def _spec_root(ctx: SearchContext, spec: dict[str, Any]) -> Path:
    assert ctx.code_root is not None and ctx.data_root is not None
    kind = str(spec.get("根") or "data").lower()
    if kind == "code":
        return ctx.code_root
    if kind == "data":
        return ctx.data_root
    raise ValueError(f"搜索源根只能是 code 或 data，实际为：{kind}")


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path.resolve())


def _extract_event_key(text: str) -> str:
    m = _事件标记.search(text or "")
    return m.group(1) if m else ""


def _clean_content(text: str) -> str:
    return re.sub(r"<!--\s*(?:事件|event)(?:键|_key)?\s*[:：=].*?-->", "", text or "", flags=re.I).strip()


def _markdown_chunks(text: str, limit: int = 900) -> list[tuple[int, str]]:
    chunks: list[tuple[int, str]] = []
    current: list[str] = []
    current_start = 1
    headings: list[str] = []

    def flush() -> None:
        nonlocal current, current_start
        body = "\n".join(current).strip()
        if body:
            prefix = " > ".join(headings[-3:])
            chunks.append((current_start, (prefix + "\n" + body).strip() if prefix else body))
        current = []

    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            flush()
            level = len(stripped) - len(stripped.lstrip("#"))
            heading = stripped[level:].strip()
            headings[:] = headings[:max(0, level - 1)]
            if heading:
                headings.append(heading)
            current_start = lineno + 1
            continue
        if not stripped:
            if current and len("\n".join(current)) >= limit // 2:
                flush()
                current_start = lineno + 1
            continue
        if not current:
            current_start = lineno
        projected = len("\n".join(current)) + len(stripped) + 1
        if current and projected > limit:
            flush()
            current_start = lineno
        current.append(stripped)
    flush()
    return chunks


def _personal_chunks(path: Path, person: str, limit: int = 650) -> list[SearchDocument]:
    text = path.read_text(encoding="utf-8")
    docs: list[SearchDocument] = []
    for n, (line, body) in enumerate(_markdown_chunks(text, limit), 1):
        cleaned = _clean_content(body)
        if not cleaned:
            continue
        docs.append(SearchDocument(
            document_id=f"personal:{person}:{n}:{hashlib.sha1(cleaned.encode()).hexdigest()[:12]}",
            source_id=f"personal:{person}", source_type="personal_memory",
            title=f"{person}的个人记忆", path=str(path), line=line,
            content=cleaned, person=person, event_key=_extract_event_key(body), authority=0.78,
            metadata={"label": "本人私册", "private_owner": person},
        ))
    return docs


def _iter_markdown_paths(root: Path, spec: dict[str, Any]) -> list[Path]:
    paths = spec.get("路径", [])
    if isinstance(paths, str):
        paths = [paths]
    recursive = bool(spec.get("递归", True))
    excludes = [str(x) for x in spec.get("排除", []) or []]
    found: set[Path] = set()
    for raw in paths:
        raw = str(raw)
        candidates: Iterable[Path]
        if any(x in raw for x in "*?["):
            candidates = root.glob(raw)
        else:
            p = (root / raw).resolve()
            if not p.is_relative_to(root):
                raise ValueError(f"搜索源路径越出公司：{raw}")
            if p.is_file():
                candidates = [p]
            elif p.is_dir():
                candidates = p.rglob("*.md") if recursive else p.glob("*.md")
            else:
                if spec.get("必需", False):
                    raise FileNotFoundError(f"搜索源不存在：{raw}")
                candidates = []
        for p in candidates:
            if not p.is_file() or p.suffix.lower() != ".md":
                continue
            rel = _relative(root, p)
            if any(Path(rel).match(pattern) or rel.startswith(pattern.rstrip("/") + "/") for pattern in excludes):
                continue
            if any(part.startswith(".") for part in p.relative_to(root).parts):
                continue
            if "_历史_勿执行" in p.parts or "scratchpad" in p.parts:
                continue
            found.add(p.resolve())
    return sorted(found)


def _collect_markdown(ctx: SearchContext, spec: dict[str, Any]) -> list[SearchDocument]:
    sid = str(spec["id"])
    label = str(spec.get("标签") or sid)
    authority = float(spec.get("权威", 0.65))
    docs: list[SearchDocument] = []
    root = _spec_root(ctx, spec)
    for path in _iter_markdown_paths(root, spec):
        rel = f"{spec.get('根', 'data')}:" + _relative(root, path)
        text = path.read_text(encoding="utf-8")
        for line, body in _markdown_chunks(text, int(spec.get("块字数", 900))):
            cleaned = _clean_content(body)
            if not cleaned:
                continue
            key = hashlib.sha1(f"{rel}:{line}:{cleaned}".encode()).hexdigest()[:16]
            docs.append(SearchDocument(
                document_id=f"{sid}:{key}", source_id=sid, source_type="company_document",
                title=path.stem, path=rel, line=line, content=cleaned,
                event_key=_extract_event_key(body), authority=authority,
                metadata={"label": label, "room": spec.get("房间", "")},
            ))
    return docs


def _collect_hall(ctx: SearchContext, spec: dict[str, Any]) -> list[SearchDocument]:
    sid = str(spec["id"])
    root = _spec_root(ctx, spec)
    base = (root / str(spec["路径"])).resolve()
    if not base.is_relative_to(root):
        raise ValueError("大厅搜索源越出公司")
    docs: list[SearchDocument] = []
    import 大厅档案
    for path in 大厅档案.各日文件(base):
        day = 大厅档案.日期(path)
        for line, raw in enumerate(大厅档案.读文本(path).splitlines(), 1):
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            content = str(item.get("text") or item.get("content") or "").strip()
            if not content:
                continue
            person = str(item.get("who") or item.get("name") or "").strip()
            when = str(item.get("时间") or item.get("time") or day).strip()
            event_key = str(item.get("事件键") or item.get("event_key") or _extract_event_key(content)).strip()
            rel = f"data:{_relative(root, path)}"
            docs.append(SearchDocument(
                document_id=f"{sid}:{rel}:{line}", source_id=sid, source_type="hall",
                title=f"大厅 {day}", path=rel, line=line, content=_clean_content(content),
                timestamp=when, person=person, event_key=event_key, authority=float(spec.get("权威", 1.0)),
                metadata={"label": spec.get("标签", "大厅原话")},
            ))
    return docs


def _normalize(text: str) -> str:
    return _标点.sub("", (text or "").lower())


def _collect_graph(ctx: SearchContext, spec: dict[str, Any]) -> list[SearchDocument]:
    sid = str(spec["id"])
    root = _spec_root(ctx, spec)
    path = Path(ctx.graph_db or (root / str(spec["路径"]))).resolve()
    if not path.is_relative_to(root):
        raise ValueError("图谱搜索源越出公司")
    if not path.exists():
        return []
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT e.*, s.name src_name, d.name dst_name, ep.content episode_content, "
            "ep.source episode_source, ep.valid_at episode_valid_at "
            "FROM edges e JOIN entities s ON s.id=e.src "
            "LEFT JOIN entities d ON d.id=e.dst_entity LEFT JOIN episodes ep ON ep.id=e.episode_id "
            "WHERE e.invalid_at IS NULL AND e.expired_at IS NULL AND e.status='现行'"
        ).fetchall()
        black = {str(r[0]) for r in con.execute("SELECT fp FROM 黑名单").fetchall()}
    finally:
        con.close()
    docs: list[SearchDocument] = []
    for row in rows:
        dst = str(row["dst_name"] or row["dst_literal"] or "").strip()
        src = str(row["src_name"] or "").strip()
        fp = f"{src}|{row['rel']}|{_normalize(dst)}"
        if fp in black:
            continue
        fact = str(row["fact"] or "").strip()
        original = str(row["episode_content"] or "").strip()
        content = f"{src} {row['rel']} {dst}。{fact}"
        if original and original not in content:
            content += f"\n原话：{original}"
        source = str(row["episode_source"] or "")
        confidence = float(row["confidence"] or 0.0)
        authority = min(1.0, float(spec.get("权威", 0.8)) * max(0.4, confidence))
        if str(row["来源类"] or "") == "亲口" or int(row["pinned"] or 0):
            authority = 1.0
        docs.append(SearchDocument(
            document_id=f"{sid}:edge:{row['id']}", source_id=sid, source_type="graph",
            title=f"{src} —{row['rel']}→ {dst}", path=f"data:{_relative(root, path)}#edge-{row['id']}",
            line=0, content=content, timestamp=str(row["valid_at"] or row["episode_valid_at"] or ""),
            person=src, event_key=_extract_event_key(source + " " + original), authority=authority,
            metadata={
                "label": spec.get("标签", "公共事实图谱"), "edge_id": int(row["id"]),
                "src": src, "rel": str(row["rel"]), "dst": dst, "original": original,
                "episode_id": row["episode_id"], "episode_source": source,
            },
        ))
    return docs


def _personal_paths(ctx: SearchContext) -> dict[str, Path]:
    allowed = set(ctx.allowed_personal_memories or set())
    if not allowed:
        return {}
    if ctx.personal_memory_paths is not None:
        return {p: Path(path) for p, path in ctx.personal_memory_paths.items() if p in allowed}
    import 部门
    return {person: 部门.个人记忆路径(person) for person in allowed}


def _collect_sources(ctx: SearchContext) -> tuple[list[SearchDocument], dict[str, dict[str, Any]]]:
    docs: list[SearchDocument] = []
    status: dict[str, dict[str, Any]] = {}
    for spec in _source_specs(ctx):
        sid = str(spec.get("id") or "")
        try:
            kind = str(spec.get("类型") or "")
            if kind == "markdown":
                found = _collect_markdown(ctx, spec)
            elif kind == "hall_jsonl":
                found = _collect_hall(ctx, spec)
            elif kind == "graph":
                found = _collect_graph(ctx, spec)
            else:
                raise ValueError(f"未知搜索源类型：{kind}")
            docs.extend(found)
            status[sid] = {"status": "ok", "documents": len(found), "label": spec.get("标签", sid)}
        except Exception as exc:  # noqa: BLE001
            status[sid] = {"status": "error", "documents": 0, "label": spec.get("标签", sid),
                           "detail": f"{type(exc).__name__}: {str(exc)[:180]}"}
    paths = _personal_paths(ctx)
    personal_id = f"personal:{ctx.principal}" if ctx.principal else "personal"
    if not ctx.principal:
        status[personal_id] = {"status": "skipped", "documents": 0, "label": "本人私册", "detail": "当前没有可验证的调用者身份"}
    elif ctx.principal not in set(ctx.allowed_personal_memories or set()):
        status[personal_id] = {"status": "error", "documents": 0, "label": "本人私册", "detail": "调用者无权读取该私册"}
    else:
        try:
            path = paths.get(ctx.principal)
            found = _personal_chunks(path, ctx.principal) if path and path.exists() else []
            docs.extend(found)
            status[personal_id] = {"status": "ok", "documents": len(found), "label": "本人私册"}
        except Exception as exc:  # noqa: BLE001
            status[personal_id] = {"status": "error", "documents": 0, "label": "本人私册",
                                   "detail": f"{type(exc).__name__}: {str(exc)[:180]}"}
    return docs, status


def _connect_index(ctx: SearchContext) -> sqlite3.Connection:
    assert ctx.index_db is not None
    ctx.index_db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ctx.index_db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS documents(
          document_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_type TEXT NOT NULL,
          title TEXT NOT NULL, path TEXT NOT NULL, line INTEGER NOT NULL, content TEXT NOT NULL,
          index_text TEXT NOT NULL, timestamp TEXT, person TEXT, event_key TEXT,
          authority REAL NOT NULL, metadata TEXT NOT NULL, content_hash TEXT NOT NULL,
          embedding_hash TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_documents_source ON documents(source_id);
        CREATE TABLE IF NOT EXISTS embeddings(
          document_id TEXT NOT NULL, model_id TEXT NOT NULL, embedding_hash TEXT NOT NULL,
          dense BLOB NOT NULL, sparse TEXT NOT NULL,
          PRIMARY KEY(document_id, model_id));
        """
    )
    mode = "unicode61" if ctx.force_no_trigram else "trigram"
    old = con.execute("SELECT v FROM settings WHERE k='tokenizer'").fetchone()
    if old and old["v"] != mode:
        con.execute("DROP TABLE IF EXISTS search_fts")
    tokenizer = "unicode61" if mode == "unicode61" else "trigram"
    try:
        con.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(document_id UNINDEXED, text, tokenize='{tokenizer}')")
    except sqlite3.Error as exc:
        raise RuntimeError(f"SQLite FTS5/{tokenizer} 不可用：{exc}") from exc
    con.execute("INSERT OR REPLACE INTO settings(k,v) VALUES('tokenizer',?)", (mode,))
    con.commit()
    return con


def _roster_people(root: Path | None) -> set[str] | None:
    """返回现役花名册人名；没有花名册的隔离索引不做跨人员清理。"""
    if root is None or not (root / "花名册.yaml").exists():
        return None
    try:
        import yaml
        roster = yaml.safe_load((root / "花名册.yaml").read_text(encoding="utf-8")) or {}
        return {str((item or {}).get("名字") or "").strip() for item in roster.values()} - {""}
    except Exception:  # noqa: BLE001 花名册坏由组织契约负责报错；搜索此处不趁机误删缓存
        return None


def _sync_index(con: sqlite3.Connection, docs: list[SearchDocument], status: dict[str, dict[str, Any]],
                root: Path | None = None) -> dict[str, int]:
    by_source: dict[str, list[SearchDocument]] = {}
    for doc in docs:
        by_source.setdefault(doc.source_id, []).append(doc)
    stats = {"new": 0, "changed": 0, "removed": 0, "orphaned": 0, "unchanged": 0}
    now = dt.datetime.now().isoformat(timespec="seconds")
    roster_people = _roster_people(root)
    if roster_people is not None:
        valid_personal = {f"personal:{person}" for person in roster_people}
        old_personal = {
            str(r[0]) for r in con.execute(
                "SELECT DISTINCT source_id FROM documents WHERE source_id LIKE 'personal:%'"
            )
        }
        for sid in old_personal - valid_personal:
            stale_ids = [str(r[0]) for r in con.execute("SELECT document_id FROM documents WHERE source_id=?", (sid,))]
            for stale in stale_ids:
                con.execute("DELETE FROM search_fts WHERE document_id=?", (stale,))
                con.execute("DELETE FROM embeddings WHERE document_id=?", (stale,))
                con.execute("DELETE FROM documents WHERE document_id=?", (stale,))
                stats["orphaned"] += 1
    for sid, state in status.items():
        if state.get("status") != "ok":
            continue
        current_ids = {d.document_id for d in by_source.get(sid, [])}
        old_ids = {str(r[0]) for r in con.execute("SELECT document_id FROM documents WHERE source_id=?", (sid,))}
        for stale in old_ids - current_ids:
            con.execute("DELETE FROM search_fts WHERE document_id=?", (stale,))
            con.execute("DELETE FROM embeddings WHERE document_id=?", (stale,))
            con.execute("DELETE FROM documents WHERE document_id=?", (stale,))
            stats["removed"] += 1
    for doc in docs:
        old = con.execute("SELECT content_hash FROM documents WHERE document_id=?", (doc.document_id,)).fetchone()
        if old and old["content_hash"] == doc.content_hash:
            stats["unchanged"] += 1
            continue
        stats["changed" if old else "new"] += 1
        con.execute("DELETE FROM search_fts WHERE document_id=?", (doc.document_id,))
        con.execute(
            "INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc.document_id, doc.source_id, doc.source_type, doc.title, doc.path, doc.line,
             doc.content, doc.index_text, doc.timestamp, doc.person, doc.event_key, doc.authority,
             json.dumps(doc.metadata, ensure_ascii=False), doc.content_hash, doc.embedding_hash, now),
        )
        con.execute("INSERT INTO search_fts(document_id,text) VALUES(?,?)", (doc.document_id, doc.index_text))
        con.execute("DELETE FROM embeddings WHERE document_id=? AND embedding_hash<>?", (doc.document_id, doc.embedding_hash))
    con.commit()
    return stats


def _pack_dense(values: list[float]) -> bytes:
    return array("f", values).tobytes()


def _unpack_dense(raw: bytes) -> list[float]:
    values = array("f")
    values.frombytes(raw)
    return list(values)


def _coerce_vector(value: Any) -> EmbeddingVector:
    if isinstance(value, EmbeddingVector):
        return value
    if isinstance(value, dict):
        return EmbeddingVector(list(value.get("dense", [])), {int(k): float(v) for k, v in value.get("sparse", {}).items()})
    if isinstance(value, tuple) and len(value) == 2:
        return EmbeddingVector(list(value[0]), {int(k): float(v) for k, v in dict(value[1]).items()})
    return EmbeddingVector(list(value or []), {})


async def _ensure_embeddings(con: sqlite3.Connection, ctx: SearchContext, embedder: Any) -> int:
    model_id = str(getattr(embedder, "model_id", type(embedder).__name__))
    rows = con.execute(
        "SELECT d.document_id,d.index_text,d.embedding_hash FROM documents d "
        "LEFT JOIN embeddings e ON e.document_id=d.document_id AND e.model_id=? "
        "WHERE e.document_id IS NULL OR e.embedding_hash<>d.embedding_hash ORDER BY d.document_id",
        (model_id,),
    ).fetchall()
    batch_size = max(1, min(int(getattr(embedder, "max_batch", 10)), 32))
    done = 0
    batches = [rows[start:start + batch_size] for start in range(0, len(rows), batch_size)]
    for wave_start in range(0, len(batches), 4):
        wave = batches[wave_start:wave_start + 4]
        results = await asyncio.gather(*(
            embedder.embed_documents([str(r["index_text"]) for r in batch]) for batch in wave
        ))
        for batch, vectors in zip(wave, results):
            if len(vectors) != len(batch):
                raise RuntimeError(f"embedding 数量不一致：请求 {len(batch)}，返回 {len(vectors)}")
            for row, raw in zip(batch, vectors):
                vec = _coerce_vector(raw)
                con.execute(
                    "INSERT OR REPLACE INTO embeddings(document_id,model_id,embedding_hash,dense,sparse) VALUES(?,?,?,?,?)",
                    (row["document_id"], model_id, row["embedding_hash"], _pack_dense(vec.dense),
                     json.dumps(vec.sparse, ensure_ascii=False, separators=(",", ":"))),
                )
                done += 1
        con.commit()
    return done


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _sparse_cosine(a: dict[int, float], b: dict[int, float]) -> float:
    if not a or not b:
        return 0.0
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    dot = sum(v * large.get(k, 0.0) for k, v in small.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _char_ngrams(text: str, n: int) -> set[str]:
    value = _normalize(text)
    if len(value) <= n:
        return {value} if value else set()
    return {value[i:i + n] for i in range(len(value) - n + 1)}


def _lexical_score(query: str, text: str) -> float:
    q = _normalize(query)
    t = _normalize(text)
    if not q or not t:
        return 0.0
    if q in t:
        return 1.0
    n = 2 if len(q) < 5 else 3
    qg = _char_ngrams(q, n)
    tg = _char_ngrams(t, n)
    overlap = len(qg & tg) / max(1, len(qg))
    words = [w for w in _标点.split(query.lower()) if len(w) >= 2]
    word_hit = sum(1 for w in words if w in text.lower()) / max(1, len(words))
    return min(1.0, max(overlap, word_hit))


def _fts_scores(con: sqlite3.Connection, query: str, allowed_ids: set[str]) -> dict[str, float]:
    q = _normalize(query)
    if not q:
        return {}
    grams = list(_char_ngrams(q, 3))[:32]
    if not grams:
        return {}
    expr = " OR ".join('"' + x.replace('"', '""') + '"' for x in grams)
    try:
        rows = con.execute(
            "SELECT document_id,bm25(search_fts) rank FROM search_fts WHERE search_fts MATCH ? ORDER BY rank LIMIT 240",
            (expr,),
        ).fetchall()
    except sqlite3.Error:
        return {}
    filtered = [r for r in rows if str(r["document_id"]) in allowed_ids]
    return {str(r["document_id"]): 1.0 / (1.0 + abs(float(r["rank"] or 0.0))) for r in filtered}


def _parse_date(value: str) -> dt.date | None:
    value = str(value or "").strip()
    m = _日期.search(value)
    if m:
        try:
            return dt.date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
        except ValueError:
            return None
    try:
        return dt.date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def _infer_time(query: str, filters: dict[str, Any], now: dt.datetime) -> None:
    if filters.get("start_at") or filters.get("end_at"):
        return
    m = _日期.search(query)
    if m:
        day = _parse_date(m.group(0))
        if day:
            filters["start_at"] = filters["end_at"] = day.isoformat()
            return
    today = now.date()
    if "前天" in query:
        day = today - dt.timedelta(days=2)
        filters["start_at"] = filters["end_at"] = day.isoformat()
    elif "昨天" in query:
        day = today - dt.timedelta(days=1)
        filters["start_at"] = filters["end_at"] = day.isoformat()
    elif "今天" in query:
        filters["start_at"] = filters["end_at"] = today.isoformat()
    else:
        recent = re.search(r"最近\s*(\d{1,3})\s*天", query)
        if recent:
            filters["start_at"] = (today - dt.timedelta(days=int(recent.group(1)) - 1)).isoformat()
            filters["end_at"] = today.isoformat()


def _expand_aliases(root: Path, query: str) -> str:
    try:
        import yaml
        roster = yaml.safe_load((root / "花名册.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return query
    additions: list[str] = []
    for role, item in roster.items():
        item = item or {}
        names = [str(item.get("名字") or ""), str(role), *[str(x) for x in item.get("别名", []) or []]]
        if any(x and x in query for x in names):
            additions.extend(x for x in names if x and x not in query)
    return query + (" " + " ".join(dict.fromkeys(additions)) if additions else "")


def _load_documents(con: sqlite3.Connection, source_ids: set[str], filters: dict[str, Any]) -> list[SearchDocument]:
    if not source_ids:
        return []
    placeholders = ",".join("?" for _ in source_ids)
    rows = con.execute(f"SELECT * FROM documents WHERE source_id IN ({placeholders})", tuple(sorted(source_ids))).fetchall()
    sources = set(filters.get("sources") or [])
    people = {str(x) for x in filters.get("people") or [] if str(x)}
    start = _parse_date(filters.get("start_at", ""))
    end = _parse_date(filters.get("end_at", ""))
    out: list[SearchDocument] = []
    for r in rows:
        if sources and str(r["source_type"]) not in sources and str(r["source_id"]) not in sources:
            continue
        content = str(r["content"])
        person = str(r["person"] or "")
        if people and not (person in people or any(p in content for p in people)):
            continue
        if start or end:
            day = _parse_date(str(r["timestamp"] or ""))
            if not day or (start and day < start) or (end and day > end):
                continue
        out.append(SearchDocument(
            document_id=str(r["document_id"]), source_id=str(r["source_id"]), source_type=str(r["source_type"]),
            title=str(r["title"]), path=str(r["path"]), line=int(r["line"]), content=content,
            timestamp=str(r["timestamp"] or ""), person=person, event_key=str(r["event_key"] or ""),
            authority=float(r["authority"]), metadata=json.loads(str(r["metadata"] or "{}")),
        ))
    return out


def _load_vectors(con: sqlite3.Connection, model_id: str, ids: set[str]) -> dict[str, EmbeddingVector]:
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = con.execute(
        f"SELECT document_id,dense,sparse FROM embeddings WHERE model_id=? AND document_id IN ({placeholders})",
        (model_id, *sorted(ids)),
    ).fetchall()
    return {
        str(r["document_id"]): EmbeddingVector(
            _unpack_dense(r["dense"]), {int(k): float(v) for k, v in json.loads(str(r["sparse"])).items()},
        ) for r in rows
    }


def _rank_positions(scores: dict[str, float]) -> dict[str, int]:
    return {doc_id: i for i, (doc_id, score) in enumerate(sorted(scores.items(), key=lambda x: x[1], reverse=True), 1) if score > 0}


def _graph_expansion(docs: dict[str, SearchDocument], lexical: dict[str, float], dense: dict[str, float],
                     sparse: dict[str, float], query: str) -> tuple[dict[str, float], dict[str, list[str]]]:
    graph_docs = {k: v for k, v in docs.items() if v.source_type == "graph"}
    adjacency: dict[str, list[str]] = {}
    for doc_id, doc in graph_docs.items():
        src = _normalize(str(doc.metadata.get("src", "")))
        dst = _normalize(str(doc.metadata.get("dst", "")))
        if src:
            adjacency.setdefault(src, []).append(doc_id)
        if dst:
            adjacency.setdefault(dst, []).append(doc_id)
    q = _normalize(query)
    seed_ids = [
        doc_id for doc_id, doc in graph_docs.items()
        if lexical.get(doc_id, 0) >= 0.12
        or dense.get(doc_id, 0) >= 0.50
        or sparse.get(doc_id, 0) >= 0.08
        or any(_normalize(str(doc.metadata.get(k, ""))) in q for k in ("src", "dst") if _normalize(str(doc.metadata.get(k, ""))))
    ]
    seed_ids = sorted(seed_ids, key=lambda x: max(lexical.get(x, 0), dense.get(x, 0), sparse.get(x, 0)), reverse=True)[:12]
    boosts: dict[str, float] = {}
    paths: dict[str, list[str]] = {}
    for seed in seed_ids:
        boosts[seed] = max(boosts.get(seed, 0), 1.0)
        paths.setdefault(seed, [seed])
        frontier = [(seed, [seed])]
        for depth in range(1, 3):
            next_frontier: list[tuple[str, list[str]]] = []
            for current, path in frontier:
                doc = graph_docs[current]
                nodes = {_normalize(str(doc.metadata.get("src", ""))), _normalize(str(doc.metadata.get("dst", "")))}
                for node in nodes - {""}:
                    for neighbor in adjacency.get(node, []):
                        if neighbor in path:
                            continue
                        new_path = path + [neighbor]
                        boost = 1.0 if depth == 1 else 0.7
                        if boost > boosts.get(neighbor, 0):
                            boosts[neighbor] = boost
                            paths[neighbor] = new_path
                        if len(new_path) < 3:
                            next_frontier.append((neighbor, new_path))
            frontier = next_frontier[:80]
    return boosts, paths


def _evidence(doc: SearchDocument) -> dict[str, Any]:
    return {
        "source": str(doc.metadata.get("label") or doc.source_id), "source_id": doc.source_id,
        "path": doc.path, "line": doc.line, "timestamp": doc.timestamp,
        "person": doc.person, "quote": doc.content[:700], "authority": round(doc.authority, 3),
    }


def _normalize_rerank(raw: Any, count: int) -> list[float]:
    if isinstance(raw, dict):
        return [float(raw.get(i, raw.get(str(i), 0.0))) for i in range(count)]
    values = list(raw or [])
    if values and isinstance(values[0], (tuple, list)):
        scores = [0.0] * count
        for idx, score in values:
            if 0 <= int(idx) < count:
                scores[int(idx)] = float(score)
        return scores
    return [float(values[i]) if i < len(values) else 0.0 for i in range(count)]


async def search(query: str, top_n: int = 6, filters: dict[str, Any] | None = None,
                 context: SearchContext | None = None) -> SearchResult:
    ctx = context or default_context()
    query = str(query or "").strip()
    if not query:
        return SearchResult(query, "failed", [], {"query": {"status": "error", "detail": "查询不能为空"}}, [], {}, {})
    filters = dict(filters or {})
    _infer_time(query, filters, ctx.now())
    assert ctx.code_root is not None
    expanded_query = _expand_aliases(ctx.code_root, query)
    docs, source_status = await asyncio.to_thread(_collect_sources, ctx)
    try:
        con = _connect_index(ctx)
        index_stats = _sync_index(con, docs, source_status, ctx.code_root)
    except Exception as exc:  # noqa: BLE001
        source_status["index"] = {"status": "error", "detail": f"{type(exc).__name__}: {str(exc)[:180]}"}
        return SearchResult(query, "failed", [], source_status, list(source_status), filters, {})

    embedder = ctx.embedder
    try:
        embedder = embedder or DashScopeHybridEmbedder()
        embedded = await _ensure_embeddings(con, ctx, embedder)
        index_stats["embedded"] = embedded
        query_vector = _coerce_vector(await embedder.embed_query(expanded_query))
        source_status["semantic"] = {"status": "ok", "model": getattr(embedder, "model_id", type(embedder).__name__)}
    except Exception as exc:  # noqa: BLE001
        query_vector = EmbeddingVector([], {})
        source_status["semantic"] = {"status": "error", "detail": f"{type(exc).__name__}: {str(exc)[:180]}"}
        if 是远程实例():
            return SearchResult(query, "failed", [], source_status, ["semantic"], filters, {})

    ok_source_ids = {sid for sid, state in source_status.items() if state.get("status") == "ok" and sid not in {"semantic", "rerank"}}
    selected = _load_documents(con, ok_source_ids, filters)
    doc_map = {d.document_id: d for d in selected}
    ids = set(doc_map)
    model_id = str(getattr(embedder, "model_id", type(embedder).__name__)) if embedder else ""
    vectors = _load_vectors(con, model_id, ids) if query_vector.dense or query_vector.sparse else {}
    fts = _fts_scores(con, expanded_query, ids)
    lexical = {doc_id: max(_lexical_score(expanded_query, doc.index_text), fts.get(doc_id, 0.0)) for doc_id, doc in doc_map.items()}
    dense = {doc_id: _cosine(query_vector.dense, vec.dense) for doc_id, vec in vectors.items()}
    sparse = {doc_id: _sparse_cosine(query_vector.sparse, vec.sparse) for doc_id, vec in vectors.items()}
    graph_boost, graph_paths = _graph_expansion(doc_map, lexical, dense, sparse, expanded_query)

    lanes = [_rank_positions(lexical), _rank_positions(dense), _rank_positions(sparse), _rank_positions(graph_boost)]
    rrf: dict[str, float] = {doc_id: 0.0 for doc_id in ids}
    for lane in lanes:
        for doc_id, rank in lane.items():
            rrf[doc_id] += 1.0 / (60 + rank)
    rrf_norm = {doc_id: min(1.0, score / (4.0 / 61.0)) for doc_id, score in rrf.items()}
    eligible = {
        doc_id for doc_id in ids
        if lexical.get(doc_id, 0) >= 0.16
        or dense.get(doc_id, 0) >= ctx.semantic_threshold
        or sparse.get(doc_id, 0) >= 0.08
        or graph_boost.get(doc_id, 0) > 0
    }
    candidates = sorted(eligible, key=lambda d: (
        rrf_norm.get(d, 0), max(lexical.get(d, 0), dense.get(d, 0), sparse.get(d, 0), graph_boost.get(d, 0)),
        doc_map[d].authority,
    ), reverse=True)[:16]

    rerank_scores = [0.0] * len(candidates)
    if candidates:
        try:
            reranker = ctx.reranker or _默认精排器()
            raw = await asyncio.wait_for(
                reranker.rerank(query, [doc_map[d].index_text for d in candidates]),
                timeout=_精排总等待秒,
            )
            rerank_scores = _normalize_rerank(raw, len(candidates))
            source_status["rerank"] = {"status": "ok", "model": getattr(reranker, "model_id", type(reranker).__name__)}
        except Exception as exc:  # noqa: BLE001
            source_status["rerank"] = {"status": "error", "detail": f"{type(exc).__name__}: {str(exc)[:180]}"}
    else:
        source_status["rerank"] = {"status": "ok", "detail": "没有达到召回门槛的候选，无需精排"}

    scored: list[tuple[float, str]] = []
    rerank_ok = source_status["rerank"]["status"] == "ok"
    for idx, doc_id in enumerate(candidates):
        doc = doc_map[doc_id]
        evidence_strength = max(lexical.get(doc_id, 0), dense.get(doc_id, 0), sparse.get(doc_id, 0), graph_boost.get(doc_id, 0))
        if rerank_ok:
            rerank_score = rerank_scores[idx]
            source_floor = 0.75 if doc.source_type == "graph" else 0.60
            exact_enough = lexical.get(doc_id, 0) >= 0.35 and rerank_score >= 0.25
            sparse_enough = sparse.get(doc_id, 0) >= 0.32 and rerank_score >= 0.45
            if not (rerank_score >= source_floor or exact_enough or sparse_enough):
                continue
            final = 0.52 * rerank_scores[idx] + 0.24 * rrf_norm.get(doc_id, 0) + 0.14 * evidence_strength + 0.10 * doc_map[doc_id].authority
        else:
            # 精排坏了时只保留强字面/强稀疏证据；纯向量“最像的一条”不能冒充答案。
            if lexical.get(doc_id, 0) < 0.25 and sparse.get(doc_id, 0) < 0.20:
                continue
            final = 0.55 * rrf_norm.get(doc_id, 0) + 0.30 * evidence_strength + 0.15 * doc_map[doc_id].authority
        if final >= 0.50:
            scored.append((final, doc_id))
    scored.sort(reverse=True)

    event_groups: dict[str, list[SearchDocument]] = {}
    exact_groups: dict[str, list[SearchDocument]] = {}
    for doc in doc_map.values():
        if doc.event_key:
            event_groups.setdefault(doc.event_key, []).append(doc)
        exact_groups.setdefault(hashlib.sha1(_normalize(doc.content).encode()).hexdigest(), []).append(doc)
    hits: list[SearchHit] = []
    seen_keys: set[str] = set()
    for score, doc_id in scored:
        doc = doc_map[doc_id]
        group_key = "event:" + doc.event_key if doc.event_key else "content:" + hashlib.sha1(_normalize(doc.content).encode()).hexdigest()
        if group_key in seen_keys:
            continue
        seen_keys.add(group_key)
        related = event_groups.get(doc.event_key, []) if doc.event_key else exact_groups.get(group_key.removeprefix("content:"), [])
        evidence = [_evidence(x) for x in related]
        path_docs = [doc_map[x] for x in graph_paths.get(doc_id, []) if x in doc_map]
        for path_doc in path_docs:
            ev = _evidence(path_doc)
            if ev not in evidence:
                evidence.append(ev)
        hits.append(SearchHit(
            document_id=doc.document_id, source=str(doc.metadata.get("label") or doc.source_id),
            source_type=doc.source_type, title=doc.title, path=doc.path, line=doc.line,
            content=doc.content, timestamp=doc.timestamp, person=doc.person,
            score=round(score, 4), authority=doc.authority, evidence=evidence,
            graph_path=[{
                "edge_id": x.metadata.get("edge_id"), "src": x.metadata.get("src"),
                "rel": x.metadata.get("rel"), "dst": x.metadata.get("dst"),
                "original": x.metadata.get("original", ""),
            } for x in path_docs],
            also_sources=sorted({str(x.metadata.get("label") or x.source_id) for x in related if x.document_id != doc.document_id}),
        ))
        if len(hits) >= max(1, min(int(top_n or 6), 10)):
            break

    errors = [s for s in source_status.values() if s.get("status") == "error"]
    successful_sources = [sid for sid in ok_source_ids if sid not in {"semantic", "rerank"}]
    if not successful_sources:
        result_status = "failed"
    elif errors:
        result_status = "partial"
    elif hits:
        result_status = "ok"
    else:
        result_status = "not_found"
    con.close()
    return SearchResult(query, result_status, hits, source_status, sorted(successful_sources), filters, index_stats)


async def warm_index(context: SearchContext | None = None) -> SearchResult:
    """显式预热索引；不拿无关查询凑结果。"""
    ctx = context or default_context()
    docs, source_status = await asyncio.to_thread(_collect_sources, ctx)
    try:
        con = _connect_index(ctx)
        assert ctx.code_root is not None
        stats = _sync_index(con, docs, source_status, ctx.code_root)
        embedder = ctx.embedder or DashScopeHybridEmbedder()
        stats["embedded"] = await _ensure_embeddings(con, ctx, embedder)
        source_status["semantic"] = {"status": "ok", "model": getattr(embedder, "model_id", type(embedder).__name__)}
        con.close()
    except Exception as exc:  # noqa: BLE001
        source_status["index"] = {"status": "error", "detail": f"{type(exc).__name__}: {str(exc)[:180]}"}
        return SearchResult("", "failed", [], source_status, [], {}, {})
    errors = any(x.get("status") == "error" for x in source_status.values())
    return SearchResult("", "partial" if errors else "ok", [], source_status,
                        sorted(sid for sid, state in source_status.items() if state.get("status") == "ok"), {}, stats)


async def api_smoke(embedder: Any = None, reranker: Any = None) -> dict[str, Any]:
    """真实接口最小自检：不建全库，只验证混合向量和重排的形状、方向与可用性。"""
    embedder = embedder or DashScopeHybridEmbedder()
    reranker = reranker or _默认精排器()
    relevant = "酒红版本需要回炉，边框太抢，需要重新做。"
    unrelated = "火星基地氧气循环参数。"
    query = "把酒红方案退回重新来"
    docs = [_coerce_vector(x) for x in await embedder.embed_documents([relevant, unrelated])]
    qv = _coerce_vector(await embedder.embed_query(query))
    scores = _normalize_rerank(await reranker.rerank(query, [relevant, unrelated]), 2)
    dense_scores = [_cosine(qv.dense, x.dense) for x in docs]
    sparse_scores = [_sparse_cosine(qv.sparse, x.sparse) for x in docs]
    if not qv.dense or not qv.sparse:
        raise RuntimeError(f"{getattr(embedder, 'model_name', 'embedding')} 未同时返回 dense 和 sparse")
    if scores[0] <= scores[1]:
        raise RuntimeError("本地 Qwen3-Reranker-4B 没把相关证据排在无关文本前")
    return {
        "status": "ok", "checked_at": dt.datetime.now().isoformat(timespec="seconds"),
        "embedding_model": getattr(embedder, "model_id", type(embedder).__name__),
        "rerank_model": getattr(reranker, "model_id", type(reranker).__name__),
        "dense_dimension": len(qv.dense), "sparse_terms": len(qv.sparse),
        "dense_scores": [round(x, 4) for x in dense_scores],
        "sparse_scores": [round(x, 4) for x in sparse_scores],
        "rerank_scores": [round(x, 4) for x in scores],
    }


def render_result(result: SearchResult) -> str:
    labels = {"ok": "完整", "partial": "不完整", "not_found": "完整但无可靠命中", "failed": "失败"}
    out = [f"【统一搜索：{labels.get(result.status, result.status)}】查询：{result.query}"]
    errors = [f"{state.get('label', sid)}：{state.get('detail', '故障')}" for sid, state in result.source_status.items() if state.get("status") == "error"]
    if errors:
        out.append("⚠️ 故障：" + "；".join(errors) + "。下面结果不能当作‘全公司只有这些/没有别的’。")
    if not result.hits:
        if result.status == "not_found":
            out.append("没有找到达到证据门槛的内容；没有拿低分第一名硬凑答案。")
        elif result.status == "failed":
            out.append("搜索链没有完成，不能据此判断有没有。")
        else:
            out.append("当前可用数据源里没有可靠命中，但搜索链不完整，不能据此判断全局没有。")
        return "\n".join(out)
    for i, hit in enumerate(result.hits, 1):
        who = f"｜{hit.person}" if hit.person else ""
        when = f"｜{hit.timestamp}" if hit.timestamp else ""
        line = f":{hit.line}" if hit.line else ""
        out.append(f"\n{i}. [{hit.source}] {hit.title}{who}{when}｜证据分 {hit.score:.3f}")
        out.append(hit.content[:900])
        out.append(f"出处：{hit.path}{line}")
        if hit.also_sources:
            out.append("同一事件的其他出处：" + "、".join(hit.also_sources))
        if hit.graph_path:
            out.append("图谱路径：" + " → ".join(f"{x['src']} -{x['rel']}→ {x['dst']}" for x in hit.graph_path))
    return "\n".join(out)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="统一搜索索引与查询")
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--principal", default="")
    parser.add_argument("--warm", action="store_true")
    args = parser.parse_args()
    context = SearchContext(principal=args.principal or None,
                            allowed_personal_memories={args.principal} if args.principal else set())
    if args.warm:
        print(json.dumps(asyncio.run(warm_index(context)).index_stats, ensure_ascii=False, indent=2))
    else:
        print(render_result(asyncio.run(search(args.query, context=context))))
