"""
Unit tests for gui_worker.py — SerialWorker framing logic.

The framing logic (_write_raw, _read_until_ack, _run_chunked_transfer) is pure
byte/JSON plumbing: it depends only on a serial port that can read()/write() and
on a handful of Qt signals it can .emit(). None of it needs a GUI, an event
loop, or real hardware.

To test it hermetically — on any machine, in CI, with no PyQt6/pyserial
installed — we install lightweight stand-ins for PyQt6.* and serial into
sys.modules BEFORE importing gui_worker. The REAL SerialWorker code then runs;
only its Qt base class and signal mechanism are faked. The signal fakes record
every emission so tests can assert on what the worker forwarded to the GUI.

This mirrors the FakeMachine approach used for the interpreter: the test double
documents exactly what the production code requires from its environment.

Run from the Software/ directory:  pytest -q
"""

import json
import sys
import types

import pytest


# ---------------------------------------------------------------------------
# Stub out PyQt6 + serial so the real SerialWorker can import and run.
# ---------------------------------------------------------------------------

class _DummyMeta(type):
    """Metaclass so dummy classes support arbitrary attribute chains
    (e.g. Qt.AlignmentFlag.AlignCenter) referenced in widget class bodies."""
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _make_dummy(name)


class _Dummy(metaclass=_DummyMeta):
    """Permissive stand-in: constructible, callable, any-attribute, subclassable.
    Used for every faked Qt symbol except pyqtSignal."""
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Dummy()

    def __call__(self, *a, **k):
        return _Dummy()


def _make_dummy(name):
    return _DummyMeta(name, (_Dummy,), {})


class _FakeSignal:
    """Records emissions and forwards to any connected callables."""
    def __init__(self):
        self.emissions = []
        self._slots = []

    def connect(self, fn):
        self._slots.append(fn)

    def emit(self, *args):
        self.emissions.append(args[0] if len(args) == 1 else args)
        for fn in self._slots:
            fn(*args)


class _SignalDescriptor:
    """Per-instance signal, like pyqtSignal: worker.sig is its own _FakeSignal."""
    def __set_name__(self, owner, name):
        self._name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        store = obj.__dict__.setdefault("_fake_signals", {})
        if self._name not in store:
            store[self._name] = _FakeSignal()
        return store[self._name]


def _pyqtSignal(*args, **kwargs):
    return _SignalDescriptor()


def _install_fake_modules():
    """Register fake PyQt6.* and serial modules in sys.modules."""
    def fake_module(name, **attrs):
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        # Anything not explicitly set resolves to a permissive dummy class.
        mod.__getattr__ = lambda n: (_ for _ in ()).throw(AttributeError(n)) \
            if n.startswith("__") else _make_dummy(n)
        return mod

    pyqt6 = types.ModuleType("PyQt6")
    qtcore = fake_module("PyQt6.QtCore", pyqtSignal=_pyqtSignal)
    qtgui = fake_module("PyQt6.QtGui")
    qtwidgets = fake_module("PyQt6.QtWidgets")
    pyqt6.QtCore, pyqt6.QtGui, pyqt6.QtWidgets = qtcore, qtgui, qtwidgets

    sys.modules.setdefault("PyQt6", pyqt6)
    sys.modules.setdefault("PyQt6.QtCore", qtcore)
    sys.modules.setdefault("PyQt6.QtGui", qtgui)
    sys.modules.setdefault("PyQt6.QtWidgets", qtwidgets)
    sys.modules.setdefault("serial", fake_module("serial"))


_install_fake_modules()

import gui_worker            # noqa: E402  (must follow the stub install)
from gui_worker import (     # noqa: E402
    SerialWorker, CHUNK_SIZE, MAX_DIRECT_PAYLOAD_BYTES,
)


# ---------------------------------------------------------------------------
# Fake serial port
# ---------------------------------------------------------------------------

