"""Rivilė GAMA mėnesio mokesčių tikrintuvas: sintetinis snapshot + atskiros taisyklės."""
from decimal import Decimal as D
from pathlib import Path

import pytest

from core.gama import Snapshot, load_snapshot, month_lines
from core.gama_check import check_month, npd_full
from tools import gama_check as cli

FIXTURE = Path(__file__).parent / "fixtures" / "gama_snapshot_synthetic.json"


def line(tb, month, code, tipas, suma):
    return {"A20_KODAS_TB": tb, "A20_KODAS_LP": str(code).zfill(12), "A20_TIPAS": str(tipas),
            "A20_DATE_SP": f"{month}-01", "A20_SUMA": str(suma)}


def snap(lines, month="2026-09", history_from="2026-01-01"):
    return Snapshot.from_dict({"month": month, "history_from": history_from,
                               "employees": [], "pay_lines": lines})


def plain_month(tb, mon, base, gpm, psd=None, vsd=None, extra=()):
    base = D(base)
    return [line(tb, mon, 100, 1, base),
            line(tb, mon, 19201, 2, psd if psd is not None else (base * D("0.0698")).quantize(D("0.01"))),
            line(tb, mon, 19202, 2, vsd if vsd is not None else (base * D("0.1252")).quantize(D("0.01"))),
            line(tb, mon, 19101, 2, gpm), *extra]


def errors(findings):
    return [f for f in findings if f.severity == "error"]


# --- loading -----------------------------------------------------------------

def test_load_snapshot_handles_bom_and_normalizes_codes():
    assert FIXTURE.read_bytes().startswith(b"\xef\xbb\xbf")
    s = load_snapshot(FIXTURE)
    assert s.month == "2026-09"
    assert s.months()[0] == "2026-01" and s.months()[-1] == "2026-09"
    assert month_lines(s, "2026-09")["00001"][("100", "1")] == D("1500.00")


def test_month_lines_keys_by_code_and_tipas():
    ml = month_lines(load_snapshot(FIXTURE), "2026-09")["00008"]
    assert ml[("302", "1")] == D("200.00")
    assert ml[("302", "2")] == D("150.00")


def test_month_lines_sums_duplicates():
    s = snap([line("1", "2026-09", 100, 1, "10.50"), line("1", "2026-09", 100, 1, "4.50")])
    assert month_lines(s, "2026-09")["1"][("100", "1")] == D("15.00")


# --- NPD formula -------------------------------------------------------------

@pytest.mark.parametrize("base,expected", [("1000", "747.00"), ("1153", "747.00"),
                                           ("1500", "576.97"), ("2678.06", "0.00"), ("5000", "0.00")])
def test_npd_full(base, expected):
    assert npd_full(D(base)) == D(expected)


# --- fixture end to end ------------------------------------------------------

@pytest.fixture(scope="module")
def result():
    return check_month(load_snapshot(FIXTURE), "2026-09")


def test_fixture_exact_errors(result):
    findings, _ = result
    errs = errors(findings)
    assert {(f.ref, f.extra["component"]) for f in errs} == {("00005", "PSD"), ("00006", "GPM")}
    psd = next(f for f in errs if f.ref == "00005")
    assert (psd.extra["expected"], psd.extra["actual"], psd.extra["diff"]) == ("97.72", "97.27", "-0.45")
    gpm = next(f for f in errs if f.ref == "00006")
    assert (gpm.extra["expected"], gpm.extra["actual"]) == ("333.61", "330.00")


def test_fixture_correct_employees_have_no_findings(result):
    findings, _ = result
    for tb in ("00001", "00002", "00004"):
        assert [f for f in findings if f.ref == tb] == []


def test_fixture_unknown_npd_mode_is_info_only(result):
    findings, _ = result
    f3 = [f for f in findings if f.ref == "00003"]
    assert [f.severity for f in f3] == ["info"]
    assert "NPD režimas nenustatytas" in f3[0].title
    assert "2026-07:F" in f3[0].detail and "2026-08:0" in f3[0].detail


def test_fixture_high_earner_over_36_vdu_warning(result):
    findings, _ = result
    f7 = [f for f in findings if f.ref == "00007"]
    assert [f.severity for f in f7] == ["warning"]
    assert "Metinė riba 36 VDU" in f7[0].title
    assert "tikrinti rankiniu būdu" in f7[0].title


def test_fixture_302_and_alimony_gpm_is_warning_not_error(result):
    findings, _ = result
    f8 = [f for f in findings if f.ref == "00008"]
    assert [f.severity for f in f8] == ["warning"]
    assert "atostoginiai / atleidimas: galimas kito mėnesio priskyrimas" in f8[0].detail
    assert f8[0].extra["expected"] == "244.21"   # alimony (302 tipas 2) is not in base


def test_fixture_summary(result):
    _, s = result
    assert s["employees"] == 8
    assert s["npd_modes"] == {"F": 5, "0": 1, "unknown": 2}
    assert s["components"]["PSD"] == {"ok": 7, "mismatch": 1}
    assert s["components"]["VSD"] == {"ok": 8, "mismatch": 0}
    assert s["components"]["PENS"] == {"ok": 1, "mismatch": 0}
    assert s["components"]["GPM_LIGA"] == {"ok": 1, "mismatch": 0}
    assert s["components"]["GPM"] == {"ok": 4, "mismatch": 2}
    assert s["findings"] == {"error": 2, "warning": 2, "info": 1}


# --- individual rules --------------------------------------------------------

def test_base_excludes_201_and_911_includes_910():
    # base = 5000 (100) + 50 (910) = 5050; 201 and 911 are not in base; total income 5150: NPD 0 in both modes
    lines = [line("1", "2026-09", 100, 1, "5000"), line("1", "2026-09", 201, 1, "100"),
             line("1", "2026-09", 910, 1, "50"), line("1", "2026-09", 911, 1, "50"),
             line("1", "2026-09", 19201, 2, "352.49"), line("1", "2026-09", 19202, 2, "632.26"),
             line("1", "2026-09", 19101, 2, "1010.00"), line("1", "2026-09", 19102, 2, "15.00")]
    findings, summ = check_month(snap(lines), "2026-09")
    assert errors(findings) == []
    assert summ["components"]["PSD"] == {"ok": 1, "mismatch": 0}
    assert summ["components"]["VSD"] == {"ok": 1, "mismatch": 0}
    assert summ["components"]["GPM_LIGA"] == {"ok": 1, "mismatch": 0}


def test_sick_gpm_mismatch_is_error():
    # istorija nustato režimą 0 (be NPD): ligos GPM 15 % be NPD
    hist = plain_month("1", "2026-08", "1000", "200.00")
    lines = [line("1", "2026-09", 201, 1, "100"), line("1", "2026-09", 19102, 2, "14.00")]
    findings, _ = check_month(snap(hist + lines), "2026-09")
    e = [x for x in errors(findings) if x.extra["component"] == "GPM_LIGA"]
    assert len(e) == 1 and e[0].extra["expected"] == "15.00"


def test_tolerance_one_cent_ok_two_cents_error():
    ok = plain_month("1", "2026-09", "1000", "50.60", psd="69.81")    # expected 69.80
    bad = plain_month("2", "2026-09", "1000", "50.60", psd="69.82")
    hist = [x for tb in ("1", "2") for x in plain_month(tb, "2026-08", "1000", "50.60")]
    findings, _ = check_month(snap(ok + bad + hist), "2026-09")
    assert [(f.ref, f.extra["component"]) for f in errors(findings)] == [("2", "PSD")]


