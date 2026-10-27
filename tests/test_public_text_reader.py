import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples" / "public_text_reader"))

from public_text import (  # noqa: E402
    PublicTextError,
    PublicTextIntegrityError,
    PublicTextReader,
)


SCHEMA = """
CREATE TABLE chunks(
  chunk_id TEXT PRIMARY KEY, doc_id TEXT, title TEXT, section TEXT, category TEXT,
  tier TEXT, source TEXT, version TEXT, chunk_type TEXT, char_count INT,
  publishable INT, sensitive INT, text TEXT, review_status TEXT DEFAULT 'active',
  status_note TEXT DEFAULT '', license_basis TEXT DEFAULT ''
);
CREATE TABLE aliases(
  alias TEXT, alias_norm TEXT, canonical TEXT, canonical_norm TEXT, kind TEXT,
  source TEXT, status TEXT, ref TEXT, note TEXT, conflict INT, primary_ INT,
  use_in_chat INT, resolution TEXT, publishable INT
);
CREATE TABLE alias_senses(alias_norm TEXT, alias TEXT, sense TEXT, keywords TEXT);
CREATE TABLE alias_groups(alias_norm TEXT, alias TEXT, members TEXT, kind TEXT, source TEXT);
CREATE TABLE facts(
  id TEXT PRIMARY KEY, kind TEXT, name TEXT, name_norm TEXT, tier TEXT,
  version TEXT, source TEXT, answerable INT, flags TEXT, attrs TEXT,
  publishable INT DEFAULT 1
);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
"""


def table_text(dungeon, players, extra=""):
    return (
        "| 副本名称 | 开启时间 | 开启人数 | 总经验值 | 坐标 |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"| {dungeon} | 全天 | {players} | 1w | 1-1 |\n"
        "战斗区域大小：半径10格的圆形平台；阶段一100-60；Boss攻击会叠加破甲。"
        + extra
    )


