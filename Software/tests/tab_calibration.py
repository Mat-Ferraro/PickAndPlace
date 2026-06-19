from gui_common import *
from gui_common import _btn, _group, _label

# ---------------------------------------------------------------------------
# Calibration tab — stepper steps/mm + ToF sensor offset calibration
# ---------------------------------------------------------------------------

_ARM_CHANNELS    = [0, 1, 2, 3]
_CHANNEL_LABELS  = [
    "Ch 0  (Pickup corner 1)",
    "Ch 1  (Pickup corner 2)",
    "Ch 2  (Pickup corner 3)",
    "Ch 3  (Pickup corner 4)",
]
_STATUS_CHANNELS = [4, 5]
_STATUS_LABELS   = ["Ch 4  Laser parked", "Ch 5  Paper present"]
_STATUS_THRESH   = 100   # mm — below this → sensor is "active"


class CalibrationTab(QWidget):
    """
    Unified calibration tab.

    Stepper section: automated steps/mm calibration via StallGuard traverse.
    Sensor section:  ToF offset baseline calibration for arm pickup sensors.
    """
    command_requested = pyqtSignal(dict)

    DEFAULT_TOUCH_THRESH_MM = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False

        # ---- stepper cal state ----
        self._cal_jog_steps = 0          # net steps jogged this session (from status)
        self._cal_axis      = ""
        self._steps_per_mm  = {"X": 0.0, "Y": 0.0, "Z": 0.0}
        self._max_travel    = {"X": 0.0, "Y": 0.0, "Z": 0.0}

        # ---- sensor cal state ----
        self._live      = [0] * 6
        self._valid     = [False] * 6
        self._baselines = [None] * 4

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setSpacing(10)
        v.addWidget(self._build_stepper_group())
        v.addWidget(self._build_sensor_arm_group())
        v.addWidget(self._build_sensor_cal_group())
        v.addWidget(self._build_sensor_status_group())
        v.addStretch()

        scroll.setWidget(inner)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    # ---- Stepper calibration ----------------------------------------

    def _build_stepper_group(self) -> QWidget:
        v = QVBoxLayout()
        v.setSpacing(8)

        # Current steps/mm per axis
        vals_row = QHBoxLayout()
        vals_row.setSpacing(20)
        self._spm_labels: dict[str, QLabel] = {}
        for axis in ("X", "Y", "Z"):
            lbl = QLabel("—")
            lbl.setStyleSheet("font-weight:bold; font-size:13px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col = QVBoxLayout()
            col.addWidget(QLabel(f"{axis} axis"), alignment=Qt.AlignmentFlag.AlignCenter)
            col.addWidget(lbl,                    alignment=Qt.AlignmentFlag.AlignCenter)
            self._spm_labels[axis] = lbl
            vals_row.addLayout(col)
        vals_row.addStretch()
        v.addLayout(vals_row)
        self._update_spm_display()

        # Trigger row — start jog-and-measure calibration for one axis
        trig_row = QHBoxLayout()
        trig_row.setSpacing(8)
        trig_row.addWidget(QLabel("Calibrate axis:"))
        self._cal_axis_combo = QComboBox()
        self._cal_axis_combo.addItems(["X", "Y", "Z"])
        self._cal_axis_combo.setFixedWidth(60)
        trig_row.addWidget(self._cal_axis_combo)
        self._btn_calibrate_stepper = _btn("Start Calibration", min_width=140)
        self._btn_calibrate_stepper.setToolTip(
            "Enter calibration for this axis, then jog it a known distance\n"
            "and enter the measured travel. steps/mm = |steps jogged| / mm.")
        self._btn_calibrate_stepper.clicked.connect(self._start_stepper_cal)
        trig_row.addWidget(self._btn_calibrate_stepper)
        trig_row.addStretch()
        v.addLayout(trig_row)

        # Jog-and-measure panel (visible only while CALIBRATING)
        self._cal_frame = QFrame()
        self._cal_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self._cal_frame.setStyleSheet(
            "QFrame { background:#fef9e7; border:1px solid #f39c12; border-radius:4px; }")
        cal_v = QVBoxLayout(self._cal_frame)
        cal_v.setSpacing(6)

        self._cal_info_lbl = QLabel("")
        self._cal_info_lbl.setWordWrap(True)
        self._cal_info_lbl.setStyleSheet("font-weight:bold;")
        cal_v.addWidget(self._cal_info_lbl)

        self._jogged_lbl = QLabel("Jogged: 0 steps")
        self._jogged_lbl.setStyleSheet("font-size:13px;")
        cal_v.addWidget(self._jogged_lbl)

        # Jog controls — send raw step counts; net is accumulated firmware-side
        jog_row = QHBoxLayout()
        jog_row.addWidget(QLabel("Jog amount (steps):"))
        self._jog_spin = QSpinBox()
        self._jog_spin.setRange(1, 100000)
        self._jog_spin.setValue(1000)
        self._jog_spin.setSingleStep(100)
        self._jog_spin.setFixedWidth(100)
        jog_row.addWidget(self._jog_spin)
        self._btn_jog_neg = _btn("Jog −", min_width=80)
        self._btn_jog_neg.clicked.connect(lambda: self._jog(-1))
        jog_row.addWidget(self._btn_jog_neg)
        self._btn_jog_pos = _btn("Jog +", min_width=80)
        self._btn_jog_pos.clicked.connect(lambda: self._jog(+1))
        jog_row.addWidget(self._btn_jog_pos)
        jog_row.addStretch()
        cal_v.addLayout(jog_row)

        cal_v.addWidget(QLabel(
            "Measure the actual travel with calipers and enter it below."))
        dist_row = QHBoxLayout()
        dist_row.addWidget(QLabel("Measured distance (mm):"))
        self._dist_spin = QDoubleSpinBox()
        self._dist_spin.setRange(1.0, 2000.0)
        self._dist_spin.setDecimals(1)
        self._dist_spin.setValue(100.0)
        self._dist_spin.setFixedWidth(100)
        dist_row.addWidget(self._dist_spin)
        self._btn_apply_dist = _btn("Apply", min_width=80)
        self._btn_apply_dist.setStyleSheet(
            "QPushButton { background-color:#27ae60; color:white; font-weight:bold; }"
            " QPushButton:disabled { background-color:#bdc3c7; color:#888; }")
        self._btn_apply_dist.clicked.connect(self._apply_cal_distance)
        dist_row.addWidget(self._btn_apply_dist)
        dist_row.addStretch()
        cal_v.addLayout(dist_row)
        self._cal_frame.setVisible(False)
        v.addWidget(self._cal_frame)

        # ---- Soft travel limits ----
        v.addWidget(_label("Soft travel limits", bold=True))
        lim_vals = QHBoxLayout()
        lim_vals.setSpacing(20)
        self._travel_labels: dict[str, QLabel] = {}
        for axis in ("X", "Y", "Z"):
            lbl = QLabel("—")
            lbl.setStyleSheet("font-weight:bold; font-size:13px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col = QVBoxLayout()
            col.addWidget(QLabel(f"{axis} max"), alignment=Qt.AlignmentFlag.AlignCenter)
            col.addWidget(lbl,                   alignment=Qt.AlignmentFlag.AlignCenter)
            self._travel_labels[axis] = lbl
            lim_vals.addLayout(col)
        lim_vals.addStretch()
        v.addLayout(lim_vals)
        self._update_max_travel_display()

        lim_row = QHBoxLayout()
        lim_row.setSpacing(8)
        lim_row.addWidget(QLabel("Set max travel:"))
        self._travel_axis_combo = QComboBox()
        self._travel_axis_combo.addItems(["X", "Y", "Z"])
        self._travel_axis_combo.setFixedWidth(60)
        lim_row.addWidget(self._travel_axis_combo)
        self._travel_spin = QDoubleSpinBox()
        self._travel_spin.setRange(1.0, 2000.0)
        self._travel_spin.setDecimals(1)
        self._travel_spin.setValue(200.0)
        self._travel_spin.setFixedWidth(100)
        lim_row.addWidget(self._travel_spin)
        self._btn_set_travel = _btn("Set Limit", min_width=100)
        self._btn_set_travel.setToolTip(
            "Set the per-axis soft travel limit (mm). A MOVE outside\n"
            "[0, max] faults the machine (soft_limit_<axis>).")
        self._btn_set_travel.clicked.connect(self._set_max_travel)
        lim_row.addWidget(self._btn_set_travel)
        lim_row.addStretch()
        v.addLayout(lim_row)

        return _group("Stepper Calibration", v)

    # ---- Sensor calibration -----------------------------------------

    def _build_sensor_arm_group(self) -> QWidget:
        v = QVBoxLayout()

        self._arm_table = QTableWidget(4, 5)
        self._arm_table.setHorizontalHeaderLabels(
            ["Channel", "Live (mm)", "Baseline (mm)", "Clearance (mm)", "Status"])
        self._arm_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        for col in (1, 2, 3, 4):
            self._arm_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.Stretch)
        self._arm_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._arm_table.verticalHeader().setVisible(False)
        self._arm_table.setMinimumHeight(140)

        for row, label in enumerate(_CHANNEL_LABELS):
            self._arm_table.setItem(row, 0, QTableWidgetItem(label))
            for col in (1, 2, 3, 4):
                self._arm_table.setItem(row, col, QTableWidgetItem("—"))

        thresh_row = QHBoxLayout()
        thresh_row.addWidget(QLabel("Touch threshold (mm):"))
        self._thresh_spin = QSpinBox()
        self._thresh_spin.setRange(1, 50)
        self._thresh_spin.setValue(self.DEFAULT_TOUCH_THRESH_MM)
        self._thresh_spin.setFixedWidth(70)
        self._thresh_spin.valueChanged.connect(self._refresh_sensor_table)
        thresh_row.addWidget(self._thresh_spin)
        self._btn_refresh_sensors = _btn("Refresh Readings", min_width=140)
        self._btn_refresh_sensors.setToolTip("Send query_sensors to update live readings")
        self._btn_refresh_sensors.clicked.connect(
            lambda: self.command_requested.emit({"cmd": "query_sensors"}))
        thresh_row.addWidget(self._btn_refresh_sensors)
        thresh_row.addStretch()

        v.addWidget(self._arm_table)
        v.addLayout(thresh_row)
        return _group("Arm Sensors — Pickup Detection (ch0–ch3)", v)

    def _build_sensor_cal_group(self) -> QWidget:
        v = QVBoxLayout()
        v.setSpacing(8)
        instructions = QLabel(
            "Calibration procedure:\n"
            "  1.  Make sure nothing is in contact with the arm pickup face.\n"
            "  2.  Press a flat surface firmly against all 4 pickup holes\n"
            "       (e.g. a sheet of flat card stock or a small board).\n"
            "  3.  Hold it in place and click  Read Baseline.\n\n"
            "Clearance = live − baseline.  Clearance ≈ 0  →  something is touching."
        )
        instructions.setWordWrap(True)
        v.addWidget(instructions)
        action_row = QHBoxLayout()
        self._btn_cal_sensors = _btn("Read Baseline", min_width=130)
        self._btn_cal_sensors.setStyleSheet(
            "QPushButton { background-color:#2980b9; color:white; font-weight:bold; }"
            " QPushButton:disabled { background-color:#bdc3c7; color:#888; }")
        self._btn_cal_sensors.clicked.connect(self._read_sensor_baseline)
        action_row.addWidget(self._btn_cal_sensors)
        self._sensor_cal_status_lbl = QLabel("Not calibrated")
        self._sensor_cal_status_lbl.setStyleSheet("color:#e67e22; font-style:italic;")
        action_row.addWidget(self._sensor_cal_status_lbl)
        action_row.addStretch()
        v.addLayout(action_row)
        return _group("Sensor Calibration", v)

    def _build_sensor_status_group(self) -> QWidget:
        grid = QGridLayout()
        grid.setSpacing(8)
        self._status_lbl: dict[int, tuple] = {}
        self._status_ind: dict[int, QLabel] = {}
        for row, (ch, label) in enumerate(zip(_STATUS_CHANNELS, _STATUS_LABELS)):
            ind = QLabel("●")
            ind.setFixedWidth(20)
            ind.setAlignment(Qt.AlignmentFlag.AlignCenter)
            dist_lbl  = QLabel("—")
            state_lbl = QLabel("—")
            state_lbl.setStyleSheet("font-weight:bold;")
            grid.addWidget(QLabel(label + ":"), row, 0)
            grid.addWidget(dist_lbl,            row, 1)
            grid.addWidget(ind,                 row, 2)
            grid.addWidget(state_lbl,           row, 3)
            self._status_lbl[ch] = (dist_lbl, state_lbl)
            self._status_ind[ch] = ind
        note = QLabel(
            "These sensors act as presence switches — distance calibration not needed.")
        note.setStyleSheet("color:#7f8c8d; font-style:italic;")
        note.setWordWrap(True)
        v = QVBoxLayout()
        v.addLayout(grid)
        v.addWidget(note)
        return _group("Status Sensors (ch4–ch5) — No Calibration Needed", v)

    # ------------------------------------------------------------------
    # Public data-update methods
    # ------------------------------------------------------------------

    def on_status(self, msg: dict):
        """Drive the jog-and-measure panel from the CALIBRATING broadcast."""
        state = msg.get("state", "IDLE")

        if state == "CALIBRATING":
            self._cal_jog_steps = msg.get("cal_steps") or 0
            self._cal_axis      = msg.get("cal_axis") or self._cal_axis
            self._cal_info_lbl.setText(
                f"Calibrating {self._cal_axis} axis — jog a known distance, "
                f"then enter the measured travel below.")
            self._jogged_lbl.setText(f"Jogged: {self._cal_jog_steps:,} steps")
            self._cal_frame.setVisible(True)
        else:
            self._cal_frame.setVisible(False)
            self._cal_jog_steps = 0
            self._cal_axis      = ""

        self._update_controls(state)

    def on_sensors(self, msg: dict):
        for entry in msg.get("tof", []):
            ch = entry.get("ch", -1)
            if 0 <= ch < 6:
                self._live[ch]  = entry.get("dist_mm", 0)
                self._valid[ch] = entry.get("valid", False)
        self._refresh_sensor_table()
        self._refresh_status_sensors()

    def on_cal_ack(self, offsets: list):
        """Called after a successful calibrate_sensors ack."""
        for i, val in enumerate(offsets[:4]):
            self._baselines[i] = val
        self._refresh_sensor_table()
        vals_str = ", ".join(f"{v} mm" for v in offsets[:4])
        self._sensor_cal_status_lbl.setText(f"Calibrated — baselines: {vals_str}")
        self._sensor_cal_status_lbl.setStyleSheet(
            "color:#27ae60; font-weight:bold;")

    def set_steps_per_mm(self, axis: str, value: float):
        if axis in self._steps_per_mm:
            self._steps_per_mm[axis] = value
            self._update_spm_display()

    def set_max_travel_value(self, axis: str, value: float):
        if axis in self._max_travel:
            self._max_travel[axis] = value
            self._update_max_travel_display()

    def set_baseline(self, channel: int, value: float):
        if 0 <= channel < 4:
            self._baselines[channel] = value
            self._update_sensor_cal_status_label()
            self._refresh_sensor_table()

    def set_connected(self, connected: bool):
        self._connected = connected
        self._update_controls("IDLE" if not connected else None)

    # ------------------------------------------------------------------
    # Internal helpers — stepper
    # ------------------------------------------------------------------

    def _start_stepper_cal(self):
        axis = self._cal_axis_combo.currentText()
        self._cal_axis = axis
        self.command_requested.emit({"cmd": "calibrate_axis", "axis": axis})

    def _jog(self, sign: int):
        steps = sign * self._jog_spin.value()
        axis  = self._cal_axis or self._cal_axis_combo.currentText()
        self.command_requested.emit({"cmd": "cal_jog", "axis": axis, "steps": steps})

    def _apply_cal_distance(self):
        dist = self._dist_spin.value()
        axis = self._cal_axis or self._cal_axis_combo.currentText()
        self.command_requested.emit({
            "cmd":  "set_cal_distance",
            "axis": axis,
            "mm":   dist,
        })
        # Optimistic local update; the authoritative value is re-queried on ack.
        if self._cal_jog_steps != 0 and dist > 0:
            self.set_steps_per_mm(axis, abs(self._cal_jog_steps) / dist)

    def _set_max_travel(self):
        axis = self._travel_axis_combo.currentText()
        mm   = self._travel_spin.value()
        self.command_requested.emit({"cmd": "set_max_travel", "axis": axis, "mm": mm})
        self.set_max_travel_value(axis, mm)   # optimistic; re-queried on ack

    def _update_spm_display(self):
        for axis, lbl in self._spm_labels.items():
            val = self._steps_per_mm.get(axis, 0.0)
            if val > 0:
                lbl.setText(f"{val:.2f} steps/mm")
                lbl.setStyleSheet("font-weight:bold; font-size:13px; color:#27ae60;")
            else:
                lbl.setText("Not calibrated")
                lbl.setStyleSheet("font-weight:bold; font-size:13px; color:#e67e22;")

    def _update_max_travel_display(self):
        for axis, lbl in self._travel_labels.items():
            val = self._max_travel.get(axis, 0.0)
            if val > 0:
                lbl.setText(f"{val:.1f} mm")
                lbl.setStyleSheet("font-weight:bold; font-size:13px; color:#27ae60;")
            else:
                lbl.setText("Not set")
                lbl.setStyleSheet("font-weight:bold; font-size:13px; color:#e67e22;")

    # ------------------------------------------------------------------
    # Internal helpers — sensor
    # ------------------------------------------------------------------

    def _read_sensor_baseline(self):
        self.command_requested.emit({"cmd": "calibrate_sensors"})

    def _refresh_sensor_table(self):
        thresh = self._thresh_spin.value()
        for row, ch in enumerate(_ARM_CHANNELS):
            live     = self._live[ch]
            valid    = self._valid[ch]
            baseline = self._baselines[row]
            live_str = f"{live}" if valid else "invalid"
            base_str = f"{baseline}" if baseline is not None else "—"
            if baseline is not None and valid:
                clearance    = live - baseline
                clear_str    = f"{clearance}"
                status_str   = "● BLOCKED" if clearance <= thresh else "○ CLEAR"
                status_color = "#e74c3c"   if clearance <= thresh else "#27ae60"
            else:
                clear_str    = "—"
                status_str   = "Not calibrated" if baseline is None else "—"
                status_color = "#e67e22"
            self._arm_table.item(row, 1).setText(live_str)
            self._arm_table.item(row, 2).setText(base_str)
            self._arm_table.item(row, 3).setText(clear_str)
            item = self._arm_table.item(row, 4)
            item.setText(status_str)
            item.setForeground(QColor(status_color))

    def _refresh_status_sensors(self):
        for ch in _STATUS_CHANNELS:
            dist  = self._live[ch]
            valid = self._valid[ch]
            dist_lbl, state_lbl = self._status_lbl[ch]
            ind = self._status_ind[ch]
            dist_lbl.setText(f"{dist} mm" if valid else "invalid")
            if not valid:
                state_lbl.setText("—")
                ind.setStyleSheet("color:#95a5a6;")
                continue
            active = dist < _STATUS_THRESH
            if ch == 4:
                state_lbl.setText("PARKED"  if active else "AWAY")
            else:
                state_lbl.setText("PRESENT" if active else "EMPTY")
            color = "#27ae60" if active else "#95a5a6"
            state_lbl.setStyleSheet(f"font-weight:bold; color:{color};")
            ind.setStyleSheet(f"color:{color};")

    def _update_sensor_cal_status_label(self):
        calibrated = [b for b in self._baselines if b is not None]
        if len(calibrated) == 4:
            vals_str = ", ".join(f"{int(v)} mm" for v in self._baselines)
            self._sensor_cal_status_lbl.setText(f"Calibrated — baselines: {vals_str}")
            self._sensor_cal_status_lbl.setStyleSheet(
                "color:#27ae60; font-weight:bold;")
        elif calibrated:
            self._sensor_cal_status_lbl.setText(
                f"Partial — {len(calibrated)}/4 channels calibrated")
            self._sensor_cal_status_lbl.setStyleSheet(
                "color:#e67e22; font-style:italic;")
        else:
            self._sensor_cal_status_lbl.setText("Not calibrated")
            self._sensor_cal_status_lbl.setStyleSheet(
                "color:#e67e22; font-style:italic;")

    def _update_controls(self, state):
        calibrating = state == "CALIBRATING"
        can_cal  = self._connected and state in ("IDLE", "READY")
        self._btn_calibrate_stepper.setEnabled(can_cal)
        self._cal_axis_combo.setEnabled(can_cal)
        # Jog + apply are live only while CALIBRATING
        self._jog_spin.setEnabled(calibrating and self._connected)
        self._btn_jog_neg.setEnabled(calibrating and self._connected)
        self._btn_jog_pos.setEnabled(calibrating and self._connected)
        self._btn_apply_dist.setEnabled(
            calibrating and self._cal_jog_steps != 0 and self._connected)
        # Soft travel limits can be set in IDLE/READY
        self._travel_axis_combo.setEnabled(can_cal)
        self._travel_spin.setEnabled(can_cal)
        self._btn_set_travel.setEnabled(can_cal)
        # Sensor controls
        self._btn_cal_sensors.setEnabled(can_cal)
        self._btn_refresh_sensors.setEnabled(self._connected)