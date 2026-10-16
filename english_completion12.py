"""Conservative independent-English reading gate; no text-model proposals."""
from difflib import SequenceMatcher
import re
from acoustic_english import words, NUMBER


def short_extra(current,old,extra,protected=()):
    from acoustic_context import spelling_intent
    from light_correction import ENGLISH_MEANING
    from personalization import QUOTED
    if not isinstance(extra,str) or not re.fullmatch(r'[A-Za-z]{2,18}[.!?。！？]?',extra.strip()):return None
    if len(current)>28 or len(words(current))>3 or len(re.findall('[\u3400-\u9fff]',current))>4:return None
    if spelling_intent(current):return None
    forbidden=r'\d|[零一二三四五六七八九十百千万亿两不没无别勿未否]|发送|取消|执行|我叫|名叫|名字|姓名|联系|'+ENGLISH_MEANING+'|'+NUMBER+r'|\b(?:send|cancel|execute|ignore|my name|call me)\b'
    if re.search(forbidden,current+' '+extra,re.I):return None
    if re.search(r'\b(?:on|off)\b',current,re.I) and words(current)!=words(extra):return None
    if any(term in current for term in list(protected)+[m.group() for m in QUOTED.finditer(current)]):return None
    target=words(extra)
    # The extra family must agree with two existing independent final families.
    # A third disagreement cannot outweigh the three exact acoustic readings.
    agreeing=[r for r in old if not re.search('[\u3400-\u9fff]',r) and words(r)==target]
    return extra if len(agreeing)>=2 and words(current)!=target else None


def extra_completion(current,primary,support,alternatives,extra,protected=()):
    """Repair an anchored provisional phrase, preserving an existing agreement.

    A new English-only family is corroboration, never a language detector.
    Its proposed words must match a whole reading from another family. This
    function returns nothing for disputed quantities, commands or personal names.
    """
    from light_correction import ENGLISH_MEANING
    from acoustic_context import spelling_intent
    from personalization import QUOTED
    old=[x for x in [primary,support]+list(alternatives or ()) if isinstance(x,str) and x]
    readings=old+[extra]
    if not isinstance(extra,str) or len(old)<3:return None
    short=short_extra(current,old,extra,protected)
    if short:return short
    if any(not isinstance(x,str) or len(x)>400 or not re.search('[A-Za-z]',x)
           or re.search(r'[\u3400-\u9fff<>`\n]|https?://|[/\\$]',x) for x in readings):return None
    if len(current)>300 or spelling_intent(current):return None
    if re.search(r'发送|取消|执行|我叫|名叫|姓名|名字|联系|\b(?:my name|call me|send|cancel|execute|ignore)\b',
                 current+' '+extra,re.I):return None
    source,target=words(current),words(extra);parsed=[words(x) for x in old]
    if not 3<=len(source)<=30 or not 4<=len(target)<=min(32,len(source)+6):return None
    if source==target or parsed.count(source)>=2 or target not in parsed:return None
    locks=re.compile(r'\d+(?:[.:/-]\d+)*|[零一二三四五六七八九十百千万亿两]+|不|没|无|别|勿|未|否|'+ENGLISH_MEANING+'|'+NUMBER+r'|\b(?:on|off)\b',re.I)
    locked=lambda text:[x.casefold().replace('’',"'") for x in locks.findall(text)]
    if any(locked(r)!=locked(current) for r in readings):return None
    for term in list(protected)+[m.group() for m in QUOTED.finditer(current)]:
        if term in current and any(r.count(term)!=current.count(term) for r in readings):return None
    if len(re.findall('[\u3400-\u9fff]',current))>8:return None
    matching=SequenceMatcher(None,source,target,autojunk=False)
    if max(b.size for b in matching.get_matching_blocks())<2 or matching.ratio()<.28:return None
    if any(SequenceMatcher(None,target,row,autojunk=False).ratio()<.45 for row in parsed):return None
    # Even the dissenting model must agree on at least two adjacent words.
    if any(max(b.size for b in SequenceMatcher(None,target,row,autojunk=False).get_matching_blocks())<2 for row in parsed):return None
    return extra
