#!/bin/sh
# Creates .env from .env.example (mode 600) and fills in the secrets the
# compose stack needs: POSTGRES_PASSWORD and INVITE_CODE. Only empty
# "NAME=" lines are filled; a value already there is never changed, so a
# second run changes nothing. Runs from anywhere; POSIX sh, BSD or GNU tools.
#
#   sh scripts/init-env.sh     (or make init-env)
set -eu

# Byte-wise tools and plain ASCII character classes, whatever the locale.
LC_ALL=C
export LC_ALL

cd "$(dirname "$0")/.."

if [ ! -f .env.example ]; then
  echo "init-env: .env.example not found in $(pwd)" >&2
  exit 1
fi

if [ ! -f .env ]; then
  # umask first: the file never exists with wider permissions.
  (umask 077 && cp .env.example .env)
  chmod 600 .env
  echo "init-env: created .env from .env.example"
fi

warn() {
  echo "init-env: warning: $*" >&2
}

# The temporary copy of .env (secrets included) goes, however the script ends.
tmp=
trap '[ -z "$tmp" ] || rm -f "$tmp"' EXIT
trap 'exit 1' HUP INT TERM

# 32 hex characters (128 bits).
random_hex() {
  od -An -tx1 -N16 /dev/urandom | tr -d ' \n'
}

# 12 characters without look-alikes (no 0/o, 1/l/i). Reads a fixed number of
# bytes: a pipeline from the endless /dev/urandom ends only through SIGPIPE,
# which CI runners (and other launchers) start processes with ignored, and
# then GNU fold reads forever (BSD tools print nothing instead).
random_code() {
  code=
  while [ ${#code} -lt 12 ]; do
    code=$code$(head -c 256 /dev/urandom | tr -dc 'abcdefghjkmnpqrstuvwxyz23456789')
  done
  printf '%s\n' "$code" | cut -c1-12
}

# NAME's value as written in .env (the last such line), or nothing.
value_of() {
  sed -n "s/^$1=//p" .env | tail -n 1
}

if grep -q "$(printf '\r')" .env; then
  warn ".env has Windows line endings (CRLF); convert it (dos2unix .env), the carriage returns end up in the values"
fi

# Fills NAME's line if it is exactly "NAME=" (no value); a value already there
# is kept. A temporary file instead of sed -i, whose syntax differs between
# BSD and GNU sed; cat > .env keeps the file's mode.
fill_if_empty() {
  name=$1
  value=$2
  if grep -q "^$name=\$" .env; then
    tmp=$(mktemp "${TMPDIR:-/tmp}/init-env.XXXXXX")
    sed "s/^$name=\$/$name=$value/" .env >"$tmp"
    cat "$tmp" >.env
    rm -f "$tmp"
    echo "init-env: generated $name"
  elif ! grep -q "^$name=" .env; then
    warn ".env has no $name= line (add one, or see .env.example)"
  elif [ -z "$(value_of "$name" | tr -d "\r\t \"'")" ]; then
    # Quotes, blanks or a carriage return only: not filled, never guessed at.
    warn "$name in .env looks empty but is not exactly \"$name=\" (quotes, spaces or CRLF?); fix the line and run again"
  fi
}

fill_if_empty POSTGRES_PASSWORD "$(random_hex)"
fill_if_empty INVITE_CODE "$(random_code)"

# A password of the operator's own goes into DATABASE_URL as is (compose.yaml)
# and compose expands a $ in it: only URL-safe characters work. The value is
# never printed.
case $(value_of POSTGRES_PASSWORD) in
  *[!A-Za-z0-9._~-]*)
    warn "POSTGRES_PASSWORD has characters other than letters, digits and . _ ~ -; it goes into DATABASE_URL as is, so use only those"
    ;;
esac

# The file holds the database password and API keys: the owner's alone.
if [ -n "$(find .env \( -perm -040 -o -perm -004 \) -print)" ]; then
  warn ".env is readable by other users; chmod 600 .env"
fi

# Without the quotes a value may be written in.
invite_code=$(value_of INVITE_CODE | tr -d "\r\"'")

cat <<EOF

Next steps:
  1. Fill in LLM_BASE_URL, LLM_API_KEY, LLM_MODEL and LLM_SMALL_MODEL in .env
     (chat does not work without them).
  2. docker compose up -d --build --wait
     (HTTPS through Caddy: set DOMAIN and COMPOSE_PROFILES=caddy in .env
      first)
  3. Create the first user:
     docker compose exec server python -m redream_api.cli create-user EMAIL

EOF
case $invite_code in
  *[![:space:]]*)
    echo "Invite code to share with the people who may sign up: $invite_code"
    ;;
esac
