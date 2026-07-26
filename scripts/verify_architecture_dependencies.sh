#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <repo-relative-dependency-manifest>" >&2
  exit 64
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
MANIFEST_ARG=$1

case "$MANIFEST_ARG" in
  /*)
    echo "absolute manifest path is not allowed: $MANIFEST_ARG" >&2
    exit 64
    ;;
esac

MANIFEST=$(realpath -m -- "$ROOT_DIR/$MANIFEST_ARG")
case "$MANIFEST" in
  "$ROOT_DIR"/*) ;;
  *)
    echo "manifest escapes repository root: $MANIFEST_ARG" >&2
    exit 64
    ;;
esac

if [ ! -f "$MANIFEST" ]; then
  echo "dependency manifest missing: $MANIFEST_ARG" >&2
  exit 66
fi

line_number=0
failed=0
while IFS= read -r line || [ -n "$line" ]; do
  line_number=$((line_number + 1))
  case "$line" in
    '') continue ;;
  esac

  if ! printf '%s\n' "$line" | grep -Eq '^[0-9a-f]{64}  [^[:space:]].*$'; then
    echo "malformed manifest line $line_number: $line" >&2
    failed=1
    continue
  fi

  expected=${line%%  *}
  dependency=${line#*  }

  case "$dependency" in
    /*)
      echo "absolute dependency path is not allowed: $dependency" >&2
      failed=1
      continue
      ;;
  esac

  target=$(realpath -m -- "$ROOT_DIR/$dependency")
  case "$target" in
    "$ROOT_DIR"/*) ;;
    *)
      echo "dependency path escapes repository root: $dependency" >&2
      failed=1
      continue
      ;;
  esac

  if [ ! -f "$target" ]; then
    echo "$dependency: missing" >&2
    failed=1
    continue
  fi

  actual=$(sha256sum "$target" | awk '{print $1}')
  if [ "$actual" != "$expected" ]; then
    echo "$dependency: FAILED expected=$expected actual=$actual" >&2
    failed=1
    continue
  fi

  echo "$dependency: OK"
done < "$MANIFEST"

if [ "$failed" -ne 0 ]; then
  exit 1
fi
