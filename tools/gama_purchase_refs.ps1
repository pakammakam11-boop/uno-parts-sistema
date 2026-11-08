# Rivilė GAMA pirkimo dokumentų nuorodos EIP importui -> UNO_DATA\gama_purchase_refs.json (tik skaitymas).
# Imama TIK agreguota/klasifikatorių informacija: kaip egzistuojantys pirkimo dokumentai (I06_OP_TIP 1/2, 3 m.)
# naudoja tipą, mokesčio kodą, PVM klasifikatorių, sąnaudų/paslaugų kodus, sandėlį, valiutą. Jokių tiekėjų, asmenų ar sumų.
# Naudojimas: .\tools\gama_purchase_refs.ps1 [-OutFile C:\Users\<user>\UNO_DATA\gama_purchase_refs.json]
param(
  [string]$Server = '<RIVILES_SERVERIS>',
  [string]$CredFile = "$env:USERPROFILE\.uno_cred.xml",
  [string]$OutFile = "$env:USERPROFILE\UNO_DATA\gama_purchase_refs.json"
)
$ErrorActionPreference = 'Stop'
$cred = Import-Clixml $CredFile

$data = Invoke-Command -ComputerName $Server -Credential $cred -ScriptBlock {
  function Q($sql) {
    $cn = New-Object System.Data.SqlClient.SqlConnection "Server=.\SQLEXPRESS;Database=UNO_PARTS;Integrated Security=True;ApplicationIntent=ReadOnly"
    $cn.Open()
    try {
      $cm = $cn.CreateCommand(); $cm.CommandTimeout = 120; $cm.CommandText = $sql
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
  $win = "h.I06_OP_TIP IN (1, 2) AND h.I06_DOK_DATA >= DATEADD(year, -3, GETDATE())"
  $errs = @()
  function Safe($name, $sql) {
    try { Q $sql } catch { $script:errs += "${name}: $($_.Exception.Message)"; @() }
  }
  $r = [ordered]@{
    header_combos = Safe 'header_combos' "SELECT h.I06_OP_TIP op_tip, h.I06_PVM_TIP pvm_tip, h.I06_KODAS_XS tax_code, h.I06_KODAS_AK ak, h.I06_VAL_POZ val_poz, h.I06_KODAS_SS ledger_ss, h.I06_KODAS_VL currency, h.I06_PVM_SKOL pvm_skol, h.I06_KODAS_MS ms, COUNT(*) n FROM I06_PARH h WHERE $win GROUP BY h.I06_OP_TIP, h.I06_PVM_TIP, h.I06_KODAS_XS, h.I06_KODAS_AK, h.I06_VAL_POZ, h.I06_KODAS_SS, h.I06_KODAS_VL, h.I06_PVM_SKOL, h.I06_KODAS_MS ORDER BY n DESC"
    line_combos = Safe 'line_combos' "SELECT TOP 60 d.I07_TIPAS tipas, d.I07_KODAS code, d.I07_KODAS_IS warehouse, d.I07_KODAS_OS obj, d.I07_KODAS_US unit, d.I07_MOKESTIS tax_idx, d.I07_MOKESTIS_P tax_pct, d.I07_KODAS_KL vat_klasi, d.I07_KODAS_KS ks_line, COUNT(*) n FROM I07_PARD d JOIN I06_PARH h ON h.I06_KODAS_PO = d.I07_KODAS_PO WHERE $win GROUP BY d.I07_TIPAS, d.I07_KODAS, d.I07_KODAS_IS, d.I07_KODAS_OS, d.I07_KODAS_US, d.I07_MOKESTIS, d.I07_MOKESTIS_P, d.I07_KODAS_KL, d.I07_KODAS_KS ORDER BY n DESC"
    taxes = Safe 'taxes' "SELECT N12_KODAS_XS code, N12_PAV name, N12_A_TAX a, N12_B_TAX b, N12_C_TAX c, N12_D_TAX d, N12_E_TAX e, N12_F_TAX f, N12_G_TAX g, N12_H_TAX h, N12_A_KLASI ka, N12_B_KLASI kb, N12_C_KLASI kc, N12_D_KLASI kd, N12_E_KLASI ke, N12_F_KLASI kf, N12_G_KLASI kg, N12_H_KLASI kh, N12_NM_KLASI knm, N12_KODAS_SS_P ledger_p, N12_KODAS_SS_PI ledger_pi FROM N12_TAX"
    vat_classifiers = Safe 'vat_classifiers' "SELECT N63_KODAS_KL code, N63_PAV name, N63_KLASI klasi FROM N63_ISAF"
    codes = Safe 'codes' "SELECT N25_KODAS_BS code, N25_PAV name, N25_TIPAS tipas, N25_KODAS_SS ledger, N25_KODAS_US unit, N25_MOKESTIS mokestis, N25_PROCENT procent, N25_OP_RUSIS op_rusis FROM N25_KOD"
    currencies = Safe 'currencies' "SELECT N45_KODAS_VL code, N45_PAV name, N45_VAL_ID id, N45_BAZE base, N45_EURO euro FROM N45_VAL"
    warehouses = Safe 'warehouses' "SELECT N07_KODAS_IS code, N07_PAV name, N07_PAD_TIP tip FROM N07_IMON"
    units = Safe 'units' "SELECT TOP 10 d.I07_KODAS_US unit, COUNT(*) n FROM I07_PARD d JOIN I06_PARH h ON h.I06_KODAS_PO = d.I07_KODAS_PO WHERE $win GROUP BY d.I07_KODAS_US ORDER BY n DESC"
    doc_number_lengths = Safe 'doc_number_lengths' "SELECT TOP 5 LEN(h.I06_DOK_NR) len, COUNT(*) n FROM I06_PARH h WHERE $win GROUP BY LEN(h.I06_DOK_NR) ORDER BY n DESC"
  }
  $r['errors'] = $errs
  $r
}
$out = [ordered]@{ generated = (Get-Date -Format 'yyyy-MM-dd'); source = 'UNO_PARTS read-only'; data = $data }
New-Item -ItemType Directory -Force -Path (Split-Path $OutFile) | Out-Null
[IO.File]::WriteAllText($OutFile, (ConvertTo-Json $out -Depth 6), [Text.UTF8Encoding]::new($false))
Write-Host ("Nuorodos -> " + $OutFile + " ; klaidu: " + @($data.errors).Count)
