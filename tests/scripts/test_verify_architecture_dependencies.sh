#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
SCRIPT="$ROOT/scripts/verify_architecture_dependencies.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT INT TERM

make_repo() {
  name=$1
  repo="$TMP/$name"
  mkdir -p "$repo/scripts" "$repo/docs"
  cp "$SCRIPT" "$repo/scripts/verify_architecture_dependencies.sh"
  chmod +x "$repo/scripts/verify_architecture_dependencies.sh"
  printf 'alpha\n' > "$repo/docs/a.md"
  printf 'beta\n' > "$repo/docs/b.md"
  (
    cd "$repo"
    sha256sum docs/a.md docs/b.md > manifest.sha256
  )
  printf '%s\n' "$repo"
}

expect_success() {
  name=$1
  shift
  if ! "$@" >"$TMP/$name.out" 2>"$TMP/$name.err"; then
    cat "$TMP/$name.out" "$TMP/$name.err" >&2
    echo "expected success: $name" >&2
    exit 1
  fi
}

expect_failure() {
  name=$1
  shift
  if "$@" >"$TMP/$name.out" 2>"$TMP/$name.err"; then
    echo "expected failure: $name" >&2
    exit 1
  fi
}

repo=$(make_repo success)
expect_success valid "$repo/scripts/verify_architecture_dependencies.sh" manifest.sha256
grep -q 'docs/a.md: OK' "$TMP/valid.out"
grep -q 'docs/b.md: OK' "$TMP/valid.out"

repo=$(make_repo missing)
rm "$repo/docs/b.md"
expect_failure missing "$repo/scripts/verify_architecture_dependencies.sh" manifest.sha256
grep -q 'missing' "$TMP/missing.err"

repo=$(make_repo changed)
printf 'changed\n' > "$repo/docs/a.md"
expect_failure changed "$repo/scripts/verify_architecture_dependencies.sh" manifest.sha256
grep -q 'expected=' "$TMP/changed.err"
grep -q 'actual=' "$TMP/changed.err"

repo=$(make_repo malformed)
printf 'not-a-manifest-line\n' > "$repo/manifest.sha256"
expect_failure malformed "$repo/scripts/verify_architecture_dependencies.sh" manifest.sha256
grep -q 'malformed manifest line' "$TMP/malformed.err"

repo=$(make_repo absolute)
hash=$(sha256sum "$repo/docs/a.md" | awk '{print $1}')
printf '%s  %s\n' "$hash" "$repo/docs/a.md" > "$repo/manifest.sha256"
expect_failure absolute "$repo/scripts/verify_architecture_dependencies.sh" manifest.sha256
grep -q 'absolute dependency path' "$TMP/absolute.err"

repo=$(make_repo escape)
printf 'outside\n' > "$TMP/outside.md"
hash=$(sha256sum "$TMP/outside.md" | awk '{print $1}')
printf '%s  ../outside.md\n' "$hash" > "$repo/manifest.sha256"
expect_failure escape "$repo/scripts/verify_architecture_dependencies.sh" manifest.sha256
grep -q 'escapes repository root' "$TMP/escape.err"

echo 'architecture dependency guard tests: PASS'
