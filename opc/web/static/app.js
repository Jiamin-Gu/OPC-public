// OPC 前端：没有框架、没有构建步骤。通过 WebSocket 接收后端事件并渲染。
"use strict";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html !== undefined) e.innerHTML = html; return e; };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const ALL = "all";
const STATUS = { pending: "待开始", running: "进行中", done: "已完成", failed: "失败", skipped: "已跳过", cancelled: "已取消", paused: "已暂停", resync: "待同步",
                 review: "待审查", reviewing: "审查中", rework: "返工中", escalated: "等你裁决" };
const VERDICT = { pass: "✅ 通过", changes: "↩️ 要求修改", unclear: "⚠️ 没有给出结论", error: "⚠️ 审查失败",
  incomplete: "⏳ 没有做完", checks: "🧪 交付检查没通过" };
const SYSTEM_VERDICTS = ["incomplete", "checks"];  // 不是审查人给的结论，而是系统判定的

// 交付要求（提交时系统自动检查）和验收标准（审查人逐条判断）
function requirementsHtml(t) {
  const ds = t.deliverables || [], acc = t.acceptance || [];
  if (!ds.length && !acc.length) return "";
  let html = "";
  if (ds.length) {
    html += `<div class="req-title">📋 交付要求（提交时系统自动检查）</div><ul class="req-list">${ds.map((d) => {
      const parts = [];
      if ((d.sections || []).length) parts.push(`章节：${d.sections.map(esc).join("、")}`);
      if ((d.must_mention || []).length) parts.push(`写到：${d.must_mention.map(esc).join("、")}`);
      if (d.min_chars) parts.push(`至少 ${d.min_chars} 字`);
      if (d.max_chars) parts.push(`建议不超过 ${d.max_chars} 字（超过时另外生成简洁版）`);
      if (d.template) parts.push(`格式：${esc(d.template)}`);
      return `<li><code>${esc(d.path)}</code>${parts.length ? "：" + parts.join("；") : ""}</li>`;
    }).join("")}</ul>`;
  }
  if (acc.length) html += `<div class="req-title">✅ 验收标准（审查人逐条判断）</div><ol class="req-list">${acc.map((a) => `<li>${esc(a)}</li>`).join("")}</ol>`;
  return html;
}

// 审查记录的补充：复审时上轮问题的处理情况、建议（不退回）
function reviewExtrasHtml(r) {
  let html = "";
  if ((r.previous || []).length) {
    html += `<div class="rv-prev">${r.previous.map((p) => `<div>${p.status === "resolved" ? "✅" : "❌"} 上轮第 ${p.no} 条${
      p.status === "resolved" ? "已解决" : "没有解决"}${p.note ? `：${esc(p.note)}` : ""}</div>`).join("")}</div>`;
  }
  if ((r.suggestions || []).length) {
    html += `<details class="rv-sugg"><summary>💡 建议（不退回）${r.suggestions.length} 条</summary><ul>${
      r.suggestions.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></details>`;
  }
  return html;
}
const isPlanReview = () => review && review.type !== "escalation";
const isEscalation = () => review && review.type === "escalation";
const SPECIAL = {
  ceo: { icon: "👤", name: "你（CEO）" },
  planner: { icon: "🗂️", name: "规划助手" },
  reporter: { icon: "📊", name: "汇报助手" },
  manager: { icon: "🧭", name: "项目经理" },
  system: { icon: "⚙️", name: "系统" },
};

let ws = null;
let home = null;            // 首页数据：公司列表、预设角色、默认模型
let presets = {};           // 角色 id -> 预设信息
let C = null;               // 当前打开的公司
let S = { tasks: {}, channels: {}, usage: {} };  // 公司状态（和后端 LangGraph state 对应）
let review = null, busy = false, paused = null, unfinished = false, roleStatus = {}, files = [];
// 运行计时：服务器时间和本机时间的差，用来显示"已用多久"
let runStarted = null, phaseSince = {}, clockSkew = 0;
let channel = ALL;
const MANAGER = "manager";      // 项目经理频道：和调度助手对话
const triaging = new Set();     // 调度助手正在理解 CEO 的话的频道
const topics = {};              // 角色频道 -> 正在讨论的工作项（任务 id），空字符串是自由聊天
const unread = new Set();
const streams = new Map();  // msg_id -> 正在流式生成的消息
const openTasks = new Set();
let openFile = null;
// 全公司频道的显示方式：simple 只看任务进展卡片和成果（过程收进"过程详情"），detail 什么都显示
let allView = (() => { try { return localStorage.getItem("opc.allView") || "simple"; } catch { return "simple"; } })();
const simpleAll = () => channel === ALL && allView === "simple";
const openDetails = new Set();  // 展开了"过程详情"的任务卡片
let coderAvailable = false;

function who(sender) {
  if (SPECIAL[sender]) return SPECIAL[sender];
  const r = presets[sender];
  return r ? { icon: r.icon, name: r.name } : { icon: "❓", name: sender };
}

function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 3500);
}

// ---------------------------------------------------------------- Markdown（够用的轻量实现）

function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

function markdown(src) {
  const lines = String(src || "").split("\n");
  const out = [];
  let i = 0;
  const isTableSep = (l) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
  const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line)) {
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      out.push(`<pre><code>${esc(buf.join("\n"))}</code></pre>`);
    } else if (/^#{1,4}\s/.test(line)) {
      const n = line.match(/^#+/)[0].length;
      out.push(`<h${n}>${inline(line.replace(/^#+\s*/, ""))}</h${n}>`);
      i++;
    } else if (line.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(cells(lines[i++]));
      out.push(`<table><thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
    } else if (/^\s*([-*]|\d+\.)\s/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s/.test(lines[i])) items.push(`<li>${inline(lines[i++].replace(/^\s*([-*]|\d+\.)\s/, ""))}</li>`);
      out.push(ordered ? `<ol>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`);
    } else if (/^>\s?/.test(line)) {
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) buf.push(inline(lines[i++].replace(/^>\s?/, "")));
      out.push(`<blockquote>${buf.join("<br>")}</blockquote>`);
    } else if (!line.trim()) {
      i++;
    } else {
      const buf = [];
      while (i < lines.length && lines[i].trim() && !/^(```|#{1,4}\s|\s*([-*]|\d+\.)\s|>)/.test(lines[i])
             && !(lines[i].includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1]))) buf.push(lines[i++]);
      out.push(`<p>${buf.map(inline).join("<br>")}</p>`);
    }
  }
  return out.join("");
}

// ---------------------------------------------------------------- 首页

function renderHome() {
  const list = $("companyList");
  list.innerHTML = "";
  if (!home.companies.length) {
    list.appendChild(el("div", "empty-note", "还没有公司。在右边创建第一家吧 👉"));
  }
  for (const c of home.companies) {
    const card = el("div", "company-card");
    card.innerHTML = `<div class="cc-main"><div class="cc-name"></div><div class="cc-mission"></div>
      <div class="cc-roles">${c.roles.map((r) => presets[r]?.icon || "").join("")}</div></div>
      <button class="icon-btn" title="删除公司">✕</button>`;
    card.querySelector(".cc-name").textContent = c.name;
    card.querySelector(".cc-mission").textContent = c.mission || "（没有设定目标）";
    card.onclick = () => send({ type: "open_company", id: c.id });
    card.querySelector(".icon-btn").onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(`删除公司「${c.name}」？\n它的对话、任务和文件都会被删除，无法恢复。`)) return;
      const res = await fetch(`/api/companies/${encodeURIComponent(c.id)}`, { method: "DELETE" });
      if (!res.ok) toast((await res.json()).detail || "删除失败");
    };
    list.appendChild(card);
  }
  if (!$("roleGrid").children.length) {
    renderRoleGrid();
    setSelect($("cModel"), modelOptions(globalLabel()), "");
    $("cEffort").innerHTML = effortOptions();
    $("cEffort").value = "standard";
  }
  $("runToggle").hidden = !home.coder?.available;
  const sbx = home.coder?.sandbox;
  $("runWhere").innerHTML = !home.coder?.available ? "" : sbx?.ok
    ? "🛡️ 检测到 Docker：命令在沙箱容器里执行，只看得到公司文件区，默认断网，碰不到你电脑上的其他东西。"
    : `⚠️ 没有检测到可用的 Docker 沙箱（${esc(sbx?.why || "")}）：命令会直接在你的电脑上执行，只在信任的环境中开启。`;
  const web = home.web || {};  // 联网能力来自 MCP 服务器：连不上时不能勾选
  $("cWeb").disabled = !web.available;
  if (!web.available) $("cWeb").checked = false;
  const failed = (web.servers || []).filter((s) => s.state === "error");
  $("webNote").innerHTML = web.available
    ? `角色需要最新信息（市场、竞品、新闻、技术文档）时可以上网查：${(web.servers || []).filter((s) => s.tools)
        .map((s) => `${esc(s.name)}（${s.tools} 个工具）`).join("、")}。`
      + (web.servers || []).filter((s) => s.error).map((s) => `<br>⚠ ${esc(s.name)}：${esc(s.error)}`).join("")
    : web.starting ? "正在启动 MCP 服务器……稍后刷新页面"
    : web.error ? `MCP 配置有错：${esc(web.error)}`
    : failed.length ? `MCP 服务器没有启动成功：${failed.map((s) => `${esc(s.name)}（${esc(s.error)}）`).join("；")}`
    : `需要先配置 MCP 服务器：在 <code>${esc(web.config || "~/.opc/mcp.json")}</code> 里写（通用的 mcpServers 格式，Cursor、Claude 等工具都用这种），见使用教程。`;
  $("coderMissing").hidden = !!home.coder?.available;
}

function renderRoleGrid() {
  // 6 个预设角色总在；从角色库添加的角色排在后面，取消选择就从这里去掉
  const grid = $("roleGrid");
  const defaults = new Set(["pm", "engineer", "designer", "qa"]);
  for (const r of home.presets) grid.appendChild(roleCard(r, defaults.has(r.id)));
  updateRoleCount();
}

function roleCard(r, on) {
  const card = el("div", "role-card" + (on ? " on" : ""));
  card.dataset.id = r.id;
  card.innerHTML = `
    <div class="rc-head"><span class="rc-icon">${r.icon}</span><span class="rc-name">${esc(r.name)}</span>
      ${r.department ? `<span class="rc-dept">${esc(r.department_name)}</span>` : ""}<span class="rc-check">✓</span></div>
    <div class="rc-tag">${esc(r.tagline)}</div>
    <button type="button" class="rc-config-toggle">⚙ 单独设置模型</button>
    <div class="role-config" hidden>
      <select data-k="model">${modelOptions("跟随公司默认")}</select>
    </div>`;
  card.onclick = (e) => {
    if (e.target.closest(".role-config, .rc-config-toggle")) return;
    if (r.department && card.classList.contains("on")) card.remove();  // 角色库的角色取消选择 = 从团队里去掉
    else card.classList.toggle("on");
    updateRoleCount();
  };
  card.querySelector(".rc-config-toggle").onclick = () => {
    const cfg = card.querySelector(".role-config");
    cfg.hidden = !cfg.hidden;
  };
  return card;
}

// ---------- 模型选择：选项的值是 "连接id|模型名"，空表示跟随上一层

const encodeRef = (ref) => (ref && ref.connection ? `${ref.connection}|${ref.model || ""}` : "");
function decodeRef(v) {
  if (!v || v === "__legacy") return null;
  const i = v.indexOf("|");
  return { connection: v.slice(0, i), model: v.slice(i + 1) };
}

function modelOptions(followLabel, extra = "") {
  const m = home?.models || { connections: [] };
  return `<option value="">${esc(followLabel)}</option>${extra}` + m.connections.map((c) =>
    `<optgroup label="${esc(c.name)}${c.has_key ? "" : "（没有 API Key）"}">`
    + c.models.map((x) => `<option value="${esc(encodeRef({ connection: c.id, model: x }))}">${esc(x)}</option>`).join("")
    + "</optgroup>").join("");
}

function setSelect(sel, html, value) {
  sel.innerHTML = html;
  if (value && ![...sel.options].some((o) => o.value === value)) {  // 连接里没列出的模型：加一项，保持原选择
    const ref = decodeRef(value);
    sel.add(new Option(`${ref?.model || value}`, value));
  }
  sel.value = value || "";
}

function globalLabel() {
  return `全局默认（${home?.models?.default_label || home?.defaults?.model || ""}）`;
}

function refreshModelSelects() {
  // 模型连接改了：所有模型下拉框重新生成，尽量保留原来的选择
  setSelect($("cModel"), modelOptions(globalLabel()), $("cModel").value);
  document.querySelectorAll('.role-card [data-k="model"]').forEach((s) => setSelect(s, modelOptions("跟随公司默认"), s.value));
}

function refHasKey(ref) {
  const m = home?.models;
  if (!m) return true;
  const conn = m.connections.find((c) => c.id === (ref || m.default)?.connection);
  return !!conn?.has_key || !!home.demo;
}

function updateRoleCount() {
  $("roleCount").textContent = `已选 ${document.querySelectorAll(".role-card.on").length} 位`;
}

$("createForm").onsubmit = async (e) => {
  e.preventDefault();
  const err = $("createError");
  const companyModel = decodeRef($("cModel").value);
  const roles = [...document.querySelectorAll(".role-card.on")].map((card) => ({
    id: card.dataset.id,
    model: decodeRef(card.querySelector('[data-k="model"]').value),
  }));
  if (!roles.length) { err.textContent = "至少选择一位团队成员"; return; }
  const noKey = roles.find((r) => !refHasKey(r.model || companyModel));
  if (noKey) {
    err.textContent = `${presets[noKey.id].name}用的模型连接没有 API Key：点上面的「⚙ 管理模型连接」填写`;
    return;
  }
  err.textContent = "";
  const res = await fetch("/api/companies", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: $("cName").value, mission: $("cMission").value, roles, model: companyModel,
                           allow_run: !!home.coder?.available && $("cAllowRun").checked,
                           web: !!home.web?.available && $("cWeb").checked, kb: $("cKb").checked,
                           effort: $("cEffort").value || "standard" }),
  });
  const data = await res.json();
  if (!res.ok) { err.textContent = data.detail?.[0]?.msg || data.detail || "创建失败"; return; }
  $("createForm").reset();
  $("roleGrid").innerHTML = "";  // 下次回到首页重新从预设角色开始
  send({ type: "open_company", id: data.id });
};

// ---------- 弹窗

document.querySelectorAll(".dlg").forEach((d) => {
  d.querySelectorAll("[data-close]").forEach((b) => { b.onclick = () => d.close(); });
  d.addEventListener("click", (e) => { if (e.target === d && !d.dataset.sticky) d.close(); });  // 点遮罩关闭（编辑中的不关）
});

