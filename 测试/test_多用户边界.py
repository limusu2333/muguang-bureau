from __future__ import annotations

import asyncio
import base64
import io
import json
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from starlette.requests import Request

from 多用户.控制面.执行器 import _输出上限, _限量读, 一次性执行器, 执行拒绝, 验证命令
from 多用户.控制面.服务 import 控制令牌有效
from 多用户.控制面.本地精排服务 import _令牌有效
from 多用户.搜索网关.应用 import _成本微元, _验证_embedding, _验证_rerank
from 多用户.网关.应用 import _上游, _受限请求体
from 多用户.网关.认证 import Access校验器


def _request(chunks: list[bytes], content_length: str | None = None) -> Request:
    messages = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ] or [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    headers = [] if content_length is None else [(b"content-length", content_length.encode("ascii"))]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers}, receive)


def _b64uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class 网关边界测试(unittest.TestCase):
    def test_监督服务控制口必须带内部令牌(self):
        token = "x" * 48
        with patch.dict("os.environ", {"XJ_SUPERVISOR_TOKEN": token}):
            self.assertTrue(控制令牌有效("Bearer " + token))
            self.assertFalse(控制令牌有效("Bearer " + "y" * 48))
            self.assertFalse(控制令牌有效(""))

    def test_Access_JWT严格验签且kid缺失时只刷新一次(self):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = private.public_key().public_numbers()
        jwk = {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": "kid-1", "n": _b64uint(numbers.n), "e": _b64uint(numbers.e)}
        calls = []

        async def provider(refresh):
            calls.append(refresh)
            return {"keys": [jwk]} if refresh else {"keys": [{**jwk, "kid": "old-kid"}]}

        now = int(time.time())
        claims = {
            "iss": "https://team.cloudflareaccess.com", "aud": "aud-1", "exp": now + 300,
            "nbf": now - 1, "sub": "cf-sub", "email": "USER@example.com", "type": "app",
        }
        token = jwt.encode(claims, private, algorithm="RS256", headers={"kid": "kid-1"})
        with patch.dict("os.environ", {"CF_ACCESS_ISSUER": claims["iss"], "CF_ACCESS_AUD": "aud-1"}):
            identity = asyncio.run(Access校验器(provider).校验(token))
        self.assertEqual((identity.sub, identity.email), ("cf-sub", "user@example.com"))
        self.assertEqual(calls, [False, True])

    def test_Access_JWT错误aud不能通过(self):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = private.public_key().public_numbers()
        jwk = {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": "kid-1", "n": _b64uint(numbers.n), "e": _b64uint(numbers.e)}

        async def provider(_refresh):
            return {"keys": [jwk]}

        now = int(time.time())
        token = jwt.encode(
            {"iss": "https://team.cloudflareaccess.com", "aud": "wrong", "exp": now + 300, "nbf": now - 1, "sub": "s", "email": "u@example.com", "type": "app"},
            private,
            algorithm="RS256",
            headers={"kid": "kid-1"},
        )
        with patch.dict("os.environ", {"CF_ACCESS_ISSUER": "https://team.cloudflareaccess.com", "CF_ACCESS_AUD": "aud-1"}):
            with self.assertRaises(jwt.InvalidAudienceError):
                asyncio.run(Access校验器(provider).校验(token))

    def test_公开网关边读边限制请求体(self):
        self.assertEqual(asyncio.run(_受限请求体(_request([b"ab", b"cd"]), 4)), b"abcd")
        with self.assertRaisesRegex(HTTPException, "请求体过大"):
            asyncio.run(_受限请求体(_request([b"abc", b"de"]), 4))
        with self.assertRaisesRegex(HTTPException, "请求体过大"):
            asyncio.run(_受限请求体(_request([], "999"), 4))

    def test_注册表上游不能改为其他主机(self):
        account_id = "acc_" + "0" * 26
        self.assertEqual(_上游({"account_id": account_id, "upstream": f"http://company-{account_id}:8000"})[0], account_id)
        with self.assertRaisesRegex(HTTPException, "不符合固定实例路由"):
            _上游({"account_id": account_id, "upstream": "http://127.0.0.1:8000"})


class 搜索与执行边界测试(unittest.TestCase):
    def test_本地精排服务只接受独立内部令牌(self):
        token = "xjrt_" + "x" * 48
        with patch.dict("os.environ", {"XJ_RERANK_TOKEN": token}):
            self.assertTrue(_令牌有效("Bearer " + token))
            self.assertFalse(_令牌有效("Bearer " + "y" * len(token)))
            self.assertFalse(_令牌有效("Bearer " + "x" * len(token)))

    def test_多用户精排协议只作为本地网关兼容层(self):
        source = (Path(__file__).resolve().parents[1] / "工具" / "统一搜索.py").read_text(encoding="utf-8")
        self.assertIn("class 本地精排网关", source)
        self.assertIn("mlx-community/Qwen3-Reranker-4B-mxfp8@25f203a237b8", source)
        self.assertNotIn("TextReRank.call", source)

    def test_搜索网关只接受锁定模型与参数(self):
        embedding = {
            "model": "qwen3.7-text-embedding", "input": {"texts": ["查询"]},
            "parameters": {"text_type": "query", "dimension": 1024, "output_type": "dense&sparse"},
        }
        clean, estimated = _验证_embedding(embedding)
        self.assertIs(clean, embedding)
        self.assertEqual(estimated, len("查询".encode("utf-8")))
        with self.assertRaisesRegex(HTTPException, "不允许的参数"):
            _验证_embedding({**embedding, "url": "http://example.com"})

        rerank = {
            "model": "qwen3-rerank", "input": {"query": "q", "documents": ["a", "b"]},
            "parameters": {"top_n": 1, "return_documents": False},
        }
        self.assertEqual(_验证_rerank(rerank)[1], 3)
        rerank["input"]["documents"] = ["a"] * 17
        rerank["parameters"]["top_n"] = 17
        with self.assertRaises(HTTPException):
            _验证_rerank(rerank)
        rerank["input"]["documents"] = ["a", "b"]
        rerank["parameters"]["top_n"] = True
        with self.assertRaisesRegex(HTTPException, "top_n 无效"):
            _验证_rerank(rerank)

    def test_搜索成本统一换算为美元微元(self):
        self.assertEqual(_成本微元(123, Decimal("0.5")), 62)

    def test_执行室只放行验收命令(self):
        self.assertEqual(验证命令("python3 -m py_compile 工具/根.py")[1], "python编译")
        with self.assertRaises(执行拒绝):
            验证命令("python3 -c 'print(1)'")
        with self.assertRaises(执行拒绝):
            验证命令("sh -c 'cat /etc/passwd'")

    def test_执行室无限输出在内存中只保留1MB(self):
        output = bytearray()
        _限量读(io.BytesIO(b"x" * (_输出上限 * 2)), output)
        self.assertEqual(len(output), _输出上限 + 1)

    def test_执行室代码验收只挂载当前账户制度(self):
        account_id = "acc_" + "0" * 26
        with tempfile.TemporaryDirectory() as root:
            policy = Path(root) / account_id
            policy.mkdir()
            process = unittest.mock.MagicMock()
            process.stdout = io.BytesIO(b"")
            process.stderr = io.BytesIO(b"")
            process.wait.return_value = 0
            with patch.dict("os.environ", {"XJ_POLICY_DIR": root}), patch(
                "多用户.控制面.执行器.subprocess.Popen", return_value=process,
            ) as popen, patch("多用户.控制面.执行器.subprocess.run"):
                一次性执行器().执行(
                    {"account_id": account_id, "runner_image_ref": "xj-runner:test"},
                    {"command": "python3 -m py_compile 工具/根.py", "root": "code"},
                )
            command = popen.call_args.args[0]
            mount = f"type=bind,source={policy.resolve()},target=/policy,readonly"
            self.assertIn(mount, command)
            self.assertTrue(any("cp -R /policy/. /runner/" in part for part in command))


if __name__ == "__main__":
    unittest.main(verbosity=2)
