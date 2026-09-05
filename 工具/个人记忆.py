#!/usr/bin/env python3
"""员工个人记忆的唯一读写层。

记忆跟人走，不跟岗位走。这里只负责四区文件、并发写保护和注入摘要；
聊天、派活和模型调度留在 `活_本人.py`，避免改记忆时牵动整套运行骨架。
"""
from __future__ import annotations

import datetime as dt
import re
import threading
from pathlib import Path

from 根 import 数据根
from 实例配置 import 主人称呼

COMPANY = 数据根

记忆区序 = ("原则", "船主教导", "近况", "流水")
_记忆写锁 = threading.Lock()
_区注入上限 = {"原则": 1200, "船主教导": 1500, "近况": 800, "流水": 1400}
_流水注入条数 = 12


def _限字(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: max(0, n - 1)].rstrip() + "…"


def _对老板称呼(s: str) -> str:
    return (s or "").replace("船主", 主人称呼())


def _记忆文件(人名: str):
    import 部门

    return 部门.个人记忆路径(人名)


def _记忆模板(人名: str) -> str:
    return f"# {人名}的记忆\n\n## 原则\n\n## 船主教导\n\n## 近况\n\n## 流水\n"


def _读记忆区(人名: str) -> dict[str, str]:
    """按四区解析个人记忆；无区头的旧内容归入流水。"""
    p = _记忆文件(人名)
    out = {name: "" for name in 记忆区序}
    if not p.exists():
        return out
    current = None
    loose: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            name = line[3:].strip()
            current = name if name in out else None
            continue
        if line.startswith("# "):
            continue
        if current:
            out[current] += re.sub(r"\s*<!--事件:[^>]+-->\s*$", "", line) + "\n"
        elif line.strip():
            loose.append(line)
    if loose:
        out["流水"] = "\n".join(loose) + "\n" + out["流水"]
    return out


def 给某人记一笔(人名: str, 经验: str, 区: str = "流水", 发生日期: str | None = None, 键: str = "") -> None:
    """追加一条个人记忆；同一事件键只写一次。"""
    经验 = (经验 or "").strip()
    if not 经验 or not str(人名 or "").strip():
        return
    if 经验.startswith("[船主教导]"):
        区 = "船主教导"
        经验 = 经验[len("[船主教导]"):].strip()
    if 区 not in 记忆区序:
        区 = "流水"
    p = _记忆文件(人名)
    p.parent.mkdir(parents=True, exist_ok=True)
    timestamp = (发生日期 or "").strip() or dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    key = str(键 or "").strip()
    marker = f" <!--事件:{key}-->" if key else ""
    row = f"- [{timestamp}] {经验}{marker}"
    with _记忆写锁:
        if not p.exists():
            p.write_text(_记忆模板(人名), encoding="utf-8")
        old = p.read_text(encoding="utf-8")
        if key and f"<!--事件:{key}-->" in old:
            return
        lines = old.split("\n")
        out: list[str] = []
        in_section = False
        inserted = False
        for line in lines:
            if line.startswith("## "):
                if in_section and not inserted:
                    while out and not out[-1].strip():
                        out.pop()
                    out.extend([row, ""])
                    inserted = True
                in_section = line[3:].strip() == 区
            out.append(line)
        if in_section and not inserted:
            while out and not out[-1].strip():
                out.pop()
            out.append(row)
            inserted = True
        if not inserted:
            out.extend([f"## {区}", row])
        p.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def 重写某人区(人名: str, 区: str, 全文: str) -> None:
    """重写原则、教导或近况；流水只增不改。"""
    if 区 not in 记忆区序 or 区 == "流水":
        return
    if not (全文 or "").strip():
        try:
            log = COMPANY / "记忆库" / "抽取日志.md"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as handle:
                handle.write(f"- [{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}] ⚠️ {人名}·{区} 收到空写入，已跳过不清段(断崖保险)\n")
        except Exception:  # noqa: BLE001
            pass
        return
    p = _记忆文件(人名)
    with _记忆写锁:
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_记忆模板(人名), encoding="utf-8")
        try:
            (p.parent / f".{p.stem}.bak").write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        sections = _读记忆区(人名)
        sections[区] = 全文.strip() + "\n"
        out = [f"# {人名}的记忆", ""]
        for name in 记忆区序:
            out.append(f"## {name}")
            body = sections[name].strip()
            if body:
                out.append(body)
            out.append("")
        p.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def 删某人一行(人名: str, 区: str, 行: str) -> bool:
    p = _记忆文件(人名)
    if not p.exists() or 区 not in 记忆区序:
        return False
    wanted = 行.strip()
    with _记忆写锁:
        out: list[str] = []
        in_section = False
        removed = False
        for line in p.read_text(encoding="utf-8").split("\n"):
            if line.startswith("## "):
                in_section = line[3:].strip() == 区
            if in_section and not removed and line.strip() == wanted:
                removed = True
                continue
            out.append(line)
        if removed:
            p.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    return removed