class SyntheticFixture:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "kb.sqlite"
        conn = sqlite3.connect(self.path)
        conn.executescript(SCHEMA)
        chunks = [
            (
                "c-prof",
                "第二章 PVE数据总结 / 2.3对各职业的深刻理解 / 2.3.3弓箭手 / 2.3.3.1猎魔人",
                "猎魔人\n"
                + "\n".join(f"附魔属性-测试{i}：增加{i}%无关伤害" for i in range(1, 16))
                + "\n核心机制：先用标记锁定目标，再消耗子弹进入决斗架势爆发。"
                "\n主要限制：热量达到100时铳枪伤害降低，需要先泄热。",
            ),
            (
                "c-fairy",
                "第六章 精灵 / 6.2各精灵介绍 / 6.2.1希尔芙",
                "希尔芙定位为治疗辅助。\n"
                "精灵礼物\n"
                "转生前喜欢的礼物：精灵香薰盒、祈祷护符。\n"
                "50级精灵技能数值\n"
                "风之治愈：恢复全队生命，并在之后数秒内持续恢复少量生命值，适合在持续掉血阶段提前释放。\n"
                "护盾：为全队提供吸收护盾，护盾存在期间提高防御并减少受到的爆发伤害，优先给前排。\n"
                "净化：解除全队负面状态，并在短时间内降低再次受到控制的概率，需要预留冷却应对机制。",
            ),
            (
                "c-d5",
                "第四章 副本 / 4.1秘境 / 4.1.9测试世界T7-20220106 / 4.1.9.1测试宫殿",
                table_text("测试宫殿", "5-5", "\n阶段一：躲避扇形攻击。"),
            ),
            (
                "c-d10",
                "第四章 副本 / 4.1秘境 / 4.1.9测试世界T7-20220106 / 4.1.9.2精英测试宫殿",
                table_text("精英测试宫殿", "10-10", "\n阶段二：分散站位并集火。"),
            ),
            (
                "c-dboss",
                "第四章 副本 / 4.1秘境 / 4.1.9测试世界T7-20220106 / 4.1.9.3希尔芙",
                table_text("希尔芙", "3-3", "\n希尔芙（BOSS）会召唤小怪。"),
            ),
            (
                "c-hist",
                "第四章 副本 / 4.1秘境 / 4.1.9测试世界T7-20220106 / 4.1.9.4旧宫殿（历史）",
                table_text("旧宫殿", "5-5"),
            ),
            (
                "c-inc",
                "第四章 副本 / 4.1秘境 / 4.1.9测试世界T7-20220106 / 4.1.9.5未完成宫（未完成）",
                table_text("未完成宫", "5-5"),
            ),
            (
                "c-noplayer",
                "第四章 副本 / 4.1秘境 / 4.1.9测试世界T7-20220106 / 4.1.9.6无人数宫",
                "| 副本名称 | 开启时间 | 总经验值 | 坐标 |\n| --- | --- | --- | --- |\n"
                "| 无人数宫 | 全天 | 1w | 1-1 |\n阶段一：注意仇恨。",
            ),
            (
                "c-noguide",
                "第四章 副本 / 4.1秘境 / 4.1.9测试世界T7-20220106 / 4.1.9.8无攻略宫",
                "| 副本名称 | 开启时间 | 开启人数 | 总经验值 | 坐标 |\n"
                "| --- | --- | --- | --- | --- |\n"
                "| 无攻略宫 | 全天 | 5-5 | 1w | 1-1 |\n"
                "无攻略宫首领：10000生命，300攻击，500防御。",
            ),
            (
                "c-other",
                "第四章 副本 / 4.1秘境 / 4.1.10另一世界T7-20220106 / 4.1.10.1另一宫殿",
                table_text("另一宫殿", "3-3"),
            ),
            (
                "c-third",
                "第四章 副本 / 4.1秘境 / 4.1.11测试之地T7-20220106 / 4.1.11.1异地宫",
                table_text("异地宫", "5-5"),
            ),
            (
                "c-long",
                "第六章 精灵 / 6.2各精灵介绍 / 6.2.2长文精灵",
                "长文精灵技能说明。" + "每段都描述技能循环与站位。" * 80,
            ),
            (
                "c-gem",
                "第二章 PVE数据总结 / 2.6宝石 / 2.6.1宝石效果",
                "| 宝石 | 效果 |\n| --- | --- |\n| 红宝石 | 增加攻击 |",
            ),
            (
                "c-secret",
                "第四章 副本 / 4.1秘境 / 4.1.9测试世界T7-20220106 / 4.1.9.7敏感宫",
                table_text("敏感宫", "5-5"),
            ),
        ]
        for chunk_id, section, text in chunks:
            conn.execute(
                "INSERT INTO chunks(chunk_id, doc_id, title, section, category, tier, source, version, "
                "chunk_type, char_count, publishable, sensitive, text, review_status) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    chunk_id,
                    "doc-" + chunk_id,
                    "测试数据",
                    section,
                    "副本" if "第四章" in section else "精灵",
                    "",
                    "public-test",
                    "test-1",
                    "text",
                    len(text),
                    1,
                    1 if chunk_id == "c-secret" else 0,
                    text,
                    "active",
                ),
            )
        conn.execute(
            "INSERT INTO aliases(alias, alias_norm, canonical, canonical_norm, use_in_chat, "
            "publishable, resolution) VALUES(?,?,?,?,?,?,?)",
            ("猎魔", "猎魔", "猎魔人", "猎魔人", 1, 1, "resolved"),
        )
        conn.execute("INSERT INTO meta VALUES('schema_version','unified-r3-public')")
        conn.execute("INSERT INTO meta VALUES('content_scope','public_clean_only')")
        conn.execute("INSERT INTO meta VALUES('kb_version','test-public')")
        conn.execute("INSERT INTO meta VALUES('stats','{\"facts_count\":0,\"chunks_count\":11}')")
        conn.commit()
        conn.close()
        self.sha = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def reader(self, **kwargs):
        return PublicTextReader(self.path, self.sha, verify_manifest=False, **kwargs)

    def cleanup(self):
        self.tmp.cleanup()


