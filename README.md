# CSV Import Doctor

Version **0.6**. Checks a CSV before import and produces a normalized copy with a
full change log. The first record must be a header. Identifiers stay text; rows
are never deleted, reordered or guessed. Only edge SPACE/TAB are trimmed.

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

## Logs and tests

Logs go to stderr as `key=value` lines with a `request_id` that matches the
`X-Request-ID` header. Cell values are never logged.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -v
```

`cleaned.csv` is not sanitized against spreadsheet formulas.
