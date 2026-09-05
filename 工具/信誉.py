#!/usr/bin/env python3
"""死平台 · 信誉 —— 把关·交付·验收的结果都记这，算出信任等级，回喂上下文。

为什么有这套（2026-07-09，船主拍板）：模型不吃钱和罚款，但吃"进它上下文的评价"——RLHF 就是拿评价矫正它。
所以奖惩＝把"评价闭环"在推理期用它读得到的上下文重建：每次把关/交付/验收的**结果**记进这本信誉账，
算出信任等级，唤醒时注入他的上下文（他带着"上次放水被抓/上次把关准"来干活）。

规则牌：
- **能力**：谁想知道某人此刻的信任等级/画像，来这查（画像/信任等级）；唤醒时自动注入本人。
- **权限**（焊平台）：只由"真实结果"落账——验收批准/打回、部门头把关准/放水，自动记；**没人能手工刷分**。
- **可见性**：逐条落 `信誉/<人名>.jsonl`，信任等级公开上公告栏（自尊维度）。
- **诚实边界**：只记"结果"不记情绪；等级只由账算，不靠谁说了算。
- **复核纪律**：部门头、经理、船主的既定汇报链对所有人一视同仁。高信誉不能免复核、不能越级；
  受限信誉只会在原有汇报链之外增加一次独立复核。
"""
from __future__ import annotations

import datetime as dt
import json
import math
import threading
from pathlib import Path

from 根 import 数据根

COMPANY = 数据根
信誉目录 = COMPANY / "信誉"
_锁 = threading.RLock()   # 并发派活时同名账(老钟)会同时append；重做/重复过门会堆同一件事——写这把锁串行 + 按键去重。RLock 可重入：记打回 持锁再调 记一笔（2026-07-10 二审）

# 事件 → 权重（评"过程质量"不只"结果"；正＝奖，负＝惩。只认这些真实结果，没人能手工刷分）
事件权重 = {
    # 奖：做得扎实、把关准
    "扎实一次过": 3,     # 一次过验收、没返工——该查都查、不马虎
    "验收通过": 2,       # 交付被船主批准
    "挡下真问题": 2,     # 独立复核/测试真抓出问题
    "把关准": 1,         # 放行的活后来验收通过
    "整合一次过": 1,
    # 惩：偷懒、马虎、乱把关
    "偷懒被抓": -4,      # 该查没查/编数据/敷衍，被审核或独立复核抓出——最重
    "放水被抓": -3,      # 放行的活被验收/测试毙
    "反复敷衍": -3,
    "交付被打回": -2,
    "误打回": -2,        # 打回的活其实没问题——防把关者一律打回保平安
}
_工作半衰批次 = 14.0   # 只有本人后续真实工作结算才推进：约 14 个工作批次后，旧结果权重减半
_休眠最多回落 = 0.05   # 公司未开工时仍会缓慢回落，但总共最多掉 5%，随后近乎停滞
_休眠趋稳天 = 3.0      # 约三天后回落速度已明显放慢
_休眠宽限天 = 1.0      # 正常隔夜不算长期停工，避免每天开工也叠加休眠损耗
# 升降职门槛已移到 职级.py（按级别分档：升职线/维持线，全靠这套衰减当尺）——信誉本模块只管信任/自主度，不再管级别


def _账(人名: str) -> Path:
    安全 = str(人名 or "").replace("/", "_").replace("\\", "_").replace("..", "_").strip() or "_未知"
    return 信誉目录 / f"{安全}.jsonl"


