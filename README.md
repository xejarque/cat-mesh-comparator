# cat-mesh-comparator

Observatorio de **presets** MeshCore en la banda 868 MHz para Cataluña. Ingiere la
telemetría que ya circula por el broker MQTT comunitario y la compara para poder
decidir qué slot usar, con datos en vez de opiniones.

Un **preset** es la tupla `(freq_mhz, bw_khz, sf, cr)`, no solo la frecuencia:
comparar `869.618/62.5/SF7` contra `869.525/250/SF11` no significa nada.

## Estado

| Fase | Qué es | Estado |
| ---- | ------ | ------ |
| 0 | Validar el contrato real del broker | **Hecho** — ver `docs/format-findings.md` |
| 1 | Esqueleto, esquema SQLite, config, despliegue | Hecho |
| 2 | Núcleo puro (normalize, attribution, aggregate, metrics) | Hecho, con tests |
| 3 | Colector MQTT + enriquecimiento con el decodificador | Hecho y probado contra el broker real |
| 4 | Rollups (minuto / hora / día, pares y calidad) | Hecho y probado contra el broker real |
| 5 | Web: resumen, en vivo, comparador, comparar (dos modos), observadores, campaña, calidad, metodología | Hecha; faltan mapa, dispersión y ranking |

### Lo que ya sabemos del broker real (2026-09-14)

- `/packets` es **JSON con `SNR`/`RSSI`/`hash`**; `/status` trae `radio` y `stats`.
- La red va con **SF7 y CR 4/6**, no con los SF11/CR5 que se ven en otras redes.
- El `hash` es **idéntico entre observers** para la misma transmisión (uno lo oyeron
  8 observers): el cálculo de PDR es válido.
- **19 observers**, 5 zonas (BCN, VLC, RMU, GRO, ALC). Ninguno publica paquetes sin
  status, así que hoy no hay paquetes sin atribuir.
- Los **slots 2 y 3 (869.493 / 869.556) están vacíos**: nadie escucha ahí. Es lo que
  hay que cubrir con la campaña.
- **La CR no parte el tráfico.** Viaja en la cabecera LoRa, así que 8 receptores con
  2 CR distintas oyen el mismo hash dentro de un solo canal físico, y 0 hashes
  aparecen en dos canales distintos. Ver `docs/format-findings.md` §7.

Detalle completo, incluidos los cuatro formatos de `radio` en circulación y las
tramas vacías, en [`docs/format-findings.md`](docs/format-findings.md).

## Dos niveles de agregación, y por qué

El comparador tiene que poder mirarse **por dimensión** (frecuencia, ancho, SF) y no
solo por la tupla completa, porque hay combinaciones que no existen en ningún preset
actual y aun así se quieren explorar.

| Nivel | Clave | Para qué |
| ----- | ----- | -------- |
| **Canal físico** (`channel_minute`, `pair_minute`) | `(freq, bw, sf)` | La unidad correcta: una transmisión ocupa un solo canal. Agrupar por frecuencia, ancho o SF sale de aquí, y sumar entre canales es seguro |
| **Preset** (`preset_minute`) | `(freq, bw, sf, cr)` | Ver las variantes de configuración de los receptores por separado |
| **Observer** (`observer_minute`) | `(pubkey)` | Ruido, ocupación, errores y `pkts_rx`. Aquí **sí** se puede filtrar por CR, porque la CR es un atributo del receptor |

**La CR queda fuera de la clave del canal a propósito, y no es un descuido: es lo que
mide el broker.** Sumar `uniq_hashes` por preset inflaba las transmisiones un **30%**
(74 frente a 57 en la primera medición) porque contaba dos veces cada paquete oído por
receptores de CR distinta. Y el PDR pasa de un 100% artificial (solo se emparejaban
observers con configuración idéntica, normalmente colocalizados) a valores útiles:
79% de media en SF7, con parejas que no oyen nada en común.

Se conserva `preset_minute` porque **no hay un único nivel correcto**: para saber qué
ve un receptor concreto con su configuración, el preset es lo adecuado.

