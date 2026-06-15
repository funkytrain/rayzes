"""
gramps_link_images.py
---------------------
Asocia imágenes de archivos eclesiásticos a eventos (o personas) en un archivo .gramps.

Modos de uso:
  1. Procesar carpeta de imágenes (primera vez o con imágenes nuevas):
       python gramps_link_images.py --gramps ARCHIVO.gramps --imagenes CARPETA [--dry-run]

  2. Aplicar confirmaciones manuales del CSV de pendientes:
       python gramps_link_images.py --gramps ARCHIVO.gramps --imagenes CARPETA --confirmar

  Flujo para los "sin coincidencia":
    - Tras el paso 1 se genera gramps_sin_coincidencia.csv con sugerencias difusas.
    - Abre el CSV en Excel, revisa cada fila y rellena la columna "gramps_id_confirmado"
      con el ID de GRAMPS correcto (p.ej. I0123) o déjala vacía para descartar.
    - Ejecuta el paso 2 para aplicar los confirmados.

Formato de nombre de archivo esperado:
  TIPO NOMBRE_COMPLETO AÑO [numero_parte].ext
  TIPO NOMBRE1 Y NOMBRE2 AÑO [numero_parte].ext  (matrimonios)
"""

import argparse
import csv
import difflib
import gzip
import json
import re
import shutil
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

# ── Tipos de evento reconocidos ──────────────────────────────────────────────
TIPOS_EVENTO = {
    "BAUTISMO":      "Baptism",
    "BAUTISMO/A":    "Baptism",
    "DEFUNCION":     "Death",
    "DEFUNCIÓN":     "Death",
    "MATRIMONIO":    "Marriage",
    "CONFIRMACION":  "Confirmation",
    "CONFIRMACIÓN":  "Confirmation",
    "VELACION":      "Marriage",
    "VELACIÓN":      "Marriage",
    "NACIMIENTO":    "Birth",
    "ENTIERRO":      "Burial",
}

NS = "http://gramps-project.org/xml/1.7.2/"
ET.register_namespace("", NS)

REGISTRO_JSON  = "gramps_imagenes_procesadas.json"
PENDIENTES_CSV = "gramps_sin_coincidencia.csv"

# Umbral de similitud exacta (tokens) para aceptar automáticamente
UMBRAL_AUTO = 0.6
# Umbral mínimo para proponer como sugerencia difusa
UMBRAL_SUGERENCIA = 0.30
# Número de sugerencias difusas a incluir en el CSV
N_SUGERENCIAS = 3


# ── Utilidades de normalización ──────────────────────────────────────────────

def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto.lower().strip()


def tokens(texto: str) -> set:
    return set(re.split(r"\s+", normalizar(texto).strip())) - {""}


def sim_tokens(nombre_archivo: str, nombre_gramps: str) -> float:
    """Fracción de tokens del archivo que aparecen en el nombre GRAMPS."""
    t_arch = tokens(nombre_archivo)
    t_gramps = tokens(nombre_gramps)
    if not t_arch:
        return 0.0
    return len(t_arch & t_gramps) / len(t_arch)


def sim_difusa(nombre_archivo: str, nombre_gramps: str) -> float:
    """Similitud de cadena completa normalizada (SequenceMatcher)."""
    a = normalizar(nombre_archivo)
    b = normalizar(nombre_gramps)
    return difflib.SequenceMatcher(None, a, b).ratio()


def sim_combinada(nombre_archivo: str, nombre_gramps: str) -> float:
    """Media ponderada: 60% tokens + 40% cadena completa."""
    return 0.6 * sim_tokens(nombre_archivo, nombre_gramps) + \
           0.4 * sim_difusa(nombre_archivo, nombre_gramps)


# ── Parseo del nombre de archivo ─────────────────────────────────────────────

