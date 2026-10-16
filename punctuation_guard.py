"""Keep newly inserted stops out of ordinary two-character Chinese words.

This does not move a mark, infer sentence meaning, or modify any letters.
Original separators win; uncertain word boundaries remain a limitation.
"""
import re
from personalization import content, QUOTED


def preserve_compounds(original, proposed):
    if content(original) != content(proposed) or QUOTED.search(original + proposed):
        return proposed
    bare = content(proposed)
    if not bare or len(bare) > 500:
        return proposed
    from light_correction import dictionary
    lex = dictionary()
    inside = set()
    for word, start, end in lex.tokenize(bare, HMM=False):
        if (len(word) == 2 and re.fullmatch('[\u3400-\u9fff]{2}', word)
                and lex.FREQ.get(word, 0) >= 20
                and not re.search('[零一二三四五六七八九十百千万亿两不没无别勿未否]', word)
                and word not in {'发送', '取消', '删除', '执行'}):
            inside.add(start + 1)
    existing = set()
    position = 0
    for char in original:
        if content(char):
            position += len(content(char))
        elif char in '，。,.!?！？;；:：':
            existing.add(position)
    result = []
    position = 0
    for char in proposed:
        if content(char):
            position += len(content(char))
        elif char in '，。,.' and position in inside and position not in existing:
            continue
        result.append(char)
    return ''.join(result)
