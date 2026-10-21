"""Scored eval for the Loupe demo documentation agent.

Each case runs the real agent (demo/agent.py) and is scored on:
  retrieval - the expected document is in the top-2 documents the agent searched
  answer    - the answer contains every expected term
  failure   - for injected-failure cases, the agent raises the expected error
Every case is also emitted to Loupe as an `eval case` span with eval.* attributes.
Deterministic: no model or paid API calls.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))
sys.path.insert(0, str(ROOT / "demo"))
from loupe import configure, Span  # noqa: E402
import agent  # noqa: E402


def score_case(case):
    """Return (checks, detail). checks maps check name -> bool."""
    if case.get("inject_failure"):
        try:
            agent.run(case["question"], fail=True)
            return {"failure": False}, "no error raised"
        except Exception as e:  # the agent is expected to fail here
            ok = type(e).__name__ == case["expect_error"]
            return {"failure": ok}, type(e).__name__
    docs = agent.search_docs(case["question"])
    answer = agent.run(case["question"])
    checks = {
        "retrieval": case["expect_doc"] in [d[0] for d in docs],
        "answer": all(t.lower() in answer.lower() for t in case["expect_terms"]),
    }
    return checks, ",".join(d[0] for d in docs)


def run_eval(cases, client=None):
    results = []
    for case in cases:
        with Span("eval case", attributes={"eval.case_id": case["id"],
                                           "eval.kind": "failure" if case.get("inject_failure") else "quality"}) as span:
            checks, detail = score_case(case)
            passed = all(checks.values())
            span.set_attribute("eval.passed", passed)
            span.set_attribute("eval.checks_passed", sum(checks.values()))
            span.set_attribute("eval.checks_total", len(checks))
        results.append({"id": case["id"], "passed": passed, "checks": checks, "detail": detail})
    return results


def summarize(results):
    total = len(results)
    passed = sum(r["passed"] for r in results)
    checks = [v for r in results for v in r["checks"].values()]
    return {"cases": total, "cases_passed": passed,
            "pass_rate": round(passed / total, 3) if total else 0.0,
            "checks": len(checks), "checks_passed": sum(checks)}


if __name__ == "__main__":
    cases = json.loads((Path(__file__).parent / "cases.json").read_text())
    client = configure(endpoint=os.environ.get("LOUPE_ENDPOINT", "http://127.0.0.1:4173"),
                       api_key=os.environ.get("LOUPE_API_KEY"), service_name="docs-agent-eval", flush_interval=.3)
    results = run_eval(cases)
    for r in results:
        print(("PASS" if r["passed"] else "FAIL"), r["id"], r["checks"], r["detail"])
    summary = summarize(results)
    print("Summary:", json.dumps(summary))
    exported = client.flush(timeout=15)
    print("Export:", client.diagnostics)
    client.close()
    sys.exit(0 if summary["cases_passed"] == summary["cases"] and exported else 1)
