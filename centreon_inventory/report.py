"""Chaîne complète : périmètre client -> matrice -> décisions -> comparaison Zabbix."""
from __future__ import annotations

from .compare import compare_with_zabbix
from .decisions import apply_decisions, prune_unused
from .filters import ClientFilter
from .mapping import Mapper
from .matrix import Matrix, build_matrix
from .model import Inventory
from .zabbix import ZabbixInventory


def build_report(inv: Inventory, mapper: Mapper | None = None, client: ClientFilter | None = None,
                 decisions: dict | None = None, zabbix: ZabbixInventory | None = None,
                 high: int = 20, medium: int = 5, mask: bool = True) -> Matrix:
    client = client or ClientFilter()
    scoped = client.apply(inv)
    m = build_matrix(scoped, mapper or Mapper(), high=high, medium=medium, mask=mask)
    m.client = client
    if client.active:
        prune_unused(m)
    apply_decisions(m, decisions)
    if zabbix is not None:
        compare_with_zabbix(m, zabbix, client)
    return m
