#!/usr/bin/env python3
"""模型接入层：按花名册.yaml把岗位映射到各家API。

- openai兼容协议覆盖 OpenAI/DeepSeek/Qwen(DashScope兼容端点)/Kimi 等多数厂商
- anthropic 单独分支
依据: 总纲C6/C7, 手册E5.6(密钥只经环境变量)。
"""
from __future__ import annotations

import json
import os
import urllib.parse
import datetime as dt
import concurrent.futures as cf
import time
import threading
from typing import Any

from 根 import 代码根, 数据根, 本机环境文件, 是远程实例

COMPANY = 代码根
核验缓存 = 数据根 / "运行状态" / "模型核验.json"
_点名锁 = threading.Lock()


def _加载_env() -> None:
    """让点名脚本单独运行时也能读取本仓.env；不覆盖外部已设置的环境变量。"""
    env = 本机环境文件()
    if env is None or not env.exists():
        return
    for raw in env.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_加载_env()


def 花名册() -> dict:
    """用正式 YAML 解析花名册，保留列表/数字/布尔类型并校验顶层结构。"""
    import yaml
    data = yaml.safe_load((COMPANY / "花名册.yaml").read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or any(not isinstance(v, dict) for v in data.values()):
        raise ValueError("花名册顶层必须是 岗位 -> 配置对象")
    if 是远程实例():
        for role, raw in data.items():
            cfg = dict(raw)
            model_id = str(cfg.get("model") or "").lower()
            cfg.update({
                "provider": "openai",
                "base_url": "http://model-proxy:4000/v1",
                "_thinking_style": (
                    "qwen" if model_id.startswith("qwen")
                    else "glm" if model_id.startswith("glm-")
                    else ""
                ),
            })
            cfg.pop("明文HTTP授权地址", None)
            data[role] = cfg
    return data


def _缓存() -> dict[str, Any]:
    if not 核验缓存.exists():
        return {}
    from 状态存储 import 读JSON
    return 读JSON(核验缓存, 默认={}, 类型=dict) or {}


def _写缓存(data: dict[str, Any]) -> None:
    from 状态存储 import 写JSON
    写JSON(核验缓存, data)


def _核验时间(raw: Any) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(raw or "").replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return None


def 核验概览(*, 阈值天数: float = 7.0) -> dict[str, Any]:
    """当前点名缓存的健康概览：是否需要在启动时补刷。"""
    roster = 花名册()
    cache = _缓存()
    now = dt.datetime.now()
    ages: list[float] = []
    缺口: list[str] = []
    for name in roster:
        seen = cache.get(name)
        if not isinstance(seen, dict):
            缺口.append(name)
            continue
        checked = _核验时间(seen.get("checked_at"))
        if not checked:
            缺口.append(name)
            continue
        age = (now - checked).total_seconds() / 86400
        ages.append(age)
        if age > 阈值天数:
            缺口.append(name)
    最旧天数 = round(max(ages), 1) if ages else None
    return {
        "存在": bool(cache),
        "岗位数": len(roster),
        "已核验": len(ages),
        "最旧天数": 最旧天数,
        "阈值天数": 阈值天数,
        "缺口": 缺口,
        "需要刷新": bool(缺口),
    }


def _一致(登记: str, 实际: str) -> bool:
    a, b = 登记.lower(), 实际.lower()
    return bool(a and b and (a in b or b in a))


def _提取_json_obj(raw: str) -> dict[str, Any]:
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError("自检没有返回JSON对象")
    data = json.loads(raw[start:end])
    if not isinstance(data, dict):
        raise ValueError("自检JSON顶层不是对象")
    return data


def _技能作用(path: Path) -> str:
    try:
        for raw in path.read_text(encoding="utf-8").splitlines()[:12]:
            line = raw.strip()
            if line.startswith(">"):
                text = line.lstrip("> ").strip()
                for prefix in ("目的：", "目的:", "职责：", "核心使命：", "来源参考：", "来源思想:"):
                    if text.startswith(prefix):
                        text = text[len(prefix):].strip()
                        break
                return text[:90] or "见技能文件"
    except Exception:  # noqa: BLE001
        pass
    return "见技能文件"


def _技能记录(path: Path, kind: str) -> dict[str, str]:
    name = path.stem
    if name.startswith("通用_"):
        name = name.removeprefix("通用_")
    if name.endswith("_技能"):
        name = name.removesuffix("_技能")
    if name == "岗位技能":
        try:
            first = path.read_text(encoding="utf-8").splitlines()[0].strip("# ").strip()
            name = first.replace("岗位技能 · ", "") or path.parent.name
        except Exception:  # noqa: BLE001
            name = path.parent.name
    return {
        "类别": kind,
        "名称": name,
        "作用": _技能作用(path) if path.exists() else "技能文件不存在",
        "状态": "已加载✅" if path.exists() else "加载失败❌",
        "path": str(path.relative_to(COMPANY)),
    }


def _相关技能(岗位: str) -> dict[str, list[dict[str, str]]]:
    技能目录 = COMPANY / "技能"
    通用 = [_技能记录(path, "通用") for path in sorted((技能目录 / "通用").glob("*.md"))]
    岗位目录 = 技能目录 / "岗位" / 岗位
    岗位技能文件 = sorted(岗位目录.glob("*.md")) if 岗位目录.exists() else [岗位目录 / "岗位技能.md"]
    岗位技能 = [_技能记录(path, "岗位") for path in 岗位技能文件]
    return {"通用技能": 通用, "岗位技能": 岗位技能}


def _自检提示(岗位: str, cfg: dict[str, str]) -> str:
    from 机房 import 岗位上下文

    return (
        岗位上下文(岗位)
        + "\n\n---\n\n"
        + "## 开工前到岗自检\n"
        + "这不是寒暄，也不是只回复'到岗'。你要检查自己是否已经加载岗位职责、岗位技能、通用纪律和能力边界。\n"
        + "只输出一个JSON对象，不要解释，不要Markdown，不要代码块，不要泄露密钥、key_env的值或完整密钥。\n"
        + "JSON尽量紧凑，字符串用短句；缺口没有就写[\"无\"]。\n"
        + "注意：模型身份由API服务器返回的model字段另行核验。你在JSON里不要根据自我感知猜模型名，只照抄花名册模型名；这不是异常理由。\n"
        + "JSON字段必须是：\n"
        + '{'
        + '"状态":"到岗或异常",'
        + f'"岗位":"{岗位}",'
        + f'"模型":"{cfg.get("model", "未知")}",'
        + '"岗位说明书":"已加载或缺失",'
        + '"岗位技能":"已加载或缺失",'
        + '"通用技能":["已加载的通用技能名"],'
        + '"职责确认":"一句话说明你现在负责什么",'
        + '"边界确认":"一句话说明你不能做什么/什么时候要请示",'
        + '"缺口":["没有就写无"]'
        + '}\n'
        + f"硬性要求：岗位必须是「{岗位}」；模型按花名册是「{cfg.get('model', '未知')}」。"
        + "如果你没看到岗位说明书、岗位技能或职责边界，就把状态写成异常并说明缺口。"
    )


def _自检通过(岗位: str, data: dict[str, Any]) -> bool:
    status = str(data.get("状态", "")).strip()
    role = str(data.get("岗位", "")).strip()
    duty = str(data.get("职责确认", "")).strip()
    boundary = str(data.get("边界确认", "")).strip()
    manual = str(data.get("岗位说明书", "")).strip()
    skill = str(data.get("岗位技能", "")).strip()
    return (
        status == "到岗"
        and role == 岗位
        and bool(duty)
        and bool(boundary)
        and "缺失" not in manual
        and "缺失" not in skill
    )


def _调用自检(岗位: str, cfg: dict[str, str]) -> tuple[str, str, int]:
    """自检第一次给足常规预算；只有空回复或半截JSON才重试一次。"""
    prompt = _自检提示(岗位, cfg)
    last_text, last_model, last_limit = "", "", 0
    for limit in (1536, 3072):
        last_text, last_model = 调用带元(岗位, prompt, 最大tokens=limit)
        last_limit = limit
        if not last_text.strip():
            continue
        try:
            _提取_json_obj(last_text)
            return last_text, last_model, limit
        except Exception:  # noqa: BLE001
            continue
    return last_text, last_model, last_limit


def _耗时(seconds: float) -> str:
    return f"{seconds:.1f}s"


def _编号(items: list[str]) -> str:
    return "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1))


def _格式化自检(
    岗位: str,
    要求: str,
    实际: str,
    self_ok: bool,
    model_ok: bool,
    自检: dict[str, Any],
    elapsed: float,
    error: str = "",
) -> str:
    model_line = f"模型名：{实际 or '未知'}"
    if 实际 and 要求 and 实际 != 要求:
        model_line += f"（花名册：{要求}）"
    skills = _相关技能(岗位)
    common_lines = [
        f"{i}. 名称：{s['名称']}，作用：{s['作用']}（{s['状态']}）"
        for i, s in enumerate(skills["通用技能"], 1)
    ]
    role_lines = [
        f"{i}. 名称：{s['名称']}，作用：{s['作用']}（{s['状态']}）"
        for i, s in enumerate(skills["岗位技能"], 1)
    ]
    if isinstance(自检, dict):
        duty = str(自检.get("职责确认") or "").strip()
        boundary_raw = str(自检.get("边界确认") or "").strip()
    else:
        duty, boundary_raw = "", ""
    if not duty:
        duty = "自检未给出职责确认"
    boundaries = [x.strip(" ；;，,") for x in boundary_raw.replace("；", "\n").replace(";", "\n").splitlines() if x.strip()]
    if not boundaries:
        boundaries = ["自检未给出工作边界"]
    status = "到岗✅" if model_ok and self_ok else "异常⚠️"
    body = [
        f"岗位：{岗位}（{status}）",
        f"岗位职责：{duty}",
        model_line,
        "通用技能：",
        "\n".join(common_lines) if common_lines else "无",
        "岗位技能：",
        "\n".join(role_lines) if role_lines else "无",
        "工作边界：",
        _编号(boundaries),
        f"耗时：{_耗时(elapsed)}",
    ]
    if error:
        body.append(f"自检问题：{error}")
    return "\n".join(body)


def _点名单岗(岗位: str, cfg: dict[str, str]) -> tuple[str, dict[str, Any], float]:
    started = time.monotonic()
    要求 = cfg.get("model", "?")
    try:
        r, 实际, used_tokens = _调用自检(岗位, cfg)
        model_ok = _一致(要求, 实际)
        try:
            自检 = _提取_json_obj(r)
            self_ok = _自检通过(岗位, 自检)
        except Exception as parse_exc:  # noqa: BLE001
            自检 = {"raw": r.strip()[:500], "error": str(parse_exc)}
            self_ok = False
        ok = model_ok and self_ok
        elapsed = time.monotonic() - started
        error = "" if self_ok else f"自检未通过：{json.dumps(自检, ensure_ascii=False)[:220]}"
        record = {
            "registered_model": 要求,
            "actual_model": 实际,
            "model_ok": model_ok,
            "self_check_ok": self_ok,
            "self_check": 自检,
            "loaded_skills": _相关技能(岗位),
            "self_check_max_tokens": used_tokens,
            "ok": ok,
            "elapsed_seconds": round(elapsed, 2),
            "checked_at": dt.datetime.now().isoformat(timespec="seconds"),
        }
        return _格式化自检(岗位, 要求, 实际, self_ok, model_ok, 自检, elapsed, error), record, elapsed
    except Exception as e:  # noqa: BLE001
        elapsed = time.monotonic() - started
        record = {
            "registered_model": 要求,
            "actual_model": "",
            "model_ok": False,
            "self_check_ok": False,
            "ok": False,
            "error": str(e),
            "loaded_skills": _相关技能(岗位),
            "elapsed_seconds": round(elapsed, 2),
            "checked_at": dt.datetime.now().isoformat(timespec="seconds"),
        }
        line = _格式化自检(岗位, 要求, "", False, False, {}, elapsed, str(e)).replace("⚠", "✗", 1)
        return line, record, elapsed


def 成员信息(roles: list[str] | None = None) -> list[dict[str, Any]]:
    """给办公室显示用：花名册是当前模型的唯一真源，点名缓存只负责核验。"""
    roster = 花名册()
    cache = _缓存()
    names = roles or list(roster.keys())
    out: list[dict[str, Any]] = []
    for name in names:
        cfg = roster.get(name, {})
        seen = cache.get(name, {}) if isinstance(cache.get(name), dict) else {}
        registered = str(cfg.get("model") or "未知")
        cached_registered = str(seen.get("registered_model") or "")
        cached_actual = str(seen.get("actual_model") or "")
        verification_current = bool(cached_actual and cached_registered == registered)
        actual = cached_actual if verification_current else ""
        ok = bool(seen.get("ok")) if verification_current else None
        out.append({
            "name": name,
            "名字": cfg.get("名字") or "",
            "title": cfg.get("title") or name,
            "tier": cfg.get("tier") or cfg.get("title") or name,
            "rank": int(str(cfg.get("rank") or "99")),
            "model": registered,
            "registered_model": registered,
            "actual_model": actual,
            "model_ok": ok,
            "checked_at": seen.get("checked_at", "") if verification_current else "",
            "provider": cfg.get("provider", ""),
            "abilities": cfg.get("abilities", ""),
            "modality": cfg.get("modality", "待核验"),
        })
    out.sort(key=lambda x: (x["rank"], x["name"]))
    return out


def 在岗(岗位: str) -> dict:
    r = 花名册()
    if 岗位 not in r:
        raise KeyError(f"花名册无此岗位: {岗位}")
    cfg = r[岗位]
    key = os.environ.get(cfg["key_env"], "")
    if not key:
        if 是远程实例():
            raise RuntimeError(f"岗位[{岗位}]缺少实例模型令牌，拒绝启动")
        raise RuntimeError(f"岗位[{岗位}]缺少密钥: 请在.env填 {cfg['key_env']}")
    if cfg.get("provider") != "anthropic":
        _校验模型地址(
            str(cfg.get("base_url") or "https://api.openai.com/v1"),
            岗位,
            str(cfg.get("明文HTTP授权地址") or ""),
        )
    return {**cfg, "key": key}


def 创建兼容客户端(配置: dict, *, timeout: float | None = None, max_retries: int = 0):
    """用已核验过的岗位配置创建 OpenAI 兼容 SDK 客户端。"""
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=str(配置["key"]),
        base_url=str(配置.get("base_url", "")).rstrip("/") or None,
        timeout=timeout,
        max_retries=max_retries,
    )


