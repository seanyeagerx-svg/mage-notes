"""Isolated short-English language retry; no corpus words or personal labels."""
import re
from english_orthography import QUANTITY
from personalization import PROTECTED

METHOD='short_word_three_families_language_only'


def word(text):
    if not isinstance(text,str) or not re.fullmatch(r'[A-Za-z]{2,24}[.!?。！？]?',text.strip()):return None
    value=text.strip().rstrip('.!?。！？')
    from light_correction import protected_short_source
    if QUANTITY.search(value) or protected_short_source(value):return None
    return value


def eligible_source(text,protected=()):
    from light_correction import protected_short_source
    if (not isinstance(text,str) or not re.fullmatch(r'[\u3400-\u9fff]{1,8}[.!?。！？]?',text.strip())
            or PROTECTED.search(text) or protected_short_source(text)
            or re.search(r'我叫|名字|姓名|执行|删除',text)):return False
    return not any(isinstance(t,str) and t and t.casefold() in text.casefold() for t in protected)


def needs_check(online,primary,support,whisper,seconds):
    if not isinstance(seconds,(float,int)) or not .2<=seconds<=6 or not eligible_source(online):return False
    if not (word(support) or word(whisper)):return False
    # Two original bilingual readers agreeing on Chinese is a real veto, even
    # if an English-only reader might invent a similar-sounding English word.
    fold=lambda x:re.sub(r'[.!?。！？\s]','',x or '')
    if re.search('[\u3400-\u9fff]',primary or '') and fold(primary)==fold(support):return False
    return True


def evidence(online,primary,support,whisper,qwen_english,parakeet,seconds):
    if not needs_check(online,primary,support,whisper,seconds):return None
    target=word(parakeet)
    if not target:return None
    key=target.casefold()
    q,s,w=(word(x) for x in (qwen_english,support,whisper))
    # Parakeet is independent but English-only; require a real multilingual
    # unhinted reader plus a third family. Both Qwen passes are one family.
    votes=[bool(x and x.casefold()==key) for x in (q,s,w)]
    if sum(votes)<2 or not any(votes[1:]):return None
    return dict(method=METHOD,online=online,primary=primary,support=support,
        whisper=whisper,qwen_english=qwen_english,parakeet=parakeet,seconds=seconds)


def repair(source,proof,protected=()):
    if not isinstance(proof,dict) or proof.get('method')!=METHOD or not eligible_source(source,protected):return None
    result=evidence(*(proof.get(k) for k in ('online','primary','support','whisper','qwen_english','parakeet','seconds')))
    if not result or not eligible_source(proof.get('online'),protected):return None
    # The evidence belongs to this utterance; do not apply it to unrelated
    # Chinese text edited after the draft. The App also checks its edit epoch.
    fold=lambda x:re.sub(r'[.!?。！？\s]','',x)
    if fold(source)!=fold(proof['online']):return None
    return word(proof['parakeet'])
