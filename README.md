# pygb
Python GameBoy Emulator

My first ever attempt at an emulator, it passes the core-cpu test suite
(ie all the CPU instructions are implemented correctly, barring some really
weird hardware bugs), but I/O is very incomplete.

In particular RAM is just implemented as a flat array, when in reality
there's a memory controller sitting between the CPU and other hardware
which is supposed to be doing things like bank switching.