def 记一笔(人名: str, 事件: str, 详情: str = "", 键: str = "", 把关者: str = "", *,
       权重覆盖: float | None = None, 工作批次: str = "", 难度: int = 0, 质量: str = "", 角色: str = "") -> None:
    """落一条真实结果进信誉账。事件必须在 事件权重 里，否则不记（防乱刷）。
    键＝事件指纹（如待验收 id）：给了就去重——同一（事件,键）只记一次，防重做/重复过门把同一件事堆好几笔。失败静默。
    把关者＝这笔『交付被打回』是谁打的回（部门头/经理名），落进记录——误打回平反时靠它精确定位那笔（十审#3）。"""
    人名 = str(人名 or "").strip()
    if not 人名 or 事件 not in 事件权重:
        return
    键 = str(键 or "").strip()
    把关者 = str(把关者 or "").strip()
    with _锁:   # 并发/重做下串行写 + 去重
        try:
            if 键:
                for e in _事件列(人名):
                    if e.get("事件") == 事件 and str(e.get("键") or "") == 键:
                        return   # 这件事这个事件已记过，不重复
            信誉目录.mkdir(parents=True, exist_ok=True)
            记 = {
                "时间": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                "事件": 事件,
                "权重": float(事件权重[事件] if 权重覆盖 is None else 权重覆盖),
                "详情": (详情 or "").strip()[:80],
                "键": 键,
            }
            if 把关者:
                记["把关者"] = 把关者
            if 工作批次:
                记["工作批次"] = str(工作批次)[:100]
            if 难度:
                记["难度"] = max(1, min(5, int(难度)))
            if 质量:
                记["质量"] = str(质量)[:20]
            if 角色:
                记["角色"] = str(角色)[:20]
            with _账(人名).open("a", encoding="utf-8") as f:
                f.write(json.dumps(记, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 记账失败不许拖累主流程
            pass


def 记打回(人名: str, 详情: str = "", 键: str = "", 把关者: str = "", *,
       工作批次: str = "", 难度: int = 0, 质量: str = "打回") -> None:
    """记一笔『交付被打回』；若近期反复被打回（近8条里≥3笔）再追记一笔『反复敷衍』（更重的惩）。
    堵"敷衍没代价"：单次打回 -2，反复敷衍再 -3。把关者＝谁打的回（供误打回平反精确定位，十审#3）。
    2026-07-10 二审修：反复敷衍**一段窗口只记一次、不滚雪球**（近窗里已有反复敷衍就不再追）；整段进同一把可重入锁、并发不重复追。"""
    with _锁:
        记一笔(
            人名, "交付被打回", 详情, 键=(f"{键}:打回" if 键 else ""), 把关者=把关者,
            工作批次=工作批次, 难度=难度, 质量=质量, 角色="交付",
        )
        近8 = _事件列(人名)[-8:]
        近打回 = sum(1 for e in 近8 if e.get("事件") == "交付被打回" and not e.get("撤销"))   # 作废的(误打回纠正)不计入反复敷衍
        近敷衍 = sum(1 for e in 近8 if e.get("事件") == "反复敷衍" and not e.get("撤销"))
        if 近打回 >= 3 and 近敷衍 == 0:   # 刚踩到"反复"线、且这轮还没记过敷衍 → 只补这一次，不每次打回都追
            记一笔(人名, "反复敷衍", f"近期被打回{近打回}次", 键=(f"{键}:敷衍" if 键 else ""))


def _任务奖励上限(难度: int) -> float:
    """一个完整任务批次可挣的最高信誉分：小活 1 分，最高难度 3 分。"""
    return 0.5 + 0.5 * max(1, min(5, int(难度 or 1)))


def 记任务通过(人名: str, 详情: str, *, 键: str, 工作批次: str, 难度: int, 一次通过: bool) -> float:
    """按难度和完成质量结算一次交付，返回这次真正计入的分。

    同一个人把同一批工作拆成多张小单，所有小单共享一个奖励上限；拆单不会把总分变多。
    """
    人名 = str(人名 or "").strip()
    批次 = str(工作批次 or 键 or "").strip()
    难度 = max(1, min(5, int(难度 or 1)))
    原始分 = _任务奖励上限(难度) * (1.0 if 一次通过 else 0.7)
    with _锁:
        同批正分 = [
            e for e in _事件列(人名)
            if not e.get("撤销") and e.get("角色") == "交付"
            and str(e.get("工作批次") or "") == 批次 and _权重(e) > 0
        ]
        已给 = sum(_权重(e) for e in 同批正分)
        最高难度 = max([难度] + [int(e.get("难度") or 1) for e in 同批正分])
        批次一次过 = 一次通过 and all(e.get("质量") != "返工后通过" for e in 同批正分)
        批次上限 = _任务奖励上限(最高难度) * (1.0 if 批次一次过 else 0.7)
        可给 = max(0.0, 批次上限 - 已给)
        实给 = round(min(原始分, 可给), 3)
        记一笔(
            人名, "验收通过", 详情, 键=键, 权重覆盖=实给,
            工作批次=批次, 难度=难度,
            质量=("一次通过" if 一次通过 else "返工后通过"), 角色="交付",
        )
        return 实给


def 任务结算分(人名: str, 键: str) -> float | None:
    """读取已落账的任务结算分，供晋升曲线复用同一份事实。"""
    for e in reversed(_事件列(人名)):
        if str(e.get("键") or "") == str(键 or "") and e.get("角色") == "交付":
            return max(0.0, _权重(e))
    return None


def 记把关结果(人名: str, 通过: bool, 详情: str, *, 键: str, 工作批次: str, 难度: int) -> None:
    """结算复核人的把关结果；同一批拆出的多张小单，正向把关奖合计最多 1 分。"""
    批次 = str(工作批次 or 键 or "").strip()
    if not 通过:
        记一笔(
            人名, "放水被抓", 详情, 键=键, 工作批次=批次,
            难度=难度, 质量="放水", 角色="审核",
        )
        return
    with _锁:
        已给 = sum(
            max(0.0, _权重(e)) for e in _事件列(人名)
            if not e.get("撤销") and e.get("角色") == "审核"
            and str(e.get("工作批次") or "") == 批次
        )
        记一笔(
            人名, "把关准", 详情, 键=键, 权重覆盖=max(0.0, 1.0 - 已给),
            工作批次=批次, 难度=难度, 质量="把关准确", 角色="审核",
        )


def _标撤销(人名: str, 事件: str, 键: str = "", 把关者: str = "") -> bool:
    """把某人账里『最近一笔』匹配(事件[,键][,把关者])的记录标 撤销=True——从算分/反复敷衍计数里排除，但留档可查（失效非删除）。返回是否标到。
    把关者匹配（十审#3）：给了把关者时，只撤『这笔正是该把关者打的回』；但记录里**没落把关者的旧账**放行（向后兼容，仍按最近一笔撤）。"""
    人名 = str(人名 or "").strip()
    把关者 = str(把关者 or "").strip()
    with _锁:
        p = _账(人名)
        if not 人名 or not p.exists():
            return False
        行们 = p.read_text(encoding="utf-8").splitlines()
        for i in range(len(行们) - 1, -1, -1):   # 从最近往前找第一笔匹配的
            ln = 行们[i].strip()
            if not ln:
                continue
            try:
                e = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
            _该笔把关者 = str(e.get("把关者") or "")
            _把关者对得上 = (not 把关者) or (not _该笔把关者) or (_该笔把关者 == 把关者)   # 没给把关者过滤/旧账没落把关者→放行；落了就得对上
            if e.get("事件") == 事件 and (not 键 or str(e.get("键") or "") == 键) and _把关者对得上 and not e.get("撤销"):
                e["撤销"] = True
                行们[i] = json.dumps(e, ensure_ascii=False)
                tmp = p.with_suffix(".jsonl.tmp")
                tmp.write_text("\n".join(x for x in 行们 if x.strip()) + "\n", encoding="utf-8")
                tmp.replace(p)
                return True
        return False


def _复核反复敷衍(人名: str) -> None:
    """重新核对某人所有『反复敷衍』还站不站得住：一笔敷衍是当初『它前面那 8 条事件里有 ≥3 笔有效打回』才记的
    （见 记打回）。若如今有支撑打回被误打回纠正、作废了，导致它前面那 8 条里有效打回不足 3 笔，这笔敷衍就失去依据、一并作废。
    **只作废、绝不新增**（新增是 记打回 的事）。
    为什么用『日志里它前面那 8 条』这个**固定窗口**、而不是『当下最近 8 条』：日志 append-only，一笔敷衍前面的 8 条永远是那 8 条、
    不随后续新打回漂移——4M2 四审改用滑动近8，被无关的近期打回撑进/把支撑打回挤出窗口，两个方向都算错（撤过头/该撤没撤）；
    2026-07-11 五审揪出、改这个稳定窗口。"""
    人名 = str(人名 or "").strip()
    with _锁:
        p = _账(人名)
        if not 人名 or not p.exists():
            return
        行们 = p.read_text(encoding="utf-8").splitlines()
        解析: list[tuple[int, dict]] = []   # (原行号, 事件) —— 只收能解析的，坏行原样留着
        for idx, ln in enumerate(行们):
            t = ln.strip()
            if not t:
                continue
            try:
                解析.append((idx, json.loads(t)))
            except Exception:  # noqa: BLE001
                continue
        改 = False
        for pos, (idx, e) in enumerate(解析):
            if e.get("事件") != "反复敷衍" or e.get("撤销"):
                continue
            前8 = [x for _, x in 解析[max(0, pos - 8):pos]]   # 这笔敷衍前面那 8 条（固定、不漂移）
            有效打回 = sum(1 for x in 前8 if x.get("事件") == "交付被打回" and not x.get("撤销"))
            if 有效打回 < 3:   # 支撑塌了 → 这笔敷衍失去依据
                e["撤销"] = True
                行们[idx] = json.dumps(e, ensure_ascii=False)
                改 = True
        if 改:
            tmp = p.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(x for x in 行们 if x.strip()) + "\n", encoding="utf-8")
            tmp.replace(p)


def 误打回(把关者: str, 被冤者: str = "", 详情: str = "", 键: str = "") -> None:
    """把关者把没问题的活打回了——**把被冤那人对应的『交付被打回』标作废**、还他清白，并罚把关者（-2），防"一律打回保平安"。
    只由船主在验收/回看时判定触发（'这打回判错了'），不自动——谁对谁错得有人真看过（船主的权）。
    十审#3 改：**先按『把关者』精确撤那笔打回**（不再撤最近一笔、免得撤到别人后来正当的打回）；**只有真撤到才罚把关者、才连坐核敷衍**——
    撤不到（名字对不上/已撤过）就幂等空转，不重复扣分、不误伤（重复平反、双卡、先端点后大厅 都不再双扣）。"""
    把关者 = str(把关者 or "").strip()
    被冤者 = str(被冤者 or "").strip()
    with _锁:
        撤到 = bool(被冤者) and _标撤销(被冤者, "交付被打回", 键=(f"{键}:打回" if 键 else ""), 把关者=把关者)
        if not 撤到:
            return   # 没有『该把关者打的、未撤销的』打回可撤 → 幂等空转，别记误打回、别重复扣分
        记一笔(把关者, "误打回", 详情)
        # 撤了这笔打回后，重新核对被冤者的『反复敷衍』还站不站得住——按每笔敷衍自己前面那8条的稳定窗口判（4M2滑动窗口两头都错，五审改）。
        _复核反复敷衍(被冤者)


def _事件列(人名: str) -> list[dict]:
    p = _账(人名)
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001
                pass
    return out


def _权重(e) -> float:
    try:
        return float(e.get("权重") or 0)
    except (TypeError, ValueError):   # 坏权重(手改损坏)按0计、只丢这一条，别让整本账作废
        return 0


def _休眠倍率(e, 此刻: dt.datetime | None = None) -> float:
    """未开工只作有界的缓慢回落；时间再久也不会单靠休眠把人打成受限。"""
    此刻 = 此刻 or dt.datetime.now()
    try:
        t = dt.datetime.strptime(str(e.get("时间", ""))[:16], "%Y-%m-%d %H:%M")
        龄天 = max(0.0, (此刻 - t).total_seconds() / 86400.0 - _休眠宽限天)
        return 1.0 - _休眠最多回落 * (1.0 - math.exp(-龄天 / _休眠趋稳天))
    except Exception:  # noqa: BLE001 时间坏就按不衰减算
        return 1.0


def _批次位置(evs: list[dict]) -> tuple[dict[int, int], int]:
    """给真实工作批次编号；同一会话拆出的多张小单只算一个批次。"""
    顺序: dict[str, int] = {}
    位置: dict[int, int] = {}
    for i, e in enumerate(evs):
        batch = str(e.get("工作批次") or "").strip()
        if batch and batch not in 顺序:
            顺序[batch] = len(顺序) + 1
        # 旧账/人工裁决没有批次字段：把它放在“当时已发生的批次”位置，不能一律塞到最早，
        # 否则今天刚记的严重问题会被误判成早已过期；它本身不额外推进工作钟。
        位置[i] = 顺序.get(batch, len(顺序))
    return 位置, len(顺序)


def _算分(人名: str, *, 含休眠: bool) -> float:
    evs = sorted(_事件列(人名), key=lambda e: str(e.get("时间", "")))
    位置, 总批次 = _批次位置(evs)
    分 = 0.0
    for i, e in enumerate(evs):
        if e.get("撤销"):
            continue
        后续工作批次 = max(0, 总批次 - 位置.get(i, 0))
        工作倍率 = 0.5 ** (后续工作批次 / _工作半衰批次)
        休眠倍率 = _休眠倍率(e) if 含休眠 else 1.0
        分 = max(0.0, 分 + _权重(e) * 工作倍率 * 休眠倍率)
    return round(分, 1)


def 信誉分(人名: str) -> float:
    """大厅显示分：真实工作会正常推进旧结果回落；未开工只缓慢回落最多 5%。

    把事件**按时间先后逐笔累加、每一步触底 0**（船主 2026-07-10 令：0 最低、不存在负数）。
    为什么逐笔触底而不是求和后夹 0——砸到 0 之后的负分就『过去了』，下一个正向能干净地把你抬起来（一次正向就脱困）；
    衰减只把分往 0 拉、绝不把 0 抬起来，所以躺平洗不白、脱困只能真挣。每笔按自己的年龄衰减。"""
    return _算分(人名, 含休眠=True)


def 工作态信誉分(人名: str) -> float:
    """用于受限和升降职判定的分：不含休眠回落，避免关着公司也改变处分或职级。"""
    return _算分(人名, 含休眠=False)


def 有近期重罚(人名: str, 工作批次: int = 14) -> bool:
    """近 N 个真实工作批次内有没有『主动砸活』的重罚事件——给降职判『主动失败』用。
    只认这几类严重的，单次『交付被打回』不算（太常见、不该穿透种子保护）。"""
    evs = sorted(_事件列(人名), key=lambda e: str(e.get("时间", "")))
    位置, 总批次 = _批次位置(evs)
    for i, e in enumerate(evs):
        if e.get("撤销") or e.get("事件") not in ("偷懒被抓", "放水被抓", "反复敷衍"):
            continue
        if 总批次 - 位置.get(i, 0) <= max(0, int(工作批次)):
            return True
    return False


def 信任等级(人名: str) -> str:
    """高 / 中 / 受限。新人（无记录）默认『中』。受限只看工作态结果，不看休眠回落。
    高＝近期持续扎实；受限＝触底、公司据此加复核/被盯。得持续挣才守得住。"""
    if not _事件列(人名):
        return "中"
    分 = 工作态信誉分(人名)
    if 分 >= 3:
        return "高"
    if 分 < 1:          # 触底附近（0~0.9）＝受限：只能靠后续真实正向结果脱困
        return "受限"
    return "中"


def 需加复核(人名: str) -> bool:
    """信任『受限』时，在固定汇报链之外增加一次独立复核；不会删减或跳过任何既定层级。"""
    return 信任等级(人名) == "受限"


def 受限名单(人名们: list[str]) -> list[str]:
    """从给定人名里挑出此刻信任等级为『受限』的（工作态信誉触底附近）。只读、不落账——给管理层做掉线预警用。
    阈值不另设：直接复用 信任等级() 的『受限』判定，与信誉体系一致（2026-07-10 三审：分不再有负数，docstring 从『≤-2』改准为『<1』）。"""
    return [str(n) for n in (人名们 or []) if 信任等级(str(n)) == "受限"]


def 画像(人名: str) -> str:
    """一句话画像，给唤醒时注入上下文用（让本人读到自己的评价）。"""
    等级 = 信任等级(人名)
    evs = _事件列(人名)
    if not evs:
        return f"信任等级：{等级}（暂无记录）。"
    近 = evs[-3:]
    近文 = "；".join(str(e.get("事件")) + (f"（{e.get('详情')}）" if e.get("详情") else "") for e in 近)
    return f"信任等级：{等级}（当前 {信誉分(人名):+g} 分；休眠只缓慢回落，真实开工后旧结果才正常淡出）。最近：{近文}。"


if __name__ == "__main__":
    人 = "自检员"
    for e in ("验收通过", "验收通过", "交付被打回", "把关准"):
        记一笔(人, e, "自检")
    print("画像：", 画像(人))
    print("信任等级：", 信任等级(人), "｜需额外独立复核：", 需加复核(人))
    _账(人).unlink(missing_ok=True)  # 自检完删掉
