"""Local acoustic agreement, never evidence from a text rewrite or dictionary.

Independent final readings often differ on one word. Preserve uncertain parts
of the live draft while applying anchored words heard by more than one model.
"""
from difflib import SequenceMatcher
import re

WORDS=re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?|\d+(?:[.:/-]\d+)*")


def chinese_original_supported(original,primary,readings):
    from personalization import content
    if re.search('[A-Za-z]',original+primary) or not re.search('[\u3400-\u9fff]',original):return False
    fold=lambda text:content(text).translate(str.maketrans('','','“”「」『』"'))
    return fold(original)!=fold(primary) and any(fold(r)==fold(original) for r in readings if isinstance(r,str))


def short_english_original_supported(original,primary,readings):
    """Do not replace disputed short English words through an earlier repair path."""
    from acoustic_context import spelling_intent
    if (not isinstance(primary,str) or not primary
            or re.search('[\u3400-\u9fff]',original+primary) or spelling_intent(original)):
        return False
    source_words=tokens(original)[0];target_words=tokens(primary)[0]
    # Added/removed words and explicit spelling keep their existing specialized
    # rules. This veto is limited to short, equally sized word substitutions.
    if not 3<=len(source_words)<=7 or len(source_words)!=len(target_words):return False
    canonical=lambda text:re.sub(r'\s+',' ',text.strip()).casefold().rstrip('.!。！')
    source=canonical(original)
    return source!=canonical(primary) and any(canonical(r)==source for r in readings if isinstance(r,str) and r)


def short_english_agreement(original,primary,readings,protected=()):
    """A lone English word needs three unprompted acoustic families agreeing."""
    from acoustic_context import spelling_intent
    from light_correction import protected_changed,protected_short_source
    from personalization import content,QUOTED
    if (not re.fullmatch('[A-Za-z]{2,24}[.!?。！？]?',primary.strip()) or spelling_intent(original)
            or not 1<=len(content(original))<=12
            or not re.fullmatch('[\u3400-\u9fffA-Za-z]+',content(original))):return None
    if protected_short_source(original):return None
    if re.search(r'[零一二三四五六七八九十百千万亿两不没无别勿未否]|发送|取消|删除|执行|我叫|名字',original):return None
    fold=lambda text:content(text).casefold()
    if fold(primary) in {'send','stop','start','delete','cancel','ignore','execute','no','not','never','open','close','buy','sell'}:return None
    if sum(fold(r)==fold(primary) for r in readings if isinstance(r,str))<2:return None
    if protected_changed(original,primary,list(protected)+[m.group() for m in QUOTED.finditer(original)]):return None
    return primary


def final_pair_agreement(original,readings,protected=()):
    """Any independent final pair may agree; the first model has no veto.

    A larger edit budget is available only for Chinese when all three final
    readings agree almost completely. All meaning and user-word locks remain.
    """
    from personalization import content
    from light_correction import acoustic_consensus,mixed_acoustic_consensus
    readings=[r for r in readings if isinstance(r,str) and r]
    fold=lambda t:content(t).casefold().translate(str.maketrans('','','“”「」『』"'))
    if readings and fold(readings[0])==fold(original):return None
    for i,primary in enumerate(readings):
        for j,support in enumerate(readings):
            if j<=i or fold(primary)!=fold(support):continue
            budget=.25
            if (not re.search('[A-Za-z]',original+primary) and len(readings)>=3
                    and all(SequenceMatcher(None,fold(primary),fold(r),autojunk=False).ratio()>=.92 for r in readings)):
                budget=.4
            exact_three=(len(readings)>=3 and not re.search('[A-Za-z]',original+primary)
                and all(fold(r)==fold(primary) for r in readings))
            result=acoustic_consensus(original,primary,support,protected,max_change_ratio=budget,
                allow_phonetic_split=exact_three)
            result=result or mixed_acoustic_consensus(original,primary,support,protected)
            if result is not None:return result
    return None


