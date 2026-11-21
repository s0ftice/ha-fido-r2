"""Discover supported lock records via the Tuya mobile account API."""
from __future__ import annotations

PRODUCT_ID = 'jiqwweph'


class _GroupSession:
    """Add the unsigned group parameter to one mobile API request."""
    def __init__(self, session, group_id):
        self._session = session
        self._group_id = str(group_id)

    def post(self, url, *, data, **kwargs):
        return self._session.post(url, data={**data, 'gid': self._group_id}, **kwargs)


def _items(response):
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        result = response.get('result')
        if isinstance(result, list):
            return result
    raise ValueError('Unexpected device-list response')


def supported_devices(records):
    """Keep only known product IDs and deduplicate across homes."""
    devices = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        if (record.get('productId') or record.get('product_id')) != PRODUCT_ID:
            continue
        device_id = record.get('devId') or record.get('deviceId') or record.get('id')
        if isinstance(device_id, str) and len(device_id) == 16 and device_id.isascii():
            name = record.get('name')
            devices[device_id] = name.strip() if isinstance(name, str) and name.strip() else 'FIDO R2'
    return devices


async def async_supported_devices(client):
    """List compatible locks across homes without saving response credentials."""
    homes = _items(await client._mobile_call('tuya.m.location.list', '2.1', {}))
    records = []
    for home in homes:
        if not isinstance(home, dict):
            continue
        group_id = home.get('gid') or home.get('groupId') or home.get('locationId')
        if group_id is None:
            continue
        original = client.session
        client.session = _GroupSession(original, group_id)
        try:
            records.extend(_items(await client._mobile_call('tuya.m.my.group.device.list', '1.0', {})))
        finally:
            client.session = original
    return supported_devices(records)
