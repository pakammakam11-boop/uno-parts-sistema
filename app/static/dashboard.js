/* Savininko apžvalga (/). Vanilla ES moduliai, be build žingsnio. API: app/dashboard_api.py (/api/dash/*).
   Prisijungimas per HR modulio sesiją: /api/hr/me (CSRF), /api/dash/access (ar mato apžvalgą), demo personos. */
import { makeTip, salesChart } from "./dashboard-charts.js";
import * as V from "./dashboard-view.js";

const { $, $$, esc } = V;
const HR = "/api/hr", DASH = "/api/dash";
const PERIODS = ["day", "week", "month", "quarter"], BRANCHES = ["all", "vln", "kns"];
const DIRECTOR_ID = 1; // tik prisijungimo ekrano grupavimui; teisę tikrina /api/dash/access

const state = {
  csrf: "",
  period: "month", branch: "all",
  seq: 0,                 // senas atsakymas neperpiešia naujo filtro
  costPath: [],           // [] | [{key,label}] | [{key,label},{key,label}]
  costSeq: 0,
  empSort: "profit", prio: "all",
  work: null, eff: null, series: [],
};
const tip = makeTip($("#chart-tip"));

class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }

async function api(url, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  if (method !== "GET") { if (state.csrf) headers["X-CSRF-Token"] = state.csrf; if (body !== undefined) headers["Content-Type"] = "application/json"; }
  let res;
  try { res = await fetch(url, { method, headers, credentials: "same-origin", body: body === undefined ? undefined : JSON.stringify(body) }); }
  catch (_) { throw new ApiError("Nepavyko susisiekti su serveriu. Patikrinkite ryšį.", 0); }
  if (!res.ok) {
    let msg = `Klaida ${res.status}`;
    try { const d = (await res.json()).detail; if (typeof d === "string") msg = d; } catch (_) { /* ne JSON */ }
    throw new ApiError(msg, res.status);
  }
  return res.json();
}
const qs = () => `period=${state.period}&branch=${state.branch}`;

/** Dashboard užklausa: 401 -> prisijungimas, 403 -> /hr (ne direktorius ir ne buhalterė). */
async function dash(path) {
  try { return await api(DASH + path); }
  catch (e) {
    if (e.status === 401) showLogin();
    if (e.status === 403) location.replace("/hr");
    throw e;
  }
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = `toast on${err ? " err" : ""}`;
  clearTimeout(toast.t); toast.t = setTimeout(() => { t.className = "toast"; }, 3200);
}

/* ---------- Prisijungimas ---------- */
const canSeeDash = (p) => p.id === DIRECTOR_ID || p.roles.includes("hr");
const initials = (n) => String(n || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();

async function showLogin() {
  $("#boot").hidden = true; $("#app-view").hidden = true; $("#login-view").hidden = false;
  const box = $("#personas");
  box.innerHTML = `<div class="panel-pad">${V.skeleton(3, 40)}</div>`;
  let people;
  try { people = await api(`${HR}/demo/personas`); }
  catch (e) { box.innerHTML = `<div class="panel-pad">${V.errorBox(e)}</div>`; box.querySelector("[data-retry]")?.addEventListener("click", showLogin); return; }
  const groups = [["Mato apžvalgą", people.filter(canSeeDash)], ["Kiti darbuotojai, pateks į Personalą", people.filter((p) => !canSeeDash(p))]].filter(([, ps]) => ps.length);
  box.innerHTML = groups.map(([title, ps]) => `<div class="persona-group"><h3>${esc(title)}</h3><ul>${ps.map((p) => `
    <li><button type="button" class="persona" data-persona="${esc(p.id)}" data-testid="persona">
      <span class="avatar" aria-hidden="true">${esc(initials(p.name))}</span>
      <span><b>${esc(p.name)}</b><small>${esc(p.position)}</small></span>
      <span class="roles">${canSeeDash(p) ? `<span class="role hr">Apžvalga</span>` : ""}</span>
    </button></li>`).join("")}</ul></div>`).join("");
}

$("#personas").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-persona]");
  if (!btn || btn.getAttribute("aria-busy") === "true") return;
  btn.setAttribute("aria-busy", "true");
  try {
    await api(`${HR}/demo/login`, { method: "POST", body: { employee_id: Number(btn.dataset.persona) } });
    location.replace("/");
  } catch (e) { toast(e.message, true); btn.removeAttribute("aria-busy"); }
});

