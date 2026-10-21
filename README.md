# ESP32-C3 GPS telemetry

PlatformIO firmware for a Seeed Studio XIAO ESP32-C3 and a BZGNNS P18 Pro
GNSS receiver. The firmware reads NMEA sentences or UBX NAV-PVT binary messages
and writes one readable GPS record per line to the ESP32 USB serial console.
Python utilities can display
the fields as readable text or plot the live track in 3D.

## Wiring

The firmware uses UART1 at 115200 baud by default:

| P18 Pro pin | Connect to XIAO ESP32-C3 |
| --- | --- |
| TX (GPS output) | D7 / GPIO20 (ESP UART RX) |
| RX (GPS input) | D6 / GPIO21 (ESP UART TX; optional if GPS only sends data) |
| GND | GND |
| Power | Use the voltage specified for your P18 Pro board |

Connect the XIAO to the computer over USB for telemetry. Confirm the P18 Pro
UART baud rate and electrical voltage levels against its documentation. Change
The pin defines are from the ESP32's perspective: `ESP_GPS_RX_PIN` is GPIO20
(connect GPS TX here), and `ESP_GPS_TX_PIN` is GPIO21 (connect GPS RX here).
Change `GPS_BAUD_RATE` or these pin defines in `src/main.c` if your module or
wiring differs. GPS coordinates and altitude may be unavailable until the
receiver gets a fix outdoors.

## Build and upload

Open this project folder in PlatformIO, select `seeed_xiao_esp32c3`, then build
and upload. Open the PlatformIO serial monitor at 115200 baud.

The USB Serial/JTAG console reports when the firmware starts and prints a
status line every five seconds with received byte count, valid GPS frame count,
record rate, and the first received bytes in hexadecimal. Binary UBX data is
decoded instead of being printed as garbled text.

For NMEA, the firmware combines GGA fields (satellite count and altitude) with
each RMC update. For UBX, it decodes each NAV-PVT message, including position,
altitude, speed, heading, satellites, and UTC. It emits one plain-text record
per RMC sentence or NAV-PVT message. The record displays GNSS UTC time first.
The output rate follows the receiver's message output rate; the firmware does
not configure the receiver's navigation rate.

For 10 Hz telemetry, configure the GNSS receiver to produce RMC updates at 10 Hz
and set its UART baud rate to match `GPS_BAUD_RATE` in `src/main.c`. The current
GPS UART setting is 115200 baud, separate from the USB monitor's 115200 baud
setting.

Each RMC update or UBX NAV-PVT message produces one line in this format:

```text
GPS|utc=120000.00|date=211026|fix=1|satellites=7|latitude=60.123456|longitude=25.123456|altitude_m=42.1|speed_kmh=1.852|course_deg=90.0
```

Unavailable values are represented by `null`; coordinates and motion data are
`null` when the receiver reports no fix. Speed is converted from NMEA knots to
km/h. Coordinates are decimal degrees, altitude is metres, and course is
degrees clockwise from north.

## Python tools

Install the Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

The dependencies include the Qt GUI backend needed to display the live plot.

Replace `COM5` with the serial port assigned to the XIAO:

```powershell
python tools\read_telemetry.py --port COM5
python tools\plot_3d.py --port COM5
python tools\plot_2d_map.py --port COM5
```

Only one program can own the serial port at a time. The plot uses local
east/north/up coordinates in metres relative to the first valid fix and shows
up to 1,000 recent points by default. Run the Python test suite with:

```powershell
python -m unittest discover -s tools -p "test_*.py"
```

The 2D map downloads Esri World Imagery satellite tiles, overlays the live GPS
track, and follows the receiver's location. It requires an internet connection
and a valid GPS fix to load its first map tiles. Satellite imagery attribution:
Esri, Maxar, Earthstar Geographics, and the GIS User Community.
