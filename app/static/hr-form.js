"use strict";
/* Personalo prašymai: naujas prašymas ir prašymo detalės (žr. hr.js). */

/* ---------- Naujas prašymas ---------- */
const form = $("#req-form");
const reqType = () => form.elements.type.value;
const leaveKind = () => form.elements.leave_kind.value;

function syncType() {
  const trip = reqType() === "trip";
  $("#kind-field").hidden = trip;
  $("#trip-fields").hidden = !trip;
  syncBalanceLine();
  updateWorkdays();
}
function syncBalanceLine() {
  const el = $("#form-balance");
  const show = reqType() === "leave" && leaveKind() === "annual" && me?.balance !== undefined && me?.balance !== null;
  el.hidden = !show;
  if (show) el.innerHTML = `Jūsų likutis: <b data-testid="form-balance">${esc(fmtDays(me.balance))}</b>`;
}
form.addEventListener("change", (ev) => {
  if (ev.target.name === "type") syncType();
  if (ev.target.name === "leave_kind") { syncBalanceLine(); updateWorkdays(); }
  if (ev.target.name === "date_from" || ev.target.name === "date_to") updateWorkdays();
});
form.addEventListener("input", (ev) => {
  if (ev.target.matches("input, select, textarea")) ev.target.removeAttribute("aria-invalid");
});

async function updateWorkdays() {
  const out = $("#dates-msg");
  const from = $("#date-from").value, to = $("#date-to").value;
  const seq = ++wdSeq;
  if (!from || !to) { out.innerHTML = `<span class="muted">Pasirinkite datas, suskaičiuosime darbo dienas.</span>`; return; }
  if (to < from) {
    $("#date-to").setAttribute("aria-invalid", "true");
    out.innerHTML = `<span class="bad" data-testid="dates-error"><i class="ph ph-warning-circle" aria-hidden="true"></i>Data „iki“ negali būti ankstesnė už „nuo“.</span>`;
    return;
  }
  $("#date-to").removeAttribute("aria-invalid");
  out.innerHTML = `<span class="muted">Skaičiuojama</span>`;
  try {
    const d = await api("/workdays", { method: "POST", body: { date_from: from, date_to: to } });
    if (seq !== wdSeq) return;
    const n = Number(d.work_days);
    const hol = list(d.holidays).map((h) => `${fmtDate(h.date)}${h.name ? ` (${esc(h.name)})` : ""}`);
    let h = `<span class="wd" data-testid="workdays">${esc(n)} darbo d.</span>`;
    if (from < isoOf(new Date())) h += `<span class="warn"><i class="ph ph-warning" aria-hidden="true"></i>Data jau praėjo. Demo tai leidžia.</span>`;
    if (reqType() === "leave" && leaveKind() === "annual" && Number.isFinite(Number(me?.balance)) && n > Number(me.balance))
      h += `<span class="bad"><i class="ph ph-warning-circle" aria-hidden="true"></i>Nepakanka likučio: liko ${esc(fmtDays(me.balance))}</span>`;
    if (hol.length) h += `<span class="hol">Šventinės dienos nesiskaičiuoja: ${hol.join(", ")}</span>`;
    out.innerHTML = h;
  } catch (e) {
    if (seq !== wdSeq || e.status === 401) return;
    out.innerHTML = `<span class="bad"><i class="ph ph-warning-circle" aria-hidden="true"></i>${esc(e.message)}</span>`;
  }
}

