"""Decide whether two-source separation is useful, without transcript labels.

Single-source recordings must stay bit-identical: even a correct separator can
distort consonants. An uncertain identity never authorizes dropping speech or
substituting the loudest voice. A reference is required to choose a speaker.
"""
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class FocusDecision:
    action: str
    choice: int | None
    balance_db: float
    reason: str


def decide(outputs, scores, raw_score=None, *, owner_min=.60, margin_min=.15, balance_min=-15., identity_gain_min=.08):
    if len(outputs)!=2 or len(scores)!=2:
        return FocusDecision('original',None,float('-inf'),'invalid_sources')
    signals=[np.asarray(x,dtype=np.float32) for x in outputs]
    if any(x.ndim!=1 or not len(x) or not np.isfinite(x).all() for x in signals):
        return FocusDecision('original',None,float('-inf'),'invalid_audio')
    if signals[0].shape!=signals[1].shape:
        return FocusDecision('original',None,float('-inf'),'length_mismatch')
    energy=[float(np.dot(x.astype(np.float64),x.astype(np.float64))) for x in signals]
    balance=float(10*np.log10(max(min(energy),1e-20)/max(max(energy),1e-20)))
    if max(energy)<1e-12 or balance<balance_min:
        return FocusDecision('original',None,balance,'single_source')
    if any(x is None or not np.isfinite(x) for x in scores):
        return FocusDecision('original',None,balance,'no_reference')
    choice=int(np.argmax(scores));other=1-choice
    if scores[choice]<owner_min or scores[choice]-scores[other]<margin_min:
        return FocusDecision('original',None,balance,'uncertain_identity')
    # A separator can split a single speaker into two different-looking halves.
    # Require the selected stream to identify the owner better than the raw
    # recording; balanced output energy alone does not prove two people spoke.
    if raw_score is None or not np.isfinite(raw_score) or scores[choice]-raw_score<identity_gain_min:
        return FocusDecision('original',None,balance,'no_identity_improvement')
    return FocusDecision('separated',choice,balance,'two_sources_owner_matched')


def apply(original,outputs,scores,raw_score=None):
    decision=decide(outputs,scores,raw_score)
    if decision.action!='separated':return original,decision
    chosen=np.asarray(outputs[decision.choice],dtype=np.float32)
    if chosen.shape!=np.asarray(original).shape:
        return original,FocusDecision('original',None,decision.balance_db,'length_mismatch')
    return chosen,decision
