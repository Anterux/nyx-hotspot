# Nyx Hotspot
App GTK4/libadwaita en Python para CachyOS (Arch), compositor niri (Wayland).
Comparte internet de ethernet por Wi-Fi usando NetworkManager.

## Reglas
- Backend con nmcli, iw e ip. Sin root; nada de sudo en el código.
- La lógica va en src/nyx_hotspot/, separada de la GUI, con pruebas en tests/.
- El perfil de NetworkManager se llama NyxHotspot (no tocar otras conexiones).
- Nunca imprimir ni guardar contraseñas en logs o archivos del repo.
- Nunca subir SSID, claves ni MACs reales; usar datos ficticios en pruebas.
- No ejecutar comandos de red destructivos sin avisar.
- Código y comentarios en español. Formato con ruff.
- No instalar hostapd: NetworkManager shared ya usa dnsmasq.

## Fase 1
1. Detectar ethernet  2. Hotspot up/down  3. Cambiar SSID/clave
4. Listar clientes (iw station dump + leases + ip neigh)  5. QR del hotspot  6. GUI
