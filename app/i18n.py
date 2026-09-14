"""Traducciones de la web.

El castellano es la lengua fuente y el idioma por defecto, así que la clave del catálogo
es el propio texto castellano. Añadir un idioma es añadir un diccionario: no hace falta
gettext, ni ficheros ``.po``, ni un paso de compilación.

Las claves se comparan con los espacios normalizados, de modo que un párrafo que en la
plantilla ocupa varias líneas casa con una sola entrada del catálogo. Las sustituciones
(``{nombre}``) van con ``str.format`` y no con ``%`` porque la web está llena de ``%``
literales que el formato de estilo antiguo confundiría con marcas.
"""

from __future__ import annotations

from collections.abc import Callable

from markupsafe import Markup

DEFAULT_LANGUAGE = "es"
LANGUAGES = ("es", "ca")
LANGUAGE_LABELS = {"es": "Castellano", "ca": "Català"}
# Etiqueta corta, para el selector de la cabecera.
LANGUAGE_SHORT = {"es": "ES", "ca": "CA"}
COOKIE_NAME = "lang"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def normalize(text: str) -> str:
    return " ".join(text.split())


def translate(text: str, lang: str, **values: object) -> Markup:
    """Traduce ``text`` a ``lang``, sustituyendo ``{nombre}`` por ``values``.

    El resultado es ``Markup`` porque los textos son nuestros y llevan HTML en línea
    (``<strong>``, ``<code>``, ``<a>``): escaparlos rompería la página. Solo se pasan a
    ``_()`` literales propios o etiquetas generadas por el código, nunca datos de red.
    Si falta la traducción se devuelve el original, así que una entrada olvidada se ve
    en castellano en vez de romper nada.
    """
    if lang != DEFAULT_LANGUAGE:
        translated = _CATALOGS.get(lang, {}).get(normalize(text))
        if translated is not None:
            text = translated
    result = Markup(text)
    return result.format(**values) if values else result


def translator(lang: str) -> Callable[..., Markup]:
    def gettext(text: str, **values: object) -> Markup:
        return translate(text, lang, **values)

    return gettext


