# FFmpeg source and redistribution checkpoint

## Current delivery: publisher download — 2026-10-02

The new installer does **not** contain FFmpeg's executable, license or README.
The desktop downloads the runtime directly from its publisher on first launch,
using the immutable vendor release URL below. It installs the executable and
publisher notices under the user's app-data `tools/ffmpeg/` directory; the native
shell places that directory on the local API's PATH. No model weights or local
voice assets are involved in this download.

```text
https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip
Archive: 114,768,076 bytes
SHA256: 60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba
```

`backend/app/audio_tools.py` verifies both the complete archive and each selected
file (exact size/SHA256). It extracts only `bin/ffmpeg.exe`, `LICENSE` and
`README.txt`, ignores all other entries, rejects symlink targets, stages files
atomically and commits notices before the executable. A directory's existence
never proves readiness. Each launch checks all three cached files before adoption.
Downloads can resume with validated byte ranges, or restart safely when the server
ignores a range. Real byte progress is available through authenticated
`GET /api/audio-tools/status`; `POST /api/audio-tools/start` retries setup.
Connection failures automatically retry twice. Individual reads time out after
30 seconds and each download attempt has a ten-minute deadline. Shutdown cancels
network work and waits for any active hash/extraction writer to exit.

Automatic download is enabled only by the desktop entry point; the setting
`desktop_audio_tools_autostart` defaults false for web deployments and tests.
The global `AudioToolsSetup` component shows preparation/progress and a retry
action on failure. Desktop generation, voice enrollment and editing reject
requests until the controller reports verified readiness. History, script
preparation and public caption import remain available.

`scripts/build-desktop.ps1` no longer accepts FFmpeg paths or copies its binary
into the package. Its build receipt records first-run publisher delivery. Tauri's
external binary/resources list must likewise omit FFmpeg. The existing Pod image's
Debian FFmpeg delivery is a separate deployment and was not changed by this work.

Verification: 11 focused download/admission tests cover checksum refusal, exact
extraction, range resume/reset, redirect refusal, network retries, cached
adoption, cancellation, concurrent start protection, timeout failure and status
contracts. A real first-run publisher download fetched all 114,768,076 bytes,
verified all hashes and reached ready in 88.39 seconds on this development
machine. The downloaded executable reported FFmpeg 9.0.2 and successfully applied
an `atempo` filter to a synthetic runtime smoke clip. That clip is not evidence
of AI voice generation or quality. Frontend production/CSS checks and build-script
syntax checks passed.

This removes the previously bundled FFmpeg executable from our installer's
redistribution contents. It introduces an approximately 115 MB first-run download
and requires internet access for initial setup. The historical partial source
evidence below remains useful if a future release bundles that vendor binary
again; it is not represented as a complete corresponding-source package.

## Earlier bundled runtime audit — 2026-10-01

The desktop launches FFmpeg as a separate executable. It bundles Gyan's
`9.0.2-essentials_build-www.gyan.dev`, built with MSYS2 GCC 16.2.0 Rev3.
Its actual configuration includes `--enable-gpl --enable-version3 --enable-static`
and does not include `--enable-nonfree`. The binary hash is:

```text
3256173f3f8bffd7df12227c68adf68025edb1832273a9530688a7bb1ed8edec
```

The downloaded original vendor ZIP was checked against the public release asset
digest: `60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba`.
The published vendor release has six binary assets; its automatically generated
GitHub source downloads are for the support repository, not the complete
FFmpeg/library source graph. [Vendor release](https://github.com/GyanD/codexffmpeg/releases/tag/9.0.2).

## Concrete local source evidence

`scripts/prepare-ffmpeg-source.py` captures the actual binary's version/configuration,
license, README, hash and 37 declared library versions. It downloads FFmpeg core
at the vendor's exact resolved commit:

```text
Revision: 946fcce07b6dcd0331c8cc609192aeff5e1924f8
Archive bytes: 17,333,895
Archive SHA256: 0aa2b1de2a5698b20a23e93d539a9a8e82ca0117496c5bdf05d198805f42bb3b
```

The archive and `source-receipt.json` are in the ignored local build directory
`build/desktop/ffmpeg-source/`. The archive is fetched directly from the
[exact FFmpeg commit](https://github.com/FFmpeg/FFmpeg/commit/946fcce07b6dcd0331c8cc609192aeff5e1924f8),
and its local hash is recorded rather than presented as an upstream signature.
It is **partial evidence**. The receipt deliberately sets
`redistribution_ready: false`; neither a successful download nor a license file
marks this dependency graph complete.

```powershell
python scripts/prepare-ffmpeg-source.py `
  --ffmpeg frontend/src-tauri/binaries/ffmpeg-x86_64-pc-windows-msvc.exe `
  --build-info frontend/src-tauri/binaries/ffmpeg-build-info.txt `
  --license frontend/src-tauri/binaries/ffmpeg-license.txt `
  --output build/desktop/ffmpeg-source --download
```

Omit `--download` when refreshing the binary/license receipt around an already
downloaded archive. The collector rejects a different vendor version or a
nonfree build; it does not modify the installer or publish files.

## What was missing to redistribute this exact binary ourselves

- Vendor build scripts and patches, or confirmation that the pinned FFmpeg core
  is unmodified. The public README contains configuration and versions, not the
  scripts used to build the executable.
- Exact source, patches, license notices and build recipes for each statically
  linked library, including transitive dependencies.
- Versions and source recipes for configured packages omitted from the README's
  version table: fontconfig, iconv, GnuTLS, libxml2, GMP, bzip2, xz/liblzma and zlib.
  A current MSYS2 package cannot establish the version used in this binary.
- Confirmation that the collected package corresponds to the bundled binary,
  plus an actual source delivery arrangement alongside the app download or
  installer. No source offer or source host has been published.

FFmpeg's own [redistribution guidance](https://ffmpeg.org/legal.html) requires
exact matching sources/build information and consideration of external
libraries. GPLv3 [corresponding source requirements](https://www.gnu.org/licenses/gpl-3.0.html#section1)
include the scripts used to control compilation and installation. Linking the
vendor's core repository alone does not supply this executable's complete source.

## Earlier options considered for binary delivery

1. Preserve the current runtime and obtain the vendor's full corresponding source
   package, then mirror it beside our approved download. This preserves runtime
   behavior and avoids retesting an alternate multimedia build. The source
   request requires authorized communication; none has been sent.
2. Build a reproducible audio-focused FFmpeg from pinned sources, archive every
   dependency, patch and recipe during the build, and run all audio extraction,
   editor, playback-format and tempo tests against it. Most current editor
   operations use built-in FFmpeg filters, but supported input/output formats
   must be preserved. A different generic GPL/LGPL vendor ZIP alone does not
   solve the matching-source problem.
3. Make first-run setup download a checksum-pinned runtime directly from its
   vendor instead of bundling it in our installer. This adds a required local
   download and needs setup progress/error handling before media operations can
   run. It is a product/packaging change and has not been implemented.

Earlier development installers containing FFmpeg retain that source-delivery
gap. The new first-run download does not retroactively qualify those artifacts.
