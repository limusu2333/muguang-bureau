#!/usr/bin/env python3
"""图记忆 · 轻量知识图谱记忆层（抄 Graphiti/Zep 的机制，用 SQLite 落地，不上图数据库）。

为什么（2026-07-06 船主拍板：三样+图谱都做）：
  散文摘要一遍遍重写会漂（第4幕坐实）；散文原话检索又连不上散在各处、隔了时间的事实。
  图谱当"能查询的索引/骨架"：事实存成 实体-关系-时间 三元组，每条指回原话(episode)，
  让三件事成立——①找得准(顺关系走、跨会话跨时间捞得到) ②认得是同一个人(别名归一)
  ③旧换新不打架(双时间轴、作废不删、只查现值视图)。**图只做索引，原话原样留、绝不替代。**

轻量路的底：SQLite 存储 + 确定性 BFS(多跳) + 视图(现值过滤)，python 内置、零外部服务。
诚实边界：抽取三元组那步(step3)要用 LLM、会偶尔编事实——那步单独做，本模块只管"结构+读写+去重+冲突+检索"，
  是纯确定性代码、离线零花钱。写入照抄 A-7 的进程内锁串行。
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from 实例配置 import 主人ID, 主人显示名, 主人称呼
from 根 import 数据根

COMPANY = 数据根
from 资料室 import 图谱库 as 库文件   # 图谱进资料室
_锁 = threading.Lock()


def _今(全: bool = True) -> str:
    d = _dt.datetime.now()
    return d.strftime("%Y-%m-%d %H:%M:%S") if 全 else d.strftime("%Y-%m-%d")


def _规整时间(值: str | None, 参考: str) -> str:
    """规整模型给的时间：剥掉误抄的 'REFERENCE_TIME=' 占位前缀，只认 YYYY / YYYY-MM / YYYY-MM-DD，
    其余(带前缀的/编的/空的)一律退回参考时间——脏时间不进库(soak 揪出 id=7 存成'REFERENCE_TIME=…')。"""
    值 = re.sub(r"^\s*REFERENCE_TIME\s*=\s*", "", (值 or "").strip()).strip()
    return 值 if re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", 值) else 参考


def _连(库: Path | None = None) -> sqlite3.Connection:
    p = 库 or 库文件
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _只读连(库: Path | None = None) -> sqlite3.Connection:
    """SQLite URI 只读连接：不切 journal_mode，并用 query_only 拦截任何误写。"""
    p = (库 or 库文件).resolve()
    c = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _归一键(x: str) -> str:
    return re.sub(r"[\s，。、·:：;；!！?？\-—()（）\"'`‘’]", "", (x or "").lower())


def _实体归一索引(c: sqlite3.Connection) -> dict[str, int]:
    """只给唯一对应的名字/别名建索引；撞名时宁可不连，避免串人。"""
    candidates: dict[str, set[int]] = {}
    for row in c.execute("SELECT id,name,aliases FROM entities"):
        names = [str(row["name"] or "")]
        try:
            names.extend(str(x) for x in (json.loads(row["aliases"] or "[]") or []))
        except Exception:  # noqa: BLE001
            pass
        for name in names:
            key = _归一键(name)
            if key:
                candidates.setdefault(key, set()).add(int(row["id"]))
    return {key: next(iter(ids)) for key, ids in candidates.items() if len(ids) == 1}


def _回填全部字面实体(c: sqlite3.Connection) -> int:
    """旧边先存 literal、同名实体后出现时补可遍历链接；原 literal 不改，补链单独留痕。"""
    index = _实体归一索引(c)
    linked = 0
    for row in c.execute("SELECT id,dst_literal FROM edges WHERE dst_entity IS NULL AND dst_literal IS NOT NULL"):
        literal = str(row["dst_literal"] or "").strip()
        entity_id = index.get(_归一键(literal))
        if not entity_id:
            continue
        c.execute(
            "INSERT OR REPLACE INTO literal_entity_links(edge_id,entity_id,literal,linked_at,reason) VALUES(?,?,?,?,?)",
            (int(row["id"]), entity_id, literal, _今(), "同名实体确定性补链"),
        )
        linked += 1
    return linked


def 建库(库: Path | None = None) -> None:
    """建三表 + FTS + 现值视图（幂等，已存在就跳过）。"""
    with _连(库) as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS episodes(
              id INTEGER PRIMARY KEY, content TEXT NOT NULL, source TEXT,
              valid_at TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS entities(
              id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
              aliases TEXT DEFAULT '[]', summary TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS edges(
              id INTEGER PRIMARY KEY,
              src INTEGER NOT NULL, rel TEXT NOT NULL,
              dst_entity INTEGER, dst_literal TEXT,     -- dst 要么连实体、要么是字面值
              fact TEXT NOT NULL, episode_id INTEGER,   -- fact=原句, episode_id=指回原话(=删除的溯源锚)
              valid_at TEXT, invalid_at TEXT,           -- 事件双时间轴：何时为真 / 何时失效
              created_at TEXT, expired_at TEXT,          -- 入库轴：录入 / 作废(删的软删)
              confidence REAL DEFAULT 1.0, status TEXT DEFAULT '现行',
              来源类 TEXT DEFAULT '推断',               -- 亲口(船主亲述/亲核) / 推断(系统抽取) / 测试 —— 防固化:只有'亲口'的准参与固化
              pinned INTEGER DEFAULT 0,                 -- 船主钉住=永不衰减/失效/淘汰(抄SSGM"受保护核心事实")
              strength REAL DEFAULT 1.0, last_hit TEXT, -- 艾宾浩斯:命中就加强(strength+1、last_hit刷新)，没人碰按 R=exp(-Δt/S) 指数衰减
              FOREIGN KEY(src) REFERENCES entities(id),
              FOREIGN KEY(dst_entity) REFERENCES entities(id),
              FOREIGN KEY(episode_id) REFERENCES episodes(id));
            CREATE INDEX IF NOT EXISTS ix_edges_src ON edges(src, rel);
            -- 黑名单(抄"依赖图+黑名单"删除法)：删过的边指纹进这，检索层兜底过滤，防某处缓存漏删的残影冒出来。
            CREATE TABLE IF NOT EXISTS 黑名单(fp TEXT PRIMARY KEY, 何时 TEXT, 因 TEXT);
            -- 旧边可能先把宾语存成 literal，同名实体后出现。补链时保留原 literal，本表记录何时/为何连上，可追溯可回放。
            CREATE TABLE IF NOT EXISTS literal_entity_links(
              edge_id INTEGER PRIMARY KEY, entity_id INTEGER NOT NULL,
              literal TEXT NOT NULL, linked_at TEXT NOT NULL, reason TEXT NOT NULL,
              FOREIGN KEY(edge_id) REFERENCES edges(id),
              FOREIGN KEY(entity_id) REFERENCES entities(id));
            -- 检索用 LIKE 子串匹配(FTS5 默认分词不认中文，单人规模 LIKE 足够、更稳)。
            -- 现值视图：没失效、没作废，才算现行事实。所有"取现值"只查它，杜绝把旧事实当现值。
            CREATE VIEW IF NOT EXISTS current_edges AS
              SELECT * FROM edges WHERE invalid_at IS NULL AND expired_at IS NULL;
            """
        )
        _回填全部字面实体(c)


