#!/usr/bin/env python3
"""Pi dashboard entrypoint. Collectors require explicit activation."""
import argparse
import fcntl
import json
import logging
from pathlib import Path
import signal
import sys
import threading
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "router"))
import monitor
from tuya_cached import CachedTuya
from juwai_local import CachedLocal
from ewelink import Ewelink
from auth import Auth
from storage import Storage

def load_config(path):
    config = json.loads(Path(path).read_text())
    for key in ("meter_enabled", "ewelink_enabled"):
        if type(config.get(key, False)) is not bool:
            raise ValueError(key + " must be a boolean")
    if config.get("meter_enabled", False):
        host = config.get("meter_host")
        if not isinstance(host, str) or not host.strip():
            raise ValueError("meter_host required before enabling collection")
    if any(type(p) is not int or not 1 <= p <= 65535 for p in
           (config.get("port", 8096), config.get("meter_port", 8838))):
        raise ValueError("Invalid TCP port")
    bonds = config.get("bonds_port")
    if bonds is not None and (type(bonds) is not int or not 1 <= bonds <= 65535 or bonds == config.get("port", 8096)):
        raise ValueError("Invalid bonds_port")
    if config.get("juwai_source", "cloud") not in ("cloud", "local"):
        raise ValueError("juwai_source must be cloud or local")
    if config.get("bind", "127.0.0.1") != "127.0.0.1":
        raise ValueError("Use loopback and an SSH tunnel until public handover")
    return config

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/modpi/monitor.json")
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.check_config:
        print("Configuration valid; meter=%s eWeLink=%s" %
              (config.get("meter_enabled", False), config.get("ewelink_enabled", False)))
        return
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    storage = Storage()
    workers, threads = [], []
    with open("/var/lib/modpi/private/process.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with storage.directory() as directory:
            store = monitor.Store("/proc/self/fd/" + str(directory) + "/monitor.sqlite3", storage.check)
            voltage = None
            if config.get("meter_enabled", False):
                workers.append(monitor.Monitor(store, config["meter_host"], config.get("meter_port", 8838)))
                voltage = monitor.VoltageReader(config["meter_host"], config.get("meter_port", 8838))
            tuya = CachedTuya(storage.directory, storage.check)
            ewelink = Ewelink(storage.directory, storage.check,
                              config="/var/lib/modpi/private/ewelink.json")
            if config.get("ewelink_enabled", False):
                workers.append(ewelink)
            server = monitor.WebServer(("127.0.0.1", config.get("port", 8096)),
                monitor.handler(store, Path(monitor.__file__).parent / "web", voltage, tuya, ewelink,
                    Auth("/var/lib/modpi/private/auth.json", "/var/lib/modpi/private/auth.sqlite3"),
                    config.get("bonds_port"),
                    juwai_local=CachedLocal(storage.directory, storage.check),
                    juwai_source=config.get("juwai_source", "cloud")))
            def stop(*_):
                for worker in workers:
                    worker.stop.set()
                threading.Thread(target=server.shutdown, daemon=True).start()
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            try:
                for worker in workers:
                    thread = threading.Thread(target=worker.poll, daemon=True)
                    threads.append(thread)
                    thread.start()
                logging.info("ModPi ready; meter=%s eWeLink=%s",
                    config.get("meter_enabled", False), config.get("ewelink_enabled", False))
                server.serve_forever(poll_interval=.5)
            finally:
                for worker in workers:
                    worker.stop.set()
                for thread in threads:
                    thread.join(timeout=10)
                server.server_close()
                store.db.close()

if __name__ == "__main__":
    main()
