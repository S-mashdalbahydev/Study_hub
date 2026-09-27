"""Cross-book study map.

Themes come from what the learner wants to get good at. Which chapters belong to each
theme is decided by the embeddings that already index the library — a search problem
vectors are reliable at — so the map stays real even when the tutor model is small.
The model's only job is proposing theme names, and the learner can supply those instead.
"""
import asyncio
import json
import math
import re
from fastapi import APIRouter, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import library
from .auth import require_user
from .db import one, rows
from .ids import parse_id, public
from .providers import ProviderError
from .tutors import choice_for

router=APIRouter(prefix='/api/learning-sessions')

MIN_CHAPTER_CHARS=800
MAX_CHAPTERS_PER_BOOK=60
MAX_THEMES=7
CHAPTERS_PER_BOOK_PER_THEME=2
RELATIVE_FLOOR=.78
# A second source only joins a topic when it is genuinely close to the best match for
# it. Without this a material with nothing to say still contributes its least-bad guess.
CROSS_FLOOR=.85
# Front and back matter is real book structure, but it teaches nothing, so the study
# map leaves it out. The reader's table of contents still shows every one of these.
FRONT_MATTER=re.compile(r'^\W*(?:(?:table of )?contents|index|glossary|copyright|acknowledg\w*|preface|'
                        r'about the author|notes|bibliograph\w*|references|further reading|colophon|'
                        r'dedication|foreword|acclaim|praise for|how to contact|o.reilly safari|'
                        r'errata|title page|epigraph|conventions used|using code examples|'
                        r'who should read|scope of|outline of)\b|\bthis book\W*$',re.I)


class CreateSession(BaseModel):
    title:str=Field(min_length=1,max_length=160)
    learning_goal:str=Field(min_length=1,max_length=1000)
    document_ids:list[str]=Field(min_length=2,max_length=20)


class Answer(BaseModel):
    answer:str=Field(min_length=1,max_length=12000)


SPACE='Study session'


async def get_session(conn,sid):
    row=await one(conn,'SELECT * FROM learning_sessions WHERE id=%s',(sid,))
    if not row:raise HTTPException(404,'Study session not found')
    materials=await rows(conn,'''SELECT m.library_item_id AS document_id,m.material_type,m.source_url,m.timestamp_start,
                                        m.timestamp_end,li.title,li.original_filename AS filename,b.kind
                                 FROM learning_materials m
                                 JOIN library_items li ON li.id=m.library_item_id
                                 JOIN content_blobs b ON b.id=li.blob_id
                                 WHERE m.session_id=%s ORDER BY li.title''',(sid,))
    answers=await rows(conn,'SELECT id,block_id,answer,feedback,created_at FROM exercise_answers WHERE session_id=%s ORDER BY created_at',(sid,))
    themes=await rows(conn,'SELECT id,title,summary,position,lesson,exercise,citations,resources FROM learning_themes WHERE session_id=%s ORDER BY position',(sid,))
    chapters=await rows(conn,'''SELECT theme_id,library_item_id AS document_id,chapter_key AS chapter_id,title,page_start AS page,
                                       page_end AS end_page,done,position,match_status
                                FROM learning_theme_chapters WHERE session_id=%s ORDER BY position''',(sid,))
    titles={m['document_id']:m['title'] for m in materials}
    kinds={m['document_id']:m['kind'] for m in materials}
    for theme in themes:
        theme['chapters']=[{**c,'book':titles.get(c['document_id'],'Source'),'kind':kinds.get(c['document_id'],'pdf')}
                           for c in chapters if c['theme_id']==theme['id']]
    result={k:row[k] for k in ('id','title','learning_goal','status','notebook','created_at','updated_at','row_version')}
    result['materials']=materials;result['answers']=answers;result['themes']=themes
    return public(result)


async def load_item(conn,item_id):
    item=await library.get_item(conn,item_id)
    if item:
        item['chunks']=await library.revision_chunks(conn,item['current_revision_id'])
    return item


