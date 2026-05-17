"""
Búsqueda de documentos de archivo para testigos importantes.

Para testigos con rango_social o profesión significativa, construye queries
contextualizadas (nombre + lugar + época), las lanza contra PARES y otros
archivos digitales usando el LLM local, y evalúa la relevancia de cada resultado.

Funciones puras — sin imports de Streamlit (excepto el módulo de render).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Modelo de datos
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ArchiveDocument:
    """Un documento de archivo encontrado para un testigo."""
    title: str
    archive: str          # "PARES", "BNE", "Internet Archive", etc.
    url: str
    date_approx: str      # Fecha aproximada del documento (texto libre)
    relevance_score: float  # 0.0–1.0
    relevance_reason: str
    query_used: str


@dataclass
class WitnessArchiveResult:
    """Resultado completo de búsqueda para un testigo."""
    witness_name: str
    witness_norm: str
    note: str
    note_category: str     # rango_social | profesión
    year_min: Optional[int]
    year_max: Optional[int]
    places: list[str]
    country_code: str = "es"          # código de país detectado o elegido por el usuario
    documents: list[ArchiveDocument] = field(default_factory=list)
    search_status: str = "pending"    # pending | searched | error
    error_msg: str = ""
    llm_summary: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Clasificación de importancia
# ─────────────────────────────────────────────────────────────────────────────

# Profesiones y rangos que justifican búsqueda en archivos (excluye artesanos comunes)
_HIGH_VALUE_CATEGORIES = {"rango_social", "profesión"}

_ARCHIVABLE_PROFESION_TOKENS = {
    # Eclesiásticos con cargos relevantes
    'presbitero', 'presbiter', 'prevere', 'mossen', 'mosse', 'mosen',
    'canonge', 'canonigo', 'capellan', 'vicario', 'arcediano', 'chantre',
    'beneficiado', 'clerigo', 'reverendo',
    # Notariales / judiciales
    'notario', 'notari', 'escribano', 'secretario', 'procurador',
    'jurado', 'jurat', 'regidor', 'alcalde', 'teniente',
    # Médicos / académicos
    'doctor', 'licenciado', 'medico', 'cirujano',
    'maestro', 'catedratico',
    # Mercantiles de cierto nivel
    'mercader',
    # Militares
    'capitan',
}

_RANGO_SOCIAL_TOKENS = {
    'don', 'dona', 'senor', 'senora', 'noble', 'hidalgo', 'hidalgos',
    'marques', 'marquesa', 'conde', 'condesa', 'baron', 'baronesa',
    'cavaller', 'caballero', 'ciudadano', 'ciutada', 'ciutadans',
    'senyor', 'senyora',
}


def _strip_accents(s: str) -> str:
    return ''.join(
        c for c in unicodedata.normalize('NFKD', s)
        if not unicodedata.combining(c)
    )


def is_important_witness(note: str, note_category: str) -> bool:
    """Devuelve True si el testigo merece búsqueda en archivos."""
    if note_category == "rango_social":
        return True
    if note_category == "profesión":
        tokens = set(_strip_accents(note.lower()).split())
        return bool(tokens & _ARCHIVABLE_PROFESION_TOKENS)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Construcción de queries
# ─────────────────────────────────────────────────────────────────────────────

def _name_variants(name: str) -> list[str]:
    """Genera variantes del nombre para ampliar la búsqueda."""
    name = name.strip()
    variants = [name]
    # Nicolau → Nicolás, Nicolas
    replacements = [
        (r'\bNicolau\b', 'Nicolás'),
        (r'\bJaume\b', 'Jaime'),
        (r'\bJoan\b', 'Juan'),
        (r'\bPere\b', 'Pedro'),
        (r'\bFrancesc\b', 'Francisco'),
        (r'\bBernat\b', 'Bernardo'),
        (r'\bAntoni\b', 'Antonio'),
        (r'\bGuillem\b', 'Guillermo'),
        (r'\bMiquel\b', 'Miguel'),
        (r'\bCatalina\b', 'Catalina'),
        (r'\bIsabel\b', 'Isabel'),
    ]
    for pat, rep in replacements:
        alt = re.sub(pat, rep, name, flags=re.IGNORECASE)
        if alt != name:
            variants.append(alt)
    # Versión sin acentos
    no_acc = _strip_accents(name)
    if no_acc not in variants:
        variants.append(no_acc)
    return list(dict.fromkeys(variants))  # mantener orden, sin duplicados


def build_search_context(result: WitnessArchiveResult) -> dict:
    """Construye el contexto de búsqueda para el LLM incluyendo fuentes por país."""
    from modules.testigos.archive_sources import get_sources_for_country

    year_range = ""
    if result.year_min and result.year_max:
        year_range = f"{result.year_min}–{result.year_max}"
    elif result.year_min:
        year_range = f"c. {result.year_min}"
    elif result.year_max:
        year_range = f"antes de {result.year_max}"

    places_str = ", ".join(result.places[:5]) if result.places else ""

    sources = get_sources_for_country(result.country_code)
    country_name = sources[0].country_name_es if sources else "España"
    scrapable = [s.name for s in sources if s.can_scrape]
    link_only = [s.name for s in sources if not s.can_scrape and s.url_template]

    return {
        "name": result.witness_name,
        "name_variants": _name_variants(result.witness_name),
        "note": result.note,
        "note_category": result.note_category,
        "year_range": year_range,
        "places": places_str,
        "country_code": result.country_code,
        "country_name": country_name,
        "scrapable_sources": ", ".join(scrapable) if scrapable else "ninguna",
        "link_only_sources": ", ".join(link_only) if link_only else "ninguna",
        "sources": sources,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Prompt para el LLM local
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """Eres un asistente especializado en genealogía histórica y archivos documentales.
Tu tarea es ayudar a encontrar documentos de archivo relacionados con testigos históricos
de registros parroquiales de cualquier país europeo.

