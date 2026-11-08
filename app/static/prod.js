"use strict";

const KINDS = [
  { kind: "employees", label: "Darbuotojų sąrašas", accept: ".xlsx", required: true, hint: "Tab. Nr., sutarta neto, norma, Rivilės kodas" },
  { kind: "timesheet", label: "Tabelis", accept: ".xlsx", required: true, hint: "Mėnesio darbo laiko žiniaraštis" },
  { kind: "rivile_hours", label: "Rivilės valandos", accept: ".xlsx", hint: "Sutikrinimui su Rivile" },
  { kind: "sick_certs", label: "Sodros nedarbingumai", accept: ".csv", hint: "Ligos dienų patikrai" },
  { kind: "vacation_requests", label: "Atostogų prašymai", accept: ".csv", hint: "Atostogų patikrai" },
  { kind: "rivile_payroll", label: "Rivilės DU žiniaraštis", accept: ".xlsx", hint: "4 žingsniui, po DU skaičiavimo Rivilėje" },
];
const SEV_ICON = { error: "ph-warning-octagon", warning: "ph-warning", info: "ph-info" };
const SEV_LABEL = { error: "Klaidos", warning: "Įspėjimai", info: "Info" };
const FIELD_LT = { gross: "Bruto", npd: "NPD", gpm: "GPM", sodra_employee: "Sodra darbuotojo", sodra_employer: "Sodra darbdavio", net: "Neto" };

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const eur = (v) => {
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString("lt-LT", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " €" : "";
};
let csrf = "";
let role = "";
// S1: konteksto versija. Atsakymas, grįžęs po paskyros ar laikotarpio pakeitimo, neberodomas.
let ctx = 0;
const isCurrentCtx = (mine) => mine === ctx;
// N2/K1: kurio skaičiavimo rezultatas šiuo metu rodomas ekrane (pagal modulį)
const shownRun = {};

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (opts.method && opts.method !== "GET") headers["X-CSRF-Token"] = csrf;
  const res = await fetch(path, { ...opts, headers, credentials: "same-origin" });
  if (res.status === 401 && path !== "/api/login") { showLogin(); throw new Error("Reikia prisijungti"); }
  if (!res.ok) {
    let msg = `Klaida ${res.status}`;
    try {
      const d = (await res.json()).detail;
      msg = Array.isArray(d) ? d.map((x) => x.msg || x).join("; ") : (d?.errors ? d.errors.join("; ") : d || msg);
    } catch (_) { /* ne JSON */ }
    throw new Error(msg);
  }
  return res.json();
}

function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast on" + (isErr ? " err" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = "toast"), 3600);
}

const period = () => $("#period").value;

function defaultPeriod() {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1); // uždaromas praėjęs mėnuo
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

/* ---------- Prisijungimas ---------- */
function clearResults() {
  ctx += 1;
  delete shownRun.timesheet;
  delete shownRun.payroll_compare;
  $("#check-stale").hidden = true;
  $("#compare-stale").hidden = true;
  // S1: kitos paskyros ar laikotarpio duomenys neturi likti ekrane
  for (const id of ["#check-kpis", "#check-findings", "#check-unsupported", "#check-expected",
    "#compare-totals", "#compare-findings", "#compare-review", "#audit", "#uploads", "#approve-status"]) {
    $(id).innerHTML = "";
  }
  ["files", "check", "approve", "compare", "audit", "gama"].forEach((k) => setBadge(k, "", ""));
  state = null;
  clearGama();
}

function showLogin() {
  clearResults();
  $("#app-view").hidden = true;
  $("#login-view").hidden = false;
  $("#login-user").focus();
}

async function showApp(me) {
  csrf = me.csrf;
  role = me.role;
  $("#who").textContent = `${me.username} (${me.role === "accountant" ? "buhalterė" : me.role})`;
  $("#login-view").hidden = true;
  $("#app-view").hidden = false;
  if (!$("#period").value) $("#period").value = defaultPeriod();
  $("#gama-month").value = period();
  gamaPermissions();
  await refresh();
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-error").textContent = "";
  try {
    const me = await api("/api/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: $("#login-user").value, password: $("#login-pass").value }),
    });
    $("#login-pass").value = "";
    await showApp(me);
  } catch (err) {
    $("#login-error").textContent = err.message;
  }
});

