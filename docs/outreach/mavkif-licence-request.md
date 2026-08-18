# Outreach: licence request to the Mavkif / LoResMT-2025 authors

**Status:** drafted 2026-08-16, **not yet posted** — posting is the owner's action, from the owner's
HuggingFace account.

**Why this exists:** `docs/URDU_BAKEOFF_RESULTS.md` §11a. `Mavkif/m2m100_rup_rur_to_ur` is the best
published Roman-Urdu→Perso-Arabic model in existence (Char-BLEU 97.44, arXiv 2503.21530 / LoResMT
2025) and is exactly the direction this project needs, but it declares **no licence at all**, so
golden rule 6 excludes it. A licence tag is the single cheapest thing that would unblock the whole
Roman-Urdu conversion feature — cheaper than training a replacement, and cheaper than shipping the
46%-accurate Qwen baseline.

**What makes this a likely oversight rather than a refusal:**

- The paper is CC BY 4.0 and describes the models, evaluation scripts and dataset pipelines as
  open-sourced.
- The base model, `facebook/m2m100_418M`, is **MIT** — so there is no upstream obstacle to a
  permissive tag on a fine-tune of it.
- The repo has no model card at all, and still contains `optimizer.pt`, `rng_state.pth` and
  `trainer_state.json` — it looks like a training checkpoint pushed and never tidied, which is
  consistent with the licence field simply never being filled in.

**Where to post:** https://huggingface.co/Mavkif/m2m100_rup_rur_to_ur/discussions/new
(there are currently no discussions on the repo, so this would be the first).

**Also worth asking about:** `Mavkif/m2m100_rup_tokenizer_both`, which the model cannot be used
without, and which is unlicensed for the same reason.

---

## Draft post

**Title:** `Could you add a licence tag? (models are unusable without one)`

**Body:**

Hi — thank you for releasing these, and congratulations on the LoResMT 2025 paper.

I'm building a personal Urdu text-to-speech project and need Roman-Urdu → Perso-Arabic conversion.
Your `m2m100_rup_rur_to_ur` is by a distance the best result I've found for this direction — the
Char-BLEU of 97.44 in the paper is far ahead of anything else published, and well ahead of the
prompted-LLM baseline I measured myself (a Qwen2.5 7B baseline produced fully correct output on
under half my test sentences).

The blocker is that the repo doesn't declare a licence. Neither the model card nor the repo metadata
has a `license` field, and without one the default is all-rights-reserved, so I can't use the weights
even for a personal, non-commercial project.

I suspect this is just an oversight rather than intentional, because:

- the paper is CC BY 4.0 and describes the models as open-sourced, and
- the base model `facebook/m2m100_418M` is MIT-licensed, so there's no upstream restriction that
  would prevent a permissive tag here.

Would you be willing to add a `license:` tag to the model card? Anything permissive (MIT, Apache-2.0)
or CC-BY-4.0 to match the paper would work for my use. It's a one-line addition to the card's YAML
front matter:

```yaml
---
license: mit
---
```

Two small related things, if you have a moment:

1. `Mavkif/m2m100_rup_tokenizer_both` would need the same tag — the model can't be loaded without it,
   so a licence on the weights alone wouldn't be enough.
2. The repo currently includes `optimizer.pt`, `rng_state.pth`, `scheduler.pt` and `trainer_state.json`
   from training. Not a problem for me, but removing them would cut the download size noticeably for
   anyone using the model for inference.

Thanks again for putting this work out publicly — it's the only thing in this space that actually
targets Roman-Urdu → Urdu properly.
