# ============================================================
# modules/general/app.py — Sección General de Rayzes
# Sub-páginas: Extremos del árbol | Inconsistencias
# ============================================================

import streamlit as st
import pandas as pd
import networkx as nx
import xml.etree.ElementTree as ET
import json
import re
import statistics
import math
from collections import defaultdict
from pathlib import Path
from io import StringIO

from translations import t, get_lang

DATA_DIR = Path(__file__).parent.parent.parent / "data"
RECORD_DATES_FILE = DATA_DIR / "gen_record_dates.json"
DISMISSED_FILE = DATA_DIR / "dismissed_inconsistencies.json"
HISTORICAL_DIR = DATA_DIR / "historical"

TODAY_YEAR = 2026

# ============================================================
# Utilities (copiadas de consanguinidad/app.py para no importar el módulo entero)
# ============================================================

def strip_ns(tag: str):
    return tag.split('}')[-1] if '}' in tag else tag


def text_of(elem):
    if elem is None:
        return None
    if elem.text and elem.text.strip():
        return elem.text.strip()
    return None


def safe_int_year(val):
    if val is None:
        return None
    try:
        if isinstance(val, int):
            return val
        s = str(val)
        m = re.search(r"(\d{4})", s)
        if m:
            return int(m.group(1))
    except Exception:
        return None
    return None


def safe_percentile(data, p):
    """p en 0..100, retorna el valor en ese percentil."""
    if not data:
        return None
    sorted_data = sorted(data)
    n = len(sorted_data)
    idx = p / 100.0 * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    return sorted_data[lo] + (idx - lo) * (sorted_data[hi] - sorted_data[lo])


# ============================================================
# Parser extendido de GRAMPS XML
# ============================================================

def parse_gramps_extended(content_bytes: bytes):
    """
    Parsea un archivo GRAMPS XML y extrae personas y familias con campos adicionales
    respecto al parser de consanguinidad: bautismo, matrimonio, todos los eventos por
    persona, y flag has_parents.

    Retorna:
      people_ext: {pid: {id, name, sex, birth_year, birth_place,
                          baptism_year, baptism_place, death_year, death_place,
                          has_parents, events: [{type, year, place}]}}
      families_ext: {fid: {id, husband, wife, children, marriage_year, marriage_place}}
    """
    if content_bytes.startswith(b'\xef\xbb\xbf'):
        content_bytes = content_bytes[3:]
    content_bytes = content_bytes.lstrip()

    if content_bytes[:4] == b'PK\x03\x04':
        st.error(t("package_error"))
        return {}, {}

    try:
        root = ET.fromstring(content_bytes)
    except Exception as e:
        st.error(t("xml_error").format(e))
        return {}, {}

    # --- Paso 0: lugares ---
    place_map = {}
    for pl in root.iter():
        if strip_ns(pl.tag).lower() != 'placeobj':
            continue
        ph = pl.get('handle') or pl.get('id')
        if not ph:
            continue
        pname = plat = plon = None
        for pch in pl:
            pctag = strip_ns(pch.tag).lower()
            if pctag in ('pname', 'ptitle', 'title', 'name'):
                pname = pch.get('value') or (pch.text.strip() if pch.text else None)
            elif pctag == 'coord':
                try:
                    plat = float(pch.get('lat') or pch.get('latitude') or '')
                except (TypeError, ValueError):
                    pass
                try:
                    plon = float(pch.get('long') or pch.get('lon') or pch.get('longitude') or '')
                except (TypeError, ValueError):
                    pass
        place_map[ph] = {'name': pname, 'lat': plat, 'lon': plon}

    # --- Paso 0b: notas (handle -> texto) ---
    notes_map = {}
    for note in root.iter():
        if strip_ns(note.tag).lower() != 'note':
            continue
        nhandle = note.get('handle') or note.get('id')
        if not nhandle:
            continue
        text = ''
        for ch in note.iter():
            if strip_ns(ch.tag).lower() == 'text' and ch.text:
                text = ch.text.strip()
                break
        if not text and note.text:
            text = note.text.strip()
        notes_map[nhandle] = text

    # --- Paso 1: eventos ---
    events = {}
    for ev in root.iter():
        tag = strip_ns(ev.tag).lower()
        if tag not in ('event', 'events'):
            continue
        ev_id = ev.get('id') or ev.get('handle')
        if not ev_id:
            continue
        date_text = None
        place_name = None
        place_hlink = None
        lat = lon = None
        ev_type = None
        ev_note_texts = []
        for ch in ev:
            ctag = strip_ns(ch.tag).lower()
            if ctag == 'type':
                ev_type = (ch.text or '').strip().lower()
            if ctag == 'dateval':
                date_text = ch.get('val') or date_text
            if ctag in ('date', 'date_iso', 'formatted'):
                if ch.text and ch.text.strip():
                    date_text = date_text or ch.text.strip()
            if ctag in ('place', 'place_ref'):
                place_hlink = ch.get('hlink') or ch.get('handle') or place_hlink
                if ch.text and ch.text.strip():
                    place_name = ch.text.strip()
                for pch in ch:
                    if strip_ns(pch.tag).lower() in ('name', 'placename') and pch.text:
                        place_name = pch.text.strip()
            if ctag in ('latitude', 'lat'):
                try:
                    lat = float(ch.text.strip())
                except Exception:
                    pass
            if ctag in ('longitude', 'lon', 'long'):
                try:
                    lon = float(ch.text.strip())
                except Exception:
                    pass
            if ctag == 'noteref':
                nh = ch.get('hlink') or ch.get('handle')
                if nh and nh in notes_map:
                    ev_note_texts.append(notes_map[nh])
        if place_hlink and place_hlink in place_map:
            pl_data = place_map[place_hlink]
            place_name = place_name or pl_data.get('name')
            lat = lat if lat is not None else pl_data.get('lat')
            lon = lon if lon is not None else pl_data.get('lon')
        entry = {'date': date_text, 'place': place_name, 'lat': lat, 'lon': lon,
                 'type': ev_type, 'notes': ev_note_texts}
        events[ev_id] = entry
        if ev.get('handle') and ev.get('handle') != ev_id:
            events[ev.get('handle')] = entry

    # --- Paso 2: personas ---
    people_ext = {}
    person_eventrefs = defaultdict(list)
    fam_children = defaultdict(list)
    fam_parents_map = defaultdict(list)
    # Mapa handle_persona → id_persona (para resolver father/mother en familias)
    person_handle_to_id = {}

    for p in root.iter():
        if strip_ns(p.tag).lower() != 'person':
            continue
        pid = p.get('id') or p.get('handle') or f"UNKNOWN_{len(people_ext)+1}"
        phandle = p.get('handle')
        if phandle and phandle != pid:
            person_handle_to_id[phandle] = pid
        name = sex = None
        raw_birth_date = None
        person_note_texts = []

        for c in p:
            tag = strip_ns(c.tag).lower()
            if tag == 'noteref':
                nh = c.get('hlink') or c.get('handle')
                if nh and nh in notes_map:
                    person_note_texts.append(notes_map[nh])
            if tag in ('name', 'names'):
                first = last = ''
                for n in c:
                    nt = strip_ns(n.tag).lower()
                    if nt in ('full', 'formatted', 'fullname'):
                        if n.text and n.text.strip():
                            name = n.text.strip()
                            break
                    if nt in ('first', 'given'):
                        if n.text:
                            first = n.text.strip()
                    if nt in ('last', 'surname', 'family'):
                        if n.text:
                            last = n.text.strip()
                if not name:
                    candidate = (first + ' ' + last).strip()
                    if candidate:
                        name = candidate
            elif tag in ('gender', 'sex'):
                sex = text_of(c)
            elif tag in ('birth', 'birthdate'):
                if list(c):
                    for ch in c:
                        if strip_ns(ch.tag).lower() in ('date', 'date_iso'):
                            raw_birth_date = text_of(ch)
                        if strip_ns(ch.tag).lower() in ('eventref', 'event_ref', 'event'):
                            h = ch.get('hlink') or ch.get('handle') or ch.get('id')
                            if h:
                                person_eventrefs[pid].append(h)
                else:
                    raw_birth_date = text_of(c)
            elif tag in ('eventref', 'event_ref'):
                h = c.get('hlink') or c.get('handle') or c.get('id')
                if h:
                    person_eventrefs[pid].append(h)
            elif tag == 'childof':
                fh = c.get('hlink') or c.get('handle') or c.get('ref')
                if fh:
                    fam_children[fh].append(pid)
            elif tag == 'parentin':
                fh = c.get('hlink') or c.get('handle') or c.get('ref')
                if fh:
                    fam_parents_map[fh].append(pid)

        if not name:
            fn = p.find(".//fullname")
            if fn is not None and fn.text:
                name = fn.text.strip()
        if not name:
            name = pid

        people_ext[pid] = {
            'id': pid,
            'name': name,
            'sex': sex or '',
            'birth_year': safe_int_year(raw_birth_date),
            'birth_place': None,
            'baptism_year': None,
            'baptism_place': None,
            'death_year': None,
            'death_place': None,
            'has_parents': False,
            'events': [],
            'notes': person_note_texts,  # textos de notas vinculadas a la persona
        }

    # --- Paso 3: familias con marriage_year ---
    # Primer sub-paso: registrar handles de familia
    fam_handle_to_id = {}
    families_ext = {}
    for f in root.iter():
        if strip_ns(f.tag).lower() != 'family':
            continue
        fid = f.get('id') or f.get('handle') or f"FAM_{len(families_ext)+1}"
        fhandle = f.get('handle')
        entry_fam = {
            'id': fid,
            'husband': None,
            'wife': None,
            'children': [],
            'marriage_year': None,
            'marriage_place': None,
            'marriage_notes': [],  # notas del evento de matrimonio
        }
        families_ext[fid] = entry_fam
        if fhandle:
            fam_handle_to_id[fhandle] = fid
            families_ext[fhandle] = entry_fam  # alias por handle

        # Extraer husband, wife, children e eventref de matrimonio directamente
        # Los hlinks apuntan a handles de persona; resolverlos a IDs via person_handle_to_id
        for ch in f:
            ctag = strip_ns(ch.tag).lower()
            if ctag == 'father':
                hlink = ch.get('hlink') or ch.get('handle')
                if hlink:
                    entry_fam['husband'] = person_handle_to_id.get(hlink, hlink)
            elif ctag == 'mother':
                hlink = ch.get('hlink') or ch.get('handle')
                if hlink:
                    entry_fam['wife'] = person_handle_to_id.get(hlink, hlink)
            elif ctag == 'child':
                hlink = ch.get('hlink') or ch.get('handle')
                if hlink:
                    entry_fam['children'].append(person_handle_to_id.get(hlink, hlink))
            elif ctag == 'eventref':
                ev_hlink = ch.get('hlink') or ch.get('handle')
                if ev_hlink and ev_hlink in events:
                    ev = events[ev_hlink]
                    ev_type_str = (ev.get('type') or '').lower()
                    if ev_type_str in ('marriage', 'matrimonio', 'casamiento', 'married'):
                        entry_fam['marriage_year'] = safe_int_year(ev.get('date'))
                        entry_fam['marriage_place'] = ev.get('place')
                        entry_fam['marriage_notes'] = ev.get('notes', [])

    # Reconciliar fam_children (de <childof> en personas) en familias
    for fh, kids in fam_children.items():
        fid = fam_handle_to_id.get(fh, fh)
        if fid not in families_ext:
            families_ext[fid] = {'id': fid, 'husband': None, 'wife': None, 'children': [],
                                  'marriage_year': None, 'marriage_place': None}
        families_ext[fid]['children'].extend(kids)

    # Reconciliar fam_parents_map (de <parentin> en personas) — solo si husband/wife aún vacíos
    for fh, pids in fam_parents_map.items():
        fid = fam_handle_to_id.get(fh, fh)
        if fid not in families_ext:
            families_ext[fid] = {'id': fid, 'husband': None, 'wife': None, 'children': [],
                                  'marriage_year': None, 'marriage_place': None}
        fam = families_ext[fid]
        if pids and not fam['husband']:
            fam['husband'] = pids[0]
        if len(pids) >= 2 and not fam['wife']:
            fam['wife'] = pids[1]

    # Eliminar alias por handle (quedan entradas duplicadas) — conservar solo IDs reales
    real_fam_ids = set(fam_handle_to_id.values())
    # Si no había ningún ID real, quedarse con todo
    if real_fam_ids:
        families_ext = {k: v for k, v in families_ext.items() if k in real_fam_ids}

    # Deduplicar children
    for fam in families_ext.values():
        fam['children'] = list(dict.fromkeys(fam.get('children', [])))

    # Marcar has_parents en personas que son hijos de alguna familia
    children_ids = set()
    for fam in families_ext.values():
        for cid in fam.get('children', []):
            children_ids.add(cid)
    for pid in people_ext:
        if pid in children_ids:
            people_ext[pid]['has_parents'] = True

    # Rellenar birth/baptism/death desde eventos por persona
    BAPTISM_TYPES = {'baptism', 'christening', 'christen', 'bautismo', 'bautizo', 'christened',
                     'baptized', 'baptised'}
    DEATH_TYPES = {'death', 'burial', 'cremation', 'entierro', 'defunción', 'defuncion',
                   'óbito', 'obito'}
    BIRTH_TYPES = {'birth', 'nacimiento'}

    for pid, evlist in person_eventrefs.items():
        if pid not in people_ext:
            continue
        p = people_ext[pid]
        for h in evlist:
            ev = events.get(h)
            if not ev:
                continue
            ev_type_str = (ev.get('type') or '').lower()
            year = safe_int_year(ev.get('date'))
            place = ev.get('place')

            # Registrar en lista de eventos
            p['events'].append({'type': ev_type_str, 'year': year, 'place': place})

            if ev_type_str in BIRTH_TYPES:
                if not p['birth_year'] and year:
                    p['birth_year'] = year
                if not p['birth_place'] and place:
                    p['birth_place'] = place
            elif ev_type_str in BAPTISM_TYPES:
                if not p['baptism_year'] and year:
                    p['baptism_year'] = year
                if not p['baptism_place'] and place:
                    p['baptism_place'] = place
            elif ev_type_str in DEATH_TYPES:
                if not p['death_year'] and year:
                    p['death_year'] = year
                if not p['death_place'] and place:
                    p['death_place'] = place

    return people_ext, families_ext


