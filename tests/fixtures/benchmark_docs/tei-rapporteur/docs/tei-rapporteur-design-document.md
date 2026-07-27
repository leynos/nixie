# TEI-Rapporteur design document

## Introduction

**TEI-Rapporteur** is a Rust-based library providing a canonical data model,
parser, and emitter for a constrained subset of **TEI P5** (Text Encoding
Initiative) XML, tailored to podcast-related use cases. It serves as the
structured "content spine" for a podcast scripting ecosystem, supporting tasks
such as episode authoring (code-named *Episodic*), cliché detection
(*Bromide*), pronunciation QA (*Chiltern*), and accent drift detection
(*Ascorbic*). The library is implemented in Rust for performance and
correctness, with a Python interface built via PyO3. Python users interact with
TEI data through high-level `msgspec.Struct` classes (from the msgspec library)
rather than low-level PyO3 objects. This design keeps Rust as the single source
of truth for parsing/validation while allowing Python developers to work with
familiar dataclass-like objects.

The guiding principle is that TEI XML is the **canonical data model**, and all
other representations (JSON, MessagePack, Python classes, etc.) are projections
or views. Rust “owns” the strong types and enforces validity, whereas Python
serves as an ergonomic scripting layer. At the FFI boundary, Python
`msgspec.Struct` instances are serialized to bytes (JSON or MessagePack) and
passed into Rust; Rust returns validated data back as bytes, which Python code
decodes into `msgspec.Struct` objects. This ensures that no parsing or schema
validation logic is duplicated in Python – the Python side is essentially
stateless with respect to TEI structure. By centralizing all XML handling in
Rust, the design achieves a **deterministic, single-source-of-truth**
conversion: for any given TEI input, Rust produces a canonical normalized
output, and the semantic content remains consistent across round-trips.

## Python projection decisions (November 2025)

- The Python packaging metadata and test bootstrap now share the same
  `msgspec` constraint (`>=0.19,<0.20`) to prevent environment drift between
  Continuous Integration (CI), local wheels, and the embedded interpreter.
- Python projections live in `tei_rapporteur.structs` and now describe body
  and inline unions explicitly (`BodyBlock` as
  `ParagraphBlock | UtteranceBlock`, `Inline` as `str | Hi | Pause`, with
  `Pause` expressed as a `TypedDict` carrying `@dur` and `@type`).
- The `ensure_msgspec_installed` test helper is guarded by a Python-aware
  initializer (`GILOnceCell` / `OnceExt::call_once_py_attached`) so `pip`
  installation runs exactly once across parallel tests while keeping the GIL
  interactions safe.

### Python projection decisions (December 2025)

- Python-facing payloads are now **internally tagged** so `msgspec` can decode
  fully typed structures without resorting to `Any`. Inline content is a tagged
  union on the `type` field (`text`, `hi`, `pause`) with pause metadata renamed
  to `dur` and `kind` to avoid discriminator collisions.
- Body blocks are likewise tagged (`paragraph`, `utterance`) and unwrapped
  (fields live alongside the discriminator), enabling streaming events to share
  the same shapes.
- A dedicated projection layer in `tei-py` translates between the canonical
  Rust TEI model and the tagged Python representation, isolating the breaking
  change to the FFI boundary while keeping the XML/serde shapes unchanged.

This document outlines the design of the `tei-rapporteur` library, including
the TEI P5 subset definition, Rust data model and crate architecture,
parsing/emitting strategy, Python integration layer, a proposed streaming
parser extension, and considerations for validation and performance. Example
Rust and Python code are provided to illustrate usage, along with an
architectural overview and a comparison of serialization pathways between Rust
and Python. Throughout, the design focuses on writing clear, maintainable code
– **Rust remains idiomatic and free of Python-specific cruft, and Python APIs
feel natural to Python users**.

## Workspace scaffolding decisions

The workspace now follows the layout described in `docs/workspace-layout.md`.
Four crates ship with the repository:

- `tei-core` defines early placeholder types that exercise crate boundaries.
  The new `DocumentTitle` newtype rejects empty titles at construction time and
  surfaces an idiomatic `thiserror` error enum so that future data-model code
  can build on consistent validation primitives.
- `tei-xml` depends on `tei-core` and offers a `serialize_document_title`
  helper. The function demonstrates how XML-facing crates will transform the
  core types while keeping validation in the domain layer.
- `tei-py` depends on both crates and forwards the serialization helper so that
  PyO3 integration work inherits the existing validation logic.
- `tei-test-helpers` exposes shared assertion utilities so that the XML and
  Python crates can reuse markup expectations without duplicating boilerplate.

The workspace manifest centralizes metadata, lint rules, and shared dependency
versions. Each crate opts into the shared configuration via `workspace = true`
stanzas, ensuring that future crates inherit the same guard rails. Behavioural
coverage for the scaffolding relies on `rstest-bdd`, providing both a happy
path scenario (serializing a valid title) and a failure scenario (rejecting an
empty title). These scenarios will evolve into richer contract tests as the
feature set grows.

## TEI P5 subset for podcast use cases

**TEI P5** is a comprehensive and extensible XML schema for encoding texts, but
using all of TEI would be overkill. The design defines a **profiled subset of
TEI P5** (informally, *TEI Episodic Profile*) that includes only the elements
and attributes needed for the intended podcasting scenarios. This focused
subset makes the data model simpler to implement and guarantees round-trip
fidelity for the features actually used in production workflows.

Key inclusions in the TEI subset (to be formally specified via an ODD
customization) are:

- **TEI Header**: Each document begins with a `<teiHeader>`, containing:

- `<fileDesc>` with bibliographic metadata (title of episode, series info,
  etc.).

- `<profileDesc>` with information like cast/speakers, languages or dialects
  used (for accent analysis), and perhaps a description of content (could
  include an index of segments or word count).

- `<encodingDesc>` to document the usage of certain annotations (e.g. a
  controlled vocabulary for cliché types or accent labels).

- `<revisionDesc>` with provenance: e.g. timestamps or versions for when the
  script was generated or edited, tool versions (Bromide, Chiltern, etc.), and
  content hashes for reproducibility.

- **Body Structure (Dialogue and Narrative)**: The main content of the episode
  script is in `<text><body>` with a structure for spoken dialogue and
  narration:

- For a **single-host or conversational script**, the model uses TEI’s spoken
  text or drama notation. Two possible approaches were considered:

- *Spoken Transcripts Module*: Use `<u>` elements (utterances) for each speaker
  turn.[^1] Each `<u>` may have a `who` attribute pointing to a speaker
  identifier (defined in the header’s cast list). Within `<u>`, the transcribed
  speech is encoded, possibly with inline elements for emphasis, pauses, etc.

- *Drama Module*: Use `<sp>` elements for speech, containing a `<speaker>`
  child for the speaker’s name and the spoken text in one or more `<p>`
  paragraphs. This approach is more verbose but explicitly tags the speaker
  name in the text flow.

- Initial support targets one approach (using `<u>` for simplicity), but the
  data model is flexible enough to extend to the other. In either case, the
  content of an utterance can include **mixed content**: plain text
  interspersed with inline tags like `<hi>` (for emphasis or styled text),
  `<seg>` (generic segment, e.g. marking a phrase), `<pause>` elements for
  pauses, or others as needed. Handling mixed content is a crucial aspect and
  is discussed in the data model design below.

- **Stand-off Annotations**: Analytical modules like Bromide will add
  annotations without altering the original text. The approach leverages TEI’s
  `<standOff>` section to hold annotations as spans that reference portions of
  the primary text. For example, Bromide could output:

- A `<spanGrp type="cliche">` grouping all detected clichés, with an attribute
  `resp="#bromide"` pointing to the responsible tool and maybe `corresp`
  linking to the overall text or episode ID.

- Inside, each `<span>` marks a clichéd phrase with attributes like `from` and
  `to` pointing to the start and end points in the text (using TEI’s canonical
  referencing scheme, e.g. pointers into utterances by character offset) and an
  `ana` (analysis) code indicating the type of cliché. The `xml:id` of the
  `<span>` uniquely identifies the annotation. This stand-off markup means the
  base script text remains intact, and multiple annotation layers (clichés,
  pronunciation issues, accent shifts, etc.) can coexist.

- Similar usage is anticipated for *Chiltern* (pronunciation QA) and *Ascorbic*
  (accent drift). For instance, Chiltern might identify words with
  pronunciation questions and mark them with `<spanGrp type="pronunciation">`
  annotations, or insert a TEI `<pron>` element if inline representation is
  needed.[^2] Ascorbic might mark segments of an utterance where a speaker’s
  accent appears to shift, using a `<spanGrp type="accent">` with spans
  covering the relevant time or text range and an `ana` value indicating the
  detected accent. These specifics will be refined as those tools develop, but
  the TEI framework can accommodate them via stand-off markup or inline tags
  (e.g., a custom `<distinctPron>` tag defined in the ODD if needed).

- All annotation types will be documented in the TEI header’s `<encodingDesc>`
  so that the semantics of `@ana` codes and custom element usage are clear and
  versioned.

By constraining the implementation to this profile (let’s call it **TEI P5
Episodic-Core**), the design avoids the complexity of the entire TEI schema.
This subset will be rigorously documented and accompanied by a Relax NG schema
and Schematron rules generated from the ODD. That approach provides a contract
for what constitutes a valid Episodic TEI document. Any TEI features outside
this profile (unknown elements or attributes) are either dropped or preserved
in a generic way (e.g., as untyped `extra` fields) so that the parser doesn’t
break on unexpected input. The goal is to **accept and preserve all relevant
information** for the intended use cases, while being forward-compatible with
minor extensions. If an input uses a TEI construct that has not yet been
modeled, the design should either ignore it safely or store it in a placeholder
structure for round-trip output, rather than erroring out.

## Architecture Overview

The `tei-rapporteur` project is organized into multiple Rust crates within a
single workspace, each with a focused responsibility. This modular design aids
clarity and allows reuse or independent testing of components:

- **`tei-core`**: A Rust library crate containing the core TEI data model
  (structs and enums representing TEI elements), business logic (e.g., any
  invariant enforcement or transformation functions), and basic validation
  routines. This crate has no dependency on Python or PyO3 – it is pure Rust
  (potentially even `no_std` compatible for portability). Rust developers can
  use `tei-core` directly to parse, manipulate, and emit TEI in Rust
  applications without any Python involvement.

- **`tei-xml`**: The XML parsing and writing logic built on a high-performance
  XML library (the implementation uses `quick-xml`). This crate provides
  functions to parse TEI XML into `tei-core` data structures and to serialize
  `tei-core` structures back to XML. It may also include streaming parse
  utilities (pull parser) and pretty-printing or canonicalization logic. In
  practice, `tei-xml` might be merged with `tei-core` if tightly coupled, but
  conceptually it’s a separate concern (XML I/O). This separation can help if
  later iterations support alternative input formats (e.g., if someone wanted
  to import a Markdown transcript and produce TEI).

