"""Strict embedding gateway with a local, host-side reranker."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response

from .账本 import 搜索账本, 账本拒绝


_上游 = "https://dashscope.aliyuncs.com/api/v1"
_最大请求 = 256 * 1024
_最大响应 = 24 * 1024 * 1024
_embedding_path = "/services/embeddings/text-embedding/text-embedding"
_rerank_path = "/services/rerank/text-rerank/text-rerank"
_embedding_model = "qwen3.7-text-embedding"


def _价格(name: str) -> Decimal:
    try:
        value = Decimal(os.environ[name])
    except (KeyError, InvalidOperation) as exc:
        raise RuntimeError(f"缺少有效的 {name}") from exc
    if not value.is_finite() or value <= 0:
        raise RuntimeError(f"{name} 必须大于 0")
    return value


def _成本微元(tokens: int, price_per_million: Decimal) -> int:
    # LiteLLM uses USD; keep search estimates in the same currency so totals are meaningful.
    return int((Decimal(tokens) * price_per_million).to_integral_value(rounding=ROUND_CEILING))


def _文本(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise HTTPException(400, f"{name} 无效")
    return value


def _只含(value: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise HTTPException(400, f"{name} 含不允许的参数：{', '.join(sorted(unknown))}")


async def _请求体(request: Request) -> dict[str, Any]:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _最大请求:
                raise HTTPException(413, "搜索请求体过大")
        except ValueError as exc:
            raise HTTPException(400, "Content-Length 无效") from exc
    buffer = bytearray()
    async for chunk in request.stream():
        buffer.extend(chunk)
        if len(buffer) > _最大请求:
            raise HTTPException(413, "搜索请求体过大")
    raw = bytes(buffer)
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "搜索请求不是有效 JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "搜索请求体必须是对象")
    return body


def _验证_embedding(body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    _只含(body, {"model", "input", "parameters"}, "embedding 请求")
    if body.get("model") != _embedding_model:
        raise HTTPException(400, f"只允许 {_embedding_model}")
    inputs = body.get("input")
    params = body.get("parameters")
    if not isinstance(inputs, dict) or not isinstance(params, dict):
        raise HTTPException(400, "embedding input/parameters 无效")
    _只含(inputs, {"texts"}, "embedding input")
    _只含(params, {"text_type", "dimension", "output_type", "instruct"}, "embedding parameters")
    texts = inputs.get("texts")
    if not isinstance(texts, list) or not 1 <= len(texts) <= 10:
        raise HTTPException(400, "embedding 每次只允许 1 到 10 段文本")
    clean = [_文本(item, "embedding 文本", 16_384) for item in texts]
    if sum(len(item) for item in clean) > 100_000:
        raise HTTPException(400, "embedding 文本总量过大")
    if params.get("text_type") not in {"query", "document"}:
        raise HTTPException(400, "embedding text_type 只允许 query/document")
    if params.get("dimension") != 1024 or params.get("output_type") != "dense&sparse":
        raise HTTPException(400, "embedding 只允许 1024 维 dense&sparse")
    if "instruct" in params:
        _文本(params["instruct"], "embedding instruct", 1000)
    estimated = sum(len(item.encode("utf-8")) for item in clean)
    if "instruct" in params:
        estimated += len(params["instruct"].encode("utf-8"))
    return body, max(1, estimated)


def _验证_rerank(body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    _只含(body, {"model", "input", "parameters"}, "rerank 请求")
    if body.get("model") != "qwen3-rerank":
        raise HTTPException(400, "只允许兼容协议模型名 qwen3-rerank")
    inputs = body.get("input")
    params = body.get("parameters")
    if not isinstance(inputs, dict) or not isinstance(params, dict):
        raise HTTPException(400, "rerank input/parameters 无效")
    _只含(inputs, {"query", "documents"}, "rerank input")
    _只含(params, {"top_n", "return_documents", "instruct"}, "rerank parameters")
    query = _文本(inputs.get("query"), "rerank query", 16_384)
    documents = inputs.get("documents")
    if not isinstance(documents, list) or not 1 <= len(documents) <= 16:
        raise HTTPException(400, "rerank 每次只允许 1 到 16 段候选文本")
    clean = [_文本(item, "rerank 文本", 16_384) for item in documents]
    if len(query) + sum(len(item) for item in clean) > 200_000:
        raise HTTPException(400, "rerank 文本总量过大")
    top_n = params.get("top_n")
    if not isinstance(top_n, int) or isinstance(top_n, bool) or not 1 <= top_n <= len(clean):
        raise HTTPException(400, "rerank top_n 无效")
    if params.get("return_documents", False) is not False:
        raise HTTPException(400, "rerank 不允许返回原文副本")
    if "instruct" in params:
        _文本(params["instruct"], "rerank instruct", 1000)
    estimated = len(query.encode("utf-8")) + sum(len(item.encode("utf-8")) for item in clean)
    if "instruct" in params:
        estimated += len(params["instruct"].encode("utf-8"))
    return body, max(1, estimated)


def _bearer(authorization: str | None) -> str:
    value = str(authorization or "")
    if not value.startswith("Bearer ") or len(value) > 1024:
        raise HTTPException(401, "缺少搜索实例令牌")
    return value[7:]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.ledger = 搜索账本(
        os.environ["XJ_SEARCH_DATABASE_URL"],
        os.environ.get("XJ_BUDGET_TIMEZONE", "Asia/Shanghai"),
    )
    app.state.true_key = os.environ["EDITH_DASHSCOPE_REAL_KEY"]
    if not app.state.true_key:
        raise RuntimeError("EDITH_DASHSCOPE_REAL_KEY 不能为空")
    app.state.admin_token = os.environ["XJ_SEARCH_ADMIN_TOKEN"]
    if len(app.state.admin_token) < 32:
        raise RuntimeError("XJ_SEARCH_ADMIN_TOKEN 长度不足")
    app.state.prices = {"embedding": _价格("XJ_EMBED_USD_PER_MTOKENS"), "rerank": Decimal("0")}
    app.state.local_rerank_url = os.environ.get("XJ_LOCAL_RERANK_URL", "").rstrip("/")
    app.state.local_rerank_token = os.environ.get("XJ_RERANK_TOKEN", "")
    if not app.state.local_rerank_url or len(app.state.local_rerank_token) < 32:
        raise RuntimeError("本地精排服务配置不完整")
    app.state.client = httpx.AsyncClient(timeout=180, follow_redirects=False, trust_env=False)
    try:
        health = await app.state.client.get(
            app.state.local_rerank_url + "/healthz",
            headers={"authorization": "Bearer " + app.state.local_rerank_token},
        )
        health_payload = health.json() if health.headers.get("content-type", "").startswith("application/json") else {}
        if health.status_code != 200 or not isinstance(health_payload, dict) or health_payload.get("ok") is not True:
            raise RuntimeError(f"本地精排健康检查失败（HTTP {health.status_code}）")
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError("本地精排健康检查不可达") from exc
    yield
    await app.state.client.aclose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}


async def _转发(request: Request, authorization: str | None, kind: str) -> Response:
    token = _bearer(authorization)
    body = await _请求体(request)
    clean, estimated_tokens = _验证_embedding(body) if kind == "embedding" else _验证_rerank(body)
    cost = _成本微元(estimated_tokens, request.app.state.prices[kind])
    try:
        event_id, _ = await asyncio.to_thread(
            request.app.state.ledger.预留,
            token,
            kind=kind,
            estimated_tokens=estimated_tokens,
            estimated_cost_micros=cost,
        )
    except 账本拒绝 as exc:
        raise HTTPException(429, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    upstream_status: int | None = None
    state = "upstream_error"
    try:
        if kind == "rerank":
            inputs = clean["input"]
            local_response = await request.app.state.client.post(
                request.app.state.local_rerank_url + "/rerank",
                json={"query": inputs["query"], "documents": inputs["documents"]},
                headers={"authorization": "Bearer " + request.app.state.local_rerank_token},
            )
            if local_response.status_code == 200:
                payload = local_response.json()
                scores = payload.get("scores") if isinstance(payload, dict) else None
                model = payload.get("model") if isinstance(payload, dict) else ""
                if not isinstance(scores, list) or len(scores) != len(inputs["documents"]):
                    raise HTTPException(502, "本地精排返回的分数数量不正确")
                response = httpx.Response(
                    200,
                    json={
                        "output": {
                            "results": [
                                {"index": index, "relevance_score": float(score)}
                                for index, score in enumerate(scores)
                            ]
                        },
                        "model": model,
                    },
                )
            else:
                response = local_response
        else:
            response = await request.app.state.client.post(
                _上游 + request.url.path,
                json=clean,
                headers={"authorization": "Bearer " + request.app.state.true_key, "content-type": "application/json"},
            )
        upstream_status = response.status_code
        if len(response.content) > _最大响应:
            raise HTTPException(502, "搜索上游响应过大")
        state = "ok" if 200 <= response.status_code < 300 else "upstream_error"
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type=response.headers.get("content-type", "application/json").split(";", 1)[0],
        )
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise HTTPException(502, "搜索上游当前不可达") from exc
    finally:
        try:
            await asyncio.to_thread(
                request.app.state.ledger.完成,
                event_id,
                status=state,
                upstream_status=upstream_status,
            )
        except RuntimeError as exc:
            # 账本确认失败时不能把上游成功伪装成可计费成功。
            raise HTTPException(503, str(exc)) from exc


@app.post(_embedding_path)
async def embedding(request: Request, authorization: str | None = Header(default=None)) -> Response:
    return await _转发(request, authorization, "embedding")


@app.post(_rerank_path)
async def rerank(request: Request, authorization: str | None = Header(default=None)) -> Response:
    return await _转发(request, authorization, "rerank")


def _管理员(request: Request, authorization: str | None) -> None:
    supplied = _bearer(authorization)
    if not hmac.compare_digest(supplied, request.app.state.admin_token):
        raise HTTPException(403, "搜索网关管理员令牌无效")


@app.post("/admin/tokens")
async def create_token(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _管理员(request, authorization)
    body = await _请求体(request)
    _只含(body, {"account_id", "daily_limit", "monthly_limit", "rpm_limit", "token"}, "搜索令牌配置")
    try:
        token = await asyncio.to_thread(
            request.app.state.ledger.创建令牌,
            str(body.get("account_id") or ""),
            daily_limit=body.get("daily_limit"),
            monthly_limit=body.get("monthly_limit"),
            rpm_limit=int(body.get("rpm_limit", 60)),
            token=body.get("token"),
        )
    except (账本拒绝, ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"token": token}


@app.delete("/admin/tokens/{account_id}")
async def revoke_token(account_id: str, request: Request, authorization: str | None = Header(default=None)) -> dict[str, bool]:
    _管理员(request, authorization)
    try:
        await asyncio.to_thread(request.app.state.ledger.撤销, account_id)
    except 账本拒绝 as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"ok": True}


@app.post("/admin/tokens/{account_id}/{action}")
async def set_token_state(account_id: str, action: str, request: Request, authorization: str | None = Header(default=None)) -> dict[str, bool]:
    _管理员(request, authorization)
    if action not in {"block", "unblock"}:
        raise HTTPException(404, "未知动作")
    try:
        await asyncio.to_thread(request.app.state.ledger.置启用, account_id, action == "unblock")
    except 账本拒绝 as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"ok": True}
