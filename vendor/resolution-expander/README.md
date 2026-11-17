# Resolution Expander

Pinned upstream: https://github.com/bwapi/bwapi/commit/9b871e2

Files downloaded from `Release_Binary/Chaoslauncher/Plugins` in the BWAPI
repository. Upstream integration and review:
https://github.com/bwapi/bwapi/pull/861

`ResolutionExpander.dll` SHA-256:
`05b50dd3e0257219362a8060a51deb916659a25d316119bea20a2b040e232bd7`

Original author attribution is retained in the game HUD. The original binary
is unmodified. `app/WideView.cpp` adapts the in-process module lookup at runtime,
with an image-size and instruction-signature check; the launcher verifies the
upstream SHA-256 before copying it into an isolated game directory. This avoids
the old window-title lookup / OpenProcess failure on the current machine.

The adapter also replaces the local StarCraft 1.16.1 import of Storm ordinal
313 (`SBltROP3`) for unflagged `SRCCOPY` operations with checked eight-bit row
copies (`app/SafeBlit.h`). This avoids the dynamically generated copy routine
seen in the 2026-11-17 user crash stack. Other raster operations still use
Storm. `validation/test_safe_blit.cpp` compares pixels with Storm across 20,000
cases and checks protected buffer boundaries. This is a targeted compatibility
workaround; the original intermittent crash was not deterministically reproduced.

The client records copy/rejection counts in
`bwapi-data/write/wide-view-render.json`. First-chance access violations are
recorded in `wide-view-fault.jsonl` for diagnosis; exceptions are never swallowed.
Native engine crash logs are surfaced as abnormal exits instead of ordinary
window closes.

Only the local player's display loads the extension. Opponent binaries and
the frozen economic/combat baseline are unchanged. `ResSettings.ini` lives
beside the copied DLL and pins all nine legacy zoom slots to one profile.

The bundled upstream `ResHackLoader.bwl` is retained for provenance; it is not
loaded or used by Commander Desktop.
