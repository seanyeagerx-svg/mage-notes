"""Recover empty English drafts from three named independent acoustic families."""
import re
import time
from english_orthography import QUANTITY
from recognition import _agreement_tokens


def word(text):
    if not isinstance(text,str) or not re.fullmatch(r'[A-Za-z]{2,24}[.!?。！？]?',text.strip()):return None
    if QUANTITY.search(text):return None
    parsed=_agreement_tokens(text)
    if not parsed or parsed[0] in {'ah','eh','er','hm','hmm','mhm','mm','oh','uh','um'}:return None
    return parsed


def evidence(qwen, sense, whisper, english):
    target=word(qwen)
    if not target:return None
    slots={'sensevoice':sense,'whisper':whisper,'parakeet':english}
    agreed=[family for family,value in slots.items() if word(value)==target]
    if 'whisper' not in agreed or 'parakeet' not in agreed:return None
    return dict(qwen_english=qwen,sensevoice=sense,whisper=whisper,parakeet=english,
        families=['qwen']+agreed,method='empty_draft_language_only_three_families')


def eligible_primary(primary,sense,whisper,seconds,protected=()):
    if not primary:return True
    from english_short_word import eligible_source,needs_check
    return eligible_source(primary,protected) and needs_check(primary,primary,sense,whisper,seconds)
