from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RagChunk:
    chunk_id: str
    source_type: str    # "person"|"family"|"event"|"note"|"doc_pdf"|"doc_txt"
    source_label: str
    text: str
    metadata: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# GRAMPS chunking
# ─────────────────────────────────────────────────────────────────────────────

def _sex_label(sex: str) -> str:
    mapping = {"M": "Hombre", "F": "Mujer", "U": "Desconocido"}
    return mapping.get(sex, sex or "Desconocido")


def chunk_gramps_db(db) -> list[RagChunk]:
    chunks: list[RagChunk] = []

    # Precompute: for each person handle → {parents, spouses, children}
    parents_of: dict[str, list[str]] = {}      # handle → list of parent names
    spouses_of: dict[str, list[str]] = {}
    children_of: dict[str, list[str]] = {}

    for fam in db.families.values():
        h_name = db.persons[fam.husband_handle].name if fam.husband_handle and fam.husband_handle in db.persons else None
        w_name = db.persons[fam.wife_handle].name if fam.wife_handle and fam.wife_handle in db.persons else None

        # spouses
        if fam.husband_handle and h_name:
            spouses_of.setdefault(fam.husband_handle, [])
            if w_name:
                spouses_of[fam.husband_handle].append(w_name)
        if fam.wife_handle and w_name:
            spouses_of.setdefault(fam.wife_handle, [])
            if h_name:
                spouses_of[fam.wife_handle].append(h_name)

        # children
        parent_names = [n for n in [h_name, w_name] if n]
        for ch_handle in fam.child_handles:
            if ch_handle in db.persons:
                # parents of this child
                parents_of.setdefault(ch_handle, [])
                parents_of[ch_handle].extend(parent_names)
                # children of parents
                ch_name = db.persons[ch_handle].name
                if fam.husband_handle:
                    children_of.setdefault(fam.husband_handle, [])
                    children_of[fam.husband_handle].append(ch_name)
                if fam.wife_handle:
                    children_of.setdefault(fam.wife_handle, [])
                    children_of[fam.wife_handle].append(ch_name)

    # Person chunks
    for handle, p in db.persons.items():
        lines = [f"[Persona] {p.name} ({p.id}) — {_sex_label(p.sex)}"]

        if p.birth_year or p.birth_place:
            parts = []
            if p.birth_year:
                parts.append(str(p.birth_year))
            if p.birth_place:
                parts.append(p.birth_place)
            lines.append("Nacimiento: " + " en ".join(parts))

        if p.baptism_year or p.baptism_place:
            parts = []
            if p.baptism_year:
                parts.append(str(p.baptism_year))
            if p.baptism_place:
                parts.append(p.baptism_place)
            lines.append("Bautismo: " + " en ".join(parts))

        if p.death_year or p.death_place:
            parts = []
            if p.death_year:
                parts.append(str(p.death_year))
            if p.death_place:
                parts.append(p.death_place)
            lines.append("Fallecimiento: " + " en ".join(parts))

        pnames = parents_of.get(handle, [])
        if pnames:
            lines.append("Padres: " + ", ".join(pnames))

        snames = spouses_of.get(handle, [])
        if snames:
            lines.append("Cónyuge(s): " + ", ".join(snames))

        cnames = children_of.get(handle, [])
        if cnames:
            lines.append("Hijos: " + ", ".join(cnames[:20]))
            if len(cnames) > 20:
                lines.append(f"  ... y {len(cnames) - 20} más")

        for ev in (p.events_summary or []):
            ev_type = ev.get("type", "")
            ev_year = ev.get("year", "")
            ev_place = ev.get("place", "")
            if ev_type not in ("Nacimiento", "Defunción", "Bautismo"):
                parts = [ev_type]
                if ev_year:
                    parts.append(str(ev_year))
                if ev_place:
                    parts.append(ev_place)
                lines.append("Evento: " + " — ".join(parts))

        for note in (p.note_texts or []):
            note = note.strip()
            if note:
                lines.append("Notas: " + note[:400])
                break

        chunks.append(RagChunk(
            chunk_id=f"person_{p.id}",
            source_type="person",
            source_label=f"{p.name} ({p.id})",
            text="\n".join(lines),
            metadata={
                "birth_year": p.birth_year,
                "death_year": p.death_year,
                "birth_place": p.birth_place,
            },
        ))

    # Family chunks
    for handle, fam in db.families.items():
        h_name = db.persons[fam.husband_handle].name if fam.husband_handle and fam.husband_handle in db.persons else "?"
        w_name = db.persons[fam.wife_handle].name if fam.wife_handle and fam.wife_handle in db.persons else "?"
        label = f"{h_name} + {w_name}"

        lines = [f"[Familia] {fam.id} — {label}"]
        if fam.marriage_year or fam.marriage_place:
            parts = []
            if fam.marriage_year:
                parts.append(str(fam.marriage_year))
            if fam.marriage_place:
                parts.append(fam.marriage_place)
            lines.append("Matrimonio: " + " en ".join(parts))

        ch_names = [db.persons[ch].name for ch in fam.child_handles if ch in db.persons]
        if ch_names:
            lines.append(f"Hijos ({len(ch_names)}): " + ", ".join(ch_names[:20]))

        for note in (fam.marriage_notes or []):
            note = note.strip()
            if note:
                lines.append("Notas: " + note[:400])
                break

        chunks.append(RagChunk(
            chunk_id=f"family_{fam.id}",
            source_type="family",
            source_label=label,
            text="\n".join(lines),
            metadata={"marriage_year": fam.marriage_year, "marriage_place": fam.marriage_place},
        ))

    # Event chunks (only those with notes or witnesses)
    for handle, ev in db.events.items():
        if not ev.note_texts and not ev.witnesses:
            continue
        place_name = ""
        if ev.place_handle and ev.place_handle in db.places:
            place_name = db.places[ev.place_handle].name

        lines = [f"[Evento] {ev.id} — {ev.type} de {ev.subject_name}"]
        if ev.date_iso:
            lines.append(f"Fecha: {ev.date_iso}")
        if place_name:
            lines.append(f"Lugar: {place_name}")
        if ev.witnesses:
            w_parts = []
            for w in ev.witnesses:
                w_parts.append(f"{w.name}" + (f" ({w.note})" if w.note else ""))
            lines.append("Testigos: " + ", ".join(w_parts))
        for note in ev.note_texts:
            note = note.strip()
            if note:
                lines.append("Notas: " + note[:600])

        chunks.append(RagChunk(
            chunk_id=f"event_{ev.id}",
            source_type="event",
            source_label=f"{ev.type} — {ev.subject_name}",
            text="\n".join(lines),
            metadata={"event_type": ev.type, "date_iso": ev.date_iso, "place": place_name},
        ))

    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# Document chunking
