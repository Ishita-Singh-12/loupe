"""Real local tool execution and spans. Optional paid-provider calls are never automatic."""
import argparse
import os
import time
from loupe import configure, trace, Span, patch_openai

DOCUMENTS = [
    ('export-reliability', 'Buffer spans in a bounded queue. Export in batches on a background thread. Retry transient failures.'),
    ('trace-model', 'A trace represents an agent run. Spans represent individual tools and model calls, linked by parent IDs.'),
    ('cost-policy', 'Keep raw token counts. Unknown models must remain unpriced. Estimates are not provider invoices.'),
    ('privacy', 'Do not capture prompts, responses or secret keys by default. Keep metadata minimal.'),
]

@trace('execute_tool search_docs', attributes={'gen_ai.operation.name':'execute_tool','gen_ai.tool.name':'search_docs'})
def search_docs(question):
    terms=set(question.lower().split())
    ranked=sorted(DOCUMENTS,key=lambda d:len(terms & set(d[1].lower().split())),reverse=True)
    time.sleep(.035)
    return ranked[:2]

@trace('execute_tool validate_sources', attributes={'gen_ai.operation.name':'execute_tool','gen_ai.tool.name':'validate_sources'})
def validate_sources(documents, fail=False):
    time.sleep(.025)
    if fail: raise TimeoutError('Simulated downstream timeout for the failure demo')
    return all(title and text for title,text in documents)

@trace('execute_tool compose_answer', attributes={'gen_ai.operation.name':'execute_tool','gen_ai.tool.name':'compose_answer'})
def compose_answer(documents):
    time.sleep(.02)
    return ' '.join(text for _,text in documents)

@trace('documentation agent')
def run(question, fail=False, provider=False):
    documents=search_docs(question)
    validate_sources(documents,fail)
    if provider:
        from openai import OpenAI
        response=OpenAI().chat.completions.create(model=os.environ.get('LOUPE_DEMO_MODEL','gpt-4o-mini'),messages=[{'role':'user','content':'Answer briefly using these docs: '+str(documents)+' Question: '+question}])
        return response.choices[0].message.content
    return compose_answer(documents)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--openai',action='store_true',help='Opt-in provider call; requires OPENAI_API_KEY and may incur provider charges')
    parser.add_argument('--runs',type=int,default=8)
    args=parser.parse_args()
    if args.openai and not os.environ.get('OPENAI_API_KEY'):
        parser.error('--openai requires OPENAI_API_KEY')
    client=configure(endpoint=os.environ.get('LOUPE_ENDPOINT','http://127.0.0.1:4173'),api_key=os.environ.get('LOUPE_API_KEY'),service_name='docs-agent',flush_interval=.3)
    if args.openai: patch_openai()
    questions=['How should I export spans without blocking?','How do trace spans connect?','What is the cost policy?','How do we protect privacy?']
    for i in range(args.runs):
        try:
            answer=run(questions[i%len(questions)],fail=i%4==3,provider=args.openai)
            print('Run',i+1,'completed:',answer[:90])
        except TimeoutError:
            print('Run',i+1,'failed: intentionally simulated tool timeout')
    ok=client.flush(timeout=10)
    print('Export:',client.diagnostics)
    client.close()
    if not ok: raise SystemExit('Some spans were not exported; check the API connection')
