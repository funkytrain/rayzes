from __future__ import annotations

import json


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI-compatible tool schemas
# ─────────────────────────────────────────────────────────────────────────────

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "count_persons",
            "description": (
                "Cuenta personas en el árbol genealógico con filtros opcionales. "
                "Úsalo para preguntas como '¿cuántos García hay?' o "
                "'¿cuántas mujeres nacieron en Sevilla?'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "surname": {
                        "type": "string",
                        "description": "Apellido a filtrar (búsqueda parcial, insensible a mayúsculas).",
                    },
                    "birth_place": {
                        "type": "string",
                        "description": "Lugar de nacimiento a filtrar (búsqueda parcial).",
                    },
                    "birth_year_from": {
                        "type": "integer",
                        "description": "Año de nacimiento mínimo (inclusive).",
                    },
                    "birth_year_to": {
                        "type": "integer",
                        "description": "Año de nacimiento máximo (inclusive).",
                    },
                    "sex": {
                        "type": "string",
                        "enum": ["M", "F"],
                        "description": "Sexo: M = hombre, F = mujer.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_persons",
            "description": (
                "Busca personas por nombre, apellido, lugar o fecha. "
                "Úsalo para '¿quién nació en X?' o 'busca a los García nacidos entre 1700 y 1800'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Nombre de pila (búsqueda parcial).",
                    },
                    "surname": {
                        "type": "string",
                        "description": "Apellido (búsqueda parcial).",
                    },
                    "birth_place": {
                        "type": "string",
                        "description": "Lugar de nacimiento (búsqueda parcial).",
                    },
                    "death_place": {
                        "type": "string",
                        "description": "Lugar de defunción (búsqueda parcial).",
                    },
                    "birth_year_from": {"type": "integer"},
                    "birth_year_to": {"type": "integer"},
                    "sex": {
                        "type": "string",
                        "enum": ["M", "F"],
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Máximo de resultados a devolver (por defecto 20).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_person_details",
            "description": (
                "Obtiene información completa de una persona por su ID de Gramps (ej. 'I0023'): "
                "padres, cónyuge(s), hijos, eventos vitales y notas."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "gramps_id": {
                        "type": "string",
                        "description": "El ID Gramps de la persona (ej. 'I0023').",
                    },
                },
                "required": ["gramps_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_common_ancestors",
            "description": (
                "Encuentra los antecesores comunes entre dos personas indicadas por su ID Gramps. "
                "Úsalo para preguntas sobre parentesco, consanguinidad o antepasados compartidos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id_a": {"type": "string", "description": "ID Gramps de la primera persona."},
                    "id_b": {"type": "string", "description": "ID Gramps de la segunda persona."},
                },
                "required": ["id_a", "id_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_relationship",
            "description": (
                "Explica en texto la relación genealógica entre dos personas "
                "(primos, tíos, abuelos, etc.) e indica el coeficiente de parentesco."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id_a": {"type": "string", "description": "ID Gramps de la primera persona."},
                    "id_b": {"type": "string", "description": "ID Gramps de la segunda persona."},
                },
                "required": ["id_a", "id_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_family_details",
            "description": (
                "Obtiene información de una familia (matrimonio) por su ID Gramps (ej. 'F0001'): "
                "cónyuges, hijos, año y lugar de matrimonio."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "gramps_id": {
                        "type": "string",
                        "description": "El ID Gramps de la familia (ej. 'F0001').",
                    },
                },
                "required": ["gramps_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_events",
            "description": (
                "Lista eventos del árbol genealógico filtrados por tipo, lugar y/o rango de años. "
                "Úsalo para '¿qué matrimonios hubo en Sevilla entre 1800 y 1850?'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_type": {
                        "type": "string",
                        "description": "Tipo de evento (ej. 'birth', 'marriage', 'death', 'baptism'). Búsqueda parcial.",
                    },
                    "place": {
                        "type": "string",
                        "description": "Lugar del evento (búsqueda parcial).",
                    },
                    "year_from": {"type": "integer"},
                    "year_to": {"type": "integer"},
                    "limit": {
                        "type": "integer",
                        "description": "Máximo de resultados (por defecto 20).",
                    },
                },
                "required": [],
            },
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Networkx graph (lazy, cached in session state)
# ─────────────────────────────────────────────────────────────────────────────

def _get_graph(db):
    import streamlit as st  # imported here to avoid top-level Streamlit dependency
    cached = st.session_state.get("rag_nx_graph")
    if cached is not None:
        return cached
    try:
        from modules.consanguinidad.app import build_graph
        G = build_graph(db.to_persons_dict(), db.to_families_dict())
        st.session_state["rag_nx_graph"] = G
        return G
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Execution functions
# ─────────────────────────────────────────────────────────────────────────────

def count_persons(db, *, surname=None, birth_place=None,
                  birth_year_from=None, birth_year_to=None, sex=None) -> dict:
    matched = []
    for p in db.persons.values():
        if surname and surname.lower() not in p.name.lower():
            continue
        if birth_place and (not p.birth_place or birth_place.lower() not in p.birth_place.lower()):
            continue
        if birth_year_from and (not p.birth_year or p.birth_year < birth_year_from):
            continue
        if birth_year_to and (not p.birth_year or p.birth_year > birth_year_to):
            continue
        if sex and p.sex != sex:
            continue
        matched.append({"id": p.id, "name": p.name, "birth_year": p.birth_year})
    return {"count": len(matched), "persons": matched[:50]}


def search_persons(db, *, name=None, surname=None, birth_place=None,
                   death_place=None, birth_year_from=None, birth_year_to=None,
                   sex=None, limit=20) -> list[dict]:
    results = []
    for p in db.persons.values():
        full_name = p.name.lower()
        if name and name.lower() not in full_name:
            continue
        if surname and surname.lower() not in full_name:
            continue
        if birth_place and (not p.birth_place or birth_place.lower() not in p.birth_place.lower()):
            continue
        if death_place and (not p.death_place or death_place.lower() not in p.death_place.lower()):
            continue
        if birth_year_from and (not p.birth_year or p.birth_year < birth_year_from):
            continue
        if birth_year_to and (not p.birth_year or p.birth_year > birth_year_to):
            continue
        if sex and p.sex != sex:
            continue
        results.append({
            "id": p.id,
            "name": p.name,
            "sex": p.sex,
            "birth_year": p.birth_year,
            "birth_place": p.birth_place,
            "death_year": p.death_year,
            "death_place": p.death_place,
        })
        if len(results) >= limit:
            break
    return results


def get_person_details(db, gramps_id: str) -> dict:
    handle = db.persons_by_gramps_id.get(gramps_id)
    if not handle:
        return {"error": f"Persona '{gramps_id}' no encontrada en el árbol."}
    p = db.persons[handle]

    parents, spouses, children = [], [], []
    for fam in db.families.values():
        if handle in fam.child_handles:
            for ph in (fam.husband_handle, fam.wife_handle):
                if ph and ph in db.persons:
                    pr = db.persons[ph]
                    parents.append({"id": pr.id, "name": pr.name})
        if handle in (fam.husband_handle, fam.wife_handle):
            other = fam.wife_handle if handle == fam.husband_handle else fam.husband_handle
            if other and other in db.persons:
                sp = db.persons[other]
                spouses.append({"id": sp.id, "name": sp.name,
                                "marriage_year": fam.marriage_year,
                                "marriage_place": fam.marriage_place})
            for ch in fam.child_handles:
                if ch in db.persons:
                    c = db.persons[ch]
                    children.append({"id": c.id, "name": c.name, "birth_year": c.birth_year})

    return {
        "id": p.id,
        "name": p.name,
        "sex": p.sex,
        "birth_year": p.birth_year,
        "birth_place": p.birth_place,
        "baptism_year": p.baptism_year,
        "baptism_place": p.baptism_place,
        "death_year": p.death_year,
        "death_place": p.death_place,
        "parents": parents,
        "spouses": spouses,
        "children": children,
        "n_children": len(children),
        "notes": p.note_texts[:3],
        "events": p.events_summary,
    }


def find_common_ancestors_tool(db, id_a: str, id_b: str) -> dict:
    from modules.consanguinidad.app import find_common_ancestors as _fca
    G = _get_graph(db)
    if G is None:
        return {"error": "No se pudo construir el grafo de parentesco."}
    if id_a not in G.nodes:
        return {"error": f"Persona '{id_a}' no encontrada en el grafo."}
    if id_b not in G.nodes:
        return {"error": f"Persona '{id_b}' no encontrada en el grafo."}
    commons = _fca(G, id_a, id_b, max_gen=12)
    if not commons:
        return {"found": False, "message": f"No se encontraron antecesores comunes entre {id_a} e {id_b}."}
    return {"found": True, "count": len(commons), "ancestors": commons[:10]}


def explain_relationship_tool(db, id_a: str, id_b: str) -> dict:
    from modules.consanguinidad.app import explain_relationship as _er
    G = _get_graph(db)
    if G is None:
        return {"error": "No se pudo construir el grafo de parentesco."}
    if id_a not in G.nodes:
        return {"error": f"Persona '{id_a}' no encontrada en el grafo."}
    if id_b not in G.nodes:
        return {"error": f"Persona '{id_b}' no encontrada en el grafo."}
    cache = {}
    text, summary = _er(G, id_a, id_b, cache, max_gen=12)
    return {
        "explanation": text,
        "phi": summary.get("phi"),
        "label": summary.get("label"),
    }


def get_family_details(db, gramps_id: str) -> dict:
    for fam in db.families.values():
        if fam.id != gramps_id:
            continue
        husband = wife = None
        if fam.husband_handle and fam.husband_handle in db.persons:
            h = db.persons[fam.husband_handle]
            husband = {"id": h.id, "name": h.name}
        if fam.wife_handle and fam.wife_handle in db.persons:
            w = db.persons[fam.wife_handle]
            wife = {"id": w.id, "name": w.name}
        children = []
        for ch in fam.child_handles:
            if ch in db.persons:
                c = db.persons[ch]
                children.append({"id": c.id, "name": c.name, "birth_year": c.birth_year})
        return {
            "id": fam.id,
            "husband": husband,
            "wife": wife,
            "marriage_year": fam.marriage_year,
            "marriage_place": fam.marriage_place,
            "children": children,
            "n_children": len(children),
            "marriage_notes": fam.marriage_notes[:3],
        }
    return {"error": f"Familia '{gramps_id}' no encontrada en el árbol."}


def list_events(db, *, event_type=None, place=None,
                year_from=None, year_to=None, limit=20) -> list[dict]:
    from modules.shared.utils import safe_year
    results = []
    for ev in db.events.values():
        if event_type and event_type.lower() not in ev.type.lower():
            continue
        if place:
            place_name = ""
            if ev.place_handle and ev.place_handle in db.places:
                place_name = db.places[ev.place_handle].name or ""
            if place.lower() not in place_name.lower():
                continue
        year = safe_year(ev.date_iso)
        if year_from and (year is None or year < year_from):
            continue
        if year_to and (year is None or year > year_to):
            continue
        place_name = ""
        if ev.place_handle and ev.place_handle in db.places:
            place_name = db.places[ev.place_handle].name or ""
        results.append({
            "id": ev.id,
            "type": ev.type,
            "year": year,
            "place": place_name,
            "subject": ev.subject_name,
            "n_witnesses": len(ev.witnesses),
        })
        if len(results) >= limit:
            break
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def execute_tool(name: str, arguments_json: str, db) -> str:
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"JSON inválido en argumentos: {e}"})
    try:
        if name == "count_persons":
            result = count_persons(db, **args)
        elif name == "search_persons":
            result = search_persons(db, **args)
        elif name == "get_person_details":
            result = get_person_details(db, **args)
        elif name == "find_common_ancestors":
            result = find_common_ancestors_tool(db, **args)
        elif name == "explain_relationship":
            result = explain_relationship_tool(db, **args)
        elif name == "get_family_details":
            result = get_family_details(db, **args)
        elif name == "list_events":
            result = list_events(db, **args)
        else:
            result = {"error": f"Herramienta desconocida: '{name}'"}
    except Exception as e:
        result = {"error": f"Error ejecutando '{name}': {str(e)}"}
    return json.dumps(result, ensure_ascii=False, default=str)
