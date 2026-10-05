# -*- mode: python ; coding: utf-8 -*-
# Сборка: pyinstaller build.spec
#
# Итоговый файл: dist/ZapretTgTray.exe (один exe, без vendor/ внутри,
# т.к. он подкачивается отдельно при первом запуске).
# console=False убирает окно консоли у лаунчера.
# manifest='app.manifest' задаёт requireAdministrator.

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# Собираем файлы тем и шрифтов customtkinter (json)
customtkinter_datas = collect_data_files("customtkinter")

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    # В datas перечисляем ТОЛЬКО то, что должно быть встроено в EXE.
    # Папка vendor исключена — она будет скачана отдельно.
    # Assets и customtkinter-данные остаются.
    datas=[
        ('assets/icon.png', 'assets'),
        ('assets/icon.ico', 'assets'),
        *customtkinter_datas,
    ],
    hiddenimports=[
        'pystray',
        'pystray._win32',
        'PIL._tkinter_finder',
        'customtkinter',
        # Криптография нужна для tg-ws-proxy (используется при импорте
        # из vendor/tgproxy/proxy/_aes.py) — так как vendor не вшит,
        # он будет подгружен с диска, но cryptography всё равно должна
        # присутствовать в сборке, иначе импорт упадёт.
        'cryptography',
        'cryptography.hazmat.primitives.ciphers',
        'cryptography.hazmat.backends.openssl',
        'logging.handlers',
        # tg-ws-proxy подгружается из vendor/ во время выполнения,
        # поэтому PyInstaller не видит его импорты при анализе.
        # Явно включаем httpx и HTTP/2-зависимости, иначе в exe
        # tgproxy упадёт с ModuleNotFoundError.
        'httpx',
        'httpcore',
        'h2',
        'hpack',
        'certifi',
        'anyio',
        'sniffio',
        'idna',
    ],
    hookspath=[],
    runtime_hooks=[],
    # Исключаем явно ненужные модули (опционально)
    excludes=[
        'venv',
        '__pycache__',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Fuck-DPI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,             # без окна консоли у самого лаунчера
    icon='assets/icon.ico',
    manifest='app.manifest',   # requireAdministrator
)
