"""PostgreSQL-backed search tokens, rate limits, budgets, and usage."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from secrets import token_urlsafe
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row


_账户格式 = re.compile(r"acc_[0-9A-HJKMNP-TV-Z]{26}", re.IGNORECASE)


class 账本拒绝(RuntimeError):
    pass


def _哈希(token: str) -> str:
    raw = str(token or "")
    if not 32 <= len(raw) <= 512 or any(ord(c) < 33 or ord(c) > 126 for c in raw):
        raise 账本拒绝("搜索实例令牌无效")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _金额微元(value: object, *, allow_none: bool = True) -> int | None:
    if value is None and allow_none:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise 账本拒绝("预算金额无效") from exc
    if not amount.is_finite() or amount < 0:
        raise 账本拒绝("预算金额无效")
    micros = int((amount * Decimal(1_000_000)).to_integral_value(rounding=ROUND_CEILING))
    if micros > 10**15:
        raise 账本拒绝("预算金额过大")
    return micros


class 搜索账本:
    def __init__(self, database_url: str, timezone_name: str = "Asia/Shanghai") -> None:
        if not database_url:
            raise RuntimeError("缺少 XJ_SEARCH_DATABASE_URL")
        self.database_url = database_url
        self.timezone = ZoneInfo(timezone_name)
        self._初始化()

    def _连接(self):
        return psycopg.connect(self.database_url, connect_timeout=5, row_factory=dict_row)

    def _初始化(self) -> None:
        with self._连接() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS search_tokens (
                    account_id TEXT PRIMARY KEY,
                    token_hash CHAR(64) NOT NULL UNIQUE,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    daily_limit_micros BIGINT,
                    monthly_limit_micros BIGINT,
                    rpm_limit INTEGER NOT NULL CHECK(rpm_limit BETWEEN 1 AND 600),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS search_usage (
                    event_id UUID PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES search_tokens(account_id),
                    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    request_kind TEXT NOT NULL CHECK(request_kind IN ('embedding','rerank')),
                    estimated_tokens INTEGER NOT NULL CHECK(estimated_tokens > 0),
                    estimated_cost_micros BIGINT NOT NULL CHECK(estimated_cost_micros >= 0),
                    status TEXT NOT NULL CHECK(status IN ('reserved','ok','upstream_error')),
                    upstream_status INTEGER
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_search_usage_account_time ON search_usage(account_id, requested_at)")

    def 创建令牌(
        self,
        account_id: str,
        *,
        daily_limit: object,
        monthly_limit: object,
        rpm_limit: int,
        token: str | None = None,
    ) -> str:
        if not _账户格式.fullmatch(str(account_id or "")):
            raise 账本拒绝("账户标识无效")
        rpm = int(rpm_limit)
        if not 1 <= rpm <= 600:
            raise 账本拒绝("每分钟请求上限必须在 1 到 600 之间")
        daily = _金额微元(daily_limit)
        monthly = _金额微元(monthly_limit)
        if daily is None and monthly is None:
            raise 账本拒绝("每日和每月搜索预算至少设置一个")
        if daily is not None and monthly is not None and daily > monthly:
            raise 账本拒绝("每日搜索预算不能高于每月预算")
        token = str(token) if token is not None else "xjs_" + token_urlsafe(36)
        if not token.startswith("xjs_"):
            raise 账本拒绝("搜索实例令牌格式无效")
        digest = _哈希(token)
        try:
            with self._连接() as conn:
                conn.execute(
                    """INSERT INTO search_tokens(account_id,token_hash,active,daily_limit_micros,monthly_limit_micros,rpm_limit)
                       VALUES(%s,%s,TRUE,%s,%s,%s)
                       ON CONFLICT(account_id) DO UPDATE SET
                         token_hash=excluded.token_hash,active=TRUE,
                         daily_limit_micros=excluded.daily_limit_micros,
                         monthly_limit_micros=excluded.monthly_limit_micros,
                         rpm_limit=excluded.rpm_limit,updated_at=now()""",
                    (account_id, digest, daily, monthly, rpm),
                )
        except psycopg.Error as exc:
            raise RuntimeError("搜索账本数据库不可用") from exc
        return token

    def 撤销(self, account_id: str) -> None:
        try:
            with self._连接() as conn:
                result = conn.execute(
                    "UPDATE search_tokens SET active=FALSE,updated_at=now() WHERE account_id=%s", (account_id,)
                )
                if result.rowcount != 1:
                    raise 账本拒绝("搜索账户不存在")
        except psycopg.Error as exc:
            raise RuntimeError("搜索账本数据库不可用") from exc

    def 置启用(self, account_id: str, active: bool) -> None:
        try:
            with self._连接() as conn:
                result = conn.execute(
                    "UPDATE search_tokens SET active=%s,updated_at=now() WHERE account_id=%s", (active, account_id)
                )
                if result.rowcount != 1:
                    raise 账本拒绝("搜索账户不存在")
        except psycopg.Error as exc:
            raise RuntimeError("搜索账本数据库不可用") from exc

    def 预留(self, token: str, *, kind: str, estimated_tokens: int, estimated_cost_micros: int) -> tuple[str, str]:
        digest = _哈希(token)
        if kind not in {"embedding", "rerank"} or not 1 <= estimated_tokens <= 2_000_000:
            raise 账本拒绝("搜索用量估算无效")
        if not 0 <= estimated_cost_micros <= 10**12:
            raise 账本拒绝("搜索成本估算无效")
        now = datetime.now(UTC)
        local_now = now.astimezone(self.timezone)
        day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        minute_start = now - timedelta(seconds=60)
        event_id = str(uuid.uuid4())
        try:
            with self._连接() as conn, conn.transaction():
                row = conn.execute("SELECT * FROM search_tokens WHERE token_hash=%s FOR UPDATE", (digest,)).fetchone()
                if row is None or not row["active"]:
                    raise 账本拒绝("搜索实例令牌无效或已撤销")
                recent = conn.execute(
                    "SELECT count(*) AS n FROM search_usage WHERE account_id=%s AND requested_at >= %s",
                    (row["account_id"], minute_start),
                ).fetchone()["n"]
                if int(recent) >= int(row["rpm_limit"]):
                    raise 账本拒绝("搜索请求频率已达到上限")
                totals = conn.execute(
                    """SELECT
                         COALESCE(sum(estimated_cost_micros) FILTER (WHERE requested_at >= %s),0) AS day_cost,
                         COALESCE(sum(estimated_cost_micros) FILTER (WHERE requested_at >= %s),0) AS month_cost
                       FROM search_usage WHERE account_id=%s""",
                    (day_start, month_start, row["account_id"]),
                ).fetchone()
                if row["daily_limit_micros"] is not None and int(totals["day_cost"]) + estimated_cost_micros > int(row["daily_limit_micros"]):
                    raise 账本拒绝("今日搜索预算已用完")
                if row["monthly_limit_micros"] is not None and int(totals["month_cost"]) + estimated_cost_micros > int(row["monthly_limit_micros"]):
                    raise 账本拒绝("本月搜索预算已用完")
                conn.execute(
                    """INSERT INTO search_usage(event_id,account_id,request_kind,estimated_tokens,estimated_cost_micros,status)
                       VALUES(%s,%s,%s,%s,%s,'reserved')""",
                    (event_id, row["account_id"], kind, estimated_tokens, estimated_cost_micros),
                )
                return event_id, str(row["account_id"])
        except psycopg.Error as exc:
            raise RuntimeError("搜索账本数据库不可用") from exc

    def 完成(self, event_id: str, *, status: str, upstream_status: int | None) -> None:
        if status not in {"ok", "upstream_error"}:
            raise ValueError("用量状态无效")
        try:
            with self._连接() as conn:
                result = conn.execute(
                    "UPDATE search_usage SET status=%s,upstream_status=%s WHERE event_id=%s AND status='reserved'",
                    (status, upstream_status, event_id),
                )
                if result.rowcount != 1:
                    raise RuntimeError("搜索用量记录缺失")
        except psycopg.Error as exc:
            raise RuntimeError("搜索账本数据库不可用") from exc
