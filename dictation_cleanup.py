"""Conservative spoken disfluencies, with explicit repair rather than guessing.

Only automatic dictation spans call this module. Quoted text, protected wording,
literal discussion of words, emphasis, and ambiguous negation remain verbatim.
"""
import re
from personalization import QUOTED
from personalization import content
from speech_text import trim_fillers


NUMBER = r'(?:\d+(?:\.\d+)?|[零〇一二三四五六七八九十百千万两]+)'
VALUE = (r'(?:(?:上午|下午|晚上|早上|凌晨|中午)?' + NUMBER
         + r'点(?:半|' + NUMBER + r'分)?|' + NUMBER + r'(?:摄氏度|分钟|小时|度|张|个|天|元))')
REPAIR = re.compile(r'(?P<old>' + VALUE + r')[ ，,]*(?:不对[ ，,]*(?:是)?|说错了[ ，,]*(?:是)?|应该是)[ ，,]*(?P<new>' + VALUE + r')')
CONFUSED_REPAIR = re.compile(REPAIR.pattern.replace('不对','(?:不对|部队)'))
REPEATED = re.compile(r'(?P<unit>还有就是|比如说|就是说|我觉得|我想|我们|你先|你看|请你|然后|就是|那个|这个|一些|因为|所以|但是)(?:[ ，,]*(?P=unit))+')
META = re.compile(r'语气词|语气助词|这个字|那个字|这个词|那个词|读音|怎么读|重复.{0,4}(?:次|遍)|逐字|原样|照读')
ENGLISH_LITERAL = re.compile(r'\b(?:verbatim|literally|repeat|quotation|quoted)\b|\bexact words\b',re.I)
# Restrict restart removal to adjacent incomplete request/ability stems. Do not
# generalize to "I think I think", reported speech, numbers, or changed intent.
ENGLISH_RESTART = re.compile(
    r"(?P<head>\b(?:I|we|you|he|she|it|they)\s+(?:would\s+like|"
    r"(?:do(?:n't)?|does(?:n't)?|did(?:n't)?)\s+(?:want|need)|want|need))"
    r"(?:[ \t]+(?:to|a|an|the))?[ \t,.—-]+(?P=head)\b",re.I)


def _value_signature(value):
    digits={c:i for i,c in enumerate('零一二三四五六七八九')}
    digits.update({'〇':0,'两':2})
    units={'十':10,'百':100,'千':1000,'万':10000}
    def number(match):
        phrase=match.group()
        if not any(c in units for c in phrase):
            return ''.join(str(digits[c]) for c in phrase)
        total=section=current=0
        for char in phrase:
            if char in digits:current=digits[char]
            elif units[char]==10000:
                total+=(section+current)*10000;section=current=0
            else:
                section+=(current or 1)*units[char];current=0
        return str(total+section+current)
    return re.sub('[零〇一二三四五六七八九十百千万两]+',number,value)


def equivalent_spoken_values(first,second):
    return _value_signature(content(first)).casefold()==_value_signature(content(second)).casefold()


def recover_spoken_repair(original, readings, protected=()):
    """Recover a misheard repair cue only if two original-audio readings agree.

    This is not a global 部队->不对 alias. Both old/new scalar values, their
    units and the explicit cue must occur in two independent acoustic readings.
    """
    if META.search(original) or QUOTED.search(original):return original
    heard=[{(_value_signature(m['old']),_value_signature(m['new']))
            for m in REPAIR.finditer(reading)} for reading in readings if isinstance(reading,str)]
    def revise(match):
        fragment=match.group()
        if '部队' not in fragment or any(term and term in fragment for term in protected):return fragment
        signature=(_value_signature(match['old']),_value_signature(match['new']))
        if sum(signature in evidence for evidence in heard)<2:return fragment
        return fragment.replace('部队','不对',1)
    return CONFUSED_REPAIR.sub(revise,original)


def clean_disfluencies(text, protected=()):
    if not text or META.search(text) or ENGLISH_LITERAL.search(text):
        return text
    spans = [m.span() for m in QUOTED.finditer(text)]
    from literal_spelling import spelling_spans
    spans.extend(spelling_spans(text))
    for term in protected:
        if term:
            spans.extend(m.span() for m in re.finditer(re.escape(term), text))
    # Process only unprotected islands so every substitution keeps literal spans.
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    out, cursor = [], 0
    for start, end in merged:
        out.extend((_clean(text[cursor:start]), text[start:end]))
        cursor = end
    out.append(_clean(text[cursor:]))
    return ''.join(out)


def _clean(text):
    text = trim_fillers(text)
    # Two bounded passes also cover a three-way stumble without an unbounded
    # rewrite loop. The second occurrence supplies the rest of the sentence.
    for _ in range(2):
        text=ENGLISH_RESTART.sub(lambda match:match['head'],text)
    text = REPEATED.sub(lambda m: m['unit'], text)
    text = re.sub(r'我[ ，,]*我(?=(?:想|要|觉得|希望|认为|感觉))', '我', text)
    # Keep emphasis (very very), grammatical repetition (had had, that that),
    # and no/no/no. A small list is preferable to deleting every doubled word.
    text = re.sub(r'\b(I|we|you|please|the|a|an)(?:[ ,]+\1)+\b',
                  lambda m: m[1], text, flags=re.I)
    if re.search(r'\b(?:I|we|you|please|want|need|would|could|should|the)\b', text, re.I):
        text = re.sub(r'\b(?:um|uh|erm)\b(?![- ]huh)[ ,]*', '', text, flags=re.I)

    def revise(match):
        old, new = match['old'], match['new']
        category = lambda value: 'time' if '点' in value else re.search(r'[^\d零〇一二三四五六七八九十百千万两.]+$', value).group()
        # “三点不对，四点也不对” rejects both alternatives; it is not a repair.
        after = text[match.end():].lstrip(' ，,')
        if category(old) != category(new) or re.match(r'也|都|还|不|没|未|别', after):
            return match.group()
        return new

    return REPAIR.sub(revise, text)
