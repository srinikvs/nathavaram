# Campaign messaging (SMS and WhatsApp)

Replaces the "Coming soon" stubs at `/campaign/sms` and `/campaign/whatsapp`. Linked from the Campaign sidebar section.

## Roles

| Action | user | manager | admin |
| --- | --- | --- | --- |
| View pages, summary, search, detail, preview, target options | yes | yes | yes |
| Create / schedule campaign (Quick Message) | no | yes | yes |
| Pause / resume / cancel / delete | no | yes (own campaigns) | yes (any) |
| Manual `POST /api/campaigns/scheduler/run` | no | no | yes |

Ownership: pause, resume, cancel and delete require the campaign's `created_by` username or an admin. Manager/admin is enforced on the server (`require_manager` / ownership check); hiding the Quick Message button is only a convenience.

## Tables

| Table | Purpose |
| --- | --- |
| `campaigns` | One SMS or WhatsApp campaign: title, message, target geo, send mode, daily IST times, status |
| `campaign_photos` | Metadata per photo (files under the campaign photo directory) |
| `campaign_recipients` | One row per matched voter with a valid mobile; `mobile` stores a **masked** display value only; status `initiated` / `processed` / `sent` / `rejected` |
| `campaign_deliveries` | One delivery attempt per recipient per slot; unique so slots are idempotent |
| `campaign_schedule_runs` | Claimed daily IST slots to prevent double-sends |
| `campaign_idempotency_keys` | Optional `Idempotency-Key` header → campaign id (per user + channel) |

## Targeting

`To` selects a geo type: Ward (primary), Booth, Panchayati (`panchayath`), Mandalam (`mandal`), or Constituency. Preview returns `matched_voters` (all active matches) and `with_mobile` (valid Indian mobiles only). Create uses only `with_mobile` recipients and rejects empties. A configurable `CAMPAIGN_MAX_RECIPIENTS` cap (default 5000) and a per-user create rate limit (`CAMPAIGN_CREATE_RATE_LIMIT`, default 10/hour) apply.

## Photos

Up to 5 photos per campaign (422 if more). Uploads reuse the Public Issues image pipeline. WhatsApp sends every attached image (caption on the first). SMS includes photo links only when `PUBLIC_BASE_URL` is set: those links are signed, expiring HMAC URLs on `/api/campaigns/photos/{id}/public` (domain-separated key `hmac(secret_key, "campaign-photo")`, default TTL 24h). Cancelled campaigns return 410 on public photo links. Auth-gated `/file` URLs are never embedded in SMS.

## Scheduling

- **Send now** — process immediately after create.
- **Schedule daily** — up to 3 times per day in `Asia/Kolkata`, optional start/end dates, active/paused toggle. On the creation day (IST), slots earlier than the creation time are skipped so a 15:30 create does not fire a 09:00 slot the same day. The in-process scheduler claims each due slot once.

## Providers and dry-run

Credentials are environment-only (`WHATSAPP_*`, `SMS_*`). When missing, the dry-run provider records realistic deliveries without external HTTP. `SMS_API_URL` must be HTTPS. The SMS API key is sent only as `Authorization: Bearer …`, never in the JSON body and never duplicated in `X-API-Key`.

## Privacy and retention

`campaign_recipients.mobile` stores only a masked display value (e.g. `******1234`). Send-time always resolves the live mobile from `voters` by `voter_id`. Soft-deleted, hard-deleted, missing, or invalid-mobile voters are marked `rejected` (`voter removed` / `missing valid mobile`) and never messaged. API/UI payloads never expose full mobiles. Provider `detail` / `last_error` text is scrubbed of phone-like digit runs. Hard-delete (including bulk) batch-scrubs stored recipient mobiles to `****`.

## Auth and idempotency

Campaign API and pages use the same session / Bearer middleware as the rest of the app (CSRF for cookie sessions). Create accepts an optional `Idempotency-Key` header so retries return the existing campaign instead of sending duplicates.
