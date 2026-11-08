"use strict";

const MODULES = {
  invoices: {
    title: "Pirkimo ir pardavimo sąskaitos",
    lede: "PDF sąskaitos nuskaitomos, patikrinama PVM aritmetika ir dublikatai, paruošiamas Rivilė ERP importo failas.",
    upload: { accept: ".pdf", multiple: true, label: "Įkelti PDF sąskaitas" },
  },
  bank: {
    title: "Banko išrašas ir mokėjimai",
    lede: "camt.053 išrašas sulyginamas su sąskaitomis. Neapmokėtoms paruošiamas mokėjimų failas, kurį tvirtinate banke.",
    upload: { accept: ".xml", multiple: false, label: "Įkelti camt.053" },
  },
  payroll: {
    title: "Darbo užmokestis ir darbo laikas",
    lede: "Tabelis sutikrinamas su Rivile, Sodros pažymomis ir atostogų prašymais. Bruto skaičiuojamas iš sutarto neto kiekvienam darbuotojui.",
    upload: { accept: ".xlsx", multiple: false, label: "Įkelti tabelį" },
  },
};

const SEV_ICON = { error: "ph-warning-octagon", warning: "ph-warning", info: "ph-info" };
const SEV_LABEL = { error: "Klaidos", warning: "Įspėjimai", info: "Info" };
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let csrfCache = null;
async function csrfToken() {
  if (csrfCache) return csrfCache;
  for (const url of ["/api/me", "/api/hr/me"]) {   // UNO sesija, tada personalo sesija (kaip serveryje)
    try {
      const r = await fetch(url, { credentials: "same-origin" });
      if (r.ok) { csrfCache = (await r.json()).csrf || ""; if (csrfCache) return csrfCache; }
    } catch (_) { /* bandome kitą */ }
  }
  return "";
}

