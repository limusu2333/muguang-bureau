#!/usr/bin/env python3
"""办公室：活公司的本地网页入口。

主入口只有 `/活厅`：船主发话 → 唤醒项目经理本人 → 本人自主判断、查证、协调、派活、请示或报告。
旧流程驱动接口只返回归档提示，不能继续制造旧公司假动作。
"""
from __future__ import annotations

import datetime as dt
import asyncio
import copy
import http.client
import http.server
import hashlib
import json
import os
import re
import secrets
import signal
import sys
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE / "工具"))
from 实例配置 import 实例字段, 主人ID, 主人显示名, 主人称呼  # noqa: E402
from 根 import 代码根, 工作根, 数据根, 本机环境文件, 是远程实例  # noqa: E402

CODE = 代码根
COMPANY = 数据根
WORK = 工作根
from 项目室 import 安全名 as _安全项目名  # noqa: E402
from 项目室 import 记录 as 记录项目对话  # noqa: E402
from 项目室 import 读取 as 项目记忆文本  # noqa: E402
# 进程工作目录只用于用户产物；代码、数据和工作区均由各模块显式选根。
WORK.mkdir(parents=True, exist_ok=True)
os.chdir(WORK)
PORT = int(os.environ.get("XJ_OFFICE_PORT", "8787"))
_本次启动时间 = dt.datetime.now()

# 瞬时停止：保存活跃任务引用，/panic 调 task.cancel() 瞬间停止
_活跃任务: dict[str, Any] = {}   # sid → (loop, task)
_活跃任务锁 = threading.Lock()
_工作线程: dict[str, threading.Thread] = {}
_工作线程锁 = threading.Lock()
_停止接活 = threading.Event()
请求体上限 = 20 * 1024 * 1024
_启动编号 = secrets.token_urlsafe(18)


def _设置告警宿主边界() -> None:
    """所有正常办公室入口都强制共用宿主模型；测试可显式使用 mock。"""
    if os.environ.get("XJ_MOCK") == "1":
        return
    # Do not let a stale shell/LaunchAgent value silently re-enable a second
    # local MLX copy in the ordinary office entry point.
    os.environ["XJ_ALERT_RENDER_REQUIRED"] = "1"
    # `make office` bypasses 打开办公室.command, so provide the same safe
    # loopback endpoint here. The token is read only from Keychain; failure
    # deliberately leaves the client on rule cards instead of loading MLX.
    os.environ["XJ_ALERT_RENDER_URL"] = "http://127.0.0.1:37657"
    if len(os.environ.get("XJ_ALERT_RENDER_TOKEN", "").strip()) < 32:
        try:
            result = subprocess.run(
                [
                    "/usr/bin/security", "find-generic-password",
                    "-s", "xj-multiuser-platform", "-a", "rerank-token", "-w",
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            token = str(result.stdout or "").strip() if result.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            token = ""
        if len(token) >= 32:
            os.environ["XJ_ALERT_RENDER_TOKEN"] = token


def _启动工作线程(name: str, target) -> threading.Thread:
    """统一登记后台线程。线程非 daemon，关门时会等待它明确收尾。"""
    def _包() -> None:
        try:
            target()
        finally:
            with _工作线程锁:
                _工作线程.pop(name, None)

    t = threading.Thread(target=_包, name=name, daemon=False)
    with _工作线程锁:
        _工作线程[name] = t
    t.start()
    return t


_轮转故障位 = COMPANY / "运行状态" / "日常轮转"
_健康请示位 = COMPANY / "请示" / "_健康巡检状态.json"
_健康告警位 = COMPANY / "运行状态" / "健康告警.json"
_健康告警锁 = threading.RLock()
_健康告警目录: dict[str, dict[str, Any]] = {}


def _登记健康告警(alerts: list[dict[str, Any]]) -> None:
    """保存服务器刚发给前端的告警，提交决定时只认这里的原件。"""
    with _健康告警锁:
        for alert in alerts:
            fingerprint = str(alert.get("指纹") or "")
            if fingerprint:
                _健康告警目录[fingerprint] = copy.deepcopy(alert)
        while len(_健康告警目录) > 240:
            _健康告警目录.pop(next(iter(_健康告警目录)))


def _执行日常轮转() -> dict:
    """执行一次容量维护，并把任何分项失败送进健康页。"""
    import 归档
    from 状态存储 import 清故障, 登记故障

    result = 归档.日常轮转()
    failures = [f"{key}: {value}" for key, value in result.items() if str(key).endswith("故障")]
    if failures:
        登记故障(_轮转故障位, "；".join(failures))
    else:
        清故障(_轮转故障位)
    return result


def _日常维护循环() -> None:
    """办公室长期开着时也每天维护一次；收到关门信号会立即退出等待。"""
    interval = max(60, int(os.environ.get("XJ_MAINTENANCE_INTERVAL", "86400")))
    while not _停止接活.wait(interval):
        try:
            result = _执行日常轮转()
            if result:
                print(f"📦 日常容量维护：{result}", flush=True)
        except Exception as e:  # noqa: BLE001
            from 状态存储 import 登记故障

            登记故障(_轮转故障位, e)
            print(f"❌ 日常容量维护失败：{type(e).__name__}: {e}", flush=True)

def _启动时补刷点名() -> None:
    """开门时先看点名缓存是否过期，过期就自动重跑一次。"""
    try:
        from 模型接入 import 核验概览, 点名流

        info = 核验概览()
        if not info.get("需要刷新"):
            return
        缺口 = info.get("缺口") or []
        已过期 = info.get("最旧天数")
        print(f"🔁 点名缓存已过期，启动补刷：最旧 {已过期} 天，缺口 {len(缺口)} 个岗位。", flush=True)
        for line in 点名流():
            print(f"   · {line}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 启动补刷点名失败：{type(e).__name__}: {e}", flush=True)

envf = 本机环境文件()
if envf is not None and envf.exists():
    for line in envf.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def 岗位列表() -> list[str]:
    names = [d.name for d in (CODE / "岗位").iterdir() if d.is_dir()]
    try:
        from 模型接入 import 成员信息

        return [m["name"] for m in 成员信息(names)]
    except Exception:
        return sorted(names)


def 成员列表() -> list[dict[str, Any]]:
    try:
        from 模型接入 import 成员信息

        return 成员信息(岗位列表())
    except Exception:
        return [{"name": r, "title": r, "tier": r, "model": "未知", "abilities": "", "modality": "待核验"} for r in 岗位列表()]


def _晋升目标(人名: str) -> str:
    """给每个人一段『往上爬』的话：现职称 + 到项目经理的路 + 当前晋升进度（在试用就提醒稳住）。"""
    try:
        import 晋升
        import 职级
        级 = 职级.评级(人名)
        职称 = 职级.职称(人名)
        if 职级.是经理(人名):   # 3L6：按现任经理身份判(不按评级==5)——被顶下的顶上者也算经理、不再催他"往上爬"
            return (f"你现在是{职称}，公司最高职位。**守住它**：这是动态的——谁若持续做得比你更扎实、更少出错，会顶替你。"
                    "所以别松懈，一样得不断证明自己。")
        st = 晋升.状态(人名)
        目标级 = 级 + 1
        下一级 = 职级.级名[目标级 - 1] if 目标级 - 1 < len(职级.级名) else "更高一级"
        达标 = 晋升.达标线.get(目标级, 0)
        if st.get("冻结"):
            return (f"你现在是{职称}。**你正在晋升试用期**——接下来 5 笔活里保持准确（最多错 1 笔）就升『{下一级}』，"
                    "错到 2 笔就前功尽弃、清零重来。现在稳住，把每一笔都做扎实。终点是项目经理。")
        return (f"你现在是{职称}。**你的目标：一路往上爬，做到项目经理。**\n"
                f"怎么爬：每个通过船主验收的成果给你攒晋升分，每次被打回大扣分（错一次≈抵三次成功），分还随时间回落——"
                f"所以得**连续十几二十个成果、还不能怎么出错**才够格升『{下一级}』（你现在 {st.get('分', 0):g}/{达标:g} 分）。"
                "这不是拼一两个任务，是长跑：把每一笔都当回事、少犯错、别停手，位子就是你的。")
    except Exception:  # noqa: BLE001
        return "你的目标：一路做到项目经理——靠长期把活干扎实、少出错、不停手，一步步往上爬。"


def 岗位上下文(岗位: str) -> str:
    岗位目录 = CODE / "岗位" / 岗位
    parts: list[str] = []
    # 1) 你是谁
    try:
        from 模型接入 import 花名册

        cfg = 花名册().get(岗位, {})
        parts.append(
            f"## 你的真实身份(系统注入, 以此为准)\n你是开发公司「{岗位}」岗, "
            f"由模型 {cfg.get('model', '未知')} 驱动。自我介绍时只报此身份, 禁止猜测或自称其他模型名。"
        )
    except Exception:
        pass
    # 2) 工作纪律（最高优先，先于任何岗位风格——防止人格压住推理）
    parts.append(
        "## 你的工作纪律（最高优先，先于任何岗位风格）\n"
        "不管你是什么岗位、什么性格，动口下结论之前永远先走这四步：\n"
        "① 先查清事实与项目约束——读当前活/历史工单、总纲、相关资料和现有实现，别凭印象或记忆；缺资料就去读，读不到就明说缺什么。\n"
        "② 显式分析——把关键因素、取舍、风险一条条想清楚，别拿到问题就选边表态。\n"
        "③ 用证据下结论——每个判断都要能指回事实（当前活要求/项目约束/代码现状/命令输出）。\n"
        "④ 诚实标注——没查证、拿不准的，明说\"未验证/不确定/需要X\"，绝不编造理由或经历来凑。\n"
        "你的岗位视角只是让你多盯某一面，绝不能代替以上分析。"
    )
    # 3) 职责与权限
    说明书p = 岗位目录 / "说明书.md"
    if 说明书p.exists():
        parts.append(说明书p.read_text(encoding="utf-8"))
    # 4) 公司纪律
    parts.append((CODE / "班规.md").read_text(encoding="utf-8"))
    # 5) 岗位视角（轻，补充，不替代分析）
    宪法p = 岗位目录 / "宪法.md"
    if 宪法p.exists():
        parts.append(宪法p.read_text(encoding="utf-8"))
    # 6) 工作经验（真实记录，非编造来历；活公司按人存，旧岗位记忆只作历史兼容）
    try:
        from 活_本人 import _岗位的人
        from 活_本人 import _记忆文件; 记忆p = _记忆文件(_岗位的人(岗位))
    except Exception:  # noqa: BLE001
        记忆p = COMPANY / "不存在的旧岗位记忆"
    if 记忆p.exists():
        parts.append(
            "## 你的工作经验（你做过的事里学到的，第一人称；都是真实工作记录，不是编的故事）\n\n"
            + 记忆p.read_text(encoding="utf-8")
        )
    # 6.5) 信誉画像（奖惩：信任等级+最近结果注入上下文，让本人带着评价干活；不改变汇报层级）
    try:
        import 信誉
        _画像 = 信誉.画像(_岗位的人(岗位))
        if _画像:
            parts.append(
                "## 你的信誉（只用于反馈与额外复核，不改变汇报层级）\n\n"
                "无论信誉高低，你都必须依次经过部门头把关、经理收敛和船主验收；"
                "高信誉不能免检、不能越级。信誉受限时，公司会在原链路外增加一次独立复核。\n"
                + _画像
            )
    except Exception:  # noqa: BLE001
        pass
    # 6.6) 晋升目标（给每个人一个往上爬的方向：一路做到项目经理——靠长期把活干扎实，不是拼一两个任务）
    try:
        parts.append("## 你的目标：一路做到项目经理\n\n" + _晋升目标(_岗位的人(岗位)))
    except Exception:  # noqa: BLE001
        pass
    # 7) 技能
    技能目录 = CODE / "技能"
    技能文件 = sorted((技能目录 / "通用").glob("*.md")) + sorted((技能目录 / "岗位" / 岗位).glob("*.md"))
    for sk in 技能文件:
        if sk.exists():
            parts.append(sk.read_text(encoding="utf-8"))
    return "\n\n---\n\n".join(parts)


from 附件服务 import 保存附件, 带附件文本  # noqa: E402,F401


def _大厅入口需预检(目标岗位: str, 被点名: bool) -> bool:
    """只有明确点名的非经理岗位在入口预检。

    经理席必须先进大厅，才能在本人不可用时交给临时经理；
    未点名的当前承接人若掉线，也由大厅回到经理席接住。
    """
    return bool(被点名) and str(目标岗位 or "") != "项目经理"


async def _消解请示(原话: str, 会话id: str, 发进度) -> bool:
    """大厅↔审批打通(2026-07-09船主拍板：哪边答都行)：船主这句若明确在答右栏某条'待他裁决'的请示→
    自动办掉(批/驳)。它是大厅消息，答复本身会被现有抽取自动学进偏好，学习闭环顺带合上。
    保守铁律：拿不准/判官挂/没pending 一律不动，请示照旧等右栏按钮，绝不替他乱办。返回是否办掉了。"""
    原话 = str(原话 or "").strip()
    if not 原话:
        return False
    try:
        import 审批
        待 = 审批.列表().get("待船主", [])
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"待裁决请示读取失败，未改动任何请示：{type(e).__name__}: {e}") from e
    if not 待:
        return False
    # 红线请示(删除/花钱/密钥/授权/改班规/上线部署)绝不从大厅闲聊自动办——那必须是右栏按钮那一下 deliberate 的动作。
    红线词 = ("删", "删除", "覆盖", "花钱", "钱", "费用", "预算", "密钥", "账号", "授权", "班规", "红线", "最终交付", "上线", "部署")
    候选 = [c for c in 待[:10]
            if not any(w in (str(c.get('任务', '')) + str(c.get('内容', ''))) for w in 红线词)][:6]
    if not 候选:
        return False   # 剩下全是红线请示→一条都不自动办，等按钮
    列 = "\n".join(f"[{i}] 问的是：{str(c.get('内容', ''))[:160]}" for i, c in enumerate(候选))
    try:
        from 记忆抽取 import 抽取模型岗, _剥JSON
        from 升级_模型层 import 建Agent
        from agentscope.message import Msg, TextBlock
        系统 = (
            f"下面是几条'等{主人称呼()}裁决'的请示（每条都在问他一个具体问题）。判断他刚说的这句话，"
            "是不是在【直接、具体地回答其中某一条问的那个点】，态度是同意(批)还是否决(驳)。\n"
            "严格铁律：\n"
            "· 泛泛的、不针对具体问题的话（'好'、'继续'、'就是聊聊'、'随便'、'你们看着办'、'嗯'）一律答序号=-1"
            "——它没有回答任何一条具体请示。\n"
            "· 只有他的话【明确对上某一条请示问的那个具体点】、态度又清楚，才认；只要有一丝拿不准，一律 -1"
            "（让他自己点右栏按钮，绝不替他乱办）。\n"
            '只输出JSON：{"序号":数字或-1,"裁决":"批|驳"}。\n\n# 待裁决请示\n' + 列)
        a = 建Agent(抽取模型岗, 系统, 名字="请示消解判官", 最大tokens=40, 思考=False)
        r = await a.reply(Msg(name="办公室", content=[TextBlock(type="text", text=f"他刚说：{原话}")], role="user"))
        c = getattr(r, "content", "")
        文 = c if isinstance(c, str) else "".join(
            (b.get("text") if isinstance(b, dict) else getattr(b, "text", "")) or "" for b in (c or []))
        obj = _剥JSON(str(文)) or {}
        序号 = int(obj.get("序号", -1))
        裁 = str(obj.get("裁决", "")).strip()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"请示消解判定失败，未改动任何请示：{type(e).__name__}: {e}") from e
    if 序号 < 0 or 序号 >= len(候选) or 裁 not in ("批", "驳"):
        return False
    卡 = 候选[序号]
    try:
        import 审批
        审批.船主裁决(str(卡.get("id", "")), 裁, 原话)
        try:
            import 看板数据
            看板数据.清待办缓存()
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"请示裁决落账失败，原请示保持不变：{type(e).__name__}: {e}") from e
    try:
        发进度({"类型": "进展", "内容": f"（已按你的话把请示办了：{str(卡.get('任务', ''))[:30]} → {裁}）"})
    except Exception:  # noqa: BLE001
        pass
    return True


