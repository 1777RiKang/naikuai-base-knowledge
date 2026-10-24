import os, sys, json, csv, sqlite3, unicodedata, re, datetime

class SecurityGateViolation(Exception):
    """构建期安全门禁异常：检测到隐私、联系方式或凭据泄漏时抛出"""
    def __init__(self, message=None, *, record_type="unknown", line=0, field="unknown", rule="unknown", rule_name=None, line_no=None, field_name=None, **kwargs):
        line_val = line_no if line_no is not None else line
        field_val = field_name if field_name is not None else field
        r_rule = rule_name or rule
        if message is None:
            message = f"Security Violation: record_type={record_type}, line={line_val}, field={field_val}, rule={r_rule}"
        super().__init__(message)
        self.record_type = record_type
        self.line = line_val
        self.field = field_val
        self.rule_name = r_rule

SECURITY_RULES = {
    "dev_path": re.compile(
        r"(?i)(?:[c-z]:[\\/](?:users|home|workspace|qq_bot|naikuai_workspace|stage|unified-r3)|/(?:home|Users)/[a-zA-Z0-9_]+)",
        re.IGNORECASE
    ),
    "qq_or_contact": re.compile(
        r"(?i)(?:(?:qq|扣扣|联系q群|q群|企鹅群|群号|交流群|官方群|加群|联系方式|联系我|客服)[^\d\w]{0,8}(\d{5,12})|(?:\bqq\s*[:：=]\s*\d{5,12}\b))",
        re.IGNORECASE
    ),
    "phone": re.compile(
        r"(?i)(?:(?:手机号|手机|联系电话|客服电话|电话)[^\d\w]{0,8}(?:\+?86[- ]?)?(?:1[3-9]\d{9}|\d{3,4}[- ]?\d{7,8}))",
        re.IGNORECASE
    ),
    "wechat": re.compile(
        r"(?i)(?:(?:微信号|微信|(?:\b[vw]x\b\s*[:：=]))[^\d\w]{0,8}([a-zA-Z][a-zA-Z0-9_-]{5,19}))",
        re.IGNORECASE
    ),
    "token_or_secret": re.compile(
        r"(?i)(?:-----BEGIN (?:[A-Z0-9_-]+ )?PRIVATE KEY-----"
        r"|\bbearer\s+[a-zA-Z0-9_\-\.]{10,}\b"
        r"|['\"]?(?:api[_-]?key|access[_-]?key|secret[_-]?key|password|token)['\"]?\s*[:=]\s*['\"]?[^\s'\",]{8,}['\"]?"
        r"|sk-[a-zA-Z0-9_\-]{20,}"
        r"|\b(?:fake|test)[_-][a-zA-Z0-9_-]*token[a-zA-Z0-9_-]*\b)",
        re.IGNORECASE
    ),
}

class SecurityGuard:
    """严密构建期安全校验网关"""
    RULES = SECURITY_RULES

    @classmethod
    def check_text(cls, text, record_type="unknown", line=0, field="unknown", **kwargs):
        if not text:
            return
        if not isinstance(text, str):
            text = str(text)
        line_no = kwargs.get('line_no', line)
        field_name = kwargs.get('field_name', field)
        for rule_name, pat in cls.RULES.items():
            if pat.search(text):
                raise SecurityGateViolation(
                    f"Security Violation: record_type={record_type}, line={line_no}, field={field_name}, rule={rule_name}",
                    record_type=record_type,
                    line=line_no,
                    field=field_name,
                    rule_name=rule_name
                )

def norm(s):
    s = unicodedata.normalize('NFKC', s or '').lower()
    return re.sub(r'[\s·・\-_/|\\,，。、:：;；"“”\'‘’()（）\[\]【】<>《》!！?？*#`~]+', '', s)

