# Token safety review (mainnet)

Each token below was checked for transfer fees, freeze ability, clawback, issuer control and real liquidity. **Nothing is traded until you confirm a token in writing.**

| Token | Issuer | Verdict | Transfer fee | Can freeze | Clawback | XRP liquidity | Round trip 100 XRP |
|---|---|---|---|---|---|---|---|
| 3RDEYE | `rHjyBqFM5oQvXu1soWtATC4r1V6GBnhCQQ` | **CAUTION** | 0.000% | yes | no | 7,206 | -2.83% |
| ARMY | `rGG3wQ4kUzd7Jnmk1n5NWPZjjut62kCBfC` | **CAUTION** | 0.000% | yes | no | 269,376 | -0.07% |
| BITx | `rBitcoiNXev8VoVxV7pwoQx1sSfonVP9i3` | **NOT RECOMMENDED** | 0.000% | yes | no | 609 | -30.17% |
| DROP | `rszenFJoDdiGjyezQc8pME9KWDQH43Tswh` | **CAUTION** | 0.000% | yes | no | 115,386 | -0.96% |
| FUZZY | `rhCAT4hRdi2Y9puNdkpMzxrdKa5wkppR62` | **CAUTION** | 0.000% | yes | no | 916,172 | -0.86% |
| HOOTS | `rNPWp2RX2GHVbFbG77k76Ak8nhKwrHu17b` | **NOT RECOMMENDED** | 0.000% | yes | no | 3,675 | -5.50% |
| PHNIX | `rDFXbW2ZZCG5WgPtqwNiA2xZokLMm9ivmN` | **CAUTION** | 0.000% | yes | no | 203,107 | -0.16% |
| PLX | `rGLEgQdktoN4Be5thhk6seg1HifGPBxY5Q` | **CAUTION** | 0.000% | yes | no | 26,848 | -1.73% |
| POLICE | `rfjutsDJfakrhZUPMBe5E65S38eRZEP3yt` | **NOT RECOMMENDED** | 0.000% | yes | no | 960 | -17.36% |
| RLUSD | `rMxCKbEDwqr76QuheSUMdEGf4B9xJ8m5De` | **NOT RECOMMENDED** | 0.000% | yes | YES | 1,884,509 | -0.10% |
| SeagullCoin | `rnqiA8vuNriU9pqD1ZDGFH8ajQBL25Wkno` | **NOT RECOMMENDED** | 0.000% | yes | no | 707 | -85.59% |
| TRIPPR | `r9QYoVFNFZV1rZtFZEypPPpka2iEVDgqY1` | **NOT RECOMMENDED** | 0.000% | yes | no | 1,027 | -17.47% |
| Xmen | `rMFL2KuFPMuiujdbVGbMzNNjj5QKyd6dZf` | **CAUTION** | 0.000% | yes | no | 10,381 | -2.50% |
| gei | `raadqNCqPHhqpb5HEMBjbfFa21YYu9GWfW` | **NOT RECOMMENDED** | 0.000% | yes | no | 3,082 | -6.11% |

## 3RDEYE (rHjyBqFM5oQvXu1soWtATC4r1V6GBnhCQQ) - CAUTION
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## ARMY (rGG3wQ4kUzd7Jnmk1n5NWPZjjut62kCBfC) - CAUTION
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## BITx (rBitcoiNXev8VoVxV7pwoQx1sSfonVP9i3) - NOT RECOMMENDED
- ⚠️ Thin market: only about 609 XRP of liquidity against XRP (we want at least 5,000).
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## DROP (rszenFJoDdiGjyezQc8pME9KWDQH43Tswh) - CAUTION
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## FUZZY (rhCAT4hRdi2Y9puNdkpMzxrdKa5wkppR62) - CAUTION
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## HOOTS (rNPWp2RX2GHVbFbG77k76Ak8nhKwrHu17b) - NOT RECOMMENDED
- ⚠️ Thin market: only about 3,675 XRP of liquidity against XRP (we want at least 5,000).
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## PHNIX (rDFXbW2ZZCG5WgPtqwNiA2xZokLMm9ivmN) - CAUTION
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## PLX (rGLEgQdktoN4Be5thhk6seg1HifGPBxY5Q) - CAUTION
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## POLICE (rfjutsDJfakrhZUPMBe5E65S38eRZEP3yt) - NOT RECOMMENDED
- ⚠️ Thin market: only about 960 XRP of liquidity against XRP (we want at least 5,000).
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## RLUSD (rMxCKbEDwqr76QuheSUMdEGf4B9xJ8m5De) - NOT RECOMMENDED
- ⚠️ CLAWBACK: the issuer can take this token back out of any wallet at any time. If that happened while a bot held it, the bot would lose that value.
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer can still change settings (e.g. raise the transfer fee up to 100%). The bots re-check the fee and flags every few minutes and stop using the token if anything gets worse.

## SeagullCoin (rnqiA8vuNriU9pqD1ZDGFH8ajQBL25Wkno) - NOT RECOMMENDED
- ⚠️ Thin market: only about 707 XRP of liquidity against XRP (we want at least 5,000).
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## TRIPPR (r9QYoVFNFZV1rZtFZEypPPpka2iEVDgqY1) - NOT RECOMMENDED
- ⚠️ Thin market: only about 1,027 XRP of liquidity against XRP (we want at least 5,000).
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## Xmen (rMFL2KuFPMuiujdbVGbMzNNjj5QKyd6dZf) - CAUTION
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## gei (raadqNCqPHhqpb5HEMBjbfFa21YYu9GWfW) - NOT RECOMMENDED
- ⚠️ Thin market: only about 3,082 XRP of liquidity against XRP (we want at least 5,000).
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).
