"""Build a queryable SQLite database from the Naikuai data-summary docx.

The extractor keeps three layers:
  sections   heading paths ("第四章 副本 / 4.1秘境 / ...")
  records    one row per table entry: name + attributes
  documents  prose paragraphs that are not inside a table

Lookup is name-first: exact -> prefix -> CJK bigram overlap, so asking for one
equipment or term returns its own rows instead of a whole section.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import zipfile
from pathlib import Path

TXT = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.S)
HEADING_STYLES = {"2": 1, "3": 2, "4": 3, "5": 4}
WS = re.compile(r"\s+")
CJK = re.compile(r"[\u3400-\u9fff]")
NUM = re.compile(r"[0-9]")


def _text(fragment: str) -> str:
    return WS.sub(" ", "".join(TXT.findall(fragment))).strip()


def _style(paragraph: str) -> str:
    match = re.search(r'<w:pStyle w:val="([^"]+)"', paragraph)
    return match.group(1) if match else ""


def _cells(row_xml: str) -> list[str]:
    out = []
    for cell in re.findall(r"<w:tc>.*?</w:tc>", row_xml, re.S):
        paras = re.findall(r"<w:p[ >].*?</w:p>", cell, re.S)
        out.append(_text(" ".join(paras)))
    return out


def iter_blocks(docx: Path):
    xml = zipfile.ZipFile(docx).read("word/document.xml").decode("utf-8")
    body = re.search(r"<w:body>(.*)</w:body>", xml, re.S).group(1)
    for block in re.findall(r"<w:tbl>.*?</w:tbl>|<w:p[ >].*?</w:p>", body, re.S):
        if block.startswith("<w:tbl>"):
            rows = [_cells(r) for r in re.findall(r"<w:tr[ >].*?</w:tr>", block, re.S)]
            yield "table", [r for r in rows if any(r)]
        else:
            level = HEADING_STYLES.get(_style(block))
            text = _text(block)
            if not text:
                continue
            yield ("heading", level, text) if level else ("para", text)


def _looks_like_header(row: list[str], rows: list[list[str]]) -> bool:
    """Header rows are short labels without digits, followed by similarly shaped data."""
    filled = [c for c in row if c]
    if len(filled) < 2:
        return False
    if any(NUM.search(c) for c in filled):
        return False
    if any(len(c) > 20 for c in filled):
        return False
    if len(rows) < 2:
        return False
    return sum(1 for c in rows[1] if c) >= 2


def _is_label(text: str) -> bool:
    """A one-cell row that names a following block rather than carrying data."""
    return bool(text) and len(text) <= 20 and not NUM.search(text) and "：" not in text


def _is_value(text: str) -> bool:
    return bool(re.match(r"^[+\-±]?\d", text)) or "%" in text or bool(re.search(r"\d+[wW万]?$", text))


def _split_pairs(row: list[str]) -> list[tuple[int, str, str]] | None:
    """Split a pair-table row into (group index, name, value).

    An empty leading cell means only the right-hand group is present, so the
    group index must come from the column position, not the filtered order.
    """
    if len(row) < 2 or len(row) % 2:
        return None
    out: list[tuple[int, str, str]] = []
    for index in range(0, len(row), 2):
        name, value = row[index], row[index + 1]
        if not name:
            continue
        if len(name) > 16 or not value:
            return None
        out.append((index // 2, name, value))
    return out or None


def _column_labels(header: list[str], width: int) -> list[str]:
    """Map a pair-table header onto its columns.

    Headers like ["武器/防具", "食物汤"] label the two *left* cells of a 4-column
    layout (the right half is relabelled later by a single-cell row such as
    "戒指"). Only where the header covers a full group do we name it; the rest
    stays positional so a missing relabel shows up as "列N" instead of silently
    borrowing the wrong label.
    """
    if width == len(header):
        return list(header)
    labels = [f"列{i + 1}" for i in range(width)]
    for group in range(width // 2):
        name = header[group] if group < len(header) else ""
        if not name:
            continue
        labels[group * 2] = name
        labels[group * 2 + 1] = name
    return labels


def _clean_name(name: str) -> tuple[str, str]:
    """Split "莫德琳（四星）" into its short name and the parenthetical note."""
    match = re.match(r"^(.+?)[（(](.+?)[）)]\s*$", name)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return name.strip(), ""


def table_rows_to_records(path: str, rows: list[list[str]]) -> list[dict]:
    records = _parse_table(path, rows)
    for rec in records:
        short, note = _clean_name(rec["name"])
        if note and "备注" not in rec["attrs"]:
            rec["name"] = short
            rec["attrs"] = {**rec["attrs"], "备注": note}
    return records


def _parse_table(path: str, rows: list[list[str]]) -> list[dict]:
    """Parse one <w:tbl>, which may hold several logical tables back to back.

    A row that reads like a header starts a new block; blocks are parsed with
    their own header, so a mid-table switch (头盔/胸甲 -> 腿甲/战靴) keeps the
    right column names for every row that follows it.
    """
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        return []
    out: list[dict] = []
    index = 0
    while index < len(rows):
        header = rows[index]
        if _looks_like_header(header, rows[index:]):
            index += 1
            block: list[list[str]] = []
            while index < len(rows) and not _looks_like_header(rows[index], rows[index:]):
                block.append(rows[index])
                index += 1
        else:
            block = [header]
            index += 1
            header = []
        out.extend(_parse_block(path, header, block))
    return out


def _parse_block(path: str, header: list[str], rows: list[list[str]]) -> list[dict]:
    width = max((len(r) for r in rows), default=0)
    if width < 2:
        return _notes(path, rows)

    # Group layout: header repeats as [name, field, field..., name, field, ...],
    # so each group is name + k attributes and a row carries several groups.
    group_width = _group_width(header, width)
    if group_width:
        return _group_records(path, header, rows, group_width)

    # A header of the same width as the rows always names every column, so use
    # it directly before guessing at name/value pairs.
    if header and len(header) == width and sum(1 for c in header if c) >= 2:
        return _flat_records(path, header, rows)

    # Two labels over a wider table describe side-by-side pair groups.
    if header and width == 2 * len(header) and _pairs_are_only_values(rows):
        return _pair_records(path, header, rows, width)
    if not header and _pairs_are_only_values(rows):
        return _pair_records(path, [], rows, width)
    return _flat_records(path, header, rows)


def _group_width(header: list[str], width: int) -> int:
    """Width of one repeating group.

    A layout like [头盔, 抗性, 护甲, 生命, 胸甲, 抗性, 护甲, 生命] repeats the
    *field names* after a varying group name, so compare the tail fields rather
    than the first cell.
    """
    if not header or len(header) != width or width < 4:
        return 0
    for step in (4, 3, 5, 2):
        if width % step:
            continue
        groups = [header[i:i + step] for i in range(0, width, step)]
        if len(groups) < 2:
            continue
        tail = groups[0][1:]
        if tail and all(group[1:] == tail for group in groups[1:]):
            return step
    return 0


def _group_records(path: str, header: list[str], rows: list[list[str]], step: int) -> list[dict]:
    fields = header[1:step]
    out: list[dict] = []
    for row in rows:
        if len(row) < step:
            continue
        for start in range(0, len(row) - step + 1, step):
            group = row[start:start + step]
            name = group[0].strip()
            if not name or len(name) > 24:
                continue
            values = [v.strip() for v in group[1:]]
            attrs = {f: v for f, v in zip(fields, values) if v}
            if attrs:
                out.append({"name": name, "attrs": attrs, "text": "", "kind": "row", "path": path})
    return out


def _pairs_are_only_values(rows: list[list[str]], threshold: float = 0.6) -> bool:
    """True when most rows alternate name/value (2 or 4 columns).

    Not all rows qualify: a table may end with a group label ("戒指") or a note,
    so require a majority instead of every row.
    """
    if not rows:
        return False
    good = 0
    for row in rows:
        filled = [c for c in row if c]
        if len(filled) < 2 or len(filled) % 2:
            continue
        ok = True
        for i in range(0, len(filled), 2):
            name, value = filled[i], filled[i + 1]
            if not name or len(name) > 16 or not value:
                ok = False
                break
            if not _is_value(value) and len(value) > 12:
                ok = False
                break
        if ok:
            good += 1
    return good / len(rows) >= threshold


def _pair_records(path: str, header: list[str], rows: list[list[str]], width: int) -> list[dict]:
    labels = [f"列{i + 1}" for i in range(max(1, width // 2))]
    for i, name in enumerate(header[:len(labels)]):
        if name:
            labels[i] = name
    out: list[dict] = []
    pending = ""
    for row in rows:
        filled = [c for c in row if c]
        if len(filled) == 1:
            value = filled[0]
            column = next((i for i, c in enumerate(row) if c), 0)
            if _is_label(value):
                if column >= 2 and len(labels) > 1:
                    labels[-1] = value
                else:
                    pending = value
            else:
                out.append({"name": pending or value, "attrs": {}, "text": value,
                            "kind": "note", "path": path})
                pending = ""
            continue
        kept = [(i // 2, row[i], row[i + 1]) for i in range(0, len(row) - 1, 2) if row[i]]
        if not kept:
            continue
        for group, name, value in kept:
            label = labels[group] if group < len(labels) else f"列{group + 1}"
            out.append({"name": (pending + " · " + name) if pending else name,
                        "attrs": {label: value}, "text": "", "kind": "row", "path": path})
        pending = ""
    return out


def _flat_records(path: str, header: list[str], rows: list[list[str]]) -> list[dict]:
    out: list[dict] = []
    pending = ""
    for row in rows:
        filled = [c for c in row if c]
        if not filled:
            continue
        if len(filled) == 1:
            value = filled[0]
            if _is_label(value):
                pending = value
            else:
                out.append({"name": pending or value, "attrs": {}, "text": value,
                            "kind": "note", "path": path})
                pending = ""
            continue
        if header and len(filled) == len(header) and filled[0] not in header:
            name, attrs = filled[0], {h: v for h, v in zip(header[1:], filled[1:]) if v}
        elif len(filled) == 2:
            name, attrs = filled[0], {"值": filled[1]}
        else:
            name, attrs = filled[0], {f"字段{i + 1}": v for i, v in enumerate(filled[1:]) if v}
        out.append({"name": (pending + " · " + name) if pending else name, "attrs": attrs,
                    "text": "", "kind": "row", "path": path})
        pending = ""
    return out


def _notes(path: str, rows: list[list[str]]) -> list[dict]:
    return [{"name": " ".join(row)[:60] or "note", "attrs": {}, "text": " ".join(row),
             "kind": "note", "path": path} for row in rows if any(row)]


_ROMAN = {"Ⅰ": "1", "Ⅱ": "2", "Ⅲ": "3", "Ⅳ": "4", "Ⅴ": "5", "Ⅵ": "6",
          "Ⅶ": "7", "Ⅷ": "8", "Ⅸ": "9", "Ⅹ": "10", "Ⅺ": "11", "Ⅻ": "12"}
_CN_NUM = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6",
           "七": "7", "八": "8", "九": "9", "十": "10"}
_ARABIC_TO_ROMAN = {v: k for k, v in _ROMAN.items()}


def normalize_numerals(text: str) -> str:
    """Fold Ⅰ/Ⅱ/2/二 into one form so "多重2" finds "多重Ⅱ".

    The source document mixes Roman, Arabic and Chinese numerals for the same
    tiers, and people type whichever they like.
    """
    out = []
    for char in text:
        if char in _ROMAN:
            out.append(_ROMAN[char])
        elif char in _CN_NUM:
            out.append(_CN_NUM[char])
        else:
            out.append(char)
    return "".join(out)


def numeral_variants(name: str) -> list[str]:
    """Every spelling of a name that a user might type."""
    base = normalize_numerals(name)
    variants = {name, base}
    # Rebuild the Roman-suffixed forms ("多重2" -> "多重Ⅱ").
    for arabic, roman in _ARABIC_TO_ROMAN.items():
        if base.endswith(arabic):
            variants.add(base[:-len(arabic)] + roman)
        if name.endswith(roman):
            variants.add(base)
    for arabic, chinese in _CN_NUM.items():
        if base.endswith(arabic):
            variants.add(base[:-len(arabic)] + chinese)
    return [v for v in variants if v]


def cjk_bigrams(text: str) -> set[str]:
    out: set[str] = set()
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        if len(run) == 1:
            out.add(run)
        out.update(run[i:i + 2] for i in range(len(run) - 1))
    out.update(w.lower() for w in re.findall(r"[A-Za-z0-9_]{2,}", text))
    return out


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS records(
  id INTEGER PRIMARY KEY, section TEXT, title TEXT, name TEXT, name_norm TEXT,
  attrs TEXT, text TEXT, kind TEXT);
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY, section TEXT, title TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS terms(
  term TEXT NOT NULL, kind TEXT NOT NULL, ref INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_records_norm ON records(name_norm);
CREATE INDEX IF NOT EXISTS idx_terms ON terms(term, kind);
"""


