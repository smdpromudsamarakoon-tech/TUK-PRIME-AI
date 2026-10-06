"""Focused offline tests for the Sri Lankan assistant update.

Run from the project root with: python -m unittest tests.test_next_update -v
No live API keys, network calls, or GUI session are required.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import personalization, study_assistant, sri_lanka_news, weather_report
from core.llm_client import describe_provider_http_error


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}", response=self)


class ProviderErrorTests(unittest.TestCase):
    def test_429_rate_limit_has_retry_guidance(self):
        response = FakeResponse(429, {"error": {"code": "rate_limit_exceeded", "message": "Too many requests"}}, {"Retry-After": "8"})
        message = describe_provider_http_error(response)
        self.assertIn("rate limit", message.lower())
        self.assertIn("8 seconds", message)

    def test_429_quota_does_not_recommend_retrying(self):
        response = FakeResponse(429, {"error": {"code": "insufficient_quota", "message": "Quota exceeded"}})
        message = describe_provider_http_error(response)
        self.assertIn("billing", message.lower())
        self.assertIn("will not restore quota", message)

    def test_401_points_to_api_key(self):
        message = describe_provider_http_error(FakeResponse(401, {"error": {"message": "Unauthorized"}}))
        self.assertIn("API key", message)

    def test_call_llm_surfaces_provider_quota_details(self):
        import core.llm_client as llm_client
        response = FakeResponse(429, {"error": {"code": "insufficient_quota", "message": "No credits remaining"}})
        with patch.object(llm_client, "get_llm_provider", return_value="openai"), \
             patch.object(llm_client, "get_llm_settings", return_value=("https://api.example.test/v1", "test-model")), \
             patch.object(llm_client, "_compatible_headers", return_value={"Content-Type": "application/json"}), \
             patch.object(llm_client.requests, "post", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "retrying will not restore quota"):
                llm_client.call_llm([{"role": "user", "content": "hi"}])


class StudyAssistantTests(unittest.TestCase):
    def test_ol_study_brief_is_curriculum_aware(self):
        result = study_assistant.study_assistant_action({"level": "O/L", "subject": "Science", "topic": "electricity", "task": "notes", "language": "Sinhala"})
        self.assertIn("Ordinary Level", result)
        self.assertIn("Science", result)
        self.assertIn("Sinhala", result)
        self.assertIn("verified source was retrieved", result)

    def test_missing_topic_asks_for_details(self):
        result = study_assistant.study_assistant_action({"level": "A/L"})
        self.assertIn("subject", result.lower())


class PersonalizationTests(unittest.TestCase):
    def test_nickname_is_saved_without_overwriting_other_config(self):
        from memory import config_manager
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "api_keys.json"
            config_path.write_text('{"assistant_name":"TUK","llm_model":"example-model"}', encoding="utf-8")
            with patch.object(config_manager, "CONFIG_DIR", root), patch.object(config_manager, "CONFIG_FILE", config_path):
                result = personalization.personalize_action({"action": "set", "preferred_address": "  Nethu   bro  "})
                self.assertIn("Nethu bro", result)
                saved = config_manager.load_api_keys()
                self.assertEqual(saved["preferred_address"], "Nethu bro")
                self.assertEqual(saved["assistant_name"], "TUK")
                self.assertEqual(saved["llm_model"], "example-model")


class SriLankanLiveDataTests(unittest.TestCase):
    def test_news_is_returned_as_spoken_text_without_browser(self):
        with patch.object(sri_lanka_news, "_google_news_rss", return_value=[]), \
             patch("actions.web_search._ddg_news", return_value=[{"title": "Local headline", "source": "Test source", "snippet": "Short summary", "url": "https://example.test/news"}]):
            result = sri_lanka_news.sri_lanka_news_action({"limit": 3})
        self.assertIn("Latest Sri Lanka headlines", result)
        self.assertIn("Local headline", result)
        self.assertIn("Source link", result)

    def test_weather_uses_live_data_provider_response(self):
        def fake_json(url, params):
            if "geocoding" in url:
                return {"results": [{"name": "Colombo", "country": "Sri Lanka", "country_code": "LK", "latitude": 6.9, "longitude": 79.8}]}
            return {"current": {"temperature_2m": 29, "relative_humidity_2m": 70, "apparent_temperature": 32, "wind_speed_10m": 10, "weather_code": 2}, "daily": {"time": ["2026-09-30", "2026-10-01"], "temperature_2m_max": [31, 30], "temperature_2m_min": [25, 24], "precipitation_probability_max": [20, 40]}}
        with patch.object(weather_report, "_get_json", side_effect=fake_json):
            result = weather_report.weather_action({"city": "Colombo, Sri Lanka", "time": "today"})
        self.assertIn("Colombo", result)
        self.assertIn("29 degrees Celsius", result)
        self.assertIn("Open-Meteo", result)


class RegressionFixTests(unittest.TestCase):
    def test_whatsapp_does_not_skip_new_message_if_old_history_contains_same_text(self):
        source = (Path(__file__).resolve().parent.parent / "actions" / "send_message.py").read_text(encoding="utf-8")
        self.assertIn('common texts like "hi"', source)
        self.assertNotIn('return f"The message to {receiver} was already sent; I did not send a duplicate."', source)

    def test_startup_news_uses_sri_lanka_action_and_wake_schedules_briefing(self):
        source = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
        self.assertIn("sri_lanka_news_action as _fetch_sri_lanka_news", source)
        self.assertIn("self._schedule_startup_briefing_if_needed()", source)
        self.assertIn("def _schedule_startup_briefing_if_needed", source)

    def test_news_rss_falls_back_to_existing_search(self):
        with patch.object(sri_lanka_news, "_google_news_rss", side_effect=TimeoutError("offline")), \
             patch("actions.web_search._ddg_news", return_value=[{"title": "Fallback headline", "source": "Fallback", "snippet": "Summary", "url": "https://example.test"}]):
            result = sri_lanka_news.sri_lanka_news_action({"limit": 3})
        self.assertIn("Fallback headline", result)


if __name__ == "__main__":
    unittest.main()
