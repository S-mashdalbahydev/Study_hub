"""Persistent research jobs that turn web sources into reviewable Study Books."""
import asyncio
from datetime import datetime, timezone
import hashlib
import ipaddress
import io
import json
import re
import socket
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, model_validator

from . import jobs, library
from .auth import require_user
from .db import one, rows
from .ids import parse_id, public
from .providers import ProviderError


def now():
    return datetime.now(timezone.utc).isoformat()

router=APIRouter(prefix='/api/research')
ACTIVE={'created','planning','searching','reading_sources','checking_coverage','synthesizing','publishing'}

JSON_FIELDS=('progress','outline','content_sections')
PROJECT='Research project'


async def row(conn,jid,full=True):
    item=await one(conn,'''SELECT j.*, (SELECT li.id FROM library_items li WHERE li.research_job_id=j.id
                                          ORDER BY li.output_slot DESC LIMIT 1) AS document_id
                            FROM research_jobs j WHERE j.id=%s''',(jid,))
    if not item: raise HTTPException(404,'Research project not found')
    result=dict(item)
    result['source_document_id']=result.pop('source_item_id')
    result['urls']=list(result['urls'] or [])
    result['sources']=await rows(conn,'''SELECT id,title,url,author,publisher,published_at,retrieved_at,source_type,retained
                                        FROM research_sources WHERE job_id=%s ORDER BY seq''',(jid,))
    result['resources']=await rows(conn,'''SELECT id,chapter,title,url,resource_type,publisher,snippet,validation_reason,selected,
                                          thumbnail_url AS thumbnail,video_count
                                          FROM research_resources WHERE job_id=%s ORDER BY seq''',(jid,))
    if not full: result.pop('draft',None)
    return result


async def update(runtime,ctx,jid,status=None,*,fence=None,**values):
    """Worker writes pass `fence`: they commit only while that attempt still owns the job."""
    if status: values['status']=status
    for key in JSON_FIELDS:
        if key in values and isinstance(values[key],str): values[key]=Jsonb(json.loads(values[key]))
        elif key in values: values[key]=Jsonb(values[key])
    sets=','.join(f'{k}=%s' for k in values)
    transaction=jobs.fenced(runtime.db,fence,ctx) if fence else runtime.db.tx(ctx)
    async with transaction as conn:
        await conn.execute(f'UPDATE research_jobs SET {sets} WHERE id=%s',(*values.values(),jid))


async def current_status(runtime,ctx,jid):
    async with runtime.db.tx(ctx) as conn:
        return (await one(conn,'SELECT status FROM research_jobs WHERE id=%s',(jid,)) or {}).get('status')

def public_url(url):
    parsed=urlparse(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname: raise ProviderError('Only public HTTP or HTTPS URLs can be researched.')
    try:
        for info in socket.getaddrinfo(parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80)):
            address=info[4][0]
            if not ipaddress.ip_address(address).is_global:
                raise ProviderError('Private or local network URLs cannot be imported.')
    except socket.gaierror: raise ProviderError('The source domain could not be resolved.') from None
    return url

async def model_text(runtime,system,user,max_tokens=768,choice=None):
    model=await runtime.catalog.resolve('studyhub-auto',choice)
    messages=[{'role':'system','content':system},{'role':'user','content':user}]
    response=await (runtime.local_tutor.open(model,messages,False,max_tokens) if model.provider=='ollama' else runtime.tutor.open(messages,False,model))
    try:
        data=json.loads(await response.aread())
        text=(data.get('message') or data['choices'][0]['message']).get('content','').strip()
        if not text: raise ProviderError('The research model returned no text.')
        return text
    finally: await response.aclose()

async def plan_research(runtime,job,choice=None):
    prompt='''Create a concise research plan for the learner request. Return JSON only: {"title":"...","description":"...","questions":["..."],"outline":["Chapter title"],"search_queries":["..."]}. Generate subject-specific coverage, no generic fixed outline. Use 3 queries/4 chapters for quick, 5/6 for standard, 8/9 for deep. Audience: %s. Goal: %s.'''%(job['level'],job['goal'])
    raw=await model_text(runtime,'You plan reliable educational research. Do not claim you searched yet.',prompt+'\nRequest: '+job['query'],768,choice)
    match=re.search(r'\{.*\}',raw,re.S)
    try: data=json.loads(match.group() if match else raw)
    except Exception: data={}
    # Small local models can explain a useful plan while failing strict JSON. Keep the
    # model-authored fields that parsed and fill structural gaps deterministically.
    subject=job['query'].strip() or 'the supplied sources'
    counts={'quick':(3,4),'standard':(5,6),'deep':(8,9)}[job['depth']]
    data.setdefault('title',subject)
    data.setdefault('description',f'A source-grounded study guide about {subject}.')
    data.setdefault('questions',[f'What are the foundations of {subject}?',f'How is {subject} applied in practice?',f'What limits and tradeoffs matter for {subject}?'])
    data.setdefault('outline',['Foundations and vocabulary','How it works','Practical applications','Limits, tradeoffs, and next steps'])
    data.setdefault('search_queries',[subject,f'{subject} official guide',f'{subject} practical examples'])
    data['outline']=[str(x).strip() for x in data['outline'] if str(x).strip()][:counts[1]] or ['Foundations','Applications','Limits']
    data['search_queries']=[str(x).strip() for x in data['search_queries'] if str(x).strip()][:counts[0]] or [subject]
    return data

