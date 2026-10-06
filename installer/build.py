"""Build TUK into a native app with PyInstaller.
Usage: python installer/build.py
macOS -> dist/TUK.app, Windows -> dist/TUK.exe, Linux -> dist/TUK.
"""
import os, platform, shutil, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
subprocess.check_call([sys.executable,"-m","pip","install","pyinstaller","keyring"])
sep=os.pathsep
add=[]
for folder in ("actions","core","memory","plugins","config"):
    add += ["--add-data", f"{ROOT/folder}{sep}{folder}"]
cmd=[sys.executable,"-m","PyInstaller","--noconfirm","--clean","--name","TUK","--windowed","--icon",str(ROOT/"config/jarvis.ico"),*add,str(ROOT/"main.py")]
subprocess.check_call(cmd,cwd=ROOT)
print("Built:", ROOT/"dist")
