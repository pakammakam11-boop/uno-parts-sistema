# Rivilė GAMA tiekėjų kartoteka -> suppliers.json (tik skaitymas).
# Imami klientai/tiekėjai, kurie per paskutinius 3 m. turėjo pirkimo dokumentų (I06_OP_TIP 1/2).
# Laukai: Rivilės kodas, pavadinimas, įmonės kodas, PVM kodas, el. paštas, banko sąskaita (N33 numatytoji arba pirma),
# sąnaudų sąskaita numatyta 6000. Adresai, telefonai, atstovai neimami (minimizavimas).
# Naudojimas: .\tools\gama_suppliers.ps1 [-OutFile C:\Users\<user>\UNO_DATA\suppliers.json]
param(
  [string]$Server = '<RIVILES_SERVERIS>',
  [string]$CredFile = "$env:USERPROFILE\.uno_cred.xml",
  [string]$OutFile = "$env:USERPROFILE\UNO_DATA\suppliers.json"
)
$ErrorActionPreference = 'Stop'
$cred = Import-Clixml $CredFile

$rows = Invoke-Command -ComputerName $Server -Credential $cred -ScriptBlock {
  $sql = @"
WITH buy AS (
  SELECT DISTINCT h.I06_KODAS_KS AS ks FROM I06_PARH h
  WHERE h.I06_OP_TIP IN (1, 2) AND h.I06_DOK_DATA >= DATEADD(year, -3, GETDATE())
),
bank AS (
  SELECT N33_KODAS_KS AS ks, N33_K_SASK AS iban,
         ROW_NUMBER() OVER (PARTITION BY N33_KODAS_KS ORDER BY N33_NUTYL DESC, N33_EIL_NR) AS rn
  FROM N33_KBAN WHERE ISNULL(N33_K_SASK, '') <> ''
)
SELECT n.N08_KODAS_KS AS rivile_code, n.N08_PAV AS name, n.N08_IM_KODAS AS company_code,
       n.N08_PVM_KODAS AS vat_code, n.N08_E_MAIL AS email, b.iban AS iban
FROM N08_KLIJ n
JOIN buy ON buy.ks = n.N08_KODAS_KS
LEFT JOIN bank b ON b.ks = n.N08_KODAS_KS AND b.rn = 1
"@
  $cn = New-Object System.Data.SqlClient.SqlConnection "Server=.\SQLEXPRESS;Database=UNO_PARTS;Integrated Security=True;ApplicationIntent=ReadOnly"
  $cn.Open()
  try {
    $cm = $cn.CreateCommand(); $cm.CommandTimeout = 120; $cm.CommandText = $sql
    $rd = $cm.ExecuteReader(); $out = @()
    while ($rd.Read()) {
      $o = [ordered]@{}
      for ($i = 0; $i -lt $rd.FieldCount; $i++) {
        $v = $rd.GetValue($i); $o[$rd.GetName($i)] = if ($v -is [DBNull]) { '' } else { "$v".Trim() }
      }
      $out += [pscustomobject]$o
    }
    , $out
  } finally { $cn.Close() }
}

$items = foreach ($r in $rows) {
  [ordered]@{
    rivile_code     = $r.rivile_code
    name            = $r.name
    company_code    = $r.company_code
    vat_code        = ($r.vat_code -replace '\s', '')
    iban            = ($r.iban -replace '\s', '')
    email           = $r.email
    expense_account = '6000'   # sąnaudų sąskaitą tikslina buhalterė (prekėms dažnai 2010)
  }
}
New-Item -ItemType Directory -Force -Path (Split-Path $OutFile) | Out-Null
if (Test-Path $OutFile) { Copy-Item $OutFile "$OutFile.bak" -Force }
[IO.File]::WriteAllText($OutFile, (ConvertTo-Json @($items) -Depth 3), [Text.UTF8Encoding]::new($false))
$withCode = @($items | Where-Object { $_.company_code }).Count
$withVat = @($items | Where-Object { $_.vat_code }).Count
$withIban = @($items | Where-Object { $_.iban }).Count
Write-Host "Tiekėjų: $(@($items).Count) (su įmonės kodu $withCode, su PVM kodu $withVat, su IBAN $withIban) -> $OutFile"
