from .base import Driver
from .modbus import ModbusDriver
from .opcua import OpcUaDriver

# protocol name in the YAML -> driver class. A new protocol = one new entry.
REGISTRY: dict[str, type[Driver]] = {
    "opcua": OpcUaDriver,
    "modbus": ModbusDriver,
}

__all__ = ["Driver", "REGISTRY", "OpcUaDriver", "ModbusDriver"]
