# Synthetic Data Validation Report

Generated: 2026-04-08T18:42:06.475075
Sample size: 2000 transactions
Fraud count: 51 (2.55%)

## 1. TransactionAmt Distribution

| Statistic | IEEE-CIS | Synthetic | % Diff |
| --- | --- | --- | --- |
| Mean | 131.23 | 117.27 | 10.6% |
| Std | 252.97 | 191.86 | 24.2% |
| Min | 0.25 | 0.58 | 132.0% |
| Q25 | 30.00 | 25.56 | 14.8% |
| Median | 58.95 | 55.45 | 5.9% |
| Q75 | 120.00 | 123.64 | 3.0% |
| Max | 31937.39 | 1500.00 | 95.3% |

Note: Synthetic amounts are capped at 1500.0 while IEEE-CIS max is 31937.39.
The log-normal parameters (mean_log=4.0, std_log=1.2) produce the synthetic distribution.

## 2. Fraud Rate Analysis

**Target fraud rate (IEEE-CIS):** 2.71%
**Observed synthetic fraud rate:** 2.55%

### Hourly Fraud Rate Comparison

| Hour | IEEE-CIS Rate | Config Multiplier | Effective Rate |
| --- | --- | --- | --- |
| 0 | 3.51% | 1.3x | 3.52% |
| 1 | 4.06% | 1.5x | 4.06% |
| 2 | 4.78% | 1.8x | 4.77% |
| 3 | 5.48% | 2.0x | 5.47% |
| 4 | 4.92% | 1.8x | 4.93% |
| 5 | 3.76% | 1.4x | 3.77% |
| 6 | 2.92% | 1.1x | 2.93% |
| 7 | 2.51% | 0.9x | 2.52% |
| 8 | 2.28% | 0.8x | 2.28% |
| 9 | 2.06% | 0.8x | 2.06% |
| 10 | 1.89% | 0.7x | 1.90% |
| 11 | 1.85% | 0.7x | 1.84% |
| 12 | 1.87% | 0.7x | 1.87% |
| 13 | 1.81% | 0.7x | 1.82% |
| 14 | 1.83% | 0.7x | 1.84% |
| 15 | 1.92% | 0.7x | 1.92% |
| 16 | 2.07% | 0.8x | 2.06% |
| 17 | 2.18% | 0.8x | 2.17% |
| 18 | 2.30% | 0.8x | 2.30% |
| 19 | 2.44% | 0.9x | 2.44% |
| 20 | 2.64% | 1.0x | 2.63% |
| 21 | 2.78% | 1.0x | 2.79% |
| 22 | 2.99% | 1.1x | 2.98% |
| 23 | 3.18% | 1.2x | 3.17% |

IEEE-CIS high-risk hours: [0, 1, 2, 3, 4, 5, 22, 23]
Config PEAK_FRAUD_HOURS: [0, 1, 2, 3, 4, 5]
**MISMATCH**: Config peak hours [0, 1, 2, 3, 4, 5] differ from IEEE-CIS [0, 1, 2, 3, 4, 5, 22, 23]. IEEE includes hours 0,1,5,22,23 as high-risk.

## 3. Card Feature Distributions

### card1 (Primary Card ID)
| Stat | IEEE-CIS | Synthetic |
| --- | --- | --- |
| Mean | 9667 | 10505 |
| Std | 4980 | 5541 |
| Min | 1000 | 1005 |
| Max | 18396 | 20000 |

### card4 (Card Network)
| Network | IEEE-CIS | Synthetic |
| --- | --- | --- |
| visa | 58.0% | 58.8% |
| mastercard | 34.0% | 32.8% |
| discover | 5.0% | 5.1% |
| american express | 3.0% | 3.4% |

### card6 (Card Type)
| Type | IEEE-CIS | Synthetic |
| --- | --- | --- |
| debit | 26.0% | 24.2% |
| credit | 61.0% | 63.1% |
| debit or credit | 10.0% | 9.6% |
| charge card | 3.0% | 3.1% |


## 4. C-Feature (Counting) Analysis

| Feature | IEEE Null% | Config Null% | Actual Null% | IEEE Mean | Synth Mean |
| --- | --- | --- | --- | --- | --- |
| C1 | 0.00 | 0.00 | 0.00 | 1.68 | 3.43 |
| C2 | 0.00 | 0.00 | 0.00 | 1.33 | 1.07 |
| C3 | 0.00 | 0.00 | 0.14 | 0.06 | 208.15 |
| C4 | 0.00 | 0.00 | 0.00 | 0.27 | 2.79 |
| C5 | 0.00 | 0.00 | 0.00 | 0.19 | 0.90 |
| C6 | 0.15 | 0.15 | 0.26 | 1.01 | 137.80 |
| C7 | 0.15 | 0.15 | 0.14 | 0.31 | 11.19 |
| C8 | 0.15 | 0.15 | 0.15 | 0.12 | 0.93 |
| C9 | 0.00 | 0.00 | 0.00 | 0.72 | 11.16 |
| C10 | 0.15 | 0.15 | 0.16 | 0.14 | 1.08 |
| C11 | 0.15 | 0.15 | 0.13 | 1.22 | 12.69 |
| C12 | 0.00 | 0.00 | 0.00 | 1.36 | 11.14 |
| C13 | 0.00 | 0.00 | 0.00 | 13.12 | 596.01 |
| C14 | 0.00 | 0.00 | 0.00 | 1.22 | 0.00 |


