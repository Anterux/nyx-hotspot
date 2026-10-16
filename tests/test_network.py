"""Pruebas de la Fase 1A con salidas simuladas (datos ficticios)."""

from __future__ import annotations

import subprocess

import pytest

from nyx_hotspot import network
from nyx_hotspot.network import (
    Cliente,
    ErrorRed,
    _dividir_linea_nmcli,
    _parsear_leases,
    _parsear_station_dump,
    _parsear_vecinos,
    create_or_update_profile,
    detectar_interfaz_wifi,
    ethernet_connected,
    generar_clave,
    hotspot_status,
    hotspot_up,
    list_clients,
    validar_clave,
    validar_ssid,
)

MAC_1 = "02:00:00:00:00:01"
MAC_2 = "02:00:00:00:00:02"
SSID_PRUEBA = "NyxPrueba"
CLAVE_PRUEBA = "ClaveFicticia123"


def _simular(respuestas: dict[tuple[str, ...], str], capturados: list):
    """Devuelve un ejecutar falso que responde con salidas simuladas."""

    def _falso(cmd: list[str], tiempo_limite: float = 10, secretos=None) -> str:
        capturados.append(cmd)
        for prefijo, salida in respuestas.items():
            if tuple(cmd[: len(prefijo)]) == prefijo:
                return salida
        raise AssertionError(f"comando no simulado: {cmd}")

    return _falso


def test_ethernet_conectado(monkeypatch):
    respuestas = {
        ("nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"): (
            "wlan0:wifi:connected\n"
            "enp8s0:ethernet:connected\n"
            "lo:loopback:connected (externally)\n"
        )
    }
    monkeypatch.setattr(network, "ejecutar", _simular(respuestas, []))
    assert ethernet_connected() is True


def test_ethernet_no_conectado(monkeypatch):
    respuestas = {
        ("nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"): (
            "wlan0:wifi:connected\nenp8s0:ethernet:unavailable\n"
        )
    }
    monkeypatch.setattr(network, "ejecutar", _simular(respuestas, []))
    assert ethernet_connected() is False


def test_ethernet_acepta_connected_externally_y_rechaza_connecting(monkeypatch):
    respuestas = {
        ("nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"): (
            "enp8s0:ethernet:connected (externally)\n"
        )
    }
    monkeypatch.setattr(network, "ejecutar", _simular(respuestas, []))
    assert ethernet_connected() is True

    respuestas = {
        ("nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"): (
            "enp8s0:ethernet:connecting\n"
        )
    }
    monkeypatch.setattr(network, "ejecutar", _simular(respuestas, []))
    assert ethernet_connected() is False


def test_dos_puntos_escapados_en_nmcli():
    assert _dividir_linea_nmcli("Mi\\:Red:wifi:connected") == [
        "Mi:Red",
        "wifi",
        "connected",
    ]


def test_detectar_wifi_usa_respaldo(monkeypatch):
    respuestas = {
        ("nmcli", "-t", "-f", "DEVICE,TYPE", "device"): (
            "enp8s0:ethernet:connected\nlo:loopback:connected\n"
        )
    }
    monkeypatch.setattr(network, "ejecutar", _simular(respuestas, []))
    assert detectar_interfaz_wifi() == "wlan0"


def test_detectar_wifi_primera(monkeypatch):
    respuestas = {
        ("nmcli", "-t", "-f", "DEVICE,TYPE", "device"): (
            "wlan1:wifi:disconnected\nwlan0:wifi:disconnected\n"
        )
    }
    monkeypatch.setattr(network, "ejecutar", _simular(respuestas, []))
    assert detectar_interfaz_wifi() == "wlan1"


def test_hotspot_activo_e_inactivo(monkeypatch):
    respuestas = {("nmcli", "-t"): "NyxHotspot:wlan0\n"}
    monkeypatch.setattr(network, "ejecutar", _simular(respuestas, []))
    assert hotspot_status() == "activo"

    respuestas = {("nmcli", "-t"): "OtraRed:wlan0\n"}
    monkeypatch.setattr(network, "ejecutar", _simular(respuestas, []))
    assert hotspot_status() == "inactivo"


