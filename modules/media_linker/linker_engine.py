"""
Motor de matching entre nombres de archivo de imagen y entidades en GrampsDB.
Importa funciones de parseo/similitud de gramps_link_images.py (raíz) y
reimplementa la búsqueda sobre GrampsDB directamente (sin ElementTree).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

# gramps_link_images.py está en la raíz del proyecto
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from gramps_link_images import (  # noqa: E402
    parsear_nombre,
    normalizar,
    sim_tokens,
    sim_combinada,
    UMBRAL_AUTO,
    UMBRAL_SUGERENCIA,
    N_SUGERENCIAS,
)

if TYPE_CHECKING:
    from modules.shared.gramps_parser import GrampsDB

_IMG_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

# Mapeo tipo_gramps → campo de año en GrampsPerson (para desempate rápido)
_TIPO_ANYO_CAMPO = {
    "Baptism":      "baptism_year",
    "Birth":        "birth_year",
    "Death":        "death_year",
    "Burial":       "death_year",
    "Confirmation": None,
    "Marriage":     None,  # se usa marriage_year de la familia
}


# ── Índices sobre GrampsDB ───────────────────────────────────────────────────

def _build_indices(db: "GrampsDB"):
    """Construye los índices necesarios para la búsqueda."""
    nombre_persona: dict[str, str] = {
        h: normalizar(p.name) for h, p in db.persons.items()
    }
    anyo_evento: dict[str, int | None] = {}
    for ev_h, ev in db.events.items():
        if ev.date_iso:
            m = re.match(r"(\d{4})", ev.date_iso)
            anyo_evento[ev_h] = int(m.group(1)) if m else None
        else:
            anyo_evento[ev_h] = None

    return nombre_persona, anyo_evento


# ── Resolución de evento para una persona conocida ───────────────────────────

def _resolver_evento(pers_handle: str, anyo: int | None, tipo_gramps: str | None,
                     db: "GrampsDB", anyo_evento: dict) -> tuple:
    """
    Dado el handle de la persona, localiza el evento del tipo pedido.
    Devuelve (ev_handle, pers_handle, fam_handle).
    """
    if tipo_gramps is None:
        return None, pers_handle, None

    if tipo_gramps == "Marriage":
        for fam_h, fam in db.families.items():
            miembros = {fam.husband_handle, fam.wife_handle}
            if pers_handle in miembros:
                for ev_h in (db.persons[pers_handle].event_handles
                             if pers_handle in db.persons else []):
                    ev = db.events.get(ev_h)
                    if ev and ev.type == "Marriage":
                        ev_anyo = anyo_evento.get(ev_h)
                        if anyo is None or ev_anyo is None or abs(ev_anyo - anyo) <= 2:
                            return ev_h, pers_handle, fam_h
                # Si no encontramos el evento via persona, buscar en eventref de familia
                # GrampsFamily no almacena event_handles propios; los events de matrimonio
                # están en event_handles de los cónyuges con type=="Marriage"
                return None, pers_handle, fam_h
        return None, pers_handle, None

    persona = db.persons.get(pers_handle)
    if persona is None:
        return None, pers_handle, None

    for ev_h in persona.event_handles:
        ev = db.events.get(ev_h)
        if ev and ev.type == tipo_gramps:
            ev_anyo = anyo_evento.get(ev_h)
            if anyo is None or ev_anyo is None or abs(ev_anyo - anyo) <= 2:
                return ev_h, pers_handle, None

    return None, pers_handle, None


def _anyo_evento_relevante(pers_handle: str, tipo_gramps: str | None,
                            db: "GrampsDB", anyo_evento: dict) -> int | None:
    """Año del evento relevante para desempate entre homónimos."""
    if tipo_gramps is None:
        return None
    if tipo_gramps == "Marriage":
        for fam in db.families.values():
            if pers_handle in {fam.husband_handle, fam.wife_handle}:
                return fam.marriage_year
        return None
    persona = db.persons.get(pers_handle)
    if persona is None:
        return None
    for ev_h in persona.event_handles:
        ev = db.events.get(ev_h)
        if ev and ev.type == tipo_gramps:
            return anyo_evento.get(ev_h)
    return None


# ── Búsqueda automática ───────────────────────────────────────────────────────

def _nombre_primero(nombre_raw: str) -> str:
    return re.split(r"\s+y\s+", normalizar(nombre_raw), flags=re.IGNORECASE)[0].strip()


def _buscar_evento_persona(nombre_raw: str, anyo: int | None, tipo_gramps: str | None,
                            db: "GrampsDB", nombre_persona: dict, anyo_evento: dict):
    nombre_p = _nombre_primero(nombre_raw)

    candidatos = [(sim_tokens(nombre_p, np), h)
                  for h, np in nombre_persona.items()
                  if sim_tokens(nombre_p, np) >= UMBRAL_AUTO]

    if not candidatos:
        return None, None, None

    sim_max = max(s for s, _ in candidatos)
    empatados = [h for s, h in candidatos if s == sim_max]

    if len(empatados) == 1 or anyo is None:
        mejor_h = empatados[0]
    else:
        def dist(h):
            ev_anyo = _anyo_evento_relevante(h, tipo_gramps, db, anyo_evento)
            return abs(ev_anyo - anyo) if ev_anyo is not None else 9999

        mejor_h = min(empatados, key=dist)

    return _resolver_evento(mejor_h, anyo, tipo_gramps, db, anyo_evento)


# ── Búsqueda difusa para sugerencias ─────────────────────────────────────────

def _buscar_sugerencias(nombre_raw: str, anyo: int | None, tipo_gramps: str | None,
                         db: "GrampsDB", nombre_persona: dict, anyo_evento: dict,
                         n: int = N_SUGERENCIAS) -> list[dict]:
    nombre_p = _nombre_primero(nombre_raw)

    scores = [(sim_combinada(nombre_p, np), h)
              for h, np in nombre_persona.items()
              if sim_combinada(nombre_p, np) >= UMBRAL_SUGERENCIA]
    scores.sort(reverse=True)

    resultado = []
    for score, h in scores[:n]:
        p = db.persons[h]
        ev_h, pers_h, fam_h = _resolver_evento(h, anyo, tipo_gramps, db, anyo_evento)
        if ev_h:
            ev = db.events[ev_h]
            destino = f"{ev.type} [{ev.id}]"
        elif fam_h:
            destino = f"familia [{db.families[fam_h].id}]"
        else:
            destino = f"persona [{p.id}]"
        resultado.append({
            "score":   round(score, 3),
            "id":      p.id,
            "nombre":  p.name,
            "destino": destino,
            "ev_h":    ev_h,
            "pers_h":  pers_h,
            "fam_h":   fam_h,
        })
    return resultado


# ── Etiqueta de destino legible ───────────────────────────────────────────────

def _destino_label(ev_h, pers_h, fam_h, db: "GrampsDB") -> str:
    if ev_h:
        ev = db.events.get(ev_h)
        return f"{ev.type} [{ev.id}]" if ev else ev_h
    if fam_h:
        fam = db.families.get(fam_h)
        return f"familia [{fam.id}]" if fam else fam_h
    if pers_h:
        p = db.persons.get(pers_h)
        return f"persona {p.name} [{p.id}]" if p else pers_h
    return ""


# ── API pública ───────────────────────────────────────────────────────────────

def scan_and_match(carpeta: str, db: "GrampsDB", ya_procesados: set) -> dict:
    """
    Escanea la carpeta y clasifica las imágenes en tres grupos:
      - auto:       coincidencia automática encontrada
      - pendientes: sin coincidencia, con sugerencias difusas
      - sin_parsear: nombre de archivo no reconocible
    """
    nombre_persona, anyo_evento = _build_indices(db)

    imagenes = sorted(Path(carpeta).glob("*"))
    imagenes = [p for p in imagenes if p.suffix.lower() in _IMG_EXTS]
    imagenes_nuevas = [p for p in imagenes if p.name not in ya_procesados]

    auto: list[dict] = []
    pendientes: list[dict] = []
    sin_parsear: list[str] = []

    for img in imagenes_nuevas:
        parsed = parsear_nombre(img.stem)
        if parsed is None:
            sin_parsear.append(img.name)
            continue

        tipo_gramps, nombre_raw, anyo, _parte = parsed
        ev_h, pers_h, fam_h = _buscar_evento_persona(
            nombre_raw, anyo, tipo_gramps, db, nombre_persona, anyo_evento
        )

        if ev_h is not None or pers_h is not None or fam_h is not None:
            elem_handle = ev_h or fam_h or pers_h
            gramps_id = ""
            if pers_h:
                gramps_id = db.persons[pers_h].id
            auto.append({
                "img_path":    str(img),
                "img_name":    img.name,
                "tipo":        tipo_gramps or "",
                "nombre":      nombre_raw,
                "anyo":        anyo or "",
                "destino":     _destino_label(ev_h, pers_h, fam_h, db),
                "elem_handle": elem_handle,
                "gramps_id":   gramps_id,
                "descripcion": img.stem,
            })
        else:
            sugs = _buscar_sugerencias(
                nombre_raw, anyo, tipo_gramps, db, nombre_persona, anyo_evento
            )
            fila: dict = {
                "img_path":              str(img),
                "img_name":              img.name,
                "tipo":                  tipo_gramps or "(desconocido)",
                "nombre":                nombre_raw,
                "anyo":                  anyo or "",
                "gramps_id_confirmado":  "",
            }
            for i, sug in enumerate(sugs, 1):
                fila[f"sug_{i}_id"]      = sug["id"]
                fila[f"sug_{i}_nombre"]  = sug["nombre"]
                fila[f"sug_{i}_destino"] = sug["destino"]
                fila[f"sug_{i}_score"]   = sug["score"]
            for i in range(len(sugs) + 1, N_SUGERENCIAS + 1):
                fila[f"sug_{i}_id"]      = ""
                fila[f"sug_{i}_nombre"]  = ""
                fila[f"sug_{i}_destino"] = ""
                fila[f"sug_{i}_score"]   = ""
            pendientes.append(fila)

    return {
        "total":         len(imagenes),
        "nuevas":        len(imagenes_nuevas),
        "ya_procesadas": len(imagenes) - len(imagenes_nuevas),
        "auto":          auto,
        "pendientes":    pendientes,
        "sin_parsear":   sin_parsear,
    }


def resolver_confirmados(pendientes: list[dict], gramps_id_map: dict,
                          db: "GrampsDB") -> tuple[list[dict], list[str]]:
    """
    Procesa las filas de pendientes donde el usuario ha rellenado gramps_id_confirmado.
    gramps_id_map: db.persons_by_gramps_id  (gramps_id → handle)
    Devuelve (links_válidos, errores).
    """
    _, anyo_evento = _build_indices(db)
    links: list[dict] = []
    errores: list[str] = []

    for fila in pendientes:
        gid = str(fila.get("gramps_id_confirmado", "")).strip()
        if not gid:
            continue
        pers_handle = gramps_id_map.get(gid)
        if pers_handle is None:
            errores.append(gid)
            continue

        img_path = fila["img_path"]
        parsed = parsear_nombre(Path(img_path).stem)
        tipo_gramps = parsed[0] if parsed else None
        anyo        = parsed[2] if parsed else None

        ev_h, pers_h, fam_h = _resolver_evento(
            pers_handle, anyo, tipo_gramps, db, anyo_evento
        )
        elem_handle = ev_h or fam_h or pers_handle
        links.append({
            "img_path":    img_path,
            "img_name":    fila["img_name"],
            "descripcion": Path(img_path).stem,
            "elem_handle": elem_handle,
            "gramps_id":   gid,
            "destino":     _destino_label(ev_h, pers_h, fam_h, db),
        })
    return links, errores
