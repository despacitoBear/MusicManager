import os
import sys
import shutil
import platform
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SYSTEM     = platform.system()

# Parse args
ONEFILE = "--onefile" in sys.argv

def check_import(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False

def find_main_script():
    for name in ("music_manager.py", "music_converter.py", "main.py"):
        p = SCRIPT_DIR / name
        if p.exists():
            return p
    return None

FFMPEG_NAME = "ffmpeg.exe" if SYSTEM == "Windows" else "ffmpeg"
FFMPEG_PATH = SCRIPT_DIR / FFMPEG_NAME

def check_requirements():
    ok = True
    main_script = find_main_script()
    
    if main_script:
        print(f"  [OK] Script   : {main_script.name}")
    else:
        print("  [!!] Script   : NOT FOUND (music_converter.py)")
        ok = False

    if FFMPEG_PATH.exists():
        print(f"  [OK] ffmpeg   : {FFMPEG_NAME}")
    else:
        print(f"  [!!] ffmpeg   : MISSING ({FFMPEG_NAME} must be in root)")
        ok = False

    # Проверяем библиотеки в самом Python, а не в папке libs
    for lib in ["mutagen", "PIL", "yt_dlp"]:
        name = "Pillow" if lib == "PIL" else lib
        if check_import(lib):
            print(f"  [OK] {name:8} : Installed in environment")
        else:
            print(f"  [!!] {name:8} : Missing — run: pip install {name.lower()}")
            ok = ok if lib == "yt_dlp" else False # yt-dlp можно сделать опциональным, если нужно

    if not check_import("PyInstaller"):
        print("  [!!] PyInstaller missing — run: pip install pyinstaller")
        ok = False
    else:
        print("  [OK] PyInstaller")

    return ok

def build():
    main_script = find_main_script()
    dist_dir = SCRIPT_DIR / "dist"
    
    # Формируем команду сборки БЕЗ папки libs
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile" if ONEFILE else "--onedir",
        "--windowed",
        "--collect-all", "PIL",        # Это лечит ошибку _imaging
        "--collect-all", "mutagen",
        "--add-binary", f"{FFMPEG_NAME}{';.' if SYSTEM == 'Windows' else ':.'}",
        "--name", "MusicManager",
        str(main_script)
    ]

    print("\nRunning PyInstaller...")
    subprocess.run(cmd, check=True)

if __name__ == "__main__":
    print("=" * 52)
    print("  MusicManager Builder (Fixed for .venv)")
    print("=" * 52)

    if not check_requirements():
        print("\n  Fix the issues above and re-run.")
        sys.exit(1)

    build()
    print("\n  BUILD SUCCESSFUL!")