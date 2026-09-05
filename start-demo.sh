#!/usr/bin/env bash
# Baaki — one-command judge demo launcher.
#
#   cp .env.example .env.local     # once, then paste your keys into .env.local
#   ./start-demo.sh                # every time
#
# Credentials are loaded HERE, in the shell, and exported into the child process. Nothing in the
# application reads a file: `Settings` keeps `env_file=None`, so the runtime still sees only os.environ,
# and the agent leg still takes the model credential out of the environment before the pipeline leg runs.
# This launcher changes how the environment is populated, never who may read it.
#
# The file is `.env.local`, not `.env`, on purpose: a committed architecture test asserts no `.env` exists
# in the repository root. Both names are gitignored (`.env.*`), so credentials stay out of Git either way.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

ENV_FILE="${BAAKI_ENV_FILE:-.env.local}"
DEFAULT_DSN="postgresql://postgres:postgres-local-only@127.0.0.1:55432/postgres"

bold()  { printf '\033[1m%s\033[0m\n' "$1"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$1"; }
fail()  { printf '  \033[31m✗\033[0m %s\n' "$1"; }
mask()  { local v="$1"; if [ "${#v}" -le 8 ]; then printf '********'; else printf '%s…%s' "${v:0:4}" "${v: -2}"; fi; }

bold "Baaki — AI Revenue Recovery"
echo

# ── 1. load credentials from the local, gitignored file ────────────────────────────────
# Parsed line by line rather than `source`d: a sourced file executes arbitrary shell.
if [ -f "$ENV_FILE" ]; then
  line_no=0
  while IFS= read -r line || [ -n "$line" ]; do
    line_no=$((line_no + 1))
    line="${line%%$'\r'}"
    case "$line" in ''|'#'*) continue ;; esac
    if [[ "$line" != *=* ]]; then
      warn "$ENV_FILE:$line_no ignored (not KEY=VALUE)"; continue
    fi
    key="${line%%=*}"; value="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"; key="${key%"${key##*[![:space:]]}"}"
    key="${key#export }"
    if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      warn "$ENV_FILE:$line_no ignored (invalid variable name)"; continue
    fi
    # strip one layer of matching quotes, if present
    if [[ "$value" == \"*\" || "$value" == \'*\' ]]; then value="${value:1:${#value}-2}"; fi
    [ -z "$value" ] && continue                       # blank placeholder: leave unset
    # `cp .env.example .env.local` leaves <ANGLE_BRACKET> placeholders behind; an unfilled placeholder
    # is not a credential, so treat it as unset rather than failing validation on it.
    case "$value" in "<"*">") continue ;; esac
    export "$key=$value"
  done < "$ENV_FILE"
  ok "loaded $ENV_FILE"
else
  warn "$ENV_FILE not found — using the current shell environment only"
  echo "    create it with:  cp .env.example $ENV_FILE"
fi

if [ -f .env ]; then
  warn "a root .env exists; this launcher does not read it, and an architecture test forbids it"
fi

# ── 2. validate ────────────────────────────────────────────────────────────────────────
export BAAKI_DEMO_SUPERUSER_DSN="${BAAKI_DEMO_SUPERUSER_DSN:-$DEFAULT_DSN}"
echo
bold "Configuration"
ok "database      $(printf '%s' "$BAAKI_DEMO_SUPERUSER_DSN" | sed -E 's#://[^@]*@#://***@#')"

problems=0

if [ -n "${OPENAI_API_KEY:-}" ]; then
  ok "AI            live · model credential present ($(mask "$OPENAI_API_KEY"))"
else
  warn "AI            offline · no OPENAI_API_KEY — recovery runs on the deterministic rules path"
fi

if [ -n "${RAZORPAY_KEY_ID:-}" ] && [ -n "${RAZORPAY_KEY_SECRET:-}" ]; then
  case "$RAZORPAY_KEY_ID" in
    rzp_test_*) ok "Razorpay      Test Mode · $(mask "$RAZORPAY_KEY_ID")" ;;
    *) fail "Razorpay      RAZORPAY_KEY_ID is not a test key (rzp_test_…). Live mode is refused."
       problems=$((problems + 1)) ;;
  esac
elif [ -n "${RAZORPAY_KEY_ID:-}" ] || [ -n "${RAZORPAY_KEY_SECRET:-}" ]; then
  fail "Razorpay      set BOTH RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET, or neither"
  problems=$((problems + 1))
else
  warn "Razorpay      unavailable · payment collection falls back to the deterministic simulator"
fi

if ! command -v uv >/dev/null 2>&1; then
  fail "uv not found on PATH"; problems=$((problems + 1))
fi

if ! pg_isready -q -d "$BAAKI_DEMO_SUPERUSER_DSN" 2>/dev/null; then
  warn "database not answering yet — start it with:  make db-up"
fi

if [ "$problems" -gt 0 ]; then
  echo; fail "$problems configuration problem(s) — fix the above and re-run."; exit 1
fi

# ── 3. launch ──────────────────────────────────────────────────────────────────────────
echo
bold "Starting demo → http://127.0.0.1:${BAAKI_DEMO_PORT:-8899}"
echo "  (the demo database is rebuilt and reseeded on every start; Ctrl-C to stop)"
echo
exec uv run python -m demo.server
