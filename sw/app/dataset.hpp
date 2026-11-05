// dataset.hpp -- DatasetLoader (SPEC 10.2): feed.bin + config.txt from the SD card into DDR.
#ifndef DK1_DATASET_HPP
#define DK1_DATASET_HPP

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../../hls/src/core/types.hpp"
#include "platform.hpp"

namespace app {

struct Dataset {
    char     dir[96];
    uint8_t* data;      // 64-byte aligned DDR buffer (IN_BUF)
    uint32_t n_msgs;
    t2t::Config cfg;
    bool     loaded;
};

// key=value lines (written next to config.json by ref/model.py Config.save)
inline bool parse_config_kv(const char* text, t2t::Config& c) {
    bool any = false;
    const char* p = text;
    while (*p) {
        char key[32];
        long long v = 0;
        const char* eq = strchr(p, '=');
        const char* nl = strchr(p, '\n');
        if (!nl) nl = p + strlen(p);
        if (eq && eq < nl && (size_t)(eq - p) < sizeof(key)) {
            memcpy(key, p, (size_t)(eq - p));
            key[eq - p] = 0;
            v = strtoll(eq + 1, 0, 10);
            any = true;
            if (!strcmp(key, "ctrl")) c.ctrl = (uint32_t)v;
            else if (!strcmp(key, "clip_qty")) c.clip_qty = (uint32_t)v;
            else if (!strcmp(key, "max_order_qty")) c.max_order_qty = (uint32_t)v;
            else if (!strcmp(key, "max_pos")) c.max_pos = (int32_t)v;
            else if (!strcmp(key, "collar")) c.collar = (uint32_t)v;
            else if (!strcmp(key, "min_gap_ns")) c.min_gap_ns = (uint32_t)v;
            else if (!strcmp(key, "max_orders")) c.max_orders = (uint32_t)v;
            else if (!strcmp(key, "lockout_ms")) c.lockout_ms = (uint32_t)v;
            else any = any;  // unknown keys ignored
        }
        p = (*nl) ? nl + 1 : nl;
    }
    return any;
}

class DatasetLoader {
public:
    explicit DatasetLoader(IFileSystem& fs) : fs_(fs) {}

    // Returns empty string on success, else an error message.
    const char* load(const char* dir, Dataset& ds) {
        ds.loaded = false;
        snprintf(ds.dir, sizeof(ds.dir), "%s", dir);
        char path[128];
        snprintf(path, sizeof(path), "%s/feed.bin", dir);
        ds.data = ddr(IN_BUF);
        long n = fs_.read(path, ds.data, IN_BUF_SIZE);
        if (n < 0) return "cannot read feed.bin";
        if (n == 0 || (n % 32) != 0) return "feed.bin size is not a positive multiple of 32";
        if ((uint32_t)n >= IN_BUF_SIZE) return "feed.bin larger than the DDR input buffer (6 M msgs)";
        ds.n_msgs = (uint32_t)(n / 32);
        ds.cfg = t2t::default_config();
        char kv[1024];
        snprintf(path, sizeof(path), "%s/config.txt", dir);
        long m = fs_.read(path, kv, sizeof(kv) - 1);
        if (m > 0) {
            kv[m] = 0;
            parse_config_kv(kv, ds.cfg);
        }
        ds.loaded = true;
        return "";
    }

private:
    IFileSystem& fs_;
};

}  // namespace app

#endif
