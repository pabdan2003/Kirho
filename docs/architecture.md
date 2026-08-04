# OhmPy architecture

This document explains how the project pieces fit together. It is intended for
someone cloning the repository for the first time who wants to know which
module does what, which conventions it assumes, and where extension points are.

For details about each class or method, go straight to the code docstrings.

---

## 1. Three engines, one UI

OhmPy tiene tres subsistemas de simulación separados: MNA analógico,
eventos digitales y coordinación mixta. No comparten matrices; el
coordinador intercambia voltajes y niveles lógicos entre ambos dominios.

```
       ┌────────────────────────────────────────────────────────────┐
       │                        UI (PyQt6)                          │
       │   main.py + ohmpy/ui/                                     │
       │   - Canvas, paleta, instrumentos, diálogos                 │
       └─────────┬─────────────────────────┬────────────────────────┘
                 │ build_engine_components │
                 ▼                         ▼
       ┌──────────────────┐      ┌──────────────────┐
       │  MNA Solver      │      │ DigitalSimulator │
       │  (analógico)     │      │  (digital)       │
       │  ohmpy/engine/  │      │  ohmpy/engine/  │
       │    mna.py        │      │  digital_engine  │
       │    components.py │      │      .py         │
       └────────┬─────────┘      └──────────┬───────┘
                │                           │
                └────────┐         ┌────────┘
                         ▼         ▼
                ┌────────────────────────┐
                │ MixedSignalInterface   │
                │ + bridges (ADC/DAC/…)  │
                │ ohmpy/engine/         │
                │   mixed_signal.py      │
                │   bridges.py           │
                └────────────────────────┘
```

- **El motor analógico (MNA)** resuelve ecuaciones algebraicas en
  variables continuas (voltajes nodales y corrientes de rama).
- **El motor digital** es a eventos discretos sobre niveles `0/1/X/Z`.
- **El coordinador mixto** avanza ambos dominios por ventanas de tiempo y
  traduce voltajes ↔ niveles lógicos en los puntos de cruce.

---

## 2. Motor MNA (`ohmpy/engine/mna.py`)

### Idea

Modified Nodal Analysis arma un sistema lineal

```
G · x = I
```

donde `x = [V_nodos | I_ramas]`. Las incógnitas son los voltajes en cada
nodo (excepto GND, que es referencia) y la corriente en cada componente
que requiere variable de rama (fuentes de voltaje, inductores en DC,
op-amps, transformadores).

Cada componente sabe estampar (`stamp`) su contribución en `G` e `I`.
El solver no conoce la física — solo orquesta el ensamblaje y la
resolución del sistema.

### Modos de análisis

| Modo | Método | Qué resuelve |
|---|---|---|
| DC lineal | `solve_dc` | Punto de operación con sólo R, V, I, L (corto), C (abierto) |
| DC no-lineal | `solve_dc_nonlinear` | Igual + diodos, BJT, MOSFET (Newton-Raphson con damping y continuación de fuente) |
| AC | `solve_ac`, `solve_ac_single` | Barrido en frecuencia. Matrices complejas. LU cacheada por frecuencia |
| Transitorio | `solve_transient` | Integración temporal con paso adaptativo y control de error LTE |

### Trucos numéricos

- **`gmin`** (1 nS por defecto): conductancia mínima añadida en cada
  nodo a tierra para que la matriz nunca sea singular. Truco clásico
  de SPICE. Eléctricamente invisible (12 V / 1 GΩ = 12 nA), pero
  evita NaN cuando el circuito tiene nodos flotantes momentáneamente
  (LEDs apagados, diodos en corte, etc.).
- **Diode limiting**: dentro de NR, el cambio de `Vd` entre iteraciones
  se acota con la fórmula de Vlach/SPICE para evitar saltos a la zona
  saturada de la exponencial. Ver `Diode._vd_limit`.
