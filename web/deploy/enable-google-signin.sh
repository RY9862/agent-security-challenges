#!/usr/bin/env bash
# Turn on "Sign in with Google" for a deployed web front end, then check it works.
# Run on the server, from the repo root (the folder with web/ in it):
#
#   web/deploy/enable-google-signin.sh                      # Docker (web/docker-compose.yml)
#   web/deploy/enable-google-signin.sh --systemd asc-web    # no Docker, systemd service name
#
# It asks for the OAuth client secret (input hidden) and never prints it. It then:
#   1. saves the secret to web/secrets/google_client_secret (readable only by the app)
#   2. sets ASC_GOOGLE_CLIENT_ID, ASC_PUBLIC_URL and the secret's location in web/.env
#   3. restarts the app (Docker: rebuild with docker-compose.google.yml; or the systemd service)
#   4. checks the live site reports Google sign-in on
# Safe to run again: it updates the same settings instead of adding copies.
#
# Options (defaults are for codesafe-ai.cyberuci.com):
#   --client-id ID      OAuth client ID (from the Google Cloud console, Credentials page)
#   --public-url URL    the site's address, no trailing slash
#   --admins LIST       comma-separated admin emails/usernames to set in ASC_ADMINS (optional)
#   --systemd NAME      the app runs as a systemd service with this name, not Docker
#   --secret-file PATH  read the secret from a file instead of asking
#   --no-restart        only write the settings
set -euo pipefail

CLIENT_ID="223170138274-6m0fvjt41sgrm2pj309i1gs09a2q4jae.apps.googleusercontent.com"
PUBLIC_URL="https://codesafe-ai.cyberuci.com"
ADMINS=""
SYSTEMD=""
SECRET_FILE_IN=""
RESTART=1
while [ $# -gt 0 ]; do
  case "$1" in
    --client-id) CLIENT_ID="$2"; shift 2 ;;
    --public-url) PUBLIC_URL="${2%/}"; shift 2 ;;
    --admins) ADMINS="$2"; shift 2 ;;
    --systemd) SYSTEMD="$2"; shift 2 ;;
    --secret-file) SECRET_FILE_IN="$2"; shift 2 ;;
    --no-restart) RESTART=0; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 1 ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WEB="$ROOT/web"
ENV_FILE="$WEB/.env"
SECRET="$WEB/secrets/google_client_secret"
[ -f "$WEB/server.py" ] || { echo "Can't find web/server.py under $ROOT." >&2; exit 1; }
grep -q "ASC_GOOGLE_CLIENT_ID" "$WEB/server.py" || {
  echo "This checkout's web/server.py has no Google sign-in. Update it first (git pull / checkout the branch)." >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "No web/.env yet. Set up the site first (web/README.md), then run this." >&2; exit 1; }
case "$CLIENT_ID" in *.apps.googleusercontent.com) ;; *)
  echo "That doesn't look like an OAuth client ID (should end in .apps.googleusercontent.com)." >&2; exit 1 ;; esac
case "$PUBLIC_URL" in https://*|http://localhost*) ;; *)
  echo "--public-url must start with https:// (Google only allows plain http for localhost)." >&2; exit 1 ;; esac

# --- 1. the secret
if [ -n "$SECRET_FILE_IN" ]; then
  VALUE="$(tr -d '[:space:]' < "$SECRET_FILE_IN")"
elif [ -s "$SECRET" ]; then
  read -r -p "A client secret is already saved. Replace it? [y/N] " yn
  [[ "$yn" =~ ^[Yy] ]] && { read -r -s -p "Paste the OAuth client secret: " VALUE; echo; } || VALUE=""
else
  read -r -s -p "Paste the OAuth client secret (from Ryan; input is hidden): " VALUE; echo
