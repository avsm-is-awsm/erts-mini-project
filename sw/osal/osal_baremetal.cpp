// osal_baremetal.cpp -- OSAL on the Zynq-7000 standalone BSP.
#include "osal.hpp"

#include "sleep.h"
#include "xtime_l.h"

namespace osal {

uint64_t Clock::now() {
    XTime t;
    XTime_GetTime(&t);
    return (uint64_t)t;
}

uint64_t Clock::ticks_per_sec() { return (uint64_t)COUNTS_PER_SECOND; }

void Timer::sleep_us(uint32_t us) { usleep(us); }

}  // namespace osal
