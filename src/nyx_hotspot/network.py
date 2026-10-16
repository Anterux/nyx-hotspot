"""Lógica de red de Nyx Hotspot (sin GUI y sin sudo).

Usa ``nmcli``, ``iw`` e ``ip`` como subprocesos. Nunca se registra
ni se propaga la contraseña en mensajes de error o registros.
"""

from __future__ import annotations

import glob
import os
import re
import secrets
import string
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Nombre del perfil propio de NetworkManager. No tocar otras conexiones.
PERFIL = "NyxHotspot"
# Interfaz de respaldo si no se detecta ninguna wifi.
INTERFAZ_POR_DEFECTO = "wlan0"
# Banda por defecto: 2,4 GHz para máxima compatibilidad.
BANDA_POR_DEFECTO = "bg"
BANDAS_VALIDAS = ("bg", "a")
# Patrón de leases de dnsmasq gestionados por NetworkManager.
PATRON_LEASES = "/var/lib/NetworkManager/dnsmasq-*.leases"


class ErrorRed(Exception):
    """Error genérico de red (nunca incluye contraseñas)."""


@dataclass(frozen=True)
class Cliente:
    """Cliente conectado al hotspot."""

    mac: str
    ip: str | None
    hostname: str
    segundos_conectado: int | None


def _ocultar(texto: str, secretos: tuple[str, ...]) -> str:
    """Reemplaza cada secreto por *** para no filtrarlo en errores."""
    resultado = texto
    for secreto in secretos:
        if secreto:
            resultado = resultado.replace(secreto, "***")
    return resultado


def ejecutar(
    cmd: list[str],
    tiempo_limite: float = 10,
    secretos: str | tuple[str, ...] | list[str] | None = None,
) -> str:
    """Ejecuta un comando sin shell y devuelve su salida estándar.

    Usa ``LC_ALL=C`` para que ``nmcli`` responda en inglés aunque el
    sistema esté en español. Nunca incluye el ``argv`` completo en los
    errores para no filtrar la clave.
    """
    if isinstance(secretos, str):
        tupla_secretos = (secretos,)
    elif secretos:
        tupla_secretos = tuple(secretos)
    else:
        tupla_secretos = ()
    base = Path(cmd[0]).name if cmd else "comando"
    entorno = {**os.environ, "LC_ALL": "C"}
    try:
        completado = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=tiempo_limite,
            env=entorno,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        detalle = _ocultar((error.stderr or "").strip(), tupla_secretos)
        mensaje = f"{base} falló con código {error.returncode}"
        if detalle:
            mensaje += f": {detalle}"
        raise ErrorRed(mensaje) from None
    except subprocess.TimeoutExpired:
        raise ErrorRed(f"{base} tardó demasiado en responder") from None
    except FileNotFoundError:
        raise ErrorRed(f"no se encontró el programa {base}") from None
    return completado.stdout or ""


def _dividir_linea_nmcli(linea: str) -> list[str]:
    """Divide una línea ``-t`` de nmcli por ':' no escapados.

    nmcli escapa ':' como '\\:' y '\\' como '\\\\'.
    """
    partes: list[str] = []
    actual: list[str] = []
    escapado = False
    for caracter in linea:
        if escapado:
            actual.append(caracter)
            escapado = False
        elif caracter == "\\":
            escapado = True
        elif caracter == ":":
            partes.append("".join(actual))
            actual = []
        else:
            actual.append(caracter)
    if escapado:
        actual.append("\\")
    partes.append("".join(actual))
    return partes


