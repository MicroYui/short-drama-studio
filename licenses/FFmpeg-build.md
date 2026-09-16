# Bundled FFmpeg build and source status

Inspected on 2026-09-16. This record describes the current macOS arm64 executable,
not every platform supported by imageio-ffmpeg. It identifies known distribution
provenance and a remaining release requirement; it is not a source offer.

## Executable and license

- Installed filename: `ffmpeg-macos-aarch64-v7.1`
- Version: FFmpeg 7.1
- Copyright (c) 2000–2024 the FFmpeg developers
- Reported executable license: GNU General Public License, version 2 or later
- License copy: [FFmpeg-GPL-2.0.txt](FFmpeg-GPL-2.0.txt)
- Application resource path:
  `python/lib/python3.13/site-packages/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1`

The Python wrapper `imageio-ffmpeg` is separately licensed under BSD-2-Clause.
Preserve its license at
`python/lib/python3.13/site-packages/imageio_ffmpeg-0.6.0.dist-info/LICENSE`.
The wrapper's BSD license is not the license of this FFmpeg executable.

## Distribution provenance

`backend/uv.lock` identifies the imageio-ffmpeg 0.6.0 macOS arm64 wheel:

`imageio_ffmpeg-0.6.0-py3-none-macosx_11_0_arm64.whl`

Its wheel SHA-256 in that lockfile is
`b1ae3173414b5fc5f538a726c4e48ea97edc0d2cdc11f103afee655c463fa742`.
This is the dependency lock entry; the wheel was not downloaded again during
the 2026-09-16 inspection.

The upstream imageio-ffmpeg v0.6.0
[_definitions.py](https://github.com/imageio/imageio-ffmpeg/blob/v0.6.0/imageio_ffmpeg/_definitions.py)
maps macOS aarch64 to the above FFmpeg filename. Its
[tasks.py](https://github.com/imageio/imageio-ffmpeg/blob/v0.6.0/tasks.py)
copies binaries from a sibling `imageio-binaries/ffmpeg` checkout or downloads
them from `imageio/imageio-binaries/ffmpeg`. These facts identify the distribution
chain, not a verified complete corresponding-source archive for this build.

## Observed build

The executable's `-L` output reports GPL version 2 or later and no warranty.
Its build information is:

```text
ffmpeg version 7.1 Copyright (c) 2000-2024 the FFmpeg developers
built with Apple clang version 13.1.6 (clang-1316.0.21.2.5)
configuration: --prefix=/Volumes/tempdisk/sw --extra-cflags=-fno-stack-check --arch=arm64 --cc=/usr/bin/clang --enable-gpl --enable-libvmaf --enable-libopenjpeg --enable-libopus --enable-libmp3lame --enable-libx264 --enable-libx265 --enable-libvpx --enable-libwebp --enable-libass --enable-libfreetype --enable-fontconfig --enable-libtheora --enable-libvorbis --enable-libsnappy --enable-libaom --enable-libvidstab --enable-libzimg --enable-libsvtav1 --enable-libharfbuzz --enable-libkvazaar --pkg-config-flags=--static --enable-ffplay --enable-postproc --enable-neon --enable-runtime-cpudetect --disable-indev=qtkit --disable-indev=x11grab_xcb
libavutil      59. 39.100 / 59. 39.100
libavcodec     61. 19.100 / 61. 19.100
libavformat    61.  7.100 / 61.  7.100
libavdevice    61.  3.100 / 61.  3.100
libavfilter    10.  4.100 / 10.  4.100
libswscale      8.  3.100 /  8.  3.100
libswresample   5.  3.100 /  5.  3.100
libpostproc    58.  3.100 / 58.  3.100
```

These build-time paths come from the upstream executable; they are not paths to
source archives supplied with Studio. Other architectures require their own
binary/license inspection before distribution.

## Remaining public-distribution requirement

Complete corresponding source delivery for this exact binary has not yet been
arranged. The distributor must obtain and supply the required corresponding
source, including the relevant library sources and build/install scripts, or
establish another applicable GPL-compliant source arrangement before public
distribution. If that source cannot be established for this build, select a
build with a verified matching source package and rerun media export checks.

The license text and generic upstream links do not complete that requirement.
No unfulfilled source offer, source availability promise or substitute generic
source archive is asserted here. This internal candidate remains pending that
work. See [FFmpeg legal information](https://ffmpeg.org/legal.html) and
[FFmpeg 7.1's GPLv2 text, section 3](https://github.com/FFmpeg/FFmpeg/blob/n7.1/COPYING.GPLv2).
