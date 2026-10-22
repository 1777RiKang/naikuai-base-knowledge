#!/usr/bin/env python3
"""Validate and compile the glossary, preserving evidence and attribution."""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from common import ROOT, load_terms, norm, read_version, tokenize
from validate import collect_errors

REPOSITORY_URL = "https://github.com/1777RiKang/naikuai-base-knowledge"
DATA_LICENSE = "CC-BY-NC-SA-4.0"
SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
CREATE TABLE entries (
  id TEXT PRIMARY KEY, term TEXT NOT NULL, canonical TEXT NOT NULL,
  kind TEXT NOT NULL, status TEXT NOT NULL, source TEXT NOT NULL,
  version INTEGER NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE entry_aliases (
  entry_id TEXT NOT NULL REFERENCES entries(id),
  alias TEXT NOT NULL, alias_norm TEXT NOT NULL UNIQUE
);
CREATE INDEX idx_alias_norm ON entry_aliases(alias_norm);
CREATE TABLE entry_names (
  entry_id TEXT NOT NULL REFERENCES entries(id), name TEXT NOT NULL,
  name_norm TEXT NOT NULL, kind TEXT NOT NULL,
  PRIMARY KEY(entry_id, name, kind)
);
CREATE INDEX idx_name_norm ON entry_names(name_norm);
CREATE TABLE senses (
  entry_id TEXT NOT NULL REFERENCES entries(id), seq INTEGER NOT NULL,
  context TEXT NOT NULL, definition TEXT NOT NULL, notes TEXT,
  PRIMARY KEY(entry_id, seq)
);
CREATE TABLE relations (
  entry_id TEXT NOT NULL REFERENCES entries(id), kind TEXT NOT NULL,
  target TEXT NOT NULL REFERENCES entries(id) DEFERRABLE INITIALLY DEFERRED,
  PRIMARY KEY(entry_id, kind, target)
);
CREATE TABLE evidence (
  entry_id TEXT NOT NULL REFERENCES entries(id), seq INTEGER NOT NULL,
  text TEXT NOT NULL, channel TEXT NOT NULL, captured_at TEXT NOT NULL,
  redacted INTEGER NOT NULL, PRIMARY KEY(entry_id, seq)
);
CREATE TABLE entry_metadata (
  entry_id TEXT PRIMARY KEY REFERENCES entries(id), source_url TEXT,
  reviewer TEXT, reviewed_at TEXT, source_path TEXT NOT NULL,
  document_json TEXT NOT NULL
);
CREATE TABLE contributors (
  entry_id TEXT NOT NULL REFERENCES entries(id), seq INTEGER NOT NULL,
  name TEXT NOT NULL, github TEXT, role TEXT NOT NULL,
  PRIMARY KEY(entry_id, seq)
);
CREATE TABLE terms (
  token TEXT NOT NULL, kind TEXT NOT NULL,
  ref TEXT NOT NULL REFERENCES entries(id), PRIMARY KEY(token, kind, ref)
);
CREATE INDEX idx_terms_token ON terms(token);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE VIEW ai_entries AS SELECT * FROM entries WHERE status = 'confirmed';
"""


def build(out_dir: Path):
    docs = load_terms()
    errors = collect_errors(docs)
    if errors:
        raise ValueError("构建前校验失败：\n" + "\n".join(errors))
    out_dir.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="glossary-", suffix=".sqlite", dir=out_dir)
    os.close(fd)
    db_path = Path(temporary)
    conn = sqlite3.connect(db_path)
    built_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    version = read_version()
    source_commit = os.environ.get("GITHUB_SHA", "local")
    counts = {"entries": len(docs), "aliases": 0, "senses": 0, "evidence": 0,
              "confirmed": sum(d["review"]["status"] == "confirmed" for _, d in docs)}
    try:
        conn.executescript(SCHEMA_SQL)
        for path, doc in docs:
            entry_id = doc["id"]
            conn.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?)", (
                entry_id, doc["term"], doc["canonical"], doc["kind"],
                doc["review"]["status"], doc["provenance"]["source"],
                doc["version"], doc["updated_at"]))
            for kind, names in (("term", [doc["term"]]), ("canonical", [doc["canonical"]]),
                                ("alias", doc.get("aliases", []))):
                for name in names:
                    conn.execute("INSERT INTO entry_names VALUES (?,?,?,?)",
                                 (entry_id, name, norm(name), kind))
            names = {doc["term"], doc["canonical"], *doc.get("aliases", [])}
            for token in sorted(set().union(*(tokenize(n) for n in names))):
                conn.execute("INSERT INTO terms VALUES (?,?,?)", (token, "entry", entry_id))
            content = " ".join(" ".join(str(s.get(k, "")) for k in ("context", "definition", "notes"))
                               for s in doc["senses"])
            for token in sorted(tokenize(content)):
                conn.execute("INSERT INTO terms VALUES (?,?,?)", (token, "content", entry_id))
            for alias in doc.get("aliases", []):
                conn.execute("INSERT INTO entry_aliases VALUES (?,?,?)", (entry_id, alias, norm(alias)))
                counts["aliases"] += 1
            for seq, sense in enumerate(doc["senses"], 1):
                conn.execute("INSERT INTO senses VALUES (?,?,?,?,?)", (
                    entry_id, seq, sense["context"], sense["definition"], sense.get("notes")))
                counts["senses"] += 1
            for seq, ev in enumerate(doc["provenance"].get("evidence", []), 1):
                conn.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?)", (
                    entry_id, seq, ev["text"], ev["channel"], ev["captured_at"], int(ev["redacted"])))
                counts["evidence"] += 1
            for kind, targets in doc.get("relations", {}).items():
                for target in targets:
                    conn.execute("INSERT INTO relations VALUES (?,?,?)", (entry_id, kind, target))
            conn.execute("INSERT INTO entry_metadata VALUES (?,?,?,?,?,?)", (
                entry_id, doc["provenance"].get("url"), doc["review"].get("reviewer"),
                doc["review"].get("reviewed_at"), path.relative_to(ROOT).as_posix(),
                json.dumps(doc, ensure_ascii=False)))
            for seq, contributor in enumerate(doc["contributors"], 1):
                conn.execute("INSERT INTO contributors VALUES (?,?,?,?,?)", (
                    entry_id, seq, contributor["name"], contributor.get("github"), contributor["role"]))
        metadata = {
            "schema_version": "2", "glossary_version": version, "built_at": built_at,
            "entry_count": str(len(docs)), "source_commit": source_commit,
            "data_license": DATA_LICENSE,
            "data_license_url": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
            "attribution": "奶块知识库（玩家整理）", "repository_url": REPOSITORY_URL,
            "default_ai_status": "confirmed",
        }
        conn.executemany("INSERT INTO meta VALUES (?,?)", metadata.items())
        conn.commit()
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity_check 失败")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("SQLite foreign_key_check 失败")
    except BaseException:
        conn.close()
        db_path.unlink(missing_ok=True)
        raise
    conn.close()
    target = out_dir / "glossary.sqlite"
    os.replace(db_path, target)
    manifest = {
        "schema_version": 2, "glossary_version": version, "built_at": built_at,
        "source_commit": source_commit, "data_license": DATA_LICENSE,
        "data_license_url": metadata["data_license_url"],
        "attribution": metadata["attribution"], "repository_url": REPOSITORY_URL,
        "default_ai_status": "confirmed",
        "sqlite": {"path": "glossary.sqlite", "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                   "bytes": target.stat().st_size}, "counts": counts,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name in ("DATA-LICENSE.md", "LICENSE"):
        (out_dir / name).write_bytes((ROOT / name).read_bytes())
    return manifest


if __name__ == "__main__":
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist"
    print(json.dumps(build(destination), ensure_ascii=False, indent=2))
