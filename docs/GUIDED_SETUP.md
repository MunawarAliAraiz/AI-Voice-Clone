# Guided Runpod setup

## Confirmed design - 2026-10-02

The user requested a simple page-by-page setup with automatic defaults and
useful Advanced choices. They confirmed: download missing models automatically
after storage/setup spending is confirmed; keep downloads running when leaving
the setup page; pause and stop owned setup compute on app exit; resume on app
reopening; load/check GPU models on the first generation instead of purchasing
a separate GPU setup test.

## Pages

1. **Connect**: Runpod key, provider validation, clear input errors. A stored
   key is not proof that the account can currently be accessed.
2. **Storage**: default to the saved dedicated voice volume. Never silently
   choose a video/other application's volume. If absent, show the required size,
   recurring price and purchase approval. Advanced permits an explicit existing
   volume selection or creation with editable name, region and size.
3. **Models**: compulsory software/inspection uses 'Preparing your setup'. Show
   one named row and measured progress bar per model. Existing valid files are
   checked; only missing or invalid files download. Pause is explicit and short.
4. **Ready**: checks completed, spending defaults visible, advanced cost choices
   editable. GPU startup/loading belongs to first real generation.

Back/Next and step links change the page only and preserve input/progress.
Independent pages can be revisited; actions that depend on key/storage/files
remain disabled with a specific reason. Changing key/storage during compute
requires stopping that compute first. No form navigation starts a new rental.

## Capacity

Replace the unmeasured 200 GB floor. The currently pinned complete graph is
49,014,734,674 bytes. New-volume size is rounded up from required persistent
files plus a 10,000,000,000-byte reserve (currently 60 GB). Software baked into
container images uses their container disks and is not counted as persistent
model storage. Any actual persistent runtime writes must be covered too.

Before downloading, an authenticated CPU scan checks the complete model graph,
valid existing files, remaining writes and actual disk free space. Existing
volume metadata alone cannot establish free space or model validity. A name
match is only a candidate; local approved identity or a volume marker is needed
for automatic ownership selection. Explicit Advanced adoption is allowed.

The existing 200 GB volume stays intact. Volumes cannot shrink in place; no
automatic deletion or migration is permitted. Creation of a smaller separate
volume must show its extra recurring cost and does not imply old storage ends.

## Exit and restart

Native exit fences new work, pauses/drains local work and requests owned cloud
cleanup before stopping the API. Confirmed cleanup allows exit. Unconfirmed
cleanup retains the ownership record/deadline and a recoverable retry state.
Explicit user pause is different from app-exit pause: only app-exit pause keeps
the approved automatic-resume intent. Failure/ambiguous-start spending guards
must remain, including no blind repeat paid attempt.

## Verification boundaries

Wizard/component fixtures, backend capacity/ownership/exit tests and native
builds are implementation checks. Published immutable worker images must include
the new capacity route before desktop packaging. A real CPU/model download,
GPU inference, audio listening and installed exit/update cycle remain separate
qualification gates. See HANDOFF.md for current evidence.

Primary provider reference: https://docs.runpod.io/storage/network-volumes
