# ntpviz 2.0

Interactive NTP visualization dashboard replacing the legacy ntpviz static HTML+PNG pages.

## Project Overview

This is a replacement for the two existing ntpviz setups (`ntpviz-1d.rimuru.io` and `ntpviz-7d.rimuru.io`) that generated static HTML pages with gnuplot PNG images. ntpviz 2.0 uses interactive JavaScript charts (uPlot) with a modern UI, deployed as a single static site on GitHub Pages.

## Architecture

- **`ntpviz2-build.py`** - Python script that reads NTPsec logs from `/var/log/ntpsec/`, downsamples them, and outputs JSON data files
- **`index.html`** - Single-file dashboard (inline CSS + JS) using uPlot for interactive time-series charts
- **`update-ntpviz2.sh`** - Shell script that runs the builder then pushes to GitHub Pages
- **`favicon.ico`**, **`ntpsec-logo.png`** - Static assets from the original ntpviz

## Data Pipeline

1. NTPsec writes stats to `/var/log/ntpsec/` (loopstats, peerstats, gpsd, temps)
2. `ntpviz2-build.py` parses logs using `ntp.statfiles.NTPStats` (system-installed), downsamples with numpy, outputs JSON to `data/1d/` and `data/7d/`
3. `index.html` fetches JSON at page load, renders interactive charts with uPlot

## Key Dependencies

- **Python**: `ntp.statfiles` (system package from ntpsec), `numpy`, `scipy`
- **Frontend**: uPlot 1.6.31 loaded from CDN (no build step, no npm)
- **Deployment**: GitHub Pages via `Gyakusetsu/ntpviz` repo (to be created)

## Data Format

JSON files use columnar arrays (matching uPlot's native format):
```json
{"data": {"time": [...], "offset": [...], "frequency": [...]}}
```

Downsampling: 30-second buckets for 1-day, 120-second buckets for 7-day.

## Deployment

The output directory structure for GitHub Pages:
```
index.html
favicon.ico
ntpsec-logo.png
CNAME
data/1d/{loopstats,peerstats,gpsd,temps,summary}.json
data/7d/{loopstats,peerstats,gpsd,temps,summary}.json
```

Cron runs `update-ntpviz2.sh` every 30 minutes, which rebuilds JSON and force-pushes to GitHub Pages.

## Current Status

- Build script and dashboard are functional and tested
- Not yet deployed - needs: git repo creation, CNAME setup, crontab update
- Old setup still running: `update-ntp-page-1d.sh` (every 30 min) and `update-ntp-page-7d.sh` (every hour)

## Commands

```bash
# Build JSON data
python3 ntpviz2-build.py -d /var/log/ntpsec -o . -p 1,7 -c

# Preview locally
python3 -m http.server 8080

# Deploy (run by cron)
sh update-ntpviz2.sh
```
