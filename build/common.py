# -*- coding: utf-8 -*-
"""Shared paths and helpers for the glossary build."""
import re
import unicodedata
from pathlib import Path

import yaml


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate keys instead of silently overwriting submitted data."""


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise yaml.constructor.ConstructorError(
                "mapping", node.start_mark, "YAML 存在重复字段", key_node.start_mark)
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)

ROOT = Path(__file__).resolve().parents[1]
TERMS_DIR = ROOT / "terms"
SCHEMA_PATH = ROOT / "schema" / "term.schema.json"
VERSION_PATH = ROOT / "VERSION"


def norm(text: str) -> str:
    """Normalize a term or alias for conflict detection and lookup."""
    text = unicodedata.normalize("NFKC", text).strip().lower()
    return re.sub(r"\s+", "", text)


def load_terms():
    """Return [(path, document), ...] sorted by path."""
    docs = []
    for path in sorted(TERMS_DIR.rglob("*.yaml")):
        try:
            with path.open(encoding="utf-8") as f:
                docs.append((path, yaml.load(f, Loader=UniqueKeyLoader)))
        except (yaml.YAMLError, UnicodeError, TypeError):
            raise ValueError(f"{path.relative_to(ROOT)}: YAML 解析失败，请检查编码、重复字段及格式（不回显内容）") from None
    return docs


def read_version() -> str:
    return VERSION_PATH.read_text(encoding="utf-8").strip()


def tokenize(text: str):
    """CJK unigrams for single chars, bigrams for runs, latin/digit tokens as-is."""
    text = unicodedata.normalize("NFKC", text)
    tokens = set()
    for chunk in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+", text):
        if re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
            if len(chunk) == 1:
                tokens.add(chunk)
            for i in range(len(chunk) - 1):
                tokens.add(chunk[i:i + 2])
            tokens.add(chunk)
        else:
            tokens.add(chunk.lower())
    return {t for t in tokens if t}
