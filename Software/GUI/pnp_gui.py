#!/usr/bin/env python3
"""
Pick-and-Place GUI  v0.2
Requires:  pip install PyQt6 pyserial

Simulator:  socket://localhost:9999/
Hardware:   COM3  (or whatever port Windows assigns)
"""

import json
import queue
import sys
import time
from typing import Optional

import serial
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QTextCursor, QColor
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLabel, QPushButton, QLineEdit, QTabWidget,
    QComboBox, QTextEdit, QGroupBox, QFrame,
    QInputDialog, QMessageBox, QSizePolicy,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QStatusBar, QSplitter,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_COLORS = {
    "IDLE":                   "#95a5a6",
    "HOMING":                 "#3498db",
    "READY":                  "#27ae60",
    "PICKING":                "#2980b9",
    "VERIFY_PICKUP":          "#2980b9",
    "MOVING_TO_LASER":        "#2980b9",
    "WAITING_FOR_LASER_SAFE": "#e67e22",
    "PLACING":                "#2980b9",
    "WAITING_FOR_CUT":        "#e67e22",
    "RETRIEVING":             "#2980b9",
    "DEPOSITING":             "#2980b9",
    "PAUSED":                 "#f39c12",
    "FAULTED":                "#e74c3c",
    "ESTOPPED":               "#c0392b",
}

BUTTON_STATES = {
    "home":        {"IDLE", "READY"},
    "start_job":   {"READY"},
    "pause":       {"PICKING", "VERIFY_PICKUP", "MOVING_TO_LASER",
                    "WAITING_FOR_LASER_SAFE", "PLACING",
                    "WAITING_FOR_CUT", "RETRIEVING", "DEPOSITING"},
    "resume":      {"PAUSED"},
    "reset_fault": {"FAULTED"},
    "reset_estop": {"ESTOPPED"},
}

TOF_PURPOSES = [
    "Pickup corner 1",
    "Pickup corner 2",
    "Pickup corner 3",
    "Pickup corner 4",
    "Laser head home",
    "Material remaining",
]

NAMED_POSITIONS = ["home", "laser_a", "laser_b", "deposit"]

