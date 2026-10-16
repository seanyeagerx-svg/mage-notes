"""Promote corroborated, unprompted final ASR over a provisional live draft.

Only distinct acoustic families vote. Text suggestions and hotword retries do
not participate. The result is for dictation; command dispatch retains its own
raw-audio checks and never consumes this selection.
"""
from collections import Counter
from difflib import SequenceMatcher
import re

# Supported scope: common complete English cardinal number words only —
# zero–nineteen, the tens twenty–ninety, hundred/thousand/million/billion and
# their ordinary hyphen/space compounds (twenty-one, one hundred ...), each
# optionally preceded by the polarity words negative/minus, including the
# digit form (negative 5). No arithmetic evaluation, currency or date parsing,
# and no automatic conversion between number words and digits.
_NUMBER_WORDS = ('seventeen|sixteen|nineteen|fourteen|fifteen|thirteen|eighteen'
                 '|eleven|twelve|hundred|thousand|million|billion|zero|seven'
                 '|eight|three|forty|fifty|sixty|seventy|twenty|thirty|eighty'
                 '|ninety|four|five|nine|one|two|six|ten')
ENGLISH_NUMERIC = re.compile(
    r"(?<![a-z])(?:"
    r"(?:negative|minus)(?:[ -]?(?:\d+(?:[.:/-]\d+)*|" + _NUMBER_WORDS + r"))+"
    r"|(?:[ -]?(?:" + _NUMBER_WORDS + r"))+"
    r")(?:[ -]?\d+(?:[.:/-]\d+)*)?(?![a-z])", re.I)
_NUMBER_WORD = re.compile(r'(?:negative|minus|' + _NUMBER_WORDS + r')', re.I)
# Common complete English ordinals: the standalone forms (first ... nineteenth,
# the tens twentieth ... ninetieth, hundredth and up) and the ordinary
# hyphen/space tens-unit compounds (twenty-first, twenty-fifth ...). Like the
# cardinals they are meaning units: a disputed ordinal reading is a changed
# value, not a spelling preference. No date or order arithmetic is done here.
_ORDINAL_UNITS = ('first|second|third|fourth|fifth|sixth|seventh|eighth|ninth'
                  '|tenth|eleventh|twelfth|thirteenth|fourteenth|fifteenth'
                  '|sixteenth|seventeenth|eighteenth|nineteenth|twentieth'
                  '|thirtieth|fortieth|fiftieth|sixtieth|seventieth|eightieth'
                  '|ninetieth|hundredth|thousandth|millionth|billionth')
ENGLISH_ORDINAL = re.compile(
    r"(?<![a-z])(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
    r"[ -]?(?:" + _ORDINAL_UNITS + r")|" + _ORDINAL_UNITS + r")(?![a-z])", re.I)
_ORDINAL_WORD = re.compile(r'(?:' + _ORDINAL_UNITS + r')', re.I)
# Supported scope: units bound to a preceding number. Common metric symbols and
# words (kg, 5 kilograms, 三公斤 ...), the temperature scales Celsius/Fahrenheit
# and percent. A disputed change of the unit or of the percent sign is a changed
# quantity, never a spelling preference; ordinary words next to a number
# (folders, files, minutes ...) are not units, and no conversion, date or
# arithmetic is done here. Ordinary words are excluded by the unit list and
# the (?![a-z]) word boundary, never by refusing a spacing or numeral variant,
# so the spaced single-letter symbols (5 g, 5 m) are protected like 5kg.
_UNIT_LATIN_WORDS = ('degrees\\s+(?:celsius|fahrenheit)|degrees|celsius|fahrenheit'
                     '|percent|kilograms?|kilomet(?:ers?|res?)|grams?|meters?|metres?'
                     '|centimet(?:ers?|res?)|millimet(?:ers?|res?)|liters?|litres?'
                     '|millilit(?:ers?|res?)|milligrams?')
_UNIT_LATIN_SYMBOLS = r'kg|mg|km|cm|mm|ml|°c|°f'
_UNIT_SYMBOL_SINGLE = r'[gtml]'
_UNIT_CHINESE = (r'摄氏度|华氏度|公斤|千克|毫克|公里|千米|厘米|毫米|毫升'
                 r'|千瓦|瓦特|瓦|吨|克|斤|两|米|升|度')
_QUANTITY_LEAD = ('(?:(?:[-+\u2212\u2012\u2013\u2014\uff0d]|负)?\\d+(?:[.:/-]\\d+)*|'
                  + ENGLISH_NUMERIC.pattern + r')')
