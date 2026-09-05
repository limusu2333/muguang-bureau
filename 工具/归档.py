#!/usr/bin/env python3
"""死平台 · 归档轮转 —— 会无限长的 jsonl 定期把旧段搬进 归档/、留近段。机房启动时跑一次。

- 信誉账：按『年龄』轮转（旧事件已衰减到≈0，搬走不影响信誉分）。
- 纯日志（职级变动）：按『行数』轮转。
- 大厅对话（按天分文件）：把很旧的整天文件搬进归档。
归档文件仍在盘上、被备份脚本带走、可回放；只是不再被现役读取路径吃进内存。
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import os
import sqlite3
from pathlib import Path

from 根 import 数据根

COMPANY = 数据根


def _归档写(归档目录: Path, 名: str, 行: list[str]) -> None:
    归档目录.mkdir(parents=True, exist_ok=True)
    with (归档目录 / 名).open("a", encoding="utf-8") as f:
        f.write("\n".join(行) + "\n")


def 归档轮转(文件: Path, 保留行数: int) -> int:
    """jsonl 超过 保留行数 就把最旧的搬进 <父>/归档/<同名>，只留最近 保留行数 行。返回搬走几行。"""
    if not 文件.exists():
        return 0
    行 = [l for l in 文件.read_text(encoding="utf-8").splitlines() if l.strip()]
    if len(行) <= 保留行数:
        return 0
    搬, 留 = 行[:-保留行数], 行[-保留行数:]
    _归档写(文件.parent / "归档", 文件.name, 搬)
    tmp = 文件.with_suffix(文件.suffix + ".tmp")
    tmp.write_text("\n".join(留) + "\n", encoding="utf-8")
    tmp.replace(文件)
    return len(搬)


def 归档旧事件(文件: Path, 保留天: int = 90) -> int:
    """按年龄轮转（信誉账用）：把『时间』早于 保留天 的事件搬进归档、留近段。
    旧事件按半衰期早已衰减到≈0，搬走不影响信誉分。时间坏的行留着不误搬。返回搬走几条。"""
    if not 文件.exists():
        return 0
    界 = dt.datetime.now() - dt.timedelta(days=保留天)
    行 = [l for l in 文件.read_text(encoding="utf-8").splitlines() if l.strip()]
    搬, 留 = [], []
    for l in 行:
        旧 = False
        try:
            t = dt.datetime.strptime(str(json.loads(l).get("时间") or "")[:16], "%Y-%m-%d %H:%M")
            旧 = t < 界
        except Exception:  # noqa: BLE001 时间坏就留着，别误搬
            旧 = False
        (搬 if 旧 else 留).append(l)
    if not 搬:
        return 0
    _归档写(文件.parent / "归档", 文件.name, 搬)
    tmp = 文件.with_suffix(文件.suffix + ".tmp")
    tmp.write_text(("\n".join(留) + "\n") if 留 else "", encoding="utf-8")
    tmp.replace(文件)
    return len(搬)


def 归档会话事件(保留天: int = 30) -> int:
    """压缩已正常结束且超过保留期的会话。无 `run结束` 的残缺会话保留在现役目录等待排查。"""
    root = COMPANY / "运行状态" / "会话事件"
    if not root.exists():
        return 0
    cutoff = dt.datetime.now().timestamp() - max(1, int(保留天)) * 86400
    moved = 0
    for src in sorted(root.glob("*.jsonl")):
        if src.stat().st_mtime >= cutoff:
            continue
        lines = [line for line in src.read_text(encoding="utf-8").splitlines() if line.strip()]
        ended = False
        for line in reversed(lines[-20:]):
            try:
                if json.loads(line).get("类型") == "run结束":
                    ended = True
                    break
            except Exception:  # noqa: BLE001
                continue
        if not ended:
            continue
        month = dt.datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m")
        dst = root / "归档" / month / f"{src.name}.gz"
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        original = src.read_bytes()
        with gzip.open(tmp, "wb") as out:
            out.write(original)
        with gzip.open(tmp, "rb") as check:
            restored = check.read()
        if hashlib.sha256(restored).digest() != hashlib.sha256(original).digest():
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"会话归档校验失败：{src.name}")
        os.replace(tmp, dst)
        src.unlink()
        moved += 1
    return moved


def _压缩单文件(src: Path, dst: Path) -> None:
    """把单个文件压成 gzip，回读哈希一致后才删除原件。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    original = src.read_bytes()
    with gzip.open(tmp, "wb") as out:
        out.write(original)
    with gzip.open(tmp, "rb") as check:
        restored = check.read()
    if hashlib.sha256(restored).digest() != hashlib.sha256(original).digest():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"归档校验失败：{src}")
    os.replace(tmp, dst)
    src.unlink()


def 归档任务记录(保留天: int = 90) -> int:
    """只压缩超过保留期的终态任务；运行中和损坏记录一律留在现役目录。"""
    root = COMPANY / "运行状态" / "任务"
    if not root.exists():
        return 0
    cutoff = dt.datetime.now() - dt.timedelta(days=max(1, int(保留天)))
    terminal = {"已完成", "失败", "已停止", "被重启中断", "被门禁拦住"}
    moved = 0
    for src in sorted(root.glob("*.json")):
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
            changed = dt.datetime.fromisoformat(str(data.get("更新时间") or ""))
        except Exception:  # noqa: BLE001 损坏记录留给健康页报，绝不误搬
            continue
        if str(data.get("状态") or "") not in terminal or changed >= cutoff:
            continue
        month = changed.strftime("%Y-%m")
        _压缩单文件(src, root / "归档" / month / f"{src.name}.gz")
        moved += 1
    return moved


