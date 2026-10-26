# Artificial CSV fixtures

All files are deliberately constructed test inputs, not real exports or datasets.
The original 11 fixture files are unchanged from 0.3. Do not resave the CP1251
example as UTF-8 or remove the intentional BOM in utf8_bom.csv.

Expected results below use **`--id-column id`**, default NBSP preservation and
UTF-8/comma, except the CP1251 example. Counts include all warnings, including C07.

| File | Data rows | Columns | Errors | Warnings | Trim changes | CLI exit |
|---|---:|---:|---:|---:|---:|---:|
| valid.csv | 3 | 3 | 0 | 2 | 0 | 0 |
| needs_trim.csv | 2 | 3 | 0 | 6 | 4 | 0 |
| duplicate_id.csv | 2 | 2 | 1 | 2 | 0 | 1 |
| duplicate_rows.csv | 2 | 2 | 1 | 3 | 0 | 1 |
| bad_width.csv | 3 | 2 | 2 | 0 | 0 | 1 |
| blank_record.csv | 3 | 2 | 1 | 0 | 0 | 1 |
| multiline.csv | 2 | 2 | 0 | 2 | 0 | 0 |
| utf8_bom.csv | 1 | 2 | 0 | 1 | 0 | 0 |
| nbsp_edges.csv | 1 | 2 | 0 | 1 | 0 | 0 |
| cp1251_semicolon.csv | 2 | 2 | 0 | 2 | 0 | 0 |
| broken_quotes.csv | n/a | n/a | no report: CSV_PARSE_ERROR | n/a | n/a | 2 |

The file named valid.csv is **not warning-free**: leading-zero and long numeric
IDs are intentionally preserved as text and reported by C07.

```bash
python -m checks examples/valid.csv --id-column id
python -m checks examples/needs_trim.csv --id-column id
python -m checks examples/duplicate_id.csv --id-column id
python -m checks examples/duplicate_rows.csv --id-column id
python -m checks examples/bad_width.csv --id-column id
python -m checks examples/cp1251_semicolon.csv --encoding cp1251 --delimiter semicolon --id-column id
```

Without `--id-column`, C05/C07 are skipped; duplicate_rows.csv is valid with one
C06 warning and exit 0. With `CSV_TRIM_NBSP=true`, nbsp_edges.csv has one planned
cell change and one additional C04 warning. No fixture is changed on disk.

The parser-only CLI remains available. `python -m csv_core examples/bad_width.csv`
still exits 0: parsing is not validation. Use `python -m checks` for checks.
