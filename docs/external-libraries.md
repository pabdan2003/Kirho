# Librerías externas

Kirho mantiene los simuladores opcionales fuera de sus dependencias base.
Se instalan en:

```text
~/.kirho/libraries/
```

Desde **Settings → External libraries** se puede abrir la carpeta, instalar
un paquete y recargar la lista. La instalación usa `pip` con `--target`, sin
modificar el entorno global de Python.

## Backends de simulación

Una librería que quiera exponer un simulador a Kirho debe publicar un entry
point en el grupo `kirho.backends`:

```toml
[project.entry-points."kirho.backends"]
pic16f887 = "mi_backend.pic16f887:create_backend"
```

Kirho descubre ese backend y lo carga bajo demanda. El núcleo solo conoce
contratos genéricos: `schematic_components()`, `create_runtime()` y, de forma
opcional, `create_component_controller(component, context)`. El backend puede
publicar la definición visual, propiedades, controles y simulación sin añadir
ramas específicas al programa principal.

El primer backend de desarrollo de Kirho se encuentra en
`backends/kirho-rp2040-backend`. Se puede instalar localmente con:

```bash
./.venv/bin/python -m pip install --target ~/.kirho/libraries \
  ./backends/kirho-rp2040-backend
```

Al recargar Settings debe aparecer como `rp2040`. Al reiniciar Kirho, el
backend publica el símbolo de la placa en una categoría separada del catálogo;
solo entonces se puede colocar en el esquemático. Incluye una definición de 40
pines, su runtime, el controlador de interfaz y la carga de firmware. El núcleo
solo aloja ese controlador y conecta sus eventos con la simulación; no contiene
reglas específicas de esta placa.

Para una instalación reproducible también se puede usar la terminal:

```bash
./.venv/bin/python -m pip install --target ~/.kirho/libraries nombre-del-backend
```

Las librerías externas ejecutan código con los permisos del usuario. Instala
solo paquetes de confianza.
