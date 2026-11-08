# UNO Parts apskaitos demo: modulių sutartys

Tikslas: lokali sistema (Python 3.12, FastAPI) workshopui 2026-11-06. Be Rivilė API: įvestis failai, išvestis Rivilė ERP importo XLSX.
Principas: **skaičiuoja kodas, ne AI.** AI (Claude) tik per anonimizatorių, pasirinktinai.

Bendri failai (NEKEISTI, tik importuoti): `core/models.py`, `core/rates_2026.py`, `core/world.py`, `core/anonymizer.py`.
Pinigai: `Decimal`, `models.money()`. Neatitikimai grąžinami kaip `models.Finding`. Tekstai UI/Finding lietuviškai.
Paleidimas iš projekto šaknies: `py -3.12 -m pytest -q`. Generuoti failai -> `data/generated/`.

## A. Sąskaitos (worker A)
Failai: `core/invoices.py`, `core/rivile_export.py`, `data/gen_invoices.py`, `tests/test_invoices.py`
- `gen_invoices.main()` -> `data/generated/saskaitos/*.pdf` (60 pirkimo iš `world.PURCHASE_INVOICES`, reportlab, LT šriftas su diakritikais, pvz. DejaVuSans arba Windows `arial.ttf`). Tiekėjų maketai 2–3 skirtingi (realistiškumui). Indeksas `world.PDF_VAT_ERROR_IDX` PDF'e turi PVM +3.40 klaidą.
- `invoices.parse_invoice_pdf(path) -> ParsedInvoice` (pdfplumber; laukai: number, issue_date, due_date, supplier_name, supplier_company_code, supplier_vat_code, supplier_iban, lines, net, vat, gross, confidence 0..1, source_file).
- `invoices.process_folder(folder) -> tuple[list[ParsedInvoice], list[Finding]]`: tiekėjo atpažinimas pagal įmonės/PVM kodą -> `world.SUPPLIERS` rivile_code; patikros: PVM aritmetika (net*rate vs vat, gross=net+vat), dublikatai (tas pats tiekėjas+nr.), nežinomas tiekėjas, trūkstami laukai.
- `rivile_export.purchases_to_xlsx(parsed, path)` (atnaujinta 2026-11-04 pagal research/rivile_erp_import.md): lapai `Clients` (tiekėjų importas), `Antrastes` (dokumentų antraštės rankiniam/API sukūrimui), `Purchase_invoice_lines` (dokumentuoti 12 stulpelių + pagalbinis DocumentNumber), `Info`. Stulpeliai `COLUMNS` dict'e. PVM: 21 % PVM1, 9 % PVM2, 12 % PVM58, 0 % PVM5.
- `rivile_export.sales_to_xlsx(invoices, path)` analogiškai pardavimams + `invoices.render_sales_invoice_pdf(invoice, path)` (pardavimo sąskaitos iš šablono).

## B. Bankas (worker B)
Failai: `core/bank.py`, `data/gen_bank.py`, `tests/test_bank.py`
- `gen_bank.main()` -> `data/generated/bankas/israsas_2026-10.xml` (ISO 20022 camt.053.001.02, iš `world.bank_transactions(PURCHASE_INVOICES, sum(e.net_agreed))`).
- `bank.parse_camt053(path) -> list[BankTransaction]` (lxml, namespace-agnostiškai, tinka ir .001.08).
- `bank.reconcile(transactions, purchase_invoices, sales_invoices) -> ReconResult`: sulyginimas 1) sąskaitos nr. paskirtyje, 2) IBAN+suma, 3) suma+tiekėjo pavadinimas ±3 d. Rezultate: matched (tx, invoice, rule), partial, duplicates, unmatched_tx (mokėjimai be sąskaitos), unpaid_invoices (su days_overdue nuo `world.PERIOD_END`), findings: list[Finding]. DU/VMI/Sodra/banko mokesčiai klasifikuojami kaip "kita" (ne neatitikimas).
- `bank.build_pain001(invoices_to_pay, path, execution_date)` -> pain.001.001.03 XML juodraštis (debtor `world.COMPANY`). Banke tvirtina žmogus.

## C. DU + tabelis (worker C)
Failai: `core/payroll.py`, `core/timesheet.py`, `data/gen_timesheet.py`, `tests/test_payroll.py`, `tests/test_timesheet.py`
- `payroll.gross_to_net(gross, *, pension_accumulation=False) -> PayResult` (gross, npd, gpm, sodra_employee, net, sodra_employer, employer_cost). NPD = max(0, 747 - 0.49*(gross-1153)), kai gross<=1153 -> 747 (proporcingai neviso etato NEtaikom, demo). GPM = 20% * (gross - sodra? NE: LT GPM bazė = gross - NPD). Sodra darbuotojo 19.5% nuo gross.
- `payroll.net_to_gross(net) -> PayResult`: atvirkštinis, tikslus iki cento (binarinė paieška/iteracija + patikra gross_to_net(gross).net == net).
- `payroll.run_month(employees, timesheet_rows) -> list[PayslipRow]`: kiekvienam: neto pagal sutartį proporcingai dirbtam laikui, viršvalandžiai x1.5 (prie bruto), atostoginiai (demo: pagal vidutinį dienos uždarbį = bruto/norm dienų), ligos 1–2 d. darbdavys 62.06 % (demo: dienos VDU*0.6206), po to Sodra. Grąžina eilutes + suvestinę (bruto, GPM, Sodra, darbdavio, neto viso) deklaracijų patikrai (GPM313/SAM).
- `payroll.export_payroll_xlsx(rows, path)` -> lentelė importui į Rivilė DU (tab_no, bruto, NPD, GPM, Sodra, neto, darbdavio įmokos).
- `gen_timesheet.main()` -> `data/generated/tabelis/tabelis_2026-10.xlsx` (57 eil., stulpeliai 1..31 su kodais: skaičius val. / A atostogos / L liga / P poilsis / VD viršvalandžiai atskiru stulpeliu) + `rivile_valandos_2026-10.xlsx` (Rivilė eksportas: tab_no, dirbta val., atostogų d., ligos d.) + `sodra_nedarbingumai_2026-10.csv` + `atostogu_prasymai_2026-10.csv`. Įdėti `world.TIMESHEET_ANOMALIES`.
- `timesheet.load_timesheet(path)`, `timesheet.check(timesheet_path, rivile_path, sodra_csv, requests_csv) -> tuple[list[TimesheetRow], list[Finding]]`: viršvalandžiai (>12 val./7 d., >180 val./metus info), atostogos be prašymo, liga be pažymos, 0 val., tabelis != Rivilė, norma vs faktas. Kiekvienam Finding `suggested_action` = paruoštas klausimas atsakingam asmeniui.

## D. Kontrolė + AI tiltas + UI (Opus, pagrindinė sesija)
`core/control.py`, `core/ai_bridge.py`, `app/main.py`, `app/static/*`.