def _明文授权匹配(url: str, 授权根: str) -> bool:
    """只允许配置里写明的 HTTP 根地址及其下级 API 路径，避免一个布尔开关放大全公司风险。"""
    if not str(授权根 or "").strip():
        return False
    target = urllib.parse.urlsplit(str(url or ""))
    approved = urllib.parse.urlsplit(str(授权根 or ""))
    target_path = (target.path or "/").rstrip("/") or "/"
    approved_path = (approved.path or "/").rstrip("/") or "/"
    return bool(
        target.scheme == approved.scheme == "http"
        and target.netloc == approved.netloc
        and (target_path == approved_path or target_path.startswith(approved_path + "/"))
    )


def 明文HTTP已授权(cfg: dict, url: str | None = None) -> bool:
    return _明文授权匹配(
        str(url or cfg.get("base_url") or ""),
        str(cfg.get("明文HTTP授权地址") or ""),
    )


def _校验模型地址(url: str, 岗位: str = "", 明文授权地址: str = "") -> None:
    parsed = urllib.parse.urlsplit(str(url or ""))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise RuntimeError(f"岗位[{岗位 or '?'}]模型地址无效")
    if 是远程实例():
        path = (parsed.path or "/").rstrip("/") or "/"
        if not (
            parsed.scheme == "http"
            and parsed.hostname == "model-proxy"
            and parsed.port == 4000
            and (path == "/v1" or path.startswith("/v1/"))
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
        ):
            raise RuntimeError(f"岗位[{岗位 or '?'}]远程实例只允许访问内部模型代理 model-proxy:4000/v1")
        return
    if (
        parsed.scheme == "http"
        and parsed.hostname not in ("127.0.0.1", "localhost", "::1")
        and not _明文授权匹配(url, 明文授权地址)
    ):
        raise RuntimeError(f"岗位[{岗位 or '?'}]模型地址是远端明文 HTTP，平台拒绝发送 API key；请改为可信 HTTPS")


