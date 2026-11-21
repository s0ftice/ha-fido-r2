"""Manual setup for the previously provisioned FIDO lock."""
from __future__ import annotations
import re
import json
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .account_devices import async_supported_devices
from .const import DOMAIN, CONF_ADDRESS, CONF_DEVICE_ID, CONF_UUID, CONF_LOCAL_KEY, CONF_MEMBER_ID, CONF_FINGERPRINT_NAMES

MAC = re.compile(r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
PASSWORD = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))
MEMBER_NUMBER = selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=100, step=1, mode=selector.NumberSelectorMode.BOX))
SCHEMA = vol.Schema({
    vol.Required(CONF_ADDRESS): str,
    vol.Required(CONF_DEVICE_ID): str,
    vol.Required(CONF_UUID): str,
    vol.Required(CONF_LOCAL_KEY): PASSWORD,
    vol.Required(CONF_MEMBER_ID, default=1): MEMBER_NUMBER,
    vol.Optional(CONF_FINGERPRINT_NAMES, default="{}"): str,
})

def parse_names(raw):
    """Parse an ID-to-name JSON object; reject malformed or ambiguous entries."""
    value = json.loads(raw or '{}')
    if not isinstance(value, dict):
        raise ValueError('not a mapping')
    result = {}
    for slot, name in value.items():
        if not isinstance(slot, str) or not slot.isdecimal() or not 0 <= int(slot) <= 255 or str(int(slot)) in result or not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            raise ValueError('invalid mapping')
        result[str(int(slot))] = name.strip()
    return result

class FidoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            method = user_input['method']
            if method == 'manual':
                return await self.async_step_manual()
            self._app_method = method
            return await self.async_step_account()
        return self.async_show_form(step_id='user', data_schema=vol.Schema({
            vol.Required('method'): selector.SelectSelector(selector.SelectSelectorConfig(
                options=['manual', 'smart_life', 'tuya_smart', 'yeeuu'], translation_key='method')),
        }))

    async def async_step_account(self, user_input=None):
        """Retrieve the key once; never save the app password or session."""
        from tuya_mobile import TuyaMobileApp, TuyaPasswordClient, TuyaMobileError
        errors = {}
        if user_input is not None:
            account = user_input['account'].strip()
            country = user_input['country_code'].strip().lstrip('+')
            if not account or not country.isascii() or not country.isdecimal():
                errors['base'] = 'invalid_account_details'
            else:
                try:
                    session = async_get_clientsession(self.hass)
                    if self._app_method == 'yeeuu':
                        from .yeeuu_profile import yeeuu_profile
                        client = TuyaPasswordClient(yeeuu_profile(), session, username=account, max_login_attempts=1)
                    else:
                        app = TuyaMobileApp.SMART_LIFE if self._app_method == 'smart_life' else TuyaMobileApp.TUYA_SMART
                        client = TuyaPasswordClient.for_application(app, session, username=account, max_login_attempts=1)
                    await client.login_with_password(user_input['password'], country_code=country)
                    self._account_client = client
                    self._account_devices = await async_supported_devices(client)
                    if not self._account_devices:
                        self._account_client = None
                        errors['base'] = 'account_no_locks'
                    elif len(self._account_devices) == 1:
                        return await self._async_select_account_device(next(iter(self._account_devices)))
                    else:
                        return await self.async_step_select_lock()
                except (TuyaMobileError, ValueError) as err:
                    self._account_client = None
                    errors['base'] = 'account_' + {
                        'TuyaMobileInvalidAuth': 'auth',
                        'TuyaMobileMFARequired': 'verification',
                        'TuyaMobileCaptchaRequired': 'verification',
                        'TuyaMobileDeviceNotFound': 'device',
                        'TuyaMobileAccountLocked': 'locked',
                    }.get(type(err).__name__, 'lookup')
        return self.async_show_form(step_id='account', data_schema=vol.Schema({
            vol.Required('account'): str,
            vol.Required('password'): PASSWORD,
            vol.Required('country_code'): str,
        }), errors=errors)

    async def async_step_select_lock(self, user_input=None):
        if user_input is not None and user_input.get('device') in self._account_devices:
            return await self._async_select_account_device(user_input['device'])
        options = [selector.SelectOptionDict(value=device_id, label=f'{name} ({device_id[-4:]})') for device_id, name in self._account_devices.items()]
        return self.async_show_form(step_id='select_lock', data_schema=vol.Schema({vol.Required('device'): selector.SelectSelector(selector.SelectSelectorConfig(options=options))}))

    async def _async_select_account_device(self, device_id):
        from tuya_mobile import TuyaMobileError
        client = self._account_client
        try:
            credentials = await client.get_device_credentials(device_id)
            self._retrieved_key = credentials.local_key
            self._retrieved_device_id = device_id
            self._retrieved_uuid = credentials.uuid if isinstance(credentials.uuid, str) and len(credentials.uuid) == 16 and credentials.uuid.isascii() else None
            try:
                response = await client._mobile_call('thing.m.device.get', '4.1', {'devId': device_id})
            except TuyaMobileError:
                response = {}
            def device_records(value):
                if isinstance(value, dict):
                    if any(str(value.get(k, '')) == device_id for k in ('devId', 'deviceId', 'id')):
                        yield value
                    for item in value.values():
                        yield from device_records(item)
                elif isinstance(value, list):
                    for item in value:
                        yield from device_records(item)
            self._retrieved_address = None
            for record in device_records(response):
                mac = record.get('mac') or record.get('macAddress') or record.get('mac_address')
                if isinstance(mac, str):
                    compact = mac.replace(':', '').replace('-', '')
                    if re.fullmatch(r'[0-9a-fA-F]{12}', compact):
                        self._retrieved_address = ':'.join(reversed([compact[i:i+2].upper() for i in range(0, 12, 2)]))
                        break
            return await self.async_step_manual()
        except TuyaMobileError:
            self._account_client = None
            return self.async_abort(reason='account_lookup_failed')
        finally:
            self._account_client = None

    async def async_step_manual(self, user_input=None):
        errors = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            key = getattr(self, '_retrieved_key', None) or user_input[CONF_LOCAL_KEY].strip()
            device_id = user_input[CONF_DEVICE_ID].strip()
            uuid = user_input[CONF_UUID].strip()
            member = user_input[CONF_MEMBER_ID]
            try:
                names = parse_names(user_input.get(CONF_FINGERPRINT_NAMES, '{}'))
            except (ValueError, TypeError):
                errors['base'] = 'invalid_fingerprint_names'
                names = {}
            if errors:
                pass
            elif getattr(self, '_retrieved_key', None) and device_id != self._retrieved_device_id:
                errors['base'] = 'device_id_changed'
            elif isinstance(member, bool) or not isinstance(member, (int, float)) or not 1 <= member <= 100 or member != int(member):
                errors["base"] = "invalid_member"
            elif not MAC.fullmatch(address):
                errors["base"] = "invalid_address"
            elif len(key) != 16 or len(device_id) != 16 or len(uuid) != 16 or not (key.isascii() and device_id.isascii() and uuid.isascii()):
                errors["base"] = "invalid_credentials"
            else:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                data = dict(user_input, address=address, local_key=key, device_id=device_id, device_uuid=uuid, member_id=int(member), fingerprint_names=names)
                return self.async_create_entry(title="FIDO BLE Unlock", data=data)
        schema = SCHEMA
        if getattr(self, '_retrieved_key', None):
            schema = vol.Schema({
                vol.Required(CONF_ADDRESS, default=self._retrieved_address or ''): str,
                vol.Required(CONF_DEVICE_ID, default=self._retrieved_device_id): str,
                vol.Required(CONF_UUID, default=self._retrieved_uuid or ''): str,
                vol.Required(CONF_MEMBER_ID, default=1): MEMBER_NUMBER,
                vol.Optional(CONF_FINGERPRINT_NAMES, default='{}'): str,
            })
        return self.async_show_form(step_id="manual", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input=None):
        """Update connection settings without disclosing the existing key."""
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            device_id = user_input[CONF_DEVICE_ID].strip()
            uuid = user_input[CONF_UUID].strip()
            new_key = user_input.get(CONF_LOCAL_KEY, "").strip()
            key = new_key or entry.data[CONF_LOCAL_KEY]
            member = user_input[CONF_MEMBER_ID]
            try:
                names = parse_names(user_input.get(CONF_FINGERPRINT_NAMES, '{}'))
            except (ValueError, TypeError):
                errors['base'] = 'invalid_fingerprint_names'
                names = {}
            if errors:
                pass
            elif isinstance(member, bool) or not isinstance(member, (int, float)) or not 1 <= member <= 100 or member != int(member):
                errors["base"] = "invalid_member"
            elif not MAC.fullmatch(address):
                errors["base"] = "invalid_address"
            elif any(len(value) != 16 or not value.isascii() for value in (key, device_id, uuid)):
                errors["base"] = "invalid_credentials"
            elif any(other.entry_id != entry.entry_id and other.data.get(CONF_ADDRESS, "").upper() == address for other in self._async_current_entries()):
                errors["base"] = "address_in_use"
            else:
                updates = {
                    CONF_ADDRESS: address,
                    CONF_DEVICE_ID: device_id, CONF_UUID: uuid,
                    CONF_LOCAL_KEY: key, CONF_MEMBER_ID: int(member), CONF_FINGERPRINT_NAMES: names,
                }
                return self.async_update_reload_and_abort(entry, data_updates=updates)
        values = user_input or entry.data
        schema = vol.Schema({
            vol.Required(CONF_ADDRESS, default=values[CONF_ADDRESS]): str,
            vol.Required(CONF_DEVICE_ID, default=values[CONF_DEVICE_ID]): str,
            vol.Required(CONF_UUID, default=values[CONF_UUID]): str,
            vol.Optional(CONF_LOCAL_KEY): PASSWORD,
            vol.Required(CONF_MEMBER_ID, default=values[CONF_MEMBER_ID]): MEMBER_NUMBER,
            vol.Optional(CONF_FINGERPRINT_NAMES, default=(values.get(CONF_FINGERPRINT_NAMES, '{}') if isinstance(values.get(CONF_FINGERPRINT_NAMES, '{}'), str) else json.dumps(values.get(CONF_FINGERPRINT_NAMES, {}), ensure_ascii=False))): str,
        })
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)
