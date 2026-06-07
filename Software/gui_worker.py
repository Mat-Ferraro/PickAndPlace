from gui_common import *
from gui_common import _btn, _group, _label, _style_btn

class SerialWorker(QThread):
    message_received   = pyqtSignal(dict)
    connection_changed = pyqtSignal(bool)
    error_occurred     = pyqtSignal(str)
    raw_tx             = pyqtSignal(str)
    raw_rx             = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._url        = ""
        self._port       = None
        self._send_queue: queue.Queue = queue.Queue()
        self._running    = False
        self._next_id    = 1

    def connect_to(self, url: str):
        self._url     = url
        self._running = True
        self.start()

    def disconnect(self):
        self._running = False

    def send(self, cmd: dict) -> int:
        cmd_id        = self._next_id
        self._next_id += 1
        msg = {"type": "cmd", "id": cmd_id, **cmd}
        self._send_queue.put(msg)
        return cmd_id

    def run(self):
        try:
            self._port = serial.serial_for_url(
                self._url, baudrate=115200, timeout=0.02)
        except Exception as exc:
            self.error_occurred.emit(f"Cannot open {self._url}: {exc}")
            return

        self.connection_changed.emit(True)
        buf = b""

        while self._running:
            while not self._send_queue.empty():
                try:
                    msg  = self._send_queue.get_nowait()
                    line = json.dumps(msg, separators=(",", ":")) + "\n"
                    self._port.write(line.encode())
                    self.raw_tx.emit(line.rstrip())
                except Exception as exc:
                    self.error_occurred.emit(f"Send error: {exc}")
                    self._running = False
                    break

            try:
                chunk = self._port.read(256)
            except Exception as exc:
                self.error_occurred.emit(f"Read error: {exc}")
                break

            if chunk:
                buf += chunk

            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    self.raw_rx.emit(line.decode())
                    self.message_received.emit(msg)
                except Exception:
                    pass

        self._port.close()
        self._port    = None
        self._running = False
        self.connection_changed.emit(False)


# ---------------------------------------------------------------------------
# Run tab
# ---------------------------------------------------------------------------

