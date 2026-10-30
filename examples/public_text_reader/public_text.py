#!/usr/bin/env python3
"""Offline, deterministic reader for the public NaiKuai summary text."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "public-text-reader/v1"
EXPECTED_DB_SCHEMA = "unified-r3-public"
EXPECTED_CONTENT_SCOPE = "public_clean_only"
DEFAULT_PER_CHUNK_CHARS = 700
DEFAULT_TOTAL_CHARS = 1800
DEFAULT_MAX_EVIDENCE = 3
MULTI_PER_CHUNK_CHARS = 450


class PublicTextError(RuntimeError):
    """Base error for the public text reader."""


class PublicTextIntegrityError(PublicTextError):
    """Raised when the database does not match its verified identity."""


def normalize(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    return re.sub(r"[\s·・\-_/|\\,，。、:：;；\"“”'‘’()（）\[\]【】<>《》!！?？*#`~]+", "", text)


_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _cn_to_int(value: str) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    if value in _CN_DIGITS:
        return _CN_DIGITS[value]
    total = 0
    current = 0
    for char in value:
        if char in _CN_DIGITS:
            current = _CN_DIGITS[char]
        elif char == "十":
            total += (current or 1) * 10
            current = 0
        elif char == "百":
            total += (current or 1) * 100
            current = 0
        else:
            return None
    return total + current if total + current else None


def _parse_layer(value: str | None) -> int | None:
    match = re.search(r"第?\s*([一二三四五六七八九十百\d]+)\s*层", value or "")
    return _cn_to_int(match.group(1)) if match else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _section_parts(section: str | None) -> list[str]:
    return [part.strip() for part in (section or "").split(" / ") if part.strip()]


def _strip_code(value: str | None) -> str:
    return re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", value or "").strip()


def _table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|") if cell.strip()]


def _is_table_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells)


@dataclass
class _TableBlock:
    rows: list[list[str]]


def _table_row_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    parts = stripped.split("|")
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return [cell.strip() for cell in parts]


def _table_blocks(raw: str) -> list[_TableBlock]:
    blocks: list[_TableBlock] = []
    current: list[list[str]] = []
    for line in (raw or "").splitlines():
        cells = _table_row_cells(line)
        if not cells:
            if current:
                blocks.append(_TableBlock(current))
                current = []
            continue
        if _is_table_separator([cell for cell in cells if cell]):
            continue
        if any(cell for cell in cells):
            current.append(cells)
    if current:
        blocks.append(_TableBlock(current))
    return blocks


def _render_table_block(block: _TableBlock) -> str:
    width = max((len(row) for row in block.rows), default=0)
    lines: list[str] = []
    for row in block.rows:
        padded = row + [""] * (width - len(row))
        rendered = " | ".join(padded).strip()
        if rendered:
            lines.append(rendered)
    return "\n".join(lines)


def _render_table_blocks(raw: str) -> str:
    rendered = [_render_table_block(block) for block in _table_blocks(raw)]
    return "\n\n".join(part for part in rendered if part)


def _table_cell_text(value: str) -> str:
    return _readable_text(value)


def _system_bonus_groups(raw: str) -> list[tuple[str, list[tuple[str, str]]]]:
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    for block in _table_blocks(raw):
        headings = [
            index
            for index, row in enumerate(block.rows)
            if sum("精灵装备加成" in cell for cell in row) >= 2
        ]
        for heading_index in headings:
            heading = block.rows[heading_index]
            end = next(
                (index for index in headings if index > heading_index),
                len(block.rows),
            )
            anchors = [
                index for index, cell in enumerate(heading) if "精灵装备加成" in cell
            ]
            for anchor in anchors:
                label = heading[anchor].split("-", 1)[-1].strip()
                values: list[tuple[str, str]] = []
                for row in block.rows[heading_index + 1 : end]:
                    if anchor >= len(row):
                        continue
                    level = _table_cell_text(row[anchor])
                    effect = _table_cell_text(row[anchor + 1]) if anchor + 1 < len(row) else ""
                    if level or effect:
                        values.append((level, effect))
                if label and values:
                    groups.append((label, values))
    return groups


def _system_slot(segment: "_SectionSegment") -> str:
    for part in reversed(segment.chunk.parts):
        match = re.match(r"^\d+(?:\.\d+)+\s*(.+)$", part.strip())
        if not match:
            continue
        label = match.group(1).strip()
        if label and "精灵装备加成" not in label:
            return label
    return ""


def _system_bonus_condition(segments: list["_SectionSegment"]) -> str | None:
    for segment in segments:
        match = re.search(r"[（(]([^）)]*精灵属性发挥百分比[^）)]*)[）)]", segment.title)
        if match:
            return match.group(1).strip()
    return None


def _system_set_rows(segments: list["_SectionSegment"]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for segment in segments:
        for block in _table_blocks(segment.raw):
            for row in block.rows:
                if len(row) < 2:
                    continue
                name = _table_cell_text(row[0])
                effect = _table_cell_text(row[1])
                if not name or not effect:
                    continue
                if name == "套装名称" or name.startswith("---"):
                    continue
                item = (name, effect)
                if item not in seen:
                    seen.add(item)
                    rows.append(item)
    return rows


def _system_level_filter(question_norm: str) -> set[int] | None:
    if any(term in question_norm for term in ("各级", "每级", "所有等级", "全部等级")):
        return None
    range_match = re.search(r"(\d{1,2})\s*[-–—－~～到至]\s*(\d{1,2})\s*级", question_norm)
    if range_match:
        low, high = sorted((int(range_match.group(1)), int(range_match.group(2))))
        return set(range(low, high + 1))
    levels = {int(value) for value in re.findall(r"(\d{1,2})\s*级", question_norm)}
    return levels or None


def _system_detail_requested(question_norm: str) -> bool:
    if re.search(r"\d{1,2}\s*级", question_norm):
        return True
    if re.search(r"多少(?:个)?部位|几个部位|几件|哪些部位", question_norm):
        return False
    return any(
        term in question_norm
        for term in ("数值", "具体", "多少", "每级", "各级", "等级表", "几级", "满级")
    )


def _render_system_segment(
    segment: "_SectionSegment",
    levels: set[int] | None = None,
    wanted_labels: set[str] | None = None,
) -> str:
    title = f"{segment.code}{segment.title.split(' / ')[0]}".strip()
    groups = _system_bonus_groups(segment.raw)
    if not groups:
        rendered = _render_table_blocks(segment.raw)
        body = rendered or segment.readable
        return f"{title}\n{body}" if title and title not in body else body
    slot = _system_slot(segment)
    lines: list[str] = []
    for label, values in groups:
        if label == "无":
            continue
        if wanted_labels is not None and label not in wanted_labels:
            continue
        filtered: list[tuple[str, str]] = []
        for level, effect in values:
            if levels is not None:
                match = re.search(r"(\d{1,2})\s*级", level)
                if not match or int(match.group(1)) not in levels:
                    continue
            filtered.append((level, effect))
        if not filtered:
            continue
        if not lines and title:
            lines.append(title)
        if slot:
            lines.append(f"{slot}｜{label}")
        else:
            lines.append(label)
        lines.extend(f"{level}：{effect}" for level, effect in filtered)
    return "\n".join(lines)


def _system_overview_source(segment: "_SectionSegment") -> str:
    title = f"{segment.code}{segment.title.split(' / ')[0]}".strip()
    groups = _system_bonus_groups(segment.raw)
    if groups:
        slot = _system_slot(segment)
        names = [name for name, _values in groups if name != "无"]
        line = f"{slot}：{'、'.join(names)}" if slot else f"属性：{'、'.join(names)}"
        condition = _system_bonus_condition([segment])
        return "\n".join(part for part in (title, line, condition) if part)
    sets = _system_set_rows([segment])
    if sets:
        return "\n".join(
            [title] + [f"{name}：{effect}" for name, effect in sets]
        )
    return _render_system_segment(segment)


def _gem_effect_row_text(text: str, name: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not name:
        return "\n".join(lines)
    key = normalize(name)
    if not key:
        return ""
    chosen: list[int] = []
    for index, line in enumerate(lines):
        head = re.split(r"[：:]", line, maxsplit=1)[0]
        if key in normalize(head):
            chosen.append(index)
    if not chosen:
        return ""
    selected: set[int] = set()
    for index in chosen:
        for back in range(index - 1, -1, -1):
            line = lines[back]
            if "：" in line or ":" in line:
                continue
            if re.match(r"^\d+(?:\.\d+)+\s", line) or len(line) > 30:
                break
            selected.add(back)
            break
        selected.add(index)
    return "\n".join(lines[index] for index in sorted(selected))


def _gem_upgrade_groups(raw: str) -> list[tuple[str, list[tuple[str, str, str]]]]:
    groups: list[tuple[str, list[tuple[str, str, str]]]] = []
    shape = ""
    rows: list[tuple[str, str, str]] = []
    for block in _table_blocks(raw):
        for row in block.rows:
            if len(row) >= 3 and "升级档次" in row[0]:
                continue
            if len(row) >= 3 and "消耗材料" in row[1]:
                continue
            if len(row) >= 3 and "升级" in row[1]:
                first = _table_cell_text(row[0])
                if first and "升级" not in first:
                    if shape and rows:
                        groups.append((shape, rows))
                    shape = first
                    rows = []
                stage = _table_cell_text(row[1])
                material = _table_cell_text(row[2])
                if shape and (stage or material):
                    rows.append((shape, stage, material))
            elif len(row) >= 2 and shape and "升级" in row[0]:
                stage = _table_cell_text(row[0])
                material = _table_cell_text(row[1])
                if stage or material:
                    rows.append((shape, stage, material))
    if shape and rows:
        groups.append((shape, rows))
    return groups


def _gem_upgrade_text(segment: "_SectionSegment", conditions: dict[str, Any]) -> str:
    groups = _gem_upgrade_groups(segment.raw)
    if not groups:
        return segment.readable
    wanted_shape = conditions.get("gem_shape")
    wanted_stage = conditions.get("gem_stage")
    lines = ["升级材料（形状｜升级档次：材料）"]
    for shape, rows in groups:
        if wanted_shape and shape != wanted_shape:
            continue
        for row_shape, stage, material in rows:
            if wanted_stage and wanted_stage not in stage:
                continue
            lines.append(f"{row_shape}｜{stage}：{material}")
    return "\n".join(lines) if len(lines) > 1 else segment.readable


def _readable_text(raw: str | None) -> str:
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"~~(.+?)~~", "", text, flags=re.S)
    text = re.sub(r"【数据摘要[^\n]*】", "", text)
    lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("**") and stripped.endswith("**") and len(stripped) > 4:
            stripped = stripped[2:-2].strip()
        cells = _table_cells(stripped)
        if cells:
            if _is_table_separator(cells):
                continue
            if len(cells) == 1:
                lines.append(cells[0])
            elif len(cells) == 2:
                lines.append(f"{cells[0]}：{cells[1]}")
            else:
                lines.append(f"{cells[0]}：" + "；".join(cells[1:]))
            continue
        if stripped.startswith("|"):
            continue
        lines.append(stripped)
    cleaned: list[str] = []
    for line in lines:
        if any(marker in line for marker in ("数据为猜测", "未上线", "历史数据", "旧版数据")):
            continue
        line = re.sub(r"([：:，,；;、])\s*[，,；;、]+", r"\1", line)
        line = re.sub(r"[，,；;、]+([。.!！?？])", r"\1", line)
        line = re.sub(r"([：:])\s*[，,；;、]+", r"\1", line)
        line = line.strip(" ，,；;、")
        if not line:
            continue
        if cleaned and cleaned[-1] == line:
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


_GENERIC_WORLDS = {
    "副本",
    "秘境",
    "活动秘境",
    "副本信息",
    "各精灵介绍",
    "各职业的深刻理解",
    "跟宠",
    "精灵",
    "数据摘要",
    "第四章 副本",
    "第二章 PVE数据总结",
    "第六章 精灵",
}


def _world_from_section(parts: list[str]) -> str:
    if len(parts) < 2:
        return ""
    chapter = parts[1]
    if chapter.startswith("4.2轮回塔"):
        return "轮回塔"
    if not chapter.startswith("4.1秘境") or len(parts) < 3:
        return ""
    candidate = _strip_code(parts[2])
    candidate = re.sub(r"T\d+", "", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"[-－]\d{6,8}", "", candidate)
    candidate = candidate.strip(" -－")
    if not candidate or candidate in _GENERIC_WORLDS or candidate.startswith(("第", "4.")):
        return ""
    return candidate


def _players_from_text(text: str | None) -> tuple[int, int] | None:
    for line in (text or "").splitlines():
        cells = _table_cells(line)
        if len(cells) >= 3 and re.fullmatch(r"\d{1,2}\s*[-~～至]\s*\d{1,2}", cells[2]):
            left, right = re.split(r"[-~～至]", cells[2])
            return int(left), int(right)
    for match in re.finditer(r"（\s*([一二三四五六七八九十两\d]{1,3})\s*人\s*）", text or ""):
        count = _cn_to_int(match.group(1))
        if count:
            return count, count
    match = re.search(r"(?:开启人数|人数)[：:\s]*(\d{1,2})\s*[-~～至]\s*(\d{1,2})", text or "")
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


_GENERIC_LABELS = {
    "精灵",
    "职业",
    "副本",
    "秘境",
    "宝石",
    "跟宠",
    "宠物",
    "活动秘境",
    "各精灵介绍",
    "各职业的深刻理解",
    "数据摘要",
    "副本信息",
    "基础数据",
    "面板",
    "介绍",
    "记录",
    "说明",
    "其他",
    "未完成",
}

_GENERIC_SUFFIXES = (
    "数据",
    "面板",
    "介绍",
    "记录",
    "基础",
    "备注",
    "其他",
    "说明",
    "机制",
    "信息",
    "平衡",
    "效果",
    "输出",
    "辅助",
    "技能",
    "装备",
    "材料",
    "任务",
    "故事",
    "图鉴",
    "属性",
)

_GUIDE_MARKERS = (
    "阶段",
    "机制",
    "战斗区域",
    "普通攻击",
    "范围",
    "分摊",
    "仇恨",
    "召唤",
    "读条",
    "狂暴",
    "打断",
    "躲避",
    "站位",
    "处理",
)

_PROFESSION_CORE_MARKERS = (
    "核心",
    "机制",
    "资源",
    "热量",
    "能量",
    "怒气",
    "子弹",
    "架势",
    "基础减伤",
    "循环",
    "连招",
    "注意",
    "限制",
    "主要",
    "技能",
    "输出手法",
    "玩法",
    "攻速",
    "倍率",
    "淬毒",
    "多重",
    "爆裂",
    "背刺",
    "突袭",
    "潜行",
    "月影",
    "战刃",
    "短匕",
    "刺击",
    "铳枪",
    "火铳",
    "决斗",
    "骑士誓约",
)

_GENERIC_NAME_PARTS = (
    "平衡",
    "机制",
    "公式",
    "规则",
    "破甲",
    "免伤",
    "防具",
    "武器",
    "装备",
    "材料",
    "面板",
    "数据",
    "商店",
    "奖励",
    "经验",
    "任务",
)


def _clean_name(piece: str) -> tuple[str, bool, int | None, str | None] | None:
    original = piece or ""
    cleaned = _strip_code(original)
    cleaned = re.sub(r"（[^）]*）|\([^)]*\)", "", cleaned)
    cleaned = re.sub(r"[-－]\d{6,8}$", "", cleaned)
    cleaned = cleaned.strip(" -－")
    if "-" in cleaned or "－" in cleaned:
        cleaned = re.split(r"[-－]", cleaned)[-1].strip()
    elite = False
    if cleaned.startswith("精英") and len(cleaned) > 2:
        elite = True
        cleaned = cleaned[2:]
    layer = _parse_layer(cleaned)
    base = None
    if layer is not None:
        base = re.sub(r"第\s*[一二三四五六七八九十百\d]+\s*层", "", cleaned).strip(" -－")
    if any(part in original and part not in ("数据", "面板") for part in _GENERIC_NAME_PARTS):
        return None
    if cleaned.endswith("精灵") and cleaned.startswith(("普通", "骑士", "充值", "氪金", "魔防", "物防", "各")):
        return None
    if cleaned.startswith(("普通", "骑士", "充值", "氪金")) and "精灵" in cleaned:
        return None
    changed = True
    while changed:
        changed = False
        for suffix in _GENERIC_SUFFIXES:
            if cleaned.endswith(suffix) and len(cleaned) > len(suffix) + 1:
                cleaned = cleaned[: -len(suffix)].strip()
                changed = True
    if cleaned.startswith("各") and any(key in cleaned for key in ("职业", "精灵", "副本", "宝石")):
        return None
    if cleaned in _GENERIC_LABELS or len(cleaned) < 2:
        return None
    if base and base not in _GENERIC_LABELS and len(base) >= 2 and base != cleaned:
        return cleaned, elite, layer, base
    return cleaned, elite, layer, None


def _split_and_clean(value: str | None) -> list[tuple[str, bool, int | None, str | None]]:
    result: list[tuple[str, bool, int | None, str | None]] = []
    for piece in re.split(r"[；;/、，]", value or ""):
        cleaned = _clean_name(piece)
        if cleaned:
            result.append(cleaned)
    return result


def _domain_for_section(parts: list[str]) -> str:
    if not parts:
        return "other"
    top = parts[0]
    section = " / ".join(parts)
    if top.startswith("第六章 精灵"):
        return "fairy"
    if top.startswith("第四章 副本"):
        return "dungeon"
    if "2.3对各职业的深刻理解" in section:
        return "profession"
    if "2.6宝石" in section:
        return "gem"
    return "other"


def _extract_boss_names(text: str | None) -> tuple[str, ...]:
    names: list[str] = []
    line_pattern = re.compile(
        r"(?:^|\|)\s*([^\|，,（(]{2,24}?)(?:（[^）]{1,10}）)?\s*[-－]\s*\d{6,8}\s*[，,]\s*(?:\d+\s*生命|攻击等级)"
    )
    hp_pattern = re.compile(
        r"(?:^|\|)\s*([^\|，,（(]{2,24}?)(?:\d+\+\d+)?\s*[，,]\s*(?:\d{2,}\s*生命|攻击等级)"
    )
    inline_pattern = re.compile(
        r"(?:^|[；;。\n])\s*([^\s，,；;：:（）()]{2,16})(?:（[^）]{1,10}）)?\s*[（(]\s*\d{2,}\s*生命"
    )
    generic = {
        "小怪",
        "精英怪",
        "普通怪",
        "首领",
        "boss",
        "BOSS",
        "召唤物",
        "能量体",
        "触须",
        "玩家",
        "副本名称",
        "开启人数",
        "阶段",
        "技能",
    }
    for pattern in (line_pattern, hp_pattern, inline_pattern):
        for match in pattern.finditer(text or ""):
            name = match.group(1).strip(" -－|")
            if len(name) < 2 or name in generic:
                continue
            if any(name.startswith(prefix) for prefix in ("技能", "阶段", "普通", "随机", "每", "对", "在", "并", "同时", "召唤", "刷新", "生成")):
                continue
            if name not in names:
                names.append(name)
    return tuple(names)


@dataclass
class _Chunk:
    pos: int
    chunk_id: str
    doc_id: str
    title: str
    section: str
    category: str
    tier: str
    source: str
    version: str
    chunk_type: str
    raw_text: str
    readable: str
    review_status: str
    status_note: str
    license_basis: str
    parts: tuple[str, ...]
    leaf_raw: str
    domain: str
    world: str
    difficulty: str
    layer: int | None
    explicit_players: tuple[int, int] | None
    players: tuple[int, int] | None
    tier_tag: str
    boss_names: tuple[str, ...]
    had_strikethrough: bool
    historical: bool
    incomplete: bool
    overview: bool
    drops: bool
    mechanics: bool
    labels: tuple[tuple[str, float, str, bool, int | None], ...] = ()
    branch_key: tuple[Any, ...] = ()
    branch_label: str = ""

    @property
    def section_norm(self) -> str:
        return normalize(self.section)

    @property
    def text_norm(self) -> str:
        return normalize(self.readable)


@dataclass
class _SectionSegment:
    chunk: _Chunk
    code: str
    title: str
    raw: str
    readable: str
    historical: bool
    had_strikethrough: bool


def _section_segments(chunk: _Chunk) -> list[_SectionSegment]:
    raw = chunk.raw_text
    heading = re.compile(r"^\s*\*\*(\d+(?:\.\d+)+[^\n*]*)\*\*\s*$", re.M)
    matches = list(heading.finditer(raw))
    pieces: list[tuple[str, str]] = []
    if matches:
        first_leaf = re.split(r"[；;]", chunk.leaf_raw, maxsplit=1)[0]
        pieces.append((raw[: matches[0].start()], first_leaf))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
            pieces.append((raw[match.start() : end], match.group(1).strip()))
    else:
        pieces.append((raw, chunk.leaf_raw))
    segments: list[_SectionSegment] = []
    for piece_raw, title in pieces:
        code_match = re.match(r"^(\d+(?:\.\d+)+)\s*(.*)$", (title or "").strip())
        code = code_match.group(1) if code_match else ""
        title_text = (code_match.group(2) if code_match else (title or "")).strip()
        parent_context = " / ".join(part.strip() for part in chunk.parts[:-1] if part.strip())
        if parent_context:
            title_text = f"{title_text} / {parent_context}".strip(" /")
        readable = _readable_text(piece_raw)
        struck = "~~" in piece_raw
        historical = chunk.historical or (struck and not readable)
        if not title_text and code:
            title_text = code
        segments.append(
            _SectionSegment(
                chunk=chunk,
                code=code,
                title=title_text,
                raw=piece_raw,
                readable=readable,
                historical=historical,
                had_strikethrough=struck,
            )
        )
    return segments


@dataclass
class _EntityRef:
    name: str
    domain: str
    positions: set[int] = field(default_factory=set)
    weight: float = 0.0
    kind: str = "leaf"


@dataclass
class _ExcerptUnit:
    chunk: "_Chunk"
    excerpt: str
    truncated: bool


@dataclass
class _Conditions:
    players: int | None = None
    difficulty: str | None = None
    layer: int | None = None
    world: str | None = None
    tier: str | None = None

    def any(self) -> bool:
        return any(value is not None for value in (self.players, self.difficulty, self.layer, self.world, self.tier))

    def describe(self) -> list[str]:
        parts: list[str] = []
        if self.players is not None:
            parts.append(f"{self.players}人")
        if self.difficulty:
            parts.append(self.difficulty)
        if self.layer is not None:
            parts.append(f"第{self.layer}层")
        if self.world:
            parts.append(self.world)
        if self.tier:
            parts.append(self.tier)
        return parts


class _Index:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.chunks: list[_Chunk] = []
        self.entities: dict[str, dict[str, _EntityRef]] = {}
        self.senses: dict[str, list[tuple[str, list[str], str]]] = {}
        self.worlds: dict[str, dict[str, Any]] = {}
        self.gem_positions: list[int] = []
        self.section_chunks: dict[str, list[int]] = {}
        self._load_chunks()
        self._assign_players()
        self._build_entities()
        self._build_worlds()
        self._load_aliases()
        self._load_senses()

    def _load_chunks(self) -> None:
        rows = self.connection.execute(
            """
            SELECT rowid, chunk_id, doc_id, title, section, category, tier, source, version,
                   chunk_type, char_count, publishable, sensitive, text, review_status,
                   status_note, license_basis
            FROM chunks
            ORDER BY rowid
            """
        ).fetchall()
        for row in rows:
            if not row["publishable"] or row["sensitive"]:
                continue
            if (row["review_status"] or "active") != "active":
                continue
            raw = row["text"] or ""
            readable = _readable_text(raw)
            parts = tuple(_section_parts(row["section"]))
            section = row["section"] or ""
            leaf = parts[-1] if parts else ""
            domain = _domain_for_section(list(parts))
            if domain not in {"profession", "fairy", "dungeon", "gem"}:
                continue
            world = _world_from_section(list(parts))
            explicit_players = _players_from_text(raw) if domain == "dungeon" else None
            difficulty = ""
            if domain == "dungeon":
                difficulty = "精英" if "精英" in leaf else "普通"
            tier_match = re.search(r"T(\d+)", section, flags=re.IGNORECASE)
            index = len(self.chunks)
            chunk = _Chunk(
                pos=index,
                chunk_id=row["chunk_id"],
                doc_id=row["doc_id"] or "",
                title=row["title"] or "",
                section=section,
                category=row["category"] or "",
                tier=row["tier"] or "",
                source=row["source"] or "",
                version=row["version"] or "",
                chunk_type=row["chunk_type"] or "",
                raw_text=raw,
                readable=readable,
                review_status=row["review_status"] or "active",
                status_note=row["status_note"] or "",
                license_basis=row["license_basis"] or "",
                parts=parts,
                leaf_raw=leaf,
                domain=domain,
                world=world,
                difficulty=difficulty,
                layer=_parse_layer(leaf),
                explicit_players=explicit_players,
                players=explicit_players,
                tier_tag=f"T{tier_match.group(1)}" if tier_match else "",
                boss_names=_extract_boss_names(raw) if domain == "dungeon" else (),
                had_strikethrough="~~" in raw,
                historical=(
                    any(marker in section for marker in ("历史", "旧版", "过期", "作废", "已删除", "未上线"))
                    or (not readable and ("~~" in raw or "猜测" in raw or "未上线" in raw))
                ),
                incomplete="未完成" in section or "未完成" in raw[:120],
                overview=("开启人数" in raw and "副本名称" in raw),
                drops="掉落" in readable,
                mechanics=any(marker in readable for marker in ("阶段", "技能", "机制", "攻击")),
            )
            if domain == "gem":
                self.gem_positions.append(index)
            self.chunks.append(chunk)
            self.section_chunks.setdefault(section, []).append(index)

    def _assign_players(self) -> None:
        unique: dict[str, set[tuple[int, int] | None]] = {}
        for chunk in self.chunks:
            unique.setdefault(chunk.section, set()).add(chunk.explicit_players)
        single = {
            section: next(iter(values))
            for section, values in unique.items()
            if len(values) == 1 and next(iter(values)) is not None
        }
        current: dict[str, tuple[int, int] | None] = {}
        for chunk in self.chunks:
            if chunk.explicit_players is not None:
                current[chunk.section] = chunk.explicit_players
            if chunk.players is None:
                if chunk.section in current:
                    chunk.players = current[chunk.section]
                elif chunk.section in single:
                    chunk.players = single[chunk.section]
        for chunk in self.chunks:
            chunk.branch_key = (chunk.section, chunk.players, chunk.difficulty, chunk.layer)
            chunk.branch_label = self._branch_label(chunk)

    @staticmethod
    def _branch_label(chunk: _Chunk) -> str:
        base = _strip_code(chunk.leaf_raw)
        base = re.sub(r"（[^）]*）|\([^)]*\)", "", base)
        base = re.sub(r"[-－]\d{6,8}$", "", base).strip(" -－")
        if chunk.players:
            base = f"{base}（{chunk.players[0]}-{chunk.players[1]}）"
        if chunk.incomplete and "未完成" not in base:
            base = f"{base}（未完成）"
        return base

    def _add_entity(self, norm: str, name: str, domain: str, positions: set[int], weight: float, kind: str) -> None:
        if len(norm) < 2:
            return
        domain_map = self.entities.setdefault(norm, {})
        ref = domain_map.get(domain)
        if ref is None:
            domain_map[domain] = _EntityRef(name=name, domain=domain, positions=set(positions), weight=weight, kind=kind)
            return
        ref.positions.update(positions)
        priority = {"leaf": 4, "base": 3, "boss": 3, "mid": 2, "panel": 2, "alias": 1}
        if priority.get(kind, 0) >= priority.get(ref.kind, 0):
            ref.name = name
            ref.kind = kind
        ref.weight = max(ref.weight, weight)

    def _build_entities(self) -> None:
        for chunk in self.chunks:
            if chunk.domain == "profession":
                if "2.3对各职业的深刻理解" not in chunk.section:
                    continue
                labels: list[tuple[str, float, str, bool, int | None]] = []
                for name, elite, layer, base in _split_and_clean(chunk.leaf_raw):
                    labels.append((name, 3.0, "leaf", elite, layer))
                for part in chunk.parts:
                    if part == chunk.parts[0] or part == chunk.leaf_raw:
                        continue
                    if not re.match(r"^\s*2\.3\.\d", part):
                        continue
                    for name, elite, layer, base in _split_and_clean(part):
                        labels.append((name, 2.0, "mid", elite, layer))
                chunk.labels = tuple(labels)
            elif chunk.domain == "fairy":
                labels = []
                if "6.2各精灵介绍" in chunk.section:
                    for name, elite, layer, base in _split_and_clean(chunk.leaf_raw):
                        labels.append((name, 3.0, "leaf", elite, layer))
                elif "6.3.6精灵显性面板" in chunk.section or "6.3.7精灵隐性面板" in chunk.section:
                    for name, elite, layer, base in _split_and_clean(chunk.leaf_raw):
                        labels.append((name, 2.0, "panel", elite, layer))
                chunk.labels = tuple(labels)
            elif chunk.domain == "dungeon":
                labels = []
                if "4.1秘境" in chunk.section or "4.2轮回塔" in chunk.section:
                    for name, elite, layer, base in _split_and_clean(chunk.leaf_raw):
                        labels.append((name, 3.0, "leaf", elite, layer))
                        if base:
                            labels.append((base, 2.5, "base", elite, None))
                chunk.labels = tuple(labels)
            for name, weight, kind, elite, layer in chunk.labels:
                norm = normalize(name)
                self._add_entity(norm, name, chunk.domain, {chunk.pos}, weight, kind)
                if chunk.domain == "profession" and "精英" in chunk.leaf_raw:
                    pass
            if chunk.domain == "dungeon" and (
                "4.1秘境" in chunk.section or "4.2轮回塔" in chunk.section
            ):
                for boss in chunk.boss_names:
                    self._add_entity(normalize(boss), boss, "dungeon", {chunk.pos}, 2.5, "boss")

    def _build_worlds(self) -> None:
        for chunk in self.chunks:
            if chunk.domain != "dungeon" or not chunk.world:
                continue
            norm = normalize(chunk.world)
            entry = self.worlds.setdefault(norm, {"name": chunk.world, "positions": set(), "dungeons": set()})
            entry["positions"].add(chunk.pos)
            for name, weight, kind, elite, layer in chunk.labels:
                if kind in {"leaf", "base"}:
                    entry["dungeons"].add(name)

    def _load_aliases(self) -> None:
        try:
            rows = self.connection.execute(
                """
                SELECT alias, alias_norm, canonical, canonical_norm, resolution,
                       status, use_in_chat, publishable
                FROM aliases
                """
            ).fetchall()
        except sqlite3.OperationalError:
            return
        targets: dict[str, list[_EntityRef]] = {}
        for norm, domain_map in self.entities.items():
            for ref in domain_map.values():
                targets.setdefault(norm, []).append(ref)
        for row in rows:
            if not row["use_in_chat"] or not row["publishable"]:
                continue
            if row["resolution"] == "ambiguous":
                continue
            alias_norm = row["alias_norm"] or normalize(row["alias"])
            for part in re.split(r"[+＋/、，,]", row["canonical"] or ""):
                canonical_norm = normalize(part)
                for reference in targets.get(canonical_norm, []):
                    self._add_entity(alias_norm, reference.name, reference.domain, set(reference.positions), 2.0, "alias")

    def _load_senses(self) -> None:
        try:
            rows = self.connection.execute(
                "SELECT alias_norm, alias, sense, keywords FROM alias_senses"
            ).fetchall()
        except sqlite3.OperationalError:
            return
        for row in rows:
            surface = row["alias_norm"] or normalize(row["alias"])
            target_norm = normalize(row["sense"])
            if target_norm not in self.entities:
                continue
            keywords = [keyword.strip() for keyword in (row["keywords"] or "").split("|") if keyword.strip()]
            self.senses.setdefault(surface, []).append((row["sense"], keywords, target_norm))

    def match_entities(self, question_norm: str) -> list[tuple[int, float, _EntityRef]]:
        matches: list[tuple[int, float, _EntityRef]] = []
        for norm, domain_map in self.entities.items():
            if len(norm) < 2 or norm not in question_norm:
                continue
            for ref in domain_map.values():
                matches.append((len(norm), ref.weight, ref))
        for surface, senses in self.senses.items():
            if surface not in question_norm:
                continue
            chosen = [sense for sense in senses if any(keyword in question_norm for keyword in sense[1])]
            if len(chosen) == 1:
                target_norm = chosen[0][2]
                for ref in self.entities.get(target_norm, {}).values():
                    matches.append((len(surface), ref.weight, ref))
        matches.sort(key=lambda item: (-item[0], -item[1], item[2].name, item[2].domain))
        return matches

    def profession_names(self) -> list[str]:
        names = {ref.name for norm, domains in self.entities.items() for domain, ref in domains.items() if domain == "profession"}
        return sorted(names)

    def fairy_names(self) -> list[str]:
        names = {ref.name for norm, domains in self.entities.items() for domain, ref in domains.items() if domain == "fairy"}
        return sorted(names)

    def dungeon_names(self) -> list[str]:
        names = {ref.name for norm, domains in self.entities.items() for domain, ref in domains.items() if domain == "dungeon" and ref.kind in {"leaf", "base"}}
        return sorted(names)


_INTENT_STOPWORDS = (
    "请问",
    "麻烦",
    "一下",
    "大佬",
    "求问",
    "知道",
    "告诉我",
    "帮我",
    "看看",
    "想问",
    "问下",
    "是啥",
    "是什么",
    "什么",
    "怎么",
    "如何",
    "哪些",
    "哪个",
    "哪里",
    "多少",
    "几个",
    "几人",
    "几个人",
    "是不是",
    "有没有",
    "能不能",
    "有什么作用",
    "有什么用",
    "有啥用",
    "用处",
    "作用",
    "可以",
    "吗",
    "呢",
    "啊",
    "吧",
    "呀",
    "的",
    "了",
    "是",
    "有",
    "和",
    "与",
    "及",
    "还有",
    "介绍",
    "机制",
    "玩法",
    "技能",
    "攻略",
    "打法",
    "怎么打",
    "怎么玩",
    "副本",
    "秘境",
    "精灵",
    "职业",
    "宝石",
    "镶嵌",
    "升级",
    "切割",
    "效果",
    "面板",
    "培养",
    "掉落",
    "入口",
    "坐标",
    "世界",
    "人数",
    "层",
    "难度",
    "精英",
    "普通",
    "多人",
    "单人",
    "一人",
    "三人",
    "五人",
    "十人",
    "各",
    "每个",
    "每一个",
    "所有",
    "全部",
    "分别",
    "分别的",
    "一些",
    "相关",
    "内容",
    "资料",
    "信息",
    "介绍下",
    "讲讲",
    "说说",
    "第",
    "层",
)


_NON_FAIRY_ITEM_TERMS = (
    "精灵石",
    "飞行精灵石",
    "精灵书",
    "精灵学识",
    "精灵礼物",
    "转生礼物",
    "精灵飞行",
    "精灵转生",
    "精灵项链",
    "精灵打包",
)

_SYSTEM_QUERY_TOPICS = (
    ("dismantle", ("分解", "拆解")),
    ("correspondence", ("对应", "适配", "哪个精灵", "哪些精灵", "给谁")),
    ("set", ("套装",)),
    ("comparison", ("对比", "比较", "相比", "比得上")),
    ("upgrade", ("升级", "升阶")),
    ("cut", ("切割", "切石")),
    ("inlay", ("镶嵌",)),
    ("parts", ("部位", "部件", "发饰", "项链", "戒指", "几件")),
    ("acquisition", ("获取", "获得", "哪里得", "哪里获得", "怎么得", "出处", "来源", "掉落")),
    (
        "bonus",
        (
            "加成",
            "属性",
            "作用",
            "用途",
            "用处",
            "有用",
            "有什么用",
            "有啥用",
            "怎么用",
            "用来",
            "干什么",
            "干啥",
            "干嘛",
            "能做什么",
            "做什么",
            "提升什么",
            "效果",
        ),
    ),
)

_SYSTEM_TITLE_TOPICS = (
    ("dismantle", ("分解", "拆解")),
    ("correspondence", ("对应",)),
    ("set", ("套装",)),
    ("comparison", ("对比", "比较")),
    ("upgrade", ("升级", "升阶")),
    ("cut", ("切割",)),
    ("inlay", ("镶嵌",)),
    ("effect", ("效果",)),
    ("bonus", ("加成", "属性")),
)

_SYSTEM_MISSING_LABELS = {
    "acquisition": "获取/来源说明",
    "upgrade": "升级规则或材料",
    "cut": "切割规则或概率",
    "inlay": "镶嵌规则或效果",
}


def _system_query_topic(question_norm: str) -> str | None:
    for topic, terms in _SYSTEM_QUERY_TOPICS:
        if any(term in question_norm for term in terms):
            return topic
    return None


def _segment_topic(title: str) -> str | None:
    for topic, terms in _SYSTEM_TITLE_TOPICS:
        if any(term in title for term in terms):
            return topic
    return None


def _leftover(question_norm: str) -> str:
    value = re.sub(r"第?[一二三四五六七八九十百\d]+层", "", question_norm)
    for word in sorted(_INTENT_STOPWORDS, key=len, reverse=True):
        value = value.replace(word, "")
    return value


def _players_match(players: tuple[int, int] | None, wanted: int) -> bool:
    return bool(players and players[0] <= wanted <= players[1])


def _result_buckets() -> dict[str, list[Any]]:
    return {"confirmed": [], "conditional": [], "conflict": [], "unknown": []}


class PublicTextReader:
    """Read-only reader over one verified public NaiKuai SQLite snapshot."""

    def __init__(
        self,
        db_path: str | Path,
        expected_sha256: str,
        *,
        per_chunk_chars: int = DEFAULT_PER_CHUNK_CHARS,
        total_chars: int = DEFAULT_TOTAL_CHARS,
        max_evidence: int = DEFAULT_MAX_EVIDENCE,
        verify_manifest: bool = True,
    ):
        self.path = Path(db_path)
        if not self.path.is_file():
            raise PublicTextError(f"database not found: {self.path}")
        expected = (expected_sha256 or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise PublicTextIntegrityError("expected_sha256 must be a 64-character hex digest")
        self.expected_sha256 = expected
        self.actual_sha256 = _sha256_file(self.path)
        if not hmac.compare_digest(self.actual_sha256, expected):
            raise PublicTextIntegrityError(
                f"SHA-256 mismatch: expected {expected}, got {self.actual_sha256}"
            )
        self.per_chunk_chars = max(120, int(per_chunk_chars))
        self.total_chars = max(self.per_chunk_chars, int(total_chars))
        self.max_evidence = max(1, int(max_evidence))
        self.manifest_verified = False
        self.manifest: dict[str, Any] = {}
        self._conn: sqlite3.Connection | None = None
        self._closed = False
        if verify_manifest:
            self._verify_manifest_if_present()
        uri = self.path.resolve().as_uri() + "?mode=ro&immutable=1"
        try:
            self._conn = sqlite3.connect(uri, uri=True, timeout=1.0)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA query_only=ON")
        except sqlite3.Error as exc:
            raise PublicTextError(f"cannot open database: {exc}") from exc
        try:
            self.meta = dict(self._conn.execute("SELECT key, value FROM meta").fetchall())
            self._validate_meta()
            self.facts_count = int(self._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
            if self.facts_count != 0:
                raise PublicTextError("public reader requires facts=0")
            self.index = _Index(self._conn)
            if not self.index.chunks:
                raise PublicTextError("no public chunks available")
        except Exception:
            self.close()
            raise

    def _verify_manifest_if_present(self) -> None:
        manifest_path = self.path.with_name("manifest.json")
        if not manifest_path.is_file():
            return
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise PublicTextIntegrityError(f"cannot read manifest: {exc}") from exc
        entry = None
        for item in data.get("files", []):
            if item.get("path") == self.path.name:
                entry = item
                break
        if entry is None:
            raise PublicTextIntegrityError("manifest has no entry for this database")
        if str(entry.get("sha256", "")).lower() != self.actual_sha256:
            raise PublicTextIntegrityError("manifest SHA-256 does not match the database")
        size = entry.get("size_bytes")
        if size is not None and int(size) != self.path.stat().st_size:
            raise PublicTextIntegrityError("manifest size does not match the database")
        self.manifest = data
        self.manifest_verified = True

    def _validate_meta(self) -> None:
        schema = self.meta.get("schema_version")
        if schema != EXPECTED_DB_SCHEMA:
            raise PublicTextError(f"unexpected database schema_version: {schema!r}")
        scope = self.meta.get("content_scope")
        if scope != EXPECTED_CONTENT_SCOPE:
            raise PublicTextError(f"database is not a public clean snapshot: content_scope={scope!r}")
        stats_raw = self.meta.get("stats")
        if stats_raw:
            try:
                stats = json.loads(stats_raw)
            except json.JSONDecodeError as exc:
                raise PublicTextError("invalid stats metadata") from exc
            if int(stats.get("facts_count", 0)) != 0:
                raise PublicTextError("metadata reports non-zero facts_count")

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
                self._closed = True

    def __enter__(self) -> "PublicTextReader":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def database_info(self) -> dict[str, Any]:
        return {
            "file": self.path.name,
            "sha256": self.actual_sha256,
            "expected_sha256": self.expected_sha256,
            "sha256_verified": True,
            "manifest_verified": self.manifest_verified,
            "schema_version": self.meta.get("schema_version"),
            "kb_version": self.meta.get("kb_version"),
            "summary_version": self.meta.get("summary_version"),
            "summary_snapshot_date": self.meta.get("summary_snapshot_date"),
            "summary_review_due": self.meta.get("summary_review_due"),
            "built_at": self.meta.get("built_at"),
            "content_scope": self.meta.get("content_scope"),
            "facts_count": self.facts_count,
            "chunks_count": len(self.index.chunks),
        }

    def _base_result(self, question: str) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "query": question,
            "matched": False,
            "action": "abstain",
            "reason": "out_of_scope",
            "route": "none",
            "text": "",
            "buckets": _result_buckets(),
            "evidence": [],
            "database": self.database_info(),
            "pagination": {
                "limit": self.max_evidence,
                "returned": 0,
                "total_candidates": 0,
                "has_more": False,
                "next_cursor": None,
                "truncated": False,
                "char_budget": self.total_chars,
                "per_chunk_budget": self.per_chunk_chars,
                "char_used": 0,
                "remaining_candidates": 0,
                "note": None,
            },
            "intent": {
                "domain": "out_of_scope",
                "entity": None,
                "conditions": {},
                "branch": None,
            },
            "clarify": None,
            "limits": {
                "network": False,
                "model": False,
                "confirmed_supported": False,
                "conflict_resolution": "out_of_scope",
                "config_values": "not_answered",
                "evidence_policy": "public_summary_text_is_conditional",
                "confirmation_scope": {
                    "current_game_config_checked": False,
                    "basis": "public_summary_only",
                    "release_sha256": self.actual_sha256,
                    "manifest_verified": self.manifest_verified,
                    "kb_version": self.meta.get("kb_version"),
                },
                "excerpt_scope": "source_fragment_not_complete_current_guide",
                "source_processing": [],
            },
        }

    def _empty_pagination(self, total: int = 0) -> dict[str, Any]:
        return {
            "limit": self.max_evidence,
            "returned": 0,
            "total_candidates": total,
            "has_more": False,
            "next_cursor": None,
            "truncated": False,
            "char_budget": self.total_chars,
            "per_chunk_budget": self.per_chunk_chars,
            "char_used": 0,
            "remaining_candidates": 0,
            "note": None,
        }

    def lookup(self, question: str, limit: int | None = None, cursor: str | None = None) -> dict[str, Any]:
        if self._conn is None:
            raise PublicTextError("reader is closed")
        if not isinstance(question, str):
            raise PublicTextError("question must be a string")
        return self._stabilize(self._lookup_impl(question, limit, cursor), question)

    def _lookup_impl(
        self, question: str, limit: int | None = None, cursor: str | None = None
    ) -> dict[str, Any]:
        if self._conn is None:
            raise PublicTextError("reader is closed")
        if not isinstance(question, str):
            raise PublicTextError("question must be a string")
        question = question.strip()
        result = self._base_result(question)
        if not question:
            result["reason"] = "empty_question"
            result["text"] = "请提出一个关于职业玩法、精灵技能或副本攻略的问题。"
            return result
        question_norm = normalize(question)
        conditions = self._parse_conditions(question_norm)
        hint = self._intent_hint(question_norm)
        matches = self.index.match_entities(question_norm)
        world = self._match_world(question_norm)
        if not world and not matches and (hint == "dungeon" or conditions.any()):
            world, ambiguous_worlds = self._match_world_prefix(question_norm)
            if ambiguous_worlds:
                return self._clarify_world_abbrev(question, conditions, ambiguous_worlds, result)
        if world:
            conditions.world = world["name"]
        if hint in {"fairy", "profession"}:
            system_labels = self._system_labels(hint)
            if any(normalize(label) in question_norm for label in system_labels):
                system_result = self._lookup_system(
                    question, question_norm, hint, conditions, result, limit, cursor
                )
                if system_result is not None:
                    return system_result
        if not matches:
            if hint == "fairy" and any(term in question_norm for term in _NON_FAIRY_ITEM_TERMS):
                result["reason"] = "out_of_scope"
                result["text"] = "该问题属于道具或材料信息，不在本组件的职业玩法、精灵技能、副本攻略范围内。"
                return result
            if "宝石" in question_norm and not any(term in question_norm for term in ("副本", "秘境")):
                return self._lookup_gem(question, question_norm, conditions, result)
            if world and (hint == "dungeon" or not hint):
                return self._lookup_world(question, question_norm, world, conditions, result, limit, cursor)
            if hint:
                return self._lookup_generic(question, question_norm, hint, conditions, result)
            result["text"] = "这个问题不在公开正文的检索范围内。"
            return result
        if hint == "gem" and any(term in question_norm for term in ("副本", "秘境", "掉落", "怎么打")):
            hint = "dungeon"
        return self._lookup_entity(question, question_norm, hint, matches, conditions, result, limit, cursor)

    def _requested_intent(self, result: dict[str, Any], question_norm: str) -> str:
        domain = result["intent"].get("domain")
        if not result["matched"] or domain in {None, "out_of_scope"}:
            return "none"
        entity = result["intent"].get("entity")
        if entity:
            question_norm = question_norm.replace(normalize(entity), "")
        if domain == "gem":
            return result["intent"].get("mode") or "overview"
        if domain == "dungeon":
            if any(term in question_norm for term in ("怎么打", "打法", "攻略", "机制", "技能")):
                return "guide"
            if self._is_list_question(question_norm):
                return "list"
            if any(term in question_norm for term in ("是什么", "是啥", "定义", "介绍")):
                return "intro"
            return "general"
        if domain == "fairy":
            if result["intent"].get("system"):
                return "system"
            if any(term in question_norm for term in ("技能", "连招", "加点", "输出")):
                return "skills"
            if any(term in question_norm for term in ("培养", "升星", "面板", "装备", "礼物")):
                return "growth"
            return "general"
        if domain == "profession":
            if any(term in question_norm for term in ("技能", "连招", "加点", "输出")):
                return "skills"
            if any(term in question_norm for term in ("怎么玩", "玩法", "机制", "攻略")):
                return "guide"
            return "general"
        return "general"

    def _stabilize(self, result: dict[str, Any], question: str) -> dict[str, Any]:
        question = question.strip()
        result["question"] = question
        handler = result.get("route")
        if not isinstance(handler, str):
            handler = (handler or {}).get("handler", "none")
        domain = result["intent"].get("domain")
        requested_intent = self._requested_intent(result, normalize(question))
        family: str | None
        if result["reason"] == "entity_type_mismatch":
            family = None
        elif domain == "profession":
            family = "profession_guide"
        elif domain == "fairy":
            family = "elf_system" if result["intent"].get("system") else "elf_skills"
        elif domain == "dungeon":
            family = "dungeon_guide" if requested_intent == "guide" else "dungeon_intro"
        elif domain == "gem":
            family = {
                "upgrade": "gem_upgrade",
                "cut": "gem_cut",
                "inlay": "gem_inlay",
                "effect": "gem_effect",
                "source": "gem_source",
            }.get(result["intent"].get("mode"), "gem_overview")
        else:
            family = None
        if not result["matched"]:
            family = None
        scope = "non_game" if not result["matched"] else "game"
        result["scope"] = scope
        entity_type = {"fairy": "elf"}.get(domain, domain)
        if result["intent"].get("system"):
            entity_type = "elf_system"
        result["entity_type"] = entity_type if domain in {"profession", "fairy", "dungeon", "gem"} else None
        result["entity"] = result["intent"].get("entity")
        result["conditions"] = dict(result["intent"].get("conditions") or {})
        result["requested_intent"] = requested_intent
        result["route"] = {
            "handler": handler,
            "intent_family": family,
            "scope": scope,
            "subject": result["entity"],
            "entity_type": result["entity_type"],
            "conditions": result["conditions"],
        }
        return result

    @staticmethod
    def _parse_conditions(question_norm: str) -> _Conditions:
        players = None
        player_match = re.search(r"(\d{1,2}|[一二三四五六七八九十两]{1,3})\s*人", question_norm)
        if player_match:
            players = _cn_to_int(player_match.group(1))
        elif "单人" in question_norm:
            players = 1
        elif "多人" in question_norm:
            players = None
        difficulty = None
        if "精英" in question_norm:
            difficulty = "精英"
        elif "普通" in question_norm:
            difficulty = "普通"
        layer = _parse_layer(question_norm)
        tier_match = re.search(r"T(\d+)", question_norm, flags=re.IGNORECASE)
        return _Conditions(
            players=players,
            difficulty=difficulty,
            layer=layer,
            tier=f"T{tier_match.group(1)}" if tier_match else None,
        )

    @staticmethod
    def _intent_hint(question_norm: str) -> str | None:
        if "宝石" in question_norm:
            return "gem"
        if "精灵" in question_norm:
            return "fairy"
        if any(term in question_norm for term in ("副本", "秘境", "怎么打", "打法", "掉落", "入口", "坐标", "几人", "几个人", "人数", "层")):
            return "dungeon"
        if any(term in question_norm for term in ("职业", "玩法", "怎么玩", "连招", "加点", "输出手法", "机制")):
            return "profession"
        return None

    def _match_world(self, question_norm: str) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        best_len = 0
        for norm, entry in self.index.worlds.items():
            if len(norm) > best_len and norm in question_norm:
                best = entry
                best_len = len(norm)
        return best

    def _match_world_prefix(
        self, question_norm: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        candidates: dict[int, dict[str, dict[str, Any]]] = {}
        for norm, entry in self.index.worlds.items():
            for length in range(len(norm) - 1, 1, -1):
                prefix = norm[:length]
                if prefix in question_norm:
                    candidates.setdefault(length, {})[entry["name"]] = entry
                    break
        if not candidates:
            return None, []
        longest = max(candidates)
        entries = list(candidates[longest].values())
        if len(entries) > 1:
            return None, entries
        return entries[0], []

    def _domain_label(self, domain: str) -> str:
        return {"profession": "职业", "fairy": "精灵", "dungeon": "副本", "gem": "宝石"}.get(domain, domain)

    def _expand_domain_matches(self, domain: str, name: str) -> list[tuple[int, float, _EntityRef]]:
        base = normalize(name)
        if len(base) < 2:
            return []
        expanded: dict[tuple[str, str], tuple[int, float, _EntityRef]] = {}
        for norm, domain_map in self.index.entities.items():
            ref = domain_map.get(domain)
            if ref is None or norm == base:
                continue
            if norm.startswith(base):
                key = (ref.name, ref.domain)
                expanded[key] = (len(norm), ref.weight, ref)
        if not expanded:
            return []
        longest = max(item[0] for item in expanded.values())
        return [item for item in expanded.values() if item[0] == longest]

    def _system_labels(self, domain: str) -> dict[str, set[str]]:
        labels: dict[str, set[str]] = {}
        for chunk in self.index.chunks:
            if chunk.domain != domain or chunk.labels:
                continue
            for part in chunk.parts:
                match = re.match(r"^(\d+(?:\.\d+)+)\s*(.+)$", part.strip())
                if not match:
                    continue
                code, label = match.groups()
                if code.count(".") != 1:
                    continue
                label = label.strip()
                if len(label) < 2 or label in _GENERIC_LABELS:
                    continue
                labels.setdefault(label, set()).add(code)
        return labels

    def _system_candidates(self, domain: str, codes: set[str]) -> list[_Chunk]:
        candidates: list[_Chunk] = []
        for chunk in self.index.chunks:
            if chunk.domain != domain or chunk.labels:
                continue
            for part in chunk.parts:
                match = re.match(r"^(\d+(?:\.\d+)+)\s*", part.strip())
                if not match:
                    continue
                code = match.group(1)
                if any(code == prefix or code.startswith(prefix + ".") for prefix in codes):
                    candidates.append(chunk)
                    break
        return candidates

    def _system_intent(
        self, domain: str, label: str, conditions: _Conditions
    ) -> dict[str, Any]:
        return {
            "domain": domain,
            "entity": label,
            "conditions": self._conditions_payload(conditions),
            "branch": None,
            "system": label,
        }

    def _system_missing(
        self,
        result: dict[str, Any],
        domain: str,
        label: str,
        topic: str | None,
        chunks: list[_Chunk],
    ) -> dict[str, Any]:
        detail = _SYSTEM_MISSING_LABELS.get(topic or "", "对应正文")
        return self._abstain(
            result,
            True,
            "not_in_public_snapshot",
            f"public_text.{domain}",
            f"本公开快照的「{label}」章节没有{detail}；已检查该章节的实际段落，未用其他章节代答。",
            unknown_kind="not_in_public_snapshot",
            unknown_detail=f"公开快照的「{label}」章节未收录{detail}。",
            unknown_chunks=chunks,
            unknown_scope_note=f"检查了公开快照中「{label}」章节的段落与子章节标题。",
        )

    def _system_historical(
        self,
        result: dict[str, Any],
        domain: str,
        label: str,
        segments: list[_SectionSegment],
    ) -> dict[str, Any]:
        titles = "、".join(
            dict.fromkeys(segment.title.split(" / ")[0] for segment in segments)
        )
        return self._abstain(
            result,
            True,
            "only_historical_or_unreleased",
            f"public_text.{domain}",
            f"公开正文的「{label}·{titles}」内容全部带删除线（历史/旧值），"
            "没有可用的当前内容；不把删除线数值当作当前规则。",
            unknown_kind="historical_source",
            unknown_detail=f"「{label}·{titles}」在公开正文中仅保留删除线历史内容，已排除。",
            unknown_type="source_boundary",
            unknown_chunks=[segment.chunk for segment in segments],
            unknown_scope_note="检查了该子章节的当前文本与删除线标记，当前内容为空。",
        )

    def _system_overview_text(
        self,
        label: str,
        topic: str | None,
        bonus_segments: list[_SectionSegment],
        set_segments: list[_SectionSegment],
    ) -> str | None:
        by_slot: dict[str, list[str]] = {}
        has_empty = False
        for segment in bonus_segments:
            slot = _system_slot(segment) or "属性"
            for name, _values in _system_bonus_groups(segment.raw):
                if name == "无":
                    has_empty = True
                    continue
                labels = by_slot.setdefault(slot, [])
                if name not in labels:
                    labels.append(name)
        sets = _system_set_rows(set_segments)
        lines: list[str] = []
        if by_slot:
            lines.append(f"「{label}」是公开正文中的属性与套装加成内容。")
            lines.append("公开正文按部位列出属性字段；未说明具体作用对象或结算方式。")
            condition = _system_bonus_condition(bonus_segments)
            if condition:
                lines.append(f"主要限制：{condition}。")
            lines.append("部位与表头列出的属性：")
            for slot, names in by_slot.items():
                lines.append(f"- {slot}：{'、'.join(names)}")
            if has_empty:
                lines.append("表头标注“无”的栏没有对应加成。")
        if topic == "set" and sets:
            lines.append("套装属性（按公开原文列出；删除线概率不计入）：")
            lines.extend(f"- {name}：{effect}" for name, effect in sets)
        elif sets:
            examples = "、".join(f"{name}（{effect}）" for name, effect in sets[:3])
            suffix = f"等 {len(sets)} 个套装档位" if len(sets) > 3 else ""
            lines.append(f"相关套装：{examples}{suffix}。")
        if by_slot:
            lines.append("表内档位是装备等级，不是精灵等级；未问具体数值时不展开等级表。")
        if not lines:
            return None
        codes = sorted(
            {
                ".".join(segment.code.split(".")[:3])
                for segment in bonus_segments + set_segments
                if segment.code
            }
        )
        source_label = "、".join(codes) if codes else "对应"
        lines.append(f"以上名称和属性取自公开正文 {source_label} 子章节。")
        return "\n".join(lines)

    def _lookup_system(
        self,
        question: str,
        question_norm: str,
        hint: str,
        conditions: _Conditions,
        result: dict[str, Any],
        limit: int | None,
        cursor: str | None,
    ) -> dict[str, Any] | None:
        labels = self._system_labels(hint)
        if not labels:
            return None
        matched_label = None
        for label in sorted(labels, key=len, reverse=True):
            if normalize(label) and normalize(label) in question_norm:
                matched_label = label
                break
        if matched_label is None:
            return None
        candidates = self._system_candidates(hint, labels[matched_label])
        if not candidates:
            return None
        segments = [segment for chunk in candidates for segment in _section_segments(chunk)]
        if not segments:
            return None
        topic = _system_query_topic(question_norm)
        rest = question_norm.replace(normalize(matched_label), "")
        leftover = _leftover(rest)
        if topic is None and leftover:
            return None
        detail = _system_detail_requested(question_norm)
        levels = _system_level_filter(question_norm) if detail else None
        bonus_segments = [
            segment for segment in segments if _segment_topic(segment.title) == "bonus"
        ]
        set_segments = [
            segment for segment in segments if _segment_topic(segment.title) == "set"
        ]
        all_bonus_labels = {
            name
            for segment in bonus_segments
            for name, _values in _system_bonus_groups(segment.raw)
            if name != "无"
        }
        wanted_labels = {
            name
            for name in all_bonus_labels
            if len(name) >= 2 and normalize(name) in question_norm
        }
        if topic in {"parts", "bonus"}:
            selected = bonus_segments if detail else bonus_segments + set_segments
        elif topic == "set":
            selected = set_segments
        elif topic == "overview" or topic is None:
            selected = bonus_segments if detail else bonus_segments + set_segments
        else:
            selected = [segment for segment in segments if _segment_topic(segment.title) == topic]
        specific = [
            term for term in ("发饰", "项链", "戒指") if term in question_norm
        ]
        if specific and topic in {"parts", "bonus", None}:
            refined = [
                segment
                for segment in bonus_segments
                if any(
                    term in segment.title
                    or term in segment.readable
                    or term in segment.chunk.leaf_raw
                    for term in specific
                )
            ]
            if refined:
                selected = refined + (
                    [] if detail else [segment for segment in selected if segment in set_segments]
                )
        if not selected:
            result["intent"] = self._system_intent(hint, matched_label, conditions)
            return self._system_missing(result, hint, matched_label, topic, candidates)
        selected = [
            segment for segment in selected if segment.readable or segment.historical
        ]
        if not selected:
            result["intent"] = self._system_intent(hint, matched_label, conditions)
            return self._system_missing(result, hint, matched_label, topic, candidates)
        current = [
            segment for segment in selected if segment.readable and not segment.historical
        ]
        historical = [
            segment for segment in selected if segment.historical and not segment.readable
        ]
        result["intent"] = self._system_intent(hint, matched_label, conditions)
        if not current:
            if historical:
                return self._system_historical(result, hint, matched_label, historical)
            return self._system_missing(result, hint, matched_label, topic, candidates)
        overview = None
        if not detail and topic in {"overview", "bonus", "parts", "set", None}:
            overview = self._system_overview_text(
                matched_label,
                topic,
                [segment for segment in current if _segment_topic(segment.title) == "bonus"],
                [segment for segment in current if _segment_topic(segment.title) == "set"],
            )
        ranked = [
            replace(
                segment.chunk,
                readable=(
                    _render_system_segment(segment, levels, wanted_labels or None)
                    if detail
                    else _system_overview_source(segment)
                    if overview
                    else _render_system_segment(segment)
                ),
                had_strikethrough=segment.had_strikethrough,
                historical=False,
                branch_label=f"{segment.code} {segment.title.split(' / ')[0]}".strip(),
                leaf_raw=segment.title,
            )
            for segment in current
        ]
        page_limit = limit if limit is not None else (4 if overview else None)
        answer = self._answer_from_chunks(
            question,
            hint,
            matched_label,
            ranked,
            result,
            page_limit,
            cursor,
            multi=False,
            conditions=None,
            text_override=overview,
        )
        if answer.get("evidence"):
            quotes = {segment.chunk.chunk_id: segment.raw.strip() for segment in current}
            for item in answer["evidence"]:
                quote = quotes.get(item["chunk_id"])
                if quote:
                    item["quote"] = quote
        if historical and answer.get("action") == "answer_with_caveat":
            answer["limits"]["source_processing"].append(
                {
                    "record_type": "execution_note",
                    "kind": "historical_subsection_excluded",
                    "detail": "同章节中仅含删除线/历史内容的子段未纳入当前回答。",
                    "chunk_ids": list(
                        dict.fromkeys(segment.chunk.chunk_id for segment in historical)
                    ),
                }
            )
        return answer

    def _lookup_entity(
        self,
        question: str,
        question_norm: str,
        hint: str | None,
        matches: list[tuple[int, float, _EntityRef]],
        conditions: _Conditions,
        result: dict[str, Any],
        limit: int | None,
        cursor: str | None,
    ) -> dict[str, Any]:
        top_length = matches[0][0]
        top = [item for item in matches if item[0] == top_length]
        names = {(item[2].name, item[2].domain) for item in top}
        domains = {item[2].domain for item in top}
        if hint:
            hinted = [item for item in matches if item[2].domain == hint]
            if not hinted:
                expanded = self._expand_domain_matches(hint, top[0][2].name)
                if expanded:
                    hinted = expanded
                else:
                    actual = sorted(domains)[0] if domains else "other"
                    return self._type_mismatch(
                        question,
                        top[0][2].name,
                        hint,
                        actual,
                        result,
                        [self.index.chunks[position] for _, _, ref in top for position in ref.positions],
                    )
            selected_refs = [item[2] for item in hinted if item[0] == max(x[0] for x in hinted)]
            domain = hint
        else:
            if len({name for name, _ in names}) > 1:
                return self._clarify_entities(question, top, result)
            if len(domains) > 1:
                if "技能" in question_norm and "fairy" in domains:
                    domain = "fairy"
                elif any(term in question_norm for term in ("怎么打", "打法", "副本", "秘境")) and "dungeon" in domains:
                    domain = "dungeon"
                elif any(term in question_norm for term in ("怎么玩", "玩法", "职业")) and "profession" in domains:
                    domain = "profession"
                else:
                    return self._clarify_types(question, top, result)
                selected_refs = [item[2] for item in top if item[2].domain == domain]
            else:
                domain = next(iter(domains))
                selected_refs = [item[2] for item in top]
        if len({(ref.name, ref.domain) for ref in selected_refs}) > 1:
            return self._clarify_entities(question, hinted if hint else top, result)
        entity = selected_refs[0].name
        result["intent"] = {
            "domain": domain,
            "entity": entity,
            "conditions": {
                "players": conditions.players,
                "difficulty": conditions.difficulty,
                "layer": conditions.layer,
                "world": conditions.world,
                "tier": conditions.tier,
            },
            "branch": None,
        }
        if domain == "dungeon":
            return self._lookup_dungeon_entity(question, question_norm, selected_refs, conditions, result, limit, cursor)
        if domain in {"profession", "fairy"}:
            return self._lookup_text_entity(question, question_norm, domain, entity, selected_refs, result, limit, cursor)
        return self._abstain(result, True, "entity_type_mismatch", f"public_text.{domain}", "该条目不属于公开正文的正文检索范围。")

    def _type_mismatch(
        self,
        question: str,
        entity: str,
        requested: str,
        actual: str,
        result: dict[str, Any],
        chunks: list[_Chunk],
    ) -> dict[str, Any]:
        requested_label = self._domain_label(requested)
        actual_label = self._domain_label(actual)
        result["matched"] = True
        result["reason"] = "entity_type_mismatch"
        result["route"] = f"public_text.{requested}"
        result["intent"]["domain"] = requested
        result["intent"]["entity"] = entity
        result["buckets"]["unknown"].append(
            self._unknown_record(
                "type_mismatch",
                f"公开正文中的同名条目属于{actual_label}，不是{requested_label}；未跨类型代答。",
                record_type="source_boundary",
                chunks=chunks,
                checked="检查了同名公开条目在索引中的实际正文类型。",
            )
        )
        result["text"] = (
            f"公开正文中的「{entity}」是{actual_label}条目，"
            f"没有对应的{requested_label}资料。本组件不把其他类型的内容当作答案。"
        )
        return result

    def _clarify_types(self, question: str, top: list[tuple[int, float, _EntityRef]], result: dict[str, Any]) -> dict[str, Any]:
        options = []
        seen = set()
        for _, _, ref in top:
            label = f"{self._domain_label(ref.domain)}：{ref.name}"
            if label not in seen:
                seen.add(label)
                options.append(label)
        prompt = f"「{top[0][2].name}」在公开正文中有多种类型，请先确认你问的是哪一种。"
        return self._clarify(result, "ambiguous_type", "public_text.clarify.type", prompt, options)

    def _clarify_entities(self, question: str, top: list[tuple[int, float, _EntityRef]], result: dict[str, Any]) -> dict[str, Any]:
        options = []
        seen = set()
        for _, _, ref in top:
            label = f"{self._domain_label(ref.domain)}：{ref.name}"
            if label not in seen:
                seen.add(label)
                options.append(label)
        prompt = "一次只能定位一个公开正文条目，请从下面的实际条目中选择。"
        return self._clarify(result, "ambiguous_entity", "public_text.clarify.entity", prompt, options)

    def _lookup_text_entity(
        self,
        question: str,
        question_norm: str,
        domain: str,
        entity: str,
        refs: list[_EntityRef],
        result: dict[str, Any],
        limit: int | None,
        cursor: str | None,
    ) -> dict[str, Any]:
        positions = sorted({position for ref in refs for position in ref.positions})
        chunks = [self.index.chunks[position] for position in positions]
        chunks = [chunk for chunk in chunks if chunk.domain == domain and chunk.readable and not chunk.historical]
        entity_norm = normalize(entity)
        focused = [chunk for chunk in chunks if entity_norm and entity_norm in normalize(chunk.leaf_raw)]
        if focused:
            chunks = focused
        if not chunks:
            return self._abstain(
                result,
                True,
                "entity_not_in_public_text",
                f"public_text.{domain}",
                f"公开正文中没有找到「{entity}」的可用当前段落；没有用相似条目替代。",
            )
        intent = self._intent_name(question_norm)
        ranked = self._rank_chunks(chunks, intent, normalize(entity))
        return self._answer_from_chunks(question, domain, entity, ranked, result, limit, cursor, multi=False, conditions=None)

    def _lookup_dungeon_entity(
        self,
        question: str,
        question_norm: str,
        refs: list[_EntityRef],
        conditions: _Conditions,
        result: dict[str, Any],
        limit: int | None,
        cursor: str | None,
    ) -> dict[str, Any]:
        entity = refs[0].name
        positions = sorted({position for ref in refs for position in ref.positions})
        candidates = [self.index.chunks[position] for position in positions]
        candidates = [chunk for chunk in candidates if chunk.domain == "dungeon" and chunk.readable]
        current = [chunk for chunk in candidates if not chunk.historical and not chunk.incomplete]
        if not current:
            if candidates:
                if all(chunk.historical for chunk in candidates):
                    return self._abstain(
                        result,
                        True,
                        "only_historical_or_unreleased",
                        "public_text.dungeon",
                        f"只找到「{entity}」标注未上线、猜测或历史的段落，不能当作当前攻略。",
                        unknown_kind="historical_source",
                        unknown_detail="相关内容在公开正文中标注为历史或未上线，已排除。",
                        unknown_type="source_boundary",
                        unknown_chunks=candidates,
                        unknown_scope_note="检查了该实体在公开快照中的当前/历史/未上线状态。",
                    )
                return self._abstain(
                    result,
                    True,
                    "only_incomplete_or_unreleased",
                    "public_text.dungeon",
                    f"只找到「{entity}」标注未完成、未上线、猜测或历史的段落，不能当作当前攻略。",
                    unknown_kind="incomplete_source",
                    unknown_detail="相关内容在公开正文中标注未完成或未上线，已排除。",
                    unknown_type="source_boundary",
                    unknown_chunks=candidates,
                    unknown_scope_note="检查了该实体在公开快照中的当前/未完成/未上线状态。",
                )
            return self._abstain(
                result,
                True,
                "only_historical_or_unreleased",
                "public_text.dungeon",
                f"只找到「{entity}」标注未上线、猜测或历史的段落，不能当作当前攻略。",
            )
        chunks = current
        groups: dict[tuple[Any, ...], list[_Chunk]] = {}
        for chunk in chunks:
            groups.setdefault(chunk.branch_key, []).append(chunk)
        filtered = self._filter_groups(groups, conditions)
        if not filtered:
            if conditions.any():
                return self._abstain(
                    result,
                    True,
                    "no_branch_matching_conditions",
                    "public_text.dungeon",
                    f"公开正文中没有符合「{'、'.join(conditions.describe())}」条件的「{entity}」分支。",
                    unknown_kind="condition_mismatch",
                    unknown_detail="已按世界/人数/层/难度条件筛选，没有回退到其他分支。",
                    unknown_chunks=chunks,
                    unknown_scope_note="检查了候选公开入口的 world/players/layer/difficulty/tier 字段。",
                )
            return self._clarify_branches(result, entity, groups)
        if len(filtered) > 1:
            return self._clarify_branches(result, entity, filtered)
        group = next(iter(filtered.values()))
        intent = self._intent_name(question_norm)
        ranked = self._rank_chunks(group, intent, normalize(entity))
        return self._answer_from_chunks(question, "dungeon", entity, ranked, result, limit, cursor, multi=False, conditions=conditions)

    @staticmethod
    def _chunk_matches_conditions(chunk: _Chunk, conditions: _Conditions) -> bool:
        if conditions.players is not None and not _players_match(chunk.players, conditions.players):
            return False
        if conditions.difficulty and chunk.difficulty != conditions.difficulty:
            return False
        if conditions.world and normalize(chunk.world) != normalize(conditions.world):
            return False
        if conditions.tier and chunk.tier_tag != conditions.tier:
            return False
        if conditions.layer is not None:
            if chunk.layer is None or chunk.layer != conditions.layer:
                return False
        return True

    def _filter_groups(
        self, groups: dict[tuple[Any, ...], list[_Chunk]], conditions: _Conditions
    ) -> dict[tuple[Any, ...], list[_Chunk]]:
        selected: dict[tuple[Any, ...], list[_Chunk]] = {}
        for key, chunks in groups.items():
            if all(self._chunk_matches_conditions(chunk, conditions) for chunk in chunks):
                selected[key] = chunks
        return selected

    @staticmethod
    def _conditions_payload(conditions: _Conditions) -> dict[str, Any]:
        return {
            "players": conditions.players,
            "difficulty": conditions.difficulty,
            "layer": conditions.layer,
            "world": conditions.world,
            "tier": conditions.tier,
        }

    @staticmethod
    def _is_list_question(question_norm: str) -> bool:
        return any(term in question_norm for term in ("有哪些", "有什么", "哪些", "列表", "清单", "几个副本", "多少副本"))

    def _clarify_branches(self, result: dict[str, Any], entity: str, groups: dict[tuple[Any, ...], list[_Chunk]]) -> dict[str, Any]:
        labels = []
        for chunks in groups.values():
            label = chunks[0].branch_label
            if label not in labels:
                labels.append(label)
        options = labels[:6]
        prompt = (
            f"「{entity}」在公开正文中有多个分支，请先确认具体条件；"
            "不同人数、难度或层数的机制不能混在一起回答。"
        )
        if len(labels) > len(options):
            prompt += f"（共 {len(labels)} 个分支，下面只列出前 {len(options)} 个）"
        result["intent"]["branch"] = labels
        return self._clarify(result, "ambiguous_branch", "public_text.clarify.branch", prompt, options)

    def _clarify_world_abbrev(
        self,
        question: str,
        conditions: _Conditions,
        worlds: list[dict[str, Any]],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        names = [entry["name"] for entry in worlds]
        result["intent"] = {
            "domain": "dungeon",
            "entity": None,
            "conditions": self._conditions_payload(conditions),
            "branch": None,
        }
        prompt = (
            "这个简称对应公开正文中的多个世界，请确认具体世界后再按人数查副本入口。"
        )
        if len(names) > 6:
            prompt += f"（共 {len(names)} 个世界，下面只列出前 6 个）"
        return self._clarify(
            result,
            "ambiguous_world_abbrev",
            "public_text.clarify.world_abbrev",
            prompt,
            names[:6],
        )

    def _lookup_world(
        self,
        question: str,
        question_norm: str,
        world: dict[str, Any],
        conditions: _Conditions,
        result: dict[str, Any],
        limit: int | None,
        cursor: str | None,
    ) -> dict[str, Any]:
        world_name = world["name"]
        positions = sorted(world["positions"])
        chunks = [self.index.chunks[position] for position in positions]
        groups: dict[tuple[str, tuple[int, int] | None], list[_Chunk]] = {}
        for chunk in chunks:
            if not chunk.readable or chunk.historical or chunk.incomplete or chunk.domain != "dungeon":
                continue
            if not self._chunk_matches_conditions(chunk, conditions):
                continue
            groups.setdefault((chunk.section, chunk.players), []).append(chunk)
        if not groups:
            if conditions.any():
                return self._abstain(
                    result,
                    True,
                    "no_branch_matching_conditions",
                    "public_text.dungeon.world",
                    f"公开正文的「{world_name}」下没有符合「{'、'.join(conditions.describe())}」条件的入口。",
                    unknown_kind="condition_mismatch",
                    unknown_detail="已按世界/人数/层/难度条件筛选，没有回退到其他分支。",
                    unknown_chunks=chunks,
                    unknown_scope_note="检查了候选公开入口的 world/players/layer/difficulty/tier 字段。",
                )
            return self._abstain(
                result,
                True,
                "insufficient_public_text",
                "public_text.dungeon.world",
                f"公开正文没有「{world_name}」下可用的副本章节。",
            )
        labels: list[str] = []
        for group in groups.values():
            label = group[0].branch_label
            if label not in labels:
                labels.append(label)
        result["intent"] = {
            "domain": "dungeon",
            "entity": world_name,
            "conditions": self._conditions_payload(conditions),
            "branch": labels,
        }
        if len(groups) == 1:
            group = next(iter(groups.values()))
            ranked = self._rank_chunks(group, self._intent_name(question_norm), normalize(world_name))
            result["intent"]["branch"] = [group[0].branch_label]
            answer = self._answer_from_chunks(
                question,
                "dungeon",
                world_name,
                ranked,
                result,
                limit,
                cursor,
                multi=False,
                conditions=conditions,
            )
            if answer.get("route") in {"public_text.dungeon", "public_text.dungeon.world"}:
                answer["route"] = "public_text.dungeon.world"
            return answer
        collection_definition = (
            conditions.players is not None
            and any(term in question_norm for term in ("什么是", "是什么", "是啥", "指什么", "指的是"))
            and any(term in question_norm for term in ("人本", "副本", "秘境"))
        )
        if self._is_list_question(question_norm) or collection_definition:
            ranked = []
            for key in sorted(groups, key=lambda item: (item[0], item[1] or (0, 0))):
                group = groups[key]
                overview = [chunk for chunk in group if chunk.overview]
                pool = overview or group
                ranked.extend(self._rank_chunks(pool, "definition", normalize(world_name))[:2])
            return self._answer_from_chunks(
                question,
                "dungeon",
                world_name,
                ranked,
                result,
                limit,
                cursor,
                multi=True,
                conditions=conditions,
            )
        options = labels[:6]
        prompt = f"「{world_name}」下有多个入口，公开正文按世界、人数和层数分章，请先确认具体副本。"
        if conditions.any():
            prompt += f"已应用条件：{'、'.join(conditions.describe())}。"
        if len(labels) > len(options):
            prompt += f"（共 {len(labels)} 个分支，下面只列出前 {len(options)} 个）"
        return self._clarify(result, "ambiguous_world", "public_text.clarify.world", prompt, options)

    def _lookup_generic(
        self,
        question: str,
        question_norm: str,
        hint: str,
        conditions: _Conditions,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        leftover = _leftover(question_norm)
        result["intent"]["domain"] = hint
        if hint == "gem":
            return self._lookup_gem(question, question_norm, conditions, result)
        if leftover:
            label = self._domain_label(hint)
            return self._abstain(
                result,
                True,
                "entity_not_in_public_text",
                f"public_text.{hint}",
                f"已识别为{label}检索，但公开正文中没有找到「{leftover}」的对应条目；没有用相似条目替代。",
            )
        if hint == "profession":
            all_options = self.index.profession_names()
            options = all_options[:8]
            prompt = "公开正文按职业分章，请先确认具体职业。"
        elif hint == "fairy":
            all_options = self.index.fairy_names()
            options = all_options[:8]
            prompt = "公开正文按精灵分章，请先确认具体精灵。"
        elif hint == "dungeon":
            all_options = self.index.dungeon_names()
            options = all_options[:8]
            prompt = "公开正文按副本分章，请先确认具体副本；同名首领不能代替副本入口。"
        else:
            all_options = []
            options = []
            prompt = "请先确认具体条目。"
        if not options:
            return self._abstain(
                result,
                True,
                "insufficient_public_text",
                f"public_text.{hint}",
                f"公开正文中没有可用的{self._domain_label(hint)}条目。",
            )
        if len(all_options) > len(options):
            prompt += f"（共 {len(all_options)} 个条目，下面只列出前 {len(options)} 个）"
        return self._clarify(result, "clarify_entity", f"public_text.clarify.{hint}", prompt, options)

    @staticmethod
    def _gem_conditions(question_norm: str) -> dict[str, Any]:
        shape = next((item for item in ("三角", "四角", "五角") if item in question_norm), None)
        stage = None
        if "溯时" in question_norm:
            stage = "命运升级为溯时"
        elif "命运" in question_norm:
            stage = "幻光升级为命运"
        elif "幻光" in question_norm:
            stage = "普通升级为幻光"
        conditions: dict[str, Any] = {}
        if shape:
            conditions["gem_shape"] = shape
        if stage:
            conditions["gem_stage"] = stage
        return conditions

    def _gem_missing(
        self,
        result: dict[str, Any],
        question_norm: str,
        mode: str,
        entity: str,
        topic_label: str,
        chunks: list[_Chunk],
    ) -> dict[str, Any]:
        result["intent"] = {
            "domain": "gem",
            "entity": entity,
            "conditions": self._gem_conditions(question_norm),
            "branch": None,
            "mode": mode,
        }
        note = (
            "这只说明本公开快照未收录，不代表游戏没有该机制。"
            if mode == "inlay"
            else ""
        )
        return self._abstain(
            result,
            True,
            "not_in_public_snapshot",
            "public_text.gem",
            f"本公开快照的 2.6 宝石章节没有{topic_label}；"
            "已检查效果、升级、切割等实际段落，未用其他表冒充。" + note,
            unknown_kind="not_in_public_snapshot",
            unknown_detail=f"公开快照的 2.6 宝石章节未收录{topic_label}。",
            unknown_chunks=chunks,
            unknown_scope_note="检查了公开快照 2.6 宝石章节的实际段落与子章节标题（效果、升级、切割等）。",
        )

    def _gem_answer(
        self,
        question: str,
        question_norm: str,
        entity: str,
        mode: str,
        selected: list[_SectionSegment],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        gem_conditions = self._gem_conditions(question_norm)
        result["intent"] = {
            "domain": "gem",
            "entity": entity,
            "conditions": gem_conditions,
            "branch": None,
            "mode": mode,
        }
        current = [segment for segment in selected if segment.readable and not segment.historical]
        historical = [segment for segment in selected if segment.historical and not segment.readable]
        if not current:
            if historical:
                titles = "、".join(
                    dict.fromkeys(
                        segment.title.split(" / ")[0] for segment in historical
                    )
                )
                return self._abstain(
                    result,
                    True,
                    "only_historical_or_unreleased",
                    "public_text.gem",
                    f"公开正文的「2.6 宝石·{titles}」内容全部带删除线（历史/旧值），"
                    "没有可用的当前内容；不把删除线数值当作当前规则。",
                    unknown_kind="historical_source",
                    unknown_detail=f"「2.6 宝石·{titles}」在公开正文中仅保留删除线历史内容，已排除。",
                    unknown_type="source_boundary",
                    unknown_chunks=[segment.chunk for segment in historical],
                    unknown_scope_note="检查了 2.6 宝石子章节的当前文本与删除线标记，当前内容为空。",
                )
            return self._abstain(
                result,
                True,
                "not_in_public_snapshot",
                "public_text.gem",
                "本公开快照的 2.6 宝石章节没有可用的当前正文。",
            )
        ranked = [
            replace(
                segment.chunk,
                readable=(
                    _gem_upgrade_text(segment, gem_conditions)
                    if mode == "upgrade"
                    else segment.readable
                ),
                had_strikethrough=segment.had_strikethrough,
                historical=False,
                branch_label=f"{segment.code} {segment.title.split(' / ')[0]}".strip(),
                leaf_raw=segment.title,
            )
            for segment in current
        ]
        answer = self._answer_from_chunks(
            question,
            "gem",
            entity,
            ranked,
            result,
            None,
            None,
            multi=False,
            conditions=None,
        )
        if mode == "upgrade" and answer.get("evidence"):
            quotes = {segment.chunk.chunk_id: segment.raw.strip() for segment in current}
            for item in answer["evidence"]:
                quote = quotes.get(item["chunk_id"])
                if quote:
                    item["quote"] = quote
        conditions = result["intent"].get("conditions") or {}
        labels = [
            conditions[key]
            for key in ("gem_shape", "gem_stage")
            if conditions.get(key)
        ]
        if labels and answer.get("action") == "answer_with_caveat":
            answer["text"] += "\n\n已识别条件：" + "、".join(labels) + "；摘录保留原文的档次/部位分栏。"
        if historical and answer.get("action") == "answer_with_caveat":
            answer["limits"]["source_processing"].append(
                {
                    "record_type": "execution_note",
                    "kind": "historical_subsection_excluded",
                    "detail": "同章节中仅含删除线/历史内容的子段未纳入当前回答。",
                    "chunk_ids": list(
                        dict.fromkeys(segment.chunk.chunk_id for segment in historical)
                    ),
                }
            )
        return answer

    def _lookup_gem(
        self,
        question: str,
        question_norm: str,
        conditions: _Conditions,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        result["intent"]["domain"] = "gem"
        all_chunks = [self.index.chunks[position] for position in self.index.gem_positions]
        segments = [segment for chunk in all_chunks for segment in _section_segments(chunk)]
        has_inlay = "镶嵌" in question_norm
        has_upgrade = "升级" in question_norm
        has_cut = "切割" in question_norm
        has_effect = any(
            term in question_norm for term in ("效果", "作用", "用处", "有什么用", "有啥用")
        )
        has_source = any(term in question_norm for term in ("获得", "获取", "出处", "哪里掉"))
        broad_action = any(term in question_norm for term in ("怎么打", "怎么玩", "怎么用", "攻略", "玩法"))
        if broad_action and not (has_inlay or has_upgrade or has_cut or has_effect or has_source):
            return self._clarify(
                result,
                "ambiguous_gem",
                "public_text.clarify.gem",
                "你指的是宝石镶嵌、升级、效果，还是切割？公开正文没有把它们混成同一张表。",
                ["宝石镶嵌", "宝石升级", "宝石效果", "宝石切割"],
            )
        if has_inlay:
            selected = [segment for segment in segments if "镶嵌" in segment.title]
            if not selected:
                return self._gem_missing(
                    result, question_norm, "inlay", "宝石镶嵌", "镶嵌规则或效果", all_chunks
                )
            return self._gem_answer(question, question_norm, "宝石镶嵌", "inlay", selected, result)
        if has_upgrade:
            selected = [segment for segment in segments if "升级" in segment.title]
            if not selected:
                return self._gem_missing(
                    result, question_norm, "upgrade", "宝石升级", "升级规则或材料", all_chunks
                )
            return self._gem_answer(question, question_norm, "宝石升级", "upgrade", selected, result)
        if has_cut:
            selected = [segment for segment in segments if "切割" in segment.title]
            if not selected:
                return self._gem_missing(
                    result, question_norm, "cut", "宝石切割", "切割规则或概率", all_chunks
                )
            return self._gem_answer(question, question_norm, "宝石切割", "cut", selected, result)
        if has_source:
            selected = [
                segment
                for segment in segments
                if any(term in segment.title for term in ("获取", "获得", "来源", "掉落", "出处"))
            ]
            if not selected:
                return self._gem_missing(
                    result, question_norm, "source", "宝石获取", "获取/来源说明", all_chunks
                )
            return self._gem_answer(question, question_norm, "宝石获取", "source", selected, result)
        if has_effect:
            leftover = _leftover(question_norm)
            effect_segments = [
                segment
                for segment in segments
                if _segment_topic(segment.title) == "effect"
                and segment.readable
                and not segment.historical
            ]
            picked: list[tuple[_SectionSegment, str]] = []
            for segment in effect_segments:
                if leftover:
                    selected_text = _gem_effect_row_text(segment.readable, leftover)
                else:
                    selected_text = segment.readable
                if selected_text:
                    picked.append((segment, selected_text))
            if not picked:
                result["intent"]["mode"] = "effect"
                return self._abstain(
                    result,
                    True,
                    "not_in_public_snapshot",
                    "public_text.gem",
                    f"公开正文的宝石效果章节没有找到「{leftover or '该宝石'}」的对应内容。",
                    unknown_kind="not_in_public_snapshot",
                    unknown_detail="具体宝石名称未在公开效果表中出现。",
                    unknown_chunks=all_chunks,
                    unknown_scope_note="检查了公开快照宝石效果章节中的宝石名称。",
                )
            ranked = [
                replace(
                    segment.chunk,
                    readable=selected_text,
                    had_strikethrough=segment.had_strikethrough,
                    historical=False,
                    branch_label=f"{segment.code} {segment.title.split(' / ')[0]}".strip(),
                    leaf_raw=segment.title,
                )
                for segment, selected_text in picked
            ]
            result["intent"]["entity"] = leftover or "宝石效果"
            result["intent"]["mode"] = "effect"
            answer = self._answer_from_chunks(question, "gem", leftover or "宝石效果", ranked, result, None, None, multi=False, conditions=None)
            if answer.get("evidence"):
                quotes = {segment.chunk.chunk_id: segment.raw.strip() for segment, _ in picked}
                for item in answer["evidence"]:
                    quote = quotes.get(item["chunk_id"])
                    if quote:
                        item["quote"] = quote
            return answer
        return self._clarify(
            result,
            "ambiguous_gem",
            "public_text.clarify.gem",
            "请先确认你要问宝石的哪一类信息：镶嵌、升级、效果，还是切割。",
            ["宝石镶嵌", "宝石升级", "宝石效果", "宝石切割"],
        )

    @staticmethod
    def _intent_name(question_norm: str) -> str:
        if any(term in question_norm for term in ("怎么打", "打法", "攻略", "机制")):
            return "strategy"
        if any(term in question_norm for term in ("掉落", "奖励")):
            return "drops"
        if any(term in question_norm for term in ("技能", "连招", "加点", "输出")):
            return "skills"
        if any(term in question_norm for term in ("是什么", "是啥", "定义", "介绍")):
            return "definition"
        return "general"

    def _rank_chunks(self, chunks: list[_Chunk], intent: str, entity_norm: str) -> list[_Chunk]:
        scored: list[tuple[float, _Chunk]] = []
        for chunk in chunks:
            if chunk.historical or not chunk.readable:
                continue
            score = 0.0
            if entity_norm and entity_norm in normalize(chunk.leaf_raw):
                score += 30
            if entity_norm and entity_norm in chunk.section_norm:
                score += 10
            if entity_norm and entity_norm in chunk.text_norm:
                score += 4
            if chunk.overview:
                score += 30
            if chunk.mechanics:
                score += 15
            if chunk.drops:
                score += 10
            if intent == "strategy":
                score += 35 if chunk.mechanics else 0
                score += 5 if chunk.overview else 0
            elif intent == "definition":
                score += 30 if chunk.overview else 0
            elif intent == "drops":
                score += 30 if chunk.drops else 0
            elif intent == "skills":
                score += 15 if chunk.mechanics else 0
            if chunk.domain == "fairy" and "6.2各精灵介绍" in chunk.section:
                score += 20
            if chunk.domain == "profession" and any(term in chunk.section for term in ("2.3.3", "2.3.4", "2.3.5")):
                score += 10
            if chunk.incomplete:
                score -= 5
            scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], item[1].section, item[1].chunk_id))
        return [chunk for _, chunk in scored]

    def _select_fairy_skills(self, text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        start = None
        for index, line in enumerate(lines):
            if "技能" not in line or "面板对照" in line:
                continue
            if any(term in line for term in ("技能数值", "技能介绍", "技能一览", "技能列表", "技能说明", "技能机制")) or line == "技能":
                start = index
                break
        if start is not None:
            return "\n".join(lines[start:])
        chosen = []
        for index, line in enumerate(lines):
            if "技能" in line or re.search(r"[（(]\s*\d+\s*[Ss秒]", line):
                chosen.append(index)
        if not chosen:
            return ""
        parts = []
        previous = None
        for index in chosen:
            if previous is not None and index > previous + 1:
                parts.append("…")
            parts.append(lines[index])
            previous = index
        return "\n".join(parts)

    def _select_profession(self, text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ""
        scores: dict[int, int] = {}
        group = 0
        tier_counts: dict[int, int] = {}
        for index, line in enumerate(lines):
            score = 0
            if index == 0:
                score = 10
            elif index == 1 and not line.startswith("附魔属性"):
                score = 5
            is_header = line.startswith("附魔属性")
            outside_parens = re.sub(r"[（(][^）)]*[）)]", "", line)
            header_name = re.split(r"[：:]", outside_parens, maxsplit=1)[0]
            is_tier = bool(re.match(r"^[^：:\s]{1,10}[ⅠⅡⅢⅣⅤ]", line)) or (
                is_header and bool(re.search(r"\d", header_name))
            )
            if is_header and not is_tier:
                group += 1
                score = max(score, 6)
            if is_tier:
                tier_counts[group] = tier_counts.get(group, 0) + 1
                score = 4 if tier_counts[group] <= 2 else 1
            if any(marker in line for marker in _PROFESSION_CORE_MARKERS):
                score = max(score, 8)
                if is_tier and tier_counts.get(group, 0) > 2:
                    score = 1
            if score >= 3:
                scores[index] = score
        if not scores:
            return ""
        keep = set(sorted(scores, key=lambda index: (-scores[index], index))[:24])
        chosen = sorted(keep)
        parts = []
        previous = None
        for index in chosen:
            if previous is not None and index > previous + 1:
                parts.append("…")
            parts.append(lines[index])
            previous = index
        if chosen and chosen[-1] < len(lines) - 1:
            parts.append("…")
        return "\n".join(parts)

    def _select_dungeon_overview(self, text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines[:3])

    def _select_dungeon_guide(self, text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        start = None
        for index, line in enumerate(lines):
            if any(marker in line for marker in _GUIDE_MARKERS):
                start = index
                break
        if start is not None:
            return "\n".join(lines[start:])
        chosen = [
            index
            for index, line in enumerate(lines)
            if any(marker in line for marker in _GUIDE_MARKERS)
        ]
        if not chosen:
            return ""
        parts = []
        previous = None
        for index in chosen:
            if previous is not None and index > previous + 1:
                parts.append("…")
            parts.append(lines[index])
            previous = index
        return "\n".join(parts)

    def _chunk_has_guide(self, chunk: _Chunk) -> bool:
        return bool(self._select_dungeon_guide(chunk.readable))

    def _select_relevant(self, chunk: _Chunk, intent: str) -> str:
        if chunk.domain == "fairy" and intent == "skills":
            return self._select_fairy_skills(chunk.readable)
        if chunk.domain == "profession" and intent in {"strategy", "general", "skills"}:
            return self._select_profession(chunk.readable)
        if chunk.domain == "dungeon" and intent == "list":
            return self._select_dungeon_overview(chunk.readable)
        if chunk.domain == "dungeon" and intent in {"strategy", "skills"}:
            return self._select_dungeon_guide(chunk.readable)
        return chunk.readable

    @staticmethod
    def _split_segments(text: str, budget: int) -> list[str]:
        if not text:
            return []
        lines = text.splitlines()
        segments: list[str] = []
        current: list[str] = []
        used = 0
        for line in lines:
            if len(line) > budget:
                if current:
                    segments.append("\n".join(current))
                    current = []
                    used = 0
                for start in range(0, len(line), budget):
                    segments.append(line[start : start + budget])
                continue
            extra = len(line) + (1 if current else 0)
            if current and used + extra > budget:
                segments.append("\n".join(current))
                current = [line]
                used = len(line)
            else:
                current.append(line)
                used += extra
        if current:
            segments.append("\n".join(current))
        return segments

    def _build_units(self, chunks: list[_Chunk], intent: str, budget: int) -> list[_ExcerptUnit]:
        units: list[_ExcerptUnit] = []
        for chunk in chunks:
            selected = self._select_relevant(chunk, intent)
            if not selected:
                continue
            segments = self._split_segments(selected, budget)
            for index, segment in enumerate(segments):
                truncated = index < len(segments) - 1
                excerpt = segment + ("…" if truncated else "")
                units.append(_ExcerptUnit(chunk=chunk, excerpt=excerpt, truncated=truncated))
        return units

    def _answer_from_chunks(
        self,
        question: str,
        domain: str,
        entity: str,
        ranked: list[_Chunk],
        result: dict[str, Any],
        limit: int | None,
        cursor: str | None,
        *,
        multi: bool,
        conditions: _Conditions | None,
        text_override: str | None = None,
    ) -> dict[str, Any]:
        if not ranked:
            return self._abstain(
                result,
                True,
                "insufficient_public_text",
                f"public_text.{domain}",
                f"公开正文没有找到「{entity}」的可用当前段落。",
            )
        intent_norm = normalize(question)
        if entity:
            intent_norm = intent_norm.replace(normalize(entity), "")
        intent = self._intent_name(intent_norm)
        if domain == "dungeon" and intent == "skills":
            intent = "strategy"
        if domain == "dungeon" and intent != "strategy" and (
            multi or self._is_list_question(normalize(question))
        ):
            intent = "list"
            overviews = [chunk for chunk in ranked if chunk.overview]
            if overviews:
                ranked = overviews
        if domain == "dungeon" and intent == "strategy" and not any(
            self._chunk_has_guide(chunk) for chunk in ranked
        ):
            return self._abstain(
                result,
                True,
                "no_strategy_in_public_text",
                f"public_text.{domain}",
                f"公开正文里「{entity}」只有入口、人数或属性面板，没有可用的攻略/机制正文；"
                "不把入口表或生命/攻击表当作完整打法。",
                unknown_kind="missing_strategy",
                unknown_detail="公开正文未收录该副本的机制或攻略段落。",
                unknown_chunks=ranked,
                unknown_scope_note="检查了该副本候选段落是否包含阶段/机制/攻略标记。",
            )
        offset = 0
        if cursor:
            try:
                offset = max(0, int(cursor))
            except ValueError as exc:
                raise PublicTextError("cursor must be an integer offset") from exc
        page_limit = self.max_evidence if limit is None else max(1, min(int(limit), 20))
        per_chunk = MULTI_PER_CHUNK_CHARS if multi else self.per_chunk_chars
        units = self._build_units(ranked, intent, per_chunk)
        if not units:
            return self._abstain(
                result,
                True,
                "insufficient_public_text",
                f"public_text.{domain}",
                f"公开正文中的「{entity}」段落为空或不可读。",
            )
        total = len(units)
        page: list[_ExcerptUnit] = []
        estimated = 0
        index = offset
        while index < total and len(page) < page_limit:
            unit = units[index]
            if page and estimated + len(unit.excerpt) > self.total_chars:
                break
            page.append(unit)
            estimated += len(unit.excerpt)
            index += 1
        if not page:
            result["matched"] = True
            result["action"] = "answer_with_caveat"
            result["reason"] = "no_more_pages"
            result["route"] = f"public_text.{domain}"
            result["text"] = "没有更多可展示的公开正文段落。"
            result["pagination"] = {
                "limit": page_limit,
                "returned": 0,
                "total_candidates": total,
                "has_more": False,
                "next_cursor": None,
                "truncated": False,
                "char_budget": self.total_chars,
                "per_chunk_budget": per_chunk,
                "char_used": 0,
                "remaining_candidates": 0,
                "note": "没有更多可展示的公开正文段落。",
            }
            return result
        evidence: list[dict[str, Any]] = []
        used = 0
        for unit in page:
            excerpt = unit.excerpt
            if not excerpt:
                continue
            used += len(excerpt)
            evidence.append(self._evidence(unit.chunk, excerpt, unit.truncated))
        if not evidence:
            return self._abstain(
                result,
                True,
                "insufficient_public_text",
                f"public_text.{domain}",
                f"公开正文中的「{entity}」段落为空或不可读。",
            )
        page_end = offset + len(page)
        has_more = page_end < total
        next_cursor = str(page_end) if has_more else None
        truncated_any = any(item["truncated"] for item in evidence) or has_more
        result["matched"] = True
        result["action"] = "answer_with_caveat"
        result["reason"] = "public_text_conditional_match"
        result["route"] = f"public_text.{domain}" + (".world" if multi else "")
        result["buckets"]["conditional"] = evidence
        result["evidence"] = evidence
        header = (
            f"据《数据摘要》公开摘要（{self.meta.get('kb_version') or '版本未知'}，"
            f"快照 {self.meta.get('summary_snapshot_date') or '日期未知'}）："
            "以下为条件性摘录，未与当前配置逐项核对；"
            "仅为公开来源片段，不构成完整当前攻略。"
        )
        if conditions and conditions.any():
            header += " 适用条件：" + "、".join(conditions.describe()) + "。"
        if multi:
            header += " 同一世界下可检索到以下入口，具体打法需再指定单个副本。"
        if text_override:
            result["text"] = f"{header}\n\n{text_override}"
        else:
            blocks = [header]
            for item in evidence:
                blocks.append(f"【{item['section']}】\n{item['excerpt']}")
            if has_more:
                blocks.append("（还有更多段落未展示，可用 next_cursor 继续分页。）")
            result["text"] = "\n\n".join(blocks)
        result["pagination"] = {
            "limit": page_limit,
            "returned": len(evidence),
            "total_candidates": total,
            "has_more": has_more,
            "next_cursor": next_cursor,
            "truncated": truncated_any,
            "char_budget": self.total_chars,
            "per_chunk_budget": per_chunk,
            "char_used": used,
            "remaining_candidates": max(0, total - page_end),
            "note": (
                f"还有 {total - page_end} 段候选正文未在本页展示。"
                if has_more
                else None
            ),
        }
        page_chunks = list({unit.chunk.chunk_id: unit.chunk for unit in page}.values())
        for chunk in page_chunks:
            if chunk.incomplete:
                result["buckets"]["unknown"].append(
                    self._unknown_record(
                        "incomplete_source",
                        f"{chunk.section} 在原文中标注未完成，摘录只作条件性参考。",
                        record_type="source_boundary",
                        chunks=[chunk],
                        checked="检查了所选公开段落是否标注未完成。",
                    )
                )
        if domain == "dungeon":
            selected_players = {chunk.players for chunk in page_chunks}
            if any(players is None for players in selected_players) and not multi:
                result["buckets"]["unknown"].append(
                    self._unknown_record(
                        "missing_field",
                        "部分副本段落未标注开启人数，不能推断人数条件。",
                        record_type="knowledge_gap",
                        chunks=page_chunks,
                        checked="检查了所选副本段落的开启人数字段。",
                    )
                )
        result["limits"]["source_processing"].extend(self._historical_notes(ranked))
        branches: list[str] = []
        for unit in page:
            if unit.chunk.branch_label not in branches:
                branches.append(unit.chunk.branch_label)
        result["intent"]["branch"] = branches
        return result

    @staticmethod
    def _historical_notes(chunks: list[_Chunk]) -> list[dict[str, Any]]:
        affected = [chunk.chunk_id for chunk in chunks if chunk.had_strikethrough]
        if not affected:
            return []
        return [
            {
                "record_type": "execution_note",
                "kind": "strikethrough_sanitized",
                "detail": "原文含删除线旧值，输出已剔除删除线内容，未当作当前攻略。",
                "chunk_ids": list(dict.fromkeys(affected)),
            }
        ]

    @staticmethod
    def _document_locator(chunk: _Chunk) -> dict[str, Any]:
        return {
            "source": chunk.source,
            "doc_id": chunk.doc_id,
            "chunk_id": chunk.chunk_id,
            "title": chunk.title,
            "section": chunk.section,
            "version": chunk.version,
            "world": chunk.world,
            "players": list(chunk.players) if chunk.players else None,
            "difficulty": chunk.difficulty,
            "layer": chunk.layer,
            "tier_tag": chunk.tier_tag,
        }

    def _source_scope(self, chunks: list[_Chunk], checked: str | None) -> dict[str, Any]:
        documents: list[dict[str, Any]] = []
        seen: set[str] = set()
        for chunk in chunks:
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            documents.append(self._document_locator(chunk))
        scope: dict[str, Any] = {
            "basis": "verified_release_and_checked_document_scope",
            "release": self.database_info(),
            "documents": documents,
        }
        if checked:
            scope["checked"] = checked
        return scope

    def _unknown_record(
        self,
        kind: str,
        detail: str,
        *,
        record_type: str,
        chunks: list[_Chunk] | None = None,
        checked: str | None = None,
    ) -> dict[str, Any]:
        return {
            "record_type": record_type,
            "kind": kind,
            "detail": detail,
            "scope": self._source_scope(chunks or [], checked),
        }

    def _evidence(self, chunk: _Chunk, excerpt: str, truncated: bool) -> dict[str, Any]:
        return {
            "bucket": "conditional",
            "source": chunk.source,
            "doc_id": chunk.doc_id,
            "chunk_id": chunk.chunk_id,
            "title": chunk.title,
            "section": chunk.section,
            "category": chunk.category,
            "tier": chunk.tier,
            "version": chunk.version,
            "review_status": chunk.review_status,
            "status_note": chunk.status_note,
            "license_basis": chunk.license_basis,
            "domain": chunk.domain,
            "world": chunk.world,
            "players": list(chunk.players) if chunk.players else None,
            "difficulty": chunk.difficulty,
            "layer": chunk.layer,
            "tier_tag": chunk.tier_tag,
            "excerpt": excerpt,
            "truncated": truncated,
            "full_chars": len(chunk.readable),
            "returned_chars": len(excerpt),
        }

    @staticmethod
    def _excerpt(text: str, budget: int) -> tuple[str, bool]:
        if len(text) <= budget:
            return text, False
        cut = text[: max(0, budget - 1)]
        if "\n" in cut:
            head, tail = cut.rsplit("\n", 1)
            if len(head) >= budget * 0.5:
                cut = head
        return cut.rstrip() + "…", True

    def _clarify(
        self,
        result: dict[str, Any],
        reason: str,
        route: str,
        prompt: str,
        options: list[str],
    ) -> dict[str, Any]:
        result["matched"] = True
        result["action"] = "clarify"
        result["reason"] = reason
        result["route"] = route
        if options:
            result["text"] = prompt + "\n可选：" + "、".join(options)
        else:
            result["text"] = prompt
        result["clarify"] = {"prompt": prompt, "options": options}
        result["pagination"] = self._empty_pagination(0)
        return result

    def _abstain(
        self,
        result: dict[str, Any],
        matched: bool,
        reason: str,
        route: str,
        text: str,
        *,
        unknown_kind: str | None = None,
        unknown_detail: str | None = None,
        unknown_type: str = "knowledge_gap",
        unknown_chunks: list[_Chunk] | None = None,
        unknown_scope_note: str | None = None,
    ) -> dict[str, Any]:
        result["matched"] = matched
        result["action"] = "abstain"
        result["reason"] = reason
        result["route"] = route
        result["text"] = text
        if unknown_kind and unknown_detail:
            result["buckets"]["unknown"].append(
                self._unknown_record(
                    unknown_kind,
                    unknown_detail,
                    record_type=unknown_type,
                    chunks=unknown_chunks,
                    checked=unknown_scope_note,
                )
            )
        result["pagination"] = self._empty_pagination(0)
        return result


__all__ = [
    "PublicTextReader",
    "PublicTextError",
    "PublicTextIntegrityError",
    "SCHEMA_VERSION",
]
