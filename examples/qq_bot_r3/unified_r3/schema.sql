-- r3 schema only. Test rows are generated independently.
CREATE TABLE alias_groups(alias_norm TEXT PRIMARY KEY, alias TEXT, members TEXT, kind TEXT, source TEXT);
CREATE TABLE alias_senses(alias_norm TEXT, alias TEXT, sense TEXT, keywords TEXT);
CREATE TABLE aliases(alias TEXT, alias_norm TEXT, canonical TEXT, canonical_norm TEXT, kind TEXT, source TEXT, status TEXT, ref TEXT, note TEXT, conflict INT, primary_ INT, use_in_chat INT, resolution TEXT, publishable INT);
CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY, doc_id TEXT, title TEXT, section TEXT, category TEXT, tier TEXT, source TEXT,
  version TEXT, chunk_type TEXT, char_count INT, publishable INT, sensitive INT, text TEXT, review_status TEXT DEFAULT 'active', status_note TEXT DEFAULT '', license_basis TEXT DEFAULT '');
CREATE VIRTUAL TABLE chunks_fts USING fts5(text, title, section, content='chunks', content_rowid='rowid', tokenize='trigram');
CREATE TABLE facts(id TEXT PRIMARY KEY, kind TEXT, name TEXT, name_norm TEXT, tier TEXT, version TEXT, source TEXT, answerable INT, flags TEXT, attrs TEXT, publishable INT DEFAULT 0);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX ix_alias ON aliases(alias_norm);
CREATE INDEX ix_fact_name ON facts(name_norm);
