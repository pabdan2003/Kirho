"""Coordinador de simulación de Kirho.

Mantiene fuera de MainWindow el despacho automático, los solvers, el estado
transitorio en vivo y la actualización de indicadores.
"""
from __future__ import annotations

from typing import Optional, List, Dict, Tuple

import numpy as np
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from kirho.engine import (
    Resistor, VoltageSource, VoltageSourceAC, CurrentSource, MNASolver,
)
from kirho.circuit_analyzer import CircuitAnalyzer, DEFAULT_STANDARD
from kirho.ui.items.component_item import ComponentItem
from kirho.ui.scene import build_engine_components_for_item, expand_subcircuits


class SimulationController:
    # ── Estado y ciclo de vida de simulación ────────────────────────────────
    _LIVE_TIME_SCALE          = 1.0
    _LIVE_TICK_MS             = 50
    _LIVE_PANEL_REFRESH_TICKS = 5
    _LIVE_MAX_STEPS_PER_TICK  = 600
    _LIVE_SAMPLES_PER_PERIOD  = 12
    _LIVE_TOL_ABS             = 1e-3
    _LIVE_TOL_REL             = 5e-2
    _LIVE_NR_TOL              = 1e-4
    _DC_TICK_MS               = 200

    def __init__(self, window):
        self._window = window
        self.solver = MNASolver()
        self._sim_running = False
        self._sim_mode = 'idle'
        self._sim_all_comps = None
        self._sim_pin_node = None
        self._live_state = None
        self._live_components = None
        self._live_pin_node = None
        self._live_freq = 60.0
        self._live_tick_count = 0
        self._live_phasor_summary = ""
        self._live_nr_max = 20

        self._sim_timer = QTimer(window)
        self._sim_timer.setInterval(self._DC_TICK_MS)
        self._sim_timer.timeout.connect(self._tick_simulation)

    def __getattr__(self, name):
        # La simulación conserva acceso a la UI sin acoplar MainWindow al
        # detalle de cada widget.
        return getattr(self._window, name)

    # ── Simulación automática ─────────────────────────────────────────────
    def _merge_all_sheets(self) -> Tuple[List[ComponentItem], Dict[str, str]]:
        """Combina componentes y netlist de todas las hojas para simulación multi-hoja.

        Cada hoja ya resuelve sus propios net labels internamente (via extract_netlist).
        Este método solo necesita unificar los nets ENTRE hojas distintas cuando el
        mismo sheet_label aparece en hojas diferentes.
        """
        all_components: List[ComponentItem] = []
        merged_pin_node: Dict[str, str] = {}

        sheet_netlists = []
        for i, sheet in enumerate(self._sheets):
            sc = sheet['scene']
            pn = sc.extract_netlist()
            prefix = f"_s{i}_"
            prefixed_pn = {}
            for pin_id, net_name in pn.items():
                new_net = (net_name if net_name == '0' or not net_name.startswith('net_')
                           else prefix + net_name)
                prefixed_pn[pin_id] = new_net
            sheet_netlists.append(prefixed_pn)
            all_components.extend(sc.components)
            merged_pin_node.update(prefixed_pn)

        # ── Unificar nets ENTRE hojas por sheet_label ─────────────────────
        # Dentro de cada hoja, extract_netlist ya unió los net labels.
        # Aquí unimos los nets canónicos de cada hoja que comparten label.
        label_canonical: Dict[str, str] = {}  # label → net canónico global

        for i, sheet in enumerate(self._sheets):
            pn = sheet_netlists[i]
            for comp in sheet['scene'].components:
                if comp.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT') and comp.sheet_label:
                    pin_key = f"{comp.name}__p1"
                    if pin_key not in pn:
                        continue
                    net = pn[pin_key]   # net ya prefijado con _sN_
                    aliases = []
                    for lbl in (comp.sheet_label.strip(), comp.name.strip()):
                        if lbl and lbl not in aliases:
                            aliases.append(lbl)
                    for lbl in aliases:
                        if lbl not in label_canonical:
                            label_canonical[lbl] = net
                        elif label_canonical[lbl] != net:
                            # Reemplazar en toda la netlist
                            target = label_canonical[lbl]
                            for k in merged_pin_node:
                                if merged_pin_node[k] == net:
                                    merged_pin_node[k] = target
                            net = target

        return all_components, merged_pin_node

    def _get_sim_context(self):
        """Retorna (all_comps, pin_node) considerando net labels multi-hoja."""
        has_net_labels = any(
            comp.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT')
            for sheet in self._sheets
            for comp in sheet['scene'].components
        )
        if has_net_labels or len(self._sheets) > 1:
            comps, pin_node = self._merge_all_sheets()
        else:
            comps = list(self.scene.components)
            pin_node = self.scene.extract_netlist()
        # Aplanar subcircuitos (SUBCKT) → componentes reales para TODOS los
        # motores (DC/AC/digital/mixto). No-op si no hay subcircuitos.
        return expand_subcircuits(comps, pin_node)

    @staticmethod
    def _is_digital_indicator_circuit(items) -> bool:
        return bool(items) and all(
            item.comp_type in ComponentItem.DIGITAL_TYPES | {
                'LED', 'LAMP', 'GND', 'NODE', 'NET_LABEL_IN', 'NET_LABEL_OUT'
            }
            for item in items
        )

    def _toggle_simulation(self, checked: bool):
        """Analiza el circuito y despacha automáticamente al solver correcto."""
        if not checked:
            self._stop_simulation()
            return

        all_comps, pin_node = self._get_sim_context()
        self._sim_all_comps = all_comps
        self._sim_pin_node = pin_node

        analyzer = CircuitAnalyzer()
        flags = analyzer.analyze(all_comps, pin_node)

        self.results_text.setPlainText(flags.summary() + "\n\nAnalizando...")
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

        # ── Despacho por modo ────────────────────────────────────────────
        # 1. Mixto (puentes ADC/DAC explícitos o fronteras implícitas):
        #    one-shot mixto. La co-simulación digital-analógica no se
        #    reanuda en vivo por ahora.
        # 2. Cualquier `has_ac` (con o sin DC, lineal o no-lineal):
        #    live transient continuo (estilo Multisim interactivo).
        # 3. Solo DC o digital: tick DC continuo (más rápido — el sistema
        #    converge al instante en cada paso, no necesita transient).
        digital_indicator_circuit = (
            flags.has_digital and self._is_digital_indicator_circuit(all_comps)
        )
        if (flags.has_bridges or bool(flags.implicit_boundary_nodes)) and not digital_indicator_circuit:
            self.run_btn.setChecked(False)
            self.run_btn.setText(self.tr("▶  SIMULATE"))
            self._run_simulation_auto(flags, pin_node)
        elif flags.has_ac:
            self._start_live_transient(flags, pin_node)
        elif flags.has_dc or flags.has_digital:
            self._sim_running = True
            self._sim_mode    = 'dc_tick'
            self.run_btn.setText(self.tr("■  STOP"))
            self._sim_timer.setInterval(self._DC_TICK_MS)
            self._sim_timer.start()
            self._run_simulation_dc()
        else:
            self.run_btn.setChecked(False)
            self.run_btn.setText(self.tr("▶  SIMULATE"))
            self.results_text.setPlainText(self.tr(
                "⚠  No components were found to simulate.\n"
                "Add components to the canvas and connect them to ground."))

    def _stop_simulation(self):
        """Detiene la simulación y apaga todos los LEDs en todas las hojas."""
        self._sim_running = False
        self._sim_mode    = 'idle'
        self._sim_timer.stop()
        self._sim_timer.setInterval(self._DC_TICK_MS)   # restaurar intervalo DC
        self._sim_all_comps = None
        self._sim_pin_node  = None
        # Limpiar estado live
        self._live_state          = None
        self._live_components     = None
        self._live_pin_node       = None
        self._live_tick_count     = 0
        self._live_phasor_summary = ""
        self.run_btn.setChecked(False)
        self.run_btn.setText(self.tr("▶  SIMULATE"))
        for sheet in self._sheets:
            for item in sheet['scene'].components:
                if item.comp_type in ComponentItem.LIGHT_TYPES:
                    item.led_on = False
                    item.update()
                elif item.comp_type == 'MULTIMETER':
                    item.meter_reading = None
                    item.update()
        self._refresh_open_multimeter_panels()

    def _tick_simulation(self):
        """Llamado por QTimer: dispatcher por modo."""
        if not self._sim_running:
            return
        if self._sim_mode == 'live_transient':
            self._tick_live_transient()
        else:
            # Modo DC tick: re-corre DC silenciosamente para refrescar LEDs
            all_comps, pin_node = self._get_sim_context()
            self._sim_all_comps = all_comps
            self._sim_pin_node  = pin_node
            self._run_simulation_dc(silent=True)

    def _run_simulation(self):
        """Compatibilidad: despacha al toggle."""
        self._toggle_simulation(True)

    def _build_analog_components(self, items, pin_node):
        from kirho.engine.components import Timer555Analog
        components, errors = [], []
        for item in items:
            if item.comp_type == 'IC555':
                p = lambda n: (item.timer_nodes[n - 1].strip()
                               or pin_node.get(f"{item.name}__p{n}", '0'))
                timer = Timer555Analog(item.name, p(1), p(2), p(3), p(4),
                                       p(5), p(6), p(7), p(8))
                timer.item = item
                components.append(timer)
                continue
            if item.comp_type in ComponentItem.DIGITAL_TYPES | {
                'NET_LABEL_IN', 'NET_LABEL_OUT', 'GND', 'NODE'
            }:
                continue
            try:
                components.extend(build_engine_components_for_item(item, pin_node))
            except Exception as error:
                errors.append(f"{item.name}: {error}")
        return components, errors

    # ── Live transient (Multisim-like) ────────────────────────────────────
    def _start_live_transient(self, flags, pin_node):
        """
        Arranca una simulación transient continua: el solver avanza el
        tiempo simulado en cada tick del QTimer, manteniendo el estado
        de capacitores, inductores y diodos entre llamadas.

        Equivalente al modo "interactivo" de Multisim.
        """
        sim_components = self._sim_all_comps or list(self.scene.components)

        analog_comps, build_errors = self._build_analog_components(
            sim_components, pin_node)

        if not analog_comps:
            self.results_text.setPlainText(
                self.tr("⚠  There are no analog components to simulate."))
            self._stop_simulation()
            return

        # Frecuencia más alta entre todas las fuentes periódicas (VAC y FGEN).
        # Rige el dt interno y, junto con _LIVE_MAX_STEPS_PER_TICK, el
        # tiempo simulado por tick.
        freq = self._max_ac_source_frequency(sim_components) or 60.0

        # ── Snapshot fasorial inicial (solo circuitos lineales) ───────
        # El triángulo de potencia y los fasores P/Q/S solo tienen sentido
        # si el circuito NO tiene componentes que generen armónicos.
        # Para no-lineales (diodos, BJT, MOSFET) ocultamos el botón.
        ac_snapshot = None
        self._live_phasor_summary = ""   # se prepended en cada refresh del panel
        if not flags.has_nonlinear:
            ac_snapshot = self.solver.solve_ac_single(analog_comps, freq)
            if ac_snapshot.get('success'):
                self._last_ac_result = ac_snapshot
                self.btn_power_triangle.setVisible(True)
                self._live_phasor_summary = self._format_phasor_summary(
                    ac_snapshot, freq)
            else:
                self.btn_power_triangle.setVisible(False)
        else:
            self.btn_power_triangle.setVisible(False)

        # Estado live: None → el primer tick calcula DC OP en t=0 y arranca
        self._live_state       = None
        self._live_components  = analog_comps
        self._live_pin_node    = pin_node
        self._live_freq        = freq if freq > 0 else 60.0
        self._live_tick_count  = 0
        self._sim_mode         = 'live_transient'
        self._sim_running      = True
        # NOTA sobre adaptive: la trapezoidal es A-estable pero NO L-estable.
        # Para circuitos muy stiff (op-amps con A=1e5) el adaptativo es
        # imprescindible para que la trapezoidal no oscile. No lo desactivamos.
        #
        # Solo el tope de iteraciones NR varía con el circuito: los diodos
        # exponenciales pueden requerir más pasos para converger que un
        # circuito puramente lineal.
        self._live_nr_max = 40 if flags.has_nonlinear else 20

        self.run_btn.setText(self.tr("■  STOP"))
        self.run_btn.setChecked(True)
        self._sim_timer.setInterval(self._LIVE_TICK_MS)
        self._sim_timer.start()

        msg = [
            self.tr("═══ LIVE SIMULATION (continuous transient, ×{scale:g}) ═══").format(scale=self._LIVE_TIME_SCALE),
            f"  {flags.summary()}",
            f"  f_AC = {self._live_freq:g} Hz  ·  paso real {self._LIVE_TICK_MS} ms"
            f"  ·  paso simulado {self._LIVE_TICK_MS * self._LIVE_TIME_SCALE:.2f} ms",
            "",
        ]

        # Si el fasorial fue válido, mostrar el resumen P/Q/S/fp arriba —
        # es información estable que no necesita refrescarse cada tick.
        if self._live_phasor_summary:
            msg.append(self._live_phasor_summary)

        msg.append("  Starting…")
        if build_errors:
            msg.append("")
            msg.append("── Warnings ──")
            msg.extend([f"  ⚠ {e}" for e in build_errors])
        self.results_text.setPlainText("\n".join(msg))

    def _tick_live_transient(self):
        """Avanza el solver `dt_sim` segundos y actualiza la UI.

        Estrategia para mantener la UI fluida en cualquier frecuencia:
          1. `dt_internal` se elige para tener ~50 muestras por período
             de la onda más rápida (resolución suficiente para osciloscopio).
          2. `dt_advance` arranca como `tick_ms · TIME_SCALE` (slow-motion
             a baja frecuencia, igual que antes).
          3. Si esa combinación produce más de MAX_STEPS pasos del solver
             por tick, se recorta `dt_advance` para respetar el tope.
             Esto se traduce en "menos tiempo simulado por frame" cuando
             la frecuencia sube — la onda se ve "rápida" pero fluida.
        """
        if self._live_components is None:
            return

        T_freq = 1.0 / max(self._live_freq, 1e-6)
        # dt_internal = T / SAMPLES_PER_PERIOD para evitar aliasing.
        # Antes había un piso de 50 ns "para no exigir absurdos", pero eso
        # provocaba aliasing salvaje para señales >1.67 MHz (T/12=50ns).
        # El piso real lo da MAX_STEPS_PER_TICK: cuanto más alta la freq,
        # menos tiempo simulado por tick — el CPU/tick queda acotado igual.
        dt_internal = max(T_freq / self._LIVE_SAMPLES_PER_PERIOD, 1e-11)

        # Tiempo simulado deseado por tick (slow-motion a baja frecuencia)
        dt_advance_ideal = (self._LIVE_TICK_MS / 1000.0) * self._LIVE_TIME_SCALE

        # Tope por costo de CPU: nunca más de MAX_STEPS pasos del solver
        dt_advance_cap = self._LIVE_MAX_STEPS_PER_TICK * dt_internal
        dt_advance = min(dt_advance_ideal, dt_advance_cap)
        # Al menos 4 pasos por tick (mantiene la integración estable)
        dt_advance = max(dt_advance, dt_internal * 4)

        t_start = float(self._live_state['t']) if self._live_state else 0.0

        tr = self.solver.solve_transient(
            self._live_components,
            t_stop        = dt_advance,
            dt            = dt_internal,
            method        = 'trapezoidal',
            adaptive      = True,
            tol_abs       = self._LIVE_TOL_ABS,
            tol_rel       = self._LIVE_TOL_REL,
            # dt_min bajo: durante la conmutación de un diodo el NR puede
            # necesitar pasos de nanosegundos para converger.
            dt_min        = 1e-10,
            t_start       = t_start,
            initial_state = self._live_state,
            nr_tol        = self._LIVE_NR_TOL,
            nr_max_iter   = self._live_nr_max,
        )

        if not tr.get('success'):
            self.results_text.setPlainText(self.tr(
                "✗ Live transient failed:\n  {error}").format(
                    error=tr.get('error', self.tr('unknown'))))
            self._stop_simulation()
            return

        # Guardar estado para el próximo tick
        self._live_state = tr['final_state']
        self._live_tick_count += 1

        # Refresco visual de los items (LEDs y voltajes instantáneos)
        self._update_items_from_live(tr)

        # Texto del panel cada N ticks (no abrumar la UI)
        if self._live_tick_count % self._LIVE_PANEL_REFRESH_TICKS == 0:
            self._refresh_live_panel(tr)

        # Empujar muestras a los instrumentos abiertos (osciloscopios, etc.)
        self._push_to_open_instruments(tr)

    def _push_to_open_instruments(self, tr):
        """Notifica a cada panel de instrumento abierto que llegó un nuevo
        bloque de muestras del solver. Sólo los OSC consumen este flujo
        por ahora."""
        pin_node = self._live_pin_node or {}
        # Si hay varias hojas, recorremos sólo la escena activa para evitar
        # alimentar OSC de otras hojas con voltajes que no corresponden.
        for item in self.scene.components:
            if item.comp_type != 'OSC':
                continue
            dlg = getattr(item, '_panel_dialog', None)
            if dlg is None or not dlg.isVisible():
                continue
            try:
                dlg.push_samples(tr, pin_node)
            except Exception:
                # Un fallo en un instrumento no debe matar el live transient.
                pass

    @staticmethod
    def _estimate_led_current(vd, color: str = 'red'):
        """Corriente directa estimada con los mismos presets del LED MNA."""
        led_params = {
            'red':    (1.0e-18, 2.0, 5.0),
            'orange': (1.0e-19, 2.1, 5.0),
            'yellow': (1.0e-20, 2.2, 5.0),
            'green':  (1.0e-23, 2.5, 5.0),
            'blue':   (1.0e-27, 3.0, 5.0),
            'white':  (1.0e-27, 3.0, 5.0),
        }
        Is, n, vd_max = led_params.get(color, led_params['red'])
        vd_arr = np.asarray(vd, dtype=float)
        vd_arr = np.clip(vd_arr, -50.0, vd_max)
        current = Is * (np.exp(vd_arr / (n * 0.02585)) - 1.0)
        return np.maximum(current, 0.0)

    @staticmethod
    def _light_threshold(item) -> float:
        if item.comp_type == 'LAMP':
            return max(float(item.value), 0.0)
        return {
            'red': 1.5, 'orange': 1.7, 'yellow': 1.8,
            'green': 1.9, 'blue': 2.6, 'white': 2.6,
        }.get(getattr(item, 'led_color', 'red'), 1.5)

    @classmethod
    def _light_is_on(cls, item, voltage: float, reference: float = 0.0) -> bool:
        drop = voltage - reference
        return (abs(drop) if item.comp_type == 'LAMP' else drop) >= cls._light_threshold(item)

    @staticmethod
    def _display_voltage(item, voltage: Optional[float], reference: float = 0.0) -> Optional[float]:
        if voltage is None:
            return None
        return abs(voltage - reference) if item.comp_type == 'LAMP' else voltage

    @staticmethod
    def _voltage_drop_array(voltages, n1: str, n2: str):
        """Devuelve V(n1)-V(n2), incluyendo tierra implícita."""
        ground = ('0', 'gnd', 'GND', '')
        if n1 in ground:
            v2 = voltages.get(n2)
            return -np.asarray(v2) if n2 not in ground and v2 is not None else np.zeros(1)
        v1 = voltages.get(n1)
        if v1 is None:
            return None
        v1 = np.asarray(v1)
        if n2 in ground:
            return v1
        v2 = voltages.get(n2)
        if v2 is None:
            return v1
        n = min(len(v1), len(v2))
        return v1[-n:] - np.asarray(v2)[-n:]

    def _update_items_from_live(self, tr):
        """Actualiza cada componente con el último valor instantáneo.

        Para LEDs en señales AC, decidir el on/off según `arr[-1]` no sirve
        (cae en una fase aleatoria), y usar el pico es demasiado sensible
        a transitorios numéricos. Usamos corriente media estimada con el
        mismo modelo del LED; si supera ~0.1 mA, lo dibujamos encendido.
        """
        v_dict   = tr.get('voltages', {})
        if not v_dict:
            return
        sim_components = self._sim_all_comps or list(self.scene.components)
        pin_node = self._live_pin_node or {}
        relay_states = {
            c.name: c.active for c in self._live_components or []
            if c.__class__.__name__ == 'Relay'
        }

        for timer in (c for c in self._live_components or []
                      if c.__class__.__name__ == 'Timer555Analog'):
            timer.item.dig_q_state = int(timer.q)
            timer.item.update()

        def _last(node):
            arr = v_dict.get(node)
            if arr is None or len(arr) == 0:
                return 0.0
            return float(arr[-1])

        for item in sim_components:
            if item.comp_type in ComponentItem.DIGITAL_TYPES:
                continue
            if item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT', 'GND', 'NODE'):
                continue

            if item.comp_type == 'RELAY':
                item.relay_active = relay_states.get(item.name, False)

            n1 = item.node1.strip() or pin_node.get(f"{item.name}__p1", "")
            n2 = item.node2.strip() or pin_node.get(f"{item.name}__p2", "0")

            v_a = _last(n1) if n1 else None
            v_k = _last(n2) if n2 not in ('0', 'gnd', 'GND', '') else 0.0

            item.result_voltage = self._display_voltage(item, v_a, v_k)

            if item.comp_type in ComponentItem.LIGHT_TYPES and n1:
                vd = self._voltage_drop_array(v_dict, n1, n2)
                if vd is None or len(vd) == 0:
                    item.led_on = False
                elif item.comp_type == 'LAMP':
                    item.led_on = abs(float(vd[-1])) >= self._light_threshold(item)
                else:
                    # Corriente media visible (~0.1 mA como umbral visual).
                    i_led = self._estimate_led_current(
                        vd, getattr(item, 'led_color', 'red'))
                    item.led_on = float(np.mean(i_led)) > 1e-4

            if item.comp_type == 'MULTIMETER' and n1:
                vd = _vd_array(n1, n2)
                if vd is not None and len(vd) > 0:
                    self._update_multimeter_from_array(item, vd)

            item.update()

        self._refresh_open_multimeter_panels()

    # ── Multímetro: actualización de lecturas ─────────────────────────────
    def _multimeter_internal_R(self, item) -> float:
        return 1e-3 if getattr(item, 'meter_quantity', 'V') == 'A' else 1e7

    def _apply_meter_reading(self, item, val: float):
        """Aplica `val` (V o A) al item según su modo, ajustando la unidad
        del display. Para A modo, `val` ya debe ser corriente (no voltaje)."""
        qty = getattr(item, 'meter_quantity', 'V')
        if qty == 'V':
            item.meter_reading = float(val)
            item.meter_reading_unit_hint = 'V'
        elif qty == 'A':
            item.meter_reading = float(val)
            item.meter_reading_unit_hint = 'A'
        else:
            item.meter_reading = None
            item.meter_reading_unit_hint = 'Ω'

    def _update_multimeter_from_array(self, item, vd_arr):
        """Calcula la lectura a partir de una ventana de muestras V_p+ − V_p−.
        DC coupling → media · AC coupling → RMS de la componente alterna."""
        import numpy as _np
        arr = _np.asarray(vd_arr, dtype=float)
        cpl = getattr(item, 'meter_coupling', 'DC')
        if cpl == 'DC':
            val = float(_np.mean(arr))
        else:
            mean = float(_np.mean(arr))
            ac_part = arr - mean
            val = float(_np.sqrt(_np.mean(ac_part * ac_part)))
        qty = getattr(item, 'meter_quantity', 'V')
        if qty == 'A':
            val = val / self._multimeter_internal_R(item)
        self._apply_meter_reading(item, val)

    def _update_multimeter_from_dc(self, item, n1, n2, dc_voltages):
        """Lectura desde un resultado DC. En AC coupling, sólo DC → 0."""
        v1 = float(dc_voltages.get(n1, 0.0)) if n1 not in ('0', 'gnd', 'GND') else 0.0
        v2 = float(dc_voltages.get(n2, 0.0)) if n2 not in ('0', 'gnd', 'GND') else 0.0
        dv = v1 - v2
        cpl = getattr(item, 'meter_coupling', 'DC')
        if cpl == 'AC':
            val = 0.0
        else:
            val = dv
        qty = getattr(item, 'meter_quantity', 'V')
        if qty == 'A':
            val = val / self._multimeter_internal_R(item)
        self._apply_meter_reading(item, val)

    def _update_multimeter_from_ac(self, item, n1, n2, ac_voltages):
        """Lectura desde un snapshot AC (fasores RMS). En DC coupling → 0."""
        V1 = ac_voltages.get(n1, 0.0 + 0.0j) if n1 not in ('0', 'gnd', 'GND') else 0.0 + 0.0j
        V2 = ac_voltages.get(n2, 0.0 + 0.0j) if n2 not in ('0', 'gnd', 'GND') else 0.0 + 0.0j
        cpl = getattr(item, 'meter_coupling', 'DC')
        if cpl == 'DC':
            val = 0.0
        else:
            val = abs(V1 - V2)
        qty = getattr(item, 'meter_quantity', 'V')
        if qty == 'A':
            val = val / self._multimeter_internal_R(item)
        self._apply_meter_reading(item, val)

    def _refresh_open_multimeter_panels(self):
        """Refresca cada panel abierto de multímetro para que muestre la lectura
        recién calculada. Llamado desde los flujos de simulación."""
        for sheet in self._sheets:
            for item in sheet['scene'].components:
                if item.comp_type != 'MULTIMETER':
                    continue
                dlg = getattr(item, '_panel_dialog', None)
                if dlg is None:
                    continue
                try:
                    if dlg.isVisible():
                        dlg._refresh_display()
                except Exception:
                    pass

    def _refresh_live_panel(self, tr):
        """Actualiza el panel de texto con voltajes instantáneos y tiempo."""
        v_dict = tr.get('voltages', {})
        t_arr  = tr.get('time', [])
        if not v_dict or len(t_arr) == 0:
            return

        t_now = float(t_arr[-1])
        out = [
            self.tr("═══ LIVE SIMULATION (×{scale:g}) ═══").format(scale=self._LIVE_TIME_SCALE),
            f"  t_simulado = {t_now*1000:.2f} ms"
            f"   ·   ticks = {self._live_tick_count}"
            f"   ·   pasos_NR = {tr.get('steps', 0)}",
            "",
        ]

        # Fasores se mantienen visibles durante toda la corrida (snapshot fijo).
        if getattr(self, '_live_phasor_summary', ''):
            out.append(self._live_phasor_summary)

        out.append(self.tr("── Instantaneous voltages ──"))
        for node, v_arr in sorted(v_dict.items()):
            if len(v_arr) > 0:
                out.append(f"  V({node}) = {float(v_arr[-1]):+.4f} V")

        self.results_text.setPlainText("\n".join(out))

    def _format_phasor_summary(self, ac_result: dict, freq: float) -> str:
        """Formatea fasores nodales y triángulo de potencia en un bloque de
        texto que se preserva mientras dura la simulación live."""
        import cmath as _cmath
        lines = [f"── Fasores AC ({freq:g} Hz, snapshot) ──"]
        for node, V in sorted(ac_result.get('voltages', {}).items()):
            lines.append(f"  V({node}) = {abs(V):.4f} V  "
                         f"∠{_cmath.phase(V)*180/_cmath.pi:.2f}°")
        tot = ac_result.get('total', {})
        if tot:
            fp_type = self._localized_power_factor_type(tot.get('fp_type', ''))
            lines += [
                "",
                self.tr("── Total power ──"),
                f"  P={tot.get('P',0):+.4f} W  Q={tot.get('Q',0):+.4f} VAR",
                f"  S={tot.get('S',0):.4f} VA  fp={tot.get('fp',0):.4f} "
                f"({fp_type})",
            ]
        lines.append("")
        return "\n".join(lines)

    def _localized_power_factor_type(self, value: str) -> str:
        return {
            'inductive': self.tr('inductive'),
            'capacitive': self.tr('capacitive'),
            'unity': self.tr('unity'),
        }.get(value, value)

    def _run_simulation_auto(self, flags=None, pin_node=None):
        """Corre DC + AC + mixto según flags y muestra todo en un panel."""
        from PyQt6.QtWidgets import QApplication
        from kirho.engine.digital_engine import (
            DigitalSimulator, Gate, Timer555, DFF, JKFF, TFF, SRFF, BinaryCounter, MUX,
        )
        from kirho.engine.bridges import ADC, DAC, ComparatorBridge, PWMBridge
        from kirho.engine.mixed_signal import MixedSignalInterface
        import cmath as _cmath

        if pin_node is None:
            pin_node = getattr(self, '_sim_pin_node', None) or self.scene.extract_netlist()
        if flags is None:
            sim_comps_for_flags = getattr(self, '_sim_all_comps', None) or list(self.scene.components)
            analyzer = CircuitAnalyzer()
            flags = analyzer.analyze(sim_comps_for_flags, pin_node)

        sim_components = getattr(self, '_sim_all_comps', None) or list(self.scene.components)

        # Detectar y mostrar el modo seleccionado automáticamente
        _modes = []
        if flags.has_dc:      _modes.append("DC")
        if flags.has_ac:
            _modes.append("AC-transient" if flags.has_nonlinear else "AC-fasorial")
        if flags.has_digital: _modes.append("Digital")
        if flags.has_bridges or flags.implicit_boundary_nodes:
            _modes.append("Mixto")
        _mode_str = " + ".join(_modes) if _modes else "—"

        out = [self.tr("═══ SIMULATION ({mode}) ═══").format(mode=_mode_str),
               f"  {flags.summary()}", ""]
        if flags.warnings:
            out.extend([f"  ⚠ {w}" for w in flags.warnings]); out.append("")

        analog_comps, build_errors = self._build_analog_components(
            sim_components, pin_node)

        # ── DC ────────────────────────────────────────────────────────────
        if flags.has_dc and analog_comps:
            dc_comps = [VoltageSource(c.name, c.n_pos, c.n_neg, 0.0)
                        if isinstance(c, VoltageSourceAC) else c for c in analog_comps]
            dc = self.solver.solve_dc(dc_comps)
            out.append(self.tr("── DC Voltages ──"))
            if dc["success"]:
                for node, v in sorted(dc["voltages"].items()):
                    out.append(f"  V({node}) = {v:+.4f} V")
                if dc.get("branch_currents"):
                    out.append(""); out.append("── Corrientes DC ──")
                    for name, i in dc["branch_currents"].items():
                        out.append(f"  I({name}) = {i*1000:+.4f} mA")
                for item in sim_components:
                    n1 = item.node1.strip() or pin_node.get(f"{item.name}__p1", "")
                    n2 = item.node2.strip() or pin_node.get(f"{item.name}__p2", "0")
                    item.result_voltage = self._display_voltage(
                        item, dc["voltages"].get(n1),
                        dc["voltages"].get(n2, 0.0))
                    if item.comp_type in ComponentItem.LIGHT_TYPES:
                        item.led_on = False
                        op = dc.get("operating_points", {}).get(item.name, {})
                        Id_op = op.get("Id", op.get("id")) if op else None
                        if item.comp_type == 'LED' and Id_op is not None:
                            item.led_on = float(Id_op) > 1e-4
                        else:
                            v_a = dc["voltages"].get(n1, 0)
                            v_k = dc["voltages"].get(n2, 0)
                            item.led_on = self._light_is_on(item, v_a, v_k)
                    item.update()
            else:
                out.append(f"  ✗ {dc['error']}")
            out.append("")

        # ── AC ────────────────────────────────────────────────────────────
        if flags.has_ac and analog_comps:
            freq = next((it.frequency for it in sim_components
                         if it.comp_type in ("VAC", "FGEN")), 60.0)

            if flags.has_nonlinear:
                # Diodos/BJT/MOSFET con AC → análisis fasorial NO es válido
                # (el componente no-lineal genera armónicos). Corremos
                # transient durante varios ciclos y reportamos forma de onda
                # estabilizada (último ciclo).
                import numpy as _np
                T        = 1.0 / freq if freq > 0 else 1e-3
                n_cycles = 3
                tr = self.solver.solve_transient(
                    analog_comps,
                    t_stop      = n_cycles * T,
                    dt          = T / 200.0,
                    method      = 'trapezoidal',
                    adaptive    = True,
                    nr_tol      = 1e-5,
                    nr_max_iter = 30,
                )
                out.append(
                    f"── Transient (nonlinear + AC, f={freq:g} Hz, "
                    f"{n_cycles} cycles) ──")
                if tr["success"]:
                    t_arr      = tr["time"]
                    v_dict     = tr["voltages"]
                    last_cycle = t_arr >= (n_cycles - 1) * T
                    for node, v_arr in sorted(v_dict.items()):
                        v_last = v_arr[last_cycle]
                        if len(v_last) == 0:
                            continue
                        vmax  = float(v_last.max())
                        vmin  = float(v_last.min())
                        vmean = float(v_last.mean())
                        vrms  = float(_np.sqrt(_np.mean(v_last ** 2)))
                        out.append(
                            f"  V({node}): pk=[{vmin:+.3f}, {vmax:+.3f}] V  "
                            f"DC={vmean:+.3f} V  RMS={vrms:.3f} V")
                    out.append(
                        f"  steps={tr['steps']}  "
                        f"dt_avg={tr['dt_stats']['mean']*1e6:.1f} µs")

                    # LEDs según corriente promedio en último ciclo (si la
                    # corriente promedio del LED supera ~0.1 mA → encendido)
                    self._update_leds_from_transient(
                        sim_components, tr, last_cycle, pin_node)

                    self._last_transient_result = tr
                    self.btn_power_triangle.setVisible(False)
                else:
                    out.append(f"  ✗ {tr['error']}")
                out.append("")
            else:
                # Circuito lineal: análisis fasorial estándar (rápido y exacto)
                ac = self.solver.solve_ac_single(analog_comps, freq)
                out.append(f"── Fasores AC ({freq} Hz) ──")
                if ac["success"]:
                    for node, V in sorted(ac["voltages"].items()):
                        out.append(f"  V({node}) = {abs(V):.4f} V  ∠{_cmath.phase(V)*180/_cmath.pi:.2f}°")
                    t = ac.get("total", {})
                    if t:
                        fp_type = self._localized_power_factor_type(t.get('fp_type', ''))
                        out += ["", self.tr("── Total power ──"),
                                f"  P={t.get('P',0):+.4f} W  Q={t.get('Q',0):+.4f} VAR",
                                f"  S={t.get('S',0):.4f} VA  fp={t.get('fp',0):.4f} ({fp_type})"]
                    self._last_ac_result = ac
                    self.btn_power_triangle.setVisible(True)
                else:
                    out.append(f"  ✗ {ac['error']}")
                out.append("")

        # ── Mixto ─────────────────────────────────────────────────────────
        if flags.needs_mixed:
            # Si no hay puentes reales ni nodos frontera, no hace falta
            # co-simulación transitoria: correr DC analógico + digital por separado
            _only_isolated = (
                not flags.has_bridges
                and not flags.implicit_boundary_nodes
            )
            if _only_isolated:
                # Evaluar puertas digitales usando los voltajes DC ya calculados
                # para determinar los niveles lógicos en las entradas
                _dc_voltages = {}
                if flags.has_dc and analog_comps:
                    _dc_res = self.solver.solve_dc(
                        [VoltageSource(c.name, c.n_pos, c.n_neg, 0.0)
                         if isinstance(c, VoltageSourceAC) else c
                         for c in analog_comps])
                    if _dc_res.get("success"):
                        _dc_voltages = _dc_res["voltages"]
                self._evaluate_digital_gates(pin_node, _dc_voltages, out=out,
                                             sim_comps=sim_components)
                out.append("")
                self.results_text.setPlainText("\n".join(out))
                self.scene.update()
                return
            # Un astable 555 común con R/C del orden de kΩ/µF tarda decenas
            # de ms por ciclo; 1 ms sólo muestra el arranque.
            t_stop = 0.1 if any(it.comp_type == 'IC555' for it in sim_components) else 1e-3
            dt_chunk = max(t_stop / 100, 1e-6)
            dsim = DigitalSimulator()
            adc_list, dac_list, comparator_list = [], [], []
            _gmap = {"AND":"AND","OR":"OR","NOT":"NOT","NAND":"NAND","NOR":"NOR","XOR":"XOR"}

            def net(item, pin, attr=None):
                manual = getattr(item, attr, '').strip() if attr else ''
                return manual or pin_node.get(f"{item.name}__p{pin}", "")

            for item in sim_components:
                ct = item.comp_type; tpd = item.dig_tpd_ns * 1e-9
                try:
                    if ct in _gmap:
                        n_in = max(1, item.dig_inputs)
                        inputs = [net(item, 2, 'node2')]
                        if n_in > 1:
                            inputs.append(net(item, 3, 'node3'))
                        inputs.extend(
                            (item.dig_input_nodes[i - 2].strip()
                             if len(item.dig_input_nodes) > i - 2
                             and item.dig_input_nodes[i - 2].strip()
                             else net(item, i + 2))
                            for i in range(2, n_in))
                        dsim.add(Gate(item.name, _gmap[ct],
                                      inputs, net(item, 1, 'node1'), t_pd=tpd,
                                      input_invert=list(
                                          getattr(item, 'dig_input_neg', []) or [])))
                    elif ct == "DFF":
                        dsim.add(DFF(item.name, d=net(item, 2, 'node2'),
                                     clk=net(item, 3, 'node3') or item.dig_clk,
                                     q=net(item, 1, 'node1'), qn=net(item, 6),
                                     reset=net(item, 5), set_=net(item, 4), t_pd=tpd))
                    elif ct == "JKFF":
                        dsim.add(JKFF(item.name, j=net(item, 2, 'node2'),
                                      k=net(item, 3, 'node3'), clk=item.dig_clk,
                                      q=net(item, 1, 'node1'), qn=net(item, 6),
                                      reset=net(item, 5), set_=net(item, 4), t_pd=tpd))
                    elif ct == "TFF":
                        dsim.add(TFF(item.name, t_in=net(item, 2, 'node2'),
                                     clk=net(item, 3, 'node3') or item.dig_clk,
                                     q=net(item, 1, 'node1'), qn=net(item, 6),
                                     reset=net(item, 5), set_=net(item, 4), t_pd=tpd))
                    elif ct == "SRFF":
                        dsim.add(SRFF(item.name, s=net(item, 2, 'node2'),
                                      r=net(item, 3, 'node3'),
                                      q=net(item, 1, 'node1'), qn=net(item, 6), t_pd=tpd))
                    elif ct == "MUX2":
                        dsim.add(MUX(item.name, [net(item, 2, 'node2'),
                                                 net(item, 3, 'node3')],
                                     [net(item, 4)], net(item, 1, 'node1'), t_pd=tpd))
                    elif ct == "COUNTER":
                        q_outputs = [net(item, 1, 'node1') or f'{item.name}_Q0']
                        q_outputs.extend(
                            net(item, pin) or f'{item.name}_Q{pin - 2}'
                            for pin in range(3, item.dig_bits + 2))
                        dsim.add(BinaryCounter(item.name, max(1, item.dig_bits),
                                               net(item, 2, 'node2') or item.dig_clk,
                                               q_outputs=q_outputs,
                                               t_pd=tpd))
                    elif ct == "IC555":
                        p = lambda n: (item.timer_nodes[n - 1].strip()
                                       or pin_node.get(f"{item.name}__p{n}", f"{item.name}_P{n}"))
                        dsim.add(Timer555(item.name, p(1), p(2), p(3), p(4),
                                           p(5), p(6), p(7), p(8), t_pd=tpd))
                    elif ct == "ADC_BRIDGE":
                        nd = item.dig_analog_node or pin_node.get(f"{item.name}__p1","")
                        adc_list.append(ADC(item.name, node=nd, bits=item.dig_bits_adc, vref=item.dig_vref))
                    elif ct == "DAC_BRIDGE":
                        nd = item.dig_analog_node or pin_node.get(f"{item.name}__p1","")
                        dac_list.append(DAC(item.name, bits=item.dig_bits_adc, vref=item.dig_vref, out_node=nd))
                    elif ct == "COMPARATOR":
                        nd = item.dig_analog_node or pin_node.get(f"{item.name}__p1","")
                        comparator_list.append(ComparatorBridge(item.name, node_pos=nd))
                    elif ct in ('LOGIC_STATE', 'CLK'):
                        out_net = net(item, 1, 'node1')
                        if out_net:
                            dsim.set_input(out_net, int(bool(item.value)), at=0.0)
                except Exception as e:
                    build_errors.append(f"{item.name}: {e}")
            if flags.implicit_boundary_nodes:
                std = DEFAULT_STANDARD
                out.append(self.tr("── Implicit boundaries ({name}) ──").format(name=std.name))
                logic_drivers = []
                for index, node in enumerate(flags.implicit_boundary_nodes):
                    detail = flags.boundary_detail[node]
                    out.append(f"  Nodo '{node}'")
                    if detail['analog_to_digital']:
                        comparator_list.append(ComparatorBridge(
                            f"__impl_{node}", node_pos=node, output_net=node,
                            vref=(std.Vil + std.Vih) / 2,
                            hysteresis=std.Vih - std.Vil))
                    if detail['digital_to_analog']:
                        source_name = f"__logic_driver_{index}"
                        analog_comps.append(VoltageSource(source_name, node, '0', std.Vol))
                        logic_drivers.append((node, source_name, std.Vol, std.Voh))
                out.append("")
            else:
                logic_drivers = []
            if analog_comps:
                iface = MixedSignalInterface(self.solver, dsim, analog_comps)
                for a in adc_list: iface.add_adc(a)
                for comparator in comparator_list:
                    iface.add_comparator(comparator)
                for driver in logic_drivers:
                    iface.add_logic_driver(*driver)
                for d in dac_list:
                    if hasattr(d,"pwm_net"): iface.add_pwm(d)
                    elif hasattr(d,"input_nets"): iface.add_dac(d)
                    else: iface.add_comparator(d)
                mr = iface.run_iterative(t_stop=t_stop, dt_chunk=dt_chunk,
                                          dt_analog=min(dt_chunk/10, 1e-6))
                out.append(self.tr("── Mixed co-simulation ──"))
                if mr.success:
                    for nd, arr in sorted(mr.analog_voltages.items()):
                        if len(arr) > 0: out.append(f"  V({nd}) = {arr[-1]:+.4f} V")
                    for net, hist in sorted(mr.digital_waveforms.items()):
                        if hist and not net.startswith("__impl"):
                            out.append(f"  {net} = {hist[-1][1]}")
                else:
                    out.append(f"  ✗ {mr.error}")
                out.append("")

        if build_errors:
            out.append("── Warnings ──")
            out.extend([f"  ⚠ {e}" for e in build_errors])
        self.results_text.setPlainText("\n".join(out))
        self.scene.update()

    def _update_leds_from_transient(self, sim_components, tr, last_cycle_mask,
                                    pin_node):
        """Actualiza LEDs y bombillos a partir del voltaje del último ciclo."""
        v_dict = tr.get("voltages", {})
        if not v_dict:
            return
        for item in sim_components:
            if item.comp_type not in ComponentItem.LIGHT_TYPES:
                continue
            n_a = item.node1.strip() or pin_node.get(f"{item.name}__p1", "")
            n_k = item.node2.strip() or pin_node.get(f"{item.name}__p2", "0")
            vd = self._voltage_drop_array(v_dict, n_a, n_k)
            if vd is None or len(vd) == 0:
                item.led_on = False
                item.update()
                continue
            try:
                vd = vd[last_cycle_mask]
                if item.comp_type == 'LAMP':
                    item.led_on = abs(float(vd[-1])) >= self._light_threshold(item)
                else:
                    i_led = self._estimate_led_current(
                        vd, getattr(item, 'led_color', 'red'))
                    item.led_on = float(np.mean(i_led)) > 1e-4
            except Exception:
                item.led_on = False
            item.update()

    def _run_simulation_dc(self, silent: bool = False):
        components = []
        errors = []

        # Usar contexto multi-hoja si disponible (net labels / múltiples hojas)
        sim_comps = getattr(self, '_sim_all_comps', None) or list(self.scene.components)
        pin_node = getattr(self, '_sim_pin_node', None) or self.scene.extract_netlist()

        for item in sim_comps:
            # Net labels y nodos auxiliares no generan componentes de engine
            if item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT', 'GND', 'NODE'):
                continue

            # Prioridad: nodo manual del usuario > nodo extraido automaticamente
            auto_n1 = pin_node.get(f"{item.name}__p1", f'iso_{item.name}_p')
            auto_n2 = pin_node.get(f"{item.name}__p2", '0')
            auto_n3 = pin_node.get(f"{item.name}__p3", '')

            n1 = item.node1.strip() if item.node1.strip() else auto_n1
            n2 = item.node2.strip() if item.node2.strip() else auto_n2
            n3 = item.node3.strip() if item.node3.strip() else auto_n3

            try:
                # ── Componentes digitales: se ignoran en DC/AC ──────────
                if item.comp_type in ComponentItem.DIGITAL_TYPES:
                    # LOGIC_STATE / CLK: modelar como fuente de voltaje ideal
                    if item.comp_type in ('LOGIC_STATE', 'CLK'):
                        std = DEFAULT_STANDARD
                        v_out = std.Voh if item.value else std.Vol
                        out_node = item.node1.strip() or pin_node.get(f"{item.name}__p1", f"ls_{item.name}")
                        if out_node and out_node not in ('0', 'gnd', 'GND'):
                            components.append(VoltageSource(item.name, out_node, '0', v_out))
                    continue

                # En DC la fuente VAC vale 0 V (valor medio de senoidal)
                if item.comp_type == 'VAC':
                    components.append(VoltageSource(item.name, n1, n2, 0.0))
                    continue

                # Validación específica de R
                if item.comp_type == 'R' and item.value <= 0:
                    errors.append(f"{item.name}: resistance must be > 0")
                    continue

                # Resto de componentes analógicos (incluye POT, XFMR, BRIDGE)
                components.extend(build_engine_components_for_item(item, pin_node))
            except Exception as e:
                errors.append(f"{item.name}: {e}")

        # ── Excluir LEDs/Diodos cuyo ánodo es salida exclusiva de puerta digital ──
        # Esos componentes no tienen driver analógico → matriz singular.
        # Se evalúan luego con _evaluate_digital_gates.
        _gate_types_dc = {'AND','OR','NOT','NAND','NOR','XOR','NAND','NOR',
                          'DFF','JKFF','TFF','SRFF','MUX2','COUNTER','IC555'}
        _dig_out_nodes = set()
        for _item in sim_comps:
            if _item.comp_type in _gate_types_dc:
                if _item.comp_type == 'IC555':
                    _pins = (3, 7)
                elif _item.comp_type == 'COUNTER':
                    _pins = (1, *range(3, max(1, _item.dig_bits) + 2))
                else:
                    _pins = (1,)
                for _pin in _pins:
                    _on = (_item.node1.strip() if _pin == 1 else '') or pin_node.get(
                        f"{_item.name}__p{_pin}", "")
                    if _on and _on not in ('0','gnd','GND'):
                        _dig_out_nodes.add(_on)
        # Reunir todos los nodos que tienen driver analógico activo
        # (fuentes de voltaje/corriente, BJT/MOSFET/OpAmp). Los pasivos
        # como R, L, C, Diode NO se cuentan como drivers — sólo aportan
        # caminos pasivos.
        _analog_driver_nodes = set()
        for _c in components:
            if _c.__class__.__name__ == 'Diode':
                continue
            for _attr in ('n_pos','n_neg','n_p','n_n','n_out','n_in',
                          'n_base','n_collector','n_emitter',
                          'n_gate','n_drain','n_source'):
                _nd = getattr(_c, _attr, None)
                if _nd and _nd not in ('0','gnd','GND'):
                    _analog_driver_nodes.add(_nd)
        # Quitar del netlist analógico los LED/Diodo cuyo ánodo está en
        # un nodo de salida sólo-digital. Diode usa n_a (ánodo).
        # Antes este filtro buscaba `n_p`/`n_pos` que Diode no tiene,
        # por lo que nunca excluía nada — funcionaba sólo porque los
        # nodos del diodo tampoco entraban al node_map. Tras corregir
        # _build_maps, los diodos sí se estampan, así que el filtro
        # tiene que usar el atributo correcto.
        components = [
            _c for _c in components
            if not (
                (
                    _c.__class__.__name__ == 'Diode'
                    and getattr(_c, 'n_a', '') in _dig_out_nodes
                    and getattr(_c, 'n_a', '') not in _analog_driver_nodes
                ) or (
                    getattr(_c, 'is_lamp', False)
                    and getattr(_c, 'n1', '') in _dig_out_nodes
                    and getattr(_c, 'n1', '') not in _analog_driver_nodes
                )
            )
        ]

        if not components:
            # Solo puertas digitales y LEDs en sus salidas: evaluar directo.
            # FIX: construir dc_voltages desde los LOGIC_STATE antes de
            # llamar a _evaluate_digital_gates. Sin esto, el diccionario
            # llega vacío y todas las entradas se leen como 0 V (LOW).
            std = DEFAULT_STANDARD
            _dig_voltages = {}
            for _it in sim_comps:
                if _it.comp_type in ('LOGIC_STATE', 'CLK'):
                    _v = std.Voh if _it.value else std.Vol
                    _net = _it.node1.strip() or pin_node.get(f"{_it.name}__p1", "")
                    if _net:
                        _dig_voltages[_net] = _v
            out = [self.tr("═══ DIGITAL SIMULATION ═══"), ""]
            self._evaluate_digital_gates(pin_node, _dig_voltages, silent=silent, out=out, sim_comps=sim_comps)
            if not silent:
                self.results_text.setPlainText('\n'.join(out))
            for sheet in self._sheets:
                sheet['scene'].update()
            return

        # Mostrar netlist extraida antes de simular
        if not silent:
            out_pre = ["═══ NETLIST EXTRAIDA ═══"]
            for item in sim_comps:
                if item.comp_type in ('GND', 'NODE'):
                    continue
                auto_n1 = pin_node.get(f"{item.name}__p1", '?')
                auto_n2 = pin_node.get(f"{item.name}__p2", '?')
                n1_show = item.node1.strip() if item.node1.strip() else auto_n1
                n2_show = item.node2.strip() if item.node2.strip() else auto_n2
                out_pre.append(f"  {item.name}: {n1_show} → {n2_show}  ({item._format_value()})")
            out_pre.append("")
            self.results_text.setPlainText('\n'.join(out_pre) + "Simulando...")
            QApplication.processEvents()

        result = self.solver.solve_dc(components)

        # Mostrar resultados
        out = []
        if result['success']:
            out.append(self.tr("═══ DC ANALYSIS ═══\n"))
            out.append(self.tr("── Node voltages ──"))
            for node, v in sorted(result['voltages'].items()):
                out.append(f"  V({node}) = {v:+.4f} V")
            if result.get('branch_currents'):
                out.append(self.tr("\n── Branch currents ──"))
                for name, i in result['branch_currents'].items():
                    out.append(f"  I({name}) = {i*1000:+.4f} mA")

            # Iteraciones Newton-Raphson (si aplica)
            if 'iterations' in result:
                out.append(self.tr("\n  [NR converged in {iterations} iterations]").format(iterations=result['iterations']))
            if 'warning' in result:
                out.append(f"\n  ⚠ {result['warning']}")

            # Puntos de operación de componentes no-lineales
            if result.get('operating_points'):
                out.append(self.tr("\n── Operating points ──"))
                for comp_name, op in result['operating_points'].items():
                    out.append(f"  {comp_name}:")
                    for k, v in op.items():
                        if isinstance(v, float):
                            out.append(f"    {k} = {v:.4g}")
                        else:
                            out.append(f"    {k} = {v}")

            # Corrientes y potencias
            out.append("\n── Corrientes y potencias ──")
            for comp in components:
                if isinstance(comp, VoltageSource):
                    i_branch = result['branch_currents'].get(comp.name, 0)
                    p = comp.V * i_branch
                    out.append(f"  I({comp.name}) = {i_branch*1000:+.4f} mA  |  P = {abs(p):.4f} W")
                elif isinstance(comp, Resistor):
                    v1 = result['voltages'].get(comp.n1, 0)
                    v2 = result['voltages'].get(comp.n2, 0)
                    i_r = (v1 - v2) / comp.R
                    p   = (v1 - v2)**2 / comp.R
                    out.append(f"  I({comp.name}) = {i_r*1000:+.4f} mA  |  P = {p*1000:.4f} mW")
                elif isinstance(comp, CurrentSource):
                    v1 = result['voltages'].get(comp.n_pos, 0)
                    v2 = result['voltages'].get(comp.n_neg, 0)
                    p = comp.I_val * (v1 - v2)
                    out.append(f"  I({comp.name}) = {comp.I_val*1000:+.4f} mA  |  P = {abs(p):.4f} W")

            # Actualizar canvas con voltajes y estado LED
            for item in sim_comps:
                auto_n1 = pin_node.get(f"{item.name}__p1", '')
                auto_n2 = pin_node.get(f"{item.name}__p2", '0')
                n1 = item.node1.strip() if item.node1.strip() else auto_n1
                n2 = item.node2.strip() if item.node2.strip() else auto_n2
                item.result_voltage = self._display_voltage(
                    item, result['voltages'].get(n1),
                    result['voltages'].get(n2, 0.0))
                if item.comp_type == 'MULTIMETER':
                    self._update_multimeter_from_dc(
                        item, n1, n2, result['voltages'])
                if item.comp_type in ComponentItem.LIGHT_TYPES:
                    led_on = False
                    op = result.get('operating_points', {}).get(item.name, {})
                    id_ = None
                    if op:
                        id_ = op.get('Id', op.get('id', op.get('I', None)))
                    if id_ is not None:
                        led_on = float(id_) > 1e-4
                    else:
                        v_a = result['voltages'].get(n1, None)
                        v_k = result['voltages'].get(n2, None)
                        if v_a is not None and v_k is not None:
                            led_on = self._light_is_on(item, v_a, v_k)
                    item.led_on = led_on
                item.update()
                if hasattr(item, 'scene') and item.scene():
                    item.scene().update(item.mapToScene(item.boundingRect()).boundingRect())

            # Debug LED — mostrar info de nodos y voltajes del LED
            led_items = [it for it in sim_comps
                         if it.comp_type in ComponentItem.LIGHT_TYPES]
            if led_items:
                out.append("\n── Debug LED ──")
                for it in led_items:
                    auto_n1 = pin_node.get(f"{it.name}__p1", '?')
                    auto_n2 = pin_node.get(f"{it.name}__p2", '?')
                    n1d = it.node1.strip() if it.node1.strip() else auto_n1
                    n2d = it.node2.strip() if it.node2.strip() else auto_n2
                    va  = result['voltages'].get(n1d, 'N/A')
                    vk  = result['voltages'].get(n2d, 'N/A')
                    op  = result.get('operating_points', {}).get(it.name, {})
                    out.append(f"  {it.name}: anode={n1d}({va}) cathode={n2d}({vk})")
                    out.append(f"    op={op}  led_on={it.led_on}")

        else:
            if not silent:
                out.append(self.tr("✗ Simulation error:\n{error}").format(error=result['error']))
                out.append(self.tr("\nCheck that the circuit has:"))
                out.append(self.tr("  • At least one voltage source"))
                out.append("  • Ground node (node '0')")
                out.append(self.tr("  • Nodes assigned to every component"))

        if errors and not silent:
            out.append("\n── Warnings ──")
            out.extend([f"  ⚠ {e}" for e in errors])

        # Evaluar puertas digitales y actualizar LEDs en su salida
        if result.get('success'):
            self._evaluate_digital_gates(pin_node, result['voltages'], silent=silent, out=out, sim_comps=sim_comps)
            # _evaluate_digital_gates ya escribió los voltajes de las salidas
            # digitales en result['voltages']; re-leer los multímetros para
            # que también midan nodos digitales (no sólo los analógicos).
            for item in sim_comps:
                if item.comp_type != 'MULTIMETER':
                    continue
                m_n1 = item.node1.strip() or pin_node.get(f"{item.name}__p1", '')
                m_n2 = item.node2.strip() or pin_node.get(f"{item.name}__p2", '0')
                self._update_multimeter_from_dc(
                    item, m_n1, m_n2, result['voltages'])

        if not silent:
            self.results_text.setPlainText('\n'.join(out))
        for sheet in self._sheets:
            sheet['scene'].update()
        self._refresh_open_multimeter_panels()


    def _evaluate_digital_gates(self, pin_node, dc_voltages, silent=False, out=None, sim_comps=None):
        std = DEFAULT_STANDARD
        _gmap = {'AND':'AND','OR':'OR','NOT':'NOT','NAND':'NAND','NOR':'NOR','XOR':'XOR'}
        _funcs = {
            'AND':  lambda vals: all(vals),
            'OR':   lambda vals: any(vals),
            'NAND': lambda vals: not all(vals),
            'NOR':  lambda vals: not any(vals),
            'XOR':  lambda vals: bool(sum(vals) % 2),
            'NOT':  lambda vals: not bool(vals[0]),
        }
        _all = sim_comps if sim_comps is not None else list(self.scene.components)
        gate_items = [it for it in _all if it.comp_type in _gmap]
        if gate_items and out is not None and not silent:
            out.append(self.tr('\n── Digital signals ──'))
        for item in gate_items:
            n_in = max(1, item.dig_inputs)
            input_logics = []
            neg_mask = list(getattr(item, 'dig_input_neg', []) or [])
            for i in range(n_in):
                if i == 0:
                    node = (item.node2.strip()
                            or pin_node.get(f'{item.name}__p2', ''))
                elif i == 1:
                    _n3 = item.node3.strip() if hasattr(item, 'node3') else ''
                    node = _n3 or pin_node.get(f'{item.name}__p3', '')
                else:
                    _extra = getattr(item, 'dig_input_nodes', [])
                    _manual_extra = _extra[i-2].strip() if len(_extra) > i-2 else ''
                    node = _manual_extra or pin_node.get(f'{item.name}__p{i+2}', '')
                if node in ('0', 'gnd', 'GND'):
                    v = 0.0
                elif not node:
                    v = 0.0
                else:
                    v = dc_voltages.get(node, 0.0)
                bit = 1 if v >= std.Vih else 0
                # Si la entrada está marcada como negada (bubble), se invierte
                # antes de evaluar la función de la compuerta.
                if i < len(neg_mask) and neg_mask[i]:
                    bit = 1 - bit
                input_logics.append(bit)
            y = int(_funcs[item.comp_type](input_logics))
            out_node = item.node1.strip() or pin_node.get(f'{item.name}__p1', '')
            v_out = std.Voh if y else std.Vol
            if out_node and out_node not in ('0', 'gnd', 'GND'):
                dc_voltages[out_node] = v_out
            for led in _all:
                if led.comp_type in ComponentItem.LIGHT_TYPES:
                    led_anode = led.node1.strip() or pin_node.get(f'{led.name}__p1', '')
                    if led_anode == out_node:
                        led.led_on = SimulationController._light_is_on(led, v_out)
                        led.update()
            if out is not None and not silent:
                out.append(f"  {item.name}_Y = {y}  ({'HIGH' if y else 'LOW'})")

        # ── NE555 ────────────────────────────────────────────────────────
        # Modelo digital del biestable interno: RESET bajo domina, THRESH
        # alto borra y TRIG bajo fija. Pin 7 (DISCH) es el complemento de OUT.
        timer_items = [it for it in _all if it.comp_type == 'IC555']
        if timer_items and out is not None and not silent:
            out.append('\n── Temporizadores 555 ──')
        for item in timer_items:
            def _bit(pin):
                node = (item.timer_nodes[pin - 1].strip()
                        or pin_node.get(f'{item.name}__p{pin}', ''))
                return 0 if node in ('', '0', 'gnd', 'GND') else int(
                    dc_voltages.get(node, 0.0) >= std.Vih)
            reset, trig, thresh = _bit(4), _bit(2), _bit(6)
            if not reset or thresh:
                item.dig_q_state = 0
            elif not trig:
                item.dig_q_state = 1
            q = int(item.dig_q_state)
            for pin, value in ((3, q), (7, 1 - q)):
                node = (item.timer_nodes[pin - 1].strip()
                        or pin_node.get(f'{item.name}__p{pin}', ''))
                if node and node not in ('0', 'gnd', 'GND'):
                    dc_voltages[node] = std.Voh if value else std.Vol
            item.update()
            if out is not None and not silent:
                out.append(f"  {item.name}: OUT={q}, DISCH={1-q}")

        # ── Multiplexores 2:1 ────────────────────────────────────────────
        # p1=salida, p2=I0, p3=I1, p4=SEL.  Y = I1 si SEL=1, si no I0.
        mux_items = [it for it in _all if it.comp_type == 'MUX2']
        if mux_items and out is not None and not silent:
            out.append('\n── Multiplexores ──')
        for item in mux_items:
            def _v(node):
                if not node or node in ('0', 'gnd', 'GND'):
                    return 0.0
                return dc_voltages.get(node, 0.0)
            n_i0  = item.node2.strip() or pin_node.get(f'{item.name}__p2', '')
            n_i1  = (item.node3.strip() if hasattr(item, 'node3') else '') \
                    or pin_node.get(f'{item.name}__p3', '')
            n_sel = (item.node4.strip() if hasattr(item, 'node4') else '') \
                or pin_node.get(f'{item.name}__p4', '')
            n_out = item.node1.strip() or pin_node.get(f'{item.name}__p1', '')
            sel = 1 if _v(n_sel) >= std.Vih else 0
            chosen = n_i1 if sel else n_i0
            y = 1 if _v(chosen) >= std.Vih else 0
            v_out = std.Voh if y else std.Vol
            if n_out and n_out not in ('0', 'gnd', 'GND'):
                dc_voltages[n_out] = v_out
            for led in _all:
                if led.comp_type in ComponentItem.LIGHT_TYPES:
                    la = led.node1.strip() or pin_node.get(f'{led.name}__p1', '')
                    if la == n_out:
                        led.led_on = SimulationController._light_is_on(led, v_out)
                        led.update()
            if out is not None and not silent:
                out.append(f"  {item.name}: SEL={sel} → Y={y}")

        # ── Evaluación de flip-flops (DFF/JKFF/TFF/SRFF) ─────────────────
        # Lectura de niveles desde dc_voltages, prioridad SET/RESET asíncronos,
        # y actualización del círculo de memoria (dig_q_state).
        ff_items = [it for it in _all
                    if it.comp_type in ComponentItem.FLIPFLOP_TYPES]
        if ff_items and out is not None and not silent:
            out.append('\n── Flip-flops ──')

        def _logic_at(node: str) -> int:
            if not node or node in ('0', 'gnd', 'GND'):
                return 0
            return 1 if dc_voltages.get(node, 0.0) >= std.Vih else 0

        for item in ff_items:
            # Resolver nodos de cada pin: manual > automático.
            n_q   = item.node1.strip() or pin_node.get(f'{item.name}__p1', '')
            n_in1 = item.node2.strip() or pin_node.get(f'{item.name}__p2', '')
            n_in2 = (item.node3.strip() if hasattr(item, 'node3') else '') \
                    or pin_node.get(f'{item.name}__p3', '')
            n_set = pin_node.get(f'{item.name}__p4', '')
            n_rst = pin_node.get(f'{item.name}__p5', '')

            # Persistir último valor de CLK por flip-flop para detectar flancos.
            last_clk = getattr(item, '_last_clk_seen', 0)
            q_prev   = int(getattr(item, 'dig_q_state', 0))
            q_new    = q_prev

            set_active = bool(_logic_at(n_set))
            rst_active = bool(_logic_at(n_rst))

            if rst_active:
                q_new = 0
            elif set_active:
                q_new = 1
            elif item.comp_type == 'SRFF':
                # Asíncrono: S=p2, R=p3
                S = _logic_at(n_in1)
                R = _logic_at(n_in2)
                if S and R:    q_new = 0    # estado prohibido → 0
                elif S:        q_new = 1
                elif R:        q_new = 0
            else:
                # Síncrono por flanco de subida.
                # DFF/TFF: CLK está en p3 (entrada secundaria)
                # JKFF: J=p2, K=p3, CLK = item.dig_clk (net global)
                if item.comp_type == 'JKFF':
                    clk_now = _logic_at(item.dig_clk)
                else:
                    clk_now = _logic_at(n_in2)
                if clk_now == 1 and last_clk == 0:
                    if item.comp_type == 'DFF':
                        q_new = _logic_at(n_in1)
                    elif item.comp_type == 'TFF':
                        if _logic_at(n_in1):
                            q_new = 1 - q_prev
                    elif item.comp_type == 'JKFF':
                        J = _logic_at(n_in1)
                        K = _logic_at(n_in2)
                        if   J == 0 and K == 0: pass
                        elif J == 0 and K == 1: q_new = 0
                        elif J == 1 and K == 0: q_new = 1
                        else:                   q_new = 1 - q_prev
                item._last_clk_seen = clk_now

            item.dig_q_state = q_new

            # Propagar Q y Q̄ a sus nodos para que la cadena digital los vea
            if n_q and n_q not in ('0', 'gnd', 'GND'):
                dc_voltages[n_q] = std.Voh if q_new else std.Vol
            n_qn = pin_node.get(f'{item.name}__p6', '')
            if n_qn and n_qn not in ('0', 'gnd', 'GND'):
                dc_voltages[n_qn] = std.Voh if (1 - q_new) else std.Vol

            # Refrescar LEDs cuyo ánodo cae sobre Q o Q̄ del flip-flop. Sin
            # esto, el LED quedaría apagado aunque la salida del FF esté en
            # alto, porque la actualización de led_on se hacía sólo en el
            # bucle de compuertas.
            for led in _all:
                if led.comp_type not in ComponentItem.LIGHT_TYPES:
                    continue
                led_anode = (led.node1.strip()
                             or pin_node.get(f'{led.name}__p1', ''))
                if not led_anode:
                    continue
                if led_anode == n_q:
                    led.led_on = SimulationController._light_is_on(
                        led, std.Voh if q_new else std.Vol)
                    led.update()
                elif led_anode == n_qn:
                    led.led_on = SimulationController._light_is_on(
                        led, std.Voh if (1 - q_new) else std.Vol)
                    led.update()

            # Repintar el componente para reflejar el círculo de memoria
            item.update()

            if out is not None and not silent:
                out.append(f"  {item.name}.Q = {q_new}")

        # ── Contadores binarios ─────────────────────────────────────────
        # El símbolo actual expone Q0 por p1 y CLK por p2. Conservamos el
        # conteo completo para el indicador visual aunque sólo Q0 esté cableado.
        counter_items = [it for it in _all if it.comp_type == 'COUNTER']
        if counter_items and out is not None and not silent:
            out.append('\n── Contadores binarios ──')
        for item in counter_items:
            q_nodes = [item.node1.strip() or pin_node.get(f'{item.name}__p1', '')]
            q_nodes.extend(pin_node.get(f'{item.name}__p{pin}', '')
                           for pin in range(3, max(1, item.dig_bits) + 2))
            n_clk = item.node2.strip() or pin_node.get(f'{item.name}__p2', '') \
                or item.dig_clk
            clk_now = _logic_at(n_clk)
            last_clk = getattr(item, '_last_clk_seen', 0)
            mask = (1 << max(1, item.dig_bits)) - 1
            count = int(getattr(item, 'dig_count_state', 0))
            if clk_now and not last_clk:
                count = (count + 1) & mask
            item._last_clk_seen = clk_now
            item.dig_count_state = count
            for bit, node in enumerate(q_nodes):
                if node and node not in ('0', 'gnd', 'GND'):
                    dc_voltages[node] = std.Voh if (count >> bit) & 1 else std.Vol
            for led in _all:
                if led.comp_type not in ComponentItem.LIGHT_TYPES:
                    continue
                anode = led.node1.strip() or pin_node.get(f'{led.name}__p1', '')
                for bit, node in enumerate(q_nodes):
                    if anode == node:
                        led.led_on = SimulationController._light_is_on(
                            led, std.Voh if (count >> bit) & 1 else std.Vol)
                        led.update()
                        break
            item.update()
            if out is not None and not silent:
                out.append(f"  {item.name} = {count:0{max(1, item.dig_bits)}b}")

    # ── Panel de propiedades ─────────────────────
    def _run_simulation_ac(self):
        """Análisis AC de frecuencia única con triángulo de potencia."""
        from PyQt6.QtWidgets import QInputDialog

        # Usar contexto multi-hoja si disponible
        sim_comps = getattr(self, '_sim_all_comps', None) or list(self.scene.components)
        pin_node = getattr(self, '_sim_pin_node', None) or self.scene.extract_netlist()

        # Buscar fuente AC en el canvas para leer la frecuencia
        ac_items = [it for it in sim_comps if it.comp_type in ('VAC', 'FGEN')]
        if not ac_items:
            self.results_text.setPlainText(self.tr(
                "⚠  There are no AC sources in the circuit.\n"
                "Add a VAC or FGEN source for AC analysis."))
            return

        # Usar la frecuencia de la primera fuente AC como referencia
        freq_default = ac_items[0].frequency
        freq, ok = QInputDialog.getDouble(
            self, self.tr('Analysis Frequency'),
            self.tr('Frequency (Hz):'), freq_default, 0.001, 1e9, 3)
        if not ok:
            return

        components = []
        errors     = []

        for item in sim_comps:
            if item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT', 'GND', 'NODE'):
                continue
            auto_n1 = pin_node.get(f"{item.name}__p1", f'iso_{item.name}_p')
            auto_n2 = pin_node.get(f"{item.name}__p2", '0')
            n1 = item.node1.strip() if item.node1.strip() else auto_n1
            n2 = item.node2.strip() if item.node2.strip() else auto_n2

            try:
                if item.comp_type in ComponentItem.DIGITAL_TYPES:
                    continue
                # En AC pura, las fuentes DC contribuyen 0 V
                if item.comp_type == 'V':
                    components.append(VoltageSource(item.name, n1, n2, 0.0))
                    continue
                # Validación específica
                if item.comp_type == 'R' and item.value <= 0:
                    errors.append(f"{item.name}: R debe ser > 0")
                    continue
                # BRIDGE en AC: los diodos linealizados a Vd=0.6V actúan
                # como cortos (gd≈4.7e7 S) y vuelven la matriz singular
                # cuando el lado AC del puente está ligado al secundario
                # flotante de un transformador. Reemplazamos por 4 resistencias
                # de la misma topología que mantienen el sistema solvable
                # sin "shortear" entre sí los nodos AC1, AC2, DC+ y DC-.
                # La rectificación verdadera no se puede representar en
                # análisis fasorial; la salida DC real se reporta en
                # post-proceso (sección "Puentes rectificadores").
                if item.comp_type == 'BRIDGE':
                    auto_n3 = pin_node.get(f"{item.name}__p3", f'dcp_{item.name}')
                    auto_n4 = pin_node.get(f"{item.name}__p4", f'dcn_{item.name}')
                    n3_b = (item.node3.strip() if hasattr(item, 'node3') and item.node3.strip()
                            else auto_n3)
                    n4_b = (item.node4.strip() if hasattr(item, 'node4') and item.node4.strip()
                            else auto_n4)
                    R_BR = 1e4   # 10 kΩ — preserva topología sin cortocircuitar
                    components.append(Resistor(f'{item.name}_R1', n1,   n3_b, R_BR))
                    components.append(Resistor(f'{item.name}_R2', n2,   n3_b, R_BR))
                    components.append(Resistor(f'{item.name}_R3', n4_b, n1,   R_BR))
                    components.append(Resistor(f'{item.name}_R4', n4_b, n2,   R_BR))
                    continue
                # Transformador: tie a tierra de alta impedancia en la
                # primaria (lado −) para evitar matriz singular cuando el
                # usuario no la conecta explícitamente a GND. 1 MΩ apenas
                # carga al circuito (Iref ≈ V/1MΩ) pero define el modo
                # común y permite resolver el AC.
                if item.comp_type == 'XFMR':
                    components.extend(build_engine_components_for_item(item, pin_node))
                    components.append(Resistor(
                        f'{item.name}_GREF', n2, '0', 1e6))
                    continue
                # Resto: helper centralizado (POT, VAC, C, L, Z, I…)
                components.extend(build_engine_components_for_item(item, pin_node))
            except Exception as e:
                errors.append(f"{item.name}: {e}")

        if not components:
            self.results_text.setPlainText("⚠  There are no simulatable components.")
            return

        solver = MNASolver()
        result = solver.solve_ac_single(components, freq)

        out = [self.tr("═══ AC ANALYSIS ═══"), self.tr("  Frequency: {frequency} Hz").format(frequency=freq), ""]

        if errors:
            out.append(self.tr("⚠ Warnings:"))
            out += [f"  {e}" for e in errors]
            out.append("")

        if not result['success']:
            out.append(self.tr("✗ Error: {error}").format(error=result['error']))
            self.results_text.setPlainText('\n'.join(out))
            self.btn_power_triangle.setVisible(False)
            return

        # Lecturas de los multímetros con los fasores AC
        for item in sim_comps:
            if item.comp_type != 'MULTIMETER':
                continue
            n1 = item.node1.strip() or pin_node.get(f"{item.name}__p1", "")
            n2 = item.node2.strip() or pin_node.get(f"{item.name}__p2", "0")
            self._update_multimeter_from_ac(item, n1, n2, result['voltages'])
            item.update()
        self._refresh_open_multimeter_panels()

        # ── Voltajes nodales ──────────────────────────────────────────────
        out.append(self.tr("── Node voltages (Vrms / ∠°) ──"))
        for node, V in sorted(result['voltages'].items()):
            import cmath
            mag   = abs(V)
            phase = cmath.phase(V) * 180 / cmath.pi
            out.append(f"  V({node}) = {mag:.4f} V  ∠{phase:.2f}°")

        # ── Rectificación: análisis híbrido AC + DC ───────────────────────
        # El fasor AC no representa rectificación (es no-lineal). Para que
        # el puente "funcione de verdad" tras el AC corremos una segunda
        # solución DC en la que:
        #   • cada VAC contribuye 0 V (componente DC de la senoide)
        #   • cada BRIDGE se reemplaza por una VoltageSource(DC+, DC−)
        #     cuyo valor es V_pk − 2·Vf, calculado a partir del fasor que
        #     vio el puente en el AC. Así LEDs/resistencias/cargas en el
        #     lado DC ven tensión DC real y el circuito “rectifica”.
        bridges = [it for it in sim_comps if it.comp_type == 'BRIDGE']
        bridge_vdc: Dict[str, float] = {}
        if bridges:
            out.append(self.tr("\n── Bridge rectifiers (DC output) ──"))
            import math as _m
            for br in bridges:
                a1 = br.node1.strip() or pin_node.get(f"{br.name}__p1", "")
                a2 = br.node2.strip() or pin_node.get(f"{br.name}__p2", "")
                d_p = br.node3.strip() or pin_node.get(f"{br.name}__p3", "")
                d_n = (br.node4.strip()
                       if hasattr(br, 'node4') else
                       pin_node.get(f"{br.name}__p4", "")) \
                      or pin_node.get(f"{br.name}__p4", "")
                v1 = result['voltages'].get(a1, 0+0j)
                v2 = result['voltages'].get(a2, 0+0j)
                # Las tensiones del solver están en Vrms (fasor = Vrms·e^jφ).
                v_rms = abs(v1 - v2)
                v_pk  = v_rms * (2 ** 0.5)
                vf    = float(getattr(br, 'bridge_vf', 0.7) or 0.7)
                v_dc_peak = max(0.0, v_pk - 2 * vf)
                v_dc_avg  = max(0.0, (2.0 / _m.pi) * v_pk - 2 * vf)
                bridge_vdc[br.name] = v_dc_peak
                out.append(f"  {br.name} (V_f = {vf:.2f} V per diode):")
                out.append(f"    V_AC across AC1-AC2 : {v_rms:.4f} Vrms ({v_pk:.4f} Vpk)")
                out.append(f"    V_DC with filter    ≈ {v_dc_peak:.4f} V"
                           f"  (peak − 2·Vf, output {d_p} − {d_n})")
                out.append(f"    V_DC without filter ≈ {v_dc_avg:.4f} V"
                           f"  (full-wave average)")
                br.result_voltage = v_dc_peak

            # ── Construir circuito DC con el puente como fuente ideal ────
            dc_components = []
            for it in sim_comps:
                if it.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT', 'GND', 'NODE'):
                    continue
                if it.comp_type in ComponentItem.DIGITAL_TYPES:
                    continue
                a1 = it.node1.strip() or pin_node.get(f"{it.name}__p1", f'iso_{it.name}_p')
                a2 = it.node2.strip() or pin_node.get(f"{it.name}__p2", '0')
                if it.comp_type == 'V':
                    dc_components.append(VoltageSource(it.name, a1, a2, it.value))
                    continue
                if it.comp_type == 'VAC':
                    # AC source en DC = 0 V (valor medio de la senoidal)
                    dc_components.append(VoltageSource(it.name, a1, a2, 0.0))
                    continue
                if it.comp_type == 'BRIDGE':
                    d_p_b = it.node3.strip() or pin_node.get(f"{it.name}__p3", f'dcp_{it.name}')
                    d_n_b = (it.node4.strip() if hasattr(it,'node4') and it.node4.strip()
                             else pin_node.get(f"{it.name}__p4", f'dcn_{it.name}'))
                    vdc = bridge_vdc.get(it.name, 0.0)
                    # Fuente DC ideal entre DC+ y DC− equivalente al rectificado.
                    dc_components.append(
                        VoltageSource(f'{it.name}_DC', d_p_b, d_n_b, vdc))
                    # Mantener AC1/AC2 referenciados a DC− vía resistencia
                    # alta. Sin esto, el secundario del transformador queda
                    # flotando en la etapa DC y la matriz se vuelve singular.
                    dc_components.append(Resistor(
                        f'{it.name}_GR1', a1, d_n_b, 1e6))
                    dc_components.append(Resistor(
                        f'{it.name}_GR2', a2, d_n_b, 1e6))
                    continue
                # XFMR en DC: cada devanado es R_winding (no acopla AC→DC),
                # añadimos también la referencia a tierra para que no flote.
                if it.comp_type == 'XFMR':
                    dc_components.extend(build_engine_components_for_item(it, pin_node))
                    dc_components.append(Resistor(
                        f'{it.name}_GREF_DC', a2, '0', 1e6))
                    continue
                if it.comp_type == 'R' and it.value <= 0:
                    continue
                # Resto: igual que en DC normal
                try:
                    dc_components.extend(build_engine_components_for_item(it, pin_node))
                except Exception:
                    pass

            if dc_components:
                dc_solver = MNASolver()
                dc_res = dc_solver.solve_dc(dc_components)
                if dc_res.get('success'):
                    out.append("")
                    out.append(self.tr("── Rectified-side DC voltages ──"))
                    for node, v in sorted(dc_res['voltages'].items()):
                        out.append(f"  V({node}) = {v:+.4f} V")
                    if dc_res.get('branch_currents'):
                        out.append("")
                        out.append(self.tr("── DC currents ──"))
                        for name, i in dc_res['branch_currents'].items():
                            out.append(f"  I({name}) = {i*1000:+.4f} mA")
                    # Encender LEDs cuyos nodos quedaron polarizados con
                    # corriente directa real. Esto refleja en pantalla que
                    # el LED está conduciendo a la salida del puente.
                    op = dc_res.get('operating_points', {}) or {}
                    for it in sim_comps:
                        if it.comp_type not in ComponentItem.LIGHT_TYPES:
                            continue
                        a1 = it.node1.strip() or pin_node.get(f"{it.name}__p1", '')
                        a2 = it.node2.strip() or pin_node.get(f"{it.name}__p2", '0')
                        Id_op = (op.get(it.name, {}) or {}).get('Id') \
                                or (op.get(it.name, {}) or {}).get('id')
                        on = False
                        if it.comp_type == 'LED' and Id_op is not None:
                            on = float(Id_op) > 1e-4
                        else:
                            v_a = dc_res['voltages'].get(a1, 0.0)
                            v_k = dc_res['voltages'].get(a2, 0.0)
                            on = self._light_is_on(it, v_a, v_k)
                        it.led_on = on
                        it.update()
                else:
                    out.append(self.tr("\n  ⚠ Rectified-side DC analysis failed: {error}").format(error=dc_res.get('error')))

        # ── Potencias por componente ──────────────────────────────────────
        out.append(self.tr("\n── Power by component ──"))
        for name, pw in result['powers'].items():
            out.append(f"  {name}:")
            out.append(f"    P = {pw['P']:+.4f} W")
            out.append(f"    Q = {pw['Q']:+.4f} VAR")
            out.append(f"    S = {pw['S']:.4f} VA")
            out.append(f"    fp= {pw['fp']:.4f}")

        # ── Triángulo de potencia total ───────────────────────────────────
        t = result['total']
        out.append(self.tr("\n── Total circuit power ──"))
        out.append(f"  P  = {t['P']:+.4f} W      (real/active power)")
        out.append(f"  Q  = {t['Q']:+.4f} VAR    (reactive power)")
        out.append(f"  S  = {t['S']:.4f} VA     (apparent power)")
        out.append(f"  fp = {t['fp']:.4f}  ({self._localized_power_factor_type(t['fp_type'])})")
        out.append("")
        out.append(self.tr("  [Click '📐 View Power Triangle']"))

        if result.get('warning'):
            out.append(f"\n⚠ {result['warning']}")

        self.results_text.setPlainText('\n'.join(out))
        self._last_ac_result = result
        self.btn_power_triangle.setVisible(True)