$("#logout").addEventListener("click", async () => {
  try { await api("/api/logout", { method: "POST" }); } catch (_) { /* vis tiek atsijungiam */ }
  csrf = "";
  showLogin();
});

/* ---------- Navigacija ---------- */
function show(view) {
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("on", v.id === `view-${view}`));
  document.querySelectorAll(".steps button").forEach((b) =>
    b.dataset.view === view ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current"));
  if (view === "audit") loadAudit();
  if (view === "gama") loadGamaRuns(true);
}
document.querySelectorAll(".steps button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
$("#period").addEventListener("change", () => {
  clearResults();
  $("#gama-month").value = period();
  if ($("#view-gama").classList.contains("on")) loadGamaRuns(true);
  refresh().catch((err) => toast(err.message, true));
});

/* ---------- Būsena ---------- */
let state = null;

async function refresh() {
  if (!period()) return;
  const requested = period();
  const mine = ctx;
  const fresh = await api(`/api/v1/periods/${requested}/state`);
  if (requested !== period() || !isCurrentCtx(mine)) return; // kontekstas pasikeitė, kol laukėme
  state = fresh;
  renderUploads(state.uploads);
  renderApproval(state.approval);
  setBadge("files", ["employees", "timesheet"].every((k) => state.uploads[k].count) ? "OK" : "", "ok");
  const run = state.runs.timesheet;
  setBadge("check", run ? String((run.counts?.error || 0) + (run.counts?.warning || 0) || "OK") : "",
    run && (run.counts?.error || 0) ? "err" : run && (run.counts?.warning || 0) ? "warn" : "ok");
  setBadge("approve", state.approval.is_approved ? "OK" : run ? "!" : "", state.approval.is_approved ? "ok" : "warn");
  await loadSavedRuns(requested, mine);
}

// S11: perkrovus puslapį atkuriame paskutinius išsaugotus rezultatus
async function loadSavedRuns(requested, mine) {
  const views = [["timesheet", renderCheck, "#check-stale"], ["payroll_compare", renderCompare, "#compare-stale"]];
  for (const [module, render, banner] of views) {
    if (!state?.runs[module]) continue;
    try {
      const saved = await api(`/api/v1/periods/${requested}/runs/${module}`);
      if (requested !== period() || !isCurrentCtx(mine)) return;
      shownRun[module] = saved.run_id;
      render(saved.result);
      showStale(banner, saved);
    } catch (_) { /* nėra išsaugoto rezultato */ }
  }
}

// K1: aktualumas tikrinamas būtent rodomam skaičiavimui (run_id), ne bet kuriam naujausiam
async function refreshFreshness(module, banner, mine, runId) {
  try {
    const saved = await api(`/api/v1/periods/${period()}/runs/${module}`);
    if (!isCurrentCtx(mine) || shownRun[module] !== runId) return; // ekrane jau kitas rezultatas
    if (saved.run_id !== runId) {
      showStale(banner, { is_current: false, stale_reasons: ["Yra naujesnis skaičiavimas: perkraukite puslapį"] });
    } else {
      showStale(banner, saved);
    }
  } catch (_) { /* nėra išsaugoto rezultato */ }
}

// N2: pasenęs rezultatas rodomas su įspėjimu, ne kaip galiojantis
function showStale(sel, saved) {
  const el = $(sel);
  const stale = saved && saved.is_current === false;
  el.hidden = !stale;
  el.innerHTML = stale
    ? `<i class="ph ph-warning"></i>Pasenęs rezultatas, paleiskite iš naujo. ${esc((saved.stale_reasons || []).join("; "))}`
    : "";
}

function setBadge(key, text, cls) {
  const el = document.querySelector(`[data-badge="${key}"]`);
  el.textContent = text;
  el.className = "badge " + (text ? cls : "");
}

function renderUploads(uploads) {
  $("#uploads").innerHTML = KINDS.map((k) => {
    const u = uploads[k.kind]?.latest;
    const icon = u ? "ph-check-circle" : k.required ? "ph-warning-circle" : "ph-circle-dashed";
    const meta = u ? `${esc(u.filename)}, ${esc(u.uploaded_by)}, ${esc(String(u.uploaded_at).slice(0, 16).replace("T", " "))}` : esc(k.hint);
    return `<li><i class="ph ${icon}"></i>
      <div><b>${esc(k.label)}${k.required ? '<span class="req">privaloma</span>' : ""}</b><small>${meta}</small></div>
      <label class="btn small file-btn"><i class="ph ph-upload-simple"></i>${u ? "Pakeisti" : "Įkelti"}
        <input type="file" accept="${k.accept}" data-kind="${k.kind}"></label></li>`;
  }).join("");
  document.querySelectorAll("[data-kind]").forEach((inp) => inp.addEventListener("change", () => upload(inp)));
}

async function upload(inp) {
  const file = inp.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    await api(`/api/v1/periods/${period()}/uploads/${inp.dataset.kind}`, { method: "POST", body: fd });
    ctx += 1; // N2: įvestys pasikeitė, vėluojantys patikros / palyginimo atsakymai nebegalioja
    toast(`Įkelta: ${file.name}`);
    await refresh();
  } catch (err) {
    toast(err.message, true);
  }
}

