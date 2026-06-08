from gui_common import *
from gui_common import _btn, _group, _label, _style_btn

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
        # Trust the firmware's authoritative program_loaded flag when present
        # (covers a program already stored in EEPROM / loaded headless), while
        # preserving the locally-tracked value if an older firmware omits it.
        self._program_loaded = msg.get("program_loaded", self._program_loaded)
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

