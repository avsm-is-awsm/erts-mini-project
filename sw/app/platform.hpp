// platform.hpp -- hardware-facing interfaces and the DDR memory map of the PS application.
// Everything that touches real hardware sits behind one of these interfaces so the session
// logic is unit-tested on the PC with mocks (sw/host_test).
#ifndef DK1_PLATFORM_HPP
#define DK1_PLATFORM_HPP

#include <stddef.h>
#include <stdint.h>

namespace app {

// ---------------------------------------------------------------- DDR map (original ZYBO, 512 MB)
// Code/heap/stack live below 32 MB (lscript default at 0x0010_0000).
enum : uint32_t {
    IN_BUF      = 0x02000000u, IN_BUF_SIZE  = 0x0C000000u,  // 192 MB = 6 M messages (D-01)
    ORD_HW_BUF  = 0x0E000000u, ORD_BUF_SIZE = 0x007FFFE0u,  // < 8 MB (one S2MM transfer)
    DBG_HW_BUF  = 0x0F000000u, DBG_BUF_SIZE = 0x03000000u,  // 48 MB = 1.5 M BBO records
    ORD_SW_BUF  = 0x12000000u,
    DBG_SW_BUF  = 0x13000000u,                               // ends 0x16000000
    SCHED_BUF   = 0x16000000u, SCHED_BUF_SIZE = 0x03000000u, // live replay schedule, 8 B/msg (ends 0x19000000)
    MAX_MSGS    = IN_BUF_SIZE / 32u,
    MAX_DBG_MSGS = DBG_BUF_SIZE / 32u - 1u,
    // AXI DMA v7.1 (Vivado 2017.2): 23-bit length register -> < 8 MB per transfer (D-29)
    DMA_CHUNK   = 0x00400000u                                // 4 MB per MM2S transfer (multiple of 32)
};

// ---------------------------------------------------------------- register access (Hardware Proxy)
class IRegBus {
public:
    virtual ~IRegBus() {}
    virtual uint32_t read32(uint32_t offset) = 0;
    virtual void write32(uint32_t offset, uint32_t value) = 0;
};

// ---------------------------------------------------------------- one AXI DMA direction
class IDmaChannel {
public:
    virtual ~IDmaChannel() {}
    virtual bool start(uint64_t addr, uint32_t len) = 0;  // simple-mode transfer
    virtual bool busy() = 0;
    virtual uint32_t transferred() = 0;                   // S2MM: bytes received (until TLAST)
    virtual void reset() = 0;
    virtual bool error() { return false; }                // DMA status error bits (decode/slave/internal)
};

// ---------------------------------------------------------------- data cache maintenance
class ICache {
public:
    virtual ~ICache() {}
    virtual void flush(const void* p, uint32_t len) = 0;       // before MM2S
    virtual void invalidate(const void* p, uint32_t len) = 0;  // after S2MM
};

// ---------------------------------------------------------------- SD card / files
class IFileSystem {
public:
    virtual ~IFileSystem() {}
    // read up to max bytes into dst; returns bytes read or -1
    virtual long read(const char* path, void* dst, uint32_t max) = 0;
    virtual bool write(const char* path, const void* src, uint32_t len) = 0;
    virtual bool list(const char* dir, void (*cb)(const char* name, uint32_t size, void* ctx),
                      void* ctx) = 0;
};

// Convert a fixed DDR address to a pointer (bare metal: identity; host tests: a mock arena).
uint8_t* ddr(uint32_t addr);

}  // namespace app

#endif
