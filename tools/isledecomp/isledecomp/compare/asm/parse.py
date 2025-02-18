"""Converts x86 machine code into text (i.e. assembly). The end goal is to
compare the code in the original and recomp binaries, using longest common
subsequence (LCS), i.e. difflib.SequenceMatcher.
The capstone library takes the raw bytes and gives us the mnemonic
and operand(s) for each instruction. We need to "sanitize" the text further
so that virtual addresses are replaced by symbol name or a generic
placeholder string."""

import re
from functools import cache
from typing import Callable, List, Optional, Tuple
from collections import namedtuple
from isledecomp.bin import InvalidVirtualAddressError
from capstone import Cs, CS_ARCH_X86, CS_MODE_32
from .const import JUMP_MNEMONICS, SINGLE_OPERAND_INSTS

disassembler = Cs(CS_ARCH_X86, CS_MODE_32)

ptr_replace_regex = re.compile(r"\[(0x[0-9a-f]+)\]")

# For matching an immediate value on its own.
# Preceded by start-of-string (first operand) or comma-space (second operand)
immediate_replace_regex = re.compile(r"(?:^|, )(0x[0-9a-f]+)")

DisasmLiteInst = namedtuple("DisasmLiteInst", "address, size, mnemonic, op_str")


@cache
def from_hex(string: str) -> Optional[int]:
    try:
        return int(string, 16)
    except ValueError:
        pass

    return None


class ParseAsm:
    def __init__(
        self,
        relocate_lookup: Optional[Callable[[int], bool]] = None,
        name_lookup: Optional[Callable[[int], str]] = None,
        float_lookup: Optional[Callable[[int, int], Optional[str]]] = None,
    ) -> None:
        self.relocate_lookup = relocate_lookup
        self.name_lookup = name_lookup
        self.float_lookup = float_lookup
        self.replacements = {}
        self.number_placeholders = True

    def reset(self):
        self.replacements = {}

    def is_relocated(self, addr: int) -> bool:
        if callable(self.relocate_lookup):
            return self.relocate_lookup(addr)

        return False

    def float_replace(self, addr: int, data_size: int) -> Optional[str]:
        if callable(self.float_lookup):
            try:
                float_str = self.float_lookup(addr, data_size)
            except InvalidVirtualAddressError:
                # probably caused by reading an invalid instruction
                return None
            if float_str is not None:
                return f"{float_str} (FLOAT)"

        return None

    def lookup(self, addr: int) -> Optional[str]:
        """Return a replacement name for this address if we find one."""
        if (cached := self.replacements.get(addr, None)) is not None:
            return cached

        if callable(self.name_lookup):
            if (name := self.name_lookup(addr)) is not None:
                self.replacements[addr] = name
                return name

        return None

    def replace(self, addr: int) -> str:
        """Same function as lookup above, but here we return a placeholder
        if there is no better name to use."""
        if (name := self.lookup(addr)) is not None:
            return name

        # The placeholder number corresponds to the number of addresses we have
        # already replaced. This is so the number will be consistent across the diff
        # if we can replace some symbols with actual names in recomp but not orig.
        idx = len(self.replacements) + 1
        placeholder = f"<OFFSET{idx}>" if self.number_placeholders else "<OFFSET>"
        self.replacements[addr] = placeholder
        return placeholder

    def hex_replace_always(self, match: re.Match) -> str:
        """If a pointer value was matched, always insert a placeholder"""
        value = int(match.group(1), 16)
        return match.group(0).replace(match.group(1), self.replace(value))

    def hex_replace_relocated(self, match: re.Match) -> str:
        """For replacing immediate value operands. We only want to
        use the placeholder if we are certain that this is a valid address.
        We can check the relocation table to find out."""
        value = int(match.group(1), 16)
        if self.is_relocated(value):
            return match.group(0).replace(match.group(1), self.replace(value))

        return match.group(0)

    def hex_replace_float(self, match: re.Match) -> str:
        """Special case for replacements on float instructions.
        If the pointer is a float constant, read it from the binary."""
        value = int(match.group(1), 16)

        # If we can find a variable name for this pointer, use it.
        placeholder = self.lookup(value)

        # Read what's under the pointer and show the decimal value.
        if placeholder is None:
            float_size = 8 if "qword" in match.string else 4
            placeholder = self.float_replace(value, float_size)

        # If we can't read the float, use a regular placeholder.
        if placeholder is None:
            placeholder = self.replace(value)

        return match.group(0).replace(match.group(1), placeholder)

    def sanitize(self, inst: DisasmLiteInst) -> Tuple[str, str]:
        # For jumps or calls, if the entire op_str is a hex number, the value
        # is a relative offset.
        # Otherwise (i.e. it looks like `dword ptr [address]`) it is an
        # absolute indirect that we will handle below.
        # Providing the starting address of the function to capstone.disasm has
        # automatically resolved relative offsets to an absolute address.
        # We will have to undo this for some of the jumps or they will not match.

        if (
            inst.mnemonic in SINGLE_OPERAND_INSTS
            and (op_str_address := from_hex(inst.op_str)) is not None
        ):
            if inst.mnemonic == "call":
                return (inst.mnemonic, self.replace(op_str_address))

            if inst.mnemonic == "push":
                if self.is_relocated(op_str_address):
                    return (inst.mnemonic, self.replace(op_str_address))

                # To avoid falling into jump handling
                return (inst.mnemonic, inst.op_str)

            if inst.mnemonic == "jmp":
                # The unwind section contains JMPs to other functions.
                # If we have a name for this address, use it. If not,
                # do not create a new placeholder. We will instead
                # fall through to generic jump handling below.
                potential_name = self.lookup(op_str_address)
                if potential_name is not None:
                    return (inst.mnemonic, potential_name)

            # Else: this is any jump
            # Show the jump offset rather than the absolute address
            jump_displacement = op_str_address - (inst.address + inst.size)
            return (inst.mnemonic, hex(jump_displacement))

        if inst.mnemonic.startswith("f"):
            # If floating point instruction
            op_str = ptr_replace_regex.sub(self.hex_replace_float, inst.op_str)
        else:
            op_str = ptr_replace_regex.sub(self.hex_replace_always, inst.op_str)

        op_str = immediate_replace_regex.sub(self.hex_replace_relocated, op_str)
        return (inst.mnemonic, op_str)

    def parse_asm(self, data: bytes, start_addr: Optional[int] = 0) -> List[str]:
        asm = []

        for raw_inst in disassembler.disasm_lite(data, start_addr):
            # Use heuristics to disregard some differences that aren't representative
            # of the accuracy of a function (e.g. global offsets)
            inst = DisasmLiteInst(*raw_inst)

            # If there is no pointer or immediate value in the op_str,
            # there is nothing to sanitize.
            # This leaves us with cases where a small immediate value or
            # small displacement (this.member or vtable calls) appears.
            # If we assume that instructions we want to sanitize need to be 5
            # bytes -- 1 for the opcode and 4 for the address -- exclude cases
            # where the hex value could not be an address.
            # The exception is jumps which are as small as 2 bytes
            # but are still useful to sanitize.
            if "0x" in inst.op_str and (
                inst.mnemonic in JUMP_MNEMONICS or inst.size > 4
            ):
                result = self.sanitize(inst)
            else:
                result = (inst.mnemonic, inst.op_str)

            # mnemonic + " " + op_str
            asm.append((hex(inst.address), " ".join(result)))

        return asm
