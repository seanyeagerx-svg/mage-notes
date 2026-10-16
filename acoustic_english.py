"""Whole-English recovery supported by independent acoustic families only.

These paths do not consume a text model's proposal or a personal guess. They
keep numeric, command, quoted and spelling intent locks, and need three complete
English readings before repairing a Chinese phonetic fragment or a short phrase.
"""
from difflib import SequenceMatcher
import re
from english_orthography import LATIN_START, LATIN_END

WORD=re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?|\d+(?:[.:/-]\d+)*")
NUMBER=LATIN_START+r'(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion)'+LATIN_END
ARTICLE={'a','an','the'}


def words(text):
    return [m.group().casefold().replace('’',"'") for m in WORD.finditer(text)]


def _inputs(original,primary,readings,protected):
    from acoustic_context import spelling_intent
    from personalization import QUOTED
    from light_correction import ENGLISH_MEANING
    finals=[primary]+[r for r in readings if isinstance(r,str) and r]
    if (not primary or len(finals)<3 or len(original)>300 or spelling_intent(original)
            or re.search(r'发送|取消|执行|我叫|名叫|姓名|名字|联系|\bmy name\b|\bcall me\b',original,re.I)):
        return None
    if any(len(r)>400 or re.search(r'[\u3400-\u9fff<>`\n]|https?://|[/\\$]',r) for r in finals):return None
    parsed=[words(r) for r in finals]
    if any(not 4<=len(row)<=48 for row in parsed):return None
    locks=re.compile(r'\d+(?:[.:/-]\d+)*|[零一二三四五六七八九十百千万亿两]+|不|没|无|别|勿|未|否|'+ENGLISH_MEANING+'|'+NUMBER+r'|\b(?:send|cancel|execute|ignore)\b',re.I)
    locked=lambda text:[x.casefold().replace('’',"'") for x in locks.findall(text)]
    if any(locked(r)!=locked(original) for r in finals):return None
    terms=list(protected)+[m.group() for m in QUOTED.finditer(original)]
    if any(term in original and original.count(term)!=r.count(term) for term in terms for r in finals):return None
    source=words(original)
    if not source or source in parsed:return None
    return finals,parsed,source


def short_phrase_agreement(original,primary,readings,protected=()):
    """A bounded short phrase needs an exact final pair plus a close third."""
    if re.search('[\u3400-\u9fff]',original):return None
    prepared=_inputs(original,primary,readings,protected)
    if prepared is None:return None
    finals,parsed,source=prepared
    if not 3<=len(source)<=12:return None
    for candidate,target in zip(finals,parsed):
        if not 4<=len(target)<=16 or parsed.count(target)<2:continue
        if any(SequenceMatcher(None,target,row,autojunk=False).ratio()<.65 for row in parsed):continue
        match=SequenceMatcher(None,source,target,autojunk=False)
        if match.ratio()<.45 or max(block.size for block in match.get_matching_blocks())<2:continue
        changed=0;valid=True
        for op,i,j,a,b in match.get_opcodes():
            if op=='equal':continue
            # Do not remove a clause or insert a different one at an edge.
            if op in ('delete','insert') or max(j-i,b-a)>3:valid=False;break
            changed+=max(j-i,b-a)
        if valid and changed<=max(3,round(len(source)*.5)):return candidate
    return None


def english_language_agreement(original,primary,readings,protected=()):
    """Recover a phonetic Chinese fragment only in an already English phrase.

    All three final families must hear complete English. Every content word in
    the chosen reading needs a second, adjacent acoustic vote; articles may
    differ. A shared run of three live English words anchors the recovery.
    """
    han=re.findall('[\u3400-\u9fff]',original)
    if not 1<=len(han)<=20:return None
    prepared=_inputs(original,primary,readings,protected)
    if prepared is None:return None
    finals,parsed,source=prepared
    if len(source)<3:return None
    for candidate,target in zip(finals,parsed):
        live=SequenceMatcher(None,source,target,autojunk=False)
        if max(block.size for block in live.get_matching_blocks())<3:continue
        # One final reading may differ locally, but none may be unrelated.
        stripped=[w for w in target if w not in ARTICLE]
        if any(SequenceMatcher(None,stripped,[w for w in row if w not in ARTICLE],autojunk=False).ratio()<.75
               for row in parsed):continue
        votes=[0]*len(target)
        skipped=False
        for row in parsed:
            if not skipped and row is target:skipped=True;continue
            for block in SequenceMatcher(None,target,row,autojunk=False).get_matching_blocks():
                if block.size>=2:
                    for index in range(block.a,block.a+block.size):votes[index]+=1
        if all(vote or word in ARTICLE for word,vote in zip(target,votes)):return candidate
    return None
