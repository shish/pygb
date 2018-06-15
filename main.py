#!/usr/bin/env python3

import sys
from typing import List
import time
from cart import Cart
from cpu import CPU, OpNotImplemented
from lcd import LCD


def info(cart):
    with open(cart, "rb") as fp:
        data = fp.read()
    cart = Cart(data)
    print(cart)
    # cpu = cpu.CPU(cart)
    # print("%d%% of instructions implemented" % (sum(op is not None for op in cpu.ops)/256*100))
    # for n, op in enumerate(cpu.ops):
    #     print("%02X %s" % (n, op.name if op else "-"))


def run(cart):
    with open(cart, "rb") as fp:
        data = fp.read()
    cart = Cart(data)
    cpu = CPU(cart)
    lcd = LCD(cpu)

    running = True
    clock = 0
    last_frame = time.time()
    while running:
        try:
            if not cpu.halt and not cpu.stop:
                clock += cpu.tick()
            else:
                clock += 4
            #if cpu.halt:
            #    print("CPU halted, waiting for interrupt")
            #    break
            #if cpu.stop:
            #    print("CPU stopped, waiting for button")
            #    break

        except OpNotImplemented as e:
            running = False
            # print(cpu)
            print(e, file=sys.stderr)
        except (Exception, KeyboardInterrupt) as e:
            running = False
            dump(cpu, str(e))

        # 4MHz / 60FPS ~= 70000 instructions per frame
        if clock > 70224 or clock > 1000:
            # print(last_frame - time.time())
            last_frame = time.time()
            clock = 0
            if not lcd.update():
                running = False
    # import collections
    # print(collections.Counter(cpu.ram[0x8000:0xA000]))
    # time.sleep(3)
    lcd.close()
    dump(cpu, "Safe exit")


def dump(cpu, err):
    print("Error: %s\nWriting details to crash.txt" % err)
    with open("crash.txt", "w") as fp:
        fp.write(str(err) + "\n\n")
        fp.write(str(cpu.debug_str) + "\n\n")
        fp.write(str(cpu) + "\n\n")
        for n in range(0x0000, 0xFFFF, 0x0010):
            fp.write(("%04X :" + (" %02X" * 16) + "\n") % (n, *cpu.ram[n:n + 0x0010]))


def main(args: List[str]) -> int:
    if args[1] == "info":
        info(args[2])

    if args[1] == "run":
        run(args[2])

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
