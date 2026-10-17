"""Generic receipt/storage/scoring code must not import a domain adapter."""
import ast
from pathlib import Path


def test_generic_core_does_not_import_domain_policy_or_settlement_modules():
    root = Path(__file__).resolve().parents[1] / "selflearn_core"
    areas = [root / "probability_receipts.py", root / "outcome_revisions.py",
             root / "store", root / "scoring"]
    violations = []
    for area in areas:
        for path in ([area] if area.is_file() else sorted(area.rglob("*.py"))):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                modules = ([node.module or ""] if isinstance(node, ast.ImportFrom) else
                           [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
                if any(any(part == "adapters" or part == "nfl" or part.startswith("nfl_")
                           for part in module.split("."))
                       for module in modules):
                    violations.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not violations, "Domain-specific policy belongs in adapters: " + ", ".join(violations)
