"""Offline release preflight for the public r3 bundle.

Reads local build artifacts only. It never downloads, executes, or modifies
release content, and it never needs a publishing token.
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "public/scripts"))
from release_cache import ASSETS, parse_manifest, validate_directory  # noqa: E402

PREFLIGHT_SCHEMA = "naikuai-release-preflight/1"
DEFAULT_DIRECTORY = Path("dist/public-r3")
DEFAULT_EVALUATION = Path("dist/retrieval-evaluation.json")
ALLOWED_ENTRIES = frozenset(ASSETS | {"manifest.json"})


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _require_version(path):
    version = Path(path).read_text(encoding="utf-8").strip()
    _require(version != "", "VERSION is empty")
    return version


def _parse_manifest(raw):
    return parse_manifest(raw, _sha256(raw))


def _check_bundle_layout(directory, collected):
    # The workflow uploads every direct member of the bundle, so anything that
    # is not manifest.json or an allowlisted asset would be published too.
    directory = Path(directory)
    entries = sorted(directory.iterdir(), key=lambda item: item.name)
    names = [entry.name for entry in entries]
    unexpected = sorted(set(names) - ALLOWED_ENTRIES)
    missing = sorted(ALLOWED_ENTRIES - set(names))
    _require(not unexpected, "unexpected release entries: " + ", ".join(unexpected))
    _require(not missing, "missing release entries: " + ", ".join(missing))
    for entry in entries:
        _require(not entry.is_symlink(), entry.name + " is a symlink")
        _require(entry.is_file(), entry.name + " is not a regular file")
    collected["entries"] = names
    return names


def _check_assets(directory, manifest, collected):
    for item in manifest["files"]:
        path = Path(directory) / item["path"]
        _require(not path.is_symlink(), item["path"] + " is a symlink")
        data = path.read_bytes()
        _require(len(data) == item["size_bytes"],
                 item["path"] + " size differs from manifest")
        _require(_sha256(data) == item["sha256"],
                 item["path"] + " digest differs from manifest")
        collected.append({
            "path": item["path"],
            "size_bytes": len(data),
            "sha256": item["sha256"],
        })
    return len(manifest["files"])


def _check_public_database(directory, manifest, collected):
    # Reuse the release downloader's own validation instead of a weaker copy.
    validate_directory(directory, manifest)
    db = Path(directory) / "naikuai-kb-public.sqlite"
    conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        meta = dict(conn.execute("SELECT key,value FROM meta"))
        _require(meta.get("kb_version") == manifest.get("kb_version"),
                 "manifest kb_version differs from database meta")
        counts = {
            "chunks": conn.execute("SELECT count(*) FROM chunks").fetchone()[0],
            "aliases": conn.execute("SELECT count(*) FROM aliases").fetchone()[0],
            "facts": conn.execute("SELECT count(*) FROM facts").fetchone()[0],
            "sensitive_chunks": conn.execute(
                "SELECT count(*) FROM chunks WHERE coalesce(sensitive,0) != 0"
            ).fetchone()[0],
            "non_publishable_chunks": conn.execute(
                "SELECT count(*) FROM chunks WHERE coalesce(publishable,0) != 1"
            ).fetchone()[0],
            "non_publishable_aliases": conn.execute(
                "SELECT count(*) FROM aliases WHERE coalesce(publishable,0) != 1"
            ).fetchone()[0],
        }
        _require(counts["facts"] == 0, "public database contains private facts")
        _require(counts["sensitive_chunks"] == 0,
                 str(counts["sensitive_chunks"]) + " chunks are flagged sensitive")
        _require(counts["non_publishable_chunks"] == 0,
                 str(counts["non_publishable_chunks"]) + " chunks are not publishable")
        _require(counts["non_publishable_aliases"] == 0,
                 str(counts["non_publishable_aliases"]) + " aliases are not publishable")
    finally:
        conn.close()
    counts["sha256"] = _sha256(db.read_bytes())
    counts["size_bytes"] = db.stat().st_size
    collected.update(counts)
    return counts


def _check_evaluation(evaluation, cases_path, directory, collected):
    evaluation = Path(evaluation)
    _require(evaluation.is_file(), "evaluation report not found: " + str(evaluation))
    cases_bytes = Path(cases_path).read_bytes()
    cases = json.loads(cases_bytes.decode("utf-8")).get("cases")
    _require(isinstance(cases, list) and cases, "fixed evaluation cases are missing")
    expected = [case.get("id") for case in cases]
    _require(all(isinstance(i, str) and i for i in expected),
             "fixed evaluation cases must all have ids")
    _require(len(expected) == len(set(expected)), "duplicate evaluation case ids")

    report = _read_json(evaluation)
    results = report.get("results")
    _require(isinstance(results, list), "evaluation results are missing")
    ran = [item.get("id") for item in results]
    _require(len(ran) == len(expected),
             "evaluation ran " + str(len(ran)) + " of " + str(len(expected)) + " cases")
    _require(set(ran) == set(expected) and len(set(ran)) == len(ran),
             "evaluation case ids differ from the fixed case set")
    failed = [item.get("id") for item in results if item.get("passed") is not True]
    _require(not failed, "failed evaluation cases: " + ", ".join(str(x) for x in failed))
    _require(report.get("cases") == len(expected),
             "evaluation report case count differs from the fixed set")
    _require(report.get("passed") == len(expected),
             "evaluation report pass count is incomplete")

    # The report must bind to the exact bytes evaluated, so a stale all-pass
    # report for another database or case set cannot vouch for this bundle.
    _require(isinstance(report.get("database_sha256"), str)
             and isinstance(report.get("cases_sha256"), str),
             "evaluation report lacks database_sha256/cases_sha256 binding; "
             "re-run evaluation with the current build/evaluate_public_r3.py")
    database_sha256 = _sha256((Path(directory) / "naikuai-kb-public.sqlite").read_bytes())
    _require(report["database_sha256"] == database_sha256,
             "evaluation report database_sha256 does not match the release database; "
             "re-run evaluation on this database")
    cases_sha256 = _sha256(cases_bytes)
    _require(report["cases_sha256"] == cases_sha256,
             "evaluation report cases_sha256 does not match the current case set; "
             "re-run evaluation with these cases")

    collected.update({"cases": len(expected), "passed": len(expected), "failed": 0,
                      "database_sha256": database_sha256,
                      "cases_sha256": cases_sha256})
    return collected


def run_preflight(directory, evaluation, output=None,
                  version_path=ROOT / "VERSION",
                  cases_path=ROOT / "eval/retrieval_cases.json"):
    """Validate a built public bundle and return a machine-readable report."""
    directory = Path(directory)
    report = {
        "schema": PREFLIGHT_SCHEMA,
        "ok": False,
        "directory": str(directory),
        "evaluation": str(evaluation),
        "version": None,
        "release_version": None,
        "manifest_sha256": None,
        "layout": {},
        "assets": [],
        "database": {},
        "evaluation_result": {},
        "checks": [],
        "failures": [],
    }

    def attempt(name, func, describe=None):
        try:
            value = func()
        except Exception as exc:  # noqa: BLE001 - every failure is evidence
            reason = type(exc).__name__ + ": " + str(exc)
            report["checks"].append({"name": name, "status": "fail", "reason": reason})
            report["failures"].append({"check": name, "reason": reason})
            return None
        detail = describe(value) if describe is not None else value
        report["checks"].append({"name": name, "status": "pass", "detail": detail})
        return value

    version = attempt("version", lambda: _require_version(version_path))
    report["version"] = version

    attempt("bundle-layout", lambda: _check_bundle_layout(directory, report["layout"]),
            lambda entries: {"entries": entries})

    raw = attempt("manifest-read",
                  lambda: (directory / "manifest.json").read_bytes(),
                  lambda data: {"size_bytes": len(data)})
    manifest = None
    if raw is not None:
        manifest = attempt("manifest-contract", lambda: _parse_manifest(raw),
                           lambda data: {
                               "schema_version": data.get("schema_version"),
                               "release_version": data.get("release_version"),
                               "files": len(data.get("files", [])),
                           })
        if manifest is not None:
            report["manifest_sha256"] = _sha256(raw)
            report["release_version"] = manifest.get("release_version")

    if version is not None and manifest is not None:
        attempt("version-match", lambda: _require(
            manifest.get("release_version") == version,
            "release_version " + repr(manifest.get("release_version"))
            + " differs from VERSION " + repr(version)),
            lambda _: {"release_version": manifest.get("release_version"),
                       "version_file": version})

    if manifest is not None:
        attempt("assets", lambda: _check_assets(directory, manifest, report["assets"]))
        attempt("public-database", lambda: _check_public_database(
            directory, manifest, report["database"]))

    attempt("evaluation", lambda: _check_evaluation(
        evaluation, cases_path, directory, report["evaluation_result"]))

    report["ok"] = not report["failures"]
    if output is not None:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Offline preflight for the public r3 release artifacts.")
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY,
                        help="built public bundle directory (default: dist/public-r3)")
    parser.add_argument("--evaluation", type=Path, default=DEFAULT_EVALUATION,
                        help="fixed-case evaluation report "
                             "(default: dist/retrieval-evaluation.json)")
    parser.add_argument("--output", type=Path,
                        help="also write the JSON evidence report to this path")
    parser.add_argument("--version-file", type=Path, default=ROOT / "VERSION",
                        help="version file to compare with the manifest")
    parser.add_argument("--cases", type=Path,
                        default=ROOT / "eval/retrieval_cases.json",
                        help="fixed evaluation case set")
    args = parser.parse_args(argv)
    report = run_preflight(args.directory, args.evaluation, output=args.output,
                           version_path=args.version_file, cases_path=args.cases)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
