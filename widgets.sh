#!/bin/sh
# Start the widget server (if it is not running) and open the widgets as small Chrome app windows in a column at the
# top-left, top to bottom in the "order" of widgets.json (default: local pages, machines, then its "windows"). Each fits
# its content and docks below the one before it. On a Mac that shows them all the time, a LaunchAgent runs this at
# login (README).
cd "$(dirname "$0")"
pgrep -qf "$PWD/widget.py" || (nohup /usr/bin/python3 "$PWD/widget.py" >> widget.log 2>&1 < /dev/null &)
sleep 1
/usr/bin/python3 -c '
import json, os
cfg = json.load(open("widgets.json")) if os.path.exists("widgets.json") else {}
windows = {"pages": ("", 120), "machines": ("machines", 180)}  # name: path, first height (each then fits)
windows.update({w[0]: (w[1], w[3]) for w in cfg.get("windows", [])})  # [name, path, top (unused), height]
y = 30
for name in cfg.get("order") or list(windows):
    path, h = windows[name]
    print(name, path or "-", y, h)
    y += h
' | while read -r name path y h; do
  [ "$path" = - ] && path=""
  open -na "Google Chrome" --args --user-data-dir="$PWD/.chrome-$name" --no-first-run --no-default-browser-check \
    --app="http://127.0.0.1:8091/$path" --window-position="0,$y" --window-size="380,$h"
done
