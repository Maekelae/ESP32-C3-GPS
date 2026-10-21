#include <errno.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define GPS_UART UART_NUM_1
#define GPS_BAUD_RATE 115200
#define ESP_GPS_RX_PIN GPIO_NUM_20
#define ESP_GPS_TX_PIN GPIO_NUM_21
#define GPS_LINE_BUFFER_SIZE 128
#define TELEMETRY_BUFFER_SIZE 192
#define UBX_MAX_PAYLOAD_SIZE 256

typedef struct {
    bool fix;
    bool has_satellites;
    bool has_position;
    bool has_altitude;
    bool has_speed;
    bool has_course;
    int satellites;
    double latitude;
    double longitude;
    double altitude_m;
    double speed_kmh;
    double course_deg;
    char utc[16];
    char date[16];
} gps_data_t;

static bool parse_number(const char *text, double *value)
{
    char *end = NULL;
    errno = 0;
    double parsed = strtod(text, &end);
    if (text == end || *end != '\0' || errno == ERANGE || !isfinite(parsed)) {
        return false;
    }
    *value = parsed;
    return true;
}

static bool parse_coordinate(const char *value, const char *hemisphere, bool latitude, double *result)
{
    double nmea_value;
    if (!parse_number(value, &nmea_value) || hemisphere[0] == '\0') {
        return false;
    }

    double degrees = floor(nmea_value / 100.0);
    double minutes = nmea_value - degrees * 100.0;
    double coordinate = degrees + minutes / 60.0;
    if (minutes >= 60.0 || (latitude && coordinate > 90.0) || (!latitude && coordinate > 180.0)) {
        return false;
    }

    if ((latitude && hemisphere[0] == 'S') || (!latitude && hemisphere[0] == 'W')) {
        coordinate = -coordinate;
    } else if ((latitude && hemisphere[0] != 'N') || (!latitude && hemisphere[0] != 'E')) {
        return false;
    }

    *result = coordinate;
    return true;
}

static void copy_nmea_text(char *destination, size_t size, const char *source)
{
    size_t index = 0;
    while (source[index] != '\0' && index + 1 < size) {
        char character = source[index];
        if ((character < '0' || character > '9') && character != '.') {
            break;
        }
        destination[index] = character;
        index++;
    }
    destination[index] = '\0';
}

static void format_number(char *output, size_t output_size, bool available, double value)
{
    if (available) {
        snprintf(output, output_size, "%.6f", value);
    } else {
        snprintf(output, output_size, "null");
    }
}

static int gps_data_to_telemetry(const gps_data_t *gps, char *output, size_t output_size)
{
    char satellites[12];
    char latitude[32];
    char longitude[32];
    char altitude[32];
    char speed[32];
    char course[32];
    if (gps->has_satellites) {
        snprintf(satellites, sizeof(satellites), "%d", gps->satellites);
    } else {
        snprintf(satellites, sizeof(satellites), "null");
    }
    format_number(latitude, sizeof(latitude), gps->has_position && gps->fix, gps->latitude);
    format_number(longitude, sizeof(longitude), gps->has_position && gps->fix, gps->longitude);
    format_number(altitude, sizeof(altitude), gps->has_altitude && gps->fix, gps->altitude_m);
    format_number(speed, sizeof(speed), gps->has_speed && gps->fix, gps->speed_kmh);
    format_number(course, sizeof(course), gps->has_course && gps->fix, gps->course_deg);

    return snprintf(
        output,
        output_size,
        "GPS|utc=%s|date=%s|fix=%s|satellites=%s|latitude=%s|longitude=%s|"
        "altitude_m=%s|speed_kmh=%s|course_deg=%s",
        gps->utc,
        gps->date,
        gps->fix ? "1" : "0",
        satellites,
        latitude,
        longitude,
        altitude,
        speed,
        course);
}