/* ---------- Patikra ---------- */
$("#run-check").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  $("#check-findings").innerHTML = `<div class="skeleton"></div><div class="skeleton" style="width:70%"></div>`;
  const mine = ctx;
  try {
    const r = await api(`/api/v1/periods/${period()}/check`, { method: "POST" });
    if (!isCurrentCtx(mine)) return;
    shownRun.timesheet = r.run_id;
    renderCheck(r);
    await refreshFreshness("timesheet", "#check-stale", mine, r.run_id);
    await refresh();
  } catch (err) {
    if (!isCurrentCtx(mine)) return;
    $("#check-findings").innerHTML = emptyHtml(err.message);
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});

function renderCheck(r) {
  const supported = Object.values(r.expected).filter((x) => x.supported).length;
  $("#check-kpis").innerHTML = [
    ["Darbuotojų", r.employees], ["Darbo dienų", r.workdays], ["Klaidos", r.counts.error],
    ["Įspėjimai", r.counts.warning], ["Paskaičiuota", `${supported} iš ${Object.keys(r.expected).length}`],
  ].map(([l, v]) => `<div class="kpi"><span>${esc(l)}</span><b>${esc(v)}</b></div>`).join("");
  $("#check-findings").innerHTML = findingsHtml(r.findings);
  bindFilters($("#check-findings"));
  $("#check-unsupported").innerHTML = r.unsupported.length
    ? `<ul class="review">${r.unsupported.map((u) => `<li><b>${esc(u.tab_no)} ${esc(u.name)}</b><small>${esc(u.reasons.join("; "))}</small></li>`).join("")}</ul>`
    : emptyHtml("Visi atvejai paskaičiuoti automatiškai.");
  const rows = Object.values(r.expected).filter((x) => x.supported);
  $("#check-expected").innerHTML = rows.length ? `<table class="data"><thead><tr>
      <th>Tab.</th><th>Darbuotojas</th><th class="num">Bruto</th><th class="num">GPM</th><th class="num">Sodra</th><th class="num">Neto</th></tr></thead><tbody>${
      rows.map((x) => `<tr><td>${esc(x.tab_no)}</td><td>${esc(x.name)}</td><td class="num">${eur(x.gross)}</td>
        <td class="num">${eur(x.gpm)}</td><td class="num">${eur(x.sodra_employee)}</td><td class="num">${eur(x.net)}</td></tr>`).join("")
    }</tbody></table>` : emptyHtml("Nėra paskaičiuotų darbuotojų.");
}

