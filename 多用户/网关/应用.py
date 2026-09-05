"""Public reverse proxy and the isolated instance control gateway.

Login visuals are deliberately outside this module and remain a separate approval point.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import quote, urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

_去除请求头 = {
    "host", "content-length", "connection", "cookie", "x-xj-csrf-token",
    "x-xj-account-id", "x-xj-user-id", "x-forwarded-user", "x-forwarded-email",
}
_去除响应头 = {"content-length", "connection", "transfer-encoding"}
_最大请求 = 20 * 1024 * 1024
_最大认证请求 = 64 * 1024
_最大反馈请求 = 7 * 1024 * 1024
_账户格式 = re.compile(r"acc_[0-9A-HJKMNP-TV-Z]{26}", re.IGNORECASE)
_本机测试值 = os.environ.get("XJ_LOCAL_TEST_MODE", "0")
if _本机测试值 not in {"0", "1"}:
    raise RuntimeError("XJ_LOCAL_TEST_MODE 只能是 0 或 1")
_本机测试 = _本机测试值 == "1"
_会话Cookie = "xj-local-session" if _本机测试 else "__Host-xj-session"
_防伪Cookie = "xj-local-csrf" if _本机测试 else "__Host-xj-csrf"
_不改数据的方法 = {"GET", "HEAD", "OPTIONS"}
_界面根 = Path(__file__).resolve().parents[1] / "界面" / "static"


class 控制客户端:
    _动作 = {
        "runtime-context", "inspect-invite", "activate", "login", "unified-login", "consume-user-handoff",
        "resolve-session", "logout", "profile", "update-profile", "status", "announcements", "run-check",
        "feedback",
    }

    def __init__(self) -> None:
        base_url = os.environ.get("XJ_SUPERVISOR_URL", "http://supervisor-bridge:8765").rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname != "supervisor-bridge" or parsed.port != 8765:
            raise RuntimeError("监督服务地址必须是内部中继")
        token = os.environ.get("XJ_SUPERVISOR_TOKEN", "")
        if len(token) < 32 or len(token) > 256 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise RuntimeError("监督服务内部令牌无效")
        self.client = httpx.AsyncClient(
            base_url=base_url, timeout=15, trust_env=False,
            headers={"authorization": "Bearer " + token},
        )

    async def call(self, action: str, payload: dict) -> dict:
        if action not in self._动作:
            raise HTTPException(500, "网关请求了未授权的控制动作")
        try:
            timeout = 150 if action == "update-profile" else 15
            response = await self.client.post("/control/" + action, json=payload, timeout=timeout)
        except httpx.HTTPError as exc:
            raise HTTPException(503, "控制服务当前不可用") from exc
        if response.status_code >= 400:
            detail = "控制服务拒绝"
            if response.headers.get("content-type", "").startswith("application/json"):
                try:
                    detail = str(response.json().get("detail") or detail)
                except (ValueError, AttributeError):
                    pass
            raise HTTPException(response.status_code, detail)
        try:
            data = response.json()
        except ValueError as exc:
            raise HTTPException(502, "控制服务响应无效") from exc
        if not isinstance(data, dict):
            raise HTTPException(502, "控制服务响应无效")
        return data

    async def close(self) -> None:
        await self.client.aclose()


async def _受限请求体(request: Request, maximum: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > maximum:
                raise HTTPException(413, "请求体过大")
        except ValueError as exc:
            raise HTTPException(400, "Content-Length 无效") from exc
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > maximum:
            raise HTTPException(413, "请求体过大")
    return bytes(body)


@asynccontextmanager
async def public_lifespan(app: FastAPI):
    app.state.control = 控制客户端()
    app.state.proxy = httpx.AsyncClient(timeout=None, follow_redirects=False, trust_env=False)
    yield
    await app.state.proxy.aclose()
    await app.state.control.close()


@asynccontextmanager
async def control_lifespan(app: FastAPI):
    app.state.control = 控制客户端()
    yield
    await app.state.control.close()


public_app = FastAPI(lifespan=public_lifespan, docs_url=None, redoc_url=None, openapi_url=None)
control_app = FastAPI(lifespan=control_lifespan, docs_url=None, redoc_url=None, openapi_url=None)
public_app.mount("/auth-ui", StaticFiles(directory=_界面根), name="auth-ui")
# Kept as a conservative import default; production starts both named apps.
app = public_app


async def _对象请求(request: Request, maximum: int = _最大认证请求) -> dict:
    raw = await _受限请求体(request, maximum)
    try:
        value = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "请求内容无效") from exc
    if not isinstance(value, dict):
        raise HTTPException(400, "请求内容无效")
    return value


async def _账户(request: Request) -> tuple[dict, str]:
    token = request.cookies.get(_会话Cookie, "")
    if not token:
        raise HTTPException(401, "请先登录")
    account = await request.app.state.control.call("resolve-session", {"session_token": token})
    return account, token


def _防伪(request: Request) -> str:
    cookie = request.cookies.get(_防伪Cookie, "")
    header = request.headers.get("x-xj-csrf-token", "")
    if not cookie or len(cookie) > 256 or not hmac.compare_digest(cookie, header):
        raise HTTPException(403, "本次操作验证失败，请刷新页面后重试")
    return cookie


async def _会话仍可用(request: Request, account_id: str, token: str) -> bool:
    try:
        data = await request.app.state.control.call("resolve-session", {"session_token": token})
        return data.get("状态") == "在用" and data.get("account_id") == account_id
    except HTTPException:
        return False


def _公开账户(account: dict) -> dict:
    return {
        key: account.get(key)
        for key in ("account_id", "email", "display_name", "call_name", "状态", "session_expires_at")
        if key in account
    }


def _写登录Cookie(response: Response, result: dict) -> None:
    session_token = str(result.pop("session_token"))
    csrf_token = str(result.pop("csrf_token"))
    max_age = max(1, int(result["session_expires_at"]) - int(time.time()))
    response.set_cookie(
        _会话Cookie, session_token, max_age=max_age, secure=not _本机测试, httponly=True,
        samesite="strict", path="/",
    )
    response.set_cookie(
        _防伪Cookie, csrf_token, max_age=max_age, secure=not _本机测试, httponly=False,
        samesite="strict", path="/",
    )
    response.headers["cache-control"] = "no-store"


def _上游(account: dict) -> tuple[str, str]:
    account_id = str(account.get("account_id") or "")
    if not _账户格式.fullmatch(account_id):
        raise HTTPException(502, "注册表账户标识无效")
    expected = f"company-{account_id.lower()}"
    raw = str(account.get("upstream") or "")
    parsed = urlsplit(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(502, "注册表上游地址无效") from exc
    if (
        parsed.scheme != "http" or parsed.hostname != expected or port != 8000
        or parsed.username is not None or parsed.password is not None
        or parsed.path not in ("", "/") or parsed.query or parsed.fragment
    ):
        raise HTTPException(502, "注册表上游地址不符合固定实例路由")
    return account_id, raw.rstrip("/")


async def _流式(
    response: httpx.Response, request: Request, account_id: str, expires: int, session_token: str,
) -> AsyncIterator[bytes]:
    iterator = response.aiter_raw()
    pending: asyncio.Task[bytes] | None = None
    next_check = 0.0
    try:
        while True:
            now = time.time()
            if now >= expires:
                break
            if now >= next_check:
                if not await _会话仍可用(request, account_id, session_token):
                    break
                next_check = now + 5.0
            if pending is None:
                pending = asyncio.create_task(iterator.__anext__())
            wait_for = min(max(0.1, next_check - now), max(0.1, expires - now))
            done, _ = await asyncio.wait({pending}, timeout=wait_for)
            if not done:
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                break
            pending = None
            yield chunk
    finally:
        if pending is not None:
            pending.cancel()
        await response.aclose()


@control_app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@control_app.post("/run-check")
async def run_check(request: Request) -> Response:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or len(auth) > 1024:
        raise HTTPException(401, "缺少实例控制令牌")
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "请求体不是有效 JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体无效")
    forbidden = {"account_id", "container", "container_name", "directory", "path"}
    if forbidden.intersection(body):
        raise HTTPException(400, "请求不得指定账户、容器或宿主路径")
    body["control_token"] = auth[7:]
    data = await request.app.state.control.call("run-check", body)
    return JSONResponse(data)


@public_app.get("/login", include_in_schema=False)
@public_app.get("/activate", include_in_schema=False)
async def login_page() -> Response:
    return FileResponse(_界面根 / "login.html", headers={"cache-control": "no-store"})


@public_app.get("/auth/runtime-context")
async def runtime_context(request: Request) -> Response:
    result = await request.app.state.control.call("runtime-context", {})
    return JSONResponse(result, headers={"cache-control": "no-store"})


@public_app.get("/auth/invite")
async def inspect_invite(request: Request, token: str = "") -> Response:
    result = await request.app.state.control.call("inspect-invite", {"token": token})
    return JSONResponse(result, headers={"cache-control": "no-store"})


@public_app.post("/auth/activate")
async def activate(request: Request) -> Response:
    body = await _对象请求(request)
    result = await request.app.state.control.call("activate", {
        "token": str(body.get("token") or ""),
        "password": str(body.get("password") or ""),
    })
    response = JSONResponse(_公开账户(result))
    if result.get("session_token"):
        _写登录Cookie(response, result)
    return response


@public_app.post("/auth/login")
async def login(request: Request) -> Response:
    body = await _对象请求(request)
    result = await request.app.state.control.call("unified-login", {
        "account": str(body.get("account") or body.get("email") or ""),
        "password": str(body.get("password") or ""),
    })
    if result.get("destination") == "management":
        token = quote(str(result.get("handoff_token") or ""), safe="")
        return JSONResponse({
            "destination": "management",
            "handoff_url": f"http://localhost:37655/admin/handoff?token={token}",
        }, headers={"cache-control": "no-store"})
    response = JSONResponse({"destination": "company", **_公开账户(result)})
    _写登录Cookie(response, result)
    return response


@public_app.get("/auth/handoff")
async def user_handoff(request: Request, token: str = "") -> Response:
    result = await request.app.state.control.call("consume-user-handoff", {"token": token})
    response = RedirectResponse(
        url="/", status_code=303,
        headers={"cache-control": "no-store", "referrer-policy": "no-referrer"},
    )
    _写登录Cookie(response, result)
    return response


@public_app.get("/auth/me")
async def me(request: Request) -> Response:
    account, _ = await _账户(request)
    return JSONResponse(_公开账户(account), headers={"cache-control": "no-store"})


@public_app.post("/auth/logout")
async def logout(request: Request) -> Response:
    account, session_token = await _账户(request)
    csrf = _防伪(request)
    await request.app.state.control.call("logout", {
        "session_token": session_token,
        "csrf_token": csrf,
        "account_id": account["account_id"],
    })
    response = JSONResponse({"ok": True}, headers={"cache-control": "no-store"})
    response.delete_cookie(
        _会话Cookie, secure=not _本机测试, httponly=True, samesite="strict", path="/",
    )
    response.delete_cookie(
        _防伪Cookie, secure=not _本机测试, httponly=False, samesite="strict", path="/",
    )
    return response


@public_app.get("/auth/profile")
async def profile(request: Request) -> Response:
    account, _ = await _账户(request)
    result = await request.app.state.control.call("profile", {"account_id": account["account_id"]})
    return JSONResponse(result, headers={"cache-control": "no-store"})


@public_app.patch("/auth/profile")
async def update_profile(request: Request) -> Response:
    account, _ = await _账户(request)
    _防伪(request)
    changes = await _对象请求(request)
    result = await request.app.state.control.call(
        "update-profile", {"account_id": account["account_id"], "changes": changes},
    )
    return JSONResponse(result, headers={"cache-control": "no-store"})


@public_app.get("/auth/announcements")
async def announcements(request: Request) -> Response:
    await _账户(request)
    result = await request.app.state.control.call("announcements", {})
    return JSONResponse(result, headers={"cache-control": "no-store"})


@public_app.post("/auth/feedback")
async def feedback(request: Request) -> Response:
    account, _ = await _账户(request)
    _防伪(request)
    body = await _对象请求(request, _最大反馈请求)
    result = await request.app.state.control.call("feedback", {
        "account_id": account["account_id"],
        "kind": body.get("kind"),
        "body": body.get("body"),
        "page": body.get("page"),
        "screenshot": body.get("screenshot"),
    })
    return JSONResponse(result, headers={"cache-control": "no-store"})


@public_app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def root(request: Request) -> Response:
    try:
        await _账户(request)
    except HTTPException as exc:
        if exc.status_code in {401, 403}:
            return FileResponse(_界面根 / "login.html", headers={"cache-control": "no-store"})
        raise
    return await proxy("", request)


@public_app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy(path: str, request: Request) -> Response:
    try:
        account, session_token = await _账户(request)
    except HTTPException as exc:
        if path == "" and request.method in {"GET", "HEAD"} and exc.status_code in {401, 403}:
            return RedirectResponse(
                url="/login", status_code=302, headers={"cache-control": "no-store"},
            )
        raise
    if request.method not in _不改数据的方法:
        _防伪(request)
    account_id, base_url = _上游(account)
    body = await _受限请求体(request, _最大请求)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _去除请求头}
    headers.update({"x-xj-account-id": account_id, "x-xj-user-id": account_id})
    upstream = base_url + "/" + path
    if request.url.query:
        upstream += "?" + request.url.query
    outgoing = request.app.state.proxy.build_request(request.method, upstream, headers=headers, content=body)
    try:
        response = await request.app.state.proxy.send(outgoing, stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "用户实例当前不可达") from exc
    response_headers = {k: v for k, v in response.headers.items() if k.lower() not in _去除响应头}
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        return StreamingResponse(
            _流式(response, request, account_id, int(account["session_expires_at"]), session_token),
            status_code=response.status_code,
            headers=response_headers,
            media_type="text/event-stream",
        )
    return StreamingResponse(
        response.aiter_raw(),
        status_code=response.status_code,
        headers=response_headers,
        background=BackgroundTask(response.aclose),
    )
