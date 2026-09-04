"""K. No float in the money-bearing packages (domain/, contracts/, db/, ledger/)."""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "baaki"
GUARDED = ["domain", "contracts", "db", "ledger", "pipeline"]   # policy/ and rules_agent/ carry probability floats; see tests/arch/test_phase2_boundary.py
ALLOWED_FLOAT_FIELDS = {"confidence", "effective_confidence", "lo", "hi", "effective", "conf", "band_c_top", "rupees"}   # probabilities / band bounds / grammar intermediates (never Paise)


def test_no_float_literals_or_annotations_on_money():
    for pkg in GUARDED:
        for f in (SRC / pkg).rglob("*.py"):
            tree = ast.parse(f.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, float):
                    raise AssertionError(f"float literal in {f}: {node.value}")
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    ann = ast.unparse(node.annotation)
                    if "float" in ann and node.target.id not in ALLOWED_FLOAT_FIELDS:
                        raise AssertionError(f"float annotation in {f}: {node.target.id}: {ann}")
