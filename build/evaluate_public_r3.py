"""Fixed natural-language retrieval checks; no model calls or chat collection."""
import argparse
import importlib.util
import json
from pathlib import Path
import statistics
import time

ROOT = Path(__file__).resolve().parents[1]


def evaluate(database, cases_path=ROOT / "eval/retrieval_cases.json", query_module=None):
    path = query_module or ROOT / "public/scripts/kb_query_public.py"
    spec = importlib.util.spec_from_file_location("evaluated_public_query", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = json.loads(Path(cases_path).read_text(encoding="utf-8"))["cases"]
    kb = module.PublicKB(str(database))
    rows = []
    try:
        for case in cases:
            started = time.perf_counter()
            result = kb.lookup(case["question"])
            elapsed = (time.perf_counter() - started) * 1000
            chunks = result.get("chunks", [])
            ids = {c["chunk_id"] for c in chunks}
            sections = [c["section"] for c in chunks]
            text = "\n".join(c["text"] for c in chunks)
            errors = []
            if result["action"] != case["action"]:
                errors.append("action")
            if case.get("empty") and (chunks or result.get("facts")):
                errors.append("expected_empty")
            if "top_section" in case and (not sections or case["top_section"] not in sections[0]):
                errors.append("top_section")
            if not set(case.get("required_ids", [])).issubset(ids):
                errors.append("missing_members")
            if any(token in section for token in case.get("forbidden_sections", []) for section in sections):
                errors.append("forbidden_section")
            if any(token not in text for token in case.get("text_contains", [])):
                errors.append("missing_evidence")
            if any(token not in result.get("prompt", "") for token in case.get("prompt_contains", [])):
                errors.append("clarify_prompt")
            rows.append({"id": case["id"], "passed": not errors, "errors": errors,
                         "action": result["action"], "sections": sections,
                         "chunk_ids": sorted(ids), "ms": round(elapsed, 2)})
    finally:
        kb.conn.close()
    times = sorted(r["ms"] for r in rows)
    return {"cases": len(rows), "passed": sum(r["passed"] for r in rows),
            "median_ms": statistics.median(times), "p95_ms": times[min(len(times)-1, int(len(times)*.95))],
            "results": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(args.database)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(f"Natural-language retrieval: {report['passed']}/{report['cases']} cases passed")
    raise SystemExit(int(report["passed"] != report["cases"]))
