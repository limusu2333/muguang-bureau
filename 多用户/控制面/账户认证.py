"""Owner-managed invitations, passwords, and revocable browser sessions."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from pwdlib import PasswordHash


class 认证错误(RuntimeError):
    pass


_密码器 = PasswordHash.recommended()
_假密码摘要 = _密码器.hash(secrets.token_urlsafe(24))
_邀请种类 = {"activate", "reset"}
_用户会话秒数 = 7 * 24 * 60 * 60
_管理员会话秒数 = 8 * 60 * 60
_登录交接秒数 = 60


def _令牌(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


def _摘要(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _邮箱(value: str) -> str:
    email = str(value or "").strip().lower()
    if not email or len(email) > 320 or "@" not in email or any(ord(c) < 32 for c in email):
        raise 认证错误("邮箱无效")
    return email


def _检查密码(value: str, minimum: int = 8) -> str:
    password = str(value or "")
    if len(password) < minimum:
        raise 认证错误(f"密码至少需要 {minimum} 个字符")
    if len(password) > 128 or len(password.encode("utf-8")) > 512 or "\x00" in password:
        raise 认证错误("密码过长或包含无效字符")
    return password


def _管理员账户(value: str) -> str:
    account = str(value or "").strip()
    if not account or len(account) > 80 or any(ord(char) < 32 for char in account):
        raise 认证错误("管理员账户无效")
    return account.casefold()


def _安全比账户(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _安全验密(password: str, encoded: str) -> tuple[bool, str | None]:
    try:
        return _密码器.verify_and_update(password, encoded)
    except Exception:  # 损坏或未知的摘要也只能按登录失败处理。
        return False, None


class 账户认证:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
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
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(accounts)").fetchall()}
            if not columns:
                raise RuntimeError("账户登记簿尚未初始化")
            additions = {
                "password_hash": "TEXT",
                "activated_at": "INTEGER",
                "failed_login_count": "INTEGER NOT NULL DEFAULT 0",
                "locked_until": "INTEGER",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE accounts ADD COLUMN {name} {declaration}")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS invitations (
                    invite_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL CHECK(kind IN ('activate','reset')),
                    expires_at INTEGER NOT NULL,
                    used_at INTEGER,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id)
                );
                CREATE INDEX IF NOT EXISTS idx_invitation_account
                    ON invitations(account_id,kind,created_at DESC);
                CREATE TABLE IF NOT EXISTS user_sessions (
                    token_hash TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    csrf_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    revoked_at INTEGER,
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id)
                );
                CREATE INDEX IF NOT EXISTS idx_user_session_account
                    ON user_sessions(account_id,revoked_at,expires_at);
                CREATE TABLE IF NOT EXISTS owner_auth (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    login_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    failed_login_count INTEGER NOT NULL DEFAULT 0,
                    locked_until INTEGER,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS owner_sessions (
                    token_hash TEXT PRIMARY KEY,
                    csrf_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    revoked_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS login_handoffs (
                    token_hash TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    used_at INTEGER,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id)
                );
                CREATE TABLE IF NOT EXISTS owner_login_handoffs (
                    token_hash TEXT PRIMARY KEY,
                    expires_at INTEGER NOT NULL,
                    used_at INTEGER,
                    created_at INTEGER NOT NULL
                );
                """
            )
            owner_columns = {row["name"] for row in conn.execute("PRAGMA table_info(owner_auth)").fetchall()}
            if "login_name" not in owner_columns:
                conn.execute("ALTER TABLE owner_auth ADD COLUMN login_name TEXT NOT NULL DEFAULT '示例主人'")

    @staticmethod
    def _公开账户(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "account_id": row["account_id"],
            "email": row["email"],
            "display_name": row["display_name"],
            "call_name": row["call_name"],
            "状态": row["status"],
            "upstream": row["upstream"],
        }

    @staticmethod
    def _写用户会话(conn: sqlite3.Connection, account_id: str) -> dict[str, Any]:
        now = int(time.time())
        token = _令牌("xjus_")
        csrf = _令牌("xjcs_")
        expires = now + _用户会话秒数
        conn.execute(
            "INSERT INTO user_sessions(token_hash,account_id,csrf_hash,created_at,expires_at,last_seen_at,revoked_at) "
            "VALUES(?,?,?,?,?,?,NULL)",
            (_摘要(token), account_id, _摘要(csrf), now, expires, now),
        )
        return {"session_token": token, "csrf_token": csrf, "session_expires_at": expires}

    @staticmethod
    def _写管理员会话(conn: sqlite3.Connection) -> dict[str, Any]:
        now = int(time.time())
        token = _令牌("xjoa_")
        csrf = _令牌("xjoc_")
        expires = now + _管理员会话秒数
        conn.execute(
            "INSERT INTO owner_sessions(token_hash,csrf_hash,created_at,expires_at,last_seen_at,revoked_at) "
            "VALUES(?,?,?,?,?,NULL)",
            (_摘要(token), _摘要(csrf), now, expires, now),
        )
        return {"session_token": token, "csrf_token": csrf, "session_expires_at": expires}

    def 发邀请(self, account_id: str, kind: str = "activate", ttl_seconds: int = 24 * 60 * 60) -> dict[str, Any]:
        if kind not in _邀请种类 or not 15 * 60 <= int(ttl_seconds) <= 7 * 24 * 60 * 60:
            raise 认证错误("邀请类型或有效期无效")
        token = _令牌("xjiv_")
        now = int(time.time())
        expires = now + int(ttl_seconds)
        invite_id = "inv_" + secrets.token_hex(16)
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
            if row is None or row["status"] == "已删":
                conn.rollback()
                raise 认证错误("账户不存在")
            if kind == "activate" and (row["status"] != "待绑定" or row["password_hash"]):
                conn.rollback()
                raise 认证错误("该账户已经激活")
            if kind == "reset" and row["status"] == "待绑定":
                conn.rollback()
                raise 认证错误("待激活账户应重新生成邀请")
            conn.execute(
                "UPDATE invitations SET used_at=? WHERE account_id=? AND kind=? AND used_at IS NULL",
                (now, account_id, kind),
            )
            if kind == "reset":
                conn.execute(
                    "UPDATE accounts SET password_hash=NULL,failed_login_count=0,locked_until=NULL WHERE account_id=?",
                    (account_id,),
                )
                conn.execute(
                    "UPDATE user_sessions SET revoked_at=? WHERE account_id=? AND revoked_at IS NULL",
                    (now, account_id),
                )
            conn.execute(
                "INSERT INTO invitations(invite_id,account_id,token_hash,kind,expires_at,used_at,created_at) "
                "VALUES(?,?,?,?,?,NULL,?)",
                (invite_id, account_id, _摘要(token), kind, expires, now),
            )
            conn.commit()
        return {"token": token, "kind": kind, "expires_at": expires, "account_id": account_id}

    def 看邀请(self, token: str) -> dict[str, Any]:
        now = int(time.time())
        with self._lock, self._连接() as conn:
            row = conn.execute(
                "SELECT i.*,a.email,a.display_name,a.call_name,a.status FROM invitations i "
                "JOIN accounts a ON a.account_id=i.account_id WHERE i.token_hash=?",
                (_摘要(str(token or "")),),
            ).fetchone()
        if row is None or row["used_at"] is not None or row["expires_at"] <= now or row["status"] == "已删":
            raise 认证错误("邀请链接无效或已经过期")
        return {
            "kind": row["kind"], "expires_at": row["expires_at"], "email": row["email"],
            "display_name": row["display_name"], "call_name": row["call_name"],
        }

    def 使用邀请(self, token: str, password: str) -> dict[str, Any]:
        encoded = _密码器.hash(_检查密码(password))
        now = int(time.time())
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT i.*,a.status FROM invitations i JOIN accounts a ON a.account_id=i.account_id "
                "WHERE i.token_hash=?",
                (_摘要(str(token or "")),),
            ).fetchone()
            if row is None or row["used_at"] is not None or row["expires_at"] <= now:
                conn.rollback()
                raise 认证错误("邀请链接无效或已经过期")
            if row["kind"] == "activate" and row["status"] != "待绑定":
                conn.rollback()
                raise 认证错误("账户已经激活或不能激活")
            if row["kind"] == "reset" and row["status"] not in {"在用", "停用"}:
                conn.rollback()
                raise 认证错误("账户当前不能重置密码")
            target_status = "在用" if row["kind"] == "activate" else row["status"]
            conn.execute(
                "UPDATE accounts SET password_hash=?,activated_at=COALESCE(activated_at,?),"
                "failed_login_count=0,locked_until=NULL,status=? WHERE account_id=?",
                (encoded, now, target_status, row["account_id"]),
            )
            conn.execute("UPDATE invitations SET used_at=? WHERE invite_id=?", (now, row["invite_id"]))
            conn.execute(
                "UPDATE user_sessions SET revoked_at=? WHERE account_id=? AND revoked_at IS NULL",
                (now, row["account_id"]),
            )
            account = conn.execute("SELECT * FROM accounts WHERE account_id=?", (row["account_id"],)).fetchone()
            result = self._公开账户(account)
            if target_status == "在用":
                result.update(self._写用户会话(conn, row["account_id"]))
            conn.commit()
        return result

    def 登录(self, email: str, password: str) -> dict[str, Any]:
        normalized = _邮箱(email)
        supplied = str(password or "")
        now = int(time.time())
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM accounts WHERE email=?", (normalized,)).fetchone()
            if row is None:
                _安全验密(supplied, _假密码摘要)
                conn.rollback()
                raise 认证错误("账号或密码错误，或者账户未启用")
            if row["locked_until"] and int(row["locked_until"]) > now:
                conn.rollback()
                raise 认证错误("登录尝试过多，请稍后再试")
            encoded = str(row["password_hash"] or _假密码摘要)
            valid, updated = _安全验密(supplied, encoded)
            if not valid or not row["password_hash"]:
                failures = int(row["failed_login_count"] or 0) + 1
                locked_until = now + 10 * 60 if failures >= 5 else None
                conn.execute(
                    "UPDATE accounts SET failed_login_count=?,locked_until=? WHERE account_id=?",
                    (0 if locked_until else failures, locked_until, row["account_id"]),
                )
                conn.commit()
                raise 认证错误("账号或密码错误，或者账户未启用")
            if row["status"] != "在用":
                conn.rollback()
                raise 认证错误("账号或密码错误，或者账户未启用")
            conn.execute(
                "UPDATE accounts SET password_hash=?,failed_login_count=0,locked_until=NULL WHERE account_id=?",
                (updated or row["password_hash"], row["account_id"]),
            )
            result = self._公开账户(row)
            result.update(self._写用户会话(conn, row["account_id"]))
            conn.commit()
        return result

    def 创建用户登录交接(self, email: str, password: str) -> dict[str, Any]:
        result = self.登录(email, password)
        self.退出(str(result["session_token"]), str(result["csrf_token"]))
        token = _令牌("xjuh_")
        now = int(time.time())
        expires = now + _登录交接秒数
        with self._lock, self._连接() as conn:
            conn.execute("DELETE FROM login_handoffs WHERE expires_at<=? OR used_at IS NOT NULL", (now,))
            conn.execute(
                "INSERT INTO login_handoffs(token_hash,account_id,expires_at,used_at,created_at) "
                "VALUES(?,?,?,NULL,?)",
                (_摘要(token), result["account_id"], expires, now),
            )
        return {"handoff_token": token, "expires_at": expires, "account_id": result["account_id"]}

    def 使用用户登录交接(self, token: str) -> dict[str, Any]:
        raw = str(token or "")
        if not 32 <= len(raw) <= 256:
            raise 认证错误("登录跳转已经失效")
        now = int(time.time())
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT h.*,a.* FROM login_handoffs h JOIN accounts a ON a.account_id=h.account_id "
                "WHERE h.token_hash=?",
                (_摘要(raw),),
            ).fetchone()
            if row is None or row["used_at"] is not None or row["expires_at"] <= now or row["status"] != "在用":
                conn.rollback()
                raise 认证错误("登录跳转已经失效")
            conn.execute("UPDATE login_handoffs SET used_at=? WHERE token_hash=?", (now, _摘要(raw)))
            result = self._公开账户(row)
            result.update(self._写用户会话(conn, row["account_id"]))
            conn.commit()
        return result

    def 解析会话(self, token: str) -> dict[str, Any]:
        raw = str(token or "")
        if not 32 <= len(raw) <= 256:
            raise 认证错误("登录已经失效")
        now = int(time.time())
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT s.expires_at,s.last_seen_at,s.revoked_at,a.* FROM user_sessions s "
                "JOIN accounts a ON a.account_id=s.account_id WHERE s.token_hash=?",
                (_摘要(raw),),
            ).fetchone()
            if row is None or row["revoked_at"] is not None or row["expires_at"] <= now or row["status"] != "在用":
                conn.rollback()
                raise 认证错误("登录已经失效")
            if now - int(row["last_seen_at"]) >= 60:
                conn.execute("UPDATE user_sessions SET last_seen_at=? WHERE token_hash=?", (now, _摘要(raw)))
            result = self._公开账户(row)
            result["session_expires_at"] = row["expires_at"]
            conn.commit()
        return result

    def 校验防伪(self, token: str, csrf: str) -> dict[str, Any]:
        account = self.解析会话(token)
        with self._lock, self._连接() as conn:
            row = conn.execute("SELECT csrf_hash FROM user_sessions WHERE token_hash=?", (_摘要(token),)).fetchone()
        if row is None or not hmac.compare_digest(row["csrf_hash"], _摘要(str(csrf or ""))):
            raise 认证错误("本次操作验证失败，请刷新页面后重试")
        return account

    def 退出(self, token: str, csrf: str) -> None:
        self.校验防伪(token, csrf)
        with self._lock, self._连接() as conn:
            conn.execute(
                "UPDATE user_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                (int(time.time()), _摘要(token)),
            )

    def 撤销用户会话(self, account_id: str) -> None:
        with self._lock, self._连接() as conn:
            conn.execute(
                "UPDATE user_sessions SET revoked_at=? WHERE account_id=? AND revoked_at IS NULL",
                (int(time.time()), account_id),
            )

    def 登录状态(self, account_id: str) -> dict[str, Any]:
        now = int(time.time())
        with self._lock, self._连接() as conn:
            row = conn.execute(
                "SELECT password_hash,activated_at FROM accounts WHERE account_id=?", (account_id,)
            ).fetchone()
            active_sessions = conn.execute(
                "SELECT COUNT(*) AS n FROM user_sessions WHERE account_id=? AND revoked_at IS NULL AND expires_at>?",
                (account_id, now),
            ).fetchone()["n"] if row is not None else 0
        if row is None:
            raise 认证错误("账户不存在")
        return {
            "已设置密码": bool(row["password_hash"]),
            "已激活": row["activated_at"] is not None,
            "登录设备数": active_sessions,
        }

    def 管理员是否已设置(self) -> bool:
        with self._lock, self._连接() as conn:
            return conn.execute("SELECT 1 FROM owner_auth WHERE singleton=1").fetchone() is not None

    def 管理员账户匹配(self, login_name: str) -> bool:
        try:
            supplied = _管理员账户(login_name)
        except 认证错误:
            return False
        with self._lock, self._连接() as conn:
            row = conn.execute("SELECT login_name FROM owner_auth WHERE singleton=1").fetchone()
        return row is not None and _安全比账户(supplied, str(row["login_name"]).casefold())

    def 设置管理员账户(self, login_name: str, password: str) -> None:
        account = _管理员账户(login_name)
        encoded = _密码器.hash(_检查密码(password, 8))
        now = int(time.time())
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM owner_auth WHERE singleton=1").fetchone() is not None:
                conn.rollback()
                raise 认证错误("管理员密码已经设置")
            conn.execute(
                "INSERT INTO owner_auth(singleton,login_name,password_hash,created_at,updated_at) VALUES(1,?,?,?,?)",
                (account, encoded, now, now),
            )
            conn.commit()

    def 管理员登录(self, login_name: str, password: str) -> dict[str, Any]:
        supplied_account = _管理员账户(login_name)
        supplied = str(password or "")
        now = int(time.time())
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM owner_auth WHERE singleton=1").fetchone()
            if row is None:
                _安全验密(supplied, _假密码摘要)
                conn.rollback()
                raise 认证错误("管理员尚未初始化")
            if row["locked_until"] and int(row["locked_until"]) > now:
                conn.rollback()
                raise 认证错误("登录尝试过多，请稍后再试")
            valid, updated = _安全验密(supplied, row["password_hash"])
            valid = valid and _安全比账户(supplied_account, str(row["login_name"]).casefold())
            if not valid:
                failures = int(row["failed_login_count"] or 0) + 1
                locked_until = now + 10 * 60 if failures >= 5 else None
                conn.execute(
                    "UPDATE owner_auth SET failed_login_count=?,locked_until=?,updated_at=? WHERE singleton=1",
                    (0 if locked_until else failures, locked_until, now),
                )
                conn.commit()
                raise 认证错误("管理员账户或密码错误")
            conn.execute(
                "UPDATE owner_auth SET password_hash=?,failed_login_count=0,locked_until=NULL,updated_at=? WHERE singleton=1",
                (updated or row["password_hash"], now),
            )
            result = self._写管理员会话(conn)
            conn.commit()
        return result

    def 创建管理员登录交接(self, login_name: str, password: str) -> dict[str, Any]:
        session = self.管理员登录(login_name, password)
        self.管理员退出(str(session["session_token"]), str(session["csrf_token"]))
        token = _令牌("xjoh_")
        now = int(time.time())
        expires = now + _登录交接秒数
        with self._lock, self._连接() as conn:
            conn.execute("DELETE FROM owner_login_handoffs WHERE expires_at<=? OR used_at IS NOT NULL", (now,))
            conn.execute(
                "INSERT INTO owner_login_handoffs(token_hash,expires_at,used_at,created_at) VALUES(?,?,NULL,?)",
                (_摘要(token), expires, now),
            )
        return {"handoff_token": token, "expires_at": expires}

    def 使用管理员登录交接(self, token: str) -> dict[str, Any]:
        raw = str(token or "")
        if not 32 <= len(raw) <= 256:
            raise 认证错误("管理员登录跳转已经失效")
        now = int(time.time())
        with self._lock, self._连接() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM owner_login_handoffs WHERE token_hash=?",
                (_摘要(raw),),
            ).fetchone()
            if row is None or row["used_at"] is not None or row["expires_at"] <= now:
                conn.rollback()
                raise 认证错误("管理员登录跳转已经失效")
            conn.execute("UPDATE owner_login_handoffs SET used_at=? WHERE token_hash=?", (now, _摘要(raw)))
            result = self._写管理员会话(conn)
            conn.commit()
        return result

    def 校验管理员(self, token: str, csrf: str | None = None) -> None:
        raw = str(token or "")
        if not 32 <= len(raw) <= 256:
            raise 认证错误("管理员登录已经失效")
        now = int(time.time())
        with self._lock, self._连接() as conn:
            row = conn.execute("SELECT * FROM owner_sessions WHERE token_hash=?", (_摘要(raw),)).fetchone()
            if row is None or row["revoked_at"] is not None or row["expires_at"] <= now:
                raise 认证错误("管理员登录已经失效")
            if csrf is not None and not hmac.compare_digest(row["csrf_hash"], _摘要(str(csrf or ""))):
                raise 认证错误("本次操作验证失败，请刷新页面后重试")
            if now - int(row["last_seen_at"]) >= 60:
                conn.execute("UPDATE owner_sessions SET last_seen_at=? WHERE token_hash=?", (now, _摘要(raw)))

    def 管理员退出(self, token: str, csrf: str) -> None:
        self.校验管理员(token, csrf)
        with self._lock, self._连接() as conn:
            conn.execute(
                "UPDATE owner_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                (int(time.time()), _摘要(token)),
            )
