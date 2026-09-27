"""Book structure taken from the book itself: bookmarks first, then real headings.

Pure functions plus `build_structure`, which renders the cover and writes the structure
artifact for one processing revision. Nothing here is invented by a model.
"""
import json
import re
import threading

def markdown_anchor(title):
    return re.sub(r'(^-|-$)','',re.sub(r'[^a-z0-9]+','-',title.lower())) or 'section'

def markdown_outline(content):
    result=[];seen={}
    for match in re.finditer(r'^(#{1,2})\s+(.+?)\s*$',content,re.M):
        title=re.sub(r'[`*_]','',match[2]).strip()
        base=markdown_anchor(title);count=seen.get(base,0);seen[base]=count+1
        result.append({'title':title,'page':1,'level':len(match[1]),'anchor':base if not count else f'{base}-{count+1}'})
    return result

PREVIEW_VERSION = 4


def numbered_level(title):
    """Depth from the book's own numbering. Deterministic: no model, no guessing."""
    text=re.sub(r'^[^\w]+','',title.strip())
    if re.match(r'^part\s+([ivxlcdm]+|\d+)\b',text,re.I):return 1
    if re.match(r'^(chapter|unit|appendix|lecture|module)\b',text,re.I):return 2
    numbered=re.match(r'^(\d+(?:\.\d+)+)(?:[\s.):]|$)',text)
    if numbered:return min(5,1+len(numbered[1].split('.')))
    if re.match(r'^\d+[\s.):]+\S',text):return 2
    return 3


def tidy(items):
    """Everything the book itself declares is kept. Only literal repeats of the same
    heading on the same page are collapsed, and entries carrying no readable text."""
    result=[];seen=set()
    for item in items:
        title=re.sub(r'\s+',' ',(item.get('title') or '')).strip()
        if not (2<=len(title)<=200) or not re.search(r'[A-Za-z؀-ۿ]',title):continue
        key=(title.lower(),item.get('page'))
        if key in seen:continue
        seen.add(key)
        result.append({**item,'title':title})
    return result


def nest(items):
    """Turn a flat levelled list into a parent/child tree with contiguous depth."""
    outline=[];stack=[];seen={}
    for item in items:
        level=max(1,int(item.get('level') or 1))
        base=markdown_anchor(item['title']);seen[base]=seen.get(base,0)+1
        ident=base if seen[base]==1 else f'{base}-{seen[base]}'
        while stack and stack[-1][0]>=level:stack.pop()
        outline.append({'id':ident,'title':item['title'],'page':item.get('page') or 1,
                        'level':len(stack)+1,'parent_id':stack[-1][1] if stack else None})
        stack.append((level,ident))
    return outline


def native_outline(pdf):
    """The publisher's own table of contents, embedded in the PDF as bookmarks."""
    items=[];last=1
    try:bookmarks=list(pdf.get_toc())
    except Exception:return []
    for bookmark in bookmarks:
        try:
            title=(bookmark.get_title() or '').strip()
            destination=bookmark.get_dest()
            page=(destination.get_index()+1) if destination else None
        except Exception:continue
        if not title:continue
        last=page or last
        items.append({'title':title,'level':bookmark.level+1,'page':last})
    return items


def heading_outline(parsed):
    """Fallback for PDFs with no bookmarks: real headings, depth from their numbering."""
    headings=[]
    for item in parsed.get('texts') or []:
        if item.get('label') not in ('section_header','title'):continue
        title=(item.get('heading') or item.get('text') or '').strip()
        if not title:continue
        headings.append({'title':title,'page':(item.get('prov') or [{}])[0].get('page_no') or 1,
                         'reported':item.get('level')})
    # Docling reports level 1 for every header in many books; only trust it when it varies.
    trusted=len({h['reported'] for h in headings if h['reported']})>1
    for heading in headings:
        heading['level']=int(heading['reported']) if trusted and heading['reported'] else numbered_level(heading['title'])
    return headings


