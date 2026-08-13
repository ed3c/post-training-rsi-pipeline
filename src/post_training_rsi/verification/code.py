from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AstSafetyResult:
    valid: bool
    errors: tuple[str, ...] = ()


class PythonAstSafetyChecker:
    """Allowlist-based static pre-filter for generated Python examples."""

    def __init__(
        self,
        allowed_import_roots: tuple[str, ...] = (
            "collections",
            "dataclasses",
            "datetime",
            "decimal",
            "fractions",
            "functools",
            "itertools",
            "json",
            "math",
            "re",
            "statistics",
            "typing",
        ),
    ) -> None:
        self.allowed_import_roots = frozenset(allowed_import_roots)

    def check(self, source: str) -> AstSafetyResult:
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return AstSafetyResult(False, (f"syntax_error:{exc.msg}:{exc.lineno}",))

        errors: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root not in self.allowed_import_roots:
                        errors.append(f"import_not_allowed:{root}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".", 1)[0]
                if root not in self.allowed_import_roots:
                    errors.append(f"import_not_allowed:{root}:{node.lineno}")
        return AstSafetyResult(not errors, tuple(errors))
