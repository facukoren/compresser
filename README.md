# Compresser

App de escritorio para Windows que comprime videos a H.264 con NVENC (GPU NVIDIA) o libx264 (CPU como fallback). Pensada para entrevistas, podcasts y charlas — donde la imagen es estática y se gana muchísimo ratio de compresión sin pérdida visible.

UI moderna en CustomTkinter, drag & drop, cola de procesamiento, telemetría en vivo de CPU/GPU/NVENC, logs persistidos, notificaciones de Windows al finalizar, integración con menú contextual del Explorador.

## Features

- **3 presets de calidad**: Alta calidad (CQ 21) · Balanceado (CQ 24) · Máximo ahorro (CQ 27)
- **NVENC con fallback inteligente**: detecta soporte real (no solo si está listado), prueba con frame real, cae a libx264 si la GPU no anda
- **Cola de videos**: arrastrá varios juntos o seleccioná con Ctrl/Shift; procesa secuencialmente
- **Drag & drop multi-archivo**: incluyendo desde el menú contextual del Explorador
- **Telemetría en vivo**: CPU% · RAM · GPU% · NVENC% · VRAM · Temp · Watts
- **Logs en vivo + archivo**: panel coloreado por nivel (INFO/OK/WARN/ERR/FFMPEG/TELEM), persistencia a `logs/` con timestamp
- **Toast nativo de Windows al finalizar** con resumen y botón "Abrir carpeta"
- **Menú contextual**: click derecho en cualquier video → "Comprimir con Compresser"
- **Plug & play**: opción de ejecutar desde Python (`run.bat`) o como `.exe` único con ffmpeg embebido

## Requisitos

- Windows 10/11 64-bit
- (Opcional pero recomendado) GPU NVIDIA con driver ≥ 530 para NVENC. Sin GPU usa libx264.

## Quick start (modo desarrollador)

```bat
git clone https://github.com/facukoren/compresser.git
cd compresser
setup.bat
```

`setup.bat` crea el venv, instala dependencias, descarga ffmpeg 6.1.1 si no lo tenés en PATH, y lanza la app.

Para corridas siguientes: `run.bat`.

## Build como .exe distribuible

```bat
build.bat
```

Genera `dist/Compresser.exe` (~68 MB, ffmpeg embebido, funciona offline en cualquier Windows 10/11 sin instalar Python ni nada).

## Asociación al menú contextual

Después de buildear:

```bat
cd dist
register_context_menu.bat
```

Registra "Comprimir con Compresser" en el menú contextual de Windows para 12 extensiones de video (`.mp4 .mkv .mov .avi .webm .flv .wmv .m4v .ts .mpg .mpeg .m2ts`). Sin admin (usa `HKEY_CURRENT_USER`).

Para sacarlo: `unregister_context_menu.bat`.

## CLI

El .exe acepta videos como argumentos. Útil para automatización:

```bat
Compresser.exe "video1.mp4" "video2.mp4" "video3.mp4"
```

Carga los tres en la cola automáticamente al iniciar.

## Pipeline de encoding

**Con NVENC (GPU)**:
```
ffmpeg -i input.mp4 \
  -c:v h264_nvenc -preset p7 -tune hq -profile:v high \
  -rc vbr -cq <21|24|27> -b:v 0 \
  -multipass fullres -spatial-aq 1 -temporal-aq 1 -bf 3 \
  -c:a aac -b:a 128k -movflags +faststart \
  output.mp4
```

**Fallback CPU (libx264)**:
```
ffmpeg -i input.mp4 \
  -c:v libx264 -preset medium -profile:v high \
  -crf <19|22|25> -bf 3 \
  -c:a aac -b:a 128k -movflags +faststart \
  output.mp4
```

## Estructura

```
compresser/
├── compresser.py                    # Todo el código de la app
├── requirements.txt                 # Deps Python
├── setup.bat                        # Primera ejecución (crea venv + lanza)
├── run.bat                          # Ejecuciones siguientes
├── build.bat                        # Empaqueta el .exe
├── register_context_menu.bat        # Registra menú contextual
└── unregister_context_menu.bat      # Lo saca
```

Artefactos generados en runtime (ignorados en git):
- `ffmpeg/` — ffmpeg descargado o embebido
- `logs/` — un archivo por sesión, timestamped
- `config.json` — última carpeta de salida elegida
- `.venv/`, `build/`, `dist/`, `__pycache__/` — Python/PyInstaller

## Dependencias

- `customtkinter` — UI moderna sobre Tk
- `tkinterdnd2` — drag & drop
- `psutil` — telemetría CPU/RAM
- `nvidia-ml-py` — telemetría GPU NVIDIA + encoder usage
- `winotify` — toasts nativos de Windows

## Licencia

MIT
