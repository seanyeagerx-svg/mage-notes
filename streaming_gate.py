"""Bounded first-commit gate; it never rewrites a recognition hypothesis."""


class InitialLanguageGate:
    """Give disagreeing language streams one more acoustic chunk to settle.

    This only holds the first non-final output for one further 320-ms model
    chunk (checked at each audio packet). Once any text is committed, the append-only owner handles
    every further update. Final results always bypass this gate.
    """
    def __init__(self):
        self.started = None

    def allowed(self, chinese, bilingual, english, elapsed, emitted, final=False):
        if final or emitted or english:
            return True
        chinese = chinese.strip()
        bilingual = bilingual.strip()
        # The emitter itself holds a Chinese opening shorter than three chars.
        if len(chinese) < 3:
            return True
        if len(bilingual) >= 3 and chinese[:3] == bilingual[:3]:
            return True
        if self.started is None:
            self.started = elapsed
        return elapsed - self.started >= .32
