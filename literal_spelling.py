"""Locate dictated letter sequences without treating ordinary I/a as codes."""
import re

_CUE=re.compile(r'字母|拼读|\b(?:spell|spelling|letters?)\b',re.I)
_RUN=re.compile(r'(?<![A-Za-z0-9])(?:[A-Za-z][\s,，.·-]+){1,}[A-Za-z](?![A-Za-z0-9])')
_NAMED=re.compile(r'(?:字母|(?i:\bletters?\b))\s*(?:(?i:are|is)\b|是|为)?[\s:：,，]*(?P<code>[A-Za-z]{1,20})(?![A-Za-z])')
_SPELLED=re.compile(r'(?i:\bspell(?:ing)?\b)[\s:：,，]+(?P<code>[A-Z]{2,20})(?![A-Za-z])')


def spelling_spans(text):
    spans=[]
    for match in _RUN.finditer(text):
        letters=re.findall('[A-Za-z]',match.group())
        standalone=not (text[:match.start()]+text[match.end():]).strip(' \t\n，。,.!?！？')
        explicit=bool(_CUE.search(text[:match.start()]))
        only_stutter=len({x.casefold() for x in letters})==1 and letters[0].casefold() in ('i','a')
        if explicit or (len(letters)>=3 and not only_stutter) or standalone:spans.append(match.span())
    for pattern in (_NAMED,_SPELLED):
        for match in pattern.finditer(text):
            word=match['code']
            # "letters are useful" is prose, not the code USEFUL.
            if len(word)==1 or word.isupper() or '字母' in match.group():
                spans.append(match.span('code'))
    merged=[]
    for start,end in sorted(spans):
        if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
        else:merged.append((start,end))
    return tuple(merged)


def preserve_spelling(original,proposed,readings=(),protected=()):
    """Keep the source if a later model drops/changes a literal letter."""
    spans=spelling_spans(original)
    if not spans:return proposed
    fold=lambda text:re.sub(r'[\s，。！？；：、,.!?;:·-]','',text).casefold()
    if fold(original)==fold(proposed):return proposed
    signature=lambda text,ranges:''.join(re.findall('[A-Za-z]',''.join(text[a:b] for a,b in ranges))).casefold()
    target_spans=spelling_spans(proposed)
    before,after=signature(original,spans),signature(proposed,target_spans)
    if before==after:return proposed
    # A fast recognizer can itself collapse I I A A into IA. Three independent
    # complete audio readings may restore repetitions, but never remove or
    # substitute a literal letter or change surrounding words.
    evidence=[text for text in readings if isinstance(text,str) and text]
    if (len(spans)==len(target_spans)==1 and 2<=len(before)<len(after)<=20
            and len(after)-len(before)<=4 and len(evidence)>=3
            and all(fold(text)==fold(proposed) for text in evidence)
            and not any(term in original and original.count(term)!=proposed.count(term) for term in protected)):
        from itertools import groupby
        groups=lambda text:[(letter,len(list(items))) for letter,items in groupby(text)]
        old,new=groups(before),groups(after)
        a,b=spans[0];x,y=target_spans[0]
        unchanged_context=fold(original[:a]+'~'+original[b:])==fold(proposed[:x]+'~'+proposed[y:])
        if (unchanged_context and [x[0] for x in old]==[x[0] for x in new]
                and all(second[1]>=first[1] for first,second in zip(old,new))):return proposed
    return original
