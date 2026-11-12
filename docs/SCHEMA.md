# Nathavaram voter schema

Phase 1 stores the village list in a relational database. SQLite is the default. The same SQLAlchemy models and this Alembic migration run on PostgreSQL when `DATABASE_URL` uses the `postgresql+psycopg://` driver.

Display names below are the exact labels in the web form and the exact CSV/Excel headers. The database and JSON API use the snake_case names.

## Voters

| Display name | Column | Type | Rules |
| --- | --- | --- | --- |
| Booth | `booth` | text, null | Up to 64 characters. Formerly "2024 Booth" / `booth_2024` |
| 2024 B.S.No | `bs_no_2024` | text, null | Up to 64 characters |
| Ward | `ward` | text, null | Up to 64 characters. Formerly "2026 Ward" / `ward_2026` |
| Ward S No | `ward_s_no` | text, null | Up to 64 characters |
| VoterId | `voter_id` | text, required | 3–32 letters or digits, stored uppercase. Unique among rows that are not soft-deleted. Entered when the voter is created and **read-only afterwards**: edit, PATCH and sync push reject a different value (see below) |
| FirstName | `first_name` | text, required | Up to 128 characters. Telugu and other Unicode letters are stored as UTF-8 |
| SurName | `surname` | text, null | Up to 128 characters |
| RelativeName | `relative_name` | text, null | Up to 128 characters |
| Address | `address` | text, null | Up to 1000 characters. Holds what used to be the separate HouseNo column, as a prefix such as `H.No 12-A, Main St` (see "House No removal") |
| Village | `village` | text, null | Up to 128 characters |
| Permanent address | `permanent_address` | text, null | Up to 1000 characters |
| Local | `is_local` | boolean, required, default `false` | Shown as **Local** with Yes / No in the web form, list, CSV and Android. Import accepts Yes/No (also Y/N, true/false, 1/0); blank means No. Rows that existed before the column was added are **No** |
| Gender | `gender` | text, required | `Male`, `Female`, `Other`, `Unknown`. Blank input becomes `Unknown`. Matching is case-insensitive and stored in this canonical form |
| Age | `age` | integer, null | Whole number from 1 to 130, or null when unknown |
| Caste | `caste` | text, null | Up to 128 characters |
| Mobile | `mobile` | text, null | Stored exactly as entered (trimmed), including a leading `0` or `+91`. Validation is loose: after removing spaces, hyphens, and an optional `+91`, `91`, or `0` prefix, the remaining value must be a 10-digit Indian mobile starting with 6–9 |
| C_City | `c_city` | text, null | Up to 128 characters |
| Profession | `profession` | text, null | Up to 128 characters |
| Party | `party` | text, null | Empty means no answer. Otherwise one of: `TDP`, `YSRCP`, `Jana Sena`, `BJP`, `INC`, `CPI`, `Independent`, `Unknown`, `Prefer not to say`. Matching is case-insensitive and stored in this canonical form |

Text columns reject ASCII control characters. Names are not restricted to a single script, so Telugu text round-trips through the API, the form, and CSV.

## Sync columns

These columns are not part of the paper list. They exist so an offline Android client can sync in phase 2 without a schema change.

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | text UUID, primary key | Client-generated or server-generated. Stable across devices |
| `created_at` | text | UTC timestamp `YYYY-MM-DDTHH:MM:SS.ffffffZ`. Text, not a database datetime, so SQLite and PostgreSQL keep the same precision |
| `updated_at` | text | Same format. Last-write-wins compares this value. API and web edits stamp the server clock. A winning sync push keeps the device timestamp |
| `revision` | integer, indexed | Server revision copied from `sync_state` when this row last changed. Pull asks for `revision > since` |
| `deleted_at` | text, null | Soft delete. Null means the voter is active. Reports, search, and export skip rows where this is set. Sync pull includes them as stripped tombstones |

### Tombstones

Deleting a voter (web, REST `DELETE`, or a sync delete) erases the personal data in the same transaction. The row keeps only `id`, `created_at`, `updated_at`, `revision`, `deleted_at`, and `source_id`. All nullable voter columns become null. Three columns are `NOT NULL`, so they hold inert values instead of the original data: `first_name` is `""`, `gender` is `Unknown`, and `voter_id` is the row id without dashes. No schema change or migration is needed. Because the unique index only covers active rows, the original VoterId is free to be used again. Only an admin can delete through the web or REST API.

### Search

Search filters (`q`, `voter_id`, `caste`, `mobile`, and the other filters) are sent in a POST body (`POST /api/voters/search`, or the web search form). They are not accepted in a GET query string, so they do not appear in URLs or access logs. `GET /api/voters` takes only `page` and `page_size`.
| `source_id` | text, null | Who wrote the row last. Web saves use `web:<username>`. Sync push stores the device id |

`sync_state` is a single row (`id = 1`) holding `current_revision`. Every insert, update, import row, and applied sync change increments it by one and stores that value on the voter. The counter starts at 0.

The unique index `uq_voters_voter_id_active` covers `voter_id` only where `deleted_at IS NULL`. A removed EPIC can be entered again. Two active rows cannot share a VoterId. The check is also enforced in the service so the API returns 409 before the database error. The message is meant for the operator: `A voter with VoterId <id> already exists. Each VoterId must be unique; search for the existing voter or use a different VoterId.`

### VoterId is fixed after create

VoterId is the unique key, so it cannot be edited once a voter exists. The web edit page shows it as plain text and does not post it. A web edit post, `PATCH /api/voters/{id}`, or a sync push that carries a different `voter_id` for an existing active record is refused and nothing is written: the web form answers 400, `PATCH` answers 422, and sync reports `validation_error` for that change. Sending the same value is accepted. A soft-deleted row only holds a placeholder `voter_id`, so re-sending a deleted record may carry a VoterId. This holds for every role, including owner and admin. Who may change the other columns is described next.