/* ---------- Patvirtinimas ---------- */
function renderApproval(a) {
  const ok = a.is_approved;
  $("#approve-status").innerHTML = ok
    ? `<div class="status-line ok"><i class="ph ph-seal-check"></i>Patvirtino ${esc(a.approved_by)}, ${esc(String(a.approved_at).slice(0, 16).replace("T", " "))}</div>`
    : `<div class="status-line no"><i class="ph ph-hourglass"></i>Nepatvirtinta. ${state?.runs.timesheet ? "Peržiūrėkite radinius ir patvirtinkite." : "Pirma paleiskite patikrą."}</div>`;
  $("#download-rivile").disabled = !ok;
}

$("#approve-btn").addEventListener("click", async () => {
  try {
    await api(`/api/v1/periods/${period()}/approve`, { method: "POST" });
    toast("Patikra patvirtinta.");
    await refresh();
  } catch (err) {
    toast(err.message, true);
  }
});

$("#download-rivile").addEventListener("click", async () => {
  const mine = ctx;
  try {
    const res = await fetch(`/api/v1/periods/${period()}/rivile-timesheet`, { credentials: "same-origin" });
    if (!res.ok) throw new Error((await res.json()).detail || `Klaida ${res.status}`);
    const name = (res.headers.get("Content-Disposition") || "").match(/filename="?([^";]+)/)?.[1] || `rivile_tabelis_${period()}.xlsx`;
    const blob = await res.blob();
    if (!isCurrentCtx(mine)) return; // kita paskyra ar laikotarpis: senos įmonės failo neatiduodam
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    toast(name.endsWith(".zip") ? `Atsisiųsta: ${name}. Viršvalandžiai atskirame faile, juos suveskite Rivilėje rankiniu būdu.` : `Atsisiųsta: ${name}`);
  } catch (err) {
    if (isCurrentCtx(mine)) toast(err.message, true);
  }
});

/* ---------- DU palyginimas ---------- */
$("#run-compare").addEventListener("click", async () => {
  const mine = ctx;
  try {
    const r = await api(`/api/v1/periods/${period()}/compare`, { method: "POST" });
    if (!isCurrentCtx(mine)) return;
    shownRun.payroll_compare = r.run_id;
    renderCompare(r);
    await refreshFreshness("payroll_compare", "#compare-stale", mine, r.run_id);
    if (!isCurrentCtx(mine)) return; // K2
    setBadge("compare", String((r.counts.error || 0) + (r.counts.warning || 0) || "OK"), r.counts.error ? "err" : r.counts.warning ? "warn" : "ok");
  } catch (err) {
    if (isCurrentCtx(mine)) toast(err.message, true);
  }
});

function totalCell(label, t) {
  const diff = Number(t.diff);
  return `<div class="total"><span>${esc(label)}</span><b>${eur(t.rivile)}</b>
    <em class="${Math.abs(diff) > 0.005 ? "bad" : "good"}">${Math.abs(diff) > 0.005 ? `skirtumas ${eur(diff)}` : "sutampa"}</em></div>`;
}

function fileCell(label, value, note) {
  return `<div class="total"><span>${esc(label)}</span><b>${eur(value)}</b><em>${esc(note)}</em></div>`;
}

function renderCompare(r) {
  // S8: deklaracijų sumos iš VISO Rivilės failo; „sutampa“ tik patikrintam pogrupiui
  const f = r.file_totals || {};
  const cov = r.coverage || {};
  $("#compare-totals").innerHTML = fileCell("GPM313 (visas Rivilės failas)", f.gpm313, `${f.rows ?? ""} eil.`)
    + fileCell("SAM (visas Rivilės failas)", f.sam, `${f.rows ?? ""} eil.`)
    + totalCell(`GPM, ${cov.label || "patikrinta dalis"}`, r.totals.gpm313)
    + totalCell(`Sodra, ${cov.label || "patikrinta dalis"}`, r.totals.sam)
    + (r.status_label ? `<div class="total" style="grid-column:1/-1"><span>Būsena</span><em class="${r.status === "sutampa" ? "good" : "bad"}">${esc(r.status_label)}</em></div>` : "");
  $("#compare-findings").innerHTML = findingsHtml(r.findings);
  bindFilters($("#compare-findings"));
  $("#compare-review").innerHTML = r.accountant_review.length
    ? `<ul class="review">${r.accountant_review.map((u) => `<li><b>${esc(u.tab_no)} ${esc(u.name)}</b><small>${esc(u.reasons.join("; "))}</small></li>`).join("")}</ul>`
    : emptyHtml("Visi darbuotojai palyginti.");
}

