"""Set up the FIDO BLE lock controls."""
from homeassistant.const import Platform
from .const import DOMAIN
PLATFORMS = [Platform.BUTTON, Platform.SENSOR]

async def async_setup_entry(hass, entry):
    from .button import ConnectionManager
    from .fingerprint import FingerprintHistory
    history = FingerprintHistory(hass, entry)
    await history.async_load()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ConnectionManager(hass, entry, history)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass, entry):
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        manager = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if manager is not None:
            await manager.async_close()
    return unloaded
