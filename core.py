"""Pure state, routing, and append-only transcript handling; no OS side effects."""
from dataclasses import dataclass
import re
import unicodedata


class StableEmitter:
    """Only append stable prefixes. Never delete already typed text, even on ASR revision."""
    def __init__(self):
        self.previous = ""
        self.emitted = ""
        self.conflict = False

    def accept(self, hypothesis: str, final=False) -> str:
        hypothesis = hypothesis.strip()
        # Byte-based ASR tokens can temporarily end inside a UTF-8 character.
        # U+FFFD is not stable text even when two consecutive frames contain it.
        # Wait for the completed character rather than committing a replacement
        # glyph that would later look like a destructive revision of typed text.
        if "\ufffd" in hypothesis:
            hypothesis = hypothesis.split("\ufffd", 1)[0]
        if final:
            stable = hypothesis
        else:
            i = 0
            while i < min(len(hypothesis), len(self.previous)) and hypothesis[i] == self.previous[i]:
                i += 1
            stable = hypothesis[:i]
        self.previous = hypothesis
        if self.conflict:
            return ""
        if len(stable) < len(self.emitted) and self.emitted.startswith(stable):
            return ""
        if not stable.startswith(self.emitted):
            self.conflict = True
            return ""
        suffix = stable[len(self.emitted):]
        self.emitted = stable
        return suffix


class WordStableEmitter(StableEmitter):
    """Commit completed English words and avoid a spurious two-character start.

    The external text remains append-only here. Manual edits are never undone
    by a streaming hypothesis; the existing guarded final pass owns revisions.
    """
    hold_han=0

    def accept(self,hypothesis: str,final=False)->str:
        hypothesis=hypothesis.strip().split('\ufffd',1)[0]
        if final:stable=hypothesis
        else:
            i=0
            while i<min(len(hypothesis),len(self.previous)) and hypothesis[i]==self.previous[i]:i+=1
            stable=hypothesis[:i]
            # A shared BPE fragment is not a stable word. Keep the whole final
            # word pending until a following boundary or sentence end arrives.
            stable=re.sub(r"[A-Za-z][A-Za-z0-9_'’-]*$",'',stable)
            hold=self.hold_han
            if hold:
                stable=re.sub(r'[\u3400-\u9fff]{1,'+str(hold)+r'}$','',stable)
            if not self.emitted and not re.search('[A-Za-z]',stable) and len(stable)<3:
                stable=''
        self.previous=hypothesis
        if len(stable)<len(self.emitted) and self.emitted.startswith(stable):return ''
        if not stable.startswith(self.emitted):
            self.conflict=True;return ''
        # A transient language/hypothesis switch must not freeze this whole
        # utterance. Resume only after the stable text once again contains the
        # entire exact emitted prefix; no byte already typed is changed.
        self.conflict=False
        suffix=stable[len(self.emitted):];self.emitted=stable
        return suffix


class PhraseStableEmitter(WordStableEmitter):
    """Hold two unfinished Chinese characters so a mixed-language word can settle."""
    hold_han=2


@dataclass(frozen=True)
class Route:
    mode: str
    label: str
    reason: str
    text: str


LABELS = {"image": "生成图片", "palette": "制作色卡", "reference": "参考图素材",
          "unmatched": "待选择工作流", "ambiguous": "需要选择工作流"}


