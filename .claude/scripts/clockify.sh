#!/usr/bin/env bash
# Wrapper minimale sulle API Clockify: avvia, ferma e interroga il timer corrente.
#
# La API key non sta mai nel repo. Va messa in una delle due:
#   export CLOCKIFY_API_KEY="..."          (nel tuo .bashrc)
#   ~/.config/clockify/api_key             (file con dentro solo la chiave)
# La chiave si genera da Clockify: Profile settings, sezione API.
#
# Uso:
#   clockify.sh start "ROBOAI-25 raccolta terzo braccio"
#   clockify.sh stop
#   clockify.sh status

set -euo pipefail

API="https://api.clockify.me/api/v1"

die() { echo "clockify: $*" >&2; exit 1; }

command -v jq >/dev/null || die "serve jq (sudo apt install jq)"

read_key() {
  if [ -n "${CLOCKIFY_API_KEY:-}" ]; then
    printf '%s' "$CLOCKIFY_API_KEY"
  elif [ -f "$HOME/.config/clockify/api_key" ]; then
    tr -d '[:space:]' < "$HOME/.config/clockify/api_key"
  else
    die "nessuna API key. Esporta CLOCKIFY_API_KEY o crea ~/.config/clockify/api_key"
  fi
}

KEY="$(read_key)"

api() {
  local method="$1" path="$2" body="${3:-}"
  local args=(-sS -X "$method" -H "X-Api-Key: $KEY" -H "Content-Type: application/json")
  [ -n "$body" ] && args+=(-d "$body")
  curl "${args[@]}" "$API$path"
}

whoami_json() {
  local out
  out="$(api GET /user)"
  echo "$out" | jq -e '.id' >/dev/null 2>&1 || die "credenziali rifiutate da Clockify"
  printf '%s' "$out"
}

ME="$(whoami_json)"
USER_ID="$(echo "$ME"  | jq -r '.id')"
WS_ID="$(echo "$ME"    | jq -r '.activeWorkspace')"

now_utc() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

current_entry() {
  api GET "/workspaces/$WS_ID/user/$USER_ID/time-entries?in-progress=true" | jq -r '.[0] // empty'
}

# durata leggibile fra due timestamp ISO 8601
human_span() {
  local start_iso="$1" end_iso="$2" s e mins
  s=$(date -u -d "$start_iso" +%s) || return 0
  e=$(date -u -d "$end_iso"   +%s) || return 0
  mins=$(( (e - s) / 60 ))
  printf '%dh %dm' $(( mins / 60 )) $(( mins % 60 ))
}

case "${1:-}" in
  start)
    desc="${2:-}"
    [ -n "$desc" ] || die "serve una descrizione: clockify.sh start \"ROBOAI-xx cosa stai facendo\""
    running="$(current_entry)"
    if [ -n "$running" ]; then
      echo "già in corso: $(echo "$running" | jq -r '.description')"
      echo "fermalo prima con: clockify.sh stop"
      exit 1
    fi
    api POST "/workspaces/$WS_ID/time-entries" \
      "$(jq -nc --arg s "$(now_utc)" --arg d "$desc" '{start:$s, description:$d}')" \
      | jq -r '"avviato: \(.description)"'
    ;;

  stop)
    running="$(current_entry)"
    [ -n "$running" ] || { echo "nessun timer in corso"; exit 0; }
    start_iso="$(echo "$running" | jq -r '.timeInterval.start')"
    end_iso="$(now_utc)"
    api PATCH "/workspaces/$WS_ID/user/$USER_ID/time-entries" \
      "$(jq -nc --arg e "$end_iso" '{end:$e}')" >/dev/null
    echo "fermato: $(echo "$running" | jq -r '.description')"
    echo "durata:  $(human_span "$start_iso" "$end_iso")"
    ;;

  status)
    running="$(current_entry)"
    if [ -z "$running" ]; then
      echo "nessun timer in corso"
    else
      start_iso="$(echo "$running" | jq -r '.timeInterval.start')"
      echo "in corso: $(echo "$running" | jq -r '.description')"
      echo "da:       $(human_span "$start_iso" "$(now_utc)")"
    fi
    ;;

  *)
    die "uso: clockify.sh {start \"descrizione\"|stop|status}"
    ;;
esac
