import asyncio
import dbussy
import ravel
from pprint import pprint
import time
import logging
import struct
import argparse

from euc.base import EUCBase

logger = logging.getLogger(__name__)

KINGSONG_READ_CHARACTER_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
KS_INIT_MAGIC = [170, 85, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 155, 20, 90, 90]


def is_ks(gatt_char_dict):
    return "org.bluez.GattCharacteristic1" in gatt_char_dict and (
        gatt_char_dict["org.bluez.GattCharacteristic1"]["UUID"][1]
        == KINGSONG_READ_CHARACTER_UUID
    )


class KS(EUCBase):
    def __init__(self, system_bus, device_path, device_info):
        super().__init__(system_bus, device_path, device_info)

        self._init_t = 0.0
        self.is_connected = False
        self._tasks = []


    async def run(self):
        self.system_bus.listen_objects_added(self.objects_added)
        self.system_bus.listen_objects_removed(self.objects_removed)
        self.system_bus.listen_propchanged(
            path="/", fallback=True, interface=None, func=self.obj_prop_changed
        )

        while True:
            if not self.is_connected:
                try:
                    await self.ble_connect()
                except dbussy.DBusError as e:
                    logger.error(e)
            await asyncio.sleep(10)

    def update_ks_properties(self, value):
        if len(value) > 16:
            if value[16] == 169:
                (
                    header,
                    voltage,
                    speed,
                    tot_dist_hi,
                    tot_dist_lo,
                    current,
                    temp,
                    mode,
                ) = struct.unpack("=HHHHHhHB", value[:15])
                tot_dist = tot_dist_hi << 16 | tot_dist_lo
                self.update_properties(
                    dict(
                        voltage=voltage / 100.0,
                        speed=speed / 100.0,
                        total_distance=tot_dist,
                        current=current,
                        temperature=temp / 100.0,
                        mode=mode,
                    )
                )
            elif value[16] == 185:
                (
                    header,
                    dist_hi,
                    dist_lo,
                    _,
                    top_speed,
                    light_status,
                    voice_status,
                    fan_status,
                    _,
                    temp2,
                ) = struct.unpack("=HHHHHBBBBH", value[:16])
                dist = dist_hi << 16 | dist_lo
                self.update_properties(
                    dict(
                        distance=dist,
                        top_speed=top_speed / 100.0,
                        light_status=light_status,
                        voice_status=voice_status,
                        fan_status=fan_status,
                        temperature2=temp2,
                    )
                )

    async def ks_init(self, char_path):
        t = time.time()
        if t - self._init_t >= 2.0:
            self._init_t = t
            logger.info("initializing...")
            char_itf = await self.bluez[char_path].get_async_interface(
                "org.bluez.GattCharacteristic1"
            )
            await char_itf.WriteValue(KS_INIT_MAGIC, {})
            await char_itf.StartNotify()
            logger.info("initialized")
        else:
            logger.info("just initialized %.1f seconds ago, skipping", t - self._init_t)

    async def ble_connect(self):
        logger.info("connecting...")
        device_itf = await self.bluez[self.device_path].get_async_interface(
            "org.bluez.Device1"
        )
        await device_itf.Connect()
        self.is_connected = await device_itf.Connected
        logger.info("connected")
        itf = await self.bluez["/"].get_async_interface(
            "org.freedesktop.DBus.ObjectManager"
        )
        managed_objects = await itf.GetManagedObjects()
        char_path = next((k for k, v in managed_objects[0].items() if is_ks(v)), None)
        logger.info("characteristic path: %s", char_path)
        if char_path:
            await self.ks_init(char_path)

    @ravel.signal(
        name="prop_changed",
        path_keyword="object_path",
        in_signature="sa{sv}as",
        arg_keys=("interface_name", "changed_properties", "invalidated_properties"),
    )
    def obj_prop_changed(
        self, object_path, interface_name, changed_properties, invalidated_properties
    ):
        if object_path.startswith(self.device_path):
            if (
                interface_name == "org.bluez.GattCharacteristic1"
                and "Value" in changed_properties
            ):
                self.update_ks_properties(bytes(changed_properties["Value"][1]))
            else:
                if (
                    object_path == self.device_path
                    and "Connected" in changed_properties
                ):
                    self.is_connected = changed_properties["Connected"][1]
                logger.info(
                    "prop changed on %s %s %r %r",
                    interface_name,
                    object_path,
                    changed_properties,
                    invalidated_properties,
                )

    @ravel.signal(
        name="objects_removed",
        in_signature="oas",
        path_keyword="object_path",
        args_keyword="args",
    )
    def objects_removed(self, object_path, args):
        if not args[0].startswith(self.device_path):
            return
        logger.info("signal received: object “%s” removed: %s", object_path, repr(args))

    @ravel.signal(
        name="object_added",
        in_signature="oa{sa{sv}}",
        path_keyword="object_path",
        args_keyword="args",
    )
    def objects_added(self, object_path, args):
        if not args[0].startswith(self.device_path):
            return
        logger.info("signal received: object “%s” added: %s", object_path, repr(args))
        if False and args[0] == self.device_path:
            self._create_task(self.ble_connect())
        elif args[0].startswith(self.device_path):
            gatt_char_props = args[1].get("org.bluez.GattCharacteristic1")
            if gatt_char_props:
                uuid = gatt_char_props.get("UUID")
                if uuid and uuid[1] == KINGSONG_READ_CHARACTER_UUID:
                    self._create_task(self.ks_init(args[0]))

    def _cleanup_task(self, task):
        logger.info("cleanup task %r", task)
        self._tasks.remove(task)

    def _create_task(self, coro):
        task = asyncio.get_event_loop().create_task(coro)
        logger.info("created task %r", task)
        self._tasks.append(task)
        task.add_done_callback(self._cleanup_task)
