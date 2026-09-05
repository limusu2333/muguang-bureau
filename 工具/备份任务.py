#!/usr/bin/env python3
"""安装并检查当前公司的 macOS 自动备份任务。"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

COMPANY = Path(__file__).resolve().parents[1]
LABEL = "com.munaigongsi.company-backup"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def 是macOS() -> bool:
    return sys.platform == "darwin"


def 期望配置() -> dict[str, Any]:
    python = COMPANY / ".venv" / "bin" / "python3"
    script = COMPANY / "工具" / "备份.py"
    state = COMPANY / "运行状态"
    return {
        "Label": LABEL,
        "ProgramArguments": [str(python), str(script), "--scheduled"],
        "WorkingDirectory": str(COMPANY),
        # 每天只唤醒做一次轻量检查；备份.py 内部三天才真正比对，无变化不打包。
        "StartCalendarInterval": {"Hour": 4, "Minute": 30},
        "RunAtLoad": True,
        "ProcessType": "Background",
        "StandardOutPath": str(state / "备份定时任务.log"),
        "StandardErrorPath": str(state / "备份定时任务.err.log"),
    }


def 检查(*, 检查加载: bool = True) -> dict[str, Any]:
    expected = 期望配置()
    result: dict[str, Any] = {
        "适用": 是macOS(),
        "已安装": PLIST.is_file(),
        "配置正确": False,
        "已加载": False,
        "配置文件": str(PLIST),
        "期望程序": expected["ProgramArguments"],
        "问题": [],
    }
    if not result["适用"]:
        result.update({
            "已安装": False,
            "配置正确": False,
            "已加载": False,
            "说明": "自动备份由 macOS 宿主管理，Linux 用户容器不运行 launchctl。",
        })
        return result
    if PLIST.is_file():
        try:
            with PLIST.open("rb") as handle:
                actual = plistlib.load(handle)
            for key in ("Label", "ProgramArguments", "WorkingDirectory", "StartCalendarInterval"):
                if actual.get(key) != expected[key]:
                    result["问题"].append(f"{key} 不是当前公司的配置")
            result["配置正确"] = not result["问题"]
            result["实际程序"] = actual.get("ProgramArguments") or []
        except Exception as e:  # noqa: BLE001
            result["问题"].append(f"配置无法读取：{type(e).__name__}: {e}")
    else:
        result["问题"].append("当前公司的自动备份任务尚未安装")

    if 检查加载:
        domain = f"gui/{os.getuid()}/{LABEL}"
        proc = subprocess.run(["launchctl", "print", domain], capture_output=True, text=True, timeout=10, check=False)
        result["已加载"] = proc.returncode == 0
        if proc.returncode and result["已安装"]:
            result["问题"].append("配置文件存在，但 macOS 没有加载它")
    return result


def 安装() -> dict[str, Any]:
    if not 是macOS():
        raise RuntimeError("自动备份任务只能安装在 macOS 宿主，不能安装进 Linux 用户容器")
    config = 期望配置()
    python = Path(config["ProgramArguments"][0])
    script = Path(config["ProgramArguments"][1])
    if not python.is_file() or not os.access(python, os.X_OK):
        raise FileNotFoundError(f"找不到当前公司的 Python：{python}")
    if not script.is_file():
        raise FileNotFoundError(f"找不到备份程序：{script}")
    Path(config["StandardOutPath"]).parent.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f"{LABEL}.", suffix=".plist", dir=PLIST.parent, delete=False) as handle:
        temp = Path(handle.name)
        plistlib.dump(config, handle, sort_keys=False)
    try:
        lint = subprocess.run(["plutil", "-lint", str(temp)], capture_output=True, text=True, timeout=10, check=False)
        if lint.returncode:
            raise RuntimeError((lint.stderr or lint.stdout or "plist 校验失败").strip())
        os.replace(temp, PLIST)
    finally:
        temp.unlink(missing_ok=True)

    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{LABEL}"
    subprocess.run(["launchctl", "bootout", target], capture_output=True, text=True, timeout=10, check=False)
    loaded = subprocess.run(["launchctl", "bootstrap", domain, str(PLIST)], capture_output=True, text=True, timeout=20, check=False)
    if loaded.returncode:
        raise RuntimeError((loaded.stderr or loaded.stdout or "macOS 加载备份任务失败").strip())
    result = 检查()
    if not result["配置正确"] or not result["已加载"]:
        raise RuntimeError("备份任务安装后自检未通过：" + "；".join(result["问题"]))
    return result


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="写入并加载当前公司的定时备份任务")
    parser.add_argument("--check", action="store_true", help="检查配置是否指向当前公司且已被 macOS 加载")
    args = parser.parse_args()
    result = 安装() if args.install else 检查()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["配置正确"] and result["已加载"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