/* ---------- GAMA patikra ---------- */
const COMP_LT = { PSD: "PSD", VSD: "VSD", PENS: "Pensijų kaupimas", GPM_LIGA: "GPM nuo ligos išmokos", GPM: "GPM", ALGA: "Alga", PRIEDAI_LAIKAS: "Priedai ir laikas" };
const SEV_ONE = { error: "Klaida", warning: "Įspėjimas", info: "Info" };
const SEV_RANK = { error: 0, warning: 1, info: 2 };
let gamaShown = null;   // rodomos patikros run_id
let gamaTimer = null;
let gamaRunsSeq = 0;   // /runs sąrašo užklausų numeris: pavėlavęs senesnis atsakymas nerodomas
let gamaOpenSeq = 0;   // ankstesnių patikrų pasirinkimo eilės numeris: rodomas tik paskutinio pasirinkimo atsakymas

const gamaMonth = () => $("#gama-month").value;

function clearGama() {
  gamaShown = null;
  gamaOpenSeq += 1;
  gamaRunsSeq += 1;
  stopGamaProgress();
  $("#gama-result").hidden = true;
  $("#gama-error").hidden = true;
  for (const id of ["#gama-kpis", "#gama-components", "#gama-findings", "#gama-runs", "#gama-result-title"]) $(id).innerHTML = "";
}

function gamaPermissions() {
  const canRun = role === "accountant" || role === "admin";
  $("#gama-run").disabled = !canRun;
  $("#gama-run").title = canRun ? "" : "Paleisti gali tik buhalterė arba administratorius";
}

function startGamaProgress(refresh) {
  const started = Date.now();
  const text = refresh ? "Traukiami duomenys iš GAMA ir tikrinama" : "Tikrinama pagal paskutinius GAMA duomenis";
  const paint = () => {
    const sec = Math.round((Date.now() - started) / 1000);
    $("#gama-progress").innerHTML = `<i class="ph ph-circle-notch" aria-hidden="true"></i>${esc(text)}, ${sec} s. Tai gali užtrukti iki minutės.`;
  };
  paint();
  gamaTimer = setInterval(paint, 1000);
}

function stopGamaProgress() {
  clearInterval(gamaTimer);
  gamaTimer = null;
  $("#gama-progress").innerHTML = "";
}

function showGamaError(msg) {
  const el = $("#gama-error");
  el.innerHTML = msg ? `<i class="ph ph-warning-octagon" aria-hidden="true"></i>${esc(msg)}` : "";
  el.hidden = !msg;
}

$("#gama-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const month = gamaMonth();
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) { showGamaError("Pasirinkite mėnesį."); $("#gama-month").focus(); return; }
  const refresh = $("#gama-refresh").checked;
  const btn = $("#gama-run");
  const mine = ctx;
  btn.disabled = true;
  showGamaError("");
  startGamaProgress(refresh);
  try {
    const r = await api("/api/v1/gama/check", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ month, refresh }),
    });
    if (!isCurrentCtx(mine) || month !== gamaMonth()) return; // kitas mėnuo ar paskyra: senas atsakymas nerodomas
    gamaOpenSeq += 1; // šviežia patikra nugali dar nepasiekusius ankstesnių patikrų atsakymus
    renderGama(r);
    $("#gama-result-title").focus();
    toast(`Patikra baigta: klaidų ${r.summary.findings?.error || 0}, įspėjimų ${r.summary.findings?.warning || 0}.`);
    await loadGamaRuns(false);
  } catch (err) {
    if (isCurrentCtx(mine)) showGamaError(err.message);
  } finally {
    if (isCurrentCtx(mine)) stopGamaProgress();
    gamaPermissions();
  }
});