def test_rounding_half_up_per_component():
    # 1000.50 * 0.0698 = 69.8349 -> 69.83; * 0.1252 = 125.2626 -> 125.26
    lines = [line("1", "2026-09", 100, 1, "1000.50"), line("1", "2026-09", 19201, 2, "69.83"),
             line("1", "2026-09", 19202, 2, "125.26")]
    _, summ = check_month(snap(lines), "2026-09")
    assert summ["components"]["PSD"] == {"ok": 1, "mismatch": 0}
    assert summ["components"]["VSD"] == {"ok": 1, "mismatch": 0}


def test_pension_stopped_last_month_is_warning_not_error():
    without = plain_month("1", "2026-09", "1000", "50.60")
    _, s = check_month(snap(without), "2026-09")
    assert s["components"].get("PENS", {"ok": 0, "mismatch": 0}) == {"ok": 0, "mismatch": 0}
    hist = plain_month("1", "2026-08", "1000", "50.60", extra=[line("1", "2026-08", 19203, 2, "30.00")])
    f, _ = check_month(snap(without + hist), "2026-09")
    assert not [x for x in errors(f) if x.extra.get("component") == "PENS"]
    warn = [x for x in f if x.severity == "warning" and x.extra.get("component") == "PENS"]
    assert len(warn) == 1 and "nutrūko" in warn[0].title


def test_pension_stopped_earlier_in_year_gives_no_finding():
    now = plain_month("1", "2026-09", "1000", "50.60")
    aug = plain_month("1", "2026-08", "1000", "50.60")
    feb = plain_month("1", "2026-02", "1000", "50.60", extra=[line("1", "2026-02", 19203, 2, "30.00")])
    f, _ = check_month(snap(now + aug + feb), "2026-09")
    assert not [x for x in f if x.extra.get("component") == "PENS"]


def test_npd_mode_zero_and_f_inferred_and_asserted():
    a = plain_month("A", "2026-08", "1000", "50.60") + plain_month("A", "2026-09", "1000", "200.00")
    b = plain_month("B", "2026-08", "1000", "200.00") + plain_month("B", "2026-09", "1000", "50.60")
    f, s = check_month(snap(a + b), "2026-09")
    assert s["npd_modes"] == {"F": 1, "0": 1, "unknown": 0}
    assert {(x.ref, x.extra["expected"]) for x in errors(f)} == {("A", "50.60"), ("B", "200.00")}


def test_unknown_mode_without_history_does_not_assert_gpm():
    f, s = check_month(snap(plain_month("1", "2026-09", "1000", "123.45")), "2026-09")
    assert errors(f) == [] and s["npd_modes"]["unknown"] == 1
    assert [x.severity for x in f] == ["info"]


def test_full_npd_zero_makes_gpm_assertable_without_history():
    f, _ = check_month(snap(plain_month("1", "2026-09", "5000", "1000.00")), "2026-09")
    assert f == []
    f, _ = check_month(snap(plain_month("1", "2026-09", "5000", "999.00")), "2026-09")
    assert [x.extra["component"] for x in errors(f)] == ["GPM"]


def test_history_month_with_zero_npd_ignored_for_mode():
    h1 = plain_month("1", "2026-07", "5000", "1000.00")   # full NPD 0: uninformative
    h2 = plain_month("1", "2026-08", "1000", "50.60")
    f, s = check_month(snap(h1 + h2 + plain_month("1", "2026-09", "1000", "50.60")), "2026-09")
    assert s["npd_modes"]["F"] == 1 and errors(f) == []


def test_annual_limit_partial_history_is_mentioned():
    lines = []
    for mon in ("2026-07", "2026-08", "2026-09"):
        lines += plain_month("1", mon, "30000", "6000.00")
    f, _ = check_month(snap(lines, history_from="2026-07-01"), "2026-09")
    w = [x for x in f if x.severity == "warning" and x.ref == "1"]
    assert len(w) == 1 and "dalinė" in w[0].detail


def test_annual_limit_full_year_not_marked_partial(result):
    findings, _ = result
    w = next(f for f in findings if f.ref == "00007")
    assert "dalinė" not in w.detail


def test_vacation_code_downgrades_gpm_mismatch_to_warning():
    lines = plain_month("1", "2026-09", "1000", "99.00", extra=[line("1", "2026-09", 300, 1, "0")])
    lines += plain_month("1", "2026-08", "1000", "50.60")
    f, _ = check_month(snap(lines), "2026-09")
    assert errors(f) == []
    assert [x.severity for x in f] == ["warning"]


def test_psd_mismatch_with_vacation_code_stays_error():
    lines = plain_month("1", "2026-09", "1000", "50.60", psd="60.00",
                        extra=[line("1", "2026-09", 300, 1, "0")])
    f, _ = check_month(snap(lines), "2026-09")
    assert [x.extra["component"] for x in errors(f)] == ["PSD"]


def test_findings_identify_only_by_tb():
    findings, _ = check_month(load_snapshot(FIXTURE), "2026-09")
    assert all(f.module == "gama_tax" and f.ref.isdigit() for f in findings)


# --- CLI ---------------------------------------------------------------------

def test_cli_writes_xlsx_to_snapshot_dir(tmp_path, capsys):
    from openpyxl import load_workbook
    src = tmp_path / "snap.json"
    src.write_bytes(FIXTURE.read_bytes())
    assert cli.main([str(src)]) == 0
    wb = load_workbook(tmp_path / "gama_check_2026-09.xlsx")
    assert wb.sheetnames == ["Suvestinė", "Radiniai"]
    assert wb["Radiniai"].max_row == 1 + 5
    assert "error" in capsys.readouterr().out


def test_cli_out_dir_and_month_option(tmp_path):
    out_dir = tmp_path / "o"
    assert cli.main([str(FIXTURE), "--month", "2026-08", "--out", str(out_dir)]) == 0
    assert (out_dir / "gama_check_2026-08.xlsx").exists()


# --- Sol round 1 -------------------------------------------------------------

def test_s1_partial_year_history_adds_summary_warning_and_marks_annual_unchecked():
    lines = plain_month("1", "2026-08", "10000", "1600.00") + plain_month("1", "2026-09", "10000", "1600.00")
    f, s = check_month(snap(lines, history_from="2026-06-01"), "2026-09")
    star = [x for x in f if x.ref == "*"]
    assert len(star) == 1 and star[0].severity == "warning"
    assert star[0].title == "Metinė 36 VDU riba netikrinta: istorija nuo 2026-06-01"
    assert s["annual_check"] == "neatlikta"


def test_s1_full_year_history_marks_annual_checked(result):
    findings, s = result
    assert s["annual_check"] == "atlikta" and not [x for x in findings if x.ref == "*"]


def test_s2_zero_base_zero_gpm_month_is_uninformative():
    # sausis: bazė 0, GPM 0 (abu režimai 0) neturi nustatyti režimo "0"
    hist = plain_month("1", "2026-08", "0", "0.00")
    f, s = check_month(snap(hist + plain_month("1", "2026-09", "1000", "50.60")), "2026-09")
    assert errors(f) == [] and s["npd_modes"]["unknown"] == 1
    f2, _ = check_month(snap(hist + plain_month("1", "2026-09", "1000", "149.40")), "2026-09")
    assert not [x for x in f2 if x.extra.get("component") == "GPM" and x.severity == "error"]


def test_s2_small_base_with_zero_gpm_gives_question_mark_not_f():
    # 500 € bazė, GPM 0 atitinka F (NPD 747 > bazės); režimas F, o ne "?"
    hist = plain_month("1", "2026-07", "1000", "50.60") + plain_month("1", "2026-08", "500", "0.00")
    f, s = check_month(snap(hist + plain_month("1", "2026-09", "1000", "50.60")), "2026-09")
    assert s["npd_modes"]["F"] == 1 and errors(f) == []