def _post(url: str, headers: dict, body: dict, timeout: int = 120, 明文授权地址: str = "") -> dict:
    import httpx
    _校验模型地址(url, 明文授权地址=明文授权地址)
    response = httpx.post(
        url, headers=headers, json=body, timeout=timeout,
        follow_redirects=False, trust_env=False,
    )
    response.raise_for_status()
    if len(response.content) > 10 * 1024 * 1024:
        raise RuntimeError("模型响应超过 10MB，拒绝载入")
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("模型响应顶层不是 JSON 对象")
    return data


def 调用带元(岗位: str, 提示: str, 最大tokens: int = 16384) -> tuple[str, str]:
    """返回 (回复文本, 服务器应答的model字段)。model字段是身份铁证, 模型嘴上自称什么不算数。"""
    cfg = 在岗(岗位)
    if cfg["provider"] == "anthropic":
        data = _post(
            "https://api.anthropic.com/v1/messages",
            {
                "content-type": "application/json",
                "x-api-key": cfg["key"],
                "anthropic-version": "2023-06-01",
            },
            {
                "model": cfg["model"],
                "max_tokens": 最大tokens,
                "messages": [{"role": "user", "content": 提示}],
            },
        )
        return "".join(b.get("text", "") for b in data.get("content", [])), str(data.get("model", "?"))
    base = cfg.get("base_url", "https://api.openai.com/v1").rstrip("/")
    # 推理模型(GLM-5.2/DeepSeek 等)的 max_tokens 含思维链(CoT)；CoT 典型 2-4K、长度不可控。
    # max_tokens 是上限不是消耗——模型只生成实际需要的量，给足只防截断、不多花钱。
    # 默认 16384：给长程工程任务留出推理和正文空间，同时仍在现役模型的输出上限内。
    # thinking 保持默认开启——干代码/设计/判断必须推理，不关思维链。
    data = _post(
        f"{base}/chat/completions",
        {"content-type": "application/json", "authorization": f"Bearer {cfg['key']}"},
        {
            "model": cfg["model"],
            "max_tokens": 最大tokens,
            "messages": [{"role": "user", "content": 提示}],
        },
        明文授权地址=str(cfg.get("明文HTTP授权地址") or ""),
    )
    msg = data["choices"][0]["message"]
    return (msg.get("content") or ""), str(data.get("model", "?"))