$("#gama-month").addEventListener("change", () => {
  ctx += 1; // vėluojantis kito mėnesio atsakymas nebegalioja
  clearGama();
  loadGamaRuns(true);
});

async function loadGamaRuns(showLatest) {
  const month = gamaMonth();
  if (!month) return;
  const mine = ctx;
  const seq = ++gamaRunsSeq;
  try {
    const r = await api(`/api/v1/gama/runs?month=${encodeURIComponent(month)}`);
    if (!isCurrentCtx(mine) || month !== gamaMonth() || seq !== gamaRunsSeq) return;
    renderGamaRuns(r.runs);
    if (showLatest && r.runs.length && gamaShown == null) await openGamaRun(r.runs[0].run_id);
  } catch (err) {
    if (isCurrentCtx(mine) && seq === gamaRunsSeq) $("#gama-runs").innerHTML = emptyHtml(err.message);
  }
}

async function openGamaRun(id) {
  const mine = ctx;
  const seq = ++gamaOpenSeq;
  try {
    const r = await api(`/api/v1/gama/runs/${id}`);
    if (!isCurrentCtx(mine) || seq !== gamaOpenSeq) return; // vėlavęs senesnio pasirinkimo atsakymas
    renderGama(r);
  } catch (err) {
    if (isCurrentCtx(mine)) toast(err.message, true);
  }
}

const fmtTime = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString("lt-LT", { dateStyle: "short", timeStyle: "short" });
};

function renderGamaRuns(runs) {
  $("#gama-runs").innerHTML = runs.length ? `<table class="data"><thead><tr><th>Laikas</th><th>Mėnuo</th><th>Paleido</th>
      <th class="num">Klaidos</th><th class="num">Įspėjimai</th><th>Rezultatas</th></tr></thead><tbody>${
    runs.map((x) => `<tr data-run="${x.run_id}"><td>${esc(fmtTime(x.created_at))}</td>
      <td>${esc(x.month)}</td><td>${esc(x.created_by)}</td><td class="num">${esc(x.summary.findings?.error ?? 0)}</td>
      <td class="num">${esc(x.summary.findings?.warning ?? 0)}</td>
      <td><button class="btn small" type="button" data-open-run="${x.run_id}">Rodyti</button></td></tr>`).join("")
  }</tbody></table>` : emptyHtml("Šiam mėnesiui patikrų dar nėra. Paspauskite „Tikrinti mėnesį GAMA“.");
  $("#gama-runs").querySelectorAll("[data-open-run]").forEach((b) => b.addEventListener("click", () => openGamaRun(Number(b.dataset.openRun))));
  markShownRun();
}

function markShownRun() {
  $("#gama-runs").querySelectorAll("tr[data-run]").forEach((tr) => {
    const on = Number(tr.dataset.run) === gamaShown;
    tr.classList.toggle("shown", on);
    const b = tr.querySelector("[data-open-run]");
    b.textContent = on ? "Rodoma" : "Rodyti";
    if (on) b.setAttribute("aria-current", "true"); else b.removeAttribute("aria-current");
  });
}

