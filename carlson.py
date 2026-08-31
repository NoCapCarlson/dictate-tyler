#!/usr/bin/env python3
"""
Carlson - hold-to-dictate voice input for the terminal.

Hold 5 for one second, speak, release: polished text is pasted at your
cursor. A quick tap of 5 still types a 5. Esc while recording cancels.
Local Whisper only - no cloud, no API keys. See META_PROMPT.md.
"""
__version__ = "1.10.0"

import argparse
import ctypes
import os
import re
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime

# Law L6: UTF-8 before anything prints a glyph on a cp1252 console.
os.environ.setdefault("PYTHONUTF8", "1")
# HF model downloads need symlinks Windows won't grant unelevated
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SR = 16000                      # Whisper native sample rate
BLOCK = 1600                    # 0.1 s audio blocks
PREROLL_BLOCKS = 8              # 0.8 s of pre-roll so first syllables survive
LIVE_WINDOW_S = 30              # live model looks at the last N seconds
LLKHF_INJECTED = 0x10           # Law L8: our own synthetic events pass free
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
VK_ESCAPE = 0x1B
MODIFIER_VKS = (0x10, 0x11, 0x12, 0x5B, 0x5C)   # shift ctrl alt lwin rwin

READY, RECORDING, POLISHING = "READY", "RECORDING", "POLISHING"

LOG_PATH = os.path.join(os.environ.get("LOCALAPPDATA", "."), "carlson.log")

# pythonw (headless autostart) has no console: give prints somewhere harmless
# to go, and send tracebacks to the log so they are never lost (Law L4).
_NO_CONSOLE = sys.stdout is None or sys.stderr is None
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(LOG_PATH, "a", encoding="utf-8", buffering=1)

DEFAULT_PROMPT = (
    "Working in a PowerShell terminal with Claude Code. Vocabulary: git, "
    "commit, branch, merge, rebase, npm, pnpm, pytest, TypeScript, Python, "
    "Docker, docker compose, repo, refactor, function, variable, endpoint, "
    "API, Postgres, env file, stack trace, pull request."
)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except Exception:
        pass


def log_exc(where):
    log(f"EXCEPTION in {where}:\n{traceback.format_exc()}")


def key_is_down(vk):
    """Async key state. ONLY valid for keys we never suppress (modifiers):
    an event swallowed by a low-level hook never updates this table in
    either direction - proven twice on 2026-08-31 (blind press -> tap-replay
    5555 loop; blind release -> recording that never ends). For the trigger
    key, the hook's own event stream is the single source of truth."""
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def modifier_held(vks=MODIFIER_VKS):
    return any(key_is_down(v) for v in vks)


# ---------------------------------------------------------------------------
# Chimes - synthesized once to wav (soft sine + harmonics, fast attack,
# glassy exponential decay: the Apple-ish sound), played via winsound.
# ---------------------------------------------------------------------------
SOUND_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "."),
                         "carlson-sounds")


def ensure_sounds():
    import wave

    import numpy as np
    os.makedirs(SOUND_DIR, exist_ok=True)
    sr = 44100

    def tone(freq, dur, vol, decay=0.09, attack=0.006):
        n = int(sr * dur)
        t = np.arange(n) / sr
        env = np.minimum(t / attack, 1.0) * np.exp(-t / decay)
        w = (np.sin(2 * np.pi * freq * t)
             + 0.28 * np.sin(4 * np.pi * freq * t)
             + 0.07 * np.sin(6 * np.pi * freq * t))
        return vol * env * w

    def mix(*parts):
        total = max(int(o * sr) + len(a) for o, a in parts)
        out = np.zeros(total)
        for o, a in parts:
            i = int(o * sr)
            out[i:i + len(a)] += a
        out = np.clip(out, -1, 1)
        return (out * 32767 * 0.38).astype("<i2")   # deliberately soft

    def write(name, data):
        p = os.path.join(SOUND_DIR, name)
        if not os.path.exists(p):
            with wave.open(p, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sr)
                w.writeframes(data.tobytes())

    # D5 rising to A5: "listening"; the reverse: "done"; soft low: cancel
    write("start_v1.wav", mix((0.000, tone(587.33, 0.16, 0.80)),
                              (0.085, tone(880.00, 0.24, 0.70))))
    write("done_v1.wav", mix((0.000, tone(880.00, 0.14, 0.70)),
                             (0.075, tone(587.33, 0.24, 0.60))))
    write("cancel_v1.wav", mix((0.000, tone(392.00, 0.12, 0.60)),
                               (0.060, tone(311.13, 0.20, 0.55))))
    write("error_v1.wav", mix((0.000, tone(233.08, 0.30, 0.70, decay=0.12))))

    # owner-picked sounds ship in the repo's assets/ - they win over the
    # synthesized ones (chime() prefers *_custom.wav)
    assets = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "assets")
    if os.path.isdir(assets):
        import shutil
        for f in os.listdir(assets):
            if f.endswith("_custom.wav"):
                dst = os.path.join(SOUND_DIR, f)
                if not os.path.exists(dst):
                    shutil.copyfile(os.path.join(assets, f), dst)


# ---------------------------------------------------------------------------
# Text polish - pure functions, covered by --selftest
# ---------------------------------------------------------------------------
# stretchy fillers: um/umm/ummm, uh/uhh/uhm/uhmm, hm/hmm/hmmm, erm, mm...
_FILLER_RE = re.compile(r"\b(?:um+|uh+m*|erm+|hm+|mm+)\b[,.!?]?\s*", re.I)
_FILLER_FULL = re.compile(r"(?:um+|uh+m*|erm+|hm+|mm+)", re.I)


