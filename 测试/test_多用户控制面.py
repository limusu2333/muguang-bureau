from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from 多用户.控制面.Docker资源 import Docker错误, Docker资源
from 多用户.控制面.注册表 import 注册表错误, 账户注册表
from 多用户.渲染制度 import 渲染


工具目录 = Path(__file__).resolve().parents[1] / "工具"
if str(工具目录) not in sys.path:
    sys.path.insert(0, str(工具目录))
备份模块 = importlib.import_module("备份")
生命周期模块 = importlib.import_module("多用户.控制面.生命周期")
生命周期 = 生命周期模块.生命周期
控制服务模块 = importlib.import_module("多用户.控制面.服务")
账户ID = "acc_" + "0" * 26
实例ID = "inst_" + "1" * 26
主人ID = "own_" + "2" * 26


class _假注册表:
    def __init__(self) -> None:
        self.record = None
        self.pending = []
        self.audits = []
        self.account = None
        self.states = []

    def 开始开通(self, record):
        self.pending = [dict(record)]
        return dict(record)

    def 待恢复开通(self):
        return [dict(record) for record in self.pending]

    def 取消未完成开通(self, account_id, *, actor="supervisor", details=None):
        self.pending = [record for record in self.pending if record["account_id"] != account_id]
        self.audits.append({
            "actor": actor, "action": "provision-rollback", "account_id": account_id,
            "result": "rollback", "details": details or {},
        })

    def 完成开通(self, record, *, control_token):
        assert self.pending and self.pending[0] == record
        self.record = dict(record)
        self.control_token = control_token
        self.pending = []
        return {"account_id": record["account_id"], "状态": "待绑定"}

    def 记审计(self, **kwargs):
        self.audits.append(kwargs)

    def 内部账户(self, account_id):
        assert account_id == self.account["account_id"]
        return dict(self.account)

    def 置状态(self, account_id, status, *, action):
        self.states.append((account_id, status, action))
        return {"account_id": account_id, "状态": status}

    def 更新用户资料(self, account_id, *, display_name, call_name):
        assert account_id == self.account["account_id"]
        self.account["display_name"] = display_name
        self.account["call_name"] = call_name
        self.audits.append({"action": "update-profile", "result": "ok"})
        return {"account_id": account_id, "状态": self.account["status"]}

    def 状态(self, account_id):
        assert account_id == self.account["account_id"]
        return {
            "account_id": account_id, "状态": self.account["status"],
            "image_ref": self.account["image_ref"],
            "runner_image_ref": self.account["runner_image_ref"],
        }

    def 更新版本(self, account_id, *, old_image_ref, image_ref, runner_image_ref, actor="owner-cli"):
        assert account_id == self.account["account_id"]
        assert old_image_ref == self.account["image_ref"]
        self.audits.append({"actor": actor, "action": "upgrade-version", "result": "ok"})
        self.account["image_ref"] = image_ref
        self.account["runner_image_ref"] = runner_image_ref
        return self.状态(account_id)


class _假平台:
    def __init__(self, prefix: str, events: list[str]) -> None:
        self.prefix = prefix
        self.events = events

    def 签发(self, account_id, **limits):
        self.events.append(f"{self.prefix}:issue")
        return limits["key"] if self.prefix == "chat" else limits["token"]

    def 置阻断(self, account_or_key, blocked):
        self.events.append(f"{self.prefix}:{'block' if blocked else 'unblock'}")

    def 撤销(self, account_or_key, **_kwargs):
        self.events.append(f"{self.prefix}:revoke")

    def 撤销别名(self, account_id):
        self.events.append(f"{self.prefix}:revoke-alias")


