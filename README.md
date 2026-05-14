# 🎬 Replacarr

**Automatically upgrade quality of recently watched movies in Radarr.**

Monitors Plex for recently played movies and triggers Radarr to replace low-quality files with better versions based on your quality profile settings.

---

## How It Works

1. Checks Plex for movies played in the last X days
2. Looks up each movie in Radarr to check current file quality
3. Compares current quality against your desired quality (resolution only)
4. If current quality is lower, sends a delete request to Radarr
5. Radarr automatically re-searches and downloads a better quality version

---

## Quick Start

### 1. Clone or download Replacarr

```bash
git clone https://github.com/PLEXEUM/replacarr.git
cd replacarr
```

### 2. Create your config file

```bash
mkdir config
cp .env.example config/.env
```

Edit `config/.env` with your settings:

```ini
PLEX_URL=http://192.168.0.77:32400
PLEX_TOKEN=your_plex_token_here

RADARR_URL=http://192.168.0.77:7878
RADARR_API_KEY=your_radarr_api_key_here

DESIRED_QUALITY=1080p
RECENT_DAYS=7
MAX_REPLACEMENTS_PER_RUN=3
SKIP_HOURS=24
LOG_LEVEL=INFO
```

### 3. Start the container

```bash
docker-compose up -d
```

### 4. Check the logs

```bash
docker logs replacarr
```

Or view the log file: `logs/replacarr.log`

---

## Configuration Options

| Setting | Description |
|---------|-------------|
| `PLEX_URL` | Your Plex server URL (e.g., http://192.168.0.77:32400) |
| `PLEX_TOKEN` | Your Plex authentication token |
| `RADARR_URL` | Your Radarr URL (e.g., http://192.168.0.77:7878) |
| `RADARR_API_KEY` | From Radarr Settings → General |
| `DESIRED_QUALITY` | Minimum quality: 480p, 720p, 1080p, or 4k |
| `RECENT_DAYS` | How many days back to check Plex watch history |
| `MAX_REPLACEMENTS_PER_RUN` | Max movies to replace in one run (safety limit) |
| `SKIP_HOURS` | Don't retry a movie again within this many hours |
| `LOG_LEVEL` | DEBUG, INFO, WARNING, or ERROR |

---

## Manual Run

Run the script immediately (not waiting for schedule):

```bash
docker exec -it replacarr python /app/replacarr.py
```

---

## View Results

- **Radarr UI** – Downloads will appear in your queue
- **Docker logs** – `docker logs replacarr`
- **Log file** – `logs/replacarr.log`
- **Last run results** – `logs/replacarr_last_run.json`

---

## Schedule

The script runs automatically at 2:00 AM daily. To change the schedule, edit the environment variable in `docker-compose.yml`:

```yaml
environment:
  - CRON_SCHEDULE=0 11 * * *
```

Cron format: `minute hour day month weekday` (using 24-hour time)

---

## Quality Comparison

Replacarr compares resolution only (ignores source type like Bluray vs WEBDL):

| Current Quality | Desired Quality | Action |
|----------------|----------------|--------|
| 480p | 720p | ✅ Replace |
| 720p | 720p | ❌ Skip |
| 1080p | 720p | ❌ Skip |

---

## Logging

**INFO mode** (default): Shows normal operation

**DEBUG mode**: Shows API requests, responses, and detailed comparison data

To enable DEBUG, change `LOG_LEVEL=DEBUG` in `.env` and restart:

```bash
docker-compose restart
```

---

## Troubleshooting

**"Missing required environment variables"**

- Ensure `config/.env` exists and has all required fields

**"Plex connection failed"**

- Verify `PLEX_URL` is correct and Plex is running
- Verify `PLEX_TOKEN` is valid (can be found in Plex web UI → Settings → Devices)

**"Radarr connection failed"**

- Verify `RADARR_URL` is correct and Radarr is running
- Verify `RADARR_API_KEY` is correct (Settings → General)

**No movies are being replaced**

- Check if movies were played within `RECENT_DAYS`
- Check if current quality already meets `DESIRED_QUALITY`
- Run with `LOG_LEVEL=DEBUG` to see detailed comparison

---

## Files

| File | Purpose |
|------|---------|
| `replacarr.py` | Main script |
| `config/.env` | Your settings (you create this) |
| `logs/replacarr.log` | Script output |
| `logs/replacarr_last_run.json` | Last run results |

---

## Requirements

- Docker Desktop (Windows/Mac) or Docker Engine (Linux)
- Plex server running and accessible
- Radarr running and accessible

---

## License

MIT

---

**Replacarr** – Upgrade your recently watched movies. 🎬