def 裁决(单名: str, 在列: str, 结论: str, 理由: str) -> str:
    src = COMPANY / "工单" / 在列 / 单名
    if not src.exists():
        return "工单不存在(可能已被移动), 刷新后重试"
    now = dt.datetime.now().strftime("%m-%d %H:%M")
    with src.open("a", encoding="utf-8") as f:
        f.write(f"\n> [船主裁决 {now}] **{结论}** {理由}\n")
    目标 = {"通过": "已完成", "打回": "进行中", "作废": "已作废"}.get(结论, 在列)
    (COMPANY / "工单" / 目标).mkdir(parents=True, exist_ok=True)
    src.rename(COMPANY / "工单" / 目标 / 单名)
    return f"{单名}: {结论} → {目标}/"


# ── 班子健康探测（极小真实调用：验证具体型号可用，不再只看接口能否打开）──
_健康缓存: dict[str, dict] = {}
_健康锁 = threading.Lock()
_健康有效秒 = 15 * 60


def _探一岗(岗位: str, cfg: dict) -> dict:
    """用一次 max_tokens=1 的真实回答验证该岗位当前配置的具体型号。"""
    import time as _t
    import httpx
    cfg = dict(cfg or {})
    base = str(cfg.get("base_url", "")).rstrip("/") or "https://api.openai.com/v1"
    key = str(cfg.get("key", "")) or os.environ.get(str(cfg.get("key_env", "")), "")
    model = str(cfg.get("model", "")).strip()
    t0 = _t.monotonic()
    parsed = urllib.parse.urlsplit(base)
    stamp = dt.datetime.now().strftime("%H:%M:%S")
    if parsed.scheme not in ("http", "https"):
        return {"ok": False, "状态": "配置错误", "原因": "base_url 协议无效", "毫秒": 0, "时间": stamp}
    from 模型接入 import 明文HTTP已授权, _校验模型地址
    已授权明文 = 明文HTTP已授权(cfg, base)
    try:
        _校验模型地址(base, 岗位, str(cfg.get("明文HTTP授权地址") or ""))
    except RuntimeError as e:
        return {"ok": False, "状态": "不安全配置", "原因": str(e), "毫秒": 0, "时间": stamp}
    if not key:
        return {"ok": False, "状态": "未配置", "原因": "缺 API key", "毫秒": 0, "时间": stamp}
    if not model:
        return {"ok": False, "状态": "配置错误", "原因": "缺模型编号", "毫秒": 0, "时间": stamp}
    try:
        response = httpx.post(
            base + "/chat/completions",
            headers={"content-type": "application/json", "authorization": "Bearer " + key},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "stream": False,
            },
            timeout=15, follow_redirects=False, trust_env=False,
        )
        code = response.status_code
        ok = 200 <= code < 300
        if ok:
            try:
                data = response.json()
                choices = data.get("choices") if isinstance(data, dict) else None
                actual = str(data.get("model") or "") if isinstance(data, dict) else ""
            except Exception:  # noqa: BLE001
                choices, actual = None, ""
            if not isinstance(choices, list) or not choices:
                ok, 状态, 原因 = False, "响应异常", f"{model} 返回 HTTP {code}，但没有回答"
            else:
                状态 = "健康"
                型号说明 = f"（服务端：{actual}）" if actual and actual != model else ""
                原因 = f"{model} 真实调用 HTTP {code}{型号说明}" + ("（先生批准的中转站）" if 已授权明文 else "")
        elif code == 401:
            状态, 原因 = "鉴权失败", "HTTP 401：API key 无效"
        elif code == 403:
            状态, 原因 = "无模型权限", f"HTTP 403：当前 key 无权使用 {model}"
        elif code == 404:
            状态, 原因 = "型号不可用", f"HTTP 404：找不到 {model}"
        elif code == 402:
            状态, 原因 = "欠费或需付费", "HTTP 402"
        elif code == 429:
            状态, 原因 = "限流或额度", "HTTP 429"
        elif 500 <= code < 600:
            状态, 原因 = "上游故障", f"HTTP {code}"
        else:
            状态, 原因 = "调用失败", f"HTTP {code}"
    except Exception as e:  # noqa: BLE001
        ok, 状态, 原因 = False, "连接失败", f"{type(e).__name__}: {str(e)[:80]}"
    return {"ok": ok, "状态": 状态, "原因": 原因, "毫秒": int((_t.monotonic() - t0) * 1000),
            "时间": dt.datetime.now().strftime("%H:%M:%S")}


