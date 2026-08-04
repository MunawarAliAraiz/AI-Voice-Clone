"""
AI Voice Clone Studio — Domain layer.

Pure logic: language and script primitives, routing, text segmentation.

Rules for everything in this package:
  * No I/O. No filesystem, no network, no database, no clock.
  * No torch, and nothing that transitively imports it.
  * No knowledge of what is loaded, running, or resident.

  * No import of `app.inference`, or of any other layer. The dependency arrow
    runs `inference -> domain`, one way. What routing needs from a catalog is
    expressed as a protocol in `ports.py` instead — see the note there; the
    alternative is a genuine circular import, not merely an ugly one.

Every function here should be testable with a literal input and a literal
expected output, on a machine with no GPU, in microseconds. If something in this
package needs to `await`, it is in the wrong package.
"""

from .language import LanguageCode, Script, TextProfile, detect_script, profile_text
from .ports import CatalogView, SpecView
from .routing import RoutePlan, TextTransform, TransformKind, UrduStrategy, resolve
from .text import TextChunk, chunk_for_synthesis, split_sentences

__all__ = [
    "Script",
    "LanguageCode",
    "TextProfile",
    "detect_script",
    "profile_text",
    "SpecView",
    "CatalogView",
    "TransformKind",
    "UrduStrategy",
    "TextTransform",
    "RoutePlan",
    "resolve",
    "TextChunk",
    "split_sentences",
    "chunk_for_synthesis",
]