def build_public_kb(db_path=None, timestamp=None, sources_dir=None, glossary_dir=None, pub_root=None):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    pub_root = pub_root or os.path.dirname(script_dir)
    sources_dir = sources_dir or os.path.join(pub_root, 'sources')
    glossary_dir = glossary_dir or os.path.join(pub_root, 'glossary')
    db_path = db_path or os.environ.get('PUBLIC_KB_DB_PATH') or (sys.argv[1] if len(sys.argv) > 1 else os.path.join(pub_root, 'naikuai-kb-public.sqlite'))

    print(f"Building public KB: {db_path}")

    # 1. 逐行读取数据并全量执行安全门禁校验 (一旦命中立即抛出致命异常并中断构建)
    chunks_file = os.path.join(sources_dir, 'chunks.jsonl')
    chunks = []
    if os.path.exists(chunks_file):
        with open(chunks_file, encoding='utf-8') as f:
            for line_idx, line in enumerate(f, start=1):
                line_str = line.strip()
                if not line_str:
                    continue
                item = json.loads(line_str)
                for k, v in item.items():
                    SecurityGuard.check_text(v, record_type="chunk", line=line_idx, field=k)
                chunks.append(item)
    print(f"Loaded {len(chunks)} chunks from {chunks_file} (Security gate check passed)")

    aliases_file = os.path.join(glossary_dir, 'aliases.tsv')
    aliases_data = []
    if os.path.exists(aliases_file):
        with open(aliases_file, encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            for row in reader:
                line_no = reader.line_num
                for k, v in row.items():
                    SecurityGuard.check_text(v, record_type="alias", line=line_no, field=k)
                aliases_data.append(row)
    print(f"Loaded {len(aliases_data)} aliases from {aliases_file} (Security gate check passed)")

    groups_file = os.path.join(glossary_dir, 'alias_groups.tsv')
    groups_data = []
    if os.path.exists(groups_file):
        with open(groups_file, encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            for row in reader:
                line_no = reader.line_num
                for k, v in row.items():
                    SecurityGuard.check_text(v, record_type="alias_group", line=line_no, field=k)
                groups_data.append(row)
        print(f"Loaded {len(groups_data)} groups from {groups_file} (Security gate check passed)")

    senses_file = os.path.join(glossary_dir, 'alias_senses.tsv')
    senses_data = []
    if os.path.exists(senses_file):
        with open(senses_file, encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            for row in reader:
                line_no = reader.line_num
                for k, v in row.items():
                    SecurityGuard.check_text(v, record_type="alias_sense", line=line_no, field=k)
                senses_data.append(row)
        print(f"Loaded {len(senses_data)} senses from {senses_file} (Security gate check passed)")

    sources_file = os.path.join(pub_root, 'sources.csv')
    sources_list = []
    if os.path.exists(sources_file):
        with open(sources_file, encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                line_no = reader.line_num
                for k, v in row.items():
                    SecurityGuard.check_text(v, record_type="source", line=line_no, field=k)
                sources_list.append(row)

    # 2. 写入临时数据库，确保原子覆盖，阻止异常数据污染或覆盖现有生产库
    temp_db_path = f"{db_path}.tmp_{os.getpid()}"
    if os.path.exists(temp_db_path):
        os.remove(temp_db_path)

    try:
        conn = sqlite3.connect(temp_db_path)
        c = conn.cursor()

        c.executescript("""
        CREATE TABLE chunks(
          chunk_id TEXT PRIMARY KEY,
          doc_id TEXT,
          title TEXT,
          section TEXT,
          category TEXT,
          tier TEXT,
          source TEXT,
          version TEXT,
          chunk_type TEXT,
          char_count INT,
          publishable INT,
          sensitive INT,
          text TEXT,
          review_status TEXT DEFAULT 'active',
          status_note TEXT DEFAULT '',
          license_basis TEXT DEFAULT ''
        );

        CREATE VIRTUAL TABLE chunks_fts USING fts5(
          text, title, section,
          content='chunks',
          content_rowid='rowid',
          tokenize='trigram'
        );

        CREATE TABLE aliases(
          alias TEXT,
          alias_norm TEXT,
          canonical TEXT,
          canonical_norm TEXT,
          kind TEXT,
          source TEXT,
          status TEXT,
          ref TEXT,
          note TEXT,
          conflict INT,
          primary_ INT,
          use_in_chat INT,
          resolution TEXT,
          publishable INT
        );
        CREATE INDEX ix_alias ON aliases(alias_norm);

        CREATE TABLE alias_groups(
          alias_norm TEXT PRIMARY KEY,
          alias TEXT,
          members TEXT,
          kind TEXT,
          source TEXT
        );

        CREATE TABLE alias_senses(
          alias_norm TEXT,
          alias TEXT,
          sense TEXT,
          keywords TEXT
        );

        CREATE TABLE facts(
          id TEXT PRIMARY KEY,
          kind TEXT,
          name TEXT,
          name_norm TEXT,
          tier TEXT,
          version TEXT,
          source TEXT,
          answerable INT,
          flags TEXT,
          attrs TEXT,
          publishable INT DEFAULT 1
        );
        CREATE INDEX ix_fact_name ON facts(name_norm);

        CREATE TABLE meta(
          key TEXT PRIMARY KEY,
          value TEXT
        );
        """)

        # 3. Populate chunks
        chunk_rows = []
        for x in chunks:
            chunk_rows.append((
                x['chunk_id'], x['doc_id'], x['title'], x['section'], x['category'],
                x['tier'], x['source'], x['version'], x['chunk_type'], x['char_count'],
                int(x.get('publishable', 1)), int(x.get('sensitive', 0)), x['text'],
                x.get('review_status', 'active'), x.get('status_note', ''), x.get('license_basis', '')
            ))
        c.executemany("INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", chunk_rows)
        c.execute("INSERT INTO chunks_fts(rowid, text, title, section) SELECT rowid, text, title, section FROM chunks")

        # 4. Populate aliases
        alias_rows = []
        for r in aliases_data:
            alias_rows.append((
                r['alias'], norm(r['alias']), r['canonical'], norm(r['canonical']),
                r.get('kind', ''), r.get('source', ''), r.get('status', ''), r.get('ref', ''),
                r.get('note', ''), int(r.get('conflict') == 'conflict'), int(r.get('primary') == 'primary'),
                int(r.get('use_in_chat') == 'yes'), r.get('resolution', 'single'),
                int(r.get('publishable') == 'yes')
            ))
        c.executemany("INSERT INTO aliases VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", alias_rows)

        # 5. Populate alias_groups
        if groups_data:
            group_rows = [(norm(g['alias']), g['alias'], g['members'], g.get('kind', ''), g.get('source', '')) for g in groups_data]
            c.executemany("INSERT INTO alias_groups VALUES(?,?,?,?,?)", group_rows)

        # 6. Populate alias_senses
        if senses_data:
            sense_rows = [(norm(s['alias']), s['alias'], s['sense'], s['keywords']) for s in senses_data]
            c.executemany("INSERT INTO alias_senses VALUES(?,?,?,?)", sense_rows)

        # 7. Metadata
        stats = {
            'chunks_count': len(chunks),
            'aliases_count': len(aliases_data),
            'facts_count': 0,
            'alias_senses_count': len(senses_data),
            'sensitive_chunks_count': 0,
            'build_timestamp_utc': timestamp or os.environ.get('PUBLIC_KB_TIMESTAMP') or datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

        meta_kvs = [
            ('schema_version', 'unified-r3-public'),
            ('kb_version', '8.6.26.10-public'),
            ('built_at', stats['build_timestamp_utc']),
            ('build_script', 'build_public.py'),
            ('content_scope', 'public_clean_only'),
            ('upstream_repo', 'https://github.com/1777RiKang/naikuai-base-knowledge'),
            ('upstream_main_commit', '42849623ddc9d9795e25bef230d5846a439b6b56'),
            ('upstream_pr11_commit', 'f45db87075f250d409347fffeca7ad7abe12615f'),
            ('summary_version', '8.6.26.10'),
            ('summary_snapshot_date', '2026-10-21'),
            ('summary_review_due', '2026-11-21'),
            ('summary_after_due', 'needs_review'),
            ('summary_validity_text', '原文：“文件生效期为1个月”，未写明起算日期'),
            ('summary_author', '乱走丶沙城'),
            ('license', 'CC BY-NC-SA 4.0 / LicenseRef-Original-Source / MIT'),
            ('sources_manifest', json.dumps(sources_list, ensure_ascii=False)),
            ('stats', json.dumps(stats, ensure_ascii=False))
        ]
        c.executemany("INSERT INTO meta VALUES(?,?)", meta_kvs)

        conn.commit()
        conn.execute("VACUUM")
        conn.close()
        conn = None

        # 原子性替换现有数据库文件：
        # 禁止先 os.remove(db_path) 再重命名；改用原子的 os.replace(temp_db_path, db_path)；
        # 在任何异常发生时捕获并确保清理 temp_db_path，绝不触碰现存的 db_path。
        try:
            os.replace(temp_db_path, db_path)
            print("Build public database succeeded!")
        except Exception as e:
            if os.path.exists(temp_db_path):
                try:
                    os.remove(temp_db_path)
                except Exception:
                    pass
            raise RuntimeError(f"Failed to atomically replace public database '{db_path}': {e}") from e

    except Exception:
        if 'conn' in locals() and conn:
            try:
                conn.close()
            except Exception:
                pass
        if os.path.exists(temp_db_path):
            try:
                os.remove(temp_db_path)
            except Exception:
                pass
        raise

if __name__ == '__main__':
    target_db = os.environ.get('PUBLIC_KB_DB_PATH') or (sys.argv[1] if len(sys.argv) > 1 else None)
    build_public_kb(db_path=target_db)
