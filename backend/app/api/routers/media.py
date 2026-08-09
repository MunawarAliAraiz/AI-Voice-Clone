"""
Media serving — signed, expiring, range-capable.

An `<audio>` element cannot send an API key, so these routes are exempt from the
key check and authenticate with the `?t=` signature instead. The token is bound
to `{kind}/{id}`, so it cannot be replayed against another item, and it expires.
`FileResponse` handles Range requests, so the player can seek.

Responses are `Content-Disposition: inline` by default. This is not cosmetic:
mobile Safari and Android Chrome honour `attachment` on a media element's load
and refuse to play the resource, offering a download instead — the play button
appears dead while the download button works. Desktop Chrome and Firefox ignore
the header for `<audio src>`, which is why an `attachment`-always server looks
perfectly fine until someone opens the app on a phone.

`?download=1` opts back into `attachment`, and the download button needs it: the
HTML `download` attribute is ignored on cross-origin URLs, and the deployed
setup is exactly that (frontend on Pages/Workers, backend on ngrok). Without a
server-side attachment path there is no way to force a save.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from ...config import Settings
from ...db import Database
from ...exceptions import HistoryNotFoundError, MediaTokenError, ProfileNotFoundError
from ..deps import get_db, get_settings
from ..media_tokens import verify_token

router = APIRouter(prefix="/media", tags=["media"])

#: Explicit, because `mimetypes` is platform-dependent — it maps `.wav` to
#: `audio/x-wav` on Linux and consults the registry on Windows, and Safari is
#: the pickiest consumer of what we send back. Anything not listed falls through
#: to `FileResponse`'s own guess.
_MEDIA_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".m4a": "audio/mp4",
}


@router.get("/{kind}/{item_id}")
async def get_media(
    kind: str,
    item_id: str,
    t: str,
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    download: bool = False,
    format: str | None = None,
) -> FileResponse:
    # Verify BEFORE any lookup, so a bad token reveals nothing about existence.
    verify_token(f"{kind}/{item_id}", t, settings.media_token_secret)

    if kind == "voice":
        row = await db.get_profile(int(item_id))
        if row is None:
            raise ProfileNotFoundError(int(item_id))
        path = row["audio_path"]
    elif kind == "history":
        row = await db.get_generation(int(item_id))
        if row is None:
            raise HistoryNotFoundError(int(item_id))
        path = row["output_path"]
    elif kind == "voice_edit":
        path = str(settings.voices_dir / f"{item_id}.wav")
    else:
        raise MediaTokenError(f"Unknown media kind {kind!r}.")

    src_path = Path(path)
    if not src_path.exists():  # noqa: ASYNC240 (one-shot stat)
        raise (HistoryNotFoundError if kind == "history" else ProfileNotFoundError)(item_id)

    serve_path = src_path
    if format:
        target_ext = f".{format.lower().lstrip('.')}"
        if target_ext in _MEDIA_TYPES and src_path.suffix.lower() != target_ext:
            converted_path = src_path.with_suffix(f".fmt_{format.lower().lstrip('.')}{target_ext}")
            if not converted_path.exists():
                cmd = ["ffmpeg", "-y", "-i", str(src_path), str(converted_path)]
                try:
                    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                except FileNotFoundError:
                    # ffmpeg missing from PATH: serve the original format rather
                    # than 500 — a wrong-but-playable file beats a hard failure.
                    res = None
                if res is not None and res.returncode == 0 and converted_path.exists():
                    serve_path = converted_path
            else:
                serve_path = converted_path

    stem = src_path.stem
    name = f"{stem}{serve_path.suffix}"
    disposition = "attachment" if download else "inline"
    return FileResponse(
        serve_path,
        media_type=_MEDIA_TYPES.get(serve_path.suffix.lower()),
        headers={"Content-Disposition": f'{disposition}; filename="{name}"'},
    )

