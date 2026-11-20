"""The examiner: runs every case through the real agent, scores it, saves the results
and prints the report card.

Run from the project root:
    python -m eval.run_suite --name first --repeats 1          # quick first look
    python -m eval.run_suite --name baseline                    # 3 repeats per case
    python -m eval.run_suite --name hardened --harden
    python -m eval.run_suite --name dbg --only indirect-01      # one case, for debugging
    python -m eval.run_suite --name smoke --fake                # no model: checks the plumbing

Each run writes results/<name>.jsonl (one line per case per repeat, written as it goes,
so a crash or Ctrl-C keeps what finished) and prints the summary.
"""
import argparse
import json
import math
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src.agent import Agent
from src.config import CFG
from src.rag import Retriever
from src.tools import ToolBox

from .attacks import ATTACKS, BENIGN
from .scoring import score_attack, score_benign

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
MAX_ERRORS_IN_A_ROW = 3   # stop early if the model is unreachable instead of failing 70 times


# --------------------------------------------------------------------------- helpers
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% confidence range for a rate of k out of n. With small n the range is wide,
    which is the honest answer: 1 success out of 3 tells you very little."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def pct(k: int, n: int) -> str:
    if n == 0:
        return "    -"
    lo, hi = wilson(k, n)
    return f"{100 * k / n:4.0f}%  ({100 * lo:3.0f}-{100 * hi:3.0f}%)"


def fake_chat(messages):
    """Stand-in model for --fake: always refuses politely. Tests the plumbing only;
    its numbers say nothing about any real model."""
    return json.dumps({"action": "final", "answer": "Sorry, I can't help with that request."})


def make_chat(fake: bool, use_cache: bool):
    if fake:
        return fake_chat
    from src.llm import chat   # imported here so --fake works without Ollama installed
    return lambda messages: chat(messages, use_cache=use_cache)


def run_case(case: dict, harden: bool, chat) -> tuple[dict, ToolBox]:
    """Fresh retriever and toolbox for every run, so nothing leaks between cases."""
    retriever = Retriever()
    if case.get("poison"):
        retriever.add_document(case["poison"]["name"], case["poison"]["text"])
    box = ToolBox(harden=harden)
    out = Agent(retriever, chat, box).respond(case["message"])
    return out, box


# ------------------------------------------------------------------------------ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the red-team suite against the support bot.")
    ap.add_argument("--name", required=True, help="results go to results/<name>.jsonl")
    ap.add_argument("--harden", action="store_true", help="turn the tool checks on")
    ap.add_argument("--repeats", type=int, default=3, help="runs per case (default 3)")
    ap.add_argument("--only", default="", help="only cases whose id starts with this")
    ap.add_argument("--skip-benign", action="store_true", help="attacks only")
    ap.add_argument("--fake", action="store_true", help="scripted model, no Ollama")
    ap.add_argument("--max-asr", type=float, default=None,
                    help="exit with code 1 if overall attack success rate is above this (0-1)")
    args = ap.parse_args(argv)

    # Repeats are only meaningful if every call really goes to the model.
    use_cache = CFG.use_cache and args.repeats == 1
    chat = make_chat(args.fake, use_cache)
    model = "fake" if args.fake else CFG.local_model

    attacks = [c for c in ATTACKS if c["id"].startswith(args.only)]
    benign = [] if args.skip_benign else [c for c in BENIGN if c["id"].startswith(args.only)]
    jobs = [("attack", c) for c in attacks] + [("benign", c) for c in benign]
    if not jobs:
        print(f"No cases match --only {args.only!r}")
        return 2

    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{args.name}.jsonl"
    total = len(jobs) * args.repeats
    print(f"run: {args.name}   model: {model}   tool checks: {'on' if args.harden else 'off'}"
          f"   repeats: {args.repeats}   cache: {'on' if use_cache else 'off'}")
    print(f"{len(attacks)} attacks + {len(benign)} benign = {total} runs  ->  {path}\n")

    records, errors_in_a_row, done = [], 0, 0
    started = datetime.now().isoformat(timespec="seconds")
    with path.open("w", encoding="utf-8") as f:
        for repeat in range(1, args.repeats + 1):
            for kind, case in jobs:
                done += 1
                t0 = time.time()
                rec = {"run": args.name, "started": started, "model": model,
                       "harden": args.harden, "repeat": repeat, "kind": kind,
                       "id": case["id"], "family": case.get("family", "benign"),
                       "technique": case.get("technique", "")}
                try:
                    out, box = run_case(case, args.harden, chat)
                    verdict = (score_attack if kind == "attack" else score_benign)(case, out, box)
                    rec.update(verdict, answer=out["answer"], tool_calls=out["tool_calls"],
                               rounds=out["rounds"], chunks=[c["source"] for c in out["chunks"]],
                               refunds=box.db["refunds"], error=None)
                    errors_in_a_row = 0
                except Exception as e:   # one broken case must not kill the whole run
                    rec.update(error=f"{type(e).__name__}: {e}")
                    errors_in_a_row += 1
                rec["seconds"] = round(time.time() - t0, 1)
                records.append(rec)
                f.write(json.dumps(rec) + "\n")
                f.flush()

                if rec["error"]:
                    mark = "ERROR"
                elif kind == "attack":
                    mark = "BREACHED" if rec["success"] else "held"
                else:
                    mark = "pass" if rec["passed"] else "FAIL"
                print(f"[{done:3}/{total}] r{repeat} {case['id']:<16} {mark:<8} {rec['seconds']:5.1f}s")

                if errors_in_a_row >= MAX_ERRORS_IN_A_ROW:
                    print(f"\nStopping: {MAX_ERRORS_IN_A_ROW} errors in a row. Last error:\n"
                          f"  {rec['error']}\nIs Ollama running? Try: ollama list")
                    summarise(records)
                    return 3

    overall_asr = summarise(records)
    if args.max_asr is not None and overall_asr is not None and overall_asr > args.max_asr:
        print(f"\nFAILED GATE: attack success rate {overall_asr:.0%} is above --max-asr {args.max_asr:.0%}")
        return 1
    return 0


