"""Publish an existing full Markdown extraction and restore its DOCX images."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET

from common import ROOT
from knowledge import redact


def extract(markdown, docx, destination):
    markdown, docx, destination = Path(markdown), Path(docx), Path(destination)
    text = markdown.read_text(encoding='utf-8-sig')
    with zipfile.ZipFile(docx) as archive:
        document = ET.fromstring(archive.read('word/document.xml'))
        relationships = {r.attrib['Id']: r.attrib['Target'] for r in
                         ET.fromstring(archive.read('word/_rels/document.xml.rels'))}
        ns = {'a':'http://schemas.openxmlformats.org/drawingml/2006/main',
              'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
              'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        images = [relationships[node.attrib['{'+ns['r']+'}embed']]
                  for node in document.findall('.//a:blip',ns)]
        placeholders = list(re.finditer(r'!\[.*?\]\(data:image/[^)]*\)',text))
        if len(images) != len(placeholders):
            raise ValueError('Markdown 图片数与原始 Word 不符')
        if len(document.findall('.//w:tbl',ns)) != len(re.findall(r'^\| ---',text,re.M)):
            raise ValueError('Markdown 表格数与原始 Word 不符')
        # Check every nonempty text run to detect incomplete extractions.
        compact = lambda value: re.sub(r'[\s|#*~_`\\]+','',value)
        full = compact(text)
        missing = [node.text for node in document.findall('.//w:t',ns)
                   if node.text and len(compact(node.text)) >= 6 and compact(node.text) not in full]
        if missing:
            raise ValueError(f'Markdown 缺少 {len(missing)} 段原文，请重新提取')
        destination.mkdir(parents=True,exist_ok=True)
        assets = destination/'images'
        assets.mkdir(exist_ok=True)
        index = iter(images)
        media = {}
        def replace(match):
            target = next(index)
            name = Path(target).name
            content = archive.read('word/'+target)
            (assets/name).write_bytes(content)
            media[name] = hashlib.sha256(content).hexdigest()
            return '![](images/'+name+')'
        text = re.sub(r'!\[.*?\]\(data:image/[^)]*\)',replace,text)
    text = redact(text,allow_author_contact=True)
    (destination/'full.md').write_text(text,encoding='utf-8',newline='\n')
    receipt = {'version':'8.6.26.10','author':'乱走丶沙城',
               'original_docx_sha256':hashlib.sha256(docx.read_bytes()).hexdigest(),
               'original_markdown_sha256':hashlib.sha256(markdown.read_bytes()).hexdigest(),
               'published_markdown_sha256':hashlib.sha256((destination/'full.md').read_bytes()).hexdigest(),
               'image_references':len(images),'images':media,
               'tables':len(document.findall('.//w:tbl',ns)),
               'publication_changes':['恢复原始 Word 中的图片','保留指定公开作者联系，其他联系方式脱敏'],
               'independent_disclaimer_file':'not-provided; original in-document notice retained'}
    (destination/'extraction-manifest.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--markdown',type=Path,required=True)
    parser.add_argument('--docx',type=Path,required=True)
    args=parser.parse_args()
    receipt=extract(args.markdown,args.docx,ROOT/'sources/data-summary')
    print(json.dumps({k:v for k,v in receipt.items() if k!='images'},ensure_ascii=False,indent=2))
