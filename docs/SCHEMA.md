# Nathavaram voter schema

Phase 1 stores the village list in a relational database. `voter_id` is the primary key of the `voters` table. SQLite is the default. The same SQLAlchemy models and this Alembic migration run on PostgreSQL when `DATABASE_URL` uses the `postgresql+psycopg://` driver.

Display names below are the exact labels in the web form and the exact CSV/Excel headers. The database and JSON API use the snake_case names.

## Voters

| Display name | Column | Type | Rules |
| --- | --- | --- | --- |
| Booth | `booth` | text, null | Up to 64 characters. Indexed |
| Booth S.No | `booth_s_no` | text, null | Up to 64 characters. Formerly "2024 B.S.No" / `bs_no_2024` |
| Ward | `ward` | text, null | Up to 64 characters. Indexed |
| Ward S.No | `ward_s_no` | text, null | Up to 64 characters |
| VoterId | `voter_id` | text, **primary key** | 3–32 letters or digits, stored uppercase. Entered when the voter is created and **read-only afterwards** (see below) |
| FirstName | `first_name` | text, required | Up to 128 characters. Telugu and other Unicode letters are stored as UTF-8 |
| LastName | `last_name` | text, null | Up to 128 characters. Formerly "SurName" / `surname` |
| RelativeName | `relative_name` | text, null | Up to 128 characters |
| HouseNo | `house_no` | text, null | Up to 64 characters |
| Gender | `gender` | text, null | Empty means unknown or not recorded. Otherwise exactly one of `Male`, `Female`, `Other` (column is `VARCHAR(16)` with a `CHECK`). Matching is case-insensitive (`male`, `MALE`) and stored in this canonical form; any other value is refused with 422. Existing voters stay empty: nothing is guessed or backfilled. Only owner/admin can change it on an existing voter (like Age) |
| Age | `age` | integer, null | Whole number from 1 to 130, or null when unknown |
| Street | `street` | text, null | Up to 1000 characters. Formerly "Address" / `address` |
| Mobile | `mobile` | text, null | Stored exactly as entered (trimmed), including a leading `0` or `+91`. Validation is loose: after removing spaces, hyphens, and an optional `+91`, `91`, or `0` prefix, the remaining value must be a 10-digit Indian mobile starting with 6–9 |
| Caste | `caste` | text, null | Up to 128 characters |
| Village | `village` | text, null | Up to 128 characters |
| Panchayath | `panchayath` | text, null | Up to 128 characters |
| Mandal | `mandal` | text, null | Up to 128 characters |
| Current City | `current_city` | text, null | Up to 128 characters. Formerly "C_City" / `c_city` |
| Local/Non-Local | `is_local` | boolean, required, default `true` (Local) for new voters | Shown as **Local** or **Non-Local** in the web form, list, CSV and Excel. The JSON API uses `true` / `false`. Import accepts Local / Non-Local (also Yes/No, Y/N, true/false, 1/0); a blank cell or a missing column means Local, and an explicit Non-Local stays Non-Local. The database `server_default` is still `false` and nothing is backfilled, so rows saved before this change keep their stored value |
| Party | `party` | text, null | Empty means no answer. Otherwise one of: `TDP`, `YSRCP`, `Jana Sena`, `BJP`, `INC`, `CPI`, `Independent`, `Unknown`, `Prefer not to say`. Matching is case-insensitive and stored in this canonical form |
| Constituency | `constituency` | text, null | Up to 128 characters. Last column. Only owner/admin can change it on an existing voter |

Permanent address and Profession were removed (migration `0007`); their values are not kept. Gender was dropped by `0007` too (its old values are not restored) and came back as the new optional column above (migration `0009`).

The voter list, CSV and Excel use these 21 columns in exactly the order above (the web list adds an Actions column after them).

Text columns reject ASCII control characters. Names are not restricted to a single script, so Telugu text round-trips through the API, the form, and CSV.

## Sync columns

These columns are not part of the paper list. They exist so an offline Android client can sync in phase 2 without a schema change.