def chinese_span_agreement(original,primary,readings,protected=()):
    """Repair anchored Chinese words supported by independent acoustic models."""
    from personalization import QUOTED
    from light_correction import LOCKED,meaning_span,dictionary,protected_changed
    from pypinyin import lazy_pinyin
    if (not primary or '\n' in primary or re.search('[A-Za-z]',original+primary)
            or re.search(r'我叫|名叫|姓名|名字|联系|https?://|```|<\|',original+primary)):
        return None
    def indexed(text):
        p=[(c,i) for i,c in enumerate(text) if re.fullmatch('[\u3400-\u9fff0-9]',c)]
        return ''.join(c for c,_ in p),[i for _,i in p]
    source,positions=indexed(original);target,targets=indexed(primary)
    if not 8<=len(source)<=300 or not 8<=len(target)<=min(320,len(source)*1.6+8):return None
    if SequenceMatcher(None,source,target,autojunk=False).ratio()<.55:return None
    others=[indexed(r)[0] for r in readings if isinstance(r,str) and r]
    if len(others)<2:return None
    votes=[0]*len(target);source_votes=[0]*len(source)
    for reading in others:
        for block in SequenceMatcher(None,target,reading,autojunk=False).get_matching_blocks():
            if block.size>=4:
                for index in range(block.a,block.a+block.size):votes[index]+=1
        for block in SequenceMatcher(None,source,reading,autojunk=False).get_matching_blocks():
            if block.size>=4:
                for index in range(block.a,block.a+block.size):source_votes[index]+=1
    protected=[m.group() for m in QUOTED.finditer(original)]+list(protected)
    changes=[];lex=dictionary();old_words=list(lex.tokenize(source,HMM=False));new_words=list(lex.tokenize(target,HMM=False))
    for op,i,j,a,b in SequenceMatcher(None,source,target,autojunk=False).get_opcodes():
        if op not in ('replace','insert') or a==b or max(j-i,b-a)>6:continue
        if not all(votes[a:b]) or any(source_votes[i:j]):continue
        before,after=source[i:j],target[a:b]
        if re.search(r'(?:我叫|名叫|名为|姓名|名字|联系|找|问|和|跟)[\u4e00-\u9fff]{0,2}$',source[max(0,i-5):i]):continue
        if meaning_span(before,after):continue
        if min(votes[a:b])<2 and (op!='replace' or lazy_pinyin(before)!=lazy_pinyin(after)):continue
        if (op=='replace' and any(len(word)>=2 and x<j and y>i for word,x,y in old_words)
                and not any(len(word)>=2 and x<b and y>a for word,x,y in new_words)):continue
        start=positions[i] if i<len(positions) else positions[-1]+1
        end=positions[j-1]+1 if i<j else start
        replacement=primary[targets[a]:targets[b-1]+1]
        candidate=original[:start]+replacement+original[end:]
        if LOCKED.findall(candidate)!=LOCKED.findall(original):continue
        if protected_changed(original,candidate,protected):continue
        changes.append((start,end,replacement))
    if not changes:return None
    result=original
    for start,end,replacement in reversed(changes):result=result[:start]+replacement+result[end:]
    return result


def tokens(text):
    matches=list(WORDS.finditer(text))
    return [m.group().casefold().replace('’',"'") for m in matches],matches


def ordinary_call_clause(original,primary,readings):
    """Only three complete readings of an explicit conditional contact request."""
    source=tokens(original)[0];target=tokens(primary)[0]
    if (source[:2]!=['call','me'] or not 5<=len(target)<=7 or target[:2]!=['call','me']
            or target[2] not in {'if','when'} or target[3] not in {'you','we','they','he','she','it','i'}):
        return False
    return sum(tokens(r)[0]==target for r in readings if isinstance(r,str) and r)>=2


def protected_english_name(original,primary,readings):
    if not primary or not re.search(r'\bmy name\b|\bcall me\b',original,re.I):return False
    if ordinary_call_clause(original,primary,readings):return False
    return tokens(original)[0]!=tokens(primary)[0]


