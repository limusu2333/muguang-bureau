from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from 多用户.部署 import 发布仓库
from 多用户.部署.发布仓库 import (
    创建候选,
    候选空间报告,
    发布仓库错误,
    封存正式版本,
    已发布版本列表,
    当前已发布版本,
    撤销未激活封存,
    废弃候选大文件,
    待发布产品版本,
    激活暂存版本,
    更新候选,
    读取候选,
    读取暂存版本,
    读取正式版本,
)
from 多用户.部署.开发源 import 开发源错误, 登记唯一开发源
from 多用户.控制面.注册表 import 账户注册表


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True, check=True,
    )
    return result.stdout.strip()


class 发布仓库测试(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.store = self.root / "store"
        self.source.mkdir()
        _git(self.source, "init", "-q")
        _git(self.source, "config", "user.name", "Release Test")
        _git(self.source, "config", "user.email", "release@example.invalid")
        (self.source / "工具").mkdir()
        (self.source / "工具" / "公司.py").write_text("VERSION = 1\n", encoding="utf-8")
        deploy = self.source / "多用户" / "部署"
        deploy.mkdir(parents=True)
        (deploy / "数据契约.json").write_text(
            "{\n"
            '  "schema_version": 1,\n'
            '  "compatible_from": 1,\n'
            '  "compatible_to": 1,\n'
            '  "migration": "none"\n'
            "}\n",
            encoding="utf-8",
        )
        (self.source / "README.md").write_text("稳定版\n", encoding="utf-8")
        _git(self.source, "add", ".")
        _git(self.source, "commit", "-qm", "release source")
        self.head = _git(self.source, "rev-parse", "HEAD")
        self.candidate_id = "candidate-20260821T120000Z-1234abcd"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _create(self) -> dict:
        return 创建候选(
            source_root=self.source,
            store_root=self.store,
            expected_head=self.head,
            candidate_id=self.candidate_id,
            notes="完整验收",
        )

    def _mark_ready(self) -> None:
        candidate = 读取候选(self.candidate_id, store_root=self.store)
        archive = Path(candidate["path"]) / "images.tar"
        archive.write_bytes(b"sealed-images")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        log = Path(candidate["path"]) / "release.log"
        log.write_text("发布环境预检通过\n全部验收通过\n", encoding="utf-8")
        log_digest = hashlib.sha256(log.read_bytes()).hexdigest()
        components = {
            name: {
                "id": "sha256:" + marker * 64,
                "candidate_tag": f"xj-{name}:candidate-test",
            }
            for name, marker in (("company", "a"), ("runner", "b"), ("platform", "c"))
        }
        external = {
            name: {
                "source_ref": source_ref,
                "id": "sha256:" + marker * 64,
                "repo_digests": [source_ref.split(":", 1)[0] + "@sha256:" + marker * 64],
                "os": "linux",
                "architecture": "arm64",
                "size_bytes": 1024,
            }
            for (name, source_ref), marker in zip(
                发布仓库._外部镜像来源.items(), ("d", "e", "f"), strict=True,
            )
        }
        runtime_checks = [
            {
                "code": code,
                "status": "passed",
                "kind": "one-shot-runtime" if code == "runner-entry" else "service-entry",
            }
            for code in sorted(发布仓库._运行时检查编号)
        ]
        更新候选(
            self.candidate_id,
            {
                "acceptance": {
                    "status": "passed",
                    "runner": "多用户.部署.候选验收",
                    "log": {
                        "file": "release.log",
                        "sha256": log_digest,
                        "bytes": log.stat().st_size,
                        "complete": True,
                    },
                },
                "images": {
                    "status": "ready",
                    "archive": "images.tar",
                    "archive_sha256": digest,
                    "components": components,
                    "external": external,
                    "runtime_checks": runtime_checks,
                    "runtime_checked": True,
                    "archive_restored": True,
                },
            },
            store_root=self.store,
        )

    def test_schema2候选和正式副本都强制验证供应链与运行时证据(self) -> None:
        self._create()
        self._mark_ready()
        candidate = 读取候选(self.candidate_id, store_root=self.store)
        manifest_path = Path(candidate["path"]) / "manifest.json"
        original = json.loads(manifest_path.read_text(encoding="utf-8"))

        mutations = (
            lambda images: images.pop("external"),
            lambda images: images["external"].pop("cloudflared"),
            lambda images: images["runtime_checks"].pop(),
            lambda images: images["runtime_checks"][0].update(status="failed"),
            lambda images: images.update(runtime_checked=False),
            lambda images: images.update(archive_restored=False),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                changed = json.loads(json.dumps(original))
                mutate(changed["images"])
                发布仓库._写JSON(manifest_path, changed)
                with self.assertRaises(发布仓库错误):
                    读取候选(self.candidate_id, store_root=self.store)
                发布仓库._写JSON(manifest_path, original)

        version = "twilight-v1.0-beta"
        staged = 封存正式版本(
            self.candidate_id, version, "暮光v1.1beta版", store_root=self.store,
        )
        staged_manifest = Path(staged["path"]) / "manifest.json"
        changed = json.loads(staged_manifest.read_text(encoding="utf-8"))
        changed["images"].pop("runtime_checked")
        发布仓库._写JSON(staged_manifest, changed)
        with self.assertRaises(发布仓库错误):
            读取暂存版本(version, store_root=self.store)

    def test_JSON原子落盘失败保留旧锁和旧清单(self) -> None:
        for relative in ("store/manifest.json", "runtime/active-release.json"):
            with self.subTest(relative=relative):
                path = self.root / relative
                发布仓库._写JSON(path, {"state": "old"})
                before = path.read_bytes()
                with patch.object(发布仓库.os, "replace", side_effect=OSError("injected")):
                    with self.assertRaises(OSError):
                        发布仓库._写JSON(path, {"state": "new"})
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
                self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_JSON原子落盘同步文件与目录(self) -> None:
        path = self.root / "atomic" / "manifest.json"
        real_fsync = os.fsync
        calls: list[int] = []

        def fsync(descriptor: int) -> None:
            calls.append(descriptor)
            real_fsync(descriptor)

        with patch.object(发布仓库.os, "fsync", side_effect=fsync):
            发布仓库._写JSON(path, {"ok": True})

        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ok": True})

    def test_候选只来自固定提交且不修改发布源(self) -> None:
        before = _git(self.source, "status", "--porcelain")
        candidate = self._create()

        self.assertEqual(candidate["source"]["head"], self.head)
        self.assertEqual(candidate["schema"], 2)
        self.assertEqual(candidate["data_contract"], {
            "schema_version": 1,
            "compatible_from": 1,
            "compatible_to": 1,
            "migration": "none",
        })
        self.assertEqual(Path(candidate["build_context"], "README.md").read_text(), "稳定版\n")
        self.assertEqual(_git(self.source, "status", "--porcelain"), before)
        self.assertFalse((Path(candidate["build_context"]) / ".git").exists())

    def test_本机唯一登记是默认候选的唯一入口(self) -> None:
        development = self.root / "development"
        development.mkdir()
        _git(development, "init", "-q")
        _git(development, "config", "user.name", "Development Test")
        _git(development, "config", "user.email", "development@example.invalid")
        _git(development, "checkout", "-qb", "agentscope-migration")
        (development / ".xj-development-source.json").write_text(
            json.dumps({
                "schema": 1,
                "kind": "xiaojiu-canonical-development-source",
                "source_id": "xiaojiu-primary-development",
                "canonical_branch": "agentscope-migration",
            }),
            encoding="utf-8",
        )
        (development / "README.md").write_text("唯一开发区\n", encoding="utf-8")
        deploy = development / "多用户" / "部署"
        deploy.mkdir(parents=True)
        (deploy / "数据契约.json").write_text(
            '{"schema_version":1,"compatible_from":1,"compatible_to":1,"migration":"none"}\n',
            encoding="utf-8",
        )
        _git(development, "add", ".")
        _git(development, "commit", "-qm", "canonical development")
        runtime = self.root / "runtime"
        record = runtime / "data" / "development-source.json"
        登记唯一开发源(
            development, record_path=record, release_store=self.store,
        )
        first_record = json.loads(record.read_text(encoding="utf-8"))
        (development / "README.md").write_text("唯一开发区继续向前\n", encoding="utf-8")
        _git(development, "add", "README.md")
        _git(development, "commit", "-qm", "advance canonical development")
        latest_head = _git(development, "rev-parse", "HEAD")
        登记唯一开发源(
            development, record_path=record, release_store=self.store,
        )
        refreshed_record = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(refreshed_record["head"], latest_head)
        self.assertEqual(refreshed_record["registered_at"], first_record["registered_at"])
        with (
            patch.object(发布仓库, "RUNTIME", runtime),
            patch.dict(os.environ, {"XJ_RELEASE_STORE": str(self.store)}, clear=True),
        ):
            state = 发布仓库.发布源状态()
        self.assertTrue(state["ready"])
        self.assertEqual(state["source"]["root"], str(development.resolve()))
        self.assertEqual(state["source"]["branch"], "agentscope-migration")

        _git(development, "checkout", "-qb", "wrong-development-line")
        with self.assertRaisesRegex(开发源错误, "唯一开发线"):
            登记唯一开发源(
                development, record_path=self.root / "wrong.json", release_store=self.store,
            )

    def test_运行数据被跟踪时禁止候选(self) -> None:
        example = self.source / ".env.example"
        example.write_text("SAFE_TEMPLATE=\n", encoding="utf-8")
        _git(self.source, "add", ".env.example")
        _git(self.source, "commit", "-qm", "safe environment template")
        self.assertTrue(发布仓库.发布源状态(source_root=self.source)["ready"])

        for relative in ("output/browser.log", ".env"):
            with self.subTest(relative=relative):
                generated = self.source / relative
                generated.parent.mkdir(parents=True, exist_ok=True)
                generated.write_text("不得发布\n", encoding="utf-8")
                _git(self.source, "add", "-f", relative)
                _git(self.source, "commit", "-qm", f"bad runtime data: {relative}")
                state = 发布仓库.发布源状态(source_root=self.source)
                self.assertFalse(state["ready"])
                self.assertIn(relative, state["reason"])
                _git(self.source, "rm", "-q", "--", relative)
                _git(self.source, "commit", "-qm", f"remove runtime data: {relative}")

    def test_未提交内容和提交变化都会阻止制造(self) -> None:
        (self.source / "untracked.txt").write_text("不得混入\n", encoding="utf-8")
        with self.assertRaisesRegex(发布仓库错误, "尚未提交"):
            self._create()
        (self.source / "untracked.txt").unlink()
        with self.assertRaisesRegex(发布仓库错误, "提交已经变化"):
            创建候选(
                source_root=self.source,
                store_root=self.store,
                expected_head="f" * 40,
                candidate_id=self.candidate_id,
            )

    def test_候选源码或镜像归档被改后禁止发布(self) -> None:
        candidate = self._create()
        company = Path(candidate["build_context"]) / "工具" / "公司.py"
        self.assertEqual(stat.S_IMODE(company.stat().st_mode), 0o444)
        company.chmod(0o644)  # 模拟磁盘上的副本被绕过权限后篡改
        company.write_text("VERSION = 2\n", encoding="utf-8")
        with self.assertRaisesRegex(发布仓库错误, "源码校验失败"):
            读取候选(self.candidate_id, store_root=self.store)

        company.write_text("VERSION = 1\n", encoding="utf-8")
        self._mark_ready()
        (Path(candidate["path"]) / "images.tar").write_bytes(b"tampered")
        with self.assertRaisesRegex(发布仓库错误, "镜像归档校验失败"):
            读取候选(self.candidate_id, store_root=self.store)

    def test_schema2候选的完整验收日志受哈希保护(self) -> None:
        candidate = self._create()
        self._mark_ready()
        log = Path(candidate["path"]) / "release.log"

        ready = 读取候选(self.candidate_id, store_root=self.store)
        self.assertTrue(ready["acceptance"]["log"]["complete"])
        self.assertEqual(
            ready["acceptance"]["log"]["sha256"],
            hashlib.sha256(log.read_bytes()).hexdigest(),
        )

        log.write_text("日志被改写\n", encoding="utf-8")
        with self.assertRaisesRegex(发布仓库错误, "验收日志(?:大小不匹配|校验失败)"):
            读取候选(self.candidate_id, store_root=self.store)

    def test_封存只进入预发布区且不占正式版本名称(self) -> None:
        candidate = self._create()
        self._mark_ready()
        version = "twilight-v1.0-beta"
        staged = 封存正式版本(self.candidate_id, version, "暮光v1.1beta版", store_root=self.store)
        lock = self.root / "current.json"

        self.assertTrue(Path(candidate["path"]).is_dir())
        self.assertEqual(Path(staged["path"]).parent.name, "staged")
        self.assertTrue(Path(staged["path"], "source", "README.md").is_file())
        self.assertTrue(Path(staged["path"], "release.log").is_file())
        self.assertEqual(staged["schema"], 2)
        self.assertIsNone(staged["published_at"])
        self.assertIsNone(当前已发布版本(lock))
        self.assertEqual(已发布版本列表(store_root=self.store), [])
        self.assertEqual(待发布产品版本(store_root=self.store), version)
        with self.assertRaises(发布仓库错误):
            读取正式版本(version, store_root=self.store)

        Path(candidate["path"], "images.tar").write_bytes(b"candidate-changed")
        self.assertEqual(读取暂存版本(version, store_root=self.store)["version"], version)

    def test_成功激活才原子移入正式区并写版本锁(self) -> None:
        self._create()
        self._mark_ready()
        version = "twilight-v1.0-beta"
        staged = 封存正式版本(
            self.candidate_id, version, "暮光v1.1beta版", store_root=self.store,
        )
        staged_path = Path(staged["path"])
        lock = self.root / "current.json"

        result = 激活暂存版本(version, store_root=self.store, lock_path=lock)
        published = 读取正式版本(version, store_root=self.store)

        self.assertEqual(result["version"], version)
        self.assertEqual(当前已发布版本(lock), version)
        self.assertFalse(staged_path.exists())
        self.assertEqual(Path(published["path"]).parent.name, "published")
        self.assertTrue(published["published_at"])
        self.assertEqual(
            stat.S_IMODE(Path(published["path"], "manifest.json").stat().st_mode), 0o444,
        )
        self.assertEqual(
            stat.S_IMODE(Path(published["build_context"]).stat().st_mode), 0o555,
        )
        self.assertEqual([
            item["version"] for item in 已发布版本列表(store_root=self.store)
        ], [version])
        with self.assertRaises(发布仓库错误):
            读取暂存版本(version, store_root=self.store)

    def test_激活写锁失败会把副本退回预发布区(self) -> None:
        executable = self.source / "start.sh"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
        _git(self.source, "add", "start.sh")
        _git(self.source, "commit", "-qm", "add executable")
        self.head = _git(self.source, "rev-parse", "HEAD")
        self._create()
        self._mark_ready()
        version = "twilight-v1.0-beta"
        封存正式版本(
            self.candidate_id, version, "暮光v1.1beta版", store_root=self.store,
        )
        invalid_lock = self.root / "lock-is-directory"
        invalid_lock.mkdir()

        with self.assertRaises(OSError):
            激活暂存版本(version, store_root=self.store, lock_path=invalid_lock)

        staged = 读取暂存版本(version, store_root=self.store)
        self.assertIsNone(staged["published_at"])
        self.assertEqual(Path(staged["path"]).parent.name, "staged")
        self.assertTrue(Path(staged["build_context"], "start.sh").stat().st_mode & 0o111)
        self.assertEqual(已发布版本列表(store_root=self.store), [])
        with self.assertRaises(发布仓库错误):
            读取正式版本(version, store_root=self.store)

    def test_未验收候选不能封存(self) -> None:
        self._create()
        with self.assertRaisesRegex(发布仓库错误, "尚未完成验收"):
            封存正式版本(
                self.candidate_id,
                "twilight-v1.0-beta",
                "暮光v1.0beta版",
                store_root=self.store,
            )

    def test_过期候选只清理大归档并保留追查材料(self) -> None:
        candidate = self._create()
        self._mark_ready()
        archive = Path(candidate["path"]) / "images.tar"
        before = archive.stat().st_size
        result = 废弃候选大文件(
            self.candidate_id, store_root=self.store, allow_ready=True,
        )
        self.assertFalse(archive.exists())
        self.assertTrue(Path(candidate["build_context"]).is_dir())
        self.assertTrue((Path(candidate["path"]) / "release.log").is_file())
        self.assertGreaterEqual(result["reclaimed_bytes"], before)
        compacted = 读取候选(self.candidate_id, store_root=self.store)
        self.assertEqual(compacted["images"]["status"], "discarded")
        report = 候选空间报告(store_root=self.store)
        self.assertEqual(report["candidates"][0]["archive_bytes"], 0)

    def test_撤销未激活封存只删除预发布副本(self) -> None:
        candidate = self._create()
        self._mark_ready()
        version = "twilight-v1.0-beta"
        staged = 封存正式版本(self.candidate_id, version, "暮光v1.1beta版", store_root=self.store)

        撤销未激活封存(
            version,
            self.candidate_id,
            store_root=self.store,
            lock_path=self.root / "current.json",
        )

        self.assertFalse(Path(staged["path"]).exists())
        self.assertTrue(Path(candidate["path"]).is_dir())
        self.assertEqual(读取候选(self.candidate_id, store_root=self.store)["candidate_id"], self.candidate_id)

    def test_候选账本和部署账本彼此分开(self) -> None:
        registry = 账户注册表(self.root / "registry.sqlite3")
        candidate = registry.创建候选记录(
            self.candidate_id,
            "候选说明",
            details={"source": {"head": self.head}},
        )
        self.assertEqual(candidate["status"], "排队中")
        registry.更新候选记录(
            self.candidate_id,
            status="待发布",
            stage="全部验收通过",
            details={"source": {"head": self.head}},
        )
        deployment_id = "deployment-20260821T130000Z-abcdef12"
        deployment = registry.创建部署记录(
            deployment_id,
            "twilight-v1.0-beta",
            kind="发布",
            from_version=None,
            target_count=1,
            details={"candidate_id": self.candidate_id},
        )
        self.assertEqual(deployment["status"], "排队中")
        registry.更新部署记录(
            deployment_id,
            status="失败",
            stage="模拟部署失败",
            target_count=1,
            success_count=0,
            failed_count=1,
            details={"candidate_id": self.candidate_id},
        )

        self.assertEqual(registry.候选记录()["status"], "待发布")
        self.assertEqual(registry.部署记录()["status"], "失败")
        self.assertEqual(len(registry.候选列表()), 1)
        self.assertEqual(len(registry.部署列表()), 1)

    def test_历史未收口部署不会被最新成功记录遮住(self) -> None:
        registry = 账户注册表(self.root / "registry.sqlite3")
        unresolved = (
            (
                "deployment-20260801T000000Z-aaaa0001",
                {"rollback_failures": {"acc_a": "仍未恢复"}},
            ),
            (
                "deployment-20260802T000000Z-bbbb0002",
                {
                    "retained_unactivated_release": {
                        "version": "twilight-release-old",
                        "reason": "等待人工核对",
                    },
                },
            ),
        )
        for deployment_id, details in unresolved:
            registry.创建部署记录(
                deployment_id,
                "twilight-v1.0-beta",
                kind="发布",
                from_version=None,
                target_count=1,
                details=details,
            )
            registry.更新部署记录(
                deployment_id,
                status="失败",
                stage="历史部署未收口",
                target_count=1,
                success_count=0,
                failed_count=1,
                details=details,
            )
        latest_id = "deployment-20260822T000000Z-cccc0003"
        registry.创建部署记录(
            latest_id,
            "twilight-v1.0-beta",
            kind="发布",
            from_version=None,
            target_count=1,
            details={},
        )
        registry.更新部署记录(
            latest_id,
            status="完成",
            stage="最新部署完成",
            target_count=1,
            success_count=1,
            failed_count=0,
            details={},
        )

        self.assertEqual(registry.部署记录()["deployment_id"], latest_id)
        self.assertEqual(
            [item["deployment_id"] for item in registry.未解决发布恢复()],
            [item[0] for item in unresolved],
        )

    def test_重启中断候选只写一次终态时间线和结构化失败(self) -> None:
        registry = 账户注册表(self.root / "registry.sqlite3")
        registry.创建候选记录(
            self.candidate_id,
            "未完成",
            details={"timeline": [{"status": "排队中", "stage": "等待验收"}]},
        )
        self.assertEqual(registry.中断未完成发布(), 1)
        self.assertEqual(registry.中断未完成发布(), 0)

        record = registry.候选记录(candidate_id=self.candidate_id)
        self.assertEqual(record["status"], "已中断")
        self.assertTrue(record["completed_at"])
        terminal = [
            item for item in record["details"]["timeline"]
            if item.get("status") == "已中断"
        ]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0]["stage"], record["stage"])
        failure = record["details"]["failure"]
        self.assertEqual(set(failure), {
            "phase", "step", "summary", "exit_code", "failed_checks",
            "next_action", "account_effect", "retry_class",
        })
        self.assertEqual(failure["step"], "候选制造")
        self.assertEqual(failure["retry_class"], "environment")

    def test_重启中断部署只写一次终态时间线和结构化失败(self) -> None:
        registry = 账户注册表(self.root / "registry.sqlite3")
        deployment_id = "deployment-20260821T130000Z-abcdef12"
        registry.创建部署记录(
            deployment_id,
            "twilight-v1.0-beta",
            kind="发布",
            from_version=None,
            target_count=2,
            details={"timeline": [{"status": "排队中", "stage": "等待开始"}]},
        )
        self.assertEqual(registry.中断未完成发布(), 1)
        self.assertEqual(registry.中断未完成发布(), 0)

        record = registry.部署记录(deployment_id=deployment_id)
        self.assertEqual(record["status"], "已中断")
        self.assertTrue(record["completed_at"])
        terminal = [
            item for item in record["details"]["timeline"]
            if item.get("status") == "已中断"
        ]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0]["stage"], record["stage"])
        failure = record["details"]["failure"]
        self.assertEqual(set(failure), {
            "phase", "step", "summary", "exit_code", "failed_checks",
            "next_action", "account_effect", "retry_class",
        })
        self.assertEqual(failure["step"], "正式部署")
        self.assertEqual(failure["retry_class"], "manual")

    def test_重启不会中断明确排除的在途部署(self) -> None:
        registry = 账户注册表(self.root / "registry.sqlite3")
        protected_id = "deployment-20260828T132841Z-abcd0001"
        registry.创建部署记录(
            protected_id,
            "twilight-v1.1-beta",
            kind="发布",
            from_version="twilight-v1.0-beta",
            target_count=1,
            details={},
        )

        self.assertEqual(
            registry.中断未完成发布(排除部署编号={protected_id}),
            0,
        )
        self.assertEqual(
            registry.部署记录(deployment_id=protected_id)["status"],
            "排队中",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
