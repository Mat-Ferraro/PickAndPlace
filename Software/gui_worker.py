from gui_common import *
from gui_common import _btn, _group, _label, _style_btn

import base64

CHUNK_SIZE = 128   # raw bytes per chunk; fits in 256-byte line with base64 + JSON overhead

# A load_program whose compact-JSON payload exceeds this many bytes is sent via
# the begin/chunk/end transfer sequence instead of a single line. Kept below the
# 256-byte read window so a direct load_program always fits in one framed line.
MAX_DIRECT_PAYLOAD_BYTES = 200


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
                    msg = self._send_queue.get_nowait()
                    if msg.get("cmd") == "load_program":
                        program = msg.pop("program", {})
                        payload = json.dumps(program, separators=(",", ":")).encode()
                        if self._should_chunk(payload):
                            # Chunked path — synchronous inside worker thread
                            self._run_chunked_transfer(
                                payload, msg["id"], program.get("name", ""))
                            continue
                        else:
                            msg["program"] = program   # small enough for one line
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

    # -----------------------------------------------------------------------
    # Chunked transfer (runs synchronously inside the worker thread)
    # -----------------------------------------------------------------------

    def _should_chunk(self, payload: bytes) -> bool:
        """True if a load_program payload is too large to send on a single line
        and must use the begin/chunk/end transfer sequence instead."""
        return len(payload) > MAX_DIRECT_PAYLOAD_BYTES

    def _write_raw(self, msg_dict: dict) -> None:
        line = json.dumps(msg_dict, separators=(",", ":")) + "\n"
        self._port.write(line.encode())
        self.raw_tx.emit(line.rstrip())

    def _read_until_ack(self, expected_cmd: str, timeout_s: float = 5.0) -> dict:
        """Read messages until we get an ACK/NACK for expected_cmd.
        All other messages (status broadcasts, etc.) are forwarded normally.
        """
        deadline = time.monotonic() + timeout_s
        buf = b""
        while time.monotonic() < deadline:
            try:
                data = self._port.read(256)
            except Exception as exc:
                raise RuntimeError(f"Read error during transfer: {exc}")
            if data:
                buf += data
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                    self.raw_rx.emit(raw.decode())
                    if msg.get("cmd") == expected_cmd and msg.get("type") in ("ack", "nack"):
                        return msg
                    else:
                        self.message_received.emit(msg)
                except Exception:
                    pass
        raise TimeoutError(f"No ACK for \'{expected_cmd}\' within {timeout_s}s")

    def _run_chunked_transfer(self, payload: bytes, cmd_id: int, program_name: str) -> None:
        """Send a load_program payload as a begin/chunk.../end sequence.
        Emits a synthetic load_program ACK or NACK at the end.
        MainWindow sees only the final result — identical to a direct load_program ACK.
        """
        chunks = [payload[i:i + CHUNK_SIZE] for i in range(0, len(payload), CHUNK_SIZE)]
        n = len(chunks)

        try:
            # Step 1: begin_transfer
            self._write_raw({"type": "cmd", "id": cmd_id, "cmd": "begin_transfer",
                              "name": program_name, "size": len(payload), "chunks": n})
            ack = self._read_until_ack("begin_transfer")
            if ack.get("type") == "nack":
                raise RuntimeError(ack.get("reason", "begin_transfer rejected"))

            # Step 2: send each chunk and wait for per-chunk ACK
            for i, chunk_bytes in enumerate(chunks):
                encoded = base64.b64encode(chunk_bytes).decode()
                self._write_raw({"type": "cmd", "id": cmd_id, "cmd": "program_chunk",
                                  "index": i, "data": encoded})
                ack = self._read_until_ack("program_chunk")
                if ack.get("type") == "nack":
                    raise RuntimeError(f"chunk {i} rejected: {ack.get('reason', '?')}")

            # Step 3: end_transfer — simulator responds with a load_program ACK
            self._write_raw({"type": "cmd", "id": cmd_id, "cmd": "end_transfer"})
            result = self._read_until_ack("load_program", timeout_s=10.0)

        except Exception as exc:
            result = {"type": "nack", "id": cmd_id, "cmd": "load_program",
                      "reason": str(exc)}

        self.message_received.emit(result)
