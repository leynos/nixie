# A Technical Implementation Blueprint for the Wildside Recommendation Engine in Rust

## Introduction

The Wildside recommendation engine represents a sophisticated system designed
to generate personalized, interesting walking tours by harmonizing geospatial
data with rich semantic information. The challenge lies in translating this
ambitious conceptual design into a robust, performant, and maintainable
software application. This report provides an exhaustive, expert-level analysis
of Rust libraries, architectural patterns, and implementation strategies to
build the Wildside engine, moving from the abstract problem statement to a
concrete engineering blueprint.

The selection of the Rust programming language for this endeavor is
well-founded. Rust's emphasis on performance, memory safety, and concurrency
makes it an ideal candidate for a system that is both data-intensive and
computationally demanding.1 The core tasks of parsing massive binary data
files, executing complex scoring algorithms, and solving NP-hard optimization
problems all align with the language's strengths.

This report is structured to follow the logical data flow of the Wildside
engine. It begins with the foundational layer of data ingestion, evaluating
options for processing OpenStreetMap and Wikidata. It then proceeds to the
implementation of the POI scoring and personalization algorithms. The
subsequent sections have been significantly revised to reflect a library-first
architecture, detailing a multi-crate structure, a stable API contract, a
rigorous testing discipline, and a clear implementation roadmap. This ensures
that every technical decision is justified, every trade-off is examined, and
the final proposed architecture is powerful, pragmatic, and built for long-term
maintainability.

### Core domain model

The engine relies on a small set of shared data structures housed in the
`wildside-core` crate. Keeping these types minimal reduces coupling while
providing a stable vocabulary across crates.

- `PointOfInterest` stores a unique identifier, a `geo::Coord`, and a map of
  tags. Tags remain a `HashMap<String, String>` to mirror the free-form
  key/value pairs common in OpenStreetMap. Convenience constructors provide
  explicit creation paths with or without tags. The struct now implements
  `rstar::RTreeObject`, allowing POIs to be inserted directly into R\*-trees
  without additional wrappers.
- `Theme` is an enum describing broad categories like history, art, and food.
  Using an enum rather than free-form strings prevents runtime typos.
- `InterestProfile` represents thematic preferences as a `HashMap<Theme, f32>`
  of weights. Builder-style methods (`with_weight` and `set_weight`) support
  ergonomic construction and mutation.
- `Route` contains the ordered list of `PointOfInterest` values selected for a
  tour and the overall `Duration` required to visit them. `Route::new` and
  `Route::empty` offer clear constructors.
<!-- markdownlint-disable-next-line MD013 -->
- `PoiStore` abstracts read-only POI access. The
  <!-- markdownlint-disable-next-line MD013 -->
  `get_pois_in_bbox(&self, bbox: &geo::Rect<f64>) -> Box<dyn Iterator<Item = PointOfInterest> + Send + '_>`
   method returns all POIs inside an axis-aligned bounding box (WGS84;
  `x = longitude`, `y = latitude`). The full semantics are documented in
  [`wildside_core::store::PoiStore`](../wildside-core/src/store.rs); indexing
  strategy is left to implementers.
- `SqlitePoiStore` is the first production-grade implementation of that
  interface. It expects two artefacts produced by the offline ETL pipeline:
  `pois.db` (an SQLite database whose `pois` table stores POI ids, coordinates,
  and JSON-encoded tags) and `pois.rstar` (a binary R\*-tree serialization).
  The binary artefact uses a fixed `WSPI` magic number, a little-endian `u16`
  version (currently `2`), followed by a `bincode` payload of
  [`PointOfInterest`](../wildside-core/src/poi.rs) structs. Version 2 stores
  the full POI records (id, `geo::Coord`, and tag map) directly in the R\*-tree
  so lookups can avoid secondary hash-map probes. During start-up the store
  reads these entries, validates them against SQLite in batches, and bulk-loads
  an in-memory `RTree<PointOfInterest>`. Bounding-box queries clone matching
  entries from the tree, avoiding additional database round-trips.
<!-- markdownlint-disable-next-line MD013 -->
- `TravelTimeProvider` produces an `n×n` matrix of `Duration` values for a
  slice of POIs via
  <!-- markdownlint-disable-next-line MD013 -->
  `get_travel_time_matrix(&self, pois: &[PointOfInterest]) -> Result<TravelTimeMatrix, TravelTimeError>`.
   The method returns an error if called with an empty slice, ensuring callers
  validate inputs before requesting travel times.

- `Scorer` converts a `PointOfInterest` and an `InterestProfile` into a `f32`
  relevance score. Implementations must be `Send + Sync` so scoring can occur
  across threads. The method is deterministic and infallible; implementers
  return `0.0` when no signals are present. This keeps scoring simple and
  composable for different weighting strategies.

  Implementations MUST return finite, non-negative values. Scores SHOULD be
  normalised to the `[0.0, 1.0]` range unless explicitly documented otherwise,
  allowing callers to combine signals with predictable bounds. Implementations
  MUST produce identical scores for identical inputs, guaranteeing
  deterministic behaviour.

- Test utilities such as an in-memory `PoiStore`, a unit travel-time provider,
  and a `TagScorer` compile automatically in tests and are gated behind a
  `test-support` feature for consumers, preventing accidental production
  dependencies.

### Spatial indexing

Spatial queries rely on the `SpatialIndex` newtype, which wraps an
`rstar::RTree<PointOfInterest>` built through the `build_spatial_index` helper.
`build_spatial_index` consumes any iterator of POIs and uses `RTree::bulk_load`
to balance the tree in a single pass without cloning owned data. The index
exposes read-only helpers for iteration, bounding-box queries, and length
checks while keeping `rstar` types out of the public API. Reusing the GeoRust
`Coord` type keeps geometric semantics consistent across ingestion, storage,
and querying components.

