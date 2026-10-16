"""Tolerant local preferences; no malformed preference can prevent startup."""
import json
from core import validate_bindings

DEFAULTS = {"dictation_key": "alt", "workflow_key": "x2", "activation": "toggle",
            "device": None, "device_signature": None, "resume_at_end": False, "enabled": True,
            "auto_correct": True, "correction_mode": "light", "voice_send": True, "send_key": "enter", "workflow_action": "paste_origin",
            "noise_filter": True, "clean_fillers": True, "punctuation": True, "paragraphs": True,
            "skill_commands": True, "skill_syntax": "dollar", "ai_assist": False, "audio_learning": False,
            "ai_provider": "glm", "onboarding_done": False, "hud_position": None,
            "volume_filter":False,"volume_min_db":-65.0,"volume_max_db":-4.0}

def load_settings(path):
    config = DEFAULTS.copy()
    try:
        saved = json.loads(path.read_text("utf-8"))
        if not isinstance(saved, dict):
            return config
        first, second = validate_bindings(saved.get("dictation_key", DEFAULTS["dictation_key"]), saved.get("workflow_key", DEFAULTS["workflow_key"]))
        config.update(dictation_key=first, workflow_key=second)
        position = saved.get('hud_position')
        if (isinstance(position, list) and len(position) == 2
                and all(type(value) is int and abs(value) <= 1000000 for value in position)):
            config['hud_position'] = position
        if saved.get("activation") in ("toggle", "continuous"):
            config["activation"] = saved["activation"]
        device = saved.get("device")
        if type(device) is int and device >= 0:
            config["device"] = device
        signature = saved.get("device_signature")
        if isinstance(signature, dict) and all(isinstance(signature.get(key), str) for key in ("name", "hostapi")):
            config["device_signature"] = {key: signature[key] for key in ("name", "hostapi")}
        for name in ("resume_at_end", "enabled", "auto_correct", "voice_send", "noise_filter", "clean_fillers", "punctuation", "paragraphs", "skill_commands", "ai_assist", "audio_learning", "onboarding_done", "volume_filter"):
            if type(saved.get(name)) is bool:
                config[name] = saved[name]
        try:
            from volume_window import limits
            _,low,high=limits(config['volume_filter'],saved.get('volume_min_db',-65),saved.get('volume_max_db',-4))
            config.update(volume_min_db=low,volume_max_db=high)
        except (ValueError,TypeError,OverflowError):
            config['volume_filter']=False
        config['ai_provider'] = saved.get('ai_provider') if saved.get('ai_provider') in ('glm','codex') else ('codex' if saved.get('ai_assist') else 'glm')
        if saved.get("send_key") in ("enter","ctrl+enter"):
            config["send_key"]=saved["send_key"]
        if saved.get('skill_syntax') in ('dollar','slash','natural'):
            config['skill_syntax']=saved['skill_syntax']
        if saved.get("correction_mode") in ("light", "personal", "raw"):
            config["correction_mode"] = saved["correction_mode"]
        elif saved.get("auto_correct") is False:
            config["correction_mode"] = "raw"
        config["auto_correct"] = config["correction_mode"] != "raw"
        if saved.get("workflow_action") in ("paste_origin", "copy", "prepare"):
            config["workflow_action"] = saved["workflow_action"]
    except (OSError, ValueError, TypeError, AttributeError):
        return DEFAULTS.copy()
    return config

def save_settings(path, config):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
