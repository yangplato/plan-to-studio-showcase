"""探测本地能力。报告只含布尔值。"""
import importlib.util
import json
import os
from pathlib import Path
import shutil


def key_available():
    if os.getenv('MINIMAX_API_KEY', '').strip():
        return True
    try:
        return bool((Path.home()/'.config/minimax/api_key').read_bytes().strip())
    except OSError:
        return False


if __name__ == '__main__':
    checks = {m: importlib.util.find_spec(m) is not None for m in ('PIL','pptx','edge_tts','playwright')}
    checks.update({cmd: bool(shutil.which(cmd)) for cmd in ('ffmpeg','ffprobe','measure-cjk')})
    checks['chrome'] = bool(shutil.which('google-chrome') or shutil.which('chromium') or
                            Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome').is_file())
    checks['realesrgan'] = os.access(Path.home()/'.local/bin/realesrgan-ncnn-vulkan',os.X_OK)
    checks['minimax_key'] = key_available()
    print(json.dumps(checks,indent=2))
