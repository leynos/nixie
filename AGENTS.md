# Assistant Instructions

## Agent Documentation

Additional agent instructions are located in `.rules/`

## Code Style and Structure

* **Code is for humans.** Write your code with clarity and empathy—assume a tired teammate will need to debug it at 3 a.m.
* **Comment *why*, not *what*.** Explain assumptions, edge cases, trade-offs, or complexity. Don't echo the obvious.
* **Clarity over cleverness.** Be concise, but favour explicit over terse or obscure idioms. Prefer code that's easy to follow.
* **Use functions and composition.** Avoid repetition by extracting reusable logic. Prefer generators or comprehensions to imperative repetition when readable.
* **Name things precisely.** Use clear, descriptive variable and function names. For booleans, prefer names with `is`, `has`, or `should`.
* **Structure logically.** Each file should encapsulate a coherent module. Group related code (e.g., models + utilities + fixtures) close together.
* **Group by feature, not layer.** Colocate views, logic, fixtures, and helpers related to a domain concept rather than splitting by type.

## Documentation Maintenance

*   **Reference:** Use the markdown files within the `docs/` directory as a knowledge base and source of truth for project requirements, dependency choices, and architectural decisions.
*   **Update:** When new decisions are made, requirements change, libraries are added/removed, or architectural patterns evolve, **proactively update** the relevant file(s) in the `docs/` directory to reflect the latest state. Ensure the documentation remains accurate and current.

## Guidelines for Code Changes & Testing

When implementing changes, adhere to the following testing procedures:

* **New Functionality:**
  * Implement unit tests covering all new code units (functions, components, classes). Implement tests **before** implementing the unit.
  * Implement behavioral tests that verify the end-to-end behavior of the new feature from a user interaction perspective.
  * Ensure both unit and behavioral tests pass before considering the functionality complete.
* **Bug Fixes:**
  * Before fixing the bug, write a new test (unit or behavioral, whichever is most appropriate) that specifically targets and reproduces the bug. This test should initially fail.
  * Implement the bug fix.
  * Verify that the new test now passes, along with all existing tests.
* **Modifying Existing Functionality:**
  * Identify the existing behavioral and unit tests relevant to the functionality being changed.
  * **First, modify the tests** to reflect the new requirements or behavior.
  * Run the tests; they should now fail.
  * Implement the code changes to the functionality.
  * Verify that the modified tests (and all other existing tests) now pass.
* **Refactoring:**
  * Identify or create a behavioral test that covers the functionality being refactored. Ensure this test passes **before** starting the refactor.
  * Perform the refactoring (e.g., extracting logic into a new unit).
  * If new units are created (e.g., a new function or component), add unit tests for these extracted units.
  * After the refactor, ensure the original behavioral test **still passes** without modification. Also ensure any new unit tests pass.

## Change Quality & Committing

* **Atomicity:** Aim for small, focused, atomic changes. Each change (and subsequent commit) should represent a single logical unit of work.
* **Quality Gates:** Before considering a change complete or proposing a commit, ensure it meets the following criteria:
  * For Python files:
    * **Testing:** Passes all relevant unit and behavioral tests according to the guidelines above.
    * **Linting:** Passes lint checks (`ruff check` or integrated editor linting).
    * **Formatting:** Adheres to formatting standards (`ruff format` or integrated editor formatting).
    * **Typechecking:** Passes type checking (`pyright` or integrated editor type checking).
  * For TypeScript files:
    * **Testing:** Passes all relevant unit and behavioral tests according to the guidelines above.
    * **Linting:** Passes lint checks (`biome check .` or integrated editor linting).
    * **Formatting:** Adheres to formatting standards (`biome check --apply .` or integrated editor formatting).
    * **TypeScript Compilation:** Compiles successfully without TypeScript errors (`tsc --noEmit`).
  * For Markdown files (`.md` only):
    * **Linting:** Passes lint checks (`markdownlint filename.md` or integrated editor linting).
* **Committing:**
  * Only changes that meet all the quality gates above should be committed.
  * Write clear, descriptive commit messages summarizing the change, following these formatting guidelines:
    * **Imperative Mood:** Use the imperative mood in the subject line (e.g., "Fix bug", "Add feature" instead of "Fixed bug", "Added feature").
    * **Subject Line:** The first line should be a concise summary of the change (ideally 50 characters or less).
    * **Body:** Separate the subject from the body with a blank line. Subsequent lines should explain the *what* and *why* of the change in more detail, including rationale, goals, and scope. Wrap the body at 72 characters.
    * **Formatting:** Use Markdown for any formatted text (like bullet points or code snippets) within the commit message body.
  * Do not commit changes that fail any of the quality gates.

## Refactoring Heuristics & Workflow

* **Recognizing Refactoring Needs:** Regularly assess the codebase for potential refactoring opportunities. Consider refactoring when you observe:
  * **Long Methods/Functions:** Functions or methods that are excessively long or try to do too many things.
  * **Duplicated Code:** Identical or very similar code blocks appearing in multiple places.
  * **Complex Conditionals:** Deeply nested or overly complex `if`/`else` or `switch` statements (high cyclomatic complexity).
  * **Large Code Blocks for Single Values:** Significant chunks of logic dedicated solely to calculating or deriving a single value.
  * **Primitive Obsession / Data Clumps:** Groups of simple variables (strings, numbers, booleans) that are frequently passed around together, often indicating a missing class or object structure.
  * **Excessive Parameters:** Functions or methods requiring a very long list of parameters.
  * **Feature Envy:** Methods that seem more interested in the data of another class/object than their own.
  * **Shotgun Surgery:** A single change requiring small modifications in many different classes or functions.
* **Post-Commit Review:** After committing a functional change or bug fix (that meets all quality gates), review the changed code and surrounding areas using the heuristics above.
* **Separate Atomic Refactors:** If refactoring is deemed necessary:
  * Perform the refactoring as a **separate, atomic commit** *after* the functional change commit.
  * Ensure the refactoring adheres to the testing guidelines (behavioral tests pass before and after, unit tests added for new units).
  * Ensure the refactoring commit itself passes all quality gates.
