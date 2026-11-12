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
| `user` | Sign in, search, add, view, and edit single voters, reports, sync |
| `admin` | Everything a user can do, plus bulk operations: CSV/Excel export, CSV import, delete |
| owner (an `admin` with the `is_owner` flag) | Everything an admin can do, plus user management at `/users` or `/api/users` |

A regular user gets 403 for export, import, delete, and user management. An admin who is not the owner gets 403 for user management only. The owner is the admin created from `ADMIN_USERNAME`; later admins are not owners.

Login is throttled (5 failures per 5 minutes per IP and per username, then 429 with `Retry-After`), and new or reset accounts must change their password before doing anything else. Details: [docs/SECURITY.md](docs/SECURITY.md).

Cookie sessions are protected against CSRF. Browser forms carry a hidden `csrf_token`. A script that signs in with the cookie must send the `csrf_token` from the login response as `X-CSRF-Token` on POST/PUT/PATCH/DELETE. Requests that use `Authorization: Bearer` are exempt.

With `APP_ENV=production` the app refuses to start unless `COOKIE_SECURE=true`.

There is always at least one active admin. An admin cannot demote or deactivate the last admin, and cannot deactivate their own account.

Both roles can see caste, party, and mobile. Treat every account as access to personal data: long passwords, a private network, and HTTPS in front of the container (`COOKIE_SECURE=true` when the site is served over HTTPS).

## CSV

Import and export use these headers, in this order:

`Booth, 2024 B.S.No, Ward, Ward S No, VoterId, FirstName, SurName, RelativeName, Address, Village, Permanent address, Local, Gender, Age, Caste, Mobile, C_City, Profession, Party`

Older files still import: `2024 Booth` / `2026 Ward` headers work, a `HouseNo` column is folded into the start of Address as `H.No <n>, <address>`, and Address, Village, Permanent address and Local may be left out. Local is Yes or No (blank means No).

The file must be UTF-8 (a byte-order mark is accepted). Export writes UTF-8 with a BOM so Excel on Windows keeps Telugu text, and it prefixes a quote when a cell starts with `=`, `+`, `-`, `@`, a tab, or a carriage return (this includes the report export). Import removes one such leading quote, so `+91` mobiles survive a round trip. Party may be empty. A fake nine-row file is at `sample/voters_sample.csv` and on the Import page after you sign in.

Import saves the valid rows and returns a report for the rest. A second import of the same VoterId skips that row because the active VoterId already exists.

## API

All `/api` routes require a signed-in user except `POST /api/auth/login`.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/auth/login` | Returns a JWT and sets the cookie. 401 for any bad credentials (same message), 429 + `Retry-After` when throttled. `must_change_password` in the response says whether a change is pending |
| POST | `/api/auth/change-password` | JSON `current_password`, `new_password` (10+ characters, different from the current one). Works while a change is pending |
| POST | `/api/auth/logout` | Clears the cookie |
| GET | `/api/auth/me` | Current user |
| GET | `/api/users` | Owner: list users |
| POST | `/api/users` | Owner: create a user |
| PATCH | `/api/users/{id}` | Owner: change role, active flag, or password (a password reset forces a change at next use) |
| GET | `/api/voters` | List one page. Query params: `page`, `page_size` only. Search filters in the URL are refused with 422 so personal data stays out of access logs |
| POST | `/api/voters/search` | Search. JSON body: `q`, `voter_id`, `village`, `is_local` (`Yes` / `No` / `Any`), `booth`, `ward`, `caste`, `party`, `mobile`, `gender`, `page`, `page_size` |
| POST | `/api/voters` | Create. Optional `id` (UUID) so a device can keep the key it generated offline |
| GET | `/api/voters/{id}` | One active voter |
| PATCH | `/api/voters/{id}` | Partial update |
| DELETE | `/api/voters/{id}` | Admin: soft delete. The row is stripped of personal fields |
| POST | `/api/voters/import.csv` | Admin: multipart CSV import. Body field name is `file` |
| POST | `/api/voters/export.csv` | Admin: CSV of the active rows matching the filters in the optional JSON body |
| POST | `/api/voters/export.xlsx` | Admin: Excel workbook of those rows |
| GET | `/api/reports/summary` | Counts by booth, ward, gender, age band, caste, party, profession |
| GET | `/api/reports/export.csv` | The same counts as CSV |
| GET | `/api/sync/pull?since=&limit=` | Changes with `revision` greater than `since`, including soft deletes |
| POST | `/api/sync/push` | Batch of device snapshots. See [docs/SYNC.md](docs/SYNC.md) |

Search is partial and case-insensitive for the text fields, including Telugu. `q` matches name, VoterId, address, village, mobile, caste, booth, ward, and party.

Editing: any signed-in user can add a voter. On an existing voter the `user` role may change only Party, Caste, Mobile and Address; owner and admin may change every field except VoterId, which nobody can change after create. See `docs/SCHEMA.md`.

`GET /healthz` does not require a login. It returns no voter data.

## Phase 2

The Android app is not in this repository. [docs/SYNC.md](docs/SYNC.md) describes the pull/push contract, last-write-wins by `updated_at`, the server revision counter, and how a VoterId clash is reported. The Room database should use the same snake_case columns, a UUID primary key, `created_at`, `updated_at`, `revision`, `deleted_at`, and `source_id`.
