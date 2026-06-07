#!/usr/bin/env python3
"""
Pick-and-Place GUI  v0.2
Requires:  pip install PyQt6 pyserial

Simulator:  socket://localhost:9999/
Hardware:   COM3  (or whatever port Windows assigns)
"""

import json
import os
import queue
import sys
import time
from datetime import datetime
from typing import Optional

import serial
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QTextCursor, QColor
from PyQt6.QtWidgets import (
    QFileDialog,
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLabel, QPushButton, QLineEdit, QTabWidget,
    QComboBox, QTextEdit, QGroupBox, QFrame,
    QInputDialog, QMessageBox, QSizePolicy,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QStatusBar, QSplitter, QPlainTextEdit,
    QDoubleSpinBox, QSpinBox, QScrollArea,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_COLORS = {
    "IDLE":      "#95a5a6",   # gray
    "HOMING":    "#3498db",   # blue
    "READY":     "#27ae60",   # green
    "RUNNING":   "#2980b9",   # active blue
    "PAUSED":    "#f39c12",   # yellow
    "FAULTED":   "#e74c3c",   # red
    "ESTOPPED":  "#c0392b",   # dark red
}

BUTTON_STATES = {
    "home":        {"IDLE", "READY"},
    "run_program": {"READY"},   # also requires _program_loaded
    "pause":       {"RUNNING"},
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
    color      = BTN_COLORS[key][0] if active else BTN_COLORS[key][1]
    text_color = "white" if active else "#555"
    weight     = "bold"  if active else "normal"
    btn.setStyleSheet(
        f"QPushButton {{ background-color:{color}; color:{text_color};"
        f" font-weight:{weight}; }}"
        " QPushButton:disabled { background-color: #bdc3c7; color: #888; font-weight: normal; }"
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
# Program editor tab
# ---------------------------------------------------------------------------

class ProgramEditorTab(QWidget):
    """
    Waypoint-based program builder.
    Layout (vertical stack):
      1. Header row: name, cycles, toolbar, status
      2. Waypoint table  (fills available height)
      3. Generate button
      4. JSON section   (fixed height at bottom)
    """

    command_requested = pyqtSignal(dict)

    _STATUS_COLORS = {
        "ok": "#27ae60", "error": "#e74c3c",
        "warning": "#e67e22", "info": "#7f8c8d",
    }

    C_NUM, C_X, C_Y, C_ZMODE, C_Z, C_SPD, C_PUMP, C_VALVE, C_DOOR, C_LASER, C_DELAY = range(11)
    COL_HEADERS = ["#", "X (mm)", "Y (mm)", "Z Mode", "Z (mm)", "Speed %",
                   "Pump", "Valve", "Door", "Laser Btn", "Wait after (ms)"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False
        self._build_ui()

    # -----------------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(4)
        root.setContentsMargins(6, 6, 6, 6)

        # ---- Row 1: header + toolbar ----
        hdr = QHBoxLayout()
        hdr.setSpacing(8)

        hdr.addWidget(_label("Cycles (0=∞):"))
        self._f_cycles = QSpinBox()
        self._f_cycles.setRange(0, 9999)
        self._f_cycles.setValue(1)
        self._f_cycles.setMaximumWidth(65)
        self._f_cycles.setToolTip(
            "Number of times to repeat the waypoint sequence.\n"
            "0 = loop until material_present is false.")
        hdr.addWidget(self._f_cycles)

        hdr.addSpacing(12)

        # Waypoint action buttons
        btn_add = _btn("+ Add Waypoint", min_width=110)
        btn_del = _btn("✕ Delete",  min_width=75)
        btn_clr = _btn("Clear All", min_width=75)
        btn_del.setStyleSheet(
            "QPushButton { color:#e74c3c; }"
            "QPushButton:disabled { color:#bdc3c7; }")
        btn_add.clicked.connect(lambda: self._add_row())
        btn_del.clicked.connect(self._delete_selected)
        btn_clr.clicked.connect(self._clear_all)
        for b in (btn_add, btn_del, btn_clr):
            hdr.addWidget(b)

        hdr.addStretch()
        self._status_lbl = QLabel("Add waypoints then click Generate")
        self._status_lbl.setStyleSheet("color:#7f8c8d;")
        hdr.addWidget(self._status_lbl)

        root.addLayout(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        # ---- Row 2: waypoint table ----
        self._table = QTableWidget(0, len(self.COL_HEADERS))
        self._table.setHorizontalHeaderLabels(self.COL_HEADERS)
        hh = self._table.horizontalHeader()
        # All columns Interactive — user can drag any divider to resize (Excel-style)
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(False)
        hh.setMinimumSectionSize(30)
        # Default widths
        for col, px in [
            (self.C_NUM,   32),
            (self.C_X,    100), (self.C_Y,    100), (self.C_Z,    100),
            (self.C_ZMODE, 82),
            (self.C_SPD,   68),
            (self.C_PUMP,  72), (self.C_VALVE, 72), (self.C_DOOR, 72),
            (self.C_LASER, 88), (self.C_DELAY, 98),
        ]:
            hh.resizeSection(col, px)

        hh.setToolTip("Z Mode: Fixed = move to exact Z;  Probe = descend until surface detected")
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setMinimumHeight(120)
        self._table.horizontalHeaderItem(self.C_DELAY).setToolTip(
            "Time to wait AFTER the arm arrives and all outputs are set.\n"
            "Sequence per row: Move → Set outputs → Wait.")
        root.addWidget(self._table, 1)   # stretch=1 → fills available height

        # ---- Row 3: generate button ----
        gen_btn = QPushButton("⟳  Generate Program JSON →")
        gen_btn.setMinimumHeight(32)
        gen_btn.setStyleSheet(
            "QPushButton { background-color:#27ae60; color:white;"
            " font-weight:bold; font-size:10pt; }"
            "QPushButton:hover { background-color:#2ecc71; }")
        gen_btn.clicked.connect(self._generate)
        root.addWidget(gen_btn)

        # ---- Row 4: JSON section ----
        json_sep = QFrame(); json_sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(json_sep)

        json_hdr = QHBoxLayout()
        json_hdr.addWidget(_label("Name:"), 0)
        self._f_name = QLineEdit("My Program")
        self._f_name.setMaximumWidth(200)
        json_hdr.addWidget(self._f_name, 0)
        json_hdr.addStretch(1)
        for label, slot in [
            ("Load File…",      self._load_file),
            ("Save File…",      self._save_file),
            ("Validate",        self._validate),
            ("Get from Machine",lambda: self.command_requested.emit({"cmd": "get_program"})),
        ]:
            b = _btn(label, min_width=0)
            b.clicked.connect(slot)
            json_hdr.addWidget(b)
        self._btn_upload = _btn("Upload to Machine", min_width=130)
        self._btn_upload.setStyleSheet(
            "QPushButton { background-color:#2980b9; color:white; font-weight:bold; }"
            "QPushButton:disabled { background-color:#bdc3c7; color:#888; }")
        self._btn_upload.clicked.connect(self._upload)
        json_hdr.addWidget(self._btn_upload)
        root.addLayout(json_hdr)

        self._editor = QPlainTextEdit()
        self._editor.setFont(QFont("Courier New", 9))
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._editor.setPlaceholderText(
            "Add waypoints above and click Generate, or load an existing program file.")
        self._editor.setFixedHeight(200)
        root.addWidget(self._editor)

        self.set_connected(False)

    # -----------------------------------------------------------------------
    # Row management
    # -----------------------------------------------------------------------

    def _add_row(self, x=0.0, y=0.0, z_mode="Fixed", z=10.0,
                 spd=70, pump="OFF", valve="Closed", door="Closed", laser="Off", delay=0):
        row = self._table.rowCount()
        self._table.insertRow(row)

        def cell(val):
            it = QTableWidgetItem(str(val))
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            return it

        def combo(*opts):
            c = QComboBox(); c.addItems(opts); return c

        num_item = cell(row + 1)
        num_item.setFlags(num_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(row, self.C_NUM,   num_item)
        self._table.setItem(row, self.C_X,     cell(x))
        self._table.setItem(row, self.C_Y,     cell(y))
        self._table.setItem(row, self.C_Z,     cell(z))
        self._table.setItem(row, self.C_SPD,   cell(spd))
        self._table.setItem(row, self.C_DELAY, cell(delay))

        z_cb = combo("Fixed", "Probe")
        z_cb.setCurrentText(z_mode)
        z_cb.currentTextChanged.connect(
            lambda t, r=row: self._on_z_mode_change(r, t))
        self._table.setCellWidget(row, self.C_ZMODE, z_cb)

        for col, opts, val in [
            (self.C_PUMP,  ("OFF", "ON"),           pump),
            (self.C_VALVE, ("Closed", "Open"),      valve),
            (self.C_DOOR,  ("Closed", "Open"),      door),
            (self.C_LASER, ("Off", "Press"),        laser),
        ]:
            cb = combo(*opts); cb.setCurrentText(val)
            self._table.setCellWidget(row, col, cb)

        self._table.setRowHeight(row, 26)
        self._table.selectRow(row)
        self._on_z_mode_change(row, z_mode)

    def _on_z_mode_change(self, row: int, mode: str) -> None:
        """Gray out Z cell when Probe is selected (global safe height is used)."""
        item = self._table.item(row, self.C_Z)
        if item is None:
            return
        if mode == "Probe":
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setBackground(QColor("#e8e8e8"))
            item.setForeground(QColor("#aaaaaa"))
            item.setText("probe")
            item.setToolTip("Global Safe Height is used — see header")
        else:
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            item.setBackground(QColor("#ffffff"))
            item.setForeground(QColor("#000000"))
            if item.text() in ("probe", "—"):
                item.setText("10.0")
            item.setToolTip("")

    def _delete_selected(self):
        row = self._table.currentRow()
        if row >= 0:
            self._table.removeRow(row)
            self._renumber()

    def _clear_all(self):
        self._table.setRowCount(0)

    def _renumber(self):
        for r in range(self._table.rowCount()):
            it = self._table.item(r, self.C_NUM)
            if it:
                it.setText(str(r + 1))

    def _read_rows(self) -> list:
        rows = []
        for r in range(self._table.rowCount()):
            def txt(col):
                it = self._table.item(r, col)
                return it.text().strip() if it else "0"
            def cb(col):
                w = self._table.cellWidget(r, col)
                return w.currentText() if w else "—"
            try:
                rows.append({
                    "x":      float(txt(self.C_X)),
                    "y":      float(txt(self.C_Y)),
                    "z_mode": cb(self.C_ZMODE),
                    "z":      float(txt(self.C_Z)) if txt(self.C_Z) else 0.0,
                    "speed":  max(1, int(float(txt(self.C_SPD)))),
                    "pump":   cb(self.C_PUMP),
                    "valve":  cb(self.C_VALVE),
                    "door":   cb(self.C_DOOR),
                    "laser":  cb(self.C_LASER),
                    "delay":  max(0, int(float(txt(self.C_DELAY)))),
                })
            except ValueError as e:
                raise ValueError(f"Row {r + 1}: {e}")
        return rows

    # -----------------------------------------------------------------------
    # Program generation
    # -----------------------------------------------------------------------

    def _build_program(self) -> dict:
        name   = self._f_name.text().strip() or "My Program"
        cycles = self._f_cycles.value()
        rows   = self._read_rows()
        if not rows:
            raise ValueError("Add at least one waypoint.")

        body = []
        for i, wp in enumerate(rows):
            x, y  = wp["x"], wp["y"]
            speed = wp["speed"]

            if wp["z_mode"] == "Probe":
                var = f"z_wp{i}"
                # x, y always required; probe descends from current Z (no approach_z needed)
                body.append({"op": "PROBE_Z", "x": x, "y": y, "store": var})
            else:
                body.append({"op": "MOVE", "x": x, "y": y,
                              "z": wp["z"], "speed": speed})

            # All outputs emitted every row — state is always explicit
            body.append({"op":"OUTPUT","name":"pump",
                          "value": wp["pump"] == "ON"})
            body.append({"op":"OUTPUT","name":"valve",
                          "value": wp["valve"] == "Open"})
            body.append({"op":"OUTPUT","name":"servo_door",
                          "value": "open" if wp["door"] == "Open" else "closed"})
            if wp["laser"] == "Press":
                body.extend([
                    {"op":"OUTPUT","name":"servo_laser_btn","value":"press"},
                    {"op":"DELAY","ms":400},
                    {"op":"OUTPUT","name":"servo_laser_btn","value":"release"},
                ])
            if wp["delay"] > 0:
                body.append({"op":"DELAY","ms":wp["delay"]})

        loop = (
            {"op":"LOOP_WHILE","condition":"material_present","body":body}
            if cycles == 0 else
            {"op":"LOOP_FOR","count":cycles,"body":body}
        )

        return {
            "version": 1, "name": name,
            "config": {
                "probe_step_mm": 1.0, "probe_max_depth_mm": 200.0,
                "probe_threshold_mm": 5.0, "default_speed_pct": 70,
            },
            "program": [
                {"op":"HOME","axes":["X","Y","Z"]},
                loop,
                {"op":"LOG","message":"Job complete"},
                {"op":"HALT"},
            ],
        }

    def _generate(self):
        try:
            prog = self._build_program()
            self._editor.setPlainText(json.dumps(prog, indent=2))
            cycle_str = ("∞ (run until empty)"
                         if self._f_cycles.value() == 0
                         else f"{self._f_cycles.value()} cycle(s)")
            self._set_status(
                f"Generated — {self._table.rowCount()} waypoints, {cycle_str}", "ok")
        except Exception as e:
            self._set_status(str(e), "error")

    # -----------------------------------------------------------------------
    # File / machine
    # -----------------------------------------------------------------------

    def _validate(self):
        text = self._editor.toPlainText().strip()
        if not text:
            self._set_status("JSON editor is empty", "warning"); return
        try:
            prog = json.loads(text)
        except json.JSONDecodeError as e:
            self._set_status(f"Invalid JSON: {e}", "error"); return
        try:
            from interpreter import ProgramValidator
            ok, err = ProgramValidator().validate(prog)
        except ImportError:
            self._set_status("interpreter.py not found", "warning"); return
        self._set_status("✓  Valid" if ok else f"✗  {err}",
                         "ok" if ok else "error")

    def _upload(self):
        text = self._editor.toPlainText().strip()
        if not text:
            self._set_status("Nothing to upload", "warning"); return
        try:
            prog = json.loads(text)
        except json.JSONDecodeError as e:
            self._set_status(f"Invalid JSON: {e}", "error"); return
        try:
            from interpreter import ProgramValidator
            ok, err = ProgramValidator().validate(prog)
            if not ok:
                self._set_status(f"Validation failed: {err}", "error"); return
        except ImportError:
            pass
        self._set_status("Uploading…", "info")
        self.command_requested.emit({"cmd": "load_program", "program": prog})

    def _load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Program", "", "JSON files (*.json)")
        if not path: return
        try:
            with open(path, encoding="utf-8") as f:
                prog = json.load(f)
            self._editor.setPlainText(json.dumps(prog, indent=2))
            self._set_status(f"Loaded: {os.path.basename(path)}", "info")
        except Exception as e:
            self._set_status(f"Error: {e}", "error")

    def _save_file(self):
        text = self._editor.toPlainText().strip()
        if not text:
            self._set_status("Nothing to save", "warning"); return
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            self._set_status(f"Invalid JSON: {e}", "error"); return
        default = parsed.get("name","program").replace(" ","_") + ".json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Program", default, "JSON files (*.json)")
        if not path: return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(parsed, f, indent=2)
            self._set_status(f"Saved: {os.path.basename(path)}", "ok")
        except Exception as e:
            self._set_status(f"Save error: {e}", "error")

    # -----------------------------------------------------------------------
    # Called from MainWindow
    # -----------------------------------------------------------------------

    def set_content(self, program: dict):
        self._editor.setPlainText(json.dumps(program, indent=2))

    def on_program(self, program: dict):
        self.set_content(program)
        self._set_status(f"Retrieved: '{program.get('name','?')}'", "ok")

    def on_upload_ack(self, msg: dict):
        self._set_status(
            f"✓  Uploaded — {msg.get('instructions','?')} instructions", "ok")

    def on_upload_nack(self, detail: str):
        self._set_status(f"✗  Rejected: {detail}", "error")

    def set_connected(self, connected: bool):
        self._connected = connected
        self._btn_upload.setEnabled(connected)

    def _set_status(self, msg: str, level: str = "info"):
        color = self._STATUS_COLORS.get(level, "#7f8c8d")
        self._status_lbl.setText(msg)
        self._status_lbl.setStyleSheet(f"color:{color};")


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------

CATEGORY_COLORS = {
    "USER":    "#2980b9",   # blue
    "PROGRAM": "#27ae60",   # green
    "STATE":   "#8e44ad",   # purple
    "FAULT":   "#e74c3c",   # red
    "SYSTEM":  "#7f8c8d",   # gray
}


class EventLog(QWidget):
    """Timestamped, categorised, saveable event log."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list = []   # (iso_ts, display_ts, category, message)
        self._active_filter = "ALL"
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(6)

        # Toolbar
        bar = QHBoxLayout()
        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["ALL", "USER", "PROGRAM", "STATE",
                                     "FAULT", "SYSTEM"])
        self._filter_combo.currentTextChanged.connect(self._apply_filter)
        bar.addWidget(QLabel("Filter:"))
        bar.addWidget(self._filter_combo)
        bar.addStretch()
        clear_btn = QPushButton("Clear")
        save_btn  = QPushButton("Save Log…")
        clear_btn.clicked.connect(self._clear)
        save_btn.clicked.connect(self._save)
        bar.addWidget(clear_btn)
        bar.addWidget(save_btn)
        root.addLayout(bar)

        self._display = QTextEdit()
        self._display.setReadOnly(True)
        self._display.setFont(QFont("Courier New", 9))
        root.addWidget(self._display)

    # ---- Public API -------------------------------------------------------

    def append(self, category: str, message: str) -> None:
        now         = datetime.now()
        iso_ts      = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        display_ts  = now.strftime("%H:%M:%S.%f")[:-3]
        self._entries.append((iso_ts, display_ts, category, message))
        if self._active_filter in ("ALL", category):
            self._write_line(display_ts, category, message)

    def _write_line(self, display_ts: str, category: str, message: str) -> None:
        color = CATEGORY_COLORS.get(category, "#000000")
        self._display.setTextColor(QColor(color))
        self._display.append(
            f"[{display_ts}]  [{category:<7}]  {message}")
        self._display.moveCursor(QTextCursor.MoveOperation.End)

    # ---- Filter -----------------------------------------------------------

    def _apply_filter(self, filter_val: str) -> None:
        self._active_filter = filter_val
        self._display.clear()
        for iso_ts, display_ts, category, message in self._entries:
            if filter_val in ("ALL", category):
                self._write_line(display_ts, category, message)

    # ---- Clear ------------------------------------------------------------

    def _clear(self) -> None:
        self._entries.clear()
        self._display.clear()

    # ---- Save -------------------------------------------------------------

    def _save(self) -> None:
        default = f"pnp_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Save Event Log", default,
            "Text files (*.txt);;CSV files (*.csv)"
        )
        if not path:
            return
        is_csv = path.lower().endswith(".csv") or "CSV" in selected_filter
        try:
            with open(path, "w", encoding="utf-8") as f:
                if is_csv:
                    f.write("Timestamp,Category,Message\n")
                    for iso_ts, _, category, message in self._entries:
                        safe = message.replace('"', '""')
                        f.write(f'"{iso_ts}","{category}","{safe}"\n')
                else:
                    f.write(f"Pick-and-Place Event Log\n")
                    f.write(f"Exported: {datetime.now().isoformat()}\n")
                    f.write("-" * 60 + "\n")
                    for iso_ts, _, category, message in self._entries:
                        f.write(f"[{iso_ts}]  [{category:<7}]  {message}\n")
        except Exception as exc:
            QMessageBox.warning(self, "Save Error", str(exc))


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
        self._state          = "IDLE"
        self._connected      = False
        self._program_loaded = False
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
        self._lbl_position   = QLabel("—")
        self._lbl_program    = QLabel("None loaded")
        self._lbl_current_op = QLabel("—")
        self._lbl_uptime     = QLabel("—")
        self._lbl_fault      = QLabel("None")
        self._lbl_fault.setStyleSheet("color:green; font-weight:bold;")
        info_form.addRow("Position:", self._lbl_position)
        info_form.addRow("Program:",  self._lbl_program)
        info_form.addRow("Op:",       self._lbl_current_op)
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
        self._btn_load        = _btn("Load Program...")
        self._btn_start       = _btn("Run Program")
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
            b.setStyleSheet(
                f"QPushButton {{ background-color:{c}; color:white; font-weight:bold; }}"
                " QPushButton:disabled { background-color: #bdc3c7; color: #888; font-weight: normal; }")

        self._btn_estop.setMinimumHeight(50)
        f = self._btn_estop.font(); f.setPointSize(14); f.setBold(True)
        self._btn_estop.setFont(f)

        btn_layout.addWidget(self._btn_home,        0, 0)
        btn_layout.addWidget(self._btn_load,        0, 1)
        btn_layout.addWidget(self._btn_start,       0, 2)
        btn_layout.addWidget(self._btn_pause,       0, 3)
        btn_layout.addWidget(self._btn_resume,      0, 4)
        btn_layout.addWidget(self._btn_reset_fault, 1, 0)
        btn_layout.addWidget(self._btn_reset_estop, 1, 1)
        btn_layout.addWidget(self._btn_estop,       0, 5, 2, 1)
        root.addWidget(_group("Controls", btn_layout))
        root.addStretch()

        self._btn_home.clicked.connect(
            lambda: self.command_requested.emit({"cmd": "home"}))
        self._btn_load.clicked.connect(self._load_program_dialog)
        self._btn_start.clicked.connect(
            lambda: self.command_requested.emit({"cmd": "run_program"}))
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

    def _load_program_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Job Program", "", "JSON files (*.json)")
        if not path:
            return
        import json, os
        try:
            with open(path) as f:
                program = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "Load Error", str(e))
            return
        self._pending_program_name = program.get("name", os.path.basename(path))
        self._pending_program      = program   # MainWindow reads this to populate editor
        self.command_requested.emit({"cmd": "load_program", "program": program})

    def on_status(self, msg: dict):
        state = msg.get("state", "IDLE")
        self._state = state
        color = STATE_COLORS.get(state, "#95a5a6")
        self._state_label.setText(state.replace("_", " "))
        self._state_label.setStyleSheet(
            f"background-color:{color}; color:white; border-radius:6px;"
            f"padding:4px; font-size:20pt; font-weight:bold;"
        )
        pos     = msg.get("position_name") or "—"
        secs    = msg.get("uptime_ms", 0) // 1000
        cur_op  = msg.get("current_op") or "—"
        step    = msg.get("step_index")
        self._lbl_position.setText(pos)
        self._lbl_current_op.setText(
            f"{cur_op}  (step {step})" if step is not None else cur_op)
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

    def set_program(self, name: str) -> None:
        """Called by MainWindow when load_program is ACKed."""
        self._program_loaded = True
        self._lbl_program.setText(name)
        self._update_buttons()

    def force_state(self, state: str) -> None:
        """Immediately apply a state and update buttons/banner without
        waiting for the next periodic status message."""
        self._state = state
        color = STATE_COLORS.get(state, '#95a5a6')
        self._state_label.setText(state.replace('_', ' '))
        self._state_label.setStyleSheet(
            f'background-color:{color}; color:white; border-radius:6px;'
            f'padding:4px; font-size:20pt; font-weight:bold;')
        self._update_buttons()

    def _update_buttons(self):
        s, c = self._state, self._connected
        self._btn_home.setEnabled(c and s in BUTTON_STATES["home"])
        self._btn_load.setEnabled(c)
        self._btn_start.setEnabled(
            c and s in BUTTON_STATES["run_program"] and self._program_loaded)
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
            "QPushButton { background-color:#2980b9; color:white; font-weight:bold; }"
            " QPushButton:disabled { background-color: #bdc3c7; color: #888; font-weight: normal; }")
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
        can_io  = self._connected and self._state in ("IDLE", "READY")
        self._btn_go.setEnabled(can_act)
        for btn in self._teach_btns.values():
            btn.setEnabled(can_act or
                           (self._connected and self._state == "IDLE"
                            and btn is self._btn_teach_target))
        # Servo and output buttons only valid in IDLE / READY
        for btn in [
            self._btn_door_open,   self._btn_door_close,
            self._btn_laser_press, self._btn_laser_release,
            self._btn_pump_on,     self._btn_pump_off,
            self._btn_valve_on,    self._btn_valve_off,
        ]:
            btn.setEnabled(can_io)
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
        self._last_state: str    = ""
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
        self._run_tab   = RunTab()
        self._prog_tab  = ProgramEditorTab()
        self._cal_tab   = CalibrationTab()
        self._svc_tab   = ServiceTab()
        self._event_log = EventLog()
        self._tabs.addTab(self._run_tab,   "Run")
        self._tabs.addTab(self._prog_tab,  "Program")
        self._tabs.addTab(self._cal_tab,   "Service")
        self._tabs.addTab(self._svc_tab,   "Comms")
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
            url = self._url_edit.text().strip()
            self._event_log.append("SYSTEM", f"Connected to {url}")
            self._send({"cmd": "query_status"})
            self._send({"cmd": "query_positions"})
            self._send({"cmd": "query_sensors"})
            self._sensor_timer.start(2000)   # poll sensors every 2 s
        else:
            self._event_log.append("SYSTEM", "Disconnected")

    def _on_error(self, msg: str):
        self._sensor_timer.stop()
        self._status_bar.showMessage(f"Error: {msg}")
        self._set_connected(False)
        self._conn_btn.setText("Connect")

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
        "teach_position": None,   # built dynamically below
        "save_position":  None,
    }

    def _send(self, cmd: dict):
        if self._worker and self._worker.isRunning():
            verb = cmd.get("cmd", "")
            if verb in self._LOG_CMDS:
                if verb == "teach_position":
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
            self._run_tab.on_status(msg)
            self._cal_tab.on_status(msg)
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
                self._svc_tab.on_sensors(msg)
            elif cmd == "query_positions":
                self._cal_tab.on_positions(msg)
            elif cmd in ("teach_position", "save_position"):
                self._cal_tab.on_teach_ack()
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
