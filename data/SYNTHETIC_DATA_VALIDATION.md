# Synthetic Data Validation Report

Generated: 2026-04-08T18:37:16.523114
Sample size: 2000 transactions
Fraud count: 146 (7.30%)

## 1. TransactionAmt Distribution

| Statistic | IEEE-CIS | Synthetic | % Diff |
| --- | --- | --- | --- |
| Mean | 131.23 | 127.84 | 2.6% |
| Std | 252.97 | 199.62 | 21.1% |
| Min | 0.25 | 0.73 | 192.0% |
| Q25 | 30.00 | 26.04 | 13.2% |
| Median | 58.95 | 58.80 | 0.3% |
| Q75 | 120.00 | 134.19 | 11.8% |
| Max | 31937.39 | 1500.00 | 95.3% |

Note: Synthetic amounts are capped at 1000.0 while IEEE-CIS max is 31937.39.
The log-normal parameters (mean_log=4.0, std_log=1.2) produce the synthetic distribution.

## 2. Fraud Rate Analysis

**Target fraud rate (IEEE-CIS):** 2.71%
**Observed synthetic fraud rate:** 7.30%

### Hourly Fraud Rate Comparison

| Hour | IEEE-CIS Rate | Config Multiplier | Effective Rate |
| --- | --- | --- | --- |
| 0 | 3.51% | 1.6x | 4.34% |
| 1 | 4.06% | 1.9x | 5.15% |
| 2 | 4.78% | 2.2x | 5.96% |
| 3 | 5.48% | 2.4x | 6.50% |
| 4 | 4.92% | 2.1x | 5.69% |
| 5 | 3.76% | 1.7x | 4.61% |
| 6 | 2.92% | 1.3x | 3.52% |
| 7 | 2.51% | 1.1x | 2.98% |
| 8 | 2.28% | 1.0x | 2.71% |
| 9 | 2.06% | 0.9x | 2.44% |
| 10 | 1.89% | 0.8x | 2.17% |
| 11 | 1.85% | 0.8x | 2.17% |
| 12 | 1.87% | 0.8x | 2.17% |
| 13 | 1.81% | 0.7x | 1.90% |
| 14 | 1.83% | 0.7x | 1.90% |
| 15 | 1.92% | 0.8x | 2.17% |
| 16 | 2.07% | 0.9x | 2.44% |
| 17 | 2.18% | 1.0x | 2.71% |
| 18 | 2.30% | 1.1x | 2.98% |
| 19 | 2.44% | 1.2x | 3.25% |
| 20 | 2.64% | 1.3x | 3.52% |
| 21 | 2.78% | 1.4x | 3.79% |
| 22 | 2.99% | 1.5x | 4.06% |
| 23 | 3.18% | 1.6x | 4.34% |

IEEE-CIS high-risk hours: [0, 1, 2, 3, 4, 5, 22, 23]
Config PEAK_FRAUD_HOURS: [2, 3, 4]
**MISMATCH**: Config peak hours [2, 3, 4] differ from IEEE-CIS [0, 1, 2, 3, 4, 5, 22, 23]. IEEE includes hours 0,1,5,22,23 as high-risk.

## 3. Card Feature Distributions

### card1 (Primary Card ID)
| Stat | IEEE-CIS | Synthetic |
| --- | --- | --- |
| Mean | 9667 | 10474 |
| Std | 4980 | 5463 |
| Min | 1000 | 1000 |
| Max | 18396 | 19991 |

### card4 (Card Network)
| Network | IEEE-CIS | Synthetic |
| --- | --- | --- |
| visa | 58.0% | 56.7% |
| mastercard | 34.0% | 35.4% |
| discover | 5.0% | 4.8% |
| american express | 3.0% | 3.0% |

### card6 (Card Type)
| Type | IEEE-CIS | Synthetic |
| --- | --- | --- |
| debit | 26.0% | 26.4% |
| credit | 61.0% | 61.1% |
| debit or credit | 10.0% | 10.3% |
| charge card | 3.0% | 2.2% |


## 4. C-Feature (Counting) Analysis

