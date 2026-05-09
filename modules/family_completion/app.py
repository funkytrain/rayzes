# ============================================================
# modules/family_completion/app.py — Family Completion Engine
# Batch Bayesiano: detecta contrayentes sin padres y propone
# candidatos usando la misma lógica de scoring de general/app.py
# ============================================================

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

import pandas as pd
import streamlit as st

from translations import t
from modules.shared.gramps_parser import parse_gramps, GrampsDB
from modules.shared.utils import normalize_name, haversine_km

# Funciones importadas desde general — NO se duplican
from modules.general.app import (
    _score_candidate,
    _apply_prior,
    _fuzzy_overlap,
    _lookup_coords,
    _compute_typical_marriage_age,
    _generate_narrative,
    _parse_cand_extra,
)

DATA_DIR = Path("data")
RESULTS_FILE = DATA_DIR / "family_completion_results.json"
DATA_DIR.mkdir(exist_ok=True)

_PAGE_SIZE = 50


# ============================================================
# Caché de parse
# ============================================================

@st.cache_data(show_spinner=False)
def _cached_parse(content_bytes: bytes) -> GrampsDB:
    return parse_gramps(content_bytes)


@st.cache_data(show_spinner=False)
def _cached_cand_extra(content_bytes: bytes) -> tuple:
    """Parsea testigos de matrimonio y coordenadas (igual que general/app.py).
    Descomprime gzip si es necesario antes de pasar a _parse_cand_extra."""
    import gzip
    data = content_bytes
    if data[:2] == b'\x1f\x8b':
        try:
            data = gzip.decompress(data)
        except Exception:
            pass
    return _parse_cand_extra(data)


# ============================================================
# Funciones puras
# ============================================================

def extract_witnesses_for_person(pid: str, db: GrampsDB, event_types: set[str]) -> list[str]:
    """
    Extrae testigos de eventos del tipo indicado para la persona con handle pid.
    Busca tanto en person.event_handles (eventos propios) como directamente
    en db.events por subject_handle — cubre casos donde el parser no enlazó
    el handle al event_handles por divergencia id/handle en el XML.
    """
    person = db.persons.get(pid)
    if not person:
        return []

    # Recopilar handles relevantes: los ya enlazados + búsqueda directa
    candidate_handles: set[str] = set(person.event_handles)
    for ev_h, ev in db.events.items():
        if ev.subject_handle == pid and ev.type.lower() in event_types:
            candidate_handles.add(ev_h)

    witnesses: list[str] = []
    for ev_h in candidate_handles:
        ev = db.events.get(ev_h)
        if ev is None or ev.type.lower() not in event_types:
            continue
        for w in ev.witnesses:
            if w.name and w.name not in witnesses:
                witnesses.append(w.name)

    return witnesses


_BAPTISM_TYPES = frozenset({'baptism', 'christening', 'christen', 'bautismo', 'bautizo',
                            'birth', 'nacimiento'})


def extract_baptism_witnesses_from_db(pid: str, db: GrampsDB) -> list[str]:
    """
    Busca eventos de tipo Baptism/Birth del pid en db.events.
    Devuelve lista de nombres de testigos.
    Sin bautismo → []. Varios bautismos → unión de testigos de todos.
    """
    return extract_witnesses_for_person(pid, db, _BAPTISM_TYPES)


def extract_children_baptism_witnesses(orphan_pid: str, db: GrampsDB) -> list[str]:
    """
    Extrae los testigos de bautismo de todos los hijos del huérfano.
    Lógica: en registros del s.XVII-XVIII es frecuente que los abuelos
    sean padrinos de sus nietos. Si el padre del huérfano aparece como
    testigo/padrino en el bautismo de algún hijo, podemos detectarlo.
    Devuelve lista deduplicada de nombres de testigos (unión de todos los hijos).
    """
    child_handles: list[str] = []
    for fam in db.families.values():
        if fam.husband_handle == orphan_pid or fam.wife_handle == orphan_pid:
            child_handles.extend(fam.child_handles)

    witnesses: list[str] = []
    for child_h in child_handles:
        for w in extract_witnesses_for_person(child_h, db, _BAPTISM_TYPES):
            if w not in witnesses:
                witnesses.append(w)
    return witnesses


