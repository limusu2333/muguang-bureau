"""Cloudflare Access JWT strict validation without direct Internet access."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import jwt


@dataclass(frozen=True)
class 身份:
    sub: str
    email: str
    exp: int
    claims: dict[str, Any]


class Access校验器:
    def __init__(self, 取公钥集: Callable[[bool], Awaitable[dict[str, Any]]]) -> None:
        self.issuer = os.environ["CF_ACCESS_ISSUER"].rstrip("/")
        self.audience = os.environ["CF_ACCESS_AUD"]
        self._取公钥集 = 取公钥集

    @staticmethod
    def _找公钥(document: dict[str, Any], kid: str) -> Any | None:
        keys = document.get("keys")
        if not isinstance(keys, list) or not 1 <= len(keys) <= 32:
            raise jwt.InvalidTokenError("JWKS 公钥集无效")
        matches: list[dict[str, Any]] = []
        for item in keys:
            if not isinstance(item, dict) or item.get("kid") != kid:
                continue
            if item.get("kty") != "RSA" or item.get("use", "sig") != "sig":
                continue
            if item.get("alg", "RS256") != "RS256":
                continue
            matches.append(item)
        if len(matches) > 1:
            raise jwt.InvalidTokenError("JWKS kid 重复")
        if not matches:
            return None
        try:
            return jwt.PyJWK.from_dict(matches[0], algorithm="RS256").key
        except (KeyError, ValueError, TypeError) as exc:
            raise jwt.InvalidTokenError("JWKS 公钥无效") from exc

    async def 校验(self, token: str) -> 身份:
        if not token or len(token) > 16_384:
            raise jwt.InvalidTokenError("Access JWT 缺失或过长")
        header = jwt.get_unverified_header(token)
        kid = str(header.get("kid") or "")
        if header.get("alg") != "RS256" or not kid or len(kid) > 256:
            raise jwt.InvalidTokenError("Access JWT 算法或 kid 无效")

        key = self._找公钥(await self._取公钥集(False), kid)
        if key is None:
            key = self._找公钥(await self._取公钥集(True), kid)
        if key is None:
            raise jwt.InvalidTokenError("Access JWT kid 不在当前公钥集中")

        claims = jwt.decode(
            token,
            key=key,
            audience=self.audience,
            issuer=self.issuer,
            algorithms=["RS256"],
            leeway=30,
            options={"require": ["exp", "nbf", "iss", "aud", "sub", "type"]},
        )
        if claims.get("type") != "app":
            raise jwt.InvalidTokenError("只接受 Cloudflare application token")
        sub = str(claims.get("sub") or "").strip()
        email = str(claims.get("email") or "").strip().lower()
        if not sub or len(sub) > 512 or not email or len(email) > 320 or "@" not in email:
            raise jwt.InvalidTokenError("Access JWT 缺少有效的 sub 或 email")
        return 身份(sub=sub, email=email, exp=int(claims["exp"]), claims=dict(claims))