## El problema central: el paquete no dice en qué preset entró

El preset es una propiedad del **receptor**, no del paquete. Un paquete se emite en
una frecuencia y el receptor está sintonizado en otra; no hay forma de que un mismo
paquete mida dos presets.

La solución es unir el pubkey del topic con la línea de tiempo de `/status` de ese
observer, y hacerlo **temporalmente correcto**:

```
packet (topic: meshcore/{IATA}/{PUBKEY}/packets)
   └─ PUBKEY ──► observer ──► /status.radio = "freq,bw,sf,cr"
```

Se guarda **cada** status con el timestamp de su payload (no basta el retained:
el retained es el estado actual y reescribiría la historia). Un paquete se resuelve
con el status en vigor en su instante.

Modos de fallo tratados, todos con test:

- Observer que nunca publica `/status` → paquete sin atribuir (`preset_id NULL`).
- Cambio de preset entre dos status → ventana ambigua: se marca `preset_uncertain`.
- Gap largo sin status → `preset_uncertain` (el preset pudo cambiar sin que lo viéramos).
- Contadores reiniciados (reboot) → no se calculan deltas falsos.

Los paquetes dudosos **no** entran en los agregados: un dato ambiguo no debe sesgar
una comparación en silencio. Se cuentan aparte.

## Dos límites que la web debe declarar

1. Solo se pueden comparar los presets **donde haya observers escuchando**. Si nadie
   está en 869.619, no hay datos de 869.619.
2. La interferencia de Meshtastic/LoRaWAN **no** aparecerá como paquetes ajenos:
   MeshCore filtra por sync word (`0x12`), así que esas tramas no llegan a la app.
   Su firma es una subida del noise floor y de `recv_errors`, no tráfico decodificable.

## Arrancar

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest          # 83 tests del núcleo
```

### Fase 0 (ya resuelta)

```bash
cp .env.example .env      # rellenar broker y credenciales de solo lectura
.venv/bin/python scripts/capture_sample.py --seconds 120 --out sample.jsonl
```

Captura y resume el contrato real. Los resultados están en
[`docs/format-findings.md`](docs/format-findings.md).

### Colector

```bash
.venv/bin/python -m app.mqtt.collector
```

Escribe en SQLite (WAL), reintenta con backoff si el broker cae, y enriquece cada
paquete decodificando su `raw` (`path_length`, `payload_type`, `is_valid`).
La descodificación es opcional y nunca fatal: si falta `meshcoredecoder` avisa y
sigue ingiriendo, y un `raw` ilegible se cuenta pero el paquete se guarda igual.

## Estructura

```
app/
  models.py         dataclasses del dominio (Packet, Status, PresetKey, Attribution)
  config.py         settings desde entorno / .env
  presets.py        planes de canales, canal físico y numeración de slots
  db.py             conexión SQLite, esquema y migraciones, escrituras
  decode.py         enriquecimiento con meshcoredecoder (nunca lanza)   (puro)
  attribution.py    línea de tiempo observer→preset y resolución        (puro)
  aggregate.py      rollups por minuto                                  (puro)
  metrics.py        resumen por preset y ranking de limpieza            (puro)
  stats.py          media, mediana y versiones ponderadas
  lora.py           tiempo de aire de LoRa: modelo, no medición         (puro)
  periods.py        regímenes de canal y comparación entre períodos      (puro)
  geo.py            distancia entre dos puntos
  health.py         ¿sigue vivo el colector?                             (puro)
  rollup.py         consolida a minuto/hora/día, pares y calidad; poda
  query.py          consultas de lectura y tall dimensional para la web
  mqtt/
    normalize.py    parseo robusto del payload MQTT                   (puro)
    collector.py    suscripción, buffering y persistencia
  web/
    main.py         FastAPI: rutas y filtros de plantilla
  templates/        Jinja2 (base + 6 páginas)
  static/app.css    sistema de diseño en tokens, sin framework ni build