def 改某人一行(人名: str, 区: str, 旧行: str, 新文: str) -> bool:
    p = _记忆文件(人名)
    replacement = (新文 or "").strip()
    if not p.exists() or 区 not in 记忆区序 or not replacement:
        return False
    wanted = 旧行.strip()
    match = re.match(r"^-\s*(?:\[[^\]]*\]\s*)?", wanted)
    new_line = f"{match.group(0) if match else '- '}{replacement}"
    with _记忆写锁:
        out: list[str] = []
        in_section = False
        changed = False
        for line in p.read_text(encoding="utf-8").split("\n"):
            if line.startswith("## "):
                in_section = line[3:].strip() == 区
            if in_section and not changed and line.strip() == wanted:
                out.append(new_line)
                changed = True
                continue
            out.append(line)
        if changed:
            p.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    return changed


def 读其人记忆(人名: str) -> str:
    sections = _读记忆区(人名)
    if not any(value.strip() for value in sections.values()):
        return "（还没攒下什么经验，第一次干这行）"
    blocks: list[str] = []
    for name in ("原则", "船主教导", "近况"):
        body = sections[name].strip()
        if body:
            limit = _区注入上限[name]
            blocks.append(f"【{name}】\n{body[-limit:] if len(body) > limit else body}")
    rows = [line for line in sections["流水"].splitlines() if line.strip()]
    if rows:
        body = "\n".join(rows[-_流水注入条数:])
        body = body[-_区注入上限["流水"]:]
        rel = _记忆文件(人名).relative_to(COMPANY)
        blocks.append(f"【最近流水】（更早的在你的记忆库文件里，需要具体细节可用 Read/Grep 去翻 {rel} 或大厅痕迹簿）\n{body}")
    return "\n\n".join(blocks)


def _聊天记忆精华(人名: str, 上限: int = 160) -> str:
    sections = _读记忆区(人名)
    pieces = []
    for name in ("原则", "船主教导", "近况"):
        body = " ".join(sections[name].split())
        if body:
            pieces.append(f"{name}:{body}")
    return _对老板称呼(_限字("；".join(pieces), 上限)) or "暂无。"


def 读近期公司摘要(行数: int = 30) -> str:
    import 资料室

    p = 资料室.会话摘要文件
    if not p.exists():
        return ""
    rows = [line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        return ""
    return "# 近期公司里聊过的事（一行一天，是场所的共同记忆——接得上就接着聊，别当新会话）\n" + "\n".join(rows[-行数:])


def _近期公司一句(上限: int = 90) -> str:
    text = 读近期公司摘要(行数=5)
    if not text:
        return "暂无。"
    heading = "# 近期公司里聊过的事（一行一天，是场所的共同记忆——接得上就接着聊，别当新会话）"
    return _对老板称呼(_限字(text.replace(heading, ""), 上限))
