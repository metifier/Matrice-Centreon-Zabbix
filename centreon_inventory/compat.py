"""Compatibilité Windows / Linux : sorties console, remplacement atomique de fichiers, certificats."""
from __future__ import annotations

import os
import sys
import time


def setup_console():
    """Évite les plantages d'affichage des accents sur une console Windows (cp850 / cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def use_system_certificates():
    """Fait confiance au magasin de certificats du système (Windows, macOS, Linux).

    Utile en entreprise quand Centreon ou Zabbix utilisent un certificat signé par une autorité
    interne déjà approuvée par Windows. Nécessite le paquet optionnel « truststore » (Python >= 3.10).
    """
    try:
        import truststore
        truststore.inject_into_ssl()
        return True
    except Exception:  # noqa: BLE001 - paquet absent ou version de Python trop ancienne
        return False


def replace_file(src, dst, attempts: int = 20, delay: float = 0.05):
    """os.replace avec nouvelles tentatives : sous Windows, la cible peut être brièvement
    verrouillée par une lecture concurrente (PermissionError)."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay)
