"""Restore only a missing English opening with four bounded acoustic witnesses.

The entire current word sequence must be an unchanged suffix in every reader.
Select an existing prefix supported by an exact pair, with all content words
heard by three families and all prefix quantities unanimous. Do not rewrite
the source, add negation, synthesize text, or act on commands.
"""
from collections import Counter
from difflib import SequenceMatcher
import re
from acoustic_context import spelling_intent
from english_orthography import NUMBER_VALUES
from english_script_recovery import NEGATIVE
from personalization import QUOTED

TOKEN=re.compile(r"\d+(?:st|nd|rd|th)\b|[A-Za-z]+(?:['’][A-Za-z]+)?|\d+",re.I)
ORDINALS=dict(zip(('first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth').split(),range(1,21)))
ORDINALS.update(dict(thirtieth=30,fortieth=40,fiftieth=50,sixtieth=60,seventieth=70,eightieth=80,ninetieth=90,hundredth=100,thousandth=1000))
ARTICLES={'a','an','the','this','that'}
STARTERS=ARTICLES|{'for','and','but','when','while','in','on','at','by','it','i','he','she','we','they','you','there','here','as','to','of','from','with'}


def key(word):
    word=word.casefold().replace('’',"'")
    if word in ORDINALS:return 'ordinal:'+str(ORDINALS[word])
    if re.fullmatch(r'\d+(st|nd|rd|th)',word):return 'ordinal:'+re.match(r'\d+',word).group()
    if word in NUMBER_VALUES:return 'number:'+NUMBER_VALUES[word]
    if word.isdigit():return 'number:'+word
    return word


def recover_prefix(current,primary,support,whisper,english,protected=()):
    readings=[primary,support,whisper,english]
    if not isinstance(current,str) or len(current)>300 or not current.strip():return None
    forbidden=r'\b(?:my name|call me|spell|spelling|letters|literal|identifier|code|send|cancel|execute|ignore)\b'
    invalid=r'[\u3400-\u9fff<>`\n/@\\$&=%+*#|~]|https?:|\d[.:/-]\d'
    for text in [current]+readings:
        if not isinstance(text,str) or not text or len(text)>400:return None
        if spelling_intent(text) or QUOTED.search(text) or re.search(forbidden+'|'+invalid,text,re.I):return None
    original=list(TOKEN.finditer(current));source=[key(m.group()) for m in original]
    if not 4<=len(source)<=32:return None
    parsed=[];prefixes=[];tokens=[]
    for text in readings:
        matches=list(TOKEN.finditer(text));row=[key(m.group()) for m in matches]
        length=len(row)-len(source)
        if not 1<=length<=6 or row[length:]!=source:return None
        prefix=text[:matches[length].start()]
        if NEGATIVE.search(prefix) or re.search(r'\b(?:um|uh|erm|ah|hmm)\b',prefix,re.I):return None
        # Reject uncertain named entities at the restored start. The unchanged
        # source may contain personal text; it remains byte-for-byte intact.
        if any(m.group()[0].isupper() and m.group().lower() not in STARTERS
               and m.group().lower() not in NUMBER_VALUES and m.group().lower() not in ORDINALS
               and m.group().lower() not in ('time','times') for m in matches[:length]):return None
        parsed.append(row);prefixes.append(row[:length]);tokens.append(matches)
    numbers=[tuple(w for w in prefix if w.startswith(('ordinal:','number:'))) for prefix in prefixes]
    if len(set(numbers))!=1:return None
    votes=Counter(tuple(prefix) for prefix in prefixes)
    eligible=[]
    for i,prefix in enumerate(prefixes):
        if votes[tuple(prefix)]<2:continue
        counts=[0]*len(prefix)
        for row in prefixes:
            for block in SequenceMatcher(None,prefix,row,autojunk=False).get_matching_blocks():
                for j in range(block.a,block.a+block.size):counts[j]+=1
        if all(v>=(2 if word in ARTICLES else 3) for word,v in zip(prefix,counts)):eligible.append(i)
    if not eligible:return None
    if len({tuple(prefixes[i]) for i in eligible})>1 and 2 not in eligible:return None
    chosen=2 if 2 in eligible else eligible[0]
    matches=tokens[chosen];length=len(prefixes[chosen])
    prefix=readings[chosen][:matches[length].start()]
    if not re.search(r'\s$',prefix):return None
    # No unsupported clause boundary before the preserved suffix.
    if re.search(r'[.!?;:]|[。！？；：]',prefix):return None
    result=(prefix.rstrip() if current.startswith(' ') else prefix)+current
    for term in protected:
        if term and term.casefold() in current.casefold() and current.casefold().count(term.casefold())!=result.casefold().count(term.casefold()):return None
    return result
