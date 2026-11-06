/* FundRadar dashboard */
const $ = (id) => document.getElementById(id);
const PALETTE = ["#c0392b", "#2e86c1", "#8e44ad", "#d35400", "#16a085", "#7f8c8d"];
const ACTION_COLORS = {
  "加仓": "#c0392b",
  "减仓": "#1e8449",
  "持有": "#2471a3",
  "观望": "#7f8c8d",
  "待刷新": "#95a5a6",
};

let state = null;
let busy = false;

/* ------------------------------------------------------------------ format */
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[ch]));

function money(value) {
  if (value === null || value === undefined || value === "") return "--";
  const num = Number(value);
  if (!Number.isFinite(num)) return "--";
  return num.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function pct(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "--";
  const num = Number(value);
  if (!Number.isFinite(num)) return "--";
  return (num >= 0 ? "+" : "") + num.toFixed(digits) + "%";
}

function clsFor(value) {
  if (value === null || value === undefined || value === "") return "flat";
  const num = Number(value);
  if (!Number.isFinite(num) || num === 0) return "flat";
  return num > 0 ? "up" : "down";
}

function toast(message, ms = 2800) {
  const el = $("toast");
  el.textContent = message;
  el.classList.remove("hidden");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => el.classList.add("hidden"), ms);
}

/* -------------------------------------------------------------------- load */
async function api(path, options) {
  const res = await fetch(path, options);
  let data = null;
  try { data = await res.json(); } catch (err) { data = null; }
  if (!res.ok || !data || data.ok === false) {
    throw new Error((data && data.error) || ("请求失败（" + res.status + "）"));
  }
  return data;
}

async function loadState() {
  try {
    const data = await api("/api/state");
    state = data.state;
    render();
  } catch (err) {
    toast("加载失败：" + err.message);
  }
}

/* ------------------------------------------------------------------ render */
function render() {
  if (!state) return;
  renderHeader();
  renderPortfolio();
  renderDiagnosis();
  renderFunds();
  $("summary").textContent = state.summary || "暂无组合建议，点击刷新行情。";
  const link = $("reportLink");
  if (state.report_url) {
    link.href = state.report_url;
    link.classList.remove("hidden");
  } else {
    link.classList.add("hidden");
  }
}

function renderHeader() {
  const market = state.market || {};
  const badge = $("regime");
  if (market.regime) {
    badge.textContent = "大盘 " + market.regime + " " + Math.round(market.score || 0) + " 分";
    badge.style.background = {
      "强势": "#c0392b", "偏强": "#d35400", "震荡": "#b7950b",
      "偏弱": "#2e86c1", "弱势": "#1e8449",
    }[market.regime] || "#2e86c1";
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }

  const banner = $("staleBanner");
  const pending = (state.funds || []).filter((fund) => fund.pending).length;
  const parts = [];
  if (state.stale) parts.push("当前展示的是 " + (state.date || "上一期") + " 的报告，可能不是今天。");
  if (pending) parts.push(pending + " 只基金还没有行情数据。");
  if (parts.length) {
    banner.innerHTML = parts.join(" ") +
      ' <button type="button" id="bannerRefresh">刷新行情</button>';
    banner.classList.remove("hidden");
    const btn = $("bannerRefresh");
    if (btn) btn.addEventListener("click", refreshData);
  } else {
    banner.classList.add("hidden");
  }
}

function renderPortfolio() {
  const p = state.portfolio || {};
  $("totalValue").textContent = money(p.total_value);
  const profitEl = $("totalProfit");
  profitEl.textContent = money(p.total_profit) + "（" + pct(p.profit_pct) + "）";
  profitEl.className = "big " + clsFor(p.total_profit);

  let today = 0;
  let hasToday = false;
  for (const fund of state.funds || []) {
    if (fund.est_chg !== null && fund.est_chg !== undefined && fund.est_chg !== ""
        && Number.isFinite(Number(fund.est_chg))) {
      today += Number(fund.market_value || 0) * Number(fund.est_chg) / 100;
      hasToday = true;
    }
  }
  const todayEl = $("todayPnl");
  todayEl.textContent = hasToday ? money(today) : "--";
  todayEl.className = "big " + clsFor(today);

  $("updatedAt").textContent = state.generated_at || "尚未生成";

  const bar = $("allocBar");
  const legend = $("legend");
  bar.innerHTML = "";
  legend.innerHTML = "";
  (state.funds || []).forEach((fund, idx) => {
    const color = PALETTE[idx % PALETTE.length];
    const weight = Number(fund.weight || 0) * 100;
    const seg = document.createElement("span");
    seg.style.width = Math.max(0, Math.min(100, weight)).toFixed(2) + "%";
    seg.style.background = color;
    seg.title = (fund.name || fund.code) + " " + weight.toFixed(1) + "%";
    bar.appendChild(seg);
    legend.innerHTML += '<span><i style="background:' + color + '"></i>' +
      esc(fund.code) + " " + weight.toFixed(1) + "%（目标 " +
      (Number(fund.target_weight || 0) * 100).toFixed(0) + "%）</span>";
  });

  const warn = $("warning");
  if (p.warning) {
    warn.textContent = "⚠️ " + p.warning;
    warn.classList.remove("hidden");
  } else {
    warn.classList.add("hidden");
  }
}