def _is_filler(word):
    return bool(_FILLER_FULL.fullmatch(word))
_HALLUCINATIONS = {
    "you", "thank you", "thanks for watching", "thank you for watching",
    "bye", "okay", "the end", "so",
}


def polish(text, multiline=False, final=True):
    """Whisper output -> clean prose. Conservative on purpose: never mangle
    file paths, versions, or identifiers. final=False (live partials) skips
    the closing period so the text doesn't churn while being spoken."""
    t = text.strip()
    t = _FILLER_RE.sub("", t)
    # spoken layout commands (a Wispr Flow staple)
    t = re.sub(r"[,.]?\s*\bnew paragraph\b[,.]?\s*", "\n\n", t, flags=re.I)
    t = re.sub(r"[,.]?\s*\bnew line\b[,.]?\s*", "\n", t, flags=re.I)
    if not multiline:
        # multiline paste is dangerous in a shell; flatten by default
        t = re.sub(r"\s*\n+\s*", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)          # no space before punct
    t = re.sub(r"([,;!?])(?=[A-Za-z])", r"\1 ", t)  # after , ; ! ? only - dots
    #                                                 and colons live in paths
    # capitalize sentence starts (also after a kept newline)
    t = re.sub(r"(^|[.!?]\s+|\n\s*)([a-z])",
               lambda m: m.group(1) + m.group(2).upper(), t)
    t = t.strip()
    if final and len(t.split()) >= 4 and t[-1:].isalnum():
        t += "."
    return t


# Live-commit policy: a word is typed only once two consecutive hypotheses
# agree on it (local agreement), so live text is append-only - it NEVER
# jumps back and forth on screen. The one clean rewrite happens at the
# final settle. Fillers never get typed at all.
def _norm_word(w):
    return w.strip(".,!?;:").lower()


def stable_words(prev, cur):
    """Longest common prefix (by normalized word) of two hypotheses,
    returned in the newer hypothesis's surface forms."""
    k = 0
    for a, b in zip(prev, cur):
        if _norm_word(a) != _norm_word(b):
            break
        k += 1
    return cur[:k]


def looks_hallucinated(text, duration_s):
    core = re.sub(r"[^a-z ]", "", text.lower()).strip()
    if not core:
        return True
    return duration_s < 1.2 and core in _HALLUCINATIONS


def load_wav_as_float32(path):
    """Read a wav (any rate, 16-bit) -> 16 kHz mono float32. Test hook."""
    import wave

    import numpy as np
    with wave.open(path, "rb") as w:
        sr, ch, sw, n = (w.getframerate(), w.getnchannels(),
                         w.getsampwidth(), w.getnframes())
        raw = w.readframes(n)
    if sw != 2:
        raise SystemExit(f"expected 16-bit wav, got {sw * 8}-bit")
    x = np.frombuffer(raw, dtype=np.int16)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    x = x.astype(np.float32) / 32768.0
    if sr != SR:
        idx = np.linspace(0, len(x) - 1, int(len(x) * SR / sr))
        x = np.interp(idx, np.arange(len(x)), x).astype(np.float32)
    return x


# ---------------------------------------------------------------------------
# Live injector - the words appear at the caret AS THEY ARE SPOKEN, edited
# in place as the model revises them (common-prefix diff: backspace the
# changed tail, retype). One lock serializes all typing; an epoch counter
# guarantees a stale live partial can never overwrite the final text.
# ---------------------------------------------------------------------------
class Injector:
    def __init__(self, kb):
        self.kb = kb
        self.cur = ""               # what we have typed for the current take
        self.epoch = 0
        self.lock = threading.Lock()

    def new_take(self):
        with self.lock:
            self.epoch += 1
            self.cur = ""
            return self.epoch

    def seal(self, epoch):
        """End a take leaving whatever is already typed in place."""
        with self.lock:
            if epoch == self.epoch:
                self.epoch += 1
                self.cur = ""

    def append(self, text, epoch=None):
        """Append-only live typing: no backspaces, no churn."""
        with self.lock:
            if epoch is not None and epoch != self.epoch:
                return False
            try:
                for i in range(0, len(text), 24):
                    self.kb.type(text[i:i + 24])
                    time.sleep(0.004)
            except Exception:
                log_exc("injector append")
            self.cur += text
            return True

    def _edit(self, target):
        """Caller holds the lock: char-prefix diff, backspace tail, retype."""
        from pynput.keyboard import Key
        cur = self.cur
        k = 0
        for a, b in zip(cur, target):
            if a != b:
                break
            k += 1
        try:
            n = len(cur) - k
            for i in range(n):
                self.kb.press(Key.backspace)
                self.kb.release(Key.backspace)
                if i % 20 == 19:
                    time.sleep(0.004)
            add = target[k:]
            for i in range(0, len(add), 24):
                self.kb.type(add[i:i + 24])
                time.sleep(0.004)
        except Exception:
            log_exc("injector")
        self.cur = target

    def sync(self, text, epoch=None, bump=False):
        with self.lock:
            if epoch is not None and epoch != self.epoch:
                return False
            if bump:
                self.epoch += 1
            self._edit(text)
            return True

    def settle(self, final_text, epoch=None):
        """The calm settle: keep every leading word the live pass already
        got right (compared by normalized word - an added comma or a case
        fix in the middle no longer triggers a full wipe-and-retype), and
        rewrite only from the first REAL word difference. On a perfect
        match, only the last word is retouched so it gains the final
        punctuation."""
        with self.lock:
            if epoch is not None and epoch != self.epoch:
                return False
            self.epoch += 1          # from here, no live append can land
            cw = self.cur.split()
            fw = final_text.split()
            k = 0
            for a, b in zip(cw, fw):
                if _norm_word(a) != _norm_word(b):
                    break
                k += 1
            if fw and k == len(fw) and k == len(cw):
                hybrid = " ".join(cw[:k - 1] + [fw[-1]])
            else:
                hybrid = " ".join(cw[:k] + fw[k:])
            self._edit(hybrid)
            return True


