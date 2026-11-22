"""Demo partner pharmacy (brief §3: refills happen off WhatsApp, at a licensed pharmacy).

DEMO ONLY: no real order is placed, nothing leaves this server. A real partner would
receive the approved request, check the prescription and confirm to the parent itself.
"""

from app.storage.models import Medicine, Refill

DEMO_LABEL = "Demo partner pharmacy (demo, no real order placed)"


class DemoPharmacy:
    name = DEMO_LABEL

    def submit(self, refill: Refill, medicine: Medicine) -> str:
        """Hand an approved refill over; returns the (fake) pharmacy reference."""
        return f"DEMO-{refill.id.removeprefix('rf_')[:8].upper()}"