sql/schema.sql
tests/              222 tests
deploy/             Caddyfile, systemd, backup
scripts/            captura de muestra (Fase 0) y chequeo de salud
Dockerfile          imagen Alpine en dos etapas (~141 MB)
docker-compose.yml  collector + web + caddy + backup
```

## La web

```bash
.venv/bin/python -m uvicorn app.web.main:app --host 127.0.0.1 --port 8000
```

En producción va detrás de Caddy, que le pone TLS y la deja en `127.0.0.1:8000`
(ver `deploy/Caddyfile`).

| Ruta | Qué hay |
| ---- | ------- |
| `/` | Resumen de las últimas 24 h y estado del sistema |
| `/vivo` | Feed de paquetes al llegar. Se consulta cada 2 s, sin SSE |
| `/comparador` | El comparador: ventana, **desglose dimensional** y **agregación por receptor** |
| `/comparar` | **Comparación aparcada**, en dos modos: simultáneo y por períodos, y el panel de **coste y beneficio de cambiar de SF** |
| `/observadores` | Quién escucha, con qué configuración y qué ruido ve |
| `/campana` | Qué canales cubre alguien y **cuáles están vacíos** |
| `/calidad` | Las cuatro causas de paquete sin atribuir, por día |
| `/metodologia` | Fórmulas y las siete trampas de lectura |

### Por qué hace falta `/comparar` y no basta con `/comparador`

`/comparador` compara canales medidos por **receptores distintos**. Eso mezcla
geografía, antena, hardware y momento: si dos canales salen diferentes, no se sabe qué
lo causó. `/comparar` compara **solo sujetos que han estado en más de un canal**, y
tiene dos modos con reglas distintas a propósito:

| | **Simultáneo** | **Por períodos** |
|---|---|---|
| Qué mide | Recepción en un punto, dos canales a la vez | Comportamiento de **toda la malla** |
| Nodos que hay que mover | Uno | Todos |
| Confusión que introduce | Ninguna: es la medida más limpia | **El tiempo** |
| Pregunta | «¿Mi nodo oye mejor en 869.450?» | «¿Qué slot debe usar la comunidad?» |

Los dos modos **no se pueden fundir en una tabla**: la regla que hace válido uno
rechazaría sistemáticamente al otro.

**Modo simultáneo.** Solo enseña sujetos presentes en más de un canal, y aplica dos
reglas:

1. **Sin solapamiento temporal no se compara.** Dos días distintos pueden diferir por
   propagación, no por canal. La vista enseña las ventanas de cada medición.
2. **Si solo cambia el SF, se avisa.** Un receptor SF7 **no puede oír** una transmisión
   SF8, así que cada uno ve a un conjunto distinto de emisores.

**Lo que hace falta para que esto sirva es mucho más barato de lo que parece: no hacen
falta muchos receptores por canal, basta con UNO que vaya alternando.**

**Por comarca** es la cuarta vista, y va **al revés**: en vez de una comarca en varios
canales, compara **el mismo canal medido por varias comarcas**. Al revés se caía en el
caso traicionero —misma frecuencia con distinto SF, donde cada receptor oyó a emisores
distintos— y las métricas eran sobre todo inventario (cuántos receptores, cuántas
recepciones), que no dice nada del canal.

Ahora la tabla **compara de verdad**: elige una comarca de referencia —la que más
receptores tiene, no la que sale mejor— y muestra la **diferencia** de cada una contra
ella, con el signo delante. En ruido y ocupación, menos es mejor. Sigue sin controlar el
receptor, así que orienta, no concluye: lo que controla es el canal, que es lo que se
discute.

**Modo por períodos.** Aquí el no solapamiento **es el diseño**: se compara una semana
con otra. Lo que sustituye a la regla de solapamiento es el **control**: otro período en
el mismo canal que A, que mide cuánta diferencia es simple paso del tiempo.

Los períodos **se deducen solos** de los datos: `observer_minute` ya sabe en qué canal
estaba cada receptor minuto a minuto, así que no hay que declarar ninguna campaña a
mano. Un tramo solo cuenta como período si al menos `MIN_REGIME_MINUTES` (60) minutos
seguidos tuvieron un canal dominante con más del 60 % de los receptores; por debajo de
eso es una migración, no un régimen.

Las medianas van **por métrica**, nunca mezcladas: promediar dBm con porcentajes y con
contadores daría una cifra que no significa nada. Y se calculan sobre los mismos sujetos
en la comparación y en el control, o no serían comparables entre sí.

**Sin control, la página dice que no se puede concluir.** No es una limitación técnica,
es lo que dicen los datos.

### Las posiciones sirven para leer el PDR, no para dibujar un mapa

**Un PDR sin distancia no significa nada**: un 79 % entre dos receptores a un kilómetro
es malo, y a cuarenta kilómetros es excelente. Por eso se guardan las posiciones que los
nodos declaran en sus **adverts** (`node_positions`) y la distancia aparece junto al PDR.

Ojo con la dirección del dato: la posición es la del **emisor** del advert, no la del
receptor que lo oyó. La de un receptor solo se conoce si él también emite y alguien lo
oye. Los nodos sin GPS no la declaran.

No hay mapa de chinchetas: saber quién escucha no ayuda a decidir un canal. Lo que ayuda
es la distancia.

### Las dos capas, y por qué están separadas

`/vivo` y `/comparador` responden preguntas distintas y por eso se actualizan distinto:

- **`/vivo`** muestra paquetes crudos al llegar. Sirve para comprobar que un nodo está
  vivo y que un cambio de configuración ha surtido efecto. **No** sirve para decidir
  qué canal es mejor: un paquete suelto no dice nada.
- **`/comparador`** trabaja sobre agregados y ventanas. Responde «qué canal aguanta
  mejor», que es una pregunta sobre distribuciones.

Para el feed en vivo **no se usa SSE**: a ~24 paquetes por minuto, preguntar cada 2 s
cuesta menos que mantener una conexión abierta por visitante. El endpoint es
`/api/packets?after=<id>`, y el navegador construye las filas con `textContent` (nunca
`innerHTML`), porque los nombres de los observadores vienen de la red.

**Sin framework de CSS y sin build.** Es una hoja de tokens escrita a mano
(`app/static/app.css`): `oklch` para el color, un único acento teal, escala por slot
verificada en daltonismo y `tabular-nums` en todas las tablas. El plan hablaba de
Tailwind; se cambió a propósito porque no aporta nada frente a un fichero de tokens y
ahorra un paso de compilación en el despliegue. Si prefieres Tailwind, es sustituir la
hoja y las clases.

El color de cada fila sale del **slot real** (869.432 → slot 1 … 869.618 → slot 4),
nunca del índice de la fila: un color que cambia al reordenar no significa nada.

## Métricas

Cada una en su nivel: ruido, ocupación y errores por **observer**; SNR, RSSI y
tráfico por **canal físico**; y el PDR por **pareja de observers** dentro de un canal.

### Dos formas de agregar, y por qué importan las dos

`/comparador` deja elegir entre **todas las recepciones** y **por receptor**:

| | Todas las recepciones | Por receptor |
|---|---|---|
| Cada receptor pesa | según cuánto oye | **uno** |
| Un nodo con malas lecturas | arrastra la media del canal | mueve un punto |
| Se ve el rango entre receptores | no | **sí** |

Con la primera, un nodo charlatán domina; con la segunda manda la mayoría de
receptores, no de paquetes. En la primera medición real, el ruido de fondo del mismo
canal iba de **−118,0 a −80,6 dBm** entre receptores: 37 dB de diferencia que en una
media quedaban invisibles.

Y hay un filtro para quedarse solo con los receptores presentes en **todos** los grupos
comparados, que convierte la comparación en apareada. Cuando la intersección está vacía
—lo normal, porque cada canal lo escucha gente distinta— la página lo dice en vez de
enseñar una tabla.

### Niveles

| Métrica | Fórmula | Fuente |
| --- | --- | --- |
| Noise floor | media de `stats.noise_floor` | `/status` (ya muestreado por el firmware) |
| Ocupación de canal | `Δ(tx_air + rx_air) / Δt × 100` | `/status` (derivada) |
| Error rate | `Δ(recv_errors) / Δt` | `/status` |
| SNR / RSSI | media, p50, % con SNR≥0 | `/packets` |
| Tráfico | recepciones/h y **hash únicos**/h (transmisiones distintas) | `/packets` |
| Bytes | longitud de la trama **en el aire** (`raw`), transmisiones distintas (una vez por hash) | `/packets` (**medido**) |
| Tiempo de aire | fórmula de Semtech sobre la **mezcla real de tamaños**, a cada SF | **modelo**, no medición |
| PDR entre observers | `|A∩B| / |A∪B|` sobre hashes, mismo **canal físico** y minuto | `/packets` |

El ranking de limpieza es un índice compuesto **heurístico** (ruido 0.35, SNR 0.25,
errores 0.25, ocupación 0.15). La web mostrará siempre los valores crudos junto a él.

## Despliegue con Docker (recomendado)

El repositorio es público, así que se clona sin credenciales de ningún tipo:

```bash
git clone https://github.com/xejarque/cat-mesh-comparator.git /opt/cat-mesh-comparator
cd /opt/cat-mesh-comparator
cp .env.example .env      # rellenar broker y credenciales
chmod 600 .env            # el .env lleva la contraseña del broker
docker compose up -d      # collector + web + backup
docker compose ps         # collector y web deben salir `healthy`
```

Para actualizar más adelante:

```bash
cd /opt/cat-mesh-comparator && git pull && docker compose up -d --build
```

En una DietPi o similar, Docker está en su propio instalador (`dietpi-software` →
Docker). Comprueba después que `docker compose version` responde: las instalaciones
viejas traen el `docker-compose` de la v1, que no entiende el `name:` del fichero de
compose.

Levanta **tres servicios** desde una sola imagen:

| Servicio | Qué hace |
| -------- | -------- |
| `collector` | Escucha el broker, escribe en SQLite y corre el rollup periódico |
| `web` | Uvicorn en `127.0.0.1:8000` (configurable, ver abajo) |
| `backup` | Copia diaria con `sqlite3 .backup`, que es seguro en caliente |

**Ojo con el volumen de SQLite.** El colector y la web son **dos contenedores**
leyendo y escribiendo el mismo fichero. Funciona porque va en un volumen con nombre
(`catmesh-data`), que vive dentro de la máquina Linux de Docker y por tanto tiene
memoria compartida POSIX de verdad, que es lo que necesita el modo WAL. **No lo
cambies por un bind mount** a una carpeta del anfitrión sin probarlo antes.

### Si accedes por VPN

Es el caso de un servidor doméstico al que no le llega internet directamente. **No uses
Caddy**: no puede obtener certificado de Let's Encrypt porque la máquina no es
alcanzable para el desafío, y no hace falta, porque la VPN ya cifra el tránsito de
punta a punta. Añadir TLS solo serviría para ver un aviso del navegador si no instalas
la autoridad de Caddy en cada dispositivo.

**Y no hay que configurar nada para que responda.** La web se publica en todas las
interfaces, así que contesta en `http://<ip-del-host>:8000` — sea la de la LAN o la de
la VPN — igual que cualquier otro servicio. Solo hace falta tocar `CATMESH_BIND` si
quieres **restringirla** a una interfaz concreta.