async def search_web(runtime,queries,limit,language='en'):
    results=[];seen=set()
    for query in queries:
        response=await runtime.client.get(runtime.settings.searxng+'/search',params={'q':query,'format':'json','language':language,'safesearch':1},timeout=30)
        response.raise_for_status()
        for item in response.json().get('results',[]):
            url=item.get('url','')
            if url and url not in seen and len(results)<limit: seen.add(url);results.append({'url':url,'search_title':item.get('title','')})
    return results

def document_chapters(runtime,document,depth):
    if not document or library.status_of(document)[0]!='ready': raise ProviderError('Choose a ready Library document for the companion.')
    outline=library.structure(runtime.storage,document).get('outline',[])
    from .notebook import FRONT_MATTER
    chapters=[]
    for index,item in enumerate(outline):
        # Books name chapters "CHAPTER 1", "Chapter 1." or "Chapter 1 — Title"; take them all.
        match=re.match(r'^chapter\s+(\d+)\b[\s.:\u2014-]*(.*)$',item['title'].strip(),re.I)
        if not match: continue
        title=(match[2].strip() or next((x['title'].strip() for x in outline[index+1:index+5]
                    if x.get('page')==item.get('page') and not re.match(r'^chapter\s+\d+\b',x['title'].strip(),re.I)),f"Chapter {match[1]}"))
        chapters.append({'number':int(match[1]),'title':title,'page':item.get('page') or 1})
    if not chapters:
        seen=set()
        for item in outline:
            title=item['title'].strip()
            # Copyright and Table of Contents are real headings but teach nothing.
            if not title or FRONT_MATTER.match(title) or item.get('level',1)>2: continue
            if title.lower() in seen: continue
            seen.add(title.lower());chapters.append({'number':len(chapters)+1,'title':title,'page':item.get('page') or 1})
    limit={'quick':3,'standard':6,'deep':20}[depth]
    return document,chapters[:limit]

def searchable_book_title(document):
    stored=document['title'].strip()
    prefix=re.sub(r'[^a-z0-9]+',' ',stored.lower()).strip()[:24]
    lines=[]
    for chunk in document['chunks'][:4]: lines.extend(chunk['text'].splitlines())
    candidates=[line.strip() for line in lines if prefix and re.sub(r'[^a-z0-9]+',' ',line.lower()).strip().startswith(prefix)]
    return min(candidates,key=len,default=stored)[:180]

def companion_units(chapters):
    """Keep short books granular and combine long books into digestible units."""
    size=1 if len(chapters)<=3 else (2 if len(chapters)<=8 else 3)
    units=[]
    for start in range(0,len(chapters),size):
        members=chapters[start:start+size]
        numbers=', '.join(str(x['number']) for x in members)
        title=' · '.join(x['title'] for x in members)
        units.append({'number':numbers,'title':title,'page':members[0]['page'],'chapters':members})
    return units

STOPWORDS={'the','and','for','with','from','into','your','this','that','how','what','are','was','were','have','has','will','can','not','but','you','their','them','then','than','over','under','about','being','been','when','where','which','who','whom','more','most','some','such','only','also','both','each','other','same','very','just','they','she','him','her','his','its','our','out','off','per'}
PLAYLIST_UNAVAILABLE_MARKERS=('this playlist does not exist','this playlist is private','this playlist has no videos','video unavailable')
BLOCKED_ARTICLE_DOMAINS=('dokumen.pub','pdfcoffee.com','reddit.com','quora.com')
CANDIDATES_PER_CATEGORY=6
RAW_RESULTS_TO_TRY=8
MAX_SELECTED_RESOURCES=4

def significant_words(text):
    return {w for w in re.findall(r'[a-zA-Z؀-ۿ]{4,}',(text or '').lower()) if w not in STOPWORDS}

def is_relevant(candidate_title,*reference_texts):
    """A candidate only counts as 'found' if its own title actually shares a real subject
    word with the unit/book it was searched for — otherwise any URL that merely mentions
    YouTube or looks like an article would silently pass as a match."""
    candidate_words=significant_words(candidate_title)
    if not candidate_words: return False
    reference_words=set()
    for text in reference_texts: reference_words|=significant_words(text)
    return bool(candidate_words & reference_words)

def youtube_host(url):
    return urlparse(url).netloc.lower().removeprefix('www.') in ('youtube.com','m.youtube.com','youtu.be')

def youtube_thumbnail(video_id):
    return f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg'