# ── 实体：归一（别名认成同一个人）──────────────────────────────
def _名熵(名: str) -> float:
    from collections import Counter
    n = len(名 or "")
    if not n:
        return 0.0
    cnt = Counter(名)
    return -sum((v / n) * math.log2(v / n) for v in cnt.values())


def 解析实体(名: str, c: sqlite3.Connection) -> int | None:
    """名→实体id（抄 Graphiti 分层：①精确 ②别名精确 ③确定性模糊[entropy门控] → 都没有返回 None，交给上层/LLM）。
    确定性模糊只对**够长够稳**的名字做（熵≥1.5 且长度≥4），防'老李'这种2字短名被瞎配到别人身上。"""
    名 = (名 or "").strip()
    if not 名:
        return None
    r = c.execute("SELECT id FROM entities WHERE name=?", (名,)).fetchone()
    if r:
        return r["id"]
    for row in c.execute("SELECT id, aliases FROM entities"):
        try:
            if 名 in (json.loads(row["aliases"]) or []):
                return row["id"]
        except Exception:  # noqa: BLE001
            pass
    if len(名) >= 4 and _名熵(名) >= 1.5:  # entropy门控：短名/低熵名不自动模糊合并
        import difflib
        k = _归一文(名)
        for row in c.execute("SELECT id, name, aliases FROM entities"):
            候 = [row["name"]] + (json.loads(row["aliases"] or "[]") or [])
            for nm in 候:
                if len(nm) >= 4 and difflib.SequenceMatcher(None, k, _归一文(nm)).ratio() >= 0.9:
                    return row["id"]
    return None


def 落实体(名: str, 别名: list[str] | None = None, summary: str = "", 库: Path | None = None) -> int:
    """新建/更新实体。已存在则合并别名（只增不覆盖 name）。返回实体id。"""
    with _锁, _连(库) as c:
        建库(库)
        eid = 解析实体(名, c)
        新别名 = [a.strip() for a in (别名 or []) if a.strip() and a.strip() != 名]
        if eid is None:
            cur = c.execute("INSERT INTO entities(name,aliases,summary) VALUES(?,?,?)",
                            (名, json.dumps(sorted(set(新别名)), ensure_ascii=False), summary))
            eid = int(cur.lastrowid)
            _回填全部字面实体(c)
            return eid
        row = c.execute("SELECT aliases,summary FROM entities WHERE id=?", (eid,)).fetchone()
        旧 = set(json.loads(row["aliases"] or "[]"))
        旧.update(新别名)
        c.execute("UPDATE entities SET aliases=?, summary=COALESCE(NULLIF(?,''),summary) WHERE id=?",
                  (json.dumps(sorted(旧), ensure_ascii=False), summary, eid))
        _回填全部字面实体(c)
        return eid


