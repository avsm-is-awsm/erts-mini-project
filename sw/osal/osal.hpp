// osal.hpp -- small OS abstraction layer (SPEC 10.3): Clock, Timer, Mutex, Queue<T,N>, Thread.
//
// Adapter pattern: the application only uses this interface.  Implementations:
//   osal_baremetal.cpp  Zynq standalone BSP (XTime global timer, single-threaded, no locks)
//   osal_host.cpp       PC host build for unit tests (std::chrono)
// A course-provided OS package (A4) would be wrapped by a third implementation.
#ifndef DK1_OSAL_HPP
#define DK1_OSAL_HPP

#include <stddef.h>
#include <stdint.h>

namespace osal {

// Monotonic tick counter (bare metal: Cortex-A9 global timer at CPU_CLK/2 = 333.33 MHz).
struct Clock {
    static uint64_t now();            // ticks
    static uint64_t ticks_per_sec();
    static double to_seconds(uint64_t ticks) { return (double)ticks / (double)ticks_per_sec(); }
};

struct Timer {
    static void sleep_us(uint32_t us);
};

// Single-core bare metal: the "mutex" only documents critical sections (interrupts of the
// DMA driver are handled by polling in this design).
class Mutex {
public:
    void lock() {}
    void unlock() {}
};

class LockGuard {
public:
    explicit LockGuard(Mutex& m) : m_(m) { m_.lock(); }
    ~LockGuard() { m_.unlock(); }
private:
    LockGuard(const LockGuard&);
    LockGuard& operator=(const LockGuard&);
    Mutex& m_;
};

// Bounded ring buffer, no dynamic allocation (Queuing pattern; used for DMA chunk scheduling).
template <typename T, size_t N>
class Queue {
public:
    Queue() : head_(0), tail_(0), count_(0) {}
    bool push(const T& v) {
        if (count_ == N) return false;
        buf_[tail_] = v;
        tail_ = (tail_ + 1) % N;
        ++count_;
        return true;
    }
    bool pop(T& v) {
        if (count_ == 0) return false;
        v = buf_[head_];
        head_ = (head_ + 1) % N;
        --count_;
        return true;
    }
    bool front(T& v) const {
        if (count_ == 0) return false;
        v = buf_[head_];
        return true;
    }
    size_t size() const { return count_; }
    bool empty() const { return count_ == 0; }
    bool full() const { return count_ == N; }
    static size_t capacity() { return N; }
private:
    T buf_[N];
    size_t head_, tail_, count_;
};

// Bare metal has one thread of control; Thread::run executes the entry inline.
class Thread {
public:
    typedef void (*Entry)(void*);
    static void run(Entry e, void* arg) { e(arg); }
};

}  // namespace osal

#endif