def figure_index(parsed,outline):
    """Docling stores captions as $ref pointers; resolve them. One bad item drops only itself."""
    by_ref={t.get('self_ref'):(t.get('text') or '') for t in (parsed.get('texts') or []) if t.get('self_ref')}
    entries=[('figure',x) for x in (parsed.get('pictures') or [])]+[('table',x) for x in (parsed.get('tables') or [])]
    figures=[]
    for index,(kind,item) in enumerate(entries,1):
        try:
            parts=[]
            for caption in item.get('captions') or []:
                parts.append((caption.get('text') or by_ref.get(caption.get('$ref'),'')) if isinstance(caption,dict) else str(caption))
            text=re.sub(r'\s+',' ',' '.join(p for p in parts if p)).strip() or re.sub(r'\s+',' ',(item.get('text') or '')).strip()
            # Uncaptioned artwork (logos, rules, decorations) would only clutter the index.
            if not text:continue
            prov=(item.get('prov') or [{}])[0];page=prov.get('page_no') or 1
            label=re.match(r'((?:figure|fig\.|table)\s*\d+(?:[.\-]\d+)*)',text,re.I)
            parent=next((node['id'] for node in reversed(outline) if node['page']<=page),None)
            # The label is shown beside the caption, so don't print it twice.
            caption=re.sub(r'^\s*[.:—–-]\s*','',text[label.end():]).strip() if label else text
            figures.append({'id':f'{kind}-{index}','label':label.group(1).strip(' .') if label else f'{kind.title()} {index}',
                            'caption':caption or text,'page':page,
                            'section_id':parent,'bounding_box':prov.get('bbox')})
        except Exception:
            continue
    return figures


def source_structure(parsed,pdf=None):
    """Book structure by priority: native PDF bookmarks, then real headings. Never invented."""
    items=native_outline(pdf) if pdf is not None else []
    if not items:items=heading_outline(parsed)
    outline=nest(tidy(items))
    return outline,figure_index(parsed,outline)


_render_lock = threading.Lock()


def build_structure(storage, kind, original_path, parsed, prefix):
    """Write structure.json (and cover.jpg for PDFs) under `prefix`; return the structure.

    PDFium is not thread-safe, so native rendering is serialized across threads."""
    with _render_lock:
        info={'version':PREVIEW_VERSION,'page_count':None,'outline':[],'figures':[],'cover':False}
        manifest={'structure_key':prefix+'/structure.json'}
        if kind=='pdf' and original_path is not None:
            import io
            import pypdfium2 as pdfium
            with pdfium.PdfDocument(original_path) as pdf:
                info['page_count']=len(pdf)
                info['outline'],info['figures']=source_structure(parsed,pdf)
                if len(pdf):
                    page=pdf[0]
                    bitmap=page.render(scale=480/page.get_width())
                    buffer=io.BytesIO()
                    bitmap.to_pil().convert('RGB').save(buffer,format='JPEG',quality=85)
                    bitmap.close();page.close()
                    storage.put_bytes(prefix+'/cover.jpg',buffer.getvalue())
                    manifest['cover_key']=prefix+'/cover.jpg';info['cover']=True
        else:
            info['outline'],info['figures']=source_structure(parsed)
        storage.put_json(manifest['structure_key'],info)
        return info,manifest


def section_rows(outline):
    """Outline entries as rows in document order. Parents always precede children."""
    result=[];seen=set()
    for position,node in enumerate(outline):
        slug=node.get('id') or markdown_anchor(node['title'])
        if slug in seen:continue
        seen.add(slug)
        page=max(1,int(node.get('page') or 1))
        result.append({'slug':slug,'title':node['title'][:500],'level':max(1,min(9,int(node.get('level') or 1))),
                       'position':len(result),'page_start':page,'parent_slug':node.get('parent_id')})
    return result
