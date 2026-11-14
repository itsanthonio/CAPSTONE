#!/usr/bin/env bash
# Deploy SankofaWatch to the Oracle VM.
#
#   deploy/deploy.sh              deploy committed code (git HEAD)
#   deploy/deploy.sh --dirty      deploy the working tree, including uncommitted changes
#   deploy/deploy.sh --web-only   restart only the web service (skip Celery workers)
#
# What it does, in order (and stops at the first problem):
#   1. packs the code, uploads it, and saves the previous code as a rollback archive
#   2. takes a database backup, then unpacks over the app folder (media/.env/models are untouched)
#   3. installs new Python packages only if requirements.txt changed
#   4. runs `manage.py check`, then migrate + collectstatic
#   5. restarts services and verifies the site answers
#
# Config (override with environment variables):
#   SSH_KEY   path to the private key       HOST   user@address of the VM
set -euo pipefail

SSH_KEY="${SSH_KEY:-$HOME/Downloads/ssh-key-2026-09-26.key}"
HOST="${HOST:-ubuntu@92.5.146.226}"
SITE_URL="${SITE_URL:-https://sankofa.92.5.146.226.sslip.io}"
MODE=head; WORKERS=1
for a in "$@"; do
  case "$a" in
    --dirty) MODE=dirty ;;
    --web-only) WORKERS=0 ;;
    -h|--help) sed -n 2,18p "$0"; exit 0 ;;
    *) echo "unknown option: $a"; exit 2 ;;
  esac
done

[ -f "$SSH_KEY" ] || { echo "SSH key not found: $SSH_KEY (set SSH_KEY=...)"; exit 1; }
cd "$(git rev-parse --show-toplevel)"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=15 "$HOST")
SCP=(scp -q -i "$SSH_KEY")
TS=$(date -u +%Y%m%d_%H%M%S)
PKG="${TMPDIR:-/tmp}/sankofawatch_$TS.tar.gz"

if [ "$MODE" = head ]; then
  if ! git diff --quiet -- sankofawatch || ! git diff --cached --quiet -- sankofawatch; then
    echo "WARNING: you have uncommitted changes in sankofawatch/ that will NOT be deployed (use --dirty to include them)."
  fi
  git archive --format=tar.gz -o "$PKG" HEAD sankofawatch
else
  git ls-files -z -c -o --exclude-standard sankofawatch | tar --null -czf "$PKG" -T -
fi
echo "[1/5] packed $(du -h "$PKG" | cut -f1) ($MODE)"

"${SCP[@]}" "$PKG" "$HOST:sankofawatch/incoming.tar.gz"
rm -f "$PKG"

"${SSH[@]}" bash -s "$TS" "$WORKERS" <<'REMOTE'
set -euo pipefail
TS=$1; WORKERS=$2
H=$HOME/sankofawatch; A=$H/app
mkdir -p $H/releases
echo "[2/5] saving rollback archive + database backup"
tar czf $H/releases/pre_$TS.tar.gz -C $H --exclude='app/media' --exclude='app/staticfiles' --exclude='app/local_exports' \
    --exclude='app/models' --exclude='app/static/data' --exclude='app/__pycache__' --exclude='*/__pycache__' app
ls -t $H/releases/pre_*.tar.gz | tail -n +4 | xargs -r rm -f          # keep the last 3
$H/backup.sh >/dev/null 2>&1 || echo "  (backup skipped or failed; see ~/backups/backup.log)"
mkdir -p $H/new && rm -rf $H/new/* && tar xzf $H/incoming.tar.gz -C $H/new --strip-components=1
NEWREQ=$(sha256sum $H/new/requirements.txt | cut -d' ' -f1)
OLDREQ=$(sha256sum $A/requirements.txt 2>/dev/null | cut -d' ' -f1 || true)
cp -a $H/new/. $A/ && rm -rf $H/new $H/incoming.tar.gz
echo "[3/5] dependencies"
if [ "$NEWREQ" != "$OLDREQ" ]; then $H/venv/bin/pip install -q -r $A/requirements.txt; echo "  requirements changed: installed"; else echo "  unchanged"; fi
cd $A
echo "[4/5] checks, migrate, collectstatic"
if ! $H/venv/bin/python manage.py check >/dev/null 2>&1; then
  echo "ERROR: manage.py check failed on the new code. Services were NOT restarted."
  echo "       Roll back with: tar xzf $H/releases/pre_$TS.tar.gz -C $H"
  $H/venv/bin/python manage.py check 2>&1 | tail -15
  exit 1
fi
$H/venv/bin/python manage.py migrate --noinput 2>&1 | tail -3
$H/venv/bin/python manage.py collectstatic --noinput 2>&1 | tail -1
echo "[5/5] restarting services"
sudo systemctl restart sankofawatch
if [ "$WORKERS" = 1 ]; then
  sudo systemctl restart sankofawatch-priority@{1..6} sankofawatch-automated@{1..5} sankofawatch-background@{1..4}
  systemctl is-active --quiet sankofawatch-celerybeat && sudo systemctl restart sankofawatch-celerybeat || true
fi
sleep 6
echo "  web: $(systemctl is-active sankofawatch)"
REMOTE

CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "$SITE_URL/accounts/login/" || true)
echo "site answers: HTTP $CODE"
[ "$CODE" = 200 ] || { echo "WARNING: unexpected response. Rollback archive: ~/sankofawatch/releases/pre_$TS.tar.gz on the VM"; exit 1; }
echo "deploy OK ($TS)"
