"""Pruebas del soporte opcional de librerías externas."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from kirho.external_libraries import ExternalLibraryManager


def _write_distribution(root, name="pic-backend", version="1.2"):
    dist_info = root / f"{name}-{version}.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        encoding="utf-8")
    (dist_info / "entry_points.txt").write_text(
        "[kirho.backends]\npic16f887 = pic_backend:create_backend\n",
        encoding="utf-8")


def test_external_packages_and_backends_are_discovered(tmp_path):
    _write_distribution(tmp_path)
    manager = ExternalLibraryManager(tmp_path)

    assert manager.list_installed() == [{"name": "pic-backend", "version": "1.2"}]
    assert manager.list_backends() == [{
        "name": "pic16f887",
        "package": "pic-backend",
        "version": "1.2",
        "value": "pic_backend:create_backend",
    }]


def test_install_isolated_and_does_not_use_shell(monkeypatch, tmp_path):
    calls = {}

    def fake_run(command, **kwargs):
        calls["command"] = command
        calls["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("kirho.external_libraries.subprocess.run", fake_run)
    manager = ExternalLibraryManager(tmp_path, python_executable="python-test")
    result = manager.install("pic-backend==1.2")

    assert result.returncode == 0
    assert calls["command"] == [
        "python-test", "-m", "pip", "install", "--upgrade", "--target",
        str(tmp_path), "pic-backend==1.2",
    ]
    assert calls["kwargs"] == {
        "capture_output": True,
        "text": True,
        "check": False,
        "shell": False,
    }


def test_install_rejects_empty_or_option_like_specs(tmp_path):
    manager = ExternalLibraryManager(tmp_path)

    with pytest.raises(ValueError):
        manager.install("")
    with pytest.raises(ValueError):
        manager.install("--no-deps")