class FakePort:
    """
    A scriptable stand-in for a pyserial port.

    Two ways to supply inbound data:
      * feed(*dicts) / feed_raw(*lines): preload bytes the worker will read.
      * responder=callable(written_msg) -> list[dict]: react to each write by
        queueing responses, the way the real simulator would.

    read_size caps bytes returned per read() so tests can force a single JSON
    line to arrive split across multiple reads (exercises the reassembly buffer).

    Recording:
      port.tx       -> list of parsed dicts written by the worker
      port.tx_lines -> list of raw written lines (without trailing newline)
    """
    def __init__(self, responder=None, read_size=4096):
        self._rx = b""
        self.tx = []
        self.tx_lines = []
        self._responder = responder
        self._read_size = read_size

    def feed(self, *msgs):
        for m in msgs:
            self._rx += (json.dumps(m) + "\n").encode()
        return self

    def feed_raw(self, *lines):
        for ln in lines:
            self._rx += (ln + "\n").encode()
        return self

    def write(self, data):
        line = data.decode().strip()
        self.tx_lines.append(line)
        try:
            msg = json.loads(line)
        except Exception:
            msg = None
        if msg is not None:
            self.tx.append(msg)
        if self._responder and msg is not None:
            for resp in self._responder(msg):
                self._rx += (json.dumps(resp) + "\n").encode()

    def read(self, n):
        if not self._rx:
            return b""
        k = min(n, self._read_size, len(self._rx))
        out, self._rx = self._rx[:k], self._rx[k:]
        return out


def make_worker(port):
    w = SerialWorker()
    w._port = port
    return w


def happy_responder(msg):
    """Simulator-like: ack every transfer step, ack load_program on end."""
    cmd, cid = msg.get("cmd"), msg.get("id")
    if cmd == "begin_transfer":
        return [{"type": "ack", "id": cid, "cmd": "begin_transfer"}]
    if cmd == "program_chunk":
        return [{"type": "ack", "id": cid, "cmd": "program_chunk",
                 "index": msg.get("index")}]
    if cmd == "end_transfer":
        return [{"type": "ack", "id": cid, "cmd": "load_program",
                 "instructions": 1, "bytes": 42}]
    return []


# ===========================================================================
# _write_raw
# ===========================================================================

class TestWriteRaw:

    def test_writes_compact_json_with_newline(self):
        port = FakePort()
        w = make_worker(port)
        w._write_raw({"type": "cmd", "id": 1, "cmd": "ping"})
        assert port.tx_lines[0].endswith("}")          # stripped on record
        raw = port._rx  # nothing fed back
        assert port.tx == [{"type": "cmd", "id": 1, "cmd": "ping"}]

    def test_emits_raw_tx_without_newline(self):
        port = FakePort()
        w = make_worker(port)
        w._write_raw({"cmd": "ping"})
        assert w.raw_tx.emissions == ['{"cmd":"ping"}']

    def test_uses_compact_separators(self):
        port = FakePort()
        w = make_worker(port)
        w._write_raw({"a": 1, "b": 2})
        assert " " not in port.tx_lines[0]              # no spaces after , or :


# ===========================================================================
# _read_until_ack
# ===========================================================================

