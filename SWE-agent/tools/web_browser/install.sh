#!/usr/bin/env bash

/path/to/root/python3.11/bin/python3 -m pip install flask requests playwright
/path/to/root/python3.11/bin/python3 -m playwright install-deps chromium

if [ -f /usr/bin/google-chrome ]; then
    export WEB_BROWSER_CHROMIUM_EXECUTABLE_PATH=/usr/bin/google-chrome
elif [ -f /usr/bin/chromium ]; then
    export WEB_BROWSER_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium
elif [ -f /usr/bin/google-chrome-stable ]; then
    export WEB_BROWSER_CHROMIUM_EXECUTABLE_PATH=/usr/bin/google-chrome-stable
else
    /path/to/root/python3.11/bin/python3 -m playwright install chromium
fi

export WEB_BROWSER_SCREENSHOT_MODE=print

export WEB_BROWSER_PORT=19321

mkdir -p /path/to/root/.web_browser_logs

run_web_browser_server &> /path/to/root/.web_browser_logs/web-browser-server.log &
