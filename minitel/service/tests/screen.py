"""Écran Minitel simulé : rejoue les octets du service dans une grille 40×24."""

from __future__ import annotations

from minitel_ha import videotex as vt


class Screen:
    def __init__(self) -> None:
        self.cells = [[" "] * (vt.WIDTH + 1) for _ in range(vt.HEIGHT + 1)]
        self.row = 1
        self.col = 1
        self.cursor_on = False
        self.inverse = False
        self.inverse_cells: set[tuple[int, int]] = set()
        self.mosaic = False
        self.mosaic_cells: set[tuple[int, int]] = set()

    def row_text(self, row: int) -> str:
        return "".join(self.cells[row][1:]).rstrip()

    def is_inverse(self, row: int, col: int) -> bool:
        return (row, col) in self.inverse_cells

    def mosaic_code(self, row: int, col: int) -> int | None:
        """Le code G1 de la case, si c'est une mosaïque."""
        return ord(self.cells[row][col]) if (row, col) in self.mosaic_cells else None

    def _put(self, char: str) -> None:
        self.cells[self.row][self.col] = char
        if self.inverse:
            self.inverse_cells.add((self.row, self.col))
        else:
            self.inverse_cells.discard((self.row, self.col))
        if self.mosaic:
            self.mosaic_cells.add((self.row, self.col))
        else:
            self.mosaic_cells.discard((self.row, self.col))
        self.last = char
        self.col += 1
        if self.col > vt.WIDTH:
            self.col = 1
            self.row = self.row + 1 if self.row < vt.HEIGHT else 1

    def feed(self, data: bytes) -> None:
        stream = iter(data)
        for byte in stream:
            if byte == vt.US:
                self.row = next(stream) - 0x40
                self.col = next(stream) - 0x40
                self.mosaic = False  # le positionnement revient au jeu de caractères normal
            elif byte == vt.SO:
                self.mosaic = True
            elif byte == vt.SI:
                self.mosaic = False
            elif byte == vt.FF:
                self.cells = [[" "] * (vt.WIDTH + 1) for _ in range(vt.HEIGHT + 1)]
                self.inverse_cells.clear()
                self.mosaic_cells.clear()
                self.row = self.col = 1
            elif byte == vt.CAN:
                for col in range(self.col, vt.WIDTH + 1):
                    self.cells[self.row][col] = " "
                    self.mosaic_cells.discard((self.row, col))
            elif byte == vt.BS:
                self.col -= 1
            elif byte == vt.CON:
                self.cursor_on = True
            elif byte == vt.COFF:
                self.cursor_on = False
            elif byte == vt.REP:
                for _ in range(next(stream) - 0x40):
                    self._put(self.last)
            elif byte == vt.ESC:
                code = next(stream)
                if code == 0x5D:
                    self.inverse = True
                elif code == 0x5C:
                    self.inverse = False
                elif code == 0x39:
                    next(stream)
            elif byte == vt.SS2:
                code = next(stream)
                self._put("?" if code < 0x40 or code >= 0x60 else chr(next(stream)))
            elif byte >= 0x20:
                self._put(chr(byte))
