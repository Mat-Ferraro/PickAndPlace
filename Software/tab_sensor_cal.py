from gui_common import *
from gui_common import _btn, _group, _label

# Channels 0-3 are the arm pickup sensors (recessed, require offset calibration).
# Channels 4-5 act as presence booleans and do not need distance calibration.
_ARM_CHANNELS     = [0, 1, 2, 3]
_STATUS_CHANNELS  = [4, 5]
_CHANNEL_LABELS   = [
    "Ch 0  (Pickup corner 1)",
    "Ch 1  (Pickup corner 2)",
    "Ch 2  (Pickup corner 3)",
    "Ch 3  (Pickup corner 4)",
]
_STATUS_LABELS    = [
    "Ch 4  Laser parked",
    "Ch 5  Paper present",
]
_STATUS_THRESH_MM = 100   # ch4/ch5: < this → active (present/parked)


class SensorCalTab(QWidget):
    """
    ToF sensor offset calibration.

    The arm sensors (ch0-ch3) are recessed ~50 mm into the arm body.
    The VL53L0X minimum ranging distance means the 'blocked' reading is not
    zero — it is whatever the sensor reports with a flat surface pressed
    against the hole. By storing this baseline and computing
    clearance = live − baseline, we can detect when the arm is touching
    something (clearance ≈ 0) vs clear (clearance >> 0).
    """

    command_requested = pyqtSignal(dict)

    # Below this clearance the channel is considered "blocked / touching".
    DEFAULT_TOUCH_THRESH_MM = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected  = False
        self._live       = [0] * 6          # raw mm from last query_sensors
        self._valid      = [False] * 6
        self._baselines  = [None] * 4       # stored offset per arm channel
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.addWidget(self._build_arm_group())
        root.addWidget(self._build_cal_group())
        root.addWidget(self._build_status_group())
        root.addStretch()

    def _build_arm_group(self) -> QGroupBox:
        """Live readings table for the four arm pickup sensors."""
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
        self._thresh_spin.valueChanged.connect(self._refresh_table)
        thresh_row.addWidget(self._thresh_spin)
        self._btn_refresh = _btn("Refresh Readings", min_width=140)
        self._btn_refresh.setToolTip("Send query_sensors to update live readings")
        self._btn_refresh.clicked.connect(
            lambda: self.command_requested.emit({"cmd": "query_sensors"}))
        thresh_row.addWidget(self._btn_refresh)
        thresh_row.addStretch()

        v.addWidget(self._arm_table)
        v.addLayout(thresh_row)
        return _group("Arm Sensors — Pickup Detection (ch0–ch3)", v)

    def _build_cal_group(self) -> QGroupBox:
        """Instructions + Read Baseline button."""
        v = QVBoxLayout()
        v.setSpacing(8)

        instructions = QLabel(
            "Calibration procedure:\n"
            "  1.  Make sure nothing is in contact with the arm pickup face.\n"
            "  2.  Press a flat surface firmly against all 4 pickup holes\n"
            "       (e.g. a sheet of flat card stock or a small board).\n"
            "  3.  Hold it in place and click  Read Baseline.\n\n"
            "The stored values represent the 'touching' reading for each sensor.\n"
            "Clearance = live − baseline.  Clearance ≈ 0  →  something is touching."
        )
        instructions.setWordWrap(True)
        v.addWidget(instructions)

        action_row = QHBoxLayout()
        self._btn_cal = _btn("Read Baseline", min_width=130)
        self._btn_cal.setStyleSheet(
            "QPushButton { background-color:#2980b9; color:white; font-weight:bold; }"
            " QPushButton:disabled { background-color:#bdc3c7; color:#888; }")
        self._btn_cal.setToolTip(
            "Read current ch0-ch3 values and store as the blocked/touching baseline")
        self._btn_cal.clicked.connect(self._read_baseline)
        action_row.addWidget(self._btn_cal)
        self._cal_status_lbl = QLabel("Not calibrated")
        self._cal_status_lbl.setStyleSheet("color:#e67e22; font-style:italic;")
        action_row.addWidget(self._cal_status_lbl)
        action_row.addStretch()
        v.addLayout(action_row)

        return _group("Calibration", v)

    def _build_status_group(self) -> QGroupBox:
        """Read-only display for ch4 (laser parked) and ch5 (paper present)."""
        grid = QGridLayout()
        grid.setSpacing(8)
        self._status_lbl    = {}
        self._status_ind    = {}
        for row, (ch, label) in enumerate(zip(_STATUS_CHANNELS, _STATUS_LABELS)):
            ind = QLabel("●")
            ind.setFixedWidth(20)
            ind.setAlignment(Qt.AlignmentFlag.AlignCenter)
            dist_lbl = QLabel("—")
            state_lbl = QLabel("—")
            state_lbl.setStyleSheet("font-weight:bold;")
            grid.addWidget(QLabel(label + ":"), row, 0)
            grid.addWidget(dist_lbl,            row, 1)
            grid.addWidget(ind,                 row, 2)
            grid.addWidget(state_lbl,           row, 3)
            self._status_lbl[ch]  = (dist_lbl, state_lbl)
            self._status_ind[ch]  = ind

        note = QLabel(
            "These sensors act as presence switches. "
            "Distance calibration is not needed — readings below "
            f"{_STATUS_THRESH_MM} mm are treated as 'active'.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#7f8c8d; font-style:italic;")
        v = QVBoxLayout()
        v.addLayout(grid)
        v.addWidget(note)
        return _group("Status Sensors (ch4–ch5) — No Calibration Needed", v)

    # ------------------------------------------------------------------
    # Data update methods (called from main window)
    # ------------------------------------------------------------------

    def on_sensors(self, msg: dict):
        """Update live readings from a query_sensors response."""
        for entry in msg.get("tof", []):
            ch   = entry.get("ch", -1)
            dist = entry.get("dist_mm", 0)
            valid = entry.get("valid", False)
            if 0 <= ch < 6:
                self._live[ch]  = dist
                self._valid[ch] = valid
        self._refresh_table()
        self._refresh_status_sensors()

    def on_cal_ack(self, offsets: list):
        """Called after a successful calibrate_sensors ack with the stored offsets."""
        for i, val in enumerate(offsets[:4]):
            self._baselines[i] = val
        self._refresh_table()
        vals_str = ", ".join(f"{v} mm" for v in offsets[:4])
        self._cal_status_lbl.setText(f"Calibrated — baselines: {vals_str}")
        self._cal_status_lbl.setStyleSheet("color:#27ae60; font-weight:bold;")

    def set_baseline(self, channel: int, value: float):
        """Set one channel's baseline (called from main window after get_param)."""
        if 0 <= channel < 4:
            self._baselines[channel] = value
            self._update_cal_status_label()
            self._refresh_table()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_baseline(self):
        """Request a calibration read. Firmware reads current ch0-ch3 and stores."""
        self.command_requested.emit({"cmd": "calibrate_sensors"})

    def _refresh_table(self):
        thresh = self._thresh_spin.value()
        for row, ch in enumerate(_ARM_CHANNELS):
            live      = self._live[ch]
            valid     = self._valid[ch]
            baseline  = self._baselines[row]
            live_str  = f"{live}" if valid else "invalid"
            base_str  = f"{baseline}" if baseline is not None else "—"

            if baseline is not None and valid:
                clearance = live - baseline
                clear_str = f"{clearance}"
                if clearance <= thresh:
                    status_str   = "● BLOCKED"
                    status_color = "#e74c3c"   # red
                else:
                    status_str   = "○ CLEAR"
                    status_color = "#27ae60"   # green
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
            dist   = self._live[ch]
            valid  = self._valid[ch]
            dist_lbl, state_lbl = self._status_lbl[ch]
            ind = self._status_ind[ch]
            dist_lbl.setText(f"{dist} mm" if valid else "invalid")
            if not valid:
                state_lbl.setText("—")
                ind.setStyleSheet("color:#95a5a6;")
                continue
            active = dist < _STATUS_THRESH_MM
            if ch == 4:
                state_lbl.setText("PARKED" if active else "AWAY")
            else:
                state_lbl.setText("PRESENT" if active else "EMPTY")
            color = "#27ae60" if active else "#95a5a6"
            state_lbl.setStyleSheet(f"font-weight:bold; color:{color};")
            ind.setStyleSheet(f"color:{color};")

    def _update_cal_status_label(self):
        calibrated = [b for b in self._baselines[:4] if b is not None]
        if len(calibrated) == 4:
            vals_str = ", ".join(f"{int(v)} mm" for v in self._baselines[:4])
            self._cal_status_lbl.setText(f"Calibrated — baselines: {vals_str}")
            self._cal_status_lbl.setStyleSheet("color:#27ae60; font-weight:bold;")
        elif calibrated:
            self._cal_status_lbl.setText(
                f"Partial — {len(calibrated)}/4 channels calibrated")
            self._cal_status_lbl.setStyleSheet("color:#e67e22; font-style:italic;")
        else:
            self._cal_status_lbl.setText("Not calibrated")
            self._cal_status_lbl.setStyleSheet("color:#e67e22; font-style:italic;")

    def set_connected(self, connected: bool):
        self._connected = connected
        self._btn_cal.setEnabled(connected)
        self._btn_refresh.setEnabled(connected)