# ---------------------------------------------------------------------------
# On-screen overlay - ONLY the Siri-like orb, transparent background, no
# text: it floats bottom-center while dictation happens and moves with the
# voice. The words themselves go to the caret via the Injector.
# ---------------------------------------------------------------------------
class Overlay:
    W, H = 120, 120
    TRANS = "#010203"          # colorkey: anything this color is see-through

    def __init__(self, app):
        self.app = app
        self.visible = False
        self.t0 = time.monotonic()
        self.root = None
        self._slvl = 0.0            # smoothed voice level for fluid motion

    def start(self):
        threading.Thread(target=self._run, daemon=True,
                         name="overlay").start()

    # every tkinter call happens on this one thread
    def _run(self):
        try:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass
            import tkinter as tk
            root = tk.Tk()
            self.root = root
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            try:
                root.attributes("-transparentcolor", self.TRANS)
            except Exception:
                pass
            root.configure(bg=self.TRANS)
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
            root.geometry(f"{self.W}x{self.H}+{(sw - self.W) // 2}"
                          f"+{sh - self.H - 64}")
            self.canvas = tk.Canvas(root, width=self.W, height=self.H,
                                    bg=self.TRANS, highlightthickness=0)
            self.canvas.pack()
            root.update_idletasks()
            self._click_through()
            root.withdraw()
            log("overlay ready")
            root.after(33, self._tick)
            root.mainloop()
        except Exception:
            log_exc("overlay")

    def _click_through(self):
        """Topmost but intangible: never steals focus, never eats a click."""
        try:
            hwnd = (user32.GetParent(self.root.winfo_id())
                    or self.root.winfo_id())
            GWL_EXSTYLE = -20
            ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            # layered | transparent | noactivate | toolwindow
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE,
                                     ex | 0x80000 | 0x20 | 0x08000000 | 0x80)
        except Exception:
            log_exc("overlay click-through")

    def _tick(self):
        try:
            app = self.app
            now = time.monotonic()
            want = (app.state in (RECORDING, POLISHING)
                    or now < app.flash_until)
            if want and not self.visible:
                self.root.deiconify()
                self.visible = True
            elif not want and self.visible:
                self.root.withdraw()
                self.visible = False
            if self.visible:
                self._draw(now)
            if app.quit.is_set():
                self.root.quit()
                return
        except Exception:
            log_exc("overlay tick")
        try:
            self.root.after(33, self._tick)
        except Exception:
            pass

    def _draw(self, now):
        import math
        app, cv = self.app, self.canvas
        cv.delete("all")
        cx = cy = 60
        t = now - self.t0
        lvl = min(app.level * 14.0, 1.0)
        state = app.state
        # the badge: white ring + blue disc, in every state (clean)
        cv.create_oval(cx - 36, cy - 36, cx + 36, cy + 36,
                       fill="#f2f3f5", outline="")
        cv.create_oval(cx - 30, cy - 30, cx + 30, cy + 30,
                       fill="#007aff", outline="")
        if state == RECORDING:
            # five rounded bars dancing with the voice; smoothed level so
            # motion is fluid, gentle idle breathing when silent
            self._slvl += (lvl - self._slvl) * 0.35
            s = self._slvl
            bars = ((-20, 18, 3.1, 0.0), (-10, 30, 3.8, 1.2),
                    (0, 42, 4.4, 2.4), (10, 24, 3.5, 3.6),
                    (20, 34, 4.1, 4.8))
            for x, baseh, freq, ph in bars:
                lim = 2 * ((900 - x * x) ** 0.5 - 5)
                h = (baseh * (0.32 + 0.68 * s)
                     + 7 * s * math.sin(t * freq * 2.0 + ph)
                     + 2.5 * math.sin(t * 1.7 + ph))
                h = max(7, min(lim, h))
                cv.create_line(cx + x, cy - h / 2, cx + x, cy + h / 2,
                               fill="#f2f3f5", width=5, capstyle="round")
        elif state == POLISHING:
            r = 16
            start = -(t * 300) % 360
            cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=start,
                          extent=110, style="arc", outline="#f2f3f5",
                          width=4)
        else:
            # brief result blink: green check / red cross, then gone
            bad = app.flash_text.startswith(("failed", "too short",
                                             "cancelled", "heard"))
            col = "#f87171" if bad else "#4ade80"
            if bad:
                cv.create_line(cx - 9, cy - 9, cx + 9, cy + 9, fill=col,
                               width=4, capstyle="round")
                cv.create_line(cx - 9, cy + 9, cx + 9, cy - 9, fill=col,
                               width=4, capstyle="round")
            else:
                cv.create_line(cx - 11, cy + 1, cx - 3, cy + 9, cx + 12,
                               cy - 8, fill=col, width=4, capstyle="round",
                               joinstyle="round")


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------
class Carlson:
    def __init__(self, args):
        self.args = args
        name = args.hold_key.strip().lower()
        self.passive = False            # alt mode: no suppression pre-trigger
        if name == "alt":
            # Alt is a modifier: combos (Alt+Tab, Alt+F4...) must keep
            # working, so nothing is suppressed until the hold triggers.
            self.hold_char = None
            self.trigger_vks = (0xA4, 0xA5)          # left / right Alt
            self.key_name = "ALT"
            self.passive = True
            self.guard_vks = (0x10, 0x11, 0x5B, 0x5C)   # shift ctrl win
        elif name in ("space", " "):
            self.hold_char = " "
            self.trigger_vks = (0x20,)
            self.key_name = "SPACE"
            self.guard_vks = MODIFIER_VKS
        else:
            self.hold_char = name
            self.trigger_vks = (ord(name.upper()),)
            self.key_name = name.upper()
            self.guard_vks = MODIFIER_VKS
        self.hold_seconds = args.hold_seconds
        self.max_hold = args.max_hold

        self.state = READY
        self.suppressing = False        # Law L1: guarded by the watchdog
        self.press_time = None
        self.hook_down = False          # physical key state, from the hook
        self.cancel_requested = False
        self.flash_text = ""            # overlay result flash
        self.flash_until = 0.0
        self.last_final = ""
        self.record_start = 0.0
        self.recording = False          # audio callback gate
        self.level = 0.0
        self.partial = ""
        self.live_done = 0.0
        self.live_prev = []             # last live hypothesis (words)
        self.live_words = []            # all words typed this take
        self.live_window_words = []     # committed words inside the window
        self.live_offset = 0.0          # take audio already trimmed away (s)
        self.live_miss = 0              # consecutive alignment failures
        self.status = "ready"
        self.history = deque(maxlen=4)
        self.quit = threading.Event()

        self.ring = deque(maxlen=PREROLL_BLOCKS)
        self.chunks = []

        self.console = None
        self.listener = None
        self.kb = None                  # pynput Controller, set in run()
        self.injector = None            # live caret typing, set in run()
        self.take_epoch = 0
        self.model_live = None
        self.model_final = None

    # --- audio -------------------------------------------------------------
    def _audio_cb(self, indata, frames, tinfo, status):
        import numpy as np
        x = indata.copy()
        try:
            self.level = float(np.sqrt(np.mean(np.square(x)))) or 0.0
        except Exception:
            self.level = 0.0
        if self.recording:
            self.chunks.append(x)
        else:
            self.ring.append(x)

    def snapshot(self, max_seconds=None):
        import numpy as np
        chunks = list(self.chunks)
        if not chunks:
            return np.zeros(0, dtype=np.float32), 0.0
        if max_seconds is not None:
            need = int(max_seconds * SR)
            got, keep = 0, []
            for c in reversed(chunks):
                keep.append(c)
                got += len(c)
                if got >= need:
                    break
            chunks = list(reversed(keep))
        audio = np.concatenate(chunks).flatten().astype(np.float32)
        return audio, len(audio) / SR

    # --- keyboard hook (runs on the hook thread; keep it tiny) --------------
    # The hook is the ONLY truthful observer of the trigger key: it sees
    # every physical event BEFORE deciding to swallow it, while a swallowed
    # event never updates GetAsyncKeyState in either direction. So the key
    # is suppressed from the very first press (nothing ever leaks into the
    # focused app), hook_down tracks the physical state, and a tap is given
    # back as one injected keystroke on early release.
    def _filter(self, msg, data):
        if data.flags & LLKHF_INJECTED:
            return                                   # Law L8
        vk = data.vkCode
        if vk in self.trigger_vks:
            if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
                self.hook_down = True
                if self.suppressing:
                    self.listener.suppress_event()   # swallow auto-repeats
                elif (self.state == READY
                        and not modifier_held(self.guard_vks)):
                    if self.press_time is None:
                        self.press_time = time.monotonic()
                    if not self.passive:
                        self.suppressing = True
                        self.listener.suppress_event()   # last line
            else:
                self.hook_down = False
                if self.suppressing:
                    self.listener.suppress_event()   # swallow the key-up too
        elif (vk == VK_ESCAPE and self.state == RECORDING
                and msg in (WM_KEYDOWN, WM_SYSKEYDOWN)):
            self.cancel_requested = True             # Law L10
            self.listener.suppress_event()
        elif (self.passive and self.press_time is not None
                and self.state == READY
                and msg in (WM_KEYDOWN, WM_SYSKEYDOWN)):
            # another key while Alt is pending: it's a shortcut
            # (Alt+Tab, Alt+F4...) - stand down, let it work natively
            self.press_time = None

    # --- watchdog: the only writer of state transitions (Laws L1, L2, L9) ---
    def watch(self):
        while not self.quit.is_set():
            try:
                down = self.hook_down
                now = time.monotonic()
                if (self.suppressing and self.listener is not None
                        and not self.listener.running):
                    # hook died: nothing is suppressing anything anymore
                    self.suppressing = False
                if self.state == RECORDING:
                    if self.cancel_requested:
                        self.cancel_recording()
                    elif not down:
                        self.finish_recording()
                    elif now - self.record_start > self.max_hold:
                        self.finish_recording(note="max hold reached")
                elif (self.passive and self.state == READY and down
                        and self.press_time is not None
                        and now - self.press_time >= self.hold_seconds):
                    self.begin_passive_hold()
                elif self.suppressing:
                    pt = self.press_time
                    if not down:
                        # early release: it was a tap - give the key back
                        self.suppressing = False
                        self.press_time = None
                        if pt is not None and self.state == READY:
                            self.inject_tap()
                    elif pt is None:
                        self.suppressing = False     # Law L1: never captive
                    elif (self.state == READY
                            and now - pt >= self.hold_seconds):
                        self.start_recording(now)
                elif not down:
                    self.press_time = None
            except Exception:
                log_exc("watch")
                self.suppressing = False
                self.cancel_requested = False
                try:
                    if self.state == RECORDING:
                        self.cancel_recording()
                    else:
                        self.state = READY
                except Exception:
                    log_exc("watch-recover")
                    self.state = READY
            time.sleep(0.015)
        self.suppressing = False

    def inject_tap(self):
        if not self.hold_char:
            return                       # alt taps behave natively
        try:
            self.kb.type(self.hold_char)
        except Exception:
            log_exc("inject_tap")

    def begin_passive_hold(self):
        """Alt hold reached the threshold: take ownership of the key.
        Suppress its further events, then hand the system a clean
        'no modifier held' state - a mask key first (so no app reads a
        lone-Alt menu gesture), then a synthetic Alt-release (so injected
        text can't become Alt+letter accelerators while the physical key
        stays down)."""
        self.suppressing = True
        try:
            from pynput.keyboard import Key, KeyCode
            mask = KeyCode.from_vk(0xE8)     # unassigned VK, no app effect
            self.kb.press(mask)
            self.kb.release(mask)
            self.kb.release(Key.alt)
        except Exception:
            log_exc("begin_passive_hold")
        self.start_recording(time.monotonic())

    # --- recording lifecycle -------------------------------------------------
    def start_recording(self, now):
        self.cancel_requested = False
        self.partial = ""
        self.live_done = 0.0
        self.live_prev = []
        self.live_words = []
        self.live_window_words = []
        self.live_offset = 0.0
        self.live_miss = 0
        self.take_epoch = self.injector.new_take()
        self.chunks = list(self.ring)     # pre-roll: never clip a first word
        self.recording = True
        self.record_start = now
        self.state = RECORDING
        self.status = "listening"
        self.set_title("Carlson - RECORDING")
        self.chime("start")

    def finish_recording(self, note=None):
        self.recording = False
        self.suppressing = False
        self.cancel_requested = False
        audio, dur = self.snapshot()
        self.state = POLISHING
        self.status = note or f"transcribing {dur:.1f}s"
        self.set_title("Carlson - polishing")
        self.chime("done")
        threading.Thread(target=self.finalize, args=(audio, dur),
                         daemon=True).start()

    def cancel_recording(self):
        self.recording = False
        self.suppressing = False
        self.cancel_requested = False
        self.partial = ""
        self.last_final = ""
        self.injector.sync("", epoch=self.take_epoch, bump=True)
        self.set_flash("cancelled", 1.2)
        self.state = READY
        self.status = "cancelled (Esc) - injected words erased"
        self.set_title("Carlson - ready")
        self.chime("cancel")

    def finalize(self, audio, dur):
        ep = self.take_epoch
        try:
            if dur < 0.35:
                self.status = "too short - nothing heard"
                self.last_final = ""
                self.injector.sync("", epoch=ep, bump=True)
                self.set_flash("too short", 1.2)
                return
            t0 = time.monotonic()
            segs, _info = self.model_final.transcribe(
                audio, language=self.args.language, beam_size=5,
                vad_filter=True, initial_prompt=self.args.prompt,
                condition_on_previous_text=False)
            raw = " ".join(s.text.strip() for s in segs).strip()
            text = polish(raw, self.args.multiline)
            took = time.monotonic() - t0
            if not text or looks_hallucinated(text, dur):
                self.status = "heard nothing usable"
                self.last_final = ""
                self.injector.sync("", epoch=ep, bump=True)
                self.set_flash("heard nothing", 1.2)
                return
            # settle the live words into the final polished text, in place
            self.injector.settle(text, epoch=ep)
            try:
                import pyperclip
                pyperclip.copy(text)      # convenience copy, nothing more
            except Exception:
                pass
            self.history.appendleft((datetime.now().strftime("%H:%M:%S"),
                                     text, dur))
            self.status = (f"typed {len(text.split())} words "
                           f"({dur:.1f}s audio, {took:.1f}s polish)")
            self.last_final = text
            self.set_flash(f"{len(text.split())} words", 1.2)
        except Exception:
            log_exc("finalize")
            self.status = f"transcription failed - see {LOG_PATH}"
            self.injector.seal(ep)        # leave the live words in place
            self.chime("error")
            self.set_flash("failed - see log", 2.5)
        finally:
            self.partial = ""
            self.state = READY
            self.set_title("Carlson - ready")

    # --- live preview thread ---------------------------------------------------
    def live_worker(self):
        while not self.quit.is_set():
            try:
                if self.state == RECORDING and self.model_live is not None:
                    ep = self.take_epoch
                    audio, dur = self.snapshot()
                    off = self.live_offset
                    win = audio[int(off * SR):]
                    wdur = len(win) / SR
                    if wdur - self.live_done >= 0.4 and wdur >= 0.6:
                        # no initial_prompt here: it re-encodes on every
                        # pass and costs real latency; the final pass owns
                        # accuracy and keeps the vocabulary bias
                        segs, _ = self.model_live.transcribe(
                            win, language=self.args.language, beam_size=1,
                            vad_filter=True, condition_on_previous_text=False,
                            vad_parameters={"min_silence_duration_ms": 300})
                        words, seg_ends, cum = [], [], 0
                        for sg in segs:
                            sw = [w for w in sg.text.split()
                                  if not _is_filler(_norm_word(w))]
                            words += sw
                            cum += len(sw)
                            seg_ends.append((cum, sg.end))
                        if self.state == RECORDING and ep == self.take_epoch:
                            stable = stable_words(self.live_prev, words)
                            self.live_prev = words
                            # age-based commits: words whose audio ended
                            # more than 2.5s ago won't change - commit them
                            # without waiting for a second agreeing pass
                            aged = 0
                            for cum_w, end in seg_ends:
                                if end <= wdur - 2.5:
                                    aged = cum_w
                                else:
                                    break
                            stable = words[:max(len(stable), aged)]
                            done = self.live_window_words
                            n = len(done)
                            aligned = (len(stable) >= n
                                       and [_norm_word(w)
                                            for w in stable[:n]]
                                       == [_norm_word(w) for w in done])
                            self.live_miss = 0 if aligned \
                                else self.live_miss + 1
                            # After a window trim the model may re-word the
                            # boundary and alignment can fail forever - the
                            # old code froze live output here for the rest
                            # of the take. Now: two straight misses and we
                            # re-anchor to the model's view (count-aligned),
                            # so the stream NEVER stops; the settle repairs
                            # any imperfection.
                            if len(stable) > n and (aligned
                                                    or self.live_miss >= 2):
                                new = stable[n:]
                                chunk = " ".join(new)
                                if not self.live_words:
                                    chunk = chunk[:1].upper() + chunk[1:]
                                else:
                                    chunk = " " + chunk
                                if self.injector.append(chunk, epoch=ep):
                                    self.live_window_words = (
                                        done + new if aligned else stable)
                                    self.live_words = self.live_words + new
                                    self.live_miss = 0
                            elif self.live_miss >= 4 and stable:
                                # model sees fewer words than committed:
                                # adopt its view so growth can resume
                                self.live_window_words = stable
                                self.live_miss = 0
                            self.partial = " ".join(self.live_words)
                            self.live_done = wdur
                            # keep passes fast: drop leading audio whose
                            # words are fully committed, so the window (and
                            # therefore the lag) stays small no matter how
                            # long the hold goes on
                            if wdur > 10.0:
                                ncom = len(self.live_window_words)
                                cut_w, cut_t = 0, 0.0
                                for cw, end in seg_ends:
                                    if cw <= ncom and end <= wdur - 3.0:
                                        cut_w, cut_t = cw, end
                                    else:
                                        break
                                if cut_t > 0:
                                    self.live_offset = off + cut_t
                                    self.live_window_words = \
                                        self.live_window_words[cut_w:]
                                    self.live_prev = []
                                    self.live_done = wdur - cut_t
                            elif (not words and not self.live_window_words
                                    and wdur > 12.0):
                                # a long pause: the window is silence with
                                # nothing committed in it - keep only the
                                # recent tail so passes stay fast and the
                                # next phrase lands promptly
                                self.live_offset = off + (wdur - 6.0)
                                self.live_prev = []
                                self.live_done = 0.0
            except Exception:
                log_exc("live_worker")
                time.sleep(1.0)
            time.sleep(0.15)

    # --- niceties ----------------------------------------------------------------
    def set_flash(self, text, secs):
        self.flash_text = text
        self.flash_until = time.monotonic() + secs

    def chime(self, name):
        if self.args.no_sound:
            return
        try:
            import winsound
            for suffix in ("custom", "v1"):
                p = os.path.join(SOUND_DIR, f"{name}_{suffix}.wav")
                if os.path.exists(p):
                    winsound.PlaySound(
                        p, winsound.SND_FILENAME | winsound.SND_ASYNC
                        | winsound.SND_NODEFAULT)
                    break
        except Exception:
            pass

    def set_title(self, s):
        try:
            if self.console is not None:
                self.console.set_window_title(s)
        except Exception:
            pass

    # --- UI ------------------------------------------------------------------------
    def view(self):
        import numpy as np
        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        key = self.key_name
        header = Table.grid(expand=True)
        header.add_column(justify="left")
        header.add_column(justify="right")
        header.add_row(
            Text.assemble(("  CARLSON ", "bold magenta"),
                          (f"v{__version__}", "dim")),
            Text("local whisper - no cloud  ", "dim"),
        )

        if self.state == RECORDING:
            elapsed = time.monotonic() - self.record_start
            badge = Text(f" REC {elapsed:5.1f}s ", style="bold white on red")
            hint = f"  release {key} to paste - Esc cancels"
        elif self.state == POLISHING:
            badge = Text(" POLISHING ", style="black on yellow")
            hint = "  hang tight"
        else:
            badge = Text(" READY ", style="black on green")
            hint = f"  hold {key} for {self.hold_seconds:g}s, speak, release"
        left = Text("  ")
        left.append_text(badge)
        left.append(hint, "dim")
        status_row = Table.grid(expand=True)
        status_row.add_column(justify="left")
        status_row.add_column(justify="right")
        status_row.add_row(left, Text(f"{self.status}  ", "dim italic"))

        db = 20 * np.log10(max(self.level, 1e-9))
        frac = min(max((db + 60.0) / 60.0, 0.0), 1.0)
        width = 34
        filled = int(frac * width)
        color = "green" if frac < 0.62 else ("yellow" if frac < 0.85 else "red")
        meter = Text.assemble(
            ("  mic ", "dim"),
            ("#" * filled, color), ("-" * (width - filled), "grey35"),
            (f" {db:6.1f} dB", "dim"),
        )

        parts = [header, Text(), status_row, meter, Text()]
        if self.state == RECORDING:
            body = Text("  ")
            body.append(self.partial if self.partial else "(listening...)",
                        "italic cyan")
            body.append(" |", "bold cyan")
            parts.append(body)
        elif self.history:
            for when, text, dur in self.history:
                shown = text if len(text) <= 160 else text[:157] + "..."
                parts.append(Text.assemble(
                    (f"  {when} ", "dim"), (f"{dur:4.1f}s  ", "dim cyan"),
                    (shown, "default")))
        else:
            parts.append(Text(
                f"  Nothing dictated yet. Focus any window, hold {key}, "
                "talk, release.", "dim"))
        parts.append(Text())
        parts.append(Text(
            f"  tap {key} still types normally - transcript stays "
            "on clipboard - Ctrl+C quits", "dim"))
        return Panel(Group(*parts), border_style="magenta",
                     title="[bold]Carlson[/]",
                     subtitle="[dim]hold-to-dictate[/]")

    # --- startup / main loop ----------------------------------------------------
    def run(self):
        from rich.console import Console
        self.console = Console()
        c = self.console
        c.print(f"[bold magenta]Carlson[/] v{__version__} - hold-to-dictate, "
                "local whisper, no cloud")

        # Law L7: one instance only
        kernel32.CreateMutexW(None, False, "CarlsonDictationSingleton")
        if kernel32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS
            c.print("[red]Carlson is already running.[/] "
                    "Use carlson-stop first.")
            log("second instance blocked by singleton mutex")
            return 1

        import sounddevice as sd
        from faster_whisper import WhisperModel
        from pynput import keyboard as pk

        self.kb = pk.Controller()
        self.injector = Injector(self.kb)
        try:
            ensure_sounds()
        except Exception:
            log_exc("ensure_sounds")

        try:
            dev = sd.query_devices(kind="input")["name"]
        except Exception:
            c.print("[red]No microphone found.[/]")
            return 1

        errs = []
        threads = os.cpu_count() or 4

        def load(attr, name):
            try:
                setattr(self, attr,
                        WhisperModel(name, device="cpu", compute_type="int8",
                                     cpu_threads=threads))
            except Exception as e:
                errs.append(f"{name}: {e}")
                log_exc(f"load {name}")

        live_name, final_name = self.args.live_model, self.args.model
        if self.args.language != "en":
            live_name = live_name.removesuffix(".en")
            final_name = final_name.removesuffix(".en")
        with c.status(f"[magenta]warming up whisper ({live_name} "
                      f"+ {final_name}, {threads} threads), mic: {dev}..."):
            t1 = threading.Thread(target=load,
                                  args=("model_live", live_name))
            t2 = threading.Thread(target=load,
                                  args=("model_final", final_name))
            t1.start()
            t2.start()
            t1.join()
            t2.join()
        if self.model_final is None:
            c.print(f"[red]Could not load Whisper: {errs}[/]")
            return 1
        if self.model_live is None:
            c.print("[yellow]Live-preview model failed to load; "
                    "final transcription still works.[/]")

        stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32",
                                blocksize=BLOCK, callback=self._audio_cb)
        stream.start()

        self.listener = pk.Listener(win32_event_filter=self._filter)
        self.listener.start()
        threading.Thread(target=self.watch, daemon=True).start()
        threading.Thread(target=self.live_worker, daemon=True).start()
        if not self.args.no_overlay:
            Overlay(self).start()
        self.set_title("Carlson - ready")
        log(f"carlson v{__version__} started (key={self.key_name}, "
            f"mic={dev}, headless={self.args.headless})")

        try:
            if self.args.headless:
                while not self.quit.is_set():
                    time.sleep(0.5)
            else:
                from rich.live import Live
                with Live(get_renderable=self.view, console=c,
                          refresh_per_second=12):
                    while not self.quit.is_set():
                        time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            self.quit.set()
            self.suppressing = False
            self.recording = False
            try:
                self.listener.stop()
            except Exception:
                pass
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        c.print("[dim]Carlson stopped. Your keyboard is untouched.[/]")
        return 0