def test_s6_without_history_gpm_asserted_only_when_both_modes_agree():
    # bazė 2676,10: pilnas NPD 0,99 €, režimai skiriasi 0,20 €: be istorijos nepatvirtinama
    f, s = check_month(snap(plain_month("1", "2026-09", "2676.10", "535.22")), "2026-09")
    assert errors(f) == [] and s["npd_modes"]["unknown"] == 1
    assert [x.severity for x in f if x.ref == "1"] == ["info"]


def test_s8_unknown_or_bad_month_raises():
    s = snap(plain_month("1", "2026-09", "1000", "50.60"))
    with pytest.raises(ValueError):
        check_month(s, "2027-01")
    with pytest.raises(ValueError):
        check_month(s, "2026-9")


def test_s8_cli_bad_month_exit_2(tmp_path, capsys):
    assert cli.main([str(FIXTURE), "--month", "2027-01", "--out", str(tmp_path)]) == 2
    assert cli.main([str(FIXTURE), "--month", "2026-9", "--out", str(tmp_path)]) == 2
    assert "YYYY-MM" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_s7_cli_refuses_output_inside_repo(capsys):
    # numatytoji išvestis = fixture katalogas, kuris yra repo viduje
    assert cli.main([str(FIXTURE)]) == 2
    assert "repo" in capsys.readouterr().err
    assert not (FIXTURE.parent / "gama_check_2026-09.xlsx").exists()


# --- Sol round 2 -------------------------------------------------------------

def _f_hist(tb="1"):
    return plain_month(tb, "2026-08", "1000", "50.60")      # režimas F


def test_s9_npd_split_between_base_and_sick_pay():
    sick = [line("1", "2026-09", 201, 1, "100")]
    good = plain_month("1", "2026-09", "1000", "64.18", extra=sick + [line("1", "2026-09", 19102, 2, "4.81")])
    f, s = check_month(snap(_f_hist() + good), "2026-09")
    assert errors(f) == [] and s["components"]["GPM_LIGA"]["ok"] == 1
    bad = plain_month("1", "2026-09", "1000", "50.60", extra=sick + [line("1", "2026-09", 19102, 2, "15.00")])
    f, _ = check_month(snap(_f_hist() + bad), "2026-09")
    assert {x.extra["component"] for x in errors(f)} == {"GPM", "GPM_LIGA"}


def test_s9_mode_zero_has_no_npd_on_either_component():
    hist = plain_month("1", "2026-08", "1000", "200.00")
    sick = [line("1", "2026-09", 201, 1, "100"), line("1", "2026-09", 19102, 2, "15.00")]
    f, s = check_month(snap(hist + plain_month("1", "2026-09", "1000", "200.00", extra=sick)), "2026-09")
    assert errors(f) == [] and s["npd_modes"]["0"] == 1


def test_s9_inference_uses_sick_pay_split_in_history():
    sick = [line("1", "2026-08", 201, 1, "100"), line("1", "2026-08", 19102, 2, "4.81")]
    hist = plain_month("1", "2026-08", "1000", "64.18", extra=sick)
    f, s = check_month(snap(hist + plain_month("1", "2026-09", "1000", "50.60")), "2026-09")
    assert s["npd_modes"]["F"] == 1 and errors(f) == []


def test_s11_ambiguous_history_month_is_skipped_not_f():
    # bazė 2677,30: F 535,44, 0: 535,46; faktinis 535,45 atitinka abu
    hist = plain_month("1", "2026-08", "2677.30", "535.45")
    f, s = check_month(snap(hist + plain_month("1", "2026-09", "1000", "200.00")), "2026-09")
    assert errors(f) == [] and s["npd_modes"]["unknown"] == 1


def test_s12_history_of_other_year_is_skipped_in_inference():
    hist = (plain_month("1", "2025-12", "1500", "195.88")
            + plain_month("1", "2026-08", "1000", "50.60"))
    f, s = check_month(snap(hist + plain_month("1", "2026-09", "1000", "200.00")), "2026-09")
    assert s["npd_modes"]["F"] == 1
    assert [x.extra["component"] for x in errors(f)] == ["GPM"]


def test_s12_unsupported_target_year_raises(tmp_path, capsys):
    s = snap(plain_month("1", "2025-12", "1000", "50.60"), month="2025-12")
    with pytest.raises(ValueError):
        check_month(s, "2025-12")


def test_s13_vsd_ceiling_not_asserted_and_warned():
    # sukaupta 130000 + 10000 viršija 138729: liko 8729 x 12,52 % = 1092,87
    hist = plain_month("1", "2026-08", "130000", "26000.00")
    cur = plain_month("1", "2026-09", "10000", "2000.00", vsd="1092.87")
    f, s = check_month(snap(hist + cur), "2026-09")
    assert not [x for x in errors(f) if x.extra["component"] == "VSD"]
    w = [x for x in f if x.extra.get("component") == "VSD" and x.severity == "warning"]
    assert len(w) == 1 and "VSD lubos 60 VDU pasiektos: tikrinti rankiniu būdu" in w[0].title


def test_s13_visible_ceiling_warned_even_with_incomplete_history():
    hist = plain_month("1", "2026-08", "130000", "26000.00")
    cur = plain_month("1", "2026-09", "10000", "2000.00", vsd="1092.87")
    f, _ = check_month(snap(hist + cur, history_from="2026-06-01"), "2026-09")
    assert not [x for x in errors(f) if x.extra["component"] == "VSD"]
    assert [x for x in f if x.extra.get("component") == "VSD" and x.severity == "warning"]


def test_s14_sick_gpm_checked_above_36_vdu():
    hist = plain_month("1", "2026-08", "80000", "16000.00")
    cur = plain_month("1", "2026-09", "5000", "1000.00",
                      extra=[line("1", "2026-09", 201, 1, "100.00"), line("1", "2026-09", 19102, 2, "0.00")])
    f, _ = check_month(snap(hist + cur, history_from="2026-01-01"), "2026-09")
    sick = [x for x in errors(f) if x.extra.get("component") == "GPM_LIGA"]
    assert len(sick) == 1 and sick[0].extra["expected"] == "15.00"


def test_s13_constant_matches_rates():
    from core import rates_2026 as r
    assert r.SODRA_VSD_CEILING_YEARLY == D("138729.00") == r.GPM_RATE_32_FROM_YEARLY


def test_s16_known_mode_preferred_when_modes_differ_by_cent():
    hist = plain_month("1", "2026-08", "1000", "200.00")            # režimas 0
    cur = plain_month("1", "2026-09", "2677.40", "535.49")
    f, _ = check_month(snap(hist + cur, history_from="2026-01-01"), "2026-09")
    assert not [x for x in errors(f) if x.extra.get("component") == "GPM"]


def test_s14_sick_gpm_checked_when_unknown_mode_affects_only_main_gpm():
    hist = plain_month("1", "2026-01", "84000", "16800.00")
    cur = plain_month("1", "2026-09", "2577.10", "500.00",
                      extra=[line("1", "2026-09", 201, 1, "100.00"), line("1", "2026-09", 19102, 2, "0.00")])
    f, _ = check_month(snap(hist + cur, history_from="2026-01-01"), "2026-09")
    sick = [x for x in errors(f) if x.extra.get("component") == "GPM_LIGA"]
    assert len(sick) == 1 and sick[0].extra["expected"] == "15.00"


def test_s17_progressive_gpm_lines_go_to_manual_check():
    hist = plain_month("1", "2026-08", "1000", "200.00")            # režimas 0
    cur = plain_month("1", "2026-09", "1000", "200.00", extra=[line("1", "2026-09", 19104, 2, "250.00")])
    f, s = check_month(snap(hist + cur, history_from="2026-01-01"), "2026-09")
    assert [x for x in f if x.severity == "warning" and x.extra.get("component") == "GPM" and x.ref == "1"]
    assert s["components"]["GPM"]["ok"] == 0