def 班子健康() -> dict:
    """全班并行做具体型号实测，缓存十五分钟，避免健康页反复消耗额度。"""
    import concurrent.futures as _cf
    import time as _t
    from 模型接入 import 花名册
    now = _t.monotonic()
    with _健康锁:
        if _健康缓存.get("_t", 0) + _健康有效秒 > now:
            return _健康缓存.get("数据", {})
    名册 = 花名册()
    数据: dict[str, dict] = {}
    with _cf.ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(_探一岗, 岗, cfg): 岗 for 岗, cfg in 名册.items()}
        for f in _cf.as_completed(futs):
            数据[futs[f]] = f.result()
    with _健康锁:
        _健康缓存["_t"] = now
        _健康缓存["数据"] = 数据
    return 数据


def _健康巡检摘要(班子: dict[str, Any] | None, 系统: dict[str, Any] | None, 异常: str = "") -> tuple[str, dict[str, Any]]:
    班子 = 班子 or {}
    系统 = 系统 or {}
    班子问题: list[str] = []
    for 岗位, 结果 in sorted(班子.items()):
        if not isinstance(结果, dict):
            班子问题.append(f"{岗位}：结果格式异常")
            continue
        if bool(结果.get("ok")):
            continue
        状态 = str(结果.get("状态") or "异常").strip()
        原因 = str(结果.get("原因") or "").strip()
        班子问题.append(f"{岗位}：{状态}" + (f"；{原因}" if 原因 else ""))
    系统问题: list[str] = []
    for item in 系统.get("问题") or []:
        if not isinstance(item, dict):
            系统问题.append(str(item))
            continue
        级别 = str(item.get("级别") or "").strip()
        区域 = str(item.get("区域") or "").strip()
        说明 = str(item.get("说明") or "").strip()
        系统问题.append(" / ".join(x for x in (级别, 区域) if x) + (f"：{说明}" if 说明 else ""))
    payload = {"异常": str(异常 or "").strip(), "班子问题": 班子问题, "系统问题": 系统问题}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest(), payload


def _健康请示仍在队列(card_id: str) -> bool:
    import 审批

    if not card_id:
        return False
    return any((d / card_id).exists() for d in (审批.待经理, 审批.待船主))


def _健康请示(班子: dict[str, Any] | None = None, 系统: dict[str, Any] | None = None, 异常: str = "") -> dict[str, Any]:
    import 审批
    from 状态存储 import 读JSON, 写JSON

    digest, 摘要 = _健康巡检摘要(班子, 系统, 异常=异常)
    now = dt.datetime.now().isoformat(timespec="seconds")
    active = bool(摘要["异常"] or 摘要["班子问题"] or 摘要["系统问题"])
    try:
        state = 读JSON(_健康请示位, 默认={}, 类型=dict) or {}
    except Exception:
        state = {}
    if not active:
        if state.get("active") or state.get("fingerprint") != digest:
            try:
                写JSON(_健康请示位, {"active": False, "fingerprint": digest, "updated_at": now}, 备份=False)
            except Exception:  # noqa: BLE001
                pass
        return {"created": False, "active": False, "fingerprint": digest, "摘要": 摘要}
    if state.get("active") and state.get("fingerprint") == digest:
        card_id = str(state.get("card_id") or "")
        if _健康请示仍在队列(card_id):
            return {"created": False, "active": True, "fingerprint": digest, "card_id": card_id, "摘要": 摘要}
    任务 = "健康巡检·" + ("异常" if 摘要["异常"] else "不健康") + "·" + dt.datetime.now().strftime("%m%d%H%M%S")
    内容线 = []
    if 摘要["异常"]:
        内容线.append(f"健康检查接口抛异常：{摘要['异常']}")
    if 摘要["班子问题"]:
        内容线.append("班子问题：\n- " + "\n- ".join(摘要["班子问题"]))
    if 摘要["系统问题"]:
        内容线.append("系统问题：\n- " + "\n- ".join(摘要["系统问题"]))
    内容 = "\n\n".join(内容线) or "健康检查发现异常，但未整理出可读问题。"
    建议 = "请先补齐权限/密钥/模型配置，或切到可执行只读探测的环境，再重新核一次健康检查。"
    cid = 审批.创建请示(任务=任务, 岗位="健康巡检", 内容=内容, 建议=建议, 直接上报=True)
    try:
        import 看板数据
        看板数据.清待办缓存()
    except Exception:  # noqa: BLE001
        pass
    try:
        写JSON(_健康请示位, {
            "active": True,
            "fingerprint": digest,
            "card_id": cid,
            "updated_at": now,
            "摘要": 摘要,
        }, 备份=False)
    except Exception:  # noqa: BLE001
        pass
    return {"created": True, "active": True, "fingerprint": digest, "card_id": cid, "摘要": 摘要}


def _记录健康告警处理(data: dict[str, Any]) -> dict[str, Any]:
    from 状态存储 import 读JSON, 写JSON

    指纹 = str(data.get("指纹") or "")[:2000]
    with _健康告警锁:
        告警 = copy.deepcopy(_健康告警目录.get(指纹) or {})
    if not 告警:
        raise ValueError("这张告警已经刷新，请重新打开当前问题后再决定")
    动作 = str(data.get("动作") or "").strip()
    告警动作 = {
        str(item.get("id") or "")
        for item in (告警.get("可选动作") or [])
        if isinstance(item, dict) and item.get("id")
    }
    系统允许动作 = {
        "同意", "稍后", "补充", "恢复任务", "补交产物", "改正交付位置", "核对模型权益",
        "核对权限", "重试任务", "查明并重试", "重新备份", "重新核验模型", "重新检测搜索", "修正组织配置",
        "修复事件记录", "修复状态文件", "恢复管理动作", "重试验收收尾", "查明容量占用",
        "交给项目经理排查", "恢复岗位调用",
    }
    if 动作 not in 告警动作 or 动作 not in 系统允许动作:
        # 兼容升级前尚未带动态选项的旧告警卡。
        if 告警动作 or 动作 not in {"同意", "稍后", "补充"}:
            raise ValueError("这项处理不在当前告警允许的动作里")
    补充 = str(data.get("补充") or "").strip()
    if 动作 == "补充" and not 补充:
        raise ValueError("补充内容不能为空")
    now = dt.datetime.now().isoformat(timespec="seconds")
    record_id = "健康-" + hashlib.sha256(
        f"{time.time_ns()}|{data.get('指纹', '')}|{动作}".encode("utf-8")
    ).hexdigest()[:14]
    record = {
        "id": record_id,
        "时间": now,
        "更新时间": now,
        "动作": 动作,
        "状态": "稍后" if 动作 == "稍后" else "已接收",
        "指纹": 指纹,
        "补充": 补充[:2000],
        "告警": 告警,
        "会话id": "",
        "结果": "",
        "错误": "",
    }
    with _健康告警锁:
        try:
            state = 读JSON(_健康告警位, 默认={}, 类型=dict) or {}
        except Exception:
            state = {}
        history = list(state.get("记录") or [])[-79:]
        history.append(record)
        写JSON(_健康告警位, {"最后处理": record, "记录": history, "updated_at": now}, 备份=False)
    return record


def _更新健康告警处理(record_id: str, **fields: Any) -> dict[str, Any]:
    from 状态存储 import 读JSON, 写JSON

    with _健康告警锁:
        state = 读JSON(_健康告警位, 默认={}, 类型=dict) or {}
        history = list(state.get("记录") or [])
        found: dict[str, Any] | None = None
        for item in history:
            if str(item.get("id") or "") == record_id:
                found = item
                break
        if found is None:
            raise FileNotFoundError(f"健康告警处理记录不存在：{record_id}")
        for key, value in fields.items():
            found[key] = value
        found["更新时间"] = dt.datetime.now().isoformat(timespec="seconds")
        state["最后处理"] = found
        state["updated_at"] = found["更新时间"]
        写JSON(_健康告警位, state, 备份=False)
        return dict(found)


