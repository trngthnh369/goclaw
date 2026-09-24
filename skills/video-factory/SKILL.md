---
name: video-factory
description: Deterministic short-video production for the Video Factory team - topic to researched, fact-checked script, Vietnamese voice-over with word-synced karaoke captions, AI images and HTML cards with motion, loudness-normalised master plus a Reels-ready review cut and per-platform captions. Agents write research, scripts and review verdicts; this skill validates, renders, checks and packages.
license: Internal
metadata:
  author: trngthnh369
  version: "0.2.0"
  bundle_revision: "2026-09-24-014"
  runtime: python3
  forked_from: comingwave-study
---

# Video Factory

Turns a topic into a finished vertical (9:16), horizontal (16:9) or square video for TikTok, Facebook / Instagram Reels and YouTube.
Three agents work it: `vf-director` (lead: runs this skill, generates pictures, renders, delivers), `vf-scriptwriter` (research + script) and `vf-reviewer` (fact check + visual QA).

The split follows the rule the other GoClaw pipelines paid for: Python owns everything mechanical, the model only reasons over artifacts.
`internal/pipeline/think_stage.go` ends an agent run on any turn without a tool call, so no stage may depend on a model remembering the next step.

## The five invariants

**1. The stage is derived, never stored.**
`studio.py next` looks at which artifacts exist AND validate, and at reviews bound to the sha of what they reviewed.
A run that dies mid-stage resumes where it stopped; a truncated artifact is simply not done yet.

**2. Artifacts reach the model through `exec` + stdout.**
`emit` prints everything a stage needs.
`read_file` caps at 50 000 chars and `exec` at 30 000, both silently, and predefined agents are workspace-restricted.

**3. A review is bound to what it reviewed.**
The script review covers research plus everything spoken or shown as text; image prompts and motion are excluded from that hash, so re-rolling a picture never reopens the fact check.
The video review is bound to the master file's sha.
A review with a verdict the evidence does not support is rejected at submit (PASS with a blocker, REVISE with nothing serious, unchecked facts or scenes).
The first well-formed model verdict for a version is final: the first live run recorded REVISE, PASS, REVISE, REVISE, PASS on one script inside a single reviewer call, and the last one used to win.

**4. What a human approves is what gets published.**
The skill ends at one review message: the review cut attached, and the Reels caption between a `[caption]` and a `[/caption]` line.
The gateway publishes only after an allowlisted person approves that exact message, and it publishes the attachment's bytes and that caption itself (`message` tool, `target: reels`); the model never supplies either.
So the review cut is encoded to Reels spec (see Renderer) and the caption is never trimmed - the script validator caps its size instead.

**5. JSON reaches the studio through files.**
Agents `write_file` into `<workspace>/inbox/<job>/<kind>.json` and run `submit --file`; only that folder is accepted.
`exec`'s shell guard scans a heredoc body like any other command text, and the first live script was refused on `su`.

## Stages

| stage | owner | done when |
|---|---|---|
| `script` / `script_revise` | vf-scriptwriter | `research.json` + `script.json` validate |
| `review_script` | vf-reviewer | a verdict bound to the current script review sha; REVISE loops back, max 2 |
| `assets` | vf-director | every `ai_image` scene has a picture attached for its CURRENT prompt |
| `render` | vf-director | `out/master.mp4` for the current inputs, hard QA passed |
| `review_video` | vf-reviewer | a verdict bound to the master sha; REVISE -> `fix_visuals` (director) or `script_revise` |
| `deliver` | vf-director | `deliver/delivery.json` status `sent` for the current master |
| `escalated` | human | `override --quote`, `feedback` or `cancel` on the human's word |
| `awaiting_approval` | human | - |
| `published` | gateway | `published --job J` after the `message` tool answered `posted` |

Two REVISE rounds on either review escalate to the human: the job's status becomes `escalated` and every `next` stops until the human answers.
`override` works only on an escalated job and stores the human's words (`--quote`), which the review message then shows the human; a director in a cron run once overrode on its own.