def test_s16_unknown_mode_accepts_either_close_result():
    cur = plain_month("1", "2026-09", "2677.40", "535.49")
    f, _ = check_month(snap(cur, history_from="2026-01-01"), "2026-09")
    assert not [x for x in errors(f) if x.extra.get("component") == "GPM"]


def test_s18_unchecked_sick_gpm_is_reported():
    cur = [line("1", "2026-09", 201, 1, "100.00"), line("1", "2026-09", 19102, 2, "123.45")]
    f, _ = check_month(snap(cur, history_from="2026-01-01"), "2026-09")
    assert [x for x in f if x.extra.get("component") == "GPM_LIGA"]


def test_s19_no_pension_stop_warning_when_base_zero():
    hist = plain_month("1", "2026-08", "1000", "50.60", extra=[line("1", "2026-08", 19203, 2, "30.00")])
    cur = [line("1", "2026-09", 201, 1, "100.00"), line("1", "2026-09", 19102, 2, "15.00")]
    f, _ = check_month(snap(hist + cur, history_from="2026-01-01"), "2026-09")
    assert not [x for x in f if x.extra.get("component") == "PENS"]


# --- A02 kintamieji, alga, tabelis (2026-11-05) --------------------------------

from decimal import ROUND_HALF_UP  # noqa: E402

A02_FIXTURE = Path(__file__).parent / "fixtures" / "gama_snapshot_a02_synthetic.json"
M = "2026-09"


def _q(x):
    return D(x).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def var(tb, code, tipas, suma, date="2026-01-01"):
    return {"A02_KODAS_LK": str(code).zfill(12), "A02_TIPAS": str(tipas), "A02_DATE": date,
            "A02_KODAS_TB": tb, "A02_SUMA": str(suma)}


def ts_row(tb, lt, hours, month=M, frak="60", la="L1"):
    return {"A18_KODAS_IS": "1", "A18_DATE": f"{month}-01", "A18_KODAS_TB": tb, "A18_KODAS_LT": str(lt).zfill(12),
            "A18_KODAS_LG": "", "A18_KODAS_LA": la, "A18_FRAKCIJA": frak, "A18_VD": "0",
            "A18_V00": str(int(D(str(hours)) * D(frak)))}


def emp(tb, base, gpm, extra=(), laikas=None):
    """Mėnesio eilutės: kodas 100 = base (su LAIKAS), PSD/VSD pagal bazę, GPM pagal pateiktą sumą."""
    base = D(base)
    rows = [line(tb, M, 100, 1, base), line(tb, M, 19201, 2, _q(base * D("0.0698"))),
            line(tb, M, 19202, 2, _q(base * D("0.1252"))), line(tb, M, 19101, 2, gpm), *extra]
    if laikas is not None:
        rows[0]["A20_LAIKAS"] = str(laikas)
    return rows


def gpm0(base):          # NPD netaikomas
    return _q(D(base) * D("0.2"))


def _build():
    pay, variables, ts = [], [], []
    # 00011: alga 1755.99, FD 32 h, norma 176 => 319.27; NPD flag 0 (pilnas), kaupimas 3 %
    pay += emp("00011", "319.27", "0.00", laikas="32",
               extra=[line("00011", M, 19203, 2, _q(D("319.27") * D("0.03")))])
    variables += [var("00011", 100, 4, "1755.99"), var("00011", 10001, 3, 0), var("00011", 19203, 3, 3)]
    ts += [ts_row("00011", 1, 32), ts_row("00011", 606, 4, frak="100")]
    # 00012: IŠSKIRTA ALGOS KLAIDA: turi būti 1183.64, mokėta 1100.00; NPD netaikomas, kaupimo nėra (19203 = 0)
    pay += emp("00012", "1100.00", gpm0("1100.00"), laikas="168")
    variables += [var("00012", 100, 4, "1240"), var("00012", 10001, 3, 1), var("00012", 19203, 3, 0)]
    ts += [ts_row("00012", 1, 168), ts_row("00012", 606, 21, frak="100")]
    # 00013: valandų neatitikimas: suma teisinga (1183.64), laikas 160 vs FD 168; kaupimas privalomas, eilutės nėra
    pay += emp("00013", "1183.64", gpm0("1183.64"), laikas="160")
    variables += [var("00013", 100, 4, "1240"), var("00013", 10001, 3, 1), var("00013", 19203, 3, 3)]
    ts += [ts_row("00013", 1, 168), ts_row("00013", 606, 21, frak="100")]
    # 00014: riboto darbingumo NPD => GPM netikrinamas (GPM sąmoningai neteisingas)
    pay += emp("00014", "1000.00", "1.00", laikas="176")
    variables += [var("00014", 100, 4, "1000"), var("00014", 10001, 3, 0), var("00014", 10003, 3, 690)]
    ts += [ts_row("00014", 1, 176), ts_row("00014", 606, 22, frak="100")]
    # 00015: priedai: įkainis 10 EUR/h (alga 1760 / norma 176); FD 150 h = 1500.00; VD 10 h (103 = 150.00, 1,5x);
    # naktis 8 h (105 = 40.00, 0,5x); poilsio 4 h (107 = 80.00, 2x: pilnas apmokėjimas). Norma iš kalendoriaus (A15), tabelyje 606 nėra.
    pay += emp("00015", "1500.00", gpm0("1770.00"), laikas="150",
               extra=[line("00015", M, 103, 1, "150.00"), line("00015", M, 105, 1, "40.00"),
                      line("00015", M, 107, 1, "80.00")])
    for r in pay:
        if r["A20_KODAS_TB"] == "00015" and r["A20_KODAS_LP"].endswith("19201"):
            r["A20_SUMA"] = str(_q(D("1770") * D("0.0698")))
        if r["A20_KODAS_TB"] == "00015" and r["A20_KODAS_LP"].endswith("19202"):
            r["A20_SUMA"] = str(_q(D("1770") * D("0.1252")))
    variables += [var("00015", 100, 4, "1760"), var("00015", 10001, 3, 1)]
    ts += [ts_row("00015", 1, 150, la="K1"), ts_row("00015", 102, 10, la="K1"), ts_row("00015", 101, 8, la="K1"),
           ts_row("00015", 107, 4, la="K1")]
    # Kalendoriai L1 ir K1: FD (LT 1) = 176 h normos valandos, DD (LT 606) = 22 normos dienos (frakcija 100).
    calendar = [{"A15_KODAS_LA": la, "A15_DATE": f"{M}-01", "A15_KODAS_LT": lt.zfill(12), "A15_KODAS_LG": "",
                 "A15_FRAKCIJA": frak, "A15_VD": "0", "A15_V00": v00}
                for la in ("L1", "K1") for lt, frak, v00 in (("1", "60", str(176 * 60)), ("606", "100", "2200"))]
    return {"month": M, "history_from": "2026-01-01", "employees": [], "pay_lines": pay,
            "variables": variables, "timesheet_hours": ts, "calendar_hours": calendar}


def a02_check(raw=None):
    return check_month(Snapshot.from_dict(raw or _build()), M)


def by_ref(findings, tb, **kw):
    return [f for f in findings if f.ref == tb and all(f.extra.get(k) == v for k, v in kw.items())]


def test_a02_fixture_file_matches_builder():
    import json
    assert json.loads(A02_FIXTURE.read_text(encoding="utf-8")) == _build()


