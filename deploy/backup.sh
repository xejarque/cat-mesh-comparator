#!/bin/sh
# Copia consistente de SQLite en caliente (no copies el fichero a pelo: con WAL una
# copia directa puede quedar incoherente).
#
# POSIX sh a propósito, sin bash: la imagen de Docker es Alpine y ahí bash no existe.
set -eu

DB="${CATMESH_DB:-/opt/cat-mesh-comparator/data/catmesh.db}"
DEST="${CATMESH_BACKUP_DIR:-/opt/cat-mesh-comparator/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

if [ ! -f "$DB" ]; then
	echo "No existe la base de datos: $DB" >&2
	exit 1
fi

mkdir -p "$DEST"
sqlite3 "$DB" ".backup '$DEST/catmesh-$STAMP.db'"

# La copia hereda el modo WAL del original, y un fichero WAL no se puede abrir en un
# montaje de solo lectura: necesita crear su `-shm` al lado. Para un fichero que se
# guarda como archivo eso es una tara, así que se pasa a diario de reversión.
sqlite3 "$DEST/catmesh-$STAMP.db" "PRAGMA journal_mode=DELETE;" >/dev/null

# Rotación: se conservan 14 días.
find "$DEST" -maxdepth 1 -name 'catmesh-*.db' -type f -mtime +14 -delete 2>/dev/null || true

echo "Copia creada: $DEST/catmesh-$STAMP.db"
