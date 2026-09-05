#!/usr/bin/env python3
"""常识门（历史群聊推荐器，已退出大厅入口）。

抄件：麦麦 MaiBot main@af9b377 reply_necessity.py（评分公式/常量/正则全数照搬），
按"单bot混人群 → 五人常驻一群"适配：分数按人各算各的，外加我们的进化「点名排他」。
出处与笔记：设计/抄件_麦麦源码笔记.md；施工图：设计/群聊语义_v3_施工方案.md。

现役大厅使用“点名直达 / 明确承接 / 默认经理席”，不调本模块。
保留评分函数只用于历史对照和离线评估，不得恢复对用户发言的阻塞权或否决权。
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from 根 import 代码根

COMPANY = 代码根

# ── 可调参数表（照抄麦麦起步，按手感调；每个数字的出处见抄件笔记） ──
启用 = False
阈值 = 65
点名排他封顶 = 30          # 喊了别人的句子，没被喊的人分数封顶（我们的进化）
存在感_近期窗口秒 = 300
存在感_每句 = 15
存在感_近期上限 = 45
存在感_连续每句 = 20
存在感_连续上限 = 40
冷场兜底_连续无人应 = 3    # 船主连着几句没人接，才让制片兜底接短反应

# 内容词表（麦麦原表 + 我们场子的补充）
直接请求词 = ("帮我", "帮忙", "能不能", "可以吗", "要不要")
弱请求词 = ("需要", "求", "看看", "试试")
问题词 = ("怎么", "如何", "为什么", "有没有")
征询词 = ("你觉得", "你认为", "咋看", "有什么建议", "你们觉得", "怎么看")
全体词 = ("大家", "各位", "都在", "你们", "兄弟们", "全员")
短反应集 = {"哈哈", "哈哈哈", "哈哈哈哈", "草", "笑死", "好", "嗯", "啊", "哦", "6", "666", "？", "?", "。。。", "…", "牛", "牛逼", "可以", "行", "ok", "OK"}
故障词 = ("超时", "报错", "坏了", "挂了", "崩", "失败", "出错", "bug", "虫", "不动了", "打不开")
任务词 = ("修", "改", "做", "查", "写", "建", "加", "删", "部署", "上线", "实现", "开发", "优化", "排查", "重构", "需求", "任务", "验收", "开工")
祈使前缀 = ("把", "给我", "去", "帮")


def _花名册() -> dict:
    import yaml
    return yaml.safe_load((COMPANY / "花名册.yaml").read_text(encoding="utf-8")) or {}


def _清洗(text: str) -> str:
    """剥掉附件占位与@符号串，留用户当前发言主体（抄麦麦 strip_reply_necessity_noise 思想）。"""
    t = " ".join((text or "").split()).strip()
    t = re.sub(r"\[已附 \d+ 个文件/截图\]", "", t)
    t = re.sub(r"@\S+", "", t)
    return t.strip()


def _是短反应(t: str) -> bool:
    return bool(t) and len(t) <= 8 and (t in 短反应集 or re.fullmatch(r"[哈嘿呵嗯哦啊呀~～!！?？。.…6\s]+", t) is not None)


def _像真问题(t: str) -> bool:
    """抄麦麦 has_reply_necessity_question：过滤假问题，认真问才算。"""
    if not t:
        return False
    if re.fullmatch(r"[？?！!~～…\s]+[\w一-鿿]{1,4}[？?！!~～…\s]+", t):
        return False
    if any(w in t for w in 问题词):
        return True
    if re.search(r"(?<![这那没])什么", t):
        return True
    if re.search(r"[吗呢](?:[？?。！!~～…]*$)", t) and 4 <= len(t) <= 80:
        return True
    return bool(re.search(r"[？?](?:$|[。！!~～…])", t) and 4 <= len(t) <= 120)


def _是任务话(t: str) -> bool:
    if any(w in t for w in ("需求", "任务", "开工", "立项", "开会", "开个会", "拉个会", "碰个会", "开会讨论")):
        return True  # "开会"曾漏网，结果执行岗绕过项目经理自行接活。
    if any(t.startswith(p) or f"，{p}" in t or f"。{p}" in t for p in 祈使前缀) and any(w in t for w in 任务词):
        return True
    return sum(1 for w in 任务词 if w in t) >= 2


# 近音字表：只给五人「名字/别名」的判别字配同音近音（含陕西话/语音转文字常见混淆）。
# 静态小表——名字极少变，改花名册名字时顺手补这里即可；不覆盖岗位名/职务词（那些是常用词，会误触）。
_近音字: dict[str, set[str]] = {
    "钟": set("盅忠终中肿踵种衷"), "梁": set("良凉粮量粱"),
    "纪": set("计记忌济系际继既"), "强": set("墙抢枪呛蔷樯"),
    "言": set("炎盐严延颜岩沿研烟"), "哥": set("歌戈鸽割胳"),
    "片": set("骗偏篇编翩"), "工": set("公功攻宫弓龚"),
    "席": set("习西息熄悉锡系细戏"), "子": set("紫仔籽姊"),
}


def _近音同(a: str, b: str) -> bool:
    return a == b or (b in _近音字 and a in _近音字[b]) or (a in _近音字 and b in _近音字[a])


def _近音命中(原文: str, 呼: str) -> bool:
    """原文里是否有一段与『呼』等长、至多差1字、且那1字是近音字的子串（认『老种』=『老钟』）。"""
    L = len(呼)
    if L < 2:  # 单字称呼不做模糊，太容易误触
        return False
    for i in range(len(原文) - L + 1):
        seg = 原文[i:i + L]
        diff = [k for k in range(L) if seg[k] != 呼[k]]
        if len(diff) == 1 and _近音同(seg[diff[0]], 呼[diff[0]]):
            return True
    return False


def _提及检测(原文: str, 花名册: dict) -> dict[str, int]:
    """每人的强相关分：@=100，名字/岗位/别名整词提及=80，名字/别名近音错字=80。岗位名按长度降序消费，防「工程师」误配。"""
    出 = {}
    消费文 = 原文
    for 岗 in sorted(花名册.keys(), key=len, reverse=True):
        cfg = 花名册[岗] or {}
        名 = str(cfg.get("名字") or "")
        呼法 = [x for x in [名, 岗, str(cfg.get("title") or "")] + list(cfg.get("别名") or []) if x]
        分 = 0
        专有 = {名} | set(cfg.get("别名") or [])   # 只对名字/别名做近音容错，岗位名/title 仍只精确
        for 呼 in sorted(set(呼法), key=len, reverse=True):
            if f"@{呼}" in 原文:
                分 = 100
                break
            if 呼 in 消费文:
                分 = max(分, 80)
                消费文 = 消费文.replace(呼, "□" * len(呼))
            elif 呼 in 专有 and _近音命中(消费文, 呼):
                分 = max(分, 80)   # 近音错字点名：和精确点名同权
                # 把命中的那段实际子串抹掉，避免同一段被别人重复消费
                for i in range(len(消费文) - len(呼) + 1):
                    seg = 消费文[i:i + len(呼)]
                    d = [k for k in range(len(呼)) if seg[k] != 呼[k]]
                    if len(d) == 1 and _近音同(seg[d[0]], 呼[d[0]]):
                        消费文 = 消费文[:i] + "□" * len(呼) + 消费文[i + len(呼):]
                        break
        if 分:
            出[岗] = 分
    return 出


def _存在感(人名: str, 记录: list[dict]) -> int:
    """抄麦麦：近300秒每句−15(封顶45) + 尾部连续每句−20(封顶40)。"""
    now = _dt.datetime.now()
    近期 = 0
    for e in reversed(记录):
        try:
            ts = _dt.datetime.strptime(str(e.get("t", ""))[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if (now - ts).total_seconds() > 存在感_近期窗口秒:
            break
        if e.get("who") == 人名:
            近期 += 1
    连续 = 0
    for e in reversed(记录):
        if e.get("who") == 人名:
            连续 += 1
            continue
        break
    return min(存在感_近期上限, 近期 * 存在感_每句) + min(存在感_连续上限, 连续 * 存在感_连续每句)


憋久_每隔一句 = 12          # 距上次开口每隔一句没说 +12
憋久_上限 = 48              # 封顶（≈ 存在感上限，两边对称）


def _憋久加成(人名: str, 记录: list[dict]) -> int:
    """Inner Thoughts balance-decay：距此人上次在大厅开口越久，越想说→加分（封顶）。
    整个窗口没说过→满额。与 _存在感（刚说过就罚）方向相反、成对使用。"""
    隔了 = 0
    for e in reversed(记录):
        if e.get("who") == 人名 or str(e.get("who") or "").startswith(f"{人名}（"):
            return min(憋久_上限, 隔了 * 憋久_每隔一句)
        if e.get("who") and e.get("who") != "船主":
            隔了 += 1
    return 憋久_上限


_报到标记 = ("我在", "在的", "在呢", "报到", "大家好", "各位好", "你们好", "我来了")


def 近场已报到(人名: str, 记录: list[dict], 窗口: int = 12) -> bool:
    """近 窗口 句内此人是否已发过"我在/报到/自我介绍/问候"类短句报到——用于避免重复报到。
    只认短句报到，长实质发言(哪怕含"我在…"的正经内容)不误杀。"""
    for e in 记录[-窗口:]:
        who = str(e.get("who") or "")
        if who != 人名 and not who.startswith(f"{人名}（"):
            continue
        t = " ".join(str(e.get("text") or "").split()).strip()
        if not t:
            continue
        if len(t) <= 20 and any(m in t for m in _报到标记):
            return True
        if t.startswith("我是") and len(t) <= 40:
            return True
    return False


def _归一岗位(raw, 花: dict) -> "str | None":
    """把模型吐的岗位名归到花名册规范键。模型常回显"测试工程师（老纪）""文案工程师（兼职）（阿言）"或简称"测试"——
    精确匹配全丢空(旧bug)。这里去尾部人名括号/认前缀简称/认人名别名。对不上→None(丢弃,绝不误配)。"""
    r = str(raw or "").strip()
    if not r:
        return None
    if r in 花:
        return r
    r2 = re.sub(r"（[^（）]*）\s*$", "", r).strip()   # 去尾部一层括号：测试工程师（老纪）→测试工程师；文案工程师（兼职）（阿言）→文案工程师（兼职）
    if r2 in 花:
        return r2
    for g in 花:                                       # role 以规范岗位名打头
        if r.startswith(g) or r2.startswith(g):
            return g
    for g in 花:                                       # 简称：测试→测试工程师、文案→文案工程师（兼职）
        if g.startswith(r) or (r2 and g.startswith(r2)):
            return g
    for g, c in 花.items():                            # 认人名/别名：老纪→测试工程师
        c = c or {}
        名 = str(c.get("名字") or "").strip()
        别 = str(c.get("别名") or "").strip()
        if 名 and (r == 名 or 名 in r):
            return g
        if 别 and (r == 别 or 别 in r):
            return g
    return None


def _归一speakers(raw_list, 花: dict) -> list:
    """归一每个 speaker 的岗位名 + 同岗去重(取最高rel)。判定门/过门共用这一份，杜绝分叉。"""
    out: dict[str, int] = {}
    for s in (raw_list or []):
        if not isinstance(s, dict):
            continue
        g = _归一岗位(s.get("role"), 花)
        if not g:
            continue
        out[g] = max(out.get(g, 0), int(s.get("rel", 0) or 0))
    return [{"role": g, "rel": rel} for g, rel in out.items()]


def 过门(text: str, 选人: list | None = None, 意图: str | None = None) -> dict:
    """入口：返回 {"唤醒名单": [{"岗位","人名","分","原因"}...], "冷场兜底": bool, "明细": {...}}。
    唤醒名单空 + 冷场兜底False = 这句话不值得叫醒任何人（真人群里"哈哈哈"常常就是没人接）。"""
    from 大厅记录 import 读对话

    花 = _花名册()
    原文 = (text or "").strip()
    净文 = _清洗(原文)
    记录 = 读对话()[-50:]

    if str(意图 or "").strip() == "status_query":
        岗 = "项目经理" if "项目经理" in 花 else (next(iter(花.keys()), ""))
        if 岗:
            人 = str((花.get(岗) or {}).get("名字") or 岗)
            return {
                "唤醒名单": [{"岗位": 岗, "人名": 人, "分": 100, "原因": "查在岗状态"}],
                "冷场兜底": False,
                "明细": {人: "100(语义意图=status_query→查工具间在岗真状态)"},
            }

    提及 = _提及检测(原文, 花)
    有点名 = any(v >= 80 for v in 提及.values())
    喊全体 = any(w in 净文 for w in 全体词)
    短反应 = _是短反应(净文)
    任务话 = _是任务话(净文)

    # 内容分（全员同值）
    内容 = 0
    理由 = []
    if _像真问题(净文):
        内容 += 15; 理由.append("问题")
    if any(w in 净文 for w in 直接请求词):
        内容 += 20; 理由.append("请求")
    if any(w in 净文 for w in 征询词):
        内容 += 20; 理由.append("征询")
    if 喊全体:
        内容 += 30; 理由.append("全体")
    if len(净文) >= 40:
        内容 += 5
    if len(净文) >= 120:
        内容 += 10
    if 短反应:
        内容 -= 25; 理由.append("短反应")

    # LLM 相关度（AutoGen auto-select）：{岗位: rel 0..100}；判定门挂了(选人 is None)→降级全员
    rel = {}
    if 选人 is not None:
        选人 = _归一speakers(选人, 花)   # 闸口自己归一岗位名(去人名括号/简称)→哪怕上游漏归一,"测试工程师（老纪）"也不会被丢空
        rel = {s["role"]: s["rel"] for s in 选人}

    名单, 明细, 候选分 = [], {}, {}
    for 岗, cfg in 花.items():
        cfg = cfg or {}
        人 = str(cfg.get("名字") or 岗)
        强 = 提及.get(岗, 0)                       # 确定性地板：@/名字/近音
        # ── 四直通铁律（硬地板，不过冲动分）：点名 / 任务→经理 / 全体征询 ──
        直通 = None
        if 强 >= 80:
            直通 = "你被点名了"
        elif 任务话 and 岗 == "项目经理":
            直通 = "有活儿要接"
        elif 喊全体 and not 短反应:
            直通 = "在征询大家"
        # 地盘必达：现由 LLM 相关度覆盖（工程部→工程师自然浮出），不再用词表
        本人内容 = 内容 + (20 if 强 >= 80 and any(w in 净文 for w in 弱请求词) else 0)
        相关 = rel.get(岗, 0)
        憋 = _憋久加成(人, 记录)
        罚 = _存在感(人, 记录)
        频率 = float(cfg.get("发言频率", 0.5) or 0.5)
        原始 = 强 + 本人内容 + 相关 + 憋 - 罚
        if 有点名 and 直通 is None and not 喊全体:
            原始 = min(原始, 点名排他封顶)
        分 = max(0, round(原始 * (0.5 + 0.5 * min(1.0, 频率))))
        候选分[岗] = 分
        明细[人] = f"{分}(强{强} 内容{本人内容} 相关{相关} 憋{憋} 罚{罚} 频{频率}{' 直通:'+直通 if 直通 else ''})"
        if 直通:
            优先 = {"你被点名了": 0, "有活儿要接": 1, "在征询大家": 2}.get(直通, 3)
            名单.append({"岗位": 岗, "人名": 人, "分": max(分, 100), "原因": 直通, "_优先": 优先, "_冲动": max(分, 100)})
        elif 相关 > 0 and 分 >= 阈值:
            名单.append({"岗位": 岗, "人名": 人, "分": 分, "原因": "话题和你的地盘相关", "_优先": 9, "_冲动": 分})

    兜底 = False
    if not 名单:
        if 短反应:
            连着没人应 = 0
            for e in reversed(记录):
                if e.get("who") == "船主":
                    连着没人应 += 1
                    continue
                break
            兜底 = 连着没人应 + 1 >= 冷场兜底_连续无人应
        elif 净文 and 选人 is None:
            raise RuntimeError("判定层没有返回候选岗位，拒绝回退成全员唤醒")
        elif 净文:
            # never-empty（AutoGen 不放空）：没人过线 → 取 LLM 相关度最高那位
            best = max(花.keys(), key=lambda g: (rel.get(g, 0), 候选分.get(g, 0)))
            人 = str((花.get(best) or {}).get("名字") or best)
            名单.append({"岗位": best, "人名": 人, "分": max(1, 候选分.get(best, 0)),
                         "原因": "话题和你的地盘相关", "_优先": 9, "_冲动": 候选分.get(best, 0)})

    # 决定性排序（删 random）：直通优先级 → 冲动分降序 → 憋久久者优先
    名单.sort(key=lambda x: (x.get("_优先", 9), -x.get("_冲动", 0)))
    for x in 名单:
        x.pop("_优先", None)
        x.pop("_冲动", None)
    return {"唤醒名单": 名单, "冷场兜底": 兜底, "明细": 明细}
