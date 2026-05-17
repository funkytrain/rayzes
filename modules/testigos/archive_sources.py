"""
Registro de fuentes de archivo histórico por país.

Cada fuente describe:
- Cómo construir la URL de búsqueda directa (para el usuario)
- Si tiene scraping automático de resultados
- Aviso al usuario si la búsqueda automática no es posible
- Palabras clave de lugar para detección automática del país
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Modelo de datos
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ArchiveSource:
    """Descripción de una fuente de archivo digital."""
    name: str                       # Nombre del portal
    country_code: str               # Código del sistema de apellidos (es, pt, fr…)
    country_name_es: str
    country_name_en: str
    url_template: str               # URL con {name}, {year_min}, {year_max} opcionales
    search_note_es: str             # Nota de uso para el usuario (ES)
    search_note_en: str             # Nota de uso para el usuario (EN)
    can_scrape: bool = False        # True si hay scraping automático implementado
    scrape_fn: Optional[Callable] = field(default=None, repr=False)
    manual_only_reason_es: str = ""  # Por qué no se puede automatizar
    manual_only_reason_en: str = ""
    # Topónimos representativos para detección automática de país
    place_keywords: list[str] = field(default_factory=list)


def _pares_url(name: str, year_min: int | None = None, year_max: int | None = None) -> str:
    params: dict = {"fraseExacta": name}
    if year_min:
        params["anio1"] = str(year_min)
    if year_max:
        params["anio2"] = str(year_max)
    return "https://pares.mcu.es/ParesBusquedas20/catalogo/find?" + urllib.parse.urlencode(params)


def _digitarq_url(name: str, year_min: int | None = None, year_max: int | None = None) -> str:
    return "https://digitarq.arquivos.pt/search?filterDef=0&anyText=" + urllib.parse.quote(name)


def _archives_fr_url(name: str, year_min: int | None = None, year_max: int | None = None) -> str:
    # Archives nationales de France — búsqueda sala virtual
    return "https://www.siv.archives-nationales.culture.gouv.fr/siv/recherche/search?fullText=" + urllib.parse.quote(name)


def _archivportal_de_url(name: str, year_min: int | None = None, year_max: int | None = None) -> str:
    return "https://www.archivportal-d.de/suche?q=" + urllib.parse.quote(name)


def _nationaal_archief_url(name: str, year_min: int | None = None, year_max: int | None = None) -> str:
    return "https://www.nationaalarchief.nl/onderzoeken/zoeken?activeTab=all&searchTerm=" + urllib.parse.quote(name)


def _archivi_it_url(name: str, year_min: int | None = None, year_max: int | None = None) -> str:
    # Archivi di Stato italiani — Sistema Archivistico Nazionale
    return "https://san.beniculturali.it/web/san/ricerca-nel-portale?q=" + urllib.parse.quote(name)


def _bne_url(name: str, year_min: int | None = None, year_max: int | None = None) -> str:
    return "https://bvpb.mcu.es/es/consulta/busqueda.do?texto=" + urllib.parse.quote(name) + "&tipoForm=B"


def _familysearch_url(name: str, year_min: int | None = None, year_max: int | None = None) -> str:
    return "https://www.familysearch.org/search/record/results?q.surname=" + urllib.parse.quote(name.split()[-1] if name.split() else name)


def _qatar_url(name: str, year_min: int | None = None, year_max: int | None = None) -> str:
    return "https://www.qdl.qa/en/search/site/" + urllib.parse.quote(name)


# ─────────────────────────────────────────────────────────────────────────────
# Fuente con scraping: PARES
# ─────────────────────────────────────────────────────────────────────────────

def _pares_scrape(name: str, year_min: int | None = None, year_max: int | None = None) -> list[dict]:
    """Scraping real de PARES. Importa la función del módulo principal."""
    from modules.testigos.archive_search import fetch_pares_results
    return fetch_pares_results(name, year_min=year_min, year_max=year_max)


# ─────────────────────────────────────────────────────────────────────────────
# Registro de fuentes
# ─────────────────────────────────────────────────────────────────────────────

# Topónimos históricos representativos para detección automática de país.
# Listas intencionalmente amplias (incluyen formas históricas y variantes).
_ES_PLACES = [
    "madrid", "barcelona", "sevilla", "valencia", "zaragoza", "toledo",
    "granada", "córdoba", "cordoba", "murcia", "orihuela", "alicante",
    "cartagena", "burgos", "salamanca", "valladolid", "segovia", "ávila",
    "avila", "cuenca", "guadalajara", "albacete", "jaén", "jaen",
    "almería", "almeria", "huelva", "cádiz", "cadiz", "málaga", "malaga",
    "badajoz", "cáceres", "caceres", "mérida", "merida", "oviedo",
    "gijón", "gijon", "bilbao", "san sebastián", "san sebastian",
    "pamplona", "logroño", "logrono", "santander", "vitoria", "león",
    "leon", "palencia", "zamora", "soria", "huesca", "teruel", "lérida",
    "lerida", "tarragona", "gerona", "girona", "castellón", "castellon",
    "elche", "guardamar", "torrevieja", "benidorm", "alcoy", "alcoi",
    "xàtiva", "xativa", "gandia", "sueca", "alzira", "requena",
    "palma", "mahón", "mahon", "ibiza", "eivissa",
    "santa cruz de tenerife", "las palmas", "ceuta", "melilla",
]

_PT_PLACES = [
    "lisboa", "porto", "coimbra", "braga", "évora", "evora", "faro",
    "setúbal", "setubal", "viseu", "leiria", "viana do castelo",
    "bragança", "braganca", "guarda", "castelo branco", "santarém",
    "santarem", "portalegre", "beja", "funchal", "ponta delgada",
    "sintra", "cascais", "almada", "amadora", "guimarães", "guimaraes",
    "matosinhos", "vila nova de gaia", "odivelas", "loures",
    "angra do heroismo",
]

_FR_PLACES = [
    "paris", "lyon", "marseille", "toulouse", "bordeaux", "nantes",
    "strasbourg", "montpellier", "lille", "rennes", "reims", "le havre",
    "saint-étienne", "saint-etienne", "toulon", "grenoble", "dijon",
    "nîmes", "nimes", "angers", "villeurbanne", "perpignan", "metz",
    "versailles", "rouen", "orleans", "nancy", "caen", "avignon",
    "poitiers", "clermont-ferrand", "brest", "amiens", "limoges",
    "aix-en-provence", "tours", "lorient", "nice", "valenciennes",
    "dunkerque", "bayonne", "pau", "chartres", "bourges",
]

_DE_PLACES = [
    "berlin", "münchen", "munich", "hamburg", "cologne", "köln", "koln",
    "frankfurt", "düsseldorf", "dusseldorf", "stuttgart", "dortmund",
    "essen", "leipzig", "bremen", "dresden", "hannover", "nuremberg",
    "nürnberg", "nurnberg", "duisburg", "bochum", "wuppertal", "bielefeld",
    "bonn", "münster", "munster", "karlsruhe", "mannheim", "augsburg",
    "wiesbaden", "gelsenkirchen", "aachen", "kiel", "freiburg",
    "braunschweig", "magdeburg", "erfurt", "mainz", "rostock",
    # Países Bajos (mismo código "de" en el sistema de apellidos)
    "amsterdam", "rotterdam", "den haag", "the hague", "utrecht",
    "eindhoven", "groningen", "tilburg", "almere", "breda", "nijmegen",
    "enschede", "haarlem", "arnhem", "zaandam", "amersfoort",
    "leiden", "dordrecht", "delft", "deventer", "zwolle",
]

_IT_PLACES = [
    "roma", "rome", "milano", "milan", "napoli", "naples", "torino",
    "turin", "palermo", "genova", "genoa", "bologna", "firenze",
    "florence", "venezia", "venice", "bari", "catania", "messina",
    "verona", "padova", "padua", "trieste", "brescia", "taranto",
    "prato", "reggio calabria", "modena", "parma", "livorno",
    "perugia", "cagliari", "bergamo", "trento", "forlì", "forli",
    "ravenna", "ferrara", "salerno", "ancona", "rimini", "siena",
    "sassari", "monza", "lecce", "vicenza", "arezzo", "pesaro",
]

_EN_PLACES = [
    "london", "manchester", "birmingham", "leeds", "glasgow", "liverpool",
    "newcastle", "sheffield", "bristol", "edinburgh", "cardiff", "belfast",
    "nottingham", "oxford", "cambridge", "york", "bath", "exeter",
    "chester", "norwich", "worcester", "leicester", "coventry", "hull",
    "new york", "boston", "philadelphia", "charleston", "virginia",
    "massachusetts", "maryland", "pennsylvania", "new england",
    "dublin", "cork", "galway", "limerick", "waterford",
]

_RU_PLACES = [
    "moscow", "moskva", "saint petersburg", "st petersburg",
    "petersburg", "novgorod", "tver", "pskov", "smolensk", "ryazan",
    "vladimir", "yaroslavl", "kostroma", "suzdal", "kyiv", "kiev",
    "minsk", "vilnius", "riga", "tallinn", "lviv", "lvov",
]


ARCHIVE_SOURCES: dict[str, list[ArchiveSource]] = {

    "es": [
        ArchiveSource(
            name="PARES — Portal de Archivos Españoles",
            country_code="es",
            country_name_es="España",
            country_name_en="Spain",
            url_template="https://pares.mcu.es/ParesBusquedas20/catalogo/find",
            search_note_es="Archivo nacional español. Búsqueda automática activa: extrae títulos y enlaces de documentos.",
            search_note_en="Spanish national archive. Automatic search active: extracts document titles and links.",
            can_scrape=True,
            scrape_fn=_pares_scrape,
            place_keywords=_ES_PLACES,
        ),
        ArchiveSource(
            name="Biblioteca Virtual del Patrimonio Bibliográfico (BNE)",
            country_code="es",
            country_name_es="España",
            country_name_en="Spain",
            url_template="https://bvpb.mcu.es/es/consulta/busqueda.do",
            search_note_es="Biblioteca Nacional de España — manuscritos e impresos digitalizados. Solo enlace directo.",
            search_note_en="National Library of Spain — digitised manuscripts and prints. Direct link only.",
            can_scrape=False,
            manual_only_reason_es="La BNE no ofrece API pública de resultados. Abre el enlace para buscar manualmente.",
            manual_only_reason_en="The BNE does not offer a public results API. Open the link to search manually.",
            place_keywords=_ES_PLACES,
        ),
    ],

    "pt": [
        ArchiveSource(
            name="Digitarq — Arquivos Nacionais de Portugal",
            country_code="pt",
            country_name_es="Portugal",
            country_name_en="Portugal",
            url_template="https://digitarq.arquivos.pt/search",
            search_note_es="Archivo nacional portugués. Solo enlace directo: Digitarq es una aplicación React que carga resultados por JavaScript.",
            search_note_en="Portuguese national archive. Direct link only: Digitarq is a React app that loads results via JavaScript.",
            can_scrape=False,
            manual_only_reason_es="Digitarq usa una SPA (React) que renderiza los resultados en el navegador. No es posible el scraping sin navegador completo.",
            manual_only_reason_en="Digitarq uses a React SPA that renders results in the browser. Scraping requires a full browser engine.",
            place_keywords=_PT_PLACES,
        ),
        ArchiveSource(
            name="FamilySearch — Registros de Portugal",
            country_code="pt",
            country_name_es="Portugal",
            country_name_en="Portugal",
            url_template="https://www.familysearch.org/search/record/results",
            search_note_es="Colecciones digitalizadas de registros parroquiales portugueses. Requiere cuenta gratuita de FamilySearch.",
            search_note_en="Digitalised Portuguese parish record collections. Requires a free FamilySearch account.",
            can_scrape=False,
            manual_only_reason_es="FamilySearch requiere autenticación para acceder a los resultados de búsqueda.",
            manual_only_reason_en="FamilySearch requires authentication to access search results.",
            place_keywords=_PT_PLACES,
        ),
    ],

    "fr": [
        ArchiveSource(
            name="Salle virtuelle des Archives nationales de France",
            country_code="fr",
            country_name_es="Francia",
            country_name_en="France",
            url_template="https://www.siv.archives-nationales.culture.gouv.fr/siv/recherche/search",
            search_note_es="Archivo nacional francés. Solo enlace directo: la aplicación requiere sesión de navegador.",
            search_note_en="French national archive. Direct link only: the application requires a browser session.",
            can_scrape=False,
            manual_only_reason_es="El portal SIV de las Archives nationales requiere cookies de sesión para devolver resultados.",
            manual_only_reason_en="The SIV portal of the Archives nationales requires session cookies to return results.",
            place_keywords=_FR_PLACES,
        ),
        ArchiveSource(
            name="FranceArchives",
            country_code="fr",
            country_name_es="Francia",
            country_name_en="France",
            url_template="https://francearchives.gouv.fr/findingaid/search",
            search_note_es="Portal agregador de archivos departamentales y nacionales franceses. Solo enlace directo.",
            search_note_en="Aggregator of French departmental and national archives. Direct link only.",
            can_scrape=False,
            manual_only_reason_es="FranceArchives no expone una API pública de resultados scranable.",
            manual_only_reason_en="FranceArchives does not expose a publicly scrapable results API.",
            place_keywords=_FR_PLACES,
        ),
    ],

    "de": [
        ArchiveSource(
            name="Archivportal-D",
            country_code="de",
            country_name_es="Alemania / Países Bajos",
            country_name_en="Germany / Netherlands",
            url_template="https://www.archivportal-d.de/suche",
            search_note_es="Portal de archivos alemanes. Solo enlace directo: la aplicación carga resultados via JavaScript.",
            search_note_en="German archives portal. Direct link only: the app loads results via JavaScript.",
            can_scrape=False,
            manual_only_reason_es="Archivportal-D es una SPA que renderiza los resultados dinámicamente en el navegador.",
            manual_only_reason_en="Archivportal-D is a SPA that renders results dynamically in the browser.",
            place_keywords=[p for p in _DE_PLACES if p not in _FR_PLACES],
        ),
        ArchiveSource(
            name="Nationaal Archief (Países Bajos)",
            country_code="de",
            country_name_es="Países Bajos",
            country_name_en="Netherlands",
            url_template="https://www.nationaalarchief.nl/onderzoeken/zoeken",
            search_note_es="Archivo nacional neerlandés. Solo enlace directo: los resultados se cargan dinámicamente.",
            search_note_en="Dutch national archive. Direct link only: results are loaded dynamically.",
            can_scrape=False,
            manual_only_reason_es="El Nationaal Archief carga los resultados de búsqueda con JavaScript.",
            manual_only_reason_en="The Nationaal Archief loads search results with JavaScript.",
            place_keywords=["amsterdam", "rotterdam", "den haag", "utrecht", "leiden",
                            "haarlem", "delft", "groningen", "eindhoven", "breda"],
        ),
    ],

    "it": [
        ArchiveSource(
            name="Sistema Archivistico Nazionale (SAN) — Italia",
            country_code="it",
            country_name_es="Italia",
            country_name_en="Italy",
            url_template="https://san.beniculturali.it/web/san/ricerca-nel-portale",
            search_note_es="Portal del sistema de archivos del Estado italiano. Solo enlace directo.",
            search_note_en="Italian State archives system portal. Direct link only.",
            can_scrape=False,
            manual_only_reason_es="El SAN italiano usa una interfaz dinámica que no permite scraping estático.",
            manual_only_reason_en="The Italian SAN uses a dynamic interface that does not allow static scraping.",
            place_keywords=_IT_PLACES,
        ),
        ArchiveSource(
            name="FamilySearch — Registros de Italia",
            country_code="it",
            country_name_es="Italia",
            country_name_en="Italy",
            url_template="https://www.familysearch.org/search/record/results",
            search_note_es="Registros parroquiales italianos digitalizados. Requiere cuenta gratuita de FamilySearch.",
            search_note_en="Digitalised Italian parish records. Requires a free FamilySearch account.",
            can_scrape=False,
            manual_only_reason_es="FamilySearch requiere autenticación para acceder a los resultados.",
            manual_only_reason_en="FamilySearch requires authentication to access results.",
            place_keywords=_IT_PLACES,
        ),
    ],

    "en": [
        ArchiveSource(
            name="The National Archives (Reino Unido)",
            country_code="en",
            country_name_es="Reino Unido",
            country_name_en="United Kingdom",
            url_template="https://discovery.nationalarchives.gov.uk/results/r",
            search_note_es="Archivo nacional del Reino Unido — Discovery. Solo enlace directo.",
            search_note_en="UK National Archives — Discovery portal. Direct link only.",
            can_scrape=False,
            manual_only_reason_es="Discovery carga sus resultados dinámicamente y requiere JavaScript.",
            manual_only_reason_en="Discovery loads results dynamically and requires JavaScript.",
            place_keywords=_EN_PLACES,
        ),
        ArchiveSource(
            name="FamilySearch — Registros de Reino Unido / EE.UU.",
            country_code="en",
            country_name_es="Reino Unido / EE.UU.",
            country_name_en="UK / USA",
            url_template="https://www.familysearch.org/search/record/results",
            search_note_es="Mayor colección de registros genealógicos del mundo. Requiere cuenta gratuita.",
            search_note_en="World's largest genealogical records collection. Requires a free account.",
            can_scrape=False,
            manual_only_reason_es="FamilySearch requiere autenticación para acceder a los resultados de búsqueda.",
            manual_only_reason_en="FamilySearch requires authentication to access search results.",
            place_keywords=_EN_PLACES,
        ),
    ],

    "ru": [
        ArchiveSource(
            name="FamilySearch — Registros de Rusia / Europa Oriental",
            country_code="ru",
            country_name_es="Rusia / Europa Oriental",
            country_name_en="Russia / Eastern Europe",
            url_template="https://www.familysearch.org/search/record/results",
            search_note_es="Colecciones de registros de Rusia y Europa del Este. Requiere cuenta gratuita.",
            search_note_en="Russia and Eastern Europe record collections. Requires a free account.",
            can_scrape=False,
            manual_only_reason_es="FamilySearch requiere autenticación. Además, muchos registros rusos están en cirílico.",
            manual_only_reason_en="FamilySearch requires authentication. Also, many Russian records are in Cyrillic.",
            place_keywords=_RU_PLACES,
        ),
    ],

    "ar": [
        ArchiveSource(
            name="Qatar Digital Library",
            country_code="ar",
            country_name_es="Mundo árabe / Oriente Medio",
            country_name_en="Arab world / Middle East",
            url_template="https://www.qdl.qa/en/search/site/",
            search_note_es="Biblioteca digital de Qatar con fuentes árabes históricas. Solo enlace directo.",
            search_note_en="Qatar digital library with historical Arabic sources. Direct link only.",
            can_scrape=False,
            manual_only_reason_es="La búsqueda automática en fuentes árabes históricas es muy compleja por el sistema de escritura y la variación de transcripción.",
            manual_only_reason_en="Automatic search in historical Arabic sources is very complex due to the writing system and transcription variation.",
            place_keywords=["bagdad", "damascus", "cairo", "córdoba", "toledo", "sevilla",
                            "granada", "almería", "murcia", "valencia", "zaragoza"],
        ),
    ],

    "zh": [
        ArchiveSource(
            name="—",
            country_code="zh",
            country_name_es="China / Corea / Vietnam",
            country_name_en="China / Korea / Vietnam",
            url_template="",
            search_note_es="No hay portales de archivo histórico del este asiático con búsqueda automática disponible.",
            search_note_en="No East Asian historical archive portals with automatic search are available.",
            can_scrape=False,
            manual_only_reason_es="Los archivos genealógicos del este asiático (zupu, jokbo) no están disponibles en portales occidentales scrapeables. Se recomienda consultar bases de datos especializadas en chino, coreano o vietnamita.",
            manual_only_reason_en="East Asian genealogical archives (zupu, jokbo) are not available in scrapeable Western portals. Consult specialised databases in Chinese, Korean, or Vietnamese.",
            place_keywords=[],
        ),
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Detección de país por topónimo
# ─────────────────────────────────────────────────────────────────────────────

# Índice invertido: topónimo → código de país
_PLACE_TO_COUNTRY: dict[str, str] = {}
for _code, _sources in ARCHIVE_SOURCES.items():
    for _src in _sources:
        for _kw in _src.place_keywords:
            _PLACE_TO_COUNTRY.setdefault(_kw.lower(), _code)


def detect_country_from_places(places: list[str]) -> str | None:
    """
    Intenta deducir el país a partir de los nombres de lugar del testigo.
    Devuelve el código de país con más coincidencias, o None si no hay señal.
    """
    from collections import Counter
    scores: Counter = Counter()
    for place in places:
        place_norm = place.lower().strip()
        # Búsqueda exacta
        if place_norm in _PLACE_TO_COUNTRY:
            scores[_PLACE_TO_COUNTRY[place_norm]] += 2
            continue
        # Búsqueda parcial (el topónimo aparece dentro del lugar)
        for kw, code in _PLACE_TO_COUNTRY.items():
            if kw in place_norm or place_norm in kw:
                scores[code] += 1
    if not scores:
        return None
    return scores.most_common(1)[0][0]


def get_sources_for_country(country_code: str) -> list[ArchiveSource]:
    """Devuelve las fuentes de archivo para un código de país. Fallback a 'es'."""
    return ARCHIVE_SOURCES.get(country_code, ARCHIVE_SOURCES["es"])


def build_direct_url(source: ArchiveSource, name: str,
                     year_min: int | None = None,
                     year_max: int | None = None) -> str:
    """Construye la URL de búsqueda directa para una fuente dada."""
    base = source.url_template
    if not base:
        return ""

    # Casos especiales con parámetros conocidos
    if "pares.mcu.es" in base:
        from modules.testigos.archive_search import build_pares_search_url
        return build_pares_search_url(name, year_min, year_max)
    if "digitarq" in base:
        return base + "?filterDef=0&anyText=" + urllib.parse.quote(name)
    if "siv.archives-nationales" in base:
        return base + "?fullText=" + urllib.parse.quote(name)
    if "archivportal-d" in base:
        return base + "?q=" + urllib.parse.quote(name)
    if "nationaalarchief" in base:
        return base + "?activeTab=all&searchTerm=" + urllib.parse.quote(name)
    if "san.beniculturali" in base:
        return base + "?q=" + urllib.parse.quote(name)
    if "discovery.nationalarchives" in base:
        return base + "?_q=" + urllib.parse.quote(name)
    if "familysearch" in base:
        parts = name.strip().split()
        surname = urllib.parse.quote(parts[-1]) if parts else urllib.parse.quote(name)
        given = urllib.parse.quote(" ".join(parts[:-1])) if len(parts) > 1 else ""
        url = base + "?q.surname=" + surname
        if given:
            url += "&q.givenName=" + given
        return url
    if "bvpb.mcu.es" in base:
        return base.rstrip("?") + "?texto=" + urllib.parse.quote(name) + "&tipoForm=B"
    if "francearchives" in base:
        return base + "?q=" + urllib.parse.quote(name)
    if "qdl.qa" in base:
        return base + urllib.parse.quote(name)

    # Fallback genérico
    sep = "&" if "?" in base else "?"
    return base + sep + "q=" + urllib.parse.quote(name)
