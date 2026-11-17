# Performance notes

## Changes

- Folder scans reuse directory-entry metadata and song lookups per directory.
  Each load still reads current file information.
- Compressed keysounds are decoded in batches of 24. Decoded PCM is reused
  across charts and sessions, with a 1 GiB / 12-hour cache limit.
- Single-clip Layers reuse their rendered image. Video encoding and rendering
  overlap with at most two pending frames.
- Image previews reuse decoded pixels, avoiding an intermediate PNG round trip.
  Track lookup and texture-cache accounting no longer scan the entire package.
- macOS package rows load lazily and image decoding runs in the background.
  Superseded selections are discarded before display.

## Measurements

Local Apple M4 comparisons using the same inputs and settings:

| Operation | Before | After |
|---|---:|---:|
| Song labels for 11,692 files, warm filesystem, median | 0.101 s | 0.066 s |
| ICARUS full audio, separate empty PCM caches | 2.932 s | 2.648 s |
| 40 image previews from a 1,941-material package | 0.225 s | 0.098 s |
| Todestrieb BGA + Layer, 4 s, 720p/60 FPS, under profiling | 1.431 s | 1.109 s |
| entelecheia BGA + Layer, same settings | 2.388 s | 2.208 s |

The complete WAV hashes and all decoded image / video-frame RGBA bytes match.
Resolution, frame rate and encoding settings were unchanged. Label timings
exclude scanning and list drawing; image timings cover the parsing core only.
Video probes were warmed. These samples do not predict whole-song timings
or performance on other disks and systems.

## Validation

The macOS regression run covered 184 tests; the nine Live2D fixture checks
were run separately against the local SDVX models. Windows passed 181 of 184
core tests; three private-fixture checks were skipped. Both x64 and ARM64
single EXEs passed first and cached launches, including native preview/export
and GPU checks. The embedded macOS core and native interface were also verified.

VANESSA alignment is checked against matching BGA pixels from GOLD, EMPRESS
and Resort Anthem. Isolated previews retain their authored coordinates.

Parameterized benchmarks are in `tools/benchmark_*.py`; regression coverage
is in `tests/`. Game fixtures are not distributed.