def assert_contract(test, result):
    for key in (
        "schema_version",
        "matched",
        "action",
        "reason",
        "route",
        "text",
        "buckets",
        "evidence",
        "database",
        "pagination",
        "intent",
        "clarify",
        "limits",
        "question",
        "scope",
        "entity_type",
        "entity",
        "conditions",
        "requested_intent",
    ):
        test.assertIn(key, result)
    test.assertEqual(result["question"], result["query"])
    test.assertIsInstance(result["route"], dict)
    test.assertIn("handler", result["route"])
    test.assertIn("intent_family", result["route"])
    test.assertIn("scope", result["route"])
    test.assertIn(
        result["route"]["intent_family"],
        {None, "profession_guide", "elf_skills", "dungeon_intro", "dungeon_guide", "gem_overview"},
    )
    test.assertEqual(set(result["buckets"]), {"confirmed", "conditional", "conflict", "unknown"})
    test.assertEqual(result["buckets"]["confirmed"], [])
    test.assertEqual(result["buckets"]["conflict"], [])
    if result["action"] == "answer_with_caveat":
        test.assertEqual(result["buckets"]["conditional"], result["evidence"])
        test.assertTrue(result["evidence"])
        for item in result["evidence"]:
            for key in ("source", "doc_id", "chunk_id", "section", "version", "excerpt", "truncated"):
                test.assertIn(key, item)


class SyntheticReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = SyntheticFixture()

    @classmethod
    def tearDownClass(cls):
        cls.fx.cleanup()

    def test_profession_answer_and_source(self):
        with self.fx.reader() as reader:
            result = reader.lookup("猎魔人怎么玩")
            assert_contract(self, result)
            self.assertTrue(result["matched"])
            self.assertEqual(result["action"], "answer_with_caveat")
            self.assertEqual(result["intent"]["domain"], "profession")
            self.assertIn("标记", result["text"])
            self.assertEqual(result["evidence"][0]["chunk_id"], "c-prof")

    def test_alias_resolves(self):
        with self.fx.reader() as reader:
            result = reader.lookup("猎魔怎么玩")
            self.assertEqual(result["action"], "answer_with_caveat")
            self.assertEqual(result["intent"]["entity"], "猎魔人")

    def test_fairy_skill_type_isolation(self):
        with self.fx.reader() as reader:
            result = reader.lookup("希尔芙技能")
            self.assertEqual(result["action"], "answer_with_caveat")
            self.assertEqual(result["intent"]["domain"], "fairy")
            self.assertEqual(result["evidence"][0]["chunk_id"], "c-fairy")

    def test_same_name_dungeon_uses_dungeon_branch(self):
        with self.fx.reader() as reader:
            result = reader.lookup("希尔芙怎么打")
            assert_contract(self, result)
            self.assertEqual(result["action"], "answer_with_caveat")
            self.assertEqual(result["intent"]["domain"], "dungeon")
            self.assertEqual(result["evidence"][0]["chunk_id"], "c-dboss")

    def test_multi_player_branch_requires_clarification(self):
        with self.fx.reader() as reader:
            result = reader.lookup("测试宫殿怎么打")
            self.assertEqual(result["action"], "clarify")
            self.assertEqual(result["reason"], "ambiguous_branch")
            self.assertEqual(len(result["clarify"]["options"]), 2)

    def test_player_condition_selects_ten_player_branch(self):
        with self.fx.reader() as reader:
            result = reader.lookup("测试宫殿十人怎么打")
            assert_contract(self, result)
            self.assertEqual(result["action"], "answer_with_caveat")
            self.assertEqual(result["evidence"][0]["chunk_id"], "c-d10")
            self.assertEqual(result["evidence"][0]["players"], [10, 10])

    def test_world_list_question(self):
        with self.fx.reader() as reader:
            result = reader.lookup("测试世界有什么副本")
            assert_contract(self, result)
            self.assertEqual(result["action"], "answer_with_caveat")
            self.assertTrue(result["pagination"]["truncated"])

    def test_historical_never_answered_as_current(self):
        with self.fx.reader() as reader:
            result = reader.lookup("旧宫殿怎么打")
            self.assertTrue(result["matched"])
            self.assertEqual(result["action"], "abstain")
            self.assertNotIn("旧宫殿", {item["section"] for item in result["evidence"]})

    def test_incomplete_never_answered_as_current(self):
        with self.fx.reader() as reader:
            result = reader.lookup("未完成宫怎么打")
            self.assertTrue(result["matched"])
            self.assertEqual(result["action"], "abstain")
            self.assertEqual(result["reason"], "only_incomplete_or_unreleased")

    def test_missing_players_is_explicit(self):
        with self.fx.reader() as reader:
            result = reader.lookup("无人数宫怎么打")
            assert_contract(self, result)
            self.assertEqual(result["action"], "answer_with_caveat")
            kinds = {item["kind"] for item in result["buckets"]["unknown"]}
            self.assertIn("missing_field", kinds)

    def test_long_text_truncates_honestly(self):
        with self.fx.reader(per_chunk_chars=200, total_chars=400) as reader:
            result = reader.lookup("长文精灵技能")
            assert_contract(self, result)
            self.assertTrue(result["pagination"]["truncated"])
            self.assertTrue(result["evidence"][0]["truncated"])
            self.assertTrue(result["evidence"][0]["excerpt"].endswith("…"))

    def test_pagination_cursor(self):
        with self.fx.reader(max_evidence=1) as reader:
            first = reader.lookup("测试世界有什么副本")
            assert_contract(self, first)
            self.assertTrue(first["pagination"]["has_more"])
            second = reader.lookup("测试世界有什么副本", cursor=first["pagination"]["next_cursor"])
            self.assertNotEqual(
                first["evidence"][0]["chunk_id"],
                second["evidence"][0]["chunk_id"],
            )

    def test_broad_gem_clarifies(self):
        with self.fx.reader() as reader:
            result = reader.lookup("宝石怎么用")
            self.assertEqual(result["action"], "clarify")
            self.assertEqual(result["reason"], "ambiguous_gem")

    def test_gem_effect_uses_gem_section(self):
        with self.fx.reader() as reader:
            result = reader.lookup("红宝石有什么效果")
            self.assertEqual(result["action"], "answer_with_caveat")
            self.assertEqual(result["intent"]["domain"], "gem")
            self.assertEqual(result["evidence"][0]["chunk_id"], "c-gem")

    def test_gem_inlay_does_not_reuse_effect_table(self):
        with self.fx.reader() as reader:
            result = reader.lookup("红宝石怎么镶嵌")
            self.assertEqual(result["action"], "abstain")
            self.assertEqual(result["reason"], "not_in_public_snapshot")

    def test_casual_chat_is_not_matched(self):
        with self.fx.reader() as reader:
            result = reader.lookup("今天天气怎么样")
            self.assertFalse(result["matched"])
            self.assertEqual(result["action"], "abstain")
            self.assertEqual(result["route"]["handler"], "none")
            self.assertEqual(result["route"]["scope"], "non_game")
            self.assertIsNone(result["route"]["intent_family"])

    def test_empty_and_non_string(self):
        with self.fx.reader() as reader:
            self.assertEqual(reader.lookup("")["reason"], "empty_question")
            with self.assertRaises(PublicTextError):
                reader.lookup(None)

    def test_sensitive_chunk_is_not_indexed(self):
        with self.fx.reader() as reader:
            result = reader.lookup("敏感宫怎么打")
            self.assertEqual(result["action"], "abstain")
            self.assertFalse(result["evidence"])
            self.assertNotIn("c-secret", json.dumps(result, ensure_ascii=False))

    def test_wrong_sha_and_facts(self):
        bad = "0" * 64
        with self.assertRaises(PublicTextIntegrityError):
            PublicTextReader(self.fx.path, bad, verify_manifest=False)
        conn = sqlite3.connect(self.fx.path)
        conn.execute(
            "INSERT INTO facts(id, kind, name, publishable) VALUES('f1','x','y',1)"
        )
        conn.commit()
        conn.close()
        with self.assertRaises(PublicTextError):
            PublicTextReader(self.fx.path, self.fx.sha, verify_manifest=False)

    def test_closed_reader_rejects_lookup(self):
        reader = self.fx.reader()
        reader.close()
        with self.assertRaises(PublicTextError):
            reader.lookup("猎魔人怎么玩")

    def test_invalid_cursor(self):
        with self.fx.reader() as reader:
            with self.assertRaises(PublicTextError):
                reader.lookup("长文精灵技能", cursor="not-an-int")

    def test_interpolation_attempt_returns_no_rows(self):
        with self.fx.reader() as reader:
            result = reader.lookup("' OR 1=1 --")
            self.assertFalse(result["matched"])
            self.assertFalse(result["evidence"])


    def test_route_interface_families(self):
        cases = {
            "猎魔人怎么玩": "profession_guide",
            "希尔芙技能": "elf_skills",
            "测试宫殿怎么打": "dungeon_guide",
            "测试宫殿是什么": "dungeon_intro",
            "宝石怎么用": "gem_overview",
        }
        with self.fx.reader() as reader:
            for question, family in cases.items():
                result = reader.lookup(question)
                assert_contract(self, result)
                self.assertEqual(result["route"]["intent_family"], family, question)
                self.assertEqual(result["route"]["scope"], "game", question)
                expected_type = {"profession_guide": "profession", "elf_skills": "elf",
                                 "dungeon_guide": "dungeon", "dungeon_intro": "dungeon",
                                 "gem_overview": "gem"}[family]
                self.assertEqual(result["route"]["entity_type"], expected_type)
                self.assertEqual(result["route"]["subject"], result["intent"]["entity"])
            casual = reader.lookup("今天天气怎么样")
            self.assertEqual(casual["route"]["intent_family"], None)
            self.assertEqual(casual["route"]["scope"], "non_game")

    def test_branch_clarify_text_lists_real_options(self):
        with self.fx.reader() as reader:
            result = reader.lookup("测试宫殿怎么打")
            self.assertEqual(result["action"], "clarify")
            self.assertIn("测试宫殿（5-5）", result["text"])
            self.assertIn("精英测试宫殿（10-10）", result["text"])
            self.assertEqual(result["text"], result["clarify"]["prompt"] + "\n可选：" + "、".join(result["clarify"]["options"]))

    def test_world_abbreviation_resolves_unique_prefix(self):
        with self.fx.reader() as reader:
            result = reader.lookup("另一3人有什么副本")
            assert_contract(self, result)
            self.assertEqual(result["action"], "answer_with_caveat")
            self.assertEqual(result["intent"]["entity"], "另一世界")
            self.assertEqual(result["conditions"]["players"], 3)
            self.assertTrue(any(item["chunk_id"] == "c-other" for item in result["evidence"]))

    def test_world_abbreviation_ambiguity_clarifies(self):
        with self.fx.reader() as reader:
            result = reader.lookup("测试3人有什么副本")
            self.assertEqual(result["action"], "clarify")
            self.assertEqual(result["reason"], "ambiguous_world_abbrev")
            self.assertEqual(set(result["clarify"]["options"]), {"测试世界", "测试之地"})

    def test_fairy_skill_excerpt_skips_panels_and_gifts(self):
        with self.fx.reader() as reader:
            result = reader.lookup("希尔芙技能")
            assert_contract(self, result)
            excerpt = result["evidence"][0]["excerpt"]
            self.assertIn("风之治愈", excerpt)
            self.assertIn("护盾", excerpt)
            self.assertIn("净化", excerpt)
            self.assertNotIn("礼物", excerpt)
            self.assertNotIn("定位为治疗辅助", excerpt)

    def test_profession_excerpt_keeps_core_mechanics(self):
        with self.fx.reader() as reader:
            result = reader.lookup("猎魔人怎么玩")
            assert_contract(self, result)
            text = "\n".join(item["excerpt"] for item in result["evidence"])
            self.assertIn("核心机制", text)
            self.assertIn("主要限制", text)
            self.assertNotIn("附魔属性-测试15", text)

    def test_dungeon_strategy_requires_guide_text(self):
        with self.fx.reader() as reader:
            strategy = reader.lookup("无攻略宫怎么打")
            self.assertTrue(strategy["matched"])
            self.assertEqual(strategy["action"], "abstain")
            self.assertEqual(strategy["reason"], "no_strategy_in_public_text")
            intro = reader.lookup("无攻略宫是什么")
            self.assertEqual(intro["action"], "answer_with_caveat")
            self.assertEqual(intro["route"]["intent_family"], "dungeon_intro")

    def test_skill_excerpt_is_from_source_and_paginates(self):
        with self.fx.reader(per_chunk_chars=80, total_chars=500, max_evidence=1) as reader:
            result = reader.lookup("希尔芙技能")
            assert_contract(self, result)
            self.assertTrue(result["pagination"]["has_more"])
            source = next(c.readable for c in reader.index.chunks if c.chunk_id == "c-fairy")
            for item in result["evidence"]:
                self.assertEqual(item["chunk_id"], "c-fairy")
                self.assertIn(item["excerpt"].replace("…", ""), source)


