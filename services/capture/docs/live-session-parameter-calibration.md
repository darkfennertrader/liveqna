# Live-session parameter calibration

Date: 2026-10-15

## Result

| Parameter | Tuned default | Basis |
|---|---:|---|
| `LIVE_SESSION_SILENCE_MS` | `500` ms | Post-STT debounce. OpenAI and Azure use 500 ms as a typical/default speech-stop value; Deepgram recommends 300–500 ms for conversational pauses. The Supervisor can return `WAIT` when this early candidate is incomplete. |
| `LIVE_SESSION_MAX_WAIT_MS` | `18000` ms | Corpus P95 question length is 31 words. At the observed weighted rate of 111.4 words/minute, that is about 16.7 seconds; 18 seconds adds a small guard while bounding continuous speech. |
| `LIVE_SESSION_MAX_WORDS` | `140` words | The largest observed question plus its two preceding sentence-like units was 129 words. The configured value covers that maximum with an 11-word guard. |
| `LIVE_SESSION_FINALIZE_GRACE_MS` | `3000` ms | Retained as a conservative system-level estimate for an in-flight final STT decode. The PDF corpus cannot measure this value. |

`LIVE_SESSION_SILENCE_MS` is an additional debounce after an STT transcript
event. With the default local STT configuration, its 500 ms is added after the
local VAD/decoder has already waited for its own `SILENCE_DURATION_MS=700` close
condition. These are deliberately separate controls.

## Corpus

Only PDF files under the supplied Czech and Belgium meeting folders were used.

| Measure | Combined | Czech | Belgium |
|---|---:|---:|---:|
| PDF documents | 12 | 7 | 5 |
| Recordings represented | 15 | 7 | 8 |
| Duration | 15.72 h | 9.21 h | 6.51 h |
| Transcript words | 105,115 | 60,145 | 44,970 |
| Question-mark units | 1,110 | 736 | 374 |
| Weighted speech rate | 111.4 wpm | 108.8 wpm | 115.1 wpm |

The two country subsets produced similar distributions:

| Text window | Combined P95 | Combined P99 | Maximum |
|---|---:|---:|---:|
| Question only | 31 | 43 | 74 |
| Question plus one preceding unit | 52 | 67 | 110 |
| Question plus two preceding units | 69 | 93 | 129 |

The units are punctuation-delimited transcript spans, not known speaker turns.
The PDFs contain meeting-level durations but no word-level timestamps, silence
durations, or reliable speaker boundaries. Therefore this is a text-window
calibration, not a measured acoustic-silence P95. A true silence P95 requires
timestamped STT/VAD events from live sessions.

## External guidance

- [OpenAI Realtime VAD](https://platform.openai.com/docs/api-reference/realtime-client-events/session?lang=node.js) documents a 500 ms default silence duration and warns that shorter values can interrupt brief pauses.
- [Azure Speech silence handling](https://learn.microsoft.com/azure/ai-services/speech-service/how-to-recognize-speech) describes 500 ms as a typical segmentation default and explains the latency versus over-segmentation tradeoff.
- [Deepgram endpointing](https://developers.deepgram.com/docs/understand-endpointing-interim-results/) recommends 300–500 ms for conversations where speakers pause mid-thought and recommends accumulating final transcript pieces before acting on an utterance boundary.

These vendor values are not treated as corpus measurements. They provide the
initial estimate for the parameter the PDFs cannot measure.