**Key Finding:** IEEE-CIS has 0% null rate for C1-C5, C9, C12-C14, but `config.py` uses non-zero null rates (2-25%). This is a distribution mismatch.

## 5. D-Feature (Time Delta) Analysis

| Feature | IEEE Null% | Config Null% | Actual Null% | IEEE Mean | Synth Mean |
| --- | --- | --- | --- | --- | --- |
| D1 | 0.002 | 0.002 | 0.003 | 132.3 | 0.0 |
| D2 | 0.470 | 0.470 | 0.471 | 195.6 | 0.0 |
| D3 | 0.470 | 0.470 | 0.481 | 168.4 | 0.0 |
| D4 | 0.568 | 0.570 | 0.564 | 123.6 | 0.3 |
| D5 | 0.753 | 0.750 | 0.759 | 107.2 | 30.0 |
| D6 | 0.572 | 0.570 | 0.565 | N/A | N/A |
| D7 | 0.651 | 0.650 | 0.677 | N/A | N/A |
| D8 | 0.676 | 0.680 | 0.675 | N/A | N/A |
| D9 | 0.862 | 0.860 | 0.868 | N/A | N/A |
| D10 | 0.869 | 0.870 | 0.860 | 86.5 | 1.4 |
| D11 | 0.872 | 0.870 | 0.870 | 99.1 | 0.0 |
| D12 | 0.889 | 0.890 | 0.886 | N/A | N/A |
| D13 | 0.891 | 0.890 | 0.889 | N/A | N/A |
| D14 | 0.901 | 0.900 | 0.900 | N/A | N/A |
| D15 | 0.912 | 0.910 | 0.909 | 82.3 | 45.9 |


## 6. M-Feature (Match) Analysis

| Feature | IEEE Null% | Config Null% | Actual Null% | Value Dist (non-null) |
| --- | --- | --- | --- | --- |
| M1 | 0.472 | 0.470 | 0.456 | T:82% F:12% NF:6% |
| M2 | 0.472 | 0.470 | 0.524 | T:73% F:19% NF:7% |
| M3 | 0.472 | 0.470 | 0.488 | T:78% F:16% NF:7% |
| M4 | 0.528 | 0.530 | 0.531 | T:86% F:10% NF:4% |
| M5 | 0.472 | 0.470 | 0.462 | T:91% F:6% NF:3% |
| M6 | 0.472 | 0.470 | 0.473 | T:76% F:18% NF:6% |
| M7 | 0.528 | 0.530 | 0.539 | T:87% F:9% NF:3% |
| M8 | 0.568 | 0.570 | 0.633 | T:22% F:56% NF:22% |
| M9 | 0.528 | 0.530 | 0.535 | T:81% F:12% NF:6% |


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
- Config max_amount: 1500.0
- IEEE-CIS spec max_amount: 1500.0
- IEEE-CIS actual max: 31937.39
- Synthetic max observed: 1500.00

### Amount Min
- Config min_amount: 0.25
- IEEE-CIS spec min_amount: 0.25

### Fraud Amount Bias
- Config FRAUD_AMOUNT_BIAS: 1.34
- IEEE spec high_amount_bias: 1.34

## 9. C-Feature Null Rate Mismatches

The IEEE-CIS dataset has 0% null for many C-features, but `config.py` applies artificial null rates:

## 10. Generation Pacing Assessment

- **DEFAULT_TARGET_TPS:** 2000
- **DEFAULT_DURATION_SECONDS:** 180
- **DEFAULT_USER_COUNT:** 5000
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
| HIGH | Amount max_amount mismatch | Config caps at 1500.0 vs IEEE spec 1500.0. |
| HIGH | Amount min_amount mismatch | Config min is 0.25 vs IEEE spec 0.25. |
| HIGH | Fraud amount bias mismatch | Config 1.34 vs IEEE 1.34. |
| MEDIUM | C-feature null rate mismatches | C1-C5, C9, C12-C14 have 0% null in IEEE but 2-25% in config. |
| MEDIUM | Peak fraud hours mismatch | Config uses [0, 1, 2, 3, 4, 5] but IEEE high-risk hours include [0, 1, 2, 3, 4, 5, 22, 23]. |
| LOW | User count too small | 500 users at 2000 TPS = ~4 TPS/user, unrealistically high. |