The following class diagram describes the relationships between
`PointOfInterest`, `SpatialIndex`, the underlying R-tree, and the
`build_spatial_index` helper.

```mermaid
classDiagram
    class PointOfInterest {
        +id: u64
        +location: Coord
        +tags: Tags
        +new(id: u64, location: Coord, tags: Tags)
        +with_empty_tags(id: u64, location: Coord)
    }
    PointOfInterest ..|> RTreeObject
    RTreeObject <|.. PointOfInterest
    class RTreeObject {
        +envelope() Envelope
    }
    class RTree {
        +bulk_load(items: Vec<T>)
        +size()
        +locate_in_envelope_intersecting(envelope: Envelope)
    }
    PointOfInterest --> RTree
    class SpatialIndex {
        +len() usize
        +is_empty() bool
        +iter() Iterator<PointOfInterest>
        +query_within(minimum: Coord, maximum: Coord) Vec<PointOfInterest>
    }
    SpatialIndex --> RTree
    PointOfInterest --> SpatialIndex
    class build_spatial_index {
        +build_spatial_index(pois: IntoIterator<PointOfInterest>) SpatialIndex
    }
    build_spatial_index --> SpatialIndex
    build_spatial_index --> PointOfInterest
```

These definitions form the backbone of the recommendation engine; higher level
components such as scorers and solvers operate exclusively on these types.

At runtime the `SqlitePoiStore` provides the fast-path for spatial lookups. The
R\*-tree owns full `PointOfInterest` values, so query responses can be produced
without re-querying SQLite. Start-up still validates every referenced id in
manageable batches, hydrating rows only to surface missing entries or malformed
tag payloads. Once validation succeeds the database connection is released, and
bounding-box queries clone matching POIs directly from the tree, guaranteeing a
stable response order.

```mermaid
sequenceDiagram
  autonumber
  actor App
  participant Store as SqlitePoiStore
  participant FS as Filesystem
  participant RTree as RTree<PointOfInterest>

  App->>Store: open(path)
  Store->>FS: read spatial_index.bin
  FS-->>Store: bytes
  Store->>Store: validate magic/version (v2)
  Store->>Store: decode Vec<PointOfInterest>
  Store->>RTree: bulk_load(PointOfInterest[])
  Store-->>App: SqlitePoiStore ready
  Note over Store,RTree: Errors surfaced as SpatialIndexError via SqlitePoiStoreError::SpatialIndex
```

The following sequence diagram shows how the application queries the SQLite POI
store which uses an R-tree to locate points within a bounding box.

```mermaid
sequenceDiagram
  autonumber
  actor App
  participant Store as SqlitePoiStore
  participant RTree as RTree<PointOfInterest>

  App->>Store: get_pois_in_bbox(bbox)
  Store->>RTree: locate_in_envelope(bbox)
  RTree-->>Store: PointOfInterest[]
  Store->>Store: sort by id
  Store-->>App: Vec<PointOfInterest>
  Note over Store: No SQLite rehydration
```

## Section 1: The Data Foundation - Ingesting and Integrating Open Data

The intelligence of the Wildside engine is predicated on the quality and
accessibility of its data. This section details the critical first stage of the
system: building a robust, efficient, and scalable data ingestion pipeline for
the OpenStreetMap (OSM) and Wikidata datasets. The architectural and library
choices made here will fundamentally impact the performance, operational cost,
and development complexity of the entire application. The goal is to transform
raw, community-driven data into a set of structured, read-only artefacts that
can be efficiently queried by the core engine.

### 1.1. Processing Geospatial Structure: A Comparative Analysis of OpenStreetMap PBF Parsers

The problem statement correctly identifies that OSM's flexible, schema-less
tagging system and the sheer volume of data contained in Protocolbuffer Binary
Format (PBF) files present a significant data processing challenge. The first
and most crucial step is to select a high-performance, reliable, and
permissively licensed parser to extract nodes, ways, and relations from these
files.

Two prominent candidates emerge from the Rust ecosystem: `osmpbf` and
`osmpbfreader`. While both are capable, they differ significantly in licensing
and features, making the choice between them a critical one.

Candidate 1: osmpbf

The osmpbf crate is a modern library designed with performance as a primary
goal.2 It offers lazy-decoding and, most importantly, built-in support for
parallelism. The PBF format is structured as a sequence of independent "blobs,"
a design that

`osmpbf` leverages to process these blobs in parallel across multiple CPU
cores. Its `par_map_reduce` method provides a high-level, idiomatic Rust API
for this purpose, which is an essential feature for efficiently processing the
country- or continent-sized PBF files that a service like Wildside would need
to ingest.2 From a legal standpoint,

`osmpbf` is dual-licensed under the Apache-2.0 and MIT licenses, which are
standard, permissive licenses suitable for commercial software development.2

Candidate 2: osmpbfreader

The osmpbfreader crate is another popular and effective library for this task,
with a track record of strong performance on large datasets.3 However, its
licensing presents a challenge. The crate is licensed under the "Do What The
Fuck You Want To Public License, Version 2" (WTFPLv2).3 While extremely
permissive in spirit, its unconventional wording can be a point of friction for
corporate legal review.

The user's requirement for a "permissive licence" is a critical non-functional
requirement. Fortunately, the legally clearer choice is also the technically
superior one for this use case. The explicit, high-level parallel processing
API in `osmpbf` directly addresses the need for efficiency in the offline ETL
process.

