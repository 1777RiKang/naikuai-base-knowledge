"""Validate and stage a public release; never replace a private database."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import os

REPOSITORY = '1777RiKang/naikuai-base-knowledge'
ASSETS = {'naikuai-kb-public.sqlite', 'README.md', 'LICENSE.md', 'source-manifest.json'}
MAX_ASSET_BYTES = 20 * 1024 * 1024


def parse_manifest(data, expected_sha256):
    if not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
        raise ValueError('invalid manifest digest')
    if len(data) > 128 * 1024 or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError('manifest digest mismatch')
    manifest = json.loads(data)
    if manifest.get('schema_version') != 'unified-r3-public' or manifest.get('repository') != REPOSITORY:
        raise ValueError('wrong public release schema or repository')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?', manifest.get('release_version', '')):
        raise ValueError('invalid release version')
    files = manifest.get('files', [])
    if len(files) != len(ASSETS) or {x.get('path') for x in files} != ASSETS:
        raise ValueError('release asset allowlist mismatch')
    for item in files:
        size = item.get('size_bytes')
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_ASSET_BYTES:
            raise ValueError('invalid asset size')
        if not re.fullmatch(r'[a-f0-9]{64}', item.get('sha256', '')):
            raise ValueError('invalid asset digest')
    return manifest


def validate_directory(directory, manifest):
    directory = Path(directory)
    for item in manifest['files']:
        p = directory / item['path']
        if p.is_symlink() or p.stat().st_size != item['size_bytes']:
            raise ValueError('asset size mismatch')
        if hashlib.sha256(p.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('asset digest mismatch')
    db = directory / 'naikuai-kb-public.sqlite'
    conn = sqlite3.connect(db.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        conn.execute('PRAGMA query_only=ON')
        if conn.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('public database integrity failed')
        meta = dict(conn.execute('SELECT key,value FROM meta'))
        if meta.get('schema_version') != 'unified-r3-public' or meta.get('content_scope') != 'public_clean_only':
            raise ValueError('not a public database')
        if conn.execute('SELECT count(*) FROM facts').fetchone()[0] != 0:
            raise ValueError('unexpected private facts in public release')
    finally:
        conn.close()


def stage_release(manifest_data, expected_sha256, fetch_asset, cache_dir):
    """fetch_asset(name, max_bytes) returns bytes; no downloaded code is run."""
    manifest = parse_manifest(manifest_data, expected_sha256)
    cache = Path(cache_dir).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / manifest['release_version']
    if target.exists():
        if target.is_symlink() or (target / 'manifest.json').read_bytes() != manifest_data:
            raise ValueError('existing version differs; refusing overwrite')
        validate_directory(target, manifest)
        return target
    with tempfile.TemporaryDirectory(prefix='.public-release-', dir=cache) as temporary:
        temp = Path(temporary)
        for item in manifest['files']:
            data = fetch_asset(item['path'], item['size_bytes'])
            if len(data) != item['size_bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('downloaded asset digest mismatch')
            (temp / item['path']).write_bytes(data)
        (temp / 'manifest.json').write_bytes(manifest_data)
        validate_directory(temp, manifest)
        os.replace(temp, target)
    return target


class ReleaseLayer:
    """Optional adapter after the bot's existing authorization gate.

    Public clarification wins before any private lookup. Otherwise exact private
    facts are retained, public evidence improves section routing, and missing
    public coverage falls back to the existing private reader. Never writes DBs.
    """
    def __init__(self, directory, expected_manifest_sha256):
        directory = Path(directory)
        manifest = parse_manifest((directory / 'manifest.json').read_bytes(), expected_manifest_sha256)
        validate_directory(directory, manifest)
        from kb_query_public import PublicKB
        self.kb = PublicKB(str(directory / 'naikuai-kb-public.sqlite'))

    def lookup(self, question, private_lookup=None):
        result = self.kb.lookup(question)
        if result['action'] == 'clarify':
            return result
        private = private_lookup(question) if private_lookup is not None else None
        if private and private.get('action') == 'clarify':
            return private
        profession = result.get('retrieval_intent') == 'profession'
        if private and profession:
            private = dict(private)
            private['facts'] = [f for f in private.get('facts', []) if f.get('kind') != 'npc']
            private['chunks'] = [c for c in private.get('chunks', [])
                                 if 'npc' not in (c.get('title', '') + c.get('section', '') + c.get('source', '')).casefold()
                                 and '家具' not in c.get('section', '')]
            if not private['facts'] and not private['chunks']:
                private['action'] = 'not_found'
        if private and private.get('facts') and not profession:
            return private
        if result.get('chunks') or result.get('facts'):
            return result
        return private if private is not None else result

    def close(self):
        self.kb.conn.close()
