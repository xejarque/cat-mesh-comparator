# Fase 0 — Contrato real del broker (RESUELTO)

Fecha de captura: 2026-09-14
Broker: `broker.livemap-meshcorecat.com:8883` (TLS, usuario de solo lectura)
IATA observados: **BCN** (11 observers), **VLC** (5), RMU, GRO, ALC
Muestras: 90 s (62 mensajes) + corrida real del colector (75 s, 30 paquetes)

## Veredicto

**El diseño aguanta.** `/packets` publica JSON con `SNR`, `RSSI` y `hash`, y
`/status` trae `radio` con `freq,bw,sf,cr`. No hace falta descodificar `raw` para
la comparación básica: todo lo necesario viene en el payload.

Pero la captura destapó **cuatro cosas que rompían suposiciones** y ya están
corregidas con tests. Todas ellas son hallazgos de este broker concreto, no de
ninguna especificación.

## 1. `/packets` — sí, JSON

39 de 39 mensajes parseados como paquete. Campos presentes: `SNR`, `RSSI`, `hash`.

```json
{"origin":"BCN_1_08980","origin_id":"1579DC3A...","timestamp":"2026-09-14T12:46:01.989983+00:00",
 "type":"PACKET","direction":"rx","time":"12:46:01","date":"14/09/2026","len":"132",
 "packet_type":"4","route":"F","payload_len":"12","raw":"F5930103...","SNR":"6.5","RSSI":"-118"}
```

**Comprobación clave para el PDR:** el `hash` es **idéntico entre observers** para la
misma transmisión. En la muestra, un hash lo oyeron **8 observers** y otro 7.
38 recepciones → solo 6 transmisiones únicas. El cruce por `hash` es válido y la
red está densamente cubierta, que es justo lo que necesita el comparador.

Longitud del `hash`: 16 caracteres hex (8 bytes), uniforme.

### Trama vacía

Un nodo (`054B2F40D43C`) emite tramas con `SNR`/`RSSI`/`hash`/`raw` en blanco y `len=0`.
Se descartan como `packet_without_data` y se cuentan: contarlas inflaría la tasa de
paquetes por preset, que es una métrica de cabecera.

## 2. `/status` — sí, con `radio`

```json
{"status":"online","timestamp":"2026-09-14T12:42:34.549678+00:00","origin":"BCN_1_08980",
 "model":"Heltec V4 OLED","firmware_version":"v1.15.0-dee3e26","radio":"869.618,62.5,7,6",
 "stats":{"battery_mv":4000,"uptime_secs":123,"queue_len":0,"noise_floor":-118,
          "tx_air_secs":1,"rx_air_secs":12,"recv_errors":0}}
```

### Cuatro formatos de `radio` en circulación

| Formato | Ejemplo | Tratamiento |
|---|---|---|
| Normal | `869.618,62.5,7,6` | directo |
| Float32 | `869.6179809,62.5,7,6` | se redondea a 3 decimales → `869.618` |
| Prefijo de chip y barras | `SX1262 869.618/62/7/6` | se extraen los 4 números |
| Vacío | `""` | sin preset (aviso de offline) |

El formato del chip afectaba a un observer **que sí publica paquetes**: sin
soportarlo, todo su tráfico habría quedado sin atribuir. El BW `62` (en vez de
`62.5`) se ajusta al ancho estándar de LoRa más cercano.

### Status offline sin `radio`

Hay avisos de `"status": "offline"` sin `radio` ni `stats`. **No deben entrar en la
línea de tiempo**: si entraran, borrarían el preset conocido del observer y dejarían
sin atribuir todo su tráfico posterior. Se ignoran para la atribución y solo se
registran como estado.

## 3. Presets reales de la red

**La red NO usa SF11/CR5.** Va con **SF7** y **CR 4/6**, que es lo que hay que
comparar:

| freq | bw | sf | cr | Etiqueta | Observado |
|---|---|---|---|---|---|
| 869.618 | 62.5 | 7 | 6 | Slot 4 (nominal 869.619) | preset principal |
| 869.618 | 62.5 | 7 | 8 | Slot 4 · SF7/CR8 | variante en uso |
| 869.618 | 62.5 | 8 | 8 | Slot 4 · SF8/CR8 | variante en uso |
| 869.432 | 62.5 | 7 | 6 | Slot 1 (nominal 869.431) | observer offline |

