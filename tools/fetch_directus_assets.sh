#!/usr/bin/env bash
# fetch_directus_assets.sh -- download Directus file bytes over HTTP.
#
# The SQL dump carries directus_files metadata but not the files themselves.
# If you have shell access to the Directus host, tarring its uploads directory
# is simpler and complete. This is the alternative when you only have the API.
#
#   export DIRECTUS_URL=https://admin.ioref.org
#   export DIRECTUS_TOKEN=<admin token>
#   ./tools/fetch_directus_assets.sh ../directus-export ../directus-uploads
#
# Downloads only the files the content actually references (~140 of 185), named
# by filename_disk so the importer's --uploads can find them. Re-runnable:
# anything already present and non-empty is skipped.

set -euo pipefail

EXPORT="${1:?usage: fetch_directus_assets.sh <export-dir> <out-dir> [--all]}"
OUT="${2:?usage: fetch_directus_assets.sh <export-dir> <out-dir> [--all]}"
ALL="${3:-}"

: "${DIRECTUS_URL:?set DIRECTUS_URL, e.g. https://admin.ioref.org}"
: "${DIRECTUS_TOKEN:?set DIRECTUS_TOKEN (admin token)}"
command -v jq >/dev/null || { echo "need jq"; exit 1; }

DIRECTUS_URL="${DIRECTUS_URL%/}"
mkdir -p "$OUT"

# Which ids are actually referenced: parts.image, part_sets.image, and the
# /images/parts/<filename> paths embedded in the guide markdown (resolved
# through filename_download, the way maker-cards' file-redirect route does).
python3 - "$EXPORT" "$ALL" > "$OUT/.wanted" <<'PY'
import json, pathlib, re, sys

export, want_all = pathlib.Path(sys.argv[1]), sys.argv[2] == "--all"
load = lambda n: [json.loads(l) for l in (export / f"{n}.jsonl").open(encoding="utf-8")]

files = load("files")
if want_all:
    wanted = {f["id"] for f in files}
else:
    parts, sets = load("parts"), load("part_sets")
    by_download = {f["filename_download"]: f["id"] for f in files if f.get("filename_download")}
    wanted = {r["image"] for r in parts if r.get("image")}
    wanted |= {s["image"] for s in sets if s.get("image")}
    doc_keys = [k for k in parts[0] if k.startswith("docs_")]
    for r in parts:
        for k in doc_keys:
            for m in re.finditer(r'src="/images/parts/([^"?]+)"', r.get(k) or ""):
                if m.group(1) in by_download:
                    wanted.add(by_download[m.group(1)])

for f in files:
    if f["id"] in wanted and f.get("filename_disk"):
        print(f"{f['id']}\t{f['filename_disk']}\t{f.get('filesize') or 0}")
PY

total=$(wc -l < "$OUT/.wanted")
bytes=$(awk -F'\t' '{s+=$3} END {printf "%.1f", s/1024/1024}' "$OUT/.wanted")
echo "==> $total files (~${bytes} MB)"

n=0; skipped=0; failed=0
while IFS=$'\t' read -r id disk _; do
  n=$((n + 1))
  if [ -s "$OUT/$disk" ]; then skipped=$((skipped + 1)); continue; fi
  if ! curl -fsSL -H "Authorization: Bearer $DIRECTUS_TOKEN" \
       "$DIRECTUS_URL/assets/$id" -o "$OUT/$disk"; then
    echo "    FAILED $id ($disk)"
    rm -f "$OUT/$disk"
    failed=$((failed + 1))
  fi
  [ $((n % 20)) -eq 0 ] && echo "    $n/$total"
done < "$OUT/.wanted"

rm -f "$OUT/.wanted"
echo "==> done: $((total - skipped - failed)) downloaded, $skipped already present, $failed failed"
echo "    now: uv run python manage.py import_directus <export> --uploads $OUT"
