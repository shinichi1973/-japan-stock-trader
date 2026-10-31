"""JSON-only shared cache. No executable deserialization; atomic local writes."""
import os, json, gzip, hashlib, tempfile, io, zipfile
from pathlib import Path
from datetime import datetime, timezone
from functools import wraps
import numpy as np
import pandas as pd

SCHEMA = 1
ROOT = Path(os.environ.get('PRECOMPUTED_CACHE_DIR', 'cache_store'))
_STATS = {'hit': 0, 'miss': 0}

def encode(x):
    if isinstance(x, pd.DataFrame):
        return {'__frame__': x.to_json(orient='split', date_format='iso', double_precision=15), 'attrs': encode(x.attrs), 'index_name': x.index.name, 'columns_name': x.columns.name}
    if isinstance(x, (pd.Timestamp, datetime)):
        return {'__date__': x.isoformat()}
    if x is pd.NaT: return None
    if isinstance(x, dict): return {str(k): encode(v) for k,v in x.items()}
    if isinstance(x, (list, tuple)): return [encode(v) for v in x]
    if isinstance(x, np.generic): return encode(x.item())
    if isinstance(x, float) and not np.isfinite(x): return {'__float__': str(x)}
    return x

def decode(x):
    if isinstance(x, list): return [decode(v) for v in x]
    if isinstance(x, dict):
        if '__frame__' in x:
            f = pd.read_json(io.StringIO(x['__frame__']), orient='split', convert_dates=False)
            f.index = pd.to_datetime(f.index)
            f.attrs = decode(x['attrs'])
            f.index.name = x.get('index_name')
            f.columns.name = x.get('columns_name')
            return f
        if '__date__' in x: return pd.Timestamp(x['__date__'])
        if '__float__' in x: return float(x['__float__'])
        return {k: decode(v) for k,v in x.items()}
    return x

