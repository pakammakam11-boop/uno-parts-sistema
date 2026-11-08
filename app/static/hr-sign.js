"use strict";
/* Personalo prašymai: demo pasirašymas, tabelis, laiškai, dialogas ir paleidimas (žr. hr.js). Įkeliamas paskutinis. */

/* ---------- Demo Smart-ID pasirašymas ---------- */
function normSign(info) {
  const doc = normDoc(info.document || {});
  const signers = list(info.signers).map(normSigner).sort((a, b) => a.sequence - b.sequence);
  const req = info.request ? normReq(info.request) : null;
  return {
    doc, signers, req,
    requestId: info.document?.request_id,
    canSign: Boolean(info.can_sign),
    code: info.control_code || "",
    why: info.reason || "",
    signer: info.signer ? normSigner(info.signer) : null,
    docStatus: doc.status,
  };
}
function whyNot(s, signerId) {
  if (s.why) return s.why;
  const mine = s.signers.find((x) => String(x.signerId) === String(signerId));
  if (s.docStatus === "completed") return "Dokumentą jau pasirašė visi.";
  if (s.docStatus === "declined" || s.signers.some((x) => x.status === "declined")) return "Dokumentas atmestas, pasirašyti nebereikia.";
  if (s.docStatus === "cancelled") return "Prašymas atšauktas, pasirašyti nebereikia.";
  if (mine?.status === "signed") return "Jūs šį dokumentą jau pasirašėte.";
  if (mine && mine.employeeId !== undefined && mine.employeeId !== myId()) return `Šis parašas skirtas kitam žmogui: ${mine.name}.`;
  const cur = currentSigner(s.signers);
  return cur ? `Dabar eilė pasirašyti: ${cur.name}. Kai ateis jūsų eilė, gausite laišką.` : "Šiuo metu pasirašyti negalima.";
}

