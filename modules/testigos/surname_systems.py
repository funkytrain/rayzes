# surname_systems.py
# Modela los sistemas de apellidos de distintos países para el análisis
# de posibles familiares por coincidencia de apellido en registros históricos.
#
# Cada sistema define:
#   - Cómo extraer los apellidos de un nombre completo (1, 2 o más)
#   - El peso relativo de cada posición (paterno, materno, etc.)
#   - Una lista de apellidos hiperfrecuentes que penalizan la señal
#   - Notas genealógicas relevantes para el investigador

import unicodedata
import re
from dataclasses import dataclass, field
from typing import List, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Utilidades internas
# ─────────────────────────────────────────────────────────────────────────────

def _normalize(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s).strip().lower()

# Partículas nobiliarias/de enlace que no forman parte del apellido base
_PARTICLES_ES = {"de", "del", "de la", "de los", "de las", "la", "el", "los",
                 "las", "y", "e", "i"}
_PARTICLES_PT = {"de", "da", "do", "das", "dos", "e"}
_PARTICLES_NL = {"van", "van de", "van den", "van der", "de", "den", "ter",
                 "ten", "te", "op", "in", "aan", "bij"}
_PARTICLES_DE = {"von", "von der", "von den", "zu", "zum", "zur", "auf", "im"}
_PARTICLES_FR = {"de", "du", "de la", "des", "d'", "le", "la", "les"}
_PARTICLES_IT = {"di", "del", "della", "dello", "degli", "dei", "d'", "da",
                 "dalla", "dalle", "dal"}

_TITLES_COMMON = {"don", "doña", "dona", "fray", "sor", "dr", "dr.", "dra",
                  "dra.", "mr", "mrs", "ms", "sir", "lord", "lady", "fra",
                  "san", "st", "sto", "sta"}


def _strip_titles(tokens: List[str]) -> List[str]:
    while tokens and tokens[0].lower().rstrip(".") in _TITLES_COMMON:
        tokens = tokens[1:]
    return tokens


def _last_real_token_idx(tokens: List[str], particles: set) -> int:
    """Índice del último token que NO sea una partícula simple."""
    for i in range(len(tokens) - 1, -1, -1):
        if tokens[i].lower().rstrip(".") not in particles:
            return i
    return len(tokens) - 1


# ─────────────────────────────────────────────────────────────────────────────
# Dataclass de resultado de extracción
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ExtractedSurnames:
    """Apellidos extraídos de un nombre según el sistema activo."""
    primary: str = ""        # Primer apellido (paterno en español, único en inglés, etc.)
    secondary: str = ""      # Segundo apellido (materno en español, apellido de casada, etc.)
    additional: List[str] = field(default_factory=list)  # Para sistemas con más de dos
    full_surname_string: str = ""  # Concatenación legible de todos

    def all_surnames(self) -> List[str]:
        parts = []
        if self.primary:
            parts.append(self.primary)
        if self.secondary:
            parts.append(self.secondary)
        parts.extend(self.additional)
        return parts


# ─────────────────────────────────────────────────────────────────────────────
# Clase base de sistema de apellidos
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SurnameSystem:
    code: str               # Código del sistema (ej. "es", "en", "pt")
    name_es: str            # Nombre en español
    name_en: str            # Nombre en inglés
    description_es: str     # Descripción breve en español
    description_en: str     # Descripción breve en inglés
    # Pesos de coincidencia por posición: primary, secondary, additional[0], ...
    # Valor 1.0 = peso máximo; menor = señal más débil
    position_weights: List[float] = field(default_factory=lambda: [1.0])
    # Apellidos hiperfrecuentes (normalizados) → reducen el score
    hyperfrequent: List[str] = field(default_factory=list)
    # Notas genealógicas para el investigador
    genealogical_notes_es: str = ""
    genealogical_notes_en: str = ""

    def extract(self, full_name: str) -> ExtractedSurnames:
        raise NotImplementedError

    def frequency_penalty(self, surname_norm: str) -> float:
        """Retorna un factor multiplicativo 0–1; 1 = sin penalización."""
        if surname_norm in self.hyperfrequent:
            return 0.25
        return 1.0

    def match_weight(self, pos_index: int) -> float:
        if pos_index < len(self.position_weights):
            return self.position_weights[pos_index]
        return 0.5


