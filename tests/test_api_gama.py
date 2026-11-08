"""GAMA patikros API: snapshot komanda pakeista (monkeypatch), duomenys tik sintetiniai, katalogas tmp."""
from __future__ import annotations

import io
import shutil
import subprocess
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import auth, gama_api, main

PASSWORD = "labai-ilgas-slaptazodis"
FIXTURE = Path(__file__).parent / "fixtures" / "gama_snapshot_synthetic.json"
MONTH = "2026-09"
CHECK = "/api/v1/gama/check"


class FakeSnapshot:
    """Imituoja tools/gama_snapshot.ps1: nukopijuoja sintetinį fixture į -OutDir/gama_<mėnuo>.json."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.returncode = 0
        self.stderr = ""
        self.raise_exc: Exception | None = None
        self.write = True

    def __call__(self, cmd, timeout):
        self.calls.append(cmd)
        assert timeout == gama_api.SNAPSHOT_TIMEOUT_S
        if self.raise_exc:
            raise self.raise_exc
        month = cmd[cmd.index("-Month") + 1]
        out = Path(cmd[cmd.index("-OutDir") + 1])
        if self.returncode == 0 and self.write:
            shutil.copy(FIXTURE, out / f"gama_{month}.json")
        return subprocess.CompletedProcess(cmd, self.returncode, "", self.stderr)


@pytest.fixture()
def fake(monkeypatch):
    f = FakeSnapshot()
    monkeypatch.setattr(gama_api, "run_snapshot", f)
    return f


@pytest.fixture()
def data(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "UNO_DATA"
    monkeypatch.setenv("UNO_GAMA_DATA_DIR", str(d))
    monkeypatch.setenv("UNO_GAMA_COMPANIES", "1,2")
    return d


@pytest.fixture()
def api(tmp_path, monkeypatch, data, fake):
    monkeypatch.setattr(auth.settings, "db_path", tmp_path / "uno.db")
    monkeypatch.setenv("UNO_DB", str(tmp_path / "uno.db"))
    monkeypatch.delattr(main.app.state, "uno_store", raising=False)
    auth.reset_failures()
    with closing(auth.connect()) as conn:
        auth.create_user(conn, 1, "rivile", PASSWORD, "accountant")
        auth.create_user(conn, 1, "vadovas", PASSWORD, "manager")
        auth.create_user(conn, 1, "admin", PASSWORD, "admin")
        auth.create_user(conn, 2, "kita", PASSWORD, "accountant")
        auth.create_user(conn, 3, "trecia", PASSWORD, "accountant")
    return TestClient(main.app)


def login(client: TestClient, username: str = "rivile") -> dict:
    r = client.post("/api/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf"]}


def run_check(client, headers, month=MONTH, refresh=True):
    return client.post(CHECK, headers=headers, json={"month": month, "refresh": refresh})


# ---------- autentifikacija ir teisės ----------

def test_requires_login(api):
    assert api.post(CHECK, json={"month": MONTH}).status_code == 401
    assert api.get("/api/v1/gama/runs").status_code == 401
    assert api.get("/api/v1/gama/runs/1/xlsx").status_code == 401


def test_requires_csrf(api, fake):
    login(api)
    r = api.post(CHECK, json={"month": MONTH}, headers={"X-CSRF-Token": "blogas"})
    assert r.status_code == 403
    assert not fake.calls


def test_manager_cannot_run_but_can_read(api, fake):
    h = login(api)
    run_id = run_check(api, h).json()["run_id"]
    api.cookies.clear()
    hm = login(api, "vadovas")
    assert run_check(api, hm).status_code == 403
    assert api.get("/api/v1/gama/runs").status_code == 200
    assert api.get(f"/api/v1/gama/runs/{run_id}/xlsx").status_code == 200


def test_admin_can_run(api):
    assert run_check(api, login(api, "admin")).status_code == 200


def test_company_not_configured_is_403(api, fake):
    h = login(api, "trecia")
    assert run_check(api, h).status_code == 403
    assert api.get("/api/v1/gama/runs").status_code == 403
    assert not fake.calls


# ---------- validacija ----------

@pytest.mark.parametrize("month", ["2026-13", "2026-9", "26-09", "2026-09; rm", "", "2026-09-01"])
def test_bad_month_is_400(api, fake, month):
    r = run_check(api, login(api), month=month)
    assert r.status_code == 400
    assert "YYYY-MM" in r.json()["detail"]
    assert not fake.calls


def test_month_missing_from_snapshot_is_400(api):
    r = run_check(api, login(api), month="2026-11")   # fixture eilutės tik iki 2026-09
    assert r.status_code == 400
    assert "2026-11" in r.json()["detail"]


def test_unsupported_year_is_400(api):
    r = run_check(api, login(api), month="2025-12")
    assert r.status_code == 400
    assert "2025" in r.json()["detail"]


def test_data_dir_inside_repo_refused(api, fake, monkeypatch):
    monkeypatch.setenv("UNO_GAMA_DATA_DIR", str(gama_api.REPO / "data" / "gama"))
    r = run_check(api, login(api))
    assert r.status_code == 500
    assert "UNO_GAMA_DATA_DIR" in r.json()["detail"]
    assert not fake.calls


# ---------- snapshot komanda ----------

def test_snapshot_failure_is_502_with_first_stderr_line_only(api, fake):
    fake.returncode = 1
    fake.stderr = "Prisijungti nepavyko\nslaptas antras eilutės tekstas\nat C:\\kelias"
    r = run_check(api, login(api))
    assert r.status_code == 502
    assert r.json()["detail"] == "GAMA eksportas nepavyko: Prisijungti nepavyko"


def test_snapshot_timeout_is_502(api, fake):
    fake.raise_exc = subprocess.TimeoutExpired(["x"], 300)
    r = run_check(api, login(api))
    assert r.status_code == 502
    assert "5 min" in r.json()["detail"]


def test_snapshot_cannot_start_is_502(api, fake):
    fake.raise_exc = FileNotFoundError("powershell")
    assert run_check(api, login(api)).status_code == 502


def test_snapshot_ok_but_no_file_is_502(api, fake):
    fake.write = False
    assert run_check(api, login(api)).status_code == 502


def test_command_is_argument_list_with_month_and_company_dir(api, fake, data, monkeypatch):
    monkeypatch.delenv("UNO_GAMA_SNAPSHOT_CMD", raising=False)
    assert run_check(api, login(api)).status_code == 200
    cmd = fake.calls[0]
    assert cmd[:5] == ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    assert cmd[5].endswith("gama_snapshot.ps1")
    assert cmd[-4:] == ["-Month", MONTH, "-OutDir", str((data / "company_1").resolve())]


def test_custom_command_from_env(monkeypatch):
    monkeypatch.setenv("UNO_GAMA_SNAPSHOT_CMD", '["py", "-3.12", "C:\\\\x y\\\\fake.py"]')
    assert gama_api.snapshot_command() == ["py", "-3.12", "C:\\x y\\fake.py"]
    monkeypatch.setenv("UNO_GAMA_SNAPSHOT_CMD", '"C:\\Program Files\\py.exe" fake.py')
    assert gama_api.snapshot_command() == ["C:\\Program Files\\py.exe", "fake.py"]


def test_refresh_false_uses_existing_file(api, fake, data):
    target = data / "company_1"
    target.mkdir(parents=True)
    shutil.copy(FIXTURE, target / f"gama_{MONTH}.json")
    r = run_check(api, login(api), refresh=False)
    assert r.status_code == 200, r.text
    assert not fake.calls


def test_refresh_false_without_file_runs_snapshot(api, fake):
    assert run_check(api, login(api), refresh=False).status_code == 200
    assert len(fake.calls) == 1


def test_corrupt_snapshot_is_422(api, fake, data):
    target = data / "company_1"
    target.mkdir(parents=True)
    (target / f"gama_{MONTH}.json").write_text("{nėra json", encoding="utf-8")
    assert run_check(api, login(api), refresh=False).status_code == 422


def test_busy_lock_is_409(api, fake):
    h = login(api)
    assert gama_api._lock.acquire(blocking=False)
    try:
        assert run_check(api, h).status_code == 409
    finally:
        gama_api._lock.release()


# ---------- rezultatas ----------

def test_success_returns_findings_and_persists_run(api, data):
    h = login(api)
    r = run_check(api, h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["month"] == MONTH
    assert body["summary"]["employees"] == 8
    assert set(body["summary"]["components"]) >= {"PSD", "VSD", "PENS", "GPM"}
    sev = [f["severity"] for f in body["findings"]]
    assert sev == sorted(sev, key=gama_api.SEVERITY_ORDER.get)          # klaidos pirmos
    psd = next(f for f in body["findings"] if f["extra"].get("component") == "PSD")
    assert psd["ref"] == "00005" and psd["extra"]["diff"] == "-0.45"
    assert (data / "company_1" / body["xlsx_name"]).is_file()

    runs = api.get("/api/v1/gama/runs", params={"month": MONTH}).json()["runs"]
    assert [x["run_id"] for x in runs] == [body["run_id"]]
    assert "findings" not in runs[0] and runs[0]["created_by"] == "rivile"
    detail = api.get(f"/api/v1/gama/runs/{body['run_id']}").json()
    assert detail["findings"] == body["findings"]
    assert api.get("/api/v1/gama/runs", params={"month": "2026-10"}).json()["runs"] == []
    audit = api.get(f"/api/v1/periods/{MONTH}/audit").json()["entries"]
    assert any(e["action"] == "run" and "gama_check" in e["detail"] for e in audit)


def test_runs_bad_month_filter_is_400(api):
    login(api)
    assert api.get("/api/v1/gama/runs", params={"month": "x"}).status_code == 400


def test_xlsx_download(api):
    h = login(api)
    run_id = run_check(api, h).json()["run_id"]
    r = api.get(f"/api/v1/gama/runs/{run_id}/xlsx")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert "gama_check_2026-09" in r.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Suvestinė", "Radiniai"]
    assert wb["Radiniai"].max_row > 1
    audit = api.get(f"/api/v1/periods/{MONTH}/audit").json()["entries"]
    assert any(e["action"] == "export" and "XLSX" in e["detail"] for e in audit)


def test_xlsx_missing_file_is_404(api, data):
    h = login(api)
    body = run_check(api, h).json()
    (data / "company_1" / body["xlsx_name"]).unlink()
    assert api.get(f"/api/v1/gama/runs/{body['run_id']}/xlsx").status_code == 404


def test_company_scoping(api, fake, data):
    h1 = login(api)
    run_id = run_check(api, h1).json()["run_id"]
    api.cookies.clear()
    h2 = login(api, "kita")
    assert api.get("/api/v1/gama/runs").json()["runs"] == []
    assert api.get(f"/api/v1/gama/runs/{run_id}").status_code == 404
    assert api.get(f"/api/v1/gama/runs/{run_id}/xlsx").status_code == 404
    # kita įmonė be atnaujinimo nemato 1-os įmonės snapshot'o: paleidžiama komanda į jos katalogą
    fake.calls.clear()
    assert run_check(api, h2, refresh=False).status_code == 200
    assert fake.calls[0][-1] == str((data / "company_2").resolve())