- **`tei-serde`**: A crate providing serde serializers/deserializers for
  converting the TEI data structures to/from other formats like JSON or
  MessagePack. The crate centralizes the `serde_json` and `rmp-serde`
  dependencies and exposes small wrapper modules (`tei_serde::json`,
  `tei_serde::msgpack`) for the rest of the workspace. This keeps
  serialization-format concerns isolated from the core domain model and from
  the PyO3 glue. For convenience, `tei-serde` may re-export the underlying
  crates to support internal test fixtures, but workspace code should prefer
  the wrapper modules so dependency boundaries stay explicit. Versioned JSON
  schema snapshots can also be included here for integration testing and for
  documentation of the JSON structure.

- **`tei-ann`**: (Optional/future) A helper crate for working with annotations
  (spans). It could provide utilities to apply stand-off `<span>` annotations
  to a text, or to extract and query them. For example, Bromide’s output might
  use this to insert cliché annotations or to merge overlapping spans. This is
  not critical for the basic parser/emitter functionality but part of the
  broader toolkit.

- **`tei-py`**: A PyO3-based Python extension module (compiled to a native
  `.pyd/.so` library) that exposes a Python API. This is the bridge between
  Python and the Rust core. It defines the Python-callable functions and
  classes, marshalling data to and from Rust. **Importantly, `tei-py` is a thin
  layer**: it should primarily accept Python byte arrays or simple types,
  delegate to Rust for actual work, and then return results back to Python
  (often as bytes). The design avoids exposing complex Rust structures directly
  through PyO3, which keeps the Rust API idiomatic and prevents Python-specific
  concepts from leaking into `tei-core`. Rust developers working on `tei-core`
  do not need to even be aware of the Python wrapper if they don’t use it.

All crates are versioned together in a single repository (monorepo), ensuring
that changes to `tei-core` stay in sync with `tei-py` and others. The release
plan is to distribute `tei-core` on crates.io for Rust use, and `tei-py` as a
Python wheel (via maturin) for easy pip installation. This approach allows
Rust-only projects and Python projects to consume the library through their
natural channels.

**Crate Dependency and Feature Flags**: To maintain a separation of concerns,
the core crates do not depend on PyO3. If needed, the architecture can create
an intermediate crate or feature for conversion glue:

- One option is to introduce a `tei-bridge` crate (or a feature in `tei-core`),
  which when enabled pulls in `pyo3` and implements traits like `FromPyObject`
  and `ToPyObject` for the core types. This would allow directly accepting
  Python `dict` or `list` structures in Rust functions via serde, using
  `pyo3-serde` under the hood. This approach is purely optional and kept behind
  a feature flag (e.g., a `python` feature) so that by default `tei-core`
  remains free of any Python code.

- Whether the implementation uses a separate `tei-bridge` crate or simply puts
  these behind a feature, the Python extension (`tei-py`) will enable that and
  thus gain the ability to easily convert Python objects. However, an even
  simpler strategy (described below) is to avoid passing rich Python objects at
  all and stick to bytes or basic types across the FFI. That reduces the need
  for a `tei-bridge` layer.

The overall architecture ensures a **clear API boundary** between Rust and
Python. Rust code is written as if it’s a standalone library (no mention of GIL,
`PyObject`, or Python-specific patterns in its public API). Python code sees a
natural interface (functions and dataclasses) and does not worry about Rust
internals. The contract at the boundary is simply serialized data. This design
avoids the “snakes in the Rust code” problem – Rust developers are not
confronted with a tangle of embedded Python types. They can contribute to
`tei-core` without knowing anything about PyO3 or Python, which is important
for long-term maintainability.

Below is a high-level diagram of the system components and data flow:

```plaintext
  [ TEI XML file ] 
         ↓ parse (Rust, quick-xml)
  ┏━━━━━━━━━━━━━━┓           FFI (bytes)           ┏━━━━━━━━━━━━━━━┓
  ┃   tei-core   ┃ ------------------------------> ┃    tei-py     ┃
  ┃  (Rust lib)  ┃ <-----------------------------> ┃ (Python ext)  ┃
  ┗━━━━━━━━━━━━━━┛       Rust structs (serde)      ┗━━━━━━━━━━━━━━━┛
         ↑                   and bytes                     ↑         
         |                (JSON/MsgPack)                   | Python API (msgspec)
         |                                                 | (e.g., Episode Struct)
         |                                                 |         
         |            ┏━━━━━━━━━━━━━━┓                     |
         |            ┃   tei-serde  ┃  JSON/MsgPack bytes |
         |            ┗━━━━━━━━━━━━━━┛  (via msgspec)      |
         |                                                 |
         |            ┏━━━━━━━━━━━━━━┓                     |
         |            ┃   tei-xml    ┃  XML string         |
         |            ┗━━━━━━━━━━━━━━┛  (quick-xml)        ↓         
[ TEI XML output ]                                      Python
                                                      application
```

*Figure: Architectural overview of **tei-rapporteur**. The `tei-core` library
in Rust manages the TEI data model and logic. `tei-xml` parses and emits TEI
XML using quick-xml. The `tei-serde` layer (if used) handles JSON/MsgPack
serialization of Rust structures. The `tei-py` module uses PyO3 to expose a
Python API, operating by passing serialized data across the FFI boundary.
Python code uses `msgspec.Struct` classes (e.g., an `Episode` class) to
represent TEI content, which are encoded/decoded to binary formats for
communication with Rust.*

## Rust Data Model and Serialization

### Data Model Design

In `tei-core`, the design defines Rust structs and enums to represent the TEI
document structure. The model closely mirrors the TEI XML hierarchy for the
chosen subset, while using Rust’s type system to enforce validity as much as
possible. For example:

- A top-level `TeiDocument` struct represents the `<TEI>` element (root of a
  TEI file). It contains a `tei_header: TeiHeader` and a `text: TeiText` (which
  in turn contains the `<body>` etc.). The model might also include metadata
  like a version number for the model or a provenance field if needed (to track
  which version of the schema or tools produced it).

- `TeiHeader`, `TeiText`, `TeiBody`, etc. are structs for those sections. For
  instance, `TeiHeader` would have fields corresponding to the allowed child
  elements (fileDesc, profileDesc, encodingDesc, revisionDesc), each likely
  optional except fileDesc. A `TeiBody` might contain a list of top-level
  divisions or utterances. In a simple podcast script, the body could be just a
  sequence of paragraphs/utterances without further nesting, or the design
  could allow a two-level hierarchy (e.g., `<div>` for sections like "Intro",
  "Interview", "Outro", each containing paragraphs or utterances).

The initial implementation lands the document shell described above:

- `TeiDocument` now owns a `TeiHeader` plus a placeholder `TeiText`. The text
  structure captures an ordered list of string segments for now; later phases
  will replace this with the richer body and inline models discussed below.
- `FileDesc` wraps the validated `DocumentTitle` and normalizes optional series
  and synopsis strings, trimming whitespace and discarding blanks. This keeps
  metadata consistent regardless of how callers provide it.
- `ProfileDesc` maintains trimmed speaker names and language tags. Attempts to
  add empty values return a `HeaderValidationError::EmptyField`, mirroring the
  strict validation approach used for titles.
- `EncodingDesc` records `AnnotationSystem` entries. Each system validates that
  its identifier is non-empty while allowing an optional free-text description.
- `RevisionDesc` stores a list of `RevisionChange` values. Changes require a
  non-empty description and optionally track responsibility strings, again
  reusing `HeaderValidationError::EmptyField` for invalid input.

The text module now models the `<text><body>` hierarchy instead of relying on
placeholder segments:

- `TeiText` owns a `TeiBody`, and `TeiBody` keeps a `Vec<BodyBlock>` so the
  order of paragraphs and utterances remains faithful to the source script.
- `BodyBlock` is an enum with `Paragraph(P)` and `Utterance(Utterance)`
  variants. This provides a single ordered surface today while leaving room for
  future variants such as divisions.
- `P` and `Utterance` wrap a `Vec<Inline>` so plain text, emphasized spans, and
  pauses share a single ordered sequence. Both structs expose helper methods
  for attaching optional `xml:id` values and, in the case of `Utterance`, a
  speaker reference.
- Input validation moved into a dedicated `BodyContentError` enum. Paragraphs
  and utterances must contain at least one meaningful inline node. Inline text
  nodes with only whitespace are rejected, inline elements such as `<hi>` must
  contain validated children, and identifiers/speaker names are trimmed before
  being recorded. This mirrors the header module’s normalization story and
  keeps downstream consumers safe from accidentally blank content.

Normalization helpers centralize the trimming logic so optional values never
carry unintentional whitespace. This by-construction approach keeps downstream
serializers simple: they do not need to handle purely cosmetic differences in
header metadata.

- For narrative text like paragraphs the implementation uses a struct `P` with a
  content field. For dialogue, if using `<u>`, the model might mirror `P`
  (since an utterance can be treated as a special kind of paragraph with a
  speaker attribute). If using `<sp>`, the implementation would have an `Sp`
  `speaker: String` field and either text content or sub-elements.

- **Mixed Content**: TEI often allows text mixed with inline elements. For
  example, an utterance could contain plain text, interspersed with `<hi>`
  elements for emphasis and `<pause/>` empty elements. To model this, the
  design uses an enum for inline content. E.g.:

```rust
#[derive(Serialize, Deserialize)]
#[serde(untagged)]
pub enum Inline {
    Text(String),
    Hi(Hi),
    Pause(Pause),
}

#[derive(Serialize, Deserialize)]
#[serde(rename = "hi")]
pub struct Hi {
    #[serde(rename = "rend", skip_serializing_if = "Option::is_none", default)]
    rend: Option<String>,
    #[serde(rename = "$value", default)]
    content: Vec<Inline>,
}

#[derive(Default, Serialize, Deserialize)]
#[serde(rename = "pause")]
pub struct Pause {
    #[serde(rename = "dur", skip_serializing_if = "Option::is_none", default)]
    duration: Option<String>,
    #[serde(rename = "type", skip_serializing_if = "Option::is_none", default)]
    kind: Option<String>,
}
```

`P` and `Utterance` both expose a `content: Vec<Inline>` surface. Inline helper
constructors (`Inline::text`, `Inline::hi`, `Inline::pause`) ensure callers can
describe emphasized segments and pause cues without juggling the underlying
structs. The `Hi::try_new` and `Hi::try_with_rend` helpers validate inline
children up-front, and `Hi::push_inline` now returns a `Result` so invalid
nodes are rejected rather than silently recorded. Validation walks the entire
inline tree: empty text nodes and empty `<hi>` elements raise
`BodyContentError`, while `<pause/>` counts as meaningful content even without
surrounding text so scripts can capture timing cues.

String-based constructors now flow through explicit `from_text_segments`
helpers so callers can convert plain text into validated inline nodes without
manually collecting `Inline` variants. The previous `P::new` and
`Utterance::new` wrappers delegate to these helpers and are marked deprecated
to steer new code towards the clearer naming while preserving backwards
compatibility.

To keep serialization ergonomic, every data model struct and enum derives
`Serialize`/`Deserialize`. The derives include `#[serde(transparent)]` on the
newtype wrappers so `quick-xml` and future JSON projections observe canonical
string shapes. Error handling now centralizes conversions via a `TeiError`
enum, allowing higher layers to convert `DocumentTitleError`,
`HeaderValidationError`, `BodyContentError`, and related cases into a single
type without losing context. Top-level constructors such as
`TeiDocument::from_title_str` and helpers like `serialize_document_title`
propagate `TeiError` directly so downstream callers always interact with the
unified surface.

