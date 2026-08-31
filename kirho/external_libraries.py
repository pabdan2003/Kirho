"""Instalación y descubrimiento de librerías opcionales de Kirho.

Las librerías externas se instalan en ``~/.kirho/libraries`` y no forman
parte de las dependencias obligatorias de la aplicación. Los backends pueden
publicar entry points en el grupo ``kirho.backends`` para que Kirho los
descubra cuando llegue la integración de simuladores de microcontroladores.
"""
from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys
from typing import List


BACKEND_ENTRY_POINT_GROUP = "kirho.backends"


class ExternalLibraryManager:
    """Gestiona paquetes opcionales en un directorio aislado por usuario."""

    def __init__(self, user_dir: str | os.PathLike[str] | None = None,
                 python_executable: str | None = None):
        self.user_dir = Path(user_dir or Path.home() / ".kirho" / "libraries")
        self.python_executable = python_executable or sys.executable

    def ensure_user_dir(self) -> str:
        """Crea y devuelve la carpeta de librerías opcionales."""
        self.user_dir.mkdir(parents=True, exist_ok=True)
        return str(self.user_dir)

    def activate(self) -> str:
        """Hace importables las librerías instaladas, sin importarlas todavía."""
        path = self.ensure_user_dir()
        if path not in sys.path:
            sys.path.insert(0, path)
        return path

    def list_installed(self) -> List[dict]:
        """Lista los paquetes instalados en la carpeta externa del usuario."""
        self.ensure_user_dir()
        packages = []
        for distribution in importlib.metadata.distributions(
                path=[str(self.user_dir)]):
            name = distribution.metadata.get("Name") or distribution.name
            packages.append({"name": name, "version": distribution.version})
        return sorted(packages, key=lambda package: package["name"].lower())

    def list_backends(self) -> List[dict]:
        """Descubre entry points de simuladores externos sin cargarlos."""
        backends = []
        for distribution in importlib.metadata.distributions(
                path=[str(self.user_dir)]):
            package = distribution.metadata.get("Name") or distribution.name
            for entry_point in distribution.entry_points:
                if entry_point.group != BACKEND_ENTRY_POINT_GROUP:
                    continue
                backends.append({
                    "name": entry_point.name,
                    "package": package,
                    "version": distribution.version,
                    "value": entry_point.value,
                })
        return sorted(backends, key=lambda backend: backend["name"].lower())

    def load_backend(self, name: str):
        """Carga un backend descubierto por nombre cuando Kirho lo necesite."""
        self.activate()
        for distribution in importlib.metadata.distributions(
                path=[str(self.user_dir)]):
            for entry_point in distribution.entry_points:
                if (entry_point.group == BACKEND_ENTRY_POINT_GROUP
                        and entry_point.name == name):
                    return entry_point.load()
        raise LookupError(f"External backend not found: {name}")

    def install(self, requirement: str) -> subprocess.CompletedProcess:
        """Instala un paquete, wheel, ruta o URL mediante pip sin usar shell."""
        requirement = requirement.strip()
        if not requirement or requirement.startswith("-") or "\x00" in requirement:
            raise ValueError("Invalid package specification")
        self.ensure_user_dir()
        return subprocess.run(
            [self.python_executable, "-m", "pip", "install", "--upgrade",
             "--target", str(self.user_dir), requirement],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )
