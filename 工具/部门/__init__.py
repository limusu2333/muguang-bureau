#!/usr/bin/env python3
"""死平台 · 部门注册台 —— 自动发现 部门/ 下每个部门模块，按岗位把人路由到他部门的个人记忆册子。

真谛（2026-07-09，船主拍板"每部门一个模块"）：部门是**一个一个分开、可插拔**的块。
- **加部门** ＝ 往 `部门/` 文件夹丢一个 `<部门名>.py`（带 `收`/`目录名`/`name`）→ 注册台下次一扫就认，自动上岗。
- **剪部门** ＝ 删那个 `.py`。
**注册台零改动、别的部门零改动**——不再有中心大字典（那正是被船主否掉的写法）。

对外只出一个口：`个人记忆路径(人名)` → 他部门目录/<人名>.md（"记忆跟人走"的物理落点）。
不在花名册里的测试名/生人仍可落旧 `记忆库/`；花名册内员工若无人认领或重复认领则报错，禁止绕行。
"""
from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path

from 根 import 代码根, 数据根

COMPANY = 数据根
_旧个人记忆目录 = 数据根 / "记忆库"   # 认不出岗位的人兜底回老位置


_加载失败: dict = {}   # 部门模块名 → 导入错误信息（不静默吞：坏了得能查出来，校验/健康用）


def 各部门() -> list:
    """扫 部门/ 文件夹，返回所有部门模块（带 收/目录名 的才算部门）。丢进来一个新 .py 就自动上岗。
    某个部门模块导入崩了不连累别的，但把错记进 _加载失败——不再静默吞，校验()/健康能查出来。"""
    出 = []
    _加载失败.clear()
    for m in pkgutil.iter_modules([str(Path(__file__).parent)]):
        try:
            mod = importlib.import_module(f"部门.{m.name}")
        except Exception as e:  # noqa: BLE001 一个部门坏了不许连累别的——但记下来，别静默
            _加载失败[m.name] = f"{type(e).__name__}: {e}"
            continue
        missing = [k for k in ("name", "目录名", "收", "头", "复核替补") if not hasattr(mod, k)]
        if missing:
            _加载失败[m.name] = "缺部门契约字段：" + "、".join(missing)
            continue
        出.append(mod)
    return 出


def 校验() -> list:
    """自检部门/花名册配置，返回问题清单（空＝健康）。船主加/改部门或花名册后用它兜底查错，别让坏配置静默跑。
    查：模块导入失败、缺『收』、收的岗位不在花名册、头不在自己收列表、多部门声明跨部门权(多经理歧义)、人名撞车(记忆会串)。"""
    问题: list[str] = []
    depts = 各部门()
    for 名, 错 in _加载失败.items():
        问题.append(f"部门模块『{名}.py』导入失败：{错}")
    try:
        import yaml
        花 = yaml.safe_load((代码根 / "花名册.yaml").read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        问题.append(f"花名册读不了：{type(e).__name__}: {e}")
        花 = {}
    岗集 = set(花.keys())
    跨权部门: list[str] = []
    插头: dict[str, list[str]] = {}
    目录们: dict[str, list[str]] = {}
    认领: dict[str, list[str]] = {g: [] for g in 岗集}
    for d in depts:
        名 = getattr(d, "目录名", getattr(d, "__name__", "?"))
        插头.setdefault(str(getattr(d, "name", "")), []).append(名)
        目录们.setdefault(str(名), []).append(str(getattr(d, "name", "")))
        收 = getattr(d, "收", None)
        if not 收:
            问题.append(f"部门『{名}』没声明『收』（它收哪些岗位）")
            收 = []
        for g in 收:
            if g not in 岗集:
                问题.append(f"部门『{名}』收的岗位『{g}』不在花名册里")
            else:
                认领[g].append(名)
        头 = getattr(d, "头", None)
        if 头 and 头 not in 收:
            问题.append(f"部门『{名}』的头『{头}』不在它自己的收列表里")
        替补 = list(getattr(d, "复核替补", []) or [])
        if not 替补:
            问题.append(f"部门『{名}』没声明独立『复核替补』，部门头本人交付时会无人复核")
        for g in 替补:
            if g not in 岗集:
                问题.append(f"部门『{名}』的复核替补『{g}』不在花名册里")
            if g in 收:
                问题.append(f"部门『{名}』的复核替补『{g}』仍在本部门，不能解决自审冲突")
        if getattr(d, "跨部门权", False):
            跨权部门.append(名)
            代理 = list(getattr(d, "代理经理复核", []) or [])
            if not 代理:
                问题.append(f"经理部门『{名}』没声明『代理经理复核』，经理本人交付时会少一道收敛")
            for g in 代理:
                if g not in 岗集:
                    问题.append(f"经理部门『{名}』的代理经理复核『{g}』不在花名册里")
        expected = (数据根 / str(名)).resolve()
        if not expected.is_relative_to(数据根):
            问题.append(f"部门『{名}』目录越出 DATA_ROOT：{expected}")
        doc = str(getattr(d, "__doc__", "") or "")
        for word in ("规则牌", "权限", "可见性", "诚实边界"):
            if word not in doc:
                问题.append(f"部门『{名}』规则牌缺『{word}』说明")
    for plug, owners in 插头.items():
        if not plug or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", plug):
            问题.append(f"部门英文插头无效：{plug or '空'}")
        elif len(owners) > 1:
            问题.append(f"部门插头『{plug}』重复：{'、'.join(owners)}")
    for dirname, plugs in 目录们.items():
        if len(plugs) > 1:
            问题.append(f"部门目录『{dirname}』被多个模块声明：{'、'.join(plugs)}")
    for job, owners in 认领.items():
        if not owners:
            问题.append(f"花名册岗位『{job}』没有任何部门认领")
        elif len(owners) > 1:
            问题.append(f"花名册岗位『{job}』被多个部门认领：{'、'.join(owners)}")
    if len(跨权部门) > 1:
        问题.append(f"多个部门声明了跨部门权（多经理歧义，只该经理办公室有）：{'、'.join(跨权部门)}")
    for n, gs in 名字冲突().items():
        问题.append(f"人名『{n}』被多个岗位共用、记忆会串：{'、'.join(gs)}")
    # F7：不同人名清洗成同一个信誉账文件名（如 a/b 与 a\b）→ 分数互相污染
    try:
        import 信誉
        账名: dict[str, list] = {}
        for g in 岗集:
            n = str((花.get(g) or {}).get("名字") or "").strip()
            if n:
                账名.setdefault(信誉._账(n).name, []).append(n)
        for f名, ns in 账名.items():
            uniq = sorted(set(ns))
            if len(uniq) > 1:
                问题.append(f"人名 {('、'.join(uniq))} 清洗成同一个信誉账文件『{f名}』、分数会串——用稳定ID或改名")
    except Exception:  # noqa: BLE001
        pass
    # F8：调任.json 读坏了（所有已调任的人会表面失忆）
    try:
        _调任目标("")   # 触发一次读、刷新 _调任读错
    except Exception:  # noqa: BLE001
        pass
    if _调任读错:
        问题.append(f"调任覆盖文件 职级/调任.json 读不了：{_调任读错}——已调任的人会集体回落原部门读空册子")
    return 问题


def _人名到岗位(人名: str):
    """扫花名册，人名→岗位。查不到返回 None。"""
    import yaml
    花 = yaml.safe_load((代码根 / "花名册.yaml").read_text(encoding="utf-8")) or {}
    目标 = str(人名 or "").strip()
    for 岗, c in 花.items():
        if str((c or {}).get("名字") or "").strip() == 目标:
            return 岗
    return None


_调任读错: str = ""   # 调任.json 解析失败记这里（F8：别纯静默回落，让已调任的人集体表面失忆还查不出）


def _调任目标(人名: str):
    """调任覆盖：这个人被平级调去哪个部门（目录名）。没有返回 None。（职级.调任 写这个文件）
    F8 二审：文件坏了记进 _调任读错、校验()能查出来，别静默让所有已调任的人回落原部门读空册子。"""
    global _调任读错
    f = COMPANY / "职级" / "调任.json"
    if not f.exists():
        _调任读错 = ""
        return None
    try:
        from 状态存储 import 读JSON
        d = 读JSON(f, 默认={}, 类型=dict) or {}
        _调任读错 = ""
        v = d.get(str(人名 or "").strip())
        return str(v).strip() or None if v else None
    except Exception as e:  # noqa: BLE001
        _调任读错 = f"{type(e).__name__}: {e}"
        raise RuntimeError(f"调任覆盖账损坏，拒绝按旧部门绕行：{_调任读错}") from e


def 认领部门(人名: str):
    """哪个部门收这个人？先查调任覆盖（换了职能就归新部门），再按花名册岗位归属。没人收返回 None。"""
    人名 = str(人名 or "").strip()
    目标目录名 = _调任目标(人名)
    if 目标目录名:
        for d in 各部门():
            if getattr(d, "目录名", None) == 目标目录名:
                return d   # 调任生效：人归到新部门
    岗 = _人名到岗位(人名)
    if not 岗:
        return None
    owners = [d for d in 各部门() if 岗 in getattr(d, "收", [])]
    if len(owners) != 1:
        detail = "无人认领" if not owners else "重复认领：" + "、".join(str(d.目录名) for d in owners)
        raise RuntimeError(f"岗位『{岗}』部门配置错误（{detail}），拒绝选择兜底部门")
    return owners[0]


def 名字冲突() -> dict:
    """稳定ID护栏：个人记忆按『人名』落盘，所以人名必须全公司唯一——两个岗位撞同一个名字＝记忆会串。
    返回 {名字: [岗位, 岗位...]}（只列撞名的）；空＝没冲突。船主招人/调岗前用它自检。"""
    try:
        import yaml
        花 = yaml.safe_load((代码根 / "花名册.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}
    名到岗: dict[str, list] = {}
    for 岗, c in 花.items():
        n = str((c or {}).get("名字") or "").strip()
        if n:
            名到岗.setdefault(n, []).append(岗)
    return {n: gs for n, gs in 名到岗.items() if len(gs) > 1}


def 部门目录(人名: str) -> Path:
    """人此刻所在部门的目录（不存在则建）。认不出岗位/没人收→兜底老记忆库目录。"""
    d = 认领部门(人名)
    目录 = (数据根 / d.目录名) if d else _旧个人记忆目录
    目录.mkdir(parents=True, exist_ok=True)
    return 目录


def 个人记忆路径(人名: str) -> Path:
    """某人的个人记忆册子路径 ＝ 他部门目录/<人名>.md（记忆跟人走的物理落点）。"""
    return 部门目录(人名) / f"{人名}.md"


# ── 上下级 / 派活权（规则焊在各部门模块的规则牌上：头＝谁、有没有跨部门权）──
def _岗位人名(岗位):
    """岗位 → 人名（花名册反查）。"""
    if not 岗位:
        return None
    try:
        import yaml
        花 = yaml.safe_load((代码根 / "花名册.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return None
    c = 花.get(岗位)
    return str((c or {}).get("名字") or "").strip() or None


def 部门头(人名: str):
    """这个人所在部门此刻的头＝部门里评级最高且达主管级的人（职级驱动，2026-07-10：升职真接管把关权）。
    职级说了算——它返回 None＝本部门无人达主管级＝无头；职级层报错则向上抛，由交付门拒绝放行。"""
    人名 = str(人名 or "").strip()
    import 职级
    return 职级.部门头(人名)


def 是部门头(人名: str) -> bool:
    """这个人是不是他自己部门的头。"""
    return bool(人名) and 部门头(人名) == str(人名).strip()


def 有跨部门权(人名: str) -> bool:
    """有没有跨部门权（整合/派活/汇总）＝此刻是不是握审批权的经理（职级驱动，2026-07-10）。
    职级是唯一真相（经理被顶下来就没权了）；职级账不可读时拒绝用部门静态 flag 绕行。"""
    人名 = str(人名 or "").strip()
    if not 人名:
        return False
    try:
        import 经理代理
        if 经理代理.是临时统筹(人名):
            return True
    except Exception:  # noqa: BLE001
        pass
    import 职级
    return 职级.是经理(人名)


def _岗位人名表() -> dict[str, str]:
    """严格读取当前花名册；权限和复核选择不允许恢复固定旧人。"""
    import 模型接入

    花 = 模型接入.花名册()
    if not 花:
        raise RuntimeError("花名册为空，拒绝选择复核人")
    return {
        str(岗): str((配置 or {}).get("名字") or "").strip()
        for 岗, 配置 in 花.items()
        if str((配置 or {}).get("名字") or "").strip()
    }


def 部门复核替补(被复核人: str, 排除人名=()) -> tuple[str | None, str | None]:
    """部门头与被复核人冲突或部门暂无合格头时，从该部门规则牌选独立替补。"""
    部 = 认领部门(被复核人)
    if 部 is None:
        raise RuntimeError(f"{被复核人}没有唯一部门，拒绝绕行部门复核")
    人表 = _岗位人名表()
    排除 = {str(x).strip() for x in 排除人名 if str(x).strip()}
    排除.add(str(被复核人 or "").strip())
    for 岗 in list(getattr(部, "复核替补", []) or []):
        人 = 人表.get(str(岗))
        if 人 and 人 not in 排除:
            return str(岗), 人
    return None, None


def 经理复核替补(排除人名=()) -> tuple[str | None, str | None]:
    """经理本人或上一层复核人就是现任经理时，选择规则牌登记的代理经理复核人。"""
    经理部门 = [d for d in 各部门() if getattr(d, "跨部门权", False)]
    if len(经理部门) != 1:
        raise RuntimeError(f"经理部门数量应为1，实际为{len(经理部门)}")
    人表 = _岗位人名表()
    排除 = {str(x).strip() for x in 排除人名 if str(x).strip()}
    for 岗 in list(getattr(经理部门[0], "代理经理复核", []) or []):
        人 = 人表.get(str(岗))
        if 人 and 人 not in 排除:
            return str(岗), 人
    return None, None


def 同部门(甲: str, 乙: str) -> bool:
    da, db = 认领部门(甲), 认领部门(乙)
    return da is not None and da is db


def 层级名(人名: str) -> str:
    """这个人在公司层级里算哪一层，给轨迹/时间轴标层用：经理 / 部门头 / <岗位>（员工）。"""
    if 有跨部门权(人名):
        return "经理"
    if 是部门头(人名):
        return "部门头"
    return _人名到岗位(人名) or "员工"


def 可派活(派活人: str, 被派人: str) -> bool:
    """分派门：谁能给谁派活。规则——经理办公室(跨部门权)→任何人；部门头→本部门的非头成员；否则不行。下级不能给上级派活。"""
    派活人, 被派人 = str(派活人 or "").strip(), str(被派人 or "").strip()
    if not 派活人 or not 被派人 or 派活人 == 被派人:
        return False
    if 有跨部门权(派活人):                      # 经理办公室的人：跨部门派任何人——但被派人必须是花名册里的真员工（防幽灵派活）
        return _人名到岗位(被派人) is not None
    if 是部门头(派活人) and 同部门(派活人, 被派人) and not 是部门头(被派人):
        return True                             # 部门头：只能派本部门的『员』（不能派另一个头/别部门/上级）
    return False


if __name__ == "__main__":
    print("已装的部门：", [f"{d.目录名}(收{d.收})" for d in 各部门()])
    for n in ("老钟", "老梁", "阿强", "阿言", "老纪", "陌生人"):
        print(f"  {n:4} → {个人记忆路径(n).relative_to(COMPANY)}")