def test_validar_ssid_y_clave():
    validar_ssid(SSID_PRUEBA)
    validar_clave(CLAVE_PRUEBA)
    with pytest.raises(ValueError):
        validar_ssid("")
    with pytest.raises(ValueError):
        validar_ssid("x" * 33)
    with pytest.raises(ValueError):
        validar_clave("corta")
    with pytest.raises(ValueError):
        validar_clave("x" * 64)


def test_generar_clave_larga_y_aleatoria():
    clave = generar_clave()
    assert len(clave) >= 12
    assert generar_clave() != generar_clave(20)
    with pytest.raises(ValueError):
        generar_clave(11)


def test_crear_perfil_nuevo_con_banda_defecto(monkeypatch):
    capturados: list = []
    respuestas = {
        ("nmcli", "-t", "-f", "DEVICE,TYPE", "device"): "wlan0:wifi:x\n",
        ("nmcli", "-t", "-f", "NAME", "connection", "show"): "OtraRed\n",
        ("nmcli", "connection", "add"): "",
    }
    monkeypatch.setattr(network, "ejecutar", _simular(respuestas, capturados))
    assert create_or_update_profile(SSID_PRUEBA, CLAVE_PRUEBA) == "creado"
    cmd = capturados[-1]
    assert "add" in cmd
    assert cmd[cmd.index("802-11-wireless.band") + 1] == "bg"
    assert cmd[cmd.index("wifi-sec.proto") + 1] == "rsn"
    assert cmd[cmd.index("wifi-sec.pairwise") + 1] == "ccmp"
    assert cmd[cmd.index("wifi-sec.group") + 1] == "ccmp"


def test_modificar_perfil_reimpone_modo_e_ip(monkeypatch):
    capturados: list = []
    respuestas = {
        ("nmcli", "-t", "-f", "DEVICE,TYPE", "device"): "wlan0:wifi:x\n",
        ("nmcli", "-t", "-f", "NAME", "connection", "show"): "NyxHotspot\n",
        ("nmcli", "connection", "modify"): "",
    }
    monkeypatch.setattr(network, "ejecutar", _simular(respuestas, capturados))
    assert (
        create_or_update_profile(SSID_PRUEBA, CLAVE_PRUEBA, band="a") == "actualizado"
    )
    cmd = capturados[-1]
    assert cmd[cmd.index("802-11-wireless.mode") + 1] == "ap"
    assert cmd[cmd.index("ipv4.method") + 1] == "shared"
    assert cmd[cmd.index("802-11-wireless.band") + 1] == "a"


def test_error_no_filtra_clave(monkeypatch):
    def _falla(*args, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=10,
            cmd=["nmcli", "connection", "modify", "wifi-sec.psk", CLAVE_PRUEBA],
            stderr=f"fallo con {CLAVE_PRUEBA}",
        )

    monkeypatch.setattr(subprocess, "run", _falla)
    with pytest.raises(ErrorRed) as info:
        network.ejecutar(["nmcli", "x"], secretos=CLAVE_PRUEBA)
    assert CLAVE_PRUEBA not in str(info.value)
    assert "nmcli" in str(info.value)

    with pytest.raises(ErrorRed) as info:
        network.ejecutar(["nmcli", "x"], secretos=CLAVE_PRUEBA)
    # El argv completo nunca aparece en el mensaje.
    assert "wifi-sec.psk" not in str(info.value)


def test_hotspot_up_bloqueado_sin_ethernet(monkeypatch):
    llamadas: list = []
    monkeypatch.setattr(network, "ethernet_connected", lambda: False)
    monkeypatch.setattr(network, "ejecutar", _simular({}, llamadas))
    with pytest.raises(ErrorRed, match="ethernet"):
        hotspot_up()
    assert llamadas == []


def test_parseo_station_dump_formato_real():
    texto = (
        f"Station {MAC_1} (on wlan0)\n"
        "\tsignal:\t-50 dBm\n"
        "\tconnected time:\t12234 seconds\n"
        f"Station {MAC_2.upper()} (on wlan0)\n"
        "\tsignal:\t-60 dBm\n"
    )
    parsed = _parsear_station_dump(texto)
    assert parsed == {MAC_1: 12234, MAC_2: None}


