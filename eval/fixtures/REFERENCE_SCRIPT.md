# Reference recording script — second speaker

For the Urdu bake-off's second reference voice. The plan requires **male and female
references**: a model that clones one well and the other badly is not generally suitable, and with
a single reference that failure mode is invisible.

Only `eval/fixtures/voice_urdu.wav` exists today (run as `--reference-id owner`). Until a second
exists, cross-speaker generalisation is **untested** and every results table must say so rather
than quietly implying otherwise.

## Consent — required before the clip is used

Every candidate model's licence forbids cloning a voice without the speaker's explicit permission
(IndicF5, OmniVoice, Higgs v3 all state it; Higgs additionally prohibits impersonation outright).
**The speaker must know their voice will be used to train/drive a voice clone, and agree to it.**
Do not enrol a recording without that.

## Why this text and not the test sentences

It deliberately shares **no sentences** with `eval/fixtures/urdu_corpus.json`. The reference clip
supplies *timbre*, and the model is then asked to speak *different* text — reusing a test sentence
as the reference would let a model appear to succeed by echoing what it just heard.

It is conversational Pakistani Urdu, first person, with a spread of common consonants and vowels,
and no code-switching (the code-switch stress lives in the target corpus, not in the reference).

## The script — Urdu

```
السلام علیکم، میرا نام ثانیہ ہے اور میں کراچی سے تعلق رکھتی ہوں۔

آج موسم کافی خوشگوار ہے، ہلکی ہلکی ہوا چل رہی ہے۔

مجھے کتابیں پڑھنے اور نئی جگہیں دیکھنے کا بہت شوق ہے۔

شام کو میں اکثر اپنی فیملی کے ساتھ چائے پیتی ہوں اور دن بھر کی باتیں کرتی ہوں۔
```

## The same script — Roman Urdu

```
Assalam-o-alaikum, mera naam Sania hai aur main Karachi se taalluq rakhti hoon.

Aaj mausam kaafi khushgawar hai, halki halki hawa chal rahi hai.

Mujhe kitaabein parhne aur nayi jagahein dekhne ka bohat shauq hai.

Shaam ko main aksar apni family ke saath chai peeti hoon aur din bhar ki baatein karti hoon.
```

The speaker should substitute **her own name and city** — it reads more naturally, and natural
delivery matters more here than matching the text exactly.

## Recording notes

| | |
|---|---|
| **Length** | 20–30 s. Longer is fine; the runtimes trim. Under ~10 s starves the speaker encoder. |
| **Room** | Quiet. No TV, music, fan, or traffic. Soft furnishings beat a bare room. |
| **Mic** | A phone is genuinely fine. Hold it ~20 cm away, off to the side, not directly in front of the mouth (avoids plosives). |
| **Delivery** | Normal conversational pace. **Not** formal reading-aloud — the target sentences are casual, and a stiff reference produces stiff output. |
| **Format** | Anything: `.m4a`, `.ogg`, `.wav`, `.mp3`. Conversion is handled. |
| **Avoid** | Clipping (don't shout), background speech, and heavy phone "noise suppression" if it can be switched off — it smears the timbre the encoder needs. |

One clean take beats several patched together.

## After recording

Save it anywhere and note the path. It gets converted, added as
`eval/fixtures/voice_urdu_female.wav`, and every bake-off arm is re-run with
`--reference-id female` against the identical corpus, so the two speakers are directly comparable.

**A transcript is also needed** — IndicF5 and Higgs v3 both take a `ref_text`. If the speaker
follows the script above, the script *is* the transcript; if she improvises, write down what she
actually said.
