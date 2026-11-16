# Gmail setup (AdSpot mailbox)

AgentCo polls the AdSpot Gmail inbox every 2 minutes (works for both Workspace and personal Gmail). Push via Pub/Sub is a later optimisation.

1. Google Cloud Console -> new project -> **APIs & Services -> Library -> Gmail API -> Enable**.
2. **OAuth consent screen**:
   * Workspace account: choose **Internal**. Refresh tokens do not expire.
   * Personal Gmail: choose **External**, add yourself as a test user, then **Publish app** (status "In production"). If you leave it in "Testing", Google expires the refresh token after 7 days. An unverified app shows a warning screen for your own account; that is fine for single-owner use.
3. **Credentials -> Create OAuth client ID -> Web application**. Add `https://developers.google.com/oauthplayground` as an authorised redirect URI.
4. Open the OAuth Playground (settings cog: tick **Use your own OAuth credentials**, paste the client ID and secret). Authorise these two scopes only:
   * `https://www.googleapis.com/auth/gmail.modify` (read, label, create drafts)
   * `https://www.googleapis.com/auth/gmail.send`
   Sign in as the AdSpot mailbox, then **Exchange authorization code for tokens** and copy the **refresh token**.
5. Set on Railway (api + worker services): `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`.

What the app does with it:
* Customer Desk: reads new inbox mail, classifies it, labels it `AgentCo/<category>`, creates Gmail **drafts** for enquiries and complaints. It has no send tool.
* Mail leaves the mailbox only through `agentco/mailer.py`: customer payment/receipt mails (customer's booking email only, 30-second undo) and publication mails (addresses from your publications table only, only when you press Send or say "send").
* Replies you want to send from a Gmail draft are sent by you in Gmail itself.
* Message-ID, thread id and booking ref are logged for every mail in and out.

Revoke access any time at https://myaccount.google.com/permissions.
