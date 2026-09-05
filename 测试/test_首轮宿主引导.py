from __future__ import annotations

import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path

from 多用户.控制面.发布管理 import 发布管理


_ACCOUNT = {
    "account_id": "acc_test",
    "状态": "在用",
    "image_ref": "xj-company:dev",
    "runner_image_ref": "xj-runner:dev",
}


class _注册表:
    def __init__(self, record: dict, candidate: dict):
        self.record = record
        self.candidate = candidate
        self.updates: list[dict] = []
        self.candidate_updates: list[dict] = []

    def 部署记录(self, *, deployment_id: str):
        return self.record if deployment_id == self.record["deployment_id"] else None

    def 列表(self):
        return [_ACCOUNT.copy()]

    def 更新部署记录(self, deployment_id: str, **values):
        self.record.update(values)
        self.updates.append(dict(values))
        return dict(self.record)

    def 候选记录(self, *, candidate_id: str):
        return self.candidate if candidate_id == self.candidate["candidate_id"] else None

    def 更新候选记录(self, candidate_id: str, **values):
        self.candidate.update(values)
        self.candidate_updates.append(dict(values))
        return dict(self.candidate)


class _生命周期:
    def __init__(self, events: list[str]):
        self.events = events
        self.image = "xj-company:dev"
        self.runner_image = "xj-runner:dev"

    def 发布维护锁(self):
        return nullcontext()

    def 升级版本(self, account_id: str, *, image: str, runner_image: str, actor: str):
        self.events.append("account")
        return {"备份": "backup.zip", "account_id": account_id, "image_ref": image}

    def 设置默认版本(self, image: str, runner_image: str):
        self.events.append("default")
        self.image = image
        self.runner_image = runner_image


class _施工:
    def __init__(
        self,
        root: Path,
        *,
        legacy: bool,
        bootstrap_check=None,
        shadow_error=None,
        boundary_states: list[dict] | None = None,
        strict_states: list[dict] | None = None,
        host_error: BaseException | None = None,
        platform_error: BaseException | None = None,
    ):
        self.events: list[str] = []
        self.plans: list[str] = []
        self.root = root
        self.candidate_path = root / "candidate"
        (self.candidate_path / "source").mkdir(parents=True)
        self.capability = {
            "ready": True,
            "mode": "legacy" if legacy else "formal",
            "migration_required": legacy,
            "active": {
                "version": "legacy-version",
                "source_sha256": "a" * 64,
                "release_path": str(root / "published" / "legacy-version"),
            },
        }
        self.bootstrap_check = bootstrap_check or {
            "ready": True,
            "source": str(self.candidate_path / "source"),
            "roles": {
                role: {"present": True} for role in ("control", "admin", "rerank")
            },
            "missing": [],
        }
        self.shadow_error = shadow_error
        self.host_error = host_error
        self.platform_error = platform_error
        self.boundary_states = [dict(item) for item in (boundary_states or [])]
        self.strict_states = [dict(item) for item in (strict_states or [])]
        self.candidate = {
            "candidate_id": "candidate-test",
            "path": str(self.candidate_path),
            "build_context": str(self.candidate_path / "source"),
            "source": {"sha256": "b" * 64},
            "acceptance": {"status": "passed"},
            "images": {"status": "ready"},
        }
        self.release_artifact = {
            **self.candidate,
            "version": "release-test",
            "version_name": "测试正式版",
            "source": {"sha256": "b" * 64},
        }
        self.registry = _注册表(
            {
                "deployment_id": "deployment-test",
                "version": "release-test",
                "kind": "发布",
                "from_version": "legacy-version",
                "status": "排队中",
                "target_count": 1,
                "success_count": 0,
                "failed_count": 0,
                "details": {
                    "candidate_id": "candidate-test",
                    "version_name": "测试正式版",
                },
            },
            self.candidate,
        )
        self.lifecycle = _生命周期(self.events)

    def 读候选(self, candidate_id: str):
        self.events.append("read-candidate")
        return dict(self.candidate)

    def 读正式(self, version: str):
        return dict(self.release_artifact)

    def 核对宿主能力(self):
        self.events.append("capability")
        if self.strict_states:
            state = self.strict_states.pop(0) if len(self.strict_states) > 1 else self.strict_states[0]
            return dict(state)
        return dict(self.capability)

    def 读取宿主边界(self):
        self.events.append("boundary-read")
        if not self.boundary_states:
            return dict(self.capability)
        state = self.boundary_states.pop(0) if len(self.boundary_states) > 1 else self.boundary_states[0]
        return dict(state)

    def 准备宿主边界(self):
        self.events.append("prepare-boundary")
        return {"ready": True, "prepared": True}

    def 核对首轮(self, candidate: dict):
        self.events.append("entry-check")
        return dict(self.bootstrap_check)

    def 影子(self, release: dict):
        self.events.append("shadow")
        if self.shadow_error:
            raise self.shadow_error
        return {"ready": True, "roles": ["control", "admin", "rerank"]}

    def 提升(self, release: dict):
        self.events.append("promote")
        return {"company": "xj-company:release-test"}

    def 封存(self, candidate_id: str, version: str, version_name: str):
        self.events.append("seal")
        return dict(self.release_artifact)

    def 平台(self, image: str, **kwargs):
        self.events.append("platform")
        if self.platform_error:
            raise self.platform_error
        return {"changed": True, "image": image}

    def 当前平台(self):
        return "xj-platform:legacy-version"

    def 激活(self, version: str):
        self.events.append("active")
        return {"version": version}

    def 计划引导(self, deployment_id: str, version: str, source_sha256: str):
        self.events.append("bootstrap-plan")
        self.plans.append("bootstrap")
        return {"switch_id": "switch-bootstrap"}

    def 计划正式(self, deployment_id: str, version: str, source_sha256: str):
        self.events.append("formal-plan")
        self.plans.append("formal")
        return {"switch_id": "switch-formal"}

    def 启动切换(self, switch_id: str):
        self.events.append("start-switch")
        return {"switch_id": switch_id, "status": "queued", "stage": "queued"}

    def 等待切换(self, switch_id: str):
        self.events.append("host-ready")
        if self.host_error:
            raise self.host_error
        return {"switch_id": switch_id, "status": "host-ready", "host_ready_at": 1}

    def 管理器(
        self,
        *,
        include_bootstrap: bool = True,
        include_auto_prepare: bool = False,
    ) -> 发布管理:
        return 发布管理(
            self.registry,
            self.lifecycle,
            read_candidate=self.读候选,
            read_version=self.读正式,
            promote_images=self.提升,
            seal_version=self.封存,
            rollout_platform=self.平台,
            current_platform=self.当前平台,
            activate_version=self.激活,
            activate_staged_version=self.激活,
            prepare_host_boundary=self.准备宿主边界 if include_auto_prepare else None,
            read_host_boundary=self.读取宿主边界 if include_auto_prepare else None,
            verify_host_capability=self.核对宿主能力,
            verify_host_shadow=self.影子,
            verify_bootstrap_candidate=self.核对首轮 if include_bootstrap else None,
            plan_host_switch=self.计划正式,
            plan_host_bootstrap_switch=self.计划引导,
            start_host_switch=self.启动切换,
            wait_host_switch=self.等待切换,
            stage_host_runtime=lambda runtime, switch_id, **kwargs: {
                "status": "staged", "switch_id": switch_id,
            },
            restore_host_runtime=lambda switch_id: {
                "status": "restored", "switch_id": switch_id,
            },
        )