static bool parse_nmea_sentence(char *line, gps_data_t *gps, bool *emit_telemetry)
{
    *emit_telemetry = false;
    if (line[0] != '$') {
        return false;
    }

    char *checksum_marker = strchr(line, '*');
    if (checksum_marker == NULL || strlen(checksum_marker + 1) < 2) {
        return false;
    }
    char *checksum_end = NULL;
    unsigned long expected_checksum = strtoul(checksum_marker + 1, &checksum_end, 16);
    if (checksum_end == checksum_marker + 1 || expected_checksum > 0xff) {
        return false;
    }
    unsigned char checksum = 0;
    for (char *character = line + 1; character < checksum_marker; character++) {
        checksum ^= (unsigned char)*character;
    }
    if (checksum != expected_checksum) {
        return false;
    }
    *checksum_marker = '\0';

    char *fields[20];
    size_t field_count = 0;
    fields[field_count++] = line;
    for (char *character = line; *character != '\0' && field_count < 20; character++) {
        if (*character == ',') {
            *character = '\0';
            fields[field_count++] = character + 1;
        }
    }

    size_t sentence_name_length = strlen(fields[0]);
    if (sentence_name_length < 4) {
        return false;
    }
    const char *sentence_type = fields[0] + sentence_name_length - 3;
    if (strcmp(sentence_type, "GGA") == 0 && field_count >= 10) {
        double quality;
        double satellites;
        if (parse_number(fields[6], &quality)) {
            gps->fix = quality > 0.0;
        }
        if (parse_number(fields[7], &satellites) && satellites >= 0 && satellites <= 99) {
            gps->satellites = (int)satellites;
            gps->has_satellites = true;
        }
        double latitude;
        double longitude;
        if (parse_coordinate(fields[2], fields[3], true, &latitude) &&
            parse_coordinate(fields[4], fields[5], false, &longitude)) {
            gps->latitude = latitude;
            gps->longitude = longitude;
            gps->has_position = true;
        }
        double altitude;
        if (parse_number(fields[9], &altitude)) {
            gps->altitude_m = altitude;
            gps->has_altitude = true;
        }
        copy_nmea_text(gps->utc, sizeof(gps->utc), fields[1]);
        return true;
    }

    if (strcmp(sentence_type, "RMC") == 0 && field_count >= 10) {
        *emit_telemetry = true;
        gps->fix = fields[2][0] == 'A';
        double latitude;
        double longitude;
        if (parse_coordinate(fields[3], fields[4], true, &latitude) &&
            parse_coordinate(fields[5], fields[6], false, &longitude)) {
            gps->latitude = latitude;
            gps->longitude = longitude;
            gps->has_position = true;
        }
        double speed_knots;
        if (parse_number(fields[7], &speed_knots) && speed_knots >= 0.0) {
            gps->speed_kmh = speed_knots * 1.852;
            gps->has_speed = true;
        }
        double course;
        if (parse_number(fields[8], &course) && course >= 0.0 && course <= 360.0) {
            gps->course_deg = course;
            gps->has_course = true;
        }
        copy_nmea_text(gps->utc, sizeof(gps->utc), fields[1]);
        copy_nmea_text(gps->date, sizeof(gps->date), fields[9]);
        return true;
    }

    return false;
}

static uint16_t read_ubx_u16(const uint8_t *data)
{
    return (uint16_t)data[0] | ((uint16_t)data[1] << 8);
}

static uint32_t read_ubx_u32(const uint8_t *data)
{
    return (uint32_t)data[0] |
        ((uint32_t)data[1] << 8) |
        ((uint32_t)data[2] << 16) |
        ((uint32_t)data[3] << 24);
}

static int32_t read_ubx_i32(const uint8_t *data)
{
    return (int32_t)read_ubx_u32(data);
}