class _假Docker:
    def __init__(self, events: list[str], fail_at: str = "") -> None:
        self.events = events
        self.fail_at = fail_at

    @staticmethod
    def 名称(account_id):
        slug = account_id.lower()
        return {
            "container": f"company-{slug}", "network": f"net-{slug}",
            "data_volume": f"data-{slug}", "work_volume": f"work-{slug}",
        }

    def 创建网络(self, _name):
        self.events.append("docker:create-network")

    def 删除网络(self, _name, *, strict=False):
        self.events.append(f"docker:delete-network:{strict}")

    def 创建卷(self, *_args):
        self.events.append("docker:create-volumes")
        if self.fail_at == "volumes":
            raise Docker错误("模拟卷初始化失败")

    def 删除卷(self, *_args, strict=False):
        self.events.append(f"docker:delete-volumes:{strict}")

    def 渲染制度(self, *, config, output, **_kwargs):
        self.events.append("docker:render-policy")
        output.mkdir()
        raw = yaml.safe_load(config.read_text(encoding="utf-8"))
        (output / "README.md").write_text(
            f"{raw['owner']['称呼']}\n{raw['公司目的']}\n", encoding="utf-8",
        )

    def 创建并启动实例(self, **kwargs):
        self.events.append("docker:start-company")
        if self.fail_at == "upgrade-start" and ":twilight-v" in kwargs.get("image", ""):
            raise Docker错误("模拟新版启动失败")

    def 删除容器(self, _name, *, strict=False):
        self.events.append(f"docker:delete-container:{strict}")

    def 重载(self, _name):
        self.events.append("docker:reload-company")

    def 停止(self, _name):
        self.events.append("docker:stop-company")

    def 创建用户备份(self, _name):
        self.events.append("docker:backup-company")
        return "/data/备份/升级前.zip"


def _新生命周期(root: Path, registry, docker, events):
    lifecycle = 生命周期.__new__(生命周期)
    lifecycle.registry = registry
    lifecycle.docker = docker
    lifecycle.litellm = _假平台("chat", events)
    lifecycle.search = _假平台("search", events)
    lifecycle.instance_root = root / "instance"
    lifecycle.policy_root = root / "policy"
    lifecycle.instance_root.mkdir(exist_ok=True)
    lifecycle.policy_root.mkdir(exist_ok=True)
    lifecycle.private_terms = root / "private-terms.txt"
    lifecycle.private_terms.write_text("私密词\n", encoding="utf-8")
    lifecycle.image = "company@test"
    lifecycle.runner_image = "runner@test"
    return lifecycle


