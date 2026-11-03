#!/usr/bin/env python3
"""Pi Tuya entrypoint: the router collector, guarded by Pi microSD storage instead of the USB stick."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "router"))
import tuya_collector
from storage import Storage

if __name__ == "__main__":
    storage = Storage()
    tuya_collector.main(storage.directory, storage.check)
