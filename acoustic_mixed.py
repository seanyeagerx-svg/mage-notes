"""Conservative whole-English-word repairs inside a Chinese utterance."""
from difflib import SequenceMatcher
import re

TOKEN=re.compile(r'[\u3400-\u9fff]|[A-Za-z]+(?:[\x27’][A-Za-z]+)?|\d+')


def domain_boundary_agreement(original,primary,readings,protected=()):
    """Resolve a name whose last sound leaked into adjacent Chinese letters.

    Requires two independent complete acoustic readings, a known domain word,
    and a long unchanged Chinese suffix. Only one or two surplus characters
    right beside the English word may disappear; meaning locks stay intact.
    """
    from acoustic_context import BUILTIN_TERMS,spelling_intent
    from personalization import content,QUOTED
    from light_correction import meaning_pair,ENGLISH_MEANING
    from pypinyin import lazy_pinyin
    if (not primary or len(original)>120 or len(primary)>160 or spelling_intent(original)
            or re.search(r'我叫|名叫|姓名|名字|联系|https?://|[<>`\n]',original+primary)):
        return None
    folded=lambda text:content(text).casefold()
    if not any(folded(r)==folded(primary) for r in readings if isinstance(r,str)):return None
    pattern=re.compile('[A-Za-z]+(?:[ -][A-Za-z]+)*')
    before=list(pattern.finditer(original));after=list(pattern.finditer(primary))
    if len(before)!=1 or len(after)!=1:return None
    old,new=before[0],after[0]
    vocabulary={re.sub('[^a-z]','',t.casefold()) for t in (*BUILTIN_TERMS,*protected)}
    if re.sub('[^a-z]','',new.group().casefold()) not in vocabulary:return None
    if folded(original[:old.start()])!=folded(primary[:new.start()]):return None
    a=content(original[old.end():]);b=content(primary[new.end():])
    if not re.fullmatch('[\u3400-\u9fff]{4,60}',b) or not 1<=len(a)-len(b)<=2:return None
    extra=len(a)-len(b);tail=a[extra:]
    if lazy_pinyin(tail)!=lazy_pinyin(b):return None
    # One adjacent homophone may settle (e.g. 做/作), but later words cannot.
    if tail[1:]!=b[1:] or meaning_pair(tail[0],b[0]):return None
    locks=re.compile(r'\d+|[零一二三四五六七八九十百千万亿两]+|不|没|无|别|勿|未|否|发送|取消|'+ENGLISH_MEANING,re.I)
    if [x.casefold() for x in locks.findall(original)]!=[x.casefold() for x in locks.findall(primary)]:return None
    if any(term in original and original.count(term)!=primary.count(term)
           for term in list(protected)+[m.group() for m in QUOTED.finditer(original)]):return None
    return primary


def mixed_word_agreement(original,primary,readings,protected=()):
    from acoustic_context import spelling_intent
    from light_correction import ENGLISH_MEANING
    from personalization import QUOTED
    if (not primary or len(original)>300 or len(primary)>400 or '\n' in primary
            or not re.search('[\u3400-\u9fff]',original) or spelling_intent(original)
            or re.search(r'我叫|名叫|姓名|名字|联系|https?://|```|<\|',original+primary)):
        return None
    def indexed(text):
        matches=list(TOKEN.finditer(text))
        return [m.group().casefold().replace('’',"'") for m in matches],matches
    source,positions=indexed(original);target,targets=indexed(primary)
    others=[indexed(r)[0] for r in readings if isinstance(r,str) and r]
    if not others or source==target:return None
    locks=re.compile(r'\d+(?:[.:/-]\d+)*|[零一二三四五六七八九十百千万亿两]+|不|没|无|别|勿|未|否|发送|取消|'+ENGLISH_MEANING,re.I)
    locked=lambda text:[x.casefold() for x in locks.findall(text)]
    protected=list(protected)+[m.group() for m in QUOTED.finditer(original)]
    changes=[]
    for op,i,j,a,b in SequenceMatcher(None,source,target,autojunk=False).get_opcodes():
        if op!='replace' or not 1<=b-a<=5 or not 1<=j-i<=12:continue
        # Only a complete English span may replace a bounded live mistake;
        # never rewrite an adjacent Chinese clause merely to improve fluency.
        if not all(re.fullmatch('[a-z]+',word) for word in target[a:b]):continue
        if not 3<=sum(map(len,target[a:b]))<=40:continue
        left=[];right=[]
        for word in reversed(target[max(0,a-4):a]):
            if not re.fullmatch('[\u3400-\u9fff]',word):break
            left.insert(0,word)
        for word in target[b:b+4]:
            if not re.fullmatch('[\u3400-\u9fff]',word):break
            right.append(word)
        if len(left)+len(right)<4:continue
        if source[max(0,i-len(left)):i]!=left or source[j:j+len(right)]!=right:continue
        anchor=left+target[a:b]+right
        if not any(any(reading[n:n+len(anchor)]==anchor for n in range(len(reading)-len(anchor)+1))
                   for reading in others):continue
        old_anchor=left+source[i:j]+right
        if any(any(reading[n:n+len(old_anchor)]==old_anchor for n in range(len(reading)-len(old_anchor)+1))
               for reading in others):continue
        start,end=positions[i].start(),positions[j-1].end()
        replacement=primary[targets[a].start():targets[b-1].end()]
        candidate=original[:start]+replacement+original[end:]
        if locked(candidate)!=locked(original):continue
        if any(term in original and candidate.count(term)!=original.count(term) for term in protected):continue
        changes.append((start,end,replacement))
    if not changes:return None
    result=original
    for start,end,text in reversed(changes):result=result[:start]+text+result[end:]
    return result
