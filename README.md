<div align="center">

# Dictate

**Press. Speak. Done.**

On-device voice intelligence for Windows. Hold a key, talk naturally, and
watch finished prose land exactly where your cursor is — live, as you speak.

![Platform](https://img.shields.io/badge/platform-Windows%2011-0078d4)
![Version](https://img.shields.io/badge/release-1.8.0-blue)
![Processing](https://img.shields.io/badge/processing-100%25%20on--device-2ea44f)
![Telemetry](https://img.shields.io/badge/telemetry-none-2ea44f)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

</div>

---

Dictate is built around a simple promise: your voice becomes clean, correctly
punctuated text in the field you're already typing in — any terminal, editor,
or browser — with nothing sent to any server, ever. Speech is transcribed by
the **Carlson voice engine**, a dual-model on-device pipeline engineered for
one quality above all others: *calm*. Words appear as you say them and they
never flicker, jump, or rewrite themselves while you're mid-sentence.

## Experience

- **Instant capture.** Hold `Space` for one second — a soft chime and a
  floating waveform badge confirm Dictate is listening. Release, and a
  polished transcript settles into place. Cancel any take with `Esc`.
- **Live, stable insertion.** Text streams into your caret as you speak,
  strictly append-only. The engine commits a word only once consecutive
  recognition passes agree on it, so what you see is never taken back.
- **Steady at any length.** Committed audio is retired from the analysis
  window, keeping latency constant whether you speak for five seconds or
  five minutes — through pauses, pace changes, and mid-thought silences.
- **Editorial finish.** Hesitations ("um", "hmm") never reach the screen.
  Capitalization, spacing, and terminal punctuation are handled. Spoken
  commands — *new line*, *new paragraph* — are honored. Output never ends
  with a newline, so nothing is ever submitted on your behalf.
- **A considered surface.** The indicator is a single clean badge with a
  responsive waveform; it appears when you speak and disappears when you're
  done. Every transcript is also placed on the clipboard.
- **Private by architecture.** Audio is processed in memory, on your CPU,
  and discarded. No accounts, no API keys, no network dependency, no
  telemetry.

## Quick start

```powershell
git clone https://github.com/NoCapCarlson/dictate-tyler.git
cd dictate-tyler
.\install.ps1
```

The installer provisions an isolated Python environment, fetches the
recognition models (~225 MB, one time), adds the `carlson` and
`carlson-stop` commands, and registers Dictate to launch at sign-in. From
then on there is nothing to start and nothing to remember:

> **Hold `Space` · speak · release.**

**Requirements:** Windows 11 · Python 3.13 · a microphone ·
`%USERPROFILE%\.local\bin` on PATH.

## Everyday control

| Command | Effect |
|---|---|
| `carlson --autostart status` | Inspect the launch-at-sign-in service |
| `carlson --autostart off` / `on` | Disable / re-enable it |
| `carlson-stop` | Stop Dictate immediately |
| `carlson` | Open the live dashboard in a terminal |

Configuration — trigger key, hold time, models, language, vocabulary
biasing, multiline mode, sounds — is covered in
[docs/configuration.md](docs/configuration.md).

## Documentation

- [Architecture](docs/architecture.md) — the dual-model pipeline, the
  stability model, and the input-integrity guarantees
- [Configuration](docs/configuration.md) — every flag, with defaults
- [Troubleshooting](docs/troubleshooting.md) — diagnostics and recovery
- [Changelog](CHANGELOG.md) — release history
- [Security & privacy](SECURITY.md)

## License

Released under the [MIT License](LICENSE).