// ---------- 编辑计划（审批时直接改：负责人、审查人、要求、依赖、验收标准、交付文件，增删任务；不调用规划助手）

let planRows = [], planNewSeq = 0;

function openPlanEditor() {
  planRows = review.task_ids.map((id) => S.tasks[id]).filter(Boolean).map((t) => ({
    id: t.id, key: t.id, title: t.title, detail: t.detail, assignee: t.assignee, reviewer: t.reviewer || "",
    depends_on: [...(t.depends_on || [])], acceptance: (t.acceptance || []).join("\n"),
    deliverables: (t.deliverables || []).map((d) => d.path).join("\n") }));
  $("planEditErr").textContent = $("planEditNote").textContent = "";
  renderPlanEditor();
  $("planDlg").showModal();
}

function roleOptions(selected, emptyLabel) {
  return (emptyLabel ? `<option value="">${emptyLabel}</option>` : "") + C.roles.map((r) => {
    const p = presets[r.id] || {};
    return `<option value="${esc(r.id)}"${r.id === selected ? " selected" : ""}>${p.icon || ""} ${esc(p.name || r.id)}</option>`;
  }).join("");
}

function renderPlanEditor() {
  const body = $("planEditBody");
  body.innerHTML = "";
  planRows.forEach((r, i) => {
    const earlier = planRows.slice(0, i);  // 只能依赖排在前面的任务，不会出现循环
    const card = el("div", "pe-task");
    card.innerHTML = `<div class="pe-head"><b>${i + 1}</b><input class="pe-title" value="${esc(r.title)}" placeholder="任务标题">
        <button class="icon-btn" data-del title="删除这个任务">🗑</button></div>
      <div class="pe-row"><label>负责人<select data-f="assignee">${roleOptions(r.assignee)}</select></label>
        <label>审查人<select data-f="reviewer">${roleOptions(r.reviewer, "不审查")}</select></label></div>
      <label>具体要求<textarea data-f="detail" rows="3">${esc(r.detail)}</textarea></label>
      ${earlier.length ? `<div class="pe-deps"><span>等这些任务完成后再开始：</span>${earlier.map((e) =>
        `<label><input type="checkbox" data-dep="${esc(e.key)}"${r.depends_on.includes(e.key) ? " checked" : ""}>${esc(e.title || "（未命名）")}</label>`).join("")}</div>` : ""}
      <div class="pe-row"><label>验收标准（一行一条）<textarea data-f="acceptance" rows="3">${esc(r.acceptance)}</textarea></label>
        <label>交付文件（一行一个路径）<textarea data-f="deliverables" rows="3" placeholder="docs/PRD.md">${esc(r.deliverables)}</textarea></label></div>`;
    card.querySelector(".pe-title").oninput = (e) => { r.title = e.target.value; };
    card.querySelectorAll("[data-f]").forEach((x) => { x.oninput = x.onchange = () => { r[x.dataset.f] = x.value; }; });
    card.querySelectorAll("[data-dep]").forEach((x) => {
      x.onchange = () => { r.depends_on = x.checked ? [...r.depends_on, x.dataset.dep] : r.depends_on.filter((d) => d !== x.dataset.dep); };
    });
    card.querySelector("[data-del]").onclick = () => deletePlanRow(i);
    body.appendChild(card);
  });
}

function deletePlanRow(i) {
  const [gone] = planRows.splice(i, 1);
  const affected = planRows.filter((r) => r.depends_on.includes(gone.key));
  affected.forEach((r) => { r.depends_on = r.depends_on.filter((d) => d !== gone.key); });
  renderPlanEditor();
  // 提示写在编辑窗口里（页面的浮动提示会被窗口挡住）
  $("planEditNote").textContent = affected.length
    ? `${affected.map((r) => `《${r.title}》`).join("、")}不再等待《${gone.title}》` : "";
}

function submitPlanEdit(approve) {
  const lines = (s) => String(s || "").split("\n").map((x) => x.trim()).filter(Boolean);
  $("planEditErr").textContent = "";
  send({ type: "plan_edit", approve, tasks: planRows.map((r) => ({
    id: r.id, key: r.key, title: r.title.trim(), detail: r.detail, assignee: r.assignee, reviewer: r.reviewer,
    depends_on: r.depends_on, acceptance: lines(r.acceptance), deliverables: lines(r.deliverables) })) });
}

$("planAddTask").onclick = () => {
  planRows.push({ id: "", key: `new${++planNewSeq}`, title: "", detail: "", assignee: C.roles[0]?.id || "", reviewer: "",
                  depends_on: [], acceptance: "", deliverables: "" });
  renderPlanEditor();
  $("planEditBody").lastElementChild?.querySelector(".pe-title")?.focus();
};
$("planSave").onclick = () => submitPlanEdit(false);
$("planSaveApprove").onclick = () => submitPlanEdit(true);

// ---------- 角色库

let libDept = "";
$("libraryBtn").onclick = () => {
  $("libSearch").value = "";
  libDept = "";
  renderLibrary();
  $("libraryDlg").showModal();
  $("libSearch").focus();
};
$("libSearch").oninput = () => renderLibrary();

function pickedIds() {
  return new Set([...document.querySelectorAll(".role-card.on")].map((c) => c.dataset.id));
}

function renderLibrary() {
  const lib = home.library || [];
  const depts = [...new Map(lib.map((r) => [r.department, r.department_name])).entries()];
  const box = $("libDepts");
  box.innerHTML = "";
  for (const [id, name] of [["", "全部"], ...depts]) {
    const n = id ? lib.filter((r) => r.department === id).length : lib.length;
    const chip = el("button", "dept-chip" + (id === libDept ? " on" : ""), `${esc(name)} ${n}`);
    chip.type = "button";
    chip.onclick = () => { libDept = id; renderLibrary(); };
    box.appendChild(chip);
  }
  const q = $("libSearch").value.trim().toLowerCase();
  const items = lib.filter((r) => (!libDept || r.department === libDept)
    && (!q || `${r.name} ${r.tagline} ${r.id} ${r.department_name}`.toLowerCase().includes(q)));
  const picked = pickedIds();
  const list = $("libList");
  list.innerHTML = "";
  if (!items.length) list.appendChild(el("div", "empty-note", "没有找到匹配的角色"));
  for (const r of items) {
    const item = el("div", "lib-item" + (picked.has(r.id) ? " on" : ""));
    item.innerHTML = `<div class="li-head"><span>${r.icon}</span><span>${esc(r.name)}</span></div>
      <div class="li-tag">${esc(r.tagline)}</div>`;
    item.title = `${r.department_name} · ${r.tagline}${r.can_code ? "\n可以用编码 agent 写代码、运行测试" : ""}`;
    item.onclick = () => {
      const card = document.querySelector(`.role-card[data-id="${CSS.escape(r.id)}"]`);
      if (card) card.remove();
      else $("roleGrid").appendChild(roleCard(r, true));
      item.classList.toggle("on", !card);
      updateRoleCount();
      updateLibPicked();
    };
    list.appendChild(item);
  }
  $("libCount").textContent = `${lib.length} 个角色`;
  updateLibPicked();
}

function updateLibPicked() {
  const lib = new Set((home.library || []).map((r) => r.id));
  const n = [...pickedIds()].filter((id) => lib.has(id)).length;
  $("libPicked").textContent = n ? `已从角色库添加 ${n} 位` : "点击角色加入团队，再点一次移除";
}

// ---------- 模型连接

$("modelsBtn").onclick = () => openModels();
$("coManage").onclick = () => { $("companyModelsDlg").close(); openModels(); };

function openModels(firstRun = false) {
  $("modelsError").textContent = "";
  $("connList").innerHTML = "";
  // .env 里没填密钥时的"环境变量"连接用不了，不显示（第一次使用的人看到它只会困惑）
  for (const c of home.models.connections) if (!c.env || c.has_key) $("connList").appendChild(connCard(c));
  $("modelsFirst").hidden = !firstRun;
  $("modelsDlg").showModal();
  if (firstRun) {  // 准备好一个空的 DeepSeek 连接，光标停在 API Key 上；欢迎说明留在视野里
    $("connAdd").click();
    $("modelsDlg").querySelector(".dlg-body").scrollTop = 0;
  }
  refreshGlobalSelect(firstRun ? "" : encodeRef(home.models.default));
  if (firstRun) {  // 全局默认选新加的连接的第一个模型
    const id = document.querySelector("#connList .conn-card:last-child").dataset.id;
    const opt = [...$("globalModel").options].find((o) => o.value.startsWith(id + "|"));
    if (opt) $("globalModel").value = opt.value;
  }
}

// 第一次打开（还没有任何可用的模型）：自动弹出模型连接，告诉用户填一个 API Key 就能开始
let modelsPrompted = false;
function promptFirstRun() {
  if (modelsPrompted || !home || home.demo || home.defaults.has_key || C) return;
  modelsPrompted = true;
  openModels(true);
}

function providerSelect(value) {
  return home.providers.map((p) => `<option value="${esc(p.id)}"${p.id === value ? " selected" : ""}>${esc(p.name)}</option>`).join("");
}

function connCard(c) {
  const card = el("div", "conn-card");
  card.dataset.id = c.id || "";
  if (c.env) {
    card.innerHTML = `<div class="cc-top"><b></b><span class="muted">来自 .env（OPC_PROVIDER / OPC_BASE_URL / OPC_API_KEY / OPC_MODEL），在 .env 里修改</span></div>
      <div class="wide muted"></div>`;
    card.querySelector("b").textContent = c.name;
    card.querySelector(".wide").textContent = `${c.base_url} · ${c.models.join("、")} · ${c.has_key ? "已设置 API Key" : "没有 API Key"}`;
    card.dataset.env = "1";
    card.appendChild(priceRow(c.id));
    return card;
  }
  card.innerHTML = `
    <div class="cc-top"><b>连接</b><div class="spacer"></div><button type="button" class="icon-btn" title="删除这个连接">✕</button></div>
    <label>名字<input data-k="name"></label>
    <label>服务商<select data-k="provider">${providerSelect(c.provider || "deepseek")}</select></label>
    <label class="wide">接口地址<input data-k="base_url" placeholder="https://…/v1"></label>
    <label class="wide">API Key<input data-k="api_key" type="password" autocomplete="off"></label>
    <label class="wide">常用模型（逗号分隔，第一个是这个连接的默认模型）<input data-k="models"></label>`;
  card.appendChild(priceRow(c.id));
  const f = (k) => card.querySelector(`[data-k="${k}"]`);
  f("name").value = c.name || "";
  f("base_url").value = c.base_url || "";
  f("models").value = (c.models || []).join(", ");
  f("api_key").placeholder = c.has_key ? "已设置，留空表示不改" : "必填";
  f("provider").onchange = () => {
    // 换服务商：接口地址和模型换成它的默认值（还没改过的才换）
    const p = home.providers.find((x) => x.id === f("provider").value);
    const old = home.providers.find((x) => x.base_url && x.base_url === f("base_url").value.trim());
    if (!f("base_url").value.trim() || old) f("base_url").value = p?.base_url || "";
    if (!f("models").value.trim() || old) f("models").value = (p?.models || []).join(", ");
    if (!f("name").value.trim() || home.providers.some((x) => x.name === f("name").value.trim())) f("name").value = p?.name || "";
    refreshGlobalSelect($("globalModel").value);
  };
  ["name", "models"].forEach((k) => { f(k).onchange = () => refreshGlobalSelect($("globalModel").value); });
  card.querySelector(".icon-btn").onclick = () => { card.remove(); refreshGlobalSelect($("globalModel").value); };
  return card;
}

function priceRow(id) {
  // 价格只用来估算花费（元/百万 token），可以不填；不同模型价格不同时，填这个连接上最常用的那个
  const p = (home.models.prices || {})[id] || {};
  const row = el("div", "price-row");
  row.innerHTML = [["input", "输入价格"], ["cached", "缓存命中价格"], ["output", "输出价格"]].map(([k, label]) =>
    `<label>${label}（元/百万 token，可选）<input data-p="${k}" type="number" min="0" step="0.01" value="${p[k] || ""}"></label>`).join("")
    + `<div class="price-note">只用来估算花了多少钱，不填就只显示 token 数。价格在服务商官网的"价格"或"计费"页面能查到</div>`;
  return row;
}

function readPrices() {
  const out = {};
  document.querySelectorAll("#connList .conn-card").forEach((card) => {
    const p = {};
    card.querySelectorAll("[data-p]").forEach((i) => { if (i.value) p[i.dataset.p] = Number(i.value); });
    if (Object.keys(p).length && card.dataset.id) out[card.dataset.id] = p;
  });
  return out;
}

$("connAdd").onclick = () => {
  const p = home.providers[0];
  const card = connCard({ id: "", name: p.name, provider: p.id, base_url: p.base_url, models: p.models });
  $("connList").appendChild(card);
  card.querySelector('[data-k="api_key"]').focus();
};

function readConnections() {
  return [...document.querySelectorAll("#connList .conn-card:not([data-env])")].map((card, i) => {
    const f = (k) => card.querySelector(`[data-k="${k}"]`).value.trim();
    if (!card.dataset.id) card.dataset.id = `c-new${Date.now().toString(36)}${i}`;  // 还没保存的连接先用临时 id，后端会重新分配
    return { id: card.dataset.id, name: f("name"), provider: f("provider"), base_url: f("base_url"),
             api_key: f("api_key"), models: f("models").split(/[,，\s]+/).filter(Boolean) };
  });
}

function refreshGlobalSelect(value) {
  const env = home.models.connections.filter((c) => c.env && c.has_key);
  const conns = [...env, ...readConnections()];
  $("globalModel").innerHTML = conns.map((c) => `<optgroup label="${esc(c.name || "连接")}">`
    + (c.models || []).map((m) => `<option value="${esc(encodeRef({ connection: c.id, model: m }))}">${esc(m)}</option>`).join("")
    + "</optgroup>").join("");
  if ([...$("globalModel").options].some((o) => o.value === value)) $("globalModel").value = value;
}

$("modelsSave").onclick = async () => {
  const connections = readConnections();
  const res = await fetch("/api/models", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ connections, default: decodeRef($("globalModel").value), prices: readPrices() }),
  });
  const data = await res.json();
  if (!res.ok) { $("modelsError").textContent = data.detail?.[0]?.msg || data.detail || "保存失败"; return; }
  $("modelsDlg").close();
  toast("模型连接已保存");
};

// ---------- 公司的模型（工作台里）