function actionColor(action) {
  return ACTION_COLORS[action] || "#7f8c8d";
}

/* ---------------------------------------------------------------- diagnosis */
function renderDiagnosis() {
  const d = state.diagnosis || {};
  const el = $("diagnosis");
  if (!Number.isFinite(Number(d.score))) {
    el.classList.add("hidden");
    return;
  }
  el.classList.remove("hidden");
  const colors = {
    "稳健": "#1e8449", "较均衡": "#2e86c1", "偏集中": "#b9770e",
    "需调整": "#d35400", "高风险": "#c0392b", "无持仓": "#7f8c8d",
  };
  const color = colors[d.level] || "#7f8c8d";
  const dims = (d.dimensions || []).map((dim) => {
    const score = Math.max(0, Math.min(100, Number(dim.score) || 0));
    return '<div class="dim-row"><div class="dim-top"><span>' + esc(dim.name) +
      "</span><span>" + Math.round(score) + '/100</span></div>' +
      '<div class="dim-bar"><i style="width:' + score.toFixed(0) + '%"></i></div>' +
      '<div class="dim-comment">' + esc(dim.comment || "") + "</div></div>";
  }).join("");
  const issues = (d.issues || []).map((issue) =>
    '<div class="diag-issue ' + esc(issue.severity || "") + '"><b>' + esc(issue.title) +
    "</b>" + esc(issue.detail) + "</div>").join("");
  const suggestions = (d.suggestions || []).map((item) => "<li>" + esc(item) + "</li>").join("");
  const positives = (d.positives || []).map((item) =>
    '<div class="diag-pos">✓ ' + esc(item) + "</div>").join("");
  el.innerHTML =
    '<div class="diag-head"><div><div class="diag-title">配置诊断 <span class="diag-level" ' +
    'style="background:' + color + '">' + esc(d.level) + "</span></div>" +
    '<div class="small">' + esc(d.summary || "") + "</div></div>" +
    '<div class="diag-score" style="color:' + color + '">' + Math.round(Number(d.score)) +
    "<span>/100</span></div></div>" +
    '<div class="diag-grid"><div>' + dims + "</div><div>" + issues + "</div></div>" +
    '<div class="diag-sugg"><div class="label">配置建议</div><ul>' + suggestions +
    "</ul>" + positives + "</div>" +
    '<div class="diag-actions"><button type="button" id="equalizeBtn">目标占比均分</button>' +
    '<button type="button" id="targetSuggestBtn" class="primary">查看建议目标</button></div>';
  const btn = $("equalizeBtn");
  if (btn) btn.addEventListener("click", equalizeTargets);
  const suggestBtn = $("targetSuggestBtn");
  if (suggestBtn) suggestBtn.addEventListener("click", showTargetSuggestion);
}

async function equalizeTargets() {
  if (!state || busy) return;
  if (!confirm("把全部基金的目标占比改为平均分配？")) return;
  busy = true;
  try {
    const data = await api("/api/portfolio/equal-targets", { method: "POST" });
    state = data.state;
    render();
    toast("目标占比已均分");
  } catch (err) {
    toast("操作失败：" + err.message);
  } finally {
    busy = false;
  }
}

function closeTargetModal() {
  $("targetModal").classList.add("hidden");
}