async function renderSign(seq, token, signerId) {
  const box = $("#sign-body");
  box.innerHTML = `<div class="panel sid"><div class="sid-body">${skeletonCards(2)}</div></div>`;
  let s;
  try { s = normSign(await api(`/sign/${encodeURIComponent(token)}/${encodeURIComponent(signerId)}`)); }
  catch (e) {
    if (stale(seq) || e.status === 401) return;
    box.innerHTML = e.status === 404 || e.status === 403
      ? stateHtml("ph-lock-simple", "Pasirašymas nerastas", "Nuoroda neteisinga arba skirta kitam darbuotojui.", `<a class="btn" href="#/to-sign">Laukia mano parašo</a>`)
      : errorHtml(e);
    box.querySelector("[data-retry]")?.addEventListener("click", () => renderSign(++navSeq, token, signerId));
    return;
  }
  if (stale(seq)) return;
  const back = s.requestId ? `#/request/${s.requestId}` : "#/to-sign";
  const r = s.req;
  const summary = r ? `${esc(reqTitle(r))}, ${fmtRange(r.dateFrom, r.dateTo)}${r.employeeName ? `<br>${esc(r.employeeName)}` : ""}` : "";
  const cur = currentSigner(s.signers, s.docStatus === "pending" ? "pending" : "done");

  box.innerHTML = `
    <a class="back" href="${back}"><i class="ph ph-arrow-left" aria-hidden="true"></i>Prašymas</a>
    <div class="panel sid" data-testid="sid">
      <div class="sid-band"><i class="ph ph-flask" aria-hidden="true"></i>Demo parašas (imitacija). Tikras Smart-ID nenaudojamas.</div>
      <div class="sid-body">
        <div class="sid-head"><i class="ph ph-fingerprint" aria-hidden="true"></i><div><h1 id="sid-title">Pasirašyti Smart-ID</h1><small>Dokobit pasirašymo imitacija</small></div></div>
        <div class="sid-doc">
          <b>${esc(s.doc.title)}</b>
          ${summary ? `<span>${summary}</span>` : ""}
          ${s.doc.id !== undefined ? `<a href="${API}/documents/${encodeURIComponent(s.doc.id)}.pdf" target="_blank" rel="noopener"><i class="ph ph-file-pdf" aria-hidden="true"></i>Peržiūrėti PDF</a>` : ""}
          ${s.doc.sha256 ? `<span class="sha">sha256 ${esc(s.doc.sha256)}</span>` : ""}
        </div>
        <ul class="signers-mini" aria-label="Pasirašantieji">${s.signers.map((x) => {
          const st = signerState(x, cur);
          return `<li class="st-${st}"><i class="ph ${STATE_ICON[st]}" aria-hidden="true"></i>${esc(ROLE_LT[x.role] || x.role)}: ${esc(x.name)}${String(x.signerId) === String(signerId) ? " (jūs)" : ""}<small>${x.signedAt ? fmtTs(x.signedAt) : STATE_LT[st]}</small></li>`;
        }).join("")}</ul>
        <div id="sid-stage" aria-live="polite"></div>
      </div>
    </div>`;
  const stage = $("#sid-stage");

  if (!s.canSign) {
    stage.innerHTML = `<div class="sid-turn" data-testid="not-your-turn"><i class="ph ph-info" aria-hidden="true"></i><span>${esc(whyNot(s, signerId))}</span></div>
      <div class="sid-actions"><button class="btn primary block" type="button" disabled aria-describedby="sid-why"><i class="ph ph-fingerprint" aria-hidden="true"></i>Pasirašyti Smart-ID (demo)</button>
      <a class="btn ghost block" href="${back}">Grįžti į prašymą</a></div>`;
    stage.querySelector(".sid-turn").id = "sid-why";
    return;
  }

  const actionsHtml = () => `
    ${s.code ? `<div class="code-box"><small>Kontrolinis kodas</small><b data-testid="control-code">${esc(s.code)}</b><small>Telefone turi būti rodomas tas pats kodas</small></div>` : ""}
    <div class="sid-actions">
      <button class="btn primary block" type="button" id="sid-sign"><i class="ph ph-fingerprint" aria-hidden="true"></i>Pasirašyti Smart-ID (demo)</button>
      <button class="btn ghost block" type="button" id="sid-decline">Atsisakyti</button>
    </div>
    <div class="decline-box" id="decline-box" hidden>
      <label for="decline-reason">Kodėl atsisakote? Priežastį matys darbuotojas.</label>
      <textarea id="decline-reason" rows="3" maxlength="500" aria-describedby="decline-msg"></textarea>
      <p class="field-msg" id="decline-msg" role="alert"></p>
      <button class="btn danger block" type="button" id="decline-ok"><i class="ph ph-x-circle" aria-hidden="true"></i>Patvirtinti atsisakymą</button>
    </div>`;
  stage.innerHTML = actionsHtml();

  const finish = (declined, nextName) => {
    stage.innerHTML = `<div class="sid-done${declined ? " declined" : ""}" data-testid="sid-done" tabindex="-1">
      <i class="ph ${declined ? "ph-x-circle" : "ph-check-circle"}" aria-hidden="true"></i>
      <b>${declined ? "Atsisakyta" : "Pasirašyta"}</b>
      <p>${declined ? "Prašymas atmestas. Darbuotojas gaus pranešimą su priežastimi." : nextName ? `Eilė pereina: ${esc(nextName)}.` : "Visi pasirašė."}</p>
      <a class="btn" href="${back}">Grįžti į prašymą</a></div>`;
    stage.firstElementChild.focus();
    refreshCount();
    setTimeout(() => { if (!stale(seq) && location.hash.startsWith("#/sign/")) location.hash = back; }, BACK_AFTER_SIGN_MS);
  };

  $("#sid-sign").addEventListener("click", async () => {
    stage.innerHTML = `${s.code ? `<div class="code-box"><small>Kontrolinis kodas</small><b>${esc(s.code)}</b></div>` : ""}
      <div class="sid-wait" data-testid="sid-wait"><i class="ph ph-circle-notch spin" aria-hidden="true"></i><b>Laukiama patvirtinimo telefone</b><p>Demo režime patvirtinama automatiškai po kelių sekundžių.</p></div>`;
    try {
      const [res] = await Promise.all([
        api(`/sign/${encodeURIComponent(token)}/${encodeURIComponent(signerId)}`, { method: "POST", body: { action: "sign" } }),
        sleep(SIGN_DELAY_MS),
      ]);
      if (stale(seq)) return;
      const mineIdx = s.signers.findIndex((x) => String(x.signerId) === String(signerId));
      const next = res?.next_signer ? nameOf(res.next_signer) : s.signers[mineIdx + 1]?.name;
      finish(false, next);
    } catch (e) {
      if (stale(seq) || e.status === 401) return;
      stage.innerHTML = `<div class="sid-turn" style="background:var(--err-wash);color:var(--err)"><i class="ph ph-warning-circle" aria-hidden="true"></i><span>${esc(e.message)}</span></div>
        <a class="btn block" href="${back}">Grįžti į prašymą</a>`;
    }
  });

  $("#sid-decline").addEventListener("click", () => {
    const boxEl = $("#decline-box");
    boxEl.hidden = false;
    $("#sid-decline").hidden = true;
    $("#decline-reason").focus();
  });
  $("#decline-reason").addEventListener("input", () => { $("#decline-msg").textContent = ""; $("#decline-reason").removeAttribute("aria-invalid"); });
  $("#decline-ok").addEventListener("click", async () => {
    const ta = $("#decline-reason"), reason = ta.value.trim(), btn = $("#decline-ok");
    if (!reason) { ta.setAttribute("aria-invalid", "true"); $("#decline-msg").textContent = "Įrašykite atsisakymo priežastį."; ta.focus(); return; }
    btn.disabled = true;
    try {
      await api(`/sign/${encodeURIComponent(token)}/${encodeURIComponent(signerId)}`, { method: "POST", body: { action: "decline", reason } });
      if (!stale(seq)) finish(true);
    } catch (e) {
      btn.disabled = false;
      if (e.status !== 401) $("#decline-msg").textContent = e.message;
    }
  });
}

