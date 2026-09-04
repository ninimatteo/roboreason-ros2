#!/usr/bin/env bash
# Wrapper minimale sulle API Clockify: avvia, ferma e interroga il timer corrente.
#
# La API key non sta mai nel repo. Va messa in una delle due:
#   export CLOCKIFY_API_KEY="..."          (nel tuo .bashrc)
#   ~/.config/clockify/api_key             (file con dentro solo la chiave)
# La chiave si genera da Clockify: Profile settings, sezione API.
#
# Il workspace richiede un progetto su ogni time entry (impostazione lato
# Clockify, non di questo script — vedi sotto). Va messo allo stesso modo:
#   export CLOCKIFY_PROJECT_ID="..."       (nel tuo .bashrc)
#   ~/.config/clockify/project_id          (file con dentro solo l'id)
# L'id si legge dalla URL del progetto su Clockify, o con:
#   clockify.sh projects
#
# Uso:
#   clockify.sh start "ROBOAI-25 raccolta terzo braccio"
#   clockify.sh stop
#   clockify.sh status
#   clockify.sh projects
#
# Nota 2026-09-04: "stop" per mesi ha stampato "fermato" anche quando la
# chiamata falliva (endpoint sbagliato — PATCH sulla collection invece di
# PUT sulla entry specifica — più curl senza controllo dello status HTTP),
# lasciando il timer aperto su Clockify senza che nessuno se ne accorgesse.
# Il fix qui sotto usa PUT /workspaces/{ws}/time-entries/{id} con project
# incluso, e ogni chiamata critica ora controlla lo status HTTP invece di
# fidarsi ciecamente dell'exit code di curl (che è 0 anche su 4xx/5xx).

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

# Vuoto se non configurato — solo "stop" ne ha davvero bisogno (il
# workspace rifiuta senza), "start"/"status" funzionano comunque.
read_project_id() {
  if [ -n "${CLOCKIFY_PROJECT_ID:-}" ]; then
    printf '%s' "$CLOCKIFY_PROJECT_ID"
  elif [ -f "$HOME/.config/clockify/project_id" ]; then
    tr -d '[:space:]' < "$HOME/.config/clockify/project_id"
  else
    printf ''
  fi
}

KEY="$(read_key)"
PROJECT_ID="$(read_project_id)"

# Risposta + status HTTP separati, in modo da poter distinguere un 4xx/5xx
# da un successo invece di fidarsi dell'exit code di curl (0 anche su
# errore HTTP, salvo --fail — che qui non aiuterebbe: serve comunque il
# body per mostrare il messaggio d'errore di Clockify).
api_raw() {
  local method="$1" path="$2" body="${3:-}"
  local args=(-sS -X "$method" -H "X-Api-Key: $KEY" -H "Content-Type: application/json" -w '\n%{http_code}')
  [ -n "$body" ] && args+=(-d "$body")
  curl "${args[@]}" "$API$path"
}

# Come api_raw, ma muore con il messaggio di Clockify se lo status non è 2xx.
api() {
  local method="$1" path="$2" body="${3:-}"
  local raw status resp_body
  raw="$(api_raw "$method" "$path" "$body")"
  status="${raw##*$'\n'}"
  resp_body="${raw%$'\n'*}"
  case "$status" in
    2??) printf '%s' "$resp_body" ;;
    *) die "chiamata $method $path fallita (HTTP $status): $(echo "$resp_body" | jq -r '.message // .' 2>/dev/null || echo "$resp_body")" ;;
  esac
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
    body="$(jq -nc --arg s "$(now_utc)" --arg d "$desc" --arg p "$PROJECT_ID" \
      '{start:$s, description:$d} + (if $p == "" then {} else {projectId:$p} end)')"
    api POST "/workspaces/$WS_ID/time-entries" "$body" | jq -r '"avviato: \(.description)"'
    ;;

  stop)
    running="$(current_entry)"
    [ -n "$running" ] || { echo "nessun timer in corso"; exit 0; }
    [ -n "$PROJECT_ID" ] || die "nessun CLOCKIFY_PROJECT_ID configurato — il workspace richiede un progetto per chiudere una entry. Esporta CLOCKIFY_PROJECT_ID o crea ~/.config/clockify/project_id (clockify.sh projects per la lista)."
    id="$(echo "$running" | jq -r '.id')"
    desc="$(echo "$running" | jq -r '.description')"
    start_iso="$(echo "$running" | jq -r '.timeInterval.start')"
    billable="$(echo "$running" | jq -r '.billable')"
    end_iso="$(now_utc)"
    # PUT sulla entry specifica, non PATCH sulla collection: quell'endpoint
    # (usato per anni da questo script) valida come una create e rifiuta
    # con 400 "Project is either required field..." se non trova un
    # progetto — e la vecchia versione non controllava lo status, quindi
    # stampava "fermato" anche quando falliva davvero. Vedi nota in testa.
    body="$(jq -nc --arg s "$start_iso" --arg e "$end_iso" --arg p "$PROJECT_ID" \
      --arg d "$desc" --argjson b "$billable" \
      '{start:$s, end:$e, projectId:$p, description:$d, billable:$b}')"
    api PUT "/workspaces/$WS_ID/time-entries/$id" "$body" >/dev/null
    echo "fermato: $desc"
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

  projects)
    api GET "/workspaces/$WS_ID/projects?page-size=100" \
      | jq -r '.[] | select(.archived | not) | "\(.id)  \(.name)"'
    ;;

  *)
    die "uso: clockify.sh {start \"descrizione\"|stop|status|projects}"
    ;;
esac
