"""Transactional provisioning and owner-only account lifecycle actions."""

from __future__ import annotations

import fcntl
import functools
import os
import math
import re
import secrets
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

from .Docker资源 import Docker资源
from .凭据 import 保存实例, 凭据不存在, 删除实例, 读取实例
from .平台客户端 import LiteLLM客户端, 搜索网关客户端
from .注册表 import 注册表错误, 账户注册表


_字母表 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_员工 = (
    ("emp_pm", "老钟"),
    ("emp_chief", "老梁"),
    ("emp_engineer", "阿强"),
    ("emp_test", "老纪"),
    ("emp_writer", "阿言"),
)
_版本镜像 = re.compile(r"xj-(?:company|runner):twilight-[a-z0-9][a-z0-9._-]{0,55}")


def _ulid() -> str:
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(_字母表[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def _文本(value: str, name: str, maximum: int) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > maximum or any(ord(c) < 32 for c in raw) or "{" in raw or "}" in raw:
        raise 注册表错误(f"{name} 无效")
    return raw


def _安全删除(path: Path, root: Path) -> None:
    target = path.resolve()
    base = root.resolve()
    if target == base or not target.is_relative_to(base):
        raise RuntimeError("拒绝删除控制面根目录之外的路径")
    if target.exists():
        for child in target.rglob("*"):
            try:
                child.chmod(0o700 if child.is_dir() else 0o600)
            except OSError:
                pass
        target.chmod(0o700)
        shutil.rmtree(target)


def _串行操作(method):
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._操作锁():
            return method(self, *args, **kwargs)
    return wrapped


class 生命周期:
    def __init__(self, registry: 账户注册表) -> None:
        self.registry = registry
        self.docker = Docker资源()
        self.litellm = LiteLLM客户端(os.environ.get("XJ_LITELLM_ADMIN_URL", "http://127.0.0.1:4000"))
        self.search = 搜索网关客户端(os.environ.get("XJ_SEARCH_ADMIN_URL", "http://127.0.0.1:4100"))
        self.instance_root = Path(os.environ.get("XJ_INSTANCE_DIR", "/var/xj/instance")).expanduser().resolve()
        self.policy_root = Path(os.environ.get("XJ_POLICY_DIR", "/var/xj/policy")).expanduser().resolve()
        self.private_terms = Path(os.environ.get("XJ_PRIVATE_TERMS_FILE", "")).expanduser().resolve()
        self.image = os.environ.get("XJ_COMPANY_IMAGE", "").strip()
        self.runner_image = os.environ.get("XJ_RUNNER_IMAGE", "").strip()
        self.lock_path = self.registry.path.with_name("lifecycle.lock")
        self._thread_lock = threading.RLock()
        self._lock_state = threading.local()
        if not self.image or not self.runner_image or any(c.isspace() for c in self.image + self.runner_image):
            raise RuntimeError("必须固定 XJ_COMPANY_IMAGE 和 XJ_RUNNER_IMAGE")
        if not self.private_terms.is_file():
            raise RuntimeError("缺少 XJ_PRIVATE_TERMS_FILE，禁止跳过私人信息扫描")
        self.instance_root.mkdir(parents=True, exist_ok=True)
        self.policy_root.mkdir(parents=True, exist_ok=True)

    def 设置默认版本(self, image: str, runner_image: str) -> None:
        image = str(image or "").strip()
        runner_image = str(runner_image or "").strip()
        development_pair = image == "xj-company:dev" and runner_image == "xj-runner:dev"
        if not development_pair and (
            not _版本镜像.fullmatch(image) or not _版本镜像.fullmatch(runner_image)
        ):
            raise 注册表错误("新用户默认版本没有固定版本号")
        self.image = image
        self.runner_image = runner_image

    @contextmanager
    def _操作锁(self):
        thread_lock = getattr(self, "_thread_lock", None)
        if thread_lock is None:
            thread_lock = threading.RLock()
            self._thread_lock = thread_lock
        state = getattr(self, "_lock_state", None)
        if state is None:
            state = threading.local()
            self._lock_state = state
        with thread_lock:
            depth = int(getattr(state, "depth", 0))
            if depth:
                state.depth = depth + 1
                try:
                    yield
                finally:
                    state.depth = depth
                return
            state.depth = 1
            try:
                with self._文件操作锁():
                    yield
            finally:
                state.depth = 0

    @contextmanager
    def _文件操作锁(self):
        lock_path = getattr(self, "lock_path", None)
        if lock_path is None:
            registry_path = getattr(self.registry, "path", None)
            lock_path = registry_path.with_name("lifecycle.lock") if registry_path else self.instance_root.parent / "lifecycle.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    @contextmanager
    def 发布维护锁(self):
        """Hold the same lock as every account lifecycle mutation for a full deployment."""
        with self._操作锁():
            yield

    def 实际运行镜像(self, account_id: str) -> str | None:
        return self.实际运行状态(account_id)["image"]

    def 实际运行状态(self, account_id: str) -> dict[str, Any]:
        resources = self.docker.名称(account_id)
        reader = getattr(self.docker, "容器镜像", None)
        image = reader(resources["container"]) if callable(reader) else None
        if image is None:
            return {"image": None, "running": False, "healthy": False}
        state_reader = getattr(self.docker, "状态", None)
        state = state_reader(resources["container"]) if callable(state_reader) else {}
        health = state.get("Health") if isinstance(state.get("Health"), dict) else {}
        return {
            "image": image,
            "running": bool(state.get("Running", True)),
            "healthy": str(health.get("Status") or "healthy") == "healthy",
        }

    @_串行操作
    def 恢复中断版本(
        self,
        account_id: str,
        *,
        image: str,
        runner_image: str,
        actor: str = "supervisor-release-recovery",
    ) -> dict[str, Any]:
        """Recreate one account from its recorded pre-deployment image without taking a second backup."""
        account = self.registry.内部账户(account_id)
        if account["status"] != "在用":
            raise 注册表错误("只有在用账户可以恢复中断部署")
        target_image = str(image or "").strip()
        target_runner = str(runner_image or "").strip()
        development = target_image == "xj-company:dev" and target_runner == "xj-runner:dev"
        if not development and (
            not _版本镜像.fullmatch(target_image) or not _版本镜像.fullmatch(target_runner)
        ):
            raise 注册表错误("中断部署的恢复版本无效")

        resources = self.docker.名称(account_id)
        config_path = self._配置文件(account)
        policy_dir = self.policy_root / account_id
        if not policy_dir.parent.is_dir():
            raise 注册表错误("账户制度根目录无效")
        chat_key = 读取实例(account["chat_credential_ref"])
        search_token = 读取实例(account["search_credential_ref"])
        token = secrets.token_hex(8)
        stage_policy = self.policy_root / f".{account_id}.recovery-{token}"
        displaced_policy = self.policy_root / f".{account_id}.interrupted-{token}"
        swapped = False
        try:
            self.docker.渲染制度(
                image=target_image, config=config_path, output=stage_policy,
                private_terms=self.private_terms,
            )
            self.docker.删除容器(resources["container"], strict=True)
            if policy_dir.exists():
                policy_dir.rename(displaced_policy)
            stage_policy.rename(policy_dir)
            swapped = True
            self.docker.创建并启动实例(
                account_id=account_id, resources=resources, image=target_image,
                config=config_path, policy=policy_dir, chat_key=chat_key, search_token=search_token,
            )
            result = self.registry.更新版本(
                account_id, old_image_ref=account["image_ref"], image_ref=target_image,
                runner_image_ref=target_runner, actor=actor,
            )
            try:
                if displaced_policy.exists():
                    _安全删除(displaced_policy, self.policy_root)
            except Exception as exc:  # 目标已健康且登记已提交，清理旧副本不能反向破坏恢复结果。
                try:
                    self.registry.记审计(
                        actor=actor, action="release-recovery-cleanup", account_id=account_id,
                        result="partial", details={"error": type(exc).__name__},
                    )
                except Exception:
                    pass
            return {**result, "恢复": "中断部署已恢复"}
        except Exception as original:
            failures: list[str] = []
            try:
                self.docker.删除容器(resources["container"])
            except Exception as exc:  # noqa: BLE001
                failures.append(f"删除失败容器:{type(exc).__name__}")
            if swapped:
                try:
                    if policy_dir.exists():
                        _安全删除(policy_dir, self.policy_root)
                    if displaced_policy.exists():
                        displaced_policy.rename(policy_dir)
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"恢复原制度:{type(exc).__name__}")
            if policy_dir.is_dir():
                try:
                    self.docker.创建并启动实例(
                        account_id=account_id, resources=resources, image=account["image_ref"],
                        config=config_path, policy=policy_dir, chat_key=chat_key, search_token=search_token,
                    )
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"恢复原容器:{type(exc).__name__}")
            try:
                self.registry.记审计(
                    actor=actor, action="release-recovery", account_id=account_id,
                    result="partial" if failures else "failed",
                    details={"target": target_image, "failures": failures},
                )
            except Exception:
                pass
            if failures:
                raise RuntimeError("中断部署恢复失败：" + "、".join(failures)) from original
            raise
        finally:
            if stage_policy.exists():
                _安全删除(stage_policy, self.policy_root)

    @staticmethod
    def _配置(
        *, account_id: str, instance_id: str, owner_id: str,
        display_name: str, call_name: str, aliases: list[str], company_purpose: str,
        chat_ref: str, search_ref: str, total_daily: float, total_monthly: float,
    ) -> dict[str, Any]:
        clean_aliases = list(dict.fromkeys(_文本(item, "主人别名", 32) for item in aliases))
        if len("、".join(clean_aliases)) > 128:
            raise 注册表错误("主人别名合计不能超过 128 字")
        return {
            "schema_version": 1,
            "account_id": account_id,
            "instance_id": instance_id,
            "owner": {
                "owner_id": owner_id,
                "display_name": _文本(display_name, "主人显示名", 32),
                "称呼": _文本(call_name, "主人称呼", 32),
                "别名": clean_aliases,
            },
            "公司目的": _文本(company_purpose, "公司目的", 200),
            "employees": [
                {"employee_id": employee_id, "人名": name, "display_name": name}
                for employee_id, name in _员工
            ],
            "界面": {"主题": "default"},
            "模型路由": {
                "chat": {"base_url": "http://model-proxy:4000/v1", "credential_ref": chat_ref},
                "search": {"base_url": "http://search-gw:4100", "credential_ref": search_ref},
            },
            "预算": {"日上限USD": total_daily, "月上限USD": total_monthly},
            "配额": {"文件总量MB": 2048},
            "功能开关": {"统一搜索": True},
        }

    def _配置文件(self, account: dict[str, Any]) -> Path:
        expected = (self.instance_root / account["account_id"] / "instance.yaml").resolve()
        configured = Path(account["instance_config_path"]).expanduser().resolve()
        if configured != expected or not configured.is_file() or configured.is_symlink():
            raise 注册表错误("账户配置路径无效")
        return configured

    @staticmethod
    def _制度文件(root: Path) -> dict[Path, bytes]:
        files: dict[Path, bytes] = {}
        for path in root.rglob("*"):
            if path.is_symlink():
                raise RuntimeError("制度目录不允许软链接")
            if path.is_file():
                files[path.relative_to(root)] = path.read_bytes()
        if not files:
            raise RuntimeError("账户制度目录为空")
        return files

    @staticmethod
    def _原位覆盖制度(root: Path, files: dict[Path, bytes]) -> None:
        current = 生命周期._制度文件(root)
        if set(current) != set(files):
            raise RuntimeError("新旧制度文件结构不一致，拒绝在线覆盖")
        for relative, content in files.items():
            target = root / relative
            target.chmod(0o600)
            target.write_bytes(content)
            target.chmod(0o444)

    def 读取资料(self, account_id: str) -> dict[str, Any]:
        account = self.registry.内部账户(account_id)
        if account["status"] != "在用":
            raise 注册表错误("只有在用账户可以读取资料")
        config_path = self._配置文件(account)
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("owner"), dict):
            raise 注册表错误("账户配置内容无效")
        owner = raw["owner"]
        if (
            raw.get("account_id") != account["account_id"]
            or raw.get("instance_id") != account["instance_id"]
            or owner.get("owner_id") != account["owner_id"]
        ):
            raise 注册表错误("账户配置的稳定身份不匹配")
        interface = raw.get("界面") if isinstance(raw.get("界面"), dict) else {}
        return {
            "display_name": _文本(owner.get("display_name"), "主人显示名", 32),
            "call_name": _文本(owner.get("称呼"), "主人称呼", 32),
            "aliases": list(owner.get("别名") or []),
            "company_purpose": _文本(raw.get("公司目的"), "公司目的", 200),
            "theme": str(interface.get("主题") or "default"),
        }

    @_串行操作
    def 更新资料(self, account_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {"display_name", "call_name", "aliases", "company_purpose", "theme"}
        if not changes or set(changes) - allowed:
            raise 注册表错误("用户资料字段无效")
        account = self.registry.内部账户(account_id)
        if account["status"] != "在用":
            raise 注册表错误("只有在用账户可以修改资料")
        config_path = self._配置文件(account)
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("owner"), dict):
            raise 注册表错误("账户配置内容无效")
        owner = dict(raw["owner"])
        if (
            raw.get("account_id") != account["account_id"]
            or raw.get("instance_id") != account["instance_id"]
            or owner.get("owner_id") != account["owner_id"]
        ):
            raise 注册表错误("账户配置的稳定身份不匹配")

        display_name = _文本(changes.get("display_name", owner.get("display_name")), "主人显示名", 32)
        call_name = _文本(changes.get("call_name", owner.get("称呼")), "主人称呼", 32)
        aliases_value = changes.get("aliases", owner.get("别名") or [])
        if not isinstance(aliases_value, list):
            raise 注册表错误("主人别名必须是数组")
        aliases = list(dict.fromkeys(_文本(item, "主人别名", 32) for item in aliases_value))
        if len("、".join(aliases)) > 128:
            raise 注册表错误("主人别名合计不能超过 128 字")
        company_purpose = _文本(
            changes.get("company_purpose", raw.get("公司目的")), "公司目的", 200,
        )
        interface = dict(raw.get("界面") or {}) if isinstance(raw.get("界面"), dict) else {}
        theme = _文本(changes.get("theme", interface.get("主题") or "default"), "界面主题", 32)

        updated = dict(raw)
        updated["owner"] = owner
        updated["owner"].update({"display_name": display_name, "称呼": call_name, "别名": aliases})
        updated["公司目的"] = company_purpose
        interface["主题"] = theme
        updated["界面"] = interface

        policy_dir = self.policy_root / account_id
        if not policy_dir.is_dir() or policy_dir.is_symlink():
            raise 注册表错误("账户制度目录无效")
        old_config = config_path.read_bytes()
        old_policy = self._制度文件(policy_dir)
        stage_config = config_path.with_name(f".instance-{secrets.token_hex(8)}.yaml")
        stage_policy = Path(tempfile.mkdtemp(prefix=f".{account_id}-", dir=self.policy_root))
        stage_policy.rmdir()
        applied = False
        try:
            stage_config.write_text(yaml.safe_dump(updated, allow_unicode=True, sort_keys=False), encoding="utf-8")
            stage_config.chmod(0o600)
            self.docker.渲染制度(
                image=self.image, config=stage_config, output=stage_policy, private_terms=self.private_terms,
            )
            new_policy = self._制度文件(stage_policy)
            if set(new_policy) != set(old_policy):
                raise RuntimeError("新旧制度文件结构不一致，拒绝更新")
            config_path.chmod(0o600)
            config_path.write_bytes(stage_config.read_bytes())
            self._原位覆盖制度(policy_dir, new_policy)
            applied = True
            self.docker.重载(f"company-{account_id.lower()}")
            self.registry.更新用户资料(
                account_id, display_name=display_name, call_name=call_name,
            )
            return self.读取资料(account_id)
        except Exception as original:
            if applied:
                try:
                    config_path.chmod(0o600)
                    config_path.write_bytes(old_config)
                    self._原位覆盖制度(policy_dir, old_policy)
                    self.docker.重载(f"company-{account_id.lower()}")
                except Exception as rollback_error:
                    raise RuntimeError("用户资料更新失败，旧配置也未能恢复") from rollback_error
            try:
                self.registry.记审计(
                    actor="user", action="update-profile", account_id=account_id, result="failed",
                    details={"error": type(original).__name__},
                )
            except Exception:
                pass
            raise
        finally:
            stage_config.unlink(missing_ok=True)
            if stage_policy.exists():
                _安全删除(stage_policy, self.policy_root)

    @_串行操作
    def 开通(
        self,
        *,
        email: str,
        display_name: str,
        call_name: str,
        aliases: list[str],
        company_purpose: str,
        chat_daily_usd: float,
        chat_monthly_usd: float,
        search_daily_usd: float,
        search_monthly_usd: float,
        chat_rpm: int = 60,
        search_rpm: int = 60,
    ) -> dict[str, Any]:
        budgets = (chat_daily_usd, chat_monthly_usd, search_daily_usd, search_monthly_usd)
        if not all(math.isfinite(value) and value > 0 for value in budgets):
            raise 注册表错误("聊天和搜索的每日/月度预算都必须大于 0")
        if chat_daily_usd > chat_monthly_usd or search_daily_usd > search_monthly_usd:
            raise 注册表错误("每日预算不能高于对应月度预算")
        if chat_monthly_usd > 1_000_000 or search_monthly_usd > 1_000_000:
            raise 注册表错误("月度预算超出系统上限")
        if not 1 <= chat_rpm <= 600 or not 1 <= search_rpm <= 600:
            raise 注册表错误("每分钟请求上限必须在 1 到 600 之间")
        total_daily = chat_daily_usd + search_daily_usd
        total_monthly = chat_monthly_usd + search_monthly_usd

        uid = _ulid()
        account_id, instance_id, owner_id = "acc_" + uid, "inst_" + _ulid(), "own_" + _ulid()
        resources = self.docker.名称(account_id)
        account_dir = self.instance_root / account_id
        policy_dir = self.policy_root / account_id
        config_path = account_dir / "instance.yaml"
        chat_ref = f"keychain://xj-multiuser/{account_id}:chat"
        search_ref = f"keychain://xj-multiuser/{account_id}:search"
        chat_key = "sk-xj-" + secrets.token_urlsafe(36)
        search_token = "xjs_" + secrets.token_urlsafe(36)
        config = self._配置(
            account_id=account_id, instance_id=instance_id, owner_id=owner_id,
            display_name=display_name, call_name=call_name, aliases=aliases,
            company_purpose=company_purpose, chat_ref=chat_ref, search_ref=search_ref,
            total_daily=total_daily, total_monthly=total_monthly,
        )
        record = {
            "account_id": account_id,
            "email": email,
            "display_name": display_name,
            "call_name": call_name,
            "instance_id": instance_id,
            "owner_id": owner_id,
            "upstream": f"http://{resources['container']}:8000",
            "chat_credential_ref": chat_ref,
            "search_credential_ref": search_ref,
            "data_volume": resources["data_volume"],
            "work_volume": resources["work_volume"],
            "instance_config_path": str(config_path),
            "image_ref": self.image,
            "runner_image_ref": self.runner_image,
        }
        self.registry.开始开通(record)
        reached: set[str] = set()
        try:
            reached.add("account-dir")
            account_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
            config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
            config_path.chmod(0o600)

            reached.add("network")
            self.docker.创建网络(resources["network"])
            reached.add("volumes")
            self.docker.创建卷(resources["data_volume"], resources["work_volume"], self.image)
            reached.add("policy-dir")
            self.docker.渲染制度(image=self.image, config=config_path, output=policy_dir, private_terms=self.private_terms)

            reached.add("chat-secret")
            保存实例(chat_ref, chat_key)
            reached.add("search-secret")
            保存实例(search_ref, search_token)

            reached.add("chat-account")
            returned_chat_key = self.litellm.签发(
                account_id, daily_usd=chat_daily_usd, monthly_usd=chat_monthly_usd, rpm_limit=chat_rpm,
                key=chat_key,
            )
            if not secrets.compare_digest(returned_chat_key, chat_key):
                raise RuntimeError("聊天平台返回了不同的用户凭据")
            reached.add("search-account")
            returned_search_token = self.search.签发(
                account_id, daily_usd=search_daily_usd, monthly_usd=search_monthly_usd, rpm_limit=search_rpm,
                token=search_token,
            )
            if not secrets.compare_digest(returned_search_token, search_token):
                raise RuntimeError("搜索平台返回了不同的用户凭据")

            reached.add("container")
            self.docker.创建并启动实例(
                account_id=account_id, resources=resources, image=self.image,
                config=config_path, policy=policy_dir, chat_key=chat_key, search_token=search_token,
            )
            return self.registry.完成开通(record, control_token=search_token)
        except Exception as original:
            failures = self._清理未完成开通(record, reached=reached)
            if not failures:
                try:
                    self.registry.取消未完成开通(
                        account_id,
                        actor="owner-cli",
                        details={"error": type(original).__name__},
                    )
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"registry:{type(exc).__name__}")
            if failures:
                try:
                    self.registry.记审计(
                        actor="owner-cli", action="provision-rollback", account_id=account_id, result="partial",
                        details={"error": type(original).__name__, "failures": failures},
                    )
                except Exception:  # 审计库故障不应遮住真正的开通失败原因。
                    pass
            raise

    def _清理未完成开通(
        self,
        record: dict[str, str],
        *,
        reached: set[str] | None = None,
    ) -> list[str]:
        account_id = record["account_id"]
        resources = self.docker.名称(account_id)
        actions = (
            ("container", lambda: self.docker.删除容器(resources["container"], strict=True)),
            ("chat-account", lambda: self.litellm.撤销别名(account_id)),
            ("search-account", lambda: self.search.撤销(account_id, missing_ok=True)),
            ("chat-secret", lambda: 删除实例(record["chat_credential_ref"], missing_ok=True)),
            ("search-secret", lambda: 删除实例(record["search_credential_ref"], missing_ok=True)),
            ("network", lambda: self.docker.删除网络(resources["network"], strict=True)),
            ("volumes", lambda: self.docker.删除卷(resources["data_volume"], resources["work_volume"], strict=True)),
            ("policy-dir", lambda: _安全删除(self.policy_root / account_id, self.policy_root)),
            ("account-dir", lambda: _安全删除(self.instance_root / account_id, self.instance_root)),
        )
        failures: list[str] = []
        for name, action in actions:
            if reached is not None and name not in reached:
                continue
            try:
                action()
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{name}:{type(exc).__name__}")
        return failures

    @_串行操作
    def 恢复未完成开通(self) -> dict[str, int]:
        recovered = 0
        failed: dict[str, list[str]] = {}
        for record in self.registry.待恢复开通():
            account_id = record["account_id"]
            failures = self._清理未完成开通(record)
            if not failures:
                try:
                    self.registry.取消未完成开通(
                        account_id,
                        actor="supervisor",
                        details={"reason": "restart-recovery"},
                    )
                    recovered += 1
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"registry:{type(exc).__name__}")
            if failures:
                failed[account_id] = failures
                try:
                    self.registry.记审计(
                        actor="supervisor", action="provision-recovery", account_id=account_id,
                        result="partial", details={"failures": failures},
                    )
                except Exception:
                    pass
        if failed:
            raise RuntimeError("未完成开户仍有资源未能清理：" + "、".join(sorted(failed)))
        return {"recovered": recovered}

    @_串行操作
    def 升级版本(
        self,
        account_id: str,
        *,
        image: str | None = None,
        runner_image: str | None = None,
        actor: str = "owner-cli",
    ) -> dict[str, Any]:
        account = self.registry.内部账户(account_id)
        if account["status"] != "在用":
            raise 注册表错误("只有在用账户可以升级")
        target_image = str(image or self.image).strip()
        target_runner = str(runner_image or self.runner_image).strip()
        development_rollback = (
            actor == "owner-release-rollback"
            and target_image == "xj-company:dev"
            and target_runner == "xj-runner:dev"
        )
        if not development_rollback and (
            not _版本镜像.fullmatch(target_image) or not _版本镜像.fullmatch(target_runner)
        ):
            raise 注册表错误("目标版本没有固定版本号，拒绝升级")
        resources = self.docker.名称(account_id)
        if account["image_ref"] == target_image and account["runner_image_ref"] == target_runner:
            reader = getattr(self.docker, "容器镜像", None)
            actual = reader(resources["container"]) if callable(reader) else target_image
            if actual == target_image:
                return {**self.registry.状态(account_id), "升级": "已经是当前版本"}
        config_path = self._配置文件(account)
        policy_dir = self.policy_root / account_id
        if not policy_dir.is_dir() or policy_dir.is_symlink():
            raise 注册表错误("账户制度目录无效")
        chat_key = 读取实例(account["chat_credential_ref"])
        search_token = 读取实例(account["search_credential_ref"])

        token = secrets.token_hex(8)
        stage_policy = self.policy_root / f".{account_id}.upgrade-{token}"
        old_policy = self.policy_root / f".{account_id}.rollback-{token}"
        swapped = False
        stopped = False
        backup_path = ""
        try:
            self.docker.渲染制度(
                image=target_image, config=config_path, output=stage_policy, private_terms=self.private_terms,
            )
            backup_path = self.docker.创建用户备份(resources["container"])
            self.docker.停止(resources["container"])
            stopped = True
            policy_dir.rename(old_policy)
            stage_policy.rename(policy_dir)
            swapped = True
            self.docker.删除容器(resources["container"], strict=True)
            self.docker.创建并启动实例(
                account_id=account_id, resources=resources, image=target_image,
                config=config_path, policy=policy_dir, chat_key=chat_key, search_token=search_token,
            )
            result = self.registry.更新版本(
                account_id, old_image_ref=account["image_ref"],
                image_ref=target_image, runner_image_ref=target_runner, actor=actor,
            )
            try:
                _安全删除(old_policy, self.policy_root)
            except Exception as exc:  # 旧制度副本清理失败不应回滚已经健康的新版本。
                try:
                    self.registry.记审计(
                        actor=actor, action="upgrade-cleanup", account_id=account_id,
                        result="partial", details={"error": type(exc).__name__},
                    )
                except Exception:
                    pass
            return {**result, "备份": backup_path}
        except Exception as original:
            rollback_failures: list[str] = []
            if stopped:
                try:
                    self.docker.删除容器(resources["container"])
                except Exception as exc:  # noqa: BLE001
                    rollback_failures.append(f"删除新容器:{type(exc).__name__}")
                if swapped:
                    try:
                        if policy_dir.exists():
                            _安全删除(policy_dir, self.policy_root)
                        old_policy.rename(policy_dir)
                    except Exception as exc:  # noqa: BLE001
                        rollback_failures.append(f"恢复制度:{type(exc).__name__}")
                try:
                    self.docker.创建并启动实例(
                        account_id=account_id, resources=resources, image=account["image_ref"],
                        config=config_path, policy=policy_dir, chat_key=chat_key, search_token=search_token,
                    )
                except Exception as exc:  # noqa: BLE001
                    rollback_failures.append(f"恢复旧容器:{type(exc).__name__}")
            try:
                self.registry.记审计(
                    actor=actor, action="upgrade-version", account_id=account_id,
                    result="partial" if rollback_failures else "failed",
                    details={
                        "target": target_image, "error": type(original).__name__,
                        "backup": backup_path, "rollback_failures": rollback_failures,
                    },
                )
            except Exception:
                pass
            if rollback_failures:
                raise RuntimeError("升级失败，旧版本也未能完整恢复：" + "、".join(rollback_failures)) from original
            raise
        finally:
            if stage_policy.exists():
                _安全删除(stage_policy, self.policy_root)

    @_串行操作
    def 停用(self, account_id: str) -> dict[str, Any]:
        account = self.registry.内部账户(account_id)
        if account["status"] != "在用":
            raise 注册表错误("只有在用账户可以停用")
        self.registry.置状态(account_id, "停用", action="stop")
        failures: list[str] = []
        actions = (
            ("chat", lambda: self.litellm.置阻断(读取实例(account["chat_credential_ref"]), True)),
            ("search", lambda: self.search.置阻断(account_id, True)),
            ("container", lambda: self.docker.停止(f"company-{account_id.lower()}")),
        )
        for name, action in actions:
            try:
                action()
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{name}:{type(exc).__name__}")
        if failures:
            self.registry.记审计(actor="owner-cli", action="stop-side-effects", account_id=account_id, result="partial", details={"failures": failures})
            raise RuntimeError("账户已在本地停用，但部分外部撤销失败：" + "、".join(failures))
        return self.registry.状态(account_id)

    @_串行操作
    def 重启(self, account_id: str) -> dict[str, Any]:
        account = self.registry.内部账户(account_id)
        if account["status"] != "停用":
            raise 注册表错误("只有停用账户可以重新启用")
        chat_key = 读取实例(account["chat_credential_ref"])
        try:
            self.litellm.置阻断(chat_key, False)
            self.search.置阻断(account_id, False)
            self.docker.重启(f"company-{account_id.lower()}")
            return self.registry.置状态(account_id, "在用", action="restart")
        except Exception:
            try:
                self.litellm.置阻断(chat_key, True)
                self.search.置阻断(account_id, True)
                self.docker.停止(f"company-{account_id.lower()}")
            except Exception:
                pass
            raise

    @_串行操作
    def 重绑(self, account_id: str, new_sub: str) -> dict[str, Any]:
        return self.registry.重新绑定(account_id, new_sub)

    @_串行操作
    def 永久删除(self, account_id: str, confirm: str) -> dict[str, Any]:
        if confirm != account_id:
            raise 注册表错误("永久删除二次确认不匹配")
        account = self.registry.内部账户(account_id)
        if account["status"] != "停用":
            raise 注册表错误("永久删除前必须先停用")
        resources = self.docker.名称(account_id)
        chat_ref = account["chat_credential_ref"]
        search_ref = account["search_credential_ref"]
        # 先撤销入口和凭据，再销毁用户数据；任何失败都保留停用状态便于重试。
        try:
            chat_key = 读取实例(chat_ref)
        except 凭据不存在:
            chat_key = ""
        if chat_key:
            self.litellm.撤销(chat_key)
        self.search.撤销(account_id)
        删除实例(chat_ref, missing_ok=True)
        删除实例(search_ref, missing_ok=True)
        self.docker.删除容器(resources["container"], strict=True)
        self.docker.删除网络(resources["network"], strict=True)
        self.docker.删除卷(resources["data_volume"], resources["work_volume"], strict=True)
        _安全删除(Path(account["instance_config_path"]).parent, self.instance_root)
        _安全删除(self.policy_root / account_id, self.policy_root)
        return self.registry.置状态(account_id, "已删", action="purge")
