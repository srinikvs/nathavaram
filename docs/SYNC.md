# Sync protocol

Phase 2 is an offline Android app using the same voter fields. It can create and edit rows without a network, then exchange them with this server. Phase 1 implements the server side and the tests. It does not build the APK.

Authentication is the same JWT as the rest of the API. Send `Authorization: Bearer <token>`. Users and passwords are not synced. Each device signs in as a person, then syncs voters.

All timestamps are UTC in the form `YYYY-MM-DDTHH:MM:SS.ffffffZ`. A value without a timezone is read as UTC. `server_time` on every sync response lets the device measure clock skew. Last-write-wins is unsafe when a phone clock is wrong, so the device should warn the operator when it differs from `server_time` by more than a few minutes.

## Identifiers

- The voter primary key is a UUID created on the device for offline inserts. The server accepts that id on create and on sync push.
- `VoterId` (the EPIC-style number) is a separate unique field among active rows.
- `revision` is assigned only by the server. Clients treat it as opaque and store the newest value they have seen.
- `source_id` on an applied push is the `device_id` from the batch.

## Pull

`GET /api/sync/pull?since=<revision>&limit=<1-1000>`

Returns every voter, including soft-deleted rows, whose `revision` is greater than `since`, ordered by `revision` ascending. `limit` defaults to 500.

```json
{
  "server_time": "2026-11-11T21:00:00.000000Z",
  "server_revision": 42,
  "since": 10,
  "has_more": false,
  "changes": [
    {
      "id": "6f1c...",
      "voter_id": "ZZZ0000001",
      "first_name": "Testperson",
      "updated_at": "2026-11-11T18:04:00.000000Z",
      "revision": 11,
      "deleted_at": null
    }
  ]
}
```

`deleted_at` set means the row was removed. The device should delete or tombstone its local copy, not resurrect it. A tombstone carries no personal data: every voter field is null, `first_name` is `""`, `gender` is `Unknown`, and `voter_id` is an inert placeholder (the row id without dashes). Only `id`, `revision`, `created_at`, `updated_at`, `deleted_at`, and `source_id` are meaningful. Do not read `voter_id` from a tombstone. When `has_more` is true, call again with `since` set to the `revision` of the last change in this page. Stop when `has_more` is false. An empty `changes` list with `server_revision` equal to the device cursor means the device is caught up.

## Push

`POST /api/sync/push`

Send the latest snapshot of each changed row, not the intermediate history. A batch may contain up to 500 changes. The HTTP status is 200 when the batch was processed. Each item has its own status, so one bad row does not reject the rest. The server commits the applied rows together.

A non-delete is a full snapshot. Omitted optional fields are stored as null. `voter_id`, `first_name`, and `gender` are required (`gender` defaults to `Unknown` only when it is left out of a normal API create; send it explicitly from the device). A delete sets `deleted` to true and needs `id` and `updated_at`.

```json
{
  "device_id": "android-6f1c",
  "changes": [
    {
      "id": "6f1c2b2e-7a0d-4e1a-9c2a-0b6e1d4a8891",
      "updated_at": "2026-11-11T18:04:00.000000Z",
      "created_at": "2026-11-11T16:00:00.000000Z",
      "deleted": false,
      "base_revision": 10,
      "voter_id": "ZZZ0000001",
      "first_name": "Testperson",
      "surname": "One",
      "gender": "Male",
      "age": 42,
      "party": null
    }
  ]
}
```

`base_revision` is the revision the device last saw for this id. When it is present and the server row's `revision` is greater, the server has changed since the device last synced, so the change is a `conflict` even if the device `updated_at` is newer. Nothing is written and the server row is returned. A device that omits `base_revision` gets plain last-write-wins by `updated_at`. An exact replay of an already-stored change is still `applied` ("Already stored"), whatever `base_revision` says.

Each change in a batch is applied in its own savepoint. A rejected change (conflict, validation error, or a database unique-key clash) never rolls back or blocks the other changes in the batch.