_CHINESE_NUMBER = r'(?:[-+\u2212\u2012\u2013\u2014\uff0d]|负)?[零一二三四五六七八九十百千万亿两]+'
# Every supported quantity prefix (signed, decimal, digit, English or Chinese
# number words) combines with every supported unit spelling, including the
# ordinary spaced single-letter symbols (5 g) and cross-script pairs (五kg,
# 5公斤, five公斤, 五%, 负五％, negative five摄氏度). Ordinary words are excluded
# by the unit list and the (?![a-z]) word boundary, never by refusing a
# spacing or numeral variant.
QUANTITY_WITH_UNIT = re.compile(
    _QUANTITY_LEAD + r"\s*(?:" + _UNIT_LATIN_WORDS + r"|" + _UNIT_LATIN_SYMBOLS + r")(?![a-z])"
    r"|" + _QUANTITY_LEAD + r"\s*" + _UNIT_SYMBOL_SINGLE + r"(?![a-z])"
    r"|" + _QUANTITY_LEAD + r"\s*(?:%|％)"
    r"|百分之[零一二三四五六七八九十百千万亿两\d]+"
    r"|" + _CHINESE_NUMBER + r"\s*(?:" + _UNIT_LATIN_WORDS + r"|" + _UNIT_LATIN_SYMBOLS + r")(?![a-z])"
    r"|" + _CHINESE_NUMBER + r"\s*" + _UNIT_SYMBOL_SINGLE + r"(?![a-z])"
    r"|" + _CHINESE_NUMBER + r"\s*(?:%|％)"
    r"|" + _QUANTITY_LEAD + r"\s*(?:" + _UNIT_CHINESE + r")"
    r"|" + _CHINESE_NUMBER + r"\s*(?:" + _UNIT_CHINESE + r")", re.I)
_UNIT_WORD = re.compile(r'(?:' + _UNIT_LATIN_WORDS + r'|' + _UNIT_LATIN_SYMBOLS + r')', re.I)
# Explicit Chinese negation/prohibition words, confirmed by real reproduced
# failures (并非/绝非/禁止/严禁 flipped to 确实/允许 on two votes). Like the
# single negation characters below, each is one meaning unit: a disputed
# deletion, addition or flip needs a complete three-family reading, never a
# two-vote majority. Ordinary characters like the 非 in 非常 are not covered —
# the phrase boundary, not any single glyph, is the limit of this protection.
NEGATION_PHRASES = '并非|绝非|禁止|严禁'


def bound_quantities(text):
    """Normalized number-bound unit tokens (5kg, 5 kg, 5%, 三公斤, degrees celsius).

    Bare numbers are not included; two readings that differ only here disagree
    about the quantity, whatever their plain numeric tokens say.
    """
    if not isinstance(text, str):
        return ()
    return tuple(re.sub(r'\s+', '', m.group().casefold().replace('％', '%'))
                 for m in QUANTITY_WITH_UNIT.finditer(text))


def english_typo_support(target, support):
    """A third family may miss one English letter, keeping all other tokens.

    This is acoustic corroboration, never a dictionary spelling guess. Limit it
    to one long English word; amounts and every Chinese token must be identical.
    """
    if len(target) != len(support):
        return False
    changes = [(a,b) for a,b in zip(target,support) if a != b]
    if len(changes) != 1:
        return False
    a,b = changes[0]
    if not re.fullmatch('[a-z]{7,24}',a) or not re.fullmatch('[a-z]{6,25}',b):
        return False
    # A near-miss number word (thirteen/thirtein) is not a real third reading
    # of the amount; the numeric vote must be verbatim three-way agreement.
    # The same holds for ordinals: fifteenth/fifteentb is a mis-spelling of
    # one reading, never independent evidence for changing the value.
    if (_NUMBER_WORD.fullmatch(a) or _NUMBER_WORD.fullmatch(b)
            or _ORDINAL_WORD.fullmatch(a) or _ORDINAL_WORD.fullmatch(b)
            or _UNIT_WORD.fullmatch(a) or _UNIT_WORD.fullmatch(b)):
        return False
    def edit_distance(first,second):
        row=list(range(len(second)+1))
        for i,char in enumerate(first,1):
            next_row=[i]
            for j,other in enumerate(second,1):
                next_row.append(min(row[j]+1,next_row[j-1]+1,row[j-1]+(char!=other)))
            row=next_row
        return row[-1]
    return a[:2] == b[:2] and a[-2:] == b[-2:] and edit_distance(a,b) == 1


def tokens(value):
    if not isinstance(value, str) or not value or len(value) > 320:
        return ()
    if re.search(r'[\r\n<>`\ufffd/@\\$&=%+*#|~]|https?:', value, re.I):
        return ()
    # A sign belongs to its number: every minus glyph (ASCII -, U+2212, U+2012,
    # U+2013, U+2014, U+FF0D) stays inside the numeric token, so readings that
    # differ only in polarity never collapse into the same vote bucket.
    return tuple(re.findall(r"[\u3400-\u9fff]|(?:[-\u2212\u2012\u2013\u2014\uff0d])?\d+(?:[.:/-]\d+)*"
                            r"|[a-z]+(?:'[a-z]+)?",
                            value.casefold().replace('’', "'")))


