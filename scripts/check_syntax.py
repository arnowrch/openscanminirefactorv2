"""Run AST syntax check on all firmware Python files. Used by VS Code task."""
import ast
import pathlib
import sys

root = pathlib.Path(__file__).parent.parent / "firmware"
ok = True
for f in sorted(root.rglob("*.py")):
    try:
        ast.parse(f.read_text(encoding="utf-8"))
        print(f"OK  {f.relative_to(root.parent)}")
    except SyntaxError as e:
        print(f"ERR {f.relative_to(root.parent)}:{e.lineno}: {e.msg}")
        ok = False

sys.exit(0 if ok else 1)