## Commands

Resolve the skill directory dynamically - every deploy adds a new numbered version:

```bash
SKILLDIR=$(ls -d /app/data/skills-store/video-factory/*/ | sort -V | tail -1)
python3 $SKILLDIR/scripts/studio.py <command>
```

Every `next` / `emit` output already contains the absolute, version-pinned command to use, so agents copy commands from there.

```
init | doctor | list | status --job J | config show | config set key=value
new --topic T [--brief B] [--format short|long|square] [--source manual|cron] [--by WHO]
next [--job J]
emit --job J --stage script|script_revise|review_script|review_video|assets
submit --job J --kind research|script|review_script|review_video --file <workspace>/inbox/<job>/<kind>.json
validate --job J
attach --job J --scene S --file PATH        redo --job J --scene S
revise-visual --job J --scene S [--prompt P] [--motion M]
render --job J [--budget SECONDS]           (resumable; PARTIAL means run it again)
package --job J                             delivered --job J --status sent|failed      published --job J
override --job J --stage script|video --quote Q (escalated jobs only) cancel --job J --reason R
backlog add --topic T [--brief B] | backlog list | backlog take | backlog remove --id bN
                                   (take prints RESUME <job> while the cron's last video is still active)
config set publish.facebook_reels.enabled=true   (review messages carry the [caption] block only when on)
```

## Renderer

- **Pronunciation**: vi-VN voices read every word with Vietnamese spelling (raw "AI" = "ai", "web" = "ốp"). `tts.VI_LEXICON` respells common terms, each one checked by synthesis + a listening judge; `VI_KNOWN_LOANWORDS` are read correctly raw. Any other word that is not a single Vietnamese syllable, and every acronym, must be respelled in the scene's `tts_text` or submit refuses the script. Studio `lexicon` entries extend the built-in list.
- **Voice**: edge-tts 7.2.8 with `boundary="WordBoundary"` (7.2.x defaults to sentences). Vietnamese gets one event per syllable = one caption token. Two requests in flight at most (the endpoint answers 503 at about four), 5 attempts with backoff, cached by a hash of voice + rate + text. The service pads ~0.13 s before and ~0.8 s after the speech; that padding is cut at the points `silencedetect` finds, not at word timings (tones ring past their boundary).
- **Captions**: the script's own text, timed by alignment with the TTS words (tolerates expanded numbers and acronyms), chunked to one line by real glyph widths read from the font (`fontmetrics.py`, stdlib TrueType parser - libass maps an ASS Fontsize to usWinAscent+usWinDescent, so Fontsize 100 is a 65.5 px em). The current word pops in the accent colour; `emphasis` phrases keep the second accent.
- **Pictures**: `ai_image` = the director's `create_image` output (Nano Banana 2 via `ag-image`), cover-cropped to 1.5x the frame and moved with `zoompan`; `card` = HTML rendered on the Chrome sidecar in an incognito context (fonts inlined as data URIs); `screenshot` = a public URL (resolved and refused if it points at a private address).
- **Clips**: one per scene with identical x264 settings, joined by the concat demuxer without re-encoding. Resumable, keyed by a hash of everything that shapes the clip.
- **Resumable to the end**: the soundtrack and the master are keyed the same way, and the review cut keeps its pass-1 statistics (`render/reviewcut.json`), so every `render` call finishes at least one step and none is redone. Each step is estimated from its measured normal cost times the slowdown last observed (`render/pace.json`); a step that would not fit the call's budget is left for the next call (`PARTIAL`). A host short of memory once made a render 5-8x slower, and the old all-at-once tail never fit the 600 s exec timeout.
- **Sound**: narration on the frame-exact timeline; optional music only from `<workspace>/music/` (tracks the human holds a licence for), ducked with `sidechaincompress`; two-pass `loudnorm` to -14 LUFS / -1.5 dBTP.
- **Out**: `master.mp4` (H.264 High, yuv420p, BT.709, 30 fps, AAC stereo 48 kHz, faststart); `preview.mp4` = the review cut, which is also the published Reel: two-pass H.264 to <= 9.5 MB (one Discord upload), 1080x1920 when the budget allows >= 1.3 Mbps else 720x1280, fixed 30 fps, 2 s closed GOP, AAC-LC stereo 48 kHz 128 kbps; `captions.srt`, `cover.jpg`, `contact.jpg`, per-scene frames, `qa.json`.
- **Images**: the prompt asks for a full-bleed 9:16 picture. Asking for "calm empty areas at top and bottom" made the model draw blurred bands with hard seams on every picture.

