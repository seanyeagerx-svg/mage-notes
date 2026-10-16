"""Speech-only housekeeping: bounded fillers and punctuation without rewriting."""
from pathlib import Path
from difflib import SequenceMatcher
import re
from personalization import QUOTED, content

MODEL = Path(__file__).resolve().parent / 'models' / 'punctuation' / 'model.int8.onnx'
FILLERS = '嗯呃啊'

# Dots inside acronyms, file names, module names and domains are content.
# Stripping them before CT punctuation silently changed S.O.P. to SOP and
# my.file.txt to myfiletxt. Preserve these literal spans without asking a
# sentence-punctuation model to reconstruct them.
DOTTED_LITERAL = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)+\.?')
TOPIC_TRANSITION = re.compile(
    r'(?:接下来|另外|此外|其次|最后|总之|换个话题|再说一个|另一方面)'
    r'|(?:Next|Furthermore|Finally|In conclusion|On another topic)\b', re.I)


def incomplete_tail(text):
    """Only clearly unfinished constructions, not a guess about every pause."""
    bare = text.rstrip(' ，。,.!?！？；;：:')
    return bool(re.search(r'(?:的是|本身是|原因是|比如说|例如|因为|所以说|还有就是|然后就是|为了|如果|但是|以及|我想|我希望|我打算|需要|准备|关于|通过|是否|有没有)$',bare)
                or re.search(r'\b(?:because|and|or|but|which|that|want to|going to|in order to|such as|to|of|for|with)$',bare,re.I))


def boundary_patch(prior, current, boundary):
    """Return a tail-only layout edit if its exact word context is still owned."""
    if not boundary or not prior or prior.endswith('\n'):
        return 0, current
    last = prior.rsplit('\n',1)[-1]
    key = boundary.get('context','')
    if not key or not content(last).casefold().endswith(key):
        return 0, current
    old = re.search(r'[ ，。,.]*$',last).group()
    if last.rstrip(' ').endswith(('?', '？', '!', '！', ';', '；', ':', '：')):
        return 0, current
    return len(old), boundary['separator'] + current.lstrip(' ')


def trim_fillers(text, protected=()):
    """Remove hesitation in a longer statement, preserving literal mentions.

    A lone 啊 / 啊？ / 好啊 stays: it can be a response, question or emotion.
    Ambiguous semantic particles are deliberately not globally deleted.
    """
    if len(content(text)) < 4:
        return text
    spans = [(m.start(), m.end()) for m in QUOTED.finditer(text)]
    spans.extend(m.span() for m in re.finditer(r'嗯哼|啊嚏|呃逆|哦豁', text))
    for term in protected:
        for m in re.finditer(re.escape(term), text):
            spans.append((m.start(), m.end()))
    # “刚才啊这个语气词” describes a word instead of hesitating.
    if re.search(r'(?:语气词|语气助词|这个字|那个字|这个词|那个词|读音|怎么读)', text):
        return text
    def replace(match):
        if any(a < match.end() and b > match.start() for a, b in spans):
            return match.group()
        start, end = match.span()
        before, after = text[:start], text[end:]
        word = match.group().rstrip('，,、 ')
        # Mentioning or reporting a vocal response is meaningful speech.
        if re.search(r'(?:回答|答复|回复|喊|叫|发出|说了|说)[ ：:]*$', before):
            return match.group()
        if '啊' in word:
            # Do not remove sentence-final particles or exclamatory opening 啊！
            if not re.search(r'[\u4e00-\u9fffA-Za-z0-9]', after):
                return match.group()
            if after.startswith(('！', '!', '？', '?')):
                return match.group()
            if not (not before.strip() or re.search(r'[。；，,;]\s*$', before)
                    or before.endswith(('然后', '就是', '这个', '那个', '我想', '我觉得',
                                        '我认为', '其实', '还有', '比如', '比如说', '但是',
                                        '所以', '我们', '咱们', '需要', '准备'))):
                return match.group()
        return ''
    return re.sub(r'[嗯呃啊]{1,4}[，,、 ]*', replace, text)