Cuando se te proporcione información sobre un testigo (nombre, profesión/rango, lugar, época y país),
debes:
1. Analizar qué tipo de documentos podrían existir en archivos históricos de ese país
2. Generar queries de búsqueda específicas para las fuentes disponibles indicadas
3. Evaluar la relevancia de los resultados encontrados
4. Estimar si los documentos corresponden a la misma persona

Responde siempre en español. Sé conciso y específico."""

_QUERY_GENERATION_PROMPT = """Testigo a investigar:
- Nombre: {name}
- Variantes del nombre: {variants}
- Nota/rol: {note}
- Categoría: {note_category}
- Período activo: {year_range}
- Lugares: {places}
- País detectado: {country_name}

Fuentes disponibles con búsqueda automática (scraping): {scrapable_sources}
Fuentes disponibles solo como enlace directo: {link_only_sources}

Genera entre 2 y 4 queries de búsqueda priorizando las fuentes con scraping automático.
Considera variantes del nombre propias del país y época (latinizaciones, traducciones, grafías alternativas).

Devuelve ÚNICAMENTE un JSON con esta estructura:
{{
  "queries": [
    {{
      "texto": "descripción de la búsqueda",
      "nombre_busqueda": "solo el nombre a buscar por frase exacta (ej: 'Nicolás Viudes')",
      "archivo": "nombre exacto de la fuente (ej: PARES)",
      "razon": "por qué esta query"
    }},
    ...
  ],
  "contexto_historico": "breve nota sobre qué tipo de documentos podrían existir para este perfil en este país"
}}

IMPORTANTE: el campo "nombre_busqueda" debe contener SOLO el nombre de la persona
(con variante local si procede), sin oficio, lugar ni fechas.
Ejemplo correcto: "nombre_busqueda": "Nicolás Viudes"
Incorrecto: "Nicolás Viudes notario Orihuela 1670"."""

_EVALUATION_PROMPT = """Evalúa si los siguientes documentos corresponden al testigo histórico.

Testigo:
- Nombre: {name}
- Variantes: {variants}
- Nota/rol: {note}
- Período: {year_range}
- Lugares: {places}

Documentos encontrados:
{documents}

Para cada documento, evalúa:
1. ¿El nombre coincide (exacto o variante plausible para la época)?
2. ¿El lugar coincide geográficamente?
3. ¿La fecha es compatible con el período de actividad?
4. ¿El cargo/título mencionado es consistente con la nota del testigo?