def extract_marriage_witnesses_for_person(pid: str, db: GrampsDB) -> list[str]:
    """
    Extrae testigos del evento de matrimonio donde pid es esposo/esposa.
    Los eventos de matrimonio están en la familia, no en la persona,
    por lo que se itera db.families.
    """
    MARRIAGE_TYPES = {'marriage', 'matrimonio', 'casamiento', 'married'}
    witnesses: list[str] = []
    for fam in db.families.values():
        if fam.husband_handle != pid and fam.wife_handle != pid:
            continue
        # Buscar eventos de matrimonio de esta familia en db.events
        for ev in db.events.values():
            if ev.type.lower() not in MARRIAGE_TYPES:
                continue
            # El sujeto de un evento de matrimonio es la familia (subject_name con "+")
            # o subject_handle puede ser None. Identificamos por subject_name o buscando
            # events cuyo subject_handle apunte a alguno de los cónyuges
            fam_subject = f"{db.persons[fam.husband_handle].name if fam.husband_handle and fam.husband_handle in db.persons else ''} + {db.persons[fam.wife_handle].name if fam.wife_handle and fam.wife_handle in db.persons else ''}".strip(" +")
            if ev.subject_name and (ev.subject_name == fam_subject or
                                     ev.subject_handle in (fam.husband_handle, fam.wife_handle)):
                for w in ev.witnesses:
                    if w.name and w.name not in witnesses:
                        witnesses.append(w.name)
    return witnesses


def _apply_f7(res: dict, orphan_bap_witnesses: list[str], children_bap_witnesses: list[str],
              config: dict) -> dict:
    """
    Calcula F7 y lo integra en el resultado de _score_candidate ya calculado.

    F7 tiene dos sub-señales que se combinan como máximo (no suma):
      - F7a: overlap entre testigos del bautismo del huérfano y los del candidato
             (tíos/abuelos del huérfano podrían coincidir con la red del candidato)
      - F7b: overlap entre testigos de bautismos de los hijos del huérfano y los
             del candidato (abuelos como padrinos de nietos — señal muy fuerte)

    El resultado se integra en prob usando el mismo mecanismo de likelihood ratio
    que _score_candidate, con peso f7 de config["weights"].
    """
    import math as _math
    fuzzy_thr = config.get("fuzzy_thr", 80)
    w7 = config.get("weights", {}).get("f7", 0)
    if w7 == 0:
        res["f7_score"] = None
        res["f7a_matches"] = []
        res["f7b_matches"] = []
        return res

    cand = res.get("_cand", {})
    cand_bap_w = cand.get("bap_witnesses", [])

    f7a, f7a_matches = _fuzzy_overlap(orphan_bap_witnesses, cand_bap_w, fuzzy_thr)
    f7b, f7b_matches = _fuzzy_overlap(children_bap_witnesses, cand_bap_w, fuzzy_thr)

    # Tomar el máximo de las dos sub-señales (no penalizar si solo una está disponible)
    scores = [s for s in (f7a, f7b) if s is not None]
    f7 = max(scores) if scores else None

    res["f7_score"] = f7
    res["f7a_matches"] = f7a_matches
    res["f7b_matches"] = f7b_matches

    if f7 is None:
        return res

    # Reintegrar en prob: mismo LR que _score_candidate pero solo para f7
    # Necesitamos recalcular con el weight relativo correcto.
    # Usamos un LR incremental: multiplicamos los odds actuales por LR_f7.
    all_weights = config.get("weights", {})
    total_w = sum(v for k, v in all_weights.items()
                  if res.get(f"{k}_score") is not None or k == "f7") or 1
    eff_w = w7 / total_w
    sensitivity = 4.0
    lr_f7 = _math.exp(sensitivity * eff_w * (f7 - 0.5))

    current_prob = res["prob"]
    current_odds = max(1e-9, current_prob) / max(1e-9, 1.0 - current_prob)
    new_odds = current_odds * lr_f7
    res["prob"] = min(0.999, max(0.001, new_odds / (1.0 + new_odds)))

    # Actualizar main_factor si f7 es el más influyente
    if abs(lr_f7 - 1.0) > abs(res.get("_best_dev", 0.0)):
        res["main_factor"] = "f7"
        res["_best_dev"] = abs(lr_f7 - 1.0)

    return res