# ── 原话 & 边 ─────────────────────────────────────────────────
def 落原话(content: str, source: str = "", valid_at: str | None = None, 库: Path | None = None) -> int:
    with _锁, _连(库) as c:
        建库(库)
        cur = c.execute("INSERT INTO episodes(content,source,valid_at,created_at) VALUES(?,?,?,?)",
                        (content, source, valid_at or _今(False), _今()))
        return int(cur.lastrowid)


def _归一文(x: str) -> str:
    return _归一键(x)


def 落边(src: str, rel: str, dst: str, fact: str, episode_id: int | None = None,
         valid_at: str | None = None, confidence: float = 1.0, status: str = "现行",
         单值: bool = False, 去重: bool = True, 来源类: str = "推断", 库: Path | None = None) -> int:
    """落一条事实三元组（抄 mem0 两段式：写前先搜同 (src,rel) 现行边，判 NOOP/UPDATE/ADD——治"重复灌"和"盲目覆盖"两病）。
    · 去重(NOOP)：新宾和某现行边字面重复/一方包含另一方 → 不重复写，返回那条已有边（治"拍=婚礼×45""偏好一堆重复"）。
    · 单值守卫：新值必须和现值**确实不同**且 confidence≥0.7，才作废旧换新(UPDATE)；否则不动(NOOP)——防低置信/垃圾值顶掉已确立的现值（治"居住地漂成西安""机身被泛称顶掉"）。
    · 作废不删、历史可回放。"""
    with _锁, _连(库) as c:
        建库(库)
        s = 解析实体(src, c)
        if s is None:
            s = int(c.execute("INSERT INTO entities(name) VALUES(?)", (src,)).lastrowid)
        d_ent = 解析实体(dst, c)
        dst显 = dst
        if d_ent:
            row = c.execute("SELECT name FROM entities WHERE id=?", (d_ent,)).fetchone()
            dst显 = row["name"] if row else dst
        va = valid_at or _今(False)
        新键 = _归一文(dst显)
        现有 = c.execute(
            "SELECT id, dst_entity, dst_literal, pinned FROM edges WHERE src=? AND rel=? AND invalid_at IS NULL AND expired_at IS NULL",
            (s, rel)).fetchall()

        def _旧键(r: sqlite3.Row) -> str:
            d = r["dst_literal"]
            if r["dst_entity"]:
                rr = c.execute("SELECT name FROM entities WHERE id=?", (r["dst_entity"],)).fetchone()
                d = rr["name"] if rr else d
            return _归一文(d)

        def _像(a: str, b: str) -> bool:  # 字面重复/一方包含另一方(够长才算,防"A"误配"AB")
            return bool(a and b and (a == b or (min(len(a), len(b)) >= 3 and (a in b or b in a))))

        if 去重 and 新键:
            for r in 现有:
                if _像(新键, _旧键(r)):
                    return int(r["id"])  # NOOP：已有类似的，不重复写
        if 单值 and 现有:
            if any(r["pinned"] for r in 现有):
                return int(现有[0]["id"])  # NOOP：船主钉住的单值现值，新值不许并列/挤掉
            真不同 = all(not _像(新键, _旧键(r)) for r in 现有)
            if not (真不同 and confidence >= 0.7):
                return int(现有[0]["id"])  # NOOP：不用重复/低置信值去顶掉已确立现值
            # UPDATE：确认是新值才作废旧的；但 pinned(船主钉住) 的现值永不被自动作废(抄SSGM受保护核心)
            c.execute("UPDATE edges SET invalid_at=? WHERE src=? AND rel=? AND invalid_at IS NULL AND expired_at IS NULL AND pinned=0",
                      (va, s, rel))
        cur = c.execute(
            "INSERT INTO edges(src,rel,dst_entity,dst_literal,fact,episode_id,valid_at,created_at,confidence,status,来源类,last_hit)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (s, rel, d_ent, None if d_ent else dst, fact, episode_id, va, _今(), confidence, status, 来源类, _今()))
        return int(cur.lastrowid)


def 作废边(edge_id: int, 库: Path | None = None) -> None:
    """软删：置 expired_at（删的实现——删掉后查现值查不到，但历史可回放）。"""
    with _锁, _连(库) as c:
        c.execute("UPDATE edges SET expired_at=? WHERE id=?", (_今(), edge_id))


def _有效宾实体(c: sqlite3.Connection, r: sqlite3.Row) -> int | None:
    if r["dst_entity"]:
        return int(r["dst_entity"])
    try:
        linked = c.execute("SELECT entity_id FROM literal_entity_links WHERE edge_id=?", (r["id"],)).fetchone()
        if linked:
            return int(linked["entity_id"])
    except sqlite3.Error:
        pass
    return _实体归一索引(c).get(_归一文(str(r["dst_literal"] or "")))


def _边指纹(c: sqlite3.Connection, r: sqlite3.Row) -> str:
    src = c.execute("SELECT name FROM entities WHERE id=?", (r["src"],)).fetchone()
    dst = r["dst_literal"]
    dst_entity = _有效宾实体(c, r)
    if dst_entity:
        er = c.execute("SELECT name FROM entities WHERE id=?", (dst_entity,)).fetchone()
        dst = er["name"] if er else dst
    return f"{(src['name'] if src else '')}|{r['rel']}|{_归一文(dst)}"


def 级联删(episode_id: int, 库: Path | None = None) -> int:
    """统一删除·溯源级联(抄'依赖图+黑名单')：给定一条原话 episode，把从它派生的所有边**软删**、指纹进黑名单，
    检索层从此过滤这些指纹——**一处删、处处不再冒出来**(治'同一件事多层各存、删不干净')。原话/历史仍在、可撤。返回删了几条边。"""
    with _锁, _连(库) as c:
        rows = c.execute("SELECT * FROM edges WHERE episode_id=? AND expired_at IS NULL", (episode_id,)).fetchall()
        for r in rows:
            c.execute("UPDATE edges SET expired_at=? WHERE id=?", (_今(), r["id"]))
            c.execute("INSERT OR IGNORE INTO 黑名单(fp,何时,因) VALUES(?,?,?)",
                      (_边指纹(c, r), _今(), f"级联删·episode{episode_id}"))
        return len(rows)


def 钉住(edge_id: int, 亲口: bool = True, 库: Path | None = None) -> bool:
    """船主钉住一条事实：pinned=1(永不衰减/永不被自动作废) + 来源类='亲口'(准参与固化)。船主亲核过的走这。"""
    with _锁, _连(库) as c:
        cur = c.execute("UPDATE edges SET pinned=1" + (", 来源类='亲口'" if 亲口 else "") + " WHERE id=?", (edge_id,))
        return cur.rowcount > 0


def 删边(edge_id: int, 库: Path | None = None) -> bool:
    """删单条边(记忆库UI橡皮擦)：作废(expired_at)+指纹进黑名单——检索层从此不再冒它。历史仍在、可回放。"""
    with _锁, _连(库) as c:
        r = c.execute("SELECT * FROM edges WHERE id=?", (edge_id,)).fetchone()
        if not r:
            return False
        c.execute("UPDATE edges SET expired_at=? WHERE id=?", (_今(), edge_id))
        c.execute("INSERT OR IGNORE INTO 黑名单(fp,何时,因) VALUES(?,?,?)", (_边指纹(c, r), _今(), f"UI删边·{edge_id}"))
        return True


def 边两端(edge_id: int, 库: Path | None = None) -> tuple:
    """取一条边的 (主实体名, 宾值)——给 船主.md 跨层删做『整事实匹配』用（要主+宾都对上才删，别单凭宾乱删）。查不到返回 (None, None)。"""
    with _锁, _连(库) as c:
        r = c.execute("SELECT * FROM edges WHERE id=?", (edge_id,)).fetchone()
        if not r:
            return None, None
        src = c.execute("SELECT name FROM entities WHERE id=?", (r["src"],)).fetchone()
        主 = src["name"] if src else ""
        宾 = r["dst_literal"]
        dst_entity = _有效宾实体(c, r)
        if dst_entity:
            er = c.execute("SELECT name FROM entities WHERE id=?", (dst_entity,)).fetchone()
            宾 = er["name"] if er else 宾
        return (str(主 or "").strip() or None, str(宾 or "").strip() or None)


def 改边(edge_id: int, 新宾: str, 库: Path | None = None) -> int | None:
    """船主亲改一条事实的值(记忆库UI)：抄Graphiti"修正=失效旧的+写新的"——旧边标 invalid_at、写新边(值=新宾、
    来源类='亲口'=船主亲改可信、继承pinned)。不物删、可回放。返回新边id。"""
    新宾 = (新宾 or "").strip()
    with _锁, _连(库) as c:
        r = c.execute("SELECT * FROM edges WHERE id=?", (edge_id,)).fetchone()
        if not r or not 新宾:
            return None
        c.execute("UPDATE edges SET invalid_at=? WHERE id=?", (_今(False), edge_id))
        d_ent = 解析实体(新宾, c)
        cur = c.execute(
            "INSERT INTO edges(src,rel,dst_entity,dst_literal,fact,episode_id,valid_at,created_at,confidence,status,来源类,pinned,last_hit)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["src"], r["rel"], d_ent, None if d_ent else 新宾, f"（船主亲改）{新宾}", r["episode_id"],
             _今(False), _今(), 1.0, "现行", "亲口", r["pinned"], _今()))
        return int(cur.lastrowid)


