"""Read-only retrieval CLI. Only confirmed entries are returned by default."""
import argparse
import json
import sqlite3
from pathlib import Path

from common import norm, tokenize


def search(db_path, text, limit=5, include_unconfirmed=False):
    if not text.strip() or limit < 1:
        return []
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        candidates = {}
        status = "" if include_unconfirmed else " AND e.status = 'confirmed'"
        for row in conn.execute(
            "SELECT DISTINCT e.id FROM entry_names n JOIN entries e ON e.id=n.entry_id "
            "WHERE n.name_norm=?" + status, (norm(text),)):
            candidates[row["id"]] = 10000
        tokens = sorted(tokenize(norm(text)))
        if tokens:
            slots = ",".join("?" for _ in tokens)
            for row in conn.execute(
                "SELECT e.id, SUM(CASE t.kind WHEN 'entry' THEN 4 ELSE 1 END) score "
                "FROM terms t JOIN entries e ON e.id=t.ref "
                f"WHERE t.token IN ({slots})" + status + " GROUP BY e.id", tokens):
                candidates[row["id"]] = candidates.get(row["id"], 0) + row["score"]
        results = []
        for entry_id, score in sorted(candidates.items(), key=lambda item: (-item[1], item[0]))[:limit]:
            row = conn.execute("SELECT document_json, source_path FROM entry_metadata WHERE entry_id=?",
                               (entry_id,)).fetchone()
            document = json.loads(row["document_json"])
            document["retrieval"] = {"score": score, "source_path": row["source_path"]}
            results.append(document)
        return results
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("text")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--include-unconfirmed", action="store_true")
    args = parser.parse_args()
    print(json.dumps(search(args.database, args.text, args.limit, args.include_unconfirmed),
                     ensure_ascii=False, indent=2))
