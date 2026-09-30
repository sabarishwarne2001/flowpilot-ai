"""Print backend Settings fields as TSV: class, name, type, default, in .env.example, in .env.production.template.

Run from backend/: python ../docs/hardening/tools/config_vars.py > OUT.tsv
"""
import ast
import re


def envkeys(path):
    keys = set()
    for line in open(path, encoding="utf-8-sig"):
        m = re.match(r"\s*#?\s*([A-Z][A-Z0-9_]+)\s*=", line)
        if m:
            keys.add(m.group(1))
    return keys


tree = ast.parse(open("app/core/config.py", encoding="utf-8-sig").read())
example, prod = envkeys(".env.example"), envkeys(".env.production.template")
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef):
        for n in node.body:
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id.isupper():
                default = ast.unparse(n.value) if n.value is not None else "<required>"
                default = default.replace("\t", " ").replace("\n", " ")[:80]
                name = n.target.id
                print(f"{node.name}\t{name}\t{ast.unparse(n.annotation)[:40]}\t{default}\t"
                      f"{'Y' if name in example else 'N'}\t{'Y' if name in prod else 'N'}")
