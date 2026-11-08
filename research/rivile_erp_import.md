# Rivile ERP purchase and bank imports

**Rivile ERP documents native CAMT bank-statement import, but the verified purchase XLSX workflow imports detail lines into an already-created document—not a verified header-plus-`Lines` workbook.** The public purchase schema does not document supplier, invoice-number, date, VAT-classifier or explicit VAT-amount columns, so those fields should not be invented when building an importer. ([Purchase import schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines), [banking reference, revision 2026-09-07](https://guide.rivile.cloud/description_module_windows/payment/payments))

**Research date:** 4 November 2026.  
**Verification standard:** “Documented” below means supported by primary documentation; it does **not** mean tested in an authenticated Rivile ERP tenant. Undated pages are identified as such rather than assigned an inferred publication date.

## Purchase XLSX import

### What the workflow imports

The official worked example instructs the user to **create and save the purchase document first**, entering its counterparty and date and choosing whether prices include VAT; the XLSX then supplies its detail lines. It also states that purchase/sales orders, invoices and returns each have their own template. ([Official worked example, undated](https://guide.rivile.cloud/modules/purchase/duk/task_000012))

Consequently, **a bulk purchase-document workbook containing a header sheet and a sheet literally named `Lines` remains unverified**. The documented detail-line workflow is not evidence that such a full-document importer exists—or that it does not exist elsewhere in the product. ([Documented import scope](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines))

### Template download path

For purchase invoices, use:

**Pirkimai → Pirkimų sąskaitos → Sukurti naują → Sąskaita → enter and save the document header → Importuoti → Atsisiųsti XLSX šabloną.** The invoice-creation guide establishes the first steps; the import guide establishes the document-level import and template download.  ([Import instructions](https://guide.rivile.cloud/modules/purchase/duk/task_000012))[1]

The schema page literally prints an additional repeated menu level—**Pirkimai → Pirkimai → Pirkimų sąskaitos**—while the practical invoice guide uses the shorter path above; the download control also appears as **Atsiųsti XLSX šabloną** in the worked example. These are documentation inconsistencies, not evidence of separate import mechanisms. ([Schema page, undated](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines), [worked example, undated](https://guide.rivile.cloud/modules/purchase/duk/task_000012))

### Worksheet names

| Document | Name printed in official documentation |
|---|---|
| Purchase order lines | `Purchase_order_lines` ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines)) |
| Purchase invoice/return lines | **`Purchase_invoice_lines.`**, with a trailing period and malformed closing quotation in the published page ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines)) |
| Purchase-document header sheet | **Not verified** in the published purchase-line schema ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines)) |
| Sheet literally named `Lines` | **Not verified** in the published purchase-line schema ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines)) |

**Exact-name caveat:** the source’s trailing period may be a publishing error, but that cannot be resolved from the page alone. **Preserve the worksheet name in the XLSX downloaded from the target tenant**; do not silently assume either `Purchase_invoice_lines` or `Purchase_invoice_lines.` is the runtime name. ([Published spelling](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines))

### Exact documented columns

These are the **12 column names**, in the order published by Rivile; capitalization is preserved. ([Official schema, undated](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines))

| Column | Type | Documented requirement and meaning |
|---|---|---|
| `DepartmentCode` | Text | Required; department code ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines)) |
| `DiscountPerc` | Number | Optional; discount percentage ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines)) |
| `ItemCode` | Text | Required; item code ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines)) |
| `ItemName` | Text | Optional; populated from the item code when omitted ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines)) |
| `LotCode` | Text | Optional; lot code ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines)) |
| `MarkCode` | Text | Optional; mark/tag code ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines)) |
| `Notes` | Text | Optional; notes ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines)) |
| `ObjectCode` | Text | Optional; object code ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines)) |
| `Price` | Number | Optional; price ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines)) |
| `Quantity` | Number | Required; quantity ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines)) |
| `Serial` | Text | Optional; series ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines)) |
| `UnitOfMeasureCode` | Text | Required; unit-of-measure code ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines)) |

**Do not add guessed fields** such as `ClientCode`, `SupplierCode`, `DocumentNumber`, `DocumentDate`, `TaxCode`, `VatAmount`, `NetAmount` or `GrossAmount`: none appears in this published purchase-line schema. Supplier, document number, document date and operation date are instead entered in the document form in the official invoice workflow. ([Schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines))[1]

