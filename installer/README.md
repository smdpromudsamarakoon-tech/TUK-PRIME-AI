# TUK installers

The release pipeline should produce:
- macOS: `TUK.app` and a `.dmg`
- Windows: `TUK.exe` (or a signed installer)
- Linux: `TUK` AppImage/package

`installer/build.py` creates the native PyInstaller application. End users do **not** need Python, pip, or a terminal once a release artifact is built.

For production releases, sign/notarize the macOS app and code-sign the Windows executable.