function showTargetSuggestion() {
  const suggestion = state.target_suggestion || {};
  const rows = suggestion.rows || [];
  if (!rows.length) {
    toast("暂时没有可用的建议目标");
    return;
  }
  $("targetSummary").textContent = suggestion.summary || "";
  $("targetRows").innerHTML = rows.map((row) => {
    const suggested = (Number(row.suggested) || 0) * 100;
    const cls = suggested <= 0 ? "zero" : ((Number(row.change_pp) || 0) > 0 ? "up" : "down");
    return "<tr><td>" + esc(row.code) + " " + esc(row.name) + "</td>" +
      "<td>" + ((Number(row.weight) || 0) * 100).toFixed(1) + "%</td>" +
      "<td>" + ((Number(row.current_target) || 0) * 100).toFixed(1) + "%</td>" +
      '<td class="' + cls + '">' + suggested.toFixed(1) + "%</td>" +
      "<td>" + esc(row.reason) + "</td></tr>";
  }).join("");
  $("targetModal").classList.remove("hidden");
}

async function applyTargets() {
  if (busy) return;
  busy = true;
  const btn = $("applyTargetBtn");
  btn.disabled = true;
  try {
    const data = await api("/api/portfolio/apply-suggested-targets", { method: "POST" });
    state = data.state;
    render();
    closeTargetModal();
    toast("已应用建议目标");
  } catch (err) {
    toast("应用失败：" + err.message, 4200);
  } finally {
    busy = false;
    btn.disabled = false;
  }
}

function renderFunds() {
  const list = $("fundList");
  const funds = state.funds || [];
  if (!funds.length) {
    list.innerHTML = '<div class="panel empty">还没有基金，点击右上角“添加基金”开始。</div>';
    return;
  }
  list.innerHTML = funds.map((fund, idx) => {
    const color = PALETTE[idx % PALETTE.length];
    const action = fund.action || "待刷新";
    const amount = Number(fund.suggest_amount || 0);
    const badgeText = amount > 0
      ? esc(action) + " · 约 " + money(amount) + " 元"
      : esc(action);
    const reasons = (fund.reasons || []).slice(0, 4)
      .map((reason) => "<li>" + esc(reason) + "</li>").join("");
    const score = Number.isFinite(Number(fund.score))
      ? "评分 " + Math.round(Number(fund.score)) + "/100" : "等待行情";
    return '<article class="fund-card" style="border-top-color:' + color + '">' +
      '<div class="fund-head"><div class="fund-title">' +
      '<span class="dot" style="background:' + color + '"></span>' +
      esc(fund.name) + '<span class="code">' + esc(fund.code) + "</span>" +
      '<div class="small">' + esc(fund.nav_source || "") + " ｜ " + score + "</div></div>" +
      '<span class="badge" style="background:' + actionColor(action) + '">' + badgeText + "</span></div>" +
      '<div class="fund-kv">' +
      "<div>组合占比<b>" + (Number(fund.weight || 0) * 100).toFixed(1) + "%</b>" +
      "<small>目标 " + (Number(fund.target_weight || 0) * 100).toFixed(0) + "%</small></div>" +
      "<div>当前市值<b>" + money(fund.market_value) + " 元</b>" +
      "<small>" + Number(fund.shares || 0).toFixed(2) + " 份</small></div>" +
      '<div>累计收益<b class="' + clsFor(fund.profit) + '">' + money(fund.profit) + " 元</b>" +
      '<small class="' + clsFor(fund.profit_pct) + '">' + pct(fund.profit_pct) + "</small></div>" +
      "<div>今日估值<b class=\"" + clsFor(fund.est_chg) + "\">" + pct(fund.est_chg) + "</b>" +
      "<small>成本 " + Number(fund.cost || 0).toFixed(4) + "</small></div>" +
      "</div>" +
      (reasons ? '<ul class="reasons">' + reasons + "</ul>" : "") +
      '<div class="fund-actions">' +
      '<button type="button" data-edit="' + esc(fund.code) + '">修改持仓/收益</button>' +
      '<button type="button" class="danger" data-del="' + esc(fund.code) + '">删除</button>' +
      "</div></article>";
  }).join("");

  list.querySelectorAll("[data-edit]").forEach((btn) => {
    btn.addEventListener("click", () => openModal(findFund(btn.dataset.edit)));
  });
  list.querySelectorAll("[data-del]").forEach((btn) => {
    btn.addEventListener("click", () => deleteFund(btn.dataset.del));
  });
}