async function api(path, opts = {}) {
  if (opts.method && opts.method !== "GET") {
    const csrf = await csrfToken();
    if (csrf) opts = { ...opts, headers: { ...(opts.headers || {}), "X-CSRF-Token": csrf } };
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = `Klaida ${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch (_) { /* ne JSON */ }
    throw new Error(msg);
  }
  return res.json();
}

function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast on" + (isErr ? " err" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = "toast"), 3200);
}

/* ---------- Navigacija ---------- */
function show(view) {
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("on", v.id === `view-${view}`));
  document.querySelectorAll(".steps button").forEach((b) => b.toggleAttribute("aria-current", b.dataset.view === view));
  document.querySelectorAll(".steps button[aria-current]").forEach((b) => b.setAttribute("aria-current", "page"));
  history.replaceState(null, "", `#${view}`);
  if (MODULES[view] && !$(`#view-${view}`).dataset.loaded) loadModule(view);
  if (view === "control") loadControl();
  if (view === "declarations") loadDeclarations();
}
document.querySelectorAll(".steps button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));

function setBadges(badges) {
  for (const [key, c] of Object.entries(badges || {})) {
    const el = document.querySelector(`[data-badge="${key}"]`);
    if (!el) continue;
    const parts = [];
    if (c.error) parts.push(`<b class="badge err">${c.error}</b>`);
    if (c.warning) parts.push(`<b class="badge warn">${c.warning}</b>`);
    el.innerHTML = parts.join("") || `<b class="badge ok">OK</b>`;
    el.title = `${c.error || 0} klaidos, ${c.warning || 0} įspėjimai`;
  }
}

/* ---------- Apžvalga ---------- */
async function loadOverview() {
  const d = await api("/api/overview");
  $("#period").textContent = d.period;
  $("#ledger tbody").innerHTML = d.ledger.map((l) => `
    <tr>
      <td><div class="line-name">${esc(l.line)}</div></td>
      <td class="num">${esc(l.price)}</td>
      <td class="what">${esc(l.what)}</td>
      <td><div class="auto"><div class="auto-bar" style="--p:${l.auto_pct}%"></div><b>${esc(l.auto)}</b></div></td>
      <td class="result-cell ${l.result ? "has" : ""}">${l.result ? esc(l.result) : "Dar nepaleista"}</td>
    </tr>`).join("");
  $("#ledger tfoot").innerHTML = `<tr><td>Iš viso</td><td class="num">${esc(d.total_price)}</td><td class="what">${esc(d.total_note)}</td><td colspan="2"></td></tr>`;
  $("#checklist").innerHTML = d.checklist.map((c) => `
    <li><i class="ph ${c.done ? "ph-check-circle" : "ph-circle-dashed"}"></i>
      <span>${esc(c.label)}<small>${esc(c.detail || "")}</small></span><span></span></li>`).join("");
  setBadges(d.badges);
}

$("#run-all").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  btn.innerHTML = `<i class="ph ph-hourglass"></i>Skaičiuojama…`;
  try {
    for (const m of ["invoices", "bank", "payroll"]) {
      renderModule(m, await api(`/api/run/${m}`, { method: "POST" }));
    }
    await loadOverview();
    toast("Uždarymas paskaičiuotas. Peržiūrėkite radinius kiekviename etape.");
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<i class="ph ph-play"></i>Paleisti uždarymą`;
  }
});

/* ---------- Moduliai ---------- */
function moduleShell(key) {
  const m = MODULES[key];
  const up = m.upload;
  return `
    <div class="head">
      <div><h1>${esc(m.title)}</h1><p class="lede">${esc(m.lede)}</p></div>
      <div class="actions">
        <label class="btn file-btn"><i class="ph ph-upload-simple"></i>${esc(up.label)}
          <input type="file" accept="${up.accept}" ${up.multiple ? "multiple" : ""} data-upload="${key}"></label>
        ${key === "invoices" ? `<button class="btn" data-mail><i class="ph ph-envelope-simple"></i>Importuoti iš pašto</button>` : ""}
        <button class="btn primary" data-run="${key}"><i class="ph ph-play"></i>Paleisti su demo duomenimis</button>
      </div>
    </div>
    ${key === "payroll" ? payrollCalcHtml() : ""}
    ${key === "invoices" ? `<p class="lede" data-sync style="margin:0 0 12px"><i class="ph ph-hard-drives"></i> Rivilės serveris: tikrinama...</p>` : ""}
    <div class="panel kpis" data-kpis></div>
    <div class="grid-2" style="margin-top:20px">
      <div class="stack">
        <div class="panel"><div class="panel-pad" style="padding-bottom:0"><h2>Radiniai</h2></div><div data-findings>${emptyHtml("Paleiskite modulį, kad pamatytumėte neatitikimus.")}</div></div>
        <div class="panel panel-pad"><h2>Paruošti failai</h2><div class="downloads" data-downloads><span class="lede">Failai atsiras po paleidimo.</span></div></div>
      </div>
      <div class="panel"><div class="panel-pad" style="padding-bottom:8px"><h2 data-table-title>Duomenys</h2></div><div class="table-wrap" data-table>${emptyHtml("Lentelė tuščia.")}</div></div>
    </div>`;
}

function agoText(iso) {
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (min < 1) return "ką tik";
  if (min < 60) return `prieš ${min} min.`;
  const h = Math.round(min / 60);
  return h < 48 ? `prieš ${h} val.` : `prieš ${Math.round(h / 24)} d.`;
}

async function loadSync(sec) {
  const el = $("[data-sync]", sec);
  if (!el) return;
  try {
    const s = await api("/api/rivile-sync");
    if (!s.last_at) { el.innerHTML = `<i class="ph ph-hard-drives"></i> Rivilės serveris dar nesikreipė.`; return; }
    const when = new Date(s.last_at).toLocaleString("lt-LT");
    const stale = Date.now() - new Date(s.last_at).getTime() > 45 * 60000;
    const sent = s.last_ack_at
      ? ` Paskutinė perduota siunta ${new Date(s.last_ack_at).toLocaleString("lt-LT")} (${s.last_ack_count} sąsk.), iš viso perduota ${s.acked_total}.`
      : " Siuntų dar neperduota.";
    el.innerHTML = `<i class="ph ph-hard-drives"></i> Rivilės serveris paskutinį kartą kreipėsi ${esc(when)} (${esc(agoText(s.last_at))})`
      + (stale ? " <b>· seniau nei 45 min., patikrinkite serverį</b>" : "") + `.${esc(sent)}`;
  } catch (_) { el.textContent = "Rivilės serverio būsenos gauti nepavyko."; }
}

function emptyHtml(text) {
  return `<div class="empty"><i class="ph ph-tray"></i>${esc(text)}</div>`;
}

function loadingHtml() {
  return `<div class="skeleton"></div><div class="skeleton" style="width:70%"></div><div class="skeleton" style="width:85%"></div>`;
}

async function loadModule(key) {
  const sec = $(`#view-${key}`);
  sec.innerHTML = moduleShell(key);
  sec.dataset.loaded = "1";
  $(`[data-run="${key}"]`, sec).addEventListener("click", () => runModule(key));
  $(`[data-upload="${key}"]`, sec).addEventListener("change", (e) => uploadModule(key, e.target.files));
  if (key === "payroll") bindCalc(sec);
  if (key === "invoices") $("[data-mail]", sec).addEventListener("click", importMail);
  if (key === "invoices") loadSync(sec);
  try {
    const last = await api(`/api/module/${key}`);
    if (last && !last.empty) renderModule(key, last);
  } catch (_) { /* dar nepaleista */ }
}

async function runModule(key) {
  const sec = $(`#view-${key}`);
  const btn = $(`[data-run="${key}"]`, sec);
  btn.disabled = true;
  $("[data-findings]", sec).innerHTML = loadingHtml();
  try {
    renderModule(key, await api(`/api/run/${key}`, { method: "POST" }));
    loadOverview();
  } catch (err) {
    $("[data-findings]", sec).innerHTML = emptyHtml(err.message);
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

async function uploadModule(key, files) {
  if (!files || !files.length) return;
  const fd = new FormData();
  [...files].forEach((f) => fd.append("files", f));
  const sec = $(`#view-${key}`);
  $("[data-findings]", sec).innerHTML = loadingHtml();
  try {
    renderModule(key, await api(`/api/upload/${key}`, { method: "POST", body: fd }));
    toast(`Apdorota: ${files.length} failas(-ai).`);
    loadOverview();
  } catch (err) {
    $("[data-findings]", sec).innerHTML = emptyHtml(err.message);
    toast(err.message, true);
  }
}

function renderModule(key, r) {
  const sec = $(`#view-${key}`);
  if (!sec.dataset.loaded) { sec.innerHTML = moduleShell(key); sec.dataset.loaded = "1"; bindModule(key, sec); }
  $("[data-kpis]", sec).innerHTML = (r.kpis || []).map((k) => `<div class="kpi"><span>${esc(k.label)}</span><b>${esc(k.value)}</b></div>`).join("");
  $("[data-findings]", sec).innerHTML = findingsHtml(r.findings || []);
  bindFilters(sec);
  sec.querySelectorAll("[data-create-supplier]").forEach((btn) =>
    btn.addEventListener("click", () => createSupplier(btn, r.findings[+btn.dataset.createSupplier].create_supplier)));
  $("[data-downloads]", sec).innerHTML = (r.downloads || []).map((d) =>
    `<a href="${esc(d.url)}" download><i class="ph ph-file-arrow-down"></i>${esc(d.label)}<small>${esc(d.meta || "")}</small></a>`).join("") || `<span class="lede">Failų nėra.</span>`;
  if (r.table) {
    $("[data-table-title]", sec).textContent = r.table.title || "Duomenys";
    $("[data-table]", sec).innerHTML = tableHtml(r.table);
  }
  if (r.badges) setBadges(r.badges);
}

function bindModule(key, sec) {
  $(`[data-run="${key}"]`, sec).addEventListener("click", () => runModule(key));
  $(`[data-upload="${key}"]`, sec).addEventListener("change", (e) => uploadModule(key, e.target.files));
  if (key === "payroll") bindCalc(sec);
  if (key === "invoices") $("[data-mail]", sec).addEventListener("click", importMail);
}

function findingsHtml(list) {
  if (!list.length) return emptyHtml("Neatitikimų nerasta.");
  const counts = { error: 0, warning: 0, info: 0 };
  list.forEach((f) => (counts[f.severity] = (counts[f.severity] || 0) + 1));
  const chips = ["all", "error", "warning", "info"].filter((s) => s === "all" || counts[s]).map((s) =>
    `<button class="chip" data-filter="${s}" aria-pressed="${s === "all"}">${s === "all" ? `Visi ${list.length}` : `${SEV_LABEL[s]} ${counts[s]}`}</button>`).join("");
  const items = list.map((f, i) => `
    <li class="finding ${esc(f.severity)}" data-sev="${esc(f.severity)}">
      <i class="ph ${SEV_ICON[f.severity] || "ph-info"}"></i>
      <div>
        <h3>${esc(f.title)}${f.ref ? `<span class="ref">${esc(f.ref)}</span>` : ""}</h3>
        <p>${esc(f.detail)}</p>
        ${f.suggested_action ? `<details><summary>Siūlomas veiksmas</summary><pre>${esc(f.suggested_action)}</pre></details>` : ""}
        ${f.create_supplier ? `<button class="btn small" style="margin-top:8px" data-create-supplier="${i}"><i class="ph ph-user-plus"></i>Sukurti tiekėją</button>` : ""}
      </div>
    </li>`).join("");
  return `<div class="filter">${chips}</div><ul class="findings" style="max-height:520px;overflow:auto">${items}</ul>`;
}

function bindFilters(scope) {
  scope.querySelectorAll("[data-filter]").forEach((chip) => chip.addEventListener("click", () => {
    const f = chip.dataset.filter;
    scope.querySelectorAll("[data-filter]").forEach((c) => c.setAttribute("aria-pressed", c === chip));
    scope.querySelectorAll(".finding").forEach((li) => (li.hidden = f !== "all" && li.dataset.sev !== f));
  }));
}

function tableHtml(t) {
  if (!t.rows || !t.rows.length) return emptyHtml("Lentelė tuščia.");
  const head = t.columns.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("");
  const body = t.rows.map((r) => `<tr class="${r._sev ? "row-" + esc(r._sev) : ""}">${
    t.columns.map((c) => `<td class="${c.num ? "num" : c.wrap ? "wrap" : ""}">${esc(r[c.key])}</td>`).join("")}</tr>`).join("");
  return `<table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

async function importMail(e) {
  const btn = e.currentTarget;
  const sec = $("#view-invoices");
  btn.disabled = true;
  btn.innerHTML = `<i class="ph ph-hourglass"></i>Tikrinamas paštas…`;
  $("[data-findings]", sec).innerHTML = loadingHtml();
  try {
    const r = await api("/api/invoices/mail", { method: "POST" });
    renderModule("invoices", r);
    const k = (r.kpis || []).find((x) => x.label === "Iš pašto");
    toast(`Paštas patikrintas${k ? `: ${k.value}` : ""}.`);
    loadOverview();
  } catch (err) {
    $("[data-findings]", sec).innerHTML = emptyHtml(err.message);
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<i class="ph ph-envelope-simple"></i>Importuoti iš pašto`;
  }
}

async function createSupplier(btn, sup) {
  const codes = [sup.company_code && `įm. kodas ${sup.company_code}`, sup.vat_code && `PVM ${sup.vat_code}`].filter(Boolean).join(", ");
  if (!confirm(`Sukurti tiekėją „${sup.name}“${codes ? ` (${codes})` : ""}?
Prieš tai patikrinkite rekvizitus: netikras tiekėjas yra dažnas sukčiavimo būdas.`)) return;
  btn.disabled = true;
  try {
    const r = await api("/api/suppliers", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(sup),
    });
    if (r.kpis) renderModule("invoices", r);
    toast(`Tiekėjas ${r.supplier.name} ${r.supplier.created ? "sukurtas" : "jau buvo"}: ${r.supplier.code}.`);
    loadOverview();
  } catch (err) {
    btn.disabled = false;
    toast(err.message, true);
  }
}

/* ---------- Mėnesinės deklaracijos ---------- */
async function loadDeclarations() {
  const sec = $("#view-declarations");
  sec.innerHTML = `
    <div class="head"><div><h1>Mėnesinės deklaracijos</h1>
      <p class="lede">GPM313, SAM, i.SAF ir PVM sumos paskaičiuotos iš DU ir sąskaitų. Deklaracijas generuoja Rivilė, sistema patikrina sumas prieš pateikimą. Pateikia žmogus.</p></div></div>
    <div class="panel kpis" data-kpis></div>
    <div class="panel" style="margin-top:20px"><div class="panel-pad" style="padding-bottom:8px"><h2 data-table-title>Deklaracijos</h2></div><div class="table-wrap" data-table>${loadingHtml()}</div></div>
    <div class="panel" style="margin-top:20px"><div class="panel-pad" style="padding-bottom:0"><h2>Patikros</h2></div><div data-findings>${loadingHtml()}</div></div>`;
  try {
    const r = await api("/api/declarations");
    $("[data-kpis]", sec).innerHTML = r.kpis.map((k) => `<div class="kpi"><span>${esc(k.label)}</span><b>${esc(k.value)}</b></div>`).join("");
    $("[data-table-title]", sec).textContent = r.table.title;
    $("[data-table]", sec).innerHTML = tableHtml(r.table);
    $("[data-findings]", sec).innerHTML = findingsHtml(r.findings);
    bindFilters(sec);
    setBadges(r.badges);
  } catch (err) {
    $("[data-findings]", sec).innerHTML = emptyHtml(err.message);
    toast(err.message, true);
  }
}

/* ---------- DU skaičiuoklė ---------- */
function payrollCalcHtml() {
  return `
    <div class="panel panel-pad" style="margin-bottom:20px">
      <h2>Neto į bruto: vieno darbuotojo patikra</h2>
      <div class="calc">
        <div class="stack" style="gap:14px">
          <div class="field"><label for="calc-net">Sutarta suma į rankas, €</label>
            <input id="calc-net" type="number" min="0" step="0.01" value="1450.00" inputmode="decimal">
            <small>2026 m. tarifai: GPM 20 %, Sodra 19,5 %, NPD pagal formulę</small></div>
          <div class="field"><label for="calc-term">Sutartis</label>
            <select id="calc-term"><option value="0">Neterminuota</option><option value="1">Terminuota</option></select></div>
        </div>
        <table class="breakdown" id="calc-out"><tr><td>Įveskite sumą</td></tr></table>
      </div>
    </div>`;
}

function bindCalc(sec) {
  const net = $("#calc-net", sec), term = $("#calc-term", sec);
  let timer;
  const run = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const fd = new FormData();
      fd.append("net", net.value || "0");
      fd.append("fixed_term", term.value);
      try {
        const r = await api("/api/payroll/calc", { method: "POST", body: fd });
        $("#calc-out", sec).innerHTML = r.lines.map((l) =>
          `<tr class="${l.total ? "total" : ""}"><td>${esc(l.label)}</td><td class="num">${esc(l.value)}</td></tr>`).join("");
      } catch (err) {
        $("#calc-out", sec).innerHTML = `<tr><td>${esc(err.message)}</td></tr>`;
      }
    }, 180);
  };
  net.addEventListener("input", run);
  term.addEventListener("change", run);
  run();
}

