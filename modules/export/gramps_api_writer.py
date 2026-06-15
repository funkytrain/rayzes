"""
modules/export/gramps_api_writer.py
------------------------------------
Escribe los cambios generados por Rayzes directamente al servidor Gramps Web API,
como alternativa a descargar un archivo .gramps modificado.

Acumula operaciones en self._ops y las ejecuta con sync():
  - Estrategia "transaction": POST /api/transactions (atómica, recomendada)
  - Estrategia "sequential":  POST/PUT individuales con ETag (fallback)

Los ítems #2 (notas en atributos de evento) y #5 (citas de archivo) siempre
usan peticiones individuales por su naturaleza multi-step.
"""

from __future__ import annotations

import datetime
import secrets
from dataclasses import dataclass, field
from typing import Optional

import requests

from modules.shared.gramps_api_client import GrampsWebWriter
from modules.shared.gramps_parser import GrampsDB


# ─────────────────────────────────────────────────────────────────────────────
# SyncResult
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SyncResult:
    success: bool
    n_ops: int
    error: str = ""
    detail: list[dict] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_TAG_COLORS = {
    "GenHelper:Error":   "#CC0000",
    "GenHelper:Warning": "#FF8C00",
}

_RELEVANCE_TO_CONFIDENCE = [
    (0.8, 4),  # Very High
    (0.6, 3),  # High
    (0.4, 2),  # Normal
    (0.2, 1),  # Low
    (0.0, 0),  # Very Low
]


def _today() -> str:
    return datetime.date.today().isoformat()


def _new_handle() -> str:
    return "_" + secrets.token_hex(14)


def _relevance_to_confidence(score: float) -> int:
    for threshold, level in _RELEVANCE_TO_CONFIDENCE:
        if score >= threshold:
            return level
    return 0


def _note_payload(text: str, handle: str | None = None) -> dict:
    """Formato de nota compatible con POST /api/notes/ y transactions."""
    import time as _time
    payload: dict = {
        "gramps_id": "N_GH_" + secrets.token_hex(6),
        "text": {"string": text, "tags": []},
        "type": "General",
        "private": False,
        "tag_list": [],
        "format": 0,
    }
    return payload


def _note_tx_payload(text: str, handle: str) -> dict:
    """Formato de nota para uso dentro de /api/transactions/ (requiere _class en campos internos)."""
    import time as _time
    return {
        "_class": "Note",
        "handle": handle,
        "gramps_id": "N_GH_" + secrets.token_hex(6),
        "text": {"_class": "StyledText", "string": text, "tags": []},
        "type": {"_class": "NoteType", "value": 1, "string": ""},
        "private": False,
        "tag_list": [],
        "format": 0,
        "change": int(_time.time()),
    }