# Button color pairs: (active_color, inactive_color)
# active = this IS the current state; inactive = it is not
BTN_COLORS = {
    "door_open":       ("#2980b9", "#bdc3c7"),   # blue   / gray
    "door_closed":     ("#e74c3c", "#bdc3c7"),   # red    / gray
    "laser_press":     ("#e67e22", "#bdc3c7"),   # orange / gray
    "laser_release":   ("#27ae60", "#bdc3c7"),   # green  / gray
    "pump_on":         ("#27ae60", "#bdc3c7"),   # green  / gray
    "pump_off":        ("#7f8c8d", "#bdc3c7"),   # dark   / gray
    "valve_on":        ("#2980b9", "#bdc3c7"),   # blue   / gray
    "valve_off":       ("#7f8c8d", "#bdc3c7"),   # dark   / gray
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _btn(label: str, min_width: int = 90) -> QPushButton:
    b = QPushButton(label)
    b.setMinimumWidth(min_width)
    return b


def _group(title: str, layout) -> QGroupBox:
    g = QGroupBox(title)
    g.setLayout(layout)
    return g


def _label(text: str, bold: bool = False, pt: int = 0) -> QLabel:
    lbl = QLabel(text)
    if bold or pt:
        f = lbl.font()
        if bold: f.setBold(True)
        if pt:   f.setPointSize(pt)
        lbl.setFont(f)
    return lbl


def _style_btn(btn: QPushButton, active: bool, key: str):
    """Apply active/inactive colour to a state-tracking button."""
    color = BTN_COLORS[key][0] if active else BTN_COLORS[key][1]
    text_color = "white" if active else "#555"
    weight = "bold" if active else "normal"
    btn.setStyleSheet(
        f"background-color:{color}; color:{text_color}; font-weight:{weight};"
    )


class StatusLight(QLabel):
    _COLORS = {
        "green":  "#27ae60", "red":    "#e74c3c",
        "yellow": "#f39c12", "gray":   "#95a5a6",
        "blue":   "#3498db", "orange": "#e67e22",
    }

    def __init__(self, size: int = 14, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.set_color("gray")

    def set_color(self, color: str):
        hex_c = self._COLORS.get(color, color)
        r = self._size // 2
        self.setStyleSheet(
            f"background-color:{hex_c}; border-radius:{r}px;"
            f"border:1px solid rgba(0,0,0,0.25);"
        )

    def set_bool(self, value: bool, true_color="green", false_color="gray"):
        self.set_color(true_color if value else false_color)


# ---------------------------------------------------------------------------
# Serial worker
# ---------------------------------------------------------------------------

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

class RunTab(QWidget):
    command_requested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state     = "IDLE"
        self._connected = False
        self._build_ui()
        self._update_buttons()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # State banner
        self._state_label = _label("IDLE", bold=True, pt=20)
        self._state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._state_label.setMinimumHeight(50)
        self._state_label.setStyleSheet(
            f"background-color:{STATE_COLORS['IDLE']}; color:white;"
            f"border-radius:6px; padding:4px;"
        )
        root.addWidget(self._state_label)

        # Info + sensors
        mid = QHBoxLayout()

        info_form = QFormLayout()
        info_form.setSpacing(6)
        self._lbl_position = QLabel("—")
        self._lbl_job      = QLabel("—")
        self._lbl_uptime   = QLabel("—")
        self._lbl_fault    = QLabel("None")
        self._lbl_fault.setStyleSheet("color:green; font-weight:bold;")
        info_form.addRow("Position:", self._lbl_position)
        info_form.addRow("Job:",      self._lbl_job)
        info_form.addRow("Uptime:",   self._lbl_uptime)
        info_form.addRow("Fault:",    self._lbl_fault)
        mid.addWidget(_group("Machine", info_form))

        sensor_grid = QGridLayout()
        sensor_grid.setSpacing(6)
        self._lights = {}
        sensors = [
            ("pickup_ok",        "Pickup OK",   "green",  "red"),
            ("material_present", "Material",    "green",  "gray"),
            ("laser_safe",       "Laser Safe",  "green",  "orange"),
            ("estop_hw",         "E-Stop HW",   "red",    "green"),
        ]
        for row, (key, label, t_col, f_col) in enumerate(sensors):
            light = StatusLight(16)
            self._lights[key] = (light, t_col, f_col)
            sensor_grid.addWidget(light,        row, 0, Qt.AlignmentFlag.AlignCenter)
            sensor_grid.addWidget(QLabel(label), row, 1)
        mid.addWidget(_group("Sensors", sensor_grid))
        root.addLayout(mid)

        # Controls
        btn_layout = QGridLayout()
        btn_layout.setSpacing(6)

        self._btn_home        = _btn("Home")
        self._btn_start       = _btn("Start Job")
        self._btn_pause       = _btn("Pause")
        self._btn_resume      = _btn("Resume")
        self._btn_reset_fault = _btn("Reset Fault")
        self._btn_reset_estop = _btn("Reset E-Stop")
        self._btn_estop       = _btn("E-STOP", min_width=120)

        for b, c in [
            (self._btn_start,  "#27ae60"),
            (self._btn_pause,  "#e67e22"),
            (self._btn_resume, "#3498db"),
            (self._btn_estop,  "#c0392b"),
        ]:
            b.setStyleSheet(f"background-color:{c}; color:white; font-weight:bold;")

        self._btn_estop.setMinimumHeight(50)
        f = self._btn_estop.font(); f.setPointSize(14); f.setBold(True)
        self._btn_estop.setFont(f)

        btn_layout.addWidget(self._btn_home,        0, 0)
        btn_layout.addWidget(self._btn_start,       0, 1)
        btn_layout.addWidget(self._btn_pause,       0, 2)
        btn_layout.addWidget(self._btn_resume,      0, 3)
        btn_layout.addWidget(self._btn_reset_fault, 1, 0)
        btn_layout.addWidget(self._btn_reset_estop, 1, 1)
        btn_layout.addWidget(self._btn_estop,       0, 4, 2, 1)
        root.addWidget(_group("Controls", btn_layout))
        root.addStretch()

        self._btn_home.clicked.connect(
            lambda: self.command_requested.emit({"cmd": "home"}))
        self._btn_start.clicked.connect(self._start_job_dialog)
        self._btn_pause.clicked.connect(
            lambda: self.command_requested.emit({"cmd": "pause"}))
        self._btn_resume.clicked.connect(
            lambda: self.command_requested.emit({"cmd": "resume"}))
        self._btn_estop.clicked.connect(
            lambda: self.command_requested.emit({"cmd": "estop"}))
        self._btn_reset_fault.clicked.connect(
            lambda: self.command_requested.emit({"cmd": "reset_fault"}))
        self._btn_reset_estop.clicked.connect(
            lambda: self.command_requested.emit({"cmd": "reset_estop"}))

    def _start_job_dialog(self):
        count, ok = QInputDialog.getInt(
            self, "Start Job",
            "Number of pieces (0 = run until stopped):",
            value=0, min=0, max=9999)
        if ok:
            self.command_requested.emit({"cmd": "start_job", "count": count})

    def on_status(self, msg: dict):
        state = msg.get("state", "IDLE")
        self._state = state
        color = STATE_COLORS.get(state, "#95a5a6")
        self._state_label.setText(state.replace("_", " "))
        self._state_label.setStyleSheet(
            f"background-color:{color}; color:white; border-radius:6px;"
            f"padding:4px; font-size:20pt; font-weight:bold;"
        )
        pos  = msg.get("position_name") or "—"
        jc   = msg.get("job_count", 0)
        jt   = msg.get("job_total", 0)
        secs = msg.get("uptime_ms", 0) // 1000
        self._lbl_position.setText(pos)
        self._lbl_job.setText(f"{jc} / {'∞' if jt == 0 else jt} pieces")
        self._lbl_uptime.setText(
            f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}:{secs % 60:02d}")
        fault = msg.get("fault")
        self._lbl_fault.setText(fault or "None")
        self._lbl_fault.setStyleSheet(
            "color:#e74c3c; font-weight:bold;" if fault
            else "color:green; font-weight:bold;"
        )
        for key, (light, t_col, f_col) in self._lights.items():
            light.set_bool(msg.get(key, False), t_col, f_col)
        self._update_buttons()

    def set_connected(self, connected: bool):
        self._connected = connected
        self._update_buttons()

    def _update_buttons(self):
        s, c = self._state, self._connected
        self._btn_home.setEnabled(c and s in BUTTON_STATES["home"])
        self._btn_start.setEnabled(c and s in BUTTON_STATES["start_job"])
        self._btn_pause.setEnabled(c and s in BUTTON_STATES["pause"])
        self._btn_resume.setEnabled(c and s in BUTTON_STATES["resume"])
        self._btn_reset_fault.setEnabled(c and s in BUTTON_STATES["reset_fault"])
        self._btn_reset_estop.setEnabled(c and s in BUTTON_STATES["reset_estop"])
        self._btn_estop.setEnabled(c)


# ---------------------------------------------------------------------------
# Calibration tab
# ---------------------------------------------------------------------------

class CalibrationTab(QWidget):
    command_requested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected     = False
        self._state         = "IDLE"
        # stored named positions  {name: (x, y, z)}
        self._stored_pos     = {n: (0.0, 0.0, 0.0) for n in NAMED_POSITIONS}
        self._current_outputs = {}   # last confirmed output state
        self._cur_x = 0.0
        self._cur_y = 0.0
        self._cur_z = 0.0
        self._build_ui()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setSpacing(8)
        left  = QVBoxLayout()
        right = QVBoxLayout()
        root.addLayout(left,  1)
        root.addLayout(right, 1)

        # ---- Current position (read-only, live from status) ----
        cur_form = QFormLayout()
        cur_form.setSpacing(5)
        self._lbl_x    = _label("—", bold=True)
        self._lbl_y    = _label("—", bold=True)
        self._lbl_z    = _label("—", bold=True)
        self._lbl_name = _label("—", bold=True)
        cur_form.addRow("X (mm):", self._lbl_x)
        cur_form.addRow("Y (mm):", self._lbl_y)
        cur_form.addRow("Z (mm):", self._lbl_z)
        cur_form.addRow("At:",     self._lbl_name)
        left.addWidget(_group("Current Position", cur_form))

        # ---- Target position (GUI-only until user acts) ----
        self._tgt = {"X": 0.0, "Y": 0.0, "Z": 0.0}
        self._tgt_labels = {}

        tgt_grid = QGridLayout()
        tgt_grid.setSpacing(6)

        for row, axis in enumerate(["X", "Y", "Z"]):
            val_lbl = QLabel("0.00")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val_lbl.setMinimumWidth(70)
            val_lbl.setStyleSheet(
                "border:1px solid #bdc3c7; border-radius:3px; padding:2px; background:white;")
            self._tgt_labels[axis] = val_lbl

            btn_dec = _btn("−", min_width=32)
            btn_inc = _btn("+", min_width=32)
            btn_dec.clicked.connect(lambda _, a=axis: self._nudge_target(a, -1))
            btn_inc.clicked.connect(lambda _, a=axis: self._nudge_target(a, +1))

            tgt_grid.addWidget(QLabel(f"{axis}:"), row, 0)
            tgt_grid.addWidget(btn_dec,            row, 1)
            tgt_grid.addWidget(val_lbl,            row, 2)
            tgt_grid.addWidget(btn_inc,            row, 3)

        # Step size selector
        step_row = QHBoxLayout()
        step_row.addWidget(QLabel("Step (mm):"))
        self._step_combo = QComboBox()
        self._step_combo.addItems(["0.01", "0.1", "1.0", "5.0", "10.0", "50.0"])
        self._step_combo.setCurrentIndex(2)
        self._step_combo.setFixedWidth(70)
        step_row.addWidget(self._step_combo)
        step_row.addStretch()

        # Action buttons
        act_row = QHBoxLayout()
        self._btn_go     = _btn("Go to Target", min_width=120)
        self._btn_go.setStyleSheet(
            "background-color:#2980b9; color:white; font-weight:bold;")
        btn_reset = _btn("Reset to Current", min_width=120)
        btn_reset.setToolTip("Copy current machine position into target fields")
        btn_reset.clicked.connect(self._reset_target_to_current)
        self._btn_go.clicked.connect(self._go_to_target)
        act_row.addWidget(self._btn_go)
        act_row.addWidget(btn_reset)
        act_row.addStretch()

        tgt_v = QVBoxLayout()
        tgt_v.addLayout(tgt_grid)
        tgt_v.addLayout(step_row)
        tgt_v.addLayout(act_row)
        self._hint_label = QLabel("")
        self._hint_label.setStyleSheet("color:#e67e22; font-style:italic;")
        self._hint_label.setWordWrap(True)
        tgt_v.addWidget(self._hint_label)
        left.addWidget(_group("Target Position", tgt_v))

        # ---- Inputs ----
        in_grid = QGridLayout()
        in_grid.setSpacing(6)
        self._input_lights = {}
        for col, (key, label, t_col, f_col) in enumerate([
            ("estop_hw",  "E-Stop HW", "red",   "green"),
            ("start_btn", "Start Btn", "green",  "gray"),
            ("pause_btn", "Pause Btn", "yellow", "gray"),
        ]):
            light = StatusLight(14)
            self._input_lights[key] = (light, t_col, f_col)
            in_grid.addWidget(light,          0, col * 2,
                              Qt.AlignmentFlag.AlignCenter)
            in_grid.addWidget(QLabel(label),  0, col * 2 + 1)
        left.addWidget(_group("Inputs", in_grid))
        left.addStretch()

        # ---- Named positions table ----
        self._pos_table = QTableWidget(len(NAMED_POSITIONS), 4)
        self._pos_table.setHorizontalHeaderLabels(["Position", "X", "Y", "Z"])
        self._pos_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        for c in (1, 2, 3):
            self._pos_table.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.ResizeMode.Stretch)
        self._pos_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._pos_table.verticalHeader().setVisible(False)
        self._pos_table.setMaximumHeight(160)
        self._populate_pos_table()

        # Teach controls
        teach_row = QHBoxLayout()
        teach_row.addWidget(QLabel("Save as:"))
        self._teach_combo = QComboBox()
        self._teach_combo.addItems(NAMED_POSITIONS)
        self._teach_combo.setFixedWidth(90)
        teach_row.addWidget(self._teach_combo)
        self._btn_teach_current = _btn("Teach Current")
        self._btn_teach_current.setToolTip(
            "Save the machine\'s current coordinates as the selected position")
        self._btn_teach_target  = _btn("Teach Target")
        self._btn_teach_target.setToolTip(
            "Save the target coordinates as the selected position (no motion)")
        teach_row.addWidget(self._btn_teach_current)
        teach_row.addWidget(self._btn_teach_target)
        teach_row.addStretch()

        self._btn_teach_current.clicked.connect(self._teach_current)
        self._btn_teach_target.clicked.connect(self._teach_target)

        self._teach_btns = {
            "current": self._btn_teach_current,
            "target":  self._btn_teach_target,
        }

        pos_v = QVBoxLayout()
        pos_v.addWidget(self._pos_table)
        pos_v.addLayout(teach_row)
        right.addWidget(_group("Named Positions", pos_v))

        # ---- Servo test ----
        servo_grid = QGridLayout()
        servo_grid.setSpacing(6)
        servo_grid.addWidget(_label("Door:"),      0, 0)
        self._btn_door_open   = _btn("Open",  min_width=80)
        self._btn_door_close  = _btn("Close", min_width=80)
        servo_grid.addWidget(self._btn_door_open,  0, 1)
        servo_grid.addWidget(self._btn_door_close, 0, 2)
        servo_grid.addWidget(_label("Laser Btn:"), 1, 0)
        self._btn_laser_press   = _btn("Press",   min_width=80)
        self._btn_laser_release = _btn("Release", min_width=80)
        servo_grid.addWidget(self._btn_laser_press,   1, 1)
        servo_grid.addWidget(self._btn_laser_release, 1, 2)
        self._btn_door_open.clicked.connect(
            lambda: self._send_servo("door", "open"))
        self._btn_door_close.clicked.connect(
            lambda: self._send_servo("door", "closed"))
        self._btn_laser_press.clicked.connect(
            lambda: self._send_servo("laser_btn", "press"))
        self._btn_laser_release.clicked.connect(
            lambda: self._send_servo("laser_btn", "release"))
        right.addWidget(_group("Servo Test", servo_grid))

        # ---- Output test ----
        out_grid = QGridLayout()
        out_grid.setSpacing(6)
        out_grid.addWidget(_label("Pump:"),  0, 0)
        self._btn_pump_on  = _btn("ON",  min_width=80)
        self._btn_pump_off = _btn("OFF", min_width=80)
        out_grid.addWidget(self._btn_pump_on,  0, 1)
        out_grid.addWidget(self._btn_pump_off, 0, 2)
        out_grid.addWidget(_label("Valve:"), 1, 0)
        self._btn_valve_on  = _btn("ON",  min_width=80)
        self._btn_valve_off = _btn("OFF", min_width=80)
        out_grid.addWidget(self._btn_valve_on,  1, 1)
        out_grid.addWidget(self._btn_valve_off, 1, 2)
        self._btn_pump_on.clicked.connect(
            lambda: self._send_output("pump", True))
        self._btn_pump_off.clicked.connect(
            lambda: self._send_output("pump", False))
        self._btn_valve_on.clicked.connect(
            lambda: self._send_output("valve", True))
        self._btn_valve_off.clicked.connect(
            lambda: self._send_output("valve", False))
        right.addWidget(_group("Output Test", out_grid))
        right.addStretch()

        self._apply_output_states({})
    # ---- Data update methods ---------------------------------------------

    def on_status(self, msg: dict):
        self._state  = msg.get("state", "IDLE")
        self._cur_x  = float(msg.get("x_mm", 0.0))
        self._cur_y  = float(msg.get("y_mm", 0.0))
        self._cur_z  = float(msg.get("z_mm", 0.0))
        self._lbl_x.setText(f"{self._cur_x:.2f}")
        self._lbl_y.setText(f"{self._cur_y:.2f}")
        self._lbl_z.setText(f"{self._cur_z:.2f}")
        self._lbl_name.setText(msg.get("position_name") or "—")
        self._update_controls()

    def on_sensors(self, msg: dict):
        inputs = msg.get("inputs", {})
        for key, (light, t_col, f_col) in self._input_lights.items():
            light.set_bool(inputs.get(key, False), t_col, f_col)
        self._current_outputs = msg.get("outputs", {})
        self._apply_output_states(self._current_outputs)

    def on_positions(self, msg: dict):
        for name, coords in msg.get("positions", {}).items():
            self._stored_pos[name] = (
                coords.get("x_mm", 0.0),
                coords.get("y_mm", 0.0),
                coords.get("z_mm", 0.0),
            )
        self._populate_pos_table()

    def on_teach_ack(self):
        self.command_requested.emit({"cmd": "query_positions"})

    def _populate_pos_table(self):
        for row, name in enumerate(NAMED_POSITIONS):
            x, y, z = self._stored_pos.get(name, (0.0, 0.0, 0.0))
            self._pos_table.setItem(row, 0, QTableWidgetItem(name))
            self._pos_table.setItem(row, 1, QTableWidgetItem(f"{x:.2f}"))
            self._pos_table.setItem(row, 2, QTableWidgetItem(f"{y:.2f}"))
            self._pos_table.setItem(row, 3, QTableWidgetItem(f"{z:.2f}"))

    def _apply_output_states(self, outputs: dict):
        door  = outputs.get("servo_door",      "closed")
        laser = outputs.get("servo_laser_btn", "release")
        pump  = outputs.get("pump",            False)
        valve = outputs.get("valve",           False)
        _style_btn(self._btn_door_open,     door  == "open",    "door_open")
        _style_btn(self._btn_door_close,    door  == "closed",  "door_closed")
        _style_btn(self._btn_laser_press,   laser == "press",   "laser_press")
        _style_btn(self._btn_laser_release, laser == "release", "laser_release")
        _style_btn(self._btn_pump_on,       pump  is True,      "pump_on")
        _style_btn(self._btn_pump_off,      pump  is False,     "pump_off")
        _style_btn(self._btn_valve_on,      valve is True,      "valve_on")
        _style_btn(self._btn_valve_off,     valve is False,     "valve_off")

    # ---- Target position helpers -----------------------------------------

    def _nudge_target(self, axis: str, direction: int):
        step = float(self._step_combo.currentText())
        self._tgt[axis] = round(self._tgt[axis] + direction * step, 4)
        self._tgt_labels[axis].setText(f"{self._tgt[axis]:.2f}")

    def _reset_target_to_current(self):
        self._tgt["X"] = self._cur_x
        self._tgt["Y"] = self._cur_y
        self._tgt["Z"] = self._cur_z
        for axis in ("X", "Y", "Z"):
            self._tgt_labels[axis].setText(f"{self._tgt[axis]:.2f}")

    def _go_to_target(self):
        self.command_requested.emit({
            "cmd":   "move_to",
            "x_mm":  self._tgt["X"],
            "y_mm":  self._tgt["Y"],
            "z_mm":  self._tgt["Z"],
        })

    def _teach_current(self):
        name = self._teach_combo.currentText()
        self.command_requested.emit({"cmd": "teach_position", "name": name})

    def _teach_target(self):
        name = self._teach_combo.currentText()
        self.command_requested.emit({
            "cmd":  "save_position",
            "name": name,
            "x_mm": self._tgt["X"],
            "y_mm": self._tgt["Y"],
            "z_mm": self._tgt["Z"],
        })

    # ---- Optimistic output helpers ---------------------------------------

    def _send_servo(self, servo: str, position: str):
        self._current_outputs[f"servo_{servo}"] = position
        self._apply_output_states(self._current_outputs)
        self.command_requested.emit(
            {"cmd": "set_servo", "servo": servo, "position": position})

    def _send_output(self, output: str, state: bool):
        self._current_outputs[output] = state
        self._apply_output_states(self._current_outputs)
        self.command_requested.emit(
            {"cmd": "set_output", "output": output, "state": state})

    # ---- Control enable/disable -----------------------------------------

    def _update_controls(self):
        can_act = self._connected and self._state == "READY"
        self._btn_go.setEnabled(can_act)
        for btn in self._teach_btns.values():
            btn.setEnabled(can_act or
                           (self._connected and self._state == "IDLE"
                            and btn is self._btn_teach_target))
        if not self._connected:
            hint = "Not connected to machine."
        elif self._state == "IDLE":
            hint = "Press Home on the Run tab to enable Go to Target and Teach Current."
        elif self._state == "HOMING":
            hint = "Homing in progress…"
        elif self._state == "READY":
            hint = ""
        else:
            hint = f"Motion controls unavailable in {self._state} state."
        self._hint_label.setText(hint)
    def set_connected(self, connected: bool):
        self._connected = connected
        self._update_controls()


# ---------------------------------------------------------------------------
# Service tab  (ToF detail + comms log only)
# ---------------------------------------------------------------------------

class ServiceTab(QWidget):
    command_requested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # ToF table
        self._tof_table = QTableWidget(6, 4)
        self._tof_table.setHorizontalHeaderLabels(
            ["Ch", "Dist (mm)", "Valid", "Purpose"])
        self._tof_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        self._tof_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._tof_table.verticalHeader().setVisible(False)
        self._tof_table.setMaximumHeight(200)
        for row in range(6):
            self._tof_table.setItem(row, 0, QTableWidgetItem(str(row)))
            self._tof_table.setItem(row, 1, QTableWidgetItem("—"))
            self._tof_table.setItem(row, 2, QTableWidgetItem("—"))
            self._tof_table.setItem(row, 3, QTableWidgetItem(TOF_PURPOSES[row]))

        tof_v = QVBoxLayout()
        qs_btn = _btn("Query Sensors")
        qs_btn.clicked.connect(
            lambda: self.command_requested.emit({"cmd": "query_sensors"}))
        tof_v.addWidget(qs_btn)
        tof_v.addWidget(self._tof_table)
        root.addWidget(_group("ToF Sensors", tof_v))

        # Comms log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Courier New", 9))
        log_v = QVBoxLayout()
        clear_btn = _btn("Clear", min_width=60)
        clear_btn.clicked.connect(self._log.clear)
        log_v.addWidget(clear_btn)
        log_v.addWidget(self._log)
        root.addWidget(_group("Communications Log", log_v), 1)

    def on_sensors(self, msg: dict):
        for entry in msg.get("tof", []):
            row = entry.get("ch", 0)
            if row >= 6:
                continue
            self._tof_table.setItem(row, 1,
                QTableWidgetItem(str(entry.get("dist_mm", "—"))))
            self._tof_table.setItem(row, 2,
                QTableWidgetItem("✓" if entry.get("valid") else "✗"))

    def log_tx(self, line: str):
        self._append(f"TX  {line}", "#2980b9")

    def log_rx(self, line: str):
        self._append(f"RX  {line}", "#27ae60")

    def _append(self, text: str, color: str):
        ts = time.strftime("%H:%M:%S")
        self._log.setTextColor(QColor(color))
        self._log.append(f"[{ts}] {text}")
        self._log.moveCursor(QTextCursor.MoveOperation.End)

    def set_connected(self, connected: bool):
        pass


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pick-and-Place Control")
        self.setMinimumSize(860, 640)
        self._worker: Optional[SerialWorker] = None
        self._sensor_timer = QTimer(self)
        self._sensor_timer.timeout.connect(self._poll_sensors)
        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        # Connection bar
        conn = QHBoxLayout()
        conn.addWidget(QLabel("Port / URL:"))
        self._url_edit = QLineEdit("socket://localhost:9999/")
        self._url_edit.setMinimumWidth(240)
        conn.addWidget(self._url_edit)
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
        self._run_tab = RunTab()
        self._cal_tab = CalibrationTab()
        self._svc_tab = ServiceTab()
        self._tabs.addTab(self._run_tab, "Run")
        self._tabs.addTab(self._cal_tab, "Service")
        self._tabs.addTab(self._svc_tab, "Comms")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self._tabs)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Not connected")

        self._run_tab.command_requested.connect(self._send)
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
            url = self._url_edit.text().strip()
            if not url:
                return
            self._worker = SerialWorker()
            self._worker.message_received.connect(self._on_message)
            self._worker.connection_changed.connect(self._on_connection_changed)
            self._worker.error_occurred.connect(self._on_error)
            self._worker.raw_tx.connect(self._svc_tab.log_tx)
            self._worker.raw_rx.connect(self._svc_tab.log_rx)
            self._worker.connect_to(url)
            self._conn_btn.setText("Disconnect")

    def _on_connection_changed(self, connected: bool):
        self._set_connected(connected)
        if connected:
            self._send({"cmd": "query_status"})
            self._send({"cmd": "query_positions"})
            self._send({"cmd": "query_sensors"})
            self._sensor_timer.start(2000)   # poll sensors every 2 s

    def _on_error(self, msg: str):
        self._sensor_timer.stop()
        self._status_bar.showMessage(f"Error: {msg}")
        self._set_connected(False)
        self._conn_btn.setText("Connect")

    def _set_connected(self, connected: bool):
        self._conn_light.set_bool(connected, "green", "red")
        self._conn_status.setText("Connected" if connected else "Disconnected")
        self._run_tab.set_connected(connected)
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

    def _send(self, cmd: dict):
        if self._worker and self._worker.isRunning():
            self._worker.send(cmd)

    # ---- Message dispatch ------------------------------------------------

    def _on_message(self, msg: dict):
        msg_type = msg.get("type")

        if msg_type == "status":
            self._run_tab.on_status(msg)
            self._cal_tab.on_status(msg)
            state = msg.get("state", "")
            self._status_bar.showMessage(
                f"State: {state}  |  seq: {msg.get('seq','—')}  |  "
                f"uptime: {msg.get('uptime_ms', 0) // 1000}s"
            )

        elif msg_type == "ack":
            cmd = msg.get("cmd", "")
            if cmd == "query_sensors":
                self._cal_tab.on_sensors(msg)
                self._svc_tab.on_sensors(msg)
            elif cmd == "query_positions":
                self._cal_tab.on_positions(msg)
            elif cmd in ("teach_position", "save_position"):
                self._cal_tab.on_teach_ack()
            elif cmd == "move_to":
                # Position will update via next status message
                pass
            elif cmd in ("set_servo", "set_output"):
                self._send({"cmd": "query_sensors"})
            self._status_bar.showMessage(
                f"ACK  id={msg.get('id')}  cmd={cmd}")

        elif msg_type == "nack":
            reason = msg.get("reason", "?")
            cmd    = msg.get("cmd", "?")
            self._status_bar.showMessage(
                f"NACK  id={msg.get('id')}  cmd={cmd}  reason={reason}")

        elif msg_type == "fault":
            reason = msg.get("reason", "?")
            self._status_bar.showMessage(f"FAULT: {reason}")
            QMessageBox.warning(self, "Fault", f"Machine fault:\n{reason}")

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
