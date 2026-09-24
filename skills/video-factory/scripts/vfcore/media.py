"""Per-scene video clips with ffmpeg.

Every scene is rendered to its own clip with identical encoder settings, then the
clips are joined by the concat demuxer without re-encoding. That keeps peak memory
to one scene at a time, lets a timed-out render resume at the next scene, and
halves encode time compared with re-encoding the joined timeline.

Pan-and-zoom runs zoompan on a picture pre-scaled to OVERSAMPLE x the frame
size, from a single decoded frame: zoompan rounds its crop origin to whole input
pixels, so oversampling is what keeps slow motion from visibly stepping.
Measured in the container (5 s of 1080x1920, veryfast): 2x = 5.5 s, 1.5x = 3.7 s wall;
zoompan itself is the cost (x264 preset barely matters).
"""

from __future__ import annotations

from pathlib import Path

from .formats import FPS, FormatSpec
from .paths import FONTS_DIR
from .util import run

OVERSAMPLE = 1.5          # measured: 1.5x renders ~35% faster than 2x; steps stay < 1 px
X264_COMMON = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-profile:v", "high",
               "-pix_fmt", "yuv420p", "-g", str(FPS), "-bf", "2", "-r", str(FPS),
               "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
               "-color_range", "tv", "-threads", "4"]
AUTO_IMAGE_MOTIONS = ("zoom_in", "pan_right", "zoom_out", "pan_left", "zoom_in", "pan_up")


def _ffmpeg(args: list[str], timeout: float = 540) -> None:
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], timeout=timeout)


def _ass_filter(ass_path: Path) -> str:
    # Filter-graph escaping: the path goes inside single quotes; ':' and '\\' must be escaped.
    def esc(p: Path) -> str:
        return p.as_posix().replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return f"ass='{esc(ass_path)}':fontsdir='{esc(FONTS_DIR)}'"


def _to_video(fmt: FormatSpec) -> str:
    return f"scale={fmt.width}:{fmt.height}:out_color_matrix=bt709:out_range=tv,format=yuv420p"


def resolve_motion(kind: str, motion: str, index: int) -> str:
    if motion and motion != "auto":
        return motion
    if kind == "card":
        return "zoom_in"
    if kind == "screenshot":
        return "scroll"
    return AUTO_IMAGE_MOTIONS[index % len(AUTO_IMAGE_MOTIONS)]


def prepare_still(src: Path, dst: Path, fmt: FormatSpec) -> None:
    """Cover-crop any picture to the frame aspect at OVERSAMPLE x resolution."""
    w, h = int(fmt.width * OVERSAMPLE), int(fmt.height * OVERSAMPLE)
    _ffmpeg(["-i", str(src), "-frames:v", "1", "-vf",
             f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},format=rgb24",
             str(dst)], timeout=120)


def _zoompan(motion: str, frames: int, fmt: FormatSpec, strength: float) -> str:
    last = max(1, frames - 1)
    ease = f"(on/{last})*(on/{last})*(3-2*(on/{last}))"   # smoothstep
    centre_x, centre_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    pan = 1 + strength * 1.2
    if motion == "zoom_in":
        z, x, y = f"1+{strength}*{ease}", centre_x, centre_y
    elif motion == "zoom_out":
        z, x, y = f"1+{strength}*(1-{ease})", centre_x, centre_y
    elif motion == "pan_left":
        z, x, y = f"{pan}", f"(iw-iw/zoom)*(1-{ease})", centre_y
    elif motion == "pan_right":
        z, x, y = f"{pan}", f"(iw-iw/zoom)*{ease}", centre_y
    elif motion == "pan_up":
        z, x, y = f"{pan}", centre_x, f"(ih-ih/zoom)*(1-{ease})"
    elif motion == "pan_down":
        z, x, y = f"{pan}", centre_x, f"(ih-ih/zoom)*{ease}"
    else:  # still
        z, x, y = "1", "0", "0"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={fmt.width}x{fmt.height}:fps={FPS}"


def render_still_clip(prepared: Path, out: Path, ass_path: Path, frames: int, motion: str,
                      fmt: FormatSpec, *, strength: float) -> None:
    graph = f"[0:v]{_zoompan(motion, frames, fmt, strength)},{_ass_filter(ass_path)},{_to_video(fmt)}[v]"
    _ffmpeg(["-i", str(prepared), "-filter_complex", graph, "-map", "[v]", "-frames:v", str(frames),
             *X264_COMMON, "-an", str(out)])


