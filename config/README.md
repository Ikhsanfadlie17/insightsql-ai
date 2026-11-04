# config/

`pricing.json` holds the model prices used for **estimated** API cost. It is the only
place prices are defined:

```json
{
  "currency": "USD",
  "updated": "2026-11-04",
  "source": "provider pricing page URL",
  "models": {
    "your-model-id": {
      "input_per_1m_usd": 0.0,
      "cached_input_per_1m_usd": 0.0,
      "output_per_1m_usd": 0.0
    }
  }
}
```

It ships with no models on purpose: prices change, so fill them in from your
provider's current pricing page. The key must match the model id the provider
reports (it may be a dated snapshot of the id you configured).

When a model has no price, or the provider reports no token usage, the request is
recorded with `est_cost_usd = null` and `cost_status` `unavailable_no_price` or
`unavailable_no_usage`. A cost is never shown as if it were known. A missing or
invalid file means no prices (it is logged, not fatal).