static bool parse_ubx_nav_pvt(const uint8_t *payload, size_t payload_length, gps_data_t *gps)
{
    if (payload_length != 92) {
        return false;
    }

    uint8_t fix_type = payload[20];
    uint8_t flags = payload[21];
    uint8_t valid = payload[11];
    uint16_t year = read_ubx_u16(payload + 4);
    uint8_t month = payload[6];
    uint8_t day = payload[7];
    uint8_t hour = payload[8];
    uint8_t minute = payload[9];
    uint8_t second = payload[10];

    gps->fix = fix_type >= 2 && fix_type <= 5 && (flags & 0x01) != 0;
    gps->has_satellites = true;
    gps->satellites = payload[23];
    if ((valid & 0x03) == 0x03) {
        snprintf(gps->date, sizeof(gps->date), "%04u-%02u-%02u", year, month, day);
        snprintf(gps->utc, sizeof(gps->utc), "%02u:%02u:%02u", hour, minute, second);
    } else {
        gps->date[0] = '\0';
        gps->utc[0] = '\0';
    }

    if (gps->fix) {
        gps->longitude = (double)read_ubx_i32(payload + 24) * 1e-7;
        gps->latitude = (double)read_ubx_i32(payload + 28) * 1e-7;
        gps->has_position = true;
        gps->altitude_m = (double)read_ubx_i32(payload + 36) / 1000.0;
        gps->has_altitude = true;
        gps->speed_kmh = (double)read_ubx_i32(payload + 60) * 0.0036;
        gps->has_speed = true;
        gps->course_deg = (double)read_ubx_i32(payload + 64) * 1e-5;
        gps->has_course = true;
    }

    return true;
}

static bool ubx_checksum_valid(const uint8_t *packet, size_t packet_length)
{
    if (packet_length < 8) {
        return false;
    }
    uint8_t checksum_a = 0;
    uint8_t checksum_b = 0;
    for (size_t index = 2; index < packet_length - 2; index++) {
        checksum_a += packet[index];
        checksum_b += checksum_a;
    }
    return checksum_a == packet[packet_length - 2] &&
        checksum_b == packet[packet_length - 1];
}

