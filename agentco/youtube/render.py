"""ffmpeg assembler: shots -> one 1080x1920 (9:16) Short with captions, loudness-normalised audio.

Security: uploaded media is UNTRUSTED. Every ffmpeg input is opened with -protocol_whitelist file,pipe, so a crafted
file cannot make ffmpeg fetch a network URL; commands are argument lists (no shell); every run has a timeout; assets
are probed first and must be real video/image streams."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

W, H, FPS = 1080, 1920, 30
MAX_SECONDS = 59.5
VOICE_TAIL = 0.25
MIN_BEAT = 2.5
WPS = 2.6                       # spoken words per second (~156 wpm)
BASE = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
WL = ["-protocol_whitelist", "file,pipe"]


class RenderError(Exception):
    pass


@dataclass
class RenderResult:
    file: Path
    preview: Path
    duration: float


def _run(cmd: list[str], timeout: int = 240) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RenderError("ffmpeg took too long and was stopped")
    if r.returncode != 0:
        raise RenderError("ffmpeg failed: " + (r.stderr or "")[-300:].strip())
    return r.stdout


def probe(path: Path) -> dict:
    out = _run(["ffprobe", "-v", "error", *WL, "-show_entries", "stream=codec_type,width,height:format=duration",
                "-of", "json", str(path)], 30)
    d = json.loads(out or "{}")
    streams = d.get("streams", [])
    return {"duration": float(d.get("format", {}).get("duration") or 0),
            "has_video": any(s.get("codec_type") == "video" for s in streams),
            "w": max([s.get("width", 0) for s in streams] or [0]), "h": max([s.get("height", 0) for s in streams] or [0])}


def check_asset(path: Path, kind: str) -> None:
    try:
        p = probe(path)
    except RenderError:
        raise RenderError("could not read this file as video/image")
    if not p["has_video"] or p["w"] < 100 or p["h"] < 100:
        raise RenderError("file has no usable picture")
    if kind == "video" and p["duration"] > 180:
        raise RenderError("clip is longer than 3 minutes")


def beat_seconds(spoken: str, voice_file: Path | None) -> float:
    if voice_file and voice_file.exists():
        return probe(voice_file)["duration"] + VOICE_TAIL
    return max(MIN_BEAT, len(spoken.split()) / WPS + 0.4)


def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _clean(t: str, limit: int) -> str:
    return re.sub(r"[{}\\\r\n]+", " ", t or "").strip()[:limit]


def build_ass(beats: list[dict]) -> str:
    """Top label = the on-screen text (different words from the voice); bottom caption = the spoken line."""
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\n\n[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,"
            "ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            "Style: Top,DejaVu Sans,78,&H0000E5FF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,8,70,70,260,1\n"
            "Style: Cap,DejaVu Sans,62,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,5,2,2,80,80,470,1\n\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    lines, t = [], 0.0
    for b in beats:
        end = t + b["seconds"]
        if b.get("on_screen"):
            lines.append(f"Dialogue: 0,{_ass_time(t)},{_ass_time(end)},Top,,0,0,0,,{_clean(b['on_screen'], 90)}")
        if b.get("spoken"):
            lines.append(f"Dialogue: 0,{_ass_time(t + 0.1)},{_ass_time(end - 0.1)},Cap,,0,0,0,,{_clean(b['spoken'], 200)}")
        t = end
    return head + "\n".join(lines) + "\n"


def _segment(out: Path, kind: str | None, asset: Path | None, voice: Path | None, secs: float) -> None:
    cmd = list(BASE)
    if kind == "video":
        cmd += ["-stream_loop", "-1", *WL, "-i", str(asset)]
        vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1,format=yuv420p"
    elif kind == "image":
        cmd += ["-loop", "1", *WL, "-i", str(asset)]
        vf = (f"scale=1350:2400:force_original_aspect_ratio=increase,crop=1350:2400,zoompan=z='min(1.0+0.0008*on,1.12)':"
              f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS},setsar=1,format=yuv420p")
    else:                                                    # placeholder brand card
        cmd += ["-f", "lavfi", "-i", f"color=c=0x0f172a:s={W}x{H}:r={FPS}"]
        vf = "setsar=1,format=yuv420p"
    cmd += ([*WL, "-i", str(voice)] if voice else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])
    cmd += ["-t", f"{secs:.3f}", "-vf", vf, "-af", "aresample=44100,apad", "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(FPS), "-c:a", "aac", "-ar", "44100", "-ac", "2", str(out)]
    _run(cmd)


def render(work: Path, shots: list[dict], allow_placeholders: bool) -> RenderResult:
    """shots: dicts with beat_no, spoken, on_screen, asset (Path|None), asset_kind, voice (Path|None). Returns the files."""
    work.mkdir(parents=True, exist_ok=True)
    beats = []
    for sh in sorted(shots, key=lambda x: x["beat_no"]):
        if sh.get("asset") is None and not allow_placeholders:
            raise RenderError(f"beat {sh['beat_no']} has no picture yet (add one, or allow placeholder cards)")
        if sh.get("asset") is not None:
            check_asset(sh["asset"], sh["asset_kind"])
        beats.append({**sh, "seconds": beat_seconds(sh["spoken"], sh.get("voice"))})
    total = sum(b["seconds"] for b in beats)
    if not beats:
        raise RenderError("there are no beats to render")
    if total > MAX_SECONDS:
        raise RenderError(f"the video would be {total:.0f}s; Shorts must stay under 60s. Shorten the script")
    segs = []
    for b in beats:
        seg = work / f"seg_{b['beat_no']}.mp4"
        _segment(seg, b["asset_kind"] if b.get("asset") else None, b.get("asset"), b.get("voice"), b["seconds"])
        segs.append(seg)
    listing = work / "list.txt"
    listing.write_text("".join(f"file '{s.as_posix()}'\n" for s in segs))
    joined = work / "joined.mp4"
    _run([*BASE, "-f", "concat", "-safe", "0", *WL, "-i", str(listing), "-c", "copy", str(joined)])
    ass = work / "captions.ass"
    ass.write_text(build_ass(beats), encoding="utf-8")
    final, prev = work / "final.mp4", work / "preview.jpg"
    _final(work, joined, ass, final)
    _run([*BASE, "-ss", "1", *WL, "-i", str(final), "-frames:v", "1", "-q:v", "3", str(prev)], 60)
    return RenderResult(final, prev, probe(final)["duration"])


def _final(work: Path, joined: Path, ass: Path, final: Path) -> str:
    # run inside the work dir so the subtitles filter can use a plain relative file name (no path escaping)
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *WL, "-i", joined.name, "-vf", f"ass={ass.name}",
           "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", final.name]
    try:
        r = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise RenderError("ffmpeg took too long and was stopped")
    if r.returncode != 0:
        raise RenderError("ffmpeg failed: " + (r.stderr or "")[-300:].strip())
    return ""