/* ---------- Kontrolė + AI tiltas ---------- */
async function loadControl() {
  const sec = $("#view-control");
  if (!sec.dataset.loaded) {
    sec.innerHTML = controlShell();
    sec.dataset.loaded = "1";
    $("#ai-pack", sec).addEventListener("click", aiPackage);
    $("#ai-copy", sec).addEventListener("click", aiCopy);
    $("#ai-restore", sec).addEventListener("click", aiRestore);
  }
  $("[data-findings]", sec).innerHTML = loadingHtml();
  try {
    const r = await api("/api/control");
    $("[data-kpis]", sec).innerHTML = r.kpis.map((k) => `<div class="kpi"><span>${esc(k.label)}</span><b>${esc(k.value)}</b></div>`).join("");
    $("[data-findings]", sec).innerHTML = findingsHtml(r.findings);
    bindFilters(sec);
    $("[data-drafts]", sec).innerHTML = r.drafts.length ? r.drafts.map((d) =>
      `<details><summary>${esc(d.to)}: ${esc(d.subject)}</summary><pre class="draft">${esc(d.body)}</pre></details>`).join("") : `<span class="lede">Laiškų nereikia.</span>`;
    setBadges(r.badges);
  } catch (err) {
    $("[data-findings]", sec).innerHTML = emptyHtml(err.message);
  }
}