Devuelve ÚNICAMENTE un JSON con esta estructura:
{{
  "evaluaciones": [
    {{
      "indice": 0,
      "relevancia": 0.0-1.0,
      "razon": "explicación concisa de por qué sí/no es el mismo individuo"
    }},
    ...
  ],
  "resumen": "resumen en 2-3 frases de qué se ha encontrado y qué identidad parece más probable"
}}"""


def build_query_prompt(ctx: dict) -> list[dict]:
    """Construye los mensajes para el LLM de generación de queries."""
    user_content = _QUERY_GENERATION_PROMPT.format(
        name=ctx["name"],
        variants=", ".join(ctx["name_variants"]),
        note=ctx["note"],
        note_category=ctx["note_category"],
        year_range=ctx["year_range"] or "desconocido",
        places=ctx["places"] or "desconocido",
        country_name=ctx.get("country_name", "España"),
        scrapable_sources=ctx.get("scrapable_sources", "PARES"),
        link_only_sources=ctx.get("link_only_sources", "BNE"),
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def build_evaluation_prompt(ctx: dict, raw_results: list[str]) -> list[dict]:
    """Construye los mensajes para el LLM de evaluación de resultados."""
    docs_formatted = "\n".join(
        f"{i}. {doc}" for i, doc in enumerate(raw_results)
    )
    user_content = _EVALUATION_PROMPT.format(
        name=ctx["name"],
        variants=", ".join(ctx["name_variants"]),
        note=ctx["note"],
        year_range=ctx["year_range"] or "desconocido",
        places=ctx["places"] or "desconocido",
        documents=docs_formatted,
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def parse_llm_json(response: str) -> dict:
    """Extrae el JSON de la respuesta del LLM (que puede tener texto adicional)."""
    # Buscar bloque JSON entre llaves
    match = re.search(r'\{[\s\S]*\}', response)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Búsqueda web simulada / real
# ─────────────────────────────────────────────────────────────────────────────

_PARES_BASE = "https://pares.mcu.es/ParesBusquedas20/catalogo/find"
_BNE_BASE = "https://bvpb.mcu.es/es/consulta/busqueda.do"


def build_pares_search_url(name: str, year_min: int | None = None, year_max: int | None = None) -> str:
    """
    Construye URL de búsqueda PARES usando el parámetro fraseExacta (campo
    'Con la frase exacta' del formulario avanzado). Opcionalmente añade rango
    de fechas con anio1/anio2.
    """
    import urllib.parse
    params = {"fraseExacta": name}
    if year_min:
        params["anio1"] = str(year_min)
    if year_max:
        params["anio2"] = str(year_max)
    return f"{_PARES_BASE}?" + urllib.parse.urlencode(params)


def build_pares_texto_url(query: str) -> str:
    """Búsqueda PARES con 'Con todas las palabras' (más amplia que fraseExacta)."""
    import urllib.parse
    return f"{_PARES_BASE}?texto={urllib.parse.quote(query)}"


def build_bne_search_url(query: str) -> str:
    """Construye URL de búsqueda en Biblioteca Virtual del Patrimonio Bibliográfico (BNE/BVPB)."""
    import urllib.parse
    return f"https://bvpb.mcu.es/es/consulta/busqueda.do?texto={urllib.parse.quote(query)}&tipoForm=B"


def build_google_archive_query(query: str, site: str = "pares.mcu.es") -> str:
    """Construye query de Google limitada a un archivo."""
    return f'site:{site} "{query}"'


def fetch_pares_results(
    name: str,
    year_min: int | None = None,
    year_max: int | None = None,
    timeout: int = 15,
) -> list[dict]:
    """
    Obtiene resultados de PARES scrapeando la página de resultados.
    Usa fraseExacta con el nombre limpio (sin oficio ni fechas) para mayor precisión.
    Devuelve lista de dicts con title, url, archive. Falla silenciosamente.
    """
    import re
    import html
    try:
        import requests

        url = build_pares_search_url(name, year_min, year_max)
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0"
        })
        resp.raise_for_status()

        base = "https://pares.mcu.es"
        results = []
        seen_urls = set()

        # Extraer pares href + texto de links a description
        for m in re.finditer(
            r'href="(/ParesBusquedas20/catalogo/description/[^"]+)"[^>]*>(.*?)</a>',
            resp.text,
            re.DOTALL,
        ):
            href, raw_title = m.group(1), m.group(2)
            # Limpiar título: quitar tags HTML y entidades
            title = re.sub(r'<[^>]+>', '', raw_title)
            title = html.unescape(title).strip()
            if not title or len(title) < 4:
                continue
            doc_url = base + href
            if doc_url in seen_urls:
                continue
            seen_urls.add(doc_url)
            results.append({
                "title": title,
                "url": doc_url,
                "date_approx": "",
                "archive": "PARES",
            })

        return results[:12]

    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Motor principal de búsqueda
# ─────────────────────────────────────────────────────────────────────────────

def search_archive_for_witness(
    result: WitnessArchiveResult,
    base_url: str,
    model: str,
    llm_timeout: int = 120,
) -> WitnessArchiveResult:
    """
    Ejecuta el pipeline completo de búsqueda para un testigo:
    1. LLM genera queries contextualizadas
    2. Se busca en PARES y otros archivos
    3. LLM evalúa relevancia de cada resultado
    4. Se devuelve el result actualizado con documentos y resumen
    """
    from modules.rag_assistant.llm_client import chat_completion

    ctx = build_search_context(result)

    # Paso 1: LLM genera queries
    try:
        query_messages = build_query_prompt(ctx)
        query_response = chat_completion(
            query_messages,
            base_url=base_url,
            model=model,
            max_tokens=600,
            temperature=0.2,
            timeout=llm_timeout,
        )
        query_data = parse_llm_json(query_response)
        queries = query_data.get("queries", [])
        context_note = query_data.get("contexto_historico", "")
    except Exception as e:
        result.search_status = "error"
        result.error_msg = f"Error generando queries: {e}"
        return result

    if not queries:
        result.search_status = "error"
        result.error_msg = "El LLM no generó queries válidas."
        return result

    # Paso 2: Buscar en archivos usando las fuentes del país
    from modules.testigos.archive_sources import get_sources_for_country, build_direct_url

    sources = ctx.get("sources") or get_sources_for_country(result.country_code)
    # Índice de fuentes por nombre (para que el LLM pueda referirse por nombre)
    sources_by_name: dict[str, object] = {s.name: s for s in sources}
    # También por palabras clave simples (PARES, BNE, Digitarq…)
    for s in sources:
        short = s.name.split("—")[0].strip().split("(")[0].strip()
        sources_by_name.setdefault(short, s)

    raw_docs: list[dict] = []
    search_urls: dict[str, str] = {}  # etiqueta → url de búsqueda directa

    for q in queries[:4]:
        query_text = q.get("texto", "")
        search_name = q.get("nombre_busqueda") or query_text
        archivo_key = q.get("archivo", "")
        if not query_text:
            continue

        # Buscar la fuente que corresponde a la clave indicada por el LLM
        source = sources_by_name.get(archivo_key)
        if source is None:
            # Búsqueda aproximada: la clave puede ser un substring del nombre
            for sname, s in sources_by_name.items():
                if archivo_key.lower() in sname.lower() or sname.lower() in archivo_key.lower():
                    source = s
                    break
        # Si no se encuentra fuente, usar la primera disponible del país
        if source is None and sources:
            source = sources[0]

        if source is None:
            continue

        # Construir URL de búsqueda directa (siempre, como enlace de apoyo)
        direct_url = build_direct_url(source, search_name, result.year_min, result.year_max)
        if direct_url:
            search_urls[f"{source.name}: {search_name}"] = direct_url

        # Si tiene scraping, ejecutarlo
        if source.can_scrape and source.scrape_fn:
            try:
                docs = source.scrape_fn(search_name, result.year_min, result.year_max)
                for d in docs:
                    d["query_used"] = query_text
                    d["archive"] = source.name
                raw_docs.extend(docs)
            except Exception:
                pass

    # Añadir enlaces directos para fuentes sin scraping que el LLM no haya mencionado
    for source in sources:
        if source.can_scrape or not source.url_template:
            continue
        label = f"{source.name}: {ctx['name']}"
        if label not in search_urls:
            direct_url = build_direct_url(source, ctx["name"], result.year_min, result.year_max)
            if direct_url:
                search_urls[label] = direct_url

    # Deduplicar raw_docs por URL
    seen: set = set()
    deduped = []
    for d in raw_docs:
        if d["url"] not in seen:
            seen.add(d["url"])
            deduped.append(d)
    raw_docs = deduped

    # Paso 3: Si hay documentos scrapeados, el LLM los evalúa
    if raw_docs:
        docs_texts = [f"[{d.get('archive','?')}] {d.get('title','Sin título')} — {d.get('url','')}" for d in raw_docs]
        try:
            eval_messages = build_evaluation_prompt(ctx, docs_texts)
            eval_response = chat_completion(
                eval_messages,
                base_url=base_url,
                model=model,
                max_tokens=800,
                temperature=0.1,
                timeout=llm_timeout,
            )
            eval_data = parse_llm_json(eval_response)
            evaluaciones = eval_data.get("evaluaciones", [])
            llm_summary = eval_data.get("resumen", context_note)
        except Exception:
            evaluaciones = []
            llm_summary = context_note

        # Construir documentos con puntuación de relevancia
        score_map = {e["indice"]: e for e in evaluaciones if "indice" in e}
        for i, raw in enumerate(raw_docs):
            ev = score_map.get(i, {})
            doc = ArchiveDocument(
                title=raw.get("title", "Sin título"),
                archive=raw.get("archive", "PARES"),
                url=raw.get("url", ""),
                date_approx=raw.get("date_approx", ""),
                relevance_score=float(ev.get("relevancia", 0.5)),
                relevance_reason=ev.get("razon", ""),
                query_used=raw.get("query_used", ""),
            )
            result.documents.append(doc)

        # Ordenar por relevancia descendente
        result.documents.sort(key=lambda d: d.relevance_score, reverse=True)
        result.llm_summary = llm_summary

    else:
        # Sin documentos scrapeados: devolvemos los enlaces de búsqueda directa
        result.llm_summary = context_note

    # Siempre añadir los enlaces directos de fuentes sin scraping como documentos de apoyo
    for label, url in search_urls.items():
        # Encontrar la fuente para extraer el aviso manual
        manual_reason = "Abre el buscador del archivo con el nombre del testigo"
        for s in sources:
            if s.name in label or label.startswith(s.name):
                if not s.can_scrape:
                    manual_reason = s.manual_only_reason_es or manual_reason
                break
        # Solo añadir si no hay ya un documento de esta fuente con score > 0
        source_name = label.split(":")[0].strip()
        already_has_scraped = any(
            d.relevance_score > 0 and source_name in d.archive
            for d in result.documents
        )
        if not already_has_scraped:
            doc = ArchiveDocument(
                title=label,
                archive=source_name,
                url=url,
                date_approx="",
                relevance_score=0.0,
                relevance_reason=manual_reason,
                query_used=label,
            )
            result.documents.append(doc)

    result.search_status = "searched"
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Filtrado de testigos importantes desde el DataFrame
# ─────────────────────────────────────────────────────────────────────────────

def get_important_witnesses(
    by_witness: dict,
    classify_fn,  # callable(note_text) → str
    min_events: int = 1,
) -> list[WitnessArchiveResult]:
    """
    Extrae de by_witness los testigos que merecen búsqueda de archivo.

    classify_fn: función de clasificación de notas (classify_note del módulo principal).
    """
    results = []
    seen = set()

    for norm_name, events in by_witness.items():
        if not events:
            continue
        # Recopilar todas las notas del testigo
        notes_raw = [
            str(ev.get('note', '')).strip()
            for ev in events
            if ev.get('note') and str(ev.get('note')).strip() not in ('', 'nan', 'None')
        ]
        if not notes_raw:
            continue

        for note_text in dict.fromkeys(notes_raw):  # notas únicas, orden preservado
            cat = classify_fn(note_text)
            if not is_important_witness(note_text, cat):
                continue

            # Nombre canónico: el primer witness_raw con nota importante
            witness_name = events[0].get('witness_raw', norm_name)
            key = (norm_name, note_text)
            if key in seen:
                continue
            seen.add(key)

            if len(events) < min_events:
                continue

            # Rango temporal
            years = []
            for ev in events:
                date = ev.get('date_iso', '')
                if date:
                    try:
                        y = int(str(date)[:4])
                        if 1000 < y < 2100:
                            years.append(y)
                    except Exception:
                        pass

            places = list({ev.get('place_name', '') for ev in events if ev.get('place_name')})

            results.append(WitnessArchiveResult(
                witness_name=witness_name,
                witness_norm=norm_name,
                note=note_text,
                note_category=cat,
                year_min=min(years) if years else None,
                year_max=max(years) if years else None,
                places=sorted(places),
            ))

    # Ordenar: rango_social primero, luego por número de apariciones desc
    def _sort_key(r):
        cat_rank = 0 if r.note_category == "rango_social" else 1
        n_events = len(by_witness.get(r.witness_norm, []))
        return (cat_rank, -n_events)

    results.sort(key=_sort_key)
    return results