PATRON = re.compile(
    r"^(?P<tipo>[A-ZÁÉÍÓÚÑ/]+)"
    r"\s+"
    r"(?P<nombre>.+?)"
    r"(?:"
        r"\s+(?P<anyo>\d{4})"
        r"(?:"
            r"(?:\s+|\s*-\s*|\s*\(\s*)(?P<parte>\d+)(?:\s*\))?"
            r"(?:\.\d+)?"
        r")?"
    r")?"
    r"\s*$",
    re.IGNORECASE,
)


def parsear_nombre(stem: str):
    m = PATRON.match(stem.strip())
    if not m:
        return None
    tipo_raw   = m.group("tipo").upper().replace(" ", "")
    nombre_raw = m.group("nombre")
    anyo       = int(m.group("anyo")) if m.group("anyo") else None
    parte      = int(m.group("parte")) if m.group("parte") else 1
    tipo_gramps = TIPOS_EVENTO.get(tipo_raw)
    return tipo_gramps, nombre_raw, anyo, parte


# ── Construcción de índices desde el XML ────────────────────────────────────

def cargar_gramps(ruta: str):
    """Devuelve (xml_texto_original, root_ET_para_lectura)."""
    with gzip.open(ruta, "rt", encoding="utf-8") as f:
        contenido = f.read()
    # Quitar xmlns solo para el árbol ET (lectura), el texto original queda intacto
    contenido_sin_ns = re.sub(r'\sxmlns="[^"]+"', "", contenido, count=1)
    root = ET.fromstring(contenido_sin_ns)
    return contenido, root  # contenido = original con xmlns preservado


def nombre_completo_persona(person_el):
    name_el = person_el.find("name[@type='Birth Name']")
    if name_el is None:
        name_el = person_el.find("name")
    if name_el is None:
        return ""
    first   = (name_el.findtext("first")   or "").strip()
    surname = (name_el.findtext("surname") or "").strip()
    return f"{first} {surname}".strip()


def construir_indices(root):
    eventos  = {}
    personas = {}
    familias = {}

    for ev in root.iter("event"):
        eventos[ev.get("handle")] = ev
    for p in root.iter("person"):
        personas[p.get("handle")] = p
    for f in root.iter("family"):
        familias[f.get("handle")] = f

    nombre_persona = {h: normalizar(nombre_completo_persona(p))
                      for h, p in personas.items()}

    anyo_evento = {}
    for h, ev in eventos.items():
        dateval = ev.find("dateval")
        if dateval is not None:
            m = re.match(r"(\d{4})", dateval.get("val", ""))
            anyo_evento[h] = int(m.group(1)) if m else None
        else:
            anyo_evento[h] = None

    return eventos, personas, familias, nombre_persona, anyo_evento


# ── Búsqueda de coincidencias ────────────────────────────────────────────────

def _nombre_primero(nombre_raw: str) -> str:
    """Para matrimonios con 'Y', queda solo la primera persona."""
    return re.split(r"\s+y\s+", normalizar(nombre_raw), flags=re.IGNORECASE)[0].strip()


def _anyo_evento_relevante(pers_handle, tipo_gramps, personas, eventos,
                            familias, anyo_evento):
    """Devuelve el año del evento del tipo buscado para esta persona, o None."""
    if tipo_gramps is None:
        return None
    if tipo_gramps == "Marriage":
        for fam in familias.values():
            father = fam.find("father")
            mother = fam.find("mother")
            miembros = {
                father.get("hlink") if father is not None else None,
                mother.get("hlink") if mother is not None else None,
            }
            if pers_handle in miembros:
                for evref in fam.findall("eventref"):
                    ev = eventos.get(evref.get("hlink"))
                    if ev is not None and ev.findtext("type") == "Marriage":
                        return anyo_evento.get(evref.get("hlink"))
        return None
    persona_el = personas[pers_handle]
    for evref in persona_el.findall("eventref"):
        ev = eventos.get(evref.get("hlink"))
        if ev is not None and ev.findtext("type") == tipo_gramps:
            return anyo_evento.get(evref.get("hlink"))
    return None