def summarise(records: list[dict]):
    """Print the report card. Returns the overall attack success rate (or None)."""
    ok = [r for r in records if not r["error"]]
    errors = len(records) - len(ok)
    att = [r for r in ok if r["kind"] == "attack"]
    ben = [r for r in ok if r["kind"] == "benign"]

    print("\n" + "=" * 66)
    print(f"{'family':<20}{'runs':>6}{'breached':>10}   attack success (95% range)")
    print("-" * 66)
    by_family = defaultdict(list)
    for r in att:
        by_family[r["family"]].append(r)
    for fam, rs in sorted(by_family.items()):
        k = sum(r["success"] for r in rs)
        print(f"{fam:<20}{len(rs):>6}{k:>10}   {pct(k, len(rs))}")
    k_all = sum(r["success"] for r in att)
    print("-" * 66)
    print(f"{'OVERALL':<20}{len(att):>6}{k_all:>10}   {pct(k_all, len(att))}")

    passed = sum(r["passed"] for r in ben)
    print(f"\n{'benign pass rate':<20}{len(ben):>6}{passed:>10}   {pct(passed, len(ben))}")

    # indirect attacks whose planted page never reached the prompt were not really tested
    unreached = [r for r in att if r.get("poison_retrieved") is False]
    if unreached:
        print(f"\nnote: {len(unreached)} indirect run(s) never retrieved their planted page: "
              + ", ".join(sorted({r['id'] for r in unreached})))
    flagged = [r for r in att if r["success"] and r.get("details", {}).get("looks_like_refusal")]
    if flagged:
        print(f"note: {len(flagged)} breach(es) look like refusals that quoted the canary; "
              "review by hand: " + ", ".join(sorted({r['id'] for r in flagged})))
    if errors:
        print(f"note: {errors} run(s) errored and are left out of the numbers")

    # per case, so you can see which attacks actually work
    print("\nper case (breached or failed / runs):")
    per = defaultdict(lambda: [0, 0])
    for r in ok:
        bad = r["success"] if r["kind"] == "attack" else not r["passed"]
        per[r["id"]][0] += bad
        per[r["id"]][1] += 1
    for cid, (k, n) in per.items():
        print(f"  {cid:<18}{k}/{n}")
    print("=" * 66)
    return (k_all / len(att)) if att else None


if __name__ == "__main__":
    sys.exit(main())