# ---------------------------------------------------------------------------
# Autostart: a per-user scheduled task that runs pythonw (no console) at
# logon. The overlay is the UI; the log is the black box (Law L4).
# ---------------------------------------------------------------------------
TASK_NAME = "Carlson"


def _powershell(script):
    import subprocess
    r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       capture_output=True, text=True)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return r.returncode, out


def autostart(mode):
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    me = os.path.abspath(__file__)
    # exclude our own pid AND our parent: the venv python.exe is a stub
    # whose real interpreter runs as its child in the same job - killing
    # the stub kills us mid-run.
    keep = {os.getpid(), os.getppid()}
    excl = "".join(f" -and $_.ProcessId -ne {p}" for p in keep)
    kill = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' or "
            "Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match "
            "'carlson'" + excl + " } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; "
            "Start-Sleep -Milliseconds 400; ")
    if mode == "on":
        script = (
            kill +
            "$u = $env:COMPUTERNAME + '\\' + $env:USERNAME; "
            "$t = New-ScheduledTaskTrigger -AtLogOn -User $u; "
            "$t.Delay = 'PT20S'; "
            "$a = New-ScheduledTaskAction -Execute '" + pyw + "' "
            "-Argument '-u \"" + me + "\" --headless'; "
            "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
            "-DontStopIfGoingOnBatteries -RestartCount 3 "
            "-RestartInterval (New-TimeSpan -Minutes 1); "
            "$s.ExecutionTimeLimit = 'PT0S'; "
            "Register-ScheduledTask -TaskName '" + TASK_NAME + "' "
            "-Trigger $t -Action $a -Settings $s -Force | Out-Null; "
            "Start-ScheduledTask -TaskName '" + TASK_NAME + "'; "
            "'Carlson autostart ON - runs at every logon (started now)'"
        )
    elif mode == "off":
        script = (
            kill +
            "Unregister-ScheduledTask -TaskName '" + TASK_NAME + "' "
            "-Confirm:$false -ErrorAction SilentlyContinue; "
            "'Carlson autostart OFF - stopped and removed from logon'"
        )
    else:
        script = (
            "$t = Get-ScheduledTask -TaskName '" + TASK_NAME + "' "
            "-ErrorAction SilentlyContinue; "
            "if ($t) { 'task: ' + $t.State } else { 'no autostart task' }; "
            "$p = Get-CimInstance Win32_Process -Filter "
            "\"Name='python.exe' or Name='pythonw.exe'\" | "
            "Where-Object { $_.CommandLine -match 'carlson' }; "
            "if ($p) { 'running: pid ' + ($p.ProcessId -join ', ') } "
            "else { 'not running' }"
        )
    code, out = _powershell(script)
    print(out if out else f"(powershell exit {code})")
    return 0 if code == 0 else 1