def semantic_units(text):
    """Polarity/amount/unit/negation units of a transcript.

    A sign belongs to its number: 负五, −5 and "negative five" are single
    polarity-bearing units, while 'negative' as an ordinary adjective never
    matches. A unit bound to its number (5kg, 5 kg, 三公斤, 5%, 5 degrees
    Celsius) is part of the same unit, so a disputed unit or percent-sign
    change can never pass as an unchanged quantity. Shared with
    light_correction so the live promotion vote and the final published text
    apply the exact same semantic judgement.
    """
    from light_correction import ENGLISH_MEANING
    meaning = QUANTITY_WITH_UNIT.pattern + \
        r'|(?:[-+\u2212\u2012\u2013\u2014\uff0d]|负)?\d+(?:[.:/-]\d+)*|负?[零一二三四五六七八九十百千万亿两]+|' \
        + NEGATION_PHRASES + r'|不|没|无|别|勿|未|否|' \
        + ENGLISH_MEANING + '|' + ENGLISH_NUMERIC.pattern + '|' + ENGLISH_ORDINAL.pattern
    for m in re.finditer(meaning, text, re.I):
        unit = m.group().casefold()
        # Hyphenated and space-separated compounds are the same number
        # (twenty-one == twenty one); spacing inside a quantity+unit is layout
        # only (5kg == 5 kg). No other unit is re-written.
        if ENGLISH_NUMERIC.fullmatch(unit):
            unit = re.sub(r'\s+', ' ', unit.replace('-', ' '))
        elif QUANTITY_WITH_UNIT.fullmatch(unit):
            # Full-width percent and spacing are layout, not meaning.
            unit = re.sub(r'\s+', '', unit.replace('％', '%'))
        yield unit


def select_final(original, raw, protected=()):
    if not isinstance(raw, dict) or not isinstance(original, str):
        return None
    from acoustic_context import spelling_intent
    from personalization import QUOTED
    if (spelling_intent(original) or QUOTED.search(original)
            or re.search(r'我叫|名叫|名字|姓名|英文名|中文名|外文名|联系'
                         r'|\b(?:my name|call me|literal|identifier|code)\b', original, re.I)):
        return None
    source = tokens(original)
    if len(source) < 3:
        return None
    readings = {k: raw.get(k) for k in ('primary', 'support', 'whisper')}
    parsed = {k: tokens(v) for k, v in readings.items()}
    if not all(parsed.values()):
        return None
    # A supported live reading is ambiguous, rather than demonstrably obsolete.
    if source in [v for v in parsed.values() if v]:
        return None
    counts = Counter(v for v in parsed.values() if v)
    for family in ('primary', 'whisper', 'support'):
        target = parsed[family]
        votes = counts[target]
        if votes < 2 or len(target) < 3 or target == source:
            continue
        # A mixed-language recovery requires Qwen + Whisper, not two readings
        # with the same Chinese-first failure tendency.
        if votes < 3 and not (parsed['primary'] == target == parsed['whisper']):
            continue
        third_confirmation = (votes == 3 or votes == 2
            and parsed['primary'] == target == parsed['whisper']
            and english_typo_support(target,parsed['support']))
        candidate = readings[family]
        from dictation_cleanup import equivalent_spoken_values
        # An equivalent number format alone is not an ASR repair. Preserve the
        # person's visible formatting instead of churning 七点九 into 7.9.
        if equivalent_spoken_values(original, candidate):
            continue
        from light_correction import protected_changed
        if protected_changed(original, candidate, [t for t in protected if isinstance(t, str) and t]):
            continue
        controls = r'发送|取消|执行|忽略|删除|\b(?:send|cancel|execute|ignore|delete)\b'
        locks = lambda text: tuple(m.group().casefold() for m in re.finditer(controls, text, re.I))
        if locks(original) != locks(candidate):
            continue
        semantic = lambda text: tuple(semantic_units(text))
        # Three independent full readings can correct a provisional "一幅"
        # into "衣服". A two-family disagreement cannot change polarity/amounts.
        if semantic(original) != semantic(candidate) and not third_confirmation:
            continue
        match = SequenceMatcher(None, source, target, autojunk=False)
        anchor = max((b.size for b in match.get_matching_blocks()), default=0)
        if votes < 3 and (match.ratio() < .4 or anchor < 3):
            continue
        if votes == 3 and (match.ratio() < .3 or (anchor < 2 and len(source) > 8)):
            continue
        if len(target) > len(source) * 2 + 8:
            continue
        return candidate
    return None