def key(kind, args):
    return hashlib.sha256(json.dumps(encode([SCHEMA, kind, args]), sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def read(kind, args, max_age=21600):
    try:
        with gzip.open(ROOT / (key(kind,args)+'.json.gz'), 'rt', encoding='utf-8') as f: p=json.load(f)
        age=datetime.now(timezone.utc).timestamp()-p['written']
        if p['schema'] != SCHEMA or not 0 <= age <= max_age: raise ValueError('expired')
        _STATS['hit'] += 1
        return decode(p['value'])
    except (OSError, ValueError, KeyError, TypeError):
        _STATS['miss'] += 1
        return None

def write(kind,args,value):
    if value is None or isinstance(value,pd.DataFrame) and value.empty: return
    ROOT.mkdir(parents=True, exist_ok=True)
    path=ROOT / (key(kind,args)+'.json.gz')
    payload={'schema':SCHEMA,'written':datetime.now(timezone.utc).timestamp(),'value':encode(value)}
    temp=None
    try:
        with tempfile.NamedTemporaryFile(dir=ROOT, delete=False) as f: temp=f.name
        with gzip.open(temp,'wt',encoding='utf-8') as f: json.dump(payload,f,ensure_ascii=False,allow_nan=False)
        os.replace(temp,path)
    except (OSError, ValueError, TypeError):
        pass  # A cache write must never block normal calculation.
    finally:
        if temp and os.path.exists(temp): os.unlink(temp)

def install(ns):
    """Same functions and arguments in worker/UI; no account or order data stored."""
    version=ns['BUILD']
    now=ns['tokyo_now']
    def phase(): return (str(now().date()), now().hour >= 16)
    # Pure indicators: input values, attributes and algorithm BUILD are part of key.
    for name in ('_add_indicators','_market_indicators','_prepare_light_history_frame',
                 'stoch_prepare_light','rsi_prepare_light','bb_prepare_light','stoch_signal_quality'):
        original=ns[name]
        def factory(fn,label):
            @wraps(fn)
            def wrapped(frame,*a,**kw):
                args=[version,frame,a,kw]
                result=read(label,args,7*86400)
                if result is not None: return result
                result=fn(frame,*a,**kw); write(label,args,result); return result
            return wrapped
        ns[name]=factory(original,name)
    original_finance=ns['_domestic_finance_snapshot']
    def finance(t):
        args=[version,phase(),t]
        result=read('finance',args)
        if isinstance(result,dict):
            payload={'fetched_at':result.get('_fetched_at'), 'values':result}
            if ns['_finance_cache_fresh'](payload,now()):
                return result
        result=original_finance(t)
        if ns['_finance_cache_fresh']({'fetched_at':result.get('_fetched_at'),'values':result},now()):
            write('finance',args,result)
        return result
    ns['_domestic_finance_snapshot']=finance
    for name in ('batch_domestic_stock_data','batch_stock_data_light'):
        original=ns[name]
        def factory(fn,label):
            @wraps(fn)
            def batch(tickers,*a,**kw):
                # refresh_token varies by caller but not price semantics; preserve other parameters.
                params=list(a)
                options=dict(kw)
                if label=='batch_domestic_stock_data':
                    if params: params[0]=''
                    options.pop('refresh_token',None)
                args=[version,phase(),params,options]
                hits={}; missing=[]; audits=[]
                for t in tickers:
                    saved=read(label,[args,t])
                    if saved is None: missing.append(t)
                    else:
                        hits[t]=saved['frame']
                        if saved.get('audit'): audits.append(saved['audit'])
                if missing:
                    fetched=fn(tuple(missing),*a,**kw)
                    prices, audit = fetched if label=='batch_domestic_stock_data' else (fetched,pd.DataFrame())
                    for t,frame in prices.items():
                        rows=audit.loc[audit['コード'].astype(str).eq(t.removesuffix('.T'))] if not audit.empty else pd.DataFrame()
                        record=rows.iloc[0].to_dict() if not rows.empty else None
                        hits[t]=frame
                        # Existing freshness flags still apply; cache only completed-day frames.
                        today=pd.Timestamp(now().date())
                        cutoff=today if now().hour>=16 else today-pd.Timedelta(days=1)
                        while cutoff.weekday()>=5: cutoff-=pd.Timedelta(days=1)
                        latest=pd.Timestamp(frame.index.max()).tz_localize(None).normalize()
                        if latest==cutoff:
                            write(label,[args,t],{'frame':frame,'audit':record})
                    if not audit.empty: audits.extend(audit.to_dict('records'))
                ordered={t:hits[t] for t in tickers if t in hits}
                return (ordered,pd.DataFrame(audits)) if label=='batch_domestic_stock_data' else ordered
            return batch
        ns[name]=factory(original,name)

def import_bundle(url, token=""):
    """Fetch once per Streamlit session. Validate entries; never extract arbitrary paths."""
    import requests
    headers={"Authorization": "Bearer "+token} if token else {}
    r=requests.get(url,headers=headers,timeout=30); r.raise_for_status()
    if len(r.content)>100*1024*1024: raise ValueError('キャッシュが100MBを超えています')
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        if sum(i.file_size for i in z.infolist())>150*1024*1024: raise ValueError('展開サイズ超過')
        for info in z.infolist():
            import re
            if not re.fullmatch(r'[0-9a-f]{64}\.json\.gz',info.filename): continue
            data=z.read(info)
            # Validate JSON now, before replacing an existing good file.
            p=json.loads(gzip.decompress(data))
            if p.get('schema')!=SCHEMA: continue
            ROOT.mkdir(parents=True,exist_ok=True)
            dest=ROOT/info.filename
            if dest.exists():
                try:
                    with gzip.open(dest,'rt',encoding='utf-8') as f: old=json.load(f)
                    if old['written']>=p['written']: continue
                except (OSError,ValueError,KeyError): pass
            with tempfile.NamedTemporaryFile(dir=ROOT,delete=False) as f: f.write(data); temp=f.name
            os.replace(temp,dest)
