// proto_roundtrip.cpp -- reads a Python-written record file, decodes every record into the
// C++ POD struct for its kind, re-encodes it to 4 x uint64 beats and writes it back.
//   proto_roundtrip <kind: msg|ord|end|bbo|auto> <in.bin> <out.bin>
// 'auto' dispatches on the record type byte (O/R -> ord, Z -> end, B -> bbo, else msg).
#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

#include "../hls/tb/tb_util.hpp"
#include "../proto/dk1proto.hpp"

static void roundtrip(const char* kind, const uint8_t* in, uint8_t* out) {
    uint64_t w[4], w2[4];
    dk1::bytes_to_beats(in, w);
    std::string k(kind);
    if (k == "auto") {
        uint8_t t = in[0];
        k = (t == 'O' || t == 'R') ? "ord" : (t == 'Z') ? "end" : (t == 'B') ? "bbo" : "msg";
    }
    if (k == "msg") {
        dk1::Msg m = dk1::unpack_msg(w);
        dk1::pack_msg(m, w2);
    } else if (k == "ord") {
        dk1::OrdRec r = dk1::unpack_ord(w);
        dk1::pack_ord(r, w2);
    } else if (k == "end") {
        dk1::EndRec r = dk1::unpack_end(w);
        dk1::pack_end(r, w2);
        // pack_end forces type 'Z' and zero flags/product: keep the original header bytes
        // for a fair byte comparison of the payload fields
        w2[0] = (w2[0] & 0xFFFFFFFF00000000ull) | (w[0] & 0xFFFFFFFFull);
    } else {
        dk1::BboRec r = dk1::unpack_bbo(w);
        dk1::pack_bbo(r, w2);
        w2[0] = (w2[0] & ~0xFFull) | (w[0] & 0xFFull);
    }
    dk1::beats_to_bytes(w2, out);
}

int main(int argc, char** argv) {
    if (argc < 4) {
        printf("usage: proto_roundtrip <msg|ord|end|bbo|auto> <in> <out>\n");
        return 2;
    }
    std::vector<uint8_t> in;
    if (!tbu::read_file(argv[2], in) || in.size() % 32) return 3;
    std::vector<uint8_t> out(in.size());
    for (size_t i = 0; i < in.size(); i += 32) roundtrip(argv[1], &in[i], &out[i]);
    return tbu::write_file(argv[3], out) ? 0 : 4;
}
