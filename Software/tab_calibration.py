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
        self._cal_steps    = 0
        self._cal_axis     = ""
        self._steps_per_mm = {"X": 0.0, "Y": 0.0, "Z": 0.0}

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

        # Trigger row
        trig_row = QHBoxLayout()
        trig_row.setSpacing(8)
        trig_row.addWidget(QLabel("Axis:"))
        self._cal_axis_combo = QComboBox()
        self._cal_axis_combo.addItems(["X", "Y", "Z"])
        self._cal_axis_combo.setFixedWidth(60)
        trig_row.addWidget(self._cal_axis_combo)
        self._btn_calibrate_stepper = _btn("Calibrate Axis", min_width=130)
        self._btn_calibrate_stepper.setToolTip(
            "Home the axis, drive to far hard stop counting steps.\n"
            "You will be prompted to enter the actual travel distance.")
        self._btn_calibrate_stepper.clicked.connect(self._start_stepper_cal)
        trig_row.addWidget(self._btn_calibrate_stepper)
        trig_row.addStretch()
        v.addLayout(trig_row)

        # Distance-entry panel (hidden until traverse complete)
        self._dist_frame = QFrame()
        self._dist_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self._dist_frame.setStyleSheet(
            "QFrame { background:#fef9e7; border:1px solid #f39c12; border-radius:4px; }")
        dist_v = QVBoxLayout(self._dist_frame)
        dist_v.setSpacing(6)
        self._cal_info_lbl = QLabel("")
        self._cal_info_lbl.setWordWrap(True)
        self._cal_info_lbl.setStyleSheet("font-weight:bold;")
        dist_v.addWidget(self._cal_info_lbl)
        dist_v.addWidget(QLabel(
            "Measure the actual travel distance with calipers and enter it below."))
        dist_row = QHBoxLayout()
        dist_row.addWidget(QLabel("Travel distance (mm):"))
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
        dist_v.addLayout(dist_row)
        self._dist_frame.setVisible(False)
        v.addWidget(self._dist_frame)

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
        """Track CALIBRATING state for the stepper distance-entry panel."""
        state     = msg.get("state", "IDLE")
        cal_steps = msg.get("cal_steps") or 0
        cal_axis  = msg.get("cal_axis")  or ""

        if state == "CALIBRATING" and cal_steps > 0 and not self._dist_frame.isVisible():
            self._cal_steps = cal_steps
            self._cal_axis  = cal_axis
            self._cal_info_lbl.setText(
                f"Traverse complete: {cal_steps:,} steps on {cal_axis} axis.")
            self._dist_frame.setVisible(True)
        elif state != "CALIBRATING":
            self._dist_frame.setVisible(False)
            self._cal_steps = 0
            self._cal_axis  = ""

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
        self.command_requested.emit({"cmd": "calibrate_axis", "axis": axis})

    def _apply_cal_distance(self):
        dist = self._dist_spin.value()
        axis = self._cal_axis or self._cal_axis_combo.currentText()
        self.command_requested.emit({
            "cmd":  "set_cal_distance",
            "axis": axis,
            "mm":   dist,
        })
        if self._cal_steps > 0 and dist > 0:
            self.set_steps_per_mm(axis, self._cal_steps / dist)

    def _update_spm_display(self):
        for axis, lbl in self._spm_labels.items():
            val = self._steps_per_mm.get(axis, 0.0)
            if val > 0:
                lbl.setText(f"{val:.2f} steps/mm")
                lbl.setStyleSheet("font-weight:bold; font-size:13px; color:#27ae60;")
            else:
                lbl.setText("Not calibrated")
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
        self._btn_apply_dist.setEnabled(
            calibrating and self._cal_steps > 0 and self._connected)
        self._btn_cal_sensors.setEnabled(can_cal)
        self._btn_refresh_sensors.setEnabled(self._connected)