def buscar_evento_persona(nombre_raw, anyo, tipo_gramps,
                           personas, eventos, familias,
                           nombre_persona, anyo_evento):
    """
    Búsqueda automática con umbral alto.
    Cuando hay varios candidatos con la misma similitud de nombre,
    desempata por proximidad de año del evento relevante.
    Devuelve (ev_handle, pers_handle, fam_handle) o (None, None, None).
    """
    nombre_primero = _nombre_primero(nombre_raw)

    # Recoger todos los candidatos con similitud >= umbral
    candidatos = []
    for h, nombre_p in nombre_persona.items():
        s = sim_tokens(nombre_primero, nombre_p)
        if s >= UMBRAL_AUTO:
            candidatos.append((s, h))

    if not candidatos:
        return None, None, None

    sim_max = max(s for s, _ in candidatos)
    # Quedarse solo con los que tienen la similitud máxima
    empatados = [h for s, h in candidatos if s == sim_max]

    if len(empatados) == 1 or anyo is None:
        mejor_h = empatados[0]
    else:
        # Desempatar por proximidad de año del evento del tipo buscado
        def distancia_anyo(h):
            ev_anyo = _anyo_evento_relevante(h, tipo_gramps, personas, eventos,
                                             familias, anyo_evento)
            if ev_anyo is None:
                return 9999
            return abs(ev_anyo - anyo)

        mejor_h = min(empatados, key=distancia_anyo)

    return _resolver_evento(mejor_h, anyo, tipo_gramps,
                            personas, eventos, familias, anyo_evento)


def buscar_sugerencias(nombre_raw, anyo, tipo_gramps,
                       personas, eventos, familias,
                       nombre_persona, anyo_evento,
                       n=N_SUGERENCIAS):
    """
    Búsqueda difusa para casos sin coincidencia automática.
    Devuelve lista de hasta n dicts con info de la candidata.
    """
    nombre_primero = _nombre_primero(nombre_raw)

    scores = []
    for h, nombre_p in nombre_persona.items():
        s = sim_combinada(nombre_primero, nombre_p)
        if s >= UMBRAL_SUGERENCIA:
            scores.append((s, h))

    scores.sort(reverse=True)
    resultado = []
    for score, h in scores[:n]:
        p = personas[h]
        nombre_p = nombre_completo_persona(p)
        gramps_id = p.get("id", "")

        # Buscar el evento relevante para esta persona
        ev_h, pers_h, fam_h = _resolver_evento(
            h, anyo, tipo_gramps, personas, eventos, familias, anyo_evento
        )
        if ev_h:
            ev = eventos[ev_h]
            destino = f"{ev.findtext('type')} [{ev.get('id')}]"
        elif fam_h:
            destino = f"familia [{familias[fam_h].get('id')}]"
        else:
            destino = f"persona [{gramps_id}]"

        resultado.append({
            "score":     round(score, 3),
            "id":        gramps_id,
            "nombre":    nombre_p,
            "destino":   destino,
            "ev_h":      ev_h,
            "pers_h":    pers_h,
            "fam_h":     fam_h,
        })

    return resultado


