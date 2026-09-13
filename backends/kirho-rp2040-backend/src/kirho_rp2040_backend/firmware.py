"""Carga y validación de firmware RP2040 (.py y .uf2)."""
from __future__ import annotations

import ast
import struct
from dataclasses import dataclass
from pathlib import Path


UF2_BLOCK_SIZE = 512
UF2_MAGIC_START = (0x0A324655, 0x9E5D5157)
UF2_MAGIC_END = 0x0AB16F30
UF2_FLAG_FAMILY_ID_PRESENT = 0x00002000
RP2040_FAMILY_ID = 0xE48BFF56
MAX_PYTHON_BYTES = 2 * 1024 * 1024
MAX_UF2_BYTES = 32 * 1024 * 1024


class FirmwareFormatError(ValueError):
    """El archivo no es un firmware RP2040 válido o compatible."""


@dataclass(frozen=True)
class FirmwareImage:
    """Firmware cargado, listo para que un runner lo ejecute después."""

    format: str
    path: str
    size_bytes: int
    source: str = ""
    binary: bytes = b""
    metadata: dict | None = None


def load_firmware(path: str | Path) -> FirmwareImage:
    """Carga un archivo .py o .uf2 y devuelve una imagen validada."""
    firmware_path = Path(path)
    if not firmware_path.is_file():
        raise FirmwareFormatError(f"firmware file not found: {firmware_path}")
    suffix = firmware_path.suffix.lower()
    if suffix == ".py":
        return _load_python(firmware_path)
    if suffix == ".uf2":
        return _load_uf2(firmware_path)
    raise FirmwareFormatError("supported firmware formats are .py and .uf2")


def _load_python(path: Path) -> FirmwareImage:
    data = path.read_bytes()
    if len(data) > MAX_PYTHON_BYTES:
        raise FirmwareFormatError("Python firmware is too large")
    try:
        source = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FirmwareFormatError("Python firmware must be UTF-8") from exc
    try:
        ast.parse(source, filename=str(path), mode="exec")
    except SyntaxError as exc:
        raise FirmwareFormatError(f"invalid Python firmware: {exc}") from exc
    return FirmwareImage(
        format="py", path=str(path), size_bytes=len(data), source=source,
        metadata={"syntax_valid": True})


def _load_uf2(path: Path) -> FirmwareImage:
    data = path.read_bytes()
    if not data or len(data) % UF2_BLOCK_SIZE:
        raise FirmwareFormatError("UF2 size must be a non-zero multiple of 512 bytes")
    if len(data) > MAX_UF2_BYTES:
        raise FirmwareFormatError("UF2 firmware is too large")

    blocks = []
    block_numbers = set()
    declared_count = None
    family_ids = set()
    addresses = []
    address_ends = []
    for offset in range(0, len(data), UF2_BLOCK_SIZE):
        block = data[offset:offset + UF2_BLOCK_SIZE]
        magic0, magic1, flags, address, payload_size, block_no, num_blocks, family_id = struct.unpack_from(
            "<8I", block, 0)
        if (magic0, magic1) != UF2_MAGIC_START or struct.unpack_from(
                "<I", block, 508)[0] != UF2_MAGIC_END:
            raise FirmwareFormatError(f"invalid UF2 magic in block {offset // UF2_BLOCK_SIZE}")
        if payload_size > 476 or 32 + payload_size > 508:
            raise FirmwareFormatError(f"invalid UF2 payload size in block {block_no}")
        if num_blocks == 0 or block_no >= num_blocks:
            raise FirmwareFormatError(f"invalid UF2 block number {block_no}")
        if block_no in block_numbers:
            raise FirmwareFormatError(f"duplicate UF2 block number {block_no}")
        if declared_count is None:
            declared_count = num_blocks
        elif declared_count != num_blocks:
            raise FirmwareFormatError("UF2 blocks disagree on total block count")
        if flags & UF2_FLAG_FAMILY_ID_PRESENT:
            family_ids.add(family_id)
        block_numbers.add(block_no)
        addresses.append(address)
        address_ends.append(address + payload_size)
        blocks.append({
            "block_no": block_no,
            "address": address,
            "payload": block[32:32 + payload_size],
        })

    if declared_count != len(blocks):
        raise FirmwareFormatError("UF2 block count does not match the file")
    if family_ids and family_ids != {RP2040_FAMILY_ID}:
        raise FirmwareFormatError("UF2 family is not RP2040")
    return FirmwareImage(
        format="uf2", path=str(path), size_bytes=len(data), binary=data,
        metadata={
            "block_count": len(blocks),
            "family_id": RP2040_FAMILY_ID if family_ids else None,
            "address_start": min(addresses),
            "address_end": max(address_ends),
            "blocks": blocks,
        })
