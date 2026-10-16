"""Select the bilingual acoustic stream when Chinese anchors also agree."""
from difflib import SequenceMatcher
import re


def mixed_language_candidate(chinese,bilingual):
    letters=re.findall('[A-Za-z]',bilingual)
    if len(letters)<4 or not re.search('[A-Za-z]{3}',bilingual):return False
    left=''.join(re.findall('[\u3400-\u9fff]',chinese))
    right=''.join(re.findall('[\u3400-\u9fff]',bilingual))
    if min(len(left),len(right))<4:return False
    match=SequenceMatcher(None,left,right,autojunk=False)
    # This is an acoustic decoder choice before committing text, not a rule
    # that replaces arbitrary written Chinese with a vocabulary suggestion.
    return match.ratio()>=.8 and max(block.size for block in match.get_matching_blocks())>=4
