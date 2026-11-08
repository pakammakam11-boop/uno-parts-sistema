"use strict";
/* Personalo prašymai (/hr). Vanilla JS, be bibliotekų. API: /api/hr (žr. .flow/2026-11-07-personalas-atostogos/SPEC.md).
   Atsakymų formos: core/hr/views.py. Skaidymas: hr.js (branduolys, prisijungimas, sąrašai), hr-form.js (naujas prašymas,
   detalės), hr-sign.js (pasirašymas, tabelis, laiškai, paleidimas). Klasikiniai skriptai dalijasi viena globalia sritimi. */

const API = "/api/hr";
const SIGN_DELAY_MS = 2000; // demo Smart-ID „patvirtinimas telefone“
const BACK_AFTER_SIGN_MS = 1600;

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const KIND_LT = { annual: "Kasmetinės atostogos", unpaid: "Nemokamos atostogos", study: "Mokymosi atostogos" };
const STATUS_LT = { pending_signatures: "Laukia parašų", approved: "Patvirtintas", rejected: "Atmestas", cancelled: "Atšauktas" };
const STATUS_CLS = { pending_signatures: "pending", approved: "approved", rejected: "rejected", cancelled: "cancelled" };
const ORDER_LT = { pending_order: "Įsakymas laukia parašo", order_signed: "Įsakymas pasirašytas", order_rejected: "Įsakymas atmestas" };
const ROLE_LT = { employee: "Darbuotojas", substitute: "Pavaduojantis", manager: "Vadovas" };
const AUDIT_LT = {
  created: "Prašymas pateiktas", document_frozen: "PDF užšaldytas", signed: "Pasirašė", declined: "Atsisakė",
  approved: "Prašymas patvirtintas", cancelled: "Prašymas atšauktas", balance_decreased: "Sumažintas likutis",
  timesheet_marked: "Pažymėta tabelyje", order_created: "Sugeneruotas įsakymas", order_signed: "Įsakymas pasirašytas",
  tamper_detected: "Aptiktas dokumento pakeitimas",
};

let me = null;          // {employee, roles, balance, demo}
let csrf = "";
let navSeq = 0;         // seniai užklausai neleidžiam perpiešti naujo ekrano
let afterLogin = "";
let wdSeq = 0;
let tsMonth = "";

class ApiError extends Error {
  constructor(msg, status) { super(msg); this.status = status; }
}

