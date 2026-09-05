#!/usr/bin/env python3
"""资料室对活人的统一工具出口：内部检索和受控外网查证。"""
from __future__ import annotations


def 资料室工具() -> list:
    from 搜索工具 import 净Glob, 净Read, 净Grep
    from 网络工具 import 网络工具集

    return [净Read(), 净Grep(), 净Glob(), *网络工具集()]