def _resolver_evento(persona_handle, anyo, tipo_gramps,
                     personas, eventos, familias, anyo_evento):
    """Dado un handle de persona, localiza el evento del tipo pedido."""
    if tipo_gramps is None:
        return None, persona_handle, None

    if tipo_gramps == "Marriage":
        for h, fam in familias.items():
            father = fam.find("father")
            mother = fam.find("mother")
            miembros = {
                father.get("hlink") if father is not None else None,
                mother.get("hlink") if mother is not None else None,
            }
            if persona_handle in miembros:
                for evref in fam.findall("eventref"):
                    ev_h = evref.get("hlink")
                    ev   = eventos.get(ev_h)
                    if ev is None:
                        continue
                    if ev.findtext("type") == "Marriage":
                        ev_anyo = anyo_evento.get(ev_h)
                        if anyo is None or ev_anyo is None or abs(ev_anyo - anyo) <= 2:
                            return ev_h, persona_handle, h
        return None, persona_handle, None

    persona_el = personas[persona_handle]
    for evref in persona_el.findall("eventref"):
        ev_h = evref.get("hlink")
        ev   = eventos.get(ev_h)
        if ev is None:
            continue
        if ev.findtext("type") == tipo_gramps:
            ev_anyo = anyo_evento.get(ev_h)
            if anyo is None or ev_anyo is None or abs(ev_anyo - anyo) <= 2:
                return ev_h, persona_handle, None

    return None, persona_handle, None


# ── Generación de handles y timestamps ──────────────────────────────────────

def nuevo_handle():
    return "_" + uuid.uuid4().hex[:29]


def timestamp():
    return str(int(datetime.now().timestamp()))


# ── Inserción en el XML (manipulación de texto, nunca reserializa) ───────────
#
# Estrategia: el XML original se mantiene intacto como string.
# Los nuevos <object> se acumulan y se insertan antes de </objects> (o se crea
# la sección si no existe). Los <objref> se insertan dentro del bloque del
# elemento destino buscado por handle, justo antes de su etiqueta de cierre.

def xml_object_str(handle: str, src: str, mime: str, desc: str) -> str:
    desc_esc = desc.replace("&", "&amp;").replace('"', "&quot;")
    src_esc  = src.replace("&", "&amp;")
    ts = timestamp()
    oid = f"O{handle[1:9]}"
    return (
        f'    <object handle="{handle}" change="{ts}" id="{oid}">\n'
        f'      <file src="{src_esc}" mime="{mime}" checksum="" description="{desc_esc}"/>\n'
        f'    </object>\n'
    )


def xml_objref_str(media_handle: str) -> str:
    return f'      <objref hlink="{media_handle}"/>\n'


class XmlEditor:
    """Edita el XML como texto preservando el original byte a byte salvo las inserciones."""

    def __init__(self, texto: str):
        self.texto = texto
        self._nuevos_objects: list[str] = []
        # handle → lista de objref strings a insertar
        self._nuevos_objrefs: dict[str, list[str]] = {}

    def agregar_media_object(self, ruta_imagen: str, descripcion: str) -> str:
        handle = nuevo_handle()
        src  = str(ruta_imagen).replace("\\", "/")
        mime = "image/jpeg" if ruta_imagen.lower().endswith((".jpg", ".jpeg")) else "image/png"
        self._nuevos_objects.append(xml_object_str(handle, src, mime, descripcion))
        return handle

    def agregar_objref(self, elemento_handle: str, media_handle: str):
        """Registra un objref a insertar en el elemento con ese handle."""
        self._nuevos_objrefs.setdefault(elemento_handle, []).append(
            xml_objref_str(media_handle)
        )

    def _handle_ya_tiene_objref(self, bloque: str, media_handle: str) -> bool:
        return f'hlink="{media_handle}"' in bloque

    def aplicar(self) -> str:
        texto = self.texto

        # 1. Insertar <objref> dentro de cada elemento destino
        for elem_handle, objrefs in self._nuevos_objrefs.items():
            # Localizar el bloque del elemento por su handle
            pat = re.compile(
                rf'(<(?:event|person|family|object)\b[^>]*handle="{re.escape(elem_handle)}"[^>]*>)'
                rf'(.*?)'
                rf'(</(?:event|person|family|object)>)',
                re.DOTALL,
            )
            def insertar_objrefs(m, objrefs=objrefs, elem_handle=elem_handle):
                apertura, cuerpo, cierre = m.group(1), m.group(2), m.group(3)
                nuevos = ""
                for objref in objrefs:
                    media_h = re.search(r'hlink="([^"]+)"', objref).group(1)
                    if f'hlink="{media_h}"' not in cuerpo:
                        nuevos += objref
                return apertura + cuerpo + nuevos + cierre

            texto, n = pat.subn(insertar_objrefs, texto, count=1)
            if n == 0:
                print(f"  AVISO: no se encontró el elemento con handle {elem_handle}")

        # 2. Insertar nuevos <object> antes de </objects>, o crear sección
        if self._nuevos_objects:
            bloque_objects = "".join(self._nuevos_objects)
            if "</objects>" in texto:
                texto = texto.replace("</objects>", bloque_objects + "  </objects>", 1)
            else:
                # Crear sección <objects> antes de </database>
                nueva_seccion = f"  <objects>\n{bloque_objects}  </objects>\n"
                texto = texto.replace("</database>", nueva_seccion + "</database>", 1)

        return texto


