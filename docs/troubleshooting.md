# Troubleshooting

## First stops

- **Log:** `%LOCALAPPDATA%\carlson.log` — unbuffered; every error lands
  here with a full trace.
- **Status:** `carlson --autostart status` — service state and process IDs.
- **Reset:** `carlson-stop`, then `carlson --autostart on`.

## Symptoms

**Nothing happens when I hold the trigger key.**
Check `carlson --autostart status`. If no process is running, inspect the
log's tail and start the service. If a process is running but capture does
not engage, confirm a microphone is present and not exclusively claimed by
another application.

**The indicator stays on screen with no dictation in progress.**
`carlson-stop` clears it instantly; check the log for the take that failed
to close and re-enable the service.

**Text goes to the wrong place.**
Insertion follows the system caret: whatever window has focus receives the
words. Keep focus in your target field while holding the key.

**No text lands in an elevated window.**
Windows blocks synthetic input into administrator windows from unelevated
processes. Run your target unelevated, or accept transcription via the
clipboard (every take is copied there).

**I plugged in a headset / changed microphones.**
Nothing to do - the audio stream watches its own heartbeat and re-opens
on the current default device within a few seconds. The dashboard status
shows "microphone recovered" when it does.

**Recognition quality dips on jargon.**
Add your terms to `~/.config/carlson/vocabulary.txt` (picked up on
restart), or bias a single run with `--prompt`.

**The service didn't start after sign-in.**
It starts 20 s after logon and retries three times a minute apart. Check
Task Scheduler for task `Carlson`, and the log for a startup trace.

## Full reinstall

```powershell
carlson --autostart off
Remove-Item -Recurse -Force .\venv
.\install.ps1
```

Models are cached per-user and are not re-downloaded.
