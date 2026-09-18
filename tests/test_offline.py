"""Offline tests for jev-skill-router — no network, no keys. Run: python3 tests/test_offline.py"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from jev_router import roster as R
from jev_router import router as RT

# Root __init__ loaded as a package alias (dir name has hyphens, so no plain import).
_pkg = types.ModuleType("jevskillrouter")
_pkg.__path__ = [str(BASE)]
sys.modules["jevskillrouter"] = _pkg
_spec = importlib.util.spec_from_file_location(
    "jevskillrouter", BASE / "__init__.py", submodule_search_locations=[str(BASE)]
)
plugin = importlib.util.module_from_spec(_spec)
plugin.__path__ = [str(BASE)]
sys.modules["jevskillrouter"] = plugin
_spec.loader.exec_module(plugin)


class StubClient:
    """Canned evaluate() result, or None to simulate failure. Records calls."""

    def __init__(self, first=None, second=None, confidence=None):
        self.first = first
        self.second = second
        self.confidence = confidence or {}
        self.calls = []

    def evaluate(self, state, questions):
        self.calls.append((state, questions))
        answers = self.second if any(k.startswith("fits::") for k in questions) else self.first
        if answers is None:
            return None
        return {
            "answers": answers,
            "confidence": self.confidence,
            "cost": "0.00002",
            "usage": {},
            "latency_ms": 12,
        }


class FailClient:
    def evaluate(self, state, questions):
        return None


class FakeCtx:
    def __init__(self, cfg=None):
        self.cfg = dict(cfg or {})
        self.hooks = []
        self.commands = []

    def get_config(self, key, default=None):
        return self.cfg.get(key, default)

    def set_config(self, key, value):
        self.cfg[key] = value

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))

    def register_cli_command(self, name, help, setup_fn, handler_fn, description=""):
        self.commands.append((name, help, setup_fn, handler_fn, description))


def write_skill(root, dirname, name, desc, body="Body text here."):
    d = Path(root) / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n", encoding="utf-8"
    )
    return d / "SKILL.md"


def first_answers(probs, acts=0.9, follow=0.9, prose=0.1):
    return {
        "which": {"probabilities": dict(probs)},
        "gate::acts_on_user_system": {"probability": acts},
        "gate::would_follow_documented_procedure": {"probability": follow},
        "gate::prose_suffices": {"probability": prose},
    }


def second_answers(choice, probs, fits):
    out = {"which": {"choice": choice, "probabilities": dict(probs)}}
    for name, prob in fits.items():
        out[f"fits::{name}"] = {"probability": prob}
    return out


def three_skills(tmp):
    write_skill(tmp, "a", "alpha", "Alpha does deploys.", "Alpha body " * 50)
    write_skill(tmp, "b", "beta", "Beta writes docs.", "Beta body " * 50)
    write_skill(tmp, "c", "gamma", "Gamma crunches numbers.", "Gamma body " * 50)
    return R.load_roster([tmp])


def test_frontmatter_quoted():
    data, _ = R.parse_frontmatter('---\nname: "my-skill"\ndescription: \'does "x" things\'\n---\n\nbody\n')
    assert data == {"name": "my-skill", "description": 'does "x" things'}, data
    print("ok  frontmatter com aspas")


def test_frontmatter_block():
    text = "---\nname: blk\ndescription: >-\n  line one\n  line two\n---\n\nbody\n"
    data, body = R.parse_frontmatter(text)
    assert data["description"] == "line one line two", data
    assert body.strip() == "body", repr(body)
    print("ok  frontmatter em bloco")


def test_frontmatter_missing():
    data, body = R.parse_frontmatter("just a body, no fence\n")
    assert data == {} and body.startswith("just a body"), (data, body)
    print("ok  frontmatter ausente")


def test_roster_scan_dedup():
    with tempfile.TemporaryDirectory() as tmp:
        write_skill(tmp, "a", "alpha", "First " * 40)
        write_skill(tmp, "b", "beta", "Second skill")
        write_skill(tmp, "dup", "alpha", "Duplicate name elsewhere")
        skills = R.load_roster([tmp])
        assert [s.name for s in skills] == ["alpha", "beta"], [s.name for s in skills]
        assert len(skills[0].description) <= R.INDEX_DESC_CHARS, skills[0].description
        print("ok  roster escaneou e deduplicou")


def test_skip_rules():
    assert RT.should_skip_request("/cmd args") is True
    assert RT.should_skip_request("   ") is True
    assert RT.should_skip_request("") is True
    assert RT.should_skip_request("x" * 4001, suggest_chars=4000) is True
    assert RT.should_skip_request("help <skill_relevance>old</skill_relevance>") is True
    assert RT.should_skip_request("help me deploy this") is False
    print("ok  skip rules")


def test_gate_mean_inverted():
    answers = {
        "gate::acts_on_user_system": {"probability": 0.8},
        "gate::would_follow_documented_procedure": {"probability": 0.6},
        "gate::prose_suffices": {"probability": 0.9},
    }
    assert abs(RT.gate_mean(answers) - 0.5) < 1e-9, RT.gate_mean(answers)
    print("ok  gate mean com chave invertida")


def test_chunking_250():
    skills = [
        R.Skill(name=f"s{i:03d}", description=f"Skill {i}", path=Path(f"/tmp/x/s{i:03d}/SKILL.md"))
        for i in range(250)
    ]
    groups = RT.chunk_roster(skills, 240)
    assert len(groups) == 2 and len(groups[0]) == 240 and len(groups[1]) == 10
    probs = {s.name: 0.001 for s in skills}
    probs["s000"] = 0.4
    probs[RT.NONE_OPTION] = 0.05
    stub = StubClient(first=first_answers(probs))
    ranked = RT.rank_wide(stub, "do the thing", skills, chunk=240, shortlist=3)
    assert ranked["calls"] == 2, ranked
    for _, questions in stub.calls:
        assert RT.NONE_OPTION in questions["which"]["criteria"], "chunk sem none_of_these"
    assert ranked["shortlist"], ranked
    print("ok  chunking 250 skills em 2 chunks com none_of_these")


def test_suggest_below_gate():
    with tempfile.TemporaryDirectory() as tmp:
        skills = three_skills(tmp)
        stub = StubClient(first=first_answers({"alpha": 0.8, "beta": 0.1, "gamma": 0.1},
                                              acts=0.1, follow=0.1, prose=0.9))
        assert RT.suggest(stub, "deploy it", skills) is None
        assert len(stub.calls) == 1, "gate baixo nao devia chamar request 2"
        print("ok  suggest gate baixo devolve None")


def test_suggest_clean_winner():
    with tempfile.TemporaryDirectory() as tmp:
        skills = three_skills(tmp)
        stub = StubClient(
            first=first_answers({"alpha": 0.7, "beta": 0.2, "gamma": 0.1}),
            second=second_answers("alpha", {"alpha": 0.75}, {"alpha": 0.9, "beta": 0.1, "gamma": 0.1}),
            confidence={"which": 0.8},
        )
        res = RT.suggest(stub, "deploy the site", skills)
        assert res is not None and res.skill == "alpha", res
        assert res.calls == 2 and res.gate >= 0.3, res
        print("ok  suggest vencedor limpo devolve Result")


def test_suggest_none_winner():
    with tempfile.TemporaryDirectory() as tmp:
        skills = three_skills(tmp)
        stub = StubClient(
            first=first_answers({"alpha": 0.5, "beta": 0.3, "gamma": 0.2}),
            second=second_answers(RT.NONE_OPTION, {RT.NONE_OPTION: 0.8}, {}),
        )
        assert RT.suggest(stub, "something else", skills) is None
        print("ok  suggest none_of_these devolve None")


def test_suggest_fits_below():
    with tempfile.TemporaryDirectory() as tmp:
        skills = three_skills(tmp)
        stub = StubClient(
            first=first_answers({"alpha": 0.7, "beta": 0.2, "gamma": 0.1}),
            second=second_answers("alpha", {"alpha": 0.6}, {"alpha": 0.1}),
        )
        assert RT.suggest(stub, "deploy the site", skills) is None
        print("ok  suggest fits abaixo devolve None")


def test_suggest_fail_open():
    with tempfile.TemporaryDirectory() as tmp:
        skills = three_skills(tmp)
        assert RT.suggest(FailClient(), "deploy it", skills) is None
        assert RT.suggest(StubClient(first=None), "deploy it", skills) is None
        print("ok  fail-open com client None")


def test_register_hook_and_command():
    ctx = FakeCtx()
    plugin.register(ctx)
    assert [n for n, _ in ctx.hooks] == ["pre_llm_call"], ctx.hooks
    assert [c[0] for c in ctx.commands] == ["jev-skill-router"], ctx.commands
    assert all(kw for _, cb in ctx.hooks for kw in [True] if "kwargs" in str(cb.__code__.co_varnames))
    print("ok  register hook e comando")


def test_hook_fail_open():
    ctx = FakeCtx({"mode": "off"})
    plugin.register(ctx)
    handler = ctx.hooks[0][1]
    assert handler(user_message="hello") is None
    assert handler(user_message=None) is None
    assert handler() is None
    assert handler(user_message="/own flow") is None
    print("ok  hook fail-open")


def test_cli_status_and_check():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        (home / "skills").mkdir(parents=True)
        old_home, old_key = os.environ.get("HERMES_HOME"), os.environ.get("AI_GATEWAY_API_KEY")
        os.environ["HERMES_HOME"] = str(home)
        os.environ.pop("AI_GATEWAY_API_KEY", None)
        try:
            ctx = FakeCtx({"mode": "off", "roster_dir": str(home / "skills"),
                           "log_path": str(home / "logs" / "jev-skill-router.log")})
            plugin.register(ctx)
            _, _, setup_fn, handler_fn, _ = ctx.commands[0]
            parser = argparse.ArgumentParser()
            setup_fn(parser)
            assert handler_fn(parser.parse_args(["status"])) == 0
            assert handler_fn(parser.parse_args(["check"])) == 1  # sem chave
        finally:
            if old_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = old_home
            if old_key is not None:
                os.environ["AI_GATEWAY_API_KEY"] = old_key
    print("ok  cli status e check")


if __name__ == "__main__":
    for fn in (
        test_frontmatter_quoted,
        test_frontmatter_block,
        test_frontmatter_missing,
        test_roster_scan_dedup,
        test_skip_rules,
        test_gate_mean_inverted,
        test_chunking_250,
        test_suggest_below_gate,
        test_suggest_clean_winner,
        test_suggest_none_winner,
        test_suggest_fits_below,
        test_suggest_fail_open,
        test_register_hook_and_command,
        test_hook_fail_open,
        test_cli_status_and_check,
    ):
        fn()
    print("\ntodos os testes offline passaram")