def playlist_details(raw,soup):
    """First video id and how many videos, read from the playlist page itself.

    The og:image already points at the first video's thumbnail, so the id is taken from
    there when the embedded data is not parseable."""
    first=None
    match=re.search(r'"videoId"\s*:\s*"([\w-]{6,})"',raw)
    if match:first=match[1]
    if not first:
        tag=soup.find('meta',attrs={'property':'og:image'})
        image=tag.get('content','') if tag else ''
        found=re.search(r'/vi/([\w-]{6,})/',image)
        if found:first=found[1]
    count=None
    for pattern in (r'"stats"\s*:\s*\[\s*\{\s*"runs"\s*:\s*\[\s*\{\s*"text"\s*:\s*"([\d,]+)"',
                    r'"videoCountText".{0,80}?"text"\s*:\s*"([\d,]+)',
                    r'([\d,]+)\s+videos?\b'):
        found=re.search(pattern,raw)
        if found:
            try:
                count=int(found[1].replace(',',''))
                break
            except ValueError:
                continue
    return first,count


async def validate_video(runtime,url,*refs):
    if not youtube_host(url) or 'list=' in url: return None
    match=re.search(r'(?:v=|youtu\.be/)([\w-]{6,})',url)
    if not match: return None
    canonical=f'https://www.youtube.com/watch?v={match[1]}'
    try: response=await runtime.client.get('https://www.youtube.com/oembed',params={'url':canonical,'format':'json'},timeout=20)
    except httpx.HTTPError: return None
    if response.status_code!=200: return None
    try: data=response.json()
    except Exception: return None
    title=(data.get('title') or '').strip();author=(data.get('author_name') or '').strip()
    if not title or not is_relevant(title,*refs): return None
    return {'url':canonical,'title':title,'publisher':author or 'youtube.com',
            'thumbnail':youtube_thumbnail(match[1]),'video_count':1,
            'validation_reason':f'YouTube confirmed this is a public video by {author}, matching this unit.' if author else 'YouTube confirmed this is a public video matching this unit.'}

async def validate_playlist(runtime,url,*refs):
    match=re.search(r'list=([\w-]+)',url)
    if not youtube_host(url) or not match: return None
    canonical=f'https://www.youtube.com/playlist?list={match[1]}'
    try: _,soup,raw=await fetch_html(runtime,canonical,timeout=20)
    except (httpx.HTTPError,ProviderError): return None
    body_text=soup.get_text(' ',strip=True).lower()
    if any(marker in body_text for marker in PLAYLIST_UNAVAILABLE_MARKERS): return None
    title_tag=soup.find('meta',attrs={'property':'og:title'})
    title=(title_tag['content'].strip() if title_tag and title_tag.get('content') else '') or (soup.title.string.strip() if soup.title and soup.title.string else '')
    title=re.sub(r'\s*-\s*YouTube\s*$','',title).strip()
    if not title or not is_relevant(title,*refs): return None
    first,count=playlist_details(raw,soup)
    return {'url':canonical,'title':title,'publisher':'youtube.com',
            'thumbnail':youtube_thumbnail(first) if first else '','video_count':count,
            'validation_reason':f'Confirmed a public YouTube playlist with {count} videos matching this unit.' if count
                                else 'Confirmed a public YouTube playlist page matching this unit.'}

async def validate_article(runtime,url,*refs):
    if youtube_host(url) or any(domain in urlparse(url).netloc.lower() for domain in BLOCKED_ARTICLE_DOMAINS): return None
    try: source=await fetch_source(runtime,{'url':url})
    except (httpx.HTTPError,ProviderError): return None
    if not is_relevant(source['title'],*refs): return None
    return {'url':source['url'],'title':source['title'],'publisher':source['publisher'],
            'thumbnail':'','video_count':None,
            'validation_reason':'Fetched a real, readable article matching this unit.'}

async def companion_resource_candidates(runtime,book_title,units):
    """Find several real candidates per category and keep only the ones that actually
    validate (reachable, public, and on-topic) — never a single first-hit guess and never
    a fabricated 'browse for it yourself' link dressed up as a discovered resource."""
    resources=[]
    for unit in units:
        topic=f'{book_title} {unit["title"]}';refs=(unit['title'],book_title)
        categories=(
            ('video','en',validate_video,[f'site:youtube.com "{topic}" explanation',f'site:youtube.com "{topic}" tutorial']),
            ('playlist_en','en',validate_playlist,[f'site:youtube.com/playlist "{topic}" course playlist',f'{topic} full course playlist']),
            ('playlist_ar','ar',validate_playlist,[f'site:youtube.com/playlist "{topic}" شرح دورة قائمة تشغيل',f'{topic} شرح كامل قائمة تشغيل']),
            ('article_or_demo','en',validate_article,[f'{topic} explanation examples exercises',f'{topic} guide tutorial']),
        )
        for kind,language,validator,queries in categories:
            raw=await search_web(runtime,queries,RAW_RESULTS_TO_TRY,language)
            found=[];seen_urls=set()
            for item in raw:
                if len(found)>=CANDIDATES_PER_CATEGORY: break
                try: candidate=await validator(runtime,item['url'],*refs)
                except ProviderError: continue
                if not candidate or candidate['url'] in seen_urls: continue
                seen_urls.add(candidate['url'])
                found.append({'id':'R'+hashlib.sha256((unit['title']+kind+candidate['url']).encode()).hexdigest()[:10],
                    'chapter':unit['title'],'title':candidate['title'],'url':candidate['url'],'resource_type':kind,
                    'publisher':candidate['publisher'],'snippet':'','validation_reason':candidate['validation_reason'],
                    'thumbnail':candidate.get('thumbnail',''),'video_count':candidate.get('video_count')})
            resources.extend(found)
    return resources

