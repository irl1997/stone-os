"""Error states shared by stoned and the UI (audit section 13).

On D-Bus they travel as org.stonemachine.Error.<Code>."""

UNSUPPORTED = "Unsupported"
PERMISSION_DENIED = "PermissionDenied"
INVALID_VALUE = "InvalidValue"
HARDWARE_ERROR = "HardwareError"
SERVICE_UNAVAILABLE = "ServiceUnavailable"
THERMAL_LIMIT = "ThermalLimit"
APPLY_FAILED = "ApplyFailed"
ROLLBACK_COMPLETE = "RollbackComplete"
ROLLBACK_FAILED = "RollbackFailed"
SAFE_MODE = "SafeMode"
BUSY = "Busy"
NOT_VALIDATED = "NotValidated"

PREFIX = "org.stonemachine.Error."


class StoneError(Exception):
    def __init__(self, code, message=""):
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def dbus_name(self):
        return PREFIX + self.code