#### Table 1: Comparative Analysis of OSM PBF Parser Crates

| Crate Name   | Primary License  | Key Technical Features                                                                              | Maintenance Status     | Suitability for Wildside                                                                                                                                                                             |
| ------------ | ---------------- | --------------------------------------------------------------------------------------------------- | ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| osmpbf       | MIT / Apache-2.0 | High-performance parallel processing (par_map_reduce), lazy decoding, clear API for PBF hierarchy.2 | Updated 5 months ago.4 | Highly Recommended. The combination of industry-standard permissive licensing and explicit support for parallelism makes it the ideal choice for a robust, commercial-grade data ingestion pipeline. |
| osmpbfreader | WTFPLv2          | Proven performance on large files, support for resolving object dependencies.                       | Updated 2 months ago.  | Not Recommended. The WTFPLv2 license introduces unnecessary legal risk for a commercial project. While technically capable, the licensing issue is a critical blocker.                               |

**Final Recommendation:** The `osmpbf` crate is the unequivocally recommended
library. Its combination of industry-standard permissive licensing and
high-performance parallel processing features makes it the ideal and most
responsible foundation for the Wildside OSM data ingestion pipeline.

#### Implemented ingestion summary API

The initial ingestion surface now lives in the `wildside-data` crate. The
`ingest_osm_pbf` function wraps `osmpbf::ElementReader::par_map_reduce` to
count nodes, ways and relations while computing a `geo::Rect` bounding box for
all node coordinates. The summary aggregates results from each blob in
parallel, providing deterministic totals that are safe to merge across threads.
The ingestion surface now exposes an `OsmIngestReport` layered atop the
summary. It emits `PointOfInterest` records: nodes tagged with `historic` or
`tourism` become POIs immediately, while tagged ways defer until their node
geometry is known. The pass keeps a lightweight coordinate index for referenced
nodes and encodes element identifiers into the `PointOfInterest::id` namespace
by reserving the top two bits for the element kind. Way geometry anchors to the
first resolved node, trading perfect accuracy for deterministic behaviour until
richer geometric primitives arrive. Ways whose referenced nodes are missing or
invalid are skipped to prevent ghost POIs.

A parallel scan records unresolved way nodes before a sequential follow-up
hydrates only the coordinates still missing, keeping memory usage bounded by
the relevant geometry. Identifiers left unresolved after both passes emit
warnings so operators can investigate fixture gaps early.

### 1.2. Semantic Enrichment: Strategies for Interfacing with Wikidata

The `wikidata=*` tag is the "critical conduit" that transforms raw OSM data
into rich, queryable knowledge. The most robust architecture for accessing this
information is to create a local, high-performance copy of the necessary
Wikidata information via an offline ETL (Extract, Transform, Load) pipeline. A
detailed technical design for this ETL lives in
`docs/wikidata-etl-pipeline-technical-design.md`.

This approach involves periodically downloading the complete Wikidata JSON dump
and loading it into a local, indexed database. A fast, parallel parser is
essential. The `wikidata-rust` crate is a purpose-built tool for this task.5
For storage, the

`wd2sql` tool provides an excellent template: it uses `simd-json` for
high-speed parsing and loads the data into a queryable SQLite database. This
strategy can be replicated or adapted, potentially using a higher-performance
key-value store like RocksDB (for which Rust has mature bindings such as
`librocksdb-sys`) to create custom indices tailored specifically to the
properties required for POI scoring.

The primary advantage of this approach is extremely low query latency, as all
lookups happen against a local database. It enables complex, pre-calculated
analytics (such as the global popularity score) and makes the service immune to
public endpoint outages. The nature of the Wildside scoring algorithm, which
requires checking multiple properties for thousands of candidate POIs per
request, makes this offline approach the only viable long-term solution.

#### 1.2.1. Initial dump acquisition implementation

The first increment of the Wikidata ETL focuses on reliably acquiring and
auditing the upstream dump artefact. The `wildside-data` crate now exposes a
`wikidata::dump` module that encapsulates three responsibilities:

- **HTTP transport:** `HttpDumpSource` wraps the asynchronous `reqwest`
  client, issues requests on the Tokio runtime, and always sends a descriptive
  `User-Agent` string. Responses stream through `tokio-util`'s `SyncIoBridge`,
  letting the downloader parse `dumpstatus.json` and copy the archive without
  materializing the payload in memory while still surfacing network faults as
  structured `TransportError` values.
- **Manifest parsing:** `resolve_latest_descriptor` downloads
  `dumpstatus.json`, decodes it with `simd-json`, and walks the manifest to
  locate the most recent `*-all.json.bz2` artefact. The parser ignores
  unrelated jobs and tolerates missing optional fields, surfacing
  `WikidataDumpError::MissingDump` when no suitable entry is found.
- **Download logging:** `DownloadLog` stores a durable audit trail in
  SQLite via `rusqlite`. The crate is compiled with the `bundled` feature, so
  CI and developer workstations link against the same SQLite release.
  Initialization seeds uniqueness and timestamp indexes. It records the
  selected file name, URL, checksums, and byte counts. This metadata primes
  future reconciliation jobs that will import claims into `pois.db`.

The binary entry point (`cargo run -p wildside-data --bin wikidata_etl`)
connects those primitives to an operator-facing CLI backed by `clap`. Users
select an output directory, optionally override the file name, and can opt in
to logging by passing `--metadata <path>`. Standardized flag handling now
covers help/version output and validation, and the tool refuses to overwrite
existing dumps unless `--overwrite` is supplied. Successful runs emit a
succinct summary, keeping the command idempotent and easy to schedule whilst
the downstream parsing stages are implemented.