async function renderNew() {
  form.reset();
  $("#form-error").hidden = true;
  $$("[aria-invalid]", form).forEach((el) => el.removeAttribute("aria-invalid"));
  syncType();
  const sel = $("#substitute");
  sel.innerHTML = `<option value="">Kraunama</option>`;
  sel.disabled = true;
  try {
    const people = list(await api("/colleagues"));
    const groups = new Map();
    for (const p of people) {
      const dep = p.department || "Kiti";
      if (!groups.has(dep)) groups.set(dep, []);
      groups.get(dep).push(p);
    }
    sel.innerHTML = `<option value="">Pasirinkite kolegą</option>` + [...groups].map(([dep, ps]) =>
      `<optgroup label="${esc(dep)}">${ps.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}${p.position ? `, ${esc(p.position)}` : ""}</option>`).join("")}</optgroup>`).join("");
    sel.disabled = false;
  } catch (e) {
    if (e.status === 401) return;
    sel.innerHTML = `<option value="">Nepavyko įkelti kolegų</option>`;
    showFormError([e.message]);
  }
}

function showFormError(msgs) {
  const box = $("#form-error");
  box.innerHTML = `<i class="ph ph-warning-circle" aria-hidden="true"></i><div>${msgs.length > 1 ? `<b>Patikrinkite formą:</b><ul>${msgs.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : esc(msgs[0])}</div>`;
  box.hidden = false;
  box.focus();
  box.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const btn = $("#submit-req");
  if (btn.getAttribute("aria-busy") === "true") return;
  const f = form.elements, trip = reqType() === "trip";
  const errs = [];
  const bad = (el, msg) => { el.setAttribute("aria-invalid", "true"); errs.push(msg); };
  if (!f.date_from.value) bad(f.date_from, "Nurodykite pradžios datą.");
  if (!f.date_to.value) bad(f.date_to, "Nurodykite pabaigos datą.");
  if (f.date_from.value && f.date_to.value && f.date_to.value < f.date_from.value) bad(f.date_to, "Data „iki“ negali būti ankstesnė už „nuo“.");
  if (!f.substitute_id.value) bad(f.substitute_id, "Pasirinkite pavaduojantį kolegą.");
  if (trip && !f.destination.value.trim()) bad(f.destination, "Nurodykite, kur vykstate.");
  if (trip && !f.purpose.value.trim()) bad(f.purpose, "Nurodykite komandiruotės tikslą.");
  if (errs.length) { showFormError(errs); return; }
  $("#form-error").hidden = true;

  const body = {
    type: trip ? "trip" : "leave", date_from: f.date_from.value, date_to: f.date_to.value,
    substitute_id: Number(f.substitute_id.value), comment: f.comment.value.trim() || undefined,
  };
  if (trip) { body.destination = f.destination.value.trim(); body.purpose = f.purpose.value.trim(); }
  else body.leave_kind = leaveKind();

  btn.setAttribute("aria-busy", "true");
  btn.disabled = true;
  const label = btn.innerHTML;
  btn.innerHTML = `<i class="ph ph-circle-notch spin" aria-hidden="true"></i>Pateikiama`;
  try {
    const res = await api("/requests", { method: "POST", body });
    const r = normReq(res);
    const link = signHref(r.signUrl) || mySignLink(r);
    const warns = list(res.warnings);
    if (warns.length) toast(warns.join(" "));
    refreshCount();
    location.hash = link || `#/request/${r.id}`;
  } catch (e) {
    if (e.status !== 401) showFormError([e.message]);
  } finally {
    btn.removeAttribute("aria-busy");
    btn.disabled = false;
    btn.innerHTML = label;
  }
});

/* ---------- Prašymo detalės ---------- */
function timelineHtml(signers, docStatus) {
  const cur = currentSigner(signers, docStatus);
  return `<ol class="timeline">${signers.map((s) => {
    const st = signerState(s, cur);
    const when = s.signedAt ? `<time>${fmtTs(s.signedAt)}</time>` : "";
    const sub = st === "signed" ? `pasirašė ${when}` : st === "declined" ? `atsisakė ${when}` : st === "current" ? "dabar eilė pasirašyti" : "laukia savo eilės";
    return `<li class="st-${st}" data-testid="signer-${esc(s.role)}" data-name="${esc(s.name)}" data-state="${st}">
      <i class="ph ${STATE_ICON[st]}" aria-hidden="true"></i>
      <div><b>${esc(s.name)}</b><small>${esc(ROLE_LT[s.role] || s.role)}, ${sub}</small>${s.reason ? `<p class="reason">Priežastis: ${esc(s.reason)}</p>` : ""}</div>
    </li>`;
  }).join("")}</ol>`;
}

