"""Final English acoustic evidence; retain quantities except narrow verified word errors."""
from collections import Counter
from difflib import SequenceMatcher
from functools import lru_cache
from itertools import product
from pathlib import Path
import re
from acoustic_english import words
from acoustic_context import spelling_intent
from english_orthography import QUANTITY
from english_script_recovery import NEGATIVE
from personalization import QUOTED


def recover_numeric_outlier(current,primary,support,whisper,english,protected=()):
    readings=[primary,support,whisper,english]
    if not isinstance(current,str) or not 2<=len(re.findall('[\u3400-\u9fff]',current))<=40:return None
    if not 4<=len(current)<=256 or spelling_intent(current) or QUOTED.search(current):return None
    if re.search(r'[<>`\n/@\\$&=%+*#|~]|https?:',current,re.I):return None
    forbidden=r'我叫|名字|姓名|联系|发送|取消|执行|[零一二三四五六七八九十百千万亿两不没无别勿未否]|\b(?:my name|call me|spell|literal|identifier|code|send|cancel|execute|ignore|buy|sell|open|close|start|stop|enable|disable|delete|keep|on|off)\b'
    if re.search(forbidden,current,re.I) or QUANTITY.search(current) or NEGATIVE.search(current):return None
    if any(not isinstance(r,str) or not 4<=len(words(r))<=32 or len(r)>320
           or re.search(r'[\u3400-\u9fff<>`\n/@\\$&=%+*#|~]|https?:',r,re.I)
           or spelling_intent(r) or QUOTED.search(r) or re.search(forbidden,r,re.I) or NEGATIVE.search(r) for r in readings):return None
    parsed=[tuple(words(r)) for r in readings];counts=Counter(parsed)
    agreed=[row for row,count in counts.items() if count==3]
    if len(agreed)!=1:return None
    target=agreed[0];source=tuple(words(current))
    if source in parsed or len(source)<2:return None
    winner=next(r for r,row in zip(readings,parsed) if row==target)
    dissent=next(r for r,row in zip(readings,parsed) if row!=target)
    # No numeric reading already visible is removed; only a single unsupported
    # number introduced by the dissenting family can stop vetoing this repair.
    if QUANTITY.search(winner) or len(list(QUANTITY.finditer(dissent)))!=1:return None
    if SequenceMatcher(None,target,tuple(words(dissent)),autojunk=False).ratio()<.60:return None
    alignment=SequenceMatcher(None,source,target,autojunk=False)
    if alignment.ratio()<.30 or sum(block.size for block in alignment.get_matching_blocks())<2:return None
    if re.search(r'(?<!^)(?<![.!?] )[A-Z][a-z]{2,}\b',winner):return None
    for term in protected:
        if term and term.casefold() in current.casefold() and winner.casefold().count(term.casefold())!=current.casefold().count(term.casefold()):return None
    return winner


DICTIONARY=Path(__file__).resolve().parent/'models/english-pronunciation/cmudict.dict'


@lru_cache(maxsize=1)
def pronunciations():
    result={}
    for line in DICTIONARY.read_text('utf-8').splitlines():
        fields=line.split()
        if len(fields)<2:continue
        word=re.sub(r'\(\d+\)$','',fields[0])
        if not re.fullmatch('[a-z]+',word):continue
        phones=tuple(re.sub('[012]$','',v) for v in fields[1:] if not v.startswith('#'))
        if not phones or any(not re.fullmatch('[A-Z]+',v) for v in phones):continue
        result.setdefault(word,set()).add(phones)
    return result


def recover_compound(current,primary,support,whisper,english,protected=()):
    readings=[primary,support,whisper,english]
    values=[current]+readings
    forbidden=r'\b(?:spell|literal|identifier|code|send|cancel|execute|ignore|my name|call me)\b'
    if any(not isinstance(t,str) or not 4<=len(words(t))<=32 or len(t)>320 or spelling_intent(t) or QUOTED.search(t)
           or re.search(r'[\u3400-\u9fff0-9<>`\n/@\\$&=%+*#|~]|https?:',t,re.I) or re.search(forbidden,t,re.I) for t in values):return None
    source=words(current);parsed=[words(t) for t in readings];target=parsed[0]
    if any(row!=target for row in parsed) or source==target:return None
    if len(list(QUANTITY.finditer(current)))!=1 or any(QUANTITY.search(t) for t in readings):return None
    changes=[x for x in SequenceMatcher(None,source,target,autojunk=False).get_opcodes() if x[0]!='equal']
    if len(changes)!=1:return None
    op,i,j,a,b=changes[0]
    if op!='replace' or not 2<=j-i<=3 or b-a!=1:return None
    old=source[i:j];new=target[a]
    if len(new)<5 or not any(QUANTITY.fullmatch(w) for w in old):return None
    # Names, quotations, and a user's protected expression stay literal.
    if any(term and term.casefold() in current.casefold() and primary.casefold().count(term.casefold())!=current.casefold().count(term.casefold()) for term in protected):return None
    positions=[list(re.finditer(r"[A-Za-z]+(?:['’][A-Za-z]+)?",t)) for t in readings]
    if a and any(p[a].group()[0].isupper() and not p[a].group().isupper() for p in positions):return None
    try:dictionary=pronunciations()
    except (OSError,UnicodeError):return None
    choices=[dictionary.get(w,set()) for w in old];expected=dictionary.get(new,set())
    if not all(choices) or not expected:return None
    if not any(tuple(phone for part in parts for phone in part) in expected for parts in product(*choices)):return None
    spans=list(re.finditer(r"[A-Za-z]+(?:['’][A-Za-z]+)?",current))
    replacement=positions[0][a].group()
    return current[:spans[i].start()]+replacement+current[spans[j-1].end():]
