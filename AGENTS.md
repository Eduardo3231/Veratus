# Veratus workspace guidance

## Scope and source of truth

- This repository supports the Veratus landing page and related operations. Compare the public site and the current `landing/` source before changing or advertising copy, media, prices, or calls to action. If they differ, keep paid work as a draft and report the mismatch.
- `README.md`, `automations/`, and older branding templates may describe an earlier electronics/pre-sale workflow. Do not reuse those claims in watch sales or advertising without checking the current landing page and the user's latest instruction.
- Preserve unrelated dirty changes. Start with `git status` and limit edits to the requested scope.

## Brand

- Brand character: premium, precise, elegant, restrained.
- Palette: obsidian `#0D0D0D`, charcoal `#2B2B2B`, ash `#8A867F`, ivory `#F6F4F0`, gold `#C8A56A`.
- The Veratus symbol is a V with wheat. Preserve the wheat, clear space, and legibility; use the existing assets in `landing/assets/` where appropriate.
- Use clear Brazilian Portuguese. Avoid inflated claims, artificial urgency, unsupported scarcity, and copied third-party branding claims.

## Commerce and marketing safeguards

- Customer-facing claims about product origin, authenticity, authorization, warranty, technical specs, stock, freight, price, and delivery must be supported for the exact item.
- Do not put personal data, credentials, payment information, leads, or verification codes in the site, Git history, campaign copy, or screenshots.
- Test mobile layout, CTA links, legal links, and the complete contact/purchase handoff before recommending a paid launch.
- Use a paused campaign draft by default. Publishing—even a paused item, which submits it to Meta review—deleting, activating, increasing budget, or setting a spending cap always needs a final explicit confirmation at action time.

## Verification

- Do not declare a Render deploy, Meta linkage, Pixel, payment method, WhatsApp channel, or campaign as working until it is checked in the relevant live system.
- After customer-facing edits, run proportionate local checks and report the exact result and any remaining live test.

## One agent per working tree

- Before editing any file, run `python scripts/agent_lock.py acquire --agent <name> --scope "<what you will change>"`. If it prints `OCUPADA`, do not edit; report the holder and stop.
- Renew with the same command during long sessions and run `python scripts/agent_lock.py release --agent <name>` when you finish.
- Never revert, reformat, or "restore" a file you did not change in this session; report it instead.
- Read `docs/HANDOFF.md` first and update it before releasing: what changed, verification run, open decisions, next step.
