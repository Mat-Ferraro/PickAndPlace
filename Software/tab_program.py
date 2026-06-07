from gui_common import *
from gui_common import _btn, _group, _label, _style_btn

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