function renderGama(r) {
  gamaShown = r.run_id;
  const s = r.summary;
  const counts = s.findings || {};
  $("#gama-result").hidden = false;
  $("#gama-result-title").textContent = `${s.month} patikra, ${fmtTime(r.created_at)}, paleido ${r.created_by}`;
  $("#gama-kpis").innerHTML = [
    ["Darbuotojų", s.employees], ["Klaidos", counts.error ?? 0], ["Įspėjimai", counts.warning ?? 0],
    ["Info", counts.info ?? 0], ["Metinė 36 VDU patikra", s.annual_check || ""],
  ].map(([l, v]) => `<div class="kpi"><span>${esc(l)}</span><b>${esc(v)}</b></div>`).join("");
  $("#gama-components").innerHTML = Object.entries(s.components || {}).map(([comp, c]) => {
    const bad = (c.mismatch || 0) > 0;
    const label = `<span>${esc(COMP_LT[comp] || comp)}</span>`;
    if (!(c.ok || 0) && !bad) return `<div class="total muted">${label}<b>netikrinta</b><em>šį mėnesį nėra ką tikrinti</em></div>`;
    return `<div class="total">${label}<b>${esc(c.ok)} tvarkoje</b>
      <em class="${bad ? "bad" : "good"}">${bad ? `neatitikimų ${esc(c.mismatch)}` : "neatitikimų nėra"}</em></div>`;
  }).join("");
  $("#gama-findings").innerHTML = gamaFindingsHtml(r.findings || []);
  bindTableFilters($("#gama-findings"));
  setBadge("gama", String((counts.error || 0) + (counts.warning || 0) || "OK"), counts.error ? "err" : counts.warning ? "warn" : "ok");
  markShownRun();
}

function gamaFindingsHtml(list) {
  if (!list.length) return emptyHtml("Neatitikimų nerasta: visi tikrinti išskaitymai sutampa.");
  const rows = [...list].sort((a, b) => (SEV_RANK[a.severity] ?? 9) - (SEV_RANK[b.severity] ?? 9));
  const counts = {};
  rows.forEach((f) => (counts[f.severity] = (counts[f.severity] || 0) + 1));
  const chips = ["all", "error", "warning", "info"].filter((x) => x === "all" || counts[x]).map((x) =>
    `<button class="chip" type="button" data-filter="${x}" aria-pressed="${x === "all"}">${x === "all" ? `Visi ${rows.length}` : `${SEV_LABEL[x]} ${counts[x]}`}</button>`).join("");
  const money = (v) => (v === undefined || v === null || v === "" ? "" : eur(v));
  const body = rows.map((f) => {
    const e = f.extra || {};
    return `<tr class="row-${esc(f.severity)}" data-sev="${esc(f.severity)}">
      <td class="sev-cell"><span class="sev ${esc(f.severity)}"><i class="ph ${SEV_ICON[f.severity] || "ph-info"}" aria-hidden="true"></i>${esc(SEV_ONE[f.severity] || f.severity)}</span></td>
      <td class="mono" data-label="Tabelio nr.">${esc(f.ref === "*" ? "visi" : f.ref)}</td><td data-label="Komponentas">${esc(COMP_LT[e.component] || e.component || "")}</td>
      <td class="wrap desc"><b>${esc(f.title)}</b><small>${esc(f.detail)}</small></td>
      <td class="num" data-label="Tikėtina">${money(e.expected)}</td><td class="num" data-label="GAMA">${money(e.actual)}</td><td class="num" data-label="Skirtumas">${money(e.diff)}</td></tr>`;
  }).join("");
  return `<div class="filter" role="group" aria-label="Rodyti pagal svarbą">${chips}</div>
    <div class="table-wrap"><table class="data gama-table"><thead><tr><th>Svarba</th><th>Darbuotojas (tabelio nr.)</th><th>Komponentas</th>
    <th>Aprašymas</th><th class="num">Tikėtina</th><th class="num">GAMA</th><th class="num">Skirtumas</th></tr></thead><tbody>${body}</tbody></table></div>`;
}

function bindTableFilters(scope) {
  scope.querySelectorAll("[data-filter]").forEach((chip) => chip.addEventListener("click", () => {
    scope.querySelectorAll("[data-filter]").forEach((c) => c.setAttribute("aria-pressed", c === chip));
    scope.querySelectorAll("tbody tr").forEach((tr) => (tr.hidden = chip.dataset.filter !== "all" && tr.dataset.sev !== chip.dataset.filter));
  }));
}

