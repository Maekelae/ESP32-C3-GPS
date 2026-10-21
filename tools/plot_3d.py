"""Plot a live GPS track in local east/north/up coordinates."""

import argparse
from collections import deque

import matplotlib

matplotlib.use("qtagg")

import matplotlib.pyplot as plt
import serial
from matplotlib.animation import FuncAnimation

from telemetry import decode_telemetry_line, local_xyz


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="ESP32 serial port, e.g. COM5")
    parser.add_argument("--baud", type=int, default=115200, help="USB serial baud rate")
    parser.add_argument(
        "--points",
        type=int,
        default=1000,
        help="Maximum track points shown (default: 1000)",
    )
    args = parser.parse_args()
    if args.points < 2:
        parser.error("--points must be at least 2")

    track: deque[tuple[float, float, float]] = deque(maxlen=args.points)
    origin: tuple[float, float, float] | None = None
    latest_fix = "Waiting for GPS fix..."
    connection = serial.Serial(args.port, args.baud, timeout=0.05)

    figure = plt.figure()
    axes = figure.add_subplot(111, projection="3d")
    (track_line,) = axes.plot([], [], [], color="tab:blue", marker=".", markersize=3)
    axes.set_xlabel("East (m)")
    axes.set_ylabel("North (m)")
    axes.set_zlabel("Up (m)")

    def update(_frame: int) -> tuple:
        nonlocal origin, latest_fix
        line = connection.readline().decode("utf-8", errors="replace").strip()
        payload = decode_telemetry_line(line)
        if payload is not None:
            latitude = payload.get("latitude")
            longitude = payload.get("longitude")
            altitude = payload.get("altitude_m")
            if (
                payload.get("fix")
                and isinstance(latitude, (int, float))
                and isinstance(longitude, (int, float))
                and isinstance(altitude, (int, float))
            ):
                current = (float(latitude), float(longitude), float(altitude))
                if origin is None:
                    origin = current
                track.append(local_xyz(*current, origin))
                latest_fix = (
                    f"Satellites: {payload.get('satellites', 'unknown')} | "
                    f"Speed: {payload.get('speed_kmh', 'unknown')} km/h"
                )
            elif not payload.get("fix"):
                latest_fix = "No GPS fix"

        if track:
            east, north, up = zip(*track)
            track_line.set_data(east, north)
            track_line.set_3d_properties(up)
            axes.set_xlim(min(east) - 1, max(east) + 1)
            axes.set_ylim(min(north) - 1, max(north) + 1)
            axes.set_zlim(min(up) - 1, max(up) + 1)
        axes.set_title(f"Live GPS track (relative to first fix)\n{latest_fix}")
        return (track_line,)

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