class Punctuator:
    def __init__(self):
        import sherpa_onnx
        if not MODEL.is_file():
            raise FileNotFoundError('缺少本地标点模型')
        self.model = sherpa_onnx.OfflinePunctuation(sherpa_onnx.OfflinePunctuationConfig(
            model=sherpa_onnx.OfflinePunctuationModelConfig(ct_transformer=str(MODEL), num_threads=2)))

    def apply(self, text):
        if not text.strip() or len(text) > 500:
            return text
        # Literal text, paths, code and multiline input are not punctuation input.
        if (QUOTED.search(text) or DOTTED_LITERAL.search(text)
                or re.search(r'https?://|[A-Za-z]:[\\/]|[$/][a-z][\w-]*|\d[,.:/-]\d|\n', text)):
            return text
        # This model expects unpunctuated text; passing an existing full stop
        # directly can produce two stops. Numeral punctuation is excluded above.
        bare = re.sub(r'[，。！？；：、,.!?;]', '', text)
        result = self.model.add_punctuation(bare).strip()
        result = re.sub(r'([，。！？；：])\1+',r'\1',result)
        if not re.search(r'[\u3400-\u9fff]', text):
            # Prefer punctuation from the acoustic model for English. CT was
            # trained primarily on Chinese and must not impose Chinese marks.
            if re.search(r'[.!?,;:]\s*$',text):
                result = text
            else:
                result = result.translate(str.maketrans('，。！？；：', ',.!?;:'))
                result = re.sub(r'([,;:!?])(?=[A-Za-z])',r'\1 ',result)
        if incomplete_tail(result):
            result = result.rstrip('。.')
        # CT punctuation is permitted to insert punctuation, never lose/change a word.
        if content(result) != content(text):
            return text
        from punctuation_guard import preserve_compounds
        return preserve_compounds(text,result)

    def boundary(self, previous, current, pause=0):
        previous = previous.rsplit('\n',1)[-1][-100:]
        if (not previous or not current or len(current)>256
                or previous.rstrip().endswith(('？','?','！','!','；',';',':','：'))
                or QUOTED.search(previous+current) or DOTTED_LITERAL.search(previous+current)
                or re.search(r'https?://|[A-Za-z]:[\\/]|[$/][a-z][\w-]*|\d[,.:/-]\d|\n',previous+current)):
            return None
        unfinished = incomplete_tail(previous)
        if (not unfinished and previous.rstrip().endswith(('。', '.'))
                and TOPIC_TRANSITION.match(current.lstrip())):
            return None
        # A decoder endpoint is an audio chunk, not proof of a sentence end.
        # Reconsider the boundary with the following words even after a long
        # thinking pause. Exact owned-span checks guard the eventual patch.
        left = previous.rstrip(' ，。,.')
        combined = left + (' ' if left[-1:].isascii() and current[:1].isascii() else '') + current
        laid_out = self.apply(combined)
        before = content(left)
        count = 0
        cut = None
        for i,char in enumerate(laid_out):
            count += len(content(char))
            if count == len(before):
                cut = i+1
                break
        if cut is None or content(laid_out[:cut]) != before:
            return None
        separator = re.match(r'[\s，。,.!?！？；;：:]*',laid_out[cut:]).group()
        if unfinished:
            separator = ''
        if separator.strip() not in ('', '，', ',', '。', '.'):
            return None
        if not re.search(r'[\u3400-\u9fff]',left+current):
            separator = separator.replace('，',',').replace('。','.')
            separator = separator.rstrip() + ' '
        return dict(context=content(previous).casefold(),separator=separator)


def paragraph_prefix(text, prior, pause, enabled=True):
    """Require a topic transition as well as a complete sentence and pause."""
    if (enabled and pause >= 1.8 and len(content(prior.rsplit('\n',1)[-1])) >= 18
            and prior.rstrip().endswith(('。', '！', '？', '.', '!', '?'))
            and not incomplete_tail(prior)
            and TOPIC_TRANSITION.match(text.lstrip())
            and text.strip() and not text.startswith('\n')):
        return '\n'
    return ''


def original_punctuation(original, corrected):
    """Keep source marks/layout; accepted English words still need spaces."""
    source,target=content(original),content(corrected)
    mapping={}
    ascii_fold=str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')
    for op,i,j,a,b in SequenceMatcher(None,source.translate(ascii_fold),target.translate(ascii_fold),autojunk=False).get_opcodes():
        for pos in range(i,j+1):
            mapping[pos]=a+min(pos-i,b-a)
    separators={}
    pos=0
    for char in original:
        if content(char):pos+=1
        else:separators.setdefault(mapping.get(pos,len(target)),[]).append(char)
    # A correction may expand IA to I I A A or split an English word. Turning
    # semantic punctuation off must not glue its accepted letters/words together.
    pos=0
    for index,char in enumerate(corrected):
        if content(char):pos+=1
        elif (char in ' \t' and 0<index<len(corrected)-1
              and re.fullmatch('[A-Za-z]',corrected[index-1])
              and re.fullmatch('[A-Za-z]',corrected[index+1]) and not separators.get(pos)):
            separators[pos]=[' ']
    return ''.join(''.join(separators.get(i,[]))+(target[i] if i<len(target) else '') for i in range(len(target)+1))