def 记命中(edge_ids: list[int], 库: Path | None = None) -> None:
    """艾宾浩斯：被检索命中的边 strength+1、last_hit 刷新——常被想起的越记越牢、衰减越慢，没人碰的慢慢淡出。"""
    ids = [i for i in (edge_ids or []) if i]
    if not ids:
        return
    with _锁, _连(库) as c:
        now = _今()
        for i in ids:
            c.execute("UPDATE edges SET strength=strength+1, last_hit=? WHERE id=?", (now, i))


def 衰减清理(库: Path | None = None, 阈: float = 0.2, 半衰天: float = 30.0) -> int:
    """遗忘任务(艾宾浩斯·离线批量·可缓)：非 pinned 的现行边，按 R=exp(-Δt/(strength×半衰天)) 算保留度，
    R<阈 的软删——没人碰的慢慢淡出活跃层(治无界增长)。原话/历史仍在。返回淡出几条。默认不自动跑，胖了再调。"""
    with _锁, _连(库) as c:
        rows = c.execute("SELECT id, strength, last_hit FROM current_edges WHERE pinned=0 AND 来源类!='亲口'").fetchall()
        淡 = 0
        for r in rows:
            if not r["last_hit"]:
                continue
            try:
                dt = (_dt.datetime.now() - _dt.datetime.strptime(r["last_hit"][:19], "%Y-%m-%d %H:%M:%S")).days
            except Exception:  # noqa: BLE001
                continue
            if math.exp(-dt / (max(r["strength"], 0.5) * 半衰天)) < 阈:
                c.execute("UPDATE edges SET expired_at=? WHERE id=?", (_今(), r["id"]))
                淡 += 1
        return 淡