def _健康告警处理视图(record: dict[str, Any]) -> dict[str, Any]:
    """把后台会话的真实终态映射回提问卡；读取失败也要把原因交给船主。"""
    out = dict(record)
    sid = str(out.get("会话id") or "")
    if not sid:
        return out
    try:
        import 任务台

        task = 任务台.读取(sid)
    except Exception as e:  # noqa: BLE001
        out.update({"状态": "状态不可读", "错误": f"任务状态读取失败：{type(e).__name__}: {e}"})
        return out
    if not task:
        return out
    task_status = str(task.get("状态") or "")
    if task_status in ("已接收", "运行中", "收尾中"):
        out["状态"] = "处理中"
    elif task_status in ("已完成", "待验收"):
        out["状态"] = "已完成"
    elif task_status in ("失败", "已停止", "被重启中断", "被门禁拦住"):
        out["状态"] = "失败"
    out["任务状态"] = task_status
    out["结果"] = str(task.get("结果") or out.get("结果") or "")
    out["错误"] = str(task.get("错误") or out.get("错误") or "")
    out["更新时间"] = str(task.get("更新时间") or out.get("更新时间") or "")
    return out


def _健康告警处理列表(record_id: str = "") -> list[dict[str, Any]]:
    from 状态存储 import 读JSON

    try:
        state = 读JSON(_健康告警位, 默认={}, 类型=dict) or {}
    except Exception:
        return []
    records = [x for x in (state.get("记录") or []) if isinstance(x, dict)]
    if record_id:
        records = [x for x in records if str(x.get("id") or "") == record_id]
    return [_健康告警处理视图(x) for x in records[-20:]][::-1]