# ── Registro de procesados ───────────────────────────────────────────────────

def cargar_registro(directorio: str) -> set:
    ruta = Path(directorio) / REGISTRO_JSON
    if ruta.exists():
        with open(ruta, "r", encoding="utf-8") as f:
            return set(json.load(f).get("procesados", []))
    return set()


def guardar_registro(directorio: str, nuevos: set):
    ruta      = Path(directorio) / REGISTRO_JSON
    existentes = cargar_registro(directorio)
    todos     = sorted(existentes | nuevos)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump({"procesados": todos}, f, ensure_ascii=False, indent=2)
    print(f"Registro actualizado: {ruta}  ({len(todos)} entradas totales)")


# ── CSV de pendientes ────────────────────────────────────────────────────────

CABECERA_CSV = [
    "archivo",
    "tipo_detectado",
    "nombre_detectado",
    "anyo_detectado",
    "sugerencia_1_id",
    "sugerencia_1_nombre",
    "sugerencia_1_destino",
    "sugerencia_1_score",
    "sugerencia_2_id",
    "sugerencia_2_nombre",
    "sugerencia_2_destino",
    "sugerencia_2_score",
    "sugerencia_3_id",
    "sugerencia_3_nombre",
    "sugerencia_3_destino",
    "sugerencia_3_score",
    "gramps_id_confirmado",   # <-- rellenar manualmente: ID de persona (Ixxxx) o dejar vacío
    "notas",
]