def english_word_agreement(original,primary,readings,protected=()):
    from acoustic_context import spelling_intent
    from light_correction import ENGLISH_MEANING,protected_changed
    from personalization import QUOTED
    if (not primary or len(original)>400 or len(primary)>640 or '\n' in primary
            or re.search('[\u3400-\u9fff]',original+primary) or spelling_intent(original)
            or re.search(r'\bmy name\b|https?://|```|<\|',original+primary,re.I)
            or (re.search(r'\bcall me\b',original+primary,re.I) and not ordinary_call_clause(original,primary,readings))):
        return None
    source,positions=tokens(original);target,targets=tokens(primary)
    if not 4<=len(source)<=64 or not 4<=len(target)<=64:return None
    others=[tokens(r)[0] for r in readings if isinstance(r,str) and r and not re.search('[\u3400-\u9fff]',r)]
    # One alternate decode from the same model is not an independent vote.
    # Callers pass SenseVoice and Whisper once each, never their hinted reruns.
    if len(others)<2 or any(len(r)<4 for r in others):return None
    # A tie with an independent reading supporting the complete live draft
    # retains that draft. E.g. Concord must not become Concorde by popularity.
    if source in others or source==target:return None
    if SequenceMatcher(None,source,target,autojunk=False).ratio()<.45:return None
    locks=re.compile(r'\d+(?:[.:/-]\d+)*|'+ENGLISH_MEANING,re.I)
    locked=lambda t:[x.casefold().replace('’',"'") for x in locks.findall(t)]
    protected=[m.group() for m in QUOTED.finditer(original)]+list(protected)
    votes=[0]*len(target)
    original_votes=[0]*len(source)
    for reading in others:
        for block in SequenceMatcher(None,target,reading,autojunk=False).get_matching_blocks():
            if block.size>=3:
                for index in range(block.a,block.a+block.size):votes[index]+=1
        for block in SequenceMatcher(None,source,reading,autojunk=False).get_matching_blocks():
            if block.size>=3:
                for index in range(block.a,block.a+block.size):original_votes[index]+=1
    changes=[]
    def add(i,j,a,b):
        if a==b or min(votes[a:b])<(2 if min(len(source),len(target))<8 else 1):return
        # Existing independently supported words win when the final ASRs split.
        if any(original_votes[i:j]):return
        if i==j:
            # Only bounded missing edges; never invent a clause mid-sentence.
            if i not in (0,len(source)) or b-a>12:return
            start=end=positions[i].start() if i<len(source) else positions[-1].end()
            replacement=primary[targets[a].start():targets[b-1].end()]
            replacement=(replacement+' ') if i==0 else (' '+replacement)
        else:
            if max(j-i,b-a)>6:return
            start,end=positions[i].start(),positions[j-1].end()
            replacement=primary[targets[a].start():targets[b-1].end()]
        candidate=original[:start]+replacement+original[end:]
        if locked(candidate)!=locked(original):return
        if protected_changed(original,candidate,protected):return
        changes.append((start,end,replacement))
    for op,i,j,a,b in SequenceMatcher(None,source,target,autojunk=False).get_opcodes():
        if op=='equal' or op=='delete':continue
        if op=='replace' and j-i==b-a:
            for n in range(j-i):add(i+n,i+n+1,a+n,a+n+1)
        else:add(i,j,a,b)
    if not changes:return None
    result=original
    for start,end,replacement in reversed(changes):result=result[:start]+replacement+result[end:]
    if locked(result)!=locked(original):return None
    # Render the case of independently matching acoustic words. The fast
    # bilingual model emits capitals; mixing those with repaired lower-case
    # fragments would look like random shouting without changing any words.
    words,spans=tokens(result);case_edits=[]
    for block in SequenceMatcher(None,words,target,autojunk=False).get_matching_blocks():
        for offset in range(block.size):
            old=spans[block.a+offset];new=targets[block.b+offset]
            candidate=result[:old.start()]+new.group()+result[old.end():]
            if not any(term in result and candidate.count(term)!=result.count(term) for term in protected):
                case_edits.append((old.start(),old.end(),new.group()))
    for start,end,replacement in reversed(case_edits):result=result[:start]+replacement+result[end:]
    return result