RESOURCE_LABELS={'video':'🎬 Video','playlist_en':'🇬🇧 English playlist','playlist_ar':'🇸🇦 قائمة تشغيل عربية','article_or_demo':'🔗 Read / explore'}
RESOURCE_ORDER=('video','playlist_en','playlist_ar','article_or_demo')

def companion_header(payload):
    return (f"# Learning Companion: {payload['document_title']}\n\n**Built from:** [{payload['document_title']}]({payload['source_url']})  \n"
            f"**Research Date:** {payload['research_date']}  \n**Learner:** {payload['level']}  \n**Goal:** {payload['goal']}\n\n"
            "> A visual, source-grounded path through the original material. Units may combine related chapters so the learning flow stays useful rather than repetitive.\n\n")

def companion_preview_draft(payload):
    """Shown during review, before the learner has curated any resources — never bakes in
    a link (real or fabricated) that hasn't been explicitly selected yet."""
    blocks=[f"> 📖 **Source reading:** [{u['members']} — open at page {u['page']}]({u['source_url']})\n\n"+u['content']+
            "\n\n> 🌐 External resources for this unit are chosen in the **Resources** tab before publishing." for u in payload['units']]
    return companion_header(payload)+'\n\n---\n\n'.join(blocks)

def companion_document_markdown(payload,resources):
    """Assembled only at publish time, from whatever the learner actually selected."""
    blocks=[]
    for unit in payload['units']:
        chapter_resources=sorted((r for r in resources if r['chapter']==unit['title'] and r.get('selected')),
            key=lambda r:RESOURCE_ORDER.index(r['resource_type']) if r['resource_type'] in RESOURCE_ORDER else len(RESOURCE_ORDER))
        links='\n'.join(f"- **{RESOURCE_LABELS.get(r['resource_type'],'Resource')}:** [{r['title']}]({r['url']}) — {r['publisher']}" for r in chapter_resources)
        if not links: links='- No useful external resource was retained for this chapter. Try updating this companion later.'
        blocks.append(f"> 📖 **Source reading:** [{unit['members']} — open at page {unit['page']}]({unit['source_url']})\n\n"+unit['content']+
            f"\n\n### 🌐 Learn another way\n{links}\n\n> YouTube links are discovered resources. Check the creator, language, and coverage before relying on them.")
    return companion_header(payload)+'\n\n---\n\n'.join(blocks)

async def companion_chapter(runtime,document,unit,next_page,resources,job,choice=None):
    chunks=[c for c in document['chunks'] if (c['page'] or 0)>=unit['page'] and (not next_page or (c['page'] or 0)<next_page)]
    evidence='\n\n'.join(c['text'] for c in chunks)[:6500]
    prompt='''Create one attractive, concise Markdown learning unit grounded only in the supplied source excerpt. It may cover one or several adjacent source chapters. Start with "## 🧭 Unit N: Title". Use these sections: "### 💡 Core ideas", "### 🧠 Mental model", "### 🔎 See it in context", "### ✍️ Check your understanding", and "### 🌱 Apply it". Use a short ASCII diagram, comparison table, timeline, decision tree, scenario, or concept map only when it truly clarifies this subject. Choose an application appropriate to the material: reflection, case analysis, calculation, role-play, checklist, observation, discussion, creative exercise, or technical build. A coding project is optional and must appear only when the source and learner goal call for one. Do not invent quotations, links, chapter names, legal/financial guidance, or current standards. Clearly separate source teaching from your illustrative examples. Refer to the original as [Book, page P]. Keep it complete and under 650 words.'''
    section=await model_text(runtime,prompt,json.dumps({'unit':unit,'source_excerpt':evidence,'learner_level':job['level'],'learner_goal':job['goal']},ensure_ascii=False),768,choice)
    section=re.sub(r'^##\s*🧭\s*Unit\s+N\s*:',f'## 🧭 Unit {unit["number"]}:',section,flags=re.I)
    if '### ✍️ Check your understanding' not in section:
        section+='\n\n### ✍️ Check your understanding\n- Explain one central idea in your own words.\n- Give an example and a counterexample.\n- Identify one uncertainty to revisit in the source.'
    if '### 🌱 Apply it' not in section:
        section+=f"\n\n### 🌱 Apply it\n- [ ] Choose one idea from {unit['title']} that matters to your goal.\n- [ ] Apply it to a real or imagined situation.\n- [ ] Record what changed in your understanding."
    source=f"/backend/api/documents/{document['id'].hex}/source#page="
    return re.sub(r'\[Book, page\s+(\d+)\]',lambda match:f"[Book, page {match[1]}]({source}{match[1]})",section,flags=re.I)

