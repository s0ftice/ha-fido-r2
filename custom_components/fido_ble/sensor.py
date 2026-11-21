"""Last fingerprint unlock sensor."""
from datetime import datetime, timezone
from homeassistant.components.sensor import SensorEntity
from .const import DOMAIN, CONF_FINGERPRINT_NAMES

async def async_setup_entry(hass, entry, async_add_entities):
    manager = hass.data[DOMAIN][entry.entry_id]
    sensor = FingerprintSensor(entry, manager.history)
    manager.history.sensor = sensor
    async_add_entities([sensor])

class FingerprintSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = 'Last fingerprint unlock'
    _attr_icon = 'mdi:fingerprint'
    def __init__(self, entry, history):
        self.entry, self.history = entry, history
        self._attr_unique_id = f'{entry.unique_id}_last_fingerprint_unlock'
        self._attr_device_info = {'identifiers': {(DOMAIN, entry.unique_id)}, 'name': 'FIDO R2', 'manufacturer': 'YouJia', 'model': 'YSR3711'}

    @property
    def native_value(self):
        latest = self.history.latest
        if not latest:
            return None
        return self.entry.data.get(CONF_FINGERPRINT_NAMES, {}).get(str(latest['slot']), f"Unknown fingerprint {latest['slot']}")

    @property
    def extra_state_attributes(self):
        latest = self.history.latest
        if not latest:
            return {}
        return {'fingerprint_id': latest['slot'], 'unlocked_at': datetime.fromtimestamp(latest['timestamp'], timezone.utc).isoformat(), 'recent_records': [{'fingerprint_id': r['slot'], 'name': self.entry.data.get(CONF_FINGERPRINT_NAMES, {}).get(str(r['slot'])), 'unlocked_at': datetime.fromtimestamp(r['timestamp'], timezone.utc).isoformat()} for r in self.history.records[-50:]]}
