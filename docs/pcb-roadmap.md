# Ruta para un editor PCB utilizable en Kirho

## Cómo usar la referencia

La imagen sólo marca un nivel de acabado visual: colocación ordenada,
footprints reales, pistas en dos capas, vías, contorno y serigrafía legible.
No hay que recrear esa placa ni su circuito. El objetivo es que Kirho pueda
producir cualquier diseño PCB de calidad real y listo para validarse/fabricarse.
No hace falta recrear KiCad completo.

## Punto de partida real

Kirho ya tiene una base útil:

- `kirho/pcb.py`: modelo en milímetros, capas `F.Cu`/`B.Cu`, pads THT/SMD,
  footprints, pistas, vías, reglas básicas y serialización `.kpcb`.
- `kirho/ui/pcb_editor.py`: cuadrícula, zoom/pan, colocación y rotación de
  footprints, contorno rectangular, ratsnest, ruteo manual, cambio de capa,
  vías, undo/redo y guardado visual.
- `main.py` y `kirho/ui/document_controller.py`: pestaña PCB, regeneración
  desde el esquemático y persistencia del `.kpcb` junto al `.csin`.
- `tests/test_pcb.py` y `tests/test_editor_scene.py`: pruebas de transferencia
  desde esquemático, geometría, persistencia y acciones básicas.

Las brechas que impiden entregar una placa fiable son: DRC inexistente,
conectividad de cobre incompleta, edición limitada de pistas/vías, librería
pequeña y ausencia de Gerber/Excellon/BOM.

## Ruta mínima, en orden

### Fase 0 — Fijar criterios de calidad

Crear un proyecto de prueba pequeño pero representativo: placa de 2 capas,
componentes THT y SMD, conectores, serigrafía, varias redes y al menos una vía.
Su topología no tiene que parecerse a la imagen; sólo debe ejercitar las
capacidades que cualquier diseño real necesita.

**Salida:** un `.csin` + `.kpcb` reproducible y una lista de criterios de
calidad eléctrica, geométrica, visual y de fabricación.

**Criterio de salida:** el proyecto abre, se regenera desde el esquemático sin
perder colocaciones manuales y sirve como prueba repetible de cada fase.

### Fase 1 — Editor PCB editable de verdad

Cerrar primero los huecos de interacción sobre el modelo actual:

1. seleccionar, borrar, mover, rotar y editar propiedades de footprints;
2. seleccionar, borrar y editar puntos/ancho/capa de pistas y vías;
3. resaltar una red completa al seleccionar un pad o footprint;
4. controlar visibilidad de capas y mostrar `F.SilkS`, `B.SilkS` y
   `Edge.Cuts` como geometría separada;
5. mantener snap, undo/redo y `board_changed` para cada mutación;
6. conservar el contorno rectangular actual; los contornos arbitrarios pueden
   esperar.

**Archivos principales:** `kirho/pcb.py`, `kirho/ui/pcb_editor.py`,
`main.py` y las dos suites de pruebas existentes.

**Criterio de salida:** se puede construir manualmente un diseño PCB completo
sin tocar JSON a mano.

### Fase 2 — Ruteo y conectividad confiables

Convertir el ruteo actual en una operación eléctricamente verificable:

1. modelar la conectividad por red a partir de pads, segmentos y vías;
2. recalcular el ratsnest con las conexiones ya realizadas, no sólo con líneas
   desde el primer pad;
3. restringir el ruteo a pads de la misma red y a capas compatibles;
4. añadir segmentos ortogonales/45° durante la previsualización;
5. permitir continuar una pista existente y eliminarla sin dejar estado huérfano;
6. contar redes completas, redes abiertas y posibles cortos.

No empezar con un autorouter. Para este objetivo, el ruteo manual asistido es
suficiente y es mucho más pequeño de validar.

**Criterio de salida:** en el tablero de aceptación se pueden rutear todas las
redes en dos capas, usando vías cuando haga falta, y el contador de redes
pendientes llega a cero.

