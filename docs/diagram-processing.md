# Diagram Rendering Flow

The following sequence diagram illustrates how Nixie processes
Mermaid diagrams within a Markdown document.

```mermaid
sequenceDiagram
    participant CLI
    participant File
    participant Diagram
    participant Renderer
    actor User
    User->>CLI: Run CLI on Markdown file
    CLI->>File: Read file contents
    CLI->>Diagram: parse_blocks(text)
    loop For each Diagram
        CLI->>CLI: Print --> line {line_start}: {schema}
        CLI->>Renderer: render_block(source, ...)
        Renderer-->>CLI: Render result
        CLI->>CLI: Print <-- line {line_end}: {schema}
    end
```
