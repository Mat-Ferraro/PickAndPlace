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

CATEGORY_COLORS = {
    "USER":    "#2980b9",   # blue
    "PROGRAM": "#27ae60",   # green
    "STATE":   "#8e44ad",   # purple
    "FAULT":   "#e74c3c",   # red
    "SYSTEM":  "#7f8c8d",   # gray
}

