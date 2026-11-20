Task set (sw mode, feed budget 128 messages / 1 ms, release jitter J = 150 µs):

| Task | Prio | T (ms) | C (µs) = WCET + overhead | U | R analytic (µs) | R measured max (µs) | jobs / misses |
|---|---|---|---|---|---|---|---|
| tick ISR | 99 | 1 | 0.8 = 0.8 + 0.0 | 0.001 | 0.8 ✓ | — | — |
| feed | 4 | 1 | 314.4 = 313.1 + 1.2 | 0.314 | 465.2 ✓ | 444.4 | 57736 / 0 |
| monitor | 3 | 10 | 2.0 = 0.8 + 1.2 | 0.000 | 467.2 ✓ | 442.8 | 5773 / 0 |
| telemetry | 2 | 100 | 27082.8 = 27081.6 + 1.2 | 0.271 | 39848.7 ✓ | 16396.7 | 577 / 0 |

U = 0.586; Liu & Layland bound for n = 4: 0.757 (passes); hyperbolic bound Π(Uᵢ+1) = 1.672 ≤ 2 (passes); exact RTA: all deadlines met.
Feed cost ≈ 2.446 µs per message; the largest budget the RTA accepts is 296 messages per 1 ms job (linear extrapolation).
