// board.hpp -- Zynq standalone implementations of the platform interfaces (board.cpp).
#ifndef DK1_BOARD_HPP
#define DK1_BOARD_HPP

#include "ff.h"
#include "platform.hpp"
#include "xaxidma.h"

namespace app {

class XilRegBus : public IRegBus {
public:
    explicit XilRegBus(uint32_t base) : base_(base) {}
    uint32_t read32(uint32_t off);
    void write32(uint32_t off, uint32_t v);
private:
    uint32_t base_;
};

class XilDma {
public:
    bool init(uint16_t device_id);
    void reset();
    XAxiDma* raw() { return &dma_; }
private:
    XAxiDma dma_;
};

class XilDmaChannel : public IDmaChannel {
public:
    // owns_reset: this channel resets the whole DMA engine in reset() (one owner per engine)
    XilDmaChannel(XilDma& d, int dir, bool owns_reset) : dma_(d), dir_(dir), owns_(owns_reset), started_(false) {}
    bool start(uint64_t addr, uint32_t len);
    bool busy();
    uint32_t transferred();
    void reset();
    bool error();
private:
    XilDma& dma_;
    int dir_;
    bool owns_;
    bool started_;
};

class XilCache : public ICache {
public:
    void flush(const void* p, uint32_t len);
    void invalidate(const void* p, uint32_t len);
};

class FatFsFileSystem : public IFileSystem {
public:
    bool mount();
    long read(const char* path, void* dst, uint32_t max);
    bool write(const char* path, const void* src, uint32_t len);
    bool list(const char* dir, void (*cb)(const char*, uint32_t, void*), void* ctx);
private:
    FATFS fs_;
};

}  // namespace app

#endif
