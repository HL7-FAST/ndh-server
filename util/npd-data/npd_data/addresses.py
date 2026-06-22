"""Address access and geographic anchor matching.

Location.address is a single object while Practitioner.address and
Organization.address are arrays; resource_addresses() normalizes both shapes
to a list. City matching is case-insensitive and whitespace-tolerant.
"""

import re

_WHITESPACE = re.compile(r"\s+")


def resource_addresses(resource):
    address = resource.get("address")
    if address is None:
        return []
    if isinstance(address, list):
        return address
    return [address]


def normalize_city(value):
    return _WHITESPACE.sub(" ", value).strip().casefold()


class AnchorMatcher:
    """Matches resources whose address city/state pair is a filter target."""

    def __init__(self, cities, state):
        self.cities = {normalize_city(c) for c in cities}
        self.state = state.strip().upper()
        # Tokens for the pre-parse line screen in line_might_match().
        self._city_tokens = [tuple(city.split()) for city in self.cities]

    def matches(self, resource):
        for address in resource_addresses(resource):
            if not isinstance(address, dict):
                continue
            city = address.get("city")
            state = address.get("state")
            if not city or not state:
                continue
            if state.strip().upper() != self.state:
                continue
            if normalize_city(city) in self.cities:
                return True
        return False

    def line_might_match(self, line_lower):
        """Cheap pre-parse screen for a lowercased raw NDJSON line.

        Requires every token of some city to appear in the line. False
        positives are fine; false negatives are not.
        """
        return any(all(token in line_lower for token in tokens) for tokens in self._city_tokens)