function findFund(code) {
  return (state.funds || []).find((fund) => String(fund.code) === String(code)) || null;
}

/* ------------------------------------------------------------------- modal */
function fillBenchmarkOptions(selected) {
  const select = $("fBenchmark");
  select.innerHTML = "";
  for (const item of (state && state.index_options) || []) {
    const option = document.createElement("option");
    option.value = item.code;
    option.textContent = item.name + (item.code ? "（" + item.code + "）" : "");
    select.appendChild(option);
  }
  if (selected && !Array.from(select.options).some((opt) => opt.value === selected)) {
    const option = document.createElement("option");
    option.value = selected;
    option.textContent = selected;
    select.appendChild(option);
  }
  select.value = selected || "";
}

function openModal(fund) {
  if (!state) return;
  const isEdit = Boolean(fund);
  $("modalTitle").textContent = isEdit ? "修改持仓 / 收益" : "添加基金";
  $("editCode").value = isEdit ? fund.code : "";
  $("fCode").value = isEdit ? fund.code : "";
  $("fCode").disabled = isEdit;
  $("fName").value = isEdit ? fund.name : "";
  $("fKind").value = isEdit ? (fund.kind || "a_share") : "a_share";
  fillBenchmarkOptions(isEdit ? (fund.benchmark || "") : "sh000688");
  $("fShares").value = isEdit ? Number(fund.shares || 0).toFixed(2) : "";
  $("fCost").value = isEdit ? Number(fund.cost || 0) : "";
  $("fValue").value = isEdit ? Number(fund.market_value || 0).toFixed(2) : "";
  $("fProfit").value = isEdit ? Number(fund.profit || 0).toFixed(2) : "0";
  $("fBudget").value = isEdit ? Math.round(Number(fund.add_budget || 200)) : 200;
  const defaultTarget = isEdit
    ? Math.round(Number(fund.target_weight || 0) * 100)
    : Math.max(1, Math.round(100 / Math.max(1, (state.funds || []).length + 1)));
  $("fTarget").value = defaultTarget;
  $("navHint").textContent = isEdit
    ? "当前净值 " + Number(fund.nav || 0).toFixed(4) + "（" + (fund.nav_source || "") + "）"
    : "填写代码后会自动查询名称；留空名称也可以保存。";
  document.querySelector('input[name="inputMode"][value="shares"]').checked = true;
  syncMode();
  $("modal").classList.remove("hidden");
  if (!isEdit) setTimeout(() => $("fCode").focus(), 30);
}

function closeModal() {
  $("modal").classList.add("hidden");
  $("fundForm").reset();
  $("editCode").value = "";
  $("fCode").disabled = false;
}

function syncMode() {
  const mode = document.querySelector('input[name="inputMode"]:checked').value;
  const sharesMode = mode === "shares";
  $("sharesFields").classList.toggle("hidden", !sharesMode);
  $("valueFields").classList.toggle("hidden", sharesMode);
  $("fCost").required = sharesMode;
  $("fShares").required = sharesMode;
  $("fValue").required = !sharesMode;
}

/* ------------------------------------------------------------------- lookup */
async function lookupCode() {
  const code = $("fCode").value.trim();
  if (!/^\d{6}$/.test(code) || $("editCode").value) return;
  try {
    const data = await api("/api/lookup?code=" + encodeURIComponent(code));
    const fund = data.fund || {};
    if (fund.name && fund.name !== code) $("fName").value = fund.name;
    if (fund.kind) $("fKind").value = fund.kind;
    if (fund.benchmark !== undefined) fillBenchmarkOptions(fund.benchmark || "");
    const nav = fund.nav ? Number(fund.nav).toFixed(4) : "--";
    $("navHint").textContent = "最新净值 " + nav +
      (fund.nav_date ? "（" + fund.nav_date + "）" : "") +
      (fund.exists ? " ｜ 已在持仓中" : "");
  } catch (err) {
    $("navHint").textContent = "未查到该基金，仍可手动填写名称和成本价。";
  }
}

