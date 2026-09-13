#!/usr/bin/env bash
# Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
set -euo pipefail

CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
PLUGIN_DIR="$CONFIG_HOME/omarchy/plugins/fperez.audio"
BIN_DIR="$HOME/.local/bin"
SHELL_CONFIG="$CONFIG_HOME/omarchy/shell.json"

if [[ -x "$BIN_DIR/imac-audio-controls" ]]; then
  "$BIN_DIR/imac-audio-controls" spatial off >/dev/null 2>&1 || true
fi
systemctl --user stop blue-view-open-quad.service >/dev/null 2>&1 || true
rm -f "$PLUGIN_DIR/Panel.qml" "$PLUGIN_DIR/CameraMonitor.qml" "$PLUGIN_DIR/Model.js" "$PLUGIN_DIR/manifest.json" \
  "$PLUGIN_DIR/blueview-logo-dark.svg"
rmdir "$PLUGIN_DIR" 2>/dev/null || true
rm -f "$BIN_DIR/imac-audio-controls" \
  "$CONFIG_HOME/pipewire/pipewire-pulse.conf.d/40-blue-view-stereo-upmix.conf" \
  "$CONFIG_HOME/pipewire/client.conf.d/40-blue-view-stereo-upmix.conf" \
  "$CONFIG_HOME/pipewire/jack.conf.d/40-blue-view-jack.conf" \
  "$CONFIG_HOME/pipewire/pipewire-pulse.conf.d/45-blue-view-quality.conf" \
  "$CONFIG_HOME/pipewire/client.conf.d/45-blue-view-quality.conf" \
  "$CONFIG_HOME/pipewire/filter-chain.conf.d/50-blue-view-open-quad.conf" \
  "$CONFIG_HOME/systemd/user/blue-view-open-quad.service"
systemctl --user daemon-reload

if [[ -f "$SHELL_CONFIG" ]]; then
  python3 - "$SHELL_CONFIG" <<'PY'
import datetime
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text())
layout = data.get("bar", {}).get("layout", {})
changed = False
for group_name in ("left", "center", "right"):
    items = layout.get(group_name)
    if isinstance(items, list):
        kept = [item for item in items if not (isinstance(item, dict) and item.get("id") == "fperez.audio")]
        if len(kept) != len(items):
            layout[group_name] = kept
            changed = True
if changed:
    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    shutil.copy2(path, path.with_name(path.name + ".bak." + stamp))
    rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    with os.fdopen(fd, "w") as stream:
        stream.write(rendered)
    os.replace(tmp_name, path)
PY
fi

printf 'Blue View Audio removed. Saved routing state, hardware config, and EQ/routing files were kept.\n'
