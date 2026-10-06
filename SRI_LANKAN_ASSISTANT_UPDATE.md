# TUK — Sri Lankan Personal Assistant update

## Preserved
- Existing project layout, provider selector, J.A.R.V.I.S. HUD, Gemini Live session, local wake daemon, desktop actions, OS reminders, memory overlay, and Sri Lankan news/weather actions.

## Added or improved
- Sinhala/Singlish writing and speaking guidance in the main system prompt.
- `personalize_user` action to explicitly set or inspect the preferred form of address without changing the full name.
- `sri_lankan_study_assistant` action for Sri Lankan O/L/A/L explanations, notes, quizzes, practice questions, and revision plans.
- Provider HTTP error diagnostics distinguish invalid authentication, denied access, invalid endpoint/model, temporary rate limiting, exhausted quota/billing, and temporary service outages.
- Provider error body codes/messages and `Retry-After` are included safely; quota/billing errors are not blindly retried.
- Offline tests cover these additions and mock live-data actions to avoid making network calls during tests.

## Important capability boundaries
- Gemini Live remains the realtime microphone/voice engine. OpenAI-compatible provider selection supports text/chat and compatible tool calls where implemented; it does not automatically provide realtime audio, vision, or all desktop actions.
- News/weather are live-data features and require internet access. No live headlines/weather are fabricated when retrieval fails.
- Wake-word detection uses the existing local Whisper-based detector and the configured TUK phrase; it is not a custom-trained wake model.
- Actual macOS/M2 UI, microphone, LaunchAgent, and wake-word hardware tests must be performed on the user's Mac.

## Tests
Run from the project root:

```bash
python -m unittest tests.test_next_update -v
python -m compileall -q .
```


## Combined WhatsApp + startup news fix (30 September 2026)
- WhatsApp no longer refuses a new send just because the same short text (for example, “hi”) appears in older chat history. It still verifies after the current send and avoids retrying uncertain sends.
- Sri Lankan news uses a fresh Google News RSS search first, then falls back to the existing DDG news backend when RSS is unavailable.
- Startup briefing now uses the dedicated Sri Lankan news action and is scheduled when wake-word mode becomes awake; it still respects the Morning Briefing setting.
- Automated regression checks cover the old-history WhatsApp issue, news fallback, and wake/startup briefing wiring. A real WhatsApp GUI send and live network news fetch still need testing on the user's Mac.
