"""Single-writer account registry and audit log."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .公告格式 import 清洗公告正文


_账户格式 = re.compile(r"acc_[0-9A-HJKMNP-TV-Z]{26}", re.IGNORECASE)
_实例格式 = re.compile(r"inst_[0-9A-HJKMNP-TV-Z]{26}", re.IGNORECASE)
_主人格式 = re.compile(r"own_[0-9A-HJKMNP-TV-Z]{26}", re.IGNORECASE)
_版本格式 = re.compile(r"twilight-v1\.0-beta\.[1-9]\d*")
_产品版本格式 = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_候选格式 = re.compile(r"candidate-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}")
_部署格式 = re.compile(r"deployment-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}")
_状态 = {"待绑定", "在用", "停用", "已删"}
_发布状态 = {"排队中", "同步中", "构建中", "更新账号中", "完成", "失败", "已中断"}
_发布进行中 = {"排队中", "同步中", "构建中", "更新账号中"}
_候选状态 = {"排队中", "验收中", "构建中", "待发布", "已封存", "失败", "已中断"}
_候选进行中 = {"排队中", "验收中", "构建中"}
_部署状态 = {"排队中", "更新账号中", "完成", "失败", "已中断"}
_部署进行中 = {"排队中", "更新账号中"}
_部署类型 = {"发布", "回退"}
_公告状态 = {"已发布", "已撤下"}
_反馈类型 = {"发现 Bug", "不好用", "功能建议", "其他"}
_反馈状态 = {"待处理", "处理中", "已解决", "不处理"}
_最大截图 = 5 * 1024 * 1024


class 注册表错误(RuntimeError):
    pass


def _现在() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _邮箱(value: str) -> str:
    email = str(value or "").strip().lower()
    if not email or len(email) > 320 or "@" not in email or any(ord(c) < 32 for c in email):
        raise 注册表错误("邮箱无效")
    return email


def _令牌哈希(token: str) -> str:
    raw = str(token or "")
    if not 32 <= len(raw) <= 512 or any(ord(c) < 33 or ord(c) > 126 for c in raw):
        raise 注册表错误("实例令牌无效")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _公告内容(title: str, body: str, body_rich: str) -> tuple[str, str, str]:
    title = str(title or "").strip()
    body = str(body or "").strip()
    body_rich = str(body_rich or "").strip()
    rich_plain = ""
    if body_rich:
        if len(body_rich) > 24000:
            raise 注册表错误("公告排版内容过长")
        body_rich, rich_plain = 清洗公告正文(body_rich)
        if rich_plain:
            body = rich_plain
    if not 1 <= len(title) <= 60 or any(ord(char) < 32 for char in title):
        raise 注册表错误("公告标题需为 1 至 60 个字符")
    if not 1 <= len(body) <= 8000 or any(ord(char) < 32 and char not in "\n\t" for char in body):
        raise 注册表错误("公告正文需为 1 至 8000 个字符")
    if body_rich and not rich_plain:
        raise 注册表错误("公告正文不能为空")
    return title, body, body_rich


def _开通值(record: dict[str, str]) -> dict[str, str]:
    account_id = str(record.get("account_id") or "")
    instance_id = str(record.get("instance_id") or "")
    owner_id = str(record.get("owner_id") or "")
    email = _邮箱(record.get("email", ""))
    if not _账户格式.fullmatch(account_id) or not _实例格式.fullmatch(instance_id) or not _主人格式.fullmatch(owner_id):
        raise 注册表错误("账户、实例或主人稳定 ID 无效")
    expected_upstream = f"http://company-{account_id.lower()}:8000"
    if record.get("upstream") != expected_upstream:
        raise 注册表错误("实例上游地址不符合固定规则")
    values = {
        "account_id": account_id,
        "email": email,
        "display_name": str(record.get("display_name") or "").strip(),
        "call_name": str(record.get("call_name") or "").strip(),
        "instance_id": instance_id,
        "owner_id": owner_id,
        "upstream": expected_upstream,
    }
    for field in ("display_name", "call_name"):
        if len(values[field]) > 32 or any(ord(c) < 32 for c in values[field]):
            raise 注册表错误(f"开通记录中的{field}无效")
    for field in (
        "chat_credential_ref", "search_credential_ref", "data_volume", "work_volume",
        "instance_config_path", "image_ref", "runner_image_ref",
    ):
        value = str(record.get(field) or "").strip()
        if not value or len(value) > 1024 or any(ord(c) < 32 for c in value):
            raise 注册表错误(f"开通记录缺少有效字段：{field}")
        values[field] = value
    return values


class 账户注册表:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._初始化()

    @contextmanager
    def _连接(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _初始化(self) -> None:
        with self._lock, self._连接() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    cf_sub TEXT UNIQUE,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT NOT NULL DEFAULT '',
                    call_name TEXT NOT NULL DEFAULT '',
                    instance_id TEXT NOT NULL UNIQUE,
                    owner_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK(status IN ('待绑定','在用','停用','已删')),
                    upstream TEXT NOT NULL,
                    control_token_hash TEXT NOT NULL UNIQUE,
                    chat_credential_ref TEXT NOT NULL,
                    search_credential_ref TEXT NOT NULL,
                    data_volume TEXT NOT NULL,
                    work_volume TEXT NOT NULL,
                    instance_config_path TEXT NOT NULL,
                    image_ref TEXT NOT NULL,
                    runner_image_ref TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    account_id TEXT,
                    result TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_audit_account_time
                    ON audit_log(account_id, occurred_at);
                CREATE TABLE IF NOT EXISTS provisioning (
                    account_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS releases (
                    version TEXT PRIMARY KEY,
                    notes TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    target_count INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_releases_created
                    ON releases(created_at DESC);
                CREATE TABLE IF NOT EXISTS release_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    notes TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_release_candidates_created
                    ON release_candidates(created_at DESC);
                CREATE TABLE IF NOT EXISTS release_deployments (
                    deployment_id TEXT PRIMARY KEY,
                    version TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('发布','回退')),
                    from_version TEXT,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    target_count INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_release_deployments_created
                    ON release_deployments(created_at DESC);
                CREATE TABLE IF NOT EXISTS announcements (
                    announcement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    body_rich TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('已发布','已撤下')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    published_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_announcements_status_published
                    ON announcements(status, published_at DESC, announcement_id DESC);
                CREATE TABLE IF NOT EXISTS feedback (
                    feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('发现 Bug','不好用','功能建议','其他')),
                    body TEXT NOT NULL,
                    page TEXT NOT NULL DEFAULT '',
                    version TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('待处理','处理中','已解决','不处理')),
                    screenshot_mime TEXT,
                    screenshot_blob BLOB,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id)
                );
                CREATE INDEX IF NOT EXISTS idx_feedback_status_created
                    ON feedback(status, created_at DESC, feedback_id DESC);
                CREATE INDEX IF NOT EXISTS idx_feedback_account_created
                    ON feedback(account_id, created_at DESC, feedback_id DESC);
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(accounts)").fetchall()}
            if "display_name" not in columns:
                conn.execute("ALTER TABLE accounts ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")
            if "call_name" not in columns:
                conn.execute("ALTER TABLE accounts ADD COLUMN call_name TEXT NOT NULL DEFAULT ''")
            announcement_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(announcements)").fetchall()
            }
            if "body_rich" not in announcement_columns:
                conn.execute("ALTER TABLE announcements ADD COLUMN body_rich TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _审计(
        conn: sqlite3.Connection,
        *,
        actor: str,
        action: str,
        account_id: str | None,
        result: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            "INSERT INTO audit_log(occurred_at,actor,action,account_id,result,details_json) VALUES(?,?,?,?,?,?)",
            (_现在(), actor, action, account_id, result, json.dumps(details or {}, ensure_ascii=False, sort_keys=True)),
        )

    @staticmethod
    def _公开(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "account_id": row["account_id"],
            "email": row["email"],
            "display_name": row["display_name"],
            "call_name": row["call_name"],
            "instance_id": row["instance_id"],
            "owner_id": row["owner_id"],
            "状态": row["status"],
            "upstream": row["upstream"],
            "image_ref": row["image_ref"],
            "runner_image_ref": row["runner_image_ref"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _内部(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data.pop("control_token_hash", None)
        return data

    def 开始开通(self, record: dict[str, str], *, actor: str = "owner-cli") -> dict[str, Any]:
        values = _开通值(record)
        stable_record = {key: values[key] for key in values}
        now = _现在()
        with self._lock, self._连接() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                occupied = conn.execute(
                    "SELECT account_id FROM accounts WHERE email=? UNION ALL SELECT account_id FROM provisioning WHERE email=?",
                    (values["email"], values["email"]),
                ).fetchone()
                if occupied is not None:
                    raise 注册表错误("该邮箱已有账户或正在开通")
                conn.execute(
                    "INSERT INTO provisioning(account_id,email,record_json,created_at,updated_at) VALUES(?,?,?,?,?)",
                    (
                        values["account_id"], values["email"],
                        json.dumps(stable_record, ensure_ascii=False, sort_keys=True), now, now,
                    ),
                )
                self._审计(
                    conn, actor=actor, action="provision-start", account_id=values["account_id"],
                    result="pending", details={"email": values["email"]},
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise 注册表错误("邮箱、账户或稳定 ID 已存在") from exc
            except Exception:
                conn.rollback()
                raise
        return dict(stable_record)

    def 待恢复开通(self) -> list[dict[str, str]]:
        with self._lock, self._连接() as conn:
            rows = conn.execute("SELECT record_json FROM provisioning ORDER BY created_at,account_id").fetchall()
        records: list[dict[str, str]] = []
        for row in rows:
            try:
                raw = json.loads(row["record_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise 注册表错误("开通恢复账损坏，拒绝自动清理") from exc
            if not isinstance(raw, dict):
                raise 注册表错误("开通恢复账损坏，拒绝自动清理")
            records.append(_开通值(raw))
        return records

    def 取消未完成开通(
        self,
        account_id: str,
        *,
        actor: str = "supervisor",
        details: dict[str, Any] | None = None,
    ) -> None:
        if not _账户格式.fullmatch(str(account_id or "")):
            raise 注册表错误("账户标识无效")
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = conn.execute("DELETE FROM provisioning WHERE account_id=?", (account_id,))
            if result.rowcount != 1:
                conn.rollback()
                raise 注册表错误("未找到待恢复的开通记录")
            self._审计(
                conn, actor=actor, action="provision-rollback", account_id=account_id,
                result="ok", details=details,
            )
            conn.commit()

    def 完成开通(self, record: dict[str, str], *, control_token: str, actor: str = "owner-cli") -> dict[str, Any]:
        values = _开通值(record)
        values["control_token_hash"] = _令牌哈希(control_token)
        now = _现在()
        with self._lock, self._连接() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                pending = conn.execute(
                    "SELECT record_json FROM provisioning WHERE account_id=?", (values["account_id"],)
                ).fetchone()
                if pending is None:
                    raise 注册表错误("开通事务没有持久恢复账")
                try:
                    expected = _开通值(json.loads(pending["record_json"]))
                except (TypeError, json.JSONDecodeError) as exc:
                    raise 注册表错误("开通恢复账损坏，拒绝完成开户") from exc
                if expected != {key: values[key] for key in expected}:
                    raise 注册表错误("开通完成记录与恢复账不一致")
                conn.execute(
                    """INSERT INTO accounts(
                        account_id,cf_sub,email,display_name,call_name,instance_id,owner_id,status,upstream,control_token_hash,
                        chat_credential_ref,search_credential_ref,data_volume,work_volume,
                        instance_config_path,image_ref,runner_image_ref,created_at,updated_at
                    ) VALUES(?,NULL,?,?,?,?,?,'待绑定',?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        values["account_id"], values["email"], values["display_name"], values["call_name"],
                        values["instance_id"], values["owner_id"],
                        values["upstream"], values["control_token_hash"], values["chat_credential_ref"],
                        values["search_credential_ref"], values["data_volume"], values["work_volume"],
                        values["instance_config_path"], values["image_ref"], values["runner_image_ref"], now, now,
                    ),
                )
                conn.execute("DELETE FROM provisioning WHERE account_id=?", (values["account_id"],))
                self._审计(
                    conn, actor=actor, action="provision", account_id=values["account_id"],
                    result="ok", details={"email": values["email"]},
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise 注册表错误("邮箱、账户或稳定 ID 已存在") from exc
            except Exception:
                conn.rollback()
                raise
        return self.状态(values["account_id"])

    def 解析身份(self, *, email: str, sub: str) -> dict[str, Any]:
        email = _邮箱(email)
        sub = str(sub or "").strip()
        if not sub or len(sub) > 512 or any(ord(c) < 32 for c in sub):
            raise 注册表错误("Cloudflare sub 无效")
        error = ""
        result: dict[str, Any] | None = None
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
            if row is None:
                error = "邮箱未开通"
                self._审计(conn, actor="public-gw", action="resolve-identity", account_id=None, result="deny", details={"email": email, "reason": error})
            elif row["status"] == "待绑定" and not row["cf_sub"]:
                occupied = conn.execute("SELECT account_id FROM accounts WHERE cf_sub=?", (sub,)).fetchone()
                if occupied is not None:
                    error = "该 Cloudflare 身份已绑定其他账户"
                    self._审计(conn, actor="public-gw", action="resolve-identity", account_id=row["account_id"], result="deny", details={"email": email, "reason": error})
                else:
                    now = _现在()
                    conn.execute(
                        "UPDATE accounts SET cf_sub=?,status='在用',updated_at=? WHERE account_id=? AND status='待绑定' AND cf_sub IS NULL",
                        (sub, now, row["account_id"]),
                    )
                    row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (row["account_id"],)).fetchone()
                    self._审计(conn, actor="public-gw", action="first-bind", account_id=row["account_id"], result="ok", details={"email": email})
                    result = self._公开(row)
            elif row["status"] == "在用" and row["cf_sub"] == sub:
                result = self._公开(row)
            elif row["status"] == "在用":
                error = "Cloudflare sub 已变化，必须由船主重新绑定"
                self._审计(conn, actor="public-gw", action="resolve-identity", account_id=row["account_id"], result="deny", details={"email": email, "reason": error})
            else:
                error = f"账户当前为{row['status']}状态"
                self._审计(conn, actor="public-gw", action="resolve-identity", account_id=row["account_id"], result="deny", details={"email": email, "reason": error})
            conn.commit()
        if error:
            raise 注册表错误(error)
        assert result is not None
        return result

    def 状态(self, account_id: str) -> dict[str, Any]:
        if not _账户格式.fullmatch(str(account_id or "")):
            raise 注册表错误("账户标识无效")
        with self._lock, self._连接() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
        if row is None:
            raise 注册表错误("账户不存在")
        return self._公开(row)

    def 列表(self) -> list[dict[str, Any]]:
        with self._lock, self._连接() as conn:
            rows = conn.execute(
                "SELECT * FROM accounts WHERE status<>'已删' ORDER BY created_at DESC,account_id DESC"
            ).fetchall()
        return [self._公开(row) for row in rows]

    def 内部账户(self, account_id: str) -> dict[str, Any]:
        if not _账户格式.fullmatch(str(account_id or "")):
            raise 注册表错误("账户标识无效")
        with self._lock, self._连接() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
        if row is None:
            raise 注册表错误("账户不存在")
        return self._内部(row)

    def 更新用户资料(self, account_id: str, *, display_name: str, call_name: str) -> dict[str, Any]:
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status,display_name,call_name FROM accounts WHERE account_id=?", (account_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                raise 注册表错误("账户不存在")
            if row["status"] != "在用":
                conn.rollback()
                raise 注册表错误("只有在用账户可以修改资料")
            conn.execute(
                "UPDATE accounts SET display_name=?,call_name=?,updated_at=? WHERE account_id=?",
                (display_name, call_name, _现在(), account_id),
            )
            self._审计(
                conn, actor="user", action="update-profile", account_id=account_id, result="ok",
                details={
                    "display_name_changed": display_name != row["display_name"],
                    "call_name_changed": call_name != row["call_name"],
                },
            )
            conn.commit()
        return self.状态(account_id)

    def 更新版本(
        self,
        account_id: str,
        *,
        old_image_ref: str,
        image_ref: str,
        runner_image_ref: str,
        actor: str = "owner-cli",
    ) -> dict[str, Any]:
        for name, value in (("公司镜像", image_ref), ("执行器镜像", runner_image_ref)):
            if not value or len(value) > 1024 or any(ord(c) < 32 for c in value):
                raise 注册表错误(f"{name}版本无效")
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status,image_ref,runner_image_ref FROM accounts WHERE account_id=?", (account_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                raise 注册表错误("账户不存在")
            if row["status"] != "在用":
                conn.rollback()
                raise 注册表错误("只有在用账户可以升级")
            if row["image_ref"] != old_image_ref:
                conn.rollback()
                raise 注册表错误("账户版本已经变化，拒绝覆盖")
            conn.execute(
                "UPDATE accounts SET image_ref=?,runner_image_ref=?,updated_at=? WHERE account_id=?",
                (image_ref, runner_image_ref, _现在(), account_id),
            )
            self._审计(
                conn, actor=actor, action="upgrade-version", account_id=account_id, result="ok",
                details={
                    "from_company": row["image_ref"], "to_company": image_ref,
                    "from_runner": row["runner_image_ref"], "to_runner": runner_image_ref,
                },
            )
            conn.commit()
        return self.状态(account_id)

    def 按控制令牌(self, token: str) -> dict[str, Any]:
        digest = _令牌哈希(token)
        with self._lock, self._连接() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE control_token_hash=?", (digest,)).fetchone()
        if row is None:
            raise 注册表错误("实例令牌无效")
        if row["status"] != "在用":
            raise 注册表错误("账户未启用")
        return self._内部(row)

    def 重新绑定(self, account_id: str, new_sub: str, *, actor: str = "owner-cli") -> dict[str, Any]:
        new_sub = str(new_sub or "").strip()
        if not new_sub or len(new_sub) > 512 or any(ord(c) < 32 for c in new_sub):
            raise 注册表错误("新 sub 无效")
        with self._lock, self._连接() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
                if row is None or row["status"] != "在用":
                    raise 注册表错误("只有在用账户可以重新绑定")
                occupied = conn.execute("SELECT account_id FROM accounts WHERE cf_sub=? AND account_id<>?", (new_sub, account_id)).fetchone()
                if occupied is not None:
                    raise 注册表错误("该 sub 已绑定其他账户")
                conn.execute("UPDATE accounts SET cf_sub=?,updated_at=? WHERE account_id=?", (new_sub, _现在(), account_id))
                self._审计(conn, actor=actor, action="rebind", account_id=account_id, result="ok")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self.状态(account_id)

    def 置状态(self, account_id: str, status: str, *, action: str, actor: str = "owner-cli") -> dict[str, Any]:
        if status not in _状态:
            raise 注册表错误("目标状态无效")
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM accounts WHERE account_id=?", (account_id,)).fetchone()
            if row is None:
                conn.rollback()
                raise 注册表错误("账户不存在")
            conn.execute("UPDATE accounts SET status=?,updated_at=? WHERE account_id=?", (status, _现在(), account_id))
            self._审计(conn, actor=actor, action=action, account_id=account_id, result="ok", details={"from": row["status"], "to": status})
            conn.commit()
        return self.状态(account_id)

    def 记审计(self, *, actor: str, action: str, account_id: str | None, result: str, details: dict[str, Any] | None = None) -> None:
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._审计(conn, actor=actor, action=action, account_id=account_id, result=result, details=details)
            conn.commit()

    def 审计记录(self, account_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        with self._lock, self._连接() as conn:
            if account_id:
                rows = conn.execute(
                    "SELECT * FROM audit_log WHERE account_id=? ORDER BY audit_id DESC LIMIT ?", (account_id, limit)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM audit_log ORDER BY audit_id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _公开公告(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "announcement_id": row["announcement_id"],
            "title": row["title"],
            "body": row["body"],
            "body_rich": row["body_rich"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "published_at": row["published_at"],
        }

    def 发布公告(
        self,
        title: str,
        body: str,
        *,
        body_rich: str = "",
        actor: str = "owner-ui",
    ) -> dict[str, Any]:
        title, body, body_rich = _公告内容(title, body, body_rich)
        now = _现在()
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = conn.execute(
                """INSERT INTO announcements(title,body,body_rich,status,created_at,updated_at,published_at)
                    VALUES(?,?,?,'已发布',?,?,?)""",
                (title, body, body_rich, now, now, now),
            )
            announcement_id = int(result.lastrowid)
            self._审计(
                conn, actor=actor, action="publish-announcement", account_id=None,
                result="ok", details={
                    "announcement_id": announcement_id,
                    "title": title,
                    "rich_text": bool(body_rich),
                },
            )
            conn.commit()
        return self.公告(announcement_id)

    def 编辑公告(
        self,
        announcement_id: int,
        title: str,
        body: str,
        *,
        body_rich: str = "",
        actor: str = "owner-ui",
    ) -> dict[str, Any]:
        try:
            announcement_id = int(announcement_id)
        except (TypeError, ValueError) as exc:
            raise 注册表错误("公告编号无效") from exc
        if announcement_id < 1:
            raise 注册表错误("公告编号无效")
        title, body, body_rich = _公告内容(title, body, body_rich)
        now = _现在()
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT title,status FROM announcements WHERE announcement_id=?", (announcement_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                raise 注册表错误("公告不存在")
            conn.execute(
                "UPDATE announcements SET title=?,body=?,body_rich=?,updated_at=? WHERE announcement_id=?",
                (title, body, body_rich, now, announcement_id),
            )
            self._审计(
                conn, actor=actor, action="edit-announcement", account_id=None, result="ok",
                details={
                    "announcement_id": announcement_id,
                    "from_title": row["title"],
                    "title": title,
                    "status": row["status"],
                    "rich_text": bool(body_rich),
                },
            )
            conn.commit()
        return self.公告(announcement_id)

    def 公告(self, announcement_id: int) -> dict[str, Any]:
        try:
            announcement_id = int(announcement_id)
        except (TypeError, ValueError) as exc:
            raise 注册表错误("公告编号无效") from exc
        if announcement_id < 1:
            raise 注册表错误("公告编号无效")
        with self._lock, self._连接() as conn:
            row = conn.execute(
                "SELECT * FROM announcements WHERE announcement_id=?", (announcement_id,)
            ).fetchone()
        if row is None:
            raise 注册表错误("公告不存在")
        return self._公开公告(row)

    def 公告列表(self, *, only_published: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._lock, self._连接() as conn:
            if only_published:
                rows = conn.execute(
                    """SELECT * FROM announcements WHERE status='已发布'
                        ORDER BY published_at DESC,announcement_id DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM announcements ORDER BY created_at DESC,announcement_id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._公开公告(row) for row in rows]

    def 设置公告状态(
        self,
        announcement_id: int,
        status: str,
        *,
        actor: str = "owner-ui",
    ) -> dict[str, Any]:
        try:
            announcement_id = int(announcement_id)
        except (TypeError, ValueError) as exc:
            raise 注册表错误("公告编号无效") from exc
        if announcement_id < 1 or status not in _公告状态:
            raise 注册表错误("公告编号或状态无效")
        now = _现在()
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status,title FROM announcements WHERE announcement_id=?", (announcement_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                raise 注册表错误("公告不存在")
            published_at = now if status == "已发布" else None
            if published_at:
                conn.execute(
                    "UPDATE announcements SET status=?,updated_at=?,published_at=? WHERE announcement_id=?",
                    (status, now, published_at, announcement_id),
                )
            else:
                conn.execute(
                    "UPDATE announcements SET status=?,updated_at=? WHERE announcement_id=?",
                    (status, now, announcement_id),
                )
            action = "publish-announcement" if status == "已发布" else "withdraw-announcement"
            self._审计(
                conn, actor=actor, action=action, account_id=None, result="ok",
                details={"announcement_id": announcement_id, "title": row["title"], "from": row["status"]},
            )
            conn.commit()
        return self.公告(announcement_id)

    @staticmethod
    def _反馈截图(value: str) -> tuple[str | None, bytes | None]:
        raw = str(value or "")
        if not raw:
            return None, None
        if len(raw) > 7 * 1024 * 1024:
            raise 注册表错误("截图不能超过 5MB")
        try:
            header, encoded = raw.split(",", 1)
        except ValueError as exc:
            raise 注册表错误("截图内容无效") from exc
        allowed = {
            "data:image/png;base64": ("image/png", b"\x89PNG\r\n\x1a\n"),
            "data:image/jpeg;base64": ("image/jpeg", b"\xff\xd8\xff"),
            "data:image/webp;base64": ("image/webp", b"RIFF"),
        }
        selected = allowed.get(header.lower())
        if selected is None:
            raise 注册表错误("截图只支持 PNG、JPEG 或 WebP")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise 注册表错误("截图内容无效") from exc
        mime, signature = selected
        if not data or len(data) > _最大截图 or not data.startswith(signature):
            raise 注册表错误("截图内容无效或超过 5MB")
        if mime == "image/webp" and (len(data) < 12 or data[8:12] != b"WEBP"):
            raise 注册表错误("截图内容无效")
        return mime, data

    @staticmethod
    def _公开反馈(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "feedback_id": row["feedback_id"],
            "account_id": row["account_id"],
            "email": row["email"],
            "display_name": row["display_name"],
            "call_name": row["call_name"],
            "kind": row["kind"],
            "body": row["body"],
            "page": row["page"],
            "version": row["version"],
            "status": row["status"],
            "has_screenshot": bool(row["screenshot_mime"]),
            "screenshot_mime": row["screenshot_mime"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def 提交反馈(
        self,
        account_id: str,
        kind: str,
        body: str,
        page: str,
        screenshot_data_url: str = "",
        *,
        actor: str = "user-ui",
    ) -> dict[str, Any]:
        account_id = str(account_id or "").strip()
        kind = str(kind or "").strip()
        body = str(body or "").strip()
        page = str(page or "").strip()
        if not _账户格式.fullmatch(account_id):
            raise 注册表错误("反馈账户无效")
        if kind not in _反馈类型:
            raise 注册表错误("反馈类型无效")
        if not 1 <= len(body) <= 5000 or any(ord(char) < 32 and char not in "\n\t" for char in body):
            raise 注册表错误("反馈内容需为 1 至 5000 个字符")
        if len(page) > 512 or any(ord(char) < 32 for char in page):
            raise 注册表错误("反馈页面信息无效")
        screenshot_mime, screenshot_blob = self._反馈截图(screenshot_data_url)
        now = _现在()
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account = conn.execute(
                "SELECT status,image_ref FROM accounts WHERE account_id=?", (account_id,)
            ).fetchone()
            if account is None or account["status"] != "在用":
                conn.rollback()
                raise 注册表错误("当前账户不能提交反馈")
            result = conn.execute(
                """INSERT INTO feedback(
                    account_id,kind,body,page,version,status,screenshot_mime,screenshot_blob,created_at,updated_at
                ) VALUES(?,?,?,?,?,'待处理',?,?,?,?)""",
                (
                    account_id, kind, body, page, account["image_ref"], screenshot_mime,
                    screenshot_blob, now, now,
                ),
            )
            feedback_id = int(result.lastrowid)
            self._审计(
                conn, actor=actor, action="submit-feedback", account_id=account_id, result="ok",
                details={"feedback_id": feedback_id, "kind": kind, "has_screenshot": bool(screenshot_mime)},
            )
            conn.commit()
        return self.反馈(feedback_id)

    def 反馈列表(self, *, status: str | None = None, limit: int = 300) -> list[dict[str, Any]]:
        if status is not None and status not in _反馈状态:
            raise 注册表错误("反馈状态无效")
        limit = max(1, min(int(limit), 500))
        query = """SELECT f.*,a.email,a.display_name,a.call_name
            FROM feedback f JOIN accounts a ON a.account_id=f.account_id"""
        params: tuple[Any, ...]
        if status is None:
            query += " ORDER BY f.created_at DESC,f.feedback_id DESC LIMIT ?"
            params = (limit,)
        else:
            query += " WHERE f.status=? ORDER BY f.created_at DESC,f.feedback_id DESC LIMIT ?"
            params = (status, limit)
        with self._lock, self._连接() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._公开反馈(row) for row in rows]

    def 反馈(self, feedback_id: int) -> dict[str, Any]:
        try:
            feedback_id = int(feedback_id)
        except (TypeError, ValueError) as exc:
            raise 注册表错误("反馈编号无效") from exc
        if feedback_id < 1:
            raise 注册表错误("反馈编号无效")
        with self._lock, self._连接() as conn:
            row = conn.execute(
                """SELECT f.*,a.email,a.display_name,a.call_name
                    FROM feedback f JOIN accounts a ON a.account_id=f.account_id
                    WHERE f.feedback_id=?""",
                (feedback_id,),
            ).fetchone()
        if row is None:
            raise 注册表错误("反馈不存在")
        return self._公开反馈(row)

    def 反馈截图(self, feedback_id: int) -> tuple[str, bytes]:
        try:
            feedback_id = int(feedback_id)
        except (TypeError, ValueError) as exc:
            raise 注册表错误("反馈编号无效") from exc
        with self._lock, self._连接() as conn:
            row = conn.execute(
                "SELECT screenshot_mime,screenshot_blob FROM feedback WHERE feedback_id=?",
                (feedback_id,),
            ).fetchone()
        if row is None or not row["screenshot_mime"] or row["screenshot_blob"] is None:
            raise 注册表错误("反馈没有截图")
        return str(row["screenshot_mime"]), bytes(row["screenshot_blob"])

    def 设置反馈状态(
        self,
        feedback_id: int,
        status: str,
        *,
        actor: str = "owner-ui",
    ) -> dict[str, Any]:
        try:
            feedback_id = int(feedback_id)
        except (TypeError, ValueError) as exc:
            raise 注册表错误("反馈编号无效") from exc
        status = str(status or "").strip()
        if feedback_id < 1 or status not in _反馈状态:
            raise 注册表错误("反馈编号或状态无效")
        now = _现在()
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT account_id,status FROM feedback WHERE feedback_id=?", (feedback_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                raise 注册表错误("反馈不存在")
            conn.execute(
                "UPDATE feedback SET status=?,updated_at=? WHERE feedback_id=?",
                (status, now, feedback_id),
            )
            self._审计(
                conn, actor=actor, action="update-feedback-status", account_id=row["account_id"],
                result="ok", details={"feedback_id": feedback_id, "from": row["status"], "to": status},
            )
            conn.commit()
        return self.反馈(feedback_id)

    @staticmethod
    def _详情(value: Any, error: str) -> dict[str, Any]:
        try:
            details = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {"error": error}
        return details if isinstance(details, dict) else {"error": error}

    @classmethod
    def _公开候选(cls, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "candidate_id": row["candidate_id"],
            "notes": row["notes"],
            "status": row["status"],
            "stage": row["stage"],
            "details": cls._详情(row["details_json"], "候选详情损坏"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
        }

    def 创建候选记录(
        self,
        candidate_id: str,
        notes: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        candidate_id = str(candidate_id or "").strip()
        notes = str(notes or "").strip()
        if not _候选格式.fullmatch(candidate_id):
            raise 注册表错误("候选编号无效")
        if len(notes) > 1000 or any(ord(char) < 32 and char not in "\n\t" for char in notes):
            raise 注册表错误("候选说明无效")
        if details is not None and not isinstance(details, dict):
            raise 注册表错误("候选详情无效")
        encoded = json.dumps(details or {}, ensure_ascii=False, sort_keys=True)
        now = _现在()
        with self._lock, self._连接() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                running_candidate = conn.execute(
                    "SELECT candidate_id FROM release_candidates "
                    "WHERE status IN ('排队中','验收中','构建中') LIMIT 1"
                ).fetchone()
                running_deployment = conn.execute(
                    "SELECT deployment_id FROM release_deployments "
                    "WHERE status IN ('排队中','更新账号中') LIMIT 1"
                ).fetchone()
                if running_candidate is not None:
                    raise 注册表错误(f"候选 {running_candidate['candidate_id']} 仍在处理中")
                if running_deployment is not None:
                    raise 注册表错误(f"部署 {running_deployment['deployment_id']} 仍在进行中")
                conn.execute(
                    """INSERT INTO release_candidates(
                        candidate_id,notes,status,stage,details_json,created_at,updated_at,completed_at
                    ) VALUES(?,?,'排队中','等待验收',?,?,?,NULL)""",
                    (candidate_id, notes, encoded, now, now),
                )
                self._审计(
                    conn, actor="owner-ui", action="release-candidate-start", account_id=None,
                    result="pending", details={"candidate_id": candidate_id},
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise 注册表错误("候选记录已经存在") from exc
            except Exception:
                conn.rollback()
                raise
        record = self.候选记录(candidate_id=candidate_id)
        assert record is not None
        return record

    def 发布维护状态(self) -> dict[str, str] | None:
        """Return the operation that currently owns the release maintenance window."""
        with self._lock, self._连接() as conn:
            candidate = conn.execute(
                "SELECT candidate_id,status,stage FROM release_candidates "
                "WHERE status IN ('排队中','验收中','构建中') ORDER BY created_at LIMIT 1"
            ).fetchone()
            deployment = conn.execute(
                "SELECT deployment_id,status,stage FROM release_deployments "
                "WHERE status IN ('排队中','更新账号中') ORDER BY created_at LIMIT 1"
            ).fetchone()
        if candidate is not None:
            return {
                "kind": "candidate", "id": candidate["candidate_id"],
                "status": candidate["status"], "stage": candidate["stage"],
            }
        if deployment is not None:
            return {
                "kind": "deployment", "id": deployment["deployment_id"],
                "status": deployment["status"], "stage": deployment["stage"],
            }
        return None

    def 要求没有发布维护(self) -> None:
        operation = self.发布维护状态()
        if operation is not None:
            raise 注册表错误(f"发布系统正在处理：{operation['stage']}；请完成后再改动账号或重启后台")

    def 更新候选记录(
        self,
        candidate_id: str,
        *,
        status: str,
        stage: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in _候选状态:
            raise 注册表错误("候选状态无效")
        stage = str(stage or "").strip()
        if not stage or len(stage) > 80:
            raise 注册表错误("候选阶段无效")
        encoded = json.dumps(details or {}, ensure_ascii=False, sort_keys=True)
        now = _现在()
        completed_at = None if status in _候选进行中 else now
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT status FROM release_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            result = conn.execute(
                """UPDATE release_candidates SET status=?,stage=?,details_json=?,updated_at=?,completed_at=?
                    WHERE candidate_id=?""",
                (status, stage, encoded, now, completed_at, candidate_id),
            )
            if result.rowcount != 1:
                conn.rollback()
                raise 注册表错误("候选记录不存在")
            if completed_at and previous is not None and previous["status"] != status:
                self._审计(
                    conn, actor="release-service",
                    action="release-candidate-finish" if previous["status"] in _候选进行中 else "release-candidate-seal",
                    account_id=None,
                    result="ok" if status in {"待发布", "已封存"} else "failed",
                    details={"candidate_id": candidate_id, "status": status},
                )
            conn.commit()
        record = self.候选记录(candidate_id=candidate_id)
        assert record is not None
        return record

    def 候选记录(self, *, candidate_id: str | None = None) -> dict[str, Any] | None:
        with self._lock, self._连接() as conn:
            if candidate_id:
                row = conn.execute(
                    "SELECT * FROM release_candidates WHERE candidate_id=?", (candidate_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM release_candidates ORDER BY created_at DESC,candidate_id DESC LIMIT 1"
                ).fetchone()
        return self._公开候选(row) if row is not None else None

    def 候选列表(self, *, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(100, int(limit)))
        with self._lock, self._连接() as conn:
            rows = conn.execute(
                "SELECT * FROM release_candidates ORDER BY created_at DESC,candidate_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._公开候选(row) for row in rows]

    @classmethod
    def _公开部署(cls, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "deployment_id": row["deployment_id"],
            "version": row["version"],
            "kind": row["kind"],
            "from_version": row["from_version"],
            "status": row["status"],
            "stage": row["stage"],
            "target_count": row["target_count"],
            "success_count": row["success_count"],
            "failed_count": row["failed_count"],
            "details": cls._详情(row["details_json"], "部署详情损坏"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
        }

    def 创建部署记录(
        self,
        deployment_id: str,
        version: str,
        *,
        kind: str,
        from_version: str | None,
        target_count: int,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        deployment_id = str(deployment_id or "").strip()
        version = str(version or "").strip()
        from_version = str(from_version or "").strip() or None
        if not _部署格式.fullmatch(deployment_id):
            raise 注册表错误("部署编号无效")
        if not _产品版本格式.fullmatch(version):
            raise 注册表错误("部署版本号无效")
        if from_version is not None and not _产品版本格式.fullmatch(from_version):
            raise 注册表错误("部署前版本号无效")
        if kind not in _部署类型:
            raise 注册表错误("部署类型无效")
        if details is not None and not isinstance(details, dict):
            raise 注册表错误("部署详情无效")
        target_count = max(0, int(target_count))
        encoded = json.dumps(details or {}, ensure_ascii=False, sort_keys=True)
        now = _现在()
        with self._lock, self._连接() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                running = conn.execute(
                    "SELECT deployment_id FROM release_deployments "
                    "WHERE status IN ('排队中','更新账号中') LIMIT 1"
                ).fetchone()
                running_candidate = conn.execute(
                    "SELECT candidate_id FROM release_candidates "
                    "WHERE status IN ('排队中','验收中','构建中') LIMIT 1"
                ).fetchone()
                if running is not None:
                    raise 注册表错误(f"部署 {running['deployment_id']} 仍在进行中")
                if running_candidate is not None:
                    raise 注册表错误(f"候选 {running_candidate['candidate_id']} 仍在处理中")
                conn.execute(
                    """INSERT INTO release_deployments(
                        deployment_id,version,kind,from_version,status,stage,target_count,
                        success_count,failed_count,details_json,created_at,updated_at,completed_at
                    ) VALUES(?,?,?,?,'排队中','等待开始',?,0,0,?,?,?,NULL)""",
                    (deployment_id, version, kind, from_version, target_count, encoded, now, now),
                )
                self._审计(
                    conn, actor="owner-ui", action="release-deployment-start", account_id=None,
                    result="pending", details={
                        "deployment_id": deployment_id, "version": version, "kind": kind,
                        "from_version": from_version, "target_count": target_count,
                    },
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise 注册表错误("部署记录已经存在") from exc
            except Exception:
                conn.rollback()
                raise
        record = self.部署记录(deployment_id=deployment_id)
        assert record is not None
        return record

    def 更新部署记录(
        self,
        deployment_id: str,
        *,
        status: str,
        stage: str,
        target_count: int,
        success_count: int,
        failed_count: int,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in _部署状态:
            raise 注册表错误("部署状态无效")
        stage = str(stage or "").strip()
        if not stage or len(stage) > 80:
            raise 注册表错误("部署阶段无效")
        counts = tuple(max(0, int(value)) for value in (target_count, success_count, failed_count))
        encoded = json.dumps(details or {}, ensure_ascii=False, sort_keys=True)
        now = _现在()
        completed_at = None if status in _部署进行中 else now
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT status FROM release_deployments WHERE deployment_id=?", (deployment_id,)
            ).fetchone()
            result = conn.execute(
                """UPDATE release_deployments SET status=?,stage=?,target_count=?,success_count=?,
                    failed_count=?,details_json=?,updated_at=?,completed_at=? WHERE deployment_id=?""",
                (status, stage, *counts, encoded, now, completed_at, deployment_id),
            )
            if result.rowcount != 1:
                conn.rollback()
                raise 注册表错误("部署记录不存在")
            if completed_at and previous is not None and previous["status"] != status:
                self._审计(
                    conn, actor="release-service", action="release-deployment-finish", account_id=None,
                    result="ok" if status == "完成" else "failed",
                    details={
                        "deployment_id": deployment_id, "status": status,
                        "success": counts[1], "failed": counts[2],
                    },
                )
            conn.commit()
        record = self.部署记录(deployment_id=deployment_id)
        assert record is not None
        return record

    def 部署记录(self, *, deployment_id: str | None = None) -> dict[str, Any] | None:
        with self._lock, self._连接() as conn:
            if deployment_id:
                row = conn.execute(
                    "SELECT * FROM release_deployments WHERE deployment_id=?", (deployment_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM release_deployments ORDER BY created_at DESC,deployment_id DESC LIMIT 1"
                ).fetchone()
        return self._公开部署(row) if row is not None else None

    def 部署列表(self, *, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(100, int(limit)))
        with self._lock, self._连接() as conn:
            rows = conn.execute(
                "SELECT * FROM release_deployments ORDER BY created_at DESC,deployment_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._公开部署(row) for row in rows]

    def 未解决发布恢复(self) -> list[dict[str, Any]]:
        """Return deployments that still retain rollback failures or rescue artifacts."""
        with self._lock, self._连接() as conn:
            rows = conn.execute(
                "SELECT * FROM release_deployments WHERE status IN ('失败','已中断') "
                "ORDER BY created_at,deployment_id"
            ).fetchall()
        unresolved = []
        for row in rows:
            record = self._公开部署(row)
            details = record.get("details") if isinstance(record.get("details"), dict) else {}
            if details.get("rollback_failures") or details.get("retained_unactivated_release"):
                unresolved.append(record)
        return unresolved

    @staticmethod
    def _公开发布(row: sqlite3.Row) -> dict[str, Any]:
        try:
            details = json.loads(row["details_json"])
        except (TypeError, json.JSONDecodeError):
            details = {"error": "发布详情损坏"}
        if not isinstance(details, dict):
            details = {"error": "发布详情损坏"}
        return {
            "version": row["version"],
            "notes": row["notes"],
            "status": row["status"],
            "stage": row["stage"],
            "target_count": row["target_count"],
            "success_count": row["success_count"],
            "failed_count": row["failed_count"],
            "details": details,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
        }

    def 创建发布(
        self,
        version: str,
        notes: str,
        *,
        target_count: int,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        version = str(version or "").strip()
        notes = str(notes or "").strip()
        if not _版本格式.fullmatch(version):
            raise 注册表错误("发布版本号无效")
        if len(notes) > 1000 or any(ord(char) < 32 and char not in "\n\t" for char in notes):
            raise 注册表错误("更新说明无效")
        if details is not None and not isinstance(details, dict):
            raise 注册表错误("发布来源信息无效")
        encoded_details = json.dumps(details or {}, ensure_ascii=False, sort_keys=True)
        source_details = (details or {}).get("source")
        source_details = source_details if isinstance(source_details, dict) else {}
        formal_details = source_details.get("formal")
        formal_details = formal_details if isinstance(formal_details, dict) else {}
        multiuser_details = source_details.get("multiuser")
        multiuser_details = multiuser_details if isinstance(multiuser_details, dict) else {}
        target_count = max(0, int(target_count))
        now = _现在()
        with self._lock, self._连接() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                running = conn.execute(
                    "SELECT version FROM releases WHERE status IN ('排队中','同步中','构建中','更新账号中') LIMIT 1"
                ).fetchone()
                if running is not None:
                    raise 注册表错误(f"版本 {running['version']} 仍在发布中")
                conn.execute(
                    """INSERT INTO releases(
                        version,notes,status,stage,target_count,success_count,failed_count,
                        details_json,created_at,updated_at,completed_at
                    ) VALUES(?,?,'排队中','等待开始',?,0,0,?,?,?,NULL)""",
                    (version, notes, target_count, encoded_details, now, now),
                )
                self._审计(
                    conn, actor="owner-ui", action="release-start", account_id=None,
                    result="pending", details={
                        "version": version,
                        "target_count": target_count,
                        "formal_head": formal_details.get("head", ""),
                        "multiuser_head": multiuser_details.get("head", ""),
                    },
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise 注册表错误("该版本已有发布记录") from exc
            except Exception:
                conn.rollback()
                raise
        record = self.发布记录(version=version)
        assert record is not None
        return record

    def 更新发布(
        self,
        version: str,
        *,
        status: str,
        stage: str,
        target_count: int,
        success_count: int,
        failed_count: int,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in _发布状态:
            raise 注册表错误("发布状态无效")
        stage = str(stage or "").strip()
        if not stage or len(stage) > 80:
            raise 注册表错误("发布阶段无效")
        counts = tuple(max(0, int(value)) for value in (target_count, success_count, failed_count))
        now = _现在()
        completed_at = now if status not in _发布进行中 else None
        encoded = json.dumps(details or {}, ensure_ascii=False, sort_keys=True)
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = conn.execute(
                """UPDATE releases SET status=?,stage=?,target_count=?,success_count=?,failed_count=?,
                    details_json=?,updated_at=?,completed_at=? WHERE version=?""",
                (status, stage, *counts, encoded, now, completed_at, version),
            )
            if result.rowcount != 1:
                conn.rollback()
                raise 注册表错误("发布记录不存在")
            if completed_at:
                self._审计(
                    conn, actor="release-service", action="release-finish", account_id=None,
                    result="ok" if status == "完成" else "failed",
                    details={"version": version, "status": status, "success": counts[1], "failed": counts[2]},
                )
            conn.commit()
        record = self.发布记录(version=version)
        assert record is not None
        return record

    def 发布记录(self, *, version: str | None = None) -> dict[str, Any] | None:
        with self._lock, self._连接() as conn:
            if version:
                row = conn.execute("SELECT * FROM releases WHERE version=?", (version,)).fetchone()
            else:
                row = conn.execute("SELECT * FROM releases ORDER BY created_at DESC,version DESC LIMIT 1").fetchone()
        return self._公开发布(row) if row is not None else None

    def 中断未完成发布(
        self, *, 排除部署编号: set[str] | None = None,
    ) -> int:
        排除部署编号 = {
            str(value) for value in (排除部署编号 or set()) if str(value)
        }
        now = _现在()
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            legacy = conn.execute(
                """UPDATE releases SET status='已中断',stage='管理服务重启，发布已停止',
                    updated_at=?,completed_at=?
                    WHERE status IN ('排队中','同步中','构建中','更新账号中')""",
                (now, now),
            )
            candidate_rows = conn.execute(
                "SELECT candidate_id,details_json FROM release_candidates "
                "WHERE status IN ('排队中','验收中','构建中')"
            ).fetchall()
            for row in candidate_rows:
                details = self._详情(row["details_json"], "候选详情损坏")
                timeline = details.get("timeline") if isinstance(details.get("timeline"), list) else []
                timeline.append({
                    "at": now, "updated_at": now, "status": "已中断",
                    "stage": "管理服务重启，候选制造已停止",
                })
                details["timeline"] = timeline[-100:]
                details.setdefault("failure", {
                    "phase": "release-system", "step": "候选制造",
                    "summary": "管理服务重启，候选制造已停止", "exit_code": None,
                    "failed_checks": [], "next_action": "重新生成候选。",
                    "account_effect": "未触碰任何账号；当前正式版本继续运行。",
                    "retry_class": "environment",
                })
                conn.execute(
                    """UPDATE release_candidates SET status='已中断',
                        stage='管理服务重启，候选制造已停止',details_json=?,updated_at=?,completed_at=?
                        WHERE candidate_id=?""",
                    (json.dumps(details, ensure_ascii=False, sort_keys=True), now, now, row["candidate_id"]),
                )
            deployment_rows = conn.execute(
                "SELECT deployment_id,details_json FROM release_deployments "
                "WHERE status IN ('排队中','更新账号中')"
            ).fetchall()
            deployment_rows = [
                row for row in deployment_rows
                if str(row["deployment_id"]) not in 排除部署编号
            ]
            for row in deployment_rows:
                details = self._详情(row["details_json"], "部署详情损坏")
                timeline = details.get("timeline") if isinstance(details.get("timeline"), list) else []
                timeline.append({
                    "at": now, "updated_at": now, "status": "已中断",
                    "stage": "管理服务重启，部署状态需人工核对",
                })
                details["timeline"] = timeline[-100:]
                details.setdefault("failure", {
                    "phase": "release-system", "step": "正式部署",
                    "summary": "管理服务重启，部署状态需人工核对", "exit_code": None,
                    "failed_checks": [],
                    "next_action": "先核对账号和共享平台的实际运行版本，再处理这次中断。",
                    "account_effect": "部署状态需要核对；系统已停止后续发布操作。",
                    "retry_class": "manual",
                })
                conn.execute(
                    """UPDATE release_deployments SET status='已中断',
                        stage='管理服务重启，部署状态需人工核对',details_json=?,updated_at=?,completed_at=?
                        WHERE deployment_id=?""",
                    (json.dumps(details, ensure_ascii=False, sort_keys=True), now, now, row["deployment_id"]),
                )
            conn.commit()
        return legacy.rowcount + len(candidate_rows) + len(deployment_rows)
