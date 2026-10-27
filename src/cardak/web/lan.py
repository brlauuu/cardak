"""This computer's address on the home network, for the link `cardak web setup` prints."""

import socket


def lan_address() -> str | None:
    """The source address for a private-range destination; a UDP connect sends no packet."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            address = s.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith("127.") else address
