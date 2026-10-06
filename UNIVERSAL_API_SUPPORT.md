# TUK API provider modes

TUK now offers three setup modes:

1. **Gemini Live** — existing realtime audio/voice session and tool integration. Requires a Gemini API key.
2. **OpenAI-compatible API** — text/chat mode for OpenAI and providers exposing the OpenAI-compatible `POST /v1/chat/completions` contract. Configure the provider's base URL, exact model ID, and API key. Examples of the general pattern include hosted gateways and self-hosted servers; each provider/model may support a different subset of capabilities.
3. **Ollama** — local text/chat mode, no cloud API key required. Ollama must be installed and the selected model pulled locally.

## Important limitations

- There is no universal API key that works across every provider. Select the matching provider and use that provider's key.
- The OpenAI-compatible mode is **text/chat only** in this build. It does not replace Gemini Live's realtime microphone, audio streaming, or Gemini-specific tool session. The UI command box accepts typed questions and shows the response in the activity log.
- A provider that does not implement OpenAI-compatible chat completions needs its own adapter; changing the URL/key alone cannot make every API format compatible.
- API keys are stored in the operating-system credential store (macOS Keychain on macOS), not in the JSON config. The config stores only provider, base URL, and model name.

## OpenAI-compatible setup

- Provider: `OpenAI-compatible API — text/chat`
- Base URL: provider's API base URL, commonly ending in `/v1`
- Model name: exact model ID offered by the provider
- API key: key issued by that provider

Restart TUK after switching provider modes. Check provider documentation for model names, quota, authentication format, and supported parameters.