| Feature | IEEE Null% | Config Null% | Actual Null% | IEEE Mean | Synth Mean |
| --- | --- | --- | --- | --- | --- |
| C1 | 0.00 | 0.02 | 0.02 | 1.68 | 3.90 |
| C2 | 0.00 | 0.02 | 0.02 | 1.33 | 1.15 |
| C3 | 0.00 | 0.20 | 0.31 | 0.06 | 220.51 |
| C4 | 0.00 | 0.08 | 0.07 | 0.27 | 2.77 |
| C5 | 0.00 | 0.25 | 0.25 | 0.19 | 0.98 |
| C6 | 0.15 | 0.30 | 0.41 | 1.01 | 151.97 |
| C7 | 0.15 | 0.35 | 0.36 | 0.31 | 11.76 |
| C8 | 0.15 | 0.40 | 0.39 | 0.12 | 0.99 |
| C9 | 0.00 | 0.15 | 0.13 | 0.72 | 12.33 |
| C10 | 0.15 | 0.30 | 0.29 | 0.14 | 1.12 |
| C11 | 0.15 | 0.35 | 0.37 | 1.22 | 14.06 |
| C12 | 0.00 | 0.10 | 0.11 | 1.36 | 12.63 |
| C13 | 0.00 | 0.20 | 0.21 | 13.12 | 657.09 |
| C14 | 0.00 | 0.25 | 0.25 | 1.22 | 0.00 |


**Key Finding:** IEEE-CIS has 0% null rate for C1-C5, C9, C12-C14, but `config.py` uses non-zero null rates (2-25%). This is a distribution mismatch.

## 5. D-Feature (Time Delta) Analysis

| Feature | IEEE Null% | Config Null% | Actual Null% | IEEE Mean | Synth Mean |
| --- | --- | --- | --- | --- | --- |
| D1 | 0.002 | 0.002 | 0.002 | 132.3 | 0.0 |
| D2 | 0.470 | 0.470 | 0.442 | 195.6 | 0.0 |
| D3 | 0.470 | 0.470 | 0.474 | 168.4 | 0.0 |
| D4 | 0.568 | 0.570 | 0.577 | 123.6 | 0.8 |
| D5 | 0.753 | 0.750 | 0.745 | 107.2 | 30.0 |
| D6 | 0.572 | 0.570 | 0.561 | N/A | N/A |
| D7 | 0.651 | 0.650 | 0.696 | N/A | N/A |
| D8 | 0.676 | 0.680 | 0.695 | N/A | N/A |
| D9 | 0.862 | 0.860 | 0.853 | N/A | N/A |
| D10 | 0.869 | 0.870 | 0.872 | 86.5 | 1.8 |
| D11 | 0.872 | 0.870 | 0.873 | 99.1 | 0.0 |
| D12 | 0.889 | 0.890 | 0.894 | N/A | N/A |
| D13 | 0.891 | 0.890 | 0.892 | N/A | N/A |
| D14 | 0.901 | 0.900 | 0.907 | N/A | N/A |
| D15 | 0.912 | 0.910 | 0.913 | 82.3 | 41.0 |


## 6. M-Feature (Match) Analysis

| Feature | IEEE Null% | Config Null% | Actual Null% | Value Dist (non-null) |
| --- | --- | --- | --- | --- |
| M1 | 0.472 | 0.470 | 0.484 | T:77% F:17% NF:6% |
| M2 | 0.472 | 0.470 | 0.539 | T:71% F:21% NF:9% |
| M3 | 0.472 | 0.470 | 0.473 | T:74% F:18% NF:8% |
| M4 | 0.528 | 0.530 | 0.538 | T:85% F:10% NF:5% |
| M5 | 0.472 | 0.470 | 0.472 | T:89% F:8% NF:3% |
| M6 | 0.472 | 0.470 | 0.474 | T:76% F:19% NF:5% |
| M7 | 0.528 | 0.530 | 0.526 | T:84% F:13% NF:4% |
| M8 | 0.568 | 0.570 | 0.627 | T:25% F:55% NF:19% |
| M9 | 0.528 | 0.530 | 0.542 | T:78% F:16% NF:6% |


## 7. Feature Compatibility with Production Model

**Model expects:** 200 features
**Synthetic provides (mapped):** 28 features
**Missing from synthetic:** 172 features
**Extra in synthetic (unused by model):** 25 features

### Missing Feature Categories

| Category | Count | Examples |
| --- | --- | --- |
| V-features (Vesta) | 147 | V1, V10, V107, V108, V109, V11, V110, V111, V112, V113... |
| id-features (Identity) | 22 | DeviceInfo, DeviceType, id_11, id_12, id_13, id_15, id_16, id_17, id_19, id_20 |
| TransactionAmt derived | 3 | TransactionAmt_bin, TransactionAmt_decimal, TransactionAmt_log |
| C-features | 0 |  |
| D-features | 2 | DeviceInfo, DeviceType |
| Other | 0 |  |