- **Fingerprint del circuito**: la caché de LU se indexa por
  `(_circuit_fingerprint, omega)`. Cambiar el wiper de un potenciómetro
  o la relación de un transformador invalida automáticamente el caché.

### Convenciones

Documentadas en el docstring de [ohmpy/engine/components.py](../ohmpy/engine/components.py).
Lo más importante:

- Nodo "0" es siempre GND.
- Unidades SI puras (V, A, Ω, F, H, s, Hz).
- Sign convention de `CurrentSource` es **SPICE** — si tu intuición
  dice "la fuente entrega +I a n_pos", probablemente tengas que invertir
  los nodos. Ver docstring de `CurrentSource` para el ejemplo.

---

## 3. Motor digital (`ohmpy/engine/digital_engine.py`)

### Idea

Simulación a eventos discretos sobre una priority-queue. Cada compuerta
o flip-flop tiene un retardo de propagación `t_pd`; cuando una entrada
cambia, se programa una evaluación a `t + t_pd`. Cero matrices.

### Modelo de señal

```
LogicLevel = { Z=-2, X=0, L=0, H=1 }
```

`X` y `L` son ambos 0 internamente — se distinguen sólo por contexto
(propagación vs. valor estable). `Z` es tristate.

### Componentes

Combinacionales (AND, OR, NOT, NAND, NOR, XOR, XNOR, BUF, TristateBuffer),
flip-flops (DFF, SRFF, JKFF, TFF), registros de desplazamiento, contadores
binarios, MUX/DEMUX, ROM, RAM. Ver módulo para la lista completa.

### Interfaz mínima

```python
sim = DigitalSimulator()
sim.add(AND("U1", inputs=["A", "B"], output="Y"))
sim.set_input("A", 1, at=0)
sim.set_input("B", 0, at=5e-9)
sim.run(until=50e-9)
sim.final_value("Y")     # estado final
sim.waveform("Y")        # [(t, valor), ...]
```

---

## 4. Coordinación mixta (`ohmpy/engine/mixed_signal.py` + `bridges.py`)

### Algoritmo

Co-simulación por ventanas de tiempo. En cada ventana `[t, t + dt_analog]`:

1. MNA avanza el dominio analógico con `solve_transient`.
2. ADCs / comparadores leen los voltajes finales y publican señales
   digitales (eventos).
3. DigitalSimulator avanza hasta `t + dt_analog`.
4. Los drivers lógicos internos y los DAC/PWM registrados actualizan las
   fuentes del MNA para la siguiente ventana.

La UI crea comparadores CMOS y fuentes lógicas internas cuando un nodo
conecta ambos dominios; ADC y DAC no son símbolos del canvas. No hay
iteración entre dominios dentro de una ventana.

### Puentes disponibles

| Puente | Dirección | Para qué |
|---|---|---|
| `ADC` | analógico → digital | Sample-and-hold + cuantización n-bit para uso desde la API |
| `DAC` | digital → analógico | Conversión de código a fuente MNA para uso desde la API |
| `ComparatorBridge` | analógico → digital (1 bit) | Histéresis configurable |
| `PWMBridge` | digital → analógico | Filtra señal PWM a su nivel DC promedio |
| `SampleAndHold` | analógico → analógico congelado | Para cadenas ADC |

---

## 5. UI (`main.py` + `ohmpy/ui/`)

### Capas

| Capa | Archivo(s) | Responsabilidad |
|---|---|---|
| Ítems gráficos | `ohmpy/ui/items/` | `ComponentItem`, `WireItem` — dibujo y picking |
| Escena | `ohmpy/ui/scene.py` | `CircuitScene` — grid, snapping, conexión de pines, ruteo, construcción del netlist (`build_engine_components_for_item`) |
| Diálogos | `ohmpy/ui/dialogs/` | Editor de valores, instrumentos (multímetro, osciloscopio, generador), análisis digital, calculadoras y ajustes |
| Estilo | `ohmpy/ui/style.py` | Colores del tema activo, fuentes, constantes geométricas, parseo SI |
| Metadata | `ohmpy/ui/component_metadata.py` | Etiquetas de pines, prefijos, listas de tipos digitales |
| Ventana | `main.py` | `MainWindow`, toolbar, loop de simulación live, persistencia de circuitos |

