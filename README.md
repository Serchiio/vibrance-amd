# Vibrance AMD

> **ES** · Color por juego para tarjetas **AMD Radeon** en Windows: la alternativa a *Vibrance GUI* (solo NVIDIA).
> **EN** · Per-game color (saturation, contrast…) for **AMD Radeon** GPUs on Windows — an alternative to NVIDIA-only *Vibrance GUI*. (English summary at the bottom.)

![Ventana de configuración](docs/captura-configuracion.png)

Sube la saturación (o brillo, contraste, tono, temperatura) **solo mientras un juego concreto está en primer plano**
—Valorant, CS2, Apex Legends, lo que elijas— y al salir del juego vuelve a tus valores de antes. Cada juego tiene su
**propio perfil**, y el color del **escritorio** es un perfil aparte y opcional.

- Habla únicamente con el **driver de AMD** (librería ADL, `atiadlxx.dll`). **No inyecta, no lee memoria ni toca el juego.**
- Icono en la bandeja, ventana de configuración con el icono de cada programa y arranque opcional con Windows.
- **Detección automática**: elige el juego de la lista de programas abiertos (con su icono y su nombre) y un **nivel
  recomendado** (Suave, Equilibrado, Competitivo, Intenso, Máximo).
- Una sola ventana de configuración aunque la abras varias veces; un segundo lanzamiento trae la existente al frente.
- Si el driver resetea el valor (cambio de modo de pantalla), lo vuelve a aplicar.

## Uso
1. Descarga/compila `VibranceAMD.exe` (ver *Compilar*) y ábrelo junto a su `config.json` (es portable).
2. Doble clic en el icono de la bandeja → **Configuración** → **＋ Agregar programa**.
3. Ajusta lo que quieras y pulsa **Guardar y aplicar** (se aplica solo, sin reiniciar).

`config.json` también se puede editar a mano (se recarga solo):

```json
{
  "intervalo_segundos": 1.0,
  "escritorio": { "activo": false, "saturacion": 100 },
  "perfiles": {
    "VALORANT-Win64-Shipping.exe": { "nombre": "Valorant", "saturacion": 190 },
    "cs2.exe": { "nombre": "Counter-Strike 2", "saturacion": 170, "contraste": 110 }
  }
}
```
Rangos: saturación 0–200 (100 = normal), brillo −100–100, contraste 0–200, tono −30–30, temperatura 4000–10000.

## Compatibilidad (léelo antes de abrir un Issue)
Usa **ADL**, la librería de AMD que instala el driver Adrenalin. **Probado únicamente** en una **Radeon RX 6600**
(RDNA 2), driver 32.0.21045.x, Windows 11, un monitor 1080p a 120 Hz.

| | |
|---|---|
| Otras Radeon con Adrenalin (RX 400/500, Vega, RX 5000/6000/7000…) | **Probablemente funciona, sin confirmar.** ADL es la API clásica de AMD; AMD recomienda ahora ADLX y podría retirar ADL algún día. |
| Portátiles con gráfica integrada + dedicada, varios monitores, HDR, *Night light*/perfiles ICC | **Sin probar.** El perfil se aplica a todas las pantallas AMD conectadas con control de color. |
| NVIDIA / Intel | **No soportado** (para NVIDIA existe Vibrance GUI). |
| Pantalla completa exclusiva | Algunos juegos ignoran el cambio; usa «pantalla completa con ventana» si no lo notas. |

**¿Algo falla o quieres confirmar tu tarjeta?** Ejecuta `VibranceAMD.exe --diagnostico` (o menú de la bandeja →
*Diagnóstico*): crea `diagnostico.txt` con tu GPU, driver y qué ajustes admite cada pantalla (**sin datos personales**), y
pégalo en un [Issue](../../issues). Con eso se amplía esta tabla. Desde el código fuente, `python vibrance_amd.py --test` comprueba que ADL puede
leer, cambiar y restaurar cada ajuste.

## Compilar
Requiere Python 3.9+ de 64 bits: ejecuta `build.bat` (genera `portable\VibranceAMD.exe`). Desde código: `pip install pystray pillow` y `python vibrance_amd.py`.

## Aviso importante
Este programa **solo cambia el color a través del driver de AMD**; no modifica ni lee ningún juego. Aun así, **no hay garantía**
de cómo trate un anticheat (Vanguard, VAC, etc.) cualquier programa externo: **úsalo bajo tu responsabilidad**. Proyecto
independiente: *AMD* y *Radeon* son marcas de Advanced Micro Devices, Inc.; no está afiliado ni respaldado por AMD, Riot Games ni Valve.

## Licencia y versiones modificadas
**Apache-2.0** (ver `LICENSE`) + archivo **`NOTICE`**. Eres libre de usarlo y modificarlo. Si publicas una versión modificada
debes **conservar el `NOTICE`** (incluye el **enlace a este proyecto original**), **indicar que es una versión modificada** y qué
cambiaste. Así cualquiera puede comparar tu versión con la original y elegir. Sugerencia de texto para tu README:

> *Basado en [Vibrance AMD](https://github.com/Serchiio/vibrance-amd) (versión original). Esta versión está modificada: …*

---

## English summary
**Vibrance AMD** applies saturation/brightness/contrast/hue/temperature **only while a chosen game is in the foreground**
(Valorant, CS2, Apex…), restoring your previous values afterwards, with one independent profile per game and an optional,
separate desktop profile. It talks only to the AMD driver (ADL) — **it never touches the game**.
**Tested only on a Radeon RX 6600** (Windows 11); other Radeon cards are *likely* to work but unconfirmed. Run
`VibranceAMD.exe --diagnostico` and paste `diagnostico.txt` in an Issue to report compatibility. Not affiliated with AMD, Riot or Valve;
no guarantee regarding anti-cheat software — use at your own risk. Licensed **Apache-2.0**; if you distribute a modified
version you must keep the `NOTICE` (link to this original project) and state that it is modified.