**Si el 8000 ya está ocupado**, cambia solo el puerto del anfitrión; el del contenedor
no hace falta tocarlo:

```bash
# En .env
CATMESH_PORT=8080
```

Para ver qué está libre antes de elegir:

```bash
ss -ltn            # puertos escuchando
docker ps          # puertos publicados por otros contenedores
```

### Publicarlo de verdad (TLS)

Solo funciona si la máquina es **alcanzable desde internet por los puertos 80 y 443**
(IP pública con redirección, o un túnel). Entonces:

```bash
# En .env
CATMESH_SITE=observatorio.tudominio.cat

docker compose --profile tls up -d
```

Caddy saca y renueva el certificado solo. El `Caddyfile` es el mismo en todos los
casos: está parametrizado con `CATMESH_SITE` y `CATMESH_UPSTREAM`.

> **Trampa de los perfiles:** un `docker compose down` normal **no** para Caddy, porque
> los servicios con perfil quedan fuera de su vista. Hay que pararlo con el mismo
> perfil: `docker compose --profile tls down`.

### Publicarlo sin abrir puertos (Cloudflare Tunnel)

Para un servidor doméstico detrás de un router, o con CGNAT. **No hace falta Caddy**:
el TLS lo pone la red de Cloudflare.

1. Añade tu dominio a Cloudflare (plan gratis) y cambia los *nameservers* en tu
   registrador. Hasta que propague, el túnel no tendrá a qué apuntar.
