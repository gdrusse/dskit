**RAN — all 11 node(s) completed**

- run: `intraday-equities-framework-2026-08-30-3ca0081e`
- document hash: `cf590fc74125de7c…`
- previous run: — (first of the series)

| node | role | status | seconds |
|---|---|---|---|
| universe | data | ok | 9.5e-05 |
| alpaca | data | ok | 0.000182 |
| session | transform | ok | 3.022593 |
| features | transform | ok | 104.557216 |
| tradable | transform | ok | 0.00013 |
| label_train | transform | ok | 2.853476 |
| label_val | transform | ok | 0.387385 |
| qhat | train | ok | 12.5825 |
| select | score | ok | 9.544822 |
| search | search | ok | 1187.308115 |
| ensemble | transform | ok | 0.000301 |