# El grueso del trabajo: cada cadena visible de la web, en catalán.
CATALAN: dict[str, str] = {
    # --- cabecera, navegación y pie ---
    "Idioma": "Idioma",
    "Datos del broker comunitario de Cataluña, en directo.": (
        "Dades del broker comunitari de Catalunya, en directe."
    ),
    "Consulta generada el": "Consulta generada el",
    "Resumen": "Resum",
    "En vivo": "En directe",
    "Comparador": "Comparador",
    "Comparar": "Comparar",
    "Comparar por períodos": "Comparar per períodes",
    "Observadores": "Observadors",
    "Campaña": "Campanya",
    "Calidad": "Qualitat",
    "Metodología": "Metodologia",
    "Cómo se miden las métricas": "Com es mesuren les mètriques",
    # --- dimensiones ---
    "Frecuencia": "Freqüència",
    "Ancho": "Amplada",
    "SF": "SF",
    # --- reloj relativo (filtro `ago`) ---
    "hace segundos": "fa segons",
    "hace {n} min": "fa {n} min",
    "hace {n} h": "fa {n} h",
    "hace {n} d": "fa {n} d",
    # --- etiquetas de métrica generadas en query.py / periods.py ---
    "Ruido de fondo": "Soroll de fons",
    "Ocupación": "Ocupació",
    "Errores/h": "Errors/h",
    "Recepciones": "Recepcions",
    "Minutos medidos": "Minuts mesurats",
    "PDR": "PDR",
    "Observaciones": "Observacions",
    # --- motivos de exclusión de la comparación por períodos ---
    "no aparece en el segundo período": "no apareix en el segon període",
    "no estaba en el primer período": "no estava en el primer període",
    # --- canal del feed en vivo ---
    "sin atribuir": "sense atribuir",
    # --- _comparar_tabs.html ---
    "Modo de comparación": "Mode de comparació",
    "Simultáneo": "Simultani",
    "Por períodos": "Per períodes",
    "El mismo receptor o pareja en dos canales <strong>a la vez</strong>. Es la medida más limpia, pero solo cubre los nodos que se mueven.": (
        "El mateix receptor o parella en dos canals <strong>alhora</strong>. És la "
        "mesura més neta, però només cobreix els nodes que es mouen."
    ),
    "La red entera en un canal contra la red entera en otro. Responde la pregunta que importa, pero arrastra el efecto del tiempo.": (
        "La xarxa sencera en un canal contra la xarxa sencera en un altre. Respon la "
        "pregunta que importa, però arrossega l'efecte del temps."
    ),
    # --- index.html ---
    "Qué canal está más limpio": "Quin canal està més net",
    "Este observatorio mide, con datos reales del broker comunitario, cómo se comporta cada <strong>canal físico</strong> de la banda de 868 MHz. La pregunta que responde no es «qué se oye ahora», sino «qué combinación de frecuencia, ancho y SF aguanta mejor» — que es una pregunta sobre distribuciones, no sobre un instante.": (
        "Aquest observatori mesura, amb dades reals del broker comunitari, com es "
        "comporta cada <strong>canal físic</strong> de la banda de 868 MHz. La "
        "pregunta que respon no és «què se sent ara», sinó «quina combinació de "
        "freqüència, amplada i SF aguanta millor» — que és una pregunta sobre "
        "distribucions, no sobre un instant."
    ),
    "Canales con datos (24 h)": "Canals amb dades (24 h)",
    "Transmisiones distintas": "Transmissions diferents",
    "<strong>El colector lleva {n} minutos sin recibir nada.</strong> Lo normal es que llegue tráfico cada pocos minutos. O el broker está caído, o el recolector se ha parado — y cuando se para no avisa. Comprueba el servicio con <code>docker compose ps</code> o <code>systemctl status cat-mesh-collector</code>.": (
        "<strong>El col·lector porta {n} minuts sense rebre res.</strong> El normal "
        "és que arribi trànsit cada pocs minuts. O el broker està caigut, o el "
        "recol·lector s'ha aturat — i quan s'atura no avisa. Comprova el servei amb "
        "<code>docker compose ps</code> o <code>systemctl status cat-mesh-collector</code>."
    ),
    "Últimas 24 horas, por canal físico": "Últimes 24 hores, per canal físic",
    "Canal": "Canal",
    "Transmisiones": "Transmissions",
    "SNR mediana": "SNR mediana",
    "Errores/h": "Errors/h",
    "CR de los receptores": "CR dels receptors",
    "La última columna es la <strong>CR de los receptores</strong>, no del tráfico. Viaja en la cabecera LoRa, así que dos receptores con CR distinta sobre la misma frecuencia y SF oyen exactamente lo mismo: por eso <strong>no</strong> se usa como dimensión al comparar.": (
        "L'última columna és la <strong>CR dels receptors</strong>, no del trànsit. "
        "Viatja a la capçalera LoRa, així que dos receptors amb CR diferent sobre la "
        "mateixa freqüència i SF senten exactament el mateix: per això <strong>no</strong> "
        "s'usa com a dimensió al comparar."
    ),
    "Ver el comparador completo →": "Veure el comparador complet →",
    "Todavía no hay datos agregados": "Encara no hi ha dades agregades",
    "El recolector está conectado, pero aún no se ha ejecutado ninguna consolidación. Los agregados se recalculan cada 5 minutos.": (
        "El recol·lector està connectat, però encara no s'ha executat cap "
        "consolidació. Els agregats es recalculen cada 5 minuts."
    ),
    "Calidad del dato": "Qualitat de la dada",
    "En los últimos 30 días: <strong>{unattributed}</strong> paquetes sin preset atribuible, <strong>{uncertain}</strong> en ventana ambigua y <strong>{conflicts}</strong> conflictos de hash.": (
        "En els últims 30 dies: <strong>{unattributed}</strong> paquets sense preset "
        "atribuïble, <strong>{uncertain}</strong> en finestra ambigua i "
        "<strong>{conflicts}</strong> conflictes de hash."
    ),
    "Los paquetes sin atribuir no entran en ningún agregado.": (
        "Els paquets sense atribuir no entren en cap agregat."
    ),
    "Por qué.": "Per què.",
    # --- vivo.html ---
    "Cada paquete que el broker acaba de entregar, sin agregar. Sirve para comprobar que un nodo está vivo y que un cambio de configuración ha surtido efecto, <strong>no</strong> para decidir qué canal es mejor: un paquete suelto no dice nada sobre eso. Para eso está el": (
        "Cada paquet que el broker acaba d'entregar, sense agregar. Serveix per "
        "comprovar que un node és viu i que un canvi de configuració ha fet efecte, "
        "<strong>no</strong> per decidir quin canal és millor: un paquet solt no diu "
        "res sobre això. Per a això hi ha el"
    ),
    "comparador": "comparador",
    "Estado": "Estat",
    "al día": "al dia",
    "Pausar": "Pausa",
    "Se consulta cada 2 s. Se conservan los 200 últimos en pantalla.": (
        "Es consulta cada 2 s. Es conserven els 200 últims a la pantalla."
    ),
    "No se puede contactar con el servidor. La tabla deja de actualizarse, pero lo que ya está en pantalla sigue siendo válido. Se reintenta solo.": (
        "No es pot contactar amb el servidor. La taula deixa d'actualitzar-se, però "
        "el que ja és a la pantalla segueix sent vàlid. Es reintenta sol."
    ),
    "Hora": "Hora",
    "Observador": "Observador",
    "Zona": "Zona",
    "SNR": "SNR",
    "RSSI": "RSSI",
    "Tipo": "Tipus",
    "Saltos": "Salts",
    "Hash": "Hash",
    "Nada en el feed todavía": "Res al feed encara",
    "El recolector está conectado, pero aún no ha guardado ningún paquete. Aparecerán aquí en cuanto lleguen.": (
        "El recol·lector està connectat, però encara no ha guardat cap paquet. "
        "Apareixeran aquí tan bon punt arribin."
    ),
    "pausado": "pausat",
    "nuevos": "nous",
    "sin conexión": "sense connexió",
    "Reanudar": "Reprèn",
    # --- observadores.html ---
    "Cada receptor que publica en el broker. Lo que se compara entre canales son <em>distribuciones</em> de lo que estos nodos oyen, así que saber quién escucha y con qué configuración es parte del dato, no un anexo.": (
        "Cada receptor que publica al broker. El que es compara entre canals són "
        "<em>distribucions</em> del que aquests nodes senten, així que saber qui "
        "escolta i amb quina configuració forma part de la dada, no és un annex."
    ),
    "Hardware": "Maquinari",
    "Configuración": "Configuració",
    "CR": "CR",
    "Última vez": "Última vegada",
    "<strong>Sin mapa, de momento.</strong> El broker no publica la posición de los observadores: el decoder sí puede extraerla de los <em>adverts</em>, pero eso todavía no se está guardando. Preferimos no dibujar un mapa con posiciones inventadas.": (
        "<strong>Sense mapa, de moment.</strong> El broker no publica la posició "
        "dels observadors: el decoder sí que pot extreure-la dels <em>adverts</em>, "
        "però això encara no s'està guardant. Preferim no dibuixar un mapa amb "
        "posicions inventades."
    ),
    "Un observador que solo aparece con un aviso <code>offline</code> no tiene preset utilizable, y sus paquetes no se pueden atribuir. Se ve en": (
        "Un observador que només apareix amb un avís <code>offline</code> no té "
        "preset utilitzable, i els seus paquets no es poden atribuir. Es veu a"
    ),
    "Calidad": "Qualitat",
    "Ningún observador todavía": "Cap observador encara",
    "El recolector aún no ha recibido ningún <code>/status</code>. Hasta que llegue uno, no hay nada que atribuir.": (
        "El recol·lector encara no ha rebut cap <code>/status</code>. Fins que "
        "n'arribi un, no hi ha res a atribuir."
    ),
    # --- comparador.html ---
    "Elige la ventana temporal y cómo quieres desglosar. Las dimensiones son <strong>frecuencia</strong>, <strong>ancho de banda</strong> y <strong>SF</strong>: son las que parten de verdad el tráfico.": (
        "Tria la finestra temporal i com vols desglossar. Les dimensions són "
        "<strong>freqüència</strong>, <strong>amplada de banda</strong> i "
        "<strong>SF</strong>: són les que parteixen de debò el trànsit."
    ),
    "Ventana": "Finestra",
    "Desglosar por": "Desglossar per",
    "Agregar": "Agregar",
    "Todas las recepciones": "Totes les recepcions",
    "Por receptor": "Per receptor",
    "Solo receptores presentes en todos los grupos": (
        "Només receptors presents a tots els grups"
    ),
    "Comparar solo los comunes ({common} de {total})": (
        "Comparar només els comuns ({common} de {total})"
    ),
    "Aplicar": "Aplica",
    "Datos hasta {ts}": "Dades fins a {ts}",
    "<strong>Cada receptor cuenta una vez</strong>, oiga mucho o poco. Con «todas las recepciones» un nodo charlatán pesa más que los demás, y uno con malas lecturas arrastra la media de todo el canal. Aquí un nodo así mueve una posición, y el rango que va al lado lo deja a la vista en vez de esconderlo.": (
        "<strong>Cada receptor compta una vegada</strong>, senti molt o poc. Amb "
        "«totes les recepcions» un node xerraire pesa més que els altres, i un amb "
        "males lectures arrossega la mitjana de tot el canal. Aquí un node així mou "
        "una posició, i el rang del costat el deixa a la vista en comptes d'amagar-lo."
    ),
    "Y solo se cuentan los <strong>{common}</strong> receptores que aparecen en <em>todos</em> los grupos comparados, de {total}. Eso convierte la comparación en apareada: mismos nodos en los dos lados.": (
        "I només es compten els <strong>{common}</strong> receptors que apareixen en "
        "<em>tots</em> els grups comparats, de {total}. Això converteix la comparació "
        "en aparellada: mateixos nodes als dos costats."
    ),
    "Ahora mismo <strong>no hay ninguno</strong>: cada grupo lo escuchan receptores distintos, así que no existe comparación apareada posible.": (
        "Ara mateix <strong>no n'hi ha cap</strong>: cada grup l'escolten receptors "
        "diferents, així que no existeix cap comparació aparellada possible."
    ),
    "Estás agrupando sin <strong>frecuencia</strong> o sin <strong>ancho</strong>. El piso de ruido y la ocupación se miden por receptor y dependen de ambos, así que estos dos valores son un promedio de canales distintos. El tráfico, el SNR y el PDR sí se pueden sumar con seguridad: una transmisión ocupa un solo canal físico.": (
        "Estàs agrupant sense <strong>freqüència</strong> o sense <strong>amplada</strong>. "
        "El terra de soroll i l'ocupació es mesuren per receptor i depenen de tots dos, "
        "així que aquests dos valors són una mitjana de canals diferents. El trànsit, "
        "l'SNR i el PDR sí que es poden sumar amb seguretat: una transmissió ocupa un "
        "sol canal físic."
    ),
    "Resumen por": "Resum per",
    "Grupo": "Grup",
    "Receptores": "Receptors",
    "(rango)": "(rang)",
    "Ocupación mediana": "Ocupació mediana",
    "Errores/h mediana": "Errors/h mediana",
    "Recepciones mediana": "Recepcions mediana",
    "PDR mediana": "PDR mediana",
    "SNR ≥ 0": "SNR ≥ 0",
    "RSSI medio": "RSSI mitjà",
    "Transmisiones distintas por grupo": "Transmissions diferents per grup",
    "Una transmisión cuenta una vez aunque la oigan ocho receptores. Es la medida de cuánto tráfico hay, no de cuántas veces se ha oído.": (
        "Una transmissió compta una vegada encara que la sentin vuit receptors. És la "
        "mesura de quant trànsit hi ha, no de quantes vegades s'ha sentit."
    ),
    "Recepciones a lo largo del tiempo": "Recepcions al llarg del temps",
    "Recepciones por minuto y grupo": "Recepcions per minut i grup",
    "Por configuración exacta de receptor": "Per configuració exacta de receptor",
    "Aquí sí se separa la CR, porque cada fila es lo que ve un receptor con su configuración concreta. Los totales de esta tabla <strong>no</strong> se deben sumar para hablar de tráfico: contarían dos veces cada paquete oído por receptores de CR distinta.": (
        "Aquí sí que se separa la CR, perquè cada fila és el que veu un receptor amb "
        "la seva configuració concreta. Els totals d'aquesta taula <strong>no</strong> "
        "s'han de sumar per parlar de trànsit: comptarien dues vegades cada paquet "
        "sentit per receptors de CR diferent."
    ),
    "Preset": "Preset",
    "Bytes": "Bytes",
    "Sin datos en esta ventana": "Sense dades en aquesta finestra",
    "Ningún receptor ha oído nada con esta combinación de dimensiones en el periodo elegido. Prueba una ventana más amplia, o mira": (
        "Cap receptor ha sentit res amb aquesta combinació de dimensions en el "
        "període triat. Prova una finestra més àmplia, o mira"
    ),
    "qué canales no cubre nadie": "quins canals no cobreix ningú",
    ": una combinación que no escucha nadie no se puede comparar con nada.": (
        ": una combinació que no escolta ningú no es pot comparar amb res."
    ),
    # --- comparar.html ---
    "Comparar canales de verdad": "Comparar canals de debò",
    "<strong>El comparador normal compara canales medidos por receptores distintos.</strong> Eso mezcla geografía, antena, hardware y momento: si dos canales salen diferentes, no se sabe qué lo causó. Aquí solo aparecen sujetos que han estado en <strong>más de un canal</strong> — el mismo receptor, la misma pareja o la misma comarca. Es la única forma de poder decir «esto es por el canal».": (
        "<strong>El comparador normal compara canals mesurats per receptors "
        "diferents.</strong> Això barreja geografia, antena, maquinari i moment: si "
        "dos canals surten diferents, no se sap què ho va causar. Aquí només apareixen "
        "subjectes que han estat en <strong>més d'un canal</strong> — el mateix "
        "receptor, la mateixa parella o la mateixa comarca. És l'única manera de poder "
        "dir «això és pel canal»."
    ),
    "Y una regla dura: <strong>si las medidas no se solapan en el tiempo, no se comparan.</strong> Dos días distintos pueden diferir por propagación, no por canal.": (
        "I una regla dura: <strong>si les mesures no se solapen en el temps, no es "
        "comparen.</strong> Dos dies diferents poden diferir per propagació, no per canal."
    ),
    "Misma frecuencia, solo cambia el SF": "Mateixa freqüència, només canvia l'SF",
    "un receptor SF7 no puede oír una transmisión SF8, así que cada uno ve a un conjunto distinto de emisores: esto describe poblaciones distintas, no el mismo enlace": (
        "un receptor SF7 no pot sentir una transmissió SF8, així que cada un veu un "
        "conjunt diferent d'emissors: això descriu poblacions diferents, no el mateix "
        "enllaç"
    ),
    "Se solapan {n} min · comparación válida": "Se solapen {n} min · comparació vàlida",
    "Sin solapamiento temporal": "Sense solapament temporal",
    "la diferencia entre canales podría ser propagación, no canal": (
        "la diferència entre canals podria ser propagació, no canal"
    ),
    "Métrica": "Mètrica",
    "Medido entre": "Mesurat entre",
    "Coste y beneficio de cambiar de SF": "Cost i benefici de canviar d'SF",
    "<strong>Los bytes se miden; el tiempo de aire se calcula.</strong> Los bytes son la trama <strong>en el aire</strong> (cabecera y ruta incluidas), y una transmisión cuenta <strong>una vez</strong> aunque la oigan ocho receptores. El tiempo de aire se <strong>modela</strong> con la fórmula de Semtech: el broker no publica con qué SF se emitió, solo con cuál escucha cada receptor.": (
        "<strong>Els bytes es mesuren; el temps d'aire es calcula.</strong> Els bytes "
        "són la trama <strong>a l'aire</strong> (capçalera i ruta incloses), i una "
        "transmissió compta <strong>una vegada</strong> encara que la sentin vuit "
        "receptors. El temps d'aire es <strong>modela</strong> amb la fórmula de "
        "Semtech: el broker no publica amb quin SF es va emetre, només amb quin "
        "escolta cada receptor."
    ),
    "Se modela la <strong>mezcla real de tamaños</strong>, no un paquete medio: el tráfico de una malla es <strong>bimodal</strong> (control pequeño y adverts grandes), así que su media no describe ningún paquete que exista. Y como SF y ancho tienen que coincidir para demodular, el SF del canal <em>es</em> el de emisión: la columna de su SF no es una hipótesis.": (
        "Es modela la <strong>barreja real de mides</strong>, no un paquet mitjà: el "
        "trànsit d'una malla és <strong>bimodal</strong> (control petit i adverts "
        "grans), així que la seva mitjana no descriu cap paquet que existeixi. I com "
        "que SF i amplada han de coincidir per demodular, l'SF del canal <em>és</em> "
        "el d'emissió: la columna del seu SF no és una hipòtesi."
    ),
    "El <strong>margen</strong> es un modelo de primer orden: SNR medido menos el umbral de demodulación de cada SF. Subir de SF no cambia la señal que llega, pero sí baja el umbral, así que el enlace cierra con más holgura. La escalera cubre <strong>SF6 a SF12</strong>: SF6 también es un preset válido, el más rápido y el menos sensible. Y solo se puede corroborar el SF donde alguien escucha:": (
        "El <strong>marge</strong> és un model de primer ordre: SNR mesurat menys el "
        "llindar de demodulació de cada SF. Pujar d'SF no canvia el senyal que "
        "arriba, però sí que baixa el llindar, així que l'enllaç tanca amb més marge. "
        "L'escala cobreix <strong>SF6 a SF12</strong>: SF6 també és un preset vàlid, "
        "el més ràpid i el menys sensible. I només es pot corroborar l'SF on algú "
        "escolta:"
    ),
    "ver los huecos →": "veure els forats →",
    "Medido: {tx} transmisiones/h · {bytes}/h · mediana {median} B en el aire · SNR p50 {snr}": (
        "Mesurat: {tx} transmissions/h · {bytes}/h · mediana {median} B a l'aire · "
        "SNR p50 {snr}"
    ),
    "ruido {dbm}": "soroll {dbm}",
    "{minutes} min con datos": "{minutes} min amb dades",
    "Mezcla real:": "Barreja real:",
    "y {n} tamaños más": "i {n} mides més",
    "Modelo (aire)": "Model (aire)",
    "Aire de 1 transmisión": "Aire d'1 transmissió",
    "lo que tarda un paquete en emitirse": "el que triga un paquet a emetre's",
    "Aire del canal por hora": "Aire del canal per hora",
    "Coste vs SF actual": "Cost vs SF actual",
    "Sensibilidad vs actual": "Sensibilitat vs actual",
    "Margen": "Marge",
    "Sin CR o sin mezcla de tamaños": "Sense CR o sense barreja de mides",
    "no se puede modelar el aire de este canal. Hace falta que el receptor publique <code>/status</code> con su configuración y que los paquetes traigan su trama <code>raw</code>.": (
        "no es pot modelar l'aire d'aquest canal. Cal que el receptor publiqui "
        "<code>/status</code> amb la seva configuració i que els paquets portin la "
        "seva trama <code>raw</code>."
    ),
    "Hoy no hay nada que comparar": "Avui no hi ha res a comparar",
    "Ningún receptor, pareja ni comarca ha estado en más de un canal en esta ventana. Y es lógico: ahora mismo <strong>todo el mundo escucha en 869.618</strong>.": (
        "Cap receptor, parella ni comarca ha estat en més d'un canal en aquesta "
        "finestra. I és lògic: ara mateix <strong>tothom escolta en 869.618</strong>."
    ),
    "Lo que hace falta es mucho más barato de lo que parece: <strong>no</strong> hacen falta muchos receptores por canal, basta con <strong>uno que vaya alternando</strong>. Un receptor que pase 24 h en 869.618 y 24 h en 869.450 aporta una comparación limpia de ruido, errores y alcance, porque mantiene antena, ubicación y hardware. Con veinte receptores repartidos por canales distintos no se consigue eso.": (
        "El que cal és molt més barat del que sembla: <strong>no</strong> calen molts "
        "receptors per canal, n'hi ha prou amb <strong>un que vagi alternant</strong>. "
        "Un receptor que passi 24 h en 869.618 i 24 h en 869.450 aporta una comparació "
        "neta de soroll, errors i abast, perquè manté antena, ubicació i maquinari. "
        "Amb vint receptors repartits per canals diferents no s'aconsegueix això."
    ),
    "Ver qué canales no cubre nadie →": "Veure quins canals no cobreix ningú →",
    "La comparación más limpia que existe: mismo equipo, misma antena, misma ubicación.": (
        "La comparació més neta que existeix: mateix equip, mateixa antena, mateixa "
        "ubicació."
    ),
    "Por pareja": "Per parella",
    "Mismo enlace entre dos receptores, medido en cada canal. El PDR sí es comparable directamente.": (
        "Mateix enllaç entre dos receptors, mesurat a cada canal. El PDR sí que és "
        "comparable directament."
    ),
    "Por comarca": "Per comarca",
    "Compara <strong>el mismo canal</strong> medido por comarcas distintas: misma frecuencia, mismo ancho y mismo SF, así que lo que cambia es el entorno. Al revés —canales distintos dentro de una comarca— se caía en el caso traicionero de «misma frecuencia, distinto SF», donde cada receptor oye a emisores distintos.": (
        "Compara <strong>el mateix canal</strong> mesurat per comarques diferents: "
        "mateixa freqüència, mateixa amplada i mateix SF, així que el que canvia és "
        "l'entorn. Al revés —canals diferents dins d'una comarca— es queia en el cas "
        "traïdor de «mateixa freqüència, SF diferent», on cada receptor sent emissors "
        "diferents."
    ),
    "Aun así <strong>no controla el receptor</strong>: cada comarca tiene el suyo, con su antena y su ubicación. Orienta, no concluye. La columna <em>(ref)</em> es la comarca con más receptores, y las demás llevan su <strong>diferencia</strong> con ella.": (
        "Tot i així <strong>no controla el receptor</strong>: cada comarca té el seu, "
        "amb la seva antena i la seva ubicació. Orienta, no conclou. La columna "
        "<em>(ref)</em> és la comarca amb més receptors, i les altres porten la seva "
        "<strong>diferència</strong> amb ella."
    ),
    "Se solapan {n} min · referencia <strong>{ref}</strong>": (
        "Se solapen {n} min · referència <strong>{ref}</strong>"
    ),
    "Comparación": "Comparació",
    "menos es mejor": "menys és millor",
    "<strong>Canales que no se pueden comparar entre comarcas</strong> en esta ventana:": (
        "<strong>Canals que no es poden comparar entre comarques</strong> en aquesta "
        "finestra:"
    ),
    "solo": "només",
    "sin solapamiento temporal": "sense solapament temporal",
    "Hace falta que <strong>dos comarcas midan el mismo canal</strong> y que coincidan en el tiempo; si el segundo canal existió fuera de la ventana, amplíala para verlo.": (
        "Cal que <strong>dues comarques mesurin el mateix canal</strong> i que "
        "coincideixin en el temps; si el segon canal va existir fora de la finestra, "
        "amplia-la per veure'l."
    ),
    # --- comparar_periodos.html ---
    "Comparar canales por períodos": "Comparar canals per períodes",
    "<strong>Aquí el efecto del tiempo juega en contra.</strong> Comparar una semana con otra elimina la confusión por geografía y equipo, pero introduce la propagación, la meteorología y cuánta gente estaba activa. Una diferencia entre dos semanas puede no tener nada que ver con el canal.": (
        "<strong>Aquí l'efecte del temps juga en contra.</strong> Comparar una setmana "
        "amb una altra elimina la confusió per geografia i equip, però introdueix la "
        "propagació, la meteorologia i quanta gent estava activa. Una diferència entre "
        "dues setmanes pot no tenir res a veure amb el canal."
    ),
    "Por eso lo que convierte esto en una conclusión es el <strong>control</strong>: otro período en el <em>mismo</em> canal que A. Si entre dos períodos del mismo canal ya hay una diferencia, una diferencia parecida entre canales no demuestra nada.": (
        "Per això el que converteix això en una conclusió és el <strong>control</strong>: "
        "un altre període en el <em>mateix</em> canal que A. Si entre dos períodes del "
        "mateix canal ja hi ha una diferència, una diferència semblant entre canals no "
        "demostra res."
    ),
    "No se ha detectado ningún período": "No s'ha detectat cap període",
    "Hacen falta al menos {n} minutos seguidos con un canal dominante claro para que un tramo cuente como período. O no hay datos suficientes todavía, o la red ha estado migrando y ningún canal llegó a dominar.": (
        "Calen almenys {n} minuts seguits amb un canal dominant clar perquè un tram "
        "compti com a període. O no hi ha dades suficients encara, o la xarxa ha estat "
        "migrant i cap canal va arribar a dominar."
    ),
    "Solo hay un período, así que no hay nada que comparar": (
        "Només hi ha un període, així que no hi ha res a comparar"
    ),
    "La red ha estado todo el tiempo en <strong>{channel}</strong> ({minutes} minutos). Para comparar hacen falta al menos dos períodos en canales distintos — es decir, una campaña de verdad:": (
        "La xarxa ha estat tot el temps en <strong>{channel}</strong> ({minutes} "
        "minuts). Per comparar calen almenys dos períodes en canals diferents — és a "
        "dir, una campanya de debò:"
    ),
    "cómo organizarla": "com organitzar-la",
    "No se pueden comparar esos dos períodos": "No es poden comparar aquests dos períodes",
    "Prueba a elegir otros dos en el selector.": "Prova de triar-ne dos altres al selector.",
    "Período A": "Període A",
    "Período B": "Període B",
    "desde": "des de",
    "<strong>Alguno de los dos períodos no fue un régimen limpio.</strong> Fracción de receptores en el canal dominante: {a} % y {b} %. Por debajo del 80 % la red estaba a medio migrar, y eso difumina la comparación.": (
        "<strong>Algun dels dos períodes no va ser un règim net.</strong> Fracció de "
        "receptors en el canal dominant: {a} % i {b} %. Per sota del 80 % la xarxa "
        "estava mig migrant, i això difumina la comparació."
    ),
    "Resumen por métrica": "Resum per mètrica",
    "Control: <strong>{label}</strong>, el mismo canal que A en otro momento. Las dos diferencias se calculan sobre los mismos sujetos.": (
        "Control: <strong>{label}</strong>, el mateix canal que A en un altre moment. "
        "Les dues diferències es calculen sobre els mateixos subjectes."
    ),
    "<strong>Sin control.</strong> No hay ningún otro período en el mismo canal que A, así que <strong>no se puede saber cuánta diferencia es simple paso del tiempo</strong>. Los números de abajo son sugerentes, pero no concluyentes. Un diseño A-B-A (volver al canal de partida) daría ese control.": (
        "<strong>Sense control.</strong> No hi ha cap altre període en el mateix canal "
        "que A, així que <strong>no es pot saber quanta diferència és simple pas del "
        "temps</strong>. Els números de sota són suggeridors, però no concloents. Un "
        "disseny A-B-A (tornar al canal de partida) donaria aquest control."
    ),
    "Sujetos": "Subjectes",
    "Diferencia A → B": "Diferència A → B",
    "Diferencia A → control": "Diferència A → control",
    "¿Supera el ruido temporal?": "¿Supera el soroll temporal?",
    "sí": "sí",
    "no": "no",
    "Cobertura": "Cobertura",
    "<strong>{n}</strong> sujetos aparecen en los dos períodos y son los únicos que se comparan. Los que solo están en uno llegaron o se fueron, y meterlos mezclaría el efecto canal con el de quién estaba escuchando.": (
        "<strong>{n}</strong> subjectes apareixen en els dos períodes i són els únics "
        "que es comparen. Els que només hi són en un van arribar o se'n van anar, i "
        "ficar-los-hi barrejaria l'efecte canal amb el de qui estava escoltant."
    ),
    "Control": "Control",
    # --- campana.html ---
    "<strong>Esto es el límite duro del observatorio.</strong> Solo se puede comparar lo que alguien está escuchando. Una combinación de frecuencia, ancho y SF sin receptores encima no produce datos, y ninguna cantidad de análisis la va a inventar. Esta página existe para ver exactamente dónde están los huecos.": (
        "<strong>Aquest és el límit dur de l'observatori.</strong> Només es pot "
        "comparar allò que algú està escoltant. Una combinació de freqüència, amplada "
        "i SF sense receptors a sobre no produeix dades, i cap quantitat d'anàlisi la "
        "inventarà. Aquesta pàgina existeix per veure exactament on són els forats."
    ),
    "Estado de cada configuración conocida, con cuántos observadores la tienen puesta. Lo que se busca es que los cuatro slots estrechos de h1.4 tengan cobertura simultánea para poder compararlos con las mismas condiciones de propagación.": (
        "Estat de cada configuració coneguda, amb quants observadors la tenen posada. "
        "El que es busca és que els quatre slots estrets de h1.4 tinguin cobertura "
        "simultània per poder comparar-los amb les mateixes condicions de propagació."
    ),
    "Última vez visto": "Última vegada vist",
    "cubierto": "cobert",
    "sin nadie escuchando": "sense ningú escoltant",
    "Qué haría falta": "Què caldria",
    "<strong>Un receptor por slot, a la vez.</strong> Comparar 869.431 contra 869.619 solo tiene sentido si las dos se miden en la misma franja horaria: la propagación cambia con la hora y con el día.": (
        "<strong>Un receptor per slot, alhora.</strong> Comparar 869.431 contra "
        "869.619 només té sentit si les dues es mesuren en la mateixa franja horària: "
        "la propagació canvia amb l'hora i amb el dia."
    ),
    "<strong>Separación geográfica.</strong> Con receptores del mismo sitio, el PDR sale cercano al 100% y no informa de nada. Los pares que no oyen nada en común son los que dicen algo sobre cobertura.": (
        "<strong>Separació geogràfica.</strong> Amb receptors del mateix lloc, el PDR "
        "surt a prop del 100% i no informa de res. Les parelles que no senten res en "
        "comú són les que diuen alguna cosa sobre cobertura."
    ),
    "<strong>Status activo.</strong> Un observador que no publica <code>/status</code> con <code>radio</code> no se puede atribuir a ningún canal; sus paquetes se descartan del análisis. En algunos despliegues basta con bajar <code>PACKETCAPTURE_STATS_REFRESH_INTERVAL</code> y comprobar que el status está habilitado.": (
        "<strong>Status actiu.</strong> Un observador que no publica <code>/status</code> "
        "amb <code>radio</code> no es pot atribuir a cap canal; els seus paquets es "
        "descarten de l'anàlisi. En alguns desplegaments n'hi ha prou amb baixar "
        "<code>PACKETCAPTURE_STATS_REFRESH_INTERVAL</code> i comprovar que el status "
        "està habilitat."
    ),
    "Protocolo, si lo que se quiere es mover toda la red": (
        "Protocol, si el que es vol és moure tota la xarxa"
    ),
    "Esta es la parte que convierte el observatorio en algo que sirve para decidir. La comparación entre períodos existe, pero <strong>un experimento mal hecho no lo arregla ningún análisis posterior</strong>.": (
        "Aquesta és la part que converteix l'observatori en una cosa que serveix per "
        "decidir. La comparació entre períodes existeix, però <strong>un experiment "
        "mal fet no l'arregla cap anàlisi posterior</strong>."
    ),
    "<strong>Mover a todos a la vez, no por goteo.</strong> Si la mitad de la red se muda el martes y la otra mitad el viernes, dos períodos se convierten en cuatro transiciones y ya no hay nada que comparar. Se acuerda día y hora, y se cambia todo junto.": (
        "<strong>Moure tothom alhora, no gota a gota.</strong> Si la meitat de la "
        "xarxa es trasllada dimarts i l'altra meitat divendres, dos períodes es "
        "converteixen en quatre transicions i ja no hi ha res a comparar. S'acorda dia "
        "i hora, i es canvia tot junt."
    ),
    "<strong>A-B-A, no solo A-B.</strong> Una semana en A, una en B y <em>otra vuelta a A</em>. Las dos semanas en A son el <strong>control</strong>: miden cuánta diferencia provoca el simple paso del tiempo. Sin eso, la herramienta no puede distinguir el canal de la meteorología, y lo dirá en vez de dar un veredicto.": (
        "<strong>A-B-A, no només A-B.</strong> Una setmana en A, una en B i <em>una "
        "altra tornada a A</em>. Les dues setmanes en A són el <strong>control</strong>: "
        "mesuren quanta diferència provoca el simple pas del temps. Sense això, l'eina "
        "no pot distingir el canal de la meteorologia, i ho dirà en comptes de donar "
        "un veredicte."
    ),
    "<strong>Misma duración y mismos receptores</strong> en cada período. El análisis solo compara los nodos presentes en los dos, así que añadir gente a mitad de campaña no suma: resta.": (
        "<strong>Mateixa durada i mateixos receptors</strong> a cada període. "
        "L'anàlisi només compara els nodes presents en els dos, així que afegir gent a "
        "mitja campanya no suma: resta."
    ),
    "<strong>Anotar la fecha y hora exactas del cambio.</strong> Son las que delimitan los períodos. La detección automática las deduce de los datos, pero un cambio apuntado evita discusiones.": (
        "<strong>Anotar la data i l'hora exactes del canvi.</strong> Són les que "
        "delimiten els períodes. La detecció automàtica les dedueix de les dades, però "
        "un canvi apuntat evita discussions."
    ),
    "<strong>Elegir bien qué se compara.</strong> Tiene sentido probar un slot claramente distinto, por ejemplo 869.450 frente al 869.618 actual, y no dos que ya compartan frecuencia: dos receptores con SF distinto <em>no se oyen entre sí</em>, así que sus métricas describen poblaciones distintas de emisores.": (
        "<strong>Triar bé què es compara.</strong> Té sentit provar un slot clarament "
        "diferent, per exemple 869.450 davant del 869.618 actual, i no dos que ja "
        "comparteixin freqüència: dos receptors amb SF diferent <em>no es senten entre "
        "si</em>, així que les seves mètriques descriuen poblacions diferents "
        "d'emissors."
    ),
    "<strong>Qué mirar en los resultados.</strong> En": (
        "<strong>Què cal mirar en els resultats.</strong> A"
    ),
    "Comparar → Por períodos": "Comparar → Per períodes",
    ", la columna que importa es la que enfrenta la diferencia entre canales con la diferencia entre dos períodos del mismo canal.": (
        ", la columna que importa és la que enfronta la diferència entre canals amb la "
        "diferència entre dos períodes del mateix canal."
    ),
    "Y qué <strong>no</strong> concluir: si las dos diferencias son parecidas, la respuesta honesta es que <em>no se puede concluir nada</em>. Significa que la variación normal de la red entre dos semanas es tan grande como el efecto que se quería medir, y eso no es un fallo de la herramienta, es lo que dicen los datos.": (
        "I què <strong>no</strong> s'ha de concloure: si les dues diferències són "
        "semblants, la resposta honesta és que <em>no es pot concloure res</em>. "
        "Significa que la variació normal de la xarxa entre dues setmanes és tan gran "
        "com l'efecte que es volia mesurar, i això no és una fallada de l'eina, és el "
        "que diuen les dades."
    ),
    "Todavía no hay configuraciones registradas": "Encara no hi ha configuracions registrades",
    "Aparecerán solas en cuanto el primer observador publique su <code>/status</code>.": (
        "Apareixeran totes soles quan el primer observador publiqui el seu "
        "<code>/status</code>."
    ),
    # --- calidad.html ---
    "Un paquete sin atribuir no es lo mismo que un paquete perdido, y tampoco todos los «sin atribuir» son la misma cosa. Esta página separa las causas para que un caso irreductible no parezca un fallo del sistema.": (
        "Un paquet sense atribuir no és el mateix que un paquet perdut, i tampoc tots "
        "els «sense atribuir» són la mateixa cosa. Aquesta pàgina separa les causes "
        "perquè un cas irreductible no sembli una fallada del sistema."
    ),
    "Causa": "Causa",
    "Qué es": "Què és",
    "¿Se puede evitar?": "¿Es pot evitar?",
    "Ventana de transición": "Finestra de transició",
    "El receptor cambió de configuración entre dos <code>/status</code>. No se sabe en qué instante exacto ocurrió.": (
        "El receptor va canviar de configuració entre dos <code>/status</code>. No se "
        "sap en quin instant exacte va passar."
    ),
    "No. Se acota a la cadencia del status y el paquete se marca como dudoso.": (
        "No. S'acota a la cadència del status i el paquet es marca com a dubtós."
    ),
    "Hueco largo sin status": "Forat llarg sense status",
    "El receptor lleva más de 10 minutos sin publicar <code>/status</code>.": (
        "El receptor porta més de 10 minuts sense publicar <code>/status</code>."
    ),
    "No. El paquete se marca como dudoso.": "No. El paquet es marca com a dubtós.",
    "Primera aparición": "Primera aparició",
    "El receptor empezó a emitir antes de publicar su primer <code>/status</code> con <code>radio</code>.": (
        "El receptor va començar a emetre abans de publicar el seu primer "
        "<code>/status</code> amb <code>radio</code>."
    ),
    "No. Esa información no existía todavía en ningún sitio.": (
        "No. Aquesta informació encara no existia enlloc."
    ),
    "Sin status utilizable": "Sense status utilitzable",
    "Solo publica avisos <code>offline</code>, sin <code>radio</code>.": (
        "Només publica avisos <code>offline</code>, sense <code>radio</code>."
    ),
    "Sí, pidiendo al operador que lo active.": "Sí, demanant a l'operador que l'activi.",
    "Conflicto de hash": "Conflicte de hash",
    "El mismo hash aparece en dos <em>canales físicos</em> distintos. Una transmisión no puede oírse en dos frecuencias: una de las dos atribuciones está mal.": (
        "El mateix hash apareix en dos <em>canals físics</em> diferents. Una "
        "transmissió no es pot sentir en dues freqüències: una de les dues atribucions "
        "està malament."
    ),
    "Es la comprobación de sanidad. Debería quedarse en cero.": (
        "És la comprovació de santedat. Hauria de quedar-se a zero."
    ),
    "Últimos días": "Últims dies",
    "Día": "Dia",
    "Observadores sin status": "Observadors sense status",
    "Dudosos": "Dubtosos",
    "Sin atribuir": "Sense atribuir",
    "Conflictos de hash": "Conflictes de hash",
    "Los paquetes <strong>dudosos y sin atribuir no entran en ningún agregado</strong>: quedan fuera de las medias, del PDR y de los rankings. Preferimos perder unas pocas muestras a que un dato ambiguo sesgue la comparación sin que se note.": (
        "Els paquets <strong>dubtosos i sense atribuir no entren en cap agregat</strong>: "
        "queden fora de les mitjanes, del PDR i dels rànquings. Preferim perdre unes "
        "quantes mostres que no pas que una dada ambigua esbiaixi la comparació sense "
        "que es noti."
    ),
    "Sin métricas de calidad todavía": "Sense mètriques de qualitat encara",
    "Se calculan junto con el resto de agregados, cada cinco minutos.": (
        "Es calculen juntament amb la resta d'agregats, cada cinc minuts."
    ),
    # --- metodologia.html ---
    "De dónde sale cada número, qué se puede sumar y qué no, y las trampas que hacen que una lectura ingenua de las tablas lleve a conclusiones falsas.": (
        "D'on surt cada número, què es pot sumar i què no, i les trampes que fan que "
        "una lectura ingènua de les taules porti a conclusions falses."
    ),
    "El canal físico es la unidad": "El canal físic és la unitat",
    "Un <strong>canal físico</strong> es <code>(frecuencia, ancho, SF)</code>. La <strong>CR no entra</strong>, y no es un descuido: la tasa de codificación viaja en la cabecera LoRa, así que dos receptores con CR distinta sobre la misma frecuencia y SF decodifican exactamente la misma transmisión.": (
        "Un <strong>canal físic</strong> és <code>(freqüència, amplada, SF)</code>. La "
        "<strong>CR no hi entra</strong>, i no és un descuit: la taxa de codificació "
        "viatja a la capçalera LoRa, així que dos receptors amb CR diferent sobre la "
        "mateixa freqüència i SF descodifiquen exactament la mateixa transmissió."
    ),
    "Medido sobre este broker: <strong>8 receptores con 2 CR distintas</strong> oyendo el mismo hash dentro de un solo canal físico, y <strong>0 hashes</strong> repartidos entre dos canales. Sumar por configuración de receptor inflaba las transmisiones distintas un <strong>30%</strong> (74 frente a 57 en la primera medición).": (
        "Mesurat sobre aquest broker: <strong>8 receptors amb 2 CR diferents</strong> "
        "sentint el mateix hash dins d'un sol canal físic, i <strong>0 hashes</strong> "
        "repartits entre dos canals. Sumar per configuració de receptor inflava les "
        "transmissions diferents un <strong>30%</strong> (74 contra 57 en la primera "
        "mesura)."
    ),
    "Además, el decoder <strong>no expone</strong> la CR con la que se emitió un paquete: el <code>raw</code> empieza después de la cabecera. La CR solo se conoce como configuración del receptor, que es lo que publica <code>/status</code>.": (
        "A més, el decoder <strong>no exposa</strong> la CR amb què es va emetre un "
        "paquet: el <code>raw</code> comença després de la capçalera. La CR només es "
        "coneix com a configuració del receptor, que és el que publica <code>/status</code>."
    ),
    "Qué se mide, y con qué": "Què es mesura, i amb què",
    "Cómo": "Com",
    "Fuente": "Font",
    "Media de <code>noise_floor</code> del receptor en la ventana.": (
        "Mitjana de <code>noise_floor</code> del receptor a la finestra."
    ),
    "<code>/status</code>. Lo muestrea el firmware: media de 64 lecturas de RSSI tomadas en silencio, recalculada cada ~2 s.": (
        "<code>/status</code>. Ho mostreja el firmware: mitjana de 64 lectures de RSSI "
        "preses en silenci, recalculada cada ~2 s."
    ),
    "Errores": "Errors",
    "<code>Δ(recv_errors) / Δt</code>": "<code>Δ(recv_errors) / Δt</code>",
    "<code>Δ(tx_air + rx_air) / Δt × 100</code>": "<code>Δ(tx_air + rx_air) / Δt × 100</code>",
    "<code>/status</code>. Es <em>derivada</em>: el firmware no la calcula.": (
        "<code>/status</code>. És <em>derivada</em>: el firmware no la calcula."
    ),
    "<code>/status</code>. Es un proxy de CRC y colisiones, no un contador dedicado.": (
        "<code>/status</code>. És un proxy de CRC i col·lisions, no un comptador dedicat."
    ),
    "SNR / RSSI": "SNR / RSSI",
    "Mediana del percentil 50 por minuto, ponderada por volumen.": (
        "Mediana del percentil 50 per minut, ponderada per volum."
    ),
    "<code>/packets</code>. Es una <strong>aproximación</strong>: para el percentil exacto hay que ir a los paquetes crudos.": (
        "<code>/packets</code>. És una <strong>aproximació</strong>: per al percentil "
        "exacte cal anar als paquets crus."
    ),
    "Hashes distintos. Una transmisión cuenta una vez aunque la oigan ocho receptores.": (
        "Hashes diferents. Una transmissió compta una vegada encara que la sentin vuit "
        "receptors."
    ),
    "Cuántas veces se ha oído algo, sumando receptores.": (
        "Quantes vegades s'ha sentit alguna cosa, sumant receptors."
    ),
    "<code>|A∩B| / |A∪B|</code> sobre hashes, dentro del mismo canal físico y minuto.": (
        "<code>|A∩B| / |A∪B|</code> sobre hashes, dins del mateix canal físic i minut."
    ),
    "Longitud de la trama <strong>en el aire</strong> (<code>raw</code>) de las transmisiones <strong>distintas</strong> (una vez por hash).": (
        "Longitud de la trama <strong>a l'aire</strong> (<code>raw</code>) de les "
        "transmissions <strong>diferents</strong> (una vegada per hash)."
    ),
    "<code>/packets</code>. <strong>Medido.</strong>": (
        "<code>/packets</code>. <strong>Mesurat.</strong>"
    ),
    "Tiempo de aire": "Temps d'aire",
    "Fórmula de Semtech sobre la <strong>mezcla real de tamaños</strong>, a cada SF de la escalera.": (
        "Fórmula de Semtech sobre la <strong>barreja real de mides</strong>, a cada SF "
        "de l'escala."
    ),
    "<strong>Modelo</strong>, no medición: el SF de emisión no se publica.": (
        "<strong>Model</strong>, no mesura: l'SF d'emissió no es publica."
    ),
    "Se guardan <strong>tres niveles</strong> porque no hay uno solo correcto: el canal físico para el tráfico, el preset exacto para saber qué ve un receptor concreto, y el observador para ruido, ocupación y errores.": (
        "Es guarden <strong>tres nivells</strong> perquè no n'hi ha un de sol correcte: "
        "el canal físic per al trànsit, el preset exacte per saber què veu un receptor "
        "concret, i l'observador per a soroll, ocupació i errors."
    ),
    "El coste de cambiar de SF: bytes medidos, aire modelado": (
        "El cost de canviar d'SF: bytes mesurats, aire modelat"
    ),
    "Los <strong>bytes</strong> son una medición: se suma la longitud de la trama <strong>en el aire</strong> (<code>raw</code>) de las transmisiones <strong>distintas</strong>. La deduplicación por hash importa —una transmisión que oyen ocho receptores aparece ocho veces en el crudo—, y sin ella el tráfico se multiplicaría por el número de receptores.": (
        "Els <strong>bytes</strong> són una mesura: se suma la longitud de la trama "
        "<strong>a l'aire</strong> (<code>raw</code>) de les transmissions "
        "<strong>diferents</strong>. La deduplicació per hash importa —una transmissió "
        "que sentin vuit receptors apareix vuit vegades en el cru—, i sense ella el "
        "trànsit es multiplicaria pel nombre de receptors."
    ),
    "Se usa la trama en el aire y <strong>no</strong> el <code>payload_len</code> del broker, que es el payload de <em>aplicación</em>: en un Ack de 6 B, la trama en el aire llega a 20-24 B, porque la cabecera y la ruta pesan más que el mensaje.": (
        "S'usa la trama a l'aire i <strong>no</strong> el <code>payload_len</code> del "
        "broker, que és el payload d'<em>aplicació</em>: en un Ack de 6 B, la trama a "
        "l'aire arriba a 20-24 B, perquè la capçalera i la ruta pesen més que el missatge."
    ),
    "Y se modela la <strong>mezcla real de tamaños</strong>, no un paquete medio. El tráfico de una malla es <strong>bimodal</strong> —control pequeño por un lado, adverts grandes por otro—, así que su media cae en un valle y no describe ningún paquete que exista. Por eso el panel enseña la mediana y la mezcla, y el aire se calcula tamaño a tamaño.": (
        "I es modela la <strong>barreja real de mides</strong>, no un paquet mitjà. El "
        "trànsit d'una malla és <strong>bimodal</strong> —control petit per una banda, "
        "adverts grans per l'altra—, així que la seva mitjana cau en una vall i no "
        "descriu cap paquet que existeixi. Per això el panell ensenya la mediana i la "
        "barreja, i l'aire es calcula mida a mida."
    ),
    "El <strong>tiempo de aire</strong> es un modelo, no una medición, porque el broker solo publica con qué configuración <em>escucha</em> cada receptor, nunca con cuál se emitió. (Matiz: como SF y ancho <strong>tienen</strong> que coincidir para demodular, el SF del canal sí es el de emisión; lo que no se conoce es la CR con la que se emitió.) Se calcula con la fórmula de Semtech (AN1200.13) sobre esa mezcla:": (
        "El <strong>temps d'aire</strong> és un model, no una mesura, perquè el broker "
        "només publica amb quina configuració <em>escolta</em> cada receptor, mai amb "
        "quina es va emetre. (Matís: com que SF i amplada <strong>han</strong> de "
        "coincidir per demodular, l'SF del canal sí que és el d'emissió; el que no es "
        "coneix és la CR amb què es va emetre.) Es calcula amb la fórmula de Semtech "
        "(AN1200.13) sobre aquesta barreja:"
    ),
    "El caso de «cambiar de SF» mantiene el ancho y recorre <strong>SF6…SF12</strong> —SF6 es válido y es el más rápido y el menos sensible—. Cada paso de SF dobla aproximadamente el aire y baja unos <strong>2,5 dB</strong> el umbral de demodulación: eso es lo que se gana y lo que se paga.": (
        "El cas de «canviar d'SF» manté l'amplada i recorre <strong>SF6…SF12</strong> "
        "—SF6 és vàlid i és el més ràpid i el menys sensible—. Cada pas d'SF dobla "
        "aproximadament l'aire i baixa uns <strong>2,5 dB</strong> el llindar de "
        "demodulació: això és el que es guanya i el que es paga."
    ),
    "El <strong>margen</strong> que se muestra es un modelo de primer orden: <code>SNR medido − umbral del SF</code>. Subir de SF no cambia la señal que llega, pero sí baja el umbral, así que el enlace cierra con más holgura. Es una estimación de sensibilidad, no un enlace medido, y por eso va etiquetada como modelo.": (
        "El <strong>marge</strong> que es mostra és un model de primer ordre: "
        "<code>SNR mesurat − llindar de l'SF</code>. Pujar d'SF no canvia el senyal que "
        "arriba, però sí que baixa el llindar, així que l'enllaç tanca amb més marge. "
        "És una estimació de sensibilitat, no un enllaç mesurat, i per això va "
        "etiquetada com a model."
    ),
    "Ojo con la confusión fácil: la <strong>ocupación</strong> que ya existía (<code>chan_util_pct</code>, derivada de <code>tx_air_secs</code> y <code>rx_air_secs</code>) <strong>sí</strong> es medida, y es otra cosa: mide cuánto estuvo ocupado el receptor, no cuánto costaría emitir un payload a otro SF.": (
        "Compte amb la confusió fàcil: l'<strong>ocupació</strong> que ja existia "
        "(<code>chan_util_pct</code>, derivada de <code>tx_air_secs</code> i "
        "<code>rx_air_secs</code>) <strong>sí</strong> que és mesurada, i és una altra "
        "cosa: mesura quant estona va estar ocupat el receptor, no quant costaria "
        "emetre un payload a un altre SF."
    ),
    "Siete trampas que hay que tener presentes": "Set trampes que cal tenir presents",
    "1. La interferencia no aparece como paquetes": (
        "1. La interferència no apareix com a paquets"
    ),
    "MeshCore filtra por <em>sync word</em>. El tráfico de Meshtastic y de LoRaWAN <strong>no llega a la aplicación</strong>: se descarta en el hardware. La firma de la interferencia es una <strong>subida del ruido de fondo y de los errores</strong>, no tráfico ajeno decodificable. Cualquier gráfico que prometa «paquetes de Meshtastic detectados» estaría mintiendo.": (
        "MeshCore filtra per <em>sync word</em>. El trànsit de Meshtastic i de LoRaWAN "
        "<strong>no arriba a l'aplicació</strong>: es descarta al maquinari. La "
        "signatura de la interferència és una <strong>pujada del soroll de fons i dels "
        "errors</strong>, no trànsit aliè decodificable. Qualsevol gràfic que prometi "
        "«paquets de Meshtastic detectats» estaria mentint."
    ),
    "2. El techo de frescura lo pone la fuente": "2. El sostre de frescor el posa la font",
    "El ruido, la ocupación y los errores se publican cada <strong>300 s</strong> (medido: 11 intervalos de exactamente 300 s, uno de 60 s). Ninguna parte del sistema puede ser más fresca que eso, y el intervalo de escritura no cambia nada de esto.": (
        "El soroll, l'ocupació i els errors es publiquen cada <strong>300 s</strong> "
        "(mesurat: 11 intervals d'exactament 300 s, un de 60 s). Cap part del sistema "
        "pot ser més fresca que això, i l'interval d'escriptura no canvia res d'això."
    ),
    "3. «¿Qué canal es mejor?» es una pregunta de distribuciones": (
        "3. «Quin canal és millor?» és una pregunta de distribucions"
    ),
    "Un paquete suelto a SNR +12 dB no dice nada. Las tablas son medias y medianas sobre una ventana precisamente porque un valor instantáneo es ruido, no señal.": (
        "Un paquet solt a SNR +12 dB no diu res. Les taules són mitjanes i medianes "
        "sobre una finestra precisament perquè un valor instantani és soroll, no senyal."
    ),
    "4. Sin receptores no hay comparación": "4. Sense receptors no hi ha comparació",
    "Solo se puede comparar lo que alguien escucha. En la primera medición los slots 2 y 3 (869.493 y 869.556) estaban vacíos.": (
        "Només es pot comparar el que algú escolta. En la primera mesura els slots 2 i "
        "3 (869.493 i 869.556) estaven buits."
    ),
    "Ver los huecos.": "Veure els forats.",
    "5. La ventana de transición es ambigua": "5. La finestra de transició és ambigua",
    "Si un receptor cambia de configuración, el cambio ocurrió en algún punto entre los dos <code>/status</code> que lo delimitan. Los paquetes de ese hueco se marcan como dudosos y quedan fuera de los agregados. No se adivina a qué configuración pertenecían.": (
        "Si un receptor canvia de configuració, el canvi va passar en algun punt entre "
        "els dos <code>/status</code> que el delimiten. Els paquets d'aquest forat es "
        "marquen com a dubtosos i queden fora dels agregats. No s'endevina a quina "
        "configuració pertanyien."
    ),
    "6. El ranking es heurístico": "6. El rànquing és heurístic",
    "El índice de limpieza combina ruido (0,35), SNR (0,25), errores (0,25) y ocupación (0,15) con pesos elegidos a mano. Es una ayuda para ordenar, no un veredicto: en las tablas van siempre los valores crudos al lado para poder discrepar.": (
        "L'índex de netedat combina soroll (0,35), SNR (0,25), errors (0,25) i ocupació "
        "(0,15) amb pesos triats a mà. És una ajuda per ordenar, no un veredicte: a les "
        "taules sempre hi van els valors crus al costat per poder discrepar."
    ),
    "7. El tiempo de aire es modelado, no medido": (
        "7. El temps d'aire és modelat, no mesurat"
    ),
    "El broker no publica con qué SF se <strong>emitió</strong> una transmisión, solo con cuál <strong>escucha</strong> cada receptor. Así que el aire de la escalera SF7…SF12 es un cálculo sobre el payload observado, no una observación. Y el margen de sensibilidad es de primer orden. Sirve para decidir, no para certificar.": (
        "El broker no publica amb quin SF es va <strong>emetre</strong> una "
        "transmissió, només amb quin <strong>escolta</strong> cada receptor. Així que "
        "l'aire de l'escala SF7…SF12 és un càlcul sobre el payload observat, no una "
        "observació. I el marge de sensibilitat és de primer ordre. Serveix per "
        "decidir, no per certificar."
    ),
    "Sobre el hash": "Sobre el hash",
    "El <code>hash</code> que publica el broker es <strong>idéntico entre receptores</strong> para la misma transmisión — comprobado: un mismo hash lo oyeron 8 receptores distintos. Es lo que permite cruzar observadores y calcular el PDR. Ojo: no es el mismo valor que el <code>message_hash</code> que devuelve el decodificador de paquetes; son dos cosas distintas y aquí se usa el del broker.": (
        "El <code>hash</code> que publica el broker és <strong>idèntic entre "
        "receptors</strong> per a la mateixa transmissió — comprovat: un mateix hash "
        "el van sentir 8 receptors diferents. És el que permet creuar observadors i "
        "calcular el PDR. Compte: no és el mateix valor que el <code>message_hash</code> "
        "que retorna el decodificador de paquets; són dues coses diferents i aquí "
        "s'usa el del broker."
    ),
}

_CATALOGS: dict[str, dict[str, str]] = {
    "ca": {normalize(key): value for key, value in CATALAN.items()},
}
