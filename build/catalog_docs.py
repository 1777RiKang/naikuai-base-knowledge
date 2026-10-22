"""Generate navigable catalog and source statistics from the authorized public full text."""
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
        '此处按主题公开《数据摘要》完整章节正文、来源定位与校验信息；每个 JSON 的 content 字段包含正文。',
        '目录数量不等于已审核知识点数，导入记录不计贡献积分。','',
        '| 分类 | 目录记录 |','| --- | --- |',
    ]
    for category,count in sorted(categories.items()):
        rows.append(f'| [{category}]({category}/) | {count} |')
    rows+=['','来源署名：'+ '；'.join(f'{k} {v} 条' for k,v in sorted(sources.items()))+'。',
           '','[《数据摘要》全文](../sources/data-summary/full.md) · [接入说明](../docs/data-summary.md)',
           '重复记录通过 duplicate_of 关联，检索时合并；每条记录都有独立稳定 ID。']
    (ROOT/'knowledge/README.md').write_text('\n'.join(rows)+'\n',encoding='utf-8')
    stats=[
        '# 本批次数据与来源概览','',
        '由 build/catalog_docs.py 从公开章节生成；正文见知识 JSON 的 content 字段及完整 Markdown。','',
        f"- 术语标注：{len(terms)} 条；公开贡献名“空调房里晒太阳”。",
        '- 术语状态：'+ '，'.join(f'{k} {v}' for k,v in sorted(Counter(d['review']['status'] for _,d in terms).items()))+'。',
        f"- 数据摘要章节：{len(docs)} 条；来源署名："+'，'.join(f'{k} {v}' for k,v in sorted(sources.items()))+'。',
        f"- 完全相同内容的重复目录：{sum('duplicate_of' in d for _,d in docs)} 条，检索去重。",
        '- 外部审核状态：'+ '，'.join(f'{k} {v}' for k,v in sorted(Counter(d['review']['status'] for _,d in docs).items()))+'。',
        '- 外部资料自动导入不计个人积分；初始 50 条种子归“空调房里晒太阳”，已确认计分，待审单列。',
        '','此处的“数据概览”与游戏资料《数据摘要》是不同文档。',
        '《数据摘要》原始署名为“乱走丶沙城”；完整正文、表格与图片已公开，导入数量不等于已审核知识点数。',
        '','查看 [分类目录](../knowledge/README.md)、[数据摘要接入](data-summary.md)。',
    ]
    (ROOT/'docs/source-overview.md').write_text('\n'.join(stats)+'\n',encoding='utf-8')


if __name__=='__main__':
    generate()
