# Evaluation fixtures

## `voice_urdu.wav`

The project owner's own voice, speaking Urdu. **6.67 s, 24 kHz, mono, 16-bit** (320,406 bytes).

Recorded and supplied by the repository's collaborator for the express purpose of testing voice
cloning on his own voice. No third-party consent question arises. Do not use it for anything else,
and do not add anyone else's voice here without their explicit consent — voice recordings are
biometric data in several jurisdictions.

**Exact transcript, Perso-Arabic:**

```
ہیلو میرا نام منور ہے اور تم بہت ہی فضول کام کر رہے ہو، بالکل بھی اچھا کام نہیں کیا تم نے۔
```

**Devanagari transliteration** (needed because every model tested so far requires Devanagari input;
F5-TTS-OpenBible-Urdu's vocabulary contains no Perso-Arabic at all):

```
हैलो मेरा नाम मुनव्वर है और तुम बहुत ही फ़ुज़ूल काम कर रहे हो, बिल्कुल भी अच्छा काम नहीं किया तुम ने।
```

## Standard target sentence

Everyday conversational register — deliberately not liturgical, since the Urdu checkpoint under test
was Bible-trained and the product needs ordinary speech.

```
اگر تمہیں فارغ وقت ملے تو مجھے فون کر لینا، ہم کہیں باہر کھانا کھانے چلتے ہیں اور تھوڑی دیر گپ شپ بھی کر لیں گے۔
अगर तुम्हें फ़ारिग़ वक़्त मिले तो मुझे फ़ोन कर लेना, हम कहीं बाहर खाना खाने चलते हैं और थोड़ी देर गप शप भी कर लेंगे।
```

Using the same reference and the same target text across every model is what makes the runs in
`docs/PHASE_A_RESULTS.md` directly comparable. Change them and the comparison table becomes
meaningless.

## Why these are in git

Both the harness and this recording previously existed only under `/workspace/engines-lab/` on a
RunPod pod. A volume migration destroyed the harness. The recording survived twice on luck.

The reference audio is 320 KB and irreplaceable; the venvs and model caches it sat beside were tens
of gigabytes and entirely regenerable. That ratio is the lesson.