# ============================================================
# Grafo de pedigree (copiado de consanguinidad/app.py:694)
# ============================================================

def build_graph(people, families):
    G = nx.DiGraph()
    for pid, pdata in people.items():
        G.add_node(pid, **{k: v for k, v in pdata.items() if not isinstance(v, list)})
    for fid, fdata in families.items():
        parents = []
        if fdata.get('husband'):
            parents.append(fdata['husband'])
        if fdata.get('wife'):
            parents.append(fdata['wife'])
        for child in fdata.get('children', []):
            for par in parents:
                if par and par in G.nodes and child in G.nodes:
                    G.add_edge(par, child)
    return G


# ============================================================
# Individuos extremos (sin padres conocidos)
# ============================================================

def find_leaf_individuals(people_ext, families_ext):
    """Retorna lista de person_ids sin padres conocidos en ninguna familia."""
    return [pid for pid, p in people_ext.items() if not p.get('has_parents', False)]


# ============================================================
# Estadísticas derivadas del propio archivo
# ============================================================

def compute_file_statistics(people_ext, families_ext):
    """Calcula estadísticas genealógicas globales del árbol."""
    marriage_ages_m = []
    marriage_ages_f = []
    inter_birth_intervals = []
    parenthood_ages = []
    mother_age_last_child = []
    father_age_last_child = []

    for fid, fam in families_ext.items():
        m_year = fam.get('marriage_year')

        # Edades al matrimonio
        for role, sex_expected in [('husband', 'M'), ('wife', 'F')]:
            pid = fam.get(role)
            if pid is None:
                continue
            p = people_ext.get(pid, {})
            birth = p.get('birth_year') or p.get('baptism_year')
            if birth and m_year and m_year > birth:
                age = m_year - birth
                if 10 <= age <= 100:
                    if sex_expected == 'M':
                        marriage_ages_m.append(age)
                    else:
                        marriage_ages_f.append(age)

        # Hijos con año conocido
        children_years = sorted([
            y for y in (
                people_ext.get(cid, {}).get('birth_year') or
                people_ext.get(cid, {}).get('baptism_year')
                for cid in fam.get('children', [])
            ) if y
        ])

        # Intervalos entre hermanos (en meses, aprox. año * 12)
        for i in range(1, len(children_years)):
            interval_months = (children_years[i] - children_years[i - 1]) * 12
            if 0 < interval_months < 600:
                inter_birth_intervals.append(interval_months)

        if children_years:
            first_child_year = children_years[0]
            last_child_year = children_years[-1]

            for role in ['husband', 'wife']:
                pid = fam.get(role)
                if pid:
                    birth = people_ext.get(pid, {}).get('birth_year') or \
                            people_ext.get(pid, {}).get('baptism_year')
                    if birth:
                        age_first = first_child_year - birth
                        if 14 <= age_first <= 70:
                            parenthood_ages.append(age_first)
                        age_last = last_child_year - birth
                        if role == 'wife' and 14 <= age_last <= 70:
                            mother_age_last_child.append(age_last)
                        elif role == 'husband' and 14 <= age_last <= 90:
                            father_age_last_child.append(age_last)

    def stats_for(data):
        if not data:
            return {'median': None, 'p5': None, 'p95': None, 'n': 0}
        return {
            'median': round(statistics.median(data), 1),
            'p5': round(safe_percentile(data, 5), 1),
            'p95': round(safe_percentile(data, 95), 1),
            'n': len(data),
        }

    return {
        'marriage_age_m': stats_for(marriage_ages_m),
        'marriage_age_f': stats_for(marriage_ages_f),
        'inter_birth': stats_for(inter_birth_intervals),
        'parenthood_age': stats_for(parenthood_ages),
        'mother_age_last': stats_for(mother_age_last_child),
        'father_age_last': stats_for(father_age_last_child),
    }