$("#logout").addEventListener("click", async () => {
  try { await api(`${HR}/logout`, { method: "POST" }); } catch (_) { /* vis tiek rodome prisijungimą */ }
  state.csrf = "";
  history.replaceState(null, "", "/");
  showLogin();
});

/* ---------- Filtrai ir URL ---------- */
function readUrl() {
  const p = new URLSearchParams(location.search);
  if (PERIODS.includes(p.get("period"))) state.period = p.get("period");
  if (BRANCHES.includes(p.get("branch"))) state.branch = p.get("branch");
  $(`#period input[value="${state.period}"]`).checked = true;
  $(`#branch input[value="${state.branch}"]`).checked = true;
}
function writeUrl() {
  const p = new URLSearchParams({ period: state.period, branch: state.branch });
  history.replaceState(null, "", `/?${p}${location.hash}`);
}
for (const id of ["#period", "#branch"]) {
  $(id).addEventListener("change", (ev) => {
    if (id === "#period") state.period = ev.target.value; else state.branch = ev.target.value;
    writeUrl();
    state.costPath = [];   // kitas laikotarpis ar filialas: tiekėjo ar dokumentų lygis gali neturėti duomenų
    loadFiltered();
  });
}

/* ---------- Įkėlimas ---------- */
function busy(on) { for (const id of ["#kpis", "#kastai", "#sales-chart", "#budget-body", "#eff-figs", "#emp-table", "#man-table", "#br-body", "#ops-body"]) $(id).setAttribute("aria-busy", String(on)); }

function settle(promise, onOk, host, retry) {
  return promise.then(onOk).catch((e) => {
    if (e.status === 401 || e.status === 403) return;
    host.innerHTML = /^(UL|OL)$/.test(host.tagName) ? `<li class="sk-wrap">${V.errorBox(e)}</li>` : V.errorBox(e);
    host.querySelector("[data-retry]")?.addEventListener("click", retry);
  });
}

async function loadFiltered() {
  const seq = ++state.seq;
  busy(true);
  const live = () => seq === state.seq;
  const q = qs();
  await Promise.all([
    settle(dash(`/summary?${q}`), (d) => live() && renderSummary(d), $("#kpis"), loadFiltered),
    settle(loadCosts(), () => {}, $("#costs-body"), () => loadCosts()),
    settle(dash(`/sales/series?${q}`), (d) => live() && renderSeries(d), $("#sales-chart"), loadFiltered),
    settle(dash(`/efficiency?${q}`), (d) => live() && renderEfficiency(d), $("#eff-figs"), loadFiltered),
    settle(dash(`/operations?${q}`), (d) => live() && renderOperations(d), $("#ops-body"), loadFiltered),
  ]);
  if (live()) busy(false);
}

function renderSummary(d) {
  $("#kpis").innerHTML = V.renderKpis(d);
  $("#range-line").innerHTML = `${esc(V.rangeText(state.period, d.range))}<span class="range-asof">Duomenys iki ${esc(V.asOf(d.as_of))}</span>`;
  $("#actions").innerHTML = V.renderActions(d.actions);
  $("#actions-meta").textContent = d.actions.length ? `${d.actions.length} veiksmai` : "";
  $("#budget-body").innerHTML = V.renderBudget(d.budget_by_branch);
  $("#budget-meta").innerHTML = V.meta({ ...d, source: d.kpis.find((k) => k.key === "sales").source });
  $("#budget-sub").textContent = `Mėnesio biudžetas: faktas iki vakar ir prognozė iki ${d.forecast.month} pabaigos`;
  $("#costs-note").innerHTML = d.costs_note ? `<i class="ph ph-info" aria-hidden="true"></i>${esc(d.costs_note)}` : "";
  $("#costs-note").hidden = !d.costs_note;
}

$("#kpis").addEventListener("click", (ev) => {
  const b = ev.target.closest("#how-btn");
  if (!b) return;
  const open = b.getAttribute("aria-expanded") !== "true";
  b.setAttribute("aria-expanded", String(open));
  $("#how").hidden = !open;
});

