from time import sleep
import os
import re
import logging
from prometheus_client import start_http_server, REGISTRY
from prometheus_client.metrics_core import GaugeMetricFamily


def get_env_var(name, default=None, prefix="WEBSOCKET_EXPORTER_"):
    """Returns the value of the environment variable with th given name
    :param name: name of environment variable
    :param prefix prefix to env var name
    :param default: default value if the environment variable was not set
    :return: value of the given environment variable
    """
    if not prefix:
        prefix = ""
    return os.environ.get(f"{prefix}{name}", default)


def validate_uri(uri):
    assert re.match(
        r"wss?://\S+", uri.strip()
    ), "Not a valid websocket uri, start with ws(s)://"


URI = get_env_var("URI")
MESSAGE = get_env_var("MESSAGE", None)
EXPECTED_MESSAGE = get_env_var("EXPECTED_MESSAGE", None)
MATCH_TYPE = get_env_var("MATCH_TYPE", "contains")
TIMEOUT = int(get_env_var("TIMEOUT", "10"))
LISTEN_ADDR = get_env_var("LISTEN_ADDR", "0.0.0.0")
LISTEN_PORT = int(get_env_var("LISTEN_PORT", "9802"))


import asyncio
import logging
from time import perf_counter
from typing import Union

from websockets import NegotiationError, client


EXACT_MATCH = "exact"
CONTAINS_MATCH = "contains"


class ProbResults(object):
    def __init__(self, up: int, latency: float = 0, received: int = 0):
        self.up = up
        self.latency = round(latency, 2)
        self.received = int(received) if received is not None else "NaN"

    def __str__(self):
        if self.up:
            return f'Websocket up, latency:{self.latency}s, expected response {"" if self.received else "NOT"} received'
        return f"Webserver DOWN"


class WebSocketProbe(object):

    def __init__(
        self, uri, message=None, expected=None, match=CONTAINS_MATCH, timeout=10
    ):
        """
        Create a websocket probe that tries establishing a connection and reports the metrics
        :param uri: starts with 'ws://' or ws://
        :param message: optional message to send to server
        :param expected: (optional) response to expect for the server
        :param match_contains: weather match the expected response exactly or response should contain this only
        """
        self.uri = uri
        self.message = message
        self.expected_message = expected
        self.match = match
        self.timeout = timeout

    def probe(self) -> ProbResults:
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        future_instance = asyncio.ensure_future(self._connect_and_get())
        result = event_loop.run_until_complete(future_instance)
        event_loop.close()
        return result

    async def _connect_and_get(self) -> ProbResults:
        socket = None
        received = None
        try:
            start = perf_counter()
            socket = await client.connect(self.uri, timeout=self.timeout)
            latency_ms = (perf_counter() - start) * 1000
            if self.message:
                await socket.send(self.message)
            if self.expected_message:
                received = await self._await_expected_response(socket)
            await socket.close()
            return ProbResults(up=True, latency=latency_ms, received=received)
        except NegotiationError:
            if socket:
                await socket.wait_closed()
            return ProbResults(up=False)

    async def _await_expected_response(self, connection) -> Union[bool, None]:
        elapsed = 0
        while elapsed < self.timeout:
            try:
                resp = await asyncio.wait_for(
                    connection.recv(), timeout=(self.timeout - elapsed)
                )
                if self._match(resp):
                    return True
                await asyncio.sleep(1)
                elapsed += 1
            except asyncio.TimeoutError:
                logging.info(
                    f"Time out while waiting for {self.expected_message} from {self.uri}"
                )
                return None
        return None

    def _match(self, resp: str) -> bool:
        if self.match == EXACT_MATCH:
            return resp == self.expected_message
        elif self.match == CONTAINS_MATCH:
            return self.expected_message in resp
        return False


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")


class WebSocketUptimeCollector(object):
    def __init__(self, probe=None):
        if not probe:
            probe = WebSocketProbe(
                uri=URI,
                message=MESSAGE,
                expected=EXPECTED_MESSAGE,
                match=MATCH_TYPE,
                timeout=TIMEOUT,
            )
        self._probe = probe

    def collect(self):
        result = self._probe.probe()
        yield GaugeMetricFamily(
            "websocket_probe_success",
            "1 if websocket is up 0 otherwise",
            value=result.up,
        )
        yield GaugeMetricFamily(
            "websocket_probe_latency",
            "latency in connection",
            value=result.latency,
            unit="milliseconds",
        )
        yield GaugeMetricFamily(
            "websocket_probe_received_expected_response",
            "1 if the expected message received after connection established 0 otherwise",
            value=result.received,
        )


def main():
    assert URI, 'URI to probe was not set, set "WEBSOCKET_EXPORTER_URI" env var'
    logging.info(f"Trying to start exporter, will probe {URI}")
    if MESSAGE:
        logging.info(f"sends {MESSAGE} after connection")
    if EXPECTED_MESSAGE:
        logging.info(f"waits for {EXPECTED_MESSAGE} to match ({MATCH_TYPE})")
        logging.info(f"Timeouts after {TIMEOUT} seconds")
    REGISTRY.register(WebSocketUptimeCollector())
    logging.info(f"started exporter, listening on {LISTEN_ADDR}:{LISTEN_PORT}")
    start_http_server(port=LISTEN_PORT, addr=LISTEN_ADDR)
    while True:
        sleep(1)


if __name__ == "__main__":
    main()
