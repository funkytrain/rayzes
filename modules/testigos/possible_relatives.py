# possible_relatives.py
# Motor de detección de posibles familiares: testigos confirmados cuyo apellido
# coincide con el del sujeto del evento, con scoring graduado por sistema de apellidos,
# frecuencia del apellido, presencia en el árbol GRAMPS y parentesco encontrado.

import unicodedata
import re
from dataclasses import dataclass, field
from typing import Optional

try:
    from rapidfuzz import fuzz as _fuzz
    _RAPIDFUZZ_OK = True
except ImportError:
    _RAPIDFUZZ_OK = False

try:
    from modules.testigos.surname_systems import (
        SurnameSystem, ExtractedSurnames, get_system, _normalize
    )
except ImportError:
    from surname_systems import (  # fallback para ejecución directa
        SurnameSystem, ExtractedSurnames, get_system, _normalize
    )

# ─────────────────────────────────────────────────────────────────────────────
# Constantes de confianza
# ─────────────────────────────────────────────────────────────────────────────

CONFIDENCE_NONE      = "none"
CONFIDENCE_VERY_LOW  = "very_low"
CONFIDENCE_LOW       = "low"
CONFIDENCE_MEDIUM    = "medium"
CONFIDENCE_HIGH      = "high"

_CONFIDENCE_RANK = {
    CONFIDENCE_NONE:     0,
    CONFIDENCE_VERY_LOW: 1,
    CONFIDENCE_LOW:      2,
    CONFIDENCE_MEDIUM:   3,
    CONFIDENCE_HIGH:     4,
}

CONFIDENCE_LABELS_ES = {
    CONFIDENCE_NONE:     "Ninguna",
    CONFIDENCE_VERY_LOW: "Muy baja",
    CONFIDENCE_LOW:      "Baja",
    CONFIDENCE_MEDIUM:   "Media",
    CONFIDENCE_HIGH:     "Alta",
}

CONFIDENCE_LABELS_EN = {
    CONFIDENCE_NONE:     "None",
    CONFIDENCE_VERY_LOW: "Very low",
    CONFIDENCE_LOW:      "Low",
    CONFIDENCE_MEDIUM:   "Medium",
    CONFIDENCE_HIGH:     "High",
}

CONFIDENCE_COLORS = {
    CONFIDENCE_NONE:     "#cccccc",
    CONFIDENCE_VERY_LOW: "#f4a261",
    CONFIDENCE_LOW:      "#e9c46a",
    CONFIDENCE_MEDIUM:   "#90be6d",
    CONFIDENCE_HIGH:     "#43aa8b",
}

# ─────────────────────────────────────────────────────────────────────────────
# Resultado de un candidato a familiar
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PossibleRelative:
    event_id: str
    witness_canon: str
    subj_name: str
    subj_id: str                       # GRAMPS person ID del sujeto (puede ser "")

    # Apellidos que dispararon la coincidencia
    witness_surname: str               # Apellido del testigo que coincidió
    subj_surname: str                  # Apellido del sujeto que coincidió
    witness_surname_position: int      # 0 = primario, 1 = secundario, etc.
    subj_surname_position: int

    # Scoring
    surname_similarity: float          # 0–1 similitud fuzzy entre apellidos
    frequency_penalty: float           # 0–1 factor por hiperfrecuencia
    position_weight_witness: float     # Peso del sistema por posición del testigo
    position_weight_subj: float        # Peso del sistema por posición del sujeto
    score: float                       # Score compuesto 0–1

    confidence: str = CONFIDENCE_LOW

    # Evidencia adicional
    gramps_candidate_id: str = ""      # ID GRAMPS del testigo si existe en árbol
    gramps_candidate_name: str = ""
    kinship_found: bool = False
    kinship_label: str = ""

    # Metadatos del evento
    event_date: str = ""
    event_type: str = ""
    event_place: str = ""

    # Control de revisión
    reviewed: bool = False
    review_result: str = ""            # "confirmed_relative" | "discarded" | ""

    def confidence_rank(self) -> int:
        return _CONFIDENCE_RANK.get(self.confidence, 0)


