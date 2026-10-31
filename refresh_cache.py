"""Headless refresh: load definitions only. Streamlit screens/orders never execute."""
import ast, os, argparse, json, zipfile, time
from pathlib import Path
from precomputed_cache import install, ROOT, _STATS

def load_definitions(path):
    tree=ast.parse(Path(path).read_text(encoding='utf-8'))
    body=[]
    for n in tree.body:
        if isinstance(n,(ast.Import,ast.ImportFrom)) and n.lineno < 45: body.append(n)
        elif isinstance(n,(ast.FunctionDef,ast.ClassDef)):
            if isinstance(n,ast.FunctionDef): n.decorator_list=[]
            body.append(n)
        elif isinstance(n,ast.Assign) and n.lineno<5500:
            names=[x.id for x in n.targets if isinstance(x,ast.Name)]
            if names and all(x.isupper() for x in names): body.append(n)
    ns={'__name__':'cache_worker'}
    exec(compile(ast.Module(body=body,type_ignores=[]),str(path),'exec'),ns)
    install(ns)
    return ns

def main():
    p=argparse.ArgumentParser();p.add_argument('--app',default='app.py');p.add_argument('--limit',type=int,default=300)
    p.add_argument('--codes',default='');p.add_argument('--output',default='cache_bundle.zip');a=p.parse_args()
    ns=load_definitions(a.app)
    if a.codes:
        symbols=tuple(x.strip().removesuffix('.T')+'.T' for x in a.codes.split(',') if x.strip())
    else:
        universe=ns['discover_domestic_topix_turnover100'](a.limit)
        if universe.empty: raise RuntimeError('対象一覧を取得できません。既存キャッシュを維持します')
        symbols=tuple(universe['コード'].astype(str).map(lambda s:s+'.T'))
    # Same phase/arguments as the production call sites, for both history settings.
    phase=(str(ns['tokyo_now']().date()),ns['tokyo_now']().hour>=16)
    counts={}
    for bars in (150,300):
        frames,audit=ns['batch_domestic_stock_data'](symbols,phase,bars)
        counts['daily_'+str(bars)]=len(frames)
        for frame in frames.values():
            for label in ('stoch_prepare_light','rsi_prepare_light','bb_prepare_light','stoch_signal_quality'):
                ns[label](frame)
    counts['finance']=0
    for symbol in symbols:
        try:
            v=ns['_domestic_finance_snapshot'](symbol)
            if ns['finance_freshness_usable'](v.get('_freshness')): counts['finance']+=1
        except Exception: pass
    ROOT.mkdir(parents=True,exist_ok=True)
    # Bound storage. Keep only recently written entries, including previous successes.
    for f in ROOT.glob('*.json.gz'):
        if time.time()-f.stat().st_mtime>8*86400: f.unlink()
    with zipfile.ZipFile(a.output,'w',compression=zipfile.ZIP_STORED) as z:
        for f in ROOT.glob('*.json.gz'): z.write(f,f.name)
    report={'fetched_at':ns['tokyo_now']().isoformat(),'scope':len(symbols),'counts':counts,'cache':_STATS}
    Path('cache_status.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False))
    if not counts['daily_150'] and not counts['finance']: raise RuntimeError('取得失敗：公開用キャッシュは更新しません')

if __name__=='__main__': main()
