/* Apžvalgos grafikai: rankomis rašyti inline SVG (be bibliotekų, be CDN).
   Taisyklės (dataviz): plonos žymės, 4px suapvalintas stulpelio galas prie duomenų, 2px linijos,
   viena ašis, plaukinis tinklelis, tekstas tik teksto spalvomis, kiekvienas grafikas turi lentelės alternatyvą. */

export const SERIES = { sales: "#c5ccd6", profit: "#c4420e", forecast: "#5f6773", vln: "#2f64a8", kns: "#d9601a" }; // ds.css: --series-muted, --accent-strong, --ink-3, --series-vln, --series-kns

const isFc = (p) => p.kind === "forecast";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/** Švarios ašies žymos: 0, 5 000, 10 000 ... (iki ~4 žymų). */
export function niceTicks(max, count = 4) {
  if (!(max > 0)) return [0, 1];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const top = Math.ceil(max / step) * step;
  const out = [];
  for (let v = 0; v <= top + step / 2; v += step) out.push(v);
  return out;
}

/** Stulpelio kelias: 4px apvalinimas viršuje (duomenų gale), kvadratinis prie bazės. */
function colPath(x, y, w, h, r = 4) {
  if (h <= 0) return "";
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

/** Horizontali juosta: apvalinta dešinėje, kvadratinė kairėje (bazė). */
function barPath(x, y, w, h, r = 4) {
  if (w <= 0) return "";
  const rr = Math.min(r, h / 2, w);
  return `M${x},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h - rr}Q${x + w},${y + h} ${x + w - rr},${y + h}H${x}Z`;
}

/* ---------- Dienos stulpeliai + pelno linija + prognozė (brūkšninė) ---------- */
/**
 * points: /api/dash/sales/series points[] ({date, sales, gross_profit, kind, closed, closed_reason}).
 * Faktas: stulpeliai (pardavimai) + linija (bendrasis pelnas). Prognozė (kind=forecast): tušti brūkšniniai stulpeliai
 * ir brūkšninė pelno linija. X ašis pagal datą: sekmadieniai ir šventės prognozėje lieka tuščios vietos.
 */
export function salesChart(host, points, { fmt, fmtTick, fmtDay, tip }) {
  host.innerHTML = "";
  if (!points.length) { host.innerHTML = `<p class="chart-empty">Šiam laikotarpiui duomenų nėra.</p>`; return; }
  const W = Math.max(320, host.clientWidth || 600), H = 220, padL = 66, padR = 12, padT = 12, padB = 26;
  const iw = W - padL - padR, ih = H - padT - padB;
  const max = Math.max(...points.map((p) => p.sales), 1);
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1];
  const DAY = 86400000, t0 = Date.parse(`${points[0].date}T12:00:00`);
  const idx = points.map((p) => Math.round((Date.parse(`${p.date}T12:00:00`) - t0) / DAY));
  const slots = idx[idx.length - 1] + 1;
  const band = iw / slots;
  const bw = Math.max(2, Math.min(24, band - 2)); // 2px oro tarp stulpelių, ne daugiau 24px
  const y = (v) => padT + ih - (v / top) * ih;
  const cx = (i) => padL + band * idx[i] + band / 2;

  let g = "";
  for (const t of ticks) g += `<line class="grid" x1="${padL}" x2="${W - padR}" y1="${y(t)}" y2="${y(t)}"/><text class="tick" x="${padL - 8}" y="${y(t) + 4}" text-anchor="end">${esc(fmtTick(t))}</text>`;
  // x žymos: kas ~7 dienos, kad nesusigrūstų
  const every = Math.max(1, Math.round(points.length / Math.max(3, Math.floor(iw / 70))));
  points.forEach((p, i) => { if (i % every === 0) g += `<text class="tick" x="${cx(i)}" y="${H - 8}" text-anchor="middle">${esc(fmtDay(p.date, true))}</text>`; });

  let bars = "";
  points.forEach((p, i) => {
    const x = cx(i) - bw / 2, top = y(p.sales), h = padT + ih - top;
    bars += isFc(p)
      ? `<path class="col-fc" d="${colPath(x + 0.5, top + 0.5, bw - 1, Math.max(0, h - 0.5))}"/>`
      : `<path class="col" d="${colPath(x, top, bw, h)}" fill="${SERIES.sales}"/>`;
  });

  // pelno linija: ištisinė iki vakar, brūkšninė (prognozė) iki mėnesio galo
  const fact = points.map((p, i) => [i, p]).filter(([, p]) => !isFc(p));
  const fc = points.map((p, i) => [i, p]).filter(([, p]) => isFc(p));
  const line = (arr, key) => arr.map(([i, p], k) => `${k ? "L" : "M"}${cx(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join("");
  let lines = fact.length ? `<path class="ln" d="${line(fact, "gross_profit")}" stroke="${SERIES.profit}"/>` : "";
  if (fc.length) {
    const join = fact.length ? [fact[fact.length - 1], ...fc] : fc;
    lines += `<path class="ln dash" d="${line(join, "gross_profit")}" stroke="${SERIES.profit}"/>`;
  }
  const lastF = fact[fact.length - 1];
  if (lastF) lines += `<circle class="dot" cx="${cx(lastF[0])}" cy="${y(lastF[1].gross_profit)}" r="4" fill="${SERIES.profit}"/>`;

  // nematomi taikiniai per visą juostos aukštį (didesni už žymę)
  let hits = "";
  points.forEach((p, i) => { hits += `<rect class="hit" x="${padL + band * idx[i]}" y="${padT}" width="${band}" height="${ih}" data-i="${i}"/>`; });

  host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="Pardavimų ir pelno grafikas per dieną, prognozė brūkšnine linija">${g}${bars}${lines}<line class="cross" id="cross" x1="0" x2="0" y1="${padT}" y2="${padT + ih}" visibility="hidden"/>${hits}</svg>`;
  const svg = host.querySelector("svg"), cross = svg.querySelector("#cross");
  const show = (el) => {
    const i = Number(el.dataset.i), p = points[i];
    cross.setAttribute("x1", cx(i)); cross.setAttribute("x2", cx(i)); cross.setAttribute("visibility", "visible");
    const rows = isFc(p)
      ? `<span><i class="key fc"></i>Pardavimų prognozė</span><b>${esc(fmt(p.sales))}</b><span><i class="key dash"></i>Pelno prognozė</span><b>${esc(fmt(p.gross_profit))}</b>`
      : p.closed ? `<span>Nedirbta</span><b>${esc(p.closed_reason || "")}</b>`
      : `<span><i class="key" style="background:${SERIES.sales}"></i>Pardavimai</span><b>${esc(fmt(p.sales))}</b><span><i class="key ln" style="background:${SERIES.profit}"></i>Bendrasis pelnas</span><b>${esc(fmt(p.gross_profit))}</b>`;
    tip.show(`<strong>${esc(fmtDay(p.date))}</strong><div class="tip-grid">${rows}</div>`, el);
  };
  const hide = () => { cross.setAttribute("visibility", "hidden"); tip.hide(); };
  svg.querySelectorAll(".hit").forEach((r) => {
    r.addEventListener("pointerenter", () => show(r));
    r.addEventListener("pointerleave", hide);
  });
}

/* ---------- Biudžeto matuoklis: faktas (užpildas) prieš biudžetą (žyma) ---------- */
/** pct: prognozė (arba faktas) % biudžeto; actual (nebūtina): faktas iki vakar % biudžeto, piešiamas tamsiau. */
export function meter(pct, { label = "", actual = null } = {}) {
  const scale = (v) => Math.min(100, (Math.max(0, Math.min(130, Number(v) || 0)) / 130) * 100); // 130 % skalė: viršijimas matosi
  const p = Number(pct) || 0;
  const cls = p >= 100 ? "ok" : p >= 90 ? "near" : "low";
  const act = actual === null ? "" : `<span class="meter-act" style="width:${scale(actual).toFixed(1)}%"></span>`;
  return `<span class="meter ${cls}${actual === null ? "" : " split"}" role="img" aria-label="${esc(label)}"><span class="meter-fill" style="width:${scale(p).toFixed(1)}%"></span>${act}<span class="meter-mark" style="left:${scale(100).toFixed(1)}%"></span></span>`;
}

/* ---------- Maži daugkartiniai: kiekvienam rodikliui atskira skalė ---------- */
/** metrics: [{label, values:[{key,label,value}], fmt}] */
export function smallMultiples(metrics) {
  return metrics.map((m) => {
    const max = Math.max(...m.values.map((v) => v.value), 1);
    const W = 220, rowH = 22, gap = 8, labelW = 64, H = m.values.length * (rowH + gap) - gap;
    const bars = m.values.map((v, i) => {
      const y = i * (rowH + gap) + 4, w = Math.max(0, ((W - labelW - 4) * v.value) / max);
      return `<text class="tick" x="0" y="${y + 12}">${esc(v.label)}</text><path d="${barPath(labelW, y, w, 14)}" fill="${SERIES[v.key] || SERIES.vln}"/>`;
    }).join("");
    const vals = m.values.map((v) => `<li><i class="key" style="background:${SERIES[v.key] || SERIES.vln}"></i>${esc(v.label)} <b>${esc(m.fmt(v.value))}</b></li>`).join("");
    return `<figure class="mult"><figcaption>${esc(m.label)}</figcaption><svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" aria-hidden="true">${bars}</svg><ul class="mult-vals">${vals}</ul></figure>`;
  }).join("");
}

/* ---------- Vidinė juosta lentelės langelyje (be fono takelio) ---------- */
export function inlineBar(value, max, color = SERIES.vln) {
  const w = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return `<span class="ibar" aria-hidden="true"><span style="width:${w.toFixed(1)}%;background:${color}"></span></span>`;
}

/* ---------- Patarimo langelis ---------- */
export function makeTip(el) {
  return {
    show(html, anchor) {
      el.innerHTML = html;
      el.hidden = false;
      const r = anchor.getBoundingClientRect(), t = el.getBoundingClientRect();
      let left = r.left + r.width / 2 - t.width / 2;
      left = Math.max(8, Math.min(window.innerWidth - t.width - 8, left));
      let top = r.top - t.height - 8;
      if (top < 8) top = r.bottom + 8;
      el.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
    },
    hide() { el.hidden = true; },
  };
}
