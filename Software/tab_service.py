from gui_common import *
from gui_common import _btn, _group, _label, _style_btn
from PyQt6.QtCore import QTimer


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
            f"border:1px solid rgba(0,0,0,0.25);")

    def set_bool(self, value: bool, true_color="green", false_color="gray"):
        self.set_color(true_color if value else false_color)


# ---------------------------------------------------------------------------
# Service tab — position control, named positions, servos, outputs
# ---------------------------------------------------------------------------

class ServiceTab(QWidget):
    command_requested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected       = False
        self._state           = "IDLE"
        self._stored_pos      = {n: (0.0, 0.0, 0.0) for n in NAMED_POSITIONS}
        self._current_outputs = {}
        self._cur_x = 0.0
        self._cur_y = 0.0
        self._cur_z = 0.0
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        top  = QHBoxLayout()
        top.setSpacing(8)
        left  = QVBoxLayout()
        right = QVBoxLayout()
        top.addLayout(left,  1)
        top.addLayout(right, 1)
        root.addLayout(top)

        # ---- Current position ----
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

        # ---- Target position ----
        self._tgt = {"X": 0.0, "Y": 0.0, "Z": 0.0}
        self._tgt_labels = {}
        tgt_grid = QGridLayout()
        tgt_grid.setSpacing(6)
        for row, axis in enumerate(["X", "Y", "Z"]):
            val_lbl = QLabel("0.00")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val_lbl.setMinimumWidth(70)
            val_lbl.setStyleSheet(
                "border:1px solid #bdc3c7; border-radius:3px; padding:2px;"
                " background:white; color:#111;")
            self._tgt_labels[axis] = val_lbl
            btn_dec = _btn("−", min_width=32)
            btn_inc = _btn("+", min_width=32)
            btn_dec.clicked.connect(lambda _, a=axis: self._nudge_target(a, -1))
            btn_inc.clicked.connect(lambda _, a=axis: self._nudge_target(a, +1))
            tgt_grid.addWidget(QLabel(f"{axis}:"), row, 0)
            tgt_grid.addWidget(btn_dec,            row, 1)
            tgt_grid.addWidget(val_lbl,            row, 2)
            tgt_grid.addWidget(btn_inc,            row, 3)

        step_row = QHBoxLayout()
        step_row.addWidget(QLabel("Step (mm):"))
        self._step_combo = QComboBox()
        self._step_combo.addItems(["0.01", "0.1", "1.0", "5.0", "10.0", "50.0"])
        self._step_combo.setCurrentIndex(2)
        self._step_combo.setFixedWidth(70)
        step_row.addWidget(self._step_combo)
        step_row.addStretch()

        act_row = QHBoxLayout()
        self._btn_go = _btn("Go to Target", min_width=120)
        self._btn_go.setStyleSheet(
            "QPushButton { background-color:#2980b9; color:white; font-weight:bold; }"
            " QPushButton:disabled { background-color:#bdc3c7; color:#888; font-weight:normal; }")
        btn_reset = _btn("Reset to Current", min_width=120)
        btn_reset.clicked.connect(self._reset_target_to_current)
        self._btn_go.clicked.connect(self._go_to_target)
        act_row.addWidget(self._btn_go)
        act_row.addWidget(btn_reset)
        act_row.addStretch()

        self._hint_label = QLabel("")
        self._hint_label.setStyleSheet("color:#e67e22; font-style:italic;")
        self._hint_label.setWordWrap(True)

        tgt_v = QVBoxLayout()
        tgt_v.addLayout(tgt_grid)
        tgt_v.addLayout(step_row)
        tgt_v.addLayout(act_row)
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
            in_grid.addWidget(light,         0, col * 2, Qt.AlignmentFlag.AlignCenter)
            in_grid.addWidget(QLabel(label), 0, col * 2 + 1)
        left.addWidget(_group("Inputs", in_grid))

        # ---- Live ToF readout ----
        self._tof_rows = QTableWidget(6, 3)
        self._tof_rows.setHorizontalHeaderLabels(["Sensor", "Distance", "Status"])
        self._tof_rows.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._tof_rows.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._tof_rows.verticalHeader().setVisible(False)
        self._tof_rows.setMaximumHeight(196)
        for row in range(6):
            self._tof_rows.setItem(row, 0, QTableWidgetItem(TOF_PURPOSES[row]))
            self._tof_rows.setItem(row, 1, QTableWidgetItem("—"))
            self._tof_rows.setItem(row, 2, QTableWidgetItem("—"))
        tof_box = QVBoxLayout()
        tof_box.addWidget(self._tof_rows)
        left.addWidget(_group("ToF Sensors (live)", tof_box))
        left.addStretch()

        # ---- Named positions ----
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

        teach_row = QHBoxLayout()
        teach_row.addWidget(QLabel("Save as:"))
        self._teach_combo = QComboBox()
        self._teach_combo.addItems(NAMED_POSITIONS)
        self._teach_combo.setFixedWidth(90)
        teach_row.addWidget(self._teach_combo)
        self._btn_teach_current = _btn("Teach Current")
        self._btn_teach_target  = _btn("Teach Target")
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
        self._btn_door_open   = _btn("Open",    min_width=80)
        self._btn_door_close  = _btn("Close",   min_width=80)
        servo_grid.addWidget(self._btn_door_open,  0, 1)
        servo_grid.addWidget(self._btn_door_close, 0, 2)
        servo_grid.addWidget(_label("Laser Btn:"), 1, 0)
        self._btn_laser_press   = _btn("Press",   min_width=80)
        self._btn_laser_release = _btn("Release", min_width=80)
        servo_grid.addWidget(self._btn_laser_press,   1, 1)
        servo_grid.addWidget(self._btn_laser_release, 1, 2)
        self._btn_door_open.clicked.connect(lambda: self._send_servo("door", "open"))
        self._btn_door_close.clicked.connect(lambda: self._send_servo("door", "closed"))
        self._btn_laser_press.clicked.connect(lambda: self._send_servo("laser_btn", "press"))
        self._btn_laser_release.clicked.connect(lambda: self._send_servo("laser_btn", "release"))
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
        out_grid.addWidget(_label("Beeper:"), 2, 0)
        self._btn_beep = _btn("Beep", min_width=80)
        out_grid.addWidget(self._btn_beep, 2, 1)
        self._btn_pump_on.clicked.connect(lambda: self._send_output("pump", True))
        self._btn_pump_off.clicked.connect(lambda: self._send_output("pump", False))
        self._btn_valve_on.clicked.connect(lambda: self._send_output("valve", True))
        self._btn_valve_off.clicked.connect(lambda: self._send_output("valve", False))
        self._btn_beep.clicked.connect(self._beep)
        right.addWidget(_group("Output Test", out_grid))
        right.addStretch()

        self._apply_output_states({})

    # ---- Data update methods ----

    def on_status(self, msg: dict):
        self._state = msg.get("state", "IDLE")
        self._cur_x = float(msg.get("x_mm", 0.0))
        self._cur_y = float(msg.get("y_mm", 0.0))
        self._cur_z = float(msg.get("z_mm", 0.0))
        self._lbl_x.setText(f"{self._cur_x:.2f}")
        self._lbl_y.setText(f"{self._cur_y:.2f}")
        self._lbl_z.setText(f"{self._cur_z:.2f}")
        self._lbl_name.setText(msg.get("position_name") or "—")
        # Inputs (button dots) and outputs (button highlights) ride in status.
        inputs = msg.get("inputs", {})
        for key, (light, t_col, f_col) in self._input_lights.items():
            light.set_bool(inputs.get(key, False), t_col, f_col)
        self._current_outputs = msg.get("outputs", {})
        self._apply_output_states(self._current_outputs)
        self._update_controls()

    def on_sensors(self, msg: dict):
        # Live ToF distances from query_sensors: [{ch, dist_mm, valid}, ...].
        for entry in msg.get("tof", []):
            ch = entry.get("ch")
            if ch is None or not (0 <= ch < 6):
                continue
            valid = bool(entry.get("valid", False))
            dist  = entry.get("dist_mm")
            if valid and dist is not None:
                self._tof_rows.item(ch, 1).setText(f"{float(dist):.0f} mm")
                self._tof_rows.item(ch, 2).setText("OK")
                self._tof_rows.item(ch, 2).setForeground(QColor("#27ae60"))
            else:
                self._tof_rows.item(ch, 1).setText("—")
                self._tof_rows.item(ch, 2).setText("no reading")
                self._tof_rows.item(ch, 2).setForeground(QColor("#e67e22"))

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

    # ---- Helpers ----

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
        pump  = outputs.get("pump",  False)
        valve = outputs.get("valve", False)
        _style_btn(self._btn_door_open,     door  == "open",    "door_open")
        _style_btn(self._btn_door_close,    door  == "closed",  "door_closed")
        _style_btn(self._btn_laser_press,   laser == "press",   "laser_press")
        _style_btn(self._btn_laser_release, laser == "release", "laser_release")
        _style_btn(self._btn_pump_on,       pump  is True,      "pump_on")
        _style_btn(self._btn_pump_off,      pump  is False,     "pump_off")
        _style_btn(self._btn_valve_on,      valve is True,      "valve_on")
        _style_btn(self._btn_valve_off,     valve is False,     "valve_off")

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
            "cmd": "move_to",
            "x_mm": self._tgt["X"], "y_mm": self._tgt["Y"], "z_mm": self._tgt["Z"],
        })

    def _teach_current(self):
        self.command_requested.emit({
            "cmd": "teach_position", "name": self._teach_combo.currentText()})

    def _teach_target(self):
        name = self._teach_combo.currentText()
        self.command_requested.emit({
            "cmd": "save_position", "name": name,
            "x_mm": self._tgt["X"], "y_mm": self._tgt["Y"], "z_mm": self._tgt["Z"],
        })

    def _send_servo(self, servo: str, position: str):
        self._current_outputs[f"servo_{servo}"] = position
        self._apply_output_states(self._current_outputs)
        self.command_requested.emit({"cmd": "set_servo", "servo": servo, "position": position})

    def _send_output(self, output: str, state: bool):
        self._current_outputs[output] = state
        self._apply_output_states(self._current_outputs)
        self.command_requested.emit({"cmd": "set_output", "output": output, "state": state})

    def _beep(self):
        # Momentary: on now, off after 300 ms.
        self.command_requested.emit({"cmd": "set_output", "output": "beeper", "state": True})
        QTimer.singleShot(300, lambda: self.command_requested.emit(
            {"cmd": "set_output", "output": "beeper", "state": False}))

    def _update_controls(self):
        can_act = self._connected and self._state == "READY"
        can_io  = self._connected and self._state in ("IDLE", "READY")
        self._btn_go.setEnabled(can_act)
        for btn in self._teach_btns.values():
            btn.setEnabled(can_act or
                           (self._connected and self._state == "IDLE"
                            and btn is self._btn_teach_target))
        for btn in [
            self._btn_door_open, self._btn_door_close,
            self._btn_laser_press, self._btn_laser_release,
            self._btn_pump_on, self._btn_pump_off,
            self._btn_valve_on, self._btn_valve_off,
            self._btn_beep,
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
# Comms tab — ToF sensor detail + serial log
# ---------------------------------------------------------------------------

class CommsTab(QWidget):
    command_requested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

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

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Courier New", 9))
        log_v = QVBoxLayout()
        log_btns = QHBoxLayout()
        clear_btn = _btn("Clear", min_width=60)
        clear_btn.clicked.connect(self._log.clear)
        export_btn = _btn("Export…", min_width=70)
        export_btn.clicked.connect(self._export)
        log_btns.addWidget(clear_btn)
        log_btns.addWidget(export_btn)
        log_btns.addStretch(1)
        log_v.addLayout(log_btns)
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

    def _export(self):
        default = f"pnp_comms_{time.strftime('%Y%m%d_%H%M%S')}.log"
        path, _sel = QFileDialog.getSaveFileName(
            self, "Export Communications Log", default,
            "Log files (*.log *.txt);;All files (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._log.toPlainText())
        except Exception as exc:
            QMessageBox.warning(self, "Export Failed", str(exc))