# ── 查询 ──────────────────────────────────────────────────────
def _边转(r: sqlite3.Row, c: sqlite3.Connection) -> dict[str, Any]:
    dst = r["dst_literal"]
    dst_entity = _有效宾实体(c, r)
    if dst_entity:
        er = c.execute("SELECT name FROM entities WHERE id=?", (dst_entity,)).fetchone()
        dst = er["name"] if er else dst
    src = c.execute("SELECT name FROM entities WHERE id=?", (r["src"],)).fetchone()
    原话 = None
    if r["episode_id"]:
        ep = c.execute("SELECT content FROM episodes WHERE id=?", (r["episode_id"],)).fetchone()
        原话 = ep["content"] if ep else None
    键 = r.keys()
    return {"id": r["id"], "src": src["name"] if src else None, "rel": r["rel"], "dst": dst,
            "dst_entity": dst_entity,
            "fact": r["fact"], "valid_at": r["valid_at"], "invalid_at": r["invalid_at"],
            "confidence": r["confidence"], "status": r["status"],
            "来源类": (r["来源类"] if "来源类" in 键 else "推断"),
            "pinned": (r["pinned"] if "pinned" in 键 else 0), "原话": 原话}


def 查现值(src: str, rel: str | None = None, 库: Path | None = None) -> list[dict]:
    """查某实体现行的事实（只走 current_edges 视图，绝不出作废/失效的旧值）。"""
    with _连(库) as c:
        s = 解析实体(src, c)
        if s is None:
            return []
        # status='现行' 才出：待确认(低置信没核)绝不当现值喂给岗位/画像注入——待确认闸这里才真生效。
        q = "SELECT * FROM current_edges WHERE src=? AND status='现行'" + (" AND rel=?" if rel else "")
        rows = c.execute(q, (s, rel) if rel else (s,)).fetchall()
        黑 = {row["fp"] for row in c.execute("SELECT fp FROM 黑名单")}
        return [_边转(r, c) for r in rows if not 黑 or _边指纹(c, r) not in 黑]


def 查那时(src: str, rel: str, at: str, 库: Path | None = None) -> list[dict]:
    """时间回放：查某时刻为真的事实（valid_at<=at 且 (invalid_at为空 或 at<invalid_at)）。"""
    with _连(库) as c:
        s = 解析实体(src, c)
        if s is None:
            return []
        rows = c.execute(
            "SELECT * FROM edges WHERE src=? AND rel=? AND valid_at<=? "
            "AND (invalid_at IS NULL OR ?<invalid_at) AND expired_at IS NULL",
            (s, rel, at, at)).fetchall()
        return [_边转(r, c) for r in rows]


