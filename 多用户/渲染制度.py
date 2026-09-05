#!/usr/bin/env python3
"""把通用制度模板严格渲染为单个远程实例的只读制度目录。"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml


_占位 = re.compile(r"{{\s*([^{}]+?)\s*}}")
_允许字段 = {"主人称呼", "主人显示名", "主人别名", "公司目的"}


def _文本(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise RuntimeError(f"{field} 必须是字符串")
    value = value.strip()
    if not value or len(value) > limit:
        raise RuntimeError(f"{field} 为空或超过 {limit} 字")
    if any(ord(ch) < 32 for ch in value) or "{" in value or "}" in value:
        raise RuntimeError(f"{field} 含控制字符或花括号")
    return value


def _值表(config_path: Path) -> dict[str, str]:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("owner"), dict):
        raise RuntimeError("INSTANCE_CONFIG 缺少 owner 对象")
    owner = raw["owner"]
    aliases = owner.get("别名") or []
    if not isinstance(aliases, list):
        raise RuntimeError("owner.别名 必须是数组")
    aliases_text = "、".join(_文本(item, "owner.别名[]", 32) for item in aliases)
    if len(aliases_text) > 128:
        raise RuntimeError("owner.别名合计超过 128 字")
    return {
        "主人称呼": _文本(owner.get("称呼"), "owner.称呼", 32),
        "主人显示名": _文本(owner.get("display_name"), "owner.display_name", 32),
        "主人别名": aliases_text,
        "公司目的": _文本(raw.get("公司目的"), "公司目的", 200),
    }


def _扫描词(path: Path | None) -> tuple[str, ...]:
    if path is None:
        return ()
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        term = line.strip()
        if term and not term.startswith("#"):
            terms.append(term)
    return tuple(dict.fromkeys(terms))


def _模板文件(source: Path) -> list[Path]:
    files: list[Path] = []
    for path in source.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"模板不允许软链接：{path.relative_to(source)}")
        if path.is_file():
            files.append(path)
    return sorted(files)


def 渲染(source: Path, output: Path, config: Path, private_terms: Path | None = None) -> dict[str, int]:
    source = source.resolve()
    output = output.resolve()
    values = _值表(config.resolve())
    terms = _扫描词(private_terms.resolve() if private_terms else None)
    files = _模板文件(source)
    if not files:
        raise RuntimeError("通用制度模板目录为空")

    with tempfile.TemporaryDirectory(prefix="xj-policy-render-") as td:
        stage = Path(td) / "policy"
        for src in files:
            rel = src.relative_to(source)
            try:
                text = src.read_text(encoding="utf-8")
            except UnicodeDecodeError as e:
                raise RuntimeError(f"制度模板必须是 UTF-8 文本：{rel}") from e
            hits = [term for term in terms if term in text]
            if hits:
                raise RuntimeError(f"通用模板含私人信息：{rel} 命中 {hits[:3]}")
            fields = set(_占位.findall(text))
            unknown = fields - _允许字段
            if unknown:
                raise RuntimeError(f"模板含未知占位：{rel}: {sorted(unknown)}")
            rendered = _占位.sub(lambda match: values[match.group(1).strip()], text)
            if "{{" in rendered or "}}" in rendered:
                raise RuntimeError(f"渲染后仍有占位残留：{rel}")
            rendered_hits = [term for term in terms if term in rendered]
            if rendered_hits:
                raise RuntimeError(f"渲染产物含私人信息：{rel} 命中 {rendered_hits[:3]}")
            dst = stage / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(rendered, encoding="utf-8")

        if output.exists() and any(output.iterdir()):
            raise RuntimeError(f"制度输出目录必须为空：{output}")
        output.mkdir(parents=True, exist_ok=True)
        for src in stage.rglob("*"):
            rel = src.relative_to(stage)
            dst = output / rel
            if src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                dst.chmod(0o444)
        for directory in sorted((p for p in output.rglob("*") if p.is_dir()), reverse=True):
            directory.chmod(0o555)
        output.chmod(0o555)
    return {"文件数": len(files), "占位数": sum(len(_占位.findall(p.read_text(encoding="utf-8"))) for p in files)}


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("/opt/xj-policy-template"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("XJ_INSTANCE_CONFIG", "/instance/instance.yaml")))
    parser.add_argument("--private-terms", type=Path)
    args = parser.parse_args()
    print(渲染(args.source, args.output, args.config, args.private_terms))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