def test_a02_salary_error_and_hours_mismatch_only():
    f, s = a02_check()
    alga = {(x.ref, x.title.split(":")[0]) for x in errors(f) if x.extra["component"] == "ALGA"}
    assert alga == {("00012", "ALGA"), ("00013", "Valandos nesutampa su tabeliu")}
    sal = [x for x in by_ref(f, "00012", component="ALGA") if x.title.startswith("ALGA")][0]
    assert (sal.extra["expected"], sal.extra["actual"]) == ("1183.64", "1100.00")
    hrs = by_ref(f, "00013", component="ALGA")[0]
    assert (hrs.extra["expected"], hrs.extra["actual"]) == ("168", "160")
    assert s["components"]["ALGA"]["mismatch"] == 2


def test_a02_correct_salary_example_319_27():
    f, _ = a02_check()
    assert not by_ref(f, "00011", component="ALGA")
    assert not by_ref(f, "00011", component="GPM")


def test_a02_npd_flag_zero_means_full_and_one_means_none():
    f, s = a02_check()
    assert s["npd_source"] == {"A02": 5, "istorija": 0, "nežinoma": 0}
    assert s["npd_modes"] == {"F": 2, "0": 3, "unknown": 0}
    assert not [x for x in errors(f) if x.extra["component"] == "GPM"]


def test_a02_flag_wrong_gpm_is_error():
    raw = _build()
    for r in raw["pay_lines"]:
        if r["A20_KODAS_TB"] == "00012" and r["A20_KODAS_LP"].endswith("19101"):
            r["A20_SUMA"] = "70.60"      # tarsi pilnas NPD, bet GAMA žymė rodo "netaikomas"
    f, _ = a02_check(raw)
    assert [x.ref for x in errors(f) if x.extra["component"] == "GPM"] == ["00012"]


def test_a02_flag_contradicting_history_gives_warning():
    raw = _build()
    raw["pay_lines"] += plain_month("00012", "2026-08", "1000", "50.60")      # istorija: pilnas NPD
    f, s = a02_check(raw)
    w = [x for x in f if x.ref == "00012" and x.title == "NPD nustatymas GAMA nesutampa su faktiniu skaičiavimu"]
    assert len(w) == 1 and w[0].severity == "warning"
    assert s["npd_source"]["A02"] == 5


def test_a02_missing_flag_falls_back_to_history_and_unknown():
    raw = _build()
    raw["variables"] = [v for v in raw["variables"] if not (
        v["A02_KODAS_TB"] in ("00012", "00013") and v["A02_KODAS_LK"].endswith("10001"))]
    raw["pay_lines"] += plain_month("00012", "2026-08", "1000", "200.00")    # istorija: 0
    _, s = a02_check(raw)
    assert s["npd_source"] == {"A02": 3, "istorija": 1, "nežinoma": 1}


def test_a02_limited_capacity_npd_warns_and_skips_gpm():
    f, _ = a02_check()
    w = [x for x in f if x.ref == "00014"]
    assert [x.title for x in w if x.severity == "warning"] == ["Riboto darbingumo NPD: tikrinti rankiniu būdu"]
    assert not [x for x in w if x.severity == "error" and x.extra["component"] == "GPM"]


def test_a02_variable_value_uses_latest_date_not_after_month():
    raw = _build()
    raw["variables"] += [var("00012", 10001, 3, 0, date="2026-10-01"),   # ateities: ignoruojama
                         var("00012", 10001, 3, 0, date="2025-06-01")]    # senesnė: nugalima 2026-01-01
    f, _ = a02_check(raw)
    assert [x.ref for x in errors(f) if x.extra["component"] == "GPM"] == []
    raw["variables"] += [var("00012", 10001, 3, 0, date="2026-09-01")]
    f, _ = a02_check(raw)
    assert "00012" in [x.ref for x in errors(f) if x.extra["component"] == "GPM"]


def test_a02_pension_variable_authoritative():
    f, s = a02_check()
    assert {x.ref for x in errors(f) if x.extra["component"] == "PENS"} == {"00013"}   # 3 % laukiama, eilutės nėra
    assert s["components"]["PENS"]["ok"] == 2          # 00011 (yra), 00012 (0 = nėra)
    raw = _build()
    raw["pay_lines"].append(line("00012", M, 19203, 2, "33.00"))   # 0 => eilutės neturi būti
    f, _ = a02_check(raw)
    assert "00012" in {x.ref for x in errors(f) if x.extra["component"] == "PENS"}


def test_a02_overtime_night_rest_ok_with_calendar_norm():
    f, s = a02_check()
    assert not [x for x in f if x.ref == "00015" and x.severity in ("error", "warning")]
    assert s["components"]["PRIEDAI_LAIKAS"] == {"ok": 3, "mismatch": 0}
    night = by_ref(f, "00015", component="PRIEDAI_LAIKAS")
    assert {x.extra["variant"] for x in night} == {"0.5"} and all(x.severity == "info" for x in night)


def test_a02_overtime_below_dk144_minimum_is_warning():
    raw = _build()
    for r in raw["pay_lines"]:
        if r["A20_KODAS_TB"] == "00015" and r["A20_KODAS_LP"].endswith("103"):
            r["A20_SUMA"] = "100.00"
    f, s = a02_check(raw)
    w = [x for x in f if x.title == "Mokama mažiau nei DK 144 str. minimumas"]
    assert len(w) == 1 and w[0].severity == "warning" and "150.00" in w[0].detail
    assert s["components"]["PRIEDAI_LAIKAS"]["mismatch"] == 1


def test_a02_overtime_paid_more_gives_no_finding():
    raw = _build()
    for r in raw["pay_lines"]:
        if r["A20_KODAS_TB"] == "00015" and r["A20_KODAS_LP"].endswith("103"):
            r["A20_SUMA"] = "300.00"
    f, _ = a02_check(raw)
    assert not [x for x in f if x.title.startswith("Mokama mažiau")]


def _drop_100(raw, tb):
    raw["pay_lines"] = [r for r in raw["pay_lines"]
                        if not (r["A20_KODAS_TB"] == tb and r["A20_KODAS_LP"].endswith("100")
                                and not r["A20_KODAS_LP"].endswith("19100"))]


def test_a02_hourly_paid_employee_skipped_with_info():
    raw = _build()
    _drop_100(raw, "00011")
    raw["pay_lines"].append(line("00011", M, 101, 1, "10.00"))
    f, _ = a02_check(raw)
    assert [x.severity for x in by_ref(f, "00011", component="ALGA")] == ["info"]


def test_a02_missing_salary_variable_is_info():
    raw = _build()
    raw["variables"] = [v for v in raw["variables"] if not (
        v["A02_KODAS_TB"] == "00011" and v["A02_KODAS_LK"].endswith("0100"))]
    f, _ = a02_check(raw)
    i = by_ref(f, "00011", component="ALGA")
    assert [x.severity for x in i] == ["info"] and i[0].title == "Alga netikrinta"


def test_a02_zero_norm_is_info():
    raw = _build()
    raw["calendar_hours"] = [c for c in raw["calendar_hours"] if c["A15_KODAS_LA"] != "L1"]   # nėra normos
    f, _ = a02_check(raw)
    assert [x.title for x in by_ref(f, "00011", component="ALGA")] == ["Alga netikrinta"]


def test_a02_legacy_fixture_summary_has_npd_source_and_no_alga_noise(result):
    _, s = result
    assert sum(s["npd_source"].values()) == 8 and s["npd_source"]["A02"] == 0
    assert s["components"]["ALGA"] == {"ok": 0, "mismatch": 0}


# --- Sol round 6 ---------------------------------------------------------------

def _set_line(raw, tb, code, suma):
    for r in raw["pay_lines"]:
        if r["A20_KODAS_TB"] == tb and r["A20_KODAS_LP"].endswith(str(code)):
            r["A20_SUMA"] = str(suma)


def _titles(f, tb):
    return [x.title for x in f if x.ref == tb]


