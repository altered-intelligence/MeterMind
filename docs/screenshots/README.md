# Screenshots

Illustrations of the interface, referenced by the project README.

These are **SVG mockups rendered with a synthetic sample dataset** — not screen captures of
real data. The numbers (dollar amounts, usage, dates) are made up for illustration. They
faithfully represent the app's layout while keeping any real billing data private.

| File | Shows |
|------|-------|
| `insights.svg` | The unified **Insights** page (latest-bill summary + at-a-glance deltas) |
| `rate-breakdown.svg` | The **Rate & fee breakdown** panel (category cards + line-item mapping) |
| `trends.svg` | **Trends by billing cycle** (usage, cost/day, effective rate for electric & gas) |
| `account-analytics.svg` | The **Account analytics** card |

## Capturing your own (optional)

The app redacts all identifiers — **account numbers, names, service addresses, and meter
numbers never appear in the UI** — so real screenshots are free of identity-PII. They do show
your dollar amounts and usage, though. To capture screens against the bundled synthetic sample
data instead, run fully offline and upload the sanitized sample bill(s):

```bash
WEATHER_PROVIDER=mock GEOCODER=mock ./scripts/dev.sh
```
