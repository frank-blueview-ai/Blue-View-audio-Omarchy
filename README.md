# Blue-View-audio-Omarchy

Blue View Audio is a branded Omarchy / Quickshell audio panel for speaker routing, microphone control, camera power, live signal levels, and a detachable visual equalizer.

Brand: [blueview.ai](https://blueview.ai) · [Blue View OS](https://bvos.blueview.ai)

It extends Omarchy's existing audio panel. The panel keeps output, microphone, balance, fade, and application-volume sliders above device selection and routing. The detached EQ keeps its bands above the visualizer. The panel shows the Blue View double-oval brand, exposes the active input and output meters, and keeps six EQ bands independently adjustable for the logical front and rear channel pairs. The EQ has bar-meter and multi-wave views and opens as a floating, pinned window so it can stay visible on another workspace.

## Audio features

- Expand **Four-channel routing** to route each of four output channels to a selected source channel; collapse it to save panel space.
- Balance left/right and fade front/rear with attenuation-only sliders, plus a center reset. These controls remain active when EQ is bypassed and preserve individual channel mute levels.
- Mute each output channel independently while keeping the master output level intact.
- Copy mono and stereo playback across four output lanes while preserving the selected channel map.
- Select audio output and input devices, and control their mute/volume levels.
- Keep the iMac's built-in four-channel PCM endpoint on the Blue View remap path. The raw hardware master is represented once as **Blue View Built-in Speakers**, since that remap owns the hardware ports for routing and EQ.
- Toggle the iMac camera through its USB authorization switch.
- Adjust six parametric bands separately for the front and rear speaker pairs. EQ starts bypassed and flat.
- Turn on Open Quad Spatial to blend incoming rear channels with a phase-shaped fill derived from the front pair. This uses PipeWire's built-in filter-chain nodes and does not decode Atmos objects or height channels.
- Open Dolby's official product page for purchase and setup information.

The Dolby button links to [Dolby Atmos for Headphones](https://www.dolby.com/experience/headphones/). Dolby consumer headphone purchase and activation are handled by Dolby Access on supported Windows/Xbox systems. This Linux applet cannot import or activate a Dolby license. Dolby Atmos Renderer is a separate production product for supported desktop/DAW workflows, not a system-wide PipeWire license.

## Sound quality

On the validated iMac, the active four-channel ALSA profile exposes S32_LE/S16_LE at 44.1–48 kHz. The running output uses S32_LE, four channels, 48 kHz: the maximum rate and word size exposed by that profile. Codec-wide advertisements of 192 kHz do not establish support for this four-channel speaker path, and a 32-bit transport does not imply 32 bits of analog resolution.

The included client/PulseAudio settings select PipeWire's maximum resampler quality, `resample.quality = 14`, for rate conversion, including 44.1 kHz material played through the 48 kHz graph. This preserves the negotiated hardware rate and channel routing. It costs more CPU than the default quality 4 and cannot restore information missing from a recording or streaming source. See [PipeWire's resampler documentation](https://docs.pipewire.org/page_man_pipewire-props_7.html). New native/ALSA clients load it when started; the PulseAudio compatibility server needs a restart while playback is idle.

EQ boosts can require additional headroom; these controls are not a mastering limiter or a guarantee against clipping at every volume/EQ combination. Start with EQ bypassed for a neutral response and lower output level when applying large boosts.

## Requirements

- Omarchy with Quickshell and its existing `omarchy.audio` panel APIs.
- PipeWire, WirePlumber, `pactl`, and a four-channel output profile.
- Python 3 and systemd user services.
- For the included camera power switch, a camera exposing a writable USB `authorized` sysfs attribute and `pkexec`.
- Qt Multimedia (`qt6-multimedia` on Arch) for the detachable camera monitor. Preview starts only while its window is open and the camera is enabled; pausing or closing the preview releases capture. No recording is created.

The iMac14,4 used for validation is specified with stereo built-in speakers. Its Linux audio card exposes four PCM output lanes, but those measurements do not prove four independent spatial speakers; the user confirmed all four cues alternating left/right, and a microphone probe detected every lane. See the [hardware topology caveat](docs/e2e-audio-2026-09-13.md#hardware-topology-caveat) before treating those lanes as front/rear speaker positions.

The included defaults match the four-channel iMac route this panel was built and tested on. For other hardware, edit `~/.config/blueview-audio/config.json` after installation:

```json
{
  "masterSink": "alsa_output.pci-0000_00_1b.0.analog-surround-40",
  "masterChannelMap": ["front-left", "front-right", "rear-left", "rear-right"],
  "cameraAuthorizationPath": "/sys/bus/usb/devices/1-5/authorized"
}
```

`masterSink` must name the four-channel PipeWire node behind the applet's `imac_fixed` remap. `masterChannelMap` describes that node's channel order. `cameraAuthorizationPath` must point to the camera device's `authorized` attribute; camera power changes prompt for administrator authorization.

Speaker routing, balance/fade, EQ, and mute preferences are stored in `~/.local/state/blueview-audio/device-state.json`, outside the watched plugin directory so saving a control does not reload the panel.

## Install

Run from this repository on the Omarchy desktop:

```sh
./scripts/install.sh
```

The installer places the panel, control helper, stereo-upmix settings for PulseAudio and native PipeWire/ALSA clients, JACK's default system-port mapping, and optional spatial filter files in the user's config directories. It adds `fperez.audio` to the right side of `~/.config/omarchy/shell.json`. It upgrades an unchanged older distributed spatial graph, keeps a backup, and preserves custom graphs. It does not enable the spatial service or turn on EQ. Reload the Omarchy shell or sign out and back in to load the panel. Restart PipeWire PulseAudio while playback is idle to apply its server-side setting; native PipeWire, ALSA, and JACK apps use client settings when next launched.

To enable spatial processing, switch on **Open Quad Spatial** in the panel. The helper only permits it while `imac_fixed` is the selected output, and turns the service off again when the switch is disabled. The EQ remains bypassed until its toggle is enabled.

## Verification

Run the helper's unit tests with `python3 -m unittest discover -s tests -v`. On the configured iMac, run `python3 tests/e2e_speaker_channels.py --run` for the opt-in four-lane check, `python3 tests/e2e_speaker_mutes.py --run` to verify each individual mute and restore path, `python3 tests/e2e_stereo_upmix.py --run` to check mono and stereo through PulseAudio, native PipeWire, and default ALSA, and `python3 tests/e2e_mpv_playback.py --run` for mono/stereo playback in MPV through its PipeWire, PulseAudio, and ALSA outputs. Run `python3 tests/e2e_jack_multichannel.py --run` for four-channel JACK port routing, `python3 tests/e2e_eq_multichannel.py --run` for a continuous Pulse stream EQ response check, and `python3 tests/e2e_jack_eq.py --run` for a live JACK EQ response check. Each records the explicitly selected ALSA monitor and refuses to run while an application is playing through the Blue View sink or any speaker channel is muted. The mute check briefly plays a quiet four-channel tone, verifies the selected lane goes silent while the other three stay live, then confirms all saved levels, routes, EQ values, and defaults are restored. The EQ checks briefly apply a quiet test curve, verify the same playback node remains attached through live graph updates, then restore the prior active curve or bypass state without editing saved sliders. These checks verify PipeWire-to-ALSA routing, not acoustic output from the speaker drivers.

JACK is a port graph rather than a normal desktop output stream. The installed client setting exposes the selected default as `system:playback_N`; JACK apps or a patchbay must connect their ports there. Four-channel JACK playback follows Blue View's channel map, but JACK's direct mono-port links do not receive the stereo upmix used by PulseAudio, native PipeWire, and ALSA streams. See [PipeWire's JACK client model](https://pipewire.pages.freedesktop.org/pipewire/page_objects_design.html) and [JACK configuration](https://pipewire.pages.freedesktop.org/pipewire/page_man_pipewire-jack_conf_5.html).

Run `python3 tests/e2e_speaker_position.py --run` for the isolated balance/fade DSP check. It measures attenuation and center restoration while keeping the same player alive.

See [the dated E2E record](docs/e2e-audio-2026-09-13.md) for current results and remaining checks.

`python3 tests/e2e_acoustic_probe.py --run --temporarily-mute-playback` compares quiet per-channel tones through Blue View and directly through the hardware sink with the selected mic. It checks capture source/format, uses 20 ms capture latency, measures tone-frequency energy against silence and neighboring frequency bins, and restores application mute states without stopping their playback. Raw mic samples stay in memory. Exit code `2` means acoustic evidence is inconclusive; moving digital meters do not pass the acoustic gate. `--peak-dbfs=-24` is the maximum permitted test level.

## Speaker response survey

`python3 tests/e2e_frequency_response.py --run --temporarily-mute-playback` plays quiet −30 dBFS per-channel tones from 40 Hz to 16 kHz, checks the electrical output and physical microphone, and reports frequency response measurements. It requires neutral balance/fade, EQ bypass, and spatial off. It restores application mutes and never edits EQ settings or saves microphone audio.

See the [measured response survey](docs/speaker-response-2026-09-13.md). The built-in microphone is uncalibrated. Results combine the speaker, room, placement, and microphone response; weak bass/treble pickup cannot establish a driver's safe operating limits. The survey is a starting point for tuning, not automatic crossover calibration. Driver-specific limits need identified hardware or calibrated acoustic measurements, especially before applying bass boosts.

`python3 tests/e2e_resampler_quality.py --run` checks 44.1 kHz playback through the 48 kHz path for PulseAudio, native PipeWire, and ALSA, confirms quality 14 on each actual stream, and measures all four lanes plus 100 ms continuity windows.

## Remove

```sh
./scripts/uninstall.sh
```

The uninstaller stops the optional spatial service and removes the panel, helper, and spatial service files. It keeps speaker-route state, the hardware config, and EQ/routing configuration so those settings can be reviewed or reused.

## License and notices

Blue View original work is source-available under [PolyForm Noncommercial 1.0.0](LICENSE). The noncommercial restriction means this project is not an OSI-approved open-source license. PipeWire's built-in filter-chain processing remains open source under its upstream terms.

The panel and model include modified Omarchy audio-panel code, which remains subject to its MIT notice. The Open Quad graph adapts PipeWire's included `sink-upmix-5.1-filter.conf`, also under its upstream MIT notice. See [NOTICE.md](NOTICE.md) and the complete texts in [third-party](third-party/).

Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).

The ordinary playback configuration now uses PipeWire’s `simple` upmix, which retains centered material in the rear pair. Open Quad remains the separate spatial option. `python3 tests/e2e_centered_fill.py --run` checks mono and centered stereo on a temporary silent virtual sink, verifies all four lanes with simple fill, and compares the PSD baseline. This is a digital regression check, not an acoustic test.
