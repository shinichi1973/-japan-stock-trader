"""Headless refresh: load definitions only. Streamlit screens/orders never execute."""
import ast, os, argparse, json, zipfile, time, subprocess, sys
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from precomputed_cache import install, ROOT, _STATS

def load_definitions(path):
    tree=ast.parse(Path(path).read_text(encoding='utf-8'))
    body=[]
    resources=[]
    for n in tree.body:
        if isinstance(n,(ast.Import,ast.ImportFrom)) and n.lineno < 45: body.append(n)
        elif isinstance(n,(ast.FunctionDef,ast.ClassDef)):
            if isinstance(n,ast.FunctionDef):
                if any('cache_resource' in ast.unparse(d) for d in n.decorator_list):
                    resources.append(n.name)
                n.decorator_list=[]
            body.append(n)
        elif isinstance(n,ast.Assign) and n.lineno<5500:
            names=[x.id for x in n.targets if isinstance(x,ast.Name)]
            if names and all(x.isupper() for x in names): body.append(n)
    ns={'__name__':'cache_worker'}
    exec(compile(ast.Module(body=body,type_ignores=[]),str(path),'exec'),ns)
    # Keep resource identity without running any Streamlit screen code.
    for name in resources:
        ns[name] = lru_cache(maxsize=None)(ns[name])
    # Successful pages may be reused within this run (150/300 bars and finance).
    ns['_fetch_free_finance_html'] = lru_cache(maxsize=4096)(ns['_fetch_free_finance_html'])
    install(ns)
    return ns

def log(message):
    print(message, flush=True)


def save_report(report):
    target = Path('cache_status.json')
    temp = target.with_suffix('.json.tmp')
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, target)


def worker(a):
    log('START: load application definitions')
    ns = load_definitions(a.app)
    log('START: discover symbols')
    if a.codes:
        symbols = tuple(dict.fromkeys(x.strip().removesuffix('.T')+'.T'
                                     for x in a.codes.split(',') if x.strip()))
    else:
        universe = ns['discover_domestic_topix_turnover100'](a.limit)
        if universe.empty:
            raise RuntimeError('対象一覧を取得できません。既存キャッシュを維持します')
        symbols = tuple(dict.fromkeys(universe['コード'].astype(str).map(lambda s:s+'.T')))
    now = ns['tokyo_now']()
    phase = (str(now.date()), now.hour >= 16)
    counts = {'daily_150':0, 'daily_300':0, 'finance':0}
    report = {'fetched_at':now.isoformat(), 'scope':len(symbols), 'counts':counts,
              'cache':_STATS, 'complete':False, 'stage':'daily', 'finance_processed':0,
              'finance_errors':0}
    save_report(report)
    for bars in (150, 300):
        for start in range(0, len(symbols), 10):
            chunk = symbols[start:start+10]
            log(f'DAILY {bars}: {start+1}-{start+len(chunk)}/{len(symbols)} start')
            frames, audit = ns['batch_domestic_stock_data'](chunk, phase, bars)
            counts['daily_'+str(bars)] += len(frames)
            for symbol, frame in frames.items():
                for label in ('stoch_prepare_light','rsi_prepare_light','bb_prepare_light','stoch_signal_quality'):
                    try:
                        ns[label](frame)
                    except Exception as exc:
                        log(f'INDICATOR {symbol} {label}: {type(exc).__name__}: {str(exc)[:200]}')
            report['stage'] = f'daily_{bars}'
            save_report(report)
            log(f'DAILY {bars}: finished {start+len(chunk)}/{len(symbols)}, frames={len(frames)}')
    def finance(symbol):
        log(f'FINANCE {symbol}: start')
        try:
            value = ns['_domestic_finance_snapshot'](symbol)
            ok = bool(ns['finance_freshness_usable'](value.get('_freshness')))
            log(f'FINANCE {symbol}: '+str(value.get('_freshness', 'unknown')))
            return ok, False
        except Exception as exc:
            log(f'FINANCE {symbol}: {type(exc).__name__}: {str(exc)[:300]}')
            return False, True
    report['stage'] = 'finance'
    # Share the original host pacing, cooldown and thread-local sessions.
    ns['_finance_transport_state']()
    with ThreadPoolExecutor(max_workers=2) as pool:
        for ok, error in pool.map(finance, symbols):
            counts['finance'] += int(ok)
            report['finance_errors'] += int(error)
            report['finance_processed'] += 1
            save_report(report)
    report['complete'] = True
    report['stage'] = 'finished'
    save_report(report)


def package(a, timed_out):
    report = json.loads(Path('cache_status.json').read_text(encoding='utf-8'))
    counts = report.get('counts', {})
    if not (counts.get('daily_150') or counts.get('daily_300') or counts.get('finance')):
        raise RuntimeError('新規取得結果なし。公開用キャッシュは更新しません')
    report['timed_out'] = timed_out
    report['complete'] = bool(report.get('complete')) and not timed_out
    report['packaged_at'] = time.time()
    ROOT.mkdir(parents=True, exist_ok=True)
    temp = Path(a.output+'.tmp')
    with zipfile.ZipFile(temp, 'w', compression=zipfile.ZIP_STORED) as z:
        for f in ROOT.glob('*.json.gz'):
            if time.time()-f.stat().st_mtime > 8*86400:
                f.unlink()
            else:
                z.write(f, f.name)
    os.replace(temp, a.output)
    save_report(report)
    log(json.dumps(report, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--app', default='app.py')
    p.add_argument('--limit', type=int, default=300)
    p.add_argument('--codes', default='')
    p.add_argument('--output', default='cache_bundle.zip')
    p.add_argument('--max-seconds', type=int, default=2400)
    p.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    a = p.parse_args()
    if a.limit < 1 or a.max_seconds < 1:
        p.error('limit and max-seconds must be positive')
    if a.worker:
        worker(a)
        return
    # Never publish an old success report after a failed new run.
    Path('cache_status.json').unlink(missing_ok=True)
    child = subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()),
                              *sys.argv[1:], '--worker'])
    timed_out = False
    try:
        result = child.wait(timeout=a.max_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        log('TIME LIMIT: stop fetching and package completed entries')
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        result = None
    if result not in (None, 0):
        raise RuntimeError(f'更新処理エラー (exit={result})。既存公開データを維持します')
    package(a, timed_out)


if __name__ == '__main__':
    main()