fi
if [ -n "${VALUE:-}" ]; then
  case "$VALUE" in GOCSPX-*) ;; *) echo "Note: Google client secrets usually start with GOCSPX-. Saving it anyway." ;; esac
  mkdir -p "$WEB/secrets" && chmod 700 "$WEB/secrets"
  umask 077
  printf '%s\n' "$VALUE" > "$SECRET"
  unset VALUE
  echo "Saved the secret to web/secrets/google_client_secret"
fi
[ -s "$SECRET" ] || { echo "No client secret saved; nothing to turn on." >&2; exit 1; }

# --- 2. settings in web/.env (replace the line if it's there, add it if not)
set_env() {
  local key="$1" val="$2"
  if grep -qE "^#?[[:space:]]*$key=" "$ENV_FILE"; then
    sed -i -E "s|^#?[[:space:]]*$key=.*|$key=$val|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}
cp "$ENV_FILE" "$ENV_FILE.bak-$(date +%Y%m%d-%H%M%S)"
set_env ASC_GOOGLE_CLIENT_ID "$CLIENT_ID"
set_env ASC_PUBLIC_URL "$PUBLIC_URL"
[ -n "$ADMINS" ] && set_env ASC_ADMINS "$ADMINS"
if grep -qE '^ASC_COOKIE_SECURE=0' "$ENV_FILE" && [[ "$PUBLIC_URL" == https://* ]]; then
  sed -i -E 's|^ASC_COOKIE_SECURE=0|# ASC_COOKIE_SECURE=0   (turned off by enable-google-signin.sh: the site is on HTTPS)|' "$ENV_FILE"
fi

if [ -n "$SYSTEMD" ]; then
  set_env ASC_GOOGLE_CLIENT_SECRET_FILE "$SECRET"
  OWNER="$(systemctl show -p User --value "$SYSTEMD" 2>/dev/null || true)"
  [ -n "$OWNER" ] && sudo chown "$OWNER" "$SECRET" && sudo chmod 400 "$SECRET"
else
  set_env COMPOSE_FILE "docker-compose.yml:docker-compose.google.yml"
  # the container runs as uid 1000, same as for secrets/nrp_key
  sudo chown 1000 "$SECRET" && sudo chmod 400 "$SECRET"
fi
echo "Updated web/.env (backup next to it): ASC_GOOGLE_CLIENT_ID, ASC_PUBLIC_URL${ADMINS:+, ASC_ADMINS}"

# --- 3. restart
if [ "$RESTART" = 1 ]; then
  if [ -n "$SYSTEMD" ]; then
    sudo systemctl restart "$SYSTEMD"
    sleep 3
    sudo journalctl -u "$SYSTEMD" --since "1 min ago" --no-pager | grep "\[asc\]" | tail -3 || true
  else
    (cd "$WEB" && GIT_COMMIT=$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown) \
        docker compose up -d --build)
    sleep 5
    (cd "$WEB" && docker compose logs --tail 50 web | grep "\[asc\]" | tail -3) || true
  fi
fi

# --- 4. check
echo
echo "Redirect URI that must be listed on the Google OAuth client:"
echo "   $PUBLIC_URL/api/auth/google/callback"
if [ "$RESTART" = 1 ]; then
  ME="$(curl -s --max-time 15 "$PUBLIC_URL/api/me" || true)"
  echo "$PUBLIC_URL/api/me -> $ME"
  if [[ "$ME" == *'"google":true'* ]]; then
    echo "OK: Google sign-in is on. Open $PUBLIC_URL and click 'Sign in with Google'."
  elif [[ "$ME" == *'"google":false'* ]]; then
    echo "The new code is running but Google sign-in is off: the client ID or secret didn't load." >&2
    echo "Check web/.env and the app's log for '[asc]' lines." >&2; exit 1
  else
    echo "The site isn't answering with the new code yet. Check the app is running and the reverse" >&2
    echo "proxy points at it, then: curl -s $PUBLIC_URL/api/me" >&2; exit 1
  fi
fi
