# TUK — Mark LV upgrade set

Included in this source release:
1. Assistant identity defaults to **TUK**.
2. Memory is explicitly context-only; current commands take priority.
3. API keys migrate from `config/api_keys.json` into the OS keychain (`keyring`).
4. Bundled API secret removed from the project config.
5. Health-check module + HUD button.
6. Cross-platform login auto-start support; background mode starts silently.
7. Background mode bootstraps the local wake-word engine and enables it.
8. App control extended with list / uninstall / update-all actions.
9. Basic Good Morning / Work Mode routines.
10. PyInstaller native-app build helper for macOS / Windows / Linux.
11. Release update checker.
12. Existing undo, plugin loader, themes, wake word and app-store systems retained.

Important: the bundled OpenWakeWord model is still the pretrained **Hey Jarvis** model.
Renaming the assistant to TUK does not magically retrain that acoustic model. A future
custom `Hey TUK` model can be dropped into the wake-word layer without changing the rest
of the architecture.


## 2026-09-30 — Gemini key compatibility fix
- All Gemini API key readers now use `memory.config_manager.get_gemini_key()`.
- Removed direct `["gemini_api_key"]` lookups that caused `KeyError` after keychain migration.
- Added `keyring` as an explicit dependency.
- Legacy plaintext keys are migrated to the OS credential store and removed from JSON.

## 2026-09-30 — Always-on mode
- New **ALWAYS-ON** button (settings drawer): silent login start (`--background`), wake word armed, tray icon.
- System tray (PyQt6 `QSystemTrayIcon`, no new dependency): click = open, menu = Open / Quit.
- Saying "Hey Jarvis" pops the window (windowed, not fullscreen); it tucks away again when TUK goes back to sleep. If you opened it yourself it stays open.
- Closing the window in Always-on mode hides it to the tray instead of quitting; quit from the tray menu.
- Shared login-start code (`core/startup.py`) now drives both AUTO-START and ALWAYS-ON. Fixes Linux `tuk.desktop`/`TUK.desktop` name mismatch that made the toggle never show ON, and packaged (frozen) builds now register the right executable.
- macOS: Accessibility prompt is requested once on first background start (`core/permissions.py`); the microphone prompt comes from macOS the first time audio opens.
- If the wake model can't be downloaded, background start shows the window rather than leaving the mic live behind a hidden window.
- Limitation: wake phrase is still the pretrained "Hey Jarvis" model.

## 2026-09-30 — Standalone macOS TUK Wake Daemon
- Always-On now launches a separate `core/tuk_daemon.py` process at login.
- The daemon owns the microphone and performs local `faster-whisper` wake detection for the standalone word `TUK`.
- On `TUK`, the daemon launches the GUI with `--wake-ui` as a separate process.
- The GUI does not create a tray icon in daemon-launched mode and can close normally on macOS.
- When the GUI goes idle, it exits; the daemon remains alive and waits for the next `TUK`.
- macOS LaunchAgent registration now uses `launchctl bootout/bootstrap` so changes take effect immediately.

## 2026-09-30 — macOS wake-ui crash fix
- Fixed "Python quit unexpectedly" when closing the wake-daemon GUI (X) or when TUK goes idle.
- The `--wake-ui` process now ends with `os._exit(0)` right after the Qt loop stops, skipping interpreter teardown (PortAudio/Qt/threads). The wake daemon stays alive and relaunches the GUI on the next "TUK".
- Wake daemon now dispatches at the very top of `main.py`, before Qt/OpenCV/Gemini imports (lean process, no native-lib clash with ctranslate2). `KMP_DUPLICATE_LIB_OK=TRUE` set early.
- `faulthandler` writes native-crash tracebacks to `~/Library/Logs/TUK/crash-{daemon,wake-ui,app}.log`.
- LaunchAgent gets `KeepAlive` (restart on crash, stay stopped on clean exit).
- macOS: `installer/build_mac_app.sh` builds `dist/TUK.app` + `dist/TUK.dmg` (thin signed launcher with NSMicrophoneUsageDescription). When TUK.app is installed, Always-on registers the LaunchAgent through `open -W -n -a TUK.app --args --wake-daemon`, so macOS treats TUK (not bare Python) as the microphone client.


