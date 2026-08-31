# Architecture

Dictate is a single-process engine (`carlson.py`) composed of five
subsystems around a lock-free shared state.

```
┌────────────┐  16 kHz ring buffer   ┌──────────────┐
│ sounddevice │ ───(0.8s pre-roll)──▶│ take buffer  │
└────────────┘                       └──────┬───────┘
                                            │ analysis window
┌────────────────────┐               ┌──────▼───────┐   agreement   ┌──────────┐
│ low-level keyboard │               │ streaming    │──(2 passes)──▶│ Injector │──▶ caret
│ hook (pynput)      │               │ model (tiny) │    commit     └────▲─────┘
└─────────┬──────────┘               └──────────────┘                    │ settle
          │ 15 ms watchdog           ┌──────────────┐                    │
          ▼                          │ finishing    │────── polish ──────┘
   overlay indicator                 │ model (base) │   (on release)
   (topmost, click-through)          └──────────────┘
```

## Capture

A persistent 16 kHz mono input stream feeds a small ring buffer at all
times. When a take begins, the ring's last 0.8 s is prepended, so the first
syllable is never clipped. RMS level drives the indicator's waveform.

## Trigger

A low-level keyboard hook implements hold-to-talk on the trigger key
(default `Space`). The key is suppressed from first press; a quick tap is
re-injected on release so normal typing is unaffected.

**The key-state invariant:** an event swallowed by a low-level hook never
updates the system's asynchronous key-state table — in either direction.
While suppressing, the hook's own event stream is therefore the *only*
authority on the physical key; the async table is consulted solely for
modifier keys, which are never suppressed. A 15 ms watchdog thread drives
every state transition, is exception-proof, releases suppression whenever
the key is not physically held, and enforces a hard cap on take length.
`Esc` during a take is captured (not forwarded) and cancels it, erasing
everything inserted.

## Streaming recognition

While recording, the streaming model re-reads the analysis window and
emits a hypothesis roughly twice per second. Two stabilization policies
turn those volatile hypotheses into calm output:

1. **Consecutive-pass agreement** — a word is committed only when two
   successive hypotheses agree on it (compared case- and
   punctuation-insensitively). Committed text is strictly append-only.
2. **Window retirement** — audio whose words are committed is dropped from
   the window (with a three-second guard band), so pass time and lag stay
   constant regardless of take length. Silence-dominated windows are
   trimmed independently, so long pauses cost nothing. If retirement lands
   on an awkward phrase boundary and alignment fails repeatedly, the
   committer re-anchors to the recognizer's current view rather than
   stalling — the stream never stops mid-take.

Hesitation tokens are filtered before commitment and never appear on
screen. Vocabulary can be biased toward a domain via a prompt.

## Insertion

The `Injector` types committed words at the active caret via synthetic
input, serialized under one lock, with an epoch counter that makes a stale
live commit impossible after a take ends. On release, the finishing model
transcribes the complete take (beam search, voice-activity filtering) and
the transcript is polished — hesitations removed, capitalization and
punctuation spacing corrected, spoken layout commands applied, newlines
flattened for shell safety, no trailing newline. The settle is a
word-level merge: leading words the live pass already matched are left
untouched; only the true tail is rewritten. The finished transcript is
also placed on the clipboard.

## Indicator

A transparent, topmost, click-through overlay renders the badge: a ring
with a five-bar waveform following smoothed microphone level, a spinner
during finishing, and a brief result mark. It cannot take focus or
intercept clicks.

## Service

A per-user scheduled task launches the engine headless at sign-in (20 s
delay, three automatic restarts on failure, no run-time limit, permitted
on battery). A named mutex guarantees a single instance. All errors append
to `%LOCALAPPDATA%\carlson.log` unbuffered.
