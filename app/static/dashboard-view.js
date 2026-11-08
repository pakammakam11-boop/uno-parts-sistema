/* Apžvalgos atvaizdavimas: grynos funkcijos (API atsakymas -> HTML). Laukai tiksliai pagal app/dashboard_api.py
   ir core/dashboard/*. Duomenų užklausos ir būsena: dashboard.js. */
import { SERIES, inlineBar, meter, smallMultiples } from "./dashboard-charts.js";

export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/* ---------- Formatai (lt-LT) ---------- */
const nf0 = new Intl.NumberFormat("lt-LT", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("lt-LT", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat("lt-LT", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const eur = (v) => (isNum(v) ? `${nf0.format(Math.round(v))} €` : "nėra");
export const eur2 = (v) => (isNum(v) ? `${nf2.format(v)} €` : "nėra");
export const pct = (v, d = 1) => (isNum(v) ? `${(d ? nf1 : nf0).format(v)} %` : "nėra");
export const int = (v) => (isNum(v) ? nf0.format(v) : "nėra");
export const dec1 = (v) => (isNum(v) ? nf1.format(v) : "nėra");
export const tickEur = (v) => (v >= 10000 ? `${nf0.format(v / 1000)} tūkst.` : nf0.format(v));
const signed = (v) => (isNum(v) ? `${v > 0 ? "+" : v < 0 ? "−" : ""}${nf1.format(Math.abs(v))} %` : "");

const pad = (n) => String(n).padStart(2, "0");
function toDate(v) { if (!v) return null; const d = new Date(String(v).length === 10 ? `${v}T12:00:00` : v); return Number.isNaN(d.getTime()) ? null : d; }
/** „duomenys iki“: data (MM-DD), o kai API duoda laiką, ir valanda. */
export function asOf(v) {
  const d = toDate(v);
  if (!d) return "";
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}${String(v).length > 10 ? ` ${pad(d.getHours())}:${pad(d.getMinutes())}` : ""}`;
}
const dayLong = new Intl.DateTimeFormat("lt-LT", { month: "long", day: "numeric", weekday: "long" });
export function fmtDay(v, short = false) {
  const d = toDate(v);
  if (!d) return String(v ?? "");
  return short ? String(d.getDate()) : dayLong.format(d);
}
const PERIOD_LT = { day: "Diena", week: "Savaitė", month: "Mėnuo", quarter: "Ketvirtis" };
/** API range.label turi brūkšnį, todėl eilutę sudarome patys iš from/to. */
export const rangeText = (period, r) => {
  if (!r) return "";
  if (r.from > r.to) return `${PERIOD_LT[period]}: dar nėra faktų (nuo ${r.from})`;
  return `${PERIOD_LT[period]}: ${r.from === r.to ? r.from : `nuo ${r.from} iki ${r.to}`}`;
};

/* ---------- Šaltinis, „duomenys iki“, būsena ---------- */
const STATUS_ICON = { demo: "ph-flask", connected: "ph-plugs-connected", waiting: "ph-hourglass-medium" };
const STATUS_LABEL = { demo: "Demo duomenys", connected: "Prijungta", waiting: "Laukia prieigos" };
export function statusChip(status, label) {
  const st = STATUS_ICON[status] ? status : "demo";
  return `<span class="st st-${st}" data-testid="status-chip"><i class="ph ${STATUS_ICON[st]}" aria-hidden="true"></i>${esc(label || STATUS_LABEL[st])}</span>`;
}
/** Kortelės meta: šaltinis, „duomenys iki“, būsena (kiekvienas API blokas turi source, as_of, status, status_label). */
export function meta(o) {
  return `<span class="meta-line" data-testid="meta"><span class="src-name"><i class="ph ph-database" aria-hidden="true"></i>${esc(o.source)}</span>`
    + `<time datetime="${esc(o.as_of)}">duomenys iki <span class="mono">${esc(asOf(o.as_of))}</span></time>${statusChip(o.status, o.status_label)}</span>`;
}

/* ---------- Būsenos ---------- */
export const skeleton = (n = 3, h = 14) => Array.from({ length: n }, () => `<div class="sk sk-line" style="height:${h}px"></div>`).join("");
export function errorBox(e) {
  return `<div class="state err"><i class="ph ph-warning-circle" aria-hidden="true"></i><b>Nepavyko įkelti</b><p>${esc(e?.message || "Bandykite dar kartą.")}</p><button class="btn small" type="button" data-retry>Bandyti dar kartą</button></div>`;
}
export const emptyBox = (title, text) => `<div class="state"><i class="ph ph-tray" aria-hidden="true"></i><b>${esc(title)}</b><p>${esc(text)}</p></div>`;

/* ---------- KPI juosta (/summary) ---------- */
const KPI_ORDER = ["sales", "gross_profit", "costs", "net"];
const KPI_UP_GOOD = { sales: true, gross_profit: true, costs: false, net: true };
function deltaHtml(d, upGood) {
  if (!isNum(d)) return `<span class="delta" data-testid="no-compare"><span class="vs">nėra palyginimo</span></span>`;
  const good = d === 0 ? null : (d > 0) === upGood;
  const icon = d > 0 ? "ph-arrow-up-right" : d < 0 ? "ph-arrow-down-right" : "ph-minus";
  return `<span class="delta ${good === null ? "" : good ? "good" : "bad"}"><i class="ph ${icon}" aria-hidden="true"></i>${signed(d)}<span class="vs"> vs ankstesnis</span></span>`;
}
export function renderKpis(s) {
  const ks = [...s.kpis].sort((a, b) => KPI_ORDER.indexOf(a.key) - KPI_ORDER.indexOf(b.key));
  const cells = ks.map((k) => `
    <div class="kpi-cell kpi-${esc(k.key)}" data-testid="kpi-${esc(k.key)}">
      <span class="kpi-label">${esc(k.label)}</span>
      <b class="kpi-value" data-testid="kpi-value">${esc(eur(k.value))}</b>
      <span class="kpi-sub">${isNum(k.margin_pct) ? `<span class="margin">marža ${esc(pct(k.margin_pct))}</span>` : ""}${deltaHtml(k.delta_pct, KPI_UP_GOOD[k.key] ?? true)}</span>
      ${meta(k)}
    </div>`).join("");
  const f = s.forecast;
  const avgs = f.how.weekday_averages.filter((w) => w.days_left > 0);
  const fc = `
    <div class="kpi-cell kpi-forecast" data-testid="kpi-forecast">
      <span class="kpi-label">Mėnesio prognozė</span>
      <b class="kpi-value" data-testid="forecast-value">${esc(eur(f.forecast))}</b>
      <span class="kpi-sub">${meter(f.pct_of_budget, { label: `Prognozė ${pct(f.pct_of_budget)} biudžeto` })}<span class="budget-pct">${esc(pct(f.pct_of_budget))} biudžeto (${esc(eur(f.budget))})</span></span>
      <span class="kpi-foot">${meta({ ...s, as_of: f.how.as_of })}<button class="linkish" type="button" id="how-btn" aria-expanded="false" aria-controls="how" data-testid="how-btn"><i class="ph ph-question" aria-hidden="true"></i>Kaip skaičiuota</button></span>
      <div class="how" id="how" hidden data-testid="how">
        <p>${esc(f.how.text)}</p>
        <dl class="how-facts">
          <dt>Faktas iki vakar</dt><dd class="num">${esc(eur(f.fact_to_date))}</dd>
          <dt>Likusios prekybos dienos</dt><dd class="num">${esc(int(f.remaining_workdays))}</dd>
          <dt>Mėnesio biudžetas</dt><dd class="num">${esc(eur(f.budget))}</dd>
        </dl>
        ${avgs.length ? `<table class="how-table"><thead><tr><th>Savaitės diena</th><th class="num">4 sav. vidurkis</th><th class="num">Liko dienų</th></tr></thead><tbody>
          ${avgs.map((w) => `<tr><td>${esc(w.weekday)}</td><td class="num">${esc(eur(w.avg_revenue))}</td><td class="num">${esc(int(w.days_left))}</td></tr>`).join("")}</tbody></table>` : ""}
      </div>
    </div>`;
  return cells + fc;
}

const SEV = { high: ["ph-warning-octagon", "Skubu"], medium: ["ph-warning", "Svarbu"], low: ["ph-info", "Žinoti"] };
export function renderActions(actions) {
  if (!actions.length) return `<li class="act-empty"><i class="ph ph-check-circle" aria-hidden="true"></i>Šiandien nieko skubaus. Visi rodikliai normos ribose.</li>`;
  return actions.map((a) => {
    const [icon, word] = SEV[a.severity] || SEV.medium;
    return `<li><a class="act sev-${esc(a.severity)}" href="${esc(a.link)}" data-testid="action" data-link="${esc(a.link)}">
      <i class="ph ${icon} act-ic" aria-hidden="true"></i>
      <span class="act-text"><b>${esc(a.title)}</b><small>${esc(a.reason)}</small></span>
      <span class="act-side"><span class="sr-only">${word}. </span><span class="act-src">${esc(a.source)}</span><i class="ph ph-caret-right" aria-hidden="true"></i></span>
    </a></li>`;
  }).join("");
}

/* ---------- Kaštai: grupė -> tiekėjai -> dokumentai (/costs) ---------- */
const costDelta = (d) => (isNum(d) ? `<span class="delta ${d > 0.5 ? "bad" : d < -0.5 ? "good" : ""}">${signed(d)}</span>` : "");
export function renderCostGroups(c) {
  if (!c.groups.length) return emptyBox("Kaštų nėra", "Šiam laikotarpiui ir filialui kaštų įrašų nerasta.");
  const max = Math.max(...c.groups.map((g) => g.amount));
  const rows = [...c.groups].sort((a, b) => b.amount - a.amount).map((g) => `
    <li><button type="button" class="cost-row" data-group="${esc(g.key)}" data-label="${esc(g.label)}" data-testid="cost-group">
      <span class="cost-name"><b>${esc(g.label)}</b><small>${esc(g.source)}, ${esc(pct(g.share_pct, 0))} visų</small></span>
      <span class="cost-bar">${inlineBar(g.amount, max, SERIES.vln)}</span>
      <span class="cost-amt num">${esc(eur(g.amount))}</span>
      <span class="cost-delta num">${costDelta(g.delta_pct)}</span>
      <i class="ph ph-caret-right" aria-hidden="true"></i>
    </button></li>`).join("");
  return `<ul class="cost-rows">${rows}</ul><div class="cost-total"><span>Iš viso</span><span class="num" data-testid="costs-total">${esc(eur(c.total))}</span><span class="num">${costDelta(c.change_pct)}</span></div>`;
}
export function renderSuppliers(d) {
  if (!d.suppliers.length) return emptyBox("Tiekėjų nėra", "Šioje grupėje dokumentų nerasta.");
  const max = Math.max(...d.suppliers.map((s) => s.amount));
  return `<ul class="cost-rows">${d.suppliers.map((s) => `
    <li><button type="button" class="cost-row" data-supplier="${esc(s.key)}" data-label="${esc(s.name)}" data-testid="cost-supplier">
      <span class="cost-name"><b>${esc(s.name)}</b><small>${esc(int(s.docs_count))} dok.</small></span>
      <span class="cost-bar">${inlineBar(s.amount, max, SERIES.vln)}</span>
      <span class="cost-amt num">${esc(eur(s.amount))}</span>
      <span class="cost-delta num">${costDelta(s.delta_pct)}</span>
      <i class="ph ph-caret-right" aria-hidden="true"></i>
    </button></li>`).join("")}</ul>`;
}
export function renderDocuments(d) {
  if (!d.documents.length) return emptyBox("Dokumentų nėra", "Šiam tiekėjui dokumentų nerasta.");
  return `<div class="table-wrap"><table class="data docs-table" data-testid="cost-docs"><thead><tr><th>Data</th><th>Dokumentas</th><th>Aprašymas</th><th class="num">Suma</th></tr></thead><tbody>
    ${d.documents.map((x) => `<tr data-testid="cost-doc"><td class="mono">${esc(x.date)}</td><td class="mono">${esc(x.number)}</td><td class="wrap">${esc(x.description)}</td><td class="num">${esc(eur2(x.amount))}</td></tr>`).join("")}
  </tbody><tfoot><tr><td colspan="3">Iš viso</td><td class="num">${esc(eur2(d.total))}</td></tr></tfoot></table></div>`;
}

/* ---------- Pardavimų serija (/sales/series) ---------- */
export function seriesTable(points) {
  return `<details class="as-table"><summary>Rodyti lentele</summary><div class="table-wrap"><table class="data"><thead><tr><th>Diena</th><th class="num">Pardavimai</th><th class="num">Bendrasis pelnas</th><th>Tipas</th></tr></thead><tbody>
    ${points.map((p) => `<tr><td class="mono">${esc(p.date)}</td><td class="num">${esc(eur(p.sales))}</td><td class="num">${esc(eur(p.gross_profit))}</td><td>${p.kind === "forecast" ? "prognozė" : p.closed ? esc(p.closed_reason || "nedirbta") : "faktas"}</td></tr>`).join("")}
  </tbody></table></div></details>`;
}
export const salesLegend = () => `<ul class="legend chart-legend"><li><i class="key" style="background:${SERIES.sales}"></i>Pardavimai</li><li><i class="key ln" style="background:${SERIES.profit}"></i>Bendrasis pelnas</li><li><i class="key fc"></i><i class="key dash"></i>Prognozė iki mėnesio galo</li></ul>`;

/* ---------- Biudžetas pagal filialą (/summary budget_by_branch) ---------- */
export function renderBudget(rows) {
  if (!rows.length) return emptyBox("Biudžeto nėra", "Šiam mėnesiui biudžetas nesuvestas.");
  const line = (label, actual, forecast, plan) => {
    const a = plan ? (actual / plan) * 100 : null, f = plan ? (forecast / plan) * 100 : null;
    return `<div class="bud-line"><span class="bud-k">${label}</span>${meter(f, { label: `${label}: prognozė ${pct(f)} biudžeto`, actual: a })}<span class="bud-v num"><b>${esc(eur(actual))}</b> faktas, ${esc(eur(forecast))} prognozė iš ${esc(eur(plan))}</span><span class="bud-p num">${esc(pct(f, 0))}</span></div>`;
  };
  return rows.map((b) => `<div class="bud-branch" data-testid="budget-branch"><h4><i class="key" style="background:${SERIES[b.branch]}"></i>${esc(b.label)}</h4>${line("Apyvarta", b.actual_revenue, b.forecast_revenue, b.budget_revenue)}${line("Bendrasis pelnas", b.actual_gross_profit, b.forecast_gross_profit, b.budget_gross_profit)}</div>`).join("")
    + `<p class="bud-legend"><span class="meter-key actual"></span>faktas iki vakar <span class="meter-key fc"></span>prognozė iki mėnesio galo <span class="meter-mark-key"></span>100 % biudžeto</p>`;
}

/* ---------- Efektyvumas (/efficiency) ---------- */
export function renderEffFigs(e) {
  return `
    <div class="fig fig-main" data-testid="fig-profit-fte"><span class="fig-k">Pelnas vienam FTE</span><b class="fig-v">${esc(eur(e.profit_per_fte))}</b><small>bendrasis pelnas / ${esc(dec1(e.fte))} FTE</small></div>
    <div class="fig"><span class="fig-k">Apyvarta vienam FTE</span><b class="fig-v">${esc(eur(e.kpis.revenue_per_fte))}</b><small>apyvarta / ${esc(dec1(e.fte))} FTE</small></div>
    <div class="fig"><span class="fig-k">Pelnas vienai eilutei</span><b class="fig-v">${esc(eur2(e.profit_per_line))}</b><small>${esc(int(e.lines))} eilučių</small></div>
    <div class="fig"><span class="fig-k">Pelnas per darbo valandą</span><b class="fig-v">${esc(eur2(e.profit_per_hour))}</b><small>${esc(int(e.hours))} val.</small></div>`;
}
const SORT_KEY = { profit: "profit", perHour: "profit_per_hour", hours: "hours" };
export function renderEmployees(emps, sort) {
  if (!emps.length) return `<tbody><tr><td>Darbuotojų duomenų nėra.</td></tr></tbody>`;
  const key = SORT_KEY[sort];
  const sorted = [...emps].sort((a, b) => b[key] - a[key]);
  const max = Math.max(...emps.map((e) => e.profit));
  const th = (k, label) => `<th class="num" aria-sort="${sort === k ? "descending" : "none"}"><button type="button" class="sort" data-sort="${k}" data-testid="sort-${k}">${label}<i class="ph ${sort === k ? "ph-sort-descending" : "ph-arrows-down-up"}" aria-hidden="true"></i></button></th>`;
  return `<thead><tr><th>Darbuotojas</th>${th("profit", "Pelnas")}${th("perHour", "Pelnas per val.")}${th("hours", "Valandos")}<th class="num">Apyvarta</th></tr></thead><tbody>
    ${sorted.map((e, i) => `<tr data-testid="emp-row"><th scope="row"><span class="rank-no" title="Vieta pagal pelną: ${esc(e.rank)}">${i + 1}</span><span class="emp-name"><span class="emp-n">${esc(e.name)}</span><small>${esc(e.branch_label)}</small></span></th>
      <td class="num"><span class="cell-bar">${inlineBar(e.profit, max, SERIES.vln)}${esc(eur(e.profit))}</span></td><td class="num">${esc(eur2(e.profit_per_hour))}</td><td class="num">${esc(int(e.hours))}</td><td class="num">${esc(eur(e.sales))}</td></tr>`).join("")}</tbody>`;
}
export function renderManufacturers(ms) {
  if (!ms.length) return `<tbody><tr><td>Gamintojų duomenų nėra.</td></tr></tbody>`;
  const sorted = [...ms].sort((a, b) => b.margin_pct - a.margin_pct);
  const max = Math.max(...ms.map((m) => m.margin_pct));
  return `<thead><tr><th>Gamintojas</th><th class="num">Apyvarta</th><th class="num">Bendrasis pelnas</th><th class="num">Marža</th></tr></thead><tbody>
    ${sorted.map((m) => `<tr><th scope="row">${esc(m.name)}</th><td class="num">${esc(eur(m.sales))}</td><td class="num">${esc(eur(m.profit))}</td><td class="num"><span class="cell-bar">${inlineBar(m.margin_pct, max, SERIES.vln)}${esc(pct(m.margin_pct))}</span></td></tr>`).join("")}</tbody>`;
}
export function renderBranches(branches) {
  if (!branches.length) return emptyBox("Filialų duomenų nėra", "Palyginimas atsiras, kai bus abiejų filialų duomenys.");
  const order = [...branches].sort((a, b) => (a.key === "vln" ? -1 : b.key === "vln" ? 1 : 0));
  const vals = (k) => order.map((b) => ({ key: b.key, label: b.label, value: b[k] }));
  return smallMultiples([
    { label: "Apyvarta", values: vals("sales"), fmt: eur },
    { label: "Bendrasis pelnas", values: vals("profit"), fmt: eur },
    { label: "Pelnas vienam FTE", values: vals("profit_per_fte"), fmt: eur },
    { label: "Marža", values: vals("margin_pct"), fmt: (v) => pct(v) },
  ]);
}
export const renderNotes = (notes) => (notes.length ? `<ul class="eff-notes">${notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : "");

/* ---------- Darbai ir paštas (/work) ---------- */
export function renderEmails(emails, prio) {
  const rows = emails.filter((m) => prio === "all" || m.priority === prio);
  if (!rows.length) return `<li class="row-empty">${prio === "all" ? "Neatsakytų laiškų nėra." : "Šio prioriteto laiškų nėra."}</li>`;
  return rows.map((m) => `<li class="mail-row" data-testid="email" data-prio="${esc(m.priority)}">
    <span class="prio prio-${esc(m.priority)}">${esc(m.priority_label)}</span>
    <span class="mail-main"><b>${esc(m.subject)}</b><small>${esc(m.from)}, ${esc(m.sender_kind)}</small></span>
    <span class="wait num ${m.waiting_hours >= 24 ? "late" : ""}"><i class="ph ph-clock" aria-hidden="true"></i>laukia ${esc(int(m.waiting_hours))} val.</span>
  </li>`).join("");
}
const daysLeftText = (t) => (t.overdue ? `vėluoja ${Math.abs(t.days_left)} d.` : t.days_left === 0 ? "šiandien" : `po ${t.days_left} d.`);
export function renderTasks(tasks) {
  if (!tasks.length) return `<li class="row-empty">Užduočių nėra.</li>`;
  return tasks.map((t) => `<li class="task-row ${t.overdue ? "overdue" : ""}" data-testid="task"><i class="ph ${t.done ? "ph-check-circle" : t.overdue ? "ph-warning-circle" : "ph-circle"}" aria-hidden="true"></i>
      <span>${esc(t.title)}</span><time class="num" datetime="${esc(t.due)}">${esc(daysLeftText(t))}, ${esc(t.due)}</time></li>`).join("");
}

/* ---------- Operacijos (/operations) ---------- */
export function renderOps(d) {
  const { calls: c, deposits: dep, returns: ret, routes: r, hr, accounting: acc } = d;
  const row = (o, { icon, title, value, detail, extra = "", href = "", testid }) => `
    <div class="op" data-testid="${testid}">
      <i class="ph ${icon} op-ic" aria-hidden="true"></i>
      <div class="op-main"><h3>${esc(title)}</h3><b class="op-v">${value}</b><p>${detail}</p>${extra}</div>
      <div class="op-foot">${meta(o)}${href ? `<a class="btn small" href="${esc(href)}" data-testid="${testid}-link">Atidaryti<i class="ph ph-arrow-right" aria-hidden="true"></i></a>` : ""}</div>
    </div>`;
  const leave = hr.on_vacation;
  return [
    row(c, { icon: "ph-phone-x", title: "Praleisti skambučiai", testid: "ops-calls",
      value: `${esc(int(c.missed))} <small>iš ${esc(int(c.calls_total))} (${esc(pct(c.missed_pct))})</small>`,
      detail: `Galima prarasta apyvarta apie <b>${esc(eur(c.lost_revenue))}</b>. ${c.last_day ? `Paskutinė prekybos diena (${esc(c.last_day.date)}): praleisti ${esc(int(c.last_day.missed))}.` : ""}`,
      extra: `<details class="assume"><summary>Prielaidos</summary><p>Prarasta apyvarta = praleisti skambučiai × konversija ${esc(pct(c.assumptions.conversion_pct, 0))} × vidutinis užsakymas ${esc(eur(c.assumptions.avg_order))}. ${esc(c.assumptions.note)}</p></details>` }),
    row(dep, { icon: "ph-hand-coins", title: "Depozitai", testid: "ops-deposits",
      value: `${esc(eur(dep.value))} <small>${esc(int(dep.count))} vnt. laukia grąžinimo</small>`,
      detail: `Ilgiau nei ${esc(int(dep.overdue_days))} d. laukia <b>${esc(int(dep.overdue_count))}</b> už ${esc(eur(dep.overdue_value))}.` }),
    row(ret, { icon: "ph-arrow-u-up-left", title: "Grąžinimai", testid: "ops-returns",
      value: `${esc(pct(ret.pct))} <small>apyvartos</small>`, detail: `${esc(int(ret.count))} grąžinimai už ${esc(eur(ret.value))}, ${esc(pct(ret.pct_of_orders))} užsakymų.` }),
    row(r, { icon: "ph-truck", title: "Maršrutai", testid: "ops-routes",
      value: `${esc(int(r.stops))} <small>sustojimų</small>`, detail: `${esc(int(r.km))} km, ${esc(dec1(r.km_per_stop))} km sustojimui. Vėlavo ${esc(int(r.late))}, laiku ${esc(pct(r.on_time_pct))}.` }),
    row(hr, { icon: "ph-signature", title: "Personalo prašymai", testid: "ops-hr", href: hr.link,
      value: `${esc(int(hr.pending_signatures))} <small>laukia tavo parašo</small>`,
      detail: leave.length ? `Šią savaitę atostogauja: ${leave.map((v) => esc(v.name)).join(", ")}.` : "Šią savaitę niekas neatostogauja." }),
    row(acc, { icon: "ph-calculator", title: "Apskaita", testid: "ops-accounting", href: acc.link,
      value: `${esc(int(acc.held_invoices))} <small>sąskaitos sulaikytos</small>`,
      detail: `Mėnesio uždarymas: ${esc(int(acc.close_errors))} klaidos. Nesuderinti banko įrašai: ${esc(int(acc.bank_unmatched))}.` }),
  ].join("");
}

/* ---------- Šaltiniai (/sources) ---------- */
export function renderSources(sources) {
  if (!sources.length) return `<li class="row-empty">Šaltinių sąrašas tuščias.</li>`;
  return sources.map((s) => `<li class="src-row" data-testid="source">
      <span class="src-main"><b>${esc(s.name)}</b><small>${esc(s.covers)}</small></span>
      <span class="src-state">${statusChip(s.status, s.status_label)}<span class="src-real">Tikra jungtis: ${statusChip(s.real.status)}${s.real.label !== STATUS_LABEL[s.real.status] ? `<small>${esc(s.real.label)}</small>` : ""}</span></span>
      <span class="src-todo">${s.todo ? `<i class="ph ph-key" aria-hidden="true"></i><span>${esc(s.todo)}${s.contact ? ` <span class="src-contact">Iš ko: ${esc(s.contact)}</span>` : ""}</span>` : `<span class="muted">Nieko daryti nereikia</span>`}</span>
    </li>`).join("");
}
