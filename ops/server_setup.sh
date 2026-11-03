#!/usr/bin/env bash
# Install / update the self-maintenance pieces on the public VM (idempotent):
# watchdog (1 min), R2 backup (daily), weekly cleanup, journal size cap, cloudflared auto-updates.
set -euo pipefail
[ -f "$(dirname "$0")/../.secrets/deploy.env" ] && . "$(dirname "$0")/../.secrets/deploy.env"
HOST="${LV_PUBLIC_HOST:?set LV_PUBLIC_HOST to the ssh host of the server}"
cd "$(dirname "$0")/server"
rsync -az ./ "$HOST:/tmp/fv-server/"
ssh "$HOST" 'sudo bash -s' <<'REMOTE'
set -euo pipefail
cd /tmp/fv-server
command -v rclone >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get -qq install -y rclone >/dev/null
install -m 0755 fv-alert fv-watchdog fv-backup-r2 fv-weekly /usr/local/bin/
install -m 0644 fv-*.service fv-*.timer /etc/systemd/system/
install -d -m 0755 /etc/systemd/journald.conf.d && install -m 0644 journald-size.conf /etc/systemd/journald.conf.d/size.conf
install -m 0644 52fibtrader-cloudflared /etc/apt/apt.conf.d/52fibtrader-cloudflared
install -d -m 0700 /etc/fibtrader
systemctl daemon-reload
systemctl restart systemd-journald
systemctl enable -q --now fv-watchdog.timer fv-backup-r2.timer fv-weekly.timer
rm -rf /tmp/fv-server
systemctl list-timers 'fv-*' --no-pager | head -5
REMOTE
