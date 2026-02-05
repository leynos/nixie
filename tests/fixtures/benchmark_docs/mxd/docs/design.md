# Design Document: Marrakesh Express Daemon (MXD)

## Architecture: Hexagonal (Ports & Adapters) Design

MXD is structured with a **Hexagonal Architecture** (ports-and-adapters) to
clearly separate the core domain logic from external concerns. The **domain
core** (business logic) is encapsulated in the `mxd` library crate, while
integration with I/O (networking, database, etc.) is handled by adapter layers
on the perimeter. This approach makes the system easier to extend and test, as
domain operations can be exercised in isolation from their environment.

- **Domain Core**: The `mxd` library contains the core Hotline protocol logic,
  including command parsing, session management, and domain models for users,
  chat, news, and files. This core is free of networking or database specifics;
  for example, it defines how a login command is validated, or how a news
  post’s threading links are managed, without assuming *how* the data is
  transmitted or stored. By keeping this logic in a library, it can be reused
  by multiple front-ends. (In fact, the project plans to factor out all
  protocol handling into the library so it can be driven by different
  servers([1](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/migration-plan-moving-mxd-protocol-implementation-to-wireframe.md#L22-L31)).)

- **Transport Adapter (Wireframe Router)**: MXD’s networking is implemented as
  a transport adapter using the **Wireframe** library. Instead of a custom TCP
  loop, the `wireframe` crate acts as the **port** for the Hotline binary
  protocol. It handles socket I/O with Tokio, decodes and encodes the
  Hotline-specific frames, and dispatches incoming messages to the appropriate
  domain handler via a routing
  table([2](https://github.com/leynos/wireframe/blob/fa6c62925443e6caed54866a95d3396eb8fa78a2/README.md#L35-L43)
   )(
  [1](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/migration-plan-moving-mxd-protocol-implementation-to-wireframe.md#L140-L148)).
   Each Hotline “transaction” type (a message or request identified by an ID)
  is mapped to a handler function. For example, the Login transaction (Hotline
  ID 0x006B) is routed to a `handle_login` handler in the domain core. The
  Wireframe adapter thus plays the role of the **primary port** on the inbound
  side: it translates raw TCP byte streams into high-level domain commands and
  routes them. Likewise, it takes domain responses and frames them back into
  Hotline protocol packets on the outbound side. This clean separation means
  the domain core doesn’t need to know about sockets or byte order; it just
  implements handlers for events like “user login request” or “post news
  article,” and the wireframe layer delivers those events.

- **Storage Adapter (Diesel ORM)**: Persistence is achieved via Diesel, serving
  as a database adapter. The domain defines repository-like functions (e.g.
  “create new user”, “list files for user”) which are implemented with Diesel
  queries under the hood. The `mxd` core code uses Diesel in a way that
  abstracts over the specific SQL backend – thanks to Diesel’s traits –
  enabling **dual database support** (SQLite and PostgreSQL) from one codebase.
  The Diesel layer is our **outbound port** for data persistence: the domain
  logic calls methods like `create_user()` or `list_article_titles()`, which in
  turn use Diesel to execute SQL against whichever database is configured. By
  constraining all DB access to this adapter module (`mxd::db` and associated
  Diesel `models`), we maintain a boundary: if we needed to swap out or modify
  the storage layer (say, use an in-memory store for testing or add caching),
  we could do so by adjusting this adapter without touching core logic.

- **Adapter Boundaries & Extension**: The hexagonal design means each adapter
  is loosely coupled to the core via well-defined interfaces. For example, the
  **transport** side could be extended – today it’s a TCP server using
  Wireframe, but one could imagine adding a websocket or HTTP interface as
  another adapter that translates external input to the same core command
  handlers. Similarly, the **storage** adapter could be extended to support new
  databases or a caching layer (e.g. using Redis, as discussed later) by
  implementing the same operations using a different mechanism. The rationale
  is to allow MXD to evolve (or be customized) at the edges – new protocols,
  new storage backends – without rewriting domain code. It also aids testing:
  the core logic can be tested with in-memory or stub adapters. In practice,
  MXD’s project structure already reflects this: core modules like `login`,
  `news`, `file` handling, etc., are in the library, and the main binary (and
  upcoming `mxd-wireframe-server` binary) just binds these to real network and
  DB
  instances([1](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/migration-plan-moving-mxd-protocol-implementation-to-wireframe.md#L22-L31)).

### Shared server module

To satisfy the bootstrap milestone in the roadmap, the legacy CLI and Tokio
listener were extracted from `src/main.rs` into the reusable `mxd::server`
module. `mxd::server::cli` now owns the `Cli`, `AppConfig`, and administrative
subcommand types so every binary parses the same configuration surface, while
`mxd::server::legacy` hosts the Tokio accept loop, connection handler, and
`create-user` workflow. The public `mxd::server::run()` entry point parses
arguments and dispatches commands, leaving `src/main.rs` as a thin shim. This
keeps the domain crate free of `wireframe::*` imports and guarantees that
upcoming binaries (for example, `mxd-wireframe-server`) can embed the same
domain logic without duplicating setup code. The change is protected by new
`rstest` fixtures around `AppConfig::load_from_iter` plus `rstest-bdd`
behaviour tests covering successful and failing `create-user` invocations.

### Runtime selection via feature flags

The bespoke Tokio loop is now gated behind a `legacy-networking` Cargo feature.
When the feature is enabled (the default for compatibility) the `mxd` binary
continues to call `legacy::dispatch()` and exposes the classic accept loop.
Disabling the feature with `--no-default-features` removes the legacy frame
handler from the build: `server::run()` delegates to the Wireframe runtime and
the `mxd` binary is omitted via `required-features`.

Administrative commands (`create-user`) live in `server::admin::run_command` so
both runtimes share identical behaviour without depending on the legacy
adapter. Unit- and behaviour-level tests assert the feature gate:
`active_runtime()` reports `Legacy` when the feature is present and `Wireframe`
when it is not, ensuring the adapter strategy in
`docs/adopting-hexagonal-architecture-in-the-mxd-wireframe-migration.md` can be
enforced without leaking transport details into domain code.

The runtime boundary is illustrated below:

```mermaid
classDiagram
    class NetworkRuntime {
        +Legacy
        +Wireframe
    }
    class AppConfig {
        +argon2_m_cost: u32
        +argon2_t_cost: u32
        +argon2_p_cost: u32
        +database: String
        +default()
    }
    class Commands {
        +CreateUser(args: CreateUserArgs)
    }
    class CreateUserArgs {
        +username: Option<String>
        +password: Option<String>
    }
    class Admin {
        +run_command(command: Commands, cfg: AppConfig): Result
        +argon2_from_config(cfg: AppConfig): Argon2
    }
    class Legacy {
        +dispatch(cli: Cli): Result
        +run_daemon(cfg: AppConfig): Result
    }
    class Wireframe {
        +run_with_cli(cli: Cli): Result
    }
    class Server {
        +run(): Result
        +run_with_cli(cli: Cli): Result
        +active_runtime(): NetworkRuntime
    }
    Server --> "1" Admin : uses
    Legacy --> "1" Admin : uses
    Wireframe --> "1" Admin : uses
    Server --> "1" Legacy : dispatches (if legacy-networking)
    Server --> "1" Wireframe : dispatches (if not legacy-networking)
    Admin --> "1" AppConfig : config
    Admin --> "1" Commands : command
    Commands --> "1" CreateUserArgs : args
```

**Layering and Responsibilities**:

- *Domain layer*: Contains entities like `Session` (session state), `Command`
  (parsed client requests), and domain services (e.g. login verification, news
  thread management). It defines **ports** – abstract operations needed from
  outside (like retrieving a user record or broadcasting a chat message).

- *Transport layer*: Implements **inbound ports** using Wireframe. It receives
  raw connections, enforces the 12-byte Hotline handshake and 20-byte frame
  structure, then calls into domain logic. For example, the handshake preamble
  is handled by a Wireframe `Preamble` implementation that reads the “TRTP”
  magic bytes and version, then invokes a success or failure callback in which
  the domain’s handshake logic
  runs([1](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/migration-plan-moving-mxd-protocol-implementation-to-wireframe.md#L54-L63)
   )(
  [1](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/migration-plan-moving-mxd-protocol-implementation-to-wireframe.md#L70-L79)).
   After handshake, Wireframe uses a custom **Serializer** to parse each
  incoming frame’s header and payload into a message struct, then uses a router
  to call the correct
  handler([1](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/migration-plan-moving-mxd-protocol-implementation-to-wireframe.md#L92-L101)
   )(
  [1](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/migration-plan-moving-mxd-protocol-implementation-to-wireframe.md#L140-L148)).
   The handlers are async functions in the domain core, which produce a
  response message that Wireframe then encodes via the serializer and sends.
  This adapter thus shields the domain from networking details like timeouts,
  fragmentation, etc., while ensuring each domain action is triggered by the
  correct protocol event.

The Wireframe bootstrap now uses preamble callbacks to mirror the legacy
handshake semantics: successful decodes write the 8-byte Hotline reply,
validation failures map to Hotline error codes, and idle sockets trigger
`preamble_timeout`, which invokes `on_preamble_decode_failure` to emit the
8-byte Hotline timeout reply before the connection is closed after the
five-second `HANDSHAKE_TIMEOUT`. This keeps the domain free of
transport-specific error handling while preserving Hotline’s observable
behaviour. Upgrading to `wireframe` v0.1.0 removed the temporary vendored fork;
the runtime now leans on `on_preamble_decode_success`,
`on_preamble_decode_failure`, and `preamble_timeout` to emit Hotline reply
codes and apply the five-second idle cap without local patches.

The handshake handler now also captures the negotiated sub-protocol ID and
sub-version in a task-scoped registry keyed by the Tokio task running the
connection. The Wireframe app builder copies that metadata into per-connection
state and app data so downstream routing can gate compatibility shims (for
example, Synapse Hotline X (SynHX) vs Hotline 1.9 quirks) without reparsing the
preamble. Entries are cleared on connection teardown to prevent leakage between
sessions while keeping the data available for the full lifetime of each
connection.

### Exclusive OR (XOR) text-field compatibility

Some Hotline clients (including SynHX) obfuscate text parameters by exclusive
OR (XOR)-ing each byte with `0xFF`. The wireframe adapter maintains a
per-connection exclusive OR (XOR) compatibility flag (`XorCompatibility`).
Inbound routing (`process_transaction_bytes`) decodes parameter payloads: when
any text field is invalid UTF-8 but becomes valid after exclusive OR (XOR), the
connection is marked as XOR-enabled and the payload is rewritten with decoded
bytes. Outbound replies are re-encoded in `HotlineProtocol::before_send`
whenever the flag is enabled, ensuring responses match the client expectation.
Only text-bearing fields (login, password, data/message, news
path/title/poster, news data flavor/body, and file names) are transformed;
numeric and binary payloads are left untouched.

The handshake sub-version does not yet provide a reliable XOR signal, so
detection is heuristic and locked in per connection after the first successful
XOR decode. This keeps the domain layer free of client quirks while allowing
future handshake-based toggles to replace the heuristic when the mapping is
known.

- *Storage layer*: Implements **outbound ports** (database operations) using
  Diesel. For instance, the domain core may call
  `db::get_user_by_name(username)` which is implemented with a Diesel query
  against the `users`
  table([3](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/login.rs#L31-L39)).
   The domain doesn’t issue SQL directly; it calls methods in the DB adapter
  that return domain types (like a `User` struct) or results. This layer also
  includes migration management and database connection pooling. By abstracting
  the DB operations in functions, it’s conceivable to swap them out (for
  testing or for a different store) by providing an alternate implementation of
  those functions.
- *Outbound transport and messaging*: Domain handlers use the
  `server::outbound::{OutboundTransport, OutboundMessaging}` traits to emit
  replies and push notifications without importing `wireframe` types. The
  `ReplyBuffer` adapter captures per-request replies for both the legacy and
  wireframe runtimes. The wireframe adapter provides
  `WireframeOutboundMessaging`, backed by `PushHandle` queues and a registry
  keyed by `OutboundConnectionId`, and registers per-connection push handles
  during `WireframeProtocol::on_connection_setup` so outbound delivery stays at
  the adapter boundary.

Overall, this hexagonal architecture provides clear **extension points**: new
protocol commands can be added by creating new domain handlers and registering
new routes in the Wireframe adapter (no need to change low-level loop logic),
new storage backends can be added via Diesel’s compile-time features (as we do
for SQLite vs Postgres), and auxiliary interfaces (like an admin CLI or a
monitoring API) could interact with the same domain functions. This modular
design aligns with the project’s goal of evolving into a full Hotline server
while remaining maintainable.

## Routing and Configuration Management

### Wireframe-Based Protocol Routing

To manage the complexity of a custom binary protocol (Hotline), MXD employs the
**Wireframe** library to drive its server-side routing. Wireframe provides an
Actix-web-style API but for arbitrary binary
protocols([2](https://github.com/leynos/wireframe/blob/fa6c62925443e6caed54866a95d3396eb8fa78a2/README.md#L3-L12)
 )(
[2](https://github.com/leynos/wireframe/blob/fa6c62925443e6caed54866a95d3396eb8fa78a2/README.md#L30-L38)).
 In MXD, we use Wireframe to handle all incoming TCP connections and route
decoded messages to handler functions based on their **transaction ID**.

**Protocol-Level Routing**: In a Hotline server, each client request is
identified by a numeric transaction type (e.g. 107 for Login, 200 for file
list, etc.). Wireframe allows us to declare routes for these IDs in a
declarative way. Conceptually, MXD’s Wireframe setup looks like:

```rust
WireframeServer::new(|| {
    WireframeApp::new()
        .with_preamble::<HotlineHandshake>()             // custom handshake for "TRTP" protocol
        .serializer(HotlineFrameSerializer)             // custom frame parser for 20-byte header
        .route(TransactionType::Login, handle_login)
        .route(TransactionType::GetFileNameList, handle_get_file_list)
        .route(TransactionType::PostNewsArticle, handle_post_news)
        // ... routes for other transactions ...
        .wrap(LoggingMiddleware)                        // e.g. log each request
})
.bind(cfg.bind_address)?
.run()
.await?;
```

In this design, the **WireframeApp** is configured with a custom
`HotlineHandshake` preamble and a `HotlineFrameSerializer`. The handshake
ensures that when a new client connects, the first 12-byte greeting (`"TRTP"`
magic and version info) is read and validated before proceeding. Wireframe
invokes the handshake handler, which checks the protocol ID and version and
sends the appropriate 8-byte reply (error code 0 for success, or an error code
for mismatches). This logic mirrors the custom handshake previously implemented
with raw Tokio (reading from the socket and writing `HANDSHAKE_OK` or an
error), but now it is encapsulated as a formal **Preamble** in the adapter. The
implemented `HotlinePreamble` (`src/wireframe/preamble.rs`) performs this
validation during bincode decoding so Wireframe halts malformed connections
before any routing occurs. Success/failure reply hooks remain separate work in
the handshake step.

#### Handshake sequence

```mermaid
sequenceDiagram
    actor Client
    participant WireframeListener
    participant WireframeApp
    participant HotlinePreamble
    participant ProtocolModule

    Client->>WireframeListener: open_tcp_connection()
    WireframeListener->>Client: read_12_byte_preamble()
    WireframeListener->>HotlinePreamble: BorrowDecode.borrow_decode(reader)
    HotlinePreamble->>ProtocolModule: parse_handshake(preamble_bytes)

    alt valid_preamble
        ProtocolModule-->>HotlinePreamble: Handshake
        HotlinePreamble-->>WireframeListener: HotlinePreamble
        WireframeListener->>WireframeApp: start_routing_messages(Handshake)
        WireframeApp-->>Client: process_hotline_requests()
    else invalid_preamble
        ProtocolModule-->>HotlinePreamble: HandshakeError
        HotlinePreamble-->>WireframeListener: DecodeError
        WireframeListener--X Client: close_connection()
    end
```

After a successful handshake, Wireframe switches to framed message mode. We
implement a **Serializer** that knows how to read and write Hotline
**transaction frames**: it reads the 20-byte header (which includes flags,
transaction type, transaction ID, payload lengths, etc.) and aggregates
fragments if a message is split across multiple TCP
segments([1](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/migration-plan-moving-mxd-protocol-implementation-to-wireframe.md#L100-L108)
 )(
[1](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/migration-plan-moving-mxd-protocol-implementation-to-wireframe.md#L109-L118)).
 This serializer produces a high-level `Envelope` or message struct (carrying
the transaction type and payload bytes). Wireframe then uses the message’s
transaction type as a key to dispatch to a handler function. We register each
supported transaction with a handler via `.route()`, using our
`TransactionType` enum to enumerate the
IDs([5](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/transaction_type.rs#L41-L50)
 )(
[5](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/transaction_type.rs#L61-L70)).
 For example, a `Login (0x006B)` message is routed to the `handle_login`
function. Inside that handler (implemented in the domain module `login.rs`),
the logic will verify credentials and produce a response struct.

The **response** is then automatically serialized by Wireframe: our handler
returns a type implementing Wireframe’s `Responder` trait (essentially
containing a reply transaction ID, maybe some data), and the serializer wraps
it into the Hotline frame format to send
back([2](https://github.com/leynos/wireframe/blob/fa6c62925443e6caed54866a95d3396eb8fa78a2/README.md#L129-L137)).
 This means the domain handler can simply return a result (e.g. a `LoginOk`
response with server info, or an error), and the adapter handles converting it
to the `FrameHeader` + payload bytes for the client. All the low-level tasks
like enforcing max payload size (1 MiB limit, for example) and frame
consistency checks (matching total size and actual data length, no duplicate
field IDs, etc.) are done in one place – the serializer – rather than scattered
in every
handler([1](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/migration-plan-moving-mxd-protocol-implementation-to-wireframe.md#L124-L132)).

**Advantages**: Using Wireframe for routing provides a clear structure and
reuse of common features:

- *Timeouts*: The handshake and possibly overall connection idle timeouts can
  be managed via Wireframe or Tokio without cluttering domain
  code([1](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/migration-plan-moving-mxd-protocol-implementation-to-wireframe.md#L82-L89)).

- *Middleware*: We can attach middleware in the pipeline (for logging, error
  handling, etc.) similar to web
  frameworks([2](https://github.com/leynos/wireframe/blob/fa6c62925443e6caed54866a95d3396eb8fa78a2/README.md#L40-L48)).
   For instance, a logging middleware can tag each incoming request with the
  connection ID and user, sending structured logs (which can feed into journald
  as described later).

- *Connection State*: Wireframe allows storing per-connection state (like our
  `Session` struct) and making it available to handlers via extractors. We plan
  to integrate `Session` (containing user ID, permissions, etc.) as shared
  state so that handlers can easily access the authenticated user and, for
  example, broadcast messages to all connected sessions via Wireframe’s
  connection management.

- *Parallelism*: The server runs on Tokio with a pool of worker tasks (by
  default equal to CPU
  cores)([2](https://github.com/leynos/wireframe/blob/fa6c62925443e6caed54866a95d3396eb8fa78a2/README.md#L46-L54)).
   Wireframe will distribute different connections or even different requests
  to these workers, allowing concurrent processing of multiple client commands.
  This is essential for chat (multiple messages interleaved) and file transfers.

**Current Status and Transition**: Initially, MXD had a bespoke TCP loop (seen
in `main.rs`) that accepted connections and spawned a task for each, manually
reading from a `BufReader` and writing with
`BufWriter`([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L239-L249)
 )(
[4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L313-L322)).
 The plan (as per the migration design) is to replace this with the
Wireframe-based server in a new binary
(`mxd-wireframe-server`)([1](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/migration-plan-moving-mxd-protocol-implementation-to-wireframe.md#L22-L31)).
 The legacy `handle_client` function reads a transaction from the socket,
parses it, and calls `handle_request()` to process
it([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L344-L353)).
 With Wireframe, that logic is refactored: `handle_request()` and the `Command`
enum remain in the domain as the core command
dispatcher([6](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/handler.rs#L34-L42)),
 but the reading/writing of frames will be done by Wireframe’s runtime.
Essentially, each route in Wireframe will call an equivalent of
`handle_request` for that specific command. For now, both approaches coexist
(the code uses the old loop), but the design ensures a smooth switchover: once
the Wireframe server is feature-complete, the old loop will be
removed([1](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/migration-plan-moving-mxd-protocol-implementation-to-wireframe.md#L40-L43)).

#### Wireframe bootstrap implementation (November 2025)

The `mxd-wireframe-server` binary now delegates to `mxd::server::wireframe`,
which wraps `wireframe::server::WireframeServer` instead of the bespoke Tokio
loop. The runtime funnels every invocation through the shared `Cli`/`AppConfig`
types so the legacy daemon and the new binary expose identical configuration
surfaces (flags, environment overrides, and `ortho-config` defaults).
Configuration loading lives inside `WireframeBootstrap`, a small struct that
resolves the textual `bind` argument into a `SocketAddr`, records the parsed
state, and applies the Wireframe accept-loop back-off defaults.

Each worker receives a `WireframeApp` via a closure that captures an
`Arc<AppConfig>` and registers it as shared application data. This keeps the
merged configuration accessible to future Wireframe extractors (for example,
handshake handlers or push queues) without inventing an extra wrapper type. The
adapter therefore still conforms to the ports-and-adapters plan: the Wireframe
runtime owns transport-level concerns (listener binding, accept-loop back-off,
lifecycle logging) while the domain code remains unaware of the new binary.
Future tasks will extend the bootstrapper to register the Hotline handshake,
serializer, and transaction routes, but those components now have a concrete
entry point rather than a design-only placeholder.

Figure 1 illustrates how the bootstrapper ties together CLI parsing,
configuration state, and the Wireframe runtime components.

```mermaid
classDiagram
    class WireframeBootstrap {
        +SocketAddr bind_addr
        +Arc<AppConfig> config
        +BackoffConfig backoff
        +prepare(config: AppConfig) Result<WireframeBootstrap>
        +run() Result<()>
    }
    class AppConfig {
        +String bind
        +String database
        +default()
    }
    class Cli {
        +AppConfig config
        +Option<Command> command
        +parse() Cli
    }
    class WireframeServer {
        +new(app_factory) WireframeServer
        +accept_backoff(backoff: BackoffConfig) WireframeServer
        +bind(addr: SocketAddr) WireframeServer
        +local_addr() Option<SocketAddr>
        +run() Result<()>
    }
    class WireframeApp {
        +default() WireframeApp
        +app_data(config: Arc<AppConfig>) WireframeApp
    }
    WireframeBootstrap --> BackoffConfig
    WireframeBootstrap --> AppConfig : prepare(config)
    WireframeBootstrap --> WireframeServer : run()
    WireframeServer --> WireframeApp : new(app_factory)
    WireframeApp --> AppConfig : app_data(config)
    Cli --> AppConfig
    Cli --> Command
```

#### Transaction framing codec (December 2025)

The `src/wireframe/codec/mod.rs` module implements `BorrowDecode` and `Encode`
for `HotlineTransaction`, enabling the wireframe transport to decode the
20-byte header and reassemble fragmented payloads according to
`docs/protocol.md`, and to emit outbound frames in the same wire format as the
legacy runtime. The codec wraps validation logic and multi-fragment reassembly
into a single decoding pass.

**Header validation.** Each frame header is validated against protocol
constraints before payload processing begins:

- `flags` must be `0` for protocol version 1.8.5.
- `total_size` must not exceed `MAX_PAYLOAD_SIZE` (1 MiB).
- `data_size` must not exceed `MAX_FRAME_DATA` (32 KiB).
- `data_size` must not exceed `total_size`.
- A zero `data_size` with non-zero `total_size` is invalid (except for empty
  frames where both are zero).

**Multi-fragment reassembly.** When `data_size < total_size`, the codec reads
continuation frames until the full payload is assembled. Each continuation
frame must maintain header consistency with the first frame (same `flags`,
`is_reply`, `type`, `id`, `error`, and `total_size`). The codec validates that
continuation data does not exceed the remaining byte count.

**Outbound encoding parity.** `HotlineTransaction` implements `bincode::Encode`
by emitting one or more physical frames. Payloads larger than `MAX_FRAME_DATA`
(32 KiB) are fragmented using the same header-stamping rules as
`TransactionWriter` (copying the base header and adjusting `data_size` per
frame). Fragmentation happens after the full payload has been assembled; the
codec does not stream or fragment during parameter serialisation.

For parameter-centric payloads, the codec exposes constructor helpers that
reuse `transaction::encode_params`, ensuring there is a single implementation
of parameter serialisation. `encode_params` validates the per-field constraints
of the parameter block (for example, counts and field lengths must fit into
`u16`), while the codec validates overall payload size limits
(`MAX_PAYLOAD_SIZE` for the assembled payload and `MAX_FRAME_DATA` per frame)
before emitting wire frames.

To match the legacy encoder for shared cases, an empty parameter list produces
an empty payload (rather than a two-byte parameter block containing a zero
parameter count).

**Wireframe frame codec.** The `src/wireframe/codec/frame.rs` module provides a
`HotlineFrameCodec` that implements wireframe's `FrameCodec` trait. The server
configures `WireframeApp::with_codec` to use this framing, so the 20-byte
Hotline header is handled inside the wireframe connection pipeline while
preserving transaction type routing and payload validation.

**Testing strategy.** The codec is tested at four levels:

1. **Unit tests** (`src/wireframe/codec/tests.rs`) use `rstest` to cover
   single-frame and multi-fragment decoding with parametrised test cases, and
   to validate that outbound encoding emits the expected frame structure.
2. **Behaviour-Driven Development (BDD) scenarios**
   (`tests/features/wireframe_transaction.feature`) express acceptance criteria
   in Gherkin syntax, with step definitions in `tests/wireframe_transaction.rs`.
3. **BDD parity scenarios**
   (`tests/features/wireframe_transaction_encoding.feature`) assert that
   transactions encoded through the wireframe codec match the byte-for-byte
   output of the legacy `TransactionWriter` for shared parameter payloads.
4. **Property tests** (`tests/wireframe_transaction.rs`) use `proptest` to
   verify that valid single-frame and multi-fragment transactions decode
   correctly, and that all invalid length combinations are rejected.

**Class diagram.** The following diagram shows the relationship between the
codec, transaction types, and validation logic:

```mermaid
classDiagram
    class FrameHeader {
        +u8 flags
        +u8 is_reply
        +u16 ty
        +u32 id
        +u32 error
        +u32 total_size
        +u32 data_size
        +from_bytes(bytes: u8_array) FrameHeader
    }

    class HotlineTransaction {
        <<impl BorrowDecode>>
        <<impl Encode>>
        -FrameHeader header
        -Vec_u8 payload
        +request_from_params(ty: u16, id: u32, params: FieldId_pairs) HotlineTransaction
        +reply_from_params(req: FrameHeader, error: u32, params: FieldId_pairs) HotlineTransaction
        +header() &FrameHeader
        +payload() u8_slice
        +into_parts() (FrameHeader, Vec_u8)
    }

    class Transaction {
        +FrameHeader header
        +Vec_u8 payload
    }

    class TransactionError {
    }

    class transaction_module {
        <<module>>
        +HEADER_LEN : usize
        +MAX_FRAME_DATA : usize
        +MAX_PAYLOAD_SIZE : usize
        +encode_params(params: FieldId_pairs) Vec_u8
        +validate_payload_parts(header: FrameHeader, payload: u8_slice) TransactionError
    }

    HotlineTransaction --> FrameHeader : has
    Transaction --> FrameHeader : has
    transaction_module --> Transaction : validates
    transaction_module --> TransactionError : returns
    HotlineTransaction ..> transaction_module : encodes params
```

The `HotlineTransaction` struct returned by decoding provides access to the
validated header and reassembled payload, ready for dispatch to command
handlers.

#### Streaming transaction framing (December 2025)

Buffered decoding and writing are appropriate for the parameter-centric Hotline
transactions currently implemented, but file transfers and large news posts can
exceed the 1 MiB buffered cap. To support those cases without exhausting
memory, MXD now surfaces an incremental streaming API at the protocol framing
layer.

**Streaming reader.** The `src/transaction/reader.rs` module introduces
`TransactionStreamReader` and `StreamingTransaction`. Callers may start a
transaction stream and consume fragments via `next_fragment()`, receiving each
`TransactionFragment` with its per-fragment header and byte slice. The stream
retains the first header for consistency checks and tracks the remaining byte
count so callers never need to buffer the complete payload.

**Streaming writer.** `TransactionWriter::write_streaming` accepts a prepared
`FrameHeader` (with `total_size` already set) plus an `AsyncRead` source. The
writer reads at most `MAX_FRAME_DATA` bytes at a time, stamps `data_size`
accordingly, and emits each fragment sequentially. Unlike the buffered
`write_transaction` path, the streaming writer does not validate parameter
structure because file transfers use raw bytes. Both buffered and streaming
writers clamp the configured `max_frame` to `MAX_FRAME_DATA` so that emitted
frames remain within the limit enforced by `read_frame`, and both flush once
the full transaction has been written so buffered sinks (for example, test
harnesses) observe the final fragment promptly.

**Limits and safety.** Buffered readers and the Wireframe codec continue to
default to `MAX_PAYLOAD_SIZE` (1 MiB). Streaming readers accept a configurable
`max_total` limit (defaulting to the same cap) that can be raised by file and
news handlers. The shared `read_frame` helper now takes a `max_total` argument
and enforces `data_size <= MAX_FRAME_DATA`, preventing oversized fragments even
when total payload limits are relaxed.

**Testing strategy.** The streaming framing is covered with `rstest` unit tests
in `src/transaction/reader.rs` and `src/transaction/writer.rs`, plus BDD
scenarios in `tests/features/transaction_streaming.feature` bound through
`rstest-bdd` v0.4.0. The scenarios cover successful multi-fragment streaming,
limit enforcement, and header mismatch rejection. Kani harnesses in
`src/transaction/reader/kani.rs`, `src/wireframe/codec/kani.rs`, and
`src/header_util/kani.rs` prove bounded header validation, fragment sizing, and
transaction ID echoing without panics.

#### Transaction routing middleware (December 2025)

The `src/wireframe/routes/mod.rs` module implements the transaction routing
middleware that bridges the wireframe transport to domain command handlers.
Rather than registering individual routes for each transaction type, a
`TransactionMiddleware` intercepts all incoming frames and dispatches them
through the existing `Command` dispatcher.

**Middleware architecture.** The `TransactionMiddleware` struct implements
wireframe's `Transform<HandlerService<Envelope>>` trait, wrapping the inner
handler service with transaction processing logic. This approach was chosen
over `from_fn` middleware because wireframe's `wrap()` method requires
`Output = HandlerService<E>`, not the `FnService` type that `from_fn` produces.

The middleware holds:

- `pool: DbPool` - The database connection pool for command handlers.
- `session: Arc<tokio::sync::Mutex<Session>>` - Per-connection session state
  tracking authentication and user context.
- `peer: SocketAddr` - The remote peer address captured during connection
  setup.
- `messaging: Arc<dyn OutboundMessaging>` - Outbound messaging adapter used to
  push server-initiated notifications.

**Processing pipeline.** When a frame arrives, the middleware:

1. Copies the raw frame bytes from the request envelope.
2. Uses the peer address captured during connection setup.
3. Acquires the session mutex and calls `process_transaction_bytes()` with a
   `RouteContext` that includes outbound messaging.
4. Replaces the response frame with the reply bytes.

The `process_transaction_bytes()` function implements the core routing logic:

1. Parse raw bytes into a domain `Transaction` via `parse_transaction()`.
2. Convert to a `Command` variant via `Command::from_transaction()`.
3. Build a `ReplyBuffer` and `CommandContext`, then call
   `Command::process_with_outbound()` to execute using outbound adapters.
4. Serialize the reply from the `ReplyBuffer` back to bytes.

Parse failures at any stage produce an error reply with `ERR_INTERNAL_SERVER`
(3), while unknown transaction types return `ERR_INTERNAL_SERVER`, matching the
`handle_unknown()` helper in `src/commands/handlers.rs`. Routing error replies
now use `ReplyBuilder` (`src/wireframe/routes/reply_builder.rs`), which
recovers the request header from raw bytes when possible so error replies
preserve transaction IDs and types even if the payload is malformed. The reply
builder emits structured `tracing` events for parse, command, and processing
failures with peer, transaction metadata, and error code fields.

**Session state management.** Unlike thread-local storage approaches, which
fail under Tokio's work-stealing scheduler, the middleware passes the session
`Arc<Mutex<Session>>` directly through its struct fields. This ensures the same
session state is accessible regardless of which worker thread processes a given
frame.

The `Session` struct tracks three core pieces of state:

- `user_id: Option<i32>` – The authenticated user's database ID, or `None` for
  unauthenticated connections.
- `privileges: Privileges` – A bitflags value encoding the 38 privilege bits
  defined in `docs/protocol.md` field 110. These control which operations the
  user may perform (file downloads, news posts, admin actions, etc.).
- `connection_flags: ConnectionFlags` – A bitflags value encoding the 3
  connection preference bits (refuse private messages, refuse chat invites,
  automatic response).

**Privilege enforcement.** Each handler checks the required privilege before
executing. The `Session` struct provides helper methods:

- `is_authenticated()` – Returns `true` if the session has a user ID.
- `has_privilege(Privileges)` – Checks if the session has a specific privilege.
- `require_privilege(Privileges)` – Returns `Ok(())` or a `PrivilegeError`.
- `require_authenticated()` – Returns `Ok(())` or
  `PrivilegeError::NotAuthenticated`.

When a privilege check fails, the middleware returns an error reply with the
appropriate error code:

- `ERR_NOT_AUTHENTICATED (1)` – The client must log in first.
- `ERR_INSUFFICIENT_PRIVILEGES (4)` – The user lacks the required privilege.

The `Privileges` bitflags type provides `default_user()` to return sensible
defaults for newly authenticated users (download files, read/send chat, read/
post news articles, etc.). A follow-up task will load per-user privileges from
the database once the permissions schema is introduced.

**Testing strategy.** The routing middleware is tested at two levels:

1. **Unit tests** (`src/wireframe/routes/tests`) use `rstest` to cover error
   reply construction, header field preservation, truncated input handling,
   unknown transaction type responses, and successful routing for the supported
   transaction types.
2. **BDD scenarios** (`tests/features/wireframe_routing.feature`) express
   acceptance criteria for routing behaviour, with step definitions in
   `tests/wireframe_routing_bdd.rs`.

Integration tests in `tests/file_list.rs`, `tests/news_categories.rs`, and
`tests/news_articles.rs` exercise the full routing path through the wireframe
server binary (`mxd-wireframe-server`), validating end-to-end transaction
handling.

### CLI, Environment, and File Configuration (OrthoConfig)

Configuration management in MXD is handled by the **OrthoConfig** library,
which provides a unified way to load settings from **command-line arguments,
environment variables, and configuration files**. The goal is to make running
and configuring the daemon flexible but with minimal boilerplate in code. MXD’s
`AppConfig` struct (now in `src/server/cli.rs`) defines all the configurable
settings, and OrthoConfig’s derive macro automatically wires up CLI flags, env
vars, and file parsing for those
fields([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L124-L132)
 )(
[4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L134-L142)).

Key aspects of configuration:

- **Single Source Struct**: All config options are fields in `AppConfig` (e.g.
  `bind` address, `database` URL/path, Argon2 parameters for password
  hashing)([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L124-L132)
   )(
  [4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L134-L142)).
   This struct is annotated with `#[derive(OrthoConfig)]` and a prefix
  `MXD_`([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L69-L76)).
   The prefix defines the environment variable naming convention and default
  config file name.

- **Command-line (CLI)**: Using Clap under the hood, OrthoConfig generates long
  flags for each field. For example, the `bind` field becomes `--bind` on the
  CLI, and `database` becomes `--database`. Defaults can be provided via
  attributes: in code, `bind` has
  `#[arg(default_value_t = "0.0.0.0:5500".to_string())]` which sets a default
  if not otherwise
  specified([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L126-L134)).
   The user can override these by passing arguments; e.g.
  `mxd --bind 192.168.1.1:1234` to listen on a custom address.

- **Environment Variables**: For each field, an env var is automatically
  recognized. The naming is `MXD_<FIELD>` in upper snake case by default. E.g.,
  `MXD_BIND` can be set to define the bind
  address([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L376-L384)),
   and `MXD_DATABASE` to set the database
  path([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L376-L384)).
   If the program runs without CLI args for those fields, it will fall back to
  env vars. Our tests confirm that the environment is picked up (and that CLI
  will override
  env)([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L376-L384)
   )(
  [4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L389-L396)).

- **Config File**: OrthoConfig also supports reading from a file (TOML by
  default). By convention, it looks for a file named `.mxd.toml` (since the
  prefix is "MXD", the default config file is `.mxd.toml` in the current
  directory or home directory). This file can contain keys matching the field
  names (e.g. `bind = "1.2.3.4:1111"`). MXD will merge this file into the
  configuration with lower precedence than env and CLI. In our tests, dropping
  a `.mxd.toml` with a bind address works and is loaded when no other override
  is
  given([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L398-L405)).

**Precedence Order**: The layers override each other in increasing order of
priority: **defaults < config file < environment < CLI**. That means default
values (in code or derived) can be overridden by anything in `.mxd.toml`, which
in turn can be overridden by environment variables, and finally any CLI option
explicitly given will trump
all([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L388-L396)).
 For example, if `.mxd.toml` sets `bind = "1.2.3.4:1111"` and the environment
has `MXD_BIND=127.0.0.1:8000`, running `mxd` with no flags will result in the
env value taking effect
(127.0.0.1:8000)([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L376-L384)).
 If the user runs `mxd --bind 0.0.0.0:9000`, that CLI `--bind` will override
both env and
file([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L389-L396)).

**Subcommands and Merging**: MXD’s CLI has subcommands (currently `create-user`
in addition to the main server run). OrthoConfig seamlessly merges global
config with subcommand-specific args. In `main.rs`, after parsing CLI, if a
subcommand like `create-user` is invoked, we call
`load_and_merge_subcommand_for<CreateUserArgs>(&args)` to combine any config
file or env settings that apply to that subcommand’s
options([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L156-L164)).
 This allows, for example, using env vars for subcommand inputs. The
`create-user` subcommand expects a username and password; you could set
`MXD_USERNAME` and `MXD_PASSWORD` env vars (since `CreateUserArgs` is also
OrthoConfig-derived with the same prefix) instead of passing them explicitly.
This merging ensures subcommands receive the full config context (including
things like Argon2 cost parameters from file/env, so that the password hashing
uses consistent settings).

Internally, OrthoConfig uses the Figment library to merge these sources, but
this is abstracted away. The result is that at runtime, our code simply does
`let cfg = AppConfig::load()?;` (or in tests `load_from_iter`) and gets a
populated `cfg` struct. Logging the config (via `Debug` derive) would show
exactly which values were set from where. We have unit tests to verify this
loading logic, covering env-only, CLI-over-env, and file loading
cases([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L376-L384)
 )(
[4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L389-L396)
 )(
[4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L397-L405)).

**Example**: By default, MXD listens on `0.0.0.0:5500` and uses an SQLite file
`mxd.db`
([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L128-L136)).
 If an operator wants to run Postgres, they would disable default features at
compile time and then run:

```bash
export MXD_DATABASE=postgres://user:pass@host/dbname
export MXD_BIND=0.0.0.0:6600
mxd
```

This would launch the server on port 6600 connected to the given Postgres.
Alternatively, one could create a config file `.mxd.toml` with those settings,
or pass them as CLI flags. The OrthoConfig system ensures all these methods
result in the same `cfg` object. This consistency prevents configuration from
being scattered across separate CLI parsing and manual env handling – it’s all
declared in one place.

## Database Support (SQLite and PostgreSQL via Diesel)

MXD is designed to support **both SQLite3 and PostgreSQL** as database
backends. This dual support is achieved through Diesel’s compile-time features
and a careful approach to schema and SQL differences. The selection is made at
build time: exactly one of the features `sqlite` or `postgres` must be enabled
in Cargo, and the codebase conditionally compiles for the chosen
backend([7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L161-L169)).
 Key implementation points include feature flags, migration management, a
unified schema model, and custom extensions for advanced SQL (like recursive
CTEs).

### Feature-Flag-Based Compilation

In `Cargo.toml`, two feature sets are defined – `sqlite` (default) and
`postgres` – each enabling the appropriate Diesel backend support and related
dependencies(
[7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L146-L154)
 )(
[7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L156-L164)).
 For example, the `postgres` feature includes `diesel/postgres`,
`diesel_async/postgres`, etc., while `sqlite` brings in `diesel/sqlite`,
`diesel_async/sqlite`, and also Diesel’s special SQLite extensions (like
support for the `RETURNING` clause on SQLite
3.35+)([7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L152-L160)).
 The MXD code uses `cfg_if!` to enforce that one and only one of these features
is active, otherwise it fails to
compile([8](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/lib.rs#L6-L14)).
 At compile time, a type alias `DbBackend` is set to `diesel::sqlite::Sqlite`
or `diesel::pg::Pg`
accordingly([8](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/lib.rs#L8-L13)),
 and similarly the connection pool types are aliased (`DbConnection` = SQLite
connection wrapper or
AsyncPgConnection)([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L19-L28)).
 This means throughout the code, we can use Diesel generically (e.g. calls like
`.execute(conn)` or Diesel’s DSL types) without committing to a specific
backend; the concrete types are decided by the feature flag.

From the developer perspective:

- To build for Postgres, you run
  `cargo build --no-default-features --features postgres`
  ([7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L3-L11)).
   The `--no-default-features` is necessary because `sqlite` is on by default.

- To build for SQLite, `cargo build --features sqlite` works (or simply
  `cargo build` since SQLite is
  default)([7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L12-L16)).

- The code ensures a compile-time error if neither or both features are
  enabled(
  [8](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/lib.rs#L6-L14)),
   preventing ambiguity.

This setup allows us to ship a single codebase and even a single binary
(compiled separately for each backend) that supports two very different
databases. The domain code (e.g. verifying a user’s password or posting a news
article) doesn’t change – Diesel’s abstraction layer and our own adapter
functions take care of the differences.

### Dual Migration Trees & Schema Alignment

Because SQLite and PostgreSQL have subtle differences in SQL and data types,
MXD uses **dual migration files** to maintain equivalent schema in both
systems. We maintain parallel migration directories: `migrations/sqlite/` and
`migrations/postgres/`, each containing the same sequence of migration versions
but with SQL dialect tailored to the
engine([7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L88-L97)).
 For example, migration “20250605142700_create_users” will have an
`up.postgres.sql` and an `up.sqlite.sql` file with appropriate SQL for
each([7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L90-L99)
 )(
[7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L101-L109)).
 The Diesel migration setup uses `embed_migrations!("migrations/<backend>")`
conditional on the feature flag to compile in the correct
set([7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L178-L186)).
 At runtime, we call a unified `apply_migrations()` which invokes Diesel’s
migration runner for the compiled-in
migrations([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L60-L69)
 )(
[9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L81-L89)).
 This ensures the database gets the right schema for whichever backend the
binary was built for.

We keep the **logical schema** identical between SQLite and PG, while allowing
implementation differences. For instance:

- **Auto-increment primary keys**: In Postgres we use
  `GENERATED BY DEFAULT AS IDENTITY` (the modern `SERIAL`
  replacement)([7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L43-L51)
   )(
  [7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L99-L107)).
   In SQLite, we use
  `INTEGER PRIMARY KEY AUTOINCREMENT`([7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L53-L61)
   )(
  [7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L111-L119)).
   Both achieve an auto-incrementing integer `id` column, but the SQL syntax
  differs.

- **Data types**: We choose types that map well to both. E.g. we use `TEXT` for
  strings (SQLite doesn’t enforce length, Postgres `TEXT` or `VARCHAR` works
  similarly)(
  [7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L63-L70));
  `BOOLEAN` is fine in Postgres and in SQLite it ends up as 0/1 integer
  (Diesel’s bool mapping covers
  it)([7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L65-L68));
   `INTEGER` for integer types, etc. Some types don’t line up – e.g. `BYTEA` vs
  `BLOB` for binary data – but we avoid unsupported types or handle them
  conditionally. The doc notes that JSONB or timezone-aware timestamps aren’t
  portable, so we either avoid them or supply separate SQL. For example, we
  store timestamps as plain `TIMESTAMP`/`DATETIME` in both (with UTC) rather
  than `TIMESTAMPTZ` which SQLite
  lacks([7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L73-L81)).

- **Defaults and constraints**: The migrations use values that exist in both
  engines (e.g. `DEFAULT now()` vs `DEFAULT CURRENT_TIMESTAMP` are set in
  respective
  files)([7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L80-L88)).
   Complex constraints or index expressions may need to be left out of SQLite
  or done via triggers if necessary (the docs mention things like expression
  indexes not supported in older
  SQLite)([7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L75-L79)).

By keeping the **Diesel schema definitions** (the Rust side) abstract, we
compile the same ORM code for both. For example, our Diesel `table!` for
`users` declares `username -> Text, active -> Bool, created_at -> Timestamp`
etc., which Diesel maps to appropriate SQL types on each
backend([7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L129-L138)).
 Thus the code accessing `users::table` or constructing a `NewUser` struct is
identical regardless of backend – only the compiled Diesel queries differ at
runtime. In Cargo features, we ensure that Diesel’s
`returning_clauses_for_sqlite_3_35` feature is enabled for SQLite builds so
that Diesel’s `.returning()` methods work on SQLite (which gained support for
RETURNING in version
3.35)([10](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/Cargo.toml#L52-L60)).
 In code, we often leverage this: e.g. inserting a new row and getting its `id`
back can be done with `.returning(...)` on both PG and SQLite (with that
feature)(
[9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L380-L388)
 )(
[9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L389-L398)).
 Where not available, we fall back to alternate methods (for example, in
SQLite, after an insert we can `SELECT last_insert_rowid()` as shown in our
`create_bundle`
function)([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L382-L390)
 )(
[9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L391-L399)).

**Embedded Migrations**: The migration files are embedded into the binary to
simplify deployment. At startup or user command, MXD can run
`run_pending_migrations()` through Diesel’s
API([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L64-L73)
 )(
[9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L82-L90)).
 We also expose a CLI subcommand or function to apply migrations manually
(`mxd create-user` will apply migrations before inserting the user, for
example([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L114-L122)
 )(
[4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L116-L124))).
 This ensures that whether you’re using SQLite or PG, the schema will be
up-to-date. The migration version numbers (the timestamps in filenames) are
kept identical between the two trees, so that Diesel’s migration tracking
(which just uses a numeric identifier) stays
consistent.[^diesel-migration-consistency] – this is important if a user
switches from SQLite to PG or vice versa with existing data: we want version
`00000000000003` to represent the same logical migration on both.

### Schema Design for Key Domains

The database schema covers several subsystems: **users**, **chat**, **news**,
**file sharing**, plus supporting tables for permissions. We designed the
schema to be as portable as possible between SQLite and PG, and to reflect the
hierarchical nature of Hotline data (news and files are hierarchical, chat has
relationships). Below is a brief overview of each:

- **Users**: A simple `users` table stores user accounts with fields
  `id`, `username`, `password`, and in later migrations additional flags (e.g.
  an `active` boolean, created_at
  timestamp)([7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L101-L108)
   )(
  [7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L113-L120)).
   Passwords are stored hashed (using Argon2id). The schema doesn’t store the
  Argon2 parameters per user – instead, global Argon2 parameters are in config.
  The `users` table is referenced by other tables (for example, chat messages
  have a `user_id` foreign key, file permissions reference `users`, etc.).

- **Chat**: To implement public and private chats and direct messages, we use a
  set of tables:

- `chat_rooms`: Represents a chat room or conversation. Fields include an
  `id`, `creator_id` (user who created the room), a `subject` (topic), a
  boolean `is_private`, and a `created_at`
  timestamp([11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L17-L25)
   )(
  [11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L59-L67)).
   If `is_private=false`, the room is public (anyone can join, e.g. a main
  lobby). If true, it’s invite-only (for direct chats or private group chats).

- `chat_participants`: A join table mapping users to chat rooms they are in. It
  has `chat_room_id` and `user_id` as a composite PK and records a `joined_at`
  time(
  [11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L25-L33)
   )(
  [11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L68-L73)).
   This table allows listing who is in a room and is crucial for broadcasting
  messages to the right subset of connected clients.

- `chat_messages`: Stores chat history (if we choose to persist it). Each
  message has an `id`, the `chat_room_id` it belongs to, the `user_id` of the
  sender, a timestamp `posted_at`, an `options` integer (for styling flags like
  emoticons or text color as per Hotline protocol), and the message
  `text`([11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L31-L38)
   )(
  [11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L74-L82)).
   By saving messages, we can support features like late joiners getting recent
  history or server archival of chats.

- `chat_invites`: When a user invites another to a private chat, we record an
  invite here. Fields: `id`, `chat_room_id` (the room to join),
  `invited_user_id`, `inviter_user_id`, and a
  timestamp([11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L40-L48)
   )(
  [11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L84-L92)).
   This helps implement invite flows (so that an invited user can later fetch
  pending invites, or the server can enforce one invite per user per room,
  etc.).

These tables map well to Hotline’s chat transactions. For example, when a user
creates a new private chat (Hotline “Invite to Chat” transaction 112), the
server will create a new `chat_rooms` entry with `is_private=true` and a
subject (maybe empty or default), add the inviter to `chat_participants`, and
create an entry in `chat_invites` for each invited
user([11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L101-L109)).
 If the invite is accepted (transaction 113/114 for accept/decline), we would
move the invited user into `chat_participants` (and possibly remove the invite
entry). Transactions like “Join Chat” (115) correspond to inserting a row in
`chat_participants`
([11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L102-L110)),
 and “Leave Chat” (116) to removing that row. Public chat (the main lobby)
might be represented by a well-known `chat_room` (e.g., id 1 for “Lobby” which
is not private and is created at server startup). All logged-in users might
automatically join that room, or at least can join it without invite, providing
the public chat experience.

**Direct messages** are essentially implemented as private chats between two
people. If User A wants to message User B, the server can create a `chat_room`
with `is_private=true`, and directly add both A and B as participants (skipping
an invite if we treat it as an immediate direct connection). Or, depending on
protocol, it might still go through an invite/accept flow. But from a data
standpoint, a one-to-one chat is just a `chat_room` with two participants.

The schema also supports chat **subjects** – users can set a chat topic which
updates `chat_rooms.subject`. Hotline protocol has a “Set Chat Subject (120)”
and it triggers a broadcast (119) to
others([11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L110-L114)).
 We store the subject so new joiners or persistent logs can know what the topic
was.

*Note*: Currently, these chat tables are designed and migrated but not yet
fully utilized in code (the chat commands beyond basic user presence aren’t
implemented yet). However, the design lays the groundwork for Phase 2 (presence
and chat features) as outlined in the roadmap.

- **News (Forums)**: Hotline servers support threaded message boards called
  “news.” Our schema for news is hierarchical:

- `news_bundles`: A *bundle* is a container of news categories, and bundles can
  nest (a bundle may have a parent
  bundle)([12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L29-L37)
   )(
  [12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L97-L105)).
   This reflects how Hotline allows grouping of forums. Fields: `id`,
  `parent_bundle_id` (self-referencing FK), `name` (title of the bundle), a
  GUID (Hotline often tracks a GUID for bundles/categories), and
  `created_at`([12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L29-L37)
   )(
  [12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L97-L105)).
   The nesting allows a tree of bundles.

- `news_categories`: These are the actual forums where articles (posts) live.
  Each category belongs to a bundle (or to no bundle, i.e., top-level if
  `bundle_id` is
  NULL)([12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L37-L45)
   )(
  [12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L107-L115)).
   Fields: `id`, `bundle_id`, `name`, `guid`, and two integers `add_sn` and
  `delete_sn` (these might track the last serial numbers of added/deleted
  articles for replication or client sync, according to Hotline protocol), plus
  timestamp(
  [12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L37-L45)
   )(
  [12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L107-L115)).

- `news_articles`: Each row is a post. The schema is designed to support
  threaded discussions and linear navigation:

- `id` (primary key),

- `category_id` (FK to news_categories),

- `parent_article_id` (FK to another news_articles – the post this is replying
  to),

- `prev_article_id` and `next_article_id` (FKs to sibling news_articles in a
  linked list of posts at the same level/thread),

- `first_child_article_id` (FK to the first reply, if this article has
  replies)(
  [12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L47-L55)
   )(
  [12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L119-L127)).

- Also, `title`, `poster` (username or alias of who posted), `posted_at`
  timestamp, `flags` (integer bitflags, e.g. for “sticky” or other attributes),
  `data_flavor` (the MIME type or format of the content, often `"text/plain"`
  or similar), and `data` (the content of the post, which could be text or an
  encoded
  file)([12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L48-L56)
   )(
  [12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L127-L135)).

These fields allow representing a threaded message board akin to classic
Hotline or BBS forums. We can traverse a thread by following
`first_child_article_id` down and `next_article_id` across – essentially a
manual linked list of threads. This design was chosen to mirror Hotline’s
protocol which provides prev/next links in news
listings([12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L64-L69)
 )(
[12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L150-L157)).
 For example, when the server sends a news article to a client, it includes
fields for next/prev article IDs and parent/child, so the client can navigate
threads; our DB stores these so that we can populate those fields
easily([13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L367-L376)
 )(
[13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L373-L380)).
 The trade-off is more work on insert (we need to update pointers when
adding/removing posts), but read operations for sequential browsing become
simple.

Additionally, we have **permissions** for news:

- `permissions` and `user_permissions` tables define what actions each user can
  do(
  [12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L17-L25)
   )(
  [12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L81-L89)
   ),(
  [12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L24-L28)
   )(
  [12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L90-L98)).
   Hotline had a fixed set of privilege codes (like code 20 for “News: Read
  Article”, code 21 for “News: Post Article”, etc., up to 38 distinct flags for
  various
  abilities)([12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L154-L161)).
   Instead of storing a bitmask on the user, we normalized it: a `permissions`
  table enumerates all possible privileges (with a code and name), and
  `user_permissions` is a many-to-many linking a user to each permission they
  have. There is a `scope` field in `permissions` (general vs folder vs bundle)
  to denote if a permission is global or tied to news
  specifically([12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L18-L26)
   )(
  [12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L82-L90)).
   This approach makes it easy to query which users have, say, “News Admin”
  rights, or to extend with new permissions.

The **news domain logic** uses these tables to implement operations:

- Listing news categories (Hotline transaction 370 “NewsCategoryNameList” and
  371 “NewsArticleNameList”) is done by traversing the bundle/category
  hierarchy. In the code, `list_names_at_path(conn, path)` will return all
  sub-bundles and categories under a given
  path([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L315-L323)
   )(
  [9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L324-L333)).
   If `path` is None, it returns top-level bundle and category names
  (concatenated)(
  [9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L324-L333)
   )(
  [9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L338-L346)).
   If `path` is a bundle name or nested bundle path, it finds that bundle and
  lists its sub-bundles and
  categories([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L320-L328)
   )(
  [9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L338-L346)).
   This is how a client browsing the forum structure gets the list of sections.
  Under the hood, to resolve a text path like `"Announcements/Server News"` to
  an ID, we use a path-walking query.

- Reading an article (transaction 400 “NewsArticleData”) requires locating the
  article by category and ID. The code uses
  `get_article(conn, path, article_id)` which first finds the `category_id` for
  the given path (via `category_id_from_path`) and then fetches the
  article([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L409-L417)
   )(
  [9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L414-L422)).
   If found, it then prepares the output parameters: title, poster, date,
  flags, etc., including pointers to neighboring
  articles([13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L367-L376)
   )(
  [13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L373-L380)).
   Those pointers (prev, next, parent, firstChild) are all stored in the DB
  row, making it straightforward to include them in the
  response([13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L367-L376)
   )(
  [13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L373-L380)).

- Posting an article (transaction 410 “PostNewsArticle”) involves inserting a
  new `news_articles` row. If it’s a new thread (no parent), it will have
  `parent_article_id = NULL` and we likely treat it as the “next” of the last
  article in that category (or if it’s the first, it becomes the category’s
  first post). If it’s a reply, we set its `parent_article_id` and also adjust
  the `prev/next` links: e.g., find the last existing reply under that parent
  and set this as its `next`. Our design can either be managed by triggers or
  (as currently) by application logic in the `create_root_article` and future
  `create_reply_article` functions. In the `create_root_article` code, we use a
  Diesel transaction and likely update `first_child_article_id` of the parent
  if it was
  empty([13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L213-L221)
   )(
  [13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L401-L410)),
   and ensure the new article’s `prev_article_id` is set appropriately (not
  shown above, but planned). The DB schema with recursive CTEs also allows
  another approach: we could generate thread ordering on the fly via queries if
  we didn’t store prev/next, but storing them simplifies the protocol
  implementation.

**Recursive Structures & CTEs**: Both the bundle/category hierarchy and the
threaded articles involve recursive tree structures. We leverage SQL recursive
Common Table Expressions (CTEs) to handle certain queries elegantly:

- To resolve a bundle/category path string (like `"Games/Trading"`), we use
  SQLite’s JSON1 extension and a recursive CTE. We split the path by `/` and
  use `json_each` to iterate segments, joining each segment name to a bundle
  parent->child relationship until we reach the final bundle or category. Our
  function `build_path_cte_with_conn(conn, step, body)` uses the Diesel CTE
  extension to chain a recursive CTE (the `step`) with a final selection
  (`body`)(
  [9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L444-L453)
   )(
  [9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L455-L459)).
   The Diesel crate itself doesn’t yet have first-class CTE query builders, so
  the project now depends on the published **`diesel-cte-ext`** crate (module
  name `diesel_cte_ext`) to supply the helpers. Earlier revisions carried an
  in-tree fork; on 11 November 2025 the dependency switched to the crates.io
  release (v0.1.0) to reuse upstream fixes and avoid duplicating maintenance.
  This provides a builder like `.with_recursive(cte_name, query)` that we can
  attach to Diesel’s `sql_query` results, and even a trait to extend Diesel
  connections with `.with_recursive()`
  methods([14](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/cte-extension-design.md#L3-L6)
   )(
  [14](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/cte-extension-design.md#L24-L32)).
   Using this, `category_id_from_path` constructs a CTE that recursively finds
  the nested bundle and then the category, returning the category’s ID if the
  path is
  valid([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L440-L448)
   )(
  [9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L449-L457)).
   This single query replaces what could be multiple queries or complex logic
  in Rust, and it runs efficiently inside the database. We verify on startup
  that SQLite has support for **recursive CTEs** (the code runs a quick
  `WITH RECURSIVE c(x)...` query on SQLite to ensure the build includes that
  feature)(
  [9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L134-L143)
   )(
  [9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L146-L154)).

- For threaded articles, one possible use of recursive CTE is retrieving an
  entire thread (all descendants of a root post). Currently, we store links so
  traversal can also be done client-side or with iterative queries. But the
  same recursive CTE tooling could fetch all `news_articles` under a given
  parent (common in hierarchical comment systems). The `diesel_cte_ext` helper
  would facilitate writing such a query in Rust for both SQLite and PG. This
  might be a future improvement for, say, implementing a “delete thread” (which
  needs to find all replies).

In summary, the schema is designed for *parity* with Hotline protocol
structures and *portability* across the two SQL engines. Diesel’s schema
definitions (in `schema.rs` and `models.rs`) unify the types, and we rely on
migrations to adjust for engine differences.

### Diesel CTE Extension (Advanced Queries)

Because MXD requires complex SQL (like recursive CTEs for path resolution and
potentially tree traversal), the implementation now depends on the external
`diesel-cte-ext` crate (whose Rust module is still named `diesel_cte_ext`).
This Diesel extension provides helpers for building and executing CTEs in a
backend-agnostic
way([14](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/cte-extension-design.md#L3-L6)).
 It defines a struct `WithCte` and trait impls so that a CTE query can
integrate with Diesel’s query builder as if it were a normal
query([14](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/cte-extension-design.md#L10-L18)
 )(
[14](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/cte-extension-design.md#L20-L28)).
 The connection types are also extended via a trait (`RecursiveCTEExt`) so code
can call, for example, `conn.with_recursive(cte_name, sql_query1, sql_query2)`
to chain two `sql_query` fragments for the recursive part and the final
select([14](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/cte-extension-design.md#L24-L32)
 )(
[14](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/cte-extension-design.md#L44-L50)).
 Functions like `build_path_cte_with_conn` (used in the path lookup) glue raw
SQL fragments into one query. The outcome is that the Rust code remains
database-neutral – it avoids embedding raw Postgres-specific queries in one
place and SQLite in another; the extension runs semantically equivalent CTEs on
both backends. For instance, `CATEGORY_STEP_SQL` and `CATEGORY_BODY_SQL` (not
shown above) are written in standard SQL that works on both (thanks to SQLite’s
JSON1 making it possible there). Using Diesel’s async support, we can
`.get_result(conn)` on the result of `build_path_cte_with_conn` and it will
execute the whole CTE and give us the ID we
need([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L446-L454)
 )(
[9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L455-L459)).

#### 11 November 2025 – Source `diesel-cte-ext` upstream

- Removed the in-repo `diesel_cte_ext` crate in favour of the published
  `diesel-cte-ext` v0.1.0 release so we inherit upstream bug fixes and test
  coverage.
- Upgraded `diesel` to 2.3.x and `diesel-async` to 0.7.x so MXD and the
  extension agree on backend APIs without patching Diesel twice.
- Regenerated both `Cargo.lock` files (root and `validator/`) so every binary
  consumes the same crates.io dependency graph.

### Dual-Database Migrations Example

To illustrate, here’s a simple example of the dual migration approach (from the
users table creation):

- **Postgres (`migrations/postgres/..._create_users/up.sql`)**:

```sql
CREATE TABLE users (
    id          INTEGER PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
    username    TEXT    NOT NULL UNIQUE,
    email       TEXT    NOT NULL UNIQUE,
    active      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);
```

- **SQLite (`migrations/sqlite/..._create_users/up.sql`)**:

```sql
CREATE TABLE users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    NOT NULL UNIQUE,
    email       TEXT    NOT NULL UNIQUE,
    active      BOOLEAN NOT NULL DEFAULT 0,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Both create the same columns, but use the appropriate auto-increment and
default value syntax for each
DB([7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L99-L107)
 )(
[7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L111-L120)).
 Diesel’s generated `schema.rs` for this table uses generic types (`Integer`,
`Text`, `Bool`, `Timestamp`
)([7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L131-L140)),
 so the Rust code can call `users::table.filter(users::username.eq(name))` and
it will work with either backend at runtime. The migrations for each backend
carry any non-portable details (like `now()` vs `CURRENT_TIMESTAMP`). This
pattern repeats for other migrations (news, files, chat, etc.). We ensure
referential integrity (foreign keys with `ON DELETE CASCADE`, etc.) in both.
One caveat: because SQLite has no native `ON DELETE CASCADE` on without foreign
key enforcement enabled, we must ensure to enable foreign_keys pragma (Diesel
does that by default).

With this setup, MXD can target embedded use (SQLite, with a single-file DB,
easy to deploy) as well as a heavier-duty server mode (PostgreSQL for
concurrent access, better performance at scale). The code and tests run against
both to verify correctness.

## Systemd Integration and Deployment Considerations

MXD is intended to run as a long-lived system service on Unix-like systems, so
integration with **systemd** is an important aspect of operational deployment.
We outline how to set up MXD with systemd units, use systemd features for
reliability, and integrate logging with the system journal.

### Service and Socket Units

A recommended way to deploy MXD is via a systemd service unit, possibly
accompanied by a socket unit for socket activation.

**Service Unit (mxd.service)**: This unit defines how the daemon starts and
stops. For example:

```ini
[Unit]
Description=Marrakesh Express Daemon (Hotline Server)
After=network.target

[Service]
Type=notify
ExecStart=/usr/local/bin/mxd --bind 0.0.0.0:5500 --database /var/lib/mxd/mxd.db
User=mxd
Group=mxd
Restart=on-failure
WatchdogSec=60s

[Install]
WantedBy=multi-user.target
```

Key points in this service file:

- `Type=notify` indicates that MXD will signal to systemd when it has completed
  startup (using the sd_notify protocol). MXD’s code will call
  `sd_notify("READY=1")` once it has bound to the port, connected to the
  database, run migrations, and is ready to accept clients. We use a Rust crate
  (or FFI to libsystemd) to send this notification.

- `ExecStart` launches the binary with any necessary flags. In this example, we
  explicitly specify `--bind` and `--database`, but note that these could also
  be configured via the environment (e.g., put
  `Environment=MXD_DATABASE=postgres://...` in the unit, etc.). One could also
  omit flags here if defaults or env suffice.

- `Restart=on-failure` ensures the service is restarted if it crashes. We might
  use `RestartSec` to control back-off. This pairs with watchdog monitoring.

- `WatchdogSec=60s` tells systemd that if 60 seconds pass without a watchdog
  heartbeat from MXD, systemd should consider it hung and restart it. In code,
  when Watchdog is enabled, MXD will periodically call
  `sd_notify("WATCHDOG=1")` (perhaps every 20 seconds via a Tokio interval) to
  reset the
  timer([1](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/migration-plan-moving-mxd-protocol-implementation-to-wireframe.md#L40-L43)).
   This provides a safety net against deadlocks or hangs (especially important
  for a server that should run unattended).

- `User` and `Group` run the service with restricted permissions (assuming an
  `mxd` user exists), and possibly one would set up `RuntimeDirectory=mxd` for
  placing sockets or PID files if needed.

**Socket Activation (mxd.socket)**: We can optionally use a socket unit to
start MXD on-demand when a connection comes in. A corresponding socket unit
might be:

```ini
[Unit]
Description=Marrakesh Express Daemon Socket

[Socket]
ListenStream=5500
NoDelay=true

[Install]
WantedBy=sockets.target
```

With this, systemd will listen on port 5500 and only spawn the service when a
client connects. In `mxd.service`, we would adjust `ExecStart` to not bind the
socket (since systemd passes it). We would also add `Accept=no` (since our
service handles multiple connections itself, it’s not one service per
connection). The MXD code would need to detect systemd socket activation:
typically by checking the environment variables `LISTEN_FDS` and `LISTEN_PID`
(provided by systemd). We would use `sd_listen_fds()` or similar to retrieve
the socket FD and then instruct our `TcpListener` to use it. The code would
prioritize this if available, otherwise fall back to a normal bind. This way,
MXD can support both modes. Socket activation is useful for starting on demand
or upgrading the service seamlessly.

With socket activation, one benefit is that if MXD is restarted (or crashes),
systemd can hold incoming connections in the queue until the service is ready
again, reducing dropped connections.

### sd_notify Usage in MXD

As mentioned, MXD uses `sd_notify` to integrate with systemd’s service
management:

- On startup, once the server is fully initialized (after setting up the
  listener and DB pool, and ideally after initial migrations and any other
  critical setup), the process will call `sd_notify("READY=1")`. This signals
  the `Service` is up, which systemd will record (useful if `Type=notify` is
  set).

- If the `WatchdogSec` is configured, the process must also send
  `sd_notify("WATCHDOG=1")` periodically. We plan a background task that ticks
  at half the watchdog interval, sending the heartbeat. The watchdog check
  covers both CPU hangs and logic deadlocks; if MXD stops responding (e.g.
  stuck in an infinite loop and fails to send the heartbeat), systemd will log
  and restart it.

- On clean shutdown (e.g. if we receive SIGTERM from systemd or the user stops
  the service), we can call `sd_notify("STOPPING=1")` to indicate we’re
  gracefully shutting down. Systemd will then know the service is intentionally
  going down (distinct from a crash). Our shutdown signal handler (which
  listens for SIGTERM and Ctrl+C) would trigger this before closing sockets and
  exiting.

In MXD’s code, integration is done via the `systemd` or `sd-notify` crate. We
wrap calls in a check so that if not running under systemd, those calls are
no-ops (they detect if $NOTIFY_SOCKET is set, etc.). This way, running MXD in a
dev environment doesn’t spam any errors. We also ensure the `notify` messages
include the main PID (this is handled by libsystemd’s convention of how to send
the UDP message to the notify socket).

### Logging to Journald (Structured Logging)

MXD uses the Rust `tracing` crate for logging, which supports structured,
contextual logging. When running under systemd, we want logs to appear in the
journal (`journalctl`) with key metadata (like severity, and custom fields such
as the client IP or username for certain events). Our logging strategy:

- During initialization, if the environment variable `JOURNAL_STREAM` or
  `INVOCATION_ID` is detected (indicative of running under systemd), we
  configure a `tracing` subscriber that outputs to the journal. We can use the
  `tracing-journald` layer or route through `slog` to systemd’s journal.
  Lacking a direct crate, an alternative approach is to use `libsystemd` to
  send logs (like `sd_journal_send`).

- Each log entry in code uses structured fields. For example, in `login.rs` we
  log `info!(%peer, %username, "authenticated")` on successful
  login([3](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/login.rs#L50-L58)).
   When hooked to journald, this could appear with fields `peer=<IP:port>` and
  `username=<name>` attached. In journald, these would show up as key-value
  metadata (possibly under fields like `MESSAGE` and `PRIORITY` for the main
  message and severity, and additional fields for our custom data). This makes
  it easy to filter logs (e.g.
  `journalctl -t mxd -o json | jq 'select(.USERNAME==\"alice\")'` to find
  logins by alice, etc.).

- We plan to log notable events at appropriate levels: successful logins as
  info, failed logins as warning (including the username
  attempted)([3](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/login.rs#L50-L58)),
   unexpected errors as error (with error codes or messages). The use of
  structured logging also helps correlate events: we can include a session ID
  or connection ID in each log after handshake, so that all logs from one
  client connection carry an identifier (this could be implemented via a
  `tracing::Span` per connection).

- If not under systemd, we default to console logging (possibly with ANSI
  coloring or plain text). This still uses `tracing`, just with a different
  subscriber (like `fmt::Subscriber` for stderr).

By logging to journald, MXD benefits from log rotation, centralized management,
and the ability to use `journalctl` filtering. It avoids maintaining separate
log files. Administrators can also set up rate limits or forward logs to syslog
or external systems as needed using standard systemd configurations.

### Example Journal Entry

For illustration, a journal entry for a login might look like:

```text
Dec 12 10:34:45 server mxd[1234]: authenticated peer=192.168.1.5:54321 username="alice"
```

Where `mxd[1234]` is the process name and PID, and our custom fields are
appended. If using structured logging via native journald, the underlying data
might have `PRIORITY=6` (info), `SYSLOG_IDENTIFIER=mxd`,
`CODE_FILE=src/login.rs`, `peer=192.168.1.5:54321`, `username=alice`, etc., all
separately queryable.

### systemd Socket and Shutdown Handling

In our runtime, we also integrate with systemd for socket passing and shutdown
signals:

- For socket activation, as mentioned, MXD will detect the inherited socket. We
  use `sd_listen_fds` to get the FD number and then create a `TcpListener` from
  it. The advantage is zero-downtime restarts (with `systemctl reload` + socket
  activation, one can start a new instance and then stop the old).

- On shutdown (SIGTERM), our `shutdown_signal()` async helper will break the
  accept loop and close
  gracefully([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L239-L248)
   )(
  [4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L260-L268)).
   We notify systemd we’re stopping (so it doesn’t think we crashed). The code
  ensures all connection tasks are informed of shutdown via a broadcast channel
  (`shutdown_tx`) and waits for them to finish or
  timeout([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L264-L272)).

- We also supply an example `.service` with `TimeoutStopSec` if needed (to give
  time for graceful shutdown). But since our accept loop breaks and we
  immediately stop reading new data, it’s just finishing up current
  transactions which should be quick.

### Journald Structured Logging Strategy

To implement structured logs to journald, one approach is to use **journald
logging driver**: we could integrate `libsystemd` calls directly. However, the
community-provided solution is often to use a crate. Since in our Cargo.toml we
haven’t added one explicitly, we outline the plan:

- Use `tracing` with a custom `Layer` that implements `on_event` by calling
  `sd_journal_send` with the formatted message and key-value pairs. We map
  tracing levels to syslog priorities (e.g. trace->debug, debug->debug,
  info->info, warn->warning, error->err).

- Each field in the tracing event can be sent as `KEY=value` to journald. For
  example, `peer` field becomes `peer=192.168.1.5:54321`. We might uppercase
  some keys or prefix them to avoid clashing with reserved journal fields.

- The `MESSAGE` field of the journal will contain the main log message (usually
  the static string, like "authenticated" in the login
  example([3](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/login.rs#L50-L58))).
   We ensure to include that, as journald expects a MESSAGE.

- Optionally include `CODE_FILE`, `CODE_LINE`, etc., using the meta provided by
  tracing (there’s an extension to get file/line from tracing’s metadata).

- This layer is only enabled if a journal is detected. Otherwise, we might just
  use `tracing_subscriber::fmt` for plain stdout/err logging.

The effect is that administrators get rich logs without us inventing a new
logging mechanism. All logs can be viewed with `journalctl -u mxd` and will
include context.

## Subsystems Detail

Now we break down each major subsystem of MXD – **user sessions**, **chat**,
**news**, **file sharing**, and **tracker beacons** – describing their domain
model, database schema mapping, protocol integration, and main behavior flows.

### User Sessions and Login

**Domain Model**: A **session** in MXD represents a connected client’s state.
In the code, this is the `Session` struct, which currently holds at least the
`user_id` of the logged-in
user([6](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/handler.rs#L20-L24))
 and can be extended with other session flags (like permissions, idle timer,
etc.). A new session is created when a client connects; before login it has no
user_id (the user is not authenticated). After a successful LOGIN command, we
attach the user’s ID to the session and consider it authenticated. The session
persists for the lifetime of the connection, tracking things like whether the
user has agreed to server rules, which chat room they are currently in, etc.,
as features expand.

**Database**: The `users` table is the authoritative store of user credentials.
It contains username (unique), password hash, and possibly other attributes
(the current schema includes an `active` flag and creation
timestamp)([7](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/supporting-both-sqlite3-and-postgresql-in-diesel.md#L101-L108)).
 Passwords are hashed using **Argon2id** with configurable cost
factors([15](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/README.md#L8-L16)).
 Those cost parameters (memory, iterations, parallelism) are part of the config
(`AppConfig`) so they can be tuned. They default to moderate values (e.g.
Argon2 `m_cost`, `t_cost` as provided by `Params::DEFAULT_*` in argon2 crate)
but can be raised for more
security([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L134-L142)).

**Protocol Mapping**: The Hotline protocol’s login sequence involves the client
sending a `LOGIN` transaction (Hotline ID 107) with parameters for username and
password. MXD parses this into a
`Command::Login { username, password, header }`
variant([13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L92-L101)
 )(
[13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L102-L110)).
 The `header` contains the transaction metadata (ID, etc.) which we preserve to
build the reply. The login handler (`handle_login`) will:

- Look up the user by name in the DB using
  `get_user_by_name`
  ([3](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/login.rs#L31-L39)).

- If the user exists, verify the provided password against the stored hash
  using Argon2. We use a constant-time comparison (`verify_password`) to avoid
  timing
  attacks([3](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/login.rs#L31-L39)
   )(
  [3](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/login.rs#L33-L37)).

- If authentication succeeds, we update the session:
  `session.user_id = Some(u.id)` meaning this connection is now associated with
  that
  user([3](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/login.rs#L32-L39)
   )(
  [3](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/login.rs#L34-L42)).
   The login handler also initialises `session.privileges` to
  `Privileges::default_user()`, granting the user standard capabilities
  (download files, read/send chat, read/post news articles, etc.). These
  privileges gate subsequent operations; handlers call
  `session.require_privilege(Privileges::XXX)` before executing privileged
  actions. A follow-up task will load per-user privileges from the database.

- Prepare a reply transaction. In Hotline, the login reply (transaction 100,
  which is a generic error or success message) includes either an error code or
  a parameter indicating success. The server typically sends back a “Client
  version” parameter on successful login. In our implementation, on success we
  set `error = 0` and include a parameter `FieldId::Version` with the server’s
  protocol
  version([3](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/login.rs#L34-L42)
   )(
  [3](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/login.rs#L36-L44)).
   On failure, we set `error = 1` (meaning bad username or password) and no
  payload. This logic is shown in `handle_login`: the tuple `(error, payload)`
  is `(0, params)` if auth ok, or `(1, empty)`
  otherwise([3](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/login.rs#L32-L39)
   )(
  [3](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/login.rs#L40-L48)).

- Log the attempt: a successful login emits an info log with the username and
  peer
  address([3](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/login.rs#L50-L58)),
   a failed one emits a
  warning([3](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/login.rs#L50-L58)).
   This helps auditing login attempts in the logs.

The reply is then sent to the client, who will proceed only if error=0. If
error=1, the client usually disconnects or can try again (our server keeps the
session open for multiple attempts; invalid attempts just get `ERR` replies as
per protocol).

**Session Management**: Once logged in, the session is recorded in an in-memory
list of active sessions. MXD will maintain a list/map of sessions (likely
within the Wireframe server state or a global). This allows implementation of
presence: e.g. broadcasting “user X has logged on” to others, or populating the
user list when someone requests it. Currently, not all that is implemented, but
planned:

- On login success, we would add this session to a global list and potentially
  send a “Notify New User” (transaction 300 series) to all existing users to
  inform them of the new login. Hotline protocol defines transaction 300 (User
  Login Notify), 301 (User Change), 302 (User Logout Notify), etc. Our
  `TransactionType::GetUserNameList` (300) and related types hint at
  this([5](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/transaction_type.rs#L44-L51)).
   For now, `GetUserNameList` (transaction 300) handling is stubbed (it returns
  Unknown if called), but we have plans to implement it such that it returns
  the list of currently online usernames. The `Session` struct will be extended
  with things like `nickname` (some servers allow an alias different from
  login) or status (away/idle).

- The session also holds any ephemeral data like the last activity timestamp
  (for idle timeout), a flag if the user has admin rights loaded, etc. We might
  load the user’s permission bits from the DB at login and store them in the
  session for quick checks.

- When the connection closes (either client disconnect or server shutdown), we
  mark the session as logged out: remove it from the active list and possibly
  notify others that the user went offline. This corresponds to sending
  transaction 302 (Notify Delete User) to remaining clients.

**Login Flow Summary**:

- Client connects -> handshake.

- Client sends LOGIN (username+password).

- Server validates:

- If OK: attach user to session, send reply with server info, log success,
  broadcast presence.

- If fail: send `ERR` reply (error code 1), log warning. (After 3 bad tries we
  might disconnect or ban temporarily as a security measure – could be added).

- Now session is authenticated. The user can perform other actions (join chat,
  list files, etc.) that require login. The server will check `session.user_id`
  exists for those commands, otherwise it might deny them with an error (indeed
  in `Command::GetFileNameList` handler, we do
  `if session.user_id is None { return error }` to ensure only logged-in users
  can list
  files([13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L178-L186)
   )(
  [13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L184-L191))).

In the current implementation, password creation (hashing) is done either via
the `create-user` CLI or could be via a registration protocol (not in classic
Hotline, which had no self-serve registration). The CLI `create-user` uses the
same hashing routine (`hash_password` with Argon2) and inserts into
DB([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L104-L112)
 )(
[4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L114-L122)).
 This ensures consistency – the server would reject login if the password
wasn’t hashed with the expected parameters.

Future enhancements for sessions:

- **Idle Timeout**: We may implement that if a user is idle for X minutes, mark
  them away or even disconnect. Session struct might hold a last-active
  timestamp updated on each command, and a background task checks for idle.

- **Concurrent Sessions**: The server might allow the same user to log in from
  multiple locations. In design we haven’t restricted it, but some servers
  disallow duplicate login. We could enforce uniqueness by checking in the user
  list if this user is already in an active session, and deny second login (or
  kick the first).

- **Privileges**: Use `user_permissions` table to load what commands this user
  can do (for example, only admin can delete files or see hidden dropbox
  files). Store those in session for quick access.

### Public Chat and Direct Messages

**Domain Model**: The chat subsystem deals with real-time messaging among
users. We have the concept of **Chat Rooms** (a channel or group in which users
talk) and **Chat Messages** (the actual messages sent). There can be one
special public chat (the “main” room where everyone is by default) and
potentially multiple private chats (either group or one-on-one).

In the domain, we represent a chat room as either an entry in the DB
(`chat_rooms`) or, at runtime, possibly as an in-memory structure with
references to participants’ sessions. For implementation, we likely maintain a
mapping from `chat_room_id` to a list of Session references, so we can quickly
broadcast messages to all in the room.

**Database Schema**: As described earlier, the key tables are `chat_rooms`,
`chat_participants`, `chat_messages`, `chat_invites`
([11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L11-L19)
 )(
[11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L40-L48)).
 These provide persistence for chat state:

- `chat_rooms`: Each has an `id` and optionally we could use the `id` 0 or 1
  for the main lobby. A `subject` field stores the room topic (for UI display
  and for the “notify subject change”
  protocol)([11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L20-L28)).

- `chat_participants`: Links users to rooms, essentially the membership
  list(
  [11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L25-L33)).
   We will often query this to get who is in a room to send them messages.

- `chat_messages`: Stores messages. It might not be strictly required to
  persist them (Hotline server could choose not to store history on disk), but
  we have it for completeness and potential features like scrollback or
  auditing. Each message row records who sent it and in which
  room([11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L31-L38)).

- `chat_invites`: Keeps track of invitations for private chats that have been
  issued but not yet
  accepted([11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L40-L48)).

**Protocol Mapping**: Hotline chat-related transactions include:

- `Send Chat` (ID 105) – a user sends a message to the currently joined chat
  room.

- `Chat Message` (ID 106) – this is actually the message broadcast from server
  to others in the room (the server echoes the message to participants,
  possibly including the sender depending on client behavior).

- `Invite to Chat` (112) and responses (113 accept, 114 decline).

- `Join Chat` (115) and `Leave Chat` (116).

- `Notify Chat Subject` (119) and `Set Chat Subject` (120).

- Also presence notifications 300-302 which tie into chat presence (user list,
  etc.).

Our server design aligns to these:

- When a user wants to join a public chat, if it’s the main chat, we could
  either assume everyone is in it by default after login or require an explicit
  join (Hotline clients typically join main chat automatically after login). To
  implement that, on successful login, the server might internally add the user
  to `chat_participants` for the main room and send them the current user list
  (or send everyone a notify about this new user). In Phase 2 of
  development([16](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/roadmap.md#L135-L143)
   )(
  [16](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/roadmap.md#L137-L145)),
   we plan to implement broadcasting user join/leave events (transactions
  301/302) to all logged-in users. The `USER_NAME_LIST_ID` constant (300) is
  used when a client requests the list of users, which we’ll respond to by
  enumerating all sessions in the main chat or all online
  users([5](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/transaction_type.rs#L5-L13)
   )(
  [5](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/transaction_type.rs#L44-L51)).

- For private chats: When user A invites B (transaction 112), the server will
  create a new `chat_rooms` entry with `creator_id=A` and
  `is_private=true`([11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L48-L55)),
   and add A to participants. Then it sends an invite notification to B. In
  protocol, B receives an Invite (maybe as an out-of-band or a specific
  transaction). We map that to perhaps sending B a message type “Invite” with
  the room ID and inviter info. We also store an entry in `chat_invites` so if
  B is offline or doesn’t accept immediately, it’s tracked. If B accepts (113),
  the server adds B to `chat_participants` for that room, sends ack to A, and
  possibly a room join broadcast. If B declines (114), we delete the invite
  entry and notify A (for example, send a decline message).

- Sending a message: If user X sends text in room Y (client issues transaction
  105 with message data), the server receives it via `Command::ChatMessage` (we
  would have a Command variant for it, similar to how
  `Command::PostNewsArticle` works). The server then creates a `chat_messages`
  entry (persisting the text) and broadcasts a `Chat Message (106)` to all
  other participants of room Y. In the broadcast, it will include the sender’s
  user ID or name and the message text, following protocol format. Using
  Wireframe, this could be done by having a route for 105 that calls a handler
  which then enqueues responses to each participant. Wireframe’s push/broadcast
  facility (perhaps using the `PushQueues` API) can send a frame to multiple
  connections.

- Chat subject change: When someone sets a new subject (transaction 120), the
  server updates `chat_rooms.subject` in the DB and broadcasts a Notify (119)
  with the new subject to all in the
  room([11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L110-L114)).
   This keeps everyone in sync and persists the topic for newcomers.

**Behavior Flows**:

- **Login to main chat**: After login, MXD could auto-join the user to the main
  lobby. This would entail sending them the current user list (simulate a 300
  transaction reply listing all users) and sending everyone else a “User
  Joined” notification (transaction 301) with their username. The chat schema
  is not directly used for main lobby presence if we treat “online users” the
  same as “participants of lobby”. We might decide that chat_room 1 = Lobby,
  and then every logged-in user is implicitly a participant there. If so, we
  can sync the `chat_participants` table with the user sessions: on login add,
  on logout remove.

- **Chat messaging**: Suppose Alice and Bob are in the Lobby. Alice sends
  “Hello” (tx 105). The server’s handler sees Session(user_id=Alice). It looks
  up which room Alice is in (could be stored in session, or by checking
  chat_participants). If in Lobby (room 1), it creates a ChatMessage record
  (room=1, user_id=Alice, text="Hello") and then sends a Chat Message (106) to
  Bob (and all in room 1). The message likely contains Alice’s user ID and the
  text. If we have a push queue per connection (Wireframe allows per-connection
  outbound buffers), we could simply call a broadcast utility. Or manually
  iterate over all sessions in room 1 and call something like
  `session.send(Message::ChatMessage { from: Alice, text:"Hello" })`. Because
  our server is async, these sends happen concurrently. Bob’s client then
  displays “Alice: Hello”.

- **Direct message flow**: Alice right-clicks Bob -> “Private Chat”. Her client
  sends Invite New Chat (112 with Bob’s ID). Server allocates new chat_room
  (say id 5) with subject empty, private=true. Adds Alice to participants.
  Creates invite entry for Bob. Sends Bob an Invite notification (likely a
  transaction telling “Alice invites you to chat id 5”). If Bob accepts, his
  client sends Accept Invite (113 referencing that chat id). Server on
  receiving that will add Bob to chat_participants (room 5, user B). Then send
  both A and B a special message indicating the chat is formed – possibly the
  protocol has them both automatically join, or maybe after accept the server
  sends a “Join Chat (115)” on their behalf. In implementation, we might just
  have both now in participants and we send them each a user list containing
  each other. From now on, any message either sends with tx 105 in that context
  (likely clients include a chat_id in the message) will only be broadcast to A
  and B. If one leaves (116), server sends notify to the other and removes from
  participants, possibly deletes the room if empty.

- **Synchronization**: We ensure that any change to chat state in memory is
  reflected in DB if persistence is desired. For example, if a user disconnects
  unexpectedly, the server will treat it as leaving all chats: remove from
  `chat_participants` in DB for each room they were in (so that the DB state
  isn’t wrong). If we want persistent chatrooms (that remain even if empty,
  like channels), we keep the `chat_rooms` entry around. If not, we might
  delete a chat_room when the last person leaves (for private ephemeral chats).
  The schema has `ON DELETE CASCADE` on participants and invites referencing
  rooms, so deleting a room auto-cleans participants and
  invites([11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L24-L32)
   )(
  [11](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/chat-schema.md#L48-L55)).

- **Direct vs Group**: The design allows group chats with more than 2
  participants by simply inviting multiple people to the same room. The
  `chat_invites` table can hold multiple invites for the same room (one per
  user invited). When each accepts, they join that room’s participants. This
  covers moderated group chats or multi-user private chats.

**Session and Presence Integration**: The chat subsystem overlaps with session
presence. The **user list (presence)** in Hotline is often considered part of
chat/lobby: a client typically has a user list showing who’s online. When
someone goes away or disconnects, those are broadcast. MXD will implement this
by treating the main lobby as the space where presence is broadcast.
Transactions 300 (request list) and the 301/302 (notifications) correspond to
this. Our plan:

- 300 GetUserNameList: client asks for the list, server replies with a list of
  all usernames currently online. We can generate this from active sessions or
  by querying `chat_participants` of the Lobby room.

- Server-initiated 301 (Notify Add User): when someone logs in, send others a
  message with their username and maybe user ID. Similarly 302 (Notify Remove
  User) on logout. The code to do this will likely live in the login/logout
  flow, not in chat per se, but it’s part of the “presence” subsystem which is
  closely tied to chat.

**Idle/Away**: Hotline also had “idle” and “away” statuses (with transactions
303 to update user flags). We foresee adding fields to Session like `is_away`
or `idle_since`. Possibly integrate with chat by broadcasting a status change
when someone toggles away.

In summary, the chat subsystem enables real-time communication:

- The **domain logic** handles membership and message distribution (with
  possible persistence).

- The **DB schema** secures consistency (especially important for long-running
  rooms or admin logging of chats).

- The **protocol handlers** ensure that every relevant client is notified of
  events (joins, leaves, messages, invites, subjects). We leverage Wireframe to
  do multi-cast easily, but carefully: we might have to iterate over
  connections since Wireframe’s routing is request/response oriented. However,
  Wireframe provides a `PushQueue` concept for unsolicited
  messages([2](https://github.com/leynos/wireframe/blob/fa6c62925443e6caed54866a95d3396eb8fa78a2/README.md#L143-L151)
   )(
  [2](https://github.com/leynos/wireframe/blob/fa6c62925443e6caed54866a95d3396eb8fa78a2/README.md#L152-L160)).
   We can obtain a handle for each connection to push outgoing frames to it
  asynchronously. For example, when a new chat message arrives, the server can
  push a frame to all other connections’ low-priority queue (since chat is not
  as urgent as say file data). The design calls for possibly separating high vs
  low priority outbound queues (for example, file transfer data might use high
  priority, chat messages low, to avoid starving control
  messages)([2](https://github.com/leynos/wireframe/blob/fa6c62925443e6caed54866a95d3396eb8fa78a2/README.md#L152-L160)
   )(
  [2](https://github.com/leynos/wireframe/blob/fa6c62925443e6caed54866a95d3396eb8fa78a2/README.md#L168-L175)).

### News Reading and Threaded Discussions

**Domain Model**: The news subsystem acts like a message board. Users with
appropriate permissions can post articles (forum posts) in various categories,
reply to existing posts, and read threads. The domain needs to manage
hierarchical data (threads) and enforce permissions (some users may only read,
others can post or moderate).

**Database Schema**: We elaborated the schema in the DB section:

- `news_bundles` and `news_categories` form a hierarchy of forums (with bundles
  possibly nesting). This is like a folder tree of forums.

- `news_articles` holds the actual posts, with linking fields for threading
  (parent, first_child, next,
  prev)([12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L66-L69)).
   Essentially it’s a threaded tree where each root post is a thread, and
  replies link off that.

- Permissions: `permissions` (with codes for things like “News: Read”, “News:
  Write”, “News: Moderate”) and `user_permissions` linking users to those
  permissions(
  [12](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/news-schema.md#L154-L161)).
   The server will use these to decide if a user can read or post in a certain
  category. Possibly the `scope` field (general vs bundle vs folder) means some
  permissions apply to specific bundles or categories, but our current design
  doesn’t have a direct link of permission to a category. We may interpret
  “folder” scope as meaning a permission is required to read a private bundle,
  etc. Implementation might simply be: if a category is meant to be private, we
  check membership in some group or permission in user_permissions.

**Protocol Mapping**:

Key Hotline transactions for news include:

- `NewsCategoryNameList (370)`: client requests the list of categories (and
  bundles) given a “path”. The server should return a list of names (both
  bundle names and category names) that are one level below the given path. In
  our commands, `GetNewsCategoryNameList` corresponds to
  this([13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L113-L121)).
   The code uses `list_names_at_path(path)` to fetch the
  names([13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L201-L209)
   )(
  [13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L291-L299)).
   The reply payload is a set of entries labeled as `FieldId::NewsCategory` for
  bundle names and possibly also categories (the client differentiates by
  context or separate calls).

- `NewsArticleNameList (371)`: client requests list of article titles in a
  given category (path). Our `GetNewsArticleNameList` command triggers
  `handle_article_titles(path)`
  ([13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L204-L212)
   )(
  [13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L316-L324)).
   That uses `list_article_titles(conn, path)` to get all titles of root posts
  in that category (i.e.,
  threads)([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L465-L473)
   )(
  [9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L474-L481)).
   We filter `news_articles` where `parent_article_id IS NULL` (root posts) and
  category matches, then return titles sorted by date or ID.

- `NewsArticleData (400)`: client requests the content of a specific article
  (by ID) and its thread context. Our `GetNewsArticleData` command calls
  `handle_article_data(path, article_id)`
  ([13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L206-L214)),
   which uses
  `get_article(path, id)`([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L409-L417))
   to fetch the article row including all its fields and then prepares a
  payload with fields: title, poster, date, flags, data (the text), and the
  various thread linkage IDs (prev, next, parent,
  firstChild)([13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L367-L376)
   )(
  [13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L373-L381)).
   Those linkage fields (prev, next, etc.) were stored in the DB when the
  article was inserted, so we just convert them to bytes and include if
  present. This gives the client everything needed to display the post and
  navigation (e.g., “Next Article” button).

- `PostNewsArticle (410)`: client posts a new article. This includes
  parameters: path (where to post), title, content (data), and some flags
  (Hotline allowed flags like whether the post is global or has attachments,
  etc.). Our `PostNewsArticle` command handler is
  `handle_post_article(path, title, flags, flavor, data)`
  ([13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L214-L221)
   )(
  [13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L401-L410)).
   This will:

- Determine which category to post in via the path (get category_id).

- Insert a new `news_articles` row with that category_id. If it’s a reply (the
  path might indicate not just category but a parent thread? Actually, in
  Hotline, replies are posted by specifying the parent ID in the request, not
  in the path. The protocol likely uses separate transaction for reply vs new
  thread, but here 410 might be used for both, differentiating by whether a
  parent ID is present in flags or so).

- If the protocol didn’t have a separate code for reply, then perhaps
  `PostNewsArticle` is used for both new thread and reply, and the parameters
  include whether it’s a reply (maybe via a *parent ID* field). However, in our
  command parsing, we treat PostNewsArticle as requiring a path and a bunch of
  fields, but not explicitly a parent ID (we did parse NewsParentId in
  `handle_article_data` but not in post). Possibly, we assume if posting to a
  path that includes a thread, but our path concept stops at category.
  Alternatively, maybe Hotline posts all go to a category (threads are not
  separate categories), and reply vs new is determined by if a *NewsParentId
  field is included* in the transaction. If so, we might need to extend
  `Command::PostNewsArticle` to carry an optional parent ID (currently it
  doesn’t in code, but maybe flags or DataFlavor indicates reply).

- For simplicity, assume `PostNewsArticle` creates a **new thread** (no
  parent). We have separate not-yet-implemented handling for replies (Hotline
  might have a different ID for replies or uses the same with a parent param).

- After insert, Diesel’s returning can give us the `id` of the new
  post(
  [13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L414-L422)).
   We return that as a parameter `NewsArticleId` to the
  client([13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L416-L423)),
   so the client knows the new post’s ID (Hotline protocol expects the server
  to respond with the ID of the created post).

- If it were a reply, we’d also need to update the `news_articles` links: set
  the `parent_article_id` on the new post, and adjust `prev/next` links among
  siblings. This logic would be similar, just a different Diesel query (which
  we plan under a `create_reply_article` function, likely).

- **Delete or moderate** functions: Not explicitly requested in prompt, but in
  design maybe a moderator can delete posts, which would involve removing an
  article and possibly all its replies (which could be done with a recursive
  CTE to collect all descendant IDs and deleting them). Our schema has
  `ON DELETE CASCADE` on these self-refs so deleting an article would cascade
  to its children? Actually, we did not specify CASCADE on `parent_article_id`
  FKs (we could but in schema snippet it’s not explicitly stated). We likely
  handle deletion manually to avoid accidentally wiping large threads
  unintentionally.

**Key Behavioral Flows**:

- *Listing forums*: The client on startup might fetch the root level
  bundles/categories (370 with no path). The server returns all top-level
  bundle names and
  categories([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L324-L333)
   )(
  [9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L338-L346)).
   The client populates the UI (say it shows “General” category,
  “Announcements” bundle, etc.). If a bundle is present, the client might
  request inside it (370 with path = that bundle) to get sub-categories. Our
  implementation of `list_names_at_path` handles both None (root) and bundle
  name cases by either filtering on `parent_bundle_id` being null or matching
  the resolved
  bundle([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L320-L328)
   )(
  [9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L336-L344)).
   We do a case-insensitive or exact match depending on how we store names
  (likely case-sensitive unique). The use of `bundle_id_from_path` resolves a
  bundle chain to an
  ID([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L319-L327)).
   This uses the JSON + CTE trick to walk the path
  segments([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L440-L448)
   )(
  [9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L449-L457)).
   We produce one combined list of bundle names and category names and send
  back. The client will typically display bundles and categories in one list
  (Hotline client shows folders for bundles and file icons for categories).

- *Reading threads*: The client picks a category and asks for articles (371).
  We respond with titles of all root articles
  (threads)([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L470-L478)).
   The client might display them in a list. Then the user selects a thread (or
  first article) and client asks for it (400 with that ID). We send the full
  article
  data([13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L367-L376)
   )(
  [13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L373-L381)).
   If the article has a `first_child_id`, the client knows there are replies;
  it may then request the first child (400 with that ID), and then subsequent
  `next_article_id` until none. Or the server might proactively send a batch
  (but Hotline likely uses client-driven pulling of each post). We might
  optimize by sending multiple at once, but to stick to protocol, we likely
  send one article per request.

- *Posting new thread*: User enters text and hits post in category X. Client
  sends 410 with path "X", title, content. Server calls `create_root_article`:

- If user lacks permission to post (not in user_permissions for News Write), we
  would return an error (likely in the reply header error code). Perhaps error
  code 1 or a specific one for “permission denied.” We’d need to define that as
  the protocol doesn’t define many error codes beyond generic ones, but we can
  choose one (maybe reuse handshake error 1 for generic fail, but better to
  define a code).

- If allowed, we insert the row. In SQLite/PG, our function returns the new
  ID(
  [13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L416-L423)).
   We craft a reply with error=0 and a parameter `NewsArticleId = <new_id>` so
  the client can, for example, immediately fetch it or display it.

- We also might broadcast a notification to other users in that category:
  Hotline servers often notify connected users that a new article was posted
  (especially if they subscribe or if the category is open). The protocol might
  not auto-push new posts, but since many Hotline clients only poll when you
  click, we might not broadcast spontaneously. Possibly for chat there is
  broadcast, for news maybe not.

- *Posting a reply*: Would be similar but include identifying the parent. If
  the protocol uses the same 410 transaction with an extra field for parent ID,
  we would handle that accordingly (not currently in code, but could be
  extended).

- We would update the parent’s `first_child_article_id` if it was None (meaning
  it was the first reply), and otherwise find the last sibling and update its
  `next_article_id` to the new post’s ID, and set new post’s `prev_article_id`
  to that sibling. This maintains the linked list of replies.

- The code already populates these fields in handle_article_data replies,
  indicating the design expects them to be
  correct([13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L367-L376)).

**Recursive CTE usage**: One advanced flow is resolving a category path to an
ID, which we covered via `category_id_from_path` and the CTE that iterates
through bundle
hierarchy([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L425-L434)
 )(
[9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L440-L448)).
 This allows commands to specify a path string like `"Announcements/Updates"`
to post or list in nested categories. The JSON approach (preparing a JSON array
of path segments and using `json_each` in a recursive query) is a clever
portable solution that works on both SQLite (with JSON1) and PG (with native
JSON functions). It saves writing engine-specific code or multiple queries.

**Permissions enforcement**: At the time of listing or reading or posting, the
server should enforce:

- If a category is supposed to be private (e.g., an admin-only forum), normal
  users shouldn’t even see it in the list. We could enforce that by checking
  user permissions during `list_names_at_path` – perhaps by filtering out
  categories or bundles that the user lacks access to. Our current
  implementation of `list_names_at_path` does not do such filtering yet; it
  returns all
  names([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L330-L338)
   )(
  [9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L339-L346)).
   To implement, we could join with a permission mapping (if we had a table
  mapping category to required perm). Or simpler: define that some categories
  are hidden if user doesn’t have a specific permission code in
  `user_permissions`. E.g., category “Staff” might require permission code 30
  (News Admin).

- For reading posts, similarly check before returning data. For posting, check
  before inserting.

We have not yet coded those checks, but the architecture supports it via the
`user_permissions` table. We would retrieve the current user’s permissions on
login (join user->user_permissions->permissions to get a list of codes) and
stash them in Session. Then each news request handler can quickly verify if the
required code is in session’s permissions. This avoids DB hit on every request.

In summary, the news subsystem is like a mini discussion forum inside MXD. It
heavily leverages hierarchical data and thus the recursive CTE in queries, and
ensures thread relationships are maintained via fields and server logic. The
Diesel CTE extension was largely justified by this subsystem, to allow
resolving nested paths with a single query on both backends.

### File Sharing (Files, Aliases, and Resumable Transfers)

**Domain Model**: The file-sharing subsystem emulates the classic Hotline
server file services: a shared file repository with folders, files, and special
folders like “drop boxes” and “upload folders.” Users can browse directories,
download files, upload files, create folders, delete or rename items if they
have permissions, and create aliases (shortcuts) to files or folders. A key
aspect is support for **resumable** transfers, meaning if a download or upload
is interrupted, it can continue from where it left off instead of restarting
entirely – critical for large files over slow connections.

**Storage Abstraction**: Instead of tying to local filesystem, MXD uses an
**object storage abstraction** via the Rust `object_store`
crate([17](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/file-sharing-design.md#L46-L54)
 )(
[17](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/file-sharing-design.md#L70-L78)).
 This means that file content is stored in a backend-agnostic way (could be
local disk, Amazon S3, Azure Blob, etc.) – essentially as opaque byte streams
identified by keys. The server does not rely on OS filesystem semantics (like
hierarchical directories), because object stores treat everything as a flat key
space. We therefore implement the folder hierarchy at the **metadata layer** in
the database, not by actual nested directories on disk. Every file or folder
gets a unique key or ID, and for files we store an `object_key` that the
object_store backend uses to locate the bytes. If using local disk, the
`object_key` might be a filepath under a root directory; if using S3, it might
be an “s3://bucket/folder/file” or some unique GUID.

**Database Schema**: The current implemented schema (from migrations) is a
simplified version: a `files` table and a `file_acl`
table([18](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/migrations/sqlite/00000000000004_create_files/up.sql#L1-L9)
 )(
[18](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/migrations/sqlite/00000000000004_create_files/up.sql#L8-L15)).
 In our initial implementation:

- `files` table fields: `id`, `name` (filename or folder name, unique globally
  in this simple schema), `object_key`, `size`. This suggests at this stage,
  it’s flat (the uniqueness of name at DB level implies we didn’t model
  directories yet, likely as a placeholder).

- `file_acl` fields: `file_id`, `user_id`, primary key on
  both(
  [18](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/migrations/sqlite/00000000000004_create_files/up.sql#L8-L15)).
   This grants a user access to a file. The initial approach seems to be a very
  basic ACL where every file explicitly lists which users can see it. This is
  not scalable for a general share (where you’d want group-based or
  public-by-default), but it might have been a starting point or a temporary
  measure until the more complex permission system is built.

However, the **intended design (per design doc)** is more advanced and matches
how Hotline works:

- A single `FileNode` table to represent both files and folders (and aliases as
  a special
  type)([17](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/file-sharing-design.md#L83-L91)
   )(
  [17](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/file-sharing-design.md#L115-L123)).
   It would have columns like:

- `id`,

- `type` (enum: file, folder, alias),

- `name` (text),

- `parent_id` (int, self-FK, null for root-level items),

- `alias_target_id` (int, self-FK if this is an alias pointing to another
  FileNode),

- `object_key` (text for actual storage key, only meaningful for files since
  folders/aliases have no data),

- `size` (bigint, size in bytes for files),

- `comment` (text, description or comment on the file/folder, since Hotline
  allowed leaving comments on files),

- `is_dropbox` (bool, true if the folder is a drop box where users can upload
  but not list files),

- timestamps and `created_by` to record who created
  it(
  [17](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/file-sharing-design.md#L115-L123)
   )(
  [17](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/file-sharing-design.md#L124-L132)).

- A `Permission` table for ACLs linking principals (user or group) to resources
  (file or
  folder)([17](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/file-sharing-design.md#L95-L99)
   )(
  [17](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/file-sharing-design.md#L126-L135)).
   The design suggests possibly reusing a common permissions table for files
  similar to how news had one. The mermaid snippet shows `Permission` with
  `resource_type` (like 'file'), `resource_id` (link to FileNode.id),
  `principal_type` ('user' or 'group'), `principal_id`, and `privileges`
  (bitmask)(
  [17](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/file-sharing-design.md#L115-L123)
   )(
  [17](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/file-sharing-design.md#L128-L136)).
   This is a flexible ACL that can grant different permission bits (like read,
  write, delete) to either individual users or groups on specific files or
  folders.

- Additionally, `User` and `Group` and `UserGroup` tables to manage user
  groups(
  [17](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/file-sharing-design.md#L91-L99)
   )(
  [17](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/file-sharing-design.md#L93-L101)).
   This way you can assign an entire group access to a folder by one entry in
  `Permission`, instead of listing every user.

Our current implementation hasn’t introduced groups or the unified Permission
table yet – it only has `file_acl` which is effectively a specific case of
permission linking user->file. Going forward, we would migrate to the richer
model:

- Possibly deprecate `file_acl` for a more general `permissions` usage, or map
  `file_acl` entries into the Permission table with `principal_type='user'` and
  `privileges` a default (like read access).

- Also introduce `groups` for easier management.

**How file operations map to this model**:

- A folder is a FileNode with `type='folder'`. It has no object_key (or
  object_key might be used to store a directory marker but likely not needed).
  Listing a folder means querying `FileNode` for all entries where
  `parent_id = that folder’s id`.

- A file is a FileNode with `type='file'`. It has an `object_key` that points
  to the actual file data in storage, and a `size`. Downloading means using the
  object_key to fetch data.

- An alias is a FileNode with `type='alias'`. The `alias_target_id` points to
  another FileNode (target). Clients treat alias like a file or folder that
  forwards to another location. On server side, listing a folder will include
  aliases. If a user tries to download an alias to a file, the server will
  actually retrieve the target’s object_key data. If they browse into an alias
  to a folder, the server will list the target folder’s contents (Hotline alias
  could link to a folder or single file).

- Each FileNode can have fine-grained permissions via the Permission table. For
  example, a folder might have an entry granting group "Everyone" read-list
  permission (so it’s public), but not write. A drop box folder might have
  everyone write permission (upload) but not read, and only admins read. The
  `is_dropbox` boolean is a convenient marker for UI or special-case logic: if
  `is_dropbox=true` and the user is not an admin (or not the uploader), the
  server might suppress listing its content (even if some permission might
  allow, we add extra rule that dropbox = no listing for non-admin, only
  uploading allowed).

- Also, `global_access` bitmask in User (in design, user had a global_access
  field(
  [17](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/file-sharing-design.md#L101-L109)))
   could indicate overall privileges (like admin flag or ratio privileges,
  etc.). We might interpret some high-level bits like “may upload anywhere
  despite folder perms”.

**Resumable Transfers**:

This is a crucial feature:

- **Downloads**: Hotline supports resume by the client specifying an offset (if
  a download was partially done, the client requests from byte X). Our server
  must handle that by reading the file from that offset onward. In an HTTP
  world, this is like Range requests. Since we use object_store, many backends
  support range queries (S3, local file, etc.). If not, we can simply open the
  file and seek to offset. We have to trust the client not to supply an invalid
  offset beyond EOF (we should clamp or error out if so).

- Implementation: The file download request might include a resume offset field
  (Hotline protocol likely has a field for resume called "file resume data" or
  similar). On receiving a download request (transaction 202 “DownloadFile”),
  we check if an offset was given. If 0 or not given, send from start. If
  non-zero, attempt to serve from that offset.

- If the client doesn’t have the file at all, offset=0, server sends whole file
  in one or multiple fragments (with the framing splitting it into pieces of
  maybe 2^16-1 bytes per fragment, as per protocol fragment field). If the file
  is huge, our server can stream it through the Wireframe codec’s streaming
  API([16](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/roadmap.md#L56-L64))
   (the roadmap mentions implementing a streaming API for large
  payloads([16](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/roadmap.md#L56-L64))).

- If offset is given, e.g. offset=500KB, we seek and start sending from 500KB
  to end. Possibly we have to also adjust the reported file size? Actually, in
  Hotline protocol, the initial download reply might tell the client the
  starting offset it will send. The design doc snippet suggests: the server on
  an upload resume tells client where to resume (maybe for download resume,
  client tells server how much it has, server just sends rest).

- We ensure that the framing in Wireframe handles partial content: in the frame
  header, `total_size` is the full file size and `data_size` in each fragment
  is the fragment chunk. Actually, if resuming, maybe `total_size` could still
  be the remaining bytes count. We need to clarify the protocol specifics to
  get this right.

- **Uploads**: Resuming uploads is more challenging, as we need to store
  partial data:

- If a client starts an upload (transaction 203 “UploadFile”), they might send
  the first N bytes then connection breaks. On reconnect, the client will issue
  another upload request with the same file and include a “resume” parameter
  which indicates how many bytes the server received (Hotline’s way is that the
  server responds to an initial upload request with a “resume offset” telling
  the client from where to continue). According to the design
  notes([17](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/file-sharing-design.md#L14-L22)),
   Hotline’s server, upon receiving an upload request, if the file exists
  partially, returns a resume position.

- Implementation approach: when a new upload starts, we create a FileNode entry
  (with a temporary status maybe, like not fully uploaded yet). The
  object_store might support appending or chunked upload. For local file
  system, we can open a file for writing and as data comes in (likely in
  fragments via frames) we write incrementally. If the connection drops, we
  have a partially written file on disk. We keep its size (how much was
  written).

- When the client reconnects and wants to resume, it sends an upload request
  for that file/path. The server can see that a file by that name exists and is
  incomplete (maybe we mark it with a flag or size less than expected, but the
  client might not send expected size). Alternatively, the client itself might
  know how much was sent and expects the server to tell how much it received.

- With object storage, it’s trickier: for S3, one might initiate a multipart
  upload and keep it open. On resume, continue the multipart. If our server
  restarts, it would have to recall the state (we might store ongoing uploads
  in DB with an upload ID).

- Simpler approach: For local or simple cases, just store the partially
  received file. When resuming, open it, get its length on disk (that is the
  resume point). Then respond to client: “resume at byte X” (where X = current
  size). Then client sends bytes starting from X. We append to file and
  complete.

- This approach is mentioned: *“when an upload is interrupted, we keep the DB
  entry (marked incomplete) and the partial file; on resume, we use file length
  as
  offset”*([17](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/file-sharing-design.md#L14-L22)
   )(
  [17](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/file-sharing-design.md#L20-L24)).

- We need to be careful to authenticate that it’s the same file being resumed
  (e.g., same user or same file name/path) to avoid one user overwriting
  another’s partial file erroneously.

- **Data Consistency**: If a file is fully uploaded, we likely mark it as
  complete (maybe a boolean or remove an 'incomplete' flag). If incomplete,
  perhaps do not allow others to download it or even list it (depending on
  policy). Or list but with size equal to current bytes, and maybe a flag.

- The design doc hints that resumable upload is complex but achievable, perhaps
  chunking the file and treating each chunk independently for easier
  resumption([17](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/file-sharing-design.md#L20-L24)).
   We might not implement full general chunking initially, but rely on simpler
  offset continue.

**Aliasing**: Creating an alias (transaction 209 “MakeFileAlias”) in domain
means:

- The user requests an alias of a file or folder to be created in some folder
  (target directory).

- The server would create a new FileNode with `type='alias'`, name perhaps
  given by user or same as target, parent_id = target directory, and
  alias_target_id = the item they want to link.

- No data copying occurs; it’s just a pointer. When listing a folder, we
  include the alias with maybe a special flag in the protocol to denote alias
  (Hotline protocol likely has a flag or type field).

- If user downloads an alias, the server resolves it to actual file and serves
  that.

- If user tries to upload into an alias (like alias to a folder), we should
  probably treat it as uploading to the real folder (resolving it).

- If the target is moved or deleted, ideally aliases should either become
  invalid or update. We might opt for making alias relationships with foreign
  keys (alias_target_id references FileNode id with maybe ON DELETE set null or
  cascade). But likely, if a target is deleted, we should delete aliases
  pointing to it (cascade) to avoid dangling. Our schema might consider that.

**Drop Boxes and Uploads**:

- A *drop box* is a folder where users can upload files but cannot see its
  contents (only an admin or privileged user can). We mark a folder
  `is_dropbox=true` for this. The server logic then, when listing that folder
  for a normal user, returns empty (even if files
  exist)([17](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/file-sharing-design.md#L14-L17)
   )(
  [17](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/file-sharing-design.md#L22-L29)).
   But it allows writing (upload).

- Implementation: Check `if folder.is_dropbox and user not admin` then return
  no entries on list. Possibly still allow user to see their own uploads?
  Hotline sometimes allowed the uploader to see the file they just uploaded
  until they disconnect. This nuance aside, drop box means write-only for
  regular users.

- An *upload folder* could be a special designated folder where all users can
  upload (could coincide with drop box concept or just a public upload area).
  In Hotline, “uploads” might refer to general permission to upload to some
  folders. We can configure certain folders with permissions or flags to
  allow/disallow upload.

**File Transfer Implementation with Wireframe**:

File transfers can be large (multiple megabytes). The `wireframe` library
supports streaming large payloads via the framing
codec([16](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/roadmap.md#L56-L64)
 )(
[16](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/roadmap.md#L58-L61)).
 The plan:

- Our custom `HotlineFrameSerializer` will detect when a message is marked as a
  data fragment (the header has `data_size` smaller than `total_size`). For
  incoming (upload) it will accumulate fragments until complete then deliver to
  handler(
  [1](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/migration-plan-moving-mxd-protocol-implementation-to-wireframe.md#L107-L116)).
   For outgoing (download) we can also send in fragments not to overwhelm
  memory. Ideally, we integrate with `tokio_util::codec::Framed` which
  `wireframe` uses under the hood. Possibly we can obtain an `AsyncRead` for
  the file and chunk it into frames of max allowed size, sending each with the
  appropriate header indicating which part.

- The design mentions property tests for multi-fragment requests to ensure
  correct
  reassembly([16](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/roadmap.md#L52-L60)),
   meaning we will be confident in splitting and combining fragments.

**Key Operations**:

- *List Directory (200 GetFileNameList)*: The server receives request for a
  directory listing (Hotline ID 200). If the protocol doesn’t explicitly send a
  path (maybe it assumes a current directory state per session), our server
  might maintain a “current directory” in the session. Actually, Hotline
  sessions typically have a concept of the user’s current folder for file
  browsing. Possibly commands like 200 with no param mean “list current
  folder”. And separate commands to change directory (Hotline had a
  “GetFileFolderContents” and used the concept of a folder ID).

- In our code, `Command::GetFileNameList` carries a `payload` of type `Vec<u8>`
  which might contain a folder identifier (if
  any)([13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L44-L52)).
   But currently, we don’t parse any parameters for file list; we just carry
  the payload raw and pass it
  on([13](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/commands.rs#L112-L116)).
   Possibly, if payload is empty, we list root. If not empty, it might contain
  the folder ID or path. We need to decode it. For now, we used
  `list_files_for_user(user_id)` which as implemented just returns all files
  that user has ACL
  to([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L598-L606)
   )(
  [9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L602-L610)).
   That’s a placeholder, effectively listing every file user can access
  (ignoring directories).

- In full implementation, we would have a `current_directory` in session (e.g.,
  a stack or pointer to a FileNode ID). If the client issues a “change
  directory” command (maybe 201 or part of 200’s payload indicates a specific
  folder), we update session’s current folder.

- Then 200 lists that folder: we query FileNode where parent_id =
  current_folder_id. Then for each entry, we output either a filename or folder
  name, probably with a type indicator. Hotline’s file listing response likely
  includes multiple fields (like an entry with name, size, type, maybe creation
  date, etc.). We might pack those fields in the returned parameters (maybe
  multiple FieldId values per file entry).

- If any aliases exist in that folder, they should appear as such (maybe name
  with an alias flag or special ID).

- If drop box and user is normal: return none (or perhaps we return a special
  message “[Drop Box]” with no files).

- *Download File (202)*: client requests to download a file with a given ID (or
  name in context). The server will find the FileNode (or alias target if
  needed), check user permission for it, then initiate transfer:

- Possibly by first sending a small reply acknowledging and perhaps containing
  the file size, then streaming the file data in subsequent frames. Hotline’s
  protocol for download might involve sending a header with file size and then
  separate data frames.

- The server will read from object_store in chunks and send fragment by
  fragment. Each fragment’s header would have same transaction ID and type=202
  reply, with `is_reply=1` and error=0, and appropriate `data_size`.

- After final fragment, maybe a final frame might indicate end of transfer (or
  the end is known when accumulated bytes == total_size on client).

- If resume offset was specified, the server adjusts which part to send.

- *Upload File (203)*: client wants to send a file. Possibly the client first
  sends an “Upload request” with filename and size. The server responds with
  either acceptance (and a place to resume from if partially exists). Then the
  client starts sending data fragments (maybe each with transaction 203,
  `is_reply=0`? Actually if the client is sending, it would use request frames).

- Our server will handle incoming frames for 203 in the `TransactionReader`
  loop, accumulating them. The `Command::Unknown` might catch them if not
  implemented, but we will implement a `Command::UploadFile` to handle
  piecewise assembly. We can utilize Wireframe’s ability to treat multiple
  frames with same transaction ID as one message by accumulating (similar to
  download but reverse direction).

- Once upload is complete (we detect final fragment by length matching total),
  we finalize: close file handle, mark DB entry complete, and send a
  confirmation to client (maybe an empty reply or an error if failed).

- If an upload is interrupted, the partial file remains. If the same user (or
  any user? Ideally same user) attempts upload of a file with same name in same
  folder again, we treat it as resume: open existing file, find size, and
  instruct client to continue from there. If the client wanted to override
  (fresh upload), they might have to cancel or remove the partial first – not
  sure if Hotline had an explicit cancel; likely the second attempt
  automatically resumes unless the user manually deletes the file on server if
  they had permission.

**Stretch Goals for File**:

- **Search**: Possibly integrating with OpenSearch to index file names or
  comments for searching (if server wants to offer search functionality).

- **Large file optimization**: using zero-copy or memory-mapped files for
  sending, applying backpressure if the client is slow (Wireframe’s push queue
  can drop to a DLQ if needed).

- **Checksum or virus scan**: not mentioned, but a real server might compute a
  hash of uploaded files or scan for malware. Could be a future addition.

### Tracker Beacons (UDP Tracking)

Hotline had the concept of **Trackers** – servers that keep a directory of
currently online servers, to facilitate discovery (especially since there’s no
centralized service in classic Hotline aside from user-run trackers). MXD aims
to support tracker registration via UDP “beacons.”

**Domain Model**: The MXD daemon can act as a **tracker client**, advertising
itself to one or more tracker services at intervals. It could also
theoretically become a tracker service itself (listing other servers), but
primary goal is likely client. So we have:

- A configuration for tracker: e.g. an address (hostname:port) of the tracker
  to register with, and maybe a desired server name and info to broadcast.

- A background task that every X seconds sends a UDP packet (“beacon”) to the
  tracker’s address.

- Possibly also responding to tracker queries if it were a tracker (but
  probably out of scope unless we implement a separate binary mode).

**Protocol**: The Virtual1’s Hotline protocol guide likely describes the UDP
beacon format. Typically, the beacon might include:

- Server name

- Server description or category

- IP/port of the server

- Number of users online

- Perhaps a tracking cookie or version.

It is connectionless: the server just periodically informs the tracker “I’m
here, with X users, name Y.” The tracker collects these and clients query the
tracker (over TCP or UDP) to get a list of servers.

**Implementation**:

- We’ll use a Tokio `UdpSocket` bound to some ephemeral port (or no bind needed
  for outgoing?). Actually, for sending beacons, we don’t need a fixed port; we
  just `socket.send_to(tracker_addr, packet_bytes)`.

- If we expect tracker to acknowledge or if NAT punching needed, we might bind
  to a stable port. But likely not necessary; trackers just receive.

- The data packet needs to be assembled per tracker’s spec. If unknown, we
  could emulate existing known implementations: Possibly a simple structure
  with a header (maybe “TRTP” or “TRAK” identifier) and fields like: server
  name length+string, IP, port, user count, maybe maximum user count, etc.

- We can fetch our external IP if needed (though tracker sees the packet source
  IP which is usually the correct one, unless NAT – then tracker uses the
  IP/port it sees).

- The daemon might also periodically update the user count in these beacons.

**Integration**:

- On startup, if tracker config is set, spawn a tokio task that runs an
  interval (say every 5 minutes or 60 seconds configurable). That task crafts
  the packet and does `udp_socket.send_to(...)`.

- Potentially, also send immediately on start (so server appears on tracker
  quickly).

- If the server is shutting down, maybe send a final beacon with user count 0
  or a “sign-off” message (some trackers might remove entry if no beacon after
  a timeout, but a courtesy sign-off could speed removal).

- If supporting multiple trackers, just maintain a list and send to each.

**Tracker UDP Packet Example** (hypothetical):

Could be as simple as:

```text
[magic bytes "TRAK"]
[port (2 bytes)]
[user count (2 bytes)]
[name length (1 byte)]
[name (N bytes)]
```

The design doc doesn’t provide the exact, but we’d get from protocol
references. We ensure to send in network byte order as needed. If encryption or
anything, likely not for tracker beacons (they were plain in classic Hotline).

**Security**: These beacons inform public of your server, which is intended.
There’s minor risk like DDoS from being listed, but that’s inherent to being
public.

**Edge Cases**:

- If tracker is down or unreachable, UDP send fails – we could log a warning
  but continue retrying periodically.

- If behind NAT, the source port of our UDP might change; trackers typically
  list whatever IP:port a beacon comes from. That means our
  `--bind 0.0.0.0:5500` for TCP might not match the UDP source port. Some
  trackers require server to specify its TCP port in the beacon so that even if
  UDP source is random, the tracker uses the provided port for clients to
  connect. We should include the listening port in the beacon payload.

- Possibly add a config for tracker key or authentication if needed (some
  trackers might require a key or version to prevent random spam). Unclear if
  classic had any auth – likely not.

**Operations**:

- We treat tracker beacons as fire-and-forget. We don’t need to handle incoming
  UDP responses in the client role. If we wanted to act as a tracker ourselves,
  we’d listen on UDP for beacons and keep a list of servers. But that’s beyond
  current scope.

- We should run the UDP socket in its own task or use
  `tokio::spawn(async move { loop { send; sleep; } })`.

- Minimal CPU overhead.

To ensure comprehensiveness: The tracker feature is somewhat separate from the
main protocol (which is TCP). But it’s important for server discovery. We
highlight it even though it doesn’t interact with other parts except needing
the current user count and server name:

- Current user count we can get by length of active sessions list.

- Server name we might take from config (we might add a config field for server
  name/description to broadcast, e.g. `--name "My BBS"`).

- Possibly also whether to advertise if server is private or requires a
  password (some trackers indicated if a server required login or was full
  etc.).

**Conclusion**: The tracker beacon subsystem is a small UDP broadcaster that
periodically announces the server’s presence to well-known tracker(s), enabling
clients to discover the server through those directories.

## Testing Strategy

MXD is built with thorough testing in mind, including unit tests, integration
tests, behavior-driven scenario tests, and even fuzzing for robustness. The
goal is to cover everything from the correctness of a single SQL query to the
end-to-end protocol compliance with real clients.

### Embedded Postgres for Testing

To test against PostgreSQL without requiring a developer to set up a database,
we utilize an **embedded Postgres server** via the `postgresql-embedded` crate.
In tests (when compiled with `--features postgres`), we use a fixture called
`postgres_db` that will launch a Postgres instance in a temporary directory,
create a fresh database, run migrations, and provide a connection
URL([15](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/README.md#L86-L94)
 )(
[15](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/README.md#L88-L95)).
 This is managed by our `test-util` helper:

- We spin up the PG server on a random free port. Our fixture code checks if
  `POSTGRES_TEST_URL` env var is set; if so, it will use that as an existing
  database (perhaps to speed up or allow testing on a manually provided
  server)([15](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/README.md#L87-L95)).
   If not, it calls `start_embedded_postgres()` to boot the embedded
  one([19](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/test-util/src/postgres.rs#L76-L84)
   )(
  [19](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/test-util/src/postgres.rs#L146-L155)).

- `start_embedded_postgres()` in our test util ensures a single global Postgres
  binary download/installation (with a lock file to avoid races between
  tests)([19](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/test-util/src/postgres.rs#L114-L123)
   )(
  [19](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/test-util/src/postgres.rs#L130-L139)),
   then starts the server and creates a database with a unique name (using a
  UUID)(
  [19](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/test-util/src/postgres.rs#L159-L168)
   )(
  [19](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/test-util/src/postgres.rs#L170-L178)).
   It returns an object containing the `DatabaseUrl` (with the new DB
  name)([19](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/test-util/src/postgres.rs#L172-L180)).
   We run our Diesel migrations on that database to set up schema for
  tests([19](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/test-util/src/postgres.rs#L146-L155)
   )(
  [19](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/test-util/src/postgres.rs#L150-L158)).

- Each test that needs a database can simply have a parameter of type
  `PostgresTestDb` or similar, and through `#[fixture]` macro, the
  `postgres_db()` provides a fresh isolated DB. We drop and recreate the public
  schema before each test to ensure a clean
  slate([15](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/README.md#L89-L95))
   (the fixture does `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` on the
  connection).

- We verify engine-specific features with tests like `test_audit_postgres()`
  which starts an embedded PG, connects, and calls `audit_postgres_features()`
  to ensure the server is version
  14+([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L159-L167)
   )(
  [9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L661-L670)).
   Similarly for SQLite, `audit_sqlite_features()` is tested on an in-memory
  DB([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L134-L143)
   )(
  [9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L654-L662)),
   so we don’t inadvertently use unsupported SQL.

Using embedded Postgres in CI means our test suite can run fully automated,
covering both backends. The overhead of launching PG is mitigated by reusing it
for multiple tests if possible (but since we recreate schema per test, we keep
one PG process around rather than per test process). We also guard some tests
with `#[ignore]` if they require certain setup (e.g. `test_audit_postgres` uses
embedded PG
differently)([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L661-L670)).

For SQLite tests, it’s simpler: we can use `:memory:` or temp files and run
migrations there. Many tests are written generic enough that they run under
both SQLite and PG depending on feature flags.

### Behaviour-Driven Tests with Rstest-BDD

We incorporate **BDD-style testing** using the `rstest` framework (with
potential `rstest-bdd` extension) to write scenario-based tests. These tests
are structured around Given/When/Then narratives, which map to setup, action,
and assertion in code.

For example, a BDD test for login might be written as (pseudo-code):

```rust
#[rstest]
fn login_success_scenario(#[future] server: RunningServer, #[fixture] temp_db: SqliteTestDb) {
    Given!("a running MXD server with an existing user 'alice'");
    create_user_via_db(&temp_db, "alice", "secret");
    When!("a client connects and sends a correct LOGIN command");
    let client = TestClient::connect(server.address);
    let response = client.login("alice", "secret");
    Then!("the server replies with success and the session is authenticated");
    assert!(response.is_ok());
    assert_eq!(response.version_param, SERVER_VERSION);
    assert!(server.session_for("alice").is_some());
}
```

We simulate a client in tests by either using an internal API or an “expect”
style script via an external client (see next section). The `rstest` macros
allow injecting fixtures easily (like `server` which might start the server in
a background thread, and `temp_db` which provides a fresh DB).

We have written tests for various flows:

- **Login/Logout**: verify that valid credentials succeed and invalid ones fail
  (and that failure doesn’t create a session). Ensure multiple commands can be
  sent on one connection (the server loop should handle sequential commands per
  session).

- **News flows**: use a BDD approach to simulate creating bundles, posting
  news, then reading it. Possibly using the `create_bundle` and
  `create_category` functions in tests to set up initial data, then calling
  into the handler functions or using an expect client to do it via protocol.

- **File flows**: for now, we might test listing and simple ACL. As file
  uploads/downloads are more complex with actual sockets, we likely rely on
  integration tests with an actual client (or at least test the
  `list_files_for_user` logic in isolation as we do in
  `test_create_bundle_and_category` (which was a basic smoke test to ensure
  migrations and inserts
  work)([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L635-L643)
   )(
  [9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L644-L652)).

The BDD style (with descriptive Given/When/Then) is documented in our internal
guide(
[20](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/rust-testing-with-rstest-fixtures.md#L2-L10)),
 emphasizing readability of test cases. It helps ensure that features meet
expected user stories. We use `rstest` fixtures heavily to avoid repeating
setup code (like starting a server or creating sample data). For instance, we
have a fixture for a logged-in session, a fixture for an embedded PG with
certain data preloaded, etc.

### Expectrl-based Protocol Harness (SynHX Compatibility)

To validate that MXD speaks the Hotline protocol correctly, we built an
integration test harness using an **external Hotline client** and the
`expectrl` library (which allows scripting an interactive session, similar to
classic Expect for automating terminal interactions). Specifically:

- We use the **Synapse Hotline X (SynHX)** client (an open-source Hotline
  client) in a headless mode. The `hx` binary (v0.2.4) can run commands from
  CLI or connect to a server if invoked
  appropriately([15](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/README.md#L98-L105)).

- In our `validator` test crate, we spawn the MXD server (perhaps on localhost
  ephemeral port) and then spawn the `hx` client as a child process. We use
  `expectrl` to interact with `hx` by sending it commands (like “open
  connection”, “login”, “send message”) and expecting certain responses (like
  specific text or success codes).

- For example, a test might start MXD, then do:

- `hx /connect 127.0.0.1 5500` and expect some greeting or login prompt.

- `hx /login alice secret` and then expect a “Login successful” message or
  absence of error.

- `hx /who` to list users and expect to see "alice".

- Or to test news: `hx /news list` and expect the categories to match what MXD
  has.

- For chat: if hx supports sending chat messages, we could simulate two hx
  instances to ensure messages broadcast.

The harness essentially uses the **actual protocol implementation from a real
client** to double-check our server. This catches any discrepancies in byte
ordering, field encoding, XOR encoding (Hotline had an XOR obfuscation for text
sometimes), etc. Our roadmap explicitly includes compatibility toggles like XOR
decoding for clients that need
it([16](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/roadmap.md#L93-L101)).
 In the validator tests, we likely verify that:

- A SynHX client can login to MXD (meaning our handshake and login responses
  are acceptable).

- It can retrieve user list, see itself.

- Possibly test sending a public chat message and ensure MXD echoes it back
  properly (though with one client, maybe hx has a loopback test mode or we
  might run two clients).

- File downloads might be tested by having hx request a file (if hx has command
  to download file to disk, we can create a small file on server and see if hx
  gets it).

- News might be tested by hx retrieving a news post.

Because these tests involve actual I/O and an external binary, they are a bit
more fragile and likely marked to run in certain environments (we need hx 0.2.4
installed in PATH as
noted([15](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/README.md#L98-L105))).
 They might not run in a typical `cargo test` unless the environment is
prepared, but we have instructions and perhaps a CI job that sets up hx for
these integration tests.

Example from `README.md`:

```bash
cd validator
cargo test
```

This will run the expect scripts. We probably have those scripts or direct
expect code in `validator/tests/` or so. The output of these tests is a high
confidence that MXD’s behavior matches an actual Hotline server as far as the
client is concerned.

### Fuzzing and Property-Based Testing

**Fuzz Testing (AFL++)**: We have integrated **AFL++** fuzzing to find crashes
or misbehavior in critical parsing code. The fuzz harness is located under
`fuzz/`
directory([15](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/README.md#L2-L5)).
 It targets the `parse_transaction` function (which decodes raw bytes into a
`Transaction`
struct)([21](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/fuzzing.md#L6-L14)
 )(
[21](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/fuzzing.md#L8-L11)).
 By fuzzing the frame parser, we ensure that arbitrary or malformed inputs from
clients won’t crash the server or produce memory safety errors. We compiled the
harness with AddressSanitizer in a Docker container to catch even memory issues
in unsafe code (though Rust code is mostly safe, the Diesel C bindings or other
dependencies could have issues).

The fuzz process:

- We generate an initial seed corpus of valid protocol frames using a small
  program (`gen_corpus.rs`) which emits some example transactions (like a
  well-formed login frame, a file list frame,
  etc.)([21](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/fuzzing.md#L8-L16)
   )(
  [21](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/fuzzing.md#L9-L12)).
   These seeds help AFL to have starting points that reach deeper into parsing
  logic.

- AFL++ then mutates these and feeds into `parse_transaction`. If
  `parse_transaction` ever panics or returns an error (actually we deliberately
  cause a panic on certain errors in the harness to treat them as crashes, see
  `The harness panics on parsing errors so crashes will be detected`
  ([21](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/fuzzing.md#L28-L32))),
   AFL will flag that input. We consider a parse error as a "crash" for fuzzing
  purposes to try to exercise all error paths – essentially, we might want to
  see if any input causes an *unexpected* error or panic (though
  parse_transaction returns Result, we might only panic on truly inconsistent
  state).

- The fuzzing runs in CI as a nightly job for several
  hours(
  [21](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/fuzzing.md#L54-L61)
   )(
  [21](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/fuzzing.md#L56-L64)).
   If any new crash is found, the input is saved to `artifacts/main/crashes`
  and can be analyzed.

- Over time, this gives us confidence that parsing (and by extension the
  handling of any malicious input) doesn’t have out-of-bound reads or infinite
  loops (e.g. an absurd length in header causing memory alloc issues).

- Fuzzing can be extended to other parts like the **tree-walking logic** (the
  “tree walkers” mention likely referring to our recursive path resolution and
  news thread traversal). For example, we could fuzz `prepare_path` and
  `category_id_from_path` by feeding random JSON or random path strings to see
  if any edge cases (like very deep nesting or special characters) break it.
  Also fuzz the Diesel CTE extension if possible (ensuring our builders handle
  weird inputs).

- Another fuzz target could be the file system operations (though those involve
  I/O, less about memory safety).

**Property-Based Testing**: While not explicitly mentioned, the phrase
“property-based testing via AFL” is a bit mixing terms. AFL is generational
fuzz, not property-based like QuickCheck. We might incorporate something like
the `proptest` crate to generate structured inputs for certain components:

- For instance, generate random sequences of valid transaction frames and
  ensure that if we serialize them out and parse back, we get the same struct
  (round-trip property). Or generate random valid news thread structures and
  ensure that if we compute something via CTE vs via recursive Rust, results
  match (checking consistency of our recursive SQL logic).

- Diesel queries could also be property-tested: e.g. ensure that for any
  randomly generated news bundle structure (within reason), our
  `bundle_id_from_path` correctly resolves a random path we know is valid.

At minimum, we have unit tests like `test_create_bundle_and_category` to ensure
basic CRUD
works([9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L635-L643)
 )(
[9](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/db.rs#L644-L652)),
 and tests for config
precedence([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L376-L384)
 )(
[4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L389-L396)),
 etc.

**Manual and Integration Testing**:

- We test installation and CLI by having tests that run
  `AppConfig::load_from_iter` with sample args and env to verify the merging
  logic (as shown by those figment Jail
  tests)([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L376-L384)
   )(
  [4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L389-L396)).

- We test error cases, e.g., if both `sqlite` and `postgres` features were on
  (though that can’t happen in normal use due to compile error), but maybe if
  the DSN string is wrong, does `establish_pool` error get propagated properly,
  etc.

- Using the `temp-env` crate in tests to set env vars (which we did with
  figment’s
  Jail)([4](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/src/main.rs#L376-L384))
   ensures we don’t pollute real environment.

In CI, we run `cargo test` for both SQLite and Postgres configurations (two
separate jobs). We also run clippy (with pedantic warnings), maybe fmt, etc.,
to keep code quality.

To summarize:

- **Unit tests** target individual functions (like password hashing, config
  loading, DB helpers).

- **Integration tests** in `tests/` directory spawn the whole app (or at least
  simulate multiple parts together) – e.g., we might start a Tokio runtime and
  run `main()` inside a test with some temp args to see that it starts and
  stops correctly (maybe using a timeout).

- **BDD tests** (using `rstest` and possibly `rstest_bdd`) provide readable
  scenarios verifying end-to-end behavior for key use cases.

- **Expect tests with SynHX** confirm protocol adherence with a real client.

- **Fuzz tests** hammer the server’s input handling for memory safety and panic
  resistance.

- **Continuous fuzzing** in CI ensures over time we catch new issues
  (especially as we add features, new code paths open up to fuzz inputs).

All these give us high confidence in MXD’s reliability and correctness across
its feature set.

### Fuzz Example and CI Integration

Our fuzz harness example in docs shows how to build and run AFL
manually([21](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/fuzzing.md#L12-L20)
 )(
[21](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/fuzzing.md#L24-L32)).
 We also provide a Dockerfile to run the fuzzer in an isolated environment with
necessary
instrumentation([21](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/fuzzing.md#L34-L42)
 )(
[21](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/fuzzing.md#L44-L52)).
 The nightly CI uses this, building the image and running it for a set
time([21](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/fuzzing.md#L54-L62)
 )(
[21](https://github.com/leynos/mxd/blob/88d1cfb3097b2d96f2b7c9d1382f6b374d7eb90c/docs/fuzzing.md#L64-L72)).
 The CI then uploads any found crashes so developers can examine them. This
process is automated but aligns with our roadmap where fuzzing is an ongoing
process (not a one-time thing) to catch regressions or deeper issues.

With these testing strategies, we aim for a robust daemon:

- If a test fails, we have pinpointed whether it’s logic (then fix code) or
  expectation (maybe update test if spec changed).

- If a fuzz case crashes, we add a regression test for that case specifically
  after fixing.

- The variety of tests (from unit to full integration with external software)
  covers both our intended behavior and actual interoperability in the wild.

## Extensibility and Operational Considerations

Finally, MXD is built to be maintainable and extensible in production. We
outline some strategies for reloading configuration, containerization, and
planned enhancements that can be added without major refactoring.

### Hot-Reload Configuration (SIGHUP Handling)

In a long-running server, it’s useful to adjust settings on the fly (like
enabling debug logging, changing a config value, reloading user/permission
data) without a full restart. We plan to support **hot-reload** of
configuration via signal (traditionally SIGHUP on Unix indicates a service
should reload its config).

Implementation approach:

- The OrthoConfig system can load from a file, so on SIGHUP, we can trigger
  `AppConfig::load()` again (merging file, env, etc.). We would likely **not**
  merge CLI again (since CLI args wouldn’t change at runtime), but re-read env
  and config file. Or we could specifically only reload the config file to
  allow dynamic tweaks (perhaps ignoring CLI since CLI was one-time).

- Using Tokio’s signal handling (similar to our shutdown with SIGTERM) we can
  register a handler for SIGHUP. When it fires, we log “Reload signal received”
  and then do something like:

```rust
if let Ok(new_cfg) = AppConfig::load() {
    apply_config_changes(old_cfg, new_cfg);
} else {
    warn!("Failed to reload config, keeping old settings");
}
```

- The tricky part is applying changes live. Some settings can be applied
  immediately: e.g. enabling a new debug log level (we could adjust tracing
  filter level), or changing Argon2 parameters (which would affect future
  password hashing, fine). But certain settings might not be safe to change
  without restart: e.g. database URL – switching the DB while running is
  complex (we’d have to initiate a new pool to a new DB; possible but risky
  while old connections active). Or `bind` address – we can’t easily change the
  listening port on the fly (it would require opening a new socket; we could do
  it but then we have two listeners unless we closed the old – not simple).

- A compromise: allow reload of some configs like log level, perhaps refresh of
  user permission data from an external source (if we had such concept), or
  toggling features (like enabling/disabling new subsystems).

- For the DB pool, one could envision wanting to change the max pool size or
  timeouts at runtime. Diesel’s pool (bb8) might allow reconfiguring, or we
  simply recreate pool (but switching pools on the fly for active queries is
  not trivial).

- If major config changes (like DB URL) are needed, a full restart is safer. So
  we document which config fields are reloadable. Possibly we restrict
  hot-reload to *settings that do not require re-binding or new connections*.
  Logging level is a clear candidate.

Alternatively, some administrators use a “rotate logs” signal or similar. We
use journald so we don’t handle log files, so no need for SIGUSR1 type for log
cycling. But if we were writing to a file, SIGHUP could trigger closing and
re-opening log files.

Our shutdown handler already catches SIGTERM; adding SIGHUP is easy. Since we
already use `tokio::signal::unix`, we can also listen for SIGHUP separately.
Ensuring that doesn’t conflict with any library usage is fine.

Hot-reload is a **stretch feature** – not critical for initial operation, but
good for QoL. If not implemented initially, our design leaves room for it.

### Container-Friendly Deployment

MXD is suited to run in a Docker or Kubernetes environment. We plan for a
**container-friendly layout**:

- **Filesystem Locations**: By default, MXD writes to `mxd.db` in current
  directory (for SQLite). In a container, one would mount a volume at, say,
  `/data` and run `--database /data/mxd.db` to persist state. We encourage
  using absolute paths for database and any storage directories so that they
  can be volume-mounted.

- The default bind `0.0.0.0:5500` is container-friendly (listens on all
  interfaces so Docker port mapping works).

- We avoid writing to arbitrary FS locations that aren’t configurable. E.g. if
  we had file storage on disk for uploaded files, we’d allow configuring that
  directory (so it can be a volume). If using S3 or others, config is
  environment-driven (so secrets can be passed in).

- The container image (if we create one) would be minimal (based on e.g.
  Debian-slim or Alpine, with the binary and possibly Diesel CLI if migrations
  are run external – but since we embed migrations, no need for diesel CLI
  inside container).

- We consider using an non-root user in container for security (maybe run as
  uid 1000).

- Logging in container: since we output to stdout/stderr (or journald if in
  systemd environment), in Docker it’s fine – stdout will capture logs which
  can be collected by Docker or K8s. If someone wanted file logs, they’d mount
  volume for log path.

- **Ports**: Document that port 5500 (TCP) needs exposing. If tracker UDP was
  used, UDP port ephemeral or 5499 might be used to send out – not an ingress
  port, so not needing exposure.

We may provide a Dockerfile for MXD in the repo eventually (especially given we
have one for fuzz with AFL). A runtime Dockerfile could be multi-stage: build
in Rust official image, then copy binary into a small base (or use something
like Rust musl to statically link for alpine).

In Kubernetes, one might run multiple instances of MXD (sharded communities?),
but Hotline isn’t really multi-instance cluster (one instance = one server).
However, one could front it with a TCP load balancer or use a NodePort. But
since state is local (db), running multiple active instances to serve same
community would require a shared DB – possible with Postgres as backend and
perhaps a Redis for ephemeral state sync (like chat presence). Our design could
be extended for that: e.g. if two MXD instances share a Postgres and a Redis,
they could handle clients in parallel and use Redis pub/sub to broadcast chat
messages across instances. That’s a stretch scenario (for scaling or HA). The
mention of Redis caching and OpenSearch suggests thinking in that direction.

### Future Extensibility: Redis, OpenSearch, etc

**Redis Caching and Pub/Sub**: Integrating Redis could serve multiple purposes:

- *Caching frequently read data*: For example, user permission sets, news
  threads, or file directory listings could be cached in Redis to reduce DB
  load, especially for expensive queries (like a large news thread retrieval).

- *Session store*: We could store session state in Redis if we wanted stateless
  MXD instances (to allow horizontal scaling). Instead of in-memory, each
  instance could fetch session info from a shared Redis so if a user connects
  to a different instance, their presence is known. However, Hotline protocol
  ties one user session to one TCP connection, so no multi-instance session
  moves needed in real-time, but for failover or load balancing initial
  assignments, a shared session store might be considered.

- *Pub/Sub for Chat*: If we had multiple MXD servers forming one logical chat
  network, using Redis Pub/Sub channels for each chat room would allow a
  message from a user on server A to be published and delivered to subscribers
  (server B for users connected there). This would require a bit of
  architecture above single instance, but it’s doable. Our ports-and-adapters
  design would help: we’d implement a **transport adapter** that on sending a
  chat message, not only sends to local sessions but also publishes to Redis;
  and a background task subscribes to Redis channels to receive messages from
  other instances and inject them into local routing.

- *Rate limiting / login attempt tracking*: Redis could be used to track failed
  login counts across instances, implement IP bans, etc.

Adding Redis would involve adding a dependency and perhaps a feature flag for
it. We might plan it as a non-critical enhancement unless scaling demands it.

**OpenSearch/Elasticsearch Integration**: This would be aimed at improved
search capabilities:

- *Full-text search for News and Files*: If news articles (especially if `data`
  field can be large text) need to be searchable (e.g., find all posts
  mentioning “foobar”), feeding them into OpenSearch would allow complex
  queries that SQL might not handle efficiently (especially SQLite). Similarly,
  for file sharing, one might index file names, comments, and allow users to
  search the repository.

- *Logging and analytics*: Another use could be sending structured logs or
  metrics to an OpenSearch cluster for analysis, though we didn’t mention
  metrics, it could be related if we extended journald logging or directly push
  logs.

- Implementation: We could use the OpenSearch REST API or an SDK to index
  records whenever they are created/updated. For instance, on posting a news
  article, asynchronously send an index request to OpenSearch with the text and
  metadata. On deletion, send a delete request to the index. Provide a search
  command in protocol (some BBS servers have a search feature) that queries
  OpenSearch and returns results.

- If not in protocol, at least an admin could use it outside of band.

**Other Stretch Goals**:

- **Metrics and Monitoring**: Integration with Prometheus or similar (e.g.
  exposing an HTTP endpoint with metrics counters for connections, commands
  processed, etc.). Not asked, but in modern ops that’s common. We could easily
  add a `/metrics` HTTP endpoint via a tiny warp/hyper server on another port,
  or simply push metrics to StatsD/Prom via exporter. This can be decided later.

- **Federation**: Possibly linking multiple servers (not sure if Hotline ever
  allowed inter-server chat or transfers, probably not, aside from trackers).

- **Alternate Protocols**: If someone wanted, the hexagonal design means we
  could plug in an HTTP API or a web UI. For example, a REST/GraphQL interface
  to manage users or view news could be added as another adapter (listening on
  another port). The domain core would be reused. This is beyond Hotline’s
  scope but could be interesting for admin controls or bridging to web.

- **Mobile or Web Client support**: If we wanted to allow modern clients that
  can’t speak Hotline binary, we could run a sidecar that translates (or
  directly integrate an adapter). This again demonstrates the flexibility of
  having domain logic separate from transport.

- **Plugin System**: Not in current plan, but one could consider a plugin
  interface where new commands or behaviors could be added without modifying
  core (maybe via dynamic libraries or a scripting engine). Since not
  requested, just an idea (some BBS allow scripting, but likely out of scope
  here).

In conclusion, MXD is structured for growth. Its current capabilities focus on
faithfully implementing Hotline server features, but the foundations (robust
async I/O, database abstraction, configuration system, etc.) allow it to be
extended and operated in various environments. Key operational features like
config reload and containerization make it practical to run continuously, and
planned enhancements like caching and search will ensure it scales with user
demands and data volume. The design decisions documented here aim to ensure
that adding those features will require minimal changes to existing components
(thanks to clear module boundaries and use of standard crates for integration).

[^diesel-migration-consistency]:
  supporting-both-sqlite3-and-postgresql-in-diesel.md#L34-L37
