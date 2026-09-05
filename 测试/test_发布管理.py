from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from 多用户.控制面 import 发布管理 as 发布管理模块, 注册表 as 注册表模块
from 多用户.控制面.发布管理 import 发布管理, 宿主环境审批要求
from 多用户.部署.发布仓库 import PRODUCT_VERSION_ID, PRODUCT_VERSION_NAME


_CANDIDATE_ID = "candidate-20260821T120000Z-1234abcd"


class _假注册表:
    def __init__(self) -> None:
        self.rows = [
            {
                "account_id": "acc_a", "状态": "在用",
                "image_ref": "xj-company:dev", "runner_image_ref": "xj-runner:dev",
            },
            {
                "account_id": "acc_b", "状态": "在用",
                "image_ref": "xj-company:dev", "runner_image_ref": "xj-runner:dev",
            },
            {
                "account_id": "acc_stopped", "状态": "停用",
                "image_ref": "xj-company:dev", "runner_image_ref": "xj-runner:dev",
            },
        ]
        self.candidates: dict[str, dict] = {}
        self.deployments: dict[str, dict] = {}

    def 列表(self):
        return [dict(row) for row in self.rows]

    def 创建候选记录(self, candidate_id, notes, *, details=None):
        record = {
            "candidate_id": candidate_id, "notes": notes, "status": "排队中",
            "stage": "等待验收", "details": details or {},
        }
        self.candidates[candidate_id] = record
        return dict(record)

    def 更新候选记录(self, candidate_id, **values):
        self.candidates[candidate_id].update(values)
        return dict(self.candidates[candidate_id])

    def 候选记录(self, *, candidate_id=None):
        if candidate_id:
            value = self.candidates.get(candidate_id)
        else:
            value = list(self.candidates.values())[-1] if self.candidates else None
        return dict(value) if value else None

    def 候选列表(self, *, limit=20):
        return [dict(item) for item in list(self.candidates.values())[-limit:]][::-1]

    def 创建部署记录(
        self, deployment_id, version, *, kind, from_version, target_count, details=None,
    ):
        record = {
            "deployment_id": deployment_id, "version": version, "kind": kind,
            "from_version": from_version, "status": "排队中", "stage": "等待开始",
            "target_count": target_count, "success_count": 0, "failed_count": 0,
            "details": details or {},
        }
        self.deployments[deployment_id] = record
        return dict(record)

    def 更新部署记录(self, deployment_id, **values):
        self.deployments[deployment_id].update(values)
        return dict(self.deployments[deployment_id])

    def 部署记录(self, *, deployment_id=None):
        if deployment_id:
            value = self.deployments.get(deployment_id)
        else:
            value = list(self.deployments.values())[-1] if self.deployments else None
        return dict(value) if value else None

    def 部署列表(self, *, limit=20):
        return [dict(item) for item in list(self.deployments.values())[-limit:]][::-1]

    def 未解决发布恢复(self):
        unresolved = []
        for item in self.deployments.values():
            details = item.get("details") if isinstance(item.get("details"), dict) else {}
            if item.get("status") in {"失败", "已中断"} and (
                details.get("rollback_failures")
                or details.get("retained_unactivated_release")
            ):
                unresolved.append(dict(item))
        return unresolved

    def 内部账户(self, account_id):
        return next(dict(row) for row in self.rows if row["account_id"] == account_id)

    def 中断未完成发布(self, *, 排除部署编号=None):
        excluded = set(排除部署编号 or set())
        count = 0
        for item in self.candidates.values():
            if item["status"] in {"排队中", "验收中", "构建中"}:
                item.update({"status": "已中断", "stage": "管理服务重启，候选制造已停止"})
                count += 1
        for item in self.deployments.values():
            if (
                item["status"] in {"排队中", "更新账号中"}
                and item["deployment_id"] not in excluded
            ):
                item.update({"status": "已中断", "stage": "管理服务重启，部署状态需人工核对"})
                count += 1
        return count

    def 发布记录(self):
        return {"version": "twilight-v1.0-beta.1", "status": "失败", "stage": "旧发布失败"}


class _假生命周期:
    def __init__(self, registry: _假注册表, fail_account: str | None = None) -> None:
        self.registry = registry
        self.calls: list[tuple[str, dict]] = []
        self.defaults: list[tuple[str, str]] = []
        self.fail_account = fail_account
        self.rollback_fail_account: str | None = None
        self.image = "xj-company:dev"
        self.runner_image = "xj-runner:dev"

    def 设置默认版本(self, image, runner_image):
        self.defaults.append((image, runner_image))
        self.image = image
        self.runner_image = runner_image

    def 升级版本(self, account_id, **kwargs):
        self.calls.append((account_id, kwargs))
        if account_id == self.fail_account and kwargs["image"] != "xj-company:dev":
            raise RuntimeError("模拟账号升级失败")
        if (
            account_id == self.rollback_fail_account
            and kwargs.get("actor") in {"owner-release-rollback", "supervisor-release-recovery"}
        ):
            raise RuntimeError("模拟账号回退失败")
        account = next(row for row in self.registry.rows if row["account_id"] == account_id)
        account["image_ref"] = kwargs["image"]
        account["runner_image_ref"] = kwargs["runner_image"]
        return {"account_id": account_id, "备份": f"backup-{account_id}.zip"}