def route_workflow(text: str) -> Route:
    """Route the opening command, not arbitrary nouns inside the whole dictation.

    Handles polite fillers, reordered noun/verb phrases, and negated alternatives.
    No arbitrary code, file lookup, or cloud model is invoked by matching.
    """
    normalized = unicodedata.normalize("NFKC", text).lower().strip()
    normalized = re.sub(r"\s+", "", normalized)
    prefix = normalized[:56]
    # A command ends before body markers; quoted prompt contents must not choose the mode.
    prefix = re.split(r"内容是|正文是|提示词是|描述如下|要求如下|以下内容", prefix)[0]
    # Mentioned/quoted examples are not commands, even if they contain a verb.
    if re.match(r'^[“「『\"\']',prefix):
        return Route('unmatched',LABELS['unmatched'],'引用内容不作为工作流口令',text)
    prefix=prefix.replace('提取配色','提取色卡').replace('提取颜色','提取色卡')
    prefix=prefix.replace('参考资料','参考素材').replace('参考素材','参考图')
    prefix=re.sub(r'(找|搜|查找|搜索)(些|一些|点)?参考(?=$|[，。,:：])',r'\1\2参考图',prefix)
    # Workflow selection may use a narrow homophone plus explicit color context.
    # This only proposes a workflow; it never rewrites dictated text or starts it.
    # Keep ordinal/list/card-count phrases and questions outside this exception.
    if re.search(r"配色|色调|颜色|色彩|调色", prefix):
        prefix = re.sub(r"(?<!第)四[、]?卡(?=制作|生成)", "色卡", prefix)
    prefix = prefix.replace("色卡制作", "制作色卡").replace("色卡生成", "生成色卡")
    prefix = prefix.replace("图片生成", "生成图片").replace("图片制作", "制作图片")
    prefix = prefix.replace("配色卡", "色卡").replace("调色板", "色卡")
    prefix = prefix.replace("配色板", "色卡").replace("色彩卡", "色卡").replace("调色盘", "色卡")
    prefix = prefix.replace("颜色卡", "色卡").replace("配色方案", "色卡")
    prefix = prefix.replace("参考图片", "参考图")
    # Known ASR homophones are normalized for routing only; never rewrite the
    # user's dictation or original workflow request.
    prefix = prefix.replace("电图", "垫图")
    prefix = re.sub(r"(制作|生成|做|搞|弄)各(?=色卡)", r"\1个", prefix)
    # Explicit negation anywhere in the clause vetoes that mode in that clause.
    clauses = re.split(r"[，。！？；,!?;]|但是|而是|改成|改为|换成", prefix)
    candidates = set()
    for clause in clauses:
        if not clause:
            continue
        # Speech fillers may omit punctuation: split an explicit positive correction.
        parts = re.split(r"(?:不要|不用|别)(.{0,10}?)(?=我要|我想|请|帮我)", clause)
        if len(parts) > 1:
            clause = parts[-1]
        target = re.search(r"色卡|参考图|垫图|素材图|图片|图像|生图|插画|海报|视频|分镜", clause)
        before_target = clause[:target.start()] if target else clause
        if re.search(r"不要|不用|别|先不|暂不|不需要|并不|不是要|不想", before_target):
            continue
        if re.search(r"怎么|如何|是什么|什么意思|讨论|介绍|讲讲", clause):
            continue
        # Require a target at the beginning after filler and an action, or the target alone.
        head = clause
        for _ in range(3):
            head = re.sub(r"^(嗯|呃|那个|就是|然后|好的|好|现在|接下来|我想要|我想|我要|要|请|帮我|给我|替我|咱们|我们|来|先|开始|麻烦|可以|做一下|做一个|做个)+", "", head)
            head = re.sub(r"^(?:这?让|用|叫).{0,10}?(?=帮我|给我|替我)", "", head)
            head = re.sub(r"^(?:让|用)?(?:chatgpt|gpt|龙虾|你)(?:来|帮我)?", "", head)
            head = re.sub(r"^(?:进入|切换到|开启)(?=色卡|生图|参考图|视频)", "", head)
        patterns = {
            "palette": r"^(?:制作|生成|做|出|提取|提炼|整理|设计|搞|弄|建立)?(?:一张|一套|一个|个|一下|张)?色卡",
            "image": r"^(?:生成|制作|画|绘制|做|出|搞|弄)(?:一张|一幅|一个|个|一下|张)?(?:图片|图像|图|画|插画|海报)|^(?:画|绘制)(?:一张|一幅|一只|一座|一朵|一个)(?!参考|垫图|素材)|^生图",
            "reference": r"^(?:生成|制作|绘制|画|寻找|查找|检索|找找|找|搜一搜|搜索|搜|整理|使用|做|准备)?(?:一张|一些|些|点|几张|一组|个|一下)?(?:参考图|垫图|素材图)",
        }
        for mode, pattern in patterns.items():
            if re.search(pattern, head):
                candidates.add(mode)
        listed=re.match(r'^(?:生成|制作|做)(?:一张|一段|一个|个|一下)?((?:图片|图像|视频|短片)(?:(?:和|以及|与|、)(?:图片|图像|视频|短片))+)',head)
        if listed:
            if re.search(r'图片|图像',listed[1]):candidates.add('image')
        if re.search(r"^(?:把|用|根据|按照|参照|从).{0,16}?(?:制作|做成|生成|提取|设计).{0,5}?色卡", head):
            candidates.add("palette")
    if len(candidates) == 1:
        mode = candidates.pop()
        return Route(mode, LABELS[mode], "根据开头口令识别；可手动改选", text)
    if len(candidates) > 1:
        return Route("ambiguous", LABELS["ambiguous"], "开头包含多个制作目标，请选一个", text)
    return Route("unmatched", LABELS["unmatched"], "保留原话，请选择工作流", text)


@dataclass
class SessionState:
    mode: str = "idle"
    paused: bool = False
    generation: int = 0

    def press(self, requested: str, continuous: bool = False) -> str:
        if self.mode == "idle":
            self.mode = requested
            self.paused = False
            self.generation += 1
            return "start"
        if self.mode != requested:
            return "busy"
        if continuous:
            self.paused = not self.paused
            if not self.paused:
                self.generation += 1
            return "pause" if self.paused else "resume"
        self.mode = "idle"
        self.paused = False
        return "stop"

    def finish(self):
        self.mode = "idle"
        self.paused = False


def validate_bindings(first: str, second: str) -> tuple[str, str]:
    from hotkeys import normalize_binding, bindings_overlap
    values=tuple(normalize_binding(value) for value in (first,second))
    if bindings_overlap(*values):raise ValueError("这个按键与另一个模式重复，请换一个。")
    return values