# ─────────────────────────────────────────────────────────────────────────────
# Sistema ESPAÑOL (y variantes históricas)
# ─────────────────────────────────────────────────────────────────────────────

class SpanishSurnameSystem(SurnameSystem):
    """
    Sistema español: dos apellidos, paterno primero y materno segundo.
    En registros históricos los testigos pueden ser abuelos, por lo que
    el apellido materno (2.º) puede coincidir con el apellido paterno (1.º)
    de la siguiente generación. Se pondera adecuadamente.

    Formato típico: [Nombre(s)] [Apellido_paterno] [Apellido_materno]
    Variantes históricas: uso de "y" o "i" como separador de apellidos,
    partículas nobiliarias (de, del, de la...) precediendo al apellido.
    """

    def extract(self, full_name: str) -> ExtractedSurnames:
        if not full_name or str(full_name).strip() in ("", "nan", "None"):
            return ExtractedSurnames()

        tokens = str(full_name).strip().split()
        tokens = _strip_titles(tokens)
        if not tokens:
            return ExtractedSurnames()

        # Separador explícito "y" / "i" entre apellidos (ej. "García y López")
        # Buscamos la última "y"/"i" que separe dos bloques de apellidos
        # (no puede ser la primera ni la última posición)
        connector_idx = None
        for i in range(1, len(tokens) - 1):
            if tokens[i].lower() in ("y", "i"):
                connector_idx = i

        if connector_idx is not None and connector_idx >= 1:
            # Apellido 1: tokens desde connector_idx-1 hacia atrás (con partículas)
            # Apellido 2: tokens desde connector_idx+1 hacia adelante
            # Heurística: primer apellido = último bloque antes del conector
            # que no sea solo primer nombre
            block1_end = connector_idx - 1
            block2_start = connector_idx + 1

            # Construir apellido 1: puede tener partícula precedente
            ap1_tokens = self._collect_surname_block(tokens, block1_end)
            ap2_tokens = tokens[block2_start:]
            ap1 = " ".join(ap1_tokens)
            ap2 = " ".join(ap2_tokens) if ap2_tokens else ""
        else:
            # Sin conector: asumimos que los 2 últimos "bloques" son apellidos
            # (un bloque puede ser "de la Cruz" → 3 tokens)
            ap1, ap2 = self._split_last_two_surnames(tokens)

        result = ExtractedSurnames(
            primary=ap1,
            secondary=ap2,
            full_surname_string=f"{ap1} {ap2}".strip() if ap2 else ap1,
        )
        return result

    def _collect_surname_block(self, tokens: List[str], end_idx: int) -> List[str]:
        """Recoge tokens[end_idx] más las partículas que lo preceden."""
        block = [tokens[end_idx]]
        i = end_idx - 1
        while i >= 1:
            if tokens[i].lower().rstrip(".") in _PARTICLES_ES:
                block.insert(0, tokens[i])
                i -= 1
            else:
                break
        return block

    def _split_last_two_surnames(self, tokens: List[str]):
        """Extrae los dos últimos apellidos de una lista de tokens."""
        if len(tokens) <= 1:
            return tokens[0] if tokens else "", ""
        if len(tokens) == 2:
            return tokens[0], tokens[1]

        # Buscar el último token real (no partícula) → 2.º apellido
        last_real = _last_real_token_idx(tokens, _PARTICLES_ES)
        ap2_tokens = self._collect_surname_block(tokens, last_real)
        remaining = tokens[:last_real - len(ap2_tokens) + 1]

        # Del resto, buscar el penúltimo token real → 1.er apellido
        if not remaining or len(remaining) <= 1:
            # Solo queda un token o ninguno: no podemos separar más
            ap1 = remaining[-1] if remaining else ""
            return ap1, " ".join(ap2_tokens)

        second_real = _last_real_token_idx(remaining, _PARTICLES_ES)
        ap1_tokens = self._collect_surname_block(remaining, second_real)

        return " ".join(ap1_tokens), " ".join(ap2_tokens)


# ─────────────────────────────────────────────────────────────────────────────
# Sistema PORTUGUÉS
# ─────────────────────────────────────────────────────────────────────────────