$("companyModelsBtn").onclick = () => {
  $("coError").textContent = "";
  $("coEffort").innerHTML = effortOptions();
  $("coEffort").value = C.effort || "standard";
  $("coEffort").dataset.initial = $("coEffort").value;
  $("coEffortDetail").textContent = effortDetail($("coEffort").value);
  $("coKb").checked = C.kb !== false;
  setSelect($("coModel"), modelOptions(globalLabel()), encodeRef(C.model_ref));
  const box = $("coRoles");
  box.innerHTML = "";
  for (const r of C.roles) {
    const p = presets[r.id] || {};
    const row = el("div", "co-row");
    row.dataset.id = r.id;
    row.innerHTML = `<span>${p.icon || ""}</span><span class="co-name"></span><select></select>`;
    row.querySelector(".co-name").textContent = p.name || r.id;
    const legacy = r.legacy_model ? `<option value="__legacy">旧版单独配置（${esc(r.model)}）</option>` : "";
    setSelect(row.querySelector("select"), modelOptions("跟随公司默认", legacy), r.legacy_model ? "__legacy" : encodeRef(r.model_ref));
    row.querySelector("select").dataset.initial = row.querySelector("select").value;
    box.appendChild(row);
  }
  $("companyModelsDlg").showModal();
};

$("coFollowAll").onclick = () => document.querySelectorAll("#coRoles select").forEach((s) => { s.value = ""; });

$("coEffort").onchange = () => { $("coEffortDetail").textContent = effortDetail($("coEffort").value); };

$("coSave").onclick = async () => {
  if ($("coKb").checked !== (C.kb !== false)) {
    const r = await fetch("/api/company/kb", { method: "PUT", headers: { "Content-Type": "application/json" },
                                               body: JSON.stringify({ enabled: $("coKb").checked }) });
    if (!r.ok) { $("coError").textContent = (await r.json()).detail || "保存失败"; return; }
  }
  if ($("coEffort").value !== $("coEffort").dataset.initial) {
    const r = await fetch("/api/company/effort", { method: "PUT", headers: { "Content-Type": "application/json" },
                                                   body: JSON.stringify({ effort: $("coEffort").value }) });
    if (!r.ok) { $("coError").textContent = (await r.json()).detail || "保存失败"; return; }
  }
  const roles = {};
  document.querySelectorAll("#coRoles .co-row").forEach((row) => {
    const s = row.querySelector("select");
    if (s.value !== s.dataset.initial && s.value !== "__legacy") roles[row.dataset.id] = decodeRef(s.value);
  });
  const res = await fetch("/api/company/models", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ default: decodeRef($("coModel").value), roles }),
  });
  const data = await res.json();
  if (!res.ok) { $("coError").textContent = data.detail?.[0]?.msg || data.detail || "保存失败"; return; }
  $("companyModelsDlg").close();
  toast("已保存，之后开始的工作按新的设置");
};

// ---------------------------------------------------------------- 视图切换

function showHome() {
  C = null;
  $("home").hidden = false;
  $("workspace").hidden = true;
  $("companyTitle").hidden = true;
  $("companyActions").hidden = true;
  $("navToggle").hidden = true;
  $("brand").hidden = false;
  document.title = "OPC · 一人公司";
  if (home) renderHome();
}

function showWorkspace() {
  $("home").hidden = true;
  $("workspace").hidden = false;
  $("companyTitle").hidden = false;
  $("companyActions").hidden = false;
  $("navToggle").hidden = false;
  $("brand").hidden = window.innerWidth < 780;
  $("ctName").textContent = C.name;
  $("ctMission").textContent = C.mission || "";
  document.title = `${C.name} · OPC`;
}

function updateBanner() {
  const b = $("banner");
  if (home?.demo) { b.textContent = "演示模式：使用假模型生成示例内容，不调用真实 API。"; b.hidden = false; }
  else if (home && !home.defaults.has_key) { b.textContent = "还没有可用的 API Key：在创建公司处点「⚙ 管理模型连接」添加一个连接，或者在项目根目录的 .env 里设置 OPC_API_KEY。"; b.hidden = false; }
  else b.hidden = true;
}

// ---------------------------------------------------------------- 频道列表

function renderChannels() {
  const list = $("channelList");
  list.innerHTML = "";
  const working = Object.keys(roleStatus);
  const add = (id, icon, name, sub, isWorking) => {
    const row = el("div", "channel" + (id === channel ? " active" : ""));
    row.innerHTML = `<span class="ch-icon">${icon}</span><div class="ch-main"><div class="ch-name">${esc(name)}</div>
      <div class="ch-sub ${isWorking ? "working" : ""}">${esc(sub)}</div></div>
      <span class="dot ${isWorking ? "working" : unread.has(id) ? "unread" : ""}"></span>`;
    row.onclick = () => { switchChannel(id); $("nav").classList.remove("show"); };
    list.appendChild(row);
  };
  const orgWorking = working.some((r) => r === "planner" || r === "reporter");
  add(ALL, "🏢", "全公司", isEscalation() ? "⚠️ 有问题等你裁决" : review ? "⏳ 有计划等你审批" : orgWorking ? `${who(working.find((r) => SPECIAL[r]))?.name}工作中…` : "下令给整个团队", orgWorking);
  add(MANAGER, "🧭", "项目经理", triaging.has(MANAGER) ? "正在理解…" : "问进度、提调整", triaging.has(MANAGER));
  list.appendChild(el("div", "nav-sep"));
  for (const r of C.roles) {
    const p = presets[r.id];
    const isWorking = !!roleStatus[r.id];
    const task = isWorking && roleStatus[r.id].task_id ? S.tasks[roleStatus[r.id].task_id] : null;
    add(r.id, p.icon, p.name, isWorking ? (task ? `正在做：${task.title}` : "正在回复…") : p.tagline, isWorking);
  }
}

function switchChannel(id) {
  channel = id;
  unread.delete(id);
  renderChannels();
  renderChat();
}

// ---------------------------------------------------------------- 对话

function renderChatHead() {
  const head = $("chatHead");
  if (channel === ALL) {
    head.innerHTML = `<span class="h-icon">🏢</span><div><div class="h-name">全公司</div><div class="h-desc">你的指令会由规划助手拆成任务，经你批准后分派给各位同事</div></div>
      <div class="view-toggle" title="简洁：每个任务一张进展卡片，过程收起来；详细：显示所有过程消息">
        <button data-v="simple" class="${allView === "simple" ? "on" : ""}">简洁</button><button data-v="detail" class="${allView === "detail" ? "on" : ""}">详细</button></div>`;
    head.querySelectorAll(".view-toggle button").forEach((b) => b.onclick = () => {
      allView = b.dataset.v;
      try { localStorage.setItem("opc.allView", allView); } catch {}
      renderChat();
    });
  } else if (channel === MANAGER) {
    head.innerHTML = `<span class="h-icon">🧭</span><div><div class="h-name">项目经理</div><div class="h-desc">了解整个任务看板：问进度、提调整。要改动已有的工作时，它会整理成调整建议，你确认后执行</div></div>`;
  } else {
    const p = presets[channel];
    const cfg = C.roles.find((r) => r.id === channel);
    head.innerHTML = `<span class="h-icon">${p.icon}</span><div><div class="h-name">${esc(p.name)}</div>
      <div class="h-desc">${esc(p.tagline)} · 模型 ${esc(cfg?.model || "")}${p.can_code && coderAvailable
        ? (C.allow_run ? '<span class="badge">🤖 编码 agent · 可运行代码</span>' : '<span class="badge">🤖 编码 agent · 不运行代码</span>') : ""}${C.web ? '<span class="badge">🌐 可联网</span>' : ""}${C.kb !== false ? '<span class="badge">📚 知识库</span>' : ""}</div>
      <div class="h-now" id="hNow"></div></div>`;
    updateNow();
  }
}

// 角色频道顶部：它现在在做什么
function updateNow() {
  const box = $("hNow");
  if (!box) return;
  const st = roleStatus[channel];
  const t = st?.task_id ? S.tasks[st.task_id] : null;
  const working = Object.values(S.tasks || {}).filter((x) => x.assignee === channel && ["running", "reviewing"].includes(x.status));
  if (t) box.innerHTML = `<span class="dot working"></span>正在${t.reviewer === channel && t.assignee !== channel ? "审查" : "做"}：《${esc(t.title)}》`;
  else if (st?.chat) box.innerHTML = `<span class="dot working"></span>正在回复你`;
  else if (working.length) box.innerHTML = `<span class="dot working"></span>正在做：${working.map((x) => `《${esc(x.title)}》`).join("、")}`;
  else box.textContent = "";
}

// ---------- 角色频道里显示它在全公司任务里做的事

const isRoleChannel = (ch) => ch !== ALL && ch !== MANAGER;

// 全公司频道里和这个角色有关的工作：它的交付、审查、提问和回答、上报，以及别人对它的审查、同事对它提问的回答
function workFor(m, role) {
  if (m.sender === "ceo" || ["event", "plan", "report"].includes(m.kind)) return false;
  const t = m.task_id ? S.tasks[m.task_id] : null;
  if (m.sender === role) return true;
  if (m.kind === "proposal") return m.reporter === role;
  if (m.kind === "review") return !!t && t.assignee === role;
  if (m.consult) return !!t && t.assignee === role;
  return false;
}

function renderWork(m, role) {
  const box = el("div", "from-all");
  const t = m.task_id ? S.tasks[m.task_id] : null;
  const head = el("div", "fa-head", `🏢 来自全公司${t ? ` · 任务《${esc(t.title)}》` : ""}`);
  if (t && (t.assignee === role || t.reviewer === role)) {
    const b = el("button", "btn ghost small", "讨论这项工作");
    b.onclick = () => { topics[role] = t.id; updateTopics(); $("input").focus(); };
    head.appendChild(b);
  }
  box.append(head, m.kind === "proposal" ? renderProposal(m, ALL) : renderMessage(m));
  return box;
}

// 正在流式生成的消息在当前频道里显示吗：本频道的，或者在角色频道里看它在全公司频道里正在做的事
const streamHere = (s) => (s.channel === channel && !(simpleAll() && presets[s.sender]))
  || (isRoleChannel(channel) && s.channel === ALL && s.sender === channel);

function renderChat() {
  renderChatHead();
  const box = $("messages");
  box.innerHTML = "";
  const own = (S.channels[channel] || []).map((m, i) => ({ m, i, work: false }));
  const work = isRoleChannel(channel) ? (S.channels[ALL] || []).filter((m) => workFor(m, channel)).map((m, i) => ({ m, i, work: true })) : [];
  const items = [...own, ...work].sort((a, b) => (a.m.ts || "").localeCompare(b.m.ts || ""));
  if (!items.length && ![...streams.values()].some(streamHere)) box.appendChild(emptyState());
  const carded = new Set();
  for (const x of items) {
    if (!x.work && simpleAll() && isTaskDetail(x.m)) {  // 过程消息：这个任务第一次出现的位置放一张进展卡片
      if (!carded.has(x.m.task_id)) {
        carded.add(x.m.task_id);
        box.appendChild(taskProgressCard(S.tasks[x.m.task_id]));
      }
      continue;
    }
    box.appendChild(x.work ? renderWork(x.m, channel) : renderMessage(x.m));
  }
  for (const s of streams.values()) {
    if (!streamHere(s)) continue;
    s.el = buildStreamEl(s);
    box.appendChild(s.el);
  }
  if (channel === ALL && isEscalation()) box.appendChild(escalationCard());
  updateTyping();
  updateComposer();
  box.scrollTop = box.scrollHeight;
}

function emptyState() {
  const box = el("div", "chat-empty");
  let examples;
  if (channel === MANAGER) {
    box.innerHTML = `<div class="big">🧭</div><h3>和项目经理沟通</h3><p>它了解每个任务的进展和依赖关系。发现哪里不对，告诉它就行：它会整理成调整建议（谁返工、哪些任务要先暂停），你确认后执行。</p>`;
    examples = ["现在进度怎么样？还有哪些没完成？", "需求文档里的候补规则不对，要按先到先得来"];
  } else if (channel === ALL) {
    box.innerHTML = `<div class="big">🏢</div><h3>给全公司下第一道指令</h3><p>规划助手会把它拆成任务、安排给合适的同事，你批准后大家开始干活。</p>`;
    examples = ["根据公司目标，做出第一版产品方案：需求、技术方案和界面设计",
                "调研三个主要竞品，写一份对比分析和我们的差异化建议",
                "为产品上线准备推广方案和第一批宣传文案"];
  } else {
    const p = presets[channel];
    box.innerHTML = `<div class="big">${p.icon}</div><h3>和${esc(p.name)}单独沟通</h3><p>${esc(p.tagline)}。这里的对话只有${esc(p.name)}能看到，它会记住你们聊过的内容。</p>`;
    examples = ["介绍一下你的职责，以及你需要我提供哪些信息", `按照你的职责，先产出一份 ${p.default_file}`];
  }
  const ex = el("div", "examples");
  for (const t of examples) {
    const b = el("button", "example");
    b.textContent = t;
    b.onclick = () => { $("input").value = t; autosize(); $("input").focus(); };
    ex.appendChild(b);
  }
  box.appendChild(ex);
  return box;
}

function appendToChat(node) {
  const box = $("messages");
  box.querySelector(".chat-empty")?.remove();
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 240;
  const typing = box.querySelector(".typing");
  typing ? box.insertBefore(node, typing) : box.appendChild(node);
  if (nearBottom) box.scrollTop = box.scrollHeight;
}

function chipsFor(tools, filesWritten) {
  const chips = el("div", "chips");
  for (const t of tools || []) {
    if ((t.name === "write_file" || t.name === "edit_file") && t.ok !== false) continue; // 写过的文件在下面显示为可点击的文件
    chips.appendChild(toolChip(t));
  }
  for (const f of filesWritten || []) {
    const c = el("span", "chip file", `✍️ ${esc(f)}`);
    c.onclick = () => openFileInPanel(f);
    chips.appendChild(c);
  }
  return chips.children.length ? chips : null;
}

