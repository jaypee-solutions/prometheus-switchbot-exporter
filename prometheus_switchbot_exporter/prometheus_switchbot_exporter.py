import json
import logging
from dataclasses import asdict, dataclass

import click
from bluepy import btle
from prometheus_client import Gauge, start_http_server

SWITCHBOT_MANUFACTURER_ID = "5900"
SWITCHBOT_SERVICE_ID = "cba20d00-224d-11e6-9fb8-0002a5d5c51b"

DEVICE_NAME_MAP = {
    "f2:0c:69:f4:56:af": "Haustür",
    "ee:ed:06:15:e1:fc": "Terasse",
    "f2:8d:34:be:e8:e6": "Wohnzimmer",
    "e3:06:1c:69:f5:67": "Büro",
    "ce:e7:0b:2e:1a:fe": "Bad",
}

gauge_rssi = Gauge(
    "switchbot_rssi",
    "The Received Signal Strength Indicator (RSSI) of the device",
    ["device", "name"],
)
gauge_battery = Gauge(
    "switchbot_battery", "The battery percentage of the device", ["device", "name"]
)
gauge_humidity = Gauge(
    "switchbot_humidity",
    "The humidity percentage measured by the device",
    ["device", "name"],
)
gauge_temperature = Gauge(
    "switchbot_temperature",
    "The temperature in celsius measured by the device",
    ["device", "name"],
)


@dataclass
class Measurement:
    battery: int
    temperature: float
    humidity: int


class SwitchbotDelegate(btle.DefaultDelegate):
    def handleDiscovery(
        self, device: btle.ScanEntry, is_new_device: bool, is_new_data: bool
    ):
        if is_switchbot_thermometer(device):
            measurement = parse_device_data(device)
            publish_measurement(device, measurement)


def is_switchbot_thermometer(device: btle.ScanEntry):
    device_str = f"Device (addr={device.addr},name={DEVICE_NAME_MAP.get(device.addr)},rssi={device.rssi},connectable={device.connectable})"

    if not device.scanData:
        logging.debug("%s exposes no data", device_str)
        return False

    scan_data = get_scan_data(device)
    logging.debug("%s scan data: %s", device_str, json.dumps(scan_data, indent=4))

    if not scan_data.get("Manufacturer", "").startswith(SWITCHBOT_MANUFACTURER_ID):
        logging.debug("%s manufacturer is not Swichbot", device_str)
        return False

    if scan_data.get("Complete 128b Services") != SWITCHBOT_SERVICE_ID:
        logging.debug("%s service ID is not Swichbot Thermometer", device_str)
        return False

    logging.info("%s is Switchbot thermometer", device_str)
    return True


def get_scan_data(device: btle.ScanEntry):
    return {description: value for _, description, value in device.getScanData()}


def parse_device_data(device: btle.ScanEntry):
    service_data = get_scan_data(device).get("16b Service Data")
    if not service_data.startswith("000d"):
        raise ValueError(f"Malformed service data '{service_data}'")

    byte2 = int(service_data[8:10], 16)
    battery = byte2 & 127

    temperature_fractional = int(service_data[11:12].encode("utf-8"), 16) / 10.0
    temperature_integral = int(service_data[12:14].encode("utf-8"), 16)
    if temperature_integral < 128:
        temperature_integral *= -1
        temperature_fractional *= -1
    else:
        temperature_integral -= 128

    temperature = temperature_integral + temperature_fractional
    humidity = int(service_data[14:16].encode("utf-8"), 16) % 128

    return Measurement(battery, temperature, humidity)


def publish_measurement(device: btle.ScanEntry, measurement: Measurement):
    logging.info("Published: %s", json.dumps(asdict(measurement)))
    gauge_rssi.labels(
        device=device.addr, name=DEVICE_NAME_MAP.get(device.addr, device.addr)
    ).set(device.rssi)
    gauge_battery.labels(
        device=device.addr, name=DEVICE_NAME_MAP.get(device.addr, device.addr)
    ).set(measurement.battery)
    gauge_humidity.labels(
        device=device.addr, name=DEVICE_NAME_MAP.get(device.addr, device.addr)
    ).set(measurement.humidity)
    gauge_temperature.labels(
        device=device.addr, name=DEVICE_NAME_MAP.get(device.addr, device.addr)
    ).set(measurement.temperature)


def configure_logging(verbose, quiet, default_level=logging.INFO):
    level = default_level + (quiet - verbose) * 10
    logging.basicConfig(
        format="[%(levelname)-8s] %(message)s", datefmt="[%X]", level=level
    )


@click.command()
@click.option(
    "-p", "--metrics-port", default=8080, help="Expose metrics as a Prometheus target"
)
@click.option("-v", "--verbose", count=True, help="Increase logging verbosity")
@click.option("-q", "--quiet", count=True, help="Decrease verbosity")
def main(metrics_port, verbose, quiet):
    """Runs a Prometheus exporter scraping metrics from Switchbot thermometers nearby"""
    configure_logging(verbose, quiet)
    start_http_server(metrics_port)

    logging.debug(
        "Started Prometheus exporter on ':%s' scrapping Swichbot thermometers nearby",
        metrics_port,
    )
    scanner = btle.Scanner().withDelegate(SwitchbotDelegate())
    while True:
        try:
            scanner.scan(60)
        except btle.BTLEDisconnectError as ex:
            logging.debug("Disconnection error: %s", ex)


if __name__ == "__main__":
    main()
