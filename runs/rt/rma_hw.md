Task set (hw mode, feed budget 8192 messages / 1 ms, release jitter J = 150 µs):

| Task | Prio | T (ms) | C (µs) = WCET + overhead | U | R analytic (µs) | R measured max (µs) | jobs / misses |
|---|---|---|---|---|---|---|---|
| tick ISR | 99 | 1 | 0.7 = 0.7 + 0.0 | 0.001 | 0.7 ✓ | — | — |
| feed | 4 | 1 | 559.7 = 558.2 + 1.5 | 0.560 | 710.4 ✓ | 183.7 | 57735 / 0 |
| monitor | 3 | 10 | 11.4 = 10.0 + 1.5 | 0.001 | 721.9 ✓ | 193.2 | 5773 / 0 |
| telemetry | 2 | 100 | 26996.2 = 26994.8 + 1.5 | 0.270 | 61972.3 ✓ | 15349.9 | 577 / 0 |

U = 0.832; Liu & Layland bound for n = 4: 0.757 (not met); hyperbolic bound Π(Uᵢ+1) = 1.984 ≤ 2 (passes); exact RTA: all deadlines met.
Feed cost ≈ 0.068 µs per message; the largest budget the RTA accepts is 10642 messages per 1 ms job (linear extrapolation).