$("#gama-xlsx").addEventListener("click", async () => {
  const mine = ctx;
  const id = gamaShown;
  if (id == null) return;
  try {
    const res = await fetch(`/api/v1/gama/runs/${id}/xlsx`, { credentials: "same-origin" });
    if (res.status === 401) { showLogin(); return; }
    if (!res.ok) throw new Error((await res.json()).detail || `Klaida ${res.status}`);
    const name = (res.headers.get("Content-Disposition") || "").match(/filename="?([^";]+)/)?.[1] || `gama_check_${gamaMonth()}.xlsx`;
    const blob = await res.blob();
    if (!isCurrentCtx(mine) || id !== gamaShown) return;
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    toast(`Atsisiųsta: ${name}`);
  } catch (err) {
    if (isCurrentCtx(mine)) toast(err.message, true);
  }
});

/* ---------- Žurnalas ---------- */
const ACTION_LT = { upload: "Įkėlė", run: "Paleido", approve: "Patvirtino", revoke: "Atšaukė", export: "Atsisiuntė" };

async function loadAudit() {
  try {
    const mine = ctx;
    const r = await api(`/api/v1/periods/${period()}/audit`);
    if (!isCurrentCtx(mine)) return;
    $("#audit").innerHTML = r.entries.length ? `<table class="data"><thead><tr><th>Laikas</th><th>Naudotojas</th><th>Veiksmas</th><th>Detalės</th></tr></thead><tbody>${
      r.entries.map((x) => `<tr><td>${esc(String(x.at).slice(0, 19).replace("T", " "))}</td><td>${esc(x.user)}</td>
        <td>${esc(ACTION_LT[x.action] || x.action)}</td><td class="wrap">${esc(x.detail)}</td></tr>`).join("")
    }</tbody></table>` : emptyHtml("Įrašų dar nėra.");
  } catch (err) {
    $("#audit").innerHTML = emptyHtml(err.message);
  }
}

/* ---------- Bendri ---------- */
function emptyHtml(text) {
  return `<div class="empty"><i class="ph ph-tray"></i>${esc(text)}</div>`;
}

function findingsHtml(list) {
  if (!list.length) return emptyHtml("Neatitikimų nerasta.");
  const counts = {};
  list.forEach((f) => (counts[f.severity] = (counts[f.severity] || 0) + 1));
  const chips = ["all", "error", "warning", "info"].filter((s) => s === "all" || counts[s]).map((s) =>
    `<button class="chip" data-filter="${s}" aria-pressed="${s === "all"}">${s === "all" ? `Visi ${list.length}` : `${SEV_LABEL[s]} ${counts[s]}`}</button>`).join("");
  const items = list.map((f) => `<li class="finding ${esc(f.severity)}" data-sev="${esc(f.severity)}">
      <i class="ph ${SEV_ICON[f.severity] || "ph-info"}"></i>
      <div><h3>${esc(f.title)}${f.ref ? `<span class="ref">${esc(f.ref)}</span>` : ""}</h3><p>${esc(f.detail)}</p>
      ${f.suggested_action ? `<details><summary>Siūlomas veiksmas</summary><pre>${esc(f.suggested_action)}</pre></details>` : ""}</div></li>`).join("");
  return `<div class="filter">${chips}</div><ul class="findings" style="max-height:520px;overflow:auto">${items}</ul>`;
}

function bindFilters(scope) {
  scope.querySelectorAll("[data-filter]").forEach((chip) => chip.addEventListener("click", () => {
    scope.querySelectorAll("[data-filter]").forEach((c) => c.setAttribute("aria-pressed", c === chip));
    scope.querySelectorAll(".finding").forEach((li) => (li.hidden = chip.dataset.filter !== "all" && li.dataset.sev !== chip.dataset.filter));
  }));
}

/* ---------- Start ---------- */
(async () => {
  try {
    const res = await fetch("/api/me", { credentials: "same-origin" });
    if (res.ok) await showApp(await res.json());
    else showLogin();
  } catch (_) {
    showLogin();
  }
})();
