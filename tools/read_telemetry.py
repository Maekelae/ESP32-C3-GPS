"""Read and pretty-print JSON GPS telemetry from the ESP32 serial port."""

import argparse
import serial

from telemetry import decode_telemetry_line, format_telemetry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="ESP32 serial port, e.g. COM5")
    parser.add_argument("--baud", type=int, default=115200, help="USB serial baud rate")
    args = parser.parse_args()

    with serial.Serial(args.port, args.baud, timeout=1) as connection:
        print(f"Listening on {args.port} at {args.baud} baud. Press Ctrl+C to stop.")
        try:
            while True:
                line = connection.readline().decode("utf-8", errors="replace").strip()
                payload = decode_telemetry_line(line)
                if payload is not None:
                    print("\n" + format_telemetry(payload) + "\n")
                elif line:
                    print(line)
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