def 检索(
    查询: str,
    topN: int = 5,
    跳: int = 2,
    库: Path | None = None,
    record_hits: bool = True,
) -> list[dict]:
    """字面召回种子后做确定性 BFS，多跳结果带完整路径和每条原话。

    `record_hits=False` 是严格只读口：统一搜索/回归使用它，不更新 strength/last_hit。
    """
    查询 = str(查询 or "").strip()
    if not 查询:
        return []
    topN = max(1, min(int(topN or 5), 50))
    跳 = max(0, min(int(跳 or 0), 4))
    with _只读连(库) as c:
        rows = c.execute("SELECT * FROM current_edges WHERE status='现行'").fetchall()
        黑 = {row["fp"] for row in c.execute("SELECT fp FROM 黑名单")}
        rows = [r for r in rows if not 黑 or _边指纹(c, r) not in 黑]
        converted = {int(r["id"]): _边转(r, c) for r in rows}
        row_by_id = {int(r["id"]): r for r in rows}
        adjacency: dict[int, list[int]] = {}
        for r in rows:
            edge_id = int(r["id"])
            src_id = int(r["src"])
            dst_id = _有效宾实体(c, r)
            adjacency.setdefault(src_id, []).append(edge_id)
            if dst_id:
                adjacency.setdefault(dst_id, []).append(edge_id)

        terms = [x for x in re.split(r"[\s，。、；;：:！？!?]+", 查询) if x]
        normalized_query = _归一文(查询)

        def seed_score(edge_id: int) -> float:
            item = converted[edge_id]
            hay = " ".join(str(item.get(k) or "") for k in ("src", "rel", "dst", "fact", "原话"))
            normalized_hay = _归一文(hay)
            score = 0.0
            if normalized_query and normalized_query in normalized_hay:
                score += 3.0
            score += sum(1.0 for term in terms if _归一文(term) and _归一文(term) in normalized_hay)
            return score

        seeds = sorted(
            ((seed_score(edge_id), edge_id) for edge_id in converted),
            key=lambda x: (x[0], -x[1]), reverse=True,
        )
        seeds = [edge_id for score, edge_id in seeds if score > 0][:topN]
        out_ids: list[int] = []
        paths: dict[int, list[int]] = {}
        for seed_id in seeds:
            if seed_id not in out_ids:
                out_ids.append(seed_id)
                paths[seed_id] = [seed_id]
            seed_row = row_by_id[seed_id]
            endpoints = [int(seed_row["src"])]
            seed_dst = _有效宾实体(c, seed_row)
            if seed_dst:
                endpoints.append(seed_dst)
            queue: list[tuple[int, int, list[int]]] = [(node, 0, [seed_id]) for node in endpoints]
            visited_states: set[tuple[int, tuple[int, ...]]] = set()
            while queue:
                node, depth, path = queue.pop(0)
                state = (node, tuple(path))
                if state in visited_states or depth >= 跳:
                    continue
                visited_states.add(state)
                for edge_id in adjacency.get(node, []):
                    if edge_id in path:
                        continue
                    new_path = path + [edge_id]
                    if edge_id not in out_ids:
                        out_ids.append(edge_id)
                        paths[edge_id] = new_path
                    row = row_by_id[edge_id]
                    next_nodes = [int(row["src"])]
                    dst_id = _有效宾实体(c, row)
                    if dst_id:
                        next_nodes.append(dst_id)
                    for next_node in next_nodes:
                        if next_node != node:
                            queue.append((next_node, depth + 1, new_path))
                    if len(out_ids) >= topN * max(1, 跳 + 1):
                        queue.clear()
                        break

        out: list[dict] = []
        for edge_id in out_ids:
            item = dict(converted[edge_id])
            item["路径"] = [
                {
                    "edge_id": pid,
                    "src": converted[pid]["src"], "rel": converted[pid]["rel"], "dst": converted[pid]["dst"],
                    "原话": converted[pid].get("原话"),
                }
                for pid in paths.get(edge_id, [edge_id])
            ]
            out.append(item)
        hit_ids = list(out_ids)
    if record_hits:
        记命中(hit_ids, 库)
    return out


# ── 从对话抽三元组入图（step3，要 LLM；三道防幻觉闸）───────────────
_单值谓词 = {"职业", "居住地", "主力机身", "当前状态"}   # 只能有一个现值的关系，落新边时旧的作废
_谓词表 = ["职业", "居住地", "主力机身", "用", "拍", "在意的人", "说过要做", "当前状态", "偏好", "有过"]

_抽图指令 = (
    "从下面对话里，抽出关于人物的【事实三元组】，**只用给定谓词**，只输出 JSON、别的都不要说。\n"
    "谓词表（只能从这里选，不在表里的事实一律不抽）：{谓词}\n"
    "规矩：\n"
    "1. 主语用人物稳定标识（实例主人必须写'{主人ID}'；其他人物写明确姓名），别用'他/我'这种代词。\n"
    "2. 时间：**只从对话里明说的时间取(相对时间换算成日期)；对话没提就直接写 {参考时间} 这个日期本身"
    "(别写 'REFERENCE_TIME=' 这种前缀)；绝不自己推断或编造时间**。\n"
    "3. 只抽对话里**明确说了**的事实；模糊的、你推测的、原文没有的，一律别抽——宁缺毋滥。\n"
    "4. 每条带一句原句(照抄对话里的话)。拿不准的把 confidence 标 0.5。\n"
    "5. 主语必须是**本人明确陈述的、关于自己的**事实；纯应答/寒暄('收到'/'在'/'好的'/'嗯')里没有事实，"
    "别从里抽、更别给只是应答的人硬安一个属性(这是无中生有)。\n"
    "6. '职业'只记他**现在**的职业；'有过'**只记过去的身份/职业**('以前是医生''当过大夫'这类)——"
    "**一次性的经历、事件、心情、熬夜/跑步/开会/剪片这些，绝不进'有过'**(那是流水不是长期事实)。\n"
    "7. '拍'专指作为婚礼摄影师的**拍摄工作**；'看'电影/剧/展是休闲、不是'拍'，这类别抽。\n"
    "8. **只抽稳定、长期的事实**（职业/居住地/器材/长期偏好/过去经历/立下的规矩）；**今天的临时状态、一次性小事、随口的念头/心情**（累了、今天调了色、开了个会、想歇会）**别抽进图**——那是流水，不是图谱事实。宁少毋滥。\n"
    "9. 值要**具体、能长期成立**：居住地=**长期住**哪('咸阳')——'X人'(籍贯,如'西安人')不是居住地、"
    "**出差/旅行时'在宁波'是临时的更不是居住地**，都别抽；主力机身/镜头只填**具体型号**('索尼A7M5'/'2470GM')，"
    "'干活用的相机''调色工具'这种**泛称/短语别抽**。\n"
    "10. '偏好'只记他**稳定、反复出现的口味/审美/原则性喜好**（如'爱红金调子''宁真勿假'）；"
    "**一次性的评论、随口的意见、夸某个东西/某人一句、对某件具体事的看法，都不是偏好，别抽**。宁缺毋滥。\n"
    "11. **主体归属(防张冠李戴，抄Graphiti)**：①每条事实必须挂到**明确的那个人**，'我/他/她'先消解成具体人名再抽；"
    "②某人提到'我爸/他的猫'这类，抽成**带主人名**的('{主人显示名}的爸')、别抽裸词；③员工干的活/说的话是**关系事实**、不是那个人的**人身属性**——"
    "别把'老纪跑了回归'记成老纪的职业/属性，人身属性(职业/居住地/机身/偏好/有过)基本只属于船主本人；"
    "④上文里被当前句代词指代的人**必须抽出**，别因为'只在上文出现'就漏。\n"
    "12. 每条标 亲述：**仅当这条是本人亲口说的、关于他自己的**事实(如{主人称呼}自己说'我住某地')→ 亲述=true；"
    "别人提到他、或你推断出来的 → 亲述=false。(亲述的才可信、才准将来固化。)\n"
    '输出：{{"三元组":[{{"主":"","谓":"","宾":"","原句":"","时间":"","confidence":1.0,"亲述":false}}]}}'
)