def compute_windowed_stats(people_ext, families_ext, window=50):
    """
    Estadísticas agrupadas en ventanas de `window` años según el año de matrimonio.
    Retorna dict: era_start -> {marriage_age_m, marriage_age_f, parenthood_age} (solo medianas).
    """
    era_data = defaultdict(lambda: {'marriage_ages_m': [], 'marriage_ages_f': [],
                                     'parenthood_ages': []})

    for fid, fam in families_ext.items():
        m_year = fam.get('marriage_year')
        if m_year is None:
            continue
        era = (m_year // window) * window

        for role, sex_expected in [('husband', 'M'), ('wife', 'F')]:
            pid = fam.get(role)
            if not pid:
                continue
            p = people_ext.get(pid, {})
            birth = p.get('birth_year') or p.get('baptism_year')
            if birth and m_year > birth:
                age = m_year - birth
                if 10 <= age <= 100:
                    if sex_expected == 'M':
                        era_data[era]['marriage_ages_m'].append(age)
                    else:
                        era_data[era]['marriage_ages_f'].append(age)

        children_years = sorted([
            y for y in (
                people_ext.get(cid, {}).get('birth_year') or
                people_ext.get(cid, {}).get('baptism_year')
                for cid in fam.get('children', [])
            ) if y
        ])
        if children_years:
            first_child_year = children_years[0]
            for role in ['husband', 'wife']:
                pid = fam.get(role)
                if pid:
                    birth = people_ext.get(pid, {}).get('birth_year') or \
                            people_ext.get(pid, {}).get('baptism_year')
                    if birth:
                        age_first = first_child_year - birth
                        if 14 <= age_first <= 70:
                            era_data[era]['parenthood_ages'].append(age_first)

    result = {}
    for era, data in era_data.items():
        result[era] = {
            'median_marriage_age_m': statistics.median(data['marriage_ages_m'])
                if data['marriage_ages_m'] else None,
            'median_marriage_age_f': statistics.median(data['marriage_ages_f'])
                if data['marriage_ages_f'] else None,
            'median_parenthood_age': statistics.median(data['parenthood_ages'])
                if data['parenthood_ages'] else None,
        }
    return result


# ============================================================
# Cálculo de viabilidad de investigación
# ============================================================

def _estimate_birth_year_from_descendants(leaf_id, people_ext, families_ext, stats, windowed_stats, window=50):
    """
    Estima el año de nacimiento de leaf_id a partir de sus descendientes cuando no hay
    fecha directa. Estrategia:
      1. Busca el hijo más antiguo con año conocido → resta la mediana de edad al primer hijo.
      2. Si no hay hijos con año, busca el nieto más antiguo → resta dos medianas.
    Retorna (estimated_year, precision) donde precision es 'child' o 'grandchild', o (None, None).
    """
    median_parenthood = stats.get('parenthood_age', {}).get('median') or 28

    # Hijos directos del individuo
    child_ids = []
    for fam in families_ext.values():
        if fam.get('husband') == leaf_id or fam.get('wife') == leaf_id:
            child_ids.extend(fam.get('children', []))

    child_years = sorted(
        y for cid in child_ids
        for y in [(people_ext.get(cid, {}).get('birth_year') or
                   people_ext.get(cid, {}).get('baptism_year'))]
        if y
    )

    if child_years:
        oldest_child_year = child_years[0]
        # Ajustar mediana según la era del hijo si hay windowed_stats
        era = (oldest_child_year // window) * window
        era_stats = windowed_stats.get(era, {})
        mp = era_stats.get('median_parenthood_age') or median_parenthood
        return round(oldest_child_year - mp), 'child'

    # Sin hijos con año → buscar nietos
    grandchild_years = []
    for cid in child_ids:
        for fam in families_ext.values():
            if fam.get('husband') == cid or fam.get('wife') == cid:
                for gcid in fam.get('children', []):
                    y = (people_ext.get(gcid, {}).get('birth_year') or
                         people_ext.get(gcid, {}).get('baptism_year'))
                    if y:
                        grandchild_years.append(y)

    if grandchild_years:
        oldest_gc_year = min(grandchild_years)
        era = (oldest_gc_year // window) * window
        era_stats = windowed_stats.get(era, {})
        mp = era_stats.get('median_parenthood_age') or median_parenthood
        # Dos generaciones de distancia
        return round(oldest_gc_year - 2 * mp), 'grandchild'

    return None, None


def compute_feasibility(leaf_id, people_ext, families_ext, stats, windowed_stats, record_dates, window=50):
    """
    Para un individuo extremo, calcula la viabilidad de continuar la investigación.
    Retorna: {'estimated_parent_birth': int|None, 'feasibility': 'possible'|'unlikely'|'unknown',
              'birth_year_estimated': bool}
    """
    p = people_ext.get(leaf_id, {})
    birth_year = p.get('birth_year') or p.get('baptism_year')
    birth_place = p.get('birth_place') or p.get('baptism_place')
    if not birth_place:
        birth_place, _ = _infer_place_from_children(leaf_id, people_ext, families_ext)
    birth_year_estimated = False

    if birth_year is None:
        # Intentar estimar el año de nacimiento a partir de descendientes
        birth_year, _ = _estimate_birth_year_from_descendants(
            leaf_id, people_ext, families_ext, stats, windowed_stats, window
        )
        if birth_year is not None:
            birth_year_estimated = True
        else:
            return {'estimated_parent_birth': None, 'feasibility': 'unknown', 'birth_year_estimated': False}

    # Obtener mediana parenthood_age para la era
    era = (birth_year // window) * window
    era_stats = windowed_stats.get(era, {})
    median_parenthood = (
        era_stats.get('median_parenthood_age')
        or stats.get('parenthood_age', {}).get('median')
        or 28
    )

    estimated_parent_birth = round(birth_year - median_parenthood)

    # Buscar fecha de registros del lugar
    oldest_baptism = None
    if birth_place and birth_place in record_dates:
        oldest_baptism = record_dates[birth_place].get('baptism')

    if oldest_baptism is not None and estimated_parent_birth is not None:
        if estimated_parent_birth > oldest_baptism:
            feasibility = 'possible'
        else:
            feasibility = 'unlikely'
    else:
        feasibility = 'unknown'

    return {'estimated_parent_birth': estimated_parent_birth, 'feasibility': feasibility,
            'birth_year_estimated': birth_year_estimated}


# ============================================================
# Helpers de detección por notas
# ============================================================

# Palabras que indican viudedad en notas (español e inglés, con variantes)
_WIDOW_KEYWORDS = [
    'viuda',         # viuda (femenino)
    'viudo',         # viudo (masculino)
    'viudez',        # viudez
    'viudedad',
    'widower',
    'widowed',
    ' widow ',       # widow como palabra sola (con espacios para evitar parciales)
    'viuv',          # variante ortográfica antigua
]

# Negaciones que invalidan la mención de viudedad en la misma frase
_WIDOW_NEGATIONS = ['no era viud', 'no era widow', 'no era viuv', 'nunca viud']

# Palabras que indican que los niños SON gemelos en notas
_TWIN_KEYWORDS = [
    'gemelo', 'gemela',
    'mellizo', 'melliza',
    'twin',
    'gemelar',
]


def _notes_mention_widowhood(texts: list) -> bool:
    """
    Devuelve True si algún texto de nota menciona viudedad de forma afirmativa.
    Excluye menciones en contexto negativo ('no era viudo').
    """
    combined = ' '.join(texts).lower()
    # Primero comprobar si hay una negación explícita
    if any(neg in combined for neg in _WIDOW_NEGATIONS):
        return False
    return any(kw in combined for kw in _WIDOW_KEYWORDS)


def _notes_mention_twins(texts: list) -> bool:
    """Devuelve True si algún texto de nota confirma que son gemelos."""
    combined = ' '.join(texts).lower()
    return any(kw in combined for kw in _TWIN_KEYWORDS)


def _person_notes(pid, people_ext):
    """Retorna la lista de textos de notas de una persona."""
    return people_ext.get(pid, {}).get('notes', [])


# ============================================================
# Detección de inconsistencias
# ============================================================

def detect_inconsistencies(people_ext, families_ext, stats, G=None):
    """
    Detecta errores y anomalías genealógicas. Retorna lista de dicts:
    {pid, name, gramps_id, category, detail, severity}
    """
    issues = []

    def add(pid, category, detail, severity='warning'):
        p = people_ext.get(pid, {})
        issues.append({
            'pid': pid,
            'name': p.get('name', pid),
            'gramps_id': p.get('id', pid),
            'category': category,
            'detail': detail,
            'severity': severity,
        })

    # Umbrales derivados del árbol
    p95_mother_last = (stats.get('mother_age_last', {}).get('p95') or 50)
    p95_father_last = (stats.get('father_age_last', {}).get('p95') or 70)
    p5_marriage_m = (stats.get('marriage_age_m', {}).get('p5') or 16)
    p95_marriage_m = (stats.get('marriage_age_m', {}).get('p95') or 60)
    p5_marriage_f = (stats.get('marriage_age_f', {}).get('p5') or 14)
    p95_marriage_f = (stats.get('marriage_age_f', {}).get('p95') or 55)

    ABS_MAX_MOTHER = 55
    ABS_MAX_FATHER = 80
    MIN_MARRIAGE = 12

    # --- A: Hijos problemáticos ---
    for fid, fam in families_ext.items():
        children_data = []
        for cid in fam.get('children', []):
            cp = people_ext.get(cid, {})
            cy = cp.get('birth_year') or cp.get('baptism_year')
            children_data.append((cy, cid))
        children_data.sort(key=lambda x: (x[0] is None, x[0]))

        # A1: Hermanos muy seguidos
        for i in range(1, len(children_data)):
            prev_year, prev_id = children_data[i - 1]
            curr_year, curr_id = children_data[i]
            if prev_year and curr_year:
                if curr_year == prev_year:
                    p_name = people_ext.get(prev_id, {}).get('name', prev_id)
                    # Si las notas de cualquiera de los dos confirman que son gemelos, omitir
                    twin_notes = (_person_notes(curr_id, people_ext) +
                                  _person_notes(prev_id, people_ext))
                    if _notes_mention_twins(twin_notes):
                        pass  # confirmados como gemelos en notas — no es anomalía
                    else:
                        add(curr_id, 'A1_twins',
                            f"Mismo año que {p_name} ({prev_year})", 'warning')
                elif curr_year - prev_year < 1:
                    p_name = people_ext.get(prev_id, {}).get('name', prev_id)
                    add(curr_id, 'A1_too_close',
                        f"Nacido en {curr_year}, hermano/a anterior {p_name} en {prev_year} (<9 meses)",
                        'error')

        mother_id = fam.get('wife')
        father_id = fam.get('husband')

        for cy, cid in children_data:
            if cy is None:
                continue

            # A2: Madre demasiado mayor
            if mother_id:
                m_birth = people_ext.get(mother_id, {}).get('birth_year') or \
                          people_ext.get(mother_id, {}).get('baptism_year')
                if m_birth:
                    m_age = cy - m_birth
                    if m_age > ABS_MAX_MOTHER:
                        add(cid, 'A2_mother_old',
                            f"Madre ({people_ext.get(mother_id, {}).get('name', mother_id)}) "
                            f"con {m_age} años al nacer el hijo/a (máx. absoluto: {ABS_MAX_MOTHER})",
                            'error')
                    elif m_age > p95_mother_last:
                        add(cid, 'A2_mother_old',
                            f"Madre ({people_ext.get(mother_id, {}).get('name', mother_id)}) "
                            f"con {m_age} años al nacer el hijo/a (P95 árbol: {p95_mother_last:.0f})",
                            'warning')

            # A2b: Padre demasiado mayor
            if father_id:
                f_birth = people_ext.get(father_id, {}).get('birth_year') or \
                          people_ext.get(father_id, {}).get('baptism_year')
                if f_birth:
                    f_age = cy - f_birth
                    if f_age > ABS_MAX_FATHER:
                        add(cid, 'A2_mother_old',
                            f"Padre ({people_ext.get(father_id, {}).get('name', father_id)}) "
                            f"con {f_age} años al nacer el hijo/a (máx. absoluto: {ABS_MAX_FATHER})",
                            'error')

            # A3: Hijo nacido antes que el padre/madre
            for par_id, par_label in [(mother_id, 'madre'), (father_id, 'padre')]:
                if par_id:
                    par_birth = people_ext.get(par_id, {}).get('birth_year') or \
                                people_ext.get(par_id, {}).get('baptism_year')
                    if par_birth and cy < par_birth:
                        add(cid, 'A3_child_before_parent',
                            f"Nacido en {cy}, antes que su {par_label} "
                            f"({people_ext.get(par_id, {}).get('name', par_id)}, nacido en {par_birth})",
                            'error')

    # --- B: Anomalías matrimoniales ---
    for fid, fam in families_ext.items():
        m_year = fam.get('marriage_year')
        if not m_year:
            continue

        # Notas combinadas del evento de matrimonio + ambos cónyuges, para detectar viudedad
        marriage_notes_all = (
            fam.get('marriage_notes', []) +
            _person_notes(fam.get('husband'), people_ext) +
            _person_notes(fam.get('wife'), people_ext)
        )
        is_widowed_marriage = _notes_mention_widowhood(marriage_notes_all)

        ages_by_role = {}
        for role, sex, p5_thr, p95_thr in [
            ('husband', 'M', p5_marriage_m, p95_marriage_m),
            ('wife', 'F', p5_marriage_f, p95_marriage_f),
        ]:
            pid = fam.get(role)
            if not pid:
                continue
            birth = people_ext.get(pid, {}).get('birth_year') or \
                    people_ext.get(pid, {}).get('baptism_year')
            if not birth:
                continue
            age = m_year - birth
            ages_by_role[role] = (pid, age)
            threshold_min = min(MIN_MARRIAGE, p5_thr)

            # B1: demasiado joven
            if age < threshold_min:
                add(pid, 'B1_marriage_young',
                    f"Matrimonio en {m_year} con {age} años (mínimo: {threshold_min})",
                    'error')
            elif age < p5_thr:
                add(pid, 'B1_marriage_young',
                    f"Matrimonio en {m_year} con {age} años (P5 árbol: {p5_thr:.0f})",
                    'warning')

            # B2: demasiado mayor — se omite si las notas indican viudedad
            if age > p95_thr:
                if is_widowed_marriage:
                    pass  # normal en segundas nupcias — omitido por mención a viudedad en notas
                else:
                    add(pid, 'B2_marriage_old',
                        f"Matrimonio en {m_year} con {age} años (P95 árbol: {p95_thr:.0f})",
                        'warning')

        # B3: diferencia extrema entre cónyuges
        if 'husband' in ages_by_role and 'wife' in ages_by_role:
            h_pid, h_age = ages_by_role['husband']
            w_pid, w_age = ages_by_role['wife']
            diff = abs(h_age - w_age)
            if diff > 30:
                add(h_pid, 'B3_age_gap',
                    f"Diferencia de edad con cónyuge: {diff} años "
                    f"(él: {h_age}, ella: {w_age})",
                    'warning')

    # --- C: Inconsistencias cronológicas ---
    for pid, p in people_ext.items():
        birth = p.get('birth_year')
        death = p.get('death_year')

        # C1: muerte antes que nacimiento
        if birth and death and death < birth:
            add(pid, 'C1_death_before_birth',
                f"Nacimiento: {birth}, fallecimiento: {death}",
                'error')

        # C2: fechas futuras
        for year_key, label in [
            ('birth_year', 'Nacimiento'),
            ('baptism_year', 'Bautismo'),
            ('death_year', 'Fallecimiento'),
        ]:
            year_val = p.get(year_key)
            if year_val and year_val > TODAY_YEAR:
                add(pid, 'C2_future_date',
                    f"{label}: {year_val} (posterior a {TODAY_YEAR})",
                    'error')

    for fid, fam in families_ext.items():
        m_year = fam.get('marriage_year')

        # C3: matrimonio antes del nacimiento de algún cónyuge
        if m_year:
            for role, label in [('husband', 'esposo'), ('wife', 'esposa')]:
                pid = fam.get(role)
                if pid:
                    birth = people_ext.get(pid, {}).get('birth_year') or \
                            people_ext.get(pid, {}).get('baptism_year')
                    if birth and m_year < birth:
                        add(pid, 'C3_marriage_before_birth',
                            f"Matrimonio en {m_year}, pero nacido/a en {birth}",
                            'error')

        # C4: hijo nacido tras muerte del padre
        father_id = fam.get('husband')
        if father_id:
            father_death = people_ext.get(father_id, {}).get('death_year')
            if father_death:
                for cid in fam.get('children', []):
                    cy = people_ext.get(cid, {}).get('birth_year') or \
                         people_ext.get(cid, {}).get('baptism_year')
                    if cy:
                        diff = cy - father_death
                        if diff > 1:
                            add(cid, 'C4_posthumous_child',
                                f"Nacido en {cy}, padre fallecido en {father_death} ({diff} años después)",
                                'warning')
                        elif diff == 1:
                            add(cid, 'C4_posthumous_child',
                                f"Nacido en {cy}, posiblemente póstumo (padre fallecido en {father_death})",
                                'info')

        # C5: hijo nacido antes del matrimonio
        if m_year:
            for cid in fam.get('children', []):
                cy = people_ext.get(cid, {}).get('birth_year') or \
                     people_ext.get(cid, {}).get('baptism_year')
                if cy and m_year - cy > 1:
                    add(cid, 'C5_premarital_birth',
                        f"Nacido en {cy}, matrimonio de padres en {m_year} ({m_year - cy} años antes)",
                        'info')

    # --- D: Imposibilidades biológicas ---
    # D1: eventos duplicados
    for pid, p in people_ext.items():
        event_types = [e['type'] for e in p.get('events', [])]
        for ev_type in ('birth', 'nacimiento', 'death', 'defunción', 'defuncion', 'burial'):
            count = event_types.count(ev_type)
            if count > 1:
                add(pid, 'D1_duplicate_event',
                    f"{count} eventos de tipo '{ev_type}'",
                    'error')

    # D2: circularidad en el árbol
    if G is not None:
        try:
            for cycle in nx.simple_cycles(G):
                if cycle:
                    for pid in cycle:
                        if pid in people_ext:
                            add(pid, 'D2_circular_ancestry',
                                f"Forma parte de un ciclo en el árbol genealógico "
                                f"({len(cycle)} personas involucradas)",
                                'error')
        except Exception:
            pass

    return issues


# ============================================================
# Extracción automática de fechas desde fuentes GRAMPS
# ============================================================

# Palabras clave por tipo de sacramento para reconocer títulos de libros
_SOURCE_KEYWORDS = {
    'baptism':      ['lb', 'bautis', 'bapti', 'christening', 'bautizo', 'baptizados',
                     'bautizados', 'bautizadas'],
    'marriage':     ['lm', 'matrimon', 'marriage', 'casados', 'casamiento', 'desposados'],
    'death':        ['ld', 'defunci', 'death', 'burial', 'entierro', 'óbito', 'obito',
                     'difuntos', 'finados'],
    'confirmation': ['lc', 'confirmaci', 'confirmados', 'confirmation'],
}


def _classify_source_title(title: str) -> str | None:
    """Devuelve el tipo de sacramento ('baptism', 'marriage', ...) o None si no reconoce."""
    tl = title.lower().strip()
    # Prioridad: prefijos exactos tipo "LB ", "LM " al inicio
    for stype, kws in _SOURCE_KEYWORDS.items():
        for kw in kws:
            if tl.startswith(kw) or f' {kw}' in tl or f'\t{kw}' in tl:
                return stype
    return None


def _extract_year_range_from_title(title: str) -> tuple[int | None, int | None]:
    """Extrae (año_inicio, año_fin) del título de un libro. Devuelve (None, None) si no encuentra."""
    # Busca patrones como "1583-1921", "1583 - 1921", "1583/1921"
    m = re.search(r'(\d{4})\s*[-/]\s*(\d{4})', title)
    if m:
        return int(m.group(1)), int(m.group(2))
    # Solo año de inicio
    m = re.search(r'(\d{4})', title)
    if m:
        return int(m.group(1)), None
    return None, None


def extract_dates_from_sources(content_bytes: bytes) -> tuple[dict, dict]:
    """
    Parsea las fuentes GRAMPS y devuelve:
      - dates: {place_name: {baptism, marriage, death, confirmation}} con el año más antiguo
      - parishes: {place_name: [{parish, title, type, start, end}, ...]} para mostrar info
    El formato esperado en el autor de la fuente es "Lugar - Parroquia" (o solo "Lugar").
    """
    if content_bytes.startswith(b'\xef\xbb\xbf'):
        content_bytes = content_bytes[3:]
    content_bytes = content_bytes.lstrip()
    try:
        root = ET.fromstring(content_bytes)
    except Exception:
        return {}, {}

    dates: dict = {}
    parishes: dict = {}

    for src in root.iter():
        if strip_ns(src.tag).lower() != 'source':
            continue
        title = author = ''
        for ch in src:
            ctag = strip_ns(ch.tag).lower()
            if ctag == 'stitle':
                title = (ch.text or '').strip()
            elif ctag == 'sauthor':
                author = (ch.text or '').strip()

        if not title or not author:
            continue

        stype = _classify_source_title(title)
        if stype is None:
            continue

        start, end = _extract_year_range_from_title(title)
        if start is None:
            continue

        # Separar lugar y parroquia del campo autor
        parts = author.split(' - ', 1)
        place = parts[0].strip()
        parish = parts[1].strip() if len(parts) > 1 else ''

        if not place:
            continue

        # Actualizar fechas mínimas por lugar
        if place not in dates:
            dates[place] = {'baptism': None, 'marriage': None, 'death': None, 'confirmation': None}
        current = dates[place][stype]
        if current is None or start < current:
            dates[place][stype] = start

        # Acumular info de parroquias
        if place not in parishes:
            parishes[place] = []
        parishes[place].append({
            'parish': parish,
            'title': title,
            'type': stype,
            'start': start,
            'end': end,
        })

    return dates, parishes


# ============================================================
# Persistencia de fechas de registros
# ============================================================

def _load_record_dates() -> dict:
    try:
        if RECORD_DATES_FILE.exists():
            with open(RECORD_DATES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_record_dates(record_dates: dict):
    try:
        DATA_DIR.mkdir(exist_ok=True)
        with open(RECORD_DATES_FILE, 'w', encoding='utf-8') as f:
            json.dump(record_dates, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.error(f"Error guardando fechas: {e}")


def _dismissed_key(issue: dict) -> str:
    return f"{issue['pid']}|{issue['category']}|{issue['detail']}"


def _load_dismissed() -> set:
    try:
        if DISMISSED_FILE.exists():
            with open(DISMISSED_FILE, 'r', encoding='utf-8') as f:
                return set(json.load(f))
    except Exception:
        pass
    return set()


def _save_dismissed(dismissed: set):
    try:
        DATA_DIR.mkdir(exist_ok=True)
        with open(DISMISSED_FILE, 'w', encoding='utf-8') as f:
            json.dump(sorted(dismissed), f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.error(f"Error guardando descartadas: {e}")


# ============================================================
# Cache wrappers
# ============================================================

@st.cache_data(show_spinner=False, ttl=3600)
def _cached_parse_extended(content_bytes: bytes):
    return parse_gramps_extended(content_bytes)


@st.cache_data(show_spinner=False, ttl=3600)
def _cached_extract_dates_from_sources(content_bytes: bytes):
    return extract_dates_from_sources(content_bytes)


@st.cache_data(show_spinner=False, ttl=3600)
def _cached_stats(content_bytes: bytes):
    people_ext, families_ext = _cached_parse_extended(content_bytes)
    stats = compute_file_statistics(people_ext, families_ext)
    windowed = compute_windowed_stats(people_ext, families_ext)
    return stats, windowed


# ============================================================
# Sub-página 1: Extremos del árbol
# ============================================================

def _infer_place_from_children(lid, people_ext, families_ext):
    """
    Si el individuo no tiene lugar de nacimiento registrado, infiere el lugar más probable
    a partir del lugar de nacimiento/bautismo más frecuente entre sus hijos.
    Retorna (lugar_inferido: str | None, es_inferido: bool).
    """
    # Recopilar lugares de nacimiento/bautismo de todos los hijos
    child_places = []
    for fam in families_ext.values():
        if fam.get('husband') == lid or fam.get('wife') == lid:
            for cid in fam.get('children', []):
                cp = people_ext.get(cid, {})
                place = cp.get('birth_place') or cp.get('baptism_place')
                if place:
                    child_places.append(place)
    if not child_places:
        return None, False
    # Lugar más frecuente entre los hijos
    from collections import Counter
    most_common = Counter(child_places).most_common(1)[0][0]
    return most_common, True


def page_extremos(content_bytes: bytes):
    st.title(t("gen_ext_title"))
    st.caption(t("gen_ext_caption"))

    with st.spinner(t("gen_inc_computing")):
        people_ext, families_ext = _cached_parse_extended(content_bytes)
        stats, windowed_stats = _cached_stats(content_bytes)

    leaf_ids = find_leaf_individuals(people_ext, families_ext)

    leaves_with_year = [lid for lid in leaf_ids
                        if people_ext[lid].get('birth_year') or people_ext[lid].get('baptism_year')]
    leaves_with_place_direct = [lid for lid in leaf_ids
                                if people_ext[lid].get('birth_place') or people_ext[lid].get('baptism_place')]
    leaves_with_place_inferred = [lid for lid in leaf_ids
                                  if not (people_ext[lid].get('birth_place') or people_ext[lid].get('baptism_place'))
                                  and _infer_place_from_children(lid, people_ext, families_ext)[0]]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(t("gen_ext_metric_total"), len(leaf_ids))
    col2.metric(t("gen_ext_metric_with_year"), len(leaves_with_year))
    col3.metric(t("gen_ext_metric_with_place"), len(leaves_with_place_direct))
    col4.metric(t("gen_ext_metric_with_inferred_place"), len(leaves_with_place_inferred),
                help=t("gen_ext_metric_inferred_help"))

    if not leaf_ids:
        st.info(t("gen_ext_no_leaves"))
        return

    # Estadísticas de referencia
    with st.expander(t("gen_ext_stats_expander"), expanded=False):
        st.caption(t("gen_ext_stats_caption"))
        st.info(t("gen_ext_stats_legend"))
        stat_rows = []
        for stat_key, label_key in [
            ('marriage_age_m', 'gen_stat_median_marriage_age_m'),
            ('marriage_age_f', 'gen_stat_median_marriage_age_f'),
            ('parenthood_age', 'gen_stat_median_parenthood_age'),
            ('mother_age_last', 'gen_stat_p95_mother_last'),
            ('father_age_last', 'gen_stat_p95_father_last'),
            ('inter_birth', 'gen_stat_median_inter_birth'),
        ]:
            s = stats.get(stat_key, {})
            n = s.get('n', 0)
            fmt = lambda v: '—' if v is None else str(round(v, 1))
            stat_rows.append({
                'Estadística': t(label_key),
                'Mediana': fmt(s.get('median')),
                'P5': fmt(s.get('p5')),
                'P95': fmt(s.get('p95')),
                'N': n,
            })
        st.dataframe(pd.DataFrame(stat_rows), width='stretch', hide_index=True)
        st.caption(t("gen_ext_stats_usage_note"))

    # Cargar fechas guardadas
    if 'gen_record_dates' not in st.session_state:
        st.session_state['gen_record_dates'] = _load_record_dates()
    record_dates = st.session_state['gen_record_dates']

    # Construir DataFrame de hojas
    rows = []
    for lid in leaf_ids:
        p = people_ext[lid]
        birth_year = p.get('birth_year') or p.get('baptism_year')
        direct_place = p.get('birth_place') or p.get('baptism_place') or ''
        inferred_place, is_inferred = _infer_place_from_children(lid, people_ext, families_ext)
        birth_place = direct_place or (inferred_place or '')
        place_source = ('—' if not birth_place
                        else t("gen_ext_place_direct") if direct_place
                        else t("gen_ext_place_inferred"))
        sex_raw = (p.get('sex') or '').upper()
        sex_label = {'M': 'H', 'F': 'M', 'MALE': 'H', 'FEMALE': 'M'}.get(sex_raw, sex_raw or '?')

        feas = compute_feasibility(lid, people_ext, families_ext, stats, windowed_stats, record_dates)
        feas_label = {
            'possible': t("gen_ext_feasibility_possible"),
            'unlikely': t("gen_ext_feasibility_unlikely"),
            'unknown': t("gen_ext_feasibility_unknown"),
        }[feas['feasibility']]

        rd = record_dates.get(birth_place, {}) if birth_place else {}
        rows.append({
            'name': p.get('name', lid),
            'gramps_id': p.get('id', lid),
            'sex': sex_label,
            'birth_year': birth_year,
            'birth_place': birth_place,
            'place_source': place_source,
            'oldest_baptism': rd.get('baptism'),
            'oldest_confirmation': rd.get('confirmation'),
            'oldest_marriage': rd.get('marriage'),
            'oldest_death': rd.get('death'),
            'estimated_parent_birth': feas['estimated_parent_birth'],
            'feasibility': feas_label,
            '_feas_raw': feas['feasibility'],
            '_lid': lid,
        })

    df_leaves = pd.DataFrame(rows)
    # Asegurar que las columnas de años editables sean float (permite NaN, compatible con Arrow)
    for col in ('oldest_baptism', 'oldest_confirmation', 'oldest_marriage', 'oldest_death',
                'birth_year', 'estimated_parent_birth'):
        df_leaves[col] = pd.to_numeric(df_leaves[col], errors='coerce')

    st.subheader(t("gen_ext_table_title"))
    st.caption(t("gen_ext_dates_format_hint"))
    st.caption(t("gen_ext_inferred_place_note"))

    # Restaurar orden de columnas si el usuario lo modificó antes de un guardado
    display_cols = list(df_leaves.drop(columns=['_feas_raw', '_lid']).columns)
    saved_col_order = st.session_state.pop('gen_leaf_editor_column_order', None)
    if saved_col_order:
        # Reordenar según el orden guardado; columnas no listadas van al final
        ordered = [c for c in saved_col_order if c in display_cols]
        remaining = [c for c in display_cols if c not in ordered]
        display_cols = ordered + remaining

    edited = st.data_editor(
        df_leaves[display_cols],
        column_config={
            'name': st.column_config.TextColumn(t("gen_ext_col_name")),
            'gramps_id': st.column_config.TextColumn(t("gen_ext_col_id")),
            'sex': st.column_config.TextColumn(t("gen_ext_col_sex")),
            'birth_year': st.column_config.NumberColumn(t("gen_ext_col_birth_year")),
            'birth_place': st.column_config.TextColumn(t("gen_ext_col_birth_place")),
            'place_source': st.column_config.TextColumn(
                t("gen_ext_col_place_source"),
                help=t("gen_ext_col_place_source_help"),
            ),
            'oldest_baptism': st.column_config.NumberColumn(
                t("gen_ext_col_oldest_baptism"),
                min_value=1000, max_value=2100, step=1,
                help=t("gen_ext_dates_col_help"),
            ),
            'oldest_confirmation': st.column_config.NumberColumn(
                t("gen_ext_col_oldest_confirmation"),
                min_value=1000, max_value=2100, step=1,
                help=t("gen_ext_dates_col_help"),
            ),
            'oldest_marriage': st.column_config.NumberColumn(
                t("gen_ext_col_oldest_marriage"),
                min_value=1000, max_value=2100, step=1,
                help=t("gen_ext_dates_col_help"),
            ),
            'oldest_death': st.column_config.NumberColumn(
                t("gen_ext_col_oldest_death"),
                min_value=1000, max_value=2100, step=1,
                help=t("gen_ext_dates_col_help"),
            ),
            'estimated_parent_birth': st.column_config.NumberColumn(t("gen_ext_estimated_parent")),
            'feasibility': st.column_config.TextColumn(t("gen_ext_col_feasibility")),
        },
        disabled=['name', 'gramps_id', 'sex', 'birth_year', 'birth_place', 'place_source',
                  'estimated_parent_birth', 'feasibility'],
        width='stretch',
        hide_index=True,
        key='gen_leaf_editor',
    )

    # --- Autocompletar fechas desde fuentes ---
    source_dates, source_parishes = _cached_extract_dates_from_sources(content_bytes)

    col_auto, col_save = st.columns([1, 1])
    with col_auto:
        if st.button(t("gen_ext_autofill_dates"), help=t("gen_ext_autofill_help")):
            filled = 0
            for place, vals in source_dates.items():
                existing = record_dates.get(place, {'baptism': None, 'confirmation': None,
                                                    'marriage': None, 'death': None})
                updated = dict(existing)
                for field in ('baptism', 'confirmation', 'marriage', 'death'):
                    if existing.get(field) is None and vals.get(field) is not None:
                        updated[field] = vals[field]
                        filled += 1
                record_dates[place] = updated
            st.session_state['gen_record_dates'] = record_dates
            st.session_state.pop('gen_leaf_editor', None)
            _save_record_dates(record_dates)
            st.success(t("gen_ext_autofill_done").format(filled))
            st.rerun()

    # --- Expander: parroquias disponibles por lugar ---
    places_in_table = {row['birth_place'] for row in rows if row.get('birth_place')}
    parishes_for_table = {p: v for p, v in source_parishes.items() if p in places_in_table}
    if parishes_for_table:
        with st.expander(t("gen_ext_parishes_expander")):
            st.caption(t("gen_ext_parishes_caption"))
            _STYPE_LABELS = {
                'baptism': t("gen_ext_col_oldest_baptism"),
                'marriage': t("gen_ext_col_oldest_marriage"),
                'death': t("gen_ext_col_oldest_death"),
                'confirmation': t("gen_ext_col_oldest_confirmation"),
            }
            par_rows = []
            for place in sorted(parishes_for_table):
                for entry in sorted(parishes_for_table[place],
                                    key=lambda e: (e['parish'], e['type'], e['start'] or 0)):
                    rng = str(entry['start']) if entry['start'] else '?'
                    if entry['end']:
                        rng += f"–{entry['end']}"
                    par_rows.append({
                        t("gen_ext_par_col_place"): place,
                        t("gen_ext_par_col_parish"): entry['parish'] or '—',
                        t("gen_ext_par_col_type"): _STYPE_LABELS.get(entry['type'], entry['type']),
                        t("gen_ext_par_col_range"): rng,
                        t("gen_ext_par_col_title"): entry['title'],
                    })
            st.dataframe(pd.DataFrame(par_rows), hide_index=True, width='stretch')

    with col_save:
        save_clicked = st.button(t("gen_ext_save_dates"), type="primary")

    if save_clicked:
        # Guardar orden de columnas actual antes de invalidar el editor
        editor_state = st.session_state.get('gen_leaf_editor', {})
        saved_column_order = None
        if isinstance(editor_state, dict):
            saved_column_order = editor_state.get('column_order')

        # Paso 1: por cada lugar, tomar el primer valor no-nulo que aparezca en cualquier fila
        # (el usuario solo rellena una fila, pero el valor aplica a todas las del mismo lugar)
        place_values: dict = {}
        for _, row in edited.iterrows():
            place = row.get('birth_place', '')
            if not place:
                continue
            if place not in place_values:
                place_values[place] = {'baptism': None, 'confirmation': None, 'marriage': None, 'death': None}
            for field in ('baptism', 'confirmation', 'marriage', 'death'):
                col = f'oldest_{field}'
                val = row.get(col)
                if pd.notna(val) and val is not None:
                    # Conservar el valor más antiguo (menor) si hay varios no-nulos
                    current = place_values[place][field]
                    new_val = int(val)
                    if current is None or new_val < current:
                        place_values[place][field] = new_val

        # Paso 2: combinar con record_dates existentes (no borrar lo ya guardado)
        for place, vals in place_values.items():
            existing = record_dates.get(place, {})
            record_dates[place] = {
                'baptism': vals['baptism'] if vals['baptism'] is not None else existing.get('baptism'),
                'confirmation': vals['confirmation'] if vals['confirmation'] is not None else existing.get('confirmation'),
                'marriage': vals['marriage'] if vals['marriage'] is not None else existing.get('marriage'),
                'death': vals['death'] if vals['death'] is not None else existing.get('death'),
            }

        st.session_state['gen_record_dates'] = record_dates
        # Invalidar la clave del editor para que se reconstruya con datos frescos, pero
        # restaurar el orden de columnas para que el usuario no pierda su ordenación.
        st.session_state.pop('gen_leaf_editor', None)
        if saved_column_order is not None:
            st.session_state['gen_leaf_editor_column_order'] = saved_column_order
        _save_record_dates(record_dates)
        st.success(t("gen_ext_saved_dates"))
        st.rerun()

    # Selector de persona de referencia para resaltar antepasados directos
    st.markdown("---")
    G = build_graph(people_ext, families_ext)
    all_people_sorted = sorted(
        people_ext.items(),
        key=lambda kv: (kv[1].get('name') or kv[0])
    )
    person_options = {pid: f"{p.get('name', pid)} ({p.get('id', pid)})"
                      for pid, p in all_people_sorted}
    ref_key = 'gen_ext_reference_person'
    saved_ref = st.session_state.get(ref_key)
    default_idx = (list(person_options.keys()).index(saved_ref)
                   if saved_ref and saved_ref in person_options else 0)
    selected_ref = st.selectbox(
        t("gen_ext_reference_person_label"),
        options=list(person_options.keys()),
        format_func=lambda pid: person_options[pid],
        index=default_idx,
        help=t("gen_ext_reference_person_help"),
        key=ref_key,
    )
    direct_ancestors: set = nx.ancestors(G, selected_ref) if selected_ref else set()
    # Añadir hermanos de cada antepasado directo (hijos de los mismos padres)
    ancestors_and_siblings: set = set(direct_ancestors)
    for anc in direct_ancestors:
        for parent in G.predecessors(anc):
            for sibling in G.successors(parent):
                if sibling != selected_ref:
                    ancestors_and_siblings.add(sibling)

    # Recalcular viabilidad con fechas actualizadas
    possible_rows = []
    dead_end_rows = []
    for i, row in df_leaves.iterrows():
        lid = row['_lid']
        feas = compute_feasibility(lid, people_ext, families_ext, stats, windowed_stats, record_dates)
        is_direct = lid in ancestors_and_siblings
        if feas['feasibility'] == 'possible':
            possible_rows.append({**row.to_dict(), '_is_direct': is_direct})
        elif feas['feasibility'] == 'unlikely':
            dead_end_rows.append({**row.to_dict(), '_is_direct': is_direct})

    SUMMARY_COLS = ['name', 'gramps_id', 'birth_year', 'birth_place',
                    'estimated_parent_birth', 'oldest_baptism']
    SUMMARY_INT_COLS = ['birth_year', 'estimated_parent_birth', 'oldest_baptism']

    def _to_summary_df(rows: list) -> pd.DataFrame:
        df = pd.DataFrame(rows)[SUMMARY_COLS + ['_is_direct']]
        for col in SUMMARY_INT_COLS:
            df[col] = pd.to_numeric(df[col], errors='coerce').astype('Int64')
        return df

    def style_direct(df: pd.DataFrame) -> pd.DataFrame:
        """Devuelve un DataFrame de estilos CSS: fondo rojo para antepasados directos."""
        styles = pd.DataFrame('', index=df.index, columns=df.columns)
        if '_is_direct' in df.columns:
            mask = df['_is_direct'].astype(bool)
            styles[mask] = 'background-color: #f28b82; color: #1a0000;'
        return styles

    st.caption(t("gen_ext_parish_records_note"))
    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader(t("gen_ext_possible_title"))
        if possible_rows:
            df_possible = _to_summary_df(possible_rows)
            st.caption(t("gen_ext_direct_ancestor_legend"))
            st.dataframe(
                df_possible.style.apply(style_direct, axis=None)
                                 .hide(subset=['_is_direct'], axis='columns'),
                width="stretch", hide_index=True,
            )
        else:
            st.info("—")
    with col_right:
        st.subheader(t("gen_ext_deadend_title"))
        if dead_end_rows:
            df_dead = _to_summary_df(dead_end_rows)
            st.caption(t("gen_ext_direct_ancestor_legend"))
            st.dataframe(
                df_dead.style.apply(style_direct, axis=None)
                             .hide(subset=['_is_direct'], axis='columns'),
                width="stretch", hide_index=True,
            )
        else:
            st.info("—")


# ============================================================
# Sub-página 2: Inconsistencias
# ============================================================

CATEGORY_LABELS = {
    'A1_too_close': 'gen_cat_A1_too_close',
    'A1_twins': 'gen_cat_A1_twins',
    'A2_mother_old': 'gen_cat_A2_mother_old',
    'A3_child_before_parent': 'gen_cat_A3_child_before_parent',
    'B1_marriage_young': 'gen_cat_B1_marriage_young',
    'B2_marriage_old': 'gen_cat_B2_marriage_old',
    'B3_age_gap': 'gen_cat_B3_age_gap',
    'C1_death_before_birth': 'gen_cat_C1_death_before_birth',
    'C2_future_date': 'gen_cat_C2_future_date',
    'C3_marriage_before_birth': 'gen_cat_C3_marriage_before_birth',
    'C4_posthumous_child': 'gen_cat_C4_posthumous_child',
    'C5_premarital_birth': 'gen_cat_C5_premarital_birth',
    'D1_duplicate_event': 'gen_cat_D1_duplicate_event',
    'D2_circular_ancestry': 'gen_cat_D2_circular_ancestry',
}

GROUP_LABELS = {
    'A': 'A — Hijos problemáticos',
    'B': 'B — Anomalías matrimoniales',
    'C': 'C — Inconsistencias cronológicas',
    'D': 'D — Imposibilidades biológicas',
}


def _render_issue_rows(rows_df: pd.DataFrame, dismissed: set, section_key: str):
    """Render a list of issues with individual dismiss/restore buttons."""
    for idx, row in rows_df.iterrows():
        key = _dismissed_key(row)
        is_dismissed = key in dismissed
        cols = st.columns([3, 1, 2, 4, 1, 1])
        cols[0].write(row['name'])
        cols[1].write(row['gramps_id'])
        cols[2].write(row['category_label'])
        cols[3].write(row['detail'])
        cols[4].write(row['severity'])
        btn_label = t("gen_inc_restore") if is_dismissed else t("gen_inc_dismiss")
        btn_key = f"dismiss_{section_key}_{idx}"
        if cols[5].button(btn_label, key=btn_key):
            if is_dismissed:
                dismissed.discard(key)
            else:
                dismissed.add(key)
            _save_dismissed(dismissed)
            st.rerun()


def page_inconsistencias(content_bytes: bytes):
    st.title(t("gen_inc_title"))
    st.caption(t("gen_inc_caption"))

    with st.spinner(t("gen_inc_computing")):
        people_ext, families_ext = _cached_parse_extended(content_bytes)
        stats, _ = _cached_stats(content_bytes)
        G = build_graph(people_ext, families_ext)
        issues = detect_inconsistencies(people_ext, families_ext, stats, G)

    # Estadísticas de referencia usadas como umbrales
    with st.expander(t("gen_inc_stats_expander"), expanded=True):
        st.info(t("gen_ext_stats_legend"))
        st.caption(t("gen_inc_stats_usage_note"))
        col_m, col_f = st.columns(2)
        with col_m:
            st.markdown(f"**{t('gen_inc_stats_men')}**")
            ma_m = stats.get('marriage_age_m', {})
            st.markdown(f"- {t('gen_inc_stat_marriage_age')}: mediana **{ma_m.get('median', '—')}**, "
                        f"P5 {ma_m.get('p5', '—')}, P95 {ma_m.get('p95', '—')} "
                        f"(n={ma_m.get('n', 0)})")
            pa = stats.get('parenthood_age', {})
            st.markdown(f"- {t('gen_inc_stat_first_child')}: mediana **{pa.get('median', '—')}** (n={pa.get('n', 0)})")
            fal = stats.get('father_age_last', {})
            st.markdown(f"- {t('gen_inc_stat_last_child')}: P95 **{fal.get('p95', '—')}** (n={fal.get('n', 0)})")
        with col_f:
            st.markdown(f"**{t('gen_inc_stats_women')}**")
            ma_f = stats.get('marriage_age_f', {})
            st.markdown(f"- {t('gen_inc_stat_marriage_age')}: mediana **{ma_f.get('median', '—')}**, "
                        f"P5 {ma_f.get('p5', '—')}, P95 {ma_f.get('p95', '—')} "
                        f"(n={ma_f.get('n', 0)})")
            mal = stats.get('mother_age_last', {})
            st.markdown(f"- {t('gen_inc_stat_last_child')}: P95 **{mal.get('p95', '—')}** (n={mal.get('n', 0)})")
            ib = stats.get('inter_birth', {})
            st.markdown(f"- {t('gen_inc_stat_inter_birth')}: mediana **{ib.get('median', '—')} {t('gen_inc_stat_months')}** "
                        f"(n={ib.get('n', 0)})")

    if not issues:
        st.success(t("gen_inc_no_issues"))
        return

    dismissed = _load_dismissed()

    df_issues = pd.DataFrame(issues)
    df_issues['category_label'] = df_issues['category'].map(
        lambda c: t(CATEGORY_LABELS.get(c, c))
    )
    df_issues['_key'] = df_issues.apply(_dismissed_key, axis=1)
    df_issues['_dismissed'] = df_issues['_key'].isin(dismissed)

    df_active = df_issues[~df_issues['_dismissed']]
    df_dismissed = df_issues[df_issues['_dismissed']]

    # Métricas (solo sobre activas)
    n_errors = (df_active['severity'] == 'error').sum()
    n_warnings = (df_active['severity'] == 'warning').sum()
    n_info = (df_active['severity'] == 'info').sum()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric(t("gen_inc_metric_errors"), n_errors)
    m2.metric(t("gen_inc_metric_warnings"), n_warnings)
    m3.metric(t("gen_inc_metric_info"), n_info)
    m4.metric(t("gen_inc_metric_dismissed"), len(df_dismissed))

    # Filtros
    all_severities = ['error', 'warning', 'info']
    all_categories = sorted(df_active['category_label'].unique()) if not df_active.empty else []
    cf1, cf2 = st.columns(2)
    severity_filter = cf1.multiselect(
        t("gen_inc_filter_severity"), all_severities, default=['error', 'warning']
    )
    category_filter = cf2.multiselect(
        t("gen_inc_filter_category"), all_categories, default=[]
    )

    filtered = df_active[df_active['severity'].isin(severity_filter)]
    if category_filter:
        filtered = filtered[filtered['category_label'].isin(category_filter)]

    if filtered.empty:
        st.info(t("gen_inc_no_issues"))
    else:
        header_cols = st.columns([3, 1, 2, 4, 1, 1])
        header_cols[0].markdown(f"**{t('gen_inc_col_name')}**")
        header_cols[1].markdown(f"**{t('gen_inc_col_id')}**")
        header_cols[2].markdown(f"**{t('gen_inc_col_category')}**")
        header_cols[3].markdown(f"**{t('gen_inc_col_detail')}**")
        header_cols[4].markdown(f"**{t('gen_inc_col_severity')}**")
        header_cols[5].markdown(f"**{t('gen_inc_col_action')}**")
        _render_issue_rows(filtered, dismissed, "main")

    # Secciones por grupo
    st.markdown("---")
    for group_prefix, group_label in GROUP_LABELS.items():
        group_df = df_active[df_active['category'].str.startswith(group_prefix)]
        if group_df.empty:
            continue
        with st.expander(f"{group_label} ({len(group_df)})", expanded=False):
            header_cols = st.columns([3, 1, 2, 4, 1, 1])
            header_cols[0].markdown(f"**{t('gen_inc_col_name')}**")
            header_cols[1].markdown(f"**{t('gen_inc_col_id')}**")
            header_cols[2].markdown(f"**{t('gen_inc_col_category')}**")
            header_cols[3].markdown(f"**{t('gen_inc_col_detail')}**")
            header_cols[4].markdown(f"**{t('gen_inc_col_severity')}**")
            header_cols[5].markdown(f"**{t('gen_inc_col_action')}**")
            _render_issue_rows(group_df, dismissed, f"group_{group_prefix}")

    # Descartadas
    if not df_dismissed.empty:
        st.markdown("---")
        with st.expander(f"{t('gen_inc_dismissed_section')} ({len(df_dismissed)})", expanded=False):
            st.caption(t("gen_inc_dismissed_caption"))
            header_cols = st.columns([3, 1, 2, 4, 1, 1])
            header_cols[0].markdown(f"**{t('gen_inc_col_name')}**")
            header_cols[1].markdown(f"**{t('gen_inc_col_id')}**")
            header_cols[2].markdown(f"**{t('gen_inc_col_category')}**")
            header_cols[3].markdown(f"**{t('gen_inc_col_detail')}**")
            header_cols[4].markdown(f"**{t('gen_inc_col_severity')}**")
            header_cols[5].markdown(f"**{t('gen_inc_col_action')}**")
            _render_issue_rows(df_dismissed, dismissed, "dismissed")
            if st.button(t("gen_inc_restore_all"), key="restore_all"):
                keys_to_restore = set(df_dismissed['_key'].tolist())
                dismissed -= keys_to_restore
                _save_dismissed(dismissed)
                st.rerun()

    # Export CSV
    csv_buf = StringIO()
    df_active[['name', 'gramps_id', 'category_label', 'detail', 'severity']].to_csv(
        csv_buf, index=False
    )
    st.download_button(
        label=t("gen_inc_export_csv"),
        data=csv_buf.getvalue().encode('utf-8'),
        file_name="inconsistencias.csv",
        mime="text/csv",
    )


# ============================================================
# Contexto histórico
# ============================================================

def _load_historical_data() -> dict:
    """Carga todos los JSON de eventos históricos del directorio HISTORICAL_DIR."""
    HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)
    data = {}
    for p in HISTORICAL_DIR.glob("*.json"):
        try:
            with open(p, encoding="utf-8") as f:
                obj = json.load(f)
            place = obj.get("place", "")
            events = obj.get("events", [])
            if place and isinstance(events, list):
                data[place] = events
        except Exception:
            pass
    return data


def _extract_places_from_tree(people_ext: dict, families_ext: dict) -> set:
    """Extrae el conjunto de municipios presentes en el árbol (eventos de personas y familias)."""
    places = set()
    for p in people_ext.values():
        for ev in p.get("events", []):
            if ev.get("place"):
                places.add(ev["place"].strip())
    for fam in families_ext.values():
        if fam.get("marriage_place"):
            places.add(fam["marriage_place"].strip())
    return places


def _get_person_places(pid: str, people_ext: dict, families_ext: dict) -> list:
    """Devuelve la lista de municipios asociados a un individuo (sus eventos + matrimonios como padre/madre)."""
    places = []
    seen = set()
    p = people_ext.get(pid, {})
    for ev in p.get("events", []):
        pl = (ev.get("place") or "").strip()
        if pl and pl not in seen:
            places.append(pl)
            seen.add(pl)
    for fam in families_ext.values():
        if fam.get("husband") == pid or fam.get("wife") == pid:
            mp = (fam.get("marriage_place") or "").strip()
            if mp and mp not in seen:
                places.append(mp)
                seen.add(mp)
    return places


def _get_family_places(fid: str, families_ext: dict, people_ext: dict) -> list:
    """Devuelve la lista de municipios asociados a una familia."""
    fam = families_ext.get(fid, {})
    places = []
    seen = set()

    mp = (fam.get("marriage_place") or "").strip()
    if mp:
        places.append(mp)
        seen.add(mp)

    for role in ["husband", "wife"]:
        pid = fam.get(role)
        if pid:
            for ev in people_ext.get(pid, {}).get("events", []):
                pl = (ev.get("place") or "").strip()
                if pl and pl not in seen:
                    places.append(pl)
                    seen.add(pl)

    for cid in fam.get("children", []):
        for ev in people_ext.get(cid, {}).get("events", []):
            pl = (ev.get("place") or "").strip()
            if pl and pl not in seen:
                places.append(pl)
                seen.add(pl)
    return places


def _build_personal_events_person(pid: str, people_ext: dict, families_ext: dict) -> list:
    """
    Construye la lista de eventos personales de un individuo para la línea de tiempo.
    Cada entrada: {year, month, description, kind='personal', place}
    """
    lang = get_lang()
    p = people_ext.get(pid, {})
    name = p.get("name", pid)
    evs = []

    BAPTISM_TYPES = {'baptism', 'christening', 'christen', 'bautismo', 'bautizo',
                     'christened', 'baptized', 'baptised'}
    DEATH_TYPES = {'death', 'burial', 'cremation', 'entierro', 'defunción',
                   'defuncion', 'óbito', 'obito'}
    BIRTH_TYPES = {'birth', 'nacimiento'}
    MARRIAGE_TYPES = {'marriage', 'matrimonio', 'casamiento', 'married'}

    for ev in p.get("events", []):
        year = ev.get("year")
        if not year:
            continue
        ev_type = (ev.get("type") or "").lower()
        place = (ev.get("place") or "").strip()

        if ev_type in BIRTH_TYPES:
            desc = t("gen_ctx_birth_label")
        elif ev_type in BAPTISM_TYPES:
            desc = t("gen_ctx_baptism_label")
        elif ev_type in DEATH_TYPES:
            desc = t("gen_ctx_death_label")
        elif ev_type in MARRIAGE_TYPES:
            desc = t("gen_ctx_marriage_label")
        else:
            desc = t("gen_ctx_other_event_label", type=ev_type) if ev_type else t("gen_ctx_other_event_label", type="—")

        evs.append({"year": year, "month": None, "description": desc,
                    "kind": "personal", "place": place})

    # Nacimientos de hijos
    for fam in families_ext.values():
        if fam.get("husband") == pid or fam.get("wife") == pid:
            for cid in fam.get("children", []):
                child = people_ext.get(cid, {})
                child_year = child.get("birth_year") or child.get("baptism_year")
                child_name = child.get("name", cid)
                child_place = child.get("birth_place") or child.get("baptism_place") or ""
                if child_year:
                    evs.append({"year": child_year, "month": None,
                                "description": t("gen_ctx_child_birth_label", name=child_name),
                                "kind": "personal", "place": child_place})

    return evs


def _build_personal_events_family(fid: str, families_ext: dict, people_ext: dict) -> list:
    """Construye la lista de eventos personales de una familia para la línea de tiempo."""
    fam = families_ext.get(fid, {})
    evs = []

    BAPTISM_TYPES = {'baptism', 'christening', 'christen', 'bautismo', 'bautizo',
                     'christened', 'baptized', 'baptised'}
    DEATH_TYPES = {'death', 'burial', 'cremation', 'entierro', 'defunción',
                   'defuncion', 'óbito', 'obito'}
    BIRTH_TYPES = {'birth', 'nacimiento'}
    MARRIAGE_TYPES = {'marriage', 'matrimonio', 'casamiento', 'married'}

    for role in ["husband", "wife"]:
        pid = fam.get(role)
        if not pid:
            continue
        p = people_ext.get(pid, {})
        pname = p.get("name", pid)
        for ev in p.get("events", []):
            year = ev.get("year")
            if not year:
                continue
            ev_type = (ev.get("type") or "").lower()
            place = (ev.get("place") or "").strip()
            if ev_type in BIRTH_TYPES:
                desc = f"{pname}: {t('gen_ctx_birth_label')}"
            elif ev_type in BAPTISM_TYPES:
                desc = f"{pname}: {t('gen_ctx_baptism_label')}"
            elif ev_type in DEATH_TYPES:
                desc = f"{pname}: {t('gen_ctx_death_label')}"
            elif ev_type in MARRIAGE_TYPES:
                desc = f"{pname}: {t('gen_ctx_marriage_label')}"
            else:
                desc = f"{pname}: {t('gen_ctx_other_event_label', type=ev_type or '—')}"
            evs.append({"year": year, "month": None, "description": desc,
                        "kind": "personal", "place": place})

    # Nacimientos de hijos
    for cid in fam.get("children", []):
        child = people_ext.get(cid, {})
        child_year = child.get("birth_year") or child.get("baptism_year")
        child_name = child.get("name", cid)
        child_place = child.get("birth_place") or child.get("baptism_place") or ""
        if child_year:
            evs.append({"year": child_year, "month": None,
                        "description": t("gen_ctx_child_birth_label", name=child_name),
                        "kind": "personal", "place": child_place})

    return evs


def _build_timeline(personal_events: list, linked_places: list, historical_data: dict,
                    ref_birth_year: int | None) -> list:
    """
    Combina eventos personales e históricos en una lista cronológica.
    Los eventos históricos se filtran para mostrar solo desde 10 años antes del
    nacimiento/primer evento personal hasta el último evento personal.
    Para cada evento histórico añade la edad aproximada si hay año de nacimiento.
    """
    rows = []

    personal_years = [ev["year"] for ev in personal_events if ev.get("year")]
    first_personal_year = min(personal_years) if personal_years else None
    last_personal_year = max(personal_years) if personal_years else None

    # Año de inicio del filtro: 10 años antes del nacimiento (o primer evento personal)
    anchor_year = ref_birth_year or first_personal_year
    year_from = (anchor_year - 10) if anchor_year else None

    for ev in personal_events:
        rows.append({
            "year": ev["year"],
            "month": ev.get("month"),
            "kind": "personal",
            "place": ev.get("place", ""),
            "description": ev["description"],
            "age": None,
        })

    for place in linked_places:
        hist_events = historical_data.get(place, [])
        for hev in hist_events:
            year = hev.get("year")
            if not year:
                continue
            if year_from is not None and year < year_from:
                continue
            if last_personal_year is not None and year > last_personal_year:
                continue
            age = None
            if ref_birth_year and year >= ref_birth_year:
                age = year - ref_birth_year
            rows.append({
                "year": year,
                "month": hev.get("month"),
                "kind": "historical",
                "place": place,
                "description": hev.get("description", ""),
                "age": age,
            })

    rows.sort(key=lambda r: (r["year"], 0 if r["month"] is None else r["month"],
                              0 if r["kind"] == "personal" else 1))
    return rows


def _render_timeline_rows(rows: list):
    """Renderiza la línea de tiempo intercalada en Streamlit."""
    if not rows:
        st.info(t("gen_ctx_no_result"))
        return

    for row in rows:
        year = row["year"]
        month = row["month"]
        kind = row["kind"]
        place = row["place"]
        desc = row["description"]
        age = row["age"]

        year_str = f"{month:02d}/{year}" if month else str(year)

        if kind == "personal":
            icon = "🔵"
            place_str = f" — *{place}*" if place else ""
            st.markdown(f"**{year_str}** {icon} {desc}{place_str}")
        else:
            icon = "📜"
            age_str = ""
            if age is not None:
                age_str = f" *{t('gen_ctx_age_note', age=age)}*"
            place_str = f" — *{place}*" if place else ""
            st.markdown(f"**{year_str}** {icon} {desc}{place_str}{age_str}")


def page_contexto_historico(content_bytes: bytes):
    st.title(t("gen_ctx_title"))
    st.caption(t("gen_ctx_caption"))

    with st.spinner(t("gen_inc_computing")):
        people_ext, families_ext = _cached_parse_extended(content_bytes)

    historical_data = _load_historical_data()
    tree_places = _extract_places_from_tree(people_ext, families_ext)

    tab_upload, tab_timeline = st.tabs([t("gen_ctx_tab_upload"), t("gen_ctx_tab_timeline")])

    # ── Pestaña 1: Gestión de lugares ──────────────────────────────────────
    with tab_upload:
        st.subheader(t("gen_ctx_places_header"))
        st.caption(t("gen_ctx_places_caption"))

        with st.expander(t("gen_ctx_json_format_title"), expanded=False):
            st.markdown(t("gen_ctx_json_format_body"))

        uploaded_json = st.file_uploader(
            t("gen_ctx_upload_label"),
            type=["json"],
            key="gen_ctx_json_uploader",
            help=t("gen_ctx_upload_help"),
        )

        if uploaded_json:
            try:
                raw = json.loads(uploaded_json.read().decode("utf-8"))
                place_name = (raw.get("place") or "").strip()
                events_list = raw.get("events")
                if not place_name or not isinstance(events_list, list):
                    st.error(t("gen_ctx_upload_error_format"))
                elif len(events_list) == 0:
                    st.error(t("gen_ctx_upload_error_events"))
                else:
                    HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)
                    safe_name = re.sub(r'[^\w\-.]', '_', place_name)
                    dest = HISTORICAL_DIR / f"{safe_name}.json"
                    # Fusionar con eventos existentes, evitando duplicados
                    existing_events = historical_data.get(place_name, [])
                    existing_keys = {
                        (e.get("year"), (e.get("description") or "").strip())
                        for e in existing_events
                    }
                    new_events = [
                        e for e in events_list
                        if (e.get("year"), (e.get("description") or "").strip()) not in existing_keys
                    ]
                    merged = existing_events + new_events
                    merged.sort(key=lambda e: (e.get("year") or 0, e.get("month") or 0))
                    with open(dest, "w", encoding="utf-8") as f:
                        json.dump({"place": place_name, "events": merged},
                                  f, ensure_ascii=False, indent=2)
                    historical_data = _load_historical_data()
                    st.success(t("gen_ctx_upload_success", place=place_name, n=len(merged)))
            except Exception as e:
                st.error(t("gen_ctx_upload_error", e=e))

        st.markdown("---")

        if not tree_places:
            st.info(t("gen_ctx_no_places"))
        else:
            rows_table = []
            for place in sorted(tree_places):
                n_events = len(historical_data.get(place, []))
                rows_table.append({"place": place, "n_events": n_events})

            col_place, col_events, col_action = st.columns([3, 1, 1])
            col_place.markdown(f"**{t('gen_ctx_place_col_name')}**")
            col_events.markdown(f"**{t('gen_ctx_place_col_events')}**")
            col_action.markdown(f"**{t('gen_ctx_place_col_action')}**")

            for row in rows_table:
                c1, c2, c3 = st.columns([3, 1, 1])
                c1.write(row["place"])
                c2.write(str(row["n_events"]) if row["n_events"] > 0 else "—")
                if row["n_events"] > 0:
                    if c3.button(t("gen_ctx_delete_btn"),
                                 key=f"del_hist_{row['place']}"):
                        safe_name = re.sub(r'[^\w\-.]', '_', row["place"])
                        dest = HISTORICAL_DIR / f"{safe_name}.json"
                        if dest.exists():
                            dest.unlink()
                        historical_data = _load_historical_data()
                        st.success(t("gen_ctx_delete_success", place=row["place"]))
                        st.rerun()

    # ── Pestaña 2: Línea de tiempo ─────────────────────────────────────────
    with tab_timeline:
        if not historical_data:
            st.info(t("gen_ctx_no_historical"))
            return

        search_type = st.radio(
            t("gen_ctx_select_type"),
            [t("gen_ctx_type_person"), t("gen_ctx_type_family")],
            horizontal=True,
            key="gen_ctx_search_type",
        )

        if search_type == t("gen_ctx_type_person"):
            person_options = {
                pid: f"{p['name']} ({p.get('birth_year') or '?'})"
                for pid, p in sorted(people_ext.items(), key=lambda x: x[1].get("name", ""))
            }
            selected_pid = st.selectbox(
                t("gen_ctx_select_person"),
                options=list(person_options.keys()),
                format_func=lambda pid: person_options[pid],
                key="gen_ctx_person_sel",
            )

            if selected_pid:
                p = people_ext[selected_pid]
                name = p.get("name", selected_pid)
                ref_birth_year = p.get("birth_year") or p.get("baptism_year")

                if not ref_birth_year:
                    st.warning(t("gen_ctx_no_birth_year"))

                linked_places = [pl for pl in _get_person_places(selected_pid, people_ext, families_ext)
                                 if pl in historical_data]

                st.caption(t("gen_ctx_places_lived",
                              places=", ".join(linked_places) if linked_places else "—"))

                if not linked_places:
                    st.info(t("gen_ctx_no_linked_places"))
                    return

                personal_events = _build_personal_events_person(selected_pid, people_ext, families_ext)
                rows = _build_timeline(personal_events, linked_places, historical_data, ref_birth_year)

                st.subheader(t("gen_ctx_timeline_header", name=name))
                st.caption(f"{t('gen_ctx_legend_personal')}   {t('gen_ctx_legend_historical')}")
                _render_timeline_rows(rows)

                if rows:
                    df_exp = pd.DataFrame([
                        {
                            t("gen_ctx_col_year"): r["year"],
                            t("gen_ctx_col_type"): t("gen_ctx_event_personal") if r["kind"] == "personal" else t("gen_ctx_event_historical"),
                            t("gen_ctx_col_place"): r["place"],
                            t("gen_ctx_col_description"): r["description"],
                        }
                        for r in rows
                    ])
                    st.download_button(
                        t("gen_ctx_export_csv"),
                        data=df_exp.to_csv(index=False).encode("utf-8"),
                        file_name=f"contexto_{name.replace(' ', '_')}.csv",
                        mime="text/csv",
                    )

        else:  # Familia
            def _family_label(fid):
                fam = families_ext.get(fid, {})
                h = people_ext.get(fam.get("husband") or "", {}).get("name", "")
                w = people_ext.get(fam.get("wife") or "", {}).get("name", "")
                my = fam.get("marriage_year") or "?"
                if h and w:
                    return f"{t('gen_ctx_family_label', husband=h, wife=w)} ({my})"
                return f"{t('gen_ctx_family_label_one', name=h or w or fid)} ({my})"

            fam_options = {
                fid: _family_label(fid)
                for fid in sorted(families_ext.keys(),
                                  key=lambda fid: _family_label(fid))
            }
            selected_fid = st.selectbox(
                t("gen_ctx_select_family"),
                options=list(fam_options.keys()),
                format_func=lambda fid: fam_options[fid],
                key="gen_ctx_family_sel",
            )

            if selected_fid:
                fam = families_ext[selected_fid]
                label = _family_label(selected_fid)

                husband_pid = fam.get("husband")
                wife_pid = fam.get("wife")
                ref_birth_year = None
                if husband_pid:
                    hp = people_ext.get(husband_pid, {})
                    ref_birth_year = hp.get("birth_year") or hp.get("baptism_year")

                linked_places = [pl for pl in _get_family_places(selected_fid, families_ext, people_ext)
                                 if pl in historical_data]

                st.caption(t("gen_ctx_places_lived",
                              places=", ".join(linked_places) if linked_places else "—"))

                if not linked_places:
                    st.info(t("gen_ctx_no_linked_places"))
                    return

                personal_events = _build_personal_events_family(selected_fid, families_ext, people_ext)
                rows = _build_timeline(personal_events, linked_places, historical_data, ref_birth_year)

                st.subheader(t("gen_ctx_timeline_header", name=label))
                st.caption(f"{t('gen_ctx_legend_personal')}   {t('gen_ctx_legend_historical')}")
                _render_timeline_rows(rows)

                if rows:
                    df_exp = pd.DataFrame([
                        {
                            t("gen_ctx_col_year"): r["year"],
                            t("gen_ctx_col_type"): t("gen_ctx_event_personal") if r["kind"] == "personal" else t("gen_ctx_event_historical"),
                            t("gen_ctx_col_place"): r["place"],
                            t("gen_ctx_col_description"): r["description"],
                        }
                        for r in rows
                    ])
                    st.download_button(
                        t("gen_ctx_export_csv"),
                        data=df_exp.to_csv(index=False).encode("utf-8"),
                        file_name=f"contexto_{label.replace(' ', '_')}.csv",
                        mime="text/csv",
                    )


# ============================================================
# Interfaz pública
# ============================================================

def render_sidebar():
    st.sidebar.markdown(t("gen_sidebar_header"))

    shared_bytes = st.session_state.get('shared_gramps_bytes')
    shared_name = st.session_state.get('shared_gramps_name', '')

    if shared_bytes:
        st.sidebar.success(f"📂 {shared_name}")
        uploaded = None
    else:
        uploaded = st.sidebar.file_uploader(
            t("upload_file"),
            type=['gramps'],
            key='gen_uploader',
        )
        if uploaded:
            content = uploaded.read()
            st.session_state['shared_gramps_bytes'] = content
            st.session_state['shared_gramps_name'] = uploaded.name
            st.session_state['gen_uploaded_bytes'] = content

    st.sidebar.markdown("---")
    st.sidebar.radio(
        t("gen_subpage_selector"),
        [t("gen_subpage_extremos"), t("gen_subpage_inconsistencias"), t("gen_subpage_contexto")],
        key='gen_active_subpage_label',
    )


def render_page():
    content_bytes = (
        st.session_state.get('gen_uploaded_bytes')
        or st.session_state.get('shared_gramps_bytes')
    )
    if not content_bytes:
        st.info(t("gen_ext_no_file"))
        return

    active = st.session_state.get('gen_active_subpage_label', '')
    if active == t("gen_subpage_inconsistencias"):
        page_inconsistencias(content_bytes)
    elif active == t("gen_subpage_contexto"):
        page_contexto_historico(content_bytes)
    else:
        page_extremos(content_bytes)