```json
{
  "server_time": "2026-11-11T21:00:01.000000Z",
  "server_revision": 43,
  "results": [
    {
      "id": "6f1c2b2e-7a0d-4e1a-9c2a-0b6e1d4a8891",
      "status": "applied",
      "server_revision": 43,
      "server_record": {}
    }
  ]
}
```

`server_record` is present for `applied`, `conflict`, and `voter_id_conflict` when the server has a row to show. The device should replace its local copy with `server_record` whenever it is present.

## Conflict policy

Last-write-wins by `updated_at`, then a new server revision.

| Situation | Status | What the server does |
| --- | --- | --- |
| No row with this `id`, and the change is not a delete | `applied` | Inserts the snapshot. `created_at` is the device value when it is valid, otherwise `updated_at` |
| No row with this `id`, and the change is a delete | `applied` | No tombstone is written. The message is `No server record; delete ignored`. Send the latest state only; a create that never reached the server does not need a tombstone |
| Incoming `updated_at` is newer than the stored row (and `base_revision` is absent or current) | `applied` | Replaces the data columns, sets `source_id` to `device_id`, and assigns the next revision. A newer non-delete clears `deleted_at` (this undeletes). A newer delete sets `deleted_at` and strips every personal column (see Pull) |
| Incoming `updated_at` is older | `conflict` | Keeps the server row and returns it |
| `base_revision` is lower than the server row's `revision` (and the change is not an exact replay) | `conflict` | Keeps the server row and returns it, for edits and for deletes. The device should show the difference and let the operator re-apply |
| Two changes reach the database with the same active `voter_id` (for example a concurrent insert) | `voter_id_conflict` | Only that change is rejected; the rest of the batch is stored |
| Equal `updated_at` and the same content, including the deleted flag | `applied` | Idempotent replay. The revision does not move |
| Equal `updated_at` and different content | `conflict` | The server row wins, so two devices that stamp the same instant cannot flap |
| Active different `id` already uses this `voter_id` | `voter_id_conflict` | Nothing is written. `server_record` is the other active row. The device should show that row and not silently merge the two people |
| `updated_at` before 2000-01-01 UTC, more than 5 minutes ahead of the server, or otherwise invalid | `validation_error` | Nothing is written for that item |

`voter_id` uniqueness ignores soft-deleted rows. After a delete is synced, another row may use that EPIC.

The server clock wins for edits made through the website and the REST CRUD API: those set `updated_at` to the current server time and therefore beat an offline edit that was stamped earlier. An offline edit stamped later than the server edit wins on the next push. That is ordinary last-write-wins. It is not a field-level merge. If two people edit different columns of the same voter, the older edit disappears.

## What the Android client should do

1. Sign in and store the JWT.
2. Keep a cursor, initially 0.
3. On connectivity: push local rows whose local dirty flag is set, using each row's UUID and its last local `updated_at`.
4. For every result with `server_record`, replace the local row and clear the dirty flag. For `voter_id_conflict` and `conflict`, keep the server row and show the operator the difference.
5. Pull with `since` = cursor until `has_more` is false. Apply changes, including tombstones, unless the local row is still dirty and its `updated_at` is newer than the pulled one (it still needs to be pushed).
6. Store `server_revision` as the cursor only after the page is applied. When a page is non-empty, the next `since` can be the last change's `revision`.
7. Use SQLite/Room with the same snake_case columns, UUID primary key, `updated_at`, `revision`, `deleted_at`, and `source_id`.

Phase 2 still has to build the Room database, the background sync worker, the offline UI, and the APK. The HTTP contract above is the part this server already accepts.

## Android client

The client lives in [`android/`](../android/README.md). One detail goes beyond the steps above: when a pulled change is skipped because a dirty local row is newer, the client still raises that row's stored `revision` to the pulled one, so its next push carries a current `base_revision` and wins by `updated_at` instead of being rejected as a stale `conflict`.
