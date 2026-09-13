import os
import math
import random
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6.QtWidgets import QApplication

from kirho.ui.dialogs.oscilloscope_dialog import (
    OscilloscopeDialog, _BUFFER_TARGET, _Screen, _compact_samples,
    _SCREEN_REFRESH_MS, _condition_samples, _estimate_period, _find_trigger,
    _measure_samples,
)
from kirho.ui.items.component_item import ComponentItem
from kirho.ui.simulation_controller import SimulationController


_APP = QApplication.instance() or QApplication([])


def test_trigger_interpolates_edges_and_single_freezes():
    samples = [(0.0, -1.0), (1.0, -0.2), (2.0, 0.2), (3.0, 1.0)]
    assert _find_trigger(samples, 0.0, 'rising', 0.1) == pytest.approx(1.5)
    falling = [(0.0, 1.0), (1.0, 0.2), (2.0, -0.2), (3.0, -1.0)]
    assert _find_trigger(falling, 0.0, 'falling', 0.1) == pytest.approx(1.5)

    screen = _Screen()
    screen.time_div = 0.1
    screen.configure_trigger('A', 'rising', 0.0, 'single', position_divs=5.0)
    times = [i / 100.0 for i in range(301)]
    volts = [math.sin(2.0 * math.pi * t) for t in times]
    screen.push(times, volts, None)
    frozen_end = screen.t_window_end

    assert not screen.single_armed
    assert screen.triggered
    assert screen._last_trigger_time == pytest.approx(1.0, abs=1e-12)
    assert frozen_end == pytest.approx(1.5, abs=1e-12)

    later = [3.01 + i / 100.0 for i in range(200)]
    screen.push(later, [math.sin(2.0 * math.pi * t) for t in later], None)
    assert screen.t_window_end == frozen_end


def test_slow_timebase_keeps_a_full_screen_without_unbounded_buffer():
    samples = [(i / 24000.0, math.sin(2.0 * math.pi * 10 * i / 24000.0))
               for i in range(52801)]
    compacted = _compact_samples(samples)
    assert len(compacted) <= _BUFFER_TARGET
    assert min(v for _, v in compacted) == pytest.approx(-1.0, abs=1e-6)
    assert max(v for _, v in compacted) == pytest.approx(1.0, abs=1e-6)

    screen = _Screen()
    screen.time_div = 0.1
    screen.configure_trigger('A', 'rising', 2.0, 'auto')
    midpoint = len(samples) // 2
    for chunk in (samples[:midpoint], samples[midpoint:]):
        screen.push([t for t, _ in chunk], [v for _, v in chunk], None)

    assert len(screen.buf_a) <= _BUFFER_TARGET
    assert screen.buf_a[-1][0] - screen.buf_a[0][0] >= 1.0


def test_slow_auto_rolls_and_pause_freezes_the_capture():
    screen = _Screen()
    screen.time_div = 0.1
    screen.configure_trigger('A', 'rising', 0.0, 'auto')
    times = [i / 100.0 for i in range(101)]
    screen.push(times, [math.sin(2.0 * math.pi * t) for t in times], None)
    frozen_end = screen.t_window_end
    frozen_count = len(screen.buf_a)

    assert screen.is_roll
    assert screen._trigger_status() == 'ROLL'
    screen.set_paused(True)
    screen.push([2.0, 2.1], [0.0, 1.0], None)
    assert screen.t_window_end == frozen_end
    assert len(screen.buf_a) == frozen_count
    assert screen._trigger_status() == 'PAUSED'

    screen.set_paused(False)
    screen.push([2.0, 2.1], [0.0, 1.0], None)
    assert screen.t_window_end == 2.1
    assert list(screen.buf_a) == [(2.0, 0.0), (2.1, 1.0)]


def test_channel_controls_and_autoscale():
    times = [i / 10000.0 for i in range(501)]
    samples = [(t, 5.0 + 2.0 * math.sin(2.0 * math.pi * 100.0 * t))
               for t in times]
    conditioned = _condition_samples(samples, 'AC', inverted=True)
    assert sum(value for _, value in conditioned) / len(conditioned) == pytest.approx(0.0)
    assert _estimate_period(conditioned) == pytest.approx(0.01, rel=1e-3)

    item = ComponentItem('OSC', 'XSC1')
    item.osc_time_div = 0.01
    dialog = OscilloscopeDialog(item)
    dialog.screen.push(times, [value for _, value in samples], None)
    dialog._on_autoscale()

    assert item.osc_v_div_a == 2.0
    assert item.osc_pos_a == pytest.approx(-2.5, abs=0.05)
    assert item.osc_time_div == pytest.approx(0.002)
    dialog.ck_enabled_b.setChecked(False)
    assert not dialog.screen.enabled_b
    dialog.close()


