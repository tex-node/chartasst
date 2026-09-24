"""Operational scripts for the Trading Assistant.

Included modules:

- :mod:`scripts.watchdog` - polls the server's ``/health`` endpoint and raises
  a Telegram alert when it fails repeatedly.

The Windows Service installer lives alongside as ``install_service.bat``.
"""
