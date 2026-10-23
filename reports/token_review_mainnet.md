# Token safety review (mainnet)

Each token below was checked for transfer fees, freeze ability, clawback, issuer control and real liquidity. **Nothing is traded until you confirm a token in writing.**

| Token | Issuer | Verdict | Transfer fee | Can freeze | Clawback | XRP liquidity | Round trip 100 XRP |
|---|---|---|---|---|---|---|---|
| 3RDEYE | `rHjyBqFM5oQvXu1soWtATC4r1V6GBnhCQQ` | **CAUTION** | 0.000% | yes | no | 7,189 | -2.83% |
| ARMY | `rGG3wQ4kUzd7Jnmk1n5NWPZjjut62kCBfC` | **CAUTION** | 0.000% | yes | no | 270,518 | -0.07% |
| BITx | `rBitcoiNXev8VoVxV7pwoQx1sSfonVP9i3` | **NOT RECOMMENDED** | 0.000% | yes | no | 613 | -30.64% |
| DROP | `rszenFJoDdiGjyezQc8pME9KWDQH43Tswh` | **CAUTION** | 0.000% | yes | no | 119,431 | -0.95% |
| FUZZY | `rhCAT4hRdi2Y9puNdkpMzxrdKa5wkppR62` | **CAUTION** | 0.000% | yes | no | 754,855 | -0.91% |
| HOOTS | `rNPWp2RX2GHVbFbG77k76Ak8nhKwrHu17b` | **NOT RECOMMENDED** | 0.000% | yes | no | 3,860 | -5.26% |
| PHNIX | `rDFXbW2ZZCG5WgPtqwNiA2xZokLMm9ivmN` | **CAUTION** | 0.000% | yes | no | 197,928 | -0.53% |
| PLX | `rGLEgQdktoN4Be5thhk6seg1HifGPBxY5Q` | **CAUTION** | 0.000% | yes | no | 26,876 | -1.73% |
| POLICE | `rfjutsDJfakrhZUPMBe5E65S38eRZEP3yt` | **NOT RECOMMENDED** | 0.000% | yes | no | 963 | -17.31% |
| RLUSD | `rMxCKbEDwqr76QuheSUMdEGf4B9xJ8m5De` | **NOT RECOMMENDED** | 0.000% | yes | YES | 1,864,201 | -0.11% |
| SeagullCoin | `rnqiA8vuNriU9pqD1ZDGFH8ajQBL25Wkno` | **NOT RECOMMENDED** | 0.000% | yes | no | 707 | -85.45% |
| TRIPPR | `r9QYoVFNFZV1rZtFZEypPPpka2iEVDgqY1` | **NOT RECOMMENDED** | 0.000% | yes | no | 1,218 | -16.20% |
| Xmen | `rMFL2KuFPMuiujdbVGbMzNNjj5QKyd6dZf` | **CAUTION** | 0.000% | yes | no | 10,234 | -2.53% |
| gei | `raadqNCqPHhqpb5HEMBjbfFa21YYu9GWfW` | **NOT RECOMMENDED** | 0.000% | yes | no | 3,083 | -6.11% |
| 🌊JeetTide | `rPeZtayaDWY7oygidnkSPK99bdLPFbYYAW` | **NOT RECOMMENDED** | 0.000% | yes | no | 426 | -33.05% |
| DeepTide | `rnHDzEH4xyW6u4LbXqDoX1uk36v5ovhbFi` | **CAUTION** | 0.000% | yes | no | 10,568 | -2.29% |
| mula | `rMULA5hkZqsu7545NPnJZfu5ghKxuRbCro` | **NOT RECOMMENDED** | 0.000% | yes | no | 50 | -89.18% |
| ape | `rnxGEQytwx7jDfoKXfot9G4gQ1pbuHwf9m` | **NOT RECOMMENDED** | 0.000% | yes | no | 563 | -28.43% |
| CODED | `rnNErmg9xVmcxxNZUwivbJSH6i9GUwYPFj` | **CAUTION** | 0.000% | yes | no | 5,043 | -3.82% |
| SLOPPY | `rpGJSnyroEUzGmphzJXR7cyb5cNrC2gjZy` | **NOT RECOMMENDED** | 0.000% | yes | no | 383 | -34.52% |
| CRAP | `rDpyFiCNn7m5Y4zqzP5W1bUUGG4SEffnom` | **NOT RECOMMENDED** | 0.000% | yes | no | 1,608 | -11.80% |
| nuked | `rfM8omKYuv2Cwq4d7dja4h8yG59P7bqfja` | **NOT RECOMMENDED** | 0.000% | yes | no | 616 | -14.56% |
| scrap | `rGHtYnnigyuaHehWGfAdoEhkoirkGNdZzo` | **CAUTION** | 0.000% | yes | no | 12,751 | -1.62% |
| FARM | `rPrAEfVATUNDTJm9CUa8tYeD7oJrVdEGhU` | **CAUTION** | 0.000% | yes | no | 42,441 | -1.37% |