| Column | Type | Meaning |
| --- | --- | --- |
| `created_at` | text | UTC timestamp `YYYY-MM-DDTHH:MM:SS.ffffffZ`. Text, not a database datetime, so SQLite and PostgreSQL keep the same precision |
| `updated_at` | text | Same format. Last-write-wins compares this value. API and web edits stamp the server clock. A winning sync push keeps the device timestamp |
| `revision` | integer, indexed | Server revision copied from `sync_state` when this row last changed. Pull asks for `revision > since` |
| `deleted_at` | text, null | Soft delete. Null means the voter is active. Reports, search, and export skip rows where this is set. Sync pull includes them as stripped tombstones |
| `source_id` | text, null | Who wrote the row last. Web saves use `web:<username>`. Sync push stores the device id |

There is no separate row id: `voter_id` identifies the row in the REST paths (`/api/voters/{voter_id}`, matched case-insensitively), in sync, and in the web URLs.

### Tombstones

Deleting a voter (web, REST `DELETE`, or a sync delete) erases the personal data in the same transaction. The row keeps only `voter_id` (its primary key), `created_at`, `updated_at`, `revision`, `deleted_at`, and `source_id`. All nullable voter columns become null; `first_name` is `""` (it is `NOT NULL`) and `is_local` is false. **The VoterId itself is kept**, because it is the key that sync and the REST API need to refer to the deleted row; it is no longer erased as it was before migration `0007`. Adding a voter with the same VoterId later (web, REST, CSV import or a newer sync push) **revives** the tombstone row in place: it gets a new `created_at`, `deleted_at` is cleared and the revision moves. Only an admin can delete through the web or REST API.

### Search

Search filters (`q`, `voter_id`, `caste`, `mobile`, and the other filters) are sent in a POST body (`POST /api/voters/search`, or the web search form). They are not accepted in a GET query string, so they do not appear in URLs or access logs. `GET /api/voters` takes only `page` and `page_size`.
| `source_id` | text, null | Who wrote the row last. Web saves use `web:<username>`. Sync push stores the device id |

`sync_state` is a single row (`id = 1`) holding `current_revision`. Every insert, update, import row, and applied sync change increments it by one and stores that value on the voter. The counter starts at 0.

Because `voter_id` is the primary key, two rows can never share a VoterId. Creating one that belongs to an active voter returns 409 (the service checks first so the API answers before any database error): `A voter with VoterId <id> already exists. Each VoterId must be unique; search for the existing voter or use a different VoterId.` CSV import skips such a row and reports `VoterId <id> already exists`. Excel (.xlsx) import goes through the same code path (`app/services/impex.py`, `_import_table`) and gives the same skip, message and report; it reads the first sheet only and stringifies numeric cells without a trailing `.0`.

The web list remembers the last search so that Back returns to it. The filters and scroll position are kept in the browser's sessionStorage (`app/static/voter-list-context.js`), not in the URL or the server logs. The list entry is rewritten to `GET /voters`, and a return from a voter's details page re-posts the saved filters once. Clear forgets them. A fresh visit to `/voters` is unfiltered.

### Voter details (web)

`GET /voters/{voter_id}` shows every voter field, read-only. Its Edit button opens the existing `/voters/{voter_id}/edit` form, which keeps the role rules in "Who may edit which field". A missing or soft-deleted VoterId returns 404 with the "not on the active list" page; the details URL has no query string.

### VoterId is fixed after create

VoterId is the primary key, so it cannot be edited once a voter exists. The web edit page shows it as plain text and does not post it. A web edit post or `PATCH /api/voters/{voter_id}` that carries a different `voter_id` is refused and nothing is written: the web form answers 400 and `PATCH` answers 422. Sending the same value is accepted. In sync a different `voter_id` is simply a different record (see `docs/SYNC.md`). This holds for every role, including owner and admin. Who may change the other columns is described next.

### Who may edit which field

The rule applies to an **existing** voter and is enforced on the server in `PATCH /api/voters/{voter_id}`, the web edit form and `POST /api/sync/push`. The Android edit screen mirrors it. "Owner/admin" is the `admin` role (the owner is an admin).

| Role | May change |
| --- | --- |
| `user` | **Party, Caste, Mobile, HouseNo, Street** only |
| `admin` / owner | every column except VoterId |
| nobody | VoterId |

