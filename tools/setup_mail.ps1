# UNO apskaita: Gmail prijungimas (sąskaitų importas iš pašto).
# Reikia Gmail "programos slaptažodžio" (App password):
#   1) Google paskyroje įjunkite 2 žingsnių patvirtinimą (myaccount.google.com/security)
#   2) Atidarykite https://myaccount.google.com/apppasswords, sukurkite slaptažodį pavadinimu "UNO"
#   3) Nukopijuokite 16 simbolių kodą (be tarpų arba su jais, abu tinka) ir įveskite čia.
# Reikšmės išsaugomos kaip USER aplinkos kintamieji UNO_IMAP_USER / UNO_IMAP_PASSWORD.

$defaultUser = "ratokampas@gmail.com"
$user = Read-Host "Gmail adresas [$defaultUser]"
if ([string]::IsNullOrWhiteSpace($user)) { $user = $defaultUser }
# Paprastas Read-Host (ne -AsSecureString): kai kuriuose terminaluose i slepta lauka neveikia iklijavimas.
$plain = (Read-Host "Gmail programos slaptazodis (iklijuokite, matysis ekrane)").Replace(" ", "").Trim()
Clear-Host

if ([string]::IsNullOrWhiteSpace($plain)) { Write-Host "Slaptazodis tuscias, nutraukta." -ForegroundColor Red; exit 1 }

[Environment]::SetEnvironmentVariable("UNO_IMAP_USER", $user, "User")
[Environment]::SetEnvironmentVariable("UNO_IMAP_PASSWORD", $plain, "User")
# Tikros UNO sąskaitos saugomos už repo (UNO_DATA), AI laukų ištraukimas tikrai dėžutei išjungtas.
if ($user -ne "ratokampas@gmail.com") {
    [Environment]::SetEnvironmentVariable("UNO_MAIL_DIR", (Join-Path $env:USERPROFILE "UNO_DATA\mail"), "User")
    [Environment]::SetEnvironmentVariable("UNO_MAIL_AI", "0", "User")
} else {
    [Environment]::SetEnvironmentVariable("UNO_MAIL_DIR", $null, "User")
    [Environment]::SetEnvironmentVariable("UNO_MAIL_AI", $null, "User")
}
# Misri asmenine dezute: imamos tik saskaitos (ne sutartys, katalogai ar issiusti laiskai).
if ($user -eq "ratokampas@gmail.com") {
    [Environment]::SetEnvironmentVariable("UNO_IMAP_QUERY", "(saskaita OR invoice OR faktura OR PVM) -from:me -filename:sutartis", "User")
} else {
    [Environment]::SetEnvironmentVariable("UNO_IMAP_QUERY", $null, "User")
}

$env:UNO_IMAP_USER = $user
$env:UNO_IMAP_PASSWORD = $plain
$check = @'
import imaplib, os
try:
    c = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
    c.login(os.environ["UNO_IMAP_USER"], os.environ["UNO_IMAP_PASSWORD"])
    c.logout()
    print("OK")
except Exception as exc:
    print("KLAIDA: " + type(exc).__name__)
'@
$result = $check | py -3.12 -
$plain = $null
Remove-Item Env:UNO_IMAP_PASSWORD -ErrorAction SilentlyContinue

if ($result -match "^OK") {
    Write-Host "Prisijungimas pavyko. Dabar uzdarykite ir is naujo paleiskite paleisti.bat." -ForegroundColor Green
} else {
    Write-Host "Prisijungti nepavyko ($result). Patikrinkite adresa ir programos slaptazodi, tada paleiskite dar karta." -ForegroundColor Red
}
