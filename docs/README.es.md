# OhmPy (legacy Spanish README)

**Simulador de circuitos electrónicos open source — analógico, digital y señal mixta.**

[Read the English documentation in the project README](../README.md).

OhmPy es un entorno de captura de esquemáticos y simulación construido en Python + PyQt6, con un motor MNA (Modified Nodal Analysis) propio que resuelve DC, AC y transitorios sobre el mismo netlist, e instrumentos virtuales (multímetro, osciloscopio de 2 canales y generador de funciones) integrados en el canvas.

![Captura principal](img/screenshot-main.png)

---

## Características

### Motor de simulación

- **Análisis DC** — lineal y no-lineal (Newton-Raphson) con continuación de fuente para circuitos con diodos, LEDs, BJT, MOSFET y op-amps.
- **Análisis AC** — barrido en frecuencia con factorización LU cacheada por punto de frecuencia.
- **Análisis transitorio** — paso adaptativo con control de error LTE para circuitos analógicos lineales y no lineales.
- **Motor digital** — simulación binaria a eventos con retardos de propagación.
- **Señal mixta** — puentes internos acoplan nodos analógicos y digitales compartidos durante la co-simulación.

### Componentes disponibles desde el editor

| Categoría | Componentes |
|---|---|
| Pasivos | Resistor, Potenciómetro, Capacitor, Inductor, Impedancia genérica |
| Fuentes | Voltaje DC, Voltaje AC, Corriente, Generador de funciones |
| Semiconductores | Diodo, LED (Vf por color), BJT NPN/PNP, MOSFET N/P, Op-Amp ideal, TL082 dual |
| Conversores | Transformador ideal, Puente de diodos rectificador |
| Digital | AND, OR, NOT, NAND, NOR, XOR, DFF, JKFF, TFF, SRFF, contador binario, MUX 2:1, NE555, estado lógico y reloj |
| Señal mixta | Puentes CMOS internos automáticos; ADC/DAC no son componentes del canvas |

La API del motor expone además XNOR, buffers, registros, memorias y clases de
puentes A/D. No todos están conectados al editor de esquemáticos.

### Instrumentos virtuales

- **Multímetro** — lecturas de voltaje DC/AC, corriente y resistencia entre dos pines del esquemático.
- **Osciloscopio** — 2 canales diferenciales, base de tiempo y escala vertical configurables; puede leer el stream serie opcional de hardware.
- **Generador de funciones** — senoidal, cuadrada y triangular con control de amplitud, frecuencia y offset.

### Herramientas auxiliares

- **Analizador de circuitos digitales** — tablas de verdad, minimización SOP/POS y construcción automática de un circuito de compuertas.
- **Calculadora de resistencias** — código de colores ↔ valor, serie E12/E24/E96.
- **Triángulo de potencia** — P, Q, S y factor de potencia para análisis AC.
- **Temas** — soporte para temas JSON personalizables. Ver [`themes/README.md`](../themes/README.md) para crear el tuyo propio.

---

## Instalación

**Requisitos:** Python 3.10 o superior, Windows / Linux / macOS.

```bash
git clone https://github.com/pabdan2003/OhmPy.git OhmPy
cd OhmPy
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

---

## Uso rápido

1. Abre OhmPy con `python main.py`.
2. Arrastra componentes desde el panel lateral al canvas.
3. Conecta pines haciendo clic en un pin y luego en otro.
4. Haz doble clic en un componente para editar su valor.
5. Pulsa **▶ SIMULAR**; OhmPy detecta automáticamente el modo DC, AC, digital o mixto.

### Ejemplo mínimo (motor desde Python)

```python
from ohmpy.engine import Resistor, VoltageSource, MNASolver

solver = MNASolver()
circuit = [
    VoltageSource("V1", "in", "0", 10.0),
    Resistor("R1", "in", "out", 1000.0),
    Resistor("R2", "out", "0", 1000.0),
]
result = solver.solve_dc(circuit)
print(result["voltages"]["out"])  # 5.0 V
```

---

## Estructura del proyecto

```
OhmPy/
├── main.py                  # Entrypoint (lanza la ventana principal)
├── ohmpy/                  # Paquete principal
│   ├── circuit_analyzer.py  # Clasificación del modo y detección de fronteras mixtas
│   ├── theme_manager.py     # Carga y persistencia de temas
│   ├── engine/
│   │   ├── mna.py           # Solver MNA (DC, AC, transitorio)
│   │   ├── components.py    # Modelos de componentes analógicos
│   │   ├── digital_engine.py# Simulador digital a eventos
│   │   ├── bridges.py       # Conversores analógico ↔ digital
│   │   └── mixed_signal.py  # Coordinador de simulación mixta
│   └── ui/
│       ├── scene.py         # Escena QGraphics y construcción del netlist
│       ├── items/           # ComponentItem, WireItem
│       ├── dialogs/         # Instrumentos y diálogos de configuración
│       └── style.py         # Tema, fuentes y constantes visuales
├── themes/                  # Temas JSON (datos)
├── firmware/                # Protocolo y ejemplos de firmware para sonda física
└── tests/                   # Suite pytest (motor, señal mixta y E/S de proyectos)
```

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest -v
```

Los pushes a `main` y los pull requests ejecutan la suite contra Python 3.10, 3.11 y 3.12 en GitHub Actions (ver `.github/workflows/ci.yml`).

---

## Roadmap

- [x] Migración de tests a `pytest` + CI en GitHub Actions.
- [x] Diagramas de Bode (magnitud y fase) sobre el análisis AC existente.
- [ ] FFT en el osciloscopio.
- [x] Exportación limitada tipo SPICE a `.net` para componentes básicos de dos terminales.
- [ ] Importación de netlists SPICE y validación de compatibilidad con simuladores externos.
- [x] Subcircuitos reutilizables (encapsulado de selección).
- [x] Undo de cambios mediante snapshots.
- [ ] Redo y migración opcional a `QUndoStack`.
- [ ] Auto-ruteo ortogonal de cables.
- [ ] Sondas persistentes en el esquemático.

---

## Contribuciones

Las contribuciones son bienvenidas. Antes de abrir un PR:

1. Lee [`docs/architecture.md`](architecture.md) para entender la separación entre motores y las convenciones globales (signos, unidades, nombres de nodos).
2. Ejecuta los tests existentes y añade los que correspondan al cambio.
3. Para cambios en el motor, incluye un caso de validación contra una solución analítica conocida.
4. Para cambios visuales, adjunta una captura antes/después.

Mapas rápidos por paquete:

- [`ohmpy/engine/README.md`](../ohmpy/engine/README.md) — qué hace cada archivo del motor.
- [`ohmpy/ui/README.md`](../ohmpy/ui/README.md) — qué hace cada archivo de la UI.
- [`themes/README.md`](../themes/README.md) — formato JSON de los temas y cómo crear el tuyo.
- [`firmware/README.md`](../firmware/README.md) — protocolo binario para la sonda física del osciloscopio.

---

## Licencia

Distribuido bajo la [licencia MIT](../LICENSE).
