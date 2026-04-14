"""mlrVp - Modern Python reimplementation of the mlrV sampling instrument."""

import os as _os
import platform as _platform

# Enable ASIO support in sounddevice/PortAudio on Windows.
# Must be set before sounddevice is imported anywhere.
if _platform.system() == "Windows":
    _os.environ.setdefault("SD_ENABLE_ASIO", "1")

__version__ = "0.6.9.15"
__app_name__ = "mlrVp"