The official procedure separately documents correcting an invoice’s VAT amount by disabling **Automatinis PVM perskaičiavimas** and editing **PVM suma** on a detail line; this does **not** establish an XLSX column for importing the supplier’s exact VAT amount.[2]

## Native bank-statement imports

### Verified XML formats

The banking reference, explicitly marked **“redakcija 2026-09-07”**, lists these supported **statement-import** formats. ([Dated primary source](https://guide.rivile.cloud/description_module_windows/payment/payments))

| Format | Verification |
|---|---|
| `camt.052.001.02` | Explicitly listed for bank-statement XML import ([reference](https://guide.rivile.cloud/description_module_windows/payment/payments)) |
| `camt.053.001.02` | Explicitly listed for bank-statement XML import ([reference](https://guide.rivile.cloud/description_module_windows/payment/payments)) |
| `camt.053.001.12` | Explicitly listed for bank-statement XML import ([reference](https://guide.rivile.cloud/description_module_windows/payment/payments)) |
| Other CAMT versions, including `camt.054` | Not verified in that supported-format list ([reference](https://guide.rivile.cloud/description_module_windows/payment/payments)) |
| MT940, CSV, XLS/XLSX statement uploads | Native support not verified from the reviewed ERP documentation; absence from this list is not proof of non-support ([reference](https://guide.rivile.cloud/description_module_windows/payment/payments)) |

The manual upload path is **Apskaita → Skolos ir mokėjimai → Mokėjimai → Sukurti naują → Importuoti iš banko (SEPA - XML)**; the dialog lets the user select incoming/outgoing payments and document/fee categories. ([Banking reference, revision 2026-09-07](https://guide.rivile.cloud/description_module_windows/payment/payments))

**Do not confuse directions:** `pain.001.001.03` and `pain.001.001.09` are listed for **payment export**, not statement import. ([Same dated reference](https://guide.rivile.cloud/description_module_windows/payment/payments))

### Banks versus file compatibility

Rivile’s **August 2025 release notes** list **SEB, Swedbank, Revolut, URBO, Luminor, Citadele, Artea and N26 Bank** for the direct **Ryšys su banku** integration, provided through **UAB Fintegry**. This is a dated direct-connection list—not a bank-by-bank certification matrix for uploaded CAMT files, nor independently confirmed November 2026 coverage. ([August 2025 release notes](https://guide.rivile.cloud/news/202508))

| Import route | Supported scope established |
|---|---|
| Manual XML upload | The three exact CAMT versions above; no complete per-bank/per-version compatibility matrix was verified ([banking reference](https://guide.rivile.cloud/description_module_windows/payment/payments)) |
| Direct bank connection | The eight banks named in the August 2025 release notes ([dated announcement](https://guide.rivile.cloud/news/202508)) |
| Paysera or other unlisted institutions | Not established by that direct-integration announcement; actual CAMT-file acceptance requires separate verification ([dated announcement](https://guide.rivile.cloud/news/202508)) |

The direct route requires the **Rivile ERP Bankų integracijos** module and account consents, and is initiated through **Mokėjimai → Ryšys su banku → Importuoti išrašus**. The setup guide separately describes linking bank accounts and configuring their payment methods and cash-ledger accounts. ([Banking reference](https://guide.rivile.cloud/description_module_windows/payment/payments), [direct-connection setup, undated](https://guide.rivile.cloud/modules/payment/bank_import/automation_bank_import))

### Counterparty recognition

Under **Nustatymai → Bendrieji → Parametrai**, **SEPA imp. kliento parinkimas** configures recognition by company registration code, bank-account number, previous payments or counterparty name; **SEPA imp. sąskaitų parinkimo konfigūracija** configures bank/operation/account recognition. Unrecognized records can arrive as **Juodraštis** for manual resolution. ([Banking reference, revision 2026-09-07](https://guide.rivile.cloud/description_module_windows/payment/payments))

The documented new-counterparty workflow is assisted rather than proven unattended creation: open the unmatched payment, select **Sukurti klientą pagal SEPA**, complete the card, then **Saugoti ir grįžti**. ([Official example, undated](https://guide.rivile.cloud/modules/payment/bank_import/sepa_import/task_003))

## Supplier and client codes

### Identifier distinctions

Rivile uses a shared **Klientai** master for suppliers and customers; a card may be marked **Tiekėjas**, **Pirkėjas**, or both, and those flags govern its availability in purchase/sales operations.[3]

| Field | Meaning and documented behavior |
|---|---|
| `Code` | Internal Rivile client code, type Text; if omitted during client import, generated from the counter ([client schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/clients)) |
| `RegCode` | Company registration code; separate from `Code`, documented as optional ([client schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/clients)) |
| `VatNumber` | Tax-identifier value interpreted with `TaxCodeTypeId`; not the internal client code ([client schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/clients)) |
| `ReferenceCode` | External-system linking code; not documented as automatically replacing the internal code ([client schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/clients)) |
| `##refId##` | Workbook-local identifier connecting related sheets; not the supplier’s registration number or ERP code ([client schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/clients)) |
| `IsSupplier` | Supplier flag; defaults to false if omitted, so explicitly populate it for purchase suppliers ([client schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/clients)) |

**Unverified constraints:** maximum `Code` length, permitted characters, case sensitivity, uniqueness scope and duplicate/upsert behavior were not established from the reviewed client-import schema. They should be confirmed before choosing an externally generated coding convention. ([Published schema and its limits](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/clients))

### Importing new counterparties

Download the client workbook at **Nustatymai → Pardavimai ir pirkimai → Klientai → Sukurti naują → Importuoti → Atsisiųsti XLSX šabloną**. ([Official client-import instructions, undated](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/clients))

Its four documented worksheets are:

| Worksheet | Relevant requirements |
|---|---|
| `Clients` | `##refId##` and `Name` required; `Code` may be generated when omitted ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/clients)) |
| `ClientAddresses` | For populated addresses, the schema requires `##refId##`, `TypeId` and `Name` ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/clients)) |
| `ClientBankAccounts` | For populated bank details, `##refId##`, `Name` and `AccountNumber` required; `BankCode` optional ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/clients)) |
| `ClientGroups` | Uses `##refId##` and `GroupCode`; the guide’s requirement wording for `GroupCode` is ambiguous ([schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/clients)) |

Use the **same `##refId##` across sheets for the same counterparty**. `AutoFill=1` can request registry-based population using `RegCode`, including name, VAT code and address; the guide says the workbook data is used if registry information is not found. ([Client-import schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/clients))

**Important schema caveat:** `SupplierPaymentTermCode` is marked required; `SupplierTaxCategoryCode`, `AccountingCategoryCode` and several other fields are marked **Privalomas***, with defaults described for some but not all of them. Therefore, `Name` plus `IsSupplier` should not be represented as a proven minimal import payload. ([Client-import schema](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/clients))

## Purchase VAT classifiers

### Three distinct configuration layers

Do not conflate **`SupplierTaxCategoryCode`**, an item’s **mokesčių klasė**, and a classifier such as **`PVM1`**: the supplier category and item class participate in selecting the applicable classifier. Rivile’s reverse-charge example explicitly configures a tax class, adds its `PVM25` mapping to a tax category, and assigns the class to the item. ([Supplier-category field](https://guide.rivile.cloud/user_interfaces/data_processing/import_export/clients))[4]

Classifier cards are maintained under **Nustatymai → Apskaita → Mokesčių klasifikatoriai**, with rate and VAT-ledger settings; reverse-charge use also requires the relevant configuration, not merely a matching classifier name.[5][4]

### Relevant purchase codes

The following meanings are verified against VMI’s **“PVM klasifikatoriaus kodų naudojimas,” dated 2025-12-16**. Its URL retains an older `2023-09-19` filename, but the retrieved document’s title/content identifies the newer revision; tenant-specific activation and configuration remain separate questions. ([VMI primary document](https://www.vmi.lt/evmi/documents/20142/391113/PVM+klasifikatorius+2023-09-19.pdf/18dadf9b-5b2a-9c17-f262-cc1d2771ff7c?t))

| Code | Purchase treatment | Rate / limitation |
|---|---|---|
| `PVM1` | Domestic goods/services subject to standard VAT | 21% ([VMI](https://www.vmi.lt/evmi/documents/20142/391113/PVM+klasifikatorius+2023-09-19.pdf/18dadf9b-5b2a-9c17-f262-cc1d2771ff7c?t)) |
| `PVM2` | Domestic reduced-rate goods/services under the historical rules | 9%; the classifier document marks the applicable period through **2025-12-31** ([VMI](https://www.vmi.lt/evmi/documents/20142/391113/PVM+klasifikatorius+2023-09-19.pdf/18dadf9b-5b2a-9c17-f262-cc1d2771ff7c?t)) |
| `PVM3` | Qualifying domestic reduced-rate goods/services | 5%; includes qualifying books from 2026-01-01 ([VMI](https://www.vmi.lt/evmi/documents/20142/391113/PVM+klasifikatorius+2023-09-19.pdf/18dadf9b-5b2a-9c17-f262-cc1d2771ff7c?t)) |
| `PVM58` | Qualifying domestic accommodation, regular passenger transport and cultural services | 12%, from **2026-01-01** ([VMI](https://www.vmi.lt/evmi/documents/20142/391113/PVM+klasifikatorius+2023-09-19.pdf/18dadf9b-5b2a-9c17-f262-cc1d2771ff7c?t)) |
| `PVM5` | VAT-exempt supplies under the specified VAT Act provisions | Exempt—not a generic zero-rate code ([VMI](https://www.vmi.lt/evmi/documents/20142/391113/PVM+klasifikatorius+2023-09-19.pdf/18dadf9b-5b2a-9c17-f262-cc1d2771ff7c?t)) |
| `PVM16` | Intra-EU goods acquisitions deemed to occur in Lithuania | 21% ([VMI](https://www.vmi.lt/evmi/documents/20142/391113/PVM+klasifikatorius+2023-09-19.pdf/18dadf9b-5b2a-9c17-f262-cc1d2771ff7c?t)) |
| `PVM21` | Services purchased from EU VAT payers, with buyer-accounted VAT under Article 95(2) | 21%; **not** the generic code for 21% VAT ([VMI](https://www.vmi.lt/evmi/documents/20142/391113/PVM+klasifikatorius+2023-09-19.pdf/18dadf9b-5b2a-9c17-f262-cc1d2771ff7c?t)) |
| `PVM20` | Foreign services, excluding purchases from EU VAT payers, with buyer-accounted VAT under Article 95(2) | 21% ([VMI](https://www.vmi.lt/evmi/documents/20142/391113/PVM+klasifikatorius+2023-09-19.pdf/18dadf9b-5b2a-9c17-f262-cc1d2771ff7c?t)) |
| `PVM25` | Domestic reverse charge under Article 96 | 21%; also explicitly demonstrated in Rivile’s purchase guide [4] |
| `PVM48` | Goods/services acquired outside Lithuania where the acquisition is not a Lithuanian VAT object | No Lithuanian output VAT; includes relevant foreign-VAT cases ([VMI](https://www.vmi.lt/evmi/documents/20142/391113/PVM+klasifikatorius+2023-09-19.pdf/18dadf9b-5b2a-9c17-f262-cc1d2771ff7c?t)) |
| `PVM100` | Other specified cases, including invoices received from Lithuanian SVS suppliers | Residual classification, not a substitute for determining the transaction correctly ([VMI](https://www.vmi.lt/evmi/documents/20142/391113/PVM+klasifikatorius+2023-09-19.pdf/18dadf9b-5b2a-9c17-f262-cc1d2771ff7c?t)) |

Rivile’s **February 2026 release notes** explicitly confirm addition of **`PVM58`, `PVM59` and `PVM60`**; VMI distinguishes `PVM59` as an outgoing/private-consumption case and `PVM60` as a qualifying incoming reverse-charge case, so the three are not interchangeable purchase codes.  ([VMI definitions](https://www.vmi.lt/evmi/documents/20142/391113/PVM+klasifikatorius+2023-09-19.pdf/18dadf9b-5b2a-9c17-f262-cc1d2771ff7c?t))[6]

## Implementation verification gates

Before committing an automated importer, the following are recommended acceptance gates:

- **Obtain the actual tenant-generated purchase XLSX:** inspect worksheet names and header cells, resolving the published trailing-period ambiguity.
- **Require a demonstrated full-document route:** if unattended creation must include supplier, invoice number, dates, classifier and exact VAT totals, obtain a working workbook or a documented integration contract rather than extending the line-only schema.
- **Test counterparty imports separately:** confirm generated-code retrieval, duplicate handling, required supplier settings and company scope.
- **Test each real bank export:** record its exact CAMT namespace/version and check transaction counts, amounts, fees, unmatched counterparties and repeat-import behavior.
- **Validate VAT selection by transaction type and date:** do not map solely from the printed percentage.
- **Retain accounting approval:** compare imported amounts and VAT against the original invoice before posting.

**Source limitation:** the supplied public GitHub repository URL and the guide-linked raw source path returned 404 during this review, so no commit history or Git-based publication dates could be verified; the findings above rely on the published guide, its explicit revision/release dates and VMI’s dated classifier document. ([Supplied repository](https://github.com/rivile-org/guide-rivile-cloud))

Citations:
[1]: https://guide.test.rivile.cloud/user_interfaces/data_processing/import_export
[2]: https://github.com/uptick/pybusinesscentral/blob/master/docs/PurchaseInvoiceLineApi.md
[3]: https://kvimo.lt/apie/pvm-kodu-klasifikatorius/
[4]: https://scanas.lt/pvm-kodai
[5]: https://www.norian.lt/finansine-apskaita-ir-atskaitomybe-34/
[6]: https://www.rivile.lt/en/produktai/rivile-erp/moduliai/pardavimo-saskaitos-ir-uzsakymai
[7]: https://www.rivile.lt/produktai/rivile-erp/moduliai/pirkimo-saskaitos-ir-uzsakymai
[8]: https://www.rivile.lt/produktai/rivile-erp/moduliai/mokejimai-bankai
[9]: https://gidas.rivile.lt/valdymo_principai/importas_eksportas
[10]: https://www.rivile.lt/apps/pardavimu-importas-is-prestashop
[11]: https://www.vmi.lt/evmi/pvm-klasifikatoriaus-kodu-naudojimo-pavyzdziai
[12]: https://www.rivile.lt/apps/susisiekti?id=68514cd2e4521972654f0687
[13]: https://www.rivile.lt/en/produktai/rivile-erp/moduliai/prekes-ir-atsargu-valdymas
[14]: https://www.rivile.lt/
[15]: https://kpmg.com/lt/lt/izvalgos/mokesciu-ir-teises-naujienlaiskiai/2025/2025-gruodis.html
[16]: https://www.rivile.lt/produktai/rivile-erp
[17]: https://www.rivile.lt/en/produktai/rivile-erp-pos
[18]: https://www.vmi.lt/evmi/documents/20142/391107/Da%C5%BEniausiai+pasitaikan%C4%8Di%C5%B3+sandori%C5%B3+deklaravimas+FR0600+++i.SAF+kodas.pdf/1690b4fc-32c6-29f7-e0f1-ab98e2239035?t
[19]: https://gidas.rivile.lt/korteles/klientai
[20]: https://gidas.rivile.lt/aktualijos
[21]: https://www.vmi.lt/evmi/documents/20142/391113/PVM+klasifikatorius+2023-09-19.pdf/18dadf9b-5b2a-9c17-f262-cc1d2771ff7c?t
[22]: https://gidas.rivile.lt/pardavimai/duk/duk_019
[23]: https://www.rivile.lt/en/produktai/erp-darbuotoju-portalas
[24]: https://www.vmi.lt/evmi/documents/20142/391113/PVM+klasifikatoriaus+kodai.pdf/d9ced2b5-6682-a27d-865f-25fc72134dd9?t=1545220074315
[25]: https://gidas.rivile.lt/pirkimai/prekiu_pirkimas
[26]: https://guide.rivile.cloud/modules/purchase/duk/task_00004
[27]: https://www.rivile.lt/en/apps/crm-saskaitu-saraso-siuntimas-klientams
[28]: https://fsaskaita.lt/imone/uzdaroji-akcine-bendrove-rivile-121411842
[29]: https://www.rivile.lt/aktualijos?page=2
[30]: https://www.rivile.lt/en/apps/pardavimu-importas-is-pigu-lt
[31]: https://www.rivile.lt/en/produktai/rivile-erp
[32]: https://www.vmi.lt/evmi/documents/20142/737112/RTD-78.pdf/6007695a-b75d-109f-ad0c-936736cc256a?t=1786454040302
[33]: https://www.rivile.lt/aktualijos/rivile-ivertino-rysku-lyderio-pranasuma
[34]: https://www.rivile.lt/kainos/gama/pirkimas
[35]: https://guide.rivile.cloud/normatives_manuals/client
[36]: https://www.rivile.lt/en/apps/vecticum-procesu-valdymo-sistema
[37]: https://guide.rivile.cloud/academy/purchases/task004
[38]: https://gidas.rivile.lt/integraciniai_sprendimai/el_parduotuves/prestashop/integracijos_diegimas
[39]: https://www.rivile.lt/produktai/rivile-epi
[40]: https://www.vmi.lt/evmi/documents/20142/391113/PVM+klasifikatorius+2025-11-27.pdf/18dadf9b-5b2a-9c17-f262-cc1d2771ff7c?t=1764249055323
[41]: https://www.vmi.lt/evmi/documents/20142/391113/PVM+klasifikatorius+2025-12-16.pdf/18dadf9b-5b2a-9c17-f262-cc1d2771ff7c?t=1765865860904
[42]: https://www.rivile.lt/diegia-ir-aptarnauja
[43]: https://gidas.rivile.lt/rivile_edi/edi_veikimas/web_importas
[44]: https://www.vmi.lt/evmi/documents/20142/391113/PVM+klasifikatorius+2023-09-19.pdf/18dadf9b-5b2a-9c17-f262-cc1d2771ff7c?t=1695116658973
[45]: https://guide.rivile.cloud/modules/purchase/invoices/purchase_invoices
[46]: https://gidas.rivile.lt/parametrai/kiti_parametrai
[47]: https://www.rivile.lt/en/produktai/rivile-erp/moduliai/ataskaitos-ir-el-laisku-sablonai
[48]: https://www.rivile.lt/en/produktai/rivile-erp/moduliai/gamybos-valdymas?m=true
[49]: https://gidas.rivile.lt/rivile_akademija/rivile_gama/ii_pamoka/prekiu_pirkimas
[50]: https://www.rivile.lt/produktai/rivile-erp/moduliai/pardavimo-kaikynai-akcijos
[51]: https://guide.rivile.cloud/modules/purchase
[52]: https://www.rivile.lt/apps/saver
[53]: https://gidas.rivile.lt/duk
[54]: https://www.rivile.lt/en/produktai/rivile-erp/moduliai/klientai-ir-skolu-valdymas
[55]: https://guide.rivile.cloud/academy/automation_training/tema-02-erp-objektas-json
[56]: https://gidas.rivile.lt/sistemos_programavimas/funkcijos/vartotoju_funkcijos
[57]: https://www.rivile.lt/en/produktai/rivile-erp/moduliai/ilgalaikio-turto-valdymas
[58]: https://guide.rivile.cloud/description_module_windows/payables_receivables
[59]: https://guide.rivile.cloud/user_interfaces/reports/task_0001
[60]: https://learn.microsoft.com/en-us/dynamics365/business-central/application/base-application/table/microsoft.purchases.history.purch.-inv.-line
[61]: https://guide.rivile.cloud/description_module_windows/purchase/purchase_orders_invoices
[62]: https://learn.microsoft.com/en-us/dynamics365/business-central/purchases-powerbi-app-semantic-model
[63]: http://www.learn.microsoft.com/en-us/dynamics365/business-central/dev-itpro/api-reference/v2.0/resources/dynamics_purchaseinvoiceline
[64]: https://guide.rivile.cloud/modules/purchase/duk/task_000012
[65]: https://guide.rivile.cloud/modules/purchase/invoices/automatic_vat_recalculation
[66]: https://www.vmi.lt/evmi/%C4%AEsiregistravusio-pvm-mok%C4%97toju-asmens1
[67]: https://www.vmi.lt/evmi/pvm-moketojais-registruotu-asmenu-pirkimo-importo-pvm-atskaita
[68]: https://www.vmi.lt/evmi/pvm-moketojai
[69]: https://learn.microsoft.com/en-sg/dynamics365/business-central/dev-itpro/api-reference/v2.0/resources/dynamics_purchaseinvoice
[70]: https://online-help.sagex3.com/erp/12/en-us/Content/FCT/CONSCPJ.htm
[71]: https://guide.rivile.cloud/erp_first_steps/data_entry_migration/duk/task_0001
[72]: https://www.vmi.lt/pardavimai/
[73]: https://guide.rivile.cloud/user_interfaces/data_processing/import_export
[74]: https://api.merit.ee/connecting-robots/reference-manual/purchase-invoices/get-purchase-invoice-details/
[75]: https://guide.rivile.cloud/user_interfaces/data_processing/import_export/products
[76]: https://www.vmi.lt/evmi/documents/20142/725548/SAF-T+technin%C4%97s+specifikacijos+klasifikatoriai.pdf/9db80a99-2254-a9eb-d988-1ec724f1f20f?t=1606462603745
[77]: https://www.vmi.lt/evmi/documents/20142/391113/PVM+klasifikatorius+2022-01-21.pdf/18dadf9b-5b2a-9c17-f262-cc1d2771ff7c?t=1642768914434
[78]: https://www.vmi.lt/evmi/pridetines-vertes-mokescio-pakeitimai-nuo-2025-m
[79]: https://guide.rivile.cloud/news/202508
[80]: https://www.vmi.lt/evmi/mano-vmi
[81]: https://guide.rivile.cloud/modules/payment/payments
[82]: https://guide.rivile.cloud/user_interfaces/data_processing/import_export/fa_operations
[83]: https://guide.rivile.cloud/integrative_solutions/analytics
[84]: https://guide.rivile.cloud/integrative_solutions/ocr
[85]: https://pharosproduction.com/insights/engineering/payment-reconciliation-engine/
[86]: https://guide.rivile.cloud/integrative_solutions/woocomerce/woocomerce
[87]: https://guide.rivile.cloud/integrative_solutions/verskislt/task01
[88]: https://guide.rivile.cloud/integrative_solutions/api/api_postman
[89]: https://guide.rivile.cloud/normatives_manuals/customs_codes
[90]: https://guide.rivile.cloud/erp_first_steps/users_roles
[91]: https://guide.rivile.cloud/academy/automation_training/docs/rivile-erp-automatizavimo-api
[92]: https://guide.rivile.cloud/academy
[93]: https://deklaravimas.vmi.lt/lt/Naujienos/NaujienosVISI.aspx
[94]: https://guide.rivile.cloud/normatives_manuals/products_services
[95]: https://guide.rivile.cloud/automation_training/praktika/pvz-finance-dokumentai
[96]: https://guide.rivile.cloud/system_individual_adaptation/consumer_rights
[97]: https://guide.rivile.cloud/modules/manufacture_operations/need_to_know
[98]: https://guide.rivile.cloud/description_module_windows/pos
[99]: https://deklaravimas.vmi.lt/lt/Korteles/PazymosKortele/Pazymos_kortele.aspx?ref=48183986
[100]: https://guide.rivile.cloud/api
[101]: https://guide.rivile.cloud/news/gido_naujienos
[102]: https://guide.rivile.cloud/
[103]: https://guide.rivile.cloud/erp_first_steps
[104]: https://guide.rivile.cloud/user_interfaces/subscription/subscription
[105]: https://guide.rivile.cloud/modules
[106]: https://guide.rivile.cloud/erp_first_steps/opening_balances/debt_records_import
[107]: https://guide.rivile.cloud/description_module_windows/payrolls/payrolls_first_steps
[108]: https://guide.rivile.cloud/description_module_windows/purchase/purchase_orders_apply_costs
[109]: https://guide.rivile.cloud/description_module_windows/normatives_manuals/products_services
[110]: https://guide.rivile.cloud/description_module_windows/manufacture/manufacture_operations
[111]: https://guide.rivile.cloud/description_module_windows/payrolls
[112]: https://guide.rivile.cloud/description_module_windows/payrolls/vocation_tariffs
[113]: https://www.vmi.lt/evmi/i.saf
[114]: https://www.vmi.lt/pardavimai/en/users/login_choose/?r=https://www.vmi.lt/pardavimai/
[115]: https://guide.rivile.cloud/modules/payment
[116]: https://guide.rivile.cloud/description_module_windows/payables_receivables/pr_registry
[117]: https://guide.rivile.cloud/description_module_windows/normatives_manuals/taxes
[118]: https://www.lnb.lt/istekliai/duomenu-bazes/lietuvos
[119]: https://guide.rivile.cloud/modules/payment/bank_import
[120]: https://guide.rivile.cloud/modules/payment/duk/task_00009
[121]: https://guide.rivile.cloud/modules/payment/bank_import/automation_bank_import
[122]: https://guide.rivile.cloud/user_interfaces/data_processing/import_export/detailed_lines
[123]: https://guide.rivile.cloud/erp_first_steps/data_entry_migration/custumer_import
[124]: https://guide.rivile.cloud/description_module_windows/payment/payments
[125]: https://guide.rivile.cloud/modules/payment/bank_import/sepa_import
[126]: https://guide.rivile.cloud/modules/payment/bank_import/sepa_import/task_001
[127]: https://guide.rivile.cloud/normatives_manuals/banks
[128]: https://guide.rivile.cloud/erp_first_steps/data_entry_migration/import_of_sales_prices
[129]: https://guide.rivile.cloud/erp_first_steps/data_entry_migration/import_of_goods_and_services
[130]: https://guide.rivile.cloud/description_module_windows/normatives_manuals/client
[131]: https://guide.rivile.cloud/api/api_usage
[132]: https://guide.rivile.cloud/erp_first_steps/opening_balances/import_of_fixed_asset_transactions
[133]: https://guide.rivile.cloud/user_interfaces/reports
[134]: https://guide.rivile.cloud/modules/payrolls/payrolls_norms/worl_calendar
[135]: https://guide.rivile.cloud/automation_training/tema-02-erp-objektas-json
[136]: https://guide.rivile.cloud/api/api_usage/api_python
[137]: https://guide.rivile.cloud/user_interfaces/data_processing/import_export/pr_documents
[138]: https://guide.rivile.cloud/erp_first_steps/basic_settings/tax_categories/task_0001
[139]: https://guide.rivile.cloud/normatives_manuals/taxes
[140]: https://guide.rivile.cloud/erp_first_steps/basic_settings/tax_categories/task_0002
[141]: https://guide.rivile.cloud/news/202604
[142]: https://guide.rivile.cloud/modules/pos/duk/task_00020
[143]: https://guide.rivile.cloud/description_module_windows/imas/isaf
[144]: https://guide.rivile.cloud/academy/accounting_settings/task_002
[145]: https://guide.rivile.cloud/automation_training/tema-04-aplinka-ir-erp-ui
[146]: https://guide.rivile.cloud/user_interfaces/data_processing/import_export/clients
[147]: https://guide.rivile.cloud/description_module_windows/purchase
[148]: https://guide.rivile.cloud/erp_first_steps/data_entry_migration/fixed_assest_cards_import
[149]: https://guide.rivile.cloud/modules/income/income_rates
[150]: https://guide.rivile.cloud/modules/purchase/document_digitization
[151]: https://guide.rivile.cloud/modules/purchase/document_digitization/task_00001
[152]: https://guide.rivile.cloud/modules/purchase/document_digitization/task_00003
[153]: https://guide.rivile.cloud/modules/purchase/duk
[154]: https://www.vmi.lt/evmi/pvm-tarifai
[155]: https://www.vmi.lt/evmi/2026-metai1
[156]: https://www.vmi.lt/evmi/mokesciu-naujienos/-/asset_publisher/Ayr4qj31Q5pu/content/d-c4-97l-vmi-prie-fm-vir-c5-a1ininko-2015-m.-liepos-21-d.-c4-afsakymo-nr.-va-49-pakeitimo-1
[157]: https://www.vmi.lt/evmi/documents/20142/391071/Mokes%C4%8Di%C5%B3+%C4%AFstatymo+pakeitimai+nuo+2026-01-01.pdf/
[158]: https://www.vmi.lt/evmi/pvm-klausimais
[159]: https://www.vmi.lt/evmi/pridetines-vertes-mokesciu-pakeitimai-nuo-2026-m.
[160]: https://www.vmi.lt/evmi/documents/20142/391107/Da%C5%BEniausiai+pasitaikan%C4%8Di%C5%B3+sandori%C5%B3+deklaravimas+FR0600+++i.SAF+kodas.pdf/1690b4fc-32c6-29f7-e0f1-ab98e2239035?t=1710757571089
[161]: https://guide.rivile.cloud/user_interfaces/data_processing/data_management/quick_date_entry
[162]: https://guide.rivile.cloud/news/202602
[163]: https://guide.rivile.cloud/erp_first_steps/opening_balances/import_of_inventory
[164]: https://guide.rivile.cloud/automation_training/praktika/index
[165]: https://aigentas.lt/blogas/rivile-integracija
[166]: https://guide.rivile.cloud/description_module_windows/pos/pos_barcode_rules
[167]: https://guide.rivile.cloud/news/202601
[168]: https://guide.test.rivile.cloud/automation_training/praktika/pvz-duomenys-eksportas
[169]: https://guide.test.rivile.cloud/automation_training/praktika/pvz-laikas-integracijos
[170]: https://guide.rivile.cloud/news
[171]: https://wyeceres.wye.com/support/solutions/articles/14000147463-purchase-invoice-overview-v6-0-
[172]: https://my.trimit.com/~/documentation/white%20paper%20-%20trimit%20setups.pdf
[173]: https://por.my.site.com/syrinxkb/s/article/Syrinx-v4-38-2-Release-Notes
[174]: https://docs.klevrapps.com/klevr-bc/klevr-keystone-analytics/api-dimensions/
[175]: https://guide.rivile.cloud/user_interfaces/data_processing/import_export/inventory_adjustments
[176]: https://guide.rivile.cloud/modules/payrolls/working_time/task_0003

---
[Quota] Used 1 Deep Research query (deep_research) | Pro: 200 left | Research: 19 left

[Conversation ID: cba4820d-787f-4728-afe5-1347e0192638]