def test_s1_supplement_only_needs_confirmed_base_pay():
    raw = _build()
    _set_line(raw, "00015", 100, "0.00")           # bazinis apmokėjimas neatitinka: 105 / 107 tik priedai nepriimami
    f, _ = a02_check(raw)
    low = [x for x in f if x.ref == "00015" and x.title == "Mokama mažiau nei DK 144 str. minimumas"]
    assert {x.detail.split(",")[0] for x in low} == {"Priedas 105"}
    assert "120.00" in next(x for x in low if "105" in x.detail).detail      # 8 h x 10 x 1,5


def test_s1_supplement_only_rejected_when_laikas_differs_from_fd():
    raw = _build()
    for r in raw["pay_lines"]:
        if r["A20_KODAS_TB"] == "00015" and r["A20_KODAS_LP"].endswith("100"):
            r["A20_LAIKAS"] = "140"
    f, _ = a02_check(raw)
    assert "Valandos nesutampa su tabeliu" in _titles(f, "00015")
    assert len([x for x in f if x.ref == "00015" and x.title.startswith("Mokama mažiau")]) == 1


def test_s1_supplement_only_rejected_when_hours_exceed_fd():
    raw = _build()
    for r in raw["timesheet_hours"]:
        if r["A18_KODAS_TB"] == "00015" and r["A18_KODAS_LT"].endswith("101"):
            r["A18_V00"] = str(160 * 60)           # naktinių 160 h > FD 150 h
    _set_line(raw, "00015", 105, "800.00")         # 0,5x = 800 (pilnas 1,5x = 2400)
    f, _ = a02_check(raw)
    assert any(x.ref == "00015" and x.title.startswith("Mokama mažiau") and "Priedas 105" in x.detail for x in f)


def test_s2_worked_without_any_pay_lines_is_error_and_counted():
    raw = _build()
    raw["timesheet_hours"] += [ts_row("00099", 1, 160), ts_row("00098", 1, 0)]
    f, s = a02_check(raw)
    miss = [x for x in f if x.title == "Tabelyje dirbta, bet GAMA priskaitymų nėra"]
    assert [(x.ref, x.severity, x.extra["component"]) for x in miss] == [("00099", "error", "ALGA")]
    assert s["employees"] == 6


def test_s3_missing_fd_row_is_not_zero_hours():
    raw = _build()
    raw["pay_lines"] += emp("00016", "0.00", "0.00")
    raw["variables"] += [var("00016", 100, 4, "1240"), var("00016", 10001, 3, 1), var("00016", 19203, 3, 0)]
    raw["timesheet_hours"] += [ts_row("00016", 606, 21, frak="100")]
    f, _ = a02_check(raw)
    e = [x for x in by_ref(f, "00016", component="ALGA") if x.severity == "error"]
    assert [(x.severity, x.extra["expected"], x.extra["actual"]) for x in e] == [("error", "1183.64", "0.00")]
    # S17: be FD eilutės valandų patikra pažymima rankinei patikrai
    assert [x for x in by_ref(f, "00016", component="ALGA") if "Valandų patikra neatlikta" in x.title]


def test_s3_no_candidates_gives_info():
    raw = _build()
    raw["pay_lines"] += emp("00016", "0.00", "0.00")
    raw["variables"] += [var("00016", 100, 4, "1240"), var("00016", 10001, 3, 1), var("00016", 19203, 3, 0)]
    raw["timesheet_hours"] += [ts_row("00016", 102, 2)]
    f, _ = a02_check(raw)
    assert sorted((x.severity, x.title) for x in by_ref(f, "00016", component="ALGA")) == [
        ("info", "Alga netikrinta"), ("warning", "Valandų patikra neatlikta: tikrinti rankiniu būdu")]


def _with_fixed_supplement(paid):
    raw = _build()
    raw["variables"] += [var("00015", 801, 4, "1760", date=f"{M}-01")]   # 801 galioja tik savo mėnesį
    _set_line(raw, "00015", 103, paid)
    return a02_check(raw)[0]


def test_s4_fixed_supplements_raise_rate_base_warns_possibly_too_little():
    f = _with_fixed_supplement("150.00")           # >= alga/norma minimumas (150), < su priedais (300)
    w = [x for x in f if x.ref == "00015" and x.title.startswith("Galimai per mažai") and "Priedas 103" in x.detail]
    assert len(w) == 1 and w[0].severity == "warning" and "150.00" in w[0].detail and "300.00" in w[0].detail
    assert not [x for x in f if x.title == "Mokama mažiau nei DK 144 str. minimumas"]


def test_s4_below_lower_minimum_keeps_old_warning_and_full_pay_is_clean():
    assert any(x.title == "Mokama mažiau nei DK 144 str. minimumas" for x in _with_fixed_supplement("100.00"))
    f = _with_fixed_supplement("300.00")
    assert not [x for x in f if x.severity == "warning" and x.ref == "00015" and "Priedas 103" in x.detail]


def test_s5_hourly_paid_extras_use_a02_hourly_rate():
    raw = _build()
    _drop_100(raw, "00011")
    raw["pay_lines"] += [line("00011", M, 101, 1, "10.00"), line("00011", M, 103, 1, "50.00")]
    raw["variables"].append(var("00011", 101, 4, "10"))
    raw["timesheet_hours"].append(ts_row("00011", 102, 10))
    f, _ = a02_check(raw)
    low = [x for x in f if x.ref == "00011" and x.title == "Mokama mažiau nei DK 144 str. minimumas"]
    assert len(low) == 1 and "150.00" in low[0].detail


def test_s5_hourly_paid_without_rate_gives_info_when_extras_exist():
    raw = _build()
    _drop_100(raw, "00011")
    raw["pay_lines"].append(line("00011", M, 101, 1, "10.00"))
    raw["timesheet_hours"].append(ts_row("00011", 102, 10))
    f, _ = a02_check(raw)
    assert "Priedai netikrinti: nėra valandos įkainio" in _titles(f, "00011")


def test_s6_laikas_checked_without_salary_or_norm():
    raw = _build()
    raw["variables"] = [v for v in raw["variables"] if not (
        v["A02_KODAS_TB"] == "00013" and v["A02_KODAS_LK"].endswith("0100"))]
    raw["calendar_hours"] = [c for c in raw["calendar_hours"] if c["A15_KODAS_LA"] != "L1"]
    f, _ = a02_check(raw)
    assert "Valandos nesutampa su tabeliu" in _titles(f, "00013")


def test_s6_day_formula_does_not_need_norm_hours():
    raw = _build()
    raw["calendar_hours"] = [c for c in raw["calendar_hours"]
                             if not (c["A15_KODAS_LA"] == "L1" and c["A15_KODAS_LT"].endswith("0001"))]
    f, _ = a02_check(raw)
    sal = [x for x in by_ref(f, "00012", component="ALGA") if x.title.startswith("ALGA")]
    assert [(x.extra["expected"], x.extra["actual"]) for x in sal] == [("1183.64", "1100.00")]


def test_s7_variable_differing_by_unit_is_ambiguous_and_flagged():
    from core.gama import variable_value
    raw = _build()
    for v in raw["variables"]:
        v["A02_KODAS_IS"] = "1"
    raw["variables"] += [{**var("00012", 100, 4, "900"), "A02_KODAS_IS": "2"},
                         {**var("00012", 10001, 3, 0), "A02_KODAS_IS": "2"}]
    snap_ = Snapshot.from_dict(raw)
    assert variable_value(snap_, "00012", "100", "4", M) is None
    assert variable_value(snap_, "00011", "100", "4", M) == D("1755.99")
    f, _ = check_month(snap_, M)
    amb = [x for x in f if x.ref == "00012" and x.title == "Kintamasis skiriasi pagal padalinį: tikrinti rankiniu būdu"]
    assert {x.extra["component"] for x in amb} == {"ALGA", "GPM"}
    assert not [x for x in by_ref(f, "00012", component="ALGA") if x.severity == "error"]