### Fase 3 — DRC mínimo antes de exportar

Hacer que `PcbRule` deje de ser sólo configuración y se convierta en una
comprobación ejecutable:

- pista menor que `min_track_width_mm`;
- taladro menor que `min_drill_mm`;
- pista, pad o vía fuera del contorno o demasiado cerca del borde;
- separaciones menores que `clearance_mm`;
- solapamiento de footprints usando courtyard;
- corto entre redes diferentes;
- pads sin conexión y redes incompletas;
- objetos en capas inexistentes o deshabilitadas.

Añadir una acción **Run DRC**, una lista de errores/advertencias y navegación
al objeto infractor. El núcleo debe devolver datos estructurados; la UI sólo los
presenta.

**Archivos:** mantener la lógica pequeña en `kirho/pcb.py` mientras quepa; crear
un módulo separado sólo si el DRC deja de ser legible allí.

**Criterio de salida:** el tablero de aceptación termina con cero errores DRC,
y cada regla tiene al menos una prueba que falla cuando se viola.

### Fase 4 — Entrega de fabricación y documentación

Con DRC en cero, exportar desde el mismo `PcbBoard`:

- Gerber de `F.Cu`, `B.Cu`, `F.SilkS`, `B.SilkS` y `Edge.Cuts`;
- Excellon de taladros;
- BOM CSV con referencia, valor y footprint;
- vista previa SVG/PNG para inspección humana;
- informe DRC junto al paquete de salida.

Conservar `.kpcb` como formato editable de Kirho. El exportador debe ser
determinista y no modificar la placa. Para el primer release se puede usar la
biblioteca estándar de Python; no añadir una dependencia EDA hasta que un
formato concreto demuestre que hace falta.

**Criterio de salida:** el paquete exportado abre en un visor Gerber externo,
los taladros coinciden con los pads THT, el contorno es válido y la salida no
depende de un diseño concreto.

### Fase 5 — Librería suficiente, no infinita

Ampliar sólo los footprints necesarios para el tablero de aceptación y los
proyectos reales inmediatos:

- DIP-8/14/16/20/24/28;
- headers y borneras de 2–8 pines;
- resistencias, capacitores, diodos y LEDs THT;
- SOIC/TSSOP/QFP sólo cuando exista un proyecto que los necesite;
- conectores y módulos externos con definición explícita de pads.

Cada footprint debe declarar número de pads, pitch, taladro, capas y geometría
de serigrafía. Un componente sin footprint no debe desaparecer en silencio:
debe quedar como advertencia visible antes de exportar.

## Orden de implementación recomendado

```text
tablero de aceptación
        ↓
edición de objetos y capas
        ↓
conectividad/ruteo asistido
        ↓
DRC
        ↓
Gerber + Excellon + BOM + preview
        ↓
librería adicional y mejoras opcionales
```

Cada fase debe cerrar con una prueba pequeña en las suites existentes. No
conviene separar el paquete en muchos módulos ni introducir un motor geométrico
antes de que los casos reales de Kirho lo pidan.

## Fuera del primer objetivo

Dejar para después de una primera placa fabricable: autorouter, zonas de cobre
con relleno, importación/exportación KiCad, visor 3D, reglas de impedancia,
paneles avanzados de fabricación y soporte exhaustivo de footprints.

## Definición de terminado

Kirho estará en el objetivo cuando, partiendo de un esquemático, pueda:

1. asignar footprints;
2. generar una placa de dos capas editable;
3. colocar y rotar los componentes;
4. rutear manualmente todas las redes con vías;
5. ejecutar DRC y obtener cero errores;
6. mostrar referencias, valores y texto de serigrafía;
7. exportar Gerber, taladros, BOM y una vista previa revisable.

Eso entrega una herramienta PCB pequeña pero real. Las funciones excluidas se
añaden sólo cuando un proyecto concreto las justifique.
