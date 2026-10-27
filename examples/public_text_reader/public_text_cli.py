#!/usr/bin/env python3
"""Command line wrapper for the offline public-text reader."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from public_text import PublicTextError, PublicTextReader


def _sha_from_manifest(db_path: Path) -> str | None:
    manifest_path = db_path.with_name("manifest.json")
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for item in data.get("files", []):
        if item.get("path") == db_path.name:
            value = str(item.get("sha256", "")).strip().lower()
            return value or None
    return None


def _emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        lines = [
            f"matched={payload['matched']} action={payload['action']} reason={payload['reason']}",
            f"route={payload['route']}",
            payload["text"],
        ]
        if payload.get("clarify"):
            lines.append("选项：" + "、".join(payload["clarify"]["options"]))
        if payload.get("evidence"):
            lines.append("证据：")
            for item in payload["evidence"]:
                lines.append(
                    f"- {item['chunk_id']} | {item['source']} | {item['section']} "
                    f"| {item['returned_chars']} chars"
                    + (" | truncated" if item["truncated"] else "")
                )
        text = "\n".join(lines)
    stream = sys.stdout
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="path to the verified public SQLite file")
    parser.add_argument("--sha256", help="expected SHA-256; defaults to sibling manifest entry")
    parser.add_argument("--question", "-q", action="append", help="question; repeatable")
    parser.add_argument("--limit", type=int, help="max evidence paragraphs per call")
    parser.add_argument("--cursor", help="pagination cursor from a previous result")
    parser.add_argument("--json", action="store_true", help="print full JSON results")
    parser.add_argument("--info", action="store_true", help="print database identity and exit")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    sha = (args.sha256 or "").strip().lower() or _sha_from_manifest(db_path)
    if not sha:
        parser.error("--sha256 is required when no sibling manifest.json provides it")

    try:
        reader = PublicTextReader(db_path, sha)
    except (PublicTextError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        if args.info:
            info = reader.database_info()
            if args.json:
                _emit({"database": info}, True)
            else:
                for key, value in info.items():
                    print(f"{key}: {value}")
            return 0

        questions = args.question or []
        if not questions:
            if sys.stdin.isatty():
                print("输入问题，空行退出。", file=sys.stderr)
                while True:
                    try:
                        line = input("> ").strip()
                    except EOFError:
                        break
                    if not line:
                        break
                    questions.append(line)
            else:
                raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
                questions = [line.strip() for line in raw.splitlines() if line.strip()]
        if not questions:
            parser.error("provide --question or pipe questions on stdin")

        for question in questions:
            result = reader.lookup(question, limit=args.limit, cursor=args.cursor)
            _emit(result, args.json)
        return 0
    except (PublicTextError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        reader.close()


if __name__ == "__main__":
    raise SystemExit(main())
