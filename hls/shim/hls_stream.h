// Minimal hls::stream shim for plain g++ builds (CI / no Vivado HLS installed).
// Only the subset used by t2t_top.cpp and the testbenches.  With Vivado/Vitis HLS the
// real header is picked up instead (the shim directory is only on the g++ include path).
#ifndef T2T_SHIM_HLS_STREAM_H
#define T2T_SHIM_HLS_STREAM_H

#include <cstdio>
#include <cstdlib>
#include <deque>

namespace hls {

template <typename T>
class stream {
public:
    stream() {}
    explicit stream(const char*) {}
    bool empty() const { return q_.empty(); }
    bool full() const { return false; }
    size_t size() const { return q_.size(); }
    void write(const T& v) { q_.push_back(v); }
    void operator<<(const T& v) { write(v); }
    T read() {
        if (q_.empty()) {
            std::fprintf(stderr, "hls::stream shim: read from empty stream\n");
            std::abort();
        }
        T v = q_.front();
        q_.pop_front();
        return v;
    }
    void operator>>(T& v) { v = read(); }
    bool read_nb(T& v) {
        if (q_.empty()) return false;
        v = read();
        return true;
    }
    bool write_nb(const T& v) {
        write(v);
        return true;
    }

private:
    stream(const stream&);
    stream& operator=(const stream&);
    std::deque<T> q_;
};

}  // namespace hls

#endif