class 首轮宿主引导测试(unittest.TestCase):
    def test_legacy仅缺运行文件时只准备一次并严格复核后继续(self):
        with tempfile.TemporaryDirectory() as td:
            legacy = {
                "ready": False,
                "mode": "legacy",
                "migration_required": True,
                "reason": "三个正式宿主启动描述尚未准备",
                "active": {"version": "legacy-version"},
            }
            prepared = {
                **legacy,
                "ready": True,
                "reason": "",
            }
            fixture = _施工(
                Path(td),
                legacy=True,
                boundary_states=[legacy, prepared],
                strict_states=[prepared],
            )
            result = fixture.管理器(include_auto_prepare=True).执行部署("deployment-test")

        self.assertEqual(result["status"], "更新账号中")
        self.assertEqual(fixture.events.count("prepare-boundary"), 1)
        self.assertEqual(
            fixture.events[:7],
            [
                "boundary-read",
                "prepare-boundary",
                "boundary-read",
                "capability",
                "read-candidate",
                "entry-check",
                "shadow",
            ],
        )
        self.assertLess(fixture.events.index("shadow"), fixture.events.index("promote"))

    def test_legacy不可准备或准备后仍未就绪时不碰不可逆操作(self):
        scenarios = (
            (
                "不可准备原因",
                [{
                    "ready": False,
                    "mode": "legacy",
                    "migration_required": True,
                    "reason": "活动版本身份损坏",
                }],
                0,
            ),
            (
                "准备后仍未就绪",
                [
                    {
                        "ready": False,
                        "mode": "legacy",
                        "migration_required": True,
                        "reason": "独立宿主 Python 环境尚未准备",
                    },
                    {
                        "ready": False,
                        "mode": "legacy",
                        "migration_required": True,
                        "reason": "独立宿主 Python 环境仍不可用",
                    },
                ],
                1,
            ),
        )
        irreversible = {"shadow", "promote", "seal", "platform", "account", "active"}

        for label, boundary_states, expected_prepare_count in scenarios:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as td:
                fixture = _施工(
                    Path(td),
                    legacy=True,
                    boundary_states=boundary_states,
                )
                result = fixture.管理器(include_auto_prepare=True).执行部署("deployment-test")

                self.assertEqual(result["status"], "失败")
                self.assertEqual(
                    fixture.events.count("prepare-boundary"),
                    expected_prepare_count,
                )
                self.assertFalse(set(fixture.events) & irreversible)

    def test_legacy首轮引导影子在不可逆操作前并走引导计划(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = _施工(Path(td), legacy=True)
            result = fixture.管理器().执行部署("deployment-test")

        self.assertEqual(result["status"], "更新账号中")
        self.assertEqual(fixture.plans, ["bootstrap"])
        self.assertEqual(fixture.events[:4], ["capability", "read-candidate", "entry-check", "shadow"])
        self.assertLess(fixture.events.index("shadow"), fixture.events.index("promote"))
        self.assertLess(fixture.events.index("promote"), fixture.events.index("account"))
        self.assertLess(fixture.events.index("active"), fixture.events.index("account"))
        self.assertLess(fixture.events.index("host-ready"), fixture.events.index("platform"))
        self.assertLess(fixture.events.index("platform"), fixture.events.index("account"))
        self.assertEqual(fixture.registry.record["details"]["bootstrap"]["kind"], "first-three-role-host")
        self.assertEqual(fixture.registry.record["details"]["host_switch"]["mode"], "legacy-migration")

    def test_正式宿主未就绪时不启动共享平台或更新账号(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = _施工(Path(td), legacy=True, host_error=RuntimeError("宿主未就绪"))
            result = fixture.管理器().执行部署("deployment-test")

        self.assertEqual(result["status"], "失败")
        self.assertIn("host-ready", fixture.events)
        self.assertNotIn("platform", fixture.events)
        self.assertNotIn("account", fixture.events)

    def test_共享平台失败时回退已先行就绪的宿主(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = _施工(Path(td), legacy=True, platform_error=RuntimeError("平台启动失败"))
            result = fixture.管理器().执行部署("deployment-test")

        self.assertEqual(result["status"], "失败")
        self.assertLess(fixture.events.index("active"), fixture.events.index("platform"))
        self.assertNotIn("account", fixture.events)
        self.assertEqual(fixture.events.count("active"), 2)

    def test_legacy缺入口在影子和不可逆操作前拒绝(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = _施工(
                Path(td),
                legacy=True,
                bootstrap_check={"ready": False, "missing": ["rerank"], "roles": {}},
            )
            result = fixture.管理器().执行部署("deployment-test")

        self.assertEqual(result["status"], "失败")
        self.assertEqual(fixture.events[:3], ["capability", "read-candidate", "entry-check"])
        self.assertNotIn("shadow", fixture.events)
        self.assertFalse(set(fixture.events) & {"promote", "seal", "platform", "account", "active", "bootstrap-plan"})
        self.assertEqual(fixture.registry.record["details"]["failure"]["phase"], "delivery")

    def test_legacy影子失败不触碰镜像账号和活动锁(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = _施工(Path(td), legacy=True, shadow_error=RuntimeError("影子不健康"))
            result = fixture.管理器().执行部署("deployment-test")

        self.assertEqual(result["status"], "失败")
        self.assertEqual(fixture.events[:4], ["capability", "read-candidate", "entry-check", "shadow"])
        self.assertFalse(set(fixture.events) & {"promote", "seal", "platform", "account", "active", "bootstrap-plan"})
        self.assertEqual(fixture.registry.record["details"]["failure"]["phase"], "delivery")

    def test_formal后续发布不走bootstrap旁路(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = _施工(Path(td), legacy=False)
            result = fixture.管理器().执行部署("deployment-test")

        self.assertEqual(result["status"], "更新账号中")
        self.assertEqual(fixture.plans, ["formal"])
        self.assertNotIn("entry-check", fixture.events)
        self.assertNotIn("bootstrap", fixture.registry.record["details"])
        self.assertEqual(fixture.registry.record["details"]["host_switch"]["mode"], "formal-switch")

    def test_候选运行环境不能绕过正式后台缺失(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = _施工(Path(td), legacy=False)
            fixture.capability = {
                "ready": False,
                "mode": "unknown",
                "migration_required": False,
                "private_terms_ready": True,
                "reason": "没有找到正在运行的正式后台",
            }
            fixture.registry.record["details"]["host_runtime"] = {
                "status": "ready",
                "python": "candidate-python",
            }
            result = fixture.管理器().执行部署("deployment-test")

        self.assertEqual(result["status"], "失败")
        self.assertIn("不能用候选环境绕过", result["details"]["failure"]["summary"])
        self.assertFalse(
            set(fixture.events) & {
                "shadow", "promote", "seal", "platform", "account", "active",
                "formal-plan", "bootstrap-plan",
            }
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
