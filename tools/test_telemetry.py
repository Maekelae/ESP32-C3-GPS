import unittest

from telemetry import decode_telemetry_line, format_telemetry, local_xyz
from plot_2d_map import lat_lon_to_mercator, lat_lon_to_tile


class TelemetryTests(unittest.TestCase):
    def test_decodes_json_after_startup_text(self) -> None:
        payload = decode_telemetry_line(
            'boot message {"type":"gps","fix":true,"latitude":60.1}'
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["latitude"], 60.1)

    def test_decodes_readable_gps_record(self) -> None:
        payload = decode_telemetry_line(
            "GPS|utc=120000.00|date=211026|fix=1|satellites=7|"
            "latitude=60.1|longitude=25.2|altitude_m=42.5|"
            "speed_kmh=1.8|course_deg=90.0"
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["utc"], "120000.00")
        self.assertEqual(payload["fix"], True)
        self.assertEqual(payload["satellites"], 7)
        self.assertEqual(payload["latitude"], 60.1)
        self.assertEqual(payload["altitude_m"], 42.5)

    def test_ignores_other_or_invalid_lines(self) -> None:
        self.assertIsNone(decode_telemetry_line("not JSON"))
        self.assertIsNone(decode_telemetry_line('{"type":"other"}'))

    def test_origin_maps_to_zero_and_altitude_to_up(self) -> None:
        origin = (60.0, 25.0, 10.0)

        self.assertEqual(local_xyz(*origin, origin), (0.0, 0.0, 0.0))
        self.assertEqual(local_xyz(60.0, 25.0, 13.5, origin)[2], 3.5)

    def test_coordinate_change_maps_to_local_metres(self) -> None:
        east, north, _ = local_xyz(60.001, 25.002, 10.0, (60.0, 25.0, 10.0))

        self.assertGreater(east, 0.0)
        self.assertGreater(north, 0.0)

    def test_gnss_time_is_first_displayed_field(self) -> None:
        formatted = format_telemetry(
            {"type": "gps", "fix": True, "utc": "120000.00", "date": "211026"}
        )

        self.assertEqual(formatted.splitlines()[0], "GNSS time (UTC): 120000.00")

    def test_map_projection_and_tile_coordinates(self) -> None:
        zoom = 18
        tile_count = 1 << zoom

        self.assertEqual(lat_lon_to_tile(0.0, 0.0, zoom), (tile_count // 2, tile_count // 2))
        projected_origin = lat_lon_to_mercator(0.0, 0.0)
        self.assertAlmostEqual(projected_origin[0], 0.0)
        self.assertAlmostEqual(projected_origin[1], 0.0)


if __name__ == "__main__":
    unittest.main()