def markdown_units(runtime,doc):
    """Markdown has headings but no pages, so chapters are split at the headings
    themselves and deep-linked to the reader page the digital book puts them on."""
    from .book import markdown_book
    content=runtime.storage.read_text(doc['object_key'],'utf-8','replace')
    reader_page={}
    for item in markdown_book(content,doc['title'],doc['filename'])['toc']:
        reader_page.setdefault(item['title'],item['reader_page'])
    headings=list(re.finditer(r'^(#{1,6})\s+(.+?)\s*$',content,re.M))
    units=[]
    for index,match in enumerate(headings):
        level=len(match[1])
        title=re.sub(r'[`*_]','',match[2]).strip()
        if level>2 or FRONT_MATTER.match(title):continue
        end=next((later.start() for later in headings[index+1:] if len(later[1])<=level),len(content))
        text=content[match.end():end].strip()
        if len(text)<MIN_CHAPTER_CHARS:continue
        units.append({'document_id':doc['id'],'chapter_id':re.sub(r'\W+','-',title.lower()).strip('-') or f'section-{index}',
                      'title':title,'page':reader_page.get(title,1),'end_page':None,
                      'text':text,'order':index})
    return units[:MAX_CHAPTERS_PER_BOOK]


def chapter_units(runtime,doc):
    """Real chapters from the book's own contents, each with the text it actually covers."""
    if doc['kind'] in ('md','txt'):
        split=markdown_units(runtime,doc)
        if split:return split
    outline=library.structure(runtime.storage,doc).get('outline') or []
    if not outline:return []
    chunks=doc['chunks']
    units=[]
    for index,node in enumerate(outline):
        level=node.get('level',1)
        if level>2 or FRONT_MATTER.match(node['title']):continue
        # A chapter runs until the next heading at the same or higher level, not until
        # its own first subsection — otherwise every chapter would be one page long.
        end=next((later['page'] for later in outline[index+1:]
                  if later.get('level',1)<=level and later['page']>node['page']),None)
        text='\n'.join(c['text'] for c in chunks
                       if (c['page'] or 0)>=node['page'] and (end is None or (c['page'] or 0)<end))
        if len(text)<MIN_CHAPTER_CHARS:continue
        units.append({'document_id':doc['id'],'chapter_id':node['id'],'title':node['title'],
                      'page':node['page'],'end_page':end,'text':text,'order':index,'level':level})
    # A "Part" holds the same words as the chapters inside it, so keeping both would file
    # the same pages under two themes. Prefer the chapters and drop the container.
    inner=[unit for unit in units
           if not any(other['level']>unit['level'] and other['page']>unit['page']
                      and (unit['end_page'] is None or other['page']<unit['end_page'])
                      for other in units)]
    units=inner or units
    units.sort(key=lambda u:len(u['text']),reverse=True)
    units=units[:MAX_CHAPTERS_PER_BOOK]
    units.sort(key=lambda u:u['order'])
    return units


def unit_vector(values):
    length=math.sqrt(sum(x*x for x in values))
    return [x/length for x in values] if length else values


def similarity(a,b):
    return sum(x*y for x,y in zip(a,b))


