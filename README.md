# Nathavaram voter list

Private web app for the Nathavaram village voter list (Andhra Pradesh). Phase 1 is the server and the browser UI: sign in, enter voters, search, import and export, and print reports. The database and the sync API are shaped so an offline Android app can be added in phase 2 without a schema change.

The sample file under `sample/` is invented. Do not commit a real voter roll.

## Stack

- Python 3.12, [FastAPI](https://fastapi.tiangolo.com/), SQLAlchemy 2, Alembic
- SQLite by default. Set `DATABASE_URL` to `postgresql+psycopg://...` to use PostgreSQL; the migration uses types that both databases accept
- Jinja templates and a small stylesheet for the UI, with Noto Sans and Noto Sans Telugu so English labels and Telugu names render together
- bcrypt password hashes and a JWT in an HttpOnly cookie (browser) or `Authorization: Bearer` header (API and, later, Android)
- pytest for the API, the sync rules, and the main pages

Interactive API docs are turned off. The endpoint list is below, and the sync rules are in [docs/SYNC.md](docs/SYNC.md). The columns are in [docs/SCHEMA.md](docs/SCHEMA.md).

## Run it locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` before the first start:

- `SECRET_KEY` — a unique string of at least 16 characters. The placeholder in the example file is rejected.
- `ADMIN_USERNAME` and `ADMIN_PASSWORD` — the first admin is created from these on startup when that username does not exist yet. The password must be at least 10 characters. The example placeholder is rejected. The password is not reset on later restarts.

Then:

```bash
uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 and sign in. The SQLite file is created at `data/nathavaram.db` (that directory is gitignored).

```bash
pytest
```

## Docker

The image is what a later Jenkins job can build. This repo does not deploy anything and does not contain CI secrets. Pass `SECRET_KEY`, `ADMIN_USERNAME`, and `ADMIN_PASSWORD` at run time.

```bash
cp .env.example .env
# edit SECRET_KEY and ADMIN_PASSWORD
docker compose up --build
```

The database file lives in the `voter-data` volume at `/data/nathavaram.db`. `GET /healthz` returns `{"status":"ok"}` and is the only data-free unauthenticated route, together with `/static` (fonts and CSS). Use it as the compose health check.

To point the same image at PostgreSQL, set `DATABASE_URL` to a `postgresql+psycopg://` URL when you start the container. Do not put that password in the image or in git.

## Roles

| Role | Can do |
| --- | --- |
| `user` | Sign in, add and edit voters, search, import, export, reports, sync |
| `admin` | Everything a user can do, plus create other users at `/users` or `POST /api/users` |

There is always at least one active admin. An admin cannot demote or deactivate the last admin, and cannot deactivate their own account.

Both roles can see caste, party, and mobile. Treat every account as access to personal data: long passwords, a private network, and HTTPS in front of the container (`COOKIE_SECURE=true` when the site is served over HTTPS).

## CSV

Import and export use these headers, in this order:

`2024 Booth, 2024 B.S.No, 2026 Ward, Ward S No, VoterId, FirstName, SurName, RelativeName, HouseNo, Gender, Age, Caste, Mobile, C_City, Profession, Party`

The file must be UTF-8 (a byte-order mark is accepted). Export writes UTF-8 with a BOM so Excel on Windows keeps Telugu text, and it prefixes a quote when a cell starts with `=`, `+`, `-`, or `@`. Party may be empty. A fake nine-row file is at `sample/voters_sample.csv` and on the Import page after you sign in.

Import saves the valid rows and returns a report for the rest. A second import of the same VoterId skips that row because the active VoterId already exists.

## API

All `/api` routes require a signed-in user except `POST /api/auth/login`.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/auth/login` | Returns a JWT and sets the cookie |
| POST | `/api/auth/logout` | Clears the cookie |
| GET | `/api/auth/me` | Current user |
| GET | `/api/users` | Admin: list users |
| POST | `/api/users` | Admin: create a user |
| PATCH | `/api/users/{id}` | Admin: change role, active flag, or password |
| GET | `/api/voters` | Search. Query params: `q`, `voter_id`, `house_no`, `booth`, `ward`, `caste`, `party`, `mobile`, `gender`, `page`, `page_size` |
| POST | `/api/voters` | Create. Optional `id` (UUID) so a device can keep the key it generated offline |
| GET | `/api/voters/{id}` | One active voter |
| PATCH | `/api/voters/{id}` | Partial update |
| DELETE | `/api/voters/{id}` | Soft delete |
| POST | `/api/voters/import.csv` | Multipart CSV import. Body field name is `file` |
| GET | `/api/voters/export.csv` | CSV of the active rows matching the same filters as the list |
| GET | `/api/voters/export.xlsx` | Excel workbook of those rows |
| GET | `/api/reports/summary` | Counts by booth, ward, gender, age band, caste, party, profession |
| GET | `/api/reports/export.csv` | The same counts as CSV |
| GET | `/api/sync/pull?since=&limit=` | Changes with `revision` greater than `since`, including soft deletes |
| POST | `/api/sync/push` | Batch of device snapshots. See [docs/SYNC.md](docs/SYNC.md) |

Search is partial and case-insensitive for the text fields, including Telugu. `q` matches name, VoterId, HouseNo, mobile, caste, booth, ward, and party.

`GET /healthz` does not require a login. It returns no voter data.

## Phase 2

The Android app is not in this repository. [docs/SYNC.md](docs/SYNC.md) describes the pull/push contract, last-write-wins by `updated_at`, the server revision counter, and how a VoterId clash is reported. The Room database should use the same snake_case columns, a UUID primary key, `created_at`, `updated_at`, `revision`, `deleted_at`, and `source_id`.
