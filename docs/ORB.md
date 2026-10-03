# Liquid orb

`frontend/src/components/LiquidOrb.tsx` exports `LiquidOrb` and
`LiquidOrbState`: `idle`, `queued`, `generating`, `complete`, `blocked`, `error`,
and `paused`. Pass `state` and an optional `className`. Set the CSS property
`--liquid-orb-size` on the component or its parent to choose its displayed size.
Its default is 64 px, with a fixed square footprint to avoid moving controls.

This is an original procedural WebGL material, with a static CSS sphere when
WebGL is unavailable or its context is lost. It uses no third-party orb code,
textures, network access, ElevenLabs service, audio input, or microphone access.
The orb is decorative (`aria-hidden`); readable generation status must be shown
next to it. Movement does not indicate measured progress and cannot replace a
progress bar or an error message.

## Performance and accessibility

- Drawing buffer never exceeds 192 × 192 px; device pixel ratio is capped at 2.
- Maximum draw rate: generation 24 fps, queued 12 fps, idle 8 fps.
- Completed, blocked, failed, and paused states are static.
- Reduced motion draws a static material, including when the preference changes.
- Hidden documents and elements outside the viewport stop scheduling frames.
- Offscreen or hidden time does not advance the animation.
- Shader, buffer, program, observers, listeners, and animation callbacks are
  cleaned up on unmount or state change. Context loss immediately shows fallback.
- The orb is hidden in forced-colour mode. Status remains readable without it.

The native desktop uses the same React component. Orb activity must be derived
from the application's real queue/status state; it must not imply a connected
Runpod account, available GPU, downloaded weights, or successful audio output.