# ─────────────────────────────────────────────────────────────────────────────
# Similitud de apellidos
# ─────────────────────────────────────────────────────────────────────────────

_FUZZY_THRESHOLD = 82   # mínimo para considerar coincidencia (0–100)

def _col_list(df, name: str) -> list:
    if name in df.columns:
        return df[name].fillna("").astype(str).tolist()
    return [""] * len(df)

def _surname_similarity(a: str, b: str) -> float:
    """Retorna similitud 0–1 entre dos apellidos normalizados."""
    a, b = _normalize(a), _normalize(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if _RAPIDFUZZ_OK:
        ratio = _fuzz.token_sort_ratio(a, b)
    else:
        # Fallback: difflib
        import difflib
        ratio = difflib.SequenceMatcher(None, a, b).ratio() * 100
    return ratio / 100.0


def _meets_threshold(similarity: float) -> bool:
    return similarity * 100 >= _FUZZY_THRESHOLD


# ─────────────────────────────────────────────────────────────────────────────
# Scoring compuesto
# ─────────────────────────────────────────────────────────────────────────────

def _compute_score(
    surname_similarity: float,
    frequency_penalty: float,
    position_weight_witness: float,
    position_weight_subj: float,
    in_tree: bool,
    kinship_found: bool,
) -> tuple[float, str]:
    """
    Calcula el score compuesto y la confianza resultante.

    Factores:
    - Similitud de apellido (base)
    - Peso de posición del testigo (cuánto vale ese apellido en su sistema)
    - Peso de posición del sujeto (ídem para el sujeto)
    - Penalización por hiperfrecuencia
    - Bonus por presencia en árbol (+0.15)
    - Bonus por parentesco confirmado en árbol (+0.25)
    """
    base = surname_similarity * position_weight_witness * position_weight_subj
    base *= frequency_penalty

    tree_bonus = 0.0
    if kinship_found:
        tree_bonus = 0.25
    elif in_tree:
        tree_bonus = 0.15

    score = min(1.0, base + tree_bonus)

    # Mapeo score → confianza
    if score < 0.15:
        confidence = CONFIDENCE_NONE
    elif score < 0.30:
        confidence = CONFIDENCE_VERY_LOW
    elif score < 0.50:
        confidence = CONFIDENCE_LOW
    elif score < 0.72:
        confidence = CONFIDENCE_MEDIUM
    else:
        confidence = CONFIDENCE_HIGH

    return round(score, 4), confidence


# ─────────────────────────────────────────────────────────────────────────────
# Función principal de detección
# ─────────────────────────────────────────────────────────────────────────────

def detect_possible_relatives(
    df,                          # DataFrame con columnas estándar del proyecto
    system_code: str = "es",
    gramps_links: dict = None,   # confirmed_links["gramps_links"] si existe
    kinship_map: dict = None,    # {event_id: kinship_label}  ó  {witness_canon: [{subj_name, kinship_label}]}
    min_confidence: str = CONFIDENCE_LOW,
    only_unreviewed: bool = False,
    existing_flags: dict = None, # {event_id: PossibleRelative} ya guardados
) -> dict[str, PossibleRelative]:
    """
    Recorre el DataFrame y genera un dict {event_id: PossibleRelative} para
    todos los eventos donde un testigo confirmado comparte apellido con el sujeto.

    Parámetros:
    - df: DataFrame con columnas event_id, witness_canon, subj_name, subj_id,
          date_iso, type, place_name
    - system_code: código del sistema de apellidos a usar
    - gramps_links: dict con claves "confirmed" y "discarded" del JSON de confirmaciones
    - kinship_map: mapa de parentescos ya calculados (opcional, para boost de confianza)
    - min_confidence: nivel mínimo de confianza para incluir en resultados
    - only_unreviewed: si True, omite los ya revisados
    - existing_flags: banderas ya calculadas (para preservar estado de revisión)
    """
    system: SurnameSystem = get_system(system_code)
    gramps_links = gramps_links or {}
    kinship_map = kinship_map or {}
    existing_flags = existing_flags or {}
    min_rank = _CONFIDENCE_RANK.get(min_confidence, 0)

    # Índice de testigos confirmados en GRAMPS: witness_canon → gramps_id
    confirmed_gramps: dict[str, str] = {}
    confirmed_gramps_names: dict[str, str] = {}
    for wit, gid in gramps_links.get("confirmed", {}).items():
        confirmed_gramps[str(wit)] = str(gid)
    # Nombres GRAMPS: si el caller pasa gramps_id_map podemos resolverlos,
    # si no queda vacío (se rellena en la UI con el id_map disponible)

    results: dict[str, PossibleRelative] = {}

    # Extracción vectorizada de columnas como listas Python.
    # Más rápido que iterrows() (evita la construcción de Series por fila)
    # y más simple que itertuples() (sin offset de índice ni colisión con keywords).
    _event_ids = _col_list(df, "event_id")
    _witness_canons = _col_list(df, "witness_canon")
    _subj_names = _col_list(df, "subj_name")
    _subj_ids = _col_list(df, "subj_id")
    _date_isos = _col_list(df, "date_iso")
    _evt_types = _col_list(df, "type")
    _place_names = _col_list(df, "place_name")

    for (event_id, witness_canon, subj_name, subj_id,
         date_iso, evt_type, place_name) in zip(
            _event_ids, _witness_canons, _subj_names, _subj_ids,
            _date_isos, _evt_types, _place_names):

        if not witness_canon or not subj_name:
            continue

        wit_extracted: ExtractedSurnames = system.extract(witness_canon)
        subj_extracted: ExtractedSurnames = system.extract(subj_name)

        wit_surnames = wit_extracted.all_surnames()
        subj_surnames = subj_extracted.all_surnames()

        if not wit_surnames or not subj_surnames:
            continue

        best: Optional[PossibleRelative] = None

        # Pre-normalizar apellidos del testigo para ws_norm, usado en
        # frequency_penalty() dentro del loop. subj_norms se computa aquí
        # pero _surname_similarity() normaliza internamente, así que no
        # elimina llamadas redundantes; reservado para cuando se refactorice
        # _surname_similarity() para aceptar formas pre-normalizadas.
        wit_norms = [_normalize(ws) for ws in wit_surnames]
        subj_norms = [_normalize(ss) for ss in subj_surnames]

        # Pre-calcular _normalize(subj_name) para el lookup de kinship_map
        # (era recalculado en cada iteración del loop de kin_entries).
        subj_name_norm = _normalize(subj_name)

        in_tree = witness_canon in confirmed_gramps

        # Buscar parentesco en kinship_map una sola vez por fila.
        # Acepta dos formatos:
        #   1. {event_id: kinship_label}  — indexado por evento (preciso)
        #   2. {witness_canon: [{subj_name, kinship_label}]}  — legacy
        kinship_found = False
        kinship_label = ""
        if kinship_map:
            direct = kinship_map.get(event_id)
            if direct and isinstance(direct, str):
                kinship_found = True
                kinship_label = direct
            else:
                for kin_entry in kinship_map.get(witness_canon, []):
                    if not isinstance(kin_entry, dict):
                        continue
                    ksubj = str(kin_entry.get("subj_name", ""))
                    ksubj_norm = _normalize(ksubj)
                    if ksubj_norm == subj_name_norm or (ksubj and ksubj_norm in subj_name_norm):
                        kinship_found = True
                        kinship_label = str(kin_entry.get("kinship_label", ""))
                        break

        # Comparar todos los pares de posiciones (wit_pos, subj_pos)
        for wi, (ws, ws_norm) in enumerate(zip(wit_surnames, wit_norms)):
            for si, (ss, _ss_norm) in enumerate(zip(subj_surnames, subj_norms)):
                sim = _surname_similarity(ws, ss)
                if not _meets_threshold(sim):
                    continue

                freq_pen = system.frequency_penalty(ws_norm)
                pw_wit = system.match_weight(wi)
                pw_subj = system.match_weight(si)

                score, confidence = _compute_score(
                    sim, freq_pen, pw_wit, pw_subj, in_tree, kinship_found
                )

                if _CONFIDENCE_RANK.get(confidence, 0) < min_rank:
                    continue

                candidate = PossibleRelative(
                    event_id=event_id,
                    witness_canon=witness_canon,
                    subj_name=subj_name,
                    subj_id=subj_id,
                    witness_surname=ws,
                    subj_surname=ss,
                    witness_surname_position=wi,
                    subj_surname_position=si,
                    surname_similarity=round(sim, 3),
                    frequency_penalty=round(freq_pen, 3),
                    position_weight_witness=pw_wit,
                    position_weight_subj=pw_subj,
                    score=score,
                    confidence=confidence,
                    gramps_candidate_id=confirmed_gramps.get(witness_canon, ""),
                    gramps_candidate_name=confirmed_gramps_names.get(witness_canon, ""),
                    kinship_found=kinship_found,
                    kinship_label=kinship_label,
                    event_date=date_iso,
                    event_type=evt_type,
                    event_place=place_name,
                )

                # Preservar estado de revisión si ya existía
                if event_id in existing_flags:
                    old = existing_flags[event_id]
                    candidate.reviewed = old.reviewed
                    candidate.review_result = old.review_result

                # Quedarse con el mejor par de posiciones para este evento
                if best is None or score > best.score:
                    best = candidate

        if best is not None:
            results[event_id] = best

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Serialización / deserialización para confirmed_links.json
# ─────────────────────────────────────────────────────────────────────────────

def possible_relatives_to_dict(relatives: dict[str, PossibleRelative]) -> dict:
    out = {}
    for eid, r in relatives.items():
        out[eid] = {
            "witness_canon": r.witness_canon,
            "subj_name": r.subj_name,
            "subj_id": r.subj_id,
            "witness_surname": r.witness_surname,
            "subj_surname": r.subj_surname,
            "witness_surname_position": r.witness_surname_position,
            "subj_surname_position": r.subj_surname_position,
            "surname_similarity": r.surname_similarity,
            "frequency_penalty": r.frequency_penalty,
            "position_weight_witness": r.position_weight_witness,
            "position_weight_subj": r.position_weight_subj,
            "score": r.score,
            "confidence": r.confidence,
            "gramps_candidate_id": r.gramps_candidate_id,
            "gramps_candidate_name": r.gramps_candidate_name,
            "kinship_found": r.kinship_found,
            "kinship_label": r.kinship_label,
            "event_date": r.event_date,
            "event_type": r.event_type,
            "event_place": r.event_place,
            "reviewed": r.reviewed,
            "review_result": r.review_result,
        }
    return out


def possible_relatives_from_dict(data: dict) -> dict[str, PossibleRelative]:
    out = {}
    for eid, d in data.items():
        try:
            out[eid] = PossibleRelative(
                event_id=eid,
                witness_canon=d.get("witness_canon", ""),
                subj_name=d.get("subj_name", ""),
                subj_id=d.get("subj_id", ""),
                witness_surname=d.get("witness_surname", ""),
                subj_surname=d.get("subj_surname", ""),
                witness_surname_position=int(d.get("witness_surname_position", 0)),
                subj_surname_position=int(d.get("subj_surname_position", 0)),
                surname_similarity=float(d.get("surname_similarity", 0)),
                frequency_penalty=float(d.get("frequency_penalty", 1)),
                position_weight_witness=float(d.get("position_weight_witness", 1)),
                position_weight_subj=float(d.get("position_weight_subj", 1)),
                score=float(d.get("score", 0)),
                confidence=d.get("confidence", CONFIDENCE_LOW),
                gramps_candidate_id=d.get("gramps_candidate_id", ""),
                gramps_candidate_name=d.get("gramps_candidate_name", ""),
                kinship_found=bool(d.get("kinship_found", False)),
                kinship_label=d.get("kinship_label", ""),
                event_date=d.get("event_date", ""),
                event_type=d.get("event_type", ""),
                event_place=d.get("event_place", ""),
                reviewed=bool(d.get("reviewed", False)),
                review_result=d.get("review_result", ""),
            )
        except Exception:
            continue
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Estadísticas agregadas para la sección de análisis
# ─────────────────────────────────────────────────────────────────────────────

def aggregate_by_witness(
    relatives: dict[str, PossibleRelative],
) -> list[dict]:
    """
    Agrupa los candidatos por testigo y calcula métricas de interés:
    - N.º de familias distintas con las que comparte apellido
    - N.º de eventos totales
    - Confianza máxima alcanzada
    - Lista de apellidos compartidos
    - Si hay parentesco confirmado en árbol en alguno
    """
    from collections import defaultdict
    by_wit: dict[str, list[PossibleRelative]] = defaultdict(list)
    for r in relatives.values():
        by_wit[r.witness_canon].append(r)

    rows = []
    for wit, entries in by_wit.items():
        families = set(_normalize(e.subj_name.split()[-1]) for e in entries if e.subj_name)
        surnames = set(_normalize(e.witness_surname) for e in entries)
        max_conf = max(entries, key=lambda e: e.confidence_rank())
        has_kinship = any(e.kinship_found for e in entries)
        confirmed_count = sum(1 for e in entries if e.review_result == "confirmed_relative")
        discarded_count = sum(1 for e in entries if e.review_result == "discarded")
        pending_count = sum(1 for e in entries if not e.reviewed)

        rows.append({
            "witness_canon": wit,
            "n_events": len(entries),
            "n_families": len(families),
            "max_confidence": max_conf.confidence,
            "max_score": round(max_conf.score, 3),
            "shared_surnames": ", ".join(sorted(surnames)),
            "kinship_in_tree": has_kinship,
            "confirmed": confirmed_count,
            "discarded": discarded_count,
            "pending": pending_count,
        })

    rows.sort(key=lambda r: (_CONFIDENCE_RANK.get(r["max_confidence"], 0),
                              r["n_events"]), reverse=True)
    return rows


def aggregate_by_surname(
    relatives: dict[str, PossibleRelative],
) -> list[dict]:
    """
    Agrupa por apellido compartido para detectar redes familiares con ese apellido.
    """
    from collections import defaultdict
    by_sn: dict[str, list[PossibleRelative]] = defaultdict(list)
    for r in relatives.values():
        key = _normalize(r.witness_surname)
        by_sn[key].append(r)

    rows = []
    for sn, entries in by_sn.items():
        witnesses = set(e.witness_canon for e in entries)
        subjects = set(e.subj_name for e in entries)
        max_conf = max(entries, key=lambda e: e.confidence_rank())
        years = []
        for e in entries:
            m = re.match(r"^(\d{4})", e.event_date)
            if m:
                years.append(int(m.group(1)))
        year_range = f"{min(years)}–{max(years)}" if years else "—"

        rows.append({
            "surname": sn,
            "n_entries": len(entries),
            "n_witnesses": len(witnesses),
            "n_subjects": len(subjects),
            "max_confidence": max_conf.confidence,
            "year_range": year_range,
        })

    rows.sort(key=lambda r: r["n_entries"], reverse=True)
    return rows
