// board.cpp -- Zynq standalone implementations of the platform interfaces:
//   XilRegBus (Xil_In32/Xil_Out32), XilDmaChannel (XAxiDma simple mode, polled),
//   XilCache (Xil_DCache*), FatFsFileSystem (xilffs), ddr() identity mapping.
#include <stdio.h>
#include <string.h>

#include "board.hpp"

#include "xaxidma.h"
#include "xil_cache.h"
#include "xil_io.h"
#include "xparameters.h"

namespace app {

uint8_t* ddr(uint32_t addr) { return (uint8_t*)(uintptr_t)addr; }

uint32_t XilRegBus::read32(uint32_t off) { return Xil_In32(base_ + off); }
void XilRegBus::write32(uint32_t off, uint32_t v) { Xil_Out32(base_ + off, v); }

// ---------------------------------------------------------------- DMA
bool XilDma::init(uint16_t device_id) {
    XAxiDma_Config* c = XAxiDma_LookupConfig(device_id);
    if (!c) return false;
    if (XAxiDma_CfgInitialize(&dma_, c) != XST_SUCCESS) return false;
    if (XAxiDma_HasSg(&dma_)) return false;  // simple mode only
    XAxiDma_IntrDisable(&dma_, XAXIDMA_IRQ_ALL_MASK, XAXIDMA_DEVICE_TO_DMA);
    XAxiDma_IntrDisable(&dma_, XAXIDMA_IRQ_ALL_MASK, XAXIDMA_DMA_TO_DEVICE);
    return true;
}

void XilDma::reset() {
    XAxiDma_Reset(&dma_);
    for (int i = 0; i < 100000 && !XAxiDma_ResetIsDone(&dma_); ++i) {}
    XAxiDma_IntrDisable(&dma_, XAXIDMA_IRQ_ALL_MASK, XAXIDMA_DEVICE_TO_DMA);
    XAxiDma_IntrDisable(&dma_, XAXIDMA_IRQ_ALL_MASK, XAXIDMA_DMA_TO_DEVICE);
}

bool XilDmaChannel::start(uint64_t addr, uint32_t len) {
    started_ = true;
    return XAxiDma_SimpleTransfer(dma_.raw(), (UINTPTR)addr, len, dir_) == XST_SUCCESS;
}

bool XilDmaChannel::busy() { return started_ && XAxiDma_Busy(dma_.raw(), dir_); }

uint32_t XilDmaChannel::transferred() {
    // S2MM: the length register holds the bytes actually received when the transfer ended
    // (TLAST); MM2S: the programmed length.
    const uint32_t base = (dir_ == XAXIDMA_DEVICE_TO_DMA) ? XAXIDMA_RX_OFFSET : XAXIDMA_TX_OFFSET;
    return XAxiDma_ReadReg(dma_.raw()->RegBase, base + XAXIDMA_BUFFLEN_OFFSET);
}

bool XilDmaChannel::error() {
    const uint32_t base = (dir_ == XAXIDMA_DEVICE_TO_DMA) ? XAXIDMA_RX_OFFSET : XAXIDMA_TX_OFFSET;
    return (XAxiDma_ReadReg(dma_.raw()->RegBase, base + XAXIDMA_SR_OFFSET) & XAXIDMA_ERR_ALL_MASK) != 0;
}

void XilDmaChannel::reset() {
    started_ = false;
    if (owns_) dma_.reset();  // XAxiDma_Reset resets both directions of the engine
}

// ---------------------------------------------------------------- cache
void XilCache::flush(const void* p, uint32_t len) { Xil_DCacheFlushRange((INTPTR)p, len); }
void XilCache::invalidate(const void* p, uint32_t len) { Xil_DCacheInvalidateRange((INTPTR)p, len); }

// ---------------------------------------------------------------- SD card (FatFs / xilffs)
bool FatFsFileSystem::mount() { return f_mount(&fs_, "0:/", 1) == FR_OK; }

long FatFsFileSystem::read(const char* path, void* dst, uint32_t max) {
    FIL f;
    if (f_open(&f, path, FA_READ) != FR_OK) return -1;
    uint8_t* p = (uint8_t*)dst;
    uint32_t total = 0;
    for (;;) {
        UINT got = 0;
        const UINT want = (max - total < 0x100000u) ? (UINT)(max - total) : 0x100000u;
        if (want == 0) break;
        if (f_read(&f, p + total, want, &got) != FR_OK) { f_close(&f); return -1; }
        total += got;
        if (got < want) break;
    }
    f_close(&f);
    return (long)total;
}

bool FatFsFileSystem::write(const char* path, const void* src, uint32_t len) {
    FIL f;
    if (f_open(&f, path, FA_WRITE | FA_CREATE_ALWAYS) != FR_OK) return false;
    const uint8_t* p = (const uint8_t*)src;
    uint32_t done = 0;
    while (done < len) {
        UINT put = 0;
        const UINT want = (len - done < 0x100000u) ? (UINT)(len - done) : 0x100000u;
        if (f_write(&f, p + done, want, &put) != FR_OK || put != want) { f_close(&f); return false; }
        done += put;
    }
    f_close(&f);
    return true;
}

bool FatFsFileSystem::list(const char* dir, void (*cb)(const char*, uint32_t, void*), void* ctx) {
    DIR d;
    FILINFO fi;
    memset(&fi, 0, sizeof fi);
#if _USE_LFN
    // FatFs R0.10b writes the long name through fi.lfname: it must point to a caller buffer,
    // or f_readdir writes to a random address (the board hung on `ls`, DECISIONS D-41).
    static TCHAR lfn[_MAX_LFN + 1];
    fi.lfname = lfn;
    fi.lfsize = sizeof lfn / sizeof lfn[0];
#endif
    if (f_opendir(&d, dir) != FR_OK) return false;
    for (;;) {
#if _USE_LFN
        lfn[0] = 0;
#endif
        if (f_readdir(&d, &fi) != FR_OK || !fi.fname[0]) break;
#if _USE_LFN
        const char* name = lfn[0] ? lfn : fi.fname;
#else
        const char* name = fi.fname;
#endif
        cb(name, (uint32_t)fi.fsize, ctx);
    }
    f_closedir(&d);
    return true;
}

}  // namespace app
