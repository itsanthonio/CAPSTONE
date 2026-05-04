#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
import platform

# ── PROJ/GDAL path fix ──────────────────────────────────────────────────────
# Must happen before any Django/GDAL import so PROJ finds the right proj.db.
# PostgreSQL's PostGIS installer sets PROJ_LIB as a Windows system env var
# pointing to its own (older) proj.db.  decouple.config() falls through to
# that system var, so we parse .env ourselves and never fall back to the
# system environment for this key.
if platform.system() == 'Windows':
    import pathlib as _pathlib
    _proj = r'C:\Program Files\QGIS 3.44.7\share\proj'   # safe default
    # Search for .env in manage.py's directory and its parent (git root may differ)
    _here = _pathlib.Path(__file__).resolve().parent
    for _candidate in (_here / '.env', _here.parent / '.env'):
        if _candidate.exists():
            for _ln in _candidate.read_text(encoding='utf-8', errors='ignore').splitlines():
                _ln = _ln.strip()
                if _ln.startswith('#') or '=' not in _ln:
                    continue
                _k, _, _v = _ln.partition('=')
                if _k.strip() in ('PROJ_LIB', 'PROJ_DATA'):
                    _proj = _v.strip().strip('"\'')
                    break
            break
    # Override whatever PostgreSQL put in the system environment
    os.environ['PROJ_LIB']  = _proj   # PROJ < 9
    os.environ['PROJ_DATA'] = _proj   # PROJ >= 9
# ────────────────────────────────────────────────────────────────────────────


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
