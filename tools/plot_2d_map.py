"""Show the live GPS track over satellite imagery tiles."""

import argparse
from io import BytesIO
import math
import urllib.error
import urllib.request

import matplotlib

matplotlib.use("qtagg")

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from PIL import Image, UnidentifiedImageError
import serial

from telemetry import decode_telemetry_line

EARTH_RADIUS_M = 6_378_137.0
TILE_SIZE = 256
MAX_MERCATOR_LATITUDE = 85.05112878
IMAGERY_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/"
    "MapServer/tile/{zoom}/{y}/{x}"
)
IMAGERY_ATTRIBUTION = "Satellite imagery: Esri, Maxar, Earthstar Geographics"


def lat_lon_to_tile(latitude: float, longitude: float, zoom: int) -> tuple[int, int]:
    """Convert WGS84 coordinates to slippy-map tile coordinates."""
    latitude = min(MAX_MERCATOR_LATITUDE, max(-MAX_MERCATOR_LATITUDE, latitude))
    tile_count = 1 << zoom
    x = int((longitude + 180.0) / 360.0 * tile_count)
    latitude_radians = math.radians(latitude)
    y = int(
        (1.0 - math.asinh(math.tan(latitude_radians)) / math.pi)
        / 2.0
        * tile_count
    )
    return min(tile_count - 1, max(0, x)), min(tile_count - 1, max(0, y))


def lat_lon_to_mercator(latitude: float, longitude: float) -> tuple[float, float]:
    """Project WGS84 coordinates into Web Mercator metres."""
    latitude = min(MAX_MERCATOR_LATITUDE, max(-MAX_MERCATOR_LATITUDE, latitude))
    x = EARTH_RADIUS_M * math.radians(longitude)
    y = EARTH_RADIUS_M * math.log(
        math.tan(math.pi / 4.0 + math.radians(latitude) / 2.0)
    )
    return x, y


def pixel_to_mercator(pixel_x: float, pixel_y: float, zoom: int) -> tuple[float, float]:
    world_size = TILE_SIZE * (1 << zoom)
    half_world_m = math.pi * EARTH_RADIUS_M
    return (
        pixel_x / world_size * 2.0 * half_world_m - half_world_m,
        half_world_m - pixel_y / world_size * 2.0 * half_world_m,
    )


class SatelliteTiles:
    """Download and cache satellite tiles around the current GPS position."""

    def __init__(self, zoom: int) -> None:
        self.zoom = zoom
        self.center_tile: tuple[int, int] | None = None
        self.cache: dict[tuple[int, int], Image.Image] = {}

    def _get_tile(self, x: int, y: int) -> Image.Image:
        tile_count = 1 << self.zoom
        x %= tile_count
        if not 0 <= y < tile_count:
            raise ValueError("Satellite tile is outside the map's latitude range.")
        key = (x, y)
        if key in self.cache:
            return self.cache[key]

        url = IMAGERY_URL.format(zoom=self.zoom, x=x, y=y)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "ESP32-C3-GPS-telemetry/1.0"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            tile = Image.open(BytesIO(response.read())).convert("RGB")
        self.cache[key] = tile
        return tile

    def around(
        self, latitude: float, longitude: float
    ) -> tuple[Image.Image, tuple[float, float, float, float]]:
        center_x, center_y = lat_lon_to_tile(latitude, longitude, self.zoom)
        self.center_tile = center_x, center_y
        first_x, first_y = center_x - 1, center_y - 1
        mosaic = Image.new("RGB", (TILE_SIZE * 3, TILE_SIZE * 3))

        for row in range(3):
            for column in range(3):
                tile_y = min((1 << self.zoom) - 1, max(0, first_y + row))
                tile = self._get_tile(first_x + column, tile_y)
                mosaic.paste(tile, (column * TILE_SIZE, row * TILE_SIZE))

        left, top = pixel_to_mercator(first_x * TILE_SIZE, first_y * TILE_SIZE, self.zoom)
        right, bottom = pixel_to_mercator(
            (first_x + 3) * TILE_SIZE,
            (first_y + 3) * TILE_SIZE,
            self.zoom,
        )
        return mosaic, (left, right, bottom, top)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="ESP32 serial port, e.g. COM3")
    parser.add_argument("--baud", type=int, default=115200, help="USB serial baud rate")
    parser.add_argument("--zoom", type=int, default=18, help="Satellite map zoom (1-19)")
    parser.add_argument(
        "--points",
        type=int,
        default=2000,
        help="Maximum track points shown (default: 2000)",
    )
    args = parser.parse_args()
    if not 1 <= args.zoom <= 19:
        parser.error("--zoom must be between 1 and 19")
    if args.points < 2:
        parser.error("--points must be at least 2")

    connection = serial.Serial(args.port, args.baud, timeout=0.05)
    tiles = SatelliteTiles(args.zoom)
    eastings: list[float] = []
    northings: list[float] = []
    map_error: str | None = None

    figure, axes = plt.subplots()
    axes.set_xlabel("Easting (Web Mercator metres)")
    axes.set_ylabel("Northing (Web Mercator metres)")
    (track_line,) = axes.plot([], [], color="tab:red", linewidth=2, label="GPS track")
    (current_position,) = axes.plot(
        [], [], marker="o", color="yellow", markeredgecolor="black", label="Current fix"
    )
    axes.legend(loc="upper right")
    figure.text(0.01, 0.01, IMAGERY_ATTRIBUTION, fontsize=8)

    def update(_frame: int) -> tuple:
        nonlocal map_error
        line = connection.readline().decode("utf-8", errors="replace").strip()
        payload = decode_telemetry_line(line)
        if payload is None:
            return track_line, current_position

        latitude = payload.get("latitude")
        longitude = payload.get("longitude")
        if (
            not payload.get("fix")
            or not isinstance(latitude, (int, float))
            or not isinstance(longitude, (int, float))
        ):
            return track_line, current_position

        latitude = float(latitude)
        longitude = float(longitude)
        east, north = lat_lon_to_mercator(latitude, longitude)
        eastings.append(east)
        northings.append(north)
        if len(eastings) > args.points:
            del eastings[:-args.points]
            del northings[:-args.points]
        track_line.set_data(eastings, northings)
        current_position.set_data([east], [north])

        if map_error is None:
            center_tile = lat_lon_to_tile(latitude, longitude, args.zoom)
            if center_tile != tiles.center_tile:
                try:
                    mosaic, extent = tiles.around(latitude, longitude)
                except (OSError, urllib.error.URLError, UnidentifiedImageError, ValueError) as error:
                    map_error = str(error)
                    print(f"Could not load satellite imagery: {map_error}")
                else:
                    if axes.images:
                        axes.images[0].remove()
                    axes.imshow(
                        mosaic,
                        extent=extent,
                        origin="upper",
                        interpolation="bilinear",
                        zorder=0,
                    )
                    axes.set_xlim(extent[0], extent[1])
                    axes.set_ylim(extent[2], extent[3])

        axes.set_title(
            f"Live GPS satellite map — {payload.get('satellites', 'unknown')} satellites"
            if map_error is None
            else f"GPS track (satellite imagery unavailable: {map_error})"
        )
        return track_line, current_position

    animation = FuncAnimation(
        figure,
        update,
        interval=50,
        blit=False,
        cache_frame_data=False,
    )
    try:
        plt.show()
    finally:
        connection.close()
        del animation


if __name__ == "__main__":
    main()
