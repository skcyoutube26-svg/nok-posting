#!/bin/sh
# Runs the stats workflow and decrypts its output with ../.stats-key.pem. Prints JSON.
set -e
R=skcyoutube26-svg/nok-posting; K="$(dirname "$0")/../.stats-key.pem"; T="$(dirname "$0")/../insights/.tmp"; mkdir -p "$T"
[ -n "$RUN" ] || { gh workflow run stats.yml -R $R >/dev/null; sleep 8; }
id=${RUN:-$(gh run list -R $R -w stats.yml -L 1 --json databaseId -q '.[0].databaseId')}
gh run watch "$id" -R $R --exit-status >/dev/null
log=$(gh run view "$id" -R $R --log)
echo "$log" | grep -o 'KEY:[A-Za-z0-9+/=]*' | tail -1 | cut -c5- | base64 -d > $T/k.enc
echo "$log" | grep -o 'DATA:[A-Za-z0-9+/=]*' | tail -1 | cut -c6- | base64 -d > $T/s.enc
openssl pkeyutl -decrypt -inkey "$K" -in $T/k.enc -out $T/k
openssl enc -d -aes-256-cbc -pbkdf2 -in $T/s.enc -pass file:$T/k
rm -rf $T