function controlShell() {
  return `
    <div class="head"><div><h1>Kontrolė, trūkstami dokumentai ir AI</h1>
      <p class="lede">Visų etapų neatitikimai vienoje vietoje, laiškų juodraščiai tiekėjams ir vadovams. AI gauna tik pseudonimizuotą tekstą.</p></div></div>
    <div class="panel kpis" data-kpis></div>
    <div class="grid-2" style="margin-top:20px">
      <div class="panel"><div class="panel-pad" style="padding-bottom:0"><h2>Visi radiniai</h2></div><div data-findings></div></div>
      <div class="panel panel-pad"><h2>Laiškų juodraščiai</h2><div data-drafts class="stack" style="gap:8px"></div></div>
    </div>
    <div class="panel panel-pad" style="margin-top:20px">
      <h2>AI tiltas: Claude mato tik žymes</h2>
      <p class="lede" style="margin-bottom:14px">Radiniai paverčiami tekstu, vardai, asmens kodai, IBAN ir el. paštai pakeičiami žymėmis. Claude atsakymas grąžinamas čia ir tikri duomenys atkuriami lokaliai.</p>
      <div class="bridge">
        <div>
          <div class="actions"><button class="btn primary" id="ai-pack"><i class="ph ph-shield-check"></i>Paruošti tekstą Claude</button>
            <button class="btn" id="ai-copy" disabled><i class="ph ph-copy"></i>Kopijuoti</button></div>
          <div id="ai-guard"></div>
          <pre class="tokentext" id="ai-text">Tekstas atsiras paspaudus „Paruošti tekstą Claude“.</pre>
        </div>
        <div>
          <div class="field"><label for="ai-answer">Claude atsakymas (su žymėmis)</label>
            <textarea id="ai-answer" placeholder="Įklijuokite Claude atsakymą čia"></textarea></div>
          <div class="actions" style="margin:10px 0"><button class="btn" id="ai-restore"><i class="ph ph-arrow-counter-clockwise"></i>Atkurti tikrus duomenis</button></div>
          <pre class="tokentext" id="ai-restored">Atkurtas tekstas bus rodomas čia.</pre>
        </div>
      </div>
    </div>`;
}