def 归档日记录(root: Path, 保留天: int = 90) -> int:
    """压缩按 YYYY-MM-DD.jsonl 命名的旧场所记录；非日期文件和坏日期不动。"""
    if not root.exists():
        return 0
    cutoff = dt.date.today() - dt.timedelta(days=max(1, int(保留天)))
    moved = 0
    for src in sorted(root.glob("*.jsonl")):
        try:
            day = dt.date.fromisoformat(src.stem)
        except ValueError:
            continue
        if day >= cutoff:
            continue
        _压缩单文件(src, root / "归档" / day.strftime("%Y-%m") / f"{src.name}.gz")
        moved += 1
    return moved


def 归档旧日志(保留天: int = 30) -> int:
    """压缩旧后端日志；当天仍在写的日志不会碰，归档后仍可按月回查。"""
    root = COMPANY / "运行状态" / "as_log"
    if not root.exists():
        return 0
    cutoff = dt.datetime.now().timestamp() - max(1, int(保留天)) * 86400
    moved = 0
    for src in sorted(root.glob("backend.*.log")):
        if src.stat().st_mtime >= cutoff:
            continue
        month = dt.datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m")
        _压缩单文件(src, root / "归档" / month / f"{src.name}.gz")
        moved += 1
    return moved


def 维护统一搜索索引() -> dict[str, int]:
    """收掉 WAL 并在空页超过四分之一时压实索引，避免长期更新只涨不回落。"""
    path = COMPANY / "运行状态" / "统一搜索.db"
    if not path.exists():
        return {"before": 0, "after": 0, "vacuumed": 0}
    before = path.stat().st_size
    con = sqlite3.connect(path, timeout=30)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        page_count = int(con.execute("PRAGMA page_count").fetchone()[0])
        free = int(con.execute("PRAGMA freelist_count").fetchone()[0])
        vacuumed = int(page_count >= 200 and free / max(1, page_count) >= 0.25)
        if vacuumed:
            con.execute("VACUUM")
    finally:
        con.close()
    return {"before": before, "after": path.stat().st_size, "vacuumed": vacuumed}


def 日常轮转() -> dict:
    """机房启动跑一次：把几处会无限长的 jsonl 轮转归档。返回 {文件: 搬走数}。某个失败不连累别的。"""
    结果: dict = {}
    信誉目录 = COMPANY / "信誉"
    if 信誉目录.exists():
        for p in 信誉目录.glob("*.jsonl"):
            try:
                n = 归档旧事件(p, 90)   # 信誉按年龄（旧的衰减到≈0）
                if n:
                    结果[f"信誉/{p.name}"] = n
            except Exception as e:  # noqa: BLE001
                结果[f"信誉/{p.name}故障"] = f"{type(e).__name__}: {e}"
    try:
        n = 归档轮转(COMPANY / "职级" / "变动.jsonl", 500)   # 纯日志按行数
        if n:
            结果["职级/变动.jsonl"] = n
    except Exception as e:  # noqa: BLE001
        结果["职级/变动.jsonl故障"] = f"{type(e).__name__}: {e}"
    对话 = COMPANY / "大厅" / "对话"
    if 对话.exists():
        界 = (dt.date.today() - dt.timedelta(days=30)).isoformat()
        for p in sorted(对话.glob("*.jsonl")):
            if p.stem < 界:   # 30天前的整天文件搬进归档
                try:
                    归 = 对话 / "归档" / p.stem[:7] / f"{p.name}.gz"
                    _压缩单文件(p, 归)
                    结果[f"大厅对话/{p.name}"] = "整天压缩归档"
                except Exception as e:  # noqa: BLE001
                    结果[f"大厅对话/{p.name}故障"] = f"{type(e).__name__}: {e}"
    try:
        n = 归档会话事件(30)
        if n:
            结果["会话事件"] = f"归档 {n} 场"
    except Exception as e:  # noqa: BLE001
        结果["会话事件故障"] = f"{type(e).__name__}: {e}"
    try:
        n = 归档任务记录(90)
        if n:
            结果["任务记录"] = f"归档 {n} 条"
    except Exception as e:  # noqa: BLE001
        结果["任务记录故障"] = f"{type(e).__name__}: {e}"
    for label, root in (
        ("工具间记录", COMPANY / "工具间" / "记录"),
        ("走廊记录", COMPANY / "走廊" / "对话"),
    ):
        try:
            n = 归档日记录(root, 90)
            if n:
                结果[label] = f"归档 {n} 天"
        except Exception as e:  # noqa: BLE001
            结果[f"{label}故障"] = f"{type(e).__name__}: {e}"
    try:
        n = 归档旧日志(30)
        if n:
            结果["后端日志"] = f"归档 {n} 份"
    except Exception as e:  # noqa: BLE001
        结果["后端日志故障"] = f"{type(e).__name__}: {e}"
    try:
        index = 维护统一搜索索引()
        if index["vacuumed"]:
            结果["统一搜索索引"] = f"压实 {index['before']}→{index['after']} 字节"
    except Exception as e:  # noqa: BLE001
        结果["统一搜索索引故障"] = f"{type(e).__name__}: {e}"
    return 结果


if __name__ == "__main__":
    print("日常轮转结果：", 日常轮转())
