# YouTube Shorts

Content > **YouTube Shorts** (or chat: "make a short for AdSpot about booking classified ads online").

```
topic -> Scriptwriter: 5 hooks scored by the youtube-agent-skill hook tool -> best hook -> beats + on-screen text + picture prompts
      -> claims check against your brand facts
you review/edit the script, approve
      -> voiceover (Gemini TTS, optional) + pictures: you upload clips from Google Flow (or Veo makes them, paid)
      -> render (ffmpeg): 1080x1920, captions, loudness-normalised audio, under 60 s
you watch it -> Upload (always PRIVATE) -> you set it to Public in YouTube Studio
```

## The installed skill
`youtube-agent-skill` (MIT, Jake Schincariol) was reviewed first (standard-library-only scripts, no network or shell calls, no hidden instructions), then installed two ways:
* **For Claude Code sessions in this repo:** `.claude/skills/yt-*` (project-local install, the README's own method). Commands such as `/yt-script`, `/yt-shorts`, `/yt-edit`, `/yt-retention`, `/yt-package`, `/yt-seo` work when you open this repo in Claude Code. They write text for you to copy; they do not publish. For best results copy `.claude/skills/voice-template.md` to `~/.claude/youtube/voice.md` and fill it in.
* **Inside the app:** the 21 hook formulas and the hook scorer are vendored unmodified in `agentco/youtube/vendor/yt_skill/` (see its NOTICE.md). The in-app Scriptwriter runs on Gemini (not Claude), so it uses the skill's *method and tools*, not the Claude slash commands.
The scorer is a heuristic: a WEAK score is a reason to rewrite beat 1, a STRONG score is not a promise.

## Google Flow
Flow has **no public API**, and I do not use unofficial wrappers that drive your Google login. So:
* **Default, "Google Flow (I upload)":** for every beat the Director writes an exact 9:16 prompt with a Copy button. You make the clip in Flow, then upload it on that beat (mp4/mov/webm, or a PNG/JPG for a slow zoom).
* **Optional, "Veo (auto, paid)":** the official Veo API generates the clips for you. Set `VEO_MODEL`. Roughly $3 per 8-second clip, so a 6-beat Short is about $18: raise the Video Producer's monthly budget first (default is $5; the budget guard will stop it and keep your project, then **Retry** resumes after you raise it).

## Voice and music
* Voice: "AI (Gemini)" needs `GEMINI_TTS_MODEL`; or "none" gives captions only (most Shorts are watched muted).
* Music is not generated (licensing). Add trending/licensed audio in the YouTube Shorts app after upload.

## Connect YouTube (one refresh token per channel)
1. Google Cloud Console: enable **YouTube Data API v3** in the same project as Gmail (or a new one).
2. OAuth consent screen: as for Gmail (Internal on Workspace, otherwise External and **Publish app**, or tokens expire after 7 days).
3. Credentials > OAuth client (Web) with redirect `https://developers.google.com/oauthplayground`. Put the id/secret in `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET`.
4. OAuth Playground (use your own credentials), scope **`https://www.googleapis.com/auth/youtube.upload`** only. When Google asks which account, pick the one that **owns the channel** (for a brand channel, choose the brand account). Exchange the code and copy the refresh token into `YOUTUBE_REFRESH_TOKEN_ADSPOT`. Repeat, signed in as the Sidadiya channel, for `YOUTUBE_REFRESH_TOKEN_SIDADIYA`.

## Things YouTube does that you should know
* **Uploads are always private.** AgentCo has no way to make a video public. Also, as far as I know, videos uploaded through an API project that has not passed Google's API compliance audit are forced to private anyway. Publishing in YouTube Studio is one tap, so I did not build around it.
* **Quota:** each upload costs 1,600 of the default 10,000 daily units, so about 6 per day. `YOUTUBE_MAX_UPLOADS_PER_DAY` (default 5) stops you before YouTube does.
* **AI disclosure:** videos are marked as containing AI-generated media (`YOUTUBE_SYNTHETIC_DISCLOSURE=true`). Keep it on for AI clips. The API field name is from YouTube's 2024 disclosure feature; I have not been able to test it against your live channel.
* Shorts are vertical and, in this app, always under 60 seconds.

## Safety rules (tested)
Private upload only; at most one upload per video (claim, send, record; a failure is never retried silently, you press Upload again); the Video Producer cannot reach mail, payments or posts; the script may not contain emails, phone numbers or booking references; uploaded clips are checked and rendered with network protocols disabled in ffmpeg; the model sees only your public brand facts and the topic.

## Not built yet
Retention analysis (`/yt-retention` on your exported CSV), comment triage, and chapters. They work today in Claude Code; wiring them into the app is a later step. Ask if you want them.