def render_scroll_clip(tall: Path, out: Path, ass_path: Path, frames: int, fmt: FormatSpec,
                       motion: str) -> None:
    """A page screenshot scrolled top to bottom (or held still), captions on top.

    The picture is decoded once and repeated in memory by `loop`; `crop` moves a
    frame-sized window down it with an eased curve that holds briefly at both ends.
    """
    last = max(1, frames - 1)
    p = f"clip((n/{last}-0.12)/0.76,0,1)"
    ease = f"({p})*({p})*(3-2*({p}))"
    y = "0" if motion == "still" else f"(ih-oh)*{ease}"
    fit = (f"scale={fmt.width}:-2:flags=lanczos,"
           f"pad=iw:max(ih\\,{fmt.height}):0:(oh-ih)/2:color=0x0B1026")
    graph = (f"[0:v]{fit},loop=loop={frames - 1}:size=1:start=0,setpts=N/{FPS}/TB,"
             f"crop={fmt.width}:{fmt.height}:0:'{y}',{_ass_filter(ass_path)},{_to_video(fmt)}[v]")
    _ffmpeg(["-i", str(tall), "-filter_complex", graph, "-map", "[v]", "-frames:v", str(frames),
             *X264_COMMON, "-an", str(out)])


def concat_clips(clips: list[Path], list_file: Path, out: Path) -> None:
    list_file.write_text("".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8")
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out)])


def mux(video: Path, audio: Path, out: Path) -> None:
    _ffmpeg(["-i", str(video), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
             "-c:v", "copy", "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(out)])


REVIEW_AUDIO_KBPS = 128           # Reels asks for AAC-LC stereo 48 kHz at 128 kbps or more
REVIEW_GOP = 2 * FPS              # Reels asks for a closed GOP of 2-5 s
# Below these video bitrates the full-size cut looks worse than a smaller one of the same size.
FULL_SIZE_MIN_KBPS = {"short": 1300, "square": 1100, "long": 2200}
SMALL_SIZE = {"short": (720, 1280), "square": (720, 720), "long": (1280, 720)}


# The review cut is the cut a person reviews in chat, and the exact file published as
# a Reel. Approval binds the chat attachment's bytes, so this file is what goes public:
# it must be publishable (H.264 at a fixed frame rate, 2 s closed GOP, AAC-LC stereo
# 48 kHz, at least 540x960) and still fit one chat upload. Two-pass ABR spends exactly
# the size budget. Measured on a 41.7 s short at 9 MB: 1080p veryfast scored SSIM
# 0.9795 against the master in 46 s, 720p 0.9751, and the slower presets were no
# better. The passes are separate calls so a render can stop between them and pick
# up at pass 2 (the render module keeps that state).
REVIEW_BITRATE_FACTORS = (1.0, 0.92, 0.84)   # retried in order while the cut is over the size limit


def review_cut_size(fmt: FormatSpec, duration: float, max_bytes: int) -> tuple[int, int, int]:
    """(width, height, video kbps) that spend max_bytes on duration seconds."""
    budget = int(max_bytes * 8 / 1000 / max(duration, 1.0) * 0.96) - REVIEW_AUDIO_KBPS
    width, height = fmt.width, fmt.height
    if budget < FULL_SIZE_MIN_KBPS[fmt.name]:
        width, height = SMALL_SIZE[fmt.name]
    return width, height, budget


def review_cut_pass(master: Path, out: Path, width: int, height: int, video_kbps: int, workdir: Path,
                    pass_no: int) -> None:
    """One x264 pass. Pass 1 leaves its statistics in workdir for pass 2."""
    common = ["-vf", f"scale={width}:{height}:flags=lanczos", "-c:v", "libx264", "-preset", "veryfast",
              "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(FPS), "-g", str(REVIEW_GOP),
              "-keyint_min", str(REVIEW_GOP), "-sc_threshold", "0",
              "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
              "-color_range", "tv", "-threads", "4", "-passlogfile", str(workdir / "reviewcut"),
              "-b:v", f"{video_kbps}k", "-maxrate", f"{int(video_kbps * 1.5)}k", "-bufsize", f"{video_kbps * 3}k"]
    if pass_no == 1:
        _ffmpeg(["-i", str(master), *common, "-pass", "1", "-an", "-f", "null", "-"])
    else:
        _ffmpeg(["-i", str(master), *common, "-pass", "2", "-c:a", "aac", "-b:a", f"{REVIEW_AUDIO_KBPS}k",
                 "-ac", "2", "-ar", "48000", "-movflags", "+faststart", str(out)])


def has_review_passlog(workdir: Path) -> bool:
    return (workdir / "reviewcut-0.log").exists() and (workdir / "reviewcut-0.log.mbtree").exists()


def clear_review_passlog(workdir: Path) -> None:
    for leftover in workdir.glob("reviewcut*"):
        leftover.unlink(missing_ok=True)


def extract_frame(video: Path, at: float, out: Path, width: int | None = None) -> None:
    vf = ["-vf", f"scale={width}:-2"] if width else []
    _ffmpeg(["-ss", f"{max(0.0, at):.3f}", "-i", str(video), "-frames:v", "1", *vf, "-q:v", "3", str(out)],
            timeout=60)
