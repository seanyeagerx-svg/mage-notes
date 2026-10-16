"""Local workflow planning, exact template delivery, and durable handoff bundles."""
from dataclasses import dataclass, asdict
from datetime import datetime
import hashlib
import json
import re
from pathlib import Path
import shutil
from core import LABELS, route_workflow

ROOT = Path(__file__).resolve().parent
TEMPLATE_SOURCE = str(ROOT / 'templates' / 'palette-public.md')

@dataclass
class WorkflowPlan:
    mode: str
    label: str
    request: str
    prompt: str
    steps: list[str]
    reference_files: list[str]
    template_name: str | None = None
    template_sha256: str | None = None
    missing_inputs: list[str] | None = None
    output: str = 'image'
    documents: list[dict] | None = None
    stage: str | None = None

def prepare_workflow(text, references=(), mode=None):
    mode = mode or route_workflow(text).mode
    if mode not in ("image", "palette", "reference"):
        raise ValueError("没有确定的工作流，已保留原话。")
    if not text.strip():
        raise ValueError("工作流还没有内容。")
    refs = list(dict.fromkeys(str(Path(p).resolve()) for p in references))
    if any(Path(p).suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".bmp") for p in refs):
        raise ValueError("这个流程只接收 PNG、JPG、WebP 或 BMP 参考图。")
    missing = [f"参考图不存在：{Path(p).name}" for p in refs if not Path(p).is_file()]
    template_name = template_hash = None
    output = 'image'
    if mode == "palette":
        template_path = ROOT / "templates" / "palette-public.md"
        template = template_path.read_text("utf-8")
        template_name = "通用色卡模板"
        template_hash = hashlib.sha256(template_path.read_bytes()).hexdigest()
        prompt = template.rstrip() + "\n\n本次补充要求（已确认的具体要求优先于模板建议）：\n" + text.strip()
        steps = ["匹配色卡原文模板", "组装口述要求与参考图", "在 GPT 中分析参考图并取得色卡提示词", "按得到的提示词生成色卡图"]
        if not refs:
            missing.append("原文模板以参考图为输入；尚未选择参考图。")
    elif mode == "image":
        prompt = "请根据下面的本次需求生成图片。遵守用户已经说明的内容、画幅和风格；若提供参考图，请按本次需求使用。\n\n本次需求：\n" + text.strip()
        steps = ["保留本次生图要求", "附上本次参考图", "在 GPT 网页生成图片", "取得生成图片并返回原对话"]
    else:
        # Only an explicit generation command requests a new reference image.
        # Search, organize and use retain a text/reference-material result.
        command = re.split(r'内容是|正文是|提示词是|描述如下|要求如下|以下内容|[，。；,;]', text, maxsplit=1)[0]
        command = command.replace('参考图片','参考图').replace('电图','垫图')
        command = re.sub(r'(参考图|垫图|素材图)(生成|制作|绘制)',r'\2\1',command)
        output = 'image' if re.search(r'(?:生成|制作|绘制|画|做)(?:一张|一些|几张|一组|个)?(?:参考图|参考图片|垫图|素材图)', command) and not re.search(r'不要|不用|别|先不|暂不', command) else 'text'
        prompt = "请按下面的需求处理本次参考图或垫图。先根据用户动词判断是寻找、整理、使用还是生成参考素材；不要把参考图片本身当成最终成图。\n\n本次需求：\n" + text.strip()
        steps = ["识别参考素材需求", "整理本次图片及其顺序", "把需求与素材交给目标助手", "保留选定参考图供后续使用"]
        if output == 'text':
            if not refs and re.search(r'(?:整理|使用)(?:一下|这些|这张|本次|一些)?(?:参考图|垫图|素材图)',command):
                missing.append('整理或使用已有素材需要参考图；尚未选择参考图。')
            prompt += "\n\n请返回可直接使用的文字结果。寻找素材时保留真实来源链接；无法检索时明确说明，不编造来源。整理已提供素材时按上传顺序标注。此步骤不生成最终成图。"
            steps = ["保留参考素材需求", "附上已有素材", "在 GPT 中检索或整理素材", "返回文字结果和来源链接"]
        else:
            steps = ["保留参考图生成要求", "附上已有素材", "在 GPT 中生成参考图", "返回参考图原文件"]
    if refs:
        prompt += "\n\n本次参考图上传顺序：\n" + "\n".join(f"{i}. {Path(p).name}" for i,p in enumerate(refs,1))
    return WorkflowPlan(mode, LABELS[mode], text, prompt, steps, refs, template_name, template_hash, missing, output)

def save_bundle(plan, folder, origin_window=None):
    """Save original references beside the prompt; never claim remote dispatch."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    task_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = folder / task_id
    destination.mkdir()
    attachments = []
    if plan.reference_files:
        (destination / "references").mkdir()
        for index, reference in enumerate(plan.reference_files, 1):
            source = Path(reference)
            if not source.is_file():
                continue
            before = source.stat()
            copied = destination / "references" / f"{index:02d}-{source.name}"
            shutil.copy2(source, copied)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("参考图在整理时发生变化，请重新整理：" + source.name)
            with copied.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            attachments.append({"source": str(source), "file": str(copied.relative_to(destination)), "sha256": digest})
    record = asdict(plan)
    record.update(schema_version=2, task_id=task_id, created_at=datetime.now().astimezone().isoformat(),
                  dispatch_status="waiting_for_web_executor", origin_window=origin_window,
                  attachments=attachments, template_source=TEMPLATE_SOURCE if plan.mode == "palette" else None)
    (destination / "prompt.txt").write_text(plan.prompt, "utf-8")
    (destination / "request.txt").write_text(plan.request, "utf-8")
    if plan.mode == "palette":
        shutil.copy2(ROOT / "templates" / "palette-public.md", destination / "template-original.md")
    (destination / "task.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), "utf-8")
    return destination
