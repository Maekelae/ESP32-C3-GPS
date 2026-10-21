"""Helpers for parsing and displaying GPS telemetry JSON lines."""

import json
import math
from typing import Any


def decode_telemetry_line(line: str) -> dict[str, Any] | None:
    """Return a GPS record from a text line or legacy JSON telemetry line."""
    line = line.strip()
    if line.startswith("GPS|"):
        values: dict[str, Any] = {"type": "gps"}
        for field in line.split("|")[1:]:
            key, separator, raw_value = field.partition("=")
            if not separator or not key:
                return None
            if raw_value in ("", "null"):
                value: Any = None
            elif key == "fix":
                value = raw_value == "1"
            elif key == "satellites":
                try:
                    value = int(raw_value)
                except ValueError:
                    return None
            elif key in {"latitude", "longitude", "altitude_m", "speed_kmh", "course_deg"}:
                try:
                    value = float(raw_value)
                except ValueError:
                    return None
            else:
                value = raw_value
            values[key] = value
        return values if "fix" in values else None

    decoder = json.JSONDecoder()
    start = line.find("{")
    while start >= 0:
        try:
            payload, _ = decoder.raw_decode(line[start:])
        except json.JSONDecodeError:
            start = line.find("{", start + 1)
            continue
        if isinstance(payload, dict) and payload.get("type") == "gps":
            return payload
        start = line.find("{", start + 1)
    return None


def format_telemetry(payload: dict[str, Any]) -> str:
    """Format one GPS package as readable lines."""
    def show(value: Any, suffix: str = "") -> str:
        return "unavailable" if value is None or value == "" else f"{value}{suffix}"

    if not payload.get("fix", False):
        position = "No GPS fix"
    else:
        position = (
            f"{show(payload.get('latitude'))}, "
            f"{show(payload.get('longitude'))}"
        )

    return "\n".join(
        (
            f"GNSS time (UTC): {show(payload.get('utc'))}",
            f"Date: {show(payload.get('date'))}",
            f"Satellites: {show(payload.get('satellites'))}",
            f"Coordinates (lat, lon): {position}",
            f"Altitude: {show(payload.get('altitude_m'), ' m')}",
            f"Velocity: {show(payload.get('speed_kmh'), ' km/h')}",
            f"Course: {show(payload.get('course_deg'), ' deg')}",
            f"Fix: {'yes' if payload.get('fix', False) else 'no'}",
        )
    )


def local_xyz(
    latitude: float,
    longitude: float,
    altitude_m: float,
    origin: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Convert GPS coordinates to local east, north, up metres."""
    earth_radius_m = 6_371_000.0
    origin_latitude, origin_longitude, origin_altitude = origin
    mean_latitude_radians = math.radians((latitude + origin_latitude) / 2.0)
    east = (
        math.radians(longitude - origin_longitude)
        * earth_radius_m
        * math.cos(mean_latitude_radians)
    )
    north = math.radians(latitude - origin_latitude) * earth_radius_m
    up = altitude_m - origin_altitude
    return east, north, up