# ─────────────────────────────────────────────────────────────────────────────

def _split_text(text: str, max_chars: int = 800, overlap: int = 80) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_chars:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                chunks.append(current)
                # overlap: carry last `overlap` chars
                current = current[-overlap:].strip() + "\n\n" + para
            else:
                # single paragraph larger than max_chars — split by sentence
                while len(para) > max_chars:
                    chunks.append(para[:max_chars])
                    para = para[max_chars - overlap:]
                current = para
    if current:
        chunks.append(current)
    return chunks


def chunk_document(filename: str, content_bytes: bytes) -> list[RagChunk]:
    ext = filename.rsplit(".", 1)[-1].lower()
    chunks: list[RagChunk] = []

    if ext == "pdf":
        try:
            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content_bytes))
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                text = text.strip()
                if not text:
                    continue
                # split long pages
                parts = _split_text(text, max_chars=900, overlap=80)
                for j, part in enumerate(parts):
                    suffix = f"_{j}" if len(parts) > 1 else ""
                    chunks.append(RagChunk(
                        chunk_id=f"doc_{filename}_p{i+1}{suffix}",
                        source_type="doc_pdf",
                        source_label=f"{filename} — Página {i+1}",
                        text=f"[Documento] {filename} — Página {i+1}\n{part}",
                        metadata={"filename": filename, "page": i + 1},
                    ))
        except Exception as e:
            chunks.append(RagChunk(
                chunk_id=f"doc_{filename}_error",
                source_type="doc_pdf",
                source_label=filename,
                text=f"[Documento] {filename}\n[Error al procesar PDF: {e}]",
                metadata={"filename": filename},
            ))

    else:  # txt and fallback
        try:
            text = content_bytes.decode("utf-8", errors="replace")
        except Exception:
            text = content_bytes.decode("latin-1", errors="replace")
        parts = _split_text(text, max_chars=800, overlap=80)
        for i, part in enumerate(parts):
            chunks.append(RagChunk(
                chunk_id=f"doc_{filename}_{i}",
                source_type="doc_txt",
                source_label=f"{filename} — fragmento {i+1}",
                text=f"[Documento] {filename}\n{part}",
                metadata={"filename": filename, "chunk_index": i},
            ))

    return chunks