def _启动健康告警后台处理(record: dict[str, Any]) -> str:
    """只排队，不在船主的 HTTP 请求里执行模型调用。"""
    import 事件总线

    record_id = str(record.get("id") or "")
    alert = record.get("告警") or {}
    title = str(alert.get("标题") or "一项公司健康告警")
    detail = str(alert.get("详情") or "没有留下详情")
    suggestion = str(alert.get("建议") or "请先查明原因，再按公司权限制度处理")
    owner_note = str(record.get("补充") or "")
    action = str(record.get("动作") or "")
    action_info = next(
        (item for item in (alert.get("可选动作") or []) if isinstance(item, dict) and item.get("id") == action),
        {},
    )
    if action == "补充":
        decision = f"我的补充是：{owner_note}"
    elif action == "同意":
        decision = "我同意按建议处理这件事。"
    else:
        decision = f"我选择：{action}（{action_info.get('标题') or action}：{action_info.get('说明') or '按这项决定处理'}）。"
    instruction = (
        "【船主处理健康告警】\n"
        f"告警：{title}\n"
        f"真实原因：{detail}\n"
        f"系统建议：{suggestion}\n"
        f"船主决定：{decision}\n\n"
        "请项目经理先核对原任务记录，再按这项决定继续处理。"
        "如果船主选的是重新执行，就按原任务再执行一遍，不要把已经确定的失败原因重新说成‘原因不明’。"
        "这次决定只针对当前告警，不扩大到其他任务；如果涉及费用、密钥、删除文件或扩大权限范围，仍须再次向船主请示。"
    )
    sid = 事件总线.新会话id()
    if action == "重新备份":
        _更新健康告警处理(record_id, 状态="备份中", 会话id=sid)

        def _直接备份() -> None:
            try:
                import 备份 as 备份系统

                archive = 备份系统.强制备份()
                verified = 备份系统.验证(archive)
                if not verified.get("ok"):
                    raise RuntimeError("新备份校验未通过：" + "；".join(verified.get("问题") or []))
                _更新健康告警处理(
                    record_id,
                    状态="已完成",
                    结果=f"已生成并完整校验：{archive.name}（{verified.get('文件数', 0)} 个文件）",
                    错误="",
                )
            except Exception as e:  # noqa: BLE001
                _更新健康告警处理(record_id, 状态="失败", 错误=f"{type(e).__name__}: {e}")

        _启动工作线程(f"备份处理-{record_id}", _直接备份)
        return sid
    _更新健康告警处理(record_id, 状态="正在交给项目经理", 会话id=sid)

    def _派发() -> None:
        connection: http.client.HTTPConnection | None = None
        try:
            if action == "补充":
                owner_display = f"补充处理要求：{owner_note}"
            elif action == "同意":
                owner_display = f"同意处理：{title}"
            else:
                owner_display = f"决定「{action}」：{title}"
            body = json.dumps({
                "text": instruction,
                "sid": sid,
                "_健康处理id": record_id,
                "_船主显示": owner_display,
            }, ensure_ascii=False).encode("utf-8")
            connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=120)
            internal_path = urllib.parse.quote("/活厅", safe="/")
            connection.request("POST", internal_path, body=body, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            payload = json.loads(response.read() or b"{}")
            if not payload.get("已开始") or str(payload.get("sid") or "") != sid:
                reason = payload.get("error") or payload.get("原因") or "项目经理没有接下这项处理"
                raise RuntimeError(str(reason))
            _更新健康告警处理(record_id, 状态="处理中", 会话id=sid)
        except Exception as e:  # noqa: BLE001
            _更新健康告警处理(record_id, 状态="失败", 错误=f"{type(e).__name__}: {e}")
        finally:
            if connection is not None:
                connection.close()

    _启动工作线程(f"健康处理-{record_id}", _派发)
    return sid


def _json_response(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode()


def _运行事件快照(raw_sid: str, raw_after: Any = 0) -> dict[str, Any]:
    """给短请求补收同一条事件流；只读，不制造事件或改任务状态。"""
    import 事件总线
    import 任务台

    sid = 事件总线.校验会话id(raw_sid)
    after = max(0, int(raw_after or 0))
    events = [
        event for event in 事件总线.读事件(sid)
        if int(event.get("序号") or 0) > after
    ]
    task = 任务台.读取(sid) or {}
    status = str(task.get("状态") or "")
    ended = status in {"已完成", "失败", "已停止", "被重启中断", "待验收", "被门禁拦住"}
    return {
        "sid": sid,
        "事件": events,
        "状态": status,
        "运行中": 事件总线.在跑(sid),
        "已结束": ended or any(event.get("类型") == "run结束" for event in events),
        "错误": str(task.get("错误") or ""),
    }


class H(http.server.BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str = "text/html; charset=utf-8", cache: str = "no-store") -> None:
        try:
            self.send_response(200)
            self.send_header("content-type", ctype)
            self.send_header("cache-control", cache)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _json(self, obj: Any) -> None:
        self._send(_json_response(obj), "application/json; charset=utf-8")

    def _body(self) -> dict[str, Any]:
        try:
            n = int(self.headers.get("content-length", 0))
        except ValueError as e:
            raise ValueError("Content-Length 无效") from e
        if n < 0 or n > 请求体上限:
            raise ValueError(f"请求体超过 {请求体上限 // 1024 // 1024}MB")
        raw = self.rfile.read(n)
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError("请求正文必须是 JSON 对象")
        return data

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)  # 修复:中文路径浏览器会URL编码(%E5..),不解码就匹配不上
        模式 = "演示模式(未连模型)" if os.environ.get("XJ_MOCK") == "1" else "真模式"
        if path == "/rollcall":
            try:
                from 模型接入 import 点名流

                self.send_response(200)
                self.send_header("content-type", "text/event-stream; charset=utf-8")
                self.send_header("cache-control", "no-cache")
                self.send_header("connection", "close")
                self.send_header("x-accel-buffering", "no")
                self.end_headers()
                self.close_connection = True
                for line in 点名流():
                    payload = json.dumps({"line": line}, ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except Exception as e:  # noqa: BLE001
                self.wfile.write(f"data: {json.dumps({'line': '点名失败: ' + str(e)}, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
            return
        if path == "/kickoff_stream":
            # 旧流水线已归档，前端统一走 /活厅
            self._send(b"data: {}\n\n", "text/event-stream; charset=utf-8")
            return
        if path == "/stream":  # 长连接 SSE——replay 历史事件 + live 推送
            import 事件总线
            sid = (urllib.parse.parse_qs(parsed.query).get("sid") or [""])[0]
            if not sid:
                self.send_response(400)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("content-type", "text/event-stream; charset=utf-8")
            self.send_header("cache-control", "no-cache")
            # 这是一次会话的有限事件流。明确用连接关闭标记正文结束，避免公网反向代理
            # 因“无 Content-Length + keep-alive”一直等待，最终让浏览器永远停在“正在回应”。
            self.send_header("connection", "close")
            self.send_header("x-accel-buffering", "no")
            self.end_headers()
            self.close_connection = True
            try:
                try:
                    从序号 = int(self.headers.get("last-event-id") or 0)
                except ValueError:
                    从序号 = 0
                for frame in 事件总线.SSE流(sid, 从序号=从序号):
                    self.wfile.write(frame.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                print(f"[/stream {sid}] 客户端断开(BrokenPipe/Reset)——正常收尾", flush=True)
            except Exception as e:  # noqa: BLE001  其它中断:打出真因,供排查"会话流连接中断"
                print(f"[/stream {sid}] 异常中断: {type(e).__name__}: {e}", flush=True)
            return
        if path == "/run-events":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                snapshot = _运行事件快照(
                    (query.get("sid") or [""])[0],
                    (query.get("after") or ["0"])[0],
                )
            except (TypeError, ValueError) as e:
                self._json({"error": f"事件补拉参数无效：{e}"})
                return
            self._json(snapshot)
            return
        if path == "/state":
            self._json({"状态": "活公司", "旧引擎": "已归档"})
            return
        if path == "/运行身份":
            if 是远程实例():
                self.send_error(404)
                return
            try:
                if str(CODE) not in sys.path:
                    sys.path.insert(1, str(CODE))
                from 多用户.版本身份 import 读取运行版本身份

                self._json(读取运行版本身份(development=True))
            except Exception as e:  # noqa: BLE001
                self._json({"mode": "unknown", "version": "", "version_name": "", "error": str(e)})
            return
        if path == "/instance.js":
            identity = {
                "accountId": str(实例字段("account_id", 默认="local")),
                "bootId": _启动编号,
                "displayName": 主人显示名(),
                "callName": 主人称呼(),
            }
            script = "window.__XJ_INSTANCE__=" + json.dumps(
                identity, ensure_ascii=True, separators=(",", ":"),
            ) + ";\n"
            self._send(script.encode("utf-8"), "application/javascript; charset=utf-8", "no-store")
            return
        if path == "/对外访问":
            if 是远程实例():
                self.send_error(404)
                return
            try:
                import 对外访问

                self._json(对外访问.状态())
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "状态": "异常", "网址": "", "error": str(e)})
            return
        if path == "/健康":  # 具体型号极小实测（15分钟缓存）
            try:
                from 健康 import 系统健康, 补齐告警
                班子 = 班子健康()
                系统 = 系统健康(活动起点=_本次启动时间)
                系统["告警"] = 补齐告警(班子, 系统)
                try:
                    from 告警表达 import 呈现, 状态 as 告警表达状态

                    系统["告警"] = 呈现(list(系统.get("告警") or []))
                    系统["告警表达"] = 告警表达状态()
                except Exception as e:  # noqa: BLE001
                    系统["告警表达"] = {"状态": "不可用", "可用": False, "错误": f"{type(e).__name__}: {e}"}
                _登记健康告警(list(系统.get("告警") or []))
                _健康请示(班子, 系统)
                payload = {"班子": 班子, "系统": 系统}
                for key in ("任务", "备份", "模型核验", "统一搜索", "告警", "历史告警", "告警表达", "时间"):
                    if key in 系统:
                        payload[key] = 系统[key]
                self._json(payload)
            except Exception as e:  # noqa: BLE001
                try:
                    _健康请示(None, None, 异常=f"{type(e).__name__}: {e}")
                except Exception:  # noqa: BLE001
                    pass
                self._json({"班子": {}, "系统": {"ok": False, "问题": [{"级别": "错误", "区域": "健康页", "说明": str(e)}]}, "error": str(e)})
            return
        if path == "/健康告警_处理状态":
            record_id = (urllib.parse.parse_qs(parsed.query).get("id") or [""])[0]
            records = _健康告警处理列表(record_id)
            self._json({"ok": True, "记录": records, "处理": records[0] if record_id and records else None})
            return
        if path.startswith("/assets/"):  # React 构建产物(前端/dist/assets/*),hash 文件名可长缓存
            dist = (CODE / "前端" / "dist").resolve()
            f = (dist / path.lstrip("/")).resolve()
            if f.is_file() and str(f).startswith(str(dist)):
                ctype = {
                    ".js": "application/javascript; charset=utf-8",
                    ".css": "text/css; charset=utf-8",
                    ".svg": "image/svg+xml",
                    ".woff2": "font/woff2",
                    ".woff": "font/woff",
                    ".map": "application/json; charset=utf-8",
                }.get(f.suffix, "application/octet-stream")
                self._send(f.read_bytes(), ctype, "public, max-age=31536000")
            else:
                self.send_response(404)
                self.end_headers()
            return
        if path == "/memory":  # 记忆库后台：每个人的四区 + 待确认（Kindroid式可见后台）
            try:
                from 活_本人 import _岗位的人, _读记忆区
                人们 = []
                for 岗 in 岗位列表():
                    人 = _岗位的人(岗)
                    区 = _读记忆区(人)
                    try:
                        import 信誉
                        _信任, _信誉画像 = 信誉.信任等级(人), 信誉.画像(人)
                    except Exception:  # noqa: BLE001
                        _信任, _信誉画像 = "中", ""
                    人们.append({
                        "人名": 人, "岗位": 岗,
                        "信任等级": _信任, "信誉画像": _信誉画像,
                        "原则": 区["原则"].strip(),
                        "教导": [l for l in 区["船主教导"].splitlines() if l.strip()],  # 叮嘱：直接生效、可删可改
                        "近况": 区["近况"].strip(),
                        "流水": [l for l in 区["流水"].splitlines() if l.strip()],
                    })
                try:  # 船主图谱：关于实例主人的现行事实(带id/来源类/pinned)，供UI展示+删/钉
                    import 图记忆
                    图谱 = [{"id": x["id"], "谓": x["rel"], "宾": x["dst"],
                            "来源类": x.get("来源类", "推断"), "pinned": x.get("pinned", 0)}
                           for x in 图记忆.查现值(主人ID())]
                except Exception:  # noqa: BLE001
                    图谱 = []
                self._json({"人们": 人们, "船主图谱": 图谱})
            except Exception as e:  # noqa: BLE001
                self._json({"人们": [], "error": str(e)})
            return
        if path == "/board-version":
            import 看板数据

            self._json({"version": 看板数据.看板版本(模式, 岗位列表())})
            return
        if path == "/board":
            import 看板数据

            self._json(看板数据.看板(模式, 岗位列表()))
            return
        if path == "/room":
            import 看板数据

            mid = (urllib.parse.parse_qs(parsed.query).get("id") or [""])[0]
            self._json(看板数据.项目详情(mid))
            return
        if path == "/inbox":
            import 看板数据

            role = (urllib.parse.parse_qs(parsed.query).get("role") or [""])[0]
            self._json({"历史": 看板数据.信箱(role)})
            return
        if path == "/hall":
            import 看板数据

            self._json(看板数据.大厅(岗位列表()))
            return
        if path == "/hall_day":
            import 看板数据

            day = (urllib.parse.parse_qs(parsed.query).get("date") or [""])[0]
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                self._json({"事件": [], "历史日期": [], "error": "日期格式应为 YYYY-MM-DD"})
            else:
                self._json(看板数据.大厅某日(day))
            return
        if path == "/职级":   # 全公司职级快照（谁什么级、谁是部门头/经理），前端职级面板用
            try:
                import 职级
                self._json({"名册": 职级.名册()})
            except Exception as e:  # noqa: BLE001
                self._json({"名册": [], "error": str(e)})
            return
        if path == "/meetings":
            import 协同

            room = (urllib.parse.parse_qs(parsed.query).get("room") or [""])[0]
            self._json({"会议": 协同.项目会议(room)})
            return
        if path == "/meeting_replay":
            import 会议室记录

            mid = (urllib.parse.parse_qs(parsed.query).get("id") or [""])[0]
            self._json({"会议id": mid, "记录": 会议室记录.读一场(mid)})
            return
        # 根路径 → React 单页入口(前端/dist/index.html)。旧 页面.py/页面.js/css 已归档到 _历史_勿执行/。
        index = CODE / "前端" / "dist" / "index.html"
        if index.is_file():
            self._send(index.read_bytes(), "text/html; charset=utf-8", "no-store")
        else:
            self._send("<h1>前端未构建</h1><p>在 前端/ 跑 npm run build 生成 dist/。</p>".encode("utf-8"))

    def do_POST(self) -> None:  # noqa: N802
        self.path = urllib.parse.unquote(self.path)  # 修复:中文路径(/判断 /插话)浏览器会URL编码,先解码再匹配
        try:
            data = self._body()
        except Exception as e:  # noqa: BLE001
            self._json({"ok": False, "error": f"请求无效：{type(e).__name__}: {e}"})
            return
        if self.path in ("/kickoff_stream", "/trigger"):
            # 旧流水线已归档，统一走 /活厅
            self._json({"reply": "旧执行引擎已归档，请直接在大厅说话，项目经理本人会处理。"})
            return
        if self.path == "/健康告警_处理":
            try:
                record = _记录健康告警处理(data)
                if record["动作"] == "稍后":
                    self._json({"ok": True, "已接收": True, "稍后": True, "处理": record})
                    return
                sid = _启动健康告警后台处理(record)
                status = "备份中" if record["动作"] == "重新备份" else "正在交给项目经理"
                self._json({
                    "ok": True,
                    "已接收": True,
                    "状态": status,
                    "处理": {**record, "会话id": sid, "状态": status},
                })
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if self.path in ("/对外访问_开启", "/对外访问_关闭"):
            if self.headers.get("X-XJ-Owner-Control") != "1":
                self._json({"ok": False, "error": "只允许从本机公司界面操作。"})
                return
            try:
                import 对外访问

                result = 对外访问.开启() if self.path.endswith("开启") else 对外访问.关闭()
                self._json(result)
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "状态": "异常", "error": str(e)})
            return
        if self.path == "/活厅":  # 活公司入口：老板发话→唤醒项目经理本人自己处理
            self._活厅(data)
            return
        if self.path == "/room_say":
            room = str(data.get("room", "")).strip()
            if not room:
                self._json({"ok": False, "error": "缺项目室 id"})
                return
            self._活厅(data, 项目室=room)
            return
        if self.path == "/职级_调级":  # 船主手动调级：撤销/纠正自动升降职（可逆）
            try:
                import 职级
                _人 = str(data.get("人", "")).strip()
                if not _人:   # 3L4：空人名硬拒、别假成功（原来 设评级('') 空转不写盘、端点却回 ok:true）
                    self._json({"ok": False, "error": "缺人名"})
                    return
                r = 职级.船主调级(_人, int(data.get("级")), str(data.get("理由", "")).strip() or "船主手调")
                try:
                    import 看板数据
                    看板数据.清待办缓存()
                except Exception:  # noqa: BLE001
                    pass
                self._json({"ok": True, "变动": r})
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if self.path == "/信誉_误打回":  # 船主回看时判"这打回判错了"→给被冤者平反(作废那笔打回+连带失依据的反复敷衍)、罚乱打回的把关者。#12 接上触发入口（2026-07-11：此前只有函数没有任何调用点，船主够不着）
            try:
                import 信誉
                把关者 = str(data.get("把关者", "")).strip()
                被冤者 = str(data.get("被冤者", "")).strip()
                if not 把关者 or not 被冤者:
                    self._json({"ok": False, "error": "缺把关者/被冤者"})
                    return
                信誉.误打回(把关者, 被冤者, str(data.get("详情", "")).strip() or "船主回看判定打回有误", str(data.get("键", "")).strip())
                try:
                    import 看板数据
                    看板数据.清待办缓存()
                except Exception:  # noqa: BLE001
                    pass
                self._json({"ok": True})
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if self.path in ("/管理_确认", "/管理_取消", "/管理_重解"):  # 船主自然语言直控·确认卡三键（2026-07-11）
            try:
                import 管理动作
                卡 = str(data.get("卡", "")).strip()
                if self.path == "/管理_确认":
                    r = 管理动作.确认执行(卡)
                elif self.path == "/管理_取消":
                    r = 管理动作.取消(卡)
                else:
                    r = 管理动作.重解(卡, str(data.get("补充", "")).strip())
                self._json(r)
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if self.path == "/活厅_验收":  # 护栏丙②：批准/打回活公司产出（③v1：大厅留痕，PM不自动resume）
            self._活厅_验收(data)
            return
        if self.path == "/reject_decomp":
            # 旧工单拆解流程已归档；待确认现在应为空（新活动走 /活厅）
            self._json({"动作": "撤废", "reply": "旧拆解引擎已归档。如需重做，直接在大厅重新说一遍需求，项目经理本人会处理。"})
            return
        try:
            self._dispatch(data)
        except Exception as e:  # noqa: BLE001
            self._json({"reply": f"出错: {e}"})

    def _活厅(self, data: dict[str, Any], 项目室: str = "") -> None:
        """活公司入口（死平台/活人）：老板发话 → 点名岗位/当前承接人/项目经理责任席先听见，
        听见的人自己判断直接回答、查证、问同事、转交、开会、派活或请示，
        全过程（思考/工具/发言）进事件总线 → 前端 /stream 看（自主≠黑箱）。"""
        import 事件总线

        项目室 = str(项目室 or data.get("project_room") or "").strip()

        if _停止接活.is_set():
            self._json({"ok": False, "error": "办公室正在关门，已停止接新任务；等当前任务收尾后重开。"})
            return

        try:
            paths = 保存附件(data.get("attachments"), "room_" + _安全项目名(项目室) if 项目室 else "活厅")
        except ValueError as e:
            self._json({"ok": False, "error": str(e)})
            return
        原文 = str(data.get("text", ""))
        # @点名直呼（2026-07-03 船主问"其他人不能参与日常聊天吗"）：@老纪/@测试工程师 → 醒来的就是那个人本人。
        # 不带 @ 默认项目经理（统筹本职）。人人都是活人，船主可以直接找任何人说话（《公司的真谛》三续1）。
        目标岗位 = "项目经理"
        被点名 = False
        m = re.match(r"^@(\S+)\s*([\s\S]*)$", 原文.strip())
        if m:
            呼 = m.group(1)
            try:
                from 模型接入 import 花名册
                名册 = 花名册()
                命中 = None
                for 岗, cfg in 名册.items():
                    if 呼 == 岗 or 呼 == str(cfg.get("名字") or ""):
                        命中 = 岗
                        break
                if 命中 is None:
                    from 平台_工具集 import 归一岗位
                    g = 归一岗位(呼)
                    if g in 名册:
                        命中 = g
                if 命中:
                    目标岗位 = 命中
                    被点名 = True
                    原文 = m.group(2).strip() or 原文
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "已开始": False, "error": f"花名册无法确认点名对象，已停止：{type(e).__name__}: {e}"})
                return
            if not 命中:
                self._json({"ok": False, "已开始": False, "error": f"花名册中没有『{呼}』，没有改叫项目经理代接"})
                return
        房间键 = f"项目室:{项目室}" if 项目室 else "大厅"
        if not 被点名:
            import 大厅
            目标岗位 = 大厅.当前责任岗位(房间键)
        if os.environ.get("XJ_MOCK") != "1" and _大厅入口需预检(目标岗位, 被点名):
            try:
                from 模型接入 import 在岗
                在岗(目标岗位)
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "已开始": False, "error": f"{目标岗位}当前不能安全到岗：{type(e).__name__}: {e}"})
                return
        # 船主自然语言直控（2026-07-11）：非@的大厅发话，先看是不是"管理指令"（平反等）。
        # 词表只做"省一跳的快捷路"(船主令：词表不许当承重墙)——先粗筛命令味的词，命中才叫模型真判；模型说是、才建待确认卡拦下，不走派活。
        # 守得很紧：不命中/模型说不是 → 下面大厅流程一个字节都不变。确认门兜底：模型判错、船主不点确认就啥也不发生。
        if (not 被点名) and 原文.strip() and any(w in 原文 for w in ("平反", "判错", "冤枉", "错怪", "误打", "打错", "错打", "翻案", "清白")):
            try:
                import 管理动作
                _卡号 = 管理动作.从船主话建卡(原文.strip())
            except Exception:  # noqa: BLE001
                _卡号 = None
            if _卡号:
                try:
                    _卡 = 管理动作._读(管理动作._找(_卡号)) or {}
                except Exception:  # noqa: BLE001
                    _卡 = {}
                _缺 = _卡.get("缺参") or []
                _提示 = (
                    "我理解你要：" + _卡.get("释义", "") + "\n影响：" + _卡.get("影响", "")
                    + ("\n⚠️ 信息还不全（缺 " + "、".join(_缺) + "），点【说清楚点】补上再确认。" if _缺 else "")
                    + "\n\n右边确认卡：【确认执行】办 · 【说清楚点】纠正 · 【取消】作罢。"
                )
                try:
                    import 大厅记录
                    大厅记录.记一句("船主", 原文.strip())
                    大厅记录.记一句("系统", "【要你确认的管理动作】\n" + _提示)
                except Exception:  # noqa: BLE001
                    pass
                self._json({"reply": _提示, "管理动作卡": _卡号})
                return
        text = 带附件文本(原文, paths)
        if 项目室:
            try:
                import 协同
                项目状态 = 协同.项目状态摘要(项目室)
            except Exception:  # noqa: BLE001
                项目状态 = ""
            text = (
                f"## 当前从项目室转入同一活公司主线\n项目室：{项目室}\n"
                f"项目状态：\n{项目状态 or '（暂无）'}\n\n"
                f"项目室既往对话：\n{项目记忆文本(项目室)[-5000:] or '（暂无）'}\n\n"
                f"## {主人称呼()}现在说\n{text}"
            )
        try:
            sid = 事件总线.校验会话id(str(data.get("sid") or "").strip() or 事件总线.新会话id())
        except ValueError as e:
            self._json({"ok": False, "error": str(e)})
            return
        if not 事件总线.开始run(sid):
            self._json({"sid": sid, "已开始": False, "原因": "该会话已在进行中"})
            return
        try:
            import 任务台
            健康处理id = str(data.get("_健康处理id") or "").strip()
            任务台.创建(
                "健康告警处理" if 健康处理id else ("项目室续办" if 项目室 else "大厅任务"),
                "健康告警处理" if 健康处理id else "活厅",
                str(data.get("_船主显示") or 原文),
                task_id=sid, 负责人=目标岗位, 房间=(f"项目室:{项目室}" if 项目室 else "大厅"),
                父任务=健康处理id,
            )
        except Exception as e:  # noqa: BLE001 任务没先落账就绝不开工
            事件总线.结束run(sid)
            self._json({"sid": sid, "已开始": False, "原因": f"任务登记失败，未开工：{type(e).__name__}: {e}"})
            return

        import 大厅记录  # 大厅这个场所自己的对话持久层（船主圣域：场所内容忠实记录、永久留存）
        # 对话连续性（2026-07-02 船主实测抓的洞）：此前每次唤醒只递当前一句，PM 在同一场对话里失忆。
        # 把大厅最近的往来垫进来意——场所的痕迹就该喂给来场所的人（圣域⑫的另一半）。
        if not 项目室:
            近来往 = 大厅记录.当前会话(大厅记录.读对话())[-12:]
            if 近来往:
                上文 = "\n".join(f"{d.get('who','')}: {str(d.get('text',''))[:400]}" for d in 近来往)[-2600:]
                text = f"## 大厅最近对话（场所留痕，接着聊，别当新会话）\n{上文}\n\n## {主人称呼()}现在说\n{text}"
        原话 = str(data.get("text", ""))
        显示原话 = str(data.get("_船主显示") or 原话)
        大厅记录.记一句("船主", 显示原话 if not paths else 带附件文本(显示原话, paths))  # 内部交办不把整段系统说明冒充船主原话
        if 项目室:
            记录项目对话(项目室, "船主", 显示原话 if not paths else 带附件文本(显示原话, paths), 目标岗位)
        本场发言: list[tuple[str, str, str]] = []  # (人名, 岗位, 内容)——收尾后喂给记忆抽取（P2实时记忆）
        _已同步项目 = [False]

        def 同步项目痕迹() -> None:
            if not 项目室 or _已同步项目[0]:
                return
            _已同步项目[0] = True
            for 人, 岗, 内容 in 本场发言:
                记录项目对话(项目室, 人, 内容, 岗)

        _已抽 = [False]
        def 起记忆抽取() -> None:
            # 记忆P2：这场谁说了话，就给谁跑一次轻量抽取（后台线程，失败只落日志，不碰公司干活）。
            # 成功/急停/报错三条路都走到这，且只跑一次。
            if _已抽[0] or not 本场发言:
                return
            _已抽[0] = True
            对话文本 = f"{主人称呼()}: {显示原话}\n" + "\n".join(f"{n}: {c[:600]}" for n, _, c in 本场发言)
            发言者们 = [(n, g) for n, g, _ in 本场发言]
            _记忆任务id = f"记忆-{sid}"
            try:
                import 任务台
                任务台.创建("记忆抽取", "大厅收尾", 对话文本[:500], task_id=_记忆任务id, 父任务=sid)
            except Exception as e:  # noqa: BLE001
                事件总线.发布事件(sid, {"类型": "警告", "内容": f"记忆抽取登记失败，已停止抽取：{type(e).__name__}: {e}"})
                return
            def _抽() -> None:
                try:
                    import 任务台
                    任务台.更新(_记忆任务id, "运行中")
                    import 记忆抽取
                    记忆抽取.抽取一场(发言者们, 对话文本)
                    import 记忆巩固  # P4 睡前巩固：每人每天一次，当天第一场对话收尾后顺路跑
                    记忆巩固.巩固到期的人()
                    任务台.更新(_记忆任务id, "已完成", 结果="抽取与到期巩固已收尾")
                except Exception as e:  # noqa: BLE001
                    try:
                        import 任务台
                        任务台.更新(_记忆任务id, "失败", 错误=f"{type(e).__name__}: {e}")
                    finally:
                        事件总线.发布事件(sid, {"类型": "警告", "内容": f"记忆后台任务失败：{type(e).__name__}: {e}"})
            _启动工作线程(f"记忆抽取-{sid}", _抽)

        def 跑() -> None:
            import asyncio as _aio
            from 活_本人 import 唤醒本人, 聊天唤醒, 重置动作数

            重置动作数(sid)  # 费用闸：新任务清零该会话的动作计数
            try:
                import 任务台
                任务台.更新(sid, "运行中")
            except Exception as e:  # noqa: BLE001
                事件总线.发布事件(sid, {"类型": "错误", "内容": f"任务状态无法进入运行中：{type(e).__name__}: {e}"})
                事件总线.结束run(sid)
                return
            loop = _aio.new_event_loop()
            _aio.set_event_loop(loop)

            会里的人: set[str] = set()  # 本轮被拉进会议的人(参会岗位)——声音在会里+召集人报告里，别再在大厅队列重复说一遍(2026-07-09船主："补充该在会议室里完成、老钟再跟我说")

            def 进度(x: Any) -> None:
                if isinstance(x, dict) and x.get("类型") == "会议开始":
                    for 岗 in (x.get("参会岗位") or []):
                        会里的人.add(str(岗))   # 召集人不在参会里→他照常在大厅报告；参会者被挡在大厅队列外
                事件总线.发布事件(sid, x if isinstance(x, dict) else {"类型": "进展", "内容": x})
            import 大厅
            task = loop.create_task(大厅.活厅(
                原话, sid, 进度, 本场发言, 会里的人,
                目标岗位, 被点名, 原文, text, 房间键=房间键,
            ))
            with _活跃任务锁:
                _活跃任务[sid] = (loop, task)
            try:
                报告, 报告已即时落盘 = loop.run_until_complete(task)
                if 报告 and ("[沉默]" in 报告 or 报告 == "[转办事]"):
                    报告 = ""  # 哨兵/沉默都不是要落盘的发言（G-8）
                if 报告 and 报告 != "（没说话）" and not 报告已即时落盘:
                    try:
                        from 活_本人 import _岗位的人
                        _匹配发言 = next(
                            ((人, 岗) for 人, 岗, 内容 in reversed(本场发言) if 内容 == 报告),
                            None,
                        )
                        _说话人 = _匹配发言[0] if _匹配发言 else _岗位的人(目标岗位)
                        大厅记录.记一句(
                            _说话人,
                            报告,
                            事件键=大厅记录.发言事件键(sid, _说话人, 报告),
                        )  # 谁说的记谁的名（来的必须是本人）
                    except Exception:  # noqa: BLE001
                        大厅记录.记一句(
                            目标岗位,
                            报告,
                            事件键=大厅记录.发言事件键(sid, 目标岗位, 报告),
                        )
                if 报告 and 报告 != "（没说话）" and not 报告已即时落盘:
                    try:
                        from 活_本人 import _岗位的人 as _人于
                        本场发言.append((_人于(目标岗位), 目标岗位, 报告))
                    except Exception:  # noqa: BLE001
                        本场发言.append((目标岗位, 目标岗位, 报告))
                # 落款按真实办活人（2026-07-04制度审查第7条：曾写死"项目经理"，阿强办的活被标成经理的）
                _谁们 = "、".join(dict.fromkeys(人 for 人, _岗, _回 in 本场发言))
                事件总线.发布事件(sid, {"类型": "立项完成", "内容": (_谁们 + "处理完了。") if _谁们 else "这轮没人接话。", "result": {"报告": 报告}})
                任务台.更新(sid, "已完成", 结果=报告 or "这轮无人接话")
                同步项目痕迹()
                起记忆抽取()
            except _aio.CancelledError:
                事件总线.发布事件(sid, {"类型": "停止", "内容": "已停止。"})
                try:
                    任务台.更新(sid, "已停止", 结果="船主急停或关门取消")
                except Exception:  # noqa: BLE001
                    pass
                同步项目痕迹()
                起记忆抽取()  # 急停≠白说：已说完落盘的发言照样进记忆（2026-07-04体检：取消路径曾整场失忆）
            except Exception as e:  # noqa: BLE001
                事件总线.发布事件(sid, {"类型": "错误", "内容": "出错：" + str(e)})
                try:
                    任务台.更新(sid, "失败", 错误=f"{type(e).__name__}: {e}")
                except Exception:  # noqa: BLE001
                    pass
                同步项目痕迹()
                起记忆抽取()
            finally:
                with _活跃任务锁:
                    _活跃任务.pop(sid, None)
                try:
                    事件总线.结束run(sid)
                except Exception as e:  # noqa: BLE001
                    try:
                        任务台.更新(sid, "失败", 错误=f"run 结束事件持久化失败：{type(e).__name__}: {e}")
                    except Exception:  # noqa: BLE001
                        pass
                    print(f"[事件总线 {sid}] run 结束未能持久化：{type(e).__name__}: {e}", flush=True)
                loop.close()

        _启动工作线程(f"活厅-{sid}", 跑)
        self._json({"sid": sid, "已开始": True, "attachments": paths})

    def _活厅_验收(self, data: dict[str, Any]) -> None:
        """护栏丙②③：船主批准/打回活公司产出。
        批准 → 产出标「已批」，大厅留痕，算正式交付。
        打回 → 产出标「已打回」，大厅留痕（v1：PM不自动resume，船主再发一句触发重做）。
        """
        import 待验收记录
        rid = str(data.get("id", "")).strip()
        action = str(data.get("action", "批准")).strip()
        批注 = str(data.get("批注", "")).strip()
        if not rid:
            self._json({"ok": False, "error": "缺 id"})
            return
        try:
            ok = 待验收记录.批准(rid, 批注) if action == "批准" else 待验收记录.打回(rid, 批注)
        except Exception as e:  # noqa: BLE001
            self._json({"ok": False, "error": f"验收留痕失败：{type(e).__name__}: {e}"})
            return
        if not ok:
            self._json({"ok": False, "error": f"找不到待验收记录：{rid}"})
            return
        待副作用 = 待验收记录.副作用待处理(rid)
        try:
            import 看板数据
            看板数据.清待办缓存()   # 拍板后立即失效待办缓存，下次读就是新的
        except Exception:  # noqa: BLE001
            pass
        self._json({"ok": True, "action": action, "待重试副作用": 待副作用})

    def _dispatch(self, data: dict[str, Any]) -> None:
        # 旧执行引擎（升级_办公室适配/升级_圆桌/升级_执行/引擎/会议）已归档到 _历史_勿执行/。
        # 现在只有一套公司：活公司（/活厅 → 项目经理本人自主处理）。
        if self.path == "/chat":
            self._json({"reply": "旧岗位私聊接口已归档。请回大厅发话，项目经理本人会判断是否找对应同事。"})
        elif self.path == "/room_say":
            self._json({"ok": False, "error": "项目室新工作必须走 /活厅 主线"})
        elif self.path == "/say":
            self._json({"reply": "旧大厅说话接口已归档。请使用 /活厅；前端大厅发送已经走活公司。"})
        elif self.path == "/verdict":
            self._json({"reply": "旧工单裁决接口已归档。活公司产出请走 /活厅_验收。"})
        elif self.path == "/panic":  # 瞬时停止：cancel asyncio task，下一个 await 点立刻抛 CancelledError
            with _活跃任务锁:
                items = list(_活跃任务.items())
            for _sid, (loop, task) in items:
                loop.call_soon_threadsafe(task.cancel)
            self._json({"reply": "已停止。" if items else "当前没有在跑的任务。"})
        elif self.path in ("/confirm", "/resume", "/stop", "/append", "/kickoff"):
            self._json({"reply": "旧执行引擎已归档。新任务直接在大厅说，项目经理本人会处理。"})
        elif self.path == "/插话":
            text = str(data.get("text", "")).strip()
            sid = str(data.get("sid", "")).strip()
            import 事件总线
            if sid and 事件总线.在跑(sid):
                事件总线.发布插话(sid, {"text": text})
                try:
                    import 大厅记录
                    大厅记录.记一句("船主", text)  # 插话也是大厅里真实说过的话，忠实留痕（圣域⑫）
                except Exception:  # noqa: BLE001
                    pass
                self._json({"reply": "已插话进会场（实时）"})
            else:
                self._json({"reply": "当前无进行中的会话。"})
        elif self.path == "/memory_op":  # 记忆库后台操作：删行 / 教导候选转正 / 候选驳回
            from 活_本人 import 删某人一行, 改某人一行
            op = str(data.get("op", "")).strip()
            if op in ("图谱删", "图谱钉", "图谱改"):  # 船主图谱:删/钉住/亲改一条事实
                import 图记忆
                eid = int(data.get("id") or 0)
                if not eid:
                    self._json({"ok": False, "error": "缺id"})
                    return
                if op == "图谱改":
                    ok = bool(图记忆.改边(eid, str(data.get("新文", "")).strip()))
                elif op == "图谱删":
                    宾 = str(data.get("宾", "")).strip()  # 跨层级联：顺手清船主.md 里同一条事实的行
                    主 = ""
                    try:
                        _主, _宾 = 图记忆.边两端(eid)   # 删前取整条事实的主+宾，做整事实匹配（别单凭宾子串乱删）
                        主 = _主 or ""
                        if not 宾:
                            宾 = _宾 or ""
                    except Exception:  # noqa: BLE001
                        pass
                    ok = 图记忆.删边(eid)
                    if ok and 宾:
                        try:
                            import 船主记忆
                            船主记忆.删含(宾, 主)   # 整事实匹配：主非船主本人时要求主+宾都在行里才删
                        except Exception:  # noqa: BLE001
                            pass
                else:
                    ok = 图记忆.钉住(eid)
                self._json({"ok": bool(ok), "error": "" if ok else "没找到这条事实或新值为空"})
                return
            人 = str(data.get("人名", "")).strip()
            行 = str(data.get("行", "")).strip()
            if not (op and 人 and 行):
                self._json({"ok": False, "error": "缺参数"})
                return
            区 = str(data.get("区", "流水")).strip() or "流水"
            if op == "删行":  # 记忆库橡皮擦（叮嘱/记事等都可删）
                ok = 删某人一行(人, 区, 行)
                self._json({"ok": ok, "error": "" if ok else "没找到这一行"})
            elif op == "改行":  # 记忆库可改（叮嘱不满意自己改）
                ok = 改某人一行(人, 区, 行, str(data.get("新文", "")).strip())
                self._json({"ok": ok, "error": "" if ok else "没找到这一行或新文为空"})
            else:
                self._json({"ok": False, "error": "未知操作"})
        elif self.path == "/approval_decide":
            try:   # M12 二审：出错回 ok:false（原来出错也走通用200、前端弹绿色成功）；船主裁决内部已清缓存(M11)
                import 审批
                card = 审批.船主裁决(str(data.get("id", "")), str(data.get("v", "")), str(data.get("reason", "")))
                self._json({"ok": True, "reply": "已裁决", "card": card})
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": f"裁决失败（多半这条已被处理过）：{type(e).__name__}: {e}"})
        elif self.path == "/meeting_say":
            import 协同
            ev = 协同.船主发言(str(data["id"]), str(data.get("text", "")))
            self._json({"reply": "已写入会议", "event": ev})
        else:
            self._json({"reply": "未知接口"})

    def log_message(self, *a: object) -> None:
        pass


