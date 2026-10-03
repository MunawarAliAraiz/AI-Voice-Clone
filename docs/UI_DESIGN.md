# Desktop workspace design

## Direction

The studio is an audio workspace. The script editor is the main surface, with
saved voices and reference recording tools in a supporting column. Other
sections, including Runpod setup, use the full content width.

Use a quiet ink background, flat charcoal surfaces, offwhite primary actions,
fine dividers, and a small warm selection accent. The colourful liquid orb is
the concentrated colour and motion in the workspace. Keep headings concise,
controls labelled, and task status explicit in words. An animated orb never
replaces a progress value or an explanation of blocked generation.

Avoid marketing headlines, gradient backgrounds, glowing card grids,
decorative badges, emoji navigation, remote fonts, and repeated entrance
animations. Preserve the user's script and in-flight job state when changing
sections.

## Tokens and layout

- `frontend/src/styles/variables.css` is the shared token source.
- `frontend/src/App.css` contains the workspace layout and control styles.
- System fonts prefer Segoe UI Variable/Segoe UI on Windows and fall back
  locally; the app does not fetch fonts.
- Body and control text are 14–15 px, with 12 px reserved for compact metadata.
- The editor/support grid is `minmax(0, 1fr) 320px`, stacking below 980 px.
- At intermediate widths, support panels can share a row. Below 640 px they
  stack. The navigation scrolls within its own row without widening the page.
- `workspace-heading`, `workspace-heading-copy`, and `workspace-heading-tools`
  provide a compact section introduction.
- `studio-main` and `studio-support` place editor and supporting tools.
- `composer-heading`, `generation-action`, and `generation-state` support the
  compact composer status area.
- Runpod model rows use measured progress and fine dividers rather than
  nested cards. Wizard footer actions have a consistent size and alignment.
- Costs, durations, byte counts, and progress use tabular numerals.

## Accessibility and motion

Text and status labels remain legible on every defined opaque surface.
Disabled controls retain readable labels; the adjacent reason explains what
must be done. Focus uses an opaque warm ring. Text inputs, secondary actions,
upload areas, and search fields have identifiable boundaries.

Primary actions have 44 px minimum height. Compact desktop icon tools are
32–40 px; touch/coarse-pointer rules enlarge the controls. The layout does not
set a document-level direction: Urdu and mixed-script text retain their own
`dir` handling. Hidden mounted panels explicitly use `display: none`.

Reduced-motion preferences shorten ordinary transitions and stop repeating
animations. The orb follows the same preference and must not animate while
the document is hidden. Status changes must correspond to real queue,
generation, or playback state, not imply microphone analysis or GPU readiness.

## Source verification, 2026-10-03

The strict CSS token check passed with 51 declared tokens. WCAG relative
luminance calculations against all four opaque background/surface tokens:

| Foreground | Minimum contrast |
| --- | ---: |
| Main text | 13.46:1 |
| Muted text | 7.59:1 |
| Secondary hints | 5.95:1 |
| Warm selection text | 8.45:1 |
| Success text | 8.88:1 |
| Error text | 7.88:1 |
| Warning text | 9.20:1 |
| Primary action text | 15.17:1 |
| Control boundary | 3.29:1 |

These are token-level calculations, not a claim that every composited element
or native app window has passed visual review. Browser checks should verify
actual layout at wide, intermediate, and narrow widths, all sections, focus
visibility, reduced motion, real per-model progress, and the desktop update
dialog. Paid generation and installed native behaviour need separate tests.

## Browser review, 2026-10-03

An isolated Vite preview used the real App and components with all fetches
handled locally. No provider was contacted and no audio was fabricated.
Browser WebGL successfully drew all seven orb states (80 px buffers at the
preview's device scale). The actual desktop layout places the script editor
before delivery controls and the voice tools. Draft text survived leaving
Studio for Runpod and returning. End-key navigation selected and focused Agents.

Document widths were within 375, 768, 1024 and 1440 px test viewports. Review
found and fixed the narrow voice form's intrinsic width and an invisible tooltip
that widened the page. Explanations are now placed inside the viewport on hover
or keyboard focus. The Runpod model page fills the content width and keeps its
named measured progress row. Model states in this preview are fixtures, not a
live download test. Reduced-motion/hidden/offscreen behavior passed mocked
lifecycle tests; native app settings and update/restart were not operated.

Screenshots: `D:/Projects/AI-Voice-Clone/build/ui-review/` (studio-desktop.png,
studio-narrow.png, models-desktop.png, orb-states.png). The frontend build,
15 real-state checks, orb lifecycle, and existing setup/gate/update view checks
passed. Installation and real GPU inference remain separate qualification.
