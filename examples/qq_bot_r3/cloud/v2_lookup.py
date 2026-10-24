#!/usr/bin/env python3
"""Read-only lookup layer for game-kb-v2.sqlite (P0 shadow)."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_DEFAULT = Path(__file__).with_name("game-kb-v2.sqlite")
SNAPSHOT = "8.6.26.09"
KIND_ORDER = {"boss": 0, "item": 1, "npc": 2, "skill": 3, "gem": 4, "gem_reference": 5}
KIND_LABEL = {
    "boss": "副本/BOSS",
    "item": "物品",
    "npc": "NPC",
    "skill": "技能",
    "gem": "宝石",
    "gem_reference": "宝石参考",
}


def norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", "", text.casefold())


def _json(value: Any, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def open_db(path: Path = DB_DEFAULT) -> sqlite3.Connection:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def _base(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "kind": row["kind"] if "kind" in row.keys() else None,
        "tier": row["tier"],
        "source": row["source"],
    }


def _item(row: sqlite3.Row) -> dict[str, Any]:
    status = row["zh_status"] if "zh_status" in row.keys() else "ok"
    notes = []
    if status == "en_placeholder":
        notes.append("中文名缺失，当前为英文占位；可按 ID 或内部名查询。")
    elif status == "internal":
        notes.append("测试/GM 物品，非玩家正式内容。")
    result = {
        "kind": "item",
        "id": row["id"],
        "name": row["zh"] or row["internal_name"],
        "context": row["internal_name"],
        "tier": row["tier"],
        "source": row["source"],
        "fields": {
            "id": row["id"],
            "namespace": row["namespace"],
            "internal_name": row["internal_name"],
            "zh": row["zh"],
            "zh_status": status,
        },
        "notes": notes,
    }
    return result


def _npc(row: sqlite3.Row) -> dict[str, Any]:
    placeholder = bool(row["hp_is_placeholder"])
    notes = []
    if placeholder:
        notes.append("血量 99999 是源表占位/无敌标记，不能当真实血量。")
    if row["hp_note"]:
        notes.append(str(row["hp_note"]))
    return {
        "kind": "npc",
        "id": row["id"],
        "name": row["name"],
        "context": f"type={row['type']}",
        "tier": row["tier"],
        "source": row["source"],
        "fields": {
            "id": row["id"],
            "hp": row["hp_effective"],
            "hp_raw": row["hp"],
            "hp_is_placeholder": placeholder,
            "type": row["type"],
            "melee_atk": row["melee_atk"],
            "ranged_atk": row["ranged_atk"],
            "melee_mag": row["melee_mag"],
            "ranged_mag": row["ranged_mag"],
            "defense": row["defense"],
            "mag_def": row["mag_def"],
            "ai": row["ai"],
            "speed": row["speed"],
            "element": row["element"],
        },
        "notes": notes,
    }


def _skill(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "kind": "skill",
        "id": row["id"],
        "name": row["name"],
        "context": f"type={row['type']}",
        "tier": row["tier"],
        "source": row["source"],
        "fields": {
            "id": row["id"],
            "type": row["type"],
            "cooldown": row["cooldown"],
            "param": _json(row["param_json"]),
            "range": _json(row["range_json"]),
            "singtime": _json(row["singtime_json"]),
            "buff": _json(row["buff_json"]),
            "child": _json(row["child_json"]),
            "hit": row["hit"],
            "damage": row["damage"],
            "element": row["element"],
        },
        "notes": [],
    }


def _boss(row: sqlite3.Row) -> dict[str, Any]:
    notes = []
    if row["time_limit"] in (None, ""):
        notes.append("源表未记录限时。")
    if row["note"]:
        notes.append(str(row["note"]))
    return {
        "kind": "boss",
        "id": row["id"],
        "name": row["dungeon"] or row["boss"],
        "context": f"{row['boss']} @ {row['difficulty'] or '未知难度'}",
        "tier": row["tier"],
        "source": row["source"],
        "fields": {
            "id": row["id"],
            "category": row["category"],
            "world": row["world"],
            "dungeon": row["dungeon"],
            "difficulty": row["difficulty"],
            "boss": row["boss"],
            "party": row["party"],
            "gear": row["gear"],
            "time_limit": row["time_limit"],
            "note": row["note"],
            "arena_id": row["arena_id"] if "arena_id" in row.keys() else None,
            "version": row["version"],
        },
        "notes": notes,
    }


def _gem(row: sqlite3.Row) -> dict[str, Any]:
    enabled = bool(row["enabled"])
    notes = []
    if not enabled:
        notes.append("源表注释/未启用，不能当正式可用宝石。")
    return {
        "kind": "gem",
        "id": row["id"],
        "name": row["name"],
        "context": row["status"] or ("enabled" if enabled else "disabled"),
        "tier": row["tier"],
        "source": row["source"],
        "fields": {
            "id": row["id"],
            "raw_id": row["raw_id"],
            "enabled": enabled,
            "status": row["status"],
            "description": row["description"],
            "type": row["type"],
            "params": _json(row["params_json"]),
            "limit_level": row["limit_level"],
            "buff_ids": _json(row["buff_ids_json"]),
            "snapshot": row["snapshot"],
        },
        "notes": notes,
    }


def _gem_reference(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "kind": "gem_reference",
        "id": row["gem_id"],
        "name": row["name"],
        "context": row["kind"],
        "tier": row["tier"],
        "source": row["source"],
        "fields": {
            "gem_id": row["gem_id"],
            "kind": row["kind"],
            "values": _json(row["values_json"]),
            "description": row["description"],
            "verified": bool(row["verified_for_current_version"]),
            "upstream": row["upstream"],
            "version": row["version"],
        },
        "notes": ["L4 社区参考值，未与当前版本逐项核对。"],
    }


def load_fact(con: sqlite3.Connection, kind: str, ref_id: Any) -> dict[str, Any] | None:
    if kind == "item":
        row = con.execute("SELECT * FROM items WHERE id=?", (ref_id,)).fetchone()
        return _item(row) if row else None
    if kind == "npc":
        row = con.execute("SELECT * FROM npcs WHERE id=?", (ref_id,)).fetchone()
        return _npc(row) if row else None
    if kind == "skill":
        row = con.execute("SELECT * FROM skills WHERE id=?", (ref_id,)).fetchone()
        return _skill(row) if row else None
    if kind == "boss":
        row = con.execute("SELECT * FROM bosses WHERE id=?", (ref_id,)).fetchone()
        return _boss(row) if row else None
    if kind == "gem":
        row = con.execute("SELECT * FROM gems WHERE id=?", (ref_id,)).fetchone()
        return _gem(row) if row else None
    if kind == "gem_reference":
        row = con.execute("SELECT * FROM gem_reference WHERE gem_id=?", (ref_id,)).fetchone()
        return _gem_reference(row) if row else None
    return None


def _row_to_fact(con: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any] | None:
    return load_fact(con, row["kind"], row["ref_id"])


def _alias_sql(kind: str | None, exact: bool, query: str) -> tuple[str, list[Any]]:
    sql = (
        "SELECT a.alias_norm, a.alias, e.entity_id, e.kind, e.name, e.context, "
        "e.tier, e.source, e.ref_id "
        "FROM aliases a JOIN entities e ON e.entity_id = a.entity_id "
    )
    params: list[Any] = [query]
    if exact:
        sql += "WHERE a.alias_norm = ?"
    else:
        sql += "WHERE length(a.alias_norm) >= 2 AND instr(?, a.alias_norm) > 0"
    if kind:
        sql += " AND e.kind = ?"
        params.append(kind)
    if not exact:
        sql += " ORDER BY length(a.alias_norm) DESC, a.alias_norm LIMIT 300"
    return sql, params


def _find_rows(
    con: sqlite3.Connection, query: str, kind: str | None
) -> tuple[list[sqlite3.Row], str]:
    sql, params = _alias_sql(kind, True, query)
    rows = con.execute(sql, params).fetchall()
    if rows:
        return rows, "exact"
    sql, params = _alias_sql(kind, False, query)
    rows = con.execute(sql, params).fetchall()
    if not rows:
        return [], "none"
    max_len = max(len(r["alias_norm"]) for r in rows)
    return [r for r in rows if len(r["alias_norm"]) == max_len], "substring"


def _load_hints(con: sqlite3.Connection, facts: list[dict[str, Any]]) -> None:
    if not facts:
        return
    ids = [f"{f['kind']}:{f['id']}" for f in facts]
    placeholders = ",".join("?" * len(ids))
    try:
        rows = con.execute(
            f"SELECT entity_id, hint, group_key FROM entity_hints WHERE entity_id IN ({placeholders})",
            ids,
        ).fetchall()
    except sqlite3.OperationalError:
        return
    hints = {row["entity_id"]: row for row in rows}
    for fact in facts:
        row = hints.get(f"{fact['kind']}:{fact['id']}")
        if row:
            fact["hint"] = row["hint"]
            fact["group_key"] = row["group_key"]


def _dedupe(con: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    seen = set()
    facts = []
    for row in rows:
        if row["entity_id"] in seen:
            continue
        seen.add(row["entity_id"])
        fact = _row_to_fact(con, row)
        if fact:
            fact["alias"] = row["alias"]
            facts.append(fact)
    facts.sort(key=lambda f: (KIND_ORDER.get(f["kind"], 99), str(f["name"]), f["id"]))
    _load_hints(con, facts)
    return facts


def _short(fact: dict[str, Any]) -> str:
    label = KIND_LABEL.get(fact["kind"], fact["kind"])
    hint = fact.get("hint") or fact.get("context")
    suffix = f"，{hint}" if hint else ""
    name = fact["name"]
    if fact["kind"] == "item":
        status = fact.get("fields", {}).get("zh_status")
        if status == "en_placeholder":
            name = f"[未汉化] {name}"
        elif status == "internal":
            name = f"[测试/GM] {name}"
    return f"{name}（{label}#{fact['id']}{suffix}）"


def _answer(fact: dict[str, Any]) -> str:
    kind = fact["kind"]
    fields = fact["fields"]
    name = fact["name"]
    if kind == "item":
        if fields.get("zh_status") == "en_placeholder":
            return f"{name}（未汉化）：ID {fields['id']}，内部名 {fields['internal_name']}；中文名暂缺。"
        if fields.get("zh_status") == "internal":
            return f"{name}（测试/GM）：ID {fields['id']}，内部名 {fields['internal_name']}。"
        return f"{name}：ID {fields['id']}，内部名 {fields['internal_name']}。"
    if kind == "npc":
        if fields["hp_is_placeholder"]:
            return f"{name}：源表血量 99999 是占位/无敌标记，不能当真实血量（类型 {fields['type']}）。"
        return f"{name}：血量 {fields['hp']}，类型 {fields['type']}。"
    if kind == "skill":
        return f"{name}：冷却 {fields['cooldown']}（源表单位），类型 {fields['type']}。"
    if kind == "boss":
        party = fields["party"] or "未知"
        gear = fields["gear"] or "未知"
        limit = fields["time_limit"] or "资料未记录"
        return (
            f"{fields['dungeon'] or name}：BOSS {fields['boss']}，"
            f"{party}，装等 {gear}，限时 {limit}。"
        )
    if kind == "gem":
        status = "启用" if fields["enabled"] else "未启用"
        desc = fields["description"] or "无描述"
        return f"{name}（{status}）：{desc}"
    if kind == "gem_reference":
        values = json.dumps(fields["values"], ensure_ascii=False)
        return f"{name}：{values}（L4 社区参考，未逐项核对当前版本）。"
    return f"{name}：{json.dumps(fields, ensure_ascii=False)}"


def _candidate_payload(facts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates = []
    groups: dict[str, dict[str, Any]] = {}
    for fact in facts:
        hint = fact.get("hint") or fact.get("context") or ""
        key = fact.get("group_key") or hint
        candidate = {
            "entity_id": f"{fact['kind']}:{fact['id']}",
            "kind": fact["kind"],
            "id": fact["id"],
            "name": fact["name"],
            "hint": hint,
            "label": _short(fact),
        }
        candidates.append(candidate)
        group = groups.setdefault(
            key, {"hint": hint or "无可用区分字段", "ids": [], "count": 0, "kinds": []}
        )
        group["ids"].append(f"{fact['kind']}:{fact['id']}")
        group["count"] += 1
        if fact["kind"] not in group["kinds"]:
            group["kinds"].append(fact["kind"])
    return candidates, list(groups.values())


def _discriminator_label(kinds: list[str]) -> str:
    if len(kinds) != 1:
        return "类型/上下文"
    return {
        "item": "内部名",
        "npc": "类型/血量/速度",
        "skill": "类型/冷却/范围",
        "boss": "副本/难度",
        "gem": "raw_id",
    }.get(kinds[0], "上下文")


def _followup(query: str, facts: list[dict[str, Any]], groups: list[dict[str, Any]]) -> str:
    if len(groups) == 1:
        ids = "、".join(groups[0]["ids"][:8])
        more = "…" if len(groups[0]["ids"]) > 8 else ""
        return f"“{query}”的 {len(facts)} 个候选现有字段完全相同，请直接给 ID：{ids}{more}。"
    label = _discriminator_label(sorted({f["kind"] for f in facts}, key=lambda k: KIND_ORDER.get(k, 99)))
    lines = [f"“{query}”有 {len(facts)} 个候选，可按{label}或 ID 指定："]
    for group in groups[:6]:
        ids = "、".join(group["ids"][:3])
        more = "…" if group["count"] > 3 else ""
        lines.append(f"- {group['hint']}（{group['count']} 个；ID {ids}{more}）")
    if len(groups) > 6:
        lines.append(f"- 另有 {len(groups) - 6} 组")
    if any(group["count"] > 1 for group in groups):
        lines.append("同一行内字段相同的候选只能按 ID 指定。")
    return "\n".join(lines)


ID_RE = re.compile(
    r"^(?:(物品|npc|技能|副本|boss|宝石|item|skill|gem)\s*[:：#-]?\s*)?(\d+)$",
    re.IGNORECASE,
)
KIND_MAP = {
    "物品": "item",
    "npc": "npc",
    "技能": "skill",
    "副本": "boss",
    "boss": "boss",
    "宝石": "gem",
    "item": "item",
    "skill": "skill",
    "gem": "gem",
}


def _id_lookup(
    con: sqlite3.Connection, query: str, kind: str | None
) -> dict[str, Any] | None:
    match = ID_RE.match(query.strip())
    if not match:
        return None
    query_kind = KIND_MAP.get((match.group(1) or "").casefold())
    if query_kind and kind and query_kind != kind:
        return None
    ref_id = int(match.group(2))
    kinds = [query_kind] if query_kind else ([kind] if kind else ["item", "npc", "skill", "boss", "gem"])
    facts = []
    for candidate_kind in kinds:
        fact = load_fact(con, candidate_kind, ref_id)
        if fact:
            fact["alias"] = f"{candidate_kind}:{ref_id}"
            facts.append(fact)
    if not facts:
        return None
    facts.sort(key=lambda f: KIND_ORDER.get(f["kind"], 99))
    _load_hints(con, facts)
    return _result(query, norm(query), [], facts, "id")


def _result(
    query: str,
    normalized: str,
    rows: list[sqlite3.Row],
    facts: list[dict[str, Any]],
    match_type: str,
) -> dict[str, Any]:
    if not facts:
        return {
            "query": query,
            "normalized": normalized,
            "matched": False,
            "match_type": match_type,
            "matched_alias": None,
            "ambiguous": False,
            "kind": None,
            "kinds": [],
            "answer": f"没有找到“{query}”对应的可回答实体。",
            "matches": [],
            "candidates": [],
            "candidate_groups": [],
            "followup": None,
            "source": None,
            "tier": None,
            "version": SNAPSHOT,
            "notes": [],
        }
    aliases = sorted({f.get("alias", "") for f in facts if f.get("alias")})
    if len(facts) == 1:
        fact = facts[0]
        return {
            "query": query,
            "normalized": normalized,
            "matched": True,
            "match_type": match_type,
            "matched_alias": fact.get("alias"),
            "ambiguous": False,
            "kind": fact["kind"],
            "kinds": [fact["kind"]],
            "answer": _answer(fact),
            "matches": facts,
            "candidates": [],
            "candidate_groups": [],
            "followup": None,
            "source": fact["source"],
            "tier": fact["tier"],
            "version": SNAPSHOT,
            "notes": list(fact.get("notes", [])),
        }
    kinds = sorted({f["kind"] for f in facts}, key=lambda k: KIND_ORDER.get(k, 99))
    label = "/".join(KIND_LABEL.get(k, k) for k in kinds)
    candidates, groups = _candidate_payload(facts)
    listing = "；".join(_short(f) for f in facts[:4])
    more = f"；另有 {len(facts) - 4} 个" if len(facts) > 4 else ""
    notes = ["P1 已生成候选提示，但仍不自动猜测；可按提示或 ID 指定。"]
    if all(f["kind"] == "npc" and f["fields"]["hp_is_placeholder"] for f in facts):
        answer = (
            f"{facts[0]['name']}：命中 {len(facts)} 个同名 NPC，"
            "血量全部是 99999 占位/无敌标记，没有可回答的真实血量；"
            f"候选按可见字段分为 {len(groups)} 组。"
        )
    else:
        answer = f"“{query}”命中 {len(facts)} 个{label}：{listing}{more}。"
    tiers = {f["tier"] for f in facts}
    sources = {f["source"] for f in facts}
    return {
        "query": query,
        "normalized": normalized,
        "matched": True,
        "match_type": match_type,
        "matched_alias": "/".join(aliases),
        "ambiguous": True,
        "kind": kinds[0] if len(kinds) == 1 else "mixed",
        "kinds": kinds,
        "answer": answer,
        "matches": facts,
        "candidates": candidates,
        "candidate_groups": groups,
        "followup": _followup(query, facts, groups),
        "source": sources.pop() if len(sources) == 1 else "multiple",
        "tier": tiers.pop() if len(tiers) == 1 else "mixed",
        "version": SNAPSHOT,
        "notes": notes,
    }


def _reference_lookup(con: sqlite3.Connection, query: str, kind: str | None) -> dict[str, Any] | None:
    if kind not in (None, "gem_reference"):
        return None
    row = con.execute("SELECT * FROM gem_reference WHERE name_norm=?", (query,)).fetchone()
    if not row:
        return None
    fact = _gem_reference(row)
    fact["alias"] = row["name"]
    return _result(query, query, [row], [fact], "reference")


def gem_rules(con: sqlite3.Connection) -> dict[str, Any]:
    constraints = {}
    row = con.execute("SELECT value_json FROM gem_rules WHERE key='gem_constraints'").fetchone()
    if row:
        constraints = _json(row["value_json"], {})
    return constraints


def equipment_slots(con: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in con.execute("SELECT * FROM equipment_slots ORDER BY id").fetchall()]


def lookup_rule(con: sqlite3.Connection, question: str) -> dict[str, Any] | None:
    q = norm(question)
    constraints = gem_rules(con)
    if ("孔" in q or "槽" in q) and ("几个" in q or "最多" in q or "上限" in q):
        for slot in equipment_slots(con):
            if norm(slot["name"]) in q:
                return {
                    "query": question,
                    "normalized": q,
                    "matched": True,
                    "match_type": "rule",
                    "matched_alias": slot["name"],
                    "ambiguous": False,
                    "kind": "rule",
                    "kinds": ["rule"],
                    "answer": f"{slot['name']}：最多 {slot['max_slots']} 个孔（默认 {slot['default_slots']}）。",
                    "matches": [],
                    "source": "gem_rules/equipment_slots",
                    "tier": "L3-Inferred",
                    "version": SNAPSHOT,
                    "notes": [],
                    "fields": {
                        "slot": slot["name"],
                        "max_slots": slot["max_slots"],
                        "min_slots": slot["min_slots"],
                        "default_slots": slot["default_slots"],
                    },
                }
    if "总点数" in q or ("点数" in q and ("上限" in q or "最多" in q)):
        value = constraints.get("max_total_points")
        return {
            "query": question,
            "normalized": q,
            "matched": True,
            "match_type": "rule",
            "matched_alias": "max_total_points",
            "ambiguous": False,
            "kind": "rule",
            "kinds": ["rule"],
                    "answer": f"单个宝石总点数上限：{value}（同一件装备多颗宝石不合并计算）。",
            "matches": [],
            "source": "gem_rules",
            "tier": "L3-Inferred",
            "version": SNAPSHOT,
            "notes": [],
            "fields": {"max_total_points": value, "scope": "per_gem", **constraints},
        }
    if "属性" in q and ("几个" in q or "最多" in q or "上限" in q or "条" in q):
        value = constraints.get("max_properties")
        return {
            "query": question,
            "normalized": q,
            "matched": True,
            "match_type": "rule",
            "matched_alias": "max_properties",
            "ambiguous": False,
            "kind": "rule",
            "kinds": ["rule"],
            "answer": f"单颗宝石最多 {value} 条属性，每条 {constraints.get('min_points_per_property')}–"
            f"{constraints.get('max_points_per_property')} 点。",
            "matches": [],
            "source": "gem_rules",
            "tier": "L3-Inferred",
            "version": SNAPSHOT,
            "notes": [],
            "fields": {"max_properties": value, **constraints},
        }
    return None


def lookup_question(
    question: str,
    kind: str | None = None,
    con: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    own = con is None
    if own:
        con = open_db()
    try:
        q = norm(question)
        if not q:
            return _result(question, q, [], [], "none")
        id_result = _id_lookup(con, question, kind)
        if id_result:
            return id_result
        rows, match_type = _find_rows(con, q, kind)
        facts = _dedupe(con, rows) if rows else []
        if facts:
            return _result(question, q, rows, facts, match_type)
        reference = _reference_lookup(con, q, kind)
        if reference:
            return reference
        rule = lookup_rule(con, question)
        if rule:
            return rule
        return _result(question, q, [], [], "none")
    finally:
        if own:
            con.close()


def shadow_record(result: dict[str, Any], elapsed_ms: float | None = None) -> dict[str, Any]:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "query": result["query"],
        "matched": result["matched"],
        "match_type": result["match_type"],
        "matched_alias": result["matched_alias"],
        "ambiguous": result["ambiguous"],
        "kinds": result["kinds"],
        "ids": [f"{m['kind']}:{m['id']}" for m in result.get("matches", [])],
        "group_count": len(result.get("candidate_groups", [])),
        "followup": result.get("followup"),
        "answer": result["answer"],
        "elapsed_ms": round(elapsed_ms, 2) if elapsed_ms is not None else None,
    }


def shadow_log(result: dict[str, Any], path: str | Path, elapsed_ms: float | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(shadow_record(result, elapsed_ms), ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only game-kb-v2 lookup")
    parser.add_argument("query", nargs="*", help="问题或实体名")
    parser.add_argument("--kind", choices=sorted(KIND_ORDER), help="限定实体类型")
    parser.add_argument("--db", type=Path, default=DB_DEFAULT)
    parser.add_argument("--log", type=Path, help="追加一行 JSON 影子日志")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = parser.parse_args(argv)
    question = " ".join(args.query).strip()
    if not question:
        parser.error("query required")
    started = time.perf_counter()
    con = open_db(args.db)
    try:
        result = lookup_question(question, args.kind, con)
    finally:
        con.close()
    elapsed = (time.perf_counter() - started) * 1000
    if args.log:
        shadow_log(result, args.log, elapsed)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["answer"])
        for note in result.get("notes", []):
            print(f"注：{note}")
        if result.get("followup"):
            print()
            print(result["followup"])
        if result["matched"]:
            print(f"来源：{result['source']}（{result['tier']}，{result['version']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
