# medicine_brands_demo.csv: DEMO DATA

A small hand-curated list of about 55 common Indian brands for blood pressure and diabetes,
used only to show the medicine-strip flow in the prototype ("Telma 40 = Telmisartan 40 mg").

- **Not a clinical reference.** It is incomplete and has not been checked by a pharmacist.
  Every match is read back to the parent for confirmation and can be corrected by family.
- Columns: `brand` (as printed, without strength), `strength` (the number printed next to the
  brand, `/` for combinations), `generic` (`+` between ingredients), `generic_strength` (same
  order as `generic`), `category` (`bp` | `diabetes`).
- For production, replace it with a maintained dataset (e.g. the MIT-licensed
  Indian-Medicine-Dataset or the government CDCI list, brief §5.2) behind the same matcher.
