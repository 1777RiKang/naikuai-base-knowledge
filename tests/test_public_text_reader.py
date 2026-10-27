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
                "c-fe-b1",
                "第六章 精灵 / 6.1精灵装备 / 6.1.1精灵装备加成（仅与精灵属性发挥百分比有关） / 6.1.1.1发饰",
                "| 精灵装备加成-生命 | | 精灵装备加成-受愈 | | 精灵装备加成-无 | |\n"
                "| --- | --- | --- | --- | --- | --- |\n"
                "| 0级 | 生命+4 | 0级 | 受愈+10 | 0级 | 无 |\n"
                "| 1级 | 生命+8 | 1级 | 受愈+25 | 1级 | 无 |\n"
                "| 2级 | 生命+12 | 2级 | 受愈+40 | 2级 | 无 |\n"
                "| 3级 | 生命+16 | 3级 | 受愈+55 | 3级 | 无 |\n"
                "| 4级 | 生命+20 | 4级 | 受愈+70 | 4级 | 无 |\n"
                "| 5级 | 生命+24 | 5级 | 受愈+85 | 5级 | 无 |\n"
                "| 6级 | 生命+28 | 6级 | 受愈+100 | 6级 | 无 |\n"
                "| 7级 | 生命+32 | 7级 | 受愈+115 | 7级 | 无 |",
            ),
            (
                "c-fe-b2",
                "第六章 精灵 / 6.1精灵装备 / 6.1.1精灵装备加成（仅与精灵属性发挥百分比有关） / 6.1.1.2项链",
                "| 精灵装备加成-魔法强度 | | 精灵装备加成-远程伤害 | | 精灵装备加成-近战伤害 | |\n"
                "| --- | --- | --- | --- | --- | --- |\n"
                "| 0级 | 魔法强度+10 | 0级 | 远程伤害+10 | 0级 | 近战伤害+8 |",
            ),
            (
                "c-fe-b3",
                "第六章 精灵 / 6.1精灵装备 / 6.1.1精灵装备加成（仅与精灵属性发挥百分比有关） / 6.1.1.3戒指",
                "| 精灵装备加成-物理防御 | | 精灵装备加成-魔法防御 | | 精灵装备加成-防暴击 | |\n"
                "| --- | --- | --- | --- | --- | --- |\n"
                "| 0级 | 物理防御+15 | 0级 | 魔法防御+15 | 0级 | 防暴击+0.75% |",
            ),
            (
                "c-fe-cmp",
                "第六章 精灵 / 6.1精灵装备 / 6.1.2精灵装备与玩家装备及BUFF的数值对比",
                "| 锋利属性 | 精灵装备 | 附魔属性 |\n"
                "| --- | --- | --- |\n"
                "| 数值 | 8 | 17 |",
            ),
            (
                "c-fe-set",
                "第六章 精灵 / 6.1精灵装备 / 6.1.3精灵装备套装属性",
                "| 套装名称 | 套装属性 | ~~概率~~ |\n"
                "| --- | --- | --- |\n"
                "| 利刃玫瑰Ⅰ | 提升4%精灵攻击力 | ~~71.85%~~ |\n"
                "| 时光跃迁Ⅰ | 降低10%精灵技能的冷却时间 | ~~0.75%~~ |",
            ),
            (
                "c-fe-hist",
                "第六章 精灵 / 6.1精灵装备 / 6.1.4精灵装备分解；6.1.5精灵对应精灵装备",
                "【数据摘要 / 第六章 精灵 / 6.1精灵装备 / 6.1.4精灵装备分解；6.1.5精灵对应精灵装备】\n"
                "|  |  |\n"
                "| --- | --- |\n"
                "| ~~装备部件~~ | ~~分解数量及概率~~ |\n"
                "| ~~发饰~~ | ~~碎片\\*1：55%，碎片\\*2：30%~~ |\n"
                "\n**6.1.5精灵对应精灵装备**\n"
                "|  |  |  |  |\n"
                "| --- | --- | --- | --- |\n"
                "| 精灵对应精灵装备 | 三星精灵 | 四星精灵 | 五星精灵 |\n"
                "| 雨之信-212 | 希尔芙、沙拉曼德 | 蜃 | 妲己、敖烈 |",
            ),
            (
                "c-gem-up",
                "第二章 PVE数据总结 / 2.6宝石 / 2.6.3宝石升级材料",
                "| 升级档次 | | 消耗材料 |\n"
                "| --- | --- | --- |\n"
                "| 三角 | 普通升级为幻光 | 精灵宝石精华液*9 |\n"
                "| 幻光升级为命运 | 虚无结晶碎石*9 |\n"
                "| 命运升级为溯时 | 深海宝石精华液*9 |\n"
                "| 四角 | 普通升级为幻光 | 精灵宝石精华液*18 |\n"
                "| 幻光升级为命运 | 虚无结晶碎石*12+虚无石英柱*6 |\n"
                "| 命运升级为溯时 | 深海宝石精华液*12+高级深海宝石精华液*4 |\n"
                "| 五角 | 普通升级为幻光 | 精灵宝石精华液*36 |\n"
                "| 幻光升级为命运 | 虚无结晶碎石*18+虚无石英柱*18 |\n"
                "| 命运升级为溯时 | 深海宝石精华液*18+高级深海宝石精华液*9 |",
            ),
            (
                "c-gem-cut",
                "第二章 PVE数据总结 / 2.6宝石 / 2.6.4宝石切割",
                "|  |  |\n"
                "| --- | --- |\n"
                "| ~~苏纪石-1/9~~ | ~~孔雀石：97.99%，海靛石：0.67%~~ |",
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


def assert_unknown_scope(test, result, reader=None):
    for record in result["buckets"]["unknown"]:
        test.assertIn(record.get("record_type"), {"knowledge_gap", "source_boundary"})
        test.assertIn("kind", record)
        test.assertIn("detail", record)
        scope = record.get("scope")
        test.assertIsInstance(scope, dict)
        test.assertEqual(scope.get("basis"), "verified_release_and_checked_document_scope")
        release = scope.get("release")
        test.assertIsInstance(release, dict)
        test.assertEqual(release.get("sha256"), result["database"]["sha256"])
        test.assertTrue(release.get("sha256_verified"))
        test.assertIn("manifest_verified", release)
        test.assertIn("kb_version", release)
        test.assertIn("summary_snapshot_date", release)
        docs = scope.get("documents")
        test.assertIsInstance(docs, list)
        test.assertTrue(docs, record.get("kind"))
        for doc in docs:
            for key in ("source", "doc_id", "chunk_id", "section", "version"):
                test.assertIn(key, doc)
            if reader is not None:
                chunk = next(c for c in reader.index.chunks if c.chunk_id == doc["chunk_id"])
                for key in ("source", "doc_id", "section", "version"):
                    test.assertEqual(doc[key], getattr(chunk, key))
        test.assertIn("checked", scope)


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
        {
            None,
            "profession_guide",
            "elf_skills",
            "elf_system",
            "dungeon_intro",
            "dungeon_guide",
            "gem_overview",
            "gem_upgrade",
            "gem_cut",
            "gem_inlay",
            "gem_effect",
            "gem_source",
        },
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
            assert_unknown_scope(self, result, reader)

    def test_type_mismatch_unknown_has_scope(self):
        with self.fx.reader() as reader:
            result = reader.lookup("猎魔人副本")
            assert_contract(self, result)
            self.assertEqual(result["reason"], "entity_type_mismatch")
            self.assertEqual([item["kind"] for item in result["buckets"]["unknown"]], ["type_mismatch"])
            assert_unknown_scope(self, result, reader)

    def test_incomplete_never_answered_as_current(self):
        with self.fx.reader() as reader:
            result = reader.lookup("未完成宫怎么打")
            self.assertTrue(result["matched"])
            self.assertEqual(result["action"], "abstain")
            self.assertEqual(result["reason"], "only_incomplete_or_unreleased")
            assert_unknown_scope(self, result, reader)

    def test_missing_players_is_explicit(self):
        with self.fx.reader() as reader:
            result = reader.lookup("无人数宫怎么打")
            assert_contract(self, result)
            self.assertEqual(result["action"], "answer_with_caveat")
            kinds = {item["kind"] for item in result["buckets"]["unknown"]}
            self.assertIn("missing_field", kinds)

    def test_unknown_scope_records_have_verified_release_and_locator(self):
        with self.fx.reader() as reader:
            result = reader.lookup("无人数宫怎么打")
            assert_contract(self, result)
            unknown = result["buckets"]["unknown"]
            self.assertEqual([item["kind"] for item in unknown], ["missing_field"])
            assert_unknown_scope(self, result, reader)

    def test_no_strategy_unknown_has_scope(self):
        with self.fx.reader() as reader:
            result = reader.lookup("无攻略宫怎么打")
            assert_contract(self, result)
            self.assertEqual(result["reason"], "no_strategy_in_public_text")
            self.assertEqual([item["kind"] for item in result["buckets"]["unknown"]], ["missing_strategy"])
            assert_unknown_scope(self, result, reader)

    def test_condition_mismatch_unknown_has_scope(self):
        with self.fx.reader() as reader:
            result = reader.lookup("测试宫殿二十人怎么打")
            assert_contract(self, result)
            self.assertEqual(result["reason"], "no_branch_matching_conditions")
            self.assertEqual([item["kind"] for item in result["buckets"]["unknown"]], ["condition_mismatch"])
            assert_unknown_scope(self, result, reader)

    def test_gem_missing_mode_unknown_has_scope(self):
        with self.fx.reader() as reader:
            result = reader.lookup("红宝石怎么镶嵌")
            assert_contract(self, result)
            self.assertEqual(result["reason"], "not_in_public_snapshot")
            assert_unknown_scope(self, result, reader)

    def test_pagination_note_is_metadata_not_unknown(self):
        with self.fx.reader(max_evidence=1) as reader:
            result = reader.lookup("测试世界有什么副本")
            assert_contract(self, result)
            self.assertTrue(result["pagination"]["has_more"])
            self.assertGreaterEqual(result["pagination"]["remaining_candidates"], 1)
            self.assertIn("未在本页展示", result["pagination"]["note"])
            self.assertFalse(any(item.get("kind") == "pagination" for item in result["buckets"]["unknown"]))
            assert_unknown_scope(self, result, reader)

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

    def test_fairy_equipment_usage_is_system_not_elf_skills(self):
        with self.fx.reader() as reader:
            result = reader.lookup("精灵装备有什么用途")
            assert_contract(self, result)
            self.assertEqual(result["action"], "answer_with_caveat")
            self.assertEqual(result["intent"]["domain"], "fairy")
            self.assertEqual(result["route"]["intent_family"], "elf_system")
            self.assertEqual(result["route"]["entity_type"], "elf_system")
            self.assertEqual(result["route"]["subject"], "精灵装备")
            self.assertEqual(
                {item["chunk_id"] for item in result["evidence"]},
                {"c-fe-b1", "c-fe-b2", "c-fe-b3", "c-fe-set"},
            )
            for marker in ("生命", "受愈", "装备等级", "不是精灵等级", "利刃玫瑰"):
                self.assertIn(marker, result["text"])
            self.assertNotIn("生命+4", result["text"])
            self.assertNotIn("0级", result["text"])
            self.assertTrue(any(item.get("quote") for item in result["evidence"]))

    def test_fairy_equipment_parts_and_set_use_real_subchapters(self):
        with self.fx.reader() as reader:
            parts = reader.lookup("精灵装备有哪些部位")
            assert_contract(self, parts)
            self.assertEqual(
                {item["chunk_id"] for item in parts["evidence"]},
                {"c-fe-b1", "c-fe-b2", "c-fe-b3", "c-fe-set"},
            )
            self.assertNotIn("生命+4", parts["text"])
            self.assertIn("装备等级", parts["text"])
            for label in ("发饰", "项链", "戒指"):
                self.assertIn(label, parts["text"])
            suit = reader.lookup("精灵装备套装属性")
            assert_contract(self, suit)
            self.assertEqual([item["chunk_id"] for item in suit["evidence"]], ["c-fe-set"])
            self.assertIn("利刃玫瑰", suit["text"])

    def test_fairy_equipment_alt_phrasing_and_comparison(self):
        with self.fx.reader() as reader:
            alt = reader.lookup("精灵装备是干什么用的")
            assert_contract(self, alt)
            self.assertEqual(alt["action"], "answer_with_caveat")
            self.assertEqual(
                {item["chunk_id"] for item in alt["evidence"]},
                {"c-fe-b1", "c-fe-b2", "c-fe-b3", "c-fe-set"},
            )
            self.assertNotIn("生命+4", alt["text"])
            how = reader.lookup("精灵装备怎么用")
            assert_contract(self, how)
            self.assertEqual(how["action"], "answer_with_caveat")
            self.assertEqual(how["route"]["intent_family"], "elf_system")
            compare = reader.lookup("精灵装备和玩家装备对比")
            assert_contract(self, compare)
            self.assertEqual([item["chunk_id"] for item in compare["evidence"]], ["c-fe-cmp"])
            self.assertIn("锋利属性", compare["text"])

    def test_fairy_equipment_correspondence_keeps_current_subsection(self):
        with self.fx.reader() as reader:
            result = reader.lookup("精灵装备对应哪个精灵")
            assert_contract(self, result)
            self.assertEqual(result["action"], "answer_with_caveat")
            self.assertEqual([item["chunk_id"] for item in result["evidence"]], ["c-fe-hist"])
            self.assertIn("6.1.5精灵对应精灵装备", result["evidence"][0]["excerpt"])
            self.assertIn("雨之信", result["text"])
            self.assertNotIn("碎片", result["text"])

    def test_fairy_equipment_dismantle_history_is_excluded(self):
        with self.fx.reader() as reader:
            result = reader.lookup("精灵装备怎么分解")
            assert_contract(self, result)
            self.assertTrue(result["matched"])
            self.assertEqual(result["action"], "abstain")
            self.assertEqual(result["reason"], "only_historical_or_unreleased")
            self.assertFalse(result["evidence"])
            self.assertEqual(
                [item["kind"] for item in result["buckets"]["unknown"]],
                ["historical_source"],
            )
            self.assertIn("删除线", result["text"])
            self.assertIn("c-fe-hist", json.dumps(result["buckets"]["unknown"], ensure_ascii=False))
            assert_unknown_scope(self, result, reader)

    def test_fairy_equipment_missing_acquisition_is_scoped(self):
        with self.fx.reader() as reader:
            result = reader.lookup("精灵装备怎么获得")
            assert_contract(self, result)
            self.assertEqual(result["action"], "abstain")
            self.assertEqual(result["reason"], "not_in_public_snapshot")
            self.assertEqual(result["route"]["intent_family"], "elf_system")
            self.assertNotIn("只收录了宝石效果", result["text"])
            documents = result["buckets"]["unknown"][0]["scope"]["documents"]
            self.assertTrue(documents)
            self.assertTrue(all(item["section"].startswith("第六章 精灵 / 6.1") for item in documents))
            assert_unknown_scope(self, result, reader)

    def test_gem_upgrade_uses_real_material_table(self):
        with self.fx.reader() as reader:
            result = reader.lookup("宝石升级需要什么材料")
            assert_contract(self, result)
            self.assertEqual(result["action"], "answer_with_caveat")
            self.assertEqual(result["route"]["intent_family"], "gem_upgrade")
            self.assertEqual([item["chunk_id"] for item in result["evidence"]], ["c-gem-up"])
            for marker in ("三角", "四角", "五角", "精灵宝石精华液", "普通升级为幻光"):
                self.assertIn(marker, result["text"])
            self.assertNotIn("只收录了宝石效果", result["text"])
            shaped = reader.lookup("四角宝石升级需要什么材料")
            assert_contract(self, shaped)
            self.assertEqual(shaped["conditions"]["gem_shape"], "四角")
            self.assertIn("已识别条件：四角", shaped["text"])
            staged = reader.lookup("宝石升级到命运需要什么")
            assert_contract(self, staged)
            self.assertEqual(staged["conditions"]["gem_stage"], "幻光升级为命运")
            self.assertIn("已识别条件：幻光升级为命运", staged["text"])

    def test_system_overview_phrasings_stay_short(self):
        with self.fx.reader() as reader:
            for question in (
                "精灵装备有啥用",
                "精灵装备有什么作用",
                "精灵装备有哪些部位",
                "精灵装备有多少部位",
            ):
                result = reader.lookup(question)
                assert_contract(self, result)
                self.assertIn("装备等级", result["text"])
                self.assertIn("不是精灵等级", result["text"])
                self.assertIn("利刃玫瑰", result["text"])
                self.assertNotIn("生命+4", result["text"])
                self.assertNotIn("0级", result["text"])
                self.assertLess(len(result["text"]), 700)

    def test_system_detail_level_keeps_values_and_column_scope(self):
        with self.fx.reader() as reader:
            result = reader.lookup("精灵装备0级发饰生命加成")
            assert_contract(self, result)
            self.assertIn("发饰｜生命", result["text"])
            self.assertIn("0级：生命+4", result["text"])
            self.assertNotIn("1级", result["text"])
            self.assertNotIn("受愈", result["text"])
            self.assertTrue(any("0级 | 生命+4" in item.get("quote", "") for item in result["evidence"]))

            ranged = reader.lookup("精灵装备0–7级发饰生命加成")
            assert_contract(self, ranged)
            self.assertIn("0级：生命+4", ranged["text"])
            self.assertIn("7级：生命+32", ranged["text"])
            self.assertNotIn("受愈", ranged["text"])

            scoped = reader.lookup("精灵装备0级加成")
            assert_contract(self, scoped)
            for marker in (
                "发饰｜生命",
                "发饰｜受愈",
                "项链｜魔法强度",
                "项链｜远程伤害",
                "戒指｜物理防御",
                "0级：受愈+10",
                "0级：远程伤害+10",
            ):
                self.assertIn(marker, scoped["text"])
            self.assertNotIn("发饰｜远程伤害", scoped["text"])
            self.assertNotIn("项链｜物理防御", scoped["text"])

    def test_gem_upgrade_keeps_shape_and_stage_scope(self):
        with self.fx.reader() as reader:
            result = reader.lookup("宝石升级需要什么材料")
            assert_contract(self, result)
            text = result["text"]
            for marker in (
                "三角｜普通升级为幻光",
                "三角｜幻光升级为命运",
                "四角｜幻光升级为命运",
                "五角｜命运升级为溯时",
            ):
                self.assertIn(marker, text)
            self.assertNotIn("升级档次｜", text)
            for line in text.splitlines():
                if "升级为" in line:
                    self.assertIn("｜", line)
            quote = result["evidence"][0].get("quote", "")
            self.assertIn("| 升级档次 |", quote)
            self.assertIn("| 幻光升级为命运 |", quote)

            four = reader.lookup("四角宝石升级需要什么材料")
            self.assertIn("四角｜", four["text"])
            self.assertNotIn("三角｜", four["text"])
            self.assertNotIn("五角｜", four["text"])

            fate = reader.lookup("宝石升级到命运需要什么")
            fate_lines = [
                line
                for line in fate["text"].splitlines()
                if "升级为" in line and "｜" in line
            ]
            self.assertTrue(fate_lines)
            self.assertTrue(all("命运" in line and "｜" in line for line in fate_lines))

    def test_gem_cut_history_is_not_answered_as_current(self):
        with self.fx.reader() as reader:
            result = reader.lookup("宝石切割概率")
            assert_contract(self, result)
            self.assertEqual(result["action"], "abstain")
            self.assertEqual(result["reason"], "only_historical_or_unreleased")
            self.assertEqual(result["route"]["intent_family"], "gem_cut")
            self.assertEqual(result["route"]["subject"], "宝石切割")
            self.assertFalse(result["evidence"])
            self.assertEqual(
                [item["kind"] for item in result["buckets"]["unknown"]],
                ["historical_source"],
            )
            self.assertIn("删除线", result["text"])
            self.assertNotIn("97.99", result["text"])
            assert_unknown_scope(self, result, reader)

    def test_gem_inlay_boundary_is_limited_to_this_snapshot(self):
        with self.fx.reader() as reader:
            result = reader.lookup("宝石镶嵌")
            assert_contract(self, result)
            self.assertEqual(result["action"], "abstain")
            self.assertEqual(result["reason"], "not_in_public_snapshot")
            self.assertEqual(result["route"]["intent_family"], "gem_inlay")
            self.assertIn("本公开快照", result["text"])
            self.assertIn("不代表游戏没有该机制", result["text"])
            documents = result["buckets"]["unknown"][0]["scope"]["documents"]
            sections = {item["section"] for item in documents}
            self.assertTrue(any("2.6.3" in section for section in sections))
            self.assertTrue(any("2.6.4" in section for section in sections))
            assert_unknown_scope(self, result, reader)

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
    db = Path(
        os.environ.get(
            "PUBLIC_TEXT_TEST_DB",
            str(ROOT / "dist" / "public-r3" / "naikuai-kb-public.sqlite"),
        )
    )
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

    def test_real_profession_boundaries_are_metadata_not_unsourced_unknown(self):
        result = self.reader.lookup("猎魔人怎么玩")
        assert_contract(self, result)
        self.assertEqual(result["action"], "answer_with_caveat")
        self.assertEqual(len(result["buckets"]["conditional"]), 3)
        self.assertEqual(result["buckets"]["unknown"], [])
        self.assertTrue(result["limits"]["confirmation_scope"]["current_game_config_checked"] is False)
        self.assertEqual(result["limits"]["excerpt_scope"], "source_fragment_not_complete_current_guide")
        self.assertTrue(result["pagination"]["has_more"])
        self.assertIn("未在本页展示", result["pagination"]["note"])
        self.assertTrue(any(
            item["kind"] == "strikethrough_sanitized"
            and item["record_type"] == "execution_note"
            for item in result["limits"]["source_processing"]
        ))
        assert_unknown_scope(self, result, self.reader)
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

    def test_real_fairy_equipment_usage(self):
        result = self.reader.lookup("精灵装备有什么用途")
        assert_contract(self, result)
        self.assertEqual(result["action"], "answer_with_caveat")
        self.assertEqual(result["route"]["intent_family"], "elf_system")
        self.assertEqual(result["route"]["entity_type"], "elf_system")
        self.assertTrue(all("6.1" in item["section"] for item in result["evidence"]))
        for marker in ("发饰", "项链", "戒指", "装备等级", "不是精灵等级", "利刃玫瑰"):
            self.assertIn(marker, result["text"])
        self.assertNotIn("0级", result["text"])
        self.assertLess(len(result["text"]), 800)
        self.assertTrue(any(item.get("quote") for item in result["evidence"]))
        detail = self.reader.lookup("精灵装备0级发饰生命加成")
        assert_contract(self, detail)
        self.assertIn("0级：生命+4", detail["text"])
        self.assertNotIn("1级", detail["text"])
        self.assertNotIn("受愈", detail["text"])
        self._assert_sources(result)
        self._assert_sources(detail)

    def test_real_fairy_equipment_set_and_correspondence(self):
        result = self.reader.lookup("精灵装备套装属性")
        assert_contract(self, result)
        self.assertEqual(result["action"], "answer_with_caveat")
        self.assertTrue(any("6.1.3" in item["section"] for item in result["evidence"]))
        self.assertIn("利刃玫瑰", result["text"])
        self._assert_sources(result)
        mapping = self.reader.lookup("精灵装备对应哪个精灵")
        assert_contract(self, mapping)
        self.assertEqual(mapping["action"], "answer_with_caveat")
        self.assertTrue(any("6.1.5" in item["section"] for item in mapping["evidence"]))
        self.assertIn("雨之信", mapping["text"])
        self.assertNotIn("碎片", mapping["text"])
        self._assert_sources(mapping)

    def test_real_fairy_equipment_dismantle_history(self):
        result = self.reader.lookup("精灵装备怎么分解")
        assert_contract(self, result)
        self.assertEqual(result["action"], "abstain")
        self.assertEqual(result["reason"], "only_historical_or_unreleased")
        self.assertEqual([item["kind"] for item in result["buckets"]["unknown"]], ["historical_source"])
        self.assertIn("删除线", result["text"])
        assert_unknown_scope(self, result, self.reader)

    def test_real_gem_upgrade_uses_material_chapter(self):
        result = self.reader.lookup("宝石升级需要什么材料")
        assert_contract(self, result)
        self.assertEqual(result["action"], "answer_with_caveat")
        self.assertEqual(result["route"]["intent_family"], "gem_upgrade")
        self.assertTrue(any("2.6.3" in item["section"] for item in result["evidence"]))
        for marker in (
            "三角｜普通升级为幻光",
            "三角｜幻光升级为命运",
            "四角｜幻光升级为命运",
            "五角｜命运升级为溯时",
            "精灵宝石精华液",
        ):
            self.assertIn(marker, result["text"])
        self.assertNotIn("升级档次｜", result["text"])
        for line in result["text"].splitlines():
            if "升级为" in line:
                self.assertIn("｜", line)
        self.assertTrue(any("| 升级档次 |" in item.get("quote", "") for item in result["evidence"]))
        self._assert_sources(result)

    def test_real_named_dungeon_boss_skills_use_guide_paragraphs(self):
        result = self.reader.lookup("副本海雾水母有什么技能")
        assert_contract(self, result)
        self.assertEqual(result["action"], "answer_with_caveat")
        self.assertEqual(result["route"]["intent_family"], "dungeon_guide")
        self.assertEqual(result["intent"]["domain"], "dungeon")
        for marker in ("阶段一", "普通攻击", "召唤触须", "撕裂灵魂", "灵魂虹吸"):
            self.assertIn(marker, result["text"])
        self.assertNotIn("40000生命", result["text"])
        other = self.reader.lookup("英灵殿有什么技能")
        assert_contract(self, other)
        self.assertEqual(other["route"]["intent_family"], "dungeon_guide")
        for marker in ("阶段一", "破甲顺劈", "召唤冰霜巨兽", "圣火降临"):
            self.assertIn(marker, other["text"])
        self._assert_sources(result)
        self._assert_sources(other)

    def test_real_named_gem_effect_keeps_only_matching_row(self):
        result = self.reader.lookup("顺劈宝石的效果是什么")
        assert_contract(self, result)
        self.assertEqual(result["action"], "answer_with_caveat")
        self.assertEqual(result["route"]["intent_family"], "gem_effect")
        self.assertIn("骑士专属宝石1-3", result["text"])
        self.assertIn("顺劈1：", result["text"])
        for marker in ("破甲1", "捍卫2", "弓箭手专属宝石", "淬毒4", "易燃5"):
            self.assertNotIn(marker, result["text"])
        self.assertTrue(any("2.6.1" in item["section"] for item in result["evidence"]))
        self.assertTrue(any(item.get("quote") for item in result["evidence"]))
        self._assert_sources(result)

    def test_real_named_gem_effect_does_not_cross_entities(self):
        result = self.reader.lookup("盾墙宝石的效果是什么")
        assert_contract(self, result)
        self.assertEqual(result["action"], "abstain")
        self.assertEqual(result["reason"], "not_in_public_snapshot")
        self.assertNotIn("顺劈1", result["text"])
        self.assertNotIn("破甲1", result["text"])
        assert_unknown_scope(self, result, self.reader)

    def test_real_fairy_equipment_overview_does_not_infer_relationships(self):
        result = self.reader.lookup("精灵装备有什么用")
        assert_contract(self, result)
        self.assertIn("未说明具体作用对象或结算方式", result["text"])
        for guess in ("给精灵", "给玩家", "提供属性加成，并通过套装"):
            self.assertNotIn(guess, result["text"])
        self.assertTrue(any(item.get("quote") for item in result["evidence"]))
        self._assert_sources(result)

    def test_real_gem_cut_is_historical(self):
        result = self.reader.lookup("宝石切割概率")
        assert_contract(self, result)
        self.assertEqual(result["action"], "abstain")
        self.assertEqual(result["reason"], "only_historical_or_unreleased")
        self.assertEqual(result["route"]["intent_family"], "gem_cut")
        self.assertEqual([item["kind"] for item in result["buckets"]["unknown"]], ["historical_source"])
        self.assertIn("删除线", result["text"])
        assert_unknown_scope(self, result, self.reader)

    def test_real_gem_inlay_is_snapshot_boundary(self):
        result = self.reader.lookup("宝石镶嵌")
        assert_contract(self, result)
        self.assertEqual(result["action"], "abstain")
        self.assertEqual(result["reason"], "not_in_public_snapshot")
        self.assertEqual(result["route"]["intent_family"], "gem_inlay")
        self.assertIn("本公开快照", result["text"])
        assert_unknown_scope(self, result, self.reader)

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
