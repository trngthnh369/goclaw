"""Video Factory core: deterministic video production around LLM-written scripts.

The model writes research, a scene script and review verdicts. Everything else -
validation, text-to-speech, caption timing, rendering, loudness, QA, packaging -
is done here, so no published artifact depends on what a model remembered to do.
"""

RENDERER_VERSION = "vf-render-2"   # 2: stereo soundtrack, publishable review cut, vi lexicon