/* -------------------------------------------------------------------- save */
async function submitFund(event) {
  event.preventDefault();
  if (busy) return;
  const code = $("fCode").value.trim();
  if (!/^\d{6}$/.test(code)) {
    toast("基金代码应为 6 位数字");
    return;
  }
  const mode = document.querySelector('input[name="inputMode"]:checked').value;
  const payload = {
    code,
    name: $("fName").value.trim(),
    kind: $("fKind").value,
    benchmark: $("fBenchmark").value,
    add_budget: Number($("fBudget").value || 200),
    target_weight: Number($("fTarget").value || 0) / 100,
  };
  if (mode === "shares") {
    payload.cost = Number($("fCost").value || 0);
    payload.shares = Number($("fShares").value || 0);
    if (payload.shares <= 0) { toast("请填写持有份额"); return; }
    if (payload.cost <= 0) { toast("请填写成本价"); return; }
  } else {
    payload.cost = 0;
    payload.value = Number($("fValue").value || 0);
    payload.profit = Number($("fProfit").value || 0);
    if (payload.value <= 0) { toast("请填写当前市值"); return; }
  }
  const editCode = $("editCode").value;
  const btn = event.submitter || $("fundForm").querySelector('button[type="submit"]');
  btn.disabled = true;
  busy = true;
  try {
    const data = await api(editCode ? "/api/funds/" + encodeURIComponent(editCode) : "/api/funds", {
      method: editCode ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state = data.state;
    render();
    closeModal();
    toast(editCode ? "已更新持仓" : "已添加基金");
  } catch (err) {
    toast("保存失败：" + err.message, 4200);
  } finally {
    btn.disabled = false;
    busy = false;
  }
}

async function deleteFund(code) {
  const fund = findFund(code);
  if (!fund) return;
  if (!confirm("确定删除 " + fund.name + "（" + code + "）？\n只删除本地持仓记录，不会赎回基金。")) return;
  try {
    const data = await api("/api/funds/" + encodeURIComponent(code), { method: "DELETE" });
    state = data.state;
    render();
    toast("已删除 " + code);
  } catch (err) {
    toast("删除失败：" + err.message);
  }
}

/* ------------------------------------------------------------------ refresh */
async function refreshData() {
  if (busy) return;
  busy = true;
  const btn = $("refreshBtn");
  btn.disabled = true;
  btn.textContent = "刷新中…";
  try {
    const res = await fetch("/api/refresh", { method: "POST" });
    let data = null;
    try { data = await res.json(); } catch (err) { data = null; }
    if (data && data.state) {
      state = data.state;
      render();
    }
    if (res.ok && data && data.ok) {
      toast("行情已刷新");
    } else {
      const tail = (data && data.output ? data.output.split("\n").filter(Boolean).slice(-2).join("；") : "");
      toast("刷新失败：" + ((data && data.error) || tail || res.status), 4500);
    }
  } catch (err) {
    toast("刷新失败：" + err.message, 4200);
  } finally {
    busy = false;
    btn.disabled = false;
    btn.textContent = "刷新行情";
  }
}

/* -------------------------------------------------------------------- wire */
function wire() {
  $("addBtn").addEventListener("click", () => openModal(null));
  $("refreshBtn").addEventListener("click", refreshData);
  $("closeModal").addEventListener("click", closeModal);
  $("cancelBtn").addEventListener("click", closeModal);
  $("modal").addEventListener("click", (event) => {
    if (event.target === $("modal")) closeModal();
  });
  $("closeTargetModal").addEventListener("click", closeTargetModal);
  $("cancelTargetBtn").addEventListener("click", closeTargetModal);
  $("applyTargetBtn").addEventListener("click", applyTargets);
  $("targetModal").addEventListener("click", (event) => {
    if (event.target === $("targetModal")) closeTargetModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!$("targetModal").classList.contains("hidden")) closeTargetModal();
    else if (!$("modal").classList.contains("hidden")) closeModal();
  });
  $("fundForm").addEventListener("submit", submitFund);
  document.querySelectorAll('input[name="inputMode"]').forEach((input) => {
    input.addEventListener("change", syncMode);
  });
  $("fCode").addEventListener("blur", lookupCode);
  $("fKind").addEventListener("change", () => {
    if ($("fKind").value === "qdii") {
      const name = $("fName").value || "";
      if (/纳斯达克|纳指|美国|标普/.test(name)) fillBenchmarkOptions("NDX");
      else fillBenchmarkOptions("");
    } else if ($("fBenchmark").value === "NDX") {
      fillBenchmarkOptions("");
    }
  });
  setInterval(() => {
    if ($("modal").classList.contains("hidden") && !busy) loadState();
  }, 60000);
}

wire();
loadState();
