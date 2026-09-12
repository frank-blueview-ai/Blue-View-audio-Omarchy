# Blue-View-audio-Omarchy

Blue View Audio is a branded Omarchy / Quickshell audio panel for speaker routing, microphone control, camera power, live signal levels, and a detachable visual equalizer.

Brand: [blueview.ai](https://blueview.ai) · [Blue View OS](https://bvos.blueview.ai)

It extends Omarchy's existing audio panel. The panel shows the Blue View double-oval brand, exposes the active input and output meters, and keeps six EQ bands independently adjustable for the front and rear speaker pairs. The EQ has bar-meter and multi-wave views and opens as a floating, pinned window so it can stay visible on another workspace.

## Audio features

- Route each of four logical speaker outputs to a selected channel.
- Select audio output and input devices, and control their mute/volume levels.
- Toggle the iMac camera through its USB authorization switch.
- Adjust six parametric bands separately for the front and rear speaker pairs. EQ starts bypassed and flat.
- Turn on Open Quad Spatial for a phase-shaped rear fill from stereo. This uses PipeWire's built-in filter-chain nodes and does not decode Atmos objects or height channels.
- Open Dolby's official product page for purchase and setup information.

The Dolby button links to [Dolby Atmos for Headphones](https://www.dolby.com/experience/headphones/). Dolby consumer headphone purchase and activation are handled by Dolby Access on supported Windows/Xbox systems. This Linux applet cannot import or activate a Dolby license. Dolby Atmos Renderer is a separate production product for supported desktop/DAW workflows, not a system-wide PipeWire license.

## Requirements

- Omarchy with Quickshell and its existing `omarchy.audio` panel APIs.
- PipeWire, WirePlumber, `pactl`, and a four-channel output profile.
- Python 3 and systemd user services.
- For the included camera power switch, a camera exposing a writable USB `authorized` sysfs attribute and `pkexec`.

The included defaults match the four-channel iMac route this panel was built and tested on. For other hardware, edit `~/.config/blueview-audio/config.json` after installation:

```json
{
  "masterSink": "alsa_output.pci-0000_00_1b.0.analog-surround-40",
  "masterChannelMap": ["rear-left", "rear-right", "front-left", "front-right"],
  "cameraAuthorizationPath": "/sys/bus/usb/devices/1-5/authorized"
}
```

`masterSink` must name the four-channel PipeWire node behind the applet's `imac_fixed` remap. `masterChannelMap` describes that node's channel order. `cameraAuthorizationPath` must point to the camera device's `authorized` attribute; camera power changes prompt for administrator authorization.

## Install

Run from this repository on the Omarchy desktop:

```sh
./scripts/install.sh
```

The installer places the panel, control helper, and optional spatial filter files in the user's config directories. It adds `fperez.audio` to the right side of `~/.config/omarchy/shell.json`. It does not enable the spatial service or turn on EQ. Reload the Omarchy shell or sign out and back in to load the panel.

To enable spatial processing, switch on **Open Quad Spatial** in the panel. The helper only permits it while `imac_fixed` is the selected output, and turns the service off again when the switch is disabled. The EQ remains bypassed until its toggle is enabled.

## Remove

```sh
./scripts/uninstall.sh
```

The uninstaller stops the optional spatial service and removes the panel, helper, and spatial service files. It keeps speaker-route state, the hardware config, and EQ/routing configuration so those settings can be reviewed or reused.

## License and notices

Blue View original work is source-available under [PolyForm Noncommercial 1.0.0](LICENSE). The noncommercial restriction means this project is not an OSI-approved open-source license. PipeWire's built-in filter-chain processing remains open source under its upstream terms.

The panel and model include modified Omarchy audio-panel code, which remains subject to its MIT notice. The Open Quad graph adapts PipeWire's included `sink-upmix-5.1-filter.conf`, also under its upstream MIT notice. See [NOTICE.md](NOTICE.md) and the complete texts in [third-party](third-party/).

Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