# ---------------------------------------------------------------------------
def selftest():
    cases = [
        ("um so basically fix the login bug",
         "So basically fix the login bug."),
        ("run the tests new line then commit", "Run the tests then commit."),
        ("open apps/api/.env and check DATABASE_URL",
         "Open apps/api/.env and check DATABASE_URL."),
        ("hello,world", "Hello, world"),
        ("  uh   git status  ", "Git status"),
        ("what does this stack trace mean?",
         "What does this stack trace mean?"),
    ]
    failed = 0
    for raw, want in cases:
        got = polish(raw)
        ok = got == want
        failed += (not ok)
        print(f"  [{'ok' if ok else 'FAIL'}] {raw!r} -> {got!r}"
              + ("" if ok else f"  (wanted {want!r})"))
    ml = polish("first line new line second line", multiline=True)
    ok = ml == "First line\nSecond line."
    failed += (not ok)
    print(f"  [{'ok' if ok else 'FAIL'}] multiline -> {ml!r}")
    for t, d, want in [("Thank you.", 0.8, True), ("Thank you.", 5.0, False),
                       ("", 3.0, True), ("Run the migration.", 0.9, False)]:
        got = looks_hallucinated(t, d)
        ok = got is want
        failed += (not ok)
        print(f"  [{'ok' if ok else 'FAIL'}] "
              f"hallucination({t!r}, {d}) -> {got}")
    print(f"  vk('5') = 0x{ord('5'):X}, injected flag = 0x{LLKHF_INJECTED:X}")
    print("SELFTEST " + ("FAILED" if failed else "PASSED"))
    return 1 if failed else 0


