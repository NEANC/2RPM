#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""验证诊断焦点在独立进程和缩放测试之后均可到达真实输入框。"""

import os
import subprocess
import sys

import pytest

FOCUS_CASE = 'tests/test_gui_pages.py::test_validation_routes_external_error_to_action_field'
SCALE_CASE = 'tests/test_gui_window.py::test_dpi_wide_header_buttons_do_not_intersect_window_controls'
RESTORE_CASE = 'tests/test_gui_pages.py::test_external_diagnostic_focus_survives_unmapped_window'


@pytest.mark.parametrize('cases', [(FOCUS_CASE,), (SCALE_CASE, FOCUS_CASE),
                                    (RESTORE_CASE, FOCUS_CASE)])
def test_external_diagnostic_focus_in_fresh_process_and_after_scaling(cases):
    """新进程中单测及同进程缩放后均需通过真实焦点断言。"""
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', *cases],
                            env={**os.environ, 'RPM_GUI_DPI_CHILD': '1'} if SCALE_CASE in cases else None,
                            capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