async def _语义去重候选(候选: list[dict], 建Agent, 库: Path | None = None) -> list[dict]:
    """mem0 式写前判定（**覆盖所有事实、不分单值多值**——这是结构性解法，不再一条条堆 prompt 规则）：
    按 (主,谓) 分组，让 LLM 判每个候选该不该记下——丢三类：①和已有值同义/重复(NOOP)；
    ②**填错关系的**(镜头填进'主力机身'、籍贯'X人'填进'居住地'、配件填进机身——不属于这个关系的东西，模型自己认，不用枚举镜头/电池/闪光灯)；③噪音/一次性的话。
    没现值时：多值全留、单值只留一个(最高置信)。解析失败→字面去重兜底，不硬拦也不全丢。"""
    from collections import defaultdict as _dd
    from agentscope.message import Msg, TextBlock
    from 记忆抽取 import _剥JSON, 抽取模型岗
    存活: list[dict] = []
    组: dict = _dd(list)
    for x in 候选:
        组[(x["主"], x["谓"])].append(x)
    for (主, 谓), xs in 组.items():
        单 = 谓 in _单值谓词
        现值 = [e["dst"] for e in 查现值(主, 谓, 库=库)]
        字面新 = [x for x in xs if not any(_归一文(x["宾"]) == _归一文(v) or _归一文(x["宾"]) in _归一文(v) for v in 现值)]
        if not 现值:
            存活.extend(xs if not 单 else [max(xs, key=lambda z: z["conf"])])  # 单值只留一个,防一次抽出俩打架
            continue
        try:
            限 = "**只能有一个值**" if 单 else "可以有多个值"
            系统 = (f"你在维护一个人的事实记忆。关系「{谓}」（{限}），已有值：{现值}。\n"
                   f"刚抽到这些新候选：{[x['宾'] for x in xs]}。\n"
                   "挑出**真正该记下**的候选——**丢掉**这三类：①和已有值同义/重复的(只换了说法、指同一件事)；"
                   "②**填错关系的**(不属于这个关系的东西，比如镜头型号填进了「主力机身」、籍贯填进了「居住地」、配件填进了机身)；③噪音/一次性的话。\n"
                   "例：「主力机身」已有['索尼A7M4']→'索尼A7M5'该记(新机身)、'2470GM'不该记(镜头不是机身)；「有过」已有['医生']→'学医'不该记(重复)。\n"
                   '只输出JSON、别的不说：{{"记下":[从上面候选里挑真正该记的，原样照抄；没有就空数组]}}')
            g = 建Agent(抽取模型岗, 系统, 名字="记忆判定", 最大tokens=512, 思考=False)
            rr = await g.reply(Msg(name="x", content=[TextBlock(type="text", text="判")], role="user"))
            cc = getattr(rr, "content", "")
            wen = cc if isinstance(cc, str) else "".join((b.get("text") if isinstance(b, dict) else getattr(b, "text", "")) or "" for b in (cc or []))
            解析 = _剥JSON(wen)
            if "记下" in 解析:
                记集 = set(解析.get("记下") or [])
                留 = [x for x in xs if x["宾"] in 记集]
            else:
                留 = 字面新  # 解析失败 → 字面去重兜底
        except Exception:  # noqa: BLE001
            留 = 字面新
        存活.extend(留[:1] if 单 else 留)  # 单值最多留一个
    return 存活


