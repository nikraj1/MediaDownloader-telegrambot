# MediaDownloader-telegrambot

A Telegram bot that downloads media from (almost) any link supported by
[yt-dlp](https://github.com/yt-dlp/yt-dlp) and sends it back as a playable
video/audio (document fallback). Single async Python process, long polling,
runs in one Docker container on a VPS. No open ports.

## How it works

link → URL extraction + normalisation → SSRF guard → rate limits / dedupe →
bounded queue → yt-dlp probe (subprocess) → policy (size, duration, live,
playlist, DRM) → yt-dlp download into a per-job temp dir → ffprobe /
faststart / thumbnail → `send_video` / `send_audio` / `send_document` → cleanup.

* **Kurigram** (maintained Pyrogram fork) over MTProto: bots can upload up to
  ~2 GB without a local Bot API server.
* Downloads go to disk, never RAM. Every yt-dlp/FFmpeg run is a killable
  subprocess with hard timeouts.
* `/cancel` or the ❌ button stops a job within seconds and removes its files.

> "Supported by yt-dlp" is not "works from a datacenter IP". YouTube often
> blocks VPS IPs; Instagram usually needs cookies.

## Commands

| Command | Who | What |
|---|---|---|
| send a link | everyone (private chat) | download video |
| `/dl <link>` | everyone (groups too) | download video |
| `/audio <link>` | everyone | audio only |
| `/cancel` | everyone | cancel your newest job |
| `/settings` | everyone | auto vs. pick-quality mode, max quality |
| `/help`, `/start` | everyone | info and limits |
| `/stats`, `/pause`, `/resume`, `/cookies` | admins | operations |

## Quick start (Docker, recommended)

```bash
git clone https://github.com/nikraj1/MediaDownloader-telegrambot /opt/mediabot
cd /opt/mediabot
cp .env.example .env && chmod 600 .env && $EDITOR .env
mkdir -p secrets/cookies data/state data/session
chmod 700 secrets secrets/cookies
sudo chown -R 10001:10001 data
export GIT_SHA=$(git rev-parse --short HEAD)
docker compose build && docker compose up -d
docker compose ps    # wait for (healthy)
sudo deploy/egress-firewall.sh apply
```

Full deploy, update, rollback and incident steps: [deploy/RUNBOOK.md](deploy/RUNBOOK.md).

## Local development

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip uninstall -y pyrogram   # kurigram ships the same module name
pip install -r requirements.txt
# needs ffmpeg + ffprobe on PATH
cp .env.example .env   # set API_ID, API_HASH, BOT_TOKEN, LOG_FORMAT=text
# and point JOB_DIR/STATE_DIR/SESSION_DIR/COOKIES_DIR at local folders, e.g. ./data/jobs
python -m app          # or: python bot.py
```

## Configuration

All settings are environment variables, see [.env.example](.env.example).
Missing or invalid values stop the bot at startup with a one-line error.

## Cookies (optional)

Put Netscape-format cookie files in `secrets/cookies/` named per site
(`instagram.txt`, `youtube.txt`, `x.txt`, `facebook.txt`, `tiktok.txt`,
`reddit.txt`, `vimeo.txt`). They're mounted read-only, copied per job, used
only for the matching domain, and never logged. Use a throwaway account.

## Privacy

Files exist only in a per-job temp directory and are deleted after delivery
(or failure). Logs never contain tokens, cookies or URL query strings.

## License

MIT, see [LICENSE](LICENSE).