def build(docx: Path, db_path: Path) -> dict:
    if db_path.exists():
        db_path.unlink()
    for suffix in ("-wal", "-shm"):
        extra = Path(str(db_path) + suffix)
        if extra.exists():
            extra.unlink()

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    path: list[str] = []
    record_count = doc_count = table_count = 0
    seen: set[tuple] = set()

    cur = conn.cursor()
    for block in iter_blocks(docx):
        if block[0] == "heading":
            _, level, text = block
            path = path[:level - 1] + [text]
            pending_heading_level = level
            continue
        if block[0] == "para":
            text = block[1]
            if path:
                cur.execute("INSERT INTO documents(section,title,text) VALUES(?,?,?)",
                            (" / ".join(path[:-1]), path[-1], text))
                doc_id = cur.lastrowid
                doc_count += 1
                for term in cjk_bigrams(text):
                    cur.execute("INSERT INTO terms(term,kind,ref) VALUES(?,?,?)", (term, "doc", doc_id))
            continue

        rows = block[1]
        table_count += 1
        section = " / ".join(path[:-1]) if len(path) > 1 else ""
        title = path[-1] if path else ""
        for rec in table_rows_to_records(" / ".join(path), rows):
            attrs_text = json.dumps(rec["attrs"], ensure_ascii=False, sort_keys=True) if rec["attrs"] else ""
            fingerprint = (rec["name"].casefold(), attrs_text, rec["text"], " / ".join(path))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            cur.execute(
                "INSERT INTO records(section,title,name,name_norm,attrs,text,kind) VALUES(?,?,?,?,?,?,?)",
                (section, title, rec["name"], normalize_numerals(rec["name"]).casefold(), attrs_text,
                 rec["text"], rec["kind"]))
            rec_id = cur.lastrowid
            record_count += 1
            blob = " ".join([rec["name"], " ".join(rec["attrs"].keys()), " ".join(rec["attrs"].values()),
                             rec["text"], title, section])
            for term in cjk_bigrams(blob):
                cur.execute("INSERT INTO terms(term,kind,ref) VALUES(?,?,?)", (term, "rec", rec_id))
    conn.commit()
    conn.close()
    return {"tables": table_count, "records": record_count, "documents": doc_count,
            "dbMiB": round(db_path.stat().st_size / 1048576, 1)}


