"""
Motor de generación de tareas de investigación genealógica.

Agrega hallazgos accionables de todos los módulos en una lista unificada
de ResearchTask, con persistencia de estado entre sesiones.

Funciones puras — sin imports de Streamlit.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Modelo de datos
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ResearchTask:
    task_id: str
    task_type: str        # find_parents | confirm_parent_candidate |
                          # resolve_inconsistency | confirm_witness_identity | resolve_tree_link
    person_id: str        # GRAMPS id (e.g. "I0042"); "" si no hay vínculo directo
    person_name: str
    title: str
    detail: str
    priority: int         # 1=crítica, 2=alta, 3=media
    source_module: str    # general | family_completion | testigos | identity_resolution
    source_ref: str       # clave estable para merge entre sesiones
    status: str           # pending | in_progress | done | discarded
    notes: str            = ""
    found_source: str     = ""
    created_at: str       = field(default_factory=_now_iso)
    updated_at: str       = field(default_factory=_now_iso)


def _make_task(
    task_type: str,
    person_id: str,
    person_name: str,
    title: str,
    detail: str,
    priority: int,
    source_module: str,
    source_ref: str,
) -> ResearchTask:
    return ResearchTask(
        task_id      = str(uuid.uuid4()),
        task_type    = task_type,
        person_id    = person_id,
        person_name  = person_name,
        title        = title,
        detail       = detail,
        priority     = priority,
        source_module= source_module,
        source_ref   = source_ref,
        status       = 'pending',
    )


# ─────────────────────────────────────────────────────────────────────────────
# Generadores por módulo
# ─────────────────────────────────────────────────────────────────────────────

def generate_tasks_from_general(
    people_ext: dict,
    families_ext: dict,
    stats: dict,
    graph: Any,
    dismissed_keys: set,
    windowed_stats: dict,
    record_dates: dict,
) -> list[ResearchTask]:
    """
    Genera tareas de:
    1. Inconsistencias activas (no descartadas): resolve_inconsistency
    2. Personas hoja con factibilidad 'possible': find_parents
    """
    tasks: list[ResearchTask] = []

    # ── 1. Inconsistencias ────────────────────────────────────────────────────
    try:
        from modules.general.app import detect_inconsistencies, _dismissed_key
        issues = detect_inconsistencies(people_ext, families_ext, stats, graph)
        for issue in issues:
            key = _dismissed_key(issue)
            if key in dismissed_keys:
                continue
            severity = issue.get('severity', 'warning')
            priority = 1 if severity == 'error' else 2
            source_ref = f"inc|{issue.get('pid','')}|{issue.get('category','')}|{issue.get('detail','')[:40]}"
            tasks.append(_make_task(
                task_type    = 'resolve_inconsistency',
                person_id    = issue.get('gramps_id', ''),
                person_name  = issue.get('name', ''),
                title        = f"Inconsistencia [{issue.get('category','')}]: {issue.get('name','')}",
                detail       = issue.get('detail', ''),
                priority     = priority,
                source_module= 'general',
                source_ref   = source_ref,
            ))
    except Exception:
        pass

    # ── 2. Personas sin padres (extremos del árbol) ───────────────────────────
    try:
        from modules.general.app import find_leaf_individuals, compute_feasibility
        leaf_ids = find_leaf_individuals(people_ext, families_ext)
        for pid in leaf_ids:
            person = people_ext.get(pid, {})
            feas = {}
            if stats:
                try:
                    feas = compute_feasibility(pid, people_ext, families_ext, stats, windowed_stats, record_dates)
                except Exception:
                    feas = {}
            if feas.get('feasibility') == 'unknown':
                continue
            estimated = feas.get('birth_year_estimated', False)
            priority = 2 if estimated else 3
            source_ref = f"leaf|{pid}"
            name = person.get('name', pid)
            birth = person.get('birth_year') or person.get('baptism_year') or ''
            place = person.get('birth_place') or person.get('baptism_place') or ''
            detail_parts = ["Sin padres conocidos."]
            if birth:
                detail_parts.append(f"Nacimiento aprox.: {birth}.")
            if place:
                detail_parts.append(f"Lugar: {place}.")
            if feas.get('estimated_parent_birth'):
                detail_parts.append(f"Padre/madre estimado/a: ~{feas['estimated_parent_birth']}.")
            tasks.append(_make_task(
                task_type    = 'find_parents',
                person_id    = person.get('id', pid),
                person_name  = name,
                title        = f"Buscar padres de {name}",
                detail       = ' '.join(detail_parts),
                priority     = priority,
                source_module= 'general',
                source_ref   = source_ref,
            ))
    except Exception:
        pass

    return tasks


def generate_tasks_from_family_completion(
    results_json: list[dict],
    confirmed_pids: set,
    min_prob: float = 0.35,
) -> list[ResearchTask]:
    """
    Genera tareas confirm_parent_candidate para candidatos con prob >= min_prob
    y cuyo orphan_pid no esté ya confirmado.
    """
    tasks: list[ResearchTask] = []
    for case in results_json:
        orphan_pid  = case.get('orphan_pid', '')
        top_prob    = case.get('top_prob', 0.0)
        if top_prob < min_prob:
            continue
        if orphan_pid in confirmed_pids:
            continue

        role        = case.get('role_needed', '')
        orphan_name = case.get('orphan_name', '')
        top_cand    = case.get('top_candidate', '')
        m_year      = case.get('marriage_year', '')
        m_place     = case.get('marriage_place', '')
        priority    = 2 if top_prob >= 0.70 else 3

        source_ref  = f"fc|{orphan_pid}|{role}"
        role_label  = 'padre' if role == 'father' else 'madre' if role == 'mother' else role

        detail_parts = [f"Candidato: {top_cand} ({top_prob:.0%})."]
        if m_year:
            detail_parts.append(f"Matrimonio: {m_year}.")
        if m_place:
            detail_parts.append(f"Lugar: {m_place}.")

        tasks.append(_make_task(
            task_type    = 'confirm_parent_candidate',
            person_id    = case.get('orphan_gramps_id', ''),
            person_name  = orphan_name,
            title        = f"Confirmar {role_label} de {orphan_name}",
            detail       = ' '.join(detail_parts),
            priority     = priority,
            source_module= 'family_completion',
            source_ref   = source_ref,
        ))
    return tasks


def generate_tasks_from_testigos(
    pending_clusters: list[dict],
    threshold: float = 5.0,
) -> list[ResearchTask]:
    """
    Genera tareas confirm_witness_identity para clusters con prioridad > threshold.
    """
    tasks: list[ResearchTask] = []
    for cluster in pending_clusters:
        prioridad = cluster.get('prioridad', 0.0)
        if prioridad <= threshold:
            continue

        variantes = cluster.get('variantes', '')
        incertidumbre = cluster.get('incertidumbre_bayes', 0.0)
        priority = 2 if incertidumbre > 0.5 else 3

        # Primer nombre de la lista de variantes como representante
        first_variant = variantes.split(' / ')[0].strip() if variantes else ''
        source_ref = f"wit|{first_variant[:30]}"

        n_ev  = cluster.get('n_eventos', 0)
        n_var = cluster.get('n_variantes', 0)
        rango = cluster.get('rango_fechas', '')
        detail = (
            f"{n_var} variantes de nombre, {n_ev} eventos. "
            f"Periodo: {rango}. "
            f"Incertidumbre: {incertidumbre:.0%}."
        )

        tasks.append(_make_task(
            task_type    = 'confirm_witness_identity',
            person_id    = '',
            person_name  = first_variant,
            title        = f"Confirmar identidad del testigo: {first_variant}",
            detail       = detail,
            priority     = priority,
            source_module= 'testigos',
            source_ref   = source_ref,
        ))
    return tasks


def generate_tasks_from_identity_resolution(
    resolution_results: list[dict],
) -> list[ResearchTask]:
    """
    Genera tareas resolve_tree_link para pares en zona de revisión (recommendation='review').
    """
    tasks: list[ResearchTask] = []
    for result in resolution_results:
        if result.get('recommendation') != 'review':
            continue

        prob         = result.get('probability', 0.0)
        witness_name = result.get('witness_name', '')
        person_name  = result.get('person_name', '')
        gramps_id    = result.get('gramps_id', '')
        priority     = 2 if prob >= 0.85 else 3

        source_ref = f"ir|{witness_name[:30]}|{gramps_id}"

        tasks.append(_make_task(
            task_type    = 'resolve_tree_link',
            person_id    = gramps_id,
            person_name  = person_name,
            title        = f"Vincular testigo '{witness_name}' con {person_name}",
            detail       = f"Probabilidad: {prob:.0%}. Testigo: {witness_name}. Árbol: {person_name} ({gramps_id}).",
            priority     = priority,
            source_module= 'identity_resolution',
            source_ref   = source_ref,
        ))
    return tasks


# ─────────────────────────────────────────────────────────────────────────────
# Merge con estado persistido
# ─────────────────────────────────────────────────────────────────────────────

def merge_with_stored(
    generated: list[ResearchTask],
    stored: list[dict],
) -> list[ResearchTask]:
    """
    Combina tareas recién generadas con el estado guardado por el usuario.

    Estrategia:
    - Índice por source_ref del almacenado.
    - Para cada tarea generada: si su source_ref existe en stored,
      copia status, notes, found_source, updated_at.
    - Tareas en stored pero no en generated se descartan
      (el hallazgo subyacente ya no existe).
    - Preserva el task_id del stored para estabilidad de widget keys.
    - Ordena: priority asc, luego probability desc (no disponible aquí → por title).
    """
    stored_by_ref: dict[str, dict] = {s['source_ref']: s for s in stored if 'source_ref' in s}

    merged: list[ResearchTask] = []
    for task in generated:
        stored_task = stored_by_ref.get(task.source_ref)
        if stored_task:
            task.task_id     = stored_task.get('task_id', task.task_id)
            task.status      = stored_task.get('status', 'pending')
            task.notes       = stored_task.get('notes', '')
            task.found_source= stored_task.get('found_source', '')
            task.updated_at  = stored_task.get('updated_at', task.updated_at)
            # Preservar created_at original
            task.created_at  = stored_task.get('created_at', task.created_at)
        merged.append(task)

    # Ordenar: prioridad ascendente, luego por title
    merged.sort(key=lambda t: (t.priority, t.title))
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# Persistencia
# ─────────────────────────────────────────────────────────────────────────────

def load_tasks(path: Path) -> list[dict]:
    """Lee data/research_tasks.json. Devuelve [] si no existe o hay error de parse."""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        pass
    return []


def save_tasks(tasks: list[ResearchTask], path: Path) -> bool:
    """
    Serializa la lista de ResearchTask y escribe a path.
    Devuelve True si tuvo éxito.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(t) for t in tasks]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        return True
    except Exception:
        return False