### El puente UI → motor

`build_engine_components_for_item` en `ohmpy/ui/scene.py` traduce un
`ComponentItem` de la escena al objeto correspondiente del motor
(`Resistor`, `VoltageSource`, etc.) usando los pines conectados como
nombres de nodo.

### Live simulation

`MainWindow._tick_live_transient` corre cada `_LIVE_TICK_MS` (50 ms = 20 Hz).
Las constantes `_LIVE_*` documentadas en `main.py` controlan el
trade-off CPU/precisión en tiempo real (tolerancias relajadas, muestras
por período, cota de pasos por tick).

---

## 6. Temas (`ohmpy/theme_manager.py` + `themes/`)

`ThemeManager` carga colores desde JSON. Hay dos fuentes:

- Built-ins definidos en el propio módulo.
- Archivos `*.json` en `themes/` o `~/.ohmpy/themes/`.

Cualquier módulo de UI accede a la paleta vía
`from ohmpy.ui.style import COLORS`. Los colores son **propiedades**
del módulo de estilo, no constantes capturadas — al cambiar de tema
se actualizan transparentemente.

---

## 7. Firmware (`firmware/`)

Opcional e independiente del simulador: protocolo binario para alimentar
el osciloscopio con muestras reales desde un microcontrolador por USB-CDC.
El directorio contiene la especificación y ejemplos de referencia;
`firmware/README.md` explica cómo adaptarlos.

El receptor vive en `ohmpy/engine/hw_stream.py` (decoder del frame
`0xAA 0x55 …`) y la integración con el dialogo del osciloscopio en
`ohmpy/ui/dialogs/hardware_source_dialog.py`.

---

## 8. Tests (`tests/`)

Suite pytest. Los archivos principales son:

- `test_engine.py` — divisores, paralelos, mallas, filtro RC en frecuencia
  e impedancia compleja.
- `test_mixed.py` — compuertas, flip-flops, contador con overflow, registro
  de desplazamiento, bus, puentes y drivers internos de señal mixta.
- `test_project_io.py` — guarda y restauración de ajustes de proyectos desde
  la interfaz.

Convención: cada caso compara contra una solución analítica conocida
con tolerancia explícita (`pytest.approx`). Tests que dependen de una
convención del motor (como `test_current_source_parallel_resistors`)
documentan en el docstring de qué convención hablan y por qué el valor
esperado tiene el signo que tiene.

---

## 9. Cómo extender

### Añadir un componente analógico

1. Crear clase en `ohmpy/engine/components.py` heredando de `Component`.
2. Implementar `stamp` (DC) y opcionalmente `stamp_ac`, `stamp_transient`,
   `stamp_linear` según corresponda.
3. Exportar en `ohmpy/engine/__init__.py`.
4. Añadir tests en `tests/test_engine.py` con un caso analítico cerrado.
5. Para que aparezca en la UI: definir su entrada en
   `ohmpy/ui/component_metadata.py` y registrar el dibujo en
   `ComponentItem`.

### Añadir una compuerta digital

1. Subclase de `Gate` en `ohmpy/engine/digital_engine.py`.
2. Implementar `_evaluate(inputs)`.
3. Exportar en `__init__.py` del paquete.
4. Test en `tests/test_mixed.py`.

### Añadir un instrumento

Diálogo nuevo en `ohmpy/ui/dialogs/`. Si lee del circuito (multímetro,
scope) consume el resultado del último `solve_*`. Si inyecta señal
(función generator), registra una `VoltageSourceAC` en la lista activa.
