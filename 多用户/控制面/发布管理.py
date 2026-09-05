"""Manufacture sealed candidates, then deploy or roll back exact artifacts."""

from __future__ import annotations

import secrets
import os
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from ..部署.发布仓库 import (
    DEVELOPMENT_VERSION_NAME,
    PRODUCT_VERSION_ID,
    PRODUCT_VERSION_NAME,
    创建候选,
    切换当前版本,
    激活暂存版本,
    发布源状态,
    封存正式版本,
    已发布版本列表,
    当前已发布版本,
    撤销未激活封存,
    废弃候选大文件,
    校验版本显示名,
    版本显示名,
    生成候选编号,
    读取候选,
    读取正式版本,
)
from ..部署.平台命令 import (
    当前平台镜像, 提升版本镜像, 撤销未激活版本镜像, 确保平台版本, 确保正式镜像, 恢复平台版本,
    恢复事务平台版本, 切换平台版本, 验收并构建候选,
)
from .生命周期 import 生命周期
from .注册表 import 账户注册表
from .宿主服务重启 import (
    准备宿主边界,
    准备候选宿主环境,
    暂存候选宿主运行面,
    恢复候选宿主运行面,
    确认候选宿主运行面,
    启动宿主切换,
    建立首轮宿主引导计划,
    建立宿主切换计划,
    核对宿主发布能力,
    核对候选宿主入口,
    核对运行版本,
    验收宿主影子,
    读取宿主切换,
    宿主助手存活,
    标记宿主切换已收口,
    标记宿主切换等待收口,
    标记宿主切换失败,
)