QUESTION_NOISE = (
    "请问", "帮我查一下", "帮我查", "查一下", "查询", "一下", "我想知道", "想知道",
    "的数值是多少", "数值是多少", "数据是多少", "属性是多少", "是多少", "多少",
    "的数值", "数值", "数据", "属性", "面板", "怎么样", "是什么", "是啥", "什么",
    "怎么样啊", "呢", "吗", "啊", "呀", "吧", "?", "？", "。", "，", ",",
)


def candidate_names(query: str) -> list[str]:
    """Turn a spoken question into the names that might be in the database.

    The caller asks "毁灭胸甲的数值是多少"; the record is stored as "毁灭胸甲".
    We strip question words from both ends and also try every 2-12 char window
    so a name embedded mid-sentence still resolves.
    """
    text = query.strip()
    for noise in QUESTION_NOISE:
        text = text.replace(noise, " ")
    text = re.sub(r"\s+", " ", text).strip()
    out = [text] if text else []
    for chunk in text.split(" "):
        chunk = chunk.strip()
        if len(chunk) >= 2:
            out.append(chunk)
    for token in re.findall(r"[\u3400-\u9fffA-Za-z0-9Ⅰ-Ⅻ·]+", query):
        for size in range(min(12, len(token)), 1, -1):
            for start in range(0, len(token) - size + 1):
                out.append(token[start:start + size])
    seen = set()
    ordered = []
    for name in sorted(out, key=len, reverse=True):
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered[:40]