2. En el panel: **Zero Trust → Networks → Tunnels → Create a tunnel**, tipo
   *Cloudflared*, y copia el **token**.
3. En el túnel, añade un **public hostname**:

   | Campo | Valor |
   | ----- | ----- |
   | Subdomain | `observatorio` |
   | Domain | `tudominio.cat` |
   | Service | `http://web:8000` |

   Tiene que ser `web:8000`, **no** `localhost:8000`: `web` es el nombre del servicio
   dentro de la red de Docker, que es donde vive también `cloudflared`.

4. En `.env`:

   ```ini
   CLOUDFLARE_TUNNEL_TOKEN=eyJhIjoi...
   ```

5. Arrancarlo:

   ```bash
   docker compose --profile tunnel up -d
   docker compose logs cloudflared | tail -5
   ```

El puerto publicado en el anfitrión no cambia: sigue sirviendo para la LAN y la VPN.

**La aplicación no tiene autenticación**, así que con el túnel activo cualquiera que
sepa la dirección ve todas las dades. Para un observatorio de datos comunitarios es lo
que se quiere, pero si algún día hiciera falta restringirlo, **Cloudflare Access** pone
una lista de correos autorizados delante del mismo hostname, sin tocar la aplicación.

### Llevarse el historial acumulado

La base de datos es un fichero, así que se copia al volumen y no se pierde nada:

```bash
docker compose down
docker run --rm -u root --entrypoint sh \
  -v cat-mesh-comparator_catmesh-data:/data -v "$PWD/data":/src:ro \
  cat-mesh-comparator -c 'cp /src/catmesh.db /data/ && chown catmesh:catmesh /data/catmesh.db'
docker compose up -d
```

### Que el colector no se muera en silencio

Un colector parado **no hace ruido**: simplemente deja de llegar dato, y eso en un
proyecto que vale por el historial acumulado es el fallo más caro. Se vigila por dos
sitios:

- **`HEALTHCHECK` del contenedor**: `scripts/check_collector.py` mira cuándo se
  escribió el último paquete y devuelve código de salida 1 si pasa de
  `CATMESH_STALE_SECONDS` (30 min por defecto). Sale en `docker compose ps`.
- **Aviso en la portada**: si el dato se queda viejo, la web lo dice con un recuadro
  rojo en vez de enseñar tablas que parecen normales.

Los contenedores usan `restart: unless-stopped`, así que se levantan solos tras un
reinicio de la máquina.

### Sin Docker (systemd + Caddy en el anfitrión)

Los ficheros de `deploy/` siguen sirviendo para una instalación a pelo:

```bash
apt install caddy python3-venv sqlite3
useradd -r -s /usr/sbin/nologin catmesh
git clone <repo> /opt/cat-mesh-comparator && cd /opt/cat-mesh-comparator
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env
cp deploy/cat-mesh-collector.service /etc/systemd/system/
cp deploy/cat-mesh-backup.{service,timer} /etc/systemd/system/
systemctl enable --now cat-mesh-collector cat-mesh-backup.timer
```