def test_s7_snapshot_script_exports_unit():
    text = (Path(__file__).parent.parent / "tools" / "gama_snapshot.ps1").read_text(encoding="utf-8-sig")
    assert "A02_KODAS_TB, A02_KODAS_IS, A02_SUMA" in text


# --- Sol round 7 ---------------------------------------------------------------

def _new_emp(raw, tb, lines_extra=(), ts_rows=(), variables=()):
    raw["pay_lines"] += [*lines_extra]
    raw["variables"] += [*variables]
    raw["timesheet_hours"] += [*ts_rows]


def test_r7_s1_rest_day_supplement_only_is_manual_not_ok():
    raw = _build()
    _set_line(raw, "00015", 107, "40.00")          # 4 h x 10 x 1 = tik priedas
    f, s = a02_check(raw)
    w = [x for x in f if x.ref == "00015" and x.title.startswith("Poilsio / švenčių dienos darbas")]
    assert len(w) == 1 and w[0].severity == "warning" and w[0].extra["component"] == "PRIEDAI_LAIKAS"
    assert w[0].extra["candidates"] == {"1": "40.00", "2": "80.00"}
    assert s["components"]["PRIEDAI_LAIKAS"] == {"ok": 2, "mismatch": 1}
    f, _ = a02_check()                              # 2x apmokėta: švaru
    assert not [x for x in f if x.ref == "00015" and x.title.startswith("Poilsio")]


def test_r7_s2_worked_marks_other_than_fd_vd_count_as_unpaid_work():
    raw = _build()
    raw["timesheet_hours"] += [ts_row("00097", 107, 8), ts_row("00096", 101, 4), ts_row("00095", 102, 2),
                               ts_row("00094", 107, 0)]
    f, s = a02_check(raw)
    miss = {x.ref for x in f if x.title == "Tabelyje dirbta, bet GAMA priskaitymų nėra"}
    assert miss == {"00097", "00096", "00095"}
    assert s["employees"] == 8


def test_r7_s7_latest_row_per_department_equal_values_are_not_ambiguous():
    from core.gama import variable_ambiguous, variable_value
    raw = _build()
    raw["variables"] = [v for v in raw["variables"] if not (
        v["A02_KODAS_TB"] == "00012" and v["A02_KODAS_LK"].endswith("0100"))]
    raw["variables"] += [{**var("00012", 100, 4, "1240", date="2026-01-01"), "A02_KODAS_IS": "1"},
                         {**var("00012", 100, 4, "1240", date="2026-05-01"), "A02_KODAS_IS": "2"}]
    sn = Snapshot.from_dict(raw)
    assert variable_value(sn, "00012", "100", "4", M) == D("1240")
    assert not variable_ambiguous(sn, "00012", "100", "4", M)


def test_r7_s7_older_row_of_other_department_still_counts():
    from core.gama import variable_ambiguous, variable_value
    raw = {"month": M, "history_from": "2026-01-01", "pay_lines": [], "variables": [
        {**var("T1", 100, 4, "1240", date="2026-01-01"), "A02_KODAS_IS": "1"},
        {**var("T1", 100, 4, "900", date="2026-05-01"), "A02_KODAS_IS": "2"}]}
    sn = Snapshot.from_dict(raw)
    assert variable_value(sn, "T1", "100", "4", M) is None and variable_ambiguous(sn, "T1", "100", "4", M)


def test_r7_s9_monthly_only_variables_do_not_carry_over():
    from core.gama import MONTHLY_ONLY_VARIABLES, variable_value
    assert {("801", "4"), ("805", "4"), ("806", "4")} <= MONTHLY_ONLY_VARIABLES
    raw = _build()
    raw["variables"] += [var("00015", 801, 4, "1760", date="2026-08-01")]
    sn = Snapshot.from_dict(raw)
    assert variable_value(sn, "00015", "801", "4", M) is None
    assert variable_value(sn, "00015", "100", "4", M) == D("1760")      # kintamasis be G_MEN keliauja toliau
    f, _ = a02_check(raw)
    assert not [x for x in f if x.ref == "00015" and x.severity in ("warning", "error")]
    raw["variables"] += [var("00015", 801, 4, "50", date="2026-09-01")]
    assert variable_value(Snapshot.from_dict(raw), "00015", "801", "4", M) == D("50")


def test_r7_s10_extra_line_without_timesheet_row_is_manual():
    title = "Priedas be tabelio valandų: tikrinti rankiniu būdu"
    raw = _build()
    raw["timesheet_hours"] = [r for r in raw["timesheet_hours"] if not (
        r["A18_KODAS_TB"] == "00015" and r["A18_KODAS_LT"].endswith("102"))]
    f, s = a02_check(raw)
    w = [x for x in f if x.ref == "00015" and x.title == title]
    assert len(w) == 1 and w[0].severity == "warning"
    assert s["components"]["PRIEDAI_LAIKAS"]["ok"] == 2
    raw = _build()
    for r in raw["timesheet_hours"]:
        if r["A18_KODAS_TB"] == "00015" and r["A18_KODAS_LT"].endswith("102"):
            r["A18_V00"] = "0"
    f, _ = a02_check(raw)
    assert len([x for x in f if x.title == title]) == 1


def test_r7_s11_extras_checked_without_salary_candidates():
    raw = _build()
    _new_emp(raw, "00016", [line("00016", M, 103, 1, "1.00")], [ts_row("00016", 102, 10)],
             [var("00016", 100, 4, "1760")])
    f, _ = a02_check(raw)
    low = [x for x in f if x.ref == "00016" and x.title == "Mokama mažiau nei DK 144 str. minimumas"]
    assert len(low) == 1 and "150.00" in low[0].detail
    assert "Alga netikrinta" in _titles(f, "00016")


def _hourly(amount, laikas="160", night="40.00"):
    raw = _build()
    base = line("00016", M, 101, 1, amount)
    base["A20_LAIKAS"] = laikas
    _new_emp(raw, "00016", [base, line("00016", M, 105, 1, night)],
             [ts_row("00016", 1, 160), ts_row("00016", 101, 8)], [var("00016", 101, 4, "10")])
    return a02_check(raw)[0]


def test_r7_s12_hourly_base_confirmed_allows_night_supplement_only():
    f = _hourly("1600.00")
    assert not [x for x in f if x.ref == "00016" and x.severity in ("warning", "error")
                and x.extra.get("component") == "PRIEDAI_LAIKAS"]
    f = _hourly("1500.00")
    assert any(x.ref == "00016" and x.title == "Mokama mažiau nei DK 144 str. minimumas" for x in f)
    f = _hourly("1600.00", laikas="150")
    assert any(x.ref == "00016" and x.title == "Mokama mažiau nei DK 144 str. minimumas" for x in f)


# --- Sol round 8 ---------------------------------------------------------------

AMB = "Kintamasis skiriasi pagal padalinį: tikrinti rankiniu būdu"


def _split_unit(raw, tb, code, tipas, a, b, date="2026-01-01"):
    """Kintamasis du padaliniuose su skirtingomis reikšmėmis (kiti kintamieji gauna padalinį 1)."""
    for v in raw["variables"]:
        v.setdefault("A02_KODAS_IS", "1")
    raw["variables"] = [v for v in raw["variables"]
                        if not (v["A02_KODAS_TB"] == tb and v["A02_KODAS_LK"].endswith(str(code).zfill(4)[-4:])
                                and v["A02_TIPAS"] == str(tipas))]
    raw["variables"] += [{**var(tb, code, tipas, a, date), "A02_KODAS_IS": "1"},
                         {**var(tb, code, tipas, b, date), "A02_KODAS_IS": "2"}]