if __name__ == "__main__":
    _设置告警宿主边界()
    # F9 二审：先绑端口＝单实例锁——第二个机房进程会因端口占用崩在这、绝不会和本进程同时动 jsonl（归档轮转在下面、绑成功后才跑）。
    try:
        _srv = http.server.ThreadingHTTPServer(("0.0.0.0" if 是远程实例() else "127.0.0.1", PORT), H)
    except OSError as e:
        print(f"❌ 端口 {PORT} 被占用（已有一个办公室在跑？）：{e}——不重复开、不动 jsonl，退出。")
        raise SystemExit(1) from e
    # 清掉上次残留的急停旗（瞬时键，不应跨重启赖着）
    try:
        from 引擎工具 import 急停文件
        急停文件.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    try:   # 开门前自检部门、花名册和房间契约，坏了大声喊出来
        import 部门
        import 房间注册
        问题 = 部门.校验() + 房间注册.校验()
        if 问题:
            print("⚠️ 部门/花名册/房间契约自检发现问题：")
            for p in 问题:
                print(f"   · {p}")
        else:
            print("✅ 部门/花名册/房间契约自检通过")
    except Exception as e:  # noqa: BLE001 自检本身崩了也不许挡开门
        print(f"（部门配置自检跳过：{type(e).__name__}: {e}）")
    try:   # 开门前把会无限长的 jsonl 轮转归档一次（旧段搬进 归档/，仍备份可回放）
        搬 = _执行日常轮转()
        if 搬:
            print(f"📦 jsonl 归档轮转：{搬}")
    except Exception as e:  # noqa: BLE001 轮转崩了也不许挡开门
        print(f"（jsonl 归档轮转跳过：{type(e).__name__}: {e}）")
    if os.environ.get("XJ_MOCK") != "1":
        _启动时补刷点名()
    try:
        import 任务台
        _中断 = 任务台.恢复中断()
        if _中断:
            print(f"⚠️ 上次重启前有 {len(_中断)} 个任务没正常收尾，已标为『被重启中断』，未自动重跑。")
    except Exception as e:  # noqa: BLE001
        print(f"❌ 任务台恢复失败：{type(e).__name__}: {e}")
    try:
        import 管理动作
        _动作恢复 = 管理动作.恢复执行中()
        if _动作恢复:
            _成功 = sum(1 for x in _动作恢复 if x.get("ok"))
            _失败 = len(_动作恢复) - _成功
            print(f"🔁 管理动作恢复：完成 {_成功} 张，仍待重试 {_失败} 张。")
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 管理动作恢复失败：{type(e).__name__}: {e}")
    try:
        import 待验收记录
        _重试数 = 0
        for _r in 待验收记录.列表(仅待验收=False):
            if _r.get("状态") in ("已批", "已打回") and 待验收记录.副作用待处理(str(_r.get("id") or "")):
                待验收记录.处理副作用(str(_r.get("id") or ""))
                _重试数 += 1
        if _重试数:
            print(f"🔁 已重试 {_重试数} 条验收裁决的未完成副作用。")
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 验收副作用恢复失败：{type(e).__name__}: {e}")

    # 告警表达模型属于宿主服务。正式 Linux 容器开门时只保留规则版，
    # 第一次真正访问健康页再经内部桥接请求宿主，避免每个账号启动就排队。
    if os.environ.get("XJ_ALERT_RENDER_REQUIRED") == "1":
        print("✅ 告警表达已切到宿主桥接；本容器不开 MLX，宿主不可用时显示规则版。", flush=True)

    if os.environ.get("XJ_MOCK") != "1":
        try:
            from 本地精排 import 完整性 as _精排完整性

            _精排完整, _精排说明 = _精排完整性()
            if _精排完整:
                print(f"✅ 本地精排文件已核对，首次搜索时按需启动：{_精排说明}", flush=True)
            else:
                print(f"⚠️ 本地精排文件不完整，搜索会自动使用基础排序：{_精排说明}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ 本地精排轻量检查失败，搜索仍会自动降级：{type(e).__name__}: {e}", flush=True)

    def _关门(_signum, _frame) -> None:
        if _停止接活.is_set():
            return
        _停止接活.set()
        print("\n办公室正在关门：停止接新任务，取消当前模型任务并等待线程收尾……", flush=True)
        with _活跃任务锁:
            items = list(_活跃任务.items())
        for _sid, (loop, task) in items:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=_srv.shutdown, name="办公室关门", daemon=True).start()

    signal.signal(signal.SIGTERM, _关门)
    signal.signal(signal.SIGINT, _关门)
    _启动工作线程("日常容量维护", _日常维护循环)
    print(f"办公室已开: http://localhost:{PORT}  (Ctrl+C退出)")
    try:
        _srv.serve_forever()
    finally:
        _srv.server_close()
        while True:
            with _工作线程锁:
                活 = [t for t in _工作线程.values() if t.is_alive()]
            if not 活:
                break
            print(f"等待 {len(活)} 个后台任务收尾……", flush=True)
            for t in 活:
                t.join(timeout=1.0)
        try:
            import 任务台
            任务台.中断运行中("办公室已关闭，但任务没有写出终态；已标记中断，未自动重跑。")
        except Exception:  # noqa: BLE001
            pass