async def process_companion(runtime,ctx,jid,job,fence,choice):
    async with runtime.db.tx(ctx) as conn:
        document=await library.get_item(conn,job['source_document_id'],await runtime.profiles.active_id(conn)) if job['source_document_id'] else None
        if document: document['chunks']=await library.revision_chunks(conn,document['current_revision_id'])
    document,chapters=document_chapters(runtime,document,job['depth'])
    if not chapters: raise ProviderError('No usable chapter headings were found in this document.')
    units=companion_units(chapters)
    await update(runtime,ctx,jid,'searching',fence=fence,outline=[f"Unit {u['number']}: {u['title']}" for u in units],progress={'message':'Finding and validating videos, playlists, and articles…','completed':1,'total':len(units)+2})
    resources=await companion_resource_candidates(runtime,searchable_book_title(document),units)
    async with jobs.fenced(runtime.db,fence,ctx) as conn:
        await conn.execute('DELETE FROM research_resources WHERE job_id=%s',(jid,))
        async with conn.cursor() as cur:
            await cur.executemany('''INSERT INTO research_resources (workspace_id,job_id,id,chapter,title,url,resource_type,publisher,snippet,
                                         validation_reason,selected,thumbnail_url,video_count) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false,%s,%s)
                                         ON CONFLICT DO NOTHING''',
                [(ctx.workspace_id,jid,r['id'],r['chapter'],r['title'],r['url'],r['resource_type'],r['publisher'],r['snippet'],
                  r['validation_reason'],r.get('thumbnail',''),r.get('video_count')) for r in resources])
    content_sections=[]
    source_base=f"/backend/api/documents/{document['id'].hex}/source"
    for index,unit in enumerate(units):
        if await current_status(runtime,ctx,jid)=='cancelled': return
        await update(runtime,ctx,jid,'synthesizing',fence=fence,progress={'message':f"Building Unit {unit['number']}: {unit['title']}…",'completed':index+2,'total':len(units)+2})
        section=await companion_chapter(runtime,document,unit,units[index+1]['page'] if index+1<len(units) else None,resources,job,choice)
        members=', '.join(f"Chapter {c['number']}" for c in unit['chapters'])
        content_sections.append({'number':unit['number'],'title':unit['title'],'page':unit['page'],'members':members,
                                 'source_url':f"{source_base}#page={unit['page']}",'content':section})
    payload={'document_title':document['title'],'source_url':source_base,
             'research_date':datetime.now(timezone.utc).date().isoformat(),'level':job['level'],'goal':job['goal'],'units':content_sections}
    await update(runtime,ctx,jid,'review',fence=fence,draft=companion_preview_draft(payload),content_sections=payload,
                 progress={'message':'Companion ready for your review','completed':len(units)+2,'total':len(units)+2})

async def fetch_html(runtime,url,timeout=40):
    url=public_url(url)
    response=await runtime.client.get(url,follow_redirects=True,timeout=timeout,headers={'User-Agent':'StudyHub research reader/0.1'})
    response.raise_for_status()
    public_url(str(response.url))
    if len(response.content)>8*1024*1024: raise ProviderError('Source is too large to read as a web page.')
    ctype=response.headers.get('content-type','')
    if 'html' not in ctype and 'text/' not in ctype: raise ProviderError('Source is not a readable web page.')
    soup=BeautifulSoup(response.text,'html.parser')
    for tag in soup(['script','style','nav','footer','aside','form','noscript','svg']): tag.decompose()
    # The raw markup is returned alongside because YouTube keeps playlist metadata in the
    # script tags that are stripped above for article reading.
    return str(response.url),soup,response.text

def extract_article_text(soup):
    main=soup.find('article') or soup.find('main') or soup.body or soup
    parts=[]
    for tag in main.find_all(['h1','h2','h3','p','li','pre','blockquote','table']):
        text=' '.join(tag.get_text(' ',strip=True).split())
        if len(text)>=30: parts.append(('# ' if tag.name.startswith('h') else '')+text)
    return '\n\n'.join(parts)

async def fetch_source(runtime,item):
    url,soup,_=await fetch_html(runtime,item['url'])
    title=(soup.title.string.strip() if soup.title and soup.title.string else item.get('search_title')) or urlparse(url).netloc
    author=''
    for attrs in ({'name':'author'},{'property':'article:author'}):
        tag=soup.find('meta',attrs=attrs)
        if tag and tag.get('content'): author=tag['content'].strip();break
    content=extract_article_text(soup)
    if len(content)<300: raise ProviderError('The page did not contain enough readable article text.')
    return {'id':'S'+hashlib.sha256(url.encode()).hexdigest()[:10],'title':title[:300],'url':url,'author':author[:200] or None,
            'publisher':urlparse(url).netloc,'published_at':None,'retrieved_at':now(),'source_type':'web','excerpt':content[:12000]}

