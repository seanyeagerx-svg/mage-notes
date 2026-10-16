"""Non-content runtime attestation, so source updates aren't mistaken for deployment."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path

VERSION='1.0.0-beta10-public.1'

SOURCE_FILES=('acoustic_context.py', 'acoustic_english.py', 'acoustic_mixed.py', 'acoustic_vote.py', 'ai_proofreader.py', 'app.py', 'audio_backpressure.py', 'audio_frontend.py', 'audio_gain.py', 'browser_bridge.py', 'chinese_script.py', 'cleanup.py', 'cloud_proofreader.py', 'core.py', 'dictation_cleanup.py', 'dictation_policy.py', 'display.py', 'download_model.py', 'draft_documents.py', 'draft_history.py', 'draft_store.py', 'draft_ui.py', 'draft_writer.py', 'empty_english.py', 'engine.py', 'english_completion12.py', 'english_evidence.py', 'english_language.py', 'english_orthography.py', 'english_prefix.py', 'english_recognition.py', 'english_script_recovery.py', 'english_selector.py', 'english_short_word.py', 'final_transcript.py', 'firered_recognition.py', 'floating_hud.py', 'focus_probe.py', 'focused_draft.py', 'hotkeys.py', 'language_routing.py', 'learning_status.py', 'library_ui.py', 'light_correction.py', 'literal_spelling.py', 'owned_process.py', 'personal_exchange.py', 'personal_learning.py', 'personal_ui.py', 'personal_word_memory.py', 'personalization.py', 'project_store.py', 'project_ui.py', 'pronunciation_memory.py', 'proofreading_history.py', 'provider_secret.py', 'punctuation_guard.py', 'recognition.py', 'reference_enrollment.py', 'relay_handoff.py', 'reuse_library.py', 'runtime_status.py', 'semantic_route.py', 'send_controller.py', 'separation_client.py', 'separation_protocol.py', 'separation_worker.py', 'session_diagnostics.py', 'settings.py', 'setup_bilingual.py', 'skill_commands.py', 'speaker_focus.py', 'speaker_target.py', 'speech_text.py', 'streaming_gate.py', 'ui.py', 'v1_ui.py', 'video_entry.py', 'voice_identity.py', 'voice_send.py', 'volume_ui.py', 'volume_window.py', 'whisper_recognition.py', 'win_input.py', 'word_audio_features.py', 'workflow_voice.py', 'workflows.py', 'requirements.txt', 'templates/palette-public.md')


def write_status(folder, started_at, state, engine):
    root=Path(__file__).resolve().parent
    refiner=getattr(getattr(engine,'decoder',None),'refiner',None)
    multilingual=getattr(refiner,'multilingual',None)
    frontend=getattr(engine,'frontend',None)
    target=getattr(refiner,'target',None)
    reference=getattr(target,'reference',None)
    value=dict(version=VERSION,pid=os.getpid(),started_at=started_at,state=state,
        observed_at=datetime.now().astimezone().isoformat(),
        acoustic_backend=getattr(refiner,'backend',None),
        multilingual_backend=getattr(multilingual,'backend',None),
        multilingual_error=getattr(refiner,'multilingual_error',None),
        domain_backend=getattr(getattr(refiner,'domain',None),'backend',None),
        domain_error=getattr(refiner,'domain_error',None),
        english_backend=getattr(getattr(refiner,'english',None),'backend',None),
        english_error=getattr(refiner,'english_error',None),
        language_retry_error=getattr(refiner,'language_retry_error',None),
        personal_word_status=getattr(getattr(refiner,'word_memory',None),'status',None),
        personal_word_count=len(getattr(getattr(refiner,'word_memory',None),'references',{})),
        personal_word_error=getattr(refiner,'personal_word_error',None),
        short_english_error=getattr(refiner,'short_english_error',None),
        empty_english_error=getattr(refiner,'empty_english_error',None),
        speaker_reference=getattr(reference,'status',None),
        speaker_reference_clips=getattr(reference,'supporting_clips',0),
        speaker_focus_error=getattr(target,'error',None),
        parallel_refinement=bool(getattr(refiner,'parallel_refinement',False)),
        quiet_compensation=getattr(frontend,'quiet_gain',None) is not None,
        continuation_detector=getattr(frontend,'continuation',None) is not None,
        microphone_recording=getattr(engine,'recording',None) is not None,
        capture_requested=bool(getattr(engine,'capture_requested',False)),
        stage_edit_audio=bool(getattr(getattr(engine,'decoder',None),'retain_audio',False)),
        queued_audio_seconds=round(float(getattr(engine,'backlog_seconds',lambda:0.0)()),3),
        stop_note=list(getattr(engine,'stop_note',None) or ()) or None,
        refinement_in_progress=bool(getattr(getattr(engine,'decoder',None),'refinement_in_progress',False)),
        refinement_deferred=bool(getattr(getattr(engine,'decoder',None),'refinement_deferred',False)))
    path=Path(folder)/'runtime-status.json'
    if state=='starting':
        value['loaded_source_hashes']={name:hashlib.sha256((root/name).read_bytes()).hexdigest()
            for name in SOURCE_FILES}
    elif path.exists():
        try:previous=json.loads(path.read_text('utf-8'))
        except (OSError,ValueError):previous={}
        if not isinstance(previous,dict):previous={}
        if previous.get('pid')==os.getpid():value['loaded_source_hashes']=previous.get('loaded_source_hashes',{})
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf-8')
    os.replace(temporary,path)
