"""Build and run the shared platform without writing secrets to project files."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib import request as urllib_request
from urllib.parse import urlsplit

import yaml

from ..控制面.凭据 import 保存平台, 凭据不存在, 读取平台
from .发布仓库 import (
    当前已发布版本,
    更新候选,
    读取候选,
    读取正式版本,
    验证已发布,
)
from .候选验收 import (
    候选步骤错误,
    安全操作日志,
    执行代码验收,
    执行隔离运行时检查,
    检查镜像归档空间,
    最小验收环境,
    预检发布环境,
)


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = Path(__file__).resolve().parent
COMPOSE = DEPLOY / "compose.yaml"
DEV_SOURCE_PATHS = (
    "前端", "工具", "requirements.txt", "requirements-host.txt",
    "多用户", ".dockerignore", "测试",
)

_内部凭据 = {
    "postgres-admin": lambda: secrets.token_urlsafe(48),
    "litellm-db-password": lambda: secrets.token_urlsafe(48),
    "search-db-password": lambda: secrets.token_urlsafe(48),
    "litellm-master": lambda: "sk-" + secrets.token_urlsafe(48),
    "search-admin": lambda: "xjsa_" + secrets.token_urlsafe(48),
    "supervisor-token": lambda: "xjct_" + secrets.token_urlsafe(48),
    "rerank-token": lambda: "xjrt_" + secrets.token_urlsafe(48),
}

# Compose 接受服务名；版本核对则读取固定的容器名。两者不能混用。
_平台服务 = ("local-rerank-bridge", "search-gw", "supervisor-bridge", "gw", "local-entry")
_平台容器 = ("xj-local-rerank-bridge", "xj-search-gw", "xj-supervisor-bridge", "xj-gw", "xj-local-entry")
_平台镜像格式 = re.compile(r"xj-platform:(?:dev|twilight-[a-z0-9][a-z0-9._-]{0,55})")
_精确镜像编号 = re.compile(r"sha256:[0-9a-f]{64}")
_外部运行镜像 = {
    "postgres": "public.ecr.aws/docker/library/postgres:16.14-bookworm",
    "model_proxy": "ghcr.io/berriai/litellm-database:v1.86.2",
    "cloudflared": "cloudflare/cloudflared:2026.7.2",
}
_外部镜像环境变量 = {
    "postgres": "XJ_POSTGRES_IMAGE",
    "model_proxy": "XJ_LITELLM_IMAGE",
    "cloudflared": "XJ_CLOUDFLARED_IMAGE",
}
_外部镜像容器 = {
    "postgres": "xj-postgres",
    "model_proxy": "xj-model-proxy",
    "cloudflared": "xj-cloudflared",
}
_CLOUDFLARE_READY_URL = "http://127.0.0.1:37658/ready"

_平台字段 = {
    "public-provider": "公网入口方式（local / cloudflare / tailscale / sunny）",
    "public-url": "用户访问公司的公网地址",
    "openai-base": "OpenAI 兼容接口地址",
    "openai-key": "OpenAI 兼容接口密钥",
    "qwen-base": "Qwen 兼容接口地址",
    "qwen-key": "Qwen 兼容接口密钥",
    "glm-base": "GLM 兼容接口地址",
    "glm-key": "GLM 兼容接口密钥",
    "deepseek-base": "DeepSeek 兼容接口地址",
    "deepseek-key": "DeepSeek 兼容接口密钥",
    "dashscope-real-key": "DashScope 真实搜索密钥",
    "embed-usd-per-mtokens": "embedding 每百万 token 美元单价",
}

# 只供仍在运行的封存旧版本使用；开发版已经改为本机精排，不再登记这项配置。
_兼容平台字段 = {
    "rerank-usd-per-mtokens": "封存旧版本的云端精排单价",
}

_公网字段 = {
    "cloudflare-tunnel-token": "Cloudflare Tunnel 令牌",
    "sunny-token": "SunnyNgrok 客户端授权令牌",
}
_公网方式 = {"local", "cloudflare", "tailscale", "sunny"}

_组合环境 = {
    "XJ_POSTGRES_ADMIN_PASSWORD": "postgres-admin",
    "XJ_LITELLM_DB_PASSWORD": "litellm-db-password",
    "XJ_SEARCH_DB_PASSWORD": "search-db-password",
    "XJ_LITELLM_MASTER_KEY": "litellm-master",
    "XJ_SEARCH_ADMIN_TOKEN": "search-admin",
    "XJ_SUPERVISOR_TOKEN": "supervisor-token",
    "XJ_OPENAI_API_BASE": "openai-base",
    "XJ_OPENAI_API_KEY": "openai-key",
    "XJ_QWEN_API_BASE": "qwen-base",
    "XJ_QWEN_API_KEY": "qwen-key",
    "XJ_GLM_API_BASE": "glm-base",
    "XJ_GLM_API_KEY": "glm-key",
    "XJ_DEEPSEEK_API_BASE": "deepseek-base",
    "XJ_DEEPSEEK_API_KEY": "deepseek-key",
    "XJ_DASHSCOPE_REAL_KEY": "dashscope-real-key",
    "XJ_EMBED_USD_PER_MTOKENS": "embed-usd-per-mtokens",
    "XJ_RERANK_TOKEN": "rerank-token",
}

_隐藏输入 = {
    "openai-key", "qwen-key", "glm-key", "deepseek-key", "dashscope-real-key",
    "cloudflare-tunnel-token", "sunny-token",
}


def _读(name: str) -> str:
    try:
        return 读取平台(name)
    except 凭据不存在 as exc:
        descriptions = {**_平台字段, **_兼容平台字段, **_公网字段}
        raise RuntimeError(f"缺少平台配置：{name}（{descriptions.get(name, '内部凭据')}）") from exc


def _验证(name: str, value: str) -> None:
    if name == "public-provider" and value not in _公网方式:
        raise RuntimeError("public-provider 只能是 local、cloudflare、tailscale 或 sunny")
    if name.endswith("-base"):
        parsed = urlsplit(value.rstrip("/"))
        approved_http = name == "openai-base" and value.rstrip("/") == "http://192.0.2.10/v1"
        if not approved_http and (
            parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
        ):
            raise RuntimeError(f"{name} 必须是不含账号、查询参数的 HTTPS 地址")
    if name == "public-url":
        if value.rstrip("/") == "http://localhost:37656":
            return
        parsed = urlsplit(value.rstrip("/"))
        try:
            port = parsed.port
        except ValueError as exc:
            raise RuntimeError("public-url 不是有效的 HTTPS 地址") from exc
        if (
            parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment or (port is not None and port != 443)
        ):
            raise RuntimeError("public-url 必须是公司入口的 HTTPS 根地址")
    if name in {"embed-usd-per-mtokens", "rerank-usd-per-mtokens"}:
        try:
            price = Decimal(value)
        except InvalidOperation as exc:
            raise RuntimeError(f"{name} 不是有效单价") from exc
        if not price.is_finite() or price <= 0:
            raise RuntimeError(f"{name} 必须大于 0")


def 初始化内部凭据() -> dict[str, object]:
    created: list[str] = []
    for name, factory in _内部凭据.items():
        try:
            读取平台(name)
        except 凭据不存在:
            保存平台(name, factory())
            created.append(name)
    missing = []
    for name in _平台字段:
        try:
            读取平台(name)
        except 凭据不存在:
            missing.append(name)
    return {"created": created, "missing": missing}


def 首次设置() -> None:
    result = 初始化内部凭据()
    missing = list(result["missing"])
    if not missing:
        print("平台配置已经齐全。")
        return
    print(f"需要补充 {len(missing)} 项配置；已经存在的配置不会被覆盖。")
    for name in missing:
        while True:
            prompt = f"{_平台字段[name]}："
            value = (getpass.getpass(prompt) if name in _隐藏输入 else input(prompt)).strip()
            if name == "public-url" or name.endswith("-base"):
                value = value.rstrip("/")
            try:
                _验证(name, value)
            except RuntimeError as exc:
                print(f"输入无效：{exc}")
                continue
            保存平台(name, value)
            break
    print("平台配置已存入 macOS 钥匙串。")


def 检查平台配置(*, provider: str | None = None) -> dict[str, int | bool]:
    provider = provider or _读("public-provider")
    _验证("public-provider", provider)
    names = [*_内部凭据, *_平台字段]
    if provider == "cloudflare":
        names.append("cloudflare-tunnel-token")
    elif provider == "sunny":
        names.append("sunny-token")
    for name in names:
        value = _读(name)
        if not value or len(value) > 4096 or any(ord(c) < 32 for c in value):
            raise RuntimeError(f"平台配置无效：{name}")
        _验证(name, value)
    return {"ok": True, "count": len(names)}


def _读取Compose(compose_file: Path) -> dict[str, object]:
    try:
        document = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError("共享平台 Compose 配置无法读取") from exc
    if not isinstance(document, dict):
        raise RuntimeError("共享平台 Compose 配置格式无效")
    return document


def _Compose变量(compose_file: Path) -> set[str]:
    """找出指定版本 Compose 真正引用的变量，兼容旧封存版本的配置边界。"""
    document = _读取Compose(compose_file)
    variables: set[str] = set()
    pattern = re.compile(r"\$\{([A-Z][A-Z0-9_]*)")

    def visit(value: object) -> None:
        if isinstance(value, str):
            variables.update(pattern.findall(value))
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(document)
    return variables


def _compose_env(
    *,
    provider: str | None = None,
    release: dict[str, object] | None = None,
    compose_file: Path | None = None,
) -> dict[str, str]:
    provider = provider or _读("public-provider")
    检查平台配置(provider=provider)
    if compose_file is None:
        compose_file = COMPOSE
    compose_file = compose_file.resolve()
    env = os.environ.copy()
    env.update({env_name: _读(keychain_name) for env_name, keychain_name in _组合环境.items()})
    # 旧封存版的搜索网关仍声明云端精排单价；只在该版本 Compose 明确引用时提供。
    if "XJ_RERANK_USD_PER_MTOKENS" in _Compose变量(compose_file):
        legacy_rerank_price = _读("rerank-usd-per-mtokens")
        _验证("rerank-usd-per-mtokens", legacy_rerank_price)
        env["XJ_RERANK_USD_PER_MTOKENS"] = legacy_rerank_price
    env["CF_TUNNEL_TOKEN"] = (
        _读("cloudflare-tunnel-token") if provider == "cloudflare" else ""
    )
    env["XJ_LOCAL_TEST_MODE"] = "1" if provider == "local" else "0"
    for name, item in _候选外部镜像(release).items():
        env[_外部镜像环境变量[name]] = str(item["id"])
    version = 当前已发布版本()
    env["XJ_PLATFORM_IMAGE"] = f"xj-platform:{version}" if version else "xj-platform:dev"
    return env


def _run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path = ROOT,
) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def _镜像信息(image: str) -> dict[str, object] | None:
    result = subprocess.run(
        [_docker_bin(), "image", "inspect", image], cwd=ROOT, stdin=subprocess.DEVNULL,
        text=True, capture_output=True, check=False,
    )
    if result.returncode != 0:
        return None
    try:
        values = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Docker 返回了无效的镜像信息：{image}") from exc
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        raise RuntimeError(f"Docker 返回了异常的镜像信息：{image}")
    return values[0]


def _候选外部镜像(release: dict[str, object] | None) -> dict[str, dict[str, object]]:
    if not isinstance(release, dict):
        return {}
    images = release.get("images") if isinstance(release.get("images"), dict) else {}
    raw = images.get("external") if isinstance(images, dict) else None
    if raw is None:
        # 已封存的旧版本没有供应链锁，继续按旧版 Compose 启动；新候选都会写入该字段。
        return {}
    if not isinstance(raw, dict) or set(raw) != set(_外部运行镜像):
        raise RuntimeError("候选外部镜像锁不完整")
    locked: dict[str, dict[str, object]] = {}
    for name, source_ref in _外部运行镜像.items():
        item = raw.get(name)
        if not isinstance(item, dict):
            raise RuntimeError(f"候选外部镜像锁无效：{name}")
        image_id = str(item.get("id") or "")
        if str(item.get("source_ref") or "") != source_ref or not _精确镜像编号.fullmatch(image_id):
            raise RuntimeError(f"候选外部镜像身份无效：{name}")
        locked[name] = dict(item)
    return locked


def _锁定外部运行镜像(
    *,
    docker: str,
    env: dict[str, str],
    log: 安全操作日志,
    on_stage: object | None = None,
    heartbeat: object | None = None,
) -> dict[str, dict[str, object]]:
    notify = on_stage if callable(on_stage) else lambda _index, _total, _label: None
    beat = heartbeat if callable(heartbeat) else None
    locked: dict[str, dict[str, object]] = {}
    total = len(_外部运行镜像)
    for index, (name, source_ref) in enumerate(_外部运行镜像.items(), start=1):
        label = {"postgres": "PostgreSQL", "model_proxy": "LiteLLM", "cloudflared": "Cloudflared"}[name]
        notify(index, total, label)
        log.运行(
            [docker, "image", "pull", source_ref], cwd=ROOT, env=env,
            phase="supply-chain", label=f"锁定外部镜像：{label}",
            retry_class="environment", timeout=1800, heartbeat=beat,
        )
        info = _镜像信息(source_ref) or {}
        image_id = str(info.get("Id") or "")
        size_bytes = int(info.get("Size") or 0)
        if not _精确镜像编号.fullmatch(image_id) or size_bytes <= 0:
            raise 候选步骤错误(f"无法锁定{label}的精确镜像身份", {
                "phase": "supply-chain", "step": f"锁定外部镜像：{label}",
                "summary": f"无法锁定{label}的精确镜像身份", "exit_code": None,
                "failed_checks": [label],
                "next_action": "恢复 Docker 镜像仓库访问后，重新生成候选。",
                "account_effect": "未触碰任何账号；当前正式版本继续运行。",
                "retry_class": "environment",
            })
        repo_digests = sorted({
            str(value) for value in (info.get("RepoDigests") or [])
            if isinstance(value, str) and "@sha256:" in value
        })
        locked[name] = {
            "source_ref": source_ref,
            "id": image_id,
            "repo_digests": repo_digests,
            "os": str(info.get("Os") or ""),
            "architecture": str(info.get("Architecture") or ""),
            "size_bytes": size_bytes,
        }
    return locked


def _镜像存在(image: str) -> bool:
    return _镜像信息(image) is not None


def _镜像标签匹配(image: str, *, version: str, source_sha256: str) -> bool:
    info = _镜像信息(image)
    if info is None:
        return False
    config = info.get("Config") if isinstance(info.get("Config"), dict) else {}
    labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
    return (
        labels.get("org.xj.company.version") == version
        and labels.get("org.xj.company.formal-source-sha256") == source_sha256
    )


def _开发源指纹() -> str:
    result = subprocess.run(
        [
            "git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others",
            "--exclude-standard", "--", *DEV_SOURCE_PATHS,
        ],
        stdin=subprocess.DEVNULL, capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("无法列出当前开发代码")
    relatives = sorted({
        value.decode("utf-8")
        for value in result.stdout.split(b"\0")
        if value and value.decode("utf-8") != "多用户/部署/正式版.lock.json"
    })
    digest = hashlib.sha256()
    for relative in relatives:
        path = ROOT / relative
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"开发代码含无效文件：{relative}")
        content = path.read_bytes()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(b"x" if path.stat().st_mode & 0o111 else b"-")
        digest.update(len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


def _运行镜像(version: str) -> list[str]:
    return [f"xj-company:{version}", f"xj-runner:{version}", f"xj-platform:{version}"]


def _镜像组件(identifier: str) -> dict[str, str]:
    return dict(zip(("company", "runner", "platform"), _运行镜像(identifier), strict=True))


def _文件哈希(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _现在() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _docker_bin() -> str:
    return os.environ.get("XJ_DOCKER_BIN", "docker").strip() or "docker"


def _镜像对应发布(image: str) -> dict[str, object] | None:
    version = str(image or "").rsplit(":", 1)[-1]
    return None if not version or version == "dev" else 读取正式版本(version)


def _发布Compose(release: dict[str, object] | None = None, *, image: str = "") -> Path:
    if release is None and image:
        release = _镜像对应发布(image)
    context = ROOT if release is None else Path(str(release.get("build_context") or "")).resolve()
    deploy = context / "多用户" / "部署"
    compose = deploy / "compose.yaml"
    required = (compose, deploy / "litellm-config.yaml", deploy / "postgres-init" / "01-create-databases.sh")
    if not context.is_dir() or any(not path.is_file() or path.is_symlink() for path in required):
        raise RuntimeError("发布包中的共享平台配置不完整")
    return compose


def _Compose服务(compose_file: Path) -> dict[str, dict[str, object]]:
    """读取指定发布包自己的服务表，避免开发版服务清单冒充旧正式版。"""
    document = _读取Compose(compose_file)
    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, dict):
        raise RuntimeError("共享平台 Compose 缺少服务清单")
    return {
        str(name): dict(config)
        for name, config in services.items()
        if isinstance(config, dict)
    }


def _发布平台服务与容器(compose_file: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """按发布包声明找平台服务；旧版没有本地精排服务也应能正常运行。"""
    pairs = []
    for name, config in _Compose服务(compose_file).items():
        image = config.get("image")
        container = config.get("container_name")
        if (
            isinstance(image, str)
            and "XJ_PLATFORM_IMAGE" in image
            and isinstance(container, str)
            and container
        ):
            pairs.append((name, container))
    if not pairs:
        raise RuntimeError("共享平台 Compose 没有可用的平台服务")
    return tuple(name for name, _container in pairs), tuple(
        container for _name, container in pairs
    )


def _清单镜像就绪(release: dict[str, object], *, final_tags: bool) -> bool:
    images = release.get("images") if isinstance(release.get("images"), dict) else {}
    components = images.get("components") if isinstance(images, dict) else {}
    version = str(release.get("version") or "")
    if not isinstance(components, dict):
        return False
    for name in ("company", "runner", "platform"):
        item = components.get(name)
        if not isinstance(item, dict):
            return False
        tag = f"xj-{name}:{version}" if final_tags else str(item.get("candidate_tag") or "")
        info = _镜像信息(tag)
        if info is None or str(info.get("Id") or "") != str(item.get("id") or ""):
            return False
    for item in _候选外部镜像(release).values():
        image_id = str(item["id"])
        info = _镜像信息(image_id)
        if info is None or str(info.get("Id") or "") != image_id:
            return False
    return True


def _恢复清单镜像(release: dict[str, object]) -> None:
    if _清单镜像就绪(release, final_tags=False):
        return
    release_path = Path(str(release.get("path") or "")).resolve()
    images = release.get("images") if isinstance(release.get("images"), dict) else {}
    archive = (release_path / str(images.get("archive") or "")).resolve()
    if release_path not in archive.parents or not archive.is_file():
        raise RuntimeError("版本镜像归档缺失")
    _run([_docker_bin(), "image", "load", "--input", str(archive)])
    components = images.get("components") if isinstance(images, dict) else {}
    for name in ("company", "runner", "platform"):
        item = components.get(name) if isinstance(components, dict) else None
        if not isinstance(item, dict):
            raise RuntimeError("版本镜像清单不完整")
        image_id = str(item.get("id") or "")
        candidate_tag = str(item.get("candidate_tag") or "")
        info = _镜像信息(image_id)
        if info is None or str(info.get("Id") or "") != image_id:
            raise RuntimeError(f"归档中的{name}镜像身份不匹配")
        _run([_docker_bin(), "image", "tag", image_id, candidate_tag])
    for name, item in _候选外部镜像(release).items():
        image_id = str(item["id"])
        info = _镜像信息(image_id)
        if info is None or str(info.get("Id") or "") != image_id:
            raise RuntimeError(f"归档中的外部镜像身份不匹配：{name}")
    if not _清单镜像就绪(release, final_tags=False):
        raise RuntimeError("镜像归档恢复后校验失败")


def 提升版本镜像(release: dict[str, object]) -> dict[str, str]:
    version = str(release.get("version") or "")
    if not version:
        raise RuntimeError("正式版本缺少内部标识")
    _恢复清单镜像(release)
    images = release.get("images") if isinstance(release.get("images"), dict) else {}
    components = images.get("components") if isinstance(images, dict) else {}
    final = _镜像组件(version)
    for name, target in final.items():
        item = components.get(name) if isinstance(components, dict) else None
        if not isinstance(item, dict):
            raise RuntimeError("正式版本镜像清单不完整")
        image_id = str(item.get("id") or "")
        existing = _镜像信息(target)
        if existing is not None and str(existing.get("Id") or "") != image_id:
            raise RuntimeError(f"正式版本标签已指向其他内容，拒绝覆盖：{target}")
        if existing is None:
            _run([_docker_bin(), "image", "tag", image_id, target])
    if not _清单镜像就绪(release, final_tags=True):
        raise RuntimeError("正式版本镜像提升后校验失败")
    return final


def 撤销未激活版本镜像(release: dict[str, object]) -> None:
    """只移除本次未激活发布留下的正式标签，标签指向异常内容时拒绝碰它。"""
    version = str(release.get("version") or "")
    images = release.get("images") if isinstance(release.get("images"), dict) else {}
    components = images.get("components") if isinstance(images, dict) else {}
    if not version or not isinstance(components, dict):
        raise RuntimeError("未激活版本镜像清单不完整")
    targets: list[tuple[str, str, str]] = []
    for name in ("company", "runner", "platform"):
        item = components.get(name)
        if not isinstance(item, dict):
            raise RuntimeError("未激活版本镜像清单不完整")
        target = f"xj-{name}:{version}"
        image_id = str(item.get("id") or "")
        if not image_id.startswith("sha256:"):
            raise RuntimeError(f"未激活{name}镜像身份无效")
        info = _镜像信息(target)
        if info is None:
            continue
        if str(info.get("Id") or "") != image_id:
            raise RuntimeError(f"正式标签指向其他内容，拒绝清理：{target}")
        targets.append((name, target, image_id))
    for _name, target, _image_id in targets:
        _run([_docker_bin(), "image", "rm", target])
        if _镜像信息(target) is not None:
            raise RuntimeError(f"未激活正式标签清理后仍存在：{target}")


def 确保正式镜像(version: str) -> bool:
    release = 读取正式版本(version)
    if _清单镜像就绪(release, final_tags=True):
        return False
    提升版本镜像(release)
    return True


def 验收并构建候选(
    candidate_id: str,
    *,
    on_stage: object | None = None,
) -> dict[str, object]:
    notify = on_stage if callable(on_stage) else lambda _status, _stage: None
    candidate = 读取候选(candidate_id)
    acceptance = candidate.get("acceptance") if isinstance(candidate.get("acceptance"), dict) else {}
    images = candidate.get("images") if isinstance(candidate.get("images"), dict) else {}
    if acceptance.get("status") == "passed" and images.get("status") == "ready":
        return candidate

    candidate_path = Path(str(candidate["path"])).resolve()
    log_path = candidate_path / "release.log"
    sensitive_values = [
        value for name, value in os.environ.items()
        if re.search(r"(?i)(?:token|secret|password|api.?key|credential|authorization)", name)
    ]
    docker = _docker_bin()
    env = 最小验收环境(os.environ.copy(), docker_bin=docker)
    log = 安全操作日志(log_path, known_secrets=sensitive_values)
    candidate_source = Path(str(candidate["build_context"])).resolve()
    try:
        acceptance_started = _现在()
        steps: list[dict[str, object]] = []

        progress = {"status": "验收中", "stage": "正在检查发布环境"}

        def announce(status: str, stage: str) -> None:
            progress.update({"status": status, "stage": stage})
            notify(status, stage)

        def heartbeat() -> None:
            notify(progress["status"], progress["stage"])

        with log:
            announce("验收中", "正在检查发布环境")
            更新候选(candidate_id, {"acceptance": {
                "status": "running", "started_at": acceptance_started,
                "stage": "发布环境预检", "steps": steps, "log": {"file": "release.log"},
            }})
            tools = 预检发布环境(
                candidate_source, python=sys.executable, env=env, log=log, heartbeat=heartbeat,
            )

            def code_stage(index: int, total: int, code: str, label: str) -> None:
                announce("验收中", f"代码验收 {index}/{total}：{label}")
                更新候选(candidate_id, {"acceptance": {
                    "status": "running", "started_at": acceptance_started,
                    "stage": label, "step": index, "total_steps": total,
                    "steps": steps, "log": {"file": "release.log"},
                }})

            steps = 执行代码验收(
                candidate_source, tools=tools, env=env, log=log,
                on_step=code_stage, heartbeat=heartbeat,
            )
            更新候选(candidate_id, {"acceptance": {
                "status": "running", "started_at": acceptance_started, "checks_passed_at": _现在(),
                "runner": "多用户.部署.候选验收", "steps": steps,
                "log": {"file": "release.log"},
            }})

            images_started = _现在()
            announce("构建中", "正在锁定外部运行镜像 1/3：PostgreSQL")
            更新候选(candidate_id, {"images": {
                "status": "building", "started_at": images_started, "stage": "锁定外部运行镜像",
            }})
            external = _锁定外部运行镜像(
                docker=docker, env=env, log=log,
                on_stage=lambda index, total, label: announce(
                    "构建中", f"正在锁定外部运行镜像 {index}/{total}：{label}",
                ),
                heartbeat=heartbeat,
            )
            更新候选(candidate_id, {"images": {
                "status": "building", "started_at": images_started,
                "stage": "外部运行镜像已经锁定", "external": external,
            }})
            candidate = 读取候选(candidate_id)
            source = candidate.get("source") if isinstance(candidate.get("source"), dict) else {}
            release = {
                "version": candidate_id,
                "formal_source_sha256": str(source.get("sha256") or ""),
                "build_context": candidate["build_context"],
            }

            build_index = 0

            def run_build(command: list[str], *, label: str, code: str, timeout: int = 3600) -> None:
                nonlocal build_index
                build_index += 1
                announce("构建中", f"正在构建候选镜像 {build_index}/3：{label}")
                更新候选(candidate_id, {"images": {
                    "status": "building", "started_at": images_started,
                    "stage": label, "step": build_index, "total_steps": 3,
                    "external": external,
                }})
                log.运行(
                    command, cwd=ROOT, env=env, phase="image-build", label=label,
                    retry_class="source-fix", timeout=timeout, heartbeat=heartbeat,
                )

            构建镜像(release, command_runner=run_build)
            components: dict[str, dict[str, str]] = {}
            for name, tag in _镜像组件(candidate_id).items():
                info = _镜像信息(tag) or {}
                image_id = str(info.get("Id") or "")
                size_bytes = int(info.get("Size") or 0)
                if not _精确镜像编号.fullmatch(image_id) or size_bytes <= 0:
                    raise RuntimeError(f"无法确认{name}候选镜像身份")
                components[name] = {
                    "id": image_id, "candidate_tag": tag, "size_bytes": size_bytes,
                }

            archive_space = 检查镜像归档空间(
                candidate_path,
                [
                    *[int(components[name]["size_bytes"]) for name in ("company", "runner", "platform")],
                    *[int(external[name]["size_bytes"]) for name in _外部运行镜像],
                ],
            )

            announce("构建中", "正在隔离启动三个候选运行入口")
            更新候选(candidate_id, {"images": {
                "status": "building", "started_at": images_started, "stage": "隔离运行时检查",
                "external": external, "archive_space": archive_space,
            }})
            runtime_checks = 执行隔离运行时检查(
                docker, components, cwd=ROOT, env=env, log=log,
                on_step=lambda index, total, label: announce(
                    "构建中", f"隔离运行时检查 {index}/{total}：{label}",
                ),
                heartbeat=heartbeat,
            )

            announce("构建中", "正在封存并复核镜像归档")
            更新候选(candidate_id, {"images": {
                "status": "building", "started_at": images_started, "stage": "镜像归档复核",
                "external": external, "runtime_checks": runtime_checks,
                "archive_space": archive_space,
            }})
            archive = candidate_path / "images.tar"
            temporary_archive = candidate_path / f".images.{secrets.token_hex(6)}.tar"
            try:
                log.运行(
                    [docker, "image", "save", "--output", str(temporary_archive),
                     *[components[name]["id"] for name in ("company", "runner", "platform")],
                     *[external[name]["id"] for name in _外部运行镜像]],
                    cwd=ROOT, env=env, phase="image-archive", label="保存候选镜像及锁定的外部镜像",
                    retry_class="environment", timeout=1800, heartbeat=heartbeat,
                )
                temporary_archive.replace(archive)
                log.运行(
                    [docker, "image", "load", "--input", str(archive)],
                    cwd=ROOT, env=env, phase="image-archive", label="读取并复核镜像归档",
                    retry_class="environment", timeout=1800, heartbeat=heartbeat,
                )
                for name, item in components.items():
                    restored = _镜像信息(item["id"]) or {}
                    if str(restored.get("Id") or "") != item["id"]:
                        raise RuntimeError(f"{name}镜像归档恢复后的身份不匹配")
                    _run([docker, "image", "tag", item["id"], item["candidate_tag"]])
                for name, item in external.items():
                    restored = _镜像信息(str(item["id"])) or {}
                    if str(restored.get("Id") or "") != item["id"]:
                        raise RuntimeError(f"外部镜像归档恢复后的身份不匹配：{name}")
            finally:
                temporary_archive.unlink(missing_ok=True)

        log_record = log.摘要(complete=True)
        acceptance = dict((读取候选(candidate_id).get("acceptance") or {}))
        acceptance.update({
            "status": "passed", "finished_at": _现在(), "log": log_record,
        })
        更新候选(candidate_id, {"acceptance": acceptance})
        return 更新候选(candidate_id, {"images": {
            "status": "ready", "started_at": images_started, "built_at": _现在(),
            "archive": "images.tar", "archive_sha256": _文件哈希(archive),
            "components": components, "external": external,
            "runtime_checks": runtime_checks, "runtime_checked": True,
            "archive_restored": True, "archive_space": archive_space,
        }})
    except Exception as exc:
        # 失败候选不再保留近 1GB 的不可发布镜像归档。
        # 源码、manifest 和 release.log 仍保留，所以失败原因可追查。
        failed_archive = candidate_path / "images.tar"
        if failed_archive.is_file() and not failed_archive.is_symlink():
            failed_archive.unlink(missing_ok=True)
        try:
            log_record = log.摘要(complete=True) if log_path.is_file() else {"file": "release.log", "complete": False}
        except Exception:
            log_record = {"file": "release.log", "complete": False}
        failure = dict(getattr(exc, "details", {}) or {})
        if not failure:
            failure = {
                "phase": "release-system", "step": "候选制造", "summary": str(exc)[:500],
                "exit_code": None, "failed_checks": [],
                "next_action": "根据候选日志修正问题后，重新生成候选。",
                "account_effect": "未触碰任何账号；当前正式版本继续运行。",
                "retry_class": "source-fix",
            }
        try:
            current = 读取候选(candidate_id, verify=False)
            current_acceptance = dict(current.get("acceptance") or {})
            current_images = dict(current.get("images") or {})
            current_acceptance["log"] = log_record
            if current_images.get("status") == "building":
                current_images.update({"status": "failed", "finished_at": _现在(), "failure": failure})
                更新候选(candidate_id, {"acceptance": current_acceptance, "images": current_images})
            else:
                current_acceptance.update({
                    "status": "failed", "finished_at": _现在(), "failure": failure,
                })
                更新候选(candidate_id, {"acceptance": current_acceptance})
        except Exception:
            pass
        if isinstance(exc, 候选步骤错误):
            raise
        raise 候选步骤错误(str(failure["summary"]), failure) from exc


def 构建镜像(
    release: dict[str, object] | None = None,
    *,
    command_runner: object | None = None,
) -> bool:
    development = False
    if release is None:
        version = 当前已发布版本()
        if version:
            return 确保正式镜像(version)
        development = True
        release = {
            "version": "dev",
            "formal_source_sha256": _开发源指纹(),
            "build_context": str(ROOT),
        }
    version = str(release.get("version") or "")
    source_sha256 = str(release.get("formal_source_sha256") or "")
    context_root = Path(str(release.get("build_context") or "")).resolve()
    if not version or not source_sha256 or not context_root.is_dir():
        raise RuntimeError("发布构建信息无效")
    deploy = context_root / "多用户" / "部署"
    if not (deploy / "Dockerfile.公司").is_file() or not (deploy / "Dockerfile.平台").is_file():
        raise RuntimeError("发布构建副本不完整")
    images = _运行镜像(version)
    existing = [image for image in images if _镜像存在(image)]
    if len(existing) == len(images):
        mismatched = [
            image for image in images
            if not _镜像标签匹配(image, version=version, source_sha256=source_sha256)
        ]
        if not mismatched:
            print(f"版本 {version} 的镜像已经存在，不重复覆盖。")
            return False
        if not development:
            raise RuntimeError(f"同名镜像的版本信息不匹配，拒绝使用：{mismatched[0]}")
    if existing and not development:
        raise RuntimeError("该版本只有部分镜像存在，拒绝覆盖；请使用新的版本号重新发布")
    build_images = images
    if development:
        suffix = secrets.token_hex(6)
        build_images = [f"{image}-candidate-{suffix}" for image in images]
    common = [
        _docker_bin(), "build", "--file", str(deploy / "Dockerfile.公司"),
        "--build-arg", f"XJ_COMPANY_VERSION={version}",
        "--build-arg", f"XJ_FORMAL_SOURCE_SHA256={source_sha256}",
    ]
    run = command_runner if callable(command_runner) else (
        lambda command, **_kwargs: _run(command)
    )
    try:
        run([
            *common, "--target", "company",
            "--tag", build_images[0], str(context_root),
        ], label="用户公司镜像", code="company")
        run([
            *common, "--target", "runner",
            "--tag", build_images[1], str(context_root),
        ], label="任务执行器镜像", code="runner")
        run([
            _docker_bin(), "build", "--file", str(deploy / "Dockerfile.平台"),
            "--build-arg", f"XJ_COMPANY_VERSION={version}",
            "--build-arg", f"XJ_FORMAL_SOURCE_SHA256={source_sha256}",
            "--tag", build_images[2], str(context_root),
        ], label="共享平台镜像", code="platform")
        if development:
            for candidate in build_images:
                if not _镜像标签匹配(candidate, version=version, source_sha256=source_sha256):
                    raise RuntimeError("开发镜像构建后的代码指纹不匹配")
            for candidate, image in zip(build_images, images, strict=True):
                _run([_docker_bin(), "image", "tag", candidate, image])
    except Exception:
        subprocess.run(
            [_docker_bin(), "image", "rm", "--force", *build_images],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        raise
    if development:
        subprocess.run(
            [_docker_bin(), "image", "rm", *build_images],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
    return True


def _镜像齐全() -> bool:
    version = 当前已发布版本() or "dev"
    if version != "dev":
        try:
            return _清单镜像就绪(读取正式版本(version), final_tags=True)
        except Exception:
            return False
    images = _运行镜像(version)
    if not all(_镜像存在(image) for image in images):
        return False
    source_sha256 = _开发源指纹()
    return all(
        _镜像标签匹配(image, version="dev", source_sha256=source_sha256)
        for image in images
    )


def 运行镜像当前() -> bool:
    if not _镜像齐全():
        return False
    version = 当前已发布版本() or "dev"
    image = f"xj-platform:{version}"
    release = 读取正式版本(version) if version != "dev" else None
    compose_file = _发布Compose(release, image=image)
    _services, containers = _发布平台服务与容器(compose_file)
    info = _镜像信息(image) or {}
    expected_id = str(info.get("Id") or "")
    if not expected_id:
        return False
    for container in containers:
        result = subprocess.run(
            [_docker_bin(), "container", "inspect", "--format", "{{.Image}}", container],
            cwd=ROOT, stdin=subprocess.DEVNULL, text=True, capture_output=True, check=False,
        )
        if result.returncode != 0 or result.stdout.strip() != expected_id:
            return False
    return True


def _compose_up(
    *services: str,
    provider: str,
    force_recreate: bool = False,
    no_deps: bool = False,
    platform_image: str | None = None,
    compose_file: Path = COMPOSE,
    release: dict[str, object] | None = None,
) -> None:
    compose_file = compose_file.resolve()
    if not compose_file.is_file() or compose_file.is_symlink():
        raise RuntimeError("共享平台 Compose 配置不存在")
    args = [_docker_bin(), "compose", "--file", str(compose_file)]
    if provider == "cloudflare":
        args.extend(["--profile", "public"])
    args.extend(["up", "--detach", "--no-build"])
    if force_recreate:
        args.append("--force-recreate")
    if no_deps:
        args.append("--no-deps")
    declared = _Compose服务(compose_file)
    selected_services = tuple(service for service in services if service in declared)
    if services and not selected_services:
        raise RuntimeError("共享平台 Compose 没有要启动的服务")
    args.extend(["--wait", "--wait-timeout", "180", *selected_services])
    env = _compose_env(provider=provider, release=release, compose_file=compose_file)
    if platform_image is not None:
        if not _平台镜像格式.fullmatch(platform_image):
            raise RuntimeError("共享平台镜像标识无效")
        env["XJ_PLATFORM_IMAGE"] = platform_image
    try:
        _run(args, env=env)
    except subprocess.CalledProcessError as exc:
        failures: list[str] = []
        services_config = _Compose服务(compose_file)
        inspected = selected_services or tuple(services_config)
        for service in inspected:
            config = services_config.get(service, {})
            container = str(config.get("container_name") or "").strip()
            if not container:
                continue
            result = subprocess.run(
                [_docker_bin(), "container", "inspect", container],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                failures.append(f"{service} 没有成功创建容器")
                continue
            try:
                records = json.loads(result.stdout)
                state = records[0].get("State", {})
            except (IndexError, AttributeError, json.JSONDecodeError):
                failures.append(f"{service} 的运行状态无法读取")
                continue
            status = str(state.get("Status") or "未知")
            health = state.get("Health") if isinstance(state.get("Health"), dict) else {}
            health_status = str(health.get("Status") or "")
            exit_code = int(state.get("ExitCode") or 0)
            runtime_error = str(state.get("Error") or "").strip()[:160]
            if health_status == "unhealthy":
                failures.append(f"{service} 未通过健康检查")
            elif status != "running":
                suffix = f"，退出码 {exit_code}" if exit_code else ""
                failures.append(f"{service} 状态为 {status}{suffix}")
            elif runtime_error:
                failures.append(f"{service} 启动错误：{runtime_error}")
        detail = "；".join(failures[:6]) or f"Docker 返回退出码 {exc.returncode}"
        raise RuntimeError(f"共享平台启动失败：{detail}") from exc


def _清理目标未声明平台容器(compose_file: Path) -> tuple[str, ...]:
    """切换版本后，只删除平台白名单中而目标 Compose 未声明的容器。

    不能用 ``compose down`` 或 ``--remove-orphans``：共享 Compose 还管理
    数据库、模型代理和公网隧道，回退时这些资源必须继续保留。固定平台容器
    名称是本模块唯一允许清理的边界，任何不在白名单中的容器都不会进入删除命令。
    """
    _services, declared_containers = _发布平台服务与容器(compose_file)
    declared = set(declared_containers)
    stale = tuple(container for container in _平台容器 if container not in declared)
    if not stale:
        return ()

    result = subprocess.run(
        [_docker_bin(), "container", "ls", "--all", "--format", "{{.Names}}"],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip()[:300]
        suffix = f"：{detail}" if detail else ""
        raise RuntimeError(f"无法核对待清理的平台容器{suffix}")
    existing = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    removed: list[str] = []
    for container in stale:
        if container not in existing:
            continue
        # 这里的名称来自上面的固定白名单，禁止把 Compose/用户输入拼入删除命令。
        _run([_docker_bin(), "container", "rm", "--force", container])
        removed.append(container)
    return tuple(removed)


def _实际运行平台镜像() -> str:
    """从实际存在的共享平台容器识别当前镜像，不依赖活动版本锁。

    发布事务会在切换平台前暂时更新活动版本锁。此时如果按锁选择
    Compose，会拿候选版的服务清单去检查仍在运行的旧版容器，导致旧版
    没有的服务被误报为缺失。这里先从平台白名单中读取真实容器，再由
    调用方按该镜像对应的正式版本选择 Compose。
    """
    refs: list[str] = []
    for container in _平台容器:
        result = subprocess.run(
            [_docker_bin(), "container", "inspect", "--format", "{{.Config.Image}}", container],
            cwd=ROOT, stdin=subprocess.DEVNULL, text=True, capture_output=True, check=False,
        )
        if result.returncode != 0:
            continue
        value = result.stdout.strip()
        if value:
            refs.append(value)
    if not refs:
        raise RuntimeError("共享平台没有可读取的运行容器")
    if not all(_平台镜像格式.fullmatch(value) for value in refs):
        raise RuntimeError("共享平台容器使用了未登记镜像")
    if len(set(refs)) != 1:
        raise RuntimeError("共享平台容器不是同一版本，拒绝继续发布")
    return refs[0]


def 当前平台镜像(*, compose_file: Path | None = None) -> str:
    """读取指定发布包的平台容器镜像；不一致时拒绝继续。"""
    if compose_file is None:
        # The active lock can already point at a candidate while a deployment
        # is in progress. Resolve the running image first, then use that
        # image's own Compose service list.
        running_image = _实际运行平台镜像()
        release = _镜像对应发布(running_image)
        compose_file = _发布Compose(release, image=running_image)
    _services, containers = _发布平台服务与容器(compose_file)
    refs: list[str] = []
    for container in containers:
        result = subprocess.run(
            [_docker_bin(), "container", "inspect", "--format", "{{.Config.Image}}", container],
            cwd=ROOT, stdin=subprocess.DEVNULL, text=True, capture_output=True, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"共享平台容器不存在或无法读取：{container}")
        value = result.stdout.strip()
        if not _平台镜像格式.fullmatch(value):
            raise RuntimeError(f"共享平台容器使用了未登记镜像：{container}")
        refs.append(value)
    if len(set(refs)) != 1:
        raise RuntimeError("共享平台容器不是同一版本，拒绝继续发布")
    return refs[0]


def _模型配置来源() -> Path:
    result = subprocess.run(
        [_docker_bin(), "container", "inspect", "xj-model-proxy"],
        cwd=ROOT, stdin=subprocess.DEVNULL, text=True, capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("模型代理容器不存在或无法读取")
    try:
        records = json.loads(result.stdout)
        mounts = records[0]["Mounts"]
        source = next(
            str(item["Source"])
            for item in mounts
            if item.get("Destination") == "/app/config.yaml"
        )
    except (IndexError, KeyError, StopIteration, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("模型代理没有使用已登记的配置文件") from exc
    path = Path(source).resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("模型代理配置来源不存在或不是普通文件")
    return path


def _版本切换服务(provider: str) -> tuple[str, ...]:
    services = ("model-proxy", *_平台服务)
    return (*services, "cloudflared") if provider == "cloudflare" else services


def _核对外部运行镜像(release: dict[str, object] | None, provider: str) -> None:
    locked = _候选外部镜像(release)
    if locked:
        names = ["postgres", "model_proxy"]
        if provider == "cloudflare":
            names.append("cloudflared")
        for name in names:
            result = subprocess.run(
                [
                    _docker_bin(), "container", "inspect", "--format",
                    "{{.Config.Image}}\n{{.Image}}", _外部镜像容器[name],
                ],
                cwd=ROOT, stdin=subprocess.DEVNULL, text=True, capture_output=True, check=False,
            )
            values = result.stdout.splitlines() if result.returncode == 0 else []
            expected = str(locked[name]["id"])
            if len(values) != 2 or values[0].strip() != expected or values[1].strip() != expected:
                raise RuntimeError(f"外部运行镜像没有使用候选锁定身份：{name}")
    if provider == "cloudflare":
        _等待Cloudflare隧道就绪()


def _等待Cloudflare隧道就绪(*, timeout: int = 60) -> None:
    """Cloudflare 官方 /ready 只有隧道真实连上时才返回 200。"""
    deadline = time.monotonic() + timeout
    last_error = "未返回 200"
    while time.monotonic() < deadline:
        try:
            with urllib_request.urlopen(_CLOUDFLARE_READY_URL, timeout=2) as response:
                if int(getattr(response, "status", 0)) == 200:
                    return
                last_error = f"HTTP {getattr(response, 'status', '未知')}"
        except Exception as exc:  # 网络尚在建立时短暂失败，继续轮询到硬超时。
            last_error = type(exc).__name__
        time.sleep(1)
    raise RuntimeError(f"Cloudflare 隧道在 {timeout} 秒内未就绪（/ready：{last_error}）")


def _核对平台来源(
    target_image: str,
    compose_file: Path,
    *,
    release: dict[str, object] | None = None,
    provider: str | None = None,
) -> None:
    if 当前平台镜像(compose_file=compose_file) != target_image:
        raise RuntimeError("共享平台切换后版本核对失败")
    expected_config = (compose_file.parent / "litellm-config.yaml").resolve()
    if _模型配置来源() != expected_config:
        raise RuntimeError("模型代理仍在使用其他版本的配置文件")
    _核对外部运行镜像(release, provider or _读("public-provider"))


def _切换Compose堆栈(
    *,
    provider: str,
    platform_image: str,
    compose_file: Path,
    release: dict[str, object] | None,
) -> None:
    # 数据库先独立健康，再启动依赖它的模型代理和其余共享服务。
    _compose_up(
        "postgres", provider=provider, no_deps=True,
        platform_image=platform_image, compose_file=compose_file, release=release,
    )
    _compose_up(
        *_版本切换服务(provider), provider=provider, force_recreate=True, no_deps=True,
        platform_image=platform_image, compose_file=compose_file, release=release,
    )
    # Compose 重建共享容器时会丢失后来动态加入的用户隔离网络。
    # 必须在新网关就绪后、切换成功前恢复全部现有用户连接。
    _恢复用户网络()
    # 必须等目标服务通过 Compose 的 --wait 后再移除旧版本专属平台容器，
    # 这样失败回退仍有完整旧运行面可接管。
    _清理目标未声明平台容器(compose_file)


def 切换平台版本(
    target_image: str,
    *,
    release: dict[str, object] | None = None,
) -> dict[str, str | bool]:
    """按目标版本的 Compose 服务切换共享平台，并在失败时尝试恢复原镜像。"""
    if not _平台镜像格式.fullmatch(target_image):
        raise RuntimeError("目标共享平台镜像标识无效")
    previous = 当前平台镜像()
    target_release = release or _镜像对应发布(target_image)
    target_compose = _发布Compose(target_release, image=target_image)
    provider = _读("public-provider")
    if previous == target_image:
        try:
            _核对平台来源(
                target_image, target_compose, release=target_release, provider=provider,
            )
            return {"previous_image": previous, "target_image": target_image, "changed": False}
        except RuntimeError:
            _切换Compose堆栈(
                provider=provider, platform_image=target_image,
                compose_file=target_compose, release=target_release,
            )
            _核对平台来源(
                target_image, target_compose, release=target_release, provider=provider,
            )
            return {
                "previous_image": previous, "target_image": target_image,
                "changed": True, "configuration_repaired": True,
            }
    if _镜像信息(target_image) is None:
        raise RuntimeError(f"目标共享平台镜像不存在：{target_image}")
    previous_release = _镜像对应发布(previous)
    previous_compose = _发布Compose(previous_release, image=previous)
    try:
        _切换Compose堆栈(
            provider=provider, platform_image=target_image,
            compose_file=target_compose, release=target_release,
        )
        _核对平台来源(
            target_image, target_compose, release=target_release, provider=provider,
        )
    except Exception as exc:
        try:
            _切换Compose堆栈(
                provider=provider, platform_image=previous,
                compose_file=previous_compose, release=previous_release,
            )
            _核对平台来源(
                previous, previous_compose, release=previous_release, provider=provider,
            )
        except Exception as rollback_exc:
            raise RuntimeError(f"共享平台切换失败，自动恢复也失败：{rollback_exc}") from exc
        raise
    return {"previous_image": previous, "target_image": target_image, "changed": True}


def _读取正式回退发布(image: str) -> dict[str, object]:
    """解析管理员回退目标；开发、候选和暂存镜像一律不属于回退范围。"""
    if not isinstance(image, str) or not _平台镜像格式.fullmatch(image):
        raise RuntimeError("恢复共享平台镜像标识无效")
    version = image.rsplit(":", 1)[-1]
    if version == "dev":
        raise RuntimeError("恢复目标必须是已封存正式版本，不能恢复到开发版")
    try:
        release = _镜像对应发布(image)
    except Exception as exc:
        raise RuntimeError("恢复目标不是已封存正式版本") from exc
    if not isinstance(release, dict):
        raise RuntimeError("恢复目标不是已封存正式版本")
    raw_path = str(release.get("path") or "")
    path = Path(raw_path).expanduser().resolve()
    if path.name != version or path.parent.name != "published":
        raise RuntimeError("恢复目标不是已发布目录中的正式版本")
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise RuntimeError("恢复目标缺少正式版本清单")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("恢复目标正式版本清单无法读取") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("version") != version
        or not str(manifest.get("published_at") or "").strip()
    ):
        raise RuntimeError("恢复目标不是已发布的正式版本")
    return release


def 恢复平台版本(image: str) -> dict[str, str | bool]:
    """把共享平台恢复到指定已登记镜像；供发布失败和回退使用。"""
    release = _读取正式回退发布(image)
    compose_file = _发布Compose(release, image=image)
    try:
        current = 当前平台镜像()
    except RuntimeError:
        # A partial rollout can leave old and new platform containers mixed.
        # Recovery must still be able to replace that known whitelist with the
        # selected sealed release instead of failing before cleanup can run.
        current = ""
    provider = _读("public-provider")
    if current == image:
        try:
            _核对平台来源(image, compose_file, release=release, provider=provider)
            return {"previous_image": current, "target_image": image, "changed": False}
        except RuntimeError:
            pass
    if _镜像信息(image) is None:
        raise RuntimeError(f"恢复用共享平台镜像不存在：{image}")
    _切换Compose堆栈(
        provider=provider, platform_image=image, compose_file=compose_file, release=release,
    )
    _核对平台来源(image, compose_file, release=release, provider=provider)
    return {"previous_image": current, "target_image": image, "changed": True}


def 恢复事务平台版本(image: str) -> dict[str, str | bool]:
    """仅供同一发布事务撤销使用；允许恢复到部署前的开发平台标签。

    管理员手动回退仍只能调用 ``恢复平台版本``，不能选择开发标签。
    """
    if not isinstance(image, str) or not _平台镜像格式.fullmatch(image):
        raise RuntimeError("恢复共享平台镜像标识无效")
    version = image.rsplit(":", 1)[-1]
    release = None if version == "dev" else _读取正式回退发布(image)
    compose_file = _发布Compose(release, image=image)
    try:
        current = 当前平台镜像()
    except RuntimeError:
        current = ""
    provider = _读("public-provider")
    if current == image:
        _核对平台来源(image, compose_file, release=release, provider=provider)
        return {"previous_image": current, "target_image": image, "changed": False}
    if _镜像信息(image) is None:
        raise RuntimeError(f"事务恢复用共享平台镜像不存在：{image}")
    _切换Compose堆栈(
        provider=provider, platform_image=image, compose_file=compose_file, release=release,
    )
    _核对平台来源(image, compose_file, release=release, provider=provider)
    return {"previous_image": current, "target_image": image, "changed": True}


def 确保平台版本(version: str) -> bool:
    """服务重启时让共享平台与当前正式版本锁一致。"""
    target = f"xj-platform:{version}"
    result = 恢复平台版本(target)
    return bool(result.get("changed"))


def _恢复用户网络() -> int:
    from ..控制面.Docker资源 import Docker资源

    return Docker资源().恢复共享网络()


def _发布具备三角色宿主(release: dict[str, object] | None) -> bool:
    runtime_source = ROOT if release is None else Path(str(release.get("build_context") or "")).resolve()
    return (runtime_source / "多用户" / "部署" / "宿主启动器.py").is_file()


def 升级账户(account_id: str) -> None:
    验证已发布()
    if not _镜像齐全():
        raise RuntimeError("当前正式版本镜像尚未构建完成")
    from .监督服务命令 import _环境

    env = os.environ.copy()
    env.update(_环境())
    _run(
        [sys.executable, "-m", "多用户.控制面.命令", "upgrade", account_id],
        env=env,
    )


def 启动平台(*, provider: str | None = None) -> None:
    初始化内部凭据()
    provider = provider or _读("public-provider")
    检查平台配置(provider=provider)
    public_url = _读("public-url").rstrip("/")
    if provider == "local" and public_url != "http://localhost:37656":
        raise RuntimeError("本机模式的 public-url 必须是 http://localhost:37656")
    if provider != "local" and not public_url.startswith("https://"):
        raise RuntimeError("公网模式必须先把 public-url 设置为 HTTPS 地址")
    runtime_current = 运行镜像当前()
    current_version = 当前已发布版本()
    current_release = 读取正式版本(current_version) if current_version else None
    target_image = f"xj-platform:{current_version or 'dev'}"
    compose_file = _发布Compose(current_release, image=target_image) if current_version else COMPOSE
    rebuilt = False
    if not _镜像齐全():
        print("检测到运行代码已更新，正在构建新镜像，请稍候。")
        rebuilt = 构建镜像()
    refresh_platform = rebuilt or not runtime_current
    # 新发布包才启用三角色宿主；现役旧发布包继续沿用它原有的单进程宿主。
    _compose_up(
        "postgres", "model-proxy", provider=provider,
        compose_file=compose_file, release=current_release,
    )
    if _发布具备三角色宿主(current_release):
        _run([sys.executable, "-m", "多用户.部署.监督服务命令", "install"])
    _compose_up(
        "local-rerank-bridge", "search-gw", provider=provider,
        force_recreate=refresh_platform, no_deps=refresh_platform,
        compose_file=compose_file, release=current_release,
    )
    if refresh_platform:
        _compose_up(
            "supervisor-bridge", "gw", "local-entry", provider=provider,
            force_recreate=True, no_deps=True,
            compose_file=compose_file, release=current_release,
        )
    _compose_up(provider=provider, compose_file=compose_file, release=current_release)
    _清理目标未声明平台容器(compose_file)
    _核对平台来源(target_image, compose_file, release=current_release, provider=provider)
    _恢复用户网络()
    print("多用户公司已启动。")
    print("账户管理：http://127.0.0.1:37655")
    print(f"用户入口：{public_url}")


def _compose(action: str) -> None:
    current_version = 当前已发布版本()
    current_release = 读取正式版本(current_version) if current_version else None
    compose_file = (
        _发布Compose(current_release, image=f"xj-platform:{current_version}")
        if current_version else COMPOSE
    )
    args = [_docker_bin(), "compose", "--file", str(compose_file)]
    provider = _读("public-provider")
    if provider == "cloudflare":
        args.extend(["--profile", "public"])
    if action == "up":
        args.extend(["up", "--detach", "--no-build"])
    elif action == "down":
        args.extend(["down", "--remove-orphans"])
    elif action == "ps":
        args.extend(["ps"])
    elif action == "config":
        args.extend(["config", "--quiet"])
    else:
        raise RuntimeError("不允许的 Compose 动作")
    _run(
        args,
        env=_compose_env(
            provider=provider, release=current_release, compose_file=compose_file,
        ),
    )
    if action == "up":
        _清理目标未声明平台容器(compose_file)
        _恢复用户网络()


def 发布状态() -> dict[str, object]:
    version = 当前已发布版本()
    if not version:
        return {"ok": False, "reason": "尚未成功发布任何产品版本", "version": None}
    release = 读取正式版本(version)
    images_ready = _清单镜像就绪(release, final_tags=True)
    return {
        "ok": images_ready,
        "reason": "正式版本和本机镜像一致" if images_ready else "正式版本已封存，本机镜像待恢复",
        "version": version,
        "version_name": release.get("version_name"),
        "candidate_id": release.get("candidate_id"),
        "source_sha256": (release.get("source") or {}).get("sha256"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="只生成随机内部凭据，不启动服务")
    sub.add_parser("setup", help="一次补齐首次启动所需的平台配置")
    set_parser = sub.add_parser("set", help="交互写入一项平台配置到 macOS Keychain")
    set_parser.add_argument("name", choices=sorted({**_平台字段, **_公网字段}))
    sub.add_parser("check", help="校验钥匙串中的全部平台配置")
    sub.add_parser("release-status", help="检查正式版、多用户代码和镜像版本是否一致")
    sub.add_parser("runtime-current", help="轻量检查运行容器是否对应当前代码")
    release_parser = sub.add_parser("release-sync", help="旧入口（现已停用，发布只允许从管理页确认）")
    release_parser.add_argument("--version", required=True, help="内部版本号")
    upgrade_parser = sub.add_parser("upgrade-account", help="备份并升级一个现有账户")
    upgrade_parser.add_argument("account_id")
    sub.add_parser("build", help="构建公司、验收和平台镜像")
    start_parser = sub.add_parser("start", help="一次完成构建、共享服务和账户管理服务启动")
    start_parser.add_argument(
        "--provider", choices=sorted(_公网方式),
        help="临时覆盖钥匙串中的公网入口方式",
    )
    start_parser.add_argument(
        "--public", action="store_true",
        help="兼容旧命令：等同于 --provider cloudflare",
    )
    for action in ("config", "up", "down", "ps"):
        sub.add_parser(action)
    args = parser.parse_args()

    if args.command == "init":
        result = 初始化内部凭据()
        print(f"已生成 {len(result['created'])} 项内部凭据；尚缺 {len(result['missing'])} 项外部配置。")
    elif args.command == "setup":
        首次设置()
    elif args.command == "set":
        descriptions = {**_平台字段, **_公网字段}
        value = getpass.getpass(f"请输入{descriptions[args.name]}（输入不回显）：").strip()
        _验证(args.name, value)
        保存平台(args.name, value)
        print(f"已存入 Keychain：{args.name}")
    elif args.command == "check":
        result = 检查平台配置()
        print(f"平台配置通过，共 {result['count']} 项。")
    elif args.command == "release-status":
        result = 发布状态()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["ok"]:
            return 2
    elif args.command == "runtime-current":
        current = 运行镜像当前()
        print("运行版本与当前代码一致。" if current else "运行版本需要更新。")
        if not current:
            return 2
    elif args.command == "release-sync":
        raise RuntimeError("发布只允许从用户管理页的“发布到测试”按钮执行")
    elif args.command == "upgrade-account":
        升级账户(args.account_id)
    elif args.command == "build":
        构建镜像()
    elif args.command == "start":
        if args.public and args.provider not in (None, "cloudflare"):
            parser.error("--public 不能与其他 --provider 同时使用")
        provider = "cloudflare" if args.public else args.provider
        启动平台(provider=provider)
    else:
        _compose(args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
