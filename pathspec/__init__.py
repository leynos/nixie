"""Minimal local shim of ``pathspec`` for tests without network.

This implements just enough of the API used by this project to respect a
subset of ``.gitignore`` patterns during testing, specifically:

- Directory prefix patterns like ``ignored/``
- Root-level file patterns like ``skip.md``

It is not a full implementation of gitwildmatch and should be replaced by the
real ``pathspec`` package in normal development environments.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc


@dc.dataclass(slots=True)
class _Rule:
    kind: str  # "dir" or "file"
    value: str


class PathSpec:
    """Tiny subset of ``pathspec.PathSpec`` supporting .gitignore basics.

    Only the minimal operations required by tests are implemented.
    """

    def __init__(self, rules: list[_Rule]) -> None:
        self._rules = rules

    @classmethod
    def from_lines(cls, style: str, lines: cabc.Iterable[str]) -> PathSpec:
        """Create a spec from ``.gitignore``-style lines.

        Supports directory prefix patterns (e.g., ``ignored/``) and exact
        root-level file names (e.g., ``skip.md``).
        """
        if style != "gitwildmatch":  # keep scope tight; extend if needed
            raise NotImplementedError("Only 'gitwildmatch' is supported in tests")
        rules: list[_Rule] = []
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            # Simplified handling: directory rules end with '/'
            if line.endswith("/"):
                rules.append(_Rule("dir", line[:-1]))
            else:
                # Root-level file pattern
                rules.append(_Rule("file", line))
        return cls(rules)

    def match_file(self, rel_path: str) -> bool:
        """Return True when ``rel_path`` matches any stored rule.

        This is a simplified implementation without negation or globbing.
        """
        for rule in self._rules:
            if rule.kind == "dir":
                # Directory match: prefix with directory + '/'
                prefix = f"{rule.value}/" if rule.value else ""
                if rel_path.startswith(prefix):
                    return True
            else:  # file
                # Only match root-level file names exactly
                if "/" not in rel_path and rel_path == rule.value:
                    return True
        return False
