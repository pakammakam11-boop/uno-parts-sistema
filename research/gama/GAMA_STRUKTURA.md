# UNO Parts: Rivilė GAMA struktūra (2026-11-05)

Šaltinis: UNO serveris <RIVILES_SERVERIS> per WinRM, tik metaduomenys ir klasifikatoriai. Asmens duomenų (vardai, sumos, asmens kodai) netraukta, nes DPA dar nėra.

## Aplinka
- Windows Server 2016, Rivilė GAMA `C:\RIV_GAMA` (atnaujinta 2026-05-21).
- MS SQL Server 2019 Express `.\SQLEXPRESS`, DB `UNO_PARTS` (272 MB, 361 lentelė).
- Administrator turi sysadmin teises SQL serveryje.

## Svarbiausia išvada
DU skaičiai yra SQL bazėje, todėl juos galima skaityti tiesiai. Tabelio XLSX importas ir DU eksportas palyginimui nereikalingi, o B fazės klausimai Liutaurui gerokai sumažėja. 3 mėn. VDU istorija yra bazėje nuo 2021-12.

## DU / personalo lentelės (A = DU modulis, K = personalas)
| Lentelė | Kas | Eilučių |
|---|---|---|
| A06_TAB | darbuotojų DU kortelės (tabelio nr. `KODAS_TB`), įdarbinta nuo/iki | 116 (54 aktyvūs) |
| A07_TABP | pareigos, kalendorius, etatas | 126 |
| A08_TABB | banko sąskaitos išmokoms | 116 |
| A11_PATB | darbuotojo priskaitymai/atskaitymai (pastovūs, su galiojimu) | 1132 |
| A03_PAS | priskaitymų/atskaitymų rūšys (45 kodai) | 45 |
| A04_PAA | rūšių parametrai (Sodra, į VDU ar ne ir kt.) | 67 |
| A12_TBL / A13_TBLG | tabelio žymėjimai (FD, L, A, VD, DN, DP...) ir grupės | 43 / 13 |
| A09_KAL, A14_TBKD, A15_TBKV | darbo kalendoriai ir normos per dieną | 2 / 119 / 778 |
| A16_TBH / A17_TBD / A18_TBV | tabelio antraštė / dienos / valandos pagal žymėjimą (D01..D31, V01..V31) | 80 / 2064 / 17693 |
| A19_OPS / A20_PA | DU operacijos ir priskaitymų eilutės (suma, laikas, vidurkis) | 2060 / 14018 |
| A21_SODR | Sodros įmokos (VV, AP, tarifai) | 2318 |
| A22_MOKH / A23_MOKD | išmokėjimo žiniaraščiai | 253 / 2370 |
| A24_LIH / A25_LID | nedarbingumai ir atostogos su vidurkiais | 976 / 2367 |
| A26_ETAT | etatai | 22 |
| A30_FAH / A31_FAD | deklaracijos (FA, GPM313/SAM laukai L27..L50) | 67 / 1967 |
| K14_ASM / K16_OPH / K17_ATOS | personalo asmenys, darbo sutarčių operacijos, atostogų likučiai | 116 / 698 / 734 |

Istorija: tabelis, priskaitymai ir Sodra nuo 2021-12 iki 2026-10, nedarbingumai iki 2026-10-19.

## Failai šiame kataloge
`tables.csv` ir `columns.csv` (pilna schema), klasifikatoriai `A01_KINT`, `A03_PAS`, `A04_PAA`, `A09_KAL`, `A12_TBL`, `A13_TBLG`, `A14_TBKD`, `A26_ETAT` (.csv), `_ranges.csv`, `_pa_by_code.csv`.

## Kitas žingsnis (B fazė, po DPA)
1. Atskiras SQL vartotojas tik skaitymui (`db_datareader` UNO_PARTS), ne Administrator / sysadmin.
2. Jungtis: skaityti A06/A18/A20/A21/A24 už mėnesį ir palyginti su mūsų laukiamu DU (iki 0,01 €).
3. Tabelio įrašymas atgal į GAMA tik per GAMA importą, niekada tiesiai į DB.
4. Prieiga iš mūsų pusės: WinRM per HTTP (5985). Gamybai reikia VPN arba HTTPS.