def detectar_interfaz_wifi() -> str:
    """Detecta la interfaz wifi con nmcli; usa wlan0 como respaldo."""
    salida = ejecutar(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"])
    for linea in salida.splitlines():
        if not linea.strip():
            continue
        partes = _dividir_linea_nmcli(linea)
        if len(partes) >= 2 and partes[1] == "wifi" and partes[0]:
            return partes[0]
    return INTERFAZ_POR_DEFECTO


def ethernet_connected() -> bool:
    """Indica si hay alguna interfaz ethernet conectada."""
    salida = ejecutar(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"])
    for linea in salida.splitlines():
        if not linea.strip():
            continue
        partes = _dividir_linea_nmcli(linea)
        if len(partes) < 3 or partes[1] != "ethernet":
            continue
        estado = partes[2]
        # Acepta "connected" y variantes como "connected (externally)",
        # pero no "connecting".
        if estado == "connected" or estado.startswith("connected "):
            return True
    return False


def hotspot_status() -> str:
    """Devuelve 'activo' si el perfil NyxHotspot está activo."""
    salida = ejecutar(
        ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"]
    )
    for linea in salida.splitlines():
        if not linea.strip():
            continue
        partes = _dividir_linea_nmcli(linea)
        if partes and partes[0] == PERFIL:
            return "activo"
    return "inactivo"


def validar_ssid(ssid: str) -> None:
    """Valida que el SSID tenga entre 1 y 32 bytes."""
    if not isinstance(ssid, str) or not ssid:
        raise ValueError("el SSID no puede estar vacío")
    if len(ssid.encode("utf-8")) > 32:
        raise ValueError("el SSID debe tener como máximo 32 bytes")


def validar_clave(clave: str) -> None:
    """Valida que la clave tenga entre 8 y 63 caracteres (sin mostrarla)."""
    if not isinstance(clave, str) or not 8 <= len(clave) <= 63:
        raise ValueError("la clave debe tener entre 8 y 63 caracteres")


def generar_clave(longitud: int = 16) -> str:
    """Genera una contraseña aleatoria de al menos 12 caracteres."""
    if longitud < 12:
        raise ValueError("la longitud mínima de la clave es 12")
    alfabeto = string.ascii_letters + string.digits
    return "".join(secrets.choice(alfabeto) for _ in range(longitud))


def _perfil_existe() -> bool:
    """Indica si el perfil NyxHotspot ya existe."""
    salida = ejecutar(["nmcli", "-t", "-f", "NAME", "connection", "show"])
    for linea in salida.splitlines():
        if not linea.strip():
            continue
        if _dividir_linea_nmcli(linea)[0] == PERFIL:
            return True
    return False


def create_or_update_profile(ssid: str, password: str, band: str | None = None) -> str:
    """Crea o actualiza el perfil NyxHotspot con WPA2 (RSN/CCMP).

    NOTA: pasar la clave por ``argv`` de nmcli es una limitación
    conocida (visible un instante en ``ps``). Se usa solo aquí y nunca
    se registra. Más adelante se puede migrar a libnm (gi.repository.NM).
    """
    validar_ssid(ssid)
    validar_clave(password)
    banda = band or BANDA_POR_DEFECTO
    if banda not in BANDAS_VALIDAS:
        raise ValueError("la banda debe ser 'bg' (2,4 GHz) o 'a' (5 GHz)")
    interfaz = detectar_interfaz_wifi()
    if _perfil_existe():
        cmd = [
            "nmcli",
            "connection",
            "modify",
            PERFIL,
            "802-11-wireless.ssid",
            ssid,
            "802-11-wireless.mode",
            "ap",
            "802-11-wireless.band",
            banda,
            "wifi-sec.key-mgmt",
            "wpa-psk",
            "wifi-sec.proto",
            "rsn",
            "wifi-sec.pairwise",
            "ccmp",
            "wifi-sec.group",
            "ccmp",
            "wifi-sec.psk",
            password,
            "ipv4.method",
            "shared",
            "connection.autoconnect",
            "no",
        ]
        ejecutar(cmd, secretos=password)
        return "actualizado"
    cmd = [
        "nmcli",
        "connection",
        "add",
        "type",
        "wifi",
        "ifname",
        interfaz,
        "con-name",
        PERFIL,
        "autoconnect",
        "no",
        "ssid",
        ssid,
        "mode",
        "ap",
        "802-11-wireless.band",
        banda,
        "wifi-sec.key-mgmt",
        "wpa-psk",
        "wifi-sec.proto",
        "rsn",
        "wifi-sec.pairwise",
        "ccmp",
        "wifi-sec.group",
        "ccmp",
        "wifi-sec.psk",
        password,
        "ipv4.method",
        "shared",
    ]
    ejecutar(cmd, secretos=password)
    return "creado"


def hotspot_up() -> None:
    """Activa el hotspot; exige ethernet conectado antes."""
    if not ethernet_connected():
        raise ErrorRed("no hay ethernet conectado; no se puede activar el hotspot")
    ejecutar(["nmcli", "connection", "up", PERFIL])


def hotspot_down() -> None:
    """Desactiva el hotspot."""
    ejecutar(["nmcli", "connection", "down", PERFIL])


def _parsear_station_dump(texto: str) -> dict[str, int | None]:
    """Extrae MAC y segundos conectados de ``iw station dump``."""
    estaciones: dict[str, int | None] = {}
    mac_actual: str | None = None
    for linea in texto.splitlines():
        coincidencia = re.match(r"^Station\s+([0-9a-fA-F:]{17})\b", linea)
        if coincidencia:
            mac_actual = coincidencia.group(1).lower()
            estaciones[mac_actual] = None
            continue
        if mac_actual is not None:
            tiempo = re.search(r"connected time:\s+(\d+)\s+seconds", linea)
            if tiempo:
                estaciones[mac_actual] = int(tiempo.group(1))
    return estaciones


def _parsear_leases(texto: str) -> dict[str, tuple[str, str]]:
    """Extrae MAC -> (IP, hostname) de leases dnsmasq (ignora '*')."""
    leases: dict[str, tuple[str, str]] = {}
    for linea in texto.splitlines():
        partes = linea.split()
        if len(partes) < 4:
            continue
        mac = partes[1].lower()
        ip = partes[2]
        nombre = partes[3]
        if nombre == "*" or not nombre:
            nombre = "desconocido"
        leases[mac] = (ip, nombre)
    return leases


def _leer_leases() -> dict[str, tuple[str, str]]:
    """Lee los leases de NM; si no hay permiso, devuelve vacío."""
    leases: dict[str, tuple[str, str]] = {}
    try:
        rutas = glob.glob(PATRON_LEASES)
    except OSError:
        return {}
    for ruta in rutas:
        try:
            texto = Path(ruta).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        leases.update(_parsear_leases(texto))
    return leases


def _parsear_vecinos(texto: str) -> dict[str, str]:
    """Extrae MAC -> IP de ``ip neigh``; ignora FAILED/INCOMPLETE e IPv6."""
    vecinos: dict[str, str] = {}
    for linea in texto.splitlines():
        partes = linea.split()
        if len(partes) < 5:
            continue
        ip = partes[0]
        if ":" in ip:
            continue
        try:
            indice = partes.index("lladdr")
        except ValueError:
            continue
        if indice + 2 >= len(partes):
            continue
        mac = partes[indice + 1].lower()
        estado = partes[indice + 2].upper()
        if estado in ("FAILED", "INCOMPLETE"):
            continue
        vecinos.setdefault(mac, ip)
    return vecinos


def list_clients() -> list[Cliente]:
    """Lista clientes combinando iw, leases de NM e ip neigh.

    Si el hotspot no está activo, devuelve [] sin llamar a ``iw``:
    con wlan0 en modo cliente, ``iw station dump`` mostraría el
    router al que estamos conectados, no clientes del hotspot.
    """
    if hotspot_status() != "activo":
        return []
    interfaz = detectar_interfaz_wifi()
    salida_iw = ejecutar(["iw", "dev", interfaz, "station", "dump"])
    estaciones = _parsear_station_dump(salida_iw)
    leases = _leer_leases()
    try:
        vecinos = _parsear_vecinos(
            ejecutar(["ip", "-o", "neigh", "show", "dev", interfaz])
        )
    except ErrorRed:
        vecinos = {}
    clientes: list[Cliente] = []
    for mac, segundos in estaciones.items():
        ip: str | None = None
        nombre = "desconocido"
        if mac in leases:
            ip, nombre = leases[mac]
        if ip is None and mac in vecinos:
            ip = vecinos[mac]
        clientes.append(
            Cliente(mac=mac, ip=ip, hostname=nombre, segundos_conectado=segundos)
        )
    clientes.sort(key=lambda cliente: cliente.mac)
    return clientes
