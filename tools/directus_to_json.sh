#!/usr/bin/env bash
# directus_to_json.sh -- turn a phys_comp_prod mysqldump into a JSON bundle.
#
# Neither application depends on MySQL. This runs once, against a throwaway
# container, and produces the JSON that the import commands actually read:
#
#   ./tools/directus_to_json.sh physcomp.sql directus-export
#
# writes directus-export/{parts,categories,subcategories,part_sets,
# parts_parts,files}.jsonl -- one JSON object per line, so a 1,500-row table
# never has to fit in a single string (mysql's JSON_ARRAYAGG is bounded by
# group_concat_max_len and truncates silently, which is a nasty way to lose
# rows).

set -euo pipefail

DUMP="${1:?usage: directus_to_json.sh <dump.sql> <outdir>}"
OUT="${2:?usage: directus_to_json.sh <dump.sql> <outdir>}"
CONTAINER="${CONTAINER:-ioref-mysql-import}"
PASSWORD="${MYSQL_PASSWORD:-ioref}"

mkdir -p "$OUT"

cleanup() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  trap cleanup EXIT
  echo "==> starting throwaway MySQL"
  docker run -d --name "$CONTAINER" \
    -e MYSQL_ROOT_PASSWORD="$PASSWORD" -e MYSQL_DATABASE=phys_comp_prod \
    mysql:8.0 >/dev/null

  echo -n "==> waiting for it"
  for _ in $(seq 1 60); do
    docker exec "$CONTAINER" mysqladmin ping -uroot -p"$PASSWORD" >/dev/null 2>&1 && break
    echo -n "."; sleep 2
  done
  echo

  echo "==> loading $DUMP"
  docker exec -i "$CONTAINER" mysql -uroot -p"$PASSWORD" phys_comp_prod < "$DUMP" 2>/dev/null
fi

emit() {  # emit <name> <select>
  local name="$1" sql="$2"
  # utf8mb4 explicitly: the tables are utf8mb3 and the client otherwise hands
  # back latin1, which mangles the degree signs and ohm symbols in descriptions
  # ("300º range", "10kΩ") into undecodable bytes.
  docker exec "$CONTAINER" mysql -uroot -p"$PASSWORD" \
    --default-character-set=utf8mb4 -N --raw --batch phys_comp_prod \
    -e "$sql" 2>/dev/null > "$OUT/$name.jsonl"
  printf '    %-16s %6s rows\n' "$name" "$(wc -l < "$OUT/$name.jsonl")"
}

echo "==> exporting"

emit parts "SELECT JSON_OBJECT(
  'id', id, 'part_number', part_number, 'name', name, 'description', description,
  'image', image, 'signal_type', signal_type, 'part_set', part_set,
  'category', category, 'subcategory', subcategory, 'hidden', hidden,
  'docs_about', docs_about, 'docs_what_it_is', docs_what_it_is,
  'docs_when_to_use_it', docs_when_to_use_it, 'docs_how_it_works', docs_how_it_works,
  'docs_how_to_use_it', docs_how_to_use_it, 'docs_getting_started', docs_getting_started,
  'docs_resources', docs_resources,
  'inventory_history', inventory_history, 'backstock_history', backstock_history,
  'price_history', price_history, 'supplier_history', supplier_history,
  'purchase_link_history', purchase_link_history,
  'min_quantity', min_quantity, 'max_quantity', max_quantity, 'unit', unit,
  'location', location, 'status', status, 'supplier_part_num', supplier_part_num,
  'label_text', label_text,
  'current_inventory', current_inventory, 'current_backstock', current_backstock
) FROM parts"

emit categories    "SELECT JSON_OBJECT('id',id,'name',name,'slug',slug) FROM categories"
emit subcategories "SELECT JSON_OBJECT('id',id,'name',name,'slug',slug,'category',category) FROM subcategories"
emit part_sets     "SELECT JSON_OBJECT('id',id,'name',name,'slug',slug,'description',description,'image',image) FROM part_sets"
emit parts_parts   "SELECT JSON_OBJECT('parts_id',parts_id,'related_parts_id',related_parts_id) FROM parts_parts"
emit files         "SELECT JSON_OBJECT('id',id,'filename_disk',filename_disk,'filename_download',filename_download,'title',title,'type',type,'filesize',filesize) FROM directus_files"

echo "==> wrote $OUT"
