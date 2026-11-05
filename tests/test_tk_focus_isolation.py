"""Small Tk-only focus traversal probe, independent of GridWise widgets."""

import subprocess
import sys

import pytest

pytestmark = pytest.mark.gui

if sys.platform != "win32":
    pytest.skip("Windows Tk focus probe", allow_module_level=True)

def test_window_lifecycle_keeps_focus_traversal_command_available():
    code = """
import tkinter as tk
from tkinter import ttk
root = tk.Tk()
root.withdraw()
try:
    for _ in range(30):
        window = tk.Toplevel(root)
        try:
            first = ttk.Button(window, text="First")
            second = ttk.Button(window, text="Second")
            first.pack()
            second.pack()
            window.update()
            assert first.tk_focusNext() == second
            assert root.tk.call("info", "commands", "tk_focusNext")
        finally:
            window.destroy()
finally:
    root.destroy()
"""
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=30)
    assert completed.returncode == 0, completed.stderr
