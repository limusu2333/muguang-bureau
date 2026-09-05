#!/usr/bin/env python3
"""会话事件总线：持久重放、实时 SSE、插话和单 run 防重。

不变量：
- 事件先写盘并 fsync，成功后才进入内存和前端；写盘失败直接抛错。
- 每条事件有单调序号，SSE 可从 Last-Event-ID 继续，不依赖易满的实时队列。
- 日志坏行和写入故障进入统一健康故障清单，不静默伪装正常。
- 常见密钥字段和 Bearer/key/token 样式在落盘前脱敏。
"""
from __future__ import annotations

import gzip
import json
import os
import queue
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

from 根 import 数据根

COMPANY = 数据根
事件目录 = COMPANY / "运行状态" / "会话事件"

_心跳 = {"类型": "心跳"}
_敏感键 = re.compile(r"(?i)(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|密钥|口令)")
_Bearer = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")
_Key样式 = re.compile(r"\b(?:sk|key|token)-[A-Za-z0-9_-]{8,}\b", re.I)


class 事件持久化失败(RuntimeError):
    pass


def _安全sid(raw: str) -> str:
    sid = str(raw or "").strip()
    if not sid or len(sid) > 96 or ".." in sid or not re.fullmatch(r"[A-Za-z0-9_.\-\u4e00-\u9fff]+", sid):
        raise ValueError("会话 id 含非法字符")
    return sid


def 校验会话id(raw: str) -> str:
    """在开任务和线程前校验；入口应把非法 id 明确回给调用方。"""
    return _安全sid(raw)