## 2026-09-30 — Voice reliability + App Store install fixes
Voice / wake ("sometimes commands are ignored"):
- **Reconnects no longer put TUK back to sleep.** Every Gemini reconnect (dropped socket, voice change, mic switch) forced `_awake = False`, so the next command was silently ignored. Only the first connect sleeps now.
- **Wake pre-roll.** The last ~2.5 s of mic audio is kept in RAM while asleep and sent to the model when "TUK" is detected, so "TUK open Safari" in one breath is no longer lost during the ~1 s detection delay. Nothing is sent while asleep. Tune/disable with `WAKE_PREROLL_SECONDS` in `main.py` (0 = off).
- **Wake detector queue kept the OLDEST audio and dropped the NEWEST** whenever a transcription was running. It now drops the oldest. Window/keep sizes increased so the word is not cut in half.
- **Wake word spelling.** Whisper rarely writes the made-up word "TUK" literally; "tuck / tock / tok" are accepted too, and Whisper is primed with "TUK". ("took" is deliberately not accepted.)
- **Daemon mic watchdog.** `tuk_daemon.py` reopens the microphone if it stalls or errors (Bluetooth headset connect/disconnect, sleep/wake) instead of staying alive but deaf. Audio buffer raised from 1.5 s to ~6 s.
- If the wake detector cannot start, TUK stays awake instead of dropping all audio.
- A saved push-to-talk setting whose hotkey failed to start no longer leaves the mic closed forever; PTT is not restored in wake-daemon mode.
- `_asst_name` placeholder fixed.

App Store / installs:
- `brew` and `mas` were invisible to the app when launched from Finder/launchd (minimal PATH). PATH is extended in `app_store.py` and in the `TUK.app` launcher (`installer/build_mac_app.sh`) - **rebuild TUK.app** to get the launcher fix.
- The App Store "Get" click reported success even when nothing was clicked, then waited 15 minutes. It now reports honestly and falls back to Homebrew / tells you to press Get.
- Detects macOS Automation denial (-1743) as well as Accessibility; also reads the button's description and stops if the app is already installed ("Open").
- The wake-daemon GUI auto-quit after 2 idle minutes and killed downloads in progress. Auto-sleep is now blocked while a confirmation or install is running (`confirm.busy()`); confirmation timeout 90 s -> 180 s.
- `open_app`: on macOS the Spotlight-typing fallback always "succeeded", so TUK said "Opened X" for apps that were not installed and never offered to download them. It now reports "not installed" so the model can call `app_store`.
- `open_app` alias matching was substring-based ("Xcode" -> VS Code, "GitHub Desktop" -> Terminal); it is now whole-word.

## 2026-09-30 — J.A.R.V.I.S. (Iron Man) interface
- **New default HUD style `jarvis`** (`HudCanvas._paint_jarvis` in `ui.py`): compass bezel with rotating degree ticks and numbers, radar sweep, counter-rotating segmented rings, gold guide arcs, voice-spectrum ring driven by the real audio level, arc-reactor coil around a lens carrying the assistant name, north marker, double corner brackets, blueprint grid background and a slow scan line.
- **Live corner telemetry** (real data): CPU / MEM bars, clock and date, network MB/s, mic level, voice state, temperature when available. A block hides itself if it would touch the ring, so it fits any window size.
- **Stark palette**: navy glass + cyan line-work + gold speaking accent (`class C`). Corners are now square (2-3 px) instead of soft iOS pills; hover/pressed/input fills retuned to match. The accent colour picker still hue-shifts the whole theme.
- The HUD button in settings now cycles J.A.R.V.I.S. -> Modern orb -> Animated face -> Reactor core. Existing users who saved a style keep it; delete `hud_style` from `config/api_keys.json` (or tap the button) to switch.


