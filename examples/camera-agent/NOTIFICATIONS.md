# External notifications

The in-UI siren and chimes only reach you while the demo page is open. For
real use, alarms and watch alerts should reach you when the tab is closed —
that's what external notifications do.

The agent fans events out to one or more **webhook URLs**. A single JSON POST
works with Slack and Discord incoming webhooks, Microsoft Teams, n8n / Home
Assistant automations, and any custom endpoint — the payload carries both
`text` (Slack) and `content` (Discord) plus structured fields.

## Configure

```yaml
# config.yml
notify_webhooks:
  - https://hooks.slack.com/services/XXX/YYY/ZZZ
  - https://discord.com/api/webhooks/123/abc
  - http://homeassistant.local:8123/api/webhook/opennvr   # any JSON consumer
notify_events: [alarm, notify]   # categories to send; default [alarm, notify]
```

Event categories:
- `alarm` — an armed alarm fired (severity `critical`)
- `notify` — a watch/monitor saw its target (severity `info`)
- `task` — a background task finished (add to `notify_events` to receive these)

## Payload

```json
{
  "text": "Fire: fire detected on cam1",
  "content": "Fire: fire detected on cam1",
  "type": "alarm",
  "title": "Fire",
  "detail": "fire detected on cam1",
  "camera": "cam1",
  "severity": "critical",
  "ts": 1781800000.0,
  "agent": "Sidhu"
}
```

Delivery is best-effort and non-blocking: a webhook that errors or times out
is logged and skipped — it never stalls detection.

## Endpoints
- `GET /notify` → `{enabled, channels, events, recent}` (status + recent deliveries)
- `POST /notify/test` → send a test event to every configured webhook

## Roadmap — richer channels (not yet built)

Webhooks cover most automation/chat targets today. Planned first-class channels:

- **SMS / phone** via Twilio (pairs with the emergency-calling hook in
  `ALARMS.md`). Keep provider secrets server-side; rate-limit; per-alarm opt-in.
- **Mobile push** (FCM / APNs / ntfy.sh) for a phone app or PWA.
- **Email** (SMTP) digests for non-urgent summaries.
- **Per-rule routing** — send a given alarm/watch to a specific channel, and
  set quiet hours / severity thresholds per channel.

These are intentionally deferred; the webhook layer is the generic foundation
they'd build on.
