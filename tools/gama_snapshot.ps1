# Rivilė GAMA DU snapshot vienam mėnesiui (tik skaitymas, be vardų / asmens kodų / adresų / banko sąskaitų).
# K16 (personalo operacijos) netraukiama: algos sumų neturi, o K16_PAV yra darbuotojo vardas (minimizavimas, 2026-11-05).
# Naudojimas: .\tools\gama_snapshot.ps1 -Month 2026-10 [-HistoryMonths 3] (istorija visada bent nuo sausio 1 d.) [-OutDir C:\Users\<user>\UNO_DATA]
# Kredencialai: Export-Clixml failas (Get-Credential), slaptažodis čia nesaugomas.
param(
  [Parameter(Mandatory)][string]$Month,
  [int]$HistoryMonths = 3,
  [string]$Server = '<RIVILES_SERVERIS>',
  [string]$CredFile = "$env:USERPROFILE\.uno_cred.xml",
  [string]$OutDir = "$env:USERPROFILE\UNO_DATA"
)
$ErrorActionPreference = 'Stop'
$m = [datetime]::ParseExact("$Month-01", 'yyyy-MM-dd', $null)
$hist = $m.AddMonths(-$HistoryMonths)
$jan1 = [datetime]::new($m.Year, 1, 1)
if ($jan1 -lt $hist) { $hist = $jan1 }   # metinei 36 VDU ribai istorija turi siekti metų pradžią
$end = $m.AddMonths(1)
$cred = Import-Clixml $CredFile