void app_main(void)
{
    const uart_config_t uart_config = {
        .baud_rate = GPS_BAUD_RATE,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT
    };
    ESP_ERROR_CHECK(uart_driver_install(GPS_UART, 2048, 0, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(GPS_UART, &uart_config));
    ESP_ERROR_CHECK(uart_set_pin(
        GPS_UART, ESP_GPS_TX_PIN, ESP_GPS_RX_PIN, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    printf("GPS telemetry firmware started. GPS UART1: RX GPIO%d, TX GPIO%d, %d baud.\n",
           ESP_GPS_RX_PIN, ESP_GPS_TX_PIN, GPS_BAUD_RATE);
    printf("Waiting for NMEA or UBX NAV-PVT input. Connect GPS TX to GPIO20/D7 and share GND.\n");

    gps_data_t gps = {0};
    char line[GPS_LINE_BUFFER_SIZE];
    size_t line_length = 0;
    uint8_t input[64];
    uint8_t ubx_packet[UBX_MAX_PAYLOAD_SIZE + 8];
    size_t ubx_length = 0;
    size_t ubx_expected_length = 0;
    bool reading_ubx = false;
    bool saw_ubx_sync = false;
    uint32_t total_bytes = 0;
    uint32_t telemetry_updates = 0;
    uint32_t valid_gps_frames = 0;
    uint8_t first_rx_bytes[8];
    size_t first_rx_length = 0;
    TickType_t last_status = xTaskGetTickCount();

    while (true) {
        int bytes_read = uart_read_bytes(GPS_UART, input, sizeof(input), pdMS_TO_TICKS(100));
        if (bytes_read > 0) {
            total_bytes += (uint32_t)bytes_read;
        }
        for (int index = 0; index < bytes_read; index++) {
            uint8_t byte = input[index];
            if (first_rx_length < sizeof(first_rx_bytes)) {
                first_rx_bytes[first_rx_length++] = byte;
            }

            if (reading_ubx) {
                if (ubx_length < sizeof(ubx_packet)) {
                    ubx_packet[ubx_length++] = byte;
                } else {
                    reading_ubx = false;
                    ubx_length = 0;
                    ubx_expected_length = 0;
                }
                if (reading_ubx && ubx_length == 6) {
                    uint16_t payload_length = read_ubx_u16(ubx_packet + 4);
                    if (payload_length > UBX_MAX_PAYLOAD_SIZE) {
                        reading_ubx = false;
                        ubx_length = 0;
                    } else {
                        ubx_expected_length = (size_t)payload_length + 8;
                    }
                }
                if (reading_ubx && ubx_expected_length != 0 &&
                    ubx_length == ubx_expected_length) {
                    if (ubx_checksum_valid(ubx_packet, ubx_length)) {
                        valid_gps_frames++;
                        if (ubx_packet[2] == 0x01 && ubx_packet[3] == 0x07 &&
                            parse_ubx_nav_pvt(ubx_packet + 6, ubx_expected_length - 8, &gps)) {
                            char telemetry[TELEMETRY_BUFFER_SIZE];
                            int telemetry_length = gps_data_to_telemetry(
                                &gps, telemetry, sizeof(telemetry));
                            if (telemetry_length >= 0 &&
                                (size_t)telemetry_length < sizeof(telemetry)) {
                                printf("%s\n", telemetry);
                                telemetry_updates++;
                            }
                        }
                    }
                    reading_ubx = false;
                    ubx_length = 0;
                    ubx_expected_length = 0;
                }
                continue;
            }

            if (saw_ubx_sync) {
                saw_ubx_sync = false;
                if (byte == 0x62) {
                    reading_ubx = true;
                    ubx_packet[0] = 0xB5;
                    ubx_packet[1] = 0x62;
                    ubx_length = 2;
                    ubx_expected_length = 0;
                    line_length = 0;
                    continue;
                }
                if (line_length + 1 < sizeof(line)) {
                    line[line_length++] = (char)0xB5;
                }
            }

            if (byte == 0xB5) {
                saw_ubx_sync = true;
                continue;
            }

            if (byte == '$') {
                line_length = 0;
                line[line_length++] = '$';
                continue;
            }

            char character = (char)byte;
            if (character == '\n') {
                line[line_length] = '\0';
                bool emit_telemetry = false;
                if (parse_nmea_sentence(line, &gps, &emit_telemetry)) {
                    valid_gps_frames++;
                    if (emit_telemetry) {
                        char telemetry[TELEMETRY_BUFFER_SIZE];
                        int telemetry_length = gps_data_to_telemetry(&gps, telemetry, sizeof(telemetry));
                        if (telemetry_length >= 0 && (size_t)telemetry_length < sizeof(telemetry)) {
                            printf("%s\n", telemetry);
                            telemetry_updates++;
                        }
                    }
                }
                line_length = 0;
            } else if (character != '\r' && character >= 0x20 && character <= 0x7e &&
                       line_length + 1 < sizeof(line)) {
                line[line_length++] = character;
            } else if (character < 0x20 || character > 0x7e ||
                       line_length + 1 >= sizeof(line)) {
                line_length = 0;
            }
        }
        TickType_t now = xTaskGetTickCount();
        if ((now - last_status) >= pdMS_TO_TICKS(5000)) {
            float interval_seconds = (float)(now - last_status) / configTICK_RATE_HZ;
            float update_rate = interval_seconds > 0.0f
                ? (float)telemetry_updates / interval_seconds
                : 0.0f;
            printf("GPS status: %lu bytes; %lu valid GPS frames; %lu records, %.1f Hz; first RX bytes:",
                   (unsigned long)total_bytes,
                   (unsigned long)valid_gps_frames,
                   (unsigned long)telemetry_updates,
                   update_rate);
            for (size_t index = 0; index < first_rx_length; index++) {
                printf(" %02X", first_rx_bytes[index]);
            }
            printf("\n");
            telemetry_updates = 0;
            valid_gps_frames = 0;
            total_bytes = 0;
            last_status = now;
        }
    }
}