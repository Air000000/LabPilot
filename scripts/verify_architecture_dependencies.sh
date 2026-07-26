#!/bin/sh
set -eu

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 1
}

[ "$#" -eq 1 ] || fail 'usage: verify_architecture_dependencies.sh <relative-manifest-path>'

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
MANIFEST_ARG=$1

case "$MANIFEST_ARG" in
    /*) fail 'manifest path must be repository-relative' ;;
    ''|.|..|../*|*/../*|*/..|./*|*/./*) fail 'manifest path contains an unsafe segment' ;;
esac

MANIFEST_PATH=$ROOT_DIR/$MANIFEST_ARG
[ -f "$MANIFEST_PATH" ] || fail "manifest not found: $MANIFEST_ARG"

MANIFEST_REAL=$(realpath -e -- "$MANIFEST_PATH") || fail "cannot resolve manifest: $MANIFEST_ARG"
case "$MANIFEST_REAL" in
    "$ROOT_DIR"/*) ;;
    *) fail 'manifest resolves outside repository root' ;;
esac

line_number=0
entry_count=0
failed=0

while IFS= read -r line || [ -n "$line" ]; do
    line_number=$((line_number + 1))
    [ -n "$line" ] || fail "blank line in manifest at line $line_number"

    expected=$(printf '%s' "$line" | cut -c1-64)
    separator=$(printf '%s' "$line" | cut -c65-66)
    relative_path=$(printf '%s' "$line" | cut -c67-)

    [ "${#expected}" -eq 64 ] || fail "invalid SHA-256 length at line $line_number"
    case "$expected" in
        *[!0-9a-f]*) fail "invalid SHA-256 value at line $line_number" ;;
    esac
    [ "$separator" = '  ' ] || fail "manifest line $line_number must use two spaces after the hash"
    [ -n "$relative_path" ] || fail "missing path at line $line_number"

    case "$relative_path" in
        /*) fail "absolute dependency path at line $line_number" ;;
        .|..|../*|*/../*|*/..|./*|*/./*) fail "unsafe dependency path at line $line_number" ;;
    esac

    target_path=$ROOT_DIR/$relative_path
    [ -f "$target_path" ] || fail "dependency not found at line $line_number: $relative_path"
    target_real=$(realpath -e -- "$target_path") || fail "cannot resolve dependency at line $line_number: $relative_path"
    case "$target_real" in
        "$ROOT_DIR"/*) ;;
        *) fail "dependency resolves outside repository root at line $line_number: $relative_path" ;;
    esac

    actual=$(sha256sum -- "$target_real" | cut -d ' ' -f1)
    entry_count=$((entry_count + 1))
    if [ "$actual" = "$expected" ]; then
        printf '%s: OK\n' "$relative_path"
    else
        printf '%s: FAILED\n' "$relative_path" >&2
        printf '  expected: %s\n' "$expected" >&2
        printf '  actual:   %s\n' "$actual" >&2
        failed=1
    fi
done < "$MANIFEST_REAL"

[ "$entry_count" -gt 0 ] || fail 'manifest contains no entries'
[ "$failed" -eq 0 ] || exit 1