let aiSession = null;
function highlightTokens(text) {
  return esc(text).replace(/\[(DARB|AK|IBAN|PVM|IMK|TEL|EMAIL|ADR)_\d{3}\]/g, (m) => `<span class="tok">${m}</span>`);
}

async function aiPackage() {
  try {
    const r = await api("/api/ai/package", { method: "POST" });
    aiSession = r.session;
    $("#ai-text").innerHTML = highlightTokens(r.text);
    $("#ai-copy").disabled = !r.safe_to_send;
    const stats = Object.entries(r.stats).map(([k, v]) => `${k}: ${v}`).join(", ");
    $("#ai-guard").innerHTML = r.safe_to_send
      ? `<div class="guard ok"><i class="ph ph-check-circle"></i>Patikra praėjo: neliko asmens kodų, IBAN ar el. paštų. Pakeista: ${esc(stats)}.</div>`
      : `<div class="guard bad"><i class="ph ph-x-circle"></i>Rasta nutekėjimų, siųsti negalima: ${esc(r.leaks.join("; "))}</div>`;
    $("#ai-copy").dataset.text = r.text;
  } catch (err) {
    toast(err.message, true);
  }
}

async function aiCopy(e) {
  try {
    await navigator.clipboard.writeText(e.currentTarget.dataset.text || "");
    toast("Nukopijuota. Įklijuokite į Claude.");
  } catch (_) {
    toast("Nepavyko nukopijuoti, pažymėkite tekstą ranka.", true);
  }
}

async function aiRestore() {
  if (!aiSession) return toast("Pirma paruoškite tekstą Claude.", true);
  const fd = new FormData();
  fd.append("session", aiSession);
  fd.append("text", $("#ai-answer").value);
  try {
    const r = await api("/api/ai/restore", { method: "POST", body: fd });
    $("#ai-restored").textContent = r.text;
  } catch (err) {
    toast(err.message, true);
  }
}

/* ---------- Start ---------- */
loadOverview().catch((err) => toast(err.message, true));
function showFromHash() {
  const v = location.hash.slice(1);
  if (v && document.getElementById(`view-${v}`)) show(v);
}
window.addEventListener("hashchange", showFromHash);
showFromHash();