def test_measurements_and_cursors_use_the_visible_window():
    times = [i / 24000.0 for i in range(241)]
    sine = [(t, 10.0 * math.sin(2.0 * math.pi * 1000.0 * t)) for t in times]
    measured = _measure_samples(sine)
    assert measured['frequency'] == pytest.approx(1000.0, rel=1e-3)
    assert measured['period'] == pytest.approx(0.001, rel=1e-3)
    assert measured['vpp'] == pytest.approx(20.0)
    assert measured['average'] == pytest.approx(0.0, abs=1e-12)
    assert measured['rms'] == pytest.approx(10.0 / math.sqrt(2), rel=0.01)

    screen = _Screen()
    screen.time_div = 0.001
    screen.configure_trigger('A', 'rising', 100.0, 'auto')
    linear_times = [i / 1000.0 for i in range(11)]
    screen.push(linear_times, [1000.0 * t for t in linear_times], None)
    screen.cursor_channel = 'A'
    screen.cursor_div_1 = 3.0
    screen.cursor_div_2 = 7.0
    dt, inverse, dv = screen.cursor_readout()
    assert dt == pytest.approx(0.004)
    assert inverse == pytest.approx(250.0)
    assert dv == pytest.approx(4.0)


@pytest.mark.parametrize('waveform', ('sine', 'square', 'triangle', 'noise'))
def test_periodic_waveforms_hold_a_stable_trigger(waveform):
    frequency = 1000.0
    period = 1.0 / frequency
    sample_rate = frequency * 24
    rng = random.Random(7)
    screen = _Screen()
    screen.time_div = 0.0002
    screen.v_div_a = 5.0
    screen.configure_trigger('A', 'rising', 0.0, 'auto')
    phases = []

    for block in range(8):
        times = [(block * 120 + i) / sample_rate for i in range(120)]
        values = []
        for t in times:
            phase = (t * frequency) % 1.0
            if waveform == 'square':
                value = 10.0 if phase < 0.5 else -10.0
            elif waveform == 'triangle':
                value = 10.0 * (4.0 * abs(phase - 0.5) - 1.0)
            else:
                value = 10.0 * math.sin(2.0 * math.pi * phase)
                if waveform == 'noise':
                    value += rng.uniform(-0.15, 0.15)
            values.append(value)
        screen.push(times, values, None)
        assert screen.triggered
        phases.append(screen._last_trigger_time % period)

    reference = phases[0]
    phase_error = lambda phase: abs((phase - reference + period / 2) % period - period / 2)
    assert max(map(phase_error, phases)) <= 2.0 / sample_rate
    assert screen.measurements('A')['frequency'] == pytest.approx(frequency, rel=0.06)


def test_scope_tracks_frequency_changes_and_enforces_ui_budgets():
    screen = _Screen()
    screen.time_div = 0.0005
    screen.configure_trigger('A', 'rising', 0.0, 'auto')
    for frequency, start, stop in ((1000.0, 0.0, 0.02), (500.0, 0.02, 0.05)):
        sample_rate = frequency * 24
        count = round((stop - start) * sample_rate)
        times = [start + i / sample_rate for i in range(count)]
        screen.push(times, [math.sin(2.0 * math.pi * frequency * t) for t in times], None)

    assert screen.measurements('A')['frequency'] == pytest.approx(500.0, rel=0.02)
    assert SimulationController._LIVE_SAMPLES_PER_PERIOD >= 24
    assert SimulationController._LIVE_SOLVER_BUDGET_MS < 16
    assert _SCREEN_REFRESH_MS >= 1000 / 30


def test_scope_render_budget_and_repeated_open_close():
    screen = _Screen()
    screen.resize(1000, 600)
    screen.time_div = 0.1
    times = [i / 8000.0 for i in range(8000)]
    screen.push(times, [math.sin(2.0 * math.pi * 10.0 * t) for t in times], None)
    screen.show()
    _APP.processEvents()
    screen.grab()  # calentamiento de fuentes y backing store de Qt
    started = time.perf_counter()
    for _ in range(20):
        screen.grab()
    assert (time.perf_counter() - started) / 20 < 0.016
    screen.close()

    for index in range(20):
        dialog = OscilloscopeDialog(ComponentItem('OSC', f'XSC{index}'))
        dialog.show()
        dialog.screen.push([0.0, 0.001], [0.0, 1.0], None)
        _APP.processEvents()
        dialog.close()
        dialog.deleteLater()
    _APP.processEvents()
    assert not any(isinstance(widget, OscilloscopeDialog) and widget.isVisible()
                   for widget in _APP.topLevelWidgets())