class PortugueseSurnameSystem(SurnameSystem):
    """
    Sistema portugués: dos apellidos. A diferencia del español, el orden
    tradicional era materno primero, paterno último (opuesto al español).
    En registros históricos el orden puede variar. Desde el s. XX se
    estandarizó como materno-paterno.
    """

    def extract(self, full_name: str) -> ExtractedSurnames:
        if not full_name or str(full_name).strip() in ("", "nan", "None"):
            return ExtractedSurnames()
        tokens = str(full_name).strip().split()
        tokens = _strip_titles(tokens)
        if not tokens:
            return ExtractedSurnames()

        # Mismo mecanismo que el español pero con partículas portuguesas
        if len(tokens) <= 1:
            return ExtractedSurnames(primary=tokens[0])
        if len(tokens) == 2:
            return ExtractedSurnames(primary=tokens[1], secondary=tokens[0],
                                     full_surname_string=f"{tokens[1]} {tokens[0]}")

        last_real = _last_real_token_idx(tokens, _PARTICLES_PT)
        ap_last_tokens = [tokens[last_real]]
        i = last_real - 1
        while i >= 1 and tokens[i].lower() in _PARTICLES_PT:
            ap_last_tokens.insert(0, tokens[i]); i -= 1

        remaining = tokens[:last_real - len(ap_last_tokens) + 1]
        if len(remaining) > 1:
            prev_real = _last_real_token_idx(remaining, _PARTICLES_PT)
            ap_prev_tokens = [remaining[prev_real]]
            j = prev_real - 1
            while j >= 1 and remaining[j].lower() in _PARTICLES_PT:
                ap_prev_tokens.insert(0, remaining[j]); j -= 1
            ap1 = " ".join(ap_prev_tokens)
            ap2 = " ".join(ap_last_tokens)
        else:
            ap1 = remaining[-1] if remaining else ""
            ap2 = " ".join(ap_last_tokens)

        # En sistema portugués histórico: último = paterno (más relevante)
        return ExtractedSurnames(
            primary=" ".join(ap_last_tokens),   # paterno (último, más fuerte)
            secondary=ap1,                       # materno (penúltimo)
            full_surname_string=f"{ap1} {ap2}".strip(),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Sistema ANGLOSAJÓN (inglés, alemán, neerlandés, escandinavo moderno)
# ─────────────────────────────────────────────────────────────────────────────

class SingleSurnameSystem(SurnameSystem):
    """
    Sistemas con un único apellido transmitido patrilinealmente.
    La coincidencia es más limpia pero menos informativa.
    """

    def __init__(self, particles: set = None, **kwargs):
        super().__init__(**kwargs)
        self._particles = particles or set()

    def extract(self, full_name: str) -> ExtractedSurnames:
        if not full_name or str(full_name).strip() in ("", "nan", "None"):
            return ExtractedSurnames()
        tokens = str(full_name).strip().split()
        tokens = _strip_titles(tokens)
        if not tokens:
            return ExtractedSurnames()

        last_real = _last_real_token_idx(tokens, self._particles)
        sn_tokens = [tokens[last_real]]
        i = last_real - 1
        while i >= 1 and tokens[i].lower().rstrip(".") in self._particles:
            sn_tokens.insert(0, tokens[i]); i -= 1

        sn = " ".join(sn_tokens)
        return ExtractedSurnames(primary=sn, full_surname_string=sn)


# ─────────────────────────────────────────────────────────────────────────────
# Sistema ÁRABE (patronímico)
# ─────────────────────────────────────────────────────────────────────────────

class ArabicPatronymicSystem(SurnameSystem):
    """
    Cadena patronímica: Nombre ibn/bint Padre ibn/bint Abuelo...
    No hay apellido fijo transmitido; cada generación usa el nombre del padre.
    La coincidencia relevante es entre el nombre del padre del testigo
    y el nombre del sujeto o de su padre.
    """

    def extract(self, full_name: str) -> ExtractedSurnames:
        if not full_name or str(full_name).strip() in ("", "nan", "None"):
            return ExtractedSurnames()

        # Separar por ibn/bint/bin/bt (con o sin tilde)
        parts = re.split(r"\b(ibn|bint|bin|bt|b\.)\b", str(full_name), flags=re.IGNORECASE)
        segments = [p.strip() for p in parts if p.strip() and
                    p.strip().lower() not in ("ibn", "bint", "bin", "bt", "b.")]

        primary = segments[1] if len(segments) > 1 else ""
        secondary = segments[2] if len(segments) > 2 else ""
        additional = segments[3:] if len(segments) > 3 else []

        return ExtractedSurnames(
            primary=primary,
            secondary=secondary,
            additional=additional,
            full_surname_string=" ibn ".join(segments),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Sistema ESLAVO (ruso, polaco, etc.) — apellidos con concordancia de género
# ─────────────────────────────────────────────────────────────────────────────

class SlavicSurnameSystem(SurnameSystem):
    """
    Sistema eslavo: un apellido transmitido patrilinealmente pero con
    desinencias de género (Ivanov/Ivanova, Kowalski/Kowalska).
    Para comparación se normaliza la raíz eliminando desinencias comunes.
    """

    _ENDINGS = ["ova", "eva", "ina", "ska", "cka", "dka",  # femeninos
                "ov",  "ev",  "in",  "ski", "cki", "dki",  # masculinos
                "ych", "ich"]

    def extract(self, full_name: str) -> ExtractedSurnames:
        if not full_name or str(full_name).strip() in ("", "nan", "None"):
            return ExtractedSurnames()
        tokens = str(full_name).strip().split()
        tokens = _strip_titles(tokens)
        if not tokens:
            return ExtractedSurnames()
        sn = tokens[-1]
        return ExtractedSurnames(primary=sn, full_surname_string=sn)

    def surname_root(self, surname: str) -> str:
        """Extrae la raíz del apellido eliminando desinencias de género."""
        s = _normalize(surname)
        for ending in self._ENDINGS:
            if s.endswith(ending) and len(s) > len(ending) + 2:
                return s[:-len(ending)]
        return s


# ─────────────────────────────────────────────────────────────────────────────
# Sistema CHINO / COREANO / VIETNAMITA — apellido primero, muy hiperfrecuente
# ─────────────────────────────────────────────────────────────────────────────

class EastAsianSurnameSystem(SurnameSystem):
    """
    El apellido va al inicio del nombre completo. El número de apellidos
    distintos es muy reducido (los 100 apellidos chinos cubren el 85% de la
    población), por lo que la señal de coincidencia es muy débil si no va
    acompañada de contexto geográfico/temporal fuerte.
    """

    def extract(self, full_name: str) -> ExtractedSurnames:
        if not full_name or str(full_name).strip() in ("", "nan", "None"):
            return ExtractedSurnames()
        tokens = str(full_name).strip().split()
        if not tokens:
            return ExtractedSurnames()
        sn = tokens[0]
        return ExtractedSurnames(primary=sn, full_surname_string=sn)


# ─────────────────────────────────────────────────────────────────────────────
# Registro de sistemas disponibles
# ─────────────────────────────────────────────────────────────────────────────

SURNAME_SYSTEMS: dict[str, SurnameSystem] = {

    "es": SpanishSurnameSystem(
        code="es",
        name_es="Español (dos apellidos)",
        name_en="Spanish (two surnames)",
        description_es=(
            "Paterno (1.º) + materno (2.º). Los testigos pueden ser abuelos, "
            "tíos o primos: el apellido materno del testigo puede coincidir con "
            "el paterno del sujeto. Ambos apellidos se analizan con pesos distintos."
        ),
        description_en=(
            "Paternal (1st) + maternal (2nd). Witnesses can be grandparents, "
            "uncles or cousins: the witness's maternal surname may match the "
            "subject's paternal surname. Both surnames are analysed with different weights."
        ),
        position_weights=[1.0, 0.75],
        hyperfrequent=[
            "garcia", "gonzalez", "rodriguez", "fernandez", "lopez", "martinez",
            "sanchez", "perez", "gomez", "martin", "jimenez", "ruiz", "hernandez",
            "diaz", "moreno", "muñoz", "alvarez", "romero", "alonso", "gutierrez",
            "navarro", "torres", "dominguez", "vazquez", "ramos", "gil", "ramirez",
            "serrano", "blanco", "molina", "morales", "suarez", "ortega", "delgado",
            "castro", "ortiz", "rubio", "marin", "santos", "nuñez",
        ],
        genealogical_notes_es=(
            "En registros parroquiales españoles (s. XVI-XIX) es frecuente que el testigo "
            "sea familiar del sujeto: padrinos suelen ser tíos, abuelos o vecinos de confianza. "
            "La coincidencia del 2.º apellido del testigo con el 1.º del sujeto indica posible "
            "parentesco materno (la madre del sujeto podría tener ese apellido como paterno). "
            "Tener en cuenta que en muchas épocas y regiones los hijos ilegítimos usaban "
            "solo apellido materno."
        ),
        genealogical_notes_en=(
            "In Spanish parish records (16th-19th c.) the witness is often a relative: "
            "godparents tend to be uncles, grandparents or trusted neighbours. A match between "
            "the witness's 2nd surname and the subject's 1st surname suggests maternal kinship."
        ),
    ),

    "pt": PortugueseSurnameSystem(
        code="pt",
        name_es="Portugués (dos apellidos, materno-paterno)",
        name_en="Portuguese (two surnames, maternal-paternal)",
        description_es=(
            "Orden histórico: materno (penúltimo) + paterno (último). "
            "La transmisión va por línea paterna pero el materno también es relevante."
        ),
        description_en=(
            "Historical order: maternal (second-to-last) + paternal (last). "
            "Transmission is patrilineal but the maternal surname is also relevant."
        ),
        position_weights=[1.0, 0.65],
        hyperfrequent=[
            "silva", "santos", "ferreira", "pereira", "oliveira", "costa",
            "rodrigues", "martins", "jesus", "sousa", "fernandes", "gomes",
            "lopes", "marques", "alves", "almeida", "ribeiro", "pinto",
            "carvalho", "teixeira",
        ],
        genealogical_notes_es=(
            "En Portugal el orden de apellidos fue materno-paterno hasta el s. XX. "
            "La coincidencia del apellido materno (penúltimo) suele indicar parentesco "
            "por línea de la madre."
        ),
        genealogical_notes_en=(
            "In Portugal the surname order was maternal-paternal until the 20th century. "
            "A match on the maternal surname (second-to-last) usually indicates kinship "
            "through the mother's line."
        ),
    ),

    "en": SingleSurnameSystem(
        code="en",
        particles={"of", "the"},
        name_es="Inglés / anglosajón (apellido único)",
        name_en="English / Anglo-Saxon (single surname)",
        description_es=(
            "Un solo apellido transmitido patrilinealmente. "
            "La señal de coincidencia es directa pero ofrece menos información "
            "sobre parentesco materno."
        ),
        description_en=(
            "Single surname transmitted patrilineally. "
            "Match signal is direct but gives less information about maternal kinship."
        ),
        position_weights=[1.0],
        hyperfrequent=[
            "smith", "jones", "williams", "taylor", "brown", "davies", "evans",
            "wilson", "thomas", "roberts", "johnson", "lewis", "walker", "robinson",
            "wood", "thompson", "white", "watson", "jackson", "wright", "green",
            "harris", "cooper", "king", "lee", "martin", "clark", "scott", "hall",
        ],
        genealogical_notes_es=(
            "En registros anglosajones (s. XVII-XIX) los testigos suelen ser "
            "vecinos o amigos de confianza más que familiares. La coincidencia "
            "de apellido sigue siendo relevante pero requiere apoyo temporal/geográfico."
        ),
        genealogical_notes_en=(
            "In Anglo-Saxon records (17th-19th c.) witnesses are often neighbours "
            "or trusted friends rather than relatives. Surname coincidence is still "
            "relevant but requires temporal/geographic support."
        ),
    ),

    "fr": SingleSurnameSystem(
        code="fr",
        particles=_PARTICLES_FR,
        name_es="Francés (apellido único con partículas)",
        name_en="French (single surname with particles)",
        description_es=(
            "Un apellido con posibles partículas nobiliarias (de, du, de la...). "
            "La nobleza conserva la partícula como parte del apellido."
        ),
        description_en=(
            "Single surname with possible noble particles (de, du, de la...). "
            "Nobility retains the particle as part of the surname."
        ),
        position_weights=[1.0],
        hyperfrequent=[
            "martin", "bernard", "thomas", "petit", "robert", "richard",
            "durand", "dubois", "moreau", "laurent", "simon", "michel",
            "lefebvre", "leroy", "roux", "david", "bertrand", "morel", "fournier",
        ],
        genealogical_notes_es=(
            "En registros parroquiales franceses los padrinos suelen ser familiares "
            "o personas de la misma parroquia. Las partículas nobiliarias forman parte "
            "del apellido y deben conservarse en la comparación."
        ),
        genealogical_notes_en=(
            "In French parish records godparents are usually family or parishioners. "
            "Noble particles are part of the surname and must be preserved in comparisons."
        ),
    ),

    "de": SingleSurnameSystem(
        code="de",
        particles=_PARTICLES_DE,
        name_es="Alemán / neerlandés (apellido único, partículas nobiliarias)",
        name_en="German / Dutch (single surname, noble particles)",
        description_es=(
            "Apellido único con partículas (von, van, zu...). "
            "Históricamente los patronímicos fueron frecuentes en el norte de Alemania "
            "y Países Bajos hasta el s. XIX."
        ),
        description_en=(
            "Single surname with particles (von, van, zu...). "
            "Patronymics were common in northern Germany and the Netherlands until the 19th century."
        ),
        position_weights=[1.0],
        hyperfrequent=[
            "mueller", "schmidt", "schneider", "fischer", "weber", "meyer",
            "wagner", "becker", "schulz", "hoffmann", "schaefer", "koch",
            "bauer", "richter", "klein", "wolf", "schroeder", "neumann",
            "schwarz", "zimmermann", "braun", "krueger", "hartmann",
            # Neerlandés
            "de jong", "jansen", "de vries", "van den berg", "van dijk",
            "bakker", "janssen", "visser", "smit", "meijer", "de graaf",
        ],
        genealogical_notes_es=(
            "En Alemania y Países Bajos los registros parroquiales son más tardíos "
            "(Reforma, s. XVI). En zonas rurales los patronímicos coexistieron con "
            "los apellidos fijos hasta bien entrado el s. XIX."
        ),
        genealogical_notes_en=(
            "In Germany and the Netherlands parish records are later (Reformation, 16th c.). "
            "In rural areas patronymics coexisted with fixed surnames well into the 19th century."
        ),
    ),

    "it": SingleSurnameSystem(
        code="it",
        particles=_PARTICLES_IT,
        name_es="Italiano (apellido único con partículas)",
        name_en="Italian (single surname with particles)",
        description_es=(
            "Apellido único transmitido patrilinealmente, con posibles partículas "
            "(di, del, della...). Históricamente los documentos notariales y "
            "parroquiales muestran gran variación ortográfica."
        ),
        description_en=(
            "Single surname transmitted patrilineally, with possible particles "
            "(di, del, della...). Historical notarial and parish records show "
            "great spelling variation."
        ),
        position_weights=[1.0],
        hyperfrequent=[
            "rossi", "russo", "ferrari", "esposito", "bianchi", "romano",
            "colombo", "ricci", "marino", "greco", "bruno", "gallo",
            "conti", "de luca", "mancini", "costa", "giordano", "rizzo",
            "lombardi", "moretti",
        ],
        genealogical_notes_es=(
            "En los registros parroquiales italianos (s. XVI en adelante) los "
            "padrinos suelen ser familiares o compadres. La variación ortográfica "
            "es alta (Ferrari/Ferrarri/Ferrar); usar comparación fuzzy."
        ),
        genealogical_notes_en=(
            "In Italian parish records (from 16th c.) godparents are usually family "
            "or compadres. Spelling variation is high; use fuzzy comparison."
        ),
    ),

    "ar": ArabicPatronymicSystem(
        code="ar",
        name_es="Árabe (cadena patronímica ibn/bint)",
        name_en="Arabic (patronymic chain ibn/bint)",
        description_es=(
            "No hay apellido fijo. El segundo elemento de la cadena (padre del "
            "testigo) es el más relevante para detectar parentesco con el sujeto."
        ),
        description_en=(
            "No fixed surname. The second element of the chain (witness's father) "
            "is most relevant for detecting kinship with the subject."
        ),
        position_weights=[1.0, 0.8, 0.5],
        hyperfrequent=[
            "muhammad", "mohammed", "ahmad", "ali", "hassan", "hussain",
            "ibrahim", "ismail", "omar", "abdallah", "abd", "khalid",
        ],
        genealogical_notes_es=(
            "En documentos históricos de Al-Ándalus o el Magreb la cadena "
            "patronímica puede tener 3-4 eslabones. El nombre del padre del "
            "testigo (2.º elemento) coincidiendo con el nombre del sujeto indica "
            "parentesco directo (el testigo sería hermano o primo del sujeto)."
        ),
        genealogical_notes_en=(
            "In historical documents from Al-Andalus or the Maghreb the patronymic "
            "chain can have 3-4 links. The witness's father's name (2nd element) "
            "matching the subject's name indicates direct kinship."
        ),
    ),

    "ru": SlavicSurnameSystem(
        code="ru",
        name_es="Ruso / eslavo oriental (apellido con desinencias de género)",
        name_en="Russian / East Slavic (gendered surname endings)",
        description_es=(
            "Apellido único patrilineal con desinencias -ov/-ova, -ev/-eva, etc. "
            "Para comparación se normaliza la raíz."
        ),
        description_en=(
            "Single patrilineal surname with endings -ov/-ova, -ev/-eva, etc. "
            "Root is normalised for comparison."
        ),
        position_weights=[1.0],
        hyperfrequent=[
            "ivanov", "ivanova", "smirnov", "smirnova", "kuznetsov",
            "popov", "popova", "sokolov", "sokolova", "lebedev", "lebedeva",
            "kozlov", "kozlova", "novikov", "novikova",
        ],
        genealogical_notes_es=(
            "En registros eclesiásticos rusos (metricheskiye knigi, desde el s. XVIII) "
            "los padrinos son generalmente familiares. La raíz del apellido es el "
            "elemento comparativo clave, ignorando la desinencia de género."
        ),
        genealogical_notes_en=(
            "In Russian ecclesiastical records (metricheskiye knigi, from 18th c.) "
            "godparents are generally relatives. The surname root is the key "
            "comparator, ignoring the gender ending."
        ),
    ),

    "zh": EastAsianSurnameSystem(
        code="zh",
        name_es="Chino / coreano / vietnamita (apellido al inicio)",
        name_en="Chinese / Korean / Vietnamese (surname first)",
        description_es=(
            "Apellido al inicio del nombre. Repertorio muy reducido (~100 apellidos "
            "para el 85% de la población china): la coincidencia sola es señal débil "
            "sin apoyo geográfico/temporal fuerte."
        ),
        description_en=(
            "Surname at the start of the name. Very limited repertoire (~100 surnames "
            "cover 85% of the Chinese population): coincidence alone is a weak signal "
            "without strong geographic/temporal support."
        ),
        position_weights=[0.6],
        hyperfrequent=[
            "wang", "li", "zhang", "liu", "chen", "yang", "huang", "zhao",
            "wu", "zhou", "xu", "sun", "ma", "zhu", "hu", "guo", "lin",
            "he", "gao", "liang", "zheng", "luo", "song", "xie", "tang",
            # Coreano
            "kim", "lee", "park", "choi", "jung", "kang", "cho", "yoon",
            # Vietnamita
            "nguyen", "tran", "le", "pham", "hoang", "huynh", "phan",
        ],
        genealogical_notes_es=(
            "La coincidencia de apellido en sistemas chinos/coreanos es casi ubicua "
            "en zonas de alta concentración de un apellido. Priorizar contexto "
            "geográfico, temporal y coincidencia de nombre de pila para inferir parentesco."
        ),
        genealogical_notes_en=(
            "Surname coincidence in Chinese/Korean systems is near-ubiquitous in "
            "areas with high concentration of a single surname. Prioritise geographic, "
            "temporal, and given-name context to infer kinship."
        ),
    ),
}


def get_system(code: str) -> SurnameSystem:
    """Devuelve el sistema de apellidos por código, con fallback a español."""
    return SURNAME_SYSTEMS.get(code, SURNAME_SYSTEMS["es"])


def list_systems_for_selector() -> list[tuple[str, str, str]]:
    """Lista de (code, nombre_es, nombre_en) para poblar un selector en la UI."""
    return [(code, sys.name_es, sys.name_en)
            for code, sys in SURNAME_SYSTEMS.items()]
