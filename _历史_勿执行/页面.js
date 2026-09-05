/* 办公室前端 · 暗房亮桌
   中栏默认=「大厅」：一条输入框对全公司说话（默认项目经理接，@岗位 指名，@所有人 广播），
   立项/请示/预审/裁决/验收/对话全部流进同一条时间线（露理由，不露黑箱）。
   项目室=点进去看一个任务的深水细节。右栏=该你出手，判断永远居中。 */
"use strict";
let MEMBERS = window.XJ_MEMBERS || [];
let ROLES = window.XJ_ROLES || MEMBERS.map(m => m.name);
const $ = (s, r = document) => r.querySelector(s);
const ce = (t, c) => { const e = document.createElement(t); if (c) e.className = c; return e; };
const bt = (cls, txt, fn) => { const b = ce("button", cls); b.textContent = txt; b.onclick = fn; return b; };
const esc = s => (s == null ? "" : String(s)).replace(/[&<>"']/g, m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
const cssEsc = s => (window.CSS && CSS.escape ? CSS.escape(String(s)) : String(s).replace(/["\\]/g, "\\$&"));

function 初始视图() {
  let v = JSON.parse(localStorage.getItem("xj_view") || "null") || { t: "hall" };
  const old = JSON.stringify(v);
  if (v.t === "welcome" || v.t === "compose") v = { t: "hall" };
  if (v.t === "chat") v = { t: "hall", role: v.role };
  if (v.t === "room") v = { t: "hall" };
  if (JSON.stringify(v) !== old) localStorage.setItem("xj_view", JSON.stringify(v));
  return v;
}
let 视图 = 初始视图();
let 看板 = null, 详情 = null, 大厅 = null;
let 临时 = [];               // 大厅本地暂存（发送中的气泡、点名结果）
let 附件 = [];               // 本次输入框附带的图片/文件
let 后台断线已提示 = false;
const H = {};                // 各容器内容指纹
const 展开状态 = {};          // 项目室折叠块状态，避免轮询刷新后合不上/又展开

function 存视图() { localStorage.setItem("xj_view", JSON.stringify(视图)); }
function 去(v) { 视图 = v; 存视图(); H.stage = null; H.stageKind = null; 渲染舞台(); 标注选中(); if (v.t === "room") 拉详情(); if (v.t === "hall") 拉大厅(); }

/* ---------- 网络 ---------- */
async function getJSON(u) {
  const r = await fetch(u);
  if (!r.ok) throw new Error(r.status + " " + r.statusText);
  return r.json();
}
async function post(u, b) {
  const r = await fetch(u, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(b || {}) });
  if (!r.ok) throw new Error(r.status + " " + r.statusText);
  return r.json();
}

/* ---------- toast(仅提示兜底) / 内联输入 ---------- */
function toast(msg, kind = "") {
  const t = ce("div", "toast " + kind); t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; setTimeout(() => t.remove(), 300); }, 2500);
}
function 问(host, 占位, onok) {
  host.querySelectorAll(".inline-in").forEach(e => e.remove());
  const wrap = ce("div", "inline-in");
  const inp = ce("input"); inp.placeholder = 占位;
  const ok = bt("prime sm", "确定", () => { const v = inp.value.trim(); wrap.remove(); onok(v); });
  const no = bt("ghost sm", "取消", () => wrap.remove());
  wrap.append(inp, ok, no); host.appendChild(wrap); inp.focus();
  inp.onkeydown = e => { if (e.key === "Enter") ok.onclick(); if (e.key === "Escape") no.onclick(); };
}

/* ---------- 泳道状态语义 ---------- */
function 态(l) {
  if (l.列 === "进行中") {
    if (l.请示中) return { cls: "ask", txt: "请示中", dot: "live" };
    if ((l.控制状态 || "").startsWith("暂停")) return { cls: "", txt: "暂停", dot: "gray" };
    return { cls: "run", txt: "运行中", dot: "live" };
  }
  return ({
    待确认: { cls: "", txt: "待你确认开工", dot: "gray" },
    待办: { cls: "", txt: "已回炉·待办", dot: "gray" },
    待验收: { cls: "ask", txt: "待你验收", dot: "live" },
    已完成: { cls: "done", txt: "已完成", dot: "green" },
    已作废: { cls: "", txt: "已作废", dot: "gray" },
  })[l.列] || { cls: "", txt: l.列, dot: "gray" };
}

/* ---------- 骨架 / 左栏 ---------- */
function 骨架() {
  const app = $("#app"); app.innerHTML = "";
  ["rail", "stage", "action"].forEach(id => { const d = ce("div", id); d.id = id; app.appendChild(d); });
}
function 渲染左栏() {
  const rail = $("#rail");
  const proj = 看板.项目 || [];
  const ids = new Set(proj.map(p => p.id));
  if (视图.t === "room" && !ids.has(视图.id)) { 视图 = { t: "hall" }; 存视图(); H.stage = null; H.stageKind = null; 详情 = null; }
  const 活 = proj.filter(p => p.活跃), 完 = proj.filter(p => !p.活跃);
  const sig = JSON.stringify({ p: proj.map(p => [p.id, p.汇总, p.待处理, p.活跃]), 急: 看板.急停, v: 视图 });
  if (H.rail === sig) return; H.rail = sig;
  rail.innerHTML = "";

  const hall = ce("div", "nav-i"); hall.dataset.hall = "1"; hall.style.marginTop = "6px";
  hall.innerHTML = `<div class="avatar">堂</div><div class="stack"><div class="ti">大厅</div><div class="sub">对全公司说话</div></div>`;
  hall.onclick = () => 去({ t: "hall" }); rail.appendChild(hall);

  rail.appendChild(秒标题("进行中", 活.length, true));
  if (!活.length) rail.appendChild(行空("还没有进行中的任务。\n去大厅说一句，立为任务。"));
  活.forEach(p => rail.appendChild(项目行(p)));

  rail.appendChild(秒标题("历史工单", 完.length, false));
  if (!完.length) rail.appendChild(行空("暂无历史工单。"));
  完.forEach(p => rail.appendChild(历史行(p)));

  const members = (看板.成员 && 看板.成员.length ? 看板.成员 : MEMBERS);
  MEMBERS = members; ROLES = members.map(m => m.name || m);
  rail.appendChild(秒标题("班子", members.length + 1, false));
  rail.appendChild(船主行());
  members.forEach(m => rail.appendChild(岗位行(m)));

  const foot = ce("div", "rail-foot");
  foot.append(bt("ghost sm", "点名", 点名), bt(看板.急停 ? "danger sm" : "ghost sm", 看板.急停 ? "解除急停" : "全员急停", 急停));
  rail.appendChild(foot);
  标注选中();
}
function 秒标题(txt, n, live) {
  const h = ce("div", "sec-h");
  h.innerHTML = `${esc(txt)}<span class="count${live && n ? " live" : ""}">${n}</span>`;
  return h;
}
function 行空(t) { const d = ce("div", "empty"); d.style.cssText = "padding:12px 16px;text-align:left;white-space:pre-line"; d.textContent = t; return d; }
function 项目行(p) {
  const el = ce("div", "nav-i"); el.dataset.room = p.id;
  const z = p.汇总 || {}, parts = [];
  if (z.进行中) parts.push(z.进行中 + " 运行"); if (z.待确认) parts.push(z.待确认 + " 待确认");
  if (z.待验收) parts.push(z.待验收 + " 待验收"); if (z.待办) parts.push(z.待办 + " 回炉");
  const stk = ce("div", "stack");
  const ti = ce("div", "ti"); ti.textContent = p.标题;
  const sb = ce("div", "sub"); sb.textContent = parts.join(" · ") || (p.活跃 ? "进行中" : "已结束");
  stk.append(ti, sb); el.append(ce("div", "dot " + (z.进行中 ? "live" : p.活跃 ? "gray" : "green")), stk);
  if (p.待处理) { const b = ce("div", "badge"); b.textContent = p.待处理; el.appendChild(b); }
  el.onclick = () => 去({ t: "room", id: p.id });
  return el;
}
function 历史行(p) {
  const el = 项目行(p);
  el.classList.add("hist");
  const sub = el.querySelector(".sub");
  if (sub) sub.textContent = `历史记录 · ${p.工单数 || 0} 张工单`;
  return el;
}
function 船主行() {
  const el = ce("div", "nav-i owner");
  el.innerHTML = `<div class="avatar crown">主</div><div class="stack"><div class="ti">船主 · 示例主人</div><div class="sub">最终拍板 · 不走模型</div></div>`;
  return el;
}
function 岗位行(member) {
  const m = typeof member === "string" ? { name: member, title: member, model: "未知" } : member;
  const r = m.name;
  const model = m.model || m.registered_model || "未知";
  const ok = m.actual_model ? (m.model_ok ? " · 已核验" : " · 模型不符") : "";
  const el = ce("div", "nav-i"); el.dataset.chat = r;
  el.innerHTML = `<div class="avatar">${esc((m.title || r)[0])}</div><div class="stack"><div class="ti">${esc(m.title || r)} · ${esc(model)}</div><div class="sub">${esc(r)} · ${esc(m.abilities || "过滤大厅 · 输入框带 @")}${esc(ok)}</div></div>`;
  el.title = `${m.tier || m.title || r}\n岗位：${r}\n模型：${model}\n能力：${m.abilities || "待补"}\n模态：${m.modality || "待核验"}`;
  el.onclick = () => { 去({ t: "hall", role: r }); 套岗位输入(r); };
  return el;
}
function 标注选中() {
  document.querySelectorAll(".nav-i").forEach(e => {
    const on = (视图.t === "hall" && !视图.role && e.dataset.hall) || (视图.t === "room" && e.dataset.room === 视图.id) || (视图.t === "hall" && e.dataset.chat === 视图.role);
    e.classList.toggle("on", !!on);
  });
}

/* ---------- 右栏：该你出手 ---------- */
function 渲染右栏() {
  const a = $("#action"); const td = 看板.待办 || {};
  const sig = JSON.stringify(td);
  if (H.action === sig) return; H.action = sig;
  a.innerHTML = "";
  const 确 = td.待确认 || [], 裁 = td.待裁决 || [], 验 = td.待验收 || [], 回 = td.待办 || [];

  a.appendChild(秒标题("待确认开工", 确.length, !!确.length));
  if (!确.length) a.appendChild(行空("无新分解，安静。"));
  const g = {}; 确.forEach(x => (g[x.会议] = g[x.会议] || []).push(x));
  Object.keys(g).forEach(mid => {
    const it = g[mid]; const box = ce("div", "todo");
    const t = ce("div", "t"); t.textContent = it[0].标题 || mid;
    const r = ce("div", "r"); r.textContent = it.length + " 张子工单 · " + it.map(x => x.角色).join("、");
    const row = ce("div", "row");
    const ok = ce("button", "prime sm"); ok.textContent = "确认开工";
    ok.onclick = e => { e.stopPropagation(); 确认开工(it.map(x => x.name), ok); };
    row.append(ok, bt("bad sm", "打回", e => { e.stopPropagation(); 问(box, "打回重分解的理由…", v => 打回重分解(v)); }));
    box.append(t, r, row);
    box.onclick = () => 去({ t: "room", id: mid }); a.appendChild(box);
  });

  a.appendChild(秒标题("等你裁决", 裁.length, !!裁.length));
  if (!裁.length) a.appendChild(行空("此刻无人请示，风平浪静。"));
  裁.forEach(c => {
    const box = ce("div", "todo");
    const t = ce("div", "t"); t.textContent = (c.岗位 || "") + " 请示";
    const r = ce("div", "r"); r.textContent = (c.内容 || "").slice(0, 60);
    const row = ce("div", "row");
    row.append(bt("ok sm", "批", e => { e.stopPropagation(); 决策(c.id, "批", ""); }),
      bt("bad sm", "驳", e => { e.stopPropagation(); 问(box, "驳回理由…", v => 决策(c.id, "驳", v)); }));
    box.append(t, r, row); a.appendChild(box);
  });

  a.appendChild(秒标题("回炉申请", 回.length, !!回.length));
  if (!回.length) a.appendChild(行空("没有回炉工单。"));
  回.forEach(x => {
    const box = ce("div", "todo");
    const report = x.经理报告 || {};
    const extra = Number(report.追加步数 || 2) || 2;
    const t = ce("div", "t"); t.textContent = x.标题 || x.name;
    const r = ce("div", "r");
    const artifact = (x.产物 || []).map(p => `${p.路径}：${p.状态}`).join("；");
    r.innerHTML = `${esc(x.角色 || "岗位")} · 已用 ${esc(x.步数 || 0)}/${esc(x.预算 || "?")} 步`
      + (artifact ? `<br>${esc(artifact)}` : "")
      + (report.理由 ? `<br><b>经理报告：</b>${esc(report.理由)}` : "")
      + (report.建议 ? `<br><b>建议：</b>${esc(report.建议)}` : "");
    const ev = ce("div", "mini-evidence");
    if ((report.依据 || []).length) ev.textContent = "依据：" + report.依据.join(" / ");
    const row = ce("div", "row");
    row.append(bt("prime sm", "批准续步", e => { e.stopPropagation(); 继续任务(x.name, extra); }),
      bt("bad sm", "驳回", e => {
        e.stopPropagation();
        if (x.请示?.id) 问(box, "驳回理由…", v => 决策(x.请示.id, "驳", v));
        else toast("这条回炉没有待裁决请示，只能打开项目室处理。", "bad");
      }),
      bt("ghost sm", "打开项目室", e => { e.stopPropagation(); 去({ t: "room", id: x.会议 }); }));
    box.append(t, r);
    if (ev.textContent) box.appendChild(ev);
    box.append(row);
    box.onclick = () => 去({ t: "room", id: x.会议 }); a.appendChild(box);
  });

  a.appendChild(秒标题("待你验收", 验.length, !!验.length));
  if (!验.length) a.appendChild(行空("没有待验收的活。"));
  验.forEach(x => {
    const box = ce("div", "todo");
    const t = ce("div", "t"); t.textContent = x.标题 || x.name;
    const r = ce("div", "r"); r.textContent = x.角色 + " 交付";
    box.append(t, r, 验收按钮(box, x.name, "待验收"));
    box.onclick = () => 去({ t: "room", id: x.会议 }); a.appendChild(box);
  });
}
function 验收按钮(host, name, col) {
  const row = ce("div", "row");
  row.append(bt("ok sm", "通过", e => { e.stopPropagation(); 裁决(name, col, "通过", ""); }),
    bt("bad sm", "打回", e => { e.stopPropagation(); 问(host, "打回理由…", v => 裁决(name, col, "打回", v)); }),
    bt("ghost sm", "作废", e => { e.stopPropagation(); 问(host, "作废理由…", v2 => 裁决(name, col, "作废", v2)); }));
  return row;
}

/* ---------- 中栏 ---------- */
function 渲染舞台() {
  if (视图.t === "hall") { 渲染大厅(); return; }
  if (视图.t === "room") { 渲染房间(); return; }
}

/* ---------- 大厅 ---------- */
function 渲染大厅() {
  const s = $("#stage");
  if (H.stageKind !== "hall") {
    H.stageKind = "hall"; H.stage = null;
    s.innerHTML = `<div class="hall"><div class="feed"><div class="feed-in" id="feed"></div></div>
      <div class="say"><div class="attach-strip" id="attachStrip"></div><div class="say-in">
        <button class="tool sm" id="fileBtn" title="添加图片或文件">＋</button>
        <input type="file" id="filePick" multiple hidden>
        <textarea id="sayIn" rows="1" placeholder="对公司说话… @会弹出岗位；可粘贴图片/文件；左栏点班子成员会过滤同一个大厅"></textarea>
        <div class="mention" id="mention"></div>
        <button class="prime sm" id="sayBtn">发送</button>
        <button class="ghost sm" id="taskBtn" style="display:none">立为任务</button>
      </div></div></div>`;
    $("#sayIn").onkeydown = e => {
      const menu = $("#mention");
      if (menu?.classList.contains("on") && ["ArrowDown", "ArrowUp", "Enter", "Escape"].includes(e.key)) { 提及键盘(e); return; }
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) 智能发送();
    };
    $("#sayIn").oninput = () => { 自适应输入($("#sayIn")); 更新提及(); };
    $("#sayIn").onpaste = 粘贴附件;
    $("#fileBtn").onclick = () => $("#filePick").click();
    $("#filePick").onchange = e => 选附件(e.target.files);
    $("#sayBtn").onclick = 智能发送; $("#taskBtn").onclick = 立项;
  }
  const feed = $("#feed"); if (!feed || !大厅) return;
  if (视图.role && !($("#sayIn").value || "").trim()) $("#sayIn").value = "@" + 视图.role + " ";
  画附件();
  const filtered = 过滤事件(大厅.事件 || []);
  const sig = JSON.stringify([filtered, 临时, 视图.role]);
  if (H.stage === sig) return; H.stage = sig;
  const box = feed.parentElement;
  const 贴底 = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  const 旧位 = box.scrollTop;
  feed.innerHTML = "";
  feed.appendChild(大厅头());
  if (!filtered.length && !临时.length) feed.appendChild(大厅空状态());
  let day = "";
  filtered.forEach(e => {
    const d = (e.t || "").slice(0, 10);
    if (d && d !== day) { day = d; const dv = ce("div", "day"); dv.textContent = d; feed.appendChild(dv); }
    feed.appendChild(事件节点(e));
  });
  临时.forEach(e => feed.appendChild(事件节点(e)));
  box.scrollTop = 贴底 ? box.scrollHeight : 旧位;
}
function 事件节点(e) {
  const _et = e.t || ""; const hm = _et.includes("T") ? _et.slice(11, 16) : _et.slice(0, 5);  // 兼容 ISO 和 HH:MM:SS 两种时间格式
  if (e.k === "分割") {  // 轮次分割线:复用按天分隔的横线样式,把一轮立项/开会和别的隔开
    const d = ce("div", "day"); d.textContent = e.text; return d;
  }
  if (e.k === "话") {
    const me = (e.who || "").indexOf("船主") >= 0;
    const b = ce("div", "bub " + (me ? "me" : "them"));
    b.innerHTML = `<div class="nm">${esc((me ? "你" : e.who) + (hm ? " · " + hm : ""))}</div><div>${esc(e.text)}</div>`;
    const wrap = ce("div"); wrap.style.display = "flex"; wrap.appendChild(b); return wrap;
  }
  if (e.k === "立项") {
    const d = ce("div", "sys");
    d.innerHTML = `<span class="tag">立项</span><b>${esc(e.text)}</b>` + (e.副 ? `<div class="sub">${esc(e.副)}</div>` : "");
    if (e.room) d.onclick = () => 去({ t: "room", id: e.room, focus: e.event });
    return d;
  }
  if (e.k === "请示") {
    const d = ce("div", "mile ask-line");
    const budget = String(e.text || "").includes("预算") || String(e.text || "").includes("步数");
    const msg = budget
      ? `${e.who || "岗位"} 提交预算熔断，已并入右栏回炉申请`
      : `${e.who || "岗位"} 卡住，请去${e.桶 === "待船主" ? "右栏裁决" : "项目室查看"}`;
    d.innerHTML = `<span class="tag">请示</span><span>${esc(msg)}</span><small>${esc(hm)}</small>`;
    if (e.room) d.onclick = () => 去({ t: "room", id: e.room, focus: e.event });
    return d;
  }
  if (e.k === "会") {
    const d = ce("div", "mile meeting-line");
    d.innerHTML = `<span class="tag">会议</span><span><b>${esc(e.text || "")}</b>${e.副 ? `<em>${esc(e.副)}</em>` : ""}</span>` + (hm ? `<small>${esc(hm)}</small>` : "");
    if (e.room) d.onclick = () => 去({ t: "room", id: e.room, focus: e.event });
    return d;
  }
  if (e.k === "工" || e.k === "警" || e.k === "完") {
    const d = ce("div", "mile work-line");
    const cls = e.k === "警" ? "warn" : e.k === "完" ? "green" : "";
    const label = e.k === "警" ? "回炉" : e.k === "完" ? "完成" : "进展";
    d.innerHTML = `<span class="tag ${cls}">${label}</span><span><b>${esc(e.text || "")}</b>${e.副 ? `<em>${esc(e.副)}</em>` : ""}</span>` + (hm ? `<small>${esc(hm)}</small>` : "");
    if (e.room) d.onclick = () => 去({ t: "room", id: e.room, focus: e.event });
    return d;
  }
  if (e.k === "系统") {
    const d = ce("div", "note sys-note");
    d.innerHTML = 系统文本(e.text || "");
    return d;
  }
  const d = ce("div", "mile");  // 决 / 验
  const tag = e.k === "验" ? `<span class="tag green">验收</span>` : e.k === "决" ? `<span class="tag">决</span>` : "";
  d.innerHTML = `${tag}<span>${esc(e.who || "")} ${esc(e.text)}</span>` + (hm ? ` <small>${esc(hm)}</small>` : "");
  if (e.room) { d.style.cursor = "pointer"; d.onclick = () => 去({ t: "room", id: e.room, focus: e.event }); }
  return d;
}
function 系统文本(text) {
  return String(text || "").split("\n").map(line => {
    const safe = esc(line);
    return line.startsWith("岗位：") ? `<b class="role-line">${safe}</b>` : safe;
  }).join("\n");
}
function 大厅头() {
  const d = ce("div", "hall-head");
  const title = 视图.role ? `大厅 · 只看 ${视图.role}` : "大厅 · 全公司唯一对话场";
  d.innerHTML = `<h1>${esc(title)}</h1><div class="meta">自然语言指挥，图片/文件直接贴进输入框。过程沉到项目室，右栏只放需要你拍板的动作。</div>`;
  if (视图.role) d.appendChild(bt("ghost sm", "看全公司", () => 去({ t: "hall" })));
  return d;
}
function 大厅空状态() {
  const d = ce("div", "empty hall-empty");
  const hasTodo = (看板?.待办?.待确认 || []).length || (看板?.待办?.待裁决 || []).length || (看板?.待办?.待验收 || []).length;
  d.innerHTML = hasTodo
    ? "大厅现在没有新的对话流。<br>需要你处理的事在右栏；任务细节进左栏项目室看。"
    : "大厅现在很安静。<br>对公司说一句，或从左栏打开项目室。";
  return d;
}
function 过滤事件(list) {
  if (!视图.role) return list;
  return list.filter(e => {
    if (e.k === "话") return e.role === 视图.role || e.who === 视图.role;
    return e.role === 视图.role || e.who === 视图.role || (e.text || "").includes(视图.role) || (e.副 || "").includes(视图.role);
  });
}
function 当前附件() {
  return 附件.map(a => ({ name: a.name, type: a.type, data: a.data }));
}
function 清附件() { 附件 = []; 画附件(); }
function 画附件() {
  const strip = $("#attachStrip"); if (!strip) return;
  strip.innerHTML = "";
  附件.forEach((a, i) => {
    const chip = ce("div", "attach");
    chip.innerHTML = a.type.startsWith("image/") ? `<img src="${a.data}" alt=""><span>${esc(a.name)}</span>` : `<span class="file-ico">文</span><span>${esc(a.name)}</span>`;
    const x = ce("button", "x"); x.textContent = "×"; x.onclick = () => { 附件.splice(i, 1); 画附件(); };
    chip.appendChild(x); strip.appendChild(chip);
  });
}
function 读文件(f) {
  return new Promise(res => {
    const r = new FileReader();
    r.onload = () => res({ name: f.name || "粘贴图片.png", type: f.type || "application/octet-stream", data: r.result });
    r.readAsDataURL(f);
  });
}
async function 选附件(files) {
  for (const f of Array.from(files || []).slice(0, 8 - 附件.length)) 附件.push(await 读文件(f));
  画附件();
}
function 粘贴附件(e) {
  const files = [];
  for (const it of Array.from(e.clipboardData?.items || [])) {
    const f = it.getAsFile && it.getAsFile();
    if (f) files.push(f);
  }
  if (files.length) 选附件(files);
}
function 自适应输入(inp) {
  inp.style.height = "auto";
  inp.style.height = Math.min(150, inp.scrollHeight) + "px";
}
function 更新提及() {
  const inp = $("#sayIn"), menu = $("#mention");
  if (!inp || !menu) return;
  const before = inp.value.slice(0, inp.selectionStart);
  const m = before.match(/@([^\s@]*)$/);
  if (!m) { menu.classList.remove("on"); return; }
  const q = m[1];
  const opts = ["所有人", ...ROLES].filter(r => r.includes(q)).slice(0, 8);
  if (!opts.length) { menu.classList.remove("on"); return; }
  menu.innerHTML = opts.map((r, i) => `<div class="${i === 0 ? "sel" : ""}" data-role="${esc(r)}">@${esc(r)}<span>${r === "所有人" ? "广播" : "指名"}</span></div>`).join("");
  menu.classList.add("on");
  menu.querySelectorAll("[data-role]").forEach(el => el.onclick = () => 选提及(el.dataset.role));
}
function 提及键盘(e) {
  const menu = $("#mention"), items = Array.from(menu.querySelectorAll("[data-role]"));
  if (e.key === "Escape") { menu.classList.remove("on"); e.preventDefault(); return; }
  let i = Math.max(0, items.findIndex(x => x.classList.contains("sel")));
  if (e.key === "ArrowDown") i = Math.min(items.length - 1, i + 1);
  if (e.key === "ArrowUp") i = Math.max(0, i - 1);
  if (e.key === "Enter") { 选提及(items[i].dataset.role); e.preventDefault(); return; }
  items.forEach((x, n) => x.classList.toggle("sel", n === i));
  e.preventDefault();
}
function 选提及(role) {
  const inp = $("#sayIn"), before = inp.value.slice(0, inp.selectionStart), after = inp.value.slice(inp.selectionStart);
  inp.value = before.replace(/@([^\s@]*)$/, "@" + role + " ") + after;
  $("#mention").classList.remove("on"); inp.focus(); 自适应输入(inp);
}
function 套岗位输入(role) {
  const inp = $("#sayIn"); if (!inp) return;
  const v = inp.value.trim();
  if (!v || v === "@") inp.value = "@" + role + " ";
  else if (!v.startsWith("@")) inp.value = "@" + role + " " + inp.value;
  inp.focus(); 自适应输入(inp); 更新提及();
}
async function 智能发送() {  // ①一个框:AI判这句聊还是开会,自动走对应的;开会进行中则插话
  const v = ($("#sayIn").value || "").trim();
  if (!v) return;
  if (window.__圆桌跑着) return 说话();  // ④开会进行中:说话()内部已把它当插话
  临时.push({ k: "话", who: "船主", text: v, t: new Date().toTimeString().slice(0, 5) }); 渲染大厅();  // 一发就显示你的话,别只在按钮干转"判断中"
  const sb = $("#sayBtn"); const 原 = sb ? sb.textContent : "发送";
  if (sb) { sb.disabled = true; sb.textContent = "判断中…（项目经理在判这句要不要开会）"; }
  let 开会 = false;
  try { const j = await post("/判断", { text: v }); 开会 = !!(j && j.开会); } catch (e) {}
  if (sb) { sb.disabled = false; sb.textContent = 原 || "发送"; }
  if (开会) 立项(); else 说话();
}
async function 说话() {
  const inp = $("#sayIn"); let v = inp.value.trim();
  if (!v && 附件.length) v = "请看附件/截图。";
  if (!v) return;
  inp.value = "";
  const files = 当前附件(); 清附件();
  临时.push({ k: "话", who: "船主", text: v + (files.length ? `\n[已附 ${files.length} 个文件/截图]` : "") }, { k: "话", who: "…", text: "…思考中…" });
  渲染大厅();
  try {
    if (window.__圆桌跑着) {  // 债②:开会进行中=实时插话,带 sid 走事件总线
      临时.pop(); 临时.pop();  // 撤掉乐观气泡(船主+思考中)——由 /stream 的「插话」事件统一显示,不重复、重连一致
      渲染大厅();
      await post("/插话", { text: v, sid: window.__当前会话 || "" });
      toast("已插话进会场，下个发言带上你的话", "good"); $("#sayIn").value = ""; return;
    }
    const j = await post("/say", { text: v, attachments: files });
    临时.pop();
    (j.replies || []).forEach(r => 临时.push({ k: "话", who: r.role, text: r.reply }));
    渲染大厅();
    await 拉大厅(); 临时 = 临时.filter(x => x.k === "系统" || x.k === "分割"); 渲染大厅();
  } catch (e) { 临时.pop(); 临时.push({ k: "系统", who: "系统", text: "出错：" + e }); 渲染大厅(); }
}
async function 立项() {
  const inp = $("#sayIn"); const v = inp.value.trim();
  if (!v) { toast("先在输入框写下需求，再点「立为任务」", "bad"); return; }
  const files = 当前附件(); 清附件();
  const btn = $("#taskBtn"); btn.disabled = true; btn.innerHTML = '<span class="spin"></span>项目会中…';
  临时.push({ k: "分割", text: "新任务 · " + new Date().toTimeString().slice(0, 5) });  // 轮次分割线:本轮开头
  const item = { k: "系统", who: "项目会", text: "立项进度\n运行中 0.0s\n准备提交需求…" };
  临时.push(item); 渲染大厅();
  const start = performance.now();
  const lines = ["准备提交需求…"];
  let running = true;
  window.__圆桌跑着 = true;  // ④开会进行中:此时说话=插话
  const paint = () => {
    const used = ((performance.now() - start) / 1000).toFixed(1);
    item.text = `立项进度\n${running ? "运行中" : "已完成"} ${used}s\n${lines.join("\n")}`;
    if (视图.t === "hall") 渲染大厅();  // 只在停留大厅时渲染,避免把进项目室的用户拉回大厅(疯狂乱跳根因)
  };
  const timer = setInterval(paint, 500);
  try {
    // 债③:两条线分离——POST /trigger 触发后台跑(立即返回 sid,不阻塞),再 EventSource(/stream) 看流(replay+live)。
    const tj = await post("/trigger", { text: v, attachments: files });
    if (!tj || !tj.sid) throw new Error("trigger 未返回会话 sid");
    const sid = tj.sid;
    window.__当前会话 = sid;  // 债②:插话带上它,实时进会场
    let result = null;
    await new Promise((resolve, reject) => {
      const es = new EventSource("/stream?sid=" + encodeURIComponent(sid));
      es.onmessage = ev => {
        let data; try { data = JSON.parse(ev.data || "{}"); } catch (_e) { return; }
        const 类型 = data.类型 || "";
        if (类型 === "心跳") return;
        if (类型 === "插话") {  // 船主插话气泡(后端发布插话时入流,单一数据源)
          临时.push({ k: "话", who: data.发言人 || "船主", text: data.内容 || "", t: data.t });
          if (视图.t === "hall") 渲染大厅();
        } else if (类型 === "发言开始") {  // 打字机:新建一个空气泡,后续增量往里追加
          临时.push({ k: "话", who: data.发言人, text: "", t: data.t, 流式: true });
          if (视图.t === "hall") 渲染大厅();
        } else if (类型 === "发言增量") {  // 打字机:一块块字实时追加到当前气泡
          const last = 临时[临时.length - 1];
          if (last && last.流式 && last.who === data.发言人) { last.text += (data.delta || ""); if (视图.t === "hall") 渲染大厅(); }
        } else if (类型 === "发言结束") {  // 打字机收尾:气泡定稿成完整文本
          const last = 临时[临时.length - 1];
          if (last && last.流式 && last.who === data.发言人) { last.text = data.内容 || last.text; delete last.流式; if (视图.t === "hall") 渲染大厅(); }
        } else if (类型 === "进展") {
          const line = data.内容 || "";
          const _m = line.match(/^💬\s*(.+?)（.+?）：\n?([\s\S]*)$/);  // ②发言→一岗一个气泡
          if (_m) { 临时.push({ k: "话", who: _m[1].trim(), text: _m[2].trim(), t: data.t }); if (视图.t === "hall") 渲染大厅(); }
          else lines.push(line);  // 流程播报(准备/收敛/完成)留在进度条
        } else if (类型 === "立项完成") {
          if (data.result) result = data.result;
        } else if (类型 === "错误") {
          es.close(); reject(new Error(data.内容 || "立项失败")); return;
        }
        paint();
        if (类型 === "run结束") { es.close(); resolve(); }  // 后台跑完,收尾
      };
      es.onerror = () => { es.close(); reject(new Error("会话流连接中断")); };
    });
    running = false; window.__圆桌跑着 = false; window.__当前会话 = null; clearInterval(timer); paint();
    inp.value = ""; btn.disabled = false; btn.textContent = "立为任务";
    const r = result || {};
    const 文 = r.待回应
      ? `圆桌停在『要问你』：${(r.待船主 || []).join("、") || "有几处要先问你"}。去左栏点开这个项目室、在对话框回一句，它就带着你的回话接着开。`
      : r.评估结论
      ? `开完会了。项目经理的结论：\n\n${r.评估结论}\n\n（讨论/评估题不出工单；若要真派活，到右栏看「待确认开工」）`
      : `已立项，${(r.工单 || []).length} 张子工单在右栏「待确认开工」等你点头。`;
    临时.push({ k: "系统", who: "办公室", text: 文 });
    临时.push({ k: "分割", text: "本次完成 · " + new Date().toTimeString().slice(0, 5) });  // 轮次分割线:本轮结束
    // 债⑤:开完会清掉立项流式塞的临时发言气泡(k:"话")——发言已落盘协同,由正式 /hall 以气泡形态接管,
    // 这样气泡仍在、且不与正式记录重复(参照 说话() 的成熟模式)。只留系统卡(立项进度/办公室文案)。
    H.rail = null; await Promise.all([拉看板(), 拉大厅()]); 临时 = 临时.filter(x => x.k === "系统" || x.k === "分割"); 渲染大厅();
  } catch (e) {
    running = false; window.__圆桌跑着 = false; window.__当前会话 = null; clearInterval(timer); lines.push("失败：" + e); paint();
    btn.disabled = false; btn.textContent = "立为任务"; toast("立项失败：" + e, "bad");
  }
}
async function 点名() {
  if (视图.t !== "hall") 去({ t: "hall" });
  const item = { k: "系统", who: "办公室", text: "点名结果\n运行中 0.0s\n并行自检准备中…" };
  临时.push(item); 渲染大厅();
  const start = performance.now();
  const lines = ["并行自检准备中…"];
  let running = true;
  const paint = () => {
    const used = ((performance.now() - start) / 1000).toFixed(1);
    item.text = `点名结果\n${running ? "运行中" : "已完成"} ${used}s\n${lines.join("\n")}`;
    if (视图.t === "hall") 渲染大厅();  // 同上:不把进项目室的用户拉回大厅
  };
  const timer = setInterval(paint, 500);
  try {
    const es = new EventSource("/rollcall");
    es.onmessage = async ev => {
      const data = JSON.parse(ev.data || "{}");
      if (data.line) {
        if (lines.length === 1 && lines[0] === "并行自检准备中…") lines.length = 0;
        lines.push(data.line);
      }
      paint();
      if ((data.line || "").startsWith("说明:")) {
        running = false;
        clearInterval(timer);
        es.close();
        paint();
        await 拉看板();
        渲染左栏();
      }
    };
    es.onerror = () => {
      running = false;
      clearInterval(timer);
      lines.push("点名连接中断。");
      es.close();
      paint();
    };
  } catch (e) {
    running = false;
    clearInterval(timer);
    lines.push("点名失败：" + e);
    paint();
  }
  paint();
}

/* ---------- 项目室 ---------- */
function 渲染房间() {
  const s = $("#stage"); H.stageKind = "room";
  if (!详情 || 详情.id !== 视图.id) {
    if (H.stage !== "loading-" + 视图.id) { H.stage = "loading-" + 视图.id; s.innerHTML = `<div class="stage-pad"><div class="empty" style="margin-top:14vh">正在打开项目室…</div></div>`; }
    return;
  }
  const d = 详情; const sig = JSON.stringify(d);
  if (H.stage === sig) return;
  const sp = {}; s.querySelectorAll("[data-log]").forEach(e => sp[e.dataset.log] = e.scrollTop);
  s.querySelectorAll("details[data-keep]").forEach(e => 展开状态[e.dataset.keep] = e.open);
  H.stage = sig;

  let html = `<div class="stage-pad"><h1>${esc(d.标题)}</h1><div class="meta">立项 ${esc(d.时间 || "")} · ${esc(d.id)}</div>`;
  html += `<div class="room-talk card"><div class="lbl">项目对话 · 绑定本工单</div><div class="room-chat-log">`;
  const mem = d.项目记忆 || [];
  if (!mem.length) html += `<div class="empty" style="padding:8px 0;text-align:left">本项目还没有单独对话。这里说的话会进入本项目记忆，相关岗位下次会看到。</div>`;
  mem.forEach(m => {
    const mine = String(m.who || "").startsWith("船主");
    html += `<div class="room-msg ${mine ? "me" : "them"}"><div class="nm">${esc((mine ? "你" : m.who) + (m.时间 ? " · " + m.时间 : ""))}</div><div>${esc(m.text || "")}</div></div>`;
  });
  html += `</div><div class="room-say"><textarea rows="1" id="roomSay" placeholder="对这个项目说话… @岗位 指名；内容会绑定到本工单历史"></textarea><button class="prime sm" id="roomSayBtn">发送</button></div></div>`;
  if (d.需求) html += `<div class="card"><div class="lbl">立项 · 船主需求</div><div class="quote">${esc(d.需求)}</div></div>`;
  if (d.项目会) html += `<details class="card meeting-minutes" open data-keep="${esc(d.id)}-static-meeting"><summary class="lbl">项目会纪要 · 方案从这里来</summary><pre>${esc(d.项目会)}</pre></details>`;
  if (d.覆盖说明 || (d.泳道 || []).length) {
    const hasMeetingPlan = Boolean(d.项目会 || d.覆盖说明 || (d.泳道 || []).some(l => l.过程?.会议依据));
    html += `<div class="card"><div class="lbl">${hasMeetingPlan ? "会议结论落地 · 待确认工单" : "待确认工单 · 准备开工"}</div>`;
    if (d.覆盖说明) html += `<div class="muted" style="line-height:1.65;margin-bottom:10px">${esc(d.覆盖说明)}</div>`;
    (d.泳道 || []).forEach(l => {
      const mm = l.模型 || {};
      const basis = l.过程?.会议依据 ? `<small>会议依据：${esc(l.过程.会议依据)}</small>` : "";
      html += `<div class="claim"><div class="role">${esc(mm.title || l.角色)}<small>${esc(mm.model || "")}</small></div><div class="desc">${basis}${esc((l.任务 || "").slice(0, 160))}</div></div>`;
    });
    html += `</div>`;
  }
  if ((d.会议协同 || []).length) {
    html += `<div class="lbl" style="margin:24px 0 12px">项目会与协同 · 可旁听/参会</div>`;
    (d.会议协同 || []).forEach(m => {
      html += `<div class="meet-card"><div class="meet-head"><div><b>${esc(m.标题 || m.类型 || "会议")}</b><span>${esc(m.类型 || "")} · 主持 ${esc(m.主席 || "")}</span></div><em>${esc(m.状态 || "")}</em></div>`;
      if ((m.参会 || []).length) html += `<div class="meet-people">参会：${(m.参会 || []).map(x => esc(x)).join("、")}</div>`;
      html += `<div class="meet-events">`;
      (m.事件 || []).forEach(ev => {
        const target = ev.目标 ? ` → ${esc(ev.目标)}` : "";
        const task = ev.关联工单 ? ` · ${esc(ev.关联工单)}` : "";
        const need = ev.需回执 ? `<span class="need-receipt">需回执</span>` : "";
        const mine = ev.发言人 === "船主" ? " owner" : ev.发言人 === "项目经理" ? " manager" : "";
        html += `<div class="meet-ev${mine}" data-event="${esc(ev.id)}"><div class="meet-meta"><b>${esc(ev.类型 || "")}</b><span>${esc(ev.发言人 || "")}${target}${task}</span><small>${esc(ev.短时 || "")}</small>${need}</div><div class="meet-body">${esc(ev.内容 || "")}</div></div>`;
      });
      html += `</div><div class="meet-say"><textarea rows="1" data-meeting-input="${esc(m.id)}" placeholder="以船主身份参会发言…"></textarea><button class="prime sm" data-meeting-send="${esc(m.id)}">发言</button></div></div>`;
    });
  }
  html += `<div class="lbl" style="margin:24px 0 12px">泳道 · 各自干各自的</div>`;
  (d.泳道 || []).forEach(l => {
    const st = 态(l);
    const mm = l.模型 || {};
    html += `<div class="lane"><div class="head"><div class="dot ${st.dot}"></div><div class="role">${esc(mm.title || l.角色)} <small>${esc(mm.model || "")}</small></div>
      <span class="pill ${st.cls}">${esc(st.txt)}</span><span class="steps">${l.步数}/${esc(l.预算 || "?")} 步</span></div>`;
    html += `<div class="process"><div class="pt">过程札记</div>`;
    if (l.过程?.任务理解) html += `<div class="pitem"><b>理解</b><span>${esc(l.过程.任务理解).slice(0, 420)}</span></div>`;
    if (l.过程?.执行要求) html += `<div class="pitem"><b>验收</b><span>${esc(l.过程.执行要求).slice(0, 360)}</span></div>`;
    if (l.过程?.白名单) html += `<div class="pitem"><b>边界</b><span>${esc(l.过程.白名单)}</span></div>`;
    if (l.过程?.会议依据) html += `<div class="pitem"><b>依据</b><span>${esc(l.过程.会议依据)}</span></div>`;
    if (l.过程?.预算依据 || l.过程?.预计字数 || l.过程?.预计token) {
      const parts = [];
      if (l.过程.预算依据) parts.push(esc(l.过程.预算依据));
      if (l.过程.预计字数) parts.push("预计字数：" + esc(l.过程.预计字数));
      if (l.过程.预计token) parts.push("预计token：" + esc(l.过程.预计token));
      html += `<div class="pitem"><b>预算</b><span>${parts.join("<br>")}</span></div>`;
    }
    if (l.过程?.产物?.length) html += `<div class="pitem"><b>产物</b><span>${l.过程.产物.map(x => `${esc(x.路径)}：${esc(x.状态)}，${esc(x.说明)}`).join("<br>")}</span></div>`;
    if (l.过程?.正式报告?.length) {
      html += `<div class="pt" style="margin-top:8px">正式报告</div>`;
      l.过程.正式报告.forEach(r => {
        html += `<div class="report-card"><div class="report-head"><b>${esc(r.类别 || "报告")}</b><span>${esc((r.时间 || "").replace("T", " "))}</span></div>
          <div class="report-row"><b>阶段</b><span>${esc(r.阶段 || "")}</span></div>
          <div class="report-row"><b>原因</b><span>${esc(r.原因 || "")}</span></div>
          <div class="report-row"><b>判断</b><span>${esc(r.判断 || "")}</span></div>
          <div class="report-row"><b>下一步</b><span>${esc(r.下一步 || "")}</span></div></div>`;
      });
    }
    if (l.过程?.事件?.length) {
      html += `<div class="pt" style="margin-top:8px">过程事件</div>`;
      l.过程.事件.forEach(ev => {
        html += `<div class="pitem event-item" data-event="${esc(ev.id)}"><b>${esc(ev.类别)}</b><span><strong>${esc(ev.标题)}</strong>${ev.说明 ? `<br>${esc(ev.说明)}` : ""}<small>${esc(ev.短时 || "")}</small></span></div>`;
      });
    } else if (l.过程?.步骤?.length) {
      html += `<div class="pitem"><b>正在做</b><span>${l.过程.步骤.map(x => "· " + esc(x)).join("<br>")}</span></div>`;
    }
    if (!l.过程?.任务理解 && !l.过程?.执行要求 && !l.过程?.白名单) html += `<div class="pitem"><b>记录</b><span>这条泳道还没有写出可追溯过程。</span></div>`;
    html += `</div>`;
    if (l.列 === "进行中") html += `<div class="acts" data-lane="${esc(l.name)}"></div>`;
    if (l.列 === "待验收") html += `<div class="acts" data-verdict="${esc(l.name)}"></div>`;
    if (l.过程?.观察?.length) {
      html += `<div class="raw-log obs-list"><div class="raw-title">观察摘要与原文（员工下一步会看到）</div>`;
      l.过程.观察.forEach((o, idx) => {
        const key = `${l.name}-obs-${idx}`;
        html += `<details class="obs-summary" data-keep="${esc(key)}"><summary>${esc(o.摘要 || `${o.动作}：${o.目标}`)}</summary><pre>${esc(o.原文 || "")}</pre></details>`;
      });
      html += `</div>`;
    }
    if (l.日志) html += `<details class="raw-log" data-keep="${esc(l.name)}-log"><summary>完整过程日志</summary><pre data-log="${esc(l.name)}">${esc(l.日志.trim())}</pre></details>`;
    (l.裁决 || []).forEach(v => html += `<div class="verdict">${esc(v.replace(/^>\s*/, ""))}</div>`);
    html += `</div>`;
  });
  const asks = d.请示 || [];
  if (asks.length) {
    html += `<div class="lbl" style="margin:24px 0 12px">请示与决策 · 谁卡住了、经理怎么判、你怎么裁</div>`;
    asks.forEach(c => {
      html += `<div class="ask-card"><div class="who">${esc(c.岗位 || "")} ｜ ${esc(c.桶)}</div>
        <div class="body">${esc(c.内容 || "")}${c.建议 ? "\n建议：" + esc(c.建议) : ""}</div>`;
      if (c.经理理由) html += `<div class="mgr">项目经理预审（${esc(c.经理裁决 || "")}）：${esc(c.经理理由)}</div>`;
      if (c.船主理由) html += `<div class="mgr">你的裁决（${esc(c.状态)}）：${esc(c.船主理由)}</div>`;
      html += `<div class="acts" data-ask="${esc(c.id)}" data-bucket="${esc(c.桶)}"></div></div>`;
    });
  }
  s.innerHTML = html + "</div>";

  const roomInp = $("#roomSay");
  const roomBtn = $("#roomSayBtn");
  if (roomInp && roomBtn) {
    roomInp.onkeydown = e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) roomBtn.click(); };
    roomInp.oninput = () => 自适应输入(roomInp);
    roomBtn.onclick = () => 项目说话(d.id, roomInp);
  }

  s.querySelectorAll("[data-lane]").forEach(host => {
    const name = host.dataset.lane;
    host.append(bt("ghost sm", "停止", () => 停止(name)),
      bt("ghost sm", "追加指示", () => 问(host.parentElement, "对这个角色追加一句指示…", v => 追加(name, v))));
  });
  s.querySelectorAll("[data-verdict]").forEach(host => host.appendChild(验收按钮(host.parentElement, host.dataset.verdict, "待验收")));
  s.querySelectorAll("[data-ask]").forEach(host => {
    if (host.dataset.bucket !== "待船主") return;
    const id = host.dataset.ask;
    host.append(bt("ok sm", "批", () => 决策(id, "批", "")), bt("bad sm", "驳", () => 问(host, "驳回理由…", v => 决策(id, "驳", v))));
  });
  s.querySelectorAll("[data-meeting-send]").forEach(btn => {
    btn.onclick = () => {
      const id = btn.dataset.meetingSend;
      const inp = s.querySelector(`[data-meeting-input="${cssEsc(id)}"]`);
      const text = (inp?.value || "").trim();
      if (!text) return;
      inp.value = "";
      会议发言(id, text);
    };
  });
  s.querySelectorAll("details[data-keep]").forEach(e => {
    const key = e.dataset.keep;
    if (Object.prototype.hasOwnProperty.call(展开状态, key)) e.open = 展开状态[key];
    e.ontoggle = () => { 展开状态[key] = e.open; };
  });
  s.querySelectorAll("[data-log]").forEach(e => { if (sp[e.dataset.log] != null) e.scrollTop = sp[e.dataset.log]; });
  定位事件();
}