def build_cand_dict_from_person(pid: str, db: GrampsDB) -> dict:
    """
    Construye el dict 'cand' que espera _score_candidate() a partir de
    una persona del árbol (candidato a padre/madre).
    """
    person = db.persons.get(pid)
    if not person:
        return {}

    bap_witnesses = extract_baptism_witnesses_from_db(pid, db)

    # Hermanos: personas en la misma familia padre (childof)
    sibling_pids: list[str] = []
    for fam in db.families.values():
        if pid in fam.child_handles:
            for ch in fam.child_handles:
                if ch != pid and ch not in sibling_pids:
                    sibling_pids.append(ch)
            break

    # Limitar a 10 hermanos más cercanos en edad
    person_year = person.baptism_year or person.birth_year or 0
    sibling_pids_sorted = sorted(
        sibling_pids,
        key=lambda h: abs((db.persons[h].baptism_year or db.persons[h].birth_year or 0) - person_year)
        if h in db.persons else 9999
    )[:10]

    siblings = []
    for sib_pid in sibling_pids_sorted:
        sib = db.persons.get(sib_pid)
        if not sib:
            continue
        sib_bap_w = extract_baptism_witnesses_from_db(sib_pid, db)
        # Testigos de matrimonio del hermano
        sib_mar_w: list[str] = []
        for fam in db.families.values():
            if fam.husband_handle == sib_pid or fam.wife_handle == sib_pid:
                for ev_h in (db.events.get(h) for h in getattr(fam, 'event_handles', [])):
                    if ev_h and ev_h.type.lower() in {'marriage', 'matrimonio'}:
                        sib_mar_w.extend(w.name for w in ev_h.witnesses)
        siblings.append({
            "name":          sib.name,
            "bap_year":      sib.baptism_year or sib.birth_year,
            "bap_place":     sib.baptism_place or sib.birth_place,
            "bap_witnesses": sib_bap_w,
            "mar_witnesses": sib_mar_w,
        })

    return {
        "name":          person.name,
        "sex":           person.sex,
        "bap_year":      person.baptism_year or person.birth_year,
        "bap_place":     person.baptism_place or person.birth_place,
        "bap_witnesses": bap_witnesses,
        "siblings":      siblings,
    }