El `Caddyfile` es el mismo en los dos casos: está parametrizado con
`CATMESH_SITE` y `CATMESH_UPSTREAM`, y sus valores por defecto (`localhost` y
`localhost:8000`) son los de una instalación sin Docker.

`backup.sh` usa `sqlite3 .backup` (copiar el fichero a pelo con WAL puede dar una
copia incoherente) y rota a los 14 días. Deja las copias en
`/opt/cat-mesh-comparator/backups` por defecto; si las quieres fuera del VPS, un
`rsync` desde ahí. `Caddyfile` queda listo para cuando exista la web.

## Pendiente

Queda de la Fase 5:

- **El mapa.** El broker no publica la posición de los observadores; el decoder sí
  puede extraerla de los *adverts*, pero todavía no se guarda. Preferimos no dibujar
  un mapa con posiciones inventadas.
- **La dispersión (ocupación vs SNR) y el ranking de limpieza**, pero **dentro de
  `/comparar`**, no como tabla suelta: ordenar canales medidos por receptores distintos
  es justo el output engañoso que el proyecto evita. El cálculo ya existe en
  `metrics.cleanliness_scores`.
- Guardar el **emisor** de cada paquete (el decoder ya lo extrae: `payload.decoded`)
  para poder analizar nodos concretos y no solo canales.
- La **dispersión** y el **ranking de limpieza**, dentro de `/comparar`.

### Las dos capas de la web (Fase 5)

| Capa | Qué muestra | Cadencia | Por qué así |
| ---- | ----------- | -------- | ----------- |
| **Viva** | feed de paquetes al llegar, observers online, último soroll, últimos errores | SSE, sub-segundo | para un operador que vigila su nodo. No agrega nada: anillo en memoria y las últimas N filas |
| **Analítica** | comparación por preset: ruido, SNR, tasa de errores, ocupación, PDR | ventana de minutos / horas / días | "¿qué slot es mejor?" es una pregunta sobre **distribuciones**, no instantánea |

Dos reglas que se derivan:

1. La vista analítica debe indicar siempre **"datos hasta las HH:MM"**, para que nadie
   confunda un agregado con el estado actual.
2. Nunca mostrar un ranking calculado con muy pocas muestras: un paquete a SNR +12 dB
   no dice nada de si un slot está limpio. La UI debe marcar los presets con datos
   insuficientes.

### `/calidad` debe distinguir cuatro causas, no una

Los paquetes sin atribuir no son todos lo mismo, y confundirlos hace pensar en un bug
donde no lo hay. Casos reales ya observados en el broker:

| Causa | Ejemplo real | ¿Se puede evitar? |
| ----- | ------------ | ----------------- |
| Ventana de transición de preset | cambio entre dos `/status` | No, se acota y se marca `uncertain` |
| Gap largo sin status | observer mudo > 600 s | No, se marca `uncertain` |
| **Primera aparición de un observer** | VLC empezó a emitir **67 s antes** de su primer status con `radio` | **No.** Esa información no existía |
| Observer nunca visto con `radio` | solo publica avisos `offline` | Se resuelve pidiendo al operador |

Los dos últimos son irreductibles: no se puede conocer un preset que nunca se ha
recibido. Van a `preset_id NULL` y quedan fuera de los agregados. En la primera
medición: **5 de 189 paquetes (2,6%)**, de los cuales 3 eran anteriores a la corrección
de la línea de tiempo.

### El techo de frescura no lo pone nuestra web

Medido sobre el broker real: **11 intervalos de exactamente 300 s** y un observer a
60 s. El firmware publica `/status` cada 300 s, así que **el noise floor, la ocupación
y los contadores de error no existen más a menudo que eso**. Ningún pipeline puede ser
más fresco que su fuente, y bajar `CATMESH_FLUSH_INTERVAL_S` no cambia nada de eso.

A ~24 paquetes/minuto (medido), el buffer de 5 s no aporta nada al rendimiento: existe
como valor defensivo por si la malla crece. Si quieres pérdida cero, pon
`CATMESH_FLUSH_INTERVAL_S=0` y se escribe cada mensaje en su propio commit.
