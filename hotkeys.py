"""Shared key vocabulary and an explicit, short-lived shortcut recorder."""
import re

MODIFIER_VKS={0x10:"shift",0xA0:"shift",0xA1:"shift",0x11:"ctrl",0xA2:"ctrl",0xA3:"ctrl",
              0x12:"alt",0xA4:"alt",0xA5:"alt",0x5B:"win",0x5C:"win"}
MOD_ORDER=("ctrl","alt","shift","win")
KEY_VKS={"backspace":8,"tab":9,"clear":12,"enter":13,"shift":16,"ctrl":17,"alt":18,
         "pause":19,"capslock":20,"escape":27,"space":32,"pageup":33,"pagedown":34,"end":35,"home":36,
         "left":37,"up":38,"right":39,"down":40,"printscreen":44,"insert":45,"delete":46,
         "lwin":91,"rwin":92,"menu":93,"multiply":106,"add":107,"separator":108,"subtract":109,
         "decimal":110,"divide":111,"numlock":144,"scrolllock":145,
         "lshift":160,"rshift":161,"lctrl":162,"rctrl":163,"lalt":164,"ralt":165,
         "browser_back":166,"browser_forward":167,"volume_mute":173,"volume_down":174,"volume_up":175,
         "media_next":176,"media_previous":177,"media_stop":178,"media_play_pause":179,
         "semicolon":186,"equal":187,"comma":188,"minus":189,"period":190,"slash":191,"backtick":192,
         "bracketleft":219,"backslash":220,"bracketright":221,"quote":222}
KEY_VKS.update({chr(v).lower():v for v in range(65,91)})
KEY_VKS.update({str(v):48+v for v in range(10)})
KEY_VKS.update({f"numpad{v}":96+v for v in range(10)})
KEY_VKS.update({f"f{v}":111+v for v in range(1,25)})
VK_KEYS={value:key for key,value in KEY_VKS.items()}
MOUSE_KEYS={"mouse_left","mouse_right","mouse_middle","x1","x2","wheel_up","wheel_down","wheel_left","wheel_right"}
DISPLAY={"ctrl":"Ctrl","alt":"Alt","shift":"Shift","win":"Win","space":"空格","enter":"回车","escape":"Esc",
         "backspace":"退格","delete":"Delete","capslock":"Caps Lock","numlock":"Num Lock","scrolllock":"Scroll Lock",
         "mouse_left":"鼠标左键","mouse_right":"鼠标右键","mouse_middle":"鼠标中键","x1":"鼠标侧键 X1","x2":"鼠标侧键 X2",
         "wheel_up":"滚轮向上","wheel_down":"滚轮向下","wheel_left":"滚轮向左","wheel_right":"滚轮向右",
         "lctrl":"左 Ctrl","rctrl":"右 Ctrl","lshift":"左 Shift","rshift":"右 Shift",
         "lalt":"左 Alt","ralt":"右 Alt","lwin":"左 Win","rwin":"右 Win","numpad_enter":"小键盘回车",
         "volume_up":"音量增加","volume_down":"音量降低","volume_mute":"静音","media_play_pause":"播放 / 暂停"}

def key_name(vk,extended=False):
    if vk==13 and extended:return "numpad_enter"
    return VK_KEYS.get(vk,f"vk_{vk:02x}")

def key_vk(name):
    if name=="numpad_enter":return 13
    if name in KEY_VKS:return KEY_VKS[name]
    if re.fullmatch(r"vk_[0-9a-f]{2}",name):return int(name[3:],16)
    return None

def display_binding(value):
    def pretty(part):
        if part in DISPLAY:return DISPLAY[part]
        if part.startswith("numpad"):return "小键盘 "+part[6:]
        if part.startswith("vk_"):return "按键 "+part[3:].upper()
        return part.upper()
    return " + ".join(pretty(part) for part in value.split("+"))

def normalize_binding(value):
    parts=re.sub(r"\s+","",value.lower()).split("+")
    base=parts[-1]
    if not base or (base not in MOUSE_KEYS and key_vk(base) is None):raise ValueError("没有识别到这个按键，请重新按一下。")
    if len(parts)!=len(set(parts)) or any(m not in MOD_ORDER for m in parts[:-1]):
        raise ValueError("没有识别到有效组合键，请重新按一下。")
    return "+".join([m for m in MOD_ORDER if m in parts[:-1]]+[base])

def base_vks(name):
    if name in ("ctrl","alt","shift"):
        return {v for v,family in MODIFIER_VKS.items() if family==name}
    return {key_vk(name)}

def bindings_overlap(first,second):
    a,b=first.split("+"),second.split("+")
    if set(a[:-1])!=set(b[:-1]):return False
    if a[-1] in MOUSE_KEYS or b[-1] in MOUSE_KEYS:return a[-1]==b[-1]
    if {a[-1],b[-1]}=={"enter","numpad_enter"}:return False
    return bool(base_vks(a[-1]) & base_vks(b[-1]))

class BindingCapture:
    """Record one chord, emit only after every key/button has been released."""
    def __init__(self):
        self.held=set()
        self.modifiers=set()
        self.base=None
        self.standalone=None
        self.error=None
    def keyboard(self,vk,down,extended=False):
        key=("keyboard",vk)
        if down:
            if key in self.held:return None  # key repeat
            self.held.add(key)
            if vk in MODIFIER_VKS:
                self.modifiers.add(MODIFIER_VKS[vk]);self.standalone=key_name(vk,extended)
            else:self._base(key_name(vk,extended))
        else:
            if key not in self.held:return None
            self.held.discard(key)
        return self.result() if not down and not self.held else None
    def mouse(self,key,down):
        marker=("mouse",key)
        if down:
            if marker in self.held:return None
            self.held.add(marker);self._base(key)
        else:
            if marker not in self.held:return None
            self.held.discard(marker)
        return self.result() if not down and not self.held else None
    def _base(self,key):
        if self.base is not None and self.base!=key:self.error="一次只录入一个按键或一组组合键，请重新按。"
        self.base=key
    def preview(self):
        return "+".join([m for m in MOD_ORDER if m in self.modifiers]+([self.base] if self.base else [])) or self.standalone or ""
    def result(self):
        if self.error:return (None,self.error)
        if self.base:return (self.preview(),None)
        if self.standalone:
            family=MODIFIER_VKS[key_vk(self.standalone)]
            return ("+".join([m for m in MOD_ORDER if m in self.modifiers and m!=family]+[self.standalone]),None)
        return (None,"组合键还需要一个主键，请重新按。")
