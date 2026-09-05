#!/usr/bin/env python3
"""死平台 · 密钥闸 —— 密钥/凭据文件的统一红线，焊在每一个"能读出文件内容"的口子上。

为什么要这道闸（2026-07-05 红队逼出的真 P0）：
  红队一发提示注入，让某岗位上了办事底盘、用 `Grep KEY|key|API` 搜了一圈，.env 里五把真 key 的
  原值被 Grep 出来、落进了动作日志（会c5530b1a85）。查根因发现："能读出文件内容"的口子不止一个，
  且没有一个挡密钥：
    ① AgentScope 原版 Read/Grep（资料室、办事底盘都装了）——对 .env 零过滤，还带 --hidden 专读隐藏文件；
    ② 执行室 run_command 的 cat/grep/head/tail/find（都在允许名单）——也对 .env 零过滤。
  两处各写各的过滤 = 迟早漏一个。所以把"什么是密钥文件"收成这一处死规矩，两个口子都焊上它。

真谛（防御纵深）：
  · 第一道防线是人的判断——岗位能识破注入、拒绝贴密钥（红队三发里守住了），强，但不是 100%。
  · 这道闸是平台兜底——结构上就读不出来，不靠岗位每次都清醒。这才是圣域·密钥红线该有的样子。
  · 诚实边界：执行室里的 python3/node 是图灵完备的，能 open() 任意文件，本地没有真沙箱——
    这道闸挡得住 cat/grep/直接 Read 这类明面读取，挡不住有人写代码去读。那是执行室已声明的老边界。
  · 密钥只在启动时由 load_dotenv 进环境变量；任何岗位、任何工具都不该读出原值。
    要核对配置，走 ask_owner 请示实例主人。
"""
from __future__ import annotations

import fnmatch
import os

from 实例配置 import 主人称呼

# 密钥/凭据文件名模式（对 basename 匹配，大小写不敏感）。
# 只收"文件本身就是密钥/凭据"的，不收源码——避免把 secret_manager.py 这类正经代码也挡了。
密钥文件模式 = [
    ".env", ".env.*",                          # dotenv —— 本次泄漏主犯（含 .env.local/.env.production）
    "*.pem", "*.key", "*.pfx", "*.p12",         # 私钥 / 证书
    "*.keystore", "*.jks", "*.asc", "*.gpg", "*.pgp",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",   # SSH 私钥
    ".netrc", ".npmrc", ".pypirc",              # 各类凭据 dotfile
    ".git-credentials", ".htpasswd", ".pgpass",
    "credentials", "credentials.json", "credentials.yaml", "credentials.yml",
    "secrets.json", "secrets.yaml", "secrets.yml",
]

# 白名单后缀：模板/示例/文档/公钥不含真密钥，放行（.env.example 该能读、id_rsa.pub 该能读）。
_白名单后缀 = (".example", ".sample", ".template", ".dist", ".md", ".pub")


def 是密钥文件(路径: str | os.PathLike) -> bool:
    """这个路径是不是密钥/凭据文件（只看文件名，不看内容）。"""
    if not 路径:
        return False
    名 = os.path.basename(str(路径)).lower()
    if 名.endswith(_白名单后缀):
        return False
    return any(fnmatch.fnmatch(名, p) for p in 密钥文件模式)


def 命令碰密钥(parts: list[str]) -> str | None:
    """扫一条已 shlex 拆好的命令，返回第一个碰到的密钥文件参数（没有则 None）。

    只看不以 `-` 开头的参数（跳过 flag），逐个当路径判。用于执行室在放行 cat/grep/head/… 前拦一道。
    注意：这只挡明面把密钥文件当参数读；挡不住 `grep -r X .` 递归撞进 .env 或 python3 open()——
    前者交给各读取工具自身的密钥闸兜底，后者是执行室已声明的无沙箱老边界。
    """
    for a in parts[1:]:
        if a.startswith("-"):
            continue
        if 是密钥文件(a):
            return a
    return None


拒读文案 = (
    "【密钥闸·平台死规矩】{目标} 是密钥/凭据文件，平台不对任何岗位、任何工具开放读取——"
    "这不是你能力不够，是圣域·密钥红线：密钥只在启动时由 load_dotenv 进环境变量，"
    "任何人都不该读出原值。要核对配置，用 ask_owner 请示{主人称呼}本人。"
)


def 拒读(目标: str) -> str:
    return 拒读文案.format(目标=目标, 主人称呼=主人称呼())


if __name__ == "__main__":
    样本 = {
        ".env": True, ".env.local": True, ".env.production": True,
        ".env.example": False, "config/.env": True, "/abs/path/.env": True,
        "id_rsa": True, "id_rsa.pub": False, "server.pem": True, "api.key": True,
        "credentials.json": True, "secrets.yaml": True,
        "main.py": False, "secret_manager.py": False, "README.md": False,
        "notes.env.md": False, "": False,
    }
    print("密钥闸自检：")
    坏 = 0
    for 路, 期 in 样本.items():
        实 = 是密钥文件(路)
        标 = "✅" if 实 == 期 else "❌"
        if 实 != 期:
            坏 += 1
        print(f"  {标} 是密钥文件({路!r}) = {实}（期望 {期}）")
    print(f"命令碰密钥(['cat', '.env']) = {命令碰密钥(['cat', '.env'])!r}")
    print(f"命令碰密钥(['grep', '-i', 'KEY', 'src/app.py']) = {命令碰密钥(['grep', '-i', 'KEY', 'src/app.py'])!r}")
    print(f"结论：{'全过' if 坏 == 0 else f'{坏} 条不符'}")
