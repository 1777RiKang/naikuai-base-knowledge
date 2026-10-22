"""Public external-source catalog validation and local-only content utilities."""
import hashlib
import json
import re
from pathlib import Path

import jsonschema

from common import ROOT
from privacy import contains_secret, SECRET_PATTERNS

KNOWLEDGE_DIR = ROOT / 'knowledge'
CONTACT = re.compile(r'(?i)(?:QQ(?:群)?|[Qq]\s*群|通讯作者\s*[Qq]?|联系\s*[Qq]群)\s*[:：]?\s*[Qq]?\s*\d{5,12}')
PUBLIC_BODY_KEYS = {'content', 'text', 'body', 'raw', 'description', 'summary'}
ATTRIBUTIONS = json.loads((ROOT / 'sources/attributions.json').read_text(encoding='utf-8'))


def redact(text):
    text = CONTACT.sub('[联系方式已隐藏]', text)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub('[凭据已隐藏]', text)
    return text


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def load_knowledge(directory=None):
    directory = Path(directory) if directory is not None else KNOWLEDGE_DIR
    return [(path, json.loads(path.read_text(encoding='utf-8')))
            for path in sorted(directory.glob('*/*.json'))]


def collect_knowledge_errors(docs):
    schema = json.loads((ROOT / 'schema/knowledge.schema.json').read_text(encoding='utf-8'))
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    errors, seen = [], set()
    by_id = {d.get('id'): d for _, d in docs if isinstance(d, dict)}
    for path, doc in docs:
        label = f'{path.parent.name}/{path.name}'
        if not isinstance(doc, dict):
            errors.append(f'{label}: 目录记录必须是对象')
            continue
        if PUBLIC_BODY_KEYS.intersection(doc):
            errors.append(f'{label}: 外部全文禁止进入公开目录')
            continue
        invalid = list(validator.iter_errors(doc))
        if invalid:
            errors.append(f'{label}: 目录 schema 校验失败（不回显数据）')
            continue
        if doc['id'] in seen:
            errors.append(f'{label}: 目录 id 重复')
        seen.add(doc['id'])
        if path.stem != doc['id'].split('.', 1)[1] or path.parent.name != doc['category']:
            errors.append(f'{label}: 文件路径必须与 id、category 一致')
        if doc['provenance']['attribution'] != ATTRIBUTIONS[doc['provenance']['source']]:
            errors.append(f'{label}: 来源署名不一致')
        if contains_secret(json.dumps(doc, ensure_ascii=False)) or CONTACT.search(json.dumps(doc, ensure_ascii=False)):
            errors.append(f'{label}: 目录含疑似凭据或联系方式（不回显）')
        if doc['review']['status'] == 'confirmed' and not (
                doc['review']['reviewer'] and doc['review']['reviewed_at']):
            errors.append(f'{label}: confirmed 必须有审核人和审核日期')
        if doc['review']['status'] == 'disputed' and not doc['review']['reviewer']:
            errors.append(f'{label}: disputed 必须有审核人')
        target = doc.get('duplicate_of')
        if target and (target not in by_id or target == doc['id']):
            errors.append(f'{label}: duplicate_of 必须指向另一条存在的目录记录')
        if target and by_id.get(target, {}).get('duplicate_of'):
            errors.append(f'{label}: duplicate_of 必须直接指向原记录，不能形成链或环')
    return errors


def validate_catalog():
    docs = load_knowledge()
    errors = collect_knowledge_errors(docs)
    if errors:
        raise ValueError('\n'.join(errors))
    return docs