- **Attributes and Identifiers**: Attributes of TEI elements become struct
  fields, using `serde(rename = "...")` to map to the actual XML attribute
  names. For example, if the XML contains `<u who="s1">...</u>`, the
  corresponding `Utterance` struct might be:

```rust
struct Utterance {
    #[serde(rename="who", default)]
    speaker: Option<String>,
    #[serde(rename="$value", default)]
    content: Vec<Inline>
}
```

The model uses `Option<String>` for the speaker reference (since it might be
omitted if there's only one speaker or if using a different markup). Similarly,
global attributes like `xml:id` can be included. The design includes an
`id: Option<String>` field on any element that can carry an `xml:id`. This
allows references between elements (e.g., a `<span from="#u42:...">`
referencing an utterance with `xml:id="u42"`). Validation ensures that if an
element says `xml:id="X"` then `X` is unique within the document.

- **Preservation of Unknowns**: Because TEI is extensible, the model should not
  choke on unexpected attributes or child elements that might appear if the
  input slightly exceeds the subset. The implementation uses catch-all
  structures to handle these cases:

- For attributes, the implementation can include a field like
  `#[serde(flatten)] extra_attrs: HashMap<String, String>` in each struct to
  collect any attribute that isn’t explicitly modeled. This approach ensures
  that unknown attributes round-trip (they’ll be output again on serialization)
  even if the code doesn’t interpret them.

- For unknown child elements, the strategy is trickier; one approach is to
  treat the content of an element as a list of an `Inline`-like enum that has a
  variant for “UnknownElement” carrying maybe a generic name and a list of
  children. This can get complex, so as a first step the implementation might
  simply ignore/drop children that aren’t recognized (with a warning) or treat
  them as text if possible. Because the input is profiled via ODD, the
  expectation is that users won’t supply out-of-profile elements except via
  forward-compatible extension, which optional updates to the model can handle.

- **Error Handling**: The design defines a central error type `TeiError` (using
  `thiserror` for convenience) to capture parse errors, validation errors,
  etc.. For example, `TeiError::Xml(String)` for an XML syntax or
  well-formedness error (wrapping the message from quick-xml if any), and
  `TeiError::Validation(String)` for semantic issues (like duplicate IDs). This
  error type is used consistently across parse/emit/validate functions.

### XML Parsing and Emitting (serde + quick-xml)

Parsing TEI XML in Rust is done entirely in the `tei-xml` layer using
**quick-xml** with Serde serialization. Quick-xml is a speedy pull-parser that
also provides Serde integration: the code simply derives `serde::Deserialize`
on the structs and then calls
`quick_xml::de::from_str::<TeiDocument>(&xml_string)` to get a `TeiDocument` in
memory. Similarly, `Serialize` is derived and `quick_xml::se::to_string(&doc)`
is used to generate an XML string. This approach yields a **semantic
round-trip**: as long as the input adheres to the data model,
`emit_tei(parse_tei(input))` will produce an XML document equivalent in content
to the original. The output may not be byte-for-byte identical (whitespace,
attribute ordering, and namespace prefixes are normalized), but it is
equivalent at the XML node level. This behaviour is considered acceptable, even
desirable, for a canonicalization perspective – it ensures consistent
formatting for downstream processing and diffing.

`tei-xml::parse_xml` now wraps `quick_xml::de::from_str`, keeping `quick-xml`
scoped to the XML crate while still surfacing failures through the shared
`TeiError::Xml { message }` variant. The helper stores the parser's message
verbatim so callers get actionable diagnostics (missing fields versus syntax
errors) without leaking the external dependency into `tei-core`. New unit tests
cover the happy-path parse of a canonical `<TEI>` shell, while the
behaviour-driven suite (powered by `rstest-bdd`) exercises structural and
well-formedness failures by feeding fixtures that omit `<teiHeader>` or end the
document abruptly. This gives us confidence that the library surfaces XML
faults consistently before we wire up emission.

`tei-xml::emit_xml` now completes the round-trip by delegating to
`quick_xml::se::to_string`. The emitter keeps XML-specific logic inside the
`tei-xml` crate while returning `TeiError::Xml` when quick-xml refuses to
serialize invalid data (for example, control characters that XML 1.0 forbids).
Fresh unit and behaviour-driven tests assert both the happy path (emitting the
minimal TEI skeleton) and the unhappy path (surfacing the serializer's control
character error message) so callers see consistent diagnostics regardless of
where emission is triggered.

```mermaid
sequenceDiagram
    participant Client
    participant tei_xml_parse_xml
    participant quick_xml_de_from_str
    participant tei_core_TeiError
    Client->>tei_xml_parse_xml: Call with XML string
    tei_xml_parse_xml->>quick_xml_de_from_str: Parse XML
    alt Success
        quick_xml_de_from_str-->>tei_xml_parse_xml: TeiDocument
        tei_xml_parse_xml-->>Client: Ok(TeiDocument)
    else Failure
        quick_xml_de_from_str-->>tei_xml_parse_xml: Error
        tei_xml_parse_xml->>tei_core_TeiError: TeiError::xml(error)
        tei_xml_parse_xml-->>Client: Err(TeiError::Xml)
    end
```

Some specifics of the parse/emit implementation:

- **Insignificant Whitespace**: By default, the model will not preserve
  insignificant whitespace. For instance, pretty-printing line breaks and
  indentation inside the XML are not retained (quick-xml’s Serde will treat
  them as either ignored or as empty text nodes that are dropped). The
  implementation normalizes text where appropriate. If whitespace is meaningful
  (e.g., within `<u>` text, spaces between words are of course preserved
  exactly as in the text content), but leading/trailing whitespace in certain
  contexts or between block elements will be trimmed or made consistent. The
  design goal is to have a *clean, normalized XML output* that tools or version
  control can reliably compare.

- **Comments and PIs**: The initial design does not parse or round-trip XML
  comments and processing instructions. These are outside the scope of Serde
  mapping (which deals with data content). The round-trip is therefore
  *semantic, not fully lossless*. Any comments in the source will be dropped
  when parsing to Rust types. This is acceptable for the intended use cases
  (manual comments in auto-generated scripts are not anticipated, though they
  could appear). As a future-proofing measure, the design has considered adding
  an **annotation field for comments** – for example, each element struct could
  have an optional `comments: Vec<String>` to collect comments that appeared
  immediately inside it (or perhaps just a special field at the document level
  for all comments). By marking such a field with
  `#[serde(skip_deserializing, skip_serializing)]`, the implementation could
  populate it via a custom parse routine if needed. However, implementing that
  would require a custom parser loop (since quick-xml Serde will ignore
  comments entirely). A more robust future solution is a *dual-layer* approach:
  maintain a parallel lossless representation for exact round-trips. Such a
  design would have an `XmlDocument` (or a low-level tree of XML nodes) that
  can capture everything including comments, entity references, etc., and then
  a conversion to `TeiDocument`. If exact re-serialization is needed for some
  editing tools, the system could use the original `XmlDocument` for output
  when no semantic changes have been made. For now, the implementation
  explicitly chooses **normalized, not fully lossless, output**, and documents
  that choice to avoid future confusion.

- **Namespaces**: TEI uses the default TEI namespace for most elements. The
  implementation preserves the namespace URI in the output. Quick-xml can
  handle namespaces, especially if the structs carry the correct names
  (possibly using `serde(rename = "tei:elementName")` if needed to bind to a
  prefix). When emitting, the code can choose a stable prefix, e.g., always use
  `tei:` on all TEI elements for clarity. The library ensures the XML root has
  the proper namespace declaration. Since the design likely only requires the
  TEI namespace (and perhaps none others except xml: for IDs and lang), this is
  straightforward. The implementation does not rely on the exact prefixes from
  input; those may be normalized in output.

- **Mixed Content Handling**: As shown earlier, by modeling mixed content with
  enums and `$value` placeholders, Serde with quick-xml handles most cases. One
  corner is that **empty elements** like `<pause/>` need to be represented
  appropriately. The design might model `<pause>` as a unit struct or as an
  enum variant with no associated data. Quick-xml’s serializer emits an empty
  tag for an empty string or unit struct. These scenarios are tested to ensure,
  for example, a `<pause/>` in the XML becomes something like `Inline::Pause`
  in Rust and back.

- **Performance**: Quick-xml and Serde are known to be efficient. The parse is
  done in one pass and directly fills the structs without intermediate DOM
  creation. The emit is similarly streaming. This means memory overhead is
  proportional to the size of the data (allocating Rust structs for the
  content) and does not hold the entire file as a heavy DOM with overhead.
  Benchmarks have shown quick-xml to be significantly faster than DOM-based
  parsers in Rust, making it suitable for even large transcripts. If the
  implementation encounters extremely large documents (say transcripts of many
  hours, tens of thousands of utterances), the memory usage of holding the
  entire `TeiDocument` at once might become a concern, which motivates the
  streaming approach described later. But for reasonably sized episodes (on the
  order of a few thousand words), this approach should be very fast and use
  only a few MBs of memory.

### JSON/MessagePack Serialization

In addition to XML, the Rust data model can be serialized to and from JSON or
MessagePack, facilitating two things:

- **Persistence/Interchange**: Episodes may be stored in a database as JSON
  (e.g., in Postgres JSONB) or sent over a network as MessagePack.

- **Python Interop**: The Python side `msgspec.Struct` classes have a schema
  that matches this JSON structure, so serializing in Rust to JSON/MsgPack and
  decoding in Python (or vice versa) allows easy data exchange.

The implementation derives `serde::Serialize`/`Deserialize` on all core types,
and the workspace routes JSON and MessagePack encoding/decoding through
`tei-serde` (wrapping `serde_json` and `rmp-serde`). The JSON structure is a
**semantic projection** of the TEI. For instance, a TEI
`<spanGrp type="cliche">` might be represented in JSON as an object like:

```json
{ "type": "cliche", "spans": [ { "id": "c1", "start": 100, "end": 120, "ana": "cliche:idiom.snowclone" } ] }
```

(This is illustrative; the actual JSON schema will be defined so that it aligns
with the Python `Struct` definitions exactly, including any nesting.)
Attributes become JSON fields, element names become object keys or struct field
names, and lists of elements become JSON arrays.

To formalize the on-the-wire format, the repository publishes a versioned JSON
Schema snapshot for `TeiDocument` under
`schemas/tei-document.schema.vX.Y.Z.json` (with
`schemas/tei-document.schema.json` tracking the latest workspace version). The
schema is generated from the canonical Rust types in `tei-core` via `schemars`,
and the root schema sets `$id` to
`urn:tei-rapporteur:schema:tei-document:X.Y.Z`. Maintainers regenerate the
snapshot with `make json-schema` whenever the Serde layout changes.

Schema generation relies on `schemars::JsonSchema` derives on the core Rust
types. To avoid forcing downstream users of `tei-core` to depend on `schemars`
when they only need Serde, the `schemars` dependency and derives are gated
behind the `tei-core` Cargo feature `json-schema`. `tei-serde` enables this
feature when generating the published schema snapshots.

Figure: Class diagram of the core `tei-core` data model and the
`tei-serde::schema` entry points used to generate the published JSON Schema
snapshots.

```mermaid
classDiagram
    %% Core document structure
    class TeiDocument {
        +TeiHeader tei_header
        +TeiText text
        +JsonSchema
    }

    class TeiHeader {
        +FileDesc file_desc
        +EncodingDesc encoding_desc
        +ProfileDesc profile_desc
        +RevisionDesc revision_desc
        +JsonSchema
    }

    class TeiText {
        +TeiBody body
        +JsonSchema
    }

    class TeiBody {
        +BodyBlock[] content
        +JsonSchema
    }

    class BodyBlock {
        <<enum>>
        +P p
        +Utterance utterance
        +JsonSchema
    }

    class P {
        +Inline[] content
        +JsonSchema
    }

    class Utterance {
        +XmlId xml_id
        +Speaker speaker
        +Inline[] content
        +JsonSchema
    }

    class Inline {
        <<enum>>
        +String text
        +Hi hi
        +Pause pause
        +JsonSchema
    }

    class Hi {
        +String rend
        +Inline[] content
        +JsonSchema
    }

    class Pause {
        +String dur
        +JsonSchema
    }

    class XmlId {
        +String value
        +JsonSchema
    }

    class Speaker {
        +String value
        +JsonSchema
    }

    class DocumentTitle {
        +String value
        +JsonSchema
    }

    %% Header subcomponents
    class FileDesc {
        +DocumentTitle title
        +JsonSchema
    }

    class EncodingDesc {
        +AnnotationSystem[] annotation_systems
        +JsonSchema
    }

    class AnnotationSystem {
        +AnnotationSystemId identifier
        +String description
        +JsonSchema
    }

    class AnnotationSystemId {
        +String value
        +JsonSchema
    }

    class ProfileDesc {
        +SpeakerName[] speakers
        +LanguageTag[] languages
        +JsonSchema
    }

    class SpeakerName {
        +String value
        +JsonSchema
    }

    class LanguageTag {
        +String value
        +JsonSchema
    }

    class RevisionDesc {
        +RevisionChange[] changes
        +JsonSchema
    }

    class RevisionChange {
        +String description
        +ResponsibleParty who
        +JsonSchema
    }

    class ResponsibleParty {
        +String value
        +JsonSchema
    }

    %% tei-serde schema module
    class TeiSerdeSchemaModule {
        +String tei_document_schema_id()
        +Schema tei_document_schema()
        +Result~String, Error~ tei_document_schema_json_pretty()
    }

    class Schema {
    }

    %% Relationships within tei-core
    TeiDocument --> TeiHeader
    TeiDocument --> TeiText

    TeiText --> TeiBody
    TeiBody --> BodyBlock

    BodyBlock --> P
    BodyBlock --> Utterance

    P --> Inline
    Utterance --> XmlId
    Utterance --> Speaker
    Utterance --> Inline

    Inline --> Hi
    Inline --> Pause

    FileDesc --> DocumentTitle

    TeiHeader --> FileDesc
    TeiHeader --> EncodingDesc
    TeiHeader --> ProfileDesc
    TeiHeader --> RevisionDesc

    EncodingDesc --> AnnotationSystem
    AnnotationSystem --> AnnotationSystemId

    ProfileDesc --> SpeakerName
    ProfileDesc --> LanguageTag

    RevisionDesc --> RevisionChange
    RevisionChange --> ResponsibleParty

    %% Schema generation coupling
    TeiSerdeSchemaModule ..> TeiDocument : JsonSchema for
    TeiSerdeSchemaModule ..> Schema : returns
```

One important design consideration is that the JSON output should be
**deterministic**. If the system serializes the same `TeiDocument` twice, it
should produce the exact same JSON string (apart from spacing if any). Serde
generally guarantees field ordering as defined in the struct, but the
documentation will detail the JSON format and may provide a canonical
serialization (for example, always sorting certain map keys if the
implementation uses maps) to ensure consistency. This deterministic JSON (and
XML) is helpful for content-addressing and change detection – e.g., a given
script always produces the same XML, so a hash can identify it uniquely.

Testing round-trip integrity is crucial: the test suite verifies that

- `TEI XML -> parse -> JSON -> emit -> TEI XML` yields the same canonical TEI
  XML (losing only insignificant differences), and

- `JSON -> serde (Rust) -> XML -> parse -> JSON` yields the same JSON
  (idempotent normalization). These invariants ensure that the projections
  don’t accidentally drop or corrupt information.

## Python Integration via PyO3 and msgspec

The Python interface to `tei-rapporteur` is designed for ease of use in
scripting and data analysis environments, without exposing the complexity of
XML or Rust internals. The module presents Python developers with simple
functions and Python data classes (via `msgspec.Struct`) corresponding to the
TEI structures while delegating the heavy lifting to Rust.

### PyO3 wrapper (`tei_py` module)

The implementation exposes a PyO3 module (referred to as `tei_rapporteur` for
Python callers) with a set of functions to create or transform TEI data. The
key functions include:

- `parse_xml(xml_str: str) -> Document` – Parse a TEI XML string into a Python
  object. Under the hood, this uses Rust’s `parse_tei` to get a `TeiDocument`.
  The function then wraps it in a PyO3 `Document` class (defined on the Rust
  side) or immediately converts it to a Python structure. The returned
  `Document` is a lightweight wrapper that holds the Rust `TeiDocument`
  internally.

- `emit_xml(doc: Document) -> str` – Take a `Document` (Rust-backed) and emit
  it as TEI XML string by calling Rust’s emitter. This returns the canonical
  XML.

- `validate(doc: Document) -> None` – Run Rust’s validation checks on the
  document (e.g., ID uniqueness, allowed structures). If validation fails, a
  Python exception (e.g., `ValueError`) is raised with details.

- Additional **constructor** functions for alternate input forms:

- `from_dict(py_obj: Mapping | Sequence) -> Document` – Accept a plain Python
  nested dict/list (for instance, something that could be the result of
  `json.loads` on the JSON serialization) and convert it to a `TeiDocument`.
  This uses serde via `pyo3-serde` to map Python built-ins into Rust
  structures. It allows quick testing or construction from Python without
  writing XML.

- `from_msgpack(bytes_obj: bytes) -> Document` – Accept a MessagePack binary
  (as `bytes`) and deserialize it (using `tei-serde`) into a `TeiDocument`.
  This is a very efficient path if the Python side already has a
  `msgspec.Struct` and encodes it to bytes. The binding now uses
  `tei_serde::msgpack::from_slice` and converts any decode failure into a Python
  `ValueError`, ensuring callers never need to reason about Rust-only error
  types.

  The following sequence diagram captures the end-to-end control flow for
  `from_msgpack`, including the propagation of successful documents and the
  error path that produces a `ValueError` when decoding fails:

  ```mermaid
  sequenceDiagram
      actor PythonUser
      participant tei_rapporteur_Python as "tei_rapporteur (Python)"
      participant PyO3_FFI_Bridge as "PyO3 FFI Bridge"
      participant Rust_document_from_msgpack as "Rust: document_from_msgpack()"
      participant tei_serde
      PythonUser->>tei_rapporteur_Python: from_msgpack(payload: bytes)
      tei_rapporteur_Python->>PyO3_FFI_Bridge: Call from_msgpack(bytes)
      PyO3_FFI_Bridge->>Rust_document_from_msgpack: document_from_msgpack(bytes)
      Rust_document_from_msgpack->>tei_serde: msgpack::from_slice(bytes)
      alt Valid MessagePack
          tei_serde-->>Rust_document_from_msgpack: TeiDocument
          Rust_document_from_msgpack-->>PyO3_FFI_Bridge: TeiDocument
          PyO3_FFI_Bridge-->>tei_rapporteur_Python: Document
          tei_rapporteur_Python-->>PythonUser: Document
      else Invalid MessagePack
          tei_serde-->>Rust_document_from_msgpack: Error
          Rust_document_from_msgpack-->>PyO3_FFI_Bridge: Error
          PyO3_FFI_Bridge-->>tei_rapporteur_Python: Raise ValueError
          tei_rapporteur_Python-->>PythonUser: ValueError
      end
  ```

- `from_json(json_str_or_bytes) -> Document` – Similar to above, but for JSON
  text. Rust uses `tei_serde::json::from_str` (wrapping `serde_json`) to
  deserialize JSON text. (This might be slightly less efficient than
  MessagePack due to parsing text, but convenient for debugging.)

- Corresponding **output** functions to retrieve data from a `Document`:

- `to_dict(doc: Document) -> dict` – Convert the `TeiDocument` into a Python
  dictionary/list structure via `pyo3-serde`. This produces a JSON-like object
  that could be used directly or fed into other libraries.

- `to_msgpack(doc: Document) -> bytes` – Serialize the `TeiDocument` to
  MessagePack bytes (using `tei-serde`).

- `to_json(doc: Document) -> bytes|str` – Serialize to JSON (the API can allow
  returning either a Python bytes or str for the JSON text).

The Python module also defines a Python-visible class, say `Document`, using
`#[pyclass]` in Rust. This class primarily holds `inner: TeiDocument` (as in
the example code) and perhaps implements `__repr__` or other niceties.
Iterators or data accessors are provided only if needed, but the expectation is
that users will usually convert the document into their own `msgspec.Struct`
classes for any intensive work. The `Document` class is mostly a vessel to
carry data between functions in this minimal API approach.

The encoder half of this API now exists alongside the decoder. `to_msgpack`
wraps a small Rust helper that invokes
`tei_serde::msgpack::to_vec_named(&TeiDocument)` and maps any `encode::Error`
into a Python `ValueError`. Although the current `TeiDocument` serializer is
infallible for valid data, the explicit error conversion keeps the glue
predictable if future schema additions require custom serialization. PyO3
enforces that callers pass a `tei_rapporteur.Document` instance, so attempts to
invoke `to_msgpack` with other Python objects are rejected with a standard
`TypeError` before even entering Rust. The helper is re-exported to Rust
callers as well so internal tests can drive round-trip assertions without going
through Python.

Behaviour-driven coverage now mirrors the decoder scenarios: one feature builds
`Document` via the Python constructor, calls `to_msgpack`, and confirms that
the stored payload decodes back to the original title. A companion scenario
intentionally calls `to_msgpack("not a document")` and asserts that the
resulting error string mentions the incorrect type, ensuring misuse fails fast
at the boundary. The unit suite supplements this with Rust-level tests that
serialize titles containing XML special characters and immediately decode the
bytes via the internal helper.

#### Current module scaffolding (Phase 2.1)

Phase 2.1 delivers the first concrete slice of this design. The `tei-py` crate
now depends on PyO3 (built with the `extension-module` feature) and exposes the
canonical `tei_rapporteur` module. The module registers the `Document`
`#[pyclass]`, a helper that constructs documents from validated titles, and a
Python-level `emit_title_markup` function that maps 1:1 to the existing Rust
helper.

The `Document` class stores a `TeiDocument` and provides Python getters plus
convenience methods that reuse the Rust validation logic instead of duplicating
checks in CFFI code.

`pyproject.toml` lives at the workspace root so `maturin develop` or
`maturin build` can discover the `tei-py/Cargo.toml` manifest without extra
flags, mirroring the structure explained in `docs/workspace-layout.md`.
Continuous integration now includes a wheel build/install smoke test on Ubuntu:
the workflow provisions Python 3.11, invokes
`maturin build --manifest-path tei-py/Cargo.toml`, installs the resulting wheel
via `pip`, and imports `tei_rapporteur` to confirm the PyO3 module initializes
correctly. This guards against future regressions so the Python surface always
builds alongside the Rust crates.

#### XML data exchange bindings (Phase 2.2)

The second milestone for the Python layer connects the core XML pipeline to
PyO3 so Python callers can avoid serializing through MessagePack when they
already have TEI strings. The new `parse_xml` and `emit_xml` functions are thin
shims: each delegates to `tei_xml::{parse_xml, emit_xml}` and then relies on
the existing `Document` wrapper to keep ownership inside Rust. The binding
layer intentionally reuses the `wrap_tei_result` helper so all failures land as
`ValueError` with the original `TeiError` message. This keeps the Python
surface predictable and avoids leaking Rust-specific enums across the FFI
boundary.

`emit_xml` returns the canonical TEI string produced by the XML crate. The
helper explicitly scans the resulting buffer for forbidden XML 1.0 control
characters before handing it back to Python so error messages mention the exact
offending code point (for example, `U+0000`). The binding preserves that
behaviour verbatim, which means Python callers observe the same diagnostics as
Rust callers. Conversely, `parse_xml` forwards the XML string into
`quick-xml::de` through the `tei-xml` helper, guaranteeing that every Python
parse flows through the same validation logic as a Rust caller.

Behaviour-driven coverage now exercises both functions via `rstest-bdd`.
Feature files construct canonical TEI fixtures, round-trip them through the
module, and assert on both happy and unhappy paths (missing headers, forbidden
characters, and so on). Unit tests invoke the exported PyO3 functions directly
to ensure `PyValueError` messages surface the helpful context. Together these
tests pin the boundary contract so future schema evolution cannot silently
break XML callers.

The Rust implementation now leans on two small macro families to keep this FFI
layer maintainable. `define_conversion_pair!` declares the twin helpers for
each transfer format (currently MessagePack and TEI XML), and the `define_py_*`
wrappers map those helpers into PyO3 functions while unifying the error text
emitted to Python. Adding another format in the future requires only
instantiating the macros with the appropriate encoder/decoder pair. On the
integration-test side, the behaviour-driven harness was decomposed into
feature-specific modules (`steps_construction`, `steps_msgpack`, `steps_xml`,
etc.) so no test file exceeds the 400-line guideline. Shared payload handling
now relies on a `PayloadSlot<T>` helper that centralizes the borrow/clear logic
for MessagePack and XML artefacts. This removes duplicated `RefCell` juggling
and eliminates the double-borrow panic that previously occurred when an error
was recorded while a document reference was still alive.

Dictionary exchange now mirrors the MessagePack and XML helpers. The binding
uses `pyo3-serde` (implemented via the `serde-pyobject` crate) to map Python
dict/list trees directly into `TeiDocument` without intermediate JSON parsing.
`from_dict` wraps `pyo3_serde::from_pyobject`, surfaces validation failures as
`ValueError`, and preserves the underlying serde error text (for example,
missing `teiHeader` or blank titles). `to_dict` uses `pyo3_serde::to_pyobject`
to return Python-native structures compatible with `msgspec.to_builtins` and
other JSON-focused libraries. Behaviour-driven tests now cover successful
decoding, missing-field failures, blank-title payloads, and the type error
raised when `to_dict` is called with the wrong object, pinning the boundary
contract for these dictionary helpers.

The following sketch illustrates how the Python API can be used:

```python
import tei_rapporteur as tei

# 1. Parse an XML file into a Document
xml_str = open("episode1.tei.xml", "r", encoding="utf-8").read()
doc = tei.parse_xml(xml_str)
doc.validate()          # Raises if anything invalid

# 2. Convert Document to a Python dict (JSON form) and to a msgspec struct
episode_dict = tei.to_dict(doc)
# Suppose a msgspec.Struct corresponding to the Episode is defined:
episode = EpisodeStruct(**episode_dict)   # msgspec.structs aren't dataclasses but can be decoded via msgspec
# (Alternatively, encoded = tei.to_msgpack(doc); episode = msgspec.msgpack.decode(encoded, type=EpisodeStruct))

# 3. Manipulate the episode via msgspec object (e.g., add an utterance)
episode.utterances.append(Utterance(speaker="guest1", content=["Thank you all for listening!"]))
# 4. Validate and emit back to TEI XML
new_doc = tei.from_struct(episode)    # Under the hood: msgspec -> bytes -> Rust
tei.validate(new_doc)
xml_out = tei.emit_xml(new_doc)
```

In this example, `from_struct` would be a convenience that detects a
`msgspec.Struct` and converts it. The design has not explicitly listed
`from_struct` above, but it can be implemented to call `msgspec.to_builtins` on
the object in Python and then call `from_dict` internally. Supporting
`from_struct` removes the need to manually encode to bytes or dictionaries –
the extension can perform that conversion by leveraging the `msgspec` API at
runtime. The PyO3 function for `from_struct` can check if the passed Python
object has a `__struct_fields__` attribute (which `msgspec.Struct` objects do)
and, if so, perform the conversion as shown in the pseudo-code snippet.

### Why `msgspec.Struct`?

The architecture favours `msgspec.Struct` for the Python representation because
it offers dataclass-like convenience with built-in serialization support. By
defining Python classes that subclass `msgspec.Struct`, the module gains
automatic `msgspec.json` and `msgspec.msgpack` encoding/decoding for those
classes. This means Python code can easily turn a nested object tree into a
bytes buffer (JSON or MessagePack) with one call, which is exactly what is
needed to send data to Rust. Likewise, when Rust returns bytes, `msgspec` can
decode them right back into class instances. This approach avoids a large
amount of boilerplate in PyO3 to map Python attributes to Rust fields.

Furthermore, using `msgspec.Struct` means the project **does not need to
maintain parallel class hierarchies** in Python and Rust manually. The schema
is defined in one place (the Python `Struct` definitions can even be generated
from the Rust definitions or vice versa). The Python classes are lightweight
and do not contain behaviour – they are purely data containers. All the real
logic (parsing, validation, etc.) resides in Rust. This separation aligns with
the design philosophy: *Rust for core logic, Python for orchestration*. The
earlier design discussion concluded that this strategy keeps the Python side
“pure” (no C extensions needed for every class, just the one extension module)
and the Rust side idiomatic.

To illustrate, imagine the following `msgspec.Struct` classes in Python
corresponding to the Rust model (simplified):

```python
import msgspec

class Span(msgspec.Struct):
    id: str
    start: int
    end: int
    ana: str            # e.g., "cliche:idiom.snowclone"

class Utterance(msgspec.Struct):
    id: str
    speaker: str | None = None
    content: list[str | dict]    # str for text, dict for an inline element like hi
                                 # (which could be represented as a dict with keys 'rend' and 'content')
    
class Episode(msgspec.Struct):
    id: str
    title: str
    utterances: list[Utterance]
    spans: list[Span] = []       # maybe stand-off annotations
```

*(In practice, `msgspec` can also support recursive types and union types; the
inline element could be better represented as a proper nested Struct class
instead of dict, e.g., a class `Hi(msgspec.Struct)` and then content:
`list[str | Hi]`. For brevity the example uses a dict in the content above.)*

Now, a Python user can create and manipulate an `Episode` instance easily. When
it's time to send it to Rust for validation or XML export, they can do:

```python
payload = msgspec.msgpack.encode(my_episode_obj)
doc = tei_rapporteur.from_msgpack(payload)   # into Rust
tei_rapporteur.validate(doc)
xml = tei_rapporteur.emit_xml(doc)
```

This sequence performs a single fast binary serialization of the whole object
(`msgspec` uses a performant native implementation), and Rust deserializes it
in one go via `tei_serde::msgpack` directly into a `TeiDocument`. This **binary
boundary** approach is extremely efficient for large data because it avoids
per-field conversions across the FFI boundary. By contrast, manually setting
attributes on PyO3 classes for each field would incur many Python C-API calls.

For smaller documents or convenience, the API also allows passing Python dicts
or even the `Struct` object directly:

```python
doc = tei_rapporteur.from_struct(my_episode_obj)
```

In this case, `from_struct` will internally call
`msgspec.to_builtins(my_episode_obj)` to get a plain dict/list structure, then
use serde to decode that to Rust. This has an extra conversion step but can be
useful for quick scripts and is still reasonably fast (msgspec’s conversion to
builtins is implemented in Rust and is quite efficient).

#### Python API Summary

The table below summarizes the Python-facing API and how each function
exchanges data:

| Python Function           | Input (Python side)                 | Conversion Mechanism                                      | When to Use                                          |
| ------------------------- | ----------------------------------- | --------------------------------------------------------- | ---------------------------------------------------- |
| `parse_xml(xml_str)`      | XML string (TEI P5)                 | Rust parses XML (quick-xml) into `TeiDocument`            | Reading TEI files from disk                          |
| `from_dict(obj)`          | `dict`/`list` tree (JSON structure) | Serde via `pyo3_serde` to Rust `TeiDocument`              | Constructing from Python data (e.g., test cases)     |
| `from_struct(obj)`        | `msgspec.Struct` instance           | Calls `msgspec.to_builtins`, then same as above           | High-level, Pythonic import of msgspec data          |
| `from_msgpack(bytes)`     | MessagePack bytes                   | Rust uses `tei_serde::msgpack` to decode to `TeiDocument` | Fast path for large data, or transferring via binary |
| `from_json(str_or_bytes)` | JSON string or bytes                | Rust uses `tei_serde::json::from_str` to decode           | When JSON text is available (slower than MsgPack)    |
| *Return: `Document`*      | *(PyO3 class wrapping data)*        | Holds Rust `TeiDocument` inside (no copy unless mutated)  | Represents TEI document in Python                    |

| Python Function   | Output (Python side)       | Conversion Mechanism                                | Notes                               |
| ----------------- | -------------------------- | --------------------------------------------------- | ----------------------------------- |
| `validate(doc)`   | `None` or throws exception | Rust validation logic, throws PyErr on failure      | Checks IDs, structure, etc.         |
| `emit_xml(doc)`   | XML string                 | Rust serializes `TeiDocument` via quick-xml         | Canonical XML output                |
| `to_dict(doc)`    | `dict`/`list` (JSON-like)  | Rust uses `pyo3_serde` to convert to Python objects | For interoperability or inspection  |
| `to_msgpack(doc)` | bytes (MessagePack)        | Rust uses `tei_serde::msgpack` to encode            | For binary storage or fast transfer |
| `to_json(doc)`    | str (JSON text) or bytes   | Rust uses `tei_serde::json` to encode               | Human-readable serialization        |

(Table: Python API functions provided by `tei-rapporteur` and their data
exchange mechanisms. The `Document` class is returned by parse/from functions
and consumed by validate/emit and conversion functions. In many cases, using
the MessagePack path is the most efficient for large payloads, whereas
dict/JSON paths are convenient for debugging and small data.)

This multi-path design ensures that for **small to medium documents**, one can
directly use `from_struct` or `from_dict` with negligible overhead – it “just
works” with idiomatic Python objects. For **very large documents** (imagine an
episode transcript with tens of thousands of words or numerous annotations),
the recommended approach is to use the MessagePack boundary, which does one
bulk serialization instead of many Python C-API calls. In all cases, the heavy
parsing of XML and enforcement of rules happens in Rust, keeping Python’s role
to encoding/decoding and high-level orchestration.

Another benefit: by not creating elaborate Python classes (no need for
per-element PyClasses or dataclasses), the Python package stays lightweight. If
future iterations aim to provide a more Pythonic OO interface (with methods on
classes, etc.), that can still happen – perhaps by making richer PyClasses that
wrap `TeiDocument` or parts of it. However, that remains optional and can be
layered later. The current approach already gives Python users a comfortable
experience using Pydantic-like or dataclass-like objects that can be easily
printed, accessed, and even validated on the Python side if needed (msgspec can
perform schema validation).

## Pull-Parser Interface (Streaming Parsing)

For certain use cases, it may be desirable to parse a TEI document
incrementally and process it element-by-element (for example, to start
analyzing or producing output from the beginning of a large transcript before
the entire file is parsed, or to reduce memory usage by not holding the entire
document tree in memory). To facilitate this, `tei-rapporteur` includes a
design for a **pull-parser interface** that yields TEI domain objects as they
are parsed.

### Design Goals for Streaming

- **Incremental processing**: The interface should allow the caller (Rust or
  Python) to obtain the next logical unit from the input stream without loading
  the whole document.

- **Retain domain structure**: Instead of low-level XML tokens, the parser
  yields high-level objects (e.g., an `Utterance` struct or a `P` paragraph
  object) that the user can work with directly. This is higher-level than
  typical SAX parsing, but lower-level than a full document parse.

- **Shared use in Rust and Python**: The implementation lives in Rust, but the
  library exposes it to Python in a natural way (e.g., as an iterator or
  generator of `msgspec`-serializable objects).

- **Opt-in and possibly experimental**: Because Rust’s stable APIs for
  generators are limited, this feature might rely on unstable features or
  external crates. The implementation will likely gate it behind a Cargo
  feature (e.g., `streaming_parser`) so users can turn it on if needed without
  affecting the stable core.

### Rust Implementation Approaches

Rust does not yet have stable support for yielding values from a function
(coroutines) in a synchronous context, but there are a few ways to implement a
pull-parser:

- **Manual Iterator**: Implement a struct (say `TeiStreamParser`) that contains
  an instance of `quick_xml::Reader` and some state, and implement the
  `Iterator` trait for it. The `next()` method would read from the XML until it
  completes one unit (e.g., one `<u>` element and its content) and then return a
  `TeiElement` enum or a specific struct. Internally, this requires managing
  the nesting and ensuring that when the iterator yields an element, it doesn’t
  consume beyond it. Quick-xml’s low-level API gives events like
  “StartElement", “Text", “EndElement". That can be leveraged: for example, on
  seeing `<u>`, the iterator could accumulate events until the matching `</u>`
  and then deserialize that chunk (perhaps by using `quick_xml::de::from_str`
  on the slice representing the `<u>` element) into an `Utterance` struct. This
  might be tricky with borrowing, but one option is to copy out the slice of
  XML for the element. Alternatively, the implementation might construct the
  struct by hand as events arrive (for example, building the string content and
  any inline elements).

- The manual iterator approach can be done on stable Rust and gives fine
  control. The downside is complexity in implementation.

- **Generator/Coroutine (Nightly)**: Rust nightly has a `Generator` trait and
  the `yield` keyword (unstable). The parser could be written as a generator
  that yields `TeiElement` as it parses. This would likely make the code
  simpler (the generator could `yield` an Utterance when finished parsing it,
  then continue parsing the next one). However, this requires nightly Rust or a
  generator library.

- **Async Stream**: Using an async context with something like the
  `async-stream` crate, the library can create an asynchronous generator. For
  example, `async_stream::try_stream!` allows `yield`ing values within an async
  function. The file input could be treated as an async stream (or a
  synchronous read wrapped in a future) and parsed elements yielded. The result
  would be a `Stream<Item = TeiElement>` that can be consumed either with async
  or by blocking. Async-stream is stable (it uses proc macros), but it
  introduces a dependency on the futures ecosystem.

An initial prototype might start with approach (1) for full control. The end
result could be an API like:

```rust
// in tei-xml crate:
pub enum TeiEvent {
    StartDocument,
    EndDocument,
    Element(ElementEnum), // e.g., Utterance(Utterance), Paragraph(P), etc.
    // potentially other event types
}

pub struct TeiPullParser<R: std::io::BufRead> {
    reader: quick_xml::Reader<R>,
    buf: Vec<u8>,
    // ... any state needed (e.g., current depth, etc.)
}

impl<R: BufRead> Iterator for TeiPullParser<R> {
    type Item = Result<TeiEvent, TeiError>;
    fn next(&mut self) -> Option<Self::Item> {
        // ... parse next piece and return Some(...) or None at EOF
    }
}
```

This iterator would yield events. It might be simplified to emit only
high-level elements (skipping StartDocument, etc., unless needed for
signalling). The `ElementEnum` could be an enum of the domain objects: e.g.,
`ElementEnum::Utterance(Utterance)`, `ElementEnum::Span(Span)`, etc., for each
top-level or annotation element worth yielding. The iterator might not emit
low-level elements like `<hi>` because those appear as part of the parent
Utterance’s inline content. The likely focus is on yielding each utterance or
each top-level division in the body.

**Python Exposure**: To expose this to Python, the API can either:

- Provide a function that returns an iterator (PyO3 can convert a Rust
  `Iterator` into a Python iterator if the PyIterProtocol is implemented). The
  module could expose `tei_rapporteur.iter_parse(xml_str)` that returns a
  Python iterable. The iteration in Python would call back into Rust’s iterator
  `next()`. Each yielded item needs to be converted to a Python object. There
  are choices: yielding a PyO3 `Document` or similar for each element might be
  heavy, whereas yielding a Python dict for each element could be fine (since
  the caller might immediately turn it into their struct or process it). The
  binding can leverage `pyo3-serde`: for each `ElementEnum` yielded, call
  `to_builtins` to get a dict, and yield that.

- Alternatively, use Python generators: PyO3 can support writing a generator
  function in Rust, but it's not straightforward. Sticking to the iterator
  protocol is typically easier.

**Experimental Nature**: Because this is complex and possibly requires nightly
features, the streaming parser is marked as an experimental opt-in. For
example, in `Cargo.toml` of `tei-xml`:

```toml
[features]
streaming = ["quick-xml", "generator"]
```

where `generator` is the nightly generator feature. Alternatively, the build
can use `async-stream` behind a `streaming_async` feature.

The documentation will clearly state that the streaming interface is available
but might require a nightly compiler or have certain limitations. The primary
crate remains focused on full-document parse.

**Use Cases**: A tool like Episodic might use the streaming parser to scan
through a TEI without loading everything, maybe to calculate timings or do a
first pass transformation. However, since Episodic likely needs the full script
in memory anyway to do planning and feedback loops, the streaming is more
useful for extremely large inputs or integrating with pipelines that prefer
streaming (e.g., if TEI input comes from a network stream).

Nonetheless, having a pull-parser aligns with Rust's zero-cost iterators
philosophy and gives advanced users more flexibility. It differentiates the
library by avoiding the all-or-nothing approach typical of many XML libraries.
Because it is built on the same data model, each yielded element is a proper
instance of the Rust structs (or easily convertible to the same) – ensuring
consistency between streaming and full parse modes.

### Implementation Notes (December 2025)

The streaming parser was implemented using a **manual Iterator approach** on
stable Rust, avoiding the need for nightly generator features. The
implementation uses `quick_xml::Reader` directly with a state machine to track
parsing context.

**Key design decisions:**

- **Header accumulation**: The `<teiHeader>` is accumulated as raw bytes and
  deserialized in one pass using the existing Serde deserializers. This ensures
  speaker declarations (in `<profileDesc>`) are available before body parsing
  begins, which is necessary for validating utterance `@who` references. The
  trade-off is that the header must fit in memory, but headers are typically
  small compared to body content.

- **State machine**: A `ParserState` enum tracks parsing context (awaiting root,
  in header, in body, in paragraph, in utterance, in emphasis, etc.). This
  enables correct handling of nested inline elements without recursion limits.
  The state machine transitions on XML start/end events and yields high-level
  domain events at appropriate boundaries.

- **Event granularity**: Events are high-level domain objects (`TeiHeader`,
  `BodyBlock`) rather than raw XML tokens. This matches the library's
  philosophy that TEI XML is the canonical format with Rust types as validated
  views. Body blocks (paragraphs and utterances) are yielded one at a time as
  they complete parsing, providing the primary streaming benefit.

- **Feature gating**: All streaming code is behind the `streaming` Cargo
  feature, keeping the default build lightweight and avoiding any overhead for
  users who don't need incremental parsing.

**Module structure:**

```text
tei-xml/src/streaming/
├── mod.rs       # Module root, re-exports
├── event.rs     # TeiEvent enum definition
├── parser.rs    # TeiPullParser struct and Iterator impl
└── state.rs     # ParserState enum and transitions
```

**Parser state machine diagram:**

The following diagram illustrates the state transitions in the streaming
parser. Each state corresponds to a variant of the `ParserState` enum, and
transitions occur in response to XML events (element starts, ends, and text
content).

```mermaid
stateDiagram-v2
    [*] --> Initial
    Initial --> AwaitingRoot : advance()

    AwaitingRoot --> AwaitingHeader : <TEI> start
    AwaitingRoot --> Error : unexpected element

    AwaitingHeader --> InHeader : <teiHeader> start
    AwaitingHeader --> Error : missing teiHeader

    InHeader --> InHeader : nested elements
    InHeader --> AwaitingText : </teiHeader> and header parsed
    InHeader --> Error : malformed header

    AwaitingText --> AwaitingBody : <text> start
    AwaitingText --> Error : missing text

    AwaitingBody --> InBody : <body> start
    AwaitingBody --> DocumentComplete : <body/> empty
    AwaitingBody --> Error : missing body

    InBody --> InParagraph : <p> start
    InBody --> InUtterance : <u> start
    InBody --> InBody : other elements
    InBody --> AfterBody : </body>
    InBody --> DocumentComplete : eof (graceful)

    AfterBody --> DocumentComplete : </text> or </TEI> or eof

    InParagraph --> InEmphasis : <hi> start
    InParagraph --> InParagraph : text, inline
    InParagraph --> InBody : </p> yields BodyBlock Paragraph

    InUtterance --> InEmphasis : <hi> start
    InUtterance --> InUtterance : text, inline
    InUtterance --> InBody : </u> yields BodyBlock Utterance

    InEmphasis --> InEmphasis : nested inline
    InEmphasis --> InParagraph : </hi> when parent paragraph
    InEmphasis --> InUtterance : </hi> when parent utterance

    InBody --> Error : malformed xml
    AfterBody --> Error : malformed xml
    InParagraph --> Error : malformed xml
    InUtterance --> Error : malformed xml
    InEmphasis --> Error : malformed xml

    DocumentComplete --> [*]
    Error --> [*]
```

The implementation includes unit tests for event predicates and state
transitions, plus behaviour-driven tests (rstest-bdd) covering happy paths
(minimal documents, paragraphs, utterances, inline elements) and unhappy paths
(malformed XML, missing header, truncated documents).

## Validation Strategy and Data Integrity

Validation of TEI documents can be incredibly complex given the full TEI
schemas (RELAX NG grammar and Schematron rules). For the focused subset, the
project enforces correctness through a combination of **Rust type structure**,
**internal checks**, and optional external schema validation for full coverage.

- **By-construction validity**: Many structural constraints are ensured simply
  by using Serde to parse into the typed structs. If the XML is not well-formed
  or violates the basic expected structure (e.g., a `<teiHeader>` is missing),
  parsing will fail immediately with an error. The Rust type definitions
  themselves act as a schema: for example, if the `TeiDocument` requires a
  `tei_header` and `text`, the absence of those in XML will cause a deserialize
  error. Similarly, if an element that should only contain certain children
  gets some other tag, quick-xml/Serde will error out unless configured to
  ignore it.

- **Internal Rust validation**: After parsing, the library invokes
  `TeiDocument::validate(&self)` which performs deeper checks that are not
  enforced by the type system:

- **Unique ID check**: Gather all `xml:id` values in the document (e.g., in
  utterances, spans, etc.) and ensure there are no duplicates. If duplicates
  exist, return a `TeiError::Validation`.

- **Reference check**: For every cross-reference (like a span’s `from`/`to`
  pointing to an `xml:id` or an XPointer into the text), verify that the target
  exists. This might involve ensuring something like “if `from="#u5:char=10"`
  then an element with id `u5` exists and has enough characters”.

- **Structural rules**: Enforce any rules of the subset that Serde doesn’t
  catch. For instance, maybe the subset says that `<body>` can contain either
  `<div>` or `<u>` directly but not both or not nested arbitrarily. If Serde
  allowed a list of a enum of `<div>|<u>`, it might accept a mix; if that’s not
  desired, validate can check and warn or error. Another example: If using
  `<spanGrp type="...">`, maybe ensure all `<span>` inside have that matching
  type context.

- **Annotation integrity**: Check that offsets in spans don’t overlap
  improperly or that spans referenced by `corresp` match an actual element id
  of an episode, etc. Some of these might be beyond simple (for instance,
  ensuring spans are within bounds of the referenced text length).

- **Value ranges and formats**: If any attributes must take certain values (say
  @type of spanGrp must be one of known values like "cliche" or "accent"), the
  validator can check that against an enum or list.

These checks ensure that even if an XML passed parsing, it adheres to the
semantic rules of the TEI profile.

The first implementation of `TeiDocument::validate` now enforces two rules for
the Episodic profile:

- `xml:id` values must be unique across header annotation systems, paragraphs,
  and utterances.
- When a cast list is present, every utterance `who` reference must match one
  of the declared speakers. An empty cast still counts as “declared”, so any
  speaker reference will fail validation until the cast is populated. If no
  cast is present, speaker references are accepted without cross-checking.

Behaviour-driven scenarios now exercise both body-only identifier clashes and
collisions between header annotation systems and body blocks. The empty-cast
edge case is encoded in the same suite: an explicitly declared but empty cast
rejects all `who` references, whereas the absence of a cast allows them, so
draft transcripts can be validated incrementally.

These checks keep documents internally consistent without requiring an external
schema.

- **External schema validation (optional)**: The roadmap includes providing a
  RELAX NG schema (and Schematron if needed) for the TEI Episodic profile.
  Rather than implementing a full RELAX NG validator in Rust (which would be a
  major project on its own), the library can integrate external tools for
  callers who need that extra guarantee:

- For example, the distribution could include a function or a command-line tool
  `tei_validate_schema(xml_str)` that, when invoked, runs the XML through an
  external validator (like `jing` for RNG or an XSLT for Schematron). This
  behaviour would remain opt-in (and certainly not in the core parse path), but
  act as a utility. The test suite or CI can call this after serialization to
  double-check that the output conforms. If any discrepancy is found, that
  indicates a bug either in the model or the understanding of the schema.

- This approach keeps the core library lean (no massive schema parsing code)
  but still provides a path to high assurance when needed. Documentation will
  outline how to run schema validation as part of a QA process (for example,
  “when `jing` is available, run `tei-rapporteur --validate file.tei.xml`,”
  which internally shells out).

- **Relax NG schema generation**: The Episodic profile now ships a Relax NG
  schema derived from the ODD and stored at `schemas/tei-episodic-profile.rng`.
  The repository does not bundle TEI Stylesheets or an XSLT 2.0 processor, so
  the schema is curated in lockstep with the ODD to keep CI deterministic. When
  TEI tooling is available, maintainers should regenerate the schema via Roma
  or TEI Stylesheets and verify that it remains byte-for-byte identical before
  updating the checked-in artifact.

- The `tei-xml` crate exposes `relax_ng_schema()` and
  `write_relax_ng_schema(path)` so callers can retrieve or materialize the
  embedded schema before invoking external validators such as `jing`.

- **CI-based external validation via jing**: The CI pipeline validates emitted
  XML against the TEI Episodic Profile Relax NG schema using `jing`. This
  external validation complements the internal Rust validation by confirming
  that the XML output matches the formal ODD-derived schema, detecting any
  drift between the embedded schema and the emitter logic, and providing
  assurance that third-party tools will accept the generated XML.

  The workflow generates fixtures at CI time rather than committing XML files,
  ensuring fixtures always reflect the current data model. A dedicated binary
  (`generate-fixtures`) produces documents exercising all profile features:

  1. Minimal document (header only)
  2. Document with paragraphs
  3. Document with utterances and speaker references
  4. Comprehensive document with all features (synopsis, speakers, languages,
     annotation systems, revision history, mixed body content)

  This strategy keeps the repository clean (no committed XML files) while
  ensuring comprehensive coverage. The generated XML includes the TEI namespace
  declaration required for schema validation, which is added by the fixture
  generator since `quick-xml`'s serde integration does not emit namespace
  declarations.

- **Round-trip validation**: As mentioned, one key validation is that
  converting from XML to JSON and back (or vice versa) yields the same content.
  The suite includes property-based tests using Rust's `proptest` crate to
  generate random but valid structures and ensure that
  `emit_xml(parse_xml(original_xml))` is equivalent to a canonicalized
  `original_xml`. JSON and MessagePack idempotence is also tested. This
  provides confidence in the correctness of the (de)serializers.

  Property-based tests using `proptest` complement the example-based tests by
  generating arbitrary valid `TeiDocument` instances and verifying round-trip
  invariants:

  - **JSON round-trip**: `struct -> JSON -> struct` produces equal values
  - **MessagePack round-trip**: `struct -> bytes -> struct` produces equal
    values
  - **XML round-trip**: `struct -> XML -> struct` produces semantically equal
    values
  - **Cross-format consistency**: JSON and MessagePack decode to equal values

  The `proptest` strategies respect validation constraints: generated titles
  are non-empty, identifiers contain no whitespace, and documents pass
  `TeiDocument::validate()`. This ensures that edge cases (long strings, nested
  inline elements, documents with many blocks) are exercised without triggering
  validation failures that would obscure serialization bugs. The strategies are
  depth-limited for recursive structures like `Hi` elements to prevent stack
  overflow during generation.

- **Evolution and Versioning**: The JSON format is versioned via published JSON
  Schema snapshots. Consumers should pin validations to a specific
  `schemas/tei-document.schema.vX.Y.Z.json` artifact (or the root schema `$id`)
  to ensure the accepted shape matches the workspace version that produced the
  payload. If the Serde layout requires a breaking change, the project will
  publish a new schema snapshot and document the compatibility story. The Rust
  library may eventually add explicit version fields and migration helpers, but
  the current contract is the schema snapshot itself.

- **Database considerations**: If storing documents in a Postgres JSONB column,
  deployments are encouraged to validate payloads against the published schema
  snapshot as early as possible (application layer, ingestion pipeline, or
  database constraints where available). Since TEI identifiers may be used as
  keys in related tables, the system should enforce proper generation of IDs
  (for example via UUIDs or nanoid) to avoid collisions.

Overall, the validation strategy is **pragmatic**: do as much as is reasonable
in Rust (fast, on-the-fly checks), rely on external proven tools for full
schema compliance when necessary, and continuously test round-trip correctness.
By controlling the subset and documenting it, the project mitigates much of
TEI’s notorious complexity.

## Performance Characteristics

Several aspects of performance have been touched on, but to summarize:

- **XML Parse/Emit Performance**: Using `quick-xml` with Serde is very fast and
  memory-efficient for XML. It avoids building a DOM; instead it streams
  through the input once to deserialize and once to serialize. Benchmarks of
  quick-xml indicate it can parse tens of megabytes of XML per second on a
  single core (exact numbers depend on hardware and data). Our subset’s
  structure (with potentially many small text nodes and frequent tags for
  utterances and spans) is a good fit for quick-xml.

- **Memory usage**: A fully parsed `TeiDocument` will use memory proportional
  to the XML size (plus overhead for pointers, enum tags, etc.). Rust’s memory
  usage should still be quite modest (likely on the order of 2-3x the XML text
  size, since it stores each string content and also allocates for each
  struct). If an episode transcript is, say, 100KB of text, the in-memory Rust
  struct might be a few hundred KB. This is fine for desktop/server
  environments. If processing gigabyte-scale inputs becomes necessary, the
  streaming interface would be the solution.

- **Python FFI overhead**: By minimizing the number of crossing points (i.e.,
  calling into Rust once with a large payload, rather than for every small
  piece of data), the architecture drastically reduces overhead. Approaches
  that use one big serialization (like MessagePack) scale well with data size.
  The alternative approach (passing Python data structures directly) would
  involve many Python C-API calls (each field access or creation crossing the
  FFI boundary), which can be 10x or more slower for large nested data.
  Therefore, `tei-rapporteur` defaults to the efficient path whenever possible.

- **Concurrency**: The Rust code doesn’t use global state and can be made
  thread-safe. PyO3 by default ensures that extension functions acquire the GIL
  when needed and release it during heavy Rust computations (if marked
  accordingly). The binding should release the GIL while parsing or emitting so
  that Python threads are not blocked if, for example, parsing a huge file.
  This can be done with the `Python::allow_threads` context in PyO3 around the
  call into quick-xml.

- **Parallelism**: Users could potentially parse multiple TEI files in parallel
  threads (from Python or Rust) since the code is thread-safe (no shared
  mutable state except perhaps some global config). Each parse will use one
  core; the implementation has not introduced any internal parallelism for a
  single parse (it’s usually not necessary given the speed, but large files
  could theoretically be split among threads by divisions).

- **Annotation overhead**: In scenarios like Bromide, where thousands of
  `<span>` annotations for clichés might be added, note that stand-off markup
  in the model is just a list of `Span` objects. Handling thousands of those is
  trivial in Rust. The serialization overhead of adding them is also linear.
  The design should ensure the storage of spans (like using `Vec<Span>`) is
  efficient for lookups if needed. If one needed to frequently find which
  utterance a span refers to, an index map could be built, but that’s likely
  outside core parsing (more in analysis tools).

- **Comparison and Diff**: Because the output XML is canonicalized, simple text
  diffing might still produce noise (due to differences in position of line
  breaks, etc.). The roadmap includes a small utility `tei-diff` that compares
  two TEI XML files in a semantic way (ignoring irrelevant differences). This
  isn't a direct performance issue, but it helps in testing and perhaps in user
  workflows to quickly identify meaningful differences between versions of a
  script.

In summary, `tei-rapporteur` is engineered to handle real-world podcast script
sizes comfortably and to integrate into a pipeline with minimal overhead. For
typical usage (scripts of a few thousand words, dozens of annotations),
performance will be near-instant for parse/emit (on the order of milliseconds).
Even for larger inputs (transcripts of hours of audio, say 100k+ words), it
should be within a couple of seconds to parse on modern hardware. The design
choices, such as using `msgspec` and binary serialization for the Python
bridge, are geared towards keeping the system scalable: small overhead for
small data, and linear scaling for large data with a low constant factor. There
is no magical zero-copy – data does get copied at boundaries – but those copies
are predictable and efficient in Rust (memcpy of a buffer, etc.), and the
architecture avoids repeated conversions.

## Example Usage

This section provides a consolidated look at how a developer might use the
`tei-rapporteur` library from both Rust and Python, demonstrating the
end-to-end workflow.

### Rust Usage Example

Suppose a developer wants to build a simple Rust program that reads a TEI file,
ensures it’s valid, and prints out all utterances with their speakers.

```rust
use tei_core::{TeiDocument, TeiError};
use tei_xml; // assuming tei_xml provides parse_tei function

fn main() -> Result<(), TeiError> {
    let xml_input = std::fs::read_to_string("episode.tei.xml")
        .expect("Failed to read file");
    // Parse XML into Rust struct
    let mut doc = tei_xml::parse_tei(&xml_input)?;        // TeiDocument
    doc.validate()?;                                      // ensure internal consistency
    // Iterate through utterances (assuming body contains a list of utterances for simplicity)
    for u in doc.text.body.u.iter() {                     // Let's say text.body.u: Vec<Utterance>
        let speaker = u.speaker.as_deref().unwrap_or("Narrator");
        let content_text = u.content.iter().map(|inline| {
            match inline {
                Inline::Text(t) => t.clone(),
                Inline::Hi(hi) => hi.content.join(""), // simplistic: join content of <hi>
                _ => "".to_string()
            }
        }).collect::<String>();
        println!("{}: {}", speaker, content_text);
    }
    // Modify the document (e.g., add a revision note in header)
    if let Some(revDesc) = doc.tei_header.revision_desc.as_mut() {
        revDesc.add_change("Validated and printed by tei-rapporteur example");
    }
    // Emit back to XML
    let xml_output = tei_xml::emit_tei(&doc)?;
    std::fs::write("episode_out.xml", xml_output).expect("Failed to write output");
    Ok(())
}
```

In this snippet:

- The program parses the XML via `tei_xml::parse_tei`. This uses quick-xml under
  the hood and returns a `TeiDocument`.

- The code calls `validate()` to perform extra checks (which could return an
  error if something is wrong).

- The structured data (here iterating utterances) is then used to do something
  useful – in this case, print each utterance. This shows how natural it is to
  work with the Rust structs; no manual XML traversal, just normal Rust struct
  access.

- The example modifies the document (e.g., add a revision entry).

- Finally, the program emits the modified document back to XML and saves it.

The Rust API is designed to be intuitive for Rust developers, with typical
patterns (an error type for fallible functions, iterators for lists of things,
optional fields for optional XML components, builder methods where appropriate,
etc.). A Rust user does not need to know anything about Python or msgspec –
those concerns are completely absent in `tei-core` and `tei-xml`.

### Python Usage Example

For Python, assume the following `msgspec.Struct` classes are defined matching
the TEI model (this could appear in the library’s documentation or a separate
package):

```python
import msgspec

class Utterance(msgspec.Struct):
    id: str
    speaker: str | None = None
    content: list[str | "Hi"]  # mixed content: strings and Hi elements

class Hi(msgspec.Struct):
    rend: str | None = None
    content: list[str] = []    # content inside <hi>

class Episode(msgspec.Struct):
    id: str
    title: str
    utterances: list[Utterance]
    spans: list[dict] = []     # spans as dicts with keys id, start, end, ana
```

Usage in Python:

```python
import tei_rapporteur as tei

# Load and parse an existing TEI XML file
with open("episode1.tei.xml", "r", encoding="utf-8") as f:
    xml_data = f.read()
doc = tei.parse_xml(xml_data)            # parse into Document
tei.validate(doc)                        # ensure it's valid (raises exception if not)

# Convert to the Episode struct for easier manipulation
episode_json = tei.to_json(doc)          # get JSON text (or bytes) for the doc
episode = msgspec.json.decode(episode_json, type=Episode)

# Suppose a Bromide analysis should run on the episode content:
# (Pseudo-code for Bromide, which produces a list of spans indicating cliches)
cliche_spans = run_bromide_analysis(episode)   # returns list of {"id": ..., "start":..., "end":..., "ana":...}
episode.spans.extend(cliche_spans)

# Convert the updated episode back to TEI
# The script sends it through Rust to regenerate XML with the new spans in <standOff>
new_doc = tei.from_struct(episode)       # accept msgspec Struct, converts internally
tei.validate(new_doc)                    # validate again after modifications
new_xml = tei.emit_xml(new_doc)
with open("episode1_annotated.xml", "w", encoding="utf-8") as f:
    f.write(new_xml)
```

In this scenario:

- The workflow parsed an XML file into a `Document` (which has the content
  inside, but not directly accessible to Python except via the API calls).

- It immediately turns that into JSON via `to_json` and decodes into the
  `Episode` struct. The result is a fully Python-native object `episode` that
  can be passed to Bromide.

- After adding Bromide’s output (spans) to the episode, the workflow generates
  updated TEI by calling `from_struct(episode)` to go back into Rust. Under the
  hood, as described, this serializes `episode` to bytes and Rust deserializes
  it.

- Rust validates and emits new XML.

- Finally, the script saves the new XML, which now contains the `<standOff>`
  with spans added by Bromide.

## Implementation status (November 2025)

The `msgspec.Struct` projections now ship with the crate as the
`tei_rapporteur.structs` submodule. `Episode`, `TeiHeader`, `FileDesc`,
`ProfileDesc`, `EncodingDesc`, `RevisionDesc`, `TeiText`, `TeiBody`,
`Paragraph`, `Utterance`, `Hi`, and `Pause` mirror the serde field names used by
`tei-core` (for example, `teiHeader`, `fileDesc`, `@xml:id`, `$value`, and the
externally tagged `p`/`u` body blocks). The Python source lives alongside the
crate and is embedded into the extension at load time, avoiding a rename of the
existing `tei_rapporteur` module while still registering a real submodule.
MessagePack emitted by `to_msgpack` decodes directly into these classes via
`msgspec.msgpack.decode(payload, type=Episode)`, and encoding the structs feeds
the bytes straight back into `from_msgpack`.

The above Python code shows how seamlessly a workflow can move between TEI (for
interchange/audit) and a Python JSON-friendly form (for analysis and
manipulation). Direct interaction with XML libraries is unnecessary, and the
runtime keeps the XML consistent automatically.

## Conclusion

`tei-rapporteur` is designed as a robust bridge between the rich structured
world of TEI and the agile world of Python data processing. By carefully
selecting a useful subset of TEI P5 and leveraging Rust’s performance and type
safety, it provides a reliable core for managing podcast scripts and
annotations. The PyO3 wrapper and `msgspec.Struct` integration ensure that
Python tools (be it data science notebooks or production pipelines) can use
this core without friction, enjoying high-level abstractions and low overhead.

Key design takeaways:

- **Single Source of Truth**: All parsing, serialization, and validation logic
  lives in one place (Rust), preventing divergence between systems.

- **Separation of Concerns**: The Rust crate structure isolates concerns (data
  model, XML I/O, Python FFI) so that each can be developed and tested in
  isolation, and developers can work on core logic without Python knowledge and
  vice versa.

- **Semantic Round-tripping**: The project prioritizes semantic fidelity of data
  over exact XML byte fidelity, enabling normalization that simplifies
  comparisons and ensures consistency. At the same time, the design leaves room
  for a future lossless mode if needed.

- **Performance and Scaling**: Using modern libraries like quick-xml and
  msgspec, the library scales from tiny snippets to large transcripts. The
  choice of binary boundaries and bulk operations keeps overhead low.

- **Extensibility**: Through the TEI ODD mechanism and JSON versioning, the
  model can evolve. Annotations for new analysis types (Chiltern, Ascorbic) can
  be added with minimal disruption, using stand-off markup or additional fields
  as appropriate. The code structure (with optional features for streaming,
  etc.) allows adding advanced functionality without affecting the stable core.

By grounding all podcast-related tooling on TEI-Rapporteur, the architecture
ensures that Episodic (the script generator) and analysis modules like Bromide
speak the same language – a structured, interoperable format. This design not
only fosters code reuse (one parser to serve all needs) but also provides
**auditability**: an episode script can be exported as TEI XML with all
annotations (clichés, pronunciation notes, accent metrics) embedded, offering
transparency into what the algorithms did. This outcome is expected to be
invaluable for debugging and for end-users who want to see a “report” of the
script’s stylistic and linguistic properties.

In summary, TEI-Rapporteur is the cornerstone of a modern, multi-language
tooling stack for spoken content creation and analysis. It marries the rich
semantics of the TEI standard with the performance of Rust and the flexibility
of Python, enabling developers and content creators to work with complex
structured documents in a straightforward and efficient way. The design
outlined here will guide the implementation and ensure that the project builds
a library that is both powerful and pleasant to use, for years of maintainable
code and extensible features to come.

**Sources:**

- ChatGPT Project Stack Strategy (discussion on TEI toolkit design and
  integration)

- ChatGPT Rust/PyO3 Integration Strategy (detailed patterns for bridging Rust
  and Python, and TEI-specific considerations)

[^1]: <https://journals.openedition.org/corpus/4553?lang=en#:~:text=32%E2%97%8B%20,describe%20a%20%E2%80%9Cspeech%20event>
[^2]: <https://www.tei-c.org/Vault/P5//2.4.0/doc/tei-p5-doc/en/html/ref-pron.html#:~:text=P5%3A%20Guidelines%20for%20Electronic%20Text,s%29%20of%20the%20word>