def _real_db_and_sha():
    db = Path(os.environ.get("PUBLIC_TEXT_TEST_DB", str(ROOT / "dist/public-r3/naikuai-kb-public.sqlite")))
    manifest = db.with_name("manifest.json")
    if not db.is_file() or not manifest.is_file():
        return None, None
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for item in data.get("files", []):
        if item.get("path") == db.name:
            return db, item["sha256"]
    return None, None


@unittest.skipUnless(_real_db_and_sha()[0] is not None, "verified public DB not available")
class RealPublicDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.sha = _real_db_and_sha()
        cls.reader = PublicTextReader(cls.db, cls.sha)

    @classmethod
    def tearDownClass(cls):
        cls.reader.close()

    def _assert_sources(self, result):
        conn = sqlite3.connect(f"file:{self.db.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            for item in result["evidence"]:
                row = conn.execute(
                    "SELECT source, doc_id, section, version FROM chunks WHERE chunk_id=?",
                    (item["chunk_id"],),
                ).fetchone()
                self.assertIsNotNone(row, item["chunk_id"])
                for key in ("source", "doc_id", "section", "version"):
                    self.assertEqual(item[key], row[key])
        finally:
            conn.close()

    def test_real_profession(self):
        result = self.reader.lookup("猎魔人怎么玩")
        assert_contract(self, result)
        self.assertEqual(result["action"], "answer_with_caveat")
        self.assertEqual(result["intent"]["domain"], "profession")
        self._assert_sources(result)

    def test_world_party_collection_definition_lists_matching_entries(self):
        result = self.reader.lookup("无尽的三人本指的是什么？")
        self.assertEqual(result["action"], "answer_with_caveat")
        self.assertEqual(result["conditions"]["players"], 3)
        self.assertEqual(result["route"]["entity_type"], "dungeon")
        for name in ("海雾水母", "春天女神", "养鸦人"):
            self.assertIn(name, result["text"])
        self.assertFalse(result["buckets"]["confirmed"])
        self._assert_sources(result)

    def test_real_archer_profession_keeps_later_enchant_groups(self):
        result = self.reader.lookup("弓箭手玩法")
        assert_contract(self, result)
        text = "\n".join(item["excerpt"] for item in result["evidence"])
        self.assertIn("附魔属性-爆裂", text)
        self.assertIn("爆裂Ⅰ", text)
        self._assert_sources(result)

    def test_real_world_list_uses_entry_overview(self):
        result = self.reader.lookup("无界3人有什么副本")
        assert_contract(self, result)
        self.assertEqual(result["action"], "answer_with_caveat")
        self.assertEqual(len(result["evidence"]), 1)
        self.assertTrue(result["evidence"][0]["excerpt"].startswith("副本名称"))
        self.assertNotIn("阶段", result["evidence"][0]["excerpt"])
        self._assert_sources(result)

    def test_real_fairy(self):
        result = self.reader.lookup("希尔芙技能")
        assert_contract(self, result)
        self.assertEqual(result["intent"]["domain"], "fairy")
        self._assert_sources(result)

    def test_real_dungeon_condition(self):
        result = self.reader.lookup("尼伯龙根十人怎么打")
        assert_contract(self, result)
        self.assertEqual(result["action"], "answer_with_caveat")
        self.assertEqual(result["intent"]["domain"], "dungeon")
        self.assertTrue(any(item["players"] == [10, 10] for item in result["evidence"]))
        self._assert_sources(result)

    def test_real_incomplete_floor(self):
        result = self.reader.lookup("轮回塔第七层怎么打")
        self.assertEqual(result["reason"], "only_incomplete_or_unreleased")

    def test_real_casual_chat(self):
        result = self.reader.lookup("今天天气怎么样")
        self.assertFalse(result["matched"])
        self.assertEqual(result["action"], "abstain")


if __name__ == "__main__":
    unittest.main()
