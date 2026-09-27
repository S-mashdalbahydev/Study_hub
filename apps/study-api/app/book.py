"""Conservative semantic book model for generated Markdown documents.

Markdown remains the durable source.  This module turns it into stable navigation and
reader pages without making physical source pages the information architecture.
"""
import re


def slug(value, seen=None):
    base=re.sub(r'(^-|-$)','',re.sub(r'[^a-z0-9]+','-',value.lower())) or 'section'
    if seen is None:return base
    count=seen.get(base,0)+1;seen[base]=count
    return base if count==1 else f'{base}-{count}'


def _block(kind, markdown, source='original', **extra):
    return {'id':'', 'type':kind, 'markdown':markdown.strip(), 'origin':source, **extra}


def markdown_book(content, title, filename='', cover=False, target_chars=2600, source_ranges=None):
    """Build hierarchy and paginate only between complete Markdown blocks."""
    lines=content.splitlines();raw=[];buffer=[];in_fence=False
    def flush():
        nonlocal buffer
        text='\n'.join(buffer).strip()
        if text:
            kind='code' if text.startswith('```') else ('callout' if text.startswith('>') else
                'table' if re.search(r'^\s*\|.*\|\s*$',text,re.M) else
                'list' if re.match(r'^\s*(?:[-*+] |\d+\. )',text) else 'paragraph')
            raw.append(_block(kind,text))
        buffer=[]
    for line in lines:
        if line.strip().startswith('```'):
            in_fence=not in_fence;buffer.append(line)
            if not in_fence:flush()
            continue
        heading=re.match(r'^(#{1,6})\s+(.+?)\s*$',line) if not in_fence else None
        if heading:
            flush();raw.append(_block('heading',line,level=len(heading[1]),title=re.sub(r'[`*_]','',heading[2]).strip()))
        elif not line.strip() and not in_fence:flush()
        else:buffer.append(line)
    flush()

    seen={};toc=[];current_part=None;current_chapter=None;current_section=None
    chapter_no=0;section_no=0
    resource_section=False
    for index,block in enumerate(raw):
        block['id']=f'block-{index+1}'
        if block['type']!='heading':
            # Explicitly label generated teaching additions where the source Markdown does.
            if block['type']=='callout' and re.search(r'AI |illustrative|example',block['markdown'],re.I):block['origin']='ai'
            if resource_section and re.search(r'https?://',block['markdown']):block['origin']='external'
            continue
        text=block['title'];level=block['level'];ident=slug(text,seen);block['id']=ident
        resource_section=bool(re.search(r'resource|watch|learn another way|further learning',text,re.I))
        is_part=bool(re.match(r'^(?:part\s+[ivxlcdm\d]+|PART\b)',text,re.I))
        is_chapter=bool(re.match(r'^(?:chapter|unit)\s*[\dNivxlcdm]*\s*[:—-]',re.sub(r'^[^\w]+','',text),re.I))
        if level==1 and index==0:kind='title'
        elif is_part:kind='part';current_part=ident;current_chapter=None
        elif level<=2 or is_chapter:
            kind='chapter';chapter_no+=1;section_no=0;current_chapter=ident
        else:
            kind='subsection' if level>=4 and current_section else 'section';section_no+=1
            if kind=='section':current_section=ident
        block['semantic_type']=kind
        if kind!='title':
            parent=(current_part if kind=='chapter' else current_chapter if kind=='section'
                    else current_section if kind=='subsection' else None)
            source_range=(source_ranges or {}).get(ident) or (source_ranges or {}).get(text)
            toc.append({'id':ident,'title':text,'kind':kind,'level':1 if kind=='part' else 2 if kind=='chapter' else 3 if kind=='section' else 4,
                        'parent_id':parent,'reader_page':1,
                        'source_start_page':source_range[0] if source_range else None,
                        'source_end_page':source_range[1] if source_range else None})

    # Cover is reader page 1. Pages after it hold whole semantic blocks.
    pages=[];page=[];size=0
    def emit():
        nonlocal page,size
        if page:
            pages.append({'id':f'reader-page-{len(pages)+2}','number':len(pages)+2,'blocks':page});page=[];size=0
    for block in raw:
        amount=len(block['markdown'])
        # Major chapters begin on a fresh page, and no block is ever split.
        if page and (size+amount>target_chars or block.get('semantic_type') in ('part','chapter')):emit()
        page.append(block);size+=amount
    emit()
    location={b['id']:p['number'] for p in pages for b in p['blocks']}
    for item in toc:item['reader_page']=location.get(item['id'],2)
    chapters=sum(1 for x in toc if x['kind']=='chapter')
    words=len(re.findall(r'\w+',content));minutes=max(1,round(words/220))
    return {'id':'book','title':title,'subtitle':next((x['title'] for x in raw if x.get('semantic_type')=='chapter'),None),
            'authors':[],'filename':filename,'cover':cover,'estimated_minutes':minutes,'chapter_count':chapters,
            'toc':toc,'pages':pages,'page_count':len(pages)+1}