/* ---------- Tabelis ---------- */
function monthNow() { return isoOf(new Date()).slice(0, 7); }
function shiftMonth(ym, delta) {
  const [y, m] = ym.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  return isoOf(d).slice(0, 7);
}
function monthLabel(ym) {
  const [y, m] = ym.split("-").map(Number);
  const name = new Date(y, m - 1, 1).toLocaleDateString("lt-LT", { month: "long" });
  return `${name.charAt(0).toUpperCase()}${name.slice(1)} ${y}`;
}
$("#ts-prev").addEventListener("click", () => { location.hash = `#/timesheet/${shiftMonth(tsMonth || monthNow(), -1)}`; });
$("#ts-next").addEventListener("click", () => { location.hash = `#/timesheet/${shiftMonth(tsMonth || monthNow(), 1)}`; });

async function renderTimesheet(seq) {
  if (!tsMonth) tsMonth = monthNow();
  const ym = tsMonth;
  $("#ts-month").textContent = monthLabel(ym);
  const box = $("#ts-body");
  box.innerHTML = `<div class="panel-pad">${skeletonCards(2)}</div>`;
  let data;
  try { data = await api(`/timesheet?month=${encodeURIComponent(ym)}`); }
  catch (e) {
    if (stale(seq) || e.status === 401) return;
    box.innerHTML = `<div class="panel-pad">${errorHtml(e)}</div>`;
    box.querySelector("[data-retry]")?.addEventListener("click", () => renderTimesheet(++navSeq));
    return;
  }
  if (stale(seq)) return;
  let days = list(data?.days);
  const today = isoOf(new Date());
  days = days.map((d) => {
    const iso = String(d.date).slice(0, 10), dt = parseIso(iso), wd = dt.getDay();
    return { iso, day: dt.getDate(), wd, hol: typeof d.holiday === "string" ? d.holiday : "", off: Boolean(d.off) };
  });
  const rows = list(data?.employees);
  if (!rows.length) { box.innerHTML = `<p class="ts-empty">Šiame mėnesyje nėra darbuotojų, kuriuos galite matyti.</p>`; return; }
  const WD = ["S", "P", "A", "T", "K", "Pn", "Š"];
  box.innerHTML = `<table class="ts" data-testid="timesheet">
    <caption class="sr-only">Tabelis, ${esc(monthLabel(ym))}</caption>
    <thead><tr><th class="name" scope="col">Darbuotojas</th>${days.map((d) => `<th scope="col" class="${d.off ? "off" : ""}${d.iso === today ? " today" : ""}"${d.hol ? ` title="${esc(d.hol)}"` : ""}>${d.day}<small>${WD[d.wd]}</small></th>`).join("")}<th scope="col">Iš viso</th></tr></thead>
    <tbody>${rows.map((row) => {
      const marks = row.marks || {};
      const name = row.name, dep = row.department || "";
      let total = 0;
      const cells = days.map((d) => {
        const mk = marks[d.iso];
        if (mk) total += 1;
        return `<td class="${d.off ? "off" : ""}${d.iso === today ? " today" : ""}" data-day="${d.iso}">${mk ? `<span class="mk ${esc(mk)}" title="${esc(mk)}">${esc(mk)}</span>` : ""}</td>`;
      }).join("");
      return `<tr data-employee="${esc(name)}"><th class="name" scope="row">${esc(name)}${dep ? `<small>${esc(dep)}</small>` : ""}</th>${cells}<td class="sum">${total ? `${total} d.` : ""}</td></tr>`;
    }).join("")}</tbody></table>`;
}

