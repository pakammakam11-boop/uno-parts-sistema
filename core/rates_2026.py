"""2026 m. LT darbo užmokesčio parametrai.

Šaltiniai (patikrinta 2026-11-04):
- VMI: https://www.vmi.lt/evmi/5725 (GPM tarifai, 36 VDU = 83 237,40 €)
- KPMG 2026-01 / bss.biz: NPD = 747 - 0,49 x (DU - MMA)
- Sodra: https://sodra.lt/imoku-tarifai/imoku-tarifai-taikomi-uz-samdomus-darbuotojus
"""
from decimal import Decimal as D

MMA = D("1153.00")              # minimali mėnesinė alga
VDU = D("2312.15")              # VDU, taikomas 2026 m. riboms (36 VDU = 83 237,40)
NPD_MAX = D("747.00")           # NPD, kai DU <= MMA
NPD_COEF = D("0.49")            # NPD = 747 - 0,49 x (DU - MMA), ne mažiau 0

GPM_RATE = D("0.20")            # iki 36 VDU per metus (visi demo darbuotojai patenka)
# Ligos išmokos, įskaitant darbdavio mokamas už 1-2 d.: 15 % GPM, be Sodros įmokų.
# Šaltinis: https://www.vmi.lt/evmi/kaip-apmokestinamas-darbo-uzmokestis- (patikrinta 2026-11-04)
SICK_PAY_GPM_RATE = D("0.15")
GPM_RATE_25_FROM_YEARLY = D("83237.40")
# Sodros VSD lubos: 60 VDU per metus. Šaltinis:
# https://sodra.lt/nuo-2026-m-sausio-1-d-taikomi-sodros-imoku-tarifai-turintiems-samdomu-darbuotoju
SODRA_VSD_CEILING_YEARLY = D("138729.00")
GPM_RATE_32_FROM_YEARLY = SODRA_VSD_CEILING_YEARLY

# Darbuotojo įmokos: pensijų 8,72 + ligos 1,99 + motinystės 1,81 + sveikatos 6,98
EMPLOYEE_SODRA = D("0.1950")
EMPLOYEE_PENSION_ACCUMULATION = D("0.03")  # tik jei dalyvauja kaupime (neprivaloma)

# Darbdavio įmokos (I nelaimingų atsitikimų grupė)
EMPLOYER_SODRA_INDEFINITE = D("0.0177")  # neterminuota sutartis
EMPLOYER_SODRA_FIXED_TERM = D("0.0249")  # terminuota sutartis

# Darbo kodekso viršvalandžių koeficientas
OVERTIME_MULTIPLIER = D("1.5")
NIGHT_MULTIPLIER = D("1.5")
HOLIDAY_MULTIPLIER = D("2.0")

VAT_STANDARD = D("0.21")
