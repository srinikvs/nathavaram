# Campaign messaging (SMS and WhatsApp)

Replaces the "Coming soon" stubs at `/campaign/sms` and `/campaign/whatsapp`. Linked from the Campaign sidebar section.

Signed-in users (user, manager, admin) manage campaigns at those pages. The JSON API is under `/api/campaigns`.

## Tables (migration 0012, additive)

| Table | Purpose |
| --- | --- |
| `campaigns` | One SMS or WhatsApp campaign: title, message, target geo, send mode, daily IST times, status |
| `campaign_photos` | Metadata per photo (files under the campaign photo directory) |
| `campaign_recipients` | One row per matched voter with a valid mobile; status `initiated` / `processed` / `sent` / `rejected` |
| `campaign_deliveries` | One delivery attempt per recipient per slot (`now` or `YYYY-MM-DDTHH:MM`); unique so slots are idempotent |
| `campaign_schedule_runs` | Claimed daily IST slots (`campaign_id`, `run_date`, `run_time`) to prevent double-sends |

## Targeting

`To` selects a geo type: Ward (primary), Booth, Panchayati (`panchayath`), Mandalam (`mandal`), or Constituency. One or more values from the voter list. Only active voters with a valid Indian mobile are included. The Quick Message modal shows the live matched count.

## Photos

Up to 5 photos per campaign. Uploads reuse the Public Issues image pipeline (JPG/PNG/HEIC, resize, strip metadata). A sixth photo is rejected with 422. WhatsApp attaches media; SMS appends photo link(s) to the body.

## Scheduling

- **Send now** — process immediately after create.
- **Schedule daily** — up to 3 times per day in `Asia/Kolkata`, optional start/end dates, active/paused toggle. The in-process scheduler claims each due slot once.

## Providers and dry-run

Credentials are environment-only (`WHATSAPP_*`, `SMS_*`). When they are missing, the dry-run provider records realistic `processed` → `sent` deliveries without any external HTTP call. KPI cards count recipient statuses for the channel.

## Privacy

Recipient mobiles are masked to the last 4 characters in every API and UI payload (`mask_campaign_mobile`, same rule as Public Issues). Stored values stay intact.

## Auth

Campaign API and pages require a signed-in user (same session / Bearer middleware as the rest of the app, including CSRF for cookie sessions).