async def synthesize_section(runtime,plan,sources,job,choice=None):
    evidence='\n\n'.join(f"[{s['id']}] {s['title']} — {s['url']}\n{s['excerpt'][:3500]}" for s in sources[:5])
    instruction='''Write an original, accurate Markdown study book from the supplied evidence. Do not copy long passages. Every factual section must cite evidence using [Sxxxx]. Include: title, research date, description, table of contents, ordered chapters matching the outline, a short Coverage and limits section identifying evident gaps, Further directions, and References with source titles and URLs. Never invent a source or citation. Explain for a %s learner whose goal is %s. Depth: %s.'''
    draft=await model_text(runtime,instruction%(job['level'],job['goal'],job['depth']),json.dumps({'plan':plan,'evidence':evidence},ensure_ascii=False),768,choice)
    draft=re.sub(r'(?im)^\*{0,2}Research Date:\*{0,2}.*$',f"**Research Date:** {datetime.now(timezone.utc).date().isoformat()}",draft)
    known={s['id']:s for s in sources}
    # Small models often preserve the evidence but rewrite [S…] as [1], (S…),
    # or invent an ID. Normalize only references we can map to retained sources.
    def numeric(match):
        index=int(match[1])-1
        return f"[{sources[index]['id']}]" if 0 <= index < len(sources) else match[0]
    draft=re.sub(r'\[(\d{1,2})\]',numeric,draft)
    draft=re.sub(r'(?<!\[)(S[0-9a-f]{10})(?!\])',r'[\1]',draft)
    draft=re.sub(r'\[(S[0-9a-f]{10})\]',lambda m:m[0] if m[1] in known else '',draft)
    cited=set(re.findall(r'\[(S[0-9a-f]{10})\]',draft))
    citation_note=''
    if not cited:
        citation_note=('\n\n> **Citation note:** The selected tutor did not place dependable inline citations. '
                       'Use the verified evidence links below to check the draft before publishing.\n')
    draft=re.sub(r'\[(S[0-9a-f]{10})\](?!\()',lambda m:f"[{m[1]}]({known[m[1]]['url']})" if m[1] in known else m[0],draft)
    bibliography='\n\n## Verified source list\n'+''.join(f"\n- [{s['id']}] [{s['title']}]({s['url']}) — {s['publisher']}; retrieved {s['retrieved_at'][:10]}" for s in sources)
    return draft+citation_note+bibliography

async def handle_research(runtime,db,job):
    """One research attempt. Every write is fenced by this attempt's lease token."""
    from .tutors import choice_for
    ctx,jid,fence=job.ctx,job.target_id,job
    async with db.tx(ctx) as conn:
        record=await row(conn,jid)
    if record['status'] in ('cancelled','review','publishing','indexing','ready'):
        return
    choice=await choice_for(runtime,ctx)
    await update(runtime,ctx,jid,'planning',fence=fence,error=None,progress={'message':'Planning the learning path…','completed':0,'total':5})
    if record.get('material_type')=='learning_companion':
        await process_companion(runtime,ctx,jid,record,fence,choice);return
    plan=await plan_research(runtime,record,choice)
    if await current_status(runtime,ctx,jid)=='cancelled': return
    await update(runtime,ctx,jid,'searching',fence=fence,outline=plan['outline'],progress={'message':'Finding reliable sources…','completed':1,'total':5})
    candidates=[{'url':u} for u in record['urls']]
    if record['mode']=='topic': candidates+=await search_web(runtime,plan['search_queries'],{'quick':5,'standard':8,'deep':12}[record['depth']])
    if await current_status(runtime,ctx,jid)=='cancelled': return
    await update(runtime,ctx,jid,'reading_sources',fence=fence,progress={'message':'Reading and comparing sources…','completed':2,'total':5})
    sources=[]
    for item in candidates:
        if len(sources)>={'quick':3,'standard':5,'deep':8}[record['depth']]: break
        try: sources.append(await fetch_source(runtime,item))
        except (httpx.HTTPError,ProviderError): continue
    if await current_status(runtime,ctx,jid)=='cancelled': return
    minimum=1 if record['mode']=='url' else 2
    if len(sources)<minimum: raise jobs.PermanentFailure('Not enough readable sources were found. Try specific public URLs or a broader topic.')
    async with jobs.fenced(db,fence,ctx) as conn:
        await conn.execute('DELETE FROM research_sources WHERE job_id=%s',(jid,))
        async with conn.cursor() as cur:
            await cur.executemany('''INSERT INTO research_sources (workspace_id,job_id,id,title,url,author,publisher,published_at,
                                         retrieved_at,source_type,retained,excerpt) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,%s)
                                         ON CONFLICT DO NOTHING''',
                [(ctx.workspace_id,jid,x['id'],x['title'],x['url'],x['author'],x['publisher'],x['published_at'],x['retrieved_at'],
                  x['source_type'],x['excerpt']) for x in sources])
    await update(runtime,ctx,jid,'checking_coverage',fence=fence,progress={'message':'Checking coverage and gaps…','completed':3,'total':5})
    await update(runtime,ctx,jid,'synthesizing',fence=fence,progress={'message':'Writing your cited study book…','completed':4,'total':5})
    draft=await synthesize_section(runtime,plan,sources,record,choice)
    if await current_status(runtime,ctx,jid)=='cancelled': return
    await update(runtime,ctx,jid,'review',fence=fence,draft=draft,progress={'message':'Draft ready for your review','completed':5,'total':5})


async def on_research_failure(runtime,db,job,message):
    async with db.tx(job.ctx) as conn:
        await conn.execute("UPDATE research_jobs SET status='failed',error=%s WHERE id=%s AND status<>'cancelled'",(message,job.target_id))


async def enqueue_research(conn,principal,jid):
    await jobs.enqueue(conn,workspace_id=principal.workspace_id,queue='research',kind='research',target_type='research_job',
                       target_id=jid,requested_by=principal.user_id,dedupe_key=f'research:{jid}',max_attempts=2)

