# Clinical rules

The full alert rules. Summary in the [README](../README.md#clinical-rules-summary).

From the 2025 AHA/ACC blood-pressure guideline and the ADA Standards of Care 2026. Every number
the doctor can change is on the dashboard (**Limits & sharing**, entered as "set by the doctor");
the glucose floors can only be made stricter. Every family alert says "above / below the limit
her doctor set" (the pronoun is set at onboarding; default "their").

| Situation | What happens |
|---|---|
| Confirmed BP at/above the severe limit (default 180/120) | **Before anything else**, one question: chest pain, breathlessness, weakness/numbness on one side or slurred speech, severe headache, confusion, or sudden vision problems? Reply 1 yes / 2 no (voice-note version in en/hi/ta) |
| … answer **1 (yes)** | **RED at once**: family alert + call; the parent is told to call 108/112 |
| … answer **2 (no)**, or **no reply in 5 minutes** (written to the audit log) | Rest 5 minutes and repeat, as before. Answered "no" and no confirmed repeat in 15 minutes → AMBER to the family |
| … **no reply** to the question **and** no confirmed repeat in 15 minutes | **RED** (`bp_severe_no_reply`): silence after a severe reading can mean she cannot reply. "…She did not answer the warning-signs question and no repeat reading was taken within 15 minutes. Please call her now." Normal red path: always reaches the family, retried until delivered, rings the family |
| Confirmed repeat 5-60 minutes later, still severe | The question again. Yes → RED + 108/112. No / no reply → **RED "Urgent: contact her doctor today"** (no ambulance wording) |
| Glucose < 54 mg/dL (fixed floor) | RED to the family; the parent gets the doctor's plan and a recheck request |
| Glucose 54-69 mg/dL (fixed floor 70) | AMBER + "check again in 15 minutes"; **no confirmed recheck in 15 minutes → family alert** |
| After any low glucose | "Did you faint, feel confused, or did someone have to help you?" **Yes = RED whatever the number** |
| Low-sugar guidance to the parent | Only the doctor's own plan from onboarding / Settings ("Your doctor's plan: …"); without one: "Please follow your doctor's plan for low sugar." Khayal never adds advice |
| Meter shows HI / LO | Stored at the meter's limit (600 / 20 mg/dL) with the flag, so the limits still apply |
| Weekly summary | Counts and ranges against the doctor's limits, and the home BP average against the 135/85 home reference. Descriptive only, through the diagnosis/dosing filter |

Plausibility (in code, before any read-back): systolic 60-260, diastolic 30-160, systolic >
diastolic, pulse 30-200, glucose 20-600 mg/dL (1.1-33.3 mmol/L); a decimal "mg/dL" value or a
large "mmol/L" value is treated as a unit mix-up and refused.