def parse_args():
    p = argparse.ArgumentParser(
        prog="carlson",
        description="Hold-to-dictate for the terminal. Local, open source, "
                    "no API keys.")
    p.add_argument("--hold-key", default="alt",
                   help="key to hold: 'alt', 'space', or a single "
                        "letter/digit (default: alt)")
    p.add_argument("--hold-seconds", type=float, default=1.0)
    p.add_argument("--model", default="base.en",
                   help="final-pass Whisper model (default: base.en; the "
                        ".en suffix is dropped for non-English languages)")
    p.add_argument("--live-model", default="tiny.en",
                   help="live-preview Whisper model (default: tiny.en)")
    p.add_argument("--language", default="en")
    p.add_argument("--prompt", default=DEFAULT_PROMPT,
                   help="vocabulary hint fed to Whisper")
    p.add_argument("--multiline", action="store_true",
                   help="keep spoken newlines (default: flatten for shells)")
    p.add_argument("--no-sound", action="store_true", help="disable beeps")
    p.add_argument("--max-hold", type=float, default=120.0)
    p.add_argument("--headless", action="store_true",
                   help="no terminal UI - the overlay is the UI "
                        "(what the autostart task uses)")
    p.add_argument("--no-overlay", action="store_true",
                   help="disable the on-screen overlay")
    p.add_argument("--autostart", choices=["on", "off", "status"],
                   help="manage the run-at-logon scheduled task")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--transcribe", metavar="WAV",
                   help="transcribe a wav file and print polished text")
    return p.parse_args()


def main():
    args = parse_args()
    if args.autostart:
        sys.exit(autostart(args.autostart))
    if _NO_CONSOLE:
        args.headless = True
    if args.selftest:
        sys.exit(selftest())
    if args.transcribe:
        from faster_whisper import WhisperModel
        audio = load_wav_as_float32(args.transcribe)
        m = WhisperModel(args.model, device="cpu", compute_type="int8")
        segs, _ = m.transcribe(audio, language=args.language, beam_size=5,
                               vad_filter=True, initial_prompt=args.prompt)
        raw = " ".join(s.text.strip() for s in segs).strip()
        print("RAW    :", raw)
        print("POLISH :", polish(raw, args.multiline))
        return
    hk = args.hold_key.strip().lower()
    if hk not in ("space", "alt") and (len(hk) != 1 or not hk.isalnum()):
        raise SystemExit(
            "--hold-key must be 'alt', 'space', or a single letter/digit")
    sys.exit(Carlson(args).run())


if __name__ == "__main__":
    main()
