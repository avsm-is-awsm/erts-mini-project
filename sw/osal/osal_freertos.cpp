// osal_freertos.cpp -- OSAL on the Zynq-7000 FreeRTOS 9.0.1 BSP (freertos901_xilinx).
#include "osal.hpp"

#include "FreeRTOS.h"
#include "semphr.h"
#include "task.h"
#include "xtime_l.h"

namespace osal {

uint64_t Clock::now() {
    XTime t;
    XTime_GetTime(&t);  // Cortex-A9 global timer, CPU/2, shared by both cores and all tasks
    return (uint64_t)t;
}

uint64_t Clock::ticks_per_sec() { return (uint64_t)COUNTS_PER_SECOND; }

void Timer::sleep_us(uint32_t us) {
    const TickType_t t = (TickType_t)(us / (1000000u / configTICK_RATE_HZ));
    if (t > 0 && xTaskGetSchedulerState() == taskSCHEDULER_RUNNING) {
        vTaskDelay(t);  // block: lower-priority tasks run meanwhile
        return;
    }
    const uint64_t end = Clock::now() + (uint64_t)us * Clock::ticks_per_sec() / 1000000u;
    while (Clock::now() < end) {
    }
}

Mutex::Mutex() : h_(xSemaphoreCreateMutex()) {}
Mutex::~Mutex() {
    if (h_) vSemaphoreDelete((SemaphoreHandle_t)h_);
}
void Mutex::lock() {
    if (h_ && xTaskGetSchedulerState() == taskSCHEDULER_RUNNING) xSemaphoreTake((SemaphoreHandle_t)h_, portMAX_DELAY);
}
void Mutex::unlock() {
    if (h_ && xTaskGetSchedulerState() == taskSCHEDULER_RUNNING) xSemaphoreGive((SemaphoreHandle_t)h_);
}

}  // namespace osal
