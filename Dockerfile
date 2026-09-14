# ---------- etapa de construcción ----------
# Aquí sí hay compiladores. Si alguna dependencia no trae rueda para musl, se compila
# en esta etapa y el resultado se copia: los compiladores no llegan a la imagen final.
FROM python:3.13-alpine AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apk add --no-cache build-base libffi-dev

WORKDIR /wheels
COPY requirements.txt ./
RUN pip wheel --wheel-dir /wheels -r requirements.txt

# ---------- imagen final ----------
FROM python:3.13-alpine

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CATMESH_DB=/data/catmesh.db

# sqlite: lo necesita deploy/backup.sh, que usa `sqlite3 .backup` para copiar la base
#         en caliente sin corromperla.
# tini:   PID 1 que recoge zombies y reparte señales. El colector las usa para vaciar
#         el buffer y cerrar limpio al pararse.
RUN apk add --no-cache sqlite tini \
    && adduser -D -H -s /sbin/nologin catmesh

WORKDIR /app

COPY --from=build /wheels /wheels
COPY requirements.txt ./
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY app ./app
COPY sql ./sql
COPY scripts ./scripts
COPY deploy ./deploy

# El colector y la web comparten el volumen de SQLite, así que corren con el mismo
# usuario: si no, los ficheros quedarían con dos dueños distintos.
RUN chmod +x /app/deploy/backup.sh \
    && mkdir -p /data /backups \
    && chown -R catmesh:catmesh /data /backups /app
USER catmesh

EXPOSE 8000

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["python", "-m", "uvicorn", "app.web.main:app", "--host", "0.0.0.0", "--port", "8000"]
