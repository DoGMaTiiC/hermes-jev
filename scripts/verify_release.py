"""Verificação do release contra RELEASE_MANIFEST.sha256 (#24).

Uso:
    python3 scripts/verify_release.py [--manifest PATH] [--root PATH]

Compara o SHA-256 de cada arquivo listado no manifest com o conteúdo em
disco. Qualquer divergência (hash diferente, arquivo ausente, linha
malformada, entrada duplicada) é FALHA: imprime o motivo e sai com 1.
Só stdlib, só leitura, roda offline.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE.parent
DEFAULT_MANIFEST = HERE.parent / "RELEASE_MANIFEST.sha256"

_LINE_RE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(\S+)\s*$")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_manifest(text: str) -> list[tuple[str, str]]:
    """Linhas ``<sha256>  <caminho>``; brancas e `#` são ignoradas."""
    entries = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            raise ValueError(f"linha {lineno} malformada: {raw!r}")
        entries.append((m.group(1).lower(), m.group(2)))
    seen = set()
    for digest, rel in entries:
        if rel in seen:
            raise ValueError(f"entrada duplicada: {rel!r}")
        seen.add(rel)
    return entries


def verify(manifest_path: str | Path, root: str | Path) -> list[tuple[str, str, str]]:
    """Confere cada entrada; retorna [(status, caminho, detalhe)]."""
    manifest_path = Path(manifest_path)
    root = Path(root)
    entries = parse_manifest(manifest_path.read_text(encoding="utf-8"))
    if not entries:
        raise ValueError("manifest vazio: nada para verificar")
    results = []
    for digest, rel in entries:
        target = root / rel
        if not target.is_file():
            results.append(("MISSING", rel, "arquivo ausente"))
            continue
        actual = sha256_of(target)
        if actual != digest:
            results.append(("MISMATCH", rel, f"esperado {digest[:12]}…, achado {actual[:12]}…"))
        else:
            results.append(("OK", rel, ""))
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verifica o release contra o manifest SHA-256")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    args = parser.parse_args(argv)
    try:
        results = verify(args.manifest, args.root)
    except (OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    bad = 0
    for status, rel, detail in results:
        suffix = f" ({detail})" if detail else ""
        print(f"{status}  {rel}{suffix}")
        if status != "OK":
            bad += 1
    print(f"# {len(results) - bad}/{len(results)} íntegros")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