def find_parent_candidates(
    orphan_pid: str,
    orphan_sex_needed: str,
    db: GrampsDB,
    people_ext: dict,
    families_ext: dict,
    target_year: int | None,
    typical_age: dict,
    max_candidates: int = 20,
) -> list[dict]:
    """
    Candidatos a padre/madre del huérfano.
    orphan_sex_needed: 'M' = buscamos padre, 'F' = buscamos madre.
    """
    orphan = db.persons.get(orphan_pid)
    if not orphan:
        return []

    orphan_name_parts = (orphan.name or "").split()
    orphan_norm_parts = [normalize_name(p) for p in orphan_name_parts]
    # Primer apellido (paterno) → candidato padre; segundo → candidato madre
    if orphan_sex_needed == "M":
        target_surname = orphan_norm_parts[1] if len(orphan_norm_parts) > 1 else None
    else:
        target_surname = orphan_norm_parts[2] if len(orphan_norm_parts) > 2 else (
            orphan_norm_parts[1] if len(orphan_norm_parts) > 1 else None
        )

    # Rango temporal esperado
    sex_age_info = typical_age.get(orphan_sex_needed, (26.0, 6.0, 0, None))
    mean_a, std_a = sex_age_info[0], sex_age_info[1]
    low_confidence = target_surname is None

    if target_year:
        expected_birth = target_year - mean_a
        birth_lo = expected_birth - 2 * std_a
        birth_hi = expected_birth + 2 * std_a
    else:
        birth_lo, birth_hi = None, None

    # Lugar del matrimonio para filtro geográfico
    orphan_place = None
    for fam in db.families.values():
        if fam.husband_handle == orphan_pid or fam.wife_handle == orphan_pid:
            if fam.marriage_place:
                orphan_place = fam.marriage_place
            break
    if not orphan_place:
        orphan_place = orphan.baptism_place or orphan.birth_place or ""

    # Coordenadas del lugar del huérfano
    orphan_lat, orphan_lon = None, None
    for pl in db.places.values():
        if pl.name and normalize_name(pl.name) == normalize_name(orphan_place):
            orphan_lat, orphan_lon = pl.lat, pl.lon
            break

    # Personas que ya tienen hijos del mismo apellido (excluir como candidatos)
    exclude_pids: set[str] = set()
    for fam in db.families.values():
        parent_pid = fam.husband_handle if orphan_sex_needed == "M" else fam.wife_handle
        if not parent_pid:
            continue
        for child_h in fam.child_handles:
            child = db.persons.get(child_h)
            if child and child.id != orphan.id:
                child_parts = normalize_name(child.name or "").split()
                if target_surname and len(child_parts) > 1 and child_parts[1] == target_surname:
                    exclude_pids.add(parent_pid)
                    break

    candidates: list[dict] = []
    for handle, person in db.persons.items():
        if person.handle == orphan_pid:
            continue
        if person.sex != orphan_sex_needed:
            continue
        if handle in exclude_pids:
            continue

        # Filtro temporal
        p_year = person.baptism_year or person.birth_year
        if birth_lo is not None and p_year is not None:
            if not (birth_lo <= p_year <= birth_hi):
                continue

        # Filtro de apellido (primer/segundo apellido)
        if target_surname:
            p_parts = normalize_name(person.name or "").split()
            p_primary = p_parts[1] if len(p_parts) > 1 else (p_parts[0] if p_parts else "")
            if p_primary != target_surname:
                continue

        # Filtro geográfico (≤ 50 km)
        if orphan_lat is not None:
            cand_place = person.baptism_place or person.birth_place or ""
            cand_lat, cand_lon = None, None
            for pl in db.places.values():
                if pl.name and normalize_name(pl.name) == normalize_name(cand_place):
                    cand_lat, cand_lon = pl.lat, pl.lon
                    break
            if cand_lat is not None:
                dist = haversine_km(orphan_lat, orphan_lon, cand_lat, cand_lon)
                if dist > 50:
                    continue

        candidates.append({
            "pid":            handle,
            "low_confidence": low_confidence,
        })

        if len(candidates) >= max_candidates:
            break

    return candidates


