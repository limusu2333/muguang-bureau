#!/usr/bin/env python3
"""创建、验证并隔离演练开发公司快照。不会包含 `.env`，不会自动删除旧备份。"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from 根 import 代码根, 工作根, 数据根, 实例配置路径, 备份根, 是远程实例

COMPANY = 数据根
CODE = 代码根
WORK = 工作根
默认目录 = 备份根
策略位 = COMPANY / "备份策略.json"
状态位 = COMPANY / "运行状态" / "备份状态.json"
默认策略 = {
    "自动检查间隔天数": 3,
    "提醒天数": 4,
    "严重告警天数": 7,
    "异盘备份间隔天数": 7,
    "恢复演练间隔天数": 30,
    "异盘目录": "",
}
_排除目录 = {
    ".git", ".venv", "node_modules", "dist", "__pycache__", "_官方参考_agentscope",
    "scratchpad", "embedding缓存", "as_log", "声线试听", "90_备份",
}
_排除文件 = {
    ".env", ".DS_Store", "大厅索引.db", "大厅索引.db-shm", "大厅索引.db-wal", "备份清单.json",
    "备份状态.json", "备份状态.json.bak", "备份定时任务.log", "备份定时任务.err.log",
}
_敏感后缀 = {".pem", ".key", ".p12", ".pfx"}
_快照前缀 = "公司快照"
_清单名 = f"{_快照前缀}/备份清单.json"
_最大成员数 = 100_000
_默认恢复上限 = 20 * 1024**3
_恢复预留空间 = 512 * 1024**2
_SQLite路径 = {"data/资料室/图谱.db", "data/运行状态/统一搜索.db"}
_本机SQLite路径 = {"资料室/图谱.db", "运行状态/统一搜索.db"}
_凭据特征 = (
    re.compile(rb"(?i)(?:sk-|xjs_)[A-Za-z0-9_-]{20,}"),
    re.compile(rb"(?i)(?:OPENAI|DASHSCOPE|ZHIPU|DEEPSEEK|EDITH_DASHSCOPE)[A-Z_]*_KEY\s*[:=]\s*[^\s,;]{16,}"),
    re.compile(rb"(?i)authorization\s*[:=]\s*bearer\s+[A-Za-z0-9._~-]{20,}"),
)
_本机数据目录 = {
    "运行状态", "记忆库", "资料室", "附件", "项目记忆", "职级", "信誉", "信箱", "会议", "协同",
    "大厅", "会议室", "走廊", "工具间", "执行室", "待验收", "工单", "请示", "管理动作", "归档",
    "编程部", "经理办公室", "编辑部", "测试部",
}


def _时间(raw: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(raw or "").replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return None


def 读取策略() -> dict[str, Any]:
    policy = dict(默认策略)
    if 策略位.exists():
        loaded = json.loads(策略位.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise TypeError("备份策略.json 必须是对象")
        policy.update(loaded)
    for key in ("自动检查间隔天数", "提醒天数", "严重告警天数", "异盘备份间隔天数", "恢复演练间隔天数"):
        value = policy.get(key)
        if not isinstance(value, int) or value <= 0:
            raise ValueError(f"{key} 必须是正整数")
    if policy["提醒天数"] <= policy["自动检查间隔天数"]:
        raise ValueError("提醒天数必须大于自动检查间隔天数")
    if policy["严重告警天数"] <= policy["提醒天数"]:
        raise ValueError("严重告警天数必须大于提醒天数")
    policy["异盘目录"] = str(policy.get("异盘目录") or "").strip()
    return policy


def 读取状态() -> dict[str, Any]:
    from 状态存储 import 读JSON

    return 读JSON(状态位, 默认={}, 类型=dict) or {}


def _写状态(state: dict[str, Any]) -> None:
    from 状态存储 import 写JSON

    写JSON(状态位, state, 备份=False)


@contextlib.contextmanager
def _互斥锁():
    默认目录.mkdir(parents=True, exist_ok=True)
    lock_path = 默认目录 / ".公司备份.lock"
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise RuntimeError("另一个备份任务正在运行") from e
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _时间(raw: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(raw or "").replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return None


def 读取策略() -> dict[str, Any]:
    policy = dict(默认策略)
    if 策略位.exists():
        loaded = json.loads(策略位.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise TypeError("备份策略.json 必须是对象")
        policy.update(loaded)
    for key in ("自动检查间隔天数", "提醒天数", "严重告警天数", "异盘备份间隔天数", "恢复演练间隔天数"):
        value = policy.get(key)
        if not isinstance(value, int) or value <= 0:
            raise ValueError(f"{key} 必须是正整数")
    if policy["提醒天数"] <= policy["自动检查间隔天数"]:
        raise ValueError("提醒天数必须大于自动检查间隔天数")
    if policy["严重告警天数"] <= policy["提醒天数"]:
        raise ValueError("严重告警天数必须大于提醒天数")
    policy["异盘目录"] = str(policy.get("异盘目录") or "").strip()
    return policy


def 读取状态() -> dict[str, Any]:
    from 状态存储 import 读JSON

    return 读JSON(状态位, 默认={}, 类型=dict) or {}


def _写状态(state: dict[str, Any]) -> None:
    from 状态存储 import 写JSON

    写JSON(状态位, state, 备份=False)


@contextlib.contextmanager
def _互斥锁():
    默认目录.mkdir(parents=True, exist_ok=True)
    lock_path = 默认目录 / ".公司备份.lock"
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise RuntimeError("另一个备份任务正在运行") from e
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _含疑似凭据(path: Path) -> bool:
    carry = b""
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            sample = carry + chunk
            if any(pattern.search(sample) for pattern in _凭据特征):
                return True
            carry = sample[-1024:]
    return False


def _排除(rel: Path) -> bool:
    if any(part in _排除目录 for part in rel.parts):
        return True
    if (
        rel.name in _排除文件 or rel.name.startswith(".env.")
        or rel.name.endswith((".db-wal", ".db-shm", ".sqlite-wal", ".sqlite-shm"))
        or rel.suffix.lower() in _敏感后缀 or rel.suffix == ".pyc"
    ):
        return True
    return False


def _恢复字节上限() -> int:
    raw = os.environ.get("XJ_RESTORE_MAX_BYTES", str(_默认恢复上限))
    try:
        value = int(raw)
    except ValueError as e:
        raise ValueError("XJ_RESTORE_MAX_BYTES 必须是正整数") from e
    if value <= 0:
        raise ValueError("XJ_RESTORE_MAX_BYTES 必须大于 0")
    return value


def _安全相对路径(name: str, *, 带前缀: bool) -> PurePosixPath:
    """只接受规范的 POSIX 相对路径，拒绝跨平台可利用的路径写法。"""
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise ValueError(f"非法路径：{name!r}")
    path = PurePosixPath(name)
    parts = path.parts
    if path.is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"越界路径：{name}")
    if 带前缀:
        if len(parts) < 2 or parts[0] != _快照前缀:
            raise ValueError(f"不在 {_快照前缀}/ 下：{name}")
        return PurePosixPath(*parts[1:])
    if parts[0] == _快照前缀:
        raise ValueError(f"清单路径不应重复快照前缀：{name}")
    return path


def _敏感归档路径(rel: PurePosixPath) -> bool:
    for part in rel.parts:
        if part == ".env" or part.startswith(".env."):
            return True
    return rel.suffix.lower() in _敏感后缀


def _普通文件(info: zipfile.ZipInfo) -> bool:
    if info.is_dir():
        return False
    if info.create_system != 3:
        return True
    mode = (info.external_attr >> 16) & 0xFFFF
    return not mode or stat.S_ISREG(mode)


def _清单(path: Path) -> tuple[dict[str, Any], dict[str, zipfile.ZipInfo], list[str], int]:
    """检查压缩包结构并返回清单；不向文件系统解压任何内容。"""
    problems: list[str] = []
    manifest: dict[str, Any] = {}
    infos: dict[str, zipfile.ZipInfo] = {}
    total = 0
    try:
        with zipfile.ZipFile(path) as zf:
            members = zf.infolist()
            if len(members) > _最大成员数:
                problems.append(f"文件数超过上限：{len(members)} > {_最大成员数}")
                return manifest, infos, problems, total

            for info in members:
                name = info.filename
                if name in infos:
                    problems.append(f"重复路径：{name}")
                    continue
                infos[name] = info
                try:
                    rel = _安全相对路径(name.rstrip("/"), 带前缀=True)
                except ValueError as e:
                    problems.append(str(e))
                    continue
                if info.flag_bits & 0x1:
                    problems.append(f"不支持加密文件：{name}")
                if info.is_dir():
                    continue
                if not _普通文件(info):
                    problems.append(f"不允许软链接或特殊文件：{name}")
                if _敏感归档路径(rel):
                    problems.append(f"备份误收敏感文件：{rel.as_posix()}")
                total += int(info.file_size)

            if total > _恢复字节上限():
                problems.append(f"解压总量超过安全上限：{total} 字节")

            manifest_info = infos.get(_清单名)
            if manifest_info is None:
                problems.append("缺少备份清单.json")
            elif manifest_info.file_size > 64 * 1024**2:
                problems.append("备份清单异常过大")
            else:
                try:
                    raw_manifest = zf.read(manifest_info)
                    parsed = json.loads(raw_manifest.decode("utf-8"))
                    if not isinstance(parsed, dict):
                        raise ValueError("清单根必须是对象")
                    manifest = parsed
                except Exception as e:  # noqa: BLE001
                    problems.append(f"清单无法读取：{type(e).__name__}: {e}")

            files = manifest.get("文件") if isinstance(manifest, dict) else None
            if manifest and manifest.get("格式") not in (1, 2):
                problems.append(f"不支持的备份格式：{manifest.get('格式')!r}")
            schema_version = manifest.get("schema_version") if manifest else None
            if schema_version is not None and schema_version != 1:
                problems.append(f"不支持的导出 schema_version：{schema_version!r}")
            if schema_version == 1:
                account_id = manifest.get("account_id")
                if not isinstance(account_id, str) or not account_id or len(account_id) > 64:
                    problems.append("导出清单缺少有效 account_id")
                if manifest.get("凭据状态") != "需重新绑定":
                    problems.append("导出清单未标明凭据需重新绑定")
            if manifest and not isinstance(files, dict):
                problems.append("清单缺少文件表")
                files = {}
            files = files or {}
            if schema_version == 1 and manifest.get("文件清单") != files:
                problems.append("导出文件清单与校验表不一致")

            expected_names = {_清单名}
            for rel_text, expected in files.items():
                try:
                    rel = _安全相对路径(rel_text, 带前缀=False)
                except ValueError as e:
                    problems.append(f"清单内{e}")
                    continue
                if manifest.get("格式") == 2 and rel.parts[0] not in {"data", "work", "instance.yaml"}:
                    problems.append(f"格式2含越界分类：{rel.as_posix()}")
                    continue
                if rel == PurePosixPath("备份清单.json"):
                    problems.append("备份清单.json 是系统保留路径，不能列入普通文件表")
                    continue
                if _敏感归档路径(rel):
                    problems.append(f"清单误收敏感文件：{rel.as_posix()}")
                name = f"{_快照前缀}/{rel.as_posix()}"
                expected_names.add(name)
                info = infos.get(name)
                if info is None:
                    problems.append(f"缺文件：{rel.as_posix()}")
                    continue
                if not isinstance(expected, dict):
                    problems.append(f"清单记录无效：{rel.as_posix()}")
                    continue
                size = expected.get("大小")
                digest = expected.get("sha256")
                if not isinstance(size, int) or size < 0 or info.file_size != size:
                    problems.append(f"大小不符：{rel.as_posix()}")
                if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    problems.append(f"哈希格式无效：{rel.as_posix()}")

            actual_files = {info.filename for info in members if not info.is_dir()}
            for name in sorted(actual_files - expected_names):
                problems.append(f"清单外文件：{name}")

            if not problems:
                bad = zf.testzip()
                if bad:
                    problems.append(f"ZIP CRC 失败：{bad}")
            if not problems:
                for rel_text, expected in files.items():
                    name = f"{_快照前缀}/{rel_text}"
                    h = hashlib.sha256()
                    with zf.open(name) as src:
                        for chunk in iter(lambda: src.read(1024 * 1024), b""):
                            h.update(chunk)
                    if h.hexdigest() != expected["sha256"]:
                        problems.append(f"哈希不符：{rel_text}")
    except Exception as e:  # noqa: BLE001
        problems.append(f"{type(e).__name__}: {e}")
    return manifest, infos, problems, total


def _收一根(root_path: Path, archive_prefix: str, out: list[tuple[str, Path]], *, 限定目录: set[str] | None = None) -> None:
    if not root_path.exists():
        return
    for root, dirs, names in os.walk(root_path, followlinks=False):
        base = Path(root)
        if base == root_path and 限定目录 is not None:
            dirs[:] = [d for d in dirs if d in 限定目录]
        dirs[:] = [d for d in dirs if not _排除((base / d).relative_to(root_path))]
        for name in names:
            p = base / name
            rel = p.relative_to(root_path)
            if not _排除(rel) and not p.is_symlink():
                out.append((f"{archive_prefix}/{rel.as_posix()}", p))


def _源文件(*, 用户数据导出: bool | None = None) -> list[tuple[str, Path]]:
    """列出一种备份的输入；整公司备份和可携带的用户导出互不混用。"""
    if 用户数据导出 is None:
        用户数据导出 = 是远程实例()
    out: list[tuple[str, Path]] = []
    if not 用户数据导出:
        _收一根(COMPANY.resolve(), "", out)
        return sorted(
            ((rel.lstrip("/"), path) for rel, path in out),
            key=lambda item: item[0],
        )
    data = 数据根.resolve()
    _收一根(data, "data", out, 限定目录=None if 是远程实例() else _本机数据目录)
    if WORK.resolve() != data:
        _收一根(WORK.resolve(), "work", out)
    return sorted(out, key=lambda item: item[0])


def _变化签名(*, 用户数据导出: bool | None = None) -> str:
    """用路径、大小和修改时间判断公司是否变过；不读取文件正文。"""
    digest = hashlib.sha256()
    for rel, path in _源文件(用户数据导出=用户数据导出):
        stat_result = path.stat()
        digest.update(f"{rel}\0{stat_result.st_size}\0{stat_result.st_mtime_ns}\n".encode("utf-8"))
    return digest.hexdigest()


def _git() -> dict[str, Any]:
    def run(*args: str) -> str:
        try:
            return subprocess.run(["git", "-C", str(CODE), *args], capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        except Exception:  # noqa: BLE001
            return ""
    status = run("status", "--porcelain=v1").splitlines()
    return {"提交": run("rev-parse", "HEAD"), "分支": run("branch", "--show-current"), "未提交路径": status}


def _复制稳定(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        before = src.stat()
        shutil.copy2(src, dst)
        after = src.stat()
        if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns):
            return
        if attempt == 0:
            continue
    raise RuntimeError(f"备份期间文件持续变化，拒绝生成不一致快照：{src}")


def _去凭据实例配置() -> dict[str, Any] | None:
    if not 实例配置路径.is_file():
        return None
    raw = yaml.safe_load(实例配置路径.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("INSTANCE_CONFIG 不是有效对象")

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: clean(item)
                for key, item in value.items()
                if str(key).lower() not in {"credential_ref", "token", "key", "api_key"}
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    cleaned = clean(raw)
    if isinstance(cleaned.get("模型路由"), dict):
        cleaned["模型路由"]["需重新绑定"] = True
    return cleaned


def _备份sqlite(src: Path, dst: Path) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
        result = str(target.execute("PRAGMA integrity_check").fetchone()[0])
        if result.lower() != "ok":
            raise RuntimeError(f"SQLite 完整性失败：{result}")
        return result
    finally:
        target.close()
        source.close()


def _创建(dest_root: Path | None, *, 用户数据导出: bool) -> Path:
    dest_root = Path(dest_root or 默认目录).resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    final = dest_root / f"公司记忆_{stamp}.zip"
    source_signature = _变化签名(用户数据导出=用户数据导出)
    with tempfile.TemporaryDirectory(prefix="company-backup-", dir=dest_root) as td:
        stage = Path(td) / "公司快照"
        files: dict[str, dict[str, Any]] = {}
        sqlite_checks: dict[str, str] = {}
        for archive_rel, src in _源文件(用户数据导出=用户数据导出):
            rel = Path(archive_rel)
            业务rel = Path(*rel.parts[1:]) if 用户数据导出 else rel
            if _排除(业务rel):
                continue
            dst = stage / rel
            if rel.as_posix() in (_SQLite路径 if 用户数据导出 else _本机SQLite路径):
                sqlite_checks[rel.as_posix()] = _备份sqlite(src, dst)
            else:
                _复制稳定(src, dst)
            if 用户数据导出 and _含疑似凭据(dst):
                raise RuntimeError(f"导出内容疑似含有明文凭据，已拒绝封包：{rel.as_posix()}")
            files[rel.as_posix()] = {"大小": dst.stat().st_size, "sha256": _sha(dst)}
        instance_config = _去凭据实例配置() if 用户数据导出 else None
        if instance_config is not None:
            config_path = stage / "instance.yaml"
            config_path.write_text(
                yaml.safe_dump(instance_config, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            if _含疑似凭据(config_path):
                raise RuntimeError("去凭据实例配置仍命中凭据特征，已拒绝封包")
            files["instance.yaml"] = {"大小": config_path.stat().st_size, "sha256": _sha(config_path)}
        git_state = _git()
        image_version = os.environ.get("XJ_IMAGE_VERSION", "").strip()
        if 用户数据导出 and not git_state.get("提交") and not image_version:
            raise RuntimeError("导出缺少代码恢复锚：必须有 Git 提交或固定镜像版本")
        excluded = sorted(_排除目录 | _排除文件 | {"一切明文凭据与令牌", "SQLite -wal/-shm 辅助文件"})
        exported_at = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        if 用户数据导出:
            manifest = {
                "格式": 2,
                "schema_version": 1,
                "account_id": str((instance_config or {}).get("account_id") or "local"),
                "创建时间": exported_at,
                "导出时间": exported_at,
                "根": {"数据": "data/", "工作区": "work/", "实例配置": "instance.yaml"},
                "Git": git_state,
                "代码恢复": {"Git提交": git_state.get("提交", ""), "镜像版本": image_version},
                "文件": files,
                "文件清单": files,
                "SQLite完整性": sqlite_checks,
                "源签名": source_signature,
                "包含清单": ["DATA_ROOT", "WORKSPACE_ROOT", "INSTANCE_CONFIG（已去凭据）", "SQLite 一致性快照", "manifest"],
                "排除清单": excluded,
                "明确排除": excluded,
                "凭据状态": "需重新绑定",
                "说明": "只含用户数据、用户工作区和去凭据实例配置；代码按镜像版本或 Git 提交恢复。",
            }
        else:
            manifest = {
                "格式": 1,
                "创建时间": exported_at,
                "公司根": str(COMPANY),
                "Git": git_state,
                "文件": files,
                "SQLite完整性": sqlite_checks,
                "源签名": source_signature,
                "明确排除": sorted(_排除目录 | _排除文件),
                "说明": "含源码与关键状态；不含 .env、私钥、虚拟环境、依赖和可重建缓存。",
            }
        (stage / "备份清单.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_zip = Path(td) / final.name
        with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for p in sorted(stage.rglob("*")):
                if p.is_file():
                    zf.write(p, Path("公司快照") / p.relative_to(stage))
        result = 验证(temp_zip)
        if not result["ok"]:
            raise RuntimeError("备份验证失败：" + "；".join(result["问题"][:5]))
        os.replace(temp_zip, final)
    if dest_root == 默认目录.resolve():
        now = dt.datetime.now().isoformat(timespec="seconds")
        state = 读取状态()
        state.update({
            "状态": "备份成功",
            "最近尝试": now,
            "最近检查": now,
            "最近成功": now,
            "最近备份": str(final),
            "源签名": source_signature,
            "有变化": False,
            "错误": "",
        })
        _写状态(state)
    return final


def 创建公司备份(dest_root: Path | None = None) -> Path:
    """封存本机整家公司，包含源码与关键状态，不作为用户可携带导出包。"""
    return _创建(dest_root, 用户数据导出=False)


def 创建用户数据导出(dest_root: Path | None = None) -> Path:
    """导出可随版本恢复的用户数据、工作区和去凭据实例配置，不包含源码。"""
    return _创建(dest_root, 用户数据导出=True)


def 创建(dest_root: Path | None = None) -> Path:
    """保留现有入口：本机封存整公司，远程实例只导出当前用户数据。"""
    return 创建用户数据导出(dest_root) if 是远程实例() else 创建公司备份(dest_root)


def 验证(path: Path) -> dict[str, Any]:
    path = Path(path).expanduser().resolve()
    manifest, _, problems, total = _清单(path)
    return {
        "ok": not problems,
        "问题": problems,
        "格式": manifest.get("格式") if manifest else None,
        "文件数": len((manifest.get("文件") or {})) if manifest else 0,
        "解压字节": total,
    }


def _安全解压(path: Path, root: Path) -> Path:
    """把已验证的快照逐文件写入临时根；永远不用 extractall。"""
    stage = root / _快照前缀
    stage.mkdir(parents=True, exist_ok=False)
    stage_real = stage.resolve()
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            rel = _安全相对路径(info.filename.rstrip("/"), 带前缀=True)
            target = stage.joinpath(*rel.parts)
            target_real = target.resolve(strict=False)
            if not target_real.is_relative_to(stage_real):
                raise RuntimeError(f"解压目标越界：{info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not _普通文件(info):
                raise RuntimeError(f"拒绝解压软链接或特殊文件：{info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            if info.create_system == 3:
                mode = ((info.external_attr >> 16) & 0xFFFF) & 0o777
                if mode:
                    target.chmod(mode)
    return stage


def _核对解压结果(stage: Path) -> None:
    manifest = json.loads((stage / "备份清单.json").read_text(encoding="utf-8"))
    files = manifest.get("文件") or {}
    actual = {
        p.relative_to(stage).as_posix()
        for p in stage.rglob("*")
        if p.is_file() and p != stage / "备份清单.json"
    }
    expected = set(files)
    if actual != expected:
        missing = sorted(expected - actual)[:5]
        extra = sorted(actual - expected)[:5]
        raise RuntimeError(f"解压结果与清单不一致；缺少={missing}；多出={extra}")
    for rel, meta in files.items():
        target = stage.joinpath(*PurePosixPath(rel).parts)
        if target.stat().st_size != meta["大小"] or _sha(target) != meta["sha256"]:
            raise RuntimeError(f"解压后二次校验失败：{rel}")


def _核对恢复内容(stage: Path) -> str:
    manifest = json.loads((stage / "备份清单.json").read_text(encoding="utf-8"))
    anchor = manifest.get("代码恢复") or {}
    if manifest.get("格式") == 2 and not (anchor.get("Git提交") or anchor.get("镜像版本")):
        raise RuntimeError("备份缺少代码恢复锚（Git 提交或镜像版本）")
    checked: list[str] = []
    for rel in (manifest.get("SQLite完整性") or {}):
        path = stage.joinpath(*PurePosixPath(rel).parts)
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = str(con.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            con.close()
        if result.lower() != "ok":
            raise RuntimeError(f"恢复后的 SQLite 完整性失败：{rel}: {result}")
        checked.append(rel)
    return f"数据/工作区文件核验通过；SQLite {len(checked)} 个；代码恢复锚有效"


def _接入演练依赖(stage: Path) -> tuple[Path, str]:
    python = CODE / ".venv" / "bin" / "python3"
    node_modules = CODE / "前端" / "node_modules"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise RuntimeError("真实公司缺少可用 .venv/bin/python3，无法做完整恢复演练")
    if not node_modules.is_dir():
        raise RuntimeError("真实公司缺少 前端/node_modules，无法做前端恢复演练")
    restored_frontend = stage / "前端"
    if not restored_frontend.is_dir():
        raise RuntimeError("快照缺少前端目录")
    link = restored_frontend / "node_modules"
    if link.exists() or link.is_symlink():
        raise RuntimeError("快照不应包含 node_modules")
    link.symlink_to(node_modules, target_is_directory=True)
    return python, str(link)


def 恢复演练(path: Path) -> dict[str, Any]:
    """格式1恢复整公司并跑检查；格式2核对用户数据与代码恢复锚。"""
    archive = Path(path).expanduser().resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"备份不存在：{archive}")
    archive_sha = _sha(archive)

    with tempfile.TemporaryDirectory(prefix="company-restore-drill-") as td:
        root = Path(td)
        if archive.stat().st_size + _恢复预留空间 > shutil.disk_usage(root).free:
            raise RuntimeError("临时盘空间不足，无法建立稳定的演练副本")
        stable_archive = root / "待恢复快照.zip"
        shutil.copyfile(archive, stable_archive)
        if _sha(stable_archive) != archive_sha or _sha(archive) != archive_sha:
            raise RuntimeError("复制演练包时源备份发生变化，结果作废")
        verified = 验证(stable_archive)
        if not verified["ok"]:
            raise RuntimeError("备份验证失败：" + "；".join(verified["问题"][:10]))
        required = int(verified["解压字节"]) + _恢复预留空间
        free = shutil.disk_usage(root).free
        if required > free:
            raise RuntimeError(f"临时盘空间不足：需要至少 {required} 字节，可用 {free} 字节")
        stage = _安全解压(stable_archive, root)
        _核对解压结果(stage)
        manifest = json.loads((stage / "备份清单.json").read_text(encoding="utf-8"))
        if manifest.get("格式") == 2:
            output = _核对恢复内容(stage)
            recovery = "按清单中的镜像版本或 Git 提交另行恢复"
        else:
            # 清单属于压缩包外壳，不是打包前公司的业务文件。
            (stage / "备份清单.json").unlink()
            python, dependency_link = _接入演练依赖(stage)
            env = os.environ.copy()
            env["XJ_MOCK"] = "1"
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            path_parts = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
            for part in str(env.get("PATH") or "").split(os.pathsep):
                if part and part not in path_parts:
                    path_parts.append(part)
            env["PATH"] = os.pathsep.join(part for part in path_parts if Path(part).is_dir())
            result = subprocess.run(
                ["make", f"PY={python}", "check"],
                cwd=stage,
                env=env,
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
            output = (result.stdout or "") + (result.stderr or "")
            if result.returncode:
                raise RuntimeError(f"恢复副本 make check 失败（退出码 {result.returncode}）：\n{output[-12000:]}")
            recovery = f"整公司恢复副本检查通过；临时复用依赖 {dependency_link}"
        return {
            "ok": True,
            "备份": str(archive),
            "备份sha256": archive_sha,
            "文件数": verified["文件数"],
            "解压字节": verified["解压字节"],
            "代码恢复": recovery,
            "检查输出": output,
        }


def _恢复目标文件(manifest: dict[str, Any], incoming: dict[str, Path], rel_text: str) -> Path:
    rel = PurePosixPath(rel_text)
    if rel == PurePosixPath("instance.yaml"):
        return incoming["config"]
    if len(rel.parts) < 2 or rel.parts[0] not in {"data", "work"}:
        raise RuntimeError(f"恢复清单含越界路径：{rel_text}")
    return incoming[rel.parts[0]].joinpath(*rel.parts[1:])


def _含凭据字段(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {"credential_ref", "token", "key", "api_key"}:
                return True
            if _含凭据字段(item):
                return True
    elif isinstance(value, list):
        return any(_含凭据字段(item) for item in value)
    return False


def 恢复到(path: Path, *, data_target: Path, work_target: Path, config_target: Path) -> dict[str, Any]:
    """把 schema_version=1 导出包安装到三个全新目标。

    不覆盖已有路径；先在各目标同盘复制并复核，全部通过后才逐项原子改名。
    """
    archive = Path(path).expanduser().resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"导出包不存在：{archive}")
    targets = {
        "data": Path(data_target).expanduser().resolve(),
        "work": Path(work_target).expanduser().resolve(),
        "config": Path(config_target).expanduser().resolve(),
    }
    values = list(targets.values())
    for index, target in enumerate(values):
        if target.exists():
            raise RuntimeError(f"恢复目标已存在，拒绝覆盖：{target}")
        for other in values[index + 1:]:
            if target == other or target.is_relative_to(other) or other.is_relative_to(target):
                raise RuntimeError("数据、工作区和配置的恢复目标不得重叠")

    archive_sha = _sha(archive)
    with tempfile.TemporaryDirectory(prefix="company-app-import-") as td:
        root = Path(td)
        stable_archive = root / "待导入快照.zip"
        shutil.copyfile(archive, stable_archive)
        if _sha(stable_archive) != archive_sha or _sha(archive) != archive_sha:
            raise RuntimeError("复制导入包时源文件发生变化")
        verified = 验证(stable_archive)
        if not verified["ok"]:
            raise RuntimeError("导出包验证失败：" + "；".join(verified["问题"][:10]))
        stage = _安全解压(stable_archive, root)
        _核对解压结果(stage)
        _核对恢复内容(stage)
        manifest = json.loads((stage / "备份清单.json").read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1:
            raise RuntimeError("独立 App 只能导入 schema_version=1 的导出包")
        source_config = stage / "instance.yaml"
        if not source_config.is_file():
            raise RuntimeError("导出包缺少去凭据 INSTANCE_CONFIG")
        config_value = yaml.safe_load(source_config.read_text(encoding="utf-8"))
        if not isinstance(config_value, dict) or _含凭据字段(config_value):
            raise RuntimeError("导入配置仍含凭据字段")
        if not isinstance(config_value.get("模型路由"), dict) or config_value["模型路由"].get("需重新绑定") is not True:
            raise RuntimeError("导入配置未标明模型需重新授权")

        token = secrets.token_hex(8)
        incoming = {
            name: target.parent / f".{target.name}.xj-import-{token}"
            for name, target in targets.items()
        }
        installed: list[Path] = []
        try:
            for target in targets.values():
                target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(stage / "data", incoming["data"]) if (stage / "data").is_dir() else incoming["data"].mkdir()
            shutil.copytree(stage / "work", incoming["work"]) if (stage / "work").is_dir() else incoming["work"].mkdir()
            shutil.copy2(source_config, incoming["config"])

            for rel_text, meta in (manifest.get("文件") or {}).items():
                target = _恢复目标文件(manifest, incoming, rel_text)
                if not target.is_file() or target.stat().st_size != meta["大小"] or _sha(target) != meta["sha256"]:
                    raise RuntimeError(f"导入临时副本校验失败：{rel_text}")
                if _含疑似凭据(target):
                    raise RuntimeError(f"导入包疑似含有明文凭据：{rel_text}")
            for rel_text in manifest.get("SQLite完整性") or {}:
                sqlite_path = _恢复目标文件(manifest, incoming, rel_text)
                with sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True) as con:
                    if str(con.execute("PRAGMA integrity_check").fetchone()[0]).lower() != "ok":
                        raise RuntimeError(f"导入 SQLite 完整性失败：{rel_text}")

            for name in ("data", "work", "config"):
                os.replace(incoming[name], targets[name])
                installed.append(targets[name])
        except Exception:
            for target in reversed(installed):
                shutil.rmtree(target, ignore_errors=True) if target.is_dir() else target.unlink(missing_ok=True)
            raise
        finally:
            for candidate in incoming.values():
                shutil.rmtree(candidate, ignore_errors=True) if candidate.is_dir() else candidate.unlink(missing_ok=True)

    return {
        "ok": True,
        "account_id": manifest["account_id"],
        "导出包sha256": archive_sha,
        "数据目标": str(targets["data"]),
        "工作区目标": str(targets["work"]),
        "配置目标": str(targets["config"]),
        "凭据状态": "需重新绑定",
    }


def _最新备份() -> Path:
    candidates = sorted(默认目录.glob("公司记忆_*.zip"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        raise FileNotFoundError(f"没有找到公司备份：{默认目录}")
    return candidates[-1]


def _已到期(now: dt.datetime, raw: str, days: int, *, fallback: dt.datetime | None = None) -> bool:
    previous = _时间(raw) or fallback
    return previous is None or (now - previous).total_seconds() >= days * 86400


def _异盘复制(archive: Path, destination: Path) -> Path:
    destination = destination.expanduser().resolve()
    if destination == 默认目录.resolve():
        raise ValueError("异盘目录不能与本机备份目录相同")
    destination.mkdir(parents=True, exist_ok=True)
    temp = destination / f".{archive.name}.{os.getpid()}.tmp"
    final = destination / archive.name
    try:
        shutil.copy2(archive, temp)
        result = 验证(temp)
        if not result["ok"]:
            raise RuntimeError("异盘副本校验失败：" + "；".join(result["问题"][:5]))
        os.replace(temp, final)
    finally:
        temp.unlink(missing_ok=True)
    return final


def 强制备份() -> Path:
    """无视三天间隔立即生成新快照；用于人工命令和重要操作前。"""
    with _互斥锁():
        try:
            return 创建()
        except Exception as e:  # noqa: BLE001
            now = dt.datetime.now().isoformat(timespec="seconds")
            state = 读取状态()
            state.update({"状态": "备份失败", "最近尝试": now, "错误": f"{type(e).__name__}: {e}"})
            _写状态(state)
            raise


def 定时维护(
    *,
    now: dt.datetime | None = None,
    执行恢复演练: bool = True,
    执行异盘复制: bool = True,
) -> dict[str, Any]:
    """每天由 macOS 唤醒；三天才检查一次，没变化就不生成重复 ZIP。"""
    current = now or dt.datetime.now()
    policy = 读取策略()
    with _互斥锁():
        state = 读取状态()
        attempted_at = current.isoformat(timespec="seconds")
        state["最近尝试"] = attempted_at
        try:
            try:
                latest = _最新备份()
            except FileNotFoundError:
                latest = None
            fallback = dt.datetime.fromtimestamp(latest.stat().st_mtime) if latest else None
            check_due = _已到期(
                current,
                str(state.get("最近检查") or ""),
                int(policy["自动检查间隔天数"]),
                fallback=fallback,
            )
            # 每天唤醒时只做快速结构检查；完整哈希校验在三天检查和恢复演练中完成。
            latest_ok = bool(latest and zipfile.is_zipfile(latest))
            if check_due or not latest_ok:
                if latest_ok:
                    latest_ok = bool(验证(latest)["ok"])
                signature = _变化签名()
                changed = signature != str(state.get("源签名") or "")
                if changed or not latest_ok:
                    latest = 创建()
                    state = 读取状态()
                else:
                    checked_at = current.isoformat(timespec="seconds")
                    state.update({
                        "状态": "已检查，无变化",
                        "最近尝试": checked_at,
                        "最近检查": checked_at,
                        "源签名": signature,
                        "有变化": False,
                        "错误": "",
                    })
                    _写状态(state)

            latest = latest or _最新备份()
            if 执行异盘复制:
                mirror_text = str(policy.get("异盘目录") or "")
                if mirror_text and _已到期(
                    current,
                    str(state.get("最近异盘备份") or ""),
                    int(policy["异盘备份间隔天数"]),
                ):
                    try:
                        mirrored = _异盘复制(latest, Path(mirror_text))
                        state.update({
                            "异盘状态": "成功",
                            "最近异盘备份": current.isoformat(timespec="seconds"),
                            "最近异盘文件": str(mirrored),
                            "异盘错误": "",
                        })
                    except Exception as e:  # noqa: BLE001
                        state.update({"异盘状态": "失败", "异盘错误": f"{type(e).__name__}: {e}"})
                        raise RuntimeError(f"异盘备份失败：{e}") from e
                elif not mirror_text:
                    state["异盘状态"] = "未配置"

            if 执行恢复演练 and _已到期(
                current,
                str(state.get("最近恢复演练") or ""),
                int(policy["恢复演练间隔天数"]),
            ):
                try:
                    drill = 恢复演练(latest)
                    state.update({
                        "恢复演练状态": "通过",
                        "最近恢复演练": current.isoformat(timespec="seconds"),
                        "恢复演练文件": str(latest),
                        "恢复演练文件数": drill["文件数"],
                        "恢复演练错误": "",
                    })
                except Exception as e:  # noqa: BLE001
                    state.update({"恢复演练状态": "失败", "恢复演练错误": f"{type(e).__name__}: {e}"})
                    raise RuntimeError(f"恢复演练失败：{e}") from e
            if state.get("状态") == "定时维护失败":
                state["状态"] = "维护正常"
            state["最近尝试"] = attempted_at
            state["错误"] = ""
            _写状态(state)
            return state
        except Exception as e:  # noqa: BLE001
            state.update({"状态": "定时维护失败", "最近尝试": attempted_at, "错误": f"{type(e).__name__}: {e}"})
            _写状态(state)
            raise


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", metavar="ZIP", help="只验证指定备份包")
    parser.add_argument(
        "--restore-drill",
        nargs="?",
        const="",
        metavar="ZIP",
        help="在临时目录恢复并跑 make check；不写真实公司，省略 ZIP 时使用最新备份",
    )
    parser.add_argument("--scheduled", action="store_true", help="按三天策略检查变化，并执行到期的异盘备份和恢复演练")
    parser.add_argument("--status", action="store_true", help="显示备份策略与最近运行状态")
    args = parser.parse_args()
    if args.verify:
        result = 验证(Path(args.verify))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    if args.status:
        print(json.dumps({"策略": 读取策略(), "状态": 读取状态()}, ensure_ascii=False, indent=2))
        return 0
    if args.scheduled:
        result = 定时维护()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.restore_drill is not None:
        archive = Path(args.restore_drill) if args.restore_drill else _最新备份()
        result = 恢复演练(archive)
        print(result["检查输出"].rstrip())
        print(
            f"恢复演练通过：{result['备份']}\n"
            f"文件 {result['文件数']} 个，解压 {result['解压字节']} 字节，真实公司未被覆盖。"
        )
        return 0
    out = 强制备份()
    print(f"已创建并验证：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
