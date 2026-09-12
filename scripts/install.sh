#!/usr/bin/env bash
# Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
PLUGIN_DIR="$CONFIG_HOME/omarchy/plugins/fperez.audio"
BIN_DIR="$HOME/.local/bin"
UNIT_DIR="$CONFIG_HOME/systemd/user"
SHELL_CONFIG="$CONFIG_HOME/omarchy/shell.json"

if [[ ! -f "$SHELL_CONFIG" ]]; then
  printf 'Omarchy shell config not found: %s\nRun this installer on an Omarchy desktop.\n' "$SHELL_CONFIG" >&2
  exit 1
fi

install_if_missing() {
  local source="$1"
  local destination="$2"
  if [[ ! -e "$destination" ]]; then
    install -Dm644 "$source" "$destination"
  fi
}

install -Dm644 "$ROOT/Panel.qml" "$PLUGIN_DIR/Panel.qml"
install -Dm644 "$ROOT/Model.js" "$PLUGIN_DIR/Model.js"
install -Dm644 "$ROOT/manifest.json" "$PLUGIN_DIR/manifest.json"
install -Dm644 "$ROOT/assets/blueview-logo-dark.svg" "$PLUGIN_DIR/blueview-logo-dark.svg"
install -Dm755 "$ROOT/scripts/imac-audio-controls" "$BIN_DIR/imac-audio-controls"
install_if_missing "$ROOT/pipewire/50-blue-view-open-quad.conf" \
  "$CONFIG_HOME/pipewire/filter-chain.conf.d/50-blue-view-open-quad.conf"
install_if_missing "$ROOT/systemd/blue-view-open-quad.service" \
  "$UNIT_DIR/blue-view-open-quad.service"

CONFIG_FILE="$CONFIG_HOME/blueview-audio/config.json"
if [[ ! -e "$CONFIG_FILE" ]]; then
  install -Dm644 "$ROOT/docs/config.example.json" "$CONFIG_FILE"
fi

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
bar = data.setdefault("bar", {})
layout = bar.setdefault("layout", {})
right = layout.setdefault("right", [])
if not any(isinstance(item, dict) and item.get("id") == "fperez.audio"
           for group in layout.values() if isinstance(group, list) for item in group):
    right.append({"id": "fperez.audio"})
rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
if path.read_text() != rendered:
    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    shutil.copy2(path, path.with_name(path.name + ".bak." + stamp))
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    with os.fdopen(fd, "w") as stream:
        stream.write(rendered)
    os.replace(tmp_name, path)
PY

systemctl --user daemon-reload
printf 'Blue View Audio installed. Reload the Omarchy shell or sign out and back in to show the panel.\n'
printf 'Open Quad Spatial remains off until you enable it in the panel.\n'
printf 'Existing hardware, EQ, and spatial configuration files are preserved.\n'