class Create(BaseModel):
    topic:str='' ; urls:list[str]=Field(default_factory=list,max_length=10)
    material_type:str='study_guide';source_document_id:str|None=None
    depth:str='standard'; level:str='intermediate'; goal:str='understand architecture and apply it'
    @model_validator(mode='after')
    def valid(self):
        self.topic=self.topic.strip();self.urls=[x.strip() for x in self.urls if x.strip()]
        if self.material_type not in ('study_guide','learning_companion'): raise ValueError('Invalid material type')
        if self.material_type=='learning_companion' and not self.source_document_id: raise ValueError('Choose a Library document')
        if self.material_type=='study_guide' and not self.topic and not self.urls: raise ValueError('Enter a topic or at least one URL')
        if self.depth not in ('quick','standard','deep') or self.level not in ('beginner','intermediate','advanced'): raise ValueError('Invalid research option')
        for url in self.urls: public_url(url)
        return self

@router.post('',status_code=202)
async def create(body:Create,request:Request):
    principal=await require_user(request);runtime=request.app.state.runtime
    mode='companion' if body.material_type=='learning_companion' else ('topic' if body.topic else 'url')
    key=request.headers.get('idempotency-key','').strip()
    digest=hashlib.sha256(body.model_dump_json().encode()).digest()
    async with runtime.db.tx(principal.ctx) as conn:
        if key:
            claimed=await one(conn,'''INSERT INTO idempotency_keys (actor_id,key,workspace_id,operation,request_hash,expires_at)
                                       VALUES (%s,%s,%s,'research.create',%s,now()+interval '24 hours')
                                       ON CONFLICT (actor_id,key) DO NOTHING RETURNING key''',(principal.user_id,key[:200],principal.workspace_id,digest))
            if not claimed:
                previous=await one(conn,'SELECT request_hash,status,response FROM idempotency_keys WHERE actor_id=%s AND key=%s',(principal.user_id,key[:200]))
                if previous and bytes(previous['request_hash'])!=digest:
                    raise HTTPException(422,'This request key was already used for a different request.')
                if previous and previous['status']=='done':
                    return previous['response']
                raise HTTPException(409,'This request is already being processed.')
        source=None
        if body.source_document_id:
            source=await library.get_item(conn,parse_id(body.source_document_id,'Library document'))
            if not source: raise HTTPException(404,'Library document not found')
        query=(f"Learning Companion: {source['title']}" if source else body.topic) or 'Create a study book from the supplied sources'
        created=await one(conn,'''INSERT INTO research_jobs (workspace_id,created_by,mode,material_type,source_item_id,query,urls,depth,level,goal,status)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'created') RETURNING id''',
                          (principal.workspace_id,principal.user_id,mode,body.material_type,source['id'] if source else None,query,
                           body.urls,body.depth,body.level,body.goal))
        await enqueue_research(conn,principal,created['id'])
        result=public(await row(conn,created['id'],False))
        if key:
            await conn.execute("UPDATE idempotency_keys SET status='done',response_status=202,response=%s WHERE actor_id=%s AND key=%s",
                               (Jsonb(result),principal.user_id,key[:200]))
    return result

@router.get('')
async def list_jobs(request:Request):
    principal=await require_user(request)
    async with request.app.state.runtime.db.tx(principal.ctx) as conn:
        ids=[x['id'] for x in await rows(conn,'SELECT id FROM research_jobs ORDER BY created_at DESC')]
        return [public(await row(conn,x,False)) for x in ids]

@router.get('/{jid}')
async def get(jid:str,request:Request):
    principal=await require_user(request)
    async with request.app.state.runtime.db.tx(principal.ctx) as conn:
        return public(await row(conn,parse_id(jid,PROJECT)))

@router.post('/{jid}/retry',status_code=202)
async def retry(jid:str,request:Request):
    principal=await require_user(request);project=parse_id(jid,PROJECT)
    async with request.app.state.runtime.db.tx(principal.ctx) as conn:
        current=await row(conn,project,False)
        if current['status'] not in ('failed','cancelled'): raise HTTPException(409,'Only a failed or cancelled project can be retried.')
        await conn.execute("UPDATE research_jobs SET status='created',error=NULL WHERE id=%s",(project,))
        await enqueue_research(conn,principal,project)
        return public(await row(conn,project,False))

@router.post('/{jid}/cancel')
async def cancel(jid:str,request:Request):
    principal=await require_user(request);project=parse_id(jid,PROJECT)
    async with request.app.state.runtime.db.tx(principal.ctx) as conn:
        await row(conn,project,False)
        await conn.execute("UPDATE research_jobs SET status='cancelled' WHERE id=%s AND status NOT IN ('ready','indexing')",(project,))
        await conn.execute("UPDATE jobs SET cancel_requested=true WHERE target_type='research_job' AND target_id=%s AND state IN ('queued','running')",(project,))
        await conn.execute("UPDATE jobs SET state='cancelled',finished_at=now(),error_code='cancelled' WHERE target_type='research_job' AND target_id=%s AND state='queued'",(project,))
        return public(await row(conn,project,False))

class Selection(BaseModel):
    selected:bool

