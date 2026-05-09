# Rayzes — Genealogical Analysis Platform

> *"Por sus rayzes e antigua descendencia es conocido."*

A web-based genealogical research platform for analyzing witness/godparent networks and consanguinity patterns in historical records. Built with Python and Streamlit, it processes GRAMPS XML database files and provides interactive visualizations, statistical analysis, and exportable reports.

---

## Table of Contents

- [Overview](#overview)
- [Modules](#modules)
  - [General (Tree Overview & Data Quality)](#general-tree-overview--data-quality)
  - [Testigos (Witness & Godparent Analysis)](#testigos-witness--godparent-analysis)
  - [Consanguinidad (Consanguinity & Inbreeding Analysis)](#consanguinidad-consanguinity--inbreeding-analysis)
  - [ADN & Genetics](#adn--genetics)
  - [Migration Intelligence](#migration-intelligence)
  - [Family Completion Engine](#family-completion-engine) *(includes manual Candidate Identification)*
  - [Export to GRAMPS (Write-back)](#export-to-gramps-write-back)
- [Getting Started](#getting-started)
- [Data Format](#data-format)
- [Tech Stack](#tech-stack)

---

## Overview

Rayzes is designed for genealogical researchers who need to go beyond basic family tree visualization. It is particularly useful for studying:

- **Compadrazgo networks**: Who were the godparents in a community, how often did they appear, and which families did they connect?
- **Genetic relatedness**: How inbred were historical populations, and which couples shared common ancestors?

The app requires a **GRAMPS XML file** (`.gramps`) as input, which you upload at startup. All analysis is performed in-browser with no backend server required.

A **language selector** (English / Spanish) is available at all times in the sidebar.

---

## Screenshots

| Map — Geographic connections | Social Network Graph |
|---|---|
| ![Map](screenshots/map.png) | ![Graph](screenshots/graph.png) |

| Top Witnesses | Confirm Identity Matches |
|---|---|
| ![Top Witnesses](screenshots/top%20witnesses.png) | ![Confirm Matches](screenshots/confirm%20matches.png) |

| Tree Endpoints | Interactive Pedigree | Historical Context |
|---|---|---|
| ![Tree Endpoints](screenshots/tree%20endpoints.png) | ![Interactive Pedigree](screenshots/interactive%20pedigree.png) | ![Historical Context](screenshots/historical%20context.png) |

---

## Modules

### General (Tree Overview & Data Quality)

This module provides three analytical sub-pages that work directly from the GRAMPS family tree data, focusing on data quality, temporal extremes, and historical enrichment. It is the natural entry point for any research session.

---

#### Extremos del árbol — Tree Endpoints

Identifies the genealogical and chronological extremes of the tree:

- **Leaf nodes**: Individuals with no known parents, representing the current research frontier
- **Research feasibility**: For each leaf node, estimates how likely it is that further ancestors can be found, based on estimated parent birth year, known location, and available record coverage
- **Record date ranges**: Allows the user to define the available parish record coverage per place (stored persistently in `data/gen_record_dates.json`), so feasibility estimates reflect actual archival reality
- **Auto-fill from sources**: Extracts start dates for baptism, marriage, death, and confirmation registers directly from the GRAMPS sources, using a keyword-based classifier that recognises common title prefixes (LB, LM, LD, LC) as well as free-text equivalents in any language. Only fills empty fields; does not overwrite manually entered values
- **Parish browser**: An expandable panel lists all sacramental registers found in the sources for each location in the table — parish name, register type, and year range — so the user can see at a glance which parish to search when a location had more than one
- **Confirmation column**: Tracks earliest available confirmation records alongside baptism, marriage, and death
- **Birth year inference**: When a leaf individual has no recorded birth or baptism year, the system estimates it from the birth years of their oldest known children or grandchildren, using the tree's own parent-age statistics
- **Birth place inference**: When no birth place is recorded, the most frequent birth/baptism place among the individual's children is used as a working hypothesis (clearly labelled in the table)
- **Column order persistence**: The column arrangement in the leaf table is preserved across saves
- **Ancestor highlighting**: A reference person selector lets the user choose any individual in the tree; their direct ancestors and siblings of those ancestors are highlighted in red in both the feasibility summary tables

---

#### Inconsistencias — Data Inconsistency Detector

Automatically scans the entire family tree for logical and biological inconsistencies, grouped into four categories:

**A — Problematic Children**
- Siblings born less than 9 months apart
- Possible twin births (same year, not annotated as twins in notes)
- Parent too old at time of child's birth (relative to P95 of the tree)
- Child born before parent

**B — Marriage Anomalies**
- Marriage at an unusually young age (below tree's own P5)
- Marriage at an unusually advanced age (above tree's own P95)
- Extreme age gap between spouses (>30 years)

**C — Chronological Inconsistencies**
- Death year before birth year
- Dates set in the future (after the current year)
- Marriage recorded before a spouse's birth
- Posthumous child (born more than 1 year after father's death)
- Premarital birth (born more than 1 year before parents' marriage)

**D — Biological Impossibilities**
- Duplicate events (two recorded births or two deaths for the same person)
- Circular ancestry (a person appears as their own ancestor in the pedigree graph)

Detection thresholds are derived automatically from the tree's own statistics (P5/P95 percentiles for marriage and parenthood ages), so the analysis adapts to the demographic profile of each dataset.

Each inconsistency is classified with a severity level: **error** (biological impossibility), **warning** (statistically anomalous), or **info** (plausible but noteworthy).

**Dismissal system**: Any inconsistency can be individually dismissed — marked as a known false positive that should not reappear in future sessions. Dismissed items are stored persistently in `data/dismissed_inconsistencies.json` and are shown in a collapsible section at the bottom of the page, where they can be restored at any time. The CSV export only includes active (non-dismissed) inconsistencies.

---

#### Contexto histórico — Historical Context

Enriches genealogical research by linking the life events of individuals and families to the historical events of the municipalities where they lived.

**Place management tab:**

Upload a JSON file containing historical events for any municipality present in the tree. Each file follows a simple structure:

```json
{
  "place": "Orihuela",
  "events": [
    { "year": 1521, "month": null, "description": "Germania revolt." },
    { "year": 1648, "month": 6, "description": "Plague epidemic." }
  ]
}
```

- `year`: event year (required)
- `month`: event month, 1–12 (optional)
- `description`: free-text description (required)

Uploading a new file for a place that already has data **merges** the events rather than replacing them: new events are appended and exact duplicates (same year and description) are discarded. The merged set is re-sorted chronologically. To start from scratch, use the delete button for that place before uploading.

The management panel lists all municipalities extracted from the tree, showing how many historical events are loaded for each and providing a delete button to remove data for a specific place.

**Timeline tab:**

Select an individual or a family from the tree. The app automatically identifies which loaded municipalities are linked to that person or family (from their birth, baptism, marriage, death, and children's events) and builds a chronological timeline interleaving:

- 🔵 **Personal events**: birth, baptism, marriage, birth of each child, death, and any other recorded events
- 📜 **Historical events**: events from the matched municipalities

For each historical event, if the individual has a known birth year, the approximate age at the time of the event is shown inline — e.g. *"Luis López was ~11 years old when this happened"*.

The timeline is scoped to a relevant window: it starts **10 years before the individual's birth** (to provide contextual lead-in) and ends at the last recorded personal event. Historical events outside this window are omitted.

The full timeline can be exported to CSV.

---

### Testigos (Witness & Godparent Analysis)

This module analyzes the role of witnesses and godparents (padrinos/madrinas) across baptism and marriage records. It includes 14 pages:

---

#### Explorar — Event Explorer

Search and filter the full event dataset. Filter by witness name, event type (baptism, marriage, etc.), location, and year range. Inspect individual records and see which witnesses appear in each event.

---

#### Mapa — Geographic Map

Visualize witness activity and migration on an interactive map. Supports **9 map modes**:

- **Hotspots**: Heatmap of event density by location
- **Connections**: Lines between places where the same witness appeared
- **Migrations**: Arrows indicating directional movement over time
- **By family**: Events grouped and colored by family surname
- **Timeline**: Animated map showing how activity shifted over the years
- **Clusters**: Geographic clustering of events
- **Radius**: Witness activity radius estimation
- **Comparison**: Side-by-side geographic comparison between witnesses
- **Influence zones**: Voronoi-based territory mapping per witness

---

#### Grafo — Social Network Graph

An interactive network graph where nodes are witnesses and edges represent co-appearances in events. Highlights:

- **Bridge witnesses**: People whose removal would disconnect parts of the network (high betweenness centrality)
- **Clusters**: Communities of witnesses that frequently collaborated
- Node sizing and coloring by event count or centrality score
- Filter by minimum edge weight or family

---

#### Timeline — Chronological View

An interactive chronological strip showing witness activity across time. Zoom in to specific decades, filter by witness or family, and inspect individual events by hovering.

---

#### Superpadrinos — Top Witnesses

Ranks witnesses by total number of events. For each top witness, shows:

- Full event timeline
- Families they most commonly served
- Geographic reach
- Years active and longevity score
- Activity trend (increasing, decreasing, or stable)

---

#### Notas — Event Notes Browser

Browse and filter the notes attached to events in the GRAMPS database. Supports manual **category overrides** to reclassify notes. Categories include things like occupation references, health records, social status markers, and more.

---

#### Análisis — Statistical Analysis

A deep-dive analytics page covering:

- **Timeline analysis**: Events per year with trend lines
- **Family pattern analysis**: Which families used the same godparents repeatedly
- **Endogamy score**: How closed the witness network is (percentage of internal vs. external connections)
- **Surname timeline**: Track how family surnames appear and disappear as witnesses over generations
- **Birth order and prestige**: Correlation between a child's birth order and the social standing of their chosen godparent
- **Stability vs. mobility**: Geographic mobility index for each witness

---

#### Identidad bayesiana — Bayesian Identity Resolution

Uses a Bayesian scoring model to compute the probability that two witness records refer to the same individual. The score combines:

- **Name similarity** (fuzzy string matching via rapidfuzz + jellyfish)
- **Temporal overlap**: Are the active years compatible?
- **Geographic proximity**: How close were the events in space?
- **Family overlap**: Did they appear for the same families?

Results are ranked by probability. High-confidence matches can be confirmed directly from this page.

---

#### Confirmar coincidencias — Confirm Identity Matches

When the same person appears under slightly different name spellings, this page presents candidate pairs for user review. For each candidate pair, you can:

- **Confirm**: Mark two records as the same person
- **Reject**: Mark them as distinct
- Confirmations are saved persistently to `data/confirmed_links.json` and propagate throughout all other pages

---

#### Pendientes — Pending Cases

Shows all witness records that have not yet been confirmed or rejected. Useful as a work queue to ensure no ambiguous identity cases go unresolved before exporting a final report.

---

#### Trayectoria vital — Life Trajectory

For a selected witness, reconstructs a probable life trajectory:

- Estimated birth and death year range
- Map of locations visited over their lifetime
- Events annotated on a personal timeline
- Family connections discovered through godparent appearances

---

#### Posibles familiares — Possible Relatives by Surname Coincidence

Detects events where a confirmed witness shares a surname with the event subject, flagging them as possible relatives for further study. The scoring model accounts for:

- **Surname position**: paternal (1st) vs. maternal (2nd) surnames are weighted differently — in Spanish records witnesses can be grandparents or uncles, so the maternal surname of a witness may match the paternal surname of the subject
- **Surname frequency**: hyper-frequent surnames (García, López, Smith…) receive a penalty, reducing false positives
- **Presence in the GRAMPS tree**: witnesses found in the family tree receive a score boost; those with a confirmed kinship path receive a larger one
- **Fuzzy matching**: historical spelling variation is handled with an 82 % similarity threshold via rapidfuzz

**Surname systems supported** (selectable in the sidebar):

| Code | System | Key characteristic |
|------|--------|--------------------|
| `es` | Spanish | Two surnames, paternal + maternal (weight 1.0 / 0.75) |
| `pt` | Portuguese | Two surnames, historical maternal-paternal order |
| `en` | English / Anglo-Saxon | Single patrilineal surname |
| `fr` | French | Single surname with noble particles (de, du, de la…) |
| `de` | German / Dutch | Single surname with particles (von, van, zu…) |
| `it` | Italian | Single surname with particles (di, del, della…) |
| `ar` | Arabic | Patronymic chain (ibn/bint); compared by chain position |
| `ru` | Russian / Slavic | Gendered endings normalised to root for comparison |
| `zh` | Chinese / Korean / Vietnamese | Surname first; heavily penalised due to limited repertoire |

Results are presented across four tabs:

- **Candidate list**: full sortable table with surname, position, similarity score, confidence level, GRAMPS ID (when found in tree), and inferred kinship relationship
- **By witness**: aggregated view showing how many distinct families each witness shares a surname with, and their maximum confidence level
- **By surname**: aggregated view showing which surnames produce the most coincidences, useful for spotting dominant family networks
- **Review**: a work queue for confirming or discarding each candidate; decisions are saved persistently to `data/confirmed_links.json`

Confidence levels:

- 🟢 **High**: rare surname + positional match + witness found in tree
- 🟡 **Medium**: moderate surname + correct positional match
- 🟠 **Low**: frequent surname or secondary-position coincidence
- 🔴 **Very low**: hyper-frequent surname with no additional evidence

Results can be exported to CSV.

---

#### Testigos en árbol — Witnesses in the Family Tree

Links witnesses from the event records back to named individuals in the GRAMPS family tree. Uses a scoring system that weighs:

- Name match quality
- Date compatibility
- Place overlap
- Existing confirmations

Shows matched individuals with their GRAMPS person ID and confidence score.

---

#### Informe — Report Export

Generates a narrative report for a selected witness or the full dataset. Export formats include:

- **HTML**: Formatted printable document with embedded charts
- **Markdown**: Plain-text structured summary
- **JSON**: Machine-readable data export

Reports include event tables, geographic summaries, family connections, and network statistics.

---

### Consanguinidad (Consanguinity & Inbreeding Analysis)

This module computes genetic relatedness metrics from the family tree. It parses the full pedigree from the GRAMPS file and runs graph-based algorithms to find relationships and inbreeding.

---

#### Inbreeding Coefficient (F)

Calculates the **Wright inbreeding coefficient (F)** for any individual in the tree. F measures the probability that both alleles at a random locus are identical by descent. The algorithm:

1. Builds a directed ancestor graph from the GRAMPS pedigree
2. Finds all paths between parents through common ancestors
3. Sums contributions from each common ancestor, weighted by path length and the ancestor's own F

Results are displayed per person with their ancestors' contribution breakdown.

---

#### Kinship Coefficient (Φ)

Computes the **kinship coefficient (Φ)** between any two selected individuals. Φ is the probability that a random allele drawn from each person is identical by descent. Φ is then converted to **coefficient of relationship (r)** for an intuitive relatedness percentage.

---

#### Relationship Classification

Given two individuals, the module classifies their relationship using both:

- **Kinship-based heuristics**: Φ thresholds for parent/child, full sibling, half-sibling, first cousin, etc.
- **Path distance heuristics**: Generational distance through the common ancestor graph

For complex pedigrees, both methods are shown and reconciled.

---

#### Consanguineous Couples

Scans all couples in the family tree and identifies pairs who are genetically related. For each consanguineous couple:

- Displays their kinship coefficient and relationship classification
- Shows the common ancestor(s) with full path
- Renders an interactive Pyvis graph of the connecting pedigree subgraph
- Sortable by F value, time period, or location

---

#### Common Ancestor Finder

Given two individuals, finds **all common ancestors** and the shortest paths through the pedigree connecting them. Results include:

- Ancestor name, GRAMPS ID, and birth/death dates
- Generation distance from each of the two individuals
- Contribution to total kinship

---

#### Pedigree Collapse Analysis

Identifies individuals who appear **more than once** in a pedigree (i.e., who are both paternal and maternal ancestors). This "pedigree collapse" is a direct consequence of consanguineous marriages in earlier generations. The page shows:

- Repeated ancestor list with frequency count
- Timeline of when pedigree collapse increased or decreased in the lineage

---

#### Interactive Pedigree Visualization

Three Pyvis-rendered interactive graph views:

- **Subtree view**: Expand an individual's ancestor tree up to N generations
- **Relationship view**: Show the pedigree subgraph connecting two specific people
- **Inbreeding colormap**: Color all individuals in the tree by their F value (gradient from white to red)

---

#### Historical Inbreeding Trends

A time-series chart of average inbreeding coefficient per generation or per decade, computed across all individuals with known birth years. Useful for identifying historical periods of increased endogamy.

---

#### Geographic Ancestor Distribution

Maps the birthplaces of an individual's ancestors across generations. Color-coded by generation depth, with lines connecting parent–child locations to reveal geographic origins and migration chains.

---

### ADN & Genetics

This module applies population genetics concepts to the GRAMPS pedigree, bridging genealogical research with modern genetic genealogy. It comprises three sub-pages accessible as tabs.

---

#### Fundadores — Founder Analysis

Identifies the **genealogical founders** of the tree: individuals with no known parents who appear as root nodes in the ancestor graph. For each founder, the module computes how much of the genetic material of any selected individual (or the whole pedigree) can be traced back to that founder.

**Individual view**: select any person in the tree and compute their founder contributions. Each founder's contribution is calculated by summing `0.5^(path_length − 1)` over all independent paths through the pedigree (accounting for pedigree collapse). Results include:

- An interactive pie chart showing the proportional contribution of each founder, plus an "unknown" slice representing ancestry not yet accounted for in the tree
- A sortable table with founder name, GRAMPS ID, and contribution percentage
- CSV export

**Pedigree-wide view**: runs the founder analysis across every non-founder individual in the tree and aggregates per-founder statistics:

- Number of descendants
- Average and maximum contribution across all descendants
- CSV export of the complete table

---

#### ADN compartido — Shared DNA Prediction

Predicts the expected shared DNA (in centimorgans, cM) between any two individuals in the tree, using the **Shared cM Project 4.0** reference data.

**How it works**:

1. Computes the **kinship coefficient (Φ)** between the two selected individuals using the full pedigree graph (identical to the algorithm used in the Consanguinity module)
2. Converts Φ to the **coefficient of relationship (R = 2Φ)**
3. Classifies the relationship by tracing direct ancestry paths and finding common ancestors, then mapping generational distances to a relationship category
4. Looks up the corresponding cM range (minimum, average, maximum) from the Shared cM Project 4.0 table

**Relationship categories supported** (16 total):

| Category | Avg. cM |
|---|---|
| Parent — Child | 3 479 |
| Full sibling | 2 543 |
| Half sibling / Grandparent | ~1 760 |
| Aunt/Uncle | 1 741 |
| Great-grandparent | 881 |
| 1st cousin | 866 |
| 1st cousin once removed | 433 |
| 2nd cousin | 229 |
| 3rd cousin | 74 |
| 4th cousin | 35 |
| Distant relative | ~12 |

Results display:
- Three metrics: Φ, R, and the classified relationship label
- A bar chart showing min/avg/max cM range
- A warning if no common ancestors were found in the tree (meaning the estimate relies solely on the kinship coefficient with no pedigree path confirmation)
- An expandable full reference table with all 16 relationship categories

> **Note:** Predictions assume a complete and accurate pedigree. Incomplete trees will underestimate kinship and produce lower cM ranges than actual DNA tests would show.

---

#### Línea materna / paterna — Matrilineal & Patrilineal Lineage Tracing

Traces the unbroken **maternal line** (mother → maternal grandmother → maternal great-grandmother…) or **paternal line** (father → paternal grandfather…) for any selected individual. These correspond to the genetic lines transmitted via mitochondrial DNA (mtDNA) and the non-recombining Y chromosome (Y-DNA) respectively.

For each line, the module provides:

- **List of ancestors** in order, with name, GRAMPS ID, birth year, and birth place
- **Generation count** (how far back the line extends before hitting a missing parent or unknown sex)
- **Stop reason**: why the trace ended
  - *No parent recorded*: the furthest-back ancestor has no parents in the tree
  - *Sex unknown*: the trace hit an individual whose sex is not recorded in GRAMPS, breaking the line
  - *Maximum depth reached*: the configurable generation limit was hit (default 20, adjustable in the sidebar up to 20)

**Visualizations**:

- **Timeline chart**: plots birth years along the line, generation by generation, so gaps and temporal jumps are immediately visible
- **Geographic map** (OpenStreetMap): renders each ancestor as a point with generation-indexed coloring, connected by lines in chronological order — useful for tracking migration patterns along a single lineage

**Sidebar control**: a global **max generations** slider (3–20, default 12) applies to all three sub-pages, controlling the depth of ancestor graph traversal.

---

### Migration Intelligence

This module traces how surnames and ancestral lineages moved across geography and time. It processes all individuals in the GRAMPS tree who have a known birth or baptism year and a geocoded place, and builds period-by-period migration trajectories.

---

#### Tab A — By Surname

Select one or more surnames to visualise their geographic trajectory. For each surname, the module:

1. Collects all tree members bearing that surname (first surname, normalised) who have known coordinates and year
2. Groups them into configurable time periods (10–100 years, default 25)
3. Computes the **geographic centroid** of each period (mean lat/lon of all individuals in that slot)
4. Draws lines connecting consecutive centroids on an OpenStreetMap base map
5. Sizes each point proportionally to the number of people it represents

**Distance table**: below the map, a summary table shows for each selected surname the total distance travelled (sum of Haversine distances between consecutive centroids), average speed in km/year, number of periods, and total time span.

**Historical correlation**: if historical event data has been loaded in the General → Historical Context sub-page, the app displays the events recorded for each place during the corresponding period — letting the researcher connect demographic movements with droughts, plagues, wars, or administrative changes.

**Spelling variant detection**: surnames that differ from a selected one by ≤ 2 characters (Levenshtein distance) are flagged with a warning, prompting the user to add the variants to the filter.

**Dispersion and unrelated-branch analysis**: for every selected surname the module automatically evaluates whether all its bearers in the tree plausibly belong to the same family line, or whether the surname is common enough that different, unconnected families share it:

- **Geographic clustering**: individuals are grouped into geographic clusters (greedy algorithm, 80 km radius). Surnames whose clusters are more than 300 km apart trigger an alert.
- **Family connectivity check**: for each pair of clusters, the app searches the GRAMPS family records for any parent–child or spousal link that bridges the two groups. If no such link is found, the clusters are declared unconnected.
- **Severity levels**:
  - ⛔ **Likely unrelated** — high dispersion + no family link detected between distant groups. The alert names each unconnected pair ("Sevilla (12 persons) ↔ Burgos (5 persons): no family relationship detected") and recommends analysing each group separately.
  - ⚠️ **Review recommended** — high dispersion but at least one family link bridges the clusters. The movement may reflect genuine migration, but manual verification is advised.
  - No alert — dispersion is within normal migration range.

---

#### Tab B — By Lineage

Select any individual in the tree and a generation depth (2–12). The module traces all direct ancestors up to that depth who have geocoded birthplaces and renders them on an interactive map:

- Points are **colour-coded by generation** (same palette as the Consanguinity geographic view, imported from the shared `geo_viz` module)
- **Directional arrows** connect parent and child locations, oriented according to the true geodesic bearing
- If historical event data is loaded, **orange dots** are overlaid on each ancestor's location marking events from that place and era, with the description shown on hover
- A **CSV download** exports the full ancestor list with name, generation, place, and coordinates (ancestors without coordinates are included with empty lat/lon fields)

---

### Family Completion Engine

This module covers two complementary approaches to finding missing parents in pre-modern genealogical records: a **manual mode** for investigating a specific case step by step, and a **batch engine** that automates the search across the entire tree.

---

#### Identificación de candidatos — Candidate Identification (manual mode)

Addresses a common problem in 16th–18th century records: marriages that do not record the parents of the contracting parties. When several individuals share the same name and approximate age, this sub-page provides a systematic, probabilistic framework for ranking them as candidates.

**The core hypothesis**: witnesses tend to repeat across events within the same social and family circle. If a witness at the mystery marriage also appears at the baptism of candidate A (or at the baptism or marriage of A's siblings), that is evidence that A is the individual being sought.

**Candidates are entered manually** — they are not required to be in the GRAMPS tree. For each candidate you can provide:

- Name and surnames
- Baptism year and place
- Baptism witnesses (free text, one per line)
- Any number of siblings, each with:
  - Name, baptism year, baptism place, and baptism witnesses
  - Marriage year, marriage place, and marriage witnesses

**Target marriage** is selected directly from the loaded GRAMPS tree. The app automatically extracts the witnesses recorded in the XML for that event.

**Typical marriage age** is computed dynamically from the tree itself — the mean and standard deviation of actual marriage ages for individuals in a matching time window and geographic area around the target event. If the local sample is too small (< 5 individuals), the window expands automatically (±30 → ±60 → ±120 years) with a note in the UI.

**Scoring model — six factors** combined via Bayesian likelihood ratios with a uniform prior over all candidates:

| Factor | Default weight | Description |
|--------|---------------|-------------|
| F1 — Own baptism witnesses | 35 % | Witness overlap between target marriage and candidate's own baptism |
| F2 — Siblings' baptism witnesses | 20 % | Overlap using the union of witnesses from all siblings' baptisms |
| F3 — Siblings' marriage witnesses | 15 % | Overlap using witnesses from siblings' marriages |
| F4 — Temporal coherence | 15 % | Gaussian score: how close the baptism year is to the expected year (target year − typical age) |
| F5 — Geographic coherence | 10 % | Exponential decay on Haversine distance between baptism place and marriage place |
| F6 — Candidate surname in witnesses | 5 % | Fraction of target witnesses who share a surname with the candidate |

Factors for which no data has been entered score `None` and do not penalise the candidate — their weight is redistributed proportionally among the active factors.

**Witness matching supports two match types**:

- **Full-name match**: fuzzy `token_sort_ratio` ≥ configurable threshold (default 80 %). Contributes 1.0 to the overlap coefficient. Displayed as `A ↔ B (score%)`.
- **Surname-only match**: triggered when the full-name match fails but the surname tokens match with ratio ≥ 85 %. The contribution is weighted by *surname rarity*:
  - Hyperfrequent surnames (García, López, Fernández, Pereira, etc.) → rarity **0.15** → contribution ~0.07 (nearly negligible)
  - Rare surnames with insufficient context (< 10 names in pool) → rarity **0.70** → contribution ~0.32 (moderate evidence — likely a sibling of the original witness)
  - Context-estimated surnames with a large pool → rarity scales inversely with observed frequency
  - Displayed as `A ≈ B (apellido, rareza X%)` in the match detail panel

**Configuration panel** (collapsed by default):

- Fuzzy threshold slider (50–100, default 80)
- Geographic scale slider in km (default 30 km)
- Per-factor weight sliders (auto-normalised to sum = 1)
- Toggle: include siblings' marriage witnesses in F3
- Toggle: enable the surname-in-witnesses factor (F6)

**Results**:

- Sortable results table with per-candidate probability and individual factor scores
- Visual progress bars coloured green (≥ 70 %), yellow (40–70 %), or red (< 40 %)
- Expandable detail panel per candidate showing which witnesses matched (full or surname), which did not, and for which events
- **Narrative summary in natural language** (ES / EN): a coherent, paragraph-form account of the evidence — typical marriage age and expected birth range, per-candidate analysis with matched witnesses, temporal and geographic coherence, and a conclusion naming the most probable candidate or flagging a tie when two candidates are within 10 percentage points of each other

---

#### Completion Engine (batch mode)

The batch engine automates the most time-consuming task in pre-modern genealogical research: finding the missing parents of individuals whose marriage records do not name them. It runs a Bayesian analysis over the entire tree, detects every spouse with no known parents, and ranks potential father or mother candidates from within the tree itself.

---

#### How it works

For each marriage in the GRAMPS tree where at least one spouse has no known parents, the engine:

1. Extracts the **witnesses recorded in the marriage act** from the GRAMPS XML — the same witness data used by the Candidate Identification sub-page in the General module
2. Searches the tree for **parent candidates** matching the orphan's surname, expected birth year range (derived from the tree's own marriage-age statistics), and geographic area (≤ 50 km)
3. Scores each candidate using the same **Bayesian likelihood-ratio model** as the manual Candidate Identification page, extended with a genealogical factor specific to this batch mode:

| Factor | Weight | Description |
|--------|--------|-------------|
| F1 — Marriage witnesses vs. candidate's baptism witnesses | 35 % | Overlap between the marriage act witnesses and the witnesses at the candidate's own baptism |
| F2 — Marriage witnesses vs. siblings' baptism witnesses | 20 % | Overlap with the union of witnesses from all of the candidate's siblings' baptisms |
| F3 — Marriage witnesses vs. siblings' marriage witnesses | 15 % | Overlap with witnesses from the candidate's siblings' marriages |
| F4 — Temporal coherence | 15 % | Gaussian score: how close the candidate's birth year is to the expected year (marriage year − typical parent age) |
| F5 — Geographic coherence | 10 % | Exponential decay on Haversine distance between candidate's baptism place and the marriage place |
| F6 — Candidate surname in witnesses | 5 % | Fraction of marriage witnesses who share a surname with the candidate |
| F7 — Generational witness overlap | 15 % | Takes the best of two sub-signals: (a) overlap between the orphan's own baptism witnesses and the candidate's baptism witnesses; (b) overlap between the baptism witnesses of the orphan's *children* and the candidate's baptism witnesses — exploiting the common practice of grandparents acting as godparents of their grandchildren |

Factors with no available data score `None` and do not penalise the candidate — their weight is redistributed proportionally among the active factors. A uniform prior over all candidates is applied after individual scoring.

**Typical parent age** is derived dynamically from the tree, using the same expanding-window algorithm as the manual identification page: it first tries a ±30 year window around the target marriage year, then ±60, then ±120 if the local sample is too small (< 5 individuals per sex).

---

#### Three-tab interface

**Tab 1 — Summary**

Four headline metrics computed over the full batch:
- Total orphaned spouses analysed
- Number with at least one candidate found
- Number of high-confidence cases (top candidate probability ≥ 65 %)
- Number of cases with no baptism witnesses (low-evidence mode)

A warning banner flags the low-evidence count, since without witnesses only F4 and F5 are active.

**Tab 2 — Results**

A scrollable, fully sortable table showing all results that pass the active sidebar filters. Columns include orphan name, marriage ID, year, role sought (father / mother), top candidate name, probability, and an evidence quality indicator.

A **search box** narrows the table instantly by any fragment of the orphan's name, their GRAMPS individual ID, or the marriage family ID. When the filtered set has 50 or fewer entries, a dropdown also appears for direct case selection.

Clicking any row selects the case and navigates to the Detail tab. The full table can be exported to CSV.

**Tab 3 — Detail**

For the selected case, shows:
- The marriage act witnesses, the orphan's own baptism witnesses (if any), and the baptism witnesses of the orphan's children (the F7 signal sources)
- A **natural-language narrative** (ES / EN) explaining the evidence: typical marriage age and expected birth range, per-candidate witness matches, temporal and geographic coherence, and a conclusion naming the most probable parent or flagging a tie
- A candidate table with per-factor scores including F7 (with a breakdown of how many matches came from the orphan's baptism vs. the children's baptisms)
- **Save to confirmed results** / **Remove saved** buttons: confirmed cases are written to `data/family_completion_results.json` and are automatically picked up by the **Export to GRAMPS** module as notes on the relevant marriage families. Re-running the analysis does not overwrite this file — the batch lives only in the session, and only your manually confirmed cases persist on disk

---

#### Sidebar filters

- **Minimum probability** slider (10 %–95 %, default 35 %): hides cases where the top candidate falls below the threshold
- **Only with witnesses** checkbox: restricts results to cases where the marriage act has at least one recorded witness
- **Role filter**: show only cases where the missing parent is the father, or only the mother, or both

---

#### Persistent data

| File | Purpose |
|---|---|
| `data/family_completion_results.json` | Manually confirmed parent–child candidate links, consumed by the Export to GRAMPS module |

---

### Export to GRAMPS (Write-back)

This module closes the research loop: it takes all the analytical work done in Rayzes — confirmed witness identities, detected inconsistencies, and Family Completion Engine candidates — and writes them back into the original GRAMPS file as standard notes and tags, without any third-party dependency.

---

#### What it writes

| Data source | Written as | Target in GRAMPS |
|---|---|---|
| Confirmed witness links (`confirmed_links.json`) | `<note>` on the person | Person record |
| Active inconsistencies (`dismissed_inconsistencies.json` excluded) | `<tagref>` pointing to a `GenHelper:Error` or `GenHelper:Warning` tag | Person or family record |
| Family Completion Engine candidates (prob ≥ threshold) | `<note>` on the family | Family (marriage) record |

**Confirmation notes** are worded as:
> *"Testigo confirmado: [witness name] identificado como [person name] ([GRAMPS ID]) por GenHelper [date]."*

**Inconsistency tags** use two fixed tag definitions that are created in the GRAMPS file if they do not already exist:
- `GenHelper:Error` (red `#CC0000`) — biological impossibilities
- `GenHelper:Warning` (orange `#FF8C00`) — statistically anomalous but possible

**Candidate notes** are worded as:
> *"Candidato probable a [father/mother] de [orphan name]: [candidate name] (prob=XX%). Factores: F1=X, F4=X, F5=X. Generado por GenHelper [date]."*

---

#### Collision-safe ID allocation

The module scans the full set of existing handles and numeric IDs (notes, tags, persons, families, events, places) in the GRAMPS file before creating anything new. New note IDs follow the `N{NNNN}` pattern; tag IDs follow `T{NNNN}`. New handles are generated as `_` + `secrets.token_hex(14)` (28-character hex strings) and checked against all existing handles before use. No existing data is ever modified or deleted.

---

#### Duplicate detection

Before adding any note or tag, the module checks whether the target person or family already carries an identical or equivalent annotation — both from the original file and from any notes added earlier in the same export session. Duplicates are silently skipped.

---

#### UI

- **Checkboxes** to select which data categories to include (confirmation notes, inconsistency tags, candidate notes)
- **Minimum probability slider** (default 65 %) for candidate notes
- **Preview metrics**: how many notes and tags would be added before generating the file
- **Tree mismatch warning**: if the loaded `.gramps` file contains significantly more or fewer persons than the saved batch results, a warning is shown before the user proceeds
- **Download button**: generates the enriched `.gramps` file in memory and offers it as a direct download — nothing is written to disk on the server
- **Expandable change log**: lists every note and tag added, with the affected person or family ID

---

#### Technical notes

- Parsing and serialisation use **lxml** with a fallback to the standard library `xml.etree.ElementTree` if lxml is not available
- The output file is gzip-compressed (`.gramps` format), identical to what GRAMPS itself produces
- Special characters in notes (`&`, `<`, `>`) are escaped automatically by both lxml and ElementTree — no manual escaping required
- The module only reads the GRAMPS file that is already loaded in the sidebar — no separate upload is needed

---

## Getting Started

### Requirements

- Python 3.10+ (3.10 or 3.11 recommended — required by pinned dependencies such as `numpy==1.26.4` and `pandas==2.2.1`)
- pip

### Installation

```bash
git clone https://github.com/funkytrain/Rayzes.git
cd Rayzes
```

Create and activate a virtual environment:

```bash
# macOS / Linux
python -m venv venv
source venv/bin/activate

# Windows
python -m venv venv
venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

> **Note on PDF export:** The `pdfkit` package (used by the Consanguinity report exporter) requires **wkhtmltopdf** to be installed at the OS level. Without it, PDF export will fail but all other features work normally.
> - **Windows:** Download the installer from [wkhtmltopdf.org](https://wkhtmltopdf.org/downloads.html) and add it to your PATH.
> - **macOS:** `brew install wkhtmltopdf`
> - **Linux:** `sudo apt install wkhtmltopdf`

### Running the App

```bash
streamlit run main.py
```

Open your browser at `http://localhost:8501`.

On startup, you will be prompted to upload a `.gramps` file. This is the standard export format from the [GRAMPS genealogy application](https://gramps-project.org/).

### Persistent Data

The following files in `data/` are created and updated automatically as you use the app:

| File | Purpose |
|---|---|
| `confirmed_links.json` | Confirmed/rejected witness identity matches; also feeds confirmation notes in the Export module |
| `note_category_overrides.json` | Manual note category reclassifications |
| `gen_record_dates.json` | User-defined civil/parish record coverage ranges per place |
| `dismissed_inconsistencies.json` | Inconsistencies manually dismissed as false positives; dismissed items are excluded from Export tags |
| `historical/<place>.json` | Historical events uploaded per municipality for the Historical Context sub-page |
| `family_completion_results.json` | Manually confirmed parent–child candidates from the Family Completion Engine; fed into Export candidate notes |

These files persist between sessions. Back them up if you want to preserve your review work.

---

## Data Format

Rayzes reads **GRAMPS XML files** (`.gramps` extension). This is the native export format of [GRAMPS](https://gramps-project.org/), a free and open-source genealogy program. To export from GRAMPS:

1. Open your database in GRAMPS
2. Go to **Family Trees → Export**
3. Choose **GRAMPS XML** as the format
4. Save the `.gramps` file and upload it to Rayzes

The app expects the file to contain people, families, events, and places. Witness references in events (roles: witness, godfather, godmother) are the primary data source for the Testigos module.

---

## Tech Stack

| Layer | Libraries |
|---|---|
| Web UI | Streamlit |
| Data processing | pandas, numpy |
| XML parsing | lxml |
| Graph analysis | networkx, pyvis |
| Geographic maps | folium, streamlit-folium |
| Fuzzy matching | rapidfuzz, jellyfish |
| Clustering | scikit-learn |
| Visualization | plotly, matplotlib, seaborn |
| Scientific computing | scipy |
| Report templating | Jinja2, pdfkit |