def _错误文字(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text[:500]


class 宿主环境审批要求(RuntimeError):
    """发布前发现运行环境不匹配，必须由管理员明确批准候选隔离环境。"""

    code = "host-runtime-approval-required"

    def __init__(self, action: str, reason: str, *, candidate_id: str = "") -> None:
        self.details = {
            "code": self.code,
            "message": f"{action}前需要您批准建立候选隔离运行环境",
            "reason": reason or "当前宿主依赖环境尚未通过核对",
            "candidate_id": candidate_id,
            "requires_approval": True,
        }
        super().__init__(self.details["message"] + "：" + self.details["reason"])


def 等待宿主切换就绪(switch_id: str, *, timeout: float = 180) -> dict[str, Any]:
    """等待正式控制/精排/管理三个宿主服务全部启动。

    宿主助手在独立进程中运行；发布线程必须等本机精排端口真正可用后，
    才能启动依赖它的共享搜索平台，不能只看到“已安排”就继续。
    """
    if not switch_id:
        raise RuntimeError("宿主切换编号为空")
    deadline = time.monotonic() + timeout
    last_stage = "宿主助手尚未报告状态"
    while time.monotonic() < deadline:
        state = 读取宿主切换()
        if not isinstance(state, dict) or state.get("switch_id") != switch_id:
            raise RuntimeError("宿主切换记录已变化，不能继续发布")
        status = str(state.get("status") or "")
        if status in {"host-ready", "completed"}:
            return state
        if status == "failed":
            raise RuntimeError(str(state.get("error") or "正式宿主切换失败"))
        last_stage = str(state.get("stage") or status or last_stage)
        time.sleep(0.25)
    raise RuntimeError(f"正式宿主在 {int(timeout)} 秒内没有就绪：{last_stage}")


def _现在编号(prefix: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{timestamp}-{secrets.token_hex(4)}"


def _正式版本标识(candidate_id: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%sz")
    suffix = str(candidate_id).rsplit("-", 1)[-1]
    return f"twilight-release-{timestamp}-{suffix}"


def _追加阶段(details: dict[str, Any], status: str, stage: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    timeline = details.get("timeline") if isinstance(details.get("timeline"), list) else []
    if timeline and isinstance(timeline[-1], dict) and (
        timeline[-1].get("status"), timeline[-1].get("stage")
    ) == (status, stage):
        timeline[-1]["updated_at"] = now
    else:
        timeline.append({"at": now, "updated_at": now, "status": status, "stage": stage})
    details["timeline"] = timeline[-100:]


def _候选源提交(details: Mapping[str, Any] | None) -> str:
    details = details if isinstance(details, Mapping) else {}
    source = details.get("source") if isinstance(details.get("source"), Mapping) else {}
    if not source and isinstance(details.get("manifest"), Mapping):
        manifest = details["manifest"]
        source = manifest.get("source") if isinstance(manifest.get("source"), Mapping) else {}
    return str(source.get("head") or "").strip()


class 发布管理:
    def __init__(
        self,
        registry: 账户注册表,
        lifecycle: 生命周期,
        *,
        source_status: Callable[..., dict[str, Any]] = 发布源状态,
        create_candidate: Callable[..., dict[str, Any]] = 创建候选,
        read_candidate: Callable[..., dict[str, Any]] = 读取候选,
        build_candidate: Callable[..., dict[str, Any]] = 验收并构建候选,
        list_versions: Callable[..., list[dict[str, Any]]] = 已发布版本列表,
        read_version: Callable[..., dict[str, Any]] = 读取正式版本,
        seal_version: Callable[..., dict[str, Any]] = 封存正式版本,
        cancel_seal: Callable[..., None] = 撤销未激活封存,
        discard_candidate: Callable[..., dict[str, Any]] = 废弃候选大文件,
        promote_images: Callable[[dict[str, object]], dict[str, str]] = 提升版本镜像,
        cleanup_images: Callable[[dict[str, object]], None] = 撤销未激活版本镜像,
        ensure_images: Callable[[str], bool] = 确保正式镜像,
        rollout_platform: Callable[..., dict[str, object]] = 切换平台版本,
        restore_platform: Callable[[str], dict[str, object]] = 恢复平台版本,
        restore_transaction_platform: Callable[[str], dict[str, object]] | None = None,
        ensure_platform: Callable[[str], bool] = 确保平台版本,
        current_platform: Callable[[], str] = 当前平台镜像,
        activate_version: Callable[[str], dict[str, Any]] = 切换当前版本,
        activate_staged_version: Callable[[str], dict[str, Any]] | None = None,
        current_version: Callable[[], str | None] = 当前已发布版本,
        prepare_host_boundary: Callable[[], dict[str, Any]] | None = None,
        prepare_candidate_host_runtime: Callable[[dict[str, Any]], dict[str, Any]] = 准备候选宿主环境,
        allow_automatic_host_prepare: bool = True,
        stage_host_runtime: Callable[[Mapping[str, Any], str], dict[str, Any]] | None = 暂存候选宿主运行面,
        restore_host_runtime: Callable[[str], dict[str, Any]] | None = 恢复候选宿主运行面,
        recover_active_host_runtime: Callable[[], dict[str, Any]] | None = None,
        finalize_host_runtime: Callable[[str], dict[str, Any]] | None = 确认候选宿主运行面,
        read_host_boundary: Callable[[], dict[str, Any]] | None = None,
        verify_host_capability: Callable[[], dict[str, Any]] | None = None,
        verify_host_shadow: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        verify_bootstrap_candidate: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        plan_host_switch: Callable[[str, str, str], dict[str, Any]] | None = None,
        plan_host_bootstrap_switch: Callable[[str, str, str], dict[str, Any]] | None = None,
        start_host_switch: Callable[[str], dict[str, Any]] | None = None,
        wait_host_switch: Callable[[str], dict[str, Any]] | None = None,
        verify_host_runtime: Callable[..., dict[str, dict[str, Any]]] | None = None,
        read_host_switch: Callable[[], dict[str, Any] | None] = 读取宿主切换,
    ) -> None:
        self.registry = registry
        self.lifecycle = lifecycle
        self._source_status = source_status
        self._create_candidate = create_candidate
        self._read_candidate = read_candidate
        self._build_candidate = build_candidate
        self._list_versions = list_versions
        self._read_version = read_version
        self._seal_version = seal_version
        self._cancel_seal = cancel_seal
        self._discard_candidate = discard_candidate
        self._promote_images = promote_images
        self._cleanup_images = cleanup_images
        self._ensure_images = ensure_images
        self._rollout_platform = rollout_platform
        self._restore_platform = restore_platform
        self._restore_transaction_platform = (
            restore_transaction_platform
            if restore_transaction_platform is not None
            else restore_platform if restore_platform is not 恢复平台版本 else 恢复事务平台版本
        )
        self._ensure_platform = ensure_platform
        self._current_platform = current_platform
        self._activate_version = activate_version
        self._activate_staged_version = (
            激活暂存版本
            if activate_staged_version is None and activate_version is 切换当前版本
            else activate_staged_version or activate_version
        )
        self._current_version = current_version
        self._prepare_host_boundary = prepare_host_boundary
        self._prepare_candidate_host_runtime = prepare_candidate_host_runtime
        self._allow_automatic_host_prepare = bool(allow_automatic_host_prepare)
        self._stage_host_runtime = stage_host_runtime
        self._restore_host_runtime = restore_host_runtime
        self._recover_active_host_runtime = recover_active_host_runtime
        self._finalize_host_runtime = finalize_host_runtime
        self._read_host_boundary = read_host_boundary
        self._verify_host_capability = verify_host_capability
        self._verify_host_shadow = verify_host_shadow
        self._verify_bootstrap_candidate = verify_bootstrap_candidate
        self._plan_host_switch = plan_host_switch
        self._plan_host_bootstrap_switch = plan_host_bootstrap_switch
        self._start_host_switch = start_host_switch
        self._wait_host_switch = wait_host_switch
        self._verify_host_runtime = verify_host_runtime
        self._read_host_switch = read_host_switch
        self._host_boundary_enabled = verify_host_capability is not None

    def 宿主环境状态(self) -> dict[str, Any]:
        """读取发布前置状态；只读，不创建环境、不重启服务。"""
        if not self._host_boundary_enabled:
            return {"ready": True, "requires_approval": False, "reason": ""}
        reader = self._read_host_boundary or self._verify_host_capability
        if reader is None:
            return {"ready": False, "requires_approval": True, "reason": "没有配置宿主环境核对器"}
        try:
            state = reader()
        except Exception as exc:
            return {
                "ready": False,
                "requires_approval": True,
                "reason": _错误文字(exc),
                "code": 宿主环境审批要求.code,
            }
        if not isinstance(state, dict):
            return {
                "ready": False,
                "requires_approval": True,
                "reason": "宿主环境核对器返回了无效状态",
                "code": 宿主环境审批要求.code,
            }
        return dict(state)

    def _发布前置宿主检查(self, action: str, *, candidate_id: str = "") -> dict[str, Any] | None:
        if not self._host_boundary_enabled:
            return None
        state = self.宿主环境状态()
        if state.get("ready"):
            try:
                return self._要求宿主边界已就绪(action)
            except Exception as exc:
                raise 宿主环境审批要求(action, _错误文字(exc), candidate_id=candidate_id) from exc
        raise 宿主环境审批要求(
            action,
            str(state.get("reason") or "宿主依赖环境尚未通过核对"),
            candidate_id=candidate_id,
        )

    def 批准候选宿主环境(self, candidate_id: str) -> dict[str, Any]:
        """在管理员批准后创建候选隔离环境；不触碰正式宿主。"""
        if not self._host_boundary_enabled:
            raise RuntimeError("当前发布器没有启用宿主环境隔离检查")
        record = self.registry.候选记录(candidate_id=candidate_id)
        if record is None:
            raise RuntimeError("候选记录不存在")
        if record.get("status") != "待发布":
            raise RuntimeError("只有已通过代码和镜像验收的候选才能建立隔离环境")
        candidate = self._read_candidate(candidate_id)
        runtime = self._prepare_candidate_host_runtime(dict(candidate))
        details = dict(record.get("details") or {})
        try:
            if self._verify_host_shadow is None:
                raise RuntimeError("没有配置候选宿主影子验收器")
            shadow = self._verify_host_shadow({
                **dict(candidate),
                "version": candidate_id,
                "version_name": str(details.get("product_version_name") or DEVELOPMENT_VERSION_NAME),
                "shadow_mode": "candidate",
                "host_runtime": runtime,
            })
        except Exception as exc:
            details["host_runtime"] = runtime
            details["failure"] = {
                "phase": "environment",
                "step": "候选隔离宿主验收",
                "summary": _错误文字(exc),
                "failed_checks": ["候选隔离宿主环境未通过三角色健康检查"],
                "next_action": "候选已停在失败状态；正式运行环境未改动，需由管理员决定后续处理。",
                "account_effect": "未触碰任何账号；当前正式版本继续运行。",
                "retry_class": "manual",
            }
            _追加阶段(details, "失败", "候选隔离宿主环境验收失败，未触碰正式运行面")
            self.registry.更新候选记录(
                candidate_id,
                status="失败",
                stage="候选隔离宿主环境验收失败，未触碰正式运行面",
                details=details,
            )
            raise RuntimeError(f"候选隔离宿主环境验收失败：{_错误文字(exc)}") from exc
        details["host_runtime"] = runtime
        details["host_shadow"] = {**dict(shadow or {}), "status": "passed"}
        details["host_runtime_approval"] = {
            "status": "approved",
            "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "candidate_id": candidate_id,
        }
        _追加阶段(details, record["status"], "候选隔离宿主环境已建立并通过验收，等待确认发布")
        self.registry.更新候选记录(
            candidate_id,
            status=record["status"],
            stage="候选隔离宿主环境已建立并通过验收，等待确认发布",
            details=details,
        )
        return runtime

    def _要求宿主边界已就绪(
        self, action: str, *, allow_legacy: bool = False,
    ) -> dict[str, Any] | None:
        if not self._host_boundary_enabled:
            return None
        try:
            assert self._verify_host_capability is not None
            capability = self._verify_host_capability()
        except Exception as exc:
            raise RuntimeError(f"宿主运行面还没准备好，不能{action}：{_错误文字(exc)}") from exc
        if not capability.get("ready"):
            raise RuntimeError(
                f"宿主运行面还没准备好，不能{action}："
                f"{capability.get('reason') or '请先执行宿主边界准备'}"
            )
        if capability.get("migration_required") and not allow_legacy:
            raise RuntimeError(
                f"当前还在旧单进程模式，不能{action}；请先单独确认迁移宿主运行面"
            )
        return capability

    def _核对首轮引导候选(
        self, candidate: dict[str, Any], capability: dict[str, Any],
    ) -> dict[str, Any]:
        if not capability.get("migration_required"):
            return {}
        if self._verify_bootstrap_candidate is None:
            raise RuntimeError("当前仍是旧单进程模式，但没有配置首轮候选宿主核对器")
        try:
            result = self._verify_bootstrap_candidate(dict(candidate))
        except Exception as exc:
            raise RuntimeError(f"首轮宿主引导候选核对失败：{_错误文字(exc)}") from exc
        if not isinstance(result, dict) or not result.get("ready"):
            missing = result.get("missing") if isinstance(result, dict) else None
            suffix = f"（缺少：{'、'.join(map(str, missing))}）" if missing else ""
            raise RuntimeError(f"候选不具备首轮三角色宿主入口{suffix}")
        return result

    def _首轮自动准备宿主边界(
        self,
        action: str,
        details: dict[str, Any],
        update: Callable[[str, str], dict[str, Any]],
    ) -> dict[str, Any] | None:
        """兼容旧测试/嵌入调用；正式管理服务显式关闭此兼容开关。"""
        if not self._host_boundary_enabled:
            return None
        assert self._verify_host_capability is not None
        reader = self._read_host_boundary or self._verify_host_capability
        try:
            capability = reader()
        except Exception as exc:
            raise RuntimeError(f"宿主运行面还没准备好，不能{action}：{_错误文字(exc)}") from exc
        if capability.get("ready") or not capability.get("migration_required"):
            if not capability.get("ready"):
                raise RuntimeError(
                    f"宿主运行面还没准备好，不能{action}："
                    f"{capability.get('reason') or '请先执行宿主边界准备'}"
                )
            if self._read_host_boundary is None:
                return capability
            try:
                return self._verify_host_capability()
            except Exception as exc:
                raise RuntimeError(f"宿主运行面复核失败：{_错误文字(exc)}") from exc
        reason = str(capability.get("reason") or "")
        preparable = {
            "独立宿主 Python 环境尚未准备",
            "独立运行目录中的本机私密扫描词尚未准备",
            "三个正式宿主启动描述尚未准备",
        }
        if reason not in preparable or self._prepare_host_boundary is None:
            raise RuntimeError(
                f"宿主运行面还没准备好，不能{action}：{reason or '请先执行宿主边界准备'}"
            )
        details["bootstrap"] = {
            "kind": "first-three-role-host",
            "status": "正在准备宿主运行文件（不会启动服务）",
            "from_legacy_version": str(
                (capability.get("active") or {}).get("version")
                if isinstance(capability.get("active"), dict)
                else ""
            ),
            "rollback_version": str(
                (capability.get("active") or {}).get("version")
                if isinstance(capability.get("active"), dict)
                else ""
            ),
            "preparation_reason": reason,
        }
        update("更新账号中", "正在准备首次宿主引导文件（只写运行文件，不启动服务）")
        try:
            self._prepare_host_boundary()
            capability = reader()
        except Exception as exc:
            raise RuntimeError(f"首次宿主运行文件准备失败：{_错误文字(exc)}") from exc
        if not capability.get("ready"):
            raise RuntimeError(
                "首次宿主运行文件已写入，但复核仍未通过："
                f"{capability.get('reason') or '状态未知'}"
            )
        if self._read_host_boundary is not None:
            try:
                capability = self._verify_host_capability()
            except Exception as exc:
                raise RuntimeError(f"宿主运行面复核失败：{_错误文字(exc)}") from exc
        details["bootstrap"]["status"] = "宿主运行文件已准备并复核通过"
        return capability

    def _开始宿主切换(
        self,
        deployment_id: str,
        version: str,
        release_artifact: dict[str, Any],
        details: dict[str, Any],
        *,
        bootstrap: bool = False,
    ) -> dict[str, Any] | None:
        if not self._host_boundary_enabled:
            return None
        source = release_artifact.get("source") if isinstance(release_artifact.get("source"), dict) else {}
        source_sha256 = str(source.get("sha256") or "")
        if len(source_sha256) != 64:
            raise RuntimeError("封存正式版本缺少源码身份，禁止切换宿主运行面")
        planner = self._plan_host_bootstrap_switch if bootstrap else self._plan_host_switch
        if planner is None or self._start_host_switch is None:
            raise RuntimeError("宿主切换计划器尚未配置")
        runtime = details.get("host_runtime") if isinstance(details.get("host_runtime"), dict) else None
        try:
            plan = planner(deployment_id, version, source_sha256, host_runtime=runtime)
        except TypeError:
            # Compatibility with embedded/test planners predating the optional runtime.
            plan = planner(deployment_id, version, source_sha256)
        if self._stage_host_runtime is not None:
            # Every switch gets a durable snapshot.  ``{}`` means keep the
            # already-approved host environment; a non-empty mapping selects
            # the newly approved candidate environment.
            runtime = runtime or {}
            previous_version = str(details.get("from_version") or "")
            try:
                try:
                    staged = self._stage_host_runtime(
                        runtime,
                        str(plan["switch_id"]),
                        previous_version=previous_version,
                    )
                except TypeError:
                    # Compatibility with embedded/test callbacks predating the
                    # durable previous-version snapshot.
                    staged = self._stage_host_runtime(runtime, str(plan["switch_id"]))
            except Exception as exc:
                try:
                    标记宿主切换失败(str(plan["switch_id"]), _错误文字(exc))
                except Exception:
                    pass
                raise
            details["host_runtime_switch"] = {
                **dict(staged or {}),
                "status": "staged",
                "switch_id": str(plan["switch_id"]),
            }
        try:
            state = self._start_host_switch(str(plan["switch_id"]))
        except Exception as exc:
            try:
                标记宿主切换失败(str(plan["switch_id"]), _错误文字(exc))
            except Exception:
                pass
            raise
        details["host_switch"] = {
            "switch_id": state.get("switch_id") or plan.get("switch_id"),
            "status": state.get("status") or "queued",
            "stage": state.get("stage") or "等待外部宿主助手",
            "target_version": version,
            "source_sha256": source_sha256,
            "mode": "legacy-migration" if bootstrap else "formal-switch",
            "helper_started": True,
        }
        return state

    def 收口宿主切换(self) -> dict[str, Any] | None:
        """Called by the new admin process after it is listening."""
        if not self._host_boundary_enabled:
            return None
        state = self._read_host_switch()
        if not state or state.get("status") not in {"host-ready", "completed"}:
            return state

        def waiting(reason: str) -> dict[str, Any]:
            nonlocal state
            if state.get("status") == "host-ready":
                try:
                    state = 标记宿主切换等待收口(
                        str(state.get("switch_id") or ""), reason,
                    )
                except Exception:
                    state = {**state, "closeout_waiting": reason}
            return state

        deployment_id = str(state.get("deployment_id") or "")
        if not deployment_id:
            return waiting("宿主记录缺少部署编号")
        record = self.registry.部署记录(deployment_id=deployment_id)
        if record is None:
            return waiting("找不到对应的部署记录")
        target_version = str(record.get("version") or "")
        target = state.get("target") if isinstance(state.get("target"), dict) else {}
        expected_source = ""
        try:
            expected_source = str((self._read_version(target_version).get("source") or {}).get("sha256") or "")
        except Exception as exc:
            return waiting(f"无法读取目标正式副本：{_错误文字(exc)}")
        # Never finalize a host snapshot from a stale or mismatched state file.
        # The registry, active lock, host-switch target and source fingerprint
        # must all describe the same sealed version first.
        if (
            str(state.get("deployment_id") or "") != deployment_id
            or str(target.get("version") or "") != target_version
            or str(target.get("source_sha256") or "") != expected_source
            or self._current_version() != target_version
        ):
            return waiting("部署记录、活动版本和宿主目标身份尚未一致")
        try:
            for account in self._在用账号():
                if (
                    account.get("image_ref") != f"xj-company:{target_version}"
                    or account.get("runner_image_ref") != f"xj-runner:{target_version}"
                ):
                    return waiting(f"账号 {account.get('account_id') or '未知'} 尚未登记目标版本")
                # Registry rows alone can be stale after a process crash.  A
                # host-ready receipt may be promoted only when every active
                # account is also running and healthy on the same image.
                account_problem = self._核对账号已恢复(
                    str(account["account_id"]),
                    image=f"xj-company:{target_version}",
                    runner_image=f"xj-runner:{target_version}",
                )
                if account_problem:
                    return waiting(
                        f"账号 {account['account_id']} 尚未健康：{account_problem}"
                    )
            actual_platform = self._current_platform()
            if actual_platform != f"xj-platform:{target_version}":
                return waiting(f"共享平台仍在运行 {actual_platform or '未知版本'}")
        except Exception as exc:
            return waiting(f"账号或共享平台核对失败：{_错误文字(exc)}")
        if self._verify_host_runtime is not None:
            try:
                self._verify_host_runtime(target_version, expected_source)
            except Exception as exc:
                return waiting(f"正式宿主核对失败：{_错误文字(exc)}")

        # The helper can finish the host switch just before the registry write
        # fails.  Complete the helper's own receipt only after every identity
        # check above has passed; a stale completed file cannot promote itself.
        try:
            closed = (
                标记宿主切换已收口(str(state["switch_id"]))
                if state.get("status") == "host-ready" else dict(state)
            )
        except Exception as exc:
            return waiting(f"管理后台无法确认宿主接管：{_错误文字(exc)}")
        if (
            closed.get("status") != "completed"
            or str((closed.get("target") or {}).get("version") or "") != target_version
            or str((closed.get("target") or {}).get("source_sha256") or "") != expected_source
        ):
            return waiting("宿主完成回执与目标版本不一致")

        record_details = record.get("details") if isinstance(record.get("details"), dict) else {}
        unresolved = record_details.get("rollback_failures") if isinstance(record_details.get("rollback_failures"), dict) else {}
        host_only_failure = (
            record.get("status") in {"失败", "已中断"}
            and unresolved
            and set(unresolved) <= {"host-switch"}
        )
        if record.get("status") not in {"更新账号中", "排队中"} and not host_only_failure:
            if self._finalize_host_runtime is not None and isinstance(closed.get("host_runtime"), dict):
                try:
                    self._finalize_host_runtime(str(closed["switch_id"]))
                except Exception as exc:
                    # Keep the receipt retryable.  Calling this transaction
                    # complete while its host snapshot is still host-ready
                    # would make the next release inherit another ambiguous
                    # half-closed state.
                    return {**closed, "closeout_waiting": f"宿主运行记录尚未收口：{_错误文字(exc)}"}
            return closed
        if self._finalize_host_runtime is not None and isinstance(closed.get("host_runtime"), dict):
            try:
                self._finalize_host_runtime(str(closed["switch_id"]))
            except Exception as exc:
                # The target services are healthy, so no rollback is needed;
                # however the durable host transaction is part of release
                # closeout.  Leave the deployment pending and let the existing
                # closeout loop retry instead of writing a false success.
                return {
                    **closed,
                    "closeout_waiting": f"宿主运行记录尚未收口：{_错误文字(exc)}",
                }
        details = dict(record.get("details") or {})
        host_details = details.get("host_switch") if isinstance(details.get("host_switch"), dict) else {}
        details["host_switch"] = host_details
        host_details.update({
            "switch_id": closed.get("switch_id"),
            "status": "completed",
            "stage": "三个正式宿主服务已收口",
            "target_version": target.get("version"),
            "source_sha256": target.get("source_sha256"),
            "completed_at": closed.get("completed_at"),
        })
        details.pop("rollback_failures", None)
        count = int(record.get("target_count") or 0)
        candidate_id = str(details.get("candidate_id") or "")
        if candidate_id:
            candidate_record = self.registry.候选记录(candidate_id=candidate_id)
            if candidate_record and candidate_record.get("status") != "已封存":
                candidate_details = dict(candidate_record.get("details") or {})
                candidate_details["version"] = target_version
                self.registry.更新候选记录(
                    candidate_id,
                    status="已封存",
                    stage=f"{details.get('version_name') or target_version}已正式发布",
                    details=candidate_details,
                )
        result = self.registry.更新部署记录(
            deployment_id,
            status="完成",
            stage="三个正式宿主服务已切换，版本生效",
            target_count=count,
            success_count=count,
            failed_count=0,
            details=details,
        )
        return result

    def _在用账号(self) -> list[dict[str, Any]]:
        return [row for row in self.registry.列表() if row["状态"] == "在用"]

    def _要求恢复已经核清(self, action: str) -> None:
        reader = getattr(self.registry, "未解决发布恢复", None)
        unresolved = reader() if callable(reader) else [
            item for item in self.registry.部署列表(limit=100)
            if (item.get("details") or {}).get("rollback_failures")
            or (item.get("details") or {}).get("retained_unactivated_release")
        ]
        if unresolved:
            raise RuntimeError(f"上一次部署还没有恢复核清，禁止{action}；请先重启后台完成恢复")

    def _要求没有进行中发布(self) -> None:
        """Use the durable registry guard even after an in-memory task died."""
        guard = getattr(self.registry, "要求没有发布维护", None)
        if callable(guard):
            guard()

    def _核对账号已恢复(
        self,
        account_id: str,
        *,
        image: str,
        runner_image: str,
    ) -> str:
        problems: list[str] = []
        try:
            account = self.registry.内部账户(account_id)
            if account.get("image_ref") != image or account.get("runner_image_ref") != runner_image:
                problems.append("登记版本仍不是部署前版本")
        except Exception as exc:
            problems.append(f"无法核对账号登记：{_错误文字(exc)}")
        state_reader = getattr(self.lifecycle, "实际运行状态", None)
        if callable(state_reader):
            try:
                state = state_reader(account_id)
                if state.get("image") != image:
                    problems.append("实际容器仍不是部署前版本")
                if state.get("running") is not True:
                    problems.append("原版本容器没有运行")
                if state.get("healthy") is not True:
                    problems.append("原版本容器没有通过健康检查")
            except Exception as exc:
                problems.append(f"无法核对实际容器：{_错误文字(exc)}")
        return "；".join(problems)

    @staticmethod
    def _登记保留产物(details: dict[str, Any], version: str) -> None:
        details["retained_unactivated_release"] = {
            "version": version,
            "reason": "回退尚未全部确认，保留正式副本和镜像供恢复使用",
        }

    def 恢复启动状态(self) -> dict[str, Any]:
        """管理服务重启时只在事实完整时收口，否则先等待或回退。"""
        current = self._current_version()
        startup_host_state: dict[str, Any] | None = None
        if self._host_boundary_enabled:
            try:
                value = self._read_host_switch()
                startup_host_state = value if isinstance(value, dict) else None
            except Exception:
                startup_host_state = None
        pending_deployments = []
        for record in self.registry.部署列表(limit=100):
            record_details = record.get("details") if isinstance(record.get("details"), dict) else {}
            host_details = (
                record_details.get("host_switch")
                if isinstance(record_details.get("host_switch"), dict)
                else {}
            )
            restart_recovery = (
                record_details.get("restart_recovery")
                if isinstance(record_details.get("restart_recovery"), dict)
                else {}
            )
            unresolved = bool(
                record_details.get("rollback_failures")
                or record_details.get("retained_unactivated_release")
            )
            detail_switch_id = str(host_details.get("switch_id") or "")
            helper_matches_current_switch = bool(
                isinstance(startup_host_state, dict)
                and (
                    str(startup_host_state.get("deployment_id") or "")
                    == str(record.get("deployment_id") or "")
                    or (
                        detail_switch_id
                        and str(startup_host_state.get("switch_id") or "")
                        == detail_switch_id
                    )
                )
            )
            if record["status"] in {"排队中", "更新账号中"} or (
                record["status"] in {"失败", "已中断"} and unresolved
            ) or (
                record["status"] in {"失败", "已中断"}
                and host_details.get("helper_started")
                and not restart_recovery.get("complete")
                and helper_matches_current_switch
            ):
                pending_deployments.append(record)

        startup_failures: dict[str, str] = {}
        current_name = DEVELOPMENT_VERSION_NAME
        if current:
            try:
                current_name = str(self._read_version(current).get("version_name") or current)
            except Exception:
                current_name = current
            # This only restores the management process' default pair.  It is
            # deliberately not an instruction to wake images or the shared
            # platform before a pending deployment has been classified.
            try:
                self.lifecycle.设置默认版本(
                    f"xj-company:{current}", f"xj-runner:{current}",
                )
            except Exception as exc:
                startup_failures["default-version"] = _错误文字(exc)

        def _匹配宿主状态(
            deployment_id: str, details: dict[str, Any],
        ) -> dict[str, Any] | None:
            if not self._host_boundary_enabled:
                return None
            try:
                state = self._read_host_switch()
            except Exception:
                return None
            if not isinstance(state, dict):
                return None
            detail = details.get("host_switch") if isinstance(details.get("host_switch"), dict) else {}
            detail_switch_id = str(detail.get("switch_id") or "")
            if (
                str(state.get("deployment_id") or "") == deployment_id
                or (detail_switch_id and str(state.get("switch_id") or "") == detail_switch_id)
            ):
                return state
            return None

        def _开发发布者仍存活(details: dict[str, Any]) -> bool:
            host = details.get("host_switch") if isinstance(details.get("host_switch"), dict) else {}
            if host.get("phase") not in {"pre-platform", "pre-platform-ready"}:
                return False
            try:
                pid = int(host.get("orchestrator_pid") or 0)
            except (TypeError, ValueError):
                return False
            if pid <= 0:
                return False
            try:
                os.kill(pid, 0)
            except (OSError, ProcessLookupError):
                return False
            return True

        def _宿主源(version: str) -> str:
            try:
                value = self._read_version(version)
            except Exception:
                return ""
            source = value.get("source") if isinstance(value.get("source"), dict) else {}
            return str(source.get("sha256") or "")

        def _宿主严格完成(
            state: dict[str, Any] | None,
            details: dict[str, Any],
            deployment_id: str,
            version: str,
            source_sha256: str,
        ) -> bool:
            if not self._host_boundary_enabled:
                return True
            if not isinstance(state, dict) or state.get("status") != "completed":
                return False
            target = state.get("target") if isinstance(state.get("target"), dict) else {}
            if (
                str(state.get("deployment_id") or "") != deployment_id
                or str(target.get("version") or "") != version
                or str(target.get("source_sha256") or "") != source_sha256
            ):
                return False
            detail = details.get("host_switch") if isinstance(details.get("host_switch"), dict) else {}
            for key, expected in (
                ("target_version", version), ("source_sha256", source_sha256),
            ):
                recorded = str(detail.get(key) or "")
                if recorded and recorded != expected:
                    return False
            if self._verify_host_runtime is not None:
                try:
                    self._verify_host_runtime(version, source_sha256)
                except Exception:
                    return False
            return True

        def _当前目标核对(
            version: str,
            *,
            expected_source: str,
            repair_accounts: bool,
            platform_expected: str = "",
        ) -> dict[str, str]:
            failures: dict[str, str] = {}
            target_image = f"xj-company:{version}"
            target_runner = f"xj-runner:{version}"
            recover = getattr(self.lifecycle, "恢复中断版本", None)
            for account in self._在用账号():
                account_id = str(account["account_id"])
                verification = self._核对账号已恢复(
                    account_id, image=target_image, runner_image=target_runner,
                )
                if verification and repair_accounts and callable(recover):
                    try:
                        recover(
                            account_id,
                            image=target_image,
                            runner_image=target_runner,
                            actor="supervisor-release-recovery",
                        )
                        verification = self._核对账号已恢复(
                            account_id, image=target_image, runner_image=target_runner,
                        )
                    except Exception as exc:
                        verification = _错误文字(exc)
                if verification:
                    failures[account_id] = verification
            try:
                if platform_expected and self._current_platform() != platform_expected:
                    failures["shared-platform"] = "共享平台没有运行目标版本"
            except Exception as exc:
                failures["shared-platform"] = _错误文字(exc)
            if self._verify_host_runtime is not None:
                try:
                    self._verify_host_runtime(version, expected_source)
                except Exception as exc:
                    failures["host-runtime"] = _错误文字(exc)
            return failures

        def _等待宿主助手(
            record: dict[str, Any], details: dict[str, Any], message: str,
        ) -> tuple[dict[str, Any], dict[str, str]]:
            deployment_id = str(record["deployment_id"])
            failures = {"host-switch": message}
            host_details = details.get("host_switch") if isinstance(details.get("host_switch"), dict) else {}
            host_switch_id = str(host_details.get("switch_id") or "")
            host_state = _匹配宿主状态(deployment_id, details)
            if isinstance(host_state, dict):
                host_switch_id = str(host_state.get("switch_id") or host_switch_id)
            if host_switch_id and isinstance(host_state, dict) and host_state.get("status") in {
                "queued", "switching", "host-ready",
            }:
                try:
                    标记宿主切换失败(host_switch_id, message)
                except Exception:
                    # The deployment record below remains the source of truth;
                    # a later startup pass can retry the host cancellation.
                    pass
            details["rollback_failures"] = failures
            details["failure"] = {
                "phase": "host-switch",
                "step": "重启后等待宿主切换",
                "summary": message,
                "exit_code": None,
                "failed_checks": [f"host-switch：{message}"],
                "next_action": "等待宿主助手收口；若助手已退出，请再次重启后台触发自动回退。",
                "account_effect": "没有并发回退账号或共享平台，避免与宿主助手互相覆盖。",
                "retry_class": "manual",
            }
            _追加阶段(details, "失败", "宿主切换仍在处理中，暂不并发回退")
            count = int(record.get("target_count") or 0)
            result = self.registry.更新部署记录(
                deployment_id,
                status="失败",
                stage="宿主切换仍在处理中，暂不并发回退",
                target_count=count,
                success_count=int(record.get("success_count") or 0),
                failed_count=int(record.get("failed_count") or 0),
                details=details,
            )
            return result, failures

        def _回退中断部署(
            record: dict[str, Any],
            details: dict[str, Any],
            account_details: dict[str, Any],
            version: str,
            active_before: str | None,
            host_state: dict[str, Any] | None,
        ) -> tuple[dict[str, Any], dict[str, str]]:
            deployment_id = str(record["deployment_id"])
            platform = details.get("platform") if isinstance(details.get("platform"), dict) else {}
            failures: dict[str, str] = {}
            previous_version = str(record.get("from_version") or details.get("from_version") or "")
            runtime_switch = details.get("host_runtime_switch")
            runtime_switch = runtime_switch if isinstance(runtime_switch, dict) else {}
            switch_id = str(runtime_switch.get("switch_id") or "")
            if not switch_id and isinstance(host_state, dict):
                switch_id = str(host_state.get("switch_id") or "")
            if switch_id and self._restore_host_runtime is not None:
                try:
                    restored = self._restore_host_runtime(switch_id)
                    details["host_runtime_switch"] = {
                        **runtime_switch,
                        **dict(restored or {}),
                        "switch_id": switch_id,
                        "status": "restored",
                    }
                except Exception as exc:
                    failures["host-runtime"] = _错误文字(exc)

            # Restoring the active pointer is separate from the public manual
            # rollback API.  The latter accepts sealed versions only; this
            # internal transaction undo may restore the recorded pre-deploy
            # pointer (including the initial development pointer).
            try:
                after_host = self._current_version()
            except Exception:
                after_host = active_before
            if active_before == version:
                if previous_version and after_host != previous_version:
                    if previous_version == "dev":
                        failures["active-version"] = "部署前是开发指针，但恢复后活动版本不是开发指针"
                    else:
                        try:
                            self._activate_version(previous_version)
                            if self._current_version() != previous_version:
                                raise RuntimeError("活动版本锁恢复后仍不是部署前版本")
                            details["active_version_recovery"] = "已恢复部署前版本"
                        except Exception as exc:
                            failures["active-version"] = _错误文字(exc)
                elif not previous_version and after_host == version:
                    failures["active-version"] = "缺少部署前活动版本，无法安全回退"
            elif active_before not in {None, "", previous_version}:
                failures["active-version"] = "活动版本不是本次部署目标或部署前版本，拒绝覆盖"

            previous_pair: tuple[str, str] | None = None
            for item in account_details.values():
                if not isinstance(item, dict):
                    continue
                image = str(item.get("previous_image") or "")
                runner = str(item.get("previous_runner_image") or "")
                if image and runner:
                    previous_pair = (image, runner)
                    break
            if previous_pair is None and previous_version:
                previous_pair = (
                    "xj-company:dev" if previous_version == "dev" else f"xj-company:{previous_version}",
                    "xj-runner:dev" if previous_version == "dev" else f"xj-runner:{previous_version}",
                )
            if previous_pair is not None:
                try:
                    self.lifecycle.设置默认版本(*previous_pair)
                except Exception as exc:
                    failures["new-account-default"] = _错误文字(exc)

            previous_platform = str(platform.get("previous_image") or "")
            if previous_platform:
                try:
                    self._restore_transaction_platform(previous_platform)
                    details["platform"] = {**platform, "status": "重启后已恢复原版本"}
                except Exception as exc:
                    failures["shared-platform"] = _错误文字(exc)

            target_image = f"xj-company:{version}"
            target_runner = f"xj-runner:{version}"
            rolled_back = 0
            actual_reader = getattr(self.lifecycle, "实际运行镜像", None)
            recover = getattr(self.lifecycle, "恢复中断版本", self.lifecycle.升级版本)
            for account_id, item in account_details.items():
                if not isinstance(item, dict):
                    continue
                try:
                    account = self.registry.内部账户(account_id)
                except Exception:
                    continue
                actual_image = None
                if callable(actual_reader):
                    try:
                        actual_image = actual_reader(account_id)
                    except Exception as exc:
                        failures[account_id] = f"无法核对实际容器镜像：{_错误文字(exc)}"
                        continue
                registry_at_target = (
                    account.get("image_ref") == target_image
                    or account.get("runner_image_ref") == target_runner
                )
                touched = str(item.get("status") or "") in {"更新中", "完成", "失败"}
                if not touched and not registry_at_target and actual_image != target_image:
                    continue
                previous_image = str(item.get("previous_image") or "")
                previous_runner = str(item.get("previous_runner_image") or "")
                if not previous_image or not previous_runner:
                    failures[account_id] = "缺少部署前版本记录"
                    continue
                try:
                    recover(
                        account_id,
                        image=previous_image,
                        runner_image=previous_runner,
                        actor="supervisor-release-recovery",
                    )
                    verification = self._核对账号已恢复(
                        account_id, image=previous_image, runner_image=previous_runner,
                    )
                    if verification:
                        failures[account_id] = verification
                        item.update({"status": "回退后核对失败", "error": verification})
                    else:
                        item["status"] = "重启后已退回原版本"
                        rolled_back += 1
                except Exception as exc:
                    failures[account_id] = _错误文字(exc)
                    item.update({"status": "回退失败", "error": _错误文字(exc)})

            candidate_id = str(details.get("candidate_id") or "")
            if failures:
                self._登记保留产物(details, version)
            elif candidate_id:
                try:
                    candidate = self._read_candidate(candidate_id)
                    self._cleanup_images({**candidate, "version": version})
                    details["formal_images"] = "重启后已清理未激活标签"
                except Exception as exc:
                    failures["unactivated-release-images"] = _错误文字(exc)
            if not failures and candidate_id:
                try:
                    self._cancel_seal(version, candidate_id)
                except Exception as exc:
                    failures["unactivated-release-copy"] = _错误文字(exc)
            if failures and candidate_id:
                self._登记保留产物(details, version)

            # 启动描述恢复、平台回退和未激活副本清理完成后，必须再核对
            # 正式控制端与管理端。仅有旧进程存活不等于正式后台已恢复。
            if switch_id and self._recover_active_host_runtime is not None:
                try:
                    active_version = str(self._current_version() or "")
                    active_source = _宿主源(active_version)
                    if not active_version or not active_source:
                        raise RuntimeError("无法读取回退后的正式版本身份")
                    try:
                        if self._verify_host_runtime is None:
                            raise RuntimeError("没有配置正式后台核对器")
                        self._verify_host_runtime(active_version, active_source)
                    except Exception:
                        restored_host = self._recover_active_host_runtime()
                        details["formal_host_recovery"] = dict(restored_host or {})
                        self._verify_host_runtime(active_version, active_source)
                except Exception as exc:
                    failures["formal-host-runtime"] = _错误文字(exc)

            if failures:
                details["rollback_failures"] = failures
            else:
                details.pop("rollback_failures", None)
                details.pop("retained_unactivated_release", None)
                if candidate_id:
                    try:
                        candidate_record = self.registry.候选记录(candidate_id=candidate_id)
                        candidate_details = dict(candidate_record.get("details") or {}) if candidate_record else {}
                        candidate_details.pop("version", None)
                        candidate_details["last_failed_deployment"] = deployment_id
                        self.registry.更新候选记录(
                            candidate_id,
                            status="待发布",
                            stage="宿主切换失败，已恢复原正式版本；候选可重新确认发布",
                            details=candidate_details,
                        )
                    except Exception as exc:
                        failures["candidate-state"] = _错误文字(exc)
                        details["rollback_failures"] = failures
                        self._登记保留产物(details, version)
            details["restart_recovery"] = {
                "rolled_back": rolled_back,
                "failures": failures,
                "complete": not failures,
            }
            details["failure"] = {
                "phase": "host-switch" if self._host_boundary_enabled else "activation",
                "step": "服务重启回退",
                "summary": "服务重启后发现部署没有完整收口",
                "exit_code": None,
                "failed_checks": [f"{name}：{message}" for name, message in failures.items()],
                "next_action": (
                    "先处理列出的回退或清理失败项，确认运行版本一致后再继续发布。"
                    if failures else "系统已恢复原版本；查清失败原因后，可以重新确认这个候选。"
                ),
                "account_effect": (
                    "部署未完成；存在未能自动恢复的项目，后续发布已经停止。"
                    if failures else "部署未完成；已变更的账号和共享平台都已恢复原版本。"
                ),
                "retry_class": "manual" if failures else "retryable",
            }
            count = int(record.get("target_count") or len(account_details))
            result = self.registry.更新部署记录(
                deployment_id,
                status="失败",
                stage=("服务重启，部分回退失败" if failures else "服务重启，已退回原版本"),
                target_count=count,
                success_count=0,
                failed_count=count,
                details=details,
            )
            return result, failures

        recovered: list[str] = []
        live_deployments: set[str] = set()
        rollback_failures: dict[str, dict[str, str]] = {}
        for record in pending_deployments:
            deployment_id = str(record["deployment_id"])
            version = str(record["version"])
            details = dict(record.get("details") or {})
            account_details = details.get("accounts") if isinstance(details.get("accounts"), dict) else {}
            expected_source = _宿主源(version)
            active_before = current
            host_state = _匹配宿主状态(deployment_id, details)

            # During the new two-phase deployment, the formal admin is
            # started by the host helper before the shared platform and user
            # accounts are switched.  Its normal startup recovery must not
            # race the owner's still-running deployment worker.  If that
            # worker has died, the ordinary rollback path below takes over.
            if (
                current == version
                and record.get("status") in {"排队中", "更新账号中"}
                and _开发发布者仍存活(details)
            ):
                recovered.append(deployment_id)
                live_deployments.add(deployment_id)
                continue

            # A host-ready receipt is an invitation to reconcile, not proof
            # that the registry update succeeded.  Give the normal close-out
            # path one chance before deciding whether to wait or roll back.
            if (
                self._host_boundary_enabled
                and isinstance(host_state, dict)
                and host_state.get("status") == "host-ready"
            ):
                try:
                    self.收口宿主切换()
                except Exception:
                    pass
                host_state = _匹配宿主状态(deployment_id, details) or host_state
                record = self.registry.部署记录(deployment_id=deployment_id) or record

            if current == version:
                if self._host_boundary_enabled and not _宿主严格完成(
                    host_state, details, deployment_id, version, expected_source,
                ):
                    live = bool(
                        isinstance(host_state, dict)
                        and host_state.get("status") in {"queued", "switching"}
                        and 宿主助手存活(host_state)
                    )
                    if live:
                        _, failures = _等待宿主助手(
                            record, details,
                            "宿主助手仍在运行或已经报告就绪，不能与它并发回退",
                        )
                        recovered.append(deployment_id)
                        rollback_failures[deployment_id] = failures
                        continue
                    # A host-ready helper is idle and waiting for the
                    # management commit.  It has not started rollback itself,
                    # so management can reconcile accounts/platform first and
                    # then signal the helper to restore the old processes.
                    result, failures = _回退中断部署(
                        record, details, account_details, version, active_before, host_state,
                    )
                    recovered.append(deployment_id)
                    if failures:
                        rollback_failures[deployment_id] = failures
                    current = self._current_version()
                    continue

                activation_failures = _当前目标核对(
                    version,
                    expected_source=expected_source,
                    repair_accounts=True,
                    platform_expected=str(
                        (details.get("platform") or {}).get("target_image") or ""
                    ) if isinstance(details.get("platform"), dict) else "",
                )
                candidate_id = str(details.get("candidate_id") or "")
                count = int(record.get("target_count") or len(account_details))
                if activation_failures:
                    details["rollback_failures"] = activation_failures
                    details["failure"] = {
                        "phase": "activation", "step": "重启后运行核对",
                        "summary": "版本锁已经生效，但部分运行资源没有恢复健康",
                        "exit_code": None,
                        "failed_checks": [
                            f"{name}：{message}" for name, message in activation_failures.items()
                        ],
                        "next_action": "先恢复列出的运行资源并核对全部账号，再继续发布。",
                        "account_effect": "版本已经登记生效，但部分账号或共享平台需要人工恢复。",
                        "retry_class": "manual",
                    }
                    self.registry.更新部署记录(
                        deployment_id,
                        status="失败",
                        stage="版本已生效，但重启后有运行资源未恢复",
                        target_count=count,
                        success_count=max(0, count - len([
                            key for key in activation_failures if key.startswith("acc_")
                        ])),
                        failed_count=len([
                            key for key in activation_failures if key.startswith("acc_")
                        ]),
                        details=details,
                    )
                    recovered.append(deployment_id)
                    rollback_failures[deployment_id] = activation_failures
                    continue
                if candidate_id:
                    candidate_record = self.registry.候选记录(candidate_id=candidate_id)
                    if candidate_record and candidate_record["status"] != "已封存":
                        candidate_details = dict(candidate_record.get("details") or {})
                        candidate_details["version"] = version
                        self.registry.更新候选记录(
                            candidate_id,
                            status="已封存",
                            stage=f"{details.get('version_name') or current_name}已正式发布",
                            details=candidate_details,
                        )
                self.registry.更新部署记录(
                    deployment_id,
                    status="完成",
                    stage="重启后核对：版本锁、账号、平台和宿主均已生效",
                    target_count=count,
                    success_count=count,
                    failed_count=0,
                    details=details,
                )
                recovered.append(deployment_id)
                continue

            if (
                self._host_boundary_enabled
                and isinstance(host_state, dict)
                and host_state.get("status") in {"queued", "switching"}
                and 宿主助手存活(host_state)
            ):
                _, failures = _等待宿主助手(
                    record, details,
                    "宿主助手仍在运行，先等待它依据切换记录自行停止或恢复",
                )
                recovered.append(deployment_id)
                rollback_failures[deployment_id] = failures
                continue

            # A stale helper record is no longer allowed to race the recovery.
            if (
                self._host_boundary_enabled
                and isinstance(host_state, dict)
                and host_state.get("status") in {"queued", "switching"}
            ):
                try:
                    标记宿主切换失败(
                        str(host_state.get("switch_id") or ""),
                        "管理服务重启时发现宿主助手已退出，改走事务回退",
                    )
                except Exception:
                    pass
            result, failures = _回退中断部署(
                record, details, account_details, version, active_before, host_state,
            )
            recovered.append(deployment_id)
            if failures:
                rollback_failures[deployment_id] = failures
            current = self._current_version()

        # 部署账即使已经完成回退，正式进程也可能只剩控制端而管理端已退出。
        # 只有开发发布后台负责发现并恢复这个缺口，正式后台不会重启自己。
        if current and self._recover_active_host_runtime is not None:
            expected_source = _宿主源(str(current))
            try:
                if not expected_source or self._verify_host_runtime is None:
                    raise RuntimeError("无法核对当前正式后台身份")
                try:
                    self._verify_host_runtime(str(current), expected_source)
                except Exception:
                    self._recover_active_host_runtime()
                    self._verify_host_runtime(str(current), expected_source)
            except Exception as exc:
                startup_failures["formal-host-runtime"] = _错误文字(exc)

        interrupted = self.registry.中断未完成发布(
            排除部署编号=live_deployments,
        )
        return {
            "current_version": current,
            "recovered_deployments": recovered,
            "rollback_failures": rollback_failures,
            "startup_failures": startup_failures,
            "interrupted_operations": interrupted,
        }

    def 状态(self) -> dict[str, Any]:
        try:
            source = self._source_status()
        except Exception as exc:
            source = {"ready": False, "reason": _错误文字(exc), "source": None}
        current = self._current_version()
        current_name = DEVELOPMENT_VERSION_NAME
        if current:
            current_name = next(
                (str(item.get("version_name") or current)
                 for item in self._list_versions(verify=False)
                 if item.get("version") == current),
                current,
            )
        versions = []
        for item in self._list_versions(verify=False):
            versions.append({
                "version": item["version"],
                "version_name": item.get("version_name") or 版本显示名(item["version"]),
                "candidate_id": item.get("candidate_id"),
                "published_at": item.get("published_at"),
            })
        host_runtime = self.宿主环境状态()
        return {
            "product_version": current,
            "product_version_name": current_name,
            "development_version_name": DEVELOPMENT_VERSION_NAME,
            "current_version": current,
            "active_accounts": len(self._在用账号()),
            "source": source,
            "latest_candidate": self.registry.候选记录(),
            "latest_deployment": self.registry.部署记录(),
            "candidates": self.registry.候选列表(limit=20),
            "deployments": self.registry.部署列表(limit=20),
            "versions": versions,
            "legacy_failure": self.registry.发布记录(),
            "host_runtime": host_runtime,
        }

    def 候选日志(self, candidate_id: str) -> dict[str, Any]:
        record = self.registry.候选记录(candidate_id=candidate_id)
        if record is None:
            raise RuntimeError("候选记录不存在")
        candidate = self._read_candidate(candidate_id, verify=False)
        acceptance = candidate.get("acceptance") if isinstance(candidate.get("acceptance"), dict) else {}
        log = acceptance.get("log") if isinstance(acceptance.get("log"), dict) else {}
        name = str(log.get("file") or "")
        if name != "release.log":
            raise RuntimeError("该候选还没有可查看的发布日志")
        root = Path(str(candidate["path"])).resolve()
        path = (root / name).resolve()
        if root not in path.parents or not path.is_file() or path.is_symlink():
            raise RuntimeError("候选发布日志不存在或路径无效")
        maximum = 512 * 1024
        with path.open("rb") as source:
            if path.stat().st_size > maximum:
                source.seek(-maximum, 2)
                raw = source.read()
                prefix = "[日志较长，以下显示最后 512KB]\n"
            else:
                raw = source.read()
                prefix = ""
        text = raw.decode("utf-8", errors="replace")
        expected = str(log.get("sha256") or "")
        if expected:
            import hashlib

            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != expected:
                raise RuntimeError("候选发布日志校验失败，拒绝展示")
        return {
            "candidate_id": candidate_id,
            "log": prefix + text,
            "complete": bool(log.get("complete")),
            "sha256": expected,
        }

    def 准备候选(self, notes: str, expected_head: str) -> dict[str, Any]:
        self._要求没有进行中发布()
        self._要求恢复已经核清("生成新候选")
        source = self._source_status()
        if not source["ready"]:
            raise RuntimeError(f"禁止生成候选：{source['reason']}")
        actual_head = str((source.get("source") or {}).get("head") or "")
        if not expected_head or expected_head != actual_head:
            raise RuntimeError("发布源提交已经变化，请重新确认")
        previous = self.registry.候选记录()
        if previous and previous.get("status") == "待发布":
            previous_details = dict(previous.get("details") or {})
            previous_head = _候选源提交(previous_details)
            if previous_head and previous_head != actual_head:
                _追加阶段(previous_details, "已中断", "开发版已变化，旧候选已过期，未触碰任何账号")
                previous_details["stale_reason"] = "开发版提交已变化，候选必须重新生成"
                self.registry.更新候选记录(
                    previous["candidate_id"],
                    status="已中断",
                    stage="开发版已变化，旧候选已过期，未触碰任何账号",
                    details=previous_details,
                )
                try:
                    cleanup = self._discard_candidate(
                        previous["candidate_id"], allow_ready=True,
                    )
                    previous_details["storage_cleanup"] = {
                        "status": "completed",
                        "reclaimed_bytes": int(cleanup.get("reclaimed_bytes") or 0),
                    }
                except Exception as exc:
                    previous_details["storage_cleanup"] = {
                        "status": "failed", "reason": _错误文字(exc),
                    }
                self.registry.更新候选记录(
                    previous["candidate_id"],
                    status="已中断",
                    stage="开发版已变化，旧候选已过期，未触碰任何账号",
                    details=previous_details,
                )
        candidate_id = 生成候选编号()
        details = {
            "track": "development",
            "product_version": None,
            "product_version_name": DEVELOPMENT_VERSION_NAME,
            "source": source["source"],
        }
        _追加阶段(details, "排队中", "等待固定候选源码")
        return self.registry.创建候选记录(
            candidate_id,
            notes,
            details=details,
        )

    def 执行候选(self, candidate_id: str) -> dict[str, Any]:
        record = self.registry.候选记录(candidate_id=candidate_id)
        if record is None or record["status"] != "排队中":
            raise RuntimeError("候选记录不在可执行状态")
        details = dict(record.get("details") or {})

        def update(status: str, stage: str) -> dict[str, Any]:
            _追加阶段(details, status, stage)
            return self.registry.更新候选记录(
                candidate_id, status=status, stage=stage, details=details,
            )

        try:
            update("验收中", "正在固定候选源码")
            source = details.get("source") if isinstance(details.get("source"), dict) else {}
            manifest = self._create_candidate(
                expected_head=str(source.get("head") or ""),
                notes=record["notes"],
                candidate_id=candidate_id,
            )
            details["manifest"] = {"path": manifest.get("path"), "source": manifest.get("source")}

            def stage(status: str, text: str) -> None:
                update(status, text)

            manifest = self._build_candidate(candidate_id, on_stage=stage)
            details["manifest"] = {
                "path": manifest.get("path"),
                "source": manifest.get("source"),
                "acceptance": manifest.get("acceptance"),
                "images": manifest.get("images"),
            }
            return update("待发布", "全部验收通过，等待确认发布")
        except Exception as exc:
            try:
                manifest = self._read_candidate(candidate_id, verify=False)
                details["manifest"] = {
                    "source": manifest.get("source"),
                    "acceptance": manifest.get("acceptance"),
                    "images": manifest.get("images"),
                }
            except Exception:
                manifest = {}
            failure = dict(getattr(exc, "details", {}) or {})
            if not failure and isinstance(manifest, dict):
                images = manifest.get("images") if isinstance(manifest.get("images"), dict) else {}
                acceptance = manifest.get("acceptance") if isinstance(manifest.get("acceptance"), dict) else {}
                raw = images.get("failure") or acceptance.get("failure")
                failure = dict(raw) if isinstance(raw, dict) else {}
            if not failure:
                failure = {
                    "phase": "release-system", "step": "候选制造",
                    "summary": _错误文字(exc), "exit_code": None, "failed_checks": [],
                    "next_action": "查看候选日志，修正问题后重新生成候选。",
                    "account_effect": "未触碰任何账号；当前正式版本继续运行。",
                    "retry_class": "source-fix",
                }
            details["failure"] = failure
            details["error"] = str(failure.get("summary") or _错误文字(exc))
            return update("失败", "候选未通过，未触碰任何账号")

    def 准备发布(self, candidate_id: str, version_name: str) -> dict[str, Any]:
        # A crashed management process may have cleared its in-memory task
        # while the durable deployment is still active.  Refuse a second
        # deployment until that record is reconciled.
        self._要求没有进行中发布()
        candidate_record = self.registry.候选记录(candidate_id=candidate_id)
        if candidate_record is None or candidate_record["status"] != "待发布":
            raise RuntimeError("只能发布已通过全部验收的候选")
        candidate = self._read_candidate(candidate_id)
        contract = candidate.get("data_contract") if isinstance(candidate.get("data_contract"), dict) else {}
        if contract.get("migration") != "none":
            raise RuntimeError("这个候选需要修改用户数据；当前发布器不执行数据迁移，禁止发布")
        current_version = self._current_version()
        if current_version:
            current = self._read_version(current_version)
            current_contract = current.get("data_contract") if isinstance(current.get("data_contract"), dict) else {}
            current_schema = int(current_contract.get("schema_version") or 0)
            candidate_schema = int(contract.get("schema_version") or 0)
            compatible_from = int(contract.get("compatible_from") or 0)
            compatible_to = int(contract.get("compatible_to") or -1)
            if current_schema < 1 or candidate_schema != current_schema:
                raise RuntimeError("候选的数据格式与当前版本不同；没有迁移方案，禁止发布")
            if not compatible_from <= current_schema <= compatible_to:
                raise RuntimeError("候选不能直接读取当前用户数据，禁止发布")
        source = self._source_status()
        candidate_source = candidate.get("source") if isinstance(candidate.get("source"), dict) else {}
        if not source.get("ready") or str((source.get("source") or {}).get("head") or "") != str(candidate_source.get("head") or ""):
            raise RuntimeError("开发版在候选生成后已经变化，请重新生成候选")
        candidate_details = (
            candidate_record.get("details")
            if isinstance(candidate_record.get("details"), dict) else {}
        )
        approved_runtime = candidate_details.get("host_runtime")
        if not (isinstance(approved_runtime, dict) and approved_runtime.get("status") == "ready"):
            self._发布前置宿主检查("确认发布", candidate_id=candidate_id)
        self._要求恢复已经核清("继续发布")
        try:
            version_name = 校验版本显示名(version_name)
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc
        if any(item.get("version_name") == version_name for item in self._list_versions(verify=False)):
            raise RuntimeError(f"正式版本名称已经存在：{version_name}")
        version = _正式版本标识(candidate_id)
        if any(item.get("version") == version for item in self._list_versions(verify=False)):
            version = _现在编号("twilight-release")
        accounts = self._在用账号()
        details = {
            "candidate_id": candidate_id,
            "version_name": version_name,
            "from_version": current_version,
            "accounts": {},
        }
        if isinstance(approved_runtime, dict) and approved_runtime.get("status") == "ready":
            details["host_runtime"] = dict(approved_runtime)
        _追加阶段(details, "排队中", "等待开始正式部署")
        return self.registry.创建部署记录(
            _现在编号("deployment"),
            version,
            kind="发布",
            from_version=current_version,
            target_count=len(accounts),
            details=details,
        )

    def 准备回退(self, version: str) -> dict[str, Any]:
        self._要求没有进行中发布()
        self._要求恢复已经核清("回退其他版本")
        if version == self._current_version():
            raise RuntimeError("选中的已经是当前运行版本")
        target = self._read_version(version)
        current_version = self._current_version()
        if current_version:
            current = self._read_version(current_version)
            current_schema = int((current.get("data_contract") or {}).get("schema_version", 0))
            contract = target.get("data_contract") if isinstance(target.get("data_contract"), dict) else {}
            # 旧的正式版本（schema 1）没有 migration 字段，按无迁移兼容读取；
            # 新版本若明确声明需要迁移，当前发布器必须拒绝回退。
            if contract.get("migration", "none") != "none":
                raise RuntimeError("目标版本需要修改用户数据；当前发布器不执行数据迁移，禁止回退")
            if not int(contract.get("compatible_from", 0)) <= current_schema <= int(contract.get("compatible_to", -1)):
                raise RuntimeError("目标版本不兼容当前用户数据，禁止直接回退")
        accounts = self._在用账号()
        details: dict[str, Any] = {"accounts": {}}
        _追加阶段(details, "排队中", "等待开始版本回退")
        return self.registry.创建部署记录(
            _现在编号("deployment"),
            version,
            kind="回退",
            from_version=current_version,
            target_count=len(accounts),
            details=details,
        )

    def 执行部署(self, deployment_id: str) -> dict[str, Any]:
        lock_factory = getattr(self.lifecycle, "发布维护锁", None)
        context = lock_factory() if callable(lock_factory) else nullcontext()
        with context:
            return self._执行已锁定部署(deployment_id)

    def _执行已锁定部署(self, deployment_id: str) -> dict[str, Any]:
        record = self.registry.部署记录(deployment_id=deployment_id)
        if record is None or record["status"] != "排队中":
            raise RuntimeError("部署记录不在可执行状态")
        version = str(record["version"])
        kind = str(record["kind"])
        details: dict[str, Any] = dict(record.get("details") or {})
        details["accounts"] = {}
        accounts = self._在用账号()
        target_count = len(accounts)
        success_count = failed_count = 0
        upgraded: list[dict[str, Any]] = []
        previous_default = (self.lifecycle.image, self.lifecycle.runner_image)
        previous_version = str(record.get("from_version") or self._current_version() or "")
        details.setdefault("from_version", previous_version)
        candidate_id = str(details.get("candidate_id") or "")
        previous_platform = ""
        sealed_candidate = ""
        default_changed = False
        activated = False
        host_helper_started = False
        host_switch_pre_platform = False
        platform_changed = False
        promotion_attempted = False
        candidate: dict[str, object] | None = None
        release_artifact: dict[str, object] | None = None
        version_name = str(details.get("version_name") or version)
        failure_phase = "delivery"
        bootstrap = False
        bootstrap_candidate_check: dict[str, Any] = {}

        def update(status: str, stage: str) -> dict[str, Any]:
            _追加阶段(details, status, stage)
            return self.registry.更新部署记录(
                deployment_id,
                status=status,
                stage=stage,
                target_count=target_count,
                success_count=success_count,
                failed_count=failed_count,
                details=details,
            )

        try:
            # Read and statically inspect the fixed candidate before any image,
            # account, platform, or active-version mutation.  This is the only
            # path allowed to cross the legacy single-process boundary.  Never
            # create or replace a host environment from this worker: that is an
            # explicit, owner-approved preflight action.
            approved_runtime = (
                details.get("host_runtime")
                if isinstance(details.get("host_runtime"), dict)
                else None
            )
            if kind == "发布" and isinstance(approved_runtime, dict) and approved_runtime.get("status") == "ready":
                boundary = self.宿主环境状态()
                if boundary.get("mode") == "unknown":
                    raise RuntimeError(
                        "当前正式后台没有完整运行，不能用候选环境绕过；"
                        "请先恢复当前正式版本后台"
                    )
                if boundary.get("private_terms_ready") is False:
                    raise RuntimeError(
                        "正式宿主缺少本机私密扫描词，不能开始发布；请先完成宿主运行面准备"
                    )
                capability = {
                    **dict(boundary),
                    "ready": True,
                    # An approved candidate replaces the Python/runtime, but
                    # it does not erase the fact that the currently running
                    # host is still the legacy single process.  The planner
                    # must therefore take the first-migration path.
                    "migration_required": bool(boundary.get("migration_required")),
                    "candidate_runtime": True,
                }
            elif self._allow_automatic_host_prepare and kind == "发布":
                capability = self._首轮自动准备宿主边界(
                    "开始部署", details, update,
                )
            else:
                capability = self._要求宿主边界已就绪("开始部署")
            if kind == "发布":
                candidate_id = str(details.get("candidate_id") or "")
                candidate = self._read_candidate(candidate_id)
                candidate_record = self.registry.候选记录(candidate_id=candidate_id)
                candidate_details = (
                    candidate_record.get("details")
                    if isinstance(candidate_record, dict)
                    and isinstance(candidate_record.get("details"), dict)
                    else {}
                )
                approved_runtime = candidate_details.get("host_runtime")
                if isinstance(approved_runtime, dict):
                    candidate = {**dict(candidate), "host_runtime": approved_runtime}
                if capability and capability.get("migration_required"):
                    bootstrap_candidate_check = self._核对首轮引导候选(
                        dict(candidate), capability,
                    )
                    bootstrap = True
                    active = capability.get("active") if isinstance(capability.get("active"), dict) else {}
                    details["bootstrap"] = {
                        **dict(details.get("bootstrap") or {}),
                        "kind": "first-three-role-host",
                        "status": "候选已具备三角色入口，等待部署前置验收",
                        "candidate_id": candidate_id,
                        "target_version": version,
                        "from_legacy_version": str(
                            active.get("version") or record.get("from_version") or ""
                        ),
                        "rollback_version": str(
                            active.get("version") or record.get("from_version") or ""
                        ),
                        "candidate_host_check": bootstrap_candidate_check,
                    }
            update("更新账号中", "正在校验已封存的交付物")
            if kind == "发布":
                assert candidate is not None
            if kind == "发布":
                if self._host_boundary_enabled:
                    # A candidate must prove that all three services can start
                    # before images are promoted or a formal copy is sealed.
                    assert self._verify_host_shadow is not None
                    details["host_shadow"] = {"status": "正在验收候选宿主入口"}
                    update("更新账号中", "正在从固定候选源码影子验收三个宿主服务")
                    shadow = self._verify_host_shadow({
                        **dict(candidate),
                        # This pass still boots the fixed candidate source.  The
                        # launcher deliberately accepts the candidate identity
                        # here; the formal version identity is created only
                        # after promotion and sealing below.
                        "version": candidate_id,
                        "version_name": version_name,
                        "shadow_mode": "candidate",
                    })
                    details["host_shadow"] = {**shadow, "status": "passed"}
                    if bootstrap:
                        details["bootstrap"]["status"] = "候选影子宿主已通过，等待正式封存"
                details["formal_images"] = "正在提升候选镜像"
                update("更新账号中", "正在提升候选镜像")
                promotion_attempted = True
                self._promote_images({**candidate, "version": version, "version_name": version_name})
                details["formal_images"] = "候选镜像已按正式版本固定"
                update("更新账号中", "正在封存独立正式版本副本")
                release_artifact = self._seal_version(candidate_id, version, version_name)
                sealed_candidate = candidate_id
            else:
                candidate_id = ""
                self._ensure_images(version)
                release_artifact = self._read_version(version)

            if self._host_boundary_enabled and kind != "发布":
                assert self._verify_host_shadow is not None
                details["host_shadow"] = {"status": "正在验收"}
                update("更新账号中", "正在用封存副本影子验收宿主运行面")
                shadow = self._verify_host_shadow(dict(release_artifact or {}))
                details["host_shadow"] = {**shadow, "status": "passed"}
                if bootstrap:
                    details["bootstrap"]["status"] = "候选影子宿主已通过，等待正式宿主切换"

            previous_platform = self._current_platform()
            details["platform"] = {
                "previous_image": previous_platform,
                "target_image": f"xj-platform:{version}",
                "status": "正在切换",
            }
            # The candidate search gateway performs a startup health check
            # against the host-side reranker on 37657.  During a version
            # change the host must therefore be made ready before Compose
            # starts search-gw; otherwise Compose reports search-gw unhealthy
            # and the whole release rolls back for a self-inflicted reason.
            if self._host_boundary_enabled:
                if self._start_host_switch is None or self._wait_host_switch is None:
                    raise RuntimeError("发布系统没有配置正式宿主就绪等待器")
                failure_phase = "host-switch"
                update("更新账号中", "正在先切换正式宿主，准备本机精排服务")
                if kind == "发布":
                    self._activate_staged_version(version)
                    release_artifact = self._read_version(version)
                else:
                    self._activate_version(version)
                activated = True
                self._开始宿主切换(
                    deployment_id, version, dict(release_artifact or {}), details,
                    bootstrap=bootstrap,
                )
                host_helper_started = True
                host_info = details.get("host_switch")
                if isinstance(host_info, dict):
                    host_info["phase"] = "pre-platform"
                    host_info["orchestrator_pid"] = os.getpid()
                update("更新账号中", "正在等待正式控制、精排和管理服务就绪")
                ready_state = self._wait_host_switch(
                    str(host_info.get("switch_id") if isinstance(host_info, dict) else "")
                )
                if isinstance(host_info, dict):
                    host_info["phase"] = "pre-platform-ready"
                    host_info["ready_at"] = ready_state.get("host_ready_at")
                host_switch_pre_platform = True
                failure_phase = "platform"
                update("更新账号中", "正式宿主已就绪，正在切换共享平台")
            else:
                failure_phase = "platform"
                update("更新账号中", "交付物已核对，正在切换共享平台")
            if kind == "发布":
                platform_result = self._rollout_platform(
                    f"xj-platform:{version}", release=release_artifact,
                )
            else:
                platform_result = self._rollout_platform(f"xj-platform:{version}")
            platform_details = dict(platform_result) if isinstance(platform_result, dict) else {}
            details["platform"] = platform_details
            platform_changed = bool(platform_details.get("changed", True))
            update("更新账号中", "共享平台已健康，准备逐个更新账号")

            target_image = f"xj-company:{version}"
            target_runner = f"xj-runner:{version}"
            details["target"] = {"image": target_image, "runner_image": target_runner}
            failure_phase = "account"
            for account in accounts:
                details["accounts"][account["account_id"]] = {
                    "status": "未更新",
                    "previous_image": account["image_ref"],
                    "previous_runner_image": account["runner_image_ref"],
                }

            for index, account in enumerate(accounts, start=1):
                account_id = account["account_id"]
                details["accounts"][account_id]["status"] = "更新中"
                update("更新账号中", f"正在更新账号 {index}/{target_count}")
                try:
                    result = self.lifecycle.升级版本(
                        account_id,
                        image=target_image,
                        runner_image=target_runner,
                        actor="owner-release" if kind == "发布" else "owner-version-rollback",
                    )
                    success_count += 1
                    upgraded.append(account)
                    details["accounts"][account_id].update({
                        "status": "完成", "backup": result.get("备份", ""),
                    })
                    update("更新账号中", f"账号 {index}/{target_count} 已健康并登记")
                except Exception as exc:
                    details["accounts"][account_id].update({
                        "status": "失败", "error": _错误文字(exc),
                    })
                    state_reader = getattr(self.lifecycle, "实际运行状态", None)
                    recover = getattr(self.lifecycle, "恢复中断版本", None)
                    if callable(state_reader) and callable(recover):
                        try:
                            actual = state_reader(account_id)
                        except Exception:
                            actual = {}
                        previous_healthy = (
                            actual.get("image") == account["image_ref"]
                            and actual.get("running") is True
                            and actual.get("healthy") is True
                        )
                        if not previous_healthy:
                            try:
                                recover(
                                    account_id,
                                    image=account["image_ref"],
                                    runner_image=account["runner_image_ref"],
                                    actor="owner-release-rollback",
                                )
                                verification = self._核对账号已恢复(
                                    account_id,
                                    image=account["image_ref"],
                                    runner_image=account["runner_image_ref"],
                                )
                                if verification:
                                    details.setdefault("account_recovery_failures", {})[account_id] = verification
                                    details["accounts"][account_id].update({
                                        "status": "恢复后核对失败", "error": verification,
                                    })
                                else:
                                    details["accounts"][account_id]["status"] = "失败后已恢复原版本"
                            except Exception as recovery_exc:
                                details.setdefault("account_recovery_failures", {})[account_id] = _错误文字(recovery_exc)
                    details["error"] = _错误文字(exc)
                    raise RuntimeError(f"账号 {account_id} 更新失败") from exc

            failure_phase = "activation"
            update("更新账号中", "正在更新新账号默认版本")
            self.lifecycle.设置默认版本(target_image, target_runner)
            default_changed = True
            if not activated:
                update("更新账号中", "正在写入最终版本锁")
                if kind == "发布":
                    self._activate_staged_version(version)
                else:
                    self._activate_version(version)
                activated = True
            if self._host_boundary_enabled and not host_switch_pre_platform:
                self._开始宿主切换(
                    deployment_id, version, dict(release_artifact or {}), details,
                    bootstrap=bootstrap,
                )
                host_helper_started = True
                return update("更新账号中", "版本已生效，等待三个正式宿主服务切换")
            if self._host_boundary_enabled and host_switch_pre_platform:
                result = self.收口宿主切换()
                if isinstance(result, dict) and result.get("status") == "完成":
                    return result
                return update("更新账号中", "版本已生效，等待三个正式宿主服务收口")
            if kind == "发布":
                candidate_record = self.registry.候选记录(candidate_id=candidate_id)
                candidate_details = dict(candidate_record.get("details") or {}) if candidate_record else {}
                candidate_details["version"] = version
                self.registry.更新候选记录(
                    candidate_id,
                    status="已封存",
                    stage=f"{version_name}已正式发布",
                    details=candidate_details,
                )
            return update("完成", "全部账号已更新，版本已生效")
        except Exception as exc:
            if (
                self._host_boundary_enabled
                and host_helper_started
                and not host_switch_pre_platform
            ):
                host_info = details.get("host_switch")
                switch_id = str(host_info.get("switch_id") or "") if isinstance(host_info, dict) else ""
                if switch_id:
                    try:
                        标记宿主切换失败(switch_id, _错误文字(exc))
                    except Exception:
                        pass
            defer_host_rollback = bool(
                activated
                and self._host_boundary_enabled
                and host_helper_started
                and not host_switch_pre_platform
            )
            if activated and not defer_host_rollback:
                # Activation is provisional until the host switch and its
                # registry reconciliation have completed.  Restore the host
                # descriptions first, then let the common rollback below
                # restore accounts/platform/defaults and the active pointer.
                details["post_activation_error"] = _错误文字(exc)
                failure_phase = "host-switch" if self._host_boundary_enabled else "activation"
                runtime_switch = details.get("host_runtime_switch")
                if (
                    isinstance(runtime_switch, dict)
                    and runtime_switch.get("status") in {"staged", "restore-failed"}
                    and self._restore_host_runtime is not None
                ):
                    try:
                        self._restore_host_runtime(str(runtime_switch.get("switch_id") or ""))
                        runtime_switch["status"] = "restored"
                    except Exception as restore_exc:
                        details.setdefault("transaction_recovery_failures", {})[
                            "host-runtime"
                        ] = _错误文字(restore_exc)
                if previous_version:
                    try:
                        self._activate_version(previous_version)
                        if self._current_version() != previous_version:
                            raise RuntimeError("活动版本锁恢复后仍不是部署前版本")
                        details["active_version_recovery"] = "已恢复部署前版本"
                    except Exception as restore_exc:
                        details.setdefault("transaction_recovery_failures", {})[
                            "active-version"
                        ] = _错误文字(restore_exc)
                elif self._host_boundary_enabled:
                    details.setdefault("transaction_recovery_failures", {})[
                        "active-version"
                    ] = "缺少部署前正式版本，无法安全回退"
            rollback_failures: dict[str, str] = dict(details.get("account_recovery_failures") or {})
            rollback_failures.update(details.get("transaction_recovery_failures") or {})
            if defer_host_rollback:
                rollback_failures["host-switch"] = (
                    "宿主助手已经启动，等待宿主切换结果；本进程不并发回退"
                )
            for previous in reversed(upgraded) if not defer_host_rollback else ():
                account_id = previous["account_id"]
                try:
                    recover = getattr(self.lifecycle, "恢复中断版本", self.lifecycle.升级版本)
                    recover(
                        account_id,
                        image=previous["image_ref"],
                        runner_image=previous["runner_image_ref"],
                        actor="owner-release-rollback",
                    )
                    verification = self._核对账号已恢复(
                        account_id,
                        image=previous["image_ref"],
                        runner_image=previous["runner_image_ref"],
                    )
                    if verification:
                        rollback_failures[account_id] = verification
                        details["accounts"][account_id].update({
                            "status": "回退后核对失败", "error": verification,
                        })
                    else:
                        details["accounts"][account_id]["status"] = "已退回原版本"
                except Exception as rollback_exc:
                    rollback_failures[account_id] = _错误文字(rollback_exc)
                    details["accounts"][account_id].update({
                        "status": "回退失败", "error": _错误文字(rollback_exc),
                    })
            if default_changed and not defer_host_rollback:
                try:
                    self.lifecycle.设置默认版本(*previous_default)
                except Exception as rollback_exc:
                    rollback_failures["new-account-default"] = _错误文字(rollback_exc)
            previous_platform = str(details.get("platform", {}).get("previous_image") or "")
            if previous_platform and not defer_host_rollback:
                try:
                    self._restore_transaction_platform(previous_platform)
                    details["platform"]["status"] = "已退回原版本"
                except Exception as rollback_exc:
                    rollback_failures["shared-platform"] = _错误文字(rollback_exc)
            if rollback_failures:
                self._登记保留产物(details, version)
            elif kind == "发布" and candidate is not None and promotion_attempted:
                try:
                    self._cleanup_images({**candidate, "version": version})
                    details["formal_images"] = "未激活标签已清理"
                except Exception as cleanup_exc:
                    rollback_failures["unactivated-release-images"] = _错误文字(cleanup_exc)
            if not rollback_failures and sealed_candidate:
                try:
                    self._cancel_seal(version, sealed_candidate)
                except Exception as rollback_exc:
                    rollback_failures["unactivated-release-copy"] = _错误文字(rollback_exc)
            if rollback_failures and sealed_candidate:
                self._登记保留产物(details, version)
            if kind == "发布" and candidate_id and not rollback_failures:
                try:
                    candidate_record = self.registry.候选记录(candidate_id=candidate_id)
                    candidate_details = dict(candidate_record.get("details") or {}) if candidate_record else {}
                    candidate_details.pop("version", None)
                    candidate_details["last_failed_deployment"] = deployment_id
                    self.registry.更新候选记录(
                        candidate_id,
                        status="待发布",
                        stage="宿主切换失败，已恢复原正式版本；候选可重新确认发布",
                        details=candidate_details,
                    )
                except Exception as rollback_exc:
                    rollback_failures["candidate-state"] = _错误文字(rollback_exc)
            success_count = 0
            failed_count = target_count
            details.setdefault("error", _错误文字(exc))
            details["rollback_failures"] = rollback_failures
            details["failure"] = {
                "phase": failure_phase,
                "step": "正式部署" if kind == "发布" else "版本回退",
                "summary": str(details.get("error") or _错误文字(exc)),
                "exit_code": None,
                "failed_checks": [
                    f"{name}：{message}" for name, message in rollback_failures.items()
                ],
                "next_action": (
                    "先处理列出的回退或清理失败项，确认运行版本一致后再继续发布。"
                    if rollback_failures
                    else "系统已恢复原版本；查清失败原因后，可以重新确认这个候选。"
                ),
                "account_effect": (
                    "部署未完成；存在未能自动恢复的项目，后续发布已经停止。"
                    if rollback_failures
                    else "部署未完成；已变更的账号和共享平台都已恢复原版本。"
                ),
                "retry_class": "manual" if rollback_failures else "retryable",
            }
            if rollback_failures:
                result = update("失败", "部署失败，部分回退需要人工处理")
            else:
                result = update("失败", "部署失败，所有账号已退回原版本")
            # When the host helper has already reached host-ready, it is the
            # only process allowed to stop the candidate host and restart the
            # sealed fallback.  Signal it after the durable deployment record
            # is written so a process-level rollback cannot lose the failure
            # receipt.
            host_switch_id = ""
            host_switch_status = ""
            host_details = details.get("host_switch") if isinstance(details.get("host_switch"), dict) else {}
            host_switch_id = str(host_details.get("switch_id") or "")
            if self._host_boundary_enabled and host_switch_id:
                try:
                    host_state = self._read_host_switch()
                except Exception:
                    host_state = None
                if isinstance(host_state, dict) and str(host_state.get("switch_id") or "") == host_switch_id:
                    host_switch_status = str(host_state.get("status") or "")
            if host_switch_status not in {"queued", "switching", "host-ready"}:
                host_switch_id = ""
            if host_switch_id:
                try:
                    标记宿主切换失败(
                        host_switch_id,
                        str(details.get("error") or _错误文字(exc)),
                    )
                except Exception as host_exc:
                    # The deployment is already durably failed.  Keep the
                    # original error visible and let startup recovery report
                    # any host-process restoration failure separately.
                    details.setdefault("rollback_failures", {})[
                        "host-process"
                    ] = _错误文字(host_exc)
            return result
