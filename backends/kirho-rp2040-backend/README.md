# Kirho RP2040 backend

Backend instalable para registrar una placa de desarrollo RP2040 en Kirho.

El paquete publica la definición verificable del pinout de 40 pines y el
símbolo del componente para el esquemático. El símbolo solo aparece después
de instalar el backend; no forma parte del catálogo base y todavía no tiene
footprint.

Instalación local durante el desarrollo:

```bash
./.venv/bin/python -m pip install --target ~/.kirho/libraries ./backends/kirho-rp2040-backend
```

El backend no es un producto oficial del fabricante de la placa.

## Runtime por eventos

El paquete también publica `create_runtime()` y el comando
`kirho-rp2040-runtime`. Esta primera versión mantiene GPIO virtuales y usa
JSON Lines por stdin/stdout; no ejecuta firmware, no accede a hardware real y
no implementa hardware-in-the-loop.

Mensajes básicos:

```json
{"type":"ping"}
{"type":"configure","gpio_nets":{"25":"LED_NET"}}
{"type":"set_gpio","gpio":25,"value":1,"time_us":1000}
{"type":"snapshot"}
```

## Archivos de firmware

El backend acepta archivos `.py` y `.uf2`. Los `.py` se validan como UTF-8 y
se comprueba su sintaxis; los `.uf2` se validan como imágenes de la familia
RP2040 y se conservan sus bloques. Desde el panel del componente, los `.py`
quedan asociados a la placa y se ejecutan o detienen con el botón global
`SIMULATE`. El runner cooperativo de MicroPython expone sobre el runtime
virtual `machine.Pin`, `PWM`, `ADC` y `time.sleep_ms`. No ejecuta UF2 ni
emula todavía el CPU ARM.