#### 1.2.2. Linked entity extraction implementation

The second increment introduces a streaming parser that connects the Wikidata
dump to the POIs discovered during OSM ingestion. The `PoiEntityLinks`
structure scans the ingested POIs, normalizes `wikidata=*` tag values (handling
common variants such as full URLs), and records the mapping from Wikidata
entity identifiers to OSM ids. The parser accepts any `Read` implementation and
wraps it in a `BufReader` so the huge JSON dump is never materialized in
memory. Each line is trimmed, commas that separate JSON entries are removed,
and the payload is deserialized via `simd-json` into a lightweight
representation containing just the entity id and claims.

Only entities referenced by the `PoiEntityLinks` set are processed further. For
those entities, the parser extracts `P1435` heritage designation claims by
inspecting the `mainsnak` data, filtering for `value` snaks, and collecting the
target entity ids. Both the linked POI ids and the designation ids are sorted
and deduplicated to keep the downstream SQLite schema deterministic. Errors are
surfaced with line numbers, so operators can diagnose malformed dump entries
without re-running the entire pipeline, while unrelated entities are skipped in
constant time.

The following sequence diagram illustrates the line-by-line processing flow,
including entity filtering, claim extraction, and error handling with
line-numbered reporting.

```mermaid
sequenceDiagram
    participant Caller
    participant extract_linked_entity_claims
    participant BufReader
    participant JSONParser as simd-json Parser
    participant PoiEntityLinks as PoiEntityLinks Filter

    Caller->>extract_linked_entity_claims: Call with reader & links

    loop For each line in dump
        extract_linked_entity_claims->>BufReader: Read next line
        BufReader-->>extract_linked_entity_claims: Line data or EOF

        alt Line read successfully
            extract_linked_entity_claims->>JSONParser: Deserialise JSON
            alt JSON valid
                JSONParser-->>extract_linked_entity_claims: RawEntity
                extract_linked_entity_claims->>PoiEntityLinks: Check entity exists in links
                alt Entity in links
                    PoiEntityLinks-->>extract_linked_entity_claims: Linked POI IDs
                    extract_linked_entity_claims->>extract_linked_entity_claims: Extract P1435 heritage claims
                    extract_linked_entity_claims->>extract_linked_entity_claims: Sort & deduplicate designations
                    extract_linked_entity_claims-->>extract_linked_entity_claims: Create EntityClaims
                else Entity not in links
                    extract_linked_entity_claims->>extract_linked_entity_claims: Skip (constant time)
                end
            else JSON invalid
                JSONParser-->>extract_linked_entity_claims: ParseError + line number
                extract_linked_entity_claims-->>Caller: Err(WikidataEtlError::ParseEntity)
            end
        else Line read failed
            BufReader-->>extract_linked_entity_claims: ReadError + line number
            extract_linked_entity_claims-->>Caller: Err(WikidataEtlError::ReadLine)
        end
    end

    extract_linked_entity_claims-->>Caller: Ok(Vec<EntityClaims>)
```

#### 1.2.3. SQLite schema for Wikidata claims

The extracted claims are stored in the shared `pois.db` database via the
`wildside_data::wikidata::store` module. Schema initialisation is handled by
the `initialise_schema` function, which enables foreign keys and creates a
compact set of normalised tables:

- `wikidata_entities` contains every entity identifier appearing in the dump.
- `poi_wikidata_links` maps POI ids to their linked Wikidata entities and
  enforces referential integrity against the existing `pois` table.
- `wikidata_entity_claims` stores statement triples for each entity, keyed by
  `(entity_id, property_id, value_entity_id)`. The first use case captures
  `P1435` heritage designations, but the schema supports future properties.

Indexes on `poi_wikidata_links(entity_id, poi_id)` and
`wikidata_entity_claims(property_id, value_entity_id, entity_id)` keep POI and
property lookups fast. A view named `poi_wikidata_claims` joins both tables so
the scoring pipeline can resolve a POI's claims without handwritten joins. A
`wikidata_schema_version` table records the schema version (`1` initially) so
future migrations can detect outdated installations. Claim persistence performs
idempotent inserts and verifies that every referenced POI exists before
linking; missing POIs raise an explicit `MissingPoi` error rather than failing
deep in SQLite.

#### Table 2: Comparative Analysis of Wikidata Interaction Strategies

| Approach                | Key Crates                         | Data Freshness                  | Request Latency              | Infrastructure Complexity     | Scalability for Wildside's Scoring                                                                                           |
| ----------------------- | ---------------------------------- | ------------------------------- | ---------------------------- | ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Live SPARQL Queries     | tokio, sparql-client 6             | Real-time                       | High (100s of ms to seconds) | Low (stateless)               | Very Poor. Infeasible to run thousands of queries per user request. Will be rate-limited and result in extreme latency.      |
| Offline Dump Processing | wikidata-rust, simd-json, rusqlite | Stale (updated on ETL schedule) | Very Low (sub-millisecond)   | High (ETL pipeline, database) | Excellent. Enables millions of fast, local lookups per second, making the personalization algorithm performant and scalable. |

**Final Recommendation:** Implement an **offline data processing pipeline
(Approach B)**. For the initial implementation, a `wd2sql`-inspired approach
using a local SQLite database managed via the `rusqlite` crate offers the best
balance. The `wikidata` crate will be invaluable for defining the Rust data
structures that model Wikidata entities and claims.