- A `user` request that changes any other column is refused and nothing is written: `PATCH` answers **403** (`Your role cannot change: <labels>. Regular users may edit only Party, Caste, Mobile, HouseNo, Street; ask an owner or admin to change other fields.`), the web form re-renders with the same message and status 403, and sync answers that change with `status: "forbidden"` (see `docs/SYNC.md`). The check compares against the stored value, so a client that sends the whole row back with the restricted fields unchanged is not refused.
- The web edit form shows the restricted fields read-only for a `user` (inputs are `readonly`, drop-downs `disabled`). The server does not rely on that: a posted value that differs is refused, and a field that was not posted counts as unchanged.
- Creating a voter is unchanged: any signed-in user can create one and may set every column, including Village, Panchayath, Mandal and Local/Non-Local. Deleting stays admin-only on the web and REST API, and a `user` also cannot delete through sync (otherwise delete-then-recreate would sidestep the rule).

### History of the column names

- `booth_2024` / `ward_2026` became `booth` / `ward` (migration `0004`). Writes (create, PATCH, sync push) still accept the old JSON keys `booth_2024` and `ward_2026`, and CSV import accepts the old headers `2024 Booth` and `2026 Ward`. Using an old and a new name for the same column in one file is rejected.
- `house_no` was folded into `address` in migration `0006` and is a real column again from `0007`, which splits a leading `H.No <n>,` back out of the old address into `house_no` and keeps the rest as `street`.
- CSV import still accepts the earlier headers `SurName`, `Address`, `C_City`, `2024 B.S.No`, `Ward S No`, `Local`, `House No`, and the internal column names (`house_no`, `booth_s_no`, ...). The removed `Permanent address` and `Profession` columns are ignored if a file still has them. `Gender` / `gender` is a real column: a valid value (Male, Female, Other, any case) is stored, a blank cell leaves it empty, an invalid value fails that row, and a file without a Gender column imports with Gender empty. Only VoterId and FirstName are required.

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

`alembic/versions/0001_initial.py` creates `users`, `sync_state`, and `voters`, including the partial unique index. `alembic/versions/0002_user_security_flags.py` adds `users.must_change_password` (existing rows become true) and `users.is_owner` (existing rows false). `0003_token_version.py` adds `users.token_version` and `users.deleted_at`. `0004_rename_booth_ward.py` renames `voters.booth_2024` to `booth` and `voters.ward_2026` to `ward` with `ALTER TABLE ... RENAME COLUMN`, so every stored value is kept, and recreates their indexes as `ix_voters_booth` and `ix_voters_ward`. Its downgrade renames them back. `0005_add_address_village_local.py` adds `address`, `village` and `permanent_address` (nullable text, so existing rows get null) and `is_local` (`NOT NULL`, server default false, so existing rows become No). `0006_fold_house_no_into_address.py` folds `house_no` into `address` and drops `house_no` (see "House No removal"); its downgrade re-adds an empty `house_no` column and leaves the folded text in `address`. Dropping a column uses `ALTER TABLE ... DROP COLUMN`, which needs SQLite 3.35 or later (PostgreSQL has always had it). `0007_voter_id_primary_key.py` rebuilds the `voters` table (copy, drop, rename) so `voter_id` becomes the primary key: it removes `id`, renames `bs_no_2024`, `surname` and `c_city` to `booth_s_no`, `last_name` and `current_city`, turns `address` into `street` plus `house_no`, adds `panchayath` and `mandal`, drops `gender`, `permanent_address` and `profession` (**their data is not kept**), and drops `uq_voters_voter_id_active`. Rows that were already soft-deleted keep the placeholder `voter_id` they had. Its downgrade rebuilds the old shape with a new UUID per row, `house_no` folded back into `address`, gender `Unknown`, and empty permanent address and profession. `0008_add_constituency.py` adds `constituency` (nullable text, so existing rows get null); its downgrade drops it. `0009_add_gender.py` adds `gender` (nullable `VARCHAR(16)` with a `CHECK` allowing null, `Male`, `Female` or `Other`; no default, so existing rows stay null and no data is invented); its downgrade drops the column and its values. **Back up the database before upgrading.** The app runs `alembic upgrade head` on startup. You can also run it yourself:

```bash
DATABASE_URL=sqlite:///./data/nathavaram.db alembic upgrade head
```

Check constraints in the database repeat the age, party and gender rules so a raw SQL write cannot bypass them.

## Master data (admin)

No new tables and no migration. `GET /api/master-data` and `GET /api/master-data/{entity}` (and the `/master-data` page) are admin-only and read-only. Each list is a `GROUP BY` over active (not soft-deleted) voters, with blank values grouped as `(blank)` and blank children not counted:

| Tab | Grouped by | Counts |
| --- | --- | --- |
| `constituency` | `constituency` | mandals, panchayaths, voters |
| `mandalam` | `mandal`, `constituency` | panchayaths, villages, voters |
| `panchayathi` | `panchayath`, `mandal` | villages, booths, voters |
| `booth` | `booth` | wards, streets, voters |
| `ward` | `ward` | booths, streets, voters |
| `street` | `street`, `ward` | booths, voters |
| `caste` | `caste` | voters |
| `profession` | none | `available: false`. Profession is not a voter column (dropped in `0007`) |
| `city` | `current_city` | local voters, voters |

An unknown entity returns 404; a non-admin gets 403. Voter sync is unaffected.

### Bulk edit and delete of master values (admin)

There is still no catalog, so a row is just "the value(s) active voters currently carry".

- `POST /api/master-data/{entity}/rename` with `{"items": [{<key columns>: value}, ...], "new_value": "..."}` writes `new_value` to the tab's **primary** column (the first column in the table above) on every active voter that matches a selected row, bumping `revision`, `updated_at` and `source_id` like a single edit. The new value goes through the same text rules as the voter field (trimmed, length limit, not blank). Several selected rows are merged into the one new value.
- Composite rows (`mandalam`, `panchayathi`, `street`) match on **all** their key columns, but only the first column is rewritten. Renaming street `Main` in ward `3` leaves street `Main` in ward `4` alone, and ward is never touched.
- A `(blank)` key matches only voters whose value is actually empty. Real values are never overwritten by it.
- A selected row that no active voter has returns 404 and nothing is applied (all-or-nothing). `profession` returns 409 (unavailable).
- `POST /api/master-data/{entity}/delete` with `{"items": [...]}` is a guard, not a delete: if any active voter still uses a selected value it answers 409 with the in-use count and changes nothing. If every value is unused it answers 200 `{"in_use": 0, "unused": N}`; there is nothing to remove from a derived list. Voter fields are never cleared to make a row disappear.
- Both endpoints are admin-only (401 anonymous, 403 for the `user` role). The `/master-data` page offers them as two-step forms (`POST /master-data/bulk-edit`, `POST /master-data/bulk-delete`).

## Bulk voter edit and delete (admin)

`POST /api/voters/bulk-edit` takes `{"voter_ids": [...], "values": {<field>: value}}` and applies the one set of values to every named active voter. Fields come from the voter columns except `voter_id`; `created_at`, `updated_at`, `revision`, `deleted_at` and `source_id` are not data fields and are refused with 422, as is `voter_id`. Values are validated by the same `VoterPatch` rules as `PATCH /api/voters/{voter_id}` (party and gender lists, age 1-130, mobile, text limits). `is_local` blank or null becomes Local; an explicit `false` / `Non-Local` stays false; if `is_local` is not in `values` the stored value is unchanged. At most 1000 ids per request. The whole request is one transaction: an invalid value or an unknown/removed voter fails it with nothing applied, and only the named ids change.

`POST /api/voters/bulk-delete` takes `{"voter_ids": [...], "confirm": true}`. Without `confirm: true` it answers 400 and deletes nothing. With it, each voter is soft-deleted exactly like `DELETE /api/voters/{voter_id}` (tombstone, new revision). The web list does this in two steps: `POST /voters/bulk-delete` shows the count, and only a second post with `confirm=yes` deletes.

Both endpoints use `require_admin`; the checkboxes and action bar are rendered only for admins, but the server enforces the role.

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