function docsHtml(r) {
  if (!r.documents.length) return `<p class="hint">Dokumentų dar nėra.</p>`;
  return r.documents.map((d) => `<div class="doc-group"><h3>${esc(d.title)}</h3><div class="docs">
      <a href="${esc(d.pdfUrl || `${API}/documents/${encodeURIComponent(d.id)}.pdf`)}" download data-testid="pdf-original"><i class="ph ph-file-pdf" aria-hidden="true"></i>Atsisiųsti PDF<small>originalas</small></a>
      ${d.status === "completed" || d.signedUrl ? `<a href="${esc(d.signedUrl || `${API}/documents/${encodeURIComponent(d.id)}.pdf?signed=1`)}" download data-testid="pdf-signed"><i class="ph ph-seal-check" aria-hidden="true"></i>Atsisiųsti pasirašytą<small>su parašų lapu</small></a>` : ""}
    </div></div>`).join("");
}

function auditHtml(items) {
  if (!items.length) return `<p class="audit-list">Įrašų dar nėra.</p>`;
  return `<ol class="audit-list">${items.map((a) => {
    const who = a.actor_name ?? nameOf(a.actor);
    return `<li><time>${fmtTs(a.ts ?? a.at ?? a.created_at)}</time><div>${esc(AUDIT_LT[a.action] || a.action)}${who ? `: ${esc(who)}` : ""}${a.detail ? `<small>${esc(a.detail)}</small>` : ""}</div></li>`;
  }).join("")}</ol>`;
}

function canCancel(r) {
  if (typeof r.canCancel === "boolean") return r.canCancel;
  if (r.employeeId !== myId() || r.status !== "pending_signatures") return false;
  return !r.signers.some((s) => s.role === "manager" && s.status === "signed");
}

