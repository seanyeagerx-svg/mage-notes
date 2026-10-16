"""Content-free learning diagnostics, kept separately for each local profile."""
import time

MESSAGES = {
    'audio_staged': '短录音已暂存；等待在同一输入框亲手改词。',
    'audio_missing': '这次没有收到可配对的短录音；文字改词仍可学习。',
    'audio_range': '文字位置已改变，未把录音配给不确定的文字。',
    'audio_rejected': '录音或文字超出短样本范围，未保存。',
    'field_unreadable': '此输入框无法读回修改；可在草稿中选中文字，用“改词入库”。',
    'field_stale': '输入框读回太慢，未把过期文字当作你的修改。',
    'field_ambiguous': '输入框里有重复片段，无法确定你改的是哪一处。',
    'unwatched_edit': '尚未建立本次文字的对照；可用“改词入库”记录。',
    'human_edit': '已收到亲手改词；可在改词记录中查看。',
    'audio_saved': '对应的短录音已保存在本机。',
    'edit_without_audio': '已收到文字修改，但没有对应短录音；旧草稿无法补回原音。',
    'audio_not_word_edit': '修改没有形成可用短录音样本；排版、清空和大段重写不会入库。',
    'learning_error': '自动学习出现错误；当前文字保留，可手动添加改法。',
}


def record(store, code):
    if code not in MESSAGES or not hasattr(store, 'db'):
        return
    # Diagnostics must never interrupt transcription or commit unrelated work.
    try:
        if store.db.in_transaction:
            return
        # Repeated failures in an unreadable field should not write and rebuild
        # the vocabulary page on every probe. Human edits are never deduplicated.
        if code.startswith('field_'):
            recent=getattr(store,'_diagnostic_times',{})
            key=(store.active,code);now=time.monotonic()
            if now-recent.get(key,-60)<30:return
            recent[key]=now;store._diagnostic_times=recent
        with store.db:
            store.db.execute('''INSERT INTO learning_events(profile,code,count,updated) VALUES(?,?,1,?)
                ON CONFLICT(profile,code) DO UPDATE SET count=count+1,updated=excluded.updated''',
                (store.active,code,time.time()))
        store.revision += 1
    except Exception:
        pass


def rows(store):
    return [dict(r) for r in store.db.execute('SELECT code,count,updated FROM learning_events WHERE profile=? ORDER BY updated DESC', (store.active,))]
