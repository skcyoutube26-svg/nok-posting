#!/bin/sh
# Runs the stats workflow and decrypts its output with the stats key. Prints JSON.
# Key lookup: $STATS_KEY if it names a file, else ../.stats-key.pem one level above the repo root.
# Temp files go under $TMPDIR (or the system temp dir) in a private mktemp dir, removed on exit.
# Exit 2 (nothing run, nothing printed) if no key is found; the error names both paths tried.
# The key file is only ever passed to openssl by path; its content is never printed.
# Windows Git Bash: native openssl.exe cannot open POSIX temp paths, so openssl runs inside the temp
# dir with relative file names, and the key path is converted with cygpath -w when cygpath exists.
set -e
R=skcyoutube26-svg/nok-posting
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENVK="${STATS_KEY:-<STATS_KEY not set>}"
PARENTK="$ROOT/../.stats-key.pem"
if [ -n "${STATS_KEY:-}" ] && [ -f "$STATS_KEY" ]; then K="$STATS_KEY"
elif [ -f "$PARENTK" ]; then K="$PARENTK"
else
  echo "read_stats: decrypt key not found. Tried: \$STATS_KEY=$ENVK ; $PARENTK" >&2
  exit 2
fi
case "$K" in /*|?:*) ;; *) K="$(pwd)/$K" ;; esac   # absolute before we cd away
command -v cygpath >/dev/null 2>&1 && K="$(cygpath -w "$K")"
T="$(mktemp -d "${TMPDIR:-/tmp}/read_stats.XXXXXX")"
trap 'rm -rf "$T"' EXIT
[ -n "$RUN" ] || { gh workflow run stats.yml -R $R >/dev/null; sleep 8; }
id=${RUN:-$(gh run list -R $R -w stats.yml -L 1 --json databaseId -q '.[0].databaseId')}
gh run watch "$id" -R $R --exit-status >/dev/null
log=$(gh run view "$id" -R $R --log)
cd "$T"
echo "$log" | grep -o 'KEY:[A-Za-z0-9+/=]*' | tail -1 | cut -c5- | base64 -d > k.enc
echo "$log" | grep -o 'DATA:[A-Za-z0-9+/=]*' | tail -1 | cut -c6- | base64 -d > s.enc
openssl pkeyutl -decrypt -inkey "$K" -in k.enc -out k
openssl enc -d -aes-256-cbc -pbkdf2 -in s.enc -pass file:k