Los slots 2 y 3 (869.493 / 869.556) **no aparecen**: no hay nadie escuchando ahí.
Es exactamente el límite que hay que cubrir con la campaña.

## 4. Observers

- **19 observers** publican status; **9** publican paquetes.
- **Ningún observer publica paquetes sin status** → 0 paquetes sin atribuir.
- `origin_id` del payload coincide con el pubkey del topic.

## 5. Enriquecimiento (`meshcoredecoder`)

Decodificando el `raw`: 30/30 correctos, **0 fallos**. Aporta lo que el JSON no da:

- `payload_type`: Advert, GroupText, Request, Response
- **`path_length`: de 0 a 8 saltos** (media 3.27 en la corrida del colector)
- `is_valid` / `errors`

## 6. Decisiones que se derivan

1. Intervalo de status observado: 300 s mayoritario (un observer a 60 s) →
   `CATMESH_STATUS_MAX_GAP_S=600` es correcto.
2. El volumen es bajo (~24 paquetes/min muestreados). SQLite de sobra.
3. Los presets deben **descubrirse de los datos**, no venir de una lista fija:
   aparecen tuplas nuevas (`SF7/CR8`) que ninguna suposición habría cubierto.
4. Hay que **declarar en la web** que solo se comparan los presets con observers.
   Hoy los slots 2 y 3 estarían vacíos.
5. El `message_hash` del decodificador **no** coincide con el `hash` del broker. Para
   cruzar observers se usa el del broker, que es el que se repite entre ellos.

## 7. La CR no identifica un canal físico (corrección)

La primera medición dio **14 "conflictos" de hash**: el mismo `hash` bajo dos presets
distintos. Investigados uno a uno, **los 14 eran `869.618/62.5/7/6` contra
`869.618/62.5/7/8`, y ninguno implicaba una frecuencia distinta.**

No son conflictos. **La tasa de codificación viaja en la cabecera LoRa**, así que dos
receptores con CR distinto sobre la misma frecuencia y SF decodifican la misma
transmisión. El canal físico es `(freq, bw, sf)`; la CR es un ajuste del receptor que
no cambia lo que es capaz de oír.

Consecuencias:

- `data_quality.hash_conflicts` ahora compara `(freq, bw, sf)`, no la tupla entera.
  Antes marcaba 14 falsos positivos que habrían hecho gritar a la página de calidad
  por un no-problema.
- **La agregación de paquetes pasa a ser por canal físico** (`channel_minute`,
  `pair_minute`), con `preset_minute` conservado para ver configuraciones de receptor
  por separado. Medido: sumar `uniq_hashes` por preset inflaba las transmisiones un
  **30%** (74 frente a 57), porque contaba dos veces cada paquete oído por receptores
  de CR distinta.
- El **PDR se vuelve informativo**: emparejando por preset solo se comparaban
  observers con configuración idéntica (normalmente colocalizados) y salía 100%. Por
  canal físico hay 277 parejas en SF7, con media del 79% y un mínimo del 0% — hay
  observers que no oyen nada en común, que es justo la información que buscábamos.
- Para la Fase 5: las dimensiones seleccionables son **frecuencia, ancho y SF**. La CR
  se puede usar como **filtro sobre observers**, nunca como partición del tráfico.

### El decodificador no expone la CR de la transmisión

Se comprobó: los campos de `to_dict()` son `isValid`, `messageHash`, `path`,
`pathByteLength`, `pathHashSize`, `pathLength`, `payload`, `payloadType`,
`payloadVersion`, `routeType`, `totalBytes`. **No hay CR/SF/BW.** El `raw` empieza
después de la cabecera LoRa, así que la CR con la que se emitió un paquete **no es
observable** desde los datos del broker. La CR solo se conoce como configuración del
receptor, que es exactamente lo que publica `/status`.

## 8. PDR medido entre observers

Con 5 horas de datos, los pares de observers que comparten preset dan **PDR del 100%**:
oyen exactamente el mismo conjunto de paquetes. Es un resultado coherente con lo
anterior (mismo canal físico, cobertura densa) y confirma que el cruce por `hash`
funciona. El PDR será informativo cuando se comparen observers **geográficamente
alejados**, que es justo lo que debe buscar la campaña.