### 1.3. Foundational Geospatial Primitives and Spatial Indexing

Once the raw data is parsed from OSM, it must be represented in structured
geometric types and indexed for efficient spatial querying. The Rust community
has consolidated its geospatial efforts into the `GeoRust` collective, which
provides a suite of interoperable and well-maintained crates.8

Core Data Types with geo

The geo crate is the cornerstone of this ecosystem, providing the fundamental
building blocks for geospatial work in Rust. It defines primitive types such as
Point, LineString, Polygon, and Coord.9 These types will serve as the canonical
representation of POI locations and walking paths throughout the Wildside
application.

High-Performance Spatial Indexing with rstar

To efficiently implement the "Candidate Selection" step, a spatial index is
non-negotiable. The R\*-tree is the ideal data structure for this task, and the
rstar crate is the premier implementation in the Rust ecosystem.10 It is
designed to work seamlessly with the types from the

`geo` crate. The practical implementation will be to build an `rstar::RTree`
during the offline data ingestion phase and load it into memory. When a user
request is received, a call to `rstar`'s rectangle query method will retrieve
all candidate POIs within a bounding box in milliseconds.12

**Recommendation:** Fully adopt the `GeoRust` **ecosystem**. Use `geo` for all
geometric representations and calculations and `rstar` for building the
in-memory spatial index of POIs for fast and efficient candidate selection.

## Section 2: Implementing the POI Scoring and Personalization Algorithm

This section translates the abstract scoring formula,
Score(POI)=wp​⋅P(POI)+wu​⋅U(POI,user_profile), into a concrete implementation
plan. This logic will be encapsulated within a dedicated `Scorer` component,
leveraging the data artefacts produced by the pipeline in Section 1.

### 2.1. Calculating Global Popularity `P(POI)`

The global popularity score, `P(POI)`, serves as a proxy for a POI's general,
objective importance. As this score is static and user-independent, it should
be computed for all relevant POIs during the offline data ingestion phase. The
output will be a compact binary file (e.g., `popularity.bin`), essentially an
array of `f32` scores keyed by an internal POI ID, which can be loaded
efficiently at runtime.

The implementation steps within the ETL pipeline are as follows:

1. After the Wikidata dump has been parsed and loaded into the local SQLite
   database, a process will iterate through each POI that has a `wikidata=*`
   tag.

2. For each POI, a series of queries will be executed against the local
   database to gather popularity metrics:

   - **Sitelink Count:** A query to count the number of sitelinks (links to
     Wikipedia articles in different languages).

   - **UNESCO World Heritage Status:** A check for the existence of a claim
     with property `P1435` (heritage designation) and value `Q9259` (UNESCO
     World Heritage Site).

3. These individual metrics are then normalized and combined using a weighted
   formula to produce a single floating-point `global_popularity_score`, which
   is then saved to the `popularity.bin` artefact.

The implemented scorer lives in the `wildside-scorer` crate. It resolves
sitelink counts from an optional `wikidata_entity_sitelinks` table, falling
back to `sitelinks` or `sitelink_count` tag entries and defaulting to zero when
no data exists. UNESCO heritage designations add a `25.0` bonus on top of the
`1.0` sitelink weight, and raw values are normalized against the run maximum
before serialization. The resulting `HashMap<u64, f32>` is persisted to
`popularity.bin` using `bincode`, providing a deterministic artefact for
request-time scoring.

### 2.2. Calculating User Relevance `U(POI, user_profile)`

The user relevance score, `U(POI, user\_profile)`, is where true
personalization occurs. This score is dynamic and must be calculated at request
time for the subset of candidate POIs retrieved from the R\*-tree spatial index.

The implementation steps at request time are as follows:

1. The application will contain a predefined, configurable mapping from
   high-level "Interest Themes" (e.g., "Modern Architecture," "Street Art") to
   specific Wikidata property-value pairs.

2. After retrieving the candidate POIs for the user's location from the
   R\*-tree, the system iterates through each one.

3. For each candidate `PointOfInterest`, the scorer performs a series of fast
   lookups against the local Wikidata database (e.g., `pois.db`) based on the
   user's active themes. For each theme that matches, a corresponding weight is
   added to the POI's temporary `user_relevance_score`.

4. Finally, the total `Score(POI)` for that request is calculated by combining
   the pre-computed `P(POI)` (loaded from `popularity.bin`) and the
   just-in-time `U(POI)` using the specified weights: wp​ and wu​.

The architectural decision to use offline, read-only data artefacts is the key
technical enabler for this entire personalization feature. Performing thousands
of property checks as indexed queries against a local database can be
accomplished in milliseconds, ensuring a responsive user experience.

**Implementation notes (Dec 2025):**

- The runtime scorer (`UserRelevanceScorer`) loads `popularity.bin` alongside a
  read-only `pois.db` connection. It queries the indexed `poi_wikidata_claims`
  view with prepared statements to keep per-POI lookups fast and predictable.
- Theme matching is declarative. A `ThemeClaimMapping` maps each `Theme` to
  one or more Wikidata `(property_id, value_entity_id)` pairs. The default
  mapping treats `Theme::History` as a proxy for UNESCO heritage status
  (`P1435 = Q9259`), with additional themes added by callers as the ETL
  surfaces richer claims.
- Per-request relevance sums the profile weights for matching themes and
  clamps the result to `0.0..=1.0`. Combining popularity and relevance uses a
  weighted mean (default 50/50). The user weight is only applied when at least
  one theme matches, so POIs without profile matches are not penalized.

## Section 3: A Library-First Architecture for the Wildside Engine

