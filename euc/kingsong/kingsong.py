import asyncio
import dbussy
import logging
import struct

from euc.base import EUCBase

logger = logging.getLogger(__name__)


class KS(EUCBase):
    KINGSONG_READ_CHARACTER_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
    KS_INIT_MAGIC = [170, 85, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 155, 20, 90, 90]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initialized = False

    async def run(self):
        try_nr = 0
        while True:
            if not self.is_connected or not self.is_initialized:
                try:
                    await self.ble_connect()
                    try_nr = 0
                    continue
                except (dbussy.DBusError, asyncio.TimeoutError) as e:
                    logger.debug(e)
                t = 5 * 2 ** min(4, try_nr)
                logger.debug("reconnect in %s seconds", t)
                await asyncio.sleep(t)
                try_nr += 1
            else:
                await asyncio.sleep(5)

    def update_ks_properties(self, value):
        if len(value) > 16:
            if value[16] == 169:
                result = struct.unpack("=HHHHHhHB", value[:15])
                tot_dist = result[3] << 16 | result[4]
                self.update_properties(
                    dict(
                        voltage=result[1] / 100.0,
                        speed=result[2] / 100.0,
                        total_distance=tot_dist,
                        current=result[5],
                        temperature=result[6] / 100.0,
                        mode=result[7],
                    )
                )
            elif value[16] == 185:
                result = struct.unpack("=HHHHHBBBBH", value[:16])
                dist = result[1] << 16 | result[2]
                self.update_properties(
                    dict(
                        distance=dist,
                        top_speed=result[4] / 100.0,
                        light_status=result[5],
                        voice_status=result[6],
                        fan_status=result[7],
                        temperature2=result[9],
                    )
                )

    async def ble_connect(self):
        logger.debug("connecting...")
        await self.connect()
        logger.debug("connected, initializing...")
        ks_char_itf, obj = await self.get_characteristic_itf_by_uuid(
            self.KINGSONG_READ_CHARACTER_UUID
        )
        await ks_char_itf.WriteValue(self.KS_INIT_MAGIC, {})
        await ks_char_itf.StartNotify()
        self.is_initialized = True
        logger.debug("initialized")

    def on_properties_changed(
        self, object_path, itf_name, changed_props, invalidated_props
    ):
        if itf_name == "org.bluez.GattCharacteristic1" and "Value" in changed_props:
            self.update_ks_properties(bytes(changed_props["Value"][1]))
