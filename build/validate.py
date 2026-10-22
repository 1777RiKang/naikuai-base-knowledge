#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate every term file: JSON Schema first, project rules second."""
import json
import re
import sys
from collections import defaultdict

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None

from common import SCHEMA_PATH, load_terms, norm
from privacy import term_privacy_errors

QQ_RE = re.compile(r"(?<!\d)\d{6,}(?!\d)")
REQUIRED_FIELDS = ("id", "term", "canonical", "kind", "senses", "provenance",
                   "contributors", "review", "version", "updated_at")


def collect_errors(docs):
    errors = []
    if jsonschema is None:
        return ["缺少依赖 jsonschema，请先 pip install -r requirements.txt"]
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    ids = {}
    terms = defaultdict(list)
    aliases = defaultdict(list)
    by_id = {}

    for path, doc in docs:
        rel = path.relative_to(path.parents[2]).as_posix()
        private = term_privacy_errors(doc, rel)
        if private:
            errors.extend(private)
            continue
        if not isinstance(doc, dict):
            errors.append(f"{rel}: YAML 顶层必须是对象")
            continue
        missing = [k for k in REQUIRED_FIELDS if k not in doc]
        if missing:
            errors.append(f"{rel}: 缺少字段 {', '.join(missing)}")
            continue
        if path.read_bytes().startswith(b"\xef\xbb\xbf"):
            errors.append(f"{rel}: 文件带 UTF-8 BOM，请存为无 BOM")
        try:
            jsonschema.validate(doc, schema, format_checker=jsonschema.FormatChecker())
        except jsonschema.ValidationError as exc:
            loc = "/".join(str(x) for x in exc.absolute_path) or "(root)"
            errors.append(f"{rel}: schema 校验失败 {loc}: {exc.message}")
            continue

        entry_id = doc["id"]
        if entry_id in ids:
            errors.append(f"{rel}: id 与 {ids[entry_id]} 重复")
        ids[entry_id] = rel
        by_id[entry_id] = doc

        if path.stem != entry_id.split(".", 1)[1]:
            errors.append(f"{rel}: 文件名必须等于 id 的后半段（{entry_id.split('.', 1)[1]}.yaml）")
        if path.parent.name != doc["kind"]:
            errors.append(f"{rel}: 目录名必须与 kind 一致（{doc['kind']}）")
        if entry_id.split('.', 1)[0] != doc['kind']:
            errors.append(f"{rel}: id 前缀必须与 kind 一致")
        if not norm(doc['term']) or not norm(doc['canonical']):
            errors.append(f"{rel}: term、canonical 不能只含空白")

        terms[norm(doc["term"])].append(rel)
        for alias in doc.get("aliases", []):
            if not norm(alias):
                errors.append(f"{rel}: 别名不能只含空白")
            if norm(alias) == norm(doc["term"]):
                errors.append(f"{rel}: 别名不能与term相同：{alias}")
            aliases[norm(alias)].append(rel)

        review = doc["review"]
        if review["status"] == "confirmed" and not (review.get("reviewer") and review.get("reviewed_at")):
            errors.append(f"{rel}: confirmed 状态必须有 reviewer 和 reviewed_at")
        if review["status"] == "disputed" and not review.get("reviewer"):
            errors.append(f"{rel}: disputed 状态必须记录提出异议的 reviewer")

        prov = doc["provenance"]
        if prov["source"] == "community":
            if not prov.get("evidence"):
                errors.append(f"{rel}: community 来源必须附至少一条证据")
        for i, ev in enumerate(prov.get("evidence", [])):
            if not ev.get("redacted"):
                errors.append(f"{rel}: 证据 #{i + 1} 未标记 redacted")
        if prov["source"] == "nkwiki" and not prov.get("url"):
            errors.append(f"{rel}: nkwiki 来源必须提供 url")

        rel_block = doc.get("relations", {})
        if doc["kind"] == "composite":
            composes = rel_block.get("composes", [])
            if len(composes) < 2:
                errors.append(f"{rel}: composite 词条必须拆分出至少 2 个组成成分")
        elif rel_block.get("composes"):
            errors.append(f"{rel}: 只有 composite 才能使用 composes")

    for term, places in terms.items():
        if len(places) > 1:
            errors.append(f"term 重复：{term} -> {', '.join(places)}")
    for alias, places in aliases.items():
        if len(places) > 1:
            errors.append(f"别名冲突：{alias} -> {', '.join(places)}")
        if alias in terms:
            errors.append(f"别名与已有词条重名：{alias} -> {', '.join(places)}")

    for entry_id, doc in by_id.items():
        rel_block = doc.get("relations", {})
        for key in ("variant_of", "composes", "related"):
            for target in rel_block.get(key, []):
                if target not in by_id:
                    errors.append(f"{entry_id}: relations.{key} 指向不存在的 id：{target}")
        if doc["kind"] == "composite":
            for target in rel_block.get("composes", []):
                if target not in by_id:
                    errors.append(f"{entry_id}: 组合成分不存在：{target}")

    return errors


def main():
    docs = load_terms()
    errors = collect_errors(docs)
    print(f"扫描 {len(docs)} 个词条文件")
    if errors:
        for err in errors:
            print(f"  ✗ {err}")
        print(f"校验失败：{len(errors)} 个问题")
        return 1
    print("校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