def score_all_orphan_marriages(
    db: GrampsDB,
    people_ext: dict,
    families_ext: dict,
    place_coords: dict,
    config: dict,
    witness_per_ev: dict,
    fam_mar_ev: dict,
    progress_callback=None,
) -> list[dict]:
    """
    Batch principal. Para cada matrimonio con al menos un contrayente sin padres.
    witness_per_ev: {event_handle → [nombre_testigo, ...]} — de _parse_cand_extra
    fam_mar_ev:     {family_id → marriage_event_handle}   — de _parse_cand_extra
    """
    results: list[dict] = []
    marriage_families = [
        (fid, fam) for fid, fam in families_ext.items()
        if fam.get("marriage_year") or fam.get("husband") or fam.get("wife")
    ]
    total = len(marriage_families)

    for i, (fid, fam) in enumerate(marriage_families):
        if progress_callback:
            progress_callback(i, total)

        marriage_year = fam.get("marriage_year")
        marriage_place = fam.get("marriage_place", "")

        # Coordenadas del matrimonio
        mar_lat, mar_lon = None, None
        for pl in db.places.values():
            if pl.name and normalize_name(pl.name) == normalize_name(marriage_place or ""):
                mar_lat, mar_lon = pl.lat, pl.lon
                break

        typical_age = _compute_typical_marriage_age(
            people_ext, families_ext, marriage_year, mar_lat, mar_lon
        )

        # Testigos del acta de matrimonio (igual que en general/page_identificacion_candidatos)
        mar_ev_h = fam_mar_ev.get(fid)
        marriage_witnesses = witness_per_ev.get(mar_ev_h, []) if mar_ev_h else []

        for role, pid_key, sex_needed in [("father", "husband", "M"), ("mother", "wife", "F")]:
            pid = fam.get(pid_key)
            if not pid:
                continue
            person_ext = people_ext.get(pid, {})
            if person_ext.get("has_parents", True):
                continue  # ya tiene padres conocidos

            person_db = db.persons.get(pid)
            # Buscar por gramps id si no hay handle directo
            if not person_db:
                person_db = next(
                    (p for p in db.persons.values() if p.id == pid), None
                )
            person_handle = person_db.handle if person_db else pid

            # target_witnesses = testigos del acta de matrimonio del huérfano
            target_witnesses = marriage_witnesses
            has_witnesses = bool(target_witnesses)

            # Fuentes adicionales para F7
            orphan_bap_witnesses = extract_baptism_witnesses_from_db(person_handle, db)
            children_bap_witnesses = extract_children_baptism_witnesses(person_handle, db)

            candidates_raw = find_parent_candidates(
                person_handle, sex_needed, db, people_ext, families_ext,
                marriage_year, typical_age,
                max_candidates=config.get("max_candidates", 20),
            )

            scored: list[dict] = []
            for cand_info in candidates_raw:
                cand_handle = cand_info["pid"]
                cand_dict = build_cand_dict_from_person(cand_handle, db)
                if not cand_dict:
                    continue
                res = _score_candidate(
                    target_witnesses, cand_dict, typical_age,
                    marriage_year, mar_lat, mar_lon, place_coords, config
                )
                res = _apply_f7(res, orphan_bap_witnesses, children_bap_witnesses, config)
                res["pid"] = cand_handle
                res["low_confidence"] = cand_info.get("low_confidence", False)
                scored.append(res)

            if scored:
                scored = _apply_prior(scored)
                scored.sort(key=lambda r: r.get("prob", 0), reverse=True)

            top = scored[0] if scored else None
            low_evidence = not has_witnesses

            orphan_gramps_id = db.persons[person_handle].id if person_handle in db.persons else pid

            results.append({
                "orphan_pid":         person_handle,
                "orphan_gramps_id":   orphan_gramps_id,
                "orphan_name":        person_ext.get("name", pid),
                "orphan_sex":      sex_needed,
                "marriage_fid":    fid,
                "marriage_year":   marriage_year,
                "marriage_place":  marriage_place,
                "role_needed":          role,
                "target_witnesses":     target_witnesses,
                "orphan_bap_witnesses": orphan_bap_witnesses,
                "children_bap_witnesses": children_bap_witnesses,
                "has_witnesses":        has_witnesses,
                "typical_age":          {k: list(v) for k, v in typical_age.items()},
                "results":         scored,
                "top_candidate":   top.get("name") if top else None,
                "top_prob":        round(top.get("prob", 0), 3) if top else None,
                "low_evidence":    low_evidence,
                "year_unknown":    marriage_year is None,
            })

    if progress_callback:
        progress_callback(total, total)

    return results


def filter_results(
    batch: list[dict],
    min_prob: float = 0.35,
    only_with_witnesses: bool = False,
    role_filter: str | None = None,
) -> list[dict]:
    out = []
    for r in batch:
        if r.get("top_prob") is None or r["top_prob"] < min_prob:
            continue
        if only_with_witnesses and not r.get("has_witnesses"):
            continue
        if role_filter and r.get("role_needed") != role_filter:
            continue
        out.append(r)
    return out


def save_confirmed_results(confirmed: list[dict]) -> None:
    """Persiste únicamente los casos confirmados manualmente por el usuario."""
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(confirmed, f, ensure_ascii=False, indent=2)