## 3RDEYE (rHjyBqFM5oQvXu1soWtATC4r1V6GBnhCQQ) - CAUTION
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## ARMY (rGG3wQ4kUzd7Jnmk1n5NWPZjjut62kCBfC) - CAUTION
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## BITx (rBitcoiNXev8VoVxV7pwoQx1sSfonVP9i3) - NOT RECOMMENDED
- ⚠️ Thin market: only about 613 XRP of liquidity against XRP (we want at least 5,000).
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## DROP (rszenFJoDdiGjyezQc8pME9KWDQH43Tswh) - CAUTION
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## FUZZY (rhCAT4hRdi2Y9puNdkpMzxrdKa5wkppR62) - CAUTION
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## HOOTS (rNPWp2RX2GHVbFbG77k76Ak8nhKwrHu17b) - NOT RECOMMENDED
- ⚠️ Thin market: only about 3,860 XRP of liquidity against XRP (we want at least 5,000).
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## PHNIX (rDFXbW2ZZCG5WgPtqwNiA2xZokLMm9ivmN) - CAUTION
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## PLX (rGLEgQdktoN4Be5thhk6seg1HifGPBxY5Q) - CAUTION
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## POLICE (rfjutsDJfakrhZUPMBe5E65S38eRZEP3yt) - NOT RECOMMENDED
- ⚠️ Thin market: only about 963 XRP of liquidity against XRP (we want at least 5,000).
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
- ⚠️ Thin market: only about 1,218 XRP of liquidity against XRP (we want at least 5,000).
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## Xmen (rMFL2KuFPMuiujdbVGbMzNNjj5QKyd6dZf) - CAUTION
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## gei (raadqNCqPHhqpb5HEMBjbfFa21YYu9GWfW) - NOT RECOMMENDED
- ⚠️ Thin market: only about 3,083 XRP of liquidity against XRP (we want at least 5,000).
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## 🌊JeetTide (rPeZtayaDWY7oygidnkSPK99bdLPFbYYAW) - NOT RECOMMENDED
- ⚠️ Thin market: only about 426 XRP of liquidity against XRP (we want at least 5,000).
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## DeepTide (rnHDzEH4xyW6u4LbXqDoX1uk36v5ovhbFi) - CAUTION
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## mula (rMULA5hkZqsu7545NPnJZfu5ghKxuRbCro) - NOT RECOMMENDED
- ⚠️ Thin market: only about 50 XRP of liquidity against XRP (we want at least 5,000).
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## ape (rnxGEQytwx7jDfoKXfot9G4gQ1pbuHwf9m) - NOT RECOMMENDED
- ⚠️ Thin market: only about 563 XRP of liquidity against XRP (we want at least 5,000).
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## CODED (rnNErmg9xVmcxxNZUwivbJSH6i9GUwYPFj) - CAUTION
- ⚠️ Buying and immediately selling 100 XRP worth loses 3.82% - prices move a lot with size.
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## SLOPPY (rpGJSnyroEUzGmphzJXR7cyb5cNrC2gjZy) - NOT RECOMMENDED
- ⚠️ Thin market: only about 383 XRP of liquidity against XRP (we want at least 5,000).
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## CRAP (rDpyFiCNn7m5Y4zqzP5W1bUUGG4SEffnom) - NOT RECOMMENDED
- ⚠️ Thin market: only about 1,608 XRP of liquidity against XRP (we want at least 5,000).
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## nuked (rfM8omKYuv2Cwq4d7dja4h8yG59P7bqfja) - NOT RECOMMENDED
- ⚠️ Thin market: only about 616 XRP of liquidity against XRP (we want at least 5,000).
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## scrap (rGHtYnnigyuaHehWGfAdoEhkoirkGNdZzo) - CAUTION
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).

## FARM (rPrAEfVATUNDTJm9CUa8tYeD7oJrVdEGhU) - CAUTION
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer account is locked ('blackholed'): its settings can never change (good).