class TestReadUntilAck:

    def test_returns_matching_ack(self):
        port = FakePort().feed({"type": "ack", "cmd": "home", "id": 3})
        w = make_worker(port)
        msg = w._read_until_ack("home")
        assert msg == {"type": "ack", "cmd": "home", "id": 3}

    def test_returns_matching_nack(self):
        port = FakePort().feed({"type": "nack", "cmd": "home",
                                "reason": "not_ready"})
        w = make_worker(port)
        msg = w._read_until_ack("home")
        assert msg["type"] == "nack" and msg["reason"] == "not_ready"

    def test_forwards_unrelated_messages_then_returns_ack(self):
        port = FakePort().feed(
            {"type": "status", "state": "READY", "seq": 1},     # broadcast
            {"type": "ack", "cmd": "other", "id": 9},           # different cmd
            {"type": "ack", "cmd": "home", "id": 3},            # the one we want
        )
        w = make_worker(port)
        msg = w._read_until_ack("home")
        assert msg["cmd"] == "home"
        # The two non-matching messages were forwarded to the GUI, the ack was not.
        forwarded = w.message_received.emissions
        assert {"type": "status", "state": "READY", "seq": 1} in forwarded
        assert {"type": "ack", "cmd": "other", "id": 9} in forwarded
        assert {"type": "ack", "cmd": "home", "id": 3} not in forwarded

    def test_does_not_treat_status_as_ack(self):
        # A status that happens to carry cmd:"home" but type:"status" is NOT an ack.
        port = FakePort().feed(
            {"type": "status", "cmd": "home"},
            {"type": "ack", "cmd": "home", "id": 1},
        )
        w = make_worker(port)
        msg = w._read_until_ack("home")
        assert msg["type"] == "ack"

    def test_reassembles_line_split_across_reads(self):
        # read_size=4 forces the JSON line to arrive in small fragments.
        port = FakePort(read_size=4).feed({"type": "ack", "cmd": "home", "id": 5})
        w = make_worker(port)
        msg = w._read_until_ack("home")
        assert msg["id"] == 5

    def test_handles_multiple_messages_in_one_read(self):
        port = FakePort(read_size=4096).feed(
            {"type": "ack", "cmd": "x", "id": 1},
            {"type": "ack", "cmd": "home", "id": 2},
        )
        w = make_worker(port)
        msg = w._read_until_ack("home")
        assert msg["id"] == 2

    def test_skips_blank_and_malformed_lines(self):
        port = FakePort()
        port.feed_raw("", "   ", "{not valid json", "")
        port.feed({"type": "ack", "cmd": "home", "id": 7})
        w = make_worker(port)
        msg = w._read_until_ack("home")
        assert msg["id"] == 7

    def test_emits_raw_rx_for_received_lines(self):
        port = FakePort().feed({"type": "ack", "cmd": "home", "id": 1})
        w = make_worker(port)
        w._read_until_ack("home")
        assert any("home" in s for s in w.raw_rx.emissions)

    def test_times_out_when_ack_never_arrives(self):
        port = FakePort()   # nothing to read, read() always returns b""
        w = make_worker(port)
        with pytest.raises(TimeoutError):
            w._read_until_ack("home", timeout_s=0.05)


# ===========================================================================
# _run_chunked_transfer
# ===========================================================================

class TestRunChunkedTransfer:

    def _small_payload(self):
        return json.dumps({"version": 1, "program": [{"op": "HALT"}]}).encode()

    def test_happy_path_single_chunk_emits_load_program_ack(self):
        payload = self._small_payload()
        port = FakePort(responder=happy_responder)
        w = make_worker(port)
        w._run_chunked_transfer(payload, cmd_id=1, program_name="demo")

        cmds = [m["cmd"] for m in port.tx]
        assert cmds == ["begin_transfer", "program_chunk", "end_transfer"]
        result = w.message_received.emissions[-1]
        assert result["type"] == "ack" and result["cmd"] == "load_program"

    def test_begin_transfer_reports_size_and_chunk_count(self):
        payload = b"x" * (CHUNK_SIZE * 2 + 5)   # -> 3 chunks
        port = FakePort(responder=happy_responder)
        w = make_worker(port)
        w._run_chunked_transfer(payload, cmd_id=1, program_name="big")

        begin = port.tx[0]
        assert begin["cmd"] == "begin_transfer"
        assert begin["size"] == len(payload)
        assert begin["chunks"] == 3
        assert begin["name"] == "big"

    def test_chunks_are_indexed_and_decode_to_payload(self):
        import base64
        payload = bytes(range(256)) * 2          # 512 bytes -> 4 chunks
        port = FakePort(responder=happy_responder)
        w = make_worker(port)
        w._run_chunked_transfer(payload, cmd_id=1, program_name="")

        chunk_msgs = [m for m in port.tx if m["cmd"] == "program_chunk"]
        assert [m["index"] for m in chunk_msgs] == [0, 1, 2, 3]
        rebuilt = b"".join(base64.b64decode(m["data"]) for m in chunk_msgs)
        assert rebuilt == payload

    def test_begin_transfer_nack_aborts_with_synthetic_nack(self):
        def responder(msg):
            if msg["cmd"] == "begin_transfer":
                return [{"type": "nack", "id": msg["id"],
                         "cmd": "begin_transfer", "reason": "not_ready"}]
            return []
        port = FakePort(responder=responder)
        w = make_worker(port)
        w._run_chunked_transfer(self._small_payload(), cmd_id=1,
                                program_name="")

        # No chunks should have been sent after begin was rejected.
        assert [m["cmd"] for m in port.tx] == ["begin_transfer"]
        result = w.message_received.emissions[-1]
        assert result["type"] == "nack" and result["cmd"] == "load_program"
        assert "not_ready" in result["reason"]

    def test_chunk_nack_aborts_with_synthetic_nack(self):
        def responder(msg):
            cid = msg.get("id")
            if msg["cmd"] == "begin_transfer":
                return [{"type": "ack", "id": cid, "cmd": "begin_transfer"}]
            if msg["cmd"] == "program_chunk":
                return [{"type": "nack", "id": cid, "cmd": "program_chunk",
                         "reason": "out_of_order_expected_0"}]
            return []
        port = FakePort(responder=responder)
        w = make_worker(port)
        w._run_chunked_transfer(self._small_payload(), cmd_id=1,
                                program_name="")

        result = w.message_received.emissions[-1]
        assert result["type"] == "nack" and result["cmd"] == "load_program"
        # end_transfer must NOT be sent once a chunk was rejected.
        assert "end_transfer" not in [m["cmd"] for m in port.tx]

    def test_synthetic_result_carries_original_cmd_id(self):
        port = FakePort(responder=happy_responder)
        w = make_worker(port)
        w._run_chunked_transfer(self._small_payload(), cmd_id=99,
                                program_name="")
        # The id is preserved through the begin/chunk/end messages.
        assert all(m["id"] == 99 for m in port.tx)


