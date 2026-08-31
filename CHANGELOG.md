# Changelog

All notable changes to Dictate are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and versions follow
semantic versioning.

## [1.8.0] — 2026-08-31
### Fixed
- Live insertion could stall partway through longer takes when the
  analysis window was retired at a phrase boundary; the engine now
  re-anchors to the recognizer's current alignment and the stream
  continues uninterrupted from first word to last.

## [1.7.0] — 2026-08-31
### Changed
- Final refinement is now a word-level merge: text the live pass already
  got right is left untouched on screen, and only the true tail is
  rewritten. Ends the visible clear-and-retype at the end of a take.
- Hesitation filtering now covers elongated forms ("ummm", "hmmm").
### Added
- Long-pause endurance: silence-dominated analysis windows are trimmed so
  multi-second pauses no longer delay the next phrase.

## [1.6.0] — 2026-08-31
### Changed
- Constant-latency streaming: audio whose words are already committed is
  retired from the analysis window, keeping recognition passes fast for
  arbitrarily long takes. First words land roughly two seconds in.
### Added
- Signature capture sound (`assets/start_custom.wav`); any
  `assets/<name>_custom.wav` overrides the built-in sound set.

## [1.5.0] — 2026-08-31
### Changed
- Live insertion made strictly append-only via consecutive-pass agreement;
  on-screen text no longer revises itself while speaking.
- Default trigger moved from `5` to `Space`.

## [1.4.0] — 2026-08-31
### Changed
- Indicator redesigned: ring badge with a five-bar responsive waveform.
- Synthesized chime set replaces system beeps.

## [1.3.0] — 2026-08-31
### Added
- Live caret insertion: words are typed where you're working as you speak,
  then settled to the final transcript in place.
### Changed
- Overlay reduced to the indicator alone; transcript display moved
  entirely into the destination field.

## [1.2.0] — 2026-08-31
### Fixed
- Key-state tracking moved fully into the input hook, eliminating two
  classes of stuck-key failures on suppressed events.

## [1.1.0] — 2026-08-31
### Added
- Launch-at-sign-in service with automatic recovery.
- On-screen indicator overlay.

## [1.0.0] — 2026-08-30
### Added
- Initial release: hold-to-dictate capture, dual-model on-device
  transcription, grammar polish, terminal-safe output, single-instance
  guard, self-test suite.
