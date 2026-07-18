"""
Setup script for Clew - Native macOS AI IDE
Usage: python3 setup.py py2app
"""

from setuptools import setup

APP = ['clew/__main__.py']
DATA_FILES = [
    ('assets', ['assets/logo.icns', 'assets/logo.png', 'assets/logo_new.png', 'assets/app_icon_1024.png', 'assets/logo.ico']),
    ('clew/web', ['clew/web/index.html', 'clew/web/style.css', 'clew/web/app.js']),
    ('clew/assets', ['clew/assets/clew_character.png', 'clew/assets/logo.icns', 'clew/assets/logo.png', 'clew/assets/logo_new.png', 'clew/assets/app_icon_1024.png', 'clew/assets/logo.ico', 'clew/assets/logo_small.png']),
]

OPTIONS = {
    'iconfile': 'assets/logo.icns',
    'argv_emulation': False,
    'plist': {
        'CFBundleIdentifier': 'io.clew.app',
        'CFBundleName': 'Clew',
        'CFBundleDisplayName': 'Clew',
        'CFBundleVersion': '1.1.0',
        'CFBundleShortVersionString': '1.1.0',
        'CFBundleExecutable': 'Clew',
        'LSMinimumSystemVersion': '13.0',
        'NSHighResolutionCapable': True,
        'LSApplicationCategoryType': 'public.app-category.developer-tools',
        'NSHumanReadableCopyright': 'Copyright 2024 Clew Contributors. Apache License 2.0.',
        'CFBundleDocumentTypes': [
            {
                'CFBundleTypeName': 'Source Code',
                'CFBundleTypeRole': 'Editor',
                'LSItemContentTypes': [
                    'public.python-script',
                    'public.javascript-source',
                    'public.typescript-source',
                    'public.shell-script',
                    'public.source-code'
                ]
            },
            {
                'CFBundleTypeName': 'Markdown',
                'CFBundleTypeRole': 'Editor',
                'LSItemContentTypes': ['net.daringfireball.markdown']
            },
            {
                'CFBundleTypeName': 'Plain Text',
                'CFBundleTypeRole': 'Editor',
                'LSItemContentTypes': ['public.plain-text']
            }
        ],
        'CFBundleURLTypes': [
            {
                'CFBundleURLName': 'io.clew.app.url',
                'CFBundleURLSchemes': ['clew']
            }
        ],
        'NSAppTransportSecurity': {
            'NSAllowsArbitraryLoads': True
        }
    },
    'excludes': [
        'tkinter', 'test', 'tests', 'unittest',
        'PyQt5', 'PyQt6', 'PySide2',
        'matplotlib', 'numpy.testing',
        'IPython', 'notebook', 'jupyter',
    ],
    'includes': [
        'PySide6', 'shiboken6',
    ],
    'packages': ['clew'],
    'resources': ['assets', 'clew/web', 'clew/assets'],
}

setup(
    name='Clew',
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)