class 发布管理测试(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = _假注册表()
        self.lifecycle = _假生命周期(self.registry)
        self.events: list[str] = []
        self.rollout_releases: list[dict | None] = []
        self.staged_versions: list[dict] = []
        self.versions: list[dict] = []
        self.current: str | None = None
        self.source = {
            "ready": True,
            "reason": "唯一发布源已经提交，可以制造候选",
            "source": {
                "head": "a" * 40, "short_head": "a" * 10,
                "committed_at": "2026-08-21T12:00:00+08:00", "summary": "release source",
            },
        }
        self.manifest = {
            "candidate_id": _CANDIDATE_ID,
            "track": "development",
            "product_version_id": None,
            "product_version_name": "开发版",
            "source": {"head": "a" * 40, "sha256": "b" * 64},
            "data_contract": {
                "schema_version": 1,
                "compatible_from": 1,
                "compatible_to": 1,
                "migration": "none",
            },
            "acceptance": {"status": "passed"},
            "images": {"status": "ready"},
            "path": "/tmp/candidate",
            "build_context": "/tmp/candidate/source",
        }

    def _manager(self, **overrides) -> 发布管理:
        values = {
            "source_status": lambda: self.source,
            "create_candidate": lambda **kwargs: dict(self.manifest),
            "read_candidate": lambda candidate_id: dict(self.manifest),
            "build_candidate": lambda candidate_id, **kwargs: dict(self.manifest),
            "list_versions": lambda **kwargs: [dict(item) for item in self.versions],
            "read_version": lambda version: next(dict(item) for item in self.versions if item["version"] == version),
            "seal_version": self._seal,
            "cancel_seal": self._cancel,
            "discard_candidate": lambda candidate_id, **kwargs: {
                "candidate_id": candidate_id, "reclaimed_bytes": 0,
            },
            "promote_images": lambda release: self.events.append(f"promote:{release['version']}") or {},
            "cleanup_images": lambda release: self.events.append(f"cleanup:{release['version']}"),
            "ensure_images": lambda version: self.events.append(f"ensure:{version}") or False,
            "rollout_platform": self._rollout,
            "restore_platform": lambda image: self.events.append(f"platform-restore:{image}") or {
                "previous_image": "xj-platform:twilight-v1.0-beta", "target_image": image, "changed": True,
            },
            "ensure_platform": lambda version: False,
            "current_platform": lambda: "xj-platform:dev",
            "activate_version": self._switch,
            "activate_staged_version": self._activate,
            "current_version": lambda: self.current,
        }
        values.update(overrides)
        return 发布管理(self.registry, self.lifecycle, **values)

    def _seal(self, candidate_id, version, version_name):
        self.events.append(f"seal:{version}:{candidate_id}")
        value = {
            **self.manifest,
            "version": version,
            "version_name": version_name,
            "staged_at": "2026-08-21T12:59:00+00:00",
            "published_at": None,
            "data_contract": dict(self.manifest["data_contract"]),
        }
        self.staged_versions.append(value)
        return value

    def _cancel(self, version, candidate_id):
        self.events.append(f"cancel:{version}:{candidate_id}")
        self.staged_versions = [
            item for item in self.staged_versions if item.get("version") != version
        ]
        self.versions = [item for item in self.versions if item.get("version") != version]

    def _rollout(self, image, *, release=None):
        self.events.append(f"platform:{image}")
        self.rollout_releases.append(dict(release) if isinstance(release, dict) else None)
        return {
            "previous_image": "xj-platform:dev",
            "target_image": image,
            "changed": True,
        }

    def _activate(self, version):
        self.events.append(f"activate:{version}")
        staged = next(item for item in self.staged_versions if item["version"] == version)
        self.staged_versions = [
            item for item in self.staged_versions if item.get("version") != version
        ]
        self.versions.append({
            **staged,
            "published_at": "2026-08-21T13:00:00+00:00",
        })
        self.current = version
        return {"version": version}

    def _switch(self, version):
        self.events.append(f"activate:{version}")
        self.current = version
        return {"version": version}

    def _ready_candidate(self) -> None:
        self.registry.candidates[_CANDIDATE_ID] = {
            "candidate_id": _CANDIDATE_ID,
            "notes": "待发布",
            "status": "待发布",
            "stage": "全部验收通过",
            "details": {},
        }

    def _pending_unactivated_release(self) -> tuple[str, str]:
        self._ready_candidate()
        version = PRODUCT_VERSION_ID
        self.staged_versions = [{
            **self.manifest,
            "version": version,
            "version_name": PRODUCT_VERSION_NAME,
            "staged_at": "2026-08-21T12:59:00+00:00",
            "published_at": None,
        }]
        target_image = f"xj-company:{version}"
        target_runner = f"xj-runner:{version}"
        self.registry.rows[0]["image_ref"] = target_image
        self.registry.rows[0]["runner_image_ref"] = target_runner
        deployment_id = "deployment-20260821T130000Z-abcdef12"
        details = {
            "candidate_id": _CANDIDATE_ID,
            "version_name": PRODUCT_VERSION_NAME,
            "platform": {
                "previous_image": "xj-platform:dev",
                "target_image": f"xj-platform:{version}",
                "status": "完成",
            },
            "accounts": {
                "acc_a": {
                    "status": "更新中",
                    "previous_image": "xj-company:dev",
                    "previous_runner_image": "xj-runner:dev",
                },
                "acc_b": {
                    "status": "未更新",
                    "previous_image": "xj-company:dev",
                    "previous_runner_image": "xj-runner:dev",
                },
            },
        }
        self.registry.创建部署记录(
            deployment_id,
            version,
            kind="发布",
            from_version=None,
            target_count=2,
            details=details,
        )
        self.registry.更新部署记录(
            deployment_id,
            status="更新账号中",
            stage="更新账号 1/2",
            target_count=2,
            success_count=1,
            failed_count=0,
            details=details,
        )
        return deployment_id, version

    def test_候选失败不触碰账号也不产生版本(self):
        manager = self._manager(
            build_candidate=lambda candidate_id, **kwargs: (_ for _ in ()).throw(RuntimeError("验收失败")),
        )
        with patch.object(发布管理模块, "生成候选编号", return_value=_CANDIDATE_ID):
            candidate = manager.准备候选("第一份候选", self.source["source"]["head"])
        result = manager.执行候选(candidate["candidate_id"])

        self.assertEqual(result["status"], "失败")
        self.assertEqual(self.lifecycle.calls, [])
        self.assertEqual(self.versions, [])
        self.assertNotIn("version", result)

    def test_宿主依赖不匹配时发布前停住且不创建部署(self):
        self._ready_candidate()
        prepared: list[dict] = []
        manager = self._manager(
            verify_host_capability=lambda: (_ for _ in ()).throw(RuntimeError("宿主依赖环境核对失败")),
            read_host_boundary=lambda: {
                "ready": False,
                "requires_approval": True,
                "reason": "宿主依赖清单已变化，需要为候选建立隔离运行环境",
            },
            prepare_candidate_host_runtime=lambda candidate: prepared.append(candidate) or {"status": "ready"},
            allow_automatic_host_prepare=False,
        )
        with self.assertRaises(宿主环境审批要求) as raised:
            manager.准备发布(_CANDIDATE_ID, "暮光v1.1beta版")
        self.assertEqual(raised.exception.details["code"], "host-runtime-approval-required")
        self.assertEqual(self.registry.deployments, {})
        self.assertEqual(prepared, [])

    def test_批准候选只建立隔离环境并完成影子验收(self):
        self._ready_candidate()
        calls: list[dict] = []
        runtime = {"status": "ready", "python": "/tmp/candidate/python3"}
        manager = self._manager(
            verify_host_capability=lambda: {
                "ready": False,
                "requires_approval": True,
                "reason": "宿主依赖清单已变化",
            },
            read_host_boundary=lambda: {
                "ready": False,
                "requires_approval": True,
                "reason": "宿主依赖清单已变化",
            },
            prepare_candidate_host_runtime=lambda candidate: runtime,
            verify_host_shadow=lambda release: calls.append(dict(release)) or {"status": "passed"},
            allow_automatic_host_prepare=False,
        )
        result = manager.批准候选宿主环境(_CANDIDATE_ID)
        self.assertEqual(result, runtime)
        self.assertEqual(calls[0]["shadow_mode"], "candidate")
        self.assertIs(calls[0]["host_runtime"], runtime)
        candidate = self.registry.候选记录(candidate_id=_CANDIDATE_ID)
        self.assertEqual(candidate["status"], "待发布")
        self.assertEqual(candidate["details"]["host_runtime"], runtime)
        self.assertEqual(candidate["details"]["host_shadow"]["status"], "passed")
        self.assertEqual(self.registry.deployments, {})

    def test_候选隔离环境已验收时发布允许进入部署队列(self):
        self._ready_candidate()
        runtime = {"status": "ready", "python": "/tmp/candidate/python3"}
        self.registry.candidates[_CANDIDATE_ID]["details"]["host_runtime"] = runtime
        manager = self._manager(
            verify_host_capability=lambda: (_ for _ in ()).throw(RuntimeError("正式宿主依赖不匹配")),
            read_host_boundary=lambda: {
                "ready": False,
                "requires_approval": True,
                "reason": "正式宿主依赖不匹配",
            },
            allow_automatic_host_prepare=False,
        )
        deployment = manager.准备发布(_CANDIDATE_ID, "暮光v1.1beta版")
        self.assertEqual(deployment["status"], "排队中")
        self.assertEqual(deployment["details"]["host_runtime"], runtime)

    def test_正式发布再次影子验收仍使用候选编号(self):
        self._ready_candidate()
        runtime = {"status": "ready", "python": "/tmp/candidate/python3"}
        self.registry.candidates[_CANDIDATE_ID]["details"]["host_runtime"] = runtime
        calls: list[dict] = []
        manager = self._manager(
            verify_host_capability=lambda: {
                "ready": True,
                "mode": "formal",
                "migration_required": False,
            },
            verify_host_shadow=lambda release: calls.append(dict(release)) or {"status": "passed"},
            plan_host_switch=lambda *args, **kwargs: {"switch_id": "switch-formal"},
            stage_host_runtime=lambda runtime, switch_id, **kwargs: {
                "runtime": runtime,
                "switch_id": switch_id,
                "status": "staged",
            },
            start_host_switch=lambda switch_id: {
                "switch_id": switch_id,
                "status": "queued",
            },
            wait_host_switch=lambda switch_id: {
                "switch_id": switch_id,
                "status": "host-ready",
                "host_ready_at": 1,
            },
        )

        deployment = manager.准备发布(_CANDIDATE_ID, "暮光v1.1beta版")
        result = manager.执行部署(deployment["deployment_id"])

        self.assertEqual(result["status"], "更新账号中")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["version"], _CANDIDATE_ID)
        self.assertEqual(calls[0]["shadow_mode"], "candidate")
        self.assertEqual(calls[0]["host_runtime"], runtime)

    def test_宿主先就绪时记录还在等待哪个发布事实(self):
        self._ready_candidate()
        version = "twilight-release-test"
        self.current = version
        self.versions = [{
            **self.manifest,
            "version": version,
            "version_name": "暮光测试版",
            "published_at": "2026-08-28T00:00:00+00:00",
        }]
        deployment_id = "deployment-20260828T000000Z-wait0001"
        details = {"candidate_id": _CANDIDATE_ID, "version_name": "暮光测试版"}
        self.registry.创建部署记录(
            deployment_id, version, kind="发布", from_version="twilight-v1.0-beta",
            target_count=2, details=details,
        )
        self.registry.更新部署记录(
            deployment_id, status="更新账号中", stage="等待账号切换",
            target_count=2, success_count=0, failed_count=0, details=details,
        )
        state = {
            "switch_id": "switch-wait",
            "deployment_id": deployment_id,
            "status": "host-ready",
            "target": {"version": version, "source_sha256": "b" * 64},
        }
        reasons: list[str] = []
        manager = self._manager(
            verify_host_capability=lambda: {"ready": True},
            read_host_switch=lambda: state,
            current_platform=lambda: f"xj-platform:{version}",
        )
        with patch.object(
            发布管理模块,
            "标记宿主切换等待收口",
            side_effect=lambda switch_id, reason: reasons.append(reason) or {
                **state, "closeout_waiting": reason,
            },
        ):
            result = manager.收口宿主切换()

        self.assertEqual(result["status"], "host-ready")
        self.assertEqual(len(reasons), 1)
        self.assertIn("账号 acc_a", reasons[0])
        self.assertEqual(self.registry.候选记录()["status"], "待发布")

    def test_宿主账号平台全部完成后才标记候选正式发布(self):
        self._ready_candidate()
        version = "twilight-release-complete"
        self.current = version
        self.versions = [{
            **self.manifest,
            "version": version,
            "version_name": "暮光测试版",
            "published_at": "2026-08-28T00:00:00+00:00",
        }]
        for row in self.registry.rows[:2]:
            row["image_ref"] = f"xj-company:{version}"
            row["runner_image_ref"] = f"xj-runner:{version}"
        deployment_id = "deployment-20260828T000000Z-done0001"
        details = {"candidate_id": _CANDIDATE_ID, "version_name": "暮光测试版"}
        self.registry.创建部署记录(
            deployment_id, version, kind="发布", from_version="twilight-v1.0-beta",
            target_count=2, details=details,
        )
        self.registry.更新部署记录(
            deployment_id, status="更新账号中", stage="等待宿主收口",
            target_count=2, success_count=2, failed_count=0, details=details,
        )
        state = {
            "switch_id": "switch-complete",
            "deployment_id": deployment_id,
            "status": "host-ready",
            "target": {"version": version, "source_sha256": "b" * 64},
        }
        closed = {**state, "status": "completed", "completed_at": 100.0}
        manager = self._manager(
            verify_host_capability=lambda: {"ready": True},
            read_host_switch=lambda: state,
            current_platform=lambda: f"xj-platform:{version}",
            verify_host_runtime=lambda target_version, source_sha256: {},
        )
        with patch.object(
            发布管理模块, "标记宿主切换已收口", return_value=closed,
        ):
            result = manager.收口宿主切换()

        self.assertEqual(result["status"], "完成")
        self.assertEqual(self.registry.候选记录()["status"], "已封存")
        self.assertEqual(
            self.registry.候选记录()["stage"], "暮光测试版已正式发布",
        )

    def test_成功发布后可以不重置环境继续发布下一版(self):
        self._ready_candidate()
        platform = {"image": "xj-platform:dev"}
        runtime_transaction: dict = {}
        host_switch: dict = {}
        switch_count = 0

        def rollout(image, *, release=None):
            previous = platform["image"]
            platform["image"] = image
            return {
                "previous_image": previous,
                "target_image": image,
                "changed": previous != image,
            }

        def plan(deployment_id, version, source_sha256, **_kwargs):
            nonlocal switch_count
            switch_count += 1
            host_switch.clear()
            host_switch.update({
                "schema": 1,
                "switch_id": f"switch-{switch_count}",
                "deployment_id": deployment_id,
                "status": "queued",
                "target": {
                    "version": version,
                    "source_sha256": source_sha256,
                },
            })
            return dict(host_switch)

        def stage(runtime, switch_id, **_kwargs):
            if runtime_transaction.get("status") in {
                "staging", "staged", "host-ready",
            }:
                raise RuntimeError("已有宿主运行面切换尚未收口")
            runtime_transaction.clear()
            runtime_transaction.update({
                "schema": 1,
                "switch_id": switch_id,
                "status": "staged",
                "runtime": dict(runtime),
            })
            return dict(runtime_transaction)

        def start(switch_id):
            self.assertEqual(host_switch["switch_id"], switch_id)
            return dict(host_switch)

        def wait(switch_id):
            self.assertEqual(host_switch["switch_id"], switch_id)
            runtime_transaction["status"] = "host-ready"
            host_switch.update({
                "status": "host-ready",
                "host_ready_at": float(switch_count),
                "host_runtime": dict(runtime_transaction),
            })
            return dict(host_switch)

        def close(switch_id):
            self.assertEqual(host_switch["switch_id"], switch_id)
            host_switch.update({
                "status": "completed",
                "completed_at": float(switch_count),
                "host_runtime": dict(runtime_transaction),
            })
            return dict(host_switch)

        def finalize(switch_id):
            self.assertEqual(runtime_transaction["switch_id"], switch_id)
            self.assertEqual(runtime_transaction["status"], "host-ready")
            runtime_transaction["status"] = "committed"
            return dict(runtime_transaction)

        manager = self._manager(
            verify_host_capability=lambda: {
                "ready": True,
                "mode": "formal",
                "migration_required": False,
            },
            verify_host_shadow=lambda release: {"status": "passed"},
            plan_host_switch=plan,
            stage_host_runtime=stage,
            start_host_switch=start,
            wait_host_switch=wait,
            read_host_switch=lambda: dict(host_switch),
            finalize_host_runtime=finalize,
            verify_host_runtime=lambda version, source_sha256: {},
            rollout_platform=rollout,
            current_platform=lambda: platform["image"],
        )

        with patch.object(发布管理模块, "标记宿主切换已收口", side_effect=close):
            first_deployment = manager.准备发布(_CANDIDATE_ID, "暮光连续发布第一版")
            first = manager.执行部署(first_deployment["deployment_id"])
            first_version = first_deployment["version"]

            second_candidate = "candidate-20260901T120000Z-5678efab"
            self.manifest = {**self.manifest, "candidate_id": second_candidate}
            self.registry.candidates[second_candidate] = {
                "candidate_id": second_candidate,
                "notes": "第二版",
                "status": "待发布",
                "stage": "全部验收通过",
                "details": {},
            }
            second_deployment = manager.准备发布(
                second_candidate, "暮光连续发布第二版",
            )
            second = manager.执行部署(second_deployment["deployment_id"])

        self.assertEqual(first["status"], "完成")
        self.assertEqual(second["status"], "完成")
        self.assertEqual(switch_count, 2)
        self.assertEqual(runtime_transaction["status"], "committed")
        self.assertEqual(second_deployment["from_version"], first_version)
        self.assertEqual(self.current, second_deployment["version"])
        self.assertEqual(
            self.registry.候选记录(candidate_id=_CANDIDATE_ID)["status"],
            "已封存",
        )
        self.assertEqual(
            self.registry.候选记录(candidate_id=second_candidate)["status"],
            "已封存",
        )

    def test_宿主运行记录落盘失败时保持待收口并可重试(self):
        self._ready_candidate()
        version = "twilight-release-closeout-retry"
        self.current = version
        self.versions = [{
            **self.manifest,
            "version": version,
            "version_name": "暮光收口重试版",
            "published_at": "2026-09-01T12:00:00+00:00",
        }]
        for row in self.registry.rows[:2]:
            row["image_ref"] = f"xj-company:{version}"
            row["runner_image_ref"] = f"xj-runner:{version}"
        deployment_id = "deployment-20260901T120000Z-retry001"
        details = {"candidate_id": _CANDIDATE_ID, "version_name": "暮光收口重试版"}
        self.registry.创建部署记录(
            deployment_id, version, kind="发布", from_version="twilight-v1.1-beta",
            target_count=2, details=details,
        )
        self.registry.更新部署记录(
            deployment_id, status="更新账号中", stage="等待宿主收口",
            target_count=2, success_count=2, failed_count=0, details=details,
        )
        state = {
            "switch_id": "switch-closeout-retry",
            "deployment_id": deployment_id,
            "status": "host-ready",
            "target": {"version": version, "source_sha256": "b" * 64},
            "host_runtime": {
                "switch_id": "switch-closeout-retry",
                "status": "host-ready",
            },
        }
        closed = {**state, "status": "completed", "completed_at": 100.0}
        attempts = 0

        def finalize(switch_id):
            nonlocal attempts
            attempts += 1
            self.assertEqual(switch_id, "switch-closeout-retry")
            if attempts == 1:
                raise OSError("模拟状态落盘失败")
            return {"switch_id": switch_id, "status": "committed"}

        manager = self._manager(
            verify_host_capability=lambda: {"ready": True},
            read_host_switch=lambda: closed,
            current_platform=lambda: f"xj-platform:{version}",
            verify_host_runtime=lambda target_version, source_sha256: {},
            finalize_host_runtime=finalize,
        )

        first = manager.收口宿主切换()
        self.assertIn("宿主运行记录尚未收口", first["closeout_waiting"])
        self.assertEqual(self.registry.部署记录(deployment_id=deployment_id)["status"], "更新账号中")
        self.assertEqual(self.registry.候选记录()["status"], "待发布")

        second = manager.收口宿主切换()
        self.assertEqual(second["status"], "完成")
        self.assertEqual(attempts, 2)
        self.assertEqual(self.registry.候选记录()["status"], "已封存")

    def test_部署失败整批回退且不封存版本(self):
        self._ready_candidate()
        self.lifecycle.fail_account = "acc_b"
        manager = self._manager()
        deployment = manager.准备发布(_CANDIDATE_ID, "暮光v1.1beta版")
        result = manager.执行部署(deployment["deployment_id"])

        self.assertEqual([item[0] for item in self.lifecycle.calls], ["acc_a", "acc_b", "acc_a"])
        self.assertEqual(self.lifecycle.calls[-1][1]["actor"], "owner-release-rollback")
        self.assertEqual(result["status"], "失败")
        self.assertEqual(self.versions, [])
        self.assertEqual(self.staged_versions, [])
        self.assertTrue(any(event.startswith("seal:") for event in self.events))
        self.assertTrue(any(event.startswith("cancel:") for event in self.events))
        self.assertIsNone(self.current)

    def test_部署回退未完成时保留暂存副本且不进入已发布列表(self):
        self._ready_candidate()
        self.lifecycle.fail_account = "acc_b"
        self.lifecycle.rollback_fail_account = "acc_a"
        manager = self._manager()
        deployment = manager.准备发布(_CANDIDATE_ID, "暮光v1.1beta版")
        version = deployment["version"]

        result = manager.执行部署(deployment["deployment_id"])

        self.assertEqual(result["status"], "失败")
        self.assertIn("acc_a", result["details"]["rollback_failures"])
        self.assertEqual(
            result["details"]["retained_unactivated_release"]["version"],
            version,
        )
        self.assertTrue(any(item["version"] == version for item in self.staged_versions))
        self.assertFalse(any(item["version"] == version for item in self.versions))
        self.assertFalse(any(event.startswith("cleanup:") for event in self.events))
        self.assertFalse(any(event.startswith("cancel:") for event in self.events))

    def test_部署平台回退失败时保留未激活副本和镜像(self):
        self._ready_candidate()
        self.lifecycle.fail_account = "acc_b"
        manager = self._manager(
            restore_platform=lambda image: (_ for _ in ()).throw(RuntimeError("模拟平台回退失败")),
        )
        deployment = manager.准备发布(_CANDIDATE_ID, "暮光v1.1beta版")
        version = deployment["version"]

        result = manager.执行部署(deployment["deployment_id"])

        self.assertEqual(result["status"], "失败")
        self.assertIn("shared-platform", result["details"]["rollback_failures"])
        self.assertEqual(
            result["details"]["retained_unactivated_release"]["version"],
            version,
        )
        self.assertTrue(any(item["version"] == version for item in self.staged_versions))
        self.assertFalse(any(item["version"] == version for item in self.versions))
        self.assertFalse(any(event.startswith("cleanup:") for event in self.events))
        self.assertFalse(any(event.startswith("cancel:") for event in self.events))

    def test_全部账号健康后才封存和激活版本(self):
        self._ready_candidate()
        manager = self._manager()
        deployment = manager.准备发布(_CANDIDATE_ID, "暮光v1.1beta版")
        version = deployment["version"]
        result = manager.执行部署(deployment["deployment_id"])

        self.assertEqual([item[0] for item in self.lifecycle.calls], ["acc_a", "acc_b"])
        self.assertEqual(self.events, [
            f"promote:{version}",
            f"seal:{version}:{_CANDIDATE_ID}",
            f"platform:xj-platform:{version}",
            f"activate:{version}",
        ])
        self.assertEqual(self.rollout_releases[-1]["version"], version)
        self.assertEqual(self.rollout_releases[-1]["candidate_id"], _CANDIDATE_ID)
        self.assertEqual(result["status"], "完成")
        self.assertEqual(self.current, version)
        self.assertEqual(self.staged_versions, [])
        self.assertTrue(any(item["version"] == version for item in self.versions))
        self.assertEqual(self.registry.候选记录()["status"], "已封存")

    def test_最后激活失败会撤销未生效副本并回退账号(self):
        self._ready_candidate()
        manager = self._manager(
            activate_staged_version=lambda version: (_ for _ in ()).throw(RuntimeError("版本锁写入失败")),
        )
        deployment = manager.准备发布(_CANDIDATE_ID, "暮光v1.1beta版")
        version = deployment["version"]
        result = manager.执行部署(deployment["deployment_id"])

        self.assertEqual([item[0] for item in self.lifecycle.calls], ["acc_a", "acc_b", "acc_b", "acc_a"])
        self.assertIn(f"cancel:{version}:{_CANDIDATE_ID}", self.events)
        self.assertEqual(self.lifecycle.defaults[-1], ("xj-company:dev", "xj-runner:dev"))
        self.assertEqual(result["status"], "失败")

    def test_激活后收尾失败也完整回退账号和默认版本(self):
        self._ready_candidate()
        self.current = "twilight-v1.0-beta"
        self.versions = [{
            "version": "twilight-v1.0-beta",
            "version_name": "暮光v1.0beta版",
            "published_at": "2026-08-20T00:00:00+00:00",
            "data_contract": {"schema_version": 1, "compatible_from": 1, "compatible_to": 1},
        }]
        original_update = self.registry.更新候选记录

        def fail_after_activation(candidate_id, **values):
            if values.get("status") == "已封存":
                raise RuntimeError("模拟审计收尾失败")
            return original_update(candidate_id, **values)

        self.registry.更新候选记录 = fail_after_activation
        manager = self._manager()
        deployment = manager.准备发布(_CANDIDATE_ID, "暮光v1.1beta版")
        version = deployment["version"]
        result = manager.执行部署(deployment["deployment_id"])

        self.assertEqual(self.current, "twilight-v1.0-beta")
        self.assertEqual([item[0] for item in self.lifecycle.calls], ["acc_a", "acc_b", "acc_b", "acc_a"])
        self.assertEqual(result["status"], "失败")
        self.assertIn("post_activation_error", result["details"])

    def test_重启后从正式锁恢复默认版本并补齐成功记录(self):
        self._ready_candidate()
        self.current = PRODUCT_VERSION_ID
        self.versions = [{
            **self.manifest,
            "version": PRODUCT_VERSION_ID,
            "version_name": PRODUCT_VERSION_NAME,
            "published_at": "2026-08-21T13:00:00+00:00",
            "data_contract": {"schema_version": 1, "compatible_from": 1, "compatible_to": 1},
        }]
        for row in self.registry.rows[:2]:
            row["image_ref"] = f"xj-company:{PRODUCT_VERSION_ID}"
            row["runner_image_ref"] = f"xj-runner:{PRODUCT_VERSION_ID}"
        deployment = self.registry.创建部署记录(
            "deployment-20260821T130000Z-abcdef12",
            PRODUCT_VERSION_ID,
            kind="发布",
            from_version=None,
            target_count=2,
            details={"candidate_id": _CANDIDATE_ID, "accounts": {"acc_a": {}, "acc_b": {}}},
        )
        self.registry.更新部署记录(
            deployment["deployment_id"], status="更新账号中", stage="准备激活",
            target_count=2, success_count=2, failed_count=0,
            details=deployment["details"],
        )

        result = self._manager().恢复启动状态()

        self.assertEqual(self.lifecycle.defaults[-1], (
            f"xj-company:{PRODUCT_VERSION_ID}", f"xj-runner:{PRODUCT_VERSION_ID}",
        ))
        self.assertEqual(self.registry.候选记录()["status"], "已封存")
        self.assertEqual(self.registry.部署记录()["status"], "完成")
        self.assertEqual(result["recovered_deployments"], [deployment["deployment_id"]])

    def test_正式后台重启时不会误中断仍在运行的发布程序(self):
        self.current = PRODUCT_VERSION_ID
        self.versions = [{
            **self.manifest,
            "version": PRODUCT_VERSION_ID,
            "version_name": PRODUCT_VERSION_NAME,
            "published_at": "2026-08-21T13:00:00+00:00",
        }]
        deployment = self.registry.创建部署记录(
            "deployment-20260828T132841Z-live0001",
            PRODUCT_VERSION_ID,
            kind="发布",
            from_version="twilight-v1.0-beta",
            target_count=2,
            details={
                "host_switch": {
                    "phase": "pre-platform-ready",
                    "orchestrator_pid": os.getpid(),
                },
            },
        )
        self.registry.更新部署记录(
            deployment["deployment_id"],
            status="更新账号中",
            stage="正式宿主已就绪，正在切换共享平台",
            target_count=2,
            success_count=0,
            failed_count=0,
            details=deployment["details"],
        )

        result = self._manager().恢复启动状态()
        current = self.registry.部署记录(deployment_id=deployment["deployment_id"])

        self.assertEqual(current["status"], "更新账号中")
        self.assertEqual(current["stage"], "正式宿主已就绪，正在切换共享平台")
        self.assertNotIn("failure", current["details"])
        self.assertEqual(result["interrupted_operations"], 0)

    def test_正式锁已生效且容器正确时修正登记并二次核对才完成(self):
        self._ready_candidate()
        self.current = PRODUCT_VERSION_ID
        self.versions = [{
            **self.manifest,
            "version": PRODUCT_VERSION_ID,
            "version_name": PRODUCT_VERSION_NAME,
            "published_at": "2026-08-21T13:00:00+00:00",
        }]
        target_image = f"xj-company:{PRODUCT_VERSION_ID}"
        target_runner = f"xj-runner:{PRODUCT_VERSION_ID}"
        self.registry.rows[1]["image_ref"] = target_image
        self.registry.rows[1]["runner_image_ref"] = target_runner
        runtime = {
            "acc_a": {"image": target_image, "running": True, "healthy": True},
            "acc_b": {"image": target_image, "running": True, "healthy": True},
        }
        recoveries: list[tuple[str, str, str, str]] = []
        state_reads: list[str] = []

        def actual_state(account_id):
            state_reads.append(account_id)
            return dict(runtime[account_id])

        def recover(account_id, *, image, runner_image, actor):
            recoveries.append((account_id, image, runner_image, actor))
            account = next(row for row in self.registry.rows if row["account_id"] == account_id)
            account["image_ref"] = image
            account["runner_image_ref"] = runner_image
            return dict(account)

        self.lifecycle.实际运行状态 = actual_state
        self.lifecycle.恢复中断版本 = recover
        deployment = self.registry.创建部署记录(
            "deployment-20260821T130000Z-active01",
            PRODUCT_VERSION_ID,
            kind="发布",
            from_version=None,
            target_count=2,
            details={"candidate_id": _CANDIDATE_ID, "accounts": {"acc_a": {}, "acc_b": {}}},
        )
        self.registry.更新部署记录(
            deployment["deployment_id"],
            status="更新账号中",
            stage="版本锁已写入，等待收尾",
            target_count=2,
            success_count=2,
            failed_count=0,
            details=deployment["details"],
        )

        result = self._manager().恢复启动状态()

        account = self.registry.内部账户("acc_a")
        self.assertEqual((account["image_ref"], account["runner_image_ref"]), (target_image, target_runner))
        self.assertEqual(recoveries, [
            ("acc_a", target_image, target_runner, "supervisor-release-recovery"),
        ])
        self.assertGreaterEqual(state_reads.count("acc_a"), 2)
        self.assertEqual(runtime["acc_a"]["image"], target_image)
        self.assertEqual(self.registry.部署记录(deployment_id=deployment["deployment_id"])["status"], "完成")
        self.assertFalse(result["rollback_failures"])

    def test_公网关闭且没有中断发布时管理后台独立恢复(self):
        self.current = PRODUCT_VERSION_ID
        self.versions = [{
            **self.manifest,
            "version": PRODUCT_VERSION_ID,
            "version_name": PRODUCT_VERSION_NAME,
            "published_at": "2026-08-21T13:00:00+00:00",
            "data_contract": {"schema_version": 1, "compatible_from": 1, "compatible_to": 1},
        }]
        manager = self._manager(
            ensure_images=lambda version: (_ for _ in ()).throw(RuntimeError("Docker 已关闭")),
            ensure_platform=lambda version: (_ for _ in ()).throw(RuntimeError("共享平台未运行")),
        )

        result = manager.恢复启动状态()

        self.assertEqual(self.lifecycle.defaults[-1], (
            f"xj-company:{PRODUCT_VERSION_ID}", f"xj-runner:{PRODUCT_VERSION_ID}",
        ))
        self.assertEqual(result["startup_failures"], {})
        self.assertEqual(result["recovered_deployments"], [])

    def test_启动时管理入口缺失会从当前封存版恢复并二次核对(self):
        self.current = PRODUCT_VERSION_ID
        self.versions = [{
            **self.manifest,
            "version": PRODUCT_VERSION_ID,
            "version_name": PRODUCT_VERSION_NAME,
        }]
        checks: list[tuple[str, str]] = []
        recovered: list[str] = []

        def verify(version, source):
            checks.append((version, source))
            if not recovered:
                raise RuntimeError("admin 正式入口无法访问")
            return {"control": {}, "admin": {}}

        manager = self._manager(
            verify_host_capability=lambda: {"ready": True},
            verify_host_runtime=verify,
            recover_active_host_runtime=lambda: recovered.append("restored") or {
                "status": "restored",
            },
        )

        result = manager.恢复启动状态()

        self.assertEqual(recovered, ["restored"])
        self.assertEqual(len(checks), 2)
        self.assertNotIn("formal-host-runtime", result["startup_failures"])

    def test_重启发生在激活前会把已切换账号退回(self):
        self._ready_candidate()
        target_image = f"xj-company:{PRODUCT_VERSION_ID}"
        target_runner = f"xj-runner:{PRODUCT_VERSION_ID}"
        self.registry.rows[0]["image_ref"] = target_image
        self.registry.rows[0]["runner_image_ref"] = target_runner
        deployment = self.registry.创建部署记录(
            "deployment-20260821T130000Z-abcdef12",
            PRODUCT_VERSION_ID,
            kind="发布",
            from_version=None,
            target_count=2,
            details={
                "candidate_id": _CANDIDATE_ID,
                "accounts": {
                    "acc_a": {
                        "previous_image": "xj-company:dev",
                        "previous_runner_image": "xj-runner:dev",
                    },
                    "acc_b": {
                        "previous_image": "xj-company:dev",
                        "previous_runner_image": "xj-runner:dev",
                    },
                },
            },
        )
        self.registry.更新部署记录(
            deployment["deployment_id"], status="更新账号中", stage="更新账号 1/2",
            target_count=2, success_count=1, failed_count=0,
            details=deployment["details"],
        )

        result = self._manager().恢复启动状态()

        self.assertEqual(len(self.lifecycle.calls), 1)
        self.assertEqual(self.lifecycle.calls[0][0], "acc_a")
        self.assertEqual(self.lifecycle.calls[0][1]["image"], "xj-company:dev")
        self.assertEqual(self.registry.部署记录()["status"], "失败")
        self.assertFalse(result["rollback_failures"])

    def test_宿主失败即使旧后台先写失败也会继续完成清理(self):
        deployment_id, version = self._pending_unactivated_release()
        record = self.registry.部署记录(deployment_id=deployment_id)
        details = dict(record["details"])
        details["host_switch"] = {
            "switch_id": "switch-failed",
            "helper_started": True,
            "status": "failed",
        }
        details["restart_recovery"] = {"complete": False}
        self.registry.更新部署记录(
            deployment_id,
            status="失败",
            stage="旧后台先写入失败",
            target_count=2,
            success_count=0,
            failed_count=2,
            details=details,
        )

        result = self._manager(
            verify_host_capability=lambda: {"ready": True},
            restore_host_runtime=lambda switch_id: {
                "switch_id": switch_id,
                "status": "restored",
            },
            read_host_switch=lambda: {
                "switch_id": "switch-failed",
                "deployment_id": deployment_id,
                "status": "failed",
            },
        ).恢复启动状态()
        recovered = self.registry.部署记录(deployment_id=deployment_id)

        self.assertIn(deployment_id, result["recovered_deployments"])
        self.assertTrue(recovered["details"]["restart_recovery"]["complete"])
        self.assertNotIn("rollback_failures", recovered["details"])
        self.assertIn(f"cancel:{version}:{_CANDIDATE_ID}", self.events)

    def test_历史失败只有助手标记但不是当前切换时不会重新处理(self):
        deployment_id, version = self._pending_unactivated_release()
        record = self.registry.部署记录(deployment_id=deployment_id)
        details = dict(record["details"])
        details["host_switch"] = {
            "switch_id": "switch-old",
            "helper_started": True,
            "status": "failed",
        }
        details["restart_recovery"] = {"complete": False}
        self.registry.更新部署记录(
            deployment_id,
            status="失败",
            stage="历史失败",
            target_count=2,
            success_count=0,
            failed_count=2,
            details=details,
        )
        manager = self._manager(read_host_switch=lambda: {
            "switch_id": "switch-current",
            "deployment_id": "deployment-current",
            "status": "completed",
        })

        result = manager.恢复启动状态()
        recovered = self.registry.部署记录(deployment_id=deployment_id)

        self.assertEqual(result["recovered_deployments"], [])
        self.assertEqual(recovered["stage"], "历史失败")
        self.assertEqual(recovered["details"]["restart_recovery"], {"complete": False})
        self.assertFalse(any(event.startswith("cancel:") for event in self.events))

    def test_启动恢复账号回退失败时保留未激活副本和镜像(self):
        deployment_id, version = self._pending_unactivated_release()
        self.lifecycle.rollback_fail_account = "acc_a"

        result = self._manager().恢复启动状态()
        deployment = self.registry.部署记录(deployment_id=deployment_id)

        self.assertIn("acc_a", result["rollback_failures"][deployment_id])
        self.assertEqual(
            deployment["details"]["retained_unactivated_release"]["version"],
            version,
        )
        self.assertTrue(any(item["version"] == version for item in self.staged_versions))
        self.assertFalse(any(item["version"] == version for item in self.versions))
        self.assertFalse(any(event.startswith("cleanup:") for event in self.events))
        self.assertFalse(any(event.startswith("cancel:") for event in self.events))

    def test_启动恢复平台回退失败时保留未激活副本和镜像(self):
        deployment_id, version = self._pending_unactivated_release()
        manager = self._manager(
            restore_platform=lambda image: (_ for _ in ()).throw(RuntimeError("模拟平台回退失败")),
        )

        result = manager.恢复启动状态()
        deployment = self.registry.部署记录(deployment_id=deployment_id)

        self.assertIn("shared-platform", result["rollback_failures"][deployment_id])
        self.assertEqual(
            deployment["details"]["retained_unactivated_release"]["version"],
            version,
        )
        self.assertTrue(any(item["version"] == version for item in self.staged_versions))
        self.assertFalse(any(item["version"] == version for item in self.versions))
        self.assertFalse(any(event.startswith("cleanup:") for event in self.events))
        self.assertFalse(any(event.startswith("cancel:") for event in self.events))

    def test_任意历史未收口部署都会同时阻断新发布和版本回退(self):
        markers = {
            "回退失败": {"rollback_failures": {"acc_a": "仍未恢复"}},
            "保留救援副本": {
                "retained_unactivated_release": {
                    "version": "twilight-release-old",
                    "reason": "等待人工核对",
                },
            },
        }
        for label, marker in markers.items():
            with self.subTest(marker=label):
                self.registry = _假注册表()
                self.lifecycle = _假生命周期(self.registry)
                self._ready_candidate()
                self.current = PRODUCT_VERSION_ID
                self.versions = [
                    {
                        "version": PRODUCT_VERSION_ID,
                        "version_name": PRODUCT_VERSION_NAME,
                        "data_contract": {
                            "schema_version": 1, "compatible_from": 1, "compatible_to": 1,
                        },
                    },
                    {
                        "version": "twilight-v0.9",
                        "version_name": "暮光v0.9版",
                        "data_contract": {
                            "schema_version": 1, "compatible_from": 1, "compatible_to": 1,
                        },
                    },
                ]
                old = self.registry.创建部署记录(
                    "deployment-20260801T000000Z-old00001",
                    "twilight-release-old",
                    kind="发布",
                    from_version=None,
                    target_count=1,
                    details=dict(marker),
                )
                self.registry.更新部署记录(
                    old["deployment_id"], status="失败", stage="历史部署未收口",
                    target_count=1, success_count=0, failed_count=1, details=dict(marker),
                )
                latest = self.registry.创建部署记录(
                    "deployment-20260822T000000Z-new00001",
                    PRODUCT_VERSION_ID,
                    kind="发布",
                    from_version=None,
                    target_count=1,
                    details={},
                )
                self.registry.更新部署记录(
                    latest["deployment_id"], status="完成", stage="较新的部署已完成",
                    target_count=1, success_count=1, failed_count=0, details={},
                )
                manager = self._manager()

                self.assertEqual(self.registry.部署记录()["status"], "完成")
                with self.assertRaisesRegex(RuntimeError, "没有恢复核清"):
                    manager.准备候选("新的候选", self.source["source"]["head"])
                with self.assertRaisesRegex(RuntimeError, "没有恢复核清"):
                    manager.准备发布(_CANDIDATE_ID, "暮光v1.1beta版")
                with self.assertRaisesRegex(RuntimeError, "没有恢复核清"):
                    manager.准备回退("twilight-v0.9")

    def test_重启恢复成功会清除历史阻断并允许后续发布和回退(self):
        deployment_id, version = self._pending_unactivated_release()
        record = self.registry.部署记录(deployment_id=deployment_id)
        details = dict(record["details"])
        details["rollback_failures"] = {"acc_a": "上次未完成"}
        details["retained_unactivated_release"] = {
            "version": version,
            "reason": "等待重启恢复",
        }
        self.registry.更新部署记录(
            deployment_id,
            status="失败",
            stage="等待重启恢复",
            target_count=2,
            success_count=1,
            failed_count=1,
            details=details,
        )
        self.versions.append({
            "version": "twilight-v0.9",
            "version_name": "暮光v0.9版",
            "data_contract": {"schema_version": 1, "compatible_from": 1, "compatible_to": 1},
        })
        manager = self._manager()
        with self.assertRaisesRegex(RuntimeError, "没有恢复核清"):
            manager.准备发布(_CANDIDATE_ID, "暮光v1.1beta版")

        recovery = manager.恢复启动状态()
        recovered = self.registry.部署记录(deployment_id=deployment_id)
        published = manager.准备发布(_CANDIDATE_ID, "暮光v1.1beta版")
        rollback = manager.准备回退("twilight-v0.9")

        self.assertFalse(recovery["rollback_failures"])
        self.assertNotIn("rollback_failures", recovered["details"])
        self.assertNotIn("retained_unactivated_release", recovered["details"])
        self.assertEqual(self.staged_versions, [])
        self.assertTrue(any(event.startswith("cancel:") for event in self.events))
        self.assertEqual(published["kind"], "发布")
        self.assertEqual(rollback["kind"], "回退")

    def test_回退先校验数据兼容再使用指定版本(self):
        old = {
            "version": "twilight-v0.9",
            "version_name": "暮光v0.9版",
            "published_at": "2026-08-01T00:00:00+00:00",
            "data_contract": {"schema_version": 1, "compatible_from": 1, "compatible_to": 1},
        }
        current = {
            "version": PRODUCT_VERSION_ID,
            "version_name": PRODUCT_VERSION_NAME,
            "published_at": "2026-08-21T00:00:00+00:00",
            "data_contract": {"schema_version": 1, "compatible_from": 1, "compatible_to": 1},
        }
        self.versions = [current, old]
        self.current = PRODUCT_VERSION_ID
        for row in self.registry.rows[:2]:
            row["image_ref"] = f"xj-company:{PRODUCT_VERSION_ID}"
            row["runner_image_ref"] = f"xj-runner:{PRODUCT_VERSION_ID}"
        manager = self._manager()
        deployment = manager.准备回退("twilight-v0.9")
        result = manager.执行部署(deployment["deployment_id"])

        self.assertEqual(self.events, [
            "ensure:twilight-v0.9",
            "platform:xj-platform:twilight-v0.9",
            "activate:twilight-v0.9",
        ])
        self.assertEqual(result["status"], "完成")
        self.assertEqual(self.current, "twilight-v0.9")

    def test_旧版本不兼容当前数据时禁止回退(self):
        self.versions = [
            {
                "version": PRODUCT_VERSION_ID,
                "data_contract": {"schema_version": 2, "compatible_from": 2, "compatible_to": 2},
            },
            {
                "version": "twilight-v0.9",
                "data_contract": {"schema_version": 1, "compatible_from": 1, "compatible_to": 1},
            },
        ]
        self.current = PRODUCT_VERSION_ID
        manager = self._manager()
        with self.assertRaisesRegex(RuntimeError, "不兼容当前用户数据"):
            manager.准备回退("twilight-v0.9")

    def test_需要数据迁移的目标版本禁止回退(self):
        self.versions = [
            {
                "version": PRODUCT_VERSION_ID,
                "data_contract": {
                    "schema_version": 1,
                    "compatible_from": 1,
                    "compatible_to": 1,
                    "migration": "none",
                },
            },
            {
                "version": "twilight-v0.9",
                "data_contract": {
                    "schema_version": 1,
                    "compatible_from": 1,
                    "compatible_to": 1,
                    "migration": "required",
                },
            },
        ]
        self.current = PRODUCT_VERSION_ID
        with self.assertRaisesRegex(RuntimeError, "当前发布器不执行数据迁移"):
            self._manager().准备回退("twilight-v0.9")
        self.assertEqual(self.registry.deployments, {})

    def test_旧失败记录不会改变当前产品版本(self):
        manager = self._manager()
        status = manager.状态()
        self.assertIsNone(status["product_version"])
        self.assertEqual(status["product_version_name"], "开发版")
        self.assertEqual(status["development_version_name"], "开发版")
        self.assertEqual(status["legacy_failure"]["version"], "twilight-v1.0-beta.1")

    def test_已有正式版后仍只生成一个开发版并在发布时命名(self):
        self.current = PRODUCT_VERSION_ID
        self.versions = [{
            "version": PRODUCT_VERSION_ID,
            "version_name": PRODUCT_VERSION_NAME,
            "published_at": "2026-08-21T13:00:00+00:00",
            "data_contract": {
                "schema_version": 1,
                "compatible_from": 1,
                "compatible_to": 1,
                "migration": "none",
            },
        }]
        manager = self._manager()
        candidate = manager.准备候选("下一轮开发版", self.source["source"]["head"])
        self.assertEqual(candidate["details"]["product_version_name"], "开发版")
        self._ready_candidate()
        deployment = manager.准备发布(_CANDIDATE_ID, "暮光v1.1beta版")
        self.assertEqual(deployment["details"]["version_name"], "暮光v1.1beta版")
        self.assertNotEqual(deployment["version"], PRODUCT_VERSION_ID)
        self.assertIsNotNone(注册表模块._产品版本格式.fullmatch(deployment["version"]))

    def test_开发源码变化后旧候选禁止发布(self):
        self._ready_candidate()
        self.source["source"]["head"] = "c" * 40

        with self.assertRaisesRegex(RuntimeError, "开发版在候选生成后已经变化"):
            self._manager().准备发布(_CANDIDATE_ID, "暮光v1.1beta版")

        self.assertEqual(self.registry.deployments, {})
        self.assertEqual(self.lifecycle.calls, [])

    def test_生成新候选会收口源码变化的旧候选(self):
        next_candidate_id = "candidate-20260827T120000Z-5678abcd"
        manager = self._manager()
        with patch.object(
            发布管理模块,
            "生成候选编号",
            side_effect=[_CANDIDATE_ID, next_candidate_id],
        ):
            first = manager.准备候选("第一份候选", self.source["source"]["head"])
            manager.执行候选(first["candidate_id"])
            self.registry.candidates[_CANDIDATE_ID]["details"]["source"] = {
                "head": "a" * 40,
            }
            self.source["source"]["head"] = "c" * 40
            second = manager.准备候选("第二份候选", self.source["source"]["head"])

        old = self.registry.候选记录(candidate_id=_CANDIDATE_ID)
        self.assertEqual(old["status"], "已中断")
        self.assertEqual(old["stage"], "开发版已变化，旧候选已过期，未触碰任何账号")
        self.assertEqual(second["status"], "排队中")

    def test_需要数据迁移的候选在触碰账号前被阻止(self):
        self._ready_candidate()
        self.manifest["data_contract"] = {
            "schema_version": 2,
            "compatible_from": 1,
            "compatible_to": 2,
            "migration": "required",
        }

        with self.assertRaisesRegex(RuntimeError, "当前发布器不执行数据迁移"):
            self._manager().准备发布(_CANDIDATE_ID, "暮光v2.0版")

        self.assertEqual(self.registry.deployments, {})
        self.assertEqual(self.lifecycle.calls, [])

    def test_候选时间线合并重复心跳且终态只登记一次(self):
        def build_candidate(candidate_id, *, on_stage):
            on_stage("验收中", "完整验收仍在运行")
            on_stage("验收中", "完整验收仍在运行")
            return dict(self.manifest)

        manager = self._manager(build_candidate=build_candidate)
        with patch.object(发布管理模块, "生成候选编号", return_value=_CANDIDATE_ID):
            manager.准备候选("时间线测试", self.source["source"]["head"])

        result = manager.执行候选(_CANDIDATE_ID)
        with self.assertRaisesRegex(RuntimeError, "不在可执行状态"):
            manager.执行候选(_CANDIDATE_ID)

        timeline = result["details"]["timeline"]
        repeated = [
            item for item in timeline
            if (item.get("status"), item.get("stage")) == ("验收中", "完整验收仍在运行")
        ]
        terminal = [item for item in timeline if item.get("status") == "待发布"]
        self.assertEqual(len(repeated), 1)
        self.assertEqual(len(terminal), 1)
        self.assertEqual(timeline[-1]["stage"], "全部验收通过，等待确认发布")


if __name__ == "__main__":
    unittest.main(verbosity=2)
