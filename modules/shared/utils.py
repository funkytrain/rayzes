"""
Utilidades compartidas entre los módulos de Rayzes.

Consolida funciones que estaban duplicadas en testigos, consanguinidad y general:
strip_ns, strip_accents, normalize_name, haversine_km, safe_year, year_from_date_str.
"""

import re
import math
import unicodedata

try:
    from dateutil import parser as _dateutil_parser
    _HAS_DATEUTIL = True
except ImportError:
    _HAS_DATEUTIL = False


def strip_ns(tag: str) -> str:
    """Elimina el prefijo de namespace XML '{http://...}' de un nombre de tag."""
    return tag.split('}')[-1] if '}' in tag else tag


def strip_accents(s) -> str:
    """Elimina diacríticos de una cadena usando normalización NFKD."""
    if s is None:
        return ""
    s = unicodedata.normalize('NFKD', str(s))
    return ''.join(ch for ch in s if not unicodedata.combining(ch))


def normalize_name(s) -> str:
    """Normaliza un nombre: elimina acentos, convierte a minúsculas y colapsa espacios."""
    if s is None:
        return ""
    result = strip_accents(str(s)).lower()
    return re.sub(r'\s+', ' ', result).strip()


def haversine_km(lat1, lon1, lat2, lon2):
    """Distancia en km entre dos coordenadas (Haversine). Devuelve None si los valores son inválidos."""
    try:
        R = 6371.0
        lat1, lon1, lat2, lon2 = map(math.radians, [float(lat1), float(lon1), float(lat2), float(lon2)])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        c = 2 * math.asin(min(1, math.sqrt(a)))
        return R * c
    except Exception:
        return None


def safe_year(val) -> 'int | None':
    """Extrae un año de 4 dígitos de un valor cualquiera. Devuelve None si no es posible."""
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


def year_from_date_str(val) -> 'int | None':
    """Extrae el año de una cadena de fecha GRAMPS (p.ej. '1742-03-15', '1742').

    Intenta primero un match rápido por regex, luego dateutil si está disponible.
    Devuelve None si el valor es vacío o no parseable.
    """
    if not val or (isinstance(val, float) and val != val):  # NaN check
        return None
    s = str(val).strip()
    if not s:
        return None
    m = re.match(r'^(\d{4})', s)
    if m:
        return int(m.group(1))
    if _HAS_DATEUTIL:
        try:
            return _dateutil_parser.parse(s).year
        except Exception:
            pass
    return None
