# CSV Import Doctor

CSV Import Doctor is a local FastAPI service for checking CSV files before import into another application or data-processing pipeline. It detects structural problems, invalid headers, edge whitespace, missing or duplicate identifiers, duplicate rows, and numeric-looking identifiers that may lose information in spreadsheet software.

The service can produce a normalized copy with before-and-after reports and a complete change log. It never guesses missing values, deletes or reorders rows, or converts identifiers to numbers. Normalization is limited to explicitly configured edge-whitespace trimming and output formatting. Input encoding and delimiter must be specified correctly; they are not detected automatically.

CSV Import Doctor — сервис на FastAPI для проверки CSV перед импортом и создания нормализованной копии с отчётами до и после обработки и журналом изменений без удаления строк и преобразования идентификаторов в числа.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
bash run.sh                      # http://127.0.0.1:8000/docs
```

## API

| Route | Result |
|---|---|
| `POST /inspect` | JSON report; never changes the file. `valid` shows data errors |
| `POST /normalize` | ZIP: `cleaned.csv`, `report.json` (before/after), `changes.json` |
| `GET /profile` | Profile, effective settings and limits |
| `GET /health`, `GET /version` | Liveness and version |

Query parameters: `encoding` (`utf-8`, `cp1251`), `delimiter` (`comma`,
`semicolon`, `tab`), `id_column`, `report_view` (`full`, `compact`).

```bash
curl -F file=@examples/needs_trim.csv "http://127.0.0.1:8000/normalize?id_column=id" -o result.zip
```

`/normalize` errors: 409 `NORMALIZATION_BLOCKED` (data errors, report included),
413 limits, 422 unreadable CSV or bad parameters.

## Settings (`.env`)

`APP_NAME`, `APP_AUTHOR`, `APP_LOG_LEVEL`, `CSV_MAX_BYTES`, `CSV_MAX_ROWS`,
`CSV_TRIM_NBSP` (`false`), `CSV_QUOTING` (`all` | `minimal`),
`CSV_REPORT_VIEW` (`full` | `compact`). Process environment overrides `.env`.
`APP_HOST`/`APP_PORT` are read by `run.sh`. Invalid values stop startup.

## Health checks

With the application running:

```bash
curl --fail-with-body http://127.0.0.1:8000/health
curl --fail-with-body http://127.0.0.1:8000/version
```

Open http://127.0.0.1:8000/docs to upload a CSV and try the API.

## Troubleshooting

| Problem | What to check |
|---|---|
| `No module named ...` | Activate `.venv` and install the required dependencies. |
| `Address already in use` | Stop the previous server or choose another port through `APP_PORT`. |
| `DECODE_ERROR` | Select the correct `encoding`; encoding is not detected automatically. |
| Unexpected column count | Select the correct `delimiter`. |
| `NORMALIZATION_BLOCKED` | Read the returned report and correct the data errors before retrying. |
| A downloaded file is not a ZIP | Check the HTTP status and response body; the server may have returned a JSON error. |

## Logs and tests

Logs go to stderr as `key=value` lines with a `request_id` that matches the
`X-Request-ID` header. Cell values are never logged.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -v
```

GitHub Actions runs the same tests on every push and pull request and keeps
a ZIP of the verified commit as a run artifact.

`cleaned.csv` is not sanitized against spreadsheet formulas.
