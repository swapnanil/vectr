"""
Data classes for the SymbolGraph public API.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Symbol:
    symbol_id: int
    workspace: str
    name: str
    kind: str           # function | method | class | interface | struct | enum
    file_path: str
    start_line: int
    end_line: int
    snippet: str = field(default="")     # first SNIPPET_LINES of the symbol body


@dataclass
class LocateResult:
    symbols: list[Symbol]
    resolution_strategy: str  # exact|suffix|same_module|import_chain|substring|fuzzy|none
    query: str


@dataclass
class CallEdge:
    from_file: str
    from_symbol: str
    from_line: int
    to_symbol: str
    edge_type: str      # calls | imports | inherits | implements
    call_count: int = 1  # UPG-4.2: distinct call sites this aggregated edge stands for