def build_all_chunks(db, doc_uploads: list[tuple[str, bytes]]) -> list[RagChunk]:
    chunks = chunk_gramps_db(db)
    for filename, content in doc_uploads:
        chunks.extend(chunk_document(filename, content))
    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# Tree-wide statistics block (for aggregate queries)
# ─────────────────────────────────────────────────────────────────────────────

def build_tree_stats(db) -> str:
    from collections import Counter

    persons = list(db.persons.values())
    families = list(db.families.values())

    # Birth years
    birth_years = [p.birth_year for p in persons if p.birth_year]
    oldest = min(birth_years) if birth_years else None
    newest = max(birth_years) if birth_years else None

    # Oldest / newest persons
    oldest_persons = sorted(
        [p for p in persons if p.birth_year],
        key=lambda p: p.birth_year
    )[:5]
    newest_persons = sorted(
        [p for p in persons if p.birth_year],
        key=lambda p: p.birth_year,
        reverse=True
    )[:5]

    # Top birth places
    birth_places = Counter(
        p.birth_place for p in persons if p.birth_place
    ).most_common(10)

    # Top surnames (last word of name as heuristic)
    surnames = Counter()
    for p in persons:
        parts = p.name.strip().split()
        if parts:
            surnames[parts[-1]] += 1
    top_surnames = surnames.most_common(10)

    # Sex counts
    males = sum(1 for p in persons if p.sex == "M")
    females = sum(1 for p in persons if p.sex == "F")
    unknown_sex = len(persons) - males - females

    lines = ["=== ESTADÍSTICAS GLOBALES DEL ÁRBOL GENEALÓGICO ==="]
    lines.append(f"Total de personas: {len(persons)}")
    lines.append(f"  Hombres: {males} | Mujeres: {females} | Sin determinar: {unknown_sex}")
    lines.append(f"Total de familias: {len(families)}")

    if oldest is not None:
        lines.append(f"Rango de fechas de nacimiento: {oldest} — {newest}")

    if oldest_persons:
        lines.append("\nPersonas con fecha de nacimiento más antigua:")
        for p in oldest_persons:
            place = f" en {p.birth_place}" if p.birth_place else ""
            lines.append(f"  - {p.name} ({p.id}): {p.birth_year}{place}")

    if newest_persons:
        lines.append("\nPersonas con fecha de nacimiento más reciente:")
        for p in newest_persons:
            place = f" en {p.birth_place}" if p.birth_place else ""
            lines.append(f"  - {p.name} ({p.id}): {p.birth_year}{place}")

    if birth_places:
        lines.append("\nLugares de nacimiento más frecuentes:")
        for place, count in birth_places:
            lines.append(f"  - {place}: {count} personas")

    if top_surnames:
        lines.append("\nApellidos más frecuentes:")
        for surname, count in top_surnames:
            lines.append(f"  - {surname}: {count} personas")

    # ── Lifespan / mortality ──────────────────────────────────────────────────
    lifespans = [
        p.death_year - p.birth_year
        for p in persons
        if p.birth_year and p.death_year and 0 < p.death_year - p.birth_year <= 110
    ]
    if lifespans:
        avg_life = sum(lifespans) / len(lifespans)
        lines.append(f"\nEsperanza de vida media (sobre {len(lifespans)} personas con nacimiento y muerte): "
                     f"{avg_life:.1f} años (min {min(lifespans)}, max {max(lifespans)})")
        # By century of birth
        for century_start, label in [(1500, "siglo XVI"), (1600, "siglo XVII"),
                                     (1700, "siglo XVIII"), (1800, "siglo XIX"), (1900, "siglo XX")]:
            subset = [
                p.death_year - p.birth_year
                for p in persons
                if p.birth_year and p.death_year
                and century_start <= p.birth_year < century_start + 100
                and 0 < p.death_year - p.birth_year <= 110
            ]
            if len(subset) >= 5:
                lines.append(f"  {label}: {sum(subset)/len(subset):.1f} años (n={len(subset)})")

    # ── Infant mortality proxy (died before age 10) ───────────────────────────
    infant_deaths = [
        p for p in persons
        if p.birth_year and p.death_year and 0 <= p.death_year - p.birth_year < 10
    ]
    adults_with_dates = [
        p for p in persons
        if p.birth_year and p.death_year and p.death_year - p.birth_year >= 10
    ]
    total_with_dates = len(infant_deaths) + len(adults_with_dates)
    if total_with_dates >= 10:
        pct = len(infant_deaths) / total_with_dates * 100
        lines.append(f"Mortalidad infantil (fallecidos antes de los 10 años): "
                     f"{len(infant_deaths)} de {total_with_dates} ({pct:.1f}%)")

    # ── Family size ───────────────────────────────────────────────────────────
    child_counts = [len(fam.child_handles) for fam in families if fam.child_handles]
    if child_counts:
        avg_children = sum(child_counts) / len(child_counts)
        lines.append(f"\nTamaño medio de familia: {avg_children:.1f} hijos por familia "
                     f"(sobre {len(child_counts)} familias con hijos registrados)")
        lines.append(f"  Máximo: {max(child_counts)} hijos | Familias con 1 hijo: "
                     f"{sum(1 for c in child_counts if c == 1)}")
        # Distribution buckets
        buckets = [(1, 1), (2, 3), (4, 6), (7, 9), (10, 999)]
        bucket_labels = ["1", "2-3", "4-6", "7-9", "10+"]
        dist_parts = []
        for (lo, hi), lbl in zip(buckets, bucket_labels):
            n = sum(1 for c in child_counts if lo <= c <= hi)
            if n:
                dist_parts.append(f"{lbl} hijos: {n} familias")
        if dist_parts:
            lines.append("  Distribución: " + " | ".join(dist_parts))

    # ── Top death places ──────────────────────────────────────────────────────
    death_places = Counter(
        p.death_place for p in persons if p.death_place
    ).most_common(5)
    if death_places:
        lines.append("\nLugares de fallecimiento más frecuentes:")
        for place, count in death_places:
            lines.append(f"  - {place}: {count} personas")

    # ── Geographic mobility: born and died in different places ────────────────
    mobile = [
        p for p in persons
        if p.birth_place and p.death_place and p.birth_place != p.death_place
    ]
    sedentary = [
        p for p in persons
        if p.birth_place and p.death_place and p.birth_place == p.death_place
    ]
    if mobile or sedentary:
        total_geo = len(mobile) + len(sedentary)
        lines.append(f"\nMovilidad geográfica (personas con lugar de nacimiento y muerte registrados): "
                     f"{total_geo} personas")
        lines.append(f"  Fallecieron en lugar distinto al de nacimiento: {len(mobile)} ({len(mobile)/total_geo*100:.1f}%)")
        lines.append(f"  Fallecieron en el mismo lugar donde nacieron: {len(sedentary)} ({len(sedentary)/total_geo*100:.1f}%)")

    # ── Marriage age statistics ───────────────────────────────────────────────
    persons_by_handle = db.persons  # handle → GrampsPerson

    # Collect (marriage_year, husband_birth, wife_birth, marriage_place) tuples
    marriage_records = []
    for fam in families:
        if not fam.marriage_year:
            continue
        h = persons_by_handle.get(fam.husband_handle) if fam.husband_handle else None
        w = persons_by_handle.get(fam.wife_handle) if fam.wife_handle else None
        h_age = fam.marriage_year - h.birth_year if (h and h.birth_year) else None
        w_age = fam.marriage_year - w.birth_year if (w and w.birth_year) else None
        # Discard implausible ages
        if h_age is not None and not (10 <= h_age <= 80):
            h_age = None
        if w_age is not None and not (10 <= w_age <= 80):
            w_age = None
        if h_age is not None or w_age is not None:
            marriage_records.append({
                "year": fam.marriage_year,
                "place": fam.marriage_place or "",
                "h_age": h_age,
                "w_age": w_age,
            })

    if marriage_records:
        h_ages = [r["h_age"] for r in marriage_records if r["h_age"] is not None]
        w_ages = [r["w_age"] for r in marriage_records if r["w_age"] is not None]
        both = [(r["h_age"], r["w_age"]) for r in marriage_records
                if r["h_age"] is not None and r["w_age"] is not None]
        diffs = [h - w for h, w in both]

        lines.append(f"\nEstadísticas de edad al matrimonio (sobre {len(marriage_records)} familias con año de matrimonio):")
        if h_ages:
            lines.append(f"  Edad media del marido al casarse: {sum(h_ages)/len(h_ages):.1f} años "
                         f"(min {min(h_ages)}, max {max(h_ages)}, n={len(h_ages)})")
        if w_ages:
            lines.append(f"  Edad media de la mujer al casarse: {sum(w_ages)/len(w_ages):.1f} años "
                         f"(min {min(w_ages)}, max {max(w_ages)}, n={len(w_ages)})")
        if diffs:
            lines.append(f"  Diferencia media de edad (marido − mujer): {sum(diffs)/len(diffs):.1f} años "
                         f"(n={len(diffs)})")

        # By century
        for century_start, label in [(1500, "siglo XVI"), (1600, "siglo XVII"),
                                     (1700, "siglo XVIII"), (1800, "siglo XIX"), (1900, "siglo XX")]:
            subset = [r for r in marriage_records if century_start <= r["year"] < century_start + 100]
            if len(subset) < 5:
                continue
            sh = [r["h_age"] for r in subset if r["h_age"] is not None]
            sw = [r["w_age"] for r in subset if r["w_age"] is not None]
            sd = [r["h_age"] - r["w_age"] for r in subset
                  if r["h_age"] is not None and r["w_age"] is not None]
            parts = [f"n={len(subset)}"]
            if sh:
                parts.append(f"marido {sum(sh)/len(sh):.1f} años")
            if sw:
                parts.append(f"mujer {sum(sw)/len(sw):.1f} años")
            if sd:
                parts.append(f"diferencia {sum(sd)/len(sd):.1f} años")
            lines.append(f"  {label}: {', '.join(parts)}")

        # By top places (those with ≥10 marriages with age data)
        place_records: dict[str, list] = {}
        for r in marriage_records:
            place = r["place"].strip()
            if place:
                place_records.setdefault(place, []).append(r)
        place_stats = []
        for place, recs in place_records.items():
            pd_list = [r["h_age"] - r["w_age"] for r in recs
                       if r["h_age"] is not None and r["w_age"] is not None]
            if len(pd_list) >= 10:
                place_stats.append((place, len(pd_list), sum(pd_list) / len(pd_list)))
        place_stats.sort(key=lambda x: -x[1])
        if place_stats:
            lines.append("  Diferencia de edad por lugar (≥10 matrimonios con datos):")
            for place, n, diff in place_stats[:10]:
                lines.append(f"    - {place}: diferencia media {diff:.1f} años (n={n})")

    # ── Interbirth intervals ──────────────────────────────────────────────────
    intervals = []
    for fam in families:
        child_years = sorted([
            db.persons[ch].birth_year
            for ch in fam.child_handles
            if ch in db.persons and db.persons[ch].birth_year
        ])
        for i in range(1, len(child_years)):
            gap = child_years[i] - child_years[i - 1]
            if 0 < gap <= 25:
                intervals.append(gap)
    if intervals:
        lines.append(f"\nIntervalo medio entre nacimientos de hermanos consecutivos: "
                     f"{sum(intervals)/len(intervals):.1f} años (n={len(intervals)})")

    # ── Remarriage / multiple marriages ──────────────────────────────────────
    spouse_count: Counter = Counter()
    for fam in families:
        if fam.husband_handle:
            spouse_count[fam.husband_handle] += 1
        if fam.wife_handle:
            spouse_count[fam.wife_handle] += 1
    remarried = {h: n for h, n in spouse_count.items() if n >= 2}
    if remarried:
        lines.append(f"\nPersonas con más de un matrimonio registrado: {len(remarried)}")
        max_marriages = max(remarried.values())
        if max_marriages >= 3:
            top_remarried = sorted(remarried.items(), key=lambda x: -x[1])[:3]
            for handle, n in top_remarried:
                name = db.persons[handle].name if handle in db.persons else handle
                lines.append(f"  - {name}: {n} matrimonios")

    # ── Persons without birth year (data completeness) ────────────────────────
    no_birth = sum(1 for p in persons if not p.birth_year)
    no_death = sum(1 for p in persons if not p.death_year)
    lines.append(f"\nCompletitud de datos:")
    lines.append(f"  Sin año de nacimiento: {no_birth} personas ({no_birth/len(persons)*100:.1f}%)")
    lines.append(f"  Sin año de fallecimiento: {no_death} personas ({no_death/len(persons)*100:.1f}%)")

    lines.append("=== FIN ESTADÍSTICAS ===")
    return "\n".join(lines)
