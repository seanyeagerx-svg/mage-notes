"""Wrong-script recovery by alignment of four independent acoustic readings.

Select one complete existing reading; never generate a sentence or combine
fragments. No text model, training reference, or extra model invocation is used.
"""
from collections import Counter
from difflib import SequenceMatcher
import re
from acoustic_english import words
from acoustic_context import spelling_intent
from english_orthography import QUANTITY,NUMBER_VALUES
from personalization import QUOTED

NEGATIVE=re.compile(r"\b(?:(?:ca|do|does|did|is|are|was|were|wo|would|should|could|must|need|dare|has|have|had|ai|sha)n['’ ]?t|no|not|never|without|cannot)\b",re.I)


def _contains(row,part):
    return any(row[i:i+len(part)]==part for i in range(len(row)-len(part)+1))


def recover(current,primary,support,whisper,english,protected=()):
    readings=[primary,support,whisper,english]
    if not isinstance(current,str) or not 2<=len(re.findall('[\u3400-\u9fff]',current))<=80:return None
    if len(current)>300 or spelling_intent(current) or QUOTED.search(current):return None
    forbidden=r'我叫|名字|姓名|联系|发送|取消|执行|\b(?:my name|call me|spell|literal|identifier|code|send|cancel|execute|ignore)\b'
    if re.search(forbidden,current,re.I) or re.search(r'[<>`\n/@\\$&=%+*#|~]|https?:',current,re.I):return None
    if any(not isinstance(r,str) or not r or len(r)>400 or re.search(r'[\u3400-\u9fff<>`\n/@\\$&=%+*#|~]|https?:',r,re.I)
           or spelling_intent(r) or QUOTED.search(r) or re.search(forbidden,r,re.I) for r in readings):return None
    parsed=[words(r) for r in readings];source=words(current)
    if any(not 4<=len(row)<=32 for row in parsed) or source in parsed:return None
    similarities=[[SequenceMatcher(None,a,b,autojunk=False).ratio() for b in parsed] for a in parsed]
    scores=[sum(row)/4 for row in similarities];best=max(scores)
    # Require a shared sentence, not four unrelated English hallucinations.
    if best<.70:return None
    top=[i for i,score in enumerate(scores) if abs(score-best)<1e-9]
    if any(best-score<.035 and abs(score-best)>=1e-9 and parsed[i]!=parsed[top[0]] for i,score in enumerate(scores)):return None
    different={tuple(parsed[i]) for i in top}
    # A tie in lexical support may be resolved only by the existing multilingual
    # acoustic reader. Its words still face all numeric and edit-span checks.
    if len(different)>1:
        if 2 not in top:return None
        chosen=2
    else:chosen=top[0]
    candidate=readings[chosen];target=parsed[chosen]
    if min(similarities[chosen])<.45:return None
    if not source and chosen!=2 and parsed.count(target)<2:return None
    lexical=lambda text:re.sub('[\u3400-\u9fff]',' ',text)
    values=lambda text:tuple(NUMBER_VALUES.get(m.group().casefold(),m.group().casefold()) for m in QUANTITY.finditer(lexical(text)))
    negatives=lambda text:tuple(m.group().casefold().replace('’',"'") for m in NEGATIVE.finditer(lexical(text)))
    original_values=values(current);original_negative=negatives(current)
    numeric_votes=Counter(values(r) for r in readings);negative_votes=Counter(negatives(r) for r in readings)
    if len({key for key in numeric_votes if key})>1 or len({key for key in negative_votes if key})>1:return None
    if any(numeric_votes) and not values(candidate):
        from english_evidence import recover_numeric_outlier
        recovered=recover_numeric_outlier(current,*readings,protected)
        if not recovered or words(recovered)!=target:return None
    if any(negative_votes) and not negatives(candidate):return None
    if original_values and values(candidate)!=original_values:return None
    if values(candidate) and numeric_votes[values(candidate)]<2:return None
    if original_negative and negatives(candidate)!=original_negative:return None
    if negatives(candidate) and negative_votes[negatives(candidate)]<3:return None
    for term in protected:
        if term and term.casefold() in current.casefold() and candidate.casefold().count(term.casefold())!=current.casefold().count(term.casefold()):return None
    # Explicit and disputed proper names do not get resolved by ordinary-word
    # frequency. Capitalization at the start of a sentence is not a name signal.
    proper=[m.group() for m in re.finditer(r'(?<!^)(?<![.!?] )[A-Z][a-z]{2,}\b',candidate)]
    if any(sum(term.casefold() in r.casefold() for r in readings)<3 for term in proper):return None
    if source:
        # Every newly restored span in a partly-English draft needs a second
        # contiguous acoustic occurrence. Word bags cannot invent a phrase.
        matching=SequenceMatcher(None,source,target,autojunk=False)
        for op,i,j,a,b in matching.get_opcodes():
            if op=='equal':continue
            part=target[a:b] if a<b else target[max(0,a-1):min(len(target),b+1)]
            if not part or sum(_contains(row,part) for row in parsed)<2:return None
    return candidate
