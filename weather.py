"""
Fetches current weather and short-range forecasts from OpenWeatherMap's
free tier. Sign up for a free API key at https://openweathermap.org/api
"""

import os
from collections import defaultdict
from datetime import datetime

import requests

CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather"
FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"


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
    resp = requests.get(CURRENT_URL, params=params, timeout=10)
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


def _aggregate_daily_blocks(blocks: list) -> dict:
    """Reduces a day's worth of 3-hour forecast blocks into one summary.
    Picks the block closest to midday as representative for "feels like"
    and condition (most relevant to what someone will actually wear),
    while temp_min/temp_max span the whole day, and rain is True if ANY
    block that day mentions rain/drizzle/thunderstorm."""
    best_block = min(blocks, key=lambda b: abs(datetime.fromtimestamp(b["dt"]).hour - 13))
    condition = best_block["weather"][0]["main"] if best_block.get("weather") else "Unknown"

    any_rain = any(
        (b["weather"][0]["main"].lower() in ("rain", "drizzle", "thunderstorm")) if b.get("weather") else False
        for b in blocks
    )

    temps = [b["main"]["temp"] for b in blocks]

    return {
        "temp_c": best_block["main"]["temp"],
        "feels_like_c": best_block["main"]["feels_like"],
        "temp_min": min(temps),
        "temp_max": max(temps),
        "condition": condition,
        "rain": any_rain,
    }


def get_forecast(city: str, days: int = 5) -> list:
    """
    Returns a list of daily summaries for today + the next (days - 1) days:
    [{"date": "YYYY-MM-DD", "temp_c", "feels_like_c", "temp_min", "temp_max",
      "condition", "rain", "city"}, ...]
    Uses OpenWeatherMap's free "5 day / 3 hour" forecast endpoint, which is
    the finest granularity available on the free tier - we aggregate its
    3-hour blocks into one summary per calendar day.
    """
    api_key = os.environ.get("OPENWEATHER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENWEATHER_API_KEY is not set. Add it to your .env file.")

    params = {"q": city, "appid": api_key, "units": "metric"}
    resp = requests.get(FORECAST_URL, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    by_date = defaultdict(list)
    for block in data.get("list", []):
        date_str = block["dt_txt"].split(" ")[0]
        by_date[date_str].append(block)

    city_name = data.get("city", {}).get("name", city)
    sorted_dates = sorted(by_date.keys())[:days]

    forecast = []
    for date_str in sorted_dates:
        summary = _aggregate_daily_blocks(by_date[date_str])
        summary["date"] = date_str
        summary["city"] = city_name
        forecast.append(summary)

    return forecast