The previous analysis focused on the "what" of the data pipeline; this section
defines the "how" of the application structure. To ensure maintainability,
testability, and reusability, the engine will not be a monolithic service.
Instead, it will be engineered as a **versioned Rust library composed of
several distinct crates**, with a minimal command-line interface (CLI) for
offline tasks.

### 3.1. The Core Principle: Engine as a Library

Instead of embedding the core logic directly within a web application, the
engine will be developed as a standalone set of crates with a stable, semantic
API. The web application (e.g., an Actix or Axum server) becomes a thin client
that depends on the engine library. This approach avoids a tangle of internal
modules and promotes a clean separation of concerns. It also defers the
complexities of a network service architecture (latency, auth, retries, new
failure modes) until they are truly needed, allowing the initial focus to be on
the core logic.

### 3.2. Proposed Crate Layout and Separation of Concerns

A Rust workspace will be used to manage the engine's components, enforcing
clear boundaries while allowing for atomic changes across crates when necessary.

- `wildside-core`: This crate is the heart of the engine. It contains
  the pure domain model and traits, with no I/O or specific framework
  dependencies (i.e., it is `#![no_std]` compatible where possible, without
  `tokio` or `actix`).

  - **Types:** `PointOfInterest`, `InterestProfile`, `Score`, `Route`,
    `Instruction`.

  - **Traits:** Defines the core abstractions: `PoiStore` (for read-only POI
    data access), `TravelTimeProvider` (for distance calculations), `Scorer`,
    and `Solver`.

  - This crate is deterministic and side-effect free, making it easy to test
    rigorously with property-based testing and fuzzing.

- `wildside-data`: Contains the ETL logic and data adapters.

  - Implements the `PoiStore` trait from the core crate.

  - Handles OSM PBF ingestion (using `osmpbf`), Wikidata dump parsing, and
    building the data artefacts (e.g., SQLite/RocksDB stores and the `rstar`
    index).

- (Planned) `wildside-scorer`: Implements the `Scorer` trait.

  - Contains the logic for both the offline pre-computation of global
    popularity scores and the per-request calculation of user relevance.

- (Planned) `wildside-solver-vrp`: The default, native Rust implementation of
  the `Solver` trait, using the `vrp-core` library.

- (Planned) `wildside-solver-ortools`: An optional implementation of the
  `Solver` trait, using bindings to Google's CP-SAT solver. This would be
  enabled via a feature flag for users who require its specific performance
  characteristics and are willing to manage the C++ dependency.

- `wildside-cli`: A small command-line application for operational tasks.

  - `ingest`: Runs the full ETL pipeline from `wildside-data` to build
    the necessary data artefacts.

    The command now uses `ortho-config` to fan in configuration sources. The
    `--osm-pbf` and `--wikidata-dump` flags map to
    `WILDSIDE_CMDS_INGEST_OSM_PBF` and `WILDSIDE_CMDS_INGEST_WIKIDATA_DUMP`
    environment variables, so CI pipelines and operators can set defaults once
    rather than repeating long paths. Until the downstream orchestration hooks
    are implemented, the CLI validates that both files exist and surfaces clear
    `MissingArgument` or `MissingSourceFile` errors. This keeps the UX stable
    while further pipeline work completes.

  - (Planned) `score`: Triggers the batch computation of global popularity
    scores.

  - (Planned) `solve`: A utility to run the solver from the command line by
    feeding it a JSON request, which is invaluable for performance testing and
    offline debugging.

### 3.3. A Stable, Performant, and Boring API Surface

The public API of the engine should be simple, stable, and predictable. The
primary interaction will be through a request/response model.

**Input Structure:**

```rust
pub struct SolveRequest {
    pub start: geo::Coord,      // f64 lat/lon
    pub duration_minutes: u16,  // Tmax
    pub interests: InterestProfile,
    pub seed: u64,              // For deterministic, reproducible heuristic runs
    pub max_nodes: Option<u16>, // Optional pruning hint for candidate search
}
```

**Output Structure:**

```rust
pub struct SolveResponse {
    pub route: Route,           // Ordered list of coords + POI IDs
    pub score: f32,             // Total collected score for the route
}
```

The inclusion of a `seed` in the request is critical for reliability. It makes
the heuristic solver's output reproducible, which is invaluable for testing,
benchmarking, and diagnosing production incidents. A lightweight
`SolveRequest::validate` helper enforces the core invariant: a duration of zero
minutes is rejected with `SolveError::InvalidRequest`. The optional `max_nodes`
pruning hint must be greater than zero when supplied; `None` leaves solver
implementations free to choose candidate limits. A `Diagnostics`
payload will be added once the telemetry schema is agreed.

### 3.4. Data and Computation Boundaries: Offline vs. Online

A strict separation between offline preparation and online serving is essential
for performance and scalability.

- **Offline Path:** The `wildside-cli` is used to execute the idempotent
  ETL process. This process takes raw OSM and Wikidata data and produces a set
  of optimized, read-only artefacts:

  - `pois.db`: An SQLite (or RocksDB) file containing the enriched POI data,
    indexed for fast lookups. Schema changes are backward compatible within a
    major release.

  - `pois.rstar`: A serialized R\*-tree file for fast spatial queries, which
    can be loaded into memory using memory-mapping (e.g., with `memmap2`) for
    near-instant startup. The layout remains stable across 0.x releases.

  - `popularity.bin`: A compact binary file of pre-calculated global
    popularity scores. The structure remains stable across 0.x releases; bump
    the artefact header version per §3.4.1 when making breaking changes.

