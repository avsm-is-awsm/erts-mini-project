// osal_host.cpp -- OSAL for the PC host build (unit tests).
#include <chrono>
#include <thread>

#include "osal.hpp"

namespace osal {

uint64_t Clock::now() {
    return (uint64_t)std::chrono::duration_cast<std::chrono::nanoseconds>(
               std::chrono::steady_clock::now().time_since_epoch()).count();
}

uint64_t Clock::ticks_per_sec() { return 1000000000ull; }

void Timer::sleep_us(uint32_t us) { std::this_thread::sleep_for(std::chrono::microseconds(us)); }

Mutex::Mutex() : h_(0) {}
Mutex::~Mutex() {}
void Mutex::lock() {}
void Mutex::unlock() {}

}  // namespace osal