@router.post('/{jid}/resources/{resource_id}/selection')
async def select_resource(jid:str,resource_id:str,body:Selection,request:Request):
    principal=await require_user(request);project=parse_id(jid,PROJECT)
    async with request.app.state.runtime.db.tx(principal.ctx) as conn:
        await row(conn,project,False)
        resource=await one(conn,'SELECT chapter,resource_type FROM research_resources WHERE job_id=%s AND id=%s FOR UPDATE',(project,resource_id))
        if not resource: raise HTTPException(404,'Resource candidate not found')
        if body.selected:
            count=(await one(conn,'''SELECT COUNT(*) AS n FROM research_resources WHERE job_id=%s AND chapter=%s AND resource_type=%s
                                       AND selected AND id<>%s''',(project,resource['chapter'],resource['resource_type'],resource_id)))['n']
            if count>=MAX_SELECTED_RESOURCES:
                raise HTTPException(409,f'You can select up to {MAX_SELECTED_RESOURCES} resources per category.')
        await conn.execute('UPDATE research_resources SET selected=%s WHERE job_id=%s AND id=%s',(body.selected,project,resource_id))
        return public(await row(conn,project,False))

def cover_image(title,label='RESEARCH STUDY BOOK'):
    from PIL import Image,ImageDraw,ImageFont
    image=Image.new('RGB',(720,960),'#20291d');draw=ImageDraw.Draw(image);font=ImageFont.load_default(size=42);small=ImageFont.load_default(size=20)
    draw.text((64,70),'STUDYHUB',fill='#c9d7ab',font=small)
    words=title.split();lines=[]
    while words:
        line=''
        while words and len(line+' '+words[0])<25: line=(line+' '+words.pop(0)).strip()
        lines.append(line or words.pop(0))
    draw.multiline_text((64,250),'\n'.join(lines[:7]),fill='#f3f4ed',font=font,spacing=20)
    draw.text((64,845),label,fill='#c9d7ab',font=small);draw.text((64,885),datetime.now().strftime('%B %Y'),fill='#aab3a2',font=small)
    buffer=io.BytesIO();image.save(buffer,format='JPEG',quality=90)
    return buffer.getvalue()

@router.post('/{jid}/publish',status_code=202)
async def publish(jid:str,request:Request):
    principal=await require_user(request);runtime=request.app.state.runtime;project=parse_id(jid,PROJECT)
    async with runtime.db.tx(principal.ctx) as conn:
        job=await one(conn,"SELECT * FROM research_jobs WHERE id=%s FOR UPDATE",(project,))
        if not job: raise HTTPException(404,'Research project not found')
        if job['status']!='review' or not job['draft']: raise HTTPException(409,'The research draft is not ready to publish')
        resources=await rows(conn,'SELECT chapter,title,url,resource_type,publisher,selected FROM research_resources WHERE job_id=%s ORDER BY seq',(project,))
        slot=(await one(conn,'SELECT COUNT(*) AS n FROM library_items WHERE research_job_id=%s',(project,)))['n']
        await conn.execute("UPDATE research_jobs SET status='publishing',progress=%s WHERE id=%s",
                           (Jsonb({'message':'Adding the Study Book to your library…','completed':5,'total':6}),project))
    companion=job['material_type']=='learning_companion'
    draft=companion_document_markdown(job['content_sections'],resources) if companion and job['content_sections'] else job['draft']
    raw=draft.encode();filename=(re.sub(r'[^A-Za-z0-9 _-]','',job['query'])[:80].strip().replace(' ','-') or 'study-book')+'.md'
    staged=runtime.storage.staging_file();staged.write_bytes(raw)
    item_id,created=await library.ingest_file(runtime,principal.ctx,staged,filename,
        origin='learning_companion' if companion else 'research_book',research_job_id=project,output_slot=slot,title=job['query'][:300])
    await asyncio.to_thread(runtime.storage.put_bytes,f'items/{item_id.hex}/cover.jpg',
                            cover_image(job['query'],'LEARNING COMPANION' if companion else 'RESEARCH STUDY BOOK'))
    async with runtime.db.tx(principal.ctx) as conn:
        if created and job['source_item_id']:
            await conn.execute('INSERT INTO item_derivations (workspace_id,item_id,source_item_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                               (principal.workspace_id,item_id,job['source_item_id']))
        # Identical bytes already in the library: that copy is the result, nothing new to index.
        status,message=('indexing','Creating the study index…') if created else ('ready','This Study Book is already in your library.')
        await conn.execute("UPDATE research_jobs SET status=%s,review_state='research_complete',progress=%s WHERE id=%s",
                           (status,Jsonb({'message':message,'completed':5 if created else 6,'total':6}),project))
        return public(await row(conn,project,False))

@router.get('/{jid}/source/{source_id}')
async def source(jid:str,source_id:str,request:Request):
    principal=await require_user(request)
    async with request.app.state.runtime.db.tx(principal.ctx) as conn:
        found=await one(conn,'SELECT title,url,author,publisher,retrieved_at,excerpt FROM research_sources WHERE job_id=%s AND id=%s',
                        (parse_id(jid,PROJECT),source_id))
    if not found: raise HTTPException(404,'Research source not found')
    return public(found)
