"""Generate synthetic r3 test rows; never read production files or game exports."""
import hashlib
import json
import pathlib
import shutil
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parent


def build_fixture(directory):
    directory = pathlib.Path(directory)
    (directory / "build").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "unified_r3/kb_query.py", directory / "kb_query.py")
    db = directory / "build/naikuai-kb.sqlite"
    if db.exists():
        raise ValueError("fixture destination already exists")
    c = sqlite3.connect(db)
    try:
        c.executescript((ROOT / "unified_r3/schema.sql").read_text(encoding="utf-8"))
        def fact(identifier, name, kind="item", answerable=1, attrs=None):
            c.execute("insert into facts values(?,?,?,?,?,?,?,?,?,?,?)", (
                identifier, kind, name, name.lower(), "synthetic", "fixture-only", "synthetic", answerable,
                "[]", json.dumps(attrs or {"说明": "合成测试占位，不是游戏数据"}, ensure_ascii=False), 0))
        for i in range(40):
            fact("fixture:" + str(i), "合成材料" + str(i))
        fact("fixture:bow", "逆流长弓", "wiki_equipment")
        fact("fixture:pick", "木镐", attrs={"name_internal": "item:fixture", "说明": "合成测试占位"})
        fact("fixture:small-one", "中级食物桌")
        fact("fixture:small-two", "顺劈", "gem")
        fact("fixture:disabled", "禁用测试宝石", "gem", answerable=0)
        for i in range(3):
            fact("fixture:large" + str(i), "合成大件" + str(i), "wiki_equipment", attrs={"说明": "超长合成测试字段" * 500})
        c.execute("insert into aliases values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "碎片", "碎片", "赐福碎片", "赐福碎片", "item", "synthetic", "synthetic", "", "", 0, 1, 1, "ambiguous", 1))
        for sense, keyword in [("赐福碎片", "赐福"), ("装备碎片", "装备"), ("传说碎片", "传说")]:
            c.execute("insert into alias_senses values(?,?,?,?)", ("碎片", "碎片", sense, keyword))
        c.execute("insert into aliases values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "合成组合", "合成组合", "合成成员甲", "合成成员甲", "group", "synthetic", "synthetic", "", "", 0, 1, 1, "group", 1))
        c.execute("insert into alias_groups values(?,?,?,?,?)", (
            "合成组合", "合成组合", json.dumps(["合成成员甲", "合成成员乙"], ensure_ascii=False), "group", "synthetic"))
        def chunk(identifier, title, section, text, status="active", sensitive=0):
            c.execute("insert into chunks values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                identifier, "synthetic:" + identifier, title, section, "synthetic", "synthetic", "synthetic",
                "fixture-only", "text", len(text), 1, sensitive, text, status,
                "仅测试旧版过滤" if status == "legacy" else "", "synthetic fixture; no game corpus"))
        chunk("fixture-gem", "宝石镶嵌规则", "装备孔位", "【宝石镶嵌规则 / 装备孔位】\n合成测试资料，未核验任何真实装备孔数。")
        chunk("fixture-legacy", "副本与 BOSS 速查", "暮色森林 女妖圣殿", "【副本与 BOSS 速查】\n暮色森林 女妖圣殿只是用于检索的标题，不包含真实攻略。", "legacy")
        chunk("fixture-candidate", "待审测试", "合成条目", "【待审测试】\n合成待审占位，不是实际知识。", "candidate")
        chunk("fixture-sensitive", "敏感测试", "合成条目", "【敏感测试】\n人工生成的敏感标记占位，没有真实个人信息。", sensitive=1)
        c.execute("insert into chunks_fts(rowid,text,title,section) select rowid,text,title,section from chunks")
        c.executemany("insert into meta values(?,?)", [("summary_version", "fixture-only"), ("summary_review_due", "synthetic")])
        c.commit()
    finally:
        c.close()
    names = ["kb_query.py", "build/naikuai-kb.sqlite"]
    receipt = {"release": "synthetic-only", "sha256": {n: hashlib.sha256((directory / n).read_bytes()).hexdigest() for n in names}}
    (directory / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    return directory


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=pathlib.Path)
    print(build_fixture(parser.parse_args().directory).resolve())