### C-Feature Coverage
Model uses: ['C10', 'C12', 'C4', 'C7', 'C8']
Synthetic generates: ['C1', 'C10', 'C11', 'C12', 'C13', 'C14', 'C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'C8', 'C9']
Model needs but synthetic has: ['C10', 'C12', 'C4', 'C7', 'C8']
Model needs but synthetic lacks: []

### D-Feature Coverage
Model uses: ['D8']
Synthetic generates: ['D1', 'D10', 'D11', 'D12', 'D13', 'D14', 'D15', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D9']
Model needs but synthetic has: ['D8']
Model needs but synthetic lacks: []

## 8. Specific Distribution Issues

### Amount Capping
- Config max_amount: 1000.0
- IEEE-CIS spec max_amount: 1500.0
- IEEE-CIS actual max: 31937.39
- Synthetic max observed: 1500.00
- **ISSUE**: Config caps at 1000.0 but IEEE spec says 1500.0.

### Amount Min
- Config min_amount: 1.0
- IEEE-CIS spec min_amount: 0.25
- **ISSUE**: Config min is 1.0 but IEEE spec says 0.25.

### Fraud Amount Bias
- Config FRAUD_AMOUNT_BIAS: 1.2
- IEEE spec high_amount_bias: 1.34
- **ISSUE**: Config uses 1.2 but IEEE spec says 1.34.

## 9. C-Feature Null Rate Mismatches

The IEEE-CIS dataset has 0% null for many C-features, but `config.py` applies artificial null rates:
| Feature | IEEE Null% | Config Null% | Abs Diff |
| --- | --- | --- | --- |
| C1 | 0.00 | 0.02 | 0.02 |
| C2 | 0.00 | 0.02 | 0.02 |
| C3 | 0.00 | 0.20 | 0.20 |
| C4 | 0.00 | 0.08 | 0.08 |
| C5 | 0.00 | 0.25 | 0.25 |
| C6 | 0.15 | 0.30 | 0.15 |
| C7 | 0.15 | 0.35 | 0.20 |
| C8 | 0.15 | 0.40 | 0.25 |
| C9 | 0.00 | 0.15 | 0.15 |
| C10 | 0.15 | 0.30 | 0.15 |
| C11 | 0.15 | 0.35 | 0.20 |
| C12 | 0.00 | 0.10 | 0.10 |
| C13 | 0.00 | 0.20 | 0.20 |
| C14 | 0.00 | 0.25 | 0.25 |

**Recommendation:** Align C-feature null rates with IEEE-CIS values.

## 10. Generation Pacing Assessment

- **DEFAULT_TARGET_TPS:** 2000
- **DEFAULT_DURATION_SECONDS:** 180
- **DEFAULT_USER_COUNT:** 500
- Total transactions per run: ~360,000

### Assessment
- The IEEE-CIS dataset has 590,540 transactions over ~182 days, averaging ~3,245 transactions/day (~0.04 TPS).
- The default TPS of 2000 is a load-testing config, not a realistic production rate.
- For a large payment processor, 2000 TPS is realistic peak volume.
- The 500 user pool is small for 2000 TPS -- this means ~4 TPS per user, which is unrealistically high.
- **Recommendation:** Increase DEFAULT_USER_COUNT to 5000+ for more realistic per-user transaction frequency.

## 11. Summary of Issues and Recommendations

| Severity | Issue | Detail |
| --- | --- | --- |
| CRITICAL | Missing 149 V-features | The model expects 149 V-features (Vesta engineered features) that the synthetic producer does not ge... |
| CRITICAL | Missing identity features | The model expects 22 identity features (DeviceInfo, DeviceType, id_11, id_12, id_13...) not generate... |
| CRITICAL | Missing TransactionAmt derived features | Model expects TransactionAmt_bin, TransactionAmt_decimal, TransactionAmt_log which are not generated... |
| HIGH | Amount max_amount mismatch | Config caps at 1000.0 vs IEEE spec 1500.0. |
| HIGH | Amount min_amount mismatch | Config min is 1.0 vs IEEE spec 0.25. |
| HIGH | Fraud amount bias mismatch | Config 1.2 vs IEEE 1.34. |
| MEDIUM | C-feature null rate mismatches | C1-C5, C9, C12-C14 have 0% null in IEEE but 2-25% in config. |
| MEDIUM | Peak fraud hours mismatch | Config uses [2, 3, 4] but IEEE high-risk hours include [0, 1, 2, 3, 4, 5, 22, 23]. |
| LOW | User count too small | 500 users at 2000 TPS = ~4 TPS/user, unrealistically high. |