$data = Invoke-Command -ComputerName $Server -Credential $cred -ArgumentList $m, $hist, $end -ScriptBlock {
  param($m, $hist, $end)
  function Q($sql) {
    $cn = New-Object System.Data.SqlClient.SqlConnection "Server=.\SQLEXPRESS;Database=UNO_PARTS;Integrated Security=True;ApplicationIntent=ReadOnly"
    $cn.Open()
    try {
      $cm = $cn.CreateCommand(); $cm.CommandTimeout = 120; $cm.CommandText = $sql
      [void]$cm.Parameters.AddWithValue('@m', $m); [void]$cm.Parameters.AddWithValue('@h', $hist); [void]$cm.Parameters.AddWithValue('@e', $end)
      $rd = $cm.ExecuteReader(); $rows = @()
      while ($rd.Read()) {
        $o = [ordered]@{}
        for ($i = 0; $i -lt $rd.FieldCount; $i++) {
          $v = $rd.GetValue($i)
          if ($v -is [DBNull]) { $v = $null } elseif ($v -is [datetime]) { $v = $v.ToString('yyyy-MM-dd') } elseif ($v -is [string]) { $v = $v.Trim() } elseif ($v -is [decimal]) { $v = $v.ToString([Globalization.CultureInfo]::InvariantCulture) }
          $o[$rd.GetName($i)] = $v
        }
        $rows += [pscustomobject]$o
      }
      , $rows
    } finally { $cn.Close() }
  }
  # Darbuotoju langas (A06): tas pats predikatas minimizuoja A06, A07 ir A11 atranka.
  $win = "A06_ZML_NUO < @e and (A06_ZML_IKI is null or A06_ZML_IKI < '1901-01-01' or A06_ZML_IKI >= @h)"
  # Nebuvimai: A24_DATE_PR = laikotarpio pradzia, A24_DATE_PB = pabaiga; kai PR tuscias (< 1901-01-01), pakaitalas A24_DATE.
  $abs_s = "(case when h.A24_DATE_PR is null or h.A24_DATE_PR < '1901-01-01' then h.A24_DATE else h.A24_DATE_PR end)"
  $abs_e = "(case when h.A24_DATE_PB is null or h.A24_DATE_PB < '1901-01-01' then $abs_s else h.A24_DATE_PB end)"
  # A11_POZ_DATE = 1: priskaitymas terminuotas (galioja tik A11_BEG_DATE..A11_END_DATE); kitaip (ne 1 / null) neterminuotas, imamas be datu lango.
  $days = (1..31 | ForEach-Object { 'D{0:00}' -f $_ }) -join ','
  [ordered]@{
    employees = Q "select A06_KODAS_TB, A06_KODAS_IS, A06_ZML_NUO, A06_ZML_IKI, A06_KODAS_GS from dbo.A06_TAB where $win"
    positions = Q "select A07_KODAS_TB, A07_KODAS_IS, A07_KODAS_LA, A07_KODAS_DS, A07_TIPAS, A07_TB_FOR, A07_V_KAL, A07_KODAS_LE, A07_SK_VIRSV from dbo.A07_TABP where A07_KODAS_TB in (select A06_KODAS_TB from dbo.A06_TAB where $win)"
    # A02: darbuotojų kintamieji su galiojimo data (10001 taikomas NPD 0/1, 10003 riboto darbingumo NPD, 100 etatinis atlyginimas,
    # 101 valandos įkainis, 19203 kaupimo %, 801/805 priedai) ir įmonės konstantos (TIPAS 1). Reikšmė = paskutinė su A02_DATE < @e.
    variables = Q "select A02_KODAS_LK, A02_TIPAS, A02_DATE, A02_KODAS_TB, A02_KODAS_IS, A02_SUMA from dbo.A02_KINR where A02_DATE < @e and (A02_TIPAS = 1 or A02_KODAS_TB in (select A06_KODAS_TB from dbo.A06_TAB where $win))"
    terms = Q "select A11_KODAS_TB, A11_KODAS_LP, A11_TIPAS, A11_KODAS_IS, A11_EIL_NR, A11_ATS_IP, A11_IP_SUMA, A11_POZ_DATE, A11_BEG_DATE, A11_END_DATE from dbo.A11_PATB where A11_KODAS_TB in (select A06_KODAS_TB from dbo.A06_TAB where $win) and (A11_POZ_DATE is null or A11_POZ_DATE <> 1 or (A11_BEG_DATE < @e and (A11_END_DATE is null or A11_END_DATE < '1901-01-01' or A11_END_DATE >= @h)))"
    calendar_days = Q "select A14_KODAS_LA, A14_DATE, A14_FRAKCIJA, A14_VD, $($days -replace 'D','A14_D') from dbo.A14_TBKD where A14_DATE >= @h and A14_DATE < @e"
    calendar_hours = Q "select A15_KODAS_LA, A15_DATE, A15_KODAS_LT, A15_KODAS_LG, A15_FRAKCIJA, A15_VD, A15_V00, $(((1..31) | ForEach-Object { 'A15_V{0:00}' -f $_ }) -join ',') from dbo.A15_TBKV where A15_DATE >= @h and A15_DATE < @e"
    timesheet_days = Q "select A17_KODAS_IS, A17_DATE, A17_KODAS_TB, A17_KODAS_LA, A17_FRAKCIJA, A17_VD, $($days -replace 'D','A17_D') from dbo.A17_TBD where A17_DATE >= @h and A17_DATE < @e"
    timesheet_hours = Q "select A18_KODAS_IS, A18_DATE, A18_KODAS_TB, A18_KODAS_LT, A18_KODAS_LG, A18_KODAS_LA, A18_FRAKCIJA, A18_VD, A18_V00, $(((1..31) | ForEach-Object { 'A18_V{0:00}' -f $_ }) -join ',') from dbo.A18_TBV where A18_DATE >= @h and A18_DATE < @e"
    operations = Q "select A19_KODAS_LO, A19_KODAS_TB, A19_DATE from dbo.A19_OPS where A19_DATE >= @h and A19_DATE < @e"
    pay_lines = Q "select A20_KODAS_LO, A20_EIL_NR, A20_KODAS_LP, A20_TIPAS, A20_KODAS_TB, A20_KODAS_IS, A20_DATE_SP, A20_DATE_KR, A20_MODUL, A20_MODUL_NR, A20_SUMA, A20_LAIKAS, A20_VIDUR from dbo.A20_PA where A20_DATE_SP >= @h and A20_DATE_SP < @e"
    sodra = Q "select A21_KODAS_LO, A21_EIL_NR, A21_KODAS_TB, A21_DATE_SP, A21_DATE_KR, A21_SUMA_VV, A21_SUMA_AP, A21_PROC01, A21_SUMA01, A21_PROC03, A21_SUMA03, A21_PROC27, A21_SUMA27 from dbo.A21_SODR where A21_DATE_SP >= @h and A21_DATE_SP < @e"
    absences = Q "select A24_KODAS_LI, A24_KODAS_TB, A24_DATE, A24_DATE_PB, A24_DATE_PR, A24_TIPAS, A24_VID_TIP, A24_KODAS_LG, A24_SUMAM, A24_VIDUR, A24_LIG_TIP, A24_BUSENA, A24_LAIKAS_MOK from dbo.A24_LIH h where $abs_s < @e and $abs_e >= @h"
    absence_lines = Q "select d.A25_KODAS_LI, d.A25_EIL_NR, d.A25_DATE, d.A25_DDIEN, d.A25_DVAL, d.A25_KDIEN, d.A25_KVAL, d.A25_SUMA, d.A25_SUMAK, d.A25_VIDUR from dbo.A25_LID d join dbo.A24_LIH h on h.A24_KODAS_LI = d.A25_KODAS_LI where $abs_s < @e and $abs_e >= @h"
  }
}

New-Item -ItemType Directory -Force $OutDir | Out-Null
$file = Join-Path $OutDir "gama_$Month.json"
$clean = [ordered]@{ month = $Month; history_from = $hist.ToString('yyyy-MM-dd'); extracted_at = (Get-Date).ToString('s') }
foreach ($k in 'employees','positions','terms','calendar_days','calendar_hours','timesheet_days','timesheet_hours','operations','pay_lines','sodra','absences','absence_lines','variables') {
  $clean[$k] = @($data[$k] | Select-Object * -ExcludeProperty PSComputerName, RunspaceId, PSShowComputerName)
}
$clean | ConvertTo-Json -Depth 5 -Compress | Set-Content -Encoding UTF8 $file
foreach ($k in $clean.Keys) { if ($clean[$k] -is [array]) { '{0,-16} {1}' -f $k, $clean[$k].Count } }
"-> $file"
