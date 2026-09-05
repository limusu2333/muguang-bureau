"""Narrow host-side clients for LiteLLM, search-gw, and Cloudflare Access."""

from __future__ import annotations

import math
import hmac
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from .凭据 import 读取平台


class 平台错误(RuntimeError):
    pass


def _本机服务(url: str, expected_port: int) -> str:
    parsed = urlsplit(url.rstrip("/"))
    try:
        port = parsed.port
    except ValueError as exc:
        raise 平台错误("平台服务地址无效") from exc
    if (
        parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
        or port != expected_port or parsed.username is not None or parsed.password is not None
        or parsed.path not in ("", "/") or parsed.query or parsed.fragment
    ):
        raise 平台错误("平台管理接口只允许固定的宿主回环地址")
    return f"http://127.0.0.1:{expected_port}"


class _JSON客户端:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.client = httpx.Client(timeout=15, follow_redirects=False, trust_env=False)

    def 请求(
        self,
        method: str,
        path: str,
        *,
        token: str,
        body: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any]:
        try:
            response = self.client.request(
                method,
                self.base_url + path,
                json=body,
                headers={"authorization": "Bearer " + token, "content-type": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise 平台错误("平台管理接口当前不可达") from exc
        if len(response.content) > 1024 * 1024:
            raise 平台错误("平台管理接口响应过大")
        try:
            data = response.json()
        except ValueError as exc:
            raise 平台错误("平台管理接口返回了无效 JSON") from exc
        if response.status_code == 404 and allow_not_found:
            return {}
        if response.status_code >= 400:
            raise 平台错误(f"平台管理接口拒绝（HTTP {response.status_code}）")
        if not isinstance(data, dict):
            raise 平台错误("平台管理接口响应无效")
        return data


class LiteLLM客户端:
    模型 = ["gpt-5.6-sol", "qwen3.8-max", "glm-5.2", "deepseek-v4-pro-0813"]

    def __init__(self, base_url: str = "http://127.0.0.1:4000") -> None:
        self.http = _JSON客户端(_本机服务(base_url, 4000))

    def 签发(
        self,
        account_id: str,
        *,
        daily_usd: float,
        monthly_usd: float,
        rpm_limit: int,
        key: str,
    ) -> str:
        if (
            not math.isfinite(daily_usd) or not math.isfinite(monthly_usd)
            or not 0 < daily_usd <= monthly_usd or monthly_usd > 1_000_000
        ):
            raise 平台错误("聊天预算无效")
        if not 1 <= rpm_limit <= 600:
            raise 平台错误("聊天每分钟请求上限无效")
        requested_key = str(key or "")
        if not requested_key.startswith("sk-") or not 32 <= len(requested_key) <= 512:
            raise 平台错误("预生成的 LiteLLM 虚拟密钥无效")
        data = self.http.请求(
            "POST",
            "/key/generate",
            token=读取平台("litellm-master"),
            body={
                "key_alias": account_id,
                "key": requested_key,
                "models": self.模型,
                "rpm_limit": rpm_limit,
                "budget_limits": [
                    {"budget_duration": "24h", "max_budget": daily_usd},
                    {"budget_duration": "30d", "max_budget": monthly_usd},
                ],
                "metadata": {"account_id": account_id},
            },
        )
        returned = str(data.get("key") or "")
        if not returned.startswith("sk-") or len(returned) > 512:
            raise 平台错误("LiteLLM 没有返回有效虚拟密钥")
        if not hmac.compare_digest(returned, requested_key):
            raise 平台错误("LiteLLM 返回的虚拟密钥不一致")
        return returned

    def 置阻断(self, key: str, blocked: bool) -> None:
        endpoint = "/key/block" if blocked else "/key/unblock"
        self.http.请求("POST", endpoint, token=读取平台("litellm-master"), body={"key": key})

    def 撤销(self, key: str) -> None:
        self.http.请求(
            "POST", "/key/delete", token=读取平台("litellm-master"), body={"keys": [key]},
        )

    def 撤销别名(self, account_id: str) -> None:
        self.http.请求(
            "POST", "/key/delete", token=读取平台("litellm-master"),
            body={"key_aliases": [account_id]},
            allow_not_found=True,
        )


class 搜索网关客户端:
    def __init__(self, base_url: str = "http://127.0.0.1:4100") -> None:
        self.http = _JSON客户端(_本机服务(base_url, 4100))

    def 签发(
        self,
        account_id: str,
        *,
        daily_usd: float,
        monthly_usd: float,
        rpm_limit: int,
        token: str,
    ) -> str:
        data = self.http.请求(
            "POST",
            "/admin/tokens",
            token=读取平台("search-admin"),
            body={
                "account_id": account_id,
                "daily_limit": daily_usd,
                "monthly_limit": monthly_usd,
                "rpm_limit": rpm_limit,
                "token": token,
            },
        )
        returned = str(data.get("token") or "")
        if not returned.startswith("xjs_") or len(returned) > 512:
            raise 平台错误("搜索网关没有返回有效实例令牌")
        if not hmac.compare_digest(returned, token):
            raise 平台错误("搜索网关返回的实例令牌不一致")
        return returned

    def 置阻断(self, account_id: str, blocked: bool) -> None:
        action = "block" if blocked else "unblock"
        self.http.请求(
            "POST",
            f"/admin/tokens/{account_id}/{action}",
            token=读取平台("search-admin"),
            body={},
        )

    def 撤销(self, account_id: str, *, missing_ok: bool = False) -> None:
        self.http.请求(
            "DELETE", f"/admin/tokens/{account_id}", token=读取平台("search-admin"), body={},
            allow_not_found=missing_ok,
        )


class Cloudflare客户端:
    _账户 = re.compile(r"[0-9a-f]{32}", re.IGNORECASE)

    def __init__(self, account_id: str) -> None:
        if not self._账户.fullmatch(str(account_id or "")):
            raise 平台错误("Cloudflare account_id 无效")
        self.account_id = account_id
        self.client = httpx.Client(timeout=15, follow_redirects=False, trust_env=False)

    def 撤销用户会话(self, email: str) -> None:
        try:
            response = self.client.post(
                f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/access/organizations/revoke_user",
                json={"email": email},
                headers={
                    "authorization": "Bearer " + 读取平台("cloudflare-api-token"),
                    "content-type": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise 平台错误("Cloudflare 会话撤销接口当前不可达") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise 平台错误("Cloudflare 会话撤销响应无效") from exc
        if response.status_code >= 400 or not isinstance(data, dict) or data.get("success") is not True:
            raise 平台错误(f"Cloudflare 会话撤销失败（HTTP {response.status_code}）")
