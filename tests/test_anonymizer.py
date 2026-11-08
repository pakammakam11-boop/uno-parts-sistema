"""Sol S5-S8: asmens duomenys neturi patekti į AI tekstą."""
from core.anonymizer import Pseudonymizer, find_leaks


def anon(text: str, people=("Kristina Navickaitė",)) -> str:
    return Pseudonymizer(known_people=list(people)).anonymize(text)


def test_known_name_any_case_s5():
    out = anon("KRISTINA NAVICKAITĖ ir kristinos navickaitės")
    assert "KRISTINA" not in out.upper().replace("[DARB_001]", "")


def test_unknown_tab_number_s5():
    out = anon("Darbuotojas D999 dirbo 0 val.")
    assert "D999" not in out and not find_leaks(out)


def test_foreign_iban_s6():
    out = anon("Grąžinta į DE89370400440532013000 ir DE89 3704 0044 0532 0130 00")
    assert "DE89" not in out and not find_leaks(out)


def test_personal_code_with_bad_checksum_s7():
    out = anon("Asmens kodas 38901011239")
    assert "38901011239" not in out and not find_leaks(out)


def test_short_company_code_s8():
    assert "1234567" not in anon("Tiekėjo kodas 1234567")


def test_invoice_numbers_stay_readable():
    out = anon("Sąskaita 001-202610013 ir PVM kodas LT100000000017")
    assert "001-202610013" in out and "LT100000000017" not in out


def test_iban_lowercase_and_double_spaces_s6():
    out = anon("de89370400440532013000 ir DE89  3704  0044 0532 0130 00")
    assert "de89" not in out.lower() and not find_leaks(out)


def test_remittance_text_never_reaches_ai_s5():
    from core.ai_bridge import build_package
    from core.models import Finding

    f = Finding("bank", "warning", "Trūksta sąskaitos", "Sumokėta („Arvydo Rimkaus avansas“)")
    pkg = build_package([f], extra_people=["Arvydas Rimkus"])
    assert "Rimk" not in pkg["text"] and pkg["safe_to_send"]


def test_remittance_with_inner_quotes_removed_s5():
    from core.ai_bridge import build_package
    from core.models import Finding

    remit = "Už „paslaugas“ Arvydo Rimkaus"
    f = Finding("bank", "warning", "Trūksta sąskaitos", f"Sumokėta („{remit}“)")
    pkg = build_package([f], free_texts=[remit])
    assert "Rimk" not in pkg["text"]


def test_zero_vat_invoice_not_missing_m1():
    from datetime import date
    from decimal import Decimal as D
    from core.invoices import ParsedInvoice
    from core.models import InvoiceLine

    p = ParsedInvoice("SF-1", date(2026, 10, 1), date(2026, 10, 15), "UAB X", "302000101", "LT200001011",
                      "LT11 7044 0600 0000 1001", [InvoiceLine("Paslauga", D(1), D(100), D(0))], D(100), D(0), D(100))
    assert p.missing_fields == []


def test_short_remittance_does_not_split_names_k1():
    from core.ai_bridge import build_package
    from core.models import Finding

    findings = [Finding("bank", "warning", "Trūksta sąskaitos", "Sumokėta („Arvydas“)"),
                Finding("timesheet", "warning", "Atostogos be prašymo", "Arvydas Rimkus 5 d. atostogų")]
    pkg = build_package(findings, extra_people=["Arvydas Rimkus"], free_texts=["Arvydas"])
    assert "Rimk" not in pkg["text"] and "Arvyd" not in pkg["text"]


def test_supplier_names_kept_remittance_removed():
    from core.ai_bridge import build_package
    from core.models import Finding

    f = Finding("bank", "warning", "Trūksta sąskaitos",
                "Sumokėta tiekėjui UAB „Baltijos detalės“ („Jono Petraičio avansas“), paskirtis: „Jonas P.“")
    text = build_package([f])["text"]
    assert "UAB „Baltijos detalės“" in text and "Petrai" not in text and "Jonas P." not in text


# ---- S5 (saugumo auditas): struktūriniai vardai ir saugumo vėliavėlė ----

def test_unknown_counterparty_person_pseudonymized_and_safe():
    from core.ai_bridge import build_package
    from core.models import Finding

    f = Finding("bank", "warning", "Mokėjimas nežinomam gavėjui",
                "2026-10-03: -120,00 EUR gavėjui Vladislav Petrenko, gavėjo nėra tiekėjų kartotekoje.")
    unsafe = build_package([f])                       # struktūrinis vardas nepaduotas: laisvo teksto patikra pagauna
    assert not unsafe["safe_to_send"] and any("Petrenko" in x for x in unsafe["leaks"])
    pkg = build_package([f], structured_names=["Vladislav Petrenko", "UAB Baltijos detalės"])
    assert "Petrenko" not in pkg["text"] and "Vladislav" not in pkg["text"]
    assert pkg["safe_to_send"] and pkg["leaks"] == []


def test_company_names_not_pseudonymized():
    from core.anonymizer import person_names
    assert person_names(["UAB Baltijos detalės", "Vladislav Petrenko", "Bankas", "MB Vardenis"]) == ["Vladislav Petrenko"]


def test_find_leaks_flags_capitalized_pairs_conservatively():
    assert find_leaks("gavėjui Vladislav Petrenko sumokėta")
    assert not find_leaks("Siūlomas veiksmas: paprašyti sąskaitos. Sąskaita 001-202610013")


def test_ai_package_endpoint_pseudonymizes_bank_party():
    from fastapi.testclient import TestClient
    from app import main
    from core.models import Finding

    main.RESULTS.clear()
    main.RESULTS["bank"] = {"table": {"rows": [{"party": "Vladislav Petrenko", "text": ""}]},
                            "_findings": [Finding("bank", "warning", "Mokėjimas nežinomam gavėjui",
                                                  "gavėjui Vladislav Petrenko, gavėjo nėra kartotekoje.")],
                            "counts": {}}
    try:
        j = TestClient(main.app).post("/api/ai/package").json()
    finally:
        main.RESULTS.clear()
    assert "Petrenko" not in j["text"] and j["safe_to_send"]


def test_structured_name_with_suffix_is_pseudonymized_and_checked():
    """Sol S5 r2: „VLADISLAV PETRENKO (IV)“ – \b po ')' nesutapdavo; vardas likdavo, o safe_to_send buvo true."""
    from core import ai_bridge
    from core.models import Finding
    f = Finding("bank", "warning", "Neatpažinta operacija", "Kontrahentas VLADISLAV PETRENKO (IV) gavo 120,00 €")
    pkg = ai_bridge.build_package([f], structured_names=["VLADISLAV PETRENKO (IV)"])
    assert "PETRENKO" not in pkg["text"].upper()
    assert pkg["safe_to_send"] is True


def test_structured_name_left_in_text_blocks_sending():
    from core import ai_bridge
    assert ai_bridge._name_left("Vladislav Petrenko (IV)", "... vladislav  PETRENKO ...") is True
    assert ai_bridge._name_left("Darbo užmokestis (masinis mokėjimas)", "darbo užmokesčio skaičiavimas") is False
    assert ai_bridge._name_left("Vladislav Petrenko (IV)", "... [DARB_001] ...") is False