## 2026-09-30 — Neon-glass dashboard refresh
- Updated the dashboard shell to a deeper navy/black surface with cyan edge lighting, more rounded interactive controls, and a larger centered assistant title.
- Increased the default workspace to 1200×780 while adapting to available Mac screen bounds; reduced side-panel widths to give the animated HUD more room.
- Preserved the existing command, audio, settings, file upload, activity log, camera, video, and HUD control wiring.
- Kept the HUD palette dark regardless of macOS light/dark appearance so the cyan/gold J.A.R.V.I.S. effects remain consistent.

## Universal API helper update
- Expanded the OpenAI-compatible backend alias list for common hosted API gateways and custom endpoints.
- Fixed the generic backend warm-up request to include configured authorization headers and normalize `/v1` base URLs.
- Added `UNIVERSAL_API_SUPPORT.md` and `config/api_keys.example.json` with setup guidance.
- Scope note: TUK's primary realtime voice session and many bundled skills still use Gemini-specific APIs; this update does not claim every feature can be switched to every provider.


## Provider setup update
- Added provider selector: Gemini Live, OpenAI-compatible text/chat, and local Ollama text/chat.
- Added configurable API base URL and model name.
- OpenAI-compatible key stored in macOS Keychain/OS credential store.
- Generic text/chat mode uses the configured provider through the existing LLM client; Gemini realtime voice remains provider-specific.
- See `UNIVERSAL_API_SUPPORT.md` for setup and limitations.


## Sri Lankan personal assistant update (2026-09-30)
- Added a Sri Lanka-focused system-prompt section for Sinhala speech/writing, local context, factual/neutral headlines, and casual personalization.
- Startup briefing now fetches Sri Lankan headlines rather than world headlines and uses the user's first name only.
- Added `actions/sri_lanka_news.py`, which returns current Sri Lankan headline search results for spoken delivery without opening a browser tab.
- Reworked `actions/weather_report.py` to fetch current conditions/forecast via Open-Meteo (no API key) and return the report for spoken delivery instead of opening a Chrome tab.
- Existing app structure, Gemini Live session, provider selector, and action auto-discovery remain intact.

## 2026-09-30 — Sri Lankan assistant roadmap implementation
- Added `actions/study_assistant.py` for Sri Lankan O/L and A/L study support: step-by-step explanations, notes, quizzes, practice questions, and revision plans. The tool avoids claiming generated questions are official past-paper questions.
- Added `actions/personalization.py` and config helpers for explicit nickname/preferred-address changes. Existing Memory Overlay remains the inspect/forget interface for saved memories.
- Expanded the system prompt with Sinhala/Singlish, local news/weather, study mode, nickname consent, voice-first action routing, and memory-management guidance.
- Improved OpenAI-compatible API error messages: distinguish authentication, access, endpoint/model, temporary rate-limit, quota/billing, and service errors; preserve provider error codes and `Retry-After` when available; do not blindly retry quota failures.
- Existing Sri Lanka news/weather actions, OS reminders, Mac desktop actions, local TUK wake detection, provider selector, and J.A.R.V.I.S. HUD remain in place.
- Added offline unit tests in `tests/test_next_update.py` for provider errors, study context, nickname persistence, and mocked news/weather actions.
- Limitations: non-Gemini provider mode remains text/chat rather than Gemini Live realtime voice; provider-specific tool/vision/audio support is not universal. News/weather require internet; local wake-word detection works offline but uses the current local Whisper model and wake phrase, not a custom-trained acoustic model.

- 2026-09-30: Fixed WhatsApp sends being skipped when the same text existed in older chat history; added RSS-first Sri Lankan headlines with search fallback; fixed startup news scheduling after wake-word activation.
