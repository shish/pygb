from enum import Enum
from cart import Cart, TestCart


try:
    # boot with the logo scroll if we have a boot rom
    with open("boot.gb", "rb") as fp:
        BOOT = list(fp.read(0x100))
        # NOP the DRM
        BOOT[0xE9] = 0x00
        BOOT[0xEA] = 0x00
        BOOT[0xFA] = 0x00
        BOOT[0xFB] = 0x00
except IOError:
    # Directly set CPU registers as
    # if the logo had been scrolled
    BOOT = [
        # prod memory
        0x31, 0xFE, 0xFF,  # LD SP,$FFFE

        # set flags
        0x3E, 0x01,  # LD A,$00
        0xCB, 0x7F,  # BIT 7,A (sets Z,n,H)
        0x37,        # SCF (sets C)

        # set registers
        0x3E, 0x01,  # LD A,$01
        0x06, 0x00,  # LD B,$01
        0x0E, 0x13,  # LD C,$13
        0x16, 0x00,  # LD D,$00
        0x1E, 0xD8,  # LD E,$D8
        0x26, 0x01,  # LD H,$01
        0x2E, 0x4D,  # LD L,$4D
    ]

    # these 5 instructions must be the final 2 --
    # after these finish executing, PC needs to be 0x100
    BOOT += [0x00] * (0xFE - len(BOOT))
    BOOT += [0xE0, 0x50]  # LDH 50,A (disable boot rom)

assert len(BOOT) == 0x100, f"Bootloader must be 256 bytes ({len(BOOT)})"