def _脱敏(value: Any, key: str = "") -> Any:
    if _敏感键.search(key):
        return "[已隐藏]"
    if isinstance(value, dict):
        return {str(k): _脱敏(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_脱敏(v) for v in value]
    if isinstance(value, str):
        return _Key样式.sub("[已隐藏]", _Bearer.sub(r"\1[已隐藏]", value))
    return value


class _会话:
    def __init__(self, sid: str) -> None:
        self.sid = _安全sid(sid)
        self.事件: list[dict[str, Any]] = []
        self.订阅队列: list[queue.Queue] = []
        self.取消队列: list[queue.Queue] = []
        self.running = False
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.最后访问 = time.monotonic()
        self.加载故障 = 0
        self._载入持久()

    def _文件(self) -> Path:
        return 事件目录 / f"{self.sid}.jsonl"

    def _载入持久(self) -> None:
        p = self._文件()
        archives = sorted(事件目录.glob(f"归档/*/{self.sid}.jsonl.gz")) if 事件目录.exists() else []
        sources: list[tuple[Path, list[str]]] = []
        for archived in archives:
            try:
                with gzip.open(archived, "rt", encoding="utf-8") as f:
                    sources.append((archived, f.read().splitlines()))
            except Exception as e:  # noqa: BLE001
                from 状态存储 import 登记故障
                登记故障(archived, f"事件归档读失败：{type(e).__name__}: {e}")
                self.加载故障 += 1
        if p.exists():
            sources.append((p, p.read_text(encoding="utf-8").splitlines()))
        if not sources:
            return
        last_seq = 0
        for source, lines in sources:
            for line_no, line in enumerate(lines, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    if not isinstance(ev, dict):
                        raise TypeError("事件顶层不是对象")
                    seq = int(ev.get("序号") or last_seq + 1)
                    if seq <= last_seq:
                        seq = last_seq + 1
                    ev["序号"] = seq
                    last_seq = seq
                    self.事件.append(ev)
                except Exception as e:  # noqa: BLE001
                    self.加载故障 += 1
                    from 状态存储 import 登记故障
                    登记故障(source, f"事件日志第 {line_no} 行损坏：{type(e).__name__}: {e}")
        if not self.加载故障:
            from 状态存储 import 清故障
            清故障(p)

    def _追加持久(self, event: dict[str, Any]) -> None:
        p = self._文件()
        try:
            事件目录.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            from 状态存储 import 清故障
            清故障(p)
        except Exception as e:  # noqa: BLE001
            from 状态存储 import 登记故障
            登记故障(p, f"事件持久化失败：{type(e).__name__}: {e}")
            raise 事件持久化失败(f"事件未写入磁盘，未向前端发布：{type(e).__name__}: {e}") from e


_总线: dict[str, _会话] = {}
_总线锁 = threading.Lock()


def _取会话(sid: str) -> _会话:
    sid = _安全sid(sid)
    with _总线锁:
        s = _总线.get(sid)
        if s is None:
            s = _会话(sid)
            _总线[sid] = s
        s.最后访问 = time.monotonic()
        if len(_总线) > 256:
            候选 = sorted(
                (x for x in _总线.values() if x is not s and not x.running and not x.订阅队列 and not x.取消队列),
                key=lambda x: x.最后访问,
            )
            for old in 候选[: max(0, len(_总线) - 256)]:
                _总线.pop(old.sid, None)
        return s


def 开始run(sid: str) -> bool:
    s = _取会话(sid)
    with s.lock:
        if s.running:
            return False
        s.running = True
        return True


def 结束run(sid: str) -> None:
    s = _取会话(sid)
    try:
        发布事件(sid, {"类型": "run结束"})
    finally:
        with s.condition:
            s.running = False
            s.condition.notify_all()


def 在跑(sid: str) -> bool:
    s = _取会话(sid)
    with s.lock:
        return s.running


def 发布事件(sid: str, event: dict[str, Any]) -> None:
    """先持久化，再进入内存和实时流。磁盘失败时事件对所有消费者都不可见。"""
    s = _取会话(sid)
    ev = _脱敏(dict(event))
    ev.setdefault("t", time.strftime("%H:%M:%S"))
    with s.condition:
        ev["序号"] = int(s.事件[-1].get("序号") or len(s.事件)) + 1 if s.事件 else 1
        s._追加持久(ev)
        s.事件.append(ev)
        s.最后访问 = time.monotonic()
        subscribers = list(s.订阅队列)
        s.condition.notify_all()
    for q in subscribers:
        try:
            q.put_nowait(ev)
        except queue.Full:
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
            q.put_nowait({
                "类型": "事件缺口",
                "序号": ev["序号"],
                "内容": "订阅者处理过慢；请按持久序号重新连接并重放。",
            })


def 读事件(sid: str) -> list[dict[str, Any]]:
    s = _取会话(sid)
    with s.lock:
        return list(s.事件)


def 订阅(sid: str) -> queue.Queue:
    """兼容内部消费者；SSE 本身不再使用这个有限队列。队列满会收到“事件缺口”。"""
    s = _取会话(sid)
    q: queue.Queue = queue.Queue(maxsize=1000)
    with s.lock:
        s.订阅队列.append(q)
    return q


def 取消订阅(sid: str, q: queue.Queue) -> None:
    s = _取会话(sid)
    with s.lock:
        if q in s.订阅队列:
            s.订阅队列.remove(q)


def SSE流(sid: str, 心跳秒: float = 15.0, 从序号: int = 0) -> Iterator[str]:
    """按持久序号重放并等待新事件；慢客户端不会因容量 1000 的队列而静默丢事件。"""
    s = _取会话(sid)
    cursor = 0
    with s.lock:
        if 从序号 > 0:
            while cursor < len(s.事件) and int(s.事件[cursor].get("序号") or 0) <= 从序号:
                cursor += 1
    while True:
        heartbeat = False
        batch: list[dict[str, Any]] = []
        with s.condition:
            if cursor >= len(s.事件):
                s.condition.wait(timeout=心跳秒)
            if cursor >= len(s.事件):
                heartbeat = True
            else:
                batch = list(s.事件[cursor:])
                cursor = len(s.事件)
        if heartbeat:
            yield f"data: {json.dumps(_心跳, ensure_ascii=False)}\n\n"
            continue
        for ev in batch:
            seq = int(ev.get("序号") or 0)
            yield f"id: {seq}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if ev.get("类型") == "run结束":
                return


def 发布插话(sid: str, payload: dict[str, Any]) -> None:
    s = _取会话(sid)
    with s.lock:
        queues = list(s.取消队列)
    发布事件(sid, {"类型": "插话", "发言人": "船主", "内容": payload.get("text", "")})
    for q in queues:
        try:
            q.put_nowait(dict(payload))
        except queue.Full:
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
            q.put_nowait({"text": payload.get("text", ""), "类型": "插话缺口", "说明": "旧插话已被最新船主指令覆盖"})


def 订阅插话(sid: str) -> queue.Queue:
    s = _取会话(sid)
    q: queue.Queue = queue.Queue(maxsize=100)
    with s.lock:
        s.取消队列.append(q)
    return q


def 取消插话订阅(sid: str, q: queue.Queue) -> None:
    s = _取会话(sid)
    with s.lock:
        if q in s.取消队列:
            s.取消队列.remove(q)


def 取插话(q: queue.Queue) -> list[dict[str, Any]]:
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def 清空session(sid: str) -> None:
    sid = _安全sid(sid)
    with _总线锁:
        s = _总线.pop(sid, None)
    p = s._文件() if s is not None else 事件目录 / f"{sid}.jsonl"
    p.unlink(missing_ok=True)
    if 事件目录.exists():
        for archived in 事件目录.glob(f"归档/*/{sid}.jsonl.gz"):
            archived.unlink(missing_ok=True)


def 健康状态() -> dict[str, Any]:
    from 状态存储 import 故障清单
    根 = str(事件目录)
    faults = [x for x in 故障清单() if str(x.get("文件") or "").startswith(根)]
    with _总线锁:
        sessions = list(_总线.values())
    return {
        "健康": not faults,
        "内存会话": len(sessions),
        "运行中": sum(1 for s in sessions if s.running),
        "损坏或写入失败": faults,
    }


def 新会话id() -> str:
    return "会" + uuid.uuid4().hex[:10]


if __name__ == "__main__":
    sid = "_自检会话"
    清空session(sid)
    assert 开始run(sid) is True
    assert 开始run(sid) is False
    发布事件(sid, {"类型": "发言", "内容": "方案 A", "api_key": "不该落盘"})
    发布事件(sid, {"类型": "发言", "内容": "Bearer abcdefghijklmnop"})
    assert len(读事件(sid)) == 2
    assert 读事件(sid)[0]["api_key"] == "[已隐藏]"
    assert "abcdefghijklmnop" not in 读事件(sid)[1]["内容"]
    cq = 订阅插话(sid)
    发布插话(sid, {"text": "先停一下"})
    assert 取插话(cq)[0]["text"] == "先停一下"
    结束run(sid)
    assert any(e.get("类型") == "run结束" for e in 读事件(sid))
    last = 读事件(sid)[-2]["序号"]
    frame = next(SSE流(sid, 从序号=last))
    assert "run结束" in frame and f"id: {last + 1}" in frame
    with _总线锁:
        _总线.pop(sid, None)
    assert len(读事件(sid)) == 4
    清空session(sid)
    print("事件总线自检通过：先落盘、序号重放、插话、脱敏、单 run。")