class 控制面测试(unittest.TestCase):
    def test_未完成开户暂时清不掉也不会拖垮控制服务(self):
        class Registry:
            def __init__(self, _path):
                pass

            def 待恢复开通(self):
                return [{"account_id": 账户ID}]

        class BrokenLifecycle:
            def __init__(self, _registry):
                pass

            def 恢复未完成开通(self):
                raise RuntimeError("Docker 暂不可用")

        class Auth:
            def __init__(self, _path):
                pass

        async def scenario():
            with (
                patch.object(控制服务模块, "账户注册表", Registry),
                patch.object(控制服务模块, "账户认证", Auth),
                patch.object(控制服务模块, "生命周期", BrokenLifecycle),
                patch.object(控制服务模块, "一次性执行器", lambda: object()),
                patch.dict(
                    控制服务模块.os.environ,
                    {
                        "XJ_SUPERVISOR_TOKEN": "t" * 32,
                        "XJ_REGISTRY_PATH": str(Path(tempfile.gettempdir()) / "unused.sqlite3"),
                    },
                    clear=False,
                ),
            ):
                async with 控制服务模块.lifespan(控制服务模块.app):
                    state = 控制服务模块.app.state.provision_recovery
                    response = await 控制服务模块.healthz(
                        SimpleNamespace(app=控制服务模块.app)
                    )
            return state, response

        state, response = asyncio.run(scenario())
        self.assertEqual(state["status"], "pending")
        self.assertEqual(state["pending"], 1)
        self.assertTrue(response["ok"])
        self.assertEqual(response["provision_recovery"], state)

    @staticmethod
    def _准备升级账户(root: Path, registry: _假注册表) -> None:
        account_dir = root / "instance" / 账户ID
        policy_dir = root / "policy" / 账户ID
        account_dir.mkdir(parents=True)
        policy_dir.mkdir(parents=True)
        config_path = account_dir / "instance.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "account_id": 账户ID, "instance_id": 实例ID,
                    "owner": {"owner_id": 主人ID, "display_name": "张三", "称呼": "张先生"},
                    "公司目的": "完成工作",
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        (policy_dir / "README.md").write_text("旧制度\n", encoding="utf-8")
        registry.account = {
            "account_id": 账户ID, "status": "在用", "instance_id": 实例ID, "owner_id": 主人ID,
            "instance_config_path": str(config_path),
            "chat_credential_ref": "chat-ref", "search_credential_ref": "search-ref",
            "image_ref": "xj-company:dev", "runner_image_ref": "xj-runner:dev",
        }

    def test_升级先备份并在健康后登记新版本(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events: list[str] = []
            registry = _假注册表()
            self._准备升级账户(root, registry)
            lifecycle = _新生命周期(root, registry, _假Docker(events), events)
            with patch.object(生命周期模块, "读取实例", side_effect=["chat-key", "search-token"]):
                result = lifecycle.升级版本(
                    账户ID,
                    image="xj-company:twilight-v1.0-beta.2",
                    runner_image="xj-runner:twilight-v1.0-beta.2",
                    actor="owner-release",
                )
            self.assertEqual(result["image_ref"], "xj-company:twilight-v1.0-beta.2")
            self.assertEqual(result["备份"], "/data/备份/升级前.zip")
            self.assertEqual(registry.audits[-1]["actor"], "owner-release")
            self.assertLess(events.index("docker:backup-company"), events.index("docker:stop-company"))
            self.assertEqual((root / "policy" / 账户ID / "README.md").read_text(encoding="utf-8"), "张先生\n完成工作\n")

    def test_新版启动失败会恢复旧容器和旧制度(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events: list[str] = []
            registry = _假注册表()
            self._准备升级账户(root, registry)
            lifecycle = _新生命周期(root, registry, _假Docker(events, "upgrade-start"), events)
            lifecycle.image = "xj-company:twilight-v1.0-beta.2"
            lifecycle.runner_image = "xj-runner:twilight-v1.0-beta.2"
            with patch.object(生命周期模块, "读取实例", side_effect=["chat-key", "search-token"]):
                with self.assertRaisesRegex(Docker错误, "模拟新版启动失败"):
                    lifecycle.升级版本(账户ID)
            self.assertEqual(registry.account["image_ref"], "xj-company:dev")
            self.assertEqual(events.count("docker:start-company"), 2)
            self.assertEqual((root / "policy" / 账户ID / "README.md").read_text(encoding="utf-8"), "旧制度\n")

    def test_整批发布失败时允许退回开发版(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events: list[str] = []
            registry = _假注册表()
            self._准备升级账户(root, registry)
            registry.account["image_ref"] = "xj-company:twilight-v1.0-beta.1"
            registry.account["runner_image_ref"] = "xj-runner:twilight-v1.0-beta.1"
            lifecycle = _新生命周期(root, registry, _假Docker(events), events)
            with patch.object(生命周期模块, "读取实例", side_effect=["chat-key", "search-token"]):
                result = lifecycle.升级版本(
                    账户ID,
                    image="xj-company:dev",
                    runner_image="xj-runner:dev",
                    actor="owner-release-rollback",
                )
            self.assertEqual(result["image_ref"], "xj-company:dev")
            self.assertEqual(result["runner_image_ref"], "xj-runner:dev")
            self.assertEqual(registry.audits[-1]["actor"], "owner-release-rollback")

    def test_中断恢复登记成功后旧制度清理失败只记警告不反向回滚(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events: list[str] = []
            starts: list[dict] = []
            registry = _假注册表()
            self._准备升级账户(root, registry)
            docker = _假Docker(events)
            original_start = docker.创建并启动实例

            def start(**kwargs):
                starts.append(dict(kwargs))
                return original_start(**kwargs)

            docker.创建并启动实例 = start
            lifecycle = _新生命周期(root, registry, docker, events)
            target_image = "xj-company:twilight-v1.0-beta.2"
            target_runner = "xj-runner:twilight-v1.0-beta.2"

            with (
                patch.object(生命周期模块, "读取实例", side_effect=["chat-key", "search-token"]),
                patch.object(
                    生命周期模块,
                    "_安全删除",
                    side_effect=RuntimeError("模拟旧制度副本清理失败"),
                ),
            ):
                result = lifecycle.恢复中断版本(
                    账户ID,
                    image=target_image,
                    runner_image=target_runner,
                    actor="supervisor-release-recovery",
                )

            self.assertEqual(result["image_ref"], target_image)
            self.assertEqual(result["runner_image_ref"], target_runner)
            self.assertEqual(registry.account["image_ref"], target_image)
            self.assertEqual(registry.account["runner_image_ref"], target_runner)
            self.assertEqual(len(starts), 1)
            self.assertEqual(starts[0]["image"], target_image)
            self.assertEqual(events.count("docker:delete-container:True"), 1)
            self.assertNotIn("docker:delete-container:False", events)
            self.assertEqual(
                (root / "policy" / 账户ID / "README.md").read_text(encoding="utf-8"),
                "张先生\n完成工作\n",
            )
            cleanup_audits = [
                item for item in registry.audits
                if item.get("action") == "release-recovery-cleanup"
            ]
            self.assertEqual(len(cleanup_audits), 1)
            self.assertEqual(cleanup_audits[0]["result"], "partial")
            self.assertEqual(cleanup_audits[0]["details"]["error"], "RuntimeError")
            self.assertFalse([
                item for item in registry.audits
                if item.get("action") == "release-recovery" and item.get("result") in {"failed", "partial"}
            ])

    def test_远程DeepSeek岗位也必须走内部模型代理(self):
        model_layer = importlib.import_module("升级_模型层")
        config = {
            "provider": "openai", "model": "deepseek-v4-pro-0813",
            "key_env": "BAILIAN_TOKEN_PLAN_API_KEY", "base_url": "http://model-proxy:4000/v1",
        }
        with patch.object(model_layer, "_配置", return_value=(config, "sk-instance-test")), patch.object(
            model_layer, "是远程实例", return_value=True
        ), patch.object(
            model_layer, "OpenAIChatModel"
        ) as openai_model, patch.object(model_layer, "DeepSeekChatModel") as deepseek_model:
            model_layer.建模型("测试工程师")
        openai_model.assert_called_once()
        deepseek_model.assert_not_called()

    def test_通用制度可渲染且不含本机私人信息(self):
        code = Path(__file__).resolve().parents[1]
        source = code / "多用户" / "通用制度"
        config = code / "多用户" / "config" / "instance.example.yaml"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            terms_file = root / "terms.txt"
            terms_file.write_text("候选测试私密词\n", encoding="utf-8")
            terms = [line.strip() for line in terms_file.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
            output = root / "policy"
            result = 渲染(source, output, config, terms_file)
            self.assertGreater(result["文件数"], 10)
            self.assertTrue((output / "岗位" / "项目经理" / "说明书.md").is_file())
            self.assertTrue((output / "技能" / "通用" / "通用_工作质量门.md").is_file())
            rendered = "\n".join(path.read_text(encoding="utf-8") for path in output.rglob("*") if path.is_file())
            self.assertNotIn("{{", rendered)
            self.assertFalse([term for term in terms if term in rendered])

    def test_用户字段带入私人词时渲染仍会拒绝(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            source.mkdir()
            (source / "README.md").write_text("主人：{{主人称呼}}\n", encoding="utf-8")
            config = root / "instance.yaml"
            config.write_text(
                yaml.safe_dump(
                    {"owner": {"称呼": "本机禁止词", "display_name": "用户", "别名": []}, "公司目的": "完成工作"},
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
            terms = root / "terms.txt"
            terms.write_text("本机禁止词\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "渲染产物含私人信息"):
                渲染(source, root / "output", config, terms)

    def test_身份首次绑定后不允许偷换_sub(self):
        with tempfile.TemporaryDirectory() as td:
            registry = 账户注册表(Path(td) / "registry.sqlite3")
            record = {
                "account_id": 账户ID, "email": "USER@example.com", "instance_id": 实例ID,
                "owner_id": 主人ID, "upstream": f"http://company-{账户ID}:8000",
                "chat_credential_ref": f"keychain://xj-multiuser/{账户ID}:chat",
                "search_credential_ref": f"keychain://xj-multiuser/{账户ID}:search",
                "data_volume": f"data-{账户ID}", "work_volume": f"work-{账户ID}",
                "instance_config_path": f"/var/xj/instance/{账户ID}/instance.yaml",
                "image_ref": "company@test", "runner_image_ref": "runner@test",
            }
            registry.开始开通(record)
            registry.完成开通(record, control_token="x" * 32)
            bound = registry.解析身份(email="user@example.com", sub="cf-sub-1")
            self.assertEqual(bound["状态"], "在用")
            self.assertEqual(registry.解析身份(email="user@example.com", sub="cf-sub-1")["account_id"], 账户ID)
            with self.assertRaisesRegex(注册表错误, "必须由船主重新绑定"):
                registry.解析身份(email="user@example.com", sub="cf-sub-2")

    def test_开通成功写入独立配置与凭据引用(self):
        with tempfile.TemporaryDirectory() as td:
            events = []
            registry = _假注册表()
            lifecycle = _新生命周期(Path(td), registry, _假Docker(events), events)
            keychain = {}

            def save(ref, value):
                events.append("keychain:save")
                keychain[ref] = value

            with patch.object(生命周期模块, "保存实例", side_effect=save), patch.object(
                生命周期模块, "删除实例", side_effect=lambda ref: keychain.pop(ref, None)
            ):
                result = lifecycle.开通(
                    email="new@example.com", display_name="张三", call_name="张先生",
                    aliases=["小张", "小张"], company_purpose="帮我完成手上的工作",
                    chat_daily_usd=1, chat_monthly_usd=10,
                    search_daily_usd=0.5, search_monthly_usd=5,
                )

            self.assertEqual(result["状态"], "待绑定")
            config = yaml.safe_load(Path(registry.record["instance_config_path"]).read_text(encoding="utf-8"))
            self.assertEqual(config["owner"]["display_name"], "张三")
            self.assertEqual(config["owner"]["别名"], ["小张"])
            self.assertTrue(config["owner"]["owner_id"].startswith("own_"))
            self.assertEqual(config["预算"]["日上限USD"], 1.5)
            self.assertEqual(set(keychain), {registry.record["chat_credential_ref"], registry.record["search_credential_ref"]})
            self.assertNotIn("sk-", Path(registry.record["instance_config_path"]).read_text(encoding="utf-8"))

    def test_开通中途失败回滚网络和配置(self):
        with tempfile.TemporaryDirectory() as td:
            events = []
            registry = _假注册表()
            lifecycle = _新生命周期(Path(td), registry, _假Docker(events, fail_at="volumes"), events)
            with self.assertRaisesRegex(Docker错误, "模拟卷初始化失败"):
                lifecycle.开通(
                    email="new@example.com", display_name="张三", call_name="张先生",
                    aliases=[], company_purpose="帮我完成手上的工作",
                    chat_daily_usd=1, chat_monthly_usd=10,
                    search_daily_usd=0.5, search_monthly_usd=5,
                )
            self.assertIn("docker:delete-network:True", events)
            self.assertEqual(list(lifecycle.instance_root.iterdir()), [])
            self.assertEqual(registry.audits[-1]["result"], "rollback")

    def test_用户只修改自己的可编辑资料并重载实例(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events = []
            registry = _假注册表()
            lifecycle = _新生命周期(root, registry, _假Docker(events), events)
            account_dir = lifecycle.instance_root / 账户ID
            policy_dir = lifecycle.policy_root / 账户ID
            account_dir.mkdir()
            policy_dir.mkdir()
            config = lifecycle._配置(
                account_id=账户ID, instance_id=实例ID, owner_id=主人ID,
                display_name="张三", call_name="张先生", aliases=["小张"], company_purpose="原目的",
                chat_ref="keychain://chat", search_ref="keychain://search",
                total_daily=1, total_monthly=10,
            )
            config["以后版本字段"] = {"必须保留": True}
            config_path = account_dir / "instance.yaml"
            config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
            (policy_dir / "README.md").write_text("旧制度\n", encoding="utf-8")
            registry.account = {
                "account_id": 账户ID, "instance_id": 实例ID, "owner_id": 主人ID,
                "status": "在用", "instance_config_path": str(config_path),
                "display_name": "张三", "call_name": "张先生",
            }

            result = lifecycle.更新资料(账户ID, {
                "display_name": "张小三", "call_name": "小张女士", "aliases": ["小张", "三三"],
                "company_purpose": "协助我完成自己的项目", "theme": "mist",
            })

            saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            self.assertEqual(result["call_name"], "小张女士")
            self.assertEqual(saved["owner"]["别名"], ["小张", "三三"])
            self.assertEqual(saved["公司目的"], "协助我完成自己的项目")
            self.assertEqual(saved["界面"]["主题"], "mist")
            self.assertEqual(saved["以后版本字段"], {"必须保留": True})
            self.assertEqual(events[-2:], ["docker:render-policy", "docker:reload-company"])

    def test_重启会清理被强制中断的开户(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "registry.sqlite3"
            registry = 账户注册表(db)
            resources = _假Docker.名称(账户ID)
            account_dir = root / "instance" / 账户ID
            policy_dir = root / "policy" / 账户ID
            chat_ref = f"keychain://xj-multiuser/{账户ID}:chat"
            search_ref = f"keychain://xj-multiuser/{账户ID}:search"
            record = {
                "account_id": 账户ID, "email": "interrupted@example.com", "instance_id": 实例ID,
                "owner_id": 主人ID, "upstream": f"http://company-{账户ID}:8000",
                "chat_credential_ref": chat_ref, "search_credential_ref": search_ref,
                "data_volume": resources["data_volume"], "work_volume": resources["work_volume"],
                "instance_config_path": str(account_dir / "instance.yaml"),
                "image_ref": "company@test", "runner_image_ref": "runner@test",
            }
            registry.开始开通(record)
            account_dir.mkdir(parents=True)
            policy_dir.mkdir(parents=True)
            (account_dir / "instance.yaml").write_text("schema_version: 1\n", encoding="utf-8")
            (policy_dir / "班规.md").write_text("测试\n", encoding="utf-8")

            events = []
            restarted_registry = 账户注册表(db)
            lifecycle = _新生命周期(root, restarted_registry, _假Docker(events), events)

            def delete(ref, *, missing_ok=False):
                events.append("keychain:delete-chat" if ref == chat_ref else "keychain:delete-search")

            with patch.object(生命周期模块, "删除实例", side_effect=delete):
                result = lifecycle.恢复未完成开通()

            self.assertEqual(result, {"recovered": 1})
            self.assertEqual(restarted_registry.待恢复开通(), [])
            with self.assertRaisesRegex(注册表错误, "账户不存在"):
                restarted_registry.状态(账户ID)
            self.assertFalse(account_dir.exists())
            self.assertFalse(policy_dir.exists())
            self.assertIn("chat:revoke-alias", events)
            self.assertIn("search:revoke", events)
            self.assertIn("docker:delete-container:True", events)
            self.assertIn("docker:delete-network:True", events)
            self.assertIn("docker:delete-volumes:True", events)
            audits = restarted_registry.审计记录(账户ID, 10)
            self.assertTrue(any(row["action"] == "provision-rollback" and row["result"] == "ok" for row in audits))

    def test_永久删除按撤权_删凭据_删数据_改状态的顺序(self):
        with tempfile.TemporaryDirectory() as td:
            events = []
            registry = _假注册表()
            lifecycle = _新生命周期(Path(td), registry, _假Docker(events), events)
            account_dir = lifecycle.instance_root / 账户ID
            policy_dir = lifecycle.policy_root / 账户ID
            account_dir.mkdir()
            policy_dir.mkdir()
            (account_dir / "instance.yaml").write_text("schema_version: 1\n", encoding="utf-8")
            chat_ref = f"keychain://xj-multiuser/{账户ID}:chat"
            search_ref = f"keychain://xj-multiuser/{账户ID}:search"
            registry.account = {
                "account_id": 账户ID, "status": "停用", "email": "user@example.com",
                "chat_credential_ref": chat_ref, "search_credential_ref": search_ref,
                "instance_config_path": str(account_dir / "instance.yaml"),
            }

            def read(ref):
                events.append("keychain:read")
                return "sk-" + "c" * 40

            def delete(ref, *, missing_ok=False):
                events.append("keychain:delete")

            with patch.object(生命周期模块, "读取实例", side_effect=read), patch.object(
                生命周期模块, "删除实例", side_effect=delete
            ):
                result = lifecycle.永久删除(账户ID, 账户ID)

            self.assertEqual(result["状态"], "已删")
            self.assertEqual(
                events,
                [
                    "keychain:read", "chat:revoke", "search:revoke",
                    "keychain:delete", "keychain:delete", "docker:delete-container:True",
                    "docker:delete-network:True", "docker:delete-volumes:True",
                ],
            )
            self.assertFalse(account_dir.exists())
            self.assertFalse(policy_dir.exists())
            self.assertEqual(registry.states[-1], (账户ID, "已删", "purge"))

    def test_Docker不可用时不得把资源误判为已删(self):
        docker = Docker资源.__new__(Docker资源)
        docker.docker = "docker"
        unavailable = SimpleNamespace(returncode=1, stdout="", stderr="Cannot connect to the Docker daemon")
        missing = SimpleNamespace(returncode=1, stdout="", stderr="Error: No such volume: data-test")
        with patch("多用户.控制面.Docker资源.subprocess.run", return_value=unavailable):
            with self.assertRaisesRegex(Docker错误, "无法确认资源状态"):
                docker._存在("volume", "data-test")
        with patch("多用户.控制面.Docker资源.subprocess.run", return_value=missing):
            self.assertFalse(docker._存在("volume", "data-test"))


class 导出恢复测试(unittest.TestCase):
    def _源(self, root: Path):
        data = root / "source-data"
        work = root / "source-work"
        destination = root / "exports"
        config = root / "instance.yaml"
        (data / "资料室").mkdir(parents=True)
        (data / "运行状态").mkdir()
        work.mkdir()
        (data / "资料室" / "船主.md").write_text("一条用户记忆\n", encoding="utf-8")
        (work / "项目.txt").write_text("一个用户项目\n", encoding="utf-8")
        for db_path in (data / "资料室" / "图谱.db", data / "运行状态" / "统一搜索.db"):
            with closing(sqlite3.connect(db_path)) as con:
                con.execute("CREATE TABLE facts(value TEXT NOT NULL)")
                con.execute("INSERT INTO facts VALUES(?)", (db_path.name,))
                con.commit()
        config.write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1, "account_id": 账户ID, "instance_id": 实例ID,
                    "owner": {"owner_id": 主人ID, "display_name": "张三", "称呼": "张先生", "别名": ["小张"]},
                    "公司目的": "帮我完成手上的工作",
                    "模型路由": {
                        "chat": {"base_url": "http://model-proxy:4000/v1", "credential_ref": f"keychain://xj-multiuser/{账户ID}:chat"},
                        "search": {"base_url": "http://search-gw:4100", "credential_ref": f"keychain://xj-multiuser/{账户ID}:search"},
                    },
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        return data, work, destination, config

    def _创建(self, root: Path):
        data, work, destination, config = self._源(root)
        with patch.multiple(
            备份模块,
            数据根=data,
            WORK=work,
            CODE=root,
            实例配置路径=config,
            是远程实例=lambda: True,
            _git=lambda: {"提交": "a" * 40, "分支": "test", "未提交路径": []},
        ):
            archive = 备份模块.创建(destination)
        return archive, data, work, config

    def test_导出包无凭据并可原子导入新目标(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive, _data, _work, _config = self._创建(root)
            self.assertTrue(备份模块.验证(archive)["ok"])
            with zipfile.ZipFile(archive) as zf:
                manifest = json.loads(zf.read("公司快照/备份清单.json"))
                exported_config = zf.read("公司快照/instance.yaml").decode("utf-8")
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["account_id"], 账户ID)
            self.assertEqual(manifest["凭据状态"], "需重新绑定")
            self.assertNotIn("credential_ref", exported_config)
            self.assertIn("需重新绑定: true", exported_config)

            restored_data = root / "app" / "data"
            restored_work = root / "app" / "work"
            restored_config = root / "app" / "instance.yaml"
            result = 备份模块.恢复到(
                archive, data_target=restored_data, work_target=restored_work, config_target=restored_config,
            )
            self.assertTrue(result["ok"])
            self.assertEqual((restored_data / "资料室" / "船主.md").read_text(encoding="utf-8"), "一条用户记忆\n")
            self.assertEqual((restored_work / "项目.txt").read_text(encoding="utf-8"), "一个用户项目\n")
            self.assertTrue(restored_config.is_file())

    def test_导入失败不污染目标且不修改原包(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive, _data, _work, _config = self._创建(root)
            before = 备份模块._sha(archive)
            targets = (root / "new-data", root / "new-work", root / "new-instance.yaml")

            original_scan = 备份模块._含疑似凭据

            def fail_incoming(path):
                if ".xj-import-" in str(path):
                    return True
                return original_scan(path)

            with patch.object(备份模块, "_含疑似凭据", side_effect=fail_incoming):
                with self.assertRaisesRegex(RuntimeError, "疑似含有明文凭据"):
                    备份模块.恢复到(
                        archive, data_target=targets[0], work_target=targets[1], config_target=targets[2],
                    )
            self.assertFalse(any(path.exists() for path in targets))
            self.assertEqual(备份模块._sha(archive), before)

    def test_封包前命中疑似凭据会拒绝导出(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data, work, destination, config = self._源(root)
            (work / "leak.txt").write_text("OPENAI_API_KEY=sk-" + "x" * 32, encoding="utf-8")
            with patch.multiple(
                备份模块,
                数据根=data,
                WORK=work,
                CODE=root,
                实例配置路径=config,
                是远程实例=lambda: True,
                _git=lambda: {"提交": "a" * 40, "分支": "test", "未提交路径": []},
            ):
                with self.assertRaisesRegex(RuntimeError, "疑似含有明文凭据"):
                    备份模块.创建(destination)
            self.assertEqual(list(destination.glob("*.zip")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
