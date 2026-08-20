"""The fiction pack: the only source of "personal" data the persona may ever speak.

Guardrail G-3 is easier to hold if the persona has no real data to leak in the first
place. The character's entire knowledge of itself — name, address, card, bank, SSN,
grandchildren — comes from one of these identities, so even a perfect jailbreak
surfaces nothing but invented values that were checked to be unusable.

**Cards must fail the Luhn check, and reserved test PANs are banned.** A published test
PAN like 4111 1111 1111 1111 passes Luhn by construction, so the pre-TTS filter (G-4)
blocks it — and blocking every card number would kill the best stalling tactic in the
playbook, reading digits aloud badly. Numbers that fail the checksum satisfy both rules at
once: unusable by a processor, speakable by the persona.

``INVARIANTS`` below records what is machine-checked. Street names are the one item that
cannot be fully verified offline; the check that exists is a curated list, and confirming
against open street data is a pre-launch step in ``docs/guardrails.md``.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from ssscammers.shared.validators import (
    aba_routing_valid,
    is_issuable_ssn,
    is_reserved_fictional_phone,
    luhn_valid,
)

__all__ = [
    "FictionIdentity",
    "generate_identity",
    "load_identity",
    "load_pack",
    "PACK_VERSION",
    "PACK_DIR",
]

PACK_VERSION = "v1"
PACK_DIR = Path(__file__).resolve().parents[2] / "data" / "fiction_pack" / PACK_VERSION

#: Card prefixes chosen to look plausible when read aloud while avoiding every
#: well-known test PAN (4111…, 4242…, 5555…, 378282…). The generated numbers fail
#: Luhn regardless, so the prefix is cosmetic — it exists so the digits sound real
#: to a scammer who is half-listening.
_CARD_PREFIXES: tuple[str, ...] = ("4539", "4716", "5312", "5427", "6011")

#: Invented street names. Compounds picked to be pronounceable and unremarkable on a
#: phone call while not naming a real street we could send someone to.
_STREET_NAMES: tuple[str, ...] = (
    "Pennyfarthing Close",
    "Widdicombe Rise",
    "Ashgrove Bellamy Way",
    "Old Thimble Lane",
    "Corbett Hollow Road",
    "Marlowe Pemberton Drive",
)

_BANK_NAMES: tuple[str, ...] = (
    "First Farmers & Mechanics Savings",
    "Pemberton County Mutual",
    "Old Harbour Thrift & Loan",
    "Cedarbrook Community Savings",
)

#: Plausible city/state/ZIP triples. Real cities keep the story believable; the
#: street is what guarantees the address is undeliverable.
_LOCALITIES: tuple[tuple[str, str, str], ...] = (
    ("Dayton", "OH", "45402"),
    ("Scranton", "PA", "18503"),
    ("Peoria", "IL", "61602"),
    ("Bakersfield", "CA", "93301"),
)

_FIRST_NAMES: tuple[str, ...] = (
    "Marjorie", "Harold", "Dorothy", "Reginald", "Eunice", "Walter",
    "Kevin", "Sharon", "Trevor", "Bernice", "Malcolm", "Gladys",
)
_LAST_NAMES: tuple[str, ...] = (
    "Pemberton", "Hollingsworth", "Whitcombe", "Ashby", "Fenwick", "Braithwaite",
)

#: What each invariant guarantees and whether it is machine-checked in CI.
INVARIANTS: dict[str, str] = {
    "card": "fails Luhn; never a published test PAN — CI-verified",
    "routing": "fails the ABA 3-7-1 checksum — CI-verified",
    "ssn": "area 900-999, never issued by the SSA — CI-verified",
    "phone": "NANP 555-0100..555-0199, reserved for fiction — CI-verified",
    "email": "example.com, reserved by RFC 2606 — CI-verified",
    "street": "curated invented names — NOT geocode-verified offline; see docs/guardrails.md",
}


@dataclass(frozen=True)
class FictionIdentity:
    """One persona's complete, invented personal history.

    Every field is something a scammer might fish for. Consistency matters as much as
    invalidity: a persona that names a different grandchild on the second ask is one a
    scammer notices.
    """

    persona_id: str
    full_name: str
    age: int
    date_of_birth: str
    street: str
    city: str
    state: str
    postal_code: str
    phone: str
    email: str
    bank_name: str
    routing_number: str
    account_number: str
    card_number: str
    card_expiry: str
    card_cvv: str
    ssn: str
    relatives: dict[str, str]
    pack_version: str = PACK_VERSION

    @property
    def address_line(self) -> str:
        return f"{self.street}, {self.city}, {self.state} {self.postal_code}"

    def to_prompt_block(self) -> str:
        """Render the identity as the fact sheet pasted into the persona's prompt.

        Written as things the character knows about itself rather than as a data table:
        the prompt reads better, and having no other personal facts is the property we
        want.
        """
        relatives = "; ".join(f"{role}: {name}" for role, name in self.relatives.items())
        return "\n".join(
            [
                f"Your name is {self.full_name}. You are {self.age} "
                f"(born {self.date_of_birth}).",
                f"You live at {self.address_line}.",
                f"Your phone number is {self.phone}. Your email, which you barely use, "
                f"is {self.email}.",
                f"You bank with {self.bank_name}. Routing {self.routing_number}, "
                f"account {self.account_number}.",
                f"Your card number is {self.card_number}, expiring {self.card_expiry}, "
                f"and the little number on the back is {self.card_cvv}.",
                f"Your social security number is {self.ssn}.",
                f"Your family: {relatives}.",
                "",
                "These are the only personal details you know. You have no others. If "
                "someone asks for something not listed here, you cannot find it, cannot "
                "remember it, or have written it down somewhere you cannot locate.",
            ]
        )


def _luhn_breaking(number: str) -> str:
    """Return ``number`` guaranteed to fail Luhn, nudging the last digit if needed."""
    if not luhn_valid(number):
        return number
    last = (int(number[-1]) + 1) % 10
    candidate = number[:-1] + str(last)
    if luhn_valid(candidate):  # pragma: no cover - a single step always breaks parity
        candidate = number[:-1] + str((last + 1) % 10)
    return candidate


def _checksum_breaking_routing(number: str) -> str:
    """Return a nine-digit string guaranteed to fail the ABA checksum."""
    if not aba_routing_valid(number):
        return number
    last = (int(number[-1]) + 1) % 10
    return number[:-1] + str(last)


def generate_identity(persona_id: str, *, seed: int | None = None) -> FictionIdentity:
    """Build one identity whose every number is provably unusable.

    Args:
        persona_id: Slug the identity belongs to.
        seed: Fixes the draw so details are stable across regenerations — a persona whose
            SSN changes between deployments contradicts itself on a scammer's second call.
    """
    rng = random.Random(seed if seed is not None else persona_id)

    first = rng.choice(_FIRST_NAMES)
    last = rng.choice(_LAST_NAMES)
    city, state, postal = rng.choice(_LOCALITIES)
    age = rng.randint(71, 86)
    birth_year = 2026 - age

    card = _luhn_breaking(
        rng.choice(_CARD_PREFIXES) + "".join(str(rng.randint(0, 9)) for _ in range(12))
    )

    # Nine-digit numbers wear two hats, and a value safe as one can be dangerous as the
    # other: a never-issued SSN can also be a checksum-valid routing number. Each is drawn
    # until it fails *both* tests, so it is safe whichever way a listener reads it.
    while True:
        # Area 900-999 was never issued by the SSA.
        ssn_digits = f"{rng.randint(900, 999)}{rng.randint(10, 99):02d}{rng.randint(1000, 9999)}"
        if not aba_routing_valid(ssn_digits):
            break
    ssn = f"{ssn_digits[:3]}-{ssn_digits[3:5]}-{ssn_digits[5:]}"

    while True:
        routing = _checksum_breaking_routing("".join(str(rng.randint(0, 9)) for _ in range(9)))
        if not is_issuable_ssn(routing):
            break
    # 555-0100..555-0199 is the NANP block reserved for fiction. Zero-padding the
    # subscriber keeps it four digits, so the whole number is a well-formed ten.
    phone = f"({rng.randint(200, 989)}) 555-{rng.randint(100, 199):04d}"

    return FictionIdentity(
        persona_id=persona_id,
        full_name=f"{first} {last}",
        age=age,
        date_of_birth=f"{rng.randint(1, 28):02d} {rng.choice(['March', 'June', 'September', 'November'])} {birth_year}",
        street=f"{rng.randint(1000, 9999)} {rng.choice(_STREET_NAMES)}",
        city=city,
        state=state,
        postal_code=postal,
        phone=phone,
        email=f"{first.lower()}.{last.lower()}@example.com",
        bank_name=rng.choice(_BANK_NAMES),
        routing_number=routing,
        account_number="".join(str(rng.randint(0, 9)) for _ in range(rng.randint(8, 11))),
        card_number=" ".join(card[i : i + 4] for i in range(0, 16, 4)),
        card_expiry=f"{rng.randint(1, 12):02d}/{rng.randint(26, 30)}",
        card_cvv=f"{rng.randint(100, 999)}",
        ssn=ssn,
        relatives={
            "son": f"{rng.choice(_FIRST_NAMES)} {last}",
            "grandson": rng.choice(["Kevin", "Trevor", "Malcolm", "Dennis"]),
            "granddaughter": rng.choice(["Sharon", "Bernice", "Gladys", "Nadine"]),
            "late spouse": rng.choice(["Doris", "Albert", "Edna", "Stanley"]),
        },
    )


def load_identity(persona_id: str) -> FictionIdentity:
    """Load a checked-in identity, generating it if the pack file is absent."""
    path = PACK_DIR / f"{persona_id}.json"
    if not path.exists():
        return generate_identity(persona_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    return FictionIdentity(**data)


def load_pack() -> dict[str, FictionIdentity]:
    """Load every identity in the checked-in pack."""
    return {path.stem: load_identity(path.stem) for path in sorted(PACK_DIR.glob("*.json"))}


def write_identity(identity: FictionIdentity) -> Path:
    """Write ``identity`` into the pack directory as formatted JSON."""
    PACK_DIR.mkdir(parents=True, exist_ok=True)
    path = PACK_DIR / f"{identity.persona_id}.json"
    path.write_text(json.dumps(asdict(identity), indent=2) + "\n", encoding="utf-8")
    return path


def assert_identity_safe(identity: FictionIdentity) -> None:
    """Raise ``AssertionError`` if any value in ``identity`` could be real or usable.

    Called by CI over the whole pack and by the generator before writing, so an identity
    cannot reach disk in a state the output filter would have to block.
    """
    assert not luhn_valid(identity.card_number), (
        f"{identity.persona_id}: card passes Luhn and would be blocked pre-TTS"
    )
    assert not aba_routing_valid(identity.routing_number), (
        f"{identity.persona_id}: routing number has a valid ABA checksum"
    )
    assert not is_issuable_ssn(identity.ssn), (
        f"{identity.persona_id}: SSN falls in an issued range"
    )
    # Cross-checks: a nine-digit value must be unusable read either way.
    assert not aba_routing_valid(identity.ssn), (
        f"{identity.persona_id}: SSN doubles as a checksum-valid routing number"
    )
    assert not is_issuable_ssn(identity.routing_number), (
        f"{identity.persona_id}: routing number doubles as an issuable SSN"
    )
    assert is_reserved_fictional_phone(identity.phone), (
        f"{identity.persona_id}: phone {identity.phone} is outside the 555-01XX block"
    )
    assert identity.email.endswith(("@example.com", "@example.org", "@example.net")), (
        f"{identity.persona_id}: email domain is not RFC 2606 reserved"
    )
    assert any(name in identity.street for name in _STREET_NAMES), (
        f"{identity.persona_id}: street is not from the curated invented list"
    )