async def 从对话抽图(对话文本: str, source: str = "对话", 参考时间: str | None = None, 库: Path | None = None) -> list[dict]:
    """一段对话 → 三元组入图。受控谓词(表外丢弃)+时间只从原文(不编)+低置信进待确认。用最便宜的 deepseek。"""
    from agentscope.message import Msg, TextBlock
    from 记忆抽取 import _剥JSON, 抽取模型岗
    from 升级_模型层 import 建Agent

    参考时间 = 参考时间 or _今(False)
    系统 = _抽图指令.format(
        谓词="/".join(_谓词表), 参考时间=参考时间,
        主人ID=主人ID(), 主人显示名=主人显示名(), 主人称呼=主人称呼(),
    )
    a = 建Agent(抽取模型岗, 系统, 名字="图谱抽取器", 最大tokens=2048, 思考=False)
    r = await a.reply(Msg(name="办公室", content=[TextBlock(type="text", text=f"REFERENCE_TIME={参考时间}\n# 对话\n{对话文本[-4000:]}")], role="user"))
    c = getattr(r, "content", "")
    文 = c if isinstance(c, str) else "".join((b.get("text") if isinstance(b, dict) else getattr(b, "text", "")) or "" for b in (c or []))
    候选 = []
    for t in (_剥JSON(文).get("三元组") or []):
        主, 谓, 宾 = (str(t.get(k) or "").strip() for k in ("主", "谓", "宾"))
        if not (主 and 谓 and 宾):
            continue
        if 谓 not in _谓词表:      # 受控闸：表外谓词一律丢，防模型自由发挥把图搞碎
            continue
        候选.append({"主": 主, "谓": 谓, "宾": 宾,
                    "conf": float(t.get("confidence") or 1.0),
                    "原句": str(t.get("原句") or f"{主}{谓}{宾}").strip(),
                    "当": _规整时间(t.get("时间"), 参考时间),
                    "来源类": "亲口" if t.get("亲述") else "推断"})  # 亲述=船主亲口→亲口(可信/可固化)，否则推断
    # mem0 式语义去重(NOOP)：多值谓词写前，让 LLM 判"新值是不是已有某个值的同义/重复"——
    # 阈值判重不行(A7M4/A7M5=0.83 比 学医/医生=0.75 还高)，同一/不同得靠模型语义判。单值交给 落边 守卫。
    存活 = await _语义去重候选(候选, 建Agent, 库)
    落 = []
    for x in 存活:
        ep = 落原话(x["原句"], source, x["当"], 库=库)
        落边(x["主"], x["谓"], x["宾"], x["原句"], ep, x["当"], confidence=x["conf"],
             status=("待确认" if x["conf"] < 0.7 else "现行"), 单值=(x["谓"] in _单值谓词),
             来源类=x.get("来源类", "推断"), 库=库)
        落.append({"主": x["主"], "谓": x["谓"], "宾": x["宾"], "confidence": x["conf"],
                   "status": "待确认" if x["conf"] < 0.7 else "现行"})
    return 落


if __name__ == "__main__":
    # 离线自检（零 API）：建库→归一→单值冲突→2跳→检索带原话；只写临时库。
    # 测库落临时目录、try/finally 兜底清（连 sqlite 的 -wal/-shm 一起），绝不污染真 记忆库/（2026-07-10 修）。
    import shutil
    import tempfile
    _临 = Path(tempfile.mkdtemp(prefix="图谱自检_"))
    测库 = _临 / "_图谱自检.db"
    try:
        建库(测库)
        落实体("示例主人", ["老板", "先生"], "摄影师", 库=测库)
        e1 = 落原话("老板说主力机身是型号A，用两年了", "对话#1", "2024-01-10", 库=测库)
        落边("老板", "主力机身", "型号A", "示例主人主力机身是型号A", e1, "2024-01-10", 单值=True, 库=测库)
        e2 = 落原话("老板说这次换了型号B", "对话#2", "2026-06-01", 库=测库)
        落边("先生", "主力机身", "型号B", "示例主人主力机身换成型号B", e2, "2026-06-01", 单值=True, 库=测库)
        e3 = 落原话("老板下周拍一场活动", "对话#3", "2026-07-05", 库=测库)
        落边("老板", "拍", "下周活动", "示例主人下周拍一场活动", e3, "2026-07-05", 库=测库)
        落边("红金中式婚礼", "用机身", "索尼A7M5", "红金婚礼用索尼A7M5拍", e3, "2026-07-05", 库=测库)

        print("① 别名归一：'老李'/'老板'/'李先生' 都解析到同一实体？")
        ids = {n: 解析实体(n, _连(测库)) for n in ("示例主人", "老板", "先生", "路人甲")}
        print("  ", ids, "→", "✅ 归一" if len({v for v in ids.values() if v}) == 1 and ids["路人甲"] is None else "❌")
        print("② 单值冲突(旧换新不打架)：现值主力机身 =",
              [x["dst"] for x in 查现值("示例主人", "主力机身", 库=测库)], "(应只出型号B)")
        print("   时间回放·2024年那时 =",
              [x["dst"] for x in 查那时("示例主人", "主力机身", "2024-06-01", 库=测库)], "(应仍出型号A)")
        print("③ 2跳检索：问'下周婚礼用什么机身' →")
        for x in 检索("下周 婚礼 机身", 库=测库):
            print("    ", x["src"], x["rel"], x["dst"], "｜原话:", x["原话"])
        print("自检完（离线零 API）")
    finally:
        shutil.rmtree(_临, ignore_errors=True)