class Reg(Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"
    H = "H"
    L = "L"

    BC = "BC"
    DE = "DE"
    AF = "AF"
    HL = "HL"

    SP = "SP"
    PC = "PC"

    MEM_AT_HL = "MEM_AT_HL"


class OpNotImplemented(Exception):
    pass


def opcode(name, cycles, args=""):
    def dec(fn):
        fn.name = name
        fn.cycles = cycles
        fn.args = args
        return fn
    return dec


class CPU:
    # <editor-fold description="Init">
    def __init__(self, cart: Cart=None):
        self.cart = cart or TestCart()
        self.interrupts = True
        self.halt = False
        self.stop = False
        self._nopslide = 0
        self.debug_str = ""

        # registers
        self.A = 0x01  # GB / SGB. FF=GBP, 11=GBC
        self.B = 0x00
        self.C = 0x13
        self.D = 0x00
        self.E = 0xD8
        self.H = 0x01
        self.L = 0x4D

        self.SP = 0xFFFE
        self.PC = 0x0000

        # flags
        self.FLAG_Z = True  # zero
        self.FLAG_N = False  # subtract
        self.FLAG_H = True  # half-carry
        self.FLAG_C = True  # carry

        self.rom = self.cart.data

        self.ram = [0] * (0xFFFF+1)

        # 16KB ROM bank 0
        for x in range(0x0000, 0x4000):
            self.ram[x] = self.rom[x]

        # 16KB Switchable ROM bank
        for x in range(0x4000, 0x8000):
            self.ram[x] = self.rom[x]

        # 8KB VRAM
        # 0x8000 - 0xA000
        # from random import randint
        # for x in range(0x8000, 0xA000):
        #   self.ram[x] = randint(0, 256)

        # 8KB Switchable RAM bank
        # 0xA000 - 0xC000

        # 8KB Internal RAM
        # 0xC000 - 0xE000

        # Echo internal RAM
        # 0xE000 - 0xFE00

        # Sprite Attrib Memory (OAM)
        # 0xFE00 - 0xFEA0

        # Empty
        # 0xFEA0 - 0xFF00

        # IO Ports
        # 0xFF00 - 0xFF4C
        self.ram[0xFF00] = 0x00  # BUTTONS
        self.ram[0xFF01] = 0x00  # SB (Serial Data)
        self.ram[0xFF02] = 0x00  # SC (Serial Control)
        self.ram[0xFF04] = 0x00  # DIV
        self.ram[0xFF05] = 0x00  # TIMA
        self.ram[0xFF06] = 0x00  # TMA
        self.ram[0xFF07] = 0x00  # TAC
        self.ram[0xFF10] = 0x80  # NR10
        self.ram[0xFF11] = 0xBF  # NR11
        self.ram[0xFF12] = 0xF3  # NR12
        self.ram[0xFF14] = 0xBF  # NR14
        self.ram[0xFF16] = 0x3F  # NR21
        self.ram[0xFF17] = 0x00  # NR22
        self.ram[0xFF19] = 0xBF  # NR24
        self.ram[0xFF1A] = 0x7F  # NR30
        self.ram[0xFF1B] = 0xFF  # NR31
        self.ram[0xFF1C] = 0x9F  # NR32
        self.ram[0xFF1E] = 0xBF  # NR33
        self.ram[0xFF20] = 0xFF  # NR41
        self.ram[0xFF21] = 0x00  # NR42
        self.ram[0xFF22] = 0x00  # NR43
        self.ram[0xFF23] = 0xBF  # NR30
        self.ram[0xFF24] = 0x77  # NR50
        self.ram[0xFF25] = 0xF3  # NR51
        self.ram[0xFF26] = 0xF1  # NR52  # 0xF0 on SGB
        self.ram[0xFF40] = 0x91  # LCDC
        self.ram[0xFF42] = 0x00  # SCX aka SCROLL_Y
        self.ram[0xFF43] = 0x00  # SCY aka SCROLL_X
        self.ram[0xFF44] = 144  # LY aka currently drawn line, 0-153, >144 = vblank
        self.ram[0xFF45] = 0x00  # LYC
        self.ram[0xFF47] = 0xFC  # BGP
        self.ram[0xFF48] = 0xFF  # OBP0
        self.ram[0xFF49] = 0xFF  # OBP1
        self.ram[0xFF4A] = 0x00  # WY
        self.ram[0xFF4B] = 0x00  # WX

        # Empty
        # 0xFF4C - 0xFF80

        # Internal RAM
        # 0xFF80 - 0xFFFF

        # Interrupt Enabled Register
        self.ram[0xFFFF] = 0x00  # IE

        # TODO: ram[E000-FE00] mirrors ram[C000-DE00]

        self.ops = [
            getattr(self, "op%02X" % n)
            for n in range(0x00, 0xFF+1)
        ]
        self.cb_ops = [
            getattr(self, "opCB%02X" % n)
            for n in range(0x00, 0xFF+1)
        ]

    def __str__(self):
        s = (
            "ZNHC PC   SP\n"
            "%d%d%d%d %04X %04X\n"
            f"A  {self.A:02X} {self.A:08b} {self.A}\n"
            f"B  {self.B:02X} {self.B:08b} {self.B}\n"
            f"C  {self.C:02X} {self.C:08b} {self.C}\n"
            f"D  {self.D:02X} {self.D:08b} {self.D}\n"
            f"E  {self.E:02X} {self.E:08b} {self.E}\n"
            f"H  {self.H:02X} {self.H:08b} {self.H}\n"
            f"L  {self.L:02X} {self.L:08b} {self.L}\n"
            % (
                self.FLAG_Z or 0, self.FLAG_N or 0, self.FLAG_H or 0, self.FLAG_C or 0,
                self.PC, self.SP,
            )
        )
        if (
            self.A > 0xFF or self.A < 0x00 or
            self.B > 0xFF or self.B < 0x00 or
            self.C > 0xFF or self.C < 0x00 or
            self.D > 0xFF or self.D < 0x00 or
            self.E > 0xFF or self.E < 0x00 or
            self.H > 0xFF or self.H < 0x00 or
            self.L > 0xFF or self.L < 0x00
        ):
            raise Exception("Register value out of range:" + s)
        return s

    # </editor-fold>

    # <editor-fold description="Tick">
    def tick(self):
        if self.ram[0xFF50] == 0:
            src = BOOT
        else:
            # print("Boot finished")
            # raise Exception()
            src = self.ram

        if self.PC >= 0xFF00:
            raise Exception("PC reached IO ports (0x%04X) after %d NOPs" % (self.PC, self._nopslide))
            
        ins = src[self.PC]
        if ins == 0x00:
            self._nopslide += 1
            self.PC += 1
            if self._nopslide > 0xFF and False:
                raise Exception("NOP slide")
            return 4
        else:
            self._nopslide = 0

        if ins == 0xCB:
            ins = src[self.PC + 1]
            cmd = self.cb_ops[ins]
            if not cmd:
                raise OpNotImplemented("Opcode CB %02X (@%04X) not implemented" % (ins, self.PC+1))
            self.PC += 1
        else:
            cmd = self.ops[ins]
            if not cmd:
                raise OpNotImplemented("Opcode %02X (@%04X) not implemented" % (ins, self.PC))

        debug = False
        if cmd.args == "B":
            param = src[self.PC + 1]
            self.debug_str = f"[{self.PC:04X}({ins:02X})]: {cmd.name.replace('n', '$%02X' % param)}"
            self.PC += 2
            cmd(param)
        elif cmd.args == "b":
            param = src[self.PC + 1]
            if param > 128:
                param -= 256
                self.debug_str = f"[{self.PC:04X}({ins:02X})]: {cmd.name.replace('n', '%d' % param)}"
            self.PC += 2
            cmd(param)
        elif cmd.args == "H":
            param = (src[self.PC + 1]) | (src[self.PC + 2] << 8)
            self.debug_str = f"[{self.PC:04X}({ins:02X})]: {cmd.name.replace('nn', '$%04X' % param)}"
            self.PC += 3
            cmd(param)
        else:
            self.debug_str = f"[{self.PC:04X}({ins:02X})]: {cmd.name}"
            self.PC += 1
            cmd()
        if debug:
            print(self.debug_str)
            print(self)

        return cmd.cycles
    # </editor-fold>

    # <editor-fold description="Registers">
    @property
    def AF(self):
        """
        >>> cpu = CPU()
        >>> cpu.A = 0x01
        >>> cpu.FLAG_Z = True
        >>> cpu.FLAG_N = True
        >>> cpu.FLAG_H = True
        >>> cpu.FLAG_C = True
        >>> cpu.AF
        496
        """
        return (
            self.A << 8 |
            (self.FLAG_Z or 0) << 7 |
            (self.FLAG_N or 0) << 6 |
            (self.FLAG_H or 0) << 5 |
            (self.FLAG_C or 0) << 4
        )

    @AF.setter
    def AF(self, val):
        self.A = val >> 8 & 0xFF
        self.FLAG_Z = bool(val & 0b10000000)
        self.FLAG_N = bool(val & 0b01000000)
        self.FLAG_H = bool(val & 0b00100000)
        self.FLAG_C = bool(val & 0b00010000)

    @property
    def BC(self):
        """
        >>> cpu = CPU()
        >>> cpu.BC = 0x1234
        >>> cpu.B, cpu.C
        (18, 52)

        >>> cpu.B, cpu.C = 1, 2
        >>> cpu.BC
        258
        """
        return self.B << 8 | self.C

    @BC.setter
    def BC(self, val):
        self.B = val >> 8 & 0xFF
        self.C = val & 0xFF

    @property
    def DE(self):
        """
        >>> cpu = CPU()
        >>> cpu.DE = 0x1234
        >>> cpu.D, cpu.E
        (18, 52)

        >>> cpu.D, cpu.E = 1, 2
        >>> cpu.DE
        258
        """
        return self.D << 8 | self.E

    @DE.setter
    def DE(self, val):
        self.D = val >> 8 & 0xFF
        self.E = val & 0xFF

    @property
    def HL(self):
        """
        >>> cpu = CPU()
        >>> cpu.HL = 0x1234
        >>> cpu.H, cpu.L
        (18, 52)

        >>> cpu.H, cpu.L = 1, 2
        >>> cpu.HL
        258
        """
        return self.H << 8 | self.L

    @HL.setter
    def HL(self, val):
        self.H = val >> 8 & 0xFF
        self.L = val & 0xFF

    @property
    def MEM_AT_HL(self):
        return self.ram[self.HL]

    @MEM_AT_HL.setter
    def MEM_AT_HL(self, val):
        self.ram[self.HL] = val
    # </editor-fold>

    # <editor-fold description="Empty Instructions">
    @opcode("ERR", 4)
    def opCB(self):
        raise OpNotImplemented("CB is special cased, you shouldn't get here")

    def _err(self, op):
        raise OpNotImplemented("Opcode D3 not implemented")

    opD3 = opcode("ERR", 4)(lambda self: self._err("D3"))
    opDB = opcode("ERR", 4)(lambda self: self._err("DB"))
    opDD = opcode("ERR", 4)(lambda self: self._err("DD"))
    opDE = opcode("ERR", 4)(lambda self: self._err("DE"))
    opE3 = opcode("ERR", 4)(lambda self: self._err("E3"))
    opE4 = opcode("ERR", 4)(lambda self: self._err("E4"))
    opEB = opcode("ERR", 4)(lambda self: self._err("EB"))
    opEC = opcode("ERR", 4)(lambda self: self._err("EC"))
    opED = opcode("ERR", 4)(lambda self: self._err("ED"))
    opF4 = opcode("ERR", 4)(lambda self: self._err("F4"))
    opFC = opcode("ERR", 4)(lambda self: self._err("FC"))
    opFD = opcode("ERR", 4)(lambda self: self._err("FD"))
    # </editor-fold>

    # <editor-fold description="3.3.1 8-Bit Loads">
    # ===================================
    # 1. LD nn,n
    def _ld_val_to_reg(self, val, reg: Reg):
        setattr(self, reg.value, val)

    # 8-bit Loads
    op06 = opcode("LD B,n", 8, "B")(lambda self, val: self._ld_val_to_reg(val, Reg.B))
    op0E = opcode("LD C,n", 8, "B")(lambda self, val: self._ld_val_to_reg(val, Reg.C))
    op16 = opcode("LD D,n", 8, "B")(lambda self, val: self._ld_val_to_reg(val, Reg.D))
    op1E = opcode("LD E,n", 8, "B")(lambda self, val: self._ld_val_to_reg(val, Reg.E))
    op26 = opcode("LD H,n", 8, "B")(lambda self, val: self._ld_val_to_reg(val, Reg.H))
    op2E = opcode("LD L,n", 8, "B")(lambda self, val: self._ld_val_to_reg(val, Reg.L))

    # ===================================
    # 2. LD r1,r2
    # Put r2 into r1
    def _ld_reg_from_reg(self, r1: Reg, r2: Reg):
        setattr(self, r1.value, getattr(self, r2.value))

    op7F = opcode("LD A,A", 4)(lambda self: self._ld_reg_from_reg(Reg.A, Reg.A))
    op78 = opcode("LD A,B", 4)(lambda self: self._ld_reg_from_reg(Reg.A, Reg.B))
    op79 = opcode("LD A,C", 4)(lambda self: self._ld_reg_from_reg(Reg.A, Reg.C))
    op7A = opcode("LD A,D", 4)(lambda self: self._ld_reg_from_reg(Reg.A, Reg.D))
    op7B = opcode("LD A,E", 4)(lambda self: self._ld_reg_from_reg(Reg.A, Reg.E))
    op7C = opcode("LD A,H", 4)(lambda self: self._ld_reg_from_reg(Reg.A, Reg.H))
    op7D = opcode("LD A,L", 4)(lambda self: self._ld_reg_from_reg(Reg.A, Reg.L))
    op7E = opcode("LD A,[HL]", 8)(lambda self: self._ld_reg_from_reg(Reg.A, Reg.MEM_AT_HL))

    op40 = opcode("LD B,B", 4)(lambda self: self._ld_reg_from_reg(Reg.B, Reg.B))
    op41 = opcode("LD B,C", 4)(lambda self: self._ld_reg_from_reg(Reg.B, Reg.C))
    op42 = opcode("LD B,D", 4)(lambda self: self._ld_reg_from_reg(Reg.B, Reg.D))
    op43 = opcode("LD B,E", 4)(lambda self: self._ld_reg_from_reg(Reg.B, Reg.E))
    op44 = opcode("LD B,H", 4)(lambda self: self._ld_reg_from_reg(Reg.B, Reg.H))
    op45 = opcode("LD B,L", 4)(lambda self: self._ld_reg_from_reg(Reg.B, Reg.L))
    op46 = opcode("LD B,[HL]", 8)(lambda self: self._ld_reg_from_reg(Reg.B, Reg.MEM_AT_HL))
    op47 = opcode("LD B,A", 4)(lambda self: self._ld_reg_from_reg(Reg.B, Reg.A))

    op48 = opcode("LD C,B", 4)(lambda self: self._ld_reg_from_reg(Reg.C, Reg.B))
    op49 = opcode("LD C,C", 4)(lambda self: self._ld_reg_from_reg(Reg.C, Reg.C))
    op4A = opcode("LD C,D", 4)(lambda self: self._ld_reg_from_reg(Reg.C, Reg.D))
    op4B = opcode("LD C,E", 4)(lambda self: self._ld_reg_from_reg(Reg.C, Reg.E))
    op4C = opcode("LD C,H", 4)(lambda self: self._ld_reg_from_reg(Reg.C, Reg.H))
    op4D = opcode("LD C,L", 4)(lambda self: self._ld_reg_from_reg(Reg.C, Reg.L))
    op4E = opcode("LD C,[HL]", 8)(lambda self: self._ld_reg_from_reg(Reg.C, Reg.MEM_AT_HL))
    op4F = opcode("LD C,A", 4)(lambda self: self._ld_reg_from_reg(Reg.C, Reg.A))

    op50 = opcode("LD D,B", 4)(lambda self: self._ld_reg_from_reg(Reg.D, Reg.B))
    op51 = opcode("LD D,C", 4)(lambda self: self._ld_reg_from_reg(Reg.D, Reg.C))
    op52 = opcode("LD D,D", 4)(lambda self: self._ld_reg_from_reg(Reg.D, Reg.D))
    op53 = opcode("LD D,E", 4)(lambda self: self._ld_reg_from_reg(Reg.D, Reg.E))
    op54 = opcode("LD D,H", 4)(lambda self: self._ld_reg_from_reg(Reg.D, Reg.H))
    op55 = opcode("LD D,L", 4)(lambda self: self._ld_reg_from_reg(Reg.D, Reg.L))
    op56 = opcode("LD D,[HL]", 8)(lambda self: self._ld_reg_from_reg(Reg.D, Reg.MEM_AT_HL))
    op57 = opcode("LD D,A", 4)(lambda self: self._ld_reg_from_reg(Reg.D, Reg.A))

    op58 = opcode("LD E,B", 4)(lambda self: self._ld_reg_from_reg(Reg.E, Reg.B))
    op59 = opcode("LD E,C", 4)(lambda self: self._ld_reg_from_reg(Reg.E, Reg.C))
    op5A = opcode("LD E,D", 4)(lambda self: self._ld_reg_from_reg(Reg.E, Reg.D))
    op5B = opcode("LD E,E", 4)(lambda self: self._ld_reg_from_reg(Reg.E, Reg.E))
    op5C = opcode("LD E,H", 4)(lambda self: self._ld_reg_from_reg(Reg.E, Reg.H))
    op5D = opcode("LD E,L", 4)(lambda self: self._ld_reg_from_reg(Reg.E, Reg.L))
    op5E = opcode("LD E,[HL]", 8)(lambda self: self._ld_reg_from_reg(Reg.E, Reg.MEM_AT_HL))
    op5F = opcode("LD E,A", 4)(lambda self: self._ld_reg_from_reg(Reg.E, Reg.A))

    op60 = opcode("LD H,B", 4)(lambda self: self._ld_reg_from_reg(Reg.H, Reg.B))
    op61 = opcode("LD H,C", 4)(lambda self: self._ld_reg_from_reg(Reg.H, Reg.C))
    op62 = opcode("LD H,D", 4)(lambda self: self._ld_reg_from_reg(Reg.H, Reg.D))
    op63 = opcode("LD H,E", 4)(lambda self: self._ld_reg_from_reg(Reg.H, Reg.E))
    op64 = opcode("LD H,H", 4)(lambda self: self._ld_reg_from_reg(Reg.H, Reg.H))
    op65 = opcode("LD H,L", 4)(lambda self: self._ld_reg_from_reg(Reg.H, Reg.L))
    op66 = opcode("LD H,[HL]", 8)(lambda self: self._ld_reg_from_reg(Reg.H, Reg.MEM_AT_HL))
    op67 = opcode("LD H,A", 4)(lambda self: self._ld_reg_from_reg(Reg.H, Reg.A))

    op68 = opcode("LD L,B", 4)(lambda self: self._ld_reg_from_reg(Reg.L, Reg.B))
    op69 = opcode("LD L,C", 4)(lambda self: self._ld_reg_from_reg(Reg.L, Reg.C))
    op6A = opcode("LD L,D", 4)(lambda self: self._ld_reg_from_reg(Reg.L, Reg.D))
    op6B = opcode("LD L,E", 4)(lambda self: self._ld_reg_from_reg(Reg.L, Reg.E))
    op6C = opcode("LD L,H", 4)(lambda self: self._ld_reg_from_reg(Reg.L, Reg.H))
    op6D = opcode("LD L,L", 4)(lambda self: self._ld_reg_from_reg(Reg.L, Reg.L))
    op6E = opcode("LD L,[HL]", 8)(lambda self: self._ld_reg_from_reg(Reg.L, Reg.MEM_AT_HL))
    op6F = opcode("LD L,A", 4)(lambda self: self._ld_reg_from_reg(Reg.L, Reg.A))

    op70 = opcode("LD [HL],B", 8)(lambda self: self._ld_reg_from_reg(Reg.MEM_AT_HL, Reg.B))
    op71 = opcode("LD [HL],C", 8)(lambda self: self._ld_reg_from_reg(Reg.MEM_AT_HL, Reg.C))
    op72 = opcode("LD [HL],D", 8)(lambda self: self._ld_reg_from_reg(Reg.MEM_AT_HL, Reg.D))
    op73 = opcode("LD [HL],E", 8)(lambda self: self._ld_reg_from_reg(Reg.MEM_AT_HL, Reg.E))
    op74 = opcode("LD [HL],H", 8)(lambda self: self._ld_reg_from_reg(Reg.MEM_AT_HL, Reg.H))
    op75 = opcode("LD [HL],L", 8)(lambda self: self._ld_reg_from_reg(Reg.MEM_AT_HL, Reg.L))
    op77 = opcode("LD [HL],A", 8)(lambda self: self._ld_reg_from_reg(Reg.MEM_AT_HL, Reg.L))

    @opcode("LD [HL],n", 12, "B")
    def op36(self, n):
        self.ram[self.HL] = n

    # ===================================
    # 3. LD A,n
    # Put n into A
    def _ld_val_to_a(self, val):
        self.A = val

    op0A = opcode("LD A,[BC]", 8)(lambda self: self._ld_val_to_a(self.ram[self.BC]))
    op1A = opcode("LD A,[DE]", 8)(lambda self: self._ld_val_to_a(self.ram[self.DE]))
    opFA = opcode("LD A,[nn]", 16, "H")(lambda self, val: self._ld_val_to_a(self.ram[val]))
    op3E = opcode("LD A,n", 8, "B")(lambda self, val: self._ld_val_to_a(val))

    # ===================================
    # 4. LD [nn],A
    def _ld_a_to_mem(self, val):
        self.ram[val] = self.A

    op02 = opcode("LD [BC],A", 8)(lambda self: self._ld_a_to_mem(self.BC))
    op12 = opcode("LD [DE],A", 8)(lambda self: self._ld_a_to_mem(self.BC))
    opEA = opcode("LD [nn],A", 16, 'H')(lambda self, val: self._ld_a_to_mem(val))

    # ===================================
    # 5. LD A,(C)
    @opcode("LD A,[C]", 8)
    def opF2(self):
        self.A = self.ram[0xFF00 + self.C]

    # ===================================
    # 6. LD (C),A
    @opcode("LD A,[C]", 8)
    def opE2(self):
        self.ram[0xFF00 + self.C] = self.A

    # ===================================
    # 7. LD A,[HLD]
    # 8. LD A,[HL-]
    # 9. LDD A,[HL]
    @opcode("LD A,[HL-]", 8)
    def op3A(self):
        self.A = self.ram[self.HL]
        self.HL -= 1

    # ===================================
    # 10. LD [HLD],A
    # 11. LD [HL-],A
    # 12. LDD [HL],A
    @opcode("LD [HL-],A", 8)
    def op32(self):
        self.ram[self.HL] = self.A
        self.HL -= 1

    # ===================================
    # 13. LD A,[HLI]
    # 14. LD A,[HL+]
    # 15. LDI A,[HL]
    @opcode("LD A,[HL+]", 8)
    def op2A(self):
        self.A = self.ram[self.HL]
        self.HL += 1

    # ===================================
    # 16. LD [HLI],A
    # 17. LD [HL+],A
    # 18. LDI [HL],A
    @opcode("LD [HL+],A", 8)
    def op22(self):
        self.ram[self.HL] = self.A
        self.HL += 1

    # ===================================
    # 19. LDH [n],A
    @opcode("LDH [n],A", 8, "B")
    def opE0(self, val):
        if val == 0x01:
            print(chr(self.A), end="")
            # print("0xFF%02X = 0x%02X (%s)" % (val, self.A, chr(self.A)))
        self.ram[0xFF00 + val] = self.A

    # ===================================
    # 20. LDH A,[n]
    @opcode("LDH A,[n]", 8, "B")
    def opF0(self, val):
        self.A = self.ram[0xFF00 + val]
    # </editor-fold>

    # <editor-fold description="3.3.2 16-Bit Loads">
    # ===================================
    # 1. LD n,nn
    op01 = opcode("LD BC,nn", 12, "H")(lambda self, val: self._ld_val_to_reg(val, Reg.BC))
    op11 = opcode("LD DE,nn", 12, "H")(lambda self, val: self._ld_val_to_reg(val, Reg.DE))
    op21 = opcode("LD HL,nn", 12, "H")(lambda self, val: self._ld_val_to_reg(val, Reg.HL))
    op31 = opcode("LD SP,nn", 12, "H")(lambda self, val: self._ld_val_to_reg(val, Reg.SP))

    # ===================================
    # 2. LD SP,HL
    opF9 = opcode("LD SP,HL", 8)(lambda self: self._ld_reg_from_reg(Reg.SP, Reg.HL))

    # ===================================
    # 3. LD HL,SP+n
    # 4. LDHL SP,n
    @opcode("LD HL,SP+n", 12, "B")
    def opF8(self, val):
        self.HL = self.SP + val
        self.FLAG_Z = False
        self.FLAG_N = False
        self.FLAG_H = None  # FIXME: Set or reset according to operation
        self.FLAG_C = None  # FIXME: Set or reset according to operation

    # ===================================
    # 5. LD [nn],SP
    @opcode("LD [nn],SP", 20, "H")
    def op08(self, val):
        self.ram[val] = self.SP

    # ===================================
    # 6. PUSH nn
    opF5 = opcode("PUSH AF", 16)(lambda self: self._push16(Reg.AF))
    opC5 = opcode("PUSH BC", 16)(lambda self: self._push16(Reg.BC))
    opD5 = opcode("PUSH DE", 16)(lambda self: self._push16(Reg.DE))
    opE5 = opcode("PUSH HL", 16)(lambda self: self._push16(Reg.HL))

    # ===================================
    # 6. POP nn
    opF1 = opcode("POP AF", 12)(lambda self: self._pop16(Reg.AF))
    opC1 = opcode("POP BC", 12)(lambda self: self._pop16(Reg.BC))
    opD1 = opcode("POP DE", 12)(lambda self: self._pop16(Reg.DE))
    opE1 = opcode("POP HL", 12)(lambda self: self._pop16(Reg.HL))

    # </editor-fold>

    # <editor-fold description="3.3.3 8-Bit Arithmetic">

    # ===================================
    # 1. ADD A,n
    def _add(self, val):
        self.A += val
        self.A &= 0xFF
        self.FLAG_Z = self.A == 0
        self.FLAG_N = False
        self.FLAG_H = None  # FIXME: Set if carry from bit 3
        self.FLAG_C = None  # FIXME: Set if carry from bit 7

    op87 = opcode("ADD A,A", 4)(lambda self: self._add(self.A))
    op80 = opcode("ADD A,B", 4)(lambda self: self._add(self.B))
    op81 = opcode("ADD A,C", 4)(lambda self: self._add(self.C))
    op82 = opcode("ADD A,D", 4)(lambda self: self._add(self.D))
    op83 = opcode("ADD A,E", 4)(lambda self: self._add(self.E))
    op84 = opcode("ADD A,H", 4)(lambda self: self._add(self.H))
    op85 = opcode("ADD A,L", 4)(lambda self: self._add(self.L))
    op86 = opcode("ADD A,[HL]", 8)(lambda self: self._add(self.ram[self.HL]))
    opC6 = opcode("ADD A,n", 8, "B")(lambda self, val: self._add(val))

    # ===================================
    # 2. ADC A,n
    def _adc(self, val):
        self.A += val + int(self.FLAG_C)
        self.A &= 0xFF
        self.FLAG_Z = self.A == 0
        self.FLAG_N = False
        self.FLAG_H = None  # FIXME: Set if carry from bit 3
        self.FLAG_C = None  # FIXME: Set if carry from bit 7

    op8F = opcode("ADC A,A", 4)(lambda self: self._adc(self.A))
    op88 = opcode("ADC A,B", 4)(lambda self: self._adc(self.B))
    op89 = opcode("ADC A,C", 4)(lambda self: self._adc(self.C))
    op8A = opcode("ADC A,D", 4)(lambda self: self._adc(self.D))
    op8B = opcode("ADC A,E", 4)(lambda self: self._adc(self.E))
    op8C = opcode("ADC A,H", 4)(lambda self: self._adc(self.H))
    op8D = opcode("ADC A,L", 4)(lambda self: self._adc(self.L))
    op8E = opcode("ADC A,[HL]", 8)(lambda self: self._adc(self.ram[self.HL]))
    opCE = opcode("ADC A,n", 8, "B")(lambda self, val: self._adc(val))

    # ===================================
    # 3. SUB n
    def _sub(self, val):
        self.A -= val
        self.FLAG_C = self.A < 0  # ??? FIXME: Set if no borrow
        self.A &= 0xFF
        self.FLAG_Z = self.A == 0
        self.FLAG_N = True
        self.FLAG_H = None  # FIXME: Set if borrow from bit 4

    op97 = opcode("SUB A,A", 4)(lambda self: self._sub(self.A))
    op90 = opcode("SUB A,B", 4)(lambda self: self._sub(self.B))
    op91 = opcode("SUB A,C", 4)(lambda self: self._sub(self.C))
    op92 = opcode("SUB A,D", 4)(lambda self: self._sub(self.D))
    op93 = opcode("SUB A,E", 4)(lambda self: self._sub(self.E))
    op94 = opcode("SUB A,H", 4)(lambda self: self._sub(self.H))
    op95 = opcode("SUB A,L", 4)(lambda self: self._sub(self.L))
    op96 = opcode("SUB A,[HL]", 8)(lambda self: self._sub(self.ram[self.HL]))
    opD6 = opcode("SUB A,n", 8, "B")(lambda self, val: self._sub(val))

    # ===================================
    # 4. SBC n
    def _sbc(self, val):
        self.A -= val + int(self.FLAG_C)
        self.A &= 0xFF
        self.FLAG_Z = self.A == 0
        self.FLAG_N = True
        self.FLAG_H = None  # FIXME: Set if no borrow from bit 4
        self.FLAG_C = None  # FIXME: Set if no borrow

    op9F = opcode("SBC A,A", 4)(lambda self: self._sbc(self.A))
    op98 = opcode("SBC A,B", 4)(lambda self: self._sbc(self.B))
    op99 = opcode("SBC A,C", 4)(lambda self: self._sbc(self.C))
    op9A = opcode("SBC A,D", 4)(lambda self: self._sbc(self.D))
    op9B = opcode("SBC A,E", 4)(lambda self: self._sbc(self.E))
    op9C = opcode("SBC A,H", 4)(lambda self: self._sbc(self.H))
    op9D = opcode("SBC A,L", 4)(lambda self: self._sbc(self.L))
    op9E = opcode("SBC A,[HL]", 8)(lambda self: self._sbc(self.ram[self.HL]))
    # op?? = opcode("SBC A,n", 8, "B")(lambda self, val: self._sbc(val))

    # ===================================
    # 5. AND n
    def _and(self, val):
        self.A &= val
        self.FLAG_Z = int(self.A == 0)
        self.FLAG_N = False
        self.FLAG_H = True
        self.FLAG_C = False

    opA7 = opcode("AND A", 4)(lambda self: self._and(self.A))
    opA0 = opcode("AND B", 4)(lambda self: self._and(self.B))
    opA1 = opcode("AND C", 4)(lambda self: self._and(self.C))
    opA2 = opcode("AND D", 4)(lambda self: self._and(self.D))
    opA3 = opcode("AND E", 4)(lambda self: self._and(self.E))
    opA4 = opcode("AND H", 4)(lambda self: self._and(self.H))
    opA5 = opcode("AND L", 4)(lambda self: self._and(self.L))
    opA6 = opcode("AND [HL]", 8)(lambda self: self._and(self.ram[self.HL]))
    opE6 = opcode("AND n", 8, "B")(lambda self, n: self._and(self.ram[n]))

    # ===================================
    # 6. OR n
    def _or(self, val):
        self.A |= val
        self.FLAG_Z = int(self.A == 0)
        self.FLAG_N = False
        self.FLAG_H = False
        self.FLAG_C = False

    opB7 = opcode("OR A", 4)(lambda self: self._or(self.A))
    opB0 = opcode("OR B", 4)(lambda self: self._or(self.B))
    opB1 = opcode("OR C", 4)(lambda self: self._or(self.C))
    opB2 = opcode("OR D", 4)(lambda self: self._or(self.D))
    opB3 = opcode("OR E", 4)(lambda self: self._or(self.E))
    opB4 = opcode("OR H", 4)(lambda self: self._or(self.H))
    opB5 = opcode("OR L", 4)(lambda self: self._or(self.L))
    opB6 = opcode("OR [HL]", 8)(lambda self: self._or(self.ram[self.HL]))
    opF6 = opcode("OR n", 8, "B")(lambda self, n: self._or(self.ram[n]))

    # ===================================
    # 7. XOR
    def _xor(self, val):
        """
        >>> c = CPU()
        >>> c.A = 0b0000
        >>> c.C = 0b1010
        >>> c._xor(c.C)
        >>> bin(c.A)
        '0b1010'
        >>> c._xor(c.A)
        >>> bin(c.A)
        '0b0'
        """
        self.A ^= val
        self.FLAG_Z = int(self.A == 0)
        self.FLAG_N = False
        self.FLAG_H = False
        self.FLAG_C = False

    opAF = opcode("XOR A", 4)(lambda self: self._xor(self.A))
    opA8 = opcode("XOR B", 4)(lambda self: self._xor(self.B))
    opA9 = opcode("XOR C", 4)(lambda self: self._xor(self.C))
    opAA = opcode("XOR D", 4)(lambda self: self._xor(self.D))
    opAB = opcode("XOR E", 4)(lambda self: self._xor(self.E))
    opAC = opcode("XOR H", 4)(lambda self: self._xor(self.H))
    opAD = opcode("XOR L", 4)(lambda self: self._xor(self.L))
    opAE = opcode("XOR [HL]", 8)(lambda self: self._xor(self.ram[self.HL]))
    opEE = opcode("XOR n", 8, "B")(lambda self, n: self._xor(self.ram[n]))

    # ===================================
    # 8. CP
    # Compare A with n
    def _cp(self, n):
        self.FLAG_Z = self.A == n
        self.FLAG_N = True
        self.FLAG_H = None  # FIXME: Set if no borrow from bit 4
        self.FLAG_C = self.A < n

    opBF = opcode("CP A", 4)(lambda self: self._cp(self.A))
    opB8 = opcode("CP B", 4)(lambda self: self._cp(self.B))
    opB9 = opcode("CP C", 4)(lambda self: self._cp(self.C))
    opBA = opcode("CP D", 4)(lambda self: self._cp(self.D))
    opBB = opcode("CP E", 4)(lambda self: self._cp(self.E))
    opBC = opcode("CP H", 4)(lambda self: self._cp(self.H))
    opBD = opcode("CP L", 4)(lambda self: self._cp(self.L))
    opBE = opcode("CP [HL]", 8)(lambda self: self._cp(self.ram[self.HL]))
    opFE = opcode("CP n", 8, "B")(lambda self, val: self._cp(val))

    # ===================================
    # 9. INC
    def _inc8(self, reg):
        val = getattr(self, reg) + 1
        val &= 0xFF
        setattr(self, reg, val)
        self.FLAG_Z = getattr(self, reg) == 0
        self.FLAG_N = False
        self.FLAG_H = None  # FIXME: "Set if carry from bit 3"

    op3C = opcode("INC A", 4)(lambda self: self._inc8("A"))
    op04 = opcode("INC B", 4)(lambda self: self._inc8("B"))
    op0C = opcode("INC C", 4)(lambda self: self._inc8("C"))
    op14 = opcode("INC D", 4)(lambda self: self._inc8("D"))
    op1C = opcode("INC E", 4)(lambda self: self._inc8("E"))
    op24 = opcode("INC H", 4)(lambda self: self._inc8("H"))
    op2C = opcode("INC L", 4)(lambda self: self._inc8("L"))
    op34 = opcode("INC [HL]", 12)(lambda self: self._inc8("MEM_AT_HL"))

    # ===================================
    # 10. DEC
    def _dec8(self, reg):
        val = getattr(self, reg) - 1
        val &= 0xFF
        setattr(self, reg, val)
        self.FLAG_Z = getattr(self, reg) == 0
        self.FLAG_N = True
        self.FLAG_H = None  # FIXME: "Set if no borrow from bit 4"

    op3D = opcode("DEC A", 4)(lambda self: self._dec8("A"))
    op05 = opcode("DEC B", 4)(lambda self: self._dec8("B"))
    op0D = opcode("DEC C", 4)(lambda self: self._dec8("C"))
    op15 = opcode("DEC D", 4)(lambda self: self._dec8("D"))
    op1D = opcode("DEC E", 4)(lambda self: self._dec8("E"))
    op25 = opcode("DEC H", 4)(lambda self: self._dec8("H"))
    op2D = opcode("DEC L", 4)(lambda self: self._dec8("L"))
    op35 = opcode("DEC [HL]", 12)(lambda self: self._dec8("MEM_AT_HL"))
    # </editor-fold>

    # <editor-fold description="3.3.4 16-Bit Arithmetic">

    # ===================================
    # 1. ADD HL,nn
    def _add_hl(self, val):
        self.HL += val
        self.HL &= 0xFFFF
        self.FLAG_N = False
        self.FLAG_H = None  # FIXME: Set if carry from bit 11
        self.FLAG_C = None  # FIXME: Set if carry from bit 15

    op09 = opcode("ADD HL,BC", 8)(lambda self: self._add_hl(self.BC))
    op19 = opcode("ADD HL,DE", 8)(lambda self: self._add_hl(self.DE))
    op29 = opcode("ADD HL,HL", 8)(lambda self: self._add_hl(self.HL))
    op39 = opcode("ADD HL,SP", 8)(lambda self: self._add_hl(self.SP))

    # ===================================
    # 2. ADD SP,n
    @opcode("ADD SP n", 16, "B")
    def opE8(self, val):
        self.SP += val
        self.SP &= 0xFFFF
        self.FLAG_Z = False
        self.FLAG_N = False
        self.FLAG_H = None  # FIXME: Set or reset according to operation
        self.FLAG_C = None  # FIXME: Set or reset according to operation

    # ===================================
    # 3. INC nn
    def _inc16(self, reg):
        val = getattr(self, reg) + 1
        val &= 0xFFFF
        setattr(self, reg, val)

    op03 = opcode("INC BC", 8)(lambda self: self._inc16("BC"))
    op13 = opcode("INC DE", 8)(lambda self: self._inc16("DE"))
    op23 = opcode("INC HL", 8)(lambda self: self._inc16("HL"))
    op33 = opcode("INC SP", 8)(lambda self: self._inc16("SP"))

    # ===================================
    # 4. DEC nn
    def _dec16(self, reg):
        val = getattr(self, reg) - 1
        val &= 0xFFFF
        setattr(self, reg, val)

    op0B = opcode("DEC BC", 8)(lambda self: self._dec16("BC"))
    op1B = opcode("DEC DE", 8)(lambda self: self._dec16("DE"))
    op2B = opcode("DEC HL", 8)(lambda self: self._dec16("HL"))
    op3B = opcode("DEC SP", 8)(lambda self: self._dec16("SP"))

    # </editor-fold>

    # <editor-fold description="3.3.5 Miscellaneous">
    # ===================================
    # 1. SWAP
    # FIXME: CB36 takes 16 cycles, not 8
    def _swap(self, reg: Reg):
        setattr(self, reg.value, ((getattr(self, reg.value) & 0xF0) >> 4) | ((getattr(self, reg.value) & 0x0F) << 4))
        self.FLAG_Z = getattr(self, reg.value) == 0
        self.FLAG_N = False
        self.FLAG_H = False
        self.FLAG_C = False

    # ===================================
    # 2. DAA
    # A = Binary Coded Decimal of A
    @opcode("DAA", 4)
    def op27(self):
        """
        >>> c = CPU()
        >>> c.A = 92
        >>> c.op27()
        >>> bin(c.A)
        '0b10010010'
        """
        self.A = (((self.A // 10) & 0xF) << 4) | ((self.A % 10) & 0xF)

    # ===================================
    # 3. CPL
    # Flip all bits in A
    @opcode("CPL", 4)
    def op2F(self):
        """
        >>> c = CPU()
        >>> c.A = 0b10101010
        >>> c.op2F()
        >>> bin(c.A)
        '0b1010101'
        """
        self.A ^= 0xFF

    # ===================================
    # 4. CCF
    @opcode("CCF", 4)
    def op3F(self):
        """
        >>> c = CPU()
        >>> c.FLAG_C = False
        >>> c.op3F()
        >>> c.FLAG_C
        True
        >>> c.op3F()
        >>> c.FLAG_C
        False
        """
        self.FLAG_N = False
        self.FLAG_H = False
        self.FLAG_C = not self.FLAG_C

    # ===================================
    # 5. SCF
    @opcode("SCF", 4)
    def op37(self):
        """
        >>> c = CPU()
        >>> c.FLAG_C = False
        >>> c.op37()
        >>> c.FLAG_C
        True
        """
        self.FLAG_N = False
        self.FLAG_H = False
        self.FLAG_C = True

    # ===================================
    # 6. NOP
    @opcode("NOP", 4)
    def op00(self):
        pass

    # ===================================
    # 7. HALT
    # Power down CPU until interrupt occurs

    @opcode("HALT", 4)
    def op76(self):
        self.halt = True
        # FIXME: weird instruction skipping behaviour when interrupts are disabled

    # ===================================
    # 8. STOP
    # Halt CPU & LCD until button pressed

    @opcode("STOP", 4, "B")
    def op10(self, sub):  # 10 00
        if sub == 10:
            self.stop = True
        else:
            raise OpNotImplemented("Missing sub-command 10:%02X" % sub)

    # ===================================
    # 9. DI
    @opcode("DI", 4)
    def opF3(self):
        # FIXME: supposed to take effect after the following instruction
        self.interrupts = False

    # ===================================
    # 10. EI
    @opcode("EI", 4)
    def opFB(self):
        # FIXME: supposed to take effect after the following instruction
        self.interrupts = True

    # </editor-fold>

    # <editor-fold description="3.3.6 Rotates & Shifts">
    # ===================================
    # 1. RCLA
    @opcode("RCLA", 4)
    def op07(self):
        """
        >>> c = CPU()
        >>> c.A = 0b10101010
        >>> c.FLAG_C = False
        >>> c.op07()
        >>> bin(c.A), c.FLAG_C
        ('0b1010100', True)
        """
        self.FLAG_C = (self.A & 0b10000000) != 0
        self.A = ((self.A << 1) & 0xFF)
        self.FLAG_Z = self.A == 0
        self.FLAG_N = False
        self.FLAG_H = False

    # ===================================
    # 2. RLA
    @opcode("RLA", 4)
    def op17(self):
        """
        >>> c = CPU()
        >>> c.A = 0b10101010
        >>> c.FLAG_C = True
        >>> c.op17()
        >>> bin(c.A), c.FLAG_C
        ('0b1010101', True)
        """
        old_c = self.FLAG_C
        self.FLAG_C = (self.A & 0b10000000) != 0
        self.A = ((self.A << 1) & 0xFF) | old_c
        self.FLAG_Z = self.A == 0
        self.FLAG_N = False
        self.FLAG_H = False

    # ===================================
    # 3. RRCA
    @opcode("RRCA", 4)
    def op0F(self):
        """
        >>> c = CPU()
        >>> c.A = 0b10101010
        >>> c.FLAG_C = True
        >>> c.op0F()
        >>> bin(c.A), c.FLAG_C
        ('0b1010101', False)
        """
        self.FLAG_C = (self.A & 0b00000001) != 0
        self.A >>= 1
        self.FLAG_Z = self.A == 0
        self.FLAG_N = False
        self.FLAG_H = False

    # ===================================
    # 4. RRA
    @opcode("RRA", 4)
    def op1F(self):
        """
        >>> c = CPU()
        >>> c.A = 0b10101010
        >>> c.FLAG_C = True
        >>> c.op1F()
        >>> bin(c.A), c.FLAG_C
        ('0b11010101', False)
        """
        old_c = self.FLAG_C
        self.FLAG_C = (self.A & 0b00000001) != 0
        self.A >>= 1
        self.A |= old_c << 7
        self.FLAG_N = 0
        self.FLAG_H = 0
        self.FLAG_Z = int(self.A == 0)

    # ===================================
    # 5. RLC
    def _rlc(self, reg: Reg):
        val = getattr(self, reg.value)
        self.FLAG_C = bool(val & 0b10000000)
        val <<= 1
        setattr(self, reg.value, val & 0xFF)
        self.FLAG_N = False
        self.FLAG_H = False
        self.FLAG_Z = val == 0

    # ===================================
    # 6. RL
    def _rl(self, reg: Reg):
        """
        >>> c = CPU()
        >>> c.A = 0b10101010
        >>> c.FLAG_C = False
        >>> c._rl(Reg.A)
        >>> bin(c.A), c.FLAG_C
        ('0b1010100', True)
        """
        val = getattr(self, reg.value)
        self.FLAG_C = bool(val & 0b10000000)
        val <<= 1
        setattr(self, reg.value, val & 0xFF)
        self.FLAG_N = False
        self.FLAG_H = False
        self.FLAG_Z = val == 0

    # ===================================
    # 7. RRC
    def _rrc(self, reg: Reg):
        val = getattr(self, reg.value)
        self.FLAG_C = bool(val & 0x1)
        val >>= 1
        setattr(self, reg.value, val)
        self.FLAG_N = False
        self.FLAG_H = False
        self.FLAG_Z = val == 0

    # ===================================
    # 8. RR
    def _rr(self, reg: Reg):
        val = getattr(self, reg.value)
        self.FLAG_C = bool(val & 0x1)
        val >>= 1
        setattr(self, reg.value, val)
        self.FLAG_N = False
        self.FLAG_H = False
        self.FLAG_Z = val == 0

    # ===================================
    # 9. SLA
    def _sla(self, reg: Reg):
        val = getattr(self, reg.value)
        self.FLAG_C = bool(val & 0b10000000)
        val <<= 1
        setattr(self, reg.value, val)
        self.FLAG_N = False
        self.FLAG_H = False
        self.FLAG_Z = val == 0

    # ===================================
    # 10. SRA
    def _sra(self, reg: Reg):
        val = getattr(self, reg.value)
        self.FLAG_C = bool(val & 0x1)
        val >>= 1
        if val & 0b01000000:
            val |= 0b10000000
        setattr(self, reg.value, val)
        self.FLAG_N = False
        self.FLAG_H = False
        self.FLAG_Z = val == 0

    # ===================================
    # 11. SRL
    def _srl(self, reg: Reg):
        val = getattr(self, reg.value)
        self.FLAG_C = bool(val & 0x1)
        val >>= 1
        val &= 0b01111111
        setattr(self, reg.value, val)
        self.FLAG_N = False
        self.FLAG_H = False
        self.FLAG_Z = val == 0

    # </editor-fold>

    # <editor-fold description="3.3.7 Bit Opcodes">
    # ===================================
    # 1. BIT b,r
    def _bit(self, reg: Reg, bit):
        """
        >>> c = CPU()
        >>> c.A = 0b00010000
        >>> c._bit(Reg.A, 4)
        >>> c.FLAG_Z
        True
        >>> c._bit(Reg.A, 3)
        >>> c.FLAG_Z
        False
        """
        self.FLAG_Z = bool(getattr(self, reg.value) & (0x1 << bit))
        self.FLAG_N = False
        self.FLAG_H = True

    # ===================================
    # 2. SET b,r
    def _set(self, reg: Reg, bit):
        """
        >>> c = CPU()
        >>> c.A = 0b10010000
        >>> c._set(Reg.A, 2)
        >>> bin(c.A)
        '0b10010100'
        """
        setattr(self, reg.value, getattr(self, reg.value) | (0x01 << bit))

    # ===================================
    # 3. RES b,r
    def _res(self, reg: Reg, bit):
        """
        >>> c = CPU()
        >>> c.A = 0b10010000
        >>> c._res(Reg.A, 4)
        >>> bin(c.A)
        '0b10000000'
        """
        setattr(self, reg.value, getattr(self, reg.value) & ((0x01 << bit) ^ 0xFF))

    _ext_gen = """
for offset, op in enumerate(["RLC", "RRC", "RL", "RR", "SLA", "SRA", "SWAP", "SRL"]):
    for code, arg in enumerate(["B", "C", "D", "E", "H", "L", "[HL]", "A"]):
        opcode = (offset * 8) + code
        time = 16 if arg == "MEM_AT_HL" else 8
        print(
            "opCB%02X = opcode(\"%s %s\", %d)(lambda self: self._%s(Reg.%s))" %
            (opcode, op, arg, time, op.lower(), arg.replace("[HL]", "MEM_AT_HL")))

for offset, op in enumerate(["BIT", "RES", "SET"]):
    for b in range(8):
        for code, arg in enumerate(["B", "C", "D", "E", "H", "L", "[HL]", "A"]):
            opcode = 0x40 + (offset * 0x40) + b * 0x08 + code
            time = 16 if arg == "MEM_AT_HL" else 8
            print(
                "opCB%02X = opcode(\"%s %d %s\", %d)(lambda self: self._%s(Reg.%s, %d))" %
                (opcode, op, b, arg, time, op.lower(), arg.replace("[HL]", "MEM_AT_HL"), b))
"""

    opCB00 = opcode("RLC B", 8)(lambda self: self._rlc(Reg.B))
    opCB01 = opcode("RLC C", 8)(lambda self: self._rlc(Reg.C))
    opCB02 = opcode("RLC D", 8)(lambda self: self._rlc(Reg.D))
    opCB03 = opcode("RLC E", 8)(lambda self: self._rlc(Reg.E))
    opCB04 = opcode("RLC H", 8)(lambda self: self._rlc(Reg.H))
    opCB05 = opcode("RLC L", 8)(lambda self: self._rlc(Reg.L))
    opCB06 = opcode("RLC [HL]", 8)(lambda self: self._rlc(Reg.MEM_AT_HL))
    opCB07 = opcode("RLC A", 8)(lambda self: self._rlc(Reg.A))
    opCB08 = opcode("RRC B", 8)(lambda self: self._rrc(Reg.B))
    opCB09 = opcode("RRC C", 8)(lambda self: self._rrc(Reg.C))
    opCB0A = opcode("RRC D", 8)(lambda self: self._rrc(Reg.D))
    opCB0B = opcode("RRC E", 8)(lambda self: self._rrc(Reg.E))
    opCB0C = opcode("RRC H", 8)(lambda self: self._rrc(Reg.H))
    opCB0D = opcode("RRC L", 8)(lambda self: self._rrc(Reg.L))
    opCB0E = opcode("RRC [HL]", 8)(lambda self: self._rrc(Reg.MEM_AT_HL))
    opCB0F = opcode("RRC A", 8)(lambda self: self._rrc(Reg.A))
    opCB10 = opcode("RL B", 8)(lambda self: self._rl(Reg.B))
    opCB11 = opcode("RL C", 8)(lambda self: self._rl(Reg.C))
    opCB12 = opcode("RL D", 8)(lambda self: self._rl(Reg.D))
    opCB13 = opcode("RL E", 8)(lambda self: self._rl(Reg.E))
    opCB14 = opcode("RL H", 8)(lambda self: self._rl(Reg.H))
    opCB15 = opcode("RL L", 8)(lambda self: self._rl(Reg.L))
    opCB16 = opcode("RL [HL]", 8)(lambda self: self._rl(Reg.MEM_AT_HL))
    opCB17 = opcode("RL A", 8)(lambda self: self._rl(Reg.A))
    opCB18 = opcode("RR B", 8)(lambda self: self._rr(Reg.B))
    opCB19 = opcode("RR C", 8)(lambda self: self._rr(Reg.C))
    opCB1A = opcode("RR D", 8)(lambda self: self._rr(Reg.D))
    opCB1B = opcode("RR E", 8)(lambda self: self._rr(Reg.E))
    opCB1C = opcode("RR H", 8)(lambda self: self._rr(Reg.H))
    opCB1D = opcode("RR L", 8)(lambda self: self._rr(Reg.L))
    opCB1E = opcode("RR [HL]", 8)(lambda self: self._rr(Reg.MEM_AT_HL))
    opCB1F = opcode("RR A", 8)(lambda self: self._rr(Reg.A))
    opCB20 = opcode("SLA B", 8)(lambda self: self._sla(Reg.B))
    opCB21 = opcode("SLA C", 8)(lambda self: self._sla(Reg.C))
    opCB22 = opcode("SLA D", 8)(lambda self: self._sla(Reg.D))
    opCB23 = opcode("SLA E", 8)(lambda self: self._sla(Reg.E))
    opCB24 = opcode("SLA H", 8)(lambda self: self._sla(Reg.H))
    opCB25 = opcode("SLA L", 8)(lambda self: self._sla(Reg.L))
    opCB26 = opcode("SLA [HL]", 8)(lambda self: self._sla(Reg.MEM_AT_HL))
    opCB27 = opcode("SLA A", 8)(lambda self: self._sla(Reg.A))
    opCB28 = opcode("SRA B", 8)(lambda self: self._sra(Reg.B))
    opCB29 = opcode("SRA C", 8)(lambda self: self._sra(Reg.C))
    opCB2A = opcode("SRA D", 8)(lambda self: self._sra(Reg.D))
    opCB2B = opcode("SRA E", 8)(lambda self: self._sra(Reg.E))
    opCB2C = opcode("SRA H", 8)(lambda self: self._sra(Reg.H))
    opCB2D = opcode("SRA L", 8)(lambda self: self._sra(Reg.L))
    opCB2E = opcode("SRA [HL]", 8)(lambda self: self._sra(Reg.MEM_AT_HL))
    opCB2F = opcode("SRA A", 8)(lambda self: self._sra(Reg.A))
    opCB30 = opcode("SWAP B", 8)(lambda self: self._swap(Reg.B))
    opCB31 = opcode("SWAP C", 8)(lambda self: self._swap(Reg.C))
    opCB32 = opcode("SWAP D", 8)(lambda self: self._swap(Reg.D))
    opCB33 = opcode("SWAP E", 8)(lambda self: self._swap(Reg.E))
    opCB34 = opcode("SWAP H", 8)(lambda self: self._swap(Reg.H))
    opCB35 = opcode("SWAP L", 8)(lambda self: self._swap(Reg.L))
    opCB36 = opcode("SWAP [HL]", 8)(lambda self: self._swap(Reg.MEM_AT_HL))
    opCB37 = opcode("SWAP A", 8)(lambda self: self._swap(Reg.A))
    opCB38 = opcode("SRL B", 8)(lambda self: self._srl(Reg.B))
    opCB39 = opcode("SRL C", 8)(lambda self: self._srl(Reg.C))
    opCB3A = opcode("SRL D", 8)(lambda self: self._srl(Reg.D))
    opCB3B = opcode("SRL E", 8)(lambda self: self._srl(Reg.E))
    opCB3C = opcode("SRL H", 8)(lambda self: self._srl(Reg.H))
    opCB3D = opcode("SRL L", 8)(lambda self: self._srl(Reg.L))
    opCB3E = opcode("SRL [HL]", 8)(lambda self: self._srl(Reg.MEM_AT_HL))
    opCB3F = opcode("SRL A", 8)(lambda self: self._srl(Reg.A))
    opCB40 = opcode("BIT 0,B", 8)(lambda self: self._bit(Reg.B, 0))
    opCB41 = opcode("BIT 0,C", 8)(lambda self: self._bit(Reg.C, 0))
    opCB42 = opcode("BIT 0,D", 8)(lambda self: self._bit(Reg.D, 0))
    opCB43 = opcode("BIT 0,E", 8)(lambda self: self._bit(Reg.E, 0))
    opCB44 = opcode("BIT 0,H", 8)(lambda self: self._bit(Reg.H, 0))
    opCB45 = opcode("BIT 0,L", 8)(lambda self: self._bit(Reg.L, 0))
    opCB46 = opcode("BIT 0,[HL]", 8)(lambda self: self._bit(Reg.MEM_AT_HL, 0))
    opCB47 = opcode("BIT 0,A", 8)(lambda self: self._bit(Reg.A, 0))
    opCB48 = opcode("BIT 1,B", 8)(lambda self: self._bit(Reg.B, 1))
    opCB49 = opcode("BIT 1,C", 8)(lambda self: self._bit(Reg.C, 1))
    opCB4A = opcode("BIT 1,D", 8)(lambda self: self._bit(Reg.D, 1))
    opCB4B = opcode("BIT 1,E", 8)(lambda self: self._bit(Reg.E, 1))
    opCB4C = opcode("BIT 1,H", 8)(lambda self: self._bit(Reg.H, 1))
    opCB4D = opcode("BIT 1,L", 8)(lambda self: self._bit(Reg.L, 1))
    opCB4E = opcode("BIT 1,[HL]", 8)(lambda self: self._bit(Reg.MEM_AT_HL, 1))
    opCB4F = opcode("BIT 1,A", 8)(lambda self: self._bit(Reg.A, 1))
    opCB50 = opcode("BIT 2,B", 8)(lambda self: self._bit(Reg.B, 2))
    opCB51 = opcode("BIT 2,C", 8)(lambda self: self._bit(Reg.C, 2))
    opCB52 = opcode("BIT 2,D", 8)(lambda self: self._bit(Reg.D, 2))
    opCB53 = opcode("BIT 2,E", 8)(lambda self: self._bit(Reg.E, 2))
    opCB54 = opcode("BIT 2,H", 8)(lambda self: self._bit(Reg.H, 2))
    opCB55 = opcode("BIT 2,L", 8)(lambda self: self._bit(Reg.L, 2))
    opCB56 = opcode("BIT 2,[HL]", 8)(lambda self: self._bit(Reg.MEM_AT_HL, 2))
    opCB57 = opcode("BIT 2,A", 8)(lambda self: self._bit(Reg.A, 2))
    opCB58 = opcode("BIT 3,B", 8)(lambda self: self._bit(Reg.B, 3))
    opCB59 = opcode("BIT 3,C", 8)(lambda self: self._bit(Reg.C, 3))
    opCB5A = opcode("BIT 3,D", 8)(lambda self: self._bit(Reg.D, 3))
    opCB5B = opcode("BIT 3,E", 8)(lambda self: self._bit(Reg.E, 3))
    opCB5C = opcode("BIT 3,H", 8)(lambda self: self._bit(Reg.H, 3))
    opCB5D = opcode("BIT 3,L", 8)(lambda self: self._bit(Reg.L, 3))
    opCB5E = opcode("BIT 3,[HL]", 8)(lambda self: self._bit(Reg.MEM_AT_HL, 3))
    opCB5F = opcode("BIT 3,A", 8)(lambda self: self._bit(Reg.A, 3))
    opCB60 = opcode("BIT 4,B", 8)(lambda self: self._bit(Reg.B, 4))
    opCB61 = opcode("BIT 4,C", 8)(lambda self: self._bit(Reg.C, 4))
    opCB62 = opcode("BIT 4,D", 8)(lambda self: self._bit(Reg.D, 4))
    opCB63 = opcode("BIT 4,E", 8)(lambda self: self._bit(Reg.E, 4))
    opCB64 = opcode("BIT 4,H", 8)(lambda self: self._bit(Reg.H, 4))
    opCB65 = opcode("BIT 4,L", 8)(lambda self: self._bit(Reg.L, 4))
    opCB66 = opcode("BIT 4,[HL]", 8)(lambda self: self._bit(Reg.MEM_AT_HL, 4))
    opCB67 = opcode("BIT 4,A", 8)(lambda self: self._bit(Reg.A, 4))
    opCB68 = opcode("BIT 5,B", 8)(lambda self: self._bit(Reg.B, 5))
    opCB69 = opcode("BIT 5,C", 8)(lambda self: self._bit(Reg.C, 5))
    opCB6A = opcode("BIT 5,D", 8)(lambda self: self._bit(Reg.D, 5))
    opCB6B = opcode("BIT 5,E", 8)(lambda self: self._bit(Reg.E, 5))
    opCB6C = opcode("BIT 5,H", 8)(lambda self: self._bit(Reg.H, 5))
    opCB6D = opcode("BIT 5,L", 8)(lambda self: self._bit(Reg.L, 5))
    opCB6E = opcode("BIT 5,[HL]", 8)(lambda self: self._bit(Reg.MEM_AT_HL, 5))
    opCB6F = opcode("BIT 5,A", 8)(lambda self: self._bit(Reg.A, 5))
    opCB70 = opcode("BIT 6,B", 8)(lambda self: self._bit(Reg.B, 6))
    opCB71 = opcode("BIT 6,C", 8)(lambda self: self._bit(Reg.C, 6))
    opCB72 = opcode("BIT 6,D", 8)(lambda self: self._bit(Reg.D, 6))
    opCB73 = opcode("BIT 6,E", 8)(lambda self: self._bit(Reg.E, 6))
    opCB74 = opcode("BIT 6,H", 8)(lambda self: self._bit(Reg.H, 6))
    opCB75 = opcode("BIT 6,L", 8)(lambda self: self._bit(Reg.L, 6))
    opCB76 = opcode("BIT 6,[HL]", 8)(lambda self: self._bit(Reg.MEM_AT_HL, 6))
    opCB77 = opcode("BIT 6,A", 8)(lambda self: self._bit(Reg.A, 6))
    opCB78 = opcode("BIT 7,B", 8)(lambda self: self._bit(Reg.B, 7))
    opCB79 = opcode("BIT 7,C", 8)(lambda self: self._bit(Reg.C, 7))
    opCB7A = opcode("BIT 7,D", 8)(lambda self: self._bit(Reg.D, 7))
    opCB7B = opcode("BIT 7,E", 8)(lambda self: self._bit(Reg.E, 7))
    opCB7C = opcode("BIT 7,H", 8)(lambda self: self._bit(Reg.H, 7))
    opCB7D = opcode("BIT 7,L", 8)(lambda self: self._bit(Reg.L, 7))
    opCB7E = opcode("BIT 7,[HL]", 8)(lambda self: self._bit(Reg.MEM_AT_HL, 7))
    opCB7F = opcode("BIT 7,A", 8)(lambda self: self._bit(Reg.A, 7))
    opCB80 = opcode("RES 0,B", 8)(lambda self: self._res(Reg.B, 0))
    opCB81 = opcode("RES 0,C", 8)(lambda self: self._res(Reg.C, 0))
    opCB82 = opcode("RES 0,D", 8)(lambda self: self._res(Reg.D, 0))
    opCB83 = opcode("RES 0,E", 8)(lambda self: self._res(Reg.E, 0))
    opCB84 = opcode("RES 0,H", 8)(lambda self: self._res(Reg.H, 0))
    opCB85 = opcode("RES 0,L", 8)(lambda self: self._res(Reg.L, 0))
    opCB86 = opcode("RES 0,[HL]", 8)(lambda self: self._res(Reg.MEM_AT_HL, 0))
    opCB87 = opcode("RES 0,A", 8)(lambda self: self._res(Reg.A, 0))
    opCB88 = opcode("RES 1,B", 8)(lambda self: self._res(Reg.B, 1))
    opCB89 = opcode("RES 1,C", 8)(lambda self: self._res(Reg.C, 1))
    opCB8A = opcode("RES 1,D", 8)(lambda self: self._res(Reg.D, 1))
    opCB8B = opcode("RES 1,E", 8)(lambda self: self._res(Reg.E, 1))
    opCB8C = opcode("RES 1,H", 8)(lambda self: self._res(Reg.H, 1))
    opCB8D = opcode("RES 1,L", 8)(lambda self: self._res(Reg.L, 1))
    opCB8E = opcode("RES 1,[HL]", 8)(lambda self: self._res(Reg.MEM_AT_HL, 1))
    opCB8F = opcode("RES 1,A", 8)(lambda self: self._res(Reg.A, 1))
    opCB90 = opcode("RES 2,B", 8)(lambda self: self._res(Reg.B, 2))
    opCB91 = opcode("RES 2,C", 8)(lambda self: self._res(Reg.C, 2))
    opCB92 = opcode("RES 2,D", 8)(lambda self: self._res(Reg.D, 2))
    opCB93 = opcode("RES 2,E", 8)(lambda self: self._res(Reg.E, 2))
    opCB94 = opcode("RES 2,H", 8)(lambda self: self._res(Reg.H, 2))
    opCB95 = opcode("RES 2,L", 8)(lambda self: self._res(Reg.L, 2))
    opCB96 = opcode("RES 2,[HL]", 8)(lambda self: self._res(Reg.MEM_AT_HL, 2))
    opCB97 = opcode("RES 2,A", 8)(lambda self: self._res(Reg.A, 2))
    opCB98 = opcode("RES 3,B", 8)(lambda self: self._res(Reg.B, 3))
    opCB99 = opcode("RES 3,C", 8)(lambda self: self._res(Reg.C, 3))
    opCB9A = opcode("RES 3,D", 8)(lambda self: self._res(Reg.D, 3))
    opCB9B = opcode("RES 3,E", 8)(lambda self: self._res(Reg.E, 3))
    opCB9C = opcode("RES 3,H", 8)(lambda self: self._res(Reg.H, 3))
    opCB9D = opcode("RES 3,L", 8)(lambda self: self._res(Reg.L, 3))
    opCB9E = opcode("RES 3,[HL]", 8)(lambda self: self._res(Reg.MEM_AT_HL, 3))
    opCB9F = opcode("RES 3,A", 8)(lambda self: self._res(Reg.A, 3))
    opCBA0 = opcode("RES 4,B", 8)(lambda self: self._res(Reg.B, 4))
    opCBA1 = opcode("RES 4,C", 8)(lambda self: self._res(Reg.C, 4))
    opCBA2 = opcode("RES 4,D", 8)(lambda self: self._res(Reg.D, 4))
    opCBA3 = opcode("RES 4,E", 8)(lambda self: self._res(Reg.E, 4))
    opCBA4 = opcode("RES 4,H", 8)(lambda self: self._res(Reg.H, 4))
    opCBA5 = opcode("RES 4,L", 8)(lambda self: self._res(Reg.L, 4))
    opCBA6 = opcode("RES 4,[HL]", 8)(lambda self: self._res(Reg.MEM_AT_HL, 4))
    opCBA7 = opcode("RES 4,A", 8)(lambda self: self._res(Reg.A, 4))
    opCBA8 = opcode("RES 5,B", 8)(lambda self: self._res(Reg.B, 5))
    opCBA9 = opcode("RES 5,C", 8)(lambda self: self._res(Reg.C, 5))
    opCBAA = opcode("RES 5,D", 8)(lambda self: self._res(Reg.D, 5))
    opCBAB = opcode("RES 5,E", 8)(lambda self: self._res(Reg.E, 5))
    opCBAC = opcode("RES 5,H", 8)(lambda self: self._res(Reg.H, 5))
    opCBAD = opcode("RES 5,L", 8)(lambda self: self._res(Reg.L, 5))
    opCBAE = opcode("RES 5,[HL]", 8)(lambda self: self._res(Reg.MEM_AT_HL, 5))
    opCBAF = opcode("RES 5,A", 8)(lambda self: self._res(Reg.A, 5))
    opCBB0 = opcode("RES 6,B", 8)(lambda self: self._res(Reg.B, 6))
    opCBB1 = opcode("RES 6,C", 8)(lambda self: self._res(Reg.C, 6))
    opCBB2 = opcode("RES 6,D", 8)(lambda self: self._res(Reg.D, 6))
    opCBB3 = opcode("RES 6,E", 8)(lambda self: self._res(Reg.E, 6))
    opCBB4 = opcode("RES 6,H", 8)(lambda self: self._res(Reg.H, 6))
    opCBB5 = opcode("RES 6,L", 8)(lambda self: self._res(Reg.L, 6))
    opCBB6 = opcode("RES 6,[HL]", 8)(lambda self: self._res(Reg.MEM_AT_HL, 6))
    opCBB7 = opcode("RES 6,A", 8)(lambda self: self._res(Reg.A, 6))
    opCBB8 = opcode("RES 7,B", 8)(lambda self: self._res(Reg.B, 7))
    opCBB9 = opcode("RES 7,C", 8)(lambda self: self._res(Reg.C, 7))
    opCBBA = opcode("RES 7,D", 8)(lambda self: self._res(Reg.D, 7))
    opCBBB = opcode("RES 7,E", 8)(lambda self: self._res(Reg.E, 7))
    opCBBC = opcode("RES 7,H", 8)(lambda self: self._res(Reg.H, 7))
    opCBBD = opcode("RES 7,L", 8)(lambda self: self._res(Reg.L, 7))
    opCBBE = opcode("RES 7,[HL]", 8)(lambda self: self._res(Reg.MEM_AT_HL, 7))
    opCBBF = opcode("RES 7,A", 8)(lambda self: self._res(Reg.A, 7))
    opCBC0 = opcode("SET 0,B", 8)(lambda self: self._set(Reg.B, 0))
    opCBC1 = opcode("SET 0,C", 8)(lambda self: self._set(Reg.C, 0))
    opCBC2 = opcode("SET 0,D", 8)(lambda self: self._set(Reg.D, 0))
    opCBC3 = opcode("SET 0,E", 8)(lambda self: self._set(Reg.E, 0))
    opCBC4 = opcode("SET 0,H", 8)(lambda self: self._set(Reg.H, 0))
    opCBC5 = opcode("SET 0,L", 8)(lambda self: self._set(Reg.L, 0))
    opCBC6 = opcode("SET 0,[HL]", 8)(lambda self: self._set(Reg.MEM_AT_HL, 0))
    opCBC7 = opcode("SET 0,A", 8)(lambda self: self._set(Reg.A, 0))
    opCBC8 = opcode("SET 1,B", 8)(lambda self: self._set(Reg.B, 1))
    opCBC9 = opcode("SET 1,C", 8)(lambda self: self._set(Reg.C, 1))
    opCBCA = opcode("SET 1,D", 8)(lambda self: self._set(Reg.D, 1))
    opCBCB = opcode("SET 1,E", 8)(lambda self: self._set(Reg.E, 1))
    opCBCC = opcode("SET 1,H", 8)(lambda self: self._set(Reg.H, 1))
    opCBCD = opcode("SET 1,L", 8)(lambda self: self._set(Reg.L, 1))
    opCBCE = opcode("SET 1,[HL]", 8)(lambda self: self._set(Reg.MEM_AT_HL, 1))
    opCBCF = opcode("SET 1,A", 8)(lambda self: self._set(Reg.A, 1))
    opCBD0 = opcode("SET 2,B", 8)(lambda self: self._set(Reg.B, 2))
    opCBD1 = opcode("SET 2,C", 8)(lambda self: self._set(Reg.C, 2))
    opCBD2 = opcode("SET 2,D", 8)(lambda self: self._set(Reg.D, 2))
    opCBD3 = opcode("SET 2,E", 8)(lambda self: self._set(Reg.E, 2))
    opCBD4 = opcode("SET 2,H", 8)(lambda self: self._set(Reg.H, 2))
    opCBD5 = opcode("SET 2,L", 8)(lambda self: self._set(Reg.L, 2))
    opCBD6 = opcode("SET 2,[HL]", 8)(lambda self: self._set(Reg.MEM_AT_HL, 2))
    opCBD7 = opcode("SET 2,A", 8)(lambda self: self._set(Reg.A, 2))
    opCBD8 = opcode("SET 3,B", 8)(lambda self: self._set(Reg.B, 3))
    opCBD9 = opcode("SET 3,C", 8)(lambda self: self._set(Reg.C, 3))
    opCBDA = opcode("SET 3,D", 8)(lambda self: self._set(Reg.D, 3))
    opCBDB = opcode("SET 3,E", 8)(lambda self: self._set(Reg.E, 3))
    opCBDC = opcode("SET 3,H", 8)(lambda self: self._set(Reg.H, 3))
    opCBDD = opcode("SET 3,L", 8)(lambda self: self._set(Reg.L, 3))
    opCBDE = opcode("SET 3,[HL]", 8)(lambda self: self._set(Reg.MEM_AT_HL, 3))
    opCBDF = opcode("SET 3,A", 8)(lambda self: self._set(Reg.A, 3))
    opCBE0 = opcode("SET 4,B", 8)(lambda self: self._set(Reg.B, 4))
    opCBE1 = opcode("SET 4,C", 8)(lambda self: self._set(Reg.C, 4))
    opCBE2 = opcode("SET 4,D", 8)(lambda self: self._set(Reg.D, 4))
    opCBE3 = opcode("SET 4,E", 8)(lambda self: self._set(Reg.E, 4))
    opCBE4 = opcode("SET 4,H", 8)(lambda self: self._set(Reg.H, 4))
    opCBE5 = opcode("SET 4,L", 8)(lambda self: self._set(Reg.L, 4))
    opCBE6 = opcode("SET 4,[HL]", 8)(lambda self: self._set(Reg.MEM_AT_HL, 4))
    opCBE7 = opcode("SET 4,A", 8)(lambda self: self._set(Reg.A, 4))
    opCBE8 = opcode("SET 5,B", 8)(lambda self: self._set(Reg.B, 5))
    opCBE9 = opcode("SET 5,C", 8)(lambda self: self._set(Reg.C, 5))
    opCBEA = opcode("SET 5,D", 8)(lambda self: self._set(Reg.D, 5))
    opCBEB = opcode("SET 5,E", 8)(lambda self: self._set(Reg.E, 5))
    opCBEC = opcode("SET 5,H", 8)(lambda self: self._set(Reg.H, 5))
    opCBED = opcode("SET 5,L", 8)(lambda self: self._set(Reg.L, 5))
    opCBEE = opcode("SET 5,[HL]", 8)(lambda self: self._set(Reg.MEM_AT_HL, 5))
    opCBEF = opcode("SET 5,A", 8)(lambda self: self._set(Reg.A, 5))
    opCBF0 = opcode("SET 6,B", 8)(lambda self: self._set(Reg.B, 6))
    opCBF1 = opcode("SET 6,C", 8)(lambda self: self._set(Reg.C, 6))
    opCBF2 = opcode("SET 6,D", 8)(lambda self: self._set(Reg.D, 6))
    opCBF3 = opcode("SET 6,E", 8)(lambda self: self._set(Reg.E, 6))
    opCBF4 = opcode("SET 6,H", 8)(lambda self: self._set(Reg.H, 6))
    opCBF5 = opcode("SET 6,L", 8)(lambda self: self._set(Reg.L, 6))
    opCBF6 = opcode("SET 6,[HL]", 8)(lambda self: self._set(Reg.MEM_AT_HL, 6))
    opCBF7 = opcode("SET 6,A", 8)(lambda self: self._set(Reg.A, 6))
    opCBF8 = opcode("SET 7,B", 8)(lambda self: self._set(Reg.B, 7))
    opCBF9 = opcode("SET 7,C", 8)(lambda self: self._set(Reg.C, 7))
    opCBFA = opcode("SET 7,D", 8)(lambda self: self._set(Reg.D, 7))
    opCBFB = opcode("SET 7,E", 8)(lambda self: self._set(Reg.E, 7))
    opCBFC = opcode("SET 7,H", 8)(lambda self: self._set(Reg.H, 7))
    opCBFD = opcode("SET 7,L", 8)(lambda self: self._set(Reg.L, 7))
    opCBFE = opcode("SET 7,[HL]", 8)(lambda self: self._set(Reg.MEM_AT_HL, 7))
    opCBFF = opcode("SET 7,A", 8)(lambda self: self._set(Reg.A, 7))

    # </editor-fold>

    # <editor-fold description="3.3.8 Jumps">
    # ===================================
    # 1. JP nn
    @opcode("JP nn", 12, "H")
    def opC3(self, nn):
        self.PC = nn

    # ===================================
    # 2. JP cc,nn
    # Absolute jump if given flag is not set / set
    @opcode("JP NZ,n", 12, "H")
    def opC2(self, n):
        if not self.FLAG_Z:
            self.PC = n

    @opcode("JP Z,n", 12, "H")
    def opCA(self, n):
        if self.FLAG_Z:
            self.PC = n

    @opcode("JP NC,n", 12, "H")
    def opD2(self, n):
        if not self.FLAG_C:
            self.PC = n

    @opcode("JP C,n", 12, "H")
    def opDA(self, n):
        if self.FLAG_C:
            self.PC = n

    # ===================================
    # 3. JP [HL]
    @opcode("JP [HL]", 4)
    def opE9(self):
        self.PC = self.ram[self.HL]

    # ===================================
    # 4. JR n
    @opcode("JR n", 8, "b")
    def op18(self, n):
        self.PC += n

    # ===================================
    # 5. JR cc,n
    # Relative jump if given flag is not set / set
    @opcode("JR NZ,n", 8, "b")
    def op20(self, n):
        if not self.FLAG_Z:
            self.PC += n

    @opcode("JR Z,n", 8, "b")
    def op28(self, n):
        if self.FLAG_Z:
            self.PC += n

    @opcode("JR NC,n", 8, "b")
    def op30(self, n):
        if not self.FLAG_C:
            self.PC += n

    @opcode("JR C,n", 8, "b")
    def op38(self, n):
        if self.FLAG_C:
            self.PC += n
    # </editor-fold>

    # <editor-fold description="3.3.9 Calls">
    # ===================================
    # 1. CALL nn
    @opcode("CALL nn", 12, "H")
    def opCD(self, nn):
        self._push16(Reg.PC)
        self.PC = nn

    # ===================================
    # 2. CALL cc,nn
    # Absolute call if given flag is not set / set
    @opcode("CALL NZ,nn", 12, "H")
    def opC4(self, n):
        if not self.FLAG_Z:
            self._push16(Reg.PC)
            self.PC = n

    @opcode("CALL Z,nn", 12, "H")
    def opCC(self, n):
        if self.FLAG_Z:
            self._push16(Reg.PC)
            self.PC = n

    @opcode("CALL NC,nn", 12, "H")
    def opD4(self, n):
        if not self.FLAG_C:
            self._push16(Reg.PC)
            self.PC = n

    @opcode("CALL C,nn", 12, "H")
    def opDC(self, n):
        if self.FLAG_C:
            self._push16(Reg.PC)
            self.PC = n

    # </editor-fold>

    # <editor-fold description="3.3.10 Restarts">
    # ===================================
    # 1. RST n
    # Push present address onto stack.
    # Jump to address $0000 + n.
    # n = $00,$08,$10,$18,$20,$28,$30,$38
    def _rst(self, val):
        self._push16(Reg.PC)
        self.PC = val

    opC7 = opcode("RST 00", 32)(lambda self: self._rst(0x00))
    opCF = opcode("RST 08", 32)(lambda self: self._rst(0x08))
    opD7 = opcode("RST 10", 32)(lambda self: self._rst(0x10))
    opDF = opcode("RST 18", 32)(lambda self: self._rst(0x18))
    opE7 = opcode("RST 20", 32)(lambda self: self._rst(0x20))
    opEF = opcode("RST 28", 32)(lambda self: self._rst(0x28))
    opF7 = opcode("RST 30", 32)(lambda self: self._rst(0x30))
    opFF = opcode("RST 38", 32)(lambda self: self._rst(0x38))
    # </editor-fold>

    # <editor-fold description="3.3.11 Returns">

    # ===================================
    # 1. RET
    opC9 = opcode("RET", 8)(lambda self: self._pop16(Reg.PC))

    # ===================================
    # 2. RET cc
    @opcode("RET NZ", 8)
    def opC0(self):
        if not self.FLAG_Z:
            self._pop16(Reg.PC)

    @opcode("RET Z", 8)
    def opC8(self):
        if self.FLAG_Z:
            self._pop16(Reg.PC)

    @opcode("RET NC", 8)
    def opD0(self):
        if not self.FLAG_C:
            self._pop16(Reg.PC)

    @opcode("RET C", 8)
    def opD8(self):
        if self.FLAG_C:
            self._pop16(Reg.PC)

    # ===================================
    # 3. RETI
    @opcode("RETI", 8)
    def opD9(self):
        self._pop16(Reg.PC)
        self.interrupts = True

    # </editor-fold>

    # =================================================================
    # STACK
    def _push16(self, reg: Reg):
        """
        >>> c = CPU()
        >>> c.B = 1234
        >>> c._push16(Reg.B)
        >>> c._pop16(Reg.A)
        >>> c.A
        1234
        """
        val = getattr(self, reg.value)
        self.ram[self.SP - 1] = (val & 0xFF00) >> 8
        self.ram[self.SP] = val & 0xFF
        self.SP -= 2
        # print("Pushing %r to stack at %r [%r]" % (val, self.SP, self.ram[-10:]))

    def _pop16(self, reg: Reg):
        val = (self.ram[self.SP+1] << 8) | self.ram[self.SP+2]
        # print("Set %r to %r from %r, %r" % (reg, val, self.SP, self.ram[-10:]))
        setattr(self, reg.value, val)
        self.SP += 2
