# Artificial CSV fixtures

All files are deliberately constructed test inputs, not real exports or datasets.
Each file has an exact expected behavior. Their bytes are part of the tests. Do not open/save the CP1251 example as UTF-8.

Default parsing is UTF-8 with a comma and a first-record header.

| File | Data records | Header fields | Behavior in 0.3 |
|---|---:|---:|---|
| `valid.csv` | 3 | 3 | Parse; IDs `00123`, `123`, `1234567890123456` remain strings; quoted comma/quotes preserved |
| `needs_trim.csv` | 2 | 3 | Parse unchanged; exactly 4 values would change under future space/TAB edge trim (1 header, 3 cells) |
| `duplicate_id.csv` | 2 | 2 | Parse; ID uniqueness is not validated yet |
| `duplicate_rows.csv` | 2 | 2 | Parse; both identical rows remain |
| `bad_width.csv` | 3 | 2 | Parse; data widths are 2, 1, 3; width validation is later C02 |
| `blank_record.csv` | 3 | 2 | Parse; middle data record is an empty tuple at physical line 3 |
| `multiline.csv` | 2 | 2 | Parse; first data record spans physical lines 2..3, next is line 4 |
| `utf8_bom.csv` | 1 | 2 | Parse; leading UTF-8 BOM recorded and removed from header text |
| `nbsp_edges.csv` | 1 | 2 | Parse; NBSP around Alice preserved |
| `cp1251_semicolon.csv` | 2 | 2 | Requires `--encoding cp1251 --delimiter semicolon`; Russian city names |
| `broken_quotes.csv` | n/a | n/a | `CSV_PARSE_ERROR` (unclosed quoted data field) |

Commands:

```bash
python -m csv_core examples/valid.csv
python -m csv_core examples/cp1251_semicolon.csv --encoding cp1251 --delimiter semicolon
python -m csv_core examples/multiline.csv
python -m csv_core examples/needs_trim.csv
python -m csv_core examples/bad_width.csv
python -m csv_core examples/broken_quotes.csv
```

The last command exits 2 by design. Others above should exit 0 under default
limits. `parsed: true` never means `validated: true` in this development stage.
Read tests in `test_core.py` for exact byte/value assertions.

Git attributes preserve the CP1251 file as bytes (`-text`); other CSV fixtures use
LF line endings. The UTF-8 BOM is intentional and must not be removed by an editor.
