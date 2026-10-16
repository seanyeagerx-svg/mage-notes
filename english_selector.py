"""Isolated whole-English consensus for English phrases, keeping the existing semantic locks."""
from difflib import SequenceMatcher
import re
from acoustic_english import words
from acoustic_context import spelling_intent
from english_orthography import QUANTITY,NUMBER_VALUES
from english_script_recovery import NEGATIVE
from personalization import QUOTED
from light_correction import ENGLISH_MEANING

SEMANTIC_LOCKS=ENGLISH_MEANING+r'|\b(?:send|cancel|execute|ignore)\b'


def semantic_words(text, pattern=SEMANTIC_LOCKS):
    """Keep command verbs distinct from an adjective inside a noun phrase.

    A preceding determiner makes 'a close eye' and 'the open window' noun
    phrases. 'Please close the window' still contains a protected action.
    This is used only with complete raw acoustic agreement below.
    """
    adjectives={m.start(1) for m in re.finditer(
        r'\b(?:a|an|the|this|that|my|your|his|her|our|their)\s+(close|open)\s+[A-Za-z]+\b',text,re.I)}
    return tuple(m.group().casefold().replace('’',"'") for m in re.finditer(pattern,text,re.I)
        if m.start() not in adjectives)


def unanimous_lexical_reading(candidate, raw):
    if not isinstance(raw,dict):return False
    target=words(candidate)
    return bool(target and all(isinstance(raw.get(k),str) and words(raw[k])==target
        for k in ('primary','support','whisper')))


def select(original,primary,support,whisper,protected=()):
    readings=[primary,support,whisper]
    if not isinstance(original,str) or not 4<=len(original)<=300 or spelling_intent(original) or QUOTED.search(original):return None
    han=re.findall('[\u3400-\u9fff]',original);letters=re.findall('[A-Za-z]',original)
    if len(han)>min(10,len(letters)/2) or len(letters)<8:return None
    forbidden=r'我叫|名字|姓名|联系|发送|取消|执行|\b(?:my name|call me|spell|literal|identifier|code|send|cancel|execute|ignore)\b'
    if re.search(forbidden,original,re.I) or re.search(r'[<>`\n/@\\$&=%+*#|~]|https?:',original,re.I):return None
    if any(not isinstance(r,str) or not r or len(r)>400 or re.search('[\u3400-\u9fff<>`\n]',r) or spelling_intent(r) or QUOTED.search(r) for r in readings):return None
    parsed=[words(r) for r in readings];source=words(original)
    if not 2<=len(source)<=36 or any(not 4<=len(row)<=40 for row in parsed) or source in parsed:return None
    numeric=lambda text:tuple(NUMBER_VALUES.get(m.group().casefold(),m.group().casefold()) for m in QUANTITY.finditer(text))
    locks=lambda text:tuple(m.group().casefold().replace('’',"'") for m in re.finditer(SEMANTIC_LOCKS,text,re.I))
    if re.search('[零一二三四五六七八九十百千万亿两不没无别勿未否]',original):return None
    if any(numeric(r)!=numeric(original) for r in readings):return None
    if any(locks(r)!=locks(original) for r in readings):
        # A live draft is a provisional reading. An adjective mistaken for an
        # action must not veto three identical independent complete readings.
        # Retain exact negative/action locks for every other disagreement.
        unanimous=len({tuple(row) for row in parsed})==1
        if not unanimous or any(semantic_words(r)!=semantic_words(original) for r in readings):return None
    for candidate,target in zip(readings,parsed):
        if parsed.count(target)<2 or len(target)>len(source)+5:continue
        alignment=SequenceMatcher(None,source,target,autojunk=False)
        if alignment.ratio()<(.3 if han else .55) or max(b.size for b in alignment.get_matching_blocks())<(2 if han else 3):continue
        if not han and (len(source)<4 or any(max(j-i,b-a)>3 for op,i,j,a,b in alignment.get_opcodes() if op!='equal')):continue
        if not han and any(i<2 and any(row[:max(2,b)]!=target[:max(2,b)] for row in parsed) for op,i,j,a,b in alignment.get_opcodes() if op!='equal'):continue
        if any(term and term.casefold() in original.casefold() and candidate.casefold().count(term.casefold())!=original.casefold().count(term.casefold()) for term in protected):continue
        return candidate
    return None
