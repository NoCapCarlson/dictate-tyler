# Configuration

All configuration is via command-line flags on the `carlson` command. The
launch-at-sign-in service uses the defaults; to run with custom flags,
disable it (`carlson --autostart off`) and run `carlson` with your flags,
or edit the scheduled task's arguments.

| Flag | Default | Description |
|---|---|---|
| `--hold-key` | `alt` | Trigger key: `alt`, `space`, or a single letter/digit |
| `--hold-seconds` | `1.0` | Hold duration before capture engages |
| `--model` | `small.en` | Finishing model (`base.en` = faster, less accurate) |
| `--live-model` | `tiny.en` | Streaming model for live insertion |
| `--language` | `en` | Recognition language |
| `--prompt` | developer vocabulary | Domain bias fed to the recognizer |
| `--multiline` | off | Preserve spoken newlines (default flattens for shell safety) |
| `--no-sound` | off | Disable audio cues |
| `--no-overlay` | off | Disable the on-screen indicator |
| `--max-hold` | `120` | Maximum single take, seconds |
| `--headless` | off | No terminal dashboard (used by the service) |
| `--autostart on\|off\|status` | — | Manage the sign-in service |
| `--selftest` | — | Run the built-in test suite |
| `--transcribe FILE.wav` | — | Transcribe a file (pipeline check, no mic) |

## Sounds

Audio cues live in `%LOCALAPPDATA%\carlson-sounds`. The engine synthesizes
a default set on first run. To replace any cue, place a 16-bit WAV named
`start_custom.wav`, `done_custom.wav`, `cancel_custom.wav`, or
`error_custom.wav` in the repository's `assets/` directory — custom files
always take precedence.

## Personal vocabulary

`~/.config/carlson/vocabulary.txt` holds one name or term per line
(created with starter entries on first run). Every entry is fed to the
finishing pass so the recognizer spells your projects, tools, and people
correctly. Lines starting with `#` are ignored; changes apply on restart.

## Spoken commands

| Say | Result |
|---|---|
| "new line" | line break (space in default flatten mode) |
| "new paragraph" | blank line (space in default flatten mode) |

## Notes

- The transcript never ends with a newline; nothing is auto-submitted.
- With the default `alt` trigger, taps and shortcuts (Alt+Tab, Alt+F4)
  behave natively; only a solitary one-second hold starts dictation. With a
  character trigger (`space`, letters), typed taps are re-injected on key
  release (~80 ms).
- Elevated (administrator) windows cannot receive synthetic input from an
  unelevated process; this is Windows policy.
