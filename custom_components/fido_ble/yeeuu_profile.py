"""YEEUU Android application profile for one-time account setup."""
from tuya_mobile import TuyaMobileAppProfile

def yeeuu_profile():
    return TuyaMobileAppProfile(
        name="YEEUU",
        app_id='xw5dr7errrc4yw3xcf4d',
        app_secret='farnwyf9fh4yjgjy7gma9tx4jddnkkcr',
        cert_sha256_hex='4C8B193E37D539D3367CF29D51BDB50CF8C36BD31CBEF43FCDD0A89AF04E072E',
        app_key='yacspgqfgnx7dpgmk8jskdnrcjc98p9r',
        package='com.ujia.smart',
        app_version='1.0.7',
        ttid='sdk_international@' + 'xw5dr7errrc4yw3xcf4d',
        sdk_version='5.24.0',
        device_core_version='5.17.0',
    )