def _tag_payload(name: str) -> dict:
    return {
        "name": name,
        "color": _TAG_COLORS.get(name, "#888888"),
        "priority": 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GrampsApiWriter
# ─────────────────────────────────────────────────────────────────────────────

class GrampsApiWriter:
    """
    Construye y ejecuta operaciones de escritura contra Gramps Web API.

    Uso:
        writer = GrampsApiWriter(base_url, token, db)
        writer.add_confirmation_notes(confirmed_links)
        writer.add_inconsistency_tags(active_issues)
        ...
        result = writer.sync()
    """

    def __init__(self, base_url: str, token: str, db: GrampsDB) -> None:
        self._web = GrampsWebWriter(base_url, token)
        self._db = db
        self._ops: list[dict] = []          # operaciones para /api/transactions
        self._changelog: list[dict] = []

        # Caches de deduplicación (cargados bajo demanda)
        self._existing_tag_handles: dict[str, str] | None = None
        self._existing_note_texts: dict[str, list[str]] = {}   # obj_handle → [text]
        self._pending_notes: dict[str, list[str]] = {}          # obj_handle → [text] añadidos en esta sesión

        # Caches para ítems de Grupo 2 — solo cargados si se usan
        self._existing_sources: dict[str, str] | None = None    # title → handle
        self._existing_repos: dict[str, str] | None = None      # name → handle

    @property
    def changelog(self) -> list[dict]:
        return self._changelog

    # ── Caches ───────────────────────────────────────────────────────────────

    def _ensure_tag_handles(self) -> dict[str, str]:
        if self._existing_tag_handles is None:
            self._existing_tag_handles = self._web.fetch_tag_handles()
        return self._existing_tag_handles

    def _ensure_source_handles(self) -> dict[str, str]:
        if self._existing_sources is None:
            self._existing_sources = self._web.fetch_source_handles()
        return self._existing_sources

    def _ensure_repo_handles(self) -> dict[str, str]:
        if self._existing_repos is None:
            self._existing_repos = self._web.fetch_repo_handles()
        return self._existing_repos

    def _get_note_texts(self, obj_type: str, handle: str) -> list[str]:
        if handle not in self._existing_note_texts:
            self._existing_note_texts[handle] = self._web.fetch_note_texts_for_object(obj_type, handle)
        return self._existing_note_texts[handle]

    def _note_already_exists(self, obj_type: str, handle: str, prefix: str) -> bool:
        for text in self._get_note_texts(obj_type, handle):
            if text.startswith(prefix):
                return True
        for text in self._pending_notes.get(handle, []):
            if text.startswith(prefix):
                return True
        return False

    def _register_pending_note(self, handle: str, text: str) -> None:
        self._pending_notes.setdefault(handle, []).append(text)

    # ── Resolución de handles ─────────────────────────────────────────────────

    def _resolve_person_handle(self, pid: str) -> str | None:
        """pid puede ser handle directo (con o sin '_' inicial) o gramps_id (Ixxxx)."""
        if not pid:
            return None
        # Handles GRAMPS XML llevan '_' prefijo; la DB de API los almacena sin él
        handle_candidate = pid.lstrip("_")
        if handle_candidate in self._db.persons:
            return handle_candidate
        # Puede que el handle venga sin '_' y ya sea correcto
        if pid in self._db.persons:
            return pid
        # Último recurso: buscar por gramps_id
        return self._db.persons_by_gramps_id.get(pid)

    def _resolve_family_handle(self, fid: str) -> str | None:
        """Busca una familia por su gramps_id."""
        for h, fam in self._db.families.items():
            if fam.id == fid:
                return h
        return None

    # ── Operación: añadir nota + noteref a un objeto ──────────────────────────

    def _queue_note_on_object(self, obj_class: str, obj_type: str, handle: str, text: str) -> None:
        """
        Añade a self._ops dos operaciones:
        1. add Note (usa _handle como handle temporal para sequential)
        2. update objeto añadiendo la nota
        """
        note_handle = _new_handle()  # sin '_': prefijo generado internamente
        self._ops.append({
            "_class": "Note",
            "_op": "add",
            "_handle": note_handle,
            "_text": text,  # texto original para construir payload en _exec_sequential
        })
        self._ops.append({
            "_class": obj_class,
            "_op": "update",
            "_handle": handle,
            "_obj_type": obj_type,
            "_note_to_add": note_handle,
        })
        self._register_pending_note(handle, text)

    # ── Operación: añadir tagref a un objeto ─────────────────────────────────

    def _queue_tag_on_object(self, obj_class: str, obj_type: str, handle: str, tag_name: str) -> None:
        tag_handles = self._ensure_tag_handles()
        tag_handle = tag_handles.get(tag_name)
        self._ops.append({
            "_class": obj_class,
            "_op": "tag",
            "_handle": handle,
            "_obj_type": obj_type,
            "_tag_name": tag_name,
            "_tag_handle": tag_handle,   # None si hay que crear el tag primero
            "_tag_color": _TAG_COLORS.get(tag_name, "#888888"),
        })

    # ─────────────────────────────────────────────────────────────────────────
    # Grupo 1 — métodos que replican GrampsWriter
    # ─────────────────────────────────────────────────────────────────────────

    def add_confirmation_notes(self, confirmed_links: dict) -> int:
        """
        Ítem #1: nota en cada persona confirmada como testigo.
        confirmed_links: {witness_name: {pid, name}}
        """
        today = _today()
        added = 0
        for witness_name, link in confirmed_links.items():
            if not isinstance(link, dict):
                continue
            pid = link.get("pid", "")
            pname = link.get("name", "")
            handle = self._resolve_person_handle(pid)
            if not handle:
                continue

            text = (
                f"Testigo confirmado: {witness_name} identificado como "
                f"{pname} ({pid}) por GenHelper {today}."
            )
            prefix = f"Testigo confirmado: {witness_name}"
            if self._note_already_exists("people", handle, prefix):
                continue

            self._queue_note_on_object("Person", "people", handle, text)
            self._changelog.append({
                "type": "confirmation_note",
                "pid": pid,
                "name": pname,
                "witness": witness_name,
            })
            added += 1
        return added

    def add_witness_attribute_notes(self, confirmed_links: dict) -> int:
        """
        Ítem #2: nota en el atributo Witness de eventos.
        Siempre usa peticiones individuales (sequential) — los atributos de eventos
        no son objetos de primer nivel en la API.
        Las operaciones se marcan con _op='witness_attr' para que sync() las procese
        de forma especial.
        """
        added = 0
        for witness_name, link in confirmed_links.items():
            if not isinstance(link, dict):
                continue
            note_text = link.get("note", "").strip()
            if not note_text:
                continue

            prefix = f"[GenHelper] {note_text[:40]}"

            # Buscar eventos que tengan un atributo Witness con este nombre
            for ev_handle, ev in self._db.events.items():
                # Verificar si este testigo aparece en el evento
                witness_found = False
                for w in (ev.witnesses or []):
                    wname = w.name if hasattr(w, "name") else str(w)
                    if wname.strip().lower() == witness_name.strip().lower():
                        witness_found = True
                        break
                if not witness_found:
                    continue

                self._ops.append({
                    "_op": "witness_attr",
                    "_ev_handle": ev_handle,
                    "_witness_name": witness_name,
                    "_note_text": f"[GenHelper] {note_text}",
                    "_note_prefix": prefix,
                })
                self._changelog.append({
                    "type": "witness_note",
                    "witness": witness_name,
                    "note": note_text,
                })
                added += 1
        return added

    def add_inconsistency_tags(self, active_issues: list[dict]) -> int:
        """
        Ítem #3: tags GenHelper:Error / GenHelper:Warning en personas y familias.
        """
        severity_to_tag = {
            "error":   "GenHelper:Error",
            "warning": "GenHelper:Warning",
        }
        target_tags: dict[tuple[str, str], tuple[str, str]] = {}  # (handle, tag_name) → (obj_class, obj_type)

        for issue in active_issues:
            severity = issue.get("severity", "")
            tag_name = severity_to_tag.get(severity)
            if not tag_name:
                continue

            pid = issue.get("pid", "")
            handle = self._db.persons_by_gramps_id.get(pid)
            obj_class, obj_type = "Person", "people"
            if not handle:
                for fam_handle, fam in self._db.families.items():
                    if fam.id == pid:
                        handle = fam_handle
                        obj_class, obj_type = "Family", "families"
                        break
            if not handle:
                continue
            target_tags[(handle, tag_name)] = (obj_class, obj_type)

        added = 0
        tag_handles = self._ensure_tag_handles()

        for (handle, tag_name), (obj_class, obj_type) in target_tags.items():
            # Comprobar si ya tiene el tag: fetch el objeto y revisar tag_list
            try:
                body, _ = self._web._get_with_etag(f"/api/{obj_type}/{handle}")
                existing_tags = body.get("tag_list", [])
                tag_handle = tag_handles.get(tag_name)
                if tag_handle and tag_handle in existing_tags:
                    continue
                # También comprobar ops pendientes
                already_queued = any(
                    op.get("_op") == "tag" and op.get("_handle") == handle and op.get("_tag_name") == tag_name
                    for op in self._ops
                )
                if already_queued:
                    continue
            except requests.HTTPError:
                pass

            self._queue_tag_on_object(obj_class, obj_type, handle, tag_name)
            self._changelog.append({
                "type": "inconsistency_tag",
                "handle": handle,
                "tag": tag_name,
            })
            added += 1
        return added

    def add_completion_candidates(self, batch_results: list[dict], min_prob: float = 0.65) -> int:
        """
        Ítem #4: nota en la familia con el candidato más probable a padre/madre.
        """
        today = _today()
        added = 0

        for case in batch_results:
            top_prob = case.get("top_prob") or 0.0
            if top_prob < min_prob:
                continue

            marriage_fid = case.get("marriage_fid", "")
            orphan_name = case.get("orphan_name", "")
            top_candidate = case.get("top_candidate", "")
            role_needed = case.get("role_needed", "")
            results = case.get("results", [])

            fam_handle = self._resolve_family_handle(marriage_fid)
            if not fam_handle:
                continue

            top_result = next(
                (r for r in results if r.get("name") == top_candidate),
                results[0] if results else {},
            )
            f1 = top_result.get("f1_score", "N/A")
            f4 = top_result.get("f4_score", "N/A")
            f5 = top_result.get("f5_score", "N/A")

            role_label = "padre" if role_needed == "father" else "madre"
            text = (
                f"Candidato probable a {role_label} de {orphan_name}: "
                f"{top_candidate} (prob={top_prob:.0%}). "
                f"Factores: F1={f1}, F4={f4}, F5={f5}. "
                f"Generado por GenHelper {today}."
            )
            prefix = f"Candidato probable a {role_label} de {orphan_name}:"
            if self._note_already_exists("families", fam_handle, prefix):
                continue

            self._queue_note_on_object("Family", "families", fam_handle, text)
            self._changelog.append({
                "type": "completion_candidate",
                "marriage_fid": marriage_fid,
                "orphan": orphan_name,
                "candidate": top_candidate,
                "prob": top_prob,
            })
            added += 1
        return added

    # ─────────────────────────────────────────────────────────────────────────
    # Grupo 2 — nuevos métodos de escritura
    # ─────────────────────────────────────────────────────────────────────────

    def add_identity_resolution_notes(self, identity_results: list[dict], threshold: float = 0.75) -> int:
        """
        Ítem #6: nota en personas con alta probabilidad de ser un testigo conocido.
        identity_results: lista de CandidatePair dicts con {pid, person_name, witness_name, probability}
        """
        today = _today()
        added = 0
        for pair in identity_results:
            prob = pair.get("probability", 0.0)
            if prob < threshold:
                continue

            pid = pair.get("pid", "")
            person_name = pair.get("person_name", "")
            witness_name = pair.get("witness_name", "")
            handle = self._resolve_person_handle(pid)
            if not handle:
                continue

            text = (
                f"Posible identificación con testigo '{witness_name}' "
                f"(confianza {prob:.0%}) por GenHelper {today}."
            )
            prefix = f"Posible identificación con testigo '{witness_name}'"
            if self._note_already_exists("people", handle, prefix):
                continue

            self._queue_note_on_object("Person", "people", handle, text)
            self._changelog.append({
                "type": "identity_note",
                "pid": pid,
                "name": person_name,
                "witness": witness_name,
                "prob": prob,
            })
            added += 1
        return added

    def add_research_task_notes(self, tasks: list[dict]) -> int:
        """
        Ítem #7: nota en personas cuya tarea de investigación está completada con fuente.
        tasks: lista de ResearchTask dicts con {person_id, title, notes, found_source, status}
        """
        today = _today()
        added = 0
        for task in tasks:
            if task.get("status") != "done":
                continue
            found_source = (task.get("found_source") or "").strip()
            if not found_source:
                continue

            person_id = task.get("person_id", "")
            title = task.get("title", "")
            task_notes = (task.get("notes") or "").strip()

            # person_id es gramps_id (ej. "I0042")
            handle = self._db.persons_by_gramps_id.get(person_id)
            if not handle:
                # También puede ser un handle directo
                handle = self._resolve_person_handle(person_id)
            if not handle:
                continue

            text = f"Tarea completada: {title}. Fuente: {found_source}."
            if task_notes:
                text += f" {task_notes}"
            text += f" [GenHelper {today}]"

            prefix = f"Tarea completada: {title}."
            if self._note_already_exists("people", handle, prefix):
                continue

            self._queue_note_on_object("Person", "people", handle, text)
            self._changelog.append({
                "type": "task_note",
                "person_id": person_id,
                "title": title,
                "source": found_source,
            })
            added += 1
        return added

    def add_archive_citations(self, archive_findings: dict, confirmed_links: dict) -> int:
        """
        Ítem #5: citas de fuentes de archivos vinculadas a personas.
        Siempre usa peticiones secuenciales (multi-step).
        Las operaciones se marcan con _op='archive_citation'.

        archive_findings: {key: {witness_norm, documents: [{title, url, archive, relevance_score}]}}
        confirmed_links: {witness_name: {pid, name}}  (puente witness→persona)
        """
        confirmed = confirmed_links if isinstance(confirmed_links, dict) else {}

        # Construir índice rápido: witness_norm → handle de persona
        witness_to_handle: dict[str, str] = {}
        for witness_name, link in confirmed.items():
            if not isinstance(link, dict):
                continue
            pid = link.get("pid", "")
            handle = self._resolve_person_handle(pid)
            if handle:
                witness_to_handle[witness_name.lower().strip()] = handle

        added = 0
        for _key, finding in archive_findings.items():
            if not isinstance(finding, dict):
                continue
            witness_norm = finding.get("witness_norm", "").lower().strip()
            person_handle = witness_to_handle.get(witness_norm)
            if not person_handle:
                continue

            documents = finding.get("documents", [])
            for doc in documents:
                if (doc.get("relevance_score") or 0.0) <= 0:
                    continue
                self._ops.append({
                    "_op": "archive_citation",
                    "_person_handle": person_handle,
                    "_doc_title": doc.get("title", ""),
                    "_doc_url": doc.get("url", ""),
                    "_archive_name": doc.get("archive", ""),
                    "_relevance_score": doc.get("relevance_score", 0.5),
                })
                self._changelog.append({
                    "type": "archive_citation",
                    "person_handle": person_handle,
                    "title": doc.get("title", ""),
                    "archive": doc.get("archive", ""),
                })
                added += 1
        return added

    # ─────────────────────────────────────────────────────────────────────────
    # sync()
    # ─────────────────────────────────────────────────────────────────────────

    def sync(self, strategy: str = "transaction") -> SyncResult:
        """
        Ejecuta todas las operaciones acumuladas contra el servidor.
        strategy: "transaction" (atómica) | "sequential" (fallback individual)
        """
        if not self._ops:
            return SyncResult(success=True, n_ops=0)

        # Separar ops especiales (siempre sequential) del resto
        special_ops = [op for op in self._ops if op.get("_op") in ("witness_attr", "archive_citation")]
        standard_ops = [op for op in self._ops if op.get("_op") not in ("witness_attr", "archive_citation")]

        completed: list[dict] = []
        failed: list[dict] = []

        # ── Ejecutar ops estándar ─────────────────────────────────────────────
        if standard_ops:
            if strategy == "transaction":
                try:
                    tx_payload = self._build_transaction_payload(standard_ops)
                    self._web.post_transaction(tx_payload)
                    completed.extend(standard_ops)
                except requests.HTTPError as e:
                    if e.response is not None and e.response.status_code in (400, 422):
                        # El servidor puede no soportar refs intra-transacción; caer a sequential
                        res = self._exec_sequential(standard_ops)
                        completed.extend(res["completed"])
                        failed.extend(res["failed"])
                    else:
                        return SyncResult(
                            success=False,
                            n_ops=0,
                            error=str(e),
                        )
                except Exception as e:
                    return SyncResult(success=False, n_ops=0, error=str(e))
            else:
                res = self._exec_sequential(standard_ops)
                completed.extend(res["completed"])
                failed.extend(res["failed"])

        # ── Ejecutar ops especiales (siempre sequential) ──────────────────────
        if special_ops:
            res = self._exec_special(special_ops)
            completed.extend(res["completed"])
            failed.extend(res["failed"])

        success = len(failed) == 0
        return SyncResult(
            success=success,
            n_ops=len(completed),
            error="" if success else f"{len(failed)} operación(es) fallidas",
            detail=failed,
        )

    # ── Construcción del payload de transacción ───────────────────────────────

    def _build_transaction_payload(self, ops: list[dict]) -> list[dict]:
        """
        Convierte self._ops en el formato que espera POST /api/transactions.
        Las operaciones add-Note y update-objeto se agrupan en pares.
        """
        # Necesitamos mapear handle temporal → índice para poder referenciar notas
        # dentro de la misma transacción. Si el servidor no lo soporta, sync() cae a sequential.
        tx: list[dict] = []
        pending_tag_creates: set[str] = set()

        tag_handles = self._ensure_tag_handles()

        for op in ops:
            op_type = op.get("_op")

            if op_type == "add":
                # Nota nueva — formato para /api/transactions/
                note_handle = op["_handle"].lstrip("_")
                text = op.get("_text", "")
                note_new = _note_tx_payload(text, note_handle)
                tx.append({
                    "type": "add",
                    "_class": "Note",
                    "handle": note_handle,
                    "old": None,
                    "new": note_new,
                })

            elif op_type == "update":
                # Actualizar objeto añadiendo una nota con el handle sin '_'
                obj_type = op["_obj_type"]
                handle = op["_handle"]
                obj_class = op["_class"]
                # Obtener body actual para construir old/new completos
                try:
                    body, _ = self._web._get_with_etag(f"/api/{obj_type}/{handle}")
                except requests.HTTPError:
                    body = {"handle": handle}
                import time as _time
                note_handle_clean = op["_note_to_add"].lstrip("_")
                old_body = dict(body)
                new_body = dict(body)
                new_body["note_list"] = list(body.get("note_list", [])) + [note_handle_clean]
                new_body["change"] = int(_time.time())
                tx.append({
                    "type": "update",
                    "_class": obj_class,
                    "handle": handle,
                    "old": old_body,
                    "new": new_body,
                })

            elif op_type == "tag":
                obj_class = op["_class"]
                obj_type = op["_obj_type"]
                handle = op["_handle"]
                tag_name = op["_tag_name"]
                tag_handle = op.get("_tag_handle") or tag_handles.get(tag_name)

                # Crear el tag si no existe
                if not tag_handle and tag_name not in pending_tag_creates:
                    tag_h = _new_handle().lstrip("_")
                    tag_new = {**_tag_payload(tag_name), "handle": tag_h}
                    tx.append({
                        "type": "add",
                        "_class": "Tag",
                        "handle": tag_h,
                        "old": None,
                        "new": tag_new,
                    })
                    pending_tag_creates.add(tag_name)

                # Actualizar objeto con el tagref
                import time as _time
                try:
                    body, _ = self._web._get_with_etag(f"/api/{obj_type}/{handle}")
                except requests.HTTPError:
                    body = {"handle": handle}
                old_body = dict(body)
                new_body = dict(body)
                tag_list = list(body.get("tag_list", []))
                if tag_handle and tag_handle not in tag_list:
                    tag_list.append(tag_handle)
                new_body["tag_list"] = tag_list
                new_body["change"] = int(_time.time())
                tx.append({
                    "type": "update",
                    "_class": obj_class,
                    "handle": handle,
                    "old": old_body,
                    "new": new_body,
                })

        return tx

    # ── Ejecución sequential ──────────────────────────────────────────────────

    def _exec_sequential(self, ops: list[dict]) -> dict:
        """Ejecuta ops estándar (add/update/tag) una a una. Devuelve {completed, failed}."""
        completed: list[dict] = []
        failed: list[dict] = []
        note_handle_map: dict[str, str] = {}  # handle_temporal → handle_real

        tag_handles = self._ensure_tag_handles()

        for op in ops:
            op_type = op.get("_op")
            try:
                if op_type == "add":
                    # Crear nota con formato correcto para POST /api/notes/
                    text = op.get("_text", "")
                    payload = _note_payload(text)
                    created = self._web._post("/api/notes/", payload)
                    real_handle = created.get("handle", "")
                    note_handle_map[op["_handle"]] = real_handle
                    completed.append(op)

                elif op_type == "update":
                    obj_type = op["_obj_type"]
                    handle = op["_handle"]
                    obj_class = op["_class"]
                    body, etag = self._web._get_with_etag(f"/api/{obj_type}/{handle}")
                    note_list = list(body.get("note_list", []))
                    tmp_handle = op["_note_to_add"]
                    real_handle = note_handle_map.get(tmp_handle, tmp_handle)
                    if real_handle and real_handle not in note_list:
                        note_list.append(real_handle)
                    body["note_list"] = note_list
                    self._web._put(f"/api/{obj_type}", handle, body, etag)
                    completed.append(op)

                elif op_type == "tag":
                    obj_class = op["_class"]
                    obj_type = op["_obj_type"]
                    handle = op["_handle"]
                    tag_name = op["_tag_name"]

                    # Crear tag si no existe
                    tag_handle = tag_handles.get(tag_name)
                    if not tag_handle:
                        created = self._web._post("/api/tags", _tag_payload(tag_name))
                        tag_handle = created.get("handle", "")
                        if tag_handle:
                            tag_handles[tag_name] = tag_handle

                    if not tag_handle:
                        failed.append({**op, "_error": "No se pudo crear el tag"})
                        continue

                    body, etag = self._web._get_with_etag(f"/api/{obj_type}/{handle}")
                    tag_list = list(body.get("tag_list", []))
                    if tag_handle not in tag_list:
                        tag_list.append(tag_handle)
                        body["tag_list"] = tag_list
                        self._web._put(f"/api/{obj_type}", handle, body, etag)
                    completed.append(op)

            except Exception as e:
                failed.append({**op, "_error": str(e)})

        return {"completed": completed, "failed": failed}

    # ── Ejecución ops especiales ──────────────────────────────────────────────

    def _exec_special(self, ops: list[dict]) -> dict:
        """Ejecuta operaciones witness_attr y archive_citation."""
        completed: list[dict] = []
        failed: list[dict] = []

        for op in ops:
            try:
                if op["_op"] == "witness_attr":
                    self._exec_witness_attr(op)
                elif op["_op"] == "archive_citation":
                    self._exec_archive_citation(op)
                completed.append(op)
            except Exception as e:
                failed.append({**op, "_error": str(e)})

        return {"completed": completed, "failed": failed}

    def _exec_witness_attr(self, op: dict) -> None:
        """
        GET evento → localizar atributo Witness → añadir nota → PUT evento.
        """
        ev_handle = op["_ev_handle"]
        witness_name = op["_witness_name"]
        note_text = op["_note_text"]
        prefix = op["_note_prefix"]

        body, etag = self._web._get_with_etag(f"/api/events/{ev_handle}")
        attr_list = body.get("attribute_list", [])
        modified = False

        for attr in attr_list:
            if attr.get("type", {}).get("string", "") != "Witness":
                continue
            if (attr.get("value") or "").strip().lower() != witness_name.strip().lower():
                continue
            # Comprobar deduplicación dentro del atributo
            attr_note_handles = attr.get("note_list", [])
            already = False
            for nh in attr_note_handles:
                try:
                    note_body, _ = self._web._get_with_etag(f"/api/notes/{nh}")
                    text = note_body.get("text", {}).get("string", "")
                    if text.startswith(prefix):
                        already = True
                        break
                except requests.HTTPError:
                    continue
            if already:
                continue

            # Crear la nota
            note_created = self._web._post("/api/notes", _note_payload(note_text))
            note_handle = note_created.get("handle", "")
            if note_handle:
                attr.setdefault("note_list", []).append(note_handle)
                modified = True

        if modified:
            self._web._put("/api/events", ev_handle, body, etag)

    def _exec_archive_citation(self, op: dict) -> None:
        """
        Crea Repository (si no existe) → Source → Citation → añade a persona.
        """
        person_handle = op["_person_handle"]
        doc_title = op["_doc_title"]
        doc_url = op["_doc_url"]
        archive_name = op["_archive_name"]
        relevance = op["_relevance_score"]
        confidence = _relevance_to_confidence(relevance)

        repo_handles = self._ensure_repo_handles()
        source_handles = self._ensure_source_handles()

        # 1. Repositorio
        repo_handle = repo_handles.get(archive_name)
        if not repo_handle:
            created = self._web._post("/api/repositories", {
                "_class": "Repository",
                "name": archive_name,
                "type": {"_class": "RepositoryType", "string": "Website"},
            })
            repo_handle = created.get("handle", "")
            if repo_handle:
                repo_handles[archive_name] = repo_handle

        # 2. Fuente
        source_handle = source_handles.get(doc_title)
        if not source_handle:
            source_payload: dict = {
                "_class": "Source",
                "title": doc_title,
                "attribute_list": [
                    {
                        "_class": "SrcAttribute",
                        "type": {"_class": "SrcAttributeType", "string": "URL"},
                        "value": doc_url,
                    }
                ],
            }
            if repo_handle:
                source_payload["reporef_list"] = [{
                    "_class": "RepoRef",
                    "ref": repo_handle,
                    "media_type": {"_class": "SourceMediaType", "string": "Electronic"},
                }]
            created = self._web._post("/api/sources", source_payload)
            source_handle = created.get("handle", "")
            if source_handle:
                source_handles[doc_title] = source_handle

        if not source_handle:
            raise RuntimeError(f"No se pudo crear la fuente '{doc_title}'")

        # 3. Cita
        citation_created = self._web._post("/api/citations", {
            "_class": "Citation",
            "source_handle": source_handle,
            "confidence": confidence,
            "page": doc_url,
        })
        citation_handle = citation_created.get("handle", "")
        if not citation_handle:
            raise RuntimeError(f"No se pudo crear la cita para '{doc_title}'")

        # 4. Añadir cita a la persona
        body, etag = self._web._get_with_etag(f"/api/people/{person_handle}")
        citation_list = list(body.get("citation_list", []))
        if citation_handle not in citation_list:
            citation_list.append(citation_handle)
            body["citation_list"] = citation_list
            self._web._put("/api/people", person_handle, body, etag)