The `wildside` CLI now wires these stages together: the `ingest` command
validates input paths, streams the PBF to derive POIs, writes `pois.db`
(creating parent directories when required), extracts linked claims from plain
JSON or `.bz2` Wikidata dumps, and serializes the R\*-tree to `pois.rstar`.
When no POIs carry a `wikidata` tag, the ETL is skipped but the claims schema
is still initialized to keep artefact shapes stable. Output paths default to
the current working directory and can be overridden via `--output-dir`.
Filesystem access during these steps relies on `cap-std`'s `fs_utf8` module and
`camino` paths to ensure UTF-8-safe handling and to keep the pipeline ready for
capability-based sandboxing.

#### 3.4.1. Artefact versioning and migration

Embed a fixed header: 4-byte ASCII magic "WSPI", u16 major, u16 minor, u8
flags, all little-endian. Bump MAJOR for incompatible changes; bump MINOR for
backward-compatible additions. Readers MUST refuse unknown MAJOR versions and
MAY accept newer MINOR versions. Provide a `wildside-cli migrate` subcommand
that detects legacy headers, runs the appropriate migrator, and emits a clear
error with expected vs found MAJOR.MINOR on mismatch.

- **Online Path:** The core engine library, when used by the web app, interacts
  *only* with these read-only artefacts. This design choice means the engine
  itself is side-effect free during a request. It allows application instances
  to be scaled horizontally without needing complex shared state or writeable
  database connections, dramatically simplifying deployment and improving
  robustness.

## Section 4: The Solver Implementation - Abstracted and Replaceable

This section addresses the most computationally intensive component of the
Wildside engine: solving the Orienteering Problem (OP). The library-first
architecture allows us to abstract the solver behind a trait, making the
specific implementation a configurable choice.

### 4.1. The `Solver` Trait: A Common Interface

The `wildside-core` crate will define a `Solver` trait. This trait will have a
single primary method,
`solve(request: &SolveRequest) -> Result<SolveResponse, SolveError>`, which
encapsulates the entire process of finding an optimal route. The trait is
object-safe and keeps the solver synchronous for embeddability. Implementations
must be `Send + Sync` so solvers can run on threaded callers. They should call
`request.validate()` (or enforce the same invariants) so that zero-duration
requests and non-finite start coordinates yield `SolveError::InvalidRequest`.
Implementations must avoid shared mutable state or use proper synchronisation
to maintain thread safety. This abstraction is the key to making the engine
flexible and future-proof.

### 4.2. Recommended Native Rust Solution with `vrp-core`

For the initial implementation, a native Rust solution is strongly recommended.
The `reinterpretcat/vrp` project, specifically the `vrp-core` crate, is a
mature, well-documented, and permissively licensed (Apache-2.0) library for
solving rich Vehicle Routing Problems.13 The primary benefits of this approach
are a seamless development experience, guaranteed memory safety for the core
logic, and a simplified build process managed entirely by Cargo.

The Orienteering Problem is a known "routing problem with profits," which can
be modeled directly using the `vrp-core` API. The solver's objective function
will be configured to maximize the total collected `Score(POI)` from visited
"jobs" (the POIs), subject to the user's time budget (Tmax​). The library's
powerful built-in metaheuristics will efficiently find a high-quality route
within the required few-second timeframe.15 This implementation will live in the

`wildside-solver-vrp` crate.

### 4.3. Optional High-Performance Backend: `wildside-solver-ortools`

To allow for future performance comparisons or to meet extreme optimization
requirements, a second implementation of the `Solver` trait can be provided in
the `wildside-solver-ortools` crate. This would use bindings to Google's highly
optimized CP-SAT solver, such as the `cp_sat` crate.

This approach offers potentially world-class performance but comes at the cost
of significant build and deployment complexity, requiring a C++ compiler and a
system-level installation of the OR-Tools library.16 By placing this
implementation behind a feature flag, consumers of the Wildside engine can
choose to opt into this complexity only if they absolutely need it, without
burdening the default setup.

### 4.4. The `TravelTimeProvider` boundary

A critical prerequisite for any VRP solver is the travel time matrix. The
solver itself is an abstract mathematical engine; it requires an external
component to provide the walking time between every pair of candidate POIs.

This is handled by the synchronous `TravelTimeProvider` trait defined in
`wildside-core`. The trait has the signature:
<!-- markdownlint-disable-next-line MD013 -->
`fn get_travel_time_matrix(&self, pois: &[PointOfInterest]) -> Result<TravelTimeMatrix, TravelTimeError>`.
 Keeping the solver synchronous preserves object safety and makes the core
embeddable.

The recommended implementation will be an adapter that makes API calls to an
external, open-source routing engine like OSRM or Valhalla, running as a
separate microservice. This adapter will use `tokio` and an HTTP client like
`reqwest` to perform these network calls. This design keeps the core library
free of a specific async runtime, making it more broadly embeddable, while
still allowing it to communicate with the necessary external services.

## Section 5: Implementation, Testing, and Deployment Strategy

This final section consolidates the architectural decisions into an actionable
plan covering packaging, testing, and versioning.

### 5.1. Packaging, Versioning, and Features

The engine will be structured for robust dependency management and deployment.

- **Licensing:** All engine crates (`wildside-*`) will be licensed under
  the permissive **ISC license**, satisfying the project's legal requirements
  while being clear and concise.

- **Versioning:** Each crate within the workspace will be independently
  versioned using Semantic Versioning. This allows for stable, predictable
  updates for consumers of the library. A `CHANGELOG.md` file will be
  maintained from the start.