async function renderRequest(seq, id) {
  const box = $("#request-body");
  box.innerHTML = `<div class="sk sk-line" style="width:40%" aria-hidden="true"></div>${skeletonCards(2)}`;
  let r;
  try { r = normReq(await api(`/requests/${encodeURIComponent(id)}`)); }
  catch (e) {
    if (stale(seq) || e.status === 401) return;
    box.innerHTML = e.status === 404 || e.status === 403
      ? stateHtml("ph-lock-simple", "Prašymas nerastas", "Jo nėra arba neturite teisės jo matyti.", `<a class="btn" href="#/mine">Grįžti į sąrašą</a>`)
      : errorHtml(e);
    box.querySelector("[data-retry]")?.addEventListener("click", () => renderRequest(++navSeq, id));
    return;
  }
  if (stale(seq)) return;
  const reqDoc = r.documents.find((d) => d.kind === "request");
  const orderDoc = r.documents.find((d) => d.kind === "order");
  const signLink = mySignLink(r);
  const orderCur = orderDoc ? currentSigner(orderDoc.signers, orderDoc.status) : null;
  const orderMine = orderCur && orderCur.employeeId === myId();
  const orderLink = orderDoc && orderMine ? docSignLink(orderDoc) : "";
  const iSignOrder = Boolean(orderLink);

  let notices = "";
  if (r.status === "rejected") {
    const who = r.declinedBy || r.signers.find((s) => s.status === "declined")?.name || "";
    const why = r.reason || r.signers.find((s) => s.status === "declined")?.reason || "";
    notices += `<div class="notice rejected" role="status"><i class="ph ph-x-circle" aria-hidden="true"></i><span>Prašymas atmestas${who ? `: ${esc(who)}` : ""}.${why ? ` Priežastis: ${esc(why)}` : ""}</span></div>`;
  }
  if (r.status === "cancelled") notices += `<div class="notice"><i class="ph ph-prohibit" aria-hidden="true"></i><span>Prašymas atšauktas. Pasirašyti jo nebereikia.</span></div>`;
  if (r.status === "approved" && r.type === "leave") notices += `<div class="notice ok"><i class="ph ph-seal-check" aria-hidden="true"></i><span>Visi pasirašė. ${r.leaveKind === "annual" ? "Likutis atnaujintas, " : ""}tabelyje pažymėta.</span></div>`;
  if (r.type === "trip" && r.orderStatus === "pending_order")
    notices += `<div class="notice order" data-testid="order-pending"><span><i class="ph ph-stamp" aria-hidden="true"></i><span><b>Įsakymas laukia parašo</b><br>${orderCur ? `Pasirašo ${esc(orderCur.name)}.` : "Vadovas pasirašys įsakymą."} Tada komandiruotė pažymima tabelyje.</span></span>
      ${iSignOrder ? `<a class="btn primary" href="${orderLink}"><i class="ph ph-signature" aria-hidden="true"></i>Pasirašyti įsakymą</a>` : ""}</div>`;
  if (r.type === "trip" && r.orderStatus === "order_signed") notices += `<div class="notice ok"><i class="ph ph-seal-check" aria-hidden="true"></i><span>Įsakymas pasirašytas. Tabelyje pažymėta K.</span></div>`;

  const reqLink = reqDoc ? docSignLink(reqDoc) : signLink;
  const mySignBtn = reqLink && !iSignOrder
    ? `<a class="btn primary" href="${reqLink}" data-testid="sign-here"><i class="ph ph-signature" aria-hidden="true"></i>Pasirašyti</a>` : "";
  const facts = [
    ["Darbuotojas", esc(r.employeeName)],
    ["Laikotarpis", fmtRange(r.dateFrom, r.dateTo)],
    ["Darbo dienų", r.workDays !== undefined ? `<span class="mono">${esc(r.workDays)}</span>` : ""],
    ["Pavaduoja", esc(r.substituteName)],
    r.type === "trip" ? ["Vieta", esc(r.destination)] : null,
    r.type === "trip" ? ["Tikslas", esc(r.purpose)] : null,
    r.comment ? ["Komentaras", esc(r.comment)] : null,
    reqDoc?.sha256 ? ["Dokumento sha256", `<span class="mono">${esc(reqDoc.sha256.slice(0, 16))}…</span>`] : null,
  ].filter((x) => x && x[1]);

  box.innerHTML = `
    <div class="detail-head">
      <div><h1 id="req-title">${esc(reqTitle(r))}</h1><p>${fmtRange(r.dateFrom, r.dateTo)}${r.workDays !== undefined ? `, ${esc(r.workDays)} darbo d.` : ""}</p></div>
      <div>${statusHtml(r)}</div>
    </div>
    ${notices}
    <div class="detail-grid">
      <div class="panel">
        <div class="panel-pad">
          <h2>Pasirašymo eilė</h2>
          ${timelineHtml(r.signers, reqDoc?.status || (r.status === "pending_signatures" ? "pending" : "done"))}
          ${orderDoc ? `<h2 style="margin-top:20px">Įsakymas</h2>${timelineHtml(orderDoc.signers, orderDoc.status)}` : ""}
          ${mySignBtn ? `<div class="req-actions">${mySignBtn}</div>` : ""}
        </div>
        <details class="audit" data-testid="audit"><summary><i class="ph ph-list-checks" aria-hidden="true"></i>Audito žurnalas<i class="ph ph-caret-right" aria-hidden="true"></i></summary>${auditHtml(r.audit)}</details>
      </div>
      <div class="panel panel-pad">
        <h2>Duomenys</h2>
        <dl class="facts">${facts.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>
        <h2 style="margin-top:20px">Dokumentai</h2>
        ${docsHtml(r)}
        ${canCancel(r) ? `<div class="cancel-row"><button class="btn danger" type="button" id="cancel-req"><i class="ph ph-prohibit" aria-hidden="true"></i>Atšaukti prašymą</button></div>` : ""}
      </div>
    </div>`;
  $(".back", $("#view-request")).setAttribute("href", r.employeeId === myId() ? "#/mine" : "#/to-sign");
  $("#cancel-req")?.addEventListener("click", async () => {
    const ok = await confirmDialog({ title: "Atšaukti prašymą?", text: "Pasirašantieji nebegalės jo pasirašyti. Jei reikės, pateiksite naują.", ok: "Atšaukti prašymą", cancel: "Palikti" });
    if (!ok) return;
    try {
      await api(`/requests/${encodeURIComponent(r.id)}/cancel`, { method: "POST", body: {} });
      toast("Prašymas atšauktas");
      refreshCount();
      renderRequest(++navSeq, r.id);
    } catch (e) { if (e.status !== 401) toast(e.message, true); }
  });
}