/* Veiksmas: vidinė nuoroda (#kastai/<grupė>, #operacijos) nuveda į skiltį, /hr veikia kaip įprasta nuoroda. */
$("#actions").addEventListener("click", (ev) => {
  const a = ev.target.closest("a.act");
  if (!a || !a.dataset.link.startsWith("#")) return;
  ev.preventDefault();
  goTo(a.dataset.link);
});
function goTo(hash) {
  const [sec, group] = hash.slice(1).split("/");
  const target = document.getElementById(sec);
  if (!target) return;
  history.replaceState(null, "", `${location.pathname}${location.search}#${sec}`);
  if (sec === "kastai" && group) openGroup(group);
  target.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  target.classList.remove("flash"); void target.offsetWidth; target.classList.add("flash");
  target.setAttribute("tabindex", "-1"); target.focus({ preventScroll: true });
}

/* ---------- Kaštai ---------- */
function costsUrl() {
  const p = state.costPath.map((x) => encodeURIComponent(x.key)).join("/");
  return `/costs${p ? `/${p}` : ""}?${qs()}`;
}
async function loadCosts() {
  const seq = ++state.costSeq;
  const d = await dash(costsUrl());
  if (seq !== state.costSeq) return;
  const lvl = state.costPath.length, body = $("#costs-body");
  if (lvl === 0) {
    body.innerHTML = V.renderCostGroups(d);
    $("#costs-sub").textContent = "Pokytis lyginant su ankstesniu laikotarpiu. Paspauskite grupę.";
  } else if (lvl === 1) {
    state.costPath[0].label = d.group.label;
    body.innerHTML = V.renderSuppliers(d);
    $("#costs-sub").textContent = "Tiekėjai šioje grupėje. Paspauskite tiekėją, kad matytumėte dokumentus.";
  } else {
    state.costPath[1].label = d.supplier.name;
    body.innerHTML = V.renderDocuments(d);
    $("#costs-sub").textContent = "Dokumentai, iš kurių sudaryta suma.";
  }
  $("#costs-meta").innerHTML = V.meta(d);
  renderCrumbs();
  body.classList.remove("enter"); void body.offsetWidth; body.classList.add("enter");
  $("#kastai").setAttribute("aria-busy", "false");
}
function renderCrumbs() {
  const c = $("#costs-crumbs");
  if (!state.costPath.length) { c.hidden = true; c.innerHTML = ""; return; }
  c.hidden = false;
  const parts = [`<button type="button" class="crumb" data-depth="0">Visi kaštai</button>`]
    .concat(state.costPath.map((p, i) => (i === state.costPath.length - 1
      ? `<span class="crumb cur" aria-current="location">${esc(p.label)}</span>`
      : `<button type="button" class="crumb" data-depth="${i + 1}">${esc(p.label)}</button>`)));
  c.innerHTML = `<button type="button" class="btn small ghost back-btn" data-depth="${state.costPath.length - 1}" data-testid="costs-back"><i class="ph ph-arrow-left" aria-hidden="true"></i>Atgal</button><span class="crumb-trail">${parts.join(`<i class="ph ph-caret-right sep" aria-hidden="true"></i>`)}</span>`;
}
function openGroup(key) { state.costPath = [{ key, label: key }]; costsReload(); }
function costsReload() {
  $("#kastai").setAttribute("aria-busy", "true");
  settle(loadCosts(), () => {}, $("#costs-body"), costsReload);
}
$("#costs-body").addEventListener("click", (ev) => {
  const row = ev.target.closest(".cost-row");
  if (!row) return;
  if (row.dataset.group) state.costPath = [{ key: row.dataset.group, label: row.dataset.label }];
  else state.costPath = [state.costPath[0], { key: row.dataset.supplier, label: row.dataset.label }];
  costsReload();
});
$("#costs-crumbs").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-depth]");
  if (!b) return;
  state.costPath = state.costPath.slice(0, Number(b.dataset.depth));
  costsReload();
  $("#costs-title").scrollIntoView({ block: "nearest" });
});