/* ---------- Pašto dėžutė ---------- */
async function renderOutbox(seq) {
  const box = $("#outbox-list");
  box.innerHTML = skeletonCards(3);
  try {
    const items = list(await api("/outbox"));
    if (stale(seq)) return;
    if (!items.length) { box.innerHTML = stateHtml("ph-tray", "Laiškų nėra", "Kai sistema išsiųs kvietimą pasirašyti ar pranešimą, jis atsiras čia."); return; }
    box.innerHTML = items.map((x) => {
      const link = x.link || x.url || "";
      const sign = signHref(link);
      const isLogin = /\/login\//.test(link);
      const btn = link ? `<a class="btn small" href="${esc(sign || link)}" data-testid="mail-link"><i class="ph ${sign ? "ph-signature" : isLogin ? "ph-sign-in" : "ph-arrow-square-out"}" aria-hidden="true"></i>${sign ? "Atidaryti pasirašymą" : isLogin ? "Prisijungti per nuorodą" : "Atidaryti nuorodą"}</a>` : "";
      return `<article class="panel mail" data-testid="mail">
        <div class="mail-top"><span>Kam: <b>${esc(nameOf(x.to) || x.to_name ? `${nameOf(x.to) || x.to_name} <${x.to_email}>` : x.to_email || "")}</b></span><time>${fmtTs(x.ts ?? x.created_at)}</time></div>
        <h3>${esc(x.subject)}</h3><p>${esc(x.body)}</p>${btn}</article>`;
    }).join("");
  } catch (e) {
    if (stale(seq) || e.status === 401) return;
    box.innerHTML = errorHtml(e);
    box.querySelector("[data-retry]")?.addEventListener("click", () => renderOutbox(++navSeq));
  }
}

/* ---------- Patvirtinimo dialogas ---------- */
function confirmDialog({ title, text, ok, cancel = "Atšaukti" }) {
  const dlg = $("#confirm-dialog");
  $("#confirm-title").textContent = title;
  $("#confirm-text").textContent = text;
  $("#confirm-ok").textContent = ok;
  $("#confirm-cancel").textContent = cancel;
  dlg.returnValue = "";
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "confirm"), { once: true });
    dlg.showModal();
    $("#confirm-cancel").focus();
  });
}

$("#reset-btn").addEventListener("click", async () => {
  const ok = await confirmDialog({
    title: "Atstatyti demo duomenis?",
    text: "Visi prašymai, parašai, tabelio žymėjimai ir laiškai bus ištrinti, likučiai grįš į pradinius. Visi naudotojai bus atjungti. Šio veiksmo atšaukti negalima.",
    ok: "Atstatyti", cancel: "Palikti kaip yra",
  });
  if (!ok) return;
  const btn = $("#reset-btn");
  btn.disabled = true;
  try {
    await api("/demo/reset", { method: "POST", body: {} });
    try { await api("/logout", { method: "POST", allow401: true }); } catch (_) { /* sesija jau gali būti išvalyta */ }
    afterLogin = "";
    history.replaceState(null, "", location.pathname);
    signedOut();
    toast("Demo duomenys atstatyti. Prisijunkite iš naujo.");
  } catch (e) { if (e.status !== 401) toast(e.message, true); }
  finally { btn.disabled = false; }
});

/* ---------- Paleidimas ---------- */
(async function boot() {
  // Dokobit stiliaus nuoroda /hr/sign/{token}/{signer} paverčiama vidiniu maršrutu
  const m = location.pathname.match(/\/hr\/sign\/([^/]+)\/([^/]+)/);
  if (m) history.replaceState(null, "", `/hr#/sign/${m[1]}/${m[2]}`);
  if (new URLSearchParams(location.search).get("link") === "invalid") {
    $("#magic-msg").textContent = "Prisijungimo nuoroda nebegalioja arba jau panaudota. Paprašykite naujos.";
    history.replaceState(null, "", location.pathname + location.hash);
  }
  try { await startApp(""); }
  catch (e) {
    if (e.status === 401) { signedOut(); return; }
    $("#boot").innerHTML = `<div class="state err"><i class="ph ph-warning-circle" aria-hidden="true"></i><b>Nepavyko paleisti</b><p>${esc(e.message)}</p><button class="btn" type="button" onclick="location.reload()">Bandyti dar kartą</button></div>`;
  }
})();
