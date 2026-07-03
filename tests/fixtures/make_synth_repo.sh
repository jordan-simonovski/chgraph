#!/bin/bash
set -euo pipefail
REPO=${1:?usage: make_synth_repo.sh <dir>}
rm -rf "$REPO"; mkdir -p "$REPO"; cd "$REPO"
git init -q -b main
git config user.name alice; git config user.email alice@example.com

commit() { # commit <days_ago> <author> <email> <msg>
  local days=$1 name=$2 email=$3 msg=$4
  local d
  d=$(date -u -v-"${days}"d '+%Y-%m-%dT12:00:00' 2>/dev/null || date -u -d "-${days} days" '+%Y-%m-%dT12:00:00')
  GIT_AUTHOR_NAME=$name GIT_AUTHOR_EMAIL=$email GIT_AUTHOR_DATE=$d \
  GIT_COMMITTER_NAME=$name GIT_COMMITTER_EMAIL=$email GIT_COMMITTER_DATE=$d \
  git commit -q -m "$msg"
}

mkdir -p src tests
printf 'def handle():\n    pass\n' > src/api.py
printf 'def helper():\n    pass\n' > src/util.py
printf 'def old_thing():\n    pass\n' > src/legacy.py
printf 'def test_handle():\n    pass\n' > tests/test_api.py
git add -A; commit 400 alice alice@example.com "initial skeleton"

printf '\ndef old_thing2():\n    pass\n' >> src/legacy.py
git add -A; commit 390 bob bob@example.com "extend legacy"

for i in 1 2 3 4 5 6; do
  days=$((200 - i * 20))
  printf '\ndef handle_v%s():\n    pass\n' "$i" >> src/api.py
  printf '\ndef test_handle_v%s():\n    pass\n' "$i" >> tests/test_api.py
  if [ "$i" -eq 4 ]; then author=bob; email=bob@example.com; else author=alice; email=alice@example.com; fi
  git add -A; commit "$days" "$author" "$email" "api feature v$i + test"
done

printf '\ndef helper2():\n    pass\n' >> src/util.py
git add -A; commit 90 bob bob@example.com "util helper2"
printf '\ndef helper3():\n    pass\n' >> src/util.py
git add -A; commit 80 bob bob@example.com "util helper3"

mkdir -p src/core
git mv src/legacy.py src/core/legacy.py
commit 60 alice alice@example.com "move legacy into core/"

printf 'debug: false\n' > config.yaml
git add -A; commit 30 alice alice@example.com "add config"

printf '\ndef helper4():\n    pass\n' >> src/util.py
printf 'verbose: true\n' >> config.yaml
git add -A; commit 10 bob bob@example.com "util helper4 + config"

printf '\ndef handle_hotfix():\n    pass\n' >> src/api.py
git add -A; commit 1 alice alice@example.com "api hotfix"

echo "TOTAL_COMMITS=$(git rev-list --count HEAD)"
