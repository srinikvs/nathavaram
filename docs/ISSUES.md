# Public Issues

Replaces the "Coming soon" stub that used to sit at `/campaign/public-issues`. Linked from `docs/SCHEMA.md`.

Signed-in users (user, manager, admin) track public issues at `/issues`. The JSON API is under `/api/issues`.

## Tables (migration 0011, additive)

| Table | Purpose |
| --- | --- |
| `public_issues` | One row per issue: `voter_id` (nullable snapshot), `name`, `mobile`, `subject`, `issue`, `status`, timestamps, `created_by` / `updated_by` (usernames) |
| `public_issue_status_events` | Append-only history: `from_status` (NULL on creation), `to_status`, `changed_by`, `changed_at` |
| `public_issue_photos` | Metadata per photo: `storage_name`, `width`, `height`, `uploaded_by`, `uploaded_at` |
| `public_issue_notes` | Notes thread. `kind` is `note` (user text, never edited) or `system` (status change line) |

Voter rows are never written by this feature. Child rows cascade on delete.

## Status

Labels: `Initiated`, `In Process`, `Resolved`, `Rejected`. New issues always start as `Initiated`.

| Role | Allowed changes |
| --- | --- |
| user | `Initiated` to `In Process` only. Anything else (including reopening) is 403 |
| manager, admin | Any status, including reopening to an earlier one |

Rules live in `app/issue_rules.py` and are checked in `app/services/issues.py`. The dropdown only shows legal options, but the server is the guard. Setting the status the issue already has is 409.

Each change appends a history row and a notes system line, for example `Initiated to In Process by <username>`.

## Contact fields

- If `voter_id` matches an **active** voter (not soft-deleted, not hard-deleted), `name` (first + last) and `mobile` are copied from that voter. The copy does not change later.
- Otherwise `voter_id` is stored as NULL and the typed `name` and `mobile` are used. Name, Subject and Issue are required; Mobile must pass the same Indian mobile check as voters.
- Issue mobile is **masked on output**: the issues list, the issue detail page, and every issues API response (create, get, search, status change) show only the last 4 characters, for example `******0012`. A value shorter than 4 characters is fully masked. The full number is stored unchanged in `public_issues.mobile`. The masking happens in `issue_payload` (`mask_issue_mobile`), so the web pages and the JSON API share one rule.
- Voter lookup (`POST /api/issues/voter-lookup`) still returns the voter's full mobile so the Add issue form can fill it in. Voter pages are not masked.
- Issues are not part of the Android sync, so the masking does not change sync.

## Roles

| Action | user | manager | admin |
| --- | --- | --- | --- |
| Look up, create, search, view | yes | yes | yes |
| Status (see above) | Initiated to In Process | any | any |
| Add note | yes | yes | yes |
| Delete note | no | no | yes |
| Add photo | yes | yes | yes |
| Remove photo | no | yes | yes |
| Delete issue (with photos, notes, history) | no | yes | yes |

## Photos

- Up to 5 per issue. The 6th upload is 409.
- The Add issue form (`/issues/new`) takes optional photos too (up to 5, chosen with the Photos field, with previews before Save). Every photo is checked before the issue is saved: a bad or oversized photo, or more than 5, re-shows the form with the error and creates no issue. The photos are stored exactly as the issue-detail upload stores them.
- Accepted: JPG, PNG, HEIC. HEIC needs `pillow-heif`; without it HEIC is refused with 415 and JPG/PNG still work.
- Every upload is re-encoded as JPEG: the long edge is capped at 1600 px (smaller images are not enlarged), and EXIF, GPS and other metadata are dropped.
- Where photos go, in order:
  1. An explicit `ISSUE_PHOTO_DIR` (env or `.env`) always wins. A relative path resolves against the project root; an absolute path is used as-is.
  2. Otherwise, if `DATABASE_URL` is a file-based SQLite database (for example `sqlite:////var/lib/nathavaram/nathavaram.db`), photos go in `issue-photos` beside the database file (for example `/var/lib/nathavaram/issue-photos`). This keeps photos in the service-owned data directory, outside a root-owned app tree.
  3. Otherwise (Postgres, `sqlite:///:memory:`, or other non-file URLs), the default is `data/issue-photos` under the project root, which is gitignored.
- The photo directory is created at startup, so the service user needs write access to its parent.
- Files are never under `app/static`. The only way to fetch one is `GET /api/issues/photos/{id}/file`, which requires sign-in (401 otherwise).

## Endpoints

- Web: `/issues`, `/issues/new`, `/issues/{id}`, and POST forms for status, delete, photos, notes. Cookie requests need the session CSRF token.
- API (`/api/issues`): `POST /voter-lookup`, `POST /search`, `POST ""`, `GET /{id}`, `POST /{id}/status`, `DELETE /{id}`, `POST /{id}/photos`, `DELETE /{id}/photos/{photo_id}`, `GET /photos/{photo_id}/file`, `POST /{id}/notes`, `DELETE /{id}/notes/{note_id}`.
- VoterIds and search text travel in POST bodies, never in URLs.
