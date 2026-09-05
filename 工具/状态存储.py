#!/usr/bin/env python3
"""状态文件的统一安全读写。

规则：
- JSON 损坏时保留原文件、登记故障并抛错，绝不把“读坏了”伪装成空状态。
- 覆盖前保存最近一份已验证的 `.bak`，写入走临时文件 + flush + fsync + rename。
- 故障清单给健康页和施工排查使用，不包含文件正文或密钥。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any

_锁 = threading.RLock()
_故障: dict[str, dict[str, str]] = {}


class 状态损坏(RuntimeError):
    pass


def _记故障(path: Path, error: Exception | str) -> None:
    with _锁:
        _故障[str(path)] = {
            "文件": str(path),
            "错误": str(error)[:240],
            "时间": dt.datetime.now().isoformat(timespec="seconds"),
        }


def 登记故障(path: Path, error: Exception | str) -> None:
    _记故障(Path(path), error)


def 清故障(path: Path) -> None:
    with _锁:
        _故障.pop(str(path), None)


def 故障清单() -> list[dict[str, str]]:
    with _锁:
        return list(_故障.values())


def 读JSON(path: Path, 默认: Any = None, 类型: type | tuple[type, ...] | None = dict) -> Any:
    """读 JSON。文件不存在返回默认；文件存在但损坏/类型不符则抛 `状态损坏`。"""
    path = Path(path)
    if not path.exists():
        return 默认
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if 类型 is not None and not isinstance(data, 类型):
            raise TypeError(f"顶层类型应为 {类型}，实际是 {type(data).__name__}")
    except Exception as e:  # noqa: BLE001
        _记故障(path, f"{type(e).__name__}: {e}")
        raise 状态损坏(f"状态文件损坏，已保留原件、拒绝覆盖：{path.name}（{type(e).__name__}: {e}）") from e
    清故障(path)
    return data


def 写JSON(path: Path, data: Any, 备份: bool = True) -> None:
    """原子写 JSON。旧文件必须先能解析，损坏时拒绝覆盖。"""
    path = Path(path)
    with _锁:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            读JSON(path, 类型=None)
            if 备份:
                shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(path)
            清故障(path)
        finally:
            tmp.unlink(missing_ok=True)
