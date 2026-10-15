# RUNBOOK

## 0. Phase 0 findings (fill in before going live)

| Item | Value | Checked on |
|---|---|---|
| `python -m py_compile` of old bot.py (B15) | | |
| Old regex vs `/reel/` and `/reels/` links (B1) | | |
| Telegram fork + pinned version (kurigram / pyrofork) | | |
| MTProto upload of ~100 MB file works | | |
| Telegram limits (Bot API 50 MB / local server 2000 MB / MTProto ~2 GB) | | |
| yt-dlp: Deno needed for YouTube? `--use-extractors` syntax? cookie write-back? | | |
| `--postprocessor-args "Merger+ffmpeg_o:-movflags +faststart"` accepted | | |
| VPS: CPU / RAM / free disk / swap | | |
| Existing containers, listening ports | | |

Per-platform from the VPS IP (`yt-dlp --simulate <url>`):

| Platform | Works without cookies | With cookies | Notes |
|---|---|---|---|
| YouTube | | | |
| Instagram | | | |
| Facebook | | | |
| X/Twitter | | | |
| TikTok | | | |
| Reddit | | | |

## 1. Server checks (read-only)

```bash
lsb_release -a; uname -r
nproc; free -h; swapon --show
df -h; df -i
sudo ss -tulpn
systemctl list-units --type=service --state=running
docker ps -a; docker stats --no-stream; docker network ls; docker volume ls
docker version; docker compose version
sudo iptables -S DOCKER-USER
```

Sizing: give the bot 1 GB RAM and 1 to 1.5 CPUs. `MAX_CONCURRENT_JOBS=2` on
2 vCPU / 2 to 4 GB. Free disk >= `MAX_CONCURRENT_JOBS x MAX_FILE_MB x 2.5 + MIN_FREE_DISK_MB`.
No swap? Add a 1 to 2 GB swap file.

Install Docker Engine + Compose plugin from Docker's official apt repo (not
the snap). `docker` group membership is root-equivalent.

## 2. First deploy

```bash
sudo mkdir -p /opt/mediabot && sudo chown $USER: /opt/mediabot
git clone https://github.com/nikraj1/MediaDownloader-telegrambot /opt/mediabot
cd /opt/mediabot
cp .env.example .env && chmod 600 .env && $EDITOR .env   # never echo secrets into shell history
mkdir -p secrets/cookies data/state data/session
chmod 700 secrets secrets/cookies
sudo chown -R 10001:10001 data
# cookie files: chmod 640, readable by uid 10001

export GIT_SHA=$(git rev-parse --short HEAD)
docker compose build
docker compose up -d
docker compose ps
docker compose logs -f --tail=100
docker compose exec bot sh -c 'id -u; ffmpeg -version | head -1; yt-dlp --version; deno --version'

sudo deploy/egress-firewall.sh apply
sudo cp deploy/mediabot-egress.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now mediabot-egress
```

Verify egress from inside the container (should fail / time out):

```bash
docker compose exec bot python -c "import urllib.request; urllib.request.urlopen('http://<host-private-ip>', timeout=5)"
```

From Telegram: `/start`, a small public video, `/audio <link>`, an
unsupported link, an oversized one, `/cancel` mid-download. After each:
`docker compose exec bot ls -la /data/jobs` should be empty.

**Stop the old Render deployment** before going live: two processes with the
same token both handle updates. Rotate the token in @BotFather if it was ever
public. Keep `ALLOWED_USER_IDS` set until the security checks pass.

## 3. Update

```bash
cd /opt/mediabot && git pull
export GIT_SHA=$(git rev-parse --short HEAD)
docker compose build && docker compose up -d
```

yt-dlp: bump weekly or the day a platform breaks:
`pip-compile --generate-hashes --upgrade-package yt-dlp requirements.in`, run
tests, deploy.

## 4. Rollback

```bash
GIT_SHA=<previous-sha> docker compose up -d   # old image is still tagged
```

Prune images monthly, keep the last 3.

## 5. Cookie refresh

1. Log in with the throwaway account in a browser, export cookies (Netscape format).
2. Copy to `secrets/cookies/<site>.txt`, `chmod 640`, owner readable by uid 10001.
3. No restart needed (files are read per job). `/cookies` shows dates.

## 6. Incidents

**YouTube suddenly fails ("Sign in to confirm you're not a bot")**: the VPS IP
is flagged. Update yt-dlp first. If it persists, mark YouTube unsupported in
`/help`; don't build around evasion.

**Disk full**: `docker compose exec bot df -h /data/jobs`; restart the
container (startup wipes `/data/jobs`); lower `MAX_FILE_MB` or
`MAX_CONCURRENT_JOBS`; `docker system prune` for old images.

**Bot not responding**: `docker compose ps` (health), `docker compose logs --tail=200 bot`,
check another process isn't using the same token, `docker compose restart bot`.

**Session corruption**: `docker compose stop bot && rm data/session/* && docker compose up -d`
(the bot re-authorises with the token).

**Errors only**: `docker compose logs bot | jq -R 'fromjson? | select(.level=="ERROR")'`

## 7. Routine

* Daily during launch week: `docker stats`, `docker compose ps`.
* Weekly: rebuild with latest yt-dlp, run regression suite, deploy if green.
* Monthly: OS updates, base image rebuild, cookie freshness check.
* Optional cron: alert if `df` free < 15% (the bot also DMs admins).
