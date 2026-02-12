#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

files=()
while IFS= read -r line; do
  files+=("$line")
done < <(
  git ls-files '*.md' '*.rst' '*.txt' '.github/ISSUE_TEMPLATE/*.yml' '.github/PULL_REQUEST_TEMPLATE.md' \
    | rg -v '^(LICENSE|requirements\.txt|requirements-dev\.txt)$'
)

results="$(
for f in "${files[@]}"; do
  [[ -f "$f" ]] || continue
  awk -v file="$f" '
    BEGIN { in_fence=0 }
    {
      line=$0
      if (line ~ /^```/) {
        in_fence = !in_fence
        next
      }
      if (in_fence) {
        next
      }

      scrub=line
      gsub(/`[^`]*`/, "", scrub)
      gsub(/https?:\/\/[^ )]+/, "", scrub)
      gsub(/\[[^]]*\]\([^)]*\)/, "", scrub)
      gsub(/[A-Z][A-Z0-9_]{2,}/, "", scrub)
      gsub(/([[:space:]]|^)(\.?\/)?[A-Za-z0-9._-]+(\/[A-Za-z0-9._-]+)+/, " ", scrub)

      if (scrub ~ /([A-Za-z][A-Za-z0-9\/-]*[[:space:]]+){3,}[A-Za-z][A-Za-z0-9\/-]*/) {
        printf "%s:%d:%s\n", file, NR, line
      }
    }
  ' "$f"
done
)"

if [[ -n "$results" ]]; then
  printf '%s\n' "$results"
  exit 1
fi

echo "No obvious untranslated English prose found."