# ===========================================================================
# _should_chunk — the direct-vs-chunked routing decision
# ===========================================================================

class TestShouldChunk:

    def test_small_payload_is_direct(self):
        w = make_worker(FakePort())
        assert w._should_chunk(b"x" * 10) is False

    def test_large_payload_is_chunked(self):
        w = make_worker(FakePort())
        assert w._should_chunk(b"x" * 1000) is True

    def test_at_threshold_is_direct(self):
        # The boundary is exclusive: exactly the limit still fits on one line.
        w = make_worker(FakePort())
        assert w._should_chunk(b"x" * MAX_DIRECT_PAYLOAD_BYTES) is False

    def test_one_over_threshold_is_chunked(self):
        w = make_worker(FakePort())
        assert w._should_chunk(b"x" * (MAX_DIRECT_PAYLOAD_BYTES + 1)) is True

    def test_empty_payload_is_direct(self):
        w = make_worker(FakePort())
        assert w._should_chunk(b"") is False

    def test_run_routes_large_program_through_chunked_transfer(self):
        # End-to-end: a program whose payload exceeds the threshold must take
        # the begin/chunk/end path; a small one must be sent as a single line.
        big_program = {"version": 1, "name": "big",
                       "program": [{"op": "LOG", "message": "x" * 400}]}
        payload = json.dumps(big_program, separators=(",", ":")).encode()
        assert len(payload) > MAX_DIRECT_PAYLOAD_BYTES   # guard the fixture

        port = FakePort(responder=happy_responder)
        w = make_worker(port)
        w._next_id = 5
        w.send({"cmd": "load_program", "program": big_program})
        # Drive one pass of the send-queue drain the way run() would, without
        # opening a real port or entering the read loop.
        msg = w._send_queue.get_nowait()
        program = msg.pop("program", {})
        payload = json.dumps(program, separators=(",", ":")).encode()
        assert w._should_chunk(payload)
        w._run_chunked_transfer(payload, msg["id"], program.get("name", ""))

        cmds = [m["cmd"] for m in port.tx]
        # A chunked payload (>200 bytes) always spans 2+ CHUNK_SIZE chunks.
        assert cmds[0] == "begin_transfer"
        assert cmds[-1] == "end_transfer"
        assert set(cmds[1:-1]) == {"program_chunk"}
        assert len(cmds) - 2 >= 2
        assert w.message_received.emissions[-1]["cmd"] == "load_program"
