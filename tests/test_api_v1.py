"""API v1: pilnas srautas su TestClient, laikina DB ir sugeneruotais demo failais."""
from __future__ import annotations

import io
import zipfile
from contextlib import closing
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from app import auth, main
from core import employees as emp, world
from core.rivile_payroll import DEFAULT_PAY_MAPPING
from core.store import Store
from data import gen_timesheet

PASSWORD = "labai-ilgas-slaptazodis"
PERIOD = "2026-10"
BASE = f"/api/v1/periods/{PERIOD}"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(scope="module")
def demo_files(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo")
    files = gen_timesheet.main(out)
    files["employees"] = emp.write_employees_xlsx(emp.from_world(), out / "darbuotojai.xlsx")
    return files


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(auth.settings, "db_path", tmp_path / "uno.db")
    monkeypatch.setenv("UNO_DB", str(tmp_path / "uno.db"))
    auth.reset_failures()
    with closing(auth.connect()) as conn:
        auth.create_user(conn, 1, "rivile", PASSWORD, "accountant")
        auth.create_user(conn, 1, "vadovas", PASSWORD, "manager")
        auth.create_user(conn, 2, "kita", PASSWORD, "accountant")
    return TestClient(main.app)


def login(client: TestClient, username: str = "rivile") -> dict:
    r = client.post("/api/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200
    return {"X-CSRF-Token": r.json()["csrf"]}


def upload(client, headers, kind, path, period=PERIOD):
    with open(path, "rb") as fh:
        return client.post(f"/api/v1/periods/{period}/uploads/{kind}", headers=headers,
                           files={"file": (path.name, fh, "application/octet-stream")})


def upload_all(client, headers, files):
    for kind, key in (("employees", "employees"), ("timesheet", "tabelis"), ("rivile_hours", "rivile"),
                      ("sick_certs", "sodra"), ("vacation_requests", "requests")):
        r = upload(client, headers, kind, files[key])
        assert r.status_code == 200, r.text


@pytest.fixture()
def checked(api, demo_files):
    headers = login(api)
    upload_all(api, headers, demo_files)
    r = api.post(f"{BASE}/check", headers=headers)
    assert r.status_code == 200, r.text
    return api, headers, r.json()


def problems(result: dict) -> set[tuple[str, str]]:
    return {(f["ref"], f["extra"]["kind"]) for f in result["findings"]
            if f["module"] == "timesheet" and f["severity"] in ("error", "warning")}


def write_rivile_payroll(path, expected: dict, tweaks: dict) -> None:
    """Rivilės DU eksportas iš mūsų tikėtinų reikšmių; tweaks[(tab, laukas)] = pridedama suma."""
    fields = ["id", "gross", "npd", "gpm", "sodra_employee", "sodra_employer", "net"]
    wb = Workbook()
    ws = wb.active
    ws.append(["Rivilė DU ataskaita"])
    ws.append([DEFAULT_PAY_MAPPING[f] for f in fields])
    for tab, e in expected.items():
        ws.append([tab] + [float(D(e[f]) + tweaks.get((tab, f), D(0))) for f in fields[1:]])
    wb.save(path)


# ---------- autentifikacija ----------

def test_unauthenticated_is_401(api):
    assert api.get(f"{BASE}/state").status_code == 401
    assert api.get(f"{BASE}/audit").status_code == 401
    assert api.get(f"{BASE}/rivile-timesheet").status_code == 401
    assert api.post(f"{BASE}/check").status_code == 401


def test_missing_csrf_is_403(api):
    login(api)
    assert api.post(f"{BASE}/check").status_code == 403


def test_manager_can_read_but_not_write(api):
    headers = login(api, "vadovas")
    assert api.get(f"{BASE}/state").status_code == 200
    assert api.post(f"{BASE}/check", headers=headers).status_code == 403


def test_bad_period_is_400(api):
    login(api)
    assert api.get("/api/v1/periods/2026-13/state").status_code == 400
    assert api.get("/api/v1/periods/labas/state").status_code == 400


# ---------- pilnas srautas ----------

def test_check_finds_the_five_injected_anomalies(checked):
    _, _, result = checked
    assert problems(result) == {(a.tab_no, a.kind) for a in world.TIMESHEET_ANOMALIES}
    assert result["counts"]["error"] >= 3 and result["rows"] == 57 and result["workdays"] == 22
    assert len(result["expected"]) == 57
    assert result["unsupported"] and all(u["reasons"] for u in result["unsupported"])
    supported = [e for e in result["expected"].values() if e["supported"]]
    assert supported and all(isinstance(e["net"], str) for e in supported)


def test_state_reflects_uploads_runs_and_approval(checked):
    api, headers, _ = checked
    state = api.get(f"{BASE}/state").json()
    assert state["uploads"]["timesheet"]["count"] == 1 and state["uploads"]["rivile_payroll"]["count"] == 0
    assert state["runs"]["timesheet"]["counts"]["error"] >= 3
    assert state["approval"]["is_approved"] is False
    assert api.post(f"{BASE}/approve", headers=headers).status_code == 200
    assert api.get(f"{BASE}/state").json()["approval"]["is_approved"] is True


def test_rivile_timesheet_requires_approval_then_downloads(checked):
    api, headers, _ = checked
    r = api.get(f"{BASE}/rivile-timesheet")
    assert r.status_code == 409 and "patvirtink" in r.json()["detail"]
    api.post(f"{BASE}/approve", headers=headers)
    r = api.get(f"{BASE}/rivile-timesheet")
    # demo tabelyje yra viršvalandžių: tabelis + atskiras viršvalandžių sąrašas ZIP'e
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert f"rivile_virsvalandziai_{PERIOD}.xlsx" in names
    wb = load_workbook(io.BytesIO(zf.read(next(n for n in names if n.startswith("rivile_tabelis")))))
    assert {"Timesheet_operations", "Lines", "Info"} <= set(wb.sheetnames)
    ops = list(wb["Timesheet_operations"].iter_rows(min_row=2, values_only=True))
    assert len(ops) == 57 and ops[0][1] == "RIV-D001" and ops[0][2] == PERIOD


def test_new_timesheet_upload_invalidates_approval(checked, demo_files):
    api, headers, _ = checked
    api.post(f"{BASE}/approve", headers=headers)
    assert api.get(f"{BASE}/rivile-timesheet").status_code != 409   # patvirtinta (eksportas gali būti sulaikytas dėl viršvalandžių)
    assert upload(api, headers, "timesheet", demo_files["tabelis"]).status_code == 200
    assert api.get(f"{BASE}/state").json()["approval"]["is_approved"] is False
    assert api.get(f"{BASE}/rivile-timesheet").status_code == 409


def test_approve_without_run_is_409(api):
    headers = login(api)
    assert api.post(f"{BASE}/approve", headers=headers).status_code == 409


def test_compare_reports_exactly_the_deliberate_diffs(checked, tmp_path):
    api, headers, result = checked
    supported = [t for t, e in result["expected"].items() if e["supported"]]
    a, b = supported[0], supported[1]
    assert api.post(f"{BASE}/approve", headers=headers).status_code == 200
    path = tmp_path / "rivile_du.xlsx"
    write_rivile_payroll(path, result["expected"], {(a, "net"): D("5.00"), (b, "gpm"): D("3.00")})
    assert upload(api, headers, "rivile_payroll", path).status_code == 200
    r = api.post(f"{BASE}/compare", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()
    per_employee = {(f["ref"], f["extra"]["kind"]) for f in data["findings"] if f["ref"] != "TOTAL"}
    assert per_employee == {(a, "diff_net"), (b, "diff_gpm")}
    totals = {f["extra"]["kind"] for f in data["findings"] if f["ref"] == "TOTAL"}
    assert totals == {"total_net", "total_gpm"}
    assert data["totals"]["gpm313"]["diff"] == "3.00" and data["totals"]["sam"]["diff"] == "0.00"
    assert data["compared"] == len(supported)
    assert {u["tab_no"] for u in data["accountant_review"]} == {
        t for t, e in result["expected"].items() if not e["supported"]}
    assert api.get(f"{BASE}/state").json()["runs"]["payroll_compare"]["id"] == data["run_id"]


def test_compare_clean_file_has_no_findings(checked, tmp_path):
    api, headers, result = checked
    api.post(f"{BASE}/approve", headers=headers)
    path = tmp_path / "rivile_du.xlsx"
    write_rivile_payroll(path, result["expected"], {})
    upload(api, headers, "rivile_payroll", path)
    assert api.post(f"{BASE}/compare", headers=headers).json()["findings"] == []


def test_compare_needs_payroll_upload_and_run(api, demo_files, tmp_path):
    headers = login(api)
    assert api.post(f"{BASE}/compare", headers=headers).status_code == 409
    path = tmp_path / "rivile_du.xlsx"
    write_rivile_payroll(path, {}, {})
    upload(api, headers, "rivile_payroll", path)
    assert api.post(f"{BASE}/compare", headers=headers).status_code == 409


# ---------- neprivalomi failai, validacija ----------

def test_check_works_without_optional_files(api, demo_files):
    headers = login(api)
    upload(api, headers, "employees", demo_files["employees"])
    upload(api, headers, "timesheet", demo_files["tabelis"])
    result = api.post(f"{BASE}/check", headers=headers).json()
    kinds = {f["extra"]["kind"] for f in result["findings"] if f["severity"] in ("error", "warning")}
    assert kinds == {"overtime_over_limit", "zero_hours"}


def test_check_requires_employees_and_timesheet(api):
    headers = login(api)
    assert api.post(f"{BASE}/check", headers=headers).status_code == 409


def test_upload_validation(api, demo_files, tmp_path):
    headers = login(api)
    assert upload(api, headers, "employees", demo_files["sodra"]).status_code == 400   # .csv vietoj .xlsx
    assert upload(api, headers, "nezinomas", demo_files["tabelis"]).status_code == 404
    bad = tmp_path / "blogas.xlsx"
    bad.write_bytes(b"ne xlsx")
    assert upload(api, headers, "timesheet", bad).status_code == 422
    assert upload(api, headers, "employees", bad).status_code == 422


def test_employees_file_errors_are_reported_per_row(api, tmp_path):
    headers = login(api)
    wb = Workbook()
    ws = wb.active
    ws.append(emp.COLUMNS)
    ws.append(["D001", "Jonas Jonaitis", "RIV1", "abc", 176, "gal būt", "", "", "", ""])
    path = tmp_path / "darbuotojai.xlsx"
    wb.save(path)
    r = upload(api, headers, "employees", path)
    assert r.status_code == 422
    errors = r.json()["detail"]["errors"]
    assert any("Eilutė 2" in e and "Sutarta neto" in e for e in errors)
    assert any("Eilutė 2" in e and "Terminuota" in e for e in errors)


# ---------- tenant izoliacija, auditas ----------

def test_tenant_isolation(checked):
    api, headers, _ = checked
    api.post(f"{BASE}/approve", headers=headers)
    other = TestClient(main.app)
    other_headers = login(other, "kita")
    state = other.get(f"{BASE}/state").json()
    assert all(v["count"] == 0 for v in state["uploads"].values())
    assert state["approval"]["is_approved"] is False and state["runs"]["timesheet"] is None
    assert other.get(f"{BASE}/audit").json()["entries"] == []
    assert other.get(f"{BASE}/rivile-timesheet").status_code == 409
    assert other.post(f"{BASE}/approve", headers=other_headers).status_code == 409


def test_audit_lists_actions(checked):
    api, headers, _ = checked
    api.post(f"{BASE}/approve", headers=headers)
    api.get(f"{BASE}/rivile-timesheet")
    actions = [e["action"] for e in api.get(f"{BASE}/audit").json()["entries"]]
    assert actions.count("upload") == 5
    assert {"run", "approve", "export"} <= set(actions)


# ---------- audito pataisos: patvirtinimas, palyginimas, rezultatai ----------

def clean_payroll(api, headers, result, tmp_path, name="rivile_du.xlsx", tweaks=None):
    path = tmp_path / name
    write_rivile_payroll(path, result["expected"], tweaks or {})
    assert upload(api, headers, "rivile_payroll", path).status_code == 200


def test_manager_can_approve_but_not_upload_check_or_compare(checked, demo_files):
    api, _, _ = checked
    manager = login(api, "vadovas")
    assert api.post(f"{BASE}/approve", headers=manager).status_code == 200
    assert api.post(f"{BASE}/check", headers=manager).status_code == 403
    assert api.post(f"{BASE}/compare", headers=manager).status_code == 403
    assert upload(api, manager, "timesheet", demo_files["tabelis"]).status_code == 403


def test_first_upload_of_unused_optional_kind_invalidates_approval(api, demo_files):
    headers = login(api)
    upload(api, headers, "employees", demo_files["employees"])
    upload(api, headers, "timesheet", demo_files["tabelis"])
    api.post(f"{BASE}/check", headers=headers)
    assert api.post(f"{BASE}/approve", headers=headers).status_code == 200
    assert api.get(f"{BASE}/state").json()["approval"]["is_approved"] is True
    assert upload(api, headers, "sick_certs", demo_files["sodra"]).status_code == 200
    assert api.get(f"{BASE}/state").json()["approval"]["is_approved"] is False


def test_rules_version_change_invalidates_approval(checked, monkeypatch):
    from app import api_v1
    api, headers, _ = checked
    api.post(f"{BASE}/approve", headers=headers)
    monkeypatch.setattr(api_v1, "RULES_VERSION", "2099.9")
    assert api.get(f"{BASE}/state").json()["approval"]["is_approved"] is False


def test_stale_approve_is_409_and_inserts_no_row(checked, demo_files):
    api, headers, _ = checked
    upload(api, headers, "sick_certs", demo_files["sodra"])      # po patikros
    r = api.post(f"{BASE}/approve", headers=headers)
    assert r.status_code == 409 and "iš naujo" in r.json()["detail"]
    assert Store(auth.db_path()).active_approval(1, PERIOD, "timesheet") is None


def test_compare_requires_approved_run(checked, tmp_path):
    api, headers, result = checked
    clean_payroll(api, headers, result, tmp_path)
    r = api.post(f"{BASE}/compare", headers=headers)
    assert r.status_code == 409 and "patvirtink" in r.json()["detail"]


def test_compare_after_input_change_is_409(checked, demo_files, tmp_path):
    api, headers, result = checked
    api.post(f"{BASE}/approve", headers=headers)
    clean_payroll(api, headers, result, tmp_path)
    assert api.post(f"{BASE}/compare", headers=headers).status_code == 200
    upload(api, headers, "vacation_requests", demo_files["requests"])
    assert api.post(f"{BASE}/compare", headers=headers).status_code == 409


def test_compare_records_timesheet_run_and_all_inputs(checked, tmp_path):
    api, headers, result = checked
    api.post(f"{BASE}/approve", headers=headers)
    clean_payroll(api, headers, result, tmp_path)
    data = api.post(f"{BASE}/compare", headers=headers).json()
    run = api.get(f"{BASE}/runs/payroll_compare").json()
    check_run = api.get(f"{BASE}/runs/timesheet").json()
    assert data["timesheet_run_id"] == check_run["run_id"]
    assert set(check_run["input_upload_ids"]) < set(run["input_upload_ids"])
    assert len(run["input_upload_ids"]) == len(check_run["input_upload_ids"]) + 1


def test_compare_totals_separate_whole_file_from_verified_subset(checked, tmp_path):
    api, headers, result = checked
    api.post(f"{BASE}/approve", headers=headers)
    path = tmp_path / "rivile_du.xlsx"
    supported = {t: e for t, e in result["expected"].items() if e["supported"]}
    # tik palaikomi darbuotojai + papildoma eilutė su 200 € GPM, kurios nesulyginame
    extra = {**next(iter(supported.values())), "gpm": "200.00"}
    write_rivile_payroll(path, {**supported, "X999": extra}, {})
    upload(api, headers, "rivile_payroll", path)
    data = api.post(f"{BASE}/compare", headers=headers).json()
    assert data["coverage"]["partial"] is True
    assert data["coverage"]["label"].startswith("patikrinta ") and " iš " in data["coverage"]["label"]
    unverified = D(data["unverified_totals"]["gpm313"])
    assert D(data["file_totals"]["gpm313"]) - D(data["totals"]["gpm313"]["rivile"]) == unverified
    assert unverified >= D("200.00")
    assert data["file_totals"]["rows"] == len(supported) + 1
    assert "patikrinta" in data["status_label"]


def test_compare_duplicate_after_rekey_is_422(checked, tmp_path):
    api, headers, result = checked
    api.post(f"{BASE}/approve", headers=headers)
    tab = next(t for t, e in result["expected"].items() if e["supported"])
    path = tmp_path / "rivile_du.xlsx"
    write_rivile_payroll(path, {tab: result["expected"][tab], f"RIV-{tab}": result["expected"][tab]}, {})
    upload(api, headers, "rivile_payroll", path)
    r = api.post(f"{BASE}/compare", headers=headers)
    assert r.status_code == 422 and r.json()["detail"]["duplicates"] == [tab]


def test_saved_run_is_readable_by_manager_and_company_scoped(checked):
    api, _, result = checked
    manager = TestClient(main.app)
    login(manager, "vadovas")
    saved = manager.get(f"{BASE}/runs/timesheet").json()
    assert saved["result"]["findings"] == result["findings"] and saved["result"]["expected"]
    assert manager.get(f"{BASE}/runs/nezinomas").status_code == 404
    assert manager.get(f"{BASE}/runs/payroll_compare").status_code == 404   # dar nevykdyta
    other = TestClient(main.app)
    login(other, "kita")
    assert other.get(f"{BASE}/runs/timesheet").status_code == 404


def test_audit_filtered_by_period(checked, tmp_path):
    api, headers, result = checked
    clean_payroll(api, headers, result, tmp_path)                      # 2026-10
    other_period = "2026-09"
    path = tmp_path / "x.xlsx"
    write_rivile_payroll(path, {}, {})
    assert upload(api, headers, "rivile_payroll", path, other_period).status_code == 200
    october = api.get(f"{BASE}/audit").json()["entries"]
    september = api.get(f"/api/v1/periods/{other_period}/audit").json()["entries"]
    assert len(september) == 1 and september[0]["period"] == other_period
    assert october and all(e["period"] == PERIOD for e in october)


def test_check_result_is_reproducible(checked, demo_files):
    api, headers, first = checked
    upload_all(api, headers, demo_files)                                # tie patys failai iš naujo
    second = api.post(f"{BASE}/check", headers=headers).json()
    strip = lambda r: {k: v for k, v in r.items() if k not in ("run_id", "inputs")}  # noqa: E731
    assert strip(first) == strip(second)
    assert api.get(f"{BASE}/runs/timesheet").json()["result"] == strip(second)


def test_unexpected_errors_do_not_leak_details(checked, tmp_path, monkeypatch):
    from app import api_v1
    api, headers, result = checked
    api.post(f"{BASE}/approve", headers=headers)
    clean_payroll(api, headers, result, tmp_path)

    def boom(path):
        raise RuntimeError("C:\\slaptas\\kelias.db")
    monkeypatch.setattr(api_v1.rivile_payroll, "read_rivile_payroll_xlsx", boom)
    r = api.post(f"{BASE}/compare", headers=headers)
    assert r.status_code == 422 and "slaptas" not in r.text and "RuntimeError" not in r.text


def test_overtime_findings_do_not_mutate_source(monkeypatch):
    from types import SimpleNamespace
    from app import api_v1
    from core.models import Finding
    original = Finding("timesheet", "error", "t", "d", ref="D1", extra={"week": 1})
    monkeypatch.setattr(api_v1.payroll_rules, "check_overtime_limits", lambda *_a, **_k: [original])
    row = SimpleNamespace(tab_no="D1", overtime_by_week=(D("3"),))
    out = api_v1._rules_overtime_findings([row])
    assert out[0].severity == "warning" and original.severity == "error" and original.detail == "d"


def test_demo_routes_disabled_in_production(api, monkeypatch):
    assert api.get("/apskaita").status_code == 200 and api.get("/api/overview").status_code == 200
    monkeypatch.setenv("UNO_DEMO", "0")
    assert api.get("/apskaita").status_code == 404
    assert api.get("/api/overview").status_code == 404
    assert api.post("/api/run/payroll").status_code == 404
    assert api.get("/app").status_code == 200
    assert api.get("/api/me").status_code == 401        # tikri maršrutai veikia


# ---------- raundas 2: N1, N2, S12 ----------

def test_rules_version_bumped_and_old_version_run_is_stale(checked):
    from app import api_v1
    from core import approval
    api, headers, result = checked
    assert api_v1.RULES_VERSION == "2026.2"
    store = Store(auth.db_path())
    ids = list(api.get(f"{BASE}/runs/timesheet").json()["input_upload_ids"])
    approval.record_run(store, 1, PERIOD, "timesheet", "2026.1", ids, result, "test")   # senų taisyklių skaičiavimas
    saved = api.get(f"{BASE}/runs/timesheet").json()
    assert saved["is_current"] is False and any("taisykl" in r for r in saved["stale_reasons"])
    assert api.post(f"{BASE}/approve", headers=headers).status_code == 409


def test_saved_timesheet_run_is_current_until_inputs_change(checked, demo_files):
    api, headers, _ = checked
    saved = api.get(f"{BASE}/runs/timesheet").json()
    assert saved["is_current"] is True and saved["stale_reasons"] == []
    assert api.get(f"{BASE}/state").json()["runs"]["timesheet"]["is_current"] is True
    upload(api, headers, "employees", demo_files["employees"])
    saved = api.get(f"{BASE}/runs/timesheet").json()
    assert saved["is_current"] is False and saved["stale_reasons"] == ["Po patikros įkeltas naujas failas: darbuotojų failas"]
    assert api.get(f"{BASE}/state").json()["runs"]["timesheet"]["is_current"] is False


def test_saved_compare_run_goes_stale_on_new_payroll_or_timesheet_change(checked, demo_files, tmp_path):
    api, headers, result = checked
    api.post(f"{BASE}/approve", headers=headers)
    clean_payroll(api, headers, result, tmp_path)
    api.post(f"{BASE}/compare", headers=headers)
    saved = api.get(f"{BASE}/runs/payroll_compare").json()
    assert saved["is_current"] is True and saved["stale_reasons"] == []
    # naujas Rivilės DU įkėlimas (#N+1): palyginimas dar remiasi senu
    path = tmp_path / "du2.xlsx"
    write_rivile_payroll(path, result["expected"], {})
    upload(api, headers, "rivile_payroll", path)
    saved = api.get(f"{BASE}/runs/payroll_compare").json()
    assert saved["is_current"] is False and any("Rivilės DU" in r for r in saved["stale_reasons"])
    assert api.get(f"{BASE}/state").json()["runs"]["payroll_compare"]["is_current"] is False
    api.post(f"{BASE}/compare", headers=headers)
    assert api.get(f"{BASE}/runs/payroll_compare").json()["is_current"] is True
    # pakeistas darbuotojų failas: tabelis nebepatvirtintas, todėl ir palyginimas nebeaktualus
    upload(api, headers, "employees", demo_files["employees"])
    saved = api.get(f"{BASE}/runs/payroll_compare").json()
    assert saved["is_current"] is False and any("Tabelio skaičiavimas" in r for r in saved["stale_reasons"])


def test_check_has_one_annual_overtime_not_checked_info(checked):
    _, _, result = checked
    notes = [f for f in result["findings"] if f["extra"].get("kind") == "annual_overtime_not_checked"]
    assert len(notes) == 1 and notes[0]["severity"] == "info"
