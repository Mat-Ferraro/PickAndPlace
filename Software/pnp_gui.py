#!/usr/bin/env python3
"""
Pick-and-Place GUI  v0.2
Requires:  pip install PyQt6 pyserial

Simulator:  socket://localhost:9999/
Hardware:   COM3  (or whatever port Windows assigns)
"""

from gui_common import *
from gui_worker import SerialWorker
from tab_run import RunTab
from tab_program import ProgramEditorTab
from tab_service import ServiceTab, CommsTab
from tab_calibration import CalibrationTab
from tab_events import EventLog

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pick-and-Place Control")
        self.setMinimumSize(860, 640)
        self._worker: Optional[SerialWorker] = None
        self._last_state: str    = ""
        self._sensor_timer = QTimer(self)
        self._sensor_timer.timeout.connect(self._poll_sensors)
        # Paced query queue — connect-time queries are sent one at a time so a
        # burst can't overrun the MCU's 64-byte serial RX buffer.
        self._query_queue: list = []
        self._query_timer = QTimer(self)
        self._query_timer.timeout.connect(self._drain_query_queue)
        # Initial get_param burst is deferred until the MCU is booted (see
        # _on_connection_changed): the Mega auto-resets on serial open and would
        # drop queries sent during its ~2 s bootloader.
        self._pending_initial_queries: list | None = None
        self._build_ui()
        self._refresh_ports()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        # Connection bar
        conn = QHBoxLayout()
        conn.addWidget(QLabel("Port / URL:"))
        self._url_combo = QComboBox()
        self._url_combo.setEditable(True)
        self._url_combo.setMinimumWidth(240)
        conn.addWidget(self._url_combo)
        self._btn_refresh_ports = QPushButton("⟳")
        self._btn_refresh_ports.setFixedWidth(32)
        self._btn_refresh_ports.setToolTip("Rescan serial ports")
        self._btn_refresh_ports.clicked.connect(self._refresh_ports)
        conn.addWidget(self._btn_refresh_ports)
        self._conn_btn = QPushButton("Connect")
        self._conn_btn.setFixedWidth(90)
        self._conn_btn.clicked.connect(self._toggle_connection)
        conn.addWidget(self._conn_btn)
        self._conn_light = StatusLight(12)
        conn.addWidget(self._conn_light)
        self._conn_status = QLabel("Disconnected")
        conn.addWidget(self._conn_status)
        conn.addStretch()
        root.addLayout(conn)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        # Tabs
        self._tabs = QTabWidget()
        self._run_tab   = RunTab()
        self._prog_tab  = ProgramEditorTab()
        self._cal_tab   = CalibrationTab()
        self._svc_tab   = ServiceTab()
        self._comms_tab = CommsTab()
        self._event_log = EventLog()
        self._tabs.addTab(self._run_tab,   "Run")
        self._tabs.addTab(self._prog_tab,  "Program")
        self._tabs.addTab(self._svc_tab,    "Service")
        self._tabs.addTab(self._cal_tab,    "Calibration")
        self._tabs.addTab(self._comms_tab,  "Comms")
        self._tabs.addTab(self._event_log, "Events")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self._tabs)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Not connected")

        self._run_tab.command_requested.connect(self._send)
        self._prog_tab.command_requested.connect(self._send)
        self._cal_tab.command_requested.connect(self._send)
        self._svc_tab.command_requested.connect(self._send)

        self._set_connected(False)

    # ---- Connection ------------------------------------------------------

    def _toggle_connection(self):
        if self._worker and self._worker.isRunning():
            self._sensor_timer.stop()
            self._worker.disconnect()
            self._conn_btn.setText("Connect")
        else:
            url = self._selected_url()
            if not url:
                return
            self._worker = SerialWorker()
            self._worker.message_received.connect(self._on_message)
            self._worker.connection_changed.connect(self._on_connection_changed)
            self._worker.error_occurred.connect(self._on_error)
            self._worker.command_failed.connect(self._on_command_failed)
            self._worker.raw_tx.connect(self._comms_tab.log_tx)
            self._worker.raw_rx.connect(self._comms_tab.log_rx)
            self._worker.connect_to(url)
            self._conn_btn.setText("Disconnect")

    def _refresh_ports(self):
        """Rescan serial ports and repopulate the dropdown with friendly labels.
        Each item shows e.g. 'COM7 — Arduino Mega 2560' but carries the bare
        device ('COM7') as its data — that's what we actually connect to."""
        import re
        prev = self._selected_url()
        try:
            from serial.tools import list_ports
            ports = list(list_ports.comports())
        except Exception:
            ports = []
        self._url_combo.blockSignals(True)
        self._url_combo.clear()
        arduino_idx = -1
        for i, p in enumerate(ports):
            desc = re.sub(r"\s*\(COM\d+\)\s*$", "", (p.description or "")).strip()
            label = f"{p.device} — {desc}" if desc and desc.lower() != "n/a" else p.device
            self._url_combo.addItem(label, p.device)
            haystack = f"{p.description or ''} {getattr(p, 'manufacturer', '') or ''}".lower()
            if "arduino" in haystack or "mega" in haystack:
                arduino_idx = i
        self._url_combo.addItem("socket://localhost:9999/", "socket://localhost:9999/")
        # Restore the prior device if still present; else prefer the Arduino.
        restored = False
        if prev:
            for i in range(self._url_combo.count()):
                if self._url_combo.itemData(i) == prev:
                    self._url_combo.setCurrentIndex(i); restored = True; break
            if not restored and prev.startswith("socket://"):
                self._url_combo.setCurrentText(prev); restored = True
        if not restored:
            if arduino_idx >= 0:
                self._url_combo.setCurrentIndex(arduino_idx)
            elif ports:
                self._url_combo.setCurrentIndex(0)
        self._url_combo.blockSignals(False)

    def _selected_url(self) -> str:
        """The actual port/URL to connect to: the selected item's device data
        when a listed port is chosen, otherwise the text the user typed."""
        combo = self._url_combo
        idx = combo.currentIndex()
        text = combo.currentText().strip()
        if idx >= 0 and combo.itemText(idx) == text:
            data = combo.itemData(idx)
            if data:
                return str(data)
        return text

    def _flush_initial_queries(self):
        """Send the deferred connect-time get_param burst, once. Triggered by the
        first status frame (MCU booted) or a fallback timer."""
        if self._pending_initial_queries:
            self._queue_queries(self._pending_initial_queries)
            self._pending_initial_queries = None

    def _queue_queries(self, queries: list):
        """Append queries to the paced send queue (one every 40 ms) so a burst
        can't overrun the MCU's serial RX buffer."""
        self._query_queue.extend(queries)
        if not self._query_timer.isActive():
            self._query_timer.start(40)

    def _drain_query_queue(self):
        if not self._query_queue:
            self._query_timer.stop()
            return
        self._send(self._query_queue.pop(0))

    def _on_connection_changed(self, connected: bool):
        self._set_connected(connected)
        if connected:
            url = self._selected_url()
            self._event_log.append("SYSTEM", f"Connected to {url}")
            # Pace these — sending all at once overruns the MCU RX buffer and
            # the later queries (the calibration ones) get silently dropped.
            queries = [
                {"cmd": "query_status"},
                {"cmd": "query_positions"},
                {"cmd": "query_sensors"},
                {"cmd": "get_param", "key": "steps_per_mm_x"},
                {"cmd": "get_param", "key": "steps_per_mm_y"},
                {"cmd": "get_param", "key": "steps_per_mm_z"},
                {"cmd": "get_param", "key": "max_travel_mm_x"},
                {"cmd": "get_param", "key": "max_travel_mm_y"},
                {"cmd": "get_param", "key": "max_travel_mm_z"},
            ]
            queries += [{"cmd": "get_param", "key": f"tof_offset_{ch}"} for ch in range(4)]
            queries += [{"cmd": "get_param", "key": "tof_max_sigma_mm"},
                        {"cmd": "get_param", "key": "tof_min_signal_kcps"}]
            # Don't send yet — the Mega is mid-reset (DTR) and would drop these.
            # Fire them on the first status frame (proof it's booted); a fallback
            # timer covers the unlikely case no status arrives. The sim sends
            # status immediately, so this fires right away there.
            self._pending_initial_queries = queries
            QTimer.singleShot(3000, self._flush_initial_queries)
            # ToF distances now ride in the 4 Hz status broadcast (push), so we no
            # longer poll query_sensors — that command stream was congesting the
            # MCU RX buffer and dropping the occasional user command. The manual
            # "Refresh Readings" button still sends a one-shot query_sensors.
        else:
            self._pending_initial_queries = None
            self._query_queue.clear()
            self._query_timer.stop()
            self._event_log.append("SYSTEM", "Disconnected")

    def _on_error(self, msg: str):
        self._sensor_timer.stop()
        self._query_timer.stop()
        self._query_queue.clear()
        self._status_bar.showMessage(f"Error: {msg}")
        self._set_connected(False)
        self._conn_btn.setText("Connect")

    def _on_command_failed(self, msg: dict):
        verb = msg.get("cmd", "?")
        self._event_log.append("SYSTEM",
            f"Command not confirmed after retries: {verb} — check the connection")
        self._status_bar.showMessage(f"Command dropped: {verb}", 4000)

    def _set_connected(self, connected: bool):
        self._conn_light.set_bool(connected, "green", "red")
        self._conn_status.setText("Connected" if connected else "Disconnected")
        self._run_tab.set_connected(connected)
        self._prog_tab.set_connected(connected)
        self._cal_tab.set_connected(connected)
        self._svc_tab.set_connected(connected)
        if not connected:
            self._status_bar.showMessage("Disconnected")

    def _on_tab_changed(self, index: int):
        # When switching to calibration, immediately refresh sensors & positions
        if index == 1 and self._worker and self._worker.isRunning():
            self._send({"cmd": "query_sensors"})
            self._send({"cmd": "query_positions"})

    def _poll_sensors(self):
        if self._worker and self._worker.isRunning():
            self._send({"cmd": "query_sensors"})

    # ---- Sending ---------------------------------------------------------

    # Commands that represent meaningful user actions worth logging
    _LOG_CMDS = {
        "home":         "User: Home initiated",
        "load_program": "User: Program loaded",
        "run_program":  "User: Program started",
        "pause":        "User: Pause requested",
        "resume":       "User: Resume requested",
        "estop":        "User: E-STOP pressed",
        "reset_fault":  "User: Fault reset",
        "reset_estop":  "User: E-Stop reset",
        "calibrate_sensors": "User: Sensor baseline read",
        "calibrate_axis":    None,   # built dynamically
        "cal_jog":           None,   # built dynamically
        "set_cal_distance":  None,   # built dynamically
        "set_max_travel":    None,   # built dynamically
        "teach_position": None,   # built dynamically below
        "save_position":  None,
    }

    def _send(self, cmd: dict):
        if self._worker and self._worker.isRunning():
            verb = cmd.get("cmd", "")
            if verb in self._LOG_CMDS:
                if verb == "calibrate_axis":
                    msg = f"User: Calibrate {cmd.get('axis','?')} axis started"
                elif verb == "cal_jog":
                    msg = (f"User: Jog {cmd.get('axis','?')} "
                           f"{cmd.get('steps',0):+d} steps")
                elif verb == "set_cal_distance":
                    msg = (f"User: Cal distance set — "
                           f"{cmd.get('axis','?')} = {cmd.get('mm',0):.1f} mm")
                elif verb == "set_max_travel":
                    msg = (f"User: Max travel set — "
                           f"{cmd.get('axis','?')} = {cmd.get('mm',0):.1f} mm")
                elif verb == "teach_position":
                    msg = f"User: Taught position '{cmd.get('name', '?')}'"
                elif verb == "save_position":
                    msg = f"User: Saved position '{cmd.get('name', '?')}'"
                elif verb == "load_program":
                    prog_name = cmd.get('program', {}).get('name', '?')
                    msg = f"User: Loaded program '{prog_name}'"
                else:
                    msg = self._LOG_CMDS[verb]
                if msg:
                    self._event_log.append("USER", msg)
            self._worker.send(cmd)

    # ---- Message dispatch ------------------------------------------------

    def _on_message(self, msg: dict):
        msg_type = msg.get("type")

        if msg_type == "status":
            if self._pending_initial_queries:
                self._flush_initial_queries()
            self._run_tab.on_status(msg)
            self._cal_tab.on_status(msg)
            self._svc_tab.on_status(msg)
            # ToF now rides in the status frame (push), so feed the live readouts
            # straight from it — no separate query_sensors poll needed.
            if "tof" in msg:
                self._svc_tab.on_sensors(msg)
                self._cal_tab.on_sensors(msg)
            state = msg.get("state", "")
            if state != self._last_state:
                fault = msg.get("fault")
                suffix = f" ({fault})" if fault else ""
                self._event_log.append("STATE",
                    f"State → {state}{suffix}")
                self._last_state = state
            # Surface program step updates (current_op changes)
            # Only log steps while actively running — suppress stale
            # messages that arrive after a stop or fault.
            op = msg.get("current_op")
            step = msg.get("step_index")
            active = state in ("RUNNING", "PAUSED")
            if active and op and op != getattr(self, '_last_op', None):
                self._last_op = op
                self._event_log.append("PROGRAM",
                    f"Step {step}: {op}")
            if not active:
                self._last_op = None
            self._status_bar.showMessage(
                f"State: {state}  |  seq: {msg.get('seq','—')}  |  "
                f"uptime: {msg.get('uptime_ms', 0) // 1000}s"
            )

        elif msg_type == "ack":
            cmd = msg.get("cmd", "")
            if cmd == "query_sensors":
                self._cal_tab.on_sensors(msg)
                self._comms_tab.on_sensors(msg)
                self._svc_tab.on_sensors(msg)
            elif cmd == "query_positions":
                self._svc_tab.on_positions(msg)
            elif cmd in ("teach_position", "save_position"):
                self._svc_tab.on_teach_ack()
            elif cmd == "move_to":
                pass
            elif cmd == "load_program":
                name = getattr(self._run_tab, '_pending_program_name',
                               'Loaded')
                self._run_tab.set_program(name)
                self._prog_tab.on_upload_ack(msg)
                # If program came from the Run tab file picker, populate editor
                pending = getattr(self._run_tab, '_pending_program', None)
                if pending:
                    self._prog_tab.set_content(pending)
                    self._run_tab._pending_program = None
            elif cmd == "get_program":
                program = msg.get("program", {})
                self._prog_tab.on_program(program)
            elif cmd == "get_param":
                key   = msg.get("key", "")
                value = msg.get("value")
                if value is not None:
                    axis_map = {
                        "steps_per_mm_x": "X",
                        "steps_per_mm_y": "Y",
                        "steps_per_mm_z": "Z",
                    }
                    travel_map = {
                        "max_travel_mm_x": "X",
                        "max_travel_mm_y": "Y",
                        "max_travel_mm_z": "Z",
                    }
                    if key in axis_map:
                        self._cal_tab.set_steps_per_mm(axis_map[key], float(value))
                    elif key in travel_map:
                        self._cal_tab.set_max_travel_value(travel_map[key], float(value))
                    elif key.startswith("tof_offset_"):
                        ch = int(key.split("_")[-1])
                        self._cal_tab.set_baseline(ch, float(value))
                    elif key in ("tof_max_sigma_mm", "tof_min_signal_kcps"):
                        self._cal_tab.set_tof_threshold_value(key, float(value))
            elif cmd == "calibrate_sensors":
                offsets = msg.get("offsets", [])
                self._cal_tab.on_cal_ack(offsets)
                self._send({"cmd": "query_sensors"})
            elif cmd == "calibrate_axis":
                pass   # state updates via status broadcast
            elif cmd == "set_cal_distance":
                # Refresh calibration values after a successful set
                self._queue_queries([{"cmd": "get_param", "key": k} for k in
                    ("steps_per_mm_x", "steps_per_mm_y", "steps_per_mm_z")])
            elif cmd == "set_max_travel":
                # Refresh travel limits after a successful set
                self._queue_queries([{"cmd": "get_param", "key": k} for k in
                    ("max_travel_mm_x", "max_travel_mm_y", "max_travel_mm_z")])
            elif cmd == "cal_jog":
                pass   # accumulator surfaced via status (cal_steps)
            elif cmd in ("reset_fault", "reset_estop"):
                self._last_fault = None   # allow next fault to log fresh
            elif cmd == "run_program":
                # Don't wait for next status broadcast — enable Pause now
                self._run_tab.force_state("RUNNING")
            elif cmd == "pause":
                self._run_tab.force_state("PAUSED")
            elif cmd == "resume":
                self._run_tab.force_state("RUNNING")
            elif cmd in ("set_servo", "set_output"):
                self._send({"cmd": "query_sensors"})
            self._status_bar.showMessage(
                f"ACK  id={msg.get('id')}  cmd={cmd}")

        elif msg_type == "nack":
            reason = msg.get("reason", "?")
            cmd    = msg.get("cmd", "?")
            if cmd == "load_program":
                detail = msg.get("detail", reason)
                self._prog_tab.on_upload_nack(detail)
                QMessageBox.warning(self, "Program Error",
                    f"Could not load program:\n{detail}")
            elif cmd in ("calibrate_axis", "cal_jog", "set_cal_distance", "set_max_travel"):
                QMessageBox.warning(self, "Calibration Error",
                    f"Calibration command failed:\n{reason}")
            self._status_bar.showMessage(
                f"NACK  id={msg.get('id')}  cmd={cmd}  reason={reason}")

        elif msg_type == "log":
            message = msg.get("message", "")
            self._event_log.append("PROGRAM", message)

        elif msg_type == "fault":
            reason = msg.get("reason", "?")
            # Deduplicate — simulator may send the same fault more than once
            if reason != getattr(self, '_last_fault', None):
                self._last_fault = reason
                self._event_log.append("FAULT", f"Machine fault: {reason}")
                QMessageBox.warning(self, "Fault", f"Machine fault:\n{reason}")
            self._status_bar.showMessage(f"FAULT: {reason}")

    def closeEvent(self, event):
        self._sensor_timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker.disconnect()
            self._worker.wait(2000)
        event.accept()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()