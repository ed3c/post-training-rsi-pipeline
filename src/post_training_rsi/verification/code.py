from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CodeDecision:
    safe: bool
    reasons: tuple[str, ...]


class PythonStaticVerifier:
    """Syntax/import/call gate. It never executes generated code."""

    def __init__(self, allowed_imports: tuple[str, ...]) -> None:
        self.allowed_imports = set(allowed_imports)
        self.forbidden_calls = {"eval", "exec", "compile", "open", "__import__"}
        self.forbidden_roots = {"subprocess", "socket"}

    def verify(self, code: str | None) -> CodeDecision:
        if not code or not code.strip():
            return CodeDecision(safe=True, reasons=())
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return CodeDecision(safe=False, reasons=(f"PYTHON_SYNTAX:{exc.msg}",))
        reasons: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", maxsplit=1)[0]
                    if root not in self.allowed_imports:
                        reasons.append(f"DISALLOWED_IMPORT:{root}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".", maxsplit=1)[0]
                if root and root not in self.allowed_imports:
                    reasons.append(f"DISALLOWED_IMPORT:{root}")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in self.forbidden_calls:
                    reasons.append(f"DISALLOWED_CALL:{node.func.id}")
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                    if node.func.value.id in self.forbidden_roots:
                        reasons.append(
                            f"DISALLOWED_CALL:{node.func.value.id}.{node.func.attr}"
                        )
        return CodeDecision(safe=not reasons, reasons=tuple(sorted(set(reasons))))
