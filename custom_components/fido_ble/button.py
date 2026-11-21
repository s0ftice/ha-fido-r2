"""Buttons for preparing a BLE connection and unlocking the FIDO lock."""
from __future__ import annotations
import asyncio
import logging
from tuya_ble_sdk.commands import parse_timestamp, parse_data_points
from tuya_ble_sdk.protocol import Frame
from homeassistant.components import bluetooth
from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from tuya_ble_sdk import TuyaBleClient, TuyaBleCredentials
from tuya_ble_sdk.protocol import TuyaBleCommandCode
from .const import DOMAIN, CONF_ADDRESS, CONF_DEVICE_ID, CONF_UUID, CONF_LOCAL_KEY, CONF_MEMBER_ID

_LOGGER = logging.getLogger(__name__)

class SerializedClient(TuyaBleClient):
    """Keep time replies and command fragments from interleaving."""
    def __init__(self, device, credentials, history):
        super().__init__(device, credentials)
        self.history = history
        self.record_tasks = set()
        self._send_lock = asyncio.Lock()
        self.time_reply_sent = asyncio.Event()

    async def _async_send(self, frame, *, key, security_flag):
        async with self._send_lock:
            await super()._async_send(frame, key=key, security_flag=security_flag)
            if frame.response_to and frame.code in (TuyaBleCommandCode.RECEIVE_UNIX_TIME_REQUEST, TuyaBleCommandCode.RECEIVE_LOCAL_TIME_REQUEST):
                self.time_reply_sent.set()

    def _handle_device_command(self, code, frame):
        if code != TuyaBleCommandCode.RECEIVE_TIME_DATA_POINT:
            return super()._handle_device_command(code, frame)
        task = asyncio.create_task(self._persist_and_ack(frame))
        self.record_tasks.add(task)
        task.add_done_callback(self.record_tasks.discard)

    async def _persist_and_ack(self, frame):
        try:
            timestamp, position = parse_timestamp(frame.data, 0)
            points = parse_data_points(frame.data, position, timestamp)
            for point in points:
                if point.identifier == 12:
                    await self.history.async_record(point.value, point.timestamp)
            response = Frame(sequence_number=self._next_sequence_number(), response_to=frame.sequence_number, code=frame.code, data=b"\x00")
            await self._async_reply(response)
        except Exception:
            _LOGGER.warning("Could not save a timestamped lock record; acknowledgment withheld")

PREPARE_SECONDS = 60

class ConnectionManager:
    """Serialize operations and retain a prepared connection for a short time."""
    def __init__(self, hass, entry, history):
        self.history = history
        self.hass = hass
        self.entry = entry
        self.lock = asyncio.Lock()
        self.client = None
        self.expiry_task = None
        self.stage = "connecting"

    async def _disconnect(self):
        client, self.client = self.client, None
        if client is not None:
            if client.record_tasks:
                await asyncio.gather(*tuple(client.record_tasks), return_exceptions=True)
            await client._async_disconnect()

    def _connected(self):
        return bool(self.client and self.client._client and self.client._client.is_connected)

    async def _connect(self):
        if self._connected():
            return
        await self._disconnect()
        data = self.entry.data
        device = bluetooth.async_ble_device_from_address(self.hass, data[CONF_ADDRESS], connectable=True)
        if device is None:
            raise HomeAssistantError("No connectable Bluetooth adapter can currently see the lock.")
        creds = TuyaBleCredentials(uuid=data[CONF_UUID], device_id=data[CONF_DEVICE_ID], local_key=data[CONF_LOCAL_KEY])
        self.client = SerializedClient(device, creds, self.history)
        self.stage = "connecting"
        await self.client._async_connect()
        self.stage = "authenticating"
        await self.client._async_handshake()
        if self.client._protocol_version != 2:
            raise HomeAssistantError("The lock reported an unexpected Bluetooth protocol version.")
        try:
            await asyncio.wait_for(self.client.time_reply_sent.wait(), timeout=1)
        except asyncio.TimeoutError:
            await asyncio.sleep(2)

    def _cancel_expiry(self):
        if self.expiry_task is not None:
            self.expiry_task.cancel()
            self.expiry_task = None

    async def _expire(self):
        try:
            await asyncio.sleep(PREPARE_SECONDS)
            async with self.lock:
                self.expiry_task = None
                await self._disconnect()
        except asyncio.CancelledError:
            raise

    async def async_prepare(self):
        async with self.lock:
            self._cancel_expiry()
            try:
                async with asyncio.timeout(60):
                    await self._connect()
                self.expiry_task = asyncio.create_task(self._expire())
            except BaseException:
                await self._disconnect()
                raise

    async def async_unlock(self, entity_id):
        async with self.lock:
            prepared = self.expiry_task is not None and self._connected()
            try:
                async with asyncio.timeout(60):
                    await self._connect()
                    self.stage = "awaiting acknowledgment"
                    request = bytes((6, 0, 2, 1, self.entry.data[CONF_MEMBER_ID]))
                    reply = await self.client._async_request(TuyaBleCommandCode.SENDER_DATA_POINTS, request)
                    if reply.code != TuyaBleCommandCode.SENDER_DATA_POINTS or reply.data != b"\x00":
                        raise HomeAssistantError("The lock did not acknowledge the unlock request as successful.")
                    self.stage = "publishing acknowledgment"
                    await asyncio.sleep(1)
                    self.hass.bus.async_fire("fido_ble_unlock_acknowledged", {"entity_id": entity_id})
            except HomeAssistantError:
                self._cancel_expiry()
                await self._disconnect()
                raise
            except Exception as exc:
                self._cancel_expiry()
                await self._disconnect()
                raise HomeAssistantError(f"Could not confirm the unlock request ({self.stage}: {type(exc).__name__}). Check the lock before retrying.") from None
            finally:
                if not prepared:
                    self._cancel_expiry()
                    await self._disconnect()

    async def async_close(self):
        async with self.lock:
            self._cancel_expiry()
            await self._disconnect()

async def async_setup_entry(hass, entry, async_add_entities):
    manager = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FidoUnlockButton(hass, entry, manager), FidoPrepareButton(hass, entry, manager)])

class FidoButton(ButtonEntity):
    _attr_has_entity_name = True
    def __init__(self, hass, entry, manager):
        self.hass = hass
        self._entry = entry
        self._manager = manager
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.unique_id)}, "name": "FIDO R2", "manufacturer": "YouJia", "model": "YSR3711"}

class FidoUnlockButton(FidoButton):
    _attr_name = "Unlock"
    _attr_icon = "mdi:lock-open-variant"
    def __init__(self, hass, entry, manager):
        super().__init__(hass, entry, manager)
        self._attr_unique_id = f"{entry.unique_id}_unlock"
    async def async_press(self):
        await self._manager.async_unlock(self.entity_id)

class FidoPrepareButton(FidoButton):
    _attr_name = "Prepare connection"
    _attr_icon = "mdi:bluetooth-connect"
    def __init__(self, hass, entry, manager):
        super().__init__(hass, entry, manager)
        self._attr_unique_id = f"{entry.unique_id}_prepare_connection"
    async def async_press(self):
        try:
            await self._manager.async_prepare()
        except HomeAssistantError:
            raise
        except Exception as exc:
            raise HomeAssistantError(f"Could not prepare the lock connection ({self._manager.stage}: {type(exc).__name__}).") from None
