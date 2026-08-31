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

Kirho puede descubrir ese backend y cargarlo bajo demanda. Esta primera capa
solo prepara instalación y descubrimiento; todavía no define la API de
ejecución del PIC ni implementa su emulación.

Para una instalación reproducible también se puede usar la terminal:

```bash
python -m pip install --target ~/.kirho/libraries nombre-del-backend
```

Las librerías externas ejecutan código con los permisos del usuario. Instala
solo paquetes de confianza.
