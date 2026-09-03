"""
Fetches current weather from OpenWeatherMap's free tier.
Sign up for a free API key at https://openweathermap.org/api
"""

import os
import requests

BASE_URL = "https://api.openweathermap.org/data/2.5/weather"


def get_current_weather(city: str) -> dict:
    """
    city: e.g. "Kochi,IN" or "London,GB"
    Returns: {"temp_c": float, "feels_like_c": float, "condition": str,
              "rain": bool, "wind_kph": float, "city": str}
    """
    api_key = os.environ.get("OPENWEATHER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENWEATHER_API_KEY is not set. Add it to your .env file.")

    params = {
        "q": city,
        "appid": api_key,
        "units": "metric",
    }
    resp = requests.get(BASE_URL, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    condition = data["weather"][0]["main"] if data.get("weather") else "Unknown"
    rain = condition.lower() in ("rain", "drizzle", "thunderstorm")

    return {
        "temp_c": data["main"]["temp"],
        "feels_like_c": data["main"]["feels_like"],
        "condition": condition,
        "rain": rain,
        "wind_kph": data.get("wind", {}).get("speed", 0) * 3.6,  # m/s -> kph
        "city": data.get("name", city),
    }