async function api(path, { method = "GET", body, allow401 = false } = {}) {
  const headers = { Accept: "application/json" };
  if (method !== "GET") {
    if (csrf) headers["X-CSRF-Token"] = csrf;
    if (body !== undefined) headers["Content-Type"] = "application/json";
  }
  let res;
  try {
    res = await fetch(API + path, { method, headers, credentials: "same-origin", body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (_) {
    throw new ApiError("Nepavyko susisiekti su serveriu. Patikrinkite ryšį ir bandykite dar kartą.", 0);
  }
  if (res.status === 401 && !allow401) { signedOut(); throw new ApiError("Sesija baigėsi. Prisijunkite iš naujo.", 401); }
  if (!res.ok) {
    let msg = `Klaida ${res.status}`;
    try {
      const d = (await res.json()).detail;
      if (Array.isArray(d)) msg = d.map((x) => x.msg || x).join("; ");
      else if (d) msg = typeof d === "string" ? d : (d.message || JSON.stringify(d));
    } catch (_) { /* ne JSON */ }
    throw new ApiError(msg, res.status);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : null;
}

/* ---------- Pagalbinės ---------- */
const list = (x) => (Array.isArray(x) ? x : []);
const nameOf = (p) => (p && typeof p === "object" ? p.name : p) || "";
const initials = (n) => String(n || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
const roles = () => me?.roles || [];
const isHr = () => roles().includes("hr");
const isManager = () => roles().includes("manager");
const myId = () => me?.employee?.id;

function fmtDays(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "";
  return n.toLocaleString("lt-LT", { maximumFractionDigits: 1 }) + " d.";
}
function parseIso(iso) {
  const m = String(iso || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
}
function isoOf(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
function fmtDate(iso, withYear) {
  const d = parseIso(iso);
  if (!d) return esc(iso);
  const opt = { month: "long", day: "numeric" };
  if (withYear || d.getFullYear() !== new Date().getFullYear()) opt.year = "numeric";
  return d.toLocaleDateString("lt-LT", opt);
}
function fmtRange(a, b) { return a === b ? fmtDate(a) : `${fmtDate(a)} iki ${fmtDate(b)}`; }
function fmtTs(ts) {
  if (ts === null || ts === undefined || ts === "") return "";
  const d = typeof ts === "number" ? new Date(ts < 1e12 ? ts * 1000 : ts) : new Date(ts);
  if (Number.isNaN(d.getTime())) return esc(ts);
  return d.toLocaleString("lt-LT", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
function signHref(url) {
  const m = String(url || "").match(/sign\/([^/?#]+)\/([^/?#]+)/);
  return m ? `#/sign/${m[1]}/${m[2]}` : "";
}

function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast on" + (isErr ? " err" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = "toast"), 4000);
}

function stateHtml(icon, title, text, extra = "", cls = "") {
  return `<div class="state ${cls}"><i class="ph ${icon}" aria-hidden="true"></i><b>${esc(title)}</b>${text ? `<p>${esc(text)}</p>` : ""}${extra}</div>`;
}
const errorHtml = (e, retry = true) => stateHtml("ph-warning-circle", "Nepavyko įkelti", e.message,
  retry ? `<button class="btn" type="button" data-retry><i class="ph ph-arrow-clockwise" aria-hidden="true"></i>Bandyti dar kartą</button>` : "", "err");
const skeletonCards = (n = 3) => Array.from({ length: n }, () => `<div class="sk sk-card" aria-hidden="true"></div>`).join("") + `<span class="sr-only">Kraunama</span>`;

/* ---------- Normalizavimas ---------- */
function normSigner(s) {
  return {
    signerId: s.signer_id,
    employeeId: s.employee?.id,
    name: nameOf(s.employee),
    role: s.role || "",
    sequence: Number(s.sequence ?? 0),
    status: s.status || "pending",
    signedAt: s.signed_at,
    reason: s.reason || "",
    signUrl: s.sign_url || "",
  };
}
function normDoc(d) {
  return {
    id: d.id, kind: d.kind || "request", title: d.title || (d.kind === "order" ? "Įsakymas" : "Prašymas"),
    status: d.status || "pending", sha256: d.sha256 || "",
    pdfUrl: d.pdf_url || "", signedUrl: d.signed_url || "",
    signers: list(d.signers).map(normSigner).sort((a, b) => a.sequence - b.sequence),
  };
}
function normReq(r) {
  const documents = list(r.documents).map(normDoc);
  const reqDoc = documents.find((d) => d.kind === "request");
  let signers = list(r.signers).map(normSigner).sort((a, b) => a.sequence - b.sequence);
  if (!signers.length && reqDoc) signers = reqDoc.signers;
  return {
    raw: r, id: r.id, type: r.type, leaveKind: r.leave_kind, dateFrom: r.date_from, dateTo: r.date_to,
    workDays: r.work_days, status: r.status, orderStatus: r.order_status || "", reason: r.reason || "",
    declinedBy: nameOf(r.declined_by),
    employeeId: r.employee?.id, employeeName: nameOf(r.employee),
    substituteName: nameOf(r.substitute),
    destination: r.destination || "", purpose: r.purpose || "", comment: r.comment || "",
    signUrl: r.sign_url || "", canCancel: r.can_cancel, createdAt: r.created_at,
    signers, documents, audit: list(r.audit),
  };
}
const reqTitle = (r) => r.type === "trip" ? `Komandiruotė${r.destination ? `: ${r.destination}` : ""}` : (KIND_LT[r.leaveKind] || "Atostogos");
const reqIcon = (r) => r.type === "trip" ? "ph-airplane-tilt" : "ph-calendar-check";

function currentSigner(signers, docStatus = "pending") {
  if (docStatus && docStatus !== "pending") return null;
  if (signers.some((s) => s.status === "declined")) return null;
  return signers.find((s) => s.status === "pending") || null;
}
function signerState(s, cur) {
  if (s.status === "signed") return "signed";
  if (s.status === "declined") return "declined";
  return cur && cur === s ? "current" : "waiting";
}
const STATE_ICON = { signed: "ph-check-circle", declined: "ph-x-circle", current: "ph-hourglass-medium", waiting: "ph-circle-dashed" };
const STATE_LT = { signed: "pasirašė", declined: "atsisakė", current: "dabar eilė", waiting: "laukia" };

/** Kur dabartiniam naudotojui pasirašyti šiame prašyme (jei jo eilė). */
function mySignLink(r) {
  if (r.signUrl) return signHref(r.signUrl) || "";
  for (const d of r.documents) {
    const cur = currentSigner(d.signers, d.status);
    if (cur && cur.employeeId === myId()) return cur.signUrl ? signHref(cur.signUrl) : "";
  }
  return "";
}
/** Kur pasirašyti konkretų dokumentą (jei jo eilė dabartiniam naudotojui). */
function docSignLink(d) {
  const cur = currentSigner(d.signers, d.status);
  return cur && cur.employeeId === myId() && cur.signUrl ? signHref(cur.signUrl) : "";
}

/* ---------- Prisijungimas ---------- */
function signedOut() {
  me = null; csrf = "";
  // ankstesnio žmogaus duomenys neturi likti ekrane
  for (const id of ["#balance-val", "#mine-list", "#to-sign-list", "#request-body", "#sign-body", "#ts-body", "#outbox-list", "#who-name", "#who-role"]) $(id).innerHTML = "";
  $("#sign-count").hidden = true;
  scope = "mine";
  $$("#scope [data-scope]").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.scope === "mine")));
  $("#app-view").hidden = true;
  $("#boot").hidden = true;
  $("#login-view").hidden = false;
  if (location.hash && !location.hash.startsWith("#/login")) afterLogin = location.hash;
  loadPersonas();
}

async function loadPersonas() {
  const panel = $("#personas-panel"), box = $("#personas");
  box.innerHTML = `<div class="panel-pad">${skeletonCards(2)}</div>`;
  panel.hidden = false;
  let people;
  try { people = list(await api("/demo/personas", { allow401: true })); }
  catch (e) {
    if (e.status === 404 || e.status === 403) { panel.hidden = true; return; }
    box.innerHTML = `<div class="panel-pad">${errorHtml(e)}</div>`;
    box.querySelector("[data-retry]")?.addEventListener("click", loadPersonas);
    return;
  }
  if (!people.length) { panel.hidden = true; return; }
  const groups = new Map();
  for (const p of people) {
    const dep = p.department || "Kiti";
    if (!groups.has(dep)) groups.set(dep, []);
    groups.get(dep).push(p);
  }
  box.innerHTML = [...groups].map(([dep, ps]) => `
    <div class="persona-group"><h3>${esc(dep)}</h3><ul>${ps.map((p) => {
      const rs = list(p.roles).filter((r) => r !== "employee");
      return `<li><button type="button" class="persona" data-persona="${esc(p.id)}" data-testid="persona">
        <span class="avatar" aria-hidden="true">${esc(initials(p.name))}</span>
        <span><b>${esc(p.name)}</b><small>${esc(p.position || "")}</small></span>
        <span class="roles">${rs.map((r) => `<span class="role ${esc(r)}">${r === "hr" ? "Personalas" : r === "manager" ? "Vadovas" : esc(r)}</span>`).join("")}</span>
      </button></li>`;
    }).join("")}</ul></div>`).join("");
}

$("#personas").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-persona]");
  if (!btn || btn.getAttribute("aria-busy") === "true") return;
  btn.setAttribute("aria-busy", "true");
  try {
    await api("/demo/login", { method: "POST", body: { employee_id: Number(btn.dataset.persona) }, allow401: true });
    await startApp(afterLogin || "#/mine");
  } catch (e) { toast(e.message, true); }
  finally { btn.removeAttribute("aria-busy"); }
});

$("#magic-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const input = $("#magic-email"), msg = $("#magic-msg"), btn = ev.submitter || $("#magic-form button[type=submit]");
  const email = input.value.trim();
  msg.className = "field-msg";
  if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    input.setAttribute("aria-invalid", "true");
    msg.textContent = email ? "Patikrinkite el. pašto adresą." : "Įveskite darbo el. paštą.";
    input.focus();
    return;
  }
  input.removeAttribute("aria-invalid");
  btn.disabled = true;
  try {
    await api("/login/request", { method: "POST", body: { email }, allow401: true });
    msg.className = "field-msg ok";
    msg.innerHTML = `<i class="ph ph-check-circle" aria-hidden="true"></i>Nuoroda išsiųsta. Jei adresas yra sistemoje, laiškas jau pakeliui.`;
  } catch (e) { msg.textContent = e.message; }
  finally { btn.disabled = false; }
});
$("#magic-email").addEventListener("input", () => { $("#magic-email").removeAttribute("aria-invalid"); });

$("#logout").addEventListener("click", async () => {
  try { await api("/logout", { method: "POST", allow401: true }); } catch (_) { /* vis tiek atjungiam */ }
  afterLogin = "";
  history.replaceState(null, "", location.pathname);
  signedOut();
});

async function loadMe(allow401 = false) {
  const d = await api("/me", { allow401 });
  me = { employee: d.employee || {}, roles: list(d.roles), balance: d.balance, demo: d.demo !== false };
  csrf = d.csrf || csrf;
  $("#who-name").textContent = me.employee.name || "";
  $("#who-role").textContent = me.employee.position || (isHr() ? "Personalas" : isManager() ? "Vadovas" : "Darbuotojas");
  $$("[data-need]").forEach((el) => {
    const need = el.dataset.need;
    el.hidden = need === "timesheet" ? !(isHr() || isManager()) : need === "hr" ? !(isHr() && me.demo) : need === "manager" ? !isManager() : false;
  });
  $("#scope").hidden = !(isHr() || isManager());
  return me;
}

async function startApp(hash) {
  await loadMe(true);
  afterLogin = "";
  $("#login-view").hidden = true;
  $("#boot").hidden = true;
  $("#app-view").hidden = false;
  $("#magic-form").reset();
  $("#magic-msg").textContent = "";
  if (hash && location.hash !== hash) location.hash = hash; else route();
  refreshCount();
}

/* ---------- Maršrutai ---------- */
const VIEWS = ["mine", "to-sign", "new", "request", "sign", "timesheet", "outbox"];
function show(view, tab = view) {
  for (const v of VIEWS) $(`#view-${v}`).classList.toggle("on", v === view);
  $$(".tabs a").forEach((a) => (a.dataset.tab === tab ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  window.scrollTo(0, 0);
  $("#main").focus({ preventScroll: true });
}

function route() {
  if (!me) return;
  const seq = ++navSeq;
  const parts = (location.hash.replace(/^#\/?/, "") || "mine").split("/");
  const [name] = parts;
  switch (name) {
    case "to-sign": show("to-sign"); return renderToSign(seq);
    case "new": show("new", "mine"); return renderNew();
    case "request": show("request", "mine"); return renderRequest(seq, parts[1]);
    case "sign": show("sign", "to-sign"); return renderSign(seq, parts[1], parts[2]);
    case "timesheet":
      if (!(isHr() || isManager())) { location.hash = "#/mine"; return; }
      show("timesheet"); if (parts[1]) tsMonth = parts[1]; return renderTimesheet(seq);
    case "outbox": show("outbox"); return renderOutbox(seq);
    default: show("mine"); return renderMine(seq);
  }
}
window.addEventListener("hashchange", route);
const stale = (seq) => seq !== navSeq;

async function refreshCount() {
  try {
    const n = list(await api("/requests?scope=to_sign")).length;
    const c = $("#sign-count");
    c.hidden = n === 0;
    c.textContent = String(n);
    $("#sign-count-sr").textContent = n ? `Laukia jūsų parašo: ${n}` : "";
  } catch (_) { /* skaitliukas neesminis */ }
}

/* ---------- Kortelės ---------- */
function chainHtml(signers, docStatus) {
  if (!signers.length) return "";
  const cur = currentSigner(signers, docStatus);
  return `<ul class="chain" aria-label="Pasirašantieji">${signers.map((s) => {
    const st = signerState(s, cur);
    return `<li class="st-${st}"><i class="ph ${STATE_ICON[st]}" aria-hidden="true"></i><span>${esc(ROLE_LT[s.role] || s.role)}: ${esc(s.name)}<span class="sr-only">, ${STATE_LT[st]}</span></span></li>`;
  }).join("")}</ul>`;
}
function statusHtml(r) {
  let h = `<span class="status ${STATUS_CLS[r.status] || ""}" data-testid="status">${esc(STATUS_LT[r.status] || r.status)}</span>`;
  if (r.type === "trip" && r.orderStatus) h += ` <span class="status ${esc(r.orderStatus)}">${esc(ORDER_LT[r.orderStatus] || r.orderStatus)}</span>`;
  return h;
}
function cardHtml(r, { showWho = false, href } = {}) {
  const reqDoc = r.documents.find((d) => d.kind === "request");
  return `<a class="req" href="${href || `#/request/${r.id}`}" data-testid="req-card" data-id="${esc(r.id)}">
    <div class="req-top">
      <div class="req-title"><i class="ph ${reqIcon(r)}" aria-hidden="true"></i><div>
        <b>${esc(reqTitle(r))}</b>
        <small>${fmtRange(r.dateFrom, r.dateTo)}${r.workDays !== undefined ? `, ${esc(r.workDays)} darbo d.` : ""}</small>
        ${showWho && r.employeeName ? `<span class="who-line">${esc(r.employeeName)}</span>` : ""}
      </div></div>
      <div>${statusHtml(r)}</div>
    </div>
    ${chainHtml(r.signers, r.status === "pending_signatures" ? (reqDoc?.status || "pending") : "done")}
  </a>`;
}

/* ---------- Mano prašymai ---------- */
let scope = "mine";
$("#scope").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-scope]");
  if (!b) return;
  scope = b.dataset.scope;
  $$("#scope [data-scope]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  renderMine(++navSeq);
});

async function renderMine(seq) {
  const box = $("#mine-list");
  box.innerHTML = skeletonCards();
  $("#mine-lede").textContent = scope === "team" ? "Jūsų pavaldinių prašymai." : scope === "all" ? "Visų darbuotojų prašymai." : "Pateikite prašymą ir sekite, kas jau pasirašė.";
  try {
    const [, data] = await Promise.all([loadMe(), api(`/requests?scope=${scope}`)]);
    if (stale(seq)) return;
    $("#balance-val").innerHTML = me.balance === undefined || me.balance === null ? "" :
      `${esc(Number(me.balance).toLocaleString("lt-LT", { maximumFractionDigits: 1 }))}<small>d.</small>`;
    const items = list(data).map(normReq);
    if (!items.length) {
      box.innerHTML = scope === "mine"
        ? stateHtml("ph-calendar-blank", "Prašymų dar nėra", "Pateikite atostogų ar komandiruotės prašymą. Pasirašysite telefonu, o kolegos gaus kvietimą.", `<a class="btn primary" href="#/new"><i class="ph ph-plus" aria-hidden="true"></i>Naujas prašymas</a>`)
        : stateHtml("ph-users-three", "Prašymų nėra", "Kai kas nors pateiks prašymą, jis atsiras čia.");
      return;
    }
    box.innerHTML = items.map((r) => cardHtml(r, { showWho: scope !== "mine" })).join("");
  } catch (e) {
    if (stale(seq) || e.status === 401) return;
    box.innerHTML = errorHtml(e);
    box.querySelector("[data-retry]")?.addEventListener("click", () => renderMine(++navSeq));
  }
}

/* ---------- Laukia mano parašo ---------- */
async function renderToSign(seq) {
  const box = $("#to-sign-list");
  box.innerHTML = skeletonCards(2);
  try {
    const items = list(await api("/requests?scope=to_sign")).map(normReq);
    if (stale(seq)) return;
    const c = $("#sign-count");
    c.hidden = items.length === 0; c.textContent = String(items.length);
    if (!items.length) {
      box.innerHTML = stateHtml("ph-seal-check", "Viskas pasirašyta", "Kai kolega paprašys jūsų parašo, prašymas atsiras čia ir gausite laišką.");
      return;
    }
    box.innerHTML = items.map((r) => {
      const href = mySignLink(r) || `#/request/${r.id}`;
      const isOrder = r.type === "trip" && r.orderStatus === "pending_order";
      return cardHtml(r, { showWho: true, href }).replace('class="req"', 'class="req attention"')
        .replace("</a>", `<div class="cta-line"><span>${isOrder ? "Įsakymas laukia jūsų parašo" : `${esc(r.employeeName || "Kolega")} laukia jūsų parašo`}</span><span class="btn primary small" aria-hidden="true"><i class="ph ph-signature" aria-hidden="true"></i>Pasirašyti</span></div></a>`);
    }).join("");
  } catch (e) {
    if (stale(seq) || e.status === 401) return;
    box.innerHTML = errorHtml(e);
    box.querySelector("[data-retry]")?.addEventListener("click", () => renderToSign(++navSeq));
  }
}
