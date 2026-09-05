"""Owner-only account administration service bound to macOS loopback."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ..版本身份 import 版本身份错误, 读取运行版本身份
from .发布管理 import 发布管理, 宿主环境审批要求, 等待宿主切换就绪
from .生命周期 import 生命周期
from .注册表 import 注册表错误, 账户注册表
from .账户认证 import 认证错误, 账户认证
from .宿主服务重启 import (
    正在重启,
    标记新进程已恢复,
    读取状态,
    请求重启,
    宿主边界状态,
    准备宿主边界,
    暂存候选宿主运行面,
    恢复候选宿主运行面,
    确认候选宿主运行面,
    核对候选宿主入口,
    建立首次宿主迁移,
    建立首轮宿主引导计划,
    启动宿主切换,
    读取宿主切换,
)
from ..部署.发布仓库 import 发布源状态


_最大请求 = 64 * 1024
_会话Cookie = "xj-owner-session"
_防伪Cookie = "xj-owner-csrf"
_界面根 = Path(__file__).resolve().parents[1] / "界面" / "static"


def _读取当前开发提交() -> str:
    """读取唯一开发区当前提交；失败原因仍由发布源检查对外说明。"""
    try:
        state = 发布源状态()
    except Exception:
        return ""
    source = state.get("source") if isinstance(state, dict) else None
    return str(source.get("head") or "") if isinstance(source, dict) else ""


# 进程启动时固定一次。HEAD 后续变化，说明这个进程仍在执行上一份代码。
_加载时开发提交 = _读取当前开发提交()


async def _对象请求(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > _最大请求:
        raise HTTPException(413, "请求内容过大")
    try:
        value = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "请求内容无效") from exc
    if not isinstance(value, dict):
        raise HTTPException(400, "请求内容无效")
    return value


def _写登录Cookie(response: Response, result: dict[str, Any]) -> None:
    max_age = max(1, int(result["session_expires_at"]) - int(time.time()))
    response.set_cookie(
        _会话Cookie, str(result["session_token"]), max_age=max_age,
        httponly=True, secure=False, samesite="strict", path="/",
    )
    response.set_cookie(
        _防伪Cookie, str(result["csrf_token"]), max_age=max_age,
        httponly=False, secure=False, samesite="strict", path="/",
    )
    response.headers["cache-control"] = "no-store"


def _管理员(request: Request, *, change: bool = False) -> str:
    token = request.cookies.get(_会话Cookie, "")
    csrf = request.headers.get("x-xj-csrf-token", "") if change else None
    try:
        request.app.state.auth.校验管理员(token, csrf)
    except 认证错误 as exc:
        raise HTTPException(401 if not token else 403, str(exc)) from exc
    return token


def _是开发管理后台(request: Request) -> bool:
    """发布能力只存在于本人的开发管理进程。"""
    state = getattr(request.app, "state", None)
    configured = getattr(state, "development_admin", None)
    if configured is not None:
        return bool(configured)
    return os.environ.get("XJ_DEVELOPMENT_ADMIN", "0") == "1"


def _要求开发管理(request: Request) -> str:
    token = _管理员(request, change=True)
    if not _是开发管理后台(request):
        raise HTTPException(
            409,
            "正式运行维护页不能生成候选或发布；请从本人的开发管理页进入",
        )
    return token


def _开发管理代码状态(request: Request) -> dict[str, Any]:
    if not _是开发管理后台(request):
        return {"loaded_head": "", "current_head": "", "reload_required": False}
    loaded = str(
        getattr(request.app.state, "loaded_development_head", "")
        or _加载时开发提交
    )
    current = _读取当前开发提交()
    return {
        "loaded_head": loaded,
        "current_head": current,
        "reload_required": bool(loaded and current and loaded != current),
    }


def _要求发布后台已加载当前代码(request: Request) -> None:
    state = _开发管理代码状态(request)
    if state["reload_required"]:
        raise HTTPException(
            409,
            {
                "code": "development-admin-reload-required",
                "message": "后台还在运行上一份代码，请先点击“重启后台”",
                **state,
            },
        )


async def _稍后退出开发管理后台() -> None:
    """先把响应交给页面，再退出；LaunchAgent 会自动拉起最新开发代码。"""
    await asyncio.sleep(0.8)
    os.kill(os.getpid(), signal.SIGTERM)


def _邀请结果(result: dict[str, Any]) -> dict[str, Any]:
    token = str(result.pop("token"))
    path = "/activate#token=" + quote(token, safe="")
    public = os.environ.get("XJ_PUBLIC_URL", "").strip().rstrip("/")
    url = ""
    if public:
        parsed = urlsplit(public)
        local_test = public == "http://localhost:37656"
        if local_test or (
            parsed.scheme == "https" and parsed.hostname and not parsed.query and not parsed.fragment
        ):
            url = public + path
    return {**result, "invite_path": path, "invite_url": url, "invite_token": token}


def _公开服务地址() -> str:
    public = os.environ.get("XJ_PUBLIC_URL", "").strip().rstrip("/")
    parsed = urlsplit(public)
    local_test = public == "http://localhost:37656"
    if local_test or (
        parsed.scheme == "https" and parsed.hostname and not parsed.query and not parsed.fragment
    ):
        return public
    raise 认证错误("用户公司入口尚未正确设置")


def _后台重启预检() -> None:
    from ..部署.监督服务命令 import LEGACY_PLIST, PLIST, _环境, _旧单进程环境

    if PLIST.is_file() and not PLIST.is_symlink():
        _环境()
        return
    # 首次迁移前仍由旧单进程服务承载后台；重启按钮不能把这条已安装路径误报成“未安装”。
    if LEGACY_PLIST.is_file() and not LEGACY_PLIST.is_symlink():
        _旧单进程环境()
        return
    if not PLIST.is_file():
        raise RuntimeError("多用户后台尚未安装为本机服务")
    raise RuntimeError("多用户后台启动描述无效")


def _要求没有发布维护(app: FastAPI) -> None:
    app.state.registry.要求没有发布维护()


@asynccontextmanager
async def _账号变更窗口(app: FastAPI):
    """让账号生命周期操作与后台重启、发布准备互斥。"""
    async with app.state.operation_lock:
        if await asyncio.to_thread(正在重启):
            raise RuntimeError("后台正在重启，恢复后才能修改账号")
        task = getattr(app.state, "release_task", None)
        if task is not None and not task.done():
            raise RuntimeError("发布后台正在恢复或部署，请等待当前阶段结束")
        await asyncio.to_thread(_要求没有发布维护, app)
        unresolved = await asyncio.to_thread(app.state.registry.未解决发布恢复)
        if unresolved:
            raise RuntimeError("上一次部署还没有恢复核清，暂时不能修改账号；请先重启后台完成恢复")
        yield


def _追加异常终态(details: dict[str, Any], status: str, stage: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    timeline = details.get("timeline") if isinstance(details.get("timeline"), list) else []
    timeline.append({"at": now, "updated_at": now, "status": status, "stage": stage})
    details["timeline"] = timeline[-100:]


async def _后台启动恢复(app: FastAPI) -> None:
    try:
        app.state.host_switch_recovery = await asyncio.to_thread(
            app.state.releases.收口宿主切换,
        )
        app.state.release_recovery = await asyncio.to_thread(
            app.state.releases.恢复启动状态,
        )
        # The formal admin starts before the shared platform and accounts are
        # switched.  Its first close-out check is therefore expected to wait.
        # Keep reconciling while the helper owns the same host-ready switch;
        # otherwise nobody observes the facts that become true seconds later.
        if os.environ.get("XJ_HOST_ROLE") == "admin":
            deadline = asyncio.get_running_loop().time() + 185
            while asyncio.get_running_loop().time() < deadline:
                state = await asyncio.to_thread(读取宿主切换)
                if not isinstance(state, dict):
                    break
                target = state.get("target") if isinstance(state.get("target"), dict) else {}
                if (
                    os.environ.get("XJ_ACTIVE_VERSION")
                    != str(target.get("version") or "")
                    or os.environ.get("XJ_RELEASE_SOURCE_SHA256")
                    != str(target.get("source_sha256") or "")
                ):
                    break
                if state.get("status") in {"queued", "switching"}:
                    await asyncio.sleep(0.25)
                    continue
                if state.get("status") != "host-ready":
                    break
                app.state.host_switch_recovery = await asyncio.to_thread(
                    app.state.releases.收口宿主切换,
                )
                if (
                    isinstance(app.state.host_switch_recovery, dict)
                    and app.state.host_switch_recovery.get("status") in {"completed", "完成"}
                ):
                    break
                await asyncio.sleep(0.5)
        app.state.restart_recovery = await asyncio.to_thread(标记新进程已恢复)
    except Exception as exc:
        app.state.release_recovery = {
            "status": "failed",
            "error": str(exc)[:500] or type(exc).__name__,
        }
    finally:
        current = getattr(app.state, "release_task", None)
        if current is asyncio.current_task():
            app.state.release_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    registry_path = Path(os.environ.get("XJ_REGISTRY_PATH", "/var/lib/xj/registry.sqlite3"))
    app.state.registry = 账户注册表(registry_path)
    app.state.auth = 账户认证(registry_path)
    app.state.lifecycle = 生命周期(app.state.registry)
    app.state.operation_lock = asyncio.Lock()
    app.state.development_admin = os.environ.get("XJ_DEVELOPMENT_ADMIN", "0") == "1"
    app.state.loaded_development_head = _加载时开发提交
    app.state.development_reload_task = None
    from .宿主服务重启 import (
        启动宿主切换,
        建立宿主切换计划,
        恢复当前正式后台,
        核对宿主发布能力,
        核对运行版本,
        验收宿主影子,
    )
    app.state.releases = 发布管理(
        app.state.registry,
        app.state.lifecycle,
        prepare_host_boundary=准备宿主边界,
        allow_automatic_host_prepare=False,
        stage_host_runtime=暂存候选宿主运行面,
        restore_host_runtime=恢复候选宿主运行面,
        recover_active_host_runtime=(
            恢复当前正式后台 if app.state.development_admin else None
        ),
        finalize_host_runtime=确认候选宿主运行面,
        read_host_boundary=宿主边界状态,
        verify_host_capability=核对宿主发布能力,
        verify_host_shadow=验收宿主影子,
        verify_bootstrap_candidate=核对候选宿主入口,
        plan_host_switch=建立宿主切换计划,
        plan_host_bootstrap_switch=建立首轮宿主引导计划,
        start_host_switch=启动宿主切换,
        wait_host_switch=等待宿主切换就绪,
        verify_host_runtime=核对运行版本,
    )
    app.state.release_task = asyncio.create_task(_后台启动恢复(app))
    yield
    task = getattr(app.state, "release_task", None)
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/admin-ui", StaticFiles(directory=_界面根), name="admin-ui")


@app.middleware("http")
async def 本机响应头(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/admin-ui/"):
        response.headers["cache-control"] = "no-store"
    response.headers["content-security-policy"] = (
        "frame-ancestors 'self' http://localhost:8787 http://127.0.0.1:8787"
    )
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["referrer-policy"] = "no-referrer"
    return response


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/", include_in_schema=False)
async def admin_page() -> Response:
    return FileResponse(_界面根 / "admin.html", headers={"cache-control": "no-store"})


@app.get("/admin/setup-status")
async def setup_status(request: Request) -> dict[str, bool]:
    configured = await asyncio.to_thread(request.app.state.auth.管理员是否已设置)
    authenticated = False
    if configured:
        try:
            await asyncio.to_thread(
                request.app.state.auth.校验管理员,
                request.cookies.get(_会话Cookie, ""),
            )
            authenticated = True
        except 认证错误:
            pass
    return {"configured": configured, "authenticated": authenticated}


@app.get("/admin/runtime-identity")
async def runtime_identity(request: Request) -> dict[str, Any]:
    """登录前只公开版本身份，不公开管理状态或版本路径。"""
    try:
        return await asyncio.to_thread(
            读取运行版本身份,
            development=_是开发管理后台(request),
        )
    except 版本身份错误 as exc:
        raise HTTPException(503, "当前正式版本身份无法确认") from exc


@app.post("/admin/setup")
async def setup(request: Request) -> Response:
    body = await _对象请求(request)
    try:
        login_name = str(body.get("account") or "")
        password = str(body.get("password") or "")
        await asyncio.to_thread(request.app.state.auth.设置管理员账户, login_name, password)
        result = await asyncio.to_thread(request.app.state.auth.管理员登录, login_name, password)
    except 认证错误 as exc:
        raise HTTPException(400, str(exc)) from exc
    response = JSONResponse({"ok": True, "destination": "management"})
    _写登录Cookie(response, result)
    return response


@app.post("/admin/login")
async def login(request: Request) -> Response:
    body = await _对象请求(request)
    login_name = str(body.get("account") or "")
    password = str(body.get("password") or "")
    try:
        is_owner = await asyncio.to_thread(request.app.state.auth.管理员账户匹配, login_name)
        if is_owner:
            result = await asyncio.to_thread(request.app.state.auth.管理员登录, login_name, password)
        else:
            public = _公开服务地址()
            handoff = await asyncio.to_thread(request.app.state.auth.创建用户登录交接, login_name, password)
    except 认证错误 as exc:
        raise HTTPException(403, str(exc)) from exc
    if not is_owner:
        response = JSONResponse({
            "ok": True,
            "destination": "company",
            "handoff_url": public + "/auth/handoff?token=" + quote(str(handoff["handoff_token"]), safe=""),
        })
        response.headers["cache-control"] = "no-store"
        return response
    response = JSONResponse({"ok": True, "destination": "management"})
    _写登录Cookie(response, result)
    return response


@app.get("/admin/handoff")
async def owner_handoff(request: Request, token: str = "") -> Response:
    try:
        result = await asyncio.to_thread(request.app.state.auth.使用管理员登录交接, token)
    except 认证错误 as exc:
        raise HTTPException(403, str(exc)) from exc
    response = RedirectResponse(url="/", status_code=303, headers={"cache-control": "no-store"})
    _写登录Cookie(response, result)
    return response


@app.post("/admin/logout")
async def logout(request: Request) -> Response:
    token = _管理员(request, change=True)
    await asyncio.to_thread(
        request.app.state.auth.管理员退出,
        token,
        request.headers.get("x-xj-csrf-token", ""),
    )
    response = JSONResponse({"ok": True}, headers={"cache-control": "no-store"})
    response.delete_cookie(_会话Cookie, path="/", httponly=True, secure=False, samesite="strict")
    response.delete_cookie(_防伪Cookie, path="/", httponly=False, secure=False, samesite="strict")
    return response


@app.get("/admin/me")
async def me(request: Request) -> dict[str, bool]:
    _管理员(request)
    return {"ok": True}


@app.get("/admin/runtime-context")
async def runtime_context(request: Request) -> dict[str, Any]:
    """告诉管理页当前是开发发布端还是正式维护端。"""
    _管理员(request)
    development = _是开发管理后台(request)
    try:
        version_identity = await asyncio.to_thread(
            读取运行版本身份,
            development=development,
        )
    except 版本身份错误 as exc:
        raise HTTPException(503, "当前正式版本身份无法确认") from exc
    return {
        "mode": "development" if development else "formal",
        "label": "开发版发布" if development else "正式运行维护",
        "can_prepare_candidate": development,
        "can_publish": development,
        "can_rollback": True,
        "can_restart": True,
        "version_identity": version_identity,
        "development_admin": _开发管理代码状态(request),
    }


@app.get("/admin/accounts")
async def accounts(request: Request) -> dict[str, Any]:
    _管理员(request)
    rows = await asyncio.to_thread(request.app.state.registry.列表)
    for row in rows:
        row["登录"] = await asyncio.to_thread(request.app.state.auth.登录状态, row["account_id"])
    return {"accounts": rows}


@app.get("/admin/announcements")
async def announcements(request: Request) -> dict[str, Any]:
    _管理员(request)
    return {"announcements": await asyncio.to_thread(request.app.state.registry.公告列表)}


@app.get("/admin/feedback")
async def feedback_list(request: Request) -> dict[str, Any]:
    _管理员(request)
    return {"feedback": await asyncio.to_thread(request.app.state.registry.反馈列表)}


@app.get("/admin/feedback/{feedback_id}/screenshot")
async def feedback_screenshot(feedback_id: int, request: Request) -> Response:
    _管理员(request)
    try:
        mime, content = await asyncio.to_thread(request.app.state.registry.反馈截图, feedback_id)
    except 注册表错误 as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(
        content=content,
        media_type=mime,
        headers={"cache-control": "no-store", "content-disposition": "inline"},
    )


@app.patch("/admin/feedback/{feedback_id}")
async def update_feedback(feedback_id: int, request: Request) -> dict[str, Any]:
    _管理员(request, change=True)
    body = await _对象请求(request)
    try:
        item = await asyncio.to_thread(
            request.app.state.registry.设置反馈状态,
            feedback_id,
            str(body.get("status") or ""),
        )
        return {"feedback": item}
    except 注册表错误 as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/admin/announcements")
async def create_announcement(request: Request) -> dict[str, Any]:
    _管理员(request, change=True)
    body = await _对象请求(request)
    try:
        announcement = await asyncio.to_thread(
            request.app.state.registry.发布公告,
            str(body.get("title") or ""),
            str(body.get("body") or ""),
            body_rich=str(body.get("body_rich") or ""),
        )
        return {"announcement": announcement}
    except 注册表错误 as exc:
        raise HTTPException(400, str(exc)) from exc


@app.patch("/admin/announcements/{announcement_id}")
async def update_announcement(announcement_id: int, request: Request) -> dict[str, Any]:
    _管理员(request, change=True)
    body = await _对象请求(request)
    try:
        announcement = await asyncio.to_thread(
            request.app.state.registry.编辑公告,
            announcement_id,
            str(body.get("title") or ""),
            str(body.get("body") or ""),
            body_rich=str(body.get("body_rich") or ""),
        )
        return {"announcement": announcement}
    except 注册表错误 as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/admin/announcements/{announcement_id}/withdraw")
async def withdraw_announcement(announcement_id: int, request: Request) -> dict[str, Any]:
    _管理员(request, change=True)
    try:
        announcement = await asyncio.to_thread(
            request.app.state.registry.设置公告状态, announcement_id, "已撤下"
        )
        return {"announcement": announcement}
    except 注册表错误 as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/admin/announcements/{announcement_id}/publish")
async def publish_announcement(announcement_id: int, request: Request) -> dict[str, Any]:
    _管理员(request, change=True)
    try:
        announcement = await asyncio.to_thread(
            request.app.state.registry.设置公告状态, announcement_id, "已发布"
        )
        return {"announcement": announcement}
    except 注册表错误 as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/admin/releases")
async def release_status(request: Request) -> dict[str, Any]:
    _管理员(request)
    result = await asyncio.to_thread(request.app.state.releases.状态)
    result["development_admin"] = _开发管理代码状态(request)
    return result


@app.get("/admin/releases/host-boundary")
async def release_host_boundary_status(request: Request) -> dict[str, Any]:
    _管理员(request)
    return await asyncio.to_thread(宿主边界状态)


@app.post("/admin/releases/host-runtime/approve")
async def approve_release_host_runtime(request: Request) -> dict[str, Any]:
    """批准当前候选建立隔离环境并完成影子验收；不会切换正式宿主。"""
    _要求开发管理(request)
    _要求发布后台已加载当前代码(request)
    body = await _对象请求(request)
    candidate_id = str(body.get("candidate_id") or "").strip()
    if body.get("confirm") != "为当前候选建立隔离环境":
        raise HTTPException(400, "请输入确认词“为当前候选建立隔离环境”")
    if not candidate_id:
        raise HTTPException(400, "缺少候选编号")
    try:
        async with request.app.state.operation_lock:
            task = request.app.state.release_task
            if task is not None and not task.done():
                raise RuntimeError("发布后台正在处理，请等待当前操作结束")
            runtime = await asyncio.to_thread(
                request.app.state.releases.批准候选宿主环境, candidate_id,
            )
            candidate = await asyncio.to_thread(
                request.app.state.registry.候选记录, candidate_id=candidate_id,
            )
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"host_runtime": runtime, "candidate": candidate}


@app.post("/admin/releases/host-boundary/prepare")
async def prepare_release_host_boundary(request: Request) -> dict[str, Any]:
    _要求开发管理(request)
    body = await _对象请求(request)
    if body.get("confirm") != "准备正式宿主运行面":
        raise HTTPException(400, "请输入确认词“准备正式宿主运行面”；这会写入正式宿主文件，但不会启动服务")
    try:
        async with request.app.state.operation_lock:
            if await asyncio.to_thread(正在重启):
                raise RuntimeError("后台正在重启，请等待恢复后再准备")
            task = request.app.state.release_task
            if task is not None and not task.done():
                raise RuntimeError("发布后台正在恢复或部署，暂不能准备")
            result = await asyncio.to_thread(准备宿主边界)
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"host_boundary": result}


@app.get("/admin/releases/host-boundary/migration")
async def release_host_boundary_migration_status(request: Request) -> dict[str, Any]:
    _管理员(request)
    return {
        "host_boundary": await asyncio.to_thread(宿主边界状态),
        "migration": await asyncio.to_thread(读取宿主切换),
    }


@app.post("/admin/releases/host-boundary/migrate")
async def migrate_release_host_boundary(request: Request) -> dict[str, Any]:
    _要求开发管理(request)
    body = await _对象请求(request)
    if body.get("confirm") != "迁移宿主运行面":
        raise HTTPException(400, "请输入确认词“迁移宿主运行面”，这不是普通重启")
    try:
        async with request.app.state.operation_lock:
            task = request.app.state.release_task
            if task is not None and not task.done():
                raise RuntimeError("发布后台正在恢复或部署，不能同时迁移")
            if await asyncio.to_thread(正在重启):
                raise RuntimeError("后台正在重启，请先等待完成")
            await asyncio.to_thread(_要求没有发布维护, request.app)
            from ..部署.宿主启动器 import 解析活动发布

            await asyncio.to_thread(准备宿主边界)
            # 准备动作会把旧正式锁复制为独立活动指针；必须在此之后读取它。
            active = await asyncio.to_thread(解析活动发布)
            plan = await asyncio.to_thread(
                建立首次宿主迁移,
                active["version"], active["source_sha256"],
            )
            state = await asyncio.to_thread(启动宿主切换, plan["switch_id"])
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "migration": state,
        "message": "迁移已交给外部宿主助手；管理后台会短暂断开，请稍后查看迁移状态",
    }


@app.get("/admin/release-candidates/{candidate_id}/log")
async def release_candidate_log(candidate_id: str, request: Request) -> Response:
    _管理员(request)
    try:
        result = await asyncio.to_thread(request.app.state.releases.候选日志, candidate_id)
    except (注册表错误, RuntimeError, ValueError) as exc:
        raise HTTPException(404, str(exc)) from exc
    return JSONResponse(result, headers={"cache-control": "no-store"})


@app.get("/admin/system-restart")
async def system_restart_status(request: Request) -> dict[str, Any]:
    _管理员(request)
    return {"restart": await asyncio.to_thread(读取状态)}


@app.post("/admin/system-restart")
async def system_restart(request: Request) -> dict[str, Any]:
    _管理员(request, change=True)
    try:
        async with request.app.state.operation_lock:
            await asyncio.to_thread(_要求没有发布维护, request.app)
            current = request.app.state.release_task
            if current is not None and not current.done():
                raise RuntimeError("发布系统正在处理，不能重启后台")
            await asyncio.to_thread(_后台重启预检)
            status = await asyncio.to_thread(请求重启)
            # 开发管理页上的一个“重启后台”必须同时覆盖正式用户后台和
            # 发布后台。正式助手独立继续执行；当前 HTTP 响应发出后，
            # 开发管理进程退出并由 LaunchAgent 从最新提交重新拉起。
            if _是开发管理后台(request):
                task = getattr(request.app.state, "development_reload_task", None)
                if task is None or task.done():
                    request.app.state.development_reload_task = asyncio.create_task(
                        _稍后退出开发管理后台()
                    )
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"restart": status}


async def _执行候选(app: FastAPI, candidate_id: str) -> None:
    try:
        await asyncio.to_thread(app.state.releases.执行候选, candidate_id)
    except Exception as exc:
        try:
            record = await asyncio.to_thread(app.state.registry.候选记录, candidate_id=candidate_id)
            if record and record["status"] in {"排队中", "验收中", "构建中"}:
                details = dict(record.get("details") or {})
                details["failure"] = {
                    "phase": "release-system", "step": "后台候选任务",
                    "summary": str(exc)[:500] or type(exc).__name__, "exit_code": None,
                    "failed_checks": [], "next_action": "重启后台并查看候选日志后再处理。",
                    "account_effect": "未触碰任何账号；当前正式版本继续运行。",
                    "retry_class": "environment",
                }
                _追加异常终态(details, "失败", "后台候选任务异常停止，未触碰任何账号")
                await asyncio.to_thread(
                    app.state.registry.更新候选记录, candidate_id,
                    status="失败", stage="后台候选任务异常停止，未触碰任何账号", details=details,
                )
        except Exception:
            pass
    finally:
        app.state.release_task = None


@app.post("/admin/release-candidates")
async def start_release_candidate(request: Request) -> dict[str, Any]:
    _要求开发管理(request)
    _要求发布后台已加载当前代码(request)
    body = await _对象请求(request)
    try:
        async with request.app.state.operation_lock:
            if await asyncio.to_thread(正在重启):
                raise RuntimeError("后台正在重启，恢复后才能生成候选")
            current = request.app.state.release_task
            if current is not None and not current.done():
                raise RuntimeError("已有候选或部署正在进行")
            record = await asyncio.to_thread(
                request.app.state.releases.准备候选,
                str(body.get("notes") or ""),
                str(body.get("source_head") or ""),
            )
            request.app.state.release_task = asyncio.create_task(
                _执行候选(request.app, record["candidate_id"])
            )
    except (注册表错误, RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"candidate": record}


async def _执行部署(app: FastAPI, deployment_id: str) -> None:
    try:
        result = await asyncio.to_thread(app.state.releases.执行部署, deployment_id)
        if isinstance(result, dict) and result.get("status") == "更新账号中":
            deadline = asyncio.get_running_loop().time() + 210
            while asyncio.get_running_loop().time() < deadline:
                record = await asyncio.to_thread(
                    app.state.registry.部署记录, deployment_id=deployment_id,
                )
                state = await asyncio.to_thread(读取宿主切换)
                if isinstance(state, dict) and state.get("status") == "failed":
                    await asyncio.to_thread(app.state.releases.恢复启动状态)
                    break
                if not isinstance(record, dict) or record.get("status") in {"完成", "失败", "已中断"}:
                    break
                if isinstance(state, dict) and state.get("status") == "completed":
                    await asyncio.to_thread(app.state.releases.收口宿主切换)
                await asyncio.sleep(0.5)
    except Exception as exc:
        try:
            record = await asyncio.to_thread(app.state.registry.部署记录, deployment_id=deployment_id)
            if record and record["status"] in {"排队中", "更新账号中"}:
                details = dict(record.get("details") or {})
                details["error"] = str(exc)[:500] or type(exc).__name__
                details["rollback_failures"] = {
                    "release-system": "后台任务异常中断，尚未完成运行状态核对",
                }
                details["retained_unactivated_release"] = {
                    "version": record["version"],
                    "reason": "状态尚未核对，保留预发布副本和镜像供恢复使用",
                }
                details["failure"] = {
                    "phase": "release-system", "step": "后台部署任务",
                    "summary": details["error"], "exit_code": None,
                    "failed_checks": [],
                    "next_action": "先核对账号和共享平台的实际运行版本，再处理这次中断。",
                    "account_effect": "部署状态需要核对；系统已停止后续发布操作。",
                    "retry_class": "manual",
                }
                _追加异常终态(details, "失败", "后台部署任务异常停止，需要核对回退状态")
                await asyncio.to_thread(
                    app.state.registry.更新部署记录, deployment_id,
                    status="失败", stage="后台部署任务异常停止，需要核对回退状态",
                    target_count=record["target_count"], success_count=record["success_count"],
                    failed_count=record["failed_count"], details=details,
                )
        except Exception:
            pass
    finally:
        app.state.release_task = None


@app.post("/admin/releases")
async def start_release(request: Request) -> dict[str, Any]:
    _要求开发管理(request)
    _要求发布后台已加载当前代码(request)
    body = await _对象请求(request)
    try:
        async with request.app.state.operation_lock:
            if await asyncio.to_thread(正在重启):
                raise RuntimeError("后台正在重启，恢复后才能发布")
            current = request.app.state.release_task
            if current is not None and not current.done():
                raise RuntimeError("已有候选或部署正在进行")
            record = await asyncio.to_thread(
                request.app.state.releases.准备发布,
                str(body.get("candidate_id") or ""),
                str(body.get("version_name") or ""),
            )
            request.app.state.release_task = asyncio.create_task(
                _执行部署(request.app, record["deployment_id"])
            )
    except 宿主环境审批要求 as exc:
        raise HTTPException(409, exc.details) from exc
    except (注册表错误, RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"deployment": record}


@app.post("/admin/releases/rollback")
async def start_release_rollback(request: Request) -> dict[str, Any]:
    _管理员(request, change=True)
    body = await _对象请求(request)
    try:
        async with request.app.state.operation_lock:
            if await asyncio.to_thread(正在重启):
                raise RuntimeError("后台正在重启，恢复后才能回退版本")
            current = request.app.state.release_task
            if current is not None and not current.done():
                raise RuntimeError("已有候选或部署正在进行")
            record = await asyncio.to_thread(
                request.app.state.releases.准备回退,
                str(body.get("version") or ""),
            )
            request.app.state.release_task = asyncio.create_task(
                _执行部署(request.app, record["deployment_id"])
            )
    except (注册表错误, RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"deployment": record}


@app.post("/admin/accounts")
async def create_account(request: Request) -> dict[str, Any]:
    _管理员(request, change=True)
    body = await _对象请求(request)
    try:
        async with _账号变更窗口(request.app):
            account = await asyncio.to_thread(
                request.app.state.lifecycle.开通,
                email=str(body.get("email") or ""),
                display_name=str(body.get("display_name") or ""),
                call_name=str(body.get("call_name") or ""),
                aliases=[str(item) for item in body.get("aliases", [])] if isinstance(body.get("aliases", []), list) else [],
                company_purpose=str(body.get("company_purpose") or ""),
                chat_daily_usd=float(body.get("chat_daily_usd", 1)),
                chat_monthly_usd=float(body.get("chat_monthly_usd", 20)),
                search_daily_usd=float(body.get("search_daily_usd", 0.2)),
                search_monthly_usd=float(body.get("search_monthly_usd", 5)),
                chat_rpm=int(body.get("chat_rpm", 60)),
                search_rpm=int(body.get("search_rpm", 60)),
            )
            invite = await asyncio.to_thread(request.app.state.auth.发邀请, account["account_id"])
            return {"account": account, "invite": _邀请结果(invite)}
    except (认证错误, 注册表错误, RuntimeError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/admin/accounts/{account_id}/invite")
async def invite(account_id: str, request: Request) -> dict[str, Any]:
    _管理员(request, change=True)
    try:
        result = await asyncio.to_thread(request.app.state.auth.发邀请, account_id, "activate")
        return _邀请结果(result)
    except 认证错误 as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/admin/accounts/{account_id}/reset-password")
async def reset_password(account_id: str, request: Request) -> dict[str, Any]:
    _管理员(request, change=True)
    try:
        result = await asyncio.to_thread(request.app.state.auth.发邀请, account_id, "reset")
        request.app.state.registry.记审计(
            actor="owner-ui", action="reset-password", account_id=account_id, result="pending",
        )
        return _邀请结果(result)
    except (认证错误, 注册表错误) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/admin/accounts/{account_id}/stop")
async def stop(account_id: str, request: Request) -> dict[str, Any]:
    _管理员(request, change=True)
    try:
        async with _账号变更窗口(request.app):
            await asyncio.to_thread(request.app.state.auth.撤销用户会话, account_id)
            return await asyncio.to_thread(request.app.state.lifecycle.停用, account_id)
    except (注册表错误, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/admin/accounts/{account_id}/restart")
async def restart(account_id: str, request: Request) -> dict[str, Any]:
    _管理员(request, change=True)
    try:
        async with _账号变更窗口(request.app):
            return await asyncio.to_thread(request.app.state.lifecycle.重启, account_id)
    except (注册表错误, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/admin/accounts/{account_id}")
async def purge(account_id: str, request: Request) -> dict[str, Any]:
    _管理员(request, change=True)
    body = await _对象请求(request)
    try:
        async with _账号变更窗口(request.app):
            await asyncio.to_thread(request.app.state.auth.撤销用户会话, account_id)
            return await asyncio.to_thread(
                request.app.state.lifecycle.永久删除, account_id, str(body.get("confirm") or "")
            )
    except (注册表错误, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/admin/audit")
async def audit(request: Request, account_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    _管理员(request)
    return {"records": await asyncio.to_thread(request.app.state.registry.审计记录, account_id, limit)}