async def derive_themes(runtime,goal,docs,choice=None):
    """Themes come from what the learner wants, phrased so the vector index can find it.

    A line format is used rather than JSON: small local models produce it reliably, and a
    malformed line is skipped instead of losing every theme to one parse error."""
    from .research import model_text
    catalogue='\n'.join('- '+doc['title'] for doc in docs)
    prompt=('A learner has these study materials:\n'+catalogue+'\n\nTheir goal:\n'+goal+
            '\n\nBreak this goal into '+str(MAX_THEMES)+' study themes these materials can really teach. '
            'Output one theme per line and nothing else, in exactly this format:\n'
            'Name | what the learner gains | words to search for in the books\n\n'
            'The Name is 2 to 5 plain words, like: Scaling under load, Validating an idea, '
            'Measuring progress, Handling failure, Shipping in small steps. No numbering, no bullets.\n'
            'Do not explain your reasoning. Do not write anything before or after the lines.')
    themes=[]
    try:
        raw=await model_text(runtime,'You plan practical study material. Follow the output format exactly.',prompt,2048,choice)
    except Exception as exc:
        raise ProviderError('The tutor model could not plan your study themes: '+str(exc)) from None
    for line in raw.splitlines():
        parts=[part.strip(' *-#\t') for part in line.split('|')]
        if len(parts)<3:continue
        name=re.sub(r'^\d+[.)]\s*','',parts[0]).strip(' "\'')
        # Models often echo the instruction line back; that is not a theme.
        if re.match(r'^(format|name|example|template|theme name)\b',name,re.I):continue
        if re.match(r'^what the learner',parts[1],re.I):continue
        if not (2<=len(name.split())<=6):continue
        themes.append({'title':name[:80],'summary':parts[1][:300],'query':(parts[2] or name)[:300]})
    unique=[];seen=set()
    for theme in themes:
        key=theme['title'].lower()
        if key in seen:continue
        seen.add(key);unique.append(theme)
    return unique[:MAX_THEMES]


def assign_chapters(themes,theme_vectors,units,chapter_vectors):
    """Every chapter lands under the single theme it matches best, and each theme keeps a
    balanced handful from each book so one large book cannot crowd the other out."""
    points=[unit_vector(v) for v in chapter_vectors]
    anchors=[unit_vector(v) for v in theme_vectors]
    theme_best=[max((similarity(points[u],anchors[t]) for u in range(len(units))),default=0) or 1
                for t in range(len(themes))]
    # Rank by fit *relative to each theme's own best*, so a chapter lands under the topic
    # it suits most rather than the topic that happens to score highest in absolute terms.
    ranked=sorted(((similarity(points[u],anchors[t])/theme_best[t],
                    similarity(points[u],anchors[t]),t,u)
                   for t in range(len(themes)) for u in range(len(units))),
                  key=lambda row:row[0],reverse=True)
    # Similarity is not comparable across themes, and a long book scores higher than a
    # short paper on the same topic. Judging each source against its own best match for
    # that theme is what lets every material contribute instead of one crowding the rest.
    best={}
    for t in range(len(themes)):
        for u in range(len(units)):
            key=(t,units[u]['document_id'])
            best[key]=max(best.get(key,0),similarity(points[u],anchors[t]))
    groups=[[] for _ in themes];taken=set();counts={}
    for relative,score,theme_index,unit_index in ranked:
        if unit_index in taken:continue
        if score<best[(theme_index,units[unit_index]['document_id'])]*RELATIVE_FLOOR:continue
        if relative<CROSS_FLOOR:continue
        book=units[unit_index]['document_id']
        if counts.get((theme_index,book),0)>=CHAPTERS_PER_BOOK_PER_THEME:continue
        groups[theme_index].append(unit_index);taken.add(unit_index)
        counts[(theme_index,book)]=counts.get((theme_index,book),0)+1
    return [sorted(group,key=lambda m:(units[m]['document_id'],units[m]['order'])) for group in groups]