Safe area for 9:16 is the intersection of TikTok, Reels and Shorts overlays: text stays inside x 70-950, y 390-1240.

## Time limits the pipeline lives under

- A sync `delegate` gets at most 600 s (`internal/tools/delegate_tool.go`), and one ag-pro call has taken 3 minutes. The video reviewer therefore reads the contact sheet and every scene frame in ONE turn: `read_image` is read-only, and the agent loop runs the read-only calls of one turn in parallel. One picture per turn ran past 600 s four times; reading only the contact sheet passed a picture-in-picture and pseudo-text that the full frames showed.
- A cron run gets `cron.job_timeout` (30m in the live config) and is retried up to 3 times with the same message. A whole video takes longer than one attempt, so the director resumes through `next`, and `backlog take` answers `RESUME <job>` instead of starting a second topic.

## Measured (container, 2026-09-24)

| | |
|---|---|
| 5 s clip, 1080x1920, zoompan at 2x / 1.5x oversampling | 5.5 s / 3.7 s wall (zoompan is the cost, not x264) |
| full render, 8 scenes, 35 s video (TTS cached) | ~105 s: clips 54, preview 19, browser 14, QA 8, audio 5 |
| render, 9 scenes, 48.8 s video, 4 clips re-rendered (live E2E #2) | 182.8 s: clips 67, review cut 79.5, QA 21.1, audio 13.9 |
| edge-tts, 8 scenes uncached | 4-46 s total (network-bound, 2-11 s per call) |
| peak RSS during render | ~510 MB |
| review cut, 41.7 s at 9 MB, two-pass | 1080p veryfast 46 s SSIM 0.9795; 720p medium 83 s SSIM 0.9751; 1080p medium 153 s SSIM 0.9789 |
| "Vì sao não bộ" at Fontsize 96 | 438 px computed, ~436 px measured on a libass frame |

## Deployment

```bash
sh skills/video-factory/deploy/deploy.sh                       # from the repo root on the Docker host
node deploy/provision.mjs agents   # inside the container (see its header) after changing agents/*.md
```

`deploy.sh` copies the skill to the next numbered folder under `/app/data/skills-store/video-factory/`, runs the tests there, and only then renames it into place.
Agents take the highest number, and from version 11 on an older version's `studio.py` hands every call to the newest one, so a new version applies at once - even to a cron retry that replays version-pinned paths from its session.
`/app/data` is a named volume, so deployed versions survive restarts and image rebuilds.

The skill is deliberately NOT in `Dockerfile.claude-cli`'s bundled skills.
The seeder numbers versions from its own DB rows (`internal/skills/seeder.go` `GetNextVersion`) and would write into folders this script already uses, while agents keep taking the highest folder - an image update could then be silently ignored.
One path, one numbering.

`exec` is denied under `/app/data` except `skills-store/`, which is why agents run the skill from there and never from the repo bind mount.
The workspace `/app/workspace/video-factory` must be in `system_configs.allowed_paths` (global allowlist) so all three agents reach it with `write_file` and `read_image`; `write_file` cannot create missing folders there, so `studio.py` creates each job's inbox itself.
The Chrome sidecar mounts `/app/workspace` read-only, which is how it opens card HTML.

## Tests

```bash
python3 -X utf8 tests/test_core.py                        # no network: schema, captions, fonts, stage machine
python3 -X utf8 tests/smoke_render.py /app/workspace/X    # real edge-tts + Chrome + ffmpeg, in the container
```
