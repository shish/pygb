import pygame

VRAM_BASE = 0x8000
SCALE = 2


class LCD:
    def __init__(self, cpu):
        self.cpu = cpu
        self._game_only = True
        self.tiles = []

        pygame.init()
        if self._game_only:
            self.screen = pygame.display.set_mode((160 * SCALE, 144 * SCALE))
        else:
            self.screen = pygame.display.set_mode((512 * SCALE, 256 * SCALE))
        pygame.display.set_caption("SPYGB - " + cpu.cart.name.decode())
        self.clock = pygame.time.Clock()

    def update(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                print("Quitting")
                return False

        neon = [
            pygame.Color(255, 63, 63),
            pygame.Color(63, 255, 63),
            pygame.Color(63, 63, 255),
            pygame.Color(0, 0, 0),
        ]
        default = [
            pygame.Color(255, 255, 255),
            pygame.Color(192, 192, 192),
            pygame.Color(128, 128, 128),
            pygame.Color(0, 0, 0),
        ]
        available_colors = default

        bgp = [
            available_colors[(self.cpu.ram[0xFF47] >> 0) & 0x3],
            available_colors[(self.cpu.ram[0xFF47] >> 2) & 0x3],
            available_colors[(self.cpu.ram[0xFF47] >> 4) & 0x3],
            available_colors[(self.cpu.ram[0xFF47] >> 6) & 0x3],
        ]
        obp0 = [
            available_colors[(self.cpu.ram[0xFF48] >> 0) & 0x3],
            available_colors[(self.cpu.ram[0xFF48] >> 2) & 0x3],
            available_colors[(self.cpu.ram[0xFF48] >> 4) & 0x3],
            available_colors[(self.cpu.ram[0xFF48] >> 6) & 0x3],
        ]
        obp1 = [
            available_colors[(self.cpu.ram[0xFF49] >> 0) & 0x3],
            available_colors[(self.cpu.ram[0xFF49] >> 2) & 0x3],
            available_colors[(self.cpu.ram[0xFF49] >> 4) & 0x3],
            available_colors[(self.cpu.ram[0xFF49] >> 6) & 0x3],
        ]

        SCROLL_Y = self.cpu.ram[0xFF42]
        SCROLL_X = self.cpu.ram[0xFF43]
        # print("SCROLL ", SCROLL_X, SCROLL_Y)

        self.tiles = []
        for tile_id in range(0x200):
            self.tiles.append(self.get_tile(tile_id, bgp))

        # Display only valid area
        self.screen.fill(bgp[0])
        if self._game_only:
            for y in range(144 // 8):
                for x in range(160 // 8):
                    tile_id = self.cpu.ram[0x9800 + y * 32 + x]
                    self.screen.blit(self.tiles[tile_id], (x*8*SCALE - SCROLL_X*SCALE, y*8*SCALE - SCROLL_Y*SCALE))

        else:
            for y in range(256 // 8):
                for x in range(256 // 8):
                    tile_id = self.cpu.ram[0x9800 + y * 32 + x]
                    self.screen.blit(self.tiles[tile_id], (x*8*SCALE, y*8*SCALE))
            pygame.draw.rect(self.screen, pygame.Color(255, 0, 0), (SCROLL_X * SCALE, SCROLL_Y * SCALE, 160 * SCALE, 144 * SCALE), 1)
            for y in range(8):
                for x in range(32):
                    self.screen.blit(self.tiles[y*32+x], (256*SCALE + x*8*SCALE, y*8*SCALE))

        pygame.display.update()
        self.clock.tick(60)
        return True

    def get_tile(self, tile_id, pallette):
        tile = self.cpu.ram[VRAM_BASE + tile_id * 16: VRAM_BASE + (tile_id * 16) + 16]
        surf = pygame.Surface((8 * SCALE, 8 * SCALE))

        for y in range(8):
            for x in range(8):
                low_byte = tile[(y * 2)]
                high_byte = tile[(y * 2) + 1]
                low_bit = (low_byte >> (7-x)) & 0x1
                high_bit = (high_byte >> (7-x)) & 0x1
                px = (high_bit << 1) | low_bit
                surf.fill(pallette[px], ((x * SCALE, y * SCALE), (SCALE, SCALE)))

        return surf

    def close(self):
        pygame.quit()
