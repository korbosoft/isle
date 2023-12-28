import re
from typing import Iterable
from collections import namedtuple

# e.g. `*** PUBLICS`
_section_change_regex = re.compile(r"^\*\*\* (?P<section>[A-Z/ ]+)")

# e.g. `     27 00034EC0     28 00034EE2     29 00034EE7     30 00034EF4`
_line_addr_pairs_findall = re.compile(r"\s+(?P<line_no>\d+) (?P<addr>[A-F0-9]{8})")

# We assume no spaces in the file name
# e.g. `  Z:\lego-island\isle\LEGO1\viewmanager\viewroi.cpp (None), 0001:00034E90-00034E97, line/addr pairs = 2`
_lines_subsection_header = re.compile(
    r"^\s*(?P<filename>\S+).*?, (?P<section>[A-F0-9]{4}):(?P<start>[A-F0-9]{8})-(?P<end>[A-F0-9]{8}), line/addr pairs = (?P<len>\d+)"
)

# e.g. `S_PUB32: [0001:0003FF60], Flags: 00000000, __read`
_publics_line_regex = re.compile(
    r"^(?P<type>\w+): \[(?P<section>\w{4}):(?P<offset>\w{8})], Flags: (?P<flags>\w{8}), (?P<name>\S+)"
)

# e.g. `(00008C) S_GPROC32: [0001:00034E90], Cb: 00000007, Type:             0x1024, ViewROI::IntrinsicImportance`
_symbol_line_regex = re.compile(
    r"\(\w+\) (?P<type>\S+): \[(?P<section>\w{4}):(?P<offset>\w{8})\], Cb: (?P<size>\w+), Type:\s+\S+, (?P<name>.+)"
)

# e.g. `         Debug start: 00000008, Debug end: 0000016E`
_gproc_debug_regex = re.compile(
    r"\s*Debug start: (?P<start>\w{8}), Debug end: (?P<end>\w{8})"
)

# e.g. `  00DA  0001:00000000  00000073  60501020`
_section_contrib_regex = re.compile(
    r"\s*(?P<module>\w{4})  (?P<section>\w{4}):(?P<offset>\w{8})  (?P<size>\w{8})  (?P<flags>\w{8})"
)

# e.g. `S_GDATA32: [0003:000004A4], Type:   T_32PRCHAR(0470), g_set`
_gdata32_regex = re.compile(
    r"S_GDATA32: \[(?P<section>\w{4}):(?P<offset>\w{8})\], Type:\s*(?P<type>\S+), (?P<name>\S+)"
)


LinesEntry = namedtuple("LinesEntry", "filename line_no addr")
PublicsEntry = namedtuple("PublicsEntry", "type section offset flags name")
SymbolsEntry = namedtuple("SymbolsEntry", "type section offset size name")
SizeRefEntry = namedtuple("SizeRefEntry", "section offset size")
GdataEntry = namedtuple("GdataEntry", "section offset type name")


class CvdumpParser:
    def __init__(self) -> None:
        self._section: str = ""
        self._lines_filename: str = ""

        self.lines = []
        self.publics = []
        self.symbols = []
        self.sizerefs = []
        self.globals = []

    def _lines_section(self, line: str):
        """Parsing entries from the LINES section. We only care about the pairs of
        line_number and address and the subsection header to indicate which code file
        we are in."""

        # Subheader indicates a new function and possibly a new code filename.
        if (match := _lines_subsection_header.match(line)) is not None:
            self._lines_filename = match.group(1)
            return

        if (matches := _line_addr_pairs_findall.findall(line)) is not None:
            for line_no, addr in matches:
                self.lines.append(
                    LinesEntry(
                        filename=self._lines_filename,
                        line_no=int(line_no),
                        addr=int(addr, 16),
                    )
                )

    def _publics_section(self, line: str):
        """Match each line from PUBLICS and pull out the symbol information.
        These are MSVC mangled symbol names. String constants and vtable
        addresses can only be found here."""
        if (match := _publics_line_regex.match(line)) is not None:
            self.publics.append(
                PublicsEntry(
                    type=match.group("type"),
                    section=int(match.group("section"), 16),
                    offset=int(match.group("offset"), 16),
                    flags=int(match.group("flags"), 16),
                    name=match.group("name"),
                )
            )

    def _globals_section(self, line: str):
        """S_PROCREF may be useful later.
        Right now we just want S_GDATA32 symbols because it is the simplest
        way to access global variables."""
        if (match := _gdata32_regex.match(line)) is not None:
            self.globals.append(
                GdataEntry(
                    section=int(match.group("section"), 16),
                    offset=int(match.group("offset"), 16),
                    type=match.group("type"),
                    name=match.group("name"),
                )
            )

    def _symbols_section(self, line: str):
        """We are interested in S_GPROC32 symbols only."""
        if (match := _symbol_line_regex.match(line)) is not None:
            if match.group("type") == "S_GPROC32":
                self.symbols.append(
                    SymbolsEntry(
                        type=match.group("type"),
                        section=int(match.group("section"), 16),
                        offset=int(match.group("offset"), 16),
                        size=int(match.group("size"), 16),
                        name=match.group("name"),
                    )
                )

    def _section_contributions(self, line: str):
        """Gives the size of elements across all sections of the binary.
        This is the easiest way to get the data size for .data and .rdata
        members that do not have a primitive data type."""
        if (match := _section_contrib_regex.match(line)) is not None:
            self.sizerefs.append(
                SizeRefEntry(
                    section=int(match.group("section"), 16),
                    offset=int(match.group("offset"), 16),
                    size=int(match.group("size"), 16),
                )
            )

    def read_line(self, line: str):
        # Blank lines are there to help the reader; they have no context significance
        if line.strip() == "":
            return

        if (match := _section_change_regex.match(line)) is not None:
            self._section = match.group(1)
            return

        if self._section == "LINES":
            self._lines_section(line)

        elif self._section == "PUBLICS":
            self._publics_section(line)

        elif self._section == "SYMBOLS":
            self._symbols_section(line)

        elif self._section == "SECTION CONTRIBUTIONS":
            self._section_contributions(line)

        elif self._section == "GLOBALS":
            self._globals_section(line)

    def read_lines(self, lines: Iterable[str]):
        for line in lines:
            self.read_line(line)