- **Feature Flags:** The engine will make judicious use of feature flags to
  allow consumers to select only the components they need.

  - `default-features = ["solver-vrp", "sqlite-store"]`: The default build will
    include the native Rust solver and the SQLite backend for data storage.

  - Optional features will include `ortools` (to enable the OR-Tools solver),
    `rocksdb` (to enable the RocksDB backend), `serde-bincode` (for potentially
    faster zero-copy caching), and `wasm` (to enable future compilation for
    on-device scoring).

### 5.2. A Non-Negotiable Testing and Benchmarking Discipline

For a library intended to be a reliable core component, a rigorous testing
discipline is non-negotiable.

- **Golden Tests:** Small graph instances (5–20 POIs) with hand-verified or
  exhaustively calculated optimal solutions will be used to validate the
  correctness of the solver's output.

- **Property Tests:** The `proptest` crate will be used to assert invariants
  that must always hold true (e.g., a route must always start and end at the
  depot, the total time must never exceed the budget, no duplicate POIs are
  visited).

- **Performance Benchmarks:** The `criterion` crate will be used to create a
  performance suite that runs on CI. This will track the p95 wall-time for
  solving problems of various sizes (e.g., N=50, 100, 200 candidates) to
  prevent performance regressions.

- **Fuzz Testing:** The scorer's theme mapping and the data ingestion logic's
  handling of Wikidata claims will be fuzzed to ensure resilience against
  unexpected or malformed data.

- **API Contract Tests:** The serialization format of `SolveRequest` and
  `SolveResponse` will be tested to ensure the "wire schema" remains stable
  across versions, preventing breaking changes for consumers.

### 5.3. Repository and Migration Strategy

The project will begin in a single Git repository configured as a Cargo
workspace. This simplifies initial development while still enforcing the clean
separation between crates. If the engine later needs to be consumed by external
partners, support non-Rust bindings (e.g., Python via PyO3), or adopt a
different release cadence from the main application, it can be promoted to its
own repository with no code churn, as the boundaries are already established.

The migration from an initial "engine-in-app" prototype to the final library
structure follows a clear path:

1. Extract all domain types into `wildside-core` and update the
   application to import from the new crate.

2. Move scoring and solver logic into the `wildside-scorer` and
   `wildside-solver-vrp` crates, implementing the traits from `core`. The
   application code becomes a thin adapter.

3. Introduce the `wildside-cli` and integrate it into the CI pipeline
   for repeatable data ingestion and performance snapshots.

4. Change the application's dependency on the engine crates from a local
   path-based dependency to a versioned one, potentially using a private crate
   registry or Git tags.

This structured approach provides immense benefits in cohesion, replaceability
of components (like the solver), reusability for other applications or
bindings, and overall reliability.

## **Works cited**

1. Rust (programming language) - Wikipedia, accessed on August 13, 2025,
   <https://en.wikipedia.org/wiki/Rust\_(programming_language)>

2. osmpbf - [crates.io](http://crates.io): Rust Package Registry, accessed on
   August 13, 2025, <https://crates.io/crates/osmpbf>

3. osmpbfreader - [crates.io](http://crates.io): Rust Package Registry,
   accessed on August 13, 2025, <https://crates.io/crates/osmpbfreader>

4. osm - Keywords - [crates.io](http://crates.io): Rust Package Registry,
   accessed on August 13, 2025, <https://crates.io/keywords/osm>

5. reinterpretcat/vrp: A Vehicle Routing Problem solver - GitHub, accessed on
   August 13, 2025, <https://github.com/reinterpretcat/vrp>

6. CP-SAT Solver | OR-Tools - Google for Developers, accessed on August 13,
   2025, <https://developers.google.com/optimization/cp/cp_solver>

7. cp_sat - Rust - [Docs.rs](http://Docs.rs), accessed on August 13, 2025,
   <https://docs.rs/cp_sat>

8. How to learn to use rust system binding API crates? - Reddit, accessed on
   August 13, 2025,
   <https://www.reddit.com/r/rust/comments/1hcjhbx/how_to_learn_to_use_rust_system_binding_api_crates/>

9. wikidata - Rust - [Docs.rs](http://Docs.rs), accessed on August 13, 2025,
   <https://docs.rs/wikidata>

10. Geospatial - Categories - [crates.io](http://crates.io): Rust Package
    Registry, accessed on August 13, 2025,
    <https://crates.io/categories/science::geo>

11. vrp_core - Rust - [Docs.rs](http://Docs.rs), accessed on August 13, 2025,
    <https://docs.rs/vrp-core/latest/vrp_core/>

12. Orienteering Problems: Models and Algorithms for Vehicle Routing Problems
    with Profits, accessed on August 13, 2025,
    <https://www.researchgate.net/publication/335520693_Orienteering_Problems_Models_and_Algorithms_for_Vehicle_Routing_Problems_with_Profits>

13. vrp_core - Rust - [Docs.rs](http://Docs.rs), accessed on August 13, 2025,
    <https://docs.rs/vrp-core>

14. VRP - Vehicle Routing Problem; ChatGPT-augmented - Stock/Inventory - Frappe
    Forum, accessed on August 13, 2025,
    <https://discuss.frappe.io/t/vrp-vehicle-routing-problem-chatgpt-augmented/105143>

15. Solving vehicle routing problem in Java - SoftwareMill, accessed on August
    13, 2025,
    <https://softwaremill.com/solving-vehicle-routing-problem-in-java/>

CP-SAT — Rust math library // [Lib.rs](http://Lib.rs), accessed on August 13,
2025, <https://lib.rs/crates/cp_sat>
