#!/usr/bin/env python3
"""跨窗口记忆 · 大厅全文检索。

抄 Claude conversation_search / recent_chats 的机制：把历史大厅痕迹建成本地索引，
本人记不清时先翻档案再开口。实现自己重写；只读真实档案，不改原始对话。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

import 大厅记录
import 大厅档案

from 根 import 数据根
from 实例配置 import 主人别名

COMPANY = 数据根
对话目录 = 大厅记录.对话目录
索引文件 = COMPANY / "运行状态" / "大厅索引.db"
from 资料室 import 会话摘要文件 as 摘要文件   # 会话摘要进资料室
强制禁trigram = False


def _截(s: str, n: int = 360) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _连() -> sqlite3.Connection:
    索引文件.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(索引文件)
    con.row_factory = sqlite3.Row
    return con


def _探测trigram(con: sqlite3.Connection, 强制: bool = False) -> bool:
    if 强制 or 强制禁trigram or os.environ.get("XJ_FORCE_NO_TRIGRAM") == "1":
        return False
    try:
        con.execute("CREATE VIRTUAL TABLE temp._fts_probe USING fts5(text, tokenize='trigram')")
        con.execute("DROP TABLE temp._fts_probe")
        return True
    except sqlite3.Error:
        return False


def _建表(con: sqlite3.Connection, mode: str) -> None:
    con.execute("CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT)")
    旧 = con.execute("SELECT v FROM settings WHERE k='tokenizer'").fetchone()
    if 旧 and 旧["v"] != mode:
        con.execute("DROP TABLE IF EXISTS fts")
        con.execute("DROP TABLE IF EXISTS messages")
        con.execute("DROP TABLE IF EXISTS files")
        con.execute("DELETE FROM settings WHERE k='tokenizer'")
    con.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL NOT NULL)")
    con.execute(
        "CREATE TABLE IF NOT EXISTS messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, rowno INTEGER NOT NULL, "
        "day TEXT NOT NULL, time TEXT, who TEXT, text TEXT NOT NULL)"
    )
    tok = "trigram" if mode == "trigram" else "unicode61"
    con.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(text, tokenize='{tok}')")
    con.execute("INSERT OR REPLACE INTO settings(k, v) VALUES('tokenizer', ?)", (mode,))
    con.commit()


def _读文件(p: Path) -> list[tuple[int, str, str, str, str]]:
    out: list[tuple[int, str, str, str, str]] = []
    day = 大厅档案.日期(p)
    for i, ln in enumerate(大厅档案.读文本(p).splitlines(), 1):
        try:
            d = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        text = str(d.get("text") or "").strip()
        who = str(d.get("who") or "").strip()
        ts = str(d.get("时间") or "").strip()
        if text:
            out.append((i, day, ts, who, text))
    return out


def _重索引文件(con: sqlite3.Connection, p: Path) -> None:
    path = str(p)
    ids = [r["id"] for r in con.execute("SELECT id FROM messages WHERE path=?", (path,)).fetchall()]
    for rid in ids:
        con.execute("DELETE FROM fts WHERE rowid=?", (rid,))
    con.execute("DELETE FROM messages WHERE path=?", (path,))
    for rowno, day, ts, who, text in _读文件(p):
        cur = con.execute(
            "INSERT INTO messages(path, rowno, day, time, who, text) VALUES(?,?,?,?,?,?)",
            (path, rowno, day, ts, who, text),
        )
        con.execute("INSERT INTO fts(rowid, text) VALUES(?, ?)", (cur.lastrowid, text))
    con.execute("INSERT OR REPLACE INTO files(path, mtime) VALUES(?, ?)", (path, p.stat().st_mtime))


def 更新索引(*, 强制禁trigram参数: bool = False) -> str:
    """增量更新大厅索引；返回当前 tokenizer 模式：trigram 或 unicode61。"""
    with _连() as con:
        mode = "trigram" if _探测trigram(con, 强制禁trigram参数) else "unicode61"
        _建表(con, mode)
        paths = 大厅档案.各日文件(对话目录)
        当前 = {str(p) for p in paths}
        for row in con.execute("SELECT path FROM files").fetchall():
            if row["path"] not in 当前:
                ids = [r["id"] for r in con.execute("SELECT id FROM messages WHERE path=?", (row["path"],)).fetchall()]
                for rid in ids:
                    con.execute("DELETE FROM fts WHERE rowid=?", (rid,))
                con.execute("DELETE FROM messages WHERE path=?", (row["path"],))
                con.execute("DELETE FROM files WHERE path=?", (row["path"],))
        for p in paths:
            mt = p.stat().st_mtime
            old = con.execute("SELECT mtime FROM files WHERE path=?", (str(p),)).fetchone()
            if not old or float(old["mtime"]) != mt:
                _重索引文件(con, p)
        con.commit()
        return mode


def _上下文(con: sqlite3.Connection, row: sqlite3.Row) -> str:
    rows = con.execute(
        "SELECT who, text FROM messages WHERE path=? AND rowno BETWEEN ? AND ? ORDER BY rowno",
        (row["path"], max(1, int(row["rowno"]) - 2), int(row["rowno"]) + 2),
    ).fetchall()
    return "\n".join(f"{r['who']}: {_截(r['text'], 180)}" for r in rows if r["text"])


def _命中片段(text: str, query: str) -> str:
    text = text or ""
    i = text.lower().find((query or "").lower())
    if i < 0:
        return _截(text, 180)
    a = max(0, i - 60)
    b = min(len(text), i + len(query) + 90)
    return ("…" if a else "") + text[a:b] + ("…" if b < len(text) else "")


def 搜历史(query: str, 条数: int = 5, *, 强制禁trigram参数: bool = False) -> list[dict[str, str]]:
    """搜索大厅历史，返回 [{时间, 谁, 命中, 前后文}]。"""
    query = (query or "").strip()
    if not query:
        return []
    条数 = max(1, min(int(条数 or 5), 20))
    mode = 更新索引(强制禁trigram参数=强制禁trigram参数)
    with _连() as con:
        if mode == "trigram":
            try:
                rows = con.execute(
                    "SELECT m.* FROM fts JOIN messages m ON m.id=fts.rowid "
                    "WHERE fts MATCH ? ORDER BY coalesce(m.time, m.day) DESC LIMIT ?",
                    (query, 条数),
                ).fetchall()
            except sqlite3.Error:
                rows = []
            if not rows:
                # trigram 分词要求≥3字符——而我们的关键词天然是两字的（人名/婚礼/验收）。
                # 短词或零命中一律落 LIKE 兜底（2026-07-03 验收抓的虫：搜"老纪"0条）。
                rows = con.execute(
                    "SELECT * FROM messages WHERE text LIKE ? ORDER BY coalesce(time, day) DESC LIMIT ?",
                    (f"%{query}%", 条数),
                ).fetchall()
        else:
            like = f"%{query}%"
            rows = con.execute(
                "SELECT * FROM messages WHERE text LIKE ? ORDER BY coalesce(time, day) DESC LIMIT ?",
                (like, 条数),
            ).fetchall()
        out: list[dict[str, str]] = []
        seen: set[int] = set()
        if mode != "trigram":
            # unicode61 对中文切词不稳定，LIKE 是中文可用的兜底；ASCII 词再补一次 FTS。
            try:
                for r in con.execute(
                    "SELECT m.* FROM fts JOIN messages m ON m.id=fts.rowid WHERE fts MATCH ? "
                    "ORDER BY coalesce(m.time, m.day) DESC LIMIT ?",
                    (query, 条数),
                ).fetchall():
                    if r["id"] not in seen:
                        rows.append(r)
            except sqlite3.Error:
                pass
        for r in rows:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            out.append({
                "时间": str(r["time"] or r["day"]),
                "谁": str(r["who"] or ""),
                "命中": _命中片段(str(r["text"] or ""), query),
                "前后文": _上下文(con, r),
            })
            if len(out) >= 条数:
                break
        return out


def 摘要按日() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    if not 摘要文件.exists():
        return out
    for ln in 摘要文件.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if len(ln) < 5 or not ln[:4].isdigit():
            continue
        out.setdefault(ln[:4], []).append(ln)
    return out


_场次缓存: dict[str, Any] = {"签名": None, "数据": []}


def _临时场次摘要(行们: list[tuple[int, str, str, str, str]]) -> str:
    """未完成每日蒸馏时，先从真实原话给场次一个可辨认的名字；不调用模型、不编造。"""
    if not 行们:
        return "当天没有可读对话"
    主人名们 = set(主人别名())
    船主原话 = [row for row in 行们 if row[3] in 主人名们]
    依据 = 船主原话[0] if 船主原话 else 行们[0]
    原文 = " ".join(依据[4].split())
    句子 = [x.strip() for x in re.split(r"[。！？!?]", 原文) if x.strip()]
    文本 = 句子[0] if 句子 else 原文
    if 文本.startswith("这是"):
        文本 = 文本[2:]
    文本 = _截(文本, 64)
    if len(行们) == 1:
        return 文本
    发言人 = []
    for row in 行们:
        who = row[3].replace("（工程师）", "").strip()
        if who and who not in 发言人 and who not in 主人名们:
            发言人.append(who)
    尾 = f"；{len(行们)} 条记录"
    if 发言人:
        尾 += "，" + "、".join(发言人[:3]) + "参与"
    return 文本 + 尾


def 场次索引(上限: int = 80) -> list[dict[str, Any]]:
    """给大厅左侧场次栏的轻量索引：日期、摘要、真实记录数与最后时间。"""
    paths = 大厅档案.各日文件(对话目录)
    summary_mtime = 摘要文件.stat().st_mtime_ns if 摘要文件.exists() else 0
    签名 = (summary_mtime, tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths))
    if _场次缓存["签名"] == 签名:
        return list(_场次缓存["数据"])[-上限:]

    摘要 = 摘要按日()
    out: list[dict[str, Any]] = []
    for p in paths:
        day = 大厅档案.日期(p)
        行们 = _读文件(p)
        mmdd = day[5:7] + day[8:10]
        整理行 = 摘要.get(mmdd, [])
        正式摘要 = _截(" ".join(line[5:].strip() for line in 整理行 if line[5:].strip()), 180)
        out.append({
            "日期": day,
            "摘要": 正式摘要 or _临时场次摘要(行们),
            "记录数": len(行们),
            "最后时间": 行们[-1][2] if 行们 else "",
            "已整理": bool(正式摘要),
        })
    _场次缓存.update({"签名": 签名, "数据": out})
    return list(out)[-上限:]


def 翻近期(天数: int = 3) -> list[dict[str, Any]]:
    """返回最近N天的摘要行 + 每天最后3句原文。"""
    天数 = max(1, min(int(天数 or 3), 14))
    摘要 = 摘要按日()
    if 对话目录.exists():
        days = 大厅档案.日期列表(对话目录)[-天数:]
    else:
        today = _dt.date.today()
        days = [(today - _dt.timedelta(days=i)).isoformat() for i in reversed(range(天数))]
    out: list[dict[str, Any]] = []
    for day in days:
        p = 大厅档案.找日(day, 对话目录)
        开头: list[dict[str, str]] = []
        原文: list[dict[str, str]] = []
        if p is not None:
            行们 = _读文件(p)
            for _, _, ts, who, text in 行们[:3]:  # "第一句是什么"这类按位置的问题靠它（验收补的）
                开头.append({"时间": ts, "谁": who, "话": _截(text, 220)})
            for _, _, ts, who, text in 行们[-3:]:
                原文.append({"时间": ts, "谁": who, "话": _截(text, 220)})
        mmdd = day[5:7] + day[8:10]
        out.append({"日期": day, "摘要": 摘要.get(mmdd, []), "开头3句": 开头, "最后3句": 原文})
    return out
