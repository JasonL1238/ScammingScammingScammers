# Legal posture

Not legal advice. This is the reasoning the system was built on, written down so the
assumptions are visible and can be corrected by someone qualified. Items marked
**[LAWYER]** are worth an hour of an actual attorney's time before the line goes live.

## Recording

The owner is in a one-party-consent state, and the agent is a party to every call it
answers — the same footing as voicemail, an answering service, or a call-screening
app. Federal law (18 U.S.C. § 2511(2)(d)) permits recording with one party's consent
where the purpose is neither criminal nor tortious; documenting attempted fraud
against your own phone line is neither.

The complication is that the caller may be in an all-party-consent state, and courts
have not settled whose law governs an interstate call. Rather than needing to win that
argument, the system moots it: **every call opens with an audible recorded-line
notice, played from TwiML before the agent speaks at all.** A caller who continues
after that has consented under even the stricter rule. This is the same mechanism
every call centre in the country relies on.

The notice is not a config option and not something the model is asked to say. It is a
pre-rendered clip in the persona's voice, played by Twilio ahead of the media stream,
so no prompt failure can drop it.

**[LAWYER]** Whether the bot-as-party theory holds in the owner's specific state.

## Robocall rules

The TCPA and the FCC's 2024 ruling on AI-generated voices regulate **calls placed to
people** — autodialing, prerecorded and AI voices, consent, opt-out. Answering an
inbound call the caller chose to place does not engage any of it.

This is the single reason the inbound-only rule is architectural rather than
stylistic. One outbound call or one text with an AI voice steps into the most actively
litigated consumer-protection statute in the country, with statutory damages per call
and a private right of action. So the system cannot place one: the Twilio subaccount
has outbound disabled, no code path constructs an outbound request, and CI fails the
build if one appears.

## Bot-disclosure statutes

Surveyed and, as written, inapplicable — they target commercial chatbots, outbound AI
robocalls, and election deepfakes:

- **California B.O.T. Act** applies to bots communicating with intent to mislead in
  order to incentivise a commercial transaction or influence a vote. Nothing is being
  sold, bought, or voted on here, and enforcement is attorney-general-only.
- **Utah AI Policy Act** attaches in consumer-transaction and regulated-profession
  contexts. This is neither.
- **California SB 243** governs companion-chatbot products offered to users. The
  callers are not users of a product.

The system nonetheless discloses, by design, to everyone except an active fraud
attempter mid-scam: a real caller, an allowlisted number, the owner's safeword, a DTMF
escape, or a scam victim calling back all trigger a truthful, fixed disclosure within
one turn. The only people left undisclosed-to are the ones every one of these statutes
was written to protect *from*.

**[LAWYER]** This posture, and a re-read of the state tracker annually — the area is
moving quickly.

## Why inbound-only keeps this clean

Scambaiting is lawful. The ways scambaiters get into trouble are initiating contact,
threats and abuse, doxxing, hacking back, and collateral damage to innocent people.
Each is removed structurally rather than by policy:

- **Every conversation is one they started.** There is no course of conduct directed
  at anyone; the only repeatable act on this side is answering a phone.
- **Nothing is taken and nothing is given.** No real data leaves, no money moves, no
  account is touched, nothing is installed. The entire output is conversation.
- **The tone ceiling is enforced.** Mildly annoying, never cruel — including after
  they swear at the persona, which they will.
- **Misroutes are a first-class path.** Conditional call forwarding *guarantees* real
  people land here, so releasing them quickly is a tested requirement, not a nicety.

## Data handling

Recordings contain the voices of real people, sometimes including a third party a
scammer names, and occasionally an innocent caller who dialled the wrong number.

- Scam-call audio is kept; **audio from a call classified legit is deleted within
  seven days** and its transcript truncated. That default is asserted by a test.
- Recordings move to owner-controlled storage and are deleted from Twilio, so there is
  one copy under one retention policy — and a platform suspension cannot destroy the
  archive.
- No voiceprints, no speaker identification, no cross-call biometric tracking. Caller
  correlation uses the phone number and nothing else.
- Nothing is published. Some calls will be funny; that is not a reason to put a
  stranger's voice on the internet, and misidentifying someone as a scammer in public
  is a real harm with a real cause of action attached.

**[LAWYER]** What is owed when a scammer reads a third party's real details aloud on a
recorded call. Redaction is what the system does; whether more is required is a
question for someone qualified.

## Platform

Twilio's acceptable-use policy is shaped around outbound abuse: robocalling, traffic
pumping, phishing, harassment. An inbound-only number that answers its own calls
politely, plays a recording notice, never dials out, never impersonates an
institution, and never solicits credentials is close to the lowest-risk voice
application on the platform. Outbound geographic permissions stay disabled, which also
removes the toll-fraud vector entirely.

## What will not be built

Recorded here so a future improvement does not quietly become a liability: no outbound
capability of any kind, no mass or campaign dialing, no scammer doxxing or voiceprint
tracking, no harvesting of scammer-side payment details, no hack-back or device
access, no impersonation of real institutions or people, no abusive personas, and no
hosting of other people's honeypots — that last one is a different and much heavier
compliance project than a person's own phone line.
