"""Bounded decode vocabulary, distinct from text replacement or voice training."""
from difflib import SequenceMatcher
import re

# Project vocabulary hints, not a claim that a user uttered a particular name.
# Names suggested by context never create an alias or a replacement rule.
BUILTIN_TERMS = ('skill', 'leader', 'SOP', 'GPT', 'ChatGPT', 'Codex', 'Whisper', 'Qwen', 'Midjourney', 'vibe coding')
MAX_TERMS = 16
MAX_CHARS = 160


def spelling_intent(text):
    return bool(re.search(r'逐(?:个|字)?字母|拼读|字母|\b(?:spell|spelling|letters)\b', text, re.I)
                or re.search(r'(?<![A-Za-z])(?:[A-Za-z][ .-]+){2,}[A-Za-z](?![A-Za-z])', text))


def clean_terms(terms):
    result, seen = [], set()
    for value in terms:
        if not isinstance(value, str):
            continue
        value = re.sub(r'\s+', ' ', value).strip()
        if (not 2 <= len(value) <= 32 or not re.fullmatch(r'[A-Za-z\u3400-\u9fff][A-Za-z0-9\u3400-\u9fff -]*', value)
                or re.search(r'发送|取消|删除|忽略|执行|\b(?:send|delete|ignore|execute)\b', value, re.I)
                or len(value.split()) > 3):
            continue
        if value.casefold() not in seen:
            seen.add(value.casefold()); result.append(value)
        if len(result) >= 64:
            break
    return tuple(result)


def select_terms(terms, previous='', online=''):
    if spelling_intent(online) or (not online.strip() and spelling_intent(previous)):
        return ()
    vocabulary = clean_terms(terms)
    heard = re.findall(r'[A-Za-z][A-Za-z0-9_-]*', online + ' ' + previous[-160:])
    def relevance(term):
        folded = term.casefold()
        return max((SequenceMatcher(None, folded, word.casefold(), autojunk=False).ratio()
                    for word in heard), default=0)
    # Stable ordering preserves the preference for explicitly learned terms.
    vocabulary = sorted(vocabulary, key=relevance, reverse=True)
    if heard:
        vocabulary = [term for term in vocabulary if relevance(term)>=.5][:6]
    result, total = [], 0
    for term in vocabulary:
        if len(result) == MAX_TERMS:
            break
        if total + len(term) + 1 <= MAX_CHARS:
            result.append(term); total += len(term) + 1
    return tuple(result)


def context_snapshot(personal, previous='', enabled=True):
    return dict(profile=personal.active, terms=clean_terms(personal.decode_terms() + list(BUILTIN_TERMS)) if enabled else (),
                previous=str(previous)[-160:] if enabled else '')


def retry_terms(terms,reading,previous=''):
    """Only retry a plausibly misheard domain word, not ordinary English.

    Correct known terms need no second decode. A common word like 'when' must
    not trigger a Qwen prompt merely because the spellings look similar.
    """
    if spelling_intent(reading):return ()
    common={'the','a','an','and','or','but','if','is','are','was','were','be','been','being','am',
        'this','that','these','those','there','their','they','them','then','than','here','where','when',
        'what','which','who','whose','whom','why','how','while','whether','with','without','from','into',
        'for','your','you','our','ours','his','her','hers','its','mine','have','has','had','having','will',
        'would','could','should','can','may','might','must','need','want','please','every','other',
        'each','some','any','all','many','more','most','only','also','very','just','well','now','not',
        'never','no','yes','make','made','like','time','year','years','work','works','done','does','did'}
    heard=[w.casefold() for w in re.findall('[A-Za-z]{3,}',reading) if w.casefold() not in common]
    result=[]
    for term in select_terms(terms,previous,reading):
        folded=term.casefold()
        if re.search(r'(?<![A-Za-z])'+re.escape(folded)+r's?(?![A-Za-z])',reading,re.I):continue
        if any((SequenceMatcher(None,folded,word,autojunk=False).ratio()>=.6 and folded[0]==word[0])
               or SequenceMatcher(None,folded,word,autojunk=False).ratio()>=.8 for word in heard):
            result.append(term)
    return tuple(result)


def contextual_reading(original, candidate, terms):
    """Decode hints may repair vocabulary; they cannot rewrite Chinese/content."""
    if not candidate or len(candidate)>max(40,len(original)*1.5+8):
        return original
    han=lambda s: ''.join(re.findall(r'[\u3400-\u9fff]',s))
    if han(original)!=han(candidate) or re.findall(r'\d+',original)!=re.findall(r'\d+',candidate):
        return original
    if spelling_intent(original):
        return original
    words=lambda s: re.findall(r'[A-Za-z]+',s.casefold())
    old,new=words(original),words(candidate)
    if old==new:
        return candidate
    vocabulary={re.sub(r'[^a-z]','',t.casefold()) for t in terms}
    meaning={'not','no','never','without','stop','start','send','delete','buy','sell','open','close','enable','disable'}
    for kind,i,j,a,b in SequenceMatcher(None,old,new,autojunk=False).get_opcodes():
        if kind=='equal':
            continue
        before,after=''.join(old[i:j]),''.join(new[a:b])
        if (kind!='replace' or after not in vocabulary or set(old[i:j]+new[a:b])&meaning
                or SequenceMatcher(None,before,after,autojunk=False).ratio()<.5):
            return original
    return candidate