def test_s14_ambiguous_10003_is_warning_and_gpm_not_asserted():
    raw = _build()
    for v in raw["variables"]:
        v["A02_KODAS_IS"] = "1"
    raw["variables"] = [v for v in raw["variables"] if not (v["A02_KODAS_TB"] == "00014" and v["A02_KODAS_LK"].endswith("10003"))]
    raw["variables"] += [{**var("00014", 10003, 3, 690), "A02_KODAS_IS": "1"},
                         {**var("00014", 10003, 3, 0), "A02_KODAS_IS": "2"}]
    f, _ = a02_check(raw)
    assert not [x for x in by_ref(f, "00014", component="GPM") if x.severity == "error"]
    assert [x.severity for x in f if x.ref == "00014" and x.title == AMB and "10003" in x.detail] == ["warning"]


def test_s14_ambiguous_19203_not_asserted_and_warned():
    raw = _build()
    for v in raw["variables"]:
        v["A02_KODAS_IS"] = "1"
    raw["variables"] = [v for v in raw["variables"] if not (v["A02_KODAS_TB"] == "00011" and v["A02_KODAS_LK"].endswith("19203"))]
    raw["variables"] += [{**var("00011", 19203, 3, 3), "A02_KODAS_IS": "1"},
                         {**var("00011", 19203, 3, 0), "A02_KODAS_IS": "2"}]
    raw["pay_lines"] = [r for r in raw["pay_lines"] if not (r["A20_KODAS_TB"] == "00011" and r["A20_KODAS_LP"].endswith("19203"))]
    f, _ = a02_check(raw)
    assert not by_ref(f, "00011", component="PENS", expected="9.58") and not [
        x for x in by_ref(f, "00011", component="PENS") if x.severity == "error"]
    assert [x.severity for x in by_ref(f, "00011", component="PENS") if x.title == AMB] == ["warning"]


def test_s14_ambiguous_fixed_supplement_skips_full_rate_with_info():
    raw = _build()
    for v in raw["variables"]:
        v["A02_KODAS_IS"] = "1"
    raw["variables"] += [{**var("00015", 801, 4, 880, f"{M}-01"), "A02_KODAS_IS": "1"},
                         {**var("00015", 801, 4, 1760, f"{M}-01"), "A02_KODAS_IS": "2"}]
    f, _ = a02_check(raw)
    amb = [x for x in f if x.ref == "00015" and x.title == AMB and "801" in x.detail]
    assert len(amb) == 1 and amb[0].severity == "info" and amb[0].extra["component"] == "PRIEDAI_LAIKAS"
    assert not [x for x in f if x.title.startswith("Galimai per mažai")]


def test_s14_ambiguous_hourly_rate_gives_manual_info():
    raw = _build()
    _drop_100(raw, "00011")
    raw["pay_lines"].append(line("00011", M, 101, 1, "10.00"))
    raw["timesheet_hours"].append(ts_row("00011", 102, 10))
    raw["variables"] += [{**var("00011", 101, 4, 10), "A02_KODAS_IS": "1"}, {**var("00011", 101, 4, 20), "A02_KODAS_IS": "2"}]
    f, _ = a02_check(raw)
    assert any(x.ref == "00011" and x.title == AMB and "101" in x.detail for x in f)
    assert "Priedai netikrinti: nėra valandos įkainio" not in _titles(f, "00011")


def test_s15_zero_101_does_not_hide_code_100_hours_check():
    raw = _build()
    raw["pay_lines"].append(line("00013", M, 101, 1, "0.00"))
    f, _ = a02_check(raw)
    assert any(x.ref == "00013" and x.title == "Valandos nesutampa su tabeliu" for x in f)
    assert not any(x.ref == "00013" and x.title.startswith("Mišrus") for x in f)
    assert not any(x.ref == "00013" and x.title == "Alga netikrinta: valandinis atlyginimas" for x in f)


def test_s15_mixed_100_and_101_warns_and_still_checks_hours():
    raw = _build()
    raw["pay_lines"].append(line("00013", M, 101, 1, "50.00"))
    f, _ = a02_check(raw)
    assert [x.severity for x in f if x.ref == "00013" and x.title == "Mišrus atlygis (100 ir 101): tikrinti rankiniu būdu"] == ["warning"]
    assert any(x.ref == "00013" and x.title == "Valandos nesutampa su tabeliu" for x in f)


def _hourly_with_supplement(norm=True):
    raw = _build()
    _drop_100(raw, "00011")
    raw["pay_lines"] += [line("00011", M, 101, 1, "1500.00"), line("00011", M, 103, 1, "150.00")]
    raw["variables"] += [var("00011", 101, 4, "10"), var("00011", 801, 4, "1760", f"{M}-01")]
    raw["timesheet_hours"] += [ts_row("00011", 102, 10)]
    if not norm:
        raw["calendar_hours"] = []
    return a02_check(raw)[0]


def test_s16_hourly_rate_includes_fixed_supplements():
    f = _hourly_with_supplement()
    w = [x for x in f if x.ref == "00011" and x.title.startswith("Galimai per mažai")]
    assert len(w) == 1 and "300.00" in w[0].detail


def test_s16_hourly_supplements_without_norm_warns():
    f = _hourly_with_supplement(norm=False)
    w = [x for x in f if x.ref == "00011" and x.title == "Pastovių priedų įtraukti į įkainį nepavyko: nėra normos"]
    assert len(w) == 1 and w[0].severity == "warning" and w[0].extra["component"] == "PRIEDAI_LAIKAS"


def test_s14_ambiguous_npd_flag_does_not_use_history_mode():
    hist = plain_month("1", "2026-08", "1000", "200.00")            # istorija: režimas 0
    cur = plain_month("1", "2026-09", "1100", "70.60")
    from core.gama import Snapshot
    d = {"month": "2026-09", "history_from": "2026-01-01", "employees": [], "pay_lines": hist + cur,
         "variables": [{"A02_KODAS_LK": "000000010001", "A02_TIPAS": "3", "A02_DATE": "2026-01-01",
                        "A02_KODAS_TB": "1", "A02_KODAS_IS": "A", "A02_SUMA": "0"},
                       {"A02_KODAS_LK": "000000010001", "A02_TIPAS": "3", "A02_DATE": "2026-01-01",
                        "A02_KODAS_TB": "1", "A02_KODAS_IS": "B", "A02_SUMA": "1"}]}
    f, _ = check_month(Snapshot.from_dict(d), "2026-09")
    assert not [x for x in errors(f) if x.extra.get("component") == "GPM"]
    assert [x for x in f if x.severity == "warning" and x.extra.get("component") == "GPM"]


def test_s17_missing_fd_row_with_code_100_is_flagged():
    raw = _build()
    raw["timesheet_hours"] = [t for t in raw["timesheet_hours"] if not (
        t["A18_KODAS_TB"] == "00011" and t["A18_KODAS_LT"].endswith("000000000001"))]
    f, _ = a02_check(raw)
    assert [x for x in by_ref(f, "00011", component="ALGA") if "Valandų patikra neatlikta" in x.title]


def test_s18_zero_hourly_line_still_checks_extras():
    raw = _build()
    _drop_100(raw, "00011")
    raw["pay_lines"] += [line("00011", M, 101, 1, "0.00")]
    raw["variables"].append(var("00011", 101, 4, "10"))
    raw["timesheet_hours"].append(ts_row("00011", 102, 10))
    f, _ = a02_check(raw)
    assert [x for x in f if x.ref == "00011" and x.extra.get("component") == "PRIEDAI_LAIKAS"
            and x.severity in ("warning", "error")]
