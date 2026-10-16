"""Resolve an acoustic language disagreement without translating mixed dictation."""
import re
from acoustic_context import spelling_intent

HAN=re.compile('[\u3400-\u9fff]')
WORD=re.compile("[A-Za-z]+(?:['’][A-Za-z]+)?")


def needs_english_retry(online,primary,support,detected_language):
    if not detected_language or detected_language=='en' or spelling_intent(online or ''):
        return False
    primary,support=primary or '',support or ''
    if any(HAN.search(text) or len(WORD.findall(text))<3 for text in (primary,support)):
        return False
    online=online or ''
    latin=sum(c.isascii() and c.isalpha() for c in online)
    han=len(HAN.findall(online))
    return latin>=8 and han<=4 and latin>=han*4
