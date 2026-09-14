PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS presets (
    preset_id  TEXT PRIMARY KEY,
    freq_mhz   REAL    NOT NULL,
    bw_khz     REAL    NOT NULL,
    sf         INTEGER NOT NULL,
    cr         INTEGER NOT NULL,
    label      TEXT,
    first_seen INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    UNIQUE (freq_mhz, bw_khz, sf, cr)
);

CREATE TABLE IF NOT EXISTS observers (
    pubkey     TEXT PRIMARY KEY,
    name       TEXT,
    iata       TEXT,
    model      TEXT,
    fw         TEXT,
    first_seen INTEGER NOT NULL,
    last_seen  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS observer_status (
    id          INTEGER PRIMARY KEY,
    pubkey      TEXT    NOT NULL REFERENCES observers (pubkey),
    ts          INTEGER NOT NULL,
    radio_raw   TEXT,
    preset_id   TEXT REFERENCES presets (preset_id),
    noise_floor INTEGER,
    tx_air_secs INTEGER,
    rx_air_secs INTEGER,
    recv_errors INTEGER,
    uptime_secs INTEGER,
    battery_mv  INTEGER,
    queue_len   INTEGER,
    online      INTEGER NOT NULL DEFAULT 1,
    UNIQUE (pubkey, ts)
);
CREATE INDEX IF NOT EXISTS idx_status_pubkey_ts ON observer_status (pubkey, ts);

-- Posición de **cualquier** nodo que emita adverts y alguien oiga. No es una tabla de
-- observadores: la posición que se aprende es la del emisor, no la del que escucha.
-- Sirve para saber a qué distancia se midió un PDR.
CREATE TABLE IF NOT EXISTS node_positions (
    pubkey  TEXT PRIMARY KEY,
    name    TEXT,
    lat     REAL    NOT NULL,
    lon     REAL    NOT NULL,
    seen_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_packets (
    id               INTEGER PRIMARY KEY,
    ts               INTEGER NOT NULL,
    observer_pubkey  TEXT    NOT NULL REFERENCES observers (pubkey),
    -- NULL = el observer no ha publicado /status, así que su preset es desconocido
    preset_id        TEXT REFERENCES presets (preset_id),
    preset_uncertain INTEGER NOT NULL DEFAULT 0,
    packet_hash      TEXT,
    snr_x4           INTEGER,
    rssi             INTEGER,
    packet_type      INTEGER,
    route            TEXT,
    payload_len      INTEGER,
    raw_hex          TEXT,
    -- Enriquecimiento opcional desde el decodificador. NULL = no decodificado.
    path_length      INTEGER,
    payload_type     TEXT,
    is_valid         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_raw_ts ON raw_packets (ts);
CREATE INDEX IF NOT EXISTS idx_raw_hash ON raw_packets (packet_hash);
CREATE INDEX IF NOT EXISTS idx_raw_observer_ts ON raw_packets (observer_pubkey, ts);
CREATE INDEX IF NOT EXISTS idx_raw_preset_ts ON raw_packets (preset_id, ts);

CREATE TABLE IF NOT EXISTS preset_minute (
    minute_ts   INTEGER NOT NULL,
    preset_id   TEXT    NOT NULL REFERENCES presets (preset_id),
    pkts        INTEGER NOT NULL,
    uniq_hashes INTEGER NOT NULL,
    snr_avg_x4  REAL,
    snr_p50_x4  REAL,
    snr_ge0_pct REAL,
    rssi_avg    REAL,
    observers   INTEGER NOT NULL,
    PRIMARY KEY (minute_ts, preset_id)
);

CREATE TABLE IF NOT EXISTS observer_minute (
    pubkey        TEXT    NOT NULL REFERENCES observers (pubkey),
    minute_ts     INTEGER NOT NULL,
    preset_id     TEXT REFERENCES presets (preset_id),
    noise_floor   REAL,
    chan_util_pct REAL,
    err_per_h     REAL,
    pkts_rx       INTEGER NOT NULL DEFAULT 0,
    -- Señal vista por este receptor. Permite agregar por receptor en vez de por
    -- paquete, para que uno que lee mal no arrastre la media del canal.
    snr_p50_x4    REAL,
    rssi_avg      REAL,
    PRIMARY KEY (pubkey, minute_ts)
);
-- La clave primaria sirve para consultar por receptor. Detectar en qué canal estaba
-- la red necesita agrupar por tiempo, y eso sin índice es un recorrido completo.
CREATE INDEX IF NOT EXISTS idx_observer_minute_ts ON observer_minute (minute_ts);

-- Agregado por CANAL FÍSICO (freq, bw, sf), con la CR fuera de la clave a propósito:
-- viaja en la cabecera LoRa, así que no parte el tráfico. Es la tabla con la que se
-- puede agrupar por frecuencia, por ancho o por SF sin contar dos veces.
CREATE TABLE IF NOT EXISTS channel_minute (
    minute_ts   INTEGER NOT NULL,
    channel_id  TEXT    NOT NULL,
    freq_mhz    REAL    NOT NULL,
    bw_khz      REAL    NOT NULL,
    sf          INTEGER NOT NULL,
    pkts        INTEGER NOT NULL,
    uniq_hashes INTEGER NOT NULL,
    -- Bytes **en el aire** (la trama LoRa, cabecera y ruta incluidas) de las
    -- transmisiones DISTINTAS: una oída por ocho receptores cuenta una vez, igual que
    -- uniq_hashes. Mide tráfico, no recepciones.
    payload_bytes INTEGER,
    -- Mezcla de tamaños, "bytes:transmisiones,...". El tráfico es bimodal (control
    -- pequeño y adverts grandes) y su media no describe ningún paquete real, así que
    -- el tiempo de aire se calcula sobre la mezcla, no sobre un tamaño medio.
    payload_sizes TEXT,
    snr_avg_x4  REAL,
    snr_p50_x4  REAL,
    snr_ge0_pct REAL,
    rssi_avg    REAL,
    observers   INTEGER NOT NULL,
    -- CR de los receptores que oyeron el canal ese minuto, para poder mostrarla.
    crs         TEXT,
    PRIMARY KEY (minute_ts, channel_id)
);
CREATE INDEX IF NOT EXISTS idx_channel_minute_ts ON channel_minute (minute_ts);

CREATE TABLE IF NOT EXISTS pair_minute (
    minute_ts  INTEGER NOT NULL,
    channel_id TEXT    NOT NULL,
    obs_a      TEXT    NOT NULL,
    obs_b      TEXT    NOT NULL,
    heard_a    INTEGER NOT NULL,
    heard_b    INTEGER NOT NULL,
    both       INTEGER NOT NULL,
    pdr_pct    REAL    NOT NULL,
    PRIMARY KEY (minute_ts, channel_id, obs_a, obs_b)
);

CREATE TABLE IF NOT EXISTS preset_hour (
    hour_ts     INTEGER NOT NULL,
    preset_id   TEXT    NOT NULL REFERENCES presets (preset_id),
    pkts        INTEGER NOT NULL,
    uniq_hashes INTEGER NOT NULL,
    snr_p50_x4  REAL,
    snr_ge0_pct REAL,
    rssi_avg    REAL,
    observers   INTEGER NOT NULL,
    PRIMARY KEY (hour_ts, preset_id)
);

CREATE TABLE IF NOT EXISTS preset_day (
    day_ts      INTEGER NOT NULL,
    preset_id   TEXT    NOT NULL REFERENCES presets (preset_id),
    pkts        INTEGER NOT NULL,
    uniq_hashes INTEGER NOT NULL,
    snr_p50_x4  REAL,
    snr_ge0_pct REAL,
    rssi_avg    REAL,
    observers   INTEGER NOT NULL,
    PRIMARY KEY (day_ts, preset_id)
);

CREATE TABLE IF NOT EXISTS data_quality (
    day_ts                   INTEGER PRIMARY KEY,
    observers_without_status INTEGER NOT NULL DEFAULT 0,
    uncertain_packets        INTEGER NOT NULL DEFAULT 0,
    unattributed_packets     INTEGER NOT NULL DEFAULT 0,
    hash_conflicts           INTEGER NOT NULL DEFAULT 0
);