// 一个工具调用的标签。via=coder 表示是编码 agent 执行的步骤
function toolChip(t) {
  const via = t.via === "coder" ? "🤖 " : "";
  if (t.name === "code_agent") return el("span", "chip agent", "🤖 交给编码 agent");
  if (t.name === "kb_search") {
    const chip = el("span", "chip kb" + (t.ok === false ? " bad" : ""),
                    `${via}📚 检索：${esc(t.query || "")}${t.ok === false ? "" : `（${t.hits ?? 0} 条）`}`);
    chip.title = (t.sources || []).join("\n") || "没有找到相关内容";
    return chip;
  }
  if (t.name === "run_command") {
    const ok = t.exit === 0;
    const chip = el("span", "chip cmd " + (ok ? "ok" : "bad"), `${via}▶ ${esc(t.command)} ${ok ? "✓" : `✗ ${t.exit ?? ""}`}`);
    chip.title = `退出码：${t.exit}`;
    return chip;
  }
  const bad = t.ok === false ? " bad" : "";
  const web = t.web || /^(search|research|fetch)__/.test(t.name);  // 以前的记录里没有 web 标记
  if (web && t.question) return el("span", "chip" + bad, `🔎 调研：${esc(t.question)}`);
  if (web && t.query) return el("span", "chip" + bad, `🌐 搜索：${esc(t.query)}`);
  if (web && !t.url) return el("span", "chip" + bad, `🌐 ${esc(t.name)}`);
  if (web) {
    const chip = el("span", "chip" + (t.ok === false ? " bad" : ""), `🌐 读网页：${esc((t.url || "").replace(/^https?:\/\//, "").slice(0, 60))}`);
    chip.title = t.url || "";
    return chip;
  }
  const labels = { list_files: "📂 查看文件列表", list_dir: `📂 ${t.path || "."}`, read_file: `📖 ${t.path}`,
                   grep: `🔍 ${t.path}`, write_file: `✍️ ${t.path}`, edit_file: `✏️ ${t.path}`,
                   heard: "📨 收到了你的插话",
                   submit_review: t.ok === false ? "🔍 提交审查结论（核对没通过，退回重交）" : "🔍 提交审查结论",
                   finish_task: t.ok === false ? "📦 提交交付（检查没通过，退回修改）" : "📦 提交交付" };
  if (t.name === "heard") return el("span", "chip heard", labels.heard);
  return el("span", "chip" + (t.ok === false ? " bad" : ""), esc(via + (labels[t.name] || t.name)));
}

function agentBlock(sender, ts) {
  const w = who(sender);
  const node = el("div", "msg");
  node.innerHTML = `<div class="avatar">${w.icon}</div><div class="msg-body">
    <div class="who"><b>${esc(w.name)}</b>${ts ? esc(ts.slice(11, 16)) : ""}</div>
    <details class="reasoning" hidden><summary>思考过程</summary><div class="reasoning-body"></div></details>
    <div class="md"></div></div>`;
  return node;
}

function renderMessage(m) {
  if (m.sender === "ceo") {
    const node = el("div", "msg-ceo" + (m.kind === "interject" ? " interject" : ""));
    const tag = (m.kind === "interject" ? `<span class="live-tag" title="执行中发出，正在工作的同事会在下一步收到">实时插话</span>` : "")
      + (m.topic && S.tasks[m.topic] ? `<span class="topic-tag">关于《${esc(S.tasks[m.topic].title)}》</span>` : "");
    node.innerHTML = `<div class="who">${tag}你（CEO）${m.ts ? " · " + esc(m.ts.slice(11, 16)) : ""}</div><div class="bubble"></div>`;
    node.querySelector(".bubble").textContent = m.text;
    return node;
  }
  if (m.kind === "event") return el("div", "event", esc(m.text));
  if (m.kind === "security") return renderSecurity(m);
  if (m.kind === "proposal") return renderProposal(m);
  if (m.kind === "plan") return renderPlan(m);
  if (m.kind === "review") return renderReview(m);
  const node = agentBlock(m.sender, m.ts);
  const body = node.querySelector(".msg-body");
  if (m.reasoning) {
    const r = node.querySelector(".reasoning");
    r.hidden = false;
    r.querySelector(".reasoning-body").textContent = m.reasoning;
  }
  const md = node.querySelector(".md");
  md.innerHTML = markdown(m.text);
  if (m.kind === "report") {
    md.classList.add("card", "report-card");
    md.insertAdjacentHTML("afterbegin", `<p><strong>${m.quick ? "📊 结果" : "📊 执行汇报"}</strong></p>`);
    md.appendChild(reportExtras(m));
    if (m.directive_id) {
      const btn = el("button", "btn small ghost trace-btn", "🔍 执行链路（每一步的用时和 token）");
      btn.onclick = () => openTrace(`key=${encodeURIComponent("directive:" + m.directive_id)}`);
      md.appendChild(btn);
    }
  }
  else if (m.kind === "error") md.classList.add("card", "error-card");
  else md.classList.add("card");
  if (m.topic && S.tasks[m.topic]) body.querySelector(".who").insertAdjacentHTML("beforeend",
    ` · <span class="topic-tag">关于《${esc(S.tasks[m.topic].title)}》</span>`);
  if (m.consult) body.querySelector(".who").insertAdjacentHTML("beforeend", ` · <span class="topic-tag">同事间提问</span>`);
  if (m.task_id && S.tasks[m.task_id]) body.querySelector(".who").insertAdjacentHTML("beforeend",
    ` · 任务：${esc(S.tasks[m.task_id].title)}${m.rework ? ` · <span class="rework-tag">按第 ${m.rework} 轮审查意见返工</span>` : ""}`);
  const chips = chipsFor(m.tools, m.files);
  if (chips) body.appendChild(chips);
  return node;
}

// ---------- 全公司频道的 CEO 视角：任务进展卡片、成果、阅读视图

// 属于某个任务的过程消息（交付、审查、返工、同事问答、任务事件）：简洁模式下收进任务卡片
// 防提示词注入的提醒：读到的外部内容里有像指令的文字。可以信任这份内容并让任务重做（这次不拦运行命令和访问网址）
function renderSecurity(m) {
  const node = el("div", "security-card");
  node.innerHTML = `<pre class="security-text"></pre>`;
  node.querySelector("pre").textContent = m.text;
  const t = m.task_id && S.tasks[m.task_id];
  if (t && !m.trusted && !t.trusted) {
    const btn = el("button", "btn small", "信任并重做");
    btn.title = "你确认这份外部内容是可信的（比如是你自己写的资料）：这个任务重新执行，这次可以正常运行代码、访问网址";
    btn.onclick = () => {
      if (!confirm(`确认这份内容可信，让《${t.title}》重做？这次不会再拦运行命令和访问网址。`)) return;
      send({ type: "task_action", action: "trust", task_id: t.id });
    };
    node.appendChild(btn);
  }
  return node;
}

function isTaskDetail(m) {
  const t = m.task_id ? S.tasks[m.task_id] : null;
  return !!t && m.sender !== "ceo" && m.kind !== "proposal" && (["text", "review", "error", "event"].includes(m.kind) || m.consult);
}

function taskState(t) {
  const reworking = t.status === "running" && (t.reviews || []).length > 0;
  const resync = t.status === "pending" && Object.keys(t.resync || {}).length > 0;
  const shown = t.paused && !["done", "failed", "skipped", "cancelled"].includes(t.status) ? "paused" : resync ? "resync" : t.status;
  const label = reworking ? `返工中（第 ${t.reviews.length} 轮意见）`
    : shown === "resync" ? "待同步（上游改了）" : STATUS[shown] || t.status;
  return { reworking, shown, label };
}

// 一行流程摘要：交付 → 第 1 轮审查要求修改 4 处 → 第 2 轮审查通过
function taskFlow(t) {
  const steps = [];
  if ((t.reviews || []).length || ["review", "reviewing", "done"].includes(t.status)) steps.push("交付");
  for (const r of t.reviews || []) {
    if (r.verdict === "pass") steps.push(`第 ${r.round} 轮审查通过`);
    else if (r.verdict === "changes") steps.push(`第 ${r.round} 轮审查要求修改 ${(r.issues || []).length} 处${r.extra ? "（在收敛，多给一轮）" : ""}`);
    else if (r.verdict === "checks") steps.push("交付检查没通过");
    else if (r.verdict === "incomplete") steps.push("步数用完还没做完");
    else steps.push("审查没有给出结论");
    if (r.waived) steps.push("你接受了");
  }
  if (t.status === "reviewing") steps.push("审查中");
  if (t.status === "escalated") steps.push("等你裁决");
  const msgs = (S.channels[ALL] || []).filter((m) => m.task_id === t.id && m.ts);
  const took = msgs.length > 1 ? (Date.parse(msgs[msgs.length - 1].ts) - Date.parse(msgs[0].ts)) / 1000 : 0;
  const used = t.usage?.calls ? ` · ${t.usage.calls} 次调用 · ${fmtTok((t.usage.prompt || 0) + (t.usage.completion || 0))} tokens` : "";
  return steps.join(" → ") + (took > 60 && ["done", "failed"].includes(t.status) ? ` · 用时 ${Math.round(took / 60)} 分钟` : "") + used;
}

function fileTitle(path) {
  const f = files.find((x) => x.path === path);
  return (f && f.title) || baseName(path);
}

