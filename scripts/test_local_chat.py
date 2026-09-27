"""Test both example documents through the running local tutor; never calls hosted chat.
Copy the example PDFs into library/ and Refresh before running.
"""
import json,urllib.request
base='http://localhost:8000'
def req(path,data=None):
 r=urllib.request.urlopen(urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'}),timeout=600)
 return r.read().decode()
s=json.loads(req('/status'));print('STATUS',s['status'],flush=True)
local=next((m for m in s['tutor']['models'] if m['provider']=='ollama'),None)
assert local, 'Pull a local chat model first'
print('LOCAL MODEL',local['model'],flush=True)
docs=[d for d in json.loads(req('/api/documents')) if d['filename'] in ('helios-workshop.pdf','river-garden.pdf')]
assert len(docs)==2 and all(d['status']=='ready' for d in docs), 'Ingest both example PDFs first'
for d in docs:
 if d['status']!='ready':continue
 question='What color is the reference tile and how many minutes must the operator wait?' if 'helios' in d['title'].lower() else 'What color is the moisture marker and how many liters of water does each bed receive?'
 for stream in [False,True]:
  answer=req('/v1/chat/completions',{'model':'studyhub-doc-'+d['id']+'--'+local['id'],'stream':stream,'messages':[{'role':'user','content':question}]})
  if stream:answer=''.join(json.loads(l[6:])['choices'][0]['delta'].get('content','') for l in answer.splitlines() if l.startswith('data: {'))
  else:answer=json.loads(answer)['choices'][0]['message']['content']
  print(d['title'],stream,answer,flush=True)
  expected=['cobalt','17'] if 'helios' in d['title'].lower() else ['amber','9']
  assert all(x in answer.lower() for x in expected),answer
print('PASS local real document answers, streamed and nonstreamed',flush=True)
