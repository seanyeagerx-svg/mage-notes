"""Mixed-language final ASR is the transcript; live text is a preview.

This affects owned dictation text only. Voice actions keep command_online and
their separate independent-audio agreement. Human edits invalidate ownership.
"""
import re


def final_base(original, utterance, protected=()):
    if not str(utterance.get('primary_backend','')).startswith('Qwen3-ASR') or utterance.get('error'):
        return None
    raw=utterance.get('unprompted_readings')
    if not isinstance(raw,dict):return None
    from final_transcript import tokens
    from acoustic_context import spelling_intent
    from personalization import QUOTED
    primary,whisper=raw.get('primary'),raw.get('whisper')
    if (not tokens(primary) or not tokens(whisper) or not tokens(raw.get('support'))
            or not re.search('[A-Za-z]{2}',primary) or not re.search('[A-Za-z]{2}',whisper)):
        return None
    if spelling_intent(original) or QUOTED.search(original):return None
    from light_correction import protected_changed
    if protected_changed(original, primary, [t for t in protected if isinstance(t, str) and t]):
        return None
    if len(primary)>max(48,len(original)*2+24):return None
    # The primary reader must pass the same independent-family and meaning
    # guards as other final revisions. Its presence alone is not agreement.
    # This retains the existing corroborated mixed-language repair and prevents
    # this entry point from bypassing names, supported previews or polarity.
    from final_transcript import select_final
    selected = select_final(original, raw, protected)
    return primary if selected is not None and tokens(selected) == tokens(primary) else None
