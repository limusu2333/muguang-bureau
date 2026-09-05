#!/usr/bin/env python3
"""活公司产出的待验收记录（仿大厅记录/会议室记录，死平台场所内生功能）。

依据《公司的真谛》⑫：场所里发生的内容忠实记录，这是场所自带的功能。
护栏丙②：活公司产出 → 标"待验收"进右栏 → 船主批了才算交付 → 可打回。
护栏丙③ v1：批准/打回时大厅留痕；PM 不自动 resume，船主再发一句触发重做。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import threading
import uuid
from pathlib import Path

from 根 import 产品根, 代码根, 工作根, 数据根

COMPANY = 数据根
待验收目录 = COMPANY / "待验收"
# 批准/打回(读全表→内存改一条→整文件重写)与 新增(append)在多线程HTTP下并发会互相盖写、静默丢单——所有写这把锁串行（记忆层同类bug的教训）
_锁 = threading.RLock()
可验根 = tuple(dict.fromkeys(root.resolve() for root in (数据根, 工作根, 产品根)))


def _路径(path: str) -> Path:
    p = Path(str(path or "")).expanduser()
    if not p.is_absolute():
        p = COMPANY / p
    p = p.resolve()
    if not any(p.is_relative_to(root) for root in 可验根 if root.exists()):
        raise ValueError(f"交付文件不在用户数据区或工作区内：{p}")
    return p


def _指纹(path: str) -> dict:
    p = _路径(path)
    if not p.is_file():
        raise FileNotFoundError(f"交付文件不存在或不是普通文件：{p}")
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    st = p.stat()
    try:
        显示 = str(p.relative_to(COMPANY))
    except ValueError:
        try:
            显示 = "work:" + str(p.relative_to(工作根))
        except ValueError:
            显示 = "product:" + str(p.relative_to(产品根))
    return {"路径": 显示, "sha256": h.hexdigest(), "大小": st.st_size, "mtime_ns": st.st_mtime_ns}


def 文件快照(files: list[str] | None) -> list[dict]:
    return [_指纹(str(p)) for p in dict.fromkeys(str(x) for x in (files or []) if str(x).strip())]


def _git提交() -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(代码根), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=3, check=True,
        )
        return r.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def 核验文件(record: dict) -> list[str]:
    """批准前核对交付时的文件内容版本。返回差异说明，空表示完全一致。"""
    差: list[str] = []
    for old in record.get("文件快照") or []:
        try:
            cur = _指纹(str(old.get("路径") or ""))
        except Exception as e:  # noqa: BLE001
            差.append(f"{old.get('路径')}: {type(e).__name__}: {e}")
            continue
        if cur.get("sha256") != old.get("sha256") or cur.get("大小") != old.get("大小"):
            差.append(f"{old.get('路径')}: 内容已变化（提交 {old.get('sha256', '')[:10]}，当前 {cur.get('sha256', '')[:10]}）")
    return 差


def _今天文件() -> Path:
    待验收目录.mkdir(parents=True, exist_ok=True)
    return 待验收目录 / f"{dt.date.today()}.jsonl"


def _所有记录() -> list[dict]:
    """读全部历史记录（所有日期文件，从旧到新）。"""
    records: list[dict] = []
    if not 待验收目录.exists():
        return records
    for p in sorted(待验收目录.glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except Exception as e:  # noqa: BLE001
                    try:
                        from 状态存储 import 登记故障
                        登记故障(p, f"JSONL 第 {len(records) + 1} 行损坏：{type(e).__name__}: {e}")
                    except Exception:  # noqa: BLE001
                        pass
    return records


def _重写文件(日期文件: Path, records: list[dict]) -> None:
    # 原子写（体检P2：整文件重写崩在半截=当天验收记录损坏）：先写临时件再rename顶上
    临时 = 日期文件.with_suffix(".jsonl.tmp")
    临时.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records if r) + "\n",
        encoding="utf-8",
    )
    临时.replace(日期文件)


def 开始(岗位: str, 任务: str, *, 部门头: str | None = None, 独立复核: str | None = None,
       收敛经理: str | None = None, 轨迹: list | None = None, 来源: str = "交付", rid: str = "",
       任务组: str = "", 难度: int = 1) -> str:
    """任务开工前先登记一条“进行中交付”，返回记录 id。"""
    rid = str(rid or uuid.uuid4().hex[:10])
    record = {
        "schema": 3,
        "id": rid,
        "时间": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "岗位": 岗位,
        "任务": (任务 or "").strip()[:100],
        "产物": "",
        "文件": [],
        "文件快照": [],
        "Git提交": _git提交(),
        "部门头": (部门头 or "").strip(),
        "独立复核": (独立复核 or "").strip(),
        "收敛经理": (收敛经理 or "").strip(),
        "轨迹": list(轨迹 or []),
        "来源": (来源 or "交付").strip(),
        "任务组": str(任务组 or rid).strip()[:100],
        "难度": max(1, min(5, int(难度 or 1))),
        "状态": "进行中",
        "批注": "",
        "批注时间": "",
        "裁决历史": [],
        "副作用": {},
    }
    with _锁:
        with _今天文件().open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return rid


def 提交(rid: str, 产物描述: str, 文件: list[str] | None = None, *, 部门头: str | None = None,
       独立复核: str | None = None, 收敛经理: str | None = None, 轨迹: list | None = None) -> str:
    """把“进行中交付”提交为待验收，并保存内容指纹。"""
    snap = 文件快照(文件)
    with _锁:
        old = _按id(rid)
        if old is None:
            raise FileNotFoundError(f"进行中交付不存在：{rid}")
        if old.get("状态") != "进行中":
            raise RuntimeError(f"交付状态不是进行中，不能提交：{old.get('状态')}")
        rec = dict(old)
        rec["产物"] = (产物描述 or "").strip()[:400]
        rec["文件"] = [str(f) for f in (文件 or [])][:20]
        rec["文件快照"] = snap
        rec["Git提交"] = _git提交()
        rec["部门头"] = (部门头 if 部门头 is not None else rec.get("部门头") or "").strip()
        rec["独立复核"] = (独立复核 if 独立复核 is not None else rec.get("独立复核") or "").strip()
        rec["收敛经理"] = (收敛经理 if 收敛经理 is not None else rec.get("收敛经理") or "").strip()
        if 轨迹 is not None:
            rec["轨迹"] = list(轨迹)
        rec["状态"] = "待验收"
        rec["提交时间"] = dt.datetime.now().isoformat(timespec="seconds")
        if not _替换记录(rid, rec):
            raise FileNotFoundError(f"交付提交时记录消失：{rid}")
    return rid


def 标记异常(rid: str, 说明: str) -> bool:
    with _锁:
        rec = _按id(rid)
        if rec is None:
            return False
        rec = dict(rec)
        rec["状态"] = "被门禁拦住"
        rec["异常"] = str(说明 or "")[:500]
        rec["异常时间"] = dt.datetime.now().isoformat(timespec="seconds")
        return _替换记录(rid, rec)


def 新增(岗位: str, 任务: str, 产物描述: str, 文件: list[str] | None = None, 部门头: str | None = None,
       独立复核: str | None = None, 收敛经理: str | None = None, 轨迹: list | None = None, 来源: str = "交付",
       任务组: str = "", 难度: int = 1) -> str:
    """兼容入口：先登记进行中，再提交为待验收。

    文件 = 干活期间真写/改过的文件路径（平台从工具调用轨迹收集的事实，不是模型自述）——
    船主验收时要看得见"到底动了哪些文件"，不能只拍描述（2026-07-02 彻查补）。
    部门头 = 放行这条交付的部门头人名；收敛经理 = 收敛放行这条的经理人名。
    验收时据此给他俩记『把关准/放水』（闭合问责环）。单人部门/无上级/经理自派为 None。
    """
    rid = 开始(
        岗位, 任务, 部门头=部门头, 独立复核=独立复核, 收敛经理=收敛经理,
        轨迹=轨迹, 来源=来源, 任务组=任务组, 难度=难度,
    )
    return 提交(rid, 产物描述, 文件, 部门头=部门头, 独立复核=独立复核, 收敛经理=收敛经理, 轨迹=轨迹)


def _按id(rid: str) -> dict | None:
    for r in _所有记录():
        if str(r.get("id") or "") == str(rid or ""):
            return r
    return None


def _像成果(text: str) -> bool:
    """粗判一段直答是不是『成果类』（值得留痕验收），不是就当聊天/应答/提问，别留痕。
    要够长 + 不是纯提问 + 带成果标记（代码块/完成词/结构化产出），三条都过才算。"""
    t = str(text or "").strip()
    if len(t) < 120:
        return False
    if t.rstrip().endswith(("？", "?")):   # 纯提问不算成果
        return False
    标记 = ("```", "改好了", "写好了", "完成了", "做完了", "已完成", "报告如下", "方案如下",
            "整理如下", "已实现", "已修复", "结论：", "总结：", "步骤：", "补丁", "diff", "def ", "函数")
    return any(m in t for m in 标记)


def 半强制留痕(岗位: str, 原话: str, 成果文: str) -> str | None:
    """大厅直答里若产出像成果，半强制补一条『可选验收』记录（船主可看可不看）。
    非成果不留；近5分钟同一原话已有记录则不重复（防和交付链的正式记录撞车）。返回 rid 或 None。"""
    try:
        if not _像成果(成果文):
            return None
        指纹 = str(原话 or "").strip()[:20]
        if 指纹:
            近5分 = dt.datetime.now() - dt.timedelta(minutes=5)
            for r in _所有记录():
                try:
                    t = dt.datetime.strptime(str(r.get("时间") or "")[:16], "%Y-%m-%d %H:%M")
                except Exception:  # noqa: BLE001
                    continue
                if t >= 近5分 and 指纹 and 指纹 in str(r.get("任务") or ""):
                    return None   # 这轮已经留过痕（多半是交付链正式记录），别再补可选的
        return 新增(岗位, f"[大厅直答] {str(原话 or '').strip()[:56]}", 成果文, 来源="大厅直答·可选验收")
    except Exception:  # noqa: BLE001 留痕失败不拖累大厅回话
        return None


def 列表(仅待验收: bool = True) -> list[dict]:
    """返回待验收记录列表（默认只返回状态=待验收的）。"""
    records = _所有记录()
    if 仅待验收:
        return [r for r in records if r.get("状态") == "待验收"]
    return records


def _替换记录(rid: str, 新记录: dict) -> bool:
    """把 id=rid 的记录替换成给定快照，返回是否找到。"""
    with _锁:
        if not 待验收目录.exists():
            return False
        for p in sorted(待验收目录.glob("*.jsonl")):
            records = []
            found = False
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if r.get("id") == rid:
                    r = dict(新记录)
                    found = True
                records.append(r)
            if found:
                _重写文件(p, records)
                return True
        return False


def _更新状态(rid: str, 新状态: str, 批注: str) -> dict | None:
    """找到 id=rid 的记录、更新状态，返回更新前快照；找不到返回 None。"""
    with _锁:
        if not 待验收目录.exists():
            return None
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        for p in sorted(待验收目录.glob("*.jsonl")):
            records = []
            旧记录: dict | None = None
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if r.get("id") == rid:
                    旧记录 = dict(r)
                    旧记录["轨迹"] = list(r.get("轨迹") or [])   # 3M4 三审：轨迹深拷一份——否则和 r 共享同一 list，下面 append 会污染旧记录，幂等门还原出幻影节点
                    r["状态"] = 新状态
                    r["批注"] = 批注
                    r["批注时间"] = now
                    r.setdefault("裁决历史", []).append({"时间": now, "状态": 新状态, "批注": 批注})
                    r.setdefault("轨迹", []).append({
                        "时间": now, "层级": "船主", "谁": "你",
                        "动作": ("批准" if 新状态 == "已批" else "打回") + (f"「{批注}」" if 批注 else ""),
                    })
                records.append(r)
            if 旧记录 is not None:
                _重写文件(p, records)
                return 旧记录
        return None


def _裁决进记忆(记录: dict, 裁决: str, 批注: str) -> None:
    """成长闭环（圣域⑦，2026-07-02 补）：船主的验收裁决写进干活人的记忆——
    被打回的教训、被批准的交付，都是这个人"越干越懂"的原料。只记事实，失败静默（不拖累验收本身）。"""
    from 活_本人 import _岗位的人, 给某人记一笔
    岗位 = str(记录.get("岗位") or "")
    任务 = str(记录.get("任务") or "")[:80]
    if 岗位:
        给某人记一笔(
            _岗位的人(岗位), f"[验收{裁决}] {任务}" + (f"｜船主批注：{批注}" if 批注 else ""),
            键=f"验收:{记录.get('id')}:{裁决}",
        )


def _记信誉(记录: dict, 事件: str, 详情: str = "") -> None:
    """把验收结果同步进当事人信誉账（奖惩燃料：验收通过=奖、被打回=惩）。只记事实，失败静默。"""
    import 信誉
    from 活_本人 import _岗位的人
    岗位 = str(记录.get("岗位") or "")
    任务 = str(记录.get("任务") or "")[:40]
    rid = str(记录.get("id") or "")
    任务组 = str(记录.get("任务组") or rid)
    难度 = max(1, min(5, int(记录.get("难度") or 1)))
    if 岗位:
        人 = _岗位的人(岗位)
        if 事件 == "交付被打回":
            信誉.记打回(
                人, 任务, 键=f"{rid}:doer", 把关者="船主",
                工作批次=任务组, 难度=难度,
            )
            期望键 = f"{rid}:doer:打回"
        else:
            有返工 = any(
                str(r.get("id") or "") != rid
                and str(r.get("任务组") or r.get("id") or "") == 任务组
                and str(r.get("岗位") or "") == 岗位
                and str(r.get("状态") or "") == "已打回"
                for r in _所有记录()
            )
            信誉.记任务通过(
                人, 任务, 键=f"{rid}:doer", 工作批次=任务组,
                难度=难度, 一次通过=not 有返工,
            )
            期望键 = f"{rid}:doer"
        if not any(str(e.get("键") or "") == 期望键 for e in 信誉._事件列(人)):
            raise RuntimeError(f"信誉落账失败：{人}/{事件}/{期望键}")
    头事件 = "把关准" if 事件 == "验收通过" else ("放水被抓" if 事件 == "交付被打回" else "")
    if 头事件:
        for who in (
            str(记录.get("部门头") or "").strip(),
            str(记录.get("独立复核") or "").strip(),
            str(记录.get("收敛经理") or "").strip(),
        ):
            if who:
                信誉.记把关结果(
                    who, 事件 == "验收通过",
                    f"放行的活{'验收通过' if 事件 == '验收通过' else '被验收打回'}：{任务}",
                    键=f"{rid}:{who}", 工作批次=任务组, 难度=难度,
                )
                if not any(str(e.get("键") or "") == f"{rid}:{who}" for e in 信誉._事件列(who)):
                    raise RuntimeError(f"把关信誉落账失败：{who}/{头事件}")


def _验收后(记录: dict, 成功: bool) -> None:
    """验收后处理：把这笔任务结果喂进当事人的**晋升曲线**（成功=验收通过、失败=打回）——
    达标进 5 笔试用、过了试用才升级（升职是长跑、事件驱动）；再只检查当事人的**降职**条件。逐条落大厅公告。失败静默。"""
    import 职级
    from 活_本人 import _岗位的人
    人 = _岗位的人(str(记录.get("岗位") or "")) or ""
    rid = str(记录.get("id") or "")
    结果键 = f"验收:{rid}:{'通过' if 成功 else '打回'}"
    公告: list[str] = []
    升成了 = ""
    if 人:
        import 晋升
        import 信誉
        if 晋升.已处理(人, 结果键):
            return
        成果分 = None
        if 成功:
            成果分 = 信誉.任务结算分(人, f"{rid}:doer")
            if 成果分 is None:
                raise RuntimeError(f"晋升结算前缺少信誉任务结算：{rid}")
        ev = 晋升.记交付(
            人, 职级.评级(人), bool(成功), 结果键=结果键, 成果分=成果分,
        )
        if ev == "晋升就绪":
            r = 职级.升(人)
            if r:
                晋升.清曲线(人)
                升成了 = 人
                公告.append(r.get("说明") or f"{人} 晋升")
        elif ev == "达标冻结":
            公告.append(f"{人} 晋升分达标，进 5 笔试用（保持准确才升，错到 2 笔清零）")
        elif ev == "清零":
            公告.append(f"{人} 试用期错太多，晋升曲线清零、从头爬")
    for c in 职级.扫降职(人):
        if c.get("人") == 升成了:
            continue
        公告.append(c.get("说明") or f"{c.get('人')} 降为 {c.get('到')}")
    if 公告:
        import 大厅记录
        for i, 话 in enumerate(公告):
            大厅记录.记一句("公司", f"【职级变动·自动】{话}", 事件键=f"{结果键}:公告:{i}")


def _安排副作用(record: dict, 裁决: str) -> dict:
    effects = {"大厅留痕": "待处理"}
    if str(record.get("来源") or "交付") == "交付":
        effects.update({"个人记忆": "待处理", "信誉": "待处理", "晋升职级": "待处理"})
    return effects


def 处理副作用(rid: str) -> dict:
    """重试一条已裁决记录的未完成副作用。每项都有交付 id 幂等键。"""
    with _锁:
        record = _按id(rid)
        if record is None:
            raise FileNotFoundError(f"待验收记录不存在：{rid}")
        状态 = str(record.get("状态") or "")
        if 状态 not in ("已批", "已打回"):
            return record
        裁决 = "批准" if 状态 == "已批" else "打回"
        成功 = 状态 == "已批"
        批注 = str(record.get("批注") or "")
        effects = dict(record.get("副作用") or _安排副作用(record, 裁决))
        for name, status in list(effects.items()):
            if status == "已完成":
                continue
            try:
                if name == "大厅留痕":
                    import 大厅记录
                    大厅记录.记一句("船主", f"【验收{裁决}】#{rid[:6]}" + (f" {批注}" if 批注 else ""), 事件键=f"验收:{rid}:大厅:{裁决}")
                elif name == "个人记忆":
                    _裁决进记忆(record, 裁决, 批注)
                elif name == "信誉":
                    _记信誉(record, "验收通过" if 成功 else "交付被打回", 批注)
                elif name == "晋升职级":
                    _验收后(record, 成功)
                effects[name] = "已完成"
                effects.pop(name + "错误", None)
            except Exception as e:  # noqa: BLE001
                effects[name] = "待重试"
                effects[name + "错误"] = f"{type(e).__name__}: {e}"[:500]
            record["副作用"] = effects
            _替换记录(rid, record)
        return record


def 副作用待处理(rid: str) -> list[str]:
    r = _按id(rid) or {}
    return [k for k, v in (r.get("副作用") or {}).items() if not k.endswith("错误") and v != "已完成"]


def 批准(rid: str, 批注: str = "") -> bool:
    """船主批准交付 → 状态改已批，大厅留痕（护栏丙③ v1：批准后 PM 不自动 resume）。"""
    cur = _按id(rid)
    if cur is None:
        return False
    if str(cur.get("状态") or "") == "待验收":
        差 = 核验文件(cur)
        if 差:
            raise RuntimeError("验收对象自提交后已变化，不能批准：" + "；".join(差[:5]))
    旧记录 = _更新状态(rid, "已批", 批注)
    if 旧记录 is None:
        return False
    if str(旧记录.get("状态") or "") in ("已批", "已打回"):   # 幂等门：已终态＝重复裁决，撤销这次重写、绝不重跑奖惩/晋升/留痕
        _替换记录(rid, 旧记录)
        return True
    cur = _按id(rid) or {}
    cur["副作用"] = _安排副作用(cur, "批准")
    _替换记录(rid, cur)
    处理副作用(rid)
    return True


def 打回(rid: str, 批注: str = "") -> bool:
    """船主打回 → 状态改已打回，大厅留痕（v1：PM 不自动 resume，船主再发一句触发重做）。"""
    旧记录 = _更新状态(rid, "已打回", 批注)
    if 旧记录 is None:
        return False
    if str(旧记录.get("状态") or "") in ("已批", "已打回"):   # 幂等门：已终态＝重复裁决，撤销这次重写、绝不重跑奖惩/晋升/留痕
        _替换记录(rid, 旧记录)
        return True
    cur = _按id(rid) or {}
    cur["副作用"] = _安排副作用(cur, "打回")
    _替换记录(rid, cur)
    处理副作用(rid)
    return True
