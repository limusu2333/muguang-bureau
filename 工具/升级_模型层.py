#!/usr/bin/env python3
"""整合升级 · 第1步 模型层:花名册五岗 → AgentScope ChatModel(替掉手写 urllib)。

- DeepSeek 两岗(测试/文案)用专用 DeepSeekChatModel(带 DeepSeek formatter,避并发 tool_result 格式坑 #1892)。
- 其余(GPT 中转站 / qwen / glm)端点均 OpenAI 兼容 → OpenAIChatModel + base_url。
  (AgentScope 无智谱专用 model；GPT 中转站与百炼兼容端点统一走 OpenAIChatModel。)
- 预算:三家 Parameters 均支持 max_tokens;统一 16384,防推理模型 thinking 吃光输出(自传教训;thinking 不关)。
- 花名册.yaml = 唯一真相,改它即调岗,本层零改。

自检:`cd 工具 && python3 升级_模型层.py` 五岗各真调一句,看服务端模型与回复。
"""
from __future__ import annotations

import os

from 模型接入 import 花名册, 在岗, _加载_env  # 花名册与密钥校验的唯一入口
from 根 import 是远程实例

from agentscope.agent import Agent, ReActConfig
from agentscope.credential import DeepSeekCredential, OpenAICredential
from agentscope.model import DeepSeekChatModel, OpenAIChatModel

默认最大tokens = 16384
def 全部岗位() -> list:
    """公司当前所有岗位；花名册读坏或为空时停止，不恢复固定旧班子。"""
    from 模型接入 import 花名册

    岗位们 = list(花名册().keys())
    if not 岗位们:
        raise RuntimeError("花名册为空，拒绝使用固定旧岗位")
    return 岗位们


def _配置(岗位: str) -> tuple[dict, str]:
    _加载_env()
    cfg = dict(在岗(岗位))
    key = str(cfg.get("key", ""))
    return cfg, key


def _模型家族(model: str) -> str:
    """按服务端模型名识别家族。密钥名只表示从哪个环境变量取值，不代表模型品牌。"""
    model_id = model.lower()
    if model_id.startswith("deepseek-"):
        return "deepseek"
    if model_id.startswith("qwen"):
        return "qwen"
    if model_id.startswith("glm-"):
        return "glm"
    return "openai_compatible"


def 建模型(岗位: str, *, stream: bool = False, 最大tokens: int = 默认最大tokens, 思考: bool = True):
    """按花名册造该岗位的官方 ChatModel。DeepSeek 专用,其余 OpenAI 兼容端点。
    思考=True 时 DeepSeek 开思维模式（reasoning_content 外露——思维透明是纲）；
    机械活（记忆抽取/摘要/巩固）传 思考=False 省时省钱。"""
    cfg, key = _配置(岗位)
    model = cfg["model"]
    base = str(cfg.get("base_url", "")).rstrip("/") or None
    family = _模型家族(model)
    if not 是远程实例() and family == "deepseek":
        # 本机 DeepSeek 直连走专用 formatter；远程统一经 model-proxy 的 OpenAI 兼容协议。
        # 不设则吃 DeepSeekChatModel.__init__ 保守默认 65536(=真容量1.5%),触发线低到5.2万、
        # 一累积就误触发框架压缩(SummarySchema maxLength=300)→jsonschema失败→"发言失败"。
        # 设真值后触发线抬到80万,一场会够不着、永不压缩,且不超真实窗口、不撞真实API。
        return DeepSeekChatModel(
            credential=DeepSeekCredential(api_key=key, base_url=base or "https://api.deepseek.com"),
            model=model,
            parameters=DeepSeekChatModel.Parameters(max_tokens=最大tokens, thinking_enable=思考),
            stream=stream,
            context_size=1_000_000,
        )
    # 思维开关各家各的方言（AgentScope的OpenAI兼容层认reasoning_content，开关得我们自己递）：
    # qwen(百炼compatible-mode)=enable_thinking；glm(智谱)=thinking.type；
    # OpenAI 兼容代理会转发 reasoning_content；各家思考开关仍需按协议传递。
    extra = None
    thinking_style = str(cfg.get("_thinking_style") or family)
    if thinking_style == "qwen":
        extra = {"enable_thinking": bool(思考)}
    elif thinking_style == "glm":
        extra = {"thinking": {"type": "enabled" if 思考 else "disabled"}}
    return OpenAIChatModel(
        credential=OpenAICredential(api_key=key, base_url=base),
        model=model,
        parameters=OpenAIChatModel.Parameters(max_tokens=最大tokens),
        stream=stream,
        extra_body=extra,
    )


def 建Agent(
    岗位: str,
    系统提示: str,
    *,
    toolkit=None,
    最大tokens: int = 默认最大tokens,
    预算步数: int = 20,
    名字: str | None = None,
    state=None,
    stream: bool = False,
    思考: bool = True,
) -> "Agent":
    """造岗位 Agent(官方 ReAct;预算=ReActConfig.max_iters;formatter 在各厂 model 上)。

    state=AgentState(permission_context=...) 时,Agent 内部 PermissionEngine 按白名单给工具把门:
    白名单内 ALLOW 自动执行、白名单外 ASK 停下转请示。不传 state=默认每步要确认(适合纯发言)。
    """
    kw = {}
    if state is not None:
        kw["state"] = state
    return Agent(
        name=名字 or 岗位,
        system_prompt=系统提示,
        model=建模型(岗位, 最大tokens=最大tokens, stream=stream, 思考=思考),
        toolkit=toolkit,
        react_config=ReActConfig(max_iters=预算步数),
        **kw,
    )


if __name__ == "__main__":
    import asyncio

    from agentscope.message import Msg, TextBlock

    def _文本(msg) -> str:
        c = getattr(msg, "content", "")
        if isinstance(c, str):
            return c.strip()
        out = []
        for b in c or []:
            t = b.get("text") if isinstance(b, dict) else getattr(b, "text", None)
            if t:
                out.append(str(t))
        return "".join(out).strip()

    async def _试(岗位: str) -> None:
        a = 建Agent(岗位, f"你是开发公司{岗位}。用一句话简短回答。", 名字=岗位)
        r = await a.reply(
            Msg(name="自检", content=[TextBlock(type="text", text="用一句话说你能不能正常工作。")], role="user")
        )
        print(f"  ✓ {岗位}（{花名册()[岗位]['model']}）：{_文本(r)[:160]}")

    async def main() -> None:
        print("第1步模型层自检（五岗各真调一句）：")
        for 岗位 in 全部岗位():
            try:
                await _试(岗位)
            except Exception as e:  # noqa: BLE001
                print(f"  ❌ {岗位}：{type(e).__name__}: {e}")

    asyncio.run(main())
