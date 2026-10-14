#!/bin/sh
# Start the widget server (if it is not running) and open the widgets as small Chrome app windows in a column at the
# top-left: local pages, machines, then the "windows" of widgets.json. Each fits its content and docks below the one
# before it. On a Mac that shows them all the time, a LaunchAgent runs this at login (README).
cd "$(dirname "$0")"
pgrep -qf "$PWD/widget.py" || (nohup /usr/bin/python3 "$PWD/widget.py" >> widget.log 2>&1 < /dev/null &)
sleep 1
/usr/bin/python3 -c '
import json, os
windows = [["pages", "", 30, 120], ["machines", "machines", 150, 180]]  # name, path, top, first height
if os.path.exists("widgets.json"):
    windows += json.load(open("widgets.json")).get("windows", [])
for name, path, y, h in windows:
    print(name, path or "-", y, h)
' | while read -r name path y h; do
  [ "$path" = - ] && path=""
  open -na "Google Chrome" --args --user-data-dir="$PWD/.chrome-$name" --no-first-run --no-default-browser-check \
    --app="http://127.0.0.1:8091/$path" --window-position="0,$y" --window-size="380,$h"
done