### Who may edit which field

The rule applies to an **existing** voter and is enforced on the server in `PATCH /api/voters/{id}`, the web edit form and `POST /api/sync/push`. The Android edit screen mirrors it. "Owner/admin" is the `admin` role (the owner is an admin).

| Role | May change |
| --- | --- |
| `user` | **Party, Caste, Mobile, Address** only |
| `admin` / owner | every column except VoterId |
| nobody | VoterId |

- A `user` request that changes any other column is refused and nothing is written: `PATCH` answers **403** (`Your role cannot change: <labels>. Regular users may edit only Party, Caste, Mobile, Address; ask an owner or admin to change other fields.`), the web form re-renders with the same message and status 403, and sync answers that change with `status: "forbidden"` (see `docs/SYNC.md`). The check compares against the stored value, so a client that sends the whole row back with the restricted fields unchanged is not refused.
- The web edit form shows the restricted fields read-only for a `user` (inputs are `readonly`, drop-downs `disabled`). The server does not rely on that: a posted value that differs is refused, and a field that was not posted counts as unchanged.
- Creating a voter is unchanged: any signed-in user can create one and may set every column, including Village, Permanent address and Local. Deleting stays admin-only on the web and REST API, and a `user` also cannot delete through sync (otherwise delete-then-recreate would sidestep the rule).

### House No removal

The separate `house_no` column is gone. Migration `0006_fold_house_no_into_address.py` moves each value to the start of `address` and then drops the column, so no data is lost:

| `house_no` | `address` before | `address` after |
| --- | --- | --- |
| `12` | `Main St` | `H.No 12, Main St` |
| `12` | empty | `H.No 12` |
| empty | anything | unchanged |

CSV import still accepts a `HouseNo` / `House No` / `house_no` column and folds it into Address in the same way, so old files keep working. Export, the web pages, search filters, reports and the Android app no longer have House No. `q` (free-text search) now matches Address and Village instead.

### Booth and Ward rename

`booth_2024` became `booth` and `ward_2026` became `ward`. The JSON API, sync and CSV export use the new names. Writes (create, PATCH, sync push) still accept the old JSON keys `booth_2024` and `ward_2026`, and CSV import accepts the old headers `2024 Booth` and `2026 Ward` as well as the old column names `booth_2024` and `ward_2026`. Using an old and a new name for the same column in one file is rejected. `2024 B.S.No` / `bs_no_2024` is unchanged.

## Users

Accounts are server-managed in phase 1. They are not part of voter sync.

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | text UUID | Primary key |
| `username` | text, unique | 3–32 characters: letters, digits, `.`, `_`, `-` |
| `password_hash` | text | bcrypt hash. The plaintext password is never stored or returned |
| `role` | text | `admin` or `user` |
| `is_active` | boolean | Inactive users cannot sign in |
| `must_change_password` | boolean, default true | Set for new users, the seeded admin, and after an admin password reset. Until cleared, every route except change-password and logout returns 403 `password_change_required`. See `docs/SECURITY.md` |
| `is_owner` | boolean, default false | Only an owner can create or manage users. Set only on the admin from `ADMIN_USERNAME`; never settable through the API |
| `created_at`, `updated_at` | text | UTC timestamps in the same format as voters |

There is no default account in the repository. On startup the app inserts one admin (`is_owner` and `must_change_password` both true) when both `ADMIN_USERNAME` and `ADMIN_PASSWORD` are set and that username does not already exist. If that username already exists, the app sets `is_owner` on it (for databases created before the flag) and leaves the password alone. Restarting does not reset the password.

## Migration

`alembic/versions/0001_initial.py` creates `users`, `sync_state`, and `voters`, including the partial unique index. `alembic/versions/0002_user_security_flags.py` adds `users.must_change_password` (existing rows become true) and `users.is_owner` (existing rows false). `0003_token_version.py` adds `users.token_version` and `users.deleted_at`. `0004_rename_booth_ward.py` renames `voters.booth_2024` to `booth` and `voters.ward_2026` to `ward` with `ALTER TABLE ... RENAME COLUMN`, so every stored value is kept, and recreates their indexes as `ix_voters_booth` and `ix_voters_ward`. Its downgrade renames them back. `0005_add_address_village_local.py` adds `address`, `village` and `permanent_address` (nullable text, so existing rows get null) and `is_local` (`NOT NULL`, server default false, so existing rows become No). `0006_fold_house_no_into_address.py` folds `house_no` into `address` and drops `house_no` (see "House No removal"); its downgrade re-adds an empty `house_no` column and leaves the folded text in `address`. Dropping a column uses `ALTER TABLE ... DROP COLUMN`, which needs SQLite 3.35 or later (PostgreSQL has always had it). The app runs `alembic upgrade head` on startup. You can also run it yourself:

```bash
DATABASE_URL=sqlite:///./data/nathavaram.db alembic upgrade head
```

Check constraints in the database repeat the gender, age, and party rules so a raw SQL write cannot bypass them.

## Age bands used by reports

These are derived. They are not stored.

| Band | Ages |
| --- | --- |
| Under 18 | 1–17 |
| 18–25 | 18–25 |
| 26–35 | 26–35 |
| 36–45 | 36–45 |
| 46–55 | 46–55 |
| 56–65 | 56–65 |
| 66+ | 66–130 |
| Unknown | null age |

Blank text groups are labelled `(blank)` in reports. Party always includes the nine dropdown values plus `(blank)`.