def 调用(岗位: str, 提示: str, 最大tokens: int = 16384) -> str:
    return 调用带元(岗位, 提示, 最大tokens)[0]


def 点名() -> list[str]:
    """逐岗位做开工自检。model字段证明身份；自检报告证明岗位上下文已加载。"""
    return list(点名流())


def 点名流():
    """并行做开工自检；谁先完成谁先产出一行，适合前端流式显示。"""
    if not _点名锁.acquire(blocking=False):
        yield "说明: 点名已在进行中，请稍后再试。"
        return
    名册 = 花名册()
    cache = _缓存()
    names = list(名册.keys())
    started = time.monotonic()
    try:
        yield f"并行自检已发起：{len(names)} 个岗位（总计0.0s）。"
        for 岗 in names:
            yield f"开始 {岗}: 自检请求已发出。"
        with cf.ThreadPoolExecutor(max_workers=max(1, min(len(names), 8))) as ex:
            futs = {ex.submit(_点名单岗, 岗, 名册[岗]): 岗 for 岗 in names}
            for fut in cf.as_completed(futs):
                岗 = futs[fut]
                try:
                    line, record, _elapsed = fut.result()
                    cache[岗] = record
                except Exception as e:  # noqa: BLE001
                    elapsed = time.monotonic() - started
                    line = f"✗ {岗}: {e}"
                    cache[岗] = {
                        "registered_model": 名册.get(岗, {}).get("model", "?"),
                        "actual_model": "",
                        "model_ok": False,
                        "self_check_ok": False,
                        "ok": False,
                        "error": str(e),
                        "elapsed_seconds": round(elapsed, 2),
                        "checked_at": dt.datetime.now().isoformat(timespec="seconds"),
                    }
                _写缓存(cache)
                yield line
        yield f"说明: ✓=model字段一致且岗位自检通过；⚠=身份或自检有问题；✗=调用失败。总耗时{_耗时(time.monotonic() - started)}。"
    finally:
        _点名锁.release()


if __name__ == "__main__":
    print("\n".join(点名()))