async def build_map(runtime,principal,sid,goal,docs,custom=()):
    units=[]
    for doc in docs:units.extend(await asyncio.to_thread(chapter_units,runtime,doc))
    if len({u['document_id'] for u in units})<2:
        raise HTTPException(409,'At least two materials need readable chapters. Open each book once so its contents are extracted, then try again.')
    choice=await choice_for(runtime,principal.ctx)
    themes=[{'title':name,'summary':'','query':name} for name in custom] if custom else await derive_themes(runtime,goal,docs,choice)
    if not themes:
        raise HTTPException(502,'The tutor model did not return usable study themes. Type your own themes, or configure a stronger tutor model.')
    chapter_vectors=[]
    for start in range(0,len(units),16):
        batch=units[start:start+16]
        chapter_vectors.extend(await runtime.embeddings.embed(
            [u['title']+'\n'+u['text'][:1200] for u in batch],title='chapter'))
    # Themes are embedded as questions, chapters as documents: the index was built that way.
    theme_vectors=await runtime.embeddings.embed(
        [t['title']+'. '+t['query'] for t in themes],query=True)
    groups=assign_chapters(themes,theme_vectors,units,chapter_vectors)
    keep=[(theme,group) for theme,group in zip(themes,groups) if len(group)>=2]
    if not keep:
        raise HTTPException(409,'These materials did not yield enough matching chapters. Try a more specific goal.')
    revisions={d['id']:d['current_revision_id'] for d in docs}
    async with runtime.db.tx(principal.ctx) as conn:
        await conn.execute('DELETE FROM learning_themes WHERE session_id=%s',(sid,))
        for position,(theme,group) in enumerate(keep):
            tid=(await one(conn,'''INSERT INTO learning_themes (workspace_id,session_id,title,summary,position)
                                     VALUES (%s,%s,%s,%s,%s) RETURNING id''',
                           (principal.workspace_id,sid,theme['title'],theme['summary'],position)))['id']
            for order,m in enumerate(group):
                unit=units[m];revision=revisions.get(unit['document_id'])
                section=await one(conn,'SELECT id FROM sections WHERE revision_id=%s AND slug=%s',(revision,unit['chapter_id'])) if revision else None
                await conn.execute('''INSERT INTO learning_theme_chapters
                    (workspace_id,session_id,theme_id,library_item_id,chapter_key,revision_id,section_id,match_status,
                     title,page_start,page_end,position)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                    (principal.workspace_id,sid,tid,unit['document_id'],unit['chapter_id'],revision if section else None,
                     section['id'] if section else None,'matched' if section else 'unresolved',unit['title'],
                     max(1,unit['page'] or 1),unit['end_page'],order))
        await conn.execute("UPDATE learning_sessions SET status='ready',row_version=row_version+1 WHERE id=%s",(sid,))


@router.get('')
async def list_sessions(request:Request):
    principal=await require_user(request)
    async with request.app.state.runtime.db.tx(principal.ctx) as conn:
        return public(await rows(conn,'SELECT id,title,learning_goal,status,created_at,updated_at FROM learning_sessions ORDER BY updated_at DESC'))


@router.post('',status_code=201)
async def create(body:CreateSession,request:Request):
    principal=await require_user(request);runtime=request.app.state.runtime
    ids=list(dict.fromkeys(parse_id(i,'Document') for i in body.document_ids))
    async with runtime.db.tx(principal.ctx) as conn:
        profile=await runtime.profiles.active_id(conn)
        docs=[await library.get_item(conn,i,profile) for i in ids]
        if len(ids)<2 or any(not d or library.status_of(d)[0]!='ready' for d in docs):raise HTTPException(409,'Select at least two ready materials')
        sid=(await one(conn,'''INSERT INTO learning_sessions (workspace_id,created_by,title,learning_goal)
                                 VALUES (%s,%s,%s,%s) RETURNING id''',(principal.workspace_id,principal.user_id,body.title,body.learning_goal)))['id']
        for d in docs:
            await conn.execute('INSERT INTO learning_materials (workspace_id,session_id,library_item_id,material_type) VALUES (%s,%s,%s,%s)',
                               (principal.workspace_id,sid,d['id'],d['kind']))
        return await get_session(conn,sid)


@router.get('/{sid}')
async def get(sid:str,request:Request):
    principal=await require_user(request)
    async with request.app.state.runtime.db.tx(principal.ctx) as conn:
        return await get_session(conn,parse_id(sid,SPACE))


class Rebuild(BaseModel):
    """Themes the learner typed. Empty means: let the tutor model propose them."""
    themes:list[str]=Field(default_factory=list,max_length=12)


@router.post('/{sid}/generate')
async def generate(sid:str,request:Request,body:Rebuild|None=None):
    principal=await require_user(request);runtime=request.app.state.runtime;space=parse_id(sid,SPACE)
    async with runtime.db.tx(principal.ctx) as conn:
        current=await get_session(conn,space)
        docs=[await load_item(conn,parse_id(m['document_id'])) for m in current['materials']]
    custom=[name.strip()[:80] for name in (body.themes if body else []) if name.strip()]
    await build_map(runtime,principal,space,current['learning_goal'],[d for d in docs if d],custom)
    async with runtime.db.tx(principal.ctx) as conn:
        return await get_session(conn,space)


class Progress(BaseModel):
    done:bool


@router.post('/{sid}/chapters/{theme_id}/{document_id}/{chapter_id}')
async def mark(sid:str,theme_id:str,document_id:str,chapter_id:str,body:Progress,request:Request):
    principal=await require_user(request);space=parse_id(sid,SPACE)
    async with request.app.state.runtime.db.tx(principal.ctx) as conn:
        await get_session(conn,space)
        updated=await one(conn,'''UPDATE learning_theme_chapters SET done=%s,done_at=CASE WHEN %s THEN now() END
            WHERE session_id=%s AND theme_id=%s AND library_item_id=%s AND chapter_key=%s RETURNING theme_id''',
            (body.done,body.done,space,parse_id(theme_id,'Topic'),parse_id(document_id,'Document'),chapter_id))
        if not updated:raise HTTPException(404,'Chapter not found in this study map')
        await conn.execute('UPDATE learning_sessions SET updated_at=now() WHERE id=%s',(space,))
        return await get_session(conn,space)


async def theme_row(conn,sid,tid):
    row=await one(conn,'SELECT * FROM learning_themes WHERE id=%s AND session_id=%s',(tid,sid))
    if not row:raise HTTPException(404,'That topic is not on this study map')
    chapters=await rows(conn,'''SELECT library_item_id AS document_id,page_start AS page,page_end AS end_page
                                 FROM learning_theme_chapters WHERE theme_id=%s AND session_id=%s ORDER BY position''',(tid,sid))
    return row,chapters


async def theme_evidence(runtime,principal,theme,chapters):
    """Passages drawn only from the chapters on this topic, so every citation is a page
    the learner can actually open. The numbering is built here, never by the model."""
    question=theme['title']+'. '+(theme['summary'] or theme['title'])
    vector=(await runtime.embeddings.embed([question],query=True))[0]
    evidence=[]
    async with runtime.db.tx(principal.ctx) as conn:
        profile=await runtime.profiles.active_id(conn)
        for document_id in dict.fromkeys(c['document_id'] for c in chapters):
            doc=await library.get_item(conn,document_id,profile)
            if not doc or doc['set_state']!='ready' or profile is None:continue
            ranges=[(c['page'],c['end_page']) for c in chapters if c['document_id']==document_id]
            kept=[]
            for match in await library.search(conn,doc,profile,vector,10):
                page=match.get('page') or None
                # Markdown carries no pages, so its passages are accepted as they come.
                inside=page is None or any(page>=start and (end is None or page<end) for start,end in ranges)
                if inside:kept.append({'text':match['text'][:1100],'page':page,'title':doc['title'],'source_id':document_id.hex})
                if len(kept)>=3:break
            evidence.extend(kept)
    return evidence


def citation_links(evidence):
    links=[]
    for index,item in enumerate(evidence,1):
        url=f"/study/{item['source_id']}"+(f"?page={item['page']}" if item['page'] else '')
        links.append({'n':index,'source_id':item['source_id'],'title':item['title'],
                      'page':item['page'],'url':url})
    return links


@router.post('/{sid}/themes/{tid}/lesson')
async def lesson(sid:str,tid:str,request:Request):
    principal=await require_user(request);runtime=request.app.state.runtime
    space,topic=parse_id(sid,SPACE),parse_id(tid,'Topic')
    async with runtime.db.tx(principal.ctx) as conn:
        await get_session(conn,space)
        theme,chapters=await theme_row(conn,space,topic)
    if not chapters:raise HTTPException(409,'This topic has no chapters to teach from.')
    evidence=await theme_evidence(runtime,principal,theme,chapters)
    if not evidence:
        raise HTTPException(409,'No indexed passages were found for this topic. Open each material once so it finishes indexing, then try again.')
    citations=citation_links(evidence)
    from .research import model_text
    choice=await choice_for(runtime,principal.ctx)
    body='\n\n'.join(f"[S{item['n']}] {item['title']}"+(f", page {item['page']}" if item['page'] else '')+
                     '\n'+source['text']
                     for item,source in zip(citations,evidence))
    instruction=('Teach this one topic to a learner, using only the numbered excerpts. '
        'Write Markdown with these headings exactly: "## What this really means", '
        '"## How the sources approach it", "## Where they differ", "## Use it", "## Practice". '
        'Under "Where they differ", say plainly if the sources simply cover different ground. '
        'Cite every factual sentence with the excerpt it came from, like [S1]. '
        'Never invent a fact, a page or a source. Under "Practice" give one short task the '
        'learner can do and what a good answer would contain. Keep it under 500 words.\n'
        'Output only the Markdown document. Do not restate the task and do not think out '
        'loud. Your first characters must be "## What this really means".')
    try:
        draft=await model_text(runtime,instruction,
            f"TOPIC: {theme['title']}\nWHY IT MATTERS: {theme['summary']}\n\nEXCERPTS:\n{body}",3000,choice)
    except ProviderError as exc:
        raise HTTPException(502,str(exc)) from None
    # Reasoning models often narrate before answering; the lesson starts at its first heading.
    start=draft.find('## ')
    if start<0:
        raise HTTPException(502,'The tutor model did not return a usable lesson. Try again, or pick a stronger tutor in Tutor settings.')
    draft=draft[start:].strip()
    known={f"S{item['n']}" for item in citations}
    # Drop any reference the model invented; a citation must point at a real excerpt.
    draft=re.sub(r'\[(S\d+)\]',lambda m:m[0] if m[1] in known else '',draft)
    practice=''
    match=re.search(r'##\s*Practice\s*\n(.+)$',draft,re.S)
    if match:practice=match.group(1).strip()[:2000]
    async with runtime.db.tx(principal.ctx) as conn:
        await conn.execute('UPDATE learning_themes SET lesson=%s,citations=%s,exercise=%s WHERE id=%s',
                           (draft,Jsonb(citations),practice,topic))
        await conn.execute('UPDATE learning_sessions SET updated_at=now() WHERE id=%s',(space,))
        return await get_session(conn,space)


@router.post('/{sid}/themes/{tid}/answer')
async def answer(sid:str,tid:str,body:Answer,request:Request):
    principal=await require_user(request);runtime=request.app.state.runtime
    space,topic=parse_id(sid,SPACE),parse_id(tid,'Topic')
    async with runtime.db.tx(principal.ctx) as conn:
        current=await get_session(conn,space)
        theme,_=await theme_row(conn,space,topic)
    if not theme['exercise']:raise HTTPException(409,'Build the lesson for this topic first.')
    from .research import model_text
    choice=await choice_for(runtime,principal.ctx)
    instruction=('You are marking a learner. Reply in Markdown, under 200 words, with: one line on '
        'what they got right, one line on what is missing or wrong, and one concrete next step. '
        'Judge only against the task and the lesson. Be specific about their actual words. '
        'If the answer is empty, off-topic or nonsense, say so plainly instead of praising it.')
    try:
        feedback=await model_text(runtime,instruction,
            f"TASK:\n{theme['exercise']}\n\nLESSON:\n{theme['lesson'][:3000]}\n\nLEARNER'S ANSWER:\n{body.answer}",700,choice)
    except ProviderError as exc:
        raise HTTPException(502,str(exc)) from None
    # Chat models like to open with "Okay, here's the feedback:" — drop that line.
    feedback=re.sub(r'^\s*(okay|sure|alright|here)[^\n]{0,80}:\s*\n+','',feedback,flags=re.I)
    async with runtime.db.tx(principal.ctx) as conn:
        record=await one(conn,'''INSERT INTO exercise_answers (workspace_id,session_id,author_id,block_id,notebook_version,answer,feedback)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id,session_id,block_id,answer,feedback,created_at''',
                         (principal.workspace_id,space,principal.user_id,topic.hex,current['row_version'],body.answer,feedback))
    return public(record)
