# Environment variables (Railway)

Set on **both** the `api` and `worker` services (use Railway "shared variables"). Names are exact.
Railway sets `PORT` itself. `DATABASE_URL` comes from the Postgres plugin (reference it as `${{Postgres.DATABASE_URL}}`).

## Required to start
| Variable | Value / where it comes from |
|---|---|
| `DATABASE_URL` | AgentCo's own Railway Postgres (plugin reference) |
| `SESSION_SECRET` | long random string: `python -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `OWNER_EMAIL` | your Google account email (the only account allowed to sign in) |
| `GOOGLE_CLIENT_ID` | Google Cloud > Credentials > OAuth client (Web). Add the Railway URL as an authorised JavaScript origin |
| `GEMINI_API_KEY` | Google AI Studio paid API key |
| `GEMINI_MODEL_FLASH` / `GEMINI_MODEL_PRO` | current model ids (defaults exist; names change, so check) |
| `ADSPOT_RO_DATABASE_URL` | URL of the SELECT-only role on AdSpot's Postgres (README step 4) |
| `SIDADIYA_RO_DATABASE_URL` | URL of the SELECT-only role on Sidadiya's Postgres |

## Required for mail (Phase 2)
| Variable | Value |
|---|---|
| `GMAIL_CLIENT_ID` | OAuth client id (docs/gmail-setup.md) |
| `GMAIL_CLIENT_SECRET` | OAuth client secret |
| `GMAIL_REFRESH_TOKEN` | refresh token for the AdSpot mailbox (scopes gmail.modify + gmail.send) |

Without these three, no mail is read or sent; bookings still sync and drafts still appear.

## Required for YouTube Shorts uploads
| Variable | Value |
|---|---|
| `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET` | OAuth client with the YouTube Data API v3 enabled (docs/youtube.md) |
| `YOUTUBE_REFRESH_TOKEN_ADSPOT` | refresh token for the AdSpot channel (scope `youtube.upload`) |
| `YOUTUBE_REFRESH_TOKEN_SIDADIYA` | refresh token for the Sidadiya channel |

Without them you can still make and download Shorts; the Upload button is disabled with a message.

## Optional: AI voice and automatic clips
| Variable | Default | Notes |
|---|---|---|
| `GEMINI_TTS_MODEL` | empty | enables "Voice: AI (Gemini)". Also `GEMINI_TTS_VOICE` (default `Kore`) and `GEMINI_TTS_PRICE_PER_1K_CHARS_USD` (0.02, check it) |
| `VEO_MODEL` | empty | enables "Clips: Veo (auto, paid)". Also `VEO_PRICE_PER_SECOND_USD` (0.40, check it). About $3 per 8 s clip: raise the Video Producer budget |
| `YOUTUBE_MAX_UPLOADS_PER_DAY` | 5 | API quota guard |
| `YOUTUBE_SYNTHETIC_DISCLOSURE` | true | mark uploads as containing AI-generated media |
| `MAX_VIDEO_MB` | 100 | largest clip you can upload |

## Recommended
| Variable | Default | Notes |
|---|---|---|
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` | empty | phone push notifications. Generate with `npx web-push generate-vapid-keys` |
| `VAPID_SUBJECT` | `mailto:owner@example.com` | set to `mailto:` your email |
| `GEMINI_PRICE_FLASH_IN/OUT`, `GEMINI_PRICE_PRO_IN/OUT` | 0.30 / 2.50 / 1.25 / 10.0 | USD per 1M tokens. Budgets are computed from these, so check Google's current prices |
| `DEFAULT_AGENT_BUDGET_USD` | 5 | monthly budget given to each new agent. Change per agent in the app |
| `UPLOAD_DIR` | `uploads` | point at a Railway **volume** (e.g. `/data/uploads`) or uploads vanish on redeploy |

## Optional
| Variable | Default | Notes |
|---|---|---|
| `ADSPOT_WRITE_DATABASE_URL` | empty | column-limited write role so AdSpot's booking shows paid (README step 5). Empty: AgentCo keeps its own record and reminds you |
| `GEMINI_IMAGE_MODEL` | empty | set to use "Gemini (auto)" images; empty = Google Flow manual |
| `GEMINI_IMAGE_PRICE_USD` | 0.04 | per image; check current price |
| `GMAIL_LABEL` | `AgentCo` | Gmail label prefix |
| `UNDO_WINDOW_SECONDS` | 30 | undo time for customer mails |
| `SESSION_TTL_MIN` | 120 | login session length |
| `COOKIE_SECURE` | true | keep true on Railway (HTTPS) |
| `MAX_UPLOAD_MB` | 10 | chat/receipt upload limit |
| `TIMEZONE` | `Asia/Colombo` | daily report time zone (reports run 07:30) |
| `LLM_PROVIDER` | `gemini` | `fake` is for tests only: never set it in production |
| `OMNIROUTE_ENABLED` | false | reserved, not built |

## Not variables, but needed
* Two SELECT-only Postgres roles (and optionally one write role) on the live apps: SQL is in README.
* Google Cloud project with Gmail API enabled and an OAuth consent screen (published, if personal Gmail).
* Custom domain later: add it in Railway, then add it to the Google OAuth authorised origins.

## Do NOT add
No bank, HelaPay, OnePay, PayHere, Meta, Facebook, Firebase or Postiz credentials, and no Google Flow login or password (Flow has no API; you upload its clips by hand). AgentCo never needs them.
