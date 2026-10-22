# Token safety review (testnet)

Each token below was checked for transfer fees, freeze ability, clawback, issuer control and real liquidity. **Nothing is traded until you confirm a token in writing.**

| Token | Issuer | Verdict | Transfer fee | Can freeze | Clawback | XRP liquidity | Round trip 100 XRP |
|---|---|---|---|---|---|---|---|
| TUS | `r93iwyqNYXszEbRAbCM7WLHTQj1fRDmAGr` | **CAUTION** | 0.000% | no | no | 25 | -88.91% |
| TEU | `r93iwyqNYXszEbRAbCM7WLHTQj1fRDmAGr` | **CAUTION** | 0.000% | no | no | 25 | -88.91% |
| TGB | `rL4c4udeKEHQRLvomLwK4gbb8ppexH9aAa` | **CAUTION** | 0.200% | yes | no | 25 | -88.92% |

## TUS (r93iwyqNYXszEbRAbCM7WLHTQj1fRDmAGr) - CAUTION
- ⚠️ Buying and immediately selling 100 XRP worth loses 88.91% - prices move a lot with size.
- The issuer has permanently given up the ability to freeze balances (good).
- The issuer can still change settings (e.g. raise the transfer fee up to 100%). The bots re-check the fee and flags every few minutes and stop using the token if anything gets worse.

## TEU (r93iwyqNYXszEbRAbCM7WLHTQj1fRDmAGr) - CAUTION
- ⚠️ Buying and immediately selling 100 XRP worth loses 88.91% - prices move a lot with size.
- The issuer has permanently given up the ability to freeze balances (good).
- The issuer can still change settings (e.g. raise the transfer fee up to 100%). The bots re-check the fee and flags every few minutes and stop using the token if anything gets worse.

## TGB (rL4c4udeKEHQRLvomLwK4gbb8ppexH9aAa) - CAUTION
- ⚠️ Buying and immediately selling 100 XRP worth loses 88.92% - prices move a lot with size.
- Transfer fee 0.200%: the issuer takes this cut every time the token moves. It's included in every profit calculation, but it makes loops through this token harder to win.
- The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is small, but a freeze at the wrong moment could trap one trade's worth.
- The issuer can still change settings (e.g. raise the transfer fee up to 100%). The bots re-check the fee and flags every few minutes and stop using the token if anything gets worse.
