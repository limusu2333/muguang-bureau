"""Owner-only command line for multi-user accounts."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .生命周期 import 生命周期
from .注册表 import 账户注册表


def _打印(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=Path(os.environ.get("XJ_REGISTRY_PATH", "/var/lib/xj/registry.sqlite3")))
    sub = parser.add_subparsers(dest="command", required=True)

    provision = sub.add_parser("provision", help="开通一个空白独立实例")
    provision.add_argument("--email", required=True)
    provision.add_argument("--display-name", required=True)
    provision.add_argument("--call-name", required=True)
    provision.add_argument("--alias", action="append", default=[])
    provision.add_argument("--company-purpose", required=True)
    provision.add_argument("--chat-daily-usd", type=float, required=True)
    provision.add_argument("--chat-monthly-usd", type=float, required=True)
    provision.add_argument("--search-daily-usd", type=float, required=True)
    provision.add_argument("--search-monthly-usd", type=float, required=True)
    provision.add_argument("--chat-rpm", type=int, default=60)
    provision.add_argument("--search-rpm", type=int, default=60)

    for name in ("status", "stop", "restart", "upgrade"):
        command = sub.add_parser(name)
        command.add_argument("account_id")
    rebind = sub.add_parser("rebind")
    rebind.add_argument("account_id")
    rebind.add_argument("new_sub")
    purge = sub.add_parser("purge")
    purge.add_argument("account_id")
    purge.add_argument("--confirm", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--account-id")
    audit.add_argument("--limit", type=int, default=100)
    sub.add_parser("recover", help="清理被中断的未完成开户")
    args = parser.parse_args()

    registry = 账户注册表(args.registry)
    if args.command == "status":
        _打印(registry.状态(args.account_id))
        return 0
    if args.command == "audit":
        _打印(registry.审计记录(args.account_id, args.limit))
        return 0
    lifecycle = 生命周期(registry)
    if args.command == "recover":
        _打印(lifecycle.恢复未完成开通())
    elif args.command == "provision":
        _打印(lifecycle.开通(
            email=args.email, display_name=args.display_name, call_name=args.call_name,
            aliases=args.alias, company_purpose=args.company_purpose,
            chat_daily_usd=args.chat_daily_usd, chat_monthly_usd=args.chat_monthly_usd,
            search_daily_usd=args.search_daily_usd, search_monthly_usd=args.search_monthly_usd,
            chat_rpm=args.chat_rpm, search_rpm=args.search_rpm,
        ))
    elif args.command == "stop":
        _打印(lifecycle.停用(args.account_id))
    elif args.command == "restart":
        _打印(lifecycle.重启(args.account_id))
    elif args.command == "upgrade":
        _打印(lifecycle.升级版本(args.account_id))
    elif args.command == "rebind":
        _打印(lifecycle.重绑(args.account_id, args.new_sub))
    elif args.command == "purge":
        _打印(lifecycle.永久删除(args.account_id, args.confirm))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
