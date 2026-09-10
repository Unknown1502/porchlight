#!/usr/bin/env python3
"""Cross-platform task runner.  ``python tasks.py <target>``

Why this exists rather than just a Makefile: `make` is not present on a stock
Windows install, so every command in the README — `make test`, `make run`,
`make eval` — failed for a judge on Windows with "command not found". A judge who
cannot run the project does not score it.

The Makefile now delegates here, so each target is defined exactly once. Add a
target by adding a function to TARGETS; the Makefile needs no change.

    python tasks.py            # list targets
    python tasks.py test
    python tasks.py demo-replay
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable


def _env() -> dict[str, str]:
    """Environment with src/ importable, without requiring an editable install."""
    env = dict(os.environ)
    src = str(ROOT / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else src
    return env


def run(*cmd: str, check: bool = True) -> int:
    printable = " ".join(str(c) for c in cmd)
    print(f"\n$ {printable}", flush=True)
    proc = subprocess.run([str(c) for c in cmd], cwd=ROOT, env=_env())
    if check and proc.returncode != 0:
        sys.exit(proc.returncode)
    return proc.returncode


# --------------------------------------------------------------------------
# Targets
# --------------------------------------------------------------------------
def install() -> None:
    """Install dev dependencies and the package in editable mode."""
    run(PY, "-m", "pip", "install", "-r", "requirements-dev.txt")
    run(PY, "-m", "pip", "install", "-e", ".")


def corpus() -> None:
    """Regenerate the synthetic corpus and its ground-truth labels."""
    run(PY, "corpus/generate.py", "--out", "corpus/seed", "--count", "60", "--campaigns", "1")


def run_server() -> None:
    """Serve the decision inbox (and the AgentCore Runtime contract) on :8080."""
    run(PY, "-m", "uvicorn", "porchlight.server:app", "--reload", "--port", "8080")


def demo() -> None:
    """Replay the corpus through the pipeline in the terminal."""
    if not (ROOT / "corpus" / "seed" / "reports.json").exists():
        corpus()
    run(PY, "-m", "porchlight.cli", "replay", "--dir", "corpus/seed", "--speed", "8")


def evaluate() -> None:
    """Run the evaluation harness and its pass/fail gates."""
    run(PY, "eval/run_eval.py", "--corpus", "corpus/seed", "--labels", "eval/labels.json")


def seeds() -> None:
    """Run the evaluation across several corpus seeds and report the spread."""
    run(PY, "eval/run_seeds.py", "--seeds", "5")


def design() -> None:
    """Check the dashboard's colour contrast and accessibility structure."""
    run(PY, "scripts/check_design.py")


def test() -> None:
    """Full test suite."""
    run(PY, "-m", "pytest", "-q")


def injection() -> None:
    """Adversarial suite only."""
    run(PY, "-m", "pytest", "-q", "tests/injection", "-s")


def lint() -> None:
    """Lint the source, corpus, eval and tests."""
    run(PY, "-m", "ruff", "check", "src", "corpus", "eval", "tests")


def docs() -> None:
    """Regenerate docs/prompts.md from the prompt modules."""
    run(PY, "scripts/gen_prompt_docs.py")


def check() -> None:
    """Everything CI runs. The one command to trust before submitting."""
    lint()
    design()
    test()
    corpus()
    evaluate()
    seeds()
    print("\nAll checks passed.")


def deploy() -> None:
    """Preflight the AgentCore Runtime deployment (dry run; --apply to deploy)."""
    if shutil.which("bash") is None:
        sys.exit("deploy needs bash. On Windows use Git Bash: bash deploy/deploy_runtime.sh")
    run("bash", "deploy/deploy_runtime.sh")


def clean() -> None:
    """Remove generated artefacts. Never touches tracked source."""
    targets = [
        ROOT / "eval" / "out",
        ROOT / ".pytest_cache",
        ROOT / ".ruff_cache",
        ROOT / "corpus" / "seed" / "reports.json",
    ]
    for t in targets:
        if t.is_dir():
            shutil.rmtree(t, ignore_errors=True)
            print(f"removed {t.relative_to(ROOT)}/")
        elif t.exists():
            t.unlink()
            print(f"removed {t.relative_to(ROOT)}")


TARGETS = {
    "install": install,
    "corpus": corpus,
    "run": run_server,
    "demo": demo,
    "eval": evaluate,
    "seeds": seeds,
    "design": design,
    "test": test,
    "injection": injection,
    "lint": lint,
    "docs": docs,
    "check": check,
    "deploy": deploy,
    "clean": clean,
}


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in {"-h", "--help", "help"}:
        width = max(len(n) for n in TARGETS)
        print(__doc__.split("\n\n")[0])
        print("\nTargets:")
        for name, fn in TARGETS.items():
            print(f"  {name:<{width}}  {(fn.__doc__ or '').splitlines()[0]}")
        return 0
    unknown = [a for a in args if a not in TARGETS]
    if unknown:
        print(f"unknown target(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"available: {', '.join(TARGETS)}", file=sys.stderr)
        return 2
    for a in args:
        TARGETS[a]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