def lookup(db_path: Path, query: str, limit: int = 5) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    seen: dict[int, dict] = {}

    def add(rows, how):
        for row in rows:
            if row["id"] in seen:
                continue
            seen[row["id"]] = {"how": how, "name": row["name"], "attrs": json.loads(row["attrs"] or "{}"),
                               "text": row["text"], "section": row["section"], "title": row["title"]}

    # Longest plausible name first: a spoken sentence should resolve to the
    # equipment it mentions, not to a section that happens to share a bigram.
    for name in candidate_names(query):
        for variant in numeral_variants(name):
            norm = normalize_numerals(variant).casefold()
            add(conn.execute("SELECT * FROM records WHERE name_norm=? LIMIT ?", (norm, limit)).fetchall(), "exact")
        if len(seen) >= limit:
            break
    if len(seen) < limit:
        for name in candidate_names(query)[:6]:
            for variant in numeral_variants(name):
                add(conn.execute("SELECT * FROM records WHERE name_norm LIKE ? LIMIT ?",
                                 (normalize_numerals(variant).casefold() + "%", limit)).fetchall(), "prefix")
            if len(seen) >= limit:
                break
    if len(seen) < limit:
        scores: dict[int, int] = {}
        for term in cjk_bigrams(query):
            for row in conn.execute("SELECT ref FROM terms WHERE term=? AND kind='rec'", (term,)):
                scores[row["ref"]] = scores.get(row["ref"], 0) + 1
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:limit * 3]
        if ranked:
            ids = [rid for rid, _ in ranked]
            placeholders = ",".join("?" * len(ids))
            rows = conn.execute(f"SELECT * FROM records WHERE id IN ({placeholders})", ids).fetchall()
            rows.sort(key=lambda r: -scores[r["id"]])
            add(rows, "bigram")
    conn.close()
    return list(seen.values())[:limit]


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    build_cmd = sub.add_parser("build")
    build_cmd.add_argument("docx", type=Path)
    build_cmd.add_argument("db", type=Path)
    query_cmd = sub.add_parser("query")
    query_cmd.add_argument("db", type=Path)
    query_cmd.add_argument("text")
    query_cmd.add_argument("--limit", type=int, default=5)
    args = parser.parse_args(argv)

    if args.cmd == "build":
        print(json.dumps(build(args.docx, args.db), ensure_ascii=False))
        return 0
    print(json.dumps(lookup(args.db, args.text, args.limit), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
