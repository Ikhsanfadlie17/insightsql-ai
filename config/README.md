# config/

`pricing.json` maps model IDs to prices used for **estimated** API cost:

```json
{
  "your-model-id": { "input_per_1m_usd": 0.0, "output_per_1m_usd": 0.0 }
}
```

It ships empty on purpose: prices change, so fill them in from your provider's
current pricing page. Requests to a model that is not listed are logged with an
estimated cost of `null` (shown as N/A) instead of a guess.
