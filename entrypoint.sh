#!/bin/bash

# Create logs directory if it doesn't exist
mkdir -p /app/logs

# Log rotation - keep last 1000 lines if file exceeds 10MB
LOG_FILE="/app/logs/replacarr.log"
if [ -f "$LOG_FILE" ] && [ $(stat -c%s "$LOG_FILE") -gt 10485760 ]; then
    echo "Log file exceeded 10MB, rotating..."
    tail -n 1000 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi

# Set default schedule if not provided (default: 2:00 AM daily)
SCHEDULE="${CRON_SCHEDULE:-0 2 * * *}"

# Add cron job that logs to both file and stdout
# Use full path to python (/usr/local/bin/python) since cron has limited PATH
echo "$SCHEDULE cd /app && /usr/local/bin/python /app/replacarr.py 2>&1 | tee -a /app/logs/replacarr.log" > /etc/cron.d/replacarr-cron
chmod 0644 /etc/cron.d/replacarr-cron
crontab /etc/cron.d/replacarr-cron

# Start cron in background
cron

# Ensure log file exists and tail it to stdout (so docker logs shows output)
touch /app/logs/replacarr.log
echo "replacarr container started - cron schedule: $SCHEDULE"
echo "Log file: /app/logs/replacarr.log"
echo "View logs with: docker logs replacarr"
echo "Manual run: docker exec -it replacarr python /app/replacarr.py"
echo ""

# Tail the log file to stdout so docker logs shows real-time output
tail -f /app/logs/replacarr.log