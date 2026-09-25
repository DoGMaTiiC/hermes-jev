"""Testes do verificador de release (#24) — determinístico.

Run: python3 scripts/test_verify_release.py

Os testes de fixture nunca tocam nos arquivos reais do repo: cada um
monta um root temporário com arquivos + manifest próprios e confere que
verify() passa no íntegro e FALHA em divergência (mutação, ausência,
manifest malformado/duplicado/vazio). O último teste é a exceção: roda
verify() contra o manifest REAL, então a suíte quebra se o release
divergir do que foi shipado.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_verify():
    spec = importlib.util.spec_from_file_location(
        "verify_release_under_test", HERE / "verify_release.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


verify_mod = _load_verify()


def _fixture(root, files=None) -> Path:
    """Escreve arquivos e um manifest válido; retorna o manifest."""
    files = files if files is not None else {
        "contracts/tool-gate-v1.json": '{"gate_version": "v2"}\n',
        "plugins/jev-judge/gate.py": "# gate\n",
        "SOURCE_PROVENANCE.md": "# provenance\n",
    }
    lines = []
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        lines.append(f"{digest}  {rel}\n")
    manifest = root / "RELEASE_MANIFEST.sha256"
    manifest.write_text(
        "# fixture\n" + "".join(sorted(lines)), encoding="utf-8"
    )
    return manifest


def _ok(results) -> bool:
    return bool(results) and all(s == "OK" for s, _, _ in results)


def test_pass_on_intact_tree():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = _fixture(root)
        results = verify_mod.verify(manifest, root)
        assert _ok(results), results
        assert len(results) == 3, results
    print("ok  árvore íntegra passa (3/3 OK)")


def test_mutation_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = _fixture(root)
        (root / "plugins/jev-judge/gate.py").write_text("# gate adulterado\n", encoding="utf-8")
        results = verify_mod.verify(manifest, root)
        assert not _ok(results), results
        bad = [(s, r) for s, r, _ in results if s != "OK"]
        assert bad == [("MISMATCH", "plugins/jev-judge/gate.py")], results
    print("ok  arquivo mutado falha como MISMATCH (e só ele)")


def test_missing_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = _fixture(root)
        (root / "SOURCE_PROVENANCE.md").unlink()
        results = verify_mod.verify(manifest, root)
        assert not _ok(results), results
        bad = [(s, r) for s, r, _ in results if s != "OK"]
        assert bad == [("MISSING", "SOURCE_PROVENANCE.md")], results
    print("ok  arquivo ausente falha como MISSING")


def test_malformed_manifest_raises():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = root / "RELEASE_MANIFEST.sha256"
        manifest.write_text("isto não é uma linha de manifest\n", encoding="utf-8")
        try:
            verify_mod.verify(manifest, root)
        except ValueError as exc:
            assert "malformada" in str(exc), exc
        else:
            raise AssertionError("manifest malformado devia levantar ValueError")
    print("ok  manifest malformado levanta ValueError")


def test_duplicate_entry_raises():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = _fixture(root)
        with manifest.open("a", encoding="utf-8") as fh:
            fh.write(manifest.read_text(encoding="utf-8").splitlines()[1] + "\n")
        try:
            verify_mod.verify(manifest, root)
        except ValueError as exc:
            assert "duplicada" in str(exc), exc
        else:
            raise AssertionError("entrada duplicada devia levantar ValueError")
    print("ok  entrada duplicada levanta ValueError")


def test_empty_manifest_raises():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = root / "RELEASE_MANIFEST.sha256"
        manifest.write_text("# só comentário\n", encoding="utf-8")
        try:
            verify_mod.verify(manifest, root)
        except ValueError as exc:
            assert "vazio" in str(exc), exc
        else:
            raise AssertionError("manifest vazio devia levantar ValueError")
    print("ok  manifest vazio levanta ValueError")


def test_extra_file_ignored():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = _fixture(root)
        (root / "rascunho.txt").write_text("fora do release\n", encoding="utf-8")
        assert _ok(verify_mod.verify(manifest, root)), "extra não listado não pode falhar"
    print("ok  arquivo extra não listado não falha")


def test_main_exit_codes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = _fixture(root)
        assert verify_mod.main(["--manifest", str(manifest), "--root", str(root)]) == 0
        (root / "SOURCE_PROVENANCE.md").unlink()
        assert verify_mod.main(["--manifest", str(manifest), "--root", str(root)]) == 1
    print("ok  main sai 0 no íntegro e 1 em divergência")


def test_real_manifest_passes():
    """O manifest REAL confere com o disco (fecha o P1 do rework)."""
    manifest = HERE.parent / "RELEASE_MANIFEST.sha256"
    root = HERE.parent
    results = verify_mod.verify(manifest, root)
    assert _ok(results), results
    print(f"ok  manifest real íntegro ({len(results)}/{len(results)} OK)")


if __name__ == "__main__":
    for fn in (
        test_pass_on_intact_tree,
        test_mutation_fails,
        test_missing_fails,
        test_malformed_manifest_raises,
        test_duplicate_entry_raises,
        test_empty_manifest_raises,
        test_extra_file_ignored,
        test_main_exit_codes,
        test_real_manifest_passes,
    ):
        fn()
    print("\ntodos os 9 testes do verificador passaram")
