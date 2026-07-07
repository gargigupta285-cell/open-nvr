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
  "agent": "Camera Agent"
}
```

Delivery is best-effort and non-blocking: a webhook that errors or times out
is logged and skipped — it never stalls detection.

## Everything else: Apprise (email, Telegram, phone push, SMS, …)

For targets that don't take a JSON webhook, the agent speaks
[Apprise](https://github.com/caronc/apprise) — 100+ services through one
**optional** dependency (`pip install apprise`) and one URL per service:

```yaml
# config.yml
notify_apprise:
  - mailto://user:app-password@gmail.com          # email (SMTP)
  - tgram://123456:ABC-bot-token/987654           # Telegram
  - ntfy://opennvr-alerts                         # phone push (self-hostable)
  - pover://user-key@app-token                    # Pushover
```

Same `notify_events` filter, same best-effort contract as the webhooks, and
delivery runs on a worker thread so an SMTP handshake never stalls a poll
loop. If URLs are configured but the package isn't installed, they no-op
with a single log warning — the agent never fails to boot over a
notification channel. Apprise URLs embed tokens: keep `config.yml`
permissions tight; the agent logs only a rejected URL's scheme, never the
full URL.

## Endpoints
- `GET /notify` → `{enabled, channels, webhooks, apprise, events, recent}`
  (status + recent deliveries)
- `POST /notify/test` → send a test event to every configured channel

## Roadmap — richer channels (not yet built)

- **Emergency calling** — a *placed phone call* on alarm trigger (pairs with
  the emergency-calling hook in `ALARMS.md`). Twilio Voice / SIP; keep
  provider secrets server-side; rate-limit; per-alarm opt-in. (SMS itself
  already works today via Apprise's `twilio://`.)
- **Per-rule routing** — send a given alarm/watch to a specific channel, and
  set quiet hours / severity thresholds per channel.

These are intentionally deferred; the webhook + Apprise layer is the generic
foundation they'd build on.
