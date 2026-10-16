"""Audio-supported English writing conventions, without changing any letters."""
from collections import defaultdict
from difflib import SequenceMatcher
import re

WORD=re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?|\d+")
# Unicode \b treats adjacent Chinese and Latin text as a single word. Spoken
# English tokens still need protection there; identifiers and word substrings
# must not become separate numbers or commands.
LATIN_START=r'(?<![A-Za-z0-9_])'
LATIN_END=r'(?![A-Za-z0-9_])'
NEGATIVE={word.replace("'",''):word for word in (
    "don't","doesn't","didn't","can't","isn't","aren't","wasn't","weren't",
    "won't","wouldn't","shouldn't","couldn't","mustn't","needn't",
    "hasn't","haven't","hadn't","daren't","shan't","ain't")}
LITERAL=re.compile(r'\b(?:word|apostrophe|code|identifier|literal|spelled)\b',re.I)
NUMBER_VALUES=dict(zip(('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen').split(),map(str,range(20))))
NUMBER_VALUES.update(dict(twenty='20',thirty='30',forty='40',fifty='50',sixty='60',seventy='70',eighty='80',ninety='90',hundred='100',thousand='1000',million='1000000',billion='1000000000',dozen='12'))
ORDINALS='first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth thirtieth fortieth fiftieth sixtieth seventieth eightieth ninetieth hundredth thousandth'.split()
NUMBER_WORD=re.compile(LATIN_START+r'(?:'+ '|'.join(list(NUMBER_VALUES)+ORDINALS+['half','quarter']) +r')'+LATIN_END,re.I)
QUANTITY=re.compile(NUMBER_WORD.pattern+'|'+LATIN_START+r'(?:minus|plus|negative|positive)'+LATIN_END+r'|(?<![A-Za-z0-9_.])[+-]?\d+(?:[.:/-]\d+)*',re.I)


def preserve_english_quantities(original,proposed):
    """A model vote cannot turn five into fifty, remove a count, or add one.

    Unambiguous single-word/digit spelling is equivalent. Complex number words
    retain their sequence rather than guessing a sum or intent correction.
    """
    if not (NUMBER_WORD.search(original) or NUMBER_WORD.search(proposed)):return proposed
    def values(text):
        return [NUMBER_VALUES.get(m.group().casefold(),m.group().casefold()) for m in QUANTITY.finditer(text)]
    before,after=values(original),values(proposed)
    if before==after:return proposed
    # Exchanging counts between objects cannot be repaired safely by position.
    if len(before)>1 and sorted(before)==sorted(after):return original
    token=re.compile(r"(?<![A-Za-z0-9_])[+-]?\d+(?:[.:/-]\d+)*|[A-Za-z]+(?:['’][A-Za-z]+)?")
    old,new=list(token.finditer(original)),list(token.finditer(proposed))
    key=lambda item:NUMBER_VALUES.get(item.group().casefold(),item.group().casefold())
    edits=[]
    for op,i,j,a,b in SequenceMatcher(None,list(map(key,old)),list(map(key,new)),autojunk=False).get_opcodes():
        if op=='equal':continue
        start=old[i].start() if i<len(old) else len(original)
        end=old[j-1].end() if i<j else start
        x=new[a].start() if a<len(new) else len(proposed)
        y=new[b-1].end() if a<b else x
        if values(original[start:end])==values(proposed[x:y]):continue
        replacement=original[start:end]
        # Restore only the affected phrase, keeping unrelated verified fixes.
        if x==y and replacement:
            if x and re.fullmatch('[A-Za-z0-9_]',proposed[x-1]):replacement=' '+replacement
            if x<len(proposed) and re.fullmatch('[A-Za-z0-9_]',proposed[x]):replacement+=' '
        elif not replacement and x and proposed[x-1].isspace():
            while y<len(proposed) and proposed[y] in ' \t':y+=1
        if replacement.isupper() and proposed[x:y].islower():replacement=replacement.lower()
        edits.append((x,y,replacement))
    result=proposed
    for x,y,replacement in reversed(edits):result=result[:x]+replacement+result[y:]
    return result if values(result)==before else original


def _eligible(text):
    from acoustic_context import spelling_intent
    from personalization import QUOTED
    return (0<len(text)<=400 and not spelling_intent(text) and not QUOTED.search(text)
        and not LITERAL.search(text)
        and not re.search(r'[\u3400-\u9fff<>`\n/@\\$&=%+*#|~]|https?:',text,re.I)
        and not re.search(r"(?<![A-Za-z])'[^'\n]*'(?![A-Za-z])",text))


def _tokens(text):
    matches=list(WORD.finditer(text));result=[];index=0
    while index<len(matches):
        match=matches[index];key=match.group().casefold().replace('’',"'").replace("'",'')
        end=match.end()
        if (key=='can' and index+1<len(matches) and matches[index+1].group().casefold()=='not'
                and re.fullmatch(r'[ \t]+',text[end:matches[index+1].start()])):
            end=matches[index+1].end();key='cannot';index+=1
        result.append((key,match.start(),end));index+=1
    return result


def restore_negative_spelling(text,readings,protected=()):
    """Insert an apostrophe or join can not, supported by two audio families.

    Never add/drop a letter or negation. Literal, quoted and personal wording
    wins even over unanimous audio. A lone word has no contextual evidence.
    """
    if not _eligible(text):return text
    source=_tokens(text);keys=[row[0] for row in source]
    votes=defaultdict(list)
    for reading in readings:
        if not isinstance(reading,str) or not _eligible(reading):continue
        target=_tokens(reading);target_keys=[row[0] for row in target];seen=set()
        for block in SequenceMatcher(None,keys,target_keys,autojunk=False).get_matching_blocks():
            if block.size<3:continue
            for offset in range(block.size):
                key,start,end=source[block.a+offset]
                _,left,right=target[block.b+offset];replacement=reading[left:right]
                folded=replacement.casefold().replace('’',"'")
                valid=(key in NEGATIVE and folded==NEGATIVE[key] and "'" not in text[start:end]
                    and '’' not in text[start:end]) or (key=='cannot' and folded=='cannot'
                    and re.fullmatch(r'can[ \t]+not',text[start:end],re.I))
                if not valid:continue
                candidate=text[:start]+replacement+text[end:]
                if any(term and term in text and candidate.count(term)!=text.count(term) for term in protected):continue
                vote=(start,end,folded)
                if vote not in seen:votes[vote].append(replacement);seen.add(vote)
    result=text
    for (start,end,_),forms in sorted(votes.items(),reverse=True):
        if len(forms)>=2:
            replacement=next((form for form in forms if not form.isupper()),forms[0])
            result=result[:start]+replacement+result[end:]
    return result


def restore_matching_layout(text,readings,protected=()):
    """Use acoustic punctuation/case only when two complete word lists match.

    Apostrophes are words here; this cannot introduce a contraction or delete
    a filler. Numeric layout, literal text and protected forms stay put.
    """
    if not _eligible(text) or re.search(r'\d',text):return text
    words=lambda value:[m.group().casefold().replace('’',"'") for m in WORD.finditer(value)]
    source=words(text)
    if len(source)<4:return text
    matching=[reading for reading in readings if isinstance(reading,str) and _eligible(reading)
        and not re.search(r'\d',reading) and words(reading)==source
        and not any(term and term in text and reading.count(term)!=text.count(term) for term in protected)]
    return matching[0] if len(matching)>=2 else text
