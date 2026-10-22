"""Generate navigable catalog and source statistics without reading local bodies."""
from collections import Counter
from pathlib import Path
import sys

from common import ROOT, load_terms
from knowledge import load_knowledge


def generate():
    terms=load_terms()
    docs=load_knowledge()
    categories=Counter(d['category'] for _,d in docs)
    sources=Counter(d['provenance']['attribution'] for _,d in docs)
    rows=[
        '# 分类知识目录','',
        '此处公开标题、分类、来源定位与校验信息；正文只在本地，不包含在仓库或 CI 产物中。',
        '目录数量不等于已审核知识点数，导入记录不计贡献积分。','',
        '| 分类 | 目录记录 |','| --- | --- |',
    ]
    for category,count in sorted(categories.items()):
        rows.append(f'| [{category}]({category}/) | {count} |')
    rows+=['','来源署名：'+ '；'.join(f'{k} {v} 条' for k,v in sorted(sources.items()))+'。',
           '','[《数据摘要》接入说明](../docs/data-summary.md) · [nkwiki 接入说明](../docs/nkwiki-integration.md)',
           '','部分 nkwiki 文章转载《数据摘要》，它们的原始来源标为“乱走丶沙城”，发布渠道保留 nkwiki。',
           '重复记录通过 duplicate_of 关联，检索时合并；每条记录都有独立稳定 ID。']
    (ROOT/'knowledge/README.md').write_text('\n'.join(rows)+'\n',encoding='utf-8')
    stats=[
        '# 本批次数据与来源概览','',
        '由 build/catalog_docs.py 从公开目录生成，不包含外部正文。','',
        f"- 术语标注：{len(terms)} 条；公开贡献名“空调房里晒太阳”。",
        '- 术语状态：'+ '，'.join(f'{k} {v}' for k,v in sorted(Counter(d['review']['status'] for _,d in terms).items()))+'。',
        f"- 外部目录：{len(docs)} 条；来源署名："+'，'.join(f'{k} {v}' for k,v in sorted(sources.items()))+'。',
        f"- 完全相同内容的重复目录：{sum('duplicate_of' in d for _,d in docs)} 条，检索去重。",
        '- 外部审核状态：'+ '，'.join(f'{k} {v}' for k,v in sorted(Counter(d['review']['status'] for _,d in docs).items()))+'。',
        '- 外部资料自动导入不计个人积分；初始 50 条种子归“空调房里晒太阳”，已确认计分，待审单列。',
        '','此处的“数据概览”与游戏资料《数据摘要》是不同文档。',
        '《数据摘要》的原始归属显示“乱走丶沙城”，其余站点记录显示“nkwiki”。',
        '','查看 [分类目录](../knowledge/README.md)、[数据摘要接入](data-summary.md) 和 [nkwiki 接入](nkwiki-integration.md)。',
    ]
    (ROOT/'docs/source-overview.md').write_text('\n'.join(stats)+'\n',encoding='utf-8')


if __name__=='__main__':
    generate()
