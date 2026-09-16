"""
Maps an OpenWeatherMap condition string ("Clear", "Clouds", "Rain", etc.)
to a small inline animated SVG icon. Kept as raw SVG markup (not a
sprite/library) so each icon can carry its own light CSS animation without
extra dependencies - marked |safe in the templates that use this.
"""

_SUN = """
<svg class="weather-icon" width="56" height="56" viewBox="0 0 56 56" fill="none">
  <g class="wi-sun-rays" style="transform-origin:28px 28px">
    <g stroke="#C9962E" stroke-width="2.5" stroke-linecap="round">
      <line x1="28" y1="4" x2="28" y2="10"/>
      <line x1="28" y1="46" x2="28" y2="52"/>
      <line x1="4" y1="28" x2="10" y2="28"/>
      <line x1="46" y1="28" x2="52" y2="28"/>
      <line x1="10.7" y1="10.7" x2="14.9" y2="14.9"/>
      <line x1="41.1" y1="41.1" x2="45.3" y2="45.3"/>
      <line x1="10.7" y1="45.3" x2="14.9" y2="41.1"/>
      <line x1="41.1" y1="14.9" x2="45.3" y2="10.7"/>
    </g>
  </g>
  <circle cx="28" cy="28" r="12" fill="#D9A83D"/>
</svg>
"""

_CLOUDS = """
<svg class="weather-icon" width="56" height="56" viewBox="0 0 56 56" fill="none">
  <g class="wi-cloud-back">
    <ellipse cx="20" cy="26" rx="12" ry="9" fill="#C7CCD3"/>
  </g>
  <g class="wi-cloud-front">
    <ellipse cx="32" cy="32" rx="15" ry="11" fill="#AEB4BC"/>
  </g>
</svg>
"""

_RAIN = """
<svg class="weather-icon" width="56" height="56" viewBox="0 0 56 56" fill="none">
  <ellipse cx="28" cy="22" rx="16" ry="11" fill="#AEB4BC"/>
  <g class="wi-raindrops" stroke="#4A7BB5" stroke-width="2.5" stroke-linecap="round">
    <line x1="18" y1="38" x2="16" y2="46"/>
    <line x1="28" y1="38" x2="26" y2="46"/>
    <line x1="38" y1="38" x2="36" y2="46"/>
  </g>
</svg>
"""

_THUNDER = """
<svg class="weather-icon" width="56" height="56" viewBox="0 0 56 56" fill="none">
  <ellipse cx="28" cy="22" rx="16" ry="11" fill="#8E94A0"/>
  <polygon class="wi-bolt" points="30,32 22,44 27,44 24,52 36,38 30,38" fill="#D9A83D"/>
</svg>
"""

_SNOW = """
<svg class="weather-icon" width="56" height="56" viewBox="0 0 56 56" fill="none">
  <ellipse cx="28" cy="22" rx="16" ry="11" fill="#C7CCD3"/>
  <g class="wi-snowflakes" fill="#DCE6F0">
    <circle cx="18" cy="40" r="2.4"/>
    <circle cx="28" cy="44" r="2.4"/>
    <circle cx="38" cy="40" r="2.4"/>
  </g>
</svg>
"""

_MIST = """
<svg class="weather-icon" width="56" height="56" viewBox="0 0 56 56" fill="none">
  <g class="wi-mist" stroke="#B6BAC0" stroke-width="3" stroke-linecap="round">
    <line x1="10" y1="22" x2="46" y2="22"/>
    <line x1="14" y1="30" x2="42" y2="30"/>
    <line x1="10" y1="38" x2="46" y2="38"/>
  </g>
</svg>
"""

_ICONS = {
    "clear": _SUN,
    "clouds": _CLOUDS,
    "rain": _RAIN,
    "drizzle": _RAIN,
    "thunderstorm": _THUNDER,
    "snow": _SNOW,
    "mist": _MIST,
    "fog": _MIST,
    "haze": _MIST,
}


def weather_icon_svg(condition: str) -> str:
    key = (condition or "").strip().lower()
    return _ICONS.get(key, _CLOUDS)