def test_parseo_leases_ignora_asterisco():
    texto = f"0 {MAC_1} 10.42.0.10 EquipoFicticio *\n0 {MAC_2} 10.42.0.11 * *\n"
    assert _parsear_leases(texto) == {
        MAC_1: ("10.42.0.10", "EquipoFicticio"),
        MAC_2: ("10.42.0.11", "desconocido"),
    }


def test_parseo_vecinos_ignora_fallos_e_ipv6():
    texto = (
        f"10.42.0.10 dev wlan0 lladdr {MAC_1} REACHABLE\n"
        f"10.42.0.12 dev wlan0 lladdr {MAC_2} FAILED\n"
        f"fe80::1 dev wlan0 lladdr {MAC_1} STALE\n"
        "10.42.0.13 dev wlan0 lladdr 02:00:00:00:00:03 INCOMPLETE\n"
    )
    assert _parsear_vecinos(texto) == {MAC_1: "10.42.0.10"}


def test_list_clients_combina_fuentes(monkeypatch):
    capturados: list = []
    respuestas = {
        ("nmcli", "-t", "-f", "NAME,DEVICE"): "NyxHotspot:wlan0\n",
        ("nmcli", "-t", "-f", "DEVICE,TYPE"): "wlan0:wifi:connected\n",
        ("iw", "dev"): (
            f"Station {MAC_1} (on wlan0)\n\tconnected time:\t60 seconds\n"
            f"Station {MAC_2} (on wlan0)\n\tconnected time:\t5 seconds\n"
        ),
        ("ip", "-o", "neigh", "show", "dev", "wlan0"): (
            f"10.42.0.10 dev wlan0 lladdr {MAC_1} STALE\n"
            f"10.42.0.11 dev wlan0 lladdr {MAC_2} FAILED\n"
        ),
    }
    monkeypatch.setattr(network, "ejecutar", _simular(respuestas, capturados))
    monkeypatch.setattr(
        network,
        "_leer_leases",
        lambda: {MAC_1: ("10.42.0.10", "EquipoFicticio")},
    )
    clientes = list_clients()
    assert clientes == [
        Cliente(
            mac=MAC_1,
            ip="10.42.0.10",
            hostname="EquipoFicticio",
            segundos_conectado=60,
        ),
        Cliente(mac=MAC_2, ip=None, hostname="desconocido", segundos_conectado=5),
    ]
    comandos_ip = [cmd for cmd in capturados if cmd[:2] == ["ip", "-o"]]
    assert comandos_ip == [["ip", "-o", "neigh", "show", "dev", "wlan0"]]


def test_list_clients_hotspot_inactivo_devuelve_vacio_sin_iw(monkeypatch):
    capturados: list = []
    respuestas = {
        ("nmcli", "-t", "-f", "NAME,DEVICE"): "OtraRed:wlan0\n",
        ("nmcli", "-t", "-f", "DEVICE,TYPE"): "wlan0:wifi:connected\n",
    }
    monkeypatch.setattr(network, "ejecutar", _simular(respuestas, capturados))
    assert list_clients() == []
    assert not [cmd for cmd in capturados if cmd[:1] == ["iw"]]


def test_list_clients_sin_permiso_en_leases(monkeypatch):
    respuestas = {
        ("nmcli", "-t", "-f", "NAME,DEVICE"): "NyxHotspot:wlan0\n",
        ("nmcli", "-t", "-f", "DEVICE,TYPE"): "wlan0:wifi:connected\n",
        ("iw", "dev"): f"Station {MAC_1} (on wlan0)\n\tconnected time:\t9 seconds\n",
        ("ip", "-o", "neigh", "show", "dev", "wlan0"): (
            f"10.42.0.10 dev wlan0 lladdr {MAC_1} REACHABLE\n"
        ),
    }
    monkeypatch.setattr(network, "ejecutar", _simular(respuestas, []))

    def _sin_permiso():
        raise PermissionError("denegado")

    monkeypatch.setattr(network.glob, "glob", lambda _: _sin_permiso())
    clientes = list_clients()
    assert clientes[0].ip == "10.42.0.10"
    assert clientes[0].hostname == "desconocido"
