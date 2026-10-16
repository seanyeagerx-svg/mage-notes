"""Portable creative task, sent only by an explicit workflow handoff."""
import base64
import hashlib
import html
import json
from pathlib import Path
import re

MAX_BYTES=16*1024*1024
SCHEMA='voiceinput.mediarelay'


def image_mime(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):return 'image/jpeg'
    if data.startswith(b'RIFF') and data[8:12]==b'WEBP':return 'image/webp'
    raise ValueError('接力参考图需要 PNG、JPG 或 WebP 格式。')


def build_packet(bundle,start=False):
    bundle=Path(bundle).resolve();record=json.loads((bundle/'task.json').read_text('utf-8'))
    mode=record.get('mode')
    if mode not in ('image','palette','reference','video_sop'):raise ValueError('本次工作流类型尚不支持交接。')
    output=record.get('output','image')
    if output not in ('image','text') or (mode not in ('reference','video_sop') and output!='image') or (mode=='video_sop' and output!='text'):
        raise ValueError('本次任务的结果类型无效，请重新整理。')
    if mode=='palette' and (not record.get('template_sha256') or not record.get('attachments')):
        raise ValueError('色卡需要原文模板和参考图，请先补齐本次任务。')
    task=str(record.get('task_id',''));prompt=record.get('prompt','')
    if not re.fullmatch(r'[A-Za-z0-9-]{8,65}',task):raise ValueError('本次任务编号无效。')
    if not isinstance(prompt,str) or not 2<=len(prompt.strip())<=(64000 if mode=='video_sop' else 16000):raise ValueError('生图提示词需要 2～16000 字。')
    attachments=record.get('attachments',[])
    if len(attachments)>8:raise ValueError('一次最多交接 8 张参考图。')
    if record.get('missing_inputs'):raise ValueError('参考图还未准备完整，请先检查本次任务。')
    refs=[];total=0
    for item in attachments:
        path=(bundle/item['file']).resolve()
        if not path.is_relative_to(bundle):raise ValueError('参考图必须属于本次任务。')
        size=path.stat().st_size;total+=size
        if total>MAX_BYTES:raise ValueError('本次交接的参考图合计不能超过 16 MB。')
        data=path.read_bytes();digest=hashlib.sha256(data).hexdigest()
        if len(data)!=size or digest!=item['sha256']:raise ValueError('参考图在保存后发生了变化，请重新整理任务。')
        refs.append(dict(name=path.name,mime=image_mime(data),size=len(data),sha256=digest,data=base64.b64encode(data).decode('ascii')))
    packet=dict(schema=SCHEMA,version=3 if start or mode=='video_sop' else (1 if mode=='image' else 2),taskId=task,requestId='voice-'+task,mode=mode,prompt=prompt,
        references=refs,createdAt=record.get('created_at'))
    if packet['version']==3:packet['start']=bool(start)
    if mode!='image':
        packet['output']=output
        if mode=='palette':packet['templateSha256']=record['template_sha256']
    (bundle/'relay.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2),'utf-8')
    return packet


def clipboard_html(packet):
    encoded=base64.b64encode(json.dumps(packet,ensure_ascii=False,separators=(',',':')).encode('utf-8')).decode('ascii')
    return '<div data-voiceinput-relay="'+encoded+'">'+html.escape(packet['prompt']).replace('\n','<br>')+'</div>'


def cf_html(fragment):
    """CF_HTML offsets count UTF-8 bytes, including non-ASCII prompts."""
    start=b'<html><body><!--StartFragment-->';end=b'<!--EndFragment--></body></html>'
    fragment=fragment.encode('utf-8')
    header='Version:1.0\r\nStartHTML:{:010d}\r\nEndHTML:{:010d}\r\nStartFragment:{:010d}\r\nEndFragment:{:010d}\r\n'
    h=len(header.format(0,0,0,0).encode('ascii'));a=h+len(start);b=a+len(fragment)
    return header.format(h,b+len(end),a,b).encode('ascii')+start+fragment+end+b'\0'


def copy_packet(owner,packet):
    """Write text fallback and HTML together; no clipboard reading or background polling."""
    import ctypes as c
    from ctypes import wintypes as w
    u=c.WinDLL('user32',use_last_error=True);k=c.WinDLL('kernel32',use_last_error=True)
    u.OpenClipboard.argtypes=[w.HWND];u.OpenClipboard.restype=w.BOOL
    u.RegisterClipboardFormatW.argtypes=[w.LPCWSTR];u.RegisterClipboardFormatW.restype=w.UINT
    u.SetClipboardData.argtypes=[w.UINT,w.HANDLE];u.SetClipboardData.restype=w.HANDLE
    k.GlobalAlloc.argtypes=[w.UINT,c.c_size_t];k.GlobalAlloc.restype=w.HGLOBAL
    k.GlobalLock.argtypes=[w.HGLOBAL];k.GlobalLock.restype=c.c_void_p
    k.GlobalUnlock.argtypes=[w.HGLOBAL];k.GlobalFree.argtypes=[w.HGLOBAL]
    formats=[(13,packet['prompt'].encode('utf-16-le')+b'\0\0'),(u.RegisterClipboardFormatW('HTML Format'),cf_html(clipboard_html(packet)))]
    allocated=[];opened=False
    try:
        for kind,data in formats:
            handle=k.GlobalAlloc(0x0002,len(data))
            if not handle:raise OSError('无法准备交接内容。')
            allocated.append([kind,handle]);pointer=k.GlobalLock(handle)
            if not pointer:raise OSError('无法读取交接内容。')
            c.memmove(pointer,data,len(data));k.GlobalUnlock(handle)
        if not u.OpenClipboard(owner):raise OSError('剪贴板正被其他程序使用，请稍后重试。')
        opened=True
        if not u.EmptyClipboard():raise OSError('无法写入剪贴板。')
        for entry in allocated:
            if not u.SetClipboardData(*entry):raise OSError('交接内容未完整写入剪贴板。')
            entry[1]=None
    finally:
        if opened:u.CloseClipboard()
        for _,handle in allocated:
            if handle:k.GlobalFree(handle)
