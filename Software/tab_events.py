from gui_common import *
from gui_common import _btn, _group, _label, _style_btn

# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------



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

