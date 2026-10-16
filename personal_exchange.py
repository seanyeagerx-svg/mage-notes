"""Portable human vocabulary. No usage corpus, audio, credentials or cloud data."""
import json
from pathlib import Path
import time
from personalization import MAX_PHRASE, _same_protected

FORMAT = 'mage-notes-vocabulary'


def export_data(store):
    return dict(format=FORMAT, version=1,
        name=next(p['name'] for p in store.profiles() if p['id']==store.active),
        rules=[{k:r[k] for k in ('source','target','left_ctx','right_ctx','scope','status')}
               for r in store.rules()],
        protected=[p['text'] for p in store.phrases() if p['protected']])


def read_file(path):
    path = Path(path)
    if path.stat().st_size > 2_000_000:
        raise ValueError('词库文件超过 2 MB')
    data = json.loads(path.read_text('utf-8-sig'))
    validate(data)
    return data


def validate(data):
    if not isinstance(data,dict) or data.get('format')!=FORMAT or data.get('version')!=1:
        raise ValueError('不是支持的个人词库文件')
    rules, protected = data.get('rules'),data.get('protected')
    if not isinstance(rules,list) or len(rules)>1000 or not isinstance(protected,list) or len(protected)>500:
        raise ValueError('词库条目过多或格式有误')
    for r in rules:
        if not isinstance(r,dict) or any(not isinstance(r.get(k),str) for k in ('source','target','left_ctx','right_ctx','scope','status')):
            raise ValueError('改词记录格式有误')
        source,target=r['source'],r['target']
        if (not 1<=len(source)<=MAX_PHRASE or not 1<=len(target)<=MAX_PHRASE or source!=source.strip()
                or target!=target.strip() or source==target or '\n' in source+target or '\r' in source+target
                or not _same_protected(source,target) or len(r['left_ctx'])>4 or len(r['right_ctx'])>4
                or r['scope'] not in ('literal','context') or r['status'] not in ('active','pending','disabled')):
            raise ValueError('词库包含无效改法、数字或否定/发送口令的修改')
    if any(not isinstance(p,str) or not 2<=len(p)<=MAX_PHRASE or p!=p.strip() for p in protected):
        raise ValueError('保留原词记录有误')


def import_data(store, data):
    """All-or-nothing import into a fresh profile; imported corrections await review."""
    validate(data)
    if store.error:
        raise ValueError('本地词库不可写，未导入')
    import uuid
    profile=uuid.uuid4().hex
    base=str(data.get('name') or '导入词库')[:16]
    names={p['name'] for p in store.profiles()}
    name=base+'（导入）';i=2
    while name in names:
        name=base+f'（导入{i}）';i+=1
    now=time.time()
    with store.db:
        store.db.execute('INSERT INTO profiles(id,name) VALUES(?,?)',(profile,name))
        for r in data['rules']:
            store.db.execute('''INSERT OR IGNORE INTO rules(profile,source,target,left_ctx,right_ctx,scope,status,origin,updated)
                VALUES(?,?,?,?,?,?,?,'imported',?)''', (profile,r['source'],r['target'],r['left_ctx'],r['right_ctx'],r['scope'],
                'disabled' if r['status']=='disabled' else 'pending',now))
        for p in data['protected']:
            store.db.execute('INSERT OR IGNORE INTO phrases VALUES(?,?,0,1,?)',(profile,p,now))
    store.revision+=1
    return profile