function plainHeadline(t) {
  if (t.headline) return t.headline;
  // 旧任务没有一句话结论：从汇报里取第一句正文（跳过标题、"做了什么"这类小标题和很短的行）
  const line = String(t.result || "").split("\n")
    .filter((s) => !/^\s*#/.test(s))
    .map((s) => s.replace(/[*>`]/g, "").replace(/^\s*[-\d.、]+\s*/, "").trim())
    .find((s) => s.length >= 15 && !/^(做了什么|没做完|逐条处理|关键决定|核心内容)/.test(s)) || "";
  const first = line.split("。")[0] + (line.includes("。") ? "。" : "");
  return first.length > 120 ? first.slice(0, 120) + "…" : first;
}

function taskProgressCard(t) {
  const p = presets[t.assignee] || {};
  const st = taskState(t);
  const node = agentBlock(t.assignee, null);
  node.dataset.taskcard = t.id;
  node.querySelector(".who").innerHTML = `<b>${esc(p.name || t.assignee)}</b>任务进展`;
  const md = node.querySelector(".md");
  md.classList.add("card", "tp-card");
  const active = ["running", "review", "reviewing", "rework"].includes(t.status) && busy;
  const live = active && streamOfTask(t);
  const headline = ["done", "review", "reviewing", "escalated", "failed"].includes(t.status) || (t.reviews || []).length ? plainHeadline(t) : "";
  const decisions = (t.decisions || []).length && t.status === "done"
    ? `<div class="tp-decisions">待你确认：${t.decisions.map(esc).join("；")}</div>` : "";
  md.innerHTML = `<div class="tp-head"><span class="tp-title">《${esc(t.title)}》</span>
      <span class="status s-${st.reworking ? "rework" : st.shown}">${st.label}</span></div>
    ${live ? `<div class="tp-live"><span class="dot working"></span><span data-act="${live.id}">${actText(live.act)}</span></div>`
      : active ? `<div class="tp-live"><span class="dot working"></span>${t.status === "reviewing" ? `${esc(who(t.reviewer).name)}正在审查…` : "正在做…"}</div>` : ""}
    ${headline ? `<div class="tp-headline">${esc(headline)}</div>` : ""}
    ${taskFlow(t) ? `<div class="tp-flow">${esc(taskFlow(t))}</div>` : ""}
    ${decisions}
    <div class="tp-actions"></div>
    <div class="tp-details"${openDetails.has(t.id) ? "" : " hidden"}></div>`;
  const actions = md.querySelector(".tp-actions");
  if (t.status === "done") {
    for (const f of (t.files || []).slice(0, 3)) {
      const b = el("button", "btn small", `📄 打开成果：${esc(fileTitle(f))}`);
      b.onclick = () => openReader(f);
      actions.appendChild(b);
    }
  }
  const more = el("button", "btn ghost small", openDetails.has(t.id) ? "收起过程" : "过程详情");
  more.onclick = () => {
    openDetails.has(t.id) ? openDetails.delete(t.id) : openDetails.add(t.id);
    node.replaceWith(taskProgressCard(S.tasks[t.id]));
  };
  actions.appendChild(more);
  if (openDetails.has(t.id)) {
    const box = md.querySelector(".tp-details");
    for (const m of (S.channels[ALL] || []).filter((x) => x.task_id === t.id && isTaskDetail(x))) box.appendChild(renderMessage(m));
  }
  return node;
}

function refreshTaskCards(ids) {
  if (!simpleAll()) return;
  for (const id of ids) {
    const old = document.querySelector(`[data-taskcard="${id}"]`);
    if (old && S.tasks[id]) old.replaceWith(taskProgressCard(S.tasks[id]));
  }
}

// 汇报下面：本次成果（文件卡片）和汇总的"需要你决定"
function reportExtras(m) {
  const box = el("div", "report-extras");
  const tasks = Object.values(S.tasks).filter((t) => t.directive_id === m.directive_id);
  const decisions = tasks.flatMap((t) => (t.decisions || []).map((d) => ({ d, t })));
  if (decisions.length) {
    box.appendChild(el("div", "tp-decisions", `<b>需要你决定（${decisions.length} 项）</b><ul>${decisions.map(({ d, t }) =>
      `<li>${esc(d)}<span class="dec-from">（${esc(who(t.assignee).name)}）</span></li>`).join("")}</ul>`));
  }
  const paths = [...new Set(tasks.filter((t) => t.status === "done").flatMap((t) => t.files || []))];
  if (paths.length) {
    box.appendChild(el("div", "results-label", "本次成果"));
    const grid = el("div", "results");
    for (const path of paths) {
      const f = files.find((x) => x.path === path) || { path };
      const card = el("button", "result-card");
      card.innerHTML = `<div class="rc-title">📄 ${esc(f.title || baseName(path))}</div>
        <div class="rc-meta">${esc(path)}${f.size ? ` · ${Math.max(1, Math.round(f.size / 1024))} KB` : ""}${f.author ? ` · ${esc(who(f.author).name)}` : ""}</div>`;
      card.onclick = () => openReader(path);
      grid.appendChild(card);
    }
    box.appendChild(grid);
  }
  const line = el("div", "usage-line");  // 用量之后还会更新（汇报本身的调用在汇报之后才记上）
  line.dataset.did = m.directive_id || "";
  fillUsageLine(line);
  box.appendChild(line);
  return box;
}

async function openReader(path) {
  const res = await fetch("/api/file?path=" + encodeURIComponent(path));
  if (!res.ok) return toast("文件不存在");
  const data = await res.json();
  $("readerTitle").textContent = fileTitle(path);
  $("readerPath").textContent = path;
  const body = $("readerBody");
  if (/\.(md|markdown)$/i.test(path)) body.innerHTML = `<div class="md">${markdown(data.content)}</div>`;
  else { body.innerHTML = ""; const pre = el("pre", "code"); pre.textContent = data.content; body.appendChild(pre); }
  body.scrollTop = 0;
  $("reader").hidden = false;
  $("reader").dataset.path = path;
}

function closeReader() { $("reader").hidden = true; }

// 调度助手整理的调整建议：谁返工 / 暂停 / 继续、哪些任务会因此暂停。确认后和看板上的操作走同一套变更单
function renderProposal(m, ch = channel) {
  const node = agentBlock("manager", m.ts);
  node.dataset.proposal = m.id;
  const md = node.querySelector(".md");
  md.classList.add("card", "proposal-card");
  const done = (S.channels[ch] || []).find((x) => x.proposal_id === m.id);
  const orders = (m.orders || []).map((o) => {
    const verb = o.kind === "rework" ? "返工" : o.kind === "pause" ? "暂停" : "继续";
    return `<li><b>${verb}</b> ${who(o.assignee).icon} ${esc(who(o.assignee).name)}《${esc(o.title)}》${o.feedback ? `：${esc(o.feedback)}` : ""}</li>`;
  }).join("");
  const title = m.reporter ? `🧭 ${who(m.reporter).icon} ${esc(who(m.reporter).name)}在做《${esc(m.reporter_task || "")}》时发现上游有问题，建议这样调整`
    : "🧭 我理解为要调整这些工作";
  md.innerHTML = `<div class="pp-title">${title}</div><ul class="pp-orders">${orders}</ul>
    ${(m.affected || []).length ? `<div class="pp-affected">依赖它们的任务会先暂停，改完后带着改动对比继续：${m.affected.map(esc).join("、")}</div>` : ""}
    <div class="pp-actions"></div>`;
  const actions = md.querySelector(".pp-actions");
  if (done) actions.innerHTML = `<span class="pp-done">${esc(done.text)}</span>`;
  else {
    actions.innerHTML = `<button class="btn ok small" data-a="confirm">确认，马上执行</button><button class="btn ghost small" data-a="cancel">不用了</button>`;
    actions.onclick = (e) => {
      const a = e.target.dataset.a;
      if (!a) return;
      send({ type: "proposal", channel: ch, id: m.id, action: a });
      actions.innerHTML = `<span class="pp-done">已提交，处理中…</span>`;
    };
  }
  return node;
}

function renderReview(m) {
  // 审查结论：谁审查谁的哪个任务、第几轮、结论、问题清单，下面是审查人的说明
  const node = agentBlock(m.sender, m.ts);
  const body = node.querySelector(".msg-body");
  if (m.reasoning) {
    const r = node.querySelector(".reasoning");
    r.hidden = false;
    r.querySelector(".reasoning-body").textContent = m.reasoning;
  }
  const t = S.tasks[m.task_id];
  const owner = t ? who(t.assignee) : null;
  const md = node.querySelector(".md");
  md.classList.add("card", "review-card", "v-" + m.verdict);
  md.innerHTML = `<div class="rv-head">🔍 审查${owner ? ` ${owner.icon} ${esc(owner.name)}` : ""}《${esc(t?.title || "")}》· 第 ${m.round} 轮${
      (m.previous || []).length ? "（复审）" : ""}
      <span class="verdict v-${esc(m.verdict)}">${VERDICT[m.verdict] || esc(m.verdict)}</span></div>
    ${m.summary ? `<div class="rv-summary">${esc(m.summary)}</div>` : ""}
    ${reviewExtrasHtml(m)}
    ${(m.issues || []).length ? `<ol class="rv-issues">${m.issues.map((i) => `<li>${esc(i.text)}</li>`).join("")}</ol>` : ""}
    <div class="rv-text">${markdown(m.text)}</div>`;
  const chips = chipsFor(m.tools, m.files);
  if (chips) body.appendChild(chips);
  return node;
}

function escalationCard() {
  // 审查多轮仍不通过：把双方的往来摘要给 CEO，由 CEO 裁决
  const e = review;
  const owner = who(e.assignee), rev = who(e.reviewer);
  const lastVerdict = e.reviews.length ? e.reviews[e.reviews.length - 1].verdict : "";
  const unfinished = lastVerdict === "incomplete";  // 步数用完还没做完
  const checksFailed = lastVerdict === "checks";    // 交付检查几次都没通过
  const title = unfinished ? `${owner.icon} ${esc(owner.name)}的《${esc(e.title)}》步数用完了两次，还没有做完`
    : checksFailed ? `${owner.icon} ${esc(owner.name)}的《${esc(e.title)}》交付检查提交了几次仍没有通过`
    : `${owner.icon} ${esc(owner.name)}的《${esc(e.title)}》审查了 ${e.reviews.filter((r) => !SYSTEM_VERDICTS.includes(r.verdict)).length} 轮仍未通过`;
  const node = el("div", "msg");
  node.dataset.escalation = e.task_id;
  node.innerHTML = `<div class="avatar">⚠️</div><div class="msg-body"><div class="who"><b>需要你裁决</b></div>
    <div class="md card escalation-card">
      <div class="rv-head">${title}</div>
      ${e.reviews.map((r) => `<div class="esc-round"><b>${SYSTEM_VERDICTS.includes(r.verdict) ? "⚙️ 系统"
        : `${rev.icon} ${esc(rev.name)} 第 ${r.round} 轮`}：${VERDICT[r.verdict] || esc(r.verdict)}</b>
        ${r.summary ? `<div>${esc(r.summary)}</div>` : ""}
        ${(r.issues || []).length ? `<ol class="rv-issues">${r.issues.map((i) => `<li>${esc(i.text)}</li>`).join("")}</ol>` : ""}</div>`).join("")}
      <details class="esc-reply"><summary>${esc(owner.name)}最后的回复</summary><div>${markdown(e.result)}</div></details>
      <div class="review-box"></div></div></div>`;
  const box = node.querySelector(".review-box");
  const buttons = () => {
    box.innerHTML = `<div class="rb-title">怎么处理？</div><div class="review-actions">
      <button class="btn ok" data-a="accept">${(unfinished || checksFailed) && e.reviewer ? "接受现在的版本，交去审查" : "接受现在的版本"}</button>
      <button class="btn" data-a="rework">${unfinished ? "接着做…" : "附上我的意见，再改一轮…"}</button>
      <button class="btn ghost" data-a="drop">放弃这个任务</button></div>`;
  };
  buttons();
  box.onclick = (ev) => {
    const a = ev.target.dataset.a;
    if (!a) return;
    if (a === "rework") {
      box.innerHTML = unfinished
        ? `<div class="rb-title">${esc(owner.name)}会带着之前的对话接着做（可以补充意见，不写也行）</div>
        <textarea rows="2" placeholder="例如：先把最重要的接口部分写完，细节放到附录"></textarea>
        <div class="review-actions"><button class="btn primary" data-a="rework!">接着做</button><button class="btn ghost" data-a="back">返回</button></div>`
        : `<div class="rb-title">告诉${esc(owner.name)}怎么改（审查人也会看到）</div>
        <textarea rows="2" placeholder="例如：导出先只做 CSV；数据库设计可以简化成一张表"></textarea>
        <div class="review-actions"><button class="btn primary" data-a="rework!">再改一轮</button><button class="btn ghost" data-a="back">返回</button></div>`;
      box.querySelector("textarea").focus();
      return;
    }
    if (a === "back") return buttons();
    const feedback = a === "rework!" ? box.querySelector("textarea").value.trim() : "";
    if (a === "rework!" && !feedback && !unfinished) return toast("请写下你的意见");
    send({ type: "review", action: a === "rework!" ? "rework" : a, feedback });
    box.innerHTML = `<div class="review-done">已提交，处理中…</div>`;
  };
  return node;
}

function renderPlan(m) {
  const node = agentBlock("planner", m.ts);
  node.dataset.plan = m.id;  // 用消息 id：同一条指令修改后会有多份计划
  const md = node.querySelector(".md");
  md.classList.add("card", "plan-card");
  const tasks = (m.tasks || []).map((id) => S.tasks[id]).filter(Boolean);
  const recalled = (m.recalled || []).length
    ? `<details class="pt-req plan-recall"><summary>📚 规划时参考了知识库里 ${m.recalled.length} 条相关记录</summary><ul>${m.recalled.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : "";
  md.innerHTML = `<div class="plan-title">${m.quick ? "⚡ 小任务 · 直接开始" : m.edited ? "执行计划（CEO 修改过）" : "执行计划"}</div>${markdown(m.text)}${recalled}<div class="plan-tasks"></div>`;
  const list = md.querySelector(".plan-tasks");
  for (const t of tasks) {
    const p = presets[t.assignee] || {};
    const deps = t.depends_on.map((d) => S.tasks[d]?.title).filter(Boolean);
    const row = el("div", "plan-task");
    row.innerHTML = `<span class="pt-icon">${p.icon || ""}</span><div class="pt-main">
      <div class="pt-title">${esc(p.name || t.assignee)}：${esc(t.title)} <span class="status s-${t.status}">${STATUS[t.status] || t.status}</span></div>
      <div class="pt-detail">${esc(t.detail)}</div>${deps.length ? `<div class="pt-deps">等待：${deps.map(esc).join("、")}</div>` : ""}
      ${t.reviewer ? `<div class="pt-deps">审查：${who(t.reviewer).icon} ${esc(who(t.reviewer).name)}</div>` : ""}
      ${requirementsHtml(t) ? `<details class="pt-req"><summary>交付要求与验收标准</summary>${requirementsHtml(t)}</details>` : ""}</div>`;
    list.appendChild(row);
  }
  // 审批按钮只放在最新的那份计划上（CEO 编辑过计划时，新卡片和旧卡片有相同的任务）
  if (isPlanReview() && review.directive_id === m.directive_id
      && (review.plan_msg ? review.plan_msg === m.id : tasks.some((t) => review.task_ids.includes(t.id)))) {
    md.appendChild(reviewBox());
  }
  return node;
}

function reviewBox() {
  const box = el("div", "review-box");
  const showButtons = () => {
    box.innerHTML = `<div class="rb-title">⏳ 等你审批：批准后各位同事开始执行</div><div class="review-actions">
      <button class="btn ok" data-a="approve">批准执行</button>
      <button class="btn" data-a="edit">✏️ 编辑计划</button>
      <button class="btn" data-a="revise">提修改意见…</button>
      <button class="btn ghost" data-a="cancel">取消这条指令</button></div>`;
  };
  showButtons();
  box.onclick = (e) => {
    const a = e.target.dataset.a;
    if (!a) return;
    if (a === "revise") {
      box.innerHTML = `<div class="rb-title">告诉规划助手怎么改</div><textarea rows="2" placeholder="例如：先不要市场，工程师的方案要包含数据库设计"></textarea>
        <div class="review-actions"><button class="btn primary" data-a="revise!">重新规划</button><button class="btn ghost" data-a="back">返回</button></div>`;
      box.querySelector("textarea").focus();
      return;
    }
    if (a === "back") return showButtons();
    if (a === "edit") return openPlanEditor();
    const feedback = a === "revise!" ? box.querySelector("textarea").value.trim() : "";
    if (a === "revise!" && !feedback) return toast("请写下修改意见");
    send({ type: "review", action: a === "revise!" ? "revise" : a, feedback });
    box.innerHTML = `<div class="review-done">已提交，处理中…</div>`;
  };
  return box;
}

// ---------- 流式消息

function buildStreamEl(s) {
  const node = agentBlock(s.sender, null);
  node.classList.add("streaming");
  node.querySelector(".md").classList.add("card");
  fillStream(node, s);
  return node;
}

function fillStream(node, s) {
  const r = node.querySelector(".reasoning");
  if (s.reasoning) {
    r.hidden = false;
    r.open = !s.text;
    r.querySelector("summary").textContent = s.text ? "思考过程" : "思考中…";
    const body = r.querySelector(".reasoning-body");
    body.textContent = s.reasoning;
    body.scrollTop = body.scrollHeight;
  }
  node.querySelector(".md").innerHTML = markdown(s.text);
  node.querySelector(".chips")?.remove();
  node.querySelector(".progress-line")?.remove();
  const chips = chipsFor(s.tools, s.files);
  if (chips) node.querySelector(".msg-body").appendChild(chips);
  if (s.progress) {
    node.querySelector(".msg-body").appendChild(
      el("div", "progress-line", `<span class="dot working"></span>🤖 编码 agent 正在写… 已输出 ${s.progress.toLocaleString()} 字`));
  }
  node.querySelectorAll(".writing").forEach((x) => x.remove());
  for (const w of Object.values(s.writing || {})) node.querySelector(".msg-body").appendChild(writingBlock(s, w));
  node.querySelector(".act-line")?.remove();
  if (s.act) node.querySelector(".msg-body").appendChild(actLine(s));
}

// ---------- 进度摘要：这一步在做什么、做了多久、为什么还没结束

const ACT_TOOLS = { read_file: "读文件", list_files: "查看文件列表", write_file: "写文件", edit_file: "修改文件",
  ask_colleague: "问同事", report_upstream_issue: "上报上游问题", submit_review: "提交审查结论", delegate_to_coder: "交给编码 agent",
  run_command: "运行命令" };
const actTool = (name) => ACT_TOOLS[name] || name || "工具";
const baseName = (p) => String(p || "").split("/").pop();
const nowSec = () => Date.now() / 1000;

function actPhase(a) {
  if (a.phase === "waiting") return "等模型回复";
  if (a.phase === "thinking") return "正在思考";
  if (a.phase === "writing") return "正在写回复";
  if (a.phase === "calling") return a.tool === "write_file" || a.tool === "edit_file" ? "正在写文件内容" : `正在准备${actTool(a.tool)}`;
  if (a.phase === "tool") return `正在${actTool(a.tool)}${a.path ? " " + esc(baseName(a.path)) : ""}`;
  return "工作中";
}

function actText(a) {
  const took = nowSec() - a.phaseSince;
  const parts = [`第 ${a.step} 步`];
  if (a.reads.length) {
    const names = a.reads.slice(-3).map((p) => esc(baseName(p))).join("、");
    parts.push(`已读 ${names}${a.reads.length > 3 ? ` 等 ${a.reads.length} 个文件` : ""}`);
  }
  parts.push(`${actPhase(a)} ${fmtDuration(took)}`);
  if (a.stepChars) parts.push(`本步已输出 ${a.stepChars.toLocaleString()} 字`);
  let hint = "";
  if (a.phase === "waiting" && took > 90) hint = "模型迟迟没有开始回复：服务繁忙或网络慢，会自动重试";
  else if (a.phase === "thinking" && took > 240) hint = "思考时间较长：要对照的文档很长时属正常";
  else if ((a.phase === "writing" || a.phase === "calling") && took > 300) hint = "内容很长，还在持续输出";
  return parts.join(" · ") + (hint ? `<div class="act-hint">${hint}</div>` : "");
}

function actLine(s) {
  return el("div", "act-line", `<span class="dot working"></span><span data-act="${s.id}">${actText(s.act)}</span>`);
}

// 某个任务当前对应的流式消息（做任务的人，审查阶段是审查人）
function streamOfTask(t) {
  const who = t.status === "reviewing" ? t.reviewer : t.assignee;
  let found = null;
  for (const s of streams.values()) if (s.channel === ALL && s.sender === who && s.act) found = s;
  return found;
}

const LIVE_TAIL = 6000;  // 实时预览只显示最后这么多字，写完后在文件区看全文

function writingBlock(s, w) {
  const box = el("details", "writing");
  box.open = !s.writingCollapsed;
  const verb = w.tool === "edit_file" ? "✏️ 正在修改" : "✍️ 正在写";
  box.innerHTML = `<summary><span class="dot working"></span>${verb} ${esc(w.path || "文件")} · 已写 ${w.chars.toLocaleString()} 字</summary>`;
  const body = el("pre", "writing-body");
  body.textContent = (w.text.length > LIVE_TAIL ? "…（前面的内容写完后可在文件区查看）\n" : "") + w.text.slice(-LIVE_TAIL);
  box.appendChild(body);
  box.ontoggle = () => { s.writingCollapsed = !box.open; };
  requestAnimationFrame(() => { body.scrollTop = body.scrollHeight; });
  return box;
}

function streamFor(ev) {
  let s = streams.get(ev.msg_id);
  if (!s) {
    s = { id: ev.msg_id, channel: ev.channel, sender: ev.sender, text: "", reasoning: "", tools: [], files: [], el: null };
    streams.set(ev.msg_id, s);
    if (streamHere(s)) {
      s.el = buildStreamEl(s);
      appendToChat(s.el);
      updateTyping();
    }
  }
  return s;
}

let pendingRender = new Set();
function scheduleStream(s) {
  pendingRender.add(s);
  if (pendingRender.size > 1) return;
  requestAnimationFrame(() => {
    const box = $("messages");
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 240;
    for (const x of pendingRender) if (x.el && streamHere(x)) fillStream(x.el, x);
    pendingRender = new Set();
    if (nearBottom) box.scrollTop = box.scrollHeight;
  });
}

function updateTyping() {
  const box = $("messages");
  box.querySelector(".typing")?.remove();
  const streaming = new Set([...streams.values()].filter(streamHere).map((s) => s.sender));
  const relevant = Object.keys(roleStatus).filter((r) => (channel === ALL ? true : r === channel) && !streaming.has(r));
  if (triaging.has(channel)) box.appendChild(el("div", "typing", `<span class="dot working"></span>🧭 调度助手正在理解你的话…`));
  if (!relevant.length) return;
  const names = relevant.map((r) => `${who(r).icon} ${who(r).name}`).join("、");
  const verb = relevant.includes("planner") ? "正在规划…" : relevant.includes("reporter") ? "正在写汇报…" : "正在工作…";
  box.appendChild(el("div", "typing", `<span class="dot working"></span>${esc(names)} ${verb}`));
}

// ---------------------------------------------------------------- 输入框与状态

function updateComposer() {
  const input = $("input");
  const p = presets[channel];
  const roleBusy = !!roleStatus[channel];
  input.placeholder = channel === MANAGER ? "问项目经理：进度、要调整哪里…"
    : channel === ALL ? (busy ? "插话：正在工作的所有同事都会在下一步收到" : "对全公司下令，Enter 发送，Shift+Enter 换行")
    : roleBusy ? `插话：只有${p?.name}会收到` : `对${p?.name}说…`;
  updateTopics();
  $("stopBtn").hidden = !busy;
  $("retryBtn").hidden = !paused;
  $("resumeRunBtn").hidden = !(unfinished && !busy);
  const hint = $("composerHint");
  if (paused) { hint.textContent = `⏸ ${paused.reason}，团队已原地暂停（已完成的工作都保留）。每 30 秒自动重试一次，恢复后从中断处继续；也可以点「立即重试」或「停止」`; hint.hidden = false; }
  else if (unfinished && !busy) { hint.textContent = "上次的执行没有跑完（可能是程序被关掉了）。可以点「继续上次的执行」接着做，已经完成的部分不会重做；直接下新指令则不再继续"; hint.hidden = false; }
  else if (isEscalation()) { hint.textContent = "⚠️ 有一个审查分歧等你在「全公司」频道裁决"; hint.hidden = false; }
  else if (review) { hint.textContent = "⏳ 有一份计划等你在「全公司」频道审批，审批后才能发新指令"; hint.hidden = false; }
  else if (busy) { hint.textContent = "团队正在工作。你随时可以插话，不会打断执行；也可以点「停止」"; hint.hidden = false; }
  else hint.hidden = true;
  // 等审批 / 裁决时只是不能对全公司下新指令；私聊和项目经理频道照常
  input.disabled = !!review && channel === ALL;
  $("sendBtn").disabled = !!review && channel === ALL;
}

function setTiming(ev) {
  if (!("run_started" in ev)) return;
  runStarted = ev.run_started;
  phaseSince = ev.phase_since || {};
  clockSkew = (ev.server_now || Date.now() / 1000) - Date.now() / 1000;
}

function serverNow() { return Date.now() / 1000 + clockSkew; }

function fmtDuration(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

// 当前指令（CEO 最近一次在全公司频道下的令）的任务；刚下令、还在规划时是空的
function currentTasks() {
  const ceo = (S.channels[ALL] || []).filter((m) => m.sender === "ceo" && m.directive_id);
  const last = ceo.length ? ceo[ceo.length - 1].directive_id : null;
  return Object.values(S.tasks || {}).filter((t) => t.directive_id === last && !["cancelled", "skipped"].includes(t.status));
}

function updateProgress() {
  const box = $("runProgress");
  const group = busy ? currentTasks() : [];
  if (!busy) { box.hidden = true; return; }
  const done = group.filter((t) => t.status === "done").length;
  const pct = group.length ? Math.round(done / group.length * 100) : 0;
  box.hidden = false;
  box.innerHTML = `${group.length ? `<span class="rp-bar"><span style="width:${pct}%"></span></span>任务 ${done}/${group.length}` : "规划中"}`
    + (runStarted ? ` · 已用 ${fmtDuration(serverNow() - runStarted)}` : "");
}

// 每秒刷新：顶部已用时间、任务卡片上"这一步用了多久"
setInterval(() => {
  if (!busy) return;
  updateProgress();
  document.querySelectorAll("[data-since]").forEach((x) => { x.textContent = fmtDuration(serverNow() - Number(x.dataset.since)); });
  document.querySelectorAll("[data-act]").forEach((x) => {
    const s = streams.get(x.dataset.act);
    if (s?.act) x.innerHTML = actText(s.act);
  });
}, 1000);

function updateStatusBar() {
  updateProgress();
  const chip = $("stateChip");
  chip.className = "state-chip" + (paused ? " review" : review ? " review" : busy ? " busy" : "");
  chip.textContent = paused ? "已暂停：等待网络" : isEscalation() ? "等你裁决" : review ? "等你审批" : busy ? "执行中" : "空闲";
  chip.title = paused ? `${paused.reason}\n${paused.error}` : "";
  const total = sumUsage(S.usage);
  const cost = usageCost(S.usage);
  $("usage").textContent = `${fmtTok(total.prompt + total.completion)} tokens${cost ? ` · ¥${cost.total.toFixed(2)}` : ""}`;
  $("usage").title = total.calls ? `${usageLine(S.usage)}\n点击查看明细` : "全公司累计 token 用量，点击查看明细";
}

// ---------------------------------------------------------------- 用量
// 输入（prompt）= 每次调用发给模型的全部内容：系统提示词、任务说明、对话、读到的文件；其中和上一次调用开头相同的部分
// 命中缓存（cached），服务商按低很多的价格计。输出（completion）= 模型生成的内容（含思考过程）。

const fmtTok = (n) => (n >= 10000 ? `${(n / 10000).toFixed(n >= 1e6 ? 0 : 1)} 万` : `${n || 0}`);

function sumUsage(byRole) {
  const out = { prompt: 0, completion: 0, cached: 0, calls: 0 };
  for (const u of Object.values(byRole || {})) for (const k in out) out[k] += u[k] || 0;
  return out;
}

function priceOf(role) {
  if (!C) return null;
  if (role === "planner") return C.planner_price || null;
  return (C.roles || []).find((r) => r.id === role)?.price || null;
}

function costOf(u, price) {
  if (!price || !u) return null;
  const cached = u.cached || 0, fresh = Math.max(0, (u.prompt || 0) - cached);
  return (fresh * (price.input || 0) + cached * (price.cached || price.input || 0) + (u.completion || 0) * (price.output || 0)) / 1e6;
}

// 按每个角色当前用的模型价格估算；没填价格的角色不计，并标出来
function usageCost(byRole) {
  let total = 0, priced = false, missing = false;
  for (const [role, u] of Object.entries(byRole || {})) {
    const c = costOf(u, priceOf(role));
    if (c === null) missing = missing || (u.prompt || 0) + (u.completion || 0) > 0;
    else { total += c; priced = true; }
  }
  return priced ? { total, missing } : null;
}

const fmtCost = (c) => (c ? ` · 约 ¥${c.total < 0.1 ? c.total.toFixed(3) : c.total.toFixed(2)}${c.missing ? "（部分角色没填价格，未计入）" : ""}` : "");
const hitRate = (u) => Math.round((u.cached || 0) / Math.max(1, u.prompt || 0) * 100);

function usageLine(byRole) {
  const u = sumUsage(byRole);
  if (!u.calls) return "";
  return `${u.calls} 次调用 · 输入 ${fmtTok(u.prompt)}（缓存命中 ${hitRate(u)}%）· 输出 ${fmtTok(u.completion)}${fmtCost(usageCost(byRole))}`;
}

function fillUsageLine(node) {
  const line = usageLine((S.directive_usage || {})[node.dataset.did]);
  node.textContent = line ? `📊 本条指令用量：${line}` : "";
}

function directiveText(did) {
  const m = (S.channels[ALL] || []).find((x) => x.directive_id === did && x.sender === "ceo");
  return m ? m.text : did;
}

// ---------- 执行链路：一条指令从规划到汇报的每一步（多久、多少 token、哪里出错）

const TRACE_NAMES = { "gen_ai.request.model": "模型", "gen_ai.usage.input_tokens": "输入 token", "gen_ai.usage.output_tokens": "输出 token",
  "gen_ai.usage.cached_tokens": "缓存命中 token", "opc.ttft_ms": "首字延迟（毫秒）", "opc.retries": "重试次数", "opc.tool_calls": "调用的工具",
  "gen_ai.tool.name": "工具", "opc.role": "角色", "opc.task_status": "结束时的状态", "opc.hits": "检索到", "opc.sources": "出处",
  "opc.input_preview": "输入（最后一条消息开头）", "opc.output_preview": "输出开头", "opc.args_preview": "参数", "error": "出错",
  "opc.directive": "指令", "opc.query": "检索内容", "opc.path": "文件", "opc.result_chars": "结果字数" };

async function openTrace(query, title) {
  const res = await fetch(`/api/trace?${query}`);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return toast(data.detail || "没有这条链路");
  const spans = data.spans, byId = Object.fromEntries(spans.map((s) => [s.span_id, { ...s, kids: [] }]));
  let root = null;
  for (const s of Object.values(byId)) (s.parent_id && byId[s.parent_id] ? byId[s.parent_id].kids.push(s) : (root = root || s));
  const now = Date.now() / 1000, t0 = Math.min(...spans.map((s) => s.start));
  const t1 = Math.max(...spans.map((s) => s.end || now)), total = Math.max(t1 - t0, 0.001);
  const llm = spans.filter((s) => s.kind === "llm"), tools = spans.filter((s) => s.kind === "tool");
  const sum = (k) => llm.reduce((a, s) => a + (s.attrs[k] || 0), 0);
  const slowest = llm.reduce((a, s) => ((s.end || now) - s.start > ((a?.end || now) - (a?.start || now)) ? s : a), null);
  const errors = spans.filter((s) => s.status === "error");
  $("traceTitle").textContent = title || root?.name || "执行链路";
  $("traceSummary").innerHTML = `<div><b>${fmtSec(total)}</b><span>总用时</span></div>
    <div><b>${llm.length}</b><span>次模型调用</span></div><div><b>${tools.length}</b><span>次工具调用</span></div>
    <div><b>${fmtTok(sum("gen_ai.usage.input_tokens"))}</b><span>输入（命中缓存 ${fmtTok(sum("gen_ai.usage.cached_tokens"))}）</span></div>
    <div><b>${fmtTok(sum("gen_ai.usage.output_tokens"))}</b><span>输出</span></div>
    <div><b class="${errors.length ? "bad" : ""}">${errors.length}</b><span>出错的步骤</span></div>
    ${slowest ? `<div class="wide"><span>最慢的一次模型调用：${fmtSec((slowest.end || now) - slowest.start)}（${esc(parentName(slowest, byId))}）</span></div>` : ""}`;
  const rows = $("traceRows");
  rows.innerHTML = "";
  const walk = (s, depth) => {
    const dur = (s.end || now) - s.start;
    const row = el("div", `trace-row tk-${s.kind}${s.status === "error" ? " err" : ""}${s.end ? "" : " running"}`);
    const tok = s.kind === "llm" && s.attrs["gen_ai.usage.input_tokens"] != null
      ? ` · ${fmtTok(s.attrs["gen_ai.usage.input_tokens"])} → ${fmtTok(s.attrs["gen_ai.usage.output_tokens"] || 0)}` : "";
    row.innerHTML = `<div class="tr-name" style="padding-left:${depth * 14}px" title="${esc(s.name)}">${esc(s.name)}</div>
      <div class="tr-track"><div class="tr-bar" style="left:${((s.start - t0) / total) * 100}%;width:${Math.max((dur / total) * 100, 0.4)}%"></div></div>
      <div class="tr-dur">${fmtSec(dur)}${tok}</div>`;
    row.onclick = () => {
      rows.querySelectorAll(".sel").forEach((x) => x.classList.remove("sel"));
      row.classList.add("sel");
      const lines = [`${s.name}  ·  ${fmtSec(dur)}${s.end ? "" : "（进行中）"}  ·  ${s.status}`];
      for (const [k, v] of Object.entries(s.attrs)) {
        if (k === "key") continue;
        lines.push(`${TRACE_NAMES[k] || k}：${typeof v === "string" ? v : JSON.stringify(v, null, 0)}`);
      }
      $("traceDetail").textContent = lines.join("\n");
    };
    rows.appendChild(row);
    s.kids.sort((a, b) => a.start - b.start).forEach((k) => walk(k, depth + 1));
  };
  if (root) walk(root, 0);
  $("traceDetail").textContent = "点一行看详情：模型、token、首字延迟、输入输出的开头、出错原因……";
  $("traceDlg").showModal();
}

function parentName(s, byId) {
  let p = byId[s.parent_id];
  while (p && p.kind !== "agent" && p.kind !== "chain" && p.parent_id) p = byId[p.parent_id];
  return p ? p.name : s.name;
}

function fmtSec(sec) {
  return sec < 1 ? `${Math.round(sec * 1000)} 毫秒` : sec < 60 ? `${sec.toFixed(1)} 秒` : `${Math.floor(sec / 60)} 分 ${Math.round(sec % 60)} 秒`;
}

async function traceList() {
  const res = await fetch("/api/traces?limit=20");
  if (!res.ok) return "";
  const { traces } = await res.json();
  if (!traces.length) return "";
  return `<table class="usage-table trace-list"><tr><th>执行链路（最近的在前，点开看每一步）</th><th>用时</th><th>模型调用</th><th>出错</th></tr>${traces.map((t) =>
    `<tr data-trace="${esc(t.trace_id)}"><td>${esc(t.name)}</td><td>${fmtSec((t.end || t.last || t.start) - t.start)}${t.end ? "" : "（进行中）"}</td>
     <td>${t.llm_calls}</td><td>${t.errors || ""}</td></tr>`).join("")}</table>`;
}

function openUsage() {
  const body = $("usageBody");
  const total = sumUsage(S.usage);
  const cost = usageCost(S.usage);
  const eff = (home?.efforts || []).find((e) => e.id === C?.effort);
  let html = `<div class="usage-total">
    <div><b>${fmtTok(total.prompt + total.completion)}</b><span>tokens 合计</span></div>
    <div><b>${total.calls}</b><span>次模型调用</span></div>
    <div><b>${hitRate(total)}%</b><span>输入命中缓存</span></div>
    <div><b>${cost ? "¥" + cost.total.toFixed(2) : "—"}</b><span>${cost ? "估算花费" : "在模型连接里填价格后显示"}</span></div></div>`;
  const row = (name, u, c) => `<tr><td>${name}</td><td>${u.calls}</td><td>${fmtTok(u.prompt)}</td><td>${hitRate(u)}%</td>
    <td>${fmtTok(u.completion)}</td><td>${c === null ? "—" : "¥" + c.toFixed(c < 0.1 ? 3 : 2)}</td></tr>`;
  const head = (first) => `<tr><th>${first}</th><th>调用</th><th>输入</th><th>缓存命中</th><th>输出</th><th>估算</th></tr>`;
  const dirs = Object.entries(S.directive_usage || {}).reverse();
  if (dirs.length) {
    html += `<table class="usage-table">${head("按指令（最近的在前）")}${dirs.map(([did, byRole]) => {
      const c = usageCost(byRole);
      return row(esc(directiveText(did)).slice(0, 60), sumUsage(byRole), c ? c.total : null);
    }).join("")}</table>`;
  }
  html += `<table class="usage-table">${head("按角色（累计）")}${Object.entries(S.usage || {}).map(([role, u]) =>
    row(`${who(role).icon || ""} ${esc(who(role).name)}`, u, costOf(u, priceOf(role)))).join("")}</table>`;
  html += `<div class="usage-note"><b>怎么算的</b>：每次调用模型，<b>输入</b>是发给它的全部内容（系统提示词、任务说明、对话、读到的文件），
    所以步数越多、读的文件越长，输入越多；其中和上一次调用开头相同的部分会<b>命中缓存</b>，价格通常低很多。<b>输出</b>是模型生成的内容（含思考过程）。
    规划、汇报、理解你的话算在"规划助手"名下；私聊只计入按角色的累计。估算按每个角色<b>当前</b>用的模型价格（在"⚙ 管理模型连接"里填，元/百万 token）。<br>
    <b>投入档位</b>：${eff ? `${esc(eff.name)}——${esc(eff.desc)}` : "标准"}。档位只限审查轮数和执行步数，不限 token：按 token 封顶会做到一半被截断，返工反而更费。可以在"⚙ 设置"里改。</div>`;
  body.innerHTML = html;
  $("usageDlg").showModal();
  traceList().then((t) => {
    if (!t) return;
    body.insertAdjacentHTML("beforeend", t);
    body.querySelectorAll("[data-trace]").forEach((tr) => {
      tr.onclick = () => openTrace(`id=${encodeURIComponent(tr.dataset.trace)}`, tr.querySelector("td").textContent);
    });
  });
}
$("usage").onclick = () => { if (C) openUsage(); };

function effortOptions() {
  return (home?.efforts || []).map((e) => `<option value="${esc(e.id)}">${esc(e.name)}：${esc(e.desc)}</option>`).join("");
}

function effortDetail(id) {
  const e = (home?.efforts || []).find((x) => x.id === id);
  return e ? `审查最多 ${e.review_rounds} 轮 · 每次执行最多 ${e.steps} 步，步数用完自动接着做 ${e.extensions} 次 · 最多问同事 ${e.asks} 次` : "";
}


// 私聊时选择讨论哪项工作：它负责或审查过的任务；选了之后这次对话带着那项工作的完整上下文，只讨论不改文件
function updateTopics() {
  const row = $("topicRow"), sel = $("topicSel");
  if (channel === ALL || channel === MANAGER || !C) { row.hidden = true; return; }
  const mine = Object.values(S.tasks || {}).filter((t) => (t.assignee === channel || t.reviewer === channel) && t.status !== "cancelled").reverse();
  if (!mine.length) { row.hidden = true; topics[channel] = ""; return; }
  row.hidden = false;
  const cur = topics[channel] || "";
  sel.innerHTML = `<option value="">自由聊天</option>` + mine.map((t) =>
    `<option value="${esc(t.id)}">《${esc(t.title)}》· ${t.reviewer === channel && t.assignee !== channel ? "审查 · " : ""}${STATUS[t.status] || t.status}</option>`).join("");
  sel.value = mine.some((t) => t.id === cur) ? cur : "";
  $("topicTip").textContent = sel.value ? "带着这项工作的要求、产出和审查意见讨论；要改动时会整理成调整建议，你确认后返工" : "";
}

$("topicSel").onchange = () => { topics[channel] = $("topicSel").value; updateTopics(); };

function submit() {
  const text = $("input").value.trim();
  if (!text || (review && channel === ALL)) return;  // 执行中也能发：后端作为实时插话处理
  send({ type: "send", text, target: channel, topic: (channel !== ALL && channel !== MANAGER && topics[channel]) || null });
  $("input").value = "";
  autosize();
}

function autosize() {
  const i = $("input");
  i.style.height = "auto";
  i.style.height = Math.min(i.scrollHeight, 200) + "px";
}

$("composer").onsubmit = (e) => { e.preventDefault(); submit(); };
$("input").oninput = autosize;
$("input").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); } };
$("stopBtn").onclick = () => send({ type: "stop" });
$("retryBtn").onclick = () => send({ type: "retry_now" });
$("resumeRunBtn").onclick = () => send({ type: "resume_run" });
$("backBtn").onclick = () => send({ type: "close_company" });

// ---------------------------------------------------------------- 任务看板

function renderBoard() {
  const tab = $("boardTab");
  tab.innerHTML = "";
  const tasks = Object.values(S.tasks || {});
  if (!tasks.length) { tab.appendChild(el("div", "board-empty", "还没有任务。<br>在「全公司」频道下令后，规划助手会把指令拆成任务显示在这里。")); return; }
  const ceoMsgs = Object.fromEntries((S.channels[ALL] || []).filter((m) => m.sender === "ceo" && m.directive_id).map((m) => [m.directive_id, m.text]));
  const order = [];
  for (const t of tasks) if (!order.includes(t.directive_id)) order.push(t.directive_id);
  for (const did of order.reverse()) {
    const group = tasks.filter((t) => t.directive_id === did && t.status !== "cancelled");
    if (!group.length) continue;
    const done = group.filter((t) => t.status === "done").length;
    const g = el("div", "directive-group");
    g.innerHTML = `<div class="dg-head"><b title="${esc(ceoMsgs[did] || "")}">${esc(ceoMsgs[did] || "指令")}</b><span>${done}/${group.length} 完成</span></div>`;
    for (const t of group) g.appendChild(taskCard(t));
    tab.appendChild(g);
  }
}

// 任务卡片上的干预按钮：只对当前指令的任务、没有待处理的审批时显示
function taskActions(t) {
  if (review || !currentTasks().some((x) => x.id === t.id)) return null;
  const box = el("div", "tc-actions");
  const add = (text, cls, fn) => { const b = el("button", `btn small ${cls}`, text); b.onclick = (e) => { e.stopPropagation(); fn(); }; box.appendChild(b); };
  const started = t.started || ["review", "rework", "escalated", "done"].includes(t.status);
  if (started && !["failed", "skipped", "cancelled"].includes(t.status)) {
    add("让它返工", "ghost", () => {
      const feedback = prompt(`《${t.title}》哪里需要改？依赖它的任务会先暂停，等它改完再继续。`);
      if (feedback !== null) send({ type: "task_action", action: "rework", task_id: t.id, feedback });
    });
  }
  if (t.status === "failed") add("重试", "primary", () => send({ type: "task_action", action: "retry", task_id: t.id }));
  if (t.paused) add("继续", "primary", () => send({ type: "task_action", action: "resume", task_id: t.id }));
  else if (!["done", "failed", "skipped", "cancelled"].includes(t.status)) add("暂停", "ghost", () => send({ type: "task_action", action: "pause", task_id: t.id }));
  return box.children.length ? box : null;
}

function taskCard(t) {
  const p = presets[t.assignee] || {};
  const card = el("div", "task-card");
  const reworking = t.status === "running" && (t.reviews || []).length > 0;
  const resync = t.status === "pending" && Object.keys(t.resync || {}).length > 0;
  const shown = t.paused && !["done", "failed", "skipped", "cancelled"].includes(t.status) ? "paused" : resync ? "resync" : t.status;
  const label = reworking ? `返工中（第 ${t.reviews.length} 轮意见）`
    : shown === "resync" ? "待同步（上游改了）" : STATUS[shown] || t.status;
  const active = ["running", "review", "reviewing", "rework"].includes(t.status) && busy && phaseSince[t.id];
  card.innerHTML = `<div class="tc-row"><span>${p.icon || ""}</span><span class="tc-title">${esc(t.title)}</span>`
    + (active ? `<span class="tc-time" data-since="${phaseSince[t.id]}">${fmtDuration(serverNow() - phaseSince[t.id])}</span>` : "")
    + `<span class="status s-${reworking ? "rework" : shown}">${label}</span></div>`;
  const live = active && streamOfTask(t);
  if (live) card.insertAdjacentHTML("beforeend", `<div class="tc-act" data-act="${live.id}">${actText(live.act)}</div>`);
  if (openTasks.has(t.id)) {
    const extra = el("div", "tc-extra");
    extra.innerHTML = `<div class="label">负责人</div>${esc(p.name || t.assignee)}<div class="label">要求</div>${esc(t.detail)}`
      + (requirementsHtml(t) ? `<div class="tc-req">${requirementsHtml(t)}</div>` : "");
    if (t.reviewer) extra.insertAdjacentHTML("beforeend", `<div class="label">审查人</div>${esc(who(t.reviewer).name)}`);
    for (const r of t.reviews || []) {
      extra.insertAdjacentHTML("beforeend", `<div class="label">第 ${r.round} 轮审查：${VERDICT[r.verdict] || esc(r.verdict)}</div>
        ${esc(r.summary || "")}${reviewExtrasHtml(r)}${(r.issues || []).length ? `<ol class="rv-issues">${r.issues.map((i) => `<li>${esc(i.text)}</li>`).join("")}</ol>` : ""}`);
    }
    if (t.ceo_note) extra.insertAdjacentHTML("beforeend", `<div class="label">你的裁决意见</div>${esc(t.ceo_note)}`);
    if (t.result) extra.insertAdjacentHTML("beforeend", `<div class="label">汇报</div><div class="md">${markdown(t.result)}</div>`);
    const chips = chipsFor([], t.files);
    if (chips) extra.appendChild(chips);
    const actions = taskActions(t);
    if (actions) extra.appendChild(actions);
    card.appendChild(extra);
  }
  card.onclick = (e) => {
    if (e.target.closest(".chip")) return;
    openTasks.has(t.id) ? openTasks.delete(t.id) : openTasks.add(t.id);
    renderBoard();
  };
  return card;
}

// ---------------------------------------------------------------- 文件

function renderFiles(changed) {
  $("fileCount").textContent = files.length || "";
  const list = $("fileList");
  list.innerHTML = files.length ? "" : `<div class="board-empty">还没有文件。同事们的产出会出现在这里。</div>`;
  for (const f of files) {
    const row = el("div", "file-row" + (f.path === openFile ? " active" : "") + (f.path === changed ? " flash" : ""));
    row.innerHTML = `<span class="fr-path" title="${esc(f.path)}">${esc(f.path)}</span><span class="fr-author" title="${esc(who(f.author).name)}">${who(f.author).icon}</span>`;
    row.onclick = () => viewFile(f.path);
    list.appendChild(row);
  }
}

async function viewFile(path) {
  const res = await fetch("/api/file?path=" + encodeURIComponent(path));
  if (!res.ok) return toast("文件不存在");
  const data = await res.json();
  openFile = path;
  $("viewer").hidden = false;
  $("viewerPath").textContent = path;
  const body = $("viewerBody");
  if (path.endsWith(".md")) {
    body.innerHTML = `<div class="md">${markdown(data.content)}</div>`;
  } else {
    body.innerHTML = "";
    const pre = el("pre", "code");
    pre.textContent = data.content;
    body.appendChild(pre);
  }
  renderFiles();
}

function openFileInPanel(path) {
  selectTab("files");
  $("panel").classList.add("show");
  viewFile(path);
}

function selectTab(name) {
  if (name === "files") $("filesTabBtn").classList.remove("new");
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $("boardTab").hidden = name !== "board";
  $("filesTab").hidden = name !== "files";
  $("kbTab").hidden = name !== "kb";
  if (name === "kb") { $("kbQuery").focus(); loadLibrary(); }
}

// ---------------------------------------------------------------- 知识库（只读搜索）

const KB_KIND = { markdown: "文档", function: "函数", class: "类", code: "代码", text: "文本", task: "任务记录" };
const KB_CATEGORY = { task: "任务记录", directive: "指令结论", lib: "公司资料", me: "个人资料" };
const kbCategory = (source) => { const h = source.split(":")[0]; return source.includes(":") && KB_CATEGORY[h] ? h : "file"; };

async function kbSearch() {
  const q = $("kbQuery").value.trim();
  if (!q) return;
  const status = $("kbStatus"), list = $("kbResults");
  status.textContent = "检索中…（第一次使用要加载向量模型、建立索引，可能需要十几秒）";
  list.innerHTML = "";
  const res = await fetch(`/api/kb/search?q=${encodeURIComponent(q)}&mode=${$("kbMode").value}&k=10`);
  const data = await res.json();
  if (!res.ok) { status.textContent = data.detail || "检索失败"; return; }
  const s = data.stats;
  status.textContent = `知识库：${s.chunks} 块（文件 ${s.sources.file || 0} 个，任务记录 ${s.sources.task || 0} 条，`
    + `指令结论 ${s.sources.directive || 0} 条，资料 ${(s.sources.lib || 0) + (s.sources.me || 0)} 份）· `
    + (s.embedder ? `向量检索已启用（${s.vectors} 块）` : "未启用向量检索，只用关键词")
    + (s.vector_error ? ` · 向量检索出错：${s.vector_error}` : "")
    + (s.reranker && $("kbMode").value === "hybrid" ? " · 已重排序" : "") + (s.rerank_error ? ` · 重排序出错：${s.rerank_error}` : "");
  if (!data.results.length) { list.appendChild(el("div", "board-empty", "没有找到相关内容。")); return; }
  for (const r of data.results) {
    const cat = kbCategory(r.source), isFile = cat === "file", isLib = cat === "lib" || cat === "me";
    const item = el("div", "kb-item");
    const ranks = [r.keyword_rank && `关键词 #${r.keyword_rank}`, r.vector_rank && `向量 #${r.vector_rank}`,
                   r.rerank_score != null && `重排得分 ${r.rerank_score}`].filter(Boolean).join(" · ");
    const kind = isFile ? (KB_KIND[r.kind] || r.kind) : KB_CATEGORY[cat];
    const src = isFile ? `${r.source}:${r.start}-${r.end}` : isLib ? r.source.slice(r.source.indexOf(":") + 1) : r.title;
    item.innerHTML = `<div class="kb-head"><span class="kb-kind${isLib ? " lib" : ""}">${esc(kind)}</span>
      <span class="kb-src" title="${esc(r.source)}">${esc(src)}</span></div>
      ${(isFile || isLib) && r.title ? `<div class="kb-title">${esc(r.title)}</div>` : ""}
      <pre class="kb-snippet"></pre><div class="kb-meta">${esc(ranks)}${isFile ? " · 点击打开文件" : " · 点击展开全文"}</div>`;
    const pre = item.querySelector(".kb-snippet");
    pre.textContent = r.snippet;
    item.onclick = () => {
      if (isFile) { selectTab("files"); viewFile(r.source); return; }
      pre.textContent = pre.textContent === r.text ? r.snippet : r.text;
    };
    list.appendChild(item);
  }
}

$("kbForm").onsubmit = (e) => { e.preventDefault(); kbSearch(); };

// ---------- 资料库：公司资料 / 个人资料（上传后立即解析、切块、入库）

let libScope = "company";

async function loadLibrary() {
  if (!C) return;
  const res = await fetch("/api/library");
  if (!res.ok) return;
  const data = await res.json();
  renderLibrary("company", data.company || []);
  renderLibrary("personal", data.personal || []);
  const n = (data.company || []).length + (data.personal || []).length;
  $("libCount").textContent = n ? `${n} 份` : "（还没有资料，可以上传）";
}

function renderLibrary(scope, items) {
  const box = $(scope === "company" ? "libCompany" : "libPersonal");
  box.innerHTML = items.length ? "" : `<div class="lib-empty muted">还没有${scope === "company" ? "公司" : "个人"}资料</div>`;
  for (const it of items) {
    const row = el("div", "lib-item" + (it.error ? " warn" : ""));
    row.innerHTML = `<span class="lib-name"></span><span class="lib-meta muted"></span><button class="icon-btn" title="删除">✕</button>`;
    row.querySelector(".lib-name").textContent = it.name;
    row.querySelector(".lib-meta").textContent = it.error ? `⚠ ${it.error}` : `${fmtSize(it.size)} · ${it.chunks} 块`;
    row.querySelector("button").onclick = async () => {
      if (!confirm(`删除资料「${it.name}」？角色之后就检索不到它了。`)) return;
      await fetch(`/api/library/${scope}?name=${encodeURIComponent(it.name)}`, { method: "DELETE" });
      loadLibrary();
    };
    box.appendChild(row);
  }
}

function fmtSize(n) {
  return n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} 字节`;
}

async function uploadLibrary(scope, files) {
  const box = $(scope === "company" ? "libCompany" : "libPersonal");
  for (const f of files) {
    const row = el("div", "lib-item busy", `<span class="lib-name">${esc(f.name)}</span><span class="lib-meta muted">入库中…（解析、切块、生成向量）</span>`);
    box.prepend(row);
    const res = await fetch(`/api/library/${scope}?name=${encodeURIComponent(f.name)}`, { method: "POST", body: f });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      row.className = "lib-item warn";
      row.querySelector(".lib-meta").textContent = `⚠ ${data.detail || "上传失败"}`;
      await new Promise((r) => setTimeout(r, 2500));
    }
  }
  loadLibrary();
}

document.querySelectorAll("[data-up]").forEach((b) => {
  b.onclick = () => { libScope = b.dataset.up; $("libFile").value = ""; $("libFile").click(); };
});
$("libFile").onchange = () => { if ($("libFile").files.length) uploadLibrary(libScope, [...$("libFile").files]); };
document.querySelectorAll(".lib-sec").forEach((sec) => {
  sec.ondragover = (e) => { e.preventDefault(); sec.classList.add("drop"); };
  sec.ondragleave = () => sec.classList.remove("drop");
  sec.ondrop = (e) => {
    e.preventDefault();
    sec.classList.remove("drop");
    if (e.dataTransfer.files.length) uploadLibrary(sec.dataset.scope, [...e.dataTransfer.files]);
  };
});

document.querySelectorAll(".tab").forEach((t) => { t.onclick = () => selectTab(t.dataset.tab); });
$("viewerClose").onclick = () => { openFile = null; $("viewer").hidden = true; renderFiles(); };
$("panelToggle").onclick = () => $("panel").classList.toggle("show");
$("navToggle").onclick = () => $("nav").classList.toggle("show");

// ---------------------------------------------------------------- 事件

const handlers = {
  home(ev) {
    home = ev;
    presets = Object.fromEntries([...ev.presets, ...(ev.library || [])].map((p) => [p.id, p]));
    if ($("roleGrid").children.length) refreshModelSelects();
    updateBanner();
    if (!C) showHome();
    promptFirstRun();
  },
  snapshot(ev) {
    const switched = !C || C.id !== ev.company.id;
    C = ev.company;
    coderAvailable = !!ev.coder_available;
    S = { tasks: {}, usage: {}, ...ev.state, channels: ev.state.channels || {} };
    review = ev.review; busy = ev.busy; paused = ev.paused || null; unfinished = !!ev.unfinished; roleStatus = ev.role_status || {}; files = ev.files;
    setTiming(ev);
    streams.clear();
    if (switched) { channel = ALL; unread.clear(); openFile = null; $("viewer").hidden = true; }
    showWorkspace();
    renderChannels(); renderChat(); renderBoard(); renderFiles(); updateStatusBar();
  },
  closed() { showHome(); },
  status(ev) {
    busy = ev.busy; review = ev.review; paused = ev.paused || null; unfinished = !!ev.unfinished; roleStatus = ev.role_status || {};
    if (ev.usage) S.usage = ev.usage;
    setTiming(ev);
    if (!busy) { for (const s of streams.values()) s.el?.remove(); streams.clear(); }
    renderChannels(); updateStatusBar(); updateComposer(); updateTyping();
    // 审批状态变化时，重绘计划卡片上的审批按钮和裁决卡片
    document.querySelectorAll("[data-plan]").forEach((node) => {
      const m = (S.channels[ALL] || []).find((x) => x.id === node.dataset.plan);
      if (m && channel === ALL) node.replaceWith(renderPlan(m));
    });
    document.querySelectorAll("[data-escalation]").forEach((node) => node.remove());
    if (channel === ALL && isEscalation()) appendToChat(escalationCard());
    if (review && channel !== ALL) unread.add(ALL), renderChannels();
  },
  role_status(ev) { roleStatus = ev.role_status; renderChannels(); updateTyping(); if (isRoleChannel(channel)) updateNow(); },
  message(ev) {
    (S.channels[ev.channel] ||= []).push(ev.message);
    const s = streams.get(ev.message.id);
    streams.delete(ev.message.id);
    if (ev.message.proposal_id) {  // 调整建议处理完：卡片上的按钮换成结果（卡片可能显示在角色频道里）
      const card = document.querySelector(`[data-proposal="${ev.message.proposal_id}"]`);
      const m = (S.channels[ev.channel] || []).find((x) => x.id === ev.message.proposal_id);
      if (card && m) card.replaceWith(renderProposal(m, ev.channel));
    }
    if (ev.channel === ALL && channel === ALL && simpleAll() && isTaskDetail(ev.message)) {  // 过程消息：更新任务卡片
      const id = ev.message.task_id;
      const old = document.querySelector(`[data-taskcard="${id}"]`);
      old ? old.replaceWith(taskProgressCard(S.tasks[id])) : appendToChat(taskProgressCard(S.tasks[id]));
      s?.el?.remove();
      updateTyping();
      return;
    }
    if (ev.channel === ALL && ev.message.kind === "report") $("filesTabBtn").classList.add("new");  // 有新成果
    const asWork = isRoleChannel(channel) && ev.channel === ALL && workFor(ev.message, channel);
    if (ev.channel === channel || asWork) {
      const node = asWork ? renderWork(ev.message, channel) : renderMessage(ev.message);
      s?.el?.isConnected ? s.el.replaceWith(node) : appendToChat(node);
      updateTyping();
    } else if (ev.message.sender !== "ceo") {
      unread.add(ev.channel);
      renderChannels();
    }
  },
  tasks(ev) {
    Object.assign(S.tasks, ev.tasks);
    Object.assign(phaseSince, ev.since || {});
    renderBoard(); renderChannels();
    refreshTaskCards(Object.keys(ev.tasks));
    if (isRoleChannel(channel)) { updateNow(); updateTopics(); }
    // 计划卡片里的任务状态跟着更新
    if (channel === ALL) document.querySelectorAll("[data-plan]").forEach((node) => {
      const m = (S.channels[ALL] || []).find((x) => x.id === node.dataset.plan);
      if (m && m.tasks?.some((id) => ev.tasks[id])) node.replaceWith(renderPlan(m));
    });
  },
  usage(ev) {
    S.usage = ev.usage;
    if (ev.directive_usage) S.directive_usage = ev.directive_usage;
    document.querySelectorAll(".usage-line[data-did]").forEach(fillUsageLine);
    updateStatusBar();
  },
  triage(ev) {
    ev.status === "working" ? triaging.add(ev.channel) : triaging.delete(ev.channel);
    renderChannels();
    if (ev.channel === channel) updateTyping();
  },
  delta(ev) {
    const s = streamFor(ev);
    s[ev.kind === "reasoning" ? "reasoning" : "text"] += ev.text;
    if (s.act) s.act.stepChars += ev.text.length;
    scheduleStream(s);
  },
  activity(ev) {
    const s = streamFor(ev);
    const a = s.act || (s.act = { step: 0, phase: "", reads: [], stepChars: 0, phaseSince: nowSec() });
    const fresh = !a.phase;
    if (ev.step !== a.step) { a.step = ev.step; a.stepChars = 0; }
    if (ev.phase !== a.phase || ev.tool !== a.tool) a.phaseSince = nowSec();
    a.phase = ev.phase; a.tool = ev.tool; a.path = ev.path;
    if (ev.phase === "tool" && ev.tool === "read_file" && ev.path && !a.reads.includes(ev.path)) a.reads.push(ev.path);
    scheduleStream(s);
    if (fresh && s.channel === ALL) {  // 任务卡片上出现进度摘要
      renderBoard();
      refreshTaskCards(Object.values(S.tasks).filter((t) => [t.assignee, t.reviewer].includes(s.sender)).map((t) => t.id));
    }
  },
  tool(ev) {
    const s = streamFor(ev);
    if ((ev.name === "write_file" || ev.name === "edit_file") && ev.ok) { if (!s.files.includes(ev.path)) s.files.push(ev.path); }
    else s.tools.push(ev);
    s.progress = 0;  // 有新步骤了，进度提示清零
    s.writing = {};  // 文件已经写好（或者失败），实时预览收起，改成文件标签
    scheduleStream(s);
  },
  progress(ev) {
    const s = streamFor(ev);
    s.progress = ev.chars;
    scheduleStream(s);
  },
  writing(ev) {
    const s = streamFor(ev);
    s.writing = s.writing || {};
    const w = s.writing[ev.index] || (s.writing[ev.index] = { tool: ev.tool, path: "", text: "", chars: 0 });
    w.text += ev.text;
    w.chars = ev.chars;
    if (ev.path) w.path = ev.path;
    scheduleStream(s);
  },
  heard(ev) {
    // 正在工作的同事收到了 CEO 的插话：在它的实时消息上标一下
    const s = streamFor(ev);
    s.tools.push({ name: "heard", ok: true });
    scheduleStream(s);
  },
  files(ev) {
    files = ev.files;
    renderFiles(ev.changed);
    if (ev.changed && ev.changed === openFile) viewFile(openFile);
  },
  notice(ev) { toast(ev.text); },
  plan_edit_error(ev) { $("planEditErr").textContent = ev.error; },  // 编辑窗口不关，改正后再提交
  plan_edit_ok() { $("planDlg").close(); },
};

function connect() {
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { $("conn").classList.add("on"); };
  ws.onmessage = (m) => { const ev = JSON.parse(m.data); (handlers[ev.type] || (() => {}))(ev); };
  ws.onclose = () => { $("conn").classList.remove("on"); setTimeout(connect, 1500); };
}

function send(msg) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
  else toast("和服务的连接断开了，正在重连…");
}

connect();

$("readerClose").onclick = closeReader;
$("reader").onclick = (e) => { if (e.target.id === "reader") closeReader(); };
$("readerPanel").onclick = () => { const p = $("reader").dataset.path; closeReader(); openFileInPanel(p); };
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("reader").hidden) closeReader(); });