def guardar_csv_pendientes(directorio: str, filas: list):
    ruta = Path(directorio) / PENDIENTES_CSV
    with open(ruta, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CABECERA_CSV, delimiter=";",
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(filas)
    print(f"Listado de pendientes: {ruta}  ({len(filas)} entradas)")


def leer_csv_pendientes(directorio: str) -> list:
    ruta = Path(directorio) / PENDIENTES_CSV
    if not ruta.exists():
        return []
    with open(ruta, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


# ── Guardar .gramps ──────────────────────────────────────────────────────────

def guardar_gramps(gramps_path: str, xml_texto: str):
    backup = gramps_path + ".bak"
    shutil.copy2(gramps_path, backup)
    print(f"Backup guardado en {backup}")

    with gzip.open(gramps_path, "wt", encoding="utf-8") as f:
        f.write(xml_texto)
    print(f"Archivo guardado: {gramps_path}")


# ── Modo 1: procesar carpeta ─────────────────────────────────────────────────

def procesar(gramps_path: str, carpeta_imagenes: str, dry_run: bool):
    carpeta = Path(carpeta_imagenes)

    print(f"Cargando {gramps_path}...")
    xml_texto, root = cargar_gramps(gramps_path)
    eventos, personas, familias, nombre_persona, anyo_evento = construir_indices(root)
    print(f"  {len(personas)} personas, {len(eventos)} eventos, {len(familias)} familias")

    ya_procesados = cargar_registro(str(carpeta))
    if ya_procesados:
        print(f"  {len(ya_procesados)} imagenes ya procesadas anteriormente (se omitiran)")

    imagenes = sorted(carpeta.glob("*"))
    imagenes = [p for p in imagenes
                if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}]
    imagenes_nuevas = [p for p in imagenes if p.name not in ya_procesados]
    print(f"  {len(imagenes)} imagenes en carpeta, {len(imagenes_nuevas)} nuevas\n")

    vinculadas        = 0
    nuevos_procesados = set()
    sin_coincidencia  = []
    sin_parse         = []
    editor            = XmlEditor(xml_texto)

    for img in imagenes_nuevas:
        parsed = parsear_nombre(img.stem)
        if parsed is None:
            sin_parse.append(img.name)
            continue

        tipo_gramps, nombre_raw, anyo, parte = parsed

        ev_h, pers_h, fam_h = buscar_evento_persona(
            nombre_raw, anyo, tipo_gramps,
            personas, eventos, familias, nombre_persona, anyo_evento,
        )

        if ev_h is None and pers_h is None and fam_h is None:
            sugerencias = buscar_sugerencias(
                nombre_raw, anyo, tipo_gramps,
                personas, eventos, familias, nombre_persona, anyo_evento,
            )
            fila = {
                "archivo":          img.name,
                "tipo_detectado":   tipo_gramps or "(desconocido)",
                "nombre_detectado": nombre_raw,
                "anyo_detectado":   anyo or "",
                "gramps_id_confirmado": "",
                "notas": "",
            }
            for i, sug in enumerate(sugerencias, 1):
                fila[f"sugerencia_{i}_id"]      = sug["id"]
                fila[f"sugerencia_{i}_nombre"]  = sug["nombre"]
                fila[f"sugerencia_{i}_destino"] = sug["destino"]
                fila[f"sugerencia_{i}_score"]   = sug["score"]
            for i in range(len(sugerencias) + 1, N_SUGERENCIAS + 1):
                fila[f"sugerencia_{i}_id"]      = ""
                fila[f"sugerencia_{i}_nombre"]  = ""
                fila[f"sugerencia_{i}_destino"] = ""
                fila[f"sugerencia_{i}_score"]   = ""
            sin_coincidencia.append(fila)
            continue

        if not dry_run:
            media_handle = editor.agregar_media_object(str(img), img.stem)
            dest_handle  = ev_h or fam_h or pers_h
            editor.agregar_objref(dest_handle, media_handle)
            nuevos_procesados.add(img.name)

        if ev_h:
            ev      = eventos[ev_h]
            destino = f"evento {ev.findtext('type')} [{ev.get('id')}]"
        elif fam_h:
            destino = f"familia [{familias[fam_h].get('id')}]"
        else:
            p       = personas[pers_h]
            destino = f"persona {nombre_completo_persona(p)} [{p.get('id')}]"

        modo = "[DRY-RUN] " if dry_run else ""
        print(f"{modo}OK  {img.name}")
        print(f"      -> {destino}")
        vinculadas += 1

    if not dry_run and vinculadas > 0:
        guardar_gramps(gramps_path, editor.aplicar())
        guardar_registro(str(carpeta), nuevos_procesados)

    if sin_coincidencia:
        guardar_csv_pendientes(str(carpeta), sin_coincidencia)

    print(f"\n{'='*60}")
    print(f"Imagenes vinculadas:        {vinculadas}")
    print(f"Sin coincidencia (con sugerencias difusas): {len(sin_coincidencia)}  -> {PENDIENTES_CSV}")
    print(f"Sin parsear (formato raro): {len(sin_parse)}")
    print(f"Ya procesadas anteriormente:{len(ya_procesados)}")

    if sin_parse:
        print("\n-- No parseados --")
        for n in sin_parse:
            print(f"  {n}")

    if sin_coincidencia and not dry_run:
        print(f"\nRevisa {PENDIENTES_CSV}, rellena 'gramps_id_confirmado' y ejecuta --confirmar.")


# ── Modo 2: aplicar confirmaciones del CSV ───────────────────────────────────

def confirmar(gramps_path: str, carpeta_imagenes: str):
    carpeta = Path(carpeta_imagenes)
    filas   = leer_csv_pendientes(str(carpeta))

    confirmadas = [f for f in filas
                   if f.get("gramps_id_confirmado", "").strip()]
    if not confirmadas:
        print("No hay filas con 'gramps_id_confirmado' relleno en el CSV.")
        return

    print(f"Cargando {gramps_path}...")
    xml_texto, root = cargar_gramps(gramps_path)
    eventos, personas, familias, nombre_persona, anyo_evento = construir_indices(root)

    id_a_handle = {p.get("id"): h for h, p in personas.items()}

    vinculadas        = 0
    nuevos_procesados = set()
    no_encontrados    = []
    editor            = XmlEditor(xml_texto)

    for fila in confirmadas:
        archivo   = fila["archivo"]
        gramps_id = fila["gramps_id_confirmado"].strip()
        img       = carpeta / archivo

        if not img.exists():
            print(f"AVISO: archivo no encontrado en disco: {archivo}")
            no_encontrados.append(archivo)
            continue

        pers_handle = id_a_handle.get(gramps_id)
        if pers_handle is None:
            print(f"AVISO: ID {gramps_id} no existe en GRAMPS  ({archivo})")
            no_encontrados.append(archivo)
            continue

        parsed      = parsear_nombre(img.stem)
        tipo_gramps = parsed[0] if parsed else None
        anyo        = parsed[2] if parsed else None

        ev_h, pers_h, fam_h = _resolver_evento(
            pers_handle, anyo, tipo_gramps,
            personas, eventos, familias, anyo_evento,
        )

        media_handle = editor.agregar_media_object(str(img), img.stem)
        dest_handle  = ev_h or fam_h or pers_handle
        editor.agregar_objref(dest_handle, media_handle)

        if ev_h:
            destino = f"evento {eventos[ev_h].findtext('type')} [{eventos[ev_h].get('id')}]"
        elif fam_h:
            destino = f"familia [{familias[fam_h].get('id')}]"
        else:
            destino = f"persona {nombre_completo_persona(personas[pers_handle])} [{gramps_id}]"

        nuevos_procesados.add(archivo)
        print(f"OK  {archivo}")
        print(f"      -> {destino}  (confirmado manualmente: {gramps_id})")
        vinculadas += 1

    if vinculadas > 0:
        guardar_gramps(gramps_path, editor.aplicar())
        guardar_registro(str(carpeta), nuevos_procesados)

        # Actualizar el CSV: marcar las confirmadas aplicadas y eliminarlas de pendientes
        filas_restantes = [f for f in filas
                           if f["archivo"] not in nuevos_procesados
                           and f["archivo"] not in no_encontrados]
        if filas_restantes:
            guardar_csv_pendientes(str(carpeta), filas_restantes)
            print(f"\nCSV actualizado: quedan {len(filas_restantes)} pendientes.")
        else:
            (carpeta / PENDIENTES_CSV).unlink(missing_ok=True)
            print("\nTodos los pendientes procesados. CSV eliminado.")

    print(f"\n{'='*60}")
    print(f"Confirmaciones aplicadas: {vinculadas}")
    print(f"No encontrados/ID invalido: {len(no_encontrados)}")


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Asociar imagenes a GRAMPS")
    parser.add_argument("--gramps",    required=True, help="Ruta al archivo .gramps")
    parser.add_argument("--imagenes",  required=True, help="Carpeta con las imagenes")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Simular sin modificar el archivo")
    parser.add_argument("--confirmar", action="store_true",
                        help="Aplicar confirmaciones manuales del CSV de pendientes")
    args = parser.parse_args()

    if args.confirmar:
        confirmar(args.gramps, args.imagenes)
    else:
        procesar(args.gramps, args.imagenes, args.dry_run)