def load_confirmed_results() -> list[dict]:
    """Carga los casos confirmados manualmente. Independiente del batch de análisis."""
    if not RESULTS_FILE.exists():
        return []
    try:
        with open(RESULTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


# ============================================================
# Utilidades de UI
# ============================================================

def _build_place_coords(db: GrampsDB) -> dict:
    """Dict {nombre_lugar → (lat, lon)} para _score_candidate/_lookup_coords."""
    coords = {}
    for pl in db.places.values():
        if pl.name and pl.lat is not None and pl.lon is not None:
            coords[pl.name] = (pl.lat, pl.lon)
    return coords


def _render_result_detail(case: dict, db: GrampsDB, people_ext: dict,
                           families_ext: dict, place_coords: dict, config: dict):
    """Vista de detalle para un caso seleccionado (Tab 3)."""
    st.markdown(f"**{t('fce_orphan')}:** {case.get('orphan_name')} — "
                f"{t('fce_role_needed')}: {case.get('role_needed')} — "
                f"{t('fce_marriage_year')}: {case.get('marriage_year') or '?'} — "
                f"{t('fce_marriage_place')}: {case.get('marriage_place') or '?'}")

    target_witnesses = case.get("target_witnesses", [])
    orphan_bap_w = case.get("orphan_bap_witnesses", [])
    children_bap_w = case.get("children_bap_witnesses", [])

    if target_witnesses:
        st.markdown(f"**{t('fce_witnesses_found')}:** {', '.join(target_witnesses)}")
    else:
        st.warning(t("fce_low_evidence_warning"))
    if orphan_bap_w:
        st.caption(f"**{t('fce_orphan_bap_witnesses')}:** {', '.join(orphan_bap_w)}")
    if children_bap_w:
        st.caption(f"**{t('fce_children_bap_witnesses')}:** {', '.join(children_bap_w)}")

    results = case.get("results", [])
    if not results:
        st.info(t("fce_no_candidates"))
        return

    typical_age = {k: tuple(v) for k, v in case.get("typical_age", {}).items()}
    target_info = {
        "witnesses": target_witnesses,
        "place":     case.get("marriage_place"),
        "year":      case.get("marriage_year"),
    }

    narrative = _generate_narrative(results, target_info, typical_age, len(results))
    with st.expander(t("fce_narrative_label"), expanded=True):
        st.markdown(narrative)

    # Tabla de candidatos
    rows = []
    for r in results:
        f7a_hits = len(r.get("f7a_matches", []))
        f7b_hits = len(r.get("f7b_matches", []))
        f7_label = (f"{r['f7_score']:.2f} (bap:{f7a_hits} hijos:{f7b_hits})"
                    if r.get("f7_score") is not None else "—")
        rows.append({
            t("fce_col_candidate"): r.get("name", "?"),
            t("fce_col_prob"):      f"{r.get('prob', 0):.1%}",
            "F1 (mat.)":            f"{r.get('f1_score', 0) or 0:.2f}",
            "F4 (temp.)":           f"{r.get('f4_score', 0) or 0:.2f}",
            "F5 (geo.)":            f"{r.get('f5_score', 0) or 0:.2f}",
            "F7 (gen.)":            f7_label,
            t("fce_col_low_ev"):    "⚠️" if r.get("low_confidence") else "✓",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True)

    # Botones: Guardar / Borrar guardado
    case_key = (case.get("orphan_pid"), case.get("marriage_fid"), case.get("role_needed"))
    btn_save, btn_del = st.columns([2, 2])

    with btn_save:
        if st.button(t("fce_send_to_export"),
                     key=f"send_{case_key[0]}_{case_key[1]}_{case_key[2]}"):
            confirmed = load_confirmed_results()
            confirmed = [r for r in confirmed
                         if not (r.get("orphan_pid") == case_key[0]
                                 and r.get("marriage_fid") == case_key[1]
                                 and r.get("role_needed") == case_key[2])]
            confirmed.append(case)
            save_confirmed_results(confirmed)
            st.success(t("fce_saved_ok"))

    with btn_del:
        confirmed_now = load_confirmed_results()
        is_saved = any(r.get("orphan_pid") == case_key[0]
                       and r.get("marriage_fid") == case_key[1]
                       and r.get("role_needed") == case_key[2]
                       for r in confirmed_now)
        if is_saved:
            if st.button(t("fce_delete_saved"),
                         key=f"del_{case_key[0]}_{case_key[1]}_{case_key[2]}"):
                confirmed_now = [r for r in confirmed_now
                                 if not (r.get("orphan_pid") == case_key[0]
                                         and r.get("marriage_fid") == case_key[1]
                                         and r.get("role_needed") == case_key[2])]
                save_confirmed_results(confirmed_now)
                st.success(t("fce_deleted_ok"))


# ============================================================
# Sidebar
# ============================================================

def render_sidebar():
    st.sidebar.markdown(f"### {t('section_family_completion')}")

    shared_bytes = st.session_state.get("shared_gramps_bytes")
    shared_name = st.session_state.get("shared_gramps_name", "")

    if shared_bytes:
        st.sidebar.success(f"📂 {shared_name}")
    else:
        uploaded = st.sidebar.file_uploader(
            t("sidebar_gramps_uploader"),
            type=["gramps"],
            key="fce_uploader",
        )
        if uploaded:
            content = uploaded.read()
            st.session_state["shared_gramps_bytes"] = content
            st.session_state["shared_gramps_name"] = uploaded.name

    st.sidebar.markdown("---")
    st.sidebar.slider(
        t("fce_min_prob_label"),
        min_value=0.10, max_value=0.95, value=0.35, step=0.05,
        key="fce_min_prob",
    )
    st.sidebar.checkbox(t("fce_only_witnesses"), value=False, key="fce_only_witnesses")
    st.sidebar.selectbox(
        t("fce_role_filter_label"),
        options=["", "father", "mother"],
        format_func=lambda x: {"": t("fce_role_all"), "father": t("fce_role_father"), "mother": t("fce_role_mother")}.get(x, x),
        key="fce_role_filter",
    )


# ============================================================
# Página principal
# ============================================================

def render_page():
    content_bytes = st.session_state.get("shared_gramps_bytes")
    if not content_bytes:
        st.info(t("sidebar_gramps_uploader"))
        return

    st.title(t("section_family_completion"))
    st.caption(t("fce_page_caption"))

    db = _cached_parse(content_bytes)
    people_ext = db.to_persons_ext()
    families_ext = db.to_families_ext()
    place_coords = _build_place_coords(db)

    # Parseo de testigos de matrimonio (misma lógica que general/page_identificacion_candidatos)
    _, _, witness_per_ev, fam_mar_ev = _cached_cand_extra(content_bytes)

    config = {
        "fuzzy_thr":     80,
        "geo_scale":     30.0,
        "use_sib_mar":   True,
        "use_surnames":  False,
        "max_candidates": 20,
        # f7: testigos de bautismo del huérfano + testigos de bautismos de sus hijos
        "weights": {"f1": 35, "f2": 20, "f3": 15, "f4": 15, "f5": 10, "f6": 5, "f7": 15},
    }

    # ── Botón de análisis ────────────────────────────────────────────────────
    run_col, _ = st.columns([2, 3])
    with run_col:
        run_clicked = st.button(t("fce_run_analysis"), type="primary")

    if run_clicked:
        progress_bar = st.progress(0, text=t("fce_progress_text"))

        def _progress(current, total):
            if total > 0:
                pct = min(current / total, 1.0)
                progress_bar.progress(pct, text=f"{t('fce_progress_text')} ({current}/{total})")

        with st.spinner(t("fce_computing")):
            batch = score_all_orphan_marriages(
                db, people_ext, families_ext, place_coords, config,
                witness_per_ev, fam_mar_ev, _progress
            )

        # El batch completo solo vive en sesión; el JSON guarda solo confirmaciones manuales
        st.session_state["fce_batch"] = batch
        progress_bar.progress(1.0, text=t("fce_done"))
        st.success(f"{t('fce_done')} — {len(batch)} {t('fce_cases_found')}")
        st.rerun()

    # ── El batch vive en sesión; no se carga del JSON (eso son confirmaciones) ─
    batch = st.session_state.get("fce_batch")

    if not batch:
        st.info(t("fce_no_results_yet"))
        return

    # ── Filtros activos ──────────────────────────────────────────────────────
    min_prob = st.session_state.get("fce_min_prob", 0.35)
    only_witnesses = st.session_state.get("fce_only_witnesses", False)
    role_filter = st.session_state.get("fce_role_filter", "") or None

    filtered = filter_results(batch, min_prob=min_prob,
                              only_with_witnesses=only_witnesses,
                              role_filter=role_filter)

    # ── Tabs ─────────────────────────────────────────────────────────────────
    tab_summary, tab_results, tab_detail = st.tabs([
        t("fce_tab_summary"), t("fce_tab_results"), t("fce_tab_detail")
    ])

    # ── Tab 1: Resumen ───────────────────────────────────────────────────────
    with tab_summary:
        n_orphans = len(batch)
        n_with_candidates = sum(1 for r in batch if r.get("results"))
        n_high_conf = sum(1 for r in batch if (r.get("top_prob") or 0) >= 0.65)
        n_no_witnesses = sum(1 for r in batch if not r.get("has_witnesses"))

        c1, c2, c3, c4 = st.columns(4)
        c1.metric(t("fce_metric_orphans"), n_orphans)
        c2.metric(t("fce_metric_with_candidates"), n_with_candidates)
        c3.metric(t("fce_metric_high_conf"), n_high_conf)
        c4.metric(t("fce_metric_no_witnesses"), n_no_witnesses)

        if n_no_witnesses > 0:
            st.warning(f"⚠️ {n_no_witnesses} {t('fce_low_evidence_warning')}")

    # ── Tab 2: Resultados ────────────────────────────────────────────────────
    with tab_results:
        if not filtered:
            st.info(t("fce_no_results_filter"))
        else:
            # Selectbox con búsqueda fuzzy nativa de Streamlit
            def _option_label(r):
                iid = r.get("orphan_gramps_id", "")
                fid = r.get("marriage_fid", "")
                yr  = r.get("marriage_year") or "?"
                return f"{r.get('orphan_name', '')}  ·  {iid}  ·  {fid}  ·  {yr}"

            option_labels = [""] + [_option_label(r) for r in filtered]
            selected_label = st.selectbox(
                t("fce_search_label"),
                options=option_labels,
                key="fce_search_select",
            )
            if selected_label:
                idx = option_labels.index(selected_label) - 1
                st.session_state["fce_selected_case"] = filtered[idx]

            # Tabla completa con scroll — sin paginación, ordenable globalmente
            rows = []
            for r in filtered:
                rows.append({
                    t("fce_col_name"):      r.get("orphan_name", ""),
                    t("fce_col_marriage"):  r.get("marriage_fid", ""),
                    t("fce_col_year"):      r.get("marriage_year") or "?",
                    t("fce_col_role"):      r.get("role_needed", ""),
                    t("fce_col_candidate"): r.get("top_candidate") or "—",
                    t("fce_col_prob"):      f"{r.get('top_prob', 0):.1%}" if r.get("top_prob") else "—",
                    t("fce_col_low_ev"):    "⚠️" if r.get("low_evidence") else "✓",
                })

            df = pd.DataFrame(rows)
            event = st.dataframe(
                df,
                use_container_width=True,
                height=600,
                selection_mode="single-row",
                on_select="rerun",
                key="fce_results_table",
            )
            selected_rows = event.selection.get("rows", []) if hasattr(event, "selection") else []
            if selected_rows and selected_rows[0] < len(filtered):
                st.session_state["fce_selected_case"] = filtered[selected_rows[0]]

            # Exportar CSV
            csv_bytes = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                t("fce_export_csv"),
                data=csv_bytes,
                file_name="family_completion_results.csv",
                mime="text/csv",
            )

    # ── Tab 3: Detalle ───────────────────────────────────────────────────────
    with tab_detail:
        case = st.session_state.get("fce_selected_case")
        if case is None:
            st.info(t("fce_select_case_hint"))
        else:
            _render_result_detail(case, db, people_ext, families_ext, place_coords, config)
