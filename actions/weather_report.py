"""Spoken weather reports using the free Open-Meteo API (no key, no browser tab)."""
from __future__ import annotations

import requests

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

_WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy with rime frost", 51: "light drizzle",
    53: "moderate drizzle", 55: "dense drizzle", 56: "light freezing drizzle",
    57: "dense freezing drizzle", 61: "light rain", 63: "moderate rain",
    65: "heavy rain", 66: "light freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "moderate snow", 75: "heavy snow", 77: "snow grains",
    80: "light rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "light snow showers", 86: "heavy snow showers", 95: "thunderstorm",
    96: "thunderstorm with light hail", 99: "thunderstorm with heavy hail",
}


def _get_json(url: str, params: dict) -> dict:
    response = requests.get(url, params=params, timeout=8, headers={"User-Agent": "TUK-Assistant/1.0"})
    response.raise_for_status()
    return response.json()


def weather_action(parameters: dict, player=None, session_memory=None) -> str:
    city = str(parameters.get("city") or "Colombo, Sri Lanka").strip()
    when = str(parameters.get("time") or "today").strip().lower()
    try:
        geo = _get_json(GEOCODE_URL, {"name": city, "count": 1, "language": "en", "format": "json"})
        places = geo.get("results") or []
        if not places:
            return f"I couldn't find a location matching {city}. Please name a Sri Lankan town or city."
        place = places[0]
        lat, lon = place["latitude"], place["longitude"]
        forecast = _get_json(FORECAST_URL, {
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
            "forecast_days": 2,
            "timezone": "Asia/Colombo" if "sri lanka" in city.lower() or place.get("country_code") == "LK" else "auto",
        })
        current = forecast.get("current") or {}
        daily = forecast.get("daily") or {}
        name = place.get("name", city)
        country = place.get("country", "")
        temp = current.get("temperature_2m")
        feels = current.get("apparent_temperature")
        humidity = current.get("relative_humidity_2m")
        wind = current.get("wind_speed_10m")
        code = current.get("weather_code")
        condition = _WEATHER_CODES.get(code, "conditions unavailable")
        parts = [f"Current weather for {name}{', ' + country if country else ''}: {condition}"]
        if temp is not None:
            parts.append(f"Temperature {round(temp)} degrees Celsius")
        if feels is not None:
            parts.append(f"feels like {round(feels)} degrees")
        if humidity is not None:
            parts.append(f"humidity {humidity} percent")
        if wind is not None:
            parts.append(f"wind {round(wind)} kilometres per hour")
        if when not in ("now", "current", "currently", "right now"):
            try:
                if daily.get("time"):
                    idx = 1 if when in ("tomorrow", "tmr") and len(daily["time"]) > 1 else 0
                    hi = daily.get("temperature_2m_max", [])[idx]
                    lo = daily.get("temperature_2m_min", [])[idx]
                    rain = daily.get("precipitation_probability_max", [None])[idx]
                    parts.append(f"Forecast: high {round(hi)} and low {round(lo)} degrees Celsius" if hi is not None and lo is not None else "")
                    if rain is not None:
                        parts.append(f"chance of precipitation {rain} percent")
            except (IndexError, TypeError, ValueError):
                pass
        result = ". ".join(p.strip() for p in parts if p).rstrip(".") + ". Data from Open-Meteo."
        _log(result, player)
        if session_memory:
            try:
                session_memory.set_last_search(query=f"weather {city} {when}", response=result)
            except Exception:
                pass
        return result
    except Exception as exc:
        msg = f"I couldn't retrieve live weather for {city} right now. Check your internet connection and try again."
        print(f"[Weather] Open-Meteo request failed: {exc}")
        _log(msg, player)
        return msg


def _log(message: str, player=None) -> None:
    print(f"[Weather] {message}")
    if player:
        try:
            player.write_log(f"SYS: {message}")
        except Exception:
            pass


TOOL = {
    "name": "weather_report",
    "description": (
        "Gets live weather and forecast data and returns it for the assistant to SPEAK aloud. "
        "Do NOT open a browser tab. For Sri Lanka questions, assume Sri Lanka; if no city is named, use Colombo. "
        "Use for current weather, rain, temperature, humidity, wind, and tomorrow's forecast."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "city": {"type": "STRING", "description": "City or town; default Colombo, Sri Lanka"},
            "time": {"type": "STRING", "description": "now, today, or tomorrow"},
        },
        "required": [],
    },
    "handler": weather_action,
}
