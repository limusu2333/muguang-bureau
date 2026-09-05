"""Build and retain immutable release candidates from one committed source tree."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .开发源 import 开发源错误, 核对开发源, 读取唯一开发源


CODE_ROOT = Path(__file__).resolve().parents[2]
# 只表示本文件所在的代码副本。生成候选时必须另行读取本机唯一登记。
ROOT = CODE_ROOT
DEPLOY = Path(__file__).resolve().parent
RUNTIME = Path.home() / "Library" / "Application Support" / "XJ Multiuser"
LOCK_PATH = Path(
    os.environ.get("XJ_RELEASE_LOCK", str(RUNTIME / "data" / "active-release.json"))
).expanduser().resolve()
LEGACY_LOCK_PATH = CODE_ROOT / "多用户" / "部署" / "正式版.lock.json"
PRODUCT_VERSION_NAME = "暮光v1.0beta版"
PRODUCT_VERSION_ID = "twilight-v1.0-beta"
DEVELOPMENT_VERSION_NAME = "开发版"
VERSION_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
CANDIDATE_PATTERN = re.compile(r"candidate-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}")
MANIFEST_SCHEMA = 2
_禁止发布的路径 = (
    ".env", ".venv/", "90_备份/", "output/", "前端/dist/", "前端/node_modules/",
    "运行状态/", "本机实例.yaml", "本机私密扫描词.txt", "多用户/部署/正式版.lock.json",
)


class 发布仓库错误(RuntimeError):
    pass


def 校验版本显示名(value: str) -> str:
    name = str(value or "").strip()
    if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
        raise 发布仓库错误("正式版本名称不能为空，且不能超过 80 个字符")
    return name


def _现在() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _仓库根(value: Path | None = None) -> Path:
    raw = value or Path(
        os.environ.get(
            "XJ_RELEASE_STORE",
            str(CODE_ROOT.parent / "90_备份" / "发布仓库"),
        )
    )
    return raw.expanduser().resolve()


def 生成候选编号() -> str:
    return datetime.now(timezone.utc).strftime("candidate-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", "-C", str(root), *args],
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["Git 操作失败"]
        raise 发布仓库错误(detail[0])
    return result.stdout.strip()


def _写JSON(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _读JSON(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise 发布仓库错误(f"版本清单无法读取：{path}") from exc
    if not isinstance(value, dict):
        raise 发布仓库错误(f"版本清单格式无效：{path}")
    return value


def _树指纹(root: Path) -> dict[str, Any]:
    if not root.is_dir() or root.is_symlink():
        raise 发布仓库错误("候选源码目录不存在或不是普通目录")
    digest = hashlib.sha256()
    count = 0
    total_size = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise 发布仓库错误(f"候选源码不允许符号链接：{relative}")
        if not path.is_file():
            continue
        content = path.read_bytes()
        executable = bool(path.stat().st_mode & 0o111)
        encoded = relative.as_posix().encode("utf-8")
        digest.update(encoded + b"\0")
        digest.update(b"x" if executable else b"-")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        count += 1
        total_size += len(content)
    return {"sha256": digest.hexdigest(), "files": count, "bytes": total_size}


def _文件哈希(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _路径禁止发布(relative: str) -> bool:
    normalized = relative.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    for blocked in _禁止发布的路径:
        if blocked.endswith("/"):
            if normalized == blocked.rstrip("/") or normalized.startswith(blocked):
                return True
        elif normalized == blocked:
            return True
    return False


def _已跟踪的运行数据(source_root: Path, head: str) -> list[str]:
    output = _git(source_root, "ls-tree", "-r", "--name-only", "-z", head)
    return sorted(path for path in output.split("\0") if path and _路径禁止发布(path))


def _设为只读(root: Path) -> None:
    """封住候选源码或正式副本；目录保留读取/穿越权限。"""
    if not root.is_dir() or root.is_symlink():
        raise 发布仓库错误("要封存的目录无效")
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            raise 发布仓库错误("封存副本中不允许符号链接")
        if path.is_dir():
            path.chmod(0o555)
        elif path.is_file():
            path.chmod(0o555 if path.stat().st_mode & 0o111 else 0o444)
    root.chmod(0o555)


def _设为可清理(root: Path) -> None:
    if not root.exists() or root.is_symlink():
        return
    root.chmod(0o700)
    for path in root.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o700)
        elif path.is_file() and not path.is_symlink():
            path.chmod(0o700 if path.stat().st_mode & 0o111 else 0o600)


def _归档路径(root: Path, relative: Any) -> Path:
    value = Path(str(relative or ""))
    if not value.parts or value.is_absolute() or ".." in value.parts:
        raise 发布仓库错误("镜像归档路径无效")
    target = (root / value).resolve()
    if root.resolve() not in target.parents:
        raise 发布仓库错误("镜像归档路径越界")
    return target


_外部镜像来源 = {
    "postgres": "public.ecr.aws/docker/library/postgres:16.14-bookworm",
    "model_proxy": "ghcr.io/berriai/litellm-database:v1.86.2",
    "cloudflared": "cloudflare/cloudflared:2026.7.2",
}
_运行时检查编号 = {"company-entry", "runner-entry", "platform-entry"}


def _验证镜像记录(
    root: Path,
    images: Any,
    *,
    schema: int,
    verify_archive: bool,
) -> None:
    if not isinstance(images, dict) or images.get("status") != "ready":
        return
    components = images.get("components")
    if not isinstance(components, dict) or set(components) != {"company", "runner", "platform"}:
        raise 发布仓库错误("候选镜像清单不完整")
    for name, item in components.items():
        if not isinstance(item, dict):
            raise 发布仓库错误(f"候选镜像记录无效：{name}")
        image_id = str(item.get("id") or "")
        tag = str(item.get("candidate_tag") or "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) or not tag:
            raise 发布仓库错误(f"候选镜像身份无效：{name}")
    if schema >= 2:
        external = images.get("external")
        if not isinstance(external, dict) or set(external) != set(_外部镜像来源):
            raise 发布仓库错误("候选外部镜像锁不完整")
        for name, source_ref in _外部镜像来源.items():
            item = external.get(name)
            if not isinstance(item, dict):
                raise 发布仓库错误(f"候选外部镜像记录无效：{name}")
            image_id = str(item.get("id") or "")
            repo_digests = item.get("repo_digests")
            if (
                str(item.get("source_ref") or "") != source_ref
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
                or not isinstance(repo_digests, list)
                or not repo_digests
                or any(not isinstance(value, str) or "@sha256:" not in value for value in repo_digests)
                or not str(item.get("os") or "")
                or not str(item.get("architecture") or "")
                or not isinstance(item.get("size_bytes"), int)
                or int(item.get("size_bytes") or 0) <= 0
            ):
                raise 发布仓库错误(f"候选外部镜像身份无效：{name}")
        runtime_checks = images.get("runtime_checks")
        if not isinstance(runtime_checks, list) or len(runtime_checks) != len(_运行时检查编号):
            raise 发布仓库错误("候选运行时检查不完整")
        checks: dict[str, dict[str, Any]] = {}
        for item in runtime_checks:
            if not isinstance(item, dict) or not isinstance(item.get("code"), str):
                raise 发布仓库错误("候选运行时检查记录无效")
            code = str(item["code"])
            if code in checks:
                raise 发布仓库错误("候选运行时检查记录重复")
            checks[code] = item
        if set(checks) != _运行时检查编号 or any(
            item.get("status") != "passed" for item in checks.values()
        ):
            raise 发布仓库错误("候选运行时检查没有全部通过")
        if images.get("runtime_checked") is not True or images.get("archive_restored") is not True:
            raise 发布仓库错误("候选镜像没有完成运行时检查和归档恢复复核")
    expected = str(images.get("archive_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise 发布仓库错误("镜像归档校验值无效")
    archive = _归档路径(root, images.get("archive"))
    if not archive.is_file() or archive.is_symlink():
        raise 发布仓库错误("镜像归档不存在")
    if verify_archive and _文件哈希(archive) != expected:
        raise 发布仓库错误("镜像归档校验失败")


def _读取数据契约(source: Path) -> dict[str, Any]:
    path = source / "多用户" / "部署" / "数据契约.json"
    value = _读JSON(path)
    try:
        schema = int(value["schema_version"])
        compatible_from = int(value["compatible_from"])
        compatible_to = int(value["compatible_to"])
    except (KeyError, TypeError, ValueError) as exc:
        raise 发布仓库错误("发布数据契约缺少有效版本范围") from exc
    migration = str(value.get("migration") or "")
    if (
        schema < 1 or compatible_from < 1 or compatible_to < compatible_from
        or not compatible_from <= schema <= compatible_to
        or migration not in {"none", "required"}
    ):
        raise 发布仓库错误("发布数据契约无效")
    return {
        "schema_version": schema,
        "compatible_from": compatible_from,
        "compatible_to": compatible_to,
        "migration": migration,
    }


def _验证验收日志(root: Path, acceptance: Any, *, schema: int, verify_log: bool) -> None:
    if schema < 2 or not isinstance(acceptance, dict):
        return
    log = acceptance.get("log") if isinstance(acceptance.get("log"), dict) else {}
    if not log or not log.get("complete"):
        if acceptance.get("status") in {"passed", "failed"}:
            raise 发布仓库错误("候选验收日志尚未完整封存")
        return
    if str(log.get("file") or "") != "release.log":
        raise 发布仓库错误("候选验收日志路径无效")
    expected = str(log.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise 发布仓库错误("候选验收日志校验值无效")
    path = _归档路径(root, "release.log")
    if not path.is_file() or path.is_symlink():
        raise 发布仓库错误("候选验收日志不存在")
    if int(log.get("bytes") or -1) != path.stat().st_size:
        raise 发布仓库错误("候选验收日志大小不匹配")
    if verify_log and _文件哈希(path) != expected:
        raise 发布仓库错误("候选验收日志校验失败")


def _选择发布源(source_root: Path | None) -> tuple[Path, dict[str, Any] | None]:
    if source_root is not None:
        return source_root.expanduser().resolve(), None
    try:
        selected = 读取唯一开发源(
            record_path=RUNTIME / "data" / "development-source.json",
            release_store=_仓库根(),
        )
        identity = 核对开发源(selected, release_store=_仓库根())
        return selected, identity
    except 开发源错误 as exc:
        raise 发布仓库错误(str(exc)) from exc


def 发布源状态(*, source_root: Path | None = None) -> dict[str, Any]:
    source_root, identity = _选择发布源(source_root)
    if not (source_root / ".git").exists():
        try:
            _git(source_root, "rev-parse", "--git-dir")
        except 发布仓库错误 as exc:
            raise 发布仓库错误("发布源不是 Git 工作区") from exc
    head = _git(source_root, "rev-parse", "HEAD")
    pending = _git(
        source_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        ".",
        ":(exclude)多用户/部署/正式版.lock.json",
    )
    forbidden = _已跟踪的运行数据(source_root, head)
    ready = not pending and not forbidden
    if pending:
        reason = "唯一开发源存在尚未提交的内容"
    elif forbidden:
        reason = "开发提交混入了本机运行数据：" + "、".join(forbidden[:5])
    else:
        reason = "唯一开发源已经提交，可以制造候选"
    return {
        "ready": ready,
        "reason": reason,
        "source": {
            "head": head,
            "short_head": head[:10],
            "branch": _git(source_root, "branch", "--show-current", check=False) or "未命名分支",
            "committed_at": _git(source_root, "show", "-s", "--format=%cI", head),
            "summary": _git(source_root, "show", "-s", "--format=%s", head),
            "clean": not pending,
            "forbidden_tracked_paths": forbidden,
            **({
                "source_id": identity["source_id"],
                "root": identity["source"],
                "canonical_branch": identity["canonical_branch"],
            } if identity else {}),
        },
    }


def _解出提交(source_root: Path, head: str, destination: Path) -> None:
    archive = destination.parent / "source.tar"
    _git(source_root, "archive", "--format=tar", f"--output={archive}", head)
    try:
        with tarfile.open(archive, "r:") as package:
            members = package.getmembers()
            for member in members:
                path = Path(member.name)
                if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                    raise 发布仓库错误(f"发布源含不允许的归档成员：{member.name}")
            package.extractall(destination, members=members, filter="data")
    finally:
        archive.unlink(missing_ok=True)


def 创建候选(
    *,
    source_root: Path | None = None,
    store_root: Path | None = None,
    expected_head: str,
    notes: str = "",
    candidate_id: str | None = None,
) -> dict[str, Any]:
    source_root, identity = _选择发布源(source_root)
    status = 发布源状态(source_root=source_root)
    if identity:
        status["source"].update({
            "source_id": identity["source_id"],
            "root": identity["source"],
            "canonical_branch": identity["canonical_branch"],
        })
    if not status["ready"]:
        raise 发布仓库错误(f"禁止制造候选：{status['reason']}")
    head = str(status["source"]["head"])
    if not expected_head or head != expected_head:
        raise 发布仓库错误("发布源提交已经变化，请重新确认")
    notes = str(notes or "").strip()
    if len(notes) > 1000 or any(ord(char) < 32 and char not in "\n\t" for char in notes):
        raise 发布仓库错误("候选说明无效")
    candidate_id = candidate_id or 生成候选编号()
    if not CANDIDATE_PATTERN.fullmatch(candidate_id):
        raise 发布仓库错误("候选编号无效")

    store = _仓库根(store_root)
    candidates = store / "candidates"
    candidates.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = candidates / candidate_id
    if destination.exists():
        raise 发布仓库错误("候选编号已经存在")
    temporary = Path(tempfile.mkdtemp(prefix=f".{candidate_id}-", dir=candidates))
    source = temporary / "source"
    source.mkdir()
    try:
        _解出提交(source_root, head, source)
        fingerprint = _树指纹(source)
        _设为只读(source)
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "candidate_id": candidate_id,
            "track": "development",
            "product_version_id": None,
            "product_version_name": DEVELOPMENT_VERSION_NAME,
            "release_name": None,
            "created_at": _现在(),
            "notes": notes,
            "source": {**status["source"], **fingerprint},
            "data_contract": _读取数据契约(source),
            "acceptance": {"status": "pending"},
            "images": {"status": "pending"},
        }
        _写JSON(temporary / "manifest.json", manifest)
        temporary.rename(destination)
        return {**manifest, "path": str(destination), "build_context": str(destination / "source")}
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _候选目录(candidate_id: str, store_root: Path | None = None) -> Path:
    if not CANDIDATE_PATTERN.fullmatch(str(candidate_id or "")):
        raise 发布仓库错误("候选编号无效")
    return _仓库根(store_root) / "candidates" / candidate_id


def 读取候选(candidate_id: str, *, store_root: Path | None = None, verify: bool = True) -> dict[str, Any]:
    path = _候选目录(candidate_id, store_root)
    manifest = _读JSON(path / "manifest.json")
    schema = int(manifest.get("schema") or 0)
    if schema not in {1, MANIFEST_SCHEMA} or manifest.get("candidate_id") != candidate_id:
        raise 发布仓库错误("候选清单身份不匹配")
    if verify:
        expected = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
        actual = _树指纹(path / "source")
        if any(actual[key] != expected.get(key) for key in ("sha256", "files", "bytes")):
            raise 发布仓库错误("候选源码校验失败，禁止继续")
        if schema >= 2 and _读取数据契约(path / "source") != manifest.get("data_contract"):
            raise 发布仓库错误("候选数据契约与固定源码不一致")
        _验证验收日志(path, manifest.get("acceptance"), schema=schema, verify_log=True)
        _验证镜像记录(path, manifest.get("images"), schema=schema, verify_archive=True)
    return {**manifest, "path": str(path), "build_context": str(path / "source")}


def 更新候选(
    candidate_id: str,
    changes: dict[str, Any],
    *,
    store_root: Path | None = None,
) -> dict[str, Any]:
    allowed = {"acceptance", "images"}
    if not changes or set(changes) - allowed:
        raise 发布仓库错误("候选清单更新字段无效")
    current = 读取候选(candidate_id, store_root=store_root)
    manifest = {key: value for key, value in current.items() if key not in {"path", "build_context"}}
    manifest.update(changes)
    path = _候选目录(candidate_id, store_root)
    acceptance = manifest.get("acceptance")
    images = manifest.get("images")
    if not isinstance(acceptance, dict) or acceptance.get("status") not in {
        "pending", "running", "passed", "failed",
    }:
        raise 发布仓库错误("候选验收状态无效")
    if not isinstance(images, dict) or images.get("status") not in {
        "pending", "building", "ready", "failed",
    }:
        raise 发布仓库错误("候选镜像状态无效")
    _验证镜像记录(
        path, images, schema=int(manifest.get("schema") or 0), verify_archive=True,
    )
    _验证验收日志(
        path, acceptance, schema=int(manifest.get("schema") or 0), verify_log=True,
    )
    _写JSON(path / "manifest.json", manifest)
    return 读取候选(candidate_id, store_root=store_root)


def _目录占用(root: Path) -> int:
    if not root.is_dir() or root.is_symlink():
        return 0
    return sum(
        path.stat().st_size for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    )


def _已被版本引用的候选(store: Path) -> set[str]:
    protected: set[str] = set()
    for area in ("staged", "published"):
        root = store / area
        if not root.is_dir() or root.is_symlink():
            continue
        for manifest_path in root.glob("*/manifest.json"):
            if not manifest_path.is_file() or manifest_path.is_symlink():
                continue
            try:
                manifest = _读JSON(manifest_path)
            except 发布仓库错误:
                continue
            candidate_id = str(manifest.get("candidate_id") or "")
            if CANDIDATE_PATTERN.fullmatch(candidate_id):
                protected.add(candidate_id)
    return protected


def 候选空间报告(*, store_root: Path | None = None) -> dict[str, Any]:
    """只读列出候选占用；不删除任何文件。"""
    store = _仓库根(store_root)
    candidates = store / "candidates"
    protected = _已被版本引用的候选(store)
    rows: list[dict[str, Any]] = []
    if candidates.is_dir() and not candidates.is_symlink():
        for path in sorted(candidates.iterdir(), key=lambda item: item.name):
            if not path.is_dir() or path.is_symlink() or not CANDIDATE_PATTERN.fullmatch(path.name):
                continue
            try:
                manifest = _读JSON(path / "manifest.json")
            except 发布仓库错误:
                manifest = {}
            acceptance = manifest.get("acceptance") if isinstance(manifest.get("acceptance"), dict) else {}
            images = manifest.get("images") if isinstance(manifest.get("images"), dict) else {}
            archive = path / "images.tar"
            rows.append({
                "candidate_id": path.name,
                "bytes": _目录占用(path),
                "archive_bytes": archive.stat().st_size if archive.is_file() and not archive.is_symlink() else 0,
                "acceptance_status": str(acceptance.get("status") or "unknown"),
                "images_status": str(images.get("status") or "unknown"),
                "protected_by_release": path.name in protected,
                "created_at": str(manifest.get("created_at") or ""),
            })
    return {
        "store": str(store),
        "candidate_bytes": sum(int(row["bytes"]) for row in rows),
        "archive_bytes": sum(int(row["archive_bytes"]) for row in rows),
        "candidates": rows,
    }


def 废弃候选大文件(
    candidate_id: str,
    *,
    store_root: Path | None = None,
    allow_ready: bool = False,
) -> dict[str, Any]:
    """仅收缩已失败/已过期候选；保留源码、清单和日志供追查。"""
    store = _仓库根(store_root)
    if candidate_id in _已被版本引用的候选(store):
        raise 发布仓库错误("候选已被暂存或正式版本引用，禁止清理")
    candidate = 读取候选(candidate_id, store_root=store, verify=False)
    acceptance = candidate.get("acceptance") if isinstance(candidate.get("acceptance"), dict) else {}
    images = candidate.get("images") if isinstance(candidate.get("images"), dict) else {}
    if acceptance.get("status") in {"pending", "running"} or images.get("status") == "building":
        raise 发布仓库错误("候选仍在运行，禁止清理")
    if images.get("status") == "ready" and not allow_ready:
        raise 发布仓库错误("已通过的候选必须先标记过期，才能清理")
    root = Path(str(candidate["path"])).resolve()
    before = _目录占用(root)
    archives = (root / "images.tar", *root.glob(".images.*.tar"))
    if any(path.is_symlink() for path in archives):
        raise 发布仓库错误("候选大文件路径异常，拒绝清理")
    manifest = {key: value for key, value in candidate.items() if key not in {"path", "build_context"}}
    manifest["images"] = {
        "status": "discarded",
        "previous_status": str(images.get("status") or "unknown"),
        "discarded_at": _现在(),
    }
    _写JSON(root / "manifest.json", manifest)
    for path in archives:
        path.unlink(missing_ok=True)
    after = _目录占用(root)
    return {"candidate_id": candidate_id, "reclaimed_bytes": max(0, before - after), "bytes": after}


def _正式目录(version: str, store_root: Path | None = None) -> Path:
    if not VERSION_PATTERN.fullmatch(str(version or "")):
        raise 发布仓库错误("正式版本号无效")
    return _仓库根(store_root) / "published" / version


def _暂存目录(version: str, store_root: Path | None = None) -> Path:
    if not VERSION_PATTERN.fullmatch(str(version or "")):
        raise 发布仓库错误("预发布版本号无效")
    return _仓库根(store_root) / "staged" / version


def _读取版本目录(path: Path, version: str, *, verify: bool) -> dict[str, Any]:
    manifest = _读JSON(path / "manifest.json")
    schema = int(manifest.get("schema") or 0)
    if manifest.get("version") != version or schema not in {1, MANIFEST_SCHEMA}:
        raise 发布仓库错误("版本清单身份不匹配")
    if verify:
        expected = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
        actual = _树指纹(path / "source")
        if any(actual[key] != expected.get(key) for key in ("sha256", "files", "bytes")):
            raise 发布仓库错误("版本源码校验失败")
        if schema >= 2 and _读取数据契约(path / "source") != manifest.get("data_contract"):
            raise 发布仓库错误("版本数据契约与固定源码不一致")
        _验证验收日志(path, manifest.get("acceptance"), schema=schema, verify_log=True)
        _验证镜像记录(path, manifest.get("images"), schema=schema, verify_archive=True)
    return {**manifest, "path": str(path), "build_context": str(path / "source")}


def 封存正式版本(
    candidate_id: str,
    version: str,
    version_name: str,
    *,
    store_root: Path | None = None,
) -> dict[str, Any]:
    candidate = 读取候选(candidate_id, store_root=store_root)
    if candidate.get("track") != "development" and candidate.get("product_version_id") not in {None, version}:
        raise 发布仓库错误("候选与要发布的产品版本不一致")
    version_name = 校验版本显示名(version_name)
    acceptance = candidate.get("acceptance") if isinstance(candidate.get("acceptance"), dict) else {}
    images = candidate.get("images") if isinstance(candidate.get("images"), dict) else {}
    if acceptance.get("status") != "passed" or images.get("status") != "ready":
        raise 发布仓库错误("候选尚未完成验收和镜像封存")
    destination = _暂存目录(version, store_root)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists():
        raise 发布仓库错误("这个候选已经存在预发布副本")
    if _正式目录(version, store_root).exists():
        raise 发布仓库错误("正式版本目录已经存在")
    source = _候选目录(candidate_id, store_root)
    manifest = {
        key: value for key, value in candidate.items() if key not in {"path", "build_context"}
    }
    manifest.update({
        "version": version,
        "version_name": version_name,
        "release_name": version_name,
        "staged_at": _现在(),
        "published_at": None,
    })
    temporary = destination.parent / f".{version}.{uuid.uuid4().hex}.tmp"
    installed = False
    try:
        # 正式版本是独立全量副本，不与候选共用硬链接数据。
        shutil.copytree(source, temporary, copy_function=shutil.copy2)
        _写JSON(temporary / "manifest.json", manifest)
        temporary.rename(destination)
        installed = True
        return 读取暂存版本(version, store_root=store_root)
    except Exception:
        shutil.rmtree(destination if installed else temporary, ignore_errors=True)
        raise


def 切换当前版本(
    version: str,
    *,
    store_root: Path | None = None,
    lock_path: Path = LOCK_PATH,
) -> dict[str, Any]:
    final = 读取正式版本(version, store_root=store_root)
    lock = {
        "schema": 3,
        "version": version,
        "version_name": final.get("version_name") or version,
        "candidate_id": final["candidate_id"],
        "published_at": final["published_at"],
        "activated_at": _现在(),
        "source": final["source"],
        "data_contract": final["data_contract"],
        "images": final["images"],
        "release_path": str(Path(str(final["path"])).resolve()),
    }
    _写JSON(lock_path, lock)
    return lock


def 激活暂存版本(
    version: str,
    *,
    store_root: Path | None = None,
    lock_path: Path = LOCK_PATH,
) -> dict[str, Any]:
    """把预发布副本原子提升为正式副本，再写入最终版本锁。"""
    staged = _暂存目录(version, store_root)
    final = _正式目录(version, store_root)
    release = 读取暂存版本(version, store_root=store_root)
    if final.exists():
        raise 发布仓库错误("正式版本目录已经存在")
    final.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = {key: value for key, value in release.items() if key not in {"path", "build_context"}}
    manifest["published_at"] = _现在()
    try:
        _写JSON(staged / "manifest.json", manifest)
        staged.rename(final)
        _设为只读(final)
        return 切换当前版本(version, store_root=store_root, lock_path=lock_path)
    except Exception:
        if final.is_dir() and not staged.exists():
            _设为可清理(final)
            final.rename(staged)
        manifest["published_at"] = None
        if staged.is_dir():
            _设为可清理(staged)
            _写JSON(staged / "manifest.json", manifest)
        raise


def 撤销未激活封存(
    version: str,
    candidate_id: str,
    *,
    store_root: Path | None = None,
    lock_path: Path = LOCK_PATH,
) -> None:
    if 当前已发布版本(lock_path) == version:
        raise 发布仓库错误("当前运行版本不能撤销")
    staged_path = _暂存目录(version, store_root)
    final_path = _正式目录(version, store_root)
    if staged_path.is_dir():
        release = 读取暂存版本(version, store_root=store_root)
        path = staged_path
    elif final_path.is_dir():
        # 仅用于恢复“目录已提升、版本锁尚未写入”时的进程中断。
        release = 读取正式版本(version, store_root=store_root)
        path = final_path
    else:
        return
    if release.get("candidate_id") != candidate_id:
        raise 发布仓库错误("未激活封存与候选不匹配")
    path = path.resolve()
    if path not in {staged_path.resolve(), final_path.resolve()}:
        raise 发布仓库错误("未激活封存路径无效")
    _设为可清理(path)
    shutil.rmtree(path)


def 读取暂存版本(
    version: str,
    *,
    store_root: Path | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    return _读取版本目录(_暂存目录(version, store_root), version, verify=verify)


def 读取正式版本(
    version: str,
    *,
    store_root: Path | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    path = _正式目录(version, store_root)
    return _读取版本目录(path, version, verify=verify)


def 当前已发布版本(path: Path = LOCK_PATH) -> str | None:
    if not path.is_file():
        if path == LOCK_PATH and LEGACY_LOCK_PATH.is_file():
            path = LEGACY_LOCK_PATH
        else:
            return None
    value = _读JSON(path)
    version = str(value.get("version") or "")
    if value.get("schema") != 3 or not VERSION_PATTERN.fullmatch(version):
        raise 发布仓库错误("正式版版本锁格式无效")
    return version


def 版本显示名(version: str | None) -> str:
    return str(version or DEVELOPMENT_VERSION_NAME)


def 已发布版本列表(*, store_root: Path | None = None, verify: bool = False) -> list[dict[str, Any]]:
    root = _仓库根(store_root) / "published"
    if not root.is_dir():
        return []
    values = []
    for path in root.iterdir():
        if path.is_dir() and VERSION_PATTERN.fullmatch(path.name):
            values.append(读取正式版本(path.name, store_root=store_root, verify=verify))
    return sorted(values, key=lambda item: str(item.get("published_at") or ""), reverse=True)


def 待发布产品版本(*, store_root: Path | None = None) -> str | None:
    published = 已发布版本列表(store_root=store_root, verify=False)
    return None if any(item.get("version") == PRODUCT_VERSION_ID for item in published) else PRODUCT_VERSION_ID


def 验证已发布(
    version: str | None = None,
    *,
    store_root: Path | None = None,
    lock_path: Path = LOCK_PATH,
) -> dict[str, Any]:
    selected = version or 当前已发布版本(lock_path)
    if not selected:
        raise 发布仓库错误("尚未发布任何正式版本")
    return 读取正式版本(selected, store_root=store_root)