/* ---------- Pardavimai ---------- */
function renderSeries(d) {
  state.series = d.points;
  drawSeries();
  $("#sales-meta").innerHTML = V.meta(d);
  $("#sales-sub").textContent = d.forecast_month
    ? `Faktas iki vakar ir prognozė iki ${d.forecast_month.month} pabaigos`
    : "Faktas pasirinktame laikotarpyje";
}
function drawSeries() {
  const host = $("#sales-chart");
  salesChart(host, state.series, { fmt: V.eur, fmtTick: V.tickEur, fmtDay: V.fmtDay, tip });
  host.insertAdjacentHTML("beforeend", V.salesLegend() + V.seriesTable(state.series));
}
let rz;
window.addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => { if (state.series.length) drawSeries(); }, 150); });

/* ---------- Efektyvumas ---------- */
function renderEfficiency(d) {
  state.eff = d;
  $("#eff-figs").innerHTML = V.renderEffFigs(d) + `<div class="fig-meta">${V.meta(d)}</div>${V.renderNotes(d.notes)}`;
  drawEmployees();
  $("#man-table").innerHTML = V.renderManufacturers(d.manufacturers);
  $("#br-body").innerHTML = V.renderBranches(d.branches);
  for (const id of ["#emp-meta", "#man-meta", "#br-meta"]) $(id).innerHTML = V.meta(d);
}
function drawEmployees() { $("#emp-table").innerHTML = V.renderEmployees(state.eff.employees, state.empSort); }
$("#emp-table").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-sort]");
  if (!b || !state.eff) return;
  state.empSort = b.dataset.sort;
  drawEmployees();
  $(`#emp-table [data-sort="${state.empSort}"]`)?.focus();
});

/* ---------- Darbai (nepriklauso nuo filtrų) ---------- */
async function loadWork() {
  const d = await dash("/work");
  state.work = d;
  drawEmails();
  $("#task-list").innerHTML = V.renderTasks(d.tasks);
  $("#mail-meta").innerHTML = V.meta(d);
  $("#task-meta").innerHTML = V.meta(d);
}
function drawEmails() { $("#mail-list").innerHTML = V.renderEmails(state.work.emails, state.prio); }
$("#mail-filter").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-prio]");
  if (!b || !state.work) return;
  state.prio = b.dataset.prio;
  $$("#mail-filter [data-prio]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  drawEmails();
});

/* ---------- Operacijos, šaltiniai ---------- */
function renderOperations(d) { $("#ops-body").innerHTML = V.renderOps(d); }
async function loadSources() {
  const d = await dash("/sources");
  $("#src-list").innerHTML = V.renderSources(d.sources);
  $("#src-meta").innerHTML = V.meta(d);
}

/* ---------- Paleidimas ---------- */
function skeletons() {
  $("#kpis").innerHTML = Array.from({ length: 5 }, () => `<div class="kpi-cell">${V.skeleton(1, 12)}${V.skeleton(1, 28)}${V.skeleton(1, 10)}</div>`).join("");
  for (const id of ["#actions", "#mail-list", "#task-list", "#src-list"]) $(id).innerHTML = `<li class="sk-wrap">${V.skeleton(3, 16)}</li>`;
  for (const id of ["#costs-body", "#sales-chart", "#budget-body", "#eff-figs", "#ops-body"]) $(id).innerHTML = `<div class="sk-wrap">${V.skeleton(4, 16)}</div>`;
}

async function boot() {
  let me, access;
  try {
    me = await api(`${HR}/me`);
    access = await api(`${DASH}/access`);
  } catch (e) {
    if (e.status === 401) { showLogin(); return; }
    $("#boot").innerHTML = V.errorBox(e);
    $("#boot [data-retry]")?.addEventListener("click", () => location.reload());
    return;
  }
  if (!access.allowed) { location.replace("/hr"); return; }
  state.csrf = me.csrf;
  $("#who-name").textContent = me.employee.name;
  $("#who-role").textContent = me.employee.position;
  readUrl();
  skeletons();
  $("#boot").hidden = true; $("#login-view").hidden = true; $("#app-view").hidden = false;
  await Promise.all([
    loadFiltered(),
    settle(loadWork(), () => {}, $("#mail-list"), () => settle(loadWork(), () => {}, $("#mail-list"))),
    settle(loadSources(), () => {}, $("#src-list"), () => settle(loadSources(), () => {}, $("#src-list"))),
  ]);
  if (location.hash.length > 1) goTo(location.hash);
  document.body.dataset.ready = "1";
}
boot();