async function 项目说话(room, inp) {
  const text = (inp?.value || "").trim();
  if (!text) return;
  inp.value = "";
  临时.push({ k: "系统", who: "项目室", text: "项目对话发送中…" });
  try {
    const j = await post("/room_say", { room, text });
    (j.replies || []).forEach(r => 临时.push({ k: "系统", who: r.role, text: `${r.role}：${r.reply}` }));
    await 拉详情();
    await 拉大厅();
  } catch (e) {
    toast("项目对话失败：" + e, "bad");
  } finally {
    临时 = 临时.filter(x => x.text !== "项目对话发送中…");
  }
}

function 定位事件() {
  if (!视图.focus) return;
  const target = document.querySelector(`[data-event="${cssEsc(视图.focus)}"]`);
  if (!target) return;
  target.classList.add("focus-hit");
  target.scrollIntoView({ block: "center", behavior: "smooth" });
  setTimeout(() => target.classList.remove("focus-hit"), 2600);
}

/* ---------- 动作 ---------- */
async function 确认开工(names, btn) {
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spin"></span>开工中'; }
  try { const j = await post("/confirm", { names }); toast("已开工：" + (j.started || []).length + " 张", "good"); await 刷新(); }
  catch (e) { toast("开工失败：" + e, "bad"); if (btn) { btn.disabled = false; btn.textContent = "确认开工"; } }
}
async function 打回重分解(reason) { await post("/reject_decomp", { reason }); toast("已写入打回理由", "good"); await 刷新(); }
async function 裁决(name, col, v, reason) {
  await fetch("/verdict", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name, col, v, reason }) });
  toast(name.slice(0, 20) + "… → " + v, v === "通过" ? "good" : ""); await 刷新();
}
async function 决策(id, v, reason) { await post("/approval_decide", { id, v, reason }); toast("请示已" + v, v === "批" ? "good" : ""); await 刷新(); }
async function 停止(name) { const j = await post("/stop", { name }); toast(j.reply || "已请求停止"); await 刷新(); }
async function 继续任务(name, steps) { const j = await post("/resume", { name, steps }); toast(j.reply || "已继续", "good"); await 刷新(); }
async function 追加(name, text) { if (!text) return; const j = await post("/append", { name, text }); toast(j.reply || "已追加", "good"); await 刷新(); }
async function 会议发言(id, text) { const j = await post("/meeting_say", { id, text }); toast(j.reply || "已写入会议", "good"); await 拉详情(); await 拉大厅(); }
async function 急停() {
  const on = !(看板 && 看板.急停); const j = await post("/panic", { on });
  toast(j.reply || "", on ? "bad" : "good"); await 刷新();
}

/* ---------- 轮询 ---------- */
async function 拉看板() { 看板 = await getJSON("/board"); 渲染左栏(); 渲染右栏(); }
async function 拉大厅() { if (视图.t !== "hall") return; 大厅 = await getJSON("/hall"); if (视图.t === "hall") 渲染大厅(); }
async function 拉详情() {
  if (视图.t !== "room") return;
  const d = await getJSON("/room?id=" + encodeURIComponent(视图.id));
  详情 = d; if (视图.t === "room" && 视图.id === d.id) 渲染房间();
}
async function 刷新() {
  try { await 拉看板(); await 拉大厅(); await 拉详情(); 后台断线已提示 = false; }
  catch (e) {
    if (!后台断线已提示) toast("办公室后台连接断了，请重新双击打开办公室。", "bad");
    后台断线已提示 = true;
  }
}

/* ---------- 启动 ---------- */
(function () {
  骨架(); 渲染舞台(); 刷新();
  setInterval(刷新, 2000);
})();
