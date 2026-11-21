"""Persist and publish timestamped fingerprint records."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from .const import DOMAIN, CONF_FINGERPRINT_NAMES

class FingerprintHistory:
    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.store = Store(hass, 1, f"{DOMAIN}_fingerprints_{entry.entry_id}", private=True)
        self.lock = asyncio.Lock()
        self.records = []
        self.sensor = None

    async def async_load(self):
        data = await self.store.async_load() or {}
        self.records = data.get('records', [])[-500:]

    async def async_record(self, slot, timestamp):
        if isinstance(slot, bool) or not isinstance(slot, int) or not isinstance(timestamp, (int, float)) or timestamp <= 0:
            return
        item = {'slot': slot, 'timestamp': float(timestamp)}
        async with self.lock:
            if any(r['slot'] == slot and r['timestamp'] == item['timestamp'] for r in self.records):
                return
            updated = sorted([*self.records, item], key=lambda r: r['timestamp'])[-500:]
            await self.store.async_save({'records': updated})
            self.records = updated
            if self.sensor and self.sensor.entity_id:
                self.sensor.async_write_ha_state()
                name = self.entry.data.get(CONF_FINGERPRINT_NAMES, {}).get(str(slot), f'Unknown fingerprint {slot}')
                event_time = dt_util.as_local(datetime.fromtimestamp(timestamp, timezone.utc)).isoformat(timespec='seconds')
                await self.hass.services.async_call('logbook', 'log', {'name': 'Fingerprint unlock', 'message': f'{name} used a fingerprint at {event_time}', 'entity_id': self.sensor.entity_id}, blocking=True)

    @property
    def latest(self):
        return self.records[-1] if self.